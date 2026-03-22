# Design: DDD Refactoring - ORM 모델 분리 + Entity 비즈니스 로직 통합

> Plan 문서: `docs/01-plan/features/ddd-refactoring.plan.md`

## 1. 설계 개요

### 핵심 변경
1. `database.py`의 ORM 모델을 각 도메인 `entity.py`로 이동
2. 기존 dataclass Entity를 제거하고 ORM 모델에 비즈니스 로직 통합
3. Strategy에서 `process_trading_cycle()` 제거, 신호 판단만 유지
4. `auto_swing_batch.py`를 오케스트레이터로 리팩토링
5. 데드코드 정리

## 2. 파일 변경 상세

### 2.1 database.py 변경

**Before** (219줄 — 모든 ORM 모델 + DB 연결):
```
app/common/database.py
  ├── UserIdSequenceModel
  ├── UserModel
  ├── AccountModel
  ├── AuthModel
  ├── StockModel
  ├── StockHistoryModel
  ├── SwingModel
  ├── EmaOptModel
  ├── TradeHistoryModel
  ├── DeviceModel
  ├── Base
  └── Database, get_db
```

**After** (DB 연결 설정만):
```python
# app/common/database.py
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Database:
    """데이터베이스 연결 관리 (싱글톤)"""
    # ... connect(), disconnect(), get_session() 유지

async def get_db():
    # ... 의존성 주입용 DB 세션 유지
```

`UserIdSequenceModel`은 User 도메인에 속하므로 `app/domain/user/entity.py`로 이동.

### 2.2 도메인별 entity.py 설계

각 도메인의 entity.py는 **ORM 모델 + 비즈니스 로직**을 포함합니다.
기존 dataclass Entity는 삭제하고 ORM 모델에 메서드를 추가합니다.

---

#### app/domain/swing/entity.py (핵심 변경)

