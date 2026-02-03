"""
Google OAuth Router - Expo 앱용
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional
from pydantic import BaseModel
import httpx

from app.common.database import get_db
from app.domain.oauth.service import OAuthService
from app.exceptions import AuthenticationError

router = APIRouter(prefix="/oauth", tags=["OAuth"])

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


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
    Google OAuth 로그인 (Expo 앱용)

    1. Expo에서 Google 로그인 후 토큰 전달
    2. 백엔드에서 토큰으로 사용자 정보 조회
    3. 사용자 생성/조회 + 우리 JWT 발급
    """
    # 1. Google access_token으로 사용자 정보 조회
    async with httpx.AsyncClient() as client:
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {request.access_token}"}
        )

    if userinfo_response.status_code != 200:
        raise AuthenticationError(
            "Google 토큰이 유효하지 않습니다",
            reason="invalid_token"
        )

    userinfo = userinfo_response.json()
    email = userinfo.get("email")
    name = userinfo.get("name", email.split("@")[0] if email else "User")

    if not email:
        raise AuthenticationError("이메일 정보를 가져올 수 없습니다", reason="no_email")

    # 2. 로그인 처리 (신규/기존 사용자)
    access_token, refresh_token = await service.oauth_login(
        email=email,
        user_name=name,
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