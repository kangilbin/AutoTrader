"""
Sentry 모니터링 초기화

SENTRY_DSN이 설정된 경우에만 활성화.
- 개발 환경 (로컬): SENTRY_DSN 미설정 -> 비활성화
- 운영 환경 (Docker): SENTRY_DSN 설정 -> 활성화
"""
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)


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
    )

    logger.info(
        f"Sentry initialized: environment={settings.SENTRY_ENVIRONMENT}, "
        f"traces_sample_rate={settings.SENTRY_TRACES_SAMPLE_RATE}"
    )
