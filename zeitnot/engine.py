"""Stockfish analysis via python-chess.

`chess.engine.SimpleEngine` replaces the hand-rolled UCI wrapper outright —
process management, the handshake, and the info parsing are all library
concerns. What is ported *exactly* is the arithmetic: `get_evaluation`,
`normalize_eval`, `classify_move`, and the structure of `analyze_game`
including its terminal-position branch. Those three functions decide every
stored `cpl` and `classification`, and they are checked against the Phase 0
grid.
"""

from __future__ import annotations

import io
import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType

import chess
import chess.engine
import chess.pgn

from zeitnot.models import MoveAnalysis

# Not configurable, deliberately. Changing it rewrites every stored cpl and
# classification, so it stays frozen for the duration of the rewrite; making it
# configurable is a post-cutover change.
ANALYSIS_DEPTH = 12


class EngineError(RuntimeError):
    """Stockfish could not be started, or died mid-analysis."""


def resolve_command() -> str:
    """The Stockfish command: `STOCKFISH_PATH` if set, else a PATH lookup.

    STOCKFISH_PATH exists because editing PATH is per-shell, easy to get wrong,
    and not where anything else about this tool is configured — the env file
    already holds every other setting. It also covers the common case of a
    binary downloaded from the Stockfish releases and left wherever it landed.
    """
    return os.environ.get("STOCKFISH_PATH", "").strip() or "stockfish"


def find_stockfish() -> str | None:
    """The executable Stockfish would actually run, or None if there is none."""
    command = resolve_command()
    if os.sep in command:
        usable = os.path.isfile(command) and os.access(command, os.X_OK)
        return command if usable else None
    return shutil.which(command)


def require_stockfish() -> None:
    """Fail before any expensive work if the engine cannot be found.

    Analysis fetches a month of games over the network and opens the database
    before a worker ever starts an engine, so without this the first sign of a
    missing Stockfish arrives well into the run.
    """
    if find_stockfish() is not None:
        return
    command = resolve_command()
    hint = (
        "  Install it — `sudo apt install stockfish`, or `brew install stockfish` —\n"
        "  or point STOCKFISH_PATH at the binary:\n"
        "    export STOCKFISH_PATH=/usr/games/stockfish\n"
        "  Debian and Ubuntu install it to /usr/games, which is on the PATH of a\n"
        "  login shell but often not otherwise, so it can be installed and still\n"
        "  not be found."
    )
    if os.sep in command:
        raise EngineError(f"Stockfish not found at {command!r} (from STOCKFISH_PATH).\n{hint}")
    raise EngineError(f"Stockfish not found: {command!r} is not on PATH.\n{hint}")


class Engine:
    """One Stockfish process.

    Each analysis worker owns its own, exactly as in the Go tree: UCI is
    stateful and a shared process would interleave `position` commands between
    workers.
    """

    def __init__(self, command: str = "") -> None:
        command = command or resolve_command()
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(command)
        except (OSError, chess.engine.EngineError) as err:
            raise EngineError(f"cannot start {command!r}: {err}") from err

    def close(self) -> None:
        self._engine.quit()

    def id_name(self) -> str:
        """What the engine calls itself over UCI — "Stockfish 16.1", say.

        `zeitnot doctor` reports it, and the bug template asks for it; both are
        otherwise asking the user to find a version string by hand.
        """
        return str(self._engine.id.get("name", ""))

    def __enter__(self) -> Engine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def analyze_position(self, board: chess.Board, depth: int) -> MoveAnalysis:
        """Search one position to a fixed depth.

        Scores are read **relative to the side to move** (`score.relative`),
        not from White's point of view. That is what raw UCI `cp` means, it is
        what the Go wrapper surfaced, and it is why `normalize_eval` exists at
        all: perspective is handled in exactly one place, by flipping on odd
        move indices. Reading `score.white()` here instead type-checks, runs,
        and silently negates every Black-to-move evaluation.
        """
        try:
            info = self._engine.analyse(board, chess.engine.Limit(depth=depth))
        except chess.engine.EngineError as err:
            raise EngineError(f"analysis failed: {err}") from err

        score = info.get("score")
        pov = score.relative if score is not None else None

        mate_in = pov.mate() if pov is not None else None
        centipawns = pov.score() if pov is not None else None

        pv = info.get("pv") or []
        best_move = pv[0].uci() if pv else ""

        return MoveAnalysis(
            best_move=best_move,
            # A mate score has no centipawn value; Go's uci package reported 0
            # for CP in that case, and get_evaluation ignores it anyway.
            evaluation=0 if centipawns is None else centipawns,
            is_mate=mate_in is not None and mate_in != 0,
            mate_in=0 if mate_in is None else mate_in,
            pv=[move.uci() for move in pv],
            depth=int(info.get("depth", 0) or 0),
        )


