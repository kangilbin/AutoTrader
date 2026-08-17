"""
KIS (한국투자증권) API 해외 주식 통합 모듈
미국 주식 (나스닥) 전용 API 호출
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.redis import get_redis
from app.core.config import get_settings
from app.core.market_code import to_ovrs_excg_cd, US_TRADE_EXCG
from app.core.order import Order, ModifyOrder
from app.exceptions import ExternalServiceError
from app.external.headers import kis_headers
from app.external.http_client import fetch
from app.external.kis_api import _get_user_auth, is_simulation, oauth_token

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
    """해외 주식 잔고 조회 (TTTS3012R) — 체결 즉시 반영되는 보유 종목/평가금액

    실시간 보유 수량/매입금액/평가손익은 본 API를 사용한다.
    output2에는 외화 예수금이 없으므로, USD 현금/주문가능금액은
    `get_foreign_margin`(해외증거금 통화별조회)에서 별도로 가져온다.
    """
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

    # 보유 종목 정규화 — mapping_swing이 기대하는 키 이름으로 변환 (USD 기준)
    output1 = [
        {
            "pdno": item.get("ovrs_pdno"),
            "prdt_name": item.get("ovrs_item_name"),
            "hldg_qty": item.get("ovrs_cblc_qty", "0"),
            "ord_psbl_qty": item.get("ord_psbl_qty", "0"),
            "pchs_avg_pric": item.get("pchs_avg_pric", "0"),
            "pchs_amt": item.get("frcr_pchs_amt1", "0"),
            "evlu_amt": item.get("ovrs_stck_evlu_amt", "0"),
            "evlu_pfls_amt": item.get("frcr_evlu_pfls_amt", "0"),
            "evlu_pfls_rt": item.get("evlu_pfls_rt", "0"),
            "prpr": item.get("now_pric2", "0"),
            "ovrs_excg_cd": item.get("ovrs_excg_cd"),
        }
        for item in result
    ]

    return {"output1": output1, "output2": output2}


async def get_foreign_margin(
    user_id: str, db: AsyncSession, crcy_cd: str = "USD",
):
    """해외증거금 통화별조회 (TTTC2101R)

    통화별 외화예수금/주문가능금액/증거금을 제공한다.
    - 가용자본 산출(get_available_capital): 외화주문가능금액(ord_psbl_amt)
    - 현금 자산 표시(mapping_swing): 외화예수금(dnca_amt)

    ⚠️ KIS 명세상 모의투자 미지원. 모의 계정은 외화예수금/주문가능금액 소스가
    없으므로 None을 반환하며, 호출부는 가용자본/현금을 '미지원'으로 처리한다.

    Returns:
        실전: 지정 통화(기본 USD) 1건을 정규화한 dict (값은 문자열, KIS 원형 유지)
        모의: None
    """
    user_data, access_data = await _get_user_auth(user_id, db)

    # 모의투자는 본 API(TTTC2101R) 미지원 — 현금/주문가능 소스 없음
    if access_data.get("simulation_yn") == "Y":
        return None

    path = "uapi/overseas-stock/v1/trading/foreign-margin"
    api_url = f"{settings.REAL_API_URL}/{path}"

    tr_id = "TTTC2101R"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]

    # output(통화별 array)에서 거래통화(USD) 1건 추출
    currency_row = next(
        (row for row in (body.get("output") or []) if row.get("crcy_cd") == crcy_cd),
        {},
    )

    return {
        "ord_psbl_amt": currency_row.get("frcr_ord_psbl_amt1", "0"),       # 외화주문가능금액 → 가용자본
        "dnca_amt": currency_row.get("frcr_dncl_amt1", "0"),              # 외화예수금 → 현금 표시
        "gnrl_ord_psbl_amt": currency_row.get("frcr_gnrl_ord_psbl_amt", "0"),  # 외화일반주문가능금액 (검증용 후보)
        "itgr_ord_psbl_amt": currency_row.get("itgr_ord_psbl_amt", "0"),       # 통합주문가능금액 (검증용 후보)
        "exrt": currency_row.get("bass_exrt", "0"),                       # 기준환율
    }


async def get_us_holdings(user_id: str, db: AsyncSession):
    """미국 전 거래소 보유종목 조회 (output1 병합)

    - 실전: OVRS_EXCG_CD="NASD"(미국전체) 1회 호출
    - 모의: 미국전체 미지원 → NASD/NYSE/AMEX 순회 후 output1 병합

    소비처(mapping_swing)는 output1만 사용(평가합계 재계산, 현금은 해외증거금 별도)하므로
    output2는 빈 dict로 반환한다.
    """
    _, access_data = await _get_user_auth(user_id, db)
    sim = access_data.get("simulation_yn") == "Y"

    if not sim:
        return await get_stock_balance(user_id, db, excg_cd="NASD")  # 미국전체 1회

    merged: List = []
    for i, excg in enumerate(US_TRADE_EXCG):  # ("NASD", "NYSE", "AMEX")
        if i > 0:
            await asyncio.sleep(0.3)  # 호출 사이 간격 — KIS 초당 거래건수 제한 회피
        r = await get_stock_balance(user_id, db, excg_cd=excg)
        for it in r["output1"]:
            # 응답에 거래소코드가 없으면 조회한 거래소로 보정 (종목별 시장 구분 보존)
            if not it.get("ovrs_excg_cd"):
                it["ovrs_excg_cd"] = excg
        merged.extend(r["output1"])
    return {"output1": merged, "output2": {}}


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
        "OVRS_EXCG_CD": to_ovrs_excg_cd(order.excg_cd),  # 정식코드(NAS/NYS/AMS) → NASD/NYSE/AMEX
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
        "OVRS_EXCG_CD": to_ovrs_excg_cd(order.excg_cd or "NAS"),
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

async def get_inquire_daily_ccld_obj(user_id: str, db: AsyncSession, excg_cd: str = "NAS", fk200="", nk200=""):
    """해외 주식 미체결 내역 조회 (excg_cd: 정식코드 NAS/NYS/AMS)

    ⚠️ 체결 확인에 쓰지 말 것 — 완전 체결된 주문은 이 목록에서 빠지므로
    "체결됨"과 "주문이 없음"을 구분할 수 없다. 체결 확인은
    get_inquire_ccnl_obj(주문체결내역)를 사용한다. 본 API는 잔량 확인 용도다.

    ⚠️ KIS 명세상 모의투자 미지원(TTTS3018R) → 모의 계정은 None 반환.
    """
    user_data, access_data = await _get_user_auth(user_id, db)

    # 모의투자는 본 API(TTTS3018R) 미지원 — 실전 호스트 오호출 방지
    if access_data.get("simulation_yn") == "Y":
        return None

    url = settings.REAL_API_URL
    tr_id = "TTTS3018R"

    path = 'uapi/overseas-stock/v1/trading/inquire-nccs'
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": to_ovrs_excg_cd(excg_cd),
        "SORT_SQN": "DS",
        "CTX_AREA_FK200": fk200,
        "CTX_AREA_NK200": nk200,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body


async def get_inquire_ccnl_obj(
    user_id: str, db: AsyncSession, excg_cd: str = "NAS",
    ord_strt_dt: str = None, ord_end_dt: str = None,
    ccld_nccs_dvsn: str = "00", fk200="", nk200="",
):
    """해외 주식 주문체결내역 조회 (TTTS3035R / 모의 VTTS3035R)

    체결·미체결을 모두 담으므로 체결 확인의 소스로 사용한다.
    미체결내역(TTTS3018R)은 완전 체결된 주문이 목록에서 빠지므로 쓸 수 없다.

    Args:
        ccld_nccs_dvsn: 00 전체 / 01 체결 / 02 미체결
        ord_strt_dt~ord_end_dt: 주문일자 구간. 기본값은 전일~당일 —
            미국 정규장(22:30~05:00 KST)이 자정을 넘겨 주문일자가 갈리기 때문.
    """
    user_data, access_data = await _get_user_auth(user_id, db)
    sim = access_data.get("simulation_yn") == "Y"

    url = settings.DEV_API_URL if sim else settings.REAL_API_URL
    tr_id = "VTTS3035R" if sim else "TTTS3035R"
    path = "uapi/overseas-stock/v1/trading/inquire-ccnl"
    api_url = f"{url}/{path}"

    now = datetime.now()
    if ord_strt_dt is None:
        ord_strt_dt = (now - timedelta(days=1)).strftime("%Y%m%d")
    if ord_end_dt is None:
        ord_end_dt = now.strftime("%Y%m%d")

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "PDNO": "",
        "ORD_STRT_DT": ord_strt_dt,
        "ORD_END_DT": ord_end_dt,
        "SLL_BUY_DVSN": "00",  # 전체
        "CCLD_NCCS_DVSN": ccld_nccs_dvsn,
        "OVRS_EXCG_CD": to_ovrs_excg_cd(excg_cd),
        "SORT_SQN": "DS",
        "ORD_DT": "",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "CTX_AREA_FK200": fk200,
        "CTX_AREA_NK200": nk200,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    return response["body"]


# ============================================================
# 체결 확인
# ============================================================


class _Unsupported:
    """체결 조회 자체가 불가함을 나타내는 마커 (모의투자 미지원 등)

    '아직 미체결(None)'과 '확인할 방법이 없음'을 호출부가 구분해야 하므로 별도 값으로 둔다.
    falsy라서 기존 `if not execution` 검사에는 그대로 걸린다.
    """
    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNSUPPORTED"


UNSUPPORTED = _Unsupported()

async def check_order_execution(
    user_id: str, order_no: str, db: AsyncSession,
    excg_cd: str = "NAS",
    max_retry: int = 3, delay: float = 2.0
) -> Optional[dict]:
    """
    해외 주식 체결 확인 (폴링) — 주문체결내역(TTTS3035R/VTTS3035R) 기준
    미국 장은 체결 지연이 길 수 있어 delay 2초 기본값

    Returns:
        체결 정보 / None(아직 미체결) / UNSUPPORTED(조회 자체가 불가 - 모의 미지원 등)
    """
    sim = await is_simulation(user_id, db)

    for attempt in range(max_retry):
        try:
            body = await get_inquire_ccnl_obj(user_id, db, excg_cd)
        except ExternalServiceError as e:
            # 모의는 TR 미지원 시 오류가 나므로 재시도 없이 '확인 불가'로 확정한다
            if sim:
                logger.warning(f"[체결확인-해외] 모의투자 주문체결내역 조회 불가({e}) → 확인 미지원 처리")
                return UNSUPPORTED
            logger.error(f"[체결확인-해외] 오류: {e}, 재시도 {attempt + 1}/{max_retry}")
            await asyncio.sleep(delay)
            continue

        if not body or body.get("rt_cd") != "0":
            msg = body.get("msg1", "응답 없음") if body else "응답 없음"
            if sim:
                logger.warning(f"[체결확인-해외] 모의투자 주문체결내역 응답 오류({msg}) → 확인 미지원 처리")
                return UNSUPPORTED
            logger.warning(f"[체결확인-해외] 응답 오류({msg}), 재시도 {attempt + 1}/{max_retry}")
            await asyncio.sleep(delay)
            continue

        for order in body.get("output") or []:
            if order.get("odno") != order_no:
                continue

            executed_qty = int(float(order.get("ft_ccld_qty") or 0))
            if executed_qty > 0:
                return {
                    "order_no": order_no,
                    "st_code": order.get("pdno"),
                    "avg_price": float(order.get("ft_ccld_unpr3") or 0),
                    "executed_qty": executed_qty,
                    "executed_amt": float(order.get("ft_ccld_amt3") or 0),
                    "trade_type": order.get("sll_buy_dvsn_cd"),
                    "remaining_qty": int(float(order.get("nccs_qty") or 0)),
                }

            reject = order.get("rjct_rson_name") or order.get("rjct_rson")
            logger.info(
                f"[체결확인-해외] 주문 {order_no} 미체결"
                + (f" (거부사유: {reject})" if reject else "")
                + f", 재시도 {attempt + 1}/{max_retry}"
            )
            break

        await asyncio.sleep(delay)

    logger.warning(f"[체결확인-해외] 주문 {order_no} 체결 확인 실패 (max_retry 초과)")
    return None


# ============================================================
# 시세 조회
# ============================================================

async def get_inquire_price(user_id: str, code: str, db: AsyncSession, excd: str = "NAS"):
    """해외 주식 현재가상세 조회 (HHDFS76200200) — excd: 정식코드 NYS/NAS/AMS
