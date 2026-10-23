"""rag-mcp는 backend 공개키로 작업 토큰을 검증하고 그 고객사만 쓴다 (SPEC-limitations-fixes 3단계)."""

import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from rag_mcp import task_token


def key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


class StubKeys:
    """PyJWKClient 대역: backend의 공개키 하나"""

    def __init__(self, public):
        self.public = public

    def get_signing_key_from_jwt(self, token):
        return type("Key", (), {"key": self.public})()


def token(private, **overrides):
    claims = {"iss": "opslens-backend", "aud": "opslens-task", "sub": "task-1", "tenant": "tenant-b",
              "exp": int(time.time()) + 60, **overrides}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "k1"})


class TaskTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_tenant_comes_from_a_valid_token(self):
        private, public = key_pair()
        self.assertEqual(await task_token.tenant_of(token(private), StubKeys(public)), "tenant-b")

    async def test_invalid_tokens_are_rejected(self):
        private, public = key_pair()
        other, _ = key_pair()
        cases = {
            "다른 키로 서명": token(other),
            "만료": token(private, exp=int(time.time()) - 1),
            "다른 대상": token(private, aud="something-else"),
            "다른 발급자": token(private, iss="someone"),
            "고객사 없음": jwt.encode({"iss": "opslens-backend", "aud": "opslens-task", "sub": "t",
                                   "exp": int(time.time()) + 60}, private, algorithm="RS256"),
            "HS256 위조": jwt.encode({"tenant": "tenant-a"}, "secret", algorithm="HS256"),
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaises(task_token.InvalidTaskToken):
                    await task_token.tenant_of(value, StubKeys(public))
        for missing in (None, ""):
            with self.assertRaises(task_token.InvalidTaskToken):
                await task_token.tenant_of(missing, StubKeys(public))


if __name__ == "__main__":
    unittest.main()
