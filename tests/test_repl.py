"""The REPL's degradation rules.

The behavior that must not change when glamour becomes rich: everywhere styling
is unavailable — a pipe, a file, NO_COLOR, TERM=dumb — nothing is streamed and
the raw markdown is printed once. Cursor-up escapes would corrupt a captured
transcript.
"""

from __future__ import annotations

import io

import pytest

from zeitnot.repl import styling_available


class _NotATTY(io.StringIO):
    def isatty(self) -> bool:
        return False


class _ATTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_pipe_disables_styling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case rich's own is_terminal gets wrong.

    rich reports True under a plain pipe, which would put escape codes into a
    redirected transcript, so the check asks the file descriptor directly.
    """
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", _NotATTY())
    assert not styling_available()


def test_a_terminal_enables_styling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", _ATTY())
    assert styling_available()


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("NO_COLOR", "1"),
        # An unset TERM is normal for a pipe and abnormal for a terminal; in
        # both cases assuming no styling is the safe read.
        ("TERM", ""),
        ("TERM", "dumb"),
    ],
)
def test_the_environment_can_disable_styling_on_a_real_terminal(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv(env, value)
    monkeypatch.setattr("sys.stdout", _ATTY())
    assert not styling_available()


def test_a_stdout_without_isatty_is_not_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some embedders replace sys.stdout with an object that has no isatty.
    Guessing "terminal" there would corrupt their capture."""
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", object())
    assert not styling_available()
