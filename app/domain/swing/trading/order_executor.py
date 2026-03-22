"""
스윙 매매 주문 실행 서비스
분할 매수/매도 로직 구현 + 체결 확인
"""
import asyncio
import json
import logging
from decimal import Decimal
from typing import Dict, Any

from app.domain.order.entity import Order
from app.external.kis_api import place_order_api, check_order_execution

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
        execution = await _check_execution_with_retry(user_id, order_no, db)
        if not execution:
            logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
            return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                    "order_no": order_no, "unconfirmed": True}
        executed_qty = execution.get("executed_qty", qty)
        avg_price = execution.get("avg_price", int(curr_price))
        executed_amount = float(executed_qty * avg_price)
        remaining_amount = float(target_amount) - executed_amount

        # 잔여 금액으로 1주도 못 사면 완료
        if remaining_amount < curr_price:
            # 거래 내역 DB 저장
            from app.domain.trade_history import TradeHistoryService
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="B",
                order_result={"qty": executed_qty, "avg_price": avg_price,
                              "order_no": order_no, "amount": executed_amount},
                reasons=[f"단일매수({signal_on_complete}차)", "100% 완료"]
            )

            logger.info(f"[{st_code}] {signal_on_complete}차 매수 완료 (단일): {executed_qty}주, {avg_price:,}원")
            return {"success": True, "completed": True, "qty": executed_qty,
                    "avg_price": avg_price, "amount": executed_amount, "phase": signal_on_complete}

        # 분할 진행 상태 (Redis 저장을 caller에 위임)
        partial_state = {
            "type": "buy",
            "phase": signal_on_complete,
            "target_amount": float(target_amount),
            "executed_amount": executed_amount,
        }
        # Redis 저장을 caller에 위임 (DB commit 후 저장하도록)

        progress_pct = executed_amount / float(target_amount) * 100
        logger.info(
            f"[{st_code}] {signal_on_complete}차 분할 매수 시작: "
            f"첫 {executed_qty}주 ({progress_pct:.1f}%), 나머지 분할 진행 예정"
        )
        return {"success": True, "completed": False, "qty": executed_qty,
                "avg_price": avg_price, "amount": executed_amount, "phase": signal_on_complete,
                "partial_state": partial_state}

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
        execution = await _check_execution_with_retry(user_id, order_no, db)
        if not execution:
            logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
            return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                    "order_no": order_no, "unconfirmed": True}
        actual_qty = execution.get("executed_qty", order_qty)

        # 단일 주문으로 완료
        if actual_qty >= target_qty:
            avg_sell_price = execution.get("avg_price", int(curr_price))

            # 거래 내역 DB 저장
            from app.domain.trade_history import TradeHistoryService
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="S",
                order_result={"qty": actual_qty, "avg_price": avg_sell_price,
                              "order_no": order_no, "amount": actual_qty * avg_sell_price},
                reasons=[f"단일매도({signal_on_complete}차)", "100% 완료"]
            )

            logger.info(f"[{st_code}] {signal_on_complete}차 매도 완료 (단일): {actual_qty}주")
            return {"success": True, "completed": True, "qty": actual_qty, "phase": signal_on_complete}

        # 분할 진행 상태 (Redis 저장을 caller에 위임)
        partial_state = {
            "type": "sell",
            "phase": signal_on_complete,
            "target_qty": target_qty,
            "executed_qty": actual_qty,
        }
        # Redis 저장을 caller에 위임

        progress_pct = actual_qty / target_qty * 100
        logger.info(
            f"[{st_code}] {signal_on_complete}차 분할 매도 시작: "
            f"첫 {actual_qty}주 ({progress_pct:.1f}%), 나머지 분할 진행 예정"
        )
        return {"success": True, "completed": False, "qty": actual_qty, "phase": signal_on_complete,
                "partial_state": partial_state}

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
                return {
                    "completed": False,
                    "aborted": True,
                    "signal_on_complete": 1 if current_hold_qty > 0 else 0,
                    "entry_price": current_entry_price,
                    "hold_qty": current_hold_qty,
                    "clear_partial": True,
                }

            target_amount = state["target_amount"]
            executed_amount = state["executed_amount"]
            remaining_amount = target_amount - executed_amount

            per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else remaining_amount
            order_amount = min(remaining_amount, per_cycle_amount)
            order_qty = int(order_amount / curr_price)

            if order_qty <= 0:
                logger.info(f"[{st_code}] {state['phase']}차 분할 매수 완료 (잔여금액 소진)")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty,
                        "clear_partial": True}

            order = Order.create(ord_dv="buy", itm_no=st_code, qty=order_qty)
            result = await place_order_api(user_id, order, db)

            if not (result and result.get("rt_cd") == "0"):
                logger.error(f"[{st_code}] 분할 매수 chunk 주문 실패")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            order_no = result.get("output", {}).get("ODNO")
            execution = await _check_execution_with_retry(user_id, order_no, db)
            if not execution:
                logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
                return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                        "order_no": order_no, "unconfirmed": True}
            executed_qty = execution.get("executed_qty", order_qty)
            avg_price = execution.get("avg_price", int(curr_price))

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
                logger.info(f"[{st_code}] {state['phase']}차 분할 매수 완료: 총 {new_hold_qty}주, 평단가={new_entry_price:,}원")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": new_entry_price, "hold_qty": new_hold_qty,
                        "clear_partial": True}

            state["executed_amount"] = new_executed_amount
            logger.info(f"[{st_code}] 분할 매수 진행: {progress_pct:.1f}% (누적 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": new_entry_price, "hold_qty": new_hold_qty,
                    "partial_state": state}

        # ── 매도 부분 실행 ──
        elif exec_type == "sell":
            target_qty = state["target_qty"]
            executed_qty_so_far = state["executed_qty"]
            remaining_qty = target_qty - executed_qty_so_far

            if remaining_qty <= 0:
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price if current_hold_qty > 0 else 0,
                        "hold_qty": current_hold_qty,
                        "clear_partial": True}

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
            execution = await _check_execution_with_retry(user_id, order_no, db)
            if not execution:
                logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
                return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                        "order_no": order_no, "unconfirmed": True}
            actual_qty = execution.get("executed_qty", order_qty)
            avg_sell_price = execution.get("avg_price", int(curr_price))

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
                logger.info(f"[{st_code}] {state['phase']}차 분할 매도 완료: {new_executed_qty}주, 잔량={new_hold_qty}주")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price if new_hold_qty > 0 else 0,
                        "hold_qty": new_hold_qty,
                        "clear_partial": True}

            state["executed_qty"] = new_executed_qty
            logger.info(f"[{st_code}] 분할 매도 진행: {progress_pct:.1f}% (잔량 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": current_entry_price, "hold_qty": new_hold_qty,
                    "partial_state": state}

        return {"completed": True, "aborted": False, "signal_on_complete": None,
                "entry_price": current_entry_price, "hold_qty": current_hold_qty}


async def _check_execution_with_retry(
    user_id: str, order_no: str, db, max_retries: int = 2, delay: float = 1.0
):
    """체결 확인 재시도 (최대 2회, 1초 간격)"""
    for attempt in range(max_retries):
        execution = await check_order_execution(user_id, order_no, db)
        if execution and execution.get("executed_qty", 0) > 0:
            return execution
        if attempt < max_retries - 1:
            await asyncio.sleep(delay)
    return None