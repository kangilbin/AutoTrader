"""
단일 20EMA 매매 전략 (Single EMA Strategy)

Entry Conditions (all 5 must be met + 2회 연속 확인):
1. 가격 위치: 현재가 > 실시간 EMA20, 괴리율 <= 2%
2. 수급 강도: (외국인 >= 3% OR 프로그램 >= 3%) AND (합 >= 4.5%)
3. 수급 유지: 이전 대비 20% 이상 감소하지 않음
4. 거래량: 전일 대비 120% 이상
5. 급등 필터: 당일 상승률 <= 7%
6. 2회 연속 확인 (Redis 상태 관리)

Risk Management:
- Stop Loss 1: 고정 -3% 손절
- Stop Loss 2: EMA -3% 이탈
- Stop Loss 3: 추세 악화 (EMA 아래에서 악화 시)
- Profit Taking 1: +8% 목표 익절
- Profit Taking 2: +3% 이상에서 수급 이탈
"""
import pandas as pd
import talib as ta
import numpy as np
import json
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SingleEMAStrategy:
    """단일 20EMA 매매 전략"""

    # 전략 파라미터
    EMA_PERIOD = 20

    # 진입 조건
    MAX_GAP_RATIO = 0.02  # 괴리율 2% 이내
    FRGN_STRONG_THRESHOLD = 3.0  # 외국인 3% 이상
    PGM_STRONG_THRESHOLD = 3.0  # 프로그램 3% 이상
    TOTAL_THRESHOLD = 4.5  # 합산 4.5% 이상
    MAINTAIN_RATIO = 0.8  # 수급 유지 기준 (80%)
    VOLUME_RATIO_THRESHOLD = 1.2  # 거래량 120% 이상
    MAX_SURGE_RATIO = 0.07  # 급등 필터 7%
    CONSECUTIVE_REQUIRED = 2  # 2회 연속 확인

    # 손절/익절
    STOP_LOSS_FIXED = -0.03  # -3%
    STOP_LOSS_EMA = -0.03  # EMA -3%
    PROFIT_TARGET = 0.08  # +8%
    PROFIT_MIN_FOR_EXIT = 0.03  # +3% (수급 이탈 시 최소 수익)
    SUPPLY_EXIT_THRESHOLD = 1.0  # 수급 1% 미만

    @classmethod
    def get_realtime_ema20(cls, df: pd.DataFrame, current_price: float) -> Optional[float]:
        """
        실시간 EMA20 계산

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
        현재가 = float(current_price)
        실시간_ema20 = cls.get_realtime_ema20(df, 현재가)

        if 실시간_ema20 is None:
            logger.warning(f"[{symbol}] EMA 계산 불가")
            return None

        # === 조건 A: 가격 위치 ===
        가격_조건 = 현재가 > 실시간_ema20
        괴리율 = (현재가 - 실시간_ema20) / 실시간_ema20
        괴리_조건 = 괴리율 <= cls.MAX_GAP_RATIO

        # === 조건 B: 수급 강도 ===
        외국인_비율 = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        프로그램_비율 = (pgtr_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        수급_조건 = (
            (외국인_비율 >= cls.FRGN_STRONG_THRESHOLD or 프로그램_비율 >= cls.PGM_STRONG_THRESHOLD) and
            (외국인_비율 + 프로그램_비율 >= cls.TOTAL_THRESHOLD)
        )

        # === 조건 C: 수급 유지 ===
        수급_유지 = True
        prev_state_key = f"entry:{symbol}"
        prev_state_str = await redis_client.get(prev_state_key)

        if prev_state_str:
            prev_state = json.loads(prev_state_str)
            이전_외국인_비율 = prev_state.get('curr_frgn_ratio', 0)
            이전_프로그램_비율 = prev_state.get('curr_pgm_ratio', 0)

            외국인_유지 = 외국인_비율 >= 이전_외국인_비율 * cls.MAINTAIN_RATIO
            프로그램_유지 = 프로그램_비율 >= 이전_프로그램_비율 * cls.MAINTAIN_RATIO

            수급_유지 = 외국인_유지 or 프로그램_유지

        # === 조건 D: 거래량 ===
        거래량_조건 = prdy_vrss_vol_rate >= (cls.VOLUME_RATIO_THRESHOLD * 100)

        # === 조건 E: 급등 필터 ===
        급등_필터 = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)

        # === 전체 조건 ===
        현재_신호 = (
            가격_조건 and 괴리_조건 and
            수급_조건 and 수급_유지 and
            거래량_조건 and 급등_필터
        )

        # === 연속 카운트 ===
        consecutive = 0
        if 현재_신호:
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
            'curr_signal': 현재_신호,
            'consecutive_count': consecutive,
            'curr_price': 현재가,
            'curr_ema20': 실시간_ema20,
            'curr_frgn_ratio': 외국인_비율,
            'curr_pgm_ratio': 프로그램_비율,
            'last_update': datetime.now().isoformat()
        }
        await redis_client.setex(prev_state_key, 900, json.dumps(new_state))

        # === 최종 판정 ===
        if consecutive >= cls.CONSECUTIVE_REQUIRED:
            logger.info(
                f"[{symbol}] 매수 신호: consecutive={consecutive}, "
                f"가격={현재가:,.0f}, EMA={실시간_ema20:,.0f}, "
                f"외국인={외국인_비율:.2f}%, 프로그램={프로그램_비율:.2f}%"
            )
            return {
                'action': 'BUY',
                'price': 현재가,
                'ema20': 실시간_ema20,
                'frgn_ratio': 외국인_비율,
                'pgm_ratio': 프로그램_비율,
                'gap_ratio': 괴리율,
                'consecutive': consecutive
            }

        # 조건 충족 중이지만 아직 2회 미달
        if 현재_신호 and consecutive == 1:
            logger.info(
                f"[{symbol}] 신호 대기 중: consecutive=1/2, "
                f"가격={현재가:,.0f}, EMA={실시간_ema20:,.0f}"
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
        매도 신호 체크

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
        현재가 = float(current_price)
        매수가 = float(entry_price)
        실시간_ema20 = cls.get_realtime_ema20(df, 현재가)

        if 실시간_ema20 is None:
            logger.warning(f"[{symbol}] EMA 계산 불가, HOLD 유지")
            return {"action": "HOLD", "reason": "EMA 계산 불가"}

        수익률 = (현재가 - 매수가) / 매수가
        현재_이탈폭 = 실시간_ema20 - 현재가

        # === 손절 1: 하드 스톱 ===

        # 고정 손절 -3%
        if 수익률 <= cls.STOP_LOSS_FIXED:
            logger.warning(f"[{symbol}] 고정 손절 -3%: {수익률*100:.2f}%")
            return {"action": "SELL", "reason": f"고정손절-3% (손실: {수익률*100:.2f}%)"}

        # EMA 대폭 이탈 (EMA -3%)
        ema_deviation = (현재가 - 실시간_ema20) / 실시간_ema20
        if ema_deviation <= cls.STOP_LOSS_EMA:
            logger.warning(f"[{symbol}] EMA 대폭 이탈: {ema_deviation*100:.2f}%")
            return {"action": "SELL", "reason": f"EMA대폭이탈 (EMA대비: {ema_deviation*100:.2f}%)"}

        # === 손절 2: 추세 기반 ===

        # EMA 아래지만 -3%까지는 아닌 구간
        if 0 < 현재_이탈폭 < 실시간_ema20 * 0.03:
            stop_key = f"stop:{position_id}"
            prev_stop_str = await redis_client.get(stop_key)

            if prev_stop_str:
                prev_stop = json.loads(prev_stop_str)
                이전_가격 = prev_stop['price']
                이전_이탈폭 = prev_stop['gap']

                # 추세 악화 확인
                가격_하락 = 현재가 < 이전_가격
                이탈_증가 = 현재_이탈폭 > 이전_이탈폭

                if 가격_하락 and 이탈_증가:
                    logger.warning(f"[{symbol}] 추세 악화 손절")
                    return {"action": "SELL", "reason": "추세악화"}
                else:
                    # 회복/횡보 중 - 상태 업데이트
                    await redis_client.setex(
                        stop_key,
                        600,  # TTL 10분
                        json.dumps({
                            'gap': 현재_이탈폭,
                            'price': 현재가,
                            'time': datetime.now().isoformat()
                        })
                    )
                    return {"action": "HOLD", "reason": "추세 회복 대기"}
            else:
                # 첫 이탈 기록
                await redis_client.setex(
                    stop_key,
                    600,
                    json.dumps({
                        'gap': 현재_이탈폭,
                        'price': 현재가,
                        'time': datetime.now().isoformat()
                    })
                )
                return {"action": "HOLD", "reason": "첫 이탈 기록"}

        # === 익절 ===

        # 목표 익절 +8%
        if 수익률 >= cls.PROFIT_TARGET:
            logger.info(f"[{symbol}] 목표 익절 +8%: {수익률*100:.2f}%")
            return {"action": "SELL", "reason": f"목표익절+8% (수익: {수익률*100:.2f}%)"}

        # 수급 이탈 익절 (+3% 이상에서)
        외국인_비율 = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        프로그램_비율 = (pgtr_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        if (수익률 >= cls.PROFIT_MIN_FOR_EXIT and
            외국인_비율 < cls.SUPPLY_EXIT_THRESHOLD and
            프로그램_비율 < cls.SUPPLY_EXIT_THRESHOLD):
            logger.info(f"[{symbol}] 수급 이탈 익절: {수익률*100:.2f}%")
            return {"action": "SELL", "reason": f"수급이탈 (수익: {수익률*100:.2f}%)"}

        # === 정상 상태 - stop 키 삭제 ===
        stop_key = f"stop:{position_id}"
        await redis_client.delete(stop_key)

        return {"action": "HOLD", "reason": "정상"}

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
        현재가 = float(current_price)
        실시간_ema20 = cls.get_realtime_ema20(df, 현재가)

        if 실시간_ema20 is None:
            return {
                "signal": "hold",
                "strength": None,
                "score": 0,
                "conditions": {},
                "indicators": {},
                "reason": "EMA 계산 불가"
            }

        # 기본 조건 체크
        가격_조건 = 현재가 > 실시간_ema20
        괴리율 = (현재가 - 실시간_ema20) / 실시간_ema20
        괴리_조건 = 괴리율 <= cls.MAX_GAP_RATIO

        외국인_비율 = (frgn_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0
        프로그램_비율 = (pgtr_ntby_qty / acml_vol * 100) if acml_vol > 0 else 0

        수급_조건 = (
            (외국인_비율 >= cls.FRGN_STRONG_THRESHOLD or 프로그램_비율 >= cls.PGM_STRONG_THRESHOLD) and
            (외국인_비율 + 프로그램_비율 >= cls.TOTAL_THRESHOLD)
        )

        거래량_조건 = prdy_vrss_vol_rate >= (cls.VOLUME_RATIO_THRESHOLD * 100)
        급등_필터 = prdy_ctrt <= (cls.MAX_SURGE_RATIO * 100)

        score = sum([가격_조건, 괴리_조건, 수급_조건, 거래량_조건, 급등_필터])

        conditions = {
            "price_above_ema": 가격_조건,
            "gap_ok": 괴리_조건,
            "supply_strong": 수급_조건,
            "volume_sufficient": 거래량_조건,
            "surge_filtered": 급등_필터
        }

        indicators = {
            "ema_20": 실시간_ema20,
            "gap_ratio": 괴리율,
            "frgn_ratio": 외국인_비율,
            "pgm_ratio": 프로그램_비율,
        }

        # 포지션이 있는 경우 손익 계산
        if entry_price:
            수익률 = (현재가 - float(entry_price)) / float(entry_price)
            indicators["profit_rate"] = 수익률

            # 간단한 매도 조건 체크
            if 수익률 <= cls.STOP_LOSS_FIXED:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"손절: 고정 -3% (현재 {수익률*100:.2f}%)"
                }

            ema_deviation = (현재가 - 실시간_ema20) / 실시간_ema20
            if ema_deviation <= cls.STOP_LOSS_EMA:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"손절: EMA -3% 이탈 (현재 {ema_deviation*100:.2f}%)"
                }

            if 수익률 >= cls.PROFIT_TARGET:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"익절: +8% 목표 (현재 {수익률*100:.2f}%)"
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