```python
"""스윙 매매 도메인 엔티티 - ORM 모델 + 비즈니스 로직"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime, Sequence, UniqueConstraint
from datetime import datetime
from decimal import Decimal
from app.common.database import Base
from app.exceptions import ValidationError


class SwingTrade(Base):
    """스윙 매매 엔티티"""
    __tablename__ = "SWING_TRADE"
    __table_args__ = (
        UniqueConstraint('ACCOUNT_NO', 'MRKT_CODE', 'ST_CODE', name='uq_swing_account_stock'),
    )

    SWING_ID = Column(Integer, Sequence('swing_id_seq'), primary_key=True)
    ACCOUNT_NO = Column(String(50), nullable=False)
    MRKT_CODE = Column(String(50), nullable=False)
    ST_CODE = Column(String(50), nullable=False)
    USE_YN = Column(CHAR(1), nullable=False, default='N')
    INIT_AMOUNT = Column(DECIMAL(15, 2), nullable=False)
    CUR_AMOUNT = Column(DECIMAL(15, 2), nullable=False)
    SWING_TYPE = Column(CHAR(1), nullable=False)
    BUY_RATIO = Column(Integer, nullable=False)
    SELL_RATIO = Column(Integer, nullable=False)
    SIGNAL = Column(Integer, nullable=False, default=0)
    ENTRY_PRICE = Column(DECIMAL(15, 2), nullable=True)
    HOLD_QTY = Column(Integer, nullable=True, default=0)
    EOD_SIGNALS = Column(String(500), nullable=True)
    PEAK_PRICE = Column(DECIMAL(15, 2), nullable=True)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)

    # ==================== 검증 ====================

    def validate(self) -> None:
        """스윙 설정 유효성 검증"""
        if not self.ACCOUNT_NO:
            raise ValidationError("계좌번호는 필수입니다")
        if not self.MRKT_CODE:
            raise ValidationError("시장코드는 필수입니다")
        if not self.ST_CODE:
            raise ValidationError("종목코드는 필수입니다")
        if self.SWING_TYPE not in ('S', 'A', 'B'):
            raise ValidationError("스윙 타입은 [S,A,B]여야 합니다")
        if not (0 <= self.BUY_RATIO <= 100):
            raise ValidationError("매수 비율은 0~100 사이여야 합니다")
        if not (0 <= self.SELL_RATIO <= 100):
            raise ValidationError("매도 비율은 0~100 사이여야 합니다")

    # ==================== 상태 조회 ====================

    def is_waiting(self) -> bool:
        return self.SIGNAL == 0

    def is_first_buy_done(self) -> bool:
        return self.SIGNAL == 1

    def is_second_buy_done(self) -> bool:
        return self.SIGNAL == 2

    def is_primary_sold(self) -> bool:
        return self.SIGNAL == 3

    def has_position(self) -> bool:
        return self.SIGNAL in (1, 2, 3)

    # ==================== 상태 전환 ====================

    def transition_to_first_buy(self, entry_price: int, hold_qty: int, peak_price: int) -> None:
        """1차 매수 완료 (SIGNAL 0 -> 1)"""
        if self.SIGNAL != 0:
            raise ValidationError(f"1차 매수는 대기 상태(0)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 1
        self.ENTRY_PRICE = Decimal(entry_price)
        self.HOLD_QTY = hold_qty
        self.PEAK_PRICE = Decimal(peak_price)
        self.MOD_DT = datetime.now()

    def transition_to_second_buy(self, new_entry_price: int, total_hold_qty: int) -> None:
        """2차 매수 완료 (SIGNAL 1 -> 2)"""
        if self.SIGNAL != 1:
            raise ValidationError(f"2차 매수는 1차 매수 상태(1)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 2
        self.ENTRY_PRICE = Decimal(new_entry_price)
        self.HOLD_QTY = total_hold_qty
        self.MOD_DT = datetime.now()

    def transition_to_primary_sell(self, remaining_qty: int) -> None:
        """1차 분할 매도 완료 (SIGNAL 1,2 -> 3)"""
        if self.SIGNAL not in (1, 2):
            raise ValidationError(f"1차 매도는 매수 상태(1,2)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 3
        self.HOLD_QTY = remaining_qty
        self.MOD_DT = datetime.now()

    def transition_to_reentry(self, new_entry_price: int, total_hold_qty: int, peak_price: int) -> None:
        """재진입 매수 완료 (SIGNAL 3 -> 1)"""
        if self.SIGNAL != 3:
            raise ValidationError(f"재진입은 1차 매도 상태(3)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 1
        self.ENTRY_PRICE = Decimal(new_entry_price)
        self.HOLD_QTY = total_hold_qty
        self.PEAK_PRICE = Decimal(peak_price)
        self.MOD_DT = datetime.now()

    def reset_cycle(self) -> None:
        """사이클 종료 — 전량 매도 후 초기화 (SIGNAL -> 0)"""
        self.SIGNAL = 0
        self.ENTRY_PRICE = None
        self.HOLD_QTY = 0
        self.PEAK_PRICE = None
        self.MOD_DT = datetime.now()

    def update_peak_price(self, current_high: int) -> None:
        """장중 고가 갱신"""
        if self.has_position() and current_high > (int(self.PEAK_PRICE) if self.PEAK_PRICE else 0):
            self.PEAK_PRICE = Decimal(current_high)

    def update_hold_qty_partial(self, sold_qty: int) -> None:
        """부분 체결 시 보유 수량 차감"""
        self.HOLD_QTY = (self.HOLD_QTY or 0) - sold_qty
        self.MOD_DT = datetime.now()

    # ==================== 팩토리 ====================

    @classmethod
    def create(cls, account_no: str, mrkt_code: str, st_code: str,
               init_amount: Decimal, swing_type: str,
               buy_ratio: int = 70, sell_ratio: int = 50) -> "SwingTrade":
        """새 스윙 매매 생성"""
        swing = cls(
            ACCOUNT_NO=account_no,
            MRKT_CODE=mrkt_code,
            ST_CODE=st_code,
            INIT_AMOUNT=init_amount,
            CUR_AMOUNT=init_amount,
            SWING_TYPE=swing_type,
            BUY_RATIO=buy_ratio,
            SELL_RATIO=sell_ratio
        )
        swing.validate()
        return swing


class EmaOption(Base):
    """이평선 옵션 엔티티"""
    __tablename__ = "EMA_OPT"

    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True)
    ST_CODE = Column(String(50), nullable=False, primary_key=True)
    SHORT_TERM = Column(Integer, nullable=False)
    MEDIUM_TERM = Column(Integer, nullable=False)
    LONG_TERM = Column(Integer, nullable=False)

    def validate(self) -> None:
        if not (1 <= self.SHORT_TERM < self.MEDIUM_TERM < self.LONG_TERM):
            raise ValidationError("이평선 기간은 단기 < 중기 < 장기 순이어야 합니다")
```

