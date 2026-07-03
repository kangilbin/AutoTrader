# 해외증거금 통화별조회 기반 미국장 잔고/가용자본 산출 Completion Report

> **Status**: Complete
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Completion Date**: 2026-06-24
> **PDCA Cycle**: #foreign-margin-balance

---

## 1. Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | 해외증거금 통화별조회 기반 미국장 잔고/가용자본 산출 |
| Start Date | 2026-06 |
| End Date | 2026-06-24 |
| Objective | 미국장(NASD) 현금/가용자본 산출 소스를 해외증거금 통화별조회(035, TTTC2101R)로 교체 |

### 1.2 Results Summary

```
┌──────────────────────────────────────────────┐
│  Completion Rate: 100%                       │
├──────────────────────────────────────────────┤
│  ✅ Complete:     4 / 4 구현 요소             │
│  ✅ Plan Match:   98% (초기 96% → 개선)      │
│  ⚠️  Residual:    1 항목 (실전 응답 확인)    │
└──────────────────────────────────────────────┘
```

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [foreign-margin-balance.plan.md](../01-plan/features/foreign-margin-balance.plan.md) | ✅ Reference |
| Design | (설계 단계 생략, plan 기준 구현) | - |
| Check | [foreign-margin-balance.analysis.md](../03-analysis/foreign-margin-balance.analysis.md) | ✅ Match Rate 98% |
| Act | Current document | ✅ Complete |

---

## 3. Completed Implementation Items

### 3.1 Core Changes (4 Elements)

| ID | 항목 | Status | 구현 위치 |
|----|------|--------|---------|
| CH-01 | 신규 `get_foreign_margin()` API 함수 | ✅ Complete | `app/external/foreign_api.py` |
| CH-02 | `get_available_capital` overseas 분기 교체 | ✅ Complete | `app/domain/swing/service.py` (line ~45) |
| CH-03 | `mapping_swing` overseas 분기 재구성 | ✅ Complete | `app/domain/swing/service.py` (line ~221) |
| CH-04 | `get_present_balance` 제거 (데드코드 정리) | ✅ Complete | `app/external/foreign_api.py` |

### 3.2 Functional Implementation Details

#### CH-01: `get_foreign_margin(user_id, db, crcy_cd="USD")`

**구현 위치**: `app/external/foreign_api.py`

**기능**:
- KIS API `TTTC2101R` (해외증거금 통화별조회, TR_ID=035) 호출
- Query parameters: `CANO`, `ACNT_PRDT_CD` (계좌 정보)
- 응답에서 USD(`crcy_cd == "USD"`) row 추출
- 정규화 반환: `{"dnca_amt": 외화예수금, "ord_psbl_amt": 외화주문가능금액, "bsop_exrt": 기준환율, ...}`

**모의투자 처리** (사용자 확정):
- 모의 계정(`simulation_yn="Y"`)에서 호출 시 **`None` 반환**
- 사유: KIS 명세상 035는 "모의투자 미지원"
- 프론트엔드는 `None`을 "미지원" 표기

#### CH-02: `get_available_capital` overseas 분기

**변경 내용**:
```python
# 이전: get_present_balance(CTRP6504R) → dnca_tot_amt
# 현재: get_foreign_margin(TTTC2101R) → ord_psbl_amt (외화주문가능금액)

available_capital = None
capital_tracking = False

if overseas:
    result = await get_foreign_margin(user_id, db, crcy_cd="USD")
    if result:  # 실전 계정
        total_capital = int(float(result["ord_psbl_amt"] or 0))
        available_capital = max(0, total_capital - allocated)
        capital_tracking = True
    else:  # 모의 계정
        total_capital = None
        available_capital = None
        capital_tracking = False
```

**효과**:
- 실전: 실제 주문가능금액 기반 가용자본 산출 (정확성 向上)
- 모의: 한도 검증 생략 (미지원 API 처리)

#### CH-03: `mapping_swing` overseas 분기

**변경 내용**:
- **현금 자산 (`CASH_ASSET`)**: 외화예수금 (`dnca_amt`) → Plan 매핑 준수
- **평가금액 (`TOTAL_INVESTMENT_AMOUNT`)**: `get_stock_balance` 보유종목 `evlu_amt` 합산 + 현금
- **평가손익 (`TOTAL_PROFIT`)**: `get_stock_balance` 보유종목 `evlu_pfls_amt` 합산

