"""
Swing Service - 비즈니스 로직 및 트랜잭션 관리
"""
import asyncio

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from typing import List, Dict, Any
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from app.domain.swing.indicators import TechnicalIndicators
from app.domain.swing.repository import SwingRepository
from app.domain.swing.entity import SwingTrade, EmaOption
from app.domain.swing.schemas import SwingCreateRequest, SwingResponse
from app.domain.stock.service import StockService
from app.domain.stock.stock_data_batch import fetch_and_store_3_years_data
from app.exceptions import DatabaseError, NotFoundError, DuplicateError, BusinessRuleError
from app.external.kis_api import get_stock_balance
from app.external import foreign_api
from app.common.database import Database
from app.common.redis import Redis
import logging
import json
import pandas as pd

logger = logging.getLogger(__name__)


# ===== 시장별 캐시 만료 기준 시각 (해당 시장 마감 = 지표 갱신 경계) =====
# J: 국내장(16:00 KST), NASD: 미국장(16:00 ET)
_MARKET_CLOSE_CONFIG = {
    "J":    {"close": time(16, 0), "tz": "Asia/Seoul"},
    "NASD": {"close": time(16, 0), "tz": "America/New_York"},
}
_DEFAULT_CLOSE = _MARKET_CLOSE_CONFIG["J"]


def market_cache_ttl(mrkt_code: str, min_ttl: int = 60) -> int:
    """
    해당 시장의 '다음 마감 시각'까지 남은 초를 반환 (서버 타임존 무관).

    지표 캐시는 그 시장 마감 후 새 일봉 데이터로 갱신되므로,
    캐시 수명을 시장별 마감 시각(16:00 현지)에 맞춘다.
    ZoneInfo 기반 타임존-aware 계산이라 컨테이너가 UTC든 KST든
    동일하게 동작한다. (기존 naive datetime.now() + 16:00 KST 고정 방식은
    UTC 컨테이너에서 오작동했고, 밤에 도는 미국 워밍업은 음수→60초로 클램프되어
    매매 시점엔 캐시가 사라지는 버그가 있었다.)
    """
    config = _MARKET_CLOSE_CONFIG.get(mrkt_code, _DEFAULT_CLOSE)
    tz = ZoneInfo(config["tz"])
    now = datetime.now(tz)
    target = datetime.combine(now.date(), config["close"], tzinfo=tz)
    if now >= target:
        target += timedelta(days=1)
    return max(int((target - now).total_seconds()), min_ttl)


