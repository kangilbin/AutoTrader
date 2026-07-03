# Gap Analysis: Conviction Sizing

> **Match Rate: 94% (Pass)**
> **Date**: 2026-05-20

## Design vs Implementation 비교

| Design 섹션 | 항목 수 | 일치 | Match Rate | Status |
|-------------|:------:|:----:|:----------:|:------:|
| 1. 파라미터 변경 | 6 | 6 | 100% | Pass |
| 2. calc_conviction() | 7 | 7 | 100% | Pass |
| 3. 백테스트 수량 계산 | 7 | 6 | 86% | Pass |
| 4. 실전 수량 계산 | 7 | 7 | 100% | Pass |
| 5. 반환값 변경 불필요 | 1 | 1 | 100% | Pass |
| 6. 데이터 해상도 차이 | 5 | 4 | 80% | Pass |
| **전체** | **33** | **31** | **94%** | **Pass** |

## Gap 목록 (1건)

| # | 항목 | Design | 구현 | 영향도 |
|---|------|--------|------|--------|
| 1 | 백테스트 거래 기록 conviction | 거래 기록 dict에 conviction 포함 (디버깅용) | 미포함 | Low |

## Documentation Debt (3건)

| # | 위치 | 현재 | 올바른 내용 |
|---|------|------|------------|
| 1 | `single_ema_backtest_strategy.py` L4 | "리스크 기반 포지션 사이징" | "Conviction 기반 포지션 사이징" |
| 2 | 백테스트 README SIGNAL 흐름도 | "매수 (리스크 기반 수량 계산)" | "매수 (Conviction 기반 수량 계산)" |
| 3 | 실전전략 README SIGNAL 흐름도 | "매수 (리스크 기반 수량 계산)" | "매수 (Conviction 기반 수량 계산)" |
