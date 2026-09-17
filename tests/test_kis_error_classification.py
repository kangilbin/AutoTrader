"""KIS 응답 코드 분류 테스트

rt_cd != "0" 은 "호출 실패"가 아니라 "KIS가 정상 응답했고 내용이 거절"이다.
전송 계층 실패는 http_client.fetch가 이미 5xx로 올리므로, 여기 도달한 거절은
기본적으로 사용자/요청 문제(4xx)이고 게이트웨이 계열(EGW*)만 인프라(5xx)다.

분류가 틀리면 사용자 오타 한 번에 스택 트레이스와 Sentry 이벤트가 발생한다
(handlers.py: 5xx만 exc_info + capture_exception).

실행:
  PYTHONPATH=. python -m unittest tests.test_kis_error_classification -v
"""
import unittest

from app.exceptions import ExternalServiceError, ValidationError
from app.external import kis_api


def _body(rt_cd="0", msg_cd="", msg1=""):
    return {"body": {"rt_cd": rt_cd, "msg_cd": msg_cd, "msg1": msg1}}


class KisInfraCodeTest(unittest.TestCase):
    """is_kis_infra_error 판별"""

    def test_egw_codes_are_infra(self):
        for code in ("EGW00133", "EGW00201", "EGW00123", "egw00133"):
            self.assertTrue(kis_api.is_kis_infra_error(code), f"{code}는 인프라성이어야 함")

    def test_business_codes_are_not_infra(self):
        for code in ("40580000", "40570000", "", None):
            self.assertFalse(kis_api.is_kis_infra_error(code), f"{code!r}은 업무 거절이어야 함")


class VerifyAccountBalanceTest(unittest.IsolatedAsyncioTestCase):
    """verify_account_balance 의 예외 분류"""

    def setUp(self):
        self._orig_fetch = kis_api.fetch
        self._orig_headers = kis_api.kis_headers
        kis_api.kis_headers = lambda *a, **k: {}
        self.access = {"simulation_yn": "N", "access_token": "t", "api_key": "k", "secret_key": "s"}

    def tearDown(self):
        kis_api.fetch = self._orig_fetch
        kis_api.kis_headers = self._orig_headers

    def _stub(self, response):
        async def _fetch(*a, **k):
            return response
        kis_api.fetch = _fetch

    async def test_success_does_not_raise(self):
        self._stub(_body(rt_cd="0"))
        await kis_api.verify_account_balance(self.access, "12345678-01")

    async def test_invalid_account_is_422_not_502(self):
        """계좌번호 오입력 — 사용자가 고칠 수 있는 문제라 4xx여야 한다"""
        self._stub(_body(rt_cd="1", msg_cd="40580000", msg1="계좌번호가 존재하지 않습니다."))

        with self.assertRaises(ValidationError) as cm:
            await kis_api.verify_account_balance(self.access, "99999999-01")

        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("계좌번호가 존재하지 않습니다", cm.exception.message)
        self.assertEqual(cm.exception.detail.get("msg_cd"), "40580000",
                         "msg_cd가 유실되면 분류 기준을 다듬을 근거가 없어진다")

    async def test_gateway_error_stays_5xx(self):
        """토큰 유량 제한 등 게이트웨이 오류 — 운영이 알아야 하므로 5xx 유지"""
        self._stub(_body(rt_cd="1", msg_cd="EGW00133", msg1="접근토큰 발급 잠시 후 다시 시도하세요."))

        with self.assertRaises(ExternalServiceError) as cm:
            await kis_api.verify_account_balance(self.access, "12345678-01")

        self.assertGreaterEqual(cm.exception.status_code, 500)
        self.assertEqual(cm.exception.detail.get("msg_cd"), "EGW00133")

    async def test_missing_msg_cd_defaults_to_4xx(self):
        """코드가 없으면 업무 거절로 본다 — 인프라 장애는 fetch가 이미 걸러낸 뒤다"""
        self._stub(_body(rt_cd="1", msg_cd="", msg1=""))

        with self.assertRaises(ValidationError) as cm:
            await kis_api.verify_account_balance(self.access, "12345678-01")

        self.assertEqual(cm.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()