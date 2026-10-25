"""
오더블럭 전략 베이스 클래스 (Base Order Block Strategy)

백테스팅과 실전 전략의 공통 상수·계산을 포함합니다.
규칙 정의: docs/01-plan/features/order-block-strategy.plan.md (3·4·8장)
모든 수치는 초기값이며 백테스트 튜닝 대상이다.
"""
from typing import Optional

from app.domain.swing.order_block import OBParams


class BaseOrderBlockStrategy:
    """오더블럭 전략 공통 베이스 클래스"""

    # ==================== 오더블럭 탐지 ====================
    PROXIMAL_SOURCE = "high"         # 근접선 기준 ("high"=OB 고가, "body"=몸통 상단)
    PIVOT_LEN = 2                    # 스윙 고점 판정 좌우 봉 수
    OB_SEARCH_BARS = 10              # BOS 봉에서 거꾸로 음봉을 찾는 범위
    OB_MAX_AGE = 40                  # BOS 후 OB 유효 거래일
    DISPLACEMENT_ATR = 2.0           # 임펄스 최소 폭: (BOS 종가 − OB 저가) / ATR
    IMPULSE_VOL_RATIO = 1.5          # 임펄스 구간 최대 거래량 / 20일 평균거래량
    MAX_ZONE_ATR = 1.5               # 존 폭 상한 (ATR 배수)
    TREND_EMA_PERIOD = 120           # 장기 추세 필터 (지표 컬럼명은 ema120)

    # ==================== 매수 ====================
    ENTRY_CHASE_ATR = 0.25           # 근접선 위 추격 허용 폭 (실전 전용 — 백테스트는 체결가를 근접선 이하로 강제)
    # 급락 필터(당일 등락률)는 두지 않는다 — 존까지의 되돌림이 하루에 크게 오면 바로 그 첫 리테스트를
    # 막는다 (백테스트: 존 터치 16회 중 6회 차단). "존이 뚫리는 중"은 갭 붕괴·장중 원격선 이탈·손절폭 상한이 막는다.
    STOP_ATR_BUFFER = 1.0            # 손절 = 원격선 − ATR × 1.0 (0.3은 꼬리 노이즈에 털렸다 — 청산 실험)
    MAX_STOP_LOSS_PCT = 7.0          # 손절폭 상한 — 넘으면 손절을 줄이지 않고 진입 포기

    # 포지션 사이징 (R 기반) — 손절 시 배정금의 RISK_PER_TRADE만 잃도록
    RISK_PER_TRADE = 0.02
    MAX_ENTRY_PCT = 0.8              # 투입 상한 (단일 이평선과 동일)
    FULL_ENTRY_PCT = 0.99            # 전량 모드 (단일 이평선과 동일 — 체결가 상승 버퍼)
    MAX_ADV_RATIO = 0.05             # 유동성 필터 (배치 공통)

    # ==================== 매도: 단일 청산선 (R 배수 기준 래칫) ====================
    #   진입 시                : 손절가 (매수 시 확정, STOP_PRICE)
    #   고점 ≥ +BREAKEVEN_R    : 본전까지 상향 (0이면 끔)
    #   고점 ≥ +TRAIL_ACTIVATE_R : 고점 − ATR × TRAIL_ATR_MULT 까지 상향
    # ※ ATR은 완성봉 기준 (단일 이평선과 같은 이유 — 장중 증분 ATR은 장 초반 작게 나온다)
    #
    # 2026-10-25 청산 구조 변경 (scripts/research/order_block_research.py exits·sensitivity):
    #   이전(버퍼 0.3·본전 1R·2R 부분익절·트레일 ×3) — 학습 −0.08R / 검증 −0.20R / 최종 −0.10R
    #   현재(버퍼 1.0·본전 없음·부분익절 없음·트레일 ×5) — 학습 +0.29R / 검증 +0.31R / 최종 +0.21R
    #   이익을 키우기 전에 털리던 구조가 손실의 주원인이었다. 주변 27개 조합 모두 플러스(넓은 평지).
    #   승률 약 30% — 대부분 −1R 손절, 소수의 큰 추세가 메우는 추세 추종형이다.
    BREAKEVEN_R = 0.0
    TRAIL_ACTIVATE_R = 2.0
    TRAIL_ATR_MULT = 5.0

    # [부분 익절] 진입가 + PARTIAL_TP_R × R 에서 절반 (SIGNAL 1 → 2, 1회) — 0이면 끔
    PARTIAL_TP_R = 0.0
    FIRST_PROFIT_TAKE_RATIO = 0.5

    # [수급 안정화] 매도 후 재진입 — 단일 이평선과 같은 값 (배치 _handle_cooling_down이 읽는다)
    COOLDOWN_OBV_EXIT = -0.5
    COOLDOWN_OBV_REENTRY = 0.5

    @classmethod
    def ob_params(cls) -> OBParams:
        return OBParams(
            proximal_source=cls.PROXIMAL_SOURCE,
            pivot_len=cls.PIVOT_LEN,
            ob_search_bars=cls.OB_SEARCH_BARS,
            ob_max_age=cls.OB_MAX_AGE,
            displacement_atr=cls.DISPLACEMENT_ATR,
            impulse_vol_ratio=cls.IMPULSE_VOL_RATIO,
            max_zone_atr=cls.MAX_ZONE_ATR,
        )

    @classmethod
    def stop_price(cls, distal: float, atr: float) -> float:
        """손절가 = 원격선 − ATR × STOP_ATR_BUFFER (꼬리 노이즈에 털리지 않도록)"""
        return distal - cls.STOP_ATR_BUFFER * (atr or 0)

    @classmethod
    def stop_pct_ok(cls, entry: float, stop: float) -> bool:
        """손절폭이 0 초과 MAX_STOP_LOSS_PCT 이하인가"""
        if entry <= 0:
            return False
        pct = (entry - stop) / entry * 100
        return 0 < pct <= cls.MAX_STOP_LOSS_PCT

    @classmethod
    def entry_qty(cls, cur_amount: float, price: float, stop: float, full_entry: bool) -> int:
        """매수 수량 — 전량 모드면 FULL_ENTRY_PCT, 아니면 R 사이징(상한 MAX_ENTRY_PCT)"""
        if price <= 0 or cur_amount <= 0:
            return 0
        if full_entry:
            return int(cur_amount * cls.FULL_ENTRY_PCT / price)
        risk = price - stop
        if risk <= 0:
            return 0
        risk_qty = int(cur_amount * cls.RISK_PER_TRADE / risk)
        cap_qty = int(cur_amount * cls.MAX_ENTRY_PCT / price)
        return min(risk_qty, cap_qty)

    @classmethod
    def calculate_exit_line(cls, entry: float, peak: float, stop: float, atr: float) -> float:
        """단일 청산선 (백테스트·실거래 공용) — 3단계가 max()로 결합돼 내려가지 않는다

        R은 실제 평단 − 손절가. R ≤ 0(체결가가 손절가 이하)이면 손절가를 그대로 돌려
        즉시 청산되게 한다 — 0을 돌리면 실전에서 영영 팔지 않는다.
        """
        if entry <= 0:
            return 0.0
        r = entry - stop
        if r <= 0:
            return stop
        peak = max(peak or 0, entry)
        exit_line = stop
        if cls.BREAKEVEN_R > 0 and peak >= entry + cls.BREAKEVEN_R * r:
            exit_line = max(exit_line, entry)
        if peak >= entry + cls.TRAIL_ACTIVATE_R * r and atr and atr > 0:
            exit_line = max(exit_line, peak - cls.TRAIL_ATR_MULT * atr)
        return exit_line

    @classmethod
    def exit_stage(cls, entry: float, peak: float, stop: float) -> str:
        """청산 사유 라벨 — 고점이 몇 R까지 갔었는가"""
        r = entry - stop
        if r <= 0:
            return "손절"
        gain_r = (max(peak or 0, entry) - entry) / r
        if gain_r >= cls.TRAIL_ACTIVATE_R:
            return "이익확정"
        if cls.BREAKEVEN_R > 0 and gain_r >= cls.BREAKEVEN_R:
            return "본전방어"
        return "손절"

    @classmethod
    def partial_target(cls, entry: float, stop: float) -> Optional[float]:
        """부분익절 목표가 = 진입가 + PARTIAL_TP_R × R (PARTIAL_TP_R ≤ 0 이면 부분익절 없음 → None)"""
        if cls.PARTIAL_TP_R <= 0:
            return None
        return entry + cls.PARTIAL_TP_R * (entry - stop)

    @classmethod
    def fallback_stop(cls, entry: float) -> float:
        """STOP_PRICE가 없을 때(편입·컬럼 추가 전 포지션) 쓰는 손절가"""
        return entry * (1 - cls.MAX_STOP_LOSS_PCT / 100)
