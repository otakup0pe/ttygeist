"""API key authentication middleware for Serial MCP Server."""

import logging
from typing import List
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


logger = logging.getLogger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication."""
    
    def __init__(self, app, api_keys: List[str], header_name: str = "X-API-Key"):
        """
        Initialize authentication middleware.
        
        Args:
            app: ASGI application
            api_keys: List of valid API keys
            header_name: Header name for API key (or "Authorization" for Bearer)
        """
        super().__init__(app)
        self.api_keys = set(api_keys)  # Use set for O(1) lookup
        self.header_name = header_name
        self.use_bearer = (header_name.lower() == "authorization")
    
    async def dispatch(self, request: Request, call_next):
        """Process request and validate API key."""
        
        # Get API key from header
        if self.use_bearer:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]  # Strip "Bearer " prefix
            else:
                api_key = None
        else:
            api_key = request.headers.get(self.header_name)
        
        # Validate API key
        if not api_key:
            logger.warning(f"Missing API key from {request.client.host}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Missing API key",
                    "detail": f"API key must be provided in {self.header_name} header"
                }
            )
        
        if api_key not in self.api_keys:
            logger.warning(f"Invalid API key from {request.client.host}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Invalid API key",
                    "detail": "The provided API key is not valid"
                }
            )
        
        # API key is valid, proceed with request
        response = await call_next(request)
        return response