**변경 요약**:
- `SwingModel` → `SwingTrade`로 클래스명 변경 (Entity 역할 명확화)
- 기존 dataclass 삭제, ORM 모델에 비즈니스 로직 통합
- `transition_to_*` 메서드에 관련 필드(`ENTRY_PRICE`, `HOLD_QTY`, `PEAK_PRICE`) 업데이트 포함
- `reset_cycle()` 추가 (전량 매도 시 모든 필드 초기화)
- `EmaOptModel` → `EmaOption`으로 클래스명 변경

---

#### app/domain/user/entity.py

```python
"""사용자 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime
from datetime import datetime
from app.common.database import Base


class UserIdSequence(Base):
    """USER_ID 시퀀스 테이블"""
    __tablename__ = "USER_ID_SEQUENCE"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now)


class User(Base):
    """사용자 엔티티"""
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True)
    USER_NAME = Column(String(50), nullable=False)
    EMAIL = Column(String(100), nullable=True, unique=True)
    PHONE = Column(CHAR(11), nullable=True)
    GOOGLE_ACCESS_TOKEN = Column(String(2000), nullable=True)
    GOOGLE_REFRESH_TOKEN = Column(String(500), nullable=True)
    GOOGLE_TOKEN_EXPIRES_AT = Column(DateTime, nullable=True)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)

    # 비즈니스 로직 (현재는 없음 — 기존 dataclass 메서드는 미사용이므로 제거)
```

**변경 요약**: `UserModel` → `User`, `UserIdSequenceModel` → `UserIdSequence`. 기존 dataclass의 `update_profile()`, `validate_phone()`, `create_oauth_user()`는 Service에서 직접 처리 중이므로 제거.

---

#### app/domain/account/entity.py

```python
"""계좌 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, DateTime, Sequence
from datetime import datetime
from app.common.database import Base


class Account(Base):
    """계좌 엔티티"""
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True)
    USER_ID = Column(String(50), nullable=False, primary_key=True)
    ACCOUNT_NO = Column(String(10), nullable=False)
    AUTH_ID = Column(Integer, nullable=False)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)
```

**변경 요약**: `AccountModel` → `Account`. 기존 dataclass 메서드(`get_cano`, `get_acnt_prdt_cd`, `update_auth`)는 미사용이므로 제거. 필요 시 추후 추가.

---

#### app/domain/auth/entity.py

```python
"""인증키 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime, Sequence
from datetime import datetime
from app.common.database import Base
from app.exceptions import ValidationError


class Auth(Base):
    """인증키 엔티티"""
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True)
    USER_ID = Column(String(50), nullable=False, primary_key=True)
    AUTH_NAME = Column(String(50), nullable=False)
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False)
    API_KEY = Column(String(200), nullable=False)
    SECRET_KEY = Column(String(350), nullable=False)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)

    def validate(self) -> None:
        if not self.API_KEY:
            raise ValidationError("API KEY는 필수입니다")
        if not self.SECRET_KEY:
            raise ValidationError("SECRET KEY는 필수입니다")
        if self.SIMULATION_YN not in ('Y', 'N'):
            raise ValidationError("모의투자 여부는 Y/N이어야 합니다")
```

