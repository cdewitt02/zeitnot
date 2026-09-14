"""Structured filters for hybrid search, and the WHERE clause they build."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class GameFilters:
    """Structured filters for hybrid search. `None` means "no filter"."""

    # String exact matches
    result: str | None = None  # "win", "loss", "draw"
    user_color: str | None = None  # "white", "black"
    time_class: str | None = None  # "bullet", "blitz", "rapid"
    weak_phase: str | None = None  # "opening", "middlegame", "endgame"

    # Pattern matches
    eco_prefix: str | None = None  # "B" for Sicilian, "E" for King's Indian
    opening_name: str | None = None  # partial match: "Sicilian", "King's Indian"

    # Ranges (inclusive)
    min_blunders: int | None = None
    max_blunders: int | None = None
    min_mistakes: int | None = None
    min_rating: int | None = None  # opponent rating
    max_rating: int | None = None

    # Time ranges
    date_from: datetime | None = None
    date_to: datetime | None = None

    # Always applied (not optional)
    username: str = ""

    def build_where(self) -> FilterResult:
        """Construct the WHERE clause and its arguments.

        The Go version threaded a parameter *index* (`startParam`, `paramNum`)
        through every fragment, so filters could be appended to a query that
        already had parameters. psycopg's positional `%s` has no indices —
        ordering is implicit in the argument sequence — so that machinery is
        gone rather than reproduced. The result is a clause plus an ordered
        argument list and nothing else.

        This is parity measured on outputs rather than structure: what has to
        match is the result set, which the corpus test asserts directly. A
        faithful port would have carried dead complexity forever, in the one
        function ADR 0001 singled out as load-bearing.
        """
        conditions: list[str] = []
        args: list[Any] = []

        def add(condition: str, arg: Any) -> None:
            conditions.append(condition)
            args.append(arg)

        if self.username:
            conditions.append("(g.white_username = %s OR g.black_username = %s)")
            # One placeholder per argument, so a value used twice is passed
            # twice. Under $N the same index could be reused; positional cannot,
            # which is why the arg counts here differ from the Go tests'.
            args.extend([self.username, self.username])

        if self.result is not None:
            if self.user_color is not None:
                # With the color known, the player-relative result translates
                # straight into the stored one.
                db_result = ""
                if self.result == "win":
                    db_result = self.user_color
                elif self.result == "loss":
                    db_result = "black" if self.user_color == "white" else "white"
                elif self.result == "draw":
                    db_result = "draw"
                add("g.result = %s", db_result)
            elif self.result == "win":
                conditions.append(
                    "((g.white_username = %s AND g.result = 'white') "
                    "OR (g.black_username = %s AND g.result = 'black'))"
                )
                args.extend([self.username, self.username])
            elif self.result == "loss":
                conditions.append(
                    "((g.white_username = %s AND g.result = 'black') "
                    "OR (g.black_username = %s AND g.result = 'white'))"
                )
                args.extend([self.username, self.username])
            elif self.result == "draw":
                add("g.result = %s", "draw")

        if self.user_color == "white":
            add("g.white_username = %s", self.username)
        elif self.user_color == "black":
            add("g.black_username = %s", self.username)

        if self.time_class is not None:
            add("g.time_class = %s", self.time_class)

        if self.eco_prefix is not None:
            # The % wildcard lives in the argument, never in the SQL text, so
            # psycopg's "double a literal %" rule never applies here.
            add("g.eco_code LIKE %s", self.eco_prefix + "%")

        if self.opening_name is not None:
            add("LOWER(g.eco_name) LIKE %s", "%" + self.opening_name.lower() + "%")

        if self.min_blunders is not None or self.max_blunders is not None:
            blunder_expr = (
                "CASE WHEN g.white_username = %s THEN g.blunders_white ELSE g.blunders_black END"
            )
            if self.min_blunders is not None:
                conditions.append(f"({blunder_expr}) >= %s")
                args.extend([self.username, self.min_blunders])
            if self.max_blunders is not None:
                conditions.append(f"({blunder_expr}) <= %s")
                args.extend([self.username, self.max_blunders])

        if self.min_mistakes is not None:
            mistake_expr = (
                "CASE WHEN g.white_username = %s THEN g.mistakes_white ELSE g.mistakes_black END"
            )
            conditions.append(f"({mistake_expr}) >= %s")
            args.extend([self.username, self.min_mistakes])

        if self.min_rating is not None or self.max_rating is not None:
            # The opponent's rating is the one opposite the player's color.
            rating_expr = (
                "CASE WHEN g.white_username = %s THEN g.black_rating ELSE g.white_rating END"
            )
            if self.min_rating is not None:
                conditions.append(f"({rating_expr}) >= %s")
                args.extend([self.username, self.min_rating])
            if self.max_rating is not None:
                conditions.append(f"({rating_expr}) <= %s")
                args.extend([self.username, self.max_rating])

        if self.date_from is not None:
            add("g.created_at >= %s", self.date_from)
        if self.date_to is not None:
            add("g.created_at <= %s", self.date_to)

        return FilterResult(clause=" AND ".join(conditions), args=args)

    def is_empty(self) -> bool:
        """True when no filter but the username is set."""
        return all(
            value is None
            for value in (
                self.result,
                self.user_color,
                self.time_class,
                self.weak_phase,
                self.eco_prefix,
                self.opening_name,
                self.min_blunders,
                self.max_blunders,
                self.min_mistakes,
                self.min_rating,
                self.max_rating,
                self.date_from,
                self.date_to,
            )
        )

    def clone(self) -> GameFilters:
        """A copy.

        Every field is a str, int, or datetime — all immutable — so a shallow
        copy is the deep copy the Go version had to spell out field by field
        because it held pointers.
        """
        return replace(self)

    def __str__(self) -> str:
        parts: list[str] = []
        if self.result is not None:
            parts.append(f"result={self.result}")
        if self.user_color is not None:
            parts.append(f"color={self.user_color}")
        if self.time_class is not None:
            parts.append(f"time={self.time_class}")
        if self.weak_phase is not None:
            parts.append(f"phase={self.weak_phase}")
        if self.eco_prefix is not None:
            parts.append(f"eco={self.eco_prefix}*")
        if self.opening_name is not None:
            parts.append(f"opening~{self.opening_name}")
        if self.min_blunders is not None:
            parts.append(f"blunders>={self.min_blunders}")
        if self.max_blunders is not None:
            parts.append(f"blunders<={self.max_blunders}")
        if self.min_mistakes is not None:
            parts.append(f"mistakes>={self.min_mistakes}")
        if self.min_rating is not None:
            parts.append(f"opponent>={self.min_rating}")
        if self.max_rating is not None:
            parts.append(f"opponent<={self.max_rating}")
        if self.date_from is not None:
            parts.append(f"from={self.date_from.strftime('%Y-%m-%d')}")
        if self.date_to is not None:
            parts.append(f"to={self.date_to.strftime('%Y-%m-%d')}")
        if not parts:
            return "no filters"
        return ", ".join(parts)


@dataclass(slots=True)
class FilterResult:
    clause: str
    args: list[Any] = field(default_factory=list)
