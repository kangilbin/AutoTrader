# swing-reg Design Document

> **Summary**: 스윙 등록/수정 시 보유 자본 한도 검증 — 가용 자본 초과 등록 방지
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-15
> **Status**: Draft
> **Planning Doc**: [swing-reg.plan.md](../../01-plan/features/swing-reg.plan.md)

---

## 1. Overview

### 1.1 Design Goals

스윙 등록(`create_swing`) 및 수정(`update_swing`) 시 사용자의 보유 자본을 초과하는 INIT_AMOUNT 설정을 차단한다. 프론트엔드 UX 보조를 위한 가용 자본 조회 API도 추가한다.

### 1.2 Design Principles

- **백엔드 필수 검증**: 데이터 무결성은 서버에서 보장
- **기존 패턴 준수**: `get_stock_balance` / `foreign_api.get_stock_balance` 분기 패턴 활용
- **최소 변경**: 기존 `create_swing`, `update_swing` 흐름에 검증 단계만 삽입

---

## 2. Architecture

### 2.1 현재 흐름 (문제)

```
POST /swing { INIT_AMOUNT: 50,000,000 }
    ↓
SwingService.create_swing()
    ↓ 검증 없음
SwingTrade.create()  ← 어떤 금액이든 통과
    ↓
repo.save() → commit
```

### 2.2 변경 후 흐름

```
POST /swing { INIT_AMOUNT: 50,000,000, MRKT_CODE: "J", ACCOUNT_NO: "XXX" }
    ↓
SwingService.create_swing()
    ↓
┌─────────────────────────────────────────────────┐
│ 1. get_available_capital(user_id, account_no,   │
│    mrkt_code)                                   │
│    ├─ KIS API → 총 자본 조회                      │
│    └─ DB → 기존 할당 INIT_AMOUNT 합계              │
│                                                 │
│ 2. INIT_AMOUNT > available_capital?             │
│    ├─ Yes → BusinessRuleError (400)             │
│    └─ No  → 계속 진행                             │
└─────────────────────────────────────────────────┘
    ↓ (통과)
SwingTrade.create()
    ↓
repo.save() → commit
```

### 2.3 가용 자본 조회 흐름 (신규 API)

```
GET /swing/available-capital?account_no=XXX&mrkt_code=J
    ↓
SwingService.get_available_capital()
    ├─ MRKT_CODE == "NASD" → foreign_api.get_stock_balance()
    └─ else               → kis_api.get_stock_balance()
    ↓
output2에서 총 자본 산출
    ↓
DB에서 기존 할당 합계 조회
    ↓
Response: { total_capital, allocated, available_capital }
```

---

## 3. Detailed Design

### 3.1 자본 산출 공식

```
총 자본     = dnca_tot_amt(예수금) + scts_evlu_amt(주식평가금액)
기존 할당   = SUM(SWING_TRADE.INIT_AMOUNT) WHERE ACCOUNT_NO = ? AND 시장 분류 일치
가용 자본   = 총 자본 - 기존 할당
```

**시장 분류 기준**:
| MRKT_CODE | 분류 | balance API | INIT_AMOUNT 합계 필터 |
|-----------|------|------------|----------------------|
| J, NX, UN | 국내 | `kis_api.get_stock_balance()` | `MRKT_CODE != 'NASD'` |
| NASD | 해외 | `foreign_api.get_stock_balance()` | `MRKT_CODE == 'NASD'` |

### 3.2 Repository 변경

**파일**: `app/domain/swing/repository.py`

**신규 메서드**:

```python
async def get_total_init_amount(
    self,
    account_no: str,
    overseas: bool = False,
    exclude_swing_id: int = None
) -> Decimal:
    """
    계좌별 INIT_AMOUNT 합계 조회

    Args:
        account_no: 계좌번호
        overseas: True면 NASD만, False면 NASD 제외
        exclude_swing_id: 제외할 스윙 ID (수정 시 자기 자신 제외)

    Returns:
        INIT_AMOUNT 합계 (없으면 Decimal(0))
    """
```

**SQL 로직**:
```sql
SELECT COALESCE(SUM(INIT_AMOUNT), 0)
FROM SWING_TRADE
WHERE ACCOUNT_NO = :account_no
  AND (MRKT_CODE = 'NASD')  -- overseas=True인 경우
  -- 또는 AND (MRKT_CODE != 'NASD')  -- overseas=False인 경우
  AND (:exclude_swing_id IS NULL OR SWING_ID != :exclude_swing_id)
```

### 3.3 Service 변경

**파일**: `app/domain/swing/service.py`

#### 3.3.1 신규 메서드: `get_available_capital`

