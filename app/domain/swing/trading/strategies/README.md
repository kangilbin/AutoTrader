# 매매 전략 구조

이 디렉토리는 두 가지 타입의 전략을 포함합니다:
1. **백테스트 전략** (`BacktestStrategy` 상속)
2. **실시간 거래 전략** (`TradingStrategy` 상속)

## 백테스트 전략 (Backtest Strategy)

### 구조
```
base_strategy.py (BacktestStrategy)
├── ema_strategy.py (EMAStrategy)
├── ichimoku_strategy.py (IchimokuStrategy)
└── single_ema_backtest_strategy.py (SingleEMABacktestStrategy)
```

### 특징
- 과거 데이터로 전략 성능 검증
- `compute(prices_df, params)` 메서드 구현 필수
- 동기 방식 실행
- `strategy_factory.py`에서 관리

### 새로운 백테스트 전략 추가 방법
```python
from .base_strategy import BacktestStrategy

class MyBacktestStrategy(BacktestStrategy):
    def __init__(self):
        super().__init__("나의 백테스트 전략")

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        # 백테스트 로직 구현
        pass
```

## 실시간 거래 전략 (Trading Strategy)

### 구조
```
base_trading_strategy.py (TradingStrategy)
└── single_ema_strategy.py (SingleEMAStrategy)
```

### 특징
- 실시간 매매 신호 생성
- 필수 메서드:
  - `check_entry_signal()`: 1차 매수 신호
  - `check_exit_signal()`: 매도 신호
  - `check_second_buy_signal()`: 2차 매수 신호
- 비동기 방식 실행 (async/await)
- Redis, DB 연동
- `trading_strategy_factory.py`에서 관리

### 새로운 실시간 거래 전략 추가 방법

#### 1. 전략 클래스 생성
```python
# ichimoku_trading_strategy.py
from .base_trading_strategy import TradingStrategy

class IchimokuTradingStrategy(TradingStrategy):
    """일목균형표 실시간 거래 전략"""

    name = "일목균형표 전략"

    @classmethod
    async def check_entry_signal(cls, redis_client, symbol, df, ...):
        # 1차 매수 로직 (TK 골든크로스 등)
        pass

    @classmethod
    async def check_exit_signal(cls, redis_client, position_id, ...):
        # 매도 로직 (손절, 구름 이탈 등)
        pass

    @classmethod
    async def check_second_buy_signal(cls, db, redis_client, swing_id, ...):
        # 2차 매수 로직
        pass
```

#### 2. 팩토리에 등록
```python
# trading_strategy_factory.py
from .ichimoku_trading_strategy import IchimokuTradingStrategy

class TradingStrategyFactory:
    _strategies: dict[str, Type[TradingStrategy]] = {
        'A': SingleEMAStrategy,
        'B': IchimokuTradingStrategy,  # 추가
        'C': SingleEMAStrategy,
    }
```

#### 3. 자동으로 배치에서 사용
`auto_swing_batch.py`는 자동으로 SWING_TYPE에 따라 전략을 선택합니다.

## 아키텍처 장점

### 1. 명확한 인터페이스
- 추상 베이스 클래스로 필수 메서드 강제
- 새로운 전략 추가 시 구현할 메서드가 명확함

### 2. 타입 안전성
- Type hints로 IDE 자동완성 지원
- 컴파일 타임 오류 감지

### 3. 확장성
- 팩토리 패턴으로 전략 추가가 용이
- 기존 코드 수정 최소화

### 4. 일관성
- 백테스트와 실시간 거래 모두 동일한 패턴 사용
- 전략 간 코드 구조 통일

## SWING_TYPE 매핑

| SWING_TYPE | 백테스트 전략 | 실시간 거래 전략 |
|------------|---------------|------------------|
| A | EMAStrategy | SingleEMAStrategy |
| B | IchimokuStrategy | SingleEMAStrategy (TODO) |
| C | SingleEMABacktestStrategy | SingleEMAStrategy |

## 참고
- 백테스트 전략: `app/domain/swing/strategy_factory.py`
- 실시간 거래 전략: `app/domain/swing/trading_strategy_factory.py`
- 배치 실행: `app/domain/swing/auto_swing_batch.py`