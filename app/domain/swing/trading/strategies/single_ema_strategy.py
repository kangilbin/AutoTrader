import pandas as pd
import talib as ta
import numpy as np
import json
import logging
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
from app.domain.swing.indicators import TechnicalIndicators
from .base_trading_strategy import TradingStrategy
from .base_single_ema import BaseSingleEMAStrategy
from app.domain.swing.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class SingleEMAStrategy(TradingStrategy, BaseSingleEMAStrategy):
    """단일 20EMA 매매 전략"""

    # 전략 이름
    name = "단일 20EMA 전략"

    # ========================================
    # 실전 전용 파라미터
    # ========================================
    CONSECUTIVE_REQUIRED = 2         # 연속 확인 횟수 (10분)
    ENTRY_STATE_TTL = 1800           # 연속성 상태 Redis TTL (초, 30분)


    # ========================================
    # 지표 계산 및 유틸리티
    # ========================================

    # 없으면 어떤 판단도 불가한 지표 (실시간 EMA/ATR/ADX/OBV 증분 계산의 입력)
    REQUIRED_CACHE_KEYS = (
        'ema20', 'adx', 'plus_dm14', 'minus_dm14', 'atr',
        'obv', 'obv_recent_diffs', 'close', 'high', 'low',
    )

    # 없으면 해당 기능만 비활성되는 지표 (손절/익절 평가에는 불필요)
    OPTIONAL_CACHE_DEFAULTS = {
        'obv_ema': None,           # accum 실시간 증분 기준 — 없으면 매수 게이트만 불능
        'avg_vol20': None,         # accum 정규화 (20일 평균거래량)
        'obv_z': 0.0,
        'open': 0.0,               # 전일 윗꼬리 필터 (0이면 필터 미적용)
        'date': None,
        'avg_daily_amount': 0.0,   # TWAP 분할 기준 (0이면 단일 주문)
    }

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
                'obv_recent_diffs': [...],  # 최근 13일 OBV diff (매수 7일/익절 14일 공용)
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
        except Exception as e:
            logger.warning(f"[{symbol}] 캐시 조회 실패: {e}")
            return None

        # 필수 지표가 하나라도 없으면 손절 판단조차 불가 → 이번 사이클 포기
        missing_required = [k for k in cls.REQUIRED_CACHE_KEYS if data.get(k) is None]
        if missing_required:
            logger.error(
                f"[{symbol}] 캐시 필수 지표 누락 {missing_required} → 판단 불가. "
                f"지표 캐시 재생성(warmup) 필요"
            )
            return None

        indicators = {k: data[k] for k in cls.REQUIRED_CACHE_KEYS}

        # 선택 지표는 없어도 해당 게이트만 비활성 — 매도/손절은 계속 동작해야 한다
        # (구버전 캐시가 남아 있어도 포지션이 무방비가 되지 않도록)
        missing_optional = [k for k in cls.OPTIONAL_CACHE_DEFAULTS if data.get(k) is None]
        if missing_optional:
            logger.warning(
                f"[{symbol}] 캐시 선택 지표 누락 {missing_optional} → 해당 게이트만 비활성 "
                f"(매도·손절은 정상 평가)"
            )
        for key, default in cls.OPTIONAL_CACHE_DEFAULTS.items():
            value = data.get(key)
            indicators[key] = default if value is None else value

        # 완성봉 기준 수급 축적도 (백테스트 _check_entry_conditions와 동일 정의)
        # 매수 게이트는 실시간 accum을 쓰고, 이 값은 두 정의의 차이를 관측하기 위한 비교용이다
        indicators['accum'] = TechnicalIndicators.calculate_accum(
            indicators['obv'], indicators['obv_ema'], indicators['avg_vol20']
        )

        return indicators

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

                # 1-2. 없으면 증분 계산 (매수용 7일: 마지막 6개 diffs + 오늘)
                yesterday_obv = cached_indicators['obv']
                yesterday_close = cached_indicators['close']
                recent_diffs = cached_indicators['obv_recent_diffs'][-6:]

                realtime_obv_z = TechnicalIndicators.calculate_realtime_obv_zscore(
                    yesterday_obv, yesterday_close, current_price, current_volume, recent_diffs
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
            # 실시간 증분 데이터
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']
            realtime_adx = cached_indicators['realtime_adx']
            realtime_ema20 = cached_indicators['realtime_ema20']
            # 중장기 수급 축적도 — 당일 거래량까지 증분 반영한 실시간 값을 사용한다.
            # (5분 사이클로 당일 수급 변화에 바로 반응하기 위함)
            # 캐시에 obv_ema/avg_vol20이 없으면 None(판단 불가)
            accum = cached_indicators.get('realtime_accum')
            accum_eod = cached_indicators.get('accum')  # 전일 완성봉 기준 (비교·로그용)

        except Exception as e:
            logger.error(f"[{symbol}] 매수 신호 지표 계산 실패: {e}", exc_info=True)
            return None

        # 캐시에서 전일 데이터 추출
        yesterday_ema20 = cached_indicators.get('ema20')       # 전일 EMA20 (종가 기준)

        # === 공통 필터 ===
        surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO

        # 전일 윗꼬리 긴 캔들 필터 (양봉/음봉 무관, 매도 압력 강한 날 다음 매수 차단)
        prev_open = cached_indicators.get('open', 0)
        prev_close = cached_indicators.get('close', 0)
        prev_high = cached_indicators.get('high', 0)
        prev_low = cached_indicators.get('low', 0)
        shadow_filtered = True
        candle_range = prev_high - prev_low
        if candle_range > 0 and prev_close > 0 and candle_range / prev_close > cls.MIN_CANDLE_RANGE_PCT:
            upper_shadow = prev_high - max(prev_open, prev_close)
            upper_shadow_ratio = upper_shadow / candle_range
            if upper_shadow_ratio >= cls.UPPER_SHADOW_RATIO_MAX:
                shadow_filtered = False
                logger.debug(f"[{symbol}] 전일 윗꼬리 필터 — 매수 차단 (비율={upper_shadow_ratio:.1%})")

        # 갭 하락 필터: 당일 시가가 전일 저가 미만이면 매수 차단
        gap_filtered = True
        if prev_low > 0 and curr_price < prev_low:
            gap_filtered = False
            logger.debug(f"[{symbol}] 갭 하락 필터 — 매수 차단 (현재가={curr_price:.0f} < 전일저가={prev_low:.0f})")

        if not surge_filtered or not shadow_filtered or not gap_filtered:
            # 공통 필터 미충족 → 연속성 리셋
            new_state = {'curr_signal': False, 'consecutive_count': 0, 'last_update': datetime.now().isoformat()}
            await redis_client.setex(f"entry:{swing_id}", cls.ENTRY_STATE_TTL, json.dumps(new_state))
            return None

        # === 추세 추종 EMA 돌파 진입 ===
        current_signal = False
        if yesterday_ema20 is not None:
            price_above_ema = curr_price > realtime_ema20 # 현재가 주가가 EMA20 보다 높을 때
            within_gap_limit = curr_price <= realtime_ema20 * cls.BREAKOUT_ENTRY_GAP_MAX # EMA20 대비 최대 +6%까지 허용

            if price_above_ema and within_gap_limit:
                trend_direction = realtime_plus_di > realtime_minus_di
                adx_sufficient = realtime_adx > cls.BREAKOUT_ENTRY_ADX_MIN  # 최소 추세 강도

                # 중장기(스윙) 수급 축적 게이트
                if accum is None:
                    # 판단 불가 → 매수는 보류(fail-closed). 매도/손절은 이 값을 쓰지 않는다
                    obv_ok = False
                    logger.warning(f"[{symbol}] 수급 축적도 계산 불가 → 매수 보류 (지표 캐시 확인 필요)")
                else:
                    obv_ok = accum > cls.ACCUM_HIGH  # 수급이 자기 평균 위로 충분히 쌓였을 때만
                    # 실시간 accum은 당일 거래량이 쌓일수록 커진다. 백테스트가 튜닝된
                    # 완성봉 값(accum_eod)과 함께 남겨, 임계값 재산정 시 근거로 쓴다
                    if accum_eod is not None and (accum > cls.ACCUM_HIGH) != (accum_eod > cls.ACCUM_HIGH):
                        logger.info(
                            f"[{symbol}] accum 판정 불일치: 실시간={accum:.2f}(사용) "
                            f"vs 완성봉={accum_eod:.2f} (임계값 {cls.ACCUM_HIGH})"
                        )

                current_signal = trend_direction and adx_sufficient and obv_ok

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
        await redis_client.setex(prev_state_key, cls.ENTRY_STATE_TTL, json.dumps(new_state))

        if consecutive >= cls.CONSECUTIVE_REQUIRED:
            logger.info(f"[{symbol}] 1차 매수 신호 발생 (EMA돌파, 연속 {consecutive}회)")
            # 차수 표기("N차 매수")는 signal_on_complete를 아는 executor가 붙인다.
            # 여기서 하드코딩하면 이력에 "1차 매수"가 중복 저장된다.
            return {'action': 'BUY', 'price': curr_price, 'reasons': ["EMA돌파"]}
        elif current_signal:
            logger.info(f"[{symbol}] 매수 신호 대기 중 (EMA돌파, {consecutive}/{cls.CONSECUTIVE_REQUIRED})")

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
        cached_indicators: Dict,
        signal: int = 1,
        peak_price: float = 0,
    ) -> Dict:
        """청산 판정 — 단일 청산선(3단계)

        손절/1차 익절/2차 익절을 하나의 청산선으로 통합했다.
        청산선은 이익이 쌓일수록 올라가므로, 이탈 시점의 가격이 곧 확정 손익이다.

        Returns:
            {"action": "SELL"|"HOLD", "reasons": [...]}
        """
        curr_price = float(current_price)
        entry = float(entry_price) if entry_price else 0
        # 청산선 폭은 **완성봉 ATR**로 잰다. 실시간 증분 ATR을 쓰면 장 초반에 값이 작아
        # 청산선이 타이트해져 조기 청산되고, 같은 날에도 평가 시각마다 청산선이 달라진다.
        # (백테스트도 완성봉 ATR을 쓰므로 정의가 일치한다)
        atr = cached_indicators.get('atr', 0) or 0

        if entry <= 0:
            logger.warning(f"[{symbol}] ENTRY_PRICE 없음, 청산 체크 스킵")
            return {"action": "HOLD", "reasons": []}

        if atr <= 0:
            logger.warning(f"[{symbol}] ATR이 0 이하, 청산 체크 스킵")
            return {"action": "HOLD", "reasons": []}

        # 장중 고가가 PEAK에 아직 반영되지 않았을 수 있으므로 현재가도 후보에 넣는다
        peak = max(peak_price or 0, entry, curr_price)
        exit_line = cls.calculate_exit_line(entry, peak, atr)

        if curr_price <= exit_line:
            gain_pct = (curr_price - entry) / entry * 100
            peak_gain = (peak - entry) / entry * 100
            if peak_gain >= cls.TRAILING_ACTIVATE_PCT:
                stage = "이익확정"
            elif peak_gain >= cls.BREAKEVEN_ACTIVATE_PCT:
                stage = "본전방어"
            else:
                stage = "손절"
            logger.warning(
                f"[{symbol}] 🚨 청산({stage}): 현재가 {curr_price:,.2f} ≤ 청산선 {exit_line:,.2f} "
                f"(평단 {entry:,.2f}, 고점 {peak:,.2f}, 손익 {gain_pct:+.1f}%)"
            )
            return {
                "action": "SELL",
                "reasons": [stage, f"청산선 {exit_line:,.2f}", f"손익 {gain_pct:+.1f}%"],
            }

        return {"action": "HOLD", "reasons": []}

    @classmethod
    async def check_partial_take_profit(
        cls,
        symbol: str,
        current_price: Decimal,
        entry_price: Decimal,
        signal: int,
    ) -> Optional[Dict]:
        """부분 익절 — 목표 수익률 도달 시 절반 확정 (SIGNAL 1에서만, 1회)

        PARTIAL_TAKE_PROFIT_PCT <= 0 이면 비활성.
        """
        if signal != 1 or cls.PARTIAL_TAKE_PROFIT_PCT <= 0:
            return None

        entry = float(entry_price) if entry_price else 0
        if entry <= 0:
            return None

        target = entry * (1 + cls.PARTIAL_TAKE_PROFIT_PCT / 100)
        curr_price = float(current_price)
        if curr_price < target:
            return None

        logger.info(
            f"[{symbol}] 부분 익절: 현재가 {curr_price:,.2f} ≥ 목표 {target:,.2f} "
            f"(+{cls.PARTIAL_TAKE_PROFIT_PCT:.0f}%) → 절반 매도"
        )
        return {
            "action": "SELL_HALF",
            "reasons": [f"부분익절(+{cls.PARTIAL_TAKE_PROFIT_PCT:.0f}%)",
                        f"목표가 {target:,.2f}"],
        }
