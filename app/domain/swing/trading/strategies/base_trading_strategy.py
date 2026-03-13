"""
실시간 거래 전략의 추상 베이스 클래스
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TradingStrategy(ABC):
    """
    실시간 거래 전략 베이스 클래스

    모든 실시간 거래 전략은 이 클래스를 상속하고
    check_entry_signal, check_exit_signal, check_second_buy_signal을 구현해야 합니다.

    Attributes:
        name: 전략 이름 (클래스 속성으로 정의)
    """

    name: str = "기본 전략"

    @classmethod
    @abstractmethod
    async def check_entry_signal(
        cls,
        redis_client,
        swing_id: int,
        symbol: str,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """
        1차 매수 진입 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            redis_client: Redis 클라이언트
            swing_id: 스윙 ID (연속성 체크용)
            symbol: 종목코드
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량
            acml_vol: 누적거래량
            prdy_vrss_vol_rate: 전일대비 거래량 비율
            prdy_ctrt: 전일대비 상승률
            cached_indicators: 실시간 증분 계산된 지표 (필수)

        Returns:
            매수 신호 정보 또는 None
        """
        pass

    @classmethod
    @abstractmethod
    async def check_exit_signal(
        cls,
        redis_client,
        position_id: int,
        symbol: str,
        current_price: Decimal,
        entry_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        cached_indicators: Dict
    ) -> Dict:
        """
        매도 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            redis_client: Redis 클라이언트
            position_id: 포지션 ID (SWING_ID)
            symbol: 종목코드
            current_price: 현재가
            entry_price: 진입가
            frgn_ntby_qty: 외국인 순매수량
            acml_vol: 누적거래량
            cached_indicators: 실시간 증분 계산된 지표 (필수)

        Returns:
            매도 신호 정보 (action: "SELL" or "HOLD")
        """
        pass

    @classmethod
    @abstractmethod
    async def check_second_buy_signal(
        cls,
        redis_client,
        swing_id: int,
        symbol: str,
        entry_price: Decimal,
        hold_qty: int,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """
        2차 매수 신호 체크 (하위 클래스에서 구현 필수)

        Args:
            redis_client: Redis 클라이언트
            swing_id: 스윙 ID
            symbol: 종목 코드
            entry_price: 1차 매수가 (평균 단가)
            hold_qty: 보유 수량
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량 (당일 실시간)
            acml_vol: 누적 거래량 (당일 실시간)
            prdy_vrss_vol_rate: 전일 대비 거래량 비율 (%)
            cached_indicators: 실시간 증분 계산된 지표 (필수)

        Returns:
            2차 매수 신호 정보 또는 None
        """
        pass

    @classmethod
    @abstractmethod
    async def get_cached_indicators(cls, redis_client, symbol: str) -> Optional[Dict]:
        """
        Redis 캐시에서 지표

        Args:
            redis_client: 캐시 서버
            symbol: 주식 코드

        Returns:
            지표 캐시 정보
        """
        pass

    @classmethod
    async def check_trailing_stop_signal(
        cls,
        symbol: str,
        current_price,
        peak_price: int,
        signal: int,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """[2차 방어선] 장중 trailing stop — 하위 클래스에서 구현"""
        return None

    @classmethod
    async def process_trading_cycle(
        cls,
        swing,
        redis_client,
        cached_indicators: Dict,
        current_price_data: Dict,
        db  # DB 세션 추가
    ) -> Dict:
        """
        스윙 매매 사이클 처리 (SIGNAL 상태 머신)

        SIGNAL 흐름:
        - 0 → 1차 매수 신호 → 1
        - 1 → 손절 신호 → 3 (전량 매도 → 0)
        - 1 → 2차 매수 신호 → 2
        - 2 → 손절 신호 → 3 (전량 매도 → 0)
        - 3 → 2차 손절 매도 → 0

        Args:
            swing: SWING_TRADE 레코드
            redis_client: Redis 클라이언트
            cached_indicators: 실시간 증분 계산된 지표
            current_price_data: 현재가 데이터 (KIS API 응답)
            db: AsyncSession (거래 내역 저장용)

        Returns:
            처리 결과 딕셔너리
        """
        from ..order_executor import SwingOrderExecutor
        from app.domain.trade_history import TradeHistoryService

        swing_id = swing.SWING_ID
        st_code = swing.ST_CODE
        user_id = swing.USER_ID if hasattr(swing, 'USER_ID') else None
        current_signal = swing.SIGNAL if hasattr(swing, 'SIGNAL') else 0
        init_amount = Decimal(str(swing.INIT_AMOUNT)) if hasattr(swing, 'INIT_AMOUNT') else Decimal(0)
        buy_ratio = swing.BUY_RATIO if hasattr(swing, 'BUY_RATIO') else 50
        sell_ratio = swing.SELL_RATIO if hasattr(swing, 'SELL_RATIO') else 50
        entry_price = int(swing.ENTRY_PRICE) if hasattr(swing, 'ENTRY_PRICE') and swing.ENTRY_PRICE else 0
        hold_qty = swing.HOLD_QTY if hasattr(swing, 'HOLD_QTY') and swing.HOLD_QTY else 0

        # 현재가 데이터 추출
        current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))
        frgn_ntby_qty = int(current_price_data.get("frgn_ntby_qty", 0))
        acml_vol = int(current_price_data.get("acml_vol", 0))
        prdy_vrss_vol_rate = float(current_price_data.get("prdy_vrss_vol_rate", 100))
        prdy_ctrt = float(current_price_data.get("prdy_ctrt", 0))


        avg_daily_amount = cached_indicators["avg_daily_amount"]
        original_entry_price = entry_price
        original_hold_qty = hold_qty
        new_signal = current_signal
        peak_price = int(swing.PEAK_PRICE) if hasattr(swing, 'PEAK_PRICE') and swing.PEAK_PRICE else 0
        original_peak_price = peak_price

        # ------------------------------------------
        # 부분 실행 진행 중 체크 (신호 로직보다 우선)
        # ------------------------------------------
        partial_key = f"partial_exec:{swing_id}"
        partial_state_str = await redis_client.get(partial_key)

        if partial_state_str and user_id:
            partial_result = await SwingOrderExecutor.continue_partial_execution(
                redis_client=redis_client,
                swing_id=swing_id,
                user_id=user_id,
                st_code=st_code,
                current_price=current_price,
                avg_daily_amount=avg_daily_amount,
                cached_indicators=cached_indicators,
                current_entry_price=entry_price,
                current_hold_qty=hold_qty,
                db=db
            )
            entry_price = partial_result.get("entry_price", entry_price)
            hold_qty = partial_result.get("hold_qty", hold_qty)

            if partial_result.get("completed") or partial_result.get("aborted"):
                new_signal = partial_result.get("signal_on_complete", current_signal) or current_signal

            return {
                "new_signal": new_signal,
                "entry_price": entry_price,
                "hold_qty": hold_qty,
                "peak_price": peak_price,
                "updated": True,
            }

        # ------------------------------------------
        # 포지션 보유 중 장중 고가로 PEAK_PRICE 갱신
        # ------------------------------------------
        if current_signal in (1, 2, 3):
            current_high = int(current_price_data.get("stck_hgpr", int(current_price)))
            if current_high > peak_price:
                peak_price = current_high

        # ------------------------------------------
        # SIGNAL 0: 대기 상태 - 1차 매수 신호 확인
        # ------------------------------------------
        if current_signal == 0:
            entry_result = await cls.check_entry_signal(
                redis_client=redis_client,
                swing_id=swing_id,
                symbol=st_code,
                current_price=current_price,
                frgn_ntby_qty=frgn_ntby_qty,
                acml_vol=acml_vol,
                prdy_vrss_vol_rate=prdy_vrss_vol_rate,
                prdy_ctrt=prdy_ctrt,
                cached_indicators=cached_indicators
            )

            if entry_result and entry_result.get("action") == "BUY":
                if user_id:
                    target_amount = init_amount * Decimal(buy_ratio) / Decimal(100)
                    order_result = await SwingOrderExecutor.execute_buy_with_partial(
                        redis_client=redis_client,
                        swing_id=swing_id,
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        target_amount=target_amount,
                        avg_daily_amount=avg_daily_amount,
                        signal_on_complete=1,
                        db=db
                    )

                    if order_result.get("success"):
                        entry_price = order_result.get("avg_price", int(current_price))
                        hold_qty = order_result.get("qty", 0)
                        if order_result.get("completed", True):
                            new_signal = 1
                        peak_price = int(current_price)

                        # 거래 내역 저장 (비율 추가)
                        reasons = entry_result.get("reasons", ["1차 매수"]).copy()
                        reasons.append(f"{buy_ratio}%")
                        trade_service = TradeHistoryService(db)
                        await trade_service.record_trade(
                            swing_id=swing_id,
                            trade_type="B",
                            order_result=order_result,
                            reasons=reasons
                        )

                        # 2차 매수 시간 필터용 Redis 키 생성 (20분 TTL)
                        await redis_client.setex(
                            f"first_buy_time:{swing_id}",
                            1200,
                            datetime.now().isoformat()
                        )
                    else:
                        logger.error(f"[{st_code}] 1차 매수 실패: {order_result.get('reason')}")
                else:
                    logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")

        # ------------------------------------------
        # SIGNAL 1: 1차 매수 완료 - 손절 체크, 2차 매수 확인
        # ------------------------------------------
        elif current_signal == 1:
            if entry_price > 0:
                # 손절 신호 체크
                exit_result = await cls.check_exit_signal(
                    redis_client=redis_client,
                    position_id=swing_id,
                    symbol=st_code,
                    current_price=current_price,
                    entry_price=Decimal(entry_price),
                    frgn_ntby_qty=frgn_ntby_qty,
                    acml_vol=acml_vol,
                    cached_indicators=cached_indicators
                )

                if exit_result and exit_result.get("action") == "SELL":
                    logger.warning(
                        f"[{st_code}] 즉시 매도 신호 발동! 현재가={int(current_price):,}원, "
                        f"평단가={entry_price:,}원, 사유={exit_result.get('reason')}"
                    )

                    if user_id:
                        # 손절은 전량 TWAP 매도
                        order_result = await SwingOrderExecutor.execute_sell_with_partial(
                            redis_client=redis_client,
                            swing_id=swing_id,
                            user_id=user_id,
                            st_code=st_code,
                            current_price=current_price,
                            target_qty=hold_qty,
                            avg_daily_amount=avg_daily_amount,
                            signal_on_complete=0,
                            db=db
                        )

                        if order_result.get("success"):
                            hold_qty -= order_result.get("qty", 0)
                            if order_result.get("completed", True):
                                new_signal = 0
                                entry_price = 0
                                hold_qty = 0
                                peak_price = 0

                            # 거래 내역 저장
                            trade_service = TradeHistoryService(db)
                            await trade_service.record_trade(
                                swing_id=swing_id,
                                trade_type="S",
                                order_result=order_result,
                                reasons=exit_result.get("reasons", ["손절"])
                            )

                            logger.info(f"[{user_id} - 주식: {st_code}] 손절 전량 매도 완료, 사이클 종료")
                        else:
                            logger.error(f"[{st_code}] 손절 매도 실패: {order_result.get('reason')}")
                    else:
                        logger.warning(f"[{st_code}] USER_ID 없음, 손절 주문 실행 불가")

                else:
                    # 손절 아니면 장중 trailing stop 체크
                    ts_result = await cls.check_trailing_stop_signal(
                        symbol=st_code,
                        current_price=current_price,
                        peak_price=peak_price,
                        signal=current_signal,
                        cached_indicators=cached_indicators
                    )

                    if ts_result and ts_result.get("action") == "SELL_PRIMARY":
                        logger.warning(
                            f"[{user_id} - 주식: {st_code}] 2차 방어선 1차 매도 신호! 사유={ts_result.get('reasons')}"
                        )

                        if user_id:
                            # 1차 분할 매도 (sell_ratio%)
                            sell_qty = int(hold_qty * sell_ratio / 100)
                            order_result = await SwingOrderExecutor.execute_sell_with_partial(
                                redis_client=redis_client,
                                swing_id=swing_id,
                                user_id=user_id,
                                st_code=st_code,
                                current_price=current_price,
                                target_qty=sell_qty,
                                avg_daily_amount=avg_daily_amount,
                                signal_on_complete=3,
                                db=db
                            )

                            if order_result.get("success"):
                                hold_qty -= order_result.get("qty", 0)
                                if order_result.get("completed", True):
                                    new_signal = 3

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=ts_result.get("reasons", ["1차 분할 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    elif ts_result and ts_result.get("action") == "SELL_ALL":
                        logger.warning(
                            f"[{user_id} - 주식: {st_code}] 2차 방어선 전량 매도 신호! 사유={ts_result.get('reasons')}"
                        )

                        if user_id:
                            # 전량 매도
                            order_result = await SwingOrderExecutor.execute_sell_with_partial(
                                redis_client=redis_client,
                                swing_id=swing_id,
                                user_id=user_id,
                                st_code=st_code,
                                current_price=current_price,
                                target_qty=hold_qty,
                                avg_daily_amount=avg_daily_amount,
                                signal_on_complete=0,
                                db=db
                            )

                            if order_result.get("success"):
                                hold_qty -= order_result.get("qty", 0)
                                if order_result.get("completed", True):
                                    new_signal = 0
                                    entry_price = 0
                                    hold_qty = 0
                                    peak_price = 0

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=ts_result.get("reasons", ["전량 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] 전량 매도 완료, 사이클 종료")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] 전량 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    else:
                        # trailing stop 신호 없으면 2차 매수 조건 확인
                        entry_result = await cls.check_second_buy_signal(
                            redis_client=redis_client,
                            swing_id=swing_id,
                            symbol=st_code,
                            entry_price=Decimal(entry_price) if entry_price > 0 else current_price,
                            hold_qty=hold_qty,
                            current_price=current_price,
                            frgn_ntby_qty=frgn_ntby_qty,
                            acml_vol=acml_vol,
                            prdy_vrss_vol_rate=prdy_vrss_vol_rate,
                            cached_indicators=cached_indicators
                        )

                        if entry_result and entry_result.get("action") == "BUY":
                            if user_id:
                                second_target_amount = init_amount * Decimal(100 - buy_ratio) / Decimal(100)
                                order_result = await SwingOrderExecutor.execute_buy_with_partial(
                                    redis_client=redis_client,
                                    swing_id=swing_id,
                                    user_id=user_id,
                                    st_code=st_code,
                                    current_price=current_price,
                                    target_amount=second_target_amount,
                                    avg_daily_amount=avg_daily_amount,
                                    signal_on_complete=2,
                                    db=db
                                )

                                if order_result.get("success"):
                                    new_avg_price = order_result.get("avg_price", int(current_price))
                                    new_qty = order_result.get("qty", 0)
                                    entry_price = SwingOrderExecutor.calculate_avg_entry_price(
                                        prev_qty=hold_qty,
                                        prev_price=entry_price,
                                        new_qty=new_qty,
                                        new_price=new_avg_price
                                    )
                                    hold_qty = hold_qty + new_qty
                                    if order_result.get("completed", True):
                                        new_signal = 2

                                    # 거래 내역 저장 (비율 추가)
                                    reasons = entry_result.get("reasons", ["2차 매수"]).copy()
                                    reasons.append(f"{100 - buy_ratio}%")
                                    trade_service = TradeHistoryService(db)
                                    await trade_service.record_trade(
                                        swing_id=swing_id,
                                        trade_type="B",
                                        order_result=order_result,
                                        reasons=reasons
                                    )

                                    logger.info(f"[{user_id} - 주식: {st_code}] 2차 매수 완료: 새 평단가={entry_price:,}원, 총수량={hold_qty}주")
                                else:
                                    logger.error(f"[{user_id} - 주식: {st_code}] 2차 매수 실패: {order_result.get('reason')}")
                            else:
                                logger.warning(f"[{st_code}] USER_ID 없음, 주문 실행 불가")


        # ------------------------------------------
        # SIGNAL 2: 2차 매수 완료 - 손절 체크, EOD 매도 체크
        # ------------------------------------------
        elif current_signal == 2:
            if entry_price > 0:
                # 손절 신호 체크
                exit_result = await cls.check_exit_signal(
                    redis_client=redis_client,
                    position_id=swing_id,
                    symbol=st_code,
                    current_price=current_price,
                    entry_price=Decimal(entry_price),
                    frgn_ntby_qty=frgn_ntby_qty,
                    acml_vol=acml_vol,
                    cached_indicators=cached_indicators
                )

                if exit_result and exit_result.get("action") == "SELL":
                    logger.warning(
                        f"[{st_code}] 즉시 매도 신호 발동! 현재가={int(current_price):,}원, "
                        f"평단가={entry_price:,}원, 사유={exit_result.get('reason')}"
                    )

                    if user_id:
                        # 손절은 전량 TWAP 매도
                        order_result = await SwingOrderExecutor.execute_sell_with_partial(
                            redis_client=redis_client,
                            swing_id=swing_id,
                            user_id=user_id,
                            st_code=st_code,
                            current_price=current_price,
                            target_qty=hold_qty,
                            avg_daily_amount=avg_daily_amount,
                            signal_on_complete=0,
                            db=db
                        )

                        if order_result.get("success"):
                            hold_qty -= order_result.get("qty", 0)
                            if order_result.get("completed", True):
                                new_signal = 0
                                entry_price = 0
                                hold_qty = 0
                                peak_price = 0

                            # 거래 내역 저장
                            trade_service = TradeHistoryService(db)
                            await trade_service.record_trade(
                                swing_id=swing_id,
                                trade_type="S",
                                order_result=order_result,
                                reasons=exit_result.get("reasons", ["손절"])
                            )

                            logger.info(f"[{user_id} - 주식: {st_code}] 손절 매도 완료, 사이클 종료")
                        else:
                            logger.error(f"[{user_id} - 주식: {st_code}] 손절 매도 실패: {order_result.get('reason')}")
                    else:
                        logger.warning(f"[{st_code}] USER_ID 없음, 손절 주문 실행 불가")

                else:
                    # 손절 아니면 장중 trailing stop 체크
                    ts_result = await cls.check_trailing_stop_signal(
                        symbol=st_code,
                        current_price=current_price,
                        peak_price=peak_price,
                        signal=current_signal,
                        cached_indicators=cached_indicators
                    )

                    if ts_result and ts_result.get("action") == "SELL_PRIMARY":
                        if user_id:
                            # 1차 분할 매도 (sell_ratio%)
                            sell_qty = int(hold_qty * sell_ratio / 100)
                            order_result = await SwingOrderExecutor.execute_sell_with_partial(
                                redis_client=redis_client,
                                swing_id=swing_id,
                                user_id=user_id,
                                st_code=st_code,
                                current_price=current_price,
                                target_qty=sell_qty,
                                avg_daily_amount=avg_daily_amount,
                                signal_on_complete=3,
                                db=db
                            )

                            if order_result.get("success"):
                                hold_qty -= order_result.get("qty", 0)
                                if order_result.get("completed", True):
                                    new_signal = 3

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=ts_result.get("reasons", ["1차 분할 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] 1차 분할 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    elif ts_result and ts_result.get("action") == "SELL_ALL":
                        logger.warning(
                            f"[{st_code}] 2차 방어선 전량 매도 신호! 사유={ts_result.get('reasons')}"
                        )

                        if user_id:
                            # 전량 매도
                            order_result = await SwingOrderExecutor.execute_sell_with_partial(
                                redis_client=redis_client,
                                swing_id=swing_id,
                                user_id=user_id,
                                st_code=st_code,
                                current_price=current_price,
                                target_qty=hold_qty,
                                avg_daily_amount=avg_daily_amount,
                                signal_on_complete=0,
                                db=db
                            )

                            if order_result.get("success"):
                                hold_qty -= order_result.get("qty", 0)
                                if order_result.get("completed", True):
                                    new_signal = 0
                                    entry_price = 0
                                    hold_qty = 0
                                    peak_price = 0

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=ts_result.get("reasons", ["전량 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] 전량 매도 완료, 사이클 종료")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] 전량 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

        # ------------------------------------------
        # SIGNAL 3: 1차 매도 완료 - 재진입 체크 또는 2차 매도
        # ------------------------------------------
        elif current_signal == 3:
            # 손절 신호 체크 (SIGNAL 1/2와 동일)
            exit_result = None
            if entry_price > 0:
                exit_result = await cls.check_exit_signal(
                    redis_client=redis_client,
                    position_id=swing_id,
                    symbol=st_code,
                    current_price=current_price,
                    entry_price=Decimal(entry_price),
                    frgn_ntby_qty=frgn_ntby_qty,
                    acml_vol=acml_vol,
                    cached_indicators=cached_indicators
                )

            if exit_result and exit_result.get("action") == "SELL":
                logger.warning(
                    f"[{st_code}] SIGNAL 3 즉시 매도 신호 발동! 현재가={int(current_price):,}원, "
                    f"평단가={entry_price:,}원"
                )

                if user_id:
                    # 손절은 전량 TWAP 매도
                    order_result = await SwingOrderExecutor.execute_sell_with_partial(
                        redis_client=redis_client,
                        swing_id=swing_id,
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        target_qty=hold_qty,
                        avg_daily_amount=avg_daily_amount,
                        signal_on_complete=0,
                        db=db
                    )

                    if order_result.get("success"):
                        hold_qty -= order_result.get("qty", 0)
                        if order_result.get("completed", True):
                            new_signal = 0
                            entry_price = 0
                            hold_qty = 0
                            peak_price = 0

                        # 거래 내역 저장
                        trade_service = TradeHistoryService(db)
                        await trade_service.record_trade(
                            swing_id=swing_id,
                            trade_type="S",
                            order_result=order_result,
                            reasons=exit_result.get("reasons", ["손절"])
                        )

                        logger.info(f"[{user_id} - 주식: {st_code}] SIGNAL 3 손절 전량 매도 완료, 사이클 종료")
                    else:
                        logger.error(f"[{st_code}] SIGNAL 3 손절 매도 실패: {order_result.get('reason')}")
                else:
                    logger.warning(f"[{st_code}] USER_ID 없음, 손절 주문 실행 불가")

            else:
                # 우선순위 1: 재진입 신호 체크 (추세 반전)
                entry_result = await cls.check_entry_signal(
                    redis_client=redis_client,
                    swing_id=swing_id,
                    symbol=st_code,
                    current_price=current_price,
                    frgn_ntby_qty=frgn_ntby_qty,
                    acml_vol=acml_vol,
                    prdy_vrss_vol_rate=prdy_vrss_vol_rate,
                    prdy_ctrt=prdy_ctrt,
                    cached_indicators=cached_indicators
                )

                if entry_result and entry_result.get("action") == "BUY":
                    if user_id:
                        # 재진입 1차 매수 (기존 잔량 유지 + 추가 매수)
                        reentry_target_amount = init_amount * Decimal(buy_ratio) / Decimal(100)
                        order_result = await SwingOrderExecutor.execute_buy_with_partial(
                            redis_client=redis_client,
                            swing_id=swing_id,
                            user_id=user_id,
                            st_code=st_code,
                            current_price=current_price,
                            target_amount=reentry_target_amount,
                            avg_daily_amount=avg_daily_amount,
                            signal_on_complete=1,
                            db=db
                        )

                        if order_result.get("success"):
                            # 기존 잔량 + 새로 매수한 수량으로 평단가 재계산
                            new_avg_price = order_result.get("avg_price", int(current_price))
                            new_qty = order_result.get("qty", 0)
                            entry_price = SwingOrderExecutor.calculate_avg_entry_price(
                                prev_qty=hold_qty,
                                prev_price=entry_price,
                                new_qty=new_qty,
                                new_price=new_avg_price
                            )
                            hold_qty = hold_qty + new_qty
                            if order_result.get("completed", True):
                                new_signal = 1
                            peak_price = int(current_price)

                            # 거래 내역 저장 (재진입)
                            reasons = ["재진입 매수", f"{buy_ratio}%"]
                            trade_service = TradeHistoryService(db)
                            await trade_service.record_trade(
                                swing_id=swing_id,
                                trade_type="B",
                                order_result=order_result,
                                reasons=reasons
                            )

                            # 2차 매수 시간 필터용 Redis 키 생성 (20분 TTL)
                            await redis_client.setex(
                                f"first_buy_time:{swing_id}",
                                1200,
                                datetime.now().isoformat()
                            )
                            logger.info(f"[{user_id} - 주식: {st_code}] 재진입 매수 완료: 평단가={entry_price:,}원, 총수량={hold_qty}주")
                        else:
                            logger.error(f"[{user_id} - 주식: {st_code}] 재진입 매수 실패: {order_result.get('reason')}")
                    else:
                        logger.warning(f"[{st_code}] USER_ID 없음, 재진입 주문 실행 불가")

                else:
                    # 우선순위 2: 장중 trailing stop (2차 전량 매도)
                    ts_result = await cls.check_trailing_stop_signal(
                        symbol=st_code,
                        current_price=current_price,
                        peak_price=peak_price,
                        signal=current_signal,
                        cached_indicators=cached_indicators
                    )

                    if ts_result and ts_result.get("action") == "SELL_ALL":
                        logger.info(f"[{st_code}] 2차 전량 매도 실행 (잔량: {hold_qty}주)")

                        if user_id:
                            order_result = await SwingOrderExecutor.execute_sell_with_partial(
                                redis_client=redis_client,
                                swing_id=swing_id,
                                user_id=user_id,
                                st_code=st_code,
                                current_price=current_price,
                                target_qty=hold_qty,
                                avg_daily_amount=avg_daily_amount,
                                signal_on_complete=0,
                                db=db
                            )

                            if order_result.get("success"):
                                hold_qty -= order_result.get("qty", 0)
                                if order_result.get("completed", True):
                                    new_signal = 0
                                    entry_price = 0
                                    hold_qty = 0
                                    peak_price = 0

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=ts_result.get("reasons", ["2차 전량 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] 2차 매도 완료, 사이클 종료")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] 2차 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

        return {
            "new_signal": new_signal,
            "entry_price": entry_price,
            "hold_qty": hold_qty,
            "peak_price": peak_price,
            "updated": (
                new_signal != current_signal
                or hold_qty != original_hold_qty
                or entry_price != original_entry_price
                or peak_price != original_peak_price
            )
        }
