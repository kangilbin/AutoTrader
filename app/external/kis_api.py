"""
KIS (한국투자증권) API 통합 모듈
"""
import asyncio
from datetime import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ExternalServiceError, NotFoundError
from app.core.config import get_settings
from app.core.security import decrypt
from app.external.headers import kis_headers, kis_error_message
from app.external.http_client import fetch
from app.common.redis import get_redis
from app.core.order import Order, ModifyOrder, same_order_no
from app.domain.account.repository import AccountRepository
from app.domain.auth.repository import AuthRepository
from typing import List, Optional

logger = logging.getLogger(__name__)
settings = get_settings()

# ============================================================
# OAuth 및 인증 관련
# ============================================================


_TOKEN_LOCK_TTL = 10  # 락 자동 해제 시간(초). 토큰 발급은 수 초 이내 완료
_TOKEN_WAIT_INTERVAL = 0.2  # 락 대기 시 Redis 재조회 간격(초)
_TOKEN_WAIT_MAX_ATTEMPTS = 50  # 최대 대기 시도 (0.2s * 50 = 10s)


def _token_cache_key(user_id: str, auth_id=None) -> str:
    """토큰 캐시 키. 인증키(AUTH_ID)별로 슬롯을 분리해 모의/실전 토큰이 공존하도록 한다."""
    return f"{user_id}_{auth_id}_access_token" if auth_id else f"{user_id}_access_token"


async def _read_cached_token(redis, cache_key: str, api_key: str, secret_key: str) -> Optional[dict]:
    """Redis에서 유효한 토큰 캐시 조회 (key 일치 확인)"""
    access_data = await redis.hgetall(cache_key)
    if not access_data:
        return None
    if access_data.get("api_key") == api_key and access_data.get("secret_key") == secret_key:
        return access_data
    return None


async def oauth_token(user_id: str, simulation_yn: str, api_key: str, secret_key: str, auth_id=None):
    """
    한국 투자 증권 접근 토큰
    유효기간 24시 이며 (1일 1회 발급) 갱신발급 주기는 6시간(6시 이내는 기존 발급키 응답)

    KIS는 동일 appkey로 1분에 1회만 토큰 발급 허용 (EGW00133)
    동시 요청에서 중복 발급을 막기 위해 Redis 분산 락 사용

    auth_id별로 캐시 슬롯을 분리해, 모의/실전 계좌를 번갈아 선택해도
    각 토큰이 공존하며 불필요한 재발급(→ 1분 제한 위반)을 방지한다.
    """
    redis = await get_redis()
    cache_key = _token_cache_key(user_id, auth_id)

    cached = await _read_cached_token(redis, cache_key, api_key, secret_key)
    if cached:
        return cached

    # key 불일치로 stale 캐시인 경우 제거
    await redis.delete(cache_key)

    lock_key = f"{cache_key}:lock"
    got_lock = await redis.set(lock_key, "1", nx=True, ex=_TOKEN_LOCK_TTL)

    if not got_lock:
        # 다른 요청이 발급 중. 발급 완료를 폴링으로 대기.
        for _ in range(_TOKEN_WAIT_MAX_ATTEMPTS):
            await asyncio.sleep(_TOKEN_WAIT_INTERVAL)
            cached = await _read_cached_token(redis, cache_key, api_key, secret_key)
            if cached:
                return cached
        raise ExternalServiceError("KIS", "토큰 발급 대기 시간 초과")

    try:
        # 락 획득 후 한 번 더 캐시 확인 (락 대기 중 다른 워커가 발급했을 수 있음)
        cached = await _read_cached_token(redis, cache_key, api_key, secret_key)
        if cached:
            return cached

        path = "oauth2/tokenP"
        if simulation_yn == "Y":
            api_url = settings.DEV_API_URL
        else:
            api_url = settings.REAL_API_URL

        url = f"{api_url}/{path}"
        query = {
            "grant_type": "client_credentials",
            "appkey": api_key,
            "appsecret": secret_key
        }

        response = await fetch("POST", url, "KIS", json=query)
        body = response["body"]
        access_token = body.get("access_token")

        if (not access_token) or (body.get("error_code")):
            raise ExternalServiceError("KIS", kis_error_message(body, "토큰 발급 실패"))

        data = {
            "access_token": access_token,
            "api_key": api_key,
            "secret_key": secret_key,
            "simulation_yn": simulation_yn
        }
        # Redis에 토큰 저장 만료기간(expires_in) 설정
        await redis.hset(cache_key, mapping=data)
        await redis.expire(cache_key, body.get("expires_in"))
        return data
    finally:
        await redis.delete(lock_key)


