"""
Swing Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from typing import List
from datetime import datetime
from decimal import Decimal
import logging

from app.domain.swing.repository import SwingRepository
from app.domain.swing.entity import SwingTrade, EmaOption
from app.domain.swing.schemas import SwingCreateRequest, SwingResponse
from app.exceptions import DatabaseError, NotFoundError, DuplicateError
from app.external.kis_api import get_stock_balance

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

    async def delete_swing(self, swing_id: int) -> bool:
        """스윙 삭제"""
        try:
            result = await self.repo.delete(swing_id)
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

            swing_dict = {swing.ST_CODE: swing for swing in swing_list}
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
                        swing_type='B'
                    )
                    swing.use_yn = 'N'
                    db_swing = await self.repo.save(swing)
                    swing_result = SwingResponse.model_validate(db_swing).model_dump()
                    result_data = {
                        **swing_result,
                        "ST_NM": buy_item.get("prdt_name"),
                        "QTY": buy_item.get("hldg_qty"),
                    }
                    results.append(result_data)
                else:
                    # 기존 데이터 merge
                    data = swing_dict[st_code]
                    init_amount = data.INIT_AMOUNT if data.INIT_AMOUNT else 1
                    rate = float((data.CUR_AMOUNT - data.INIT_AMOUNT) / init_amount * 100)
                    result_data = {
                        "SWING_ID": data.SWING_ID,
                        "ST_CODE": data.ST_CODE,
                        "ACCOUNT_NO": data.ACCOUNT_NO,
                        "USE_YN": data.USE_YN,
                        "INIT_AMOUNT": data.INIT_AMOUNT,
                        "CUR_AMOUNT": data.CUR_AMOUNT,
                        "SWING_TYPE": data.SWING_TYPE,
                        "ST_NM": buy_item.get("prdt_name"),
                        "QTY": buy_item.get("hldg_qty"),
                        "RATE": rate,
                    }
                    results.append(result_data)

            # 2. swing_list에만 있는 항목 추가
            for swing in swing_list:
                if swing.ST_CODE not in buy_dict:
                    init_amount = swing.INIT_AMOUNT if swing.INIT_AMOUNT else 1
                    rate = float((swing.CUR_AMOUNT - swing.INIT_AMOUNT) / init_amount * 100)
                    result_data = {
                        "SWING_ID": swing.SWING_ID,
                        "ST_CODE": swing.ST_CODE,
                        "ACCOUNT_NO": swing.ACCOUNT_NO,
                        "USE_YN": swing.USE_YN,
                        "INIT_AMOUNT": swing.INIT_AMOUNT,
                        "CUR_AMOUNT": swing.CUR_AMOUNT,
                        "SWING_TYPE": swing.SWING_TYPE,
                        "ST_NM": None,
                        "QTY": 0,
                        "RATE": rate,
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

    async def transition_signal(self, swing_id: int, new_signal: int, reason: str = None) -> dict:
        """
        신호 상태 전환 (유효성 검증 포함)

        Args:
            swing_id: 스윙 ID
            new_signal: 새로운 신호 값 (0, 1, 2, 3)
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

            # 상태 전환 유효성 검증
            valid_transitions = {
                0: [1],        # 대기 → 1차 매수만 가능
                1: [2, 3],     # 1차 매수 → 2차 매수 또는 매도
                2: [3],        # 2차 매수 → 매도만 가능
                3: [0]         # 매도 → 초기화만 가능
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