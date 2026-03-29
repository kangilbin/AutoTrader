# Design: balance-api (계좌번호 검증 API)

> Plan 참조: `docs/01-plan/features/balance-api.plan.md`

## 1. API 명세

### `POST /accounts/verify`

| 항목 | 값 |
|------|-----|
| Method | POST |
| Path | `/accounts/verify` |
| Auth | Bearer Token (JWT) 필수 |

### 요청 Body

```json
{
  "AUTH_ID": 1,
  "ACCOUNT_NO": "1234567801"
}
```

### 성공 응답

```json
{
  "success": true,
  "message": "계좌번호 검증 성공",
  "data": {
    "account_no": "1234567801",
    "valid": true
  }
}
```

## 2. 구현 상세

### 2-1. AccountVerifyRequest (Schema)

**파일**: `app/domain/account/schemas.py`

```python
class AccountVerifyRequest(BaseModel):
    """계좌번호 검증 요청"""
    ACCOUNT_NO: str
    AUTH_ID: int
```

### 2-2. verify_account_balance (KIS API)

**파일**: `app/external/kis_api.py`

- `access_data`와 `account_no`를 받아 KIS 잔고 조회 API 호출
- 기존 `get_stock_balance`와 달리 페이지네이션/결과 수집 불필요
- 호출 성공 = 계좌 유효, 실패 시 `fetch` 내부에서 `ExternalServiceError` 발생

```python
async def verify_account_balance(access_data: dict, account_no: str):
    """계좌번호 검증 - KIS 잔고 조회 API로 유효성 확인"""
    # account_no[:8] → CANO, account_no[-2:] → ACNT_PRDT_CD
    # KIS API 호출 성공 시 return, 실패 시 ExternalServiceError
```

### 2-3. AccountService.verify_account

**파일**: `app/domain/account/service.py`

```python
async def verify_account(self, user_id: str, auth_id: int, account_no: str) -> dict:
    # 1. AuthRepository로 인증키 조회 (소유권 검증: user_id + auth_id)
    # 2. oauth_token으로 access_data 획득
    # 3. verify_account_balance 호출
    # 4. 성공 시 {"account_no": ..., "valid": True} 반환
```

### 2-4. Account Router

**파일**: `app/domain/account/router.py`

```python
@router.post("/verify")
async def verify_account(
    request: AccountVerifyRequest,
    service: Annotated[AccountService, Depends(get_account_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    result = await service.verify_account(user_id, request.AUTH_ID, request.ACCOUNT_NO)
    return success_response("계좌번호 검증 성공", result)
```

## 3. 구현 순서

1. `AccountVerifyRequest` Schema 추가
2. `verify_account_balance` KIS API 함수 추가
3. `AccountService.verify_account()` 메서드 추가
4. Router에 `POST /accounts/verify` 엔드포인트 추가

## 4. 에러 처리

| 상황 | 예외 | HTTP |
|------|------|------|
| 미인증 | AuthenticationError | 401 |
| 인증키 없음 | NotFoundError | 404 |
| KIS API 오류 (잘못된 계좌) | ExternalServiceError | 502 |

> 인증키 소유권 검증: `AuthRepository.find_by_id(user_id, auth_id)` - user_id가 일치하지 않으면 None 반환 → NotFoundError
