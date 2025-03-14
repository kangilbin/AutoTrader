from api.KISOpenApi import oauth_token
from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisConnection import redis


# 종목 코드 조회


# 주식 잔고 조회
async def get_stock_balance(user_id: str):
    user_info = await redis().hgetall(user_id)
    access_token = await redis().get(f"{user_id}_access_token")

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



