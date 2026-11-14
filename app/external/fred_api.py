"""
FRED — 미국 하이일드 스프레드(ICE BofA US High Yield OAS, BAMLH0A0HYM2) 일별 값

두 경로를 지원한다.
- FRED_API_KEY 가 있으면 공식 JSON API(api.stlouisfed.org/fred/series/observations)
- 없으면 인증키가 필요 없는 CSV 엔드포인트(fredgraph.csv) — http_client.fetch 는 JSON 만 파싱하므로 여기서 직접 부른다
FRED 는 이 시리즈를 최근 3년치만 제공한다(ICE 라이선스). 진입 게이트 판정에는 직전 6개 값이면 충분하다.
휴장일은 값이 "." 로 내려오며, 그 날짜는 건너뛴다.

키 노출 주의: FRED API 는 키를 URL 쿼리(api_key=)로만 받는다. httpx 는 요청을 INFO 로그에 전체 URL 로 남기고
예외 메시지에도 URL 이 들어가므로, 로그 필터로 api_key 값을 가리고 오류에는 URL 대신 상태 코드만 담는다.
"""
import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings
from app.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_API_URL = "https://api.stlouisfed.org/fred/series/observations"
_TIMEOUT_SEC = 20.0
_KEY_PATTERN = re.compile(r"(api_key=)[^&\s\"']+")


class _RedactApiKeyFilter(logging.Filter):
    """httpx 가 URL 째로 남기는 로그에서 api_key 값을 가린다 (레코드 인자까지 문자열로 바꿔 치환)"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _KEY_PATTERN.sub(r"\1***", record.msg)
        if isinstance(record.args, dict):
            record.args = {k: (_KEY_PATTERN.sub(r"\1***", str(v)) if "api_key=" in str(v) else v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_KEY_PATTERN.sub(r"\1***", str(a)) if "api_key=" in str(a) else a for a in record.args)
        return True


def _install_log_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactApiKeyFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_RedactApiKeyFilter())


_install_log_filter()


def parse_csv(text: str) -> List[Tuple[date, float]]:
    """fredgraph.csv 본문 → [(날짜, 값), ...] (날짜 오름차순, 값 없는 날 제외)

    첫 줄은 헤더(observation_date, 시리즈 ID)다. 날짜·값을 읽을 수 없는 줄은 건너뛰고,
    읽을 수 있는 줄이 하나도 없으면 호출부가 오류로 다룰 수 있게 빈 목록을 돌려준다.
    """
    rows: List[Tuple[date, float]] = []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            rows.append((datetime.strptime(row[0], "%Y-%m-%d").date(), float(row[1])))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def parse_json(body: dict) -> List[Tuple[date, float]]:
    """공식 API 응답 {"observations": [{"date": "YYYY-MM-DD", "value": "3.30"}, ...]} → [(날짜, 값), ...] (오름차순, "." 제외)"""
    rows: List[Tuple[date, float]] = []
    for obs in (body or {}).get("observations", []) or []:
        value = obs.get("value")
        if value in (None, "", "."):
            continue
        try:
            rows.append((datetime.strptime(obs["date"], "%Y-%m-%d").date(), float(value)))
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def _api_key() -> Optional[str]:
    key = get_settings().FRED_API_KEY
    return key.strip() if key and key.strip() else None


async def get_series(series_id: str, lookback_days: int = 60) -> List[Tuple[date, float]]:
    """최근 lookback_days 일 구간의 일별 값 — 실패하거나 값이 하나도 없으면 ExternalServiceError

    오류 메시지에는 URL(키 포함)을 넣지 않는다. 상태 코드·예외 종류만 남긴다.
    """
    # FRED 날짜는 미국 기준이다 — 서버 시간대와 무관하게 ET 날짜로 구간을 잡는다
    start = (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=lookback_days)).isoformat()
    key = _api_key()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            if key:
                response = await client.get(_API_URL, params={
                    "series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start})
            else:
                response = await client.get(_CSV_URL, params={"id": series_id, "cosd": start})
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ExternalServiceError("FRED", f"{series_id} 조회 실패: HTTP {e.response.status_code}", status_code=502) from None
    except httpx.HTTPError as e:
        raise ExternalServiceError("FRED", f"{series_id} 조회 실패: {type(e).__name__}", status_code=502) from None

    try:
        rows = parse_json(response.json()) if key else parse_csv(response.text)
    except ValueError:
        rows = []
    if not rows:
        raise ExternalServiceError("FRED", f"{series_id} 응답에 읽을 수 있는 값이 없습니다")
    return rows
