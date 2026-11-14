"""
스윙 매매 주문 실행 서비스
분할 매수/매도 로직 구현 + 체결 확인
"""
import json
import logging
from decimal import Decimal
from datetime import datetime
from typing import Dict, Optional, Any

from app.domain.order.entity import Order
from app.external.kis_api import place_order_api, get_stock_balance, check_order_execution
from app.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class SwingOrderExecutor:
    """
    스윙 매매 주문 실행기

    분할 매수/매도:
    - 1차 매수: buy_ratio% of init_amount
    - 2차 매수: remaining (100 - buy_ratio)%
    - 1차 매도: sell_ratio% of holdings
    - 2차 매도: remaining holdings

    체결 분할 (TWAP):
    - 주문금액 / 일평균거래대금 > SLIPPAGE_RATIO 초과 시 자동 분할
    - 5분 사이클마다 한 chunk씩 실행
    """

    SLIPPAGE_RATIO: float = 0.005  # 사이클당 일거래대금 0.5% 초과 시 분할

    @classmethod
    async def execute_first_buy(
        cls,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        init_amount: Decimal,
        buy_ratio: int,
        db=None
    ) -> Dict[str, Any]:
        """
        1차 매수 실행

        Args:
            user_id: 사용자 ID
            st_code: 종목 코드
            current_price: 현재가
            init_amount: 초기 투자금
            buy_ratio: 매수 비율 (%)

        Returns:
            주문 결과
        """
        # 1차 매수 금액 계산
        first_buy_amount = init_amount * Decimal(buy_ratio) / Decimal(100)

        # 매수 수량 계산 (시장가 주문이므로 현재가 기준)
        if current_price <= 0:
            logger.error(f"[{st_code}] 현재가가 0 이하: {current_price}")
            return {"success": False, "reason": "현재가 오류"}

        qty = int(first_buy_amount / current_price)

        if qty <= 0:
            logger.warning(f"[{user_id} - 주식: {st_code}] 매수 수량 0: 금액={first_buy_amount}, 현재가={current_price}")
            return {"success": False, "reason": "매수 수량 부족"}

        logger.info(
            f"[{user_id} - 주식: {st_code}] 1차 매수 시도: "
            f"금액={first_buy_amount:,.0f}원 ({buy_ratio}%), "
            f"수량={qty}주, 현재가={current_price:,.0f}원"
        )

        # 주문 실행
        try:
            order = Order.create(ord_dv="buy", itm_no=st_code, qty=qty)
            result = await place_order_api(user_id, order, db)

            if result and result.get("rt_cd") == "0":
                order_no = result.get("output", {}).get("ODNO")
                logger.info(f"[{st_code}] 1차 매수 주문 성공: 주문번호={order_no}")

                # 체결 확인 (폴링)
                execution = await check_order_execution(user_id, order_no, db)

                if execution:
                    avg_price = execution.get("avg_price", 0)
                    executed_qty = execution.get("executed_qty", qty)
                    logger.info(f"[{st_code}] 1차 매수 체결: 평균가={avg_price:,}원, 수량={executed_qty}주")

                    return {
                        "success": True,
                        "order_no": order_no,
                        "qty": executed_qty,
                        "avg_price": avg_price,
                        "amount": first_buy_amount,
                        "phase": 1
                    }
                else:
                    # 체결 확인 실패 시 현재가로 대체
                    logger.warning(f"[{st_code}] 체결 확인 실패, 현재가로 대체")
                    return {
                        "success": True,
                        "order_no": order_no,
                        "qty": qty,
                        "avg_price": int(current_price),
                        "amount": first_buy_amount,
                        "phase": 1
                    }
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                logger.error(f"[{st_code}] 1차 매수 주문 실패: {error_msg}")
                return {"success": False, "reason": error_msg}

        except Exception as e:
            logger.error(f"[{st_code}] 1차 매수 주문 예외: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}

    @classmethod
    async def execute_second_buy(
        cls,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        init_amount: Decimal,
        buy_ratio: int,
        db=None
    ) -> Dict[str, Any]:
        """
        2차 매수 실행 (나머지 전부)

        Args:
            user_id: 사용자 ID
            st_code: 종목 코드
            current_price: 현재가
            init_amount: 초기 투자금
            buy_ratio: 1차 매수 비율 (2차는 100 - buy_ratio)

        Returns:
            주문 결과
        """
        # 2차 매수 금액 계산 (나머지 전부)
        second_buy_amount = init_amount * Decimal(100 - buy_ratio) / Decimal(100)

        if current_price <= 0:
            logger.error(f"[{st_code}] 현재가가 0 이하: {current_price}")
            return {"success": False, "reason": "현재가 오류"}

        qty = int(second_buy_amount / current_price)

        if qty <= 0:
            logger.warning(f"[{st_code}] 2차 매수 수량 0: 금액={second_buy_amount}, 현재가={current_price}")
            return {"success": False, "reason": "매수 수량 부족"}

        logger.info(
            f"[{st_code}] 2차 매수 시도: "
            f"금액={second_buy_amount:,.0f}원 ({100-buy_ratio}%), "
            f"수량={qty}주, 현재가={current_price:,.0f}원"
        )

        try:
            order = Order.create(ord_dv="buy", itm_no=st_code, qty=qty)
            result = await place_order_api(user_id, order, db)

            if result and result.get("rt_cd") == "0":
                order_no = result.get("output", {}).get("ODNO")
                logger.info(f"[{st_code}] 2차 매수 주문 성공: 주문번호={order_no}")

                # 체결 확인 (폴링)
                execution = await check_order_execution(user_id, order_no, db)

                if execution:
                    avg_price = execution.get("avg_price", 0)
                    executed_qty = execution.get("executed_qty", qty)
                    logger.info(f"[{st_code}] 2차 매수 체결: 평균가={avg_price:,}원, 수량={executed_qty}주")

                    return {
                        "success": True,
                        "order_no": order_no,
                        "qty": executed_qty,
                        "avg_price": avg_price,
                        "amount": second_buy_amount,
                        "phase": 2
                    }
                else:
                    # 체결 확인 실패 시 현재가로 대체
                    logger.warning(f"[{st_code}] 체결 확인 실패, 현재가로 대체")
                    return {
                        "success": True,
                        "order_no": order_no,
                        "qty": qty,
                        "avg_price": int(current_price),
                        "amount": second_buy_amount,
                        "phase": 2
                    }
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                logger.error(f"[{st_code}] 2차 매수 주문 실패: {error_msg}")
                return {"success": False, "reason": error_msg}

        except Exception as e:
            logger.error(f"[{st_code}] 2차 매수 주문 예외: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}

    @classmethod
    def calculate_avg_entry_price(
        cls,
        prev_qty: int,
        prev_price: int,
        new_qty: int,
        new_price: int
    ) -> int:
        """
        평균 매수 단가 계산 (2차 매수 시)

        Args:
            prev_qty: 기존 보유 수량
            prev_price: 기존 평균 단가
            new_qty: 추가 매수 수량
            new_price: 추가 매수 단가

        Returns:
            새로운 평균 단가
        """
        if prev_qty + new_qty == 0:
            return 0

        total_amount = (prev_qty * prev_price) + (new_qty * new_price)
        return int(total_amount / (prev_qty + new_qty))

    @classmethod
    async def execute_first_sell(
        cls,
        user_id: str,
        st_code: str,
        sell_ratio: int,
        db=None
    ) -> Dict[str, Any]:
        """
        1차 매도 실행

        Args:
            user_id: 사용자 ID
            st_code: 종목 코드
            sell_ratio: 매도 비율 (%)

        Returns:
            주문 결과
        """
        # 보유 수량 조회
        holdings = await cls._get_holding_qty(user_id, st_code, db)

        if holdings <= 0:
            logger.warning(f"[{st_code}] 보유 수량 없음")
            return {"success": False, "reason": "보유 수량 없음"}

        # 1차 매도 수량 계산
        sell_qty = int(holdings * sell_ratio / 100)

        if sell_qty <= 0:
            logger.warning(f"[{st_code}] 1차 매도 수량 0: 보유={holdings}, 비율={sell_ratio}%")
            return {"success": False, "reason": "매도 수량 부족"}

        logger.info(
            f"[{st_code}] 1차 매도 시도: "
            f"수량={sell_qty}주 ({sell_ratio}% of {holdings}주)"
        )

        try:
            order = Order.create(ord_dv="sell", itm_no=st_code, qty=sell_qty)
            result = await place_order_api(user_id, order, db)

            if result and result.get("rt_cd") == "0":
                logger.info(f"[{st_code}] 1차 매도 주문 성공: {result}")
                return {
                    "success": True,
                    "order_no": result.get("output", {}).get("ODNO"),
                    "qty": sell_qty,
                    "remaining": holdings - sell_qty,
                    "phase": 1
                }
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                logger.error(f"[{st_code}] 1차 매도 주문 실패: {error_msg}")
                return {"success": False, "reason": error_msg}

        except Exception as e:
            logger.error(f"[{st_code}] 1차 매도 주문 예외: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}

    @classmethod
    async def execute_second_sell(
        cls,
        user_id: str,
        st_code: str,
        db=None
    ) -> Dict[str, Any]:
        """
        2차 매도 실행 (나머지 전부)

        Args:
            user_id: 사용자 ID
            st_code: 종목 코드

        Returns:
            주문 결과
        """
        # 보유 수량 조회
        holdings = await cls._get_holding_qty(user_id, st_code, db)

        if holdings <= 0:
            logger.warning(f"[{st_code}] 보유 수량 없음 (이미 전량 매도)")
            return {"success": True, "reason": "이미 전량 매도", "qty": 0}

        logger.info(f"[{st_code}] 2차 매도 시도: 수량={holdings}주 (전량)")

        try:
            order = Order.create(ord_dv="sell", itm_no=st_code, qty=holdings)
            result = await place_order_api(user_id, order, db)

            if result and result.get("rt_cd") == "0":
                logger.info(f"[{st_code}] 2차 매도 주문 성공: {result}")
                return {
                    "success": True,
                    "order_no": result.get("output", {}).get("ODNO"),
                    "qty": holdings,
                    "remaining": 0,
                    "phase": 2
                }
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                logger.error(f"[{st_code}] 2차 매도 주문 실패: {error_msg}")
                return {"success": False, "reason": error_msg}

        except Exception as e:
            logger.error(f"[{st_code}] 2차 매도 주문 예외: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}

    @classmethod
    async def _get_holding_qty(cls, user_id: str, st_code: str, db=None) -> int:
        """보유 수량 조회"""
        try:
            balance = await get_stock_balance(user_id, db)

            for item in balance:
                if item.get("pdno") == st_code:
                    return int(item.get("hldg_qty", 0))

            return 0

        except Exception as e:
            logger.error(f"[{st_code}] 보유 수량 조회 실패: {e}")
            return 0

    # ========================================
    # 체결 분할 실행 (TWAP)
    # ========================================

    @classmethod
    async def execute_buy_with_partial(
        cls,
        redis_client,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        target_amount: Decimal,
        avg_daily_amount: float,
        signal_on_complete: int,
        db=None
    ) -> Dict[str, Any]:
        """
        분할 매수 시작 (첫 사이클)

        - target_amount <= 사이클당 한도: 단일 주문
        - target_amount > 사이클당 한도: 첫 chunk 주문 + Redis 상태 저장

        Returns:
            success, completed, qty, avg_price, amount, phase
        """
        curr_price = float(current_price)
        per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else float(target_amount)

        # 단일 주문 조건: 목표금액이 사이클 한도 이하
        order_amount = float(target_amount) if float(target_amount) <= per_cycle_amount else per_cycle_amount
        qty = int(order_amount / curr_price)

        if qty <= 0:
            return {"success": False, "reason": "매수 수량 부족"}

        order = Order.create(ord_dv="buy", itm_no=st_code, qty=qty)
        result = await place_order_api(user_id, order, db)

        if not (result and result.get("rt_cd") == "0"):
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            logger.error(f"[{st_code}] {signal_on_complete}차 매수 주문 실패: {error_msg}")
            return {"success": False, "reason": error_msg}

        order_no = result.get("output", {}).get("ODNO")
        execution = await check_order_execution(user_id, order_no, db)
        executed_qty = execution.get("executed_qty", qty) if execution else qty
        avg_price = execution.get("avg_price", int(curr_price)) if execution else int(curr_price)
        executed_amount = float(executed_qty * avg_price)
        remaining_amount = float(target_amount) - executed_amount

        # 잔여 금액으로 1주도 못 사면 완료
        if remaining_amount < curr_price:
            logger.info(f"[{st_code}] {signal_on_complete}차 매수 완료 (단일): {executed_qty}주, {avg_price:,}원")
            return {"success": True, "completed": True, "qty": executed_qty,
                    "avg_price": avg_price, "amount": executed_amount, "phase": signal_on_complete}

        # Redis 상태 저장 (분할 진행)
        partial_state = {
            "type": "buy",
            "phase": signal_on_complete,
            "target_amount": float(target_amount),
            "executed_amount": executed_amount,
        }
        await redis_client.setex(f"partial_exec:{swing_id}", 86400, json.dumps(partial_state))

        progress_pct = executed_amount / float(target_amount) * 100
        logger.info(
            f"[{st_code}] {signal_on_complete}차 분할 매수 시작: "
            f"첫 {executed_qty}주 ({progress_pct:.1f}%), 나머지 분할 진행 예정"
        )
        return {"success": True, "completed": False, "qty": executed_qty,
                "avg_price": avg_price, "amount": executed_amount, "phase": signal_on_complete}

    @classmethod
    async def execute_sell_with_partial(
        cls,
        redis_client,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        target_qty: int,
        avg_daily_amount: float,
        signal_on_complete: int,
        db=None
    ) -> Dict[str, Any]:
        """
        분할 매도 시작 (첫 사이클)

        Returns:
            success, completed, qty, phase
        """
        if target_qty <= 0:
            return {"success": False, "reason": "매도 수량 부족"}

        curr_price = float(current_price)
        per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else float(target_qty * curr_price)
        per_cycle_qty = max(1, int(per_cycle_amount / curr_price))

        # 단일 주문 조건: 목표수량이 사이클 한도 이하
        order_qty = target_qty if target_qty <= per_cycle_qty else per_cycle_qty

        order = Order.create(ord_dv="sell", itm_no=st_code, qty=order_qty)
        result = await place_order_api(user_id, order, db)

        if not (result and result.get("rt_cd") == "0"):
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            logger.error(f"[{st_code}] {signal_on_complete}차 매도 주문 실패: {error_msg}")
            return {"success": False, "reason": error_msg}

        order_no = result.get("output", {}).get("ODNO")
        execution = await check_order_execution(user_id, order_no, db)
        actual_qty = execution.get("executed_qty", order_qty) if execution else order_qty

        # 단일 주문으로 완료
        if actual_qty >= target_qty:
            logger.info(f"[{st_code}] {signal_on_complete}차 매도 완료 (단일): {actual_qty}주")
            return {"success": True, "completed": True, "qty": actual_qty, "phase": signal_on_complete}

        # Redis 상태 저장 (분할 진행)
        partial_state = {
            "type": "sell",
            "phase": signal_on_complete,
            "target_qty": target_qty,
            "executed_qty": actual_qty,
        }
        await redis_client.setex(f"partial_exec:{swing_id}", 86400, json.dumps(partial_state))

        progress_pct = actual_qty / target_qty * 100
        logger.info(
            f"[{st_code}] {signal_on_complete}차 분할 매도 시작: "
            f"첫 {actual_qty}주 ({progress_pct:.1f}%), 나머지 분할 진행 예정"
        )
        return {"success": True, "completed": False, "qty": actual_qty, "phase": signal_on_complete}

    @classmethod
    async def continue_partial_execution(
        cls,
        redis_client,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        avg_daily_amount: float,
        cached_indicators: Dict,
        current_entry_price: int,
        current_hold_qty: int,
        db
    ) -> Dict[str, Any]:
        """
        부분 실행 사이클 처리 (5분 간격 배치에서 호출)

        Returns:
            completed: 목표 완료 여부
            aborted: 손절로 인한 중단 여부
            signal_on_complete: 완료/중단 시 새 SIGNAL 값
            entry_price: 현재 평균 단가
            hold_qty: 현재 보유 수량
        """
        from app.domain.trade_history import TradeHistoryService

        partial_key = f"partial_exec:{swing_id}"
        partial_state_str = await redis_client.get(partial_key)

        if not partial_state_str:
            return {"completed": True, "aborted": False, "signal_on_complete": None,
                    "entry_price": current_entry_price, "hold_qty": current_hold_qty}

        state = json.loads(partial_state_str)
        exec_type = state["type"]
        curr_price = float(current_price)

        # ── 매수 부분 실행 ──
        if exec_type == "buy":
            # 손절 체크: 매수 중 EMA-ATR 이탈 시 중단
            ema = cached_indicators.get("realtime_ema20", 0)
            atr = cached_indicators.get("realtime_atr", 0)
            if ema > 0 and atr > 0 and curr_price <= ema - atr:
                logger.warning(f"[{st_code}] 분할 매수 중 손절 신호 → 매수 중단 (보유 {current_hold_qty}주)")
                await redis_client.delete(partial_key)
                return {
                    "completed": False,
                    "aborted": True,
                    "signal_on_complete": 1 if current_hold_qty > 0 else 0,
                    "entry_price": current_entry_price,
                    "hold_qty": current_hold_qty,
                }

            target_amount = state["target_amount"]
            executed_amount = state["executed_amount"]
            remaining_amount = target_amount - executed_amount

            per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else remaining_amount
            order_amount = min(remaining_amount, per_cycle_amount)
            order_qty = int(order_amount / curr_price)

            if order_qty <= 0:
                await redis_client.delete(partial_key)
                logger.info(f"[{st_code}] {state['phase']}차 분할 매수 완료 (잔여금액 소진)")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            order = Order.create(ord_dv="buy", itm_no=st_code, qty=order_qty)
            result = await place_order_api(user_id, order, db)

            if not (result and result.get("rt_cd") == "0"):
                logger.error(f"[{st_code}] 분할 매수 chunk 주문 실패")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            order_no = result.get("output", {}).get("ODNO")
            execution = await check_order_execution(user_id, order_no, db)
            executed_qty = execution.get("executed_qty", order_qty) if execution else order_qty
            avg_price = execution.get("avg_price", int(curr_price)) if execution else int(curr_price)

            chunk_amount = float(executed_qty * avg_price)
            new_executed_amount = executed_amount + chunk_amount

            # 평균 단가 재계산
            new_entry_price = cls.calculate_avg_entry_price(
                prev_qty=current_hold_qty, prev_price=current_entry_price,
                new_qty=executed_qty, new_price=avg_price
            )
            new_hold_qty = current_hold_qty + executed_qty

            # 거래 내역 저장
            progress_pct = new_executed_amount / target_amount * 100
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="B",
                order_result={"qty": executed_qty, "avg_price": avg_price,
                              "order_no": order_no, "amount": chunk_amount},
                reasons=[f"분할매수({state['phase']}차)", f"{progress_pct:.0f}% 완료"]
            )

            # 완료 여부
            if target_amount - new_executed_amount < curr_price:
                await redis_client.delete(partial_key)
                logger.info(f"[{st_code}] {state['phase']}차 분할 매수 완료: 총 {new_hold_qty}주, 평단가={new_entry_price:,}원")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": new_entry_price, "hold_qty": new_hold_qty}

            state["executed_amount"] = new_executed_amount
            await redis_client.setex(partial_key, 86400, json.dumps(state))
            logger.info(f"[{st_code}] 분할 매수 진행: {progress_pct:.1f}% (누적 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": new_entry_price, "hold_qty": new_hold_qty}

        # ── 매도 부분 실행 ──
        elif exec_type == "sell":
            target_qty = state["target_qty"]
            executed_qty_so_far = state["executed_qty"]
            remaining_qty = target_qty - executed_qty_so_far

            if remaining_qty <= 0:
                await redis_client.delete(partial_key)
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price if current_hold_qty > 0 else 0,
                        "hold_qty": current_hold_qty}

            per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else remaining_qty * curr_price
            per_cycle_qty = max(1, int(per_cycle_amount / curr_price))
            order_qty = min(remaining_qty, per_cycle_qty)

            order = Order.create(ord_dv="sell", itm_no=st_code, qty=order_qty)
            result = await place_order_api(user_id, order, db)

            if not (result and result.get("rt_cd") == "0"):
                logger.error(f"[{st_code}] 분할 매도 chunk 주문 실패")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            order_no = result.get("output", {}).get("ODNO")
            execution = await check_order_execution(user_id, order_no, db)
            actual_qty = execution.get("executed_qty", order_qty) if execution else order_qty
            avg_sell_price = execution.get("avg_price", int(curr_price)) if execution else int(curr_price)

            new_executed_qty = executed_qty_so_far + actual_qty
            new_hold_qty = current_hold_qty - actual_qty

            # 거래 내역 저장
            progress_pct = new_executed_qty / target_qty * 100
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="S",
                order_result={"qty": actual_qty, "avg_price": avg_sell_price,
                              "order_no": order_no, "amount": actual_qty * avg_sell_price},
                reasons=[f"분할매도({state['phase']}차)", f"{progress_pct:.0f}% 완료"]
            )

            if new_executed_qty >= target_qty:
                await redis_client.delete(partial_key)
                logger.info(f"[{st_code}] {state['phase']}차 분할 매도 완료: {new_executed_qty}주, 잔량={new_hold_qty}주")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price if new_hold_qty > 0 else 0,
                        "hold_qty": new_hold_qty}

            state["executed_qty"] = new_executed_qty
            await redis_client.setex(partial_key, 86400, json.dumps(state))
            logger.info(f"[{st_code}] 분할 매도 진행: {progress_pct:.1f}% (잔량 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": current_entry_price, "hold_qty": new_hold_qty}

        return {"completed": True, "aborted": False, "signal_on_complete": None,
                "entry_price": current_entry_price, "hold_qty": current_hold_qty}