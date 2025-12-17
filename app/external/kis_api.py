"""
KIS (한국투자증권) API 통합 모듈
"""
from datetime import datetime, timedelta
import logging

from app.common.exceptions import ExternalAPIException
from app.module.config import get_env
from app.module.fetch_api import fetch
from app.module.redis_connection import get_redis
from app.order.entity import Order, ModifyOrder

logger = logging.getLogger(__name__)


# ============================================================
# OAuth 및 인증 관련
# ============================================================


async def oauth_token(user_id: str, simulation_yn: str, api_key: str, secret_key: str):
    """
    한국 투자 증권 접근 토큰
    유효기간 24시 이며 (1일 1회 발급) 갱신발급 주기는 6시간(6시 이내는 기존 발급키 응답)
    """
    redis = await get_redis()
    access_data = await redis.hgetall(f"{user_id}_access_token")
    if access_data:
        if access_data.get("api_key") == api_key and access_data.get("secret_key") == secret_key:
            return access_data
        else:
            await redis.delete(f"{user_id}_access_token")

    path = "oauth2/tokenP"
    if simulation_yn == "Y":
        api_url = get_env("DEV_API_URL")
    else:
        api_url = get_env("REAL_API_URL")

    url = f"{api_url}/{path}"
    body = {
        "grant_type": "client_credentials",
        "appkey": api_key,
        "appsecret": secret_key
    }

    response = await fetch("POST", url, json=body)
    access_token = response.get("access_token")

    if (not access_token) or (response.get("error_code")):
        raise ExternalAPIException("KIS", response.get("error_description") or response.get("error_code") or "토큰 발급 실패")

    data = {
        "access_token": access_token,
        "api_key": api_key,
        "secret_key": secret_key,
        "simulation_yn": simulation_yn
    }
    # Redis에 토큰 저장 만료기간(expires_in) 설정
    await redis.hset(f"{user_id}_access_token", mapping=data)
    await redis.expire(f"{user_id}_access_token", response.get("expires_in"))
    return data


async def get_approval(user_id: str):
    """실시간 (웹소켓) 접속키 발급"""
    redis = await get_redis()
    user_auth = await redis.hgetall(f"{user_id}_access_token")

    if not user_auth:
        raise ExternalAPIException("KIS", "사용자 인증 정보가 없습니다.")

    if user_auth.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
        socket_url = get_env("DEV_SOCKET_URL")
    else:
        url = get_env("REAL_API_URL")
        socket_url = get_env("REAL_SOCKET_URL")

    path = "oauth2/Approval"
    api_url = f"{url}/{path}"

    body = {
        "grant_type": "client_credentials",
        "appkey": user_auth.get('api_key'),
        "secretkey": user_auth.get('secret_key')
    }
    response = await fetch("POST", api_url, json=body)
    approval_key = response.get("approval_key")
    if not approval_key:
        raise ExternalAPIException(
            "KIS",
            response.get("error_description") or response.get("error_code") or "approval_key 발급 실패"
        )


    data = {
        "socket_token": approval_key,
        "url": socket_url
    }
    # Redis에 토큰 저장 만료기간(expires_in) 설정
    redis = await get_redis()
    await redis.hset(f"{user_id}_socket_token", mapping=data)
    await redis.expire(f"{user_id}_socket_token", 86400)
    return data


async def _get_user_auth(user_id: str):
    """사용자 인증 정보 조회"""
    redis = await get_redis()
    access_data = await redis.hgetall(f"{user_id}_access_token")
    user_data = await redis.hgetall(user_id)

    if not access_data:
        access_data = await oauth_token(
            user_id,
            user_data.get("SIMULATION_YN"),
            user_data.get("API_KEY"),
            user_data.get("SECRET_KEY")
        )

    return user_data, access_data


# ============================================================
# 잔고 조회 관련
# ============================================================

async def get_balance(user_id: str) -> int:
    """현금 잔고 조회"""
    user_data, access_data = await _get_user_auth(user_id)
    path = "uapi/domestic-stock/v1/trading/inquire-psbl-order"
    api_url = f"{access_data.get('api_url')}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC8908R"  # 모의투자
    else:
        tr_id = "TTTC8908R"  # 실전투자

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P",
    }

    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "PDNO": "",
        "ORD_UNPR": "",
        "ORD_DVSN": "01",
        "CMA_EVLU_AMT_ICLD_YN": "Y",
        "OVRS_ICLD_YN": "Y"
    }
    response = await fetch("GET", api_url, params=params, headers=headers)
    cash = response['output']['ord_psbl_cash']
    return int(cash)


async def get_stock_balance(user_id: str, fk100="", nk100=""):
    """보유 주식 조회"""
    user_data, access_data = await _get_user_auth(user_id)

    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")

    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC8434R"  # 모의투자
    else:
        tr_id = "TTTC8434R"  # 실전투자

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"
    }
    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": fk100,
        "CTX_AREA_NK100": nk100
    }
    response = await fetch("GET", api_url, params=params, headers=headers)
    ctx_area_fk100 = response.get("ctx_area_fk100")
    ctx_area_nk100 = response.get("ctx_area_nk100")

    result = response.get("output1")

    if ctx_area_fk100 != "" and ctx_area_nk100 != "":
        result.add(await get_stock_balance(user_id, ctx_area_fk100, ctx_area_nk100))

    return result


# ============================================================
# 주문 관련
# ============================================================

