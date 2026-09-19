"""Username case-insensitivity — issue #15.

Chess.com's URL path is case-insensitive but the JSON it returns carries the
player's *registered* capitalization. Every comparison in zeitnot was exact, so
a username typed in any other case fetched games successfully and then
attributed all of them to the wrong player — colour, result, phase statistics,
pattern verdict and termination all derived from a comparison that could not
match, and the wrong summary is what got embedded.

The whole failure is silent. Nothing here asserts an error message; what it
asserts is that three spellings of one name produce *the same* output, which is
the only shape of test that can catch a bug whose symptom is plausible data.
"""

from __future__ import annotations

import dataclasses

import pytest

from zeitnot.models import Game, MoveAnalysis, Player, canonical_username, same_username
from zeitnot.summary import extract_summary_data, generate_summary

# The capitalization Chess.com records, and the two other ways a user might
# reasonably type it: as it is displayed, as it is typed in a hurry, and as it
# comes off a keyboard with caps lock on.
REGISTERED = "Hikaru"
SPELLINGS = ("Hikaru", "hikaru", "HIKARU")

PGN = (
    '[Event "Live Chess"]\n'
    '[ECOUrl "https://www.chess.com/openings/Queens-Gambit-Declined"]\n'
    '[Termination "Gravity_Chess won by resignation"]\n'
    "\n1. d4 d5 2. c4 e6"
)


def _game(registered_is_white: bool = True) -> Game:
    """One game, with our player seated on one side or the other.

    White wins it either way, so which seat the player is found in decides
    whether they won or lost. That is the point: the seat is chosen by the
    username comparison, and everything else follows from it.
    """
    if registered_is_white:
        white = Player(username=REGISTERED, rating=2800, result="win")
        black = Player(username="Gravity_Chess", rating=2400, result="resigned")
    else:
        white = Player(username="Gravity_Chess", rating=2400, result="win")
        black = Player(username=REGISTERED, rating=2800, result="resigned")
    return Game(uuid="u", pgn=PGN, time_class="blitz", white=white, black=black)


def _moves() -> list[MoveAnalysis]:
    return [
        # Ply order, so index parity is the side to move. The two sides are
        # given deliberately different losses: a summary built from the wrong
        # seat is then wrong in its numbers, not only in its label.
        MoveAnalysis(played_move="d2d4", centipawn_loss=10, classification="good"),
        MoveAnalysis(played_move="d7d5", centipawn_loss=300, classification="blunder"),
        MoveAnalysis(played_move="c2c4", centipawn_loss=20, classification="good"),
        MoveAnalysis(played_move="e7e6", centipawn_loss=150, classification="mistake"),
    ]


# ---------- the comparison itself ----------


@pytest.mark.parametrize("typed", SPELLINGS)
def test_any_spelling_names_the_same_account(typed: str) -> None:
    assert same_username(typed, REGISTERED)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("hikaru", "hikaru2"),
        ("hikaru", ""),
        ("hikaru", "ikaru"),
        # Chess.com allows hyphens and underscores, and they are not case.
        ("magnus_c", "magnus-c"),
    ],
)
def test_different_accounts_stay_different(a: str, b: str) -> None:
    assert not same_username(a, b)


def test_surrounding_whitespace_is_not_a_different_account() -> None:
    """A name pasted out of a browser arrives with a trailing space."""
    assert same_username(" Hikaru ", "hikaru")


# ---------- resolving against an archive payload ----------


@pytest.mark.parametrize("typed", SPELLINGS)
def test_the_archive_supplies_the_registered_spelling(typed: str) -> None:
    assert canonical_username(typed, [_game()]) == REGISTERED


@pytest.mark.parametrize("typed", SPELLINGS)
def test_resolution_reads_the_black_seat_too(typed: str) -> None:
    assert canonical_username(typed, [_game(registered_is_white=False)]) == REGISTERED


def test_resolution_skips_games_the_player_is_not_in() -> None:
    """The first game that names the player wins, not the first game."""
    other = Game(
        uuid="other",
        pgn=PGN,
        white=Player(username="Naroditsky"),
        black=Player(username="Gravity_Chess"),
    )
    assert canonical_username("HIKARU", [other, _game()]) == REGISTERED


def test_an_archive_without_the_player_resolves_to_nothing() -> None:
    assert canonical_username("hikaru", []) is None


# ---------- what the bug actually cost: the summary ----------


@pytest.mark.parametrize("typed", SPELLINGS)
def test_every_spelling_summarizes_from_the_same_side_of_the_board(typed: str) -> None:
    """The regression in one assertion.

    Before the fix, `hikaru` and `HIKARU` produced `color=black`, `result=lost`
    and the opponent's CPL, because `username == game.white.username` could not
    match. Nothing errored; the summary was simply about the other player.
    """
    reference = extract_summary_data(_game(), _moves(), REGISTERED)
    assert dataclasses.asdict(extract_summary_data(_game(), _moves(), typed)) == (
        dataclasses.asdict(reference)
    )


@pytest.mark.parametrize("typed", SPELLINGS)
def test_the_embedded_text_does_not_depend_on_how_the_name_was_typed(typed: str) -> None:
    """`summary_text` *is* the embedded text, so a divergence here is a
    divergence in the vector, which nothing downstream can detect."""
    want = generate_summary(extract_summary_data(_game(), _moves(), REGISTERED))
    assert generate_summary(extract_summary_data(_game(), _moves(), typed)) == want


def test_the_player_is_white_when_the_registered_name_is_white() -> None:
    """Guards the direction of the fix: case-insensitive, not always-Black.

    `extract_summary_data` picks Black as its `else`, exactly as
    `weakest_phase` picks the endgame, so a comparison that never matches is
    indistinguishable from one that always resolves to Black. These assert the
    values rather than only their agreement.
    """
    data = extract_summary_data(_game(), _moves(), "HIKARU")
    assert data.player_color == "white"
    assert data.result == "won"

    flipped = extract_summary_data(_game(registered_is_white=False), _moves(), "HIKARU")
    assert flipped.player_color == "black"
    assert flipped.result == "lost"
