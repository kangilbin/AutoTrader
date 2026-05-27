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
        await asyncio.sleep(0.3)  # KIS API 초당 거래건수 제한 방지
        return await get_stock_balance(
            user_id, db, excg_cd, crcy_cd,
            body.get("ctx_area_fk200", ""),
            body.get("ctx_area_nk200", ""),
            result
        )

    return {"output1": result, "output2": output2}


async def get_present_balance(
    user_id: str, db: AsyncSession,
    natn_cd: str = "840",            # 840: 미국
    tr_mket_cd: str = "00",          # 00: 전체 (NASD/NYSE/AMEX 통합)
    wcrc_frcr_dvsn_cd: str = "02",   # 02: 외화(USD) 기준
    inqr_dvsn_cd: str = "00",        # 00: 전체
):
    """해외 주식 체결기준 현재 잔고 조회 (CTRP6504R)

    `get_stock_balance`(TTTS3012R)에는 외화 예수금/사용가능금액이 없어
    가용 자본 산출이 불가능하다. 본 API는 외화사용가능금액(USD 현금)을
    포함하므로 swing_mapping/get_available_capital 등에서 사용한다.

    응답은 `get_stock_balance`와 동일한 (output1, output2) 형태로 정규화하여
    호출부에서 시장별 분기 외 추가 변환을 최소화한다.

    Returns:
        {
            "output1": [...],   # 보유 종목 (USD 기준, 정규화된 필드)
            "output2": {...},   # 계좌 요약 (USD 가용 + 합계)
        }
    """
    user_data, access_data = await _get_user_auth(user_id, db)

    path = "uapi/overseas-stock/v1/trading/inquire-present-balance"
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    api_url = f"{url}/{path}"

    tr_id = "VTRP6504R" if access_data.get("simulation_yn") == "Y" else "CTRP6504R"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "WCRC_FRCR_DVSN_CD": wcrc_frcr_dvsn_cd,
        "NATN_CD": natn_cd,
        "TR_MKET_CD": tr_mket_cd,
        "INQR_DVSN_CD": inqr_dvsn_cd,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]

    # 보유 종목 정규화 — mapping_swing이 기대하는 키 이름으로 변환 (USD 기준)
    output1 = [
        {
            "pdno": item.get("pdno"),
            "prdt_name": item.get("prdt_name"),
            "hldg_qty": item.get("ccld_qty_smtl1", "0"),
            "ord_psbl_qty": item.get("ord_psbl_qty1", "0"),
            "pchs_avg_pric": item.get("avg_unpr3", "0"),
            "pchs_amt": item.get("frcr_pchs_amt", "0"),
            "evlu_amt": item.get("frcr_evlu_amt2", "0"),
            "evlu_pfls_amt": item.get("evlu_pfls_amt2", "0"),
            "evlu_pfls_rt": item.get("evlu_pfls_rt1", "0"),
            "prpr": item.get("ovrs_now_pric1", "0"),
            "ovrs_excg_cd": item.get("ovrs_excg_cd"),
        }
        for item in (body.get("output1") or [])
    ]

    # 통화별 잔고(output2)에서 거래통화(USD) 1건 추출
    crcy_code = "USD" if natn_cd == "840" else None
    currency_row = next(
        (
            row for row in (body.get("output2") or [])
            if not crcy_code or row.get("crcy_cd") == crcy_code
        ),
        {},
    )

    # 종합 요약(output3)
    summary = body.get("output3") or {}

    # USD 기준 종목별 합계 — 종합 요약은 원화 환산이므로 USD 합계는 output1에서 직접 산출
    def _to_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    pchs_total = sum(_to_float(item["pchs_amt"]) for item in output1)
    evlu_total = sum(_to_float(item["evlu_amt"]) for item in output1)
    pfls_total = sum(_to_float(item["evlu_pfls_amt"]) for item in output1)

    # mapping_swing summary가 기대하는 키로 매핑 (USD 단위)
    output2 = {
        "dnca_tot_amt": currency_row.get("frcr_dncl_amt_2") or summary.get("frcr_use_psbl_amt", "0"),
        "tot_evlu_amt": f"{evlu_total:.2f}",
        "pchs_amt_smtl_amt": f"{pchs_total:.2f}",
        "evlu_pfls_smtl_amt": f"{pfls_total:.2f}",
        # 부가 정보 (필요 시 호출부에서 활용)
        "frcr_use_psbl_amt": summary.get("frcr_use_psbl_amt", "0"),
        "frcr_drwg_psbl_amt": currency_row.get("frcr_drwg_psbl_amt_1", "0"),
        "tot_asst_amt_krw": summary.get("tot_asst_amt", "0"),
        "frcr_evlu_tota_krw": summary.get("frcr_evlu_tota", "0"),
        "exrt": currency_row.get("frst_bltn_exrt", "0"),
    }

    return {"output1": output1, "output2": output2}


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
        "OVRS_EXCG_CD": order.excg_cd, # NASD
        "PDNO": order.itm_no,
        "ORD_QTY": str(order.qty),
        "OVRS_ORD_UNPR": str(order.unpr),
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가 (미국 시장가 제한)
    }
    response = await fetch("POST", api_url, "KIS", json=query, headers=headers)
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

async def get_inquire_price(user_id: str, code: str, db: AsyncSession):
    """해외 주식 현재가 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/price"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS00000300")
    query = {
        "AUTH": "",
        "EXCD": "NAS",
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


async def get_inquire_asking_price(user_id: str, code: str, db: AsyncSession):
    """해외 주식 호가 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/inquire-asking-price"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76200100")
    query = {
        "AUTH": "",
        "EXCD": 'NAS',
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
        "VOL_RANG": "5",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output2")


async def get_volume_rank(user_id: str, db: AsyncSession):
    """해외주식 거래량 순위"""
    user_data, access_data = await _get_user_auth(user_id, db)
    path = "uapi/overseas-stock/v1/ranking/trade-vol"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76270000")
    query = {
        "KEYB": "",
        "AUTH": "",
        "EXCD": "NAS",
        "NDAY": "0",
        "MINX": "4",
        "VOL_RANG": "4",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output2")


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
        "NDAY": "8",
        "VOL_RANG": "4",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output2")
