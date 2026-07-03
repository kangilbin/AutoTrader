# Plan: swing-reg (스윙 등록 자본 한도 검증)

## 1. 개요

스윙 종목 등록 시 사용자의 보유 자본을 초과하는 금액으로 등록할 수 없도록 검증 로직을 추가한다.

### 현재 문제
- `create_swing`에 INIT_AMOUNT에 대한 자본 한도 검증이 없음
- 사용자가 보유 자본 3,000만원인데 스윙 합계 5,000만원으로 등록 가능
- `update_swing`에서 INIT_AMOUNT 변경 시에도 동일한 문제 존재

### 목표
- **등록 시**: INIT_AMOUNT ≤ (보유 자본 - 기존 스윙 INIT_AMOUNT 합계)
- **수정 시**: 변경된 INIT_AMOUNT도 동일 규칙 적용

## 2. 자본 산출 기준

### 보유 자본 = `get_stock_balance` → output2.`dnca_tot_amt` (예수금총액)

> `mapping_swing` (service.py:250)에서 이미 `CASH_ASSET`으로 사용 중인 필드

### 기존 할당 금액 = 해당 계좌의 SWING_TRADE.INIT_AMOUNT 합계
- `USE_YN` 무관하게 **모든 등록된 스윙**의 INIT_AMOUNT 합산
- 비활성(USE_YN='N')이라도 자본은 이미 할당된 것으로 간주

### 가용 자본 계산
```
가용 자본 = 예수금총액(dnca_tot_amt) + 기존 스윙 INIT_AMOUNT 합계 - 기존 스윙 INIT_AMOUNT 합계
```

> **주의**: `dnca_tot_amt`는 주식 매수에 사용되지 않은 순수 현금이다. 
> 하지만 스윙에 등록된 금액이 아직 매수 전이라면 예수금에 포함되어 있다.
> 따라서 정확한 총 자본은:
> ```
> 총 자본 = 예수금(dnca_tot_amt) + 주식평가금액(scts_evlu_amt)
> 가용 자본 = 총 자본 - 기존 스윙 INIT_AMOUNT 합계
> ```

## 3. 백엔드 vs 프론트엔드 역할 분담

### 백엔드 (필수 - 데이터 무결성 보장)

| 항목 | 설명 |
|------|------|
| **검증 위치** | `SwingService.create_swing()`, `SwingService.update_swing()` |
| **검증 로직** | INIT_AMOUNT ≤ 가용 자본 |
| **실패 시** | `BusinessRuleError` 발생 (400) |
| **이유** | API 직접 호출 방어, 동시성 안전 |

### 프론트엔드 (UX 보조 - 사전 검증)

프론트엔드에서 처리해야 할 부분:

| 항목 | 설명 |
|------|------|
| **가용 자본 표시** | 스윙 등록 폼에 "등록 가능 금액: X원" 표시 |
| **실시간 검증** | INIT_AMOUNT 입력 시 가용 자본 초과 여부 즉시 피드백 |
| **데이터 소스** | `GET /swing/list` 응답의 `summary.CASH_ASSET` + 스윙 목록 활용 |
| **계산 방법** | `가용 자본 = summary에서 제공하는 총 자본 - 스윙 리스트 INIT_AMOUNT 합계` |
| **초과 시 UI** | 등록 버튼 비활성화 + "보유 자본을 초과합니다" 경고 메시지 |

#### 프론트엔드 구현 상세

1. **스윙 등록 페이지 진입 시**
   - `GET /swing/available-capital?account_no=XXX` 호출 (신규 API)
   - 응답: `{ available_capital: 10000000, total_capital: 30000000, allocated: 20000000 }`

2. **INIT_AMOUNT 입력 필드**
   - 가용 자본 초과 입력 시 빨간색 경고 + 등록 버튼 disabled
   - 가용 자본 이하 입력 시 정상 표시

3. **스윙 수정 페이지**
   - 기존 INIT_AMOUNT는 가용 자본 계산에서 제외 (자기 자신 금액은 차감)
   - 변경 시 동일한 실시간 검증 적용

## 4. 구현 계획

### 4.1 Repository 변경 (`repository.py`)

**신규 메서드**: `get_total_init_amount(account_no: str) -> Decimal`
- 해당 계좌의 모든 SWING_TRADE.INIT_AMOUNT 합계 조회
- 특정 swing_id 제외 옵션 (수정 시 자기 자신 제외)

### 4.2 Service 변경 (`service.py`)

**신규 메서드**: `get_available_capital(user_id: str, account_no: str, exclude_swing_id: int = None) -> dict`
- `get_stock_balance` 호출 → 총 자본 산출
- `repo.get_total_init_amount` 호출 → 기존 할당 합계
- 가용 자본 = 총 자본 - 기존 할당 합계
- 반환: `{ total_capital, allocated, available_capital }`

**`create_swing` 수정**:
- 등록 전 `get_available_capital` 호출
- `INIT_AMOUNT > available_capital` → `BusinessRuleError` 발생

**`update_swing` 수정**:
- INIT_AMOUNT 변경 시 `get_available_capital(exclude_swing_id=swing_id)` 호출
- 동일 검증 적용

### 4.3 Router 변경 (`router.py`)

**신규 엔드포인트**: `GET /swing/available-capital`
- Query: `account_no` (필수)
- 응답: 총 자본, 할당 금액, 가용 자본
- 용도: 프론트엔드에서 등록 폼 진입 시 호출

### 4.4 에러 응답

```json
{
  "success": false,
  "error_code": "BUSINESS_RULE_VIOLATION",
  "message": "투자 가능 금액을 초과했습니다. 가용 자본: 10,000,000원, 요청 금액: 15,000,000원",
  "detail": {
    "available_capital": 10000000,
    "requested_amount": 15000000,
    "total_capital": 30000000,
    "allocated": 20000000
  }
}
```

## 5. 구현 순서

1. `SwingRepository.get_total_init_amount()` 추가
2. `SwingService.get_available_capital()` 추가  
3. `SwingService.create_swing()` 검증 로직 추가
4. `SwingService.update_swing()` 검증 로직 추가
5. `GET /swing/available-capital` 엔드포인트 추가

## 6. 고려사항

### 시장 분리
- 국내(J, NX, UN)와 해외(NASD)는 별도 계좌/자본이므로, `get_stock_balance`와 `foreign_api.get_stock_balance`를 MRKT_CODE에 따라 분기 필요
- INIT_AMOUNT 합계도 시장별로 분리 조회

### KIS API 호출 비용
- 등록/수정마다 KIS API를 호출하므로 레이턴시 증가 (~200-500ms)
- 캐싱은 실시간 자본 변동이 있어 적용하기 어려움
- 프론트엔드 사전 검증으로 불필요한 호출 최소화

### 동시성
- DB 트랜잭션 내에서 합계 조회 → 검증 → 저장이 순차 실행되므로 기본적 안전성 확보
- 극단적 동시 등록은 UniqueConstraint로 방어 (같은 종목 중복 등록 불가)
