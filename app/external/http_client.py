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

# 전송 계층 재시도(연결 수립 실패 / 연결 끊김 / 응답 무응답).
# 유량초과 재시도와 예산을 따로 센다 — 하나의 카운터로 세면 유량초과로 이미
# 재시도한 요청이 그 뒤 타임아웃을 만났을 때 남은 기회가 0이 되어, 정작 복구
# 가능한 실패를 재시도 한 번 없이 올려버린다 (배치가 동시 5건을 돌려 유량초과가
# 실제로 발생하는 경로라 그냥 이론상의 조합이 아니다).
MAX_TRANSPORT_RETRIES = 2
TRANSPORT_RETRY_DELAY = 0.5

# ReadTimeout 재시도는 예산을 더 좁게 쓴다. 재시도 1회가 read 제한시간(20초)을
# 통째로 더 쓰므로, 연결 재시도와 같은 횟수를 주면 한 종목이 60초 넘게 세마포어
# 슬롯을 물고 있어 배치 주기(5분) 안에 나머지 종목 판단이 밀린다.
MAX_READ_RETRIES = 1

# 이 시간을 넘긴 응답은 성공했어도 경고로 남긴다.
# ReadTimeout 이 '영영 안 오는 응답'인지 '느린 응답의 꼬리'인지는 성공한 호출의
# 소요 시간 분포를 봐야 갈린다. 그 분포가 없으면 재시도를 늘려야 할지 커넥션
# 수명(keepalive_expiry)을 줄여야 할지 근거 없이 고르게 된다.
SLOW_RESPONSE_SEC = 3.0

# 응답 대기 20초, 연결 수립 10초.
# connect: 5초로 두었더니 배치 시작 직후 여러 연결을 동시에 맺는 구간에서
#   KIS 가 느려질 때마다 타임아웃이 났다.
# read: 10초는 여유가 없다 — 유량이 몰릴 때 KIS 응답이 6.8초까지 관측됐다.
#   대기를 넉넉히 두는 이유는 주문(POST) 때문이다. 주문의 ReadTimeout 은
#   '체결됐는지 모르는 상태'를 만들고, 그 상태가 체결확인 실패 → 취소 실패 →
#   추적 불가로 이어진다. POST 는 재시도가 중복 주문이 되어 쓸 수 없으므로,
#   응답을 받아내는 것이 그 연쇄를 막는 유일하게 싼 방법이다.
#   조회(GET)는 멱등이라 대기에 더해 재시도도 쓴다 (fetch 의 ReadTimeout 절).
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

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


def _appkey(kwargs: dict) -> str | None:
    """유량 한도의 축이 되는 앱키.

    없으면(토큰 발급 등) 별도 제한(1분 1회)을 받으므로 여기서는 대상이 아니다.
    """
    return (kwargs.get("headers") or {}).get("appkey")


async def _throttle(url: str, kwargs: dict) -> None:
    """KIS 유량 제한 — 앱키 단위로 호출 간격을 벌린다

    KIS 한도는 앱키 기준이라 전역 제한은 축이 틀리다. 헤더의 appkey를 키로 쓰고,
    한도는 대상 서버(모의/실전)로 판단한다. appkey가 없는 호출(토큰 발급 등)은
    별도 제한(1분 1회)을 받으므로 여기서는 건드리지 않는다.
    """
    appkey = _appkey(kwargs)
    if not appkey:
        return

    settings = get_settings()
    is_simulation = url.startswith(settings.DEV_API_URL)
    limit = settings.KIS_RATE_LIMIT_SIM if is_simulation else settings.KIS_RATE_LIMIT_REAL
    await kis_rate_limiter.acquire(appkey, limit)


