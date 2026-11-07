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
| 15:35 | `day_collect_job` | 일별 데이터 수집 + EOD trailing stop 신호 확정 |

### SIGNAL 상태 흐름

```
SIGNAL 0 (대기)
  │ 1차 매수
  ▼
SIGNAL 1 (1차 매수 완료) ← PEAK_PRICE = 매수가 초기화
  │ 매일 15:35 EOD: PEAK_PRICE = max(PEAK_PRICE, 종가)
  ├─ [1차 방어선] 즉시 손절 ──────────────────────→ SIGNAL 0 (PEAK_PRICE = NULL)
  ├─ [2차 방어선] EOD 전량 매도 ──────────────────→ SIGNAL 0 (PEAK_PRICE = NULL)
  ├─ [2차 방어선] EOD 1차 분할 매도 ──→ SIGNAL 3 (PEAK_PRICE 유지)
  │                                          │ 매일 15:35 EOD: PEAK_PRICE = max(PEAK_PRICE, 종가)
  │                                          ├─ 재진입 매수 ──→ SIGNAL 1 (PEAK_PRICE = 현재가)
  │                                          └─ EOD 2차 전량 매도 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
  │
  └─ 2차 매수 조건 충족 ──→ SIGNAL 2 (PEAK_PRICE 유지)
                                │ 매일 15:35 EOD: PEAK_PRICE = max(PEAK_PRICE, 종가)
                                ├─ [1차 방어선] 즉시 손절 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
                                ├─ [2차 방어선] EOD 1차 분할 매도 ──→ SIGNAL 3
                                └─ [2차 방어선] EOD 전량 매도 ──→ SIGNAL 0 (PEAK_PRICE = NULL)
```

> **SIGNAL 3 재진입**: 1차 분할 매도 후 잔량 보유 중, 1차 매수 조건 재충족 시 잔량 유지 + 추가 매수로 재진입합니다. PEAK_PRICE는 현재가로 재설정됩니다.

---

### 매수 전략

#### 1차 매수

아래 **6가지 조건 모두** 충족 후 **연속 2회(약 10분)** 확인 시 진입 (현재가 기준)

1. **EMA 근접**: 현재가 ≥ 실시간 EMA20 × 0.995 (EMA 0.5% 이내)
2. **수급 강도**: OBV z-score > 0 AND 전전일 대비 상승
3. **급등 필터**: 전일 대비 변동률 ≤ 5% (당일 급등 추격매수 방지)
4. **추세 강화**: +DI > -DI AND ADX 상승 중 (전일 대비)
5. **EMA 상승**: EMA20 > 전전일 EMA20 (하락장 진입 방지)
6. **전일 양봉**: 전일 종가 > 전전일 종가

> 연속 2회 조건은 Redis에 상태를 저장하여 swing_id별로 독립 관리합니다.
> 전전일 데이터(`prev_close`, `prev_obv_z`, `prev_adx`, `prev_ema20`)는 Redis 캐시에 포함되어 실시간 조건 판단에 사용됩니다.

#### 2차 매수

1차 매수 후 **최소 20분 경과** 시, 아래 **시나리오 A 또는 B** 중 하나 충족 시 추가 매수

**시나리오 A - 추세 강화형**
- 가격 가드레일: EMA + ATR×0.3 ≤ 현재가 ≤ EMA + ATR×2.0
- ADX > 25 (강한 추세 지속)
- +DI > -DI (상승 방향 유지)
- OBV z-score ≥ 1.2 (수급 지속)

**시나리오 B - 눌림목 반등**
- 가격 가드레일: EMA - ATR×0.5 ≤ 현재가 ≤ EMA + ATR×0.3
- 18 ≤ ADX ≤ 23 (조정 구간, 추세 유지)
- +DI > -DI (상승 방향 유지)
- OBV z-score > 0.5 (수급 중립 이상)
- 장중 저가(Redis 기록) 대비 0.4% 이상 반등

---

### 매도 전략

#### [1차 방어선] 장중 즉시 매도 (5분마다 체크)

- **EMA-ATR 동적 손절**: 현재가 ≤ 실시간 EMA20 - ATR×1.0 시 즉시 전량 매도
- SIGNAL 상태(1 또는 2) 무관하게 항상 최우선 적용
- 즉시 SIGNAL 0으로 복귀

#### [2차 방어선] EOD 조건부 trailing stop (장 마감 후 종가 기준)

매일 15:35 `day_collect_job`에서 SIGNAL 1, 2, 3 상태의 종목을 대상으로 trailing stop 조건을 체크합니다.
결과는 DB의 `EOD_SIGNALS` 컬럼에 JSON으로 저장되고, `PEAK_PRICE`도 함께 업데이트됩니다.
다음 거래일 장중에 이 신호를 읽어 매도를 실행합니다.

