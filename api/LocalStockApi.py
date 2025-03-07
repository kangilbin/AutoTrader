from module.DBConnection import sql_execute, DBConnectionPool
from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisConnection import redis


# 종목 코드 조회



# 주식 잔고 조회
async def get_stock_balance(cano: str, acnt_prdt_cd: str):
    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        # "Content-Type": "application/json",
        "authorization": f"Bearer {await redis().get('access_token')}",
        "appkey": get_env("API_KEY"),
        "appsecret": get_env("SECRET_KEY"),
        "tr_id": "TTTC8434R",
        "custtype": "P"  # B:법인, P:개인
    }
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
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


# 주식 현재가 / 호가 실시간
# FID 입력 종목코드
async def get_price_info(code: str):
    path = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization": f"Bearer {await redis().get('access_token')}",
        "appkey": get_env("API_KEY"),
        "appsecret": get_env("SECRET_KEY"),
        "tr_id": "FHKST01010200"
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code
    }
    return await fetch("GET", api_url, params=params, headers=headers)

