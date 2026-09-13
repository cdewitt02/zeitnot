"""Shared fixtures.

The corpus-backed tests run against the *live* database, deliberately. Phase 2
of the rewrite plan verifies the database layer "against the live corpus, not
fixtures", because a float-conversion bug in the vector path is exactly the kind
of defect a fixture would reproduce faithfully and wrongly.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "testdata" / "golden"


def load_golden(name: str) -> Any:
    return json.loads((GOLDEN_DIR / name).read_text())


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    return url


@pytest.fixture(scope="session")
def db(database_url: str):  # type: ignore[no-untyped-def]
    from zeitnot.db import DB

    database = DB(database_url)
    yield database
    database.close()


@pytest.fixture(scope="session")
def corpus_username() -> str:
    """The username the goldens were captured for.

    Read from the prompt manifest rather than hardcoded, so a recapture for a
    different player does not silently compare the wrong things.
    """
    manifest = GOLDEN_DIR / "prompts" / "manifest.json"
    if not manifest.exists():
        pytest.skip(
            "corpus goldens are gitignored and are not present; "
            "see testdata/golden/MANIFEST.md for what it takes to recapture them"
        )
    return str(json.loads(manifest.read_text())["username"])


@pytest.fixture(scope="session")
def prompt_manifest() -> dict[str, Any]:
    manifest = GOLDEN_DIR / "prompts" / "manifest.json"
    if not manifest.exists():
        pytest.skip(
            "corpus goldens are gitignored and are not present; "
            "see testdata/golden/MANIFEST.md for what it takes to recapture them"
        )
    result: dict[str, Any] = json.loads(manifest.read_text())
    return result


@pytest.fixture(scope="session")
def summary_goldens() -> list[dict[str, Any]]:
    path = GOLDEN_DIR / "summaries.json"
    if not path.exists():
        pytest.skip(
            "corpus goldens are gitignored and are not present; "
            "see testdata/golden/MANIFEST.md for what it takes to recapture them"
        )
    result: list[dict[str, Any]] = json.loads(path.read_text())
    return result
