"""
전역 예외 핸들러

main.py에서 등록:
    from app.exceptions.handlers import register_exception_handlers
    register_exception_handlers(app)
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.exceptions.base import AppError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI):
    """
    전역 예외 핸들러 등록

    모든 AppError를 HTTP 응답으로 변환
    - 도메인 예외 → 4xx (400, 404, 409, 422)
    - 인증/인가 → 401, 403
    - 인프라 → 5xx (500, 502, 503)
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        """
        AppError 계열 통합 처리

        예외의 status_code, error_code를 활용해 HTTP 응답 생성
        """
        # 에러 로깅 (5xx는 ERROR, 4xx는 WARNING)
        log_method = logger.error if exc.status_code >= 500 else logger.warning
        log_method(
            f"[{exc.error_code}] {exc.message}",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status_code": exc.status_code,
                "error_code": exc.error_code,
                "detail": exc.detail
            },
            exc_info=exc.status_code >= 500  # 5xx만 스택 트레이스 출력
        )

        # 응답 생성
        response_body = {
            "success": False,
            "error_code": exc.error_code,
            "message": exc.message
        }

        # detail이 있으면 추가 (개발 환경에서 유용)
        if exc.detail is not None:
            response_body["detail"] = exc.detail

        return JSONResponse(
            status_code=exc.status_code,
            content=response_body
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        예상하지 못한 예외 처리 (폴백)

        AppError로 변환되지 않은 모든 예외를 500으로 처리
        """
        logger.error(
            "Unhandled exception",
            exc_info=exc,
            extra={
                "path": request.url.path,
                "method": request.method,
                "exception_type": type(exc).__name__
            }
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "서버 내부 오류가 발생했습니다"
            }
        )