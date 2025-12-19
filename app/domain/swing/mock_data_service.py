"""
Mock data service for foreign/program net buying data
실제 API 연동 전까지 사용할 모의 데이터 생성 서비스
"""
import random
from typing import Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MockMarketDataService:
    """시장 수급 모의 데이터 서비스"""

    @staticmethod
    def get_net_buying_data(st_code: str, current_price: float, volume: int) -> Dict[str, int]:
        """
        외국인/기관 순매수량 모의 데이터 생성

        Args:
            st_code: 종목 코드
            current_price: 현재가
            volume: 당일 거래량

        Returns:
            {
                "frgn_ntby_qty": 외국인 순매수량 (음수 가능),
                "pgtr_ntby_qty": 기관 순매수량 (음수 가능)
            }
        """
        # 시드 설정 (종목코드 + 날짜로 일관성 유지)
        seed = int(st_code) + datetime.now().day
        random.seed(seed)

        # 거래량의 -10% ~ +10% 범위에서 순매수량 생성
        base_range = int(volume * 0.1)

        # 외국인 순매수량 (-10% ~ +10%)
        frgn_ntby_qty = random.randint(-base_range, base_range)

        # 기관 순매수량 (-10% ~ +10%)
        pgtr_ntby_qty = random.randint(-base_range, base_range)

        # 가격 상승 시 순매수 가능성 높이기 (현실성 부여)
        # 간단한 휴리스틱: 거래량이 많고 가격이 높으면 순매수 경향
        if volume > 1000000:  # 거래량 100만주 이상
            # 70% 확률로 순매수로 변환
            if random.random() < 0.7:
                frgn_ntby_qty = abs(frgn_ntby_qty)
                pgtr_ntby_qty = abs(pgtr_ntby_qty)

        logger.debug(
            f"[MOCK] {st_code} - 외국인: {frgn_ntby_qty:,}, "
            f"기관: {pgtr_ntby_qty:,} (거래량: {volume:,})"
        )

        return {
            "frgn_ntby_qty": frgn_ntby_qty,
            "pgtr_ntby_qty": pgtr_ntby_qty
        }
