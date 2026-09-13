"""Phase 5, and the Phase 7 cutover gate: Assembled Prompt parity.

**The parity target is the same Assembled Prompt, not the same answer.** Answers
are non-deterministic — no call site sets a temperature, so every provider
samples at its own default and the *Go* implementation already answers the same
question differently on consecutive runs. Diffing answers would compare two
samples from one distribution and dress the difference up as a finding.

A prompt that matches means retrieval, routing, classification, and stats
formatting all match: one diff covers the whole path, because every input the
chat provider receives is the prompt.

**There is no allowance for a legitimate difference here.** The ordering defects
that would have produced one were fixed in the Go tree before the goldens were
captured. If a new ordering difference appears, it is a bug in the port.
"""

from __future__ import annotations

import difflib
import json
import pathlib
from typing import Any

import pytest

from tests.conftest import GOLDEN_DIR, load_golden
from zeitnot.chat.classifier import classify_query, extract_mentioned_openings
from zeitnot.chat.service import Config, Service
from zeitnot.config import resolve
from zeitnot.db import DB
from zeitnot.search.parser import QueryParser

pytestmark = [pytest.mark.corpus, pytest.mark.golden]


# ---------- pure functions: no corpus, no provider ----------


def test_query_classification_matches_the_golden() -> None:
    """Classification decides which context the router assembles, so a question
    that reroutes changes the prompt wholesale."""
    for case in load_golden("classification.json"):
        question = case["question"]
        assert str(classify_query(question)) == case["query_type"], question
        assert extract_mentioned_openings(question) == case["mentioned_openings"], question


def test_query_parsing_matches_the_golden() -> None:
    """Parsing decides which games are retrieved, so a filter that differs
    changes the prompt without changing a single format string."""
    parser = QueryParser()
    for case in load_golden("parsing.json"):
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
    assertion possible at all.
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


# ---------- the whole-corpus gate ----------


@pytest.fixture(scope="module")
def service(db: DB, prompt_manifest: dict[str, Any]) -> Service:
    """A Service wired exactly as `zeitnot chat` wires it.

    Same embedder, same NumSimilar and DetailLimit as the capture. A different
    value for either would produce a prompt no real session ever sees.
    """
    cfg = resolve()
    if cfg.embed_provider != "ollama" or cfg.embed_model != "nomic-embed-text":
        pytest.skip(
            f"goldens were captured with ollama/nomic-embed-text; "
            f"this environment has {cfg.embed_provider}/{cfg.embed_model}"
        )
    embedder = cfg.new_embedder()

    # A chat model is required by the constructor but never called: the gate
    # reads the prompt, not an answer.
    from tests.llmtest import FakeChatModel

    return Service(
        db,
        FakeChatModel(),
        embedder,
        Config(
            username=prompt_manifest["username"],
            num_similar=prompt_manifest["num_similar"],
            detail_limit=prompt_manifest["detail_limit"],
        ),
    )


def _check_fingerprint(db: DB, prompt_manifest: dict[str, Any]) -> None:
    """Refuse to compare across a corpus change.

    The whole-corpus goldens do not survive a growing corpus: every added game
    shifts win rates, CPL averages, and the comparison strings built from them.
    Reporting "corpus changed, recapture required" is the point — a diff here
    would look exactly like a port bug.
    """
    import hashlib

    with db.cursor() as cur:
        cur.execute("SELECT uuid::text FROM games")
        uuids = sorted(row[0] for row in cur.fetchall())

    digest = hashlib.sha256()
    for uuid in uuids:
        digest.update(uuid.encode())
        digest.update(b"\n")
    fingerprint = digest.hexdigest()[:16]

    if fingerprint != prompt_manifest["corpus_fingerprint"]:
        pytest.skip(
            f"corpus changed, recapture required: fingerprint {fingerprint} "
            f"({len(uuids)} games) vs golden {prompt_manifest['corpus_fingerprint']} "
            f"({prompt_manifest['game_count']} games). "
            "These prompts are whole-corpus goldens frozen at the cutover and "
            "there is no capture tool any more; see testdata/golden/MANIFEST.md"
        )