```python
async def get_available_capital(
    self,
    user_id: str,
    account_no: str,
    mrkt_code: str,
    exclude_swing_id: int = None
) -> dict:
    """
    가용 자본 조회

    Args:
        user_id: 사용자 ID
        account_no: 계좌번호
        mrkt_code: 시장코드 (시장 분류 판단용)
        exclude_swing_id: 제외할 스윙 ID (수정 시)

    Returns:
        {
            "total_capital": int,      # 총 자본 (예수금 + 주식평가)
            "allocated": int,          # 기존 할당 합계
            "available_capital": int   # 가용 자본
        }
    """
```

**로직**:
1. `overseas = mrkt_code == "NASD"` 판단
2. 국내/해외에 따라 `get_stock_balance` 또는 `foreign_api.get_stock_balance` 호출
3. `output2`에서 총 자본 산출:
   - 국내: `int(output2["dnca_tot_amt"]) + int(output2["scts_evlu_amt"])`
   - 해외: `foreign_api` 응답의 동등 필드 사용
4. `repo.get_total_init_amount(account_no, overseas, exclude_swing_id)` 호출
5. `available_capital = total_capital - allocated` 계산
6. dict 반환

#### 3.3.2 `create_swing` 수정

기존 코드 37~86행에서 `SwingTrade.create()` **이전에** 검증 삽입:

```python
async def create_swing(self, user_id: str, request: SwingCreateRequest) -> dict:
    try:
        # ★ 신규: 자본 한도 검증
        capital_info = await self.get_available_capital(
            user_id, request.ACCOUNT_NO, request.MRKT_CODE
        )
        if request.INIT_AMOUNT > capital_info["available_capital"]:
            raise BusinessRuleError(
                f"투자 가능 금액을 초과했습니다. "
                f"가용 자본: {capital_info['available_capital']:,}원, "
                f"요청 금액: {request.INIT_AMOUNT:,}원",
                rule="CAPITAL_LIMIT_EXCEEDED",
                detail={
                    "available_capital": capital_info["available_capital"],
                    "requested_amount": request.INIT_AMOUNT,
                    "total_capital": capital_info["total_capital"],
                    "allocated": capital_info["allocated"]
                }
            )

        # 기존 로직 유지
        swing = SwingTrade.create(...)
        ...
```

#### 3.3.3 `update_swing` 수정

기존 코드 107~137행에서 `INIT_AMOUNT` 변경 감지 시 검증 삽입:

```python
async def update_swing(self, swing_id: int, data: dict, user_id: str = None) -> dict:
    try:
        swing = await self.repo.find_by_id(swing_id)
        if not swing:
            raise NotFoundError("스윙 전략", swing_id)

        # ★ 신규: INIT_AMOUNT 변경 시 자본 한도 검증
        if "INIT_AMOUNT" in data and user_id:
            capital_info = await self.get_available_capital(
                user_id, swing.ACCOUNT_NO, swing.MRKT_CODE,
                exclude_swing_id=swing_id  # 자기 자신 제외
            )
            if data["INIT_AMOUNT"] > capital_info["available_capital"]:
                raise BusinessRuleError(
                    f"투자 가능 금액을 초과했습니다. "
                    f"가용 자본: {capital_info['available_capital']:,}원, "
                    f"요청 금액: {data['INIT_AMOUNT']:,}원",
                    rule="CAPITAL_LIMIT_EXCEEDED",
                    detail={...}
                )

        # 기존 INIT_AMOUNT 차액 반영 로직 유지
        if "INIT_AMOUNT" in data:
            diff = Decimal(data["INIT_AMOUNT"]) - swing.INIT_AMOUNT
            data["CUR_AMOUNT"] = swing.CUR_AMOUNT + diff
        ...
```

### 3.4 Router 변경

**파일**: `app/domain/swing/router.py`

**신규 엔드포인트**:

```python
@router.get("/available-capital")
async def get_available_capital(
    account_no: str = Query(..., description="계좌번호"),
    mrkt_code: str = Query("J", description="시장코드 (J:국내, NASD:해외)"),
    service: Annotated[SwingService, Depends(get_swing_service)] = None,
    user_id: Annotated[str, Depends(get_current_user)] = None
):
    """가용 자본 조회 (프론트엔드 UX용)"""
    result = await service.get_available_capital(user_id, account_no, mrkt_code)
    return success_response("가용 자본 조회 완료", result)
```

> **주의**: `/available-capital`은 `/swing/{swing_id}` 패턴보다 **먼저** 등록해야 경로 충돌 방지. 기존 `list_swing_mapping`(`/list`) 다음에 배치.

### 3.5 에러 응답 스펙

**HTTP 400 — 자본 한도 초과**:

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

---

## 4. Data Design

### 4.1 기존 테이블 변경

