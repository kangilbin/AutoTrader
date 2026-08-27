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
│   ├── scheduler.py         # APScheduler 크론 작업 설정 (국내/미국 잡 분리)
│   ├── middleware.py        # 디바이스 검증 등 미들웨어
│   └── email.py             # 메일 발송
├── exceptions/              # 예외 처리 (상세: exceptions/README.md)
│   ├── base.py              # AppError 베이스 클래스
│   ├── domain.py            # 도메인 예외 (4xx)
│   ├── infrastructure.py    # 인프라 예외 (5xx)
│   ├── auth.py              # 인증/인가 예외 (401, 403)
│   └── handlers.py          # 전역 예외 핸들러
├── core/                    # 앱 설정 + 계층 공용 유틸 (domain·external 양쪽에서 사용)
│   ├── config.py            # Pydantic Settings (환경변수, 크론 설정 포함)
│   ├── response.py          # 표준 API 응답 헬퍼
│   ├── security.py          # 암호화 유틸 (AES, 해싱, JWT)
│   ├── health.py            # 헬스체크 로직
│   ├── sentry.py            # Sentry 초기화
│   ├── market_code.py       # 시장 코드 판별/변환 (국내 vs NYS/NAS/AMS)
│   ├── price.py             # 가격 정밀도·호가 단위 (국내 정수 / 해외 소수점)
│   └── order.py             # 주문 파라미터 DTO + 주문번호 비교
├── external/                # 외부 API 통합
│   ├── kis_api.py           # KIS 국내 API (시세, 주문, 잔고, 체결)
│   ├── foreign_api.py       # KIS 해외 API (시세, 호가, 주문, 체결)
│   ├── http_client.py       # 범용 HTTP 클라이언트 (httpx 래퍼, 재시도)
│   ├── headers.py           # KIS API 헤더 생성
│   └── expo_push.py         # Expo 푸시 알림 발송
├── domain/                  # 도메인별 모듈
│   ├── routers/             # 라우터 통합 + 비도메인 라우터
│   │   ├── __init__.py      # 전체 라우터 등록
│   │   ├── backtest_router.py   # 백테스팅 API
│   │   └── health_router.py     # 헬스체크 API
│   ├── swing/               # 스윙 매매 도메인
│   │   ├── entity.py        # SwingTrade 엔티티 (SIGNAL 상태 전이)
│   │   ├── schemas.py       # Request/Response DTO
│   │   ├── repository.py    # 데이터 접근 계층
│   │   ├── service.py       # 비즈니스 로직 + 지표 캐시 워밍업
│   │   ├── router.py        # API 엔드포인트
│   │   ├── indicators.py    # 지표 캐시 스키마/계산
│   │   ├── tech_analysis.py # 기술 지표 계산 (TA-Lib)
│   │   ├── trading/         # 실시간 매매 실행
│   │   │   ├── auto_swing_batch.py       # 정기 매매 배치 (오케스트레이터)
│   │   │   ├── order_executor.py         # 주문 실행·체결 확인·TWAP 분할
│   │   │   ├── trading_strategy_factory.py
│   │   │   └── strategies/
│   │   │       ├── base_trading_strategy.py  # TradingStrategy 추상 클래스
│   │   │       ├── base_single_ema.py        # 단일 EMA 공통 로직/파라미터
│   │   │       └── single_ema_strategy.py    # 실전 전략 (유일)
│   │   └── backtest/        # 백테스팅 (실전 전략과 분리)
│   │       ├── backtest_service.py
│   │       ├── strategy_factory.py
│   │       └── strategies/
│   │           ├── base_strategy.py              # BacktestStrategy 추상 클래스
│   │           ├── ema_strategy.py               # A: 이평선
│   │           ├── ichimoku_strategy.py          # B: 일목균형표
│   │           └── single_ema_backtest_strategy.py  # S: 단일 20EMA
│   ├── stock/               # 종목 마스터 + 일별 데이터 수집 배치
│   │   ├── stock_data_batch.py       # 일별 OHLCV 수집
│   │   └── price_adjustment_batch.py # 액면분할 등 가격 보정
│   ├── trade_history/       # 체결 이력 (TRADE_HISTORY)
│   └── [domain]/            # user, account, auth, order, device,
│                            # notification, oauth, gemini
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

4. **매매 전략**: 전략 패턴. **실전과 백테스트가 별도 계층으로 분리**되어 있다.
   실전 전략은 `check_entry_signal`/`check_exit_signal`(비동기, Redis 상태 사용)을,
   백테스트 전략은 과거 봉 배열 기반 판정을 제공하므로 인터페이스가 다르다.

   **실전 매매** — `trading/trading_strategy_factory.py` → `TradingStrategy` 상속

   | SWING_TYPE | 전략 |
   |------------|------|
   | `S` | `SingleEMAStrategy` — 단일 20EMA + 단일 청산선 (**현재 유일한 실전 전략**) |
   | 그 외 | `SingleEMAStrategy` 폴백 (warning 로그) |

   **백테스트** — `backtest/strategy_factory.py` → `BacktestStrategy` 상속

   | 타입 | 전략 |
   |------|------|
   | `A` | `EMAStrategy` — EMA 골든크로스 (단기/중기/장기) |
   | `B` | `IchimokuStrategy` — 일목균형표 신호 |
   | `S` | `SingleEMABacktestStrategy` — 단일 20EMA (실전과 동일 정의) |

   `EMAStrategy`·`IchimokuStrategy`는 백테스트에만 등록되어 있어 실전 매매에서는
   호출되지 않는다. 실전 전략 추가 시 `TradingStrategyFactory._strategies`에 등록해야 한다.

