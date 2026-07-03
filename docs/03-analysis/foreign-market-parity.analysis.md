# 미국장 vs 한국장 20EMA 실전 전략 일치성 분석

작성일: 2026-05-25
대상: `SingleEMAStrategy` (단일 20EMA 추세 추종 전략)
범위: `app/domain/swing/trading/` + `app/external/foreign_api.py` + `app/common/scheduler.py` + `app/domain/stock/stock_data_batch.py`

---

## 1. 결론 요약

| 영역 | 한국 ↔ 미국 동일성 | 비고 |
|---|---|---|
| 신호 로직(SingleEMAStrategy) | ✅ 100% 동일 — 동일 클래스 사용 | 분기 없음 |
| 지표 계산 (EMA/ATR/ADX/OBV) | ✅ 동일 | TA-Lib + 증분 캐시 공통 |
| 포지션 사이징 (conviction) | ✅ 동일 | 통화 단위만 USD/KRW 차이 (스케일 무관) |
| 임계값 (ratio 기반 상수) | ✅ 동일 | 통화 무관 비율/표준화 지표 |
| 데이터 수집 — 일별 OHLCV | ⚠️ **버그 있음** | `STCK_BSOP_DATE`를 서버 로컬 시간으로 저장 |
| 데이터 수집 — 외국인 순매수 | ✅ 의도된 0 처리 | 전략 로직에서 미사용 |
| 시세 응답 필드 매핑 | ✅ 분기 처리 정상 | last/rate/tvol ↔ stck_prpr/prdy_ctrt/acml_vol |
| 주문 실행 (지정가/시장가) | ✅ 의도된 차이 | 미국은 지정가 ±0.5% 슬리피지 |
| 체결 확인 폴링 | ✅ 의도된 차이 | 미국 2초/3회, 국내 1초/2회 |
| 가용 자본 조회 (`dnca_tot_amt`) | ⚠️ **검증 필요** | KIS 해외 API output2에 해당 필드 부재 가능성 |
| 개장 초기 보호 (Opening Guard) | ⚠️ **데드 코드** | 함수만 정의되고 호출되지 않음 — 양쪽 모두 미적용 |
| 사전장 스케줄 (한국 08:00–08:55) | ⚠️ **무의미한 호출** | 장 시작 전 cron 실행, 미국은 11:00 ET 버퍼로 회피됨 |

**전략 코드 자체는 시장 분기 없이 동일하게 작동한다**.
문제가 있다면 *전략 외곽*(데이터 수집 날짜, 잔고 필드 매핑, 데드코드 보호장치)에 집중되어 있다.

---

## 2. 검토 흐름 — 한국 vs 미국 코드 경로 비교

```
auto_swing_batch.process_single_swing()
├─ mrkt_code == "NASD" → _overseas = True
├─ get_inquire_price()          → 시세 API 분기 ✓
├─ enrich_cached_indicators_with_realtime()  ← 공통
├─ check_entry_signal()         ← 공통 (SingleEMAStrategy)
├─ check_exit_signal()          ← 공통
└─ execute_buy/sell_with_partial()
   ├─ 주문 단가: USD는 지정가 ±0.5%, KRW는 시장가
   ├─ KIS API: foreign_api vs kis_api 분기
   └─ 체결 폴링: 미국 2초/3회 vs 국내 1초/2회
```

신호 판단부는 **분기가 전혀 없다**. 동일 클래스(`SingleEMAStrategy`)가 한국/미국에 모두 적용된다.

---

## 3. 발견된 이슈 (심각도 순)

### 🔴 Critical-1. 미국 일별 OHLCV 저장 시 영업일이 1일 어긋남

**위치**: `app/domain/swing/trading/auto_swing_batch.py:710,723`

```python
"STCK_BSOP_DATE": datetime.now().strftime('%Y%m%d'),  # ⚠️ 서버 로컬 시간
```

