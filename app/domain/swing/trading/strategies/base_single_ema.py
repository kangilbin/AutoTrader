"""
단일 20EMA 전략 베이스 클래스 (Base Single EMA Strategy)

백테스팅과 실전 전략의 공통 로직을 포함합니다.
- 공통 파라미터
- 2차 매수 조건 파라미터
- trailing stop 파라미터
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

    # 1차 매수 [시나리오 A] 눌림목 매집 진입
    ACCUM_ENTRY_ATR_LOWER = -0.5     # 하한: EMA - ATR × 0.5
    ACCUM_ENTRY_ATR_UPPER = 0.5      # 상한: EMA + ATR × 0.5
    ACCUM_ENTRY_ADX_MIN = 18         # ADX 하한 (약한 추세 배제)
    ACCUM_ENTRY_ADX_MAX = 30         # ADX 상한
    ACCUM_ENTRY_OBV_MIN = 0.0        # OBV z-score 최소값

    # 1차 매수 [시나리오 B] 추세 추종 EMA 돌파 진입
    BREAKOUT_ENTRY_GAP_MAX = 1.03    # EMA 괴리율 상한 (돌파 직후만)
    BREAKOUT_ENTRY_ADX_MIN = 15      # ADX 최소값 (최소 추세 강도)
    BREAKOUT_ENTRY_OBV_MIN = 0.0     # OBV z-score 최소값

    # 2차 매수 조건 (통합: 추세 안정화 후 추가 매수)
    SECOND_BUY_ATR_LOWER = 0.5       # 하한: EMA + ATR × 0.5 (1차 매집 범위 이탈 확인)
    SECOND_BUY_ATR_UPPER = 2.0       # 상한: EMA + ATR × 2.0 (과열 방지)
    SECOND_BUY_ADX_MIN = 20          # ADX 최소값 (안정적 추세)
    SECOND_BUY_OBV_MIN = 0.5         # OBV z-score 최소값

    # 매도 조건
    # [1차 방어선] 장중 손절
    ATR_MULTIPLIER = 1.0

    # [2차 방어선] 조건부 trailing stop (ATR 기반 동적 임계값)
    TRAILING_STOP_ATR_PARTIAL_MULT = 2.0  # ATR × 2.0 하락 시 1차 분할 매도
    TRAILING_STOP_ATR_FULL_MULT = 3.0     # ATR × 3.0 하락 시 2차 전량 매도
    TRAILING_STOP_PARTIAL_MIN = 3.0       # 최소 하한 (ATR 기반이 너무 작을 때 안전장치)
    TRAILING_STOP_FULL_MIN = 5.0          # 최소 하한 (ATR 기반이 너무 작을 때 안전장치)
    # 폴백 (ATR 무효 시 고정값)
    TRAILING_STOP_PARTIAL = 5.0
    TRAILING_STOP_FULL = 8.0

