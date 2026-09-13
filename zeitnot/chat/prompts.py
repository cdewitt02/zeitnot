"""The pre-router prompt builder.

**This is not on the live path.** `QueryRouter.build_prompt` supersedes it and
is what `Service` calls; nothing invokes `build_system_prompt` today. It is
ported because the Go tree still carries it and dropping it would be a scope
change disguised as a cleanup — but it is dead code, and a reader looking for
what shapes the prompt wants `router.py`.

Its FORMATTING block is the one part worth keeping in view: it is the only
place the markdown subset the terminal renderer expects is written down, and the
router's instructions do not repeat it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from io import StringIO

from zeitnot.db.records import SimilarGameResult

_PATTERNS = (
    "Came back from losing position",
    "Steady advantage throughout",
    "Converted a close game",
    "Threw a winning position",
    "Was outplayed",
    "Lost a close game",
    "Missed winning opportunity",
    "Saved a draw from worse position",
    "Wild game ended in draw",
    "Even game throughout",
)


@dataclass(slots=True)
class GameStats:
    total_games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    as_white: int = 0
    as_black: int = 0
    openings: dict[str, int] = field(default_factory=dict)
    weakest_phases: dict[str, int] = field(default_factory=dict)
    patterns: dict[str, int] = field(default_factory=dict)


class PromptBuilder:
    def __init__(self, username: str) -> None:
        self._username = username

    def build_system_prompt(self, games: Sequence[SimilarGameResult], detail_limit: int) -> str:
        sb = StringIO()

        sb.write(f"You are a chess coach for {self._username}. ")
        sb.write(
            "Your role is to provide insightful analysis based on the player's game history.\n\n"
        )

        if not games:
            sb.write("Note: No relevant games were found in the database.\n\n")
        else:
            stats = aggregate_game_stats(games)

            sb.write(f"SUMMARY STATISTICS (based on {stats.total_games} relevant games):\n")
            sb.write(
                f"- Win/Loss/Draw Record: {stats.wins} wins, {stats.losses} losses, "
                f"{stats.draws} draws\n"
            )
            sb.write(
                f"- Color Distribution: {stats.as_white} games as white, "
                f"{stats.as_black} games as black\n"
            )

            # Sorted, unlike the Go original, which ranged a map here. The Go
            # version was non-deterministic; since this is dead code it could
            # not have been observed, but porting the randomness forward would
            # be porting a defect into a language that does not have it.
            if stats.openings:
                sb.write("\nOPENINGS PLAYED (opening name and frequency):\n")
                for opening in sorted(stats.openings):
                    sb.write(f"- {opening}: {stats.openings[opening]} games\n")

            if stats.weakest_phases:
                sb.write(
                    "\nWEAKEST GAME PHASES (phase where player made the most errors, "
                    "independent of game result):\n"
                )
                for phase in sorted(stats.weakest_phases):
                    sb.write(f"- {phase}: {stats.weakest_phases[phase]} games\n")

            if stats.patterns:
                sb.write("\nGAME PATTERNS (how games unfolded, independent of weakest phase):\n")
                for pattern in sorted(stats.patterns):
                    sb.write(f"- {pattern}: {stats.patterns[pattern]} games\n")

            num_details = min(len(games), detail_limit)
            sb.write(f"\nTOP {num_details} MOST RELEVANT GAMES (of {len(games)} analyzed):\n")
            for i in range(num_details):
                sb.write(f"{i + 1}. {games[i].summary_text.replace(chr(10), ' ')}\n")

        sb.write("\nINSTRUCTIONS:\n")
        sb.write("- Interpret all questions in the context of chess and the player's games\n")
        sb.write(
            "- If a question seems unclear, assume it's about chess improvement, openings, "
            "tactics, or game patterns\n"
        )
        sb.write(
            "- Use SUMMARY STATISTICS for overall trends and patterns "
            "(based on all analyzed games)\n"
        )
        sb.write("- Use TOP RELEVANT GAMES for specific examples and detailed analysis\n")
        sb.write(
            "- When discussing tendencies, cite the statistics; when giving examples, "
            "reference the detailed games\n"
        )
        sb.write(
            "- If insufficient game data exists for a question, provide general chess "
            "principles relevant to the topic\n"
        )
        sb.write(
            "- Identify patterns across multiple games rather than focusing on individual games\n"
        )
        sb.write("- Highlight both recurring weaknesses and consistent strengths\n")
        sb.write("- Give specific, actionable recommendations\n")
        sb.write("- Use proper chess notation and terminology\n")

        # Formatting is pinned because the answer is rendered as markdown in a
        # terminal. Left unsaid, models drift between prose, markdown, and
        # occasional HTML; naming the subset keeps the rendered output stable
        # and keeps it readable as raw text when styling is unavailable.
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

        return sb.getvalue()


def aggregate_game_stats(games: Sequence[SimilarGameResult]) -> GameStats:
    """Tally win/loss/draw, color, opening, phase, and pattern counts.

    Derived by *parsing the summary text* rather than by reading columns, which
    is why preserved defect 2 reaches here: a drawn game's summary begins with
    "lost", so `draws` is always 0 and `losses` overcounts by the number of
    draws. The player_stats table computes the same figures correctly in SQL.
    """
    stats = GameStats(total_games=len(games))

    for game in games:
        summary = game.summary_text

        if summary.startswith("won"):
            stats.wins += 1
        elif summary.startswith("lost"):
            stats.losses += 1
        elif summary.startswith("drew"):
            stats.draws += 1

        if "as white" in summary:
            stats.as_white += 1
        elif "as black" in summary:
            stats.as_black += 1

        for line in summary.split("\n"):
            if line.startswith("Played "):
                opening = line.removeprefix("Played ").removesuffix(".")
                stats.openings[opening] = stats.openings.get(opening, 0) + 1

            if "was weakest" in line:
                # "Opening was weakest." -> "Opening"
                phase = line.removesuffix(".").removesuffix(" was weakest")
                stats.weakest_phases[phase] = stats.weakest_phases.get(phase, 0) + 1

            for pattern in _PATTERNS:
                if pattern in line:
                    stats.patterns[pattern] = stats.patterns.get(pattern, 0) + 1

    return stats
