from sqlalchemy.ext.asyncio import AsyncSession

from app.api.local_stock_api import get_stock_balance
from app.stock.stock_service import get_stock_info
from app.stock.stock_crud import update_stock
from app.swing.swing_crud import insert_swing, select_swing, select_swing_account, list_day_swing, update_swing, delete_swing, insert_swing_option
from app.swing.swing_model import SwingCreate
from app.stock.stock_data_batch import fetch_and_store_3_years_data, get_batch_status as get_stock_batch_status
from datetime import datetime
import asyncio


# 스윙 전략 등록
async def create_swing(db: AsyncSession, swing_data: SwingCreate):
    # 스윙 등록
    try:
        # 스윙
        await insert_swing(db, swing_data)
        if swing_data.SWING_TYPE == 'A':
            await insert_swing_option(db, swing_data)
        await db.commit()

    except Exception as e:
        await db.rollback()  # 실패 시 모두 롤백
        raise

    stock_data = await get_stock_info(db, swing_data.ST_CODE)

    # 데이터 적재 여부
    if stock_data["DATA_YN"] != 'Y':
        # 3년 데이터 적재를 백그라운드에서 실행 (기존 DB 세션 전달)
        asyncio.create_task(fetch_and_store_3_years_data(swing_data.USER_ID, swing_data.ST_CODE, stock_data))

        # 스톡 데이터 상태를 즉시 업데이트 (백그라운드 작업 시작을 표시)
        stock_data["MOD_DT"] = datetime.now()
        await update_stock(db, stock_data)



# 스윙 전략 수정
async def mod_swing(db: AsyncSession, swing_data: SwingCreate):
    swing_data.MOD_DT = datetime.now()
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

async def mapping_swing(db: AsyncSession, user_id: str, account_no: str):
    """
    swing_list와 buy_list를 비교하여 새로운 스윙을 등록하거나 기존 데이터를 merge합니다.

    Args:
        db: 데이터베이스 세션
        user_id: 사용자 ID
        account_no: 계좌 번호

    Returns:
        dict: 처리 결과 (신규 등록 수, 업데이트 수, 스킵 수)
    """
    # 기존 스윙 목록 조회
    swing_list = await select_swing_account(db, user_id, account_no)

    # 보유 주식 목록 조회
    buy_list = await get_stock_balance(user_id)

    # swing_list를 종목 코드 기준으로 딕셔너리로 변환
    swing_dict = {swing.ST_CODE: swing for swing in swing_list}

    # 처리 결과 카운터
    new_count = 0
    update_count = 0
    skip_count = 0
    results = []

    # buy_list의 각 종목을 확인
    for buy_item in buy_list:
        st_code = buy_item.get("pdno")  # API 응답에서 종목 코드

        if not st_code:
            continue

        # swing_list에 없는 종목이면 새롭게 등록
        if st_code not in swing_dict:
            # 새 스윙 데이터 생성
            new_swing = SwingCreate(
                ST_CODE=st_code,
                USER_ID=user_id,
                ACCOUNT_NO=account_no,
                USE_YN='N',
                SWING_AMOUNT= 0,  # 매입금액
                SWING_TYPE='B',  # 기본값: 이평선
            )
            swing_result = await insert_swing(db, new_swing)
            await db.commit()
            result_data = {
                **swing_result,
                "ST_NM": buy_item.get("prdt_name"), # 상품명
                "QTY": buy_item.get("hldg_qty"), # 보유 수량
                ## 평균금액, 수익률, 등등 값 구해서 반환
            }
            # 여기서 스윙 목록에 추가하는 로직 작성
            results.append(result_data)
        else:
            # 이미 있으면 merge (필요시 업데이트)
            existing_swing = swing_dict[st_code]

    # 커밋

    return {
        "new_count": new_count,
        "update_count": update_count,
        "skip_count": skip_count,
        "total_processed": len(buy_list)
    }




