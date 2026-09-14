"""`zeitnot doctor` — every startup check, run together and reported as a list.

None of these checks are new. What did not exist was a way to run them *without*
doing the expensive thing they guard: the first honest verification of a setup
was `zeitnot data analyze`, which needs the network, Chess.com, Stockfish,
PostgreSQL and an embedding provider all at once and takes about a second per
game. Every misconfiguration was therefore discovered at the most expensive
possible moment, one at a time, because each check correctly aborts at the first
failure.

Doctor inverts both properties:

- **It never stops at a failure.** One run reports everything that is wrong,
  which is the difference between one round trip and five.
- **It changes nothing.** No migration, no ingestion, no index adoption, no chat
  request. It is safe to run at any point, including against a populated corpus.

Where a check has an existing failure message, that message is reported verbatim
rather than re-worded here. Those messages were written against reproduced
failures and already name their remedies; a second phrasing of each would be one
more thing to keep true.

Output is deliberately plain ASCII with no styling, because it is written to be
pasted into a bug report — the issue template asks for exactly the fields this
prints. It goes through `redact_secrets` on the way out for the same reason.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from zeitnot import config, engine, envfile
from zeitnot.db import DB, DBConnectionError
from zeitnot.llm.base import Embedder, Preflighter
from zeitnot.llm.errors import ErrorKind, LLMError
from zeitnot.llm.providers import (
    ANTHROPIC,
    ANTHROPIC_API_KEY_ENV,
    OPENAI,
    OPENAI_API_KEY_ENV,
)


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "FAIL"
    SKIP = "skip"


@dataclass(slots=True)
class Check:
    """One question asked and answered.

    `detail` may be several lines: the existing failure messages are multi-line
    and are reported as they were written, so the renderer indents continuation
    lines rather than the messages being flattened.
    """

    name: str
    status: Status
    detail: str = ""


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]

    @property
    def warned(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]

    @property
    def exit_code(self) -> int:
        """Non-zero only on a failure. A warning is something to know about, not
        something that stops the tool working, and a doctor that exits non-zero
        for both cannot be used in a script."""
        return 1 if self.failed else 0


# Wide enough for the longest name below, so details line up in a column.
_NAME_WIDTH = 20
_MARKER = {
    Status.OK: "[ ok ]",
    Status.WARN: "[warn]",
    Status.FAIL: "[FAIL]",
    Status.SKIP: "[skip]",
}


def render(check: Check, out: TextIO) -> None:
    """Write one finished check. Called as each completes rather than at the
    end, so the thirty seconds the database check is allowed to wait is visibly
    attributed to the database rather than looking like a hang."""
    prefix = f"{_MARKER[check.status]} {check.name.ljust(_NAME_WIDTH)}"
    lines = config.redact_secrets(check.detail).split("\n") or [""]
    print(f"{prefix} {lines[0]}".rstrip(), file=out)
    padding = " " * (len(prefix) + 1)
    for line in lines[1:]:
        print(f"{padding}{line}".rstrip(), file=out)


def render_summary(report: Report, out: TextIO) -> None:
    counts = [
        f"{len(report.failed)} failed" if report.failed else "",
        f"{len(report.warned)} warning{'s' if len(report.warned) != 1 else ''}"
        if report.warned
        else "",
        f"{sum(1 for c in report.checks if c.status is Status.OK)} ok",
    ]
    print("", file=out)
    print(", ".join(part for part in counts if part), file=out)
    if not report.failed:
        print("", file=out)
        print(
            "Nothing is blocking a run. Next: zeitnot data analyze <username> <year> <month>",
            file=out,
        )


# ---------- the checks ----------


def check_env_file(loaded: envfile.Loaded) -> list[Check]:
    """Report what the loader did, including what it deliberately did not do.

    The deferred names are the important half. Precedence used to run the other
    way — sourcing the file overwrote a value exported by hand — so someone
    changing a port and seeing no effect has to be told *which* copy is winning,
    not merely that one is.
    """
    if loaded.path is None:
        return [
            Check(
                "environment file",
                Status.SKIP,
                f"{envfile.PATH_VAR} is empty, so no file was read",
            )
        ]

    where = _display_path(loaded.path)
    if not loaded.exists and not loaded.problems:
        return [
            Check(
                "environment file",
                Status.WARN,
                f"no file at {where}\n"
                "Values have to come from the environment instead. If you have one, it is in "
                "another directory:\n"
                f"zeitnot reads {envfile.DEFAULT_FILENAME} from the working directory, or "
                f"{envfile.PATH_VAR} if that is set.",
            )
        ]

    checks: list[Check] = []
    lines = [f"{where}: {len(loaded.applied)} value(s) applied"]
    if loaded.deferred:
        lines.append(
            f"already set in the environment, so the file's values were not used: "
            f"{', '.join(loaded.deferred)}"
        )
    if loaded.crlf:
        lines.append(
            "saved with Windows (CRLF) line endings; the carriage returns were stripped on read"
        )
    checks.append(Check("environment file", Status.OK, "\n".join(lines)))

    if loaded.problems:
        checks.append(Check("env file syntax", Status.WARN, "\n".join(loaded.problems)))
    return checks


def check_configuration(environ: Mapping[str, str]) -> tuple[Check, config.Config | None]:
    try:
        cfg = config.resolve(lambda key: environ.get(key, ""))
    except config.ConfigError as err:
        return Check("configuration", Status.FAIL, str(err)), None

    detail = (
        f"chat {cfg.chat_provider} / {cfg.chat_model}\n"
        f"embeddings {cfg.embed_provider} / {cfg.embed_model}"
    )
    if cfg.uses_hosted_provider():
        detail += (
            "\na hosted provider is selected — game summaries and the username "
            "are sent to a third party"
        )
    return Check("configuration", Status.OK, detail), cfg


def check_credentials(cfg: config.Config, environ: Mapping[str, str]) -> Check:
    """Whether the keys the *selected* providers need are present.

    Presence only. The value is never read into the report, and the length is
    not reported either — a key length is not a secret but it is not diagnostic
    either, and this output is written to be pasted in public.
    """
    needed: list[str] = []
    if cfg.chat_provider == ANTHROPIC:
        needed.append(ANTHROPIC_API_KEY_ENV)
    if cfg.chat_provider == OPENAI or cfg.embed_provider == OPENAI:
        needed.append(OPENAI_API_KEY_ENV)

    if not needed:
        return Check("credentials", Status.SKIP, "no hosted provider is selected")

    missing = [name for name in needed if not environ.get(name, "").strip()]
    if missing:
        return Check(
            "credentials",
            Status.FAIL,
            f"not set: {', '.join(missing)}\nThe selected providers require them.",
        )
    return Check("credentials", Status.OK, f"set: {', '.join(needed)}")


def check_stockfish() -> Check:
    """Found *and* runs.

    A PATH lookup answers "is there a file". Starting the process answers "is it
    an engine", which is the question a setup check is actually asking: a binary
    for the wrong architecture, or a wrapper script that never speaks UCI,
    passes the first and fails the second — and would otherwise fail in a worker
    thread, after a month of games had already been fetched.
    """
    found = engine.find_stockfish()
    if found is None:
        try:
            engine.require_stockfish()
        except engine.EngineError as err:
            return Check("stockfish", Status.FAIL, str(err))
    try:
        name = engine.probe()
    except engine.EngineError as err:
        return Check(
            "stockfish",
            Status.FAIL,
            f"found at {found} but it did not start: {err}",
        )
    return Check("stockfish", Status.OK, f"{name or 'started'} at {found}")


def check_database_url(environ: Mapping[str, str]) -> tuple[Check, str]:
    url = environ.get("DATABASE_URL", "")
    if not url:
        return (
            Check(
                "DATABASE_URL",
                Status.FAIL,
                "not set.\n"
                f"Put it in {envfile.DEFAULT_FILENAME} — see "
                f"{envfile.DEFAULT_FILENAME}.example — or export it.",
            ),
            "",
        )
    problem = config.database_url_problem(url)
    if problem:
        return Check("DATABASE_URL", Status.FAIL, problem), ""
    return Check("DATABASE_URL", Status.OK, url), url


def check_port_agreement(url: str, environ: Mapping[str, str]) -> Check:
    """The one invariant nothing else enforces.

    `docker-compose.yml` publishes `ZEITNOT_DB_PORT` and zeitnot connects to
    whatever `DATABASE_URL` names; the two are configured independently. When
    they disagree the resulting error is about credentials, because on the
    machine that needed a different port, 5432 is usually the PostgreSQL that
    caused the move.

    A warning rather than a failure: someone running their own PostgreSQL has
    every right to a `DATABASE_URL` that has nothing to do with Compose.
    """
    declared = environ.get("ZEITNOT_DB_PORT", "").strip()
    if not declared:
        return Check(
            "database port",
            Status.SKIP,
            "ZEITNOT_DB_PORT is not set, so docker compose publishes 5432",
        )
    try:
        connecting = urlsplit(url).port
    except ValueError:
        return Check(
            "database port",
            Status.WARN,
            f"DATABASE_URL does not carry a readable port, so it cannot be "
            f"compared with ZEITNOT_DB_PORT={declared}",
        )

    actual = 5432 if connecting is None else connecting
    if str(actual) == declared:
        return Check("database port", Status.OK, f"compose and DATABASE_URL both use {actual}")
    return Check(
        "database port",
        Status.WARN,
        f"docker compose publishes {declared} but DATABASE_URL connects to {actual}.\n"
        "Nothing else checks that these agree. Set ZEITNOT_DB_PORT in the env file and let\n"
        "DATABASE_URL expand it, so there is one place to change.",
    )


def check_database(url: str) -> tuple[list[Check], DB | None]:
    """Connect, then ask what is actually in there.

    The connect wait is the same thirty seconds the real commands allow, and
    deliberately so: a doctor that gives up sooner than the tool would report a
    failure on a cold container that `zeitnot chat` handles fine, which is worse
    than being slow.
    """
    try:
        database = DB(url)
    except DBConnectionError as err:
        return [Check("database", Status.FAIL, str(err))], None

    checks = [Check("database", Status.OK, "connected")]
    try:
        with database.cursor() as cur:
            cur.execute(
                """SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector'),
                          to_regclass('games') IS NOT NULL,
                          to_regclass('game_summaries') IS NOT NULL"""
            )
            row = cur.fetchone()
    except Exception as err:  # a diagnostic must not raise while diagnosing
        checks.append(Check("corpus", Status.WARN, f"could not be inspected: {err}"))
        return checks, database
    if row is None:
        checks.append(Check("corpus", Status.WARN, "could not be inspected"))
        return checks, database

    has_vector, has_games, has_summaries = (bool(row[0]), bool(row[1]), bool(row[2]))
    if not has_vector:
        checks.append(
            Check(
                "pgvector",
                Status.FAIL,
                "the vector extension is not installed in this database.\n"
                "The compose image enables it on a first start with an empty volume; a\n"
                "half-initialized one never gets it. Discard the volume and let it\n"
                "initialize again — this deletes any analyzed games:\n"
                "  docker compose down -v && docker compose up -d",
            )
        )
    else:
        checks.append(Check("pgvector", Status.OK, "installed"))

    if not has_games:
        checks.append(
            Check(
                "corpus",
                Status.OK,
                "no tables yet — zeitnot creates them on the first `zeitnot data analyze`",
            )
        )
        return checks, database

    status, detail = _corpus_detail(database, has_summaries)
    checks.append(Check("corpus", status, detail))
    return checks, database


def _corpus_detail(database: DB, has_summaries: bool) -> tuple[Status, str]:
    try:
        with database.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM games")
            games = int((cur.fetchone() or (0,))[0])
            summaries = 0
            if has_summaries:
                cur.execute("SELECT COUNT(*) FROM game_summaries")
                summaries = int((cur.fetchone() or (0,))[0])
    except Exception as err:  # a diagnostic must not raise while diagnosing
        return Status.WARN, f"could not be counted: {err}"

    if games == 0:
        return Status.OK, "empty — run `zeitnot data analyze <username> <year> <month>`"
    if summaries < games:
        return (
            Status.WARN,
            f"{games} games, but only {summaries} have a summary — "
            "re-run `zeitnot data analyze` for the months that did not finish",
        )
    return Status.OK, f"{games} games, {summaries} summaries"


def check_chat_provider(cfg: config.Config) -> Check:
    """Reachability, credentials and the configured model, before a first
    question rather than after one.

    Only `zeitnot chat` needs this, which is why a failure here says so: someone
    ingesting games has no use for a chat provider and should not be told their
    setup is broken over one.
    """
    check = _preflight("chat provider", cfg.new_chat_model)[0]
    if check.status is Status.FAIL:
        check.detail += "\nOnly `zeitnot chat` needs this; `zeitnot data analyze` does not."
    return check


def check_embedding_provider(cfg: config.Config) -> tuple[Check, Embedder | None]:
    check, model = _preflight("embeddings", cfg.new_embedder)
    embedder = model if isinstance(model, Embedder) else None
    return check, embedder


def _preflight(name: str, build: Callable[[], object]) -> tuple[Check, object | None]:
    """Construct an adapter and run its startup check, reporting either failure
    as a line rather than an exception.

    Construction is where a missing API key is caught, and preflight is where an
    unreachable Ollama or an unpulled model is; both are ordinary setup states,
    so neither is allowed to end the run.
    """
    try:
        model = build()
    except (LLMError, config.ConfigError) as err:
        return Check(name, Status.FAIL, str(err)), None

    if not isinstance(model, Preflighter):
        return Check(name, Status.OK, "configured (no startup check available)"), model

    try:
        model.preflight()
    except LLMError as err:
        if err.kind is ErrorKind.PREFLIGHT_INCONCLUSIVE:
            return Check(name, Status.WARN, f"check skipped: {err}"), model
        return Check(name, Status.FAIL, str(err)), model
    return Check(name, Status.OK, "reachable, credentials and model accepted"), model


def check_index(database: DB, embedder: Embedder) -> Check:
    """Whether the configured embedder matches the index it would query.

    Read-only: `adopt` is False, so doctor never stamps an unstamped index. That
    is ingestion's decision to make, and a diagnostic that quietly took it would
    change what the next real run does.
    """
    warnings = io.StringIO()
    try:
        config.check_index(database, embedder, False, warnings)
    except config.ConfigError as err:
        return Check("index provenance", Status.FAIL, str(err))

    noted = warnings.getvalue().strip()
    if noted:
        return Check("index provenance", Status.WARN, noted)
    return Check("index provenance", Status.OK, f"{embedder.name()} / {embedder.model()}")


# ---------- the run ----------


def run(
    out: TextIO,
    environ: MutableMapping[str, str] | None = None,
    loaded: envfile.Loaded | None = None,
) -> Report:
    """Every check, in order of how fast it answers.

    Everything local and instant comes first, so a wrong provider name or a
    missing Stockfish is on screen before the run spends thirty seconds finding
    out that nothing is listening on 5432.
    """
    env = os.environ if environ is None else environ
    report = Report()

    def record(check: Check) -> None:
        report.checks.append(check)
        render(check, out)

    if loaded is None:
        loaded = envfile.load(env)
    for check in check_env_file(loaded):
        record(check)

    configuration, cfg = check_configuration(env)
    record(configuration)
    if cfg is not None:
        record(check_credentials(cfg, env))

    record(check_stockfish())

    url_check, url = check_database_url(env)
    record(url_check)
    if url:
        record(check_port_agreement(url, env))

    database: DB | None = None
    if url:
        checks, database = check_database(url)
        for check in checks:
            record(check)

    embedder: Embedder | None = None
    if cfg is not None:
        record(check_chat_provider(cfg))
        embed_check, embedder = check_embedding_provider(cfg)
        record(embed_check)

    if database is not None and embedder is not None:
        record(check_index(database, embedder))
    if database is not None:
        database.close()

    render_summary(report, out)
    return report


def _display_path(path: Path) -> str:
    """An absolute path, because "which file did it read" is the question a
    relative one leaves open — and running from the wrong directory is the
    failure a fixed filename trades for."""
    try:
        return str(path.resolve())
    except OSError:
        return str(path)
