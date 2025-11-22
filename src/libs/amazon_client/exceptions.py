class AmazonAsyncClientError(Exception):
    """Amazon client base exception"""


class AmazonNetworkError(AmazonAsyncClientError):
    """Network-level error (connection, timeout, etc.)"""

    def __init__(self, message: str, *, original_exc: Exception | None = None):
        super().__init__(message)
        self.original_exc = original_exc


class AmazonAuthError(AmazonAsyncClientError):
    """Auth / permission error (401/403, invalid LWA etc.)"""

    def __init__(self, message: str, *, status_code: int, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AmazonThrottlingError(AmazonAsyncClientError):
    """Throttling error (429, QuotaExceeded, Throttling)"""
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        retry_after: float | None = None,
        body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        self.body = body


class AmazonSPAPIError(AmazonAsyncClientError):
    """Generic SP-API error (4xx/5xx without throttling/auth-specific semantics)"""
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body
