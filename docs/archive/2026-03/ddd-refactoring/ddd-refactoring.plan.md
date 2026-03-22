# Plan: DDD Refactoring - Entity 중심 상태 관리 복원

## 1. 개요

### 배경
현재 프로젝트는 CLAUDE.md에 "DDD Lite + Layered Architecture"로 설계되어 있으며, Entity에 비즈니스 로직(상태 전환, 검증)을 캡슐화하도록 명시되어 있다. 그러나 실제 구현에서는 Entity의 상태 전환 메서드가 전혀 사용되지 않고, Strategy 클래스가 신호 판단 + 주문 실행 + 상태 결정을 모두 담당하는 구조로 되어 있다.

### 핵심 문제
- **Entity**: `transition_to_*` 메서드 8개가 정의되어 있으나 호출되지 않음 (데드 코드)
- **Strategy** (`base_trading_strategy.py:153-859`): 700+ 라인의 `process_trading_cycle()`이 상태 머신 역할까지 수행
- **Service/Repository**: Entity를 거치지 않고 dict/SQL로 직접 SIGNAL 변경
- **결과**: 상태 전환 규칙이 Entity와 Strategy에 중복 정의, 검증 로직 미실행

### 목표
DDD 원칙에 맞게 각 계층의 책임을 재분배한다:
- **Strategy**: 매매 신호 판단만 담당 ("매수해야 함" / "매도해야 함")
- **Service**: 오케스트레이션 (신호 판단 요청 -> 주문 실행 -> Entity 상태 전환 -> 저장)
- **Entity**: 상태 전환 규칙 + 검증의 단일 소스

## 2. 현재 구조 분석

### 현재 데이터 흐름 (문제)
```
auto_swing_batch.py
  -> Strategy.process_trading_cycle()     # 750+ 라인, 모든 책임 집중
       -> check_entry_signal()            # 신호 판단 (Strategy 역할 O)
       -> SwingOrderExecutor.execute_*()  # 주문 실행 (Strategy 역할 X)
       -> new_signal = 1                  # 상태 결정 (Entity 역할)
       -> return {"new_signal": 1, ...}   # dict로 반환
  -> SwingService.update_swing(dict)      # dict 전달
  -> SwingRepository.update(SQL)          # Entity 우회, 직접 SQL UPDATE
```

### 문제점
1. Entity의 `transition_to_first_buy()` 등에 있는 **전제 조건 검증이 실행되지 않음**
   - 예: signal이 0이 아닌데 1차 매수로 전환하는 것을 막지 못함
2. 상태 전환 규칙이 Strategy의 if/elif 분기에 묻혀있어 **규칙 변경 시 찾기 어려움**
3. Strategy가 주문 실행까지 담당하여 **단일 책임 원칙 위반**
4. `process_trading_cycle()` 700+ 라인으로 **테스트/유지보수 어려움**

### 영향 받는 파일

| 파일 | 현재 역할 | 변경 후 역할 |
|------|-----------|-------------|
| `domain/swing/entity.py` | 상태 전환 메서드 정의 (미사용) | 상태 전환의 단일 소스 (실제 사용) |
| `domain/swing/trading/strategies/base_trading_strategy.py` | 신호 판단 + 주문 실행 + 상태 결정 | 신호 판단만 (check_*_signal) |
| `domain/swing/trading/auto_swing_batch.py` | Strategy 호출 + dict로 업데이트 | 오케스트레이션 흐름 변경 |
| `domain/swing/service.py` | dict 전달자 | Entity 로드 -> 상태 전환 -> 저장 |
| `domain/swing/repository.py` | dict 기반 SQL UPDATE | Entity 기반 저장 |
| `domain/swing/trading/order_executor.py` | Strategy 내부에서 호출됨 | Service/Batch에서 직접 호출 |

## 3. 목표 구조

### 리팩토링 후 데이터 흐름
```
auto_swing_batch.py (오케스트레이터)
  1. SwingEntity 로드 (Repository)
  2. Strategy.check_entry_signal()          # 판단만
  3. if 매수 신호:
       SwingOrderExecutor.execute_buy()     # 주문 실행
       swing_entity.transition_to_first_buy()  # Entity가 상태 전환 + 검증
       Repository.save(swing_entity)        # Entity 저장
       TradeHistoryService.record_trade()   # 거래 내역
```

### 계층별 책임 재정의

#### Entity (상태 + 규칙)
```python
class SwingTrade:
    def transition_to_first_buy(self, entry_price, hold_qty, peak_price):
        """1차 매수 상태로 전환 - 검증 포함"""
        if self.signal != 0:
            raise ValidationError(...)
        self.signal = 1
        self.entry_price = entry_price
        self.hold_qty = hold_qty
        self.peak_price = peak_price
        self.mod_dt = datetime.now()
```

#### Strategy (신호 판단만)
```python
class TradingStrategy:
    async def check_entry_signal(...) -> Optional[Dict]:
        """매수 진입 신호 체크 - 기존과 동일"""
        # EMA, ADX, OBV 등 기술지표 분석
        return {"action": "BUY", "reasons": [...]}

    # process_trading_cycle() 제거 또는 대폭 축소
```

