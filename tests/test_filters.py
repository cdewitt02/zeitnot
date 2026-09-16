"""Ported from the BuildWHERE / Clone / IsEmpty / String half of parser_test.go.

The argument *counts* differ from the Go table on purpose. Under `$N` the same
parameter index could be referenced by two placeholders, so the username was
appended once for `(white = $1 OR black = $1)`; psycopg's positional `%s` has no
indices, so the value is passed once per placeholder. What has to match is the
result set, which `test_db_corpus.py` asserts against the live corpus.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from zeitnot.search import GameFilters
from zeitnot.search.parser import QueryParser


@pytest.mark.parametrize(
    ("filters", "want_clause", "want_args"),
    [
        pytest.param(GameFilters(username="testuser"), True, 2, id="username only"),
        pytest.param(
            GameFilters(username="testuser", result="loss"),
            True,
            # username twice for the base filter, twice more for the
            # result translation that has no color to lean on.
            4,
            id="username and result",
        ),
        pytest.param(
            GameFilters(username="testuser", result="loss", user_color="white"),
            True,
            # With the color known the result is one literal, not a disjunction.
            4,
            id="result and color collapse to an exact match",
        ),
        pytest.param(
            GameFilters(username="testuser", eco_prefix="B"), True, 3, id="eco prefix filter"
        ),
        pytest.param(
            GameFilters(username="testuser", date_from=datetime.now() - timedelta(days=7)),
            True,
            3,
            id="date range filter",
        ),
        pytest.param(GameFilters(), False, 0, id="empty filters"),
    ],
)
def test_build_where(filters: GameFilters, want_clause: bool, want_args: int) -> None:
    result = filters.build_where()
    assert bool(result.clause) is want_clause
    assert len(result.args) == want_args
    # Every placeholder must have exactly one argument. This is the invariant
    # the numbering machinery used to carry, and losing it silently is the one
    # way this rewrite could go wrong.
    assert result.clause.count("%s") == len(result.args)


def test_result_translation_by_color() -> None:
    for color, want in (("white", "black"), ("black", "white")):
        result = GameFilters(username="u", result="loss", user_color=color).build_where()
        assert want in result.args, f"a loss as {color} is a win for {want}"

    for color in ("white", "black"):
        result = GameFilters(username="u", result="win", user_color=color).build_where()
        assert color in result.args


def test_like_wildcards_stay_in_the_arguments() -> None:
    """psycopg requires a literal `%` in SQL text to be doubled.

    Every wildcard here lives in an argument instead, which is why no escaping
    sweep was needed. If one ever migrates into the clause, this fails.
    """
    filters = GameFilters(username="u", eco_prefix="B", opening_name="Sicilian")
    result = filters.build_where()
    assert result.clause.replace("%s", "").count("%") == 0
    assert "B%" in result.args
    assert "%sicilian%" in result.args


def test_is_empty() -> None:
    assert GameFilters().is_empty()
    assert GameFilters(username="testuser").is_empty(), "the username alone is not a filter"
    assert not GameFilters(result="win").is_empty()
    assert not GameFilters(eco_prefix="B").is_empty()


def test_clone_is_independent() -> None:
    original = GameFilters(
        username="testuser",
        result="win",
        user_color="white",
        time_class="blitz",
        eco_prefix="B",
        min_blunders=1,
    )
    clone = original.clone()
    assert clone == original

    clone.result = "loss"
    assert original.result == "win", "clone modified original — not an independent copy"


def test_str() -> None:
    text = str(GameFilters(result="loss", user_color="black", time_class="blitz"))
    assert "result=loss" in text
    assert "color=black" in text
    assert "time=blitz" in text
    assert str(GameFilters()) == "no filters"


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (datetime(2026, 9, 1, tzinfo=UTC), None, [2, 3, 4]),
        (None, datetime(2026, 9, 15, tzinfo=UTC), [1, 2, 3]),
        (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 15, tzinfo=UTC), [2, 3]),
    ],
)
def test_date_filters_use_played_time(
    date_from: datetime | None, date_to: datetime | None, expected: list[int]
) -> None:
    """An old game imported today is still old; both bounds are inclusive.

    Execute the portable WHERE fragment in SQLite with ISO UTC timestamps.
    Only the driver's placeholder syntax is translated; this does not test
    PostgreSQL's TIMESTAMPTZ conversion.
    """
    result = GameFilters(username="u", date_from=date_from, date_to=date_to).build_where()
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE games (id INTEGER, white_username TEXT, black_username TEXT, "
            "played_at TEXT, created_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?)",
            [
                (i, "u", "opponent", played.isoformat(), "2026-09-16T00:00:00+00:00")
                for i, played in enumerate(
                    [
                        datetime(2025, 9, 1, tzinfo=UTC),
                        datetime(2026, 9, 1, tzinfo=UTC),
                        datetime(2026, 9, 15, tzinfo=UTC),
                        datetime(2026, 9, 16, tzinfo=UTC),
                    ],
                    start=1,
                )
            ],
        )
        rows = connection.execute(
            "SELECT g.id FROM games g WHERE " + result.clause.replace("%s", "?") + " ORDER BY g.id",
            [arg.isoformat() if isinstance(arg, datetime) else arg for arg in result.args],
        ).fetchall()
        assert [row[0] for row in rows] == expected
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("query", "days"),
    [
        ("today", 1),
        ("yesterday", 2),
        ("this week", 7),
        ("last week", 14),
        ("this month", 30),
        ("last month", 60),
        ("this year", 365),
        ("recent", 14),
    ],
)
def test_relative_date_filters_are_utc(query: str, days: int) -> None:
    before = datetime.now(UTC)
    filters = QueryParser().parse(query, "u").filters
    after = datetime.now(UTC)
    assert filters.date_from is not None
    assert filters.date_from.tzinfo is UTC
    assert before - timedelta(days=days) <= filters.date_from <= after - timedelta(days=days)
