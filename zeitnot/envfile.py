"""Load the environment file zeitnot is configured from.

Nothing loaded it before. The file was documented as something the *shell*
sourced, which made the shell zeitnot's configuration loader and every shell
pathology a zeitnot bug: a file saved with CRLF put a carriage return in the
middle of `DATABASE_URL`, a port defined below the URL that used it expanded to
nothing, `export $(cat ... | xargs)` split the comments into arguments, and
sourcing silently overwrote a value the user had just exported by hand. Five of
the README's troubleshooting entries describe those, and all five are this one
missing step.

Parsing the file here makes them impossible rather than documented:

- Carriage returns are stripped on read, so line endings stop mattering.
- References are resolved against the whole file, so definition *order* stops
  mattering — `DATABASE_URL` may name `${ZEITNOT_DB_PORT}` from above or below.
- Comments and quoting are handled by a parser rather than by word splitting.

**Precedence is the real environment first, always.** A value already exported
is the most recent and most deliberate thing the user did, and it is what
`docker compose` sees too, so the file never overwrites it. The old surprise ran
the other way — sourcing clobbered an exported override, which is why a port
change so often appeared not to take — and this half of the fix has to be
*reported* rather than merely correct, which is why `Loaded` records the names
it declined to set.

The parser is deliberately small. It is not a shell: there are no multi-line
values, no command substitution, and no backslash escapes beyond `\\$`.
Anything it cannot read is collected as a complaint rather than raised, because
a loader that stops at the first bad line cannot tell you about the second one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

# The file read when ZEITNOT_ENV_FILE says nothing. Resolved against the working
# directory rather than searched for up the tree: a fixed answer that `zeitnot
# doctor` prints as an absolute path is easier to reason about than a search
# that quietly finds the wrong file one directory up.
DEFAULT_FILENAME = ".env"

# Points the loader at another file. Set it to the empty string to load nothing,
# which is the escape hatch for an environment that is already fully configured
# — a container, or CI.
PATH_VAR = "ZEITNOT_ENV_FILE"

_ASSIGNMENT = re.compile(r"^(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$")

# A reference, with any backslash that escapes it captured alongside so `\$` can
# be passed through as a literal without a substitution pass of its own.
_REFERENCE = re.compile(r"(\\?)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")

# Only whitespace-then-hash starts a comment on an unquoted value. A bare `#` is
# ordinary text, which matters because it is legal in a URL fragment.
_INLINE_COMMENT = re.compile(r"[ \t]+#.*$")


@dataclass(slots=True)
class Loaded:
    """What one load did, in the terms `zeitnot doctor` reports it in.

    Values are never recorded — only names. This object is built on a file
    holding API keys, and it is handed straight to a diagnostic command whose
    output is written to be pasted into a public issue.
    """

    path: Path | None
    exists: bool = False
    applied: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    crlf: bool = False


def default_path(environ: Mapping[str, str] | None = None) -> Path | None:
    """The file `load` reads, or None when loading is switched off."""
    env = os.environ if environ is None else environ
    override = env.get(PATH_VAR)
    if override is None:
        return Path(DEFAULT_FILENAME)
    override = override.strip()
    return Path(override) if override else None


def load(environ: MutableMapping[str, str] | None = None, path: Path | None = None) -> Loaded:
    """Apply the file's values to `environ`, leaving anything already set alone.

    A missing file is not a problem: a user who exports everything by hand, or
    who runs from a container, never needs one. It is reported as absent so
    doctor can say *which* path was looked at, since running from the wrong
    directory is the failure this replaces.
    """
    env = os.environ if environ is None else environ
    target = default_path(env) if path is None else path
    if target is None:
        return Loaded(path=None)

    try:
        # Bytes, then decode, rather than read_text: text mode translates CRLF
        # to LF on the way in, which would fix the file silently and leave
        # `crlf` — the thing doctor reports — permanently False. The utf-8-sig
        # codec drops a byte-order mark, which a Windows editor may have written
        # and which would otherwise become part of the first key's name: the
        # same class of invisible corruption as the carriage returns.
        text = target.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        return Loaded(path=target)
    except UnicodeDecodeError:
        return Loaded(path=target, exists=True, problems=("is not valid UTF-8 text",))
    except OSError as err:
        return Loaded(path=target, exists=True, problems=(f"could not be read: {err}",))

    values, problems, crlf = parse(text, env)

    applied: list[str] = []
    deferred: list[str] = []
    for name, value in values.items():
        # An exported-but-empty variable counts as unset, matching how the rest
        # of zeitnot reads the environment: `config.resolve` treats "" as absent
        # everywhere, so the file filling one in is what the user expects.
        if env.get(name, ""):
            deferred.append(name)
            continue
        env[name] = value
        applied.append(name)

    return Loaded(
        path=target,
        exists=True,
        applied=tuple(applied),
        deferred=tuple(deferred),
        problems=tuple(problems),
        crlf=crlf,
    )


def parse(
    text: str, environ: Mapping[str, str] | None = None
) -> tuple[dict[str, str], list[str], bool]:
    """Split an env file into resolved values, complaints, and whether it had
    CRLF line endings.

    `environ` resolves references only; nothing is written to it here.
    """
    env = os.environ if environ is None else environ
    crlf = "\r\n" in text

    raw: dict[str, str] = {}
    quotes: dict[str, str] = {}
    problems: list[str] = []

    # split("\n") rather than splitlines(): the latter also breaks on \x0b and
    # \x0c, which in a value are corruption to report rather than a line ending
    # to honor.
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.rstrip("\r").strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(stripped)
        if match is None:
            problems.append(f"line {number} is not a NAME=VALUE assignment: {stripped[:48]!r}")
            continue
        name, quote, value, problem = _unquote(match.group(1), match.group(2))
        if problem:
            problems.append(f"line {number}: {problem}")
        raw[name] = value
        quotes[name] = quote

    resolved, reference_problems = _resolve(raw, quotes, env)
    return resolved, problems + reference_problems, crlf


def _unquote(name: str, value: str) -> tuple[str, str, str, str]:
    """Strip quoting from one raw value.

    Returns the name, the quote character, the text, and a complaint or "".
    The quote decides interpolation later: single quotes suppress it exactly as
    a shell does, which is what the README already tells anyone whose
    `DATABASE_URL` arrived with `${ZEITNOT_DB_PORT}` still in it.
    """
    value = value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end == -1:
            return name, "", value[1:], f"{name} has an unterminated {quote} quote"
        return name, quote, value[1:end], ""
    return name, "", _INLINE_COMMENT.sub("", value).strip(), ""


def _resolve(
    raw: Mapping[str, str], quotes: Mapping[str, str], environ: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Expand `${NAME}` and `$NAME` references in every value.

    References resolve against the real environment first and the file second,
    regardless of where in the file the referenced name is defined. Both halves
    matter. Resolving the environment first keeps a built value consistent with
    the one actually in effect — an exported `ZEITNOT_DB_PORT=5433` has to
    produce a `DATABASE_URL` naming 5433, not the 5432 the file happens to say.
    Ignoring order is what retires the "empty port" failure, where a
    `ZEITNOT_DB_PORT` appended *below* the URL that used it expanded to nothing
    and libpq read the gap as 5432.
    """
    resolved: dict[str, str] = {}
    problems: list[str] = []

    def file_value(name: str, chain: tuple[str, ...]) -> str:
        if name in resolved:
            return resolved[name]
        if name in chain:
            cycle = " -> ".join((*chain, name))
            problems.append(f"{name} refers to itself: {cycle}")
            return ""
        text = _interpolate(raw[name], quotes[name], (*chain, name))
        resolved[name] = text
        return text

    def reference(name: str, chain: tuple[str, ...]) -> str:
        from_environment = environ.get(name, "")
        if from_environment:
            return from_environment
        if name in raw:
            return file_value(name, chain)
        problems.append(
            f"${{{name}}} is not defined in the file or the environment, so it expanded to nothing"
        )
        return ""

    def _interpolate(text: str, quote: str, chain: tuple[str, ...]) -> str:
        if quote == "'":
            return text

        def replace(match: re.Match[str]) -> str:
            escape, braced, bare = match.group(1), match.group(2), match.group(3)
            if escape:
                return match.group(0)[1:]
            return reference(braced or bare, chain)

        # The second pass covers a `\$` that escapes something which is not a
        # reference at all — `\$5`. The first pass cannot see it, because there
        # is no name after the dollar for it to match.
        return _REFERENCE.sub(replace, text).replace("\\$", "$")

    for name in raw:
        file_value(name, ())
    return resolved, problems
