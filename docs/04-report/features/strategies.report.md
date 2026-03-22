# 전략 코드 리뷰 및 개선 완료 보고서

> **피처**: strategies (단일 20EMA 스윙 매매 전략)
>
> **프로젝트**: AutoTrader
>
> **작성일**: 2026-02-28
>
> **PDCA 상태**: Completed (Match Rate: 100%)

---

## 1. 개요

### 1.1 목적

실전 매매 전략(`SingleEMAStrategy`)에 대한 코드 리뷰에서 발견된 5개 이슈를 수정하고, 추가로 매매 전략 로직 개선(PEAK_PRICE 추적, ATR 기반 동적 trailing stop)을 적용한다. 모든 변경 사항을 백테스팅 전략에도 동기화한다.

### 1.2 작업 범위

| 구분 | 항목 수 | 우선도 |
|------|:-------:|:------:|
| 코드 리뷰 수정 (Critical) | 3건 | C-1, C-2, C-3 |
| 코드 리뷰 수정 (Warning) | 2건 | W-1, W-3 |
| 전략 로직 개선 | 2건 | PEAK_PRICE, ATR trailing |
| 백테스팅 동기화 | 5건 | 전체 반영 |

### 1.3 변경 파일 요약

| 파일 | 변경 유형 | 라인 변경 |
|------|-----------|-----------|
| `app/domain/swing/trading/strategies/single_ema_strategy.py` | 수정 | +34/-7 |
| `app/domain/swing/trading/strategies/base_trading_strategy.py` | 수정 | +195/-97 |
| `app/domain/swing/trading/strategies/base_single_ema.py` | 수정 | +11/-4 |
| `app/domain/swing/service.py` | 수정 | +8/-4 |
| `app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py` | 수정 | +37/-15 |
| **총 6개 파일** | | **+290/-127** |

---

## 2. 코드 리뷰 수정 사항 (5건)

### 2.1 C-1: `.decode()` 제거 — Critical

**문제**: Redis가 `decode_responses=True`로 설정되어 `str`을 반환하는데, `.decode()` 호출 시 `AttributeError` 발생. 눌림목 반등 2차 매수가 프로덕션에서 작동 불가 상태.

**수정 파일**: `single_ema_strategy.py` (line 439)

```python
# Before (에러 발생)
intraday_low = float(intraday_low_str.decode())

# After
intraday_low = float(intraday_low_str)
```

**영향 범위**: `check_second_buy_signal` — 시나리오 B(눌림목 반등) 정상 작동 복구

---

### 2.2 C-2: SIGNAL 3 손절 체크 누락 — Critical

**문제**: SIGNAL 1/2에는 `check_exit_signal` 호출이 있지만 SIGNAL 3에는 없음. 1차 분할 매도 후 잔여 물량이 급락해도 즉시 손절 불가.

**수정 파일**: `base_trading_strategy.py` (lines 664-819)

**수정 내용**:
- SIGNAL 3 블록 시작에 SIGNAL 1/2와 동일한 손절 체크 패턴 삽입
- `check_exit_signal()` → `execute_second_sell()` → 4항목 리셋(signal=0, entry_price=0, hold_qty=0, peak_price=0)
- `TradeHistoryService.record_trade()` 호출
- 기존 재진입/EOD 로직은 `else` 블록으로 이동 (손절 미발생 시에만 실행)

**SIGNAL 상태 머신 변경**:

```
[수정 전]  SIGNAL 3 → 재진입 / EOD 전량매도만 가능
[수정 후]  SIGNAL 3 → 즉시 손절 / 재진입 / EOD 전량매도 (손절 우선)
```

---

### 2.3 C-3: OBV NaN 필터링 — Critical

**문제**: `obv.diff()` 결과에 NaN이 포함될 수 있어 OBV z-score 계산이 왜곡되거나 JSON 직렬화 시 에러 발생 가능.

**수정 파일**: `service.py` (2개 위치 — lines 297, 429)

```python
# Before
recent_6_diffs = obv_diffs.iloc[-7:-1].tolist()

# After
recent_6_diffs = [x for x in obv_diffs.iloc[-7:-1].tolist() if not pd.isna(x)]
```

