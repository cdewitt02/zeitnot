"""Adapts the OpenAI Chat Completions and Embeddings APIs to the llm protocols.

This is the only adapter that implements both halves of the split — chat and
embeddings — which is what makes an Ollama-free setup possible at all.

**Retry ownership sits with the SDK.** It retries 408/409/429/5xx and connection
errors internally; wrapping that in an adapter-level loop would turn three
attempts into nine against an endpoint that just said 429. This adapter maps the
SDK's final error onto the llm kinds and does nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import openai

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
    DEFAULT_MAX_TOKENS,
    MAX_RETRIES,
    OPENAI,
    OPENAI_API_KEY_ENV,
    OPENAI_DEFAULT_CHAT_MODEL,
    OPENAI_DEFAULT_EMBED_DIMENSIONS,
    OPENAI_DEFAULT_EMBED_MODEL,
)

PROVIDER_NAME = OPENAI

# Caps inputs per embeddings request. The API allows 2048 array entries; this
# stays well under it, and under the 300k-token request cap that a large batch
# of game summaries would otherwise approach.
_MAX_BATCH = 128

# Embedding models that cannot truncate, so the width is fixed by the model
# rather than by configuration. A model that is absent reports 0 from
# dimensions(), meaning "unknown", and the startup width check is skipped rather
# than guessed.
_NATIVE_DIMENSIONS = {"text-embedding-ada-002": 1536}


def _new_client(
    api_key: str,
    base_url: str,
    max_retries: int | None,
    client: openai.OpenAI | None,
    http_client: Any = None,
) -> openai.OpenAI:
    if client is not None:
        return client
    if not api_key.strip():
        raise LLMError(
            PROVIDER_NAME,
            "configure",
            ErrorKind.NOT_CONFIGURED,
            f"provider {PROVIDER_NAME} requires {OPENAI_API_KEY_ENV}",
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
    return openai.OpenAI(**kwargs)


def _supports_dimensions(model: str) -> bool:
    """Whether the model accepts the `dimensions` parameter.

    Sending it to a model that does not — ada-002 — is a 400, so it is omitted
    rather than sent hopefully.
    """
    return model.startswith("text-embedding-3")


# ---------- Chat ----------


class OpenAIChatModel:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 0,
        base_url: str = "",
        max_retries: int | None = None,
        client: openai.OpenAI | None = None,
        http_client: Any = None,
    ) -> None:
        self._client = _new_client(api_key, base_url, max_retries, client, http_client)
        self._model = model or OPENAI_DEFAULT_CHAT_MODEL
        self._max_tokens = max_tokens if max_tokens > 0 else DEFAULT_MAX_TOKENS

    def name(self) -> str:
        return PROVIDER_NAME

    def preflight(self) -> None:
        _preflight(self._client, "chat", self._model)

    def chat(self, req: ChatRequest) -> ChatResponse:
        params, model = self._build_params(req)
        try:
            completion = self._client.chat.completions.create(**params)
        except Exception as err:
            raise _classify("chat", err) from err
        return _build_response(completion, model)

    def chat_stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResponse:
        """The same request, delivered incrementally. on_delta receives content
        deltas only.

        The accumulated completion is reassembled into the identical shape
        `chat` receives, so both paths share `_build_response` and cannot drift
        on refusal handling, truncation, or usage accounting.
        """
        params, model = self._build_params(req)
        params["stream"] = True
        # Usage arrives only in a final extra chunk, and only when asked for.
        # Without this the streamed path would report zero tokens while the
        # buffered path reported real ones.
        params["stream_options"] = {"include_usage": True}

        parts: list[str] = []
        finish_reason = ""
        refusal = ""
        response_model = ""
        usage = Usage()

        try:
            stream = self._client.chat.completions.create(**params)
            for chunk in stream:
                if getattr(chunk, "model", ""):
                    response_model = chunk.model
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = Usage(
                        input_tokens=int(getattr(chunk_usage, "prompt_tokens", 0) or 0),
                        output_tokens=int(getattr(chunk_usage, "completion_tokens", 0) or 0),
                    )
                if not chunk.choices:
                    # The usage-only chunk that follows the last content chunk.
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if getattr(delta, "refusal", None):
                    refusal = delta.refusal
                content = getattr(delta, "content", None)
                if not content:
                    continue
                deliver(on_delta, content)
                parts.append(content)
        except ConsumerError as err:
            # The caller's own failure, handed back exactly as they raised it.
            raise err.cause from None
        except LLMError:
            raise
        except Exception as err:
            raise _classify("chat", err) from err

        return _finish(
            text="".join(parts),
            refusal=refusal,
            finish_reason=finish_reason,
            response_model=response_model,
            model=model,
            usage=usage,
            had_choices=True,
        )

    def _build_params(self, req: ChatRequest) -> tuple[dict[str, Any], str]:
        # OpenAI is lenient about message ordering where Anthropic is not.
        # Validating identically here is what keeps a malformed conversation
        # from being rejected by one provider and silently accepted by another.
        validate_messages(req.messages)

        model = req.model or self._model
        max_tokens = req.max_tokens if req.max_tokens > 0 else self._max_tokens

        # OpenAI takes the system prompt as messages[0]; lifting it out of the
        # caller's sequence is this adapter's job, not the caller's.
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend(
            {
                "role": ROLE_ASSISTANT if m.role == ROLE_ASSISTANT else "user",
                "content": m.content,
            }
            for m in req.messages
        )

        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            # max_completion_tokens, not the deprecated max_tokens: the latter
            # is rejected outright by reasoning models.
            "max_completion_tokens": max_tokens,
        }
        if req.temperature is not None:
            params["temperature"] = req.temperature
        if req.stop_after:
            params["stop"] = list(req.stop_after)

        return params, model


def _build_response(completion: Any, model: str) -> ChatResponse:
    if not completion.choices:
        return _finish("", "", "", "", model, Usage(), had_choices=False)
    # n defaults to 1 and this adapter never raises it, so there is exactly one
    # choice to read.
    choice = completion.choices[0]
    usage = getattr(completion, "usage", None)
    return _finish(
        # Reasoning tokens never appear in content on this API — the model's
        # thinking is billed in usage.completion_tokens_details and not
        # returned — so unlike the Anthropic adapter there are no thinking
        # blocks to filter.
        text=choice.message.content or "",
        refusal=getattr(choice.message, "refusal", None) or "",
        finish_reason=choice.finish_reason or "",
        response_model=getattr(completion, "model", "") or "",
        model=model,
        usage=Usage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        ),
        had_choices=True,
    )


def _finish(
    text: str,
    refusal: str,
    finish_reason: str,
    response_model: str,
    model: str,
    usage: Usage,
    had_choices: bool,
) -> ChatResponse:
    """The single place both paths turn a completed exchange into a response."""
    if not had_choices:
        raise LLMError(
            PROVIDER_NAME, "chat", ErrorKind.BAD_RESPONSE, f"model {model} returned no choices"
        )
    if refusal:
        raise LLMError(
            PROVIDER_NAME,
            "chat",
            ErrorKind.BAD_RESPONSE,
            f"model {model} declined the request: {refusal}",
        )
    text = text.strip()
    if not text:
        raise LLMError(
            PROVIDER_NAME,
            "chat",
            ErrorKind.BAD_RESPONSE,
            f"model {model} returned no text content (finish_reason={finish_reason})",
        )
    return ChatResponse(
        text=text,
        model=response_model or model,
        finish_reason=_normalize_finish_reason(finish_reason),
        usage=usage,
    )


def _normalize_finish_reason(reason: str) -> str:
    # An absent reason on an otherwise complete response is a normal stop;
    # OpenAI-compatible gateways are the ones that omit it.
    if reason in ("stop", ""):
        return FINISH_STOP
    if reason == "length":
        return FINISH_LENGTH
    if reason == "content_filter":
        return FINISH_CONTENT_FILTER
    return FINISH_OTHER


# ---------- Embeddings ----------


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        dimensions: int = 0,
        base_url: str = "",
        max_retries: int | None = None,
        client: openai.OpenAI | None = None,
        http_client: Any = None,
    ) -> None:
        self._client = _new_client(api_key, base_url, max_retries, client, http_client)
        self._model = model or OPENAI_DEFAULT_EMBED_MODEL
        self._dims = dimensions if dimensions > 0 else OPENAI_DEFAULT_EMBED_DIMENSIONS

    def name(self) -> str:
        return PROVIDER_NAME

    def model(self) -> str:
        return self._model

    def dimensions(self) -> int:
        """The width this embedder will actually produce.

        The configured width for models that can truncate, the model's fixed
        width for those that cannot, and 0 — "unknown", check skipped — for a
        model this adapter has no fact about.
        """
        if _supports_dimensions(self._model):
            return self._dims
        return _NATIVE_DIMENSIONS.get(self._model, 0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch natively: unlike Ollama's one-prompt-per-request endpoint, a
        single call carries many inputs. Inputs are chunked so a large batch
        cannot exceed the API's array limit.
        """
        if not texts:
            return []
        for i, text in enumerate(texts):
            # The API rejects an empty string. Catching it here names the input,
            # which a 400 from the far end would not.
            if not text.strip():
                raise LLMError(
                    PROVIDER_NAME,
                    "embed",
                    ErrorKind.INVALID_REQUEST,
                    f"input {i} is empty; embedding an empty string is not possible",
                )

        out: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = list(texts[start : start + _MAX_BATCH])

            params: dict[str, Any] = {"model": self._model, "input": batch}
            if _supports_dimensions(self._model):
                # The whole reason a hosted embedder needs no migration: ask for
                # the width the existing vector(768) column declares.
                params["dimensions"] = self._dims

            try:
                response = self._client.embeddings.create(**params)
            except Exception as err:
                raise _classify("embed", err) from err

            if len(response.data) != len(batch):
                raise LLMError(
                    PROVIDER_NAME,
                    "embed",
                    ErrorKind.BAD_RESPONSE,
                    f"asked for {len(batch)} embeddings, got {len(response.data)}",
                )

            # Order the vectors by the index the response reports rather than by
            # arrival. embed's contract is one vector per input *in input
            # order*, and a mismatched pairing would attach every summary to the
            # wrong vector — silently, and only detectable as bad retrieval.
            vectors: list[list[float] | None] = [None] * len(batch)
            for item in response.data:
                index = int(item.index)
                if index < 0 or index >= len(batch):
                    raise LLMError(
                        PROVIDER_NAME,
                        "embed",
                        ErrorKind.BAD_RESPONSE,
                        f"response index {index} is outside the batch of {len(batch)}",
                    )
                if not item.embedding:
                    raise LLMError(
                        PROVIDER_NAME,
                        "embed",
                        ErrorKind.BAD_RESPONSE,
                        f"model {self._model} returned an empty embedding "
                        f"for input {start + index}",
                    )
                if vectors[index] is not None:
                    raise LLMError(
                        PROVIDER_NAME,
                        "embed",
                        ErrorKind.BAD_RESPONSE,
                        f"response repeated index {index}",
                    )
                vectors[index] = [float(v) for v in item.embedding]
            out.extend(v for v in vectors if v is not None)
        return out

    def preflight(self) -> None:
        _preflight(self._client, "embed", self._model)


