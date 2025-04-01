import asyncio
import logging

from api.LocalStockApi import get_target_price
from crud.StockCrud import insert_bulk_stock_hstr
from module.AESCrypto import decrypt
from module.DBConnection import get_db
from services.StockService import get_avg_stock_price
from services.SwingService import get_day_swing


async def trade_job():
    db = await get_db()
    swing_list = await get_day_swing(db)

    tasks = []
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

        # 이평선 계산
        avg = await get_avg_stock_price(db, swing.STOCK_CODE, swing.SHORT_TERM, swing.MEDIUM_TERM, swing.LONG_TERM)

        # 매수 or 매매 조건 확인

        # 매수 or 매매 실행

        # 비동기 작업을 백그라운드에서 실행
        task = asyncio.create_task(fetch("GET", api_url, body={}, headers=headers))
        tasks.append(task)
        await asyncio.gather(*tasks) #  모든 작업이 완료될 때까지 기다리는 함수
        print("#################1시간 스윙################")


async def day_collect_job():
    """
    일 데이터 수집
    stck_hgpr : 고가
    stck_lwpr : 저가
    stck_clspr : 종가
    acml_vol : 거래량
    """
    logging.debug("#################데이터 수집 시작################")
    db = await get_db()
    swing_stock_list = await get_day_swing(db)

    tasks = []
    for swing_stock in swing_stock_list:
        code = swing_stock.STOCK_CODE
        tasks.append(collect_and_insert_stock_data(db, code))

    await asyncio.gather(*tasks)
    logging.debug("#################데이터 수집 종료################")


async def collect_and_insert_stock_data(db, code):
    response = await get_target_price(code)
    response.set("STOCK_CODE", code)
    await insert_bulk_stock_hstr(db, response)