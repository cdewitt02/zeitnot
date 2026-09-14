"""Row types. One dataclass per table shape the queries return."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class GameRecord:
    uuid: str = ""
    url: str = ""
    pgn: str = ""
    eco_code: str = ""
    eco_name: str = ""
    white_username: str = ""
    white_rating: int = 0
    black_username: str = ""
    black_rating: int = 0
    result: str = ""  # "white", "black", "draw"
    termination_type: str = ""  # "checkmate", "resignation", "timeout", etc.
    time_control: str = ""
    time_class: str = ""
    rated: bool = False
    avg_cpl_white: float = 0.0
    avg_cpl_black: float = 0.0
    blunders_white: int = 0
    blunders_black: int = 0
    mistakes_white: int = 0
    mistakes_black: int = 0
    inaccuracies_white: int = 0
    inaccuracies_black: int = 0
    best_moves_white: int = 0
    best_moves_black: int = 0
    played_at: datetime | None = None


@dataclass(slots=True)
class MoveRecord:
    id: int = 0
    game_uuid: str = ""
    move_number: int = 0
    side: str = ""  # "white" or "black"
    played_move: str = ""  # UCI notation: "e2e4"
    best_move: str = ""  # UCI notation: "e2e4"
    fen_before: str = ""
    evaluation: int = 0  # centipawns
    is_mate: bool = False
    mate_in: int = 0
    cpl: int = 0  # centipawn loss
    classification: str = ""  # "best", "good", "inaccuracy", "mistake", "blunder"


@dataclass(slots=True)
class GameSummary:
    game_uuid: str = ""
    summary_text: str = ""
    embedding: list[float] = field(default_factory=list)


@dataclass(slots=True)
class SimilarGameResult:
    game_uuid: str = ""
    summary_text: str = ""
    distance: float = 0.0
    game: GameRecord | None = None


@dataclass(slots=True)
class OpeningStatsRow:
    """The GetOpeningStats aggregate. Distinct from models.OpeningStats, which
    is the per-player dimensional breakdown stored as JSON."""

    eco_code: str = ""
    eco_name: str = ""
    games_as_white: int = 0
    games_as_black: int = 0
    wins_as_white: int = 0
    wins_as_black: int = 0
    avg_cpl_as_white: float = 0.0
    avg_cpl_as_black: float = 0.0


@dataclass(slots=True)
class IndexMeta:
    """Which embedder produced the vectors in game_summaries.

    Width alone cannot catch a provider change: two 768-dimension models occupy
    different vector spaces, so mixing them degrades retrieval silently.
    """

    embed_provider: str = ""
    embed_model: str = ""
    dimensions: int = 0


@dataclass(slots=True)
class SummaryTextRow:
    """One stored summary awaiting a fresh vector."""

    game_uuid: str = ""
    summary_text: str = ""
