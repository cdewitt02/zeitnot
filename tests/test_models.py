"""Game field derivation — the PGN header and opening-URL parsing.

internal/models had no tests in Go. These exist because `opening_name` and
`eco_code` feed the stored `eco_name` / `eco_code` columns and the Game Summary
text, so a regex that ports slightly wrong is a silent corpus divergence.
"""

from __future__ import annotations

import pytest

from zeitnot.models import Game, Player, YearMonth, rating_band

PGN = """[Event "Live Chess"]
[White "PennedIn"]
[Black "cdew4"]
[Result "1-0"]
[ECO "A07"]
[ECOUrl "https://www.chess.com/openings/Kings-Indian-Attack-2...c6-3.Bg2"]
[Termination "PennedIn won by checkmate"]

1. Nf3 c6 1-0
"""


def test_pgn_headers() -> None:
    game = Game(pgn=PGN)
    assert game.eco_code() == "A07"
    assert game.termination_type() == "PennedIn won by checkmate"
    assert game._pgn_header("Nonexistent") == ""


@pytest.mark.parametrize(
    ("eco_url", "want"),
    [
        # The docstring's own example.
        (
            "https://www.chess.com/openings/Pirc-Defense-Main-Line-Kholmov-System-4...Bg7",
            "Pirc Defense Main Line Kholmov System",
        ),
        # "..." splits.
        (
            "https://www.chess.com/openings/Kings-Indian-Attack-2...c6-3.Bg2",
            "Kings Indian Attack",
        ),
        # "-2." splits, and the dash inside a name does not.
        (
            "https://www.chess.com/openings/Caro-Kann-Defense-2.Nf3",
            "Caro Kann Defense",
        ),
        # No variation suffix at all.
        ("https://www.chess.com/openings/London-System", "London System"),
        # Not an openings URL.
        ("https://www.chess.com/games/123", ""),
        ("", ""),
    ],
)
def test_opening_name(eco_url: str, want: str) -> None:
    assert Game(eco=eco_url).opening_name() == want


@pytest.mark.parametrize(
    ("white_result", "black_result", "want"),
    [
        ("win", "resigned", "white"),
        ("timeout", "win", "black"),
        ("agreed", "agreed", "draw"),
    ],
)
def test_game_result(white_result: str, black_result: str, want: str) -> None:
    game = Game(white=Player(result=white_result), black=Player(result=black_result))
    assert game.game_result() == want


@pytest.mark.parametrize(
    ("rating", "want"),
    [
        (0, "<1000"),
        (999, "<1000"),
        (1000, "1000-1200"),
        (1199, "1000-1200"),
        (1200, "1200-1400"),
        (1400, "1400-1600"),
        (1600, "1600-1800"),
        (1800, "1800-2000"),
        (1999, "1800-2000"),
        (2000, "2000+"),
        (3000, "2000+"),
    ],
)
def test_rating_band(rating: int, want: str) -> None:
    assert rating_band(rating) == want


def test_game_from_json_tolerates_a_sparse_payload() -> None:
    """Chess.com omits fields rather than nulling them.

    The Go tree got this for free from encoding/json's zero values; here it has
    to be deliberate, and it matters because a KeyError during ingestion would
    abort a whole month's analysis.
    """
    game = Game.from_json({"uuid": "abc", "white": {"username": "a"}})
    assert game.uuid == "abc"
    assert game.white.username == "a"
    assert game.black.username == ""
    assert game.rated is False
    assert game.accuracies == {}
    assert game.end_time == 0


@pytest.mark.parametrize("month", ["01", "08", "12"])
def test_yearmonth_accepts_a_padded_month(month: str) -> None:
    assert YearMonth(year=2026, month=month).month == month


@pytest.mark.parametrize(
    "month",
    ["1", "8", "0", "00", "13", "", "aa", "8 ", "008", "١٢"],
    ids=["1", "8", "0", "00", "13", "empty", "letters", "trailing-space", "three-digit", "arabic"],
)
def test_yearmonth_rejects_a_month_the_url_cannot_express(month: str) -> None:
    """An unpadded month builds a path Chess.com answers with 404 — the same
    status it uses for an unknown username. Rejecting it here is what keeps a
    padding mistake from being reported as a misspelled username."""
    with pytest.raises(ValueError, match="expected 01-12"):
        YearMonth(year=2026, month=month)
