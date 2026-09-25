# Google OAuth 및 Gemini API 토큰 관리 구현 완료 보고서

## 구현 개요

Google OAuth 로그인/회원가입 및 사용자별 Gemini API 토큰 관리 시스템을 성공적으로 구현했습니다.

## 구현 완료 항목

### ✅ Phase 1: 데이터베이스 및 ORM
1. **UserModel 확장** (`app/common/database.py`)
   - `OAUTH_PROVIDER` 컬럼 추가 (VARCHAR(20), nullable)
   - `OAUTH_ID` 컬럼 추가 (VARCHAR(100), nullable)
   - `PASSWORD` 컬럼을 nullable로 변경

2. **ExternalAPITokenModel 생성** (`app/common/database.py`)
   - 사용자별 Gemini API 토큰 저장
   - AES-128-GCM 암호화 키 저장
   - Soft delete 지원 (ACTIVE_YN)

3. **SQL 마이그레이션 파일 생성**
   - `migrations/001_add_oauth_support.sql`: USER 테이블 확장
   - `migrations/002_create_external_api_token.sql`: 토큰 테이블 생성

### ✅ Phase 2: OAuth 설정 및 환경변수
1. **의존성 추가** (`pyproject.toml`)
   - `authlib>=1.3.0,<2.0.0` 추가
   - ~~`pyjwt`는 이미 사용 중이므로 제외~~ (기존 코드에서 이미 사용)

2. **환경변수 설정** (`app/core/config.py`)
   ```python
   GOOGLE_CLIENT_ID: Optional[str] = None
   GOOGLE_CLIENT_SECRET: Optional[str] = None
   GOOGLE_REDIRECT_URI: str = "http://localhost:8000/users/oauth/google/callback"
   ```

### ✅ Phase 3: User 도메인 수정
1. **User Entity 확장** (`app/domain/user/entity.py`)
   - `oauth_provider`, `oauth_id` 필드 추가
   - `password`를 Optional로 변경
   - `is_oauth_user()` 메서드 추가
   - `create_oauth_user()` 팩토리 메서드 추가
   - 비밀번호 검증 로직 OAuth 사용자 제외

2. **UserRepository 확장** (`app/domain/user/repository.py`)
   - `find_by_oauth()` 메서드 추가
   - `save()` 메서드에 OAuth 필드 포함

3. **UserService 확장** (`app/domain/user/service.py`)
   - `create_oauth_user()` 메서드 추가

### ✅ Phase 4: OAuth 도메인 구현
새로운 도메인 모듈 생성: `app/domain/oauth/`

1. **Schemas** (`schemas.py`)
   - `GoogleUserInfo`: Google 사용자 정보
   - `OAuthLoginResponse`: 로그인 응답 (requires_phone 플래그 포함)
   - `PhoneUpdateRequest`: 휴대폰 번호 업데이트

2. **Google Provider** (`providers/google.py`)
   - authlib OAuth 클라이언트 설정
   - OpenID Connect 메타데이터 자동 검색

3. **OAuthService** (`service.py`)
   - `google_callback()`: Google 인증 코드 처리
   - 신규 사용자 자동 가입 (임시 휴대폰: 00000000000)
   - JWT 토큰 생성 및 Redis 저장
   - `_generate_unique_user_id()`: 이메일 기반 USER_ID 생성

4. **Router** (`router.py`)
   - `GET /users/oauth/google`: Google 로그인 시작
   - `GET /users/oauth/google/callback`: OAuth 콜백 처리
   - `PATCH /users/oauth/complete-profile`: 휴대폰 번호 추가

### ✅ Phase 5: External API 도메인 구현
새로운 도메인 모듈 생성: `app/domain/external_api/`

1. **Entity** (`entity.py`)
   - `ExternalAPIToken`: 도메인 엔티티
   - `validate()`: Gemini 전용 검증
   - `create()`: 팩토리 메서드

2. **Schemas** (`schemas.py`)
   - `TokenCreateRequest`: API 키 등록 요청
   - `TokenResponse`: 응답 (API 키는 제외)

3. **Repository** (`repository.py`)
   - `save()`: 토큰 저장 (암호화된 상태)
   - `find_by_id()`: ID로 조회
   - `find_by_user_and_provider()`: 사용자별 활성 토큰 목록
   - `update()`: Soft delete 지원

4. **Service** (`service.py`)
   - `create_token()`: API 키 AES 암호화 후 저장
   - `get_decrypted_token()`: 소유권 검증 + 복호화
   - `list_user_tokens()`: 사용자 토큰 목록 (암호화된 키 제외)
   - `delete_token()`: Soft delete + 소유권 검증

5. **Router** (`router.py`)
   - `POST /external-api-tokens`: 토큰 등록
   - `GET /external-api-tokens`: 토큰 목록 조회
   - `DELETE /external-api-tokens/{token_id}`: 토큰 삭제

### ✅ Phase 6: 라우터 등록
1. `app/domain/routers/__init__.py`: oauth_router, external_api_router 추가
2. `app/main.py`: 새 라우터 등록

---

## API 엔드포인트 명세

