"""Domain types shared across the package."""

from zeitnot.models.analysis import (
    GameSummaryData,
    MoveAnalysis,
    PhaseStats,
    YearMonth,
)
from zeitnot.models.game import (
    Game,
    Player,
    canonical_username,
    normalize_termination,
    same_username,
)
from zeitnot.models.player_stats import (
    ColorStats,
    OpeningStats,
    PeriodStats,
    PlayerStats,
    RatingBandStats,
    TimeClassStats,
    rating_band,
)

__all__ = [
    "ColorStats",
    "Game",
    "GameSummaryData",
    "MoveAnalysis",
    "OpeningStats",
    "PeriodStats",
    "PhaseStats",
    "Player",
    "PlayerStats",
    "RatingBandStats",
    "TimeClassStats",
    "YearMonth",
    "canonical_username",
    "normalize_termination",
    "rating_band",
    "same_username",
]
