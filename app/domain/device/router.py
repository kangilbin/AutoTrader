"""
Device Router - 디바이스 관리 API
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.domain.device.service import DeviceService
from app.domain.device.schemas import DeviceCreateRequest, DeviceUpdateRequest
from app.core.response import success_response

router = APIRouter(prefix="/device", tags=["Device"])


@router.post("", summary="디바이스 등록")
async def create_device(
    request: DeviceCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """디바이스 등록"""
    service = DeviceService(db)
    result = await service.create_device(request)
    return success_response("디바이스 등록 완료", result)


@router.get("", summary="디바이스 목록 조회")
async def get_devices(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """디바이스 목록 조회"""
    service = DeviceService(db)
    result = await service.get_all_devices()
    return success_response("디바이스 목록 조회 완료", result)


@router.get("/{device_id}", summary="디바이스 상세 조회")
async def get_device(
    device_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """디바이스 상세 조회"""
    service = DeviceService(db)
    result = await service.get_device(device_id)
    return success_response("디바이스 조회 완료", result)


@router.put("/{device_id}", summary="디바이스 수정")
async def update_device(
    device_id: str,
    request: DeviceUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """디바이스 수정 (활성화/비활성화 포함)"""
    service = DeviceService(db)
    result = await service.update_device(device_id, request)
    return success_response("디바이스 수정 완료", result)


@router.delete("/{device_id}", summary="디바이스 삭제")
async def delete_device(
    device_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """디바이스 삭제"""
    service = DeviceService(db)
    await service.delete_device(device_id)
    return success_response("디바이스 삭제 완료")