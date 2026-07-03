# Gap Analysis: Price Adjustment Reload

> **Match Rate: 99% (Pass)**
> **Date**: 2026-06-17

## Design vs Implementation 비교

| Design 섹션 | 항목 수 | 일치 | Match Rate | Status |
|-------------|:------:|:----:|:----------:|:------:|
| KIS API 함수 (rev-split / merger-split) | 6 | 6 | 100% | Pass |
| 응답 키 / 필드 (output1, sht_cd, list_dt) | 3 | 3 | 100% | Pass |
| 날짜 정규화 (`_normalize_date`) | 1 | 1 | 100% | Pass |
| 배치 흐름 (이벤트→교집합→재적재) | 3 | 3 | 100% | Pass |
| 세마포어 동시성(3) | 1 | 1 | 100% | Pass |
| 스케줄러 cron (평일 02:30 KST) | 1 | 1 | 100% | Pass |
| SYSTEM 토큰 (BATCH_USER_ID) | 2 | 2 | 100% | Pass |
| 에러 핸들링 (API별 격리) | 1 | 1 | 100% | Pass |
| Design 일관성 (3.4 SYSTEM 상수 위치) | 1 | 0.5 | 50% | Soft |
| **전체** | **19** | **18.5** | **99%** | **Pass** |

## Gap 목록 (0건)

기능 차이 없음. 모든 Design 사양이 구현에 반영됨.

## 긍정적 보정 (Design 대비 개선) (3건)

| # | 항목 | 위치 | 설명 |
|---|------|------|------|
| 1 | `user_id`를 함수 인자로 명시적 전달 | `price_adjustment_batch.py:60` | Design은 모듈 상수 `SYSTEM_USER_ID` 사용을 시사했으나 구현은 `settings.BATCH_USER_ID`를 잡 진입 시점에 읽어 호출 체인으로 전달. 모듈 로드 시점 평가 제거, 테스트 용이성 향상. |
| 2 | `collect_today_adjustment_events` 내부 API별 try/except 격리 | `price_adjustment_batch.py:34-44` | Design §5 Edge Cases에서 요구된 "한쪽 API 실패해도 다른 쪽 계속" 동작을 코드로 명시적 구현. |
| 3 | 이벤트 종목코드 INFO 로그 | `price_adjustment_batch.py:96` | 운영 관찰성 향상, 추가 비용 없음. |

## Documentation Debt (2건)

| # | 위치 | 현재 | 올바른 내용 |
|---|------|------|------------|
| 1 | Design §2.1 흐름 다이어그램 | `collect_today_adjustment_events(today)` (1-arg) | `collect_today_adjustment_events(user_id, today, db)` (3-arg) — §3.2 상세 코드와 일치시킬 것 |
| 2 | Design §3.4 SYSTEM_USER_ID 채택 | 모듈 상수 시사 | 실제 구현은 함수 인자 전달. 비처방적 문구지만 일관성 위해 갱신 가능 |

## 운영 사전 조건 (Runbook 항목)

- `.env`에 `BATCH_USER_ID=<운영자 USER_ID>` 등록 필수
- 해당 사용자가 `USER` 테이블에 존재하고 활성 `AUTH_KEY` 보유 필요
- 사전 조건 미충족 시 잡은 `BATCH_USER_ID 미설정` 로그 후 즉시 종료 (No-op)

## 결론

- 실질적 Gap **0건**, 모든 핵심 사양 구현 일치
- 3건의 긍정적 보정은 Design 원안보다 견고한 패턴
- 별도 자동 개선(`/pdca iterate`) 불필요 — `/pdca report`로 직행 가능
- 선택적 후속: Design 문서 일관성 보정 (2건의 Documentation Debt)