from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.health import readiness_status
from app.core.response import success_response, error_response
from app.core.config import get_settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """liveness: 프로세스가 요청을 처리 가능한지만 확인"""
    settings = get_settings()
    return success_response(
        message="healthy",
        data={
            "status": "healthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
        },
    )


@router.get("/ready")
async def readiness_check():
    """readiness: 필수 의존성(DB/Redis) 준비 여부 확인"""
    settings = get_settings()

    # 운영 정책: 지금은 Redis가 필수라고 하셨으니 True 고정.
    # DB도 보통 필수이므로 True로 둡니다(원하면 환경변수로 분리 가능).
    result = await readiness_status(
        timeout_sec=1.0,
        require_redis=True,
        require_db=True,
    )

    if result["status"] == "ready":
        return success_response(message="ready", data=result)

    return JSONResponse(
        status_code=503,
        content=error_response(
            message="not_ready",
            error_code="NOT_READY",
            detail=result,
        ),
    )