def test_the_corpus_fingerprint_still_matches(db: DB, prompt_manifest: dict[str, Any]) -> None:
    _check_fingerprint(db, prompt_manifest)


@pytest.mark.parametrize("index", range(1, 13))
def test_assembled_prompt_matches_the_golden_byte_for_byte(
    db: DB, service: Service, prompt_manifest: dict[str, Any], index: int
) -> None:
    """The gate. All twelve frozen questions, byte for byte.

    Ten from docs/multi-provider/03-eval-plan.md §2 plus the two adversarial
    extras, at the recorded corpus fingerprint.
    """
    _check_fingerprint(db, prompt_manifest)

    golden_path = GOLDEN_DIR / "prompts" / f"{index:02d}.txt"
    want = golden_path.read_text()
    question = prompt_manifest["questions"][index - 1]

    qctx = service.router.route(question)
    got = service.build_prompt(qctx)

    if got != want:
        diff = "\n".join(
            difflib.unified_diff(
                want.splitlines(),
                got.splitlines(),
                fromfile=f"go/{index:02d}.txt",
                tofile="python",
                lineterm="",
            )
        )
        pytest.fail(f"prompt {index} ({question!r}) diverged:\n{diff}")


def test_the_prompt_is_reproducible_across_repeated_assembly(
    db: DB, service: Service, prompt_manifest: dict[str, Any]
) -> None:
    """Two runs of the same question must produce the same prompt.

    This is what docs/multi-provider/03-eval-plan.md depends on for
    cross-run comparability, and it is independent of whether the port matches
    Go — a Python-only source of nondeterminism would fail here even with every
    golden passing.
    """
    _check_fingerprint(db, prompt_manifest)

    question = prompt_manifest["questions"][2]  # a comparative question
    first = service.build_prompt(service.router.route(question))
    for _ in range(2):
        assert service.build_prompt(service.router.route(question)) == first


def test_the_manifest_questions_are_the_frozen_eval_set(
    prompt_manifest: dict[str, Any],
) -> None:
    """A guard against the question set drifting.

    Changing a question destroys comparability with every recorded eval run;
    adding one is fine. This asserts the ten scored questions are unchanged and
    in order.
    """
    assert prompt_manifest["questions"][:10] == [
        "What's my average centipawn loss?",
        "How many games have I played and what's my win rate?",
        "Am I better with white or black?",
        "Which time control is my best?",
        "Show me games where I threw a winning position",
        "What openings do I lose with most often?",
        "What should I study to improve fastest?",
        "What's my biggest weakness?",
        "Have I improved over the last month?",
        "Is my accuracy getting better or worse?",
    ]


def test_every_query_type_is_exercised_by_the_frozen_set(
    prompt_manifest: dict[str, Any],
) -> None:
    """Two questions per QueryType, since the type decides which context the
    router assembles. A single-category set would hide a whole branch."""
    types = [str(classify_query(q)) for q in prompt_manifest["questions"][:10]]
    assert set(types) == {
        "aggregate",
        "comparative",
        "specific_games",
        "recommendation",
        "trend",
    }


def test_goldens_are_present_or_the_suite_says_why() -> None:
    """A directory-shape check, so a missing capture reads as "regenerate"
    rather than as a mysterious skip."""
    manifest = GOLDEN_DIR / "prompts" / "manifest.json"
    assert manifest.exists()
    questions = json.loads(manifest.read_text())["questions"]
    for index in range(1, len(questions) + 1):
        path: pathlib.Path = GOLDEN_DIR / "prompts" / f"{index:02d}.txt"
        assert path.exists(), f"missing {path}; rerun cmd/golden"
