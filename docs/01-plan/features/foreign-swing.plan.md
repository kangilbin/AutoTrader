# Plan: 해외 스윙 매매 완성 (foreign-swing)

> **Summary**: 스윙 등록 시 시장 구분 코드(MRKT_CODE) 기반으로 국내(kis_api)/해외(foreign_api) API를 자동 분기하여 해외 종목 스윙 매매를 완전히 지원
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-11
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

현재 스윙 등록(`POST /swing`)은 `MRKT_CODE`를 받아 DB에 저장하지만, 등록 시점의 백그라운드 데이터 적재(`stock_data_batch.py`)가 국내 API(`kis_api.get_stock_data`)만 사용한다. 해외 종목(NASD) 등록 시 `foreign_api`를 통해 데이터를 가져오도록 분기를 완성하고, 관련된 나머지 갭을 해소한다.

### 1.2 Background

`foreign-stock` 피처(완료, 99%)에서 해외 주식 API 통합의 대부분이 구현됨:
- `us_trade_job`, `us_day_collect_job`, `us_ema_cache_warmup_job` 배치 완성
- `process_single_swing`에서 `_overseas` 플래그로 API 분기 완료
- `mapping_swing`에서 잔고 조회 API 분기 완료
- `order_executor.py`에서 주문 API 분기 완료

**남은 갭**: 스윙 등록 시점의 초기 데이터 적재 경로와 일부 보조 로직에서 해외 API 분기가 누락됨.

### 1.3 Related Documents

- Plan: `docs/01-plan/features/foreign-stock.plan.md` (완료된 해외 주식 API 지원)
- 참조 코드: `app/domain/swing/router.py#L22-30` (스윙 등록 엔드포인트)

---

## 2. Scope

### 2.1 In Scope

- [ ] `stock_data_batch.py`의 `fetch_and_store_3_years_data()`에 해외 API 분기 추가
- [ ] 해외 종목 데이터 필드 매핑 정규화 (foreign_api 응답 → DB 스키마)
- [ ] `day_collect_job`/`us_day_collect_job` 데이터 수집 분기 검증
- [ ] 스윙 등록 → 데이터 적재 → 지표 캐싱 E2E 흐름 해외 종목 검증

### 2.2 Out of Scope

- NYSE, AMEX 거래소 지원 (현재 NASD만)
- 프리마켓/애프터마켓 매매
- 환율 연동 손익 계산
- 해외 주식 전용 전략 (국내와 동일 전략 공유)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `fetch_and_store_3_years_data()`에서 MRKT_CODE가 NASD일 때 `foreign_api.get_stock_data()` 호출 | High | Pending |
| FR-02 | foreign_api 일별 시세 응답 필드를 DB 스키마(STCK_OPRC, STCK_HGPR 등)로 매핑 | High | Pending |
| FR-03 | `us_day_collect_job`에서 해외 종목 당일 OHLCV 데이터 정상 수집 검증 | Medium | Pending |
| FR-04 | 스윙 등록(NASD) → 3년 데이터 적재 → 지표 캐싱 → 배치 매매 전체 흐름 동작 확인 | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 호환성 | 기존 국내 스윙 흐름에 영향 없음 | 국내 스윙 등록/매매 정상 동작 확인 |
| 데이터 정합성 | 해외 OHLCV 데이터가 DB 스키마에 맞게 저장 | STOCK_DAY_HISTORY 테이블 조회 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] NASD 종목 스윙 등록 시 `foreign_api`로 3년치 데이터 적재
- [ ] 적재된 데이터로 지표 캐싱(Redis) 정상 완료
- [ ] `us_trade_job` 배치에서 해당 종목 매매 신호 판단 정상 동작
- [ ] 기존 국내 스윙 흐름 회귀 없음

---

## 5. 현재 코드 분석 (Gap 식별)

### 5.1 이미 완성된 부분

| 영역 | 파일 | 상태 |
|------|------|------|
| 스윙 등록 API | `swing/router.py`, `swing/service.py` | MRKT_CODE 수신 및 엔티티 생성 |
| 엔티티 검증 | `swing/entity.py` | `VALID_MRKT_CODES = ('J', 'NX', 'UN', 'NASD')` |
| 배치 매매 분기 | `auto_swing_batch.py` | `us_trade_job()` + `process_single_swing()` 내 `_overseas` 분기 |
| 주문 실행 분기 | `order_executor.py` | `foreign_api.place_order_api()` 분기 완료 |
| 잔고 조회 분기 | `swing/service.py:mapping_swing()` | `NASD → foreign_api.get_stock_balance()` |
| 지표 캐시 워밍업 | `auto_swing_batch.py` | `us_ema_cache_warmup_job()` 완료 |
| 스케줄러 | `scheduler.py` | 미국 장 시간대 전체 스케줄 등록 완료 |

