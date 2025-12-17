"""
Auth Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from typing import List
import logging

from app.domain.auth.repository import AuthRepository
from app.domain.auth.entity import Auth
from app.domain.auth.schemas import AuthCreateRequest, AuthResponse
from app.core.security import encrypt, decrypt
from app.exceptions.http import BusinessException, NotFoundException
from app.module.redis_connection import get_redis
from app.external.kis_api import oauth_token

logger = logging.getLogger(__name__)


class AuthService:
    """인증키 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)

    async def create_auth(self, user_id: str, request: AuthCreateRequest) -> dict:
        """인증키 등록"""
        try:
            # 도메인 엔티티 생성 (비즈니스 검증)
            auth = Auth.create(
                user_id=user_id,
                auth_name=request.AUTH_NAME,
                simulation_yn=request.SIMULATION_YN,
                api_key=encrypt(request.API_KEY),
                secret_key=encrypt(request.SECRET_KEY)
            )

            db_auth = await self.repo.save(auth)
            await self.db.commit()

            return AuthResponse.model_validate(db_auth).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"인증키 등록 실패: {e}", exc_info=True)
            raise BusinessException("인증키 등록에 실패했습니다")

    async def get_auth_keys(self, user_id: str) -> List[dict]:
        """인증키 목록 조회"""
        auth_list = await self.repo.find_all_by_user(user_id)
        return [AuthResponse.model_validate(a).model_dump() for a in auth_list]

    async def choose_auth(self, user_id: str, auth_id: int, account_no: str) -> dict:
        """인증키 선택 및 OAuth 토큰 발급"""
        auth_data = await self.repo.find_by_id(user_id, auth_id)
        if not auth_data:
            raise NotFoundException("인증키", auth_id)

        # Redis에 계좌번호 저장
        redis = await get_redis()
        await redis.hset(user_id, "ACCOUNT_NO", account_no)

        # OAuth 토큰 발급
        await oauth_token(
            user_id,
            auth_data["SIMULATION_YN"],
            decrypt(auth_data["API_KEY"]),
            decrypt(auth_data["SECRET_KEY"])
        )

        return AuthResponse.model_validate(auth_data).model_dump()

    async def update_auth(self, auth_id: int, data: dict) -> dict:
        """인증키 수정"""
        try:
            if "API_KEY" in data:
                data["API_KEY"] = encrypt(data["API_KEY"])
            if "SECRET_KEY" in data:
                data["SECRET_KEY"] = encrypt(data["SECRET_KEY"])
            data["MOD_DT"] = datetime.now()

            result = await self.repo.update(auth_id, data)
            await self.db.commit()
            return AuthResponse.model_validate(result).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"인증키 수정 실패: {e}", exc_info=True)
            raise BusinessException("인증키 수정에 실패했습니다")

    async def delete_auth(self, auth_id: int) -> bool:
        """인증키 삭제"""
        try:
            result = await self.repo.delete(auth_id)
            await self.db.commit()
            if not result:
                raise NotFoundException("인증키", auth_id)
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"인증키 삭제 실패: {e}", exc_info=True)
            raise BusinessException("인증키 삭제에 실패했습니다")