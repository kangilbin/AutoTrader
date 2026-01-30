"""
실시간 거래 전략의 추상 베이스 클래스
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any
import pandas as pd
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
    async def update_eod_signals_to_db(cls, row, prev_row, current_eod_signals):
        pass

    @classmethod
    async def check_eod_sell_signal(
        cls,
        swing,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """
        EOD 신호 기반 매도 신호 체크 (DB의 EOD_SIGNALS 읽기)

        Args:
            swing: SWING_TRADE 레코드 (EOD_SIGNALS 컬럼 포함)
            cached_indicators: 실시간 증분 계산된 지표

        Returns:
            {
                "action": "SELL_PARTIAL" | "SELL_ALL" | "HOLD",
                "reason": str,
                "signal_count": int
            }
        """
        import json

        eod_signals_json = swing.EOD_SIGNALS if hasattr(swing, 'EOD_SIGNALS') else None

        if not eod_signals_json:
            return {"action": "HOLD", "reason": "EOD 신호 없음", "signal_count": 0}

        try:
            eod_signals = json.loads(eod_signals_json)
            signal_count = len(eod_signals)

            # EOD 신호 이름을 한글로 매핑
            signal_map = {
                "ema_breach": "EMA 종가 이탈",
                "trend_weak": "추세 약화",
                "supply_weak": "수급 이탈"
            }
            reasons = [signal_map.get(key, key) for key in eod_signals.keys()]

            if signal_count >= 3:
                return {
                    "action": "SELL_ALL",
                    "reasons": ["EOD 전량 매도"] + reasons,
                    "signal_count": signal_count
                }
            elif signal_count >= 2:
                return {
                    "action": "SELL_PARTIAL",
                    "reasons": ["EOD 1차 매도"] + reasons,
                    "signal_count": signal_count
                }
            else:
                return {"action": "HOLD", "reasons": [], "signal_count": signal_count}

        except Exception as e:
            logger.error(f"EOD 신호 파싱 실패: {e}")
            return {"action": "HOLD", "reasons": [], "signal_count": 0}

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


        new_signal = current_signal

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
                    order_result = await SwingOrderExecutor.execute_first_buy(
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        init_amount=init_amount,
                        buy_ratio=buy_ratio
                    )

                    if order_result.get("success"):
                        new_signal = 1
                        entry_price = order_result.get("avg_price", int(current_price))
                        hold_qty = order_result.get("qty", 0)

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
                        # 손절은 전량 매도
                        order_result = await SwingOrderExecutor.execute_second_sell(
                            user_id=user_id,
                            st_code=st_code
                        )

                        if order_result.get("success"):
                            new_signal = 0
                            entry_price = 0
                            hold_qty = 0

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
                    # 손절 아니면 EOD 매도 신호 체크
                    eod_result = await cls.check_eod_sell_signal(swing, cached_indicators)

                    if eod_result.get("action") == "SELL_PARTIAL":
                        logger.warning(
                            f"[{user_id} - 주식: {st_code}] EOD 1차 매도 신호! 사유={eod_result.get('reason')}"
                        )

                        if user_id:
                            # 1차 분할 매도 (sell_ratio%)
                            order_result = await SwingOrderExecutor.execute_first_sell(
                                user_id=user_id,
                                st_code=st_code,
                                sell_ratio=sell_ratio
                            )

                            if order_result.get("success"):
                                new_signal = 3
                                hold_qty = order_result.get("remaining", 0)

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=[eod_result.get("reason", "EOD 1차 매도")]
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] EOD 1차 분할 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] EOD 1차 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    elif eod_result.get("action") == "SELL_ALL":
                        logger.warning(
                            f"[{user_id} - 주식: {st_code}] EOD 2차 매도 신호! 사유={eod_result.get('reason')}"
                        )

                        if user_id:
                            # 전량 매도
                            order_result = await SwingOrderExecutor.execute_second_sell(
                                user_id=user_id,
                                st_code=st_code
                            )

                            if order_result.get("success"):
                                new_signal = 0
                                entry_price = 0
                                hold_qty = 0

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=[eod_result.get("reason", "EOD 전량 매도")]
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] EOD 전량 매도 완료, 사이클 종료")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] EOD 전량 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    else:
                        # EOD 매도 신호도 없으면 2차 매수 조건 확인
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
                                order_result = await SwingOrderExecutor.execute_second_buy(
                                    user_id=user_id,
                                    st_code=st_code,
                                    current_price=current_price,
                                    init_amount=init_amount,
                                    buy_ratio=buy_ratio
                                )

                                if order_result.get("success"):
                                    new_signal = 2
                                    new_avg_price = order_result.get("avg_price", int(current_price))
                                    new_qty = order_result.get("qty", 0)
                                    entry_price = SwingOrderExecutor.calculate_avg_entry_price(
                                        prev_qty=hold_qty,
                                        prev_price=entry_price,
                                        new_qty=new_qty,
                                        new_price=new_avg_price
                                    )
                                    hold_qty = hold_qty + new_qty

                                    # 거래 내역 저장 (비율 추가)
                                    reasons = entry_result.get("reasons", ["2차 매수"]).copy()
                                    reasons.append(f"{buy_ratio}%")
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
                        # 손절은 전량 매도
                        order_result = await SwingOrderExecutor.execute_second_sell(
                            user_id=user_id,
                            st_code=st_code
                        )

                        if order_result.get("success"):
                            new_signal = 0
                            entry_price = 0
                            hold_qty = 0

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
                    # 손절 아니면 EOD 매도 신호 체크
                    eod_result = await cls.check_eod_sell_signal(swing, cached_indicators)

                    if eod_result.get("action") == "SELL_PARTIAL":
                        if user_id:
                            # 1차 분할 매도 (sell_ratio%)
                            order_result = await SwingOrderExecutor.execute_first_sell(
                                user_id=user_id,
                                st_code=st_code,
                                sell_ratio=sell_ratio
                            )

                            if order_result.get("success"):
                                new_signal = 3
                                hold_qty = order_result.get("remaining", 0)

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=eod_result.get("reasons", ["EOD 1차 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] EOD 1차 분할 매도 완료 (sell_ratio={sell_ratio}%, 잔량={hold_qty}주)")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] EOD 1차 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

                    elif eod_result.get("action") == "SELL_ALL":
                        logger.warning(
                            f"[{st_code}] EOD 전량 매도 신호! 사유={eod_result.get('reasons')}"
                        )

                        if user_id:
                            # 전량 매도
                            order_result = await SwingOrderExecutor.execute_second_sell(
                                user_id=user_id,
                                st_code=st_code
                            )

                            if order_result.get("success"):
                                new_signal = 0
                                entry_price = 0
                                hold_qty = 0

                                # 거래 내역 저장
                                trade_service = TradeHistoryService(db)
                                await trade_service.record_trade(
                                    swing_id=swing_id,
                                    trade_type="S",
                                    order_result=order_result,
                                    reasons=eod_result.get("reasons", ["EOD 전량 매도"])
                                )

                                logger.info(f"[{user_id} - 주식: {st_code}] EOD 전량 매도 완료, 사이클 종료")
                            else:
                                logger.error(f"[{user_id} - 주식: {st_code}] EOD 전량 매도 실패: {order_result.get('reason')}")
                        else:
                            logger.warning(f"[{st_code}] USER_ID 없음, 매도 주문 실행 불가")

        # ------------------------------------------
        # SIGNAL 3: 1차 매도 완료 - 재진입 체크 또는 2차 매도
        # ------------------------------------------
        elif current_signal == 3:
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
                    order_result = await SwingOrderExecutor.execute_first_buy(
                        user_id=user_id,
                        st_code=st_code,
                        current_price=current_price,
                        init_amount=init_amount,
                        buy_ratio=buy_ratio
                    )

                    if order_result.get("success"):
                        new_signal = 1
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
                # 우선순위 2: 2차 매도 신호 체크 (EOD 3개 or 추가 하락)
                eod_result = await cls.check_eod_sell_signal(swing, cached_indicators)

                if eod_result.get("action") == "SELL_ALL" or eod_result.get("signal_count") >= 3:
                    logger.info(f"[{st_code}] 2차 전량 매도 실행 (잔량: {hold_qty}주)")

                    if user_id:
                        order_result = await SwingOrderExecutor.execute_second_sell(
                            user_id=user_id,
                            st_code=st_code
                        )

                        if order_result.get("success"):
                            new_signal = 0
                            entry_price = 0
                            hold_qty = 0

                            # 거래 내역 저장
                            trade_service = TradeHistoryService(db)
                            await trade_service.record_trade(
                                swing_id=swing_id,
                                trade_type="S",
                                order_result=order_result,
                                reasons=eod_result.get("reasons", ["2차 전량 매도"])
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
            "updated": new_signal != current_signal
        }
