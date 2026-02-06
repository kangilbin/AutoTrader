# Swing Trading Domain

스윙 매매 도메인 - 백테스트와 실시간 자동 거래 기능 제공

## 디렉토리 구조

```
app/domain/swing/
├── __init__.py
├── entity.py                    # 도메인 엔티티 (SwingTrade, EmaOption 등)
├── schemas.py                   # API Request/Response DTO
├── repository.py                # 데이터 접근 계층
├── service.py                   # 비즈니스 로직 + 트랜잭션 관리
├── router.py                    # API 엔드포인트
├── tech_analysis.py             # 공통 기술 분석 (EMA, 일목균형표 등)
│
├── backtest/                    # 📊 백테스트 (과거 데이터 검증)
│   ├── __init__.py
│   ├── backtest_service.py      # 백테스트 실행 서비스
│   ├── strategy_factory.py      # 백테스트 전략 팩토리
│   └── strategies/
│       ├── base_strategy.py     # BacktestStrategy 추상 클래스
│       ├── ema_strategy.py      # 이평선 백테스트 전략
│       ├── ichimoku_strategy.py # 일목균형표 백테스트 전략
│       └── single_ema_backtest_strategy.py
│
└── trading/                     # 🤖 실시간 자동 거래
    ├── __init__.py
    ├── auto_swing_batch.py      # 배치 작업 (5분 간격)
    ├── order_executor.py        # 주문 실행 로직
    ├── trading_strategy_factory.py  # 실시간 거래 전략 팩토리
    └── strategies/
        ├── base_trading_strategy.py  # TradingStrategy 추상 클래스
        └── single_ema_strategy.py    # 단일 20EMA 실시간 전략
```

## 백테스트 (Backtest)

### 목적
과거 데이터로 매매 전략의 성능을 검증

### 핵심 파일
- `backtest/strategies/base_strategy.py`: 모든 백테스트 전략의 베이스 클래스
- `backtest/strategy_factory.py`: SWING_TYPE에 따라 전략 선택
- `backtest/backtest_service.py`: 백테스트 실행 및 결과 계산

### 특징
- 동기 방식 실행 (`compute()` 메서드)
- 매수/매도 시뮬레이션
- 수익률, 거래 내역 반환

### 사용 예시
```python
from app.domain.swing.backtest.strategy_factory import StrategyFactory

strategy = StrategyFactory.get_strategy('A')  # 이평선 전략
result = strategy.compute(prices_df, params)
```

## 실시간 자동 거래 (Trading)

### 목적
실시간 시세를 기반으로 매매 신호 생성 및 자동 주문 실행

### 핵심 파일
- `trading/strategies/base_trading_strategy.py`: 모든 실시간 전략의 베이스 클래스
- `trading/trading_strategy_factory.py`: SWING_TYPE에 따라 전략 선택
- `trading/auto_swing_batch.py`: 배치 작업 (평일 10:00-15:00, 5분 간격)
- `trading/order_executor.py`: 실제 주문 실행

### 특징
- 비동기 방식 실행 (`async def` 메서드)
- Redis 상태 관리 (연속 신호 확인)
- DB 연동 (TRADE_HISTORY, STOCK_DAY_HISTORY)
- 분할 매수/매도 지원

### 매매 신호 메서드
```python
class TradingStrategy(ABC):
    @abstractmethod
    async def check_entry_signal(...) -> Optional[Dict]:
        """1차 매수 신호"""
    
    @abstractmethod
    async def check_second_buy_signal(...) -> Optional[Dict]:
        """2차 매수 신호"""
    
    @abstractmethod
    async def check_exit_signal(...) -> Dict:
        """매도 신호"""
```

### 사용 예시
```python
from app.domain.swing.trading.trading_strategy_factory import TradingStrategyFactory

strategy = TradingStrategyFactory.get_strategy('S')  # SingleEMA 전략
signal = await strategy.check_entry_signal(...)
```

## SIGNAL 상태 관리

```
0 (대기) → [1차 매수] → 1 (부분 포지션) → [2차 매수] → 2 (전체 포지션)
                             ↓                               ↓
                         [1차 매도]                      [1차 매도]
                             ↓                               ↓
                          3 (부분 청산) → [2차 매도] → 0 (사이클 완료)
```

## 새로운 전략 추가 방법

### 백테스트 전략 추가
```python
# backtest/strategies/my_strategy.py
from .base_strategy import BacktestStrategy

class MyBacktestStrategy(BacktestStrategy):
    def __init__(self):
        super().__init__("나의 전략")
    
    def compute(self, prices_df, params):
        # 백테스트 로직
        pass

# backtest/strategy_factory.py에 등록
_strategies = {
    "A": EMAStrategy(),
    "M": MyBacktestStrategy(),  # 추가
}
```

### 실시간 거래 전략 추가
```python
# trading/strategies/my_trading_strategy.py
from .base_trading_strategy import TradingStrategy

class MyTradingStrategy(TradingStrategy):
    name = "나의 실시간 전략"
    
    @classmethod
    async def check_entry_signal(cls, ...):
        # 1차 매수 로직
        pass
    
    @classmethod
    async def check_second_buy_signal(cls, ...):
        # 2차 매수 로직
        pass
    
    @classmethod
    async def check_exit_signal(cls, ...):
        # 매도 로직
        pass

# trading/trading_strategy_factory.py에 등록
_strategies = {
    'S': SingleEMAStrategy,
    'M': MyTradingStrategy,  # 추가
}
```

## 참고
- 백테스트는 `backtest/` 디렉토리에서 독립적으로 관리
- 실시간 거래는 `trading/` 디렉토리에서 독립적으로 관리
- 공통 로직(entity, repository, service)은 상위 디렉토리에 위치
- 기술 분석 함수(`tech_analysis.py`)는 양쪽에서 공통으로 사용
