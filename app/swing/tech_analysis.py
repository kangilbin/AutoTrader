import pandas as pd
import numpy as np
import talib as ta



# ADX 계산(추세의 강도)
def adx(df, period: int = 14):
    df = df.copy()
    df['prev_close'] = df['STCK_CLPR'].shift(1)

    # TR 계산
    tr1 = df['STCK_HGPR'] - df['STCK_LWPR']
    tr2 = abs(df['STCK_HGPR'] - df['prev_close'])
    tr3 = abs(df['STCK_LWPR'] - df['prev_close'])
    df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM, -DM 계산
    df['+DM'] = np.where((df['STCK_HGPR'] - df['STCK_HGPR'].shift(1)) > (df['STCK_LWPR'].shift(1) - df['STCK_LWPR']),
                         np.maximum(df['STCK_HGPR'] - df['STCK_HGPR'].shift(1), 0), 0)
    df['-DM'] = np.where((df['STCK_LWPR'].shift(1) - df['STCK_LWPR']) > (df['STCK_HGPR'] - df['STCK_HGPR'].shift(1)),
                         np.maximum(df['STCK_LWPR'].shift(1) - df['STCK_LWPR'], 0), 0)

    # 14일 이동평균 계산
    tr_n = df['TR'].rolling(window=period).sum()
    plus_dm_n = df['+DM'].rolling(window=period).sum()
    minus_dm_n = df['-DM'].rolling(window=period).sum()

    plus_di = 100 * (plus_dm_n / tr_n)
    minus_di = 100 * (minus_dm_n / tr_n)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx_val = dx.rolling(window=period).mean()

    return plus_di.iloc[-1], minus_di.iloc[-1], adx_val.iloc[-1]

# OBV 거래량 흐름
def obv(df):
    obv_val = np.where(df['STCK_CLPR'] > df['STCK_CLPR'].shift(1), df['ACML_VOL'],
                         np.where(df['STCK_CLPR'] < df['STCK_CLPR'].shift(1), -df['ACML_VOL'], 0))
    return pd.Series(obv_val, index=df.index).cumsum()

