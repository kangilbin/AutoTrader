"""
Order DTO (Data Transfer Object)
"""
from pydantic import BaseModel
from typing import Optional


class OrderModifyRequest(BaseModel):
    """주문 정정/취소 요청"""
    ORD_ORGNO: str  # 주문조직번호
    ORGN_ODNO: str  # 원주문번호
    ORD_DVSN: str  # 주문구분
    RVSE_CNCL_DVSN_CD: str  # 정정:01, 취소:02
    ORD_QTY: int  # 주문수량
    ORD_UNPR: int  # 주문단가
    QTY_ALL_ORD_YN: str  # 잔량전부주문여부


class SellAllRequest(BaseModel):
    """전량 매도 요청"""
    ST_CODE: str    # 종목코드
    MRKT_CODE: str  # 시장구분코드 (J, NX, UN, NASD)
    QTY: int        # 매도 수량