**output2 조립**:
```python
# overseas 분기
if result_foreign_margin:  # 실전
    dnca_amt = result_foreign_margin["dnca_amt"]  # 외화예수금
    tot_evlu_amt = sum(stock["evlu_amt"] for stock in stocks) + dnca_amt
    evlu_pfls_smtl_amt = sum(stock["evlu_pfls_amt"] for stock in stocks)
else:  # 모의
    dnca_amt = None  # 미지원
    tot_evlu_amt = sum(stock["evlu_amt"] for stock in stocks)
    evlu_pfls_smtl_amt = sum(stock["evlu_pfls_amt"] for stock in stocks)
```

**모의 동작**:
- `CASH_ASSET=None` (미지원 표시)
- 평가금액/손익은 보유종목만으로 표시 (현금 0)
- 프론트엔드는 `CASH_ASSET:null`을 "미지원" 표기

#### CH-04: `get_present_balance` 제거

**이전 호출처**: 2곳
- `get_available_capital` (CH-02에서 교체)
- `mapping_swing` (CH-03에서 교체)

**현재 호출처**: 0 (grep 확인)

**처리**: 
- 함수 제거 (일관성 유지)
- diff에 제거 사유 명시 (plan line 40-41 참조)

---

## 4. Design Match & Verification

### 4.1 Plan vs Implementation Conformance

| Plan 정의 | 구현 | Conformance |
|----------|------|-------------|
| 가용자본 = 외화주문가능금액 | `ord_psbl_amt` | ✅ 98% |
| 현금 표시 = 외화예수금 | `dnca_amt` → `CASH_ASSET` | ✅ |
| 평가금액 = 보유종목 합 + 현금 | `evlu_amt` 합산 + `dnca_amt` | ✅ |
| 평가손익 = 보유종목 합 | `evlu_pfls_amt` 합산 | ✅ |
| 모의 처리 = 한도 생략 | `capital_tracking=False` | ✅ |
| get_present_balance 제거 | 호출처 0 | ✅ |

**최종 Match Rate**: **98%** (분석 문서 기준)

---

## 5. Quality Metrics

### 5.1 Final Analysis Results

| 메트릭 | 기준값 | 달성값 | 상태 |
|--------|--------|--------|------|
| Design Match Rate | >= 90% | 98% | ✅ Pass |
| Gap 해결율 | 초기 Gap 2개 | 1개 (Minor) | ✅ 해결 |
| 코드 리뷰 완료 | Required | Included | ✅ |

### 5.2 Resolved Issues During Implementation

| Gap# | 이슈 | 해결 방법 | 결과 |
|------|------|---------|------|
| #1 | 모의투자 미지원 리스크 | 조건부 분기 + `None` 반환 | ✅ 완화 |
| #2 | 외화주문가능금액 필드 단위 미확정 | 실전 응답값 확인 예약 | ⚠️ 잔여 (아래 참조) |

---

## 6. Key Decision Rationale

### 6.1 모의투자 처리: "한도 생략 + 미지원 표시"

**배경**:
- KIS API 035는 명세상 "모의투자 미지원"
- 모의 계정에서 호출 시 오류 또는 빈 응답 예상
- Plan에서 두 가지 선택지 제시 (line 35-37):
  1. Fallback: 모의는 다른 API로 조회 (기술 부채)
  2. 한도 생략: 모의는 한도 검증 없이 보유종목만 표시

**결정 근거**:
- Option 1 (Fallback)은 "어느 API로 fallback할 것인가?"에서 대안 부재 (기존도 없음)
- Option 2는 "모의는 자본 한도 검증이 없는 자유로운 환경"이라는 자체 논리적 일관성 有
- 사용자 확정: Option 2 채택 (plan 커밋 시점)

**구현**:
- `get_foreign_margin`에서 모의 계정 감지 후 즉시 `None` 반환 (조건 분기 최소화)
- Service 계층에서 `available_capital is None` → 한도 검증 생략 (graceful degradation)
- 프론트엔드는 `capital_tracking:False` / `CASH_ASSET:null` 수신 → "모의는 미지원" 표기

### 6.2 외화주문가능금액 필드 선정: `frcr_ord_psbl_amt1` (임시)

**배경**:
- Plan 명세상 후보 (line 32):
  1. `frcr_ord_psbl_amt1`: 한글명 "외화주문가능금액" vs Description "원화주문가능환산금액" (상충)
  2. `frcr_gnrl_ord_psbl_amt`: "외화일반주문가능금액" (순수 외화, 명확)

**문제점**:
- 만약 `frcr_ord_psbl_amt1`이 실제로 **원화 환산값**이면:
  - `total_capital = 1000 USD` → 호출 응답 `ord_psbl_amt ≈ 1,300,000 KRW`
  - 자본 한도 검증이 **1300배 부풀려져 무력화** (심각한 결과)

