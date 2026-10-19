"""agent만 이 Server를 호출할 수 있게 서비스 토큰을 검사하는 ASGI 미들웨어."""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class ServiceTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()
        self.enabled = bool(token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not self.enabled or not hmac.compare_digest(given, self.expected):
                await JSONResponse({"error": "invalid service token"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)
