# 예외 처리 가이드

## 파일 구조

```
app/exceptions/
├── base.py            # AppError 베이스 클래스
├── domain.py          # 도메인 예외 (4xx)
├── infrastructure.py  # 인프라 예외 (5xx)
├── auth.py            # 인증/인가 예외 (401, 403)
├── handlers.py        # 전역 핸들러
└── __init__.py        # 통합 export
```

## 예외 체계

### 1. 도메인 예외 (domain.py)

**Service, Repository, Entity에서 사용**

```python
from app.exceptions import ValidationError, NotFoundError, DuplicateError, BusinessRuleError

# Entity에서 검증
class SwingTrade:
    def validate(self):
        if self.buy_ratio + self.sell_ratio > 100:
            raise ValidationError("매수/매도 비율 합이 100을 초과할 수 없습니다", field="ratio")

# Service에서 비즈니스 규칙
class SwingService:
    async def create_swing(self, request):
        try:
            swing = SwingTrade.create(...)
            db_swing = await self.repo.save(swing)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise DuplicateError("스윙 전략", request.ST_CODE)

    async def get_swing(self, swing_id):
        swing = await self.repo.find_by_id(swing_id)
        if not swing:
            raise NotFoundError("스윙 전략", swing_id)
        return swing
```

**HTTP 매핑:**
- `ValidationError` → 422 Unprocessable Entity
- `NotFoundError` → 404 Not Found
- `DuplicateError` → 409 Conflict
- `BusinessRuleError` → 400 Bad Request
- `PermissionDeniedError` → 403 Forbidden

### 2. 인프라 예외 (infrastructure.py)

**외부 API, DB, Redis 연동 시 사용**

```python
from app.exceptions import ExternalServiceError, DatabaseError, CacheError

# 외부 API 호출 (fetch 유틸리티 사용)
# app.module.fetch_api.fetch()가 자동으로 ExternalServiceError 처리
async def get_stock_balance(user_id: str):
    # fetch()에 service_name 전달하면 자동으로 예외 처리됨
    response = await fetch(
        "GET",
        api_url,
        service_name="KIS API",  # 중요: 서비스 이름 명시
        headers=headers
    )
    return response

# 외부 API 호출 (직접 httpx 사용 시)
async def get_stock_data_direct():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(api_url)
            return response.json()
    except httpx.TimeoutException as e:
        raise ExternalServiceError(
            service="Stock API",
            message="타임아웃",
            status_code=504,
            original_error=e
        )
    except httpx.RequestError as e:
        raise ExternalServiceError(
            service="Stock API",
            message="요청 실패",
            status_code=503,
            original_error=e
        )

# DB 작업
class SwingRepository:
    async def save(self, swing):
        try:
            self.db.add(swing)
            await self.db.flush()
        except SQLAlchemyError as e:
            raise DatabaseError(
                "스윙 저장 실패",
                operation="insert",
                original_error=e
            )
```

**HTTP 매핑:**
- `ExternalServiceError` → 502 Bad Gateway / 504 Gateway Timeout
- `DatabaseError` → 500 Internal Server Error
- `CacheError` → 500 (또는 로깅만 하고 무시)
- `ConfigurationError` → 500

### 3. 인증/인가 예외 (auth.py)

**Router, Middleware, Dependency에서 사용**

```python
from app.exceptions import AuthenticationError, AuthorizationError

# JWT 검증
def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("토큰이 만료되었습니다", reason="token_expired")
    except jwt.JWTError:
        raise AuthenticationError("유효하지 않은 토큰입니다", reason="invalid_token")

    return payload.get("sub")

# 권한 검증
def require_admin(user_id: str = Depends(get_current_user)):
    if not is_admin(user_id):
        raise AuthorizationError("관리자 권한이 필요합니다", required_role="admin")
```

**HTTP 매핑:**
- `AuthenticationError` → 401 Unauthorized
- `AuthorizationError` → 403 Forbidden

## 전역 핸들러 등록

**main.py에 추가:**

```python
from fastapi import FastAPI
from app.exceptions.handlers import register_exception_handlers

app = FastAPI()

# 전역 핸들러 등록
register_exception_handlers(app)

# 라우터 등록
app.include_router(user_router)
app.include_router(swing_router)
```

## 응답 형식

모든 예외는 다음 형식으로 변환:

```json
{
  "success": false,
  "error_code": "NOT_FOUND",
  "message": "스윙 전략을(를) 찾을 수 없습니다: 123",
  "detail": {
    "resource": "스윙 전략",
    "identifier": "123"
  }
}
```

## 에러 코드 체계

### 도메인 (4xx)
- `VALIDATION_ERROR` (422)
- `NOT_FOUND` (404)
- `DUPLICATE` (409)
- `BUSINESS_RULE_VIOLATION` (400)
- `PERMISSION_DENIED` (403)

### 인증/인가 (401, 403)
- `AUTHENTICATION_FAILED` (401)
- `TOKEN_EXPIRED` (401)
- `INVALID_TOKEN` (401)
- `AUTHORIZATION_FAILED` (403)

### 인프라 (5xx)
- `EXTERNAL_SERVICE_ERROR` (502/503/504)
- `DATABASE_ERROR` (500)
- `CACHE_ERROR` (500)
- `CONFIGURATION_ERROR` (500)

### 기타
- `INTERNAL_SERVER_ERROR` (500) - 예상하지 못한 예외

## 마이그레이션 가이드

### common/exceptions.py에서 마이그레이션

**Before:**
```python
from app.common.exceptions import NotFoundException, DuplicateException, BusinessException

raise NotFoundException("스윙 전략", swing_id)
raise DuplicateException("스윙 전략", code)
raise BusinessException("잘못된 요청")
```

**After:**
```python
from app.exceptions import NotFoundError, DuplicateError, BusinessRuleError

raise NotFoundError("스윙 전략", swing_id)
raise DuplicateError("스윙 전략", code)
raise BusinessRuleError("잘못된 요청")
```

### HTTPException에서 마이그레이션

**Before:**
```python
from fastapi import HTTPException

raise HTTPException(status_code=404, detail="Not found")
raise HTTPException(status_code=401, detail="Unauthorized")
```

**After:**
```python
from app.exceptions import NotFoundError, AuthenticationError

raise NotFoundError("리소스", identifier)
raise AuthenticationError("인증 실패")
```

## 베스트 프랙티스

1. **Service/Repository/Entity는 도메인 예외만 사용**
   - HTTP 의존성 없음
   - 배치 작업, CLI에서도 재사용 가능

2. **원본 예외 보존**
   ```python
   except SQLAlchemyError as e:
       raise DatabaseError("DB 오류", original_error=e)
   ```

3. **detail은 디버깅용 정보만**
   - 프로덕션에서는 노출 주의
   - 민감 정보 포함 금지

4. **에러 코드는 일관성 유지**
   - 대문자 + 언더스코어
   - 동사_명사 형태 권장 (VALIDATION_ERROR, NOT_FOUND 등)

5. **로깅은 전역 핸들러에서 일괄 처리**
   - Service에서 중복 로깅 불필요
   - 5xx는 ERROR, 4xx는 WARNING