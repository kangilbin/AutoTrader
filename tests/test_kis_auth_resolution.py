"""
KIS 인증 해석 회귀 테스트

실행:
  uv run pytest tests/test_kis_auth_resolution.py -v

"어떤 인증키로 KIS를 호출할 것인가"를 정하는 분기만 덮는다. 이 판단이 틀리면
남의 계좌에 실제 주문이 나가므로 실패 비용이 이 코드베이스에서 가장 크다.

해석 순서 (app/external/kis_api.py)
  1. account_no 지정 → ACCOUNT 테이블의 계좌↔인증키 바인딩 (배치 경로)
  2. 미지정 → Redis 세션에서 사용자가 고른 인증키 (API 요청 경로)
  3. 시세 전용(_quote_auth)만 1·2 실패 시 계좌 무관 키로 폴백

여기 있는 4건은 모두 **실제로 저질러진 실수**를 고정한 것이다. 넓은 커버리지가
목적이 아니므로, 기본 경로 확인처럼 틀릴 여지가 적은 케이스는 일부러 넣지 않았다.
HTTP 호출 본문도 제외한다 — 목으로 막은 테스트는 '내 목이 내 코드와 일치한다'만
증명하므로, 실제 KIS 호출로 확인하는 편이 낫다.
"""
import logging
import unittest
from types import SimpleNamespace

from app.domain.account.repository import AccountRepository
from app.exceptions import ExternalServiceError, NotFoundError
from app.external import kis_api


# ==================== 테스트 대역 ====================

class FakeRedis:
    """세션 해시({user_id})와 토큰 캐시({user}_{auth}_access_token)를 담는다"""

    def __init__(self):
        self.store = {}

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def delete(self, *keys):
        return sum(bool(self.store.pop(k, None)) for k in keys)

    async def hdel(self, key, *fields):
        bucket = self.store.get(key, {})
        return sum(bool(bucket.pop(f, None)) for f in fields)


class FakeAuthRepo:
    """AUTH_KEY 조회 대역. 인증키는 {AUTH_ID: SIMULATION_YN} 으로 정의한다."""

    rows = {}

    def __init__(self, db):
        pass

    async def find_by_id(self, user_id, auth_id):
        sim = FakeAuthRepo.rows.get(auth_id)
        if sim is None:
            return None
        return {
            "AUTH_ID": auth_id, "USER_ID": user_id, "AUTH_NAME": f"key{auth_id}",
            "SIMULATION_YN": sim, "API_KEY": f"enc-api-{auth_id}",
            "SECRET_KEY": f"enc-sec-{auth_id}",
        }

    async def find_all_by_user(self, user_id):
        return [
            SimpleNamespace(AUTH_ID=aid, SIMULATION_YN=sim)
            for aid, sim in sorted(FakeAuthRepo.rows.items())
        ]


class FakeAccountRepo:
    """ACCOUNT 조회 대역. 계좌번호 → AUTH_ID 바인딩."""

    bindings = {}

    def __init__(self, db):
        pass

    async def find_auth_id_by_account_no(self, user_id, account_no):
        return FakeAccountRepo.bindings.get(account_no)


async def _coro(value):
    return value


