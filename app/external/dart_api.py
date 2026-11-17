"""
DART OpenAPI (금융감독원 전자공시) — 국내 종목 재무 (KIS 국내 재무 TR 은 2026-11-11 종료됐다)

- 고유번호(corp_code) 매핑: corpCode.xml (zip). 상장사만 골라 Redis 에 7일 캐시한다.
- 단일회사 주요계정(fnlttSinglAcnt): 손익·재무상태. 분기·반기 보고서의 thstrm_amount 는 **그 분기 단독** 값,
  thstrm_add_amount 가 연 누적이다 (KIS 처럼 누적에서 빼지 않는다). 사업보고서는 당기·전기·전전기 3개년이 한 번에 온다.
- 단일회사 주요 재무지표(fnlttSinglIndx): 수익성·안정성·성장성. 분류 하나당 1회 호출. 2023년 3분기 이후 보고서만 있다.
- status 013 은 오류가 아니라 '해당 보고서 없음'이다 (미제출·제공 이전 기간).

키 노출 주의: DART 는 키를 URL 쿼리(crtfc_key=)로만 받는다. httpx 가 요청을 INFO 로그에 전체 URL 로 남기므로
로그 필터로 값을 가리고, 오류에는 URL 대신 상태·메시지만 담는다 (fred_api 와 같은 방식).
"""
import asyncio
import io
import logging
import re
import zipfile
from xml.etree import ElementTree

import httpx

from app.common.redis import get_redis
from app.core.config import get_settings
from app.exceptions import ConfigurationError, ExternalServiceError
from app.external.http_client import get_client

logger = logging.getLogger(__name__)
settings = get_settings()

_BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드 — 1분기·반기·3분기·사업
Q1, HALF, Q3, ANNUAL = "11013", "11012", "11014", "11011"

# 지표분류코드 (활동성 M240000 은 판단에 쓰지 않아 부르지 않는다)
INDICATOR_CLASSES = ("M210000", "M220000", "M230000")

_CORP_KEY = "dart:corp_codes"
_CORP_TTL = 7 * 24 * 3600

_STATUS_OK = "000"
_STATUS_NO_DATA = "013"

_KEY_PATTERN = re.compile(r"(crtfc_key=)[^&\s\"']+")