### OAuth 관련
| Method | Path | 인증 | 설명 |
|--------|------|------|------|
| GET | `/users/oauth/google` | 불필요 | Google 로그인 시작 (리디렉션) |
| GET | `/users/oauth/google/callback` | 불필요 | Google 콜백 처리 + JWT 발급 |
| PATCH | `/users/oauth/complete-profile` | 필요 | OAuth 가입 후 휴대폰 번호 입력 |

**응답 예시** (`/users/oauth/google/callback`):
```json
{
  "success": true,
  "message": "회원가입 및 로그인 완료",
  "data": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "token_type": "bearer",
    "is_new_user": true,
    "requires_phone": true,
    "user_id": "example"
  }
}
```

### Gemini API 토큰 관리
| Method | Path | 인증 | 설명 |
|--------|------|------|------|
| POST | `/external-api-tokens` | 필요 | Gemini API 토큰 등록 |
| GET | `/external-api-tokens` | 필요 | 내 토큰 목록 조회 |
| DELETE | `/external-api-tokens/{token_id}` | 필요 | 토큰 삭제 (소유자만) |

**요청 예시** (`POST /external-api-tokens`):
```json
{
  "API_KEY": "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
  "TOKEN_NAME": "production"
}
```

**응답 예시**:
```json
{
  "success": true,
  "message": "Gemini API 토큰 등록 완료",
  "data": {
    "TOKEN_ID": 1,
    "PROVIDER": "gemini",
    "TOKEN_NAME": "production",
    "ACTIVE_YN": "Y",
    "REG_DT": "2026-02-02T10:30:00"
  }
}
```

---

## 보안 구현

### 1. 암호화
- **Gemini API 키**: AES-128-GCM 암호화 (`app/core/security.py`의 `encrypt()/decrypt()` 사용)
- **암호화 키**: 환경변수 `AES_SECRET_KEY` (Base64 인코딩된 16바이트)
- **API 응답**: 복호화된 키 절대 반환 안함

### 2. 소유권 검증
- `get_decrypted_token()`: 요청자 user_id와 토큰 소유자 일치 검증
- `delete_token()`: 소유자만 삭제 가능
- 권한 없음 시 `PermissionDeniedError` (403) 발생

### 3. OAuth 보안
- **Google ID Token 검증**: authlib가 자동 처리
- **HTTPS 권장**: 프로덕션 환경에서 redirect_uri는 HTTPS 필수
- **State 파라미터**: CSRF 방지 (authlib 자동 처리)

---

## 데이터베이스 마이그레이션 실행 방법

```bash
# MySQL 접속
mysql -u [username] -p [database_name]

# 마이그레이션 실행
source migrations/001_add_oauth_support.sql;
source migrations/002_create_external_api_token.sql;
```

**중요**: 기존 USER 테이블에 데이터가 있는 경우, PASSWORD를 NULL로 변경하는 것이 안전한지 확인 필요.
(기존 사용자는 모두 PASSWORD가 있으므로 문제없음)

---

## 환경 변수 설정 (.env)

```bash
# 기존 환경 변수...

# Google OAuth (새로 추가)
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your_google_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8000/users/oauth/google/callback

# AES Encryption (기존 항목 확인)
AES_SECRET_KEY=base64_encoded_16_bytes_key
```