class AuthResolutionTest(unittest.IsolatedAsyncioTestCase):
    USER = "u1"

    async def asyncSetUp(self):
        self._orig = {
            "get_redis": kis_api.get_redis,
            "AuthRepository": kis_api.AuthRepository,
            "AccountRepository": kis_api.AccountRepository,
            "oauth_token": kis_api.oauth_token,
            "decrypt": kis_api.decrypt,
        }

        self.redis = FakeRedis()
        self.issued = []        # oauth_token 호출 기록 (어떤 키로 발급을 시도했는가)
        self.db = object()      # 세션은 대역에 전달되기만 하면 된다

        FakeAuthRepo.rows = {1: "N", 2: "Y"}        # 1=실전키, 2=모의키
        FakeAccountRepo.bindings = {"real-01": 1, "sim-01": 2}

        async def fake_oauth(user_id, simulation_yn, api_key, secret_key, auth_id=None):
            self.issued.append({"auth_id": auth_id, "simulation_yn": simulation_yn})
            return {"access_token": f"T{auth_id}", "simulation_yn": simulation_yn}

        kis_api.get_redis = lambda: _coro(self.redis)
        kis_api.AuthRepository = FakeAuthRepo
        kis_api.AccountRepository = FakeAccountRepo
        kis_api.oauth_token = fake_oauth
        kis_api.decrypt = lambda v: v.replace("enc-", "")

    async def asyncTearDown(self):
        for name, orig in self._orig.items():
            setattr(kis_api, name, orig)

    def set_session(self, account_no, auth_id):
        """사용자가 앱에서 인증키를 고른 상태"""
        self.redis.store[self.USER] = {"ACCOUNT_NO": account_no, "AUTH_ID": str(auth_id)}

    # ------------------------------------------------------------------

    async def test_account_wins_over_session(self):
        """세션이 다른 계좌를 가리켜도 인자로 받은 계좌가 이긴다

        Redis 세션의 ACCOUNT_NO는 '사용자가 앱에서 마지막으로 고른 계좌'라
        SWING_TRADE.ACCOUNT_NO와 다를 수 있다. 세션을 따라가면 실전 계좌로 등록한
        스윙이 모의 계좌에 주문되고, 실전 포지션은 청산되지 않은 채 남는다.
        """
        self.set_session("sim-01", 2)       # 사용자는 앱에서 모의계좌를 골라둔 상태

        user_data, access = await kis_api._get_user_auth(self.USER, self.db, "real-01")

        self.assertEqual(user_data["ACCOUNT_NO"], "real-01")
        self.assertEqual(access["simulation_yn"], "N", "세션의 모의키가 사용됐다")

    async def test_no_selection_raises_instead_of_guessing(self):
        """주문 경로는 인증키가 확정되지 않으면 폴백 없이 실패한다

        "세션에 없으면 알아서 하나 가져오면 되지 않나"는 자연스러운 편의 개선이고
        시세 경로에는 실제로 그렇게 했다. 그러나 주문 경로에 같은 폴백을 넣으면
        계좌가 여러 개인 사용자에게 임의 계좌로 주문이 나간다.
        """
        with self.assertRaises(ExternalServiceError):
            await kis_api._get_user_auth(self.USER, self.db)

    async def test_quote_token_failure_does_not_switch_key(self):
        """시세 폴백은 '인증키 미선택'에만 적용된다 — 발급 실패는 그대로 올린다

        _get_user_auth는 '인증키 미선택'과 '토큰 발급 실패'에 같은 예외 타입을 쓴다.
        예외를 잡아 폴백하면 일시적 KIS 장애가 조용히 다른 appkey 발급으로 이어져,
        장애가 로그에 남지 않고 appkey당 1분 발급 제한까지 건드린다.
        """
        self.set_session("sim-01", 2)

        # 세션 키(2)만 실패시키고 나머지는 정상 발급된다.
        # 폴백을 타면 실전키(1)로 '성공'해버리므로 예외 발생 자체가 판별 신호가 된다.
        ok = kis_api.oauth_token

        async def fail_session_key(user_id, simulation_yn, api_key, secret_key, auth_id=None):
            if str(auth_id) == "2":
                raise ExternalServiceError("KIS", "토큰 발급 실패")
            return await ok(user_id, simulation_yn, api_key, secret_key, auth_id=auth_id)

        kis_api.oauth_token = fail_session_key

        with self.assertRaises(ExternalServiceError) as ctx:
            await kis_api._quote_auth(self.USER, self.db)

        self.assertIn("토큰 발급 실패", str(ctx.exception))
        self.assertEqual(
            [i["auth_id"] for i in self.issued], [],
            "세션 키 실패 후 다른 appkey로 발급을 시도했다 (장애가 삼켜짐)",
        )


