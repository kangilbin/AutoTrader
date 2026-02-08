"""
User API Router
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.exceptions import AuthenticationError
from app.domain.user.service import UserService
from app.common.redis import get_redis

router = APIRouter(prefix="/users", tags=["Users"])


def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    """UserService 의존성 주입"""
    return UserService(db)


@router.post("/refresh")
async def refresh(
    request: Request,
    service: Annotated[UserService, Depends(get_user_service)]
):
    """토큰 갱신"""
    body = await request.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise AuthenticationError("refresh_token이 필요합니다")

    access_token = await service.refresh_token(refresh_token)
    return {"message": "토큰 재발급", "data": {"access_token": access_token}}


@router.post("/logout")
async def logout(user_id: Annotated[str, Depends(get_current_user)]):
    """로그아웃"""
    redis = await get_redis()
    await redis.delete(user_id)
    return {"message": "로그아웃 성공"}