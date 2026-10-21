"""
Finnhub API — 미국 종목 재무 지표 (KIS 해외 TR 에는 재무제표가 없다)

무료 등급 분당 60회. 종목 리뷰 1건에 2회(metric, earnings)를 쓴다.
키는 URL 쿼리가 아니라 헤더로 보낸다 — URL 은 로그·에러 메시지에 남을 수 있다.
"""
from app.core.config import get_settings
from app.exceptions import ConfigurationError
from app.external.http_client import fetch

settings = get_settings()

_BASE_URL = "https://finnhub.io/api/v1"


def _headers() -> dict:
    if not settings.FINNHUB_API_KEY:
        raise ConfigurationError("FINNHUB_API_KEY가 설정되지 않았습니다", config_key="FINNHUB_API_KEY")
    return {"X-Finnhub-Token": settings.FINNHUB_API_KEY}


async def get_metrics(symbol: str) -> dict:
    """기본 재무 지표 — {"metric": {TTM·연간 지표}, "series": {"quarterly": {지표: [{period, v}, ...]}}}

    series.quarterly 는 개별 분기 값이다 (KIS 국내 재무 TR 처럼 연 누적이 아니다).
    """
    response = await fetch("GET", f"{_BASE_URL}/stock/metric", "Finnhub",
                           params={"symbol": symbol, "metric": "all"}, headers=_headers())
    return response["body"] or {}


async def get_earnings(symbol: str) -> list:
    """분기 실적 서프라이즈 — [{period, estimate, actual, surprisePercent, ...}] (최신 먼저)"""
    response = await fetch("GET", f"{_BASE_URL}/stock/earnings", "Finnhub",
                           params={"symbol": symbol}, headers=_headers())
    body = response["body"]
    return body if isinstance(body, list) else []