class DuplicateAccountBindingTest(unittest.IsolatedAsyncioTestCase):
    """같은 계좌가 여러 인증키에 묶여 있어도 결정적으로 하나를 고른다

    ACCOUNT에 (USER_ID, ACCOUNT_NO) 유니크 제약이 없고 계좌 등록도 중복을 막지 않는다.
    앱키 교체 시 흔하다: 새 AUTH_KEY 등록 → 계좌 재등록 → 옛 행 잔존.
    임의 행을 고르면 폐기된 키로 주문이 나가거나 실전/모의가 어긋난다.

    ⚠️ ORDER BY 자체는 DB가 수행하므로 여기서 검증되지 않는다.
       이 테스트가 고정하는 건 '정렬된 결과의 첫 행을 쓰고 경고를 남긴다'는 선택 규칙이다.
    """

    def _repo_returning(self, auth_ids):
        class Result:
            def scalars(self_inner):
                return SimpleNamespace(all=lambda: list(auth_ids))

        class DB:
            async def execute(self_inner, query):
                return Result()

        return AccountRepository(DB())

    async def test_picks_first_row_and_warns(self):
        repo = self._repo_returning([7, 3])      # ACCOUNT_ID DESC → 최신 등록분이 앞

        with self.assertLogs("app.domain.account.repository", level=logging.WARNING) as logs:
            auth_id = await repo.find_auth_id_by_account_no("u1", "dup-01")

        self.assertEqual(auth_id, 7, "최신 등록분이 아닌 인증키가 선택됐다")
        self.assertIn("dup-01", logs.output[0])


class AccountVerifyTokenPathTest(unittest.IsolatedAsyncioTestCase):
    """계좌 검증이 캐시 없는 토큰 발급 경로를 타지 않는지 고정

    실제로 저질러진 실수: verify_account 가 캐싱 없는 발급 함수를 써서 매번 새
    토큰을 요청했다. KIS는 동일 appkey로 1분 1회만 발급을 허용하므로
    '인증키 등록 → 계좌 검증'이라는 정상 흐름이 403(EGW00133)으로 막혔다.
    """

    async def test_verify_account_uses_cached_token_path(self):
        from app.domain.account import service as acc_svc

        calls = []

        async def fake_token(user_id, auth_id, db):
            calls.append((user_id, auth_id))
            return {"access_token": "t", "simulation_yn": "N"}

        async def fake_verify(access_data, account_no):
            return None

        orig_token, orig_verify = acc_svc.token_for_auth_id, acc_svc.verify_account_balance
        acc_svc.token_for_auth_id, acc_svc.verify_account_balance = fake_token, fake_verify
        try:
            result = await acc_svc.AccountService(object()).verify_account("u1", 5, "12345678-01")
        finally:
            acc_svc.token_for_auth_id, acc_svc.verify_account_balance = orig_token, orig_verify

        self.assertEqual(calls, [("u1", 5)], "캐시 경로(token_for_auth_id)를 타지 않았다")
        self.assertTrue(result["valid"])

    def test_uncached_issuer_is_not_reintroduced(self):
        """캐시를 우회하는 발급 함수가 다시 생기면 같은 버그가 재발한다"""
        self.assertFalse(
            hasattr(kis_api, "issue_token"),
            "issue_token(캐싱 없는 발급)이 되살아났다 — appkey 1분 1회 제한에 다시 걸린다",
        )