#### Batch/Service (오케스트레이션)
```python
async def process_single_swing(swing_entity, strategy, ...):
    """오케스트레이터: 판단 -> 실행 -> 상태전환 -> 저장"""
    if swing_entity.is_waiting():
        signal = await strategy.check_entry_signal(...)
        if signal and signal["action"] == "BUY":
            order = await executor.execute_buy(...)
            if order["success"]:
                swing_entity.transition_to_first_buy(
                    entry_price=order["avg_price"],
                    hold_qty=order["qty"],
                    peak_price=int(current_price)
                )
                await repo.save(swing_entity)
                await trade_service.record_trade(...)
```

## 4. 구현 계획

### Phase 1: Entity 보강 (기반 작업)
1. `SwingTrade` Entity에 `entry_price`, `hold_qty`, `peak_price` 필드 추가
2. `transition_to_*` 메서드에 관련 필드 업데이트 로직 추가
3. `reset_cycle()` 메서드 추가 (사이클 종료 시 모든 필드 초기화)
4. Entity를 ORM 모델 기반으로 통합 (현재 dataclass와 SwingModel 이원화 문제 해결)

### Phase 2: Strategy 책임 축소
1. `process_trading_cycle()` 에서 주문 실행/상태 결정 로직 제거
2. Strategy는 순수 신호 판단 메서드만 유지:
   - `check_entry_signal()` (기존 유지)
   - `check_exit_signal()` (기존 유지)
   - `check_second_buy_signal()` (기존 유지)
   - `check_trailing_stop_signal()` (기존 유지)
3. `process_trading_cycle()` 제거

### Phase 3: 오케스트레이션 계층 구현
1. `auto_swing_batch.py`의 `process_single_swing()` 리팩토링
   - Entity 로드 -> 현재 signal에 따른 분기 -> Strategy 판단 요청 -> 주문 실행 -> Entity 상태 전환 -> 저장
2. signal별 분기 로직을 명확한 핸들러로 분리
3. SwingOrderExecutor를 Strategy 외부에서 직접 호출

### Phase 4: Repository/Service 정리
1. Repository에 Entity 기반 save/update 메서드 추가
2. `SwingService.update_swing(dict)` 패턴을 Entity 기반으로 전환
3. 미사용 Repository 메서드 정리 (`reset_signals_by_value` 등)

### Phase 5: 기타 도메인 데드코드 정리
1. 다른 도메인 Entity 데드코드 삭제 (order, stock, user, auth, account, device)
2. 미사용 Schema 클래스 삭제
3. 미사용 external API 함수 삭제

## 5. 구현 순서

```
Phase 1 (Entity 보강)
  -> Phase 2 (Strategy 축소) + Phase 4 (Repository 정리)  [병렬 가능]
     -> Phase 3 (오케스트레이션 구현)  [Phase 1,2,4 완료 후]
        -> Phase 5 (데드코드 정리)  [마지막]
```

## 6. 위험 요소 및 대응

### 위험 1: Entity-ORM 이원화
- **현재**: Entity는 dataclass, DB는 SwingModel (SQLAlchemy)
- **문제**: Repository.save() 시 Entity → Model 변환 필요
- **대응**: Entity를 SQLAlchemy 모델 기반으로 통합하거나, 명확한 매핑 계층 유지

### 위험 2: 배치 성능
- **우려**: Entity를 매번 로드하면 N+1 쿼리 발생 가능
- **대응**: 현재 `find_active_swings()`가 이미 개별 레코드를 반환하므로 추가 쿼리 없음. Entity 로드는 기존 조회 결과를 매핑하는 것으로 충분

### 위험 3: 부분 체결 상태 관리
- **현재**: Redis에 `partial_exec:{swing_id}` 키로 부분 체결 상태 관리
- **대응**: 부분 체결 흐름은 기존 패턴 유지 (Entity 상태 전환은 체결 완료 시에만 수행)

### 위험 4: process_trading_cycle 의존성
- **현재**: auto_swing_batch.py가 strategy.process_trading_cycle()에 전적으로 의존
- **대응**: 점진적 전환 - 먼저 새 오케스트레이터를 만들고, 기존 코드와 병행 테스트 후 교체

## 7. 범위 외 (이번 리팩토링에서 제외)

- Aggregate Root 패턴 도입
- Domain Event 패턴 도입
- Repository Interface 분리 (현재 단일 구현체로 충분)
- Value Object 도입
- 새로운 매매 전략 추가

## 8. 성공 기준

1. Entity의 `transition_to_*` 메서드가 실제 호출되어 상태 전환 검증이 동작
2. Strategy 클래스에 주문 실행/상태 결정 코드가 없음
3. `process_trading_cycle()` 제거 또는 오케스트레이터로 대체
4. 기존 매매 기능이 동일하게 동작 (회귀 없음)
5. 데드코드 68개 항목 정리 완료
