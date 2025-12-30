"""
Account API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.domain.account.service import AccountService
from app.domain.account.schemas import AccountCreateRequest
from app.external.kis_api import get_stock_balance

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def get_account_service(db: AsyncSession = Depends(get_db)) -> AccountService:
    """AccountService 의존성 주입"""
    return AccountService(db)


@router.get("")
async def list_accounts(
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 목록 조회"""
    account_list = await service.get_accounts(user_id)
    return {"message": "계좌 리스트 조회", "data": account_list}


@router.post("")
async def register_account(
    request: AccountCreateRequest,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 등록"""
    account_info = await service.create_account(user_id, request)
    return {"message": "계좌 등록 성공", "data": account_info}


@router.get("/balance")
async def get_balance(
    user_id: Annotated[str, Depends(get_current_user)]
):
    """잔고 조회"""
    balance = await get_stock_balance(user_id)
    return {"message": "계좌 잔고 조회", "data": balance}


@router.get("/{account_id}")
async def get_account_detail(
    account_id: str,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 상세 조회"""
    account_info = await service.get_account(account_id, user_id)
    return {"message": "계좌 조회", "data": account_info}


@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 삭제"""
    await service.delete_account(account_id)
    return {"message": "계좌 삭제 성공"}