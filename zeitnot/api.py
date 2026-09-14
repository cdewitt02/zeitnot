"""The Chess.com public API client."""

from __future__ import annotations

import requests

from zeitnot.models import Game, YearMonth

# A module attribute so tests can point it at a fixture server. Nothing else
# reassigns it.
BASE_URL = "https://api.chess.com/pub"

# Identifies zeitnot to Chess.com. Requests without one go out as the HTTP
# library's default, which the public API is known to reject or throttle — a 403
# on a new user's very first run with a perfectly valid username. The API docs
# ask for contact information, which the repository URL provides.
USER_AGENT = "zeitnot/0.1 (+https://github.com/cdewitt02/zeitnot)"

# An explicit timeout. Without one a hung connection hangs the whole ingestion
# pipeline indefinitely.
TIMEOUT = 30.0


class ChessComError(Exception):
    """A Chess.com request that did not produce games."""


def get_data(date: YearMonth, username: str) -> list[Game]:
    """Fetch one month of games for a player.

    **Every non-2xx status is an error.** This matters more than it looks: an
    error body parses cleanly into a payload with no "games" key, so returning
    it as an empty list made the program print "Fetched 0 games" and exit 0 on
    every failure.
    """
    url = f"{BASE_URL}/player/{username}/games/{date.year}/{date.month}"

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as err:
        raise ChessComError(
            f"cannot reach the Chess.com API: {err} (check your network connection)"
        ) from err

    if not 200 <= response.status_code < 300:
        raise ChessComError(_status_message(response, date, username))

    try:
        payload = response.json()
    except ValueError as err:
        raise ChessComError(
            f"Chess.com returned a body that is not the expected JSON: {err}: "
            f"{_snippet(response.text)}"
        ) from err

    if not isinstance(payload, dict):
        raise ChessComError(
            f"Chess.com returned a body that is not the expected JSON: "
            f"expected an object, got {type(payload).__name__}: {_snippet(response.text)}"
        )

    return [Game.from_json(raw) for raw in payload.get("games", [])]


def _status_message(response: requests.Response, date: YearMonth, username: str) -> str:
    """Turn a status code into a message that names the remedy.

    Each class here has a different one, and the user is usually about to spend
    minutes on Stockfish analysis.
    """
    body = _snippet(response.text)

    if response.status_code == 404:
        # A 404 does not distinguish these, so the message must not either:
        # asserting one cause sent a user hunting a username that was correct.
        return (
            f'no games found for user "{username}" in {date.year}/{date.month}: '
            "Chess.com returned 404 — either the username is misspelled, or that "
            "player has no games archived for that month"
        )
    if response.status_code == 429:
        retry_after = (response.headers.get("Retry-After") or "").strip()
        if retry_after:
            return (
                f"rate limited by Chess.com (429); retry after {retry_after} seconds, "
                "or ingest fewer months at a time"
            )
        return (
            "rate limited by Chess.com (429); wait a minute and retry, "
            "or ingest fewer months at a time"
        )
    if response.status_code == 403:
        return (
            "Chess.com refused the request (403). This usually means the client was "
            f'blocked; zeitnot identifies itself as "{USER_AGENT}". Response: {body}'
        )
    return f"Chess.com returned status {response.status_code}: {body}"


def _snippet(body: str) -> str:
    body = body.strip()
    if not body:
        return "(empty body)"
    return body[:300] + "..." if len(body) > 300 else body
