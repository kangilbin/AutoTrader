from api.KISOpenApi import oauth_token
from model.schemas import OrderModel
from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisConnection import get_redis


# 주식 잔고 조회
async def get_stock_balance(user_id: str):
    user_info = await get_redis().hgetall(user_id)
    access_token = await get_redis().get(f"{user_id}_access_token")

    if not access_token:
        response = await oauth_token(user_id, user_info.get("API_KEY"), user_info.get("SECRET_KEY"))
        access_token = response.get("access_token")

    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": user_info.get("API_KEY"),
        "appsecret": user_info.get("SECRET_KEY"),
        "tr_id": "TTTC8434R",
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_info.get("CANO"),
        "ACNT_PRDT_CD": user_info.get("ACNT_PRDT_CD"),
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    return await fetch("GET", api_url, params=params, headers=headers)


# 주식 주문
# ord_dv : buy(매수), sell(매도)
# itm_no : 종목번호
# qty : 주문수량
# unpr : 주문단가
async def get_order_cash(user_id: str, order: OrderModel):
    user_info = await get_redis().hgetall(user_id)
    access_token = await get_redis().get(f"{user_id}_access_token")

    if not access_token:
        response = await oauth_token(user_id, user_info.get("API_KEY"), user_info.get("SECRET_KEY"))
        access_token = response.get("access_token")

    path = "/uapi/domestic-stock/v1/trading/order-cash"
    api_url = f"{get_env('API_URL')}/{path}"

    if order.ORD_DV == "buy":
        tr_id = "TTTC0802U" # 주식 현금 매수 주문    [모의투자] VTTC0802U : 주식 현금 매수 주문
    elif order.ORD_DV == "sell":
        tr_id = "TTTC0801U" # 주식 현금 매도 주문    [모의투자] VTTC0801U : 주식 현금 매도 주문
    else:
        return None

    if order.ITM_NO == "":
        return None

    if order.QTY == 0:
        return None

    if order.UNPR == 0:
        return None

    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": user_info.get("API_KEY"),
        "appsecret": user_info.get("SECRET_KEY"),
        "tr_id": tr_id,
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": user_info.get("CANO"),                  # 종합계좌번호 8자리
        "ACNT_PRDT_CD": user_info.get("ACNT_PRDT_CD"),  # 계좌상품코드 2자리
        "PDNO": order.ITM_NO,                                 # 종목코드(6자리) ETN의 경우, Q로 시작 (EX. Q500001)
        "ORD_DVSN": "01",                               # 주문구분 00:지정가, 01:시장가, 02:조건부지정가  나머지주문구분 API 문서 참조
        "ORD_QTY": str(order.QTY),                       # 주문주식수
        "ORD_UNPR": str(order.UNPR)                      # 주문단가
    }

    return await fetch("POST", api_url, params=params, headers=headers)
