"""Game ingestion: fetch, analyze, summarize, embed, store.

The worker pool is a `ThreadPoolExecutor`. The workload is Stockfish subprocess
I/O, so the GIL is not a factor — each worker spends its time blocked on a pipe.
What matters is that **the fail-fast semantics are preserved**: the first error
cancels the run, and each worker owns its own engine process, because UCI is
stateful and a shared engine would interleave `position` commands.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime

from zeitnot.db import DB, GameRecord, MoveRecord
from zeitnot.engine import ANALYSIS_DEPTH, Engine, analyze_game
from zeitnot.llm.base import Embedder, embed_one
from zeitnot.models import Game, MoveAnalysis
from zeitnot.summary import extract_summary_data, generate_summary


@dataclass(slots=True)
class SideStats:
    total_cpl: int = 0
    moves: int = 0
    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0
    best_moves: int = 0


def to_move_records(game_uuid: str, analyses: Sequence[MoveAnalysis]) -> list[MoveRecord]:
    records: list[MoveRecord] = []
    for i, analysis in enumerate(analyses):
        records.append(
            MoveRecord(
                game_uuid=game_uuid,
                # Chess numbering: indices 0,1 are move 1; 2,3 are move 2.
                move_number=(i // 2) + 1,
                side="black" if i % 2 == 1 else "white",
                played_move=analysis.played_move,
                best_move=analysis.best_move,
                fen_before=analysis.fen_before,
                evaluation=analysis.evaluation,
                is_mate=analysis.is_mate,
                mate_in=analysis.mate_in,
                cpl=analysis.centipawn_loss,
                classification=analysis.classification,
            )
        )
    return records


def aggregate_stats(analyses: Sequence[MoveAnalysis]) -> tuple[SideStats, SideStats]:
    white, black = SideStats(), SideStats()
    for i, analysis in enumerate(analyses):
        stats = black if i % 2 == 1 else white
        stats.moves += 1
        stats.total_cpl += analysis.centipawn_loss
        if analysis.classification == "blunder":
            stats.blunders += 1
        elif analysis.classification == "mistake":
            stats.mistakes += 1
        elif analysis.classification == "inaccuracy":
            stats.inaccuracies += 1
        elif analysis.classification == "best":
            stats.best_moves += 1
    return white, black


def process_game(
    engine: Engine, database: DB, embedder: Embedder, username: str, game: Game
) -> None:
    """Analyze one game and write its three rows: game, moves, summary."""
    analyses = analyze_game(engine, game.pgn, ANALYSIS_DEPTH)

    summary_data = extract_summary_data(game, analyses, username)
    game_summary = generate_summary(summary_data)

    embedding = embed_one(embedder, game_summary)

    white, black = aggregate_stats(analyses)
    avg_cpl_white = white.total_cpl / white.moves if white.moves > 0 else 0.0
    avg_cpl_black = black.total_cpl / black.moves if black.moves > 0 else 0.0

    database.save_game(
        GameRecord(
            uuid=game.uuid,
            url=game.url,
            pgn=game.pgn,
            eco_code=game.eco_code(),
            eco_name=game.opening_name(),
            white_username=game.white.username,
            white_rating=game.white.rating,
            black_username=game.black.username,
            black_rating=game.black.rating,
            result=game.game_result(),
            termination_type=game.termination_type(),
            time_control=game.time_control,
            time_class=game.time_class,
            rated=game.rated,
            avg_cpl_white=avg_cpl_white,
            avg_cpl_black=avg_cpl_black,
            blunders_white=white.blunders,
            blunders_black=black.blunders,
            mistakes_white=white.mistakes,
            mistakes_black=black.mistakes,
            inaccuracies_white=white.inaccuracies,
            inaccuracies_black=black.inaccuracies,
            best_moves_white=white.best_moves,
            best_moves_black=black.best_moves,
            played_at=datetime.fromtimestamp(game.end_time, tz=UTC),
        )
    )
    database.save_moves(to_move_records(game.uuid, analyses))
    database.save_game_summary(game.uuid, game_summary, embedding)


class WorkerPool:
    """Analyzes games in parallel, failing fast.

    Ingestion stays resumable through the already-analyzed filter the caller
    applies before handing games over, so a run that stops halfway loses only
    the game it was working on.
    """

    def __init__(self, num_workers: int, database: DB, embedder: Embedder, username: str) -> None:
        self._num_workers = num_workers
        self._db = database
        self._embedder = embedder
        self._username = username

    def process(self, games: Sequence[Game]) -> None:
        if not games:
            return

        total = len(games)
        cancelled = threading.Event()
        completed = 0
        lock = threading.Lock()
        # The first error wins, mirroring Go's one-slot error channel. Later
        # failures are dropped rather than racing to overwrite it, so the
        # message names the game that actually stopped the run.
        first_error: BaseException | None = None

        def report(game: Game, worker_id: int) -> None:
            nonlocal completed
            with lock:
                completed += 1
                done = completed
            print(
                f"[{done}/{total}] Worker {worker_id}: "
                f"{game.white.username} vs {game.black.username}"
            )

        # Each worker drains from one shared iterator, so a slow game does not
        # leave a worker idle while another has a queue.
        queue = iter(games)
        queue_lock = threading.Lock()

        def run(worker_id: int) -> None:
            # One engine per worker, started inside the worker so a failure to
            # start is attributed to it rather than to the pool.
            with Engine() as engine:
                while not cancelled.is_set():
                    with queue_lock:
                        game = next(queue, None)
                    if game is None:
                        return
                    try:
                        process_game(engine, self._db, self._embedder, self._username, game)
                    except Exception as err:
                        nonlocal first_error
                        with lock:
                            if first_error is None:
                                first_error = RuntimeError(
                                    f"worker {worker_id}: game {game.uuid}: {err}"
                                )
                                first_error.__cause__ = err
                        cancelled.set()
                        return
                    report(game, worker_id)

        with ThreadPoolExecutor(max_workers=self._num_workers) as pool:
            futures = [pool.submit(run, i) for i in range(self._num_workers)]
            # FIRST_EXCEPTION covers the case a worker fails before its own
            # try block — starting Stockfish, most likely.
            wait(futures, return_when=FIRST_EXCEPTION)
            cancelled.set()
            for future in futures:
                future.result()

        if first_error is not None:
            raise first_error
