"""
Order Service - 비즈니스 로직
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.entity import Order, ModifyOrder
from app.domain.order.schemas import OrderModifyRequest, SellAllRequest
from app.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class OrderService:
    """주문 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_cancelable_orders(
        self,
        user_id: str,
        fk100: str = "",
        nk100: str = ""
    ) -> dict:
        """
        정정/취소 가능 주문 내역 조회

        Args:
            user_id: 사용자 ID
            fk100: 페이징 키
            nk100: 페이징 키

        Returns:
            주문 내역 리스트
        """
        from app.external.kis_api import get_cancelable_orders_api

        return await get_cancelable_orders_api(user_id, self.db, fk100, nk100)

    async def modify_or_cancel_order(
        self,
        user_id: str,
        request: OrderModifyRequest
    ) -> dict:
        """
        주문 정정/취소

        Args:
            user_id: 사용자 ID
            request: 정정/취소 요청

        Returns:
            처리 결과
        """
        from app.external.kis_api import modify_or_cancel_order_api

        # 도메인 엔티티 생성 및 검증
        modify_order = ModifyOrder.create(
            ord_orgno=request.ORD_ORGNO,
            orgn_odno=request.ORGN_ODNO,
            ord_dvsn=request.ORD_DVSN,
            rvse_cncl_dvsn_cd=request.RVSE_CNCL_DVSN_CD,
            ord_qty=request.ORD_QTY,
            ord_unpr=request.ORD_UNPR,
            qty_all_ord_yn=request.QTY_ALL_ORD_YN
        )

        # KIS API 호출
        result = await modify_or_cancel_order_api(user_id, modify_order, self.db)
        return result

    async def sell_all(self, user_id: str, request: SellAllRequest) -> dict:
        """
        전량 매도 주문

        MRKT_CODE로 국내/해외 판별 후 해당 시장의 place_order_api 호출
        - 국내: 시장가 매도 (unpr=0)
        - 해외: 현재가 조회 후 지정가 매도 (현재가 * 0.995)
        """
        from app.external import kis_api, foreign_api

        is_overseas = request.MRKT_CODE == "NASD"

        if is_overseas:
            price_data = await foreign_api.get_inquire_price(user_id, request.ST_CODE, self.db)
            current_price = float(price_data.get("last", 0))
            order_price = int(current_price * 0.995 * 100) / 100
            order = Order.create(
                ord_dv="sell", itm_no=request.ST_CODE, qty=request.QTY,
                unpr=order_price, excg_cd=request.MRKT_CODE
            )
            result = await foreign_api.place_order_api(user_id, order, self.db)
        else:
            order = Order.create(
                ord_dv="sell", itm_no=request.ST_CODE, qty=request.QTY
            )
            result = await kis_api.place_order_api(user_id, order, self.db)

        if not result or result.get("rt_cd") != "0":
            error_msg = result.get("msg1", "주문 실패") if result else "응답 없음"
            raise ExternalServiceError("KIS", f"전량 매도 실패: {error_msg}")

        return result