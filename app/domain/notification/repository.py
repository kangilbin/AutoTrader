"""
Notification Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from typing import Optional
from datetime import datetime

from app.domain.notification.entity import UserNotiSetting, UserPushToken


class NotificationRepository:
    """알림 설정 및 푸쉬 토큰 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 알림 설정 ──

    async def find_settings_by_user_id(self, user_id: str) -> list[UserNotiSetting]:
        """사용자 알림 설정 전체 조회"""
        result = await self.db.execute(
            select(UserNotiSetting).where(UserNotiSetting.USER_ID == user_id)
        )
        return list(result.scalars().all())

    async def find_setting(self, user_id: str, noti_type: str) -> Optional[UserNotiSetting]:
        """사용자 특정 알림 설정 조회"""
        result = await self.db.execute(
            select(UserNotiSetting).where(
                UserNotiSetting.USER_ID == user_id,
                UserNotiSetting.NOTI_TYPE == noti_type
            )
        )
        return result.scalar_one_or_none()

    async def save_setting(self, setting: UserNotiSetting) -> UserNotiSetting:
        """알림 설정 저장"""
        self.db.add(setting)
        await self.db.flush()
        return setting

    async def is_enabled(self, user_id: str, noti_type: str) -> bool:
        """특정 알림 유형 활성화 여부 확인"""
        setting = await self.find_setting(user_id, noti_type)
        return setting is not None and setting.USE_YN == 'Y'

    # ── 푸쉬 토큰 ──

    async def find_active_tokens_by_user_id(self, user_id: str) -> list[UserPushToken]:
        """사용자의 활성 푸쉬 토큰 목록 조회"""
        result = await self.db.execute(
            select(UserPushToken).where(
                UserPushToken.USER_ID == user_id,
                UserPushToken.ACTIVE_YN == 'Y'
            )
        )
        return list(result.scalars().all())

    async def find_token(self, user_id: str, push_token: str) -> Optional[UserPushToken]:
        """특정 푸쉬 토큰 조회"""
        result = await self.db.execute(
            select(UserPushToken).where(
                UserPushToken.USER_ID == user_id,
                UserPushToken.PUSH_TOKEN == push_token
            )
        )
        return result.scalar_one_or_none()

    async def save_token(self, token: UserPushToken) -> UserPushToken:
        """푸쉬 토큰 저장"""
        self.db.add(token)
        await self.db.flush()
        return token

    async def deactivate_token(self, user_id: str, push_token: str) -> None:
        """특정 사용자의 푸쉬 토큰 비활성화"""
        await self.db.execute(
            update(UserPushToken)
            .where(
                UserPushToken.USER_ID == user_id,
                UserPushToken.PUSH_TOKEN == push_token
            )
            .values(ACTIVE_YN='N', MOD_DT=datetime.now())
        )
        await self.db.flush()

    async def deactivate_tokens_by_push_token(self, push_token: str) -> None:
        """DeviceNotRegistered 에러 시 토큰 비활성화"""
        await self.db.execute(
            update(UserPushToken)
            .where(UserPushToken.PUSH_TOKEN == push_token)
            .values(ACTIVE_YN='N', MOD_DT=datetime.now())
        )
        await self.db.flush()