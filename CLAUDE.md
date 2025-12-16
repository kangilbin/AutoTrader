# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소의 코드에서 작업할 때 참고할 지침을 제공합니다.

## 프로젝트 개요

Auto-Trader는 FastAPI로 구축된 한국 주식 자동매매 백엔드 서비스입니다. 사용자가 계좌를 등록하고 한국투자증권(KIS) API에 연결하여 기술 지표(EMA, RSI, ADX, OBV)를 기반으로 자동 스윙 매매 전략을 실행할 수 있습니다.

## 커맨드

### 개발
```bash
# 의존성 설치 (uv 패키지 매니저 사용)
uv sync

# 개발 서버 실행
uvicorn app.main:app --reload

# Docker로 실행
docker build -t auto-trader .
docker run -p 8000:auto-trader
```

### 환경 변수
`.env` 파일에 다음 항목이 필요합니다:
- `DATABASE_URL`: MySQL 비동기 연결 문자열 (asyncmy 드라이버)
- KIS API 자격증명 (AUTH_KEY 테이블을 통해 사용자별로 관리)

## 아키텍처

### 디렉토리 구조
```
app/
├── main.py              # FastAPI 앱 진입점 및 모든 라우트 정의
├── api/                 # 외부 API 통합
│   ├── kis_open_api.py  # KIS OAuth 토큰 관리
│   └── local_stock_api.py # KIS 국내 주식 API 호출
├── infrastructure/
│   ├── database/        # SQLAlchemy 비동기 설정, 테이블 정의
│   └── security/        # JWT 유틸, 암호화
├── module/
│   ├── schedules.py     # APScheduler 크론 작업
│   └── redis_connection.py # Redis 싱글톤
├── swing/               # 스윙 매매 도메인
│   ├── strategies/      # 매매 전략 구현
│   │   ├── base_strategy.py
│   │   ├── ema_strategy.py      # EMA 골든크로스 전략
│   │   └── ichimoku_strategy.py # 일목균형표 전략
│   ├── tech_analysis.py # 기술 지표 계산
│   ├── auto_swing_batch.py # 정기 매매 작업
│   └── backtest/        # 백테스팅 기능
└── [domain]/            # user, account, auth, stock, order 모듈
    ├── *_model.py       # Pydantic 모델
    ├── *_service.py     # 비즈니스 로직
    └── *_crud.py        # 데이터베이스 작업
```

### 핵심 패턴

1. **데이터베이스**: MySQL과 비동기 SQLAlchemy(asyncmy). `Database` 클래스에서 엔진/세션 싱글톤 패턴. 시작 시 자동으로 테이블 생성.

2. **인증**: `fastapi-jwt-auth`를 통한 JWT 토큰. 사용자 자격증명 + KIS API 키는 `cryptography`로 암호화.

3. **정기 매매**: APSche[duler가 `trade_job`을 1시간 단위로(평일 9AM-3PM) 실행하고, `day_collect_job`은 3:31PM에 일일 데이터 수집.

4. **매매 전략**: `BaseStrategy` 추상 클래스를 사용한 전략 패턴. 현재 구현:
   - `EmaStrategy`: EMA 골든크로스 (단기/중기/장기)
   - `IchimokuStrategy`: 일목균형표 신호

5. **신호 흐름**: SWING_TRADE의 SIGNAL 컬럼이 상태 추적: 0=초기, 1=1차 매수, 2=2차 매수, 3=매도

### 데이터베이스 테이블
- USER, ACCOUNT, AUTH_KEY: 사용자/계좌 관리
- STOCK_INFO, STOCK_DAY_HISTORY: 주식 마스터 데이터 및 OHLCV
- SWING_TRADE, EMA_OPT: 매매 설정
- TRADE_HISTORY: 체결 내역

### 외부 의존성
- KIS Open API: 실시간 시세 및 주문 체결
- Redis: 토큰 캐싱
- MySQL: 데이터 영속성
- TA-Lib: 기술 지표 계산

## 개발 원칙

### 1. 도메인 계층 구조
각 도메인은 다음 계층을 명확히 분리:
```
[domain]/
├── router.py      # API 엔드포인트 정의 (HTTP 요청/응답만 처리)
├── service.py     # 비즈니스 로직 (트랜잭션, 검증, 도메인 규칙)
├── crud.py        # 데이터 접근 계층 (순수 DB 쿼리만)
└── model.py       # Pydantic 스키마
```
- **Router**: HTTP 관련 로직만. 비즈니스 로직 금지
- **Service**: 비즈니스 규칙, 여러 CRUD 조합, 트랜잭션 관리
- **CRUD**: 단일 테이블 쿼리. 비즈니스 로직 금지

### 2. 예외 처리 표준화
```python
# 도메인별 예외 정의 (app/infrastructure/exceptions.py)
class AppException(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message

class NotFoundException(AppException):
    def __init__(self, resource: str):
        super().__init__(404, "NOT_FOUND", f"{resource}을(를) 찾을 수 없습니다")

class BusinessException(AppException):
    def __init__(self, message: str):
        super().__init__(400, "BUSINESS_ERROR", message)
```
- Service 계층에서 예외 발생, Router에서 HTTPException으로 변환 금지
- 전역 예외 핸들러에서 일괄 처리

### 3. Dependency Injection 활용
```python
# 세션 주입
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session

# 서비스 주입
def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)

# 라우터에서 사용
@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service)
):
    return await service.get_by_id(user_id)
```
- 전역 객체 직접 import 대신 Depends 사용
- 테스트 시 의존성 오버라이드 용이

### 4. Pydantic 모델 분리
```python
# model.py 구조
class UserBase(BaseModel):
    email: str
    name: str

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None

class UserResponse(UserBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
```
- **Create**: 생성 시 필수 필드
- **Update**: 부분 업데이트용 (모든 필드 Optional)
- **Response**: API 응답용 (DB 모델에서 변환)
- SQLAlchemy 모델과 Pydantic 스키마 혼용 금지

### 5. 라우터 분리
```python
# main.py
app.include_router(user_router, prefix="/api/users", tags=["users"])
app.include_router(account_router, prefix="/api/accounts", tags=["accounts"])
app.include_router(swing_router, prefix="/api/swing", tags=["swing"])
```
- 도메인별 라우터 파일 분리
- main.py는 라우터 등록과 앱 설정만
- 공통 의존성은 `dependencies.py`에 정의

### 6. 비동기 일관성
```python
# 올바른 예시
async def get_user(user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

# 피해야 할 예시 (동기 호출 혼용)
def get_user_sync(user_id: int):  # 동기 함수에서 비동기 DB 호출 불가
    ...
```
- 모든 DB 작업은 `async/await` 사용
- 동기 라이브러리 사용 시 `run_in_executor`로 감싸기
- `asyncio.run()` 중첩 호출 금지

### 7. 응답 형식 통일
```python
class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    message: str | None = None

# 사용
@router.get("/users/{id}", response_model=ApiResponse[UserResponse])
async def get_user(id: int):
    user = await service.get_by_id(id)
    return ApiResponse(data=user)
```

### 8. 환경 설정 관리
```python
# config.py
class Settings(BaseSettings):
    database_url: str
    redis_url: str
    jwt_secret: str

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
```
- 하드코딩된 설정값 금지
- 환경별 설정은 `.env` 파일로 관리