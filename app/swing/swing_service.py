from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.swing.tech_analysis import sell_or_buy
from app.stock.stock_service import get_stock_info
from app.stock.stock_crud import update_stock
from app.swing.swing_crud import insert_swing, select_swing, select_swing_account, list_day_swing, update_swing, delete_swing
from app.swing.swing_model import SwingCreate
from app.stock.stock_data_batch import fetch_and_store_3_years_data, get_batch_status as get_stock_batch_status
from datetime import datetime, UTC
from app.stock.stock_service import get_day_stock_price
import pandas as pd
import asyncio


# 스윙 전략 등록
async def create_swing(db: AsyncSession, swing_data: SwingCreate):
    # 스윙 등록
    await insert_swing(db, swing_data)

    stock_data = await get_stock_info(db, swing_data.ST_CODE)

    # 데이터 적재 여부
    if stock_data["DATA_YN"] != 'Y':
        # 3년 데이터 적재를 백그라운드에서 실행 (기존 DB 세션 전달)
        asyncio.create_task(fetch_and_store_3_years_data(swing_data.USER_ID, swing_data.ST_CODE, stock_data))

        # 스톡 데이터 상태를 즉시 업데이트 (백그라운드 작업 시작을 표시)
        stock_data["MOD_DT"] = datetime.now(UTC)
        await update_stock(db, stock_data)


# 스윙 전략 수정
async def mod_swing(db: AsyncSession, swing_data: SwingCreate):
    swing_data.MOD_DT = datetime.now(UTC)
    return await update_swing(db, swing_data)


# 스윙 전략 조회
async def get_swing(db: AsyncSession, swing_id: int):
    return await select_swing(db, swing_id)


# 스윙 전략 조회(계좌 번호)
async def get_swing_account_no(db: AsyncSession, user_id: str, account_no: str):
    return await select_swing_account(db, user_id, account_no)


# 모든 등록된 스윙 조회
async def get_day_swing(db: AsyncSession):
    return await list_day_swing(db)


# 스윙 전략 삭제
async def remove_swing(db: AsyncSession, swing_id: int):
    return await delete_swing(db, swing_id)


# 배치 작업 상태 조회
async def get_batch_status(db: AsyncSession, code: str):
    """
    배치 작업의 현재 상태를 조회하는 함수
    
    Args:
        db: 데이터베이스 세션
        code: 주식 코드
    
    Returns:
        dict: 배치 작업 상태 정보
    """
    return await get_stock_batch_status(db, code)