**문제**:
- `us_day_collect_job`이 미국 동부시간 **16:35 ET**에 실행됨.
- 서버 타임존이 KST이면 `datetime.now()` = 미국장 마감일 + 1일.
- 예: 미국 거래일 2026-05-22(금) → 한국 시간 2026-05-23(토) 05:35 → DB에 "20260523"으로 저장.

**영향**:
- US 종목의 STOCK_DAY_HISTORY 테이블이 실제 영업일과 +1일 어긋남.
- `_calculate_indicators`는 OHLCV 컬럼 값 자체로 EMA/ATR/ADX/OBV를 계산하므로 **지표 수치는 정확**.
- 그러나 `STCK_BSOP_DATE`를 키로 사용하는 모든 조회(`find_history`, 보고서 화면, 차트)는 1일 어긋난 날짜를 보여줌.
- 일별 데이터 호출 응답에 이미 정확한 `xymd` (미국 거래일자) 필드가 있음에도 사용하지 않고 덮어쓰는 구조.

**수정 방향**:
```python
if _overseas:
    history_data = [{
        ...
        "STCK_BSOP_DATE": response.get('xymd'),  # KIS 응답의 실제 거래일자 사용
        ...
    }]
```

같은 패턴이 한국에도 적용되나 한국은 KST 15:35 실행이라 같은 날짜라 문제없음. 다만 일관성을 위해 `stck_bsop_date` 응답 필드 사용을 권장.

---

### 🔴 Critical-2. `dnca_tot_amt` — 해외 잔고에서 0 반환 가능성

**위치**: `app/domain/swing/service.py:49,315`

```python
balance_data = await foreign_api.get_stock_balance(user_id, self.db)
output2 = balance_data["output2"]
cash = int(output2.get("dnca_tot_amt", 0))   # ← default 0
```

**문제**:
- KIS 해외주식 잔고 API(`uapi/overseas-stock/v1/trading/inquire-balance`) output2의 표준 응답에는 `dnca_tot_amt` (원화 예수금) 필드가 일반적으로 포함되지 않음.
- USD 잔고는 보통 `frcr_dncl_amt1`, `frcr_evlu_tota`, `tot_asst_amt` 등의 별도 외화 필드로 제공됨.
- 만약 `dnca_tot_amt`가 응답에 없으면 `cash = 0` → `available_capital = -allocated` (음수).
- 결과: `get_available_capital`이 잘못된 값을 반환 → 활성화 시 자본 한도 검증 실패 또는 우회.

**검증 필요**:
1. 실제 KIS 해외 잔고 API 응답을 로그로 확인.
2. `dnca_tot_amt`가 응답에 있는지, 있다면 KRW인지 USD인지 (환산 시 추가 처리 필요).
3. 없다면 USD 잔고에 해당하는 필드(`frcr_dncl_amt1` 등)로 대체.

**한국장은 정상**: 한국 KIS 잔고 API output2는 `dnca_tot_amt` 필드를 반드시 포함.

---

### 🟡 Major-1. Opening Guard 데드코드 — 양쪽 모두 보호 미작동

**위치**:
- 정의: `app/domain/swing/trading/auto_swing_batch.py:51` (`is_opening_guard`)
- 상수: `app/domain/swing/trading/strategies/base_single_ema.py:84` (`OPENING_GUARD_MINUTES = 10`)
- **호출처 없음** (grep 결과 0건)

**문제**:
- 개장 직후 10분간 PEAK_PRICE 오염 방지 및 익절 체크 스킵을 위해 설계된 보호 장치가 어디서도 사용되지 않음.
- 한국장은 09:00 정확히 trade_job이 돌고, 09:00–09:10의 호가 불균형/시초가 스파이크가 PEAK_PRICE에 그대로 반영됨.
- 미국장은 11:00 ET부터 trade_job 시작이라 자연스럽게 90분 버퍼가 있어 영향 적음.

