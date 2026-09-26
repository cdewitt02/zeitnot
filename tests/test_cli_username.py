"""The command wiring for issue #15, with no database and no network.

`tests/test_username.py` pins the resolution itself and `tests/test_db_corpus.py`
pins it against a real database. What is left, and what the issue's "done when"
actually names, is that the **command** resolves before it uses the name — that
`chat` hands the corpus's spelling to everything downstream rather than whatever
was typed. That is wiring, not logic, and it is the part a later refactor would
quietly drop.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from zeitnot import cli, config

REGISTERED = "cdew4"


class _FakeDB:
    """Just enough DB for `chat` to reach the REPL."""

    def __init__(self, known: str | None = REGISTERED) -> None:
        self._known = known

    def __enter__(self) -> _FakeDB:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def corpus_is_initialized(self) -> bool:
        return True

    def canonical_username(self, typed: str) -> str | None:
        if self._known is not None and typed.strip().lower() == self._known.lower():
            return self._known
        return None


class _Cfg:
    chat_model = "fake-model"

    def new_chat_model(self) -> object:
        return object()

    def new_embedder(self) -> object:
        return object()

    def summary(self) -> str:
        return "fake / fake"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _patch_chat(monkeypatch: pytest.MonkeyPatch, db: _FakeDB) -> dict[str, Any]:
    """Stub everything `chat` touches past name resolution; return what the REPL saw."""
    seen: dict[str, Any] = {}

    monkeypatch.setattr(cli, "_open_db", lambda *a: db)
    monkeypatch.setattr(cli, "_database_url", lambda: "postgresql://fake/fake")
    # `cli` does `from zeitnot import config`, so the module object is shared
    # and patching it here is what the command will see.
    monkeypatch.setattr(config, "resolve", lambda *a, **k: _Cfg())
    monkeypatch.setattr(config, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(config, "check_index", lambda *a, **k: None)
    monkeypatch.setattr(cli, "Service", lambda *a, **k: object())

    def _capture_repl(service: object, username: str, summary: str) -> None:
        seen["username"] = username

    monkeypatch.setattr("zeitnot.repl.run_repl", _capture_repl)
    return seen


@pytest.mark.parametrize("typed", ["cdew4", "CDEW4", "Cdew4", "  cdew4  "])
def test_chat_resolves_before_it_builds_anything(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    """`chat` carries the name into every filter, prompt and banner.

    Resolution has to happen before the Service is constructed, so this asserts
    the name the REPL was handed rather than anything it printed.
    """
    seen = _patch_chat(monkeypatch, _FakeDB())

    result = runner.invoke(cli.app, ["chat", typed])

    assert result.exit_code == 0, result.output
    assert seen["username"] == REGISTERED


def test_a_differing_spelling_is_reported_rather_than_silently_swapped(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correct-but-silent is how this bug got here; say which name is in use."""
    _patch_chat(monkeypatch, _FakeDB())

    result = runner.invoke(cli.app, ["chat", "CDEW4"])

    assert result.exit_code == 0, result.output
    assert REGISTERED in result.output
    assert "CDEW4" in result.output


def test_the_spelling_that_was_typed_is_not_announced(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is for a difference, so the ordinary run stays quiet."""
    _patch_chat(monkeypatch, _FakeDB())

    result = runner.invoke(cli.app, ["chat", REGISTERED])

    assert result.exit_code == 0, result.output
    assert "the spelling Chess.com records" not in result.output


def test_a_name_in_no_game_fails_naming_the_remedy(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The symptom this replaces was `0 total games` and no explanation.

    It also has to rule out the wrong diagnosis: someone who has just learned
    that case used to matter will assume case is the problem, and it no longer
    is.
    """
    seen = _patch_chat(monkeypatch, _FakeDB(known=None))

    result = runner.invoke(cli.app, ["chat", "someone-else"])

    assert result.exit_code == 1
    assert "no games in this corpus" in result.output
    assert "zeitnot data analyze someone-else" in result.output
    assert "case you typed is not the problem" in result.output
    assert "username" not in seen
