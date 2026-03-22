# Design: push-noti (푸쉬 알림)

> Plan 문서: `docs/01-plan/features/push-noti.plan.md`

## 1. 아키텍처 개요

```
┌──────────────────────────────────────────────────────────────┐
│ Expo App (Frontend)                                          │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐ │
│  │ notifications │  │ expo-notifica- │  │ backEndApi.ts    │ │
│  │ .tsx (설정UI) │  │ tions (토큰)   │  │ (API 호출)       │ │
│  └──────┬───────┘  └───────┬────────┘  └────────┬─────────┘ │
└─────────┼──────────────────┼───────────────────┼────────────┘
          │ PUT              │ POST              │ GET
          │ /notification-   │ /push-token       │ /notification-
          │ settings         │                   │ settings
┌─────────┼──────────────────┼───────────────────┼────────────┐
│ FastAPI Backend                                              │
│  ┌──────▼──────────────────▼───────────────────▼─────────┐  │
│  │              notification/router.py                    │  │
│  │  GET/PUT /users/notification-settings                  │  │
│  │  POST/DELETE /users/push-token                         │  │
│  └──────────────────────┬────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────▼────────────────────────────────┐  │
│  │           notification/service.py                      │  │
│  │  NotificationSettingService (설정 CRUD)                │  │
│  │  PushNotificationService (알림 전송)                   │  │
│  └───────────┬──────────────────────┬────────────────────┘  │
│              │                      │                        │
│  ┌───────────▼──────────┐  ┌───────▼────────────────────┐  │
│  │ notification/         │  │ external/expo_push.py      │  │
│  │ repository.py (DB)    │  │ (Expo Push API 호출)       │  │
│  └───────────┬──────────┘  └───────┬────────────────────┘  │
│              │                      │                        │
│  ┌───────────▼──────────┐  ┌───────▼────────────────────┐  │
│  │ MySQL                 │  │ https://exp.host/--/api/   │  │
│  │ USER_NOTI_SETTING     │  │ v2/push/send               │  │
│  │ USER_PUSH_TOKEN       │  │ (Expo Push Service)        │  │
│  └──────────────────────┘  └────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ auto_swing_batch.py / order_executor.py               │   │
│  │  매수/매도 체결 → PushNotificationService 호출        │   │
│  │  (asyncio.create_task로 fire-and-forget)              │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## 2. DB 테이블 설계

### 2-1. USER_NOTI_SETTING

```sql
CREATE TABLE USER_NOTI_SETTING (
    USER_ID     VARCHAR(50) NOT NULL COMMENT '사용자 ID',
    BUY_NOTI_YN CHAR(1)     NOT NULL DEFAULT 'N' COMMENT '매수 알림 여부',
    SELL_NOTI_YN CHAR(1)    NOT NULL DEFAULT 'N' COMMENT '매도 알림 여부',
    REG_DT      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '등록일',
    MOD_DT      DATETIME    NULL COMMENT '수정일',
    PRIMARY KEY (USER_ID)
) COMMENT '사용자 알림 설정';
```

### 2-2. USER_PUSH_TOKEN

```sql
CREATE TABLE USER_PUSH_TOKEN (
    TOKEN_ID    INT          NOT NULL AUTO_INCREMENT COMMENT '토큰 ID',
    USER_ID     VARCHAR(50)  NOT NULL COMMENT '사용자 ID',
    PUSH_TOKEN  VARCHAR(200) NOT NULL COMMENT 'Expo Push Token',
    DEVICE_TYPE VARCHAR(20)  NULL COMMENT 'ios / android',
    ACTIVE_YN   CHAR(1)      NOT NULL DEFAULT 'Y' COMMENT '활성 여부',
    REG_DT      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '등록일',
    MOD_DT      DATETIME     NULL COMMENT '수정일',
    PRIMARY KEY (TOKEN_ID),
    UNIQUE KEY uq_user_push_token (USER_ID, PUSH_TOKEN)
) COMMENT '사용자 푸쉬 토큰';
```

## 3. 파일 구조

### 3-1. 신규 파일

```
app/domain/notification/
├── __init__.py
├── entity.py              # UserNotiSetting, UserPushToken ORM 엔티티
├── schemas.py             # Request/Response DTO
├── repository.py          # DB 접근 계층
├── service.py             # NotificationSettingService + PushNotificationService
└── router.py              # API 엔드포인트