**변경 요약**: `AuthModel` → `Auth`. `validate()` 유지 (Service에서 사용 가능). `is_simulation()`, `update()` 미사용이므로 제거.

---

#### app/domain/stock/entity.py

```python
"""주식 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime
from datetime import datetime
from app.common.database import Base


class Stock(Base):
    """종목 정보 엔티티"""
    __tablename__ = "STOCK_INFO"

    MRKT_CODE = Column(String(50), nullable=False, primary_key=True)
    ST_CODE = Column(String(50), nullable=False, primary_key=True)
    SD_CODE = Column(String(50), nullable=False)
    ST_NM = Column(String(100), nullable=False)
    DATA_YN = Column(CHAR(1), nullable=False, default='N')
    DEL_YN = Column(CHAR(1), nullable=False, default='N')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)


class StockHistory(Base):
    """주식 일별 데이터 엔티티"""
    __tablename__ = "STOCK_DAY_HISTORY"

    MRKT_CODE = Column(String(50), nullable=False, primary_key=True)
    ST_CODE = Column(String(50), nullable=False, primary_key=True)
    STCK_BSOP_DATE = Column(String(8), nullable=False, primary_key=True)
    STCK_OPRC = Column(DECIMAL(15, 2), nullable=False)
    STCK_HGPR = Column(DECIMAL(15, 2), nullable=False)
    STCK_LWPR = Column(DECIMAL(15, 2), nullable=False)
    STCK_CLPR = Column(DECIMAL(15, 2), nullable=False)
    ACML_VOL = Column(Integer, nullable=False)
    FRGN_NTBY_QTY = Column(Integer, nullable=True)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)
```

**변경 요약**: `StockModel` → `Stock`, `StockHistoryModel` → `StockHistory`. 기존 dataclass 메서드(`is_data_loaded`, `get_price_change` 등) 모두 미사용이므로 제거.

---

#### app/domain/trade_history/entity.py (신규)

```python
"""거래 내역 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime, Sequence
from datetime import datetime
from app.common.database import Base


class TradeHistory(Base):
    """거래 내역 엔티티"""
    __tablename__ = "TRADE_HISTORY"

    TRADE_ID = Column(Integer, Sequence('trade_id_seq'), primary_key=True)
    SWING_ID = Column(Integer, nullable=False)
    TRADE_DATE = Column(DateTime, nullable=False)
    TRADE_TYPE = Column(CHAR(1), nullable=False)
    TRADE_PRICE = Column(DECIMAL(15, 2), nullable=False)
    TRADE_QTY = Column(Integer, nullable=False)
    TRADE_AMOUNT = Column(DECIMAL(15, 2), nullable=False)
    TRADE_REASONS = Column(String(500), nullable=True)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
```

**변경 요약**: `TradeHistoryModel` → `TradeHistory`. 현재 trade_history 도메인에는 entity.py가 없으므로 신규 생성.

---

#### app/domain/device/entity.py (신규)

```python
"""디바이스 도메인 엔티티"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime
from datetime import datetime
from app.common.database import Base


class Device(Base):
    """디바이스 화이트리스트 엔티티"""
    __tablename__ = "DEVICE"

    DEVICE_ID = Column(String(100), primary_key=True)
    DEVICE_NAME = Column(String(100), nullable=False)
    USER_ID = Column(String(50), nullable=True)
    ACTIVE_YN = Column(CHAR(1), default='Y', nullable=False)
    REG_DT = Column(DateTime, default=datetime.now, nullable=False)
    MOD_DT = Column(DateTime)
```

**변경 요약**: `DeviceModel` → `Device`. 기존에 entity.py 없었으므로 신규 생성.

---

#### app/domain/order/entity.py