**전제 조건 (2가지 중 하나 이상 충족 시 매도 판단):**

| 조건 | 설명 |
|-----|------|
| 추세 약화 | (+DI - -DI) 격차가 2일 연속 감소 |
| 수급 약화 | OBV z-score가 2일전 대비 감소 |

> 추세 약화와 수급 약화는 각각 독립적인 위험 신호이므로, 하나만 발생해도 하락률 평가를 진행합니다.

**1차 분할 매도 (SIGNAL 1/2 → SIGNAL 3)**
- 전제 조건 충족 + 고점(PEAK_PRICE) 대비 **5% 이상** 하락 시
- `sell_ratio%` 분할 매도, 잔량 보유 후 SIGNAL 3으로 전환

**2차 전량 매도 (SIGNAL 3 → SIGNAL 0)**
- 전제 조건 충족 + 고점(PEAK_PRICE) 대비 **8% 이상** 하락 시
- 잔량 전량 매도 후 SIGNAL 0으로 복귀

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
| TRAILING_STOP_PARTIAL | 5.0 | 고점 대비 -5% → 1차 분할 매도 |
| TRAILING_STOP_FULL | 8.0 | 고점 대비 -8% → 2차 전량 매도 |
| SECOND_BUY_TIME_MIN | 1200 | 2차 매수 최소 대기 시간 (초, 20분) |
| TREND_BUY_ATR_LOWER | 0.3 | 시나리오 A 하한 배수 |
| TREND_BUY_ATR_UPPER | 2.0 | 시나리오 A 상한 배수 |
| TREND_BUY_OBV_THRESHOLD | 1.2 | 시나리오 A OBV 기준 |
| TREND_BUY_ADX_MIN | 25 | 시나리오 A ADX 최소값 |
| PULLBACK_BUY_ATR_LOWER | -0.5 | 시나리오 B 하한 배수 |
| PULLBACK_BUY_ATR_UPPER | 0.3 | 시나리오 B 상한 배수 |
| PULLBACK_BUY_ADX_MIN | 18 | 시나리오 B ADX 하한 |
| PULLBACK_BUY_ADX_MAX | 23 | 시나리오 B ADX 상한 |
| PULLBACK_BUY_OBV_MIN | 0.5 | 시나리오 B OBV 기준 (백테스트는 0.0) |
| PULLBACK_BUY_REBOUND_RATIO | 1.004 | 시나리오 B 저가 반등 비율 (0.4%) |

---

## 실전 전략 vs 백테스팅 전략 비교

| 구분 | 실전 전략 (SingleEMAStrategy) | 백테스팅 전략 (SingleEMABacktestStrategy) |
|------|-------------------------------|------------------------------------------|
| **데이터** | 실시간 5분 간격 체크 | 일별 OHLCV 데이터 |
| **1차 매수 수급** | OBV z > 0 + 전전일 대비 상승 | 동일 |
| **1차 매수 추세** | +DI > -DI AND ADX 상승 중 | 동일 |
| **1차 매수 EMA 상승** | EMA20 > 전전일 EMA20 | 동일 |
| **1차 매수 전일 양봉** | 전일 종가 > 전전일 종가 | 동일 |
| **1차 매수 급등 필터** | 전일 대비 ≤ 5% | 동일 |
| **연속 확인** | 2회 (Redis, ~10분) | 1회 |
| **2차 매수** | 동일 (시나리오 A/B, 20분 경과) | 동일 (시나리오 A/B) |
| **즉시 손절** | 실시간 현재가 기준 | 일일 저가 기준 |
| **EOD 매도 로직** | trailing stop (DI격차+OBV+고점하락률) | 동일 |
| **EOD 신호 저장** | DB (EOD_SIGNALS + PEAK_PRICE 컬럼) | 인메모리 (peak_price 변수) |
| **1차 분할 매도** | 고점 대비 -5% (SIGNAL 1/2 → 3) | 동일 (BUY_COMPLETE → SELL_PRIMARY) |
| **2차 전량 매도** | 고점 대비 -8% (SIGNAL 3 → 0) | 동일 (SELL_PRIMARY → None) |
| **1차 매도 후 재진입** | SIGNAL 3에서 재진입 매수 가능 | SELL_PRIMARY에서 재진입 매수 가능 |
| **포지션 상태** | SIGNAL 0→1→2→3→0 | None→BUY_COMPLETE→SELL_PRIMARY→None |
| **주요 목적** | 장중 실시간 매매 | 과거 데이터 성능 검증 |

---

## 관련 문서

- [백테스팅 전략](../../backtest/strategies/README.md) - 백테스팅용 전략
- [백테스팅 API](../../backtest/backtest_router.py) - 백테스팅 REST API