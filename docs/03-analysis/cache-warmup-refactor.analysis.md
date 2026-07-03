# 지표 캐시 워밍업 TTL/스코프 리팩터링 분석 (Check)

작성일: 2026-07-04
대상: `app/domain/swing/service.py` (`market_cache_ttl`, `warmup_ema_cache`), `app/domain/swing/trading/auto_swing_batch.py` (워밍업 잡 호출부), `app/main.py` (startup)
유형: 버그 수정 + 리팩터링 (설계문서 없이 대화 기반 스펙 → 사후 Check)

---

## 1. 결론 요약

| 항목 | 의도(스펙) | 구현 | 판정 |
|---|---|---|---|
| 미국 캐시가 매매 시점까지 생존 | 캐시 TTL = 해당 시장 다음 마감까지 | `market_cache_ttl()` 도입, 3곳 적용 | ✅ |
| 서버 타임존 무관 동작 | naive `datetime.now()` 제거, ZoneInfo 명시 | tz-aware 계산 | ✅ |
| 워밍업 대상 시장 선택 | `scope` = domestic/overseas/all | `overseas_only:bool` → `scope:str` 대칭화 | ✅ |
| 미국 종목 이중 계산 제거 | 08:29=국내만, 09:00 ET=미국만 | `scope="domestic"` / `"overseas"` 분리 | ✅ |
| 재시작 안전망 | startup은 전체 워밍업 | `main.py` 기본값 `scope="all"` | ✅ |
| 부분체결 상태 TTL(86400) | 변경 대상 아님 | 미변경 | ✅ (의도) |

**Match Rate: 95%** — 이번 리팩터링 스코프 내 의도는 전부 구현·검증됨. 감점 5%는 (a) 자동화 테스트 부재, (b) 인접 잠재 리스크(국내 cron 타임존 의존성)가 이 수정으로 해소되지 않은 점.

---

## 2. 원인 분석 (수정 전 버그)

`warmup_ema_cache` / `cache_single_indicators`의 기존 TTL:

```python
now = datetime.now()                                  # naive (서버 로컬존)
target = datetime.combine(now.date(), time(16, 0))    # "오늘 16:00" (KST 마감 전제)
ttl = max(int((target - now).total_seconds()), 60)
```

- **국내 전제 로직**: 16:00은 국내장 마감 기준. 미국 종목에 그대로 적용됨.
- **미국 워밍업이 밤에 실행**: `us_ema_cache_warmup_job`은 09:00 ET(≈22:00~23:00 KST)에 발동. 이 시점 "오늘 16:00"은 이미 과거 → `(target-now)` 음수 → `max(음수, 60)` = **60초 클램프** → `us_trade_job`이 도는 순간 캐시 소멸 → `등록된 캐시 정보가 없습니다`.
- **비대칭 스코프**: `overseas_only`만 존재하고 "국내 전용"이 없어, 08:29 국내 잡이 미국까지 중복으로 데움.

---

## 3. 수정 내용

### 3.1 시장별 TTL 헬퍼 (`service.py`)
```python
_MARKET_CLOSE_CONFIG = {
    "J":    {"close": time(16, 0), "tz": "Asia/Seoul"},
    "NASD": {"close": time(16, 0), "tz": "America/New_York"},
}

def market_cache_ttl(mrkt_code: str, min_ttl: int = 60) -> int:
    config = _MARKET_CLOSE_CONFIG.get(mrkt_code, _DEFAULT_CLOSE)
    tz = ZoneInfo(config["tz"])
    now = datetime.now(tz)
    target = datetime.combine(now.date(), config["close"], tzinfo=tz)
    if now >= target:
        target += timedelta(days=1)
    return max(int((target - now).total_seconds()), min_ttl)
```
- tz-aware 뺄셈은 절대 시각차라 서버 타임존과 무관.
- `now >= target`이면 다음날로 → 밤 워밍업의 음수 TTL 근본 차단.

### 3.2 스코프 대칭화 (`service.py`)
`overseas_only: bool` → `scope: str = "all"` ("domestic" | "overseas" | "all"), NASD=미국 / 그 외=국내.

### 3.3 호출부 (`auto_swing_batch.py`, `main.py`)
| 호출처 | 시각 | scope |
|---|---|---|
| `ema_cache_warmup_job` | 08:29 KST | `"domestic"` |
| `us_ema_cache_warmup_job` | 09:00 ET | `"overseas"` |
| `_warmup_ema_cache` (startup) | 앱 시작 | `"all"` (기본값) |

---

## 4. 런타임 검증 결과

현재 시각 KST 00:38 / ET 11:38 기준 실행:

```
J        TTL= 55308s (15.4h)   → 다음 KST 16:00까지
NASD     TTL= 15708s (4.4h)    → 다음 ET 16:00까지
UNKNOWN  TTL= 55308s           → 기본값(국내) 폴백
assert market_cache_ttl('NASD') > 60  → PASS (60초 클램프 탈출 확인)
```
- 두 모듈 import 정상, `overseas_only` 잔재 0건.
- scope 필터 단위 검증: domestic→국내만 / overseas→미국만 / all→전체.

---

## 5. 남은 갭 / 잠재 리스크

### 5.1 [정정] "배포 컨테이너 = UTC"는 단정할 수 없음
- Dockerfile에 TZ 미설정 → `python:3.12-slim` 기본은 UTC. 하지만 런타임(env `TZ`, 오케스트레이터, `/etc/localtime` 마운트)로 덮일 수 있음.
- **국내 매매가 정상 동작한다는 사실 자체가 런타임 실효 타임존이 KST임을 시사** (아래 5.2 근거). 따라서 초기 진단에서 "컨테이너는 UTC다"라고 단정한 것은 과장이었음.
- 다만 이번 TTL 수정은 **UTC/KST 어느 쪽이든 안전**(tz-aware)하므로 이 불확실성과 무관하게 올바르다.

### 5.2 [인접 리스크·이번 수정 범위 밖] 국내 cron 잡의 타임존 미지정
- `scheduler.py`의 국내 잡(`ema_cache_warmup_job` 08:29, `trade_job` 08–15, `day_collect_job` 15:35, `price_adjustment_reload_job` 02:30)은 **`timezone=` 미지정** → `scheduler.timezone`(=tzlocal 런타임 로컬존)에 의존.
- 미국 잡만 `timezone='America/New_York'` 명시.
- 만약 런타임이 UTC라면 국내 잡은 KST 기준 +9h로 어긋나 매매 자체가 오작동함. **현재 국내가 정상이므로 런타임은 사실상 KST로 추정**되지만, Dockerfile로는 보장되지 않는 암묵 의존성.
- **권고**: 국내 잡에도 `timezone='Asia/Seoul'`을 명시하거나 Dockerfile에 `ENV TZ=Asia/Seoul`+tzdata를 고정해 배포 환경 변화에 견고하게 만들 것. (별도 작업으로 분리 권장)

### 5.3 자동화 테스트 부재
- `market_cache_ttl`은 순수 함수라 단위 테스트 용이. DST 경계(3월/11월), 주말, `now==target` 경계 케이스에 대한 테스트 추가 권장.

---

## 6. 다음 단계

- [ ] (권고) 5.2 국내 cron 타임존 명시 — 별도 수정
- [ ] (선택) `market_cache_ttl` 단위 테스트 추가 (DST/주말/경계)
- [ ] 실제 배포(운영) 환경에서 미국 워밍업→매매 시점 캐시 생존 스모크 확인