Order 도메인은 주문 API 삭제 후 잔존한 코드입니다. 현재 `Order`, `ModifyOrder` dataclass와 `OrderResponse`, `CancelableOrderResponse` 스키마 모두 미사용입니다.

**결정**: order 도메인에는 ORM 모델이 없으므로 (주문은 KIS API 직접 호출), entity.py를 빈 파일로 유지하거나 도메인 폴더 자체를 삭제합니다. (KIS API 주문 파라미터 검증이 필요하면 추후 추가)

---

### 2.3 Import 경로 변경

모든 파일에서 `from app.common.database import XxxModel`을 `from app.domain.xxx.entity import Xxx`로 변경합니다.

| Before | After |
|--------|-------|
| `from app.common.database import SwingModel` | `from app.domain.swing.entity import SwingTrade` |
| `from app.common.database import EmaOptModel` | `from app.domain.swing.entity import EmaOption` |
| `from app.common.database import UserModel` | `from app.domain.user.entity import User` |
| `from app.common.database import UserIdSequenceModel` | `from app.domain.user.entity import UserIdSequence` |
| `from app.common.database import AccountModel` | `from app.domain.account.entity import Account` |
| `from app.common.database import AuthModel` | `from app.domain.auth.entity import Auth` |
| `from app.common.database import StockModel` | `from app.domain.stock.entity import Stock` |
| `from app.common.database import StockHistoryModel` | `from app.domain.stock.entity import StockHistory` |
| `from app.common.database import TradeHistoryModel` | `from app.domain.trade_history.entity import TradeHistory` |
| `from app.common.database import DeviceModel` | `from app.domain.device.entity import Device` |
| `from app.common.database import Database, get_db, Base` | 변경 없음 (database.py에 유지) |

**크로스 도메인 import** (JOIN 쿼리용):
- `swing/repository.py`에서 `Stock` import (종목명 JOIN) — 허용 (Repository 계층에서 다른 도메인 Entity 참조는 READ 목적으로 허용)
- `trade_history/repository.py`에서 `Account`, `SwingTrade` import (JOIN 쿼리) — 허용

### 2.4 Repository 변경

Entity가 곧 ORM 모델이므로, Repository에서 Entity → Model 변환 로직이 불필요해집니다.

**Before** (swing/repository.py:66-83):
```python
async def save(self, swing: SwingTrade) -> SwingModel:
    db_swing = SwingModel(                    # Entity → Model 수동 변환
        ACCOUNT_NO=swing.account_no,          # 소문자 → 대문자 매핑
        ST_CODE=swing.st_code,
        ...
    )
    self.db.add(db_swing)
    await self.db.flush()
    return db_swing
```

**After**:
```python
async def save(self, swing: SwingTrade) -> SwingTrade:
    self.db.add(swing)                        # 변환 불필요, 바로 저장
    await self.db.flush()
    await self.db.refresh(swing)
    return swing
```

### 2.5 Strategy 변경 — process_trading_cycle() 제거

**Before**: `base_trading_strategy.py` (860줄)
- `process_trading_cycle()` — 700줄, 신호 판단 + 주문 실행 + 상태 결정

**After**: Strategy는 신호 판단 메서드만 유지
```python
class TradingStrategy(ABC):
    # 유지 (신호 판단)
    check_entry_signal()          # 1차 매수 진입 신호
    check_exit_signal()           # 손절 신호
    check_second_buy_signal()     # 2차 매수 신호
    check_trailing_stop_signal()  # Trailing stop 신호
    get_cached_indicators()       # 캐시 지표 조회

    # 삭제
    process_trading_cycle()       # 오케스트레이터로 이동
```

### 2.6 auto_swing_batch.py 리팩토링 — 오케스트레이터 역할

`process_trading_cycle()` 700줄의 로직을 `auto_swing_batch.py`로 이동하되, Entity 메서드를 사용하여 상태 전환합니다.