def _classify(op: str, err: BaseException) -> LLMError:
    """Map an SDK error, after its retries are exhausted, onto a kind."""
    if isinstance(err, LLMError):
        return err
    if isinstance(err, json.JSONDecodeError):
        return LLMError(PROVIDER_NAME, op, ErrorKind.BAD_RESPONSE, str(err), err)

    status = getattr(err, "status_code", None)
    if isinstance(err, openai.APIStatusError) or isinstance(status, int):
        status_code = int(status) if isinstance(status, int) else 500
        kind = classify_status(status_code)
        if status_code == 400:
            # OpenAI reports an oversized prompt as a 400 with a specific code,
            # which is a different remedy from a malformed request.
            lower = str(err).lower()
            if "context_length_exceeded" in lower or "context length" in lower:
                return LLMError(
                    PROVIDER_NAME,
                    op,
                    ErrorKind.CONTEXT_LENGTH,
                    f"{err}: lower DetailLimit or /clear the conversation",
                    err,
                )
        if status_code in (401, 403):
            return LLMError(PROVIDER_NAME, op, kind, f"{err} (check {OPENAI_API_KEY_ENV})", err)
        return LLMError(PROVIDER_NAME, op, kind, str(err), err)

    return LLMError(PROVIDER_NAME, op, ErrorKind.UNAVAILABLE, str(err), err)


