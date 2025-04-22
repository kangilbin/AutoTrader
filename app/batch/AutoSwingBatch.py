import asyncio
import logging
import pandas as pd
import numpy as np
from app.api.LocalStockApi import get_target_price
from app.crud.StockCrud import insert_bulk_stock_hstr
from app.module.AESCrypto import decrypt
from app.module.DBConnection import get_db
from app.module.FetchAPI import fetch
from app.services.StockService import get_day_stock_price
from app.services.SwingService import get_day_swing

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
        if swing.SIGNAL == "0":# 첫 매수 or 매도

            type, isTrue = detect_change(df, swing.SHORT_TERM, swing.MEDIUM_TERM)
            if type == "buy" and isTrue:
                # 매수 신호 발생
                print("매수 신호 발생")
                # 매수 로직 실행
                # 예: await buy_stock(swing.STOCK_CODE, amount)
            elif type == "sell" and isTrue:
                # 매도 신호 발생
                print("매도 신호 발생")
                # 매도 로직 실행
                # 예: await sell_stock(swing.STOCK_CODE, amount)

            else:
                # 매수/매도 신호 없음
                print("매수/매도 신호 없음")
                # 로직 실행 안함

            # SIGNAL 1로 변경
        elif swing.SIGNAL == "1": # 첫 매수 후 추가 매수
            detect_change(df, swing.MEDIUM_TERM, swing.LONG_TERM)
            # SIGNAL 2로 변경
        elif swing.SIGNAL == "2": # 첫 매도
            detect_buy_signal(df, swing.SHORT_TERM, swing.MEDIUM_TERM)
            # SIGNAL 3으로 변경
        else: # 전부 매도
            detect_buy_signal(df, swing.MEDIUM_TERM, swing.LONG_TERM)
            # SIGNAL 0로 변경


        # 3. 보조 지표 (ADX, OBV) 필터링

        # 매수 or 매매 실행

        # 비동기 작업을 백그라운드에서 실행
        #task = asyncio.create_task(fetch("GET", api_url, body={}, headers=headers))
        #tasks.append(task)
        #await asyncio.gather(*tasks) #  모든 작업이 완료될 때까지 기다리는 함수
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

    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    gain = pd.Series(gain, index=series.index)
    loss = pd.Series(loss, index=series.index)

    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ADX 계산
def adx(df):
    df = df.copy()
    df['prev_close'] = df['STCK_CLPR'].shift(1)

    tr1 = df['high'] - df['low']
    tr2 = abs(df['STCK_HGPR'] - df['prev_close'])
    tr3 = abs(df['low'] - df['prev_close'])
    df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    df['TR'] = pd.DataFrame({
        'TR1': df['STCK_HGPR'] - df['STCK_LWPR'],
        'TR2': abs(df['STCK_HGPR'] - df['STCK_CLPR'].shift(1)),
        'TR3': abs(df['STCK_LWPR'] - df['STCK_CLPR'].shift(1))
    }).max(axis=1)

    # +DM, -DM 계산
    df['+DM'] = df['high'] - df['high'].shift(1)
    df['-DM'] = df['low'].shift(1) - df['low']

    df['+DM'] = df['+DM'].where(df['+DM'] > 0, 0)
    df['-DM'] = df['-DM'].where(df['-DM'] > 0, 0)

    # 14일 이동평균 계산
    df['+DM_avg'] = df['+DM'].rolling(window=period).sum()
    df['-DM_avg'] = df['-DM'].rolling(window=period).sum()
    df['TR_avg'] = df['TR'].rolling(window=period).sum()

    # +DI, -DI 계산
    df['+DI'] = 100 * df['+DM_avg'] / df['TR_avg']
    df['-DI'] = 100 * df['-DM_avg'] / df['TR_avg']


    # DX 계산
    df['DX'] = 100 * abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI'])

    # ADX 계산
    df['adx'] = df['DX'].rolling(window=period).mean()

    return df['adx']



def sell_or_buy(df: pd.DataFrame, short_line: int, mid_line: int, long_line: int):
    """
    타점
    """
    df['ema_short'] = ema(df['STCK_CLPR'], short_line)
    df['ema_mid'] = ema(df['STCK_CLPR'], mid_line)
    df['ema_long'] = ema(df['STCK_CLPR'], long_line)
    df['rsi'] = rsi(df['STCK_CLPR'])

    #초단타 → (5, 13, 6) 중장기 → (20, 50, 15)
    df['macd'] = ema(df['STCK_CLPR'], 12) - ema(df['STCK_CLPR'], 26)
    df['signal'] = ema(df['macd'], 9)

    # 매수 조건
    # 단기-중기
    first_buy_cond1 = (df['ema_short'] > df['ema_mid']) & (df['ema_short'].shift(1) <= df['ema_mid'].shift(1)) & (df['STCK_CLPR'] > df['ema_mid'])
    first_buy_cond2 = (df['rsi'] > 30) & (df['rsi'].shift(1) <= 30)
    first_buy_cond3 = (df['macd'] > df['signal']) & (df['macd'].shift(1) <= df['signal'].shift(1))
    first_buy_signal = first_buy_cond1 & first_buy_cond2 & first_buy_cond3

    # 중기-장기
    second_buy_cond1 = (df['ema_mid'] > df['ema_long']) & (df['ema_mid'].shift(1) <= df['ema_long'].shift(1)) & (df['STCK_CLPR'] > df['ema_long'])
    second_buy_cond2 = (df['rsi'] > 40) & (df['rsi'].shift(1) <= 40)
    second_buy_cond3 = (df['macd'] > df['signal'])
    second_buy_signal = second_buy_cond1 & second_buy_cond2 & second_buy_cond3

    # 매도 조건
    # 단기-중기
    first_sell_cond = (df['ema_short'] < df['ema_mid']) & (df['ema_short'].shift(1) >= df['ema_mid'].shift(1))
    rsi_sell_cond = (df['rsi'] < 70) & (df['rsi'].shift(1) >= 70)
    macd_sell_cond = (df['macd'] < df['signal']) & (df['macd'].shift(1) >= df['signal'].shift(1))
    first_sell_signal = first_sell_cond & rsi_sell_cond & macd_sell_cond

    # 중기-장기
    second_sell_cond1 = (df['ema_mid'] < df['ema_long']) & (df['ema_mid'].shift(1) >= df['ema_long'].shift(1))
    second_sell_cond2 = (df['rsi'] < 60) & (df['rsi'].shift(1) >= 60)
    second_sell_cond3 = (df['macd'] < df['signal'])
    second_sell_signal = second_sell_cond1 & second_sell_cond2 & second_sell_cond3

    return first_buy_signal, first_sell_signal, second_buy_signal, second_sell_signal
