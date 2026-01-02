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
- `DATABASE_URL`: MySQL 비동기 연결 문자열 (aiomysql 드라이버)
- `JWT_SECRET_KEY`: JWT 토큰 서명 키
- `REDIS_URL`: Redis 연결 URL
- `REDIS_PASSWORD`: Redis 비밀번호
- `AES_SECRET_KEY`: AES 암호화 키 (Base64 인코딩된 16바이트)

## 아키텍처

### 아키텍처 패턴: DDD Lite + Layered Architecture

본 프로젝트는 **순수 DDD가 아닌 실용적인 하이브리드 아키텍처**를 채택합니다.

| 요소 | 현재 구조 | 순수 DDD |
|------|-----------|----------|
| Entity | ✅ 비즈니스 로직 포함 | ✅ |
| Repository | ✅ 단일 구현체 | Interface + 구현 분리 |
| Service | ✅ 트랜잭션 관리 | Application + Domain Service 분리 |
| Aggregate Root | ❌ 미적용 | ✅ 필수 |
| Domain Event | ❌ 미적용 | ✅ 이벤트 기반 |
| Value Object | ❌ 미적용 | ✅ 불변 객체 |

**선택 이유**: 중소규모 프로젝트에서 순수 DDD는 오버엔지니어링. 실용적인 계층 분리로 충분한 유지보수성 확보.

### 디렉토리 구조
```
app/
├── main.py                  # FastAPI 앱 진입점, 라우터 등록
├── common/                  # 공통 인프라
│   ├── database.py          # SQLAlchemy 비동기 설정, Database 싱글톤
│   ├── dependencies.py      # FastAPI 의존성 (get_db, get_current_user)
│   ├── redis.py             # Redis 연결 관리, 싱글톤
│   └── scheduler.py         # APScheduler 크론 작업 설정
├── exceptions/              # 예외 처리 (상세: exceptions/README.md)
│   ├── base.py              # AppError 베이스 클래스
│   ├── domain.py            # 도메인 예외 (4xx)
│   ├── infrastructure.py    # 인프라 예외 (5xx)
│   ├── auth.py              # 인증/인가 예외 (401, 403)
│   └── handlers.py          # 전역 예외 핸들러
├── core/                    # 앱 설정/유틸리티
│   ├── config.py            # Pydantic Settings (환경변수)
│   ├── response.py          # 표준 API 응답 헬퍼
│   ├── security.py          # 암호화 유틸 (AES, 해싱, JWT)
│   └── health.py            # 헬스체크 로직
├── external/                # 외부 API 통합
│   ├── kis_api.py           # KIS Open API 호출 (시세, 주문, 잔고)
│   ├── http_client.py       # 범용 HTTP 클라이언트 (httpx 래퍼)
│   └── headers.py           # KIS API 헤더 생성
├── infrastructure/
│   └── database/
│       └── tables.py        # SQLAlchemy 테이블 정의
├── routers/                 # 비도메인 라우터
│   ├── backtest_router.py   # 백테스팅 API
│   └── health_router.py     # 헬스체크 API
├── domain/                  # 도메인별 모듈
│   ├── routers.py           # 라우터 통합 (전체 등록)
│   ├── swing/               # 스윙 매매 도메인
│   │   ├── entity.py        # SwingTrade, EmaOption 엔티티
│   │   ├── schemas.py       # Request/Response DTO
│   │   ├── repository.py    # 데이터 접근 계층
│   │   ├── service.py       # 비즈니스 로직
│   │   ├── router.py        # API 엔드포인트
│   │   ├── strategies/      # 매매 전략 구현
│   │   │   ├── base_strategy.py
│   │   │   ├── ema_strategy.py
│   │   │   └── ichimoku_strategy.py
│   │   ├── tech_analysis.py # 기술 지표 계산
│   │   ├── auto_swing_batch.py  # 정기 매매 배치
│   │   └── backtest/        # 백테스팅 기능
│   └── [domain]/            # user, account, auth, stock, order
│       ├── entity.py        # 도메인 엔티티 (비즈니스 로직 포함)
│       ├── schemas.py       # Pydantic DTO (Request/Response)
│       ├── repository.py    # 데이터 접근 계층
│       ├── service.py       # 비즈니스 로직 + 트랜잭션 관리
│       └── router.py        # API 엔드포인트
```

### 계층별 책임

