"""
Notification API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.notification.service import NotificationSettingService
from app.domain.notification.schemas import (
    NotiSettingUpdateRequest,
    PushTokenRegisterRequest,
    PushTokenDeleteRequest,
)

router = APIRouter(prefix="/users", tags=["Notification"])


def get_noti_service(db: AsyncSession = Depends(get_db)) -> NotificationSettingService:
    """NotificationSettingService 의존성 주입"""
    return NotificationSettingService(db)


@router.get("/notification-settings")
async def get_notification_settings(
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """알림 설정 전체 조회"""
    result = await service.get_settings(user_id)
    return success_response("알림 설정 조회 완료", result)


@router.put("/notification-settings")
async def update_notification_setting(
    request: NotiSettingUpdateRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """알림 설정 개별 변경"""
    result = await service.update_setting(user_id, request)
    return success_response("알림 설정 변경 완료", result)


@router.post("/push-token")
async def register_push_token(
    request: PushTokenRegisterRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """푸쉬 토큰 등록"""
    result = await service.register_push_token(user_id, request)
    return success_response("푸쉬 토큰 등록 완료", result)


@router.delete("/push-token")
async def delete_push_token(
    request: PushTokenDeleteRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """푸쉬 토큰 삭제"""
    result = await service.delete_push_token(user_id, request)
    return success_response("푸쉬 토큰 삭제 완료", result)