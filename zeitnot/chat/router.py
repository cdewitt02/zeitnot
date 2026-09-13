"""Assembles the prompt: classify the question, gather context, format it.

**This module's output is the parity target for the whole rewrite.** Every input
the chat provider receives is the assembled prompt, so a matching prompt leaves
nothing downstream for the port to have gotten wrong. It is checked byte for
byte against the Phase 0 goldens for the twelve frozen questions.

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

from zeitnot.chat.classifier import QueryType, classify_query, extract_mentioned_openings
from zeitnot.db import DB
from zeitnot.db.records import SimilarGameResult
from zeitnot.models import ColorStats, OpeningStats, PlayerStats, RatingBandStats, TimeClassStats
from zeitnot.search.hybrid import HybridSearcher, SearchQuery


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


@dataclass(slots=True)
class QueryContext:
    """Everything gathered to answer one question."""

    query_type: QueryType
    player_stats: PlayerStats | None = None
    games: list[SimilarGameResult] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    mentioned_openings: list[str] = field(default_factory=list)


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
            self._write_player_stats(sb, stats, qctx.query_type)

            if qctx.query_type is QueryType.TREND:
                self._write_trend_stats(sb, stats)

            if qctx.mentioned_openings:
                self._write_mentioned_opening_stats(sb, stats, qctx.mentioned_openings)

        if qctx.games:
            self._write_game_context(sb, qctx.games, detail_limit, qctx.query_type)

        self._write_instructions(sb, qctx.query_type)

        return sb.getvalue()

    # ---------- player overview ----------

    def _write_player_stats(self, sb: StringIO, stats: PlayerStats, query_type: QueryType) -> None:
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
                    f"- As {color}: {s.games} games, {s.win_rate:.1f}% win rate "
                    f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}, "
                    f"{s.avg_cpl:.1f} avg CPL "
                    f"{format_cpl_comparison(s.avg_cpl, stats.avg_cpl)}\n"
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
                    f"- {tc}: {s.games} games, {s.win_rate:.1f}% win rate "
                    f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}, "
                    f"{s.avg_cpl:.1f} avg CPL "
                    f"{format_cpl_comparison(s.avg_cpl, stats.avg_cpl)}\n"
                )
            self._write_time_control_insights(sb, stats.stats_by_time_class)

        # Comparative and recommendation questions get the extra dimensions.
        # The others do not, to keep the prompt from burying the answer.
        if query_type in (QueryType.COMPARATIVE, QueryType.RECOMMENDATION):
            if stats.stats_by_rating_band:
                sb.write("\nPerformance by opponent rating:\n")
                for band in sorted(stats.stats_by_rating_band):
                    s = stats.stats_by_rating_band[band]
                    sb.write(
                        f"- vs {band}: {s.games} games, {s.win_rate:.1f}% win rate "
                        f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}\n"
                    )
                self._write_rating_band_insights(sb, stats.stats_by_rating_band)

            if stats.stats_by_opening:
                self._write_opening_stats(sb, stats.stats_by_opening, overall_win_rate)

        # Useful for questions about flagging, checkmates, and so on.
        if stats.stats_by_termination:
            sb.write("\nGame endings:\n")
            for term in sorted(stats.stats_by_termination):
                count = stats.stats_by_termination[term]
                pct = count / stats.total_games * 100
                sb.write(f"- {term}: {count} ({pct:.1f}%)\n")

        sb.write("\n")

    def _write_color_comparison(self, sb: StringIO, white: ColorStats, black: ColorStats) -> None:
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
        if len(time_classes) < 2:
            return

        best_tc = worst_tc = ""
        best_rate, worst_rate = -1.0, 101.0
        min_games = 3

        for tc in sorted(time_classes):
            s = time_classes[tc]
            if s.games < min_games:
                continue
            if s.win_rate > best_rate:
                best_rate, best_tc = s.win_rate, tc
            if s.win_rate < worst_rate:
                worst_rate, worst_tc = s.win_rate, tc

        if best_tc and worst_tc and best_tc != worst_tc:
            sb.write(f"  → STRONGEST time control: {best_tc} ({best_rate:.1f}% win rate)\n")
            sb.write(f"  → WEAKEST time control: {worst_tc} ({worst_rate:.1f}% win rate)\n")
            sb.write(f"  → Difference: {best_rate - worst_rate:.1f} percentage points\n")

    def _write_rating_band_insights(self, sb: StringIO, bands: dict[str, RatingBandStats]) -> None:
        if len(bands) < 2:
            return

        best_band = worst_band = ""
        best_rate, worst_rate = -1.0, 101.0
        min_games = 3

        for band in sorted(bands):
            s = bands[band]
            if s.games < min_games:
                continue
            if s.win_rate > best_rate:
                best_rate, best_band = s.win_rate, band
            if s.win_rate < worst_rate:
                worst_rate, worst_band = s.win_rate, band

        if best_band and worst_band:
            sb.write(
                f"  → BEST performance vs: {best_band} rated opponents "
                f"({best_rate:.1f}% win rate)\n"
            )
            sb.write(
                f"  → WORST performance vs: {worst_band} rated opponents "
                f"({worst_rate:.1f}% win rate)\n"
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
            sb.write(
                f"- {name} ({eco}): {s.games} games, {s.win_rate:.1f}% win rate "
                f"{format_win_rate_comparison(s.win_rate, overall_win_rate)}, "
                f"{s.avg_cpl:.1f} CPL\n"
            )

        # Best and worst, over openings with enough games to mean anything.
        best: tuple[str, OpeningStats] | None = None
        worst: tuple[str, OpeningStats] | None = None
        for eco, s in entries:
            if s.games < 3:
                continue
            if best is None or s.win_rate > best[1].win_rate:
                best = (eco, s)
            if worst is None or s.win_rate < worst[1].win_rate:
                worst = (eco, s)

        if best is not None and worst is not None:
            best_name = best[1].opening_name or best[0]
            worst_name = worst[1].opening_name or worst[0]
            delta = best[1].win_rate - worst[1].win_rate
            sb.write(
                f"\n  → STRONGEST opening (min 3 games): {best_name} - "
                f"{best[1].win_rate:.1f}% win rate\n"
            )
            sb.write(
                f"  → WEAKEST opening (min 3 games): {worst_name} - "
                f"{worst[1].win_rate:.1f}% win rate\n"
            )
            sb.write(f"  → Spread: {delta:.1f} percentage points between best and worst\n")

    # ---------- retrieved games ----------

    def _write_game_context(
        self,
        sb: StringIO,
        games: list[SimilarGameResult],
        detail_limit: int,
        query_type: QueryType,
    ) -> None:
        num_details = min(len(games), detail_limit)

        if query_type in (QueryType.AGGREGATE, QueryType.COMPARATIVE):
            sb.write(f"EXAMPLE GAMES (showing {num_details} relevant games for context):\n")
        else:
            sb.write(f"RELEVANT GAMES (top {num_details} matches):\n")

        for i in range(num_details):
            game = games[i]
            record = game.game
            opponent = ""
            if record is not None:
                if record.white_username == self._username:
                    opponent = record.black_username
                else:
                    opponent = record.white_username

            # The summary is flattened to one line so each game is one entry.
            summary = game.summary_text.replace("\n", " ")
            sb.write(f"{i + 1}. [vs {opponent}] {summary}\n")
        sb.write("\n")

    # ---------- instructions ----------

    def _write_instructions(self, sb: StringIO, query_type: QueryType) -> None:
        sb.write("INSTRUCTIONS:\n")
        sb.write("- Interpret all questions in the context of chess and the player's games\n")

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
            sb.write(
                "- When citing specific games, use the actual opponent username shown in "
                "brackets [vs USERNAME] to identify the game\n"
            )
            sb.write("- Quote specific details from game summaries when relevant\n")
        elif query_type is QueryType.RECOMMENDATION:
            sb.write("- Analyze PLAYER OVERVIEW to identify weaknesses and areas for improvement\n")
            sb.write("- Use RELEVANT GAMES as concrete examples of the issues\n")
            sb.write(
                "- When citing specific games, use the actual opponent username shown in "
                "brackets [vs USERNAME] to identify the game\n"
            )
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
            sb.write(
                "- When citing specific games, use the actual opponent username shown in "
                "brackets [vs USERNAME] to identify the game\n"
            )

        sb.write("- Use proper chess notation and terminology\n")
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

        sb.write("OPENING-SPECIFIC STATS (for openings mentioned in your question):\n")

        for opening in mentioned:
            opening_lower = opening.lower()
            for eco in sorted(stats.stats_by_opening):
                o = stats.stats_by_opening[eco]
                if eco.lower() == opening_lower or opening_lower in o.opening_name.lower():
                    name = o.opening_name or eco
                    sb.write(f"\n{name} ({eco}):\n")
                    sb.write(f"  - Games: {o.games}\n")
                    sb.write(f"  - Record: {o.wins} wins, {o.losses} losses, {o.draws} draws\n")
                    sb.write(f"  - Win rate: {o.win_rate:.1f}%\n")
                    sb.write(f"  - Average CPL: {o.avg_cpl:.1f}\n")

        sb.write("\n")
