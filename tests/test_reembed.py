"""`zeitnot data reembed`: the width check before its first write (#21), and
one `embed` call per batch rather than per summary (#20).

With no database and no network: the DB is a fake that records writes, and the
embedder is `tests/llmtest.py`'s.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from typer.testing import CliRunner

from tests.llmtest import FakeEmbedder
from zeitnot import cli, config
from zeitnot.db import IndexMeta
from zeitnot.db.records import SummaryTextRow


class _FakeDB:
    """Just enough DB for `reembed`, recording every write."""

    def __init__(
        self,
        column_dims: int = 768,
        meta: IndexMeta | None = None,
        rows: list[SummaryTextRow] | None = None,
    ) -> None:
        self.column_dims = column_dims
        self.meta = meta
        self.rows = rows or [SummaryTextRow(game_uuid="a", summary_text="won as white in blitz.\n")]
        self.updated: list[str] = []
        self.vectors: dict[str, list[float]] = {}
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
        return self.rows

    def update_summary_embedding(self, game_uuid: str, vector: list[float]) -> None:
        self.updated.append(game_uuid)
        self.vectors[game_uuid] = vector

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


# ---------- batching (#20) ----------


def _rows(n: int) -> list[SummaryTextRow]:
    # Distinct lengths, because FakeEmbedder's vectors depend on text length:
    # a vector written against the wrong game would then show up as a mismatch.
    return [SummaryTextRow(game_uuid=f"g{i}", summary_text="x" * (i + 1)) for i in range(n)]


def test_one_embed_call_per_batch_not_per_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """300 summaries are ⌈300/128⌉ = 3 requests on a batching embedder, not 300."""
    rows = _rows(300)
    db = _FakeDB(rows=rows)
    embedder = FakeEmbedder()

    code, output = _run(monkeypatch, db, embedder)

    assert code == 0, output
    assert [len(call) for call in embedder.calls] == [128, 128, 44]
    assert db.updated == [row.game_uuid for row in rows]


def test_each_vector_is_written_to_its_own_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """`embed` returns vectors in input order, so the pairing is positional."""
    rows = _rows(130)
    db = _FakeDB(rows=rows)

    code, output = _run(monkeypatch, db, FakeEmbedder())

    assert code == 0, output
    reference = FakeEmbedder()
    for row in rows:
        assert db.vectors[row.game_uuid] == reference.embed([row.summary_text])[0], row.game_uuid


def test_progress_still_advances_per_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """The progress line is how anyone decides whether to walk away, so it keeps
    its every-25 cadence rather than jumping a chunk at a time."""
    code, output = _run(monkeypatch, _FakeDB(rows=_rows(130)), FakeEmbedder())

    assert code == 0, output
    progress = [line for line in output.splitlines() if line.startswith("[")]
    assert progress == [f"[{n}/130]" for n in (25, 50, 75, 100, 125, 130)]


@dataclass
class _FailsOnCall(FakeEmbedder):
    fail_on: int = 2

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(self.calls) + 1 == self.fail_on:
            self.calls.append(list(texts))
            raise RuntimeError("embedding service went away")
        return super().embed(texts)


def test_a_failure_partway_keeps_the_vectors_already_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first batch is written before the second is requested, and the index
    is not restamped for a pass that did not finish."""
    db = _FakeDB(rows=_rows(300))

    code, _ = _run(monkeypatch, db, _FailsOnCall(fail_on=2))

    assert code != 0
    assert db.updated == [f"g{i}" for i in range(128)]
    assert db.stamped is None


@dataclass
class _Short(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return super().embed(texts)[:-1]


def test_a_short_batch_fails_rather_than_misaligning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pairing is positional, so a missing vector must stop the pass rather than
    shift every later vector onto the wrong game."""
    db = _FakeDB(rows=_rows(3))

    code, output = _run(monkeypatch, db, _Short())

    assert code == 1
    assert "returned 2 vectors for 3 summaries" in output
    assert db.updated == []
