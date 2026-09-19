"""The database layer, pointed at the *existing* Postgres corpus.

No schema change, no data store change, no re-ingestion. The SQL carries over
from `internal/db` intact in meaning, but **not verbatim**: pgx uses
PostgreSQL's native `$1, $2` placeholders and psycopg does not support them,
taking positional `%s` instead. Every literal `%` in these queries lives in an
*argument* (`prefix + "%"`), never in the SQL text, so psycopg's rule about
doubling a literal `%` never applies.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any, LiteralString

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import tuple_row
from psycopg_pool import ConnectionPool, PoolTimeout

from zeitnot.config import redact_secrets
from zeitnot.db.records import (
    GameRecord,
    GameSummary,
    IndexMeta,
    MoveRecord,
    OpeningStatsRow,
    SimilarGameResult,
    SummaryTextRow,
)
from zeitnot.db.schema import SCHEMA
from zeitnot.models import (
    ColorStats,
    OpeningStats,
    PeriodStats,
    PlayerStats,
    RatingBandStats,
    TimeClassStats,
    normalize_termination,
    rating_band,
)
from zeitnot.search.filters import GameFilters

__all__ = [
    "DB",
    "GameFilters",
    "GameRecord",
    "GameSummary",
    "IndexMeta",
    "MoveRecord",
    "OpeningStatsRow",
    "SimilarGameResult",
    "SummaryTextRow",
]

# The column list every GameRecord read shares. One definition, so a column
# added to one query cannot silently shift the unpacking in another.
_GAME_COLUMNS = """uuid, url, pgn, eco_code, eco_name,
       white_username, white_rating, black_username, black_rating,
       result, termination_type, time_control, time_class, rated,
       avg_cpl_white, avg_cpl_black,
       blunders_white, blunders_black,
       mistakes_white, mistakes_black,
       inaccuracies_white, inaccuracies_black,
       best_moves_white, best_moves_black"""

_MOVE_COLUMNS = """id, game_uuid, move_number, side, played_move, best_move,
       fen_before, evaluation, is_mate, mate_in, cpl, classification"""

# The same columns qualified for the join in get_moves_by_classification.
_MOVE_COLUMNS_M = """m.id, m.game_uuid, m.move_number, m.side, m.played_move, m.best_move,
       m.fen_before, m.evaluation, m.is_mate, m.mate_in, m.cpl, m.classification"""


def _game_from_row(row: Sequence[Any]) -> GameRecord:
    """Build a GameRecord from a `_GAME_COLUMNS` row.

    Nullable text columns come back as None; the Go scan targets were plain
    strings, so an empty string is what the rest of the code has always seen.
    """
    return GameRecord(
        uuid=str(row[0]),
        url=row[1] or "",
        pgn=row[2] or "",
        eco_code=row[3] or "",
        eco_name=row[4] or "",
        white_username=row[5] or "",
        white_rating=row[6] or 0,
        black_username=row[7] or "",
        black_rating=row[8] or 0,
        result=row[9] or "",
        termination_type=row[10] or "",
        time_control=row[11] or "",
        time_class=row[12] or "",
        rated=bool(row[13]),
        avg_cpl_white=float(row[14] or 0.0),
        avg_cpl_black=float(row[15] or 0.0),
        blunders_white=row[16] or 0,
        blunders_black=row[17] or 0,
        mistakes_white=row[18] or 0,
        mistakes_black=row[19] or 0,
        inaccuracies_white=row[20] or 0,
        inaccuracies_black=row[21] or 0,
        best_moves_white=row[22] or 0,
        best_moves_black=row[23] or 0,
    )


def _move_from_row(row: Sequence[Any]) -> MoveRecord:
    return MoveRecord(
        id=row[0],
        game_uuid=str(row[1]),
        move_number=row[2],
        side=row[3] or "",
        played_move=row[4] or "",
        best_move=row[5] or "",
        fen_before=row[6] or "",
        evaluation=row[7] or 0,
        is_mate=bool(row[8]),
        mate_in=row[9] or 0,
        cpl=row[10] or 0,
        classification=row[11] or "",
    )


# Long enough to cover the gap between `docker compose up -d` returning and
# Postgres accepting connections on a cold container.
_CONNECT_TIMEOUT = 30.0


class DBConnectionError(Exception):
    """The pool never came up. Carries the reason libpq actually gave."""


# Written by the pool's background threads through _RedactingLogFilter, read by
# _open_pool when the wait times out. A module-level holder rather than an
# instance attribute because the filter is installed once, on the shared
# "psycopg.pool" logger, before any DB exists.
_last_connect_error = ""


class _RedactingLogFilter(logging.Filter):
    """Blank credentials in psycopg_pool's own log records, and keep the last
    connection failure so it can be reported once instead of fifteen times.

    The pool logs connection failures itself, on the "psycopg.pool" logger,
    without passing through any of zeitnot's error paths. A malformed
    DATABASE_URL makes libpq quote the whole DSN back — `missing "=" after
    "postgres://user:password@host/db"` — which reaches stderr with the password
    intact. Redacting at zeitnot's own output sites does not cover this one,
    because zeitnot never sees the string.

    Those same records are also the only place the *reason* for a failed
    connection appears: the pool retries until the wait times out and then
    raises PoolTimeout, which knows nothing about refused connections or bad
    passwords. So the retry chatter is captured and swallowed here, and
    _open_pool raises it back as one actionable error.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        global _last_connect_error

        # Render args in first: the DSN arrives as an argument, not in msg.
        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        if record.msg.startswith("error connecting"):
            _last_connect_error = record.msg
            # Suppressed, not just redacted: one attempt per retry means this
            # line otherwise scrolls the eventual error off the screen. Any
            # other pool record still reaches stderr.
            return False
        return True


