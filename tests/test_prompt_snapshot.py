"""The whole assembled prompt, byte for byte, with no corpus behind it.

`test_router_prompt.py` asserts properties: no opponent handle, no superlative a
sample cannot support, an opening question gets opening stats. Each of those
names the thing it is looking for, so a change nobody asserted — a reordered
section, a dropped instruction, a doubled blank line — passes every one of them.
Catching that was the job of `testdata/golden/prompts/`, and it went with the
rest of the corpus goldens
([ADR 0003](../docs/adr/0003-retire-the-parity-goldens.md)). This is the
replacement.

**Why this is not the thing the ADR refuses to build.** The retired prompts were
whole-*corpus* captures: every added game shifted win rates and CPL averages, so
they were unreproducible, unreviewable, gitignored, and stale within a week. The
snapshots here are rendered from `tests/promptfixtures.py` — six hand-written
buckets — so they are committed, they survive a growing corpus because no corpus
is involved, and a change to them shows up as a readable diff in the pull request
that caused it. That last point is the whole difference. A golden nobody can see
proves nothing; a golden in the diff is the review.

**When one of these fails**, read the diff and decide whether the change was
intended. If it was, regenerate:

    ZEITNOT_UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshot.py

and commit the result *in the same change that caused it*, so the reviewer sees
the prompt move alongside the code that moved it. Regenerating to clear a red
test in an unrelated change is the failure mode this file exists to prevent.
"""

from __future__ import annotations

import difflib
import os
import pathlib
from typing import NamedTuple

import pytest

from tests.conftest import GOLDEN_DIR
from tests.promptfixtures import make_prompt
from zeitnot.chat.classifier import QueryType

SNAPSHOT_DIR = GOLDEN_DIR / "prompt_snapshots"


# Every query type, plus the two branches that are not a query type:
#
# `about_openings` keys off a separate predicate rather than rerouting the
# question, so it is only visible on a type that does not already get opening
# aggregates — comparative and recommendation always do, and their snapshots
# would be byte-identical.
#
# `mentioned_openings` writes the OPENING-SPECIFIC STATS section, and both of
# its outcomes are worth pinning: a named opening the player has games in, and
# one they have none in, which must say so rather than going silent.
class Case(NamedTuple):
    name: str
    query_type: QueryType
    about_openings: bool = False
    mentioned_openings: list[str] = []  # noqa: RUF012 - read-only, never mutated
    filters: list[str] = []  # noqa: RUF012 - read-only, never mutated


CASES: list[Case] = [
    *[Case(str(query_type), query_type) for query_type in QueryType],
    Case("specific_games-openings", QueryType.SPECIFIC_GAMES, about_openings=True),
    Case(
        "recommendation-named-opening",
        QueryType.RECOMMENDATION,
        mentioned_openings=["Caro Kann Defense"],
    ),
    Case(
        "recommendation-unplayed-opening",
        QueryType.RECOMMENDATION,
        mentioned_openings=["Catalan"],
    ),
    # The filter note, which `Service.build_prompt` appends after the router is
    # done. It is the last prompt text still formatted to imitate Go's `%v` —
    # space-separated inside square brackets — and the retired whole-corpus
    # goldens were the only thing that had ever pinned it.
    Case(
        "specific_games-filtered",
        QueryType.SPECIFIC_GAMES,
        filters=["result: loss", "color: black", "time_class: blitz"],
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_the_assembled_prompt_matches_its_snapshot(case: Case) -> None:
    got = make_prompt(
        case.query_type,
        about_openings=case.about_openings,
        mentioned_openings=case.mentioned_openings,
        filters=case.filters,
    )
    name = case.name
    path: pathlib.Path = SNAPSHOT_DIR / f"{name}.txt"

    if os.environ.get("ZEITNOT_UPDATE_PROMPT_SNAPSHOTS"):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        pytest.skip(f"rewrote {path.name}; review the diff before committing it")

    assert path.exists(), (
        f"missing {path}. Every case in CASES needs a committed snapshot — "
        "regenerate with ZEITNOT_UPDATE_PROMPT_SNAPSHOTS=1 and commit the file."
    )
    want = path.read_text()
    if got == want:
        return

    diff = "\n".join(
        difflib.unified_diff(
            want.splitlines(),
            got.splitlines(),
            fromfile=f"snapshot/{path.name}",
            tofile="assembled now",
            lineterm="",
        )
    )
    pytest.fail(f"the assembled prompt for {name} changed:\n{diff}")


def test_every_snapshot_on_disk_belongs_to_a_case() -> None:
    """A snapshot left behind after its case is renamed reads as passing.

    Nothing collects it, so nothing compares it, and the file sits in the
    repository looking like coverage.
    """
    expected = {f"{case.name}.txt" for case in CASES}
    found = {path.name for path in SNAPSHOT_DIR.glob("*.txt")}
    assert found == expected, (
        f"orphaned snapshots: {sorted(found - expected)}; missing: {sorted(expected - found)}"
    )