5. **신호 흐름**: SWING_TRADE.SIGNAL 컬럼으로 상태 추적

   | SIGNAL | 의미 |
   |--------|------|
   | 0 | 매수 대기 |
   | 1 | 포지션 보유 (부분익절 전) |
   | 2 | 포지션 보유 (부분익절 후 잔여) |
   | 3 | 수급 이탈 대기 (매도 직후 쿨다운 1단계) |
   | 4 | 수급 재유입 대기 (쿨다운 2단계) |

   **상태 전이** (`domain/swing/entity.py`)

   | 전이 | 메서드 | 조건 |
   |------|--------|------|
   | 0 → 1 | `transition_to_buy` | 매수 체결 (진입은 1회뿐, 2차 매수 없음) |
   | 0 → 1 | `adopt_position` | 기존 보유 포지션 편입 (체결 이력·자금 차감 없음) |
   | 1 → 2 | `transition_to_partial` | 부분익절 50% 매도 (SIGNAL 1에서 1회만) |
   | 1/2 → 3 | `reset_cycle` | 청산선 이탈 전량 매도 → **0이 아니라 3으로 진입** |
   | 3 → 4 | `transition_to_reentry_waiting` | OBV z < `COOLDOWN_OBV_EXIT` (수급 이탈 확인) |
   | 4 → 0 | `transition_to_waiting` | OBV z > `COOLDOWN_OBV_REENTRY` (수급 재유입 확인) |

   매도는 차수가 아니라 **종류**로 구분한다 — `부분 매도`(1→2)와 `전량 매도`(1/2→3).
   전량 매도는 부분익절을 거치지 않아도 발생하므로 매도 횟수는 사이클당 1~2회다.
   매도 직후 SIGNAL 0으로 바로 돌아가지 않는 이유는 잔존 수급으로 진입 조건이
   즉시 재충족되는 것을 막기 위함이다 (`reset_cycle` docstring 참고).

### 데이터베이스 테이블

테이블 정의는 각 도메인의 `entity.py`에 있다 (중앙 `tables.py` 없음).

| 테이블 | 엔티티 | 정의 위치 | 용도 |
|--------|--------|-----------|------|
| `USER` | `User` | `domain/user/entity.py` | 사용자 |
| `USER_ID_SEQUENCE` | `UserIdSequence` | `domain/user/entity.py` | USER_ID 자동 생성 시퀀스 |
| `ACCOUNT` | `Account` | `domain/account/entity.py` | 증권 계좌 |
| `AUTH_KEY` | `Auth` | `domain/auth/entity.py` | KIS 인증키 (AES 암호화 저장) |
| `DEVICE` | `Device` | `domain/device/entity.py` | 디바이스 화이트리스트 |
| `USER_NOTI_SETTING` | `UserNotiSetting` | `domain/notification/entity.py` | 알림 설정 (유형별 1행) |
| `USER_PUSH_TOKEN` | `UserPushToken` | `domain/notification/entity.py` | Expo 푸시 토큰 |
| `STOCK_INFO` | `Stock` | `domain/stock/entity.py` | 종목 마스터 (국내/NYS/NAS/AMS) |
| `STOCK_DAY_HISTORY` | `StockHistory` | `domain/stock/entity.py` | 일별 OHLCV — **완성봉만 적재** |
| `SWING_TRADE` | `SwingTrade` | `domain/swing/entity.py` | 스윙 매매 설정 + SIGNAL 상태 |
| `EMA_OPT` | `EmaOption` | `domain/swing/entity.py` | 3-EMA 기간 설정 (아래 주의 참고) |
| `TRADE_HISTORY` | `TradeHistory` | `domain/trade_history/entity.py` | 체결 내역 + 실현손익 |

**주의사항**

- `STOCK_DAY_HISTORY`에는 **완성된 일봉만** 넣는다. 실시간 지표가 이 값을 전일 기준으로
  참조하므로, 장중/프리마켓 미완성 봉이 섞이면 지표가 당일 값을 선견(look-ahead)한다.
- `EMA_OPT`은 `SWING_TYPE='A'` 등록 시에만 저장되고(`swing/service.py`) **읽는 코드가 없다.**
  실전 전략이 `SingleEMAStrategy` 하나로 통합되면서 자체 상수를 쓰기 때문이다.
  3-EMA 전략을 실전에 다시 등록할 때까지는 사실상 미사용 테이블이다.
- `TRADE_HISTORY`는 `order_executor`만 저장한다. 배치는 매매 사유(`reasons`)만 넘기고
  체결 수량·단가를 아는 executor가 단독으로 기록한다 (저장 지점 단일화).

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

4. 코드 변경 진행 방식 (설명-동반 수정)
- (1) 먼저 구현 방향성을 제시하고 사용자 동의를 받는다.
- (2) 동의 후 곧장 적용하지 말고, 각 코드 수정(diff)마다 "이 변경이 무엇을/왜 하는지"를
      해당 변경에 붙여 설명한다. 사용자는 설명과 diff를 비교하며 이해한 뒤 accept한다.
- (3) 여러 파일·큰 변경은 한 번에 몰지 말고 이해 가능한 덩어리로 쪼개
      (설명 → diff → accept)를 반복한다.
- 자명한 변경(오타/포맷)은 생략 가능.