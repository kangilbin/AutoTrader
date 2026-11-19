# 매매 전략 구조

이 디렉토리는 두 가지 타입의 전략을 포함합니다:
1. **백테스트 전략** (`BacktestStrategy` 상속) - 과거 데이터로 전략 성능 검증 (동기)
2. **실시간 거래 전략** (`TradingStrategy` 상속) - 실시간 매매 신호 생성 (비동기)

## 파일 구조

```
base_strategy.py                    # 백테스트 전략 베이스
├── ema_strategy.py                 # EMA 골든크로스 백테스트
├── ichimoku_strategy.py            # 일목균형표 백테스트
└── single_ema_backtest_strategy.py # 단일 20EMA 백테스트

base_trading_strategy.py            # 실시간 거래 전략 베이스
base_single_ema.py                  # 실전/백테스트 공통 파라미터 및 trailing stop 파라미터
└── single_ema_strategy.py          # 단일 20EMA 실전 전략

trading_strategy_factory.py         # 실시간 전략 팩토리
strategy_factory.py                 # 백테스트 전략 팩토리 (상위 디렉토리)
```

## SWING_TYPE 매핑

| SWING_TYPE | 백테스트 전략 | 실시간 거래 전략 |
|------------|---------------|------------------|
| A          | EMAStrategy | SingleEMAStrategy |
| B          | IchimokuStrategy | SingleEMAStrategy (TODO) |
| S          | SingleEMABacktestStrategy | SingleEMAStrategy |

---

## 단일 20EMA 실전 전략 (SingleEMAStrategy)

20일 지수이동평균(EMA)을 중심으로 추세 추종 매매를 수행하는 전략입니다.
분할 매수/매도를 지원하며, 장중 실시간 신호 기반으로 매매를 실행합니다.

### 사용 지표

| 지표 | 용도 |
|-----|------|
| EMA 20 | 단기 추세 판단, 매수/매도 기준선 |
| ADX / +DI / -DI | 추세 강도 및 방향 판단 |
| ATR (14) | 변동성 기반 손절선, 가격 가드레일 |
| OBV z-score | 거래량 기반 수급 강도 |

모든 지표는 전일 종가 기준으로 Redis에 캐시되며, 장중에 현재가를 이용한 증분 계산으로 실시간 업데이트됩니다.

### 배치 스케줄

| 시간 | 배치 | 설명 |
|-----|------|------|
| 08:29 | `cache_warmup_job` | 지표 캐시 워밍업 (Redis) |
| 09:00~14:55 | `trade_job` | 장중 매수/매도 체크 (5분 간격) |
| 15:35 | `day_collect_job` | 일별 데이터 수집 |

### SIGNAL 상태 흐름

```
SIGNAL 0 (대기)
  │ 1차 매수
  ▼
SIGNAL 1 (1차 매수 완료) ← PEAK_PRICE = 매수가 초기화
  │ 장중 5분마다: PEAK_PRICE = max(PEAK_PRICE, 장중고가)
  ├─ [1차 방어선] 즉시 손절 ──────────────────────→ SIGNAL 0 (PEAK_PRICE = NULL)
  ├─ [2차 방어선] 장중 전량 매도 ─────────────────→ SIGNAL 0 (PEAK_PRICE = NULL)
  ├─ [2차 방어선] 장중 1차 분할 매도 ──→ SIGNAL 3 (PEAK_PRICE 유지)
  │                                          │ 장중 5분마다: PEAK_PRICE = max(PEAK_PRICE, 장중고가)
  │                                          ├─ [1차 방어선] 즉시 손절 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
  │                                          ├─ 재진입 매수 ──→ SIGNAL 1 (PEAK_PRICE = 현재가)
  │                                          └─ 장중 2차 전량 매도 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
  │
  └─ 2차 매수 조건 충족 ──→ SIGNAL 2 (PEAK_PRICE 유지)
                                │ 장중 5분마다: PEAK_PRICE = max(PEAK_PRICE, 장중고가)
                                ├─ [1차 방어선] 즉시 손절 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
                                ├─ [2차 방어선] 장중 1차 분할 매도 ──→ SIGNAL 3
                                └─ [2차 방어선] 장중 전량 매도 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
```

> **SIGNAL 3 재진입**: 1차 분할 매도 후 잔량 보유 중, 1차 매수 조건 재충족 시 잔량 유지 + 추가 매수로 재진입합니다. PEAK_PRICE는 현재가로 재설정됩니다.

---

### 매수 전략

#### 1차 매수

공통 필터 + **시나리오 A 또는 B** 충족 후 **연속 2회(약 10분)** 확인 시 진입 (현재가 기준)

**공통 필터:**
- **급등 필터**: 당일 변동률(절대값) ≤ 5% (급등/급락 종목 배제)
- **전일 양봉**: 전일 종가 >= 전일 시가 (양봉 캔들)

**시나리오 A — 눌림목 매집 진입**
- **가격 범위**: 실시간 EMA20 ± ATR×0.5 이내
- **수급 개선**: OBV z-score > 0 AND 전일 대비 상승
- **추세 존재**: ADX 18~30 + +DI > -DI + EMA 상승 중 (실시간 > 전일)