**영향**:
- 한국장에서만 의미 있는 영향. 미국장 영향 없음.
- 결과적으로 한국/미국 *동일하게 보호 없는 상태*이므로 본 분석의 "외국장 동작 동등성" 측면에서는 문제 없음.

**권장**:
- `_handle_position`에서 PEAK_PRICE 갱신 전후로 `is_opening_guard(mrkt_code)` 체크 추가.
- 혹은 데드코드 제거.

---

### 🟡 Major-2. 한국 trade_job 사전장 호출 (08:00–08:55)

**위치**: `app/common/scheduler.py:26-33`

```python
CronTrigger(minute='*/5', hour='8-14', day_of_week='mon-fri')
```

**문제**:
- 한국장은 09:00 개장인데 trade_job이 08:00, 08:05, ... 08:55에도 실행됨.
- KIS 한국 시세 API는 사전장 시간에 전일 종가/이상값을 반환 → `prdy_ctrt`가 잘못된 기준일 수 있음.
- 잠재적으로 사전장 시점에 매수 신호가 잘못 발생할 위험.

**미국장 비교**:
- `us_trade_job`는 11:00–15:55 ET로 의도적으로 1.5시간 버퍼 → 사전장 호출 없음.
- 즉, 미국장이 더 보수적으로 보호되어 있음.

**권장**:
- `hour='9-14'`로 수정 (한국 정규장 시작 시각으로).

---

### 🟢 Minor-1. 미국장 `prdy_ctrt`/`rate` 단위 가정

**위치**: `app/domain/swing/trading/auto_swing_batch.py:160`

```python
prdy_ctrt = float(current_price_data.get("rate", 0))
```

**현황**:
- KIS 해외 `HHDFS00000300` 응답의 `rate`는 등락률(%, 부호 포함, 예: `"2.50"` = +2.5%).
- 전략은 `abs(prdy_ctrt)/100 <= 0.05`로 검사 → 5% 이내만 매수.
- **이 가정이 맞으면 한국 `prdy_ctrt`와 의미적으로 동일**.

**리스크**:
- KIS API 응답 포맷이 시기/계약별로 미세하게 다를 수 있음. 실제 응답값을 한 번 로깅하여 단위(%) 확인 권장.
- 만약 `rate`가 소수(`0.025`)로 오면 surge filter가 사실상 무력화되어 모든 종목이 통과 → 급등주 매수 위험.

---

### 🟢 Minor-2. 미국장 외국인 순매수 데이터 부재

**위치**: `app/domain/swing/trading/auto_swing_batch.py:158`

```python
frgn_ntby_qty = 0  # 해외 시 외국인 순매수 미제공
```

**현황**:
- 미국 KIS API는 외국인 순매수 미제공 → 0으로 처리.
- `SingleEMAStrategy.check_entry_signal`에서 `frgn_ntby_qty` 파라미터를 받지만 **실제 로직 어디에서도 사용하지 않음**.
- 따라서 한국과 미국 모두 외국인 데이터가 매매 결정에 영향을 주지 않음.

**평가**: 동작 동일성 측면에서 문제 없음.

---

### 🟢 Minor-3. 미국장 지정가 주문의 `int()` 절삭

**위치**: `app/domain/swing/trading/order_executor.py:92, 183, 309, 384`

```python
order_unpr = int(float(current_price) * 1.005 * 100) / 100 if _overseas else 0
```

**현황**:
- 의도: 현재가 ±0.5% 슬리피지로 지정가.
- 실제: `int(... * 100) / 100`은 **floor**, 즉 매수가는 항상 0.5% 미만으로 절삭됨.
- 예: 현재가 $123.45 → `123.45 * 1.005 = 124.067` → `int(12406.7) = 12406` → `/100 = 124.06`. 즉 0.49%로 잘림.

**영향**:
- 매수 측: 슬리피지가 0.5% 미만이라 체결률 미세하게 낮아짐.
- 매도 측: `0.995` 곱은 floor가 유리하게 작용 (더 낮은 지정가 = 체결 잘 됨).