app/external/
└── expo_push.py           # Expo Push API 호출 (http_client.py 활용)
```

### 3-2. 수정 파일

| 파일 | 수정 내용 |
|------|----------|
| `app/domain/routers/__init__.py` | notification_router import 추가 |
| `app/main.py` | notification_router include 추가 |
| `app/common/database.py` | `_import_all_entities()`에 notification.entity 추가 |
| `app/domain/swing/trading/auto_swing_batch.py` | 체결 완료 시 알림 전송 호출 |

## 4. Entity 설계

### 4-1. UserNotiSetting (`notification/entity.py`)

```python
from sqlalchemy import Column, String, CHAR, DateTime
from datetime import datetime
from app.common.database import Base


class UserNotiSetting(Base):
    """사용자 알림 설정 엔티티"""
    __tablename__ = "USER_NOTI_SETTING"

    USER_ID = Column(String(50), primary_key=True, comment='사용자 ID')
    BUY_NOTI_YN = Column(CHAR(1), nullable=False, default='N', comment='매수 알림 여부')
    SELL_NOTI_YN = Column(CHAR(1), nullable=False, default='N', comment='매도 알림 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')
```

### 4-2. UserPushToken (`notification/entity.py`)

```python
from sqlalchemy import Column, Integer, String, CHAR, DateTime, Sequence, UniqueConstraint

class UserPushToken(Base):
    """사용자 푸쉬 토큰 엔티티"""
    __tablename__ = "USER_PUSH_TOKEN"
    __table_args__ = (
        UniqueConstraint('USER_ID', 'PUSH_TOKEN', name='uq_user_push_token'),
    )

    TOKEN_ID = Column(Integer, Sequence('push_token_id_seq'), primary_key=True, comment='토큰 ID')
    USER_ID = Column(String(50), nullable=False, comment='사용자 ID')
    PUSH_TOKEN = Column(String(200), nullable=False, comment='Expo Push Token')
    DEVICE_TYPE = Column(String(20), nullable=True, comment='ios / android')
    ACTIVE_YN = Column(CHAR(1), nullable=False, default='Y', comment='활성 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')
```

## 5. Schemas 설계 (`notification/schemas.py`)

```python
from pydantic import BaseModel, ConfigDict
from typing import Optional


class NotiSettingResponse(BaseModel):
    """알림 설정 조회 응답"""
    BUY_NOTI_YN: str
    SELL_NOTI_YN: str

    model_config = ConfigDict(from_attributes=True)


class NotiSettingUpdateRequest(BaseModel):
    """알림 설정 변경 요청"""
    BUY_NOTI_YN: str   # 'Y' or 'N'
    SELL_NOTI_YN: str   # 'Y' or 'N'


class PushTokenRegisterRequest(BaseModel):
    """푸쉬 토큰 등록 요청"""
    PUSH_TOKEN: str             # ExponentPushToken[xxx]
    DEVICE_TYPE: Optional[str] = None  # 'ios' or 'android'


class PushTokenDeleteRequest(BaseModel):
    """푸쉬 토큰 삭제 요청"""
    PUSH_TOKEN: str
```

## 6. Repository 설계 (`notification/repository.py`)

```python
class NotificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 알림 설정 ──
    async def find_setting_by_user_id(self, user_id: str) -> UserNotiSetting | None
    async def save_setting(self, setting: UserNotiSetting) -> UserNotiSetting
    async def update_setting(self, user_id: str, data: dict) -> UserNotiSetting | None

    # ── 푸쉬 토큰 ──
    async def find_active_tokens_by_user_id(self, user_id: str) -> list[UserPushToken]
    async def find_token(self, user_id: str, push_token: str) -> UserPushToken | None
    async def save_token(self, token: UserPushToken) -> UserPushToken
    async def deactivate_token(self, user_id: str, push_token: str) -> None
    async def deactivate_tokens_by_push_token(self, push_token: str) -> None
        """DeviceNotRegistered 에러 시 토큰 비활성화"""
