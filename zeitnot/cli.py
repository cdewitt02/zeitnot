"""The `zeitnot` command: `zeitnot data ...` and `zeitnot chat ...`.

One binary with two subcommands, where Go had two `cmd/` packages. That is the
one user-visible gain of the rewrite: `zeitnot data analyze magnus 2026 08`
rather than `go run ./cmd/data analyze magnus 2026 08`, and an install route
that does not require the user's toolchain to be a *build* toolchain.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Annotated

import typer

from zeitnot import config, engine, envfile
from zeitnot.api import ChessComError, get_data
from zeitnot.chat.service import Config as ChatConfig
from zeitnot.chat.service import Service
from zeitnot.db import DB, DBConnectionError, IndexMeta
from zeitnot.llm.base import Embedder, embed_one
from zeitnot.models import Game, YearMonth

_HELP = "A chess coach that answers questions about your own games."

app = typer.Typer(add_completion=False, no_args_is_help=True, help=_HELP)
data_app = typer.Typer(no_args_is_help=True, help="Ingest and maintain the corpus.")
app.add_typer(data_app, name="data")

# What `envfile.load` did, kept so `doctor` can report it in context rather than
# loading a second time and describing a different run than the one that
# configured the process.
_ENV_LOAD: envfile.Loaded | None = None


# help= is passed explicitly because registering a callback makes Typer take the
# app's help text from it, and the text should not depend on that.
@app.callback(help=_HELP)
def _load_environment(ctx: typer.Context) -> None:
    """Read the environment file before any subcommand reads the environment.

    Nothing did this before, which made the *shell* zeitnot's configuration
    loader — see `zeitnot.envfile` for what that cost. Values already exported
    are left alone, so anything set by hand still wins.
    """
    global _ENV_LOAD

    _ENV_LOAD = envfile.load()
    if ctx.invoked_subcommand == "doctor":
        # doctor reports these itself, alongside the file they came from.
        return
    for problem in _ENV_LOAD.problems:
        print(f"Warning: {_ENV_LOAD.path}: {problem}", file=sys.stderr)


# Number of games retrieved for context, and the number of most-relevant games
# shown in the prompt. Both feed the assembled prompt, so changing either
# invalidates the Phase 5 goldens.
DEFAULT_NUM_SIMILAR = 100
DEFAULT_DETAIL_LIMIT = 10


def _fail(message: str) -> None:
    # Redacted here rather than at each call site: several of these messages are
    # wrapped psycopg errors, which embed the connection string.
    print(f"Error: {config.redact_secrets(message)}", file=sys.stderr)
    raise typer.Exit(1)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        _fail(
            "DATABASE_URL environment variable is required.\n"
            f"  zeitnot reads {envfile.DEFAULT_FILENAME} from the working directory — copy "
            f"{envfile.DEFAULT_FILENAME}.example to it — or export the variable.\n"
            "  `zeitnot doctor` says which file was read and what it set."
        )
    _reject_control_characters(url)
    _reject_unexpanded_port(url)
    return url


# Both checks live in `zeitnot.config`, because `zeitnot doctor` asks the same
# questions without exiting on the first answer. These wrappers are what turns a
# message into the exit every other startup failure uses.


def _reject_unexpanded_port(url: str) -> None:
    problem = config.unexpanded_port_problem(url)
    if problem:
        _fail(problem)


def _reject_control_characters(url: str) -> None:
    problem = config.control_character_problem(url)
    if problem:
        _fail(problem)


def _open_db(url: str = "") -> DB:
    """Open the pool, reporting a failure the way every other startup check is.

    An unreachable database is the most likely thing to go wrong on a first
    setup — the container is still starting, or the published port is not the
    one in DATABASE_URL — and a psycopg traceback is the wrong shape for it.
    """
    try:
        return DB(url or _database_url())
    except DBConnectionError as err:
        _fail(str(err))
        raise


def _num_workers() -> int:
    raw = os.environ.get("NUM_WORKERS", "")
    if raw:
        try:
            n = int(raw)
        except ValueError:
            return 4
        if n > 0:
            return n
    return 4


def _new_embedder(database: DB, adopt: bool) -> Embedder:
    """Resolve the embedding provider from the shared config.

    Ingestion used to hardcode the Ollama endpoint and model, which meant a
    provider chosen for chat left ingestion silently on a different model than
    the one that built the index.
    """
    cfg = config.resolve()
    embedder = cfg.new_embedder()
    config.preflight(sys.stderr, embedder)
    config.check_index(database, embedder, adopt, sys.stderr)
    return embedder


# ---------- zeitnot doctor ----------


@app.command("doctor")
def doctor() -> None:
    """Check the setup and report everything that is wrong, without running a job.

    Every check here already ran somewhere; what it did not have was a way to
    run without the expensive work it guards. The first honest verification of a
    setup used to be `zeitnot data analyze`, which needs the network, Chess.com,
    Stockfish, PostgreSQL and an embedding provider at once — so every mistake
    was found at the worst possible moment, one per attempt.
    """
    from zeitnot import doctor as checks

    report = checks.run(sys.stdout, loaded=_ENV_LOAD)
    raise typer.Exit(report.exit_code)


# ---------- zeitnot data ----------


@data_app.command("analyze")
def analyze(
    username: Annotated[str, typer.Argument(help="Chess.com username")],
    year: Annotated[int, typer.Argument(help="Year, e.g. 2026")],
    month: Annotated[str, typer.Argument(help="Month with its leading zero, e.g. 08")],
) -> None:
    """Fetch and analyze one month of games from Chess.com."""
    # Validated before the request, because Chess.com answers a malformed month
    # with the same 404 it uses for an unknown username.
    try:
        when = YearMonth(year=year, month=month)
    except ValueError as err:
        _fail(str(err))
        return

    # Before the fetch: a month of games and a database connection are wasted
    # work if there is no engine to analyze them with.
    try:
        engine.require_stockfish()
    except engine.EngineError as err:
        _fail(str(err))
        return

    try:
        games = get_data(when, username)
    except ChessComError as err:
        # The message already names the remedy; a "Failed to get data:" prefix
        # would only push it further from the start of the line.
        _fail(str(err))
        return
    print(f"Fetched {len(games)} games from Chess.com")

    with _open_db() as database:
        database.migrate()

        to_analyze: list[Game] = [g for g in games if not database.game_exists(g.uuid)]
        if not to_analyze:
            print("All games already analyzed!")
            return
        print(f"{len(to_analyze)} new games to analyze")

        # Ingestion writes the index, so it adopts an unstamped one.
        embedder = _new_embedder(database, adopt=True)
        print(f"Embeddings: {embedder.name()} / {embedder.model()}")

        workers = _num_workers()
        print(f"Starting {workers} workers...")
        start = time.monotonic()

        from zeitnot.ingest import WorkerPool

        WorkerPool(workers, database, embedder, username).process(to_analyze)

        elapsed = time.monotonic() - start
        rate = len(to_analyze) / elapsed if elapsed > 0 else 0.0
        print(
            f"Successfully analyzed {len(to_analyze)} games in {elapsed:.3f}s "
            f"({rate:.2f} games/sec)"
        )

        print("\nRefreshing aggregate stats...")
        stats = database.refresh_player_stats(username)
        win_rate = stats.wins / stats.total_games * 100 if stats.total_games else 0.0
        print(f"Stats updated: {stats.total_games} total games, {win_rate:.1f}% win rate")


@data_app.command("refresh-stats")
def refresh_stats(username: Annotated[str, typer.Argument(help="Chess.com username")]) -> None:
    """Recompute the aggregate stats for a player."""
    with _open_db() as database:
        database.migrate()

        print(f"Refreshing stats for {username}...")
        start = time.monotonic()
        stats = database.refresh_player_stats(username)
        print(f"Stats refreshed in {time.monotonic() - start:.3f}s")

        print(f"\nPlayer: {stats.username}")
        print(
            f"Total Games: {stats.total_games} "
            f"(W: {stats.wins}, L: {stats.losses}, D: {stats.draws})"
        )
        print(f"Average CPL: {stats.avg_cpl:.1f}")

        # Sorted, where the Go version ranged maps. This is display output
        # rather than prompt text, but a listing that reorders between runs is
        # still worse than one that does not.
        if stats.stats_by_color:
            print("\nBy Color:")
            for color in sorted(stats.stats_by_color):
                s = stats.stats_by_color[color]
                print(
                    f"  {color}: {s.games} games, {s.win_rate:.1f}% win rate, "
                    f"{s.avg_cpl:.1f} avg CPL"
                )

        if stats.stats_by_time_class:
            print("\nBy Time Class:")
            for tc in sorted(stats.stats_by_time_class):
                s = stats.stats_by_time_class[tc]
                print(
                    f"  {tc}: {s.games} games, {s.win_rate:.1f}% win rate, {s.avg_cpl:.1f} avg CPL"
                )

        if stats.stats_by_termination:
            print("\nBy Termination:")
            for term in sorted(stats.stats_by_termination):
                print(f"  {term}: {stats.stats_by_termination[term]}")


@data_app.command("reembed")
def reembed() -> None:
    """Rebuild every vector from the stored summary text.

    Summaries are generated deterministically with no LLM and no Stockfish, so
    switching embedding models is a bounded re-embed pass rather than a
    re-analysis.
    """
    with _open_db() as database:
        database.migrate()

        cfg = config.resolve()
        embedder = cfg.new_embedder()
        config.preflight(sys.stderr, embedder)

        rows = database.all_summary_texts()
        if not rows:
            print("No stored summaries to re-embed.")
            return

        print(f"Re-embedding {len(rows)} summaries with {embedder.name()} / {embedder.model()}...")
        start = time.monotonic()

        for i, row in enumerate(rows):
            vector = embed_one(embedder, row.summary_text)
            database.update_summary_embedding(row.game_uuid, vector)
            if (i + 1) % 25 == 0 or i + 1 == len(rows):
                print(f"[{i + 1}/{len(rows)}]")

        database.set_index_meta(
            IndexMeta(
                embed_provider=embedder.name(),
                embed_model=embedder.model(),
                dimensions=embedder.dimensions(),
            )
        )

        print(f"Re-embedded {len(rows)} summaries in {time.monotonic() - start:.3f}s")


# ---------- zeitnot chat ----------


@app.command("chat")
def chat(
    username: Annotated[str, typer.Argument(help="Chess.com username to filter games")],
    chat_model: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Chat model for the selected CHAT_PROVIDER. Overrides CHAT_MODEL, "
                "so pass a model the provider actually offers."
            )
        ),
    ] = None,
) -> None:
    """Ask questions about your analyzed games."""
    from zeitnot.repl import run_repl

    database_url = _database_url()

    # Resolve providers and credentials before the welcome banner, so an auth or
    # model failure is never revealed only after the first question.
    try:
        cfg = config.resolve(config.os_env, chat_model or "")
    except config.ConfigError as err:
        _fail(str(err))
        return

    with _open_db(database_url) as database:
        try:
            model = cfg.new_chat_model()
            embedder = cfg.new_embedder()
            config.preflight(sys.stderr, model, embedder)
            # Chat only reads the index, so it never adopts an unstamped one.
            config.check_index(database, embedder, False, sys.stderr)
        except Exception as err:
            _fail(str(err))
            return

        service = Service(
            database,
            model,
            embedder,
            ChatConfig(
                chat_model=cfg.chat_model,
                username=username,
                num_similar=DEFAULT_NUM_SIMILAR,
                detail_limit=DEFAULT_DETAIL_LIMIT,
            ),
        )
        run_repl(service, username, cfg.summary())


if __name__ == "__main__":  # pragma: no cover
    app()
