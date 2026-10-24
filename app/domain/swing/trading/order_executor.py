"""
스윙 매매 주문 실행 서비스
단일 주문 매수/매도 + 체결 확인
"""
import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any

from app.core.market_code import is_overseas
from app.core.order import Order, ModifyOrder
from app.core.price import normalize_order_price, round_price, to_price
from app.exceptions import OrderOutcomeUnknownError
from app.external import kis_api, foreign_api

logger = logging.getLogger(__name__)

# 체결 조회 간격 (초). 해외는 체결내역 반영이 느려 더 길게 둔다.
_FILL_POLL_DELAY = 1.0
_FILL_POLL_DELAY_OVERSEAS = 2.0


class SwingOrderExecutor:
    """
    스윙 매매 주문 실행기

    - 신호 1회당 단일 주문 (분할 없음)
    - 체결 확인 + 부분 체결 시 잔량 취소 (confirm_and_settle)
    - 체결 미확인 주문은 다음 사이클 재확인 (resolve_pending_order)
    """

    MAX_PENDING_ATTEMPTS: int = 6  # 체결 미확인 주문 재확인 한도 (초과 시 미체결로 간주)
    FILL_WAIT_RETRIES: int = 3     # 부분 체결 시 잔량 취소 전 추가 확인 횟수 (조회 간격마다 1회)

    @classmethod
    async def resolve_order_price(
        cls, user_id: str, st_code: str, mrkt_code: str, is_buy: bool, db,
        account_no: str = None,
    ) -> float | None:
        """해외 지정가 주문 단가 결정 (매수=매도1호가, 매도=매수1호가)

        국내는 시장가(ORD_UNPR 0)로 나가므로 호출하지 않는다.
        호가 조회 실패 시 None → 호출부는 이번 사이클 주문을 건너뛰고 다음 사이클에
        신호를 다시 평가한다. (부정확한 단가로 미체결 주문을 남기지 않기 위함)
        """
        quote = await foreign_api.get_best_quote(user_id, st_code, db, excd=mrkt_code, account_no=account_no)
        if not quote:
            return None

        price = quote["ask"] if is_buy else quote["bid"]
        if price <= 0:
            logger.warning(f"[{st_code}] {'매도' if is_buy else '매수'}호가 없음 (quote={quote})")
            return None

        return normalize_order_price(price, is_buy=is_buy)

    @classmethod
    async def place_order(
        cls, user_id: str, st_code: str, qty: int, is_buy: bool, mrkt_code: str, db,
        account_no: str = None,
    ) -> tuple:
        """국내/해외 주문 전송 (해외=호가 기반 지정가, 국내=시장가)

        Returns:
            (KIS 응답, 주문단가) — 해외 호가 조회 실패 시 (None, None)
        """
        _overseas = is_overseas(mrkt_code)
        ord_price = 0.0

        if _overseas:
            ord_price = await cls.resolve_order_price(user_id, st_code, mrkt_code, is_buy, db, account_no)
            if ord_price is None:
                return None, None

        order = Order.create(
            ord_dv="buy" if is_buy else "sell", itm_no=st_code, qty=qty,
            unpr=ord_price, excg_cd=mrkt_code if _overseas else "",
        )

        placed_at = datetime.now().strftime("%Y%m%d%H%M%S")  # KST (서버 TZ) — 체결내역 ord_dt/ord_tmd 와 같은 기준
        try:
            if _overseas:
                return await foreign_api.place_order_api(user_id, order, db, account_no), ord_price
            return await kis_api.place_order_api(user_id, order, db, account_no), ord_price
        except OrderOutcomeUnknownError as e:
            # 접수됐는지 모른다 → '접수됨(주문번호 미상)'으로 넘긴다. rt_cd="0"은 거짓 성공이 아니라
            # 호출부를 체결 미확인 경로(pending_order, order_no=None)로 보내기 위한 것이다.
            # 실패로 확정하면 실제로 체결된 주문이 기록 없이 사라진다.
            # 확인은 다음 사이클 resolve_pending_order 가 체결내역(종목·구분·수량·시각)으로 한다.
            logger.warning(
                f"[{st_code}] {'매수' if is_buy else '매도'} {qty}주 주문 결과 미상({e.message}) "
                f"→ 체결 미확인 주문으로 추적"
            )
            return {"rt_cd": "0", "outcome_unknown": True, "output": {}, "placed_at": placed_at}, ord_price

    @classmethod
    async def cancel_order(
        cls, user_id: str, order_no: str, st_code: str, mrkt_code: str, db,
        ord_orgno: str = "", account_no: str = None,
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
                result = await foreign_api.modify_or_cancel_order_api(user_id, cancel, db, account_no)
            else:
                result = await kis_api.modify_or_cancel_order_api(user_id, cancel, db, account_no)

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
        ord_orgno: str = "", max_retries: int = 2, account_no: str = None,
    ):
        """체결 확인 + 부분 체결 시 잔량 취소

        해외는 지정가라 부분 체결이 남을 수 있는데, 잔량 주문을 살려두면 다음 사이클이
        낸 주문과 함께 체결되어 목표 수량/금액을 초과한다. 확인 직후 잔량을 정리한다.

        취소 전에 FILL_WAIT_RETRIES 회 더 조회해 체결이 끝나길 기다린다 (진행 중인 체결을 자르지 않도록).
        취소 후에는 성공·거부와 무관하게 다시 조회해 확인~취소 사이 체결까지 수량에 넣는다.

        Returns:
            체결 정보 또는 None (미확인)
        """
        if not order_no:
            # 주문 결과 미상(place_order 참고) — 주문번호 매칭은 항상 빗나가 슬롯만 붙잡는다.
            # 확인은 다음 사이클 resolve_pending_order 가 체결내역 속성 매칭으로 한다.
            return None

        _overseas = is_overseas(mrkt_code)
        execution = await _check_execution_with_retry(
            user_id, order_no, db, max_retries=max_retries,
            overseas=_overseas, mrkt_code=mrkt_code,
            ord_qty=ord_qty, ord_price=ord_price, account_no=account_no,
        )
        if not execution:
            return None

        if execution.get("executed_qty", 0) >= ord_qty:
            return execution

        async def _recheck(current):
            """한 간격 쉬고 재조회 — 체결 수량이 늘었을 때만 갱신"""
            await asyncio.sleep(_FILL_POLL_DELAY_OVERSEAS if _overseas else _FILL_POLL_DELAY)
            latest = await _check_execution_with_retry(
                user_id, order_no, db, max_retries=1,
                overseas=_overseas, mrkt_code=mrkt_code,
                ord_qty=ord_qty, ord_price=ord_price, account_no=account_no,
            )
            if latest and latest.get("executed_qty", 0) > current.get("executed_qty", 0):
                return latest
            return current

        # 일부만 보인 건 체결이 진행 중이라서일 수 있다 (모의투자는 시세 흐름에 맞춰 나눠 체결된다).
        # 곧바로 취소하면 주문 대부분이 취소된다 (2026-10-23: 280주 주문이 7·2·1주로 끝남).
        for _ in range(cls.FILL_WAIT_RETRIES):
            execution = await _recheck(execution)
            if execution.get("executed_qty", 0) >= ord_qty:
                return execution

        executed_qty = execution.get("executed_qty", 0)
        cancelled = await cls.cancel_order(user_id, order_no, st_code, mrkt_code, db, ord_orgno, account_no)
        if not cancelled:
            logger.warning(f"[{st_code}] 주문 {order_no} 잔량 취소 실패 → 체결 재확인")

        # 확인과 취소 사이에 들어온 체결은 취소 성공 여부와 무관하게 재조회해야 잡힌다.
        # 성공일 때 재조회를 생략하면 그 체결이 기록에서 빠진다 (2026-10-23: 1주 누락).
        execution = await _recheck(execution)
        final_qty = execution.get("executed_qty", 0)
        if final_qty > executed_qty:
            logger.info(f"[{st_code}] 취소 후 재확인: 체결 수량 {executed_qty} → {final_qty}주")
        if cancelled:
            logger.info(
                f"[{st_code}] 부분 체결 {final_qty}/{ord_qty}주 → 잔량 {ord_qty - final_qty}주 취소"
            )
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
        평균 매수 단가 계산 (기존 보유분과 새 체결분 가중 평균)

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
    # 주문 실행 (단일 주문)
    # ========================================
    #
    # 주문은 신호 1회당 한 번만 낸다. 예전엔 일거래대금 대비 큰 주문을 5분마다 나눠 냈지만(TWAP),
    # 청산이 1시간 넘게 늘어지고(2026-10-23: 1시간 동안 31%) 백테스트(신호일 일괄 체결)와 어긋나
    # 제거했다. 유동성 부족은 진입 단계(_handle_waiting 의 MAX_ADV_RATIO)에서 막는다.

    @classmethod
    async def _place_and_confirm(
        cls, side: str, swing_id: int, user_id: str, st_code: str, current_price: Decimal,
        qty: int, phase: int, db, mrkt_code: str, account_no: str, pending_extra: dict,
    ) -> Dict[str, Any]:
        """주문 전송 + 체결 확인 (매수/매도 공통)

        Returns:
            실패: {"success": False, "reason"}
            체결 미확인: {"success": True, "unconfirmed": True, "pending_order"} — 다음 사이클 재확인
            체결: {"success": True, "qty", "avg_price", "amount", "order_no"}
        """
        is_buy = side == "buy"
        label = "매수" if is_buy else "매도"
        result, ord_price = await cls.place_order(user_id, st_code, qty, is_buy, mrkt_code, db, account_no)

        if ord_price is None:
            logger.warning(f"[{st_code}] 호가 조회 실패 → 이번 사이클 {label} 보류 (다음 사이클 재평가)")
            return {"success": False, "reason": "호가 조회 실패"}

        if not (result and result.get("rt_cd") == "0"):
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            logger.error(f"[{st_code}] {label} 주문 실패: {error_msg}")
            return {"success": False, "reason": error_msg}

        fill_price = ord_price or round_price(float(current_price))  # 국내 시장가는 주문단가가 없어 현재가 기준
        order_no = result.get("output", {}).get("ODNO")
        ord_orgno = result.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")
        execution = await cls.confirm_and_settle(
            user_id, st_code, order_no, mrkt_code, qty, fill_price, db,
            ord_orgno=ord_orgno, account_no=account_no,
        )
        if not execution:
            logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
            return {"success": True, "unconfirmed": True,
                    "pending_order": {"type": side, "order_no": order_no,
                                      "phase": phase, "ord_qty": qty,
                                      "mrkt_code": mrkt_code, "attempts": 0,
                                      "placed_at": result.get("placed_at"),
                                      "ord_price": fill_price,
                                      "ord_orgno": ord_orgno,
                                      **pending_extra}}

        executed_qty = execution.get("executed_qty", qty)
        avg_price = execution.get("avg_price", fill_price)
        return {"success": True, "qty": executed_qty, "avg_price": avg_price,
                "amount": float(executed_qty * avg_price), "order_no": order_no}

    @staticmethod
    def _fill_reason(executed_qty: int, ord_qty: int) -> list:
        """일부만 체결됐을 때만 사유를 남긴다 — 기록만 보고 잔량 취소를 알 수 있게"""
        return [f"부분 체결 {executed_qty}/{ord_qty}주"] if executed_qty < ord_qty else []

    @classmethod
    async def execute_buy(
        cls,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        qty: int,
        signal_on_complete: int,
        db=None,
        mrkt_code: str = "",
        reasons: list = None,
        account_no: str = None,
        pending_extra: dict = None,
    ) -> Dict[str, Any]:
        """매수 (단일 주문)

        pending_extra: 체결 미확인 시 pending_order에 함께 실을 값 (예: 오더블럭 손절가)

        일부만 체결되면 체결분만 반영한다 — 진입은 사이클당 1회라 잔량을 다시 사지 않는다.
        체결 이력(TRADE_HISTORY) 저장은 여기서만 한다 (매도와 동일).

        Returns:
            success, qty, avg_price, amount (+ 미확인 시 unconfirmed, pending_order)
        """
        if qty <= 0:
            return {"success": False, "reason": "매수 수량 부족"}

        filled = await cls._place_and_confirm(
            "buy", swing_id, user_id, st_code, current_price, qty, signal_on_complete,
            db, mrkt_code, account_no, pending_extra=pending_extra or {},
        )
        if not filled["success"] or filled.get("unconfirmed"):
            return filled

        from app.domain.trade_history import TradeHistoryService
        await TradeHistoryService(db).record_trade(
            swing_id=swing_id,
            trade_type="B",
            order_result=filled,
            reasons=((reasons or [])
                     + [r for r in [await cls._amount_reason(swing_id, filled["amount"], db, "투입")] if r]
                     + cls._fill_reason(filled["qty"], qty)),
            mrkt_code=mrkt_code,
        )
        logger.info(f"[{st_code}] 매수 체결: {filled['qty']}/{qty}주 @ {filled['avg_price']:,}")
        return filled

    @classmethod
    async def execute_sell(
        cls,
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        target_qty: int,
        signal_on_complete: int,
        db=None,
        mrkt_code: str = "",
        reasons: list = None,
        account_no: str = None,
    ) -> Dict[str, Any]:
        """매도 (단일 주문)

        체결 이력(TRADE_HISTORY) 저장은 여기서만 한다 — 체결 수량/단가를 아는 유일한 지점이라
        호출부가 따로 기록하면 같은 체결이 두 번 저장된다. 매매 사유는 reasons로 받는다.

        Returns:
            success, completed(목표 수량 전부 체결), qty, avg_price, amount
            (+ 미확인 시 unconfirmed, pending_order)
        """
        if target_qty <= 0:
            return {"success": False, "reason": "매도 수량 부족"}

        filled = await cls._place_and_confirm(
            "sell", swing_id, user_id, st_code, current_price, target_qty, signal_on_complete,
            db, mrkt_code, account_no, pending_extra={"target_qty": target_qty},
        )
        if not filled["success"] or filled.get("unconfirmed"):
            return filled

        from app.domain.trade_history import TradeHistoryService
        await TradeHistoryService(db).record_trade(
            swing_id=swing_id,
            trade_type="S",
            order_result=filled,
            reasons=([cls._sell_label(signal_on_complete)] + (reasons or [])
                     + [r for r in [await cls._amount_reason(swing_id, filled["amount"], db, "회수")] if r]
                     + cls._fill_reason(filled["qty"], target_qty)),
            mrkt_code=mrkt_code,
        )
        logger.info(f"[{st_code}] {cls._sell_label(signal_on_complete)} 체결: "
                    f"{filled['qty']}/{target_qty}주 @ {filled['avg_price']}")
        return {**filled, "completed": filled["qty"] >= target_qty}

    # 결과 미상 주문 매칭 시 주문시각 여유 (서버 시계 ↔ KIS 주문시각 오차 흡수).
    # 같은 종목·구분·수량 주문이 이 안에 두 번 나갈 수 없다 — 사이클당 1회이고
    # 미확인 주문이 있는 동안 신규 주문은 막힌다.
    ORDER_CLOCK_SKEW_SEC: int = 60

    @classmethod
    async def _find_unknown_order(cls, user_id: str, st_code: str, mrkt_code: str,
                                  pending: Dict[str, Any], db, account_no: str = None) -> Dict[str, Any] | None:
        """주문번호 없는 미확인 주문을 체결내역에서 찾는다 (종목·구분·수량 일치 + 주문시각 이후 가장 이른 것)

        미체결로 살아있는 주문도 반환한다(executed_qty=0) — 주문번호를 알아야 취소·재확인이 가능하다.
        """
        placed_at = _ord_time({"ord_dt": (pending.get("placed_at") or "")[:8],
                               "ord_tmd": (pending.get("placed_at") or "")[8:]})
        if not placed_at:
            logger.error(f"[{st_code}] 결과 미상 주문에 주문시각이 없어 매칭 불가: {pending}")
            return None

        since = placed_at - timedelta(seconds=cls.ORDER_CLOCK_SKEW_SEC)
        orders = await _find_executions(
            user_id, st_code, mrkt_code, pending.get("type"),
            since.strftime("%Y%m%d"), datetime.now().strftime("%Y%m%d"), db, account_no,
        )
        # 주문시각을 못 읽는 주문은 제외한다 — 이전 주문과 섞일 수 있다
        candidates = [
            o for o in orders or []
            if o["order_no"] and o["ord_qty"] == pending.get("ord_qty")
            and _ord_time(o) is not None and _ord_time(o) >= since
        ]
        return min(candidates, key=_ord_time) if candidates else None

    @classmethod
    async def find_unrecorded_fills(cls, user_id: str, st_code: str, mrkt_code: str, side: str,
                                    since: datetime, db, account_no: str = None) -> list | None:
        """since(스윙 MOD_DT) 이후 체결된 주문 — DB 에 반영되지 않은 체결 후보 (시간순)

        정상 기록된 체결은 add_amount/deduct_amount/상태 전이가 MOD_DT 를 체결 뒤로 갱신하므로
        since 이후에는 롤백으로 누락된 체결(또는 증권사 앱 직접 주문)만 남는다.

        Returns:
            [{order_no, ord_dt, ord_tmd, ord_qty, executed_qty, avg_price, executed_amt}]
            조회 실패 시 None (없음과 구분 — 호출부는 상태를 건드리지 말고 다음 사이클에 재시도)
        """
        if since is None:
            # 기준 시각이 없으면 이미 기록된 체결과 구분할 수 없다 — 조회 불가로 취급
            logger.error(f"[{st_code}] 누락 체결 조회 기준 시각(MOD_DT/REG_DT) 없음 → 조회 생략")
            return None
        orders = await _find_executions(
            user_id, st_code, mrkt_code, side,
            since.strftime("%Y%m%d"), datetime.now().strftime("%Y%m%d"), db, account_no,
        )
        if orders is None:
            return None
        fills = [o for o in orders
                 if o["executed_qty"] > 0 and _ord_time(o) is not None and _ord_time(o) > since]
        return sorted(fills, key=_ord_time)

    @classmethod
    async def resolve_pending_order(
        cls,
        pending: Dict[str, Any],
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        current_entry_price: int,
        current_hold_qty: int,
        db,
        account_no: str = None,
    ) -> Dict[str, Any]:
        """체결 미확인 주문 재확인 (다음 사이클에서 호출)

        주문은 이미 나갔지만 체결 확인에 실패한 건을 주문번호로 재조회한다.
        확인 전까지는 신규 주문을 내지 않으므로 중복 주문이 발생하지 않는다.

        Returns:
            completed: 신호 완료 여부 (True면 signal_on_complete 로 전이)
            signal_on_complete, entry_price, hold_qty, chunk_amount, exec_type
            pending_state: 있으면 아직 미확인 → 다음 사이클 재확인, 없으면 확인 완료 또는 포기 → pending 키 삭제
        """
        order_no = pending.get("order_no")
        mrkt_code = pending.get("mrkt_code", "")
        curr_price = float(current_price)
        unchanged = {"completed": False, "signal_on_complete": None,
                     "entry_price": current_entry_price, "hold_qty": current_hold_qty}

        ord_qty = pending.get("ord_qty", 0)
        ord_orgno = pending.get("ord_orgno", "")

        # ── 주문 결과 미상 (응답 유실로 주문번호 없음) → 체결내역에서 찾아 기존 흐름에 합류 ──
        if not order_no:
            match = await cls._find_unknown_order(user_id, st_code, mrkt_code, pending, db, account_no)
            if not match:
                attempts = pending.get("attempts", 0) + 1
                if attempts >= cls.MAX_PENDING_ATTEMPTS:
                    # 체결내역에 없다 = 접수되지 않은 주문. 취소할 대상도 없다.
                    logger.warning(
                        f"[{st_code}] 결과 미상 주문({pending.get('type')} {ord_qty}주 @ {pending.get('placed_at')}) "
                        f"{attempts}회 조회에도 체결내역에 없음 → 미접수로 판단, 추적 종료"
                    )
                    return unchanged
                pending["attempts"] = attempts
                logger.warning(
                    f"[{st_code}] 결과 미상 주문 체결내역 미발견 ({attempts}/{cls.MAX_PENDING_ATTEMPTS}), 다음 사이클 재조회"
                )
                return {**unchanged, "pending_state": pending}

            order_no = pending["order_no"] = match["order_no"]
            logger.info(
                f"[{st_code}] 결과 미상 주문 → 체결내역에서 주문 {order_no} 발견 "
                f"({match['ord_dt']} {match['ord_tmd']}, 체결 {match['executed_qty']}/{match['ord_qty']}주)"
            )
            # 이후는 주문번호가 있는 미확인 주문과 같다 (부분체결 잔량 취소·이력 기록 포함)
        # 주문이 실제로 나간 단가 — 재확인은 몇 사이클 뒤라 그 시점 현재가를 쓰면
        # 체결가가 실제와 크게 어긋난다 (ENTRY_PRICE·손절 기준으로 이어짐)
        ord_price = float(pending.get("ord_price") or round_price(curr_price))
        execution = await cls.confirm_and_settle(
            user_id, st_code, order_no, mrkt_code, ord_qty,
            ord_price, db, ord_orgno=ord_orgno, max_retries=1, account_no=account_no,
        )

        # ── 여전히 확인 불가 ──
        if not execution or execution.get("executed_qty", 0) <= 0:
            attempts = pending.get("attempts", 0) + 1
            if attempts >= cls.MAX_PENDING_ATTEMPTS:
                # 방치하면 몇 시간 뒤 체결돼 DB에 없는 포지션이 생기므로 잔량을 취소한다
                cancelled = await cls.cancel_order(
                    user_id, order_no, st_code, mrkt_code, db, ord_orgno, account_no
                )
                logger.warning(
                    f"[{st_code}] 주문 {order_no} 체결 확인 {attempts}회 실패 → "
                    f"잔량 취소 {'성공' if cancelled else '실패'}, 추적 종료"
                    + ("" if cancelled else " (수동 확인 필요)")
                )
                if cancelled:
                    # 취소 직전 체결이 있었을 수 있다 — 재조회해서 반영하고 끝낸다
                    return await cls.settle_after_cancel(
                        pending, swing_id, user_id, st_code, current_price,
                        current_entry_price, current_hold_qty, db, account_no,
                    )
                return unchanged  # pending_state 없음 → 키 삭제
            pending["attempts"] = attempts
            logger.warning(f"[{st_code}] 주문 {order_no} 체결 미확인 ({attempts}/{cls.MAX_PENDING_ATTEMPTS}), 다음 사이클 재확인")
            return {**unchanged, "pending_state": pending}

        # ── 체결 확인됨 → 이력/상태 반영 ──
        return await cls._apply_pending_fill(
            pending, execution, swing_id, st_code, current_entry_price, current_hold_qty, db,
        )

    @classmethod
    async def settle_after_cancel(
        cls,
        pending: Dict[str, Any],
        swing_id: int,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        current_entry_price: int,
        current_hold_qty: int,
        db,
        account_no: str = None,
    ) -> Dict[str, Any]:
        """미확인 주문 취소 직후 재조회 — 확인~취소 사이에 들어온 체결을 반영한다

        취소가 성공해도 그 직전 체결은 남는다. 재조회 없이 추적을 끝내면 증권사엔 있고
        DB엔 없는 체결이 된다 (confirm_and_settle 의 취소 후 재조회와 같은 이유).

        Returns:
            resolve_pending_order 와 같은 형태. pending_state 는 없다 (취소했으므로 추적 종료).
        """
        unchanged = {"completed": False, "signal_on_complete": None,
                     "entry_price": current_entry_price, "hold_qty": current_hold_qty}
        mrkt_code = pending.get("mrkt_code", "")
        _overseas = is_overseas(mrkt_code)

        await asyncio.sleep(_FILL_POLL_DELAY_OVERSEAS if _overseas else _FILL_POLL_DELAY)
        execution = await _check_execution_with_retry(
            user_id, pending["order_no"], db, max_retries=1,
            overseas=_overseas, mrkt_code=mrkt_code,
            ord_qty=pending.get("ord_qty", 0),
            ord_price=float(pending.get("ord_price") or round_price(float(current_price))),
            account_no=account_no,
        )
        if not execution:
            return unchanged
        if execution.get("simulated"):
            # 체결 조회 미지원(해외 모의) — '전량 체결 가정'을 방금 취소한 주문에 적용하면 거짓 기록이 된다
            logger.warning(f"[{st_code}] 취소한 주문 {pending['order_no']} 체결 조회 불가 → 반영 생략 (수동 확인 필요)")
            return unchanged

        logger.warning(
            f"[{st_code}] 취소한 주문 {pending['order_no']}에서 취소 직전 체결 "
            f"{execution['executed_qty']}주 발견 → 반영"
        )
        return await cls._apply_pending_fill(
            pending, execution, swing_id, st_code, current_entry_price, current_hold_qty, db,
        )

    @classmethod
    async def _apply_pending_fill(
        cls,
        pending: Dict[str, Any],
        execution: Dict[str, Any],
        swing_id: int,
        st_code: str,
        current_entry_price: int,
        current_hold_qty: int,
        db,
    ) -> Dict[str, Any]:
        """확인된 지연 체결 → 이력 기록 + 상태 결과 (resolve_pending_order·settle_after_cancel 공용)"""
        from app.domain.trade_history import TradeHistoryService

        order_no = pending["order_no"]
        mrkt_code = pending.get("mrkt_code", "")
        ord_qty = pending.get("ord_qty", 0)
        executed_qty = execution["executed_qty"]
        avg_price = execution.get("avg_price") or float(pending.get("ord_price") or 0)
        chunk_amount = float(executed_qty * avg_price)
        phase = pending.get("phase")
        exec_type = pending.get("type")
        trade_service = TradeHistoryService(db)

        if exec_type == "buy":
            new_entry_price = cls.calculate_avg_entry_price(
                prev_qty=current_hold_qty, prev_price=current_entry_price,
                new_qty=executed_qty, new_price=avg_price
            )
            await trade_service.record_trade(
                swing_id=swing_id,
                trade_type="B",
                order_result={"qty": executed_qty, "avg_price": avg_price,
                              "order_no": order_no, "amount": chunk_amount},
                reasons=(["지연 체결 확인"]
                         + [r for r in [await cls._amount_reason(swing_id, chunk_amount, db, "투입")] if r]
                         + cls._fill_reason(executed_qty, ord_qty)),
                mrkt_code=mrkt_code,
            )
            logger.info(f"[{st_code}] 미확인 주문 {order_no} 체결 확인: 매수 {executed_qty}주 @ {avg_price}")
            # 일부 체결이어도 진입 완료 — 진입은 1회뿐이라 잔량을 다시 사지 않는다 (execute_buy 와 동일)
            return {"completed": True, "signal_on_complete": phase,
                    "entry_price": new_entry_price, "hold_qty": current_hold_qty + executed_qty,
                    "chunk_amount": chunk_amount, "exec_type": "buy"}

        # ── 매도 ──
        target_qty = int(pending.get("target_qty") or ord_qty)
        new_hold_qty = max(0, current_hold_qty - executed_qty)

        await trade_service.record_trade(
            swing_id=swing_id,
            trade_type="S",
            order_result={"qty": executed_qty, "avg_price": avg_price,
                          "order_no": order_no, "amount": chunk_amount},
            reasons=([cls._sell_label(phase), "지연 체결 확인"]
                     + [r for r in [await cls._amount_reason(swing_id, chunk_amount, db, "회수")] if r]
                     + cls._fill_reason(executed_qty, target_qty)),
            mrkt_code=mrkt_code,
        )
        logger.info(f"[{st_code}] 미확인 주문 {order_no} 체결 확인: 매도 {executed_qty}주 @ {avg_price}")

        # 부분익절(phase 2)은 체결분만큼으로 1→2 전이. 전량 매도(phase 0)는 목표를 다 채웠을 때만
        # 사이클을 닫고, 일부 체결이면 보유만 줄인다 — 잔량은 다음 사이클 청산 판정이 다시 판다.
        completed = phase != 0 or executed_qty >= target_qty
        return {"completed": completed, "signal_on_complete": phase,
                "entry_price": current_entry_price if new_hold_qty > 0 else 0,
                "hold_qty": new_hold_qty,
                "chunk_amount": chunk_amount, "exec_type": "sell"}