async def _token_for_auth_id(user_id: str, auth_id, db: AsyncSession) -> dict:
    """AUTH_KEY의 인증키로 토큰 확보 (캐시 우선, 없으면 발급)

    캐시 슬롯이 auth_id별로 분리되어 있어 모의/실전 토큰이 공존한다.
    """
    redis = await get_redis()
    access_data = await redis.hgetall(_token_cache_key(user_id, auth_id))
    if access_data:
        return access_data

    auth_data = await AuthRepository(db).find_by_id(user_id, int(auth_id))
    if not auth_data:
        raise NotFoundError("인증키", auth_id)

    return await oauth_token(
        user_id,
        auth_data["SIMULATION_YN"],
        decrypt(auth_data["API_KEY"]),
        decrypt(auth_data["SECRET_KEY"]),
        auth_id=auth_id,
    )


async def _get_account_auth(user_id: str, account_no: str, db: AsyncSession):
    """계좌번호 기준 인증 해석 (로그인 세션이 없는 배치 경로)

    Redis 세션의 ACCOUNT_NO는 '사용자가 앱에서 마지막으로 고른 계좌'라
    스윙이 등록된 계좌와 다를 수 있다. 계좌가 명시되면 세션을 보지 않고
    ACCOUNT 테이블의 계좌↔인증키 바인딩을 따른다 (실전/모의 혼동 차단).
    """
    auth_id = await AccountRepository(db).find_auth_id_by_account_no(user_id, account_no)
    if not auth_id:
        raise NotFoundError("계좌", account_no)

    access_data = await _token_for_auth_id(user_id, auth_id, db)
    return {"ACCOUNT_NO": account_no, "AUTH_ID": str(auth_id)}, access_data


async def _get_user_auth(user_id: str, db: AsyncSession, account_no: str = None):
    """인증 정보 조회

    account_no 지정 시: DB의 계좌↔인증키 바인딩으로 확정 (배치 등 세션 없는 경로)
    미지정 시: Redis 세션에서 사용자가 선택한 인증키 사용 (API 요청 경로)
    """
    if account_no:
        return await _get_account_auth(user_id, account_no, db)

    redis = await get_redis()
    user_data = await redis.hgetall(user_id)

    auth_id = user_data.get("AUTH_ID")
    if not auth_id:
        raise ExternalServiceError("KIS", "인증키가 선택되지 않았습니다. 인증키를 먼저 선택해주세요.")

    # 선택된 인증키 슬롯에서 토큰 조회 (모의/실전 전환해도 각자 캐시 유지)
    access_data = await _token_for_auth_id(user_id, auth_id, db)

    return user_data, access_data


async def _quote_auth(user_id: str, db: AsyncSession, account_no: str = None) -> dict:
    """시세 조회용 인증 (CANO를 쓰지 않는 함수 전용)

    계좌 지정 > 로그인 세션 > 계좌 무관 폴백 순으로 해석한다.
    시세는 어느 인증키로 조회해도 결과가 같으므로 마지막 폴백이 안전하고,
    덕분에 배치가 로그인 세션 없이도 시세를 볼 수 있다.
    주문·잔고는 계좌가 확정돼야 하므로 이 헬퍼를 쓰지 않는다.
    """
    if account_no:
        _, access_data = await _get_account_auth(user_id, account_no, db)
        return access_data

    # 세션에 선택된 인증키가 있으면 그 키를 쓴다. 예외를 잡아 폴백하지 않는 이유:
    # _get_user_auth는 '인증키 미선택'과 '토큰 발급 실패/대기 초과'에 같은 예외를 쓴다.
    # 후자까지 삼키면 일시적 KIS 장애가 조용히 다른 appkey 발급으로 이어져
    # 장애가 로그에 안 남고 appkey별 1분 발급 제한까지 건드린다.
    redis = await get_redis()
    auth_id = (await redis.hgetall(user_id)).get("AUTH_ID")
    if auth_id:
        return await _token_for_auth_id(user_id, auth_id, db)

    # 선택된 인증키 없음 (배치/세션 만료) → 계좌 무관 키로 조회
    return await get_quote_auth(user_id, db)


