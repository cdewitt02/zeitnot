"""The query parser and the classifier, against their committed tables.

**Unmarked on purpose.** These three tests lived in `test_parity_prompt.py`
under a module-level `pytestmark = [corpus, golden]` that neither of them
needed: they are pure functions over a fixed question set, and both tables are
committed. CI runs `pytest -m "not corpus"`, so the mark deselected them, and
`zeitnot/search/parser.py` and `zeitnot/chat/classifier.py` — 477 lines that
decide which games are retrieved and which context the router assembles — were
covered by nothing on any pull request. Splitting them out is the fix.

Coverage that exists but does not run is coverage that cannot fail, which is the
same shape as the defect that survived a byte-for-byte port: every check on the
phase buckets ran against a golden captured from the implementation that had the
bug.

**Both tables started as Go captures**: the questions were chosen by hand, but
the expected values were what the Go implementation returned, which is why this
module used to say that a failure here meant "the parser changed", not "the
parser broke". `parsing.json` was read entry by entry under #40/#41 — eight
entries are now hand-written expectations, the other 29 were confirmed as the
feature working as designed — so that caveat is retired and a failure reads the
ordinary way.

The three rules behind the rewritten entries are asserted at the bottom of this
file *without* reading the table, which is the part the golden could never do
for itself: it agreed with the code because both came from the same
implementation.
"""

from __future__ import annotations

from tests.conftest import load_golden
from zeitnot.chat.classifier import classify_query, extract_mentioned_openings
from zeitnot.search.parser import QueryParser


def test_query_classification_matches_the_golden() -> None:
    """Classification decides which context the router assembles, so a question
    that reroutes changes the prompt wholesale."""
    cases = load_golden("classification.json")
    assert cases

    for case in cases:
        question = case["question"]
        assert str(classify_query(question)) == case["query_type"], question
        assert extract_mentioned_openings(question) == case["mentioned_openings"], question


def test_query_parsing_matches_the_golden() -> None:
    """Parsing decides which games are retrieved, so a filter that differs
    changes the prompt without changing a single format string."""
    cases = load_golden("parsing.json")
    assert cases

    parser = QueryParser()
    for case in cases:
        question = case["question"]
        result = parser.parse(question, "testuser")
        f = result.filters

        assert result.semantic_query == case["semantic_query"], question
        assert result.extracted_filters == case["extracted_filters"], question
        assert f.result == case["result"], question
        assert f.user_color == case["user_color"], question
        assert f.time_class == case["time_class"], question
        assert f.weak_phase == case["weak_phase"], question
        assert f.eco_prefix == case["eco_prefix"], question
        assert f.opening_name == case["opening_name"], question
        assert f.min_blunders == case["min_blunders"], question
        assert f.max_blunders == case["max_blunders"], question
        assert f.min_mistakes == case["min_mistakes"], question
        assert f.min_rating == case["min_rating"], question
        assert f.max_rating == case["max_rating"], question
        # A flag, not a value: date_from is now() minus a duration, so a
        # captured timestamp would fail one second after capture.
        assert (f.date_from is not None) == case["date_from_set"], question
        assert (f.date_to is not None) == case["date_to_set"], question


def test_parsing_is_stable_across_repeated_calls() -> None:
    """The property the Go tree lacked.

    Its keyword loops ranged maps and took the first match, so a query matching
    two keywords in one table resolved differently between runs. That reached
    retrieval and therefore the prompt. Sorted iteration is what makes this
    assertion possible at all, and `docs/codebase-invariants.md` lists it under
    the properties a change that looks like a cleanup can break.
    """
    parser = QueryParser()
    ambiguous = [
        "my wins and losses",  # two result keywords
        "queens gambit and kings indian",  # two openings of equal length
        "as white or as black",  # two color keywords
        "my opening and endgame errors",  # two phase keywords
        "blitz and bullet games",  # two time classes
    ]
    for question in ambiguous:
        first = parser.parse(question, "u")
        for _ in range(5):
            again = parser.parse(question, "u")
            assert again.semantic_query == first.semantic_query, question
            assert again.extracted_filters == first.extracted_filters, question
            assert again.filters.result == first.filters.result, question
            assert again.filters.eco_prefix == first.filters.eco_prefix, question


