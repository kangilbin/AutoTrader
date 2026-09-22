"""
수정주가 변동 종목 자동 재적재 배치

매일 새벽 02:30 KST에 KSD 예탁원정보 API(액면교체/합병/분할)를 호출하여
당일 효력 발생 이벤트를 수집하고, 등록된 종목(DATA_YN='Y') 중 영향받는
종목의 3년치 OHLCV를 KIS 수정주가 기준으로 자동 재적재한다.
"""
import asyncio
import logging
from datetime import datetime

from app.common.database import Database
from app.core.config import get_settings
from app.exceptions import ExternalServiceError
from app.domain.stock.service import StockService
from app.domain.stock.stock_data_batch import fetch_and_store_3_years_data
from app.external.kis_api import (
    get_rev_split_schedule,
    get_merger_split_schedule,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_RELOAD_SEMAPHORE = asyncio.Semaphore(3)


def _normalize_date(s: str) -> str:
    """YYYYMMDD, YYYY/MM/DD, YYYY-MM-DD → YYYYMMDD"""
    return ''.join(c for c in (s or '') if c.isdigit())[:8]


async def collect_today_adjustment_events(user_id: str, today: str, db) -> set[str]:
    """rev-split + merger-split 응답에서 list_dt==today 종목코드 set 반환

    둘 다 실패하면 예외를 올린다. 빈 set 을 돌려주면 호출부가 "당일 이벤트 없음"
    으로 정상 종료해, 조회를 못 한 것과 이벤트가 정말 없는 것이 로그에서 구분되지
    않는다 — 모의 앱키로 실전 전용 TR 을 호출해 둘 다 실패한 날 잡이 조용히
    끝난 적이 있다. 재적재가 조용히 스킵되면 분할 전 가격이 그대로 남아 지표가
    틀어지므로, 모르고 지나가는 쪽이 실패로 끝나는 쪽보다 나쁘다.

    한쪽만 실패하면 그 유형의 이벤트만 놓치므로 나머지 결과로 진행한다.
    """
    failures: list[str] = []

    try:
        rev = await get_rev_split_schedule(user_id, today, today, db)
    except Exception as e:
        logger.error(f"[PRICE ADJ] rev-split 조회 실패: {e}")
        failures.append("rev-split")
        rev = {}

    try:
        mer = await get_merger_split_schedule(user_id, today, today, db)
    except Exception as e:
        logger.error(f"[PRICE ADJ] merger-split 조회 실패: {e}")
        failures.append("merger-split")
        mer = {}

    if len(failures) == 2:
        raise ExternalServiceError(
            "KIS", f"수정주가 이벤트 조회 전부 실패 ({', '.join(failures)})"
        )
    if failures:
        logger.warning(
            f"[PRICE ADJ] {failures[0]} 조회 실패 - 나머지 결과로만 진행 "
            f"(해당 유형 이벤트는 놓칠 수 있음)"
        )

    codes: set[str] = set()
    for row in (rev.get("output1") or []):
        if _normalize_date(row.get("list_dt", "")) == today:
            code = (row.get("sht_cd") or "").strip()
            if code:
                codes.add(code)
    for row in (mer.get("output1") or []):
        if _normalize_date(row.get("list_dt", "")) == today:
            code = (row.get("sht_cd") or "").strip()
            if code:
                codes.add(code)
    return codes


async def reload_single_stock(stock, user_id: str):
    """단일 종목 3년치 재적재 (세마포어로 동시성 제어)"""
    async with _RELOAD_SEMAPHORE:
        code = stock.ST_CODE
        mrkt_code = stock.MRKT_CODE
        try:
            stock_data = {"ST_NM": stock.ST_NM}
            await fetch_and_store_3_years_data(
                user_id=user_id,
                mrkt_code=mrkt_code,
                st_code=code,
                stock_data=stock_data,
            )
            logger.info(f"[PRICE ADJ] 재적재 완료: {mrkt_code}/{code}")
        except Exception as e:
            logger.error(f"[PRICE ADJ] 재적재 실패: {mrkt_code}/{code} - {e}")
            raise


async def price_adjustment_reload_job():
    """매일 02:30 KST 평일 실행"""
    user_id = settings.BATCH_USER_ID
    if not user_id:
        logger.error("[PRICE ADJ] BATCH_USER_ID 미설정 - 잡 중단")
        return

    today = datetime.now().strftime("%Y%m%d")
    logger.info(f"[PRICE ADJ] 수정주가 재적재 잡 시작 (today={today})")

    db = await Database.get_session()
    try:
        event_codes = await collect_today_adjustment_events(user_id, today, db)
        if not event_codes:
            logger.info("[PRICE ADJ] 당일 이벤트 없음 - 종료")
            return

        logger.info(f"[PRICE ADJ] 이벤트 종목코드: {sorted(event_codes)}")

        stock_service = StockService(db)
        target_stocks = await stock_service.get_data_target_stocks(overseas=False)
        targets = [s for s in target_stocks if s.ST_CODE in event_codes]

        logger.info(
            f"[PRICE ADJ] 이벤트 {len(event_codes)}건 중 재적재 대상 {len(targets)}건"
        )
        if not targets:
            return

        tasks = [reload_single_stock(s, user_id) for s in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(
            f"[PRICE ADJ] 완료 - 성공: {success}/{len(targets)}"
        )

    except Exception as e:
        logger.error(f"[PRICE ADJ] 잡 실패: {e}", exc_info=True)
    finally:
        await db.close()