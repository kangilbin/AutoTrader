"""
Swing Service - 비즈니스 로직 및 트랜잭션 관리
"""
import asyncio

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from typing import List, Dict, Any
from datetime import datetime
from decimal import Decimal
import logging

import talib as ta
import numpy as np
import pandas as pd

from app.domain.swing.repository import SwingRepository
from app.domain.swing.entity import SwingTrade, EmaOption
from app.domain.swing.schemas import SwingCreateRequest, SwingResponse
from app.domain.stock.service import StockService
from app.domain.stock.stock_data_batch import fetch_and_store_3_years_data
from app.exceptions import DatabaseError, NotFoundError, DuplicateError
from app.external.kis_api import get_stock_balance
from app.common.redis import Redis

logger = logging.getLogger(__name__)


class SwingService:
    """스윙 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SwingRepository(db)

    async def create_swing(self, user_id: str, request: SwingCreateRequest) -> dict:
        """스윙 전략 등록"""
        try:
            # 도메인 엔티티 생성 (비즈니스 검증)
            swing = SwingTrade.create(
                account_no=request.ACCOUNT_NO,
                st_code=request.ST_CODE,
                init_amount=Decimal(request.INIT_AMOUNT),
                swing_type=request.SWING_TYPE,
                buy_ratio=request.BUY_RATIO,
                sell_ratio=request.SELL_RATIO
            )

            db_swing = await self.repo.save(swing)

            # 이평선 전략인 경우 옵션 저장
            if request.SWING_TYPE == 'A':
                ema = EmaOption(
                    account_no=request.ACCOUNT_NO,
                    st_code=request.ST_CODE,
                    short_term=request.SHORT_TERM,
                    medium_term=request.MEDIUM_TERM,
                    long_term=request.LONG_TERM
                )
                ema.validate()
                await self.repo.save_ema_option(ema)

            await self.db.commit()

            # 데이터 적재 여부 확인 후 백그라운드 실행
            stock_service = StockService(self.db)
            stock_info = await stock_service.get_stock_info(request.ST_CODE)

            if stock_info.get("DATA_YN") == 'N':
                asyncio.create_task(
                    fetch_and_store_3_years_data(user_id, request.ST_CODE, stock_info)
                )
                logger.info(f"[{request.ST_CODE}] 데이터 적재 백그라운드 태스크 시작")

            # 등록된 종목의 지표 캐싱
            await self.cache_single_indicators(request.ST_CODE)

            return SwingResponse.model_validate(db_swing).model_dump()

        except IntegrityError as e:
            await self.db.rollback()
            logger.error(f"스윙 등록 실패 (중복): {e}", exc_info=True)
            raise DuplicateError("스윙 전략", request.ST_CODE)
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 등록 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 등록에 실패했습니다")

    async def get_swing(self, swing_id: int) -> dict:
        """스윙 조회"""
        swing = await self.repo.find_by_id(swing_id)
        if not swing:
            raise NotFoundError("스윙 전략", swing_id)
        return SwingResponse.model_validate(swing).model_dump()

    async def update_swing(self, swing_id: int, data: dict) -> dict:
        """스윙 수정"""
        try:
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

    async def mapping_swing(self, user_id: str, account_no: str) -> List[dict]:
        """스윙 목록과 보유 주식 매핑"""
        try:
            swing_list = await self.repo.find_all_by_account_no(account_no)
            buy_list = await get_stock_balance(user_id)

            swing_dict = {swing["ST_CODE"]: swing for swing in swing_list}
            buy_dict = {item.get("pdno"): item for item in buy_list if item.get("pdno")}
            results = []

            # 1. buy_list 기준으로 처리 (기존 로직)
            for buy_item in buy_list:
                st_code = buy_item.get("pdno")
                if not st_code:
                    continue

                if st_code not in swing_dict:
                    # 새 스윙 등록
                    swing = SwingTrade.create(
                        account_no=account_no,
                        st_code=st_code,
                        init_amount=Decimal(0),
                        swing_type='S'
                    )
                    swing.use_yn = 'N'
                    db_swing = await self.repo.save(swing)
                    swing_result = SwingResponse.model_validate(db_swing).model_dump()
                    result_data = {
                        **swing_result,
                        "ST_NM": buy_item.get("prdt_name"),
                        "HLDG_QTY": buy_item.get("hldg_qty"),
                        "EVLU_AMT": buy_item.get("evlu_amt"),
                    }
                    results.append(result_data)
                else:
                    # 기존 데이터 merge
                    data = swing_dict[st_code]
                    init_amount = data["INIT_AMOUNT"] if data["INIT_AMOUNT"] else 1
                    rate = float((data["CUR_AMOUNT"] - data["INIT_AMOUNT"]) / init_amount * 100)
                    result_data = {
                        **data,
                        # "SWING_ID": data["SWING_ID"],
                        # "ST_CODE": data["ST_CODE"],
                        # "ACCOUNT_NO": data["ACCOUNT_NO"],
                        # "USE_YN": data["USE_YN"],
                        # "INIT_AMOUNT": data["INIT_AMOUNT"],
                        # "CUR_AMOUNT": data["CUR_AMOUNT"],
                        # "SWING_TYPE": data["SWING_TYPE"],
                        "ST_NM": buy_item.get("prdt_name"),
                        "HLDG_QTY": buy_item.get("hldg_qty"),
                        "EVLU_AMT": buy_item.get("evlu_amt"),
                        "EVLU_PFLS_RT": rate,
                        "EVLU_PFLS_AMT": data["INIT_AMOUNT"] - data["CUR_AMOUNT"],
                    }
                    results.append(result_data)

            # 2. swing_list에만 있는 항목 추가
            for swing in swing_list:
                if swing["ST_CODE"] not in buy_dict:
                    init_amount = swing["INIT_AMOUNT"] if swing["INIT_AMOUNT"] else 1
                    rate = float((swing["CUR_AMOUNT"] - swing["INIT_AMOUNT"]) / init_amount * 100)
                    result_data = {
                        **swing,
                        # "SWING_ID": swing["SWING_ID"],
                        # "ST_CODE": swing["ST_CODE"],
                        # "ST_NM": swing["ST_NM"],
                        # "ACCOUNT_NO": swing["ACCOUNT_NO"],
                        # "USE_YN": swing["USE_YN"],
                        # "INIT_AMOUNT": swing["INIT_AMOUNT"],
                        # "CUR_AMOUNT": swing["CUR_AMOUNT"],
                        # "SWING_TYPE": swing["SWING_TYPE"],
                        "EVLU_PFLS_RT": rate,
                        "EVLU_PFLS_AMT": swing["INIT_AMOUNT"] - swing["CUR_AMOUNT"],
                    }
                    results.append(result_data)

            await self.db.commit()
            return results

        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"스윙 매핑 실패: {e}", exc_info=True)
            raise DatabaseError("스윙 매핑에 실패했습니다")

    async def get_active_swings(self) -> List:
        """활성화된 스윙 목록 조회 (배치용)"""
        return await self.repo.find_active_swings()

    async def get_pending_sell_swings(self) -> List:
        """매도 대기 중인 스윙 목록 조회 (SIGNAL 4/5)"""
        return await self.repo.find_pending_sell_swings()

    async def get_holding_swings(self) -> List:
        """포지션 보유 중인 스윙 목록 조회 (SIGNAL 1/2)"""
        return await self.repo.find_holding_swings()

    async def get_swings_by_signals(self, signals: List[int]) -> List:
        """특정 SIGNAL 값의 스윙 목록 조회"""
        return await self.repo.find_swings_by_signals(signals)

    async def transition_signal(self, swing_id: int, new_signal: int, reason: str = None) -> dict:
        """
        신호 상태 전환 (유효성 검증 포함)

        SIGNAL 상태:
        - 0: 대기 (포지션 없음)
        - 1: 1차 매수 완료
        - 2: 2차 매수 완료
        - 3: 장중 손절 완료
        - 4: 1차 매도 대기 (50% 매도 대기, 종가 확정)
        - 5: 2차 매도 대기 (전량 매도 대기, 종가 확정)

        Args:
            swing_id: 스윙 ID
            new_signal: 새로운 신호 값 (0, 1, 2, 3, 4, 5)
            reason: 전환 사유 (로깅용)

        Returns:
            업데이트된 스윙 정보

        Raises:
            ValidationError: 잘못된 상태 전환
        """
        from app.exceptions import ValidationError

        try:
            # 현재 스윙 조회
            swing = await self.repo.find_by_id(swing_id)
            if not swing:
                raise NotFoundError("스윙 전략", swing_id)

            current_signal = swing.SIGNAL if hasattr(swing, 'SIGNAL') else 0

            # 상태 전환 유효성 검증 (확장된 규칙)
            valid_transitions = {
                0: [1],           # 대기 → 1차 매수만 가능
                1: [2, 3, 4, 5],  # 1차 매수 → 2차 매수, 장중손절, 1차/2차 매도대기
                2: [3, 4, 5],     # 2차 매수 → 장중손절, 1차/2차 매도대기
                3: [0],           # 장중 손절 → 초기화
                4: [1, 0],        # 1차 매도 대기 → 잔량보유(50%매도후), 전량청산
                5: [0]            # 2차 매도 대기 → 전량 청산(초기화)
            }

            if new_signal not in valid_transitions.get(current_signal, []):
                raise ValidationError(
                    f"잘못된 신호 전환: {current_signal} → {new_signal}. "
                    f"허용된 전환: {valid_transitions.get(current_signal, [])}"
                )

            # 업데이트
            result = await self.update_swing(
                swing_id,
                {
                    "SIGNAL": new_signal,
                    "MOD_DT": datetime.now()
                }
            )

            logger.info(
                f"신호 전환 완료: SWING_ID={swing_id}, {current_signal} → {new_signal}"
                + (f" (사유: {reason})" if reason else "")
            )

            return result

        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"신호 전환 실패: {e}", exc_info=True)
            raise DatabaseError("신호 전환에 실패했습니다")

    async def reset_completed_signals(self) -> int:
        """
        매도 완료(SIGNAL=3) 상태를 초기화(SIGNAL=0)

        배치 작업으로 주기적으로 실행하거나,
        사용자 요청 시 수동으로 실행

        Returns:
            초기화된 스윙 개수
        """
        try:
            count = await self.repo.reset_signals_by_value(old_value=3, new_value=0)
            await self.db.commit()
            logger.info(f"매도 완료 신호 초기화: {count}건")
            return count
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"신호 초기화 실패: {e}", exc_info=True)
            raise DatabaseError("신호 초기화에 실패했습니다")

    async def cache_single_indicators(self, st_code: str) -> bool:
        """
        단일 종목 지표 캐싱 (스윙 등록 시 호출)

        Args:
            st_code: 종목 코드

        Returns:
            캐싱 성공 여부
        """
        from app.domain.swing.indicators import TechnicalIndicators
        import json

        try:
            stock_service = StockService(self.db)
            redis_client = await Redis.get_connection()

            # 과거 3년 데이터 조회
            start_date = datetime.now() - relativedelta(years=3)
            price_history = await stock_service.get_stock_history(st_code, start_date)

            if not price_history or len(price_history) < 20:
                logger.warning(
                    f"[{st_code}] 지표 캐싱 스킵 - 데이터 부족: "
                    f"{len(price_history) if price_history else 0}일"
                )
                return False

            # 지표 계산
            df = pd.DataFrame(price_history)
            indicators = TechnicalIndicators.prepare_indicators_from_df(df)

            if len(indicators) < 2:
                logger.warning(f"[{st_code}] 지표 데이터 부족 (2일 미만)")
                return False

            today = indicators.iloc[-1]
            yesterday = indicators.iloc[-2]

            required_cols = ['ema_20', 'adx', 'plus_di', 'minus_di']
            if not all(col in today.index for col in required_cols):
                logger.warning(f"[{st_code}] 필수 지표 누락")
                return False

            if any(pd.isna(today[col]) for col in required_cols):
                logger.warning(f"[{st_code}] 지표 값 NaN")
                return False

            indicators_data = {
                "ema20": {
                    "today": float(today['ema_20']),
                    "yesterday": float(yesterday['ema_20'])
                },
                "adx": {
                    "today": float(today['adx']),
                    "yesterday": float(yesterday['adx'])
                },
                "plus_di": {
                    "today": float(today['plus_di']),
                    "yesterday": float(yesterday['plus_di'])
                },
                "minus_di": {
                    "today": float(today['minus_di']),
                    "yesterday": float(yesterday['minus_di'])
                },
                "date": today['STCK_BSOP_DATE']
            }

            await redis_client.setex(
                f"indicators:{st_code}",
                604800,
                json.dumps(indicators_data)
            )

            logger.info(
                f"[{st_code}] 지표 캐싱 완료: EMA={indicators_data['ema20']['today']:.2f}, "
                f"ADX={indicators_data['adx']['today']:.1f} "
                f"(데이터: {len(price_history)}일)"
            )
            return True

        except Exception as e:
            logger.error(f"[{st_code}] 지표 캐싱 실패: {e}")
            return False

    async def warmup_ema_cache(self, redis_client) -> Dict[str, Any]:
        """
        지표 캐시 워밍업 (애플리케이션 시작 시 또는 스케줄 배치)

        - 대상: SWING_TRADE.USE_YN = 'Y'인 종목
        - 작업: 과거 3년 데이터로 지표 계산 → Redis 저장
        - 저장 지표: EMA20, ADX, +DI, -DI (오늘/어제 2일치)

        Args:
            redis_client: Redis 클라이언트

        Returns:
            워밍업 결과 (성공/실패 건수)
        """
        from app.domain.swing.indicators import TechnicalIndicators
        import json

        logger.info("=== 지표 캐시 워밍업 시작 (EMA20, ADX, DI) ===")

        stock_service = StockService(self.db)
        success_count = 0
        fail_count = 0

        try:
            # 1. 활성 종목 코드 조회
            active_codes = await self.repo.find_active_stock_codes()
            logger.info(f"활성 종목 수: {len(active_codes)}개")

            if not active_codes:
                logger.info("활성 종목 없음, 워밍업 스킵")
                return {"success": 0, "fail": 0, "total": 0}

            # 2. 각 종목별 지표 계산 및 캐싱
            for st_code in active_codes:
                try:
                    # 과거 3년 데이터 조회
                    start_date = datetime.now() - relativedelta(year=3)
                    price_history = await stock_service.get_stock_history(st_code, start_date)

                    if not price_history or len(price_history) < 20:
                        logger.warning(
                            f"[{st_code}] 데이터 부족: "
                            f"{len(price_history) if price_history else 0}일"
                        )
                        fail_count += 1
                        continue

                    # DataFrame 생성 및 지표 계산
                    df = pd.DataFrame(price_history)
                    indicators = TechnicalIndicators.prepare_indicators_from_df(df)

                    if len(indicators) < 2:
                        logger.warning(f"[{st_code}] 지표 데이터 부족 (2일 미만)")
                        fail_count += 1
                        continue

                    # 최근 2일 데이터 추출
                    today = indicators.iloc[-1]
                    yesterday = indicators.iloc[-2]

                    # 필수 지표 존재 여부 확인
                    required_cols = ['ema_20', 'adx', 'plus_di', 'minus_di']
                    if not all(col in today.index for col in required_cols):
                        logger.warning(f"[{st_code}] 필수 지표 누락")
                        fail_count += 1
                        continue

                    # NaN 체크
                    if any(pd.isna(today[col]) for col in required_cols):
                        logger.warning(f"[{st_code}] 지표 값 NaN")
                        fail_count += 1
                        continue

                    ema20 = float(today['ema_20'])
                    indicators_data = {
                        "ema20": {
                            "today": float(today['ema_20']),
                            "yesterday": float(yesterday['ema_20'])
                        },
                        "adx": {
                            "today": float(today['adx']),
                            "yesterday": float(yesterday['adx'])
                        },
                        "plus_di": {
                            "today": float(today['plus_di']),
                            "yesterday": float(yesterday['plus_di'])
                        },
                        "minus_di": {
                            "today": float(today['minus_di']),
                            "yesterday": float(yesterday['minus_di'])
                        },
                        "date": today['STCK_BSOP_DATE']
                    }

                    await redis_client.setex(
                        f"indicators:{st_code}",
                        604800,
                        json.dumps(indicators_data)
                    )

                    logger.info(
                        f"[{st_code}] 지표 캐싱 완료: EMA={ema20:.2f}, "
                        f"ADX={indicators_data['adx']['today']:.1f}, "
                        f"+DI={indicators_data['plus_di']['today']:.1f}, "
                        f"-DI={indicators_data['minus_di']['today']:.1f} "
                        f"(데이터: {len(price_history)}일)"
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