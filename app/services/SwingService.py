from sqlalchemy.ext.asyncio import AsyncSession
from app.services.StockService import get_stock_info
from app.api.LocalStockApi import get_stock_data
from app.crud.StockCrud import update_stock, insert_bulk_stock_hstr
from app.crud.SwingCrud import insert_swing, select_swing, select_swing_account, list_day_swing, update_swing, delete_swing
from app.model.schemas.SwingModel import SwingCreate
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.prop.constants import KST
import asyncio
import logging

MAX_ITEMS_PER_REQUEST = 100


# 스윙 전략 등록
async def create_swing(db: AsyncSession, swing_data: SwingCreate):
    # 스윙 등록
    await insert_swing(db, swing_data)

    stock_data = await get_stock_info(db, swing_data.ST_CODE)

    # 데이터 적재 여부
    if stock_data["DATA_YN"] == 'N':
        # 3년 데이터 적재
        await fetch_and_store_3_years_data(db, swing_data.USER_ID, swing_data.ST_CODE)

        stock_data["MOD_DT"] = datetime.now(KST)
        await update_stock(db, stock_data)


# 스윙 전략 수정
async def mod_swing(db: AsyncSession, swing_data: SwingCreate):
    swing_data.MOD_DT = datetime.now(KST)
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


# 3년 데이터 적재
async def fetch_and_store_3_years_data(db: AsyncSession, user_id: str, code: str):
    total_cnt = 0
    end_date = datetime.now(KST).date()  # 오늘 날짜
    start_date = end_date - relativedelta(years=3)  # 3년 전 날짜
    current_date = start_date

    while current_date < end_date:
        next_date = current_date + relativedelta(days=MAX_ITEMS_PER_REQUEST)
        if next_date > end_date:
            next_date = end_date

        print(f"Fetching data from {current_date} to {next_date}")

        response = await get_stock_data(user_id, code, current_date.strftime('%Y%m%d'), next_date.strftime('%Y%m%d'))

        # Bulk insert 저장
        # response 데이터를 List[dict] 형태로 변환하여 한번에 insert
        cnt = await insert_bulk_stock_hstr(db, response)
        total_cnt += cnt

        current_date = next_date

        await asyncio.sleep(1)  # API 서버 부하 방지

    logging.debug(f"Stock data inserted: {total_cnt}")
