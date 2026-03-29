"""
Account API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.account.service import AccountService
from app.domain.account.schemas import AccountCreateRequest, AccountVerifyRequest

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
    return success_response("계좌 리스트 조회", account_list)

@router.post("/verify")
async def verify_account(
    request: AccountVerifyRequest,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌번호 검증 - KIS API로 유효성 확인"""
    result = await service.verify_account(user_id, request.AUTH_ID, request.ACCOUNT_NO)
    return success_response("계좌번호 검증 성공", result)


@router.post("")
async def register_account(
    request: AccountCreateRequest,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 등록"""
    account_info = await service.create_account(user_id, request)
    return success_response("계좌 등록 성공", account_info)

@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """계좌 삭제"""
    await service.delete_account(account_id)
    return success_response("계좌 삭제 성공")