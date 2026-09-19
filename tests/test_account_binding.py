"""
계좌↔인증키 바인딩 회귀 테스트

실행:
  uv run pytest tests/test_account_binding.py -v

ACCOUNT에는 (USER_ID, ACCOUNT_NO) 유니크 제약이 없다. DB 스키마를 바꾸지 않기로 했으므로
"계좌 하나당 바인딩 하나"를 코드로 지켜야 한다. 지키지 못하면:

  - 배치 조회가 스윙 1건을 여러 행으로 반환 → gather가 같은 스윙을 동시에 두 번 실행
    → 같은 종목에 주문이 두 번 나간다 (실금액 손실)
  - 화면이 보여주는 인증키와 실제 주문에 쓰이는 인증키가 달라진다

raw SQL이라 파이썬 단위 테스트로는 검증되지 않으므로, 쿼리 문자열을 실제로 꺼내
SQLite에 실행해 행 수를 확인한다.
"""
import asyncio
import sqlite3
import unittest
from types import SimpleNamespace

from app.domain.account.repository import AccountRepository
from app.domain.swing.repository import SwingRepository


class CapturingDB:
    """실행된 SQL을 잡아두는 대역 (쿼리 문자열 자체가 검증 대상)"""

    def __init__(self):
        self.sql = None
        self.params = None

    async def execute(self, query, params=None):
        self.sql = str(query)
        self.params = params
        return _EmptyResult()


class _EmptyResult:
    def all(self):
        return []

    def __iter__(self):
        return iter(())


def _seeded_db():
    """앱키 교체로 같은 계좌가 두 인증키에 등록된 상태 (둘 다 살아있음)"""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE SWING_TRADE (SWING_ID INT, ACCOUNT_NO TEXT, USE_YN TEXT,
                                  MRKT_CODE TEXT, ST_CODE TEXT);
        CREATE TABLE ACCOUNT (ACCOUNT_ID INT, ACCOUNT_NO TEXT, USER_ID TEXT, AUTH_ID INT);
        CREATE TABLE AUTH_KEY (AUTH_ID INT, USER_ID TEXT, API_KEY TEXT, SECRET_KEY TEXT,
                               SIMULATION_YN TEXT);
        INSERT INTO SWING_TRADE VALUES (1, 'acc-a', 'Y', 'J', '005930');
        INSERT INTO ACCOUNT VALUES (10, 'acc-a', 'u1', 100), (11, 'acc-a', 'u1', 101);
        INSERT INTO AUTH_KEY VALUES (100, 'u1', 'old-key', 's', 'N'),
                                    (101, 'u1', 'new-key', 's', 'N');
    """)
    return db


def _capture(repo_factory, method, *args):
    db = CapturingDB()
    asyncio.run(getattr(repo_factory(db), method)(*args))
    return db


class BatchQueryBindingTest(unittest.TestCase):
    """배치 스윙 조회가 계좌 중복에도 스윙 1건을 1행으로 반환하는지"""

    def _run(self, method):
        cap = _capture(SwingRepository, method)
        with _seeded_db() as sqlite_db:
            return sqlite_db.execute(cap.sql).fetchall()

    def test_domestic_query_returns_one_row_per_swing(self):
        rows = self._run("find_active_domestic_swings")

        self.assertEqual(
            len(rows), 1,
            "계좌 중복으로 스윙이 여러 행이 됐다 — 배치가 같은 스윙에 주문을 두 번 넣는다",
        )

    def test_binding_picks_latest_auth_key(self):
        """최신 등록분을 쓴다 (find_auth_id_by_account_no와 같은 규칙)"""
        rows = self._run("find_active_domestic_swings")

        self.assertIn("new-key", rows[0], "폐기된 옛 인증키가 선택됐다")

    def test_all_active_query_also_pinned(self):
        """LEFT JOIN을 쓰는 전체 조회에도 같은 고정이 걸려 있다"""
        self.assertEqual(len(self._run("find_active_swings")), 1)


class AccountListBindingTest(unittest.TestCase):
    """계좌 목록이 중복 행을 접고, 배치와 같은 인증키를 보여주는지"""

    def test_duplicate_account_listed_once_with_latest_key(self):
        cap = _capture(AccountRepository, "find_all_by_user", "u1")

        with _seeded_db() as sqlite_db:
            rows = sqlite_db.execute(cap.sql, cap.params).fetchall()

        self.assertEqual(len(rows), 1, "같은 계좌가 목록에 두 번 표시된다")
        self.assertEqual(
            rows[0][2], 101,
            "화면이 보여주는 인증키가 배치가 실제로 쓰는 인증키와 다르다",
        )


class AccountRegistrationTest(unittest.IsolatedAsyncioTestCase):
    """계좌 재등록이 새 행을 만들지 않고 인증키만 갈아끼우는지

    앱키를 교체하면 사용자는 계좌를 '다시 등록'한다. 그때마다 INSERT하면 폐기된
    인증키를 가리키는 행이 쌓이고, 그 중복이 위 배치 조회 문제의 원인이 된다.
    """

    USER = "u1"

    def _service(self, existing):
        from datetime import datetime
        from app.domain.account import service as acc_svc

        test = self
        self.saved = []
        self.updated = []

        class FakeDB:
            async def commit(self_inner):
                pass

            async def rollback(self_inner):
                pass

        class FakeRepo:
            def __init__(self_inner, db):
                pass

            async def find_latest_by_account_no(self_inner, user_id, account_no):
                return existing

            async def update(self_inner, user_id, account_id, data):
                test.updated.append((user_id, account_id, data))
                return SimpleNamespace(
                    ACCOUNT_ID=account_id, USER_ID=user_id, ACCOUNT_NO="acc-a",
                    AUTH_ID=data["AUTH_ID"], REG_DT=datetime.now(), MOD_DT=datetime.now(),
                )

            async def save(self_inner, account):
                test.saved.append(account)
                account.ACCOUNT_ID = 99
                return account

        service = acc_svc.AccountService(FakeDB())
        service.repo = FakeRepo(None)
        return service

    def _request(self, auth_id):
        from app.domain.account.schemas import AccountCreateRequest

        return AccountCreateRequest(ACCOUNT_NO="acc-a", AUTH_ID=auth_id)

    async def test_reregistration_rebinds_instead_of_inserting(self):
        existing = SimpleNamespace(ACCOUNT_ID=10, ACCOUNT_NO="acc-a", USER_ID=self.USER, AUTH_ID=100)
        service = self._service(existing)

        result = await service.create_account(self.USER, self._request(101))

        self.assertEqual(self.saved, [], "이미 등록된 계좌인데 새 행을 만들었다")
        self.assertEqual(self.updated[0][1], 10, "기존 행이 아닌 다른 행을 갱신했다")
        self.assertEqual(self.updated[0][2]["AUTH_ID"], 101)
        self.assertEqual(result["AUTH_ID"], 101)

    async def test_new_account_is_inserted(self):
        """처음 등록하는 계좌는 그대로 INSERT (과잉 차단 방지)"""
        service = self._service(None)

        await service.create_account(self.USER, self._request(101))

        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.updated, [])


if __name__ == "__main__":
    unittest.main()