class RevokedAuthCacheTest(unittest.IsolatedAsyncioTestCase):
    """삭제·교체된 인증키의 토큰 캐시가 계속 쓰이지 않는지 고정

    실제로 저질러진 실수: 인증키를 삭제해도 Redis 토큰 캐시는 그대로 남았고,
    token_for_auth_id가 캐시를 먼저 보고 DB를 건너뛰었다. 그 결과 폐기된
    인증키로 발급된 토큰이 expires_in(최대 24h) 동안 계속 유효하게 쓰여
    사용자가 지운 앱키로 실주문이 나갈 수 있었다.
    """

    USER = "u1"

    async def asyncSetUp(self):
        self._orig = {
            "get_redis": kis_api.get_redis,
            "AuthRepository": kis_api.AuthRepository,
            "oauth_token": kis_api.oauth_token,
            "decrypt": kis_api.decrypt,
        }
        self.redis = FakeRedis()
        FakeAuthRepo.rows = {1: "N"}        # 1번만 존재. 9번은 삭제된 인증키.

        async def fake_oauth(user_id, simulation_yn, api_key, secret_key, auth_id=None):
            return {"access_token": f"T{auth_id}", "simulation_yn": simulation_yn}

        kis_api.get_redis = lambda: _coro(self.redis)
        kis_api.AuthRepository = FakeAuthRepo
        kis_api.oauth_token = fake_oauth
        kis_api.decrypt = lambda v: v.replace("enc-", "")

    async def asyncTearDown(self):
        for name, orig in self._orig.items():
            setattr(kis_api, name, orig)

    def warm_cache(self, auth_id, token="stale-token"):
        key = kis_api._token_cache_key(self.USER, auth_id)
        self.redis.store[key] = {"access_token": token, "simulation_yn": "N"}
        return key

    async def test_warm_cache_does_not_resurrect_deleted_auth(self):
        """캐시가 남아 있어도 AUTH_KEY에 없으면 거부한다

        캐시를 먼저 반환하면 DB 삭제가 무의미해진다. 여기서 실패한다는 건
        token_for_auth_id가 다시 '캐시 우선' 순서로 돌아갔다는 뜻이다.
        """
        key = self.warm_cache(9)

        with self.assertRaises(NotFoundError):
            await kis_api.token_for_auth_id(self.USER, 9, object())

        self.assertNotIn(key, self.redis.store, "거부는 했지만 남은 캐시를 정리하지 않았다")

    async def test_live_auth_still_resolves(self):
        """살아 있는 인증키는 그대로 토큰을 받는다 (과잉 차단 방지)"""
        access = await kis_api.token_for_auth_id(self.USER, 1, object())
        self.assertEqual(access["access_token"], "T1")

    async def test_invalidate_clears_token_and_lock(self):
        """무효화는 토큰 슬롯과 발급 락을 함께 지운다

        락만 남으면 다음 요청이 최대 10초 폴링 후 '토큰 발급 대기 시간 초과'로 실패한다.
        """
        key = self.warm_cache(1)
        self.redis.store[f"{key}:lock"] = "1"

        await kis_api.invalidate_token_cache(self.USER, 1)

        self.assertNotIn(key, self.redis.store)
        self.assertNotIn(f"{key}:lock", self.redis.store)


class FakeAccountRepoRW:
    """인증키↔계좌 바인딩 대역. bound: {auth_id: [account_no]}"""

    def __init__(self):
        self.bound = {}
        self.by_id = {}                 # (user_id, account_id) -> account_no
        self.deleted_by_auth = []

    async def find_account_no_by_id(self, user_id, account_id):
        return self.by_id.get((user_id, account_id))

    async def delete(self, user_id, account_id):
        account_no = self.by_id.pop((user_id, account_id), None)
        if account_no is None:
            return False
        for nos in self.bound.values():
            if account_no in nos:
                nos.remove(account_no)
        return True

    async def find_account_nos_by_auth(self, user_id, auth_id):
        return list(self.bound.get(auth_id, []))

    async def delete_by_auth(self, user_id, auth_id):
        self.deleted_by_auth.append((user_id, auth_id))
        return len(self.bound.pop(auth_id, []))

    async def find_existing_account_nos(self, account_nos):
        """삭제 후에도 다른 인증키에 남아 있는 계좌번호"""
        alive = {no for nos in self.bound.values() for no in nos}
        return [no for no in account_nos if no in alive]


