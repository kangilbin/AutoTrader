"""
전역 예외 핸들러

main.py에서 등록:
    from app.exceptions.handlers import register_exception_handlers
    register_exception_handlers(app)
"""
import logging
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.exceptions.base import AppError

logger = logging.getLogger(__name__)


def app_error_response(request: Request, exc: AppError) -> JSONResponse:
    """AppError → JSONResponse 변환 (로깅 + Sentry 전송 정책 포함)

    미들웨어(BaseHTTPMiddleware)는 FastAPI 의 ExceptionMiddleware 바깥에서 실행되므로
    @app.exception_handler(AppError) 가 닿지 않는다. 미들웨어 쪽에서도 동일한 응답
    형식과 로깅 정책을 쓰도록 이 함수로 분리했다.
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

    # 5xx 에러만 Sentry에 전송
    if exc.status_code >= 500:
        sentry_sdk.capture_exception(exc)

    response_body = {
        "success": False,
        "error_code": exc.error_code,
        "message": exc.message
    }
    if exc.detail is not None:
        response_body["detail"] = exc.detail

    return JSONResponse(status_code=exc.status_code, content=response_body)


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
        """AppError 계열 통합 처리 (변환은 app_error_response 에 위임)"""
        return app_error_response(request, exc)

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

        # 미처리 예외는 항상 Sentry에 전송
        sentry_sdk.capture_exception(exc)

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "서버 내부 오류가 발생했습니다"
            }
        )