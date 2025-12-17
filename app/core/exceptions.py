"""
표준 예외 클래스 정의
"""
from fastapi import HTTPException, status
from typing import Any, Optional


class AppException(HTTPException):
    """애플리케이션 기본 예외"""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: Optional[str] = None,
        headers: Optional[dict] = None
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code


class NotFoundException(AppException):
    """리소스를 찾을 수 없음 (404)"""

    def __init__(self, resource: str, identifier: Any = None):
        detail = f"{resource}을(를) 찾을 수 없습니다"
        if identifier:
            detail += f": {identifier}"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            error_code="NOT_FOUND"
        )


class DuplicateException(AppException):
    """중복 리소스 (409)"""

    def __init__(self, resource: str, identifier: Any = None):
        detail = f"이미 존재하는 {resource}입니다"
        if identifier:
            detail += f": {identifier}"
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="DUPLICATE"
        )


class UnauthorizedException(AppException):
    """인증 실패 (401)"""

    def __init__(self, detail: str = "인증이 필요합니다"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            error_code="UNAUTHORIZED",
            headers={"WWW-Authenticate": "Bearer"}
        )


class ForbiddenException(AppException):
    """권한 없음 (403)"""

    def __init__(self, detail: str = "접근 권한이 없습니다"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="FORBIDDEN"
        )


class ValidationException(AppException):
    """유효성 검증 실패 (422)"""

    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            error_code="VALIDATION_ERROR"
        )


class ExternalAPIException(AppException):
    """외부 API 호출 실패 (502)"""

    def __init__(self, service: str, detail: str):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{service} API 오류: {detail}",
            error_code="EXTERNAL_API_ERROR"
        )


class BusinessException(AppException):
    """비즈니스 로직 예외 (400)"""

    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            error_code="BUSINESS_ERROR"
        )