class FakeSwingRepoRW:
    """스윙 조회/삭제 대역. rows: [SimpleNamespace(...)]"""

    def __init__(self):
        self.rows = []
        self.deleted_nos = None
        self.deleted_ema_nos = None

    async def find_by_account_nos(self, account_nos):
        return [r for r in self.rows if r.ACCOUNT_NO in account_nos]

    async def delete_by_account_nos(self, account_nos):
        self.deleted_nos = list(account_nos)
        return len([r for r in self.rows if r.ACCOUNT_NO in account_nos])

    async def delete_ema_options_by_account_nos(self, account_nos):
        self.deleted_ema_nos = list(account_nos)
        return 0


class AuthDeletionCacheCleanupTest(unittest.IsolatedAsyncioTestCase):
    """인증키 삭제가 토큰 캐시와 세션 선택 상태까지 정리하는지 고정"""

    USER = "u1"

    async def asyncSetUp(self):
        from app.domain.auth import service as auth_svc

        self.svc_mod = auth_svc
        self.redis = FakeRedis()
        # 서비스는 세션 해시를, kis_api.invalidate_token_cache는 토큰 슬롯을
        # 각자 자기 모듈의 get_redis로 접근하므로 둘 다 대역으로 바꾼다.
        self._orig_redis = (auth_svc.get_redis, kis_api.get_redis)
        auth_svc.get_redis = kis_api.get_redis = lambda: _coro(self.redis)

        class FakeDB:
            async def commit(self_inner):
                return None

        class FakeRepo:
            deleted = []

            def __init__(self_inner, db):
                pass

            async def delete(self_inner, user_id, auth_id):
                FakeRepo.deleted.append((user_id, auth_id))
                return True

        FakeRepo.deleted = []
        self.service = auth_svc.AuthService(FakeDB())
        self.service.repo = FakeRepo(None)
        # 삭제 규칙은 AccountService에 한 벌만 있고 AuthService가 위임한다.
        # 위임 경로까지 함께 고정하려고 대역은 그 안쪽 Repository에 끼운다.
        self.accounts = FakeAccountRepoRW()
        self.swings = FakeSwingRepoRW()
        self.service.account_service.repo = self.accounts
        self.service.account_service.swing_repo = self.swings

    async def asyncTearDown(self):
        self.svc_mod.get_redis, kis_api.get_redis = self._orig_redis

    async def test_delete_clears_cache_and_selection(self):
        """삭제 대상이 현재 선택된 인증키면 선택 상태까지 해제한다

        세션의 AUTH_ID가 삭제된 키를 계속 가리키면 이후 모든 KIS 요청이
        '인증키 없음'으로 실패한다. 사용자에겐 재선택을 요구해야 한다.
        """
        key = kis_api._token_cache_key(self.USER, 3)
        self.redis.store[key] = {"access_token": "t"}
        self.redis.store[self.USER] = {"ACCOUNT_NO": "real-01", "AUTH_ID": "3"}

        await self.service.delete_auth(self.USER, 3)

        self.assertNotIn(key, self.redis.store, "삭제된 인증키의 토큰이 캐시에 남았다")
        self.assertEqual(
            self.redis.store[self.USER], {},
            "세션이 삭제된 인증키를 계속 가리킨다",
        )

    async def test_delete_cascades_accounts_and_swings(self):
        """인증키를 지우면 그 인증키로 등록한 계좌와 스윙까지 정리한다

        계좌만 남기면 배치가 인증키 없는 계좌의 스윙을 매 주기 집어 실패한다
        (find_active_domestic_swings의 JOIN AUTH_KEY가 깨진다).
        """
        self.accounts.bound = {3: ["acc-a", "acc-b"]}
        self.swings.rows = [
            SimpleNamespace(SWING_ID=1, ACCOUNT_NO="acc-a", SIGNAL=0, HOLD_QTY=0),
            SimpleNamespace(SWING_ID=2, ACCOUNT_NO="acc-b", SIGNAL=1, HOLD_QTY=5),
        ]

        # 정리 로그는 규칙이 사는 곳(AccountService)에서 나온다
        with self.assertLogs("app.domain.account.service", level="WARNING") as logs:
            await self.service.delete_auth(self.USER, 3)

        self.assertEqual(self.accounts.deleted_by_auth, [(self.USER, 3)])
        self.assertEqual(sorted(self.swings.deleted_nos), ["acc-a", "acc-b"])
        self.assertEqual(sorted(self.swings.deleted_ema_nos), ["acc-a", "acc-b"])
        self.assertTrue(
            any("SWING_ID=2" in line for line in logs.output),
            "보유 포지션 상태로 삭제된 스윙이 로그에 남지 않았다",
        )

    async def test_swing_survives_when_account_bound_to_another_key(self):
        """같은 계좌가 다른 인증키로도 등록돼 있으면 스윙을 지우지 않는다

        앱키 교체 시 같은 계좌가 여러 AUTH_ID로 남는 게 실제로 발생한다
        (ACCOUNT에 (USER_ID, ACCOUNT_NO) 유니크 제약 없음). 이때 배치는 남은
        바인딩으로 계속 동작하므로, 스윙을 지우면 멀쩡한 매매가 사라진다.
        """
        self.accounts.bound = {3: ["acc-a"], 9: ["acc-a"]}      # 9번 인증키에도 동일 계좌
        self.swings.rows = [SimpleNamespace(SWING_ID=1, ACCOUNT_NO="acc-a", SIGNAL=1, HOLD_QTY=3)]

        await self.service.delete_auth(self.USER, 3)

        self.assertIsNone(self.swings.deleted_nos, "다른 인증키가 살아있는 계좌의 스윙이 삭제됐다")

    async def test_delete_keeps_selection_of_other_key(self):
        """다른 인증키를 쓰는 중이면 세션 선택은 건드리지 않는다"""
        self.redis.store[self.USER] = {"ACCOUNT_NO": "real-01", "AUTH_ID": "1"}

        await self.service.delete_auth(self.USER, 3)

        self.assertEqual(self.redis.store[self.USER]["AUTH_ID"], "1",
                         "관계없는 인증키 선택이 해제됐다")


