"""Per-move engine analysis and the summary data derived from a whole game."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class MoveAnalysis:
    """One position's evaluation plus the verdict on the move actually played.

    Moves are UCI strings rather than library objects: they are what reaches the
    `moves` table, and keeping the stored form here means the engine layer is the
    only place that has to know about python-chess.
    """

    evaluation: int = 0  # centipawns, +150 = white up 1.5 pawns
    is_mate: bool = False
    mate_in: int = 0
    best_move: str = ""
    pv: list[str] = field(default_factory=list)
    depth: int = 0
    played_move: str = ""
    centipawn_loss: int = 0
    classification: str = ""
    fen_before: str = ""


@dataclass(slots=True)
class PhaseStats:
    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0
    total_cpl: int = 0
    move_count: int = 0


@dataclass(slots=True)
class GameSummaryData:
    result: str = ""  # "won", "lost", "drew" — from the player's perspective
    player_color: str = ""
    time_class: str = ""
    opening_name: str = ""
    eco_code: str = ""

    total_moves: int = 0

    opening: PhaseStats = field(default_factory=PhaseStats)
    middlegame: PhaseStats = field(default_factory=PhaseStats)
    endgame: PhaseStats = field(default_factory=PhaseStats)

    biggest_swing: int = 0  # largest single-move CPL by the player
    biggest_swing_move: int = 0
    was_winning: bool = False
    was_losing: bool = False

    termination_type: str = ""
    opponent_rating: int = 0


@dataclass(slots=True)
class YearMonth:
    year: int
    month: str  # a string, because Chess.com's URL needs the leading zero

    def __post_init__(self) -> None:
        """Reject a month the Chess.com URL cannot express.

        An unpadded month builds a path Chess.com answers with 404, which is
        indistinguishable from a misspelled username at the HTTP layer. Catching
        it here means the caller never spends a request finding out.
        """
        if not (
            len(self.month) == 2
            and self.month.isascii()
            and self.month.isdigit()
            and 1 <= int(self.month) <= 12
        ):
            raise ValueError(
                f"invalid month {self.month!r}: expected 01-12, with the leading zero "
                "(Chess.com's URL requires two digits)"
            )
