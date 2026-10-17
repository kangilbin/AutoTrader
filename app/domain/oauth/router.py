"""
Google OAuth Router - Expo 앱용
"""
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional
from pydantic import BaseModel

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.oauth.service import OAuthService

router = APIRouter(prefix="/oauth", tags=["OAuth"])


class GoogleLoginRequest(BaseModel):
    """Google OAuth 로그인 요청 (Expo에서 전달)"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 3600


class GoogleTokenUpdateRequest(BaseModel):
    """Google 토큰 업데이트 요청 (프론트에서 갱신 후)"""
    access_token: str
    expires_in: int = 3600


def get_oauth_service(db: AsyncSession = Depends(get_db)) -> OAuthService:
    """OAuthService 의존성 주입"""
    return OAuthService(db)


@router.post("/google/login")
async def google_login(
    request: GoogleLoginRequest,
    service: Annotated[OAuthService, Depends(get_oauth_service)],
    x_device_id: Annotated[str, Header()],
    x_device_name: Annotated[str, Header()]
):
    """
    Google OAuth 로그인
    - Expo에서 Google 로그인 후 토큰 전달
    - 사용자 생성/조회 + JWT 발급
    """
    result = await service.google_login(
        google_access_token=request.access_token,
        google_refresh_token=request.refresh_token,
        expires_in=request.expires_in,
        device_id=x_device_id,
        device_name=x_device_name
    )

    return result


@router.post("/google/token")
async def update_google_token(
    request: GoogleTokenUpdateRequest,
    service: Annotated[OAuthService, Depends(get_oauth_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """
    Google 토큰 업데이트
    - 프론트에서 refresh_token으로 갱신 후 호출
    - 새 access_token 저장
    """
    await service.update_google_token(
        user_id=user_id,
        google_access_token=request.access_token,
        expires_in=request.expires_in
    )

    return success_response("Google 토큰 업데이트 완료")