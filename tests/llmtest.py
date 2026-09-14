"""Fakes and the shared conformance suite for llm adapters.

The fakes exist so HybridSearcher, QueryRouter, and chat.Service become testable
at all — a concrete-client dependency would make every one of them require a
live Ollama.

The conformance table is the most valuable single file in the Go tree and it
carries over unchanged in spirit: one table of wire-level situations every
provider can produce, asserting that the *normalized outcome* is the same even
though the JSON that produces it differs per provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from zeitnot.llm.base import (
    FINISH_LENGTH,
    FINISH_STOP,
    ROLE_USER,
    ChatRequest,
    ChatResponse,
    Message,
    Usage,
)
from zeitnot.llm.errors import ErrorKind

# ---------- scenarios ----------

SUCCESS = "success"
EMPTY_CONTENT = "empty content"
TRUNCATED = "truncated response"
UNAUTHORIZED = "401 unauthorized"
RATE_LIMITED = "429 rate limited"
SERVER_ERROR = "500 server error"
MALFORMED = "malformed body"


@dataclass(frozen=True)
class Expectation:
    """The normalized semantics every adapter must produce for one scenario."""

    scenario: str
    want_kind: ErrorKind | None = None  # None means the call must succeed
    want_finish: str | None = None


# Asserts on error classification, never on attempt counts: retry behavior is
# deliberately non-uniform — the SDKs retry for hosted providers while the
# Ollama adapter fails fast against a local process.
CHAT_EXPECTATIONS = [
    Expectation(SUCCESS, want_finish=FINISH_STOP),
    Expectation(TRUNCATED, want_finish=FINISH_LENGTH),
    Expectation(EMPTY_CONTENT, want_kind=ErrorKind.BAD_RESPONSE),
    Expectation(MALFORMED, want_kind=ErrorKind.BAD_RESPONSE),
    Expectation(UNAUTHORIZED, want_kind=ErrorKind.UNAUTHORIZED),
    Expectation(RATE_LIMITED, want_kind=ErrorKind.RATE_LIMITED),
    Expectation(SERVER_ERROR, want_kind=ErrorKind.UNAVAILABLE),
]


def sample_request() -> ChatRequest:
    """The message shape every adapter must accept: a system prompt as a field,
    and messages that begin with a user turn and alternate."""
    return ChatRequest(
        system="You are a chess coach.",
        messages=[Message(role=ROLE_USER, content="Why do I lose in the endgame?")],
    )


# The message shapes every adapter must reject, so a malformed conversation is
# refused here rather than by one provider's API and not another's.
INVALID_MESSAGE_CASES: list[tuple[str, Sequence[Message]]] = [
    ("empty", []),
    ("starts with assistant", [Message(role="assistant", content="hi")]),
    (
        "non-alternating",
        [Message(role="user", content="a"), Message(role="user", content="b")],
    ),
    ("system smuggled into messages", [Message(role="system", content="be nice")]),
]


class ConsumerRefusedError(Exception):
    """Raised by the on_delta callback in the consumer-failure case.

    A type the adapter has never seen, which is what makes "propagates
    unchanged" checkable.
    """


# ---------- fakes ----------


@dataclass
class FakeEmbedder:
    """Deterministic vectors, no network."""

    dims: int = 768
    error: Exception | None = None
    calls: list[list[str]] = field(default_factory=list)

    def name(self) -> str:
        return "fake"

    def model(self) -> str:
        return "fake-embed"

    def dimensions(self) -> int:
        return self.dims or 768

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        # Deterministic and input-dependent; the values carry no meaning beyond
        # "the same text embeds the same way".
        return [[((len(text) + j) % 17) / 17 for j in range(self.dimensions())] for text in texts]


@dataclass
class FakeChatModel:
    """Canned responses, recording the requests it received — which is what
    makes the system-prompt and alternation rules assertable."""

    response: ChatResponse | None = None
    error: Exception | None = None
    requests: list[ChatRequest] = field(default_factory=list)
    deltas: Sequence[str] = ()

    def name(self) -> str:
        return "fake"

    def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return ChatResponse(
            text=f"fake answer to {req.messages[-1].content!r}",
            model="fake-chat",
            finish_reason=FINISH_STOP,
            usage=Usage(),
        )


@dataclass
class FakeStreamingChatModel(FakeChatModel):
    """A FakeChatModel that also streams, so the two paths can be compared."""

    def chat_stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResponse:
        buffered = self.chat(req)
        pieces: Sequence[str] = self.deltas or _split_for_streaming(buffered.text)
        for piece in pieces:
            on_delta(piece)
        return buffered


def _split_for_streaming(text: str, chunks: int = 3) -> list[str]:
    """Cut text into several pieces, so a suite asserting "more than one delta"
    cannot pass against a buffered implementation."""
    if not text:
        return []
    size = max(1, len(text) // chunks)
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---------- request-shape assertions ----------


def assert_no_temperature(params: dict[str, Any]) -> None:
    """The assertion the cutover gate cannot make.

    Phase 7 gates on prompt parity alone, which covers everything the provider
    *reads* but nothing about how the request is *shaped*. A stray temperature
    default would change answer distributions invisibly, because prompt parity
    would still pass.
    """
    assert "temperature" not in params, (
        "temperature must be absent when the caller did not set one; "
        "a default here changes answer distributions with prompt parity intact"
    )
