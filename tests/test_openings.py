"""The opening table's own invariants.

`zeitnot/openings.py` replaced two hand-maintained lists — one in the classifier
deciding which openings a question *names*, one in the parser deciding which
games it *retrieves*. They drifted apart on 37 spellings and neither carried the
Danish Gambit, so a question comparing it to the Caro-Kann reached the model as
Caro-Kann stats with nothing saying the other half was missing (#41).

These assertions are what keeps a table that big correct as entries are added.
Unmarked: no database, no corpus, no provider.
"""

from __future__ import annotations

from zeitnot.chat.classifier import extract_mentioned_openings
from zeitnot.openings import OPENINGS, display_name, normalize
from zeitnot.search.parser import OPENING_PATTERNS, QueryParser


def test_no_alias_belongs_to_two_openings() -> None:
    """One spelling, one opening. A duplicate would silently take whichever
    entry the dict comprehension built last."""
    owners: dict[str, str] = {}
    for opening in OPENINGS:
        for alias in opening.aliases:
            assert alias not in owners, f"{alias!r}: {owners.get(alias)} and {opening.name}"
            owners[alias] = opening.name


def test_every_alias_is_lowercase_and_stripped() -> None:
    """Aliases are matched against a lowercased question with a bare `in`, so
    anything else in the table can never match."""
    for opening in OPENINGS:
        for alias in opening.aliases:
            assert alias == alias.lower().strip(), repr(alias)


def test_every_name_survives_normalization_as_a_search_needle() -> None:
    """`name` is also the `LIKE %name%` needle against `games.eco_name`, which
    holds a Chess.com URL slug: hyphens spaced out, apostrophes gone. A name
    that changes under `normalize` has to go through it at every comparison."""
    for opening in OPENINGS:
        assert normalize(opening.name) == normalize(normalize(opening.name)), opening.name


def test_every_eco_prefix_is_a_plausible_eco_range() -> None:
    for opening in OPENINGS:
        prefix = opening.eco_prefix
        assert 1 <= len(prefix) <= 3, opening.name
        assert prefix[0] in "ABCDE", opening.name
        assert prefix[1:].isdigit() or not prefix[1:], opening.name


def test_the_classifier_and_the_parser_see_the_same_spellings() -> None:
    """The drift this table exists to prevent. Both consumers derive from
    `OPENINGS`, so a spelling one knows the other knows."""
    from_table = {alias for opening in OPENINGS for alias in opening.aliases}
    assert set(OPENING_PATTERNS) == from_table


def test_the_danish_gambit_is_in_the_table() -> None:
    """The reported bug, as a one-line assertion."""
    assert extract_mentioned_openings("how are my Danish Gambit games?") == ["danish"]
    assert display_name("danish") == "Danish Gambit"
    assert OPENING_PATTERNS["danish"].eco_prefix == "C21"


def test_a_question_reports_one_entry_per_opening_it_names() -> None:
    """Two spellings of one opening is one mention, and one mention is one stats
    block in the prompt."""
    assert extract_mentioned_openings("French Defense analysis") == ["french"]
    # Both spellings of one opening, one entry. Which spelling comes back is not
    # the point and the two are the same length; that there is one is the point.
    assert len(extract_mentioned_openings("the Caro-Kann and the caro kann")) == 1


def test_a_compound_name_reports_the_specific_opening_only() -> None:
    """ "sicilian najdorf" names the Najdorf. Reporting the Sicilian as well
    would put every Sicilian game's stats beside it."""
    assert extract_mentioned_openings("my sicilian najdorf games") == ["najdorf"]
    assert extract_mentioned_openings("the kings indian attack") == ["kings indian attack"]
    # Two openings genuinely named stay two openings.
    assert extract_mentioned_openings("my Sicilian, and the Najdorf in particular") == [
        "sicilian",
        "najdorf",
    ]


def test_a_compound_name_retrieves_the_specific_opening_only() -> None:
    """The same precedence on the retrieval side, where it selects games."""
    parsed = QueryParser().parse("my sicilian najdorf games", "player")
    assert parsed.filters.eco_prefix == "B9"
    assert parsed.filters.opening_name == "Najdorf"


def test_a_hyphenated_name_matches_what_chess_com_stored() -> None:
    """`eco_name` has no hyphens, so the standard spelling has to be folded
    before it is used as a LIKE needle — it matched nothing before."""
    parsed = QueryParser().parse("Caro-Kann games", "player")
    assert parsed.filters.opening_name == "Caro-Kann"
    clause, args = parsed.filters.build_where().clause, parsed.filters.build_where().args
    assert "LOWER(g.eco_name) LIKE %s" in clause
    assert "%caro kann%" in args
