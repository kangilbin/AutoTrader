# stock-data Planning Document

> **Summary**: 장 운영 시간 중 3년치 데이터 적재 시 금일(당일) 데이터 제외 처리
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-15
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

스윙 등록/활성화 시 실행되는 3년치 데이터 적재(`fetch_and_store_3_years_data`)에서 **장 운영 중일 때 금일 데이터가 포함되는 문제**를 해결한다.

### 1.2 Background

**현재 동작:**
- `stock_data_batch.py:38`에서 `end_date = datetime.now().date()`로 설정 → 오늘 날짜까지 포함하여 API 호출
- 장 운영 중에 스윙을 등록하면 **변동성 있는 당일 미확정 OHLCV 데이터**가 `STOCK_DAY_HISTORY`에 적재됨

**문제점:**
- 자동 스윙매매 알고리즘이 `기존 적재 데이터 + 실시간 현재가`를 증분 계산하여 기술 지표(EMA, ADX 등)를 산출
- 미확정 당일 데이터가 이미 적재되어 있으면, **장중 실시간 데이터와 중복/충돌** → 지표 계산 왜곡
- `day_collect_job`(15:35 KST) / `us_day_collect_job`(16:35 ET)이 장 마감 후 확정 데이터를 별도 수집하므로, 초기 적재 시 당일 데이터는 불필요

**영향 범위:**
- `service.py:72-74` — 스윙 등록 시 백그라운드 적재
- `service.py:128-130` — 스윙 활성화(USE_YN=Y) 시 백그라운드 적재
- 국내장(J) + 미국장(NASD) 모두 해당

### 1.3 Related Documents

- 스케줄러: `app/common/scheduler.py`
- 데이터 배치: `app/domain/stock/stock_data_batch.py`
- 스윙 서비스: `app/domain/swing/service.py`
- 일일 수집: `auto_swing_batch.py` (`day_collect_job`, `us_day_collect_job`)

---

## 2. Scope

### 2.1 In Scope

- [x] 장 운영 시간 판별 유틸리티 구현 (국내/미국)
- [x] `fetch_and_store_3_years_data`에서 장중이면 `end_date`를 전일(어제)로 설정
- [x] 장 마감 후에는 기존대로 금일까지 적재 허용

### 2.2 Out of Scope

- `day_collect_job` / `us_day_collect_job` 로직 변경 (이미 장 마감 후 실행됨)
- 스윙 매매 알고리즘 자체 수정
- 공휴일/휴장일 캘린더 연동 (향후 개선 사항)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 국내 장 운영 시간(08:00~15:35 KST) 판별 함수 구현 | High | Pending |
| FR-02 | 미국 장 운영 시간(09:00~16:35 ET) 판별 함수 구현 (서머타임 자동 반영) | High | Pending |
| FR-03 | `fetch_and_store_3_years_data`에서 장중이면 `end_date`를 전일로 조정 | High | Pending |
| FR-04 | 장 마감 후 적재 시에는 금일 데이터 포함 (기존 동작 유지) | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 정확성 | 장 운영 시간 판별이 서머타임 전환에도 정확할 것 | America/New_York 타임존 사용 검증 |
| 호환성 | 기존 스케줄러 및 day_collect_job과 충돌 없을 것 | 통합 테스트 |
| 단순성 | 최소 코드 변경으로 구현 | 변경 파일 수 최소화 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [x] 장중 스윙 등록 시 당일 미확정 데이터가 적재되지 않음
- [x] 장 마감 후 스윙 등록 시 당일 확정 데이터가 정상 적재됨
- [x] 국내장/미국장 모두 정상 동작
- [x] 기존 `day_collect_job` 동작에 영향 없음

### 4.2 Quality Criteria

- [x] 서머타임 전환 시에도 정확한 시간 판별 (America/New_York 타임존 활용)
- [x] 기존 코드 최소 변경

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 장 마감 직후 시간대(15:30~15:35)에 판별 오류 | Medium | Low | 마감 시간에 버퍼(+35분) 적용하여 day_collect_job 실행 시점까지 포함 |
| 미국 서머타임 전환 시 오판 | High | Low | `pytz`/`zoneinfo`의 `America/New_York` 사용으로 자동 반영 |
| 주말/공휴일에 불필요한 판별 로직 실행 | Low | Medium | 주말은 장 마감 상태로 처리 (금일 포함 적재 허용 - 어차피 데이터 없음) |

---

## 6. Architecture Considerations

### 6.1 구현 전략

**변경 대상 파일:**

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/stock/stock_data_batch.py` | `end_date` 결정 시 장 운영 여부 확인 로직 추가 |

**장 운영 시간 판별 로직:**

```
시장별 판별 기준:
┌──────────────────────────────────────────────────┐
│ 국내(J):                                          │
│   장중: 평일 08:00 ~ 15:35 KST                    │
│   → end_date = 어제 (전일)                         │
│   장마감: 그 외 시간                                │
│   → end_date = 오늘 (기존 동작)                     │
├──────────────────────────────────────────────────┤
│ 미국(NASD):                                       │
│   장중: 평일 09:00 ~ 16:35 ET (서머타임 자동 반영)   │
│   → end_date = 어제 (전일)                         │
│   장마감: 그 외 시간                                │
│   → end_date = 오늘 (기존 동작)                     │
└──────────────────────────────────────────────────┘
```

### 6.2 기존 아키텍처 준수

- **DDD Lite 계층 유지**: 유틸리티 성격의 로직을 `stock_data_batch.py` 내부에 배치
- **비동기 일관성**: 기존 async 패턴 유지
- **기존 타임존 패턴 활용**: `scheduler.py`에서 이미 `America/New_York` 타임존 사용 중 → 동일 패턴 적용

---

## 7. Convention Prerequisites

### 7.1 Existing Project Conventions

- [x] `CLAUDE.md` has coding conventions section
- [x] 네이밍 컨벤션: snake_case 함수명
- [x] 예외 처리 표준화 적용
- [x] 비동기 일관성 준수

### 7.2 추가 환경 변수

없음 (기존 환경 변수만 사용)

---

## 8. Next Steps

1. [x] Design 문서 작성 (`stock-data.design.md`)
2. [x] 구현
3. [x] Gap 분석

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-15 | Initial draft | 강일빈 |
