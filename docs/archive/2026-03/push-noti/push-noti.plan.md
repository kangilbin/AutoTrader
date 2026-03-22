# Plan: push-noti (푸쉬 알림)

## 1. 개요

### 목적
자동 스윙 매매 체결(매수/매도) 시 사용자 앱(Expo)으로 푸쉬 알림을 전송하여 실시간 매매 상황을 알려주는 기능.

### 배경
- 프론트엔드(Expo 앱)에 이미 알림 설정 화면 존재 (`notifications.tsx`)
- 앱에서 `BUY_NOTI_YN`, `SELL_NOTI_YN` 설정을 백엔드로 전달하는 API 호출 구조 존재
- 백엔드에 해당 API 엔드포인트 및 DB 테이블은 **아직 미구현**
- `order_executor.py`에서 매수/매도 체결 시점은 명확하게 정의되어 있음

## 2. 요구사항

### 기능 요구사항 (FR)

| ID | 요구사항 | 우선순위 |
|----|---------|---------|
| FR-01 | Expo Push Token 등록 API — 앱 로그인/시작 시 디바이스 push token을 백엔드에 저장 | P0 |
| FR-02 | 알림 설정 조회 API (`GET /users/notification-settings`) | P0 |
| FR-03 | 알림 설정 변경 API (`PUT /users/notification-settings`) | P0 |
| FR-04 | 매수 체결 시 푸쉬 알림 전송 (BUY_NOTI_YN='Y'인 사용자) | P0 |
| FR-05 | 매도 체결 시 푸쉬 알림 전송 (SELL_NOTI_YN='Y'인 사용자) | P0 |
| FR-06 | 알림 실패 시 매매 로직에 영향 없이 에러 로깅만 수행 (fire-and-forget) | P0 |

### 비기능 요구사항 (NFR)

| ID | 요구사항 |
|----|---------|
| NFR-01 | 알림 전송은 비동기(fire-and-forget)로 처리 — 체결 응답 지연 없어야 함 |
| NFR-02 | Expo Push API 호출 실패 시 3회 재시도 후 로깅 |
| NFR-03 | 한 사용자가 여러 디바이스를 가질 수 있음 (1:N) |

## 3. 현재 상태 분석

### 프론트엔드 (Expo 앱) — 이미 존재
- **알림 설정 화면**: `notifications.tsx` — `BUY_NOTI_YN`, `SELL_NOTI_YN` 토글 UI
- **API 호출**: `backEndApi.ts` — `GET /users/notification-settings`, `PUT /users/notification-settings`
- **타입 정의**: `types/user.ts` — `NotificationSettings`, `UpdateNotificationRequest`
- **미구현**: Expo Push Token 등록 로직 (`expo-notifications` 미설치)

### 백엔드 (FastAPI) — 신규 구현 필요
- **알림 설정 API**: `GET /users/notification-settings`, `PUT /users/notification-settings` — 미구현
- **Push Token 등록 API**: 미구현
- **DB 테이블**: `USER_NOTI_SETTING`, `USER_PUSH_TOKEN` — 미존재
- **알림 전송 서비스**: 미존재

### 체결 시점 (order_executor.py)
알림을 삽입할 위치:
1. **단일 매수 완료**: `execute_buy_with_partial` → `remaining_amount < curr_price` 분기 (line 111)
2. **단일 매도 완료**: `execute_sell_with_partial` → `actual_qty >= target_qty` 분기 (line 186)
3. **분할 매수 chunk 완료**: `continue_partial_execution` → buy 섹션의 trade 기록 후 (line 310~318)
4. **분할 매도 chunk 완료**: `continue_partial_execution` → sell 섹션의 trade 기록 후 (line 365~374)

## 4. 기술 스택

| 구분 | 기술 | 이유 |
|------|------|------|
| 푸쉬 서비스 | **Expo Push API** (HTTP) | 프론트가 Expo 앱이므로 Expo Push Notification Service 직접 호출 |
| HTTP 클라이언트 | **httpx** (기존 사용 중) | 비동기 호출, 기존 `http_client.py` 활용 가능 |
| DB | MySQL (기존) | USER_NOTI_SETTING, USER_PUSH_TOKEN 테이블 추가 |

> **참고**: Expo Push API는 FCM/APNs를 래핑하므로 별도 Firebase 설정 불필요. `ExponentPushToken[xxx]` 형태의 토큰으로 직접 `https://exp.host/--/api/v2/push/send` 호출.

## 5. 구현 범위

### 백엔드 (이 프로젝트)

#### 5-1. DB 테이블