```python
async def process_single_swing(swing: SwingTrade, strategy, redis_client, db):
    """오케스트레이터: 판단 -> 실행 -> Entity 상태 전환 -> 저장"""

    # 부분 체결 진행 중이면 우선 처리 (기존 로직 유지)
    if await _handle_partial_execution(swing, redis_client, db):
        return

    # 장중 고가 갱신
    swing.update_peak_price(current_high)

    # SIGNAL별 분기
    if swing.is_waiting():
        await _handle_signal_0(swing, strategy, ...)

    elif swing.is_first_buy_done():
        await _handle_signal_1(swing, strategy, ...)

    elif swing.is_second_buy_done():
        await _handle_signal_2(swing, strategy, ...)

    elif swing.is_primary_sold():
        await _handle_signal_3(swing, strategy, ...)

    # 변경사항 저장
    await db.flush()
    await db.commit()


async def _handle_signal_0(swing: SwingTrade, strategy, executor, ...):
    """SIGNAL 0: 대기 -> 1차 매수 체크"""
    entry = await strategy.check_entry_signal(...)
    if entry and entry["action"] == "BUY":
        order = await executor.execute_buy_with_partial(...)
        if order["success"] and order.get("completed", True):
            swing.transition_to_first_buy(              # Entity 메서드 호출
                entry_price=order["avg_price"],
                hold_qty=order["qty"],
                peak_price=int(current_price)
            )
        await trade_service.record_trade(...)


async def _handle_signal_1(swing: SwingTrade, strategy, executor, ...):
    """SIGNAL 1: 1차 매수 완료 -> 손절/trailing stop/2차 매수"""
    # 1. 손절 체크
    exit_result = await strategy.check_exit_signal(...)
    if exit_result and exit_result["action"] == "SELL":
        order = await executor.execute_sell_with_partial(...)
        if order["success"] and order.get("completed", True):
            swing.reset_cycle()                         # Entity 메서드 호출
        return

    # 2. Trailing stop 체크
    ts_result = await strategy.check_trailing_stop_signal(...)
    if ts_result and ts_result["action"] == "SELL_PRIMARY":
        order = await executor.execute_sell_with_partial(...)
        if order["success"] and order.get("completed", True):
            swing.transition_to_primary_sell(           # Entity 메서드 호출
                remaining_qty=swing.HOLD_QTY - order["qty"]
            )
        return

    if ts_result and ts_result["action"] == "SELL_ALL":
        order = await executor.execute_sell_with_partial(...)
        if order["success"] and order.get("completed", True):
            swing.reset_cycle()                         # Entity 메서드 호출
        return

    # 3. 2차 매수 체크
    entry = await strategy.check_second_buy_signal(...)
    if entry and entry["action"] == "BUY":
        order = await executor.execute_buy_with_partial(...)
        if order["success"] and order.get("completed", True):
            new_avg = calculate_avg_entry_price(...)
            swing.transition_to_second_buy(             # Entity 메서드 호출
                new_entry_price=new_avg,
                total_hold_qty=swing.HOLD_QTY + order["qty"]
            )
```

## 3. 구현 순서

```
Step 1: database.py에서 Base/Database/get_db만 남기고 ORM 모델 분리
        각 도메인 entity.py에 ORM 모델 이동 + 클래스명 변경
        모든 import 경로 변경

Step 2: swing/entity.py에 비즈니스 로직 통합
        SwingTrade에 transition_to_*, reset_cycle 등 메서드 추가
        기존 dataclass 내용 삭제

Step 3: swing/repository.py 정리
        Entity → Model 변환 로직 제거 (SwingTrade가 곧 ORM 모델)
        미사용 메서드 삭제

Step 4: base_trading_strategy.py에서 process_trading_cycle() 제거

Step 5: auto_swing_batch.py를 오케스트레이터로 리팩토링
        _handle_signal_0/1/2/3 헬퍼 함수 구현
        Entity 메서드 호출로 상태 전환

Step 6: 기타 도메인 데드코드 정리
        미사용 entity dataclass 메서드 정리 (user, account, stock 등)
        미사용 schema 클래스, external API 함수 삭제
```

