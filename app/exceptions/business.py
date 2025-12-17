"""
표준 예외 클래스
"""
from fastapi import HTTPException, status
from typing import Optional, Any

# ... existing code ...


# ============================================================
# HTTP 비의존(도메인/서버) 예외 - FastAPI/HTTPException에 의존하지 않음
# ============================================================

class BizException(Exception):
    """
    HTTP 비의존 예외의 베이스.
    - main.py(HTTP 경계)에서만 status_code를 HTTP 응답으로 변환한다.
    """
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        detail: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail


class ConfigurationError(BizException):
    """서버 설정/환경 변수/키 포맷 오류 등 (보통 500)"""
    def __init__(self, message: str, *, detail: Any = None):
        super().__init__(
            message,
            error_code="CONFIGURATION_ERROR",
            status_code=500,
            detail=detail,
        )

