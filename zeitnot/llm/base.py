"""The provider-neutral protocols zeitnot uses to talk to language models.

Chat and embeddings are deliberately separate concepts: Anthropic offers no
embeddings API, so a single Provider protocol would have to lie about its
capabilities.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from zeitnot.llm.errors import ErrorKind, LLMError

Role = Literal["user", "assistant"]

ROLE_USER: Role = "user"
ROLE_ASSISTANT: Role = "assistant"

# Normalized finish reasons. Anything an adapter cannot map becomes "other".
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"
FINISH_OTHER = "other"


@dataclass(slots=True, frozen=True)
class Message:
    """One conversational turn.

    There is deliberately no "system" role: the system prompt is a field on
    ChatRequest, not a message. That matches Anthropic's wire format, and the
    Ollama and OpenAI adapters prepend it as a message themselves, so callers
    cannot construct a shape Anthropic rejects.
    """

    role: str
    content: str


@dataclass(slots=True)
class ChatRequest:
    system: str = ""  # may be empty
    messages: Sequence[Message] = ()  # must alternate, must begin with a user turn
    model: str = ""  # empty => the adapter's configured default

    # Optional knobs. None / zero mean "provider default"; adapters omit rather
    # than guess. In particular a temperature that was never set must not
    # become a sent field — a stray default would change answer distributions
    # invisibly, since prompt parity would still pass.
    max_tokens: int = 0
    temperature: float | None = None
    stop_after: Sequence[str] = ()


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ChatResponse:
    text: str
    model: str  # the model that actually served the request
    finish_reason: str = FINISH_OTHER
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class ChatModel(Protocol):
    def chat(self, req: ChatRequest) -> ChatResponse: ...

    def name(self) -> str:
        """Identify the provider for error messages, banners, and eval labels."""
        ...


@runtime_checkable
class StreamingChatModel(ChatModel, Protocol):
    """An optional capability, implemented by all three adapters today.

    It is separate from ChatModel so a future adapter can omit it: callers check
    with isinstance, and an adapter that does not implement it still works.

    `chat_stream` must deliver exactly the text `chat` would have returned, both
    through on_delta and in the response. A caller displays the deltas and
    records the response, so any divergence shows the user one answer and
    remembers another. An exception raised by on_delta propagates unchanged, so
    a consumer failure is never mistaken for a provider failure.
    """

    def chat_stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResponse: ...


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input, in input order.

        Adapters that lack native batching loop internally, so callers need only
        one code path.
        """
        ...

    def dimensions(self) -> int:
        """The vector width this embedder produces, or 0 when it is not known
        ahead of the first call. Startup uses it to verify the vector(N) column
        instead of discovering a mismatch mid-ingestion."""
        ...

    def model(self) -> str:
        """The embedding model producing the vectors. Recorded alongside the
        index so a later change is caught rather than silently degrading
        retrieval."""
        ...

    def name(self) -> str: ...


@runtime_checkable
class Preflighter(Protocol):
    """Implemented by adapters that can verify credentials, reachability, and
    the configured model before the first real request.

    An LLMError whose kind is PREFLIGHT_INCONCLUSIVE is a warning: the check
    itself could not be completed — a models endpoint a gateway does not
    implement, say — and startup should continue. Any other error is fatal.
    """

    def preflight(self) -> None: ...


def embed_one(embedder: Embedder, text: str) -> list[float]:
    """Convenience for the many call sites that embed exactly one string."""
    vectors = embedder.embed([text])
    if len(vectors) != 1:
        raise LLMError(
            embedder.name(),
            "embed",
            ErrorKind.BAD_RESPONSE,
            f"expected 1 vector, got {len(vectors)}",
        )
    return vectors[0]


def validate_messages(messages: Sequence[Message]) -> None:
    """Enforce the shape every adapter must be able to send: non-empty, begins
    with a user turn, strictly alternating.

    Today's history assembly satisfies this by construction; validating here is
    what keeps it true, and what stops a malformed conversation from being
    rejected by one provider and silently accepted by another.
    """
    if not messages:
        raise LLMError("llm", "validate", ErrorKind.INVALID_REQUEST, "messages must not be empty")
    if messages[0].role != ROLE_USER:
        raise LLMError(
            "llm",
            "validate",
            ErrorKind.INVALID_REQUEST,
            f"messages must begin with a {ROLE_USER!r} turn, got {messages[0].role!r}",
        )
    for i, message in enumerate(messages):
        if message.role not in (ROLE_USER, ROLE_ASSISTANT):
            raise LLMError(
                "llm",
                "validate",
                ErrorKind.INVALID_REQUEST,
                f"message {i} has unsupported role {message.role!r} "
                "(the system prompt belongs in ChatRequest.system)",
            )
        if i > 0 and message.role == messages[i - 1].role:
            raise LLMError(
                "llm",
                "validate",
                ErrorKind.INVALID_REQUEST,
                f"messages must alternate, but {i - 1} and {i} are both {message.role!r}",
            )
