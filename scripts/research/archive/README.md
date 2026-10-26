# 오더블럭 검증 스크립트 (보관)

2026-10-24 ~ 26 오더블럭 전략 검증에 쓴 연구 스크립트다. 오더블럭 실전·백테스트 코드는 2026-10-26 제거했다.
결론과 수치는 `docs/archive/2026-10/order-block/` 문서에 있다.

**이 폴더의 스크립트는 지금 코드에서는 실행되지 않는다** (제거된 `app/domain/swing/order_block.py`,
`OrderBlockBacktestStrategy` 등을 import 한다). 다시 돌리려면 마지막으로 동작한 커밋을 체크아웃한다:

```bash
git checkout 8426b2b -- app/domain/swing/order_block.py \
  app/domain/swing/trading/strategies/base_order_block.py \
  app/domain/swing/backtest/strategies/order_block_backtest_strategy.py
```

| 파일 | 내용 |
|------|------|
| `order_block_research.py` | 기간 분할 백테스트, 무작위 진입 대조군, ablation, 필터 완화, 청산 구조·민감도 실험 (fork 병렬) |
| `swing_portfolio.py` | 스윙 합산 시뮬레이터 (종목별 배정금 → 시장별 합산 곡선, CAGR·MDD·MAR·노출률), 비용·체결 강화, 품질 점수 |

`swing_portfolio.py`의 합산 곡선·지표·종목 단순 보유 비교는 오더블럭과 무관하다 —
단일 이평선 검증에 재사용할 때는 이 부분만 떼어 쓴다.
