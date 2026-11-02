"""
풀 설정 서비스 테스트

실행:
  uv run pytest tests/test_pool_service.py -v

SWING_TRADE 와 마찬가지로 풀 설정에도 USER_ID 가 없다. 소유자는 ACCOUNT 로만 확인되므로
그 검증이 빠지면 계좌번호만 아는 사용자가 남의 자동매매 설정(리스크·활성화)을 바꾼다.
자기 계좌는 정상 동작해서 사용 중에는 드러나지 않으므로 테스트로 고정한다.
"""
import unittest
from unittest.mock import AsyncMock
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from pydantic import ValidationError as PydanticValidationError
from app.domain.pool.entity import DEFAULT_MAX_POSITIONS, DEFAULT_RISK_PCT, PoolSetting
from app.domain.pool.schemas import PoolSettingUpdateRequest
from app.domain.pool.service import PoolService
from app.exceptions import DuplicateError, NotFoundError, ValidationError

OWNER, INTRUDER, ACCOUNT = "owner", "intruder", "12345678-01"


class FakeDB:
    def __init__(self, commit_error=None):
        self.commits = 0
        self.rollbacks = 0
        self.commit_error = commit_error

    async def commit(self):
        if self.commit_error:
            raise self.commit_error
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakePoolRepo:
    def __init__(self, rows=()):
        self.rows = {(r.ACCOUNT_NO, r.MARKET): r for r in rows}
        self.saved = []

    async def find(self, account_no, market):
        return self.rows.get((account_no, market))

    async def find_by_account(self, account_no):
        return [r for (a, _), r in self.rows.items() if a == account_no]

    async def save(self, setting):
        self.saved.append(setting)
        self.rows[(setting.ACCOUNT_NO, setting.MARKET)] = setting
        return setting


class FakeAccountRepo:
    """ACCOUNT 조회 대역 — 소유자의 계좌에만 인증키 ID 를 돌려준다"""

    async def find_auth_id_by_account_no(self, user_id, account_no):
        return 7 if (user_id == OWNER and account_no == ACCOUNT) else None


def make_service(rows=(), commit_error=None):
    service = PoolService(FakeDB(commit_error))
    service.repo = FakePoolRepo(rows)
    service.account_repo = FakeAccountRepo()
    return service


def request(**kw):
    return PoolSettingUpdateRequest(ACCOUNT_NO=ACCOUNT, MARKET=kw.pop("MARKET", "KR"), **kw)


class GetSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_for_both_markets_without_persisting(self):
        service = make_service()
        result = await service.get_settings(OWNER, ACCOUNT)
        self.assertEqual([r["MARKET"] for r in result], ["KR", "US"])
        for r in result:
            self.assertEqual((r["USE_YN"], r["RISK_PCT"], r["MAX_POSITIONS"]), ("N", DEFAULT_RISK_PCT, DEFAULT_MAX_POSITIONS))
        self.assertEqual(service.repo.saved, [])                  # 조회만으로 행을 만들지 않는다
        self.assertEqual(service.db.commits, 0)

    async def test_saved_row_overrides_default_for_its_market_only(self):
        kr = PoolSetting.create(ACCOUNT, "KR", use_yn="Y")
        by_market = {r["MARKET"]: r for r in await make_service([kr]).get_settings(OWNER, ACCOUNT)}
        self.assertEqual((by_market["KR"]["USE_YN"], by_market["US"]["USE_YN"]), ("Y", "N"))
        for r in by_market.values():       # 고정값은 저장된 행과 무관하게 항상 같다
            self.assertEqual((r["RISK_PCT"], r["MAX_POSITIONS"], r["MAX_POSITION_PCT"], r["MAX_OPEN_RISK_PCT"]),
                             (DEFAULT_RISK_PCT, DEFAULT_MAX_POSITIONS, Decimal("0.095"), Decimal("0.08")))

    async def test_foreign_account_looks_nonexistent(self):
        with self.assertRaises(NotFoundError):
            await make_service().get_settings(INTRUDER, ACCOUNT)


class UpdateSettingTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_row_from_defaults_and_applies_sent_fields(self):
        service = make_service()
        result = await service.update_setting(OWNER, request(USE_YN="Y", PARK_RESERVE_PCT=Decimal("0.1")))
        self.assertEqual((result["USE_YN"], result["PARK_RESERVE_PCT"]), ("Y", Decimal("0.1")))
        self.assertEqual((result["RISK_PCT"], result["MAX_POSITIONS"]), (DEFAULT_RISK_PCT, DEFAULT_MAX_POSITIONS))   # 고정값
        self.assertEqual((len(service.repo.saved), service.db.commits), (1, 1))

    async def test_partial_update_keeps_other_fields(self):
        existing = PoolSetting.create(ACCOUNT, "KR", use_yn="Y")
        result = await make_service([existing]).update_setting(OWNER, request(PARK_RESERVE_PCT=Decimal("0.1")))
        self.assertEqual((result["USE_YN"], result["PARK_RESERVE_PCT"]), ("Y", Decimal("0.1")))

    async def test_fixed_values_cannot_be_sent_at_all(self):
        """거래당 리스크·종목당 최대 비중·미청산 리스크 한도는 종목 수에서 정해지는 값 — 요청에 담으면 조용히 무시하지 않고 거절한다"""
        for field, value in (("RISK_PCT", Decimal("0.001")), ("MAX_POSITION_PCT", Decimal("0.1")),
                             ("MAX_OPEN_RISK_PCT", Decimal("0.2"))):
            with self.assertRaises(PydanticValidationError, msg=field):
                request(**{field: value})

    async def test_omitted_fields_stay_unchanged(self):
        existing = PoolSetting.create(ACCOUNT, "KR", use_yn="Y")
        existing.update_settings(PARK_RESERVE_PCT=0.1)
        result = await make_service([existing]).update_setting(OWNER, request(MAX_POSITIONS=12))
        self.assertEqual((result["USE_YN"], result["MAX_POSITIONS"], result["PARK_RESERVE_PCT"]), ("Y", 12, Decimal("0.1")))

    async def test_markets_are_independent_rows(self):
        service = make_service()
        await service.update_setting(OWNER, request(MARKET="KR", USE_YN="Y"))
        await service.update_setting(OWNER, request(MARKET="US", PARK_RESERVE_PCT=Decimal("0.1")))
        result = {r["MARKET"]: r for r in await service.get_settings(OWNER, ACCOUNT)}
        self.assertEqual((result["KR"]["USE_YN"], result["US"]["USE_YN"]), ("Y", "Y"))   # 새 행은 켜진 상태
        self.assertEqual((result["KR"]["PARK_RESERVE_PCT"], result["US"]["PARK_RESERVE_PCT"]),
                         (Decimal("0.05"), Decimal("0.1")))

    async def test_foreign_account_cannot_change_settings(self):
        service = make_service()
        with self.assertRaises(NotFoundError):
            await service.update_setting(INTRUDER, request(USE_YN="Y"))
        self.assertEqual((service.repo.saved, service.db.commits), ([], 0))

    async def test_invalid_value_is_rejected_and_rolled_back(self):
        service = make_service()
        with self.assertRaises(ValidationError):
            await service.update_setting(OWNER, request(USE_YN="Y", PARK_RESERVE_PCT=Decimal("0.9")))
        self.assertEqual((service.repo.saved, service.db.commits, service.db.rollbacks), ([], 0, 1))

    async def test_invalid_update_does_not_leave_existing_row_half_changed(self):
        existing = PoolSetting.create(ACCOUNT, "KR")
        with self.assertRaises(ValidationError):
            await make_service([existing]).update_setting(OWNER, request(USE_YN="Y", PARK_RESERVE_PCT=Decimal("0.9")))
        self.assertEqual((existing.USE_YN, existing.PARK_RESERVE_PCT), ("N", Decimal("0.05")))

    async def test_explicit_null_on_required_field_is_a_validation_error_not_a_crash(self):
        service = make_service()
        with self.assertRaises(ValidationError):
            await service.update_setting(OWNER, request(PARK_RESERVE_PCT=None))
        self.assertEqual(service.db.rollbacks, 1)

    async def test_concurrent_create_is_reported_as_duplicate(self):
        service = make_service(commit_error=IntegrityError("insert", {}, Exception("dup")))
        with self.assertRaises(DuplicateError):
            await service.update_setting(OWNER, request(USE_YN="Y"))
        self.assertEqual(service.db.rollbacks, 1)


