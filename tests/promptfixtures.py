"""A synthetic QueryContext with the shape of a real corpus.

Shared by `test_router_prompt.py`, which asserts individual properties of the
assembled prompt, and `test_prompt_snapshot.py`, which pins the whole thing.
Both need the same context and neither needs a database: `build_prompt` reads no
collaborator, it formats a `QueryContext` that `route` has already filled in.

The numbers are the real 195-game corpus's shape rather than round figures —
one dominant bucket, one that wins more often on a seventh of the games, and two
buckets of exactly one game. That is what makes the `MIN_GAMES_FOR_COMPARISON`
floor and the win-rate-versus-accuracy note reachable at all.
"""

from __future__ import annotations

from typing import cast

from zeitnot.chat.classifier import QueryType
from zeitnot.chat.router import QueryContext, QueryRouter
from zeitnot.db import DB
from zeitnot.db.records import GameRecord, SimilarGameResult
from zeitnot.models import ColorStats, OpeningStats, PlayerStats
from zeitnot.search.hybrid import HybridSearcher

USERNAME = "cdew4"
OPPONENT = "kaldhdalalq19"
DETAIL_LIMIT = 10


def make_router() -> QueryRouter:
    """Casting None is what keeps these tests in the no-database subset; if the
    prompt path ever grows a query of its own, this raises rather than quietly
    passing."""
    return QueryRouter(cast(DB, None), cast(HybridSearcher, None), USERNAME, 100)


def make_stats() -> PlayerStats:
    return PlayerStats(
        username=USERNAME,
        total_games=195,
        wins=107,
        losses=81,
        draws=7,
        avg_cpl=163.1,
        stats_by_color={
            "white": ColorStats(
                games=100, wins=48, losses=48, draws=4, avg_cpl=151.5, win_rate=48.0
            ),
            "black": ColorStats(
                games=95, wins=59, losses=33, draws=3, avg_cpl=175.3, win_rate=62.1
            ),
        },
        stats_by_time_class={
            "blitz": ColorStats(
                games=176, wins=94, losses=75, draws=7, avg_cpl=153.9, win_rate=53.4
            ),
            "bullet": ColorStats(games=17, wins=11, losses=6, avg_cpl=243.0, win_rate=64.7),
            "daily": ColorStats(games=1, wins=1, avg_cpl=563.9, win_rate=100.0),
            "rapid": ColorStats(games=1, wins=1, avg_cpl=21.5, win_rate=100.0),
        },
        stats_by_opening={
            "B01": OpeningStats(
                eco_code="B01",
                opening_name="Scandinavian Defense",
                games=24,
                wins=14,
                losses=9,
                draws=1,
                avg_cpl=150.0,
                win_rate=58.3,
            ),
            "B12": OpeningStats(
                eco_code="B12",
                opening_name="Caro Kann Defense",
                games=18,
                wins=6,
                losses=12,
                avg_cpl=170.0,
                win_rate=33.3,
            ),
            "C50": OpeningStats(
                eco_code="C50",
                opening_name="Italian Game",
                games=2,
                wins=2,
                avg_cpl=90.0,
                win_rate=100.0,
            ),
        },
    )


def make_games(count: int = 2) -> list[SimilarGameResult]:
    return [
        SimilarGameResult(
            game_uuid=f"uuid-{i}",
            summary_text=(
                f"won as white in bullet.\nPlayed Scandinavian Defense.\n"
                f"Opponent rating: {1000 + i}.\n"
            ),
            distance=0.1,
            game=GameRecord(
                uuid=f"uuid-{i}",
                white_username=USERNAME,
                black_username=OPPONENT,
                black_rating=1000 + i,
            ),
        )
        for i in range(count)
    ]


def make_prompt(
    query_type: QueryType,
    *,
    about_openings: bool = False,
    mentioned_openings: list[str] | None = None,
) -> str:
    return make_router().build_prompt(
        QueryContext(
            query_type=query_type,
            player_stats=make_stats(),
            games=make_games(),
            about_openings=about_openings,
            mentioned_openings=mentioned_openings or [],
        ),
        detail_limit=DETAIL_LIMIT,
    )
