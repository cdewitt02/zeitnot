"""The one table of opening names.

Two things need to know what an opening is called, and they used to hold
separate lists that drifted apart: `zeitnot/chat/classifier.py` decided which
openings a question *names* (36 entries), and `zeitnot/search/parser.py` decided
which games it *retrieves* (37 entries). They disagreed on 37 keys — the
classifier knew `vienna`, `scotch` and `slav`, the parser knew `najdorf`, `qgd`
and `dragon` — and neither knew the Danish Gambit, so a question comparing it to
the Caro-Kann produced a prompt with Caro-Kann stats, no Danish stats, and
nothing saying the second half was missing. Both consumers now derive from
`OPENINGS`; splitting the table again is how that bug comes back (#41).

An `eco_prefix` is matched with `LIKE prefix || '%'` against `games.eco_code`,
so it is as specific as the opening is: `B1` is every Caro-Kann, `D00` is the
London alone. `aliases` are matched as substrings of the lowercased question,
which is why they are spellings rather than names — both `caro-kann` and
`caro kann`, because people type both.

**Aliases are compared against Chess.com's opening names, which have no
hyphens.** `Game.opening_name` parses them out of a URL and replaces `-` with a
space, so `Caro-Kann Defense Advance Bayonet Attack` is stored as
`Caro Kann Defense Advance Bayonet Attack`. Anything comparing an alias or a
`name` against stored text goes through `normalize` first; a bare `in` silently
reports zero games for the standard spelling of a hyphenated opening.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Opening:
    """One opening: what to call it, where its games are, and how people spell it.

    `aliases` is ordered shortest-first by convention, because the two consumers
    want opposite ends of it — the parser takes the longest match, so that
    `sicilian najdorf` selects the Najdorf rather than the Sicilian it contains,
    and the classifier takes the shortest, because the root spelling is the one
    that matches the most stored opening names.
    """

    name: str
    eco_prefix: str
    aliases: tuple[str, ...]


def normalize(text: str) -> str:
    """Fold a name or alias to the form Chess.com's opening names are stored in.

    Lowercased, with hyphens becoming spaces and apostrophes dropped, so that
    `Caro-Kann`, `caro kann` and the stored `Caro Kann Defense Exchange
    Variation` all meet on the same string — and so do `Queen's Gambit` and the
    stored `Queens Gambit Declined`. The two marks are not treated alike because
    Chess.com does not treat them alike: its opening names write a hyphen as a
    space and an apostrophe as nothing at all.
    """
    folded = _APOSTROPHE.sub("", text.lower().replace("-", " "))
    return " ".join(folded.split())

_APOSTROPHE = re.compile(r"['\u2019]")

OPENINGS: tuple[Opening, ...] = (
    # --- 1.e4 e5, open games (C2x-C5x) ---
    Opening("King's Pawn Game", "C20", ("kings pawn game", "king's pawn game")),
    Opening("Bongcloud", "C20", ("bongcloud",)),
    Opening("Danish Gambit", "C21", ("danish",)),
    Opening("Center Game", "C22", ("center game", "centre game")),
    Opening("Bishop's Opening", "C23", ("bishops opening", "bishop's opening")),
    Opening("Vienna", "C2", ("vienna",)),
    Opening("Frankenstein-Dracula", "C27", ("frankenstein", "dracula")),
    Opening("Vienna Gambit", "C29", ("vienna gambit",)),
    Opening("King's Gambit", "C3", ("kings gambit", "king's gambit")),
    Opening("King's Gambit Declined", "C30", ("kgd", "kings gambit declined", "king's gambit declined")),
    Opening("Falkbeer", "C31", ("falkbeer",)),
    Opening("King's Gambit Accepted", "C33", ("kga", "kings gambit accepted", "king's gambit accepted")),
    Opening("Muzio Gambit", "C37", ("muzio",)),
    Opening("Latvian Gambit", "C40", ("latvian",)),
    Opening("Elephant Gambit", "C40", ("elephant gambit",)),
    Opening("Damiano Defense", "C40", ("damiano",)),
    Opening("Philidor", "C41", ("philidor",)),
    Opening("Petrov", "C42", ("petrov", "petroff", "russian game")),
    Opening("Stafford Gambit", "C42", ("stafford",)),
    Opening("Ponziani", "C44", ("ponziani",)),
    Opening("Goring Gambit", "C44", ("goring", "göring")),
    Opening("Scotch Gambit", "C44", ("scotch gambit",)),
    Opening("Scotch", "C45", ("scotch",)),
    Opening("Four Knights", "C46", ("four knights",)),
    Opening("Italian", "C5", ("italian", "italian game", "giuoco piano")),
    Opening("Hungarian Defense", "C50", ("hungarian",)),
    Opening("Giuoco Pianissimo", "C50", ("pianissimo",)),
    Opening("Evans Gambit", "C51", ("evans gambit", "evan's gambit")),
    Opening("Two Knights", "C55", ("two knights",)),
    Opening("Fried Liver Attack", "C57", ("fried liver",)),
    Opening("Traxler Counterattack", "C57", ("traxler", "wilkes-barre", "wilkes barre")),
    Opening("Ruy Lopez", "C6", ("spanish", "ruy lopez", "spanish game")),
    Opening("Schliemann", "C63", ("schliemann", "jaenisch gambit")),
    Opening("Berlin", "C65", ("berlin",)),
    Opening("Ruy Lopez Exchange", "C68", ("ruy lopez exchange", "spanish exchange")),
    Opening("Arkhangelsk", "C78", ("arkhangelsk", "archangel")),
    Opening("Open Ruy Lopez", "C80", ("open ruy lopez", "open spanish")),
    Opening("Closed Ruy Lopez", "C84", ("closed ruy lopez", "closed spanish")),
    Opening("Marshall Attack", "C89", ("marshall",)),
    Opening("Zaitsev", "C92", ("zaitsev",)),
    Opening("Breyer", "C94", ("breyer",)),
    # --- 1.e4, semi-open defenses (B0x-B1x, C0x-C1x) ---
    Opening("Owen's Defense", "B00", ("owens defense", "owen's defense")),
    Opening("Nimzowitsch Defense", "B00", ("nimzowitsch defense",)),
    Opening("St. George Defense", "B00", ("st george", "st. george")),
    Opening("Borg Defense", "B00", ("borg defense",)),
    Opening("Scandinavian", "B01", ("scandinavian", "center counter")),
    Opening("Portuguese Gambit", "B01", ("portuguese",)),
    Opening("Icelandic Gambit", "B01", ("icelandic",)),
    Opening("Alekhine", "B0", ("alekhine",)),
    Opening("Modern", "B06", ("modern defense",)),
    Opening("Pirc", "B0", ("pirc", "pirc defense")),
    Opening("150 Attack", "B07", ("150 attack",)),
    Opening("Austrian Attack", "B09", ("austrian attack",)),
    Opening("Caro-Kann", "B1", ("caro kann", "caro-kann")),
    Opening("Caro-Kann Two Knights", "B11", ("caro kann two knights", "caro-kann two knights")),
    Opening("Caro-Kann Advance", "B12", ("caro kann advance", "caro-kann advance")),
    Opening("Fantasy Variation", "B12", ("fantasy variation", "caro kann fantasy", "caro-kann fantasy")),
    Opening("Panov-Botvinnik", "B13", ("panov", "panov-botvinnik", "panov botvinnik")),
    Opening("French", "C0", ("french", "french defense", "french defence")),
    Opening("French Exchange", "C01", ("french exchange",)),
    Opening("French Advance", "C02", ("french advance",)),
    Opening("French Tarrasch", "C03", ("french tarrasch",)),
    Opening("French Rubinstein", "C10", ("french rubinstein", "rubinstein french")),
    Opening("MacCutcheon", "C12", ("maccutcheon", "mccutcheon")),
    Opening("Winawer", "C15", ("winawer",)),
    # --- 1.e4 c5, Sicilian (B2x-B9x) ---
    Opening("Sicilian", "B", ("sicilian",)),
    Opening("Wing Gambit", "B20", ("wing gambit",)),
    Opening("Smith-Morra", "B21", ("smith morra", "smith-morra", "morra gambit")),
    Opening("Alapin", "B22", ("alapin",)),
    Opening("Grand Prix Attack", "B23", ("grand prix",)),
    Opening("Closed Sicilian", "B23", ("closed sicilian",)),
    Opening("Hyperaccelerated Dragon", "B27", ("hyperaccelerated", "hyper accelerated", "hyper-accelerated")),
    Opening("Rossolimo", "B31", ("rossolimo",)),
    Opening("Kalashnikov", "B32", ("kalashnikov",)),
    Opening("Sveshnikov", "B33", ("sveshnikov",)),
    Opening("Accelerated Dragon", "B34", ("accelerated dragon",)),
    Opening("Kan", "B41", ("sicilian kan", "kan variation", "paulsen")),
    Opening("Taimanov", "B44", ("taimanov",)),
    Opening("Sicilian Four Knights", "B45", ("sicilian four knights",)),
    Opening("Moscow Variation", "B51", ("moscow variation",)),
    Opening("Sozin", "B57", ("sozin",)),
    Opening("Richter-Rauzer", "B6", ("richter rauzer", "richter-rauzer")),
    Opening("Dragon", "B7", ("dragon", "sicilian dragon")),
    Opening("Yugoslav Attack", "B76", ("yugoslav attack",)),
    Opening("Scheveningen", "B8", ("scheveningen",)),
    Opening("Najdorf", "B9", ("najdorf", "sicilian najdorf")),
    # --- 1.d4 d5, Queen's Gambit and relatives (D0x-D6x) ---
    Opening("Queen's Pawn", "D00", ("queens pawn", "queen's pawn")),
    Opening("Blackmar-Diemer", "D00", ("blackmar", "blackmar-diemer")),
    Opening("London", "D00", ("london", "london system")),
    Opening("Barry Attack", "D00", ("barry attack",)),
    Opening("Veresov", "D01", ("veresov",)),
    Opening("Jobava London", "D01", ("jobava",)),
    Opening("Colle", "D05", ("colle",)),
    Opening("Stonewall Attack", "D05", ("stonewall attack",)),
    Opening("Baltic Defense", "D06", ("baltic",)),
    Opening("Chigorin", "D07", ("chigorin",)),
    Opening("Albin", "D08", ("albin",)),
    Opening("Slav", "D1", ("slav",)),
    Opening("Exchange Slav", "D13", ("slav exchange", "exchange slav")),
    Opening("Chebanenko Slav", "D15", ("chebanenko",)),
    Opening("Queen's Gambit Accepted", "D2", ("qga",)),
    Opening("Queen's Gambit", "D", ("qgd", "queens gambit", "queen's gambit")),
    Opening("Tarrasch", "D32", ("tarrasch",)),
    Opening("QGD Exchange", "D35", ("qgd exchange", "queens gambit exchange", "queen's gambit exchange")),
    Opening("Ragozin", "D38", ("ragozin",)),
    Opening("Semi-Slav", "D4", ("semi slav", "semi-slav")),
    Opening("Semi-Tarrasch", "D40", ("semi tarrasch", "semi-tarrasch")),
    Opening("Botvinnik Semi-Slav", "D44", ("botvinnik variation", "botvinnik semi slav", "botvinnik semi-slav")),
    Opening("Meran", "D46", ("meran",)),
    Opening("Cambridge Springs", "D52", ("cambridge springs",)),
    Opening("Lasker Defense", "D56", ("lasker defense", "lasker defence")),
    Opening("Tartakower", "D58", ("tartakower",)),
    Opening("Orthodox Defense", "D6", ("orthodox",)),
    Opening("Grunfeld", "D7", ("grunfeld", "grünfeld")),
    Opening("Grunfeld Exchange", "D85", ("grunfeld exchange", "grünfeld exchange")),
    Opening("Russian System", "D96", ("russian system",)),
    # --- 1.d4 Nf6, Indian defenses (A4x-A6x, E0x-E9x) ---
    Opening("Englund Gambit", "A40", ("englund",)),
    Opening("Old Benoni", "A43", ("old benoni",)),
    Opening("Trompowsky", "A45", ("trompowsky",)),
    Opening("Torre Attack", "A46", ("torre",)),
    Opening("Fajarowicz", "A51", ("fajarowicz",)),
    Opening("Budapest", "A52", ("budapest",)),
    Opening("Old Indian", "A53", ("old indian",)),
    Opening("Czech Benoni", "A56", ("czech benoni",)),
    Opening("Benoni", "A6", ("benoni",)),
    Opening("Benko Gambit", "A57", ("benko", "volga")),
    Opening("Dutch", "A8", ("dutch", "dutch defense")),
    Opening("Staunton Gambit", "A82", ("staunton gambit",)),
    Opening("Leningrad Dutch", "A87", ("leningrad",)),
    Opening("Stonewall", "A90", ("stonewall",)),
    Opening("Catalan", "E0", ("catalan",)),
    Opening("Open Catalan", "E04", ("open catalan",)),
    Opening("Closed Catalan", "E06", ("closed catalan",)),
    Opening("Blumenfeld Gambit", "E10", ("blumenfeld",)),
    Opening("Bogo-Indian", "E11", ("bogo",)),
    Opening("Queen's Indian", "E1", ("queens indian", "queen's indian")),
    Opening("Nimzo-Indian", "E", ("nimzo", "nimzo indian", "nimzo-indian")),
    Opening("Nimzo-Indian Rubinstein", "E4", ("nimzo rubinstein", "nimzo-indian rubinstein")),
    Opening("King's Indian", "E", ("kid", "kings indian", "king's indian")),
    Opening("Averbakh", "E73", ("averbakh",)),
    Opening("KID Four Pawns", "E76", ("kid four pawns", "kings indian four pawns", "king's indian four pawns")),
    Opening("KID Samisch", "E80", ("kid samisch", "kid sämisch", "kings indian samisch", "king's indian samisch")),
    Opening("Mar del Plata", "E97", ("mar del plata",)),
    # --- Flank and irregular (A0x-A3x) ---
    Opening("Polish", "A00", ("polish opening", "sokolsky", "orangutan")),
    Opening("Grob", "A00", ("grob",)),
    Opening("Van't Kruijs", "A00", ("vant kruijs", "van't kruijs")),
    Opening("Van Geet", "A00", ("van geet", "dunst")),
    Opening("Hungarian Opening", "A00", ("hungarian opening",)),
    Opening("Saragossa", "A00", ("saragossa",)),
    Opening("Durkin", "A00", ("durkin", "sodium attack")),
    Opening("Amar", "A00", ("amar opening", "paris opening")),
    Opening("Nimzowitsch-Larsen", "A01", ("larsen", "nimzo larsen", "nimzo-larsen")),
    Opening("Bird", "A02", ("bird",)),
    Opening("From's Gambit", "A02", ("froms gambit", "from's gambit")),
    Opening("Zukertort", "A04", ("zukertort",)),
    Opening("Reti", "A0", ("reti", "réti")),
    Opening("King's Indian Attack", "A07", ("kings indian attack", "king's indian attack")),
    Opening("English", "A1", ("english", "english opening")),
    Opening("Mikenas-Carls", "A18", ("mikenas",)),
    Opening("King's English", "A20", ("kings english", "king's english", "reversed sicilian")),
    Opening("Symmetrical English", "A30", ("symmetrical english",)),
)


_BY_ALIAS: dict[str, Opening] = {normalize(a): o for o in OPENINGS for a in o.aliases}
_BY_NAME: dict[str, Opening] = {normalize(o.name): o for o in OPENINGS}


def display_name(mention: str) -> str:
    """The canonical name for something a question called an opening.

    `danish` becomes `Danish Gambit`, so the prompt names an opening the way a
    coach would rather than the way the question happened to spell it. Anything
    the table does not know comes back unchanged — `mentioned_openings` also
    carries bare ECO codes, and the prompt fixtures pass full Chess.com names.
    """
    key = normalize(mention)
    opening = _BY_ALIAS.get(key) or _BY_NAME.get(key)
    return opening.name if opening is not None else mention
