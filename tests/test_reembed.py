"""`zeitnot data reembed` checks the column width before its first write (#21).

With no database and no network: the DB is a fake that records writes, and the
embedder is `tests/llmtest.py`'s.
"""

from __future__ import annotations

import io

import pytest
from typer.testing import CliRunner

from tests.llmtest import FakeEmbedder
from zeitnot import cli, config
from zeitnot.db import IndexMeta
from zeitnot.db.records import SummaryTextRow


class _FakeDB:
    """Just enough DB for `reembed`, recording every write."""

    def __init__(self, column_dims: int = 768, meta: IndexMeta | None = None) -> None:
        self.column_dims = column_dims
        self.meta = meta
        self.updated: list[str] = []
        self.stamped: IndexMeta | None = None

    def __enter__(self) -> _FakeDB:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def migrate(self) -> None:
        return None

    def embedding_dimensions(self) -> int:
        return self.column_dims

    def get_index_meta(self) -> IndexMeta | None:
        return self.meta

    def all_summary_texts(self) -> list[SummaryTextRow]:
        return [SummaryTextRow(game_uuid="a", summary_text="won as white in blitz.\n")]

    def update_summary_embedding(self, game_uuid: str, vector: list[float]) -> None:
        self.updated.append(game_uuid)

    def set_index_meta(self, meta: IndexMeta) -> None:
        self.stamped = meta


class _Cfg:
    def __init__(self, embedder: FakeEmbedder) -> None:
        self._embedder = embedder

    def new_embedder(self) -> FakeEmbedder:
        return self._embedder


def _run(monkeypatch: pytest.MonkeyPatch, db: _FakeDB, embedder: FakeEmbedder) -> tuple[int, str]:
    monkeypatch.setattr(cli, "_open_db", lambda *a: db)
    monkeypatch.setattr(config, "resolve", lambda *a, **k: _Cfg(embedder))
    monkeypatch.setattr(config, "preflight", lambda *a, **k: None)
    result = CliRunner().invoke(cli.app, ["data", "reembed"])
    return result.exit_code, result.output


def test_a_width_mismatch_fails_before_the_first_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Used to print "Re-embedding …" and then die on a raw pgvector error."""
    db = _FakeDB(column_dims=768)
    embedder = FakeEmbedder(dims=1024)

    code, output = _run(monkeypatch, db, embedder)

    assert code == 1
    assert (
        "embedding width mismatch: fake/fake-embed produces 1024 dimensions "
        "but game_summaries.embedding is vector(768)"
    ) in output
    assert "Re-embedding" not in output
    assert embedder.calls == []
    assert db.updated == []
    assert db.stamped is None


def test_a_provenance_mismatch_does_not_stop_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-embedding is how a provenance mismatch is resolved, so it must run."""
    db = _FakeDB(meta=IndexMeta(embed_provider="other", embed_model="other-embed", dimensions=768))

    code, output = _run(monkeypatch, db, FakeEmbedder(dims=768))

    assert code == 0, output
    assert db.updated == ["a"]
    assert db.stamped == IndexMeta(embed_provider="fake", embed_model="fake-embed", dimensions=768)


def test_check_index_still_refuses_a_width_mismatch() -> None:
    """The width check moved into a helper; `check_index` must still run it."""
    with pytest.raises(config.ConfigError, match="embedding width mismatch"):
        config.check_index(_FakeDB(column_dims=768), FakeEmbedder(dims=1024), False, io.StringIO())  # type: ignore[arg-type]