class SwingService:
    """스윙 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SwingRepository(db)

    async def get_available_capital(
        self, user_id: str, account_no: str, mrkt_code: str, exclude_swing_id: int = None
    ) -> dict:
        """가용 자본 조회"""
        overseas = mrkt_code == "NASD"

        if overseas:
            # 해외증거금 통화별조회 — 외화주문가능금액(ord_psbl_amt)을 가용자본으로 사용
            margin = await foreign_api.get_foreign_margin(user_id, self.db)
            if margin is None:
                # 모의투자: 현금/주문가능 소스 없음 → 한도 추적 불가
                allocated = int(await self.repo.get_total_init_amount(account_no, overseas, exclude_swing_id))
                return {
                    "total_capital": None,
                    "allocated": allocated,
                    "available_capital": None,
                    "capital_tracking": False,
                }
            cash = int(float(margin.get("ord_psbl_amt", 0) or 0))
        else:
            balance_data = await get_stock_balance(user_id, self.db)
            output2 = balance_data["output2"]
            cash = int(float(output2.get("dnca_tot_amt", 0) or 0))

        allocated = int(await self.repo.get_total_init_amount(account_no, overseas, exclude_swing_id))
        available_capital = cash - allocated

        return {
            "total_capital": cash,
            "allocated": allocated,
            "available_capital": available_capital,
            "capital_tracking": True,
        }

    async def create_swing(self, user_id: str, request: SwingCreateRequest) -> dict:
        """스윙 전략 등록"""
        try:
            # 도메인 엔티티 생성 (비즈니스 검증) — 등록 시 USE_YN='N'이므로 자본 검증 불필요 (활성화 시 검증)
            swing = SwingTrade.create(
                account_no=request.ACCOUNT_NO,
                mrkt_code=request.MRKT_CODE,
                st_code=request.ST_CODE,
                init_amount=Decimal(request.INIT_AMOUNT),
                swing_type=request.SWING_TYPE,
            )

            db_swing = await self.repo.save(swing)

            # 이평선 전략인 경우 옵션 저장
            if request.SWING_TYPE == 'A':
                ema = EmaOption(
                    ACCOUNT_NO=request.ACCOUNT_NO,
                    ST_CODE=request.ST_CODE,
                    SHORT_TERM=request.SHORT_TERM,
                    MEDIUM_TERM=request.MEDIUM_TERM,
                    LONG_TERM=request.LONG_TERM
                )
                ema.validate()
                await self.repo.save_ema_option(ema)

            await self.db.commit()

            # 데이터 적재 여부 확인 후 백그라운드 실행
            stock_service = StockService(self.db)
            stock_info = await stock_service.get_stock_info(request.MRKT_CODE, request.ST_CODE)

            if stock_info.get("DATA_YN") != 'Y':
                asyncio.create_task(
                    self._fetch_and_cache(user_id, request.MRKT_CODE, request.ST_CODE, stock_info)
                )
                logger.info(f"[{request.MRKT_CODE}/{request.ST_CODE}] 데이터 적재 + 캐싱 백그라운드 태스크 시작")

            return SwingResponse.model_validate(db_swing).model_dump()

        except IntegrityError as e:
            await self.db.rollback()
            logger.error(f"스윙 등록 실패 (중복): {e}", exc_info=True)
            raise DuplicateError("스윙 전략", request.ST_CODE)
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 등록 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 등록에 실패했습니다")

    @staticmethod
    async def _fetch_and_cache(user_id: str, mrkt_code: str, st_code: str, stock_info: dict):
        """데이터 적재 후 지표 캐싱 (백그라운드 태스크용)"""
        await fetch_and_store_3_years_data(user_id, mrkt_code, st_code, stock_info)

        db = await Database.get_session()
        try:
            service = SwingService(db)
            await service.cache_single_indicators(mrkt_code, st_code)
        finally:
            await db.close()

    async def get_swing(self, swing_id: int) -> dict:
        """스윙 조회"""
        swing = await self.repo.find_by_id(swing_id)
        if not swing:
            raise NotFoundError("스윙 전략", swing_id)
        return SwingResponse.model_validate(swing).model_dump()

    async def update_swing(self, swing_id: int, data: dict, user_id: str = None) -> dict:
        """스윙 수정"""
        try:
            swing = await self.repo.find_by_id(swing_id)
            if not swing:
                raise NotFoundError("스윙 전략", swing_id)

            # 자본 한도 검증 (활성 스윙만 합산)
            # - INIT_AMOUNT 변경 시: 변경 후 금액이 가용 자본 초과 여부
            # - USE_YN 활성화 시: 해당 스윙 INIT_AMOUNT가 가용 자본 초과 여부
            need_capital_check = user_id and (
                "INIT_AMOUNT" in data
                or (data.get("USE_YN") == "Y" and swing.USE_YN == "N")
            )
            if need_capital_check:
                # 활성 스윙이면 자기 자신 제외, 비활성→활성 전환이면 제외 불필요(이미 합산에서 빠져있음)
                exclude_id = swing_id if swing.USE_YN == "Y" else None
                capital_info = await self.get_available_capital(
                    user_id, swing.ACCOUNT_NO, swing.MRKT_CODE,
                    exclude_swing_id=exclude_id
                )
                check_amount = data.get("INIT_AMOUNT", int(swing.INIT_AMOUNT))
                # 모의투자 등 가용자본 추적 불가(available_capital=None) 시 한도 검증 생략
                if capital_info.get("available_capital") is not None and check_amount > capital_info["available_capital"]:
                    raise BusinessRuleError(
                        f"투자 가능 금액을 초과했습니다. "
                        f"가용 자본: {capital_info['available_capital']:,}원, "
                        f"요청 금액: {check_amount:,}원",
                        rule="CAPITAL_LIMIT_EXCEEDED",
                        detail={
                            "available_capital": capital_info["available_capital"],
                            "requested_amount": check_amount,
                            "total_capital": capital_info["total_capital"],
                            "allocated": capital_info["allocated"],
                        }
                    )

            # INIT_AMOUNT 변경 시 차액을 CUR_AMOUNT에도 반영
            if "INIT_AMOUNT" in data:
                diff = Decimal(data["INIT_AMOUNT"]) - swing.INIT_AMOUNT
                data["CUR_AMOUNT"] = swing.CUR_AMOUNT + diff

            data["MOD_DT"] = datetime.now()
            result = await self.repo.update(swing_id, data)
            await self.db.commit()

            return SwingResponse.model_validate(result).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 수정 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 수정에 실패했습니다")

    async def delete_swing(self, swing_id: int, swing_type: str) -> bool:
        """스윙 삭제"""
        try:
            result = await self.repo.delete(swing_id)

            if swing_type == 'A':
                await self.repo.delete_ema_option(swing_id)
            await self.db.commit()
            if not result:
                raise NotFoundError("스윙 전략", swing_id)
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 삭제 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 삭제에 실패했습니다")

    async def mapping_swing(self, user_id: str, account_no: str, mrkt_code: str = "J") -> dict:
        """스윙 목록과 보유 주식 매핑"""
        try:
            # 통화별 정밀도: KRW=정수, USD=소수점 2자리 (센트)
            overseas = mrkt_code == "NASD"

            def _to_amount(value):
                """문자열/숫자를 출력용 금액으로 변환 (KRW=int, USD=float 2자리)"""
                try:
                    f = float(value or 0)
                except (TypeError, ValueError):
                    return 0.0 if overseas else 0
                return round(f, 2) if overseas else int(f)

            def _to_decimal(value) -> Decimal:
                """산술용 Decimal (Decimal + 문자열 호환)"""
                try:
                    return Decimal(str(value or 0))
                except (TypeError, ValueError, InvalidOperation):
                    return Decimal(0)

            cash_supported = True  # 모의투자(현금 소스 없음)면 False → CASH_ASSET 미지원
            swing_list = await self.repo.find_all_by_account_no(account_no, mrkt_code)
            if overseas:
                # 해외: 보유 종목은 TTTS3012R(체결 즉시 반영), USD 현금은 해외증거금 통화별조회
                holdings = await foreign_api.get_stock_balance(user_id, self.db)
                margin = await foreign_api.get_foreign_margin(user_id, self.db)
                buy_list = holdings["output1"]
                # 035에는 평가금액/손익이 없어 보유종목(TTTS3012R)을 합산해 summary용 output2를 구성
                evlu_sum = sum((_to_decimal(i.get("evlu_amt")) for i in buy_list), Decimal(0))
                pfls_sum = sum((_to_decimal(i.get("evlu_pfls_amt")) for i in buy_list), Decimal(0))
                if margin is None:
                    # 모의투자: 현금 소스 없음 → 총평가는 보유종목만, CASH_ASSET 미지원
                    cash_amt = Decimal(0)
                    cash_supported = False
                    dnca_display = "0"
                else:
                    cash_amt = _to_decimal(margin.get("dnca_amt"))
                    dnca_display = margin.get("dnca_amt", "0")
                output2 = {
                    "tot_evlu_amt": str(evlu_sum + cash_amt),  # 현금 포함 총평가 (모의는 현금 0)
                    "evlu_pfls_smtl_amt": str(pfls_sum),
                    "dnca_tot_amt": dnca_display,  # 외화예수금 → CASH_ASSET
                }
            else:
                balance_data = await get_stock_balance(user_id, self.db)
                buy_list = balance_data["output1"]
                output2 = balance_data["output2"]

            swing_dict = {swing["ST_CODE"]: swing for swing in swing_list}
            buy_dict = {item.get("pdno"): item for item in buy_list if item.get("pdno")}
            stock_service = StockService(self.db)
            results = []

            # 1. buy_list 기준으로 처리 (기존 로직)
            for buy_item in buy_list:
                st_code = buy_item.get("pdno")
                if not st_code:
                    continue

                if st_code not in swing_dict:
                    # 새 스윙 등록 — 매입금액을 초기 투자금으로 설정
                    item_mrkt_code = mrkt_code if overseas else buy_item.get("mrkt_code", "J")
                    pchs_amt = _to_decimal(buy_item.get("pchs_amt", 0))
                    try:
                        async with self.db.begin_nested():
                            swing = SwingTrade.create(
                                account_no=account_no,
                                mrkt_code=item_mrkt_code,
                                st_code=st_code,
                                init_amount=pchs_amt,
                                swing_type='S'
                            )
                            swing.CUR_AMOUNT = Decimal(0)  # 이미 매수 완료 상태
                            swing.ENTRY_PRICE = _to_decimal(buy_item.get("pchs_avg_pric", 0))
                            swing.HOLD_QTY = int(float(buy_item.get("hldg_qty", 0) or 0))
                            swing.USE_YN = 'N'
                            db_swing = await self.repo.save(swing)
                    except IntegrityError:
                        db_swing = await self.repo.find_by_account_and_stock(account_no, item_mrkt_code, st_code)
                        if not db_swing:
                            continue

                    # 주가 데이터 적재 여부 확인 후 백그라운드 적재
                    stock_info = await stock_service.get_stock_info(item_mrkt_code, st_code)
                    if stock_info.get("DATA_YN") != 'Y':
                        asyncio.create_task(
                            self._fetch_and_cache(user_id, item_mrkt_code, st_code, stock_info)
                        )
                        logger.info(f"[{item_mrkt_code}/{st_code}] 매핑 등록 - 데이터 적재 백그라운드 태스크 시작")

                    swing_result = SwingResponse.model_validate(db_swing).model_dump()
                    result_data = {
                        **swing_result,
                        "ST_NM": buy_item.get("prdt_name"),
                        "HLDG_QTY": buy_item.get("hldg_qty"),
                        "EVLU_AMT": _to_amount(buy_item.get("evlu_amt", 0)),
                        "EVLU_PFLS_RT": float(buy_item.get("evlu_pfls_rt", 0) or 0),
                        "EVLU_PFLS_AMT": _to_amount(buy_item.get("evlu_pfls_amt", 0)),
                        "PRPR": float(buy_item.get("prpr", 0) or 0),
                    }
                    results.append(result_data)
                else:
                    # 기존 데이터 merge
                    data = swing_dict[st_code]
                    evlu_amt_dec = _to_decimal(buy_item.get("evlu_amt", 0))

                    if data["INIT_AMOUNT"]:
                        # INIT_AMOUNT > 0: 자체 계산 (서비스에서 등록/매매한 종목)
                        init_amount = data["INIT_AMOUNT"]
                        total_asset = data["CUR_AMOUNT"] + evlu_amt_dec
                        rate = float((total_asset - init_amount) / init_amount * 100) if init_amount else 0.0
                        pfls_amt_dec = total_asset - init_amount
                        pfls_amt = _to_amount(pfls_amt_dec)
                    else:
                        # INIT_AMOUNT = 0: KIS API 값 사용 (외부 매수 자동 등록 종목)
                        rate = float(buy_item.get("evlu_pfls_rt", 0) or 0)
                        pfls_amt = _to_amount(buy_item.get("evlu_pfls_amt", 0))

                    result_data = {
                        **data,
                        "ST_NM": buy_item.get("prdt_name"),
                        "HLDG_QTY": buy_item.get("hldg_qty"),
                        "EVLU_AMT": _to_amount(evlu_amt_dec),
                        "EVLU_PFLS_RT": rate,
                        "EVLU_PFLS_AMT": pfls_amt,
                        "PRPR": float(buy_item.get("prpr", 0) or 0),
                    }
                    results.append(result_data)

            # 2. swing_list에만 있는 항목 추가 (보유 주식 없음, evlu_amt = 0)
            for swing in swing_list:
                if swing["ST_CODE"] not in buy_dict:
                    if swing["INIT_AMOUNT"]:
                        init_amount = swing["INIT_AMOUNT"]
                        rate = float((swing["CUR_AMOUNT"] - init_amount) / init_amount * 100)
                        pfls_amt = _to_amount(swing["CUR_AMOUNT"] - init_amount)
                    else:
                        rate = 0.0
                        pfls_amt = 0.0 if overseas else 0
                    result_data = {
                        **swing,
                        "EVLU_PFLS_RT": rate,
                        "EVLU_PFLS_AMT": pfls_amt,
                    }
                    results.append(result_data)

            await self.db.commit()

            # output2에서 계좌 요약 정보 매핑
            total_eval = _to_amount(output2.get("tot_evlu_amt", 0))  # 현재 총평가금(현금 포함)
            evlu_pfls = _to_amount(output2.get("evlu_pfls_smtl_amt", 0))
            # 투자전 원금 = 총평가금 - 평가손익. 현금은 손익이 0이라 상쇄되므로 현금 포함 총원금이 됨
            principal = total_eval - evlu_pfls
            profit_rate = round(evlu_pfls / principal * 100, 2) if principal else 0.0

            summary = {
                "TOTAL_INVESTMENT_AMOUNT": total_eval,
                "TOTAL_PRINCIPAL": principal,
                "TOTAL_PROFIT": evlu_pfls,
                "TOTAL_PROFIT_RATE": profit_rate,
                "CASH_ASSET": _to_amount(output2.get("dnca_tot_amt", 0)) if cash_supported else None,
            }

            return {"list": results, "summary": summary}

        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 매핑 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 매핑에 실패했습니다")

    async def get_active_swings(self) -> List:
        """활성화된 스윙 목록 조회 (배치용)"""
        return await self.repo.find_active_swings()

    async def get_active_domestic_swings(self) -> List:
        """활성화된 국내 스윙 목록 조회"""
        return await self.repo.find_active_domestic_swings()

    async def get_active_overseas_swings(self) -> List:
        """활성화된 해외 스윙 목록 조회"""
        return await self.repo.find_active_overseas_swings()

    async def _calculate_indicators(self, mrkt_code: str, st_code: str) -> dict | None:
        """지표 계산 공통 로직

        Args:
            mrkt_code: 시장 코드
            st_code: 종목 코드

        Returns:
            지표 데이터 dict 또는 None (데이터 부족/검증 실패 시)
        """
        stock_service = StockService(self.db)
        start_date = datetime.now() - relativedelta(years=3)
        price_history = await stock_service.get_stock_history(mrkt_code, st_code, start_date)

        if not price_history or len(price_history) < 20:
            logger.warning(
                f"[{st_code}] 데이터 부족: "
                f"{len(price_history) if price_history else 0}일"
            )
            return None

        df = pd.DataFrame(price_history)

        # 일평균 거래대금 계산 (최근 20일)
        recent_20 = df.tail(20) if len(df) >= 20 else df
        avg_daily_amount = float(
            (recent_20['ACML_VOL'].astype(float) * recent_20['STCK_CLPR'].astype(float)).mean()
        )

        # 백테스트와 동일하게 매수용 OBV z-score 14일 기준으로 통일
        indicators = TechnicalIndicators.prepare_indicators_from_df(df, obv_lookback=14)

        if len(indicators) < 15:
            logger.warning(f"[{st_code}] 지표 데이터 부족 (15일 미만, OBV z-score 14일 계산 불가)")
            return None

        yesterday = indicators.iloc[-1]
        required_cols = ['ema20', 'adx', 'plus_di', 'minus_di', 'atr', 'obv', 'obv_z']
        if not all(col in yesterday.index for col in required_cols):
            logger.warning(f"[{st_code}] 필수 지표 누락")
            return None

        if any(pd.isna(yesterday[col]) for col in required_cols):
            logger.warning(f"[{st_code}] 지표 값 NaN")
            return None

        # OBV diff 최근 13일 추출 (NaN 필터링)
        # 슬라이스 크기 13은 다음 사용처를 모두 커버:
        #   - 매수용 OBV z-score 14일 (13개 과거 diff + 오늘 1개 = 14개)
        #   - 2차 익절용 OBV z-score 14일 (동일)
        #   - 단기 OBV 누적 변화 3일 (2개 과거 diff + 오늘 1개)
        # ⚠️ 매수/익절 lookback을 14 초과로 늘리려면 이 슬라이스도 함께 키워야 함
        obv_diffs = indicators['obv'].diff()
        recent_obv_diffs = [float(x) for x in obv_diffs.iloc[-13:].tolist() if not pd.isna(x)]

        # DM14 역산 (+DM14 = +DI × ATR / 100)
        atr = float(yesterday['atr'])
        plus_dm14 = (float(yesterday['plus_di']) * atr) / 100
        minus_dm14 = (float(yesterday['minus_di']) * atr) / 100

        indicators_data = {
            "ema20": float(yesterday['ema20']),
            "adx": float(yesterday['adx']),
            "plus_dm14": plus_dm14,
            "minus_dm14": minus_dm14,
            "atr": atr,
            "obv": float(yesterday['obv']),
            "obv_z": float(yesterday['obv_z']),
            "obv_recent_diffs": recent_obv_diffs,
            "close": float(yesterday['STCK_CLPR']),
            "open": float(yesterday['STCK_OPRC']),
            "high": float(yesterday['STCK_HGPR']),
            "low": float(yesterday['STCK_LWPR']),
            "date": yesterday['STCK_BSOP_DATE'],
            "avg_daily_amount": avg_daily_amount,
        }

        return indicators_data

    async def cache_single_indicators(self, mrkt_code: str, st_code: str) -> bool:
        """단일 종목 지표 캐싱 (스윙 등록 시 호출)"""
        try:
            redis_client = await Redis.get_connection()
            indicators_data = await self._calculate_indicators(mrkt_code, st_code)
            if not indicators_data:
                return False

            ttl = market_cache_ttl(mrkt_code)

            await redis_client.setex(
                f"indicators:{st_code}",
                ttl,
                json.dumps(indicators_data)
            )

            logger.info(
                f"[{st_code}] 지표 캐싱 완료: EMA={indicators_data['ema20']:.2f}, "
                f"ADX={indicators_data['adx']:.1f}, "
                f"ATR={indicators_data['atr']:.2f}, "
                f"OBV_Z={indicators_data['obv_z']:.2f}"
            )
            return True

        except Exception as e:
            logger.error(f"[{st_code}] 지표 캐싱 실패: {e}")
            return False

    async def warmup_ema_cache(self, redis_client, scope: str = "all") -> Dict[str, Any]:
        """
        지표 캐시 워밍업 (애플리케이션 시작 시 또는 스케줄 배치)

        - 대상: SWING_TRADE.USE_YN = 'Y'인 종목
        - 작업: 과거 3년 데이터로 지표 계산 → Redis 저장
        - 저장 지표: EMA20, ADX, +DI, -DI, ATR, OBV-Z

        Args:
            redis_client: Redis 클라이언트
            scope: 워밍업 대상 시장
                - "domestic": 국내(J 등 NASD 외)만
                - "overseas": 미국(NASD)만
                - "all": 전체 (기본값, 앱 시작 시 사용)

        Returns:
            워밍업 결과 (성공/실패 건수)
        """

        logger.info(f"=== 지표 캐시 워밍업 시작 (scope={scope}) ===")

        success_count = 0
        fail_count = 0

        try:
            # 1. 활성 종목 코드 조회
            active_codes = await self.repo.find_active_stock_codes()
            if scope == "overseas":
                active_codes = [(m, s) for m, s in active_codes if m == "NASD"]
            elif scope == "domestic":
                active_codes = [(m, s) for m, s in active_codes if m != "NASD"]
            logger.info(f"활성 종목 수: {len(active_codes)}개")

            if not active_codes:
                logger.info("활성 종목 없음, 워밍업 스킵")
                return {"success": 0, "fail": 0, "total": 0}

            # 2. 각 종목별 지표 계산 및 캐싱
            for mrkt_code, st_code in active_codes:
                try:
                    indicators_data = await self._calculate_indicators(mrkt_code, st_code)
                    if not indicators_data:
                        fail_count += 1
                        continue

                    ttl = market_cache_ttl(mrkt_code)

                    await redis_client.setex(
                        f"indicators:{st_code}",
                        ttl,
                        json.dumps(indicators_data)
                    )

                    logger.info(
                        f"[{st_code}] 지표 캐싱 완료: EMA={indicators_data['ema20']:.2f}, "
                        f"ADX={indicators_data['adx']:.1f}, "
                        f"+DM14={indicators_data['plus_dm14']:.2f}, "
                        f"-DM14={indicators_data['minus_dm14']:.2f}, "
                        f"ATR={indicators_data['atr']:.2f}, "
                        f"OBV={indicators_data['obv']:.0f}, "
                        f"OBV_Z={indicators_data['obv_z']:.2f}"
                    )
                    success_count += 1

                except Exception as e:
                    logger.error(f"[{st_code}] 지표 캐싱 실패: {e}", exc_info=True)
                    fail_count += 1

            result = {
                "success": success_count,
                "fail": fail_count,
                "total": len(active_codes)
            }
            logger.info(
                f"=== 지표 캐시 워밍업 완료: 성공 {success_count}, 실패 {fail_count} ==="
            )
            return result

        except SQLAlchemyError as e:
            logger.error(f"지표 캐시 워밍업 DB 오류: {e}", exc_info=True)
            raise DatabaseError("지표 캐시 워밍업에 실패했습니다")