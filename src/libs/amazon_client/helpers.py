from http import HTTPStatus
from typing import NoReturn

from httpx import Response

from .exceptions import AmazonAuthError, AmazonSPAPIError, AmazonThrottlingError

THROTTLING_CODES = {"QuotaExceeded", "Throttling", "RequestThrottled"}


def get_error_code_and_message(response: Response) -> tuple[str | None, str]:
    try:
        data = response.json()
    except ValueError:
        data = None

    code: str | None = None
    message: str | None = None

    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] or {}
            if isinstance(first, dict):
                code = first.get("code") or code
                message = first.get("message") or message
    if not message:
        message = response.text[:500]

    return code, message


def process_errors(response: Response) -> NoReturn:
    text_body = response.text
    code, message = get_error_code_and_message(response)

    status = response.status_code
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        raise AmazonAuthError(
            message=f"Amazon auth error ({status}): {message}",
            status_code=status,
            body=text_body,
        )

    retry_after_header = response.headers.get("Retry-After")
    retry_after: float | None = None
    if retry_after_header:
        try:
            retry_after = float(retry_after_header)
        except ValueError:
            retry_after = None

    if status == HTTPStatus.TOO_MANY_REQUESTS or (code and code in THROTTLING_CODES):
        raise AmazonThrottlingError(
            message=f"Amazon throttling error ({status}): {message}",
            status_code=status,
            code=code,
            retry_after=retry_after,
            body=text_body,
        )
    raise AmazonSPAPIError(
        message=f"Amazon SP-API error ({status}): {message}",
        status_code=status,
        code=code,
        body=text_body,
    )