**시나리오 B — 추세 추종 돌파 진입**
- **EMA 상승 추세**: 실시간 EMA20 > 전일 EMA20
- **가격 확인**: 현재가 > 실시간 EMA20 (괴리율 3% 이내)
- **추세 방향**: +DI > -DI + ADX > 15
- **수급 동반**: OBV z-score > 0

> 연속 2회 조건은 Redis에 상태를 저장하여 swing_id별로 독립 관리합니다.
> EMA 상승 추세는 실시간 EMA20과 전일 EMA20을 비교하여 판단합니다.

#### 2차 매수

1차 매수 후 **최소 20분 경과** 시, 아래 조건 **모두** 충족 시 추가 매수

- **전일 양봉**: 전일 종가 >= 전일 시가
- **가격 위치**: EMA + ATR×0.5 ≤ 현재가 ≤ EMA + ATR×2.0 (추세 확인 + 과열 방지)
- **추세 안정**: ADX >= 20 + +DI > -DI
- **수급 확인**: OBV z-score >= 0.5

---

### 매도 전략

#### [1차 방어선] 장중 즉시 매도 (5분마다 체크)

- **EMA-ATR 동적 손절**: 현재가 ≤ 실시간 EMA20 - ATR×1.0 시 즉시 전량 매도
- SIGNAL 상태(1, 2, 3) 무관하게 항상 최우선 적용
- 즉시 SIGNAL 0으로 복귀
- ATR=0인 경우 손절 체크 스킵 (HOLD)

#### [2차 방어선] 장중 조건부 trailing stop (5분마다 체크)

장중 5분마다 `trade_job`에서 SIGNAL 1, 2, 3 상태의 종목을 대상으로 trailing stop 조건을 체크합니다.
게이트 조건 충족 시 즉시 매도를 실행합니다.

**게이트 조건 (2가지 **모두** 충족 시 매도 판단):**

| 조건 | 설명 |
|-----|------|
| 추세 약화 | (+DI - -DI) 격차가 2일 연속 감소 (prev_prev > prev > today) |
| 수급 약화 | OBV z-score < -0.65 (SUPPLY_WEAKNESS_OBV_Z 임계값) |

> 추세 추종 전략에서 매도는 보수적이어야 하므로, 추세 약화와 수급 약화가 동시에 발생해야 매도를 판단합니다.

**DI 비교 데이터 소스:**
- `prev_prev DI`: 캐시 워밍업 시 저장 (`prev_plus_di`, `prev_minus_di`)
- `prev DI`: 캐시의 `plus_dm14/minus_dm14/atr`에서 역산 (`(dm14/atr) × 100`)
- `today DI`: 실시간 증분 계산 (`realtime_plus_di`, `realtime_minus_di`)

**1차 분할 매도 (SIGNAL 1/2 → SIGNAL 3)**
- 게이트 조건 충족 + 현재가 ≤ 고점(PEAK_PRICE) - **ATR×2.0**
- `sell_ratio%` 분할 매도, 잔량 보유 후 SIGNAL 3으로 전환

**2차 전량 매도 (SIGNAL 3 → SIGNAL 0)**
- 게이트 조건 충족 + 현재가 ≤ 고점(PEAK_PRICE) - **ATR×3.0**
- 잔량 전량 매도 후 SIGNAL 0으로 복귀

**ATR 기반 손절가 계산:**
- 1차 손절가 = `PEAK_PRICE - ATR × 2.0`
- 2차 손절가 = `PEAK_PRICE - ATR × 3.0`
- ATR 무효 시 폴백: 1차 5.0%, 2차 8.0%

---

### 체결 분할 (TWAP)

대량 주문 시 시장 충격(슬리피지)을 줄이기 위해 주문을 자동으로 여러 사이클에 나눠 체결합니다.
매수/매도 모두 동일하게 적용됩니다.

#### 발동 조건

- 주문금액(또는 목표수량 × 현재가)이 **일평균거래대금 × 0.5%** 초과 시 자동 분할
- 이하이면 단일 주문으로 한 번에 처리

#### 분할 실행 흐름

```
1사이클 (신호 발생 시점)
    │
    ├─ 단일 주문 가능 → 즉시 전량 체결 (completed: True)
    │
    └─ 분할 필요 → 첫 chunk 주문 + Redis에 진행 상태 저장 (completed: False)
                        │
                        ├─ 다음 사이클 (5분 후)
                        │   └─ continue_partial_execution 호출
                        │       ├─ 매수 중 손절 신호 발생 → 매수 중단 (aborted: True)
                        │       ├─ 잔여 chunk 주문 + 상태 업데이트
                        │       └─ 완료 시 Redis 키 삭제 (completed: True)
                        │
                        └─ 목표 금액/수량 소진까지 반복
```

#### 우선순위

분할 체결이 진행 중(`partial_exec:{swing_id}` Redis 키 존재)이면 신호 로직보다 **최우선으로 처리**됩니다.
분할이 완전히 완료된 후 SIGNAL 상태가 업데이트됩니다.

