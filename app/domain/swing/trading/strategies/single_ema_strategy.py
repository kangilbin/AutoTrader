import pandas as pd
import talib as ta
import numpy as np
import json
import logging
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timedelta, date
from .base_trading_strategy import TradingStrategy
from .base_single_ema import BaseSingleEMAStrategy
from app.domain.swing.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class SingleEMAStrategy(TradingStrategy, BaseSingleEMAStrategy):
    """단일 20EMA 매매 전략 (하이브리드 매도 로직)"""

    # 전략 이름
    name = "단일 20EMA 전략"

    # ========================================
    # 실전 전용 파라미터
    # ========================================
    # 매수 조건
    CONSECUTIVE_REQUIRED = 2         # 연속 확인 횟수 (10분)

    # 공통
    SECOND_BUY_TIME_MIN = 1200       # 1차 매수 후 최소 경과 시간 (초, 20분)


    # ========================================
    # 지표 계산 및 유틸리티
    # ========================================

    @classmethod
    async def get_cached_indicators(cls, redis_client, symbol: str) -> Optional[Dict]:
        """
        Redis 캐시에서 지표 조회 (평탄화된 구조)

        Returns:
            {
                'ema20': 50000.0,  # 어제 종가 기준 EMA20
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
                'date': data['date'],
                'avg_daily_amount': data['avg_daily_amount'],
                'prev_close': data['prev_close'],
                'prev_obv_z': data['prev_obv_z'],
                'prev_adx': data['prev_adx'],
                'prev_ema20': data['prev_ema20'],
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
        swing_id: int,
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
            # 실시간 DI, ADX 사용
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']
            realtime_adx = cached_indicators['realtime_adx']

            # 실시간 EMA 사용
            realtime_ema20 = cached_indicators['realtime_ema20']

            # 실시간 OBV z-score 사용
            realtime_obv_z = cached_indicators['realtime_obv_z']

        except Exception as e:
            logger.error(f"[{symbol}] 매수 신호 지표 계산 실패: {e}", exc_info=True)
            return None

        # 캐시에서 전일/전전일 데이터 추출
        prev_close = cached_indicators.get('prev_close')       # 전전일 종가
        prev_obv_z = cached_indicators.get('prev_obv_z')       # 전전일 OBV z-score
        prev_adx = cached_indicators.get('prev_adx')           # 전일 ADX
        prev_ema20 = cached_indicators.get('prev_ema20')       # 전일 EMA20
        yesterday_close = cached_indicators.get('close')       # 전일 종가
        yesterday_ema20 = cached_indicators.get('ema20')       # 전일 EMA20 (종가 기준)
        atr = cached_indicators.get('realtime_atr', 0)

        # === 공통 필터 ===
        surge_filtered = (prev_close is not None and prev_close > 0 and
                         (yesterday_close - prev_close) / prev_close <= cls.MAX_SURGE_RATIO)
        intraday_surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO
        prev_day_bullish = (prev_close is not None and yesterday_close > prev_close)

        if not (surge_filtered and intraday_surge_filtered and prev_day_bullish):
            # 공통 필터 미충족 → 연속성 리셋
            new_state = {'curr_signal': False, 'consecutive_count': 0, 'last_update': datetime.now().isoformat()}
            await redis_client.setex(f"entry:{swing_id}", 900, json.dumps(new_state))
            return None

        # === 시나리오 A: 눌림목 매집 진입 ===
        scenario_a = False
        if atr > 0:
            accum_lower = realtime_ema20 + (atr * cls.ACCUM_ENTRY_ATR_LOWER)
            accum_upper = realtime_ema20 + (atr * cls.ACCUM_ENTRY_ATR_UPPER)

            if accum_lower <= curr_price <= accum_upper:
                obv_accumulating = (realtime_obv_z > cls.ACCUM_ENTRY_OBV_MIN) and (prev_obv_z is not None and realtime_obv_z > prev_obv_z)
                adx_mid_range = cls.ACCUM_ENTRY_ADX_MIN <= realtime_adx <= cls.ACCUM_ENTRY_ADX_MAX
                ema_rising = (prev_ema20 is not None and realtime_ema20 > prev_ema20)
                trend_direction = realtime_plus_di > realtime_minus_di  # 상승 추세 방향

                scenario_a = obv_accumulating and adx_mid_range and ema_rising and trend_direction

        # === 시나리오 B: 추세 추종 EMA 돌파 진입 ===
        scenario_b = False
        if yesterday_ema20 is not None and yesterday_close is not None:
            ema_crossover = (yesterday_close < yesterday_ema20) and (curr_price > realtime_ema20)
            within_gap_limit = curr_price <= realtime_ema20 * cls.BREAKOUT_ENTRY_GAP_MAX

            if ema_crossover and within_gap_limit:
                trend_direction = realtime_plus_di > realtime_minus_di
                adx_sufficient = realtime_adx > cls.BREAKOUT_ENTRY_ADX_MIN  # 최소 추세 강도
                obv_positive = realtime_obv_z > cls.BREAKOUT_ENTRY_OBV_MIN

                scenario_b = trend_direction and adx_sufficient and obv_positive

        current_signal = scenario_a or scenario_b

        # 연속성 체크 (Redis, swing_id별 분리)
        prev_state_key = f"entry:{swing_id}"
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
            scenario_name = "눌림목매집" if scenario_a else "EMA돌파"
            logger.info(f"[{symbol}] 1차 매수 신호 발생 ({scenario_name}, 연속 {consecutive}회)")
            return {'action': 'BUY', 'price': curr_price, 'reasons': ["1차 매수", scenario_name]}
        elif current_signal:
            scenario_name = "눌림목매집" if scenario_a else "EMA돌파"
            logger.info(f"[{symbol}] 매수 신호 대기 중 ({scenario_name}, {consecutive}/{cls.CONSECUTIVE_REQUIRED})")

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
        return result if result else {"action": "HOLD", "reasons": []}

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
        """2차 매수 신호 체크 (통합: 추세 안정화 후 추가 매수)"""
        try:
            curr_price = float(current_price)

            # 시간 필터: 1차 매수 후 최소 20분 경과 체크
            time_key = f"first_buy_time:{swing_id}"
            if await redis_client.exists(time_key):
                return None

            # 지표 사용 (모두 실시간 증분 계산 완료 상태)
            realtime_adx = cached_indicators['realtime_adx']
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']
            atr = cached_indicators['realtime_atr']
            realtime_ema20 = cached_indicators['realtime_ema20']
            realtime_obv_z = cached_indicators['realtime_obv_z']

            # 가격 위치: EMA 이상 ~ EMA + ATR × 2.0 (추세 확인 + 과열 방지)
            lower = realtime_ema20 + (atr * cls.SECOND_BUY_ATR_LOWER)
            upper = realtime_ema20 + (atr * cls.SECOND_BUY_ATR_UPPER)

            if lower <= curr_price <= upper:
                # 추세 안정: ADX >= 20 + 상승 방향
                if realtime_adx >= cls.SECOND_BUY_ADX_MIN and realtime_plus_di > realtime_minus_di:
                    # 수급 확인: OBV z-score
                    if realtime_obv_z >= cls.SECOND_BUY_OBV_MIN:
                        atr_ratio = f"{(curr_price-realtime_ema20)/atr:.2f}" if atr > 0 else "N/A"
                        logger.info(f"[{symbol}] ✅ 2차 매수 신호 (추세안정): EMA+ATR×{atr_ratio}, ADX={realtime_adx:.1f}")
                        return {
                            'action': 'BUY',
                            'price': curr_price,
                            'reasons': ["2차 매수", "추세 안정"]
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

        # ATR=0 가드: ATR이 유효하지 않으면 손절 체크 스킵
        if atr <= 0:
            logger.warning(f"[{symbol}] ATR이 0 이하, 손절 체크 스킵")
            return {"action": "HOLD", "reasons": []}

        # EMA-ATR 동적 손절
        ema_atr_stop = realtime_ema20 - (atr * cls.ATR_MULTIPLIER)
        if curr_price <= ema_atr_stop:
            logger.warning(f"[{symbol}] 🚨 즉시 매도 신호: EMA-ATR손절(현재가≤{ema_atr_stop:,.0f})")
            return {
                "action": "SELL",
                "reasons": [
                    "손절",
                    f"EMA-ATR 이탈 (손절가: {ema_atr_stop:,.0f}원)"
                ]
            }

        return {"action": "HOLD", "reasons": []}

    @classmethod
    async def update_eod_signals_to_db(
        cls,
        row,
        prev_row,
        prev_prev_row,
        peak_price: float,
        signal: int
    ):
        """
        EOD trailing stop 체크 + PEAK_PRICE 업데이트

        Args:
            row: 오늘 지표
            prev_row: 어제 지표
            prev_prev_row: 그저께 지표
            peak_price: 현재 PEAK_PRICE
            signal: 현재 SIGNAL (1, 2, 3)

        Returns:
            (eod_signals_json, updated_peak_price)
        """
        close = float(row['STCK_CLPR'])

        # 고점 업데이트
        updated_peak = max(peak_price or 0, close)

        # NaN 체크
        required_cols = ["minus_di", "plus_di", "obv_z"]
        if any(pd.isna(row.get(col)) for col in required_cols):
            return None, updated_peak
        if any(pd.isna(prev_row.get(col)) for col in ["minus_di", "plus_di"]):
            return None, updated_peak
        if any(pd.isna(prev_prev_row.get(col)) for col in ["minus_di", "plus_di", "obv_z"]):
            return None, updated_peak

        # 조건 1: 추세 약화 — (+DI - -DI) 격차 2일 연속 감소
        di_spread_today = row["plus_di"] - row["minus_di"]
        di_spread_prev = prev_row["plus_di"] - prev_row["minus_di"]
        di_spread_prev2 = prev_prev_row["plus_di"] - prev_prev_row["minus_di"]
        trend_weakening = di_spread_today < di_spread_prev < di_spread_prev2

        # 조건 2: 수급 약화 — OBV z-score 감소 (2일전 대비)
        supply_weakening = row["obv_z"] < prev_prev_row["obv_z"]

        if not (trend_weakening and supply_weakening):
            return None, updated_peak

        # 고점 대비 하락률 계산
        if updated_peak <= 0:
            return None, updated_peak
        drawdown_pct = (updated_peak - close) / updated_peak * 100

        # ATR 기반 동적 trailing stop 임계값 계산
        atr = float(row.get('atr', 0))
        if atr > 0 and updated_peak > 0:
            atr_pct = (atr / updated_peak) * 100  # ATR을 고점 대비 비율(%)로 변환
            trailing_partial = max(atr_pct * cls.TRAILING_STOP_ATR_PARTIAL_MULT, cls.TRAILING_STOP_PARTIAL_MIN)
            trailing_full = max(atr_pct * cls.TRAILING_STOP_ATR_FULL_MULT, cls.TRAILING_STOP_FULL_MIN)
        else:
            # ATR 무효 시 고정값 폴백
            trailing_partial = cls.TRAILING_STOP_PARTIAL
            trailing_full = cls.TRAILING_STOP_FULL

        # 약화 사유 동적 생성
        weakness_reasons = []
        if trend_weakening:
            weakness_reasons.append("추세약화")
        if supply_weakening:
            weakness_reasons.append("수급약화")
        weakness_str = "+".join(weakness_reasons)

        # 매도 결정
        action = None
        reason = ""

        if signal == 3 and drawdown_pct >= trailing_full:
            action = "SELL_ALL"
            reason = f"2차 전량매도(고점대비 -{drawdown_pct:.1f}%+{weakness_str}, 기준:{trailing_full:.1f}%)"
        elif signal in (1, 2) and drawdown_pct >= trailing_partial:
            action = "SELL_PRIMARY"
            reason = f"1차 분할매도(고점대비 -{drawdown_pct:.1f}%+{weakness_str}, 기준:{trailing_partial:.1f}%)"

        if action:
            eod_json = json.dumps({"action": action, "reason": reason, "date": date.today().isoformat()})
            return eod_json, updated_peak

        return None, updated_peak
