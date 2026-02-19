"""
OAuth Service - Google OAuth 비즈니스 로직
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import logging

from app.domain.user.repository import UserRepository
from app.domain.user.entity import User
from app.domain.device.repository import DeviceRepository
from app.core.security import create_access_token, create_refresh_token
from app.core.config import get_settings
from app.exceptions import AuthenticationError, DatabaseError, ExternalServiceError
from app.common.redis import get_redis
from app.common.email import EmailService
from app.external.http_client import fetch

logger = logging.getLogger(__name__)
settings = get_settings()

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class OAuthService:
    """OAuth 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.device_repo = DeviceRepository(db)

    async def google_login(
        self,
        google_access_token: str,
        google_refresh_token: Optional[str],
        expires_in: int,
        device_id: str,
        device_name: str
    ) -> dict:
        """
        Google OAuth 로그인 (디바이스 권한 검증 포함)

        1. Google access_token으로 사용자 정보 조회
        2. 신규 사용자: 계정 생성 + 디바이스 권한 요청 (ACTIVE_YN='N')
        3. 기존 사용자 + 디바이스 미승인: 권한 없음 반환
        4. 기존 사용자 + 디바이스 승인: 로그인 성공 (JWT 발급)
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

            if not user_info:
                # 신규 사용자: 계정 생성 + 디바이스 권한 요청
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

                # 디바이스 등록 (비활성 상태 - 승인 대기)
                await self.device_repo.save_inactive(
                    device_id=device_id,
                    device_name=device_name,
                    user_id=user_id
                )
                await self.db.commit()

                # 관리자에게 이메일 알림 (비동기로 처리, 실패해도 무시)
                asyncio.get_event_loop().run_in_executor(
                    None,
                    EmailService.send_device_registration_notification,
                    user_id, user_name, device_id, device_name
                )

                logger.info(f"신규 사용자 등록 + 디바이스 권한 요청: {user_id}, {device_id}")
                return {"status": "DEVICE_PENDING"}

            # 기존 사용자: Google 토큰 업데이트
            user_id = user_info["USER_ID"]
            user_name = user_info["USER_NAME"]
            user_phone = user_info["PHONE"]

            await self.user_repo.update_google_tokens(
                user_id=user_id,
                access_token=google_access_token,
                refresh_token=google_refresh_token,
                expires_at=expires_at
            )

            # 3. 디바이스 권한 확인
            device = await self.device_repo.find_by_id(device_id)

            if not device:
                # 디바이스가 등록되지 않은 경우 - 권한 요청 등록
                await self.device_repo.save_inactive(
                    device_id=device_id,
                    device_name=device_name,
                    user_id=user_id
                )
                await self.db.commit()

                # 관리자에게 이메일 알림 (비동기로 처리, 실패해도 무시)
                asyncio.get_event_loop().run_in_executor(
                    None,
                    EmailService.send_device_registration_notification,
                    user_id, user_name, device_id, device_name
                )

                logger.info(f"기존 사용자 새 디바이스 권한 요청: {user_id}, {device_id}")
                return {"status": "DEVICE_PENDING"}

            if device.get("ACTIVE_YN") != "Y":
                # 디바이스가 등록되어 있지만 미승인 상태
                await self.db.commit()

                logger.warning(f"디바이스 권한 없음: {user_id}, {device_id}")
                return {"status": "DEVICE_DENIED"}

            # 4. 디바이스 승인됨 - 로그인 성공
            await self.db.commit()

            access_token = create_access_token(
                user_id,
                user_info={"USER_NAME": user_name, "EMAIL": email, "PHONE": user_phone}
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

            logger.info(f"로그인 성공: {user_id}, {device_id}")
            return {
                "status": "LOGIN_SUCCESS",
                "access_token": access_token,
                "refresh_token": refresh_token
            }

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