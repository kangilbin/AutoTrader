"""
User Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from typing import Optional
from datetime import datetime

from app.domain.user.entity import User, UserIdSequence
from app.domain.user.schemas import UserResponse
import logging

logger = logging.getLogger(__name__)


class UserRepository:
    """사용자 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, user_id: str) -> Optional[dict]:
        """사용자 조회"""
        query = select(User).filter(User.USER_ID == user_id)
        result = await self.db.execute(query)
        db_user = result.scalars().first()
        if not db_user:
            return None
        return UserResponse.model_validate(db_user).model_dump()

    async def save(self, user: User) -> User:
        """사용자 저장 (flush만 수행)"""
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def update(self, user_id: str, data: dict) -> Optional[User]:
        """사용자 수정 (flush만 수행)"""
        query = (
            update(User)
            .filter(User.USER_ID == user_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(User, user_id)

    async def delete(self, user_id: str) -> bool:
        """사용자 삭제 (flush만 수행)"""
        query = delete(User).filter(User.USER_ID == user_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def exists(self, user_id: str) -> bool:
        """사용자 존재 여부 확인"""
        query = select(User.USER_ID).filter(User.USER_ID == user_id)
        result = await self.db.execute(query)
        return result.scalar() is not None

    async def find_by_email(self, email: str) -> Optional[dict]:
        """이메일로 사용자 조회"""
        query = select(User).filter(User.EMAIL == email)
        result = await self.db.execute(query)
        db_user = result.scalars().first()
        if not db_user:
            return None
        return {
            "USER_ID": db_user.USER_ID,
            "USER_NAME": db_user.USER_NAME,
            "EMAIL": db_user.EMAIL,
            "PHONE": db_user.PHONE,
            "GOOGLE_ACCESS_TOKEN": db_user.GOOGLE_ACCESS_TOKEN,
            "GOOGLE_REFRESH_TOKEN": db_user.GOOGLE_REFRESH_TOKEN,
            "GOOGLE_TOKEN_EXPIRES_AT": db_user.GOOGLE_TOKEN_EXPIRES_AT,
            "REG_DT": db_user.REG_DT,
            "MOD_DT": db_user.MOD_DT,
        }

    async def find_by_id_with_tokens(self, user_id: str) -> Optional[dict]:
        """사용자 조회 (Google 토큰 포함)"""
        query = select(User).filter(User.USER_ID == user_id)
        result = await self.db.execute(query)
        db_user = result.scalars().first()
        if not db_user:
            return None
        return {
            "USER_ID": db_user.USER_ID,
            "USER_NAME": db_user.USER_NAME,
            "EMAIL": db_user.EMAIL,
            "PHONE": db_user.PHONE,
            "GOOGLE_ACCESS_TOKEN": db_user.GOOGLE_ACCESS_TOKEN,
            "GOOGLE_REFRESH_TOKEN": db_user.GOOGLE_REFRESH_TOKEN,
            "GOOGLE_TOKEN_EXPIRES_AT": db_user.GOOGLE_TOKEN_EXPIRES_AT,
        }

    async def update_google_tokens(
        self,
        user_id: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_at: datetime
    ) -> None:
        """Google 토큰 업데이트"""
        data = {
            "GOOGLE_ACCESS_TOKEN": access_token,
            "GOOGLE_TOKEN_EXPIRES_AT": expires_at,
            "MOD_DT": datetime.now()
        }
        if refresh_token:
            data["GOOGLE_REFRESH_TOKEN"] = refresh_token

        query = (
            update(User)
            .filter(User.USER_ID == user_id)
            .values(**data)
        )
        await self.db.execute(query)
        await self.db.flush()

    async def generate_next_user_id(self) -> str:
        """
        다음 USER_ID 생성 (USR00001, USR00002, ...)

        시퀀스 테이블에 레코드를 삽입하여 AUTO_INCREMENT 값을 얻고,
        USR + 5자리 숫자 형식으로 변환
        """
        # 시퀀스 테이블에 레코드 삽입
        seq_record = UserIdSequence()
        self.db.add(seq_record)
        await self.db.flush()
        await self.db.refresh(seq_record)

        # USR + 5자리 숫자 (예: USR00001, USR00123)
        sequence_number = seq_record.id
        user_id = f"USR{sequence_number:05d}"

        return user_id