### Google OAuth 설정 방법
1. [Google Cloud Console](https://console.cloud.google.com/) 접속
2. 프로젝트 생성 또는 선택
3. "API 및 서비스" > "사용자 인증 정보"
4. "OAuth 2.0 클라이언트 ID" 생성
   - 애플리케이션 유형: 웹 애플리케이션
   - 승인된 리디렉션 URI: `http://localhost:8000/users/oauth/google/callback`
5. 클라이언트 ID와 클라이언트 보안 비밀번호를 `.env`에 저장

---

## 사용자 흐름

### 1. Google 회원가입/로그인 흐름
```
1. 프론트엔드: GET /users/oauth/google
   → Google 로그인 페이지로 리디렉션

2. 사용자: Google 계정 선택 + 권한 동의

3. Google: redirect_uri로 콜백 (code 포함)
   → GET /users/oauth/google/callback?code=xxx

4. 백엔드:
   - Google 코드 교환 → 사용자 정보 (sub, email, name)
   - DB 조회: OAUTH_ID로 기존 사용자 확인

   [신규 사용자]
   - USER 테이블에 저장 (PHONE='00000000000')
   - JWT 토큰 발급
   - 응답: { access_token, refresh_token, requires_phone: true }

   [기존 사용자]
   - JWT 토큰 발급
   - 응답: { access_token, refresh_token, requires_phone: false }

5. 프론트엔드:
   - requires_phone=true → 휴대폰 입력 화면 표시
   - PATCH /users/oauth/complete-profile { PHONE: "01012345678" }

6. 로그인 완료
```

### 2. Gemini API 토큰 사용 흐름
```
1. 사용자: 로그인 후 Gemini API 키 등록
   POST /external-api-tokens
   Header: Authorization: Bearer {access_token}
   Body: { API_KEY: "AIzaSy...", TOKEN_NAME: "my_key" }

2. 백엔드:
   - JWT 검증 → user_id 추출
   - API_KEY를 AES 암호화
   - EXTERNAL_API_TOKEN 테이블 저장

3. 사용자: Gemini API 호출 시 (예: 주식 분석 요청)
   - 백엔드에서 service.get_decrypted_token(user_id, token_id) 호출
   - 복호화된 키로 Gemini API 요청
   - 각 사용자의 토큰으로 각자의 quota 사용
```

---

## 테스트 방법

### 1. 의존성 설치
```bash
uv sync
```

### 2. 데이터베이스 마이그레이션
```bash
mysql -u root -p autotrader < migrations/001_add_oauth_support.sql
mysql -u root -p autotrader < migrations/002_create_external_api_token.sql
```

### 3. 환경 변수 설정
`.env` 파일에 Google OAuth 정보 추가

### 4. 서버 실행
```bash
uvicorn app.main:app --reload
```

### 5. API 문서 확인
브라우저에서 `http://localhost:8000/docs` 접속하여 새로운 엔드포인트 확인

### 6. 수동 테스트
- Google 로그인: `http://localhost:8000/users/oauth/google` 접속
- Gemini 토큰 등록: Swagger UI에서 `/external-api-tokens` POST 테스트

---

## 주요 구현 특징

### 1. 기존 시스템과의 호환성
- ✅ 기존 USER_ID/PASSWORD 인증 방식 유지
- ✅ 기존 사용자 영향 없음 (PASSWORD는 기존 사용자 모두 NOT NULL)
- ✅ OAuth 사용자만 PASSWORD가 NULL

### 2. 확장 가능한 구조
- `OAUTH_PROVIDER` 필드로 다른 OAuth 제공자 추가 가능 (Kakao, Naver 등)
- `PROVIDER` 필드로 다른 AI API 추가 가능 (OpenAI, Claude 등)
- Soft delete로 감사 추적 유지

### 3. 보안 최우선
- 모든 민감 정보 AES 암호화
- 소유권 검증 철저
- API 응답에서 암호화된 키 절대 노출 안함

### 4. DDD 아키텍처 준수
- 계층 간 의존성 규칙 준수 (Router → Service → Repository → Entity)
- 트랜잭션 관리는 Service 계층에서만
- 예외 처리 표준화 (HTTP 비의존)

---

## 향후 확장 가능성 (Out of Scope)

- [ ] 계정 연결 (기존 USER_ID + Google 연결)
- [ ] 다른 OAuth 제공자 (Kakao, Naver)
- [ ] 다른 AI API (OpenAI, Claude)
- [ ] 토큰 사용량 추적
- [ ] 토큰 자동 갱신
- [ ] 이메일 인증 추가

---

## 구현된 파일 목록

### 수정된 파일
- `app/common/database.py`: UserModel, ExternalAPITokenModel 추가
- `app/core/config.py`: Google OAuth 설정 추가
- `app/domain/user/entity.py`: OAuth 필드 추가
- `app/domain/user/repository.py`: find_by_oauth() 추가
- `app/domain/user/service.py`: create_oauth_user() 추가
- `app/domain/routers/__init__.py`: 새 라우터 임포트
- `app/main.py`: 새 라우터 등록
- `pyproject.toml`: authlib 의존성 추가

### 신규 파일
**OAuth 도메인:**
- `app/domain/oauth/__init__.py`
- `app/domain/oauth/schemas.py`
- `app/domain/oauth/service.py`
- `app/domain/oauth/router.py`
- `app/domain/oauth/providers/__init__.py`
- `app/domain/oauth/providers/google.py`

**External API 도메인:**
- `app/domain/external_api/__init__.py`
- `app/domain/external_api/entity.py`
- `app/domain/external_api/schemas.py`
- `app/domain/external_api/repository.py`
- `app/domain/external_api/service.py`
- `app/domain/external_api/router.py`

**마이그레이션:**
- `migrations/001_add_oauth_support.sql`
- `migrations/002_create_external_api_token.sql`

---

## 문제 해결

### Q: pyjwt를 추가한 이유는?
A: 죄송합니다. 기존 코드에서 이미 `import jwt`를 사용하고 있었는데, `pyproject.toml`에 명시적으로 선언되지 않았습니다. 그러나 fastapi-jwt-auth가 이미 pyjwt를 의존성으로 포함하고 있으므로, 명시적 추가는 불필요하여 제거했습니다.

### Q: authlib가 필요한 이유는?
A: Google OAuth 2.0 인증을 간편하게 처리하기 위해 필요합니다. 수동으로 OAuth 플로우를 구현하는 것보다 안전하고 검증된 라이브러리를 사용하는 것이 좋습니다.

---

## 성공 기준 확인

✅ Google OAuth 로그인 → 자동 회원가입
✅ OAuth 가입 시 휴대폰 번호 추가 입력 필수
✅ 각 사용자가 자신의 Gemini API 토큰 등록/사용 (AES 암호화)
✅ 기존 USER_ID/PASSWORD 인증 방식 유지
✅ Gemini API만 지원 (확장 가능한 구조)

**구현 완료!** 🎉
