from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text, and_
from model.TableCreate import Swing
from model.schemas.SwingModel import SwingCreate, SwingResponse
from sqlalchemy.exc import SQLAlchemyError
import logging


# 스윙 생성
async def insert_swing(db: AsyncSession, swing_data: SwingCreate):
    try:
        # 새로운 사용자 객체 생성
        swing_info = Swing(ACCOUNT_NO=swing_data.ACCOUNT_NO, USER_ID=swing_data.USER_ID, ST_CODE=swing_data.ST_CODE, USE_YN=swing_data.USE_YN,
                        SWING_AMOUNT=swing_data.SWING_AMOUNT, SWING_TYPE=swing_data.SWING_TYPE, SHORT_TERM=swing_data.SHORT_TERM,
                        MEDIUM_TERM=swing_data.MEDIUM_TERM, LONG_TERM=swing_data.LONG_TERM, BUY_RATIO=swing_data.BUY_RATIO,
                        SELL_RATIO=swing_data.SELL_RATIO, CROSS_TYPE=swing_data.CROSS_TYPE)
        db.add(swing_info)
        await db.commit()
        await db.refresh(swing_info)
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return SwingResponse.from_orm(swing_info)


# 스윙 조회
async def select_swing(db: AsyncSession, swing_id: int):
    try:
        query = select(Swing).filter(Swing.SWING_ID == swing_id)
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars()


# 스윙 조회(계좌 번호)
async def select_swing_account(db: AsyncSession, user_id, account_no: str):
    try:
        query = select(Swing).filter(
            and_(Swing.USER_ID == user_id, Swing.ACCOUNT_NO == account_no)
        )
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars().all()


# 모든 스윙 조회(사용중)
async def list_all_swing(db: AsyncSession):
    try:
        query = text("SELECT ST.*, U.API_KEY, U.SECRET_KEY "
                     "FROM SWING_TRADE ST "
                     "LEFT JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
                     "LEFT JOIN USER U ON A.USER_ID = U.USER_ID "
                     "WHERE ST.USE_YN = 'Y'")
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.all()


# 스윙 업데이트
async def update_swing(db: AsyncSession, swing_data: SwingCreate):
    try:
        query = (
            update(Swing)
            .filter(Swing.SWING_ID == swing_data.SWING_ID)
            .values(**swing_data.dict())
            .execution_options(synchronize_session=False)
        )
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    # 업데이트 후 db에서 다시 가져오기
    return await db.get(Swing, swing_data.SWING_ID)


# 스윙 삭제
async def delete_swing(db: AsyncSession, swing_id: int):
    try:
        query = delete(Swing).filter(Swing.SWING_ID == swing_id)
        result = await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise

    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return swing_id
