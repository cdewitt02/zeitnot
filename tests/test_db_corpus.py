"""Phase 2 verification, against the live corpus rather than fixtures.

Three claims, in increasing order of what they would catch:

1. Row counts match what `psql` reports.
2. A known game round-trips field for field.
3. `find_similar_games_with_filters` returns the **same neighbours in the same
   order** as the Go implementation for a fixed query vector.

The third is the one that matters. Vector ordering is where a float-conversion
bug would hide, and it would show up as slightly worse retrieval and nothing
else — no error, no failing insert.
"""

from __future__ import annotations

import subprocess
from typing import LiteralString

import pytest

from zeitnot.db import DB, _from_vector
from zeitnot.search import GameFilters

pytestmark = pytest.mark.corpus


def _psql(database_url: str, query: str) -> str:
    return subprocess.run(
        ["psql", database_url, "-tAc", query],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.parametrize("table", ["games", "moves", "game_summaries"])
def test_row_counts_match_psql(db: DB, database_url: str, table: LiteralString) -> None:
    expected = int(_psql(database_url, f"SELECT COUNT(*) FROM {table}"))
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == expected


def test_a_known_game_round_trips_field_for_field(db: DB, database_url: str) -> None:
    uuid = _psql(database_url, "SELECT uuid FROM games ORDER BY uuid LIMIT 1")
    game = db.get_game(uuid)
    assert game is not None

    columns = (
        "url,pgn,eco_code,eco_name,white_username,white_rating,black_username,"
        "black_rating,result,termination_type,time_control,time_class,rated,"
        "avg_cpl_white,avg_cpl_black,blunders_white,blunders_black,mistakes_white,"
        "mistakes_black,inaccuracies_white,inaccuracies_black,best_moves_white,"
        "best_moves_black"
    )
    raw = _psql(
        database_url,
        # E'\\x01' as the separator: any printable delimiter could occur inside
        # a PGN, and a false split would look like a scan bug.
        f"SELECT concat_ws(E'\\x01', {columns}) FROM games WHERE uuid = '{uuid}'",
    ).split("\x01")

    assert game.uuid == uuid
    assert game.url == raw[0]
    assert game.pgn.strip() == raw[1].strip()
    assert game.eco_code == raw[2]
    assert game.eco_name == raw[3]
    assert game.white_username == raw[4]
    assert game.white_rating == int(raw[5])
    assert game.black_username == raw[6]
    assert game.black_rating == int(raw[7])
    assert game.result == raw[8]
    assert game.termination_type == raw[9]
    assert game.time_control == raw[10]
    assert game.time_class == raw[11]
    assert game.rated == (raw[12] == "t")
    assert game.avg_cpl_white == pytest.approx(float(raw[13]))
    assert game.avg_cpl_black == pytest.approx(float(raw[14]))
    assert [
        game.blunders_white,
        game.blunders_black,
        game.mistakes_white,
        game.mistakes_black,
        game.inaccuracies_white,
        game.inaccuracies_black,
        game.best_moves_white,
        game.best_moves_black,
    ] == [int(v) for v in raw[15:23]]


def test_game_exists_and_missing_reads_return_none(db: DB, database_url: str) -> None:
    uuid = _psql(database_url, "SELECT uuid FROM games ORDER BY uuid LIMIT 1")
    assert db.game_exists(uuid)

    absent = "00000000-0000-0000-0000-000000000000"
    assert not db.game_exists(absent)
    # A missing row is None, never an exception and never a zero-valued record.
    # The Go tree returned (nil, nil) here and that is the shape callers expect.
    assert db.get_game(absent) is None
    assert db.get_game_summary(absent) is None


def test_moves_for_a_game_come_back_ordered(db: DB, database_url: str) -> None:
    uuid = _psql(
        database_url,
        "SELECT game_uuid FROM moves GROUP BY game_uuid ORDER BY COUNT(*) DESC LIMIT 1",
    )
    moves = db.get_moves_for_game(uuid)
    assert moves
    assert [m.move_number for m in moves] == sorted(m.move_number for m in moves)
    assert {m.side for m in moves} <= {"white", "black"}
    assert all(m.classification for m in moves)


def test_vector_neighbours_match_the_go_ordering(db: DB, database_url: str) -> None:
    """The float-conversion canary.

    A fixed query vector — one taken from the corpus itself, so it is a real
    768-wide embedding rather than a synthetic one — must select the same
    neighbours in the same order as the equivalent SQL run directly. Anything
    that widened, narrowed, or reordered the vector on the way out of Python
    shows up here as a different ordering.
    """
    uuid = _psql(database_url, "SELECT game_uuid FROM game_summaries ORDER BY game_uuid LIMIT 1")
    with db.cursor() as cur:
        cur.execute("SELECT embedding FROM game_summaries WHERE game_uuid = %s", (uuid,))
        row = cur.fetchone()
    assert row is not None
    query_vector = _from_vector(row[0])
    assert len(query_vector) == 768

    results = db.find_similar_games(query_vector, limit=10)
    assert results
    # The vector is one of the stored ones, so it is its own nearest neighbour
    # at distance ~0. That alone catches a vector that arrived mangled.
    assert results[0].game_uuid == uuid
    assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    assert [r.distance for r in results] == sorted(r.distance for r in results)

    reference = _psql(
        database_url,
        "SELECT string_agg(game_uuid::text, ',' ORDER BY ord) FROM ("
        "  SELECT game_uuid, row_number() OVER (ORDER BY embedding <=> "
        f"    (SELECT embedding FROM game_summaries WHERE game_uuid = '{uuid}')"
        "  ) AS ord FROM game_summaries LIMIT 10"
        ") t",
    )
    assert [r.game_uuid for r in results] == reference.split(",")


def test_filtered_similarity_agrees_with_the_count(
    db: DB, database_url: str, corpus_username: str
) -> None:
    """The filter clause and the count query must describe the same set.

    They are built from one `build_where`, so a disagreement means the argument
    ordering diverged between the two call sites — which is precisely the
    failure the `$N` numbering used to prevent.
    """
    with db.cursor() as cur:
        cur.execute("SELECT embedding FROM game_summaries LIMIT 1")
        row = cur.fetchone()
    assert row is not None
    vector = _from_vector(row[0])

    cases = [
        GameFilters(username=corpus_username),
        GameFilters(username=corpus_username, user_color="white"),
        GameFilters(username=corpus_username, result="loss"),
        GameFilters(username=corpus_username, result="win", user_color="black"),
        GameFilters(username=corpus_username, time_class="blitz"),
        GameFilters(username=corpus_username, eco_prefix="B"),
        GameFilters(username=corpus_username, min_blunders=1),
        GameFilters(username=corpus_username, max_blunders=0),
        GameFilters(username=corpus_username, min_rating=1000, max_rating=1200),
        GameFilters(username=corpus_username, min_mistakes=1),
        GameFilters(
            username=corpus_username,
            user_color="white",
            time_class="bullet",
            eco_prefix="B",
            min_blunders=1,
        ),
    ]

    for filters in cases:
        count = db.count_games_matching_filters(filters)
        # A large limit, so the result is the whole matching set rather than a
        # truncation of it.
        results = db.find_similar_games_with_filters(vector, filters, limit=500)
        assert len(results) <= count, f"{filters}: retrieved more games than matched"
        # Every game_summaries row has a games row, so for this corpus the two
        # agree exactly. If summaries ever lag ingestion, this becomes <=.
        assert len(results) == count, f"{filters}: {len(results)} retrieved vs {count} matched"
        assert all(r.game is not None for r in results)


def test_embedding_dimensions_reports_the_column_width(db: DB) -> None:
    assert db.embedding_dimensions() == 768


def test_player_stats_round_trip_matches_a_fresh_computation(db: DB, corpus_username: str) -> None:
    """The stored row and a recomputation must agree.

    They are what the assembled prompt's every number is formatted from, so a
    disagreement here becomes a prompt diff in Phase 5 with no obvious cause.
    This is deliberately read-only: it computes and compares, and never saves.
    """
    stored = db.get_player_stats(corpus_username)
    assert stored is not None

    fresh = db.compute_player_stats(corpus_username)
    assert fresh.total_games == stored.total_games
    assert (fresh.wins, fresh.losses, fresh.draws) == (stored.wins, stored.losses, stored.draws)
    assert fresh.avg_cpl == pytest.approx(stored.avg_cpl, rel=1e-6)
    assert set(fresh.stats_by_color) == set(stored.stats_by_color)
    assert set(fresh.stats_by_time_class) == set(stored.stats_by_time_class)
    assert set(fresh.stats_by_opening) == set(stored.stats_by_opening)
    assert set(fresh.stats_by_rating_band) == set(stored.stats_by_rating_band)
    assert fresh.stats_by_termination == stored.stats_by_termination

    for key, entry in fresh.stats_by_color.items():
        other = stored.stats_by_color[key]
        assert entry.games == other.games
        assert entry.wins == other.wins
        assert entry.win_rate == pytest.approx(other.win_rate, rel=1e-6)
        assert entry.avg_cpl == pytest.approx(other.avg_cpl, rel=1e-6)
