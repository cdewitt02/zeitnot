"""Termination normalization — readiness P0-8.

Chess.com's termination strings embed the winning player's username, and those
reached hosted Chat Providers verbatim through two paths: the aggregate "Game
endings" section of the assembled prompt, and every retrieved Game Summary.
"""

from __future__ import annotations

import pytest

from zeitnot.models import normalize_termination


@pytest.mark.parametrize(
    ("termination", "result", "want"),
    [
        # Decisive results name the winner; the key must name the outcome.
        ("Bolzman0 won by resignation", "lost", "lost by resignation"),
        ("Bolzman0 won by resignation", "won", "won by resignation"),
        ("AlexanderZapata37811 won by checkmate", "lost", "lost by checkmate"),
        ("cdew4 won by checkmate", "won", "won by checkmate"),
        ("someone won on time", "lost", "lost on time"),
        ("cdew4 won on time", "won", "won on time"),
        ("someone won - game abandoned", "lost", "lost by abandonment"),
        # Draws are already anonymous, but still get the player's perspective.
        ("Game drawn by agreement", "drew", "drawn by agreement"),
        ("Game drawn by stalemate", "drew", "drawn by stalemate"),
        ("Game drawn by repetition", "drew", "drawn by repetition"),
        ("Game drawn by insufficient material", "drew", "drawn by insufficient material"),
        (
            "Game drawn by timeout vs insufficient material",
            "drew",
            "drawn by timeout vs insufficient material",
        ),
    ],
    ids=lambda v: str(v)[:38],
)
def test_normalizes_every_shape_chesscom_emits(termination: str, result: str, want: str) -> None:
    """These nine shapes are every distinct form in a 195-game corpus."""
    assert normalize_termination(termination, result) == want


@pytest.mark.parametrize(
    "termination",
    [
        "Bolzman0 won by resignation",
        "AlexanderZapata37811 won by checkmate",
        "xX_SomeHandle_Xx won on time",
        "player123 won - game abandoned",
        "TotallyUnexpected format from a future API",
        "Someone won by a method we have never seen",
    ],
)
@pytest.mark.parametrize("result", ["won", "lost", "drew"])
def test_no_opponent_handle_survives_normalization(termination: str, result: str) -> None:
    """The load-bearing assertion: whatever the input, no token from it that
    looks like a username reaches the output.

    Written as a property over the whole output rather than a per-shape
    expectation, because the risk is an *unanticipated* shape passing through —
    which is exactly what the "by other means" fallback exists to stop.
    """
    out = normalize_termination(termination, result)
    for handle in ("Bolzman0", "AlexanderZapata37811", "xX_SomeHandle_Xx", "player123"):
        assert handle.lower() not in out.lower(), f"{handle!r} leaked into {out!r}"


@pytest.mark.parametrize("result", ["won", "lost", "drew"])
def test_an_unparseable_string_is_not_echoed(result: str) -> None:
    """Passing the raw string through on an unrecognized shape would reintroduce
    the leak for exactly the inputs we cannot reason about."""
    assert normalize_termination("Gibberish_Handle stuff", result) == f"{result} by other means"


def test_an_empty_termination_stays_empty() -> None:
    """The aggregation site skips falsy keys, so an empty string must not become
    the string "won by other means" and create a phantom bucket."""
    assert normalize_termination("", "won") == ""
    assert normalize_termination("   ", "lost") == ""


def test_the_same_method_aggregates_across_opponents() -> None:
    """The point of the change: nine games lost to nine different opponents by
    resignation must land in one bucket, not nine."""
    keys = {normalize_termination(f"opponent{i} won by resignation", "lost") for i in range(9)}
    assert keys == {"lost by resignation"}
