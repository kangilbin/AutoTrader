"""
오더블럭 실전 전략 (Order Block Strategy)

상승 구조 돌파(BOS)를 만든 직전 음봉(오더블럭)까지 가격이 처음 되돌아올 때 사고,
오더블럭 하단(원격선) 붕괴를 손절로 삼는다.
규칙: docs/01-plan/features/order-block-strategy.plan.md, 설계: docs/02-design/... (5장)

- 오더블럭은 장 전 워밍업이 완성봉으로 탐지해 Redis `ob_zone:{st_code}`에 둔다 (SwingService)
- 손절가는 매수 시 확정해 SWING_TRADE.STOP_PRICE에 저장한다 (OB는 다음 날 캐시에서 소멸)
- 청산선·수량·목표가 계산은 BaseOrderBlockStrategy를 백테스트와 공유한다
"""
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from .base_order_block import BaseOrderBlockStrategy
from .base_trading_strategy import TradingStrategy
from .single_ema_strategy import SingleEMAStrategy

logger = logging.getLogger(__name__)


class OrderBlockStrategy(TradingStrategy, BaseOrderBlockStrategy):
    """오더블럭 매매 전략"""

    # 사용자에게 보이는 이름 (AI 종목 분석 추천 전략 등) — 앱의 등록·설정 화면 표기와 같게 둔다
    name = "오더블럭"

    # ========================================
    # 실전 전용 파라미터 (단일 이평선과 같은 값)
    # ========================================
    CONSECUTIVE_REQUIRED = 2         # 연속 확인 횟수 (10분)
    ENTRY_STATE_TTL = 1800           # 연속성 상태 Redis TTL (초, 30분)
    TRADE_CYCLE_MINUTES = 5          # 매매 배치 주기 — 설명용 (scheduler.py 크론과 맞출 것)
    DAY_FLAG_TTL = 86400             # 당일 플래그(ob_broken / ob_used) TTL — 키에 전일 날짜가 들어가 날이 바뀌면 자연 무효

    @classmethod
    def describe(cls) -> dict:
        """AI 종목 분석에 전달하는 전략 설명 (수치는 아래 판정 로직과 같은 상수)"""
        return {
            "이름": cls.name,
            "요약": "상승 구조 돌파(BOS)를 만든 직전 음봉(오더블럭)까지 가격이 처음 되돌아올 때 사고, "
                    "오더블럭 하단 붕괴를 손절로 삼는다",
            "매수조건": [
                f"직전 스윙 고점을 종가로 돌파한 상승(변위 ATR×{cls.DISPLACEMENT_ATR:g} 이상, "
                f"거래량 20일 평균의 {cls.IMPULSE_VOL_RATIO:g}배 이상, {cls.TREND_EMA_PERIOD}일 EMA 위)의 직전 음봉을 오더블럭으로 본다",
                f"돌파 후 {cls.OB_MAX_AGE}거래일 안에 처음으로 오더블럭 상단까지 되돌아왔을 때만 (두 번째 터치부터는 무효)",
                f"현재가가 오더블럭 하단 위, 상단 + ATR×{cls.ENTRY_CHASE_ATR:g} 이내",
                f"위 조건이 {cls.TRADE_CYCLE_MINUTES}분 간격으로 {cls.CONSECUTIVE_REQUIRED}회 연속 충족 "
                f"(약 {cls.TRADE_CYCLE_MINUTES * cls.CONSECUTIVE_REQUIRED}분)",
            ],
            "매수차단": [
                "시가 또는 장중 현재가가 오더블럭 하단 아래 (그날 무효)",
                f"손절폭이 진입가의 {cls.MAX_STOP_LOSS_PCT:g}% 초과 (손절을 줄이지 않고 진입 포기)",
                f"오더블럭 폭이 ATR×{cls.MAX_ZONE_ATR:g} 초과",
            ],
            "매수수량": f"손절 시 배정금의 {cls.RISK_PER_TRADE * 100:g}%만 잃도록 (상한 {cls.MAX_ENTRY_PCT * 100:g}%), "
                        f"전량 매수 설정 시 {cls.FULL_ENTRY_PCT * 100:g}%. 진입은 사이클당 1회 (추가 매수 없음)",
            "청산": [
                f"손절가 = 오더블럭 하단 − ATR×{cls.STOP_ATR_BUFFER:g} (매수 시 확정, R = 진입가 − 손절가)",
                f"고점 +{cls.BREAKEVEN_R:g}R → 본전, +{cls.TRAIL_ACTIVATE_R:g}R → 고점 − ATR×{cls.TRAIL_ATR_MULT:g} 로 상향 (내려가지 않음)",
                "청산선 이탈 시 전량 매도",
                f"+{cls.PARTIAL_TP_R:g}R 도달 시 절반 부분익절 (1회)",
            ],
            "매도후": f"OBV z < {cls.COOLDOWN_OBV_EXIT:g} (수급 이탈) → OBV z > {cls.COOLDOWN_OBV_REENTRY:g} "
                      f"(재유입) 확인 후 매수 대기. 이미 매매한 오더블럭은 다시 쓰지 않는다",
            "잘맞는종목": "상승 추세 중 눌림과 재상승을 반복하는 종목, 거래량을 동반한 강한 상승 이력이 있는 종목",
            "안맞는종목": f"장기 하락 추세({cls.TREND_EMA_PERIOD}일 EMA 아래), 갭이 잦은 종목(존을 건너뛴다), "
                          "거래량이 적은 종목, 되돌림 없이 한 방향으로만 오르는 종목(진입 기회 없음)",
        }

    # ========================================
    # 지표
    # ========================================

    @classmethod
    async def get_cached_indicators(cls, redis_client, symbol: str) -> Optional[Dict]:
        """공용 지표 캐시 + 활성 오더블럭

        indicators:{st_code}는 전략과 무관한 공용 워밍업 산출물이라 검증 로직을 단일 이평선과 공유한다.
        OB가 없거나 읽기에 실패해도 None을 돌리지 않는다 — OB는 매수에만 필요하고,
        청산은 STOP_PRICE·ATR로 계속 동작해야 한다.
        """
        indicators = await SingleEMAStrategy.get_cached_indicators(redis_client, symbol)
        if not indicators:
            return None
        try:
            raw = await redis_client.get(f"ob_zone:{symbol}")
            indicators["ob_zone"] = json.loads(raw) if raw else None
        except Exception as e:
            logger.warning(f"[{symbol}] 오더블럭 캐시 조회 실패 → 이번 사이클 매수 판정 생략: {e}")
            indicators["ob_zone"] = None
        return indicators

    # ========================================
    # 매수
    # ========================================

    @classmethod
    async def _reset_entry_state(cls, redis_client, swing_id: int) -> None:
        state = {'curr_signal': False, 'consecutive_count': 0, 'last_update': datetime.now().isoformat()}
        await redis_client.setex(f"entry:{swing_id}", cls.ENTRY_STATE_TTL, json.dumps(state))

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
        """활성 오더블럭의 첫 리테스트 매수 신호"""
        ob = cached_indicators.get('ob_zone')
        if not ob:
            return None

        curr_price = float(current_price)
        proximal, distal = float(ob['proximal']), float(ob['distal'])
        atr = cached_indicators.get('atr', 0) or 0                       # 전일 완성봉 ATR
        today_low = cached_indicators.get('today_low') or curr_price
        today_open = cached_indicators.get('today_open') or 0
        day = cached_indicators.get('date')
        broken_key = f"ob_broken:{symbol}:{day}"

        # 장중 붕괴 — 오늘 한 번이라도 원격선 아래였으면 그날은 무효 (반등해도 첫 리테스트가 깨진 것)
        if curr_price < distal:
            await redis_client.setex(broken_key, cls.DAY_FLAG_TTL, "1")
            logger.info(f"[{symbol}] 오더블럭 붕괴 (현재가 {curr_price:,.2f} < 하단 {distal:,.2f}) → 오늘 매수 안 함")
            await cls._reset_entry_state(redis_client, swing_id)
            return None
        if await redis_client.exists(broken_key):
            await cls._reset_entry_state(redis_client, swing_id)
            return None
        # 갭 붕괴 — 시가가 원격선 아래
        if today_open and today_open < distal:
            await cls._reset_entry_state(redis_client, swing_id)
            return None
        # 같은 날 같은 OB 재진입 방지 (손절 → 쿨다운이 장중에 다 지나가는 경우)
        if await redis_client.get(f"ob_used:{swing_id}:{day}") == ob.get('ob_date'):
            return None

        stop = cls.stop_price(distal, atr)
        touched = today_low <= proximal
        in_zone = distal < curr_price <= proximal + cls.ENTRY_CHASE_ATR * atr
        current_signal = atr > 0 and touched and in_zone and cls.stop_pct_ok(curr_price, stop)

        # 연속성 체크 (단일 이평선과 같은 형식, swing_id별)
        state_key = f"entry:{swing_id}"
        consecutive = 0
        if current_signal:
            prev_str = await redis_client.get(state_key)
            prev = json.loads(prev_str) if prev_str else {}
            consecutive = prev.get('consecutive_count', 0) + 1 if prev.get('curr_signal') else 1
        state = {'curr_signal': current_signal, 'consecutive_count': consecutive,
                 'last_update': datetime.now().isoformat()}
        await redis_client.setex(state_key, cls.ENTRY_STATE_TTL, json.dumps(state))

        if consecutive >= cls.CONSECUTIVE_REQUIRED:
            logger.info(
                f"[{symbol}] 오더블럭 매수 신호 (존 {distal:,.2f}~{proximal:,.2f}, 손절 {stop:,.2f}, "
                f"연속 {consecutive}회)"
            )
            return {
                'action': 'BUY', 'price': curr_price, 'stop_price': stop, 'ob_date': ob.get('ob_date'),
                'reasons': ["오더블럭", f"존 {distal:,.2f}~{proximal:,.2f}", f"손절 {stop:,.2f}"],
            }
        if current_signal:
            logger.info(f"[{symbol}] 오더블럭 매수 신호 대기 ({consecutive}/{cls.CONSECUTIVE_REQUIRED})")
        return None

    @classmethod
    def calc_entry_qty(cls, cur_amount: float, price: float, full_entry: bool,
                       cached_indicators: Dict, entry_result: Dict) -> int:
        """R 사이징 — 손절 시 배정금의 RISK_PER_TRADE만 잃도록 (전량 모드면 FULL_ENTRY_PCT)"""
        return cls.entry_qty(cur_amount, price, float(entry_result['stop_price']), full_entry)

    # ========================================
    # 매도
    # ========================================

    @classmethod
    def _resolve_stop(cls, symbol: str, entry: float, stop_price: Optional[float]) -> float:
        if stop_price:
            return float(stop_price)
        # 편입 포지션·컬럼 추가 전 포지션 — OB 근거가 없으니 최대 손절폭으로 지킨다
        logger.warning(f"[{symbol}] STOP_PRICE 없음 → 폴백 손절 (진입가 −{cls.MAX_STOP_LOSS_PCT:g}%)")
        return cls.fallback_stop(entry)

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
        stop_price: Optional[float] = None,
    ) -> Dict:
        """청산 판정 — 단일 청산선 (손절가 → +1R 본전 → +2R 고점 − ATR×TRAIL_ATR_MULT)

        ATR이 없어도 손절·본전 단계는 동작해야 한다 (시작 청산선이 ATR과 무관) — 트레일링만 생략된다.
        """
        curr_price = float(current_price)
        entry = float(entry_price) if entry_price else 0
        if entry <= 0:
            logger.warning(f"[{symbol}] ENTRY_PRICE 없음, 청산 체크 스킵")
            return {"action": "HOLD", "reasons": []}

        stop = cls._resolve_stop(symbol, entry, stop_price)
        atr = cached_indicators.get('atr', 0) or 0                        # 완성봉 ATR
        # 장중 고가가 PEAK에 아직 반영되지 않았을 수 있으므로 현재가도 후보에 넣는다
        peak = max(peak_price or 0, entry, curr_price)
        exit_line = cls.calculate_exit_line(entry, peak, stop, atr)

        if curr_price <= exit_line:
            stage = cls.exit_stage(entry, peak, stop)
            gain_pct = (curr_price - entry) / entry * 100
            logger.warning(
                f"[{symbol}] 🚨 청산({stage}): 현재가 {curr_price:,.2f} ≤ 청산선 {exit_line:,.2f} "
                f"(평단 {entry:,.2f}, 손절 {stop:,.2f}, 고점 {peak:,.2f}, 손익 {gain_pct:+.1f}%)"
            )
            return {"action": "SELL",
                    "reasons": [stage, f"청산선 {exit_line:,.2f}", f"손익 {gain_pct:+.1f}%"]}
        return {"action": "HOLD", "reasons": []}

    @classmethod
    async def check_partial_take_profit(
        cls,
        symbol: str,
        current_price: Decimal,
        entry_price: Decimal,
        signal: int,
        stop_price: Optional[float] = None,
    ) -> Optional[Dict]:
        """부분 익절 — 진입가 + PARTIAL_TP_R × R 도달 시 절반 (SIGNAL 1에서만, 1회)"""
        if signal != 1 or cls.PARTIAL_TP_R <= 0:
            return None
        entry = float(entry_price) if entry_price else 0
        if entry <= 0:
            return None

        stop = cls._resolve_stop(symbol, entry, stop_price)
        target = cls.partial_target(entry, stop)
        curr_price = float(current_price)
        if curr_price < target:
            return None

        logger.info(f"[{symbol}] 부분 익절: 현재가 {curr_price:,.2f} ≥ 목표 {target:,.2f} "
                    f"(+{cls.PARTIAL_TP_R:g}R) → 절반 매도")
        return {"action": "SELL_HALF",
                "reasons": [f"부분익절(+{cls.PARTIAL_TP_R:g}R)", f"목표가 {target:,.2f}"]}
