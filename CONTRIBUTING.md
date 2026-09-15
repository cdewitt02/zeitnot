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
| `golden` | locally captured corpus goldens | the file the test reads is absent |

```bash
pytest -m "not corpus"          # the portable subset — what a fresh clone runs
pytest                          # everything the environment supports
```

The corpus-backed tests run against the **live** database on purpose. A
float-conversion bug in the vector path is exactly the kind of defect a fixture
would reproduce faithfully and wrongly.

## Commits

One phase or one concern per commit, with a message that says *why*. The commit
log is the design record for anything not written down in `docs/`.

## Before you touch the prompt path or `summary.py`

[`docs/codebase-invariants.md`](docs/codebase-invariants.md) covers the
properties that are easy to break with a change that looks like a cleanup: the
sorted-iteration rule the assembled prompt depends on, why the goldens are
frozen and have no capture tool, and the one preserved defect still in
`summary.py`.
