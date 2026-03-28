# Plan: auth-key 삭제 API

## 개요
클라이언트에서 `DELETE /auths/{auth_id}` 요청으로 인증키를 삭제하는 기능 추가.

## 현재 상황
- **Router**: DELETE 엔드포인트 없음 (GET, POST, POST/choice만 존재)
- **Service**: `delete_auth(auth_id)` 존재하지만 **user_id 검증 누락** (보안 취약점)
- **Repository**: `delete(auth_id)` 존재하지만 **user_id 필터 없음**

## 구현 범위

### 필수 변경
1. **Router** (`router.py`): `DELETE /auths/{auth_id}` 엔드포인트 추가
2. **Service** (`service.py`): `delete_auth`에 user_id 파라미터 추가, 소유권 검증
3. **Repository** (`repository.py`): `delete`에 user_id 조건 추가

### 보안 요구사항
- 본인 소유 인증키만 삭제 가능 (user_id + auth_id로 조회 후 삭제)
- 존재하지 않거나 타인의 키 삭제 시도 시 404 반환

## 구현 순서
1. Repository: `delete(auth_id)` → `delete(user_id, auth_id)`
2. Service: `delete_auth(auth_id)` → `delete_auth(user_id, auth_id)`
3. Router: `DELETE /auths/{auth_id}` 엔드포인트 추가

## 영향 범위
- `delete_auth`, `delete` 메서드 시그니처 변경 → 기존 호출부 확인 필요
