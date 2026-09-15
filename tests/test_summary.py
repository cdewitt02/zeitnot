"""Pure-function tests for Game Summary generation.

Everything here needs neither a database nor a captured golden, so it always
runs — including in CI, which is the whole point. This module absorbed the
corpus-free half of `test_parity_summary.py` when the corpus-derived goldens
were retired ([ADR 0003](../docs/adr/0003-retire-the-parity-goldens.md)); the
half that compared against `summaries.json` went with the file.

The claim that file made which does *not* die with it — that every stored
summary still re-derives from the stored `games` and `moves` rows — is in
`test_summary_corpus.py`, which needs the database and no golden at all.
"""

from __future__ import annotations

import pytest

from zeitnot.models import Game, GameSummaryData, MoveAnalysis, PhaseStats, Player
from zeitnot.summary import (
    MIDDLEGAME_END,
    OPENING_END,
    classify_game_length,
    detect_pattern,
    extract_summary_data,
    generate_summary,
    weakest_phase,
)

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


# ---------- the phase boundaries ----------


def _ply_moves(count: int) -> list[MoveAnalysis]:
    """`count` plies, each costing one centipawn more than the last.

    Distinct CPLs make the bucket a move landed in recoverable from `total_cpl`
    alone, so a boundary that is off by one is visible rather than absorbed.
    """
    return [
        MoveAnalysis(played_move="a2a3", classification="good", centipawn_loss=i + 1)
        for i in range(count)
    ]


@pytest.mark.parametrize("color", ["white", "black"])
def test_phase_boundaries_are_full_moves_not_plies(color: str) -> None:
    """The boundaries count full moves, so each phase is two plies per move.

    This is the regression test for Q8. `extract_summary_data` iterates plies
    and the constants are full moves, so reading the ply index as a move number
    halved every boundary: "opening" meant full moves 1-5, and the endgame
    bucket collected everything from full move 13 on. On one real 195-game
    corpus that moved 45% of `weakest_phase` verdicts.

    A full move is one ply for the player whose summary this is, so with 60
    plies each side gets exactly 30 — 10 in the opening, 15 in the middlegame,
    and 5 in the endgame, whichever colour they played.
    """
    username = "player" if color == "white" else "opponent"
    data = extract_summary_data(_game("win", "resigned"), _ply_moves(60), username)

    assert data.player_color == color
    assert data.opening.move_count == OPENING_END
    assert data.middlegame.move_count == MIDDLEGAME_END - OPENING_END
    assert data.endgame.move_count == 30 - MIDDLEGAME_END
    assert data.opening.move_count + data.middlegame.move_count + data.endgame.move_count == 30


@pytest.mark.parametrize(
    ("plies", "want_opening", "want_middlegame"),
    [
        # White moves on even ply indices, so full move 10 is ply index 18.
        (19, 10, 0),  # ..through full move 10: opening is full
        (21, 10, 1),  # ..full move 11 has started: the first middlegame move
        (49, 10, 15),  # ..through full move 25: middlegame is full
        (51, 10, 15),  # ..full move 26: the extra move lands in the endgame
    ],
)
def test_the_opening_and_middlegame_close_on_the_documented_move(
    plies: int, want_opening: int, want_middlegame: int
) -> None:
    """Each boundary is checked from both sides, not only from below."""
    data = extract_summary_data(_game("win", "resigned"), _ply_moves(plies), "player")
    assert data.opening.move_count == want_opening
    assert data.middlegame.move_count == want_middlegame


def test_a_short_game_never_reaches_the_later_phases() -> None:
    """A phase with no moves must stay empty rather than borrowing from another.

    `weakest_phase` scores an empty phase as 0.0 average, so a phase wrongly
    credited with moves changes the verdict.
    """
    data = extract_summary_data(_game("win", "resigned"), _ply_moves(6), "player")
    assert data.opening.move_count == 3
    assert data.middlegame.move_count == 0
    assert data.endgame.move_count == 0


# ---------- the verdict tables ----------


@pytest.mark.parametrize(
    ("total_moves", "want"),
    [
        (0, "Short game"),
        (19, "Short game"),
        (20, "Medium length game"),
        (39, "Medium length game"),
        (40, "Long game"),
        (200, "Long game"),
    ],
)
def test_classify_game_length_boundaries(total_moves: int, want: str) -> None:
    assert classify_game_length(total_moves) == want


@pytest.mark.parametrize(
    ("result", "was_winning", "was_losing", "want"),
    [
        ("won", False, True, "Came back from losing position"),
        ("won", True, True, "Came back from losing position"),
        ("won", True, False, "Steady advantage throughout"),
        ("won", False, False, "Converted a close game"),
        ("lost", True, False, "Threw a winning position"),
        ("lost", True, True, "Threw a winning position"),
        ("lost", False, True, "Was outplayed"),
        ("lost", False, False, "Lost a close game"),
        # Live since the draw fix landed on 2026-08-31. That these four were
        # already tested while still unreachable is what made the fix a
        # one-line change; `test_the_drew_verdicts_are_reachable` above is the
        # other half, asserting that a real drawn game now gets here at all.
        ("drew", True, False, "Missed winning opportunity"),
        ("drew", False, True, "Saved a draw from worse position"),
        ("drew", True, True, "Wild game ended in draw"),
        ("drew", False, False, "Even game throughout"),
        ("something else", False, False, "Unknown pattern"),
    ],
)
def test_detect_pattern_matrix(result: str, was_winning: bool, was_losing: bool, want: str) -> None:
    data = GameSummaryData(result=result, was_winning=was_winning, was_losing=was_losing)
    assert detect_pattern(data) == want


def test_weakest_phase_picks_the_highest_average_not_the_highest_total() -> None:
    """Averages, not totals — a long clean endgame must not outrank a short
    sloppy opening."""
    opening = PhaseStats(total_cpl=300, move_count=3)  # avg 100
    middlegame = PhaseStats(total_cpl=150, move_count=15)  # avg 10
    endgame = PhaseStats(total_cpl=400, move_count=40)  # avg 10, highest total
    assert weakest_phase(opening, middlegame, endgame) == "Opening was weakest"
    assert weakest_phase(PhaseStats(), middlegame, PhaseStats()) == "Middlegame was weakest"


def test_the_remaining_preserved_defect_is_still_preserved() -> None:
    """A guard against someone "fixing" this incidentally.

    `weakest_phase` reports "Endgame was weakest" on any tie, because the
    endgame is the `else` catch-all. Fixing it changes Game Summary text and
    therefore makes every stored vector stale relative to its own source, so it
    needs its own change with its own verification rather than riding along
    with something else.

    This used to sit beside a second assertion, that the goldens did not yet
    contain "drew" — a check on a captured file's staleness rather than on the
    code. That file is retired; this half was always the one about behavior.
    """
    assert weakest_phase(PhaseStats(), PhaseStats(), PhaseStats()) == "Endgame was weakest", (
        "an all-zero tie must still report the endgame; preserved defect 1 has been fixed"
    )
