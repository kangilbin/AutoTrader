"""
인프라 예외 - 외부 시스템/의존성 오류

Repository, External API 호출 시 사용
전역 핸들러에서 5xx HTTP 응답으로 변환
"""
from typing import Any, Optional
from app.exceptions.base import AppError


class InfrastructureError(AppError):
    """인프라 예외 베이스"""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "INFRASTRUCTURE_ERROR",
        status_code: int = 500,
        detail: Optional[Any] = None
    ):
        super().__init__(
            message,
            error_code=error_code,
            status_code=status_code,
            detail=detail
        )


class ExternalServiceError(InfrastructureError):
    """
    외부 API 호출 실패

    사용처: KIS API, 기타 외부 서비스 연동
    HTTP: 502 Bad Gateway / 503 Service Unavailable / 504 Gateway Timeout

    예시:
    - KIS API 응답 오류
    - 타임아웃
    - 네트워크 오류
    """

    def __init__(
        self,
        service: str,
        message: str,
        *,
        status_code: int = 502,
        original_error: Optional[Exception] = None,
        detail: Optional[Any] = None
    ):
        super().__init__(
            f"{service} 연동 오류: {message}",
            error_code="EXTERNAL_SERVICE_ERROR",
            status_code=status_code,
            detail=detail or {"service": service, "original_error": str(original_error) if original_error else None}
        )
        self.service = service
        self.original_error = original_error


class DatabaseError(InfrastructureError):
    """
    데이터베이스 오류

    사용처: Repository에서 DB 작업 실패
    HTTP: 500 Internal Server Error

    예시:
    - 연결 실패
    - 트랜잭션 오류
    - 쿼리 타임아웃
    """

    def __init__(
        self,
        message: str,
        *,
        operation: Optional[str] = None,
        original_error: Optional[Exception] = None,
        detail: Optional[Any] = None
    ):
        super().__init__(
            f"데이터베이스 오류: {message}",
            error_code="DATABASE_ERROR",
            status_code=500,
            detail=detail or {"operation": operation, "original_error": str(original_error) if original_error else None}
        )
        self.operation = operation
        self.original_error = original_error


class CacheError(InfrastructureError):
    """
    캐시(Redis) 오류

    사용처: Redis 연결/작업 실패
    HTTP: 500 Internal Server Error (비필수 기능이면 로깅만 하고 무시)

    예시:
    - Redis 연결 실패
    - 키 만료 실패
    """

    def __init__(
        self,
        message: str,
        *,
        key: Optional[str] = None,
        original_error: Optional[Exception] = None,
        detail: Optional[Any] = None
    ):
        super().__init__(
            f"캐시 오류: {message}",
            error_code="CACHE_ERROR",
            status_code=500,
            detail=detail or {"key": key, "original_error": str(original_error) if original_error else None}
        )
        self.key = key
        self.original_error = original_error


class ConfigurationError(InfrastructureError):
    """
    설정/환경 변수 오류

    사용처: 앱 시작 시 설정 검증
    HTTP: 500 Internal Server Error

    예시:
    - 필수 환경 변수 누락
    - 잘못된 설정 값
    - AES 키 포맷 오류
    """

    def __init__(self, message: str, *, config_key: Optional[str] = None, detail: Optional[Any] = None):
        super().__init__(
            f"설정 오류: {message}",
            error_code="CONFIGURATION_ERROR",
            status_code=500,
            detail=detail or {"config_key": config_key} if config_key else None
        )
        self.config_key = config_key