import asyncio
import logging
import pandas as pd
from api.LocalStockApi import get_target_price
from crud.StockCrud import insert_bulk_stock_hstr
from module.AESCrypto import decrypt
from module.DBConnection import get_db
from services.StockService import get_day_stock_price
from services.SwingService import get_day_swing

async def trade_job():
    db = await get_db()
    swing_list = await get_day_swing(db)

    tasks = []
    for swing in swing_list:
        print("#################스윙 시작################")
        print(swing.SWING_ID, swing.STOCK_CODE, decrypt(swing.APP_KEY), decrypt(swing.SECRET_KEY))

        # 1. 이평선 조회
        price_days = await get_day_stock_price(db, swing.ST_CODE, swing.LONG_TERM)
        df = pd.DataFrame([price_day.__dict__ for price_day in price_days])
        df = df.drop(columns=["_sa_instance_state"], errors="ignore")

        # 2. 주 지표(이평선, MACD, RSI) 매매 매도 타점 확인
        # 2-1. 단기 이평선, 중기 이평선, 장기 이평선

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


# 지수 이동 평균(EMA) 계산 함수
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# 상대 강도 지수(RSI) 계산 함수
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def detect_trend_change(df: pd.DataFrame, short_line: int, long_line: int) -> pd.DataFrame:
    """
    이평선 매매 타점
    """
    df["ema_short"] = ema(df["STCK_CLPR"], short_line)
    df["ema_long"] = ema(df["STCK_CLPR"], long_line)

    # MACD(short_line,long_line) 계산
    df["macd"] = df["ema_short"] - df["ema_long"]
    df["signal"] = ema(df["macd"], 9)  # MACD의 9일 시그널선

    # 지수 이평선 : EMA(ema_short) > EMA(ema_long) 골든크로스 발생 체크
    df["ema_golden_cross"] = (df["ema_short"] > df["ema_long"]) & (df["ema_short"].shift(1) <= df["ema_long"].shift(1))

    # MACD(ema_short,ema_long) 골든크로스 발생 체크
    df["macd_golden_cross"] = (df["macd"] > df["signal"]) & (df["macd"].shift(1) <= df["signal"].shift(1))

    # 매수 타점 (EMA 골든크로스 & MACD 골든크로스 동시 발생)
    df["buy_signal"] = df["ema_golden_cross"] & df["macd_golden_cross"]

    return df

