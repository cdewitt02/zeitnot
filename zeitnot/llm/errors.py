"""Sentinel error kinds every adapter normalizes to.

Callers react to categories, never to message strings. The Go tree used wrapped
sentinel error values and `errors.Is`; the Python equivalent is one exception
type carrying a `kind`, because catching by class and inspecting one field is
what reads naturally here — and `isinstance` plus `kind` is exactly as
checkable as `errors.Is` was.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum


class ErrorKind(Enum):
    """The normalized failure categories. Values are the Go sentinel messages."""

    NOT_CONFIGURED = "llm: provider not configured"
    UNAUTHORIZED = "llm: authentication failed"
    RATE_LIMITED = "llm: rate limited"
    UNAVAILABLE = "llm: provider unavailable"
    BAD_RESPONSE = "llm: malformed or empty response"
    CONTEXT_LENGTH = "llm: input exceeds model context window"
    MODEL_NOT_FOUND = "llm: model not available on provider"
    INVALID_REQUEST = "llm: invalid request"
    # A startup check that could not be completed. A warning, not a failure.
    PREFLIGHT_INCONCLUSIVE = "llm: preflight check inconclusive"


class LLMError(Exception):
    """Carries the provider and operation alongside a kind.

    `cause` is the underlying exception, kept so a traceback still points at
    what actually failed.
    """

    def __init__(
        self,
        provider: str,
        op: str,
        kind: ErrorKind,
        detail: str = "",
        cause: BaseException | None = None,
    ) -> None:
        self.provider = provider
        self.op = op
        self.kind = kind
        self.detail = detail
        self.cause = cause
        message = f"{provider} {op}: {kind.value}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class ConsumerError(Exception):
    """Internal marker wrapping an exception raised by a caller's on_delta.

    A consumer failure is the consumer's error, not the provider's. But an
    adapter's streaming loop runs inside a `try` that catches broad SDK
    exceptions, so without a marker the caller's own exception is swept up and
    classified as a provider fault — and the REPL loses its ability to tell
    "the terminal write failed" from "Anthropic is down".

    Adapters wrap the on_delta call in `deliver`, and unwrap on the way out, so
    what reaches the caller is the exact exception they raised.
    """

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


def deliver(on_delta: Callable[[str], None], text: str) -> None:
    """Hand one delta to the caller, marking any failure as theirs."""
    try:
        on_delta(text)
    except ConsumerError:
        raise
    except BaseException as err:
        raise ConsumerError(err) from err


def classify_status(status: int) -> ErrorKind:
    """Map an HTTP status onto a kind."""
    if status in (401, 403):
        return ErrorKind.UNAUTHORIZED
    if status == 404:
        return ErrorKind.MODEL_NOT_FOUND
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if status >= 500:
        return ErrorKind.UNAVAILABLE
    if status >= 400:
        return ErrorKind.INVALID_REQUEST
    return ErrorKind.BAD_RESPONSE