async def place_order_api(user_id: str, order: Order):
    """주식 주문"""
    user_data, access_data = await _get_user_auth(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    api_url = f"{url}/{path}"

    if order.ord_dv == "buy":
        if access_data.get("simulation_yn") == "Y":
            tr_id = "VTTC0802U"  # 모의투자
        else:
            tr_id = "TTTC0012U"  # 실전투자
    elif order.ord_dv == "sell":
        if access_data.get("simulation_yn") == "Y":
            tr_id = "VTTC0801U"  # 모의투자
        else:
            tr_id = "TTTC0011U"  # 실전투자
    else:
        return None

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"
    }
    params = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "PDNO": order.itm_no,
        "ORD_DVSN": "01",  # 시장가
        "ORD_QTY": str(order.qty),
        "ORD_UNPR": "0"
    }

    return await fetch("POST", api_url, body=params, headers=headers)


async def get_cancelable_orders_api(user_id: str, fk100="", nk100=""):
    """주식 정정/취소 가능 주문 내역"""
    user_data, access_data = await _get_user_auth(user_id)

    path = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    api_url = f"{get_env('REAL_API_URL')}/{path}"

    tr_id = "TTTC0084R"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "INQR_DVSN_1": "1",
        "INQR_DVSN_2": "0",
        "CTX_AREA_FK100": fk100,
        "CTX_AREA_NK100": nk100
    }

    return await fetch("POST", api_url, json=body, headers=headers)


async def modify_or_cancel_order_api(user_id: str, order: ModifyOrder):
    """주문 정정/취소"""
    user_data, access_data = await _get_user_auth(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC0803U"  # 모의투자
    else:
        tr_id = "TTTC0013U"  # 실전투자

    # 잔량전부인 경우 수량 0 처리
    ord_qty = 0 if order.qty_all_ord_yn == 'Y' else order.ord_qty

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "KRX_FWDG_ORD_ORGNO": order.ord_orgno,
        "ORGN_ODNO": order.orgn_odno,
        "ORD_DVSN": order.ord_dvsn,
        "RVSE_CNCL_DVSN_CD": order.rvse_cncl_dvsn_cd,
        "ORD_QTY": str(ord_qty),
        "ORD_UNPR": str(order.ord_unpr),
        "QTY_ALL_ORD_YN": order.qty_all_ord_yn
    }

    return await fetch("POST", api_url, json=body, headers=headers)


# ============================================================
# 시세 조회 관련
# ============================================================

async def get_inquire_daily_ccld_obj(user_id: str, inqr_strt_dt=None, inqr_end_dt=None, fk100="", nk100=""):
    """주식일별주문체결(현황)조회"""
    user_data, access_data = await _get_user_auth(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")

    path = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'
    api_url = f"{url}/{path}"

    if inqr_strt_dt is None:
        inqr_strt_dt = datetime.today().strftime("%Y%m%d")

    if inqr_end_dt is None:
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    current_date = datetime.today()
    three_months_ago = current_date - timedelta(days=90)
    if datetime.strptime(inqr_strt_dt, "%Y%m%d") > three_months_ago:
        tr_id = "CTSC9215R"
    else:
        tr_id = "TTTC0081R"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": tr_id,
        "custtype": "P"
    }
    body = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "INQR_STRT_DT": inqr_strt_dt,
        "INQR_END_DT": inqr_end_dt,
        "SLL_BUY_DVSN_CD": "00",
        "INQR_DVSN": "01",
        "PDNO": "",
        "CCLD_DVSN": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "0",
        "CTX_AREA_FK100": fk100,
        "CTX_AREA_NK100": nk100
    }

    return await fetch("POST", api_url, json=body, headers=headers)


async def get_target_price(code: str):
    """종목 일별 시세 조회"""
    redis = await get_redis()
    access_data = await redis.hgetall("mgnt_access_token")

    if not access_data:
        access_data = await oauth_token("mgnt", "Y", get_env("API_KEY"), get_env("SECRET_KEY"))

    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")

    path = 'uapi/domestic-stock/v1/quotations/inquire-daily-price'
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST01010400",
    }

    body = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_ORG_ADJ_PRC": "1",
        "FID_PERIOD_DIV_CODE": "D"
    }
    response = await fetch("POST", api_url, json=body, headers=headers)
    return response['output'][0]


async def get_stock_data(user_id: str, code: str, start_date: str, end_date: str):
    """기간별 주식 데이터 조회"""
    user_data, access_data = await _get_user_auth(user_id)
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST03010100",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }

    response = await fetch("GET", api_url, params=params, headers=headers)

    # API 응답 데이터의 키를 대문자로 변경하고 st_code 추가
    if response and "output2" in response:
        for item in response["output2"]:
            converted_item = {}

            column_mapping = {
                'stck_oprc': 'STCK_OPRC',
                'stck_hgpr': 'STCK_HGPR',
                'stck_lwpr': 'STCK_LWPR',
                'stck_clpr': 'STCK_CLPR',
                'acml_vol': 'ACML_VOL',
                'stck_bsop_date': 'STCK_BSOP_DATE'
            }

            for api_key, db_key in column_mapping.items():
                if api_key in item:
                    converted_item[db_key] = item[api_key]

            converted_item["ST_CODE"] = code

            item.clear()
            item.update(converted_item)

    return response


async def get_inquire_asking_price(user_id: str, code: str):
    """주식 호가 조회"""
    user_data, access_data = await _get_user_auth(user_id)
    path = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    if access_data.get("simulation_yn") == "Y":
        url = get_env("DEV_API_URL")
    else:
        url = get_env("REAL_API_URL")
    api_url = f"{url}/{path}"

    headers = {
        "authorization": f"Bearer {access_data.get('access_token')}",
        "appkey": access_data.get("api_key"),
        "appsecret": access_data.get("secret_key"),
        "tr_id": "FHKST01010200",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }

    return await fetch("GET", api_url, params=params, headers=headers)