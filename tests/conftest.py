"""Shared fixtures.

The corpus-backed tests run against the *live* database, deliberately. Phase 2
of the rewrite plan verifies the database layer "against the live corpus, not
fixtures", because a float-conversion bug in the vector path is exactly the kind
of defect a fixture would reproduce faithfully and wrongly.

They assume that database was ingested by the current tree. A corpus is
disposable ([ADR 0004](../docs/adr/0004-the-corpus-is-ephemeral.md)): when a
corpus test fails on a database ingested by older code, the answer is to drop it
and re-ingest, not to teach the test about old rows.

Nothing here reads a gitignored file any more. The corpus-derived goldens were
retired in [ADR 0003](../docs/adr/0003-retire-the-parity-goldens.md); what is
left in `testdata/golden/` is committed, so a fixture that skips on a missing
file no longer has anything to skip for.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - the DB import is deferred to the fixture
    from zeitnot.db import DB

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "testdata" / "golden"


def load_golden(name: str) -> Any:
    """Read one of the committed expected-output tables.

    All three are in the repository, so this reads rather than guards: a missing
    file is a broken checkout and should surface as the error it is.
    """
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
def corpus_username(db: DB) -> str:
    """Whose corpus this is, asked of the corpus.

    This used to be read from the prompt golden's manifest, which pinned every
    corpus test to one capture on one machine. A zeitnot database holds one
    player's games — that is what `zeitnot data` ingests — so the username that
    appears in every game is derivable, and deriving it is what lets these tests
    run against anybody's corpus.

    `ZEITNOT_CORPUS_USERNAME` overrides it, for a database that was pointed at
    more than one player.
    """
    override = os.environ.get("ZEITNOT_CORPUS_USERNAME", "")
    if override:
        return override

    with db.cursor() as cur:
        cur.execute(
            """SELECT username, COUNT(*) AS n FROM (
                   SELECT white_username AS username FROM games
                   UNION ALL
                   SELECT black_username AS username FROM games
               ) sides
               GROUP BY username ORDER BY n DESC, username LIMIT 1"""
        )
        row = cur.fetchone()

    if row is None or not row[0]:
        pytest.skip("the database has no games, so there is no corpus to test against")
    return str(row[0])
