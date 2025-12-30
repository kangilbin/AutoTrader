"""
인증/인가 예외

Router, Middleware, Dependency에서 사용
전역 핸들러에서 401/403 HTTP 응답으로 변환
"""
from typing import Any, Optional
from app.exceptions.base import AppError


class AuthenticationError(AppError):
    """
    인증 실패

    사용처:
    - JWT 토큰 검증 실패
    - 로그인 실패
    - 만료된 토큰

    HTTP: 401 Unauthorized
    """

    def __init__(
        self,
        message: str = "인증에 실패했습니다",
        *,
        reason: Optional[str] = None,
        detail: Optional[Any] = None
    ):
        super().__init__(
            message,
            error_code="AUTHENTICATION_FAILED",
            status_code=401,
            detail=detail or {"reason": reason} if reason else None
        )
        self.reason = reason


class AuthorizationError(AppError):
    """
    권한 없음 (인가 실패)

    사용처:
    - 필요한 권한 부족
    - 역할(Role) 부족

    HTTP: 403 Forbidden

    참고: 도메인 레벨 권한(소유권)은 domain.PermissionDeniedError 사용
    """

    def __init__(
        self,
        message: str = "접근 권한이 없습니다",
        *,
        required_role: Optional[str] = None,
        detail: Optional[Any] = None
    ):
        super().__init__(
            message,
            error_code="AUTHORIZATION_FAILED",
            status_code=403,
            detail=detail or {"required_role": required_role} if required_role else None
        )
        self.required_role = required_role