#### 1. Entity (entity.py)
도메인 객체. 비즈니스 규칙과 유효성 검증 로직 포함.
```python
class SwingTrade:
    """스윙 매매 도메인 엔티티"""

    @classmethod
    def create(cls, account_no: str, st_code: str, init_amount: Decimal, ...) -> "SwingTrade":
        """팩토리 메서드 - 비즈니스 규칙 검증"""
        if init_amount < 0:
            raise ValueError("초기 금액은 0 이상이어야 합니다")
        # ...

    def validate(self):
        """불변 조건 검증"""
        if self.buy_ratio + self.sell_ratio > 100:
            raise ValueError("매수/매도 비율 합이 100을 초과할 수 없습니다")
```

#### 2. Schemas (schemas.py)
API 요청/응답 DTO. SQLAlchemy 모델과 분리.
```python
class SwingCreateRequest(BaseModel):
    """스윙 생성 요청"""
    ACCOUNT_NO: str
    ST_CODE: str
    INIT_AMOUNT: int
    SWING_TYPE: str = 'A'

class SwingResponse(BaseModel):
    """스윙 응답"""
    SWING_ID: int
    ST_CODE: str
    USE_YN: str

    model_config = ConfigDict(from_attributes=True)
```

#### 3. Repository (repository.py)
데이터 접근 계층. 순수 DB 쿼리만 담당. 트랜잭션 관리 안함.
```python
class SwingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, swing: SwingTrade) -> SwingTrade:
        """저장 (flush만, commit 안함)"""
        self.db.add(swing)
        await self.db.flush()
        return swing

    async def find_by_id(self, swing_id: int) -> SwingTrade | None:
        result = await self.db.execute(
            select(SwingTrade).where(SwingTrade.SWING_ID == swing_id)
        )
        return result.scalar_one_or_none()
```

#### 4. Service (service.py)
비즈니스 로직 조합 + 트랜잭션 경계 관리.
```python
class SwingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SwingRepository(db)

    async def create_swing(self, user_id: str, request: SwingCreateRequest) -> dict:
        try:
            # 도메인 엔티티 생성 (비즈니스 검증)
            swing = SwingTrade.create(...)
            db_swing = await self.repo.save(swing)
            await self.db.commit()
            return SwingResponse.model_validate(db_swing).model_dump()
        except IntegrityError:
            await self.db.rollback()
            raise DuplicateException("스윙 전략", request.ST_CODE)
```

#### 5. Router (router.py)
HTTP 요청/응답 처리만. 비즈니스 로직 금지.
```python
router = APIRouter(prefix="/swing", tags=["Swing"])

@router.post("")
async def create_swing(
    request: SwingCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    service = SwingService(db)
    result = await service.create_swing(user_id, request)
    return success_response("스윙 등록 완료", result)
```

### 핵심 패턴

1. **데이터베이스**: MySQL + 비동기 SQLAlchemy(aiomysql). `Database` 클래스에서 엔진/세션 싱글톤 패턴.

2. **인증**: JWT 토큰 기반. 사용자 자격증명 + KIS API 키는 AES 암호화.

3. **정기 매매**: APScheduler가 `trade_job`을 1시간 단위로(평일 9AM-3PM) 실행. `day_collect_job`은 3:31PM에 일일 데이터 수집.

4. **매매 전략**: `BaseStrategy` 추상 클래스 + 전략 패턴.
   - `EmaStrategy`: EMA 골든크로스 (단기/중기/장기)
   - `IchimokuStrategy`: 일목균형표 신호

5. **신호 흐름**: SWING_TRADE.SIGNAL 컬럼으로 상태 추적
   - 0=초기, 1=1차 매수, 2=2차 매수, 3=매도

### 데이터베이스 테이블
- `USER`, `ACCOUNT`, `AUTH_KEY`: 사용자/계좌 관리
- `STOCK_INFO`, `STOCK_DAY_HISTORY`: 주식 마스터 및 OHLCV
- `SWING_TRADE`, `EMA_OPT`: 매매 설정
- `TRADE_HISTORY`: 체결 내역

### 외부 의존성
- **KIS Open API**: 실시간 시세 및 주문 체결
- **Redis**: 토큰 캐싱
- **MySQL**: 데이터 영속성
- **TA-Lib**: 기술 지표 계산

## 개발 원칙

### 1. 계층 간 의존성 규칙
```
Router → Service → Repository → Entity
           ↓
        Schemas (DTO)
```
- 상위 계층만 하위 계층 참조 가능
- Entity는 어떤 계층도 참조하지 않음 (순수 도메인)
- Schemas는 계층 간 데이터 전달용

### 2. 트랜잭션 관리
```python
# Repository: flush만 (commit 안함)
async def save(self, entity):
    self.db.add(entity)
    await self.db.flush()
    return entity

# Service: 트랜잭션 경계 관리
async def create(self, data):
    try:
        result = await self.repo.save(entity)
        await self.db.commit()  # 여기서만 commit
        return result
    except Exception:
        await self.db.rollback()
        raise
```

### 3. 예외 처리 표준화

**📚 상세 가이드**: [`app/exceptions/README.md`](app/exceptions/README.md) 참고

#### HTTP 비의존 예외 체계

모든 예외는 `AppError` 베이스 상속 (HTTP 의존성 없음):

```python
# Service/Repository/Entity에서 사용
from app.exceptions import NotFoundError, DuplicateError, ValidationError

async def get_swing(self, swing_id: int):
    swing = await self.repo.find_by_id(swing_id)
    if not swing:
        raise NotFoundError("스윙 전략", swing_id)  # HTTP 비의존
    return swing
```

#### 예외 분류

**도메인 예외** (`exceptions/domain.py`) - Service에서 사용:
- `ValidationError` (422) - Entity/Request 검증 실패
- `NotFoundError` (404) - 리소스 없음
- `DuplicateError` (409) - 중복 리소스
- `BusinessRuleError` (400) - 비즈니스 규칙 위반
- `PermissionDeniedError` (403) - 소유권 검증 실패

**인프라 예외** (`exceptions/infrastructure.py`) - 외부 연동:
- `ExternalServiceError` (502/504) - 외부 API 오류
- `DatabaseError` (500) - DB 오류
- `CacheError` (500) - Redis 오류
- `ConfigurationError` (500) - 설정 오류

**인증/인가 예외** (`exceptions/auth.py`) - Router/Middleware:
- `AuthenticationError` (401) - 인증 실패
- `TokenExpiredError` (401) - 토큰 만료
- `AuthorizationError` (403) - 권한 부족

#### 전역 핸들러

```python
# main.py
from app.exceptions.handlers import register_exception_handlers

register_exception_handlers(app)  # 자동으로 모든 AppError → HTTP 응답 변환
```

#### 응답 형식

```json
{
  "success": false,
  "error_code": "NOT_FOUND",
  "message": "스윙 전략을(를) 찾을 수 없습니다: 123",
  "detail": {"resource": "스윙 전략", "identifier": "123"}
}
```

### 4. 의존성 주입
```python
# common/dependencies.py
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    db = await Database.get_session()
    try:
        yield db
    finally:
        await db.close()

def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    # JWT 검증 후 user_id 반환
    ...

# Router에서 사용
@router.get("/{id}")
async def get_item(
    id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    service = ItemService(db)
    return await service.get(id)
```

### 5. 응답 형식 통일
```python
# core/response.py
def success_response(message: str, data: Any = None) -> dict:
    return {"success": True, "message": message, "data": data}

def error_response(message: str, error_code: str = None) -> dict:
    return {"success": False, "message": message, "error_code": error_code}
```


### 6. 비동기 일관성
- 모든 DB 작업은 `async/await` 사용
- 동기 라이브러리는 `run_in_executor`로 감싸기
- `asyncio.run()` 중첩 호출 금지

### 7. 네이밍 컨벤션
- **파일명**: 소문자 + 언더스코어 (`swing_service.py`)
- **클래스**: PascalCase (`SwingService`)
- **함수/변수**: snake_case (`get_active_swings`)
- **상수**: UPPER_SNAKE_CASE (`MAX_RETRY_COUNT`)
- **DB 컬럼**: UPPER_SNAKE_CASE (`SWING_ID`, `ST_CODE`)


## 작업 방식
1. 복잡한 작업 시 반드시 계획 먼저 수립
- 코드 작성 전에 계획을 먼저 보여줄 것
- 사용자 확인 후 실행

2. 계획 수립 시 포함 사항
- 파일 구조 (어떤 파일을 만들지)
- 구현 순서 (Schema → Model → Repository → Service → Endpoint)

3. 작업 우선순위
- 정확성 > 계회성 > 일관성 > 간결성