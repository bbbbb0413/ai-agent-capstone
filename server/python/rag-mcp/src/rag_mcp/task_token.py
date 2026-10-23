"""작업 토큰 검증 (SPEC-limitations-fixes 3단계).

backend가 실행 중인 작업마다 발급한 서명 토큰에서 고객사를 읽는다. 공개키(JWKS)로 검증만 하므로 이 Server는
토큰을 만들 수 없다. 공개키는 backend에서 받아 캐시하고, 모르는 키 ID가 오면(backend 재시작) 다시 받는다.
"""

import asyncio
import os

import jwt

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")
MCP_BACKEND_TOKEN = os.environ.get("MCP_BACKEND_TOKEN", "")
ISSUER = "opslens-backend"
AUDIENCE = "opslens-task"


class InvalidTaskToken(Exception):
    """토큰이 없거나 서명·발급자·대상·만료가 맞지 않다."""


_keys: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _keys
    if _keys is None:
        _keys = jwt.PyJWKClient(f"{BACKEND_URL}/internal/.well-known/jwks.json", cache_keys=True,
                                headers={"Authorization": f"Bearer {MCP_BACKEND_TOKEN}"}, timeout=5)
    return _keys


def _verify(token: str, keys: jwt.PyJWKClient) -> str:
    try:
        key = keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "sub", "tenant"]})
    except (jwt.PyJWTError, ValueError) as e:
        raise InvalidTaskToken(type(e).__name__) from e
    return str(claims["tenant"])


async def tenant_of(token: str | None, keys: jwt.PyJWKClient | None = None) -> str:
    """토큰의 고객사. 공개키 조회가 동기 호출이라 스레드에서 돈다."""
    if not token:
        raise InvalidTaskToken("missing")
    return await asyncio.to_thread(_verify, token, keys or _jwks())
