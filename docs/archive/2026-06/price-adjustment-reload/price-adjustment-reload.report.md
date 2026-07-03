# Price Adjustment Reload (수정주가 재적재) 완료 보고서

> **피처**: price-adjustment-reload (액면교체/합병/분할 자동 재적재)
>
> **프로젝트**: AutoTrader
>
> **작성일**: 2026-06-17
>
> **PDCA 상태**: Completed (Match Rate: 99%, 실호출 검증 완료)

---

## 1. 개요

### 1.1 목적

상장기업이 액면교체/합병/분할을 단행하면 신주 재상장일 이후 KIS API는 **수정주가**로 보정된 OHLCV를 응답한다. 그러나 우리 DB에는 변경 이전 시점에 적재된 원래 가격이 남아있어 **시계열 불연속**이 발생한다. 이로 인해 EMA·ATR·RSI 등 기술 지표 계산이 왜곡되고 스윙 매매 신호가 잘못 발생할 수 있다.

### 1.2 문제 상황

```
액면병합 5:1 예시
─────────────────
DB 저장 (분할 전, 원주가):     5,000원
KIS 응답 (분할 후, 수정주가): 25,000원  ← 5배 환산
─────────────────
EMA20 계산 시 시계열 점프 → 가짜 골든크로스 / 가짜 매수 신호
```

### 1.3 해결 방안

매일 새벽 02:30 KST에 KSD(예탁결제원) 예탁원정보 API 2종을 호출하여 **당일 효력 발생 이벤트**를 수집하고, 등록된 종목(`DATA_YN='Y'`) 중 영향받는 종목의 3년치 OHLCV를 **새 수정주가 기준으로 자동 재적재**한다.

- 적재 시점: 새벽 02:30 KST (한국·미국 trade_job 모두 미실행 시간대)
- 효력일 판정: KSD 응답 `list_dt`(상장/등록일) = KIS 수정주가 적용 시점
- DB 업데이트: 기존 `save_history_bulk`의 `ON DUPLICATE KEY UPDATE` 활용

### 1.4 변경 파일 요약

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/external/kis_api.py` | 함수 2개 추가 | `get_rev_split_schedule`, `get_merger_split_schedule` |
| `app/domain/stock/price_adjustment_batch.py` | **신규** | 잡 본체 + 이벤트 수집 + 단일종목 재적재 |
| `app/common/scheduler.py` | cron 1줄 추가 | 평일 02:30 KST 등록 |
| `app/core/config.py` | 필드 1개 추가 | `BATCH_USER_ID: Optional[str]` |
| **총 4개 파일** | | |

---

## 2. 구현 상세

### 2.1 KSD 예탁원정보 API 통합 (`app/external/kis_api.py`)

| 함수 | URL | TR-ID | 핵심 파라미터 |
|------|-----|-------|--------------|
| `get_rev_split_schedule` | `/uapi/domestic-stock/v1/ksdinfo/rev-split` | `HHKDB669105C0` | `SHT_CD=""`, `MARKET_GB="0"` |
| `get_merger_split_schedule` | `/uapi/domestic-stock/v1/ksdinfo/merger-split` | `HHKDB669104C0` | `SHT_CD=""` |

두 함수 모두 동일 시그니처:
```python
async def get_X_schedule(user_id: str, from_date: str, to_date: str, db: AsyncSession) -> dict
```

### 2.2 배치 잡 (`app/domain/stock/price_adjustment_batch.py`)

```
price_adjustment_reload_job()
  ↓ BATCH_USER_ID 가드
  ↓
collect_today_adjustment_events(user_id, today, db)
  ↓ rev-split + merger-split 두 API 호출 (try/except 격리)
  ↓ output1에서 list_dt == today 필터
  ↓ sht_cd set 반환
  ↓
StockService.get_data_target_stocks(overseas=False)
  ↓ DATA_YN='Y' 국내 종목과 set 교집합
  ↓
세마포어(3) 병렬로 reload_single_stock()
  ↓ fetch_and_store_3_years_data 재호출
  ↓ ON DUPLICATE KEY UPDATE로 덮어쓰기
