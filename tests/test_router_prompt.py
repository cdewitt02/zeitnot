"""What the assembled prompt must never contain, and what it must.

Unmarked on purpose: no database, no provider, no goldens. The only coverage
this module had was `test_parity_prompt.py`, which is `corpus`-marked *and*
`golden`-marked — so it is deselected in CI and skipped on any machine whose
corpus is not the frozen 74-game capture. Two defects that shipped to a real
session were therefore invisible to every check that actually runs:

1. Every retrieved game was labelled with its opponent's Chess.com handle, and
   the instructions told the model to repeat it. With a hosted chat provider
   that is a third party's username leaving the machine, which the README
   promises does not happen.
2. A bucket was crowned STRONGEST on win rate alone. On a real corpus that named
   17 bullet games at 243 CPL the player's best time control over 176 blitz
   games at 154, and both a 3B and a 20B local model repeated the verdict.

Neither is a formatting preference, so both get a test that fails loudly.
"""

from __future__ import annotations

from typing import cast

from zeitnot.chat.classifier import QueryType, classify_query, mentions_openings
from zeitnot.chat.router import QueryContext, QueryRouter
from zeitnot.db import DB
from zeitnot.db.records import GameRecord, SimilarGameResult
from zeitnot.models import ColorStats, OpeningStats, PlayerStats
from zeitnot.search.hybrid import HybridSearcher

USERNAME = "cdew4"
OPPONENT = "kaldhdalalq19"


def _router() -> QueryRouter:
    """`build_prompt` reads neither collaborator — it formats a QueryContext
    that `route` has already filled in. Casting None is what keeps this test in
    the no-database subset; if the prompt path ever grows a query of its own,
    this raises rather than quietly passing."""
    return QueryRouter(cast(DB, None), cast(HybridSearcher, None), USERNAME, 100)


def _stats() -> PlayerStats:
    """The real corpus's shape: one dominant bucket, one that wins more often on
    a seventh of the games, and two buckets of exactly one game."""
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


def _games(count: int = 2) -> list[SimilarGameResult]:
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


def _prompt(query_type: QueryType, *, about_openings: bool = False) -> str:
    return _router().build_prompt(
        QueryContext(
            query_type=query_type,
            player_stats=_stats(),
            games=_games(),
            about_openings=about_openings,
        ),
        detail_limit=10,
    )


# ---------- no opponent reaches a hosted provider ----------


def test_no_opponent_username_appears_in_the_prompt() -> None:
    """The README's promise, as an assertion.

    The handle is on the GameRecord — retrieval needs it to know which side the
    player was — so this is about what gets *written*, not what gets loaded.
    """
    for query_type in QueryType:
        prompt = _prompt(query_type)
        assert OPPONENT not in prompt, query_type
        assert "[vs " not in prompt, query_type


def test_the_instructions_never_ask_for_an_opponent_username() -> None:
    """The leak had two halves: the label, and an instruction to repeat it.
    Removing one without the other leaves a model inventing handles instead."""
    for query_type in QueryType:
        prompt = _prompt(query_type)
        assert "USERNAME" not in prompt, query_type
        assert "opponent username" not in prompt, query_type


def test_games_are_cited_by_position() -> None:
    prompt = _prompt(QueryType.SPECIFIC_GAMES)
    assert "Game 1: won as white in bullet." in prompt
    assert "Game 2: " in prompt
    assert "identify them by label, e.g. Game 3" in prompt


def test_a_stale_summary_has_its_termination_line_dropped() -> None:
    """The fourth leak path, and the last one.

    P0-8 normalized the termination when a summary is *written*. Every game
    ingested before that still has the handle in `summary_text`, retrieval hands
    it straight to the prompt, and no session can repair the stored row. Found
    live on a 195-game corpus with all three other fixes already in place.
    """
    games = _games(1)
    games[0].summary_text = (
        "lost as white in bullet.\nPlayed Modern Defense.\n"
        f"Game length: Long game.\nTermination type: {OPPONENT} won on time.\n"
        "Opponent rating: 1038.\n"
    )
    prompt = _router().build_prompt(
        QueryContext(query_type=QueryType.SPECIFIC_GAMES, player_stats=_stats(), games=games),
        detail_limit=10,
    )
    assert OPPONENT not in prompt
    assert "Termination type:" not in prompt
    # Everything else about the game survives — it is anonymous without that line.
    assert "lost as white in bullet." in prompt
    assert "Played Modern Defense." in prompt
    assert "Opponent rating: 1038." in prompt


def test_a_normalized_summary_keeps_its_termination_line() -> None:
    games = _games(1)
    games[
        0
    ].summary_text = (
        "lost as white in bullet.\nTermination type: lost on time.\nOpponent rating: 1038.\n"
    )
    prompt = _router().build_prompt(
        QueryContext(query_type=QueryType.SPECIFIC_GAMES, player_stats=_stats(), games=games),
        detail_limit=10,
    )
    assert "Termination type: lost on time." in prompt


def test_the_opponent_rating_survives_as_the_discriminator() -> None:
    """Anonymising must not make the games indistinguishable to the reader."""
    assert "Opponent rating: 1000." in _prompt(QueryType.SPECIFIC_GAMES)


# ---------- no verdict a sample cannot support ----------


def test_a_one_game_bucket_is_reported_without_a_comparison() -> None:
    prompt = _prompt(QueryType.AGGREGATE)
    assert "- daily: 1 game, 100.0% win rate, 563.9 avg CPL (only 1 game - too few to compare)" in (
        prompt
    )
    assert "45.1% ABOVE overall" not in prompt


