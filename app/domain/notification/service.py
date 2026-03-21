"""
Notification Service - 알림 설정 CRUD + 푸쉬 알림 전송
"""
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.domain.notification.repository import NotificationRepository
from app.domain.notification.entity import UserNotiSetting, UserPushToken
from app.domain.notification.schemas import (
    NotiSettingItem,
    NotiSettingUpdateRequest,
    PushTokenRegisterRequest,
    PushTokenDeleteRequest,
)
from app.external.expo_push import send_expo_push
from app.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class NotificationSettingService:
    """알림 설정 CRUD 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = NotificationRepository(db)

    async def get_settings(self, user_id: str) -> dict:
        """알림 설정 전체 조회 → {NOTI_TYPE: USE_YN} dict 반환"""
        try:
            settings = await self.repo.find_settings_by_user_id(user_id)
            result = {s.NOTI_TYPE: s.USE_YN for s in settings}
            return result
        except SQLAlchemyError as e:
            logger.error(f"알림 설정 조회 실패: {e}", exc_info=True)
            raise DatabaseError("알림 설정 조회에 실패했습니다")

    async def update_setting(self, user_id: str, request: NotiSettingUpdateRequest) -> dict:
        """알림 설정 개별 변경 (없으면 생성, 있으면 업데이트)"""
        try:
            setting = await self.repo.find_setting(user_id, request.NOTI_TYPE)
            if setting:
                setting.USE_YN = request.USE_YN
                setting.MOD_DT = datetime.now()
            else:
                setting = UserNotiSetting(
                    USER_ID=user_id,
                    NOTI_TYPE=request.NOTI_TYPE,
                    USE_YN=request.USE_YN
                )
                await self.repo.save_setting(setting)
            await self.db.commit()
            return NotiSettingItem.model_validate(setting).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"알림 설정 변경 실패: {e}", exc_info=True)
            raise DatabaseError("알림 설정 변경에 실패했습니다")

    async def register_push_token(self, user_id: str, request: PushTokenRegisterRequest) -> dict:
        """푸쉬 토큰 등록 (이미 존재하면 활성화)"""
        try:
            existing = await self.repo.find_token(user_id, request.PUSH_TOKEN)
            if existing:
                existing.ACTIVE_YN = 'Y'
                existing.DEVICE_TYPE = request.DEVICE_TYPE or existing.DEVICE_TYPE
                existing.MOD_DT = datetime.now()
            else:
                token = UserPushToken(
                    USER_ID=user_id,
                    PUSH_TOKEN=request.PUSH_TOKEN,
                    DEVICE_TYPE=request.DEVICE_TYPE
                )
                await self.repo.save_token(token)
            await self.db.commit()
            return {"success": True}
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"푸쉬 토큰 등록 실패: {e}", exc_info=True)
            raise DatabaseError("푸쉬 토큰 등록에 실패했습니다")

    async def delete_push_token(self, user_id: str, request: PushTokenDeleteRequest) -> dict:
        """푸쉬 토큰 비활성화 (로그아웃 시)"""
        try:
            await self.repo.deactivate_token(user_id, request.PUSH_TOKEN)
            await self.db.commit()
            return {"success": True}
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"푸쉬 토큰 삭제 실패: {e}", exc_info=True)
            raise DatabaseError("푸쉬 토큰 삭제에 실패했습니다")


class PushNotificationService:
    """푸쉬 알림 전송 서비스 (fire-and-forget 사용)"""

    @staticmethod
    async def send_notification(
        user_id: str,
        noti_type: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> None:
        """
        푸쉬 알림 전송 (범용)

        Args:
            user_id: 사용자 ID
            noti_type: 알림 유형 ('BUY', 'SELL', 'SIGNAL' 등)
            title: 알림 제목
            body: 알림 본문
            data: 앱에 전달할 추가 데이터

        독립 DB 세션 사용 → 호출자 트랜잭션과 완전 격리
        """
        from app.common.database import Database

        db = await Database.get_session()
        try:
            repo = NotificationRepository(db)

            # 해당 알림 유형이 활성화되어 있는지 확인
            if not await repo.is_enabled(user_id, noti_type):
                return

            tokens = await repo.find_active_tokens_by_user_id(user_id)
            if not tokens:
                logger.debug(f"[{user_id}] 활성 푸쉬 토큰 없음, 알림 건너뜀")
                return

            push_tokens = [t.PUSH_TOKEN for t in tokens]
            failed_tokens = await send_expo_push(
                push_tokens, title, body, data=data or {}
            )

            for token in failed_tokens:
                await repo.deactivate_tokens_by_push_token(token)
            if failed_tokens:
                await db.commit()

        except Exception as e:
            logger.error(f"[{user_id}] 푸쉬 알림 전송 실패 (type={noti_type}): {e}", exc_info=True)
        finally:
            await db.close()

    @staticmethod
    async def send_trade_notification(
        user_id: str,
        noti_type: str,
        st_code: str,
        qty: int,
        price: int,
        reasons: list[str] | None = None,
    ) -> None:
        """매매 체결 푸쉬 알림 (send_notification 래퍼)"""
        reason_text = reasons[0] if reasons else "매매 체결"
        is_buy = "매수" in reason_text
        type_label = "매수" if is_buy else "매도"
        amount = qty * price

        title = f"[{st_code}] {type_label} 체결"
        body = f"{qty}주 x {price:,}원 = {amount:,}원"
        if reasons:
            body += f"\n{', '.join(reasons)}"

        await PushNotificationService.send_notification(
            user_id=user_id,
            noti_type=noti_type,
            title=title,
            body=body,
            data={
                "type": "trade",
                "noti_type": noti_type,
                "st_code": st_code,
            },
        )