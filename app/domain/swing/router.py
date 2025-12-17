"""
Swing API Router
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.domain.swing.service import SwingService
from app.domain.swing.schemas import SwingCreateRequest

router = APIRouter(prefix="/swing", tags=["Swing"])


def get_swing_service(db: AsyncSession = Depends(get_db)) -> SwingService:
    """SwingService 의존성 주입"""
    return SwingService(db)


@router.post("")
async def register_swing(
    request: SwingCreateRequest,
    service: Annotated[SwingService, Depends(get_swing_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """스윙 전략 등록"""
    response = await service.create_swing(user_id, request)
    return {"message": "스윙 등록 완료", "data": response}


@router.get("/list")
async def list_swing_mapping(
    account_no: str = Query(..., description="계좌번호"),
    service: Annotated[SwingService, Depends(get_swing_service)] = None,
    user_id: Annotated[str, Depends(get_current_user)] = None
):
    """스윙 목록 매핑 조회"""
    result = await service.mapping_swing(user_id, account_no)
    return {"message": "스윙 매핑 완료", "data": result}


@router.get("/{swing_id}")
async def get_swing(
    swing_id: int,
    service: Annotated[SwingService, Depends(get_swing_service)]
):
    """스윙 전략 조회"""
    result = await service.get_swing(swing_id)
    return {"message": "스윙 조회 완료", "data": result}


@router.delete("/{swing_id}")
async def delete_swing(
    swing_id: int,
    service: Annotated[SwingService, Depends(get_swing_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """스윙 전략 삭제"""
    await service.delete_swing(swing_id)
    return {"message": "스윙 삭제 완료"}