## 4. 영향 범위 (import 변경 파일 목록)

| 파일 | 변경 내용 |
|------|----------|
| `app/common/database.py` | ORM 모델 제거, Base/Database/get_db만 유지 |
| `app/common/__init__.py` | Model export 제거 |
| `app/domain/swing/repository.py` | `SwingModel` → `SwingTrade`, `EmaOptModel` → `EmaOption`, Entity 변환 로직 제거 |
| `app/domain/swing/service.py` | `SwingTrade` import 경로 변경, dataclass Entity 사용 제거 |
| `app/domain/swing/trading/auto_swing_batch.py` | 오케스트레이터 리팩토링 |
| `app/domain/swing/trading/strategies/base_trading_strategy.py` | `process_trading_cycle()` 제거 |
| `app/domain/user/repository.py` | `UserModel` → `User`, `UserIdSequenceModel` → `UserIdSequence` |
| `app/domain/account/repository.py` | `AccountModel` → `Account`, `AuthModel` → `Auth` |
| `app/domain/auth/repository.py` | `AuthModel` → `Auth` |
| `app/domain/stock/repository.py` | `StockModel` → `Stock`, `StockHistoryModel` → `StockHistory` |
| `app/domain/trade_history/repository.py` | `TradeHistoryModel` → `TradeHistory`, `AccountModel` → `Account`, `SwingModel` → `SwingTrade` |
| `app/domain/device/repository.py` | `DeviceModel` → `Device` |
| `app/main.py` | `Database` import 유지 (변경 없음) |

## 5. 삭제 대상 정리

### 데드코드 삭제
- `app/domain/swing/entity.py` — 기존 dataclass 전체 (ORM 모델로 대체)
- `app/domain/user/entity.py` — 기존 dataclass 전체
- `app/domain/account/entity.py` — 기존 dataclass 전체
- `app/domain/auth/entity.py` — 기존 dataclass 전체 (validate만 ORM에 이식)
- `app/domain/stock/entity.py` — 기존 dataclass 전체
- `app/domain/order/entity.py` — 기존 dataclass 전체 (주문 API 삭제됨)
- `app/domain/order/schemas.py` — `OrderResponse`, `CancelableOrderResponse`
- `app/domain/swing/schemas.py` — `SwingMappingResponse` (미사용)
- `app/external/kis_api.py` — `get_approval()`, `get_balance()`, `get_inquire_daily_ccld_obj()`
- `app/domain/trade_history/service.py` — `get_latest_buy()`, `get_latest_sell()`
- `app/domain/trade_history/repository.py` — `find_by_id()`
- `app/domain/swing/service.py` — `get_holding_swings()`, `get_eod_target_swings()`
- `app/domain/swing/repository.py` — `find_by_account()`, `find_pending_sell_swings()`, `reset_signals_by_value()`
- `app/domain/stock/repository.py` — `get_foreign_net_buy_sum()`, `get_stock_volume_sum()`
- `app/domain/device/repository.py` — `find_all()`, `find_by_user()`, `update()`, `delete()`, `exists()`
- `app/core/response.py` — `paginated_response()`
- `app/domain/stock/stock_data_batch.py` — `get_batch_status()`

## 6. 변경하지 않는 것

- `app/common/database.py`의 `Database`, `get_db`, `Base` — 위치 유지
- Strategy의 `check_entry_signal()`, `check_exit_signal()`, `check_second_buy_signal()`, `check_trailing_stop_signal()` — 역할 유지
- `SwingOrderExecutor` — 주문 실행 로직 유지 (호출 위치만 Strategy → Batch로 변경)
- 스케줄러 설정 (`scheduler.py`) — 변경 없음
- Redis 캐시 구조 — 변경 없음
- API 라우터/스키마 — 변경 없음 (import 경로만 변경)