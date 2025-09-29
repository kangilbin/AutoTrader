import pandas as pd
import talib as ta



def ema_swing_signals(df: pd.DataFrame, short_line: int, mid_line: int, long_line: int) -> tuple[bool, bool, bool, bool, bool]:
    """
    스윙 매매 특화 신호 생성:
    - 1차 매수: 단기 > 중기 상태 + (RSI / STOCH K/D / MACD) 컨펌 2/3 + ADX 하한 + OBV 양호
    - 2차 매수: 중기 > 장기 상태 + (RSI / STOCH K/D / MACD) 컨펌 2/3 + OBV 양호
    - 1차/2차 매도: 단<중 / 중<장 상태 + (RSI / STOCH K/D / MACD) 컨펌 2/3 + OBV 약화
    - 손절: ATR 기반 Chandelier Exit
    Returns: (buy1, buy2, sell1, sell2, stop_loss)
    """
    # 날짜 정렬 및 숫자형 보정
    if "STCK_BSOP_DATE" in df.columns:
        df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    for col in ["STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # numpy 배열로 변환
    high = df["STCK_HGPR"].values
    low = df["STCK_LWPR"].values
    close = df["STCK_CLPR"].values
    volume = df["ACML_VOL"].values

    # 지수 이동 평균(EMA)
    df["ema_short"] = pd.Series(ta.EMA(close, timeperiod=short_line), index=df.index)
    df["ema_mid"] = pd.Series(ta.EMA(close, timeperiod=mid_line), index=df.index)
    df["ema_long"] = pd.Series(ta.EMA(close, timeperiod=long_line), index=df.index)

    # DMI/ADX
    df["plus_di"] = pd.Series(ta.PLUS_DI(high, low, close, timeperiod=14), index=df.index)
    df["minus_di"] = pd.Series(ta.MINUS_DI(high, low, close, timeperiod=14), index=df.index)
    df["adx"] = pd.Series(ta.ADX(high, low, close, timeperiod=14), index=df.index)

    # RSI
    df["rsi"] = pd.Series(ta.RSI(close, timeperiod=14), index=df.index)

    # OBV 및 z-score
    df["obv"] = pd.Series(ta.OBV(close, volume), index=df.index)
    epsilon = 1e-9
    obv_diff = df["obv"].diff()
    obv_mean = obv_diff.rolling(7, min_periods=3).mean()
    obv_std = obv_diff.rolling(7, min_periods=3).std().replace(0, epsilon).fillna(epsilon)
    obv_z = ((obv_diff - obv_mean) / obv_std).fillna(0.0)
    obv_buy_cond = (obv_diff.iloc[-1] > 0) and (obv_z.iloc[-1] > 1.0)
    obv_sell_cond = (obv_diff.iloc[-1] < 0) and (obv_z.iloc[-1] < -1.0)

    # 스토캐스틱
    slowk_np, slowd_np = ta.STOCH(
        high, low, close,
        fastk_period=14, slowk_period=3, slowk_matype=0,
        slowd_period=3, slowd_matype=0
    )
    slowk = pd.Series(slowk_np, index=df.index)
    slowd = pd.Series(slowd_np, index=df.index)

    # MACD
    macd_np, macd_sig_np, macd_hist_np = ta.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    df["macd"] = pd.Series(macd_np, index=df.index)
    df["macd_signal"] = pd.Series(macd_sig_np, index=df.index)
    df["macd_hist"] = pd.Series(macd_hist_np, index=df.index)

    # ATR
    df["atr"] = pd.Series(ta.ATR(high, low, close, timeperiod=14), index=df.index)

    # 데이터 길이/NaN 가드
    min_needed = max(long_line, 26, 22, 14) + 1
    if len(df) < min_needed:
        return False, False, False, False, False

    tail2_cols = ["ema_short", "ema_mid", "rsi", "macd", "macd_signal"]
    tail1_cols = ["ema_long", "adx", "plus_di", "minus_di", "atr"]
    if df[tail2_cols].iloc[-2:].isna().any().any() or df[tail1_cols].iloc[-1:].isna().any().any():
        return False, False, False, False, False
    if pd.isna(slowk.iloc[-1]) or pd.isna(slowd.iloc[-1]) or pd.isna(slowk.iloc[-2]) or pd.isna(slowd.iloc[-2]):
        return False, False, False, False, False

    # 최신/직전 값
    ema_s_now, ema_m_now, ema_l_now = df["ema_short"].iloc[-1], df["ema_mid"].iloc[-1], df["ema_long"].iloc[-1]
    rsi_now, rsi_prev = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
    adx_now = df["adx"].iloc[-1]
    atr_now = df["atr"].iloc[-1]
    slowk_now, slowk_prev = slowk.iloc[-1], slowk.iloc[-2]
    slowd_now, slowd_prev = slowd.iloc[-1], slowd.iloc[-2]
    macd_now, macd_sig_now = df["macd"].iloc[-1], df["macd_signal"].iloc[-1]
    macd_prev, macd_sig_prev = df["macd"].iloc[-2], df["macd_signal"].iloc[-2]
    close_now = close[-1]

    # 교차 유틸
    def crossed_above(v_now, v_prev, ref_now, ref_prev) -> bool:
        return (v_prev <= ref_prev) and (v_now > ref_now)

    def crossed_below(v_now, v_prev, ref_now, ref_prev) -> bool:
        return (v_prev >= ref_prev) and (v_now < ref_now)

    # 트리거(교차)
    rsi_buy_trigger = crossed_above(rsi_now, rsi_prev, 50, 50) or (rsi_now < 30 and rsi_now > rsi_prev)
    rsi_sell_trigger = crossed_below(rsi_now, rsi_prev, 70, 70) or (rsi_now > 70 and rsi_now < rsi_prev)
    stoch_buy_trigger = crossed_above(slowk_now, slowk_prev, 20, 20)
    stoch_sell_trigger = crossed_below(slowk_now, slowk_prev, 80, 80)
    macd_buy_trigger = crossed_above(macd_now, macd_prev, macd_sig_now, macd_sig_prev) and (adx_now > 20)
    macd_sell_trigger = crossed_below(macd_now, macd_prev, macd_sig_now, macd_sig_prev) and (adx_now > 20)

    # 구간(레벨) 컨펌
    rsi_bull = (rsi_now > 50) and (rsi_now >= rsi_prev)
    rsi_bear = (rsi_now < 50) and (rsi_now <= rsi_prev)
    stoch_kd_bull = (slowk_now > slowd_now) and (slowk_now > 20) and (slowd_now > 20)
    stoch_kd_bear = (slowk_now < slowd_now) and (slowk_now < 80) and (slowd_now < 80)
    macd_bull = (macd_now > macd_sig_now) and (macd_now > 0)
    macd_bear = (macd_now < macd_sig_now) and (macd_now < 0)


    # 레벨 또는 트리거를 묶어 1표 인정
    def votes_bull() -> int:
        rsi_ok = rsi_bull or rsi_buy_trigger
        stoch_ok = stoch_kd_bull or stoch_buy_trigger
        macd_ok = macd_bull or macd_buy_trigger
        return int(rsi_ok) + int(stoch_ok) + int(macd_ok)

    def votes_bear() -> int:
        rsi_ok = rsi_bear or rsi_sell_trigger
        stoch_ok = stoch_kd_bear or stoch_sell_trigger
        macd_ok = macd_bear or macd_sell_trigger
        return int(rsi_ok) + int(stoch_ok) + int(macd_ok)

    # 상태(이평)
    state_up_s_gt_m = (ema_s_now > ema_m_now)
    state_up_m_gt_l = (ema_m_now > ema_l_now)
    state_dn_s_lt_m = (ema_s_now < ema_m_now)
    state_dn_m_lt_l = (ema_m_now < ema_l_now)

    # 컨펌 요구 개수
    k_confirm = 2  # 3개(RSI / STOCH K/D / MACD) 중 최소 2개 이상

    # === 매수/매도 신호 생성부 ===
    # 1차 매수 신호:
    # - 상태(필수): 단기 EMA > 중기 EMA
    # - 보조지표 컨펌: RSI(레벨/교차), STOCH K/D(레벨/교차), MACD(레벨/교차) 중 2개 이상
    # - 추세강도 필터: ADX > 15
    # - 거래량/수급: OBV z-score 양성(obv_buy_cond)
    first_buy_signal = bool(
        state_up_s_gt_m and (votes_bull() >= k_confirm) and (adx_now > 15) and obv_buy_cond
    )

    # 2차 매수 신호:
    # - 상태(필수): 중기 EMA > 장기 EMA
    # - 보조지표 컨펌: RSI/스토캐/MACD 중 2개 이상
    # - 거래량/수급: OBV z-score 양성(obv_buy_cond)
    #   (비고) 2차 매수는 추세 강화 구간이므로 ADX 하한은 선택적으로 생략
    second_buy_signal = bool(
        state_up_m_gt_l and (votes_bull() >= k_confirm) and obv_buy_cond
    )

    # 1차 매도 신호:
    # - 상태(필수): 단기 EMA < 중기 EMA
    # - 보조지표 컨펌: RSI(레벨/교차), STOCH K/D(레벨/교차), MACD(레벨/교차) 중 2개 이상(약세 컨펌)
    # - 거래량/수급: OBV z-score 음성(obv_sell_cond)
    first_sell_signal = bool(
        state_dn_s_lt_m and (votes_bear() >= k_confirm) and obv_sell_cond
    )

    # 2차 매도 신호:
    # - 상태(필수): 중기 EMA < 장기 EMA
    # - 보조지표 컨펌: RSI/스토캐/MACD 중 2개 이상(약세 컨펌)
    # - 거래량/수급: OBV z-score 음성(obv_sell_cond)
    second_sell_signal = bool(
        state_dn_m_lt_l and (votes_bear() >= k_confirm) and obv_sell_cond
    )

    # ATR 기반 Chandelier Exit (롱 포지션 트레일링 스톱):
    # - 기준: 최근 22봉 최고가 - ATR(14) * 2.5
    # - 현재가가 CE 아래면 손절/청산 신호
    highest_n = pd.Series(high, index=df.index).rolling(22, min_periods=1).max().iloc[-1]
    ce_long = highest_n - 2.5 * atr_now if pd.notna(atr_now) else None
    stop_loss_signal = bool(ce_long is not None and close_now < ce_long)

    return first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal


def ichimoku_swing_signals(df: pd.DataFrame) -> tuple[bool, bool, bool, bool, bool]:
    """
    일목균형표 기반 신호 생성:
    - 1차 매수: TK 골든크로스(전환선↑기준선) + 구름 상단 돌파/유지 + 치코우(현재가 > 26봉 전 종가)
    - 2차 매수: 구름 강세(스팬A>스팬B) + 기준선 위 + (최근 TK 골든크로스 or 구름 재돌파)
    - 1차 매도: TK 데드크로스 + 전환선 하회 or 구름 하향 압력 초입
    - 2차 매도: 구름 하단 이탈 or 구름 약세(스팬A<스팬B) + TK 데드크로스
    - 손절: min(기준선, 구름 하단) - 1.5*ATR(롱 관점)
    Returns: (buy1, buy2, sell1, sell2, stop_loss)
    """
    TENKAN_PERIOD = 9
    KIJUN_PERIOD = 26
    SENKOU_B_PERIOD = 52
    ATR_PERIOD = 14

    if df is None or len(df) == 0:
        return False, False, False, False, False

    # 날짜 정렬 및 숫자형 보정
    if "STCK_BSOP_DATE" in df.columns:
        df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    for col in ["STCK_HGPR", "STCK_LWPR", "STCK_CLPR"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    high = df["STCK_HGPR"]
    low = df["STCK_LWPR"]
    close = df["STCK_CLPR"]

    # 데이터 길이/NaN 가드 (선행스팬 26 시프트 반영)
    min_needed = max(SENKOU_B_PERIOD, KIJUN_PERIOD, TENKAN_PERIOD) + 26 + 1
    if len(df) < min_needed:
        return False, False, False, False, False

    # 전환선(Tenkan), 기준선(Kijun)
    tenkan_high = high.rolling(TENKAN_PERIOD, min_periods=TENKAN_PERIOD).max()
    tenkan_low = low.rolling(TENKAN_PERIOD, min_periods=TENKAN_PERIOD).min()
    tenkan = (tenkan_high + tenkan_low) / 2.0

    kijun_high = high.rolling(KIJUN_PERIOD, min_periods=KIJUN_PERIOD).max()
    kijun_low = low.rolling(KIJUN_PERIOD, min_periods=KIJUN_PERIOD).min()
    kijun = (kijun_high + kijun_low) / 2.0

    # 선행스팬 A/B (현재 시점에서 사용 가능하도록 shift(26))
    senkou_a = ((tenkan + kijun) / 2.0).shift(26)
    senkou_b_high = high.rolling(SENKOU_B_PERIOD, min_periods=SENKOU_B_PERIOD).max()
    senkou_b_low = low.rolling(SENKOU_B_PERIOD, min_periods=SENKOU_B_PERIOD).min()
    senkou_b = ((senkou_b_high + senkou_b_low) / 2.0).shift(26)

    # 치코우는 전형적으로 종가를 -26 시프트하나, 룩어헤드 방지 위해
    # "현재가 vs 26봉 전 종가" 비교로 동일 의미의 컨펌 사용
    close_26ago = close.shift(26)

    # ATR (리스크/손절 계산)
    atr = pd.Series(ta.ATR(high.values, low.values, close.values, timeperiod=ATR_PERIOD), index=df.index)

    # NaN 방지
    tail_cols = [tenkan, kijun, senkou_a, senkou_b, close_26ago, atr]
    for s in tail_cols:
        if pd.isna(s.iloc[-1]) or pd.isna(s.iloc[-2]):
            return False, False, False, False, False
    if pd.isna(close.iloc[-1]) or pd.isna(close.iloc[-2]):
        return False, False, False, False, False

    # 최신/직전 값
    tenkan_now, tenkan_prev = tenkan.iloc[-1], tenkan.iloc[-2]
    kijun_now, kijun_prev = kijun.iloc[-1], kijun.iloc[-2]
    senkou_a_now, senkou_b_now = senkou_a.iloc[-1], senkou_b.iloc[-1]
    senkou_a_prev, senkou_b_prev = senkou_a.iloc[-2], senkou_b.iloc[-2]
    close_now, close_prev = close.iloc[-1], close.iloc[-2]
    close_26ago_now, close_26ago_prev = close_26ago.iloc[-1], close_26ago.iloc[-2]
    atr_now = atr.iloc[-1]

    # 유틸(교차)
    def crossed_above(v_now, v_prev, ref_now, ref_prev) -> bool:
        return (v_prev <= ref_prev) and (v_now > ref_now)

    def crossed_below(v_now, v_prev, ref_now, ref_prev) -> bool:
        return (v_prev >= ref_prev) and (v_now < ref_now)

    # 상태/레벨
    tk_bull_cross = crossed_above(tenkan_now, tenkan_prev, kijun_now, kijun_prev)
    tk_bear_cross = crossed_below(tenkan_now, tenkan_prev, kijun_now, kijun_prev)

    cloud_bull = senkou_a_now > senkou_b_now
    cloud_bear = senkou_a_now < senkou_b_now

    above_cloud_now = close_now > max(senkou_a_now, senkou_b_now)
    below_cloud_now = close_now < min(senkou_a_now, senkou_b_now)

    # 구름 상단 재돌파(리테스트) 감지
    rebreak_up = (close_prev <= max(senkou_a_prev, senkou_b_prev)) and above_cloud_now
    rebreak_down = (close_prev >= min(senkou_a_prev, senkou_b_prev)) and below_cloud_now

    # 치코우 컨펌(동일 의미 비교)
    chikou_bull = (pd.notna(close_26ago_now) and close_now > close_26ago_now)

    # 과열 진입 방지(가격이 기준선에서 과도하게 이탈)
    over_ext_up = (atr_now > 0) and ((close_now - kijun_now) / atr_now > 2.0)

    # === 신호 정의 ===
    # 1차 매수: TK 골든크로스 + (구름 상단) + 치코우 우위, 과열 아님
    first_buy_signal = bool(
        tk_bull_cross and above_cloud_now and chikou_bull and not over_ext_up
    )

    # 2차 매수: 구름 강세 + 기준선 위 + (최근 TK 골든크로스 or 구름 재돌파), 과열 아님
    second_buy_signal = bool(
        cloud_bull and (close_now > kijun_now) and (tk_bull_cross or rebreak_up) and not over_ext_up
    )

    # 1차 매도: TK 데드크로스 + (전환선 하회 또는 하락 압력 초입)
    first_sell_signal = bool(
        tk_bear_cross and ((close_now < tenkan_now) or rebreak_down)
    )

    # 2차 매도: 구름 하단 이탈 또는 구름 약세 + TK 데드크로스
    second_sell_signal = bool(
        (below_cloud_now or cloud_bear) and (tk_bear_cross or rebreak_down)
    )

    # 손절(롱 관점): min(기준선, 구름 하단) - 1.5*ATR
    cloud_floor_now = min(senkou_a_now, senkou_b_now)
    sl_level = min(kijun_now, cloud_floor_now)
    stop_loss_signal = bool(pd.notna(sl_level) and pd.notna(atr_now) and (close_now < (sl_level - 1.5 * atr_now)))

    return first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal, stop_loss_signal
