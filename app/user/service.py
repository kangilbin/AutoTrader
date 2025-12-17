"""
User Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import logging

from app.user.repository import UserRepository
from app.user.entity import User
from app.user.schemas import UserCreateRequest, UserResponse
from app.core.security import (
    hash_password,
    check_password,
    create_access_token,
    create_refresh_token,
    verify_token,
)
from app.core.config import get_settings
from app.common.exceptions import (
    UnauthorizedException,
    BusinessException,
    DuplicateException,
)
from app.module.redis_connection import get_redis

logger = logging.getLogger(__name__)
settings = get_settings()


class UserService:
    """사용자 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UserRepository(db)

    async def create_user(self, request: UserCreateRequest) -> dict:
        """회원 가입"""
        try:
            # 중복 검사
            if await self.repo.exists(request.USER_ID):
                raise DuplicateException("사용자", request.USER_ID)

            # 도메인 엔티티 생성 (비즈니스 검증 포함)
            user = User.create(
                user_id=request.USER_ID,
                user_name=request.USER_NAME,
                phone=request.PHONE,
                password=hash_password(request.PASSWORD)
            )

            # 저장
            db_user = await self.repo.save(user)
            await self.db.commit()

            return UserResponse.model_validate(db_user).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"회원 가입 실패: {e}", exc_info=True)
            raise BusinessException("회원 가입에 실패했습니다")

    async def login(self, user_id: str, password: str) -> tuple[str, str]:
        """로그인"""
        user_info = await self.repo.find_by_id_with_password(user_id)

        if not user_info or not check_password(password, user_info["PASSWORD"]):
            raise UnauthorizedException("잘못된 아이디 또는 비밀번호입니다")

        # 토큰 생성
        access_token = create_access_token(
            user_id,
            user_info={"USER_NAME": user_info["USER_NAME"], "PHONE": user_info["PHONE"]}
        )
        refresh_token = create_refresh_token(user_id)

        # Redis에 저장
        redis = await get_redis()
        await redis.hset(user_id, mapping={
            "refresh_token": refresh_token,
            "USER_NAME": user_info["USER_NAME"],
            "PHONE": user_info["PHONE"]
        })
        await redis.expire(user_id, int(settings.token_refresh_exp.total_seconds()))

        return access_token, refresh_token

    async def check_duplicate(self, user_id: str) -> bool:
        """ID 중복 검사"""
        return await self.repo.exists(user_id)

    async def refresh_token(self, refresh_token: str) -> str:
        """토큰 갱신"""
        token_data = verify_token(refresh_token)
        if token_data is None:
            raise UnauthorizedException("유효하지 않은 리프레시 토큰입니다")

        user_id = token_data.user_id
        redis = await get_redis()

        # Redis 검증
        user_info = await redis.hgetall(user_id)
        if not user_info or user_info.get("refresh_token") != refresh_token:
            raise UnauthorizedException("유효하지 않은 리프레시 토큰입니다")

        # 새 액세스 토큰 발급
        access_token = create_access_token(
            user_id,
            user_info={"USER_NAME": user_info.get("USER_NAME"), "PHONE": user_info.get("PHONE")}
        )

        return access_token

    async def update_user(self, user_id: str, data: dict) -> dict:
        """회원 정보 수정"""
        try:
            data["MOD_DT"] = datetime.now()
            result = await self.repo.update(user_id, data)
            await self.db.commit()
            return UserResponse.model_validate(result).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"회원 정보 수정 실패: {e}", exc_info=True)
            raise BusinessException("회원 정보 수정에 실패했습니다")

    async def delete_user(self, user_id: str) -> bool:
        """회원 탈퇴"""
        try:
            result = await self.repo.delete(user_id)
            await self.db.commit()
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"회원 탈퇴 실패: {e}", exc_info=True)
            raise BusinessException("회원 탈퇴에 실패했습니다")