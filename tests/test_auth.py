"""Tests for APIKeyAuthASGIMiddleware."""

import json

import pytest

from ttygeist.auth import APIKeyAuthASGIMiddleware


def _make_scope(headers=None, scope_type="http"):
    """Build a minimal ASGI scope."""
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.encode(), v.encode()))
    return {"type": scope_type, "headers": raw_headers}


class ResponseCapture:
    """Capture ASGI send() calls."""

    def __init__(self):
        self.events = []
        self.status = None
        self.body = None

    async def send(self, event):
        self.events.append(event)
        if event.get("type") == "http.response.start":
            self.status = event["status"]
        elif event.get("type") == "http.response.body":
            self.body = event.get("body", b"")


class FakeApp:
    """Fake ASGI app that records whether it was called."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.asyncio
class TestCustomHeader:
    async def test_valid_key(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret123"])
        scope = _make_scope({"X-API-Key": "secret123"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert app.called

    async def test_invalid_key(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret123"])
        scope = _make_scope({"X-API-Key": "wrong"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert not app.called
        assert cap.status == 401

    async def test_missing_header(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret123"])
        scope = _make_scope({})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert not app.called
        assert cap.status == 401

    async def test_case_insensitive_header_lookup(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["key1"])
        # Headers in ASGI are lowercase bytes
        scope = _make_scope({"x-api-key": "key1"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert app.called


@pytest.mark.asyncio
class TestBearerAuth:
    async def test_valid_bearer(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["mytoken"], header_name="Authorization")
        scope = _make_scope({"Authorization": "Bearer mytoken"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert app.called

    async def test_invalid_bearer(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["mytoken"], header_name="Authorization")
        scope = _make_scope({"Authorization": "Bearer wrongtoken"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert not app.called
        assert cap.status == 401

    async def test_missing_bearer_prefix(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["mytoken"], header_name="Authorization")
        scope = _make_scope({"Authorization": "mytoken"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert not app.called
        assert cap.status == 401


@pytest.mark.asyncio
class TestMultipleKeys:
    async def test_any_valid_key_accepted(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["key1", "key2", "key3"])
        for key in ["key1", "key2", "key3"]:
            app.called = False
            scope = _make_scope({"X-API-Key": key})
            cap = ResponseCapture()
            await mw(scope, None, cap.send)
            assert app.called, f"Key {key} should be accepted"


@pytest.mark.asyncio
class TestNoKeys:
    async def test_no_keys_rejects(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=[])
        scope = _make_scope({"X-API-Key": "anything"})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert not app.called
        assert cap.status == 401


@pytest.mark.asyncio
class TestNonHTTPPassthrough:
    async def test_websocket_passes_through(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret"])
        scope = _make_scope(scope_type="websocket")
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert app.called

    async def test_lifespan_passes_through(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret"])
        scope = _make_scope(scope_type="lifespan")
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        assert app.called


@pytest.mark.asyncio
class TestResponseFormat:
    async def test_401_returns_json(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret"])
        scope = _make_scope({})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        body = json.loads(cap.body)
        assert "error" in body
        assert "detail" in body

    async def test_401_content_type(self):
        app = FakeApp()
        mw = APIKeyAuthASGIMiddleware(app, api_keys=["secret"])
        scope = _make_scope({})
        cap = ResponseCapture()
        await mw(scope, None, cap.send)
        start = cap.events[0]
        headers = dict(start["headers"])
        assert headers[b"content-type"] == b"application/json"
