"""
풀 설정(POOL_SETTING) 엔티티 테스트

실행:
  uv run pytest tests/test_pool_setting.py -v

풀 설정이 켜진 계좌·시장만 새 방식으로 매수하므로, 기본값이 꺼짐이고 입력 실수(과도한 리스크 등)가
엔티티에서 막히는지 고정한다.
"""
import unittest
from decimal import Decimal

from app.common.database import Base
from app.domain.pool.entity import (
    DEFAULT_MAX_OPEN_RISK_PCT,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_POSITIONS,
    DEFAULT_RISK_PCT,
    PARK_ETF,
    PoolSetting,
    pool_market_of,
)
from app.domain.pool.schemas import PoolSettingResponse, PoolSettingUpdateRequest
from app.exceptions import ValidationError


class CreateTest(unittest.TestCase):
    def test_defaults_are_agreed_values_and_off(self):
        """합의한 기본값(집중형: 리스크 0.70%·종목당 9.5%·10종목·총 8%)이고, 기본은 꺼진 상태"""
        s = PoolSetting.create("12345678-01", "KR")
        self.assertEqual(s.USE_YN, "N")
        self.assertFalse(s.is_active())
        self.assertEqual(
            (s.RISK_PCT, s.MAX_POSITIONS, s.MAX_POSITION_PCT, s.MAX_OPEN_RISK_PCT),
            (DEFAULT_RISK_PCT, DEFAULT_MAX_POSITIONS, DEFAULT_MAX_POSITION_PCT, DEFAULT_MAX_OPEN_RISK_PCT))
        self.assertEqual(
            (DEFAULT_RISK_PCT, DEFAULT_MAX_POSITIONS, DEFAULT_MAX_POSITION_PCT, DEFAULT_MAX_OPEN_RISK_PCT),
            (Decimal("0.0070"), 10, Decimal("0.095"), Decimal("0.08")))

    def test_active(self):
        self.assertTrue(PoolSetting.create("A", "US", use_yn="Y").is_active())

    def test_max_positions_none_means_unlimited(self):
        self.assertIsNone(PoolSetting.create("A", "KR", max_positions=None).MAX_POSITIONS)

    def test_float_inputs_do_not_leak_binary_noise(self):
        s = PoolSetting.create("A", "KR", risk_pct=0.0005)
        self.assertEqual(s.RISK_PCT, Decimal("0.0005"))


class ValidationTest(unittest.TestCase):
    def assertInvalid(self, **kw):
        with self.assertRaises(ValidationError):
            PoolSetting.create("A", **{"market": "KR", **kw})

    def test_market(self):
        self.assertInvalid(market="JP")

    def test_account_required(self):
        with self.assertRaises(ValidationError):
            PoolSetting.create("", "KR")

    def test_use_yn(self):
        self.assertInvalid(use_yn="X")

    def test_risk_bounds(self):
        """0 이하와 1% 초과는 막는다 — 정확히 1%는 허용"""
        self.assertInvalid(risk_pct=0)
        self.assertInvalid(risk_pct=-0.001)
        self.assertInvalid(risk_pct=0.0101)
        PoolSetting.create("A", "KR", risk_pct=0.01, max_open_risk_pct=0.08)

    def test_max_positions(self):
        self.assertInvalid(max_positions=0)

    def test_max_position_pct(self):
        self.assertInvalid(max_position_pct=0)
        self.assertInvalid(max_position_pct=1.01)
        PoolSetting.create("A", "KR", max_position_pct=1)

    def test_open_risk_bounds(self):
        self.assertInvalid(max_open_risk_pct=0)
        self.assertInvalid(max_open_risk_pct=0.21)

    def test_trade_risk_cannot_exceed_total_open_risk(self):
        """거래당 리스크가 총 열린 리스크 상한보다 크면 첫 매수도 한도에 걸린다"""
        self.assertInvalid(risk_pct=0.005, max_open_risk_pct=0.004)


