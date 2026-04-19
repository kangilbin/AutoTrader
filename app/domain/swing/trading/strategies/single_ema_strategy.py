"""
단일 20EMA 매매 전략 (Single EMA Strategy)

Entry Conditions (all 5 must be met + 2회 연속 확인):
1. 가격 위치: 현재가 > 실시간 EMA20, 괴리율 <= 2%
2. 수급 강도: 외국인 >= 3%
3. 수급 유지: 이전 대비 20% 이상 감소하지 않음
4. 거래량: 전일 대비 120% 이상
5. 급등 필터: 당일 상승률 <= 7%
6. 2회 연속 확인 (Redis 상태 관리)

Exit Conditions (종가 기준, 다음날 시초 매도):
1. 절대 손절: -3% (장중 실시간, 즉시 전량 매도)
2. 1차 매도: (EMA 이탈, 외국인 이탈, 추세 약화) 중 2개 → 다음날 시초 50% 매도
3. 2차 매도: (EMA 이탈, 외국인 이탈, 추세 약화) 모두 충족 → 다음날 시초 전량 매도
"""
import pandas as pd
import talib as ta
import numpy as np
import json
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import logging

from .base_trading_strategy import TradingStrategy

logger = logging.getLogger(__name__)


class SingleEMAStrategy(TradingStrategy):
    """단일 20EMA 매매 전략"""

    # 전략 이름
    name = "단일 20EMA 전략"

    # 전략 파라미터
    EMA_PERIOD = 20

    # 1차 매수 진입 조건
    MAX_GAP_RATIO = 0.02  # 괴리율 2% 이내
    FRGN_STRONG_THRESHOLD = 3.0  # 외국인 3% 이상
    MAINTAIN_RATIO = 0.8  # 수급 유지 기준 (80%)
    VOLUME_RATIO_THRESHOLD = 1.2  # 거래량 120% 이상
    MAX_SURGE_RATIO = 0.07  # 급등 필터 7%
    CONSECUTIVE_REQUIRED = 2  # 2회 연속 확인

    # 2차 매수 진입 조건
    SECOND_BUY_PRICE_GAIN_MIN = 0.01  # 최소 1% 상승
    SECOND_BUY_PRICE_GAIN_MAX = 0.04  # 최대 4% 상승
    SECOND_BUY_FRGN_THRESHOLD = 1.2  # 외국인 1.2% 이상 (1차보다 완화, 거래량 강도 포함)
    SECOND_BUY_SAFETY_MARGIN = 0.04  # 손절가 위 4% 안전 마진
    SECOND_BUY_TIME_MIN = 600  # 최소 10분 경과 (같은 날)

    # 청산 조건
    STOP_LOSS_FIXED = -0.03  # 고정 손절 -3%
    SUPPLY_REVERSAL_THRESHOLD = -2.0  # 수급 반전 (순매도 -2%)
    SUPPLY_WEAK_THRESHOLD = 1.0  # 수급 약화 (1% 미만)
    EMA_BREACH_REQUIRED = 2  # EMA 이탈 2회 연속 확인

    @classmethod
    def get_realtime_ema20(cls, df: pd.DataFrame, current_price: float) -> Optional[float]:
        """
        실시간 EMA20 계산 (레거시 - DataFrame 기반)

        ⚠️ 주의: 이 메서드는 캐싱을 사용하지 않습니다.
        실전 매매에서는 get_realtime_ema20_cached() 사용 권장

        Args:
            df: 과거 주가 데이터 (OHLCV)
            current_price: 현재가

        Returns:
            실시간 EMA20 값 또는 None
        """
        if len(df) < cls.EMA_PERIOD:
            return None

        # 종가 배열 생성
        close_prices = df["STCK_CLPR"].values.astype(float)

        # 현재가 추가
        close_with_today = np.append(close_prices, current_price)

        # EMA 계산
        ema_array = ta.EMA(close_with_today, timeperiod=cls.EMA_PERIOD)

        if len(ema_array) == 0 or np.isnan(ema_array[-1]):
            return None

        return float(ema_array[-1])

    @classmethod
    async def get_realtime_ema20_cached(
        cls,
        redis_client,
        st_code: str,
        current_price: float,
        stock_service=None
    ) -> Optional[float]:
        """
        실시간 EMA20 조회 (캐시 우선 전략)

        1. Redis 캐시 조회 (워밍업 배치로 사전 계산됨)
        2. 캐시 히트: 점진적 계산 (어제 EMA + 오늘 종가)
        3. 캐시 미스: Fallback으로 즉시 계산 (DB 조회)

        Args:
            redis_client: Redis 클라이언트
            st_code: 종목 코드
            current_price: 현재가
            stock_service: StockService 인스턴스 (fallback용, 선택적)

        Returns:
            실시간 EMA20 값 또는 None
        """
        cache_key = f"ema20:{st_code}"

        # ========================================
        # 1단계: 캐시 조회 (대부분 여기서 히트)
        # ========================================
        try:
            cached_ema_str = await redis_client.get(cache_key)

            if cached_ema_str:
                # ✅ 캐시 히트 (배치로 사전 계산됨)
                prev_ema = float(cached_ema_str)
                k = 2 / (cls.EMA_PERIOD + 1)  # 0.0952 for period=20

                # 점진적 계산: EMA(오늘) = 오늘종가 × k + EMA(어제) × (1-k)
                new_ema = (current_price * k) + (prev_ema * (1 - k))

                # 업데이트 (TTL: 7일)
                await redis_client.setex(cache_key, 604800, str(new_ema))

                logger.debug(f"[{st_code}] EMA20 캐시 히트: {prev_ema:.2f} → {new_ema:.2f}")
                return new_ema

        except Exception as e:
            logger.warning(f"[{st_code}] Redis 조회 실패: {e}, Fallback 실행")

        # ========================================
        # 2단계: 캐시 미스 - Fallback (거의 발생 안 함)
        # ========================================
        logger.warning(f"[{st_code}] EMA20 캐시 미스! Fallback 실행...")

        if stock_service is None:
            logger.error(f"[{st_code}] stock_service 없음, EMA 계산 불가")
            return None

        # 즉시 계산 (배치 실패 시 대비)
        try:
            from datetime import datetime, timedelta
            import pandas as pd

            start_date = datetime.now() - timedelta(days=120)
            price_history = await stock_service.get_stock_history(st_code, start_date)

            if not price_history or len(price_history) < cls.EMA_PERIOD:
                logger.error(f"[{st_code}] 데이터 부족: {len(price_history) if price_history else 0}일")
                return None

            df = pd.DataFrame(price_history)
            close_prices = df["STCK_CLPR"].values.astype(float)
            ema_array = ta.EMA(close_prices, timeperiod=cls.EMA_PERIOD)

            if len(ema_array) == 0 or np.isnan(ema_array[-1]):
                logger.error(f"[{st_code}] EMA 계산 실패")
                return None

            prev_ema = float(ema_array[-1])
            k = 2 / (cls.EMA_PERIOD + 1)
            new_ema = (current_price * k) + (prev_ema * (1 - k))

            # 캐시 저장
            await redis_client.setex(cache_key, 604800, str(new_ema))

            logger.info(f"[{st_code}] Fallback 계산 완료: {new_ema:.2f} (데이터: {len(price_history)}일)")
            return new_ema

        except Exception as e:
            logger.error(f"[{st_code}] Fallback 계산 실패: {e}", exc_info=True)
            return None

    @classmethod
    async def check_entry_signal(
        cls,
        redis_client,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal,
        frgn_ntby_qty: int,
        pgtr_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float
    ) -> Optional[Dict]:
        """
        진입 신호 체크 (2회 연속 확인)

        Args:
            redis_client: Redis 클라이언트
            symbol: 종목코드
            df: 주가 데이터
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량
            pgtr_ntby_qty: 프로그램 순매수량
            acml_vol: 누적거래량
            prdy_vrss_vol_rate: 전일대비 거래량 비율
            prdy_ctrt: 전일대비 상승률

        Returns:
            매수 신호 정보 또는 None
        """
        # === EMA 계산 ===
        curr_price = float(current_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20 is None:
            logger.warning(f"[{symbol}] EMA 계산 불가")
            return None

        # === 조건 A: 가격 위치 ===
        price_condition = curr_price > realtime_ema20
        gap_ratio = (curr_price - realtime_ema20) / realtime_ema20
        gap_condition = gap_ratio <= cls.MAX_GAP_RATIO

        # === 조건 B: 수급 강도 (외국인만) ===
        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        supply_condition = frgn_ratio >= cls.FRGN_STRONG_THRESHOLD

        # === 조건 C: 수급 유지 (외국인만) ===
        supply_maintained = True
        prev_state_key = f"entry:{symbol}"
        prev_state_str = await redis_client.get(prev_state_key)

        if prev_state_str:
            prev_state = json.loads(prev_state_str)
            prev_frgn_ratio = prev_state.get('curr_frgn_ratio', 0)

            supply_maintained = frgn_ratio >= prev_frgn_ratio * cls.MAINTAIN_RATIO

        # === 조건 D: 거래량 ===
        volume_condition = prdy_vrss_vol_rate >= (cls.VOLUME_RATIO_THRESHOLD * 100)

        # === 조건 E: 급등 필터 ===
        surge_filtered = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)

        # === 전체 조건 ===
        current_signal = (
            price_condition and gap_condition and
            supply_condition and supply_maintained and
            volume_condition and surge_filtered
        )

        # === 연속 카운트 ===
        consecutive = 0
        if current_signal:
            if prev_state_str:
                prev_state = json.loads(prev_state_str)
                if prev_state.get('curr_signal'):
                    consecutive = prev_state.get('consecutive_count', 0) + 1
                else:
                    consecutive = 1
            else:
                consecutive = 1

        # === 상태 저장 (TTL 15분 = 900초) ===
        new_state = {
            'curr_signal': current_signal,
            'consecutive_count': consecutive,
            'curr_price': curr_price,
            'curr_ema20': realtime_ema20,
            'curr_frgn_ratio': frgn_ratio,
            'last_update': datetime.now().isoformat()
        }
        await redis_client.setex(prev_state_key, 900, json.dumps(new_state))

        # === 최종 판정 ===
        if consecutive >= cls.CONSECUTIVE_REQUIRED:
            logger.info(
                f"[{symbol}] 1차 매수 신호: consecutive={consecutive}, "
                f"가격={curr_price:,.0f}, EMA={realtime_ema20:,.0f}, "
                f"외국인={frgn_ratio:.2f}%"
            )
            return {
                'action': 'BUY',
                'price': curr_price,
                'ema20': realtime_ema20,
                'frgn_ratio': frgn_ratio,
                'gap_ratio': gap_ratio,
                'consecutive': consecutive
            }

        # 조건 충족 중이지만 아직 2회 미달
        if current_signal and consecutive == 1:
            logger.info(
                f"[{symbol}] 신호 대기 중: consecutive=1/2, "
                f"가격={curr_price:,.0f}, EMA={realtime_ema20:,.0f}"
            )

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
        pgtr_ntby_qty: int,
        acml_vol: int
    ) -> Dict:
        """
        매도 신호 체크 (추세/수급 기반, 익절 없음)

        청산 우선순위:
        1. 고정 손절 -3%
        2. 수급 반전 (순매도 -2% 이상)
        3. EMA 이탈 (2회 연속)
        4. 수급 약화 (둘 다 1% 미만)
        5. 추세 악화 (EMA 아래 악화)

        Args:
            redis_client: Redis 클라이언트
            position_id: 포지션 ID (SWING_ID)
            symbol: 종목코드
            df: 주가 데이터
            current_price: 현재가
            entry_price: 진입가
            frgn_ntby_qty: 외국인 순매수량
            pgtr_ntby_qty: 프로그램 순매수량
            acml_vol: 누적거래량

        Returns:
            매도 신호 정보
        """
        curr_price = float(current_price)
        entry = float(entry_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20 is None:
            logger.warning(f"[{symbol}] EMA 계산 불가, HOLD 유지")
            return {"action": "HOLD", "reason": "EMA 계산 불가"}

        profit_rate = (curr_price - entry) / entry
        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        # ========================================
        # 1. 고정 손절 -3% (최우선)
        # ========================================
        if profit_rate <= cls.STOP_LOSS_FIXED:
            logger.warning(f"[{symbol}] 고정 손절: {profit_rate*100:.2f}%")
            return {"action": "SELL", "reason": f"고정손절 (손실: {profit_rate*100:.2f}%)"}

        # ========================================
        # 2. 수급 반전 (외국인 순매도 전환)
        # ========================================
        if frgn_ratio <= cls.SUPPLY_REVERSAL_THRESHOLD:
            logger.warning(f"[{symbol}] 수급 반전: 외국인={frgn_ratio:.2f}%")
            return {
                "action": "SELL",
                "reason": f"수급반전 (외국인={frgn_ratio:.1f}%)"
            }

        # ========================================
        # 3. EMA 이탈 (2회 연속 확인)
        # ========================================
        ema_key = f"ema_breach:{position_id}"
        below_ema = curr_price < realtime_ema20

        if below_ema:
            prev_ema_str = await redis_client.get(ema_key)

            if prev_ema_str:
                prev_ema = json.loads(prev_ema_str)
                breach_count = prev_ema.get('breach_count', 0) + 1

                if breach_count >= cls.EMA_BREACH_REQUIRED:
                    logger.warning(
                        f"[{symbol}] EMA 이탈 {breach_count}회: "
                        f"현재가={curr_price:,.0f}, EMA={realtime_ema20:,.0f}"
                    )
                    return {
                        "action": "SELL",
                        "reason": f"EMA이탈 (현재가={curr_price:,.0f} < EMA={realtime_ema20:,.0f})"
                    }
                else:
                    # 카운트 증가
                    await redis_client.setex(
                        ema_key,
                        600,
                        json.dumps({
                            'breach_count': breach_count,
                            'price': curr_price,
                            'ema': realtime_ema20,
                            'time': datetime.now().isoformat()
                        })
                    )
                    logger.info(f"[{symbol}] EMA 이탈 {breach_count}/{cls.EMA_BREACH_REQUIRED}회")
                    return {"action": "HOLD", "reason": f"EMA 이탈 대기 ({breach_count}/2)"}
            else:
                # 첫 이탈 기록
                await redis_client.setex(
                    ema_key,
                    600,
                    json.dumps({
                        'breach_count': 1,
                        'price': curr_price,
                        'ema': realtime_ema20,
                        'time': datetime.now().isoformat()
                    })
                )
                logger.info(f"[{symbol}] EMA 첫 이탈 기록")
                return {"action": "HOLD", "reason": "EMA 이탈 대기 (1/2)"}
        else:
            # EMA 위로 복귀 - 카운트 리셋
            await redis_client.delete(ema_key)

        # ========================================
        # 4. 수급 약화 (외국인 1% 미만)
        # ========================================
        if frgn_ratio < cls.SUPPLY_WEAK_THRESHOLD:
            logger.warning(f"[{symbol}] 수급 약화: 외국인={frgn_ratio:.2f}%")
            return {
                "action": "SELL",
                "reason": f"수급약화 (외국인={frgn_ratio:.1f}%)"
            }

        # ========================================
        # 5. 추세 악화 (EMA 아래에서 가격 하락 + 이탈폭 증가)
        # ========================================
        if below_ema:
            current_gap = realtime_ema20 - curr_price
            trend_key = f"trend:{position_id}"
            prev_trend_str = await redis_client.get(trend_key)

            if prev_trend_str:
                prev_trend = json.loads(prev_trend_str)
                prev_price = prev_trend['price']
                prev_gap = prev_trend['gap']

                price_declined = curr_price < prev_price
                gap_increased = current_gap > prev_gap

                if price_declined and gap_increased:
                    logger.warning(f"[{symbol}] 추세 악화")
                    return {"action": "SELL", "reason": "추세악화"}
                else:
                    # 상태 업데이트
                    await redis_client.setex(
                        trend_key,
                        600,
                        json.dumps({
                            'gap': current_gap,
                            'price': curr_price,
                            'time': datetime.now().isoformat()
                        })
                    )
            else:
                # 첫 기록
                await redis_client.setex(
                    trend_key,
                    600,
                    json.dumps({
                        'gap': current_gap,
                        'price': curr_price,
                        'time': datetime.now().isoformat()
                    })
                )
        else:
            # EMA 위면 추세 키 삭제
            await redis_client.delete(f"trend:{position_id}")

        # ========================================
        # 정상 보유 (조건 유지)
        # ========================================
        logger.info(
            f"[{symbol}] HOLD: 수익률={profit_rate*100:.2f}%, "
            f"외국인={frgn_ratio:.2f}%"
        )
        return {"action": "HOLD", "reason": "정상"}

    @classmethod
    async def check_second_buy_signal(
        cls,
        db,
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
        2차 매수 신호 체크 (TRADE_HISTORY + STOCK_DAY_HISTORY + 실시간 API)

        1차 매수 이후 추가 매수 기회 포착

        Conditions (all must pass):
        1. 가격 범위: 1차 매수가 대비 +1% ~ +4%
        2. EMA 위치: 현재가 > EMA20 (추세 확인)
        3. 수급 강도: 1차 매수 시점 이후 외국인 누적 ≥ 1.2% (거래량 강도 포함)
        4. 손절 안전거리: 현재가 >= 손절가 × 1.04 (4% 안전 마진)
        5. 시간 간격: 같은 날이면 10분 이상 경과

        Args:
            db: 데이터베이스 세션
            redis_client: Redis 클라이언트   
            swing_id: 스윙 ID
            symbol: 종목 코드
            df: 주가 데이터
            entry_price: 1차 매수가 (평균 단가)
            hold_qty: 보유 수량
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량 (당일 실시간)
            acml_vol: 누적 거래량 (당일 실시간)
            prdy_vrss_vol_rate: 전일 대비 거래량 비율 (%)

        Returns:
            2차 매수 신호 정보 또는 None
        """
        from sqlalchemy import select, func
        from app.common.database import TradeHistoryModel, StockHistoryModel
        from datetime import datetime

        curr_price = float(current_price)
        entry = float(entry_price)

        # ========================================
        # 1. 1차 매수 데이터 조회 (TRADE_HISTORY)
        # ========================================
        try:
            # 가장 최근 매수(B) 내역 조회
            result = await db.execute(
                select(TradeHistoryModel)
                .where(
                    TradeHistoryModel.SWING_ID == swing_id,
                    TradeHistoryModel.TRADE_TYPE == 'B'
                )
                .order_by(TradeHistoryModel.TRADE_DATE.desc())
                .limit(1)
            )
            first_buy = result.scalar_one_or_none()

            if not first_buy:
                logger.warning(f"[{symbol}] 1차 매수 내역 없음 (SWING_ID={swing_id})")
                return None

            first_buy_dt = first_buy.TRADE_DATE
            first_buy_date_str = first_buy_dt.strftime('%Y%m%d')
            today_str = datetime.now().strftime('%Y%m%d')

            logger.info(
                f"[{symbol}] 1차 매수 시점: {first_buy_dt.strftime('%Y-%m-%d %H:%M:%S')}, "
                f"매수가: {first_buy.TRADE_PRICE:,.0f}"
            )

        except Exception as e:
            logger.error(f"[{symbol}] TRADE_HISTORY 조회 실패: {e}", exc_info=True)
            return None

        # ========================================
        # 2. 가격 조건 체크 (1차 매수가 기준)
        # ========================================
        price_gain = (curr_price - entry) / entry

        if price_gain < cls.SECOND_BUY_PRICE_GAIN_MIN:
            logger.debug(
                f"[{symbol}] 2차 매수 가격 미달: {price_gain*100:.2f}% "
                f"(최소 {cls.SECOND_BUY_PRICE_GAIN_MIN*100}% 필요)"
            )
            return None

        if price_gain > cls.SECOND_BUY_PRICE_GAIN_MAX:
            logger.debug(
                f"[{symbol}] 2차 매수 가격 초과: {price_gain*100:.2f}% "
                f"(최대 {cls.SECOND_BUY_PRICE_GAIN_MAX*100}%)"
            )
            return None

        # ========================================
        # 3. EMA 위치 체크 (추세 확인)
        # ========================================
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20 is None:
            logger.warning(f"[{symbol}] EMA 계산 불가")
            return None

        if curr_price <= realtime_ema20:
            logger.debug(f"[{symbol}] EMA 아래: 현재가={curr_price:,.0f}, EMA={realtime_ema20:,.0f}")
            return None

        # ========================================
        # 4. 수급 강도 체크 (1차 매수 이후 누적)
        # ========================================
        try:
            # 6-1. STOCK_DAY_HISTORY에서 1차 매수일 ~ 어제까지 누적
            yesterday_str = (datetime.now() - pd.Timedelta(days=1)).strftime('%Y%m%d')

            # 1차 매수가 어제 이전인 경우에만 DB 조회
            if first_buy_date_str < today_str:
                result = await db.execute(
                    select(
                        func.sum(StockHistoryModel.FRGN_NTBY_QTY).label('total_frgn'),
                        func.sum(StockHistoryModel.ACML_VOL).label('total_vol')
                    )
                    .where(
                        StockHistoryModel.ST_CODE == symbol,
                        StockHistoryModel.STCK_BSOP_DATE >= first_buy_date_str,
                        StockHistoryModel.STCK_BSOP_DATE <= yesterday_str
                    )
                )
                past_data = result.one()
                past_frgn = past_data.total_frgn or 0
                past_vol = past_data.total_vol or 0

                logger.debug(
                    f"[{symbol}] 과거 수급 ({first_buy_date_str}~{yesterday_str}): "
                    f"외국인={past_frgn:,}, 거래량={past_vol:,}"
                )
            else:
                # 1차 매수가 오늘인 경우 (과거 데이터 없음)
                past_frgn = 0
                past_vol = 0
                logger.debug(f"[{symbol}] 1차 매수가 당일, 과거 데이터 없음")

            # 6-2. 당일 실시간 데이터 추가
            total_frgn = past_frgn + frgn_ntby_qty
            total_vol = past_vol + acml_vol

            if total_vol == 0:
                logger.warning(f"[{symbol}] 누적 거래량 0")
                return None

            cumulative_frgn_ratio = (total_frgn / total_vol) * 100

            logger.info(
                f"[{symbol}] 누적 외국인 수급 ({first_buy_date_str}~현재): "
                f"{cumulative_frgn_ratio:.2f}% (외국인={total_frgn:,}, 거래량={total_vol:,})"
            )

            if cumulative_frgn_ratio < cls.SECOND_BUY_FRGN_THRESHOLD:
                logger.debug(
                    f"[{symbol}] 수급 부족: {cumulative_frgn_ratio:.2f}% "
                    f"(최소 {cls.SECOND_BUY_FRGN_THRESHOLD}% 필요)"
                )
                return None

        except Exception as e:
            logger.error(f"[{symbol}] 수급 데이터 조회 실패: {e}", exc_info=True)
            return None

        # ========================================
        # 5. 손절 안전거리 체크 (현재가 >= 손절가 × 1.04)
        # ========================================
        stop_loss_price = entry * (1 + cls.STOP_LOSS_FIXED)  # -3% 손절가
        safety_threshold = stop_loss_price * (1 + cls.SECOND_BUY_SAFETY_MARGIN)  # 손절가 위 4%

        if curr_price < safety_threshold:
            logger.debug(
                f"[{symbol}] 손절 안전거리 부족: 현재가={curr_price:,.0f}, "
                f"안전 기준={safety_threshold:,.0f}"
            )
            return None

        # ========================================
        # 6. 시간 간격 체크 (같은 날만)
        # ========================================
        if first_buy_date_str == today_str:
            elapsed_seconds = (datetime.now() - first_buy_dt).total_seconds()

            if elapsed_seconds < cls.SECOND_BUY_TIME_MIN:
                logger.debug(
                    f"[{symbol}] 시간 간격 부족: {elapsed_seconds/60:.1f}분 "
                    f"(최소 {cls.SECOND_BUY_TIME_MIN/60}분 필요)"
                )
                return None

        # ========================================
        # ✅ 모든 조건 충족
        # ========================================
        logger.info(
            f"[{symbol}] 2차 매수 신호 발생: "
            f"가격상승={price_gain*100:.2f}%, "
            f"외국인={cumulative_frgn_ratio:.2f}%"
        )

        return {
            'action': 'BUY',
            'price': curr_price,
            'ema20': realtime_ema20,
            'price_gain': price_gain,
            'frgn_ratio': cumulative_frgn_ratio,
            'first_buy_date': first_buy_dt.strftime('%Y-%m-%d %H:%M:%S')
        }

    @classmethod
    def analyze(
        cls,
        df: pd.DataFrame,
        current_price: Decimal,
        frgn_ntby_qty: int,
        pgtr_ntby_qty: int,
        acml_vol: int,
        prdy_vrss_vol_rate: float,
        prdy_ctrt: float,
        entry_price: Optional[Decimal] = None,
        current_signal: int = 0
    ) -> Dict:
        """
        전략 분석 (Redis 없이 동기 분석만, 실제 신호는 async 메서드 사용)

        기존 코드와의 호환성을 위한 wrapper 메서드
        실제 Redis 기반 신호 생성은 check_entry_signal/check_exit_signal 사용

        Returns:
            간단한 분석 결과
        """
        curr_price = float(current_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20 is None:
            return {
                "signal": "hold",
                "strength": None,
                "score": 0,
                "conditions": {},
                "indicators": {},
                "reason": "EMA 계산 불가"
            }

        # 기본 조건 체크
        price_condition = curr_price > realtime_ema20
        gap_ratio = (curr_price - realtime_ema20) / realtime_ema20
        gap_condition = gap_ratio <= cls.MAX_GAP_RATIO

        frgn_ratio = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        pgm_ratio = (pgtr_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        supply_condition = frgn_ratio >= cls.FRGN_STRONG_THRESHOLD

        volume_condition = prdy_vrss_vol_rate >= (cls.VOLUME_RATIO_THRESHOLD * 100)
        surge_filtered = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)

        score = sum([price_condition, gap_condition, supply_condition, volume_condition, surge_filtered])

        conditions = {
            "price_above_ema": price_condition,
            "gap_ok": gap_condition,
            "supply_strong": supply_condition,
            "volume_sufficient": volume_condition,
            "surge_filtered": surge_filtered
        }

        indicators = {
            "ema_20": realtime_ema20,
            "gap_ratio": gap_ratio,
            "frgn_ratio": frgn_ratio,
            "pgm_ratio": pgm_ratio,
        }

        # 포지션이 있는 경우 손익 계산
        if entry_price:
            profit_rate = (curr_price - float(entry_price)) / float(entry_price)
            indicators["profit_rate"] = profit_rate

            # 간단한 매도 조건 체크
            # 1. 고정 손절
            if profit_rate <= cls.STOP_LOSS_FIXED:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"손절: 고정 -3% (현재 {profit_rate*100:.2f}%)"
                }

            # 2. 수급 반전
            if frgn_ratio <= cls.SUPPLY_REVERSAL_THRESHOLD:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"수급 반전 (외국인={frgn_ratio:.1f}%)"
                }

            # 3. EMA 이탈 (Redis 없이는 연속 확인 불가)
            if curr_price < realtime_ema20:
                return {
                    "signal": "sell",
                    "strength": "weak",  # Redis 확인 필요
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"EMA 이탈 가능성 (Redis 확인 필요)"
                }

            # 4. 수급 약화
            if frgn_ratio < cls.SUPPLY_WEAK_THRESHOLD:
                return {
                    "signal": "sell",
                    "strength": "medium",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"수급 약화 (외국인={frgn_ratio:.1f}%)"
                }

        # 진입 신호 (단, Redis 없이는 연속 확인 불가)
        if current_signal == 0 and score == 5:
            return {
                "signal": "buy",
                "strength": "weak",  # Redis 확인 필요
                "score": score,
                "conditions": conditions,
                "indicators": indicators,
                "reason": "조건 충족 (Redis 연속 확인 필요)"
            }

        return {
            "signal": "hold",
            "strength": None,
            "score": score,
            "conditions": conditions,
            "indicators": indicators,
            "reason": f"조건 미충족 (점수: {score}/5)"
        }

    # ========================================
    # 새로운 매도 전략 메서드들
    # ========================================

    @classmethod
    def check_stop_loss_immediate(
        cls,
        current_price: Decimal,
        entry_price: Decimal
    ) -> bool:
        """
        절대 손절 체크 (장중 실시간, -3%)

        Args:
            current_price: 현재가
            entry_price: 진입가 (평균 단가)

        Returns:
            손절 여부
        """
        profit_rate = (float(current_price) - float(entry_price)) / float(entry_price)
        return profit_rate <= cls.STOP_LOSS_FIXED

    @classmethod
    async def check_foreign_exit_signal(
        cls,
        db,
        symbol: str
    ) -> bool:
        """
        외국인 이탈 신호 체크 (최근 2일 합산 순매도)

        Args:
            db: 데이터베이스 세션
            symbol: 종목 코드

        Returns:
            외국인 이탈 신호 여부
        """
        from sqlalchemy import select, func
        from app.common.database import StockHistoryModel
        from datetime import datetime, timedelta

        try:
            # 최근 2일 데이터 조회
            today = datetime.now()
            two_days_ago = today - timedelta(days=2)
            two_days_ago_str = two_days_ago.strftime('%Y%m%d')

            result = await db.execute(
                select(func.sum(StockHistoryModel.FRGN_NTBY_QTY).label('total_frgn'))
                .where(
                    StockHistoryModel.ST_CODE == symbol,
                    StockHistoryModel.STCK_BSOP_DATE >= two_days_ago_str
                )
            )
            data = result.one()
            total_frgn = data.total_frgn or 0

            # 2일 합산이 순매도면 이탈 신호
            is_exit = total_frgn < 0

            logger.debug(
                f"[{symbol}] 외국인 이탈 체크 (최근 2일): "
                f"합산={total_frgn:,}, 이탈={is_exit}"
            )

            return is_exit

        except Exception as e:
            logger.error(f"[{symbol}] 외국인 이탈 신호 체크 실패: {e}", exc_info=True)
            return False

    @classmethod
    async def check_trend_weakness_signal(
        cls,
        redis_client,
        position_id: int,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal
    ) -> bool:
        """
        추세 약화 신호 체크

        조건: EMA 아래 + 가격 하락 + 이탈폭 증가

        Args:
            redis_client: Redis 클라이언트
            position_id: 포지션 ID
            symbol: 종목 코드
            df: 주가 데이터
            current_price: 현재가

        Returns:
            추세 약화 신호 여부
        """
        curr_price = float(current_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20 is None:
            return False

        below_ema = curr_price < realtime_ema20

        if not below_ema:
            return False

        # Redis에서 이전 추세 데이터 조회
        trend_key = f"trend:{position_id}"
        prev_trend_str = await redis_client.get(trend_key)

        if not prev_trend_str:
            # 첫 기록
            current_gap = realtime_ema20 - curr_price
            await redis_client.setex(
                trend_key,
                600,
                json.dumps({
                    'gap': current_gap,
                    'price': curr_price,
                    'time': datetime.now().isoformat()
                })
            )
            return False

        # 이전 데이터와 비교
        prev_trend = json.loads(prev_trend_str)
        prev_price = prev_trend['price']
        prev_gap = prev_trend['gap']
        current_gap = realtime_ema20 - curr_price

        price_declined = curr_price < prev_price
        gap_increased = current_gap > prev_gap

        # 추세 약화 = 가격 하락 + 이탈폭 증가
        is_weak = price_declined and gap_increased

        # 상태 업데이트
        await redis_client.setex(
            trend_key,
            600,
            json.dumps({
                'gap': current_gap,
                'price': curr_price,
                'time': datetime.now().isoformat()
            })
        )

        logger.debug(
            f"[{symbol}] 추세 약화 체크: "
            f"가격하락={price_declined}, 이탈폭증가={gap_increased}, 약화={is_weak}"
        )

        return is_weak

    @classmethod
    async def check_first_sell_signal_eod(
        cls,
        db,
        redis_client,
        position_id: int,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal
    ) -> Dict:
        """
        1차 매도 신호 체크 (종가 기준)

        조건: (EMA 이탈, 외국인 이탈, 추세 약화) 중 2개 충족

        Args:
            db: 데이터베이스 세션
            redis_client: Redis 클라이언트
            position_id: 포지션 ID
            symbol: 종목 코드
            df: 주가 데이터
            current_price: 현재가 (종가)

        Returns:
            매도 신호 정보
        """
        # 3가지 신호 체크
        signals = {}

        # 1. EMA 이탈 (2회 연속)
        curr_price = float(current_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20:
            below_ema = curr_price < realtime_ema20
            ema_key = f"ema_breach:{position_id}"

            if below_ema:
                prev_ema_str = await redis_client.get(ema_key)
                if prev_ema_str:
                    prev_ema = json.loads(prev_ema_str)
                    breach_count = prev_ema.get('breach_count', 0) + 1

                    signals['ema_breach'] = breach_count >= cls.EMA_BREACH_REQUIRED

                    # 카운트 업데이트
                    await redis_client.setex(
                        ema_key,
                        86400,  # 24시간
                        json.dumps({
                            'breach_count': breach_count,
                            'price': curr_price,
                            'ema': realtime_ema20,
                            'time': datetime.now().isoformat()
                        })
                    )
                else:
                    # 첫 이탈
                    signals['ema_breach'] = False
                    await redis_client.setex(
                        ema_key,
                        86400,
                        json.dumps({
                            'breach_count': 1,
                            'price': curr_price,
                            'ema': realtime_ema20,
                            'time': datetime.now().isoformat()
                        })
                    )
            else:
                signals['ema_breach'] = False
                await redis_client.delete(ema_key)
        else:
            signals['ema_breach'] = False

        # 2. 외국인 이탈
        signals['foreign_exit'] = await cls.check_foreign_exit_signal(db, symbol)

        # 3. 추세 약화
        signals['trend_weakness'] = await cls.check_trend_weakness_signal(
            redis_client, position_id, symbol, df, current_price
        )

        # 충족 개수 계산
        satisfied_count = sum(signals.values())

        logger.info(
            f"[{symbol}] 1차 매도 신호 체크 (종가): "
            f"EMA이탈={signals['ema_breach']}, "
            f"외국인이탈={signals['foreign_exit']}, "
            f"추세약화={signals['trend_weakness']}, "
            f"충족={satisfied_count}/3"
        )

        # 2개 이상 충족 시 매도 신호
        if satisfied_count >= 2:
            reasons = [k for k, v in signals.items() if v]
            return {
                "action": "SELL",
                "reason": f"1차 매도 신호 ({', '.join(reasons)})",
                "signals": signals,
                "satisfied_count": satisfied_count
            }

        return {
            "action": "HOLD",
            "reason": f"1차 매도 조건 미충족 ({satisfied_count}/2)",
            "signals": signals,
            "satisfied_count": satisfied_count
        }

    @classmethod
    async def check_second_sell_signal_eod(
        cls,
        db,
        redis_client,
        position_id: int,
        symbol: str,
        df: pd.DataFrame,
        current_price: Decimal
    ) -> Dict:
        """
        2차 매도 신호 체크 (종가 기준)

        조건: (EMA 이탈 유지, 외국인 이탈 유지/확대, 추세 약화 확정) 모두 충족

        Args:
            db: 데이터베이스 세션
            redis_client: Redis 클라이언트
            position_id: 포지션 ID
            symbol: 종목 코드
            df: 주가 데이터
            current_price: 현재가 (종가)

        Returns:
            매도 신호 정보
        """
        # 3가지 신호 체크 (1차 매도와 동일한 로직)
        signals = {}

        # 1. EMA 이탈 유지
        curr_price = float(current_price)
        realtime_ema20 = cls.get_realtime_ema20(df, curr_price)

        if realtime_ema20:
            below_ema = curr_price < realtime_ema20
            ema_key = f"ema_breach:{position_id}"

            if below_ema:
                prev_ema_str = await redis_client.get(ema_key)
                if prev_ema_str:
                    prev_ema = json.loads(prev_ema_str)
                    breach_count = prev_ema.get('breach_count', 0) + 1

                    signals['ema_breach'] = breach_count >= cls.EMA_BREACH_REQUIRED

                    # 카운트 업데이트
                    await redis_client.setex(
                        ema_key,
                        86400,
                        json.dumps({
                            'breach_count': breach_count,
                            'price': curr_price,
                            'ema': realtime_ema20,
                            'time': datetime.now().isoformat()
                        })
                    )
                else:
                    signals['ema_breach'] = False
            else:
                signals['ema_breach'] = False
                await redis_client.delete(ema_key)
        else:
            signals['ema_breach'] = False

        # 2. 외국인 이탈 유지/확대
        signals['foreign_exit'] = await cls.check_foreign_exit_signal(db, symbol)

        # 3. 추세 약화 확정
        signals['trend_weakness'] = await cls.check_trend_weakness_signal(
            redis_client, position_id, symbol, df, current_price
        )

        # 모두 충족 확인
        all_satisfied = all(signals.values())

        logger.info(
            f"[{symbol}] 2차 매도 신호 체크 (종가): "
            f"EMA이탈={signals['ema_breach']}, "
            f"외국인이탈={signals['foreign_exit']}, "
            f"추세약화={signals['trend_weakness']}, "
            f"모두충족={all_satisfied}"
        )

        # 모두 충족 시 매도 신호
        if all_satisfied:
            return {
                "action": "SELL",
                "reason": "2차 매도 신호 (모든 조건 충족)",
                "signals": signals,
                "all_satisfied": True
            }

        return {
            "action": "HOLD",
            "reason": "2차 매도 조건 미충족",
            "signals": signals,
            "all_satisfied": False
        }
