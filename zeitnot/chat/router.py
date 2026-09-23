"""Assembles the prompt: classify the question, gather context, format it.

**This module's output is the parity target for the whole rewrite.** Every input
the chat provider receives is the assembled prompt, so a matching prompt leaves
nothing downstream for the port to have gotten wrong. It was checked byte for
byte against the Phase 0 goldens for the twelve frozen questions — **until
2026-09-14**, when the prompt was deliberately changed to stop leaking opponent
usernames and to stop crowning a bucket on win rate alone. Those goldens now
record the previous text; see `testdata/golden/MANIFEST.md`.

Two things this module must not do, both of which it did:

- **No opponent username reaches the prompt.** A hosted chat provider receives
  this text verbatim, and the opponent never agreed to that. See
  `_write_game_context`.
- **No superlative rests on a percentage the sample cannot support.** Anything
  under `MIN_GAMES_FOR_COMPARISON` games gets its numbers reported and its
  comparison withheld.

Two consequences run through the file:

- **Every dict that reaches the prompt is iterated in sorted order.** Go
  randomizes map iteration and seven sites here let that reach the output, which
  made two identical runs produce different prompts. Python dicts are ordered,
  so the risk is subtler but real: insertion order depends on the order rows
  come back from a query. Sorting makes the prompt depend on the data.
- **Float formatting is `%.1f` throughout.** Go's fmt and Python's format spec
  both round the exact binary value half to even, verified identical across
  half-way cases (0.25 -> 0.2, 1.25 -> 1.2 in both), so no normalization step is
  needed on either side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO

from zeitnot.chat.classifier import (
    QueryType,
    classify_query,
    extract_mentioned_openings,
    mentions_openings,
)
from zeitnot.db import DB
from zeitnot.db.records import SimilarGameResult
from zeitnot.models import ColorStats, OpeningStats, PlayerStats, RatingBandStats, TimeClassStats
from zeitnot.models.game import is_normalized_termination
from zeitnot.search.hybrid import HybridSearcher, SearchQuery
from zeitnot.summary import strip_unnormalized_termination


def format_win_rate_comparison(rate: float, baseline: float) -> str:
    """A pre-computed comparison string for win rates.

    Pre-computed because a small local model reliably misreads "36.8% vs 32.4%"
    and reliably reads "4.4% ABOVE overall".
    """
    delta = rate - baseline
    if delta > 0.5:
        return f"({delta:.1f}% ABOVE overall)"
    if delta < -0.5:
        return f"({-delta:.1f}% BELOW overall)"
    return "(≈ same as overall)"


def format_cpl_comparison(cpl: float, baseline: float) -> str:
    """The same for centipawn loss, where lower is better."""
    delta = cpl - baseline
    if delta < -5:
        return f"({-delta:.1f} BETTER than overall)"
    if delta > 5:
        return f"({delta:.1f} WORSE than overall)"
    return "(≈ same as overall)"


# Below this many games a percentage is noise. What gets withheld from a small
# bucket is the *comparison*, never the row: the count and the rate are real,
# and dropping them would read as missing data. A one-game bucket rendered as
# "100.0% win rate (45.1% ABOVE overall)" is a true sentence that every model
# tested read as a finding.
MIN_GAMES_FOR_COMPARISON = 3


def format_games(count: int) -> str:
    """ "1 game", not "1 games". It appears in every dimensional row."""
    return "1 game" if count == 1 else f"{count} games"


def format_dimension_row(
    label: str,
    games: int,
    win_rate: float,
    avg_cpl: float,
    overall_win_rate: float,
    overall_cpl: float,
) -> str:
    """One row of a dimensional breakdown — by color, by time control.

    Identical in shape for every dimension, so the small-sample rule is written
    once and cannot be applied to one breakdown and forgotten on the next.
    """
    if games < MIN_GAMES_FOR_COMPARISON:
        return (
            f"- {label}: {format_games(games)}, {win_rate:.1f}% win rate, "
            f"{avg_cpl:.1f} avg CPL "
            f"(only {format_games(games)} - too few to compare)\n"
        )
    return (
        f"- {label}: {format_games(games)}, {win_rate:.1f}% win rate "
        f"{format_win_rate_comparison(win_rate, overall_win_rate)}, "
        f"{avg_cpl:.1f} avg CPL "
        f"{format_cpl_comparison(avg_cpl, overall_cpl)}\n"
    )


@dataclass(slots=True)
class QueryContext:
    """Everything gathered to answer one question."""

    query_type: QueryType
    player_stats: PlayerStats | None = None
    games: list[SimilarGameResult] = field(default_factory=list[SimilarGameResult])
    filters: list[str] = field(default_factory=list[str])
    mentioned_openings: list[str] = field(default_factory=list[str])
    # Whether the question is about openings, which is not the same as which
    # type it is. See QueryRouter._write_player_stats.
    about_openings: bool = False


class QueryRouter:
    def __init__(
        self,
        database: DB,
        searcher: HybridSearcher,
        username: str,
        num_similar: int,
    ) -> None:
        self._db = database
        self._searcher = searcher
        self._username = username
        self._num_similar = num_similar

    def route(self, question: str) -> QueryContext:
        """Classify the query and gather the context its type calls for."""
        query_type = classify_query(question)
        qctx = QueryContext(
            query_type=query_type,
            mentioned_openings=extract_mentioned_openings(question),
            about_openings=mentions_openings(question),
            player_stats=self._db.get_player_stats(self._username),
        )

        if query_type in (
            QueryType.SPECIFIC_GAMES,
            QueryType.RECOMMENDATION,
            QueryType.TREND,
        ):
            # Retrieval failure is fatal here: these types have nothing to say
            # without games.
            qctx.games, qctx.filters = self._search_games(question)
        else:
            # Aggregate and comparative answers come from the stats, so games
            # are illustration. A retrieval failure degrades rather than fails,
            # and only three examples are kept.
            try:
                games, filters = self._search_games(question)
            except Exception:
                qctx.games = []
            else:
                qctx.games = games[:3]
                qctx.filters = filters

        return qctx

    def _search_games(self, question: str) -> tuple[list[SimilarGameResult], list[str]]:
        result = self._searcher.search(
            SearchQuery(query=question, top_k=self._num_similar), self._username
        )
        return result.games, result.extracted_filters

    def build_prompt(self, qctx: QueryContext, detail_limit: int) -> str:
        """Create the system prompt for this question."""
        sb = StringIO()

        sb.write(f"You are a chess coach for {self._username}. ")
        sb.write(
            "Your role is to provide insightful analysis based on the player's game history.\n\n"
        )

        stats = qctx.player_stats
        if stats is not None and stats.total_games > 0:
            self._write_player_stats(sb, stats, qctx.query_type, qctx.about_openings)

            if qctx.query_type is QueryType.TREND:
                self._write_trend_stats(sb, stats)

            if qctx.mentioned_openings:
                self._write_mentioned_opening_stats(sb, stats, qctx.mentioned_openings)

        if qctx.games:
            self._write_game_context(sb, qctx.games, detail_limit, qctx.query_type)

        self._write_instructions(sb, qctx.query_type)

        return sb.getvalue()

    # ---------- player overview ----------

    def _write_player_stats(
        self,
        sb: StringIO,
        stats: PlayerStats,
        query_type: QueryType,
        about_openings: bool = False,
    ) -> None:
        sb.write("PLAYER OVERVIEW (from all analyzed games):\n")
        sb.write(f"- Total games analyzed: {stats.total_games}\n")

        overall_win_rate = 0.0
        if stats.total_games > 0:
            overall_win_rate = stats.wins / stats.total_games * 100
        sb.write(
            f"- Overall record: {stats.wins} wins, {stats.losses} losses, "
            f"{stats.draws} draws ({overall_win_rate:.1f}% win rate)\n"
        )
        sb.write(f"- Average centipawn loss: {stats.avg_cpl:.1f}\n")

        if stats.stats_by_color:
            sb.write("\nPerformance by color:\n")
            for color in sorted(stats.stats_by_color):
                s = stats.stats_by_color[color]
                sb.write(
                    format_dimension_row(
                        f"As {color}",
                        s.games,
                        s.win_rate,
                        s.avg_cpl,
                        overall_win_rate,
                        stats.avg_cpl,
                    )
                )
            white = stats.stats_by_color.get("white")
            black = stats.stats_by_color.get("black")
            if white is not None and black is not None:
                self._write_color_comparison(sb, white, black)

        if stats.stats_by_time_class:
            sb.write("\nPerformance by time control:\n")
            for tc in sorted(stats.stats_by_time_class):
                s = stats.stats_by_time_class[tc]
                sb.write(
                    format_dimension_row(
                        tc,
                        s.games,
                        s.win_rate,
                        s.avg_cpl,
                        overall_win_rate,
                        stats.avg_cpl,
                    )
                )
            self._write_time_control_insights(sb, stats.stats_by_time_class)

        # Comparative and recommendation questions get the extra dimensions.
        # The others do not, to keep the prompt from burying the answer.
        wide = query_type in (QueryType.COMPARATIVE, QueryType.RECOMMENDATION)
        if wide and stats.stats_by_rating_band:
            sb.write("\nPerformance by opponent rating:\n")
            for band in sorted(stats.stats_by_rating_band):
                s = stats.stats_by_rating_band[band]
                if s.games < MIN_GAMES_FOR_COMPARISON:
                    sb.write(
                        f"- vs {band}: {format_games(s.games)}, "
                        f"{s.win_rate:.1f}% win rate "
                        f"(only {format_games(s.games)} - too few to compare)\n"
                    )
                else:
                    sb.write(
                        f"- vs {band}: {format_games(s.games)}, "
                        f"{s.win_rate:.1f}% win rate "
                        f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}\n"
                    )
            self._write_rating_band_insights(sb, stats.stats_by_rating_band)

        # Opening aggregates follow the *question*, not its type. "What openings
        # do I lose with most often?" classifies as specific_games — retrieval
        # only — so the one block that could answer it was omitted and the answer
        # got assembled from whichever ten games came back. Classification is
        # untouched; this is an extra signal, not a reroute.
        if stats.stats_by_opening and (wide or about_openings):
            self._write_opening_stats(sb, stats.stats_by_opening, overall_win_rate)

        # Useful for questions about flagging, checkmates, and so on — and the
        # one section whose *keys* come from Chess.com rather than from this
        # codebase. Terminations are normalized when a stats row is written, so
        # a row written before that landed still reads "2DBEACH won by
        # resignation". Refusing the whole section is the only safe response: the
        # keys cannot be repaired here (the player's own result is not in the
        # aggregate), and dropping them one at a time would leave percentages
        # that no longer sum. `zeitnot data refresh-stats <username>` rebuilds it.
        if stats.stats_by_termination:
            if all(is_normalized_termination(term) for term in stats.stats_by_termination):
                sb.write("\nGame endings:\n")
                for term in sorted(stats.stats_by_termination):
                    count = stats.stats_by_termination[term]
                    pct = count / stats.total_games * 100
                    sb.write(f"- {term}: {count} ({pct:.1f}%)\n")
            else:
                sb.write(
                    "\nGame endings: not available - the stored breakdown predates the "
                    "current format and was withheld\n"
                )

        sb.write("\n")

    def _write_color_comparison(self, sb: StringIO, white: ColorStats, black: ColorStats) -> None:
        """Withheld unless both sides clear `MIN_GAMES_FOR_COMPARISON`.

        This was the one comparison in the file with no floor under it, and
        `compute_player_stats` seeds both colors whether or not either was
        played — so a first month that happened to be lopsided rendered

            - As black: 0 games, 0.0% win rate, 0.0 avg CPL (only 0 games ...)
            - As white: 2 games, 50.0% win rate, 56.4 avg CPL (only 2 games ...)
              → Direct comparison: White win rate is 50.0% HIGHER than Black;
                plays 56.4 CPL BETTER as Black

        a verdict about a color the player had never played, directly beneath
        two rows that both said the sample was too small. It cannot appear on a
        corpus of any real size — the maintainer's splits 100/95 — which is why
        it outlived the sweep that put a floor under every other bucket, and why
        only a first-time user was ever going to see it.

        Silent, like the other two insight helpers: the rows above already
        report both counts, so there is nothing to explain the absence of.
        """
        if white.games < MIN_GAMES_FOR_COMPARISON or black.games < MIN_GAMES_FOR_COMPARISON:
            return

        win_rate_delta = white.win_rate - black.win_rate
        cpl_delta = white.avg_cpl - black.avg_cpl

        sb.write("  → Direct comparison: ")
        if win_rate_delta > 1:
            sb.write(f"White win rate is {win_rate_delta:.1f}% HIGHER than Black")
        elif win_rate_delta < -1:
            sb.write(f"Black win rate is {-win_rate_delta:.1f}% HIGHER than White")
        else:
            sb.write("Win rates approximately EQUAL between colors")

        if cpl_delta < -5:
            sb.write(f"; plays {-cpl_delta:.1f} CPL BETTER as White\n")
        elif cpl_delta > 5:
            sb.write(f"; plays {cpl_delta:.1f} CPL BETTER as Black\n")
        else:
            sb.write("; similar accuracy with both colors\n")

    def _write_time_control_insights(
        self, sb: StringIO, time_classes: dict[str, TimeClassStats]
    ) -> None:
        """Highest and lowest win rate — deliberately not "strongest" and "weakest".

        The old wording crowned a bucket on win rate alone and every model tested
        repeated the verdict as given. On a real corpus that named a 17-game
        bullet sample at 243 CPL the player's STRONGEST time control, over 176
        blitz games at 154. Win rate and accuracy are two findings; which one
        decides "best" is the reader's call, not this function's — so it reports
        both and says so when they disagree.
        """
        if len(time_classes) < 2:
            return

        best: tuple[str, TimeClassStats] | None = None
        worst: tuple[str, TimeClassStats] | None = None

        for tc in sorted(time_classes):
            s = time_classes[tc]
            if s.games < MIN_GAMES_FOR_COMPARISON:
                continue
            if best is None or s.win_rate > best[1].win_rate:
                best = (tc, s)
            if worst is None or s.win_rate < worst[1].win_rate:
                worst = (tc, s)

        if best is None or worst is None or best[0] == worst[0]:
            return

        best_tc, b = best
        worst_tc, w = worst
        floor = MIN_GAMES_FOR_COMPARISON
        sb.write(
            f"  → HIGHEST win rate (min {floor} games): {best_tc} - {b.win_rate:.1f}% "
            f"over {format_games(b.games)}, {b.avg_cpl:.1f} avg CPL\n"
        )
        sb.write(
            f"  → LOWEST win rate (min {floor} games): {worst_tc} - {w.win_rate:.1f}% "
            f"over {format_games(w.games)}, {w.avg_cpl:.1f} avg CPL\n"
        )
        sb.write(f"  → Difference: {b.win_rate - w.win_rate:.1f} percentage points\n")
        if b.avg_cpl > w.avg_cpl:
            sb.write(
                f"  → NOTE: {best_tc} has the higher win rate but the WORSE accuracy "
                f"({b.avg_cpl:.1f} vs {w.avg_cpl:.1f} avg CPL, lower is better), so it is "
                f"not simply the stronger time control\n"
            )

    def _write_rating_band_insights(self, sb: StringIO, bands: dict[str, RatingBandStats]) -> None:
        if len(bands) < 2:
            return

        best: tuple[str, RatingBandStats] | None = None
        worst: tuple[str, RatingBandStats] | None = None

        for band in sorted(bands):
            s = bands[band]
            if s.games < MIN_GAMES_FOR_COMPARISON:
                continue
            if best is None or s.win_rate > best[1].win_rate:
                best = (band, s)
            if worst is None or s.win_rate < worst[1].win_rate:
                worst = (band, s)

        if best is None or worst is None or best[0] == worst[0]:
            return

        floor = MIN_GAMES_FOR_COMPARISON
        sb.write(
            f"  → HIGHEST win rate (min {floor} games): vs {best[0]} rated opponents - "
            f"{best[1].win_rate:.1f}% over {format_games(best[1].games)}\n"
        )
        sb.write(
            f"  → LOWEST win rate (min {floor} games): vs {worst[0]} rated opponents - "
            f"{worst[1].win_rate:.1f}% over {format_games(worst[1].games)}\n"
        )

    def _write_opening_stats(
        self, sb: StringIO, openings: dict[str, OpeningStats], overall_win_rate: float
    ) -> None:
        # Sorted by games played, ties broken on ECO code. Without the tie-break
        # the ordering of equal-count openings would depend on dict insertion
        # order, which depends on the order rows came back from the query.
        entries = sorted(
            ((eco, openings[eco]) for eco in sorted(openings)),
            key=lambda item: (-item[1].games, item[0]),
        )

        sb.write("\nMost played openings:\n")
        for eco, s in entries[:5]:
            name = s.opening_name or eco
            record = f"{s.wins}W-{s.losses}L-{s.draws}D"
            if s.games < MIN_GAMES_FOR_COMPARISON:
                sb.write(
                    f"- {name} ({eco}): {format_games(s.games)}, {record}, "
                    f"{s.win_rate:.1f}% win rate, {s.avg_cpl:.1f} CPL "
                    f"(only {format_games(s.games)} - too few to compare)\n"
                )
            else:
                sb.write(
                    f"- {name} ({eco}): {format_games(s.games)}, {record}, "
                    f"{s.win_rate:.1f}% win rate "
                    f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}, "
                    f"{s.avg_cpl:.1f} CPL\n"
                )

        # Ordered by losses, which is a different question from the one above
        # and one the corpus is regularly asked: "what do I lose with most
        # often". Without this the answer was assembled from whichever games
        # retrieval happened to return. A count needs no sample-size caveat —
        # three losses in three games really are three losses.
        by_losses = sorted(entries, key=lambda item: (-item[1].losses, item[0]))
        if by_losses and by_losses[0][1].losses > 0:
            sb.write("\nOpenings by losses (most first):\n")
            for eco, s in by_losses[:5]:
                if s.losses == 0:
                    break
                name = s.opening_name or eco
                sb.write(
                    f"- {name} ({eco}): {s.losses} of the player's losses, "
                    f"over {format_games(s.games)} ({s.win_rate:.1f}% win rate)\n"
                )

        # Highest and lowest win rate, over openings with enough games to mean
        # anything. Not "strongest" and "weakest" — see the time-control
        # equivalent for why that wording had to go.
        best: tuple[str, OpeningStats] | None = None
        worst: tuple[str, OpeningStats] | None = None
        for eco, s in entries:
            if s.games < MIN_GAMES_FOR_COMPARISON:
                continue
            if best is None or s.win_rate > best[1].win_rate:
                best = (eco, s)
            if worst is None or s.win_rate < worst[1].win_rate:
                worst = (eco, s)

        if best is not None and worst is not None and best[0] != worst[0]:
            best_name = best[1].opening_name or best[0]
            worst_name = worst[1].opening_name or worst[0]
            delta = best[1].win_rate - worst[1].win_rate
            floor = MIN_GAMES_FOR_COMPARISON
            sb.write(
                f"  → HIGHEST win rate (min {floor} games): {best_name} - "
                f"{best[1].win_rate:.1f}% over {format_games(best[1].games)}\n"
            )
            sb.write(
                f"  → LOWEST win rate (min {floor} games): {worst_name} - "
                f"{worst[1].win_rate:.1f}% over {format_games(worst[1].games)}\n"
            )
            sb.write(f"  → Spread: {delta:.1f} percentage points between the two\n")

    # ---------- retrieved games ----------

    def _write_game_context(
        self,
        sb: StringIO,
        games: list[SimilarGameResult],
        detail_limit: int,
        query_type: QueryType,
    ) -> None:
        """Label each retrieved game by position, never by opponent.

        **No opponent username reaches the prompt.** Selecting a hosted chat
        provider sends this text off the machine, and an opponent never agreed
        to that — the README promises as much. Readiness P0-8 stripped handles
        from the aggregates and from the stored Game Summary; this label was the
        third path and it survived, because it is attached here at assembly
        rather than stored. `Opponent rating` is already in the summary and is
        the discriminator a player actually needs.
        """
        num_details = min(len(games), detail_limit)

        if query_type in (QueryType.AGGREGATE, QueryType.COMPARATIVE):
            sb.write(f"EXAMPLE GAMES (showing {num_details} relevant games for context):\n")
        else:
            sb.write(f"RELEVANT GAMES (top {num_details} matches):\n")

        for i in range(num_details):
            # Scrubbed before flattening: a summary stored before P0-8 still has
            # the opponent's handle in its termination line, and re-ingesting is
            # the only thing that repairs the row itself.
            summary = strip_unnormalized_termination(games[i].summary_text).replace("\n", " ")
            sb.write(f"Game {i + 1}: {summary}\n")
        sb.write("\n")

    # ---------- instructions ----------

    def _write_instructions(self, sb: StringIO, query_type: QueryType) -> None:
        sb.write("INSTRUCTIONS:\n")
        sb.write("- Interpret all questions in the context of chess and the player's games\n")

        sb.write("\nFORMATTING:\n")
        sb.write("- Reply in GitHub-flavored markdown\n")
        sb.write("- Use ## for section headings, - for bullets, and **bold** for emphasis\n")
        sb.write(
            "- Put chess moves and openings in `backticks`, and multi-move lines in "
            "fenced code blocks\n"
        )
        sb.write(
            "- Use a markdown table when comparing three or more things across the same fields\n"
        )
        sb.write("- Do not use HTML, images, or heading levels above ###\n")

        if query_type is QueryType.AGGREGATE:
            sb.write("- This is a STATISTICS question - use the PLAYER OVERVIEW data primarily\n")
            sb.write("- Provide specific numbers and percentages from the stats\n")
            sb.write("- Example games are for illustration only\n")
        elif query_type is QueryType.COMPARATIVE:
            sb.write(
                "- This is a COMPARISON question - compare the relevant dimensions "
                "from PLAYER OVERVIEW\n"
            )
            sb.write("- Clearly state which option is better and by how much\n")
            sb.write("- Use specific numbers to support comparisons\n")
        elif query_type is QueryType.SPECIFIC_GAMES:
            sb.write("- Use RELEVANT GAMES for specific examples and patterns\n")
            sb.write("- Reference PLAYER OVERVIEW for context on how typical these games are\n")
            sb.write("- When citing specific games, identify them by label, e.g. Game 3\n")
            sb.write("- Quote specific details from game summaries when relevant\n")
        elif query_type is QueryType.RECOMMENDATION:
            sb.write("- Analyze PLAYER OVERVIEW to identify weaknesses and areas for improvement\n")
            sb.write("- Use RELEVANT GAMES as concrete examples of the issues\n")
            sb.write("- When citing specific games, identify them by label, e.g. Game 3\n")
            sb.write("- Provide specific, actionable recommendations\n")
            sb.write("- Prioritize the most impactful areas for improvement\n")
        elif query_type is QueryType.TREND:
            sb.write(
                "- This is a TREND question - focus on comparing RECENT PERFORMANCE to "
                "ALL-TIME stats\n"
            )
            sb.write("- Highlight specific improvements or regressions with numbers\n")
            sb.write("- If recent data is limited, say so and explain what more data would show\n")
            sb.write("- Use RELEVANT GAMES to illustrate specific changes in play\n")
            sb.write("- When citing specific games, identify them by label, e.g. Game 3\n")

        sb.write("- Use proper chess notation and terminology\n")
        # The corpus stores every move, and none of them reach this prompt. Asked
        # about an opening, a model with only the opening's *name* will supply the
        # move order from memory and present it as the player's own — observed on
        # both a 3B and a 20B local model. Naming the gap is the cheap half of the
        # fix; feeding the moves table in is the other half and is not done yet.
        sb.write(
            "- Do not invent moves, move orders, ECO codes, opponent names, or dates. "
            "No move list is provided here, so do not present one as the player's\n"
        )
        sb.write(
            "- Small samples are marked as too few to compare - do not draw conclusions from them\n"
        )
        sb.write("- If insufficient data exists for a question, say so clearly\n")

    # ---------- trend and opening detail ----------

    def _write_trend_stats(self, sb: StringIO, stats: PlayerStats) -> None:
        sb.write("RECENT PERFORMANCE (trend analysis):\n")

        all_time_win_rate = 0.0
        if stats.total_games > 0:
            all_time_win_rate = stats.wins / stats.total_games * 100
        sb.write(
            f"All-time: {stats.total_games} games, {all_time_win_rate:.1f}% win rate, "
            f"{stats.avg_cpl:.1f} avg CPL\n"
        )

        last_30 = stats.last_30_days
        if last_30 is not None and last_30.games > 0:
            sb.write(
                f"Last 30 days: {last_30.games} games, {last_30.win_rate:.1f}% win rate, "
                f"{last_30.avg_cpl:.1f} avg CPL\n"
            )

            win_rate_delta = last_30.win_rate - all_time_win_rate
            cpl_delta = last_30.avg_cpl - stats.avg_cpl

            if win_rate_delta > 0:
                sb.write(f"  → Win rate UP {win_rate_delta:.1f}% vs all-time\n")
            elif win_rate_delta < 0:
                sb.write(f"  → Win rate DOWN {-win_rate_delta:.1f}% vs all-time\n")

            if cpl_delta < 0:
                sb.write(f"  → CPL improved by {-cpl_delta:.1f} (lower is better)\n")
            elif cpl_delta > 0:
                sb.write(f"  → CPL worse by {cpl_delta:.1f} (lower is better)\n")
        else:
            sb.write("Last 30 days: No games with recorded dates in this period\n")

        last_90 = stats.last_90_days
        if last_90 is not None and last_90.games > 0:
            sb.write(
                f"Last 90 days: {last_90.games} games, {last_90.win_rate:.1f}% win rate, "
                f"{last_90.avg_cpl:.1f} avg CPL\n"
            )

        sb.write("\n")

    def _write_mentioned_opening_stats(
        self, sb: StringIO, stats: PlayerStats, mentioned: list[str]
    ) -> None:
        if not stats.stats_by_opening:
            return

        matches: list[tuple[str, OpeningStats]] = []
        for opening in mentioned:
            opening_lower = opening.lower()
            for eco in sorted(stats.stats_by_opening):
                o = stats.stats_by_opening[eco]
                if eco.lower() == opening_lower or opening_lower in o.opening_name.lower():
                    matches.append((eco, o))

        sb.write("OPENING-SPECIFIC STATS (for openings mentioned in your question):\n")
        if not matches:
            sb.write(f"No analyzed games in: {', '.join(mentioned)}\n")
        for eco, matched_stats in matches:
            name = matched_stats.opening_name or eco
            sb.write(f"\n{name} ({eco}):\n")
            sb.write(f"  - Games: {matched_stats.games}\n")
            sb.write(
                f"  - Record: {matched_stats.wins} wins, {matched_stats.losses} losses, "
                f"{matched_stats.draws} draws\n"
            )
            sb.write(f"  - Win rate: {matched_stats.win_rate:.1f}%\n")
            sb.write(f"  - Average CPL: {matched_stats.avg_cpl:.1f}\n")

        sb.write("\n")
