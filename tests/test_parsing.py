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

**Read a failure here as "the parser changed", not "the parser broke."** Both
tables are Go captures: the questions were chosen by hand, but the expected
values are what the Go implementation returned. `parsing.json` is known to
record wrong answers — `"What's my average centipawn loss?"` carries a
`result: loss` filter, so a question about average accuracy retrieves only lost
games — and this module asserts them, because pinning the behavior is still
worth more than not pinning it. The defects are tracked as issue #40 and the
table split as #41; `testdata/golden/MANIFEST.md` lists what is known to be
wrong.
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
