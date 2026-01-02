"""
백테스트 전략 팩토리
"""
from typing import Dict
from .strategies.base_strategy import BacktestStrategy
from .strategies.ema_strategy import EMAStrategy
from .strategies.ichimoku_strategy import IchimokuStrategy
from .strategies.single_ema_backtest_strategy import SingleEMABacktestStrategy


class StrategyFactory:
    """전략 생성 팩토리 클래스"""

    # 전략 매핑
    _strategies: Dict[str, BacktestStrategy] = {
        "A": EMAStrategy(),
        "B": IchimokuStrategy(),
        "S": SingleEMABacktestStrategy(),
    }
    
    @classmethod
    def get_strategy(cls, strategy_type: str) -> BacktestStrategy:
        """
        전략 타입에 해당하는 전략 객체 반환
        
        Args:
            strategy_type: "A" (이평선), "B" (일목균형표), "C" (단순 이평선)
            
        Returns:
            BacktestStrategy 인스턴스
            
        Raises:
            ValueError: 지원하지 않는 전략 타입
        """
        strategy = cls._strategies.get(strategy_type)
        
        if not strategy:
            raise ValueError(
                f"지원하지 않는 전략 타입: {strategy_type}. "
                f"사용 가능한 타입: {list(cls._strategies.keys())}"
            )
        
        return strategy

    @classmethod
    def get_available_strategies(cls) -> Dict[str, str]:
        """
        사용 가능한 전략 목록 반환
        
        Returns:
            {전략코드: 전략이름} 딕셔너리
        """
        return {
            code: strategy.name 
            for code, strategy in cls._strategies.items()
        }
