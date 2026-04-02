"""
KIS (한국투자증권) API 해외 주식 통합 모듈
미국 주식 (나스닥) 전용 API 호출
"""
import asyncio
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.redis import get_redis
from app.core.config import get_settings
from app.domain.order.entity import Order, ModifyOrder
from app.external.headers import kis_headers
from app.external.http_client import fetch
from app.external.kis_api import _get_user_auth, oauth_token

logger = logging.getLogger(__name__)
settings = get_settings()


# ============================================================
# 잔고 조회
# ============================================================

async def get_stock_balance(
    user_id: str, db: AsyncSession,
    excg_cd: str = "NASD", crcy_cd: str = "USD",
    fk200="", nk200="", result: Optional[List] = None,
):
    """해외 주식 잔고 조회 (output1: 종목 리스트, output2: 계좌 요약)"""
    user_data, access_data = await _get_user_auth(user_id, db)

    path = "uapi/overseas-stock/v1/trading/inquire-balance"
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    api_url = f"{url}/{path}"

    tr_id = "VTTS3012R" if access_data.get("simulation_yn") == "Y" else "TTTS3012R"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": excg_cd,
        "TR_CRCY_CD": crcy_cd,
        "CTX_AREA_FK200": fk200,
        "CTX_AREA_NK200": nk200,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    header = response["header"]
    tr_cont = header.get("tr_cont")

    if result is None:
        result = list(body.get("output1", []))
    else:
        result.extend(body.get("output1", []))

    output2 = body.get("output2", {})

    if tr_cont in ("F", "M"):
        return await get_stock_balance(
            user_id, db, excg_cd, crcy_cd,
            body.get("ctx_area_fk200", ""),
            body.get("ctx_area_nk200", ""),
            result
        )

    return {"output1": result, "output2": output2}


# ============================================================
# 주문
# ============================================================

async def place_order_api(user_id: str, order: Order, db: AsyncSession):
    """해외 주식 주문 (미국)"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-stock/v1/trading/order"
    api_url = f"{url}/{path}"

    sim = access_data.get("simulation_yn") == "Y"
    if order.ord_dv == "buy":
        tr_id = "VTTT1002U" if sim else "JTTT1002U"  # 미국 매수
    elif order.ord_dv == "sell":
        tr_id = "VTTT1001U" if sim else "JTTT1006U"  # 미국 매도
    else:
        return None

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": order.excg_cd,
        "PDNO": order.itm_no,
        "ORD_QTY": str(order.qty),
        "OVRS_ORD_UNPR": str(order.unpr),
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가 (미국 시장가 제한)
    }
    response = await fetch("POST", api_url, "KIS", body=query, headers=headers)
    body = response["body"]
    return body


# ============================================================
# 주문 정정/취소
# ============================================================

async def modify_or_cancel_order_api(user_id: str, order: ModifyOrder, db: AsyncSession):
    """해외 주식 주문 정정/취소"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-stock/v1/trading/order-rvsecncl"
    api_url = f"{url}/{path}"

    sim = access_data.get("simulation_yn") == "Y"
    tr_id = "VTTT1004U" if sim else "JTTT1004U"

    ord_qty = 0 if order.qty_all_ord_yn == 'Y' else order.ord_qty

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": order.excg_cd if hasattr(order, 'excg_cd') else "NASD",
        "PDNO": order.pdno,
        "ORGN_ODNO": order.orgn_odno,
        "RVSE_CNCL_DVSN_CD": order.rvse_cncl_dvsn_cd,
        "ORD_QTY": str(ord_qty),
        "OVRS_ORD_UNPR": str(order.ord_unpr) if order.rvse_cncl_dvsn_cd == '01' else "0",
    }
    response = await fetch("POST", api_url, "KIS", json=query, headers=headers)
    body = response["body"]
    return body


# ============================================================
# 미체결 내역 조회
# ============================================================

