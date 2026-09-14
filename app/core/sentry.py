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

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        before_send=_before_send,
    )

    logger.info(
        f"Sentry initialized: environment={settings.SENTRY_ENVIRONMENT}, "
        f"traces_sample_rate={settings.SENTRY_TRACES_SAMPLE_RATE}"
    )