def probe() -> str:
    """Start the engine, ask what it is, and shut it down.

    `require_stockfish` answers "is there a file at that path". This answers
    "does it run", which is the question a setup check is really asking: a
    binary built for another architecture, or a wrapper script that never speaks
    UCI, passes the first and fails the second — and would otherwise surface in
    an analysis worker, after a month of games had already been fetched.
    """
    with start_engine() as running:
        return running.id_name()


@contextmanager
def start_engine(command: str = "") -> Generator[Engine, None, None]:
    engine = Engine(command)
    try:
        yield engine
    finally:
        engine.close()


def get_evaluation(analysis: MoveAnalysis) -> int:
    """Collapse a mate score and a centipawn score onto one integer scale.

    A mate in 1 is 9999 and a mate in 5 is 9995, so a faster mate is better;
    being mated is the mirror image. Everything else is the raw centipawn value.
    """
    if analysis.is_mate:
        if analysis.mate_in > 0:
            return 10000 - analysis.mate_in
        return -10000 - analysis.mate_in
    return analysis.evaluation


def normalize_eval(evaluation: int, move_index: int) -> int:
    """Flip a White-relative score to the side to move at `move_index`."""
    if move_index % 2 == 1:
        return -evaluation
    return evaluation


def classify_move(cpl: int) -> str:
    if cpl <= 0:
        return "best"
    if cpl <= 50:
        return "good"
    if cpl <= 100:
        return "inaccuracy"
    if cpl <= 200:
        return "mistake"
    return "blunder"


def read_pgn(pgn_text: str) -> chess.pgn.Game:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise EngineError("PGN contained no game")
    return game


def analyze_game(engine: Engine, pgn_text: str, depth: int) -> list[MoveAnalysis]:
    """Analyze every move of a game.

    Two searches per move — the position before, and the position after — which
    is why ingestion is dominated by Stockfish. Halving that is a real ~2x win
    and it is deliberately **not** folded in here: an optimization inside a port
    means a diff that fails can no longer be attributed.
    """
    game = read_pgn(pgn_text)

    # en_passant="fen" on every FEN below. python-chess defaults to "legal",
    # which omits the en-passant square unless a capture is actually available;
    # Go's notnil/chess emits it after any double pawn push. The stored
    # fen_before column has the square, so "fen" is the form that matches.
    board = game.board()
    positions = [board.copy(stack=False)]
    moves: list[chess.Move] = []
    for move in game.mainline_moves():
        moves.append(move)
        board.push(move)
        positions.append(board.copy(stack=False))

    analyses: list[MoveAnalysis] = []
    for i, played in enumerate(moves):
        before = engine.analyze_position(positions[i], depth)
        after_pos = positions[i + 1]

        if after_pos.is_game_over(claim_draw=False):
            # A terminal position has no evaluation to read: Stockfish has no
            # legal move to search. Score it directly rather than asking.
            # Checkmate is scored from White's side (+10000 when White
            # delivered it); every other terminal position — stalemate,
            # insufficient material, repetition — is a dead draw at 0.
            actual_eval = (10000 if i % 2 == 0 else -10000) if after_pos.is_checkmate() else 0
        else:
            after = engine.analyze_position(after_pos, depth)
            actual_eval = normalize_eval(get_evaluation(after), i + 1)

        best_eval = normalize_eval(get_evaluation(before), i)

        # Both sides' loss is measured as "how much worse than best", which is
        # why the subtraction reverses for Black.
        cpl = best_eval - actual_eval if i % 2 == 0 else actual_eval - best_eval
        cpl = max(cpl, 0)

        classification = classify_move(cpl)

        # Playing the engine's own recommendation is zero loss by definition,
        # even when the two searches disagree by a few centipawns at this depth.
        if before.best_move and played.uci() == before.best_move:
            classification = "best"
            cpl = 0

        analyses.append(
            MoveAnalysis(
                best_move=before.best_move,
                evaluation=before.evaluation,
                is_mate=before.is_mate,
                mate_in=before.mate_in,
                pv=before.pv,
                depth=before.depth,
                played_move=played.uci(),
                centipawn_loss=cpl,
                classification=classification,
                fen_before=positions[i].fen(en_passant="fen"),
            )
        )

    return analyses
