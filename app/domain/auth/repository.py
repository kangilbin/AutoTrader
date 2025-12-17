"""
Auth Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_
from typing import Optional, List

from app.common.database import AuthModel
from app.domain.auth.entity import Auth
import logging

logger = logging.getLogger(__name__)


class AuthRepository:
    """인증키 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, user_id: str, auth_id: int) -> Optional[dict]:
        """인증키 조회"""
        query = select(AuthModel).filter(
            and_(AuthModel.USER_ID == user_id, AuthModel.AUTH_ID == auth_id)
        )
        result = await self.db.execute(query)
        db_auth = result.scalars().first()
        if not db_auth:
            return None
        return {
            "AUTH_ID": db_auth.AUTH_ID,
            "USER_ID": db_auth.USER_ID,
            "AUTH_NAME": db_auth.AUTH_NAME,
            "SIMULATION_YN": db_auth.SIMULATION_YN,
            "API_KEY": db_auth.API_KEY,
            "SECRET_KEY": db_auth.SECRET_KEY,
            "REG_DT": db_auth.REG_DT,
            "MOD_DT": db_auth.MOD_DT,
        }

    async def find_all_by_user(self, user_id: str) -> List[AuthModel]:
        """사용자의 모든 인증키 조회"""
        query = select(AuthModel).filter(AuthModel.USER_ID == user_id)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def save(self, auth: Auth) -> AuthModel:
        """인증키 저장 (flush만 수행)"""
        db_auth = AuthModel(
            USER_ID=auth.user_id,
            AUTH_NAME=auth.auth_name,
            SIMULATION_YN=auth.simulation_yn,
            API_KEY=auth.api_key,
            SECRET_KEY=auth.secret_key
        )
        self.db.add(db_auth)
        await self.db.flush()
        await self.db.refresh(db_auth)
        return db_auth

    async def update(self, auth_id: int, data: dict) -> Optional[AuthModel]:
        """인증키 수정 (flush만 수행)"""
        query = (
            update(AuthModel)
            .filter(AuthModel.AUTH_ID == auth_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(AuthModel, auth_id)

    async def delete(self, auth_id: int) -> bool:
        """인증키 삭제 (flush만 수행)"""
        query = delete(AuthModel).filter(AuthModel.AUTH_ID == auth_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0