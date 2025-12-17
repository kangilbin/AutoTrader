"""
공통 의존성 주입
"""
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import get_db
from app.common.exceptions import UnauthorizedException
from app.core.security import verify_token

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """현재 인증된 사용자 ID 반환"""
    token = credentials.credentials
    token_data = verify_token(token)

    if token_data is None:
        raise UnauthorizedException("유효하지 않은 토큰입니다")

    return token_data.user_id


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str | None:
    """현재 인증된 사용자 ID 반환 (선택적)"""
    try:
        return await get_current_user(credentials)
    except UnauthorizedException:
        return None