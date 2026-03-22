# Plan: trading-fix — 자동 매매 배치 안정성 개선

## 개요
자동 매매 알고리즘 전수 검토에서 발견된 11개 이슈(CRITICAL 2건, HIGH 3건, MEDIUM 3건, LOW 3건)를 수정하여 실전 매매 안정성을 확보한다.

> **참고**: 검토 시 12개로 보고되었으나, #5(재진입 자본 초과)는 오분석으로 확인되어 제외. 실제 수정 대상은 11건.

## 현황 분석

### 발견된 이슈 목록

| # | 이슈 | 심각도 | 파일 | 수정 범위 |
|---|------|--------|------|-----------|
| 1 | DB 세션 공유 동시성 버그 | CRITICAL | `auto_swing_batch.py` | 구조 변경 |
| 2 | 예외 시 DB 롤백 누락 | CRITICAL | `auto_swing_batch.py` | 에러 핸들링 |
| 3 | 15:00~15:30 매매 시간 공백 | HIGH | `scheduler.py` | 스케줄 변경 |
| 4 | 부분 체결 Redis-DB 상태 불일치 | HIGH | `order_executor.py` | 순서 변경 |
| 5 | ~~재진입 자본 초과~~ | ~~HIGH~~ | - | **제외 (오분석)** |
| 6 | 체결 확인 폴백 부정확 | HIGH | `order_executor.py` | 로직 보강 |
| 7 | PEAK_PRICE 갱신 지연 (5분) | MEDIUM | 설계적 한계 | 문서화 |
| 8 | 연속 신호 Redis TTL 유실 가능 | MEDIUM | `single_ema_strategy.py` | TTL 조정 |
| 9 | 알림 fire-and-forget 예외 무시 | MEDIUM | `auto_swing_batch.py` | 예외 핸들링 |
| 10 | OBV z-score ddof=1 소표본 이슈 | LOW | `indicators.py` | 유지 (의도적) |
| 11 | 전략 팩토리 A/B 주석 처리 | LOW | `trading_strategy_factory.py` | 정리 |
| 12 | SELL_RATIO 정수 절삭 | LOW | `auto_swing_batch.py` | 유지 (의도적) |

---

## 수정 계획 상세

### #1. DB 세션 공유 동시성 버그 [CRITICAL]

**현재 문제:**
```python
# trade_job()
db = await Database.get_session()           # 세션 1개 생성
swing_service = SwingService(db)            # 공유
tasks = [process_single_swing(row, swing_service, redis) for row in swing_list]
await asyncio.gather(*tasks)                # 동시 실행 → 세션 충돌
```

**수정 방향:** 각 종목별로 독립 세션 생성

```python
# 수정 후 trade_job()
async def trade_job():
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        swing_list = await swing_service.get_active_swings()  # 목록 조회만
        redis_client = await Redis.get_connection()

        tasks = [
            process_single_swing(swing_row, redis_client)  # swing_service 전달 안함
            for swing_row in swing_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # ... 결과 로깅
    except Exception as e:
        logger.error(f"trade_job 실패: {e}", exc_info=True)
    finally:
        await db.close()

async def process_single_swing(swing_row, redis_client):
    async with _SEMAPHORE:
        db = await Database.get_session()     # ← 개별 세션 생성
        try:
            swing_service = SwingService(db)  # ← 개별 서비스 인스턴스
            # ... 기존 로직
            await db.commit()
        except Exception as e:
            await db.rollback()               # ← #2 함께 해결
            logger.error(...)
        finally:
            await db.close()                  # ← 세션 반환
```

**변경 파일:** `auto_swing_batch.py`
**영향 범위:**
- `process_single_swing` 시그니처 변경 (`swing_service` → `redis_client`만 전달)
- 내부에서 `SwingService`, `swing_service.repo.find_by_id` 개별 생성
- `_handle_signal_*` 함수에 전달하는 `db` 파라미터 = 개별 세션

---

### #2. 예외 시 DB 롤백 누락 [CRITICAL]

**현재 문제:**
```python
except Exception as e:
    logger.error(...)  # rollback 없음 → dirty 세션 방치
```

**수정 방향:** #1과 함께 해결. 개별 세션 + try/except/finally 패턴

```python
async def process_single_swing(swing_row, redis_client):
    async with _SEMAPHORE:
        db = await Database.get_session()
        try:
            # ... 매매 로직
            await db.flush()
            await db.commit()
        except Exception as e:
            await db.rollback()    # ← 추가
            logger.error(
                f"스윙 처리 실패 (SWING_ID={swing_row.SWING_ID}): {e}",
                exc_info=True
            )
        finally:
            await db.close()       # ← 보장
```

