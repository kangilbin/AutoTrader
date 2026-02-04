"""
OAuth Service - Google OAuth 비즈니스 로직
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from typing import Optional
import logging

from app.domain.user.repository import UserRepository
from app.domain.user.entity import User
from app.core.security import create_access_token, create_refresh_token
from app.core.config import get_settings
from app.exceptions import AuthenticationError, DatabaseError, ExternalServiceError
from app.common.redis import get_redis
from app.external.http_client import fetch

logger = logging.getLogger(__name__)
settings = get_settings()

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class OAuthService:
    """OAuth 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)

    async def google_login(
        self,
        google_access_token: str,
        google_refresh_token: Optional[str],
        expires_in: int
    ) -> tuple[str, str]:
        """
        Google OAuth 로그인
        1. Google access_token으로 사용자 정보 조회
        2. 기존 사용자: Google 토큰 업데이트 + 우리 JWT 발급
        3. 신규 사용자: 계정 생성 + 우리 JWT 발급
        """
        try:
            # 1. Google API로 사용자 정보 조회
            response = await fetch(
                method="GET",
                url=GOOGLE_USERINFO_URL,
                service_name="Google OAuth",
                headers={"Authorization": f"Bearer {google_access_token}"}
            )
            userinfo = response["body"]
            email = userinfo.get("email")
            user_name = userinfo.get("name", email.split("@")[0] if email else "User")

            if not email:
                raise AuthenticationError("이메일 정보를 가져올 수 없습니다", reason="no_email")

            expires_at = datetime.now() + timedelta(seconds=expires_in)

            # 2. 기존 사용자 조회
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

            # 3. JWT 토큰 발급
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

        except ExternalServiceError:
            raise AuthenticationError("Google 토큰이 유효하지 않습니다", reason="invalid_token")
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"OAuth 로그인 실패: {e}", exc_info=True)
            raise DatabaseError("OAuth 로그인에 실패했습니다", operation="oauth_login", original_error=e)

    async def get_valid_google_token(self, user_id: str) -> str:
        """
        유효한 Google access_token 반환
        - 만료됐으면 GOOGLE_TOKEN_EXPIRED 에러 (프론트에서 갱신 필요)
        """
        user_info = await self.user_repo.find_by_id_with_tokens(user_id)
        if not user_info:
            raise AuthenticationError("사용자를 찾을 수 없습니다")

        if not user_info.get("GOOGLE_ACCESS_TOKEN"):
            raise AuthenticationError("Google 로그인이 필요합니다", reason="no_token")

        expires_at = user_info.get("GOOGLE_TOKEN_EXPIRES_AT")
        if expires_at and expires_at <= datetime.now():
            raise AuthenticationError("Google 토큰이 만료되었습니다", reason="token_expired")

        return user_info["GOOGLE_ACCESS_TOKEN"]

    async def update_google_token(
        self,
        user_id: str,
        google_access_token: str,
        expires_in: int
    ) -> None:
        """
        Google access_token 업데이트 (프론트에서 갱신 후 호출)
        """
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        await self.user_repo.update_google_tokens(
            user_id=user_id,
            access_token=google_access_token,
            refresh_token=None,  # refresh_token은 유지
            expires_at=expires_at
        )
        await self.db.commit()