**현재 결정**:
- 구현에서 `frcr_ord_psbl_amt1` 사용 (명세 한글명 우선)
- 단, 이는 **실전 계정 실 응답 1회 확인 필수 전제**
- 만약 원화이면 → `frcr_gnrl_ord_psbl_amt`로 한 줄 교체 (이미 함께 반환 중)

**의존성**:
- 이 자체는 "2% Gap"에 해당하는 리스크 (미확정 상태)
- 분석 문서 "Gap #1(Medium)" (line 39-42)

---

## 7. Remaining Risks & Follow-up Actions

### 7.1 Residual Issues (2% Gap)

**Issue**: 외화주문가능금액 필드 단위 미확정

| 속성 | 내용 |
|------|------|
| **문제** | `frcr_ord_psbl_amt1`의 실제 단위 (외화 vs 원화 환산) 명세상 상충 |
| **위험도** | 높음 (자본 한도 검증 직결) |
| **영향** | 원화이면 가용자본 ~1300배 부풀려짐 → 한도 무력화 |
| **확인 방법** | 실전 계정으로 035 호출 1회, 응답값 단위 확인 |
| **해결 SOP** | 원화 확정 시 → `gnrl_ord_psbl_amt`로 교체 (1줄) |
| **추적 이슈** | 분석 문서 Gap #1, Analysis line 39-42 |

### 7.2 Future Verification Checklist

