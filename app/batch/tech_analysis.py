import pandas as pd
import numpy as np

# 지수 이동 평균(EMA) 계산 함수
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# rsi 계산 (모멘텀과 과매도/과매수)
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

def sell_or_buy(df: pd.DataFrame, short_line: int, mid_line: int, long_line: int, buy_price: float = None, stop_loss_rate: float = -0.05) -> tuple:
    """
    타점
    """
    ema_short = ema(df['STCK_CLPR'], short_line)
    ema_mid = ema(df['STCK_CLPR'], mid_line)
    ema_long = ema(df['STCK_CLPR'], long_line)
    first_ema_gap_now = ema_mid.iloc[-1] - ema_short.iloc[-1]
    first_ema_gap_prev = ema_mid.iloc[-3] - ema_short.iloc[-3]

    # 이평선 수렴(1% 이하로 수렴)
    first_ema_buy_cond = first_ema_gap_now < first_ema_gap_prev and first_ema_gap_now < ema_mid.iloc[-1] * 0.01 and ema_short.iloc[-1] > ema_mid.iloc[-1]
    first_ema_sell_cond = first_ema_gap_now > first_ema_gap_prev and first_ema_gap_now < ema_mid.iloc[-1] * 0.01 and ema_short.iloc[-1] < ema_mid.iloc[-1]

    rsi_vals = rsi(df['STCK_CLPR'])
    rsi_now, rsi_prev = rsi_vals.iloc[-1], rsi_vals.iloc[-2]
    rsi_diff = rsi_now - rsi_prev
    rsi_strong_buy_cond = rsi_diff > 5 and rsi_prev < 30 and rsi_now > 30 # RSI가 급격히 반등할 때
    rsi_strong_sell_cond = rsi_diff < -5 and rsi_prev > 70 and rsi_now < 70 # RSI가 급격히 하락할 때


    plus_di, minus_di, adx_val = adx(df)
    obv_vals = obv(df)
    obv_ma3 = obv_vals.rolling(3).mean()
    obv_ma7 = obv_vals.rolling(7).mean()

    first_adx_buy_cond = (adx_val.iloc[-1] > 25) & (plus_di > minus_di)
    second_adx_buy_cond = (adx_val.iloc[-1] > 30) & (plus_di > minus_di)
    first_obv_buy_cond = obv_vals.iloc[-1] > obv_ma3.iloc[-1]
    second_obv_buy_cond = obv_vals.iloc[-1] > obv_ma7.iloc[-1]

    # 단기-중기
    first_buy_cond1 = (ema_short.iloc[-1] > ema_mid.iloc[-1]) & (ema_short.iloc[-2] <= ema_mid.iloc[-2])
    first_buy_cond2 = (rsi_now > 30) & (rsi_prev <= 30)
    first_buy_signal1 = first_buy_cond1 & first_buy_cond2 & first_adx_buy_cond & first_obv_buy_cond
    # first_buy_signal2 = first_ema_buy_cond & rsi_strong_buy_cond & first_adx_buy_cond 급등 신호
    # first_buy_signal = first_buy_signal1 | first_buy_signal2
    first_buy_signal = first_buy_signal1

    # 중기-장기
    second_buy_cond1 = (ema_mid.iloc[-1] > ema_long.iloc[-1]) & (ema_mid.iloc[-2] <= ema_long.iloc[-2])
    second_buy_cond2 = (rsi_now > 40) & (rsi_prev <= 40)
    second_buy_signal = second_buy_cond1 & second_buy_cond2 & second_adx_buy_cond & second_obv_buy_cond

    first_adx_sell_cond = (adx_val.iloc[-1] > 25) & (plus_di < minus_di)
    second_adx_sell_cond = (adx_val.iloc[-1] > 30) & (plus_di < minus_di)
    first_obv_sell_cond = obv_vals.iloc[-1] < obv_ma3.iloc[-1]
    second_obv_sell_cond = obv_vals.iloc[-1] < obv_ma7.iloc[-1]

    # 익절 단기-중기
    first_sell_cond1 = (ema_short.iloc[-1] < ema_mid.iloc[-1]) & (ema_short.iloc[-2] >= ema_mid.iloc[-2])
    first_sell_cond2 = (rsi_now < 70) & (rsi_prev >= 70)
    first_sell_signal1 = first_sell_cond1 & first_sell_cond2 & first_adx_sell_cond & first_obv_sell_cond
    first_sell_signal2 = first_ema_sell_cond & rsi_strong_sell_cond
    first_sell_signal = first_sell_signal1 | first_sell_signal2

    # 익절 중기-장기
    second_sell_cond1 = (ema_mid.iloc[-1] < ema_long.iloc[-1]) & (ema_mid.iloc[-2] >= ema_long.iloc[-2])
    second_sell_cond2 = (rsi_now < 60) & (rsi_prev >= 60)
    second_sell_signal = second_sell_cond1 & second_sell_cond2 & second_adx_sell_cond & second_obv_sell_cond

    # 손절 조건
    stop_loss_signal = False
    if buy_price is not None:
        stop_loss_price = buy_price * (1 + stop_loss_rate)
        stop_loss_signal = df['STCK_CLPR'].iloc[-1] <= stop_loss_price
    return first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal