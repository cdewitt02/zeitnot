"""The shared conformance suite, run against all three adapters.

One table of wire-level situations, provider-shaped fixtures per scenario, and
one set of normalized expectations. A provider that classifies a 429 as
"unavailable", or returns an empty answer as success, fails here rather than in
production.

Two mechanisms stand in for the Go suite's single httptest.Server, because the
adapters do not share a transport: the Ollama client is hand-rolled `requests`,
while the Anthropic and OpenAI SDKs are built on `httpx`. `responses` intercepts
the first; the second gets a `MockTransport` injected through the adapters'
`http_client` parameter, which is the direct analogue of the Go suite pointing
`option.WithHTTPClient` at an httptest.Server. `Recorder` hides that split so
the conformance table itself stays provider-neutral, which is the property that
makes it worth having.

Note the SDKs are built on `httpx2`, not `httpx` — they type-check the injected
client, so importing the wrong one fails loudly rather than silently bypassing
the mock.

Every adapter is pointed at a fake base URL and every request is intercepted, so
nothing here touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import httpx2
import pytest
import responses

from tests.llmtest import (
    CHAT_EXPECTATIONS,
    EMPTY_CONTENT,
    INVALID_MESSAGE_CASES,
    MALFORMED,
    RATE_LIMITED,
    SERVER_ERROR,
    SUCCESS,
    TRUNCATED,
    UNAUTHORIZED,
    ConsumerRefusedError,
    Expectation,
    sample_request,
)
from zeitnot.llm.base import ChatRequest, ChatResponse
from zeitnot.llm.errors import ErrorKind, LLMError

BASE = "http://provider.test"

# The answer every success fixture returns, in pieces. Several pieces, so the
# streaming assertions cannot pass against a buffered implementation.
ANSWER_PARTS = ["Your endgames ", "leak centipawns ", "around move 30."]
ANSWER = "".join(ANSWER_PARTS)


# ---------- the transport-neutral recorder ----------


class Recorder(Protocol):
    """Registers a canned response and records what was sent to get it."""

    def post(self, url: str, *, status: int, body: str, content_type: str = ...) -> None: ...

    def sent(self) -> list[dict[str, Any]]:
        """The JSON bodies of the requests that were actually made."""
        ...

    def call_count(self) -> int: ...


class _RequestsRecorder:
    """Backed by `responses`, for the hand-rolled Ollama client."""

    def __init__(self, mock: responses.RequestsMock) -> None:
        self._mock = mock

    def post(
        self, url: str, *, status: int, body: str, content_type: str = "application/json"
    ) -> None:
        self._mock.add(responses.POST, url, body=body, status=status, content_type=content_type)

    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(call.request.body or "{}") for call in self._mock.calls]

    def call_count(self) -> int:
        return len(self._mock.calls)


class _HttpxRecorder:
    """Backed by httpx2.MockTransport, for the two SDKs built on it.

    Responses are queued rather than keyed by URL: every scenario here registers
    exactly the calls it expects, in order, and a request arriving with nothing
    queued is itself the failure the "no request was sent" tests look for.
    """

    def __init__(self) -> None:
        self._queued: list[httpx2.Response] = []
        self._requests: list[httpx2.Request] = []

    def post(
        self, url: str, *, status: int, body: str, content_type: str = "application/json"
    ) -> None:
        self._queued.append(
            httpx2.Response(status, content=body, headers={"content-type": content_type})
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self._requests.append(request)
        if not self._queued:
            raise AssertionError(f"unexpected request to {request.url}")
        return self._queued.pop(0)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(r.content or b"{}") for r in self._requests]

    def call_count(self) -> int:
        return len(self._requests)


@contextmanager
def _requests_mock() -> Iterator[Recorder]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield _RequestsRecorder(mock)


@contextmanager
def _httpx_mock() -> Iterator[Recorder]:
    """Yields a recorder whose transport the adapter factories pick up.

    The factories read `_ACTIVE_HTTPX`, because the adapter has to be
    constructed *after* the fixtures are registered — the same ordering the Go
    suite gets for free by starting its server first.
    """
    global _ACTIVE_HTTPX
    recorder = _HttpxRecorder()
    _ACTIVE_HTTPX = recorder
    try:
        yield recorder
    finally:
        _ACTIVE_HTTPX = None


_ACTIVE_HTTPX: _HttpxRecorder | None = None


def _http_client() -> httpx2.Client | None:
    if _ACTIVE_HTTPX is None:
        return None
    return httpx2.Client(transport=_ACTIVE_HTTPX.transport())


# ---------- Ollama fixtures ----------


def _ollama_chat_body(content: str, done_reason: str = "stop") -> dict[str, Any]:
    return {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": content},
        "done_reason": done_reason,
        "prompt_eval_count": 11,
        "eval_count": 7,
    }


def _register_ollama(mock: Recorder, scenario: str, stream: bool) -> None:
    url = f"{BASE}/api/chat"
    if scenario in (SUCCESS, TRUNCATED):
        done = "stop" if scenario == SUCCESS else "length"
        if stream:
            # NDJSON, not SSE: one object per token, then a final object
            # carrying the stats and done_reason.
            lines = [json.dumps(_ollama_chat_body(part, "")) for part in ANSWER_PARTS]
            lines.append(json.dumps(_ollama_chat_body("", done)))
            mock.post(url, status=200, body="\n".join(lines), content_type="application/x-ndjson")
        else:
            mock.post(url, status=200, body=json.dumps(_ollama_chat_body(ANSWER, done)))
    elif scenario == EMPTY_CONTENT:
        mock.post(url, status=200, body=json.dumps(_ollama_chat_body("")))
    elif scenario == MALFORMED:
        mock.post(url, status=200, body="{not json at all")
    elif scenario == UNAUTHORIZED:
        mock.post(url, status=401, body="forbidden")
    elif scenario == RATE_LIMITED:
        mock.post(url, status=429, body="slow down")
    elif scenario == SERVER_ERROR:
        mock.post(url, status=500, body="boom")


def _new_ollama() -> Any:
    from zeitnot.llm.ollama import OllamaChatModel

    return OllamaChatModel(base_url=BASE)


# ---------- Anthropic fixtures ----------


def _anthropic_body(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def _anthropic_sse(parts: list[str], stop_reason: str) -> str:
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": _anthropic_body("", stop_reason)}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    events.extend(
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": part},
            },
        )
        for part in parts
    )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events)


def _register_anthropic(mock: Recorder, scenario: str, stream: bool) -> None:
    url = f"{BASE}/v1/messages"
    sse = "text/event-stream"
    if scenario in (SUCCESS, TRUNCATED):
        stop = "end_turn" if scenario == SUCCESS else "max_tokens"
        if stream:
            mock.post(url, status=200, body=_anthropic_sse(ANSWER_PARTS, stop), content_type=sse)
        else:
            mock.post(url, status=200, body=json.dumps(_anthropic_body(ANSWER, stop)))
    elif scenario == EMPTY_CONTENT:
        if stream:
            mock.post(url, status=200, body=_anthropic_sse([], "end_turn"), content_type=sse)
        else:
            mock.post(url, status=200, body=json.dumps(_anthropic_body("")))
    elif scenario == MALFORMED:
        mock.post(url, status=200, body="{not json at all")
    elif scenario == UNAUTHORIZED:
        mock.post(url, status=401, body=json.dumps({"error": {"message": "bad key"}}))
    elif scenario == RATE_LIMITED:
        mock.post(url, status=429, body=json.dumps({"error": {"message": "slow"}}))
    elif scenario == SERVER_ERROR:
        mock.post(url, status=500, body=json.dumps({"error": {"message": "boom"}}))


def _new_anthropic() -> Any:
    from zeitnot.llm.anthropic import AnthropicChatModel

    # max_retries=0 so a fixture answers once. Never layer an adapter-level loop
    # on top of the SDK's own retries.
    return AnthropicChatModel(
        api_key="sk-test", base_url=BASE, max_retries=0, http_client=_http_client()
    )


# ---------- OpenAI fixtures ----------


def _openai_body(text: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5-2025-08-07",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _openai_sse(parts: list[str], finish_reason: str) -> str:
    def chunk(choices: list[dict[str, Any]], usage: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5-2025-08-07",
            "choices": choices,
        }
        if usage is not None:
            out["usage"] = usage
        return out

    chunks = [
        chunk([{"index": 0, "delta": {"content": part}, "finish_reason": None}]) for part in parts
    ]
    chunks.append(chunk([{"index": 0, "delta": {}, "finish_reason": finish_reason}]))
    # The usage-only chunk that follows the last content chunk, which arrives
    # only because the adapter asks for stream_options.include_usage.
    chunks.append(chunk([], {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}))
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _register_openai(mock: Recorder, scenario: str, stream: bool) -> None:
    url = f"{BASE}/chat/completions"
    sse = "text/event-stream"
    if scenario in (SUCCESS, TRUNCATED):
        finish = "stop" if scenario == SUCCESS else "length"
        if stream:
            mock.post(url, status=200, body=_openai_sse(ANSWER_PARTS, finish), content_type=sse)
        else:
            mock.post(url, status=200, body=json.dumps(_openai_body(ANSWER, finish)))
    elif scenario == EMPTY_CONTENT:
        if stream:
            mock.post(url, status=200, body=_openai_sse([], "stop"), content_type=sse)
        else:
            mock.post(url, status=200, body=json.dumps(_openai_body("")))
    elif scenario == MALFORMED:
        mock.post(url, status=200, body="{not json at all")
    elif scenario == UNAUTHORIZED:
        mock.post(url, status=401, body=json.dumps({"error": {"message": "bad key"}}))
    elif scenario == RATE_LIMITED:
        mock.post(url, status=429, body=json.dumps({"error": {"message": "slow"}}))
    elif scenario == SERVER_ERROR:
        mock.post(url, status=500, body=json.dumps({"error": {"message": "boom"}}))


def _new_openai() -> Any:
    from zeitnot.llm.openai import OpenAIChatModel

    return OpenAIChatModel(
        api_key="sk-test", base_url=BASE, max_retries=0, http_client=_http_client()
    )


# name, mock factory, adapter factory, fixture registrar
PROVIDERS: list[tuple[str, Any, Any, Any]] = [
    ("ollama", _requests_mock, _new_ollama, _register_ollama),
    ("anthropic", _httpx_mock, _new_anthropic, _register_anthropic),
    ("openai", _httpx_mock, _new_openai, _register_openai),
]
PROVIDER_IDS = [p[0] for p in PROVIDERS]


# ---------- the suite ----------


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
@pytest.mark.parametrize("exp", CHAT_EXPECTATIONS, ids=lambda e: e.scenario)
def test_chat_conformance(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
    exp: Expectation,
) -> None:
    with mock_factory() as mock:
        register(mock, exp.scenario, False)
        model = new()

        if exp.want_kind is not None:
            with pytest.raises(LLMError) as excinfo:
                model.chat(sample_request())
            assert excinfo.value.kind is exp.want_kind, (
                f"{name}/{exp.scenario}: kind = {excinfo.value.kind}, want {exp.want_kind}"
            )
            return

        resp = model.chat(sample_request())
        assert resp.text, f"{name}/{exp.scenario}: want non-empty text"
        assert resp.finish_reason == exp.want_finish, (
            f"{name}/{exp.scenario}: finish_reason = {resp.finish_reason}, want {exp.want_finish}"
        )


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
@pytest.mark.parametrize("exp", CHAT_EXPECTATIONS, ids=lambda e: e.scenario)
def test_stream_conformance(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
    exp: Expectation,
) -> None:
    """The streaming half, with equivalence as its central assertion.

    The deltas a caller displays must concatenate to exactly the text the same
    request would have returned non-streamed. A provider whose stream drops a
    fragment, or splices in thinking text the buffered path filters out, would
    show the user one answer and record another in the conversation history.
    """
    with mock_factory() as mock:
        register(mock, exp.scenario, True)
        model = new()
        deltas: list[str] = []

        if exp.want_kind is not None:
            with pytest.raises(LLMError) as excinfo:
                model.chat_stream(sample_request(), deltas.append)
            assert excinfo.value.kind is exp.want_kind, (
                f"{name}/{exp.scenario}: kind = {excinfo.value.kind}, want {exp.want_kind}"
            )
            return

        resp = model.chat_stream(sample_request(), deltas.append)
        assert resp.finish_reason == exp.want_finish

        # The deltas are the user-visible answer; resp.text is what goes into
        # history. They must be the same text.
        assert "".join(deltas).strip() == resp.text
        assert len(deltas) >= 2, (
            f"{name}: got {len(deltas)} delta(s); a streaming fixture must deliver several, "
            "or this suite would pass against a buffered implementation"
        )


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
def test_consumer_error_is_not_reclassified(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
) -> None:
    """A failure inside the caller's callback must surface as the caller's own
    error, not as a provider fault.

    The REPL relies on this to tell "the terminal write failed" from "Anthropic
    is down".
    """
    with mock_factory() as mock:
        register(mock, SUCCESS, True)
        model = new()

        def refuse(_: str) -> None:
            raise ConsumerRefusedError("the consumer refused a delta")

        with pytest.raises(ConsumerRefusedError):
            model.chat_stream(sample_request(), refuse)


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
@pytest.mark.parametrize(
    ("case_name", "messages"), INVALID_MESSAGE_CASES, ids=[c[0] for c in INVALID_MESSAGE_CASES]
)
def test_message_validation_happens_before_any_request(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
    case_name: str,
    messages: Any,
) -> None:
    """Rejected identically by every adapter, and rejected *before* a request
    goes out.

    Nothing is registered, so the recorder's call count is what proves no
    request was sent.
    """
    with mock_factory() as mock:
        model = new()

        def buffered() -> None:
            model.chat(ChatRequest(messages=messages))

        def streamed() -> None:
            model.chat_stream(ChatRequest(messages=messages), lambda _: None)

        # Both entry points, because an adapter that validates in chat() but
        # not in chat_stream() would pass a suite that only checked one.
        for call in (buffered, streamed):
            with pytest.raises(LLMError) as excinfo:
                call()
            assert excinfo.value.kind is ErrorKind.INVALID_REQUEST
        assert mock.call_count() == 0, (
            f"{name}/{case_name}: a request was sent for a shape that should have been rejected"
        )


# ---------- outbound request shape ----------
#
# What the cutover deliberately does not test. Phase 7 gates on prompt parity
# alone, which covers everything the provider *reads* but nothing about how the
# request is *shaped*.


def test_ollama_puts_the_system_prompt_in_messages_zero() -> None:
    with _requests_mock() as mock:
        _register_ollama(mock, SUCCESS, False)
        _new_ollama().chat(sample_request())
        body = mock.sent()[0]

    assert body["model"] == "llama3.2"
    assert body["messages"][0] == {"role": "system", "content": "You are a chess coach."}
    assert body["messages"][1]["role"] == "user"
    assert body["stream"] is False
    # Options are omitted entirely when the caller set none — including
    # temperature. See test_no_adapter_sends_an_unrequested_temperature.
    assert "options" not in body


def test_anthropic_puts_the_system_prompt_at_the_top_level() -> None:
    with _httpx_mock() as mock:
        _register_anthropic(mock, SUCCESS, False)
        _new_anthropic().chat(sample_request())
        body = mock.sent()[0]

    assert body["model"] == "claude-opus-5"
    # The difference the protocol exists to hide from callers: a top-level
    # parameter here, messages[0] everywhere else.
    assert body["system"] == [{"type": "text", "text": "You are a chess coach."}]
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["max_tokens"] == 16000


def test_openai_sends_max_completion_tokens_and_never_max_tokens() -> None:
    with _httpx_mock() as mock:
        _register_openai(mock, SUCCESS, False)
        _new_openai().chat(sample_request())
        body = mock.sent()[0]

    assert body["model"] == "gpt-5-2025-08-07"
    assert body["messages"][0] == {"role": "system", "content": "You are a chess coach."}
    # max_completion_tokens, not the deprecated max_tokens: the latter is
    # rejected outright by reasoning models, and this is the assertion that
    # catches a well-meaning "simplification" back to it.
    assert body["max_completion_tokens"] == 16000
    assert "max_tokens" not in body


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
def test_no_adapter_sends_an_unrequested_temperature(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
) -> None:
    """The assertion the cutover gate cannot make.

    No call site sets ChatRequest.temperature, so every provider samples at its
    own default. A stray default injected by an adapter would change answer
    distributions invisibly — prompt parity would still pass, because the prompt
    is unchanged.
    """
    with mock_factory() as mock:
        register(mock, SUCCESS, False)
        new().chat(sample_request())
        body = mock.sent()[0]

    assert "temperature" not in body, f"{name} sent a temperature the caller never set"
    assert "temperature" not in body.get("options", {}), (
        f"{name} sent a temperature in options that the caller never set"
    )


@pytest.mark.parametrize(("name", "mock_factory", "new", "register"), PROVIDERS, ids=PROVIDER_IDS)
def test_a_set_temperature_does_reach_the_wire(
    name: str,
    mock_factory: Callable[[], Any],
    new: Callable[[], Any],
    register: Callable[..., None],
) -> None:
    """The other half: omitting it must be about the caller's intent, not about
    the adapter dropping the field.

    0.0 specifically, because it is falsy — a truthiness check instead of an
    `is not None` check would silently drop the one temperature a user is most
    likely to set deliberately.
    """
    with mock_factory() as mock:
        register(mock, SUCCESS, False)
        req = sample_request()
        req.temperature = 0.0
        new().chat(req)
        body = mock.sent()[0]

    sent = body.get("temperature", body.get("options", {}).get("temperature"))
    assert sent == 0.0, f"{name} dropped an explicitly set temperature of 0.0"


# ---------- embeddings ----------


def test_ollama_embed_rejects_an_empty_vector() -> None:
    """The (nil, nil) defect, in its Python shape.

    An Ollama error body parses cleanly into a response with no embedding. The
    old Go client returned that as success and the empty vector reached the
    vector(768) column. An empty vector is an error.
    """
    from zeitnot.llm.ollama import OllamaEmbedder

    with _requests_mock() as mock:
        mock.post(f"{BASE}/api/embeddings", status=200, body=json.dumps({"error": "no such model"}))
        with pytest.raises(LLMError) as excinfo:
            OllamaEmbedder(base_url=BASE).embed(["a summary"])

    assert excinfo.value.kind is ErrorKind.BAD_RESPONSE
    assert "empty embedding" in str(excinfo.value)


def test_ollama_embed_returns_one_vector_per_input_in_order() -> None:
    from zeitnot.llm.ollama import OllamaEmbedder

    with _requests_mock() as mock:
        for value in (1.0, 2.0, 3.0):
            mock.post(f"{BASE}/api/embeddings", status=200, body=json.dumps({"embedding": [value]}))
        vectors = OllamaEmbedder(base_url=BASE).embed(["a", "b", "c"])

    assert vectors == [[1.0], [2.0], [3.0]]


def test_ollama_dimensions_are_known_for_the_default_model_and_unknown_otherwise() -> None:
    from zeitnot.llm.ollama import OllamaEmbedder

    assert OllamaEmbedder(base_url=BASE).dimensions() == 768
    # A tag is stripped before the lookup.
    assert OllamaEmbedder(base_url=BASE, model="nomic-embed-text:latest").dimensions() == 768
    # Unknown means 0, meaning "skip the width check" — never a guess.
    assert OllamaEmbedder(base_url=BASE, model="some-new-model").dimensions() == 0


def test_openai_embed_orders_vectors_by_the_reported_index() -> None:
    """Order by the index the response reports, not by arrival.

    embed's contract is one vector per input *in input order*. A mismatched
    pairing would attach every summary to the wrong vector — silently, and only
    detectable as bad retrieval.
    """
    from zeitnot.llm.openai import OpenAIEmbedder

    with _httpx_mock() as mock:
        mock.post(
            f"{BASE}/embeddings",
            status=200,
            body=json.dumps(
                {
                    "object": "list",
                    "model": "text-embedding-3-small",
                    # Deliberately out of order.
                    "data": [
                        {"object": "embedding", "index": 2, "embedding": [3.0]},
                        {"object": "embedding", "index": 0, "embedding": [1.0]},
                        {"object": "embedding", "index": 1, "embedding": [2.0]},
                    ],
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                }
            ),
        )
        embedder = OpenAIEmbedder(
            api_key="sk-test", base_url=BASE, max_retries=0, http_client=_http_client()
        )
        vectors = embedder.embed(["a", "b", "c"])
        body = mock.sent()[0]

    assert vectors == [[1.0], [2.0], [3.0]]
    # The whole reason a hosted embedder needs no migration: ask for the width
    # the existing vector(768) column declares.
    assert body["dimensions"] == 768


def test_openai_embed_omits_dimensions_for_a_model_that_cannot_truncate() -> None:
    """Sending `dimensions` to ada-002 is a 400, so it is omitted rather than
    sent hopefully."""
    from zeitnot.llm.openai import OpenAIEmbedder

    with _httpx_mock() as mock:
        embedder = OpenAIEmbedder(
            api_key="sk-test",
            base_url=BASE,
            max_retries=0,
            model="text-embedding-ada-002",
            http_client=_http_client(),
        )
        # The model's fixed width, not the configured one.
        assert embedder.dimensions() == 1536
        mock.post(
            f"{BASE}/embeddings",
            status=200,
            body=json.dumps(
                {
                    "object": "list",
                    "model": "text-embedding-ada-002",
                    "data": [{"object": "embedding", "index": 0, "embedding": [1.0]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            ),
        )
        embedder.embed(["a"])
        body = mock.sent()[0]

    assert "dimensions" not in body


def test_openai_embed_rejects_an_empty_input_by_index() -> None:
    from zeitnot.llm.openai import OpenAIEmbedder

    embedder = OpenAIEmbedder(api_key="sk-test", base_url=BASE, max_retries=0)
    with pytest.raises(LLMError) as excinfo:
        embedder.embed(["fine", "   "])
    assert excinfo.value.kind is ErrorKind.INVALID_REQUEST
    # Naming the input is the point: a 400 from the far end would not.
    assert "input 1" in str(excinfo.value)


def test_embed_one_rejects_a_response_of_the_wrong_length() -> None:
    from tests.llmtest import FakeEmbedder
    from zeitnot.llm.base import embed_one

    class Broken(FakeEmbedder):
        def embed(self, texts: Any) -> list[list[float]]:
            return []

    with pytest.raises(LLMError) as excinfo:
        embed_one(Broken(), "a summary")
    assert excinfo.value.kind is ErrorKind.BAD_RESPONSE


# ---------- configuration ----------


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")],
)
def test_a_missing_credential_names_its_environment_variable(provider: str, env_var: str) -> None:
    """The credential check lives in the constructor, which `zeitnot chat` calls
    before its welcome banner. `resolve` itself stays credential-free so
    ingestion — which needs no chat provider — is not blocked by a missing chat
    key."""
    from zeitnot.config import resolve

    cfg = resolve(lambda k: {"CHAT_PROVIDER": provider}.get(k, ""), "")
    with pytest.raises(LLMError) as excinfo:
        cfg.new_chat_model()
    assert excinfo.value.kind is ErrorKind.NOT_CONFIGURED
    assert env_var in str(excinfo.value)


def test_the_embedder_is_unaffected_by_a_missing_chat_credential() -> None:
    from zeitnot.config import resolve

    cfg = resolve(lambda k: {"CHAT_PROVIDER": "anthropic"}.get(k, ""), "")
    embedder = cfg.new_embedder()  # must not raise: it never needed that key
    assert embedder.name() == "ollama"


def test_a_missing_openai_key_fails_for_both_halves() -> None:
    """OpenAI is the one provider whose key both halves need, so a missing key
    must be reported by whichever half is being built."""
    from zeitnot.config import resolve

    cfg = resolve(lambda k: {"CHAT_PROVIDER": "openai", "EMBED_PROVIDER": "openai"}.get(k, ""), "")
    for build in (cfg.new_chat_model, cfg.new_embedder):
        with pytest.raises(LLMError) as excinfo:
            build()
        assert "OPENAI_API_KEY" in str(excinfo.value)


def test_the_fakes_satisfy_the_protocols_they_stand_in_for() -> None:
    """The fakes have to be substitutable for the real adapters, or the tests
    that use them prove nothing about the code that runs."""
    from tests.llmtest import FakeEmbedder, FakeStreamingChatModel
    from zeitnot.llm.base import ChatModel, Embedder, StreamingChatModel

    model = FakeStreamingChatModel()
    assert isinstance(model, ChatModel)
    assert isinstance(model, StreamingChatModel)
    assert isinstance(FakeEmbedder(), Embedder)

    deltas: list[str] = []
    resp: ChatResponse = model.chat_stream(sample_request(), deltas.append)
    assert "".join(deltas) == resp.text
    assert len(deltas) >= 2
