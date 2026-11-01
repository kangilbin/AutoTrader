"""
단일 20EMA 매매 전략 (Single EMA Strategy)

**매수 조건 (Entry Conditions):**
1. EMA 추세: 현재가 >= 실시간 EMA20 * 0.995 (0.5% 여유)
2. 수급 강도: OBV z-score >= 1.0
3. 괴리율 필터: EMA 괴리율 <= 5%
4. 추세 방향: +DI > -DI
5. 연속 확인: 2회 (Redis 상태 관리, 5분 주기 노이즈 필터링)

**2차 매수 조건 (20분 경과 후):**
- **시나리오 A (추세 강화형):** EMA + ATR × (0.3~2.0), ADX > 25, OBV z-score >= 1.2
- **시나리오 B (눌림목 반등):** EMA ± ATR × 0.5, 18 <= ADX <= 23, OBV z-score > 0.5, 장중 저가 대비 0.4% 반등

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
    -   수급 이탈 (OBV z-score)
2.  **2차 전량 매도:** 1차 매도 후, 3개 조건이 **모두** 충족 시
"""
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
    PULLBACK_BUY_OBV_MIN = 0.5       # OBV z-score 최소값 (눌림목 반등, 베이스는 0.0)

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
                'ema120': 49000.0, # 어제 종가 기준 EMA120 (하락장 필터용)
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
                'ema120': data['ema120'],
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
            # 실시간 DI 사용
            realtime_plus_di = cached_indicators['realtime_plus_di']
            realtime_minus_di = cached_indicators['realtime_minus_di']

            # 실시간 EMA 사용
            realtime_ema20 = cached_indicators['realtime_ema20']
            realtime_ema120 = cached_indicators['realtime_ema120']
            gap_ratio = cached_indicators['realtime_gap_ratio']

            # 실시간 OBV z-score 사용
            realtime_obv_z = cached_indicators['realtime_obv_z']

        except Exception as e:
            logger.error(f"[{symbol}] 매수 신호 지표 계산 실패: {e}", exc_info=True)
            return None

        # 하락장 필터: 20 EMA < 120 EMA 시 매수 금지
        if realtime_ema20 < realtime_ema120:
            logger.debug(f"[{symbol}] 하락장 감지 (EMA20={realtime_ema20:.0f} < EMA120={realtime_ema120:.0f}), 매수 금지")
            return None

        # 조건 검증
        price_above_ema = curr_price >= realtime_ema20 * 0.995  # 0.5% 여유
        supply_strong = realtime_obv_z >= cls.OBV_Z_BUY_THRESHOLD
        gap_filtered = gap_ratio <= cls.MAX_GAP_RATIO
        trend_upward = realtime_plus_di > realtime_minus_di

        current_signal = all([price_above_ema, supply_strong, gap_filtered, trend_upward])

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
            logger.info(f"[{symbol}] 1차 매수 신호 발생 (연속 {consecutive}회)")
            return {'action': 'BUY', 'price': curr_price, 'reasons': ["1차 매수"]}
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
            realtime_ema120 = cached_indicators['realtime_ema120']
            realtime_obv_z = cached_indicators['realtime_obv_z']

            # 하락장 필터: 20 EMA < 120 EMA 시 2차 매수 금지
            if realtime_ema20 < realtime_ema120:
                logger.debug(f"[{symbol}] 하락장 감지 (EMA20={realtime_ema20:.0f} < EMA120={realtime_ema120:.0f}), 2차 매수 금지")
                return None

            # === 시나리오 A: 추세 강화형 ===
            # 가격 가드레일: EMA + ATR × (0.3 ~ 2.5)
            trend_lower = realtime_ema20 + (atr * cls.TREND_BUY_ATR_LOWER)
            trend_upper = realtime_ema20 + (atr * cls.TREND_BUY_ATR_UPPER)

            if trend_lower <= curr_price <= trend_upper:
                # 추세 강도: ADX > 25
                if realtime_adx > cls.TREND_BUY_ADX_MIN:
                    # 추세 방향: +DI > -DI
                    if realtime_plus_di > realtime_minus_di:
                        # 수급 지속: OBV z-score
                        if realtime_obv_z >= cls.TREND_BUY_OBV_THRESHOLD:
                            logger.info(f"[{symbol}] ✅ 2차 매수 신호 (추세 강화형): EMA+ATR×{(curr_price-realtime_ema20)/atr:.2f}")
                            return {
                                'action': 'BUY',
                                'price': curr_price,
                                'reasons': ["2차 매수", "추세 강화"]
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
                        # 수급 유지: OBV z-score (중립 이상)
                        supply_ok = realtime_obv_z > cls.PULLBACK_BUY_OBV_MIN
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
                                        'reasons': ["2차 매수", "눌림목 반등"]
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
        current_eod_signals: str
    ) -> str:
        """
        종가 기준 EOD 신호를 DB에 저장할 JSON 생성

        Args:
            row: today 지표
            prev_row: yesterday 지표
            current_eod_signals: 현재 DB의 EOD_SIGNALS JSON

        Returns:
            업데이트된 EOD_SIGNALS JSON 문자열
        """

        # 기존 신호 로드
        signals = json.loads(current_eod_signals) if current_eod_signals else {}
        current_date = date.today().isoformat()

        # 3일 지난 신호 삭제
        for signal_name, date_str in list(signals.items()):
            signal_date = date.fromisoformat(date_str)
            if (date.today() - signal_date).days >= 3:
                del signals[signal_name]

        # EOD 신호 체크 및 갱신 (발생 시 저장, 미발생 시 해제)
        if cls._check_ema_breach_eod(row): signals['ema_breach'] = current_date
        if cls._check_trend_weakness_eod(row, prev_row): signals['trend_weak'] = current_date
        if cls._check_supply_weakness_eod(row): signals['supply_weak'] = current_date

        return json.dumps(signals) if signals else None
