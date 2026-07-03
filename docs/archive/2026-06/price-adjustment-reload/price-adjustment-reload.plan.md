# Plan: 수정주가 변동 종목 자동 재적재 (Price Adjustment Reload)

## 개요
액면교체/합병/분할 등으로 주가가 수정될 때, **이미 적재된 종목(`DATA_YN='Y'`)의 3년치 OHLCV를 새 수정주가 기준으로 재적재**한다. 수동 개입 없이 매일 새벽 자동 실행.

## 문제 정의

### 현재 흐름의 빈틈
- 종목 등록 시점에 `fetch_and_store_3_years_data`로 3년치 OHLCV를 적재
- 이후 `day_collect_job`이 매일 당일 OHLCV만 누적 적재
- **액면분할/병합/흡수합병으로 수정주가가 변경되면, 과거 적재 데이터와 새 수정주가 데이터가 불일치**
- 결과: EMA/RSI/ATR 등 지표가 잘못된 가격 시계열로 계산 → 매수/매도 신호 오류

### KIS API 동작 (검증 완료)
- `get_stock_data`가 `FID_ORG_ADJ_PRC="0"` (수정주가)으로 호출 중 (`kis_api.py:513, 539`)
- 즉 효력발생일 이후 동일 종목코드로 3년치 다시 받으면 **새 수정주가 기준 시계열**이 반환됨
- 따라서 단순 재적재만으로 해결 가능

### 외부 이벤트 소스
| API | 잡히는 이벤트 |
|-----|--------------|
| `/uapi/domestic-stock/v1/ksdinfo/rev-split` | 액면교체 (액면분할/병합) |
| `/uapi/domestic-stock/v1/ksdinfo/merger-split` | 흡수합병 (종목 유지) / 인적·물적 분할 |

두 API 모두 종목코드 공백 입력 시 전체 이벤트 응답.

## 변경 대상

### 신규 파일
- `app/external/kis_api.py` — 두 KIS 예탁원 API 호출 함수 추가
  - `get_rev_split_schedule(from_date, to_date)`
  - `get_merger_split_schedule(from_date, to_date)`
- `app/domain/stock/price_adjustment_batch.py` — 재적재 배치 잡

### 수정 파일
- `app/common/scheduler.py` — 새벽 02:30 KST 스케줄 1개 추가

### 변경 안 함 (자연 처리 가능)
- 상폐 처리: KIS 당일 조회 시 자연 skip → DEL_YN 분기 불필요
- 신주 매핑: 사용자 수동 등록
- SWING_TRADE 평균단가 보정: KIS 잔고가 source of truth

## 처리 로직

### 1. 이벤트 수집
```
new 함수: collect_today_adjustment_events()
- rev-split 공백 호출 1회 (조회기간: 오늘~오늘)
- merger-split 공백 호출 1회 (조회기간: 오늘~오늘)
- 두 응답에서 효력발생일 == 오늘인 이벤트만 추출
```

### 2. 등록 종목 필터링
```
- STOCK_INFO에서 DATA_YN='Y' AND DEL_YN='N' 조회 (국내만, MRKT_CODE != 'NASD')
- 이벤트 종목코드와 INNER JOIN (메모리 set 교집합)
```

### 3. 재적재
```
대상 종목 각각에 대해:
  1) DATA_YN='Y' → 'P'로 잠금 (trade_job 진입 방지)
  2) STOCK_DAY_HISTORY에서 (MRKT_CODE, ST_CODE) DELETE
  3) fetch_and_store_3_years_data 재사용 호출
     → 함수 내부에서 DATA_YN='Y' 복원
  4) 실패 시 DATA_YN='E'로 마킹 (기존 함수의 에러 핸들링 그대로)
```

### 4. 스케줄러 등록
```python
# 새벽 02:30 KST (KIS 점검시간 피함, trade_job 미실행 시간대)
scheduler.add_job(
    price_adjustment_reload_job,
    CronTrigger(minute='30', hour='2', day_of_week='mon-fri')
)
```

## 구현 순서

1. `app/external/kis_api.py` — `get_rev_split_schedule`, `get_merger_split_schedule` 함수 추가
2. `app/domain/stock/price_adjustment_batch.py` — `price_adjustment_reload_job` 구현
   - 이벤트 수집 → 필터링 → 재적재 흐름
3. `app/common/scheduler.py` — 새벽 02:30 cron 등록
4. 로컬에서 임의 종목코드로 함수 직접 호출하여 동작 검증

## 예상 영향

- **지표 정확도 회복**: 액면분할 직후 EMA/ATR 등 가짜 신호 제거
- **API 부담**: 일 2회 추가 호출(스케줄 호출) + 이벤트 발생 종목 × 약 30회(3년치 백필) — 발생 빈도 매우 낮으므로 부담 미미
- **DB 쓰기**: 이벤트 발생 종목당 약 700행 DELETE + INSERT
- **trade_job 영향 없음**: 새벽 02:30은 한국·미국 trade_job 모두 미실행 시간대

## 주의사항

- **`DATA_YN='P'` 잠금 필수**: 미국장 trade_job은 새벽에도 돌지만 한국 종목 재적재만 처리하므로 충돌 없음. 그래도 동시 등록 요청(`fetch_and_store_3_years_data`) 가능성 차단 위해 잠금 유지.
- **멱등성**: 같은 날 두 번 실행돼도 DELETE 후 INSERT라 결과 동일. 별도 EVENT_LOG 테이블 불필요.
- **장애 복구**: 하루 놓치면 다음 날 효력일은 이미 지나서 누락 가능. 운영 안정화 후 D-3 ~ D+0 백필로 확장 검토. 1단계 구현에서는 제외.
- **해외 종목(MRKT_CODE='NASD') 제외**: KSD API는 국내 종목만 대상.

## 후속 작업 (이번 범위 제외)

- 합병 상폐로 인한 좀비 SWING_TRADE 알림 (자연 skip은 되지만 사용자 인지를 위함)
- D-3 ~ D+0 백필 (장애 복구용)
- 인적분할 신주 자동 등록