"""API key authentication middleware for ttygeist (pure ASGI).

This replaces the earlier BaseHTTPMiddleware implementation to avoid
side effects with streaming request bodies used by FastMCP.
"""

from __future__ import annotations

import hmac
import logging
from typing import Iterable, Set, Callable, Awaitable, Dict, Any

logger = logging.getLogger(__name__)


class APIKeyAuthASGIMiddleware:
    """Minimal ASGI middleware enforcing API key authentication.

    Supports two header styles:
      1. Custom header (default: X-API-Key) whose value is the key.
      2. Authorization: Bearer <key> (set header_name="Authorization").

    The middleware performs constant‑time comparisons to reduce timing
    side-channel leakage (mostly academic for typical deployments but cheap).
    """

    def __init__(self, app: Callable, api_keys: Iterable[str], header_name: str = "X-API-Key") -> None:
        self.app = app
        # Normalize and store as a set for membership checks
        self._keys: Set[str] = {str(k) for k in api_keys if str(k)}
        self.header_name = header_name
        self._use_bearer = header_name.lower() == "authorization"

        if not self._keys:
            logger.warning("APIKeyAuthASGIMiddleware initialized with zero keys; all requests will be rejected unless allow_anon bypasses middleware.")

    async def __call__(self, scope: Dict[str, Any], receive: Callable[[], Awaitable[Dict[str, Any]]], send: Callable[[Dict[str, Any]], Awaitable[None]]):
        # Only guard HTTP requests; pass through websockets & other scope types.
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        # Fast path: if no keys configured treat as locked down (reject) — but the
        # server code should normally not install this middleware in allow_anon mode.
        if not self._keys:
            await self._reject(send, detail="No API keys configured")
            return

        # Build a header dict (bytes -> str decode once).
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

        candidate: str | None = None
        if self._use_bearer:
            auth_val = headers.get("authorization", "")
            if auth_val.startswith("Bearer "):
                candidate = auth_val[7:]
        else:
            candidate = headers.get(self.header_name.lower())

        if not candidate:
            logger.debug("Auth reject: missing API key header")
            await self._reject(send, error="Missing API key", detail=f"Provide key in {self.header_name} header")
            return

        # Constant‑time membership: iterate all keys and compare_digest; succeed on first match.
        accepted = False
        for key in self._keys:
            if hmac.compare_digest(candidate, key):  # constant-time
                accepted = True
                break

        if not accepted:
            logger.info("Auth reject: invalid API key presented")
            await self._reject(send, error="Invalid API key", detail="Provided key not recognized")
            return

        # Proceed to wrapped app.
        return await self.app(scope, receive, send)

    async def _reject(self, send: Callable[[Dict[str, Any]], Awaitable[None]], error: str = "Unauthorized", detail: str = "Missing or invalid API key") -> None:
        body_bytes = (f"{{\n  \"error\": \"{error}\",\n  \"detail\": \"{detail}\"\n}}" ).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body_bytes})


# Backwards compatibility export (old name was APIKeyAuthMiddleware)
APIKeyAuthMiddleware = APIKeyAuthASGIMiddleware
