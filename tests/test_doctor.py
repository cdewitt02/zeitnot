"""The checks that answer without a database, a network, or an engine.

The rest of `zeitnot.doctor` is a thin wrapper over calls the existing suites
already cover — `config.resolve`, `engine.require_stockfish`, each adapter's
preflight — so what is worth testing here is the part doctor adds: that a
failure is reported rather than raised, that the port invariant nothing else
enforces is actually compared, and that the output is safe to paste in public.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from zeitnot import config, doctor, envfile
from zeitnot.doctor import Check, Report, Status


def resolved(environ: dict[str, str]) -> config.Config:
    return config.resolve(lambda key: environ.get(key, ""))


# ---------- DATABASE_URL ----------


def test_a_missing_url_names_the_file_and_the_doctor() -> None:
    check, url = doctor.check_database_url({})
    assert check.status is Status.FAIL
    assert url == ""
    assert envfile.DEFAULT_FILENAME in check.detail


def test_a_control_character_is_reported_rather_than_raised() -> None:
    """cli exits on this one. doctor has to keep going: reporting the first of
    five problems five times is the round trip doctor exists to remove."""
    check, url = doctor.check_database_url(
        {"DATABASE_URL": "postgres://c:c@localhost:5433\r/zeitnot"}
    )
    assert check.status is Status.FAIL
    assert "control character" in check.detail
    assert url == ""


def test_a_usable_url_is_returned_for_the_checks_that_need_it() -> None:
    check, url = doctor.check_database_url(
        {"DATABASE_URL": "postgres://c:c@localhost:5432/zeitnot"}
    )
    assert check.status is Status.OK
    assert url == "postgres://c:c@localhost:5432/zeitnot"


# ---------- the port invariant ----------


def test_a_port_disagreement_is_reported() -> None:
    """The README says outright that nothing checks these agree. This is the
    something."""
    check = doctor.check_port_agreement(
        "postgres://c:c@localhost:5432/zeitnot", {"ZEITNOT_DB_PORT": "5433"}
    )
    assert check.status is Status.WARN
    assert "5433" in check.detail and "5432" in check.detail


def test_a_missing_port_in_the_url_means_5432() -> None:
    """No port at all is legitimate and means 5432; only an empty one is not."""
    check = doctor.check_port_agreement(
        "postgres://c:c@localhost/zeitnot", {"ZEITNOT_DB_PORT": "5432"}
    )
    assert check.status is Status.OK


def test_agreement_passes() -> None:
    check = doctor.check_port_agreement(
        "postgres://c:c@localhost:5433/zeitnot", {"ZEITNOT_DB_PORT": "5433"}
    )
    assert check.status is Status.OK


def test_no_declared_port_is_skipped_rather_than_guessed() -> None:
    check = doctor.check_port_agreement("postgres://c:c@localhost:5432/zeitnot", {})
    assert check.status is Status.SKIP


def test_an_unreadable_port_warns_instead_of_raising() -> None:
    """urlsplit raises ValueError on a non-numeric port, and a diagnostic that
    dies while diagnosing is worse than the failure it was called about."""
    check = doctor.check_port_agreement(
        "postgres://c:c@localhost:notaport/zeitnot", {"ZEITNOT_DB_PORT": "5433"}
    )
    assert check.status is Status.WARN


# ---------- the environment file ----------


def test_a_missing_file_says_which_path_was_looked_at(tmp_path: Path) -> None:
    """Running from the wrong directory is the failure a fixed filename trades
    for, so the absolute path is the whole answer."""
    checks = doctor.check_env_file(envfile.Loaded(path=tmp_path / "absent"))
    assert checks[0].status is Status.WARN
    assert str(tmp_path.resolve()) in checks[0].detail


def test_deferred_names_are_reported(tmp_path: Path) -> None:
    """Someone changing a port and seeing no effect has to be told which copy is
    winning, not merely that one is."""
    checks = doctor.check_env_file(
        envfile.Loaded(
            path=tmp_path / "envfile",
            exists=True,
            applied=("ZEITNOT_DB_PORT",),
            deferred=("DATABASE_URL",),
        )
    )
    assert checks[0].status is Status.OK
    assert "DATABASE_URL" in checks[0].detail


def test_handled_crlf_is_noted_without_being_a_failure(tmp_path: Path) -> None:
    checks = doctor.check_env_file(
        envfile.Loaded(path=tmp_path / "envfile", exists=True, crlf=True)
    )
    assert checks[0].status is Status.OK
    assert "CRLF" in checks[0].detail


def test_syntax_problems_become_their_own_check(tmp_path: Path) -> None:
    checks = doctor.check_env_file(
        envfile.Loaded(path=tmp_path / "envfile", exists=True, problems=("line 3 is bad",))
    )
    assert [c.status for c in checks] == [Status.OK, Status.WARN]
    assert "line 3 is bad" in checks[1].detail


def test_loading_switched_off_is_a_skip_not_a_warning() -> None:
    checks = doctor.check_env_file(envfile.Loaded(path=None))
    assert checks[0].status is Status.SKIP


# ---------- credentials ----------


def test_credentials_are_reported_by_presence_never_by_value() -> None:
    cfg = resolved({"CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-secret"})
    check = doctor.check_credentials(cfg, {"ANTHROPIC_API_KEY": "sk-secret"})
    assert check.status is Status.OK
    assert "sk-secret" not in check.detail


def test_a_missing_key_for_a_selected_provider_fails() -> None:
    check = doctor.check_credentials(resolved({"CHAT_PROVIDER": "openai"}), {})
    assert check.status is Status.FAIL
    assert "OPENAI_API_KEY" in check.detail


def test_local_only_needs_no_credentials() -> None:
    check = doctor.check_credentials(resolved({}), {})
    assert check.status is Status.SKIP


# ---------- reporting ----------


def test_only_a_failure_sets_a_non_zero_exit() -> None:
    """A warning is something to know about, not something that stops the tool
    working, and a doctor that exits non-zero for both cannot be scripted."""
    assert Report([Check("a", Status.WARN), Check("b", Status.OK)]).exit_code == 0
    assert Report([Check("a", Status.FAIL)]).exit_code == 1


def test_output_is_redacted() -> None:
    """It is written to be pasted into an issue, and the DSN it reports carries
    a password."""
    out = io.StringIO()
    doctor.render(Check("DATABASE_URL", Status.OK, "postgres://zeitnot:hunter2@localhost/db"), out)
    assert "hunter2" not in out.getvalue()
    assert "zeitnot:***@" in out.getvalue()


def test_multi_line_details_are_indented_under_their_check() -> None:
    """The existing failure messages are multi-line and are reported as they
    were written, so they have to survive the column layout."""
    out = io.StringIO()
    doctor.render(Check("stockfish", Status.FAIL, "not found\n  install it"), out)
    first, second = out.getvalue().splitlines()
    assert first.startswith("[FAIL] stockfish")
    assert second.strip() == "install it"
    assert second.index("install") > first.index("not found") - 1


@pytest.mark.parametrize("status", list(Status))
def test_every_status_has_a_marker(status: Status) -> None:
    out = io.StringIO()
    doctor.render(Check("x", status), out)
    assert out.getvalue().strip() != ""