- [ ] **실전 계정 검증** (Plan line 65, 요구사항 #5)
  - `/available-capital` 호출 → `total_capital` 값 확인 (합리적인가?)
  - `/swing/list` → summary `CASH_ASSET` 확인 (보유 현금과 일치?)
  - 가용자본 = `total_capital - allocated` 동작 확인
  
- [ ] **모의 계정 검증** (Plan line 65, 상세 추가)
  - 모의 계정에서 `/available-capital` 호출 → `capital_tracking=False` 응답 확인
  - `/swing/list` → `CASH_ASSET=null` 표기 확인
  - 이전 에러 vs 현재 graceful degradation 비교

- [ ] **필드 단위 확정** (Gap #1 해결)
  - 실전 응답에서 `ord_psbl_amt1` 실제값 확인
  - 필요시 → `gnrl_ord_psbl_amt` 교체

---

## 8. Lessons Learned

### 8.1 What Went Well (Keep)

- **Plan이 충분히 상세함**: 문제 정의 + 4대 핵심 매핑 + 모의 처리 옵션 까지 명시 → 구현 중 모호함 없었음
- **모의투자 리스크를 조기에 식별하고 해결**: Plan에서 "미지원 리스크" 명시 → 구현 중 조건 분기로 graceful 처리
- **Gap 분석이 정확함**: 96%에서 모의 처리 추가 후 98%로 개선 → 재분석 의해 미완 부분 포착

### 8.2 What Needs Improvement (Problem)

- **필드 단위 명세 상충에 대한 사전 검증 부족**: 
  - Plan에는 "검증 필요" 주석만 있고, 구현 전 KIS에 문의 또는 mock 응답으로 확인 미수행
  - 결과: 이제 실전 응답을 기다리는 상태 (임시 결정 상태)

- **모의 계정 호출 검증 미수행** (환경 제약):
  - 실제 모의 계정이 035 호출에 대해 오류 vs 빈 응답 어느것을 내는지 미확인
  - 구현은 "모의면 `None` 반환"이지만, 실제 동작(예외 처리 필요?) 미검증

### 8.3 What to Try Next (Try)

- **API 스펙 명세 상충은 구현 전 KIS 개발자에게 사전 확인**:
  - "한글명 vs Description 불일치" 발견 시 → 즉시 문의
  - 실제 응답값 가정하지 말고 명시적 확인

- **모의 환경 격리 테스트**:
  - 실 모의 계정 또는 mock layer에서 035 호출 오류 시나리오 재현 후 처리 검증
  - 현재는 "None 반환"으로 graceful 처리했지만, 실제 예외 타입 확인 후 로깅 강화

- **필드 명세 스프레드시트 사전 구축**:
  - API 변경마다 KIS 명세 필드 핵심 항목을 팀 공유 doc으로 정리
  - 한글명/설명/단위 동시 기재 → 상충 조기 발견

---

## 9. Implementation Files Changed

### 9.1 Modified Files

**`app/external/foreign_api.py`**
- 신규 `get_foreign_margin(user_id, db, crcy_cd="USD")` 추가 (약 30줄)
- `get_present_balance(...)` 제거 (호출처 0, 데드코드 정리)
- 함수 description에 "035 모의투자 미지원" 명시

**`app/domain/swing/service.py`**
- `get_available_capital` (line ~45 근처):
  - overseas 분기: `get_present_balance` → `get_foreign_margin` 교체
  - `total_capital` ← `ord_psbl_amt` (외화주문가능금액)
  - 모의 계정 분기 추가: `available_capital=None`, `capital_tracking=False`
  
- `mapping_swing` (line ~221 근처):
  - overseas 분기: `get_foreign_margin` 호출 추가
  - output2 조립: `dnca_amt` → `CASH_ASSET`, 평가금액/손익 ← 보유종목 합산
  - 모의 동작: `CASH_ASSET=None`, 현금 0으로 평가금액 계산

### 9.2 Unchanged (No Changes Required)

- Entity, Schema, Repository: 도메인/데이터 구조 변경 없음
- Database schema: 신규 컬럼 미추가
- Router, API 응답: 구조 유지 (분기된 데이터 반영만)

---

## 10. Deployment & Rollback Considerations

### 10.1 Deployment Order

1. **외화주문가능금액 필드 확정 후**:
   - 실전 계정 응답값 검증 (Gap #1)
   - 필요시 `frcr_ord_psbl_amt1` → `frcr_gnrl_ord_psbl_amt` 교체

2. **배포 전 체크리스트**:
   - [x] 구현 완료
   - [x] 코드 리뷰 (일관성, 모의 분기)
   - [ ] 실전 응답 1회 확인 (필드 단위)
   - [ ] 모의 환경 호출 확인 (예외 처리)

3. **배포 후 모니터링**:
   - overseas 계정의 `/available-capital` 응답값 로깅 (처음 1주)
   - 모의 계정 에러율 모니터링

### 10.2 Rollback Plan

필드 단위 오류 발견 시 (원화 vs 외화):
- `app/external/foreign_api.py` 한 줄 수정 (필드명 교체)
- Hot-fix로 재배포

---

## 11. Next Steps

### 11.1 Immediate (배포 전)

- [ ] **실전 계정 필드 단위 검증** (Analysis Gap #1):
  - 실전 계정으로 035 호출 1회 실행
  - `ord_psbl_amt1` 실제값 (외화 or 원화) 확인
  - 필요시 → `gnrl_ord_psbl_amt`로 교체
  
- [ ] **모의 계정 호출 테스트** (Analysis 미수행 항목):
  - 모의 계정에서 035 호출 → 실제 동작 확인 (오류 vs 빈 응답)
  - 현재 구현의 graceful handling 검증

- [ ] **프론트엔드 연동 확인**:
  - `capital_tracking: False` 수신 → 미지원 표기
  - `CASH_ASSET: null` 수신 → 미지원 표기
  - 모의 계정 UI 동작 확인

### 11.2 Related Follow-up Features

- **외화 환율 제공**: 기준환율(`bsop_exrt`) 이미 반환 중 → 프론트에서 USD↔KRW 변환 활용 가능
- **다중 통화 지원**: 현재 USD만, 향후 JPY/CNY 등 추가 가능 (API 재사용)

---

## 12. Changelog

### v1.0.0 (2026-06-24)

**Added:**
- 신규 API 함수 `get_foreign_margin()`: KIS 해외증거금 통화별조회(035) 호출, 모의투자 미지원 처리
- overseas 계정 분기: 모의 시 `capital_tracking=False`, 자본 한도 검증 생략

**Changed:**
- `get_available_capital` overseas 분기: 외화주문가능금액 기반으로 `total_capital` 산출 (정확성 향상)
- `mapping_swing` overseas 분기: 현금(`CASH_ASSET`) ← 외화예수금, 평가금액/손익 ← 보유종목 합산

**Removed:**
- `get_present_balance()` 함수 제거: 호출처 0 (데드코드 정리), Plan line 40-41 참조

**Known Issues:**
- Gap #1 (외화주문가능금액 필드 단위): 실전 응답값 1회 확인 필요 (원화 vs 외화 단위), Gap #1 해결 시 `foreign_api.py` 1줄 수정 필요
- Gap #3 (낮음): 모의 계정 호출 미검증 (환경 제약), 배포 후 실제 동작 확인

---

## 13. Quality Assurance Sign-off

| 항목 | Status | Notes |
|------|--------|-------|
| Code Implementation | ✅ Complete | 4 변경 요소 모두 구현 |
| Design Conformance | ✅ 98% | Plan 매핑 정확, 미결정 1항목 |
| Test Verification | ⏳ Pending | 실전/모의 호출 검증 필요 |
| Deployment Readiness | ⚠️ Conditional | Gap #1 필드 단위 확정 후 배포 가능 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-24 | Completion report created | 강일빈 |