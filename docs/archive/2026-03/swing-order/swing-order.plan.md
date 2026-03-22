# Plan: swing-order 매도 실현손익 데이터 적재

## 개요
스윙 자동매매에서 매도 체결 시 실현손익(PnL), 수익률, 수수료, 세금 데이터를 TRADE_HISTORY에 적재하여 프론트엔드에서 백테스트와 동일한 수준의 매매 성과를 확인할 수 있게 한다.

## 현황 분석

### 문제점
- **백테스트** (`single_ema_backtest_strategy.py`): 매도 시 `realized_pnl`, `realized_pnl_pct`, `commission`, `tax`, `net_proceeds` 계산하여 응답
- **실제 매매** (`order_executor.py` → `TradeHistoryService.record_trade`): 매도 시 `avg_price`, `qty`, `amount`만 저장
- 프론트엔드에서 실제 매매의 손익/수익률을 확인할 수 없음

### 현재 TRADE_HISTORY 테이블
```
TRADE_ID, SWING_ID, TRADE_DATE, TRADE_TYPE(B/S),
TRADE_PRICE, TRADE_QTY, TRADE_AMOUNT, TRADE_REASONS, REG_DT
```

### KIS API 체결 조회 응답 (check_order_execution에서 추출 가능)
- `avg_prvs`: 평균체결가 → 이미 `avg_price`로 사용 중
- `tot_ccld_amt`: 총체결금액 → 이미 `executed_amt`로 사용 중
- `tot_ccld_qty`: 총체결수량 → 이미 `executed_qty`로 사용 중

### 실현손익 계산에 필요한 데이터
KIS API 응답만으로는 실현손익을 계산할 수 없다. **매수 평균단가**(SWING_TRADE.ENTRY_PRICE)가 필요하며, 이는 이미 swing entity에 관리되고 있다.

## 구현 범위

### 1. TRADE_HISTORY 엔티티 확장 (매도 전용 컬럼 추가)
| 컬럼 | 타입 | 설명 | 매수 시 | 매도 시 |
|------|------|------|---------|---------|
| COMMISSION | DECIMAL(15,2) | 수수료 | NULL | 계산값 |
| TAX | DECIMAL(15,2) | 세금(매도세) | NULL | 계산값 |
| NET_PROCEEDS | DECIMAL(15,2) | 순수익 (매도금액-수수료-세금) | NULL | 계산값 |
| REALIZED_PNL | DECIMAL(15,2) | 실현손익 | NULL | 계산값 |
| REALIZED_PNL_PCT | DECIMAL(8,2) | 실현손익률(%) | NULL | 계산값 |
| AVG_BUY_PRICE | DECIMAL(15,2) | 매수평균단가 (매도 시 스냅샷) | NULL | swing.ENTRY_PRICE |

### 2. 손익 계산 로직 (백테스트와 동일)
```python
# 수수료: 매도금액 × 0.00147 (증권사 수수료율)
commission = sell_amount * 0.00147

# 세금: 매도금액 × 0.0020 (거래세)
tax = sell_amount * 0.0020

# 순수익
net_proceeds = sell_amount - commission - tax

# 실현손익: (매도가 - 매수평균가) × 수량 - 수수료 - 세금
realized_pnl = (sell_price - avg_buy_price) * qty - commission - tax

# 수익률: ((매도가 / 매수평균가) - 1) × 100
realized_pnl_pct = ((sell_price / avg_buy_price) - 1) * 100
```

### 3. record_trade 수정
- `trade_type == "S"` 일 때 swing의 ENTRY_PRICE를 조회하여 손익 계산
- 계산된 값을 새 컬럼에 저장

### 4. 응답 스키마 확장
- `TradeHistoryResponse`에 새 필드 추가 (Optional, 매도 시에만 값 존재)

## 구현 순서

1. **Entity** - `TradeHistory`에 6개 컬럼 추가
2. **DB 마이그레이션** - ALTER TABLE로 컬럼 추가
3. **Schemas** - `TradeHistoryResponse`에 Optional 필드 추가
4. **Service** - `record_trade`에서 매도 시 손익 계산 로직 추가
5. **Repository** - `save` 메서드에 새 필드 매핑 추가

## 영향 범위
- `app/domain/trade_history/entity.py` - 컬럼 추가
- `app/domain/trade_history/schemas.py` - 응답 필드 추가
- `app/domain/trade_history/service.py` - 매도 손익 계산 로직
- `app/domain/trade_history/repository.py` - save 메서드 필드 매핑
- DB: TRADE_HISTORY 테이블 ALTER

## 기술 참고
- 수수료율(0.00147), 세금율(0.0020)은 `single_ema_backtest_strategy.py`의 `COMMISSION_RATE`, `TAX_RATE`와 동일
- 매수 평균단가는 `SwingTrade.ENTRY_PRICE`에서 가져옴 (이미 2차 매수 시 가중평균 계산됨)
- 기존 매수 데이터는 영향 없음 (새 컬럼 모두 nullable)