class AccountDeletionCascadeTest(unittest.IsolatedAsyncioTestCase):
    """계좌를 직접 지우는 경로도 스윙·EMA_OPT를 함께 정리하는지 고정

    인증키 삭제만 막아두면 반쪽이다. 계좌만 지우면 스윙은 배치 조회(JOIN ACCOUNT)
    에서 빠져 조용히 멈추고, 같은 계좌번호로 재등록하는 순간 옛 스윙과 옛 EMA_OPT가
    되살아난다 (계좌번호가 같으면 다시 조인되므로).
    """

    USER = "u1"

    async def asyncSetUp(self):
        from app.domain.account import service as acc_svc

        test = self
        self.committed = False

        class FakeDB:
            async def commit(self_inner):
                test.committed = True

            async def rollback(self_inner):
                pass

        self.service = acc_svc.AccountService(FakeDB())
        self.accounts = FakeAccountRepoRW()
        self.swings = FakeSwingRepoRW()
        self.service.repo = self.accounts
        self.service.swing_repo = self.swings

        # ACCOUNT_ID 5 = 계좌 acc-a (인증키 3에 묶임)
        self.accounts.bound = {3: ["acc-a"]}
        self.accounts.by_id = {("u1", "5"): "acc-a"}
        self.swings.rows = [SimpleNamespace(SWING_ID=1, ACCOUNT_NO="acc-a", SIGNAL=0, HOLD_QTY=0)]

    async def test_delete_account_cascades_swings_and_ema(self):
        await self.service.delete_account(self.USER, "5")

        self.assertEqual(self.swings.deleted_nos, ["acc-a"])
        self.assertEqual(self.swings.deleted_ema_nos, ["acc-a"], "EMA_OPT가 남았다")
        self.assertTrue(self.committed)

    async def test_delete_account_rejects_other_users_account(self):
        """남의 ACCOUNT_ID를 넘기면 조회 단계에서 막힌다 (소유권 검증)"""
        with self.assertRaises(NotFoundError):
            await self.service.delete_account("intruder", "5")

        self.assertIsNone(self.swings.deleted_nos)
        self.assertFalse(self.committed)