```

### 2.3 효력일 판정: `list_dt` 사용 (`_normalize_date` 헬퍼)

KSD 응답 분석 결과 (실호출, 100건):

| 상태 | 비율 | 의미 |
|------|------|------|
| `list_dt` 채워짐 | 54% | 거래정지 종료일 확정 = 거래 재개일 결정 = 효력일 |
| `list_dt` 빈 값 | 46% | 거래정지 종료일 미정 = 미래 미확정 이벤트 |

빈 list_dt는 `_normalize_date("") == today`가 False라 자연 skip. 추후 거래정지 종료 시점이 정해지면 KSD가 list_dt를 채워줘 그 날 잡이 잡음.

```python
def _normalize_date(s: str) -> str:
    """YYYYMMDD, YYYY/MM/DD, YYYY-MM-DD → YYYYMMDD"""
    return ''.join(c for c in (s or '') if c.isdigit())[:8]
```

### 2.4 스케줄러 등록 (`app/common/scheduler.py`)

```python
scheduler.add_job(
    price_adjustment_reload_job,
    CronTrigger(minute='30', hour='2', day_of_week='mon-fri')
)
```

KIS 점검시간(03:40~04:10) 회피, day_collect_job/trade_job과 시간대 비중복.

### 2.5 시스템 토큰 컨텍스트 (`app/core/config.py`)

```python
BATCH_USER_ID: Optional[str] = None
```

기존 `mgnt_access_token` 패턴은 `settings.API_KEY`/`SECRET_KEY`가 config/.env에 미정의된 상태라 신뢰 불가. 대신 USER 테이블에 등록된 운영자 ID를 환경변수로 지정하여 `_get_user_auth` 흐름을 그대로 활용한다.

---

## 3. 의사결정 기록

진행 중 사용자 지적으로 방향이 바뀐 주요 결정들:

| # | 초기 제안 | 최종 결정 | 결정 근거 |
|---|----------|----------|----------|
| 1 | 16:00 KST 적재 | 02:30 KST 적재 | 종가 판단이라 시점 무관, trade_job과 시간대 분리 |
| 2 | trade_job 동시성 잠금, EVENT_LOG 테이블, SWING_TRADE 평균단가 보정 | 모두 제외 | 새벽이라 trade_job 미실행, UPSERT로 멱등성, KIS 잔고가 source of truth |
| 3 | merger-split는 후속 작업 | rev-split + merger-split 동시 처리 | API 호출 1줄 추가라 오버헤드 미미, 흡수합병 후 비율 변경 종목 누락 방지 |
| 4 | 상폐 종목 DEL_YN 분기 | 미구현 (자연 처리) | KIS 당일 조회 시 데이터 없음 응답 → trade_job이 자연 skip |
| 5 | `fetch_and_store_3_years_data` 시그니처 변경 | 변경 없음 | `_get_user_auth(user_id, db)`가 user_id 종류 무관하게 동작 |
| 6 | DELETE 후 INSERT | UPSERT만 사용 | `save_history_bulk`가 이미 `ON DUPLICATE KEY UPDATE` |
| 7 | 효력일 = list_dt (추론) | 효력일 = list_dt (실증) | 100건 실호출에서 list_dt = 거래정지 종료+1일 = 거래 재개일 확인 |

---

## 4. 검증 결과

### 4.1 Gap 분석 (Design ↔ Implementation)

| 항목 | 결과 |
|------|------|
| Match Rate | **99%** (19 항목 중 18.5 일치) |
| 실질 Gap | **0건** |
| 긍정적 보정 | 3건 (user_id 명시적 전달, try/except 격리, 운영 로그) |
| Documentation Debt | 2건 (Design §2.1/§3.4 일관성, 비처방적) |

### 4.2 실호출 검증

| 검증 항목 | 결과 |
|----------|------|
| `_normalize_date` 단위 | ✅ 6/6 케이스 통과 |
| rev-split 실호출 | ✅ rt_cd=0, output1 100건 응답, 모든 필드 확인 |
| merger-split 실호출 | ✅ 동일 |
| `list_dt` 패턴 분석 | ✅ 54건 채워짐, 모두 td_stop_dt 종료+1일 일치 |
| KIS 수정주가 자동 환산 | ✅ 005930 2018-05-04 50:1 분할 후 5만원대 응답 실증 |
| `collect_today_adjustment_events` dry-run | ✅ 0건 정상 |

### 4.3 KIS 수정주가 환산 실증

```
종목: 005930 (삼성전자), 액면분할 2018-05-04 50:1
조회기간: 2018-04-01 ~ 2018-06-01 (42 row)

