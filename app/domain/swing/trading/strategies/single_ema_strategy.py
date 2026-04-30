"""
단일 20EMA 매매 전략 (Single EMA Strategy)

**매수 조건 (Entry Conditions):**
1. EMA 추세: 현재가 > 실시간 EMA20
2. 수급 강도: (외국인 >= 1.5%) AND (OBV z-score >= 1.0)
3. 급등 필터: 당일 상승률 <= 5%
4. 괴리율 필터: EMA 괴리율 <= 5%
5. 추세 강도: ADX > 25
6. 추세 방향: +DI > -DI
7. 연속 확인: 2회 (Redis 상태 관리)

**매도 조건 (Exit Conditions) - 이원화된 하이브리드 전략:**

**[1차 방어선] 장중 즉시 매도 (5분마다 체크, OR 조건)**
*   목표: 급락 사고 방어
1.  **고정 손절:** -3%
2.  **EMA-ATR 동적 손절:** 현재가 <= EMA - (ATR × 1.0)
3.  **급격한 수급 반전:** 외국인 순매도 비율 <= -2.0%

**[2차 방어선] 장 마감 매도 (매일 종가에 체크, 교차 검증)**
*   목표: 노이즈를 제거한 추세 이탈 '확정'
*   **시간 윈도우:** 최근 3거래일 이내 발생한 신호만 유효
1.  **1차 분할 매도 (50%):** 아래 3개 조건 중 **2개 이상** 충족 시
    -   EMA 종가 이탈
    -   추세 약화 (ADX/DMI 2일 연속 약세)
    -   수급 이탈 (OBV z-score 또는 일일 외국인 순매수 비율)
2.  **2차 전량 매도:** 1차 매도 후, 아래 조건 중 하나라도 충족 시
    -   -3% 고정 손절 도달
    -   장 마감 시, 위 3개 조건이 **모두** 충족
    -   1차 매도가 대비 -2% 추가 하락
"""
import pandas as pd
import talib as ta
import numpy as np
import json
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timedelta
import logging

