from pydantic import BaseModel


# ord_dv : buy(매수), sell(매도)
# itm_no : 종목번호
# qty : 주문수량
# unpr : 주문단가
# user_id: str, ord_dv="", itm_no="", qty=0, unpr=0
class Order(BaseModel):
    ORD_DV: str
    ITM_NO: str
    QTY: int


# ord_orgno : 주문조직번호
# orgn_odno : 원주문번호
# ord_dvsn : 주문구분
# rvse_cncl_dvsn_cd : 정정 : 01, 취소 : 02
# ord_qty : 주문주식수
# ord_unpr : 주문단가
# qty_all_ord_yn : 잔량전부주문여부 [정정/취소] Y : 잔량전부, N : 잔량일부
# ord_orgno="", orgn_odno="", ord_dvsn="", rvse_cncl_dvsn_cd="", ord_qty=0, ord_unpr=0, qty_all_ord_yn=""
class ModOrder(BaseModel):
    ORD_ORGNO: str
    ORGN_ODNO: str
    ORD_DVSN: str
    RVSE_CNCL_DVSN_CD: str
    ORD_QTY: int
    ORD_UNPR: int
    QTY_ALL_ORD_YN: str
