"""
예외 처리 통합 모듈

사용 가이드:
-----------
1. Service/Repository/Entity: 도메인 예외만 사용
   from app.exceptions import ValidationError, NotFoundError, BusinessRuleError

2. 외부 API 연동: 인프라 예외 사용
   from app.exceptions import ExternalServiceError

3. 인증/인가: auth 예외 사용
   from app.exceptions import AuthenticationError, AuthorizationError

4. 전역 핸들러(main.py): AppError 베이스로 일괄 처리
   @app.exception_handler(AppError)
"""

# 베이스
from app.exceptions.base import AppError

# 도메인 예외 (Service, Repository, Entity에서 사용)
from app.exceptions.domain import (
    DomainError,
    ValidationError,
    NotFoundError,
    DuplicateError,
    BusinessRuleError,
    PermissionDeniedError
)

# 인프라 예외 (Repository, External API에서 사용)
from app.exceptions.infrastructure import (
    InfrastructureError,
    ExternalServiceError,
    DatabaseError,
    CacheError,
    ConfigurationError
)

# 인증/인가 예외 (Router, Middleware, Dependency에서 사용)
from app.exceptions.auth import (
    AuthenticationError,
    AuthorizationError,
    DeviceNotAllowedError
)

__all__ = [
    # 베이스
    "AppError",

    # 도메인
    "DomainError",
    "ValidationError",
    "NotFoundError",
    "DuplicateError",
    "BusinessRuleError",
    "PermissionDeniedError",

    # 인프라
    "InfrastructureError",
    "ExternalServiceError",
    "DatabaseError",
    "CacheError",
    "ConfigurationError",

    # 인증/인가
    "AuthenticationError",
    "AuthorizationError",
    "DeviceNotAllowedError"
]