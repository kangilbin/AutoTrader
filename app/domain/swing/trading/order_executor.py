"""
스윙 매매 주문 실행 서비스
분할 매수/매도 로직 구현 + 체결 확인
"""
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
    """

    @classmethod
    async def execute_first_buy(
        cls,
        user_id: str,
        st_code: str,
        current_price: Decimal,
        init_amount: Decimal,
        buy_ratio: int
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
            result = await place_order_api(user_id, order)

            if result and result.get("rt_cd") == "0":
                order_no = result.get("output", {}).get("ODNO")
                logger.info(f"[{st_code}] 1차 매수 주문 성공: 주문번호={order_no}")

                # 체결 확인 (폴링)
                execution = await check_order_execution(user_id, order_no)

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
        buy_ratio: int
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
            result = await place_order_api(user_id, order)

            if result and result.get("rt_cd") == "0":
                order_no = result.get("output", {}).get("ODNO")
                logger.info(f"[{st_code}] 2차 매수 주문 성공: 주문번호={order_no}")

                # 체결 확인 (폴링)
                execution = await check_order_execution(user_id, order_no)

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
        sell_ratio: int
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
        holdings = await cls._get_holding_qty(user_id, st_code)

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
            result = await place_order_api(user_id, order)

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
        st_code: str
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
        holdings = await cls._get_holding_qty(user_id, st_code)

        if holdings <= 0:
            logger.warning(f"[{st_code}] 보유 수량 없음 (이미 전량 매도)")
            return {"success": True, "reason": "이미 전량 매도", "qty": 0}

        logger.info(f"[{st_code}] 2차 매도 시도: 수량={holdings}주 (전량)")

        try:
            order = Order.create(ord_dv="sell", itm_no=st_code, qty=holdings)
            result = await place_order_api(user_id, order)

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
    async def _get_holding_qty(cls, user_id: str, st_code: str) -> int:
        """보유 수량 조회"""
        try:
            balance = await get_stock_balance(user_id)

            for item in balance:
                if item.get("pdno") == st_code:
                    return int(item.get("hldg_qty", 0))

            return 0

        except Exception as e:
            logger.error(f"[{st_code}] 보유 수량 조회 실패: {e}")
            return 0