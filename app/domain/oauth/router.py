"""
Google OAuth Router - Expo 앱용
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional
from pydantic import BaseModel

from app.common.database import get_db
from app.domain.oauth.service import OAuthService

router = APIRouter(prefix="/oauth", tags=["OAuth"])


class GoogleLoginRequest(BaseModel):
    """Google OAuth 로그인 요청 (Expo에서 전달)"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 3600


def get_oauth_service(db: AsyncSession = Depends(get_db)) -> OAuthService:
    """OAuthService 의존성 주입"""
    return OAuthService(db)


@router.post("/google/login")
async def google_login(
    request: GoogleLoginRequest,
    service: Annotated[OAuthService, Depends(get_oauth_service)]
):
    """
    Google OAuth 로그인
    - Expo에서 Google 로그인 후 토큰 전달
    - 사용자 생성/조회 + JWT 발급
    """
    access_token, refresh_token = await service.google_login(
        google_access_token=request.access_token,
        google_refresh_token=request.refresh_token,
        expires_in=request.expires_in
    )

    return {
        "message": "Google 로그인 성공",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token
        }
    }