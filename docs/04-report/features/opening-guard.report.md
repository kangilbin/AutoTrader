# Opening Guard (개장 보호) 완료 보고서

> **피처**: opening-guard (개장 초기 노이즈 방지)
>
> **프로젝트**: AutoTrader
>
> **작성일**: 2026-05-14
>
> **PDCA 상태**: Completed (Match Rate: 100%)

---

## 1. 개요

### 1.1 목적

장 시작 직후 호가 불균형 / 시초가 오버슈팅으로 발생하는 **장중고가 스파이크**가 `PEAK_PRICE`를 오염시켜 **가짜 익절 신호**가 발생하는 문제를 해결한다.

### 1.2 문제 상황

```
09:00 장 시작 → 장중고가 15만원 (순간 스파이크)
09:00~09:05 → 정상가 10만원으로 복귀
09:05 첫 체크 → PEAK_PRICE = max(매수가, 장중고가) = 15만원 (오염!)
              → 익절선 = 15만원 - ATR×2.0 ≈ 12~13만원
              → 현재가 10만원 < 12만원 → 즉시 익절 발동! (가짜 신호)
```

실제로는 수익이 발생하지 않았음에도 스파이크로 인해 비정상적으로 높아진 PEAK 기준으로 매도 신호가 발생.

### 1.3 해결 방안

**Opening Guard** — 개장 후 일정 시간(기본 10분) 동안:
- PEAK_PRICE 갱신 시 장중고가 대신 **현재가** 사용 (스파이크 배제)
- Trailing Stop **익절 체크 스킵** (가짜 신호 방지)
- **손절 체크는 유지** (급락 방어)

### 1.4 변경 파일 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/domain/swing/trading/strategies/base_single_ema.py` | 수정 | `OPENING_GUARD_MINUTES` 파라미터 추가 |
| `app/domain/swing/trading/auto_swing_batch.py` | 수정 | `is_opening_guard()` 함수 + PEAK/익절 가드 로직 |
| `app/domain/swing/trading/strategies/README.md` | 수정 | 문서 반영 (섹션 + 파라미터 테이블) |
| **총 3개 파일** | | |

---

## 2. 구현 상세

### 2.1 시장별 개장 시간 매핑

```python
_MARKET_OPEN_CONFIG = {
    "J":    {"open": dt_time(9, 0),   "tz": "Asia/Seoul"},      # KRX
    "NX":   {"open": dt_time(9, 0),   "tz": "Asia/Seoul"},      # NXT
    "UN":   {"open": dt_time(9, 0),   "tz": "Asia/Seoul"},      # 통합
    "NASD": {"open": dt_time(9, 30),  "tz": "America/New_York"}, # 나스닥 (ET)
}
```

- 나스닥은 `America/New_York` 타임존으로 **서머타임 자동 반영** (기존 APScheduler 설정과 동일 방식)
- 미등록 시장코드는 KRX("J")로 fallback

### 2.2 `is_opening_guard()` 함수

```python
def is_opening_guard(mrkt_code: str, guard_minutes: int = 10) -> bool:
    config = _MARKET_OPEN_CONFIG.get(mrkt_code, _MARKET_OPEN_CONFIG["J"])
    tz = ZoneInfo(config["tz"])
    now = datetime.now(tz).time()
    open_dt = datetime.combine(datetime.today(), config["open"])
    guard_end = (open_dt + timedelta(minutes=guard_minutes)).time()
    return config["open"] <= now < guard_end
```

### 2.3 PEAK_PRICE 갱신 로직 변경 (`auto_swing_batch.py`)

```python
# 변경 전
swing.update_peak_price(int(current_high))

# 변경 후
_in_opening_guard = is_opening_guard(mrkt_code, strategy.OPENING_GUARD_MINUTES)
if swing.has_position():
    peak_source = int(current_price) if _in_opening_guard else int(current_high)
    swing.update_peak_price(peak_source)
```

### 2.4 익절 체크 스킵 (`_handle_position`)

```python
# 변경 전
ts_result = await strategy.check_trailing_stop_signal(...)

# 변경 후
if in_opening_guard:
    ts_result = None  # 개장 보호 기간 — 익절 체크 스킵
else:
    ts_result = await strategy.check_trailing_stop_signal(...)
```

손절 체크(`check_exit_signal`)는 가드 없이 항상 실행 — 급락 방어 유지.

### 2.5 파라미터

| 파라미터 | 값 | 위치 | 설명 |
|---------|-----|------|------|
| `OPENING_GUARD_MINUTES` | 10 | `base_single_ema.py` | 개장 후 보호 기간 (분) |

---

## 3. Gap Analysis 결과

### 3.1 설계-구현 일치율: **100%** (6/6)

| # | 설계 요구사항 | 구현 상태 |
|---|-------------|:--------:|
| 1 | 개장 후 10분간 보호 기간 | ✅ |
| 2 | PEAK 갱신: 보호 기간 중 현재가로 | ✅ |
| 3 | 익절 체크 스킵 | ✅ |
| 4 | 손절 체크 유지 | ✅ |
| 5 | 시장별 타임존 지원 (KRX/NASD) | ✅ |
| 6 | 파라미터화 (OPENING_GUARD_MINUTES) | ✅ |

### 3.2 엣지 케이스 검토

| 엣지 케이스 | 상태 | 비고 |
|------------|:----:|------|
| NASD 자정 전후 개장 (겨울 22:30 KST) | ✅ | ET 기준 판단, 영향 없음 |
| `guard_minutes=0` | ✅ | `open <= now < open` → 항상 False, 보호 비활성화 |
| 분할 체결 진행 중 + 개장 보호 | ✅ | 분할 체결이 먼저 return하므로 PEAK/익절까지 미도달 |
| 미등록 시장코드 | ✅ | KRX fallback |

### 3.3 알려진 제한사항

- `datetime.today()`가 naive datetime이나, `.time()` 추출 후 비교하므로 10분 범위에서는 실질적 문제 없음
- 개장 초반 10분 내 실제 급등(노이즈가 아닌) 시 PEAK에 장중고가 미반영 → 보호 해제 후 자연 갱신

---

## 4. 동작 흐름 요약

```
개장 (09:00 KRX / 09:30 ET)
  │
  ├─ 보호 기간 (10분)
  │   ├─ PEAK = max(PEAK, 현재가)     ← 장중고가 스파이크 무시
  │   ├─ 익절 체크: 스킵               ← 가짜 신호 방지
  │   └─ 손절 체크: 정상 동작           ← 급락 방어 유지
  │
  └─ 보호 해제 (09:10 KRX / 09:40 ET)
      ├─ PEAK = max(PEAK, 장중고가)    ← 정상 로직 복원
      └─ 익절/손절 모두 정상 동작
```

---

## 5. PDCA 진행 요약

```
[Plan] — → [Design] — → [Do] ✅ → [Check] ✅ (100%) → [Report] ✅
```

- **Plan/Design**: 논의 기반 설계 (대화에서 직접 합의)
- **Do**: 3개 파일 수정
- **Check**: Gap Analysis 100% 일치
- **Report**: 본 문서
