"""Resolves provider selection from the environment.

Both entrypoints — `zeitnot chat` and `zeitnot data` — resolve through here, so
they cannot drift into the split-brain where chat runs on one provider and
ingestion silently runs on another.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TextIO
from urllib.parse import urlsplit

from zeitnot.llm.base import Embedder, Preflighter
from zeitnot.llm.errors import ErrorKind, LLMError
from zeitnot.llm.providers import (
    ANTHROPIC,
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_DEFAULT_MODEL,
    CHAT_PROVIDERS,
    EMBED_PROVIDERS,
    OLLAMA,
    OLLAMA_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_CHAT_MODEL,
    OLLAMA_DEFAULT_EMBED_MODEL,
    OPENAI,
    OPENAI_API_KEY_ENV,
    OPENAI_DEFAULT_CHAT_MODEL,
    OPENAI_DEFAULT_EMBED_MODEL,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from zeitnot.db import DB
    from zeitnot.llm.base import ChatModel

# Env reads one environment variable. Tests pass a dict lookup.
Env = Callable[[str], str]


def os_env(key: str) -> str:
    """Read the process environment."""
    return os.environ.get(key, "")


class ConfigError(Exception):
    """An unusable provider selection. Raised by `resolve`, never later."""


@dataclass(slots=True)
class Config:
    """The resolved provider selection.

    Both defaults are ollama, so an existing setup with only DATABASE_URL and
    OLLAMA_URL behaves identically and the tool never starts spending money
    because a default moved.
    """

    chat_provider: str
    chat_model: str
    embed_provider: str
    embed_model: str
    ollama_url: str

    # API keys are never printed, only passed to the adapter that needs them.
    _anthropic_api_key: str = field(default="", repr=False)
    _openai_api_key: str = field(default="", repr=False)

    def uses_hosted_provider(self) -> bool:
        """Whether any selected provider sends data off-machine."""
        return self.chat_provider != OLLAMA or self.embed_provider != OLLAMA

    def new_chat_model(self) -> ChatModel:
        # Imported here rather than at module scope so `resolve` — and its
        # error messages about unknown providers — never depend on a vendor SDK
        # being importable.
        if self.chat_provider == OLLAMA:
            from zeitnot.llm.ollama import OllamaChatModel

            return OllamaChatModel(base_url=self.ollama_url, model=self.chat_model)
        if self.chat_provider == ANTHROPIC:
            from zeitnot.llm.anthropic import AnthropicChatModel

            return AnthropicChatModel(api_key=self._anthropic_api_key, model=self.chat_model)
        if self.chat_provider == OPENAI:
            from zeitnot.llm.openai import OpenAIChatModel

            return OpenAIChatModel(api_key=self._openai_api_key, model=self.chat_model)
        raise ConfigError(f"unknown chat provider {self.chat_provider!r}")

    def new_embedder(self) -> Embedder:
        if self.embed_provider == OLLAMA:
            from zeitnot.llm.ollama import OllamaEmbedder

            return OllamaEmbedder(base_url=self.ollama_url, model=self.embed_model)
        if self.embed_provider == OPENAI:
            # dimensions is left at its default, which is the width the
            # game_summaries column already declares. An index built by another
            # embedder is caught by check_index, not here.
            from zeitnot.llm.openai import OpenAIEmbedder

            return OpenAIEmbedder(api_key=self._openai_api_key, model=self.embed_model)
        raise ConfigError(f"unknown embed provider {self.embed_provider!r}")

    def summary(self) -> str:
        """The resolved configuration, printed at startup so a user who set only
        CHAT_PROVIDER can see that embeddings stayed local."""
        lines = [
            f"Chat:       {self.chat_provider} / {self.chat_model}",
            f"Embeddings: {self.embed_provider} / {self.embed_model}",
        ]
        text = "\n".join(lines)
        if self.uses_hosted_provider():
            text += (
                "\nNote: a hosted provider is selected — game summaries and the "
                "username are sent to a third party."
            )
        return text


def resolve(env: Env | None = None, chat_model_override: str = "") -> Config:
    """Build a Config from the environment.

    `chat_model_override` is the positional CLI argument, which is the most
    specific source for the chat model. Precedence is: positional arg ->
    CHAT_MODEL -> provider default.
    """
    read = os_env if env is None else env

    chat_provider = _provider_or(read("CHAT_PROVIDER"), OLLAMA)
    embed_provider = _provider_or(read("EMBED_PROVIDER"), OLLAMA)
    ollama_url = _value_or(read("OLLAMA_URL"), OLLAMA_DEFAULT_BASE_URL)

    if chat_provider not in CHAT_PROVIDERS:
        raise ConfigError(
            f"unknown CHAT_PROVIDER {chat_provider!r}; valid values: {', '.join(CHAT_PROVIDERS)}"
        )
    if embed_provider not in EMBED_PROVIDERS:
        if embed_provider == ANTHROPIC:
            raise ConfigError(
                "EMBED_PROVIDER=anthropic is not supported: Anthropic offers no embeddings API. "
                "Keep EMBED_PROVIDER=ollama or use EMBED_PROVIDER=openai "
                "(chat and embeddings are selected independently)"
            )
        raise ConfigError(
            f"unknown EMBED_PROVIDER {embed_provider!r}; valid values: {', '.join(EMBED_PROVIDERS)}"
        )

    chat_model = _first_non_empty(chat_model_override, read("CHAT_MODEL"))
    if not chat_model:
        chat_model = {
            OLLAMA: OLLAMA_DEFAULT_CHAT_MODEL,
            ANTHROPIC: ANTHROPIC_DEFAULT_MODEL,
            OPENAI: OPENAI_DEFAULT_CHAT_MODEL,
        }[chat_provider]

    # OLLAMA_EMBED_MODEL stays a working alias for EMBED_MODEL — it is
    # documented in the README today and costs one line to honor — but only
    # while the embed provider is Ollama. Honoring it otherwise would send a
    # still-exported "nomic-embed-text" to OpenAI.
    embed_model = read("EMBED_MODEL")
    if embed_provider == OLLAMA:
        embed_model = _first_non_empty(embed_model, read("OLLAMA_EMBED_MODEL"))
    embed_model = embed_model.strip()
    if not embed_model:
        embed_model = {
            OLLAMA: OLLAMA_DEFAULT_EMBED_MODEL,
            OPENAI: OPENAI_DEFAULT_EMBED_MODEL,
        }[embed_provider]

    # Credentials are checked when the chat model is constructed, which
    # `zeitnot chat` does before its welcome banner. Doing it here instead would
    # make ingestion — which needs no chat provider at all — fail over a missing
    # chat credential.
    return Config(
        chat_provider=chat_provider,
        chat_model=chat_model,
        embed_provider=embed_provider,
        embed_model=embed_model,
        ollama_url=ollama_url,
        _anthropic_api_key=read(ANTHROPIC_API_KEY_ENV).strip(),
        _openai_api_key=read(OPENAI_API_KEY_ENV).strip(),
    )


def _provider_or(value: str, fallback: str) -> str:
    """Normalize a provider name, which is case-insensitive."""
    value = value.strip()
    return value.lower() if value else fallback


def _value_or(value: str, fallback: str) -> str:
    value = value.strip()
    return value or fallback


def _first_non_empty(*values: str) -> str:
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


# ---------- credential redaction ----------

# scheme://user:password@host — psycopg embeds the connection string in parse
# and connection errors, so anything that prints a wrapped database error prints
# the password with it.
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):(?P<password>[^@\s]*)@"
)

# Provider keys, on the same reasoning: an adapter that quotes a response body
# in an error could carry one, and the bug template asks people to paste error
# output into a public issue.
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


def redact_secrets(text: str) -> str:
    """Blank credentials in anything about to be shown to a user.

    Applied at the point of *output* rather than at each raise site. Error text
    arrives from psycopg and three vendor SDKs, so enumerating the raise sites
    means being right about all of them forever; redacting where they converge
    means being right once.

    The username is kept. It is not a secret, and it is usually the thing that
    makes a connection error diagnosable.
    """
    text = _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:***@", text)
    return _API_KEY.sub("sk-***", text)


# ---------- DATABASE_URL validation ----------
#
# Two failures that libpq reports as something else entirely, caught before a
# connection is attempted. They live here rather than in the CLI because
# `zeitnot doctor` has to ask the same questions without exiting on the first
# answer, and one definition is what keeps the two from drifting.
#
# Both messages changed shape when zeitnot started reading the env file itself
# (`zeitnot.envfile`): the loader strips carriage returns and resolves the
# file's own references in any order, so neither failure can originate in the
# file any more. What is left is the shell, and that is what the remedies now
# name.


def control_character_problem(url: str) -> str:
    r"""A carriage return in the DSN, which is invisible and never valid.

    libpq answers a CR in the port field by reporting a host it cannot resolve,
    which is true and entirely unhelpful: the host is fine, the port is
    "5433\r". Nothing legitimate puts a control character in a connection
    string.
    """
    found = next((c for c in url if ord(c) < 0x20), "")
    if not found:
        return ""
    return (
        f"DATABASE_URL contains a control character ({found!r}), so it is not the "
        "URL it looks like.\n"
        "  zeitnot reads the env file itself and strips carriage returns, so this "
        "one came from the shell —\n"
        "  a file with Windows (CRLF) line endings that was sourced, whose value "
        "then outranks the file's.\n"
        "  Drop the shell's copy and let zeitnot read the file:\n"
        "    unset DATABASE_URL\n"
        "  or convert the file once:\n"
        r"    sed -i 's/\r$//' .env"
    )


def unexpanded_port_problem(url: str) -> str:
    """A port that expanded to nothing, which libpq quietly reads as 5432.

    On the machine that needed a different port in the first place, 5432 is
    usually somebody else's PostgreSQL — so the failure talks about credentials
    and never mentions the port.
    """
    if "${" in url or "$(" in url:
        return (
            "DATABASE_URL still contains an unexpanded variable, so nothing "
            "substituted it.\n"
            "  Single quotes around the value prevent substitution, in the env file "
            "as well as in the shell; use double quotes."
        )
    if urlsplit(url).netloc.endswith(":"):
        return (
            "DATABASE_URL has an empty port, so PostgreSQL would silently be "
            "asked for 5432.\n"
            "  ZEITNOT_DB_PORT expanded to nothing. zeitnot resolves the env file's "
            "own references whatever\n"
            "  order they are written in, so this came from the shell: the URL was "
            "built before the port was set.\n"
            "  Drop the shell's copy and let zeitnot build it from the file:\n"
            "    unset DATABASE_URL"
        )
    return ""


def database_url_problem(url: str) -> str:
    """Everything wrong with a DSN that is knowable without connecting, or ""."""
    return control_character_problem(url) or unexpanded_port_problem(url)


# ---------- startup checks ----------


def preflight(warn: TextIO, *models: Any) -> None:
    """Run each adapter's reachability, credential, and model checks.

    An inconclusive result — a models endpoint a gateway does not implement,
    say — is written to `warn` and swallowed: the real call will report the
    truth, and blocking startup over an auxiliary call would break valid setups.
    Anything else propagates.
    """
    for model in models:
        if not isinstance(model, Preflighter):
            continue
        try:
            model.preflight()
        except LLMError as err:
            if err.kind is ErrorKind.PREFLIGHT_INCONCLUSIVE:
                print(f"Warning: startup check skipped: {redact_secrets(str(err))}", file=warn)
                continue
            raise


def check_index(database: DB, embedder: Embedder, adopt: bool, warn: TextIO) -> None:
    """Verify that the configured embedder matches the index it will query or
    extend.

    Two distinct failures hide here. The vector(N) column width is the obvious
    one. The subtler one is provenance: two 768-dimension models from different
    providers pass a width check while producing vectors in unrelated spaces, so
    cosine distance across them is meaningless and retrieval degrades without
    erroring.

    `adopt` records the current embedder when the index carries no stamp yet —
    which is every index built before provenance existed. Callers that write to
    the index (ingestion) adopt; read-only callers (chat) do not.
    """
    from zeitnot.db import IndexMeta

    dims = embedder.dimensions()
    if dims > 0:
        try:
            column_dims = database.embedding_dimensions()
        except Exception as err:
            print(
                f"Warning: could not read the embedding column width: {redact_secrets(str(err))}",
                file=warn,
            )
        else:
            if column_dims > 0 and column_dims != dims:
                raise ConfigError(
                    f"embedding width mismatch: {embedder.name()}/{embedder.model()} produces "
                    f"{dims} dimensions but game_summaries.embedding is vector({column_dims})"
                )

    try:
        meta = database.get_index_meta()
    except Exception as err:
        print(f"Warning: could not read index provenance: {redact_secrets(str(err))}", file=warn)
        return

    if meta is None:
        if not adopt:
            return
        database.set_index_meta(
            IndexMeta(
                embed_provider=embedder.name(),
                embed_model=embedder.model(),
                dimensions=dims,
            )
        )
        return

    if meta.embed_provider != embedder.name() or meta.embed_model != embedder.model():
        raise ConfigError(
            f"embedding provider mismatch: the index was built with "
            f"{meta.embed_provider}/{meta.embed_model} but the configured embedder is "
            f"{embedder.name()}/{embedder.model()}. "
            "Vectors from different models are not comparable even at the same width. "
            f"Either restore EMBED_PROVIDER={meta.embed_provider} and "
            f"EMBED_MODEL={meta.embed_model}, or re-embed with: zeitnot data reembed"
        )