def _ord_time(order: dict) -> datetime | None:
    """체결내역 주문의 ord_dt(YYYYMMDD) + ord_tmd(HHMMSS) → datetime (KST). 형식이 깨졌으면 None"""
    try:
        return datetime.strptime(f"{order.get('ord_dt', '')}{str(order.get('ord_tmd', '')).zfill(6)}", "%Y%m%d%H%M%S")
    except ValueError:
        return None


async def _find_executions(user_id: str, st_code: str, mrkt_code: str, side: str,
                           start_dt: str, end_dt: str, db, account_no: str = None):
    """국내/해외 주문내역 조회 분기 (형태는 kis_api.find_executions 참고, 조회 실패 시 None)"""
    if is_overseas(mrkt_code):
        return await foreign_api.find_executions(
            user_id, db, st_code, side, start_dt, end_dt, excg_cd=mrkt_code, account_no=account_no
        )
    return await kis_api.find_executions(user_id, db, st_code, side, start_dt, end_dt, account_no=account_no)


async def _check_execution_with_retry(
    user_id: str, order_no: str, db,
    max_retries: int = 2, delay: float = _FILL_POLL_DELAY,
    overseas: bool = False, mrkt_code: str = "",
    ord_qty: int = 0, ord_price: float = 0.0,
    account_no: str = None,
):
    """체결 확인 재시도 (국내: 1초 간격, 해외: 2초 간격)

    해외는 주문체결내역(TTTS3035R/VTTS3035R)으로 확인한다. 모의 계정이 이 TR을
    지원하지 않아 조회 자체가 불가한 경우(UNSUPPORTED)에만, 확인을 포기하고
    '주문 수량 전량이 주문 단가에 체결됐다'고 가정한 결과를 반환한다.
    """
    _delay = _FILL_POLL_DELAY_OVERSEAS if overseas else delay
    for attempt in range(max_retries):
        if overseas:
            execution = await foreign_api.check_order_execution(
                user_id, order_no, db, excg_cd=mrkt_code, account_no=account_no
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
            execution = await kis_api.check_order_execution(user_id, order_no, db, account_no=account_no)
        if execution and execution.get("executed_qty", 0) > 0:
            return execution
        if attempt < max_retries - 1:
            await asyncio.sleep(_delay)
    return None