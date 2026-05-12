"""
단일 20EMA 매매 전략 (Single EMA Strategy)

**매수 조건 (Entry Conditions):**
1. EMA 추세: 현재가 >= 실시간 EMA20 * 0.995 (0.5% 여유)
2. 수급 강도: (외국인 >= 1.5%) AND (OBV z-score >= 1.0)
3. 급등 필터: 당일 상승률 <= 5%
4. 괴리율 필터: EMA 괴리율 <= 5%
5. 추세 방향: +DI > -DI
6. 연속 확인: 2회 (Redis 상태 관리, 5분 주기 노이즈 필터링)

**2차 매수 조건 (20분 경과 후):**
- **시나리오 A (추세 강화형):** EMA + ATR × (0.3~2.0), ADX > 25, 외국인 >= 1.5%, OBV z-score >= 1.2
- **시나리오 B (눌림목 반등):** EMA ± ATR × 0.5, 18 <= ADX <= 23, 장중 저가 대비 0.4% 반등

**매도 조건 (Exit Conditions) - 이원화된 하이브리드 전략:**

**[1차 방어선] 장중 즉시 매도 (5분마다 체크)**
*   목표: 급락 사고 방어
1.  **EMA-ATR 동적 손절:** 현재가 <= EMA - (ATR × 1.0)

**[2차 방어선] 장 마감 매도 (매일 종가에 체크, 교차 검증)**
*   목표: 노이즈를 제거한 추세 이탈 '확정'
*   **시간 윈도우:** 최근 3거래일 이내 발생한 신호만 유효
1.  **1차 분할 매도 (50%):** 아래 3개 조건 중 **2개 이상** 충족 시
    -   EMA 종가 이탈
    -   추세 약화 (ADX/DMI 2일 연속 약세)
    -   수급 이탈 (OBV z-score 또는 일일 외국인 순매수 비율)
2.  **2차 전량 매도:** 1차 매도 후, 아래 조건 중 하나라도 충족 시
    -   장 마감 시, 위 3개 조건이 **모두** 충족
    -   1차 매도가 대비 -2% 추가 하락
"""
import pandas as pd
import talib as ta
import numpy as np
import json
import logging
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timedelta
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
    CONSECUTIVE_REQUIRED = 2

    # 2차 매수 조건
    # [시나리오 A] 추세 강화형 (EMA-ATR 가드레일)
    TREND_BUY_ATR_LOWER = 0.3        # 하한: EMA + ATR × 0.3 (추세 가속 최소선)
    TREND_BUY_ATR_UPPER = 2.0        # 상한: EMA + ATR × 2.0 (과열 방지선)
    TREND_BUY_FRGN_THRESHOLD = 1.5   # 외국인 비율 최소값
    TREND_BUY_OBV_THRESHOLD = 1.2    # OBV z-score 최소값
    TREND_BUY_ADX_MIN = 25           # ADX 최소값 (강한 추세)

    # [시나리오 B] 눌림목 반등 (EMA-ATR 가드레일)
    PULLBACK_BUY_ATR_LOWER = -0.5    # 하한: EMA - ATR × 0.5 (조정 허용 하한)
    PULLBACK_BUY_ATR_UPPER = 0.3     # 상한: EMA + ATR × 0.3 (조정 범위 상한)
    PULLBACK_BUY_FRGN_MIN = 0.5      # 외국인 비율 최소값
    PULLBACK_BUY_OBV_MIN = 0.5       # OBV z-score 최소값
    PULLBACK_BUY_ADX_MIN = 18        # ADX 하한 (추세 유지)
    PULLBACK_BUY_ADX_MAX = 23        # ADX 상한 (조정 구간)
    PULLBACK_BUY_REBOUND_RATIO = 1.004  # 장중 저가 대비 반등 비율 (0.4%)

    # 공통
    SECOND_BUY_TIME_MIN = 1200       # 1차 매수 후 최소 경과 시간 (초, 20분)

    # 매도 조건 (이원화)
    # [1차 방어선]
    ATR_MULTIPLIER = 1.0
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
    async def get_cached_indicators(cls, redis_client, symbol: str) -> Optional[Dict]:
        """
        Redis 캐시에서 지표 조회 (평탄화된 구조)

        Returns:
            {
                'ema20': 50000.0,  # 어제 종가 기준 EMA (중간값)
                'adx': 25.5,       # 어제 ADX (중간값)
                'plus_dm14': 360.0,   # 어제 +DM14 (중간값)
                'minus_dm14': 180.0,  # 어제 -DM14 (중간값)
                'atr': 1200.0,     # 어제 ATR (중간값)
                'obv': 1000000.0,  # 어제 OBV (중간값)
                'obv_z': 1.5,
                'obv_recent_diffs': [100, 200, -150, 300, 50, 120],
                'close': 51000.0,  # 어제 종가
                'high': 52000.0,   # 어제 고가
                'low': 50000.0,    # 어제 저가
                'date': '20260127'
            } or None
        """
        try:
            cached = await redis_client.get(f"indicators:{symbol}")
            if not cached:
                return None

            data = json.loads(cached)
            # 평탄화된 구조 그대로 반환
            return {
                'ema20': data['ema20'],
                'adx': data['adx'],
                'plus_dm14': data['plus_dm14'],    # 중간값
                'minus_dm14': data['minus_dm14'],  # 중간값
                'atr': data['atr'],
                'obv': data['obv'],
                'obv_z': data['obv_z'],
                'obv_recent_diffs': data['obv_recent_diffs'],
                'close': data['close'],
                'high': data['high'],
                'low': data['low'],
                'date': data['date']
            }
        except Exception as e:
            logger.warning(f"[{symbol}] 캐시 조회 실패: {e}")
            return None

    @classmethod
    async def get_realtime_ema20(
        cls,
        redis_client,
        symbol: str,
        df: pd.DataFrame,
        current_price: float,
        cached_indicators: Optional[Dict] = None
    ) -> Optional[float]:
        """
        최적화된 실시간 EMA20 계산 (캐시 우선)

        전략:
        1. cached_indicators 파라미터 우선 사용
        2. 없으면 Redis 캐시에서 어제 EMA 조회 시도
        3. 캐시 히트: 증분 계산 (O(1), 수백 배 빠름) ⚡
        4. 캐시 미스: TA-Lib 전체 계산 (O(n), 폴백)

        Args:
            redis_client: Redis 클라이언트
            symbol: 종목 코드
            df: 과거 OHLCV 데이터
            current_price: 현재가
            cached_indicators: 미리 조회한 캐시 데이터

        Returns:
            실시간 EMA20 값
        """
        try:
            # 1. 파라미터로 전달된 캐시 우선 사용
            if not cached_indicators:
                cached_indicators = await cls.get_cached_indicators(redis_client, symbol)

            if cached_indicators:
                # 1-1. 이미 증분 계산된 값이 있으면 바로 사용 (auto_swing_batch에서 호출 시)
                if 'realtime_ema20' in cached_indicators:
                    realtime_ema = cached_indicators['realtime_ema20']
                    logger.debug(f"[{symbol}] 실시간 EMA 재사용: {realtime_ema:.2f}")
                    return realtime_ema

                # 1-2. 없으면 증분 계산
                yesterday_ema = cached_indicators['ema20']
                realtime_ema = TechnicalIndicators.calculate_realtime_ema_from_cache(
                    yesterday_ema, current_price, cls.EMA_PERIOD
                )
                logger.debug(
                    f"[{symbol}] EMA 캐시 히트 - 증분 계산: "
                    f"어제={yesterday_ema:.2f} → 오늘={realtime_ema:.2f}"
                )
                return realtime_ema

            # 2. 캐시 미스: 전체 계산 (폴백)
            logger.debug(f"[{symbol}] EMA 캐시 미스 - TA-Lib 전체 계산")
            if len(df) < cls.EMA_PERIOD:
                return None
            close_prices = df["STCK_CLPR"].values.astype(float)
            close_with_today = np.append(close_prices, current_price)
            ema_array = ta.EMA(close_with_today, timeperiod=cls.EMA_PERIOD)
            return float(ema_array[-1]) if len(ema_array) > 0 and not np.isnan(ema_array[-1]) else None

        except Exception as e:
            logger.error(f"[{symbol}] 실시간 EMA 계산 실패: {e}", exc_info=True)
            # 최종 폴백: 기존 방식
            if len(df) < cls.EMA_PERIOD:
                return None
            close_prices = df["STCK_CLPR"].values.astype(float)
            close_with_today = np.append(close_prices, current_price)
            ema_array = ta.EMA(close_with_today, timeperiod=cls.EMA_PERIOD)
            return float(ema_array[-1]) if len(ema_array) > 0 and not np.isnan(ema_array[-1]) else None

    @classmethod
    async def get_realtime_obv_zscore(
        cls,
        redis_client,
        symbol: str,
        df: Optional[pd.DataFrame],
        current_price: float,
        current_volume: int,
        cached_indicators: Optional[Dict] = None
    ) -> Optional[float]:
        """
        최적화된 실시간 OBV z-score 계산 (캐시 우선)

        전략:
        1. cached_indicators 파라미터 우선 사용
        2. 없으면 Redis 캐시에서 어제 OBV, 최근 6일 diff 조회 시도
        3. 캐시 히트: 증분 계산 (O(1), 매우 빠름) ⚡
        4. 캐시 미스: TA-Lib 전체 계산 (O(n), 폴백)

        Args:
            redis_client: Redis 클라이언트
            symbol: 종목 코드
            df: 과거 OHLCV 데이터
            current_price: 현재가
            current_volume: 현재 누적 거래량
            cached_indicators: 미리 조회한 캐시 데이터

        Returns:
            실시간 OBV z-score 값
        """
        try:
            # 1. 파라미터로 전달된 캐시 우선 사용
            if not cached_indicators:
                cached_indicators = await cls.get_cached_indicators(redis_client, symbol)

            if cached_indicators:
                # 1-1. 이미 증분 계산된 값이 있으면 바로 사용 (auto_swing_batch에서 호출 시)
                if 'realtime_obv_z' in cached_indicators:
                    realtime_obv_z = cached_indicators['realtime_obv_z']
                    logger.debug(f"[{symbol}] 실시간 OBV z-score 재사용: {realtime_obv_z:.2f}")
                    return realtime_obv_z

                # 1-2. 없으면 증분 계산
                yesterday_obv = cached_indicators['obv']
                yesterday_close = cached_indicators['close']
                recent_6_diffs = cached_indicators['obv_recent_diffs']

                realtime_obv_z = TechnicalIndicators.calculate_realtime_obv_zscore(
                    yesterday_obv, yesterday_close, current_price, current_volume, recent_6_diffs
                )
                logger.debug(
                    f"[{symbol}] OBV z-score 캐시 히트 - 증분 계산: {realtime_obv_z:.2f}"
                )
                return realtime_obv_z

            # 2. 캐시 미스: TA-Lib 전체 계산 (폴백)
            logger.debug(f"[{symbol}] OBV z-score 캐시 미스 - TA-Lib 전체 계산")
            if df is None or len(df) < 8:
                logger.warning(f"[{symbol}] OBV z-score 계산 불가: 데이터 부족")
                return None

            # OBV 계산
            close_prices = df["STCK_CLPR"].values.astype(float)
            volumes = df["ACML_VOL"].values.astype(float)

            # 오늘 데이터 추가
            close_with_today = np.append(close_prices, current_price)
            volumes_with_today = np.append(volumes, current_volume)

            obv = TechnicalIndicators.calculate_obv(close_with_today, volumes_with_today)
            if obv is None:
                return None

            obv_z = TechnicalIndicators.calculate_obv_zscore(obv, lookback=7)
            return float(obv_z[-1]) if obv_z is not None and len(obv_z) > 0 else None

        except Exception as e:
            logger.error(f"[{symbol}] 실시간 OBV z-score 계산 실패: {e}", exc_info=True)
            return None

    # ========================================
    # 매수 신호 로직 (기존과 유사)
    # ========================================

    @classmethod
    async def check_entry_signal(
        cls,
        redis_client,
        symbol: str,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """1차 매수 진입 신호 체크"""
        curr_price = float(current_price)

        # 지표 사용 (모두 실시간 증분 계산 완료 상태)
        try:
            # 실시간 DI 사용
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']

            # 실시간 EMA 사용
            realtime_ema20 = cached_indicators['realtime_ema20']
            gap_ratio = cached_indicators['realtime_gap_ratio']

            # 실시간 OBV z-score 사용
            realtime_obv_z = cached_indicators['realtime_obv_z']

        except Exception as e:
            logger.error(f"[{symbol}] 매수 신호 지표 계산 실패: {e}", exc_info=True)
            return None

        # 조건 검증
        price_above_ema = curr_price >= realtime_ema20 * 0.995  # 0.5% 여유
        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        supply_strong = (frgn_ratio >= cls.FRGN_STRONG_THRESHOLD) and (realtime_obv_z >= cls.OBV_Z_BUY_THRESHOLD)
        surge_filtered = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)
        gap_filtered = gap_ratio <= cls.MAX_GAP_RATIO
        trend_upward = realtime_plus_di > realtime_minus_di

        current_signal = all([price_above_ema, supply_strong, surge_filtered, gap_filtered, trend_upward])

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
            logger.info(f"[{symbol}] 1차 매수 신호 발생 (연속 {consecutive}회)")
            return {'action': 'BUY', 'price': curr_price, 'reason': f"1차 매수 (연속 {consecutive}회)"}
        elif current_signal:
            logger.info(f"[{symbol}] 매수 신호 대기 중 ({consecutive}/{cls.CONSECUTIVE_REQUIRED})")

        return None

    @classmethod
    async def check_exit_signal(
        cls,
        redis_client,
        position_id: int,
        symbol: str,
        current_price: Decimal,
        entry_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        cached_indicators: Dict
    ) -> Dict:
        """
        매도 신호 체크 (베이스 클래스 구현)
        실제로는 check_immediate_sell_signal을 호출합니다.
        """
        result = await cls.check_immediate_sell_signal(
            redis_client, symbol, current_price, cached_indicators
        )
        return result if result else {"action": "HOLD", "reason": "매도 조건 미충족"}

    @classmethod
    async def check_second_buy_signal(
        cls,
        redis_client,
        swing_id: int,
        symbol: str,
        entry_price: Decimal,
        hold_qty: int,
        current_price: Decimal,
        frgn_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """
        2차 매수 신호 체크 (하이브리드: 추세 강화형 + 눌림목 반등)

        시나리오 A: 추세 강화형 (EMA + ATR × 0.3 ~ 2.5)
        시나리오 B: 눌림목 반등 (EMA - ATR × 0.5 ~ EMA + ATR × 0.3)
        """
        try:
            curr_price = float(current_price)

            # 시간 필터: 1차 매수 후 최소 20분 경과 체크
            time_key = f"first_buy_time:{swing_id}"
            if await redis_client.exists(time_key):
                return None  # 키 존재 = 20분 미경과 → 2차 매수 불가

            # 지표 사용 (모두 실시간 증분 계산 완료 상태)
            realtime_adx = cached_indicators['realtime_adx']
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']
            atr = cached_indicators['realtime_atr']
            realtime_ema20 = cached_indicators['realtime_ema20']
            realtime_obv_z = cached_indicators['realtime_obv_z']

            frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

            # === 시나리오 A: 추세 강화형 ===
            # 가격 가드레일: EMA + ATR × (0.3 ~ 2.5)
            trend_lower = realtime_ema20 + (atr * cls.TREND_BUY_ATR_LOWER)
            trend_upper = realtime_ema20 + (atr * cls.TREND_BUY_ATR_UPPER)

            if trend_lower <= curr_price <= trend_upper:
                # 추세 강도: ADX > 25
                if realtime_adx > cls.TREND_BUY_ADX_MIN:
                    # 추세 방향: +DI > -DI
                    if realtime_plus_di > realtime_minus_di:
                        # 수급 지속: 외국인 AND OBV
                        if frgn_ratio >= cls.TREND_BUY_FRGN_THRESHOLD and realtime_obv_z >= cls.TREND_BUY_OBV_THRESHOLD:
                            logger.info(f"[{symbol}] ✅ 2차 매수 신호 (추세 강화형): EMA+ATR×{(curr_price-realtime_ema20)/atr:.2f}")
                            return {
                                'action': 'BUY',
                                'price': curr_price,
                                'reason': f"2차매수(추세강화)"
                            }

            # === 시나리오 B: 눌림목 반등 ===
            # 가격 가드레일: EMA - ATR × 0.5 ~ EMA + ATR × 0.3
            pullback_lower = realtime_ema20 + (atr * cls.PULLBACK_BUY_ATR_LOWER)  # EMA - ATR × 0.5
            pullback_upper = realtime_ema20 + (atr * cls.PULLBACK_BUY_ATR_UPPER)  # EMA + ATR × 0.3

            if pullback_lower <= curr_price <= pullback_upper:
                # 추세 강도: 18 <= ADX <= 23 (중간 추세, 조정 구간)
                if cls.PULLBACK_BUY_ADX_MIN <= realtime_adx <= cls.PULLBACK_BUY_ADX_MAX:
                    # 추세 방향: +DI > -DI
                    if realtime_plus_di > realtime_minus_di:
                        # 수급 유지: 외국인 OR OBV (중립 이상)
                        supply_ok = (frgn_ratio > cls.PULLBACK_BUY_FRGN_MIN) or (realtime_obv_z > cls.PULLBACK_BUY_OBV_MIN)
                        if supply_ok:
                            # 반등 신호: 장중 저가 대비 0.4% 반등
                            intraday_low_key = f"intraday_low:{swing_id}"
                            intraday_low_str = await redis_client.get(intraday_low_key)

                            if intraday_low_str:
                                intraday_low = float(intraday_low_str.decode())
                                # 현재가가 저가보다 낮으면 갱신
                                if curr_price < intraday_low:
                                    await redis_client.setex(intraday_low_key, 86400, str(curr_price))
                                    intraday_low = curr_price

                                # 저점 대비 0.4% 이상 반등했는지 확인
                                if curr_price >= intraday_low * cls.PULLBACK_BUY_REBOUND_RATIO:
                                    logger.info(f"[{symbol}] ✅ 2차 매수 신호 (눌림목 반등): 저가 대비 {((curr_price/intraday_low-1)*100):.2f}% 반등")
                                    return {
                                        'action': 'BUY',
                                        'price': curr_price,
                                        'reason': f"2차매수(눌림목반등)"
                                    }
                            else:
                                # 최초 저가 기록
                                await redis_client.setex(intraday_low_key, 86400, str(curr_price))

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
        redis_client,
        symbol: str,
        current_price: Decimal,
        cached_indicators: Dict
    ) -> Optional[Dict]:
        """
        [1차 방어선] 장중 즉시 매도 신호 체크
        - trade_job (5분 주기)에서 호출
        - 조건: EMA-ATR 동적 손절만 사용 (백테스팅과 일치)
        """
        curr_price = float(current_price)

        # 실시간 EMA, ATR 사용
        realtime_ema20 = cached_indicators['realtime_ema20']
        atr = cached_indicators['realtime_atr']

        # EMA-ATR 동적 손절
        ema_atr_stop = realtime_ema20 - (atr * cls.ATR_MULTIPLIER)
        if curr_price <= ema_atr_stop:
            logger.warning(f"[{symbol}] 🚨 즉시 매도 신호: EMA-ATR손절(현재가≤{ema_atr_stop:,.0f})")
            return {"action": "SELL", "reason": f"즉시매도: EMA-ATR손절(현재가≤{ema_atr_stop:,.0f})"}

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

            # 2차-1: 추가 하락
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
        # 2차-2: 1차 매도 상태에서 모든 신호 충족 시
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
