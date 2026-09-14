"""Ingestion: move-record shaping, stats aggregation, and the worker pool.

The pool's fail-fast semantics are the part worth testing. `cmd/data` had no
tests in Go, and "the first error cancels the run" is the property that keeps a
half-ingested month from looking like a successful one.
"""

from __future__ import annotations

import threading

import pytest

from zeitnot.ingest import WorkerPool, aggregate_stats, to_move_records
from zeitnot.models import Game, MoveAnalysis, Player


def _analyses(classifications: list[str], cpls: list[int] | None = None) -> list[MoveAnalysis]:
    cpls = cpls or [0] * len(classifications)
    return [
        MoveAnalysis(
            played_move=f"m{i}",
            best_move=f"b{i}",
            classification=c,
            centipawn_loss=cpl,
            evaluation=10 * i,
        )
        for i, (c, cpl) in enumerate(zip(classifications, cpls, strict=True))
    ]


def test_move_records_number_and_side_moves_the_way_chess_does() -> None:
    """Indices 0,1 are move 1; 2,3 are move 2. An off-by-one here would
    misattribute every Black move in the corpus."""
    records = to_move_records("game-1", _analyses(["best"] * 5))

    assert [(r.move_number, r.side) for r in records] == [
        (1, "white"),
        (1, "black"),
        (2, "white"),
        (2, "black"),
        (3, "white"),
    ]
    assert all(r.game_uuid == "game-1" for r in records)
    assert records[0].played_move == "m0"
    assert records[0].best_move == "b0"


def test_aggregate_stats_splits_by_side_and_counts_each_classification() -> None:
    analyses = _analyses(
        # white: best, blunder, good     black: mistake, inaccuracy, best
        ["best", "mistake", "blunder", "inaccuracy", "good", "best"],
        [0, 150, 400, 80, 20, 0],
    )
    white, black = aggregate_stats(analyses)

    assert (white.moves, black.moves) == (3, 3)
    assert (white.blunders, white.best_moves) == (1, 1)
    assert (black.mistakes, black.inaccuracies, black.best_moves) == (1, 1, 1)
    # "good" is counted in neither the error buckets nor best_moves, which is
    # why the four counters do not sum to moves.
    assert white.total_cpl == 0 + 400 + 20
    assert black.total_cpl == 150 + 80 + 0


def test_aggregate_stats_of_an_empty_game_is_all_zero() -> None:
    white, black = aggregate_stats([])
    assert (white.moves, black.moves) == (0, 0)


class _FailingPool(WorkerPool):
    """A pool whose per-game work is scripted, so failure ordering is testable
    without Stockfish, a database, or an embedder."""

    def __init__(self, num_workers: int, fail_on: set[str]) -> None:
        super().__init__(num_workers, None, None, "u")  # type: ignore[arg-type]
        self.fail_on = fail_on
        self.processed: list[str] = []
        self._lock = threading.Lock()


def _patch(monkeypatch: pytest.MonkeyPatch, pool: _FailingPool) -> None:
    """Replace the engine and the per-game work with scripted stand-ins."""

    class FakeEngine:
        def __enter__(self) -> FakeEngine:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def process_game(engine: object, db: object, emb: object, user: str, game: Game) -> None:
        if game.uuid in pool.fail_on:
            raise RuntimeError(f"analysis failed for {game.uuid}")
        with pool._lock:
            pool.processed.append(game.uuid)

    monkeypatch.setattr("zeitnot.ingest.Engine", FakeEngine)
    monkeypatch.setattr("zeitnot.ingest.process_game", process_game)


def _games(count: int) -> list[Game]:
    return [
        Game(
            uuid=f"g{i}",
            white=Player(username="u"),
            black=Player(username=f"opp{i}"),
        )
        for i in range(count)
    ]


def test_the_pool_processes_every_game_when_nothing_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pool = _FailingPool(3, fail_on=set())
    _patch(monkeypatch, pool)

    pool.process(_games(10))

    assert sorted(pool.processed) == [f"g{i}" for i in range(10)]
    # Each game is reported exactly once, and the counter reaches the total.
    out = capsys.readouterr().out
    assert "[10/10]" in out


def test_the_first_error_cancels_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-fast, and the error names the game that stopped it.

    Without cancellation a failing month would keep burning Stockfish time on
    games whose results are about to be discarded, and the operator would see
    the failure only at the end.
    """
    pool = _FailingPool(2, fail_on={"g3"})
    _patch(monkeypatch, pool)

    with pytest.raises(RuntimeError) as excinfo:
        pool.process(_games(40))

    message = str(excinfo.value)
    assert "g3" in message, "the error must name the game that failed"
    assert "worker" in message
    # Cancellation is cooperative and checked between games, so a few in-flight
    # games may still finish. What must not happen is the whole batch running.
    assert len(pool.processed) < 40, "the run continued past the first failure"


def test_an_empty_batch_starts_no_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingestion filters already-analyzed games before calling process, so an
    empty batch is the common "nothing new this month" path — it must not pay
    to start engines."""
    started = 0

    class CountingEngine:
        def __init__(self) -> None:
            nonlocal started
            started += 1

        def __enter__(self) -> CountingEngine:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("zeitnot.ingest.Engine", CountingEngine)
    WorkerPool(4, None, None, "u").process([])  # type: ignore[arg-type]
    assert started == 0