**USER_NOTI_SETTING** — 사용자별 알림 설정
| 컬럼 | 타입 | 설명 |
|------|------|------|
| USER_ID | VARCHAR(50) PK | 사용자 ID |
| BUY_NOTI_YN | CHAR(1) DEFAULT 'N' | 매수 알림 여부 |
| SELL_NOTI_YN | CHAR(1) DEFAULT 'N' | 매도 알림 여부 |
| REG_DT | DATETIME | 등록일 |
| MOD_DT | DATETIME | 수정일 |

**USER_PUSH_TOKEN** — 사용자별 푸쉬 토큰 (1:N)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| TOKEN_ID | INT PK AUTO_INCREMENT | 토큰 ID |
| USER_ID | VARCHAR(50) | 사용자 ID |
| PUSH_TOKEN | VARCHAR(200) | Expo Push Token |
| DEVICE_TYPE | VARCHAR(20) | ios / android |
| ACTIVE_YN | CHAR(1) DEFAULT 'Y' | 활성 여부 |
| REG_DT | DATETIME | 등록일 |
| MOD_DT | DATETIME | 수정일 |

#### 5-2. 새로운 도메인 모듈: `app/domain/notification/`

```
app/domain/notification/
├── __init__.py
├── entity.py          # UserNotiSetting, UserPushToken 엔티티
├── schemas.py         # Request/Response DTO
├── repository.py      # DB 접근 계층
├── service.py         # 알림 설정 CRUD + 푸쉬 전송 로직
└── router.py          # API 엔드포인트 (알림 설정은 /users 하위로 등록)
```

#### 5-3. API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/users/notification-settings` | 알림 설정 조회 |
| PUT | `/users/notification-settings` | 알림 설정 변경 |
| POST | `/users/push-token` | Push Token 등록/갱신 |
| DELETE | `/users/push-token` | Push Token 삭제 (로그아웃 시) |

#### 5-4. 푸쉬 알림 전송 서비스

```python
# app/domain/notification/service.py
class PushNotificationService:
    async def send_trade_notification(
        user_id: str,
        trade_type: str,   # "B" or "S"
        st_code: str,
        qty: int,
        price: int,
        amount: float
    ) -> None:
        """매매 체결 푸쉬 알림 전송 (fire-and-forget)"""
```

#### 5-5. order_executor.py 수정
- 매수/매도 체결 완료 시점에 `PushNotificationService.send_trade_notification()` 호출
- `asyncio.create_task()`로 fire-and-forget 처리 (체결 응답에 영향 없음)

### 프론트엔드 (Expo 앱) — 별도 작업

> 이 프로젝트(백엔드) 범위 밖이지만 참고용으로 기록

- `expo-notifications` 패키지 설치
- 앱 시작 시 Expo Push Token 획득 → `POST /users/push-token` 호출
- 로그아웃 시 `DELETE /users/push-token` 호출

## 6. 구현 순서

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | Entity 정의 (UserNotiSetting, UserPushToken) | `notification/entity.py` |
| 2 | Schemas 정의 | `notification/schemas.py` |
| 3 | Repository 구현 | `notification/repository.py` |
| 4 | Service 구현 (설정 CRUD + Expo Push 전송) | `notification/service.py` |
| 5 | Router 구현 (알림 설정 + 토큰 관리 API) | `notification/router.py` |
| 6 | Router 등록 | `domain/routers.py` |
| 7 | order_executor.py에 알림 호출 삽입 | `swing/trading/order_executor.py` |
| 8 | 통합 테스트 | - |

## 7. 리스크 및 고려사항

| 리스크 | 대응 |
|--------|------|
| Expo Push Token 만료/변경 | 앱 시작마다 토큰 갱신, 전송 실패 시 ACTIVE_YN='N' 처리 |
| 알림 전송 실패가 매매에 영향 | fire-and-forget + try/except로 완전 격리 |
| 대량 알림 (다수 사용자 동시 체결) | Expo Push API는 배치 전송 지원 (최대 100건), 필요시 활용 |
| Push Token 없는 사용자 | 설정이 Y여도 토큰 없으면 skip, 경고 로그만 |

## 8. 완료 기준

- [ ] 알림 설정 조회/변경 API 정상 동작
- [ ] Push Token 등록/삭제 API 정상 동작
- [ ] 매수 체결 시 BUY_NOTI_YN='Y' 사용자에게 푸쉬 알림 수신
- [ ] 매도 체결 시 SELL_NOTI_YN='Y' 사용자에게 푸쉬 알림 수신
- [ ] 알림 전송 실패 시 매매 로직 정상 진행
