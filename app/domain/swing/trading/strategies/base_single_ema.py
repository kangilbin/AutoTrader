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

    # 포지션 사이징 (ATR 기반)
    RISK_PCT = 0.02                  # 1회 손절 시 자산 대비 최대 손실률 (2%)
    MAX_POSITION_PCT = 0.25          # 1종목 최대 포지션 비중 (25%)

    # 매도 조건
    # [1차 방어선] 장중 손절 (EMA-ATR 이탈 시 즉시 전량 매도)
    ATR_MULTIPLIER = 1.0

    # [2차 방어선] trailing stop 익절 (고점 대비 ATR 하락 시 전량 매도)
    TRAILING_STOP_ATR_MULT = 2.0  # ATR × 2.0 하락 시 전량 익절
    # 폴백 (ATR 무효 시 고정값)
    TRAILING_STOP_FALLBACK_PCT = 5.0

