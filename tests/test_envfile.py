"""The loader that replaced the shell.

Every case here is a failure the README used to document a workaround for. They
run with no database, no network and no goldens, because the whole point of the
module is that configuration stops depending on the environment being right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zeitnot import envfile


def write(tmp_path: Path, text: str, name: str = "envfile") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def load(
    tmp_path: Path, text: str, environ: dict[str, str] | None = None
) -> tuple[dict[str, str], envfile.Loaded]:
    env: dict[str, str] = {} if environ is None else environ
    loaded = envfile.load(env, write(tmp_path, text))
    return env, loaded


# ---------- parsing ----------


def test_export_prefix_and_comments_are_understood(tmp_path: Path) -> None:
    """`export $(cat ... | xargs)` split comments into arguments and died on
    them. A parser reads what a shell would, without the word splitting."""
    env, loaded = load(
        tmp_path,
        "# a comment\n\nexport CHAT_PROVIDER=ollama\nEMBED_PROVIDER=ollama   # trailing comment\n",
    )
    assert env["CHAT_PROVIDER"] == "ollama"
    assert env["EMBED_PROVIDER"] == "ollama"
    assert loaded.problems == ()


def test_a_hash_inside_a_value_is_not_a_comment(tmp_path: Path) -> None:
    """Only whitespace-then-hash starts a comment; a bare one is legal in a URL."""
    env, _ = load(tmp_path, "DATABASE_URL=postgres://u:p@localhost:5432/db#frag\n")
    assert env["DATABASE_URL"] == "postgres://u:p@localhost:5432/db#frag"


def test_quotes_are_stripped(tmp_path: Path) -> None:
    env, _ = load(tmp_path, "A=\"double\"\nB='single'\nC=bare\n")
    assert (env["A"], env["B"], env["C"]) == ("double", "single", "bare")


def test_an_unterminated_quote_is_reported(tmp_path: Path) -> None:
    _, loaded = load(tmp_path, 'A="oops\n')
    assert any("unterminated" in problem for problem in loaded.problems)


def test_a_line_that_is_not_an_assignment_is_reported_and_skipped(tmp_path: Path) -> None:
    """Collected rather than raised: a loader that stops at the first bad line
    cannot tell you about the second one."""
    env, loaded = load(tmp_path, "this is not an assignment\nA=1\n")
    assert env["A"] == "1"
    assert len(loaded.problems) == 1
    assert "line 1" in loaded.problems[0]


# ---------- the failures this module exists to retire ----------


def test_crlf_line_endings_no_longer_corrupt_every_value(tmp_path: Path) -> None:
    """The worst of them: sourcing a CRLF file put a carriage return on the end
    of every value, including the API keys, and — because DATABASE_URL is built
    from ZEITNOT_DB_PORT — in the *middle* of the connection string."""
    env, loaded = load(
        tmp_path,
        "export ZEITNOT_DB_PORT=5433\r\n"
        'export DATABASE_URL="postgres://c:c@localhost:${ZEITNOT_DB_PORT}/zeitnot"\r\n',
    )
    assert env["ZEITNOT_DB_PORT"] == "5433"
    assert env["DATABASE_URL"] == "postgres://c:c@localhost:5433/zeitnot"
    assert "\r" not in env["DATABASE_URL"]
    assert loaded.crlf is True


def test_a_reference_defined_below_its_use_still_resolves(tmp_path: Path) -> None:
    """Appending ZEITNOT_DB_PORT to a file that already had DATABASE_URL used to
    leave "localhost:/zeitnot", which libpq reads as 5432 — usually the very
    server the user was moving off."""
    env, _ = load(
        tmp_path,
        'export DATABASE_URL="postgres://c:c@localhost:${ZEITNOT_DB_PORT}/zeitnot"\n'
        "export ZEITNOT_DB_PORT=5433\n",
    )
    assert env["DATABASE_URL"] == "postgres://c:c@localhost:5433/zeitnot"


def test_a_byte_order_mark_does_not_become_part_of_the_first_name(tmp_path: Path) -> None:
    path = tmp_path / "envfile"
    path.write_text("A=1\n", encoding="utf-8-sig")
    env: dict[str, str] = {}
    envfile.load(env, path)
    assert env == {"A": "1"}


# ---------- precedence ----------


def test_the_environment_wins_and_the_file_says_so(tmp_path: Path) -> None:
    """The old surprise ran the other way: sourcing overwrote a value exported
    by hand, which is why a port change so often appeared not to take. The
    deferred name is reported because being right silently is not enough here."""
    env, loaded = load(
        tmp_path,
        "DATABASE_URL=postgres://from:file@localhost:5432/zeitnot\n",
        {"DATABASE_URL": "postgres://from:shell@localhost:5433/zeitnot"},
    )
    assert env["DATABASE_URL"] == "postgres://from:shell@localhost:5433/zeitnot"
    assert loaded.deferred == ("DATABASE_URL",)
    assert loaded.applied == ()


def test_an_exported_empty_value_counts_as_unset(tmp_path: Path) -> None:
    """`config.resolve` treats "" as absent everywhere, so the file filling one
    in is what the user expects."""
    env, loaded = load(tmp_path, "ANTHROPIC_API_KEY=sk-from-file\n", {"ANTHROPIC_API_KEY": ""})
    assert env["ANTHROPIC_API_KEY"] == "sk-from-file"
    assert loaded.applied == ("ANTHROPIC_API_KEY",)


def test_a_reference_resolves_against_the_environment_before_the_file(tmp_path: Path) -> None:
    """A built value has to be consistent with the port actually in effect: an
    exported ZEITNOT_DB_PORT=5433 must produce a URL naming 5433, not the 5432
    the file happens to say."""
    env, _ = load(
        tmp_path,
        "ZEITNOT_DB_PORT=5432\n"
        'DATABASE_URL="postgres://c:c@localhost:${ZEITNOT_DB_PORT}/zeitnot"\n',
        {"ZEITNOT_DB_PORT": "5433"},
    )
    assert env["DATABASE_URL"] == "postgres://c:c@localhost:5433/zeitnot"


# ---------- interpolation edge cases ----------


def test_single_quotes_suppress_interpolation(tmp_path: Path) -> None:
    """Matching the shell, and matching what the README already tells anyone
    whose DATABASE_URL arrived with the variable still in it."""
    env, _ = load(tmp_path, "PORT=5433\nA='literal ${PORT}'\n")
    assert env["A"] == "literal ${PORT}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PORT=5433\nA=\\${PORT}\n", "${PORT}"),
        # Not a reference at all — there is no name after the dollar.
        ("A=costs \\$5\n", "costs $5"),
    ],
)
def test_a_backslash_escapes_a_dollar(tmp_path: Path, text: str, expected: str) -> None:
    env, _ = load(tmp_path, text)
    assert env["A"] == expected


def test_an_undefined_reference_expands_to_nothing_and_is_reported(tmp_path: Path) -> None:
    env, loaded = load(tmp_path, 'A="x${NOWHERE}y"\n')
    assert env["A"] == "xy"
    assert any("NOWHERE" in problem for problem in loaded.problems)


def test_a_reference_cycle_is_reported_rather_than_recursing(tmp_path: Path) -> None:
    _, loaded = load(tmp_path, 'A="${B}"\nB="${A}"\n')
    assert any("refers to itself" in problem for problem in loaded.problems)


# ---------- which file ----------


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """A user who exports everything by hand, or runs from a container, never
    needs one."""
    loaded = envfile.load({}, tmp_path / "absent")
    assert loaded.exists is False
    assert loaded.problems == ()


def test_the_path_variable_redirects_the_loader(tmp_path: Path) -> None:
    path = write(tmp_path, "A=1\n")
    assert envfile.default_path({envfile.PATH_VAR: str(path)}) == path


def test_an_empty_path_variable_switches_loading_off(tmp_path: Path) -> None:
    """The escape hatch for an environment that is already fully configured."""
    assert envfile.default_path({envfile.PATH_VAR: ""}) is None
    assert envfile.load({}, None) is not None


def test_the_default_is_relative_to_the_working_directory() -> None:
    assert envfile.default_path({}) == Path(envfile.DEFAULT_FILENAME)


def test_values_are_never_recorded_in_the_result(tmp_path: Path) -> None:
    """`Loaded` is handed to a command whose output is written to be pasted in
    public, and it is built on a file holding API keys."""
    _, loaded = load(tmp_path, "ANTHROPIC_API_KEY=sk-secret-value\n")
    assert "sk-secret-value" not in repr(loaded)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A=  spaced  \n", "spaced"),
        ("A=\n", ""),
        ("  export A=1\n", "1"),
        ("A = 1\n", "1"),
    ],
)
def test_whitespace_around_an_assignment_is_tolerated(
    tmp_path: Path, text: str, expected: str
) -> None:
    """docker compose already trims these, so a file it reads happily has to
    mean the same thing here — the two disagreeing is what let Compose come up
    fine while every sourced value was subtly wrong."""
    env, _ = load(tmp_path, text)
    assert env["A"] == expected