def _install_pool_log_redaction() -> None:
    logger = logging.getLogger("psycopg.pool")
    if not any(isinstance(f, _RedactingLogFilter) for f in logger.filters):
        logger.addFilter(_RedactingLogFilter())


def _connect_failure(seconds: float) -> DBConnectionError:
    """Turn a PoolTimeout into something a first-time setup can act on."""
    reason = _last_connect_error
    # "error connecting in 'pool-1': connection failed: ..." — the pool's own
    # framing is noise once this is the only line printed.
    _, _, detail = reason.partition(": ")
    detail = (detail or reason).strip().removeprefix("connection failed:").strip()
    lines = [f"could not connect to the database after {seconds:.0f}s"]
    # libpq's message runs to several lines, and repeats itself once per address
    # it tried — identical text for IPv4 and IPv6 on a localhost DSN.
    for line in detail.splitlines():
        line = f"  {line.strip()}"
        if line.strip() and line not in lines:
            lines.append(line)
    lines += [f"  {line}" for line in _hint(detail)]
    return DBConnectionError("\n".join(lines))


def _hint(detail: str) -> list[str]:
    """The remedy, chosen by what PostgreSQL actually objected to.

    These three have nothing to do with each other, and the wrong hint costs
    more than none: "check the port" reads as a dead end to someone whose port
    is already right.
    """
    if "does not exist" in detail and "database" in detail:
        # The server is up and the credentials work, so this is not a
        # connection problem at all. The postgres image runs POSTGRES_DB and
        # the pgvector script only when the data directory is empty, so a
        # volume left half-initialized — an interrupted first `docker compose
        # up`, typically — is never repaired by starting the container again.
        return [
            "The container is running and the credentials work, so this is a",
            "half-initialized data volume: PostgreSQL only creates POSTGRES_DB on a",
            "first start with an empty one, and no restart repairs it. Discard the",
            "volume and let it initialize again — this deletes any analyzed games:",
            "  docker compose down -v && docker compose up -d",
        ]
    if "authentication failed" in detail or "role" in detail:
        return [
            "Something answered, but it is not the zeitnot container — usually a",
            "PostgreSQL already on that port, which also stopped the container from",
            "binding it. Publish another port with ZEITNOT_DB_PORT and match it in",
            "DATABASE_URL; `docker compose ps` shows the published one.",
        ]
    return [
        "Check `docker compose ps` — nothing is listening on that port. The port it",
        "publishes is ZEITNOT_DB_PORT (default 5432), and DATABASE_URL has to name",
        "the same one.",
    ]


