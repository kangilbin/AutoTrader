"""
실시간 거래 전략 팩토리
"""
import logging
from typing import Type
from .strategies.base_trading_strategy import TradingStrategy
from .strategies.single_ema_strategy import SingleEMAStrategy

logger = logging.getLogger(__name__)


class TradingStrategyFactory:
    """
    실시간 거래 전략 팩토리

    SWING_TYPE에 따라 적절한 실시간 거래 전략 클래스를 반환합니다.
    백테스트 전략(BacktestStrategy)과 분리되어 있으며,
    실시간 매매에 필요한 메서드(check_entry_signal 등)를 제공하는
    TradingStrategy를 상속한 전략만 등록됩니다.
    """

    # 실시간 거래 전략 매핑
    _strategies: dict[str, Type[TradingStrategy]] = {
        # 'A': SingleEMAStrategy,  # 이평선 전략
        # 'B': SingleEMAStrategy,  # 일목균형표 (TODO: IchimokuTradingStrategy 구현 필요)
        'S': SingleEMAStrategy,  # 단일 20EMA 전략 (기본)
    }

    @classmethod
    def get_strategy(cls, swing_type: str) -> Type[TradingStrategy]:
        """
        SWING_TYPE에 따른 실시간 거래 전략 클래스 반환

        현재는 모든 타입이 SingleEMAStrategy를 사용합니다.
        향후 다른 실시간 거래 전략이 추가되면 매핑을 확장하세요.

        Args:
            swing_type: SWING_TRADE.SWING_TYPE
                - 'A': 이평선 전략
                - 'B': 일목균형표 전략 (현재 SingleEMAStrategy 사용)
                - 'C': 단일 20EMA 전략
                - 기타: SingleEMAStrategy (기본값)

        Returns:
            전략 클래스 (check_entry_signal, check_exit_signal, check_second_buy_signal 제공)
        """
        strategy = cls._strategies.get(swing_type, SingleEMAStrategy)

        if swing_type not in cls._strategies:
            logger.warning(
                f"지원하지 않는 SWING_TYPE='{swing_type}', "
                f"기본 전략(SingleEMAStrategy) 사용"
            )

        return strategy

    @classmethod
    def get_available_strategies(cls) -> dict:
        """
        사용 가능한 실시간 거래 전략 목록 반환

        Returns:
            {전략코드: 전략클래스명} 딕셔너리
        """
        return {
            code: strategy.__name__
            for code, strategy in cls._strategies.items()
        }