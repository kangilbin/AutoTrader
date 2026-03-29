# Plan: balance-api (계좌번호 검증 API)

## 1. 개요

### 배경
클라이언트에서 계좌를 등록하기 전, 해당 계좌번호가 유효한지 검증할 수 있는 API가 없음. KIS 잔고 조회 API를 활용하면 계좌번호의 유효성을 확인할 수 있음.

### 목표
클라이언트가 `auth_id`와 `account_no`를 전달하면, 해당 인증키의 appkey/appsecret으로 KIS 잔고 조회 API를 호출하여 계좌번호가 유효한지 검증하는 API 제공.

### 범위
- Account 도메인에 계좌 검증 엔드포인트 추가
- KIS 잔고 조회 API 호출 → 성공 시 유효, 실패 시 에러
- 검증 전용 KIS API 함수 신규 생성

## 2. 기능 요구사항

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| FR-01 | auth_id로 인증키(appkey/appsecret) 조회 | 필수 |
| FR-02 | 조회한 인증키로 OAuth 토큰 발급 | 필수 |
| FR-03 | KIS 잔고 조회 API로 계좌번호 유효성 확인 | 필수 |
| FR-04 | JWT 인증 필수 + 인증키 소유권 검증 | 필수 |

## 3. API 설계

### `POST /accounts/verify`

**인증**: Bearer Token (JWT) 필수

**요청**:
```json
{
  "AUTH_ID": 1,
  "ACCOUNT_NO": "1234567801"
}
```

**성공 응답**:
```json
{
  "success": true,
  "message": "계좌번호 검증 성공",
  "data": { "account_no": "1234567801", "valid": true }
}
```

**실패**: KIS API 오류 시 ExternalServiceError (502)

## 4. 구현 계획

### 수정/생성 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/account/schemas.py` | `AccountVerifyRequest` DTO 추가 |
| `app/domain/account/service.py` | `verify_account()` 메서드 추가 |
| `app/domain/account/router.py` | `POST /accounts/verify` 엔드포인트 추가 |
| `app/external/kis_api.py` | `verify_account_balance()` 함수 추가 |

### 구현 순서
1. Schema - `AccountVerifyRequest` 추가
2. KIS API - `verify_account_balance()` 함수 추가
3. Service - `verify_account()` 메서드 추가
4. Router - `POST /accounts/verify` 엔드포인트 추가