class UpdateTest(unittest.TestCase):
    def test_partial_update_and_mod_dt(self):
        s = PoolSetting.create("A", "KR")
        s.update_settings(USE_YN="Y", PARK_RESERVE_PCT=0.1)
        self.assertTrue(s.is_active())
        self.assertEqual(s.PARK_RESERVE_PCT, Decimal("0.1"))
        self.assertEqual(s.MAX_POSITIONS, DEFAULT_MAX_POSITIONS)   # 고정값은 그대로
        self.assertIsNotNone(s.MOD_DT)

    def test_invalid_update_rolls_back(self):
        """검증에 실패하면 원래 값으로 되돌린다 — 일부만 바뀐 설정이 남으면 안 된다"""
        s = PoolSetting.create("A", "KR")
        with self.assertRaises(ValidationError):
            s.update_settings(USE_YN="Y", PARK_RESERVE_PCT=0.9)
        self.assertEqual((s.USE_YN, s.PARK_RESERVE_PCT), ("N", Decimal("0.05")))
        self.assertIsNone(s.MOD_DT)

    def test_fixed_values_cannot_be_updated(self):
        """거래당 리스크·종목당 최대 비중·미청산 리스크 한도는 종목 수에서 정해지는 값 — 같은 값을 보내도 거절한다"""
        s = PoolSetting.create("A", "KR")
        for field, value in (("RISK_PCT", 0.007), ("MAX_POSITION_PCT", 0.095), ("MAX_OPEN_RISK_PCT", 0.08)):
            with self.assertRaises(ValidationError, msg=field):
                s.update_settings(USE_YN="Y", **{field: value})
        self.assertEqual(s.USE_YN, "N", "거절되면 함께 보낸 항목도 반영되지 않는다")

    def test_rows_loaded_from_db_get_the_fixed_values(self):
        """고정값은 컬럼이 아니라 DB 에서 읽은 행(reconstructor)에도 항상 채워진다 — 저장된 값과 어긋날 수 없다"""
        s = PoolSetting(ACCOUNT_NO="A", MARKET="KR", USE_YN="Y")
        s.RISK_PCT = Decimal("0.5")                      # 어떤 경로로든 틀어졌다고 가정
        s._on_load()
        self.assertEqual((s.RISK_PCT, s.MAX_POSITIONS, s.MAX_POSITION_PCT, s.MAX_OPEN_RISK_PCT),
                         (Decimal("0.0070"), 10, Decimal("0.095"), Decimal("0.08")))

    def test_only_the_count_is_a_column_the_rest_is_derived(self):
        import app.common.database as database
        database._import_all_entities()
        cols = set(Base.metadata.tables["POOL_SETTING"].columns.keys())
        self.assertIn("MAX_POSITIONS", cols)
        self.assertTrue({"RISK_PCT", "MAX_POSITION_PCT", "MAX_OPEN_RISK_PCT"}.isdisjoint(cols))

    def test_derived_limits_follow_the_count_and_always_stay_consistent(self):
        """어떤 종목 수에서도: 합이 95% 이내, 거래당 리스크는 비중에서 잘리는 값, 상한 1% 이내, 총 리스크가 한도 안"""
        from app.domain.pool.entity import (
            MAX_POSITIONS_SETTING, MIN_POSITIONS_SETTING, TARGET_EXPOSURE, WIDEST_STOP_PCT, derive_limits)
        for n in range(MIN_POSITIONS_SETTING, MAX_POSITIONS_SETTING + 1):
            pct, risk = derive_limits(n)
            self.assertLessEqual(pct * n, TARGET_EXPOSURE, f"N={n}: 합계가 95% 를 넘음")
            self.assertGreaterEqual(pct * n, TARGET_EXPOSURE - Decimal("0.003"), f"N={n}: 내림 오차가 큼")
            self.assertGreaterEqual(risk, pct * WIDEST_STOP_PCT, f"N={n}: 손절폭 7% 종목이 비중에서 안 잘림")
            self.assertLessEqual(risk, Decimal("0.01"), f"N={n}: 거래당 리스크 상한(1%) 초과")
            self.assertLessEqual(n * pct * WIDEST_STOP_PCT, DEFAULT_MAX_OPEN_RISK_PCT, f"N={n}: 총 리스크가 한도 초과")
        self.assertEqual(derive_limits(10), (Decimal("0.0950"), Decimal("0.0070")))      # 기본값은 합의한 구성 그대로

    def test_out_of_range_stored_count_is_clamped_when_loaded_but_not_when_created(self):
        """DB 에 범위 밖 값(예전 버전·수동 수정)이 있어도 읽을 때 7~30 으로 맞춘다 — 그대로면 거래당 리스크가 1% 를 넘고 풀 켜기도 막힌다"""
        for stored, expected in ((1, 7), (3, 7), (6, 7), (7, 7), (30, 30), (31, 30), (99, 30)):
            s = PoolSetting(ACCOUNT_NO="A", MARKET="KR", MAX_POSITIONS=stored, USE_YN="N",
                            PARK_RESERVE_PCT=Decimal("0.05"))      # DB 에서 읽은 행과 같은 상태
            s._on_load()
            self.assertEqual(s.MAX_POSITIONS, expected, stored)
            self.assertLessEqual(s.RISK_PCT, Decimal("0.01"), f"stored={stored}: 파생 리스크가 상한 이내여야 한다")
            s.update_settings(USE_YN="Y")       # 켜기(검증)가 막히지 않는다
        # 테스트·연구가 create(max_positions=...) 로 만든 값은 건드리지 않는다
        made = PoolSetting.create("A", "KR", max_positions=2, risk_pct="0.001", max_position_pct="0.1")
        self.assertEqual(made.MAX_POSITIONS, 2)

    def test_below_the_minimum_would_break_the_risk_ceiling(self):
        """6종목이면 거래당 리스크가 1% 를 넘는다 — 그래서 선택 범위가 7부터다"""
        from app.domain.pool.entity import derive_limits
        self.assertGreater(derive_limits(6)[1], Decimal("0.01"))

    def test_count_range_is_validated_on_update_only_and_rolls_derived_back(self):
        s = PoolSetting.create("A", "KR")
        for bad in (6, 31, 0, None, True, 10.5, "10"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                s.update_settings(MAX_POSITIONS=bad)
        self.assertEqual((s.MAX_POSITIONS, s.MAX_POSITION_PCT, s.RISK_PCT), (10, Decimal("0.0950"), Decimal("0.0070")))
        s.update_settings(MAX_POSITIONS=20)
        self.assertEqual((s.MAX_POSITIONS, s.MAX_POSITION_PCT), (20, Decimal("0.0475")))
        with self.assertRaises(ValidationError):
            s.update_settings(MAX_POSITIONS=12, PARK_RESERVE_PCT=0.9)     # 함께 보낸 항목이 틀리면 종목 수·파생값도 되돌린다
        self.assertEqual((s.MAX_POSITIONS, s.MAX_POSITION_PCT), (20, Decimal("0.0475")))

    def test_unknown_field_rejected(self):
        """계좌·시장(키)은 바꿀 수 없다"""
        s = PoolSetting.create("A", "KR")
        with self.assertRaises(ValidationError):
            s.update_settings(MARKET="US")


class MarketMappingTest(unittest.TestCase):
    def test_pool_market_of(self):
        for code in ("J", "NX", "UN"):
            self.assertEqual(pool_market_of(code), "KR")
        for code in ("NYS", "NAS", "AMS", "US"):
            self.assertEqual(pool_market_of(code), "US")


class SchemaAndTableTest(unittest.TestCase):
    def test_response_from_entity(self):
        r = PoolSettingResponse.model_validate(PoolSetting.create("A", "KR"))
        self.assertEqual((r.ACCOUNT_NO, r.MARKET, r.USE_YN), ("A", "KR", "N"))

    def test_request_rejects_unknown_market(self):
        with self.assertRaises(Exception):
            PoolSettingUpdateRequest(ACCOUNT_NO="A", MARKET="JP")

    def test_table_registered_with_composite_key(self):
        """create_all 이 만들 수 있도록 메타데이터에 등록되고, (계좌, 시장)이 기본키다"""
        import app.common.database as database
        database._import_all_entities()
        table = Base.metadata.tables["POOL_SETTING"]
        self.assertEqual({c.name for c in table.primary_key.columns}, {"ACCOUNT_NO", "MARKET"})



class ParkingSettingTest(unittest.TestCase):
    """파킹 설정 — 종목은 시장별 고정, 설정은 남길 현금 비율뿐. 파킹은 풀이 켜져 있으면 항상 허용된다"""

    def test_default_reserve_is_5pct(self):
        s = PoolSetting.create("A", "KR", use_yn="Y")
        self.assertEqual(s.PARK_RESERVE_PCT, Decimal("0.05"))

    def test_enabled_follows_pool_only(self):
        s = PoolSetting.create("A", "KR", use_yn="Y")
        self.assertTrue(s.parking_enabled(), "별도 스위치 없이 풀이 켜져 있으면 허용")
        s.update_settings(USE_YN="N")
        self.assertFalse(s.parking_enabled(), "풀이 꺼지면 파킹도 동작하지 않는다")

    def test_etf_is_fixed_per_market(self):
        kr, us = PoolSetting.create("A", "KR"), PoolSetting.create("A", "US")
        self.assertEqual((kr.park_code, kr.park_mrkt_code), ("459580", "J"))
        self.assertEqual((us.park_code, us.park_mrkt_code), ("SGOV", "NYS"))
        self.assertEqual(PARK_ETF["KR"]["name"], "KODEX CD금리액티브(합성)")

    def test_etf_cannot_be_overridden(self):
        s = PoolSetting.create("A", "KR")
        for field in ("PARK_CODE", "PARK_EXCG"):
            with self.assertRaises(ValidationError):
                s.update_settings(**{field: "X"})

    def test_park_yn_is_no_longer_settable(self):
        """파킹 사용 스위치는 없앴다 — 보내면 조용히 무시하지 않고 거절한다"""
        s = PoolSetting.create("A", "KR")
        with self.assertRaises(ValidationError):
            s.update_settings(PARK_YN="Y")

    def test_reserve_range(self):
        s = PoolSetting.create("A", "KR")
        for bad in (-0.01, 0.6):
            with self.assertRaises(ValidationError):
                s.update_settings(PARK_RESERVE_PCT=bad)
        with self.assertRaises(ValidationError):
            s.update_settings(PARK_RESERVE_PCT=None)
        s.update_settings(PARK_RESERVE_PCT=0)
        self.assertEqual(s.PARK_RESERVE_PCT, Decimal("0"))

    def test_response_has_reserve_and_no_park_yn(self):
        r = PoolSettingResponse.model_validate(PoolSetting.create("A", "KR")).model_dump()
        self.assertEqual(r["PARK_RESERVE_PCT"], Decimal("0.05"))
        self.assertNotIn("PARK_YN", r)

    def test_request_rejects_park_yn(self):
        with self.assertRaises(Exception):
            PoolSettingUpdateRequest(ACCOUNT_NO="A", MARKET="KR", PARK_YN="Y")


if __name__ == "__main__":
    unittest.main()
