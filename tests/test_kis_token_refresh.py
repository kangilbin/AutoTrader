"""
KIS 토큰 거절 복구 회귀 테스트

실행:
  uv run pytest tests/test_kis_token_refresh.py -v

토큰 캐시는 TTL로만 만료되므로, KIS가 먼저 토큰을 끊으면 TTL이 끝날 때까지
모든 호출이 '기간이 만료된 token 입니다'(EGW00123)로 실패했다 (2026-09-29 운영 로그).
kis_fetch의 재발급-재시도와 TTL 계산(_token_ttl)을 고정한다.

Lua(_DISCARD_IF_SAME_TOKEN)는 대역으로 흉내 낸다. 실제 Redis 동작은 로컬 Redis에서
따로 확인했다 — 여기서는 '누구의 토큰을 지우는가' 판단만 본다.
"""
import json
import unittest
from datetime import datetime, timedelta

from app.exceptions import ExternalServiceError
from app.external import kis_api

USER, AUTH = "u1", "7"
CACHE_KEY = f"{USER}_{AUTH}_access_token"


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def hget(self, key, field):
        return self.store.get(key, {}).get(field)

    async def eval(self, script, numkeys, key, token):
        # _DISCARD_IF_SAME_TOKEN 의미: 캐시 토큰이 거절된 토큰일 때만 삭제
        if self.store.get(key, {}).get("access_token") == token:
            del self.store[key]
            return 1
        return 0


def _rejected(msg_cd, msg1):
    """http_client.fetch가 KIS 4xx/5xx를 올리는 모양 그대로"""
    return ExternalServiceError(
        "KIS", msg1, detail={"response_text": json.dumps({"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1})}
    )


async def _coro(value):
    return value


class KisFetchRetryTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = {n: getattr(kis_api, n) for n in ("fetch", "get_redis", "oauth_token")}
        self.redis = FakeRedis()
        self.calls = []         # fetch에 실린 authorization 헤더 기록
        self.issued = 0

        async def fake_oauth(user_id, simulation_yn, api_key, secret_key, auth_id=None):
            # 실제 oauth_token처럼: 캐시가 있으면 그것, 없으면 새로 발급해 캐시
            cached = self.redis.store.get(kis_api._token_cache_key(user_id, auth_id))
            if cached:
                return dict(cached)
            self.issued += 1
            data = {"access_token": "NEW", "api_key": api_key, "secret_key": secret_key,
                    "simulation_yn": simulation_yn}
            self.redis.store[kis_api._token_cache_key(user_id, auth_id)] = dict(data)
            return data

        kis_api.get_redis = lambda: _coro(self.redis)
        kis_api.oauth_token = fake_oauth

    async def asyncTearDown(self):
        for name, orig in self._orig.items():
            setattr(kis_api, name, orig)

    def _access(self, token="OLD"):
        """token_for_auth_id가 돌려주는 모양"""
        return {"access_token": token, "api_key": "k", "secret_key": "s", "simulation_yn": "Y",
                "user_id": USER, "auth_id": AUTH}

    def _stub_fetch(self, *outcomes):
        """호출마다 outcomes를 차례로 내놓는다 (예외면 raise)"""
        queue = list(outcomes)

        async def _fetch(method, url, service, **kwargs):
            self.calls.append(kwargs["headers"]["authorization"])
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        kis_api.fetch = _fetch

    # ------------------------------------------------------------------

    async def test_expired_token_is_reissued_and_retried_once(self):
        """운영 장애 재현 — 만료 거절 후 새 토큰으로 한 번 더 보내 성공한다"""
        self.redis.store[CACHE_KEY] = {"access_token": "OLD"}
        self._stub_fetch(_rejected("EGW00123", "기간이 만료된 token 입니다."), {"body": {"rt_cd": "0"}})
        access = self._access()

        result = await kis_api.kis_fetch("GET", "u", access, headers={"authorization": "Bearer OLD", "tr_id": "X"})

        self.assertEqual(result["body"]["rt_cd"], "0")
        self.assertEqual(self.calls, ["Bearer OLD", "Bearer NEW"])
        self.assertEqual(self.issued, 1)
        self.assertEqual(access["access_token"], "NEW", "후속 호출이 거절된 토큰을 또 쓰면 안 된다")

    async def test_non_token_error_is_not_retried(self):
        """업무/인프라 오류까지 재시도하면 주문이 중복되거나 발급 한도를 소모한다"""
        self._stub_fetch(_rejected("EGW00133", "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"))

        with self.assertRaises(ExternalServiceError):
            await kis_api.kis_fetch("POST", "u", self._access(), headers={"authorization": "Bearer OLD"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.issued, 0)

    async def test_retry_rejected_again_is_raised_without_loop(self):
        """재발급 토큰도 거절되면 한 번에 끝낸다 (무한 재발급 → EGW00133 방지)"""
        rejected = _rejected("EGW00121", "유효하지 않은 token 입니다.")
        self._stub_fetch(rejected, rejected)

        with self.assertRaises(ExternalServiceError):
            await kis_api.kis_fetch("GET", "u", self._access(), headers={"authorization": "Bearer OLD"})
        self.assertEqual(len(self.calls), 2)

    async def test_concurrent_rejection_keeps_fresh_token(self):
        """다른 요청이 이미 새 토큰을 넣었으면 지우지 않고 그 토큰을 재사용한다

        무조건 지우면 동시에 거절된 배치 요청 수만큼 발급이 일어나
        appkey당 1분 1회 제한(EGW00133)에 걸린다.
        """
        self.redis.store[CACHE_KEY] = {"access_token": "OTHER_FRESH", "api_key": "k",
                                       "secret_key": "s", "simulation_yn": "Y"}
        self._stub_fetch(_rejected("EGW00123", "기간이 만료된 token 입니다."), {"body": {}})

        await kis_api.kis_fetch("GET", "u", self._access("OLD"), headers={"authorization": "Bearer OLD"})

        self.assertEqual(self.issued, 0)
        self.assertEqual(self.calls[-1], "Bearer OTHER_FRESH")


class TokenTtlTest(unittest.TestCase):

    def _expiring_in(self, seconds):
        exp = datetime.now(kis_api._KST) + timedelta(seconds=seconds)
        return exp.strftime("%Y-%m-%d %H:%M:%S")

    def test_uses_expiry_time_not_expires_in(self):
        """6시간 내 재요청 시 기존 토큰(2시간 남음)이 와도 expires_in(24h)을 믿지 않는다"""
        ttl = kis_api._token_ttl({"expires_in": 86400, "access_token_token_expired": self._expiring_in(7200)})
        self.assertAlmostEqual(ttl, 7200 - kis_api._TOKEN_TTL_MARGIN, delta=5)

    def test_falls_back_to_expires_in_when_expiry_missing(self):
        self.assertEqual(kis_api._token_ttl({"expires_in": 86400}), 86400 - kis_api._TOKEN_TTL_MARGIN)

    def test_short_remaining_is_cached_only_that_long(self):
        ttl = kis_api._token_ttl({"access_token_token_expired": self._expiring_in(120)})
        self.assertTrue(0 < ttl <= 120)

    def test_already_expired_is_not_cached(self):
        self.assertLessEqual(kis_api._token_ttl({"access_token_token_expired": self._expiring_in(-60)}), 0)
        self.assertLessEqual(kis_api._token_ttl({}), 0)


if __name__ == "__main__":
    unittest.main()
