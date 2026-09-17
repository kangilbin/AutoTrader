"""
Auth API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.auth.service import AuthService
from app.domain.auth.schemas import AuthCreateRequest, AuthChoiceRequest

router = APIRouter(prefix="/auths", tags=["Auth"])


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
    return success_response("보안키 조회 성공", auth_keys)


@router.post("")
async def register_auth(
    request: AuthCreateRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 등록 (KIS 토큰 발급 검증은 서비스에서 수행)"""
    auth_info = await service.create_auth(user_id, request)
    return success_response("보안키 등록 완료", auth_info)


@router.get("/{auth_id}/delete-impact")
async def get_auth_delete_impact(
    auth_id: int,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 삭제 영향도 조회 (함께 삭제되는 계좌·자동매매)"""
    impact = await service.delete_impact(user_id, auth_id)
    return success_response("삭제 영향도 조회", impact)


@router.delete("/{auth_id}")
async def delete_auth(
    auth_id: int,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 삭제"""
    await service.delete_auth(user_id, auth_id)
    return success_response("보안키 삭제 완료")


@router.post("/choice")
async def choose_auth(
    request: AuthChoiceRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """보안키 선택"""
    await service.choose_auth(user_id, request.AUTH_ID, request.ACCOUNT_NO)
    return success_response("보안키 선택 완료")