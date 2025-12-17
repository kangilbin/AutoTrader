"""
Order 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass
from typing import Optional

from app.common.exceptions import BusinessException


@dataclass
class Order:
    """주문 엔티티"""
    ord_dv: str  # buy(매수), sell(매도)
    itm_no: str  # 종목번호
    qty: int  # 주문수량
    unpr: int = 0  # 주문단가 (시장가일 경우 0)

    def validate(self) -> None:
        """주문 유효성 검증"""
        if self.ord_dv not in ('buy', 'sell'):
            raise BusinessException("주문구분은 buy 또는 sell이어야 합니다")
        if not self.itm_no:
            raise BusinessException("종목번호는 필수입니다")
        if self.qty <= 0:
            raise BusinessException("주문수량은 0보다 커야 합니다")

    def is_buy_order(self) -> bool:
        """매수 주문 여부"""
        return self.ord_dv == 'buy'

    def is_sell_order(self) -> bool:
        """매도 주문 여부"""
        return self.ord_dv == 'sell'

    @classmethod
    def create(cls, ord_dv: str, itm_no: str, qty: int, unpr: int = 0) -> "Order":
        """주문 생성"""
        order = cls(ord_dv=ord_dv, itm_no=itm_no, qty=qty, unpr=unpr)
        order.validate()
        return order


@dataclass
class ModifyOrder:
    """주문 정정/취소 엔티티"""
    ord_orgno: str  # 주문조직번호
    orgn_odno: str  # 원주문번호
    ord_dvsn: str  # 주문구분 (00:지정가, 01:시장가)
    rvse_cncl_dvsn_cd: str  # 정정:01, 취소:02
    ord_qty: int  # 주문수량
    ord_unpr: int  # 주문단가
    qty_all_ord_yn: str  # 잔량전부주문여부 (Y:전부, N:일부)

    def validate(self) -> None:
        """정정/취소 유효성 검증"""
        if not self.ord_orgno:
            raise BusinessException("주문조직번호는 필수입니다")
        if not self.orgn_odno:
            raise BusinessException("원주문번호는 필수입니다")
        if not self.ord_dvsn:
            raise BusinessException("주문구분은 필수입니다")
        if self.rvse_cncl_dvsn_cd not in ('01', '02'):
            raise BusinessException("정정취소구분코드는 01(정정) 또는 02(취소)여야 합니다")
        if self.qty_all_ord_yn not in ('Y', 'N'):
            raise BusinessException("잔량전부주문여부는 Y 또는 N이어야 합니다")

        # 잔량전부인 경우 수량은 0
        if self.qty_all_ord_yn == 'Y' and self.ord_qty > 0:
            raise BusinessException("잔량전부 취소/정정 시 주문수량은 0이어야 합니다")

        # 잔량일부인 경우 수량 필수
        if self.qty_all_ord_yn == 'N' and self.ord_qty <= 0:
            raise BusinessException("잔량일부 취소/정정 시 주문수량이 필요합니다")

        # 정정인 경우 단가 필수
        if self.rvse_cncl_dvsn_cd == '01' and self.ord_unpr <= 0:
            raise BusinessException("정정 주문 시 주문단가가 필요합니다")

    def is_modify(self) -> bool:
        """정정 주문 여부"""
        return self.rvse_cncl_dvsn_cd == '01'

    def is_cancel(self) -> bool:
        """취소 주문 여부"""
        return self.rvse_cncl_dvsn_cd == '02'

    @classmethod
    def create(cls, ord_orgno: str, orgn_odno: str, ord_dvsn: str,
               rvse_cncl_dvsn_cd: str, ord_qty: int, ord_unpr: int,
               qty_all_ord_yn: str) -> "ModifyOrder":
        """정정/취소 주문 생성"""
        order = cls(
            ord_orgno=ord_orgno,
            orgn_odno=orgn_odno,
            ord_dvsn=ord_dvsn,
            rvse_cncl_dvsn_cd=rvse_cncl_dvsn_cd,
            ord_qty=ord_qty,
            ord_unpr=ord_unpr,
            qty_all_ord_yn=qty_all_ord_yn
        )
        order.validate()
        return order