def _hint(model: str) -> str:
    """Enrich — never gate — the failure the live check already established."""
    lower = model.lower()
    for marker in ("llama", "mistral", "qwen", "gemma", "phi", "deepseek", "nomic"):
        if marker in lower:
            return (
                " That looks like an Ollama model — did you mean CHAT_PROVIDER=ollama?"
                " Note the chat model can be passed positionally, which outranks CHAT_MODEL."
            )
    if lower.startswith("claude"):
        return " That looks like an Anthropic model — did you mean CHAT_PROVIDER=anthropic?"
    return ""


def _preflight(client: openai.OpenAI, op: str, model: str) -> None:
    """Verify credentials and that the configured model exists, before the
    welcome banner rather than at the first question.

    A models-list call that fails for a non-auth reason warns and continues: the
    endpoint is frequently absent behind an OpenAI-compatible gateway (LiteLLM,
    OpenRouter, a local vLLM) reached via base_url, and blocking startup over an
    auxiliary call would break valid setups. 401/403 is the credential check,
    and it is fatal.
    """
    ids: list[str] = []
    try:
        for info in client.models.list():
            if info.id == model:
                return
            ids.append(info.id)
    except Exception as err:
        status = getattr(err, "status_code", None)
        if status in (401, 403):
            raise LLMError(
                PROVIDER_NAME,
                "preflight",
                ErrorKind.UNAUTHORIZED,
                f"{err} (check {OPENAI_API_KEY_ENV})",
                err,
            ) from err
        raise LLMError(
            PROVIDER_NAME, "preflight", ErrorKind.PREFLIGHT_INCONCLUSIVE, str(err), err
        ) from err

    if not ids:
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
        f"{model!r} is not available on OpenAI ({op}).{_hint(model)} "
        f"Available models include: {', '.join(ids[:5])}",
    )
