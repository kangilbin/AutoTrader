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

    # AES Encryption
    AES_SECRET_KEY: Optional[str] = None

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