async def get_quote_auth(user_id: str, db: AsyncSession) -> dict:
    """시세 조회 전용 토큰 (계좌 무관)

    시세 TR은 CANO를 쓰지 않으므로 계좌를 고를 필요가 없다. 배치처럼
    로그인 세션도 계좌 컨텍스트도 없는 경로가 시세를 볼 수 있게 한다.

    선택 규칙: 실전키 우선(모의 도메인은 일부 TR 미지원) → AUTH_ID 오름차순.
    조회 전용이므로 어느 키를 써도 결과가 같고, 주문으로 새지 않는다.
    """
    auths = await AuthRepository(db).find_all_by_user(user_id)
    if not auths:
        raise NotFoundError("인증키", user_id)

    auth = sorted(auths, key=lambda a: (a.SIMULATION_YN == "Y", a.AUTH_ID))[0]
    return await _token_for_auth_id(user_id, auth.AUTH_ID, db)


async def is_simulation(user_id: str, db: AsyncSession, account_no: str = None) -> bool:
    """선택된(또는 계좌에 묶인) 인증키가 모의투자 계정인지 여부

    모의투자 미지원 API(해외 미체결 조회 등)를 호출부에서 건너뛰기 위해
    simulation_yn 판별을 공개 헬퍼로 노출한다. (토큰 캐시 재사용 → 추가 비용 없음)
    """
    _, access_data = await _get_user_auth(user_id, db, account_no)
    return access_data.get("simulation_yn") == "Y"


# ============================================================
# 토큰 발급 (캐싱 없음)
# ============================================================

async def issue_token(simulation_yn: str, api_key: str, secret_key: str) -> dict:
    """KIS OAuth 토큰 발급 (Redis 캐싱 없이 즉시 발급)"""
    path = "oauth2/tokenP"
    if simulation_yn == "Y":
        api_url = settings.DEV_API_URL
    else:
        api_url = settings.REAL_API_URL

    url = f"{api_url}/{path}"
    query = {
        "grant_type": "client_credentials",
        "appkey": api_key,
        "appsecret": secret_key,
    }

    response = await fetch("POST", url, "KIS", json=query)
    body = response["body"]
    access_token = body.get("access_token")

    if (not access_token) or (body.get("error_code")):
        raise ExternalServiceError("KIS", kis_error_message(body, "토큰 발급 실패"))

    return {
        "access_token": access_token,
        "api_key": api_key,
        "secret_key": secret_key,
        "simulation_yn": simulation_yn,
    }


# ============================================================
# 계좌 검증 관련
# ============================================================

async def verify_account_balance(access_data: dict, account_no: str):
    """계좌번호 검증 - KIS 잔고 조회 API로 유효성 확인"""
    path = "uapi/domestic-stock/v1/trading/inquire-balance"
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL

    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC8434R"
    else:
        tr_id = "TTTC8434R"

    headers = kis_headers(access_data, tr_id=tr_id)

    query = {
        "CANO": account_no[:8],
        "ACNT_PRDT_CD": account_no[-2:],
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]

    if body.get("rt_cd") != "0":
        raise ExternalServiceError("KIS", body.get("msg1", "계좌번호 검증 실패"))


# ============================================================
# 잔고 조회 관련
# ============================================================