**권장**: `round(... , 2)` 사용으로 통일하여 0.5% 정확히 적용.

---

### 🟢 Minor-4. 한국 trade_job hour='8' 표기

`hour='8-14'`는 시작이 08시지만 의도는 09시. 본 분석과는 별개로 일관성 개선 항목.

---

## 4. 한국 ↔ 미국 데이터 흐름 매핑 표

### 4-1. 일별 OHLCV (3년 적재 + 일일 수집)

| 필드 | 한국 (KIS) | 미국 (KIS NAS) | 매핑 |
|---|---|---|---|
| 시가 | `stck_oprc` | `open` | `STCK_OPRC` ✓ |
| 고가 | `stck_hgpr` | `high` | `STCK_HGPR` ✓ |
| 저가 | `stck_lwpr` | `low` | `STCK_LWPR` ✓ |
| 종가 | `stck_clpr` | `clos` | `STCK_CLPR` ✓ |
| 거래량 | `acml_vol` | `tvol` | `ACML_VOL` ✓ |
| 외국인 순매수 | `frgn_ntby_qty` | (없음) | `FRGN_NTBY_QTY` (미국=0) |
| 날짜 | `stck_bsop_date` | `xymd` | `STCK_BSOP_DATE` ⚠️ 미사용 |

### 4-2. 실시간 시세

| 항목 | 한국 (`inquire-price`) | 미국 (`HHDFS00000300`) | strategy 사용 |
|---|---|---|---|
| 현재가 | `stck_prpr` | `last` | ✓ |
| 고가 | `stck_hgpr` | `high` | ✓ (ATR 증분용) |
| 저가 | `stck_lwpr` | `low` | ✓ |
| 거래량 | `acml_vol` | `tvol` | ✓ (OBV 증분용) |
| 등락률 | `prdy_ctrt` (%) | `rate` (%) | ✓ (surge filter) |
| 외국인 순매수 | `frgn_ntby_qty` | — (0 처리) | 미사용 |
| 전일대비 거래량비 | `prdy_vrss_vol_rate` | — (100 처리) | 미사용 |

---

## 5. 권장 조치 (우선순위)

| # | 작업 | 우선도 | 예상 작업량 |
|---|---|---|---|
| 1 | `STCK_BSOP_DATE` 응답 `xymd` 사용으로 변경 | 🔴 즉시 | 10분 |
| 2 | 해외 잔고 `dnca_tot_amt` 응답 실측 + USD 필드 매핑 | 🔴 즉시 | 30분 |
| 3 | `is_opening_guard` 활용 또는 데드코드 제거 | 🟡 1주일 내 | 20분 |
| 4 | 한국 trade_job cron `hour='9-14'`로 정정 | 🟡 1주일 내 | 5분 |
| 5 | `rate` 응답 단위 실측 로깅 | 🟢 다음 배치 사이클 | 5분 |
| 6 | `order_unpr` `int/100` → `round(..., 2)` | 🟢 다음 배치 사이클 | 5분 |

---

## 6. 결론

> **전략 로직과 지표 계산은 한국장과 미국장이 정확히 동일하게 작동한다.**
> 매매 시그널, 임계값, 포지션 사이징, 분할 체결 알고리즘 모두 시장 분기 없이 공유되며, 통화 단위만 자연스럽게 USD/KRW로 분리된다.

위험은 **전략 외곽의 데이터 정합성**에 있다:
1. 미국 일별 데이터 영업일이 +1일 어긋날 가능성 (`datetime.now()` 사용).
2. 해외 잔고 조회의 `dnca_tot_amt` 필드 의존 — 응답에 없으면 가용 자본 0으로 계산.

위 두 가지를 검증/수정하면 한국장과 미국장의 동등 운영이 안전하게 보장된다.

Match Rate: **약 85%** — 전략 로직은 100% 동일하나, 인프라 레이어에 잠재적 결함 2건이 남아 있어 90% 미만으로 평가.