async def get_inquire_daily_ccld_obj(user_id: str, db: AsyncSession, excg_cd: str = "NASD", fk200="", nk200=""):
    """해외 주식 미체결 내역 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.REAL_API_URL
    tr_id = "TTTS3018R"

    path = 'uapi/overseas-stock/v1/trading/inquire-nccs'
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": excg_cd,
        "SORT_SQN": "DS",
        "CTX_AREA_FK200": fk200,
        "CTX_AREA_NK200": nk200,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body


# ============================================================
# 체결 확인
# ============================================================

async def check_order_execution(
    user_id: str, order_no: str, db: AsyncSession,
    excg_cd: str = "NASD",
    max_retry: int = 3, delay: float = 2.0
) -> Optional[dict]:
    """
    해외 주식 체결 확인 (폴링)
    미국 장은 체결 지연이 길 수 있어 delay 2초 기본값

    Returns:
        체결 정보 또는 None
    """
    for attempt in range(max_retry):
        try:
            result = await get_inquire_daily_ccld_obj(user_id, db, excg_cd)

            if not result or "output" not in result:
                logger.warning(f"[체결확인-해외] 응답 없음, 재시도 {attempt + 1}/{max_retry}")
                await asyncio.sleep(delay)
                continue

            for order in result.get("output", []):
                if order.get("odno") == order_no:
                    executed_qty = int(order.get("ft_ccld_qty", 0))

                    if executed_qty > 0:
                        return {
                            "order_no": order_no,
                            "st_code": order.get("pdno"),
                            "avg_price": float(order.get("ft_ccld_unpr3", 0)),
                            "executed_qty": executed_qty,
                            "executed_amt": float(order.get("ft_ccld_amt3", 0)),
                            "trade_type": order.get("sll_buy_dvsn_cd")
                        }
                    else:
                        logger.info(f"[체결확인-해외] 주문 {order_no} 미체결, 재시도 {attempt + 1}/{max_retry}")
                        break

            await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"[체결확인-해외] 오류: {e}, 재시도 {attempt + 1}/{max_retry}")
            await asyncio.sleep(delay)

    logger.warning(f"[체결확인-해외] 주문 {order_no} 체결 확인 실패 (max_retry 초과)")
    return None


# ============================================================
# 시세 조회
# ============================================================

async def get_inquire_price(user_id: str, code: str, db: AsyncSession, excd: str = "NAS"):
    """해외 주식 현재가 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/price"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS00000300")
    query = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": code,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")


async def get_target_price(code: str, excd: str = "NAS"):
    """해외 종목 일별 시세 조회 (관리자 토큰 사용)"""
    redis = await get_redis()
    access_data = await redis.hgetall("mgnt_access_token")

    if not access_data:
        access_data = await oauth_token("mgnt", "Y", settings.API_KEY, settings.SECRET_KEY)

    url = settings.REAL_API_URL
    path = 'uapi/overseas-price/v1/quotations/dailyprice'
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76240000")
    query = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": code,
        "GUBN": "0",
        "BYMD": "",
        "MODP": "0",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    output = body.get("output2", [])
    return output[0] if output else None


async def get_stock_data(user_id: str, code: str, start_date: str, end_date: str, db: AsyncSession):
    """해외 주식 기간별 데이터 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/dailyprice"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76240000")

    params = {
        "AUTH": "",
        "EXCD": "NAS",
        "SYMB": code,
        "GUBN": "0",
        "BYMD": end_date,
        "MODP": "0",
    }

    response = await fetch("GET", api_url, "KIS", params=params, headers=headers)
    body = response["body"]

    # API 응답 키를 DB 스키마에 맞게 변환
    if body and "output2" in body:
        for item in body["output2"]:
            converted_item = {}
            column_mapping = {
                'open': 'STCK_OPRC',
                'high': 'STCK_HGPR',
                'low': 'STCK_LWPR',
                'clos': 'STCK_CLPR',
                'tvol': 'ACML_VOL',
                'xymd': 'STCK_BSOP_DATE',
            }
            for api_key, db_key in column_mapping.items():
                if api_key in item:
                    converted_item[db_key] = item[api_key]

            converted_item["ST_CODE"] = code
            item.clear()
            item.update(converted_item)

    return body


async def get_inquire_asking_price(user_id: str, code: str, db: AsyncSession, excd: str = "NAS"):
    """해외 주식 호가 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/inquire-asking-price"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76200100")
    query = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": code,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body


# ============================================================
# 순위 조회
# ============================================================

async def get_fluctuation_rank(user_id: str, db: AsyncSession, rank_sort_cls_code: str = "0", excd: str = "NAS"):
    """해외주식 등락률 순위"""
    user_data, access_data = await _get_user_auth(user_id, db)
    path = "uapi/overseas-stock/v1/ranking/price-fluct"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76260000")
    query = {
        "KEYB": "",
        "AUTH": "",
        "EXCD": excd,
        "GUBN": rank_sort_cls_code,
        "MINX": "4",
        "VOL_RANG": "4",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")


async def get_volume_rank(user_id: str, db: AsyncSession, excd: str = "NAS"):
    """해외주식 거래량 순위"""
    user_data, access_data = await _get_user_auth(user_id, db)
    path = "uapi/overseas-stock/v1/ranking/volume-surge"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76270000")
    query = {
        "KEYB": "",
        "AUTH": "",
        "EXCD": excd,
        "MINX": "4",
        "VOL_RANG": "4",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")


async def get_volume_power_rank(user_id: str, db: AsyncSession, excd: str = "NAS"):
    """해외주식 체결강도 순위"""
    user_data, access_data = await _get_user_auth(user_id, db)
    path = "uapi/overseas-stock/v1/ranking/volume-power"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76280000")
    query = {
        "KEYB": "",
        "AUTH": "",
        "EXCD": excd,
        "MINX": "4",
        "VOL_RANG": "4",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")
