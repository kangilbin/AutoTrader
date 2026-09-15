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
from app.exceptions import ExternalServiceError
from app.external import kis_api


# ==================== 테스트 대역 ====================

class FakeRedis:
    """세션 해시({user_id})와 토큰 캐시({user}_{auth}_access_token)를 담는다"""

    def __init__(self):
        self.store = {}

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))


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


if __name__ == "__main__":
    unittest.main()