# swing-trade 공통 필터 개선 Planning Document

> **Summary**: 매수 신호 공통 필터의 전일 양봉 판단을 20EMA 상승 판단으로 교체
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-15
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

현재 `SingleEMAStrategy`의 매수 신호 공통 필터에서 전일 양봉 여부를 `yesterday_open >= yesterday_close`로 판단하고 있으나, 시가/종가 비교는 장중 변동성에 의해 부정확한 결과를 낼 수 있다. 이를 **전일 20EMA 상승 여부**(전일 EMA20 > 전전일 EMA20)로 교체하여 추세 판단의 정확성을 높인다.

### 1.2 Background

- 양봉 판단은 시가 대비 종가가 높은지만 확인하므로, 극히 작은 차이(1원)로도 양봉이 되거나 장중 큰 상승 후 음봉 마감하는 경우를 제대로 반영하지 못함
- 20EMA는 20일간의 가격 추세를 평활화한 지표로, EMA가 상승 중이라는 것은 가격이 올랐다는 의미
- 이미 시나리오 A에서 `ema_rising = (yesterday_ema20 is not None and realtime_ema20 > yesterday_ema20)`으로 **오늘 vs 어제** EMA 비교를 사용 중
- 공통 필터에서는 **어제 vs 전전일** EMA 비교가 필요 → 전전일 EMA20 데이터 캐시 추가 필요

### 1.3 Related Documents

- 전략 구현: `app/domain/swing/trading/strategies/single_ema_strategy.py`
- 데이터 적재: `app/domain/swing/service.py` (L462~486)

---

## 2. Scope

### 2.1 In Scope

- [x] 캐시 데이터에 전전일 EMA20(`prev_ema20`) 추가
- [x] 공통 필터 `prev_day_bullish` 로직 변경 (양봉 → EMA 상승)
- [x] 2차 매수 신호 판단에도 동일한 양봉 필터가 있다면 함께 수정

### 2.2 Out of Scope

- 매도 로직 변경
- 백테스트 전략 파라미터 수정
- 시나리오 A/B 내부 조건 변경

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `service.py` 캐시 적재 시 전전일 EMA20(`prev_ema20`) 값 추가 | High | Pending |
| FR-02 | `get_cached_indicators()`에서 `prev_ema20` 필드 반환 | High | Pending |
| FR-03 | 매수 공통 필터: `prev_day_bullish` → `ema20_rising` 변경 (`yesterday_ema20 > prev_ema20`) | High | Pending |
| FR-04 | 2차 매수/매도 등 다른 위치의 양봉 판단도 동일 패턴이면 함께 수정 | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 호환성 | 기존 캐시에 `prev_ema20`가 없어도 None 처리 (graceful fallback) | 코드 리뷰 |
| 성능 | 추가 Redis 비용 없음 (기존 캐시 구조에 필드 1개 추가) | - |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [x] `prev_ema20` 캐시 적재 및 조회 구현
- [x] 공통 필터 로직 변경 완료
- [x] 기존 캐시 미존재 시 fallback 처리 (None → 필터 통과 or 차단 결정)

### 4.2 Quality Criteria

- [x] 서버 정상 기동
- [x] 기존 매매 흐름에 영향 없음

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 기존 Redis 캐시에 `prev_ema20` 없음 (배포 직후) | Medium | High | `get()` + None 체크로 graceful 처리, 다음 일일 적재 시 자동 해결 |
| 전전일 EMA20가 NaN인 종목 (신규 상장 등) | Low | Low | NaN 체크 후 None 처리 |

---

## 6. Implementation Plan

### 6.1 수정 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/swing/service.py` (~L462) | 캐시 적재 시 `prev_ema20` 필드 추가 (`prev_prev['ema_20']`) |
| `app/domain/swing/trading/strategies/single_ema_strategy.py` (~L66,81) | `get_cached_indicators()`에 `prev_ema20` 반환 추가 |
| `app/domain/swing/trading/strategies/single_ema_strategy.py` (~L276,281) | 공통 필터 변수명 및 로직 변경 |

### 6.2 변경 상세

#### 1) `service.py` 캐시 적재 (L482~485 부근)

```python
# 기존: prev_plus_di, prev_minus_di, prev_obv_z만 저장
# 추가:
"prev_ema20": float(prev_prev['ema_20']) if not pd.isna(prev_prev['ema_20']) else None,
```

#### 2) `single_ema_strategy.py` 캐시 조회 (L81~83 부근)

```python
# 기존 반환 필드에 추가:
'prev_ema20': data.get('prev_ema20'),
```

#### 3) `single_ema_strategy.py` 공통 필터 (L274~281)

```python
# Before:
yesterday_open = cached_indicators.get('open')
yesterday_close = cached_indicators.get('close')
yesterday_ema20 = cached_indicators.get('ema20')
...
prev_day_bullish = (yesterday_open is not None and yesterday_close >= yesterday_open)

# After:
yesterday_ema20 = cached_indicators.get('ema20')
prev_ema20 = cached_indicators.get('prev_ema20')
...
ema20_rising = (yesterday_ema20 is not None and prev_ema20 is not None and yesterday_ema20 > prev_ema20)
```

> **Note**: `yesterday_open`, `yesterday_close`는 다른 곳에서 사용 중인지 확인 후, 미사용 시 제거

---

## 7. Next Steps

1. [ ] Design 문서 작성 (`swing-trade.design.md`)
2. [ ] 구현
3. [ ] Gap 분석

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-15 | Initial draft | 강일빈 |
