"""Extracts structured filters from natural-language queries.

Pattern matching identifies filterable criteria while preserving the semantic
remainder for embedding search.

Every keyword table is iterated in **sorted key order**. The loops take the
first match and stop, so ranging a dict in insertion order would make the result
depend on how the table happens to be written; sorting makes it depend on the
table's contents instead. The Go tree ranged maps here, which Go randomizes —
that was a live defect, fixed there before the goldens were captured, because a
different filter selects different games and therefore changes the assembled
prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from zeitnot.search.filters import GameFilters


@dataclass(frozen=True, slots=True)
class OpeningPattern:
    eco_prefix: str
    opening_name: str


@dataclass(frozen=True, slots=True)
class TimePattern:
    pattern: re.Pattern[str]
    duration: timedelta


@dataclass(slots=True)
class ParseResult:
    filters: GameFilters
    semantic_query: str
    extracted_filters: list[str] = field(default_factory=list[str])


OPENING_PATTERNS: dict[str, OpeningPattern] = {
    # Sicilian variations (B20-B99)
    "sicilian": OpeningPattern("B", "Sicilian"),
    "sicilian najdorf": OpeningPattern("B9", "Sicilian"),
    "najdorf": OpeningPattern("B9", "Najdorf"),
    "dragon": OpeningPattern("B7", "Dragon"),
    "sicilian dragon": OpeningPattern("B7", "Dragon"),
    # King's Indian (E60-E99)
    "king's indian": OpeningPattern("E", "King's Indian"),
    "kings indian": OpeningPattern("E", "King's Indian"),
    "kid": OpeningPattern("E", "King's Indian"),
    # Queen's Gambit (D06-D69)
    "queen's gambit": OpeningPattern("D", "Queen's Gambit"),
    "queens gambit": OpeningPattern("D", "Queen's Gambit"),
    "qgd": OpeningPattern("D", "Queen's Gambit"),
    "qga": OpeningPattern("D", "Queen's Gambit Accepted"),
    # Ruy Lopez (C60-C99)
    "ruy lopez": OpeningPattern("C6", "Ruy Lopez"),
    "spanish": OpeningPattern("C6", "Ruy Lopez"),
    "spanish game": OpeningPattern("C6", "Ruy Lopez"),
    # French Defense (C00-C19)
    "french": OpeningPattern("C0", "French"),
    "french defense": OpeningPattern("C0", "French"),
    "french defence": OpeningPattern("C0", "French"),
    # Caro-Kann (B10-B19)
    "caro-kann": OpeningPattern("B1", "Caro-Kann"),
    "caro kann": OpeningPattern("B1", "Caro-Kann"),
    # Italian Game (C50-C59)
    "italian": OpeningPattern("C5", "Italian"),
    "italian game": OpeningPattern("C5", "Italian"),
    "giuoco piano": OpeningPattern("C5", "Italian"),
    # English Opening (A10-A39)
    "english": OpeningPattern("A1", "English"),
    "english opening": OpeningPattern("A1", "English"),
    # London System (D00)
    "london": OpeningPattern("D00", "London"),
    "london system": OpeningPattern("D00", "London"),
    # Scandinavian (B01)
    "scandinavian": OpeningPattern("B01", "Scandinavian"),
    # Pirc Defense (B07-B09)
    "pirc": OpeningPattern("B0", "Pirc"),
    "pirc defense": OpeningPattern("B0", "Pirc"),
    # Dutch Defense (A80-A99)
    "dutch": OpeningPattern("A8", "Dutch"),
    "dutch defense": OpeningPattern("A8", "Dutch"),
    # Nimzo-Indian (E20-E59)
    "nimzo-indian": OpeningPattern("E", "Nimzo-Indian"),
    "nimzo indian": OpeningPattern("E", "Nimzo-Indian"),
    "nimzo": OpeningPattern("E", "Nimzo-Indian"),
    # Grunfeld (D70-D99)
    "grunfeld": OpeningPattern("D7", "Grunfeld"),
    "grünfeld": OpeningPattern("D7", "Grunfeld"),
}

RESULT_KEYWORDS: dict[str, str] = {
    "win": "win",
    "wins": "win",
    "won": "win",
    "winning": "win",
    "victory": "win",
    "victories": "win",
    "loss": "loss",
    "losses": "loss",
    "lost": "loss",
    "losing": "loss",
    "defeat": "loss",
    "defeats": "loss",
    "draw": "draw",
    "draws": "draw",
    "drew": "draw",
    "tie": "draw",
    "ties": "draw",
}

COLOR_KEYWORDS: dict[str, str] = {
    "as white": "white",
    "with white": "white",
    "playing white": "white",
    "white pieces": "white",
    "as black": "black",
    "with black": "black",
    "playing black": "black",
    "black pieces": "black",
}

TIME_CLASS_KEYWORDS: dict[str, str] = {
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "classical",
    "daily": "daily",
}

PHASE_KEYWORDS: dict[str, str] = {
    "opening": "opening",
    "openings": "opening",
    "middlegame": "middlegame",
    "middle game": "middlegame",
    "midgame": "middlegame",
    "endgame": "endgame",
    "end game": "endgame",
    "endings": "endgame",
}

# Ordered, not sorted: this is a list in the Go tree too, and the order encodes
# precedence — "this week" is checked before "last week", and "recent" last.
TIME_PATTERNS: list[TimePattern] = [
    TimePattern(re.compile(r"(?i)\b(today|this day)\b"), timedelta(hours=24)),
    TimePattern(re.compile(r"(?i)\b(yesterday)\b"), timedelta(hours=48)),
    TimePattern(re.compile(r"(?i)\b(this week|past week|last 7 days)\b"), timedelta(days=7)),
    TimePattern(re.compile(r"(?i)\b(last week)\b"), timedelta(days=14)),
    TimePattern(re.compile(r"(?i)\b(this month|past month|last 30 days)\b"), timedelta(days=30)),
    TimePattern(re.compile(r"(?i)\b(last month)\b"), timedelta(days=60)),
    TimePattern(re.compile(r"(?i)\b(this year|past year|last 365 days)\b"), timedelta(days=365)),
    TimePattern(re.compile(r"(?i)\b(recent|recently|lately)\b"), timedelta(days=14)),
]

_BLUNDER_PATTERNS: list[tuple[re.Pattern[str], int | None, int | None]] = [
    # (pattern, min_blunders, max_blunders). Order encodes precedence: the
    # negations are checked before the bare "blunder", which would otherwise
    # match inside all of them.
    (re.compile(r"(?i)\bno blunders?\b"), None, 0),
    (re.compile(r"(?i)\bwithout blunders?\b"), None, 0),
    (re.compile(r"(?i)\bdidn'?t blunder\b"), None, 0),
    (re.compile(r"(?i)\b(blunder|blunders|blundered)\b"), 1, None),
]

_WHITESPACE = re.compile(r"\s+")


class QueryParser:
    def parse(self, query: str, username: str) -> ParseResult:
        filters = GameFilters(username=username)
        extracted: list[str] = []
        remaining = query
        lower = query.lower()

        # Opening patterns: collect every match, then take the longest keyword.
        # Ties go to the alphabetically first, because the candidates are built
        # in sorted order and the comparison is strictly greater-than.
        matches = [kw for kw in sorted(OPENING_PATTERNS) if kw in lower]
        if matches:
            longest = matches[0]
            for keyword in matches:
                if len(keyword) > len(longest):
                    longest = keyword
            pattern = OPENING_PATTERNS[longest]
            filters.eco_prefix = pattern.eco_prefix
            filters.opening_name = pattern.opening_name
            extracted.append("opening: " + pattern.opening_name)
            remaining = _remove_keyword(remaining, longest)

        for keyword in sorted(COLOR_KEYWORDS):
            if keyword in lower:
                filters.user_color = COLOR_KEYWORDS[keyword]
                extracted.append("color: " + COLOR_KEYWORDS[keyword])
                remaining = _remove_keyword(remaining, keyword)
                break

        for table, attribute, label in (
            (RESULT_KEYWORDS, "result", "result: "),
            (TIME_CLASS_KEYWORDS, "time_class", "time control: "),
            (PHASE_KEYWORDS, "weak_phase", "phase: "),
        ):
            for keyword in sorted(table):
                pattern_re = re.compile(r"(?i)\b" + re.escape(keyword) + r"\b")
                if pattern_re.search(lower):
                    setattr(filters, attribute, table[keyword])
                    extracted.append(label + table[keyword])
                    remaining = pattern_re.sub("", remaining)
                    break

        now = datetime.now(UTC)
        for time_pattern in TIME_PATTERNS:
            if time_pattern.pattern.search(lower):
                filters.date_from = now - time_pattern.duration
                # The regex source, not the matched text — this is what the Go
                # version recorded, and the goldens carry it.
                extracted.append("date: " + time_pattern.pattern.pattern)
                remaining = time_pattern.pattern.sub("", remaining)
                break

        for blunder_pattern, minimum, maximum in _BLUNDER_PATTERNS:
            # Matched against the original query, not the lowercased copy. The
            # patterns are already case-insensitive, so this is equivalent —
            # and it is what the Go version did.
            if blunder_pattern.search(query):
                if minimum is not None:
                    filters.min_blunders = minimum
                    extracted.append(f"min blunders: {minimum}")
                if maximum is not None:
                    filters.max_blunders = maximum
                    extracted.append(f"max blunders: {maximum}")
                remaining = blunder_pattern.sub("", remaining)
                break

        return ParseResult(
            filters=filters,
            semantic_query=_clean_query(remaining),
            extracted_filters=extracted,
        )


def _remove_keyword(query: str, keyword: str) -> str:
    """Remove a keyword from the query, case-insensitively."""
    return re.sub(re.escape(keyword), "", query, flags=re.IGNORECASE)


def _clean_query(query: str) -> str:
    """Collapse whitespace and trim trailing punctuation."""
    return _WHITESPACE.sub(" ", query).strip(" .,!?")