**변경 파일:** `auto_swing_batch.py`
**참고:** `_handle_signal_*` 내부의 개별 commit/flush는 제거하고, `process_single_swing` 최상위에서 단일 commit 유지

---

### #3. 15:00~15:30 매매 시간 공백 [HIGH]

**현재 문제:**
- `trade_job`: 10:00~14:59 (5분마다)
- 15:00~15:30 장 마감 전 35분간 손절 불가

**수정 방향:** 장 마감 전 마지막 손절 체크 전용 작업 추가

```python
# scheduler.py 에 추가
async def schedule_start():
    # 기존 trade_job (변경 없음)
    scheduler.add_job(
        trade_job,
        CronTrigger(minute='*/5', hour='10-14', day_of_week='mon-fri')
    )

    # [신규] 장 마감 전 손절 전용 체크 (15:00 ~ 15:20, 5분마다)
    scheduler.add_job(
        trade_job,
        CronTrigger(minute='*/5', hour='15', day_of_week='mon-fri'),
        kwargs={'exit_only': True}  # 매도만 실행
    )
    # ...
```

`trade_job`에 `exit_only` 파라미터 추가:
```python
async def trade_job(exit_only: bool = False):
    # exit_only=True일 때: check_entry_signal / check_second_buy_signal 스킵
    # check_exit_signal / check_trailing_stop_signal만 실행
```

**변경 파일:** `scheduler.py`, `auto_swing_batch.py`
**주의:** 15:25 이후에는 KIS API 주문 접수가 안 될 수 있으므로 15:00~15:20 범위로 제한

---

### #4. 부분 체결 Redis-DB 상태 불일치 [HIGH]

**현재 문제:**
```
Redis 저장(partial_exec) → DB commit 사이에 크래시 → 상태 불일치
```

**수정 방향:** DB commit 후 Redis 저장 (순서 변경)

```python
# order_executor.py - execute_buy_with_partial 수정

# 기존: Redis 먼저 → DB 나중
await redis_client.setex(f"partial_exec:{swing_id}", 86400, json.dumps(partial_state))

# 수정: DB commit을 caller(process_single_swing)에서 먼저 수행 후,
#        Redis 상태 저장은 commit 성공 후에만 실행
```

구현 패턴:
```python
# process_single_swing 내부
partial_result = await SwingOrderExecutor.execute_buy_with_partial(...)

# Entity 상태 업데이트
swing.ENTRY_PRICE = ...
swing.HOLD_QTY = ...

# DB 먼저 커밋
await db.flush()
await db.commit()

# Redis 상태 저장 (DB 성공 후에만)
if partial_result.get("partial_state"):
    await redis_client.setex(
        f"partial_exec:{swing_id}", 86400,
        json.dumps(partial_result["partial_state"])
    )
```

**변경 파일:** `order_executor.py`, `auto_swing_batch.py`
**트레이드오프:** Redis 저장 실패 시 → 다음 사이클에서 partial이 아닌 신규 주문으로 처리됨. 기존 체결분은 DB에 안전하게 보존.

---

### #6. 체결 확인 폴백 부정확 [HIGH]

**현재 문제:**
```python
execution = await check_order_execution(user_id, order_no, db)
executed_qty = execution.get("executed_qty", qty) if execution else qty  # 미확인 시 전량 체결 가정
```

**수정 방향:** 체결 조회 실패 시 재시도 + 미체결 상태 보존

```python
# order_executor.py 수정
execution = await check_order_execution(user_id, order_no, db)

if not execution:
    # 1차 재시도 (1초 대기 후)
    await asyncio.sleep(1)
    execution = await check_order_execution(user_id, order_no, db)

if not execution:
    # 체결 확인 불가 → 안전하게 0건 체결로 간주
    logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no}), 다음 사이클에서 재확인")
    return {
        "success": True,
        "completed": False,
        "qty": 0,
        "avg_price": 0,
        "order_no": order_no,
        "unconfirmed": True  # 미확인 플래그
    }
```

**변경 파일:** `order_executor.py`
**참고:** 미체결 주문은 다음 5분 사이클에서 `continue_partial_execution`이 실제 잔고 기준으로 재확인

---

### #7. PEAK_PRICE 갱신 지연 (5분) [MEDIUM]

**현재 상태:** 5분 간격 배치에서 KIS API의 `stck_hgpr`(당일 고가)를 사용하여 갱신

**판정:** 설계적 한계 (허용)
- 5분 간격은 스윙 매매 전략에서 합리적인 타임프레임
- 실시간 고가(`stck_hgpr`)를 사용하므로 5분 내 최고가는 반영됨
- Trailing stop의 ATR×2.0/3.0 배수가 이 지연을 충분히 흡수

**수정:** 없음 (문서화만)

---

### #8. 연속 신호 Redis TTL 유실 가능 [MEDIUM]

