"""Ported from internal/config/config_test.go.

Backward compatibility lives in the precedence table, which is why it is the
highest-value test in the config layer: it is a table over an env map with no
network, and every row is a setup somebody may already have.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from zeitnot.config import Config, ConfigError, redact_secrets, resolve


def env_of(pairs: dict[str, str]):  # type: ignore[no-untyped-def]
    def read(key: str) -> str:
        return pairs.get(key, "")

    return read


PRECEDENCE_CASES = [
    pytest.param(
        {},
        "",
        ("ollama", "llama3.2", "ollama", "nomic-embed-text", "http://localhost:11434"),
        # The backward-compatibility case: a user with only the pre-existing
        # variables must see identical behavior.
        id="defaults are ollama end to end",
    ),
    pytest.param(
        {"OLLAMA_URL": "http://box:11434", "OLLAMA_EMBED_MODEL": "mxbai-embed-large"},
        "",
        ("ollama", "llama3.2", "ollama", "mxbai-embed-large", "http://box:11434"),
        id="pre-existing OLLAMA_ variables still apply",
    ),
    pytest.param(
        {"EMBED_MODEL": "bge-m3", "OLLAMA_EMBED_MODEL": "nomic-embed-text"},
        "",
        ("ollama", "llama3.2", "ollama", "bge-m3", "http://localhost:11434"),
        id="EMBED_MODEL outranks the OLLAMA_EMBED_MODEL alias",
    ),
    pytest.param(
        {"CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-test"},
        "",
        ("anthropic", "claude-opus-5", "ollama", "nomic-embed-text", "http://localhost:11434"),
        id="anthropic chat keeps embeddings local",
    ),
    pytest.param(
        {
            "CHAT_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-test",
            "CHAT_MODEL": "claude-sonnet-5",
        },
        "",
        ("anthropic", "claude-sonnet-5", "ollama", "nomic-embed-text", "http://localhost:11434"),
        id="CHAT_MODEL overrides the provider default",
    ),
    pytest.param(
        {"CHAT_MODEL": "llama3.1"},
        "mistral",
        ("ollama", "mistral", "ollama", "nomic-embed-text", "http://localhost:11434"),
        # The positional argument is the most specific source, which is what
        # keeps `zeitnot chat <username> [model]` working.
        id="positional argument outranks CHAT_MODEL",
    ),
    pytest.param(
        {"CHAT_PROVIDER": "Anthropic", "ANTHROPIC_API_KEY": "sk-test"},
        "",
        ("anthropic", "claude-opus-5", "ollama", "nomic-embed-text", "http://localhost:11434"),
        id="provider names are case-insensitive",
    ),
    pytest.param(
        {"CHAT_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"},
        "",
        ("openai", "gpt-5-2025-08-07", "ollama", "nomic-embed-text", "http://localhost:11434"),
        id="openai chat keeps embeddings local",
    ),
    pytest.param(
        {"CHAT_PROVIDER": "openai", "EMBED_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"},
        "",
        (
            "openai",
            "gpt-5-2025-08-07",
            "openai",
            "text-embedding-3-small",
            "http://localhost:11434",
        ),
        # The configuration that removes Ollama from the prerequisite list
        # entirely: both providers hosted.
        id="openai for both chat and embeddings",
    ),
    pytest.param(
        {
            "CHAT_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant",
            "EMBED_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
        },
        "",
        (
            "anthropic",
            "claude-opus-5",
            "openai",
            "text-embedding-3-small",
            "http://localhost:11434",
        ),
        # The headline mix: hosted chat, local embeddings already indexed. It
        # must require no re-embedding.
        id="anthropic chat with openai embeddings",
    ),
    pytest.param(
        {
            "EMBED_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
            "OLLAMA_EMBED_MODEL": "nomic-embed-text",
        },
        "",
        ("ollama", "llama3.2", "openai", "text-embedding-3-small", "http://localhost:11434"),
        # The alias is scoped to Ollama. A user who switches embed providers
        # with OLLAMA_EMBED_MODEL still exported must not send an Ollama model
        # name to OpenAI.
        id="OLLAMA_EMBED_MODEL does not leak into another embed provider",
    ),
    pytest.param(
        {
            "EMBED_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
            "EMBED_MODEL": "text-embedding-3-large",
        },
        "",
        ("ollama", "llama3.2", "openai", "text-embedding-3-large", "http://localhost:11434"),
        id="EMBED_MODEL applies to a hosted embed provider",
    ),
]


@pytest.mark.parametrize(("env", "positional", "want"), PRECEDENCE_CASES)
def test_resolve_precedence(
    env: dict[str, str], positional: str, want: tuple[str, str, str, str, str]
) -> None:
    cfg = resolve(env_of(env), positional)
    got = (
        cfg.chat_provider,
        cfg.chat_model,
        cfg.embed_provider,
        cfg.embed_model,
        cfg.ollama_url,
    )
    assert got == want


@pytest.mark.parametrize(
    ("env", "want_text"),
    [
        pytest.param(
            {"CHAT_PROVIDER": "gemini"},
            ["CHAT_PROVIDER", "ollama", "anthropic", "openai"],
            id="unknown chat provider lists valid values",
        ),
        pytest.param(
            {"EMBED_PROVIDER": "voyage"},
            ["EMBED_PROVIDER", "ollama", "openai"],
            id="unknown embed provider lists valid values",
        ),
        pytest.param(
            {"EMBED_PROVIDER": "anthropic"},
            ["no embeddings API", "EMBED_PROVIDER=ollama", "EMBED_PROVIDER=openai"],
            # Anthropic has no embeddings API, so this is explained rather than
            # silently falling back.
            id="anthropic embeddings are refused with an explanation",
        ),
    ],
)
def test_resolve_errors(env: dict[str, str], want_text: list[str]) -> None:
    with pytest.raises(ConfigError) as excinfo:
        resolve(env_of(env), "")
    message = str(excinfo.value)
    for want in want_text:
        assert want in message, f"error = {message!r}, want it to mention {want!r}"


def test_summary_never_leaks_the_key() -> None:
    """The key must never reach a message or the startup banner."""
    cfg = resolve(
        env_of({"CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-secret-value"}), ""
    )
    summary = cfg.summary()
    assert "sk-ant-secret-value" not in summary
    assert "sent to a third party" in summary
    assert cfg.uses_hosted_provider()

    # repr is the other way a secret escapes — into a log line or a traceback.
    assert "sk-ant-secret-value" not in repr(cfg)


def test_local_only_setup_says_nothing_about_egress() -> None:
    cfg = resolve(env_of({}), "")
    assert not cfg.uses_hosted_provider(), (
        "the default configuration must stay account-free and local"
    )
    assert "third party" not in cfg.summary()


def test_preflight_swallows_an_inconclusive_check_and_reraises_anything_else() -> None:
    from zeitnot.config import preflight
    from zeitnot.llm.errors import ErrorKind, LLMError

    class Inconclusive:
        def preflight(self) -> None:
            raise LLMError("fake", "chat", ErrorKind.PREFLIGHT_INCONCLUSIVE, "no models endpoint")

    class Fatal:
        def preflight(self) -> None:
            raise LLMError("fake", "chat", ErrorKind.UNAUTHORIZED, "bad key")

    class NotAPreflighter:
        pass

    warn = io.StringIO()
    preflight(warn, Inconclusive(), NotAPreflighter())
    assert "startup check skipped" in warn.getvalue()

    with pytest.raises(LLMError) as excinfo:
        preflight(io.StringIO(), Fatal())
    assert excinfo.value.kind is ErrorKind.UNAUTHORIZED


def test_config_is_constructible_without_an_sdk_installed() -> None:
    """resolve() and its error messages must not depend on a vendor SDK.

    Nothing about naming a provider requires importing its client, and making
    startup do so would turn an unrelated install problem into an unresolvable
    configuration error.
    """
    cfg = Config(
        chat_provider="anthropic",
        chat_model="claude-opus-5",
        embed_provider="ollama",
        embed_model="nomic-embed-text",
        ollama_url="http://localhost:11434",
    )
    assert cfg.uses_hosted_provider()


# ---------- credential redaction (readiness P2-5) ----------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "connection failed for postgres://zeitnot:hunter2@localhost:5432/zeitnot",
            "connection failed for postgres://zeitnot:***@localhost:5432/zeitnot",
        ),
        # psycopg's own parse errors quote the URL back.
        (
            "invalid dsn: postgresql://user:s3cr3t@db.example.com:5432/app?sslmode=require",
            "invalid dsn: postgresql://user:***@db.example.com:5432/app?sslmode=require",
        ),
        # A percent-encoded @ inside the password must not end the match early.
        ("postgresql://u:p%40ss@h/db", "postgresql://u:***@h/db"),
        # Two URLs in one message.
        (
            "tried postgres://a:1@x/db then postgres://b:2@y/db",
            "tried postgres://a:***@x/db then postgres://b:***@y/db",
        ),
        # Provider keys, since the bug template asks for pasted error output.
        ("key sk-ant-api03-AbC123xyz_-9 rejected", "key sk-*** rejected"),
        ("Incorrect API key provided: sk-proj-abcd1234EFGH", "Incorrect API key provided: sk-***"),
    ],
    ids=["basic", "psycopg-dsn", "encoded-at", "two-urls", "anthropic-key", "openai-key"],
)
def test_redact_secrets_blanks_credentials(text: str, want: str) -> None:
    assert redact_secrets(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "postgres://zeitnot@localhost/zeitnot",  # no password component
        "Stockfish not found on PATH",
        "sk-",  # too short to be a key
        "",
    ],
)
def test_redact_secrets_leaves_everything_else_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_the_username_survives_redaction() -> None:
    """The user is not a secret, and it is usually what makes a connection error
    diagnosable — "wrong user" and "wrong password" look identical otherwise."""
    assert "zeitnot" in redact_secrets("postgres://zeitnot:pw@localhost/db")


def test_the_pool_logger_filter_redacts_a_quoted_dsn() -> None:
    """psycopg_pool logs connection failures on its own logger, without passing
    through any zeitnot error path. A malformed DATABASE_URL makes libpq quote
    the whole DSN back, so this is the path that actually leaked in practice.
    """
    import logging

    from zeitnot.db import _install_pool_log_redaction

    _install_pool_log_redaction()
    logger = logging.getLogger("psycopg.pool")

    record = logger.makeRecord(
        "psycopg.pool",
        logging.WARNING,
        __file__,
        0,
        'error connecting in %r: missing "=" after "%s" in connection info string',
        ("pool-1", "notascheme://zeitnot:hunter2@localhost/zeitnot"),
        None,
    )
    for log_filter in logger.filters:
        if isinstance(log_filter, logging.Filter):
            log_filter.filter(record)

    assert "hunter2" not in record.getMessage()
    assert "zeitnot:***@" in record.getMessage()


def test_the_pool_logger_filter_swallows_retry_chatter_and_keeps_the_reason() -> None:
    """The pool retries once per second until the wait times out, so letting
    every attempt through scrolls the eventual error off the screen. The last
    one is kept so _connect_failure can report it instead of a bare PoolTimeout.
    """
    import logging

    from zeitnot.db import _install_pool_log_redaction

    _install_pool_log_redaction()
    logger = logging.getLogger("psycopg.pool")
    [pool_filter] = [f for f in logger.filters if isinstance(f, logging.Filter)]

    record = logger.makeRecord(
        "psycopg.pool",
        logging.WARNING,
        __file__,
        0,
        "error connecting in %r: connection failed: connection to server at "
        '"127.0.0.1", port 5499 failed: Connection refused',
        ("pool-1",),
        None,
    )
    assert pool_filter.filter(record) is False

    import zeitnot.db

    assert "Connection refused" in zeitnot.db._last_connect_error


def test_a_pool_timeout_is_reported_as_the_reason_libpq_gave() -> None:
    """A PoolTimeout says only that the pool did not fill. The refused
    connection, the wrong port, and the wrong password all look identical
    through it, which is the failure a first-time setup actually hits.
    """
    import zeitnot.db
    from zeitnot.db import _connect_failure

    zeitnot.db._last_connect_error = (
        "error connecting in 'pool-1': connection failed: connection to server "
        'at "127.0.0.1", port 5499 failed: Connection refused\n'
        '\tconnection to server at "127.0.0.1", port 5499 failed: Connection refused'
    )
    message = str(_connect_failure(30.0))

    assert "could not connect to the database after 30s" in message
    # The duplicate libpq line — one per address tried — appears once.
    assert message.count("Connection refused") == 1
    assert "ZEITNOT_DB_PORT" in message


def test_a_connection_failure_does_not_leak_the_password() -> None:
    """The DSN reaches this path through psycopg_pool's own log record, which
    never passes through zeitnot's output sites."""
    import logging

    import zeitnot.db
    from zeitnot.db import _connect_failure, _install_pool_log_redaction

    _install_pool_log_redaction()
    logger = logging.getLogger("psycopg.pool")
    record = logger.makeRecord(
        "psycopg.pool",
        logging.WARNING,
        __file__,
        0,
        'error connecting in %r: missing "=" after "%s" in connection info string',
        ("pool-1", "notascheme://zeitnot:hunter2@localhost/zeitnot"),
        None,
    )
    for log_filter in logger.filters:
        if isinstance(log_filter, logging.Filter):
            log_filter.filter(record)

    message = str(_connect_failure(30.0))
    assert "hunter2" not in message
    assert "zeitnot:***@" in message
    assert zeitnot.db._last_connect_error  # captured, not merely redacted