2018-04-02 (분할 전):                종가 48,540원   ← 5만원대 = 수정주가
2018-04-27 (분할 전 마지막 거래일):  종가 53,000원
2018-04-30 ~ 2018-05-03 (거래정지):  53,000으로 표시
2018-05-04 (재상장):                  종가 51,900원
```

원주가였다면 분할 전 약 250만원대여야 함. **5만원대로 확인 → KIS API가 자동 환산** → 본 기능의 핵심 전제 검증 완료.

---

## 5. 운영 사항

### 5.1 사전 조건

- `.env`에 `BATCH_USER_ID=<운영자 USER_ID>` 추가
- 해당 사용자가 `USER` 테이블에 존재
- 해당 사용자의 활성 `AUTH_KEY` (실전키) 존재

미설정 시 잡은 `BATCH_USER_ID 미설정` 로그만 남기고 즉시 종료 (no-op).

### 5.2 모니터링 로그

`[PRICE ADJ]` 프리픽스로 다음 로그 발생:

```
[PRICE ADJ] 수정주가 재적재 잡 시작 (today=YYYYMMDD)
[PRICE ADJ] 당일 이벤트 없음 - 종료                              (이벤트 0건)
[PRICE ADJ] 이벤트 종목코드: ['005930', ...]                     (이벤트 발생)
[PRICE ADJ] 이벤트 N건 중 재적재 대상 M건
[PRICE ADJ] 재적재 완료: J/005930
[PRICE ADJ] 완료 - 성공: M/M
```

### 5.3 후속 검토 항목 (이번 범위 제외)

- D-3 ~ D+0 백필 (장애 복구 보강)
- 인적분할 신주 자동 등록
- 합병 상폐로 좀비 SWING_TRADE 알림

---

## 6. PDCA 사이클 요약

| Phase | 산출물 | 기간 |
|-------|--------|------|
| Plan | [`docs/01-plan/features/price-adjustment-reload.plan.md`](../../01-plan/features/price-adjustment-reload.plan.md) | 2026-06-17 |
| Design | [`docs/02-design/features/price-adjustment-reload.design.md`](../../02-design/features/price-adjustment-reload.design.md) | 2026-06-17 |
| Do | 코드 4개 파일 변경 (신규 1, 수정 3) | 2026-06-17 |
| Check | [`docs/03-analysis/price-adjustment-reload.analysis.md`](../../03-analysis/price-adjustment-reload.analysis.md), Match Rate 99%, 실호출 6종 통과 | 2026-06-17 |
| Report | 본 문서 | 2026-06-17 |

### 6.1 학습 사항

1. **검증을 늦추면 비싸다**: Gap 분석 99%로 보고 진행을 제안했으나 실호출 안 하면 `list_dt` 빈 row 패턴, KIS 수정주가 환산 실증 등 핵심 사실을 놓칠 뻔함. 정적 분석으로는 외부 시스템 동작 검증 불가.
2. **시장 메커니즘 추론도 실증으로 확정**: list_dt vs record_date 논의에서 시장 표준 추론에 의존했지만, 실제 KSD 응답 패턴은 추론과 일치하더라도 추가 케이스(빈 list_dt)가 발견됨.
3. **외부 명세는 일관성 있게 동작하지 않을 수 있음**: KIS 응답 필드 길이 명세(`list_dt` Length 9/10)와 실제 동작(빈 문자열 가능)이 다름. 정규화 헬퍼는 그래서 필수.

---

## 7. 결론

수정주가 변동 종목 자동 재적재 기능이 **설계 일치율 99% + 외부 API 실호출 6종 검증 통과** 상태로 완성되었다. 운영 적용 시 `.env`에 `BATCH_USER_ID` 추가만 필요하며, 첫 효력 이벤트 발생일에 정상 동작 여부를 운영 로그(`[PRICE ADJ]`)로 모니터링하면 된다.
