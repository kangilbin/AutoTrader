"""
Sentry 모니터링 초기화

SENTRY_DSN이 설정된 경우에만 활성화.
- 개발 환경 (로컬): SENTRY_DSN 미설정 -> 비활성화
- 운영 환경 (Docker): SENTRY_DSN 설정 -> 활성화
"""
import logging

from app.core.config import get_settings
from app.exceptions.base import AppError

logger = logging.getLogger(__name__)


def _before_send(event, hint):
    """4xx 성격의 AppError 는 Sentry 로 보내지 않는다.

    접근 거부·검증 실패는 클라이언트 문제이지 서버 장애가 아니다.
    공개 포트로 들어오는 스캐너 요청(/keyfile.json 등)이 대량으로 쿼터를 소모하는 것을 막는다.
    """
    exc_info = (hint or {}).get("exc_info")
    if exc_info:
        exc = exc_info[1]
        if isinstance(exc, AppError) and exc.status_code < 500:
            return None
    return event


def _kis_event_scrubber(scrubber_cls, default_denylist):
    """KIS 인증 정보를 Sentry 전송 전에 마스킹하는 스크러버

    예외가 나면 프레임의 지역변수가 이벤트에 실려 나간다. KIS 호출 경로에서는
    거기에 appkey/appsecret(헤더·토큰 발급 body)과 복호화된 api_key/secret_key,
    access_token 이 들어 있다. 실제로 토큰 발급 403 이벤트에 appkey 가 평문으로
    올라간 적이 있다.

    SDK 기본 스크러버로는 막히지 않는다 — 두 가지 이유다.
      1) 매칭이 완전일치다. 덴리스트의 'secret' 은 'appsecret' 에 걸리지 않고,
         'api_key' 는 있어도 KIS 가 쓰는 'appkey' 는 없다 (apikey 와 한 글자 차이).
      2) recursive 가 기본 False 라 kwargs→json→appkey 같은 중첩 dict 는
         탐색조차 하지 않는다.

    Sentry 서버측 스크러빙은 부분일치라 appsecret 을 가려주지만, 그건 데이터가
    이미 전송된 뒤의 일이다. 여기서 막아 애초에 내보내지 않는다.
    """
    return scrubber_cls(
        denylist=default_denylist + ["appkey", "appsecret", "secret_key", "access_token"],
        recursive=True,
    )


def init_sentry() -> None:
    """
    Sentry SDK 초기화

    SENTRY_DSN이 없으면 아무 동작도 하지 않음.
    sentry_sdk.init()은 DSN이 None이면 내부적으로 비활성화됨.
    """
    settings = get_settings()

    if not settings.SENTRY_DSN:
        logger.info("Sentry DSN not configured, skipping initialization")
        return

    import sentry_sdk
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        before_send=_before_send,
        event_scrubber=_kis_event_scrubber(EventScrubber, DEFAULT_DENYLIST),
    )

    logger.info(
        f"Sentry initialized: environment={settings.SENTRY_ENVIRONMENT}, "
        f"traces_sample_rate={settings.SENTRY_TRACES_SAMPLE_RATE}"
    )