def test_a_missing_database_is_not_reported_as_a_port_problem() -> None:
    """The container is up and the credentials work, so "check the port" is a
    dead end. The postgres image runs POSTGRES_DB only on a first start with an
    empty data directory, so an interrupted first `up` leaves a volume that no
    restart repairs.
    """
    from zeitnot.db import _hint

    hint = " ".join(_hint('FATAL:  database "zeitnot" does not exist'))

    assert "down -v" in hint
    assert "ZEITNOT_DB_PORT" not in hint


def test_a_rejected_login_points_at_the_wrong_server_not_the_volume() -> None:
    from zeitnot.db import _hint

    hint = " ".join(_hint('FATAL:  password authentication failed for user "zeitnot"'))

    assert "ZEITNOT_DB_PORT" in hint
    assert "down -v" not in hint


def test_a_refused_connection_points_at_the_port() -> None:
    from zeitnot.db import _hint

    hint = " ".join(_hint("Connection refused"))

    assert "ZEITNOT_DB_PORT" in hint
    assert "down -v" not in hint


@pytest.mark.parametrize(
    "url",
    [
        # The CR lands mid-URL, because the port is interpolated into it.
        "postgres://zeitnot:zeitnot@localhost:5433\r/zeitnot?sslmode=disable",
        "postgres://zeitnot:zeitnot@localhost:5433/zeitnot\r",
        "postgres://zeitnot:zeitnot@localhost:5433/zeitnot\n",
        "postgres://zeitnot:zeitnot@localhost:5433/zeitnot\t",
    ],
)
def test_a_control_character_in_the_dsn_is_rejected_before_connecting(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sourcing a file saved with Windows line endings puts a CR in every value.
    libpq answers a CR in the port field with "failed to resolve host", which is
    true and useless — the host is fine and the character is invisible.
    """
    import typer

    from zeitnot.cli import _reject_control_characters

    with pytest.raises(typer.Exit):
        _reject_control_characters(url)

    err = capsys.readouterr().err
    assert "control character" in err
    assert "CRLF" in err


def test_an_ordinary_dsn_is_left_alone() -> None:
    from zeitnot.cli import _reject_control_characters

    # Must return rather than raise; anything else would break every run.
    _reject_control_characters("postgres://u:p@localhost:5432/zeitnot")


def test_an_empty_port_is_rejected_rather_than_silently_meaning_5432(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ZEITNOT_DB_PORT set below DATABASE_URL in the env file expands to
    nothing, leaving "localhost:/zeitnot". libpq reads that as 5432 — usually
    the very server the user moved off — so the failure blames credentials.
    """
    import typer

    from zeitnot.cli import _reject_unexpanded_port

    with pytest.raises(typer.Exit):
        _reject_unexpanded_port("postgres://c:c@localhost:/zeitnot?sslmode=disable")

    assert "empty port" in capsys.readouterr().err


def test_an_unexpanded_variable_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Single quotes round the value stop the shell substituting it."""
    import typer

    from zeitnot.cli import _reject_unexpanded_port

    with pytest.raises(typer.Exit):
        _reject_unexpanded_port("postgres://c:c@localhost:${ZEITNOT_DB_PORT}/zeitnot")

    assert "unexpanded variable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "postgres://c:c@localhost:5433/zeitnot",
        # No port at all is legitimate and means 5432; only an empty one is not.
        "postgres://c:c@localhost/zeitnot",
    ],
)
def test_a_well_formed_dsn_passes_the_port_check(url: str) -> None:
    from zeitnot.cli import _reject_unexpanded_port

    _reject_unexpanded_port(url)


def test_stockfish_path_overrides_the_path_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Editing PATH is per-shell and easy to get wrong; the env file is where
    everything else lives."""
    from zeitnot.engine import find_stockfish, resolve_command

    binary = tmp_path / "sf"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setenv("STOCKFISH_PATH", str(binary))
    assert resolve_command() == str(binary)
    assert find_stockfish() == str(binary)


def test_an_unset_stockfish_path_falls_back_to_the_plain_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zeitnot.engine import resolve_command

    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    assert resolve_command() == "stockfish"
    # Whitespace-only is treated as unset; a blank line in an env file is not a
    # request to run a binary called "".
    monkeypatch.setenv("STOCKFISH_PATH", "   ")
    assert resolve_command() == "stockfish"


def test_a_stockfish_path_pointing_nowhere_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "not on PATH" would be a lie when the user set an explicit path."""
    from zeitnot.engine import EngineError, require_stockfish

    monkeypatch.setenv("STOCKFISH_PATH", str(tmp_path / "absent"))
    with pytest.raises(EngineError) as excinfo:
        require_stockfish()

    message = str(excinfo.value)
    assert "STOCKFISH_PATH" in message
    assert "not on PATH" not in message


def test_a_directory_is_not_mistaken_for_the_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from zeitnot.engine import find_stockfish

    monkeypatch.setenv("STOCKFISH_PATH", str(tmp_path))
    assert find_stockfish() is None
