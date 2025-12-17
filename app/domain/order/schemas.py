"""
Order DTO (Data Transfer Object)
"""
from pydantic import BaseModel
from typing import Optional


class OrderCreateRequest(BaseModel):
    """주문 요청"""
    ORD_DV: str  # buy(매수), sell(매도)
    ITM_NO: str  # 종목번호
    QTY: int  # 주문수량


class OrderModifyRequest(BaseModel):
    """주문 정정/취소 요청"""
    ORD_ORGNO: str  # 주문조직번호
    ORGN_ODNO: str  # 원주문번호
    ORD_DVSN: str  # 주문구분
    RVSE_CNCL_DVSN_CD: str  # 정정:01, 취소:02
    ORD_QTY: int  # 주문수량
    ORD_UNPR: int  # 주문단가
    QTY_ALL_ORD_YN: str  # 잔량전부주문여부


class OrderResponse(BaseModel):
    """주문 응답"""
    rt_cd: Optional[str] = None  # 성공실패여부
    msg_cd: Optional[str] = None  # 응답코드
    msg1: Optional[str] = None  # 응답메시지


class CancelableOrderResponse(BaseModel):
    """취소 가능 주문 응답"""
    ord_dt: Optional[str] = None  # 주문일자
    ord_gno_brno: Optional[str] = None  # 주문채번지점번호
    odno: Optional[str] = None  # 주문번호
    orgn_odno: Optional[str] = None  # 원주문번호
    pdno: Optional[str] = None  # 종목코드
    prdt_name: Optional[str] = None  # 종목명
    sll_buy_dvsn_cd: Optional[str] = None  # 매도매수구분
    ord_qty: Optional[str] = None  # 주문수량
    ord_unpr: Optional[str] = None  # 주문단가
    ord_tmd: Optional[str] = None  # 주문시각
    tot_ccld_qty: Optional[str] = None  # 총체결수량
    psbl_qty: Optional[str] = None  # 취소/정정 가능수량