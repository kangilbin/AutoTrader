"""
Gemini 재시도·모델 대체 정책 테스트

실행:
  uv run pytest tests/test_gemini_retry.py -v

운영(2026-10-21)에서 무료 등급이 이렇게 실패했다:
- 3.8-flash 가 상한을 넘겨 504 → 같은 모델로 3분 더 기다림 → 또 504
- 하루 20회 한도 초과 429 를 10초 뒤 재시도 → 한도만 낭비
- AI Studio 에서 3.8 이 23/20 — 실패한 요청도 일일 한도에 센다
그래서 모델마다 한 번만 시도하고, 실패면 바로 다음 모델로 넘어가는 것을 고정한다.
"""
import asyncio
import unittest
from unittest.mock import patch

import httpx
from google.genai import errors
from pydantic import BaseModel

from app.exceptions import ExternalServiceError, QuotaExhaustedError
from app.external import gemini_api


class _Answer(BaseModel):
    ok: bool


class _Resp:
    usage_metadata = None
    candidates = []

    def __init__(self):
        self.parsed = _Answer(ok=True)


def _api_error(code: int, message: str = "err", details: list = None) -> errors.APIError:
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": message, "status": "X", "details": details or []}})


def _daily_quota_error(delay: str = "49261s") -> errors.APIError:
    return _api_error(429, "You exceeded your current quota", [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ])


class _FakeModels:
    """모델별로 미리 정한 결과를 차례로 돌려주는 가짜 generate_content"""

    def __init__(self, script: dict):
        self.script = {m: list(outcomes) for m, outcomes in script.items()}
        self.calls = []

    async def generate_content(self, model, contents, config):
        self.calls.append((model, config.thinking_config is not None))
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, models):
        self.aio = type("Aio", (), {"models": models})()


