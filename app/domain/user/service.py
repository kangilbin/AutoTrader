"""
User Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import logging

from app.domain.user.repository import UserRepository
from app.domain.user.schemas import UserResponse
from app.core.security import create_access_token, verify_token
from app.core.config import get_settings
from app.exceptions import (
    AuthenticationError,
    DatabaseError,
)
from app.common.redis import get_redis

logger = logging.getLogger(__name__)
settings = get_settings()


class UserService:
    """사용자 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UserRepository(db)

    async def refresh_token(self, refresh_token: str) -> str:
        """토큰 갱신"""
        token_data = verify_token(refresh_token, expected_type="refresh")
        if token_data is None:
            raise AuthenticationError("유효하지 않은 리프레시 토큰입니다")

        user_id = token_data.user_id
        redis = await get_redis()

        # Redis 검증
        user_info = await redis.hgetall(user_id)
        if not user_info or user_info.get("refresh_token") != refresh_token:
            raise AuthenticationError("유효하지 않은 리프레시 토큰입니다")

        # 새 액세스 토큰 발급
        access_token = create_access_token(
            user_id,
            user_info={"USER_NAME": user_info.get("USER_NAME"), "EMAIL": user_info.get("EMAIL"), "PHONE": user_info.get("PHONE")}
        )

        return access_token

    async def update_user(self, user_id: str, data: dict) -> dict:
        """회원 정보 수정"""
        try:
            data["MOD_DT"] = datetime.now()
            result = await self.repo.update(user_id, data)
            await self.db.commit()

            user_data = UserResponse.model_validate(result).model_dump()

            # access_token 재발급 (변경된 정보 반영)
            user_info = {"USER_NAME": user_data.get("USER_NAME"), "EMAIL": user_data.get("EMAIL"), "PHONE": user_data.get("PHONE")}
            access_token = create_access_token(user_id, user_info=user_info)

            # Redis 갱신 (refresh_token 유지, 사용자 정보만 업데이트)
            redis = await get_redis()
            existing = await redis.hgetall(user_id)
            if existing:
                await redis.hset(user_id, mapping={
                    **existing,
                    "USER_NAME": user_data.get("USER_NAME"),
                    "PHONE": user_data.get("PHONE"),
                })
            return {"user": user_data, "access_token": access_token}
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"회원 정보 수정 실패: {e}", exc_info=True)
            raise DatabaseError("회원 정보 수정에 실패했습니다", operation="update", original_error=e)

    async def delete_user(self, user_id: str) -> bool:
        """회원 탈퇴"""
        try:
            result = await self.repo.delete(user_id)
            await self.db.commit()
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"회원 탈퇴 실패: {e}", exc_info=True)
            raise DatabaseError("회원 탈퇴에 실패했습니다", operation="delete", original_error=e)
