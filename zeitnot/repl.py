"""The terminal REPL.

`rich` replaces glamour and `prompt_toolkit` replaces the bufio scanner. The
296-line `internal/render` package — a hand-rolled soft-wrapping stream writer
that counted physical lines so it could erase them with cursor escapes — is
replaced by `rich.live`, which owns that problem.

**The behavior that must not change** is the degradation. Everywhere styling is
unavailable — a pipe, a file, NO_COLOR, TERM=dumb — nothing is streamed and the
raw markdown is printed once. Cursor-up escapes would corrupt a captured
transcript, and markdown is readable on its own, which is what makes "when in
doubt, print the source" the right failure mode.
"""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from zeitnot.chat.service import Service
from zeitnot.config import redact_secrets

# The point below which wrapping does more harm than good, and the point above
# which full-width prose is measurably harder to read. The coach's answers are
# prose, not code.
_MIN_WIDTH = 40
_MAX_WIDTH = 100


def styling_available() -> bool:
    """Whether output can carry ANSI styling.

    Disabled when stdout is not a terminal (so `zeitnot chat ... > notes.md`
    captures clean markdown rather than escape codes), when NO_COLOR is set (the
    no-color.org convention), or when TERM says the terminal cannot render it.

    `sys.stdout.isatty()` directly, not `rich.Console.is_terminal`: rich applies
    its own heuristics and reports True under a plain pipe, which would put
    cursor escapes into a redirected transcript. The Go version asked the same
    question of the file descriptor, and that is the question that matters.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("dumb", ""):
        # An unset TERM is normal for a pipe and abnormal for a terminal; in
        # both cases assuming no styling is the safe read.
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _console(styled: bool) -> Console:
    """Leave a column free: writing into the last cell makes some terminals wrap
    on their own, which would double-count lines in the live view."""
    width = min(max(Console().width - 1, _MIN_WIDTH), _MAX_WIDTH)
    return Console(width=width, soft_wrap=False, force_terminal=styled or None)


def run_repl(service: Service, username: str, config_summary: str) -> None:
    styled = styling_available()
    console = _console(styled)

    print("Chess Coach Chat")
    print("================")
    print(f"Analyzing games for: {username}")
    print(config_summary)
    print()
    print("Ask questions about your chess games.")
    print("Commands: /clear (reset conversation), exit/quit (leave)")
    print()

    prompt_session = _prompt_session(styled)

    while True:
        try:
            line = _read_line(prompt_session)
        except EOFError:
            break
        except KeyboardInterrupt:
            # Ctrl-C at the prompt leaves, matching the Go signal handler.
            print("\nGoodbye!")
            break

        question = line.strip()
        if not question:
            continue
        if question in ("exit", "quit"):
            print("Goodbye!")
            break
        if question == "/clear":
            service.clear_history()
            print("Conversation cleared.")
            continue

        try:
            _answer(service, console, styled, question)
        except KeyboardInterrupt:
            # Ctrl-C during an answer abandons that answer, not the session.
            print("\n(interrupted)\n")
        except Exception as err:
            # Fail loudly. There is deliberately no fallback to another
            # provider: silently answering from a different model is the exact
            # confusion this feature exists to prevent.
            print(f"Error: {redact_secrets(str(err))}", file=sys.stderr)


def _prompt_session(styled: bool) -> object | None:
    """A prompt_toolkit session when there is a terminal to drive, else None.

    prompt_toolkit gives line editing and history that the Go scanner did not
    have; it is not usable against a pipe, which is what the fallback is for.
    """
    if not styled:
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory

        return PromptSession(history=InMemoryHistory())
    except Exception:
        # A terminal prompt_toolkit cannot drive is not worth failing over.
        return None


def _read_line(session: object | None) -> str:
    if session is None:
        # The prompt is written before the read, and echoed with the input, so
        # a piped transcript reads as a conversation rather than as a wall of
        # answers.
        sys.stdout.write("You: ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        sys.stdout.write(line)
        sys.stdout.flush()
        return line
    return str(session.prompt("You: "))  # type: ignore[attr-defined]


def _answer(service: Service, console: Console, styled: bool, question: str) -> None:
    """Ask one question and print the result.

    On a styled terminal the reply streams as plain text so there is something
    to read while the model works, then is repainted as rendered markdown.
    Rendering cannot happen incrementally: wrapping, list alignment, and table
    widths are all properties of the finished document, so a partial one would
    be laid out wrong and re-laid-out on every token.
    """
    if not styled:
        print("Thinking...")
        answer = service.ask(question)
        print(f"\nCoach: {answer}\n")
        return

    print()

    # Retrieval — embedding the question, then the vector search — runs before
    # the provider is called at all, so without this the terminal sits silent
    # for several seconds.
    parts: list[str] = []
    with Live(
        Text("  Thinking...", style="dim"),
        console=console,
        refresh_per_second=12,
        transient=True,  # erased on exit, so the repaint lands in its place
    ) as live:

        def on_delta(delta: str) -> None:
            parts.append(delta)
            live.update(Text("".join(parts)))

        answer = service.ask_stream(question, on_delta)

    console.print(Markdown(answer))
    print()
