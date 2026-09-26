"""Query classification: what kind of question is this, and what does it mention.

Keyword-based and order-dependent. The order of the checks in `classify_query`
is the precedence: a question that is both a recommendation and a comparison is
a recommendation, because that is the more specific intent.
"""

from __future__ import annotations

import re
from enum import Enum

from zeitnot.openings import OPENINGS


class QueryType(Enum):
    """What the question is asking for, which decides what context is assembled."""

    # Overall statistics — "What's my average CPL?", "How many games?"
    AGGREGATE = "aggregate"
    # Comparing dimensions — "Am I better with white or black?"
    COMPARATIVE = "comparative"
    # Particular games or examples — "Show me games where I threw"
    SPECIFIC_GAMES = "specific_games"
    # Advice or next steps — "What should I study?"
    RECOMMENDATION = "recommendation"
    # Change over time — "Have I improved?"
    TREND = "trend"

    def __str__(self) -> str:
        return self.value


_TREND_KEYWORDS = (
    "improved",
    "improving",
    "getting better",
    "getting worse",
    "progress",
    "trend",
    "over time",
    "recently",
    "lately",
    "last month",
    "last week",
    "past few",
    "compared to before",
    "used to",
    "changed",
    "changing",
)

_RECOMMENDATION_KEYWORDS = (
    "should i study",
    "should i focus",
    "should i learn",
    "should i practice",
    "how can i improve",
    "how do i improve",
    "how to improve",
    "what to study",
    "what to practice",
    "what to focus",
    "recommend",
    "suggestion",
    "advice",
    "tips for",
    "help me get better",
    "what's my biggest weakness",
    "what are my weaknesses",
    "where should i",
    "what should i work on",
)

_COMPARATIVE_KEYWORDS = (
    "better with white or black",
    "better as white or black",
    "white or black",
    "better at bullet or blitz",
    "best time control",
    "worst time control",
    "best opening",
    "worst opening",
    "weakest opening",
    "strongest opening",
    "compare",
    "versus",
    " vs ",
    "difference between",
    "which is better",
    "which is worse",
    "do i perform better",
    "am i better",
    "am i worse",
    "higher rated or lower rated",
    "against higher",
    "against lower",
)

_AGGREGATE_KEYWORDS = (
    "average",
    "total games",
    "how many games",
    "win rate",
    "winning percentage",
    "win percentage",
    "loss rate",
    "draw rate",
    "how often do i",
    "what percentage",
    "what's my record",
    "what is my record",
    "overall",
    "statistics",
    "stats",
    "centipawn loss",
    "cpl",
    "accuracy",
    "how many wins",
    "how many losses",
    "how many draws",
    "most common",
    "most played",
    "how frequently",
)


def classify_query(question: str) -> QueryType:
    """Determine the type of question being asked.

    Order is precedence, most specific first. Anything unmatched is a question
    about particular games, which is the branch that leans hardest on retrieval.
    """
    q = question.lower()

    if _contains_any(q, _RECOMMENDATION_KEYWORDS):
        return QueryType.RECOMMENDATION
    if _contains_any(q, _TREND_KEYWORDS):
        return QueryType.TREND
    if _contains_any(q, _COMPARATIVE_KEYWORDS):
        return QueryType.COMPARATIVE
    if _contains_any(q, _AGGREGATE_KEYWORDS):
        return QueryType.AGGREGATE
    return QueryType.SPECIFIC_GAMES


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


# Enough to tell that a question is *about* openings, whether or not it names
# one. "opening" also matches "openings", so only the distinct stems are listed.
_OPENING_TOPIC_KEYWORDS = (
    "opening",
    "repertoire",
    "defense",
    "defence",
    "gambit",
    "variation",
)


def mentions_openings(question: str) -> bool:
    """Whether the question is about openings at all.

    Deliberately separate from `classify_query`. The type decides which context
    the router assembles; this decides whether one extra block belongs in it.
    Folding it into classification would reroute every question containing the
    word "defense" and change the prompt wholesale — see
    `QueryRouter._write_player_stats` for what it is used for.
    """
    if extract_mentioned_openings(question):
        return True
    return _contains_any(question.lower(), _OPENING_TOPIC_KEYWORDS)


_ECO_PATTERN = re.compile(r"\b[A-E]\d{2}\b")


def extract_mentioned_openings(question: str) -> list[str]:
    """Find any chess openings named in the query, by name or by ECO code.

    The openings come from `zeitnot.openings.OPENINGS`, which the parser reads
    too — this used to be a list of its own, and the two drifted until neither
    knew the Danish Gambit (#41).

    One opening reports at most once, as the **shortest** of its spellings the
    question contains, because that is the one that matches the most stored
    opening names: `french` finds `French Defense Advance Variation`, while
    `french defense` would miss anything Chess.com happens to call something
    else. What comes back feeds `QueryRouter._write_mentioned_opening_stats`,
    which looks each one up in the player's own openings, so every extra entry
    is another stats block in the prompt.
    """
    q = question.lower()
    matched = [(o, [a for a in o.aliases if a in q]) for o in OPENINGS]
    matched = [(o, present) for o, present in matched if present]

    found: list[str] = []
    for opening, present in matched:
        # A question about the Najdorf says "sicilian" only inside "sicilian
        # najdorf". Reporting the Sicilian as well would put every Sicilian
        # game's stats next to it, so an opening drops out when everything the
        # question spelled for it was part of a longer opening's name.
        longer = [a for other, others in matched if other is not opening for a in others]
        if all(any(a != b and a in b for b in longer) for a in present):
            continue
        found.append(min(present, key=len))

    found.extend(_ECO_PATTERN.findall(question.upper()))
    return found
