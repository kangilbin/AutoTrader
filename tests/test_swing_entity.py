"""
SwingTrade 엔티티 - SIGNAL 상태 전이 유닛 테스트

실행:
  PYTHONPATH=. python -m unittest tests.test_swing_entity -v
  또는 (pytest 설치 시):
  pytest tests/test_swing_entity.py -v

대상: app/domain/swing/entity.py 의 SwingTrade
  - SIGNAL 상태머신: 0(대기) → 1(매수) → 2(1차익절) → 3(수급이탈대기) → 4(재유입대기) → 0
  - DB/외부 API 의존 없이 객체 속성 조작만으로 검증
"""
import unittest
from decimal import Decimal

from app.domain.swing.entity import SwingTrade
from app.exceptions import ValidationError


def _make_swing(**overrides) -> SwingTrade:
    """테스트용 SwingTrade 생성 헬퍼 (DB 세션 불필요)"""
    defaults = dict(
        ACCOUNT_NO="12345678-01",
        MRKT_CODE="J",
        ST_CODE="005930",
        SWING_TYPE="A",
        SIGNAL=0,
    )
    defaults.update(overrides)
    return SwingTrade(**defaults)


class TestSwingValidate(unittest.TestCase):
    """validate(): 필수값/허용값 검증"""

    def test_valid_swing_passes(self):
        _make_swing().validate()  # 예외 없이 통과해야 함

    def test_invalid_market_code_raises(self):
        with self.assertRaises(ValidationError):
            _make_swing(MRKT_CODE="XX").validate()

    def test_invalid_swing_type_raises(self):
        with self.assertRaises(ValidationError):
            _make_swing(SWING_TYPE="Z").validate()

    def test_missing_required_fields_raise(self):
        cases = {
            "ACCOUNT_NO": _make_swing(ACCOUNT_NO=""),
            "MRKT_CODE": _make_swing(MRKT_CODE=""),
            "ST_CODE": _make_swing(ST_CODE=""),
        }
        for field, swing in cases.items():
            with self.subTest(missing=field):
                with self.assertRaises(ValidationError):
                    swing.validate()


class TestSwingStatusQueries(unittest.TestCase):
    """상태 조회 메서드: SIGNAL 값에 따른 분기"""

    def test_status_flags(self):
        # (SIGNAL, is_waiting, is_cooling_down, has_position, is_partial_position)
        cases = [
            (0, True, False, False, False),
            (1, False, False, True, False),
            (2, False, False, True, True),
            (3, False, True, False, False),
            (4, False, True, False, False),
        ]
        for signal, waiting, cooling, has_pos, partial in cases:
            with self.subTest(signal=signal):
                s = _make_swing(SIGNAL=signal)
                self.assertEqual(s.is_waiting(), waiting)
                self.assertEqual(s.is_cooling_down(), cooling)
                self.assertEqual(s.has_position(), has_pos)
                self.assertEqual(s.is_partial_position(), partial)


class TestSwingTransitions(unittest.TestCase):
    """상태 전이: 정상 흐름과 가드(잘못된 상태에서 차단)"""

    def test_full_cycle_0_to_0(self):
        """정상 사이클: 0 → 1 → 2 → (전량매도) 3 → 4 → 0"""
        s = _make_swing(SIGNAL=0)

        s.transition_to_buy(entry_price=70000, hold_qty=10, peak_price=70000)
        self.assertEqual(s.SIGNAL, 1)
        self.assertEqual(s.ENTRY_PRICE, Decimal(70000))
        self.assertEqual(s.HOLD_QTY, 10)
        self.assertEqual(s.PEAK_PRICE, Decimal(70000))

        s.transition_to_partial(sold_qty=5)
        self.assertEqual(s.SIGNAL, 2)
        self.assertEqual(s.HOLD_QTY, 5)
        self.assertIsNone(s.PEAK_PRICE)  # 2차 익절 위해 PEAK 리셋

        s.reset_cycle()
        self.assertEqual(s.SIGNAL, 3)
        self.assertEqual(s.HOLD_QTY, 0)
        self.assertIsNone(s.ENTRY_PRICE)

        s.transition_to_reentry_waiting()
        self.assertEqual(s.SIGNAL, 4)

        s.transition_to_waiting()
        self.assertEqual(s.SIGNAL, 0)

    def test_buy_only_from_waiting(self):
        """매수는 SIGNAL 0에서만 가능 — 보유 중(1)이면 차단"""
        s = _make_swing(SIGNAL=1)
        with self.assertRaises(ValidationError):
            s.transition_to_buy(entry_price=70000, hold_qty=10, peak_price=70000)

    def test_partial_only_from_signal_1(self):
        """1차 익절은 SIGNAL 1에서만 가능"""
        s = _make_swing(SIGNAL=2)
        with self.assertRaises(ValidationError):
            s.transition_to_partial(sold_qty=5)

    def test_reentry_waiting_only_from_signal_3(self):
        """수급 재유입 대기 전환은 SIGNAL 3에서만 가능"""
        s = _make_swing(SIGNAL=0)
        with self.assertRaises(ValidationError):
            s.transition_to_reentry_waiting()

    def test_waiting_only_from_signal_4(self):
        """매수 대기 전환은 SIGNAL 4에서만 가능"""
        s = _make_swing(SIGNAL=3)
        with self.assertRaises(ValidationError):
            s.transition_to_waiting()


if __name__ == "__main__":
    unittest.main()