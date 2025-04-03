import asyncio
import logging

from api.LocalStockApi import get_target_price
from crud.StockCrud import insert_bulk_stock_hstr
from module.AESCrypto import decrypt
from module.DBConnection import get_db
from services.StockService import get_avg_stock_price
from services.SwingService import get_day_swing
import pandas as pd

async def trade_job():
    db = await get_db()
    swing_list = await get_day_swing(db)

    tasks = []
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

        # 1. 이평선 조회
        avg = await get_avg_stock_price(db, swing.STOCK_CODE, swing.SHORT_TERM, swing.MEDIUM_TERM, swing.LONG_TERM)

        # 2. 주 지표(이평선, MACD, RSI) 매매 매도 타점 확인
        if swing.CROSS_TYPE == "R":
            if avg["short_avg"] > avg["medium_avg"] and avg["medium_avg"] > avg["long_avg"]:
                # 매수 조건
                print("매수 조건 충족")
                api_url = f"https://api.example.com/buy?code={swing.STOCK_CODE}&amount={swing.SWING_AMOUNT}"
            elif avg["short_avg"] < avg["medium_avg"] and avg["medium_avg"] < avg["long_avg"]:
                # 매도 조건
                print("매도 조건 충족")
                api_url = f"https://api.example.com/sell?code={swing.STOCK_CODE}&amount={swing.SWING_AMOUNT}"

        # 3. 보조 지표 (ADX, OBV) 필터링

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


def detect_trend_change(stock_data: dict) -> dict:
    """
    단장중 -> 정배열 전환 및 정배열 완성 시점 판별
    """
    if not stock_data:
        return {"error": "No data available"}

    today_short = stock_data["today_short_ma"]
    today_mid = stock_data["today_mid_ma"]
    today_long = stock_data["today_long_ma"]

    yesterday_short = stock_data["yesterday_short_ma"]
    yesterday_mid = stock_data["yesterday_mid_ma"]
    yesterday_long = stock_data["yesterday_long_ma"]

    result = {
        "date": stock_data["today_date"],
        "is_trend_change": False,  # 단장중 -> 정배열 전환 여부
        "is_complete_trend": False  # 정배열 완성 여부
    }

    # 단장중 -> 정배열 전환 판별
    if yesterday_short < yesterday_mid or yesterday_mid < yesterday_long:
        if today_short > today_mid and today_mid > today_long:
            result["is_trend_change"] = True

    # 정배열 완성 판별 (중기선이 장기선을 상향 돌파)
    if yesterday_mid < yesterday_long and today_mid > today_long:
        result["is_complete_trend"] = True

    return result