from .base_trading_strategy import TradingStrategy
from app.domain.swing.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class SingleEMAStrategy(TradingStrategy):
    """단일 20EMA 매매 전략 (하이브리드 매도 로직)"""

    # 전략 이름
    name = "단일 20EMA 전략"

    # ========================================
    # 전략 파라미터
    # ========================================
    EMA_PERIOD = 20

    # 매수 조건
    FRGN_STRONG_THRESHOLD = 1.5
    OBV_Z_BUY_THRESHOLD = 1.0
    MAX_SURGE_RATIO = 0.05
    MAX_GAP_RATIO = 0.05
    ADX_THRESHOLD = 25
    CONSECUTIVE_REQUIRED = 2

    # 2차 매수 조건
    # [시나리오 A] 추세 강화형
    SECOND_BUY_PRICE_GAIN_MIN = 0.02
    SECOND_BUY_PRICE_GAIN_MAX = 0.08
    SECOND_BUY_FRGN_THRESHOLD = 1.5
    SECOND_BUY_OBV_THRESHOLD = 1.2
    SECOND_BUY_SAFETY_MARGIN = 0.04
    SECOND_BUY_TIME_MIN = 600

    # [시나리오 B] 조정 매수형
    PULLBACK_BUY_PRICE_RANGE = (-0.01, 0.01)  # 진입가 ±1%
    PULLBACK_BUY_FRGN_MIN = 0.5               # 외국인 최소 요구치
    PULLBACK_BUY_OBV_MIN = 0.5                # OBV z-score 최소 요구치
    PULLBACK_BUY_ATR_MULTIPLIER = 0.5         # ATR 안전 거리

    # 매도 조건 (이원화)
    # [1차 방어선]
    STOP_LOSS_FIXED = -0.03
    ATR_MULTIPLIER = 1.0
    SUPPLY_REVERSAL_THRESHOLD = -2.0
    # [2차 방어선]
    EOD_SIGNAL_WINDOW_DAYS = 3  # 시간 윈도우 (3일)
    EOD_TREND_WEAK_DAYS = 2
    EOD_SUPPLY_WEAK_FRGN_RATIO = 1.0
    EOD_SUPPLY_WEAK_OBV_Z = -1.0
    SECONDARY_SELL_ADDITIONAL_DROP = -0.02


    # ========================================
    # 지표 계산 및 유틸리티
    # ========================================

    @classmethod
    def get_realtime_ema20(cls, df: pd.DataFrame, current_price: float) -> Optional[float]:
        if len(df) < cls.EMA_PERIOD:
            return None
        close_prices = df["STCK_CLPR"].values.astype(float)
        close_with_today = np.append(close_prices, current_price)
        ema_array = ta.EMA(close_with_today, timeperiod=cls.EMA_PERIOD)
        return float(ema_array[-1]) if len(ema_array) > 0 and not np.isnan(ema_array[-1]) else None

    # ========================================
    # 매수 신호 로직 (기존과 유사)
    # ========================================

    @classmethod
    async def check_entry_signal(
        cls,
        redis_client,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float
    ) -> Optional[Dict]:
        """1차 매수 진입 신호 체크"""
        curr_price = float(current_price)

        # 지표 계산
        try:
            if 'obv_z' not in df.columns or 'adx' not in df.columns:
                df = TechnicalIndicators.prepare_indicators_from_df(df)
            last_row = df.iloc[-1]
            realtime_ema20 = cls.get_realtime_ema20(df, curr_price)
            if realtime_ema20 is None: return None

            obv_z = last_row.get('obv_z', 0)
            adx = last_row.get('adx', 0)
            plus_di = last_row.get('plus_di', 0)
            minus_di = last_row.get('minus_di', 0)
            gap_ratio = TechnicalIndicators.calculate_gap_ratio(curr_price, realtime_ema20)
        except Exception as e:
            logger.error(f"[{symbol}] 매수 신호 지표 계산 실패: {e}", exc_info=True)
            return None

        # 조건 검증
        price_above_ema = curr_price > realtime_ema20
        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        supply_strong = (frgn_ratio >= cls.FRGN_STRONG_THRESHOLD) and (obv_z >= cls.OBV_Z_BUY_THRESHOLD)
        surge_filtered = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)
        gap_filtered = gap_ratio <= cls.MAX_GAP_RATIO
        trend_strong = adx > cls.ADX_THRESHOLD
        trend_upward = plus_di > minus_di

        current_signal = all([price_above_ema, supply_strong, surge_filtered, gap_filtered, trend_strong, trend_upward])

        # 연속성 체크 (Redis)
        prev_state_key = f"entry:{symbol}"
        prev_state_str = await redis_client.get(prev_state_key)
        consecutive = 0
        if current_signal:
            if prev_state_str:
                prev_state = json.loads(prev_state_str)
                consecutive = prev_state.get('consecutive_count', 0) + 1 if prev_state.get('curr_signal') else 1
            else:
                consecutive = 1

        # 상태 저장
        new_state = {'curr_signal': current_signal, 'consecutive_count': consecutive, 'last_update': datetime.now().isoformat()}
        await redis_client.setex(prev_state_key, 900, json.dumps(new_state))

        if consecutive >= cls.CONSECUTIVE_REQUIRED:
            logger.info(f"[{symbol}] ✅ 1차 매수 신호 발생 (연속 {consecutive}회)")
            return {'action': 'BUY', 'price': curr_price, 'reason': f"1차 매수 (연속 {consecutive}회)"}
        elif current_signal:
            logger.info(f"[{symbol}] 🔔 매수 신호 대기 중 ({consecutive}/{cls.CONSECUTIVE_REQUIRED})")

        return None

    @classmethod
    async def check_exit_signal(
        cls,
        redis_client,
        position_id: int,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        entry_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int
    ) -> Dict:
        """
        매도 신호 체크 (베이스 클래스 구현)
        실제로는 check_immediate_sell_signal을 호출합니다.
        """
        result = await cls.check_immediate_sell_signal(
            symbol, df, current_price, entry_price, frgn_ntby_qty, acml_vol
        )
        return result if result else {"action": "HOLD", "reason": "매도 조건 미충족"}

    @classmethod
    async def check_second_buy_signal(
        cls,
        swing_repository,
        stock_repository,
        redis_client,
        swing_id: int,
        symbol: str,
        df: pd.DataFrame,
        entry_price: Decimal,
        hold_qty: int,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float
    ) -> Optional[Dict]:
        """
        2차 매수 신호 체크 (하이브리드: 추세 강화형 + 조정 매수형)

        시나리오 A: 추세 강화형 (2~8% 상승)
        시나리오 B: 건강한 조정 후 반등 (진입가 ±1%)
        """
        try:
            curr_price = float(current_price)
            entry = float(entry_price)
            price_change = (curr_price - entry) / entry

            # 지표 계산
            if 'obv_z' not in df.columns or 'adx' not in df.columns or 'atr' not in df.columns:
                df = TechnicalIndicators.prepare_indicators_from_df(df, atr_period=14)

            last_row = df.iloc[-1]
            realtime_ema20 = cls.get_realtime_ema20(df, curr_price)
            if realtime_ema20 is None:
                return None

            obv_z = last_row.get('obv_z', 0)
            atr = last_row.get('atr', 0)
            plus_di = last_row.get('plus_di', 0)
            minus_di = last_row.get('minus_di', 0)
            frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

            # === 시나리오 A: 추세 강화형 (2~8% 상승) ===
            if cls.SECOND_BUY_PRICE_GAIN_MIN <= price_change <= cls.SECOND_BUY_PRICE_GAIN_MAX:
                # 조건: EMA 위 + 강한 수급 + 안전 마진
                if curr_price > realtime_ema20:
                    if frgn_ratio >= cls.SECOND_BUY_FRGN_THRESHOLD and obv_z >= cls.SECOND_BUY_OBV_THRESHOLD:
                        stop_loss_price = entry * (1 + cls.STOP_LOSS_FIXED)
                        safety_threshold = stop_loss_price * (1 + cls.SECOND_BUY_SAFETY_MARGIN)
                        if curr_price >= safety_threshold:
                            logger.info(f"[{symbol}] ✅ 2차 매수 신호 (추세 강화형): {price_change*100:.2f}% 상승")
                            return {
                                'action': 'BUY',
                                'price': curr_price,
                                'reason': f"2차매수(추세강화 +{price_change*100:.1f}%)"
                            }

            # === 시나리오 B: 건강한 조정 후 반등 (진입가 ±1%) ===
            if cls.PULLBACK_BUY_PRICE_RANGE[0] <= price_change <= cls.PULLBACK_BUY_PRICE_RANGE[1]:
                # 조건 1: EMA 위에서 지지 (0.5% 여유)
                if curr_price >= realtime_ema20 * 0.995:
                    # 조건 2: 수급 유지 (외국인 OR OBV)
                    supply_ok = (frgn_ratio > cls.PULLBACK_BUY_FRGN_MIN) or (obv_z > cls.PULLBACK_BUY_OBV_MIN)
                    if supply_ok:
                        # 조건 3: 추세 유지
                        if plus_di > minus_di:
                            # 조건 4: ATR 대비 안전 거리
                            atr_support = realtime_ema20 - (atr * cls.PULLBACK_BUY_ATR_MULTIPLIER)
                            if curr_price > atr_support:
                                logger.info(f"[{symbol}] ✅ 2차 매수 신호 (조정 매수형): 진입가 근처 지지")
                                return {
                                    'action': 'BUY',
                                    'price': curr_price,
                                    'reason': f"2차매수(조정반등 {price_change*100:+.1f}%)"
                                }

            return None

        except Exception as e:
            logger.error(f"[{symbol}] 2차 매수 신호 체크 실패: {e}", exc_info=True)
            return None

    # ========================================
    # 매도 신호 로직 (핵심: 이원화된 하이브리드 전략)
    # ========================================

    @classmethod
    async def check_immediate_sell_signal(
        cls,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        entry_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int
    ) -> Optional[Dict]:
        """
        [1차 방어선] 장중 즉시 매도 신호 체크 (OR 조건)
        - trade_job (5분 주기)에서 호출
        """
        curr_price = float(current_price)
        entry = float(entry_price)
        reasons = []

        # 조건 1: 고정 손절
        profit_rate = (curr_price - entry) / entry
        if profit_rate <= cls.STOP_LOSS_FIXED:
            reasons.append(f"고정손절({profit_rate*100:.2f}%)")

        # 조건 2: EMA-ATR 동적 손절
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)
        if realtime_ema20:
            if 'atr' not in df.columns:
                df = TechnicalIndicators.prepare_indicators_from_df(df, atr_period=14)
            if 'atr' in df.columns and not df['atr'].isna().all():
                atr = float(df['atr'].iloc[-1])
                ema_atr_stop = realtime_ema20 - (atr * cls.ATR_MULTIPLIER)
                if curr_price <= ema_atr_stop:
                    reasons.append(f"EMA-ATR손절(현재가≤{ema_atr_stop:,.0f})")

        # 조건 3: 급격한 수급 반전
        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        if frgn_ratio <= cls.SUPPLY_REVERSAL_THRESHOLD:
            reasons.append(f"수급반전(외국인={frgn_ratio:.1f}%)")

        if reasons:
            reason_str = " + ".join(reasons)
            logger.warning(f"[{symbol}] 🚨 즉시 매도 신호: {reason_str}")
            return {"action": "SELL", "reason": f"즉시매도: {reason_str}"}

        return {"action": "HOLD", "reason": "즉시 매도 조건 미충족"}

    @classmethod
    async def check_eod_sell_signals(
        cls,
        redis_client,
        position: Dict,
        df_day: pd.DataFrame,
        daily_frgn_ratio: float,
        daily_obv_z: float
    ) -> Optional[Dict]:
        """
        [2차 방어선] 장 마감 매도 신호 체크 (교차 검증)
        - day_collect_job (장 마감 후)에서 호출
        """
        symbol = position['st_code']
        position_id = position['id']
        entry_price = float(position['avg_price'])
        last_close = float(df_day.iloc[-1]['STCK_CLPR'])

        # 0. 2차 전량 매도 조건 우선 체크 (1차 분할매도 상태일 때)
        if position['status'] == 'SELL_PRIMARY':
            first_sell_price = float(position['first_sell_price']) # DB에 1차 매도가 저장 필요

            # 2차-1: 고정 손절
            profit_rate = (last_close - entry_price) / entry_price
            if profit_rate <= cls.STOP_LOSS_FIXED:
                return {"action": "SELL_ALL", "reason": f"2차매도(고정손절: {profit_rate*100:.2f}%)"}

            # 2차-2: 추가 하락
            additional_drop = (last_close - first_sell_price) / first_sell_price
            if additional_drop <= cls.SECONDARY_SELL_ADDITIONAL_DROP:
                return {"action": "SELL_ALL", "reason": f"2차매도(추가하락: {additional_drop*100:.2f}%)"}

        # 1. 3가지 EOD 신호의 발생 여부를 체크하고 Redis에 기록
        await cls._log_eod_signal(redis_client, 'ema_breach', position_id,
            cls._check_ema_breach_eod(df_day), symbol)
        await cls._log_eod_signal(redis_client, 'trend_weak', position_id,
            cls._check_trend_weakness_eod(df_day), symbol)
        await cls._log_eod_signal(redis_client, 'supply_weak', position_id,
            cls._check_supply_weakness_eod(daily_frgn_ratio, daily_obv_z), symbol)

        # 2. 시간 윈도우 내 유효한 신호 개수 확인
        signal_keys = [f"eod_signal:{position_id}:{sig}" for sig in ['ema_breach', 'trend_weak', 'supply_weak']]
        valid_signal_count = await redis_client.exists(*signal_keys)
        
        active_signals = [key.decode().split(':')[-1] for key in await redis_client.mget(signal_keys) if key]


        logger.info(f"[{symbol}] EOD 신호 점검: {valid_signal_count}/3개 충족. (신호: {active_signals})")

        # 3. 매도 결정
        # 2차-3: 1차 매도 상태에서 모든 신호 충족 시
        if position['status'] == 'SELL_PRIMARY' and valid_signal_count >= 3:
            return {"action": "SELL_ALL", "reason": f"2차매도(모든 EOD 신호 충족)"}
            
        # 1차 분할 매도: 2개 이상 충족 시
        if position['status'] == 'BUY_COMPLETE' and valid_signal_count >= 2:
            return {"action": "SELL_PRIMARY", "reason": f"1차매도({valid_signal_count}/3 충족: {active_signals})"}

        return {"action": "HOLD", "reason": f"EOD 매도 조건 미충족 ({valid_signal_count}/3)"}


    @classmethod
    async def _log_eod_signal(cls, redis_client, signal_name: str, position_id: int, is_triggered: bool, symbol: str):
        """EOD 신호 발생 시 Redis에 TTL과 함께 기록"""
        key = f"eod_signal:{position_id}:{signal_name}"
        ttl = timedelta(days=cls.EOD_SIGNAL_WINDOW_DAYS).total_seconds()
        
        if is_triggered:
            await redis_client.setex(key, int(ttl), "1")
            logger.debug(f"[{symbol}] EOD 신호 '{signal_name}' 발생, Redis에 기록 (TTL: {cls.EOD_SIGNAL_WINDOW_DAYS}일)")
        else:
            # 신호가 발생하지 않은 경우, 과거 기록이 있다면 삭제 (연속성 조건이 아닌 경우)
            # 추세 약화와 같이 연속성 조건이 필요한 경우 이 로직은 수정되어야 함
            if signal_name != 'trend_weak':
                 await redis_client.delete(key)


    @classmethod
    def _check_ema_breach_eod(cls, df_day: pd.DataFrame) -> bool:
        """EOD 신호 1: 종가가 EMA 아래로 하회했는지 체크"""
        last = df_day.iloc[-1]
        return last['STCK_CLPR'] < last['ema_20']

    @classmethod
    def _check_trend_weakness_eod(cls, df_day: pd.DataFrame) -> bool:
        """EOD 신호 2: ADX/DMI 추세가 2일 연속 약화되었는지 체크"""
        if len(df_day) < cls.EOD_TREND_WEAK_DAYS:
            return False
        
        last_two_days = df_day.tail(cls.EOD_TREND_WEAK_DAYS)
        
        for _, row in last_two_days.iterrows():
            is_weak = row['adx'] < 20 and row['minus_di'] > row['plus_di']
            if not is_weak:
                return False # 하루라도 강세면 조건 미충족
        return True # 2일 모두 약세

    @classmethod
    def _check_supply_weakness_eod(cls, daily_frgn_ratio: float, daily_obv_z: float) -> bool:
        """EOD 신호 3: 일일 수급이 약화되었는지 체크 (OR 조건)"""
        is_frgn_weak = daily_frgn_ratio < cls.EOD_SUPPLY_WEAK_FRGN_RATIO
        is_obv_weak = daily_obv_z < cls.EOD_SUPPLY_WEAK_OBV_Z
        return is_frgn_weak or is_obv_weak
