"""
주식 데이터 배치 작업 - 3년치 데이터 적재
"""
from datetime import datetime, time as dt_time, timedelta
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo
import asyncio
import logging
import time

from app.core.market_code import is_overseas
from app.external.kis_api import get_stock_data
from app.external import foreign_api
from app.common.database import Database
from app.domain.stock.service import StockService

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_REQUEST = 100

# ===== 일봉 확정 시각 (시장 로컬 타임존 기준) =====
# 정규장 마감(국내 15:30 KST / 미국 16:00 ET) + KIS 일봉 확정 대기 여유
_SESSION_COMPLETE = {
    "KR": {"time": dt_time(15, 35), "tz": "Asia/Seoul"},
    "US": {"time": dt_time(16, 35), "tz": "America/New_York"},
}

# 수집 잡은 확정 시각보다 이만큼 뒤에 돌린다.
# 같은 시각에 걸어두면 잡이 몇 ms만 일찍 깨도 is_today_incomplete가 True가 되어
# 전 종목이 스킵되고(재시도 없음) 하루치 OHLCV가 조용히 유실된다.
COLLECT_JOB_MARGIN_MIN = 5


def collect_job_cron(overseas: bool) -> dict:
    """일별 수집 잡의 cron 인자 (확정 시각 + 여유)

    스케줄러가 이 값을 쓰므로 확정 시각을 바꾸면 잡 시각도 함께 움직인다.
    """
    config = _SESSION_COMPLETE["US" if overseas else "KR"]
    fire_at = (
        datetime.combine(datetime.today(), config["time"])
        + timedelta(minutes=COLLECT_JOB_MARGIN_MIN)
    )
    return {
        "minute": str(fire_at.minute),
        "hour": str(fire_at.hour),
        "timezone": config["tz"],
    }


def is_today_incomplete(mrkt_code: str) -> bool:
    """
    오늘 거래 세션이 아직 완료되지 않았는지 판별한다.
    완료 전이면 당일 데이터는 미확정(부분봉)이므로 저장하면 안 된다.

    - 주말: 오늘은 거래일 아님 → True (당일 저장 금지, 직전 거래일까지만)
    - 프리마켓·장중: 세션 미완료 → True (당일 저장 금지)
    - 정규장 마감 후: 세션 완료 → False (당일 완성봉 저장 가능)

    ※ 기존 is_market_open은 "프리마켓(9:00 ET 이전)"을 "마감 후"와 동일 취급해
       미완성 당일봉을 적재하는 버그가 있었음. 마감 시각 단일 기준으로 정정.

    Args:
        mrkt_code: 시장 코드 ("J"=국내, "NYS/NAS/AMS"=미국)

    Returns:
        True: 오늘 세션 미완료 (당일 저장 금지)
        False: 오늘 세션 완료 (당일 완성봉 저장 가능)
    """
    config = _SESSION_COMPLETE["US" if is_overseas(mrkt_code) else "KR"]
    now = datetime.now(ZoneInfo(config["tz"]))

    if now.weekday() >= 5:
        return True

    return now.time() < config["time"]


