"""The re-derivation check: every stored summary still follows from stored rows.

This is the one claim the retired `summaries.json` suite made that needed no
golden, and it is the reason the retirement is a narrowing rather than a loss.
The Go capture tool re-derived every summary from the stored `games` and `moves`
rows and compared it against the stored `summary_text`, reporting a mismatch
loudly; 74 of 74 matched at the capture commit. Comparing the tree against
itself through the database is a different question from comparing it against a
frozen file, and only the second one needed a capture tool.

**What it protects.** The summary text *is* the embedded text. If `summary.py`
changes and the stored rows are not regenerated, every vector in
`game_summaries` still describes text the tree no longer produces — retrieval
degrades, and nothing anywhere errors. That is this project's characteristic
bug, silence, in the place it is hardest to notice.

**What a failure means.** Not that the code is wrong. It means the corpus
predates a deliberate summary change and has not been regenerated — which today
has no remedy: `zeitnot data reembed` rebuilds vectors from the *stored* text
without regenerating it, and `zeitnot data analyze` skips any game it has
already seen. The message says so rather than leaving the reader to conclude
they have broken something.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from zeitnot.db import DB
from zeitnot.models import Game, MoveAnalysis, Player
from zeitnot.summary import extract_summary_data, generate_summary

pytestmark = pytest.mark.corpus

_ECO_URL_HEADER = re.compile(r'\[ECOUrl "([^"]*)"\]')

# `generate_summary` emits exactly these eight lines, in this order, always.
_SUMMARY_LINES = (
    "result, colour and time class",
    "opening name",
    "blunder / mistake / inaccuracy counts",
    "weakest phase",
    "game pattern",
    "game length",
    "termination type",
    "opponent rating",
)


def _rebuild_game(row: dict[str, Any]) -> Game:
    """Reconstruct the ingestion-time Game from stored columns.

    `Game.eco` holds the Chess.com openings URL, which is not a column but is
    carried in the PGN as `[ECOUrl "..."]`. Recovering it there is what lets
    `opening_name()` run against stored data.
    """
    game = Game(
        uuid=row["uuid"],
        pgn=row["pgn"],
        time_class=row["time_class"],
        white=Player(username=row["white_username"], rating=row["white_rating"]),
        black=Player(username=row["black_username"], rating=row["black_rating"]),
    )
    match = _ECO_URL_HEADER.search(row["pgn"])
    if match:
        game.eco = match.group(1)

    if row["result"] == "white":
        game.white.result, game.black.result = "win", "lost"
    elif row["result"] == "black":
        game.white.result, game.black.result = "lost", "win"
    else:
        game.white.result, game.black.result = "draw", "draw"
    return game


@pytest.fixture(scope="module")
def regenerated(db: DB, corpus_username: str) -> dict[str, str]:
    """Every stored game's summary, regenerated from the rows it was built from."""
    with db.cursor() as cur:
        cur.execute(
            """SELECT g.uuid::text, g.pgn, g.result, g.time_class,
                      g.white_username, g.white_rating, g.black_username, g.black_rating
               FROM games g ORDER BY g.uuid"""
        )
        rows = cur.fetchall()

    out: dict[str, str] = {}
    for row in rows:
        record = {
            "uuid": row[0],
            "pgn": row[1] or "",
            "result": row[2] or "",
            "time_class": row[3] or "",
            "white_username": row[4] or "",
            "white_rating": row[5] or 0,
            "black_username": row[6] or "",
            "black_rating": row[7] or 0,
        }
        moves = [
            MoveAnalysis(
                evaluation=m.evaluation,
                is_mate=m.is_mate,
                mate_in=m.mate_in,
                centipawn_loss=m.cpl,
                classification=m.classification,
                fen_before=m.fen_before,
            )
            for m in db.get_moves_for_game(row[0])
        ]
        out[row[0]] = generate_summary(
            extract_summary_data(_rebuild_game(record), moves, corpus_username)
        )
    return out


def test_every_stored_summary_re_derives_from_the_stored_rows(
    db: DB, regenerated: dict[str, str]
) -> None:
    """The claim that protects the embeddings.

    Every row in `game_summaries` must be exactly what this tree produces from
    the `games` and `moves` rows that are still sitting next to it.
    """
    stored = db.all_summary_texts()
    assert stored, "no game has a stored summary, so nothing was compared"

    orphans = [row.game_uuid for row in stored if row.game_uuid not in regenerated]
    assert not orphans, (
        f"{len(orphans)} summaries have no game row: {orphans[:3]}. "
        "A summary outlived its game, so the corpus is inconsistent rather than stale."
    )

    drifted = [
        (row.game_uuid, row.summary_text, regenerated[row.game_uuid])
        for row in stored
        if row.summary_text and row.summary_text != regenerated[row.game_uuid]
    ]
    if not drifted:
        return

    # Which lines moved, not just how many rows did. A corpus that predates the
    # termination normalization differs on exactly one line of every summary; a
    # fresh defect in phase bucketing differs on another. Reporting the counts
    # per line is what separates "this corpus is old" from "this change broke
    # something", which is the question the reader actually has.
    by_line: dict[str, int] = {}
    for _uuid, was, now in drifted:
        for label, old, new in zip(
            _SUMMARY_LINES, was.splitlines(), now.splitlines(), strict=False
        ):
            if old != new:
                by_line[label] = by_line.get(label, 0) + 1

    breakdown = "\n".join(
        f"  {count:>4}  {label}"
        for label, count in sorted(by_line.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    examples = "\n".join(
        f"{uuid}\n  stored:      {was!r}\n  re-derived:  {now!r}" for uuid, was, now in drifted[:3]
    )
    raise AssertionError(
        f"{len(drifted)} of {len(stored)} stored summaries no longer match what this tree "
        "derives from the same rows, so their embeddings describe text that no longer "
        f"exists.\n\nLines that differ:\n{breakdown}\n\n"
        "This is a stale corpus, not necessarily a code defect. It is what a deliberate "
        "change to summary.py leaves behind, and there is no command that repairs it: "
        "`zeitnot data reembed` rebuilds vectors from the stored text without regenerating "
        "it, and `zeitnot data analyze` skips games it has already seen. The regeneration "
        "pass is unbuilt work — see docs/codebase-invariants.md.\n\n"
        f"{examples}"
    )


def test_every_analyzed_game_has_a_summary(db: DB) -> None:
    """A game with moves and no summary is invisible to retrieval.

    It costs the same Stockfish time as every other game and then never reaches
    a prompt, which is a silent hole rather than an error.
    """
    with db.cursor() as cur:
        cur.execute(
            """SELECT g.uuid::text FROM games g
               WHERE EXISTS (SELECT 1 FROM moves m WHERE m.game_uuid = g.uuid)
                 AND NOT EXISTS (
                     SELECT 1 FROM game_summaries s WHERE s.game_uuid = g.uuid
                 )
               ORDER BY g.uuid"""
        )
        missing = [str(row[0]) for row in cur.fetchall()]

    assert not missing, (
        f"{len(missing)} analyzed games have no summary and so can never be retrieved: "
        f"{missing[:5]}"
    )
