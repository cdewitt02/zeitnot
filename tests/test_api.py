"""Ported from internal/api/data_test.go.

The defect these exist for: every error status used to unmarshal into an empty
game list and return a nil error, so the pipeline reported success on every
failure. That is this project's characteristic bug — silence — and it is what
the whole file is arranged around.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager

import pytest
import responses

from zeitnot import api
from zeitnot.api import USER_AGENT, ChessComError, get_data
from zeitnot.models import YearMonth

TEST_DATE = YearMonth(year=2026, month="01")
BASE = "http://chesscom.test/pub"
URL = f"{BASE}/player/magnus/games/2026/01"


@contextmanager
def serve() -> Generator[responses.RequestsMock, None, None]:
    """Point the client at a fixture server for the duration of one test."""
    previous = api.BASE_URL
    api.BASE_URL = BASE
    try:
        with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
            yield mock
    finally:
        api.BASE_URL = previous


@pytest.mark.parametrize(
    ("status", "body", "want_text"),
    [
        pytest.param(
            404,
            '{"code":0,"message":"User not found."}',
            ["magnus", "404", "misspelled", "no games archived"],
            # A 404 cannot tell these apart, so the message names both rather
            # than sending the user after a username that may be correct.
            id="404 names both causes it cannot distinguish",
        ),
        pytest.param(429, "", ["rate limited", "429"], id="429 says how to recover"),
        pytest.param(
            403,
            "Forbidden",
            ["403", "zeitnot/0.1"],
            # A client that sends no User-Agent is known to be blocked —
            # reachable on a first run with a perfectly valid username.
            id="403 mentions the client identity",
        ),
        pytest.param(
            500,
            "upstream exploded",
            ["500", "upstream exploded"],
            id="other statuses carry the code and body",
        ),
    ],
)
def test_an_error_status_is_never_silent_success(
    status: int, body: str, want_text: list[str]
) -> None:
    with serve() as mock:
        mock.add(responses.GET, URL, body=body, status=status)
        with pytest.raises(ChessComError) as excinfo:
            get_data(TEST_DATE, "magnus")

    message = str(excinfo.value)
    for want in want_text:
        assert want in message, f"error = {message!r}, want it to mention {want!r}"


def test_retry_after_is_surfaced() -> None:
    with serve() as mock:
        mock.add(responses.GET, URL, body="", status=429, headers={"Retry-After": "60"})
        with pytest.raises(ChessComError) as excinfo:
            get_data(TEST_DATE, "magnus")
    assert "60" in str(excinfo.value)


def test_sends_a_user_agent() -> None:
    """Chess.com rejects clients that do not identify themselves."""
    with serve() as mock:
        mock.add(responses.GET, URL, json={"games": []}, status=200)
        get_data(TEST_DATE, "magnus")
        sent = mock.calls[0].request.headers.get("User-Agent", "")

    assert sent == USER_AGENT
    assert "python-requests" not in sent, (
        "requests still go out as the default client, which Chess.com blocks"
    )


def test_requests_the_month_path() -> None:
    with serve() as mock:
        mock.add(responses.GET, URL, json={"games": []}, status=200)
        get_data(TEST_DATE, "magnus")
        path = mock.calls[0].request.path_url

    # The month keeps its leading zero, which is why YearMonth.month is a string
    # rather than an int.
    assert path == "/pub/player/magnus/games/2026/01"


def test_an_empty_month_is_not_an_error() -> None:
    """A month with no games is a real, successful answer — it must stay
    distinguishable from a failure."""
    with serve() as mock:
        mock.add(responses.GET, URL, body='{"games":[]}', status=200)
        assert get_data(TEST_DATE, "magnus") == []


def test_success_decodes_games() -> None:
    with serve() as mock:
        mock.add(
            responses.GET,
            URL,
            body=json.dumps(
                {
                    "games": [
                        {"uuid": "abc", "url": "https://chess.com/game/1"},
                        {"uuid": "def", "url": "https://chess.com/game/2"},
                    ]
                }
            ),
            status=200,
        )
        games = get_data(TEST_DATE, "magnus")

    assert [g.uuid for g in games] == ["abc", "def"]
    assert games[0].url == "https://chess.com/game/1"


def test_a_malformed_body_is_an_error() -> None:
    """A 200 carrying something other than the expected JSON must fail loudly,
    not decode into an empty list."""
    with serve() as mock:
        mock.add(responses.GET, URL, body="<html>maintenance</html>", status=200)
        with pytest.raises(ChessComError) as excinfo:
            get_data(TEST_DATE, "magnus")
    assert "maintenance" in str(excinfo.value)


def test_a_json_body_that_is_not_an_object_is_an_error() -> None:
    """Valid JSON of the wrong shape.

    Go's decoder would have accepted a bare array into a struct only by
    erroring; Python's json accepts it as a list and `.get` would then raise an
    AttributeError deep in the pipeline instead of here.
    """
    with serve() as mock:
        mock.add(responses.GET, URL, body="[1, 2, 3]", status=200)
        with pytest.raises(ChessComError) as excinfo:
            get_data(TEST_DATE, "magnus")
    assert "expected an object" in str(excinfo.value)


def test_a_transport_failure_names_the_network() -> None:
    """A dial failure is not a Chess.com error, and the message should not send
    the user to check their username."""
    import requests

    with serve() as mock:
        mock.add(responses.GET, URL, body=requests.ConnectionError("no route to host"))
        with pytest.raises(ChessComError) as excinfo:
            get_data(TEST_DATE, "magnus")
    assert "network connection" in str(excinfo.value)