```

> Repository는 flush만 수행, commit은 Service에서.

## 7. Service 설계

### 7-1. NotificationSettingService (`notification/service.py`)

알림 설정 CRUD 담당. Router에서 호출.

```python
class NotificationSettingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = NotificationRepository(db)

    async def get_settings(self, user_id: str) -> dict:
        """알림 설정 조회 (없으면 기본값 반환)"""
        setting = await self.repo.find_setting_by_user_id(user_id)
        if not setting:
            return {"BUY_NOTI_YN": "N", "SELL_NOTI_YN": "N"}
        return NotiSettingResponse.model_validate(setting).model_dump()

    async def update_settings(self, user_id: str, request: NotiSettingUpdateRequest) -> dict:
        """알림 설정 변경 (없으면 생성, 있으면 업데이트)"""
        # upsert 패턴
        setting = await self.repo.find_setting_by_user_id(user_id)
        if setting:
            setting.BUY_NOTI_YN = request.BUY_NOTI_YN
            setting.SELL_NOTI_YN = request.SELL_NOTI_YN
            setting.MOD_DT = datetime.now()
        else:
            setting = UserNotiSetting(
                USER_ID=user_id,
                BUY_NOTI_YN=request.BUY_NOTI_YN,
                SELL_NOTI_YN=request.SELL_NOTI_YN
            )
            await self.repo.save_setting(setting)
        await self.db.commit()
        return NotiSettingResponse.model_validate(setting).model_dump()

    async def register_push_token(self, user_id: str, request: PushTokenRegisterRequest) -> dict:
        """푸쉬 토큰 등록 (이미 존재하면 활성화)"""
        existing = await self.repo.find_token(user_id, request.PUSH_TOKEN)
        if existing:
            existing.ACTIVE_YN = 'Y'
            existing.MOD_DT = datetime.now()
        else:
            token = UserPushToken(
                USER_ID=user_id,
                PUSH_TOKEN=request.PUSH_TOKEN,
                DEVICE_TYPE=request.DEVICE_TYPE
            )
            await self.repo.save_token(token)
        await self.db.commit()
        return {"success": True}

    async def delete_push_token(self, user_id: str, request: PushTokenDeleteRequest) -> dict:
        """푸쉬 토큰 비활성화 (로그아웃 시)"""
        await self.repo.deactivate_token(user_id, request.PUSH_TOKEN)
        await self.db.commit()
        return {"success": True}
```

### 7-2. PushNotificationService (`notification/service.py`)

매매 체결 시 푸쉬 알림 전송 담당. `auto_swing_batch.py`에서 호출.

```python
class PushNotificationService:
    """푸쉬 알림 전송 서비스 (fire-and-forget 사용)"""

    @staticmethod
    async def send_trade_notification(
        user_id: str,
        trade_type: str,       # "B" (매수) or "S" (매도)
        st_code: str,
        qty: int,
        price: int,
        reasons: list[str] | None = None,
    ) -> None:
        """
        매매 체결 푸쉬 알림 전송

        1. DB에서 사용자 알림 설정 확인 (BUY_NOTI_YN / SELL_NOTI_YN)
        2. 설정이 'Y'이면 활성 토큰 목록 조회
        3. Expo Push API로 알림 전송
        4. DeviceNotRegistered 에러 시 토큰 비활성화
        """
        from app.common.database import Database

        db = await Database.get_session()
        try:
            repo = NotificationRepository(db)

            # 1. 알림 설정 확인
            setting = await repo.find_setting_by_user_id(user_id)
            if not setting:
                return

            if trade_type == "B" and setting.BUY_NOTI_YN != 'Y':
                return
            if trade_type == "S" and setting.SELL_NOTI_YN != 'Y':
                return

            # 2. 활성 토큰 조회
            tokens = await repo.find_active_tokens_by_user_id(user_id)
            if not tokens:
                logger.debug(f"[{user_id}] 활성 푸쉬 토큰 없음, 알림 건너뜀")
                return

            # 3. 알림 메시지 생성
            title, body = _build_trade_message(trade_type, st_code, qty, price, reasons)

            # 4. Expo Push API 전송
            push_tokens = [t.PUSH_TOKEN for t in tokens]
            failed_tokens = await send_expo_push(push_tokens, title, body, data={
                "type": "trade",
                "trade_type": trade_type,
                "st_code": st_code,
            })

            # 5. 실패한 토큰 비활성화
            for token in failed_tokens:
                await repo.deactivate_tokens_by_push_token(token)
            if failed_tokens:
                await db.commit()

        except Exception as e:
            logger.error(f"[{user_id}] 푸쉬 알림 전송 실패: {e}", exc_info=True)
        finally:
            await db.close()


def _build_trade_message(
    trade_type: str, st_code: str, qty: int, price: int,
    reasons: list[str] | None
) -> tuple[str, str]:
    """알림 메시지 생성"""
    type_label = "매수" if trade_type == "B" else "매도"
    amount = qty * price
    title = f"[{st_code}] {type_label} 체결"
    body = f"{qty}주 × {price:,}원 = {amount:,}원"
    if reasons:
        body += f"\n{', '.join(reasons)}"
    return title, body
