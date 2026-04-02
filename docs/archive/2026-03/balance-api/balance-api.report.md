# PDCA Completion Report: balance-api (계좌번호 검증 API)

> **Date**: 2026-03-29 | **Match Rate**: 100% | **Iterations**: 0

## 1. 요약

| 항목 | 내용 |
|------|------|
| Feature | balance-api (계좌번호 검증 API) |
| 목표 | auth_id + account_no로 KIS API를 통한 계좌번호 유효성 검증 |
| 결과 | 구현 완료, Match Rate 100% |
| PDCA 반복 | 0회 (1차 구현에서 통과) |

## 2. PDCA 진행 이력

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ (100%) → [Report] ✅
```

| Phase | 상태 | 비고 |
|-------|------|------|
| Plan | 완료 | 초기 잔고 조회 → 계좌 검증으로 방향 수정 |
| Design | 완료 | POST /accounts/verify 설계 |
| Do | 완료 | 4개 파일 수정/추가 |
| Check | 100% | Gap 없음 |
| Report | 완료 | 본 문서 |

## 3. 구현 결과

### API Endpoint

```
POST /accounts/verify
Authorization: Bearer {jwt_token}

Body: { "AUTH_ID": 1, "ACCOUNT_NO": "1234567801" }
```

### 변경 파일

| 파일 | 변경 |
|------|------|
| `app/domain/account/schemas.py` | `AccountVerifyRequest` DTO 추가 |
| `app/external/kis_api.py` | `issue_token()` (캐싱 없는 토큰 발급), `verify_account_balance()` 추가 |
| `app/domain/account/service.py` | `verify_account()` 메서드 추가 |
| `app/domain/account/router.py` | `POST /accounts/verify` 엔드포인트 추가 |

### 처리 흐름

```
Client → POST /accounts/verify (auth_id, account_no)
  → AuthRepository.find_by_id(user_id, auth_id)  # 인증키 조회 + 소유권 검증
  → issue_token(simulation_yn, appkey, appsecret)  # 캐싱 없이 토큰 발급
  → verify_account_balance(access_data, account_no)  # KIS 잔고 API 호출
  → rt_cd == "0" ? 성공 : ExternalServiceError(msg1)
```

## 4. 설계 결정 기록

| 결정 | 이유 |
|------|------|
| Account 도메인에 추가 | 계좌 검증은 계좌의 하위 기능 |
| `issue_token` 신규 함수 | 검증 용도의 일회성 호출에 Redis 캐싱 불필요 |
| `rt_cd` 체크 | KIS API가 잘못된 계좌번호에도 200을 반환하므로 body 레벨 검증 필요 |
| Repository/Entity 미생성 | DB 저장 없이 외부 API 호출만 수행 |

## 5. Commit

- `04f4692` - 계좌번호 검증 API 추가 (POST /accounts/verify)
- Branch: `tmp`
