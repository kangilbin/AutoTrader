"""
환경 설정 관리 - Pydantic Settings 기반
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Optional
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """애플리케이션 설정"""

    # Database
    DATABASE_URL: str
    DB_ECHO: bool = False  # SQL 로깅 (프로덕션에서는 False)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: Optional[str] = None
    REDIS_MAX_CONNECTIONS: int = 10

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # KIS API
    DEV_API_URL: str = "https://openapivts.koreainvestment.com:29443"
    REAL_API_URL: str = "https://openapi.koreainvestment.com:9443"

    # KIS 유량 제한 — **리미터의 슬라이딩 창 안에서 허용할 호출 건수**.
    # 초과하면 KIS가 즉시 거절하지 않고 응답을 수 초간 지연시킨 뒤 500을 주므로,
    # 타임아웃과 겹쳐 배치가 통째로 실패한다.
    #
    # KIS 한도를 그대로 적는다. 안전 마진은 이 값을 깎아서가 아니라 리미터가 KIS의
    # 1초보다 넓은 창(rate_limiter.WINDOW_SEC)으로 세는 것으로 확보한다 — 값을 깎으면
    # 지터 방어는 안 되면서 KIS가 허용하는 버스트만 막혀 사용자 응답이 느려진다.
    # (자세한 근거는 rate_limiter.py 모듈 주석)
    #
    # 실전: KIS 한도 20건/초 대비 13으로 둔다. 창이 1.3초이므로 지속 처리량은
    # 13/1.3 = 10건/초 — 간격 방식이던 이전과 같은 속도다. 전 종목을 도는
    # day_collect_job 이 이 속도에 직접 묶여 있어 낮추면 수집이 그만큼 길어진다.
    # 버스트 13건이 KIS 의 한 창에 다 들어가도 한도 20건 이내라 안전하다.
    KIS_RATE_LIMIT_REAL: float = 13.0
    KIS_RATE_LIMIT_SIM: float = 2.0

    # 시스템 배치 잡 사용자 ID (KIS 토큰 발급 컨텍스트)
    BATCH_USER_ID: Optional[str] = None

    # 정규장 시간 밖에도 매매 배치를 실행할지 (모의투자 테스트 전용)
    # 프리마켓/휴장 시세는 거래량이 없어 지표가 왜곡되므로 운영에서는 반드시 False
    ALLOW_OFFHOURS_TRADING: bool = False

    # 해외 매매 배치 실행 주기 (ET 기준 cron). 기본값이 운영값이며,
    # 테스트로 더 자주/넓게 돌리려면 .env에서 덮어쓴다 (예: "*/1", "1-23")
    # ※ 분할 체결(TWAP)이 "5분 사이클당 한 chunk" 전제로 동작하므로 주기 변경 시 분할 속도도 함께 바뀐다
    US_TRADE_CRON_MINUTE: str = "*/5"
    US_TRADE_CRON_HOUR: str = "10-15"

    # AES Encryption
    AES_SECRET_KEY: Optional[str] = None

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None

    # Email
    SMTP_HOST: str = "smtp.naver.com"  # Gmail SMTP 서버
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None  # 발신자 이메일 (환경변수에서 설정)
    SMTP_PASSWORD: Optional[str] = None  # 앱 비밀번호 (환경변수에서 설정)
    ADMIN_EMAIL: str = "kib3388@naver.com"  # 관리자 이메일

    # Sentry
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "development"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # App
    DEBUG: bool = False
    APP_NAME: str = "AutoTrader"
    APP_VERSION: str = "1.0.0"

    @property
    def token_access_exp(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)

    @property
    def token_refresh_exp(self) -> timedelta:
        return timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # .env에 정의되지 않은 변수 무시
    )


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글톤 반환 (캐싱)"""
    return Settings()
