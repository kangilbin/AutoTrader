"""
Auth Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List
import logging

from app.domain.auth.repository import AuthRepository
from app.domain.account.service import AccountService
from app.domain.auth.entity import Auth
from app.domain.auth.schemas import AuthCreateRequest, AuthResponse
from app.core.security import encrypt, decrypt
from app.exceptions import NotFoundError, DatabaseError
from app.common.redis import get_redis
from app.external.kis_api import oauth_token, invalidate_token_cache

logger = logging.getLogger(__name__)


class AuthService:
    """인증키 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)
        # 인증키 삭제는 그 인증키로 등록된 계좌·스윙까지 정리한다. 삭제 규칙은
        # 계좌 쪽에 한 벌만 두고(계좌 단독 삭제와 동일 규칙) 여기서는 위임한다.
        self.account_service = AccountService(db)

    async def create_auth(self, user_id: str, request: AuthCreateRequest) -> dict:
        """인증키 등록 - KIS 토큰 발급으로 유효성 검증

        저장(flush)으로 AUTH_ID를 먼저 확보한 뒤 검증한다. 검증 토큰이 실사용
        슬롯({user}_{auth}_access_token)에 캐시되어, 등록 직후 계좌 검증이
        재발급 없이 그대로 재사용한다. AUTH_ID 없이 발급하면 아무도 읽지 않는
        슬롯에 저장돼 곧바로 재발급이 일어나고, appkey당 1분 1회 제한(EGW00133)에
        걸린다. 검증 실패 시 롤백되므로 쓸 수 없는 인증키가 남지는 않는다.
        """
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

            await oauth_token(
                user_id,
                request.SIMULATION_YN,
                request.API_KEY,
                request.SECRET_KEY,
                auth_id=db_auth.AUTH_ID,
            )

            await self.db.commit()

            return AuthResponse.model_validate(db_auth).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"인증키 등록 실패: {e}", exc_info=True)
            raise DatabaseError("인증키 등록에 실패했습니다", operation="insert", original_error=e)
        except Exception:
            # 토큰 발급 실패 = 앱키가 유효하지 않음. INSERT를 되돌린다.
            await self.db.rollback()
            raise

    async def get_auth_keys(self, user_id: str) -> List[dict]:
        """인증키 목록 조회"""
        auth_list = await self.repo.find_all_by_user(user_id)
        return [AuthResponse.model_validate(a).model_dump() for a in auth_list]

    async def choose_auth(self, user_id: str, auth_id: int, account_no: str) -> dict:
        """인증키 선택 및 OAuth 토큰 발급"""
        auth_data = await self.repo.find_by_id(user_id, auth_id)
        if not auth_data:
            raise NotFoundError("인증키", auth_id)

        # Redis에 계좌번호, 인증키 ID 저장
        redis = await get_redis()
        await redis.hset(user_id, mapping={
            "ACCOUNT_NO": account_no,
            "AUTH_ID": str(auth_id),
        })

        # OAuth 토큰 발급 (선택한 인증키(auth_id) 슬롯에 저장 → 모의/실전 전환 시 공존)
        await oauth_token(
            user_id,
            auth_data["SIMULATION_YN"],
            decrypt(auth_data["API_KEY"]),
            decrypt(auth_data["SECRET_KEY"]),
            auth_id=auth_id,
        )

        return AuthResponse.model_validate(auth_data).model_dump()

    async def delete_impact(self, user_id: str, auth_id: int) -> dict:
        """인증키 삭제 영향도 조회 (삭제 전 확인용)

        소유권은 여기서 검증한다. 검증 없이 위임하면 남의 AUTH_ID로 계좌번호와
        보유 종목을 엿볼 수 있다.
        """
        if not await self.repo.find_by_id(user_id, auth_id):
            raise NotFoundError("인증키", auth_id)

        return await self.account_service.delete_impact_by_auth(user_id, auth_id)

    async def delete_auth(self, user_id: str, auth_id: int) -> bool:
        """인증키 삭제 - 계좌·스윙 동반 삭제, 소유권 검증 포함

        인증키가 사라지면 그 인증키로 등록한 계좌는 주문할 수단이 없다. 계좌를
        남겨두면 배치가 인증키 없는 계좌의 스윙을 매 주기 집어 실패하므로,
        계좌와 그 계좌의 스윙까지 한 트랜잭션에서 함께 정리한다.
        """
        try:
            result = await self.repo.delete(user_id, auth_id)
            if not result:
                raise NotFoundError("인증키", auth_id)

            await self.account_service.delete_accounts_by_auth(user_id, auth_id)
            await self.db.commit()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"인증키 삭제 실패: {e}", exc_info=True)
            raise DatabaseError("인증키 삭제에 실패했습니다", operation="delete", original_error=e)

        # 커밋 이후에 정리한다. DB 삭제가 확정되지 않았는데 캐시를 먼저 비우면
        # 롤백 시 멀쩡한 인증키의 토큰만 날아가 재발급(1분 제한)을 유발한다.
        await self._clear_auth_cache(user_id, auth_id)
        return result

    async def _clear_auth_cache(self, user_id: str, auth_id: int) -> None:
        """삭제된 인증키의 토큰 캐시와 세션 선택 상태 제거

        토큰 캐시는 expires_in(최대 24h)까지 살아 있어, 지우지 않으면 폐기된
        인증키로 그 기간 내내 주문이 나간다.
        세션의 AUTH_ID가 삭제 대상을 가리키면 '선택 안 됨'으로 되돌려,
        이후 요청이 매번 NotFoundError로 실패하는 대신 인증키 재선택을 요구하게 한다.

        캐시 정리 실패로 삭제 API를 실패시키지는 않는다. DB가 진실이고,
        token_for_auth_id가 AUTH_KEY를 먼저 조회하므로 남은 캐시는 다음 호출에서
        걸러진다 (이 정리는 그 시점을 앞당기는 역할).
        """
        try:
            await invalidate_token_cache(user_id, auth_id)

            redis = await get_redis()
            session = await redis.hgetall(user_id)
            if session.get("AUTH_ID") == str(auth_id):
                await redis.hdel(user_id, "AUTH_ID", "ACCOUNT_NO")
        except Exception as e:
            logger.warning(f"인증키 캐시 정리 실패 (user_id={user_id}, auth_id={auth_id}): {e}")
