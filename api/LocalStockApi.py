from module.DBConnection import sql_execute, DBConnectionPool
from module.FetchAPI import fetch
from module.Config import get_env
from module.RedisClient import redis_client


# 종목 코드 조회
async def get_stocks(pool: DBConnectionPool, name: str):
    query = "SELECT ST_CODE FROM KIS_LOCAL_STOCKS WHERE NAME = %s"
    return await sql_execute(pool, query, (name,))


# 주식 잔고 조회
async def get_stock_balance(cano: str, acnt_prdt_cd: str):
    redis = await redis_client()

    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        # "Content-Type": "application/json",
        "authorization": f"Bearer {await redis.get('access_token')}",
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
#
#
async def get_price_info(code: str):
    redis = await redis_client()
    path = "/uapi/domestic-stock/v1/ranking/disparity"
    api_url = f"{get_env('API_URL')}/{path}"

    headers = {
        "authorization": f"Bearer {await redis.get('access_token')}",
        "appkey": get_env("API_KEY"),
        "appsecret": get_env("SECRET_KEY"),
        "tr_id": "FHPST01780000",
        "custtype": "P",
    }

    params = {
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20178",
        "fid_div_cls_code": div_cd,
        "fid_rank_sort_cls_code": sort,
        "fid_input_iscd": "0000",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_vol_cnt": "",
    }
    return await fetch("GET", api_url, params=params, headers=headers)

