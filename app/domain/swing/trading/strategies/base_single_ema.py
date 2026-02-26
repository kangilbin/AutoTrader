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

    # 매수 조건
    OBV_Z_BUY_THRESHOLD = 1.0
    OBV_LOOKBACK = 7
    MAX_SURGE_RATIO = 0.05       # 전일 대비 최대 급등률 (5%)

    # 2차 매수 조건
    # [시나리오 A] 추세 강화형 (EMA-ATR 가드레일)
    TREND_BUY_ATR_LOWER = 0.3        # 하한: EMA + ATR × 0.3 (추세 가속 최소선)
    TREND_BUY_ATR_UPPER = 2.0        # 상한: EMA + ATR × 2.0 (과열 방지선)
    TREND_BUY_OBV_THRESHOLD = 1.2    # OBV z-score 최소값
    TREND_BUY_ADX_MIN = 25           # ADX 최소값 (강한 추세)

    # [시나리오 B] 눌림목 반등 (EMA-ATR 가드레일)
    PULLBACK_BUY_ATR_LOWER = -0.5    # 하한: EMA - ATR × 0.5 (조정 허용 하한)
    PULLBACK_BUY_ATR_UPPER = 0.3     # 상한: EMA + ATR × 0.3 (조정 범위 상한)
    PULLBACK_BUY_OBV_MIN = 0.0       # OBV z-score 최소값 (중립 이상)
    PULLBACK_BUY_ADX_MIN = 18        # ADX 하한 (추세 유지)
    PULLBACK_BUY_ADX_MAX = 23        # ADX 상한 (조정 구간)
    PULLBACK_BUY_REBOUND_RATIO = 1.004  # 저점 대비 반등 비율 (0.4%)

    # 매도 조건
    # [1차 방어선] 장중 손절
    ATR_MULTIPLIER = 1.0

    # [2차 방어선] 조건부 trailing stop
    TRAILING_STOP_PARTIAL = 5.0  # 고점 대비 -5% → 1차 분할 매도
    TRAILING_STOP_FULL = 8.0     # 고점 대비 -8% → 2차 전량 매도