ㅂ
    현재체결가(HHDFS00000300)와 달리 open/high/low 를 함께 제공하여
    실시간 지표(ATR 등) 계산에 필요한 당일 고가/저가를 얻을 수 있다.
    단, 현지통화 등락률 필드는 없으므로 (last-base)/base 로 계산해 사용한다.
    """
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/price-detail"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76200200")
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


async def get_stock_data(user_id: str, code: str, start_date: str, end_date: str, db: AsyncSession, excd: str = "NAS"):
    """해외 주식 기간별 데이터 조회 (excd: 정식코드 NYS/NAS/AMS)"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/dailyprice"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76240000")

    params = {
        "AUTH": "",
        "EXCD": excd,
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
    """해외 주식 호가 조회 (excd: 정식코드 NYS/NAS/AMS)"""
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


def _to_float(value) -> float:
    """KIS 응답 문자열 → float (빈값/비정상 값은 0)"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def get_best_quote(user_id: str, code: str, db: AsyncSession, excd: str = "NAS") -> Optional[dict]:
    """해외 주식 최우선 호가 조회 (매도1호가/매수1호가)

    미국은 지정가(ORD_DVSN "00")만 가능하므로 주문 단가를 직접 정해야 한다.
    현재가(last)는 '마지막 체결가'라 그 사이 호가가 움직였으면 체결되지 않으므로,
    즉시 체결되는 반대편 호가를 단가로 쓴다. (매수→ask, 매도→bid)

    Returns:
        {"ask": 매도1호가, "bid": 매수1호가} 또는 None(조회/파싱 실패)
    """
    body = await get_inquire_asking_price(user_id, code, db, excd)
    if not body:
        return None

    # output2는 배열이며 1~10호가가 담긴다. 주문에는 최우선호가만 쓰므로 첫 원소만 사용.
    # (output1은 기본 시세(last/open/high/low)라 호가 필드가 없다)
    rows = body.get("output2") or []
    quote = rows[0] if isinstance(rows, list) and rows else None

    if not isinstance(quote, dict):
        logger.warning(
            f"[호가조회] {code}({excd}) output2 없음/형식 불일치 "
            f"(type={type(rows).__name__}, 응답 키={list(body.keys())})"
        )
        return None

    ask = _to_float(quote.get("pask1"))
    bid = _to_float(quote.get("pbid1"))

    if ask <= 0 and bid <= 0:
        logger.warning(f"[호가조회] {code}({excd}) 최우선호가 없음 (output2[0] 키={list(quote.keys())})")
        return None

    return {"ask": ask, "bid": bid}


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


async def get_volume_rank(user_id: str, db: AsyncSession, excd: str = "NAS"):
    """해외주식 거래량 순위 (excd: 정식코드 NYS/NAS/AMS)"""
    user_data, access_data = await _get_user_auth(user_id, db)
    path = "uapi/overseas-stock/v1/ranking/trade-vol"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS76270000")
    query = {
        "KEYB": "",
        "AUTH": "",
        "EXCD": excd,
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
