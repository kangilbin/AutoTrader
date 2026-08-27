"""
스윙 매매 주문 실행 서비스
분할 매수/매도 로직 구현 + 체결 확인
"""
import asyncio
import json
import logging
from decimal import Decimal
from typing import Dict, Any

from app.core.market_code import is_overseas
from app.core.order import Order, ModifyOrder
from app.core.price import normalize_order_price, round_price, to_price
from app.external import kis_api, foreign_api

logger = logging.getLogger(__name__)


class SwingOrderExecutor:
    """
    스윙 매매 주문 실행기

    체결 분할 (TWAP):
    - 주문금액 / 일평균거래대금 > SLIPPAGE_RATIO 초과 시 자동 분할
    - 5분 사이클마다 한 chunk씩 실행
    """

    SLIPPAGE_RATIO: float = 0.005  # 사이클당 일거래대금 0.5% 초과 시 분할
    MAX_PENDING_ATTEMPTS: int = 6  # 체결 미확인 주문 재확인 한도 (초과 시 미체결로 간주)

    @classmethod
    async def resolve_order_price(
        cls, user_id: str, st_code: str, mrkt_code: str, is_buy: bool, db
    ) -> float | None:
        """해외 지정가 주문 단가 결정 (매수=매도1호가, 매도=매수1호가)

        국내는 시장가(ORD_UNPR 0)로 나가므로 호출하지 않는다.
        호가 조회 실패 시 None → 호출부는 이번 사이클 주문을 건너뛰고 다음 사이클에
        신호를 다시 평가한다. (부정확한 단가로 미체결 주문을 남기지 않기 위함)
        """
        quote = await foreign_api.get_best_quote(user_id, st_code, db, excd=mrkt_code)
        if not quote:
            return None

        price = quote["ask"] if is_buy else quote["bid"]
        if price <= 0:
            logger.warning(f"[{st_code}] {'매도' if is_buy else '매수'}호가 없음 (quote={quote})")
            return None

        return normalize_order_price(price, is_buy=is_buy)

    @classmethod
    async def place_order(
        cls, user_id: str, st_code: str, qty: int, is_buy: bool, mrkt_code: str, db
    ) -> tuple:
        """국내/해외 주문 전송 (해외=호가 기반 지정가, 국내=시장가)

        Returns:
            (KIS 응답, 주문단가) — 해외 호가 조회 실패 시 (None, None)
        """
        _overseas = is_overseas(mrkt_code)
        ord_price = 0.0

        if _overseas:
            ord_price = await cls.resolve_order_price(user_id, st_code, mrkt_code, is_buy, db)
            if ord_price is None:
                return None, None

        order = Order.create(
            ord_dv="buy" if is_buy else "sell", itm_no=st_code, qty=qty,
            unpr=ord_price, excg_cd=mrkt_code if _overseas else "",
        )

        if _overseas:
            return await foreign_api.place_order_api(user_id, order, db), ord_price
        return await kis_api.place_order_api(user_id, order, db), ord_price

    @classmethod
    async def cancel_order(
        cls, user_id: str, order_no: str, st_code: str, mrkt_code: str, db,
        ord_orgno: str = "",
    ) -> bool:
        """미체결 잔량 전부 취소 (RVSE_CNCL_DVSN_CD "02")

        살아있는 주문을 그대로 두면 나중에 체결되어 DB에 없는 포지션이 생기므로,
        부분 체결 후 잔량이나 추적을 포기하는 주문은 반드시 정리한다.
        """
        _overseas = is_overseas(mrkt_code)

        try:
            cancel = ModifyOrder.create(
                ord_orgno=ord_orgno,
                orgn_odno=order_no,
                # 정정취소는 원주문의 주문구분을 그대로 넣어야 한다
                # (해외=지정가 "00", 국내=place_order_api가 시장가 "01"로 주문)
                ord_dvsn="00" if _overseas else "01",
                rvse_cncl_dvsn_cd="02",  # 취소
                ord_qty=0,               # 잔량 전부 취소 시 0
                ord_unpr=0,
                qty_all_ord_yn="Y",
                pdno=st_code,
                excg_cd=mrkt_code if _overseas else "",
            )

            if _overseas:
                result = await foreign_api.modify_or_cancel_order_api(user_id, cancel, db)
            else:
                result = await kis_api.modify_or_cancel_order_api(user_id, cancel, db)

            if result and result.get("rt_cd") == "0":
                return True

            logger.warning(
                f"[{st_code}] 주문 {order_no} 취소 거부: "
                f"{result.get('msg1', '응답 없음') if result else '응답 없음'}"
            )
            return False

        except Exception as e:
            logger.error(f"[{st_code}] 주문 {order_no} 취소 실패: {e}")
            return False

    @classmethod
    async def confirm_and_settle(
        cls, user_id: str, st_code: str, order_no: str, mrkt_code: str,
        ord_qty: int, ord_price: float, db,
        ord_orgno: str = "", max_retries: int = 2,
    ):
        """체결 확인 + 부분 체결 시 잔량 취소

        해외는 지정가라 부분 체결이 남을 수 있는데, 잔량 주문을 살려두면 다음 사이클이
        낸 주문과 함께 체결되어 목표 수량/금액을 초과한다. 확인 직후 잔량을 정리한다.

        취소가 거부되면 그 사이 체결됐을 가능성이 있으므로 한 번 더 확인해 수량을 보정한다.

        Returns:
            체결 정보 또는 None (미확인)
        """
        _overseas = is_overseas(mrkt_code)
        execution = await _check_execution_with_retry(
            user_id, order_no, db, max_retries=max_retries,
            overseas=_overseas, mrkt_code=mrkt_code,
            ord_qty=ord_qty, ord_price=ord_price,
        )
        if not execution:
            return None

        executed_qty = execution.get("executed_qty", 0)
        if executed_qty >= ord_qty:
            return execution

        cancelled = await cls.cancel_order(user_id, order_no, st_code, mrkt_code, db, ord_orgno)
        if cancelled:
            logger.info(
                f"[{st_code}] 부분 체결 {executed_qty}/{ord_qty}주 → 잔량 {ord_qty - executed_qty}주 취소"
            )
            return execution

        # 취소 거부 = 잔량이 이미 체결됐을 수 있음 → 재확인해서 체결 수량 갱신
        logger.warning(f"[{st_code}] 주문 {order_no} 잔량 취소 실패 → 체결 재확인")
        recheck = await _check_execution_with_retry(
            user_id, order_no, db, max_retries=1,
            overseas=_overseas, mrkt_code=mrkt_code,
            ord_qty=ord_qty, ord_price=ord_price,
        )
        if recheck and recheck.get("executed_qty", 0) > executed_qty:
            logger.info(
                f"[{st_code}] 재확인 결과 체결 수량 갱신: "
                f"{executed_qty} → {recheck['executed_qty']}주"
            )
            return recheck
        return execution

    @classmethod
    def calculate_avg_entry_price(
        cls,
        prev_qty: int,
        prev_price: float,
        new_qty: int,
        new_price: float
    ) -> float:
        """
        평균 매수 단가 계산 (TWAP 분할 매수 chunk 간 평단 갱신)

        Args:
            prev_qty: 기존 보유 수량
            prev_price: 기존 평균 단가
            new_qty: 추가 매수 수량
            new_price: 추가 매수 단가

        Returns:
            새로운 평균 단가 (ENTRY_PRICE 컬럼 정밀도인 소수점 2자리)
        """
        if prev_qty + new_qty == 0:
            return 0

        total_amount = (Decimal(str(prev_qty)) * Decimal(str(prev_price))
                        + Decimal(str(new_qty)) * Decimal(str(new_price)))
        return float(to_price(total_amount / Decimal(prev_qty + new_qty)))

    # 매도 종류 라벨. 호출부(auto_swing_batch)가 넘기는 signal_on_complete 값 기준 —
    # 부분익절은 2(전이 후 SIGNAL과 일치), 전량매도는 0(실제 전이는 reset_cycle로 SIGNAL 3).
    # 매수처럼 '차수'로 쓸 수 없는 값이라(f"{0}차 매도" = "0차 매도") 성격을 나타내는 이름으로 둔다.
    SELL_LABELS = {2: "부분 매도", 0: "전량 매도"}

    @classmethod
    def _sell_label(cls, signal_on_complete: int) -> str:
        return cls.SELL_LABELS.get(signal_on_complete, "매도")

    @classmethod
    async def _amount_reason(cls, swing_id: int, amount: float, db, label: str) -> str | None:
        """설정 금액(INIT_AMOUNT) 대비 금액 비율 → "투입 33.5%" / "회수 35.2%"

        분모를 CUR_AMOUNT가 아니라 INIT_AMOUNT로 두는 이유:
        CUR_AMOUNT는 매수 시 차감·매도 시 가산되므로(entity.deduct_amount/add_amount)
        기준이 매번 달라져 누적 투입·회수 규모를 읽을 수 없다.
        분모가 같으므로 "투입 33.5% → 회수 35.2%"를 그대로 비교할 수 있다.

        부가 정보이므로 조회 실패/INIT_AMOUNT 없음이면 None을 반환해
        체결 이력 저장 자체는 막지 않는다.
        """
        if amount <= 0:
            return None
        try:
            from app.domain.swing.repository import SwingRepository
            swing = await SwingRepository(db).find_by_id(swing_id)
            init_amount = float(swing.INIT_AMOUNT) if swing and swing.INIT_AMOUNT else 0.0
        except Exception as e:
            logger.warning(f"[SWING {swing_id}] {label} 비율 계산 실패({e}) → 사유 생략")
            return None

        if init_amount <= 0:
            return None
        return f"{label} {amount / init_amount * 100:.1f}%"

    # ========================================
    # 체결 분할 실행 (TWAP)
    # ========================================

    @classmethod
    async def execute_buy_with_partial(
        cls,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        target_amount: Decimal,
        avg_daily_amount: float,
        signal_on_complete: int,
        db=None,
        mrkt_code: str = "",
        reasons: list = None,
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

        _overseas = is_overseas(mrkt_code)
        result, ord_price = await cls.place_order(user_id, st_code, qty, True, mrkt_code, db)

        if ord_price is None:
            logger.warning(f"[{st_code}] 호가 조회 실패 → 이번 사이클 매수 보류 (다음 사이클 재평가)")
            return {"success": False, "reason": "호가 조회 실패"}

        if not (result and result.get("rt_cd") == "0"):
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            logger.error(f"[{st_code}] {signal_on_complete}차 매수 주문 실패: {error_msg}")
            return {"success": False, "reason": error_msg}

        fill_price = ord_price or round_price(curr_price)  # 국내 시장가는 주문단가가 없어 현재가 기준
        order_no = result.get("output", {}).get("ODNO")
        execution = await cls.confirm_and_settle(
            user_id, st_code, order_no, mrkt_code, qty, fill_price, db,
            ord_orgno=result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", ""),
        )
        if not execution:
            logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
            return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                    "order_no": order_no, "unconfirmed": True,
                    "pending_order": {"type": "buy", "order_no": order_no,
                                      "phase": signal_on_complete, "ord_qty": qty,
                                      "target_amount": float(target_amount),
                                      "mrkt_code": mrkt_code, "attempts": 0,
                                      "ord_price": fill_price,
                                      "ord_orgno": result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")}}
        executed_qty = execution.get("executed_qty", qty)
        avg_price = execution.get("avg_price", fill_price)
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
                reasons=((reasons or [])
                         + [r for r in [await cls._amount_reason(swing_id, executed_amount, db, "투입")] if r]),
                mrkt_code=mrkt_code,
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

        # 첫 chunk도 실제 체결이므로 기록한다 (이후 chunk는 continue_partial_execution이 기록)
        from app.domain.trade_history import TradeHistoryService
        trade_service = TradeHistoryService(db)
        await trade_service.record_trade(
            swing_id=swing_id,
            trade_type="B",
            order_result={"qty": executed_qty, "avg_price": avg_price,
                          "order_no": order_no, "amount": executed_amount},
            reasons=((reasons or [])
                     + [r for r in [await cls._amount_reason(swing_id, executed_amount, db, "투입")] if r]
                     + [f"진행 {progress_pct:.0f}%"]),
            mrkt_code=mrkt_code,
        )

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
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        target_qty: int,
        avg_daily_amount: float,
        signal_on_complete: int,
        db=None,
        mrkt_code: str = "",
        reasons: list = None,
    ) -> Dict[str, Any]:
        """
        분할 매도 시작 (첫 사이클)

        체결 이력(TRADE_HISTORY) 저장은 여기서만 한다 — 체결 수량/단가를 아는 유일한 지점이라
        호출부가 따로 기록하면 같은 체결이 두 번 저장된다. 매매 사유는 reasons로 받는다.

        Returns:
            success, completed, qty, avg_price, amount, phase
        """
        if target_qty <= 0:
            return {"success": False, "reason": "매도 수량 부족"}

        curr_price = float(current_price)
        per_cycle_amount = avg_daily_amount * cls.SLIPPAGE_RATIO if avg_daily_amount > 0 else float(target_qty * curr_price)
        per_cycle_qty = max(1, int(per_cycle_amount / curr_price))

        # 단일 주문 조건: 목표수량이 사이클 한도 이하
        order_qty = target_qty if target_qty <= per_cycle_qty else per_cycle_qty

        _overseas = is_overseas(mrkt_code)
        result, ord_price = await cls.place_order(user_id, st_code, order_qty, False, mrkt_code, db)

        if ord_price is None:
            logger.warning(f"[{st_code}] 호가 조회 실패 → 이번 사이클 매도 보류 (다음 사이클 재평가)")
            return {"success": False, "reason": "호가 조회 실패"}

        if not (result and result.get("rt_cd") == "0"):
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            logger.error(f"[{st_code}] {signal_on_complete}차 매도 주문 실패: {error_msg}")
            return {"success": False, "reason": error_msg}

        fill_price = ord_price or round_price(curr_price)
        order_no = result.get("output", {}).get("ODNO")
        execution = await cls.confirm_and_settle(
            user_id, st_code, order_no, mrkt_code, order_qty, fill_price, db,
            ord_orgno=result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", ""),
        )
        if not execution:
            logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
            return {"success": True, "completed": False, "qty": 0, "avg_price": 0,
                    "order_no": order_no, "unconfirmed": True,
                    "pending_order": {"type": "sell", "order_no": order_no,
                                      "phase": signal_on_complete, "ord_qty": order_qty,
                                      "target_qty": target_qty,
                                      "mrkt_code": mrkt_code, "attempts": 0,
                                      "ord_price": fill_price,
                                      "ord_orgno": result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")}}
        actual_qty = execution.get("executed_qty", order_qty)
        avg_sell_price = execution.get("avg_price", fill_price)

        # 단일 주문으로 완료
        if actual_qty >= target_qty:
            # 거래 내역 DB 저장
            from app.domain.trade_history import TradeHistoryService
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="S",
                order_result={"qty": actual_qty, "avg_price": avg_sell_price,
                              "order_no": order_no, "amount": actual_qty * avg_sell_price},
                reasons=([cls._sell_label(signal_on_complete)] + (reasons or [])
                         + [r for r in [await cls._amount_reason(
                             swing_id, float(actual_qty * avg_sell_price), db, "회수")] if r]),
                mrkt_code=mrkt_code,
            )

            logger.info(f"[{st_code}] {signal_on_complete}차 매도 완료 (단일): {actual_qty}주 @ {avg_sell_price}")
            # 호출부가 CUR_AMOUNT를 가산할 때 체결가를 쓰도록 매수와 같은 형태로 반환한다
            return {"success": True, "completed": True, "qty": actual_qty,
                    "avg_price": avg_sell_price, "amount": float(actual_qty * avg_sell_price),
                    "phase": signal_on_complete}

        # 분할 진행 상태 (Redis 저장을 caller에 위임)
        partial_state = {
            "type": "sell",
            "phase": signal_on_complete,
            "target_qty": target_qty,
            "executed_qty": actual_qty,
        }
        # Redis 저장을 caller에 위임

        progress_pct = actual_qty / target_qty * 100

        from app.domain.trade_history import TradeHistoryService
        trade_service = TradeHistoryService(db)
        await trade_service.record_trade(
            swing_id=swing_id,
            trade_type="S",
            order_result={"qty": actual_qty, "avg_price": avg_sell_price,
                          "order_no": order_no, "amount": float(actual_qty * avg_sell_price)},
            reasons=([cls._sell_label(signal_on_complete)] + (reasons or [])
                     + [r for r in [await cls._amount_reason(
                         swing_id, float(actual_qty * avg_sell_price), db, "회수")] if r]
                     + [f"진행 {progress_pct:.0f}%"]),
            mrkt_code=mrkt_code,
        )

        logger.info(
            f"[{st_code}] {signal_on_complete}차 분할 매도 시작: "
            f"첫 {actual_qty}주 ({progress_pct:.1f}%), 나머지 분할 진행 예정"
        )
        return {"success": True, "completed": False, "qty": actual_qty,
                "avg_price": avg_sell_price, "amount": float(actual_qty * avg_sell_price),
                "phase": signal_on_complete, "partial_state": partial_state}

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
        db,
        mrkt_code: str = "",
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

            _overseas = is_overseas(mrkt_code)
            result, ord_price = await cls.place_order(user_id, st_code, order_qty, True, mrkt_code, db)

            if ord_price is None:
                logger.warning(f"[{st_code}] 호가 조회 실패 → 이번 사이클 분할 매수 chunk 보류")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            if not (result and result.get("rt_cd") == "0"):
                logger.error(f"[{st_code}] 분할 매수 chunk 주문 실패")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            fill_price = ord_price or round_price(curr_price)
            order_no = result.get("output", {}).get("ODNO")
            execution = await cls.confirm_and_settle(
                user_id, st_code, order_no, mrkt_code, order_qty, fill_price, db,
                ord_orgno=result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", ""),
            )
            if not execution:
                logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty,
                        "pending_order": {"type": "buy", "order_no": order_no,
                                          "phase": state["phase"], "ord_qty": order_qty,
                                          "target_amount": target_amount,
                                          "mrkt_code": mrkt_code, "attempts": 0,
                                      "ord_price": fill_price,
                                      "ord_orgno": result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")}}
            executed_qty = execution.get("executed_qty", order_qty)
            avg_price = execution.get("avg_price", fill_price)

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
                reasons=([r for r in [await cls._amount_reason(swing_id, chunk_amount, db, "투입")] if r]
                         + [f"진행 {progress_pct:.0f}%"]),
                mrkt_code=mrkt_code,
            )

            # 완료 여부
            if target_amount - new_executed_amount < curr_price:
                logger.info(f"[{st_code}] {state['phase']}차 분할 매수 완료: 총 {new_hold_qty}주, 평단가={new_entry_price:,}원")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": new_entry_price, "hold_qty": new_hold_qty,
                        "chunk_amount": chunk_amount, "exec_type": "buy",
                        "clear_partial": True}

            state["executed_amount"] = new_executed_amount
            logger.info(f"[{st_code}] 분할 매수 진행: {progress_pct:.1f}% (누적 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": new_entry_price, "hold_qty": new_hold_qty,
                    "chunk_amount": chunk_amount, "exec_type": "buy",
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

            _overseas = is_overseas(mrkt_code)
            result, ord_price = await cls.place_order(user_id, st_code, order_qty, False, mrkt_code, db)

            if ord_price is None:
                logger.warning(f"[{st_code}] 호가 조회 실패 → 이번 사이클 분할 매도 chunk 보류")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            if not (result and result.get("rt_cd") == "0"):
                logger.error(f"[{st_code}] 분할 매도 chunk 주문 실패")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty}

            fill_price = ord_price or round_price(curr_price)
            order_no = result.get("output", {}).get("ODNO")
            execution = await cls.confirm_and_settle(
                user_id, st_code, order_no, mrkt_code, order_qty, fill_price, db,
                ord_orgno=result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", ""),
            )
            if not execution:
                logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
                return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price, "hold_qty": current_hold_qty,
                        "pending_order": {"type": "sell", "order_no": order_no,
                                          "phase": state["phase"], "ord_qty": order_qty,
                                          "target_qty": target_qty,
                                          "mrkt_code": mrkt_code, "attempts": 0,
                                      "ord_price": fill_price,
                                      "ord_orgno": result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")}}
            actual_qty = execution.get("executed_qty", order_qty)
            avg_sell_price = execution.get("avg_price", fill_price)

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
                reasons=([cls._sell_label(state["phase"])]
                         + [r for r in [await cls._amount_reason(
                             swing_id, float(actual_qty * avg_sell_price), db, "회수")] if r]
                         + [f"진행 {progress_pct:.0f}%"]),
                mrkt_code=mrkt_code,
            )

            chunk_amount = float(actual_qty * avg_sell_price)

            if new_executed_qty >= target_qty:
                logger.info(f"[{st_code}] {state['phase']}차 분할 매도 완료: {new_executed_qty}주, 잔량={new_hold_qty}주")
                return {"completed": True, "aborted": False, "signal_on_complete": state["phase"],
                        "entry_price": current_entry_price if new_hold_qty > 0 else 0,
                        "hold_qty": new_hold_qty,
                        "chunk_amount": chunk_amount, "exec_type": "sell",
                        "clear_partial": True}

            state["executed_qty"] = new_executed_qty
            logger.info(f"[{st_code}] 분할 매도 진행: {progress_pct:.1f}% (잔량 {new_hold_qty}주)")
            return {"completed": False, "aborted": False, "signal_on_complete": state["phase"],
                    "entry_price": current_entry_price, "hold_qty": new_hold_qty,
                    "chunk_amount": chunk_amount, "exec_type": "sell",
                    "partial_state": state}

        return {"completed": True, "aborted": False, "signal_on_complete": None,
                "entry_price": current_entry_price, "hold_qty": current_hold_qty}

    @classmethod
    async def resolve_pending_order(
        cls,
        pending: Dict[str, Any],
        partial_state: Dict[str, Any] | None,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        current_entry_price: int,
        current_hold_qty: int,
        db,
    ) -> Dict[str, Any]:
        """체결 미확인 주문 재확인 (다음 사이클에서 호출)

        주문은 이미 나갔지만 체결 확인에 실패한 건을 주문번호로 재조회한다.
        확인 전까지는 신규 주문을 내지 않으므로 중복 주문이 발생하지 않는다.

        반환 형태는 continue_partial_execution과 동일 (호출부 후처리 공용):
        - pending_state 존재: 아직 미확인 → 다음 사이클에 재확인
        - pending_state 없음: 확인 완료 또는 포기 → pending 키 삭제
        """
        from app.domain.trade_history import TradeHistoryService

        order_no = pending.get("order_no")
        mrkt_code = pending.get("mrkt_code", "")
        _overseas = is_overseas(mrkt_code)
        curr_price = float(current_price)
        unchanged = {"completed": False, "aborted": False, "signal_on_complete": None,
                     "entry_price": current_entry_price, "hold_qty": current_hold_qty}

        ord_qty = pending.get("ord_qty", 0)
        ord_orgno = pending.get("ord_orgno", "")
        # 주문이 실제로 나간 단가 — 재확인은 몇 사이클 뒤라 그 시점 현재가를 쓰면
        # 체결가가 실제와 크게 어긋난다 (ENTRY_PRICE·손절 기준으로 이어짐)
        ord_price = float(pending.get("ord_price") or round_price(curr_price))
        execution = await cls.confirm_and_settle(
            user_id, st_code, order_no, mrkt_code, ord_qty,
            ord_price, db, ord_orgno=ord_orgno, max_retries=1,
        )

        # ── 여전히 확인 불가 ──
        if not execution or execution.get("executed_qty", 0) <= 0:
            attempts = pending.get("attempts", 0) + 1
            if attempts >= cls.MAX_PENDING_ATTEMPTS:
                # 방치하면 몇 시간 뒤 체결돼 DB에 없는 포지션이 생기므로 잔량을 취소한다
                cancelled = await cls.cancel_order(
                    user_id, order_no, st_code, mrkt_code, db, ord_orgno
                )
                logger.warning(
                    f"[{st_code}] 주문 {order_no} 체결 확인 {attempts}회 실패 → "
                    f"잔량 취소 {'성공' if cancelled else '실패'}, 추적 종료"
                    + ("" if cancelled else " (수동 확인 필요)")
                )
                return unchanged  # pending_state 없음 → 키 삭제
            pending["attempts"] = attempts
            logger.warning(f"[{st_code}] 주문 {order_no} 체결 미확인 ({attempts}/{cls.MAX_PENDING_ATTEMPTS}), 다음 사이클 재확인")
            return {**unchanged, "pending_state": pending}

        # ── 체결 확인됨 → 이력/상태 반영 ──
        executed_qty = execution["executed_qty"]
        avg_price = execution.get("avg_price") or ord_price
        chunk_amount = float(executed_qty * avg_price)
        phase = pending.get("phase")
        exec_type = pending.get("type")
        trade_service = TradeHistoryService(db)

        if exec_type == "buy":
            target_amount = float(pending.get("target_amount", 0))
            executed_before = float((partial_state or {}).get("executed_amount", 0))
            new_executed_amount = executed_before + chunk_amount

            new_entry_price = cls.calculate_avg_entry_price(
                prev_qty=current_hold_qty, prev_price=current_entry_price,
                new_qty=executed_qty, new_price=avg_price
            )
            new_hold_qty = current_hold_qty + executed_qty

            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="B",
                order_result={"qty": executed_qty, "avg_price": avg_price,
                              "order_no": order_no, "amount": chunk_amount},
                reasons=(["지연 체결 확인"]
                         + [r for r in [await cls._amount_reason(swing_id, chunk_amount, db, "투입")] if r]
                         + ([] if target_amount <= 0 or target_amount - new_executed_amount < curr_price
                            else [f"진행 {new_executed_amount / target_amount * 100:.0f}%"])),
                mrkt_code=mrkt_code,
            )
            logger.info(f"[{st_code}] 미확인 주문 {order_no} 체결 확인: 매수 {executed_qty}주 @ {avg_price}")

            base = {"aborted": False, "signal_on_complete": phase,
                    "entry_price": new_entry_price, "hold_qty": new_hold_qty,
                    "chunk_amount": chunk_amount, "exec_type": "buy"}

            if target_amount - new_executed_amount < curr_price:
                return {**base, "completed": True, "clear_partial": True}
            return {**base, "completed": False,
                    "partial_state": {"type": "buy", "phase": phase,
                                      "target_amount": target_amount,
                                      "executed_amount": new_executed_amount}}

        # ── 매도 ──
        target_qty = int(pending.get("target_qty", 0))
        executed_before = int((partial_state or {}).get("executed_qty", 0))
        new_executed_qty = executed_before + executed_qty
        new_hold_qty = max(0, current_hold_qty - executed_qty)

        await trade_service.record_trade(
            swing_id=swing_id,
            trade_type="S",
            order_result={"qty": executed_qty, "avg_price": avg_price,
                          "order_no": order_no, "amount": chunk_amount},
            reasons=([cls._sell_label(phase), "지연 체결 확인"]
                     + [r for r in [await cls._amount_reason(swing_id, chunk_amount, db, "회수")] if r]
                     + ([] if target_qty <= 0 or new_executed_qty >= target_qty
                        else [f"진행 {new_executed_qty / target_qty * 100:.0f}%"])),
            mrkt_code=mrkt_code,
        )
        logger.info(f"[{st_code}] 미확인 주문 {order_no} 체결 확인: 매도 {executed_qty}주 @ {avg_price}")

        base = {"aborted": False, "signal_on_complete": phase,
                "hold_qty": new_hold_qty,
                "chunk_amount": chunk_amount, "exec_type": "sell"}

        if new_executed_qty >= target_qty:
            return {**base, "completed": True, "clear_partial": True,
                    "entry_price": current_entry_price if new_hold_qty > 0 else 0}
        return {**base, "completed": False, "entry_price": current_entry_price,
                "partial_state": {"type": "sell", "phase": phase,
                                  "target_qty": target_qty,
                                  "executed_qty": new_executed_qty}}