### 5.2 미완성 Gap

| Gap | 파일 | 문제 | 해결 방안 |
|-----|------|------|-----------|
| **G-01** | `stock_data_batch.py:59` | `get_stock_data`가 `kis_api`에서만 import — NASD 종목도 국내 API로 호출 | `mrkt_code` 기반 분기: NASD이면 `foreign_api.get_stock_data()` 호출 |
| **G-02** | `stock_data_batch.py:96-107` | 응답 필드 매핑이 국내 전용 (`STCK_OPRC`, `STCK_HGPR` 등) | foreign_api 응답 필드 확인 후 매핑 함수 추가 또는 foreign_api 내부에서 정규화 |
| **G-03** | `auto_swing_batch.py:815` | `us_day_collect_job` 내부 데이터 수집 로직 검증 필요 | foreign_api 사용 여부 및 필드 매핑 확인 |

---

## 6. 구현 계획

### 6.1 API 분기 패턴

```
스윙 등록 (POST /swing, MRKT_CODE=NASD)
    ↓
SwingService.create_swing()
    ↓ stock_info.DATA_YN != 'Y'
    ↓
_fetch_and_cache() → fetch_and_store_3_years_data()
    ↓ mrkt_code 확인
    ↓
┌──────────────────────────────────────┐
│ NASD         → foreign_api.get_stock_data()  │
│ J / NX / UN  → kis_api.get_stock_data()      │
└──────────────────────────────────────┘
    ↓
DB 저장 (STOCK_DAY_HISTORY)
    ↓
cache_single_indicators() → Redis 저장
```

### 6.2 구현 순서

| 단계 | 작업 | 파일 | 설명 |
|------|------|------|------|
| 1 | `fetch_and_store_3_years_data` API 분기 | `stock_data_batch.py` | `mrkt_code == "NASD"`이면 `foreign_api.get_stock_data()` 호출 |
| 2 | 해외 응답 필드 매핑 정규화 | `stock_data_batch.py` 또는 `foreign_api.py` | 응답을 국내와 동일한 키(`STCK_OPRC` 등)로 정규화 |
| 3 | `us_day_collect_job` 분기 검증/수정 | `auto_swing_batch.py` | 일일 데이터 수집에서 해외 API 사용 확인 |
| 4 | E2E 테스트 | - | NASD 종목 등록 → 데이터 적재 → 캐싱 → 배치 매매 |

---

## 7. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| foreign_api 일별 시세 응답 필드명이 국내와 다름 | 데이터 매핑 오류로 지표 계산 실패 | High | foreign_api 응답 구조 사전 확인, 정규화 레이어 추가 |
| 3년치 해외 데이터 요청 시 API Rate Limit | 데이터 적재 실패 | Medium | 세마포어(3)로 동시 요청 제한 (기존 로직 유지) |
| 기존 국내 흐름 회귀 | 국내 매매 중단 | Low | 분기 로직만 추가, 기존 코드 변경 최소화 |

---

## 8. Architecture Considerations

### 8.1 Project Level

| Level | Selected |
|-------|:--------:|
| **Enterprise** (DDD Lite + Layered) | O |

기존 아키텍처 패턴 유지. 변경 범위가 작아 새로운 아키텍처 결정 불필요.

### 8.2 Key Decision

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| 응답 필드 정규화 위치 | A) `stock_data_batch.py`에서 매핑 / B) `foreign_api.py` 내부에서 정규화 | 코드 분석 후 결정 | foreign_api가 이미 정규화하고 있다면 A 불필요 |

---

## 9. Convention Prerequisites

### 9.1 Existing Conventions (CLAUDE.md)

- [x] DDD Lite + Layered Architecture
- [x] Repository: flush만, Service: commit
- [x] Entity에서 비즈니스 검증
- [x] DB 컬럼: UPPER_SNAKE_CASE

---

## 10. Next Steps

1. [ ] Design 문서 작성 (`foreign-swing.design.md`)
2. [ ] 구현 (G-01 ~ G-03 해소)
3. [ ] Gap Analysis

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-11 | Initial draft | 강일빈 |