없음. 기존 `SWING_TRADE.INIT_AMOUNT` 컬럼을 그대로 활용.

### 4.2 KIS API output2 필드 매핑

| output2 필드 | 설명 | 용도 |
|-------------|------|------|
| `dnca_tot_amt` | 예수금총액 | 현금 자산 |
| `scts_evlu_amt` | 주식평가금액 | 주식 자산 |
| `tot_evlu_amt` | 총평가금액 | 참고용 (= dnca + scts + ...) |

**총 자본 계산**: `dnca_tot_amt + scts_evlu_amt`

> `tot_evlu_amt`는 신용/대출 등 포함 가능하므로 직접 합산이 더 정확

---

## 5. API Specification

### 5.1 GET /swing/available-capital

**Request**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| account_no | string | Yes | 계좌번호 |
| mrkt_code | string | No (default: "J") | 시장코드 |

**Response** (200):
```json
{
  "success": true,
  "message": "가용 자본 조회 완료",
  "data": {
    "total_capital": 30000000,
    "allocated": 20000000,
    "available_capital": 10000000
  }
}
```

### 5.2 POST /swing (기존 — 에러 케이스 추가)

**기존 동작**: 변경 없음

**추가 에러 응답** (400):
- 조건: `INIT_AMOUNT > available_capital`
- 에러 코드: `BUSINESS_RULE_VIOLATION`
- detail에 `available_capital`, `requested_amount`, `total_capital`, `allocated` 포함

### 5.3 PUT /swing/{swing_id}/settings (기존 — 에러 케이스 추가)

**기존 동작**: 변경 없음

**추가 에러 응답** (400):
- 조건: `INIT_AMOUNT` 필드 변경 시 새 금액 > available_capital
- 동일 에러 형식 (exclude_swing_id로 자기 자신 제외 후 계산)

---

## 6. Frontend Guide (프론트엔드 전달 사항)

### 6.1 스윙 등록 페이지

1. **페이지 진입 시** `GET /swing/available-capital?account_no=XXX&mrkt_code=J` 호출
2. 응답의 `available_capital`을 "등록 가능 금액" 으로 표시
3. INIT_AMOUNT 입력 필드에 실시간 검증:
   - `입력값 > available_capital` → 빨간색 경고 텍스트 + 등록 버튼 disabled
   - 경고 메시지 예: "보유 자본을 초과합니다 (등록 가능: 10,000,000원)"
4. 정상 범위 입력 시 등록 버튼 활성화

### 6.2 스윙 수정 페이지

1. 수정 폼 진입 시 동일 API 호출
2. **가용 자본에 현재 스윙의 INIT_AMOUNT를 더해서** 표시 (자기 자신은 가용 범위에 포함)
   - `실제 가용 = available_capital + 현재 스윙 INIT_AMOUNT`
3. 변경된 INIT_AMOUNT에 대해 동일한 실시간 검증

### 6.3 에러 핸들링

백엔드 400 응답 시 `detail.available_capital` 값을 사용하여 사용자 친화적 메시지 표시:
```
"투자 가능 금액(10,000,000원)을 초과했습니다."
```

---

## 7. Implementation Order

| 순서 | 파일 | 변경 내용 | 의존성 |
|------|------|----------|--------|
| 1 | `repository.py` | `get_total_init_amount()` 추가 | 없음 |
| 2 | `service.py` | `get_available_capital()` 추가 | #1 |
| 3 | `service.py` | `create_swing()` 검증 삽입 | #2 |
| 4 | `service.py` | `update_swing()` 검증 삽입 | #2 |
| 5 | `router.py` | `GET /swing/available-capital` 추가 | #2 |

---

## 8. Edge Cases & Considerations

### 8.1 INIT_AMOUNT = 0인 스윙

- `mapping_swing`에서 자동 생성된 스윙 (매수 보유 종목 매핑)은 `INIT_AMOUNT=0`
- 합계 계산에 포함되지만 영향 없음 (0원)

### 8.2 KIS API 호출 실패

- `get_stock_balance` 실패 시 `ExternalServiceError` (502) 전파
- 프론트엔드에서는 "자본 정보를 불러올 수 없습니다" 표시 후 등록 버튼 비활성화 권장

### 8.3 장 마감 후 자본 변동

- 장 마감 후에도 예수금은 정산 과정에서 변동 가능
- 실시간 API 호출이므로 항상 최신값 기준 검증

### 8.4 update_swing에서 user_id가 없는 경우

- `update_swing`은 내부 배치에서도 호출될 수 있음 (user_id=None)
- `user_id`가 없으면 자본 검증 스킵 (KIS API 호출 불가)
- 배치는 INIT_AMOUNT를 변경하지 않으므로 실질적 위험 없음