async def fetch(method: str, url: str, service_name: str = "External API", **kwargs):
    method = method.upper()

    # 재시도 예산은 원인별로 따로 센다 (상수 주석 참고).
    rate_limit_retries = 0
    transport_retries = 0

    # 반복 상한 = 두 예산의 합 + 최초 시도. 모든 continue 가 둘 중 한 카운터를
    # 올리므로 실제로는 상한에 닿기 전에 반환하거나 예외가 오른다. 상한을 명시해
    # 두는 건 루프가 조용히 끝나 None 이 반환되는 경로를 원천 차단하기 위함이다 —
    # 호출부는 전부 response["body"] 를 바로 꺼내므로 None 은 엉뚱한 곳에서 터진다.
    for _ in range(MAX_RATE_LIMIT_RETRIES + MAX_TRANSPORT_RETRIES + 1):
        try:
            await _throttle(url, kwargs)
            response = await get_client().request(method, url, **kwargs)

            # 느린 응답 계측. raise_for_status 앞에 두어 느린 4xx/5xx 도 잡는다.
            elapsed = response.elapsed.total_seconds()
            if elapsed >= SLOW_RESPONSE_SEC:
                logger.warning(
                    f"[{service_name}] 응답 지연 {elapsed:.1f}초 - {method} {response.url.path}"
                )

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
            # (ConnectTimeout 은 TimeoutException 의 하위 클래스라 이 절이 먼저 와야 한다)
            if transport_retries < MAX_TRANSPORT_RETRIES:
                transport_retries += 1
                delay = TRANSPORT_RETRY_DELAY + random.uniform(0, RATE_LIMIT_JITTER)
                logger.warning(
                    f"[{service_name}] 연결 수립 실패, {delay:.1f}초 후 재시도 "
                    f"({transport_retries}/{MAX_TRANSPORT_RETRIES})"
                )
                await asyncio.sleep(delay)
                continue

            raise ExternalServiceError(
                service=service_name,
                message=f"요청 시간 초과 ({type(e).__name__})",
                status_code=504,
                original_error=e
            )
        except httpx.ReadTimeout as e:
            # 요청은 나갔는데 제한 시간 안에 응답 헤더가 한 바이트도 안 온 경우.
            # 피어가 FIN 없이 끊어 죽은 연결에 요청이 빨려 들어간 경우와, KIS 가
            # 정말 느린 경우가 같은 증상을 낸다. 어느 쪽이든 답은 재시도다 —
            # httpcore 는 타임아웃 시 _response_closed() 로 그 연결을 닫고 풀에서
            # 버리므로(_async/http11.py), 재시도는 반드시 다른 연결로 나간다.
            #
            # GET 만 재시도한다. POST(주문)는 서버가 이미 접수했을 수 있어
            # 재시도하면 중복 주문이 된다 — 미확인 주문은 order_executor 의
            # 체결 재확인이 맡는다.
            # 예산이 MAX_TRANSPORT_RETRIES 가 아니라 MAX_READ_RETRIES 인 이유는
            # 재시도 1회가 read 제한시간을 통째로 더 쓰기 때문이다 (상수 주석 참고).
            if method == "GET" and transport_retries < MAX_READ_RETRIES:
                transport_retries += 1
                delay = TRANSPORT_RETRY_DELAY + random.uniform(0, RATE_LIMIT_JITTER)
                logger.warning(
                    f"[{service_name}] 응답 없음(ReadTimeout), {delay:.1f}초 후 재시도 "
                    f"({transport_retries}/{MAX_READ_RETRIES})"
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
            # 남은 종류(Write/Pool). 어느 단계에서 끊겼는지 남긴다 — 제한 시간도
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

            # 초당 거래건수 초과
            if RATE_LIMIT_MSG in error_msg:
                delay = RATE_LIMIT_DELAY * (rate_limit_retries + 1) + random.uniform(0, RATE_LIMIT_JITTER)

                # 초과 신호를 제한기에 되먹인다. 이 코루틴만 쉬면 같은 앱키를 쓰는
                # 다른 호출이 그 사이에도 계속 나가 집계 속도가 안 떨어지고, 재시도가
                # 연달아 또 초과당한다 (백오프가 '누가 거절당하는지'만 바꾼다).
                # 재시도 예산을 다 썼을 때도 거는 이유: 이 요청은 포기해도 같은 배치의
                # 나머지 종목은 계속 호출하므로 초과 신호는 그쪽에 더 필요하다.
                kis_rate_limiter.penalize(_appkey(kwargs), delay)

                # 창 계산이 맞다면 이 경로는 안 타는 것이 정상이라 warning 으로 남긴다.
                # 찍히기 시작하면 창 폭이나 한도 설정이 실제와 어긋났다는 신호다.
                if rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                    rate_limit_retries += 1
                    logger.warning(
                        f"[{service_name}] 초당 거래건수 초과, {delay:.1f}초 후 재시도 "
                        f"({rate_limit_retries}/{MAX_RATE_LIMIT_RETRIES})"
                    )
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
            # 재사용하려던 연결을 서버가 이미 닫은 경우(RemoteProtocolError:
            # "Server disconnected without sending a response")가 대부분이다.
            # 풀은 FIN 을 못 보고 살아 있는 연결로 들고 있다가 요청을 보내고 끊긴다.
            # 다음 시도는 풀이 그 연결을 버리고 새로 맺으므로 대개 성공한다.
            #
            # GET 만 재시도한다. ConnectTimeout 과 달리 요청이 전송된 뒤 끊긴 것이라,
            # POST(주문)는 서버가 이미 접수했을 수 있고 재시도하면 중복 주문이 된다.
            # 주문의 미확인 상태는 order_executor 의 체결 재확인이 맡는다.
            if method == "GET" and transport_retries < MAX_TRANSPORT_RETRIES:
                transport_retries += 1
                delay = TRANSPORT_RETRY_DELAY + random.uniform(0, RATE_LIMIT_JITTER)
                logger.warning(
                    f"[{service_name}] 연결 끊김({type(e).__name__}), {delay:.1f}초 후 재시도 "
                    f"({transport_retries}/{MAX_TRANSPORT_RETRIES})"
                )
                await asyncio.sleep(delay)
                continue

            raise ExternalServiceError(
                service=service_name,
                message=f"요청 실패: {str(e)}",
                status_code=503,
                original_error=e
            )

    # 도달 불가(위 상한 주석 참고). 방어적으로 남긴다 — None 반환만은 막는다.
    raise ExternalServiceError(
        service=service_name,
        message="재시도 예산 소진",
        status_code=504,
    )