async def get_stock_balance(user_id: str, db: AsyncSession, fk100="", nk100="", result: Optional[List] = None,
                            account_no: str = None):
    """보유 주식 조회 (output1: 종목 리스트, output2: 계좌 요약)"""
    user_data, access_data = await _get_user_auth(user_id, db, account_no)

    path = "uapi/domestic-stock/v1/trading/inquire-balance"
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL

    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC8434R"  # 모의투자
    else:
        tr_id = "TTTC8434R"  # 실전투자

    headers = kis_headers(
        access_data,
        tr_id=tr_id,
    )

    query = {
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
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    header = response["header"]
    tr_cont = header.get("tr_cont")

    ctx_area_fk100 = body.get("ctx_area_fk100")
    ctx_area_nk100 = body.get("ctx_area_nk100")
    if result is None:
        result = list(body.get("output1"))
    else:
        result.extend(body.get("output1"))

    # output2: 계좌 요약 (예수금, 총평가금액, 손익 등) - 마지막 호출 값이 유효
    output2 = body.get("output2", [{}])
    output2_data = output2[0] if output2 else {}

    if tr_cont == "F" or tr_cont == "M":  # 다음 페이지 존재하는 경우 자기 호출 처리
        return await get_stock_balance(user_id, db, ctx_area_fk100, ctx_area_nk100, result, account_no)

    return {"output1": result, "output2": output2_data}


# ============================================================
# 주문 관련
# ============================================================

async def place_order_api(user_id: str, order: Order, db: AsyncSession, account_no: str = None):
    """주식 주문"""
    user_data, access_data = await _get_user_auth(user_id, db, account_no)
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL
    path = "uapi/domestic-stock/v1/trading/order-cash"
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

    headers = kis_headers(
        access_data,
        tr_id=tr_id,
    )

    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "PDNO": order.itm_no,
        "ORD_DVSN": "01",  # 시장가
        "ORD_QTY": str(order.qty),
        "ORD_UNPR": "0"
    }
    response = await fetch("POST", api_url, "KIS", json=query, headers=headers)
    body = response["body"]
    return body


async def get_cancelable_orders_api(user_id: str, db: AsyncSession, fk100="", nk100="", account_no: str = None):
    """주식 정정/취소 가능 주문 내역"""
    user_data, access_data = await _get_user_auth(user_id, db, account_no)

    path = "uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    api_url = f"{settings.REAL_API_URL}/{path}"
    
    tr_id = "TTTC0084R"

    headers = kis_headers(
        access_data,
        tr_id=tr_id,
    )
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "INQR_DVSN_1": "1",
        "INQR_DVSN_2": "0",
        "CTX_AREA_FK100": fk100,
        "CTX_AREA_NK100": nk100
    }
    response = await fetch("POST", api_url, "KIS", json=query, headers=headers)
    body = response["body"]
    return body


