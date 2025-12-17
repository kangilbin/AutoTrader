"""
Auth API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.common.exceptions import BusinessException
from app.auth.service import AuthService
from app.auth.schemas import AuthCreateRequest, AuthChoiceRequest
from app.external.kis_api import oauth_token

router = APIRouter(prefix="/auth", tags=["Auth"])


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    """AuthService 의존성 주입"""
    return AuthService(db)


@router.get("")
async def list_auth_keys(
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 목록 조회"""
    auth_keys = await service.get_auth_keys(user_id)
    return {"message": "보안키 조회 성공", "data": auth_keys}


@router.post("")
async def register_auth(
    request: AuthCreateRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 등록"""
    try:
        # KIS API 검증
        await oauth_token(
            user_id,
            request.SIMULATION_YN,
            request.API_KEY,
            request.SECRET_KEY
        )

        auth_info = await service.create_auth(user_id, request)
        return {"message": "보안키 등록 완료", "data": auth_info}
    except Exception as e:
        raise BusinessException(str(e))


@router.post("/choice")
async def choose_auth(
    request: AuthChoiceRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 선택"""
    await service.choose_auth(user_id, request.AUTH_ID, request.ACCOUNT_NO)
    return {"message": "보안키 선택 완료"}