class AuthRegistrationTokenSlotTest(unittest.IsolatedAsyncioTestCase):
    """등록 검증 토큰이 실사용 캐시 슬롯에 들어가는지 고정

    AUTH_ID 없이 발급하면 토큰이 {user}_access_token 에 저장되는데, 이 슬롯은
    읽는 코드가 없다. 그 결과 등록 직후 계좌 검증이 같은 appkey로 1분 내 재발급을
    요청해 EGW00133(1분 1회 제한)에 걸린다. 발급 1회로 등록→검증이 끝나야 한다.
    """

    USER = "u1"

    async def asyncSetUp(self):
        from app.domain.auth import service as auth_svc

        self.svc_mod = auth_svc
        self.issued = []
        self.committed = False
        self.rolled_back = False
        self._orig = (auth_svc.oauth_token, auth_svc.encrypt)

        test = self

        async def fake_oauth(user_id, simulation_yn, api_key, secret_key, auth_id=None):
            test.issued.append({"auth_id": auth_id, "api_key": api_key})
            return {"access_token": "T"}

        auth_svc.oauth_token = fake_oauth
        auth_svc.encrypt = lambda v: f"enc-{v}"

        class FakeDB:
            async def commit(self_inner):
                test.committed = True

            async def rollback(self_inner):
                test.rolled_back = True

        class FakeRepo:
            def __init__(self_inner, db):
                pass

            async def save(self_inner, auth):
                auth.AUTH_ID = 7          # flush 시 시퀀스가 채워주는 값
                return auth

        self.service = auth_svc.AuthService(FakeDB())
        self.service.repo = FakeRepo(None)

    async def asyncTearDown(self):
        self.svc_mod.oauth_token, self.svc_mod.encrypt = self._orig

    def _request(self):
        from app.domain.auth.schemas import AuthCreateRequest

        return AuthCreateRequest(
            AUTH_NAME="실전키", SIMULATION_YN="N", API_KEY="ak", SECRET_KEY="sk"
        )

    async def test_validation_token_is_cached_under_new_auth_id(self):
        """검증 발급이 방금 채번된 AUTH_ID 슬롯으로 들어간다"""
        result = await self.service.create_auth(self.USER, self._request())

        self.assertEqual(result["AUTH_ID"], 7)
        self.assertEqual(
            [i["auth_id"] for i in self.issued], [7],
            "AUTH_ID 없이 발급해 아무도 읽지 않는 슬롯에 캐시됐다",
        )
        self.assertTrue(self.committed)

    async def test_invalid_key_rolls_back_insert(self):
        """토큰 발급이 실패하면 INSERT를 되돌린다 (못 쓰는 인증키가 남지 않게)"""
        async def fail(*args, **kwargs):
            raise ExternalServiceError("KIS", "토큰 발급 실패")

        self.svc_mod.oauth_token = fail

        with self.assertRaises(ExternalServiceError):
            await self.service.create_auth(self.USER, self._request())

        self.assertTrue(self.rolled_back, "발급 실패인데 롤백하지 않았다")
        self.assertFalse(self.committed)


if __name__ == "__main__":
    unittest.main()