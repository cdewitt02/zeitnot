"""Pre-computed aggregate statistics for one player.

The dimensional breakdowns are dicts because they are stored as JSON columns.
They are also what the assembled prompt is built from, so every iteration over
one of them is sorted at the point of use — see `zeitnot.chat.router`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PeriodStats:
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    avg_cpl: float = 0.0
    win_rate: float = 0.0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> PeriodStats:
        return cls(
            games=int(raw.get("games", 0) or 0),
            wins=int(raw.get("wins", 0) or 0),
            losses=int(raw.get("losses", 0) or 0),
            draws=int(raw.get("draws", 0) or 0),
            avg_cpl=float(raw.get("avg_cpl", 0.0) or 0.0),
            win_rate=float(raw.get("win_rate", 0.0) or 0.0),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "avg_cpl": self.avg_cpl,
            "win_rate": self.win_rate,
        }


@dataclass(slots=True)
class ColorStats:
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    avg_cpl: float = 0.0
    win_rate: float = 0.0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ColorStats:
        return cls(
            games=int(raw.get("games", 0) or 0),
            wins=int(raw.get("wins", 0) or 0),
            losses=int(raw.get("losses", 0) or 0),
            draws=int(raw.get("draws", 0) or 0),
            avg_cpl=float(raw.get("avg_cpl", 0.0) or 0.0),
            win_rate=float(raw.get("win_rate", 0.0) or 0.0),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "avg_cpl": self.avg_cpl,
            "win_rate": self.win_rate,
        }


# TimeClassStats and RatingBandStats carry the same fields as ColorStats and
# the same JSON shape. They stay distinct names because the Go tree named them
# separately and the prompt code reads better for it, but there is deliberately
# no third copy of the body.
TimeClassStats = ColorStats
RatingBandStats = ColorStats


@dataclass(slots=True)
class OpeningStats:
    eco_code: str = ""
    opening_name: str = ""
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    avg_cpl: float = 0.0
    win_rate: float = 0.0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> OpeningStats:
        return cls(
            eco_code=str(raw.get("eco_code", "") or ""),
            opening_name=str(raw.get("opening_name", "") or ""),
            games=int(raw.get("games", 0) or 0),
            wins=int(raw.get("wins", 0) or 0),
            losses=int(raw.get("losses", 0) or 0),
            draws=int(raw.get("draws", 0) or 0),
            avg_cpl=float(raw.get("avg_cpl", 0.0) or 0.0),
            win_rate=float(raw.get("win_rate", 0.0) or 0.0),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "eco_code": self.eco_code,
            "opening_name": self.opening_name,
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "avg_cpl": self.avg_cpl,
            "win_rate": self.win_rate,
        }


@dataclass(slots=True)
class PlayerStats:
    username: str = ""
    total_games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    avg_cpl: float = 0.0

    stats_by_color: dict[str, ColorStats] = field(default_factory=dict)
    stats_by_time_class: dict[str, TimeClassStats] = field(default_factory=dict)
    stats_by_opening: dict[str, OpeningStats] = field(default_factory=dict)
    stats_by_rating_band: dict[str, RatingBandStats] = field(default_factory=dict)

    stats_by_termination: dict[str, int] = field(default_factory=dict)

    last_30_days: PeriodStats | None = None
    last_90_days: PeriodStats | None = None

    updated_at: datetime | None = None


def rating_band(rating: int) -> str:
    """Bucket an opponent rating.

    Bands: "<1000", "1000-1200", "1200-1400", "1400-1600", "1600-1800",
    "1800-2000", "2000+".
    """
    if rating < 1000:
        return "<1000"
    if rating < 1200:
        return "1000-1200"
    if rating < 1400:
        return "1200-1400"
    if rating < 1600:
        return "1400-1600"
    if rating < 1800:
        return "1600-1800"
    if rating < 2000:
        return "1800-2000"
    return "2000+"
