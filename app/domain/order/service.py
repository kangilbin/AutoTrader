"""
Order Service - 비즈니스 로직
"""
import logging

from app.domain.order.entity import Order, ModifyOrder
from app.domain.order.schemas import OrderCreateRequest, OrderModifyRequest

logger = logging.getLogger(__name__)


class OrderService:
    """주문 서비스"""

    def __init__(self):
        # 순환 참조 방지를 위해 지연 임포트
        pass

    async def place_order(self, user_id: str, request: OrderCreateRequest) -> dict:
        """
        주식 매수/매도 주문

        Args:
            user_id: 사용자 ID
            request: 주문 요청

        Returns:
            주문 결과
        """
        # 순환 참조 방지를 위해 지연 임포트
        from app.external.kis_api import place_order_api

        # 도메인 엔티티 생성 및 검증
        order = Order.create(
            ord_dv=request.ORD_DV,
            itm_no=request.ITM_NO,
            qty=request.QTY
        )

        # KIS API 호출
        result = await place_order_api(user_id, order)
        return result

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

        return await get_cancelable_orders_api(user_id, fk100, nk100)

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
        result = await modify_or_cancel_order_api(user_id, modify_order)
        return result