**영향 범위**: `cache_single_indicators`, `warmup_ema_cache` — 단기 상장 종목의 데이터 안정성 확보

---

### 2.4 W-1: ATR=0 가드 — Warning

**문제**: ATR=0이면 `ema_atr_stop = realtime_ema20`이 되어, 정상 가격에서도 손절이 트리거될 수 있음.

**수정 파일**: `single_ema_strategy.py` (2개 위치)

**(A)** `check_immediate_sell_signal` (lines 486-489): ATR <= 0이면 HOLD 반환

```python
if atr <= 0:
    logger.warning(f"[{symbol}] ATR이 0 이하, 손절 체크 스킵")
    return {"action": "HOLD", "reasons": []}
```

**(B)** `check_second_buy_signal` 로그 (line 413): ZeroDivisionError 방지

```python
atr_ratio = f"{(curr_price-realtime_ema20)/atr:.2f}" if atr > 0 else "N/A"
```

---

### 2.5 W-3: 당일 급등 필터 추가 — Warning

**문제**: 전일(D-1 vs D-2) 급등만 필터링하고, 당일 장중 급등(D0 vs D-1)은 미체크. 장 개시 후 급등 종목에 매수 진입 가능.

**수정 파일**: `single_ema_strategy.py` (lines 314, 319)

```python
# 기존: 전일 급등 필터 (D-1 vs D-2)
surge_filtered = (prev_close is not None and ...)

# 추가: 당일 장중 급등 필터 (D0 vs D-1)
intraday_surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO

# all() 조건에 추가
current_signal = all([price_above_ema, supply_strong, surge_filtered,
                      intraday_surge_filtered, trend_upward, ema_rising, prev_day_bullish])
```

**참고**: `abs()` 사용으로 급락 종목도 필터링 (양방향 급변 차단)

---

## 3. 전략 로직 개선 (2건)

### 3.1 PEAK_PRICE(고점) 장중 추적

**문제**: `peak_price`가 EOD(장 마감)에만 갱신되어, 장중 고점이 반영되지 않음. Trailing stop 기준 가격이 부정확.

**수정 파일**: `base_trading_strategy.py`

**수정 내용**:

| 항목 | 수정 전 | 수정 후 |
|------|---------|---------|
| 갱신 시점 | EOD (1일 1회) | 5분마다 체크 (장중 실시간) |
| 데이터 소스 | 종가(close) | 장중 고가(`stck_hgpr`) |
| 변경 감지 | 미포함 | `original_peak_price` 비교 |
| DB 반영 | signal 변경 시에만 | peak_price 변경 시에도 반영 |

**구현 위치** (lines 276-282):

```python
if current_signal in (1, 2, 3):
    current_high = int(current_price_data.get("stck_hgpr", int(current_price)))
    if current_high > peak_price:
        peak_price = current_high
```

**`updated` 플래그** (line 838): peak_price 변경도 DB 업데이트 트리거에 포함

---

### 3.2 ATR 기반 동적 Trailing Stop

**문제**: 고정 임계값(5%/8%)은 종목 변동성을 반영하지 못함. 변동성이 낮은 종목은 조기 매도, 높은 종목은 늦은 매도.

**수정 파일**: `base_single_ema.py` (상수), `single_ema_strategy.py` (로직)

**새로운 상수** (`base_single_ema.py`):

| 상수 | 값 | 설명 |
|------|----|------|
| `TRAILING_STOP_ATR_PARTIAL_MULT` | 2.0 | ATR × 2.0 하락 시 1차 분할 매도 |
| `TRAILING_STOP_ATR_FULL_MULT` | 3.0 | ATR × 3.0 하락 시 2차 전량 매도 |
| `TRAILING_STOP_PARTIAL_MIN` | 3.0% | 최소 하한 (안전장치) |
| `TRAILING_STOP_FULL_MIN` | 5.0% | 최소 하한 (안전장치) |
| `TRAILING_STOP_PARTIAL` | 5.0% | 폴백 (ATR 무효 시) |
| `TRAILING_STOP_FULL` | 8.0% | 폴백 (ATR 무효 시) |

