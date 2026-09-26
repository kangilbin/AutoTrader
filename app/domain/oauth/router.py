"""
Google OAuth Router
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from urllib.parse import urlencode
import httpx

from app.common.database import get_db
from app.core.config import get_settings
from app.domain.oauth.service import OAuthService
from app.exceptions import AuthenticationError

router = APIRouter(prefix="/oauth", tags=["OAuth"])
settings = get_settings()

# Google OAuth URLs
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Gemini API scope
SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/generative-language.retriever",
]


def get_oauth_service(db: AsyncSession = Depends(get_db)) -> OAuthService:
    """OAuthService 의존성 주입"""
    return OAuthService(db)


@router.get("/google/login")
async def google_login():
    """
    Google OAuth 로그인 URL 생성
    - 클라이언트를 Google 로그인 페이지로 리다이렉트
    - prompt=consent로 항상 refresh_token 받음
    """
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # refresh_token 받기 위해 필수
        "prompt": "consent",  # 항상 동의 화면 표시 → refresh_token 항상 받음
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_callback(
    code: Annotated[str, Query(description="Google authorization code")],
    service: Annotated[OAuthService, Depends(get_oauth_service)]
):
    """
    Google OAuth 콜백 처리
    - authorization code로 토큰 교환
    - 사용자 정보 조회
    - 로그인 처리 (신규/기존 사용자)
    - 우리 JWT 토큰 반환
    """
    # 1. authorization code로 토큰 교환
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            }
        )

    if token_response.status_code != 200:
        raise AuthenticationError(
            f"Google 토큰 교환 실패: {token_response.text}",
            reason="token_exchange_failed"
        )

    token_data = token_response.json()
    google_access_token = token_data["access_token"]
    google_refresh_token = token_data.get("refresh_token")  # 첫 로그인에만 있을 수 있음
    expires_in = token_data.get("expires_in", 3600)

    # 2. 사용자 정보 조회
    async with httpx.AsyncClient() as client:
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"}
        )

    if userinfo_response.status_code != 200:
        raise AuthenticationError(
            "Google 사용자 정보 조회 실패",
            reason="userinfo_failed"
        )

    userinfo = userinfo_response.json()
    email = userinfo.get("email")
    name = userinfo.get("name", email.split("@")[0])

    if not email:
        raise AuthenticationError("이메일 정보를 가져올 수 없습니다", reason="no_email")

    # 3. 로그인 처리 (신규/기존 사용자)
    access_token, refresh_token = await service.oauth_login(
        email=email,
        user_name=name,
        google_access_token=google_access_token,
        google_refresh_token=google_refresh_token,
        expires_in=expires_in
    )

    return {
        "message": "Google 로그인 성공",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token
        }
    }