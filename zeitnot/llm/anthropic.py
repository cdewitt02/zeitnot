"""Adapts the Anthropic Messages API to the ChatModel protocol.

There is deliberately no embedder here: Anthropic offers no embeddings API, so
this module implements exactly one of the two protocols.

**Retry ownership sits with the SDK.** It retries 408/409/429/5xx and connection
errors internally; wrapping that in an adapter-level loop would turn three
attempts into nine against an endpoint that just said 429. This adapter maps the
SDK's final error onto the llm kinds and does nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic

from zeitnot.llm.base import (
    FINISH_CONTENT_FILTER,
    FINISH_LENGTH,
    FINISH_OTHER,
    FINISH_STOP,
    ROLE_ASSISTANT,
    ChatRequest,
    ChatResponse,
    Usage,
    validate_messages,
)
from zeitnot.llm.errors import ConsumerError, ErrorKind, LLMError, classify_status, deliver
from zeitnot.llm.providers import (
    ANTHROPIC,
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_DEFAULT_MODEL,
    DEFAULT_MAX_TOKENS,
    MAX_RETRIES,
)

PROVIDER_NAME = ANTHROPIC


class AnthropicChatModel:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 0,
        base_url: str = "",
        max_retries: int | None = None,
        client: anthropic.Anthropic | None = None,
        http_client: Any = None,
    ) -> None:
        if client is None:
            if not api_key.strip():
                raise LLMError(
                    PROVIDER_NAME,
                    "configure",
                    ErrorKind.NOT_CONFIGURED,
                    f"CHAT_PROVIDER={PROVIDER_NAME} requires {ANTHROPIC_API_KEY_ENV}",
                )
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "max_retries": MAX_RETRIES if max_retries is None else max_retries,
            }
            if base_url:
                kwargs["base_url"] = base_url
            if http_client is not None:
                # Tests inject an httpx.MockTransport here, which is the direct
                # analogue of the Go suite pointing option.WithHTTPClient at an
                # httptest.Server.
                kwargs["http_client"] = http_client
            client = anthropic.Anthropic(**kwargs)

        self._client = client
        self._model = model or ANTHROPIC_DEFAULT_MODEL
        self._max_tokens = max_tokens if max_tokens > 0 else DEFAULT_MAX_TOKENS

    def name(self) -> str:
        return PROVIDER_NAME

    def chat(self, req: ChatRequest) -> ChatResponse:
        params, model = self._build_params(req)
        try:
            message = self._client.messages.create(**params)
        except Exception as err:
            raise _classify(err) from err
        return _build_response(message, model)

    def chat_stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResponse:
        """The same request, delivered incrementally.

        on_delta receives text fragments only: thinking and tool-use deltas are
        skipped, for the same reason `chat` skips those blocks.

        The accumulated message is the identical shape `chat` receives, so both
        paths share `_build_response` and cannot drift on refusal handling,
        truncation, or usage accounting.
        """
        params, model = self._build_params(req)
        try:
            with self._client.messages.stream(**params) as stream:
                for text in stream.text_stream:
                    if not text:
                        continue
                    deliver(on_delta, text)
                final = stream.get_final_message()
        except AssertionError as err:
            # The SDK's signal that nothing ever accumulated: the response body
            # was not a parseable event stream. Bare AssertionError is a poor
            # contract to depend on, but the alternative is letting it fall
            # through to _classify and be reported as "provider unavailable",
            # which sends the user to check their network over a malformed
            # body. It is caught here, where its meaning is unambiguous, rather
            # than in _classify, where it is not.
            raise LLMError(
                PROVIDER_NAME,
                "chat",
                ErrorKind.BAD_RESPONSE,
                "the response was not a parseable event stream",
                err,
            ) from err
        except ConsumerError as err:
            # The caller's own failure, handed back exactly as they raised it.
            raise err.cause from None
        except LLMError:
            raise
        except Exception as err:
            raise _classify(err) from err
        return _build_response(final, model)

    def preflight(self) -> None:
        """Verify credentials and that the configured model exists, before the
        welcome banner rather than at the first question.

        A models-list call that fails for a non-auth reason warns and continues:
        the endpoint may be absent behind an Anthropic-compatible gateway, and
        blocking startup over an auxiliary call would break valid setups.
        401/403 is the credential check, and it is fatal.
        """
        ids: list[str] = []
        try:
            for info in self._client.models.list(limit=100):
                if info.id == self._model:
                    return
                ids.append(info.id)
        except Exception as err:
            status = getattr(err, "status_code", None)
            if status in (401, 403):
                raise LLMError(
                    PROVIDER_NAME,
                    "preflight",
                    ErrorKind.UNAUTHORIZED,
                    f"{err} (check {ANTHROPIC_API_KEY_ENV})",
                    err,
                ) from err
            raise LLMError(
                PROVIDER_NAME, "preflight", ErrorKind.PREFLIGHT_INCONCLUSIVE, str(err), err
            ) from err

        if not ids:
            # A successful but empty listing tells us nothing.
            raise LLMError(
                PROVIDER_NAME,
                "preflight",
                ErrorKind.PREFLIGHT_INCONCLUSIVE,
                "models list returned no models",
            )

        raise LLMError(
            PROVIDER_NAME,
            "preflight",
            ErrorKind.MODEL_NOT_FOUND,
            f"{self._model!r} is not available on Anthropic.{_hint(self._model)} "
            f"Available models include: {', '.join(ids[:5])}",
        )

    def _build_params(self, req: ChatRequest) -> tuple[dict[str, Any], str]:
        # Anthropic requires messages to begin with a user turn and to
        # alternate. Today's history assembly satisfies that by accident; here
        # it becomes guaranteed, with a clear error instead of a passed-through
        # 400.
        validate_messages(req.messages)

        model = req.model or self._model
        max_tokens = req.max_tokens if req.max_tokens > 0 else self._max_tokens

        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": ROLE_ASSISTANT if m.role == ROLE_ASSISTANT else "user",
                    "content": [{"type": "text", "text": m.content}],
                }
                for m in req.messages
            ],
        }
        # The system prompt is a top-level parameter, never a message. This is
        # the difference the protocol exists to hide from callers.
        if req.system:
            params["system"] = [{"type": "text", "text": req.system}]
        if req.temperature is not None:
            # Not a named parameter any more. Current Anthropic models removed
            # the sampling knobs outright — `messages.create` has no
            # `temperature` argument, and the API answers 400 if one arrives.
            # It is still forwarded rather than dropped, because dropping a
            # value the caller explicitly set would silently sample at a
            # different setting than they asked for; a 400 says so out loud.
            # No call site sets it today.
            params["extra_body"] = {"temperature": req.temperature}
        if req.stop_after:
            params["stop_sequences"] = list(req.stop_after)

        return params, model


def _build_response(message: Any, model: str) -> ChatResponse:
    """Normalize a completed message, however it arrived."""
    # content is an array of typed blocks. Concatenate the text ones and skip
    # everything else — notably thinking blocks, which a naive read would either
    # drop the answer for or splice reasoning into it.
    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    ).strip()

    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        raise LLMError(
            PROVIDER_NAME, "chat", ErrorKind.BAD_RESPONSE, f"model {model} declined the request"
        )
    if stop_reason == "model_context_window_exceeded":
        raise LLMError(
            PROVIDER_NAME,
            "chat",
            ErrorKind.CONTEXT_LENGTH,
            f"prompt exceeds the context window of {model}; "
            "lower DetailLimit or /clear the conversation",
        )
    if not text:
        raise LLMError(
            PROVIDER_NAME,
            "chat",
            ErrorKind.BAD_RESPONSE,
            f"model {model} returned no text content (stop_reason={stop_reason})",
        )

    usage = getattr(message, "usage", None)
    return ChatResponse(
        text=text,
        model=str(getattr(message, "model", "") or model),
        finish_reason=_normalize_stop_reason(stop_reason),
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        ),
    )


def _normalize_stop_reason(reason: str | None) -> str:
    if reason in ("end_turn", "stop_sequence"):
        return FINISH_STOP
    if reason == "max_tokens":
        return FINISH_LENGTH
    if reason == "refusal":
        return FINISH_CONTENT_FILTER
    return FINISH_OTHER


def _classify(err: BaseException) -> LLMError:
    """Map an SDK error, after its retries are exhausted, onto a kind."""
    if isinstance(err, LLMError):
        return err

    # A body the SDK could not decode is deterministic — no retry, and it is a
    # bad response rather than an unavailable provider.
    if isinstance(err, json.JSONDecodeError):
        return LLMError(PROVIDER_NAME, "chat", ErrorKind.BAD_RESPONSE, str(err), err)

    status = getattr(err, "status_code", None)
    if isinstance(err, anthropic.APIStatusError) or isinstance(status, int):
        status_code = int(status) if isinstance(status, int) else 500
        kind = classify_status(status_code)
        if status_code == 400 and "context" in str(err).lower():
            kind = ErrorKind.CONTEXT_LENGTH
        if status_code in (401, 403):
            return LLMError(
                PROVIDER_NAME, "chat", kind, f"{err} (check {ANTHROPIC_API_KEY_ENV})", err
            )
        return LLMError(PROVIDER_NAME, "chat", kind, str(err), err)

    return LLMError(PROVIDER_NAME, "chat", ErrorKind.UNAVAILABLE, str(err), err)


def _hint(model: str) -> str:
    """Enrich — never gate — the failure the live check already established.

    The common footgun is copying the README's positional model argument (an
    Ollama model name) while CHAT_PROVIDER=anthropic.
    """
    lower = model.lower()
    for marker in ("llama", "mistral", "qwen", "gemma", "phi", "deepseek", "nomic"):
        if marker in lower:
            return (
                " That looks like an Ollama model — did you mean CHAT_PROVIDER=ollama?"
                " Note the chat model can be passed positionally, which outranks CHAT_MODEL."
            )
    return ""
