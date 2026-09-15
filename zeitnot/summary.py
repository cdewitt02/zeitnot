"""Game Summary generation: pure functions over stored data.

No I/O, no LLM, no Stockfish. The summary text **is** the embedded text, so any
divergence here makes every stored embedding stale relative to its own source —
silently, because nothing errors.

The Phase 0 goldens no longer describe this module: they were captured before
the draw fix (2026-08-31) and before the phase boundaries were corrected from
plies to full moves (2026-09-14). Coverage that runs everywhere is in
`tests/test_summary.py`; see `testdata/golden/MANIFEST.md` for what is stale.
"""

from __future__ import annotations

from collections.abc import Sequence

from zeitnot.models import (
    Game,
    GameSummaryData,
    MoveAnalysis,
    PhaseStats,
    normalize_termination,
)
from zeitnot.models.game import is_normalized_termination

# The one line of a Game Summary whose value comes from Chess.com rather than
# from this module.
TERMINATION_PREFIX = "Termination type: "

# Phase boundaries in **full moves**, not plies. A full move is one move by each
# side, so full move 10 ends at ply index 19. `extract_summary_data` iterates
# plies and converts, the same way `to_move_records` does in zeitnot/ingest.py.
OPENING_END = 10  # full moves 1-10
MIDDLEGAME_END = 25  # full moves 11-25
# 26+ is endgame

WINNING_THRESHOLD = 200  # +2.00 pawns = winning
LOSING_THRESHOLD = -200


def _player_eval(move: MoveAnalysis, player_color: str) -> int:
    """Convert Stockfish's evaluation to the player's perspective."""
    if move.is_mate:
        evaluation = 10000 - move.mate_in if move.mate_in > 0 else -10000 - move.mate_in
    else:
        evaluation = move.evaluation

    if player_color == "black":
        evaluation = -evaluation
    return evaluation


def extract_summary_data(
    game: Game, moves: Sequence[MoveAnalysis], username: str
) -> GameSummaryData:
    player_color = "white" if username == game.white.username else "black"

    winner = game.game_result()
    if winner == player_color:
        result = "won"
    elif winner == "draw":
        # Was `winner == ""` until 2026-08-31, which game_result() never returns
        # — so every draw fell through to "lost". Preserved through the port on
        # purpose (any diff there was supposed to mean a porting bug), fixed
        # here as its own change. Summaries written before the fix say "lost"
        # for a draw; `zeitnot data reembed` after regenerating them realigns
        # the vectors with their own text.
        result = "drew"
    else:
        result = "lost"

    opponent_rating = game.black.rating if player_color == "white" else game.white.rating

    opening_stats = PhaseStats()
    middlegame_stats = PhaseStats()
    endgame_stats = PhaseStats()
    biggest_swing = 0
    biggest_swing_move = 0
    was_winning = False
    was_losing = False

    for i, move in enumerate(moves):
        # Track the evaluation at every move — both players' — to detect
        # whether the player was ever winning or losing.
        evaluation = _player_eval(move, player_color)
        if evaluation > WINNING_THRESHOLD:
            was_winning = True
        if evaluation < LOSING_THRESHOLD:
            was_losing = True

        # Only track CPL and phase stats for the player's own moves.
        is_player_move = (player_color == "white" and i % 2 == 0) or (
            player_color == "black" and i % 2 == 1
        )
        if not is_player_move:
            continue

        if move.centipawn_loss > biggest_swing:
            biggest_swing = move.centipawn_loss
            # A ply number despite the name. Neither this nor `biggest_swing`
            # reaches the summary text, so nothing renders it today; whatever
            # first does has to convert it or label it as a ply.
            biggest_swing_move = i + 1

        # `i` indexes plies, and the boundaries above are full moves. Reading the
        # index as a move number is what made "opening" mean the first five full
        # moves and swept most of the middlegame into the endgame bucket — see
        # docs/opensource-readiness/02-open-questions.md Q8.
        move_number = i // 2 + 1
        if move_number <= OPENING_END:
            phase = opening_stats
        elif move_number <= MIDDLEGAME_END:
            phase = middlegame_stats
        else:
            phase = endgame_stats

        phase.move_count += 1
        phase.total_cpl += move.centipawn_loss
        if move.classification == "blunder":
            phase.blunders += 1
        elif move.classification == "mistake":
            phase.mistakes += 1
        elif move.classification == "inaccuracy":
            phase.inaccuracies += 1

    return GameSummaryData(
        result=result,
        player_color=player_color,
        time_class=game.time_class,
        opening_name=game.opening_name(),
        eco_code=game.eco_code(),
        total_moves=len(moves),
        opening=opening_stats,
        middlegame=middlegame_stats,
        endgame=endgame_stats,
        biggest_swing=biggest_swing,
        biggest_swing_move=biggest_swing_move,
        was_winning=was_winning,
        was_losing=was_losing,
        termination_type=normalize_termination(game.termination_type(), result),
        opponent_rating=opponent_rating,
    )