async def fetch_and_store_3_years_data(user_id: str, mrkt_code: str, st_code: str, stock_data: dict):
    """
    3년치 주식 데이터를 백그라운드에서 병렬로 가져와서 DB에 저장하는 배치 작업

    Args:
        user_id: 사용자 ID
        mrkt_code: 시장 코드
        st_code: 종목 코드
        stock_data: 주식 메타 정보
    """
    db = await Database.get_session()
    try:
        stock_service = StockService(db)

        # 상태 업데이트: 처리 중
        await stock_service.update_stock(mrkt_code, st_code, {"DATA_YN": 'P'})
        logger.info(f"Started background data fetch for {mrkt_code}/{st_code}")

        # 거래일 경계는 시장 타임존 기준 (미국=ET, 국내=KST) — 서버 로컬 시계에 의존하지 않음
        market_tz = ZoneInfo("America/New_York") if is_overseas(mrkt_code) else ZoneInfo("Asia/Seoul")
        today = datetime.now(market_tz).date()
        if is_today_incomplete(mrkt_code):
            end_date = today - timedelta(days=1)
            logger.info(f"[{mrkt_code}/{st_code}] 세션 미완료 - 전일({end_date})까지 적재")
        else:
            end_date = today
            logger.info(f"[{mrkt_code}/{st_code}] 세션 완료 - 금일({end_date})까지 적재")

        start_date = end_date - relativedelta(years=3)
        current_date = start_date

        # 날짜 범위 생성
        date_ranges = []
        while current_date < end_date:
            next_date = current_date + relativedelta(days=MAX_ITEMS_PER_REQUEST)
            if next_date > end_date:
                next_date = end_date
            date_ranges.append((current_date, next_date))
            current_date = next_date

        logger.info(f"Created {len(date_ranges)} date ranges for {st_code}")

        # 동시 실행 제한 세마포어
        semaphore = asyncio.Semaphore(3)

        async def process_date_range(range_start, range_end):
            task_start_time = time.time()
            async with semaphore:
                try:
                    if is_overseas(mrkt_code):
                        response = await foreign_api.get_stock_data(
                            user_id, st_code,
                            range_start.strftime('%Y%m%d'),
                            range_end.strftime('%Y%m%d'),
                            db, excd=mrkt_code
                        )
                    else:
                        response = await get_stock_data(
                            user_id, st_code,
                            range_start.strftime('%Y%m%d'),
                            range_end.strftime('%Y%m%d'),
                            db
                        )
                    if response and "output2" in response:
                        api_data_count = len(response["output2"])
                        task_time = time.time() - task_start_time
                        logger.debug(f"API call completed for {range_start} to {range_end}: {api_data_count} records in {task_time:.2f}s")
                        return response["output2"]
                    else:
                        logger.warning(f"No data for {range_start} to {range_end}")
                        return None
                except Exception as e:
                    logger.error(f"Error processing {range_start} to {range_end}: {e}")
                    return None

        logger.info(f"Processing all {len(date_ranges)} tasks in parallel")

        # 모든 태스크 실행
        tasks = [process_date_range(start, end) for start, end in date_ranges]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 정리 및 DB 저장
        successful_tasks = 0
        failed_tasks = 0
        total_cnt = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_tasks += 1
                logger.error(f"Task failed: {result}")
            elif result is not None:
                try:
                    # history_data 변환
                    history_data = []
                    for item in result:
                        history_data.append({
                            "MRKT_CODE": mrkt_code,
                            "ST_CODE": st_code,
                            "STCK_BSOP_DATE": item.get("STCK_BSOP_DATE"),
                            "STCK_OPRC": item.get("STCK_OPRC"),
                            "STCK_HGPR": item.get("STCK_HGPR"),
                            "STCK_LWPR": item.get("STCK_LWPR"),
                            "STCK_CLPR": item.get("STCK_CLPR"),
                            "ACML_VOL": item.get("ACML_VOL"),
                            "REG_DT": datetime.now()
                        })

                    if history_data:
                        cnt = await stock_service.save_history_bulk(history_data)
                        successful_tasks += 1
                        total_cnt += cnt
                        logger.debug(f"DB insert completed for batch {i+1}: {cnt} records")
                except Exception as db_error:
                    failed_tasks += 1
                    logger.error(f"DB insert failed for batch {i+1}: {db_error}")
            else:
                failed_tasks += 1

        # 상태 업데이트: 완료
        await stock_service.update_stock(mrkt_code, st_code, {"DATA_YN": 'Y'})
        logger.info(f"Stock {mrkt_code}/{st_code} updated to DATA_YN=Y, total {total_cnt} records")

    except Exception as e:
        try:
            stock_service = StockService(db)
            await stock_service.update_stock(mrkt_code, st_code, {"DATA_YN": 'E'})
            logger.error(f"Updated {mrkt_code}/{st_code} to DATA_YN=E due to error: {e}")
        except Exception as update_error:
            logger.error(f"Failed to update error status for {mrkt_code}/{st_code}: {update_error}")

        logger.error(f"Background fetch failed for {mrkt_code}/{st_code}: {e}")
        raise
    finally:
        await db.close()