**동작 원리** (`single_ema_strategy.py` lines 558-587):

```
ATR 유효 시:
  atr_pct = (ATR / 고점가) × 100
  1차 매도 기준 = max(atr_pct × 2.0, 3.0%)
  2차 매도 기준 = max(atr_pct × 3.0, 5.0%)

ATR 무효 시 (폴백):
  1차 매도 기준 = 5.0% (고정)
  2차 매도 기준 = 8.0% (고정)
```

**예시**:

| 종목 유형 | ATR | 고점가 | atr_pct | 1차 기준 | 2차 기준 |
|-----------|-----|--------|---------|----------|----------|
| 저변동성 | 500 | 50,000 | 1.0% | 3.0% (최소) | 5.0% (최소) |
| 중변동성 | 1,500 | 50,000 | 3.0% | 6.0% | 9.0% |
| 고변동성 | 3,000 | 50,000 | 6.0% | 12.0% | 18.0% |

---

## 4. 백테스팅 동기화

**대상 파일**: `app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py`

모든 실전 전략 변경 사항을 백테스팅에 반영:

| # | 변경 사항 | 실전 | 백테스팅 |
|---|-----------|:----:|:--------:|
| 1 | PEAK_PRICE 장중 고가 사용 | `stck_hgpr` | `STCK_HGPR` |
| 2 | ATR=0 가드 | HOLD 반환 | 조건 스킵 |
| 3 | ATR 기반 동적 trailing stop | 동일 수식 | 동일 수식 |
| 4 | 당일 급등 필터 분리 | `abs(prdy_ctrt)` | `abs(daily_return)` |
| 5 | Docstring 업데이트 | 적용 | 적용 |

**공유 상수**: `BaseSingleEMAStrategy`에서 상속하므로 상수 값은 자동 동기화

---

## 5. Gap Analysis 결과

**분석일**: 2026-02-28 | **분석 도구**: gap-detector

| 이슈 | 일치율 | 상태 |
|------|:------:|:----:|
| C-1: `.decode()` 제거 | 100% | PASS |
| C-2: SIGNAL 3 손절 체크 | 100% | PASS |
| C-3: NaN 필터링 | 100% | PASS |
| W-1: ATR=0 가드 | 100% | PASS |
| W-3: 당일 급등 필터 | 100% | PASS |
| **전체 Match Rate** | **100%** | **PASS** |

**부작용**: 미발견 | **일관성**: 유지 | **엣지 케이스**: 적절히 처리됨

---

## 6. 향후 검토 사항

이번 작업 범위에는 포함되지 않았으나, 향후 검토할 수 있는 항목:

| 우선도 | 항목 | 설명 |
|:------:|------|------|
| 중 | 2차 매수 ADX 공백 구간 | ADX 23~25 사이에서 시나리오 A/B 모두 비활성 |
| 중 | SIGNAL 3 재진입 쿨다운 | 분할매도 후 즉시 재진입 시 동일 약세 구간 재진입 위험 |
| 낮 | 미사용 파라미터 활용 | `frgn_ntby_qty`, `acml_vol`, `prdy_vrss_vol_rate` 활용 가능 |
| 낮 | `intraday_low` TTL 조정 | 86400초(24시간) → 장 마감까지로 변경 검토 |
| 낮 | 캐싱 로직 중복 제거 | `service.py`의 `cache_single_indicators`와 `warmup_ema_cache` 공통화 |
| 낮 | ATR trailing stop 배수 최적화 | 백테스팅을 통한 2.0/3.0 배수 검증 필요 |

---

## 7. 결론

### 7.1 핵심 성과

- **Critical 버그 3건 수정**: `.decode()` 에러, SIGNAL 3 손절 누락, NaN 오염 — 프로덕션 안정성 확보
- **방어 로직 2건 추가**: ATR=0 가드, 당일 급등 필터 — 비정상 데이터 방어
- **매매 정확도 향상**: 장중 고점 추적, ATR 기반 동적 trailing stop — 변동성 적응형 매도 전략
- **백테스팅 동기화**: 실전/백테스팅 전략 완전 일치 — 백테스트 신뢰도 확보