def sell_or_buy(df: pd.DataFrame, short_line: int, mid_line: int, long_line: int) -> tuple:
    """
    매수/매도 신호를 검출하는 함수

    Args:
        df (pd.DataFrame): 주가 데이터
        short_line (int): 단기 이동평균선 기간
        mid_line (int): 중기 이동평균선 기간
        long_line (int): 장기 이동평균선 기간
        buy_price (float, optional): 매수 가격. Defaults to None.
        rsi_period (int, optional): RSI 계산 기간. Defaults to 14.
        stop_loss_rate (float, optional): 손절 비율. Defaults to -0.05.

    Returns:
        tuple: (매수신호1, 매수신호2, 매도신호1, 매도신호2, 손절신호)
    """
    # numpy 배열로 변환
    high = df["STCK_HGPR"].values
    low = df["STCK_LWPR"].values
    close = df["STCK_CLPR"].values
    volume = df["ACML_VOL"].values

    # 지수 이동 평균(EMA)
    df["ema_short"] = ta.EMA(close, short_line)
    df["ema_mid"] = ta.EMA(close, mid_line)
    df["ema_long"] = ta.EMA(close, long_line)

    #  DMI (+DI, -DI, ADX)
    df["plus_di"] = ta.PLUS_DI(high, low, close, timeperiod=14)
    df["minus_di"] = ta.MINUS_DI(high, low, close, timeperiod=14)
    df["adx"] = ta.ADX(high, low, close, timeperiod=14)

    # RSI
    df["rsi"] = ta.RSI(close, timeperiod=14)

    # OBV
    df["obv"] = ta.OBV(close, volume)

    first_ema_gap_now = abs(df["ema_mid"].iloc[-1] - df["ema_short"].iloc[-1])
    first_ema_gap_prev = abs(df["ema_mid"].iloc[-2] - df["ema_short"].iloc[-2])

    # 이평선 수렴(1% 이하로 수렴) 매도 신호
    #first_ema_buy_cond = first_ema_gap_now < first_ema_gap_prev and first_ema_gap_now < ema_mid.iloc[-1] * 0.01 and ema_short.iloc[-1] > ema_mid.iloc[-1]
    first_ema_sell_cond = (
            first_ema_gap_now > first_ema_gap_prev and
            first_ema_gap_now / df["ema_mid"].iloc[-1] < 0.01 and
            df["ema_short"].iloc[-1] < df["ema_mid"].iloc[-1]
    )

    rsi_now, rsi_prev = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
    rsi_diff = rsi_now - rsi_prev
    #rsi_strong_buy_cond = rsi_diff > 5 and rsi_prev < 30 and rsi_now > 30 # RSI가 급격히 반등할 때

    # RSI 매도 신호
    rsi_sell_cond = (
            rsi_diff < -5 and
            rsi_prev > 70 and
            rsi_now < 70
    )
    # RSI 매수 신호
    rsi_buy_cond = (
            rsi_diff > 5 and  # 오늘 RSI가 전일보다 급등
            rsi_prev < 30 and  # 전일 RSI가 과매도 구간
            rsi_now > 30  # 오늘 RSI가 과매도 구간 위로 올라옴
    )
    obv_diff = df['obv'].diff()
    obv_diff_avg = obv_diff.rolling(7).mean()  # 최근 7일 평균 변화량
    obv_cond = obv_diff.iloc[-1] > obv_diff_avg.iloc[-1] * 1.5  # 평균 대비 1.5배 이상

    adx_now = df["adx"].iloc[-1]
    first_adx_buy_cond = (adx_now> 25) & (df["plus_di"].iloc[-1] > df["minus_di"].iloc[-1])
    second_adx_buy_cond = (adx_now > 30) & (df["plus_di"].iloc[-1] > df["minus_di"].iloc[-1])

    # 매수 단기-중기 (이평선 + RSI + DMI + OBV)
    first_buy_cond = (df["ema_short"].iloc[-1] > df["ema_mid"].iloc[-1]) & (df["ema_short"].iloc[-2] <=  df["ema_mid"].iloc[-2])
    first_buy_signal = first_buy_cond & rsi_buy_cond & first_adx_buy_cond & obv_cond

    # 매수 중기-장기 (이평선 + DMI + OBV)
    second_buy_cond1 = (df["ema_mid"].iloc[-1] > df["ema_long"].iloc[-1]) & (df["ema_long"].iloc[-2] <= df["ema_mid"].iloc[-2])
    second_buy_signal = second_buy_cond1 & second_adx_buy_cond & obv_cond

    first_adx_sell_cond = (adx_now > 25) & (df["plus_di"].iloc[-1] < df["minus_di"].iloc[-1])
    second_adx_sell_cond = (adx_now > 30) & (df["plus_di"].iloc[-1] < df["minus_di"].iloc[-1])

    # 매도 단기-중기
    first_sell_cond1 = (df["ema_short"].iloc[-1] < df["ema_mid"].iloc[-1]) & (df["ema_short"].iloc[-2] >= df["ema_mid"].iloc[-2])
    first_sell_signal = first_sell_cond1 & rsi_sell_cond & first_adx_sell_cond & obv_cond
    first_sell_signal = first_sell_signal | first_ema_sell_cond

    # 매도 중기-장기
    second_sell_cond = (df["ema_mid"].iloc[-1] < df["ema_long"].iloc[-1]) & (df["ema_mid"].iloc[-2] >= df["ema_long"].iloc[-2])
    second_sell_signal = second_sell_cond & second_adx_sell_cond

    # 손절 조건
    # stop_loss_signal = False
    # if buy_price is not None:
    #     stop_loss_price = buy_price * (1 + stop_loss_rate)
    #     stop_loss_signal = df['STCK_CLPR'].iloc[-1] <= stop_loss_price
    return first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal