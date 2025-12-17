"""
User Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from typing import Optional

from app.common.database import UserModel
from app.user.entity import User
from app.user.schemas import UserResponse
import logging

logger = logging.getLogger(__name__)


class UserRepository:
    """사용자 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, user_id: str) -> Optional[dict]:
        """사용자 조회"""
        query = select(UserModel).filter(UserModel.USER_ID == user_id)
        result = await self.db.execute(query)
        db_user = result.scalars().first()
        if not db_user:
            return None
        return UserResponse.model_validate(db_user).model_dump()

    async def find_by_id_with_password(self, user_id: str) -> Optional[dict]:
        """사용자 조회 (비밀번호 포함)"""
        query = select(UserModel).filter(UserModel.USER_ID == user_id)
        result = await self.db.execute(query)
        db_user = result.scalars().first()
        if not db_user:
            return None
        return {
            "USER_ID": db_user.USER_ID,
            "USER_NAME": db_user.USER_NAME,
            "PHONE": db_user.PHONE,
            "PASSWORD": db_user.PASSWORD,
            "REG_DT": db_user.REG_DT,
            "MOD_DT": db_user.MOD_DT,
        }

    async def save(self, user: User) -> UserModel:
        """사용자 저장 (flush만 수행)"""
        db_user = UserModel(
            USER_ID=user.user_id,
            USER_NAME=user.user_name,
            PHONE=user.phone,
            PASSWORD=user.password
        )
        self.db.add(db_user)
        await self.db.flush()
        await self.db.refresh(db_user)
        return db_user

    async def update(self, user_id: str, data: dict) -> Optional[UserModel]:
        """사용자 수정 (flush만 수행)"""
        query = (
            update(UserModel)
            .filter(UserModel.USER_ID == user_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(UserModel, user_id)

    async def delete(self, user_id: str) -> bool:
        """사용자 삭제 (flush만 수행)"""
        query = delete(UserModel).filter(UserModel.USER_ID == user_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def exists(self, user_id: str) -> bool:
        """사용자 존재 여부 확인"""
        query = select(UserModel.USER_ID).filter(UserModel.USER_ID == user_id)
        result = await self.db.execute(query)
        return result.scalar() is not None