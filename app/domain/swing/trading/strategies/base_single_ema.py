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
    MAX_LOSS_PCT = 0.03              # 손절 시 배정금 대비 최대 손실률 (3%, 리스크 기반 수량 조절)

    # 매도 조건
    # [손절] EMA - ATR×N 이탈 시 즉시 전량 매도 (SIGNAL 2에서는 본전 방어 적용)
    ATR_MULTIPLIER = 2.0

    # [1차 익절] 고점 대비 ATR 하락 시 50% 매도 (SIGNAL 1 → 2)
    TRAILING_STOP_ATR_MULT = 2.0          # 고점 - ATR × 2.0
    FIRST_PROFIT_TAKE_RATIO = 0.5         # 50% 매도

    # [2차 익절] 고점 대비 ATR 하락 + OBV 꺾임 시 잔량 전량 매도 (SIGNAL 2 → 0)
    OBV_Z_SELL_THRESHOLD = -0.5           # OBV z-score < -0.5 시 2차 매도

    # [수급 안정화] 전량 매도 후 재진입 기준
    COOLDOWN_OBV_EXIT = -0.5              # 수급 이탈 확인 기준 (obv_z < -0.5)
    COOLDOWN_OBV_REENTRY = 0.5            # 수급 재유입 확인 기준 (obv_z > 0.5)

    # 폴백 (ATR 무효 시 고정값)
    TRAILING_STOP_FALLBACK_PCT = 5.0

    # 개장 초기 노이즈 보호 (Opening Guard)
    OPENING_GUARD_MINUTES = 10            # 개장 후 10분간 PEAK 보호 & 익절 체크 스킵