```

**핵심 설계 포인트**:
- **독립 DB 세션 사용**: `Database.get_session()`으로 새 세션 생성 → 매매 트랜잭션과 완전 격리
- **fire-and-forget**: `asyncio.create_task()`로 호출하여 매매 응답에 영향 없음
- **DeviceNotRegistered 자동 정리**: Expo가 반환하는 에러로 만료 토큰 자동 비활성화

## 8. Expo Push API 연동 (`external/expo_push.py`)

```python
"""
Expo Push Notification API 클라이언트
https://docs.expo.dev/push-notifications/sending-notifications/
"""
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
MAX_BATCH_SIZE = 100
MAX_RETRIES = 3


async def send_expo_push(
    push_tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> list[str]:
    """
    Expo Push API로 알림 전송

    Args:
        push_tokens: Expo Push Token 목록
        title: 알림 제목
        body: 알림 본문
        data: 앱에 전달할 추가 데이터

    Returns:
        실패한 토큰 목록 (DeviceNotRegistered)
    """
    failed_tokens = []

    # 100개씩 배치 분할
    for i in range(0, len(push_tokens), MAX_BATCH_SIZE):
        chunk = push_tokens[i:i + MAX_BATCH_SIZE]
        messages = [
            {
                "to": token,
                "title": title,
                "body": body,
                "sound": "default",
                "data": data or {},
            }
            for token in chunk
        ]

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                    response = await client.post(EXPO_PUSH_URL, json=messages)
                    response.raise_for_status()

                result = response.json()
                tickets = result.get("data", [])

                # 개별 메시지 에러 체크
                for idx, ticket in enumerate(tickets):
                    if ticket.get("status") == "error":
                        error_type = ticket.get("details", {}).get("error", "")
                        if error_type == "DeviceNotRegistered":
                            failed_tokens.append(chunk[idx])
                            logger.info(f"Expo 토큰 만료: {chunk[idx][:30]}...")
                        else:
                            logger.warning(f"Expo Push 에러: {ticket.get('message')}")
                break  # 성공 시 재시도 루프 탈출

            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Expo Push 재시도 {attempt + 1}/{MAX_RETRIES}: {e}")
                else:
                    logger.error(f"Expo Push 최종 실패: {e}")
            except Exception as e:
                logger.error(f"Expo Push 예상치 못한 에러: {e}", exc_info=True)
                break

    return failed_tokens
```

**설계 결정**:
- 기존 `http_client.py`의 `fetch()`를 사용하지 않는 이유: Expo API는 HTTP 200으로 개별 메시지 에러를 반환하므로 `raise_for_status()`만으로 부족. 개별 ticket 검사 로직 필요.
- 전용 모듈로 분리하여 Expo 특화 로직 캡슐화.

## 9. Router 설계 (`notification/router.py`)

```python
router = APIRouter(prefix="/users", tags=["Notification"])


@router.get("/notification-settings")
async def get_notification_settings(
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """알림 설정 조회"""
    result = await service.get_settings(user_id)
    return success_response("알림 설정 조회 완료", result)


@router.put("/notification-settings")
async def update_notification_settings(
    request: NotiSettingUpdateRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """알림 설정 변경"""
    result = await service.update_settings(user_id, request)
    return success_response("알림 설정 변경 완료", result)


@router.post("/push-token")
async def register_push_token(
    request: PushTokenRegisterRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """푸쉬 토큰 등록"""
    result = await service.register_push_token(user_id, request)
    return success_response("푸쉬 토큰 등록 완료", result)


@router.delete("/push-token")
async def delete_push_token(
    request: PushTokenDeleteRequest,
    service: Annotated[NotificationSettingService, Depends(get_noti_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """푸쉬 토큰 삭제"""
    result = await service.delete_push_token(user_id, request)
    return success_response("푸쉬 토큰 삭제 완료", result)
```

> Router prefix를 `/users`로 설정하여 프론트엔드의 기존 API 호출 경로(`/users/notification-settings`)와 일치시킴.

## 10. 알림 호출 통합 설계 (auto_swing_batch.py)

### 10-1. 호출 전략: 단순화

`record_trade()` 호출 지점이 12곳에 분산되어 있으므로, **`record_trade()` 내부에서 알림을 트리거**하는 방식 대신 **`process_single_swing()` 함수 레벨에서 통합 처리**합니다.

**이유**:
- order_executor.py 내부의 `record_trade()`는 이미 `user_id`를 모르는 경우가 있음
- `process_single_swing()`에서 모든 signal 핸들러 실행 후 결과를 알고 있음
- 한 사이클에서 한 번만 알림 전송 (분할 체결 중간 chunk마다 알리지 않음)

### 10-2. 통합 포인트

`auto_swing_batch.py`의 `process_single_swing()` 함수에서, 각 signal 핸들러 호출 후 **상태 변화가 발생한 경우**에만 알림을 전송합니다.

```python
# process_single_swing() 내부 (line 193~227 사이)

# 변경 전 SIGNAL 저장
prev_signal = swing.SIGNAL

# === 4. SIGNAL별 오케스트레이션 ===
if swing.is_waiting():
    await _handle_signal_0(...)
elif swing.is_first_buy_done():
    await _handle_signal_1(...)
# ... (기존 코드)

# === 5. Entity 변경사항 저장 ===
await db.flush()
await db.commit()

# === 6. 푸쉬 알림 (fire-and-forget) ===
if user_id and swing.SIGNAL != prev_signal:
    _fire_trade_notification(user_id, swing, prev_signal, st_code)
```

### 10-3. 알림 트리거 함수

```python
import asyncio
from app.domain.notification.service import PushNotificationService


def _fire_trade_notification(
    user_id: str, swing, prev_signal: int, st_code: str
):
    """SIGNAL 변경에 따른 푸쉬 알림 (fire-and-forget)"""
    new_signal = swing.SIGNAL
    entry_price = int(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
    hold_qty = swing.HOLD_QTY or 0

    # 매수 체결 (SIGNAL 증가: 0→1, 1→2)
    if new_signal in (1, 2) and prev_signal < new_signal:
        phase = new_signal
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                trade_type="B",
                st_code=st_code,
                qty=hold_qty,
                price=entry_price,
                reasons=[f"{phase}차 매수 완료"],
            )
        )

    # 매도 체결 (SIGNAL 3으로 전환, 또는 0으로 리셋)
    elif new_signal == 3 and prev_signal in (1, 2):
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                trade_type="S",
                st_code=st_code,
                qty=hold_qty,
                price=entry_price,
                reasons=["1차 분할 매도 완료"],
            )
        )

    elif new_signal == 0 and prev_signal in (1, 2, 3):
        asyncio.create_task(
            PushNotificationService.send_trade_notification(
                user_id=user_id,
                trade_type="S",
                st_code=st_code,
                qty=0,
                price=entry_price,
                reasons=["전량 매도 완료"],
            )
        )
```

### 10-4. 부분 체결 처리 (line 154~187)

분할 체결 완료 시에도 동일한 패턴 적용:

```python
# 부분 체결 완료/중단 시
if partial_result.get("completed") or partial_result.get("aborted"):
    # ... (기존 상태 업데이트 코드)
    await db.flush()
    await db.commit()

    # 푸쉬 알림
    if user_id and swing.SIGNAL != prev_signal:
        _fire_trade_notification(user_id, swing, prev_signal, st_code)
    return
```

## 11. 알림 메시지 형식

| 상황 | title | body |
|------|-------|------|
| 1차 매수 완료 | `[005930] 매수 체결` | `100주 × 72,000원 = 7,200,000원\n1차 매수 완료` |
| 2차 매수 완료 | `[005930] 매수 체결` | `50주 × 68,000원 = 3,400,000원\n2차 매수 완료` |
| 1차 분할 매도 | `[005930] 매도 체결` | `75주 × 80,000원 = 6,000,000원\n1차 분할 매도 완료` |
| 전량 매도 | `[005930] 매도 체결` | `0주 × 80,000원 = 0원\n전량 매도 완료` |

> 추후 STOCK_INFO JOIN으로 종목명(ST_NM)을 포함하면: `[삼성전자] 매수 체결`

## 12. 구현 순서

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 1 | Entity 정의 | `notification/entity.py` | - |
| 2 | DB 테이블 생성 (DDL 실행) | MySQL | Entity |
| 3 | Schemas 정의 | `notification/schemas.py` | - |
| 4 | Repository 구현 | `notification/repository.py` | Entity |
| 5 | Expo Push 클라이언트 | `external/expo_push.py` | - |
| 6 | Service 구현 | `notification/service.py` | Repository, Expo Push |
| 7 | Router 구현 | `notification/router.py` | Service, Schemas |
| 8 | Router 등록 | `routers/__init__.py`, `main.py` | Router |
| 9 | Entity 등록 | `common/database.py` | Entity |
| 10 | auto_swing_batch.py 수정 | `trading/auto_swing_batch.py` | Service |

## 13. 의존성

### 추가 패키지: 없음
- `httpx`는 이미 설치됨 → Expo Push API 호출에 그대로 사용
- 별도 SDK 불필요 (Expo Push는 단순 HTTP POST)