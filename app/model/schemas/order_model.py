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

