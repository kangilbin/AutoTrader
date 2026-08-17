"""
단일 20EMA 전략 베이스 클래스 (Base Single EMA Strategy)

백테스팅과 실전 전략의 공통 로직을 포함합니다.
- 매수 조건 파라미터
- 매도 조건 파라미터
- 포지션 사이징 파라미터
"""


class BaseSingleEMAStrategy:
    """단일 20EMA 전략 공통 베이스 클래스"""

    # ========================================
    # 공통 파라미터
    # ========================================

    # EMA 기간
    EMA_PERIOD = 20

    # 매수 공통 조건
    OBV_Z_BUY_THRESHOLD = 1.0
    OBV_LOOKBACK = 14                # 매수용 OBV z-score 기간
    OBV_LOOKBACK_SELL = 14           # 2차 익절용 OBV z-score 기간 (추세 레벨 수급 이탈 감지)
    MAX_SURGE_RATIO = 0.05       # 전일 대비 최대 급등률 (5%)
    UPPER_SHADOW_RATIO_MAX = 0.4     # 전일 윗꼬리가 캔들 범위의 40% 이상이면 매수 차단
    MIN_CANDLE_RANGE_PCT = 0.03      # 윗꼬리 필터 최소 캔들 범위 (종가 대비 3%)

    # 매수 [추세 추종 EMA 돌파 진입]
    BREAKOUT_ENTRY_GAP_MAX = 1.06    # EMA 괴리율 상한 (돌파 직후만)
    BREAKOUT_ENTRY_ADX_MIN = 15      # ADX 최소값 (최소 추세 강도)
    BREAKOUT_ENTRY_OBV_MIN = 0.0     # OBV z-score 최소값 (레거시, conviction 등서 참조)

    # 매수 [중장기(스윙) 수급 축적 게이트 — OBV vs EMA(OBV), avg_vol20 정규화]
    # accum = (obv - obv_ema) / avg_vol20  ("OBV가 자기 최근 평균보다 얼마나 위인가")
    # 매수 조건: accum > ACCUM_HIGH  (수급이 자기 평균 위로 충분히 쌓였을 때만)
    # ※ 실거래는 당일 거래량까지 증분 반영한 **실시간 accum**으로 판정한다 (5분 사이클 반응성 우선).
    #    백테스트는 일봉만 있어 장중 상태를 재현할 수 없으므로 **전일 완성봉**으로 판정한다
    #    (당일봉으로 판정하면 마감 후에야 알 수 있는 값으로 당일 매수하는 선견 편향이 생김).
    #    → 실거래 게이트가 백테스트보다 느슨하게 열릴 수 있으니 ACCUM_HIGH 재산정 시 감안할 것.
    # ※ QQQ/AMD 백테스트: span=12가 두 종목 모두 플러스로 가장 강건 (20은 QQQ 과적합).
    #    obv_z 스파이크 기반 "탑승(경로2)"은 급락 반등에 반응해 성과를 깎아 제거함.
    ACCUM_EMA_PERIOD = 12            # OBV EMA 기간 (스윙 수급 창; 백테스트 튜닝 대상)
    ACCUM_HIGH = 1.0                 # 매수 통과 기준 (백테스트 튜닝 대상)

    # 포지션 사이징 (Conviction Score 기반)
    # Qty = int(CUR_AMOUNT × MAX_ENTRY_PCT × conviction / 현재가)
    MAX_ENTRY_PCT = 0.8              # 최대 투입 비율 (배정금의 80%)
    MIN_CONVICTION = 0.4             # 최소 확신도 (0.4 = 32% 투입)

    # Conviction 가중치
    CONVICTION_OBV_WEIGHT = 0.7      # OBV z-score 가중치 (70%)
    CONVICTION_ADX_WEIGHT = 0.3      # ADX 가중치 (30%)

    @classmethod
    def calc_conviction(cls, adx: float, obv_z: float) -> float:
        """
        매수 신호 강도 기반 확신도 계산 (MIN_CONVICTION ~ 1.0)

        - OBV z-score (70%): 수급 강도 → 추세 지속 가능성
        - ADX (30%): 추세 강도 (25 이상은 과열 감점)
        """
        # OBV 점수: z=0→0.3, z=1.0→0.77, z=1.5+→1.0
        obv_score = max(0.3, min(1.0, 0.3 + obv_z * 0.467))

        # ADX 점수: 15→0.3, 25→1.0, 30+→감점
        if adx <= 25:
            adx_score = max(0.3, min(1.0, 0.3 + (adx - 15) * 0.07))
        else:
            adx_score = max(0.5, 1.0 - (adx - 25) * 0.04)

        raw = obv_score * cls.CONVICTION_OBV_WEIGHT + adx_score * cls.CONVICTION_ADX_WEIGHT
        return max(cls.MIN_CONVICTION, min(1.0, raw))

    # ==================== 매도 조건: 단일 청산선(3단계) ====================
    # 청산선 하나가 이익 구간을 따라 올라가며 손실 제한 → 본전 확보 → 이익 확정을 순서대로 수행한다.
    # (예전에는 익절선(고점 기준)과 손절선(진입가 기준)이 따로 있었고, 1차 익절 후 PEAK를
    #  매도가로 리셋해 잔량이 곧바로 털렸다. 그 구조가 손익비를 0.54까지 떨어뜨렸다.)
    #
    #   진입 시        : max(진입가 − ATR×ATR_MULTIPLIER, 진입가 × (1 − MAX_STOP_LOSS_PCT/100))
    #   이익 +20% 이상 : 진입가(본전)까지 상향
    #   이익 +30% 이상 : 고점 − ATR×TRAILING_STOP_ATR_MULT 까지 상향 (이익 확정)
    #   청산선 이탈    : 잔량 전량 매도
    #
    # ※ "이익"은 고점(PEAK) 기준이며, 청산선은 매 사이클 위 규칙으로 재계산한다.
    #    각 단계가 max()로 결합돼 사실상 내려가지 않는다(래칫).
    # ※ ATR은 반드시 **완성봉 기준**을 넣을 것. 실시간 증분 ATR은 장 초반 당일 고저 범위가
    #    덜 벌어져 작게 나오고, 그러면 청산선이 과도하게 타이트해져 조기 청산된다.
    #    (같은 날에도 평가 시각마다 청산선이 달라져 백테스트와 의미가 어긋난다)
    # 파라미터 근거: AMD(강한 상승)·QQQ(완만한 상승)·PATH(하락 후 횡보) 3종목 백테스트에서
    #    최저 수익률이 -4.71% → +5.64%로 개선되는 조합. 표본이 작아 추가 검증 권장.
    ATR_MULTIPLIER = 2.0                  # 초기 손절 폭 (진입가 기준)
    MAX_STOP_LOSS_PCT = 7.0               # 최대 손절 허용 비율 (매수가 대비 7%)
    BREAKEVEN_ACTIVATE_PCT = 20.0         # 이 이익률부터 청산선을 본전으로 상향
    TRAILING_ACTIVATE_PCT = 30.0          # 이 이익률부터 트레일링 시작
    TRAILING_STOP_ATR_MULT = 4.0          # 고점 − ATR × 4.0

    # [부분 익절] 목표 수익률 도달 시 절반 확정 (선택 기능, 0 이하면 비활성)
    PARTIAL_TAKE_PROFIT_PCT = 50.0        # 이익 +50%에서 절반 매도 (SIGNAL 1 → 2)
    FIRST_PROFIT_TAKE_RATIO = 0.5         # 절반 매도 비율

    # (레거시) 2차 익절 OBV 게이트 — 단일 청산선 도입으로 미사용
    OBV_Z_SELL_THRESHOLD = -0.5

    @classmethod
    def calculate_exit_line(cls, entry_price: float, peak_price: float, atr: float) -> float:
        """단일 청산선 계산 (백테스트·실거래 공용)

        3단계가 max()로 결합돼 이익이 쌓일수록 위로만 올라간다.

        Args:
            entry_price: 평균 매수 단가
            peak_price: 보유 기간 중 고점 (없으면 진입가)
            atr: 실시간 ATR

        Returns:
            청산선 가격 (0이면 판정 불가)
        """
        if entry_price <= 0:
            return 0.0

        peak = max(peak_price or 0, entry_price)

        # 1단계: 최악 방어 (진입가 기준 고정)
        exit_line = entry_price * (1 - cls.MAX_STOP_LOSS_PCT / 100)
        if atr and atr > 0:
            exit_line = max(exit_line, entry_price - atr * cls.ATR_MULTIPLIER)

        gain_pct = (peak - entry_price) / entry_price * 100

        # 2단계: 본전 확보
        if gain_pct >= cls.BREAKEVEN_ACTIVATE_PCT:
            exit_line = max(exit_line, entry_price)

        # 3단계: 이익 확정 (고점 추종)
        if gain_pct >= cls.TRAILING_ACTIVATE_PCT and atr and atr > 0:
            exit_line = max(exit_line, peak - atr * cls.TRAILING_STOP_ATR_MULT)

        return exit_line

    # [수급 안정화] 전량 매도 후 재진입 기준
    COOLDOWN_OBV_EXIT = -0.5              # 수급 이탈 확인 기준 (obv_z < -0.5)
    COOLDOWN_OBV_REENTRY = 0.5            # 수급 재유입 확인 기준 (obv_z > 0.5)

    # 폴백 (ATR 무효 시 고정값)
    TRAILING_STOP_FALLBACK_PCT = 5.0

    # 개장 초기 노이즈 보호 (Opening Guard)
    OPENING_GUARD_MINUTES = 10            # 개장 후 10분간 PEAK 보호 & 익절 체크 스킵