class _RedactKeyFilter(logging.Filter):
    """httpx 가 URL 째로 남기는 로그에서 crtfc_key 값을 가린다 (레코드 인자까지 문자열로 바꿔 치환)"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _KEY_PATTERN.sub(r"\1***", record.msg)
        if isinstance(record.args, dict):
            record.args = {k: (_KEY_PATTERN.sub(r"\1***", str(v)) if "crtfc_key=" in str(v) else v)
                           for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_KEY_PATTERN.sub(r"\1***", str(a)) if "crtfc_key=" in str(a) else a
                                for a in record.args)
        return True


def _install_log_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactKeyFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_RedactKeyFilter())


_install_log_filter()


def _key() -> str:
    if not settings.DART_API_KEY:
        raise ConfigurationError("DART_API_KEY가 설정되지 않았습니다", config_key="DART_API_KEY")
    return settings.DART_API_KEY


async def _get(path: str, params: dict) -> httpx.Response:
    """GET — 오류에는 URL(키가 들어 있다) 대신 종류만 담는다"""
    try:
        response = await get_client().get(f"{_BASE_URL}/{path}", params={"crtfc_key": _key(), **params})
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as e:
        raise ExternalServiceError(service="DART", message=f"HTTP {e.response.status_code}", status_code=502)
    except httpx.HTTPError as e:
        raise ExternalServiceError(service="DART", message=f"요청 실패 ({type(e).__name__})", status_code=504)


async def _get_json(path: str, params: dict) -> list[dict]:
    """JSON 엔드포인트 호출 → list 행. 013(없음)은 빈 목록, 그 밖의 비정상 status 는 오류"""
    try:
        body = (await _get(path, params)).json()
    except ValueError:
        raise ExternalServiceError(service="DART", message="JSON 이 아닌 응답", status_code=502)

    status = body.get("status")
    if status == _STATUS_NO_DATA:
        return []
    if status != _STATUS_OK:
        raise ExternalServiceError(
            service="DART", message=f"{status} {body.get('message')}", status_code=502,
            detail={"status": status},
        )
    return body.get("list") or []


def _parse_corp_codes(xml_bytes: bytes) -> dict[str, str]:
    """corpCode.xml → {종목코드: 고유번호} (비상장은 종목코드가 비어 있어 제외)"""
    mapping = {}
    for item in ElementTree.fromstring(xml_bytes).iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = (item.findtext("corp_code") or "").strip()
    return mapping


async def _load_corp_codes() -> dict[str, str]:
    response = await _get("corpCode.xml", {})
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        # zip 해제·XML 파싱은 CPU 작업이라 이벤트 루프를 막지 않게 스레드로 넘긴다
        return await asyncio.to_thread(_parse_corp_codes, archive.read(archive.namelist()[0]))
    except (zipfile.BadZipFile, ElementTree.ParseError, IndexError):
        # 키 오류 등은 zip 대신 JSON 오류 본문이 온다
        raise ExternalServiceError(service="DART", message="고유번호 파일을 읽지 못했습니다", status_code=502)


async def get_corp_code(stock_code: str) -> str | None:
    """종목코드 → DART 고유번호. 상장사가 아니거나 DART 에 없으면 None"""
    redis = await get_redis()
    if not await redis.exists(_CORP_KEY):
        mapping = await _load_corp_codes()
        await redis.hset(_CORP_KEY, mapping=mapping)
        await redis.expire(_CORP_KEY, _CORP_TTL)
        logger.info(f"[DART] 고유번호 {len(mapping)}건 적재")
    return await redis.hget(_CORP_KEY, stock_code)


async def get_accounts(corp_code: str, year: int, reprt_code: str) -> list[dict]:
    """단일회사 주요계정 — 연결(CFS) 우선, 연결 재무제표가 없는 회사는 개별(OFS). 보고서가 없으면 []"""
    rows = await _get_json("fnlttSinglAcnt.json",
                           {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code})
    consolidated = [r for r in rows if r.get("fs_div") == "CFS"]
    return consolidated or rows


async def get_indicators(corp_code: str, year: int, reprt_code: str) -> list[dict]:
    """단일회사 주요 재무지표 — 수익성·안정성·성장성 3개 분류를 이어서 부른다 (분류당 1회 호출)"""
    rows: list[dict] = []
    for idx_cl_code in INDICATOR_CLASSES:
        rows += await _get_json("fnlttSinglIndx.json", {
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": reprt_code, "idx_cl_code": idx_cl_code,
        })
    return rows


def report_candidates(today_year: int, count: int) -> list[tuple[int, str]]:
    """제출됐을 법한 보고서를 최신 순으로 (연도, 보고서코드) — 올해 3분기→반기→1분기, 작년 사업→3분기→…

    아직 제출 전인 보고서는 013 으로 비어 오므로 호출부가 건너뛴다.
    """
    result: list[tuple[int, str]] = []
    year = today_year
    while len(result) < count:
        result += [(year, Q3), (year, HALF), (year, Q1)]
        year -= 1
        result.append((year, ANNUAL))
    return result[:count]


# 분기 추이에 쓰는 보고서 수 / 아직 안 올라온 보고서를 건너뛰며 훑을 후보 수 (올해 3 + 작년 4 + …)
_QUARTERS = 5
_SCAN_CANDIDATES = 12


async def get_recent_reports(corp_code: str, today_year: int) -> dict:
    """최근 제출 보고서들의 주요계정과 최신 보고서의 재무지표

    Returns:
        {"reports": [{"year", "reprt_code", "rows"}, ...] (최신 먼저, 제출된 것만),
         "indicators": 최신 보고서의 지표 행 (보고서가 없으면 [])}
        사업보고서가 하나도 없으면 연간 실적용으로 가장 최근 것을 하나 더 가져온다.
    """
    reports: list[dict] = []
    for year, reprt_code in report_candidates(today_year, _SCAN_CANDIDATES):
        if len(reports) >= _QUARTERS:
            break
        rows = await get_accounts(corp_code, year, reprt_code)
        if rows:
            reports.append({"year": year, "reprt_code": reprt_code, "rows": rows})

    if reports and not any(r["reprt_code"] == ANNUAL for r in reports):
        year = today_year - 1
        for _ in range(3):  # 직전·전전 사업연도까지만 찾는다
            rows = await get_accounts(corp_code, year, ANNUAL)
            if rows:
                reports.append({"year": year, "reprt_code": ANNUAL, "rows": rows})
                break
            year -= 1

    indicators = await get_indicators(corp_code, reports[0]["year"], reports[0]["reprt_code"]) if reports else []
    return {"reports": reports, "indicators": indicators}
