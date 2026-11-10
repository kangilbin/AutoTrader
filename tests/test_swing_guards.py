"""스윙 등록 가드 — 계좌 소유권, 파킹 ETF 제외

실행: uv run pytest tests/test_swing_guards.py -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.pool.entity import PoolSetting, is_park_etf
from app.domain.swing import service as svc_mod
from app.domain.swing.schemas import SwingCreateRequest
from app.domain.swing.service import SwingService
from app.exceptions import BusinessRuleError, NotFoundError
from tests.test_swing_summary import ACCOUNT, Env, FakeDB, holding, row


class IsParkEtfTest(unittest.TestCase):
    def test_fixed_codes_per_market(self):
        self.assertTrue(is_park_etf("KR", "459580"))
        self.assertTrue(is_park_etf("US", "SGOV"))
        self.assertFalse(is_park_etf("KR", "005930"))
        self.assertFalse(is_park_etf("US", "459580"), "시장이 다르면 다른 종목")
        self.assertFalse(is_park_etf("KR", "SGOV"))


class FakeSwingRepo:
    def __init__(self):
        self.saved = []

    async def save(self, swing):
        self.saved.append(swing)
        return swing

    async def save_ema_option(self, ema):
        pass


class CreateSwingGuardTest(unittest.IsolatedAsyncioTestCase):
    def service(self, owned=True):
        svc = SwingService(FakeDB())
        svc.repo = FakeSwingRepo()

        class FakeAccountRepo:
            def __init__(self, db):
                pass

            async def find_auth_id_by_account_no(self, user_id, account_no):
                return 7 if owned else None

        self.pool_ensured = []
        ensured = self.pool_ensured

        class FakePoolRepo:
            def __init__(self, db):
                pass

            async def ensure_active(self, account_no, market):
                ensured.append((account_no, market))

        for target, fake in (("AccountRepository", FakeAccountRepo), ("PoolRepository", FakePoolRepo)):
            p = patch.object(svc_mod, target, fake)
            p.start()
            self.addCleanup(p.stop)
        return svc

    @staticmethod
    def request(code="005930", mrkt="J", account=ACCOUNT):
        return SwingCreateRequest(ACCOUNT_NO=account, MRKT_CODE=mrkt, ST_CODE=code, INIT_AMOUNT=0, SWING_TYPE="S")

    async def test_foreign_account_is_not_found_and_nothing_saved(self):
        svc = self.service(owned=False)
        with self.assertRaises(NotFoundError):
            await svc.create_swing("attacker", self.request())
        self.assertEqual(svc.repo.saved, [])

    async def test_own_account_registers(self):
        svc = self.service()
        out = await svc.create_swing("u", self.request())
        self.assertEqual(out["ST_CODE"], "005930")
        self.assertEqual(len(svc.repo.saved), 1)

    async def test_registering_turns_the_pool_on_for_that_market(self):
        """풀 스위치를 앱에서 없앴다 — 스윙을 등록하면 그 계좌·시장의 풀이 켜져 있어야 매수가 일어난다"""
        svc = self.service()
        await svc.create_swing("u", self.request(code="005930", mrkt="J"))
        await svc.create_swing("u", self.request(code="AAPL", mrkt="NAS"))
        self.assertEqual(self.pool_ensured, [(ACCOUNT, "KR"), (ACCOUNT, "US")])

    async def test_rejected_registration_does_not_touch_the_pool(self):
        svc = self.service(owned=False)
        with self.assertRaises(NotFoundError):
            await svc.create_swing("attacker", self.request())
        self.assertEqual(self.pool_ensured, [])

    async def test_park_etf_cannot_be_registered_as_swing(self):
        for code, mrkt in (("459580", "J"), ("459580", "NX"), ("459580", "UN"), ("SGOV", "NYS"), ("SGOV", "NAS")):
            svc = self.service()
            with self.assertRaises(BusinessRuleError, msg=f"{code}/{mrkt}"):
                await svc.create_swing("u", self.request(code=code, mrkt=mrkt))
            self.assertEqual(svc.repo.saved, [])

    async def test_same_ticker_in_other_market_is_allowed(self):
        """SGOV 는 미국 풀의 파킹 종목이라 국내 코드로는 막지 않는다 (코드 체계가 달라 실제로는 없는 종목)"""
        svc = self.service()
        await svc.create_swing("u", self.request(code="SGOV", mrkt="J"))
        self.assertEqual(len(svc.repo.saved), 1)

    async def test_ownership_is_checked_before_anything_else(self):
        svc = self.service(owned=False)
        with self.assertRaises(NotFoundError):
            await svc.create_swing("attacker", self.request(code="459580"))


class AutoRegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_parked_etf_holding_is_not_auto_registered(self):
        """파킹 ETF 를 보유해도 목록 조회가 스윙으로 자동 등록하지 않는다 (가짜 저장소에는 save 가 없어 등록 시도 시 실패)"""
        env = Env(swings=[row(1, "005930")],
                  holdings=[holding("459580", 80, 86_000_000, 1_000, 0.1, 85_999_000)],
                  pool_setting=PoolSetting.create(ACCOUNT, "KR", use_yn="Y"))
        result = await env.run()
        self.assertEqual([r["ST_CODE"] for r in result["list"]], ["005930"])


if __name__ == "__main__":
    unittest.main()