**현재 문제:** TTL 900초(15분), 5분 간격 실행 → 정상 시 문제없으나, 배치 지연 시 유실 가능

**수정 방향:** TTL을 1800초(30분)로 확장

```python
# single_ema_strategy.py
await redis_client.setex(prev_state_key, 1800, json.dumps(new_state))  # 15분 → 30분
```

**변경 파일:** `single_ema_strategy.py`
**이유:** 5분 × 2회 연속 = 10분 필요. TTL 30분이면 3사이클 지연까지 허용.

---

### #9. 알림 fire-and-forget 예외 무시 [MEDIUM]

**현재 문제:**
```python
asyncio.create_task(PushNotificationService.send_trade_notification(...))
# 예외 발생 시 "Task exception was never retrieved" 경고
```

**수정 방향:** 예외 콜백 추가

```python
def _fire_trade_notification(user_id, swing, prev_signal, st_code):
    # ...
    task = asyncio.create_task(
        PushNotificationService.send_trade_notification(...)
    )
    task.add_done_callback(_on_notification_done)

def _on_notification_done(task: asyncio.Task):
    if task.exception():
        logger.warning(f"푸쉬 알림 전송 실패: {task.exception()}")
```

**변경 파일:** `auto_swing_batch.py`

---

### #10. OBV z-score ddof=1 소표본 이슈 [LOW]

**판정:** 유지 (의도적 설계)
- `ddof=1`(표본 표준편차)은 통계적으로 올바른 선택
- 7개 데이터 포인트에서 보수적인 z-score는 오히려 false positive를 줄임
- 백테스팅의 `calculate_obv_zscore`와 동일한 `ddof=1` 사용으로 일관성 유지

**수정:** 없음

---

### #11. 전략 팩토리 A/B 주석 처리 [LOW]

**현재 상태:**
```python
_strategies = {
    # 'A': SingleEMAStrategy,  # 주석
    # 'B': SingleEMAStrategy,  # 주석
    'S': SingleEMAStrategy,
}
```

**수정 방향:** 주석 제거하고 명확히 정리

```python
_strategies = {
    'S': SingleEMAStrategy,  # 단일 20EMA 전략 (현재 유일한 실전 전략)
}
# 'A'(이평선), 'B'(일목균형표)는 별도 전략 클래스 구현 후 등록
```

**변경 파일:** `trading_strategy_factory.py`

---

### #12. SELL_RATIO 정수 절삭 [LOW]

**현재 상태:** `int(hold_qty * SELL_RATIO / 100)` → 소수량에서 비율 부정확

**판정:** 유지 (의도적 설계)
- 주식은 정수 단위로만 매매 가능
- `int()` 절삭 = 보수적 매도 (항상 잔량이 많은 쪽)
- 소수량(1~2주) 보유 시 분할 매도 자체가 비효율적이므로 문제 아님

**수정:** 없음

---

## 구현 순서

| 단계 | 이슈 | 이유 |
|------|------|------|
| 1단계 | #1 + #2 (DB 세션 분리 + 롤백) | 데이터 정합성의 근본 원인. 가장 먼저 해결 필수 |
| 2단계 | #3 (매매 시간 확장) | 손절 공백 제거로 리스크 차단 |
| 3단계 | #4 (Redis-DB 순서 변경) | 부분 체결 안정성 확보 |
| 4단계 | #6 (체결 확인 보강) | 주문-체결 정합성 강화 |
| 5단계 | #8, #9, #11 (MEDIUM/LOW 일괄) | 마이너 개선사항 |

## 변경 파일 요약

| 파일 | 변경 내용 |
|------|-----------|
| `auto_swing_batch.py` | 개별 세션 생성, 롤백 추가, exit_only 모드, 알림 예외 처리 |
| `scheduler.py` | 15:00~15:20 손절 전용 스케줄 추가 |
| `order_executor.py` | Redis-DB 순서 변경, 체결 확인 재시도 로직 |
| `single_ema_strategy.py` | Redis TTL 900→1800 |
| `trading_strategy_factory.py` | 주석 정리 |

## 리스크 및 주의사항

1. **#1 세션 분리 시 DB 커넥션 풀 고려**: 동시 5개 세션이 열리므로 커넥션 풀 크기 확인 필요 (기본 5~10개)
2. **#3 exit_only 모드**: 매도만 실행하므로 새로운 매수 진입은 차단됨. 의도된 동작.
3. **#4 순서 변경**: Redis 저장 실패 시 다음 사이클에서 중복 주문 가능성 → `continue_partial_execution`에서 실제 잔고 검증 로직으로 방어
4. **테스트**: 실전 투입 전 모의투자(SIMULATION_YN='Y')로 전체 사이클 검증 필수
