"""Custom exceptions for DhanHQ API interactions."""


class DhanError(Exception):
    """Base exception for all Dhan client errors."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        self.status_code = status_code
        self.response = response or {}
        super().__init__(message)


class AuthenticationError(DhanError):
    """Raised when API credentials are invalid or expired (HTTP 401/403)."""
    pass


class RateLimitError(DhanError):
    """Raised when DhanHQ rate limit is hit.

    Option chain: 1 unique request per 3 seconds.
    Historical data: varies by endpoint.
    """

    def __init__(self, message: str = "Rate limit exceeded", retry_after: float = 3.0, **kw):
        self.retry_after = retry_after
        super().__init__(message, **kw)


class InstrumentNotFoundError(DhanError):
    """Raised when a security ID or symbol cannot be resolved from the scrip master."""
    pass


class WebSocketError(DhanError):
    """Raised on WebSocket connection, subscription, or parsing failures."""
    pass


class WebSocketDisconnected(WebSocketError):
    """Raised when the feed connection drops unexpectedly."""
    pass


class MockModeError(DhanError):
    """Raised when an operation is attempted in mock mode that requires live connectivity."""
    pass
