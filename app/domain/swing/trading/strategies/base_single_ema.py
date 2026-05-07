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
    OBV_LOOKBACK = 7
    MAX_SURGE_RATIO = 0.05       # 전일 대비 최대 급등률 (5%)

    # 매수 [시나리오 A] 눌림목 매집 진입
    ACCUM_ENTRY_ATR_LOWER = -0.5     # 하한: EMA - ATR × 0.5
    ACCUM_ENTRY_ATR_UPPER = 0.5      # 상한: EMA + ATR × 0.5
    ACCUM_ENTRY_ADX_MIN = 18         # ADX 하한 (약한 추세 배제)
    ACCUM_ENTRY_ADX_MAX = 30         # ADX 상한
    ACCUM_ENTRY_OBV_MIN = 0.0        # OBV z-score 최소값

    # 매수 [시나리오 B] 추세 추종 EMA 돌파 진입
    BREAKOUT_ENTRY_GAP_MAX = 1.06    # EMA 괴리율 상한 (돌파 직후만)
    BREAKOUT_ENTRY_ADX_MIN = 15      # ADX 최소값 (최소 추세 강도)
    BREAKOUT_ENTRY_OBV_MIN = 0.0     # OBV z-score 최소값

    # 포지션 사이징
    # Qty = min(CUR_AMOUNT × ENTRY_PCT / 현재가, CUR_AMOUNT × MAX_LOSS_PCT / risk_per_share)
    ENTRY_PCT = 0.5                  # 매 사이클 배정금 대비 투입 비율 (50%)
    MAX_LOSS_PCT = 0.10              # 손절 시 배정금 대비 최대 손실률 (10%, 고변동성 안전장치)

    # 매도 조건
    # [손절] EMA-ATR 이탈 시 즉시 전량 매도 (SIGNAL 2에서는 본전 방어 적용)
    ATR_MULTIPLIER = 1.0

    # [1차 익절] 고점 대비 ATR 하락 시 50% 매도 (SIGNAL 1 → 2)
    TRAILING_STOP_ATR_MULT = 2.0          # 고점 - ATR × 2.0
    FIRST_PROFIT_TAKE_RATIO = 0.5         # 50% 매도

    # [2차 익절] 고점 대비 ATR 하락 + OBV 꺾임 시 잔량 전량 매도 (SIGNAL 2 → 0)
    OBV_Z_SELL_THRESHOLD = -0.5           # OBV z-score < -0.5 시 2차 매도

    # 폴백 (ATR 무효 시 고정값)
    TRAILING_STOP_FALLBACK_PCT = 5.0

