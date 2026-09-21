import asyncio
import logging
import random

import httpx
from app.core.config import get_settings
from app.exceptions import ExternalServiceError
from app.external.rate_limiter import kis_rate_limiter

logger = logging.getLogger(__name__)

RATE_LIMIT_MSG = "초당 거래건수를 초과"
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_DELAY = 1.0
# 재시도 지터. 동시에 유량을 초과한 요청들이 고정 간격으로 재시도하면 같은 순간에
# 다시 몰려 또 초과한다 (배치가 스윙 N건을 동시에 돌리므로 실제로 발생).
RATE_LIMIT_JITTER = 0.5

# 연결 수립 실패 재시도. 요청이 전송되기 전에 끊긴 것이라 중복 부작용이 없다.
MAX_CONNECT_RETRIES = 2
CONNECT_RETRY_DELAY = 0.5

# 전체 10초, 연결 수립 10초. connect 를 5초로 두었더니 배치 시작 직후
# 여러 연결을 동시에 맺는 구간에서 KIS 가 느려질 때마다 타임아웃이 났다.
_TIMEOUT = httpx.Timeout(10.0, connect=10.0)

# 커넥션 재사용 수명. httpx 기본값 5초는 배치 주기(5분)보다 짧아, 배치가 끝나고
# 5초 뒤 연결이 전부 닫혔다. 다음 배치는 매번 빈 풀에서 시작해 핸드셰이크를
# 여러 개 동시에 열었고, 그 순간 KIS 가 느리면 connect 타임아웃이 났다.
# 수명을 배치 간격보다 길게 두면 두 번째 배치부터는 핸드셰이크 자체가 없다.
# 연결 수 상한(max_connections)은 기본값을 유지한다 — 한도 축은 앱키이고,
# 사용자가 늘면 앱키별로 연결이 필요하므로 여기를 조이면 처리량이 깎인다.
_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
    keepalive_expiry=600.0,
)
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """공용 HTTP 클라이언트 (커넥션·TLS 세션 재사용)

    요청마다 AsyncClient를 새로 만들면 매번 TLS 핸드셰이크를 다시 한다. 유량 제한이
    빡빡한 KIS에서는 그 지연이 타임아웃과 겹쳐 실패율을 끌어올린다.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS)
    return _client


async def close_client() -> None:
    """앱 종료 시 커넥션 정리"""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _throttle(url: str, kwargs: dict) -> None:
    """KIS 유량 제한 — 앱키 단위로 호출 간격을 벌린다

    KIS 한도는 앱키 기준이라 전역 제한은 축이 틀리다. 헤더의 appkey를 키로 쓰고,
    한도는 대상 서버(모의/실전)로 판단한다. appkey가 없는 호출(토큰 발급 등)은
    별도 제한(1분 1회)을 받으므로 여기서는 건드리지 않는다.
    """
    headers = kwargs.get("headers") or {}
    appkey = headers.get("appkey")
    if not appkey:
        return

    settings = get_settings()
    is_simulation = url.startswith(settings.DEV_API_URL)
    rate = settings.KIS_RATE_LIMIT_SIM if is_simulation else settings.KIS_RATE_LIMIT_REAL
    await kis_rate_limiter.acquire(appkey, rate)


async def fetch(method: str, url: str, service_name: str = "External API", **kwargs):
    method = method.upper()

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            await _throttle(url, kwargs)
            response = await get_client().request(method, url, **kwargs)
            response.raise_for_status() # 4xx/5xx는 예외로 처리

            try:
                return {
                    "body": response.json(),
                    "header": dict(response.headers),
                }
            except ValueError as e:
                raise ExternalServiceError(
                    service=service_name,
                    message="응답이 JSON 형식이 아닙니다",
                    status_code=502,
                    original_error=e,
                    detail={
                        "url": url,
                        "method": method,
                        "status_code": response.status_code,
                        "response_text": response.text,
                    },
                )

        except httpx.ConnectTimeout as e:
            # 연결 수립 단계에서 끊긴 것 = 요청이 서버에 전송된 적이 없다.
            # 그래서 주문(POST)이라도 재시도에 중복 위험이 없다.
            # ReadTimeout 은 요청이 도달했을 수 있어 아래에서 재시도 없이 올린다.
            # (ConnectTimeout 은 TimeoutException 의 하위 클래스라 이 절이 먼저 와야 한다)
            if attempt < MAX_CONNECT_RETRIES:
                delay = CONNECT_RETRY_DELAY + random.uniform(0, RATE_LIMIT_JITTER)
                logger.warning(
                    f"[{service_name}] 연결 수립 실패, {delay:.1f}초 후 재시도 "
                    f"({attempt + 1}/{MAX_CONNECT_RETRIES})"
                )
                await asyncio.sleep(delay)
                continue

            raise ExternalServiceError(
                service=service_name,
                message=f"요청 시간 초과 ({type(e).__name__})",
                status_code=504,
                original_error=e
            )
        except httpx.TimeoutException as e:
            # 어느 단계에서 끊겼는지 남긴다. connect/read/write/pool 이 제한 시간도
            # 원인도 달라, 종류를 모르면 로그만으로 진단이 안 된다.
            raise ExternalServiceError(
                service=service_name,
                message=f"요청 시간 초과 ({type(e).__name__})",
                status_code=504,
                original_error=e
            )
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
            except ValueError:
                body = {}

            # KIS 일반 API: msg1 / KIS OAuth: error_description, error_code
            error_msg = (
                body.get('error_description')
                or body.get('error_code')
                or body.get('msg1')
                or e.response.text
                or f"HTTP {e.response.status_code}"
            )

            # 초당 거래건수 초과 시 재시도
            if RATE_LIMIT_MSG in error_msg and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = RATE_LIMIT_DELAY * (attempt + 1) + random.uniform(0, RATE_LIMIT_JITTER)
                logger.debug(f"[{service_name}] 초당 거래건수 초과, {delay:.1f}초 후 재시도 ({attempt + 1}/{MAX_RATE_LIMIT_RETRIES})")
                await asyncio.sleep(delay)
                continue

            raise ExternalServiceError(
                service=service_name,
                message=error_msg,
                status_code=502,
                original_error=e,
                detail={
                    "url": str(e.request.url),
                    "method": method,
                    "status_code": e.response.status_code,
                    "response_text": e.response.text,
                },
            )
        except httpx.RequestError as e:
            raise ExternalServiceError(
                service=service_name,
                message=f"요청 실패: {str(e)}",
                status_code=503,
                original_error=e
            )