def generate_summary(data: GameSummaryData) -> str:
    """Render the Game Summary.

    Every field is formatted with `%s` or `%d` in the Go original — no float
    ever reaches a string here — so there is no cross-language float-formatting
    risk in this function. The trailing newline on the last line is deliberate:
    the stored `summary_text` has it.
    """
    total_blunders = data.opening.blunders + data.middlegame.blunders + data.endgame.blunders
    total_mistakes = data.opening.mistakes + data.middlegame.mistakes + data.endgame.mistakes
    total_inaccuracies = (
        data.opening.inaccuracies + data.middlegame.inaccuracies + data.endgame.inaccuracies
    )

    return (
        f"{data.result} as {data.player_color} in {data.time_class}.\n"
        f"Played {data.opening_name}.\n"
        f"{data.player_color} performance with {total_blunders} blunders, "
        f"{total_mistakes} mistakes, and {total_inaccuracies} inaccuracies.\n"
        f"{weakest_phase(data.opening, data.middlegame, data.endgame)}.\n"
        f"{detect_pattern(data)}.\n"
        f"Game length: {classify_game_length(data.total_moves)}.\n"
        f"Termination type: {data.termination_type}.\n"
        f"Opponent rating: {data.opponent_rating}.\n"
    )


def classify_game_length(total_moves: int) -> str:
    if total_moves < 20:
        return "Short game"
    if total_moves < 40:
        return "Medium length game"
    return "Long game"


def weakest_phase(opening: PhaseStats, middlegame: PhaseStats, endgame: PhaseStats) -> str:
    """Name the phase with the highest average centipawn loss.

    PARITY: "Endgame was weakest" is the `else` catch-all, so **any tie between
    two phase averages is reported as an endgame weakness** rather than as a
    tie. Reconstructing this across all 74 stored games: 53 endgame verdicts are
    strictly correct, 20 middlegame, 1 opening, and zero reach the tie-fallback.

    The defect is real and currently unreached, and it is preserved exactly
    anyway. "Unreached on today's corpus" is not "unreachable": the first user
    with different games hits the branch, the two implementations diverge, and
    nothing fails — because the goldens were captured from a corpus where it
    never fired. Fixing it is a post-cutover change with its own verification,
    and it makes the stored corpus internally inconsistent until summaries are
    regenerated.

    The three divisions are int-to-float and IEEE-754 in both languages, so the
    bits and the `>` results are identical. No epsilon is involved.
    """
    opening_avg = opening.total_cpl / opening.move_count if opening.move_count > 0 else 0.0
    middlegame_avg = (
        middlegame.total_cpl / middlegame.move_count if middlegame.move_count > 0 else 0.0
    )
    endgame_avg = endgame.total_cpl / endgame.move_count if endgame.move_count > 0 else 0.0

    if opening_avg > middlegame_avg and opening_avg > endgame_avg:
        return "Opening was weakest"
    if middlegame_avg > opening_avg and middlegame_avg > endgame_avg:
        return "Middlegame was weakest"
    return "Endgame was weakest"


def detect_pattern(data: GameSummaryData) -> str:
    """Describe how the game unfolded.

    A matrix over whether the player was ever winning (eval > +200) or losing
    (eval < -200) at any point, crossed with the result.
    """
    if data.result == "won":
        if data.was_losing:
            return "Came back from losing position"
        if data.was_winning:
            return "Steady advantage throughout"
        return "Converted a close game"

    if data.result == "lost":
        if data.was_winning:
            return "Threw a winning position"
        if data.was_losing:
            return "Was outplayed"
        return "Lost a close game"

    # Live since the draw fix above; unreachable before it, which is why these
    # four verdicts had never been emitted by either implementation.
    if data.result == "drew":
        if data.was_winning and not data.was_losing:
            return "Missed winning opportunity"
        if data.was_losing and not data.was_winning:
            return "Saved a draw from worse position"
        if data.was_winning and data.was_losing:
            return "Wild game ended in draw"
        return "Even game throughout"

    return "Unknown pattern"


def strip_unnormalized_termination(summary_text: str) -> str:
    """Drop the termination line when it predates `normalize_termination`.

    **A stored summary is not necessarily a safe summary.** Normalization runs
    when a summary is generated, so every game ingested before readiness P0-8
    landed still has "FLAJarda won on time" sitting in `summary_text`, and that
    text is retrieved and placed in the prompt verbatim. Re-ingesting fixes the
    stored row; nothing in a chat session can, because the row is what there is.

    So the line is dropped rather than repaired. The result, colour, opening,
    error counts and opponent rating all survive — the summary is anonymous
    without this line, which is exactly what P0-8 established when it removed the
    handle from newly written ones.

    A summary written by the current tree is returned unchanged.
    """
    kept: list[str] = []
    for line in summary_text.split("\n"):
        value = line.strip()
        if value.startswith(TERMINATION_PREFIX):
            termination = value[len(TERMINATION_PREFIX) :].strip().rstrip(".").strip()
            if termination and not is_normalized_termination(termination):
                continue
        kept.append(line)
    return "\n".join(kept)
