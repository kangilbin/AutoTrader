"""
예외 처리 베이스 - HTTP 비의존
모든 커스텀 예외는 이 베이스를 상속
"""
from typing import Any, Optional


class AppError(Exception):
    """
    애플리케이션 예외 베이스 클래스

    - HTTP 비의존 (순수 Exception 상속)
    - status_code, error_code는 전역 핸들러에서 HTTP 응답 변환용 메타데이터
    - Service, Repository, Entity 등 모든 계층에서 사용 가능
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "APP_ERROR",
        status_code: int = 500,
        detail: Optional[Any] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail

    def __str__(self):
        return f"[{self.error_code}] {self.message}"

    def __repr__(self):
        return f"{self.__class__.__name__}(message={self.message!r}, error_code={self.error_code!r})"