class DB:
    """A connection pool plus the queries zeitnot runs against it."""

    def __init__(self, conn_string: str = "") -> None:
        global _last_connect_error

        if not conn_string:
            conn_string = os.environ.get("DATABASE_URL", "")
        self._conn_string = conn_string
        _install_pool_log_redaction()
        _last_connect_error = ""
        self._pool = self._open_pool()

    def _open_pool(self) -> ConnectionPool[psycopg.Connection[Any]]:
        def configure(conn: psycopg.Connection[Any]) -> None:
            # pgvector's adapters are registered per connection, so this has to
            # be the pool's configure hook rather than a one-off call.
            #
            # A brand-new database has no `vector` type yet — migrate() is what
            # creates the extension — and registration against a missing type
            # raises. Tolerating that is what lets DB() open at all on an empty
            # database; migrate() reopens the pool afterwards so every
            # connection ends up with the adapters. Swallowing it here would be
            # wrong on a *migrated* database, which is why migrate() reopens
            # rather than leaving it to chance.
            try:
                register_vector(conn)
            except psycopg.ProgrammingError:
                # "vector type not found in the database" — the extension is
                # not installed yet. Any other ProgrammingError would be a real
                # problem, but pgvector raises this bare type for both, so the
                # narrow catch is not available.
                conn.rollback()

        pool: ConnectionPool[psycopg.Connection[Any]] = ConnectionPool(
            self._conn_string,
            configure=configure,
            open=True,
            kwargs={"row_factory": tuple_row},
        )
        # Verify the connection now rather than at the first query, so a bad
        # DATABASE_URL is a startup error. The wait is generous because
        # `docker compose up -d` returns before Postgres accepts connections,
        # and a first run that races the container should retry, not fail.
        try:
            pool.wait(timeout=_CONNECT_TIMEOUT)
        except PoolTimeout as err:
            pool.close()
            raise _connect_failure(_CONNECT_TIMEOUT) from err
        return pool

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> DB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def cursor(self) -> Generator[psycopg.Cursor[Any], None, None]:
        """A cursor on a pooled connection, committed on clean exit."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            yield cur

    # ---------- schema ----------

    def migrate(self) -> None:
        """Idempotent, and identical to the Go version — including
        `CREATE EXTENSION IF NOT EXISTS vector`."""
        # One execute with many statements: psycopg falls back to the simple
        # query protocol when there are no parameters, which is what allows it.
        with self.cursor() as cur:
            cur.execute(SCHEMA)

        # Reopen so every pooled connection registers the pgvector adapters.
        # On a database that already had the extension this is a no-op in
        # effect; on a fresh one it is what makes the first insert work.
        old, self._pool = self._pool, self._open_pool()
        old.close()

    def corpus_is_initialized(self) -> bool:
        """Whether `migrate` has ever run against this database.

        Read-only, and asked by the commands that do *not* migrate. `zeitnot
        chat` is the one people reach for first — before analyzing anything, or
        pointed at a database that was never ingested into — and without this it
        answered the first question with a raw `relation "player_stats" does not
        exist`, then printed "Thinking..." and nothing else.
        """
        with self.cursor() as cur:
            cur.execute("SELECT to_regclass('games') IS NOT NULL")
            row = cur.fetchone()
        return bool(row and row[0])

    # ---------- games ----------

    def save_game(self, game: GameRecord) -> None:
        query = """
            INSERT INTO games (
                uuid, url, pgn, eco_code, eco_name,
                white_username, white_rating, black_username, black_rating,
                result, termination_type, time_control, time_class, rated,
                avg_cpl_white, avg_cpl_black,
                blunders_white, blunders_black,
                mistakes_white, mistakes_black,
                inaccuracies_white, inaccuracies_black,
                best_moves_white, best_moves_black, played_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (uuid) DO UPDATE SET
                pgn = EXCLUDED.pgn,
                avg_cpl_white = EXCLUDED.avg_cpl_white,
                avg_cpl_black = EXCLUDED.avg_cpl_black,
                blunders_white = EXCLUDED.blunders_white,
                blunders_black = EXCLUDED.blunders_black,
                mistakes_white = EXCLUDED.mistakes_white,
                mistakes_black = EXCLUDED.mistakes_black,
                inaccuracies_white = EXCLUDED.inaccuracies_white,
                inaccuracies_black = EXCLUDED.inaccuracies_black,
                best_moves_white = EXCLUDED.best_moves_white,
                best_moves_black = EXCLUDED.best_moves_black,
                termination_type = EXCLUDED.termination_type,
                played_at = EXCLUDED.played_at
        """
        with self.cursor() as cur:
            cur.execute(
                query,
                (
                    game.uuid, game.url, game.pgn, game.eco_code, game.eco_name,
                    game.white_username, game.white_rating,
                    game.black_username, game.black_rating,
                    game.result, game.termination_type, game.time_control,
                    game.time_class, game.rated,
                    game.avg_cpl_white, game.avg_cpl_black,
                    game.blunders_white, game.blunders_black,
                    game.mistakes_white, game.mistakes_black,
                    game.inaccuracies_white, game.inaccuracies_black,
                    game.best_moves_white, game.best_moves_black, game.played_at,
                ),
            )  # fmt: skip

    def get_game(self, uuid: str) -> GameRecord | None:
        with self.cursor() as cur:
            cur.execute(f"SELECT {_GAME_COLUMNS} FROM games WHERE uuid = %s", (uuid,))
            row = cur.fetchone()
        return None if row is None else _game_from_row(row)

    def game_exists(self, uuid: str) -> bool:
        with self.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM games WHERE uuid = %s)", (uuid,))
            row = cur.fetchone()
        return bool(row and row[0])

    def canonical_username(self, typed: str) -> str | None:
        """The corpus's own spelling of a player name, matched case-insensitively.

        The read commands never see an archive payload, so this is where they
        resolve what `zeitnot.models.canonical_username` resolves from one. The
        stored spelling is authoritative for both: `white_username` and
        `black_username` are written straight from the Chess.com JSON, so a
        corpus ingested under a mistyped case still holds the registered
        capitalization on every row — it is only the comparison that was ever
        wrong.

        This is the one predicate in the module that wraps an indexed column in
        `LOWER()`, and so the one that cannot use `idx_games_white_username`. It
        runs **once per invocation**, before any query the user waits on, which
        is what buys every other predicate the right to stay an exact match.

        `None` when no game names the player at all — the case that used to
        present as `Stats updated: 0 total games` with nothing to explain it.
        """
        with self.cursor() as cur:
            cur.execute(
                """SELECT CASE WHEN LOWER(white_username) = LOWER(%(u)s)
                                THEN white_username ELSE black_username END
                   FROM games
                   WHERE LOWER(white_username) = LOWER(%(u)s)
                      OR LOWER(black_username) = LOWER(%(u)s)
                   LIMIT 1""",
                {"u": typed.strip()},
            )
            row = cur.fetchone()
        return str(row[0]) if row else None

    def get_games_by_eco(self, eco_prefix: str, limit: int) -> list[GameRecord]:
        with self.cursor() as cur:
            cur.execute(
                f"""SELECT {_GAME_COLUMNS} FROM games
                    WHERE eco_code LIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s""",
                (eco_prefix + "%", limit),
            )
            return [_game_from_row(row) for row in cur.fetchall()]

    def get_games_by_player(self, username: str, limit: int) -> list[GameRecord]:
        with self.cursor() as cur:
            cur.execute(
                f"""SELECT {_GAME_COLUMNS} FROM games
                    WHERE white_username = %s OR black_username = %s
                    ORDER BY created_at DESC
                    LIMIT %s""",
                (username, username, limit),
            )
            return [_game_from_row(row) for row in cur.fetchall()]

    def get_opening_stats(self, username: str) -> list[OpeningStatsRow]:
        query = """
            SELECT
                eco_code,
                MAX(eco_name) as eco_name,
                COUNT(*) FILTER (WHERE white_username = %(u)s) as games_as_white,
                COUNT(*) FILTER (WHERE black_username = %(u)s) as games_as_black,
                COUNT(*) FILTER (WHERE white_username = %(u)s AND result = 'white')
                    as wins_as_white,
                COUNT(*) FILTER (WHERE black_username = %(u)s AND result = 'black')
                    as wins_as_black,
                COALESCE(AVG(avg_cpl_white) FILTER (WHERE white_username = %(u)s), 0)
                    as avg_cpl_white,
                COALESCE(AVG(avg_cpl_black) FILTER (WHERE black_username = %(u)s), 0)
                    as avg_cpl_black
            FROM games
            WHERE white_username = %(u)s OR black_username = %(u)s
            GROUP BY eco_code
            ORDER BY (COUNT(*) FILTER (WHERE white_username = %(u)s)
                      + COUNT(*) FILTER (WHERE black_username = %(u)s)) DESC
        """
        # Named placeholders here rather than positional: the username appears
        # eight times, and repeating it eight times in the argument tuple would
        # be a transcription bug waiting to happen. Everywhere the count is
        # small, positional stays.
        with self.cursor() as cur:
            cur.execute(query, {"u": username})
            return [
                OpeningStatsRow(
                    eco_code=row[0] or "",
                    eco_name=row[1] or "",
                    games_as_white=row[2],
                    games_as_black=row[3],
                    wins_as_white=row[4],
                    wins_as_black=row[5],
                    avg_cpl_as_white=float(row[6]),
                    avg_cpl_as_black=float(row[7]),
                )
                for row in cur.fetchall()
            ]

    # ---------- moves ----------

    def save_moves(self, moves: Sequence[MoveRecord]) -> None:
        if not moves:
            return
        # COPY, the psycopg equivalent of pgx's CopyFrom. A game is 60-120 rows
        # and ingestion writes one game at a time, so this is about keeping the
        # write shape identical rather than about throughput.
        with (
            self.cursor() as cur,
            cur.copy(
                """COPY moves (
                   game_uuid, move_number, side, played_move, best_move,
                   fen_before, evaluation, is_mate, mate_in, cpl, classification
               ) FROM STDIN"""
            ) as copy,
        ):
            for m in moves:
                copy.write_row(
                    (
                        m.game_uuid, m.move_number, m.side, m.played_move, m.best_move,
                        m.fen_before, m.evaluation, m.is_mate, m.mate_in, m.cpl,
                        m.classification,
                    )
                )  # fmt: skip

    def get_moves_for_game(self, game_uuid: str) -> list[MoveRecord]:
        with self.cursor() as cur:
            cur.execute(
                # move_number is the *full* move, so two rows share every value
                # and ordering by it alone leaves the tie to the planner. It
                # does get broken the wrong way in practice — observed on a real
                # corpus returning Black's move 4 ahead of White's. Every caller
                # reads this list as plies and takes the side from the index
                # parity, so a swapped pair credits one player's mistakes to the
                # other. The second key is what makes the order a ply order.
                f"""SELECT {_MOVE_COLUMNS} FROM moves
                    WHERE game_uuid = %s
                    ORDER BY move_number, (side = 'black')""",
                (game_uuid,),
            )
            return [_move_from_row(row) for row in cur.fetchall()]

    def get_blunders_for_player(self, username: str, limit: int) -> list[MoveRecord]:
        return self.get_moves_by_classification(username, "blunder", limit)

    def get_moves_by_classification(
        self, username: str, classification: str, limit: int
    ) -> list[MoveRecord]:
        # The Go tree had GetBlundersForPlayer and GetMovesByClassification as
        # two near-identical queries differing only in a literal 'blunder'.
        # One query, one argument.
        query = f"""
            SELECT {_MOVE_COLUMNS_M}
            FROM moves m
            JOIN games g ON m.game_uuid = g.uuid
            WHERE m.classification = %s
              AND ((m.side = 'white' AND g.white_username = %s)
                OR (m.side = 'black' AND g.black_username = %s))
            ORDER BY m.cpl DESC
            LIMIT %s
        """
        with self.cursor() as cur:
            cur.execute(query, (classification, username, username, limit))
            return [_move_from_row(row) for row in cur.fetchall()]

    def delete_moves_for_game(self, game_uuid: str) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM moves WHERE game_uuid = %s", (game_uuid,))

    # ---------- summaries and similarity ----------

    def save_game_summary(
        self, game_uuid: str, summary_text: str, embedding: Sequence[float]
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO game_summaries (game_uuid, summary_text, embedding)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (game_uuid) DO UPDATE SET
                       summary_text = EXCLUDED.summary_text,
                       embedding = EXCLUDED.embedding""",
                (game_uuid, summary_text, _vector(embedding)),
            )

    def get_game_summary(self, game_uuid: str) -> GameSummary | None:
        with self.cursor() as cur:
            cur.execute(
                """SELECT game_uuid, summary_text, embedding
                   FROM game_summaries WHERE game_uuid = %s""",
                (game_uuid,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return GameSummary(
            game_uuid=str(row[0]),
            summary_text=row[1] or "",
            embedding=_from_vector(row[2]),
        )

    def find_similar_games(
        self, query_embedding: Sequence[float], limit: int
    ) -> list[SimilarGameResult]:
        with self.cursor() as cur:
            cur.execute(
                """SELECT gs.game_uuid, gs.summary_text, gs.embedding <=> %s AS distance
                   FROM game_summaries gs
                   ORDER BY distance
                   LIMIT %s""",
                (_vector(query_embedding), limit),
            )
            return [
                SimilarGameResult(
                    game_uuid=str(row[0]), summary_text=row[1] or "", distance=float(row[2])
                )
                for row in cur.fetchall()
            ]

    def find_similar_games_with_filters(
        self, query_embedding: Sequence[float], filters: GameFilters, limit: int
    ) -> list[SimilarGameResult]:
        """Vector similarity combined with the dynamic WHERE clause.

        The vector goes first in the argument sequence because it is first in
        the SELECT list, the filter arguments follow the WHERE they belong to,
        and the limit is last. Under `$N` that ordering was carried by an index
        threaded through `BuildWHERE`; here it is carried by the list itself.
        """
        base: LiteralString = """
            SELECT gs.game_uuid, gs.summary_text, gs.embedding <=> %s AS distance,
                   g.uuid, g.url, g.pgn, g.eco_code, g.eco_name,
                   g.white_username, g.white_rating, g.black_username, g.black_rating,
                   g.result, g.time_control, g.time_class, g.rated,
                   g.avg_cpl_white, g.avg_cpl_black,
                   g.blunders_white, g.blunders_black,
                   g.mistakes_white, g.mistakes_black,
                   g.inaccuracies_white, g.inaccuracies_black,
                   g.best_moves_white, g.best_moves_black
            FROM game_summaries gs
            JOIN games g ON gs.game_uuid = g.uuid
        """
        where = filters.build_where()
        query: LiteralString = base + (f" WHERE {where.clause}" if where.clause else "")
        query += " ORDER BY distance LIMIT %s"
        args: list[Any] = [_vector(query_embedding), *where.args, limit]

        with self.cursor() as cur:
            cur.execute(query, args)
            results: list[SimilarGameResult] = []
            for row in cur.fetchall():
                # The GameRecord columns here omit termination_type and
                # played_at, matching the Go query, so the row cannot be handed
                # to _game_from_row.
                game = GameRecord(
                    uuid=str(row[3]),
                    url=row[4] or "",
                    pgn=row[5] or "",
                    eco_code=row[6] or "",
                    eco_name=row[7] or "",
                    white_username=row[8] or "",
                    white_rating=row[9] or 0,
                    black_username=row[10] or "",
                    black_rating=row[11] or 0,
                    result=row[12] or "",
                    time_control=row[13] or "",
                    time_class=row[14] or "",
                    rated=bool(row[15]),
                    avg_cpl_white=float(row[16] or 0.0),
                    avg_cpl_black=float(row[17] or 0.0),
                    blunders_white=row[18] or 0,
                    blunders_black=row[19] or 0,
                    mistakes_white=row[20] or 0,
                    mistakes_black=row[21] or 0,
                    inaccuracies_white=row[22] or 0,
                    inaccuracies_black=row[23] or 0,
                    best_moves_white=row[24] or 0,
                    best_moves_black=row[25] or 0,
                )
                results.append(
                    SimilarGameResult(
                        game_uuid=str(row[0]),
                        summary_text=row[1] or "",
                        distance=float(row[2]),
                        game=game,
                    )
                )
            return results

    def count_games_matching_filters(self, filters: GameFilters) -> int:
        """How restrictive the filters are, before running the vector search."""
        where = filters.build_where()
        query: LiteralString = "SELECT COUNT(*) FROM games g"
        if where.clause:
            query += f" WHERE {where.clause}"
        with self.cursor() as cur:
            cur.execute(query, where.args)
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def count_games_with_summaries(self) -> int:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM game_summaries")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def games_without_summaries(self, limit: int) -> list[str]:
        with self.cursor() as cur:
            cur.execute(
                """SELECT g.uuid
                   FROM games g
                   LEFT JOIN game_summaries gs ON g.uuid = gs.game_uuid
                   WHERE gs.game_uuid IS NULL
                   LIMIT %s""",
                (limit,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    # ---------- index provenance ----------

    def get_index_meta(self) -> IndexMeta | None:
        """The recorded provenance, or None when the index carries no stamp —
        either because it predates provenance or because the table does not
        exist yet (`zeitnot chat` never runs migrations)."""
        try:
            with self.cursor() as cur:
                cur.execute(
                    "SELECT embed_provider, embed_model, dimensions FROM index_meta WHERE id = 1"
                )
                row = cur.fetchone()
        except psycopg.errors.UndefinedTable:
            return None
        if row is None:
            return None
        return IndexMeta(embed_provider=row[0], embed_model=row[1], dimensions=row[2])

    def set_index_meta(self, meta: IndexMeta) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO index_meta (id, embed_provider, embed_model, dimensions, updated_at)
                   VALUES (1, %s, %s, %s, NOW())
                   ON CONFLICT (id) DO UPDATE SET
                       embed_provider = EXCLUDED.embed_provider,
                       embed_model    = EXCLUDED.embed_model,
                       dimensions     = EXCLUDED.dimensions,
                       updated_at     = NOW()""",
                (meta.embed_provider, meta.embed_model, meta.dimensions),
            )

    def embedding_dimensions(self) -> int:
        """The declared width of `game_summaries.embedding`, so a mismatch is a
        startup message rather than a mid-ingestion insert error."""
        with self.cursor() as cur:
            cur.execute(
                """SELECT a.atttypmod
                   FROM pg_attribute a
                   JOIN pg_class c ON c.oid = a.attrelid
                   WHERE c.relname = 'game_summaries' AND a.attname = 'embedding'"""
            )
            row = cur.fetchone()
        if row is None:
            return 0
        dims = int(row[0])
        return 0 if dims < 0 else dims  # a negative atttypmod is an unconstrained column

    def all_summary_texts(self) -> list[SummaryTextRow]:
        """Every stored summary.

        Re-embedding is bounded work: summaries are generated deterministically
        with no LLM and no Stockfish, so a provider swap reads stored text and
        updates vectors rather than re-running analysis.
        """
        with self.cursor() as cur:
            cur.execute("SELECT game_uuid, summary_text FROM game_summaries")
            return [
                SummaryTextRow(game_uuid=str(row[0]), summary_text=row[1] or "")
                for row in cur.fetchall()
            ]

    def update_summary_embedding(self, game_uuid: str, embedding: Sequence[float]) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE game_summaries SET embedding = %s WHERE game_uuid = %s",
                (_vector(embedding), game_uuid),
            )

    # ---------- player stats ----------

    def save_player_stats(self, stats: PlayerStats) -> None:
        query = """
            INSERT INTO player_stats (
                username, total_games, wins, losses, draws, avg_cpl,
                stats_by_color, stats_by_time_class, stats_by_opening,
                stats_by_rating_band, stats_by_termination,
                last_30_days, last_90_days, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                total_games = EXCLUDED.total_games,
                wins = EXCLUDED.wins,
                losses = EXCLUDED.losses,
                draws = EXCLUDED.draws,
                avg_cpl = EXCLUDED.avg_cpl,
                stats_by_color = EXCLUDED.stats_by_color,
                stats_by_time_class = EXCLUDED.stats_by_time_class,
                stats_by_opening = EXCLUDED.stats_by_opening,
                stats_by_rating_band = EXCLUDED.stats_by_rating_band,
                stats_by_termination = EXCLUDED.stats_by_termination,
                last_30_days = EXCLUDED.last_30_days,
                last_90_days = EXCLUDED.last_90_days,
                updated_at = EXCLUDED.updated_at
        """
        with self.cursor() as cur:
            cur.execute(
                query,
                (
                    stats.username,
                    stats.total_games,
                    stats.wins,
                    stats.losses,
                    stats.draws,
                    stats.avg_cpl,
                    _dump(_sorted_map({k: v.to_json() for k, v in stats.stats_by_color.items()})),
                    _dump(
                        _sorted_map({k: v.to_json() for k, v in stats.stats_by_time_class.items()})
                    ),
                    _dump(_sorted_map({k: v.to_json() for k, v in stats.stats_by_opening.items()})),
                    _dump(
                        _sorted_map({k: v.to_json() for k, v in stats.stats_by_rating_band.items()})
                    ),
                    _dump(_sorted_map(stats.stats_by_termination)),
                    _dump(stats.last_30_days.to_json() if stats.last_30_days else None),
                    _dump(stats.last_90_days.to_json() if stats.last_90_days else None),
                    stats.updated_at,
                ),
            )

    def get_player_stats(self, username: str) -> PlayerStats | None:
        with self.cursor() as cur:
            cur.execute(
                """SELECT username, total_games, wins, losses, draws, avg_cpl,
                          stats_by_color, stats_by_time_class, stats_by_opening,
                          stats_by_rating_band, stats_by_termination,
                          last_30_days, last_90_days, updated_at
                   FROM player_stats
                   WHERE username = %s""",
                (username,),
            )
            row = cur.fetchone()
        if row is None:
            return None

        stats = PlayerStats(
            username=row[0],
            total_games=row[1] or 0,
            wins=row[2] or 0,
            losses=row[3] or 0,
            draws=row[4] or 0,
            avg_cpl=float(row[5] or 0.0),
            updated_at=row[13],
        )
        stats.stats_by_color = {k: ColorStats.from_json(v) for k, v in _load(row[6]).items()}
        stats.stats_by_time_class = {
            k: TimeClassStats.from_json(v) for k, v in _load(row[7]).items()
        }
        stats.stats_by_opening = {k: OpeningStats.from_json(v) for k, v in _load(row[8]).items()}
        stats.stats_by_rating_band = {
            k: RatingBandStats.from_json(v) for k, v in _load(row[9]).items()
        }
        stats.stats_by_termination = {k: int(v) for k, v in _load(row[10]).items()}
        period_30 = _load(row[11])
        period_90 = _load(row[12])
        stats.last_30_days = PeriodStats.from_json(period_30) if period_30 else None
        stats.last_90_days = PeriodStats.from_json(period_90) if period_90 else None
        return stats

    def compute_player_stats(self, username: str) -> PlayerStats:
        """Aggregate every game for a player, from scratch.

        A full recomputation rather than an incremental update. The dimensional
        averages use the same running-mean update the Go version used —
        `avg += (value - avg) / n` — because switching to a two-pass mean would
        change the stored numbers in the last decimal place, and those numbers
        are formatted into the assembled prompt.
        """
        stats = PlayerStats(username=username, updated_at=datetime.now().astimezone())
        stats.stats_by_color = {"white": ColorStats(), "black": ColorStats()}

        with self.cursor() as cur:
            cur.execute(
                """SELECT
                       white_username, black_username,
                       white_rating, black_rating,
                       result, termination_type, time_class,
                       eco_code, eco_name,
                       -- ::float8 rather than the bare REAL columns. In text
                       -- mode Postgres prints a float4 with float4 precision,
                       -- so float() would land on a different double than the
                       -- widened float32 pgx produced — and that difference
                       -- propagates through the running mean into stored JSON.
                       -- Widening server-side reproduces the Go read exactly.
                       avg_cpl_white::float8, avg_cpl_black::float8
                   FROM games
                   WHERE white_username = %s OR black_username = %s
                   -- Ordered because the dimensional averages are computed
                   -- with a running mean, which is not associative in floating
                   -- point: the same games in a different order produce a
                   -- value that differs in the last ulp. Verified — the Go
                   -- tree produced different avg_cpl figures for identical
                   -- games held in a different heap order.
                   ORDER BY uuid""",
                (username, username),
            )
            rows = cur.fetchall()

        total_cpl = 0.0
        games_with_cpl = 0

        for row in rows:
            (
                white_username,
                _black_username,
                white_rating,
                black_rating,
                result,
                termination_type,
                time_class,
                eco_code,
                eco_name,
                avg_cpl_white,
                avg_cpl_black,
            ) = row

            if white_username == username:
                player_color = "white"
                opponent_rating = black_rating or 0
                player_cpl = float(avg_cpl_white or 0.0)
            else:
                player_color = "black"
                opponent_rating = white_rating or 0
                player_cpl = float(avg_cpl_black or 0.0)

            player_won = player_lost = False
            if result == "white":
                player_won = player_color == "white"
                player_lost = player_color == "black"
            elif result == "black":
                player_won = player_color == "black"
                player_lost = player_color == "white"

            stats.total_games += 1
            if player_won:
                stats.wins += 1
            elif player_lost:
                stats.losses += 1
            else:
                stats.draws += 1

            if player_cpl > 0:
                total_cpl += player_cpl
                games_with_cpl += 1

            _tally(stats.stats_by_color[player_color], player_won, player_lost, player_cpl)

            if time_class:
                bucket = stats.stats_by_time_class.setdefault(time_class, TimeClassStats())
                _tally(bucket, player_won, player_lost, player_cpl)

            if eco_code:
                opening = stats.stats_by_opening.get(eco_code)
                if opening is None:
                    opening = OpeningStats(eco_code=eco_code, opening_name=eco_name or "")
                    stats.stats_by_opening[eco_code] = opening
                _tally(opening, player_won, player_lost, player_cpl)

            band = stats.stats_by_rating_band.setdefault(
                rating_band(opponent_rating), RatingBandStats()
            )
            _tally(band, player_won, player_lost, player_cpl)

            if termination_type:
                # Normalized before tallying, not before printing: the raw
                # string names the winning player, so bucketing on it leaks a
                # third party's handle into player_stats and from there into
                # every aggregate prompt.
                player_result = "won" if player_won else "lost" if player_lost else "drew"
                key = normalize_termination(termination_type, player_result)
                if key:
                    stats.stats_by_termination[key] = stats.stats_by_termination.get(key, 0) + 1

        if games_with_cpl > 0:
            stats.avg_cpl = total_cpl / games_with_cpl

        for group in (
            stats.stats_by_color,
            stats.stats_by_time_class,
            stats.stats_by_opening,
            stats.stats_by_rating_band,
        ):
            for entry in group.values():
                if entry.games > 0:
                    entry.win_rate = entry.wins / entry.games * 100

        return stats

    def compute_period_stats(self, username: str, days: int) -> PeriodStats:
        """Stats for games played in the last N days."""
        query = """
            SELECT
                COUNT(*) as games,
                COUNT(*) FILTER (WHERE
                    (white_username = %(u)s AND result = 'white') OR
                    (black_username = %(u)s AND result = 'black')
                ) as wins,
                COUNT(*) FILTER (WHERE
                    (white_username = %(u)s AND result = 'black') OR
                    (black_username = %(u)s AND result = 'white')
                ) as losses,
                COUNT(*) FILTER (WHERE result = 'draw') as draws,
                COALESCE(AVG(
                    CASE
                        WHEN white_username = %(u)s THEN avg_cpl_white
                        ELSE avg_cpl_black
                    END
                ) FILTER (WHERE
                    CASE
                        WHEN white_username = %(u)s THEN avg_cpl_white
                        ELSE avg_cpl_black
                    END > 0
                ), 0) as avg_cpl
            FROM games
            WHERE (white_username = %(u)s OR black_username = %(u)s)
              AND played_at >= NOW() - INTERVAL '1 day' * %(days)s
        """
        with self.cursor() as cur:
            cur.execute(query, {"u": username, "days": days})
            row = cur.fetchone()
        if row is None:  # COUNT always returns a row; this is for the type checker
            return PeriodStats()
        stats = PeriodStats(
            games=row[0],
            wins=row[1],
            losses=row[2],
            draws=row[3],
            avg_cpl=float(row[4] or 0.0),
        )
        if stats.games > 0:
            stats.win_rate = stats.wins / stats.games * 100
        return stats

    def refresh_player_stats(self, username: str) -> PlayerStats:
        """Compute and save the player stats in one operation."""
        stats = self.compute_player_stats(username)
        stats.last_30_days = self.compute_period_stats(username, 30)
        stats.last_90_days = self.compute_period_stats(username, 90)
        self.save_player_stats(stats)
        return stats


def _tally(entry: Any, won: bool, lost: bool, cpl: float) -> None:
    """One game's contribution to a dimensional bucket."""
    entry.games += 1
    if won:
        entry.wins += 1
    elif lost:
        entry.losses += 1
    else:
        entry.draws += 1
    if cpl > 0:
        # Running mean: new_avg = old_avg + (new_value - old_avg) / n.
        entry.avg_cpl += (cpl - entry.avg_cpl) / entry.games


def _vector(values: Sequence[float]) -> Vector:
    """Wrap a sequence of floats as a pgvector value.

    A plain list is adapted as `double precision[]`, which has no `<=>`
    operator, so this is what makes the similarity queries run at all. It is
    also the one conversion the stored corpus depends on: `vector` is float4, so
    Postgres narrows on the way in exactly as it did for pgx.
    """
    return Vector(list(values))


def _from_vector(value: Any) -> list[float]:
    """Coerce a stored embedding back to a plain list of floats.

    pgvector hands back its own Vector type, which is not iterable. Reading the
    width or the values off one is the only place the port touches raw vector
    data, and getting it wrong would be silent: a mangled query vector still
    returns ten neighbours, just the wrong ten.
    """
    if value is None:
        return []
    if hasattr(value, "to_list"):
        return [float(v) for v in value.to_list()]
    return [float(v) for v in value]


def _dump(value: Any) -> str:
    """Serialize to the byte-identical JSON the Go tree wrote.

    Two of encoding/json's habits have to be reproduced, because these strings
    are stored and a `git diff` of a dumped table would otherwise be noise:

    - **Map keys are sorted; struct fields are not.** Go sorts only when
      marshalling a map, and emits struct fields in declaration order. So the
      outer dicts here — keyed by color, time class, ECO code, rating band —
      are sorted by the caller, while each value's field order comes from its
      `to_json`. Passing `sort_keys=True` would sort both and reorder every
      inner object.
    - **`<`, `>`, and `&` are escaped.** encoding/json HTML-escapes them by
      default, which matters here because the "<1000" rating band is a map key.
    """
    text = json.dumps(_go_floats(value), separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _go_floats(value: Any) -> Any:
    """Rewrite integral floats as ints, ahead of serialization.

    Go's encoding/json emits an integral float64 without a fractional part —
    `25`, where Python writes `25.0`. A win rate of exactly 25% is common, so
    this is not an edge case. Rewriting the value is the reliable way to get
    there: json's C encoder ignores a subclass's float handling.
    """
    if isinstance(value, dict):
        return {k: _go_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_go_floats(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _sorted_map(values: dict[str, Any]) -> dict[str, Any]:
    """Reorder a dict by key, so `_dump` reproduces Go's map ordering."""
    return {key: values[key] for key in sorted(values)}


def _load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return {}
    return parsed