class RouterWiringTest(unittest.TestCase):
    def test_routes_are_registered(self):
        from app.domain import routers
        from app.domain.pool.router import router
        routes = {(m, r.path) for r in router.routes for m in r.methods}
        self.assertEqual(routes, {("GET", "/pool"), ("PUT", "/pool"), ("GET", "/pool/park"), ("POST", "/pool/park")})
        self.assertIn("pool_router", routers.__all__)

    def client(self, user_id=OWNER):
        """실제 HTTP 경로 — 인증·서비스 의존성만 대역으로 바꿔 라우팅·직렬화·예외 응답을 확인한다"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.common.dependencies import get_current_user
        from app.domain.pool.router import get_pool_service, router
        from app.exceptions.handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(router)
        service = make_service()
        app.dependency_overrides[get_pool_service] = lambda: service
        app.dependency_overrides[get_current_user] = lambda: user_id
        return TestClient(app)

    def test_get_returns_defaults_as_json_numbers(self):
        body = self.client().get("/pool", params={"account_no": ACCOUNT}).json()
        self.assertTrue(body["success"])
        kr = body["data"][0]
        self.assertEqual((kr["MARKET"], kr["USE_YN"], kr["MAX_POSITIONS"]), ("KR", "N", DEFAULT_MAX_POSITIONS))
        self.assertEqual((kr["RISK_PCT"], kr["MAX_POSITION_PCT"], kr["MAX_OPEN_RISK_PCT"]), (0.007, 0.095, 0.08))   # JSON 숫자(문자열 아님)

    def test_put_then_get_roundtrip(self):
        c = self.client()
        put = c.put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "US", "USE_YN": "Y", "PARK_RESERVE_PCT": "0.1"})
        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.json()["data"]["PARK_RESERVE_PCT"], 0.1)
        self.assertEqual((put.json()["data"]["RISK_PCT"], put.json()["data"]["MAX_POSITIONS"]), (0.007, 10))   # 고정값
        us = c.get("/pool", params={"account_no": ACCOUNT}).json()["data"][1]
        self.assertEqual((us["MARKET"], us["USE_YN"], us["MAX_POSITIONS"]), ("US", "Y", 10))

    def test_http_count_update_returns_derived_json_numbers_and_rejects_out_of_range(self):
        c = self.client()
        ok = c.put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "KR", "MAX_POSITIONS": 20})
        self.assertEqual(ok.status_code, 200)
        data = ok.json()["data"]
        self.assertEqual((data["MAX_POSITIONS"], data["MAX_POSITION_PCT"], data["RISK_PCT"]), (20, 0.0475, 0.007))
        for bad in (6, 31):
            r = c.put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "KR", "MAX_POSITIONS": bad})
            self.assertEqual(r.status_code, 422, bad)
        kr = c.get("/pool", params={"account_no": ACCOUNT}).json()["data"][0]
        self.assertEqual(kr["MAX_POSITIONS"], 20, "범위 밖 요청은 기존 값을 바꾸지 않는다")

    def test_sending_a_fixed_value_gets_422_and_changes_nothing(self):
        c = self.client()
        for field, value in (("RISK_PCT", "0.001"), ("MAX_POSITION_PCT", "0.5"), ("MAX_OPEN_RISK_PCT", "0.2")):
            r = c.put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "KR", "USE_YN": "Y", field: value})
            self.assertEqual(r.status_code, 422, field)
        kr = c.get("/pool", params={"account_no": ACCOUNT}).json()["data"][0]
        self.assertEqual(kr["USE_YN"], "N", "거절된 요청은 다른 항목도 바꾸지 않는다")

    def test_foreign_account_gets_404_not_data(self):
        r = self.client(INTRUDER).get("/pool", params={"account_no": ACCOUNT})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["success"])

    def test_invalid_value_gets_422_with_message(self):
        r = self.client().put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "KR", "PARK_RESERVE_PCT": "0.9"})
        self.assertEqual(r.status_code, 422)
        self.assertFalse(r.json()["success"])

    def test_unknown_market_gets_422(self):
        r = self.client().put("/pool", json={"ACCOUNT_NO": ACCOUNT, "MARKET": "JP"})
        self.assertEqual(r.status_code, 422)


class MaxPositionsSettingTest(unittest.IsolatedAsyncioTestCase):
    """동시 보유 종목 수만 사용자가 정하고, 종목당 최대 비중·거래당 리스크는 거기서 자동으로 바뀐다"""

    async def test_changing_count_changes_the_derived_limits(self):
        service = make_service()
        r10 = await service.update_setting(OWNER, request(MAX_POSITIONS=10))
        self.assertEqual((r10["MAX_POSITIONS"], r10["MAX_POSITION_PCT"], r10["RISK_PCT"]),
                         (10, Decimal("0.0950"), Decimal("0.0070")))
        r20 = await service.update_setting(OWNER, request(MAX_POSITIONS=20))
        self.assertEqual((r20["MAX_POSITIONS"], r20["MAX_POSITION_PCT"], r20["RISK_PCT"]),
                         (20, Decimal("0.0475"), Decimal("0.0070")))
        r7 = await service.update_setting(OWNER, request(MAX_POSITIONS=7))
        self.assertEqual((r7["MAX_POSITIONS"], r7["MAX_POSITION_PCT"], r7["RISK_PCT"]),
                         (7, Decimal("0.1357"), Decimal("0.00950")))      # 종목이 적으면 한 종목이 커져 리스크도 따라 올라간다
        self.assertEqual(r7["MAX_OPEN_RISK_PCT"], Decimal("0.08"))

    async def test_derived_limits_are_recomputed_when_loaded_from_the_db(self):
        """저장되는 것은 종목 수뿐 — 읽을 때마다 파생값을 다시 계산하므로 어긋날 수 없다"""
        row = PoolSetting.create(ACCOUNT, "KR", max_positions=20)
        row.MAX_POSITION_PCT = Decimal("0.9")      # 어떤 경로로든 틀어졌다고 가정
        row._on_load()
        self.assertEqual((row.MAX_POSITION_PCT, row.RISK_PCT), (Decimal("0.0475"), Decimal("0.0070")))

    async def test_out_of_range_or_non_integer_is_rejected_and_nothing_changes(self):
        for bad in (6, 31, 0, -5):
            service = make_service([PoolSetting.create(ACCOUNT, "KR", max_positions=10)])
            with self.assertRaises(ValidationError, msg=bad):
                await service.update_setting(OWNER, request(MAX_POSITIONS=bad))
            self.assertEqual((service.db.commits, service.db.rollbacks), (0, 1))
        existing = PoolSetting.create(ACCOUNT, "KR", max_positions=10)
        with self.assertRaises(ValidationError):
            await make_service([existing]).update_setting(OWNER, request(MAX_POSITIONS=3, USE_YN="N"))
        self.assertEqual((existing.MAX_POSITIONS, existing.MAX_POSITION_PCT), (10, Decimal("0.0950")))

    async def test_fifteen_rounds_the_weight_down_so_the_total_never_exceeds_95_percent(self):
        out = await make_service().update_setting(OWNER, request(MAX_POSITIONS=15))
        self.assertEqual(out["MAX_POSITION_PCT"], Decimal("0.0633"))      # 0.95 ÷ 15 = 0.06333… 내림


class EnsureActiveTest(unittest.IsolatedAsyncioTestCase):
    """스윙 등록·활성화가 부르는 '풀을 켜진 상태로 보장' — 앱에 풀 스위치가 없으므로 이것이 풀을 켜는 유일한 경로"""

    def repo(self, existing=None):
        from app.domain.pool.repository import PoolRepository

        class R(PoolRepository):
            def __init__(inner):
                inner.saved = []

            async def find(inner, account_no, market):
                return existing

            async def save(inner, setting):
                inner.saved.append(setting)
                return setting

            async def _insert_new(inner, setting):
                inner.saved.append(setting)
                return setting
        return R()

    async def test_creates_an_active_row_when_missing(self):
        r = self.repo(None)
        setting = await r.ensure_active(ACCOUNT, "KR")
        self.assertEqual((setting.USE_YN, setting.MARKET, setting.ACCOUNT_NO), ("Y", "KR", ACCOUNT))
        self.assertEqual(r.saved, [setting])

    async def test_turns_an_inactive_row_on_and_keeps_its_other_settings(self):
        existing = PoolSetting.create(ACCOUNT, "US", use_yn="N")
        existing.update_settings(PARK_RESERVE_PCT=0.1)
        r = self.repo(existing)
        setting = await r.ensure_active(ACCOUNT, "US")
        self.assertIs(setting, existing)
        self.assertEqual((setting.USE_YN, setting.PARK_RESERVE_PCT), ("Y", Decimal("0.1")))

    async def test_concurrent_first_creation_uses_the_row_the_other_request_made(self):
        """같은 계좌·시장의 첫 등록이 동시에 들어와 기본키가 충돌해도 에러로 번지지 않는다 — 스윙 등록이 '중복'으로 거절되면 안 된다"""
        from sqlalchemy.exc import IntegrityError
        from app.domain.pool.repository import PoolRepository

        class Nested:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class DupDb:
            def begin_nested(self):
                return Nested()

            def add(self, obj):
                pass

            async def flush(self):
                raise IntegrityError("insert", {}, Exception("duplicate key"))

        repo = PoolRepository(DupDb())
        repo.find = AsyncMock(return_value=None)       # 같은 트랜잭션 스냅샷에서는 방금 커밋된 행이 안 보일 수 있다
        setting = await repo.ensure_active(ACCOUNT, "KR")   # 예외 없이 끝나야 한다
        self.assertEqual((setting.USE_YN, setting.MARKET), ("Y", "KR"))
        self.assertEqual(repo.find.await_count, 2, "충돌 뒤 한 번 더 찾아본다")

    async def test_concurrent_creation_finds_and_turns_on_the_other_requests_inactive_row(self):
        from sqlalchemy.exc import IntegrityError
        from app.domain.pool.repository import PoolRepository
        other = PoolSetting.create(ACCOUNT, "KR", use_yn="N")
        repo = PoolRepository(None)
        repo._insert_new = AsyncMock(return_value=None)
        repo.find = AsyncMock(side_effect=[None, other])
        repo.save = AsyncMock(side_effect=lambda s: s)
        setting = await repo.ensure_active(ACCOUNT, "KR")
        self.assertIs(setting, other)
        self.assertEqual(setting.USE_YN, "Y")

    async def test_active_row_stays_active(self):
        existing = PoolSetting.create(ACCOUNT, "KR", use_yn="Y")
        setting = await self.repo(existing).ensure_active(ACCOUNT, "KR")
        self.assertEqual(setting.USE_YN, "Y")

    async def test_saving_only_parking_settings_creates_an_active_row(self):
        """파킹 설정만 저장해도 이 계좌의 스윙이 매수에서 빠지지 않는다 (새 행이 꺼진 채 만들어지면 안 된다)"""
        service = make_service()
        result = await service.update_setting(OWNER, request(PARK_RESERVE_PCT=Decimal("0.1")))
        self.assertEqual((result["USE_YN"], result["PARK_RESERVE_PCT"]), ("Y", Decimal("0.1")))


if __name__ == "__main__":
    unittest.main()