### 7.2 PDCA 사이클 요약

```
[Plan] 코드 리뷰 5건 수정 계획   ✅
[Do]   구현 (6개 파일, 7건 수정)  ✅
[Check] Gap Analysis 100%       ✅
[Report] 완료 보고서             ✅ (본 문서)
```

---

---

## 8. v2.0 — 매수/매도 전략 통합 리팩토링 (2026-03-08)

### 8.1 목적

매수 시나리오 B의 전략적 효율성 개선, 전전일 데이터 의존성 제거, 백테스팅-실전 간 완전 통일.

### 8.2 매수 전략 변경

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| **시나리오 B 진입** | EMA 크로스오버 (전일 종가 < EMA → 금일 종가 > EMA) | EMA 상승 추세 (금일 EMA > 전일 EMA + 가격 > EMA) |
| **시나리오 A EMA** | 전전일 EMA 대비 비교 | 전일 EMA 대비 비교 |
| **시나리오 A ADX (백테스트)** | 하한만 (>=18) | 18~30 범위 (실전과 동일) |
| **시나리오 A +DI>-DI (백테스트)** | 체크 없음 | +DI > -DI 체크 추가 |
| **OBV 수급 조건** | 전전일 대비 (시나리오 A) | 전일 대비 |
| **전일 양봉** | 백테스트 2차 매수: 엄격(>), 실전: 체크 없음 | 통일: >= (1차/2차 공통), 실전 2차 매수에 추가 |
| **급등 필터** | 2중 (전전일→전일 + 전일→금일) | 단일: 당일 변동률 abs ≤ 5% |

### 8.3 매도 전략 변경

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| **EOD OBV 비교** | 전전일 대비 | 전일 대비 (매수 조건과 대칭) |
| **EOD 게이트 조건** | 문서: OR / 코드: AND (불일치) | 문서/코드 통일: AND |

### 8.4 캐시 데이터 정리

삭제: `prev_close`, `prev_obv_z`, `prev_adx`, `prev_ema20`, `day_before` 변수
추가: `open` (전일 시가, 양봉 판단용)

### 8.5 최종 전략 구조

```
매수:
  공통 필터: 급등 필터 (abs ≤ 5%) + 전일 양봉 (종가 >= 시가)
  시나리오 A: EMA±ATR×0.5 + OBV상승(전일대비) + ADX 18~30 + +DI>-DI + EMA상승(전일대비)
  시나리오 B: EMA상승(전일대비) + 가격>EMA(괴리≤3%) + +DI>-DI + ADX>15 + OBV>0
  2차 매수: 전일양봉 + EMA+ATR×(0.5~2.0) + ADX≥20 + +DI>-DI + OBV z≥0.5

매도:
  [1차] 즉시 손절: 가격 ≤ EMA - ATR×1.0
  [2차] EOD: DI스프레드 2일연속감소 AND OBV전일대비감소 → ATR기반 trailing stop
```

### 8.6 검증 결과

| 항목 | 결과 |
|------|------|
| 백테스팅 ↔ 실전 로직 일치 | 100% |
| 코드 ↔ README 문서 일치 | 100% |
| 매수-매도 대칭성 | 확보 |
| 불필요 캐시 데이터 제거 | 4필드 + day_before |
| 디버그 코드 제거 | 완료 |

### 8.7 수정 파일

| 파일 | 변경 유형 |
|------|-----------|
| `single_ema_backtest_strategy.py` | 매수/매도 로직, 디버그 코드 제거 |
| `single_ema_strategy.py` | 매수/매도 로직, 캐시 필드 정리 |
| `service.py` | 캐시 저장 필드 정리 (2곳) |
| `backtest/strategies/README.md` | 전략 문서 전면 갱신 |
| `trading/strategies/README.md` | 전략 문서 전면 갱신 |

---

## Version History

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|-----------|--------|
| 1.0 | 2026-02-28 | 초기 보고서 작성 (코드 리뷰 5건 수정 + ATR trailing stop) | report-generator |
| 2.0 | 2026-03-08 | 매수/매도 전략 통합 리팩토링 (EMA 상승 추세, 캐시 정리, 문서 통일) | report-generator |
