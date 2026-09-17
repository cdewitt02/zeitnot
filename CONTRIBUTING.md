# Contributing

Contributions are welcome. Bug reports and feature requests go through the
[issue templates](.github/ISSUE_TEMPLATE); pull requests follow
[the PR template](.github/PULL_REQUEST_TEMPLATE.md).

## Setup

```bash
uv sync --dev                  # the virtualenv, zeitnot, and the four gates
cp .env.example .env
uv run zeitnot doctor          # every startup check, in one pass
```

**`uv sync --dev`, not `uv pip install -e ".[dev]"`.** The development
dependencies are a [PEP 735](https://peps.python.org/pep-0735/) dependency
group, not an extra, and asking for an extra that does not exist is a *warning*:
the install exits 0 and silently contains none of the gates below. Either prefix
commands with `uv run`, or activate the virtualenv yourself.

You will also need PostgreSQL with pgvector, Stockfish on `PATH`, and — for the
default configuration — Ollama with `nomic-embed-text` and `llama3.2` pulled.
`zeitnot doctor` reports which of those are missing rather than failing at the
first one; the README's [Quick start](README.md#quick-start) covers installing
them.

Nothing needs sourcing: zeitnot reads its environment file from the working
directory itself, and anything already exported outranks the file.

## The checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pyright
uv run pytest -m "not corpus"
```

All four must pass, and CI runs exactly these on 3.11 and 3.13. The one
difference is the last line: CI has no database, so it runs the `not corpus`
subset. A bare `pytest` locally is a superset of it, not a different check.

**`mypy --strict` is not optional and not negotiable down.** It is configured in
`pyproject.toml` and it is what replaced the Go compiler; a `# type: ignore`
needs a reason next to it.

**`pyright` is a second checker, not a stricter one**, and it runs at *standard*
rather than strict. It is here for one rule mypy does not implement:
`LiteralString`, which is what keeps a filter value out of the SQL text that
`zeitnot/search/filters.py` builds. If you are adding a query, values go in
`args` as `%s` placeholders and never into the clause — the type will tell you
so. Raising pyright to strict is not a tidy-up: it reports ~70 findings, almost
all of them the LLM SDKs' own return types being partially unknown.

## The testing matrix

Tests are marked by what they need, so a bare `pytest` says what it did not
check rather than skipping silently.

| Marker | Needs | Skips when |
|---|---|---|
| *(unmarked)* | nothing | never — these must always run |
| `corpus` | `DATABASE_URL` pointing at a populated database | it is unset |

There used to be a third, `golden`, for tests reading a corpus capture that
existed on one machine. Those files were retired in
[ADR 0003](docs/adr/0003-retire-the-parity-goldens.md); every expected-output
file in `testdata/golden/` is now committed, so no test can fail to find what it
reads.

```bash
pytest -m "not corpus"          # the portable subset — what a fresh clone runs
pytest                          # everything the environment supports
```

The corpus-backed tests run against the **live** database on purpose. A
float-conversion bug in the vector path is exactly the kind of defect a fixture
would reproduce faithfully and wrongly.

**One `corpus` test fails on any corpus analyzed before 2026-09-14, and that is
correct.** `tests/test_summary_corpus.py` checks that every stored summary still
re-derives from the `games` and `moves` rows next to it. Three deliberate changes
— termination normalization, the phase-boundary fix, and the draw fix — moved the
text without a way to regenerate what is stored, so an older corpus genuinely has
summaries their own vectors no longer describe. The failure names which summary
lines moved. If the lines it names are those three, you have a stale corpus and
not a regression; if it names something else, look at your change.

## Commits

One phase or one concern per commit, with a message that says *why*. The commit
log is the design record for anything not written down in `docs/`.

## Before you touch the prompt path or `summary.py`

[`docs/codebase-invariants.md`](docs/codebase-invariants.md) covers the
properties that are easy to break with a change that looks like a cleanup: the
sorted-iteration rule the assembled prompt depends on, what the committed
expected-output tables are for and how to regenerate the prompt snapshots
honestly, and the one preserved defect still in `summary.py`.

A change to the assembled prompt turns `tests/test_prompt_snapshot.py` red with
a diff. Read it, decide whether it is what you meant, and if it is, regenerate
with `ZEITNOT_UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshot.py`
and commit the snapshots **in the same change**. The point of the file being
committed is that your reviewer sees the prompt move next to the code that moved
it.