def test_a_one_game_bucket_is_never_named_in_a_superlative() -> None:
    prompt = _prompt(QueryType.COMPARATIVE)
    for line in prompt.splitlines():
        if "HIGHEST win rate" in line or "LOWEST win rate" in line:
            assert "daily" not in line, line
            assert "rapid" not in line, line


def test_win_rate_alone_never_crowns_a_time_control() -> None:
    prompt = _prompt(QueryType.COMPARATIVE)
    assert "STRONGEST time control" not in prompt
    assert "WEAKEST time control" not in prompt
    assert "HIGHEST win rate (min 3 games): bullet - 64.7% over 17 games, 243.0 avg CPL" in prompt
    assert "LOWEST win rate (min 3 games): blitz - 53.4% over 176 games, 153.9 avg CPL" in prompt


def test_a_higher_win_rate_on_worse_accuracy_says_so() -> None:
    """The line that stops "bullet is your best time control"."""
    prompt = _prompt(QueryType.COMPARATIVE)
    assert "NOTE: bullet has the higher win rate but the WORSE accuracy" in prompt
    assert "243.0 vs 153.9 avg CPL, lower is better" in prompt


def test_the_note_is_absent_when_the_two_measures_agree() -> None:
    """It is a contradiction warning, not decoration."""
    stats = _stats()
    stats.stats_by_time_class["bullet"] = ColorStats(
        games=17, wins=11, losses=6, avg_cpl=100.0, win_rate=64.7
    )
    prompt = _router().build_prompt(
        QueryContext(query_type=QueryType.COMPARATIVE, player_stats=stats, games=_games()),
        detail_limit=10,
    )
    assert "WORSE accuracy" not in prompt


def test_counts_are_pluralized() -> None:
    prompt = _prompt(QueryType.AGGREGATE)
    assert "1 games" not in prompt


def test_the_model_is_told_not_to_invent_what_it_was_not_given() -> None:
    """No move list reaches the prompt, and both models tested supplied one from
    memory and attributed it to the player."""
    prompt = _prompt(QueryType.SPECIFIC_GAMES)
    assert "Do not invent moves, move orders, ECO codes, opponent names, or dates" in prompt


# ---------- an opening question gets the opening stats ----------


def test_the_readme_opening_question_still_classifies_as_specific_games() -> None:
    """Guards the decision not to reroute: classification is a tracked golden."""
    assert classify_query("What openings do I lose with most often?") is QueryType.SPECIFIC_GAMES


def test_an_opening_question_is_recognized_without_naming_an_opening() -> None:
    assert mentions_openings("What openings do I lose with most often?")
    assert mentions_openings("How is my Caro-Kann?")
    assert not mentions_openings("Which time control is my best?")


def test_opening_stats_reach_a_question_about_openings() -> None:
    """Without this the question the README advertises was answered from
    whichever ten games retrieval returned, with no per-opening totals at all."""
    prompt = _prompt(QueryType.SPECIFIC_GAMES, about_openings=True)
    assert "Openings by losses (most first):" in prompt
    assert "Caro Kann Defense (B12): 12 of the player's losses" in prompt
    assert "Scandinavian Defense (B01): 9 of the player's losses" in prompt


def test_opening_stats_stay_out_of_an_unrelated_question() -> None:
    assert "Most played openings" not in _prompt(QueryType.SPECIFIC_GAMES)


def test_a_two_game_opening_is_reported_without_a_comparison() -> None:
    prompt = _prompt(QueryType.SPECIFIC_GAMES, about_openings=True)
    assert (
        "Italian Game (C50): 2 games, 2W-0L-0D, 100.0% win rate, 90.0 CPL (only 2 games" in prompt
    )
    for line in prompt.splitlines():
        if "HIGHEST win rate" in line:
            assert "Italian" not in line, line


# ---------- a stale aggregate is refused, not echoed ----------


def _prompt_with_terminations(terminations: dict[str, int]) -> str:
    stats = _stats()
    stats.stats_by_termination = terminations
    return _router().build_prompt(
        QueryContext(query_type=QueryType.AGGREGATE, player_stats=stats, games=_games()),
        detail_limit=10,
    )


def test_a_normalized_termination_breakdown_is_written() -> None:
    prompt = _prompt_with_terminations({"lost by resignation": 57, "won on time": 20})
    assert "Game endings:" in prompt
    assert "- lost by resignation: 57 (29.2%)" in prompt


def test_a_stale_termination_breakdown_is_withheld_whole() -> None:
    """The third leak path, and the one that survived P0-8.

    Normalization runs when a `player_stats` row is written, so any corpus
    analyzed before that landed still has raw keys — one bucket per opponent,
    each naming them. Observed live on a 195-game corpus after every other fix
    in this change was in place.
    """
    prompt = _prompt_with_terminations(
        {"2DBEACH won by resignation": 1, "Ajmayer0673 won by resignation": 1}
    )
    assert "2DBEACH" not in prompt
    assert "Ajmayer0673" not in prompt
    assert "Game endings: not available" in prompt


def test_one_stale_key_withholds_the_whole_section() -> None:
    """Dropping the bad keys individually would leave percentages that no longer
    sum, and would still be one parser bug away from a leak."""
    prompt = _prompt_with_terminations(
        {"lost by resignation": 57, "SomeHandle won by checkmate": 1}
    )
    assert "SomeHandle" not in prompt
    assert "lost by resignation" not in prompt
    assert "Game endings: not available" in prompt


# ---------- determinism, without a corpus ----------


def test_the_prompt_is_byte_identical_across_repeated_assembly() -> None:
    first = _prompt(QueryType.RECOMMENDATION, about_openings=True)
    for _ in range(3):
        assert _prompt(QueryType.RECOMMENDATION, about_openings=True) == first
