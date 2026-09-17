"""The engine's pure functions, and the half of a move row it does not decide.

Two levels. The eval-helper grid is arithmetic over a committed table and runs
anywhere. The corpus test replays stored PGNs and checks move identity, which
needs a database but no Stockfish.

The third level is gone. `analysis.json` recorded what Go produced from five
games with the engine on PATH at the capture, and both the tool that made it and
the implementation it described were deleted — see
[ADR 0003](../docs/adr/0003-retire-the-parity-goldens.md). Stockfish is
deterministic for a given build and not across builds, so that file could only
ever answer "does Python match Go on one machine", and there is no longer a Go
to match.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import load_golden
from zeitnot.db import DB
from zeitnot.engine import (
    Engine,
    analyze_game,
    classify_move,
    find_stockfish,
    get_evaluation,
    move_identities,
    normalize_eval,
)
from zeitnot.models import MoveAnalysis


def test_eval_helper_grid_matches_the_golden() -> None:
    """Every branch of the three functions that decide a stored move's verdict.

    A grid rather than a corpus sample: mate in both directions, all four
    classification boundaries, and odd and even move indices are not all reached
    by any one player's games, and a boundary that is off by one would show up
    only on the first game that hits it. That is also why the table is committed
    — it describes the functions, not a corpus.
    """
    cases: list[dict[str, Any]] = load_golden("eval_helpers.json")
    assert cases

    for case in cases:
        analysis = MoveAnalysis(
            evaluation=case["evaluation"],
            is_mate=case["is_mate"],
            mate_in=case["mate_in"],
        )
        got = get_evaluation(analysis)
        assert got == case["get_evaluation"], case
        assert normalize_eval(got, case["move_index"]) == case["normalize_eval"], case
        assert classify_move(case["classify_move_cpl"]) == case["classify_move"], case


@pytest.mark.parametrize(
    ("cpl", "want"),
    [
        (-100, "best"),
        (0, "best"),
        (1, "good"),
        (50, "good"),
        (51, "inaccuracy"),
        (100, "inaccuracy"),
        (101, "mistake"),
        (200, "mistake"),
        (201, "blunder"),
        (10000, "blunder"),
    ],
)
def test_classify_move_boundaries(cpl: int, want: str) -> None:
    assert classify_move(cpl) == want


def test_get_evaluation_prefers_a_faster_mate() -> None:
    """Mate in 1 must outrank mate in 5, and being mated must mirror that."""
    mate_in_1 = get_evaluation(MoveAnalysis(is_mate=True, mate_in=1))
    mate_in_5 = get_evaluation(MoveAnalysis(is_mate=True, mate_in=5))
    assert mate_in_1 == 9999
    assert mate_in_5 == 9995
    assert mate_in_1 > mate_in_5

    mated_in_1 = get_evaluation(MoveAnalysis(is_mate=True, mate_in=-1))
    mated_in_5 = get_evaluation(MoveAnalysis(is_mate=True, mate_in=-5))
    assert mated_in_1 == -9999
    assert mated_in_5 == -9995
    assert mated_in_1 < mated_in_5

    # A centipawn score is passed through untouched, whatever mate_in holds.
    assert get_evaluation(MoveAnalysis(evaluation=137, is_mate=False, mate_in=3)) == 137


def test_normalize_eval_flips_on_odd_indices_only() -> None:
    for index in (0, 2, 4):
        assert normalize_eval(150, index) == 150
    for index in (1, 3, 5):
        assert normalize_eval(150, index) == -150


# ---------- the terminal-position branch ----------

pytest_stockfish = pytest.mark.skipif(
    find_stockfish() is None,
    reason="stockfish is not on PATH and STOCKFISH_PATH is unset",
)


@pytest_stockfish
def test_a_game_ending_in_checkmate_scores_the_final_position_directly() -> None:
    """Stockfish has no legal move to search in a mated position, so the final
    move's evaluation is assigned rather than asked for. Getting this wrong
    would not error — it would quietly misprice the last move of every decisive
    game.
    """
    # Fool's mate: Black delivers checkmate on move index 3, an odd index.
    pgn = '[Event "t"]\n[Result "0-1"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n'
    with Engine() as engine:
        analyses = analyze_game(engine, pgn, depth=6)

    assert len(analyses) == 4
    assert analyses[-1].played_move == "d8h4"
    # Black mated, so the delivering move is its own best: zero loss.
    assert analyses[-1].centipawn_loss == 0
    assert analyses[-1].classification == "best"


@pytest_stockfish
def test_a_game_ending_in_stalemate_scores_the_final_position_as_a_draw() -> None:
    """The other half of the terminal branch: a draw is 0, not a mate score."""
    pgn = (
        '[Event "t"]\n[Result "1/2-1/2"]\n\n'
        "1. e3 a5 2. Qh5 Ra6 3. Qxa5 h5 4. Qxc7 Rah6 5. h4 f6 6. Qxd7+ Kf7 "
        "7. Qxb7 Qd3 8. Qxb8 Qh7 9. Qxc8 Kg6 10. Qe6 1/2-1/2\n"
    )
    with Engine() as engine:
        analyses = analyze_game(engine, pgn, depth=6)

    assert len(analyses) == 19
    # White's 10. Qe6 stalemates Black, throwing away an overwhelming position,
    # so it must be scored as a large loss rather than as a mate.
    last = analyses[-1]
    assert last.played_move == "c8e6"
    assert last.classification == "blunder"
    assert last.centipawn_loss > 0


# ---------- the half of a move row the engine does not decide ----------


@pytest.mark.corpus
def test_move_identity_and_fens_still_match_the_stored_corpus(db: DB) -> None:
    """`played_move` and `fen_before` come from PGN parsing and board replay.

    This is the assertion preserved out of the retired `analysis.json` suite.
    That file pinned every *evaluation* to one Stockfish build and could not
    survive a different one, but these two fields were never the engine's to
    decide, so they stay reproducible against whatever the corpus was analyzed
    with. They are what would silently break if python-chess disagreed with
    notnil/chess about move numbering, promotion encoding, or the en-passant
    field — and it is the test that caught `board.fen()` defaulting to
    `en_passant="legal"`, which prints "-" unless a capture is actually
    available where Go printed the square after any double pawn push.

    It gained reach in the retirement rather than losing it: the golden version
    ran against the five games the capture happened to hold, and skipped
    entirely on any corpus that no longer contained all five. This runs against
    every analyzed game in the database and needs no engine.
    """
    with db.cursor() as cur:
        cur.execute("SELECT uuid::text, pgn FROM games ORDER BY uuid")
        games = [(str(row[0]), row[1] or "") for row in cur.fetchall()]
    assert games, "the database has no games"

    checked = 0
    for uuid, pgn in games:
        stored = db.get_moves_for_game(uuid)
        if not stored:
            continue  # ingested but not yet analyzed; there is nothing to compare
        fresh = move_identities(pgn)
        assert len(fresh) == len(stored), f"{uuid}: move count differs"
        for i, (want, (played, fen)) in enumerate(zip(stored, fresh, strict=True)):
            assert played == want.played_move, f"{uuid} move {i}: played_move"
            assert fen == want.fen_before, f"{uuid} move {i}: fen_before"
        checked += 1

    assert checked, "no game in the database has stored moves, so nothing was compared"
