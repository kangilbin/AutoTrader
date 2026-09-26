"""
OAuth Service - Google OAuth 비즈니스 로직
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from typing import Optional
import httpx
import logging

from app.domain.user.repository import UserRepository
from app.domain.user.entity import User
from app.core.security import create_access_token, create_refresh_token
from app.core.config import get_settings
from app.exceptions import AuthenticationError, DatabaseError
from app.common.redis import get_redis

logger = logging.getLogger(__name__)
settings = get_settings()

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthService:
    """OAuth 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)

    async def oauth_login(
        self,
        email: str,
        user_name: str,
        google_access_token: str,
        google_refresh_token: Optional[str],
        expires_in: int
    ) -> tuple[str, str]:
        """
        Google OAuth 로그인
        - 기존 사용자: Google 토큰 업데이트 + 우리 JWT 발급
        - 신규 사용자: 계정 생성 + 우리 JWT 발급
        """
        try:
            expires_at = datetime.now() + timedelta(seconds=expires_in)

            # 기존 사용자 조회
            user_info = await self.user_repo.find_by_email(email)

            if user_info:
                # 기존 사용자: Google 토큰 업데이트
                await self.user_repo.update_google_tokens(
                    user_id=user_info["USER_ID"],
                    access_token=google_access_token,
                    refresh_token=google_refresh_token,
                    expires_at=expires_at
                )
                await self.db.commit()
                user_id = user_info["USER_ID"]
                user_name = user_info["USER_NAME"]
            else:
                # 신규 사용자: 계정 생성
                user_id = await self.user_repo.generate_next_user_id()
                user = User.create_oauth_user(
                    user_id=user_id,
                    user_name=user_name,
                    email=email,
                    google_access_token=google_access_token,
                    google_refresh_token=google_refresh_token,
                    google_token_expires_at=expires_at
                )
                await self.user_repo.save(user)
                await self.db.commit()

            # 우리 JWT 토큰 발급
            access_token = create_access_token(
                user_id,
                user_info={"USER_NAME": user_name, "EMAIL": email}
            )
            refresh_token = create_refresh_token(user_id)

            # Redis에 저장
            redis = await get_redis()
            await redis.hset(user_id, mapping={
                "refresh_token": refresh_token,
                "USER_NAME": user_name,
                "EMAIL": email
            })
            await redis.expire(user_id, int(settings.token_refresh_exp.total_seconds()))

            return access_token, refresh_token

        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"OAuth 로그인 실패: {e}", exc_info=True)
            raise DatabaseError("OAuth 로그인에 실패했습니다", operation="oauth_login", original_error=e)

    async def refresh_google_token(self, user_id: str) -> str:
        """
        Google access_token 갱신
        - refresh_token으로 새 access_token 발급
        - 실패 시 AuthenticationError (재로그인 필요)
        """
        user_info = await self.user_repo.find_by_id_with_tokens(user_id)
        if not user_info or not user_info.get("GOOGLE_REFRESH_TOKEN"):
            raise AuthenticationError("Google 재로그인이 필요합니다", reason="no_refresh_token")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "refresh_token": user_info["GOOGLE_REFRESH_TOKEN"],
                        "grant_type": "refresh_token"
                    }
                )

            if response.status_code != 200:
                logger.warning(f"Google 토큰 갱신 실패: {response.text}")
                raise AuthenticationError("Google 재로그인이 필요합니다", reason="refresh_failed")

            token_data = response.json()
            new_access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            expires_at = datetime.now() + timedelta(seconds=expires_in)

            # DB 업데이트
            await self.user_repo.update_google_tokens(
                user_id=user_info["USER_ID"],
                access_token=new_access_token,
                refresh_token=None,  # refresh_token은 유지
                expires_at=expires_at
            )
            await self.db.commit()

            return new_access_token

        except httpx.HTTPError as e:
            logger.error(f"Google API 호출 실패: {e}", exc_info=True)
            raise AuthenticationError("Google 재로그인이 필요합니다", reason="api_error")

    async def get_valid_google_token(self, user_id: str) -> str:
        """
        유효한 Google access_token 반환
        - 만료됐으면 자동 갱신
        - 갱신 실패 시 AuthenticationError
        """
        user_info = await self.user_repo.find_by_id_with_tokens(user_id)
        if not user_info:
            raise AuthenticationError("사용자를 찾을 수 없습니다")

        if not user_info.get("GOOGLE_ACCESS_TOKEN"):
            raise AuthenticationError("Google 로그인이 필요합니다", reason="no_token")

        expires_at = user_info.get("GOOGLE_TOKEN_EXPIRES_AT")
        if expires_at and expires_at <= datetime.now():
            # 토큰 만료 → 갱신
            return await self.refresh_google_token(user_id)

        return user_info["GOOGLE_ACCESS_TOKEN"]