class GeminiRetryTest(unittest.TestCase):
    CHAIN = ["m1", "m2", "m3"]

    def setUp(self):
        gemini_api._exhausted_until.clear()

    def _run(self, script: dict):
        models = _FakeModels(script)
        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)

        with patch.object(gemini_api, "_get_client", return_value=_FakeClient(models)), \
                patch.object(gemini_api, "_model_chain", return_value=self.CHAIN), \
                patch.object(gemini_api.asyncio, "sleep", fake_sleep):
            result = asyncio.run(gemini_api.generate_structured("p", _Answer))
        return result, models.calls, sleeps

    def test_daily_quota_skips_model_without_waiting(self):
        """일일 한도 429 는 재시도하지 않고 다음 모델로 — 대기 없음"""
        (_, model), calls, sleeps = self._run({"m1": [_daily_quota_error()], "m2": [_Resp()]})
        self.assertEqual(model, "m2")
        self.assertEqual([c[0] for c in calls], ["m1", "m2"])
        self.assertEqual(sleeps, [])

    def test_exhausted_model_is_not_called_again(self):
        """한도를 다 쓴 모델은 초기화 전까지 호출 자체를 하지 않는다"""
        self._run({"m1": [_daily_quota_error()], "m2": [_Resp()]})
        (_, model), calls, _ = self._run({"m2": [_Resp()]})
        self.assertEqual([c[0] for c in calls], ["m2"])

    def test_timeout_504_moves_to_next_model(self):
        """504(호출 상한 초과)는 같은 모델로 다시 기다리지 않는다"""
        (_, model), calls, sleeps = self._run({"m1": [_api_error(504)], "m2": [_Resp()]})
        self.assertEqual([c[0] for c in calls], ["m1", "m2"])
        self.assertEqual(sleeps, [])

    def test_client_timeout_moves_to_next_model(self):
        (_, model), calls, _ = self._run({"m1": [httpx.ReadTimeout("t")], "m2": [_Resp()]})
        self.assertEqual(model, "m2")

    def test_overload_moves_to_next_model_without_retry(self):
        """503(혼잡)도 같은 모델로 다시 보내지 않는다 — 실패 요청도 한도에 들어간다"""
        (_, model), calls, sleeps = self._run({"m1": [_api_error(503)], "m2": [_Resp()]})
        self.assertEqual(model, "m2")
        self.assertEqual([c[0] for c in calls], ["m1", "m2"])
        self.assertEqual(sleeps, [])

    def test_per_minute_429_moves_to_next_model(self):
        """분당 한도 429 는 다음 모델로 (모델마다 분당 한도가 따로) — 소진 표시는 하지 않는다"""
        (_, model), calls, _ = self._run({"m1": [_api_error(429, "rate")], "m2": [_Resp()]})
        self.assertEqual([c[0] for c in calls], ["m1", "m2"])
        self.assertNotIn("m1", gemini_api._exhausted_until)

    def test_each_model_called_at_most_once(self):
        """전부 실패해도 모델별 호출은 1회 — 분석 1건이 한 모델 한도를 여러 번 태우지 않는다"""
        script = {m: [_api_error(503)] for m in self.CHAIN}
        models = _FakeModels(script)
        with patch.object(gemini_api, "_get_client", return_value=_FakeClient(models)), \
                patch.object(gemini_api, "_model_chain", return_value=self.CHAIN):
            with self.assertRaises(ExternalServiceError):
                asyncio.run(gemini_api.generate_structured("p", _Answer))
        self.assertEqual([c[0] for c in models.calls], self.CHAIN)

    def test_thinking_unsupported_retries_without_thinking(self):
        """추론 수준 지정을 거부하는 모델은 설정 없이 다시 — 시도 횟수에 넣지 않는다"""
        err = _api_error(400, "Thinking level LOW is not supported for this model.")
        (_, model), calls, _ = self._run({"m1": [err, _Resp()]})
        self.assertEqual(calls, [("m1", True), ("m1", False)])

    def test_bad_request_raises_immediately(self):
        models = _FakeModels({"m1": [_api_error(400, "invalid schema")]})
        with patch.object(gemini_api, "_get_client", return_value=_FakeClient(models)), \
                patch.object(gemini_api, "_model_chain", return_value=self.CHAIN):
            with self.assertRaises(ExternalServiceError):
                asyncio.run(gemini_api.generate_structured("p", _Answer))
        self.assertEqual(len(models.calls), 1)

    def test_all_daily_exhausted_message(self):
        models = _FakeModels({m: [_daily_quota_error()] for m in self.CHAIN})
        with patch.object(gemini_api, "_get_client", return_value=_FakeClient(models)), \
                patch.object(gemini_api, "_model_chain", return_value=self.CHAIN):
            with self.assertRaises(QuotaExhaustedError) as ctx:
                asyncio.run(gemini_api.generate_structured("p", _Answer))
            # 이후 요청은 분석을 시작하기 전에 소진 상태를 알 수 있어야 한다
            self.assertIsNotNone(gemini_api.quota_reset_at())
        self.assertEqual(ctx.exception.error_code, "QUOTA_EXHAUSTED")
        self.assertIn("무료 사용량", ctx.exception.message)
        self.assertIsNotNone(ctx.exception.retry_at)

    def test_partial_exhaustion_is_congestion_not_quota(self):
        """일부만 소진되고 나머지가 혼잡이면 '사용량 소진'이 아니다 — 잠시 뒤 재시도로 풀릴 수 있다"""
        script = {"m1": [_daily_quota_error()], "m2": [_api_error(503)], "m3": [_api_error(503)]}
        models = _FakeModels(script)

        async def no_sleep(_):
            return None

        with patch.object(gemini_api, "_get_client", return_value=_FakeClient(models)), \
                patch.object(gemini_api, "_model_chain", return_value=self.CHAIN), \
                patch.object(gemini_api.asyncio, "sleep", no_sleep):
            with self.assertRaises(ExternalServiceError) as ctx:
                asyncio.run(gemini_api.generate_structured("p", _Answer))
            self.assertIsNone(gemini_api.quota_reset_at())
        self.assertNotIsInstance(ctx.exception, QuotaExhaustedError)
        self.assertIn("일부 모델은 오늘 사용량 소진", ctx.exception.message)


class DailyQuotaDelayTest(unittest.TestCase):

    def setUp(self):
        gemini_api._exhausted_until.clear()

    def test_reads_retry_delay(self):
        self.assertEqual(gemini_api._daily_quota_delay(_daily_quota_error("49261s")), 49261)

    def test_per_minute_429_is_not_daily(self):
        self.assertIsNone(gemini_api._daily_quota_delay(_api_error(429, "rate")))

    def test_other_codes_are_not_daily(self):
        self.assertIsNone(gemini_api._daily_quota_delay(_api_error(503)))


if __name__ == "__main__":
    unittest.main()
