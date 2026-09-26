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

from zeitnot.openings import OPENINGS
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


# Derived, not written out. The openings live in `zeitnot.openings`, which the
# classifier reads too; a second hand-maintained table here is what let the two
# disagree on 37 spellings while neither carried the Danish Gambit (#41).
OPENING_PATTERNS: dict[str, OpeningPattern] = {
    alias: OpeningPattern(opening.eco_prefix, opening.name)
    for opening in OPENINGS
    for alias in opening.aliases
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

# A result word names a filter only when it names the *outcome of a game*. These
# are the phrases where it does not: it names a metric the question is asking
# about ("centipawn loss", "win rate"), or it describes a position inside a game
# ("threw a winning position"). The characters they cover are masked out before
# the result table is scanned, so such a word neither sets a filter nor gets
# stripped from the text handed to the embedder.
#
# This is the boundary the parser draws, and it is deliberately a phrase list
# rather than a rule: "loss" is an outcome, "centipawn loss" is a number.
_METRIC_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(?:centipawn|cp)\s+loss(?:es)?\b"),
    re.compile(
        r"(?i)\b(?:win|loss|draw)\s*[-/]?\s*(?:rate|rates|percent|percentage|percentages|ratio)\b"
    ),
    re.compile(
        r"(?i)\b(?:winning|losing|won|lost|drawn)\s+"
        r"(?:position|positions|endgame|endgames|chances|advantage)\b"
    ),
]

# Colours are a comparison, not a filter, when the question names both of them:
# "Am I better with white or black?" is about the difference between the two.
_WHITE_MENTION = re.compile(r"(?i)\bwhite\b")
_BLACK_MENTION = re.compile(r"(?i)\bblack\b")

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

        names_both_colors = bool(_WHITE_MENTION.search(lower)) and bool(
            _BLACK_MENTION.search(lower)
        )
        if not names_both_colors:
            for keyword in sorted(COLOR_KEYWORDS):
                if keyword in lower:
                    filters.user_color = COLOR_KEYWORDS[keyword]
                    extracted.append("color: " + COLOR_KEYWORDS[keyword])
                    remaining = _remove_keyword(remaining, keyword)
                    break

        # The result table is scanned against a copy with the metric phrases
        # blanked out, so "centipawn loss" and "win rate" are invisible to it.
        masked = _mask_metric_phrases(lower)
        for keyword in sorted(RESULT_KEYWORDS):
            pattern_re = _word_pattern(keyword)
            if pattern_re.search(masked):
                filters.result = RESULT_KEYWORDS[keyword]
                extracted.append("result: " + RESULT_KEYWORDS[keyword])
                remaining = _strip_unprotected(remaining, pattern_re)
                break

        # `label is None` means "set the field, announce nothing". The phase
        # filter is parsed and merged but `build_where` never applies it, so
        # announcing it told the model the games in front of it were endgame
        # games when nothing had selected for that — the small half of #18.
        tables: tuple[tuple[dict[str, str], str, str | None], ...] = (
            (TIME_CLASS_KEYWORDS, "time_class", "time control: "),
            (PHASE_KEYWORDS, "weak_phase", None),
        )
        for table, attribute, label in tables:
            for keyword in sorted(table):
                pattern_re = _word_pattern(keyword)
                if pattern_re.search(lower):
                    setattr(filters, attribute, table[keyword])
                    if label is not None:
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


def _word_pattern(keyword: str) -> re.Pattern[str]:
    """A case-insensitive, word-bounded match for a literal keyword."""
    return re.compile(r"(?i)\b" + re.escape(keyword) + r"\b")


def _remove_keyword(query: str, keyword: str) -> str:
    """Remove a keyword from the query, case-insensitively.

    Word-bounded, like every other removal here. It used to be a bare substring
    replacement, which is the same code doing a different thing: `"whitespace in
    my games"` minus `white` came back as `"space in my games"`.
    """
    return _word_pattern(keyword).sub("", query)


def _mask_metric_phrases(text: str) -> str:
    """Blank out every metric phrase, preserving length and therefore offsets.

    The filler is a non-word character, so no keyword can match across or inside
    a masked span.
    """
    for phrase in _METRIC_PHRASES:
        text = phrase.sub(lambda m: "#" * len(m.group(0)), text)
    return text


def _strip_unprotected(text: str, pattern: re.Pattern[str]) -> str:
    """Remove matches of `pattern`, leaving those inside a metric phrase alone.

    Masking is length-preserving, so a match found in the masked copy carries
    offsets that address the real text.
    """
    masked = _mask_metric_phrases(text)
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(masked):
        pieces.append(text[cursor : match.start()])
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _clean_query(query: str) -> str:
    """Collapse whitespace and trim trailing punctuation."""
    return _WHITESPACE.sub(" ", query).strip(" .,!?")