#### 매수 중 손절 처리

분할 매수 진행 중 EMA-ATR 손절 조건 충족 시:
- 매수 즉시 중단 (추가 chunk 취소)
- 이미 매수한 수량은 보유 유지
- 보유 수량 있으면 SIGNAL 1, 없으면 SIGNAL 0으로 처리

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| SLIPPAGE_RATIO | 0.005 | 사이클당 일평균거래대금 비율 (0.5%) |

---

### 주요 파라미터

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| EMA_PERIOD | 20 | 단기 EMA 기간 |
| OBV_LOOKBACK | 7 | OBV z-score 계산 기간 |
| MAX_SURGE_RATIO | 0.05 | 전일 대비 최대 급등률 (5%) |
| CONSECUTIVE_REQUIRED | 2 | 연속 확인 횟수 (10분) |
| ATR_MULTIPLIER | 1.0 | 즉시 손절 ATR 배수 |
| SUPPLY_WEAKNESS_OBV_Z | -0.65 | 수급 약화 OBV z-score 임계값 |
| TRAILING_STOP_ATR_PARTIAL_MULT | 2.0 | 1차 분할 매도 ATR 배수 |
| TRAILING_STOP_ATR_FULL_MULT | 3.0 | 2차 전량 매도 ATR 배수 |
| TRAILING_STOP_PARTIAL | 5.0 | 1차 폴백 (ATR 무효 시) |
| TRAILING_STOP_FULL | 8.0 | 2차 폴백 (ATR 무효 시) |
| SECOND_BUY_TIME_MIN | 1200 | 2차 매수 최소 대기 시간 (초, 20분) |
| SECOND_BUY_ATR_LOWER | 0.5 | 2차 매수 하한 배수 (EMA + ATR×0.5) |
| SECOND_BUY_ATR_UPPER | 2.0 | 2차 매수 상한 배수 (EMA + ATR×2.0) |
| SECOND_BUY_ADX_MIN | 20 | 2차 매수 ADX 최소값 |
| SECOND_BUY_OBV_MIN | 0.5 | 2차 매수 OBV z-score 최소값 |

---

## 실전 전략 vs 백테스팅 전략 비교

| 구분 | 실전 전략 (SingleEMAStrategy) | 백테스팅 전략 (SingleEMABacktestStrategy) |
|------|-------------------------------|------------------------------------------|
| **데이터** | 실시간 5분 간격 체크 | 일별 OHLCV 데이터 |
| **1차 매수 시나리오** | A(눌림목 매집) + B(추세 추종 돌파) | 동일 |
| **1차 매수 수급(A)** | OBV z > 0 + 전일 대비 상승 | 동일 |
| **1차 매수 EMA 상승(A)** | 실시간 EMA20 > 전일 EMA20 | 금일 EMA20 > 전일 EMA20 |
| **1차 매수 EMA 추세(B)** | 실시간 EMA20 > 전일 EMA20 + 현재가 > EMA | 금일 EMA20 > 전일 EMA20 + 종가 > EMA |
| **전일 양봉** | 전일 종가 >= 전일 시가 (양봉 캔들) | 동일 |
| **급등 필터** | 당일 변동률(abs) ≤ 5% | 동일 |
| **연속 확인** | 2회 (Redis, ~10분) | 1회 |
| **2차 매수** | 통합 조건 (20분 경과 + 전일 양봉) | 동일 (전일 양봉) |
| **즉시 손절** | 실시간 현재가 기준 | 일일 저가 기준 |
| **2차 방어선** | 장중 실시간 trailing stop (5분마다 체크) | EOD 조건부 trailing stop (종가 기준) |
| **2차 방어선 하락률 기준** | 현재가 ≤ 고점 - ATR×MULT | 저가 ≤ 고점 - ATR×MULT |
| **PEAK_PRICE 갱신** | 장중 5분마다 고가(stck_hgpr) 기준 | 일일 고가(STCK_HGPR) 기준 |
| **DI 비교 데이터** | 캐시(prev_prev) + 캐시 역산(prev) + 실시간(today) | 일별 데이터 직접 비교 |
| **1차 분할 매도** | 고점 대비 ATR×2.0 (최소 3%) | 동일 (BUY_COMPLETE → SELL_PRIMARY) |
| **2차 전량 매도** | 고점 대비 ATR×3.0 (최소 5%) | 동일 (SELL_PRIMARY → None) |
| **1차 매도 후 재진입** | SIGNAL 3에서 재진입 매수 가능 | SELL_PRIMARY에서 재진입 매수 가능 |
| **포지션 상태** | SIGNAL 0→1→2→3→0 | None→BUY_COMPLETE→SELL_PRIMARY→None |
| **주요 목적** | 장중 실시간 매매 | 과거 데이터 성능 검증 |

---

## 관련 문서

- [백테스팅 전략](../../backtest/strategies/README.md) - 백테스팅용 전략
- [백테스팅 API](../../backtest/backtest_router.py) - 백테스팅 REST API