import unittest

import httpx

from agent.backend import Backend


def client(handler) -> Backend:
    backend = Backend()
    backend.http = httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handler))
    backend.RESULT_RETRY_SECONDS = (0, 0)
    return backend


class BackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_emit_raises_when_backend_rejects_the_event(self):
        backend = client(lambda request: httpx.Response(400))
        with self.assertRaises(httpx.HTTPStatusError):
            await backend.emit("t1", "STEP", {})

    async def test_result_retries_server_errors_until_saved(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503 if len(calls) < 3 else 202)

        await client(handler).result("t1", status="DONE", answer="ok")
        self.assertEqual(3, len(calls))

    async def test_result_gives_up_after_retries(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ConnectError("down")

        with self.assertRaises(httpx.ConnectError):
            await client(handler).result("t1", status="DONE", answer="ok")
        self.assertEqual(3, len(calls))

    async def test_result_does_not_retry_client_errors(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(404)

        with self.assertRaises(httpx.HTTPStatusError):
            await client(handler).result("t1", status="DONE", answer="ok")
        self.assertEqual(1, len(calls))

    async def test_task_token_conflict_means_the_task_is_not_active(self):
        from agent.backend import TaskFinished
        with self.assertRaises(TaskFinished):
            await client(lambda request: httpx.Response(409, text="FAILED")).task_token("t1")
        token = await client(lambda request: httpx.Response(200, json={"token": "abc", "expiresAt": "x"})).task_token("t1")
        self.assertEqual(token, "abc")
        with self.assertRaises(httpx.HTTPStatusError):
            await client(lambda request: httpx.Response(401)).task_token("t1")


if __name__ == "__main__":
    unittest.main()