def test_a_metric_name_does_not_apply_a_result_filter() -> None:
    """The defect in #40, asserted from the specification rather than the table.

    A result word is a filter only when it names the outcome of a game. Inside
    a metric name it names a number, and inside "winning position" it describes
    a position in a game that was very likely lost. Both used to set a filter
    *and* get cut out of the text handed to the embedder, so the question about
    average centipawn loss reached retrieval as "average centipawn", restricted
    to games the player lost.
    """
    parser = QueryParser()
    for question in (
        "What's my average centipawn loss?",
        "How many games have I played and what's my win rate?",
        "Show me games where I threw a winning position",
        "What's my draw rate in the Sicilian?",
        "my cp loss by phase",
    ):
        result = parser.parse(question, "u")
        assert result.filters.result is None, question
        assert "result: win" not in result.extracted_filters, question
        assert "result: loss" not in result.extracted_filters, question
        assert "result: draw" not in result.extracted_filters, question

    # And the metric survives into the semantic query intact.
    assert (
        parser.parse("What's my average centipawn loss?", "u").semantic_query
        == "What's my average centipawn loss"
    )


def test_an_outcome_word_outside_a_metric_name_still_filters() -> None:
    """The guard is a phrase list, not a blanket exemption for the word."""
    parser = QueryParser()
    assert parser.parse("my losses", "u").filters.result == "loss"
    assert parser.parse("games I won", "u").filters.result == "win"
    assert parser.parse("show me draws", "u").filters.result == "draw"
    # One protected occurrence and one free one in the same question: the free
    # one filters, and only the free one is removed.
    mixed = parser.parse("my win rate in games I won", "u")
    assert mixed.filters.result == "win"
    assert mixed.semantic_query == "my win rate in games I"


def test_a_question_naming_both_colors_applies_no_color_filter() -> None:
    """A comparison is not a restriction.

    "Am I better with white or black?" matched `with white` first and stopped,
    so a question about the difference between the two sides retrieved one of
    them — and asked the embedder about "Am I better or black".
    """
    parser = QueryParser()
    for question in (
        "Am I better with white or black?",
        "as white or as black",
        "do I blunder more with the white pieces or the black pieces?",
    ):
        result = parser.parse(question, "u")
        assert result.filters.user_color is None, question
        assert not any(f.startswith("color: ") for f in result.extracted_filters), question

    assert (
        parser.parse("Am I better with white or black?", "u").semantic_query
        == "Am I better with white or black"
    )
    # One colour named is still a filter.
    assert parser.parse("my games as black", "u").filters.user_color == "black"


def test_keyword_removal_is_word_bounded() -> None:
    """`_remove_keyword` used to be a bare substring replacement, unlike the
    result and time-control loops, which compiled `\\b…\\b`. Two code paths that
    should agree now do."""
    parser = QueryParser()
    result = parser.parse("whitespace in my games", "u")
    assert result.filters.user_color is None
    assert result.semantic_query == "whitespace in my games"


def test_the_phase_filter_is_parsed_but_not_announced() -> None:
    """The small half of #18.

    `build_where` never applies `weak_phase`, so listing it in
    `extracted_filters` put "Note: The search was filtered by: [phase: endgame]"
    in front of the model for a set of games nothing had selected for. The field
    stays — `hybrid.py` merges it and something may yet apply it — the claim
    goes.
    """
    parser = QueryParser()
    result = parser.parse("show me my endgame losses", "u")
    assert result.filters.weak_phase == "endgame"
    assert result.extracted_filters == ["result: loss"]
