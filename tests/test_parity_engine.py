"""Phase 3, the engine half.

Two levels. The eval-helper grid is pure arithmetic and runs anywhere. The
re-analysis test starts a real Stockfish, re-analyzes five games, and diffs
every field of every move against what Go produced from the same engine.

Stockfish at a fixed depth on a fixed position is deterministic, so "identical"
is a reasonable bar rather than an aspirational one. It is deterministic *for a
given build*, though, which is why the reference is Go's output at the capture
rather than the stored rows — see the re-analysis test.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import load_golden
from zeitnot.db import DB
from zeitnot.engine import (
    ANALYSIS_DEPTH,
    Engine,
    analyze_game,
    classify_move,
    find_stockfish,
    get_evaluation,
    normalize_eval,
)
from zeitnot.models import MoveAnalysis


def test_eval_helper_grid_matches_the_golden() -> None:
    """Every branch of the three functions that decide a stored move's verdict.

    A grid rather than a corpus sample: mate in both directions, all four
    classification boundaries, and odd and even move indices are not all reached
    by 74 games, and a boundary that is off by one would show up only on the
    first game that hits it.
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


# ---------- the live re-analysis diff ----------

pytest_stockfish = pytest.mark.skipif(
    find_stockfish() is None,
    reason="stockfish is not on PATH and STOCKFISH_PATH is unset",
)


@pytest_stockfish
@pytest.mark.golden
# Also `corpus`: the `_pgns` fixture reads PGNs from the live database. Without
# this it sat in the `not corpus` subset CI runs and passed only because the
# golden it needs is absent there — with a database and a golden both present it
# raised KeyError rather than skipping.
@pytest.mark.corpus
def test_reanalysis_matches_the_go_analysis_move_for_move(_pgns: dict[str, str]) -> None:
    """Re-analyze the same games Go did and diff every field of every move.

    **Not against the database.** The stored `moves` rows were written by a
    different Stockfish build and are no longer reproducible by anything: the
    current *Go* tree diverges from them on all 12 of 12 and all 17 of 17
    evaluations on the two shortest games, and on 3 of 17 classifications. So
    "does Python match the corpus?" has no answer, and the question the port is
    actually on the hook for is "does Python match Go, given the same engine?"

    That reframing is not a weakening. Every field here — cpl, classification,
    evaluation, best_move, fen_before — is exactly what would be written on the
    next ingestion run, so this is precisely the comparison that decides whether
    new rows from Python are interchangeable with new rows from Go.

    The consequence, recorded in MANIFEST.md, is that this is the one golden
    tied to a Stockfish version rather than to a commit.
    """
    goldens: list[dict[str, Any]] = load_golden("analysis.json")
    assert goldens

    with Engine() as engine:
        for golden in goldens:
            uuid = golden["game_uuid"]
            pgn = _pgns[uuid]
            fresh = analyze_game(engine, pgn, ANALYSIS_DEPTH)

            assert len(fresh) == len(golden["moves"]), f"{uuid}: move count differs"

            for i, (want, got) in enumerate(zip(golden["moves"], fresh, strict=True)):
                where = f"{uuid} move index {i}"
                assert got.played_move == want["played_move"], f"{where}: played_move"
                assert got.best_move == want["best_move"], f"{where}: best_move"
                assert got.evaluation == want["evaluation"], f"{where}: evaluation"
                assert got.is_mate == want["is_mate"], f"{where}: is_mate"
                assert got.mate_in == want["mate_in"], f"{where}: mate_in"
                assert got.centipawn_loss == want["cpl"], f"{where}: cpl"
                assert got.classification == want["classification"], f"{where}: classification"
                assert got.fen_before == want["fen_before"], f"{where}: fen_before"


@pytest.fixture(scope="module")
def _pgns(db: DB) -> dict[str, str]:
    """The PGNs `analysis.json` was captured from, or a skip that says why not.

    A partial result used to be returned silently, so the test raised `KeyError`
    on the first game the database did not have. A corpus rebuilt or re-ingested
    since the capture is the ordinary cause of that, and it is a recapture
    question rather than a failure — `prompts/` already refuses on a fingerprint
    mismatch for exactly this reason, and this is the equivalent for the one
    golden that never had the check.
    """
    goldens: list[dict[str, Any]] = load_golden("analysis.json")
    uuids = [g["game_uuid"] for g in goldens]
    with db.cursor() as cur:
        cur.execute("SELECT uuid::text, pgn FROM games WHERE uuid::text = ANY(%s)", (uuids,))
        pgns: dict[str, str] = {row[0]: row[1] for row in cur.fetchall()}

    missing = [u for u in uuids if u not in pgns]
    if missing:
        pytest.skip(
            f"{len(missing)} of {len(uuids)} games in analysis.json are absent from this "
            "database, so it is not the corpus the golden was captured from — see "
            "testdata/golden/MANIFEST.md"
        )
    return pgns


@pytest_stockfish
def test_a_game_ending_in_checkmate_scores_the_final_position_directly() -> None:
    """The terminal-position branch.

    Stockfish has no legal move to search in a mated position, so the final
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


@pytest_stockfish
@pytest.mark.corpus
def test_move_identity_and_fens_still_match_the_stored_corpus(
    db: DB, _pgns: dict[str, str]
) -> None:
    """The half of a move row that does not depend on Stockfish.

    `played_move` and `fen_before` come from PGN parsing and board replay, not
    from the engine, so unlike the evaluations they *are* still reproducible
    against the stored corpus — and they are what would silently break if
    python-chess disagreed with notnil/chess about move numbering, promotion
    encoding, or the en-passant field.

    This is the test that caught python-chess omitting the en-passant square:
    its `fen()` defaults to `en_passant="legal"`, which prints "-" unless a
    capture is actually available, where Go prints the square after any double
    pawn push.
    """
    with Engine() as engine:
        for uuid, pgn in _pgns.items():
            stored = db.get_moves_for_game(uuid)
            fresh = analyze_game(engine, pgn, ANALYSIS_DEPTH)
            assert len(fresh) == len(stored), f"{uuid}: move count differs"
            for i, (want, got) in enumerate(zip(stored, fresh, strict=True)):
                assert got.played_move == want.played_move, f"{uuid} move {i}: played_move"
                assert got.fen_before == want.fen_before, f"{uuid} move {i}: fen_before"