async def _check_execution_with_retry(
    user_id: str, order_no: str, db,
    max_retries: int = 2, delay: float = 1.0,
    overseas: bool = False, mrkt_code: str = "",
    ord_qty: int = 0, ord_price: float = 0.0,
):
    """체결 확인 재시도 (국내: 1초 간격, 해외: 2초 간격)

    해외는 주문체결내역(TTTS3035R/VTTS3035R)으로 확인한다. 모의 계정이 이 TR을
    지원하지 않아 조회 자체가 불가한 경우(UNSUPPORTED)에만, 확인을 포기하고
    '주문 수량 전량이 주문 단가에 체결됐다'고 가정한 결과를 반환한다.
    """
    _delay = 2.0 if overseas else delay
    for attempt in range(max_retries):
        if overseas:
            execution = await foreign_api.check_order_execution(
                user_id, order_no, db, excg_cd=mrkt_code
            )
            if execution is foreign_api.UNSUPPORTED:
                logger.warning(
                    f"[체결확인] 해외 체결 조회 미지원 — 전량 체결 가정 "
                    f"(주문 {order_no}, {ord_qty}주 @ {ord_price})"
                )
                return {
                    "order_no": order_no,
                    "executed_qty": ord_qty,
                    "avg_price": ord_price,
                    "simulated": True,
                }
        else:
            execution = await kis_api.check_order_execution(user_id, order_no, db)
        if execution and execution.get("executed_qty", 0) > 0:
            return execution
        if attempt < max_retries - 1:
            await asyncio.sleep(_delay)
    return None