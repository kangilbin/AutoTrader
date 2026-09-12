"""
KIS 주문 파라미터 DTO - 비즈니스(입력) 검증 캡슐화

주문은 KIS API를 직접 호출하므로 별도 ORM 모델이 없습니다.
domain(생성)·external(소비) 양 계층이 공용으로 사용하는 파라미터 DTO이므로
최하위 공유 계층(app/core)에 둡니다. dataclass로 주문 파라미터 검증을 수행합니다.
"""
from dataclasses import dataclass

from app.exceptions import ValidationError


@dataclass
class Order:
    """주문 엔티티"""
    ord_dv: str  # buy(매수), sell(매도)
    itm_no: str  # 종목번호
    qty: int  # 주문수량
    unpr: float = 0  # 주문단가 (국내 시장가는 0, 해외는 지정가라 필수 / 해외는 소수점 단가)
    excg_cd: str = ""  # 해외 거래소 정식코드 (NYS/NAS/AMS) — 국내 시 빈문자열, KIS 코드 변환은 foreign_api에서 수행

    def is_overseas(self) -> bool:
        """해외 주문 여부 (거래소 코드 유무로 판별)"""
        return bool(self.excg_cd)

    def validate(self) -> None:
        """주문 유효성 검증"""
        if self.ord_dv not in ('buy', 'sell'):
            raise ValidationError("주문구분은 buy 또는 sell이어야 합니다")
        if not self.itm_no:
            raise ValidationError("종목번호는 필수입니다")
        if self.qty <= 0:
            raise ValidationError("주문수량은 0보다 커야 합니다")
        # 미국 주문은 ORD_DVSN "00"(지정가)만 사용하므로 단가 0은 항상 오류
        if self.is_overseas() and self.unpr <= 0:
            raise ValidationError("해외 주문은 지정가 단가가 필요합니다")

    def is_buy_order(self) -> bool:
        """매수 주문 여부"""
        return self.ord_dv == 'buy'

    def is_sell_order(self) -> bool:
        """매도 주문 여부"""
        return self.ord_dv == 'sell'

    @classmethod
    def create(cls, ord_dv: str, itm_no: str, qty: int, unpr: float = 0, excg_cd: str = "") -> "Order":
        """주문 생성"""
        order = cls(ord_dv=ord_dv, itm_no=itm_no, qty=qty, unpr=unpr, excg_cd=excg_cd)
        order.validate()
        return order


@dataclass
class ModifyOrder:
    """주문 정정/취소 엔티티"""
    ord_orgno: str  # 주문조직번호 (국내 전용, 해외는 미사용)
    orgn_odno: str  # 원주문번호
    ord_dvsn: str  # 주문구분 (00:지정가, 01:시장가)
    rvse_cncl_dvsn_cd: str  # 정정:01, 취소:02
    ord_qty: int  # 주문수량
    ord_unpr: float  # 주문단가 (해외는 소수점 단가)
    qty_all_ord_yn: str  # 잔량전부주문여부 (Y:전부, N:일부)
    pdno: str  # 상품번호
    excg_cd: str = ""  # 해외 거래소 정식코드 (NYS/NAS/AMS) — 국내 시 빈문자열

    def is_overseas(self) -> bool:
        """해외 주문 여부 (거래소 코드 유무로 판별)"""
        return bool(self.excg_cd)

    def validate(self) -> None:
        """정정/취소 유효성 검증"""
        # 해외 정정/취소 요청엔 주문조직번호 항목이 없다 (CANO + 거래소 + 원주문번호로 식별)
        if not self.ord_orgno and not self.is_overseas():
            raise ValidationError("주문조직번호는 필수입니다")
        if not self.orgn_odno:
            raise ValidationError("원주문번호는 필수입니다")
        if not self.ord_dvsn:
            raise ValidationError("주문구분은 필수입니다")
        if self.rvse_cncl_dvsn_cd not in ('01', '02'):
            raise ValidationError("정정취소구분코드는 01(정정) 또는 02(취소)여야 합니다")
        if self.qty_all_ord_yn not in ('Y', 'N'):
            raise ValidationError("잔량전부주문여부는 Y 또는 N이어야 합니다")

        if self.qty_all_ord_yn == 'Y' and self.ord_qty > 0:
            raise ValidationError("잔량전부 취소/정정 시 주문수량은 0이어야 합니다")

        if self.qty_all_ord_yn == 'N' and self.ord_qty <= 0:
            raise ValidationError("잔량일부 취소/정정 시 주문수량이 필요합니다")

        if self.rvse_cncl_dvsn_cd == '01' and self.ord_unpr <= 0:
            raise ValidationError("정정 주문 시 주문단가가 필요합니다")

    def is_modify(self) -> bool:
        """정정 주문 여부"""
        return self.rvse_cncl_dvsn_cd == '01'

    def is_cancel(self) -> bool:
        """취소 주문 여부"""
        return self.rvse_cncl_dvsn_cd == '02'

    @classmethod
    def create(cls, ord_orgno: str, orgn_odno: str, ord_dvsn: str,
               rvse_cncl_dvsn_cd: str, ord_qty: int, ord_unpr: float,
               qty_all_ord_yn: str, pdno: str = "", excg_cd: str = "") -> "ModifyOrder":
        """정정/취소 주문 생성"""
        order = cls(
            ord_orgno=ord_orgno,
            orgn_odno=orgn_odno,
            ord_dvsn=ord_dvsn,
            rvse_cncl_dvsn_cd=rvse_cncl_dvsn_cd,
            ord_qty=ord_qty,
            ord_unpr=ord_unpr,
            qty_all_ord_yn=qty_all_ord_yn,
            pdno=pdno,
            excg_cd=excg_cd,
        )
        order.validate()
        return order


def same_order_no(a: str | None, b: str | None) -> bool:
    """주문번호 동일 여부 (선행 0 무시)

    KIS는 API별로 주문번호 패딩이 다르다.
    - 주문 전송 응답(ODNO): '0000041672' (10자리 zero-padding)
    - 주문체결내역 응답(odno): '41672' (패딩 없음)
    문자열을 그대로 비교하면 같은 주문을 못 찾아 체결을 미체결로 오판한다.
    """
    if not a or not b:
        return False
    return a.strip().lstrip("0") == b.strip().lstrip("0")
