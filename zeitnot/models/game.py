"""The Chess.com game payload and the fields zeitnot derives from it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_PGN_HEADER = re.compile(r'\[(\w+) "([^"]*)"\]')
_OPENING_URL = re.compile(r"/openings/([^/]+)")
# Matches "-4." or "..." — the point where a variation's move list begins.
_VARIATION_SPLIT = re.compile(r"(-\d+\.|\.\.\.)")


@dataclass(slots=True)
class Player:
    uuid: str = ""
    username: str = ""
    rating: int = 0
    result: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Player:
        return cls(
            uuid=str(raw.get("uuid", "")),
            username=str(raw.get("username", "")),
            rating=int(raw.get("rating", 0) or 0),
            result=str(raw.get("result", "")),
        )


@dataclass(slots=True)
class Game:
    uuid: str = ""
    url: str = ""
    pgn: str = ""
    time_control: str = ""
    end_time: int = 0
    rated: bool = False
    accuracies: dict[str, float] = field(default_factory=dict[str, float])
    tcn: str = ""
    initial_setup: str = ""
    fen: str = ""
    time_class: str = ""
    # eco is the Chess.com *openings URL*, not the ECO code. The code lives in
    # the PGN header; this field is what opening_name() parses.
    eco: str = ""
    white: Player = field(default_factory=Player)
    black: Player = field(default_factory=Player)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Game:
        return cls(
            uuid=str(raw.get("uuid", "")),
            url=str(raw.get("url", "")),
            pgn=str(raw.get("pgn", "")),
            time_control=str(raw.get("time_control", "")),
            end_time=int(raw.get("end_time", 0) or 0),
            rated=bool(raw.get("rated", False)),
            accuracies={k: float(v) for k, v in (raw.get("accuracies") or {}).items()},
            tcn=str(raw.get("tcn", "")),
            initial_setup=str(raw.get("initial_setup", "")),
            fen=str(raw.get("fen", "")),
            time_class=str(raw.get("time_class", "")),
            eco=str(raw.get("eco", "")),
            white=Player.from_json(raw.get("white") or {}),
            black=Player.from_json(raw.get("black") or {}),
        )

    def game_result(self) -> str:
        if self.white.result == "win":
            return "white"
        if self.black.result == "win":
            return "black"
        return "draw"

    def _pgn_header(self, name: str) -> str:
        for key, value in _PGN_HEADER.findall(self.pgn):
            if key == name:
                return str(value)
        return ""

    def eco_code(self) -> str:
        return self._pgn_header("ECO")

    def opening_name(self) -> str:
        """Extract the opening name from the Chess.com openings URL.

        "https://www.chess.com/openings/Pirc-Defense-Main-Line-Kholmov-System-4...Bg7"
        becomes "Pirc Defense Main Line Kholmov System".
        """
        match = _OPENING_URL.search(self.eco)
        if match is None:
            return ""
        # Go's regexp.Split with n=2 keeps the capture group out of the result
        # and yields at most two pieces; re.split keeps the group, so take the
        # first piece either way.
        name = _VARIATION_SPLIT.split(match.group(1), maxsplit=1)[0]
        return name.replace("-", " ")

    def termination_type(self) -> str:
        return self._pgn_header("Termination")


# Chess.com's termination strings embed the winner's username — "Bolzman0 won by
# resignation". Three things follow, and only the first is obvious:
#
#   1. Disclosure. That handle belongs to a third party who never chose this
#      tool, and it reaches a hosted Chat Provider verbatim.
#   2. It never aggregates. Because the username is part of the key, every
#      opponent forms their own bucket: 90 distinct values across 195 games,
#      nearly all of them "1 (0.5%)".
#   3. It carries no coaching signal. *How* the game ended is useful. *Who* it
#      was against is not, in an aggregate.
#
# Draws are already anonymous ("Game drawn by agreement"); only decisive results
# name a player.

_WON_CONNECTORS = (
    (" won by ", "by"),
    (" won on ", "on"),
    (" won - game ", "by"),
)
_DRAWN_CONNECTOR = "drawn by "


def normalize_termination(termination: str, result: str) -> str:
    """Reduce a termination string to (outcome, method) from the player's side.

    `result` is the player's own outcome — "won", "lost", or "drew" — so the key
    is what happened to *them*, not who the winner was. "Bolzman0 won by
    resignation" becomes "lost by resignation" for the player who resigned and
    "won by resignation" for the one who did not.

    Anything unrecognized collapses to "<result> by other means" rather than
    passing through. That is deliberate: an unparsed string is exactly the case
    where a username might still be embedded, so the fallback must not echo it.
    """
    raw = termination.strip()
    if not raw:
        return ""

    method = ""
    verb = "by"
    lowered = raw.lower()

    for connector, connector_verb in _WON_CONNECTORS:
        index = lowered.find(connector)
        if index != -1:
            method = raw[index + len(connector) :].strip().lower()
            verb = connector_verb
            break
    else:
        index = lowered.find(_DRAWN_CONNECTOR)
        if index != -1:
            method = raw[index + len(_DRAWN_CONNECTOR) :].strip().lower()

    if not method:
        return f"{result} by other means"

    # "won - game abandoned" reads as a method of "abandoned"; name it as one.
    if method == "abandoned":
        method = "abandonment"

    outcome = "drawn" if result == "drew" else result
    return f"{outcome} {verb} {method}"


# What `normalize_termination` can produce, as a prefix set. Chess.com's raw
# strings lead with the *winner's username*, so anything not starting here is
# either raw or from some future format — and both may carry a handle.
#
# "drew " is here as well as "drawn ": a draw whose method parsed becomes "drawn
# by agreement", but the unparsed fallback is `f"{result} by other means"` and
# `result` is "drew". Both are outputs, so both are normalized.
_NORMALIZED_PREFIXES = ("won ", "lost ", "drawn ", "drew ")


def is_normalized_termination(value: str) -> bool:
    """Whether a stored termination has been through `normalize_termination`.

    Read-side companion to it, for the aggregates. Normalization happens when a
    row is written, so a `player_stats` row computed before that landed still
    holds raw Chess.com strings — "2DBEACH won by resignation" — and nothing
    downstream could tell. Any consumer that puts these keys somewhere they must
    not leak has to ask first; see `zeitnot.chat.router`.
    """
    return value.strip().lower().startswith(_NORMALIZED_PREFIXES)


# Chess.com's URL path is case-insensitive — /pub/player/HIKARU and
# /pub/player/hikaru are the same archive — but the JSON it returns carries the
# player's *registered* capitalization on every game. Comparing what the user
# typed against that payload with `==` therefore fails for every spelling but
# one, and it fails in the worst possible way: not with an error, but with the
# player never being found to be White. Colour, result, phase statistics,
# pattern verdict and termination are all derived from that one comparison, so
# an archive fetched under the wrong case is summarized entirely from the wrong
# side of the board and embedded that way.
#
# `lower()` rather than `casefold()` on purpose: the same comparison is made in
# SQL by `LOWER()`, and the two have to agree on every input. Chess.com
# usernames are ASCII letters, digits, underscore and hyphen, where the two
# functions are identical anyway.


def same_username(a: str, b: str) -> bool:
    """Whether two spellings name the same Chess.com account.

    Matching Chess.com's own semantics rather than being lenient to input: the
    API treats these as one account, so anything that treats them as two is
    describing a player who does not exist.
    """
    return a.strip().lower() == b.strip().lower()


def canonical_username(typed: str, games: Sequence[Game]) -> str | None:
    """The registered spelling of `typed`, read off an archive payload.

    The archive is the authority on how a name is spelled, and it states it on
    every game. Resolving once against it lets every comparison downstream — in
    Python and in SQL — stay an exact match, which is both faster (the
    `games(white_username)` index stays usable) and easier to reason about than
    a case-insensitive predicate repeated at thirty call sites.

    `None` when no game in `games` names the player, which for a real archive
    means there were no games at all.
    """
    for game in games:
        if same_username(typed, game.white.username):
            return game.white.username
        if same_username(typed, game.black.username):
            return game.black.username
    return None