async def modify_or_cancel_order_api(user_id: str, order: ModifyOrder, db: AsyncSession, account_no: str = None):
    """주문 정정/취소"""
    user_data, access_data = await _get_user_auth(user_id, db, account_no)
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL
    path = "uapi/domestic-stock/v1/trading/order-rvsecncl"
    api_url = f"{url}/{path}"

    if access_data.get("simulation_yn") == "Y":
        tr_id = "VTTC0803U"  # 모의투자
    else:
        tr_id = "TTTC0013U"  # 실전투자

    # 잔량전부인 경우 수량 0 처리
    ord_qty = 0 if order.qty_all_ord_yn == 'Y' else order.ord_qty

    headers = kis_headers(
        access_data,
        tr_id=tr_id,
    )
    query = {
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
    response = await fetch("POST", api_url, "KIS", json=query, headers=headers)
    body = response["body"]
    return body

async def get_inquire_daily_ccld_obj(user_id: str, db: AsyncSession, inqr_strt_dt=None, inqr_end_dt=None, fk100="", nk100="",
                                     account_no: str = None):
    """주식일별주문체결(현황)조회"""
    user_data, access_data = await _get_user_auth(user_id, db, account_no)
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
        tr_id = "VTSC9215R"
    else:
        url = settings.REAL_API_URL
        tr_id = "CTSC9215R"

    path = 'uapi/domestic-stock/v1/trading/inquire-daily-ccld'
    api_url = f"{url}/{path}"

    if inqr_strt_dt is None:
        inqr_strt_dt = datetime.today().strftime("%Y%m%d")

    if inqr_end_dt is None:
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    headers = kis_headers(
        access_data,
        tr_id=tr_id,
    )
    query = {
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
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body


async def check_order_execution(user_id: str, order_no: str, db: AsyncSession, max_retry: int = 3, delay: float = 1.0,
                                account_no: str = None) -> Optional[dict]:
    """
    주문 체결 확인 (폴링)

    Args:
        user_id: 사용자 ID
        order_no: 주문번호 (ODNO)
        db: AsyncSession (DB 세션)
        max_retry: 최대 재시도 횟수
        delay: 재시도 간격 (초)

    Returns:
        체결 정보 또는 None
        {
            "order_no": 주문번호,
            "st_code": 종목코드,
            "avg_price": 평균체결가,
            "executed_qty": 체결수량,
            "executed_amt": 체결금액,
            "trade_type": 매매구분 (01:매도, 02:매수)
        }
    """
    import asyncio

    for attempt in range(max_retry):
        try:
            result = await get_inquire_daily_ccld_obj(user_id, db, account_no=account_no)

            if not result or "output1" not in result:
                logger.warning(f"[체결확인] 응답 없음, 재시도 {attempt + 1}/{max_retry}")
                await asyncio.sleep(delay)
                continue

            # 주문번호로 체결 내역 찾기
            for order in result.get("output1", []):
                if same_order_no(order.get("odno"), order_no):
                    executed_qty = int(order.get("tot_ccld_qty", 0))

                    if executed_qty > 0:
                        # 체결 완료
                        return {
                            "order_no": order_no,
                            "st_code": order.get("pdno"),
                            "avg_price": int(order.get("avg_prvs", 0)),
                            "executed_qty": executed_qty,
                            "executed_amt": int(order.get("tot_ccld_amt", 0)),
                            "trade_type": order.get("sll_buy_dvsn_cd")  # 01:매도, 02:매수
                        }
                    else:
                        # 미체결 상태
                        logger.info(f"[체결확인] 주문 {order_no} 미체결, 재시도 {attempt + 1}/{max_retry}")
                        break

            await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"[체결확인] 오류: {e}, 재시도 {attempt + 1}/{max_retry}")
            await asyncio.sleep(delay)

    logger.warning(f"[체결확인] 주문 {order_no} 체결 확인 실패 (max_retry 초과)")
    return None


async def get_target_price(user_id: str, code: str, db: AsyncSession, access_data: dict = None):
    """종목 일별 시세 조회 (배치용 — BATCH_USER_ID의 인증키로 조회)

    이전에는 `mgnt` 전용 토큰 캐시 + settings.API_KEY/SECRET_KEY를 썼으나,
    두 설정이 Settings에 정의되지 않아 캐시가 만료되는 순간 AttributeError로
    수집 전 종목이 실패했다. 시세 TR은 계좌가 필요 없으므로 계좌 무관 토큰을 쓴다.
    """
    access_data = access_data or await _quote_auth(user_id, db)

    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL

    path = 'uapi/domestic-stock/v1/quotations/inquire-daily-price'
    api_url = f"{url}/{path}"

    headers = kis_headers(
        access_data,
        tr_id="FHKST01010400",
    )

    query = {
        "fid_cond_mrkt_div_code": "J",
        "FID_INPUT_ISCD": code,
        "FID_ORG_ADJ_PRC": "0",
        "FID_PERIOD_DIV_CODE": "D"
    }
    # 국내 시세 조회 TR은 GET + query string이다 (POST/json은 output 없는 응답 → KeyError).
    # mgnt 경로가 죽어 있어 이 함수가 성공 실행된 적이 없었기에 드러나지 않았던 버그.
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    output = body.get("output")
    if not output:
        raise ExternalServiceError("KIS", kis_error_message(body, f"{code} 일별 시세 조회 실패"))
    return output[0]


async def get_stock_data(user_id: str, code: str, start_date: str, end_date: str, db: AsyncSession, account_no: str = None):
    """기간별 주식 데이터 조회"""
    access_data = await _quote_auth(user_id, db, account_no)
    url = settings.REAL_API_URL
    path = "uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    api_url = f"{url}/{path}"

    headers = kis_headers(
        access_data,
        tr_id="FHKST03010100",
    )

    params = {
        "fid_cond_mrkt_div_code": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }

    response = await fetch("GET", api_url, "KIS", params=params, headers=headers)
    body = response["body"]
    # API 응답 데이터의 키를 대문자로 변경하고 st_code 추가
    if body and "output2" in body:
        for item in body["output2"]:
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

    return body


async def get_inquire_asking_price(user_id: str, code: str, db: AsyncSession, account_no: str = None):
    """주식 호가 조회"""
    access_data = await _quote_auth(user_id, db, account_no)
    path = "uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(
        access_data,
        tr_id="FHKST01010200",
    )

    query = {
        "fid_cond_mrkt_div_code": "J",
        "FID_INPUT_ISCD": code,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body


async def get_inquire_price(user_id: str, code: str, db: AsyncSession, account_no: str = None):
    """주식현재가 시세"""
    access_data = await _quote_auth(user_id, db, account_no)
    path = "uapi/domestic-stock/v1/quotations/inquire-price"
    if access_data.get("simulation_yn") == "Y":
        url = settings.DEV_API_URL
    else:
        url = settings.REAL_API_URL
    api_url = f"{url}/{path}"

    headers = kis_headers(
        access_data,
        tr_id="FHKST01010100",
    )

    query = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body

async def get_fluctuation_rank(user_id: str, db: AsyncSession, rank_sort_cls_code: str = "0", prc_cls_code: str = "1", account_no: str = None):
    """국내주식 등락률 순위"""
    access_data = await _quote_auth(user_id, db, account_no)
    path = "uapi/domestic-stock/v1/ranking/fluctuation"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"


    headers = kis_headers(
        access_data,
        tr_id="FHPST01700000",
    )

    query = {
        "FID_RSFL_RATE2": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20170",
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": rank_sort_cls_code,
        "FID_INPUT_CNT_1": "0",
        "FID_PRC_CLS_CODE": prc_cls_code,
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_TRGT_CLS_CODE": "0",
        "FID_TRGT_EXLS_CLS_CODE": "0",
        "FID_DIV_CLS_CODE": "1",
        "FID_RSFL_RATE1": "",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")

async def get_volume_rank(user_id: str, db: AsyncSession, blng_cls_code: str = "3", account_no: str = None):
    """국내주식 거래량 순위"""
    access_data = await _quote_auth(user_id, db, account_no)
    path = "uapi/domestic-stock/v1/quotations/volume-rank"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"


    headers = kis_headers(
        access_data,
        tr_id="FHPST01710000",
    )

    query = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20170",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "1",
        "FID_BLNG_CLS_CODE": blng_cls_code,
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")

async def get_volume_power_rank(user_id: str, db: AsyncSession, input_iscd: str = "0000", account_no: str = None):
    """국내주식 체결강도 순위"""
    access_data = await _quote_auth(user_id, db, account_no)
    path = "uapi/domestic-stock/v1/ranking/volume-power"
    url = settings.REAL_API_URL
    api_url = f"{url}/{path}"


    headers = kis_headers(
        access_data,
        tr_id="FHPST01680000",
    )

    query = {
        "fid_trgt_exls_cls_code": "1",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20168",
        "fid_input_iscd": input_iscd,
        "fid_div_cls_code": "0",
        "fid_trgt_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")


# ============================================================
# 예탁원정보 (KSD) - 액면교체/합병/분할 일정
# ============================================================


async def get_rev_split_schedule(
    user_id: str, from_date: str, to_date: str, db: AsyncSession,
    account_no: str = None,
) -> dict:
    """예탁원정보 액면교체일정 조회 (액면분할/병합)

    Args:
        user_id: KIS 토큰 컨텍스트 사용자
        from_date: 조회 시작일 (YYYYMMDD)
        to_date: 조회 종료일 (YYYYMMDD)
        db: AsyncSession

    Returns:
        KIS 응답 body (output1 배열 포함)
    """
    access_data = await _quote_auth(user_id, db, account_no)
    url = settings.REAL_API_URL
    path = "uapi/domestic-stock/v1/ksdinfo/rev-split"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHKDB669105C0")

    params = {
        "SHT_CD": "",
        "CTS": "",
        "F_DT": from_date,
        "T_DT": to_date,
        "MARKET_GB": "0",
    }
    response = await fetch("GET", api_url, "KIS", params=params, headers=headers)
    return response["body"]


async def get_merger_split_schedule(
    user_id: str, from_date: str, to_date: str, db: AsyncSession,
    account_no: str = None,
) -> dict:
    """예탁원정보 합병/분할일정 조회

    Args:
        user_id: KIS 토큰 컨텍스트 사용자
        from_date: 조회 시작일 (YYYYMMDD)
        to_date: 조회 종료일 (YYYYMMDD)
        db: AsyncSession

    Returns:
        KIS 응답 body (output1 배열 포함)
    """
    access_data = await _quote_auth(user_id, db, account_no)
    url = settings.REAL_API_URL
    path = "uapi/domestic-stock/v1/ksdinfo/merger-split"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHKDB669104C0")

    params = {
        "CTS": "",
        "F_DT": from_date,
        "T_DT": to_date,
        "SHT_CD": "",
    }
    response = await fetch("GET", api_url, "KIS", params=params, headers=headers)
    return response["body"]
