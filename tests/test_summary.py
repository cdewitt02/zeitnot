"""Pure-function tests for Game Summary generation.

Separate from `test_parity_summary.py`, which is marked `corpus` and `golden` at
module level and therefore skips everywhere those are unavailable — including
CI. Summary logic that needs neither a database nor a captured golden belongs
here, where it always runs.
"""

from __future__ import annotations

import pytest

from zeitnot.models import Game, MoveAnalysis, Player
from zeitnot.summary import detect_pattern, extract_summary_data, generate_summary

PGN = (
    '[Event "Live Chess"]\n'
    '[ECOUrl "https://www.chess.com/openings/Queens-Gambit-Declined"]\n'
    "\n1. d4 d5"
)


def _game(white_result: str, black_result: str) -> Game:
    return Game(
        uuid="test-uuid",
        pgn=PGN,
        time_class="blitz",
        white=Player(username="player", rating=1500, result=white_result),
        black=Player(username="opponent", rating=1520, result=black_result),
    )


def _moves() -> list[MoveAnalysis]:
    return [
        MoveAnalysis(played_move="d2d4", classification="good", centipawn_loss=10),
        MoveAnalysis(played_move="d7d5", classification="good", centipawn_loss=12),
    ]


# ---------- the draw fix ----------


@pytest.mark.parametrize(
    ("white_result", "black_result", "want"),
    [
        ("win", "resigned", "won"),
        ("resigned", "win", "lost"),
        ("agreed", "agreed", "drew"),
        ("stalemate", "stalemate", "drew"),
        ("repetition", "repetition", "drew"),
        ("insufficient", "insufficient", "drew"),
        ("50move", "50move", "drew"),
        ("timevsinsufficient", "timevsinsufficient", "drew"),
    ],
)
def test_a_drawn_game_is_summarized_as_a_draw(
    white_result: str, black_result: str, want: str
) -> None:
    """Regression test for the defect preserved through the Python port.

    `game_result()` returns "draw", never "", so the `drew` branch in
    `extract_summary_data` was dead and every draw was reported as a loss. This
    was visible in the summary text, in the embeddings built from it, and in the
    win/loss/draw tallies `prompts.py` derives by reading that text.
    """
    data = extract_summary_data(_game(white_result, black_result), _moves(), "player")
    assert data.result == want


def test_a_drawn_summary_starts_with_drew() -> None:
    """`chat/prompts.py` tallies results with `summary.startswith("drew")`, so
    the first word is load-bearing rather than cosmetic."""
    data = extract_summary_data(_game("agreed", "agreed"), _moves(), "player")
    text = generate_summary(data)
    assert text.startswith("drew as white in blitz.")


def test_the_result_is_from_the_players_perspective_not_whites() -> None:
    game = _game("resigned", "win")
    assert extract_summary_data(game, _moves(), "player").result == "lost"
    assert extract_summary_data(game, _moves(), "opponent").result == "won"


# ---------- the four verdicts the draw fix makes reachable ----------


@pytest.mark.parametrize(
    ("was_winning", "was_losing", "want"),
    [
        (True, False, "Missed winning opportunity"),
        (False, True, "Saved a draw from worse position"),
        (True, True, "Wild game ended in draw"),
        (False, False, "Even game throughout"),
    ],
)
def test_the_drew_verdicts_are_reachable(was_winning: bool, was_losing: bool, want: str) -> None:
    """These four were ported but unreachable — `extract_summary_data` never
    produced "drew". Fixing that made them live, so they are now worth asserting
    rather than merely carrying."""
    data = extract_summary_data(_game("agreed", "agreed"), _moves(), "player")
    data.was_winning = was_winning
    data.was_losing = was_losing
    assert detect_pattern(data) == want
