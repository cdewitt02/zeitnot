"""Adapts a local Ollama server to the llm protocols.

The client stays hand-rolled `requests`: the surface is two endpoints with flat
JSON, and the official Python package would pull in a dependency to reach them.

**There is deliberately no retry loop.** Ollama is a local process — 429 does not
happen, a dial failure means it is not running, and a 5xx usually means the model
failed to load. Retrying only delays telling the user that.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import requests

from zeitnot.llm.base import (
    FINISH_LENGTH,
    FINISH_OTHER,
    FINISH_STOP,
    ChatRequest,
    ChatResponse,
    Usage,
    validate_messages,
)
from zeitnot.llm.errors import ErrorKind, LLMError, classify_status
from zeitnot.llm.providers import (
    OLLAMA,
    OLLAMA_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_CHAT_MODEL,
    OLLAMA_DEFAULT_EMBED_MODEL,
)

PROVIDER_NAME = OLLAMA

# A cold model can take well over the 10s the old client allowed.
_DEFAULT_EMBED_TIMEOUT = 60.0
_DEFAULT_CHAT_TIMEOUT = 120.0

# Lets startup verify the vector(N) column without a probe request. A model that
# is absent reports 0, meaning "unknown", and the check is skipped rather than
# guessed.
_KNOWN_DIMENSIONS = {
    "nomic-embed-text": 768,
    "all-minilm": 384,
    "mxbai-embed-large": 1024,
    "bge-m3": 1024,
    "snowflake-arctic-embed": 1024,
    "snowflake-arctic-embed2": 1024,
}


def _base_model_name(model: str) -> str:
    """Strip an Ollama tag ("nomic-embed-text:latest")."""
    return model.split(":", 1)[0]


def _snippet(body: str) -> str:
    body = body.strip()
    return body[:300] + "..." if len(body) > 300 else body


class _Client:
    """The shared HTTP plumbing. One pooled session per adapter."""

    def __init__(
        self,
        base_url: str,
        model: str,
        default_model: str,
        timeout: float,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        self.model = model or default_model
        self.timeout = timeout
        # A Session rather than module-level requests calls: connection reuse
        # matters when ingestion embeds a few thousand summaries in a row.
        self._session = session or requests.Session()

    def name(self) -> str:
        return PROVIDER_NAME

    def _post(self, op: str, path: str, body: Any, stream: bool) -> requests.Response:
        try:
            response = self._session.post(
                self.base_url + path,
                json=body,
                timeout=self.timeout,
                stream=stream,
                headers={"Content-Type": "application/json"},
            )
        except requests.RequestException as err:
            raise LLMError(
                PROVIDER_NAME,
                op,
                ErrorKind.UNAVAILABLE,
                f"{err} (is Ollama running at {self.base_url}?)",
                err,
            ) from err

        if not 200 <= response.status_code < 300:
            body_text = _snippet(response.text)
            if response.status_code == 404:
                raise LLMError(
                    PROVIDER_NAME,
                    op,
                    ErrorKind.MODEL_NOT_FOUND,
                    f"status 404: {body_text} (try: ollama pull {self.model})",
                )
            raise LLMError(
                PROVIDER_NAME,
                op,
                classify_status(response.status_code),
                f"status {response.status_code}: {body_text}",
            )
        return response

    def post_json(self, op: str, path: str, body: Any) -> dict[str, Any]:
        response = self._post(op, path, body, stream=False)
        try:
            parsed = response.json()
        except ValueError as err:
            raise LLMError(
                PROVIDER_NAME, op, ErrorKind.BAD_RESPONSE, f"{err}: {_snippet(response.text)}", err
            ) from err
        if not isinstance(parsed, dict):
            raise LLMError(
                PROVIDER_NAME, op, ErrorKind.BAD_RESPONSE, f"expected an object, got {type(parsed)}"
            )
        return parsed

    def post_stream(self, op: str, path: str, body: Any) -> Iterator[dict[str, Any]]:
        """Yield each newline-delimited JSON object as it arrives.

        Ollama streams NDJSON rather than SSE, so this iterates lines instead of
        parsing events. Failures map onto the same kinds `post_json` uses: a
        stream that fails must be indistinguishable, to the caller, from a
        non-streaming call that failed the same way.
        """
        response = self._post(op, path, body, stream=True)
        try:
            for raw in response.iter_lines():
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except ValueError as err:
                    raise LLMError(
                        PROVIDER_NAME,
                        op,
                        ErrorKind.BAD_RESPONSE,
                        f"{err}: {_snippet(raw.decode('utf-8', 'replace'))}",
                        err,
                    ) from err
                if isinstance(parsed, dict):
                    yield parsed
        except requests.RequestException as err:
            # A stream that stops mid-response is a transport failure, not a bad
            # body: nothing about the JSON already received was malformed.
            raise LLMError(PROVIDER_NAME, op, ErrorKind.UNAVAILABLE, str(err), err) from err
        finally:
            response.close()

    def preflight(self, op: str) -> None:
        """Check that Ollama is reachable and the configured model is pulled.

        "Model not pulled" is a top setup failure, so it is worth catching
        before the first question rather than after.
        """
        try:
            response = self._session.get(self.base_url + "/api/tags", timeout=self.timeout)
        except requests.RequestException as err:
            # A dial failure against a local process is definitive, not
            # inconclusive.
            raise LLMError(
                PROVIDER_NAME,
                op,
                ErrorKind.UNAVAILABLE,
                f"cannot reach Ollama at {self.base_url}: {err} (is it running?)",
                err,
            ) from err

        if response.status_code != 200:
            raise LLMError(
                PROVIDER_NAME,
                op,
                ErrorKind.PREFLIGHT_INCONCLUSIVE,
                f"/api/tags returned status {response.status_code}",
            )
        try:
            tags = response.json()
        except ValueError as err:
            raise LLMError(
                PROVIDER_NAME, op, ErrorKind.PREFLIGHT_INCONCLUSIVE, str(err), err
            ) from err

        for entry in (tags or {}).get("models", []):
            for candidate in (entry.get("name", ""), entry.get("model", "")):
                if candidate == self.model or _base_model_name(candidate) == _base_model_name(
                    self.model
                ):
                    return
        raise LLMError(
            PROVIDER_NAME,
            op,
            ErrorKind.MODEL_NOT_FOUND,
            f"model {self.model!r} is not pulled; run: ollama pull {self.model}",
        )


# ---------- Chat ----------


class OllamaChatModel:
    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        timeout: float = _DEFAULT_CHAT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._client = _Client(
            base_url, model, OLLAMA_DEFAULT_CHAT_MODEL, timeout or _DEFAULT_CHAT_TIMEOUT, session
        )

    def name(self) -> str:
        return PROVIDER_NAME

    def chat(self, req: ChatRequest) -> ChatResponse:
        body, model = self._build_request(req, stream=False)
        out = self._client.post_json("chat", "/api/chat", body)
        return _build_response(out, model, out.get("message", {}).get("content", ""))

    def chat_stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResponse:
        """The same request with Ollama's stream flag set.

        Ollama streams partial messages and then a final object carrying the
        stats and done_reason, so the deltas are concatenated here rather than
        trusting any single object to hold the whole answer.
        """
        body, model = self._build_request(req, stream=True)

        parts: list[str] = []
        last: dict[str, Any] = {}
        for obj in self._client.post_stream("chat", "/api/chat", body):
            # Ollama reports mid-stream errors in the body of a 200 response, so
            # this is the only place they can be caught.
            if obj.get("error"):
                raise LLMError(PROVIDER_NAME, "chat", ErrorKind.BAD_RESPONSE, str(obj["error"]))
            last = obj
            content = obj.get("message", {}).get("content", "")
            if not content:
                continue
            parts.append(content)
            # A consumer failure is the consumer's error, not the provider's, so
            # it propagates untouched rather than being classified.
            on_delta(content)

        return _build_response(last, model, "".join(parts))

    def _build_request(self, req: ChatRequest, stream: bool) -> tuple[dict[str, Any], str]:
        validate_messages(req.messages)

        model = req.model or self._client.model

        # Ollama takes the system prompt as messages[0]; lifting it out of the
        # caller's sequence is this adapter's job, not the caller's.
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)

        body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}

        # Options are omitted entirely when the caller set none. In particular a
        # temperature that was never asked for must not be sent.
        options: dict[str, Any] = {}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if req.max_tokens > 0:
            options["num_predict"] = req.max_tokens
        if req.stop_after:
            options["stop"] = list(req.stop_after)
        if options:
            body["options"] = options

        return body, model

    def preflight(self) -> None:
        self._client.preflight("chat")


def _build_response(out: dict[str, Any], model: str, text: str) -> ChatResponse:
    """Normalize a completed exchange.

    `text` is passed separately because a streamed answer is assembled from many
    objects while `out` holds only the last one.
    """
    text = text.strip()
    if not text:
        # Empty content is an error, not a short answer.
        raise LLMError(
            PROVIDER_NAME, "chat", ErrorKind.BAD_RESPONSE, f"model {model} returned no content"
        )

    return ChatResponse(
        text=text,
        model=out.get("model") or model,
        finish_reason=_normalize_done_reason(out.get("done_reason", "")),
        usage=Usage(
            input_tokens=int(out.get("prompt_eval_count", 0) or 0),
            output_tokens=int(out.get("eval_count", 0) or 0),
        ),
    )


def _normalize_done_reason(reason: str) -> str:
    if reason in ("stop", ""):
        return FINISH_STOP
    if reason == "length":
        return FINISH_LENGTH
    return FINISH_OTHER


# ---------- Embeddings ----------


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        timeout: float = _DEFAULT_EMBED_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._client = _Client(
            base_url, model, OLLAMA_DEFAULT_EMBED_MODEL, timeout or _DEFAULT_EMBED_TIMEOUT, session
        )

    def name(self) -> str:
        return PROVIDER_NAME

    def model(self) -> str:
        return self._client.model

    def dimensions(self) -> int:
        return _KNOWN_DIMENSIONS.get(_base_model_name(self._client.model), 0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One request per text: /api/embeddings takes a single prompt."""
        out: list[list[float]] = []
        for i, text in enumerate(texts):
            response = self._client.post_json(
                "embed", "/api/embeddings", {"model": self._client.model, "prompt": text}
            )
            vector = response.get("embedding") or []
            if not vector:
                # The old Go client returned (nil, nil) here: an Ollama error
                # body unmarshals cleanly into an empty embedding, and the nil
                # vector reached the vector(768) column. An empty vector is an
                # error.
                raise LLMError(
                    PROVIDER_NAME,
                    "embed",
                    ErrorKind.BAD_RESPONSE,
                    f"model {self._client.model} returned an empty embedding for input {i}",
                )
            out.append([float(v) for v in vector])
        return out

    def preflight(self) -> None:
        self._client.preflight("embed")
