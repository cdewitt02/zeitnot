# Zeitnot

Chat with your own Chess.com games. Zeitnot analyzes every move with Stockfish,
writes a summary of each game, and answers questions about your play grounded in
what actually happened on the board — not in generic chess advice.

```console
$ zeitnot chat cdew4

Chess Coach Chat
================
Analyzing games for: cdew4
Chat:       ollama / llama3.2
Embeddings: ollama / nomic-embed-text

Ask questions about your chess games.
Commands: /clear (reset conversation), exit/quit (leave)

You: Which time control is my best, and why?
  Thinking...

Rapid, clearly — and the gap is bigger than most players expect.

  Rapid    4 games    75.0% win rate    37.2 avg CPL
  Blitz   32 games    34.4% win rate    98.9 avg CPL
  Bullet  38 games    26.3% win rate   242.6 avg CPL

Your rapid centipawn loss is 37, which is genuinely strong. In bullet it is
243 — nearly seven times worse. That is not a different opponent pool, it is
the same player with less time to see.

The tell is where the mistakes land: your bullet games show blunders spread
through the middlegame, while your rapid games mostly stay clean until the
endgame. You are not losing bullet games on preparation. You are losing them
on calculation you do not have time to do.

If the goal is rating, play more rapid. If the goal is improvement, your
bullet games are the cheapest source of tactical mistakes to review.

You: exit
Goodbye!
```

That answer is built from 74 real analyzed games. Every number in it comes from
your own database.

## Setup

Four things have to be on the machine before the steps below:

- **Docker** — or PostgreSQL with the
  [pgvector](https://github.com/pgvector/pgvector) extension, if you would
  rather run your own
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** or
  **[pipx](https://pipx.pypa.io/stable/installation/)**, to install the command
- **Stockfish** — `sudo apt install stockfish`, or `brew install stockfish`

Then one decision, which is step 3 and nothing else: where the language models
run.

| | **Local** (Ollama) | **Hosted** (Anthropic / OpenAI) |
|---|---|---|
| Getting started | ~18 minutes of unattended download, ~1.5 GB | ~8–10 minutes of *active* work: account, billing, an API key |
| Per question | free | billed per token |
| Your games | never leave the machine | summaries and your username go to the provider |
| Hardware | wants RAM and spare cores | none |

Neither wins on answer quality in any way that has been measured — see
[Choosing a chat model](#choosing-a-chat-model). Hosted reaches a first answer
sooner; local needs no account, no key, and no network. Both are fully
supported, and switching chat providers later is cheap and reversible.

**Whichever you pick, expect fifteen minutes minimum before a first answer.**
Ingestion runs Stockfish over every move at roughly a second per game, and no
choice here changes that.

### 1. Configure, and start the database

```bash
cp .env.example .env
docker compose up -d
```

Zeitnot reads that file from the working directory on every run — there is
nothing to source. A variable already exported wins over the file, so a value
set by hand is never overwritten.

The database needs no further setup: the image ships pgvector, the extension is
enabled on first start, and zeitnot creates its own tables when you first run
it.

Already running PostgreSQL on port 5432? Change `ZEITNOT_DB_PORT` in `.env`.
Docker Compose reads the same file, and `DATABASE_URL` is built from that same
variable, so both sides move together — and `zeitnot doctor` compares them.

<details>
<summary>Using your own PostgreSQL instead</summary>

```bash
psql -c "CREATE DATABASE zeitnot;"
psql -d zeitnot -c "CREATE EXTENSION vector;"
```

You will need pgvector installed, which often means building it from source
against your local PostgreSQL headers. The container exists to avoid exactly
this step.
</details>

### 2. Install zeitnot

```bash
uv tool install .          # or: pipx install .
```

<details>
<summary>Development install</summary>

```bash
uv venv && uv pip install -e ".[dev]"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks a change has to pass.
</details>

### 3. Choose where the models run

**Local** is the default, and needs no change to `.env`:

```bash
ollama pull nomic-embed-text   # embeddings
ollama pull llama3.2           # chat
```

**Hosted** — set these two in `.env`. OpenAI is the only provider that serves
both roles, so it is the only one that takes Ollama off the list entirely:

```bash
OPENAI_API_KEY=sk-...
CHAT_PROVIDER=openai
EMBED_PROVIDER=openai
```

Chat and embeddings are selected independently, so mixed setups are ordinary
rather than exotic — Anthropic for chat with embeddings left on Ollama, for
instance. That combination still needs Ollama installed, because Anthropic
offers no embeddings API. See [Providers](#providers).

### 4. Check the setup

```bash
zeitnot doctor
```

```console
[ ok ] environment file     /home/you/zeitnot/.env: 4 value(s) applied
[ ok ] configuration        chat ollama / llama3.2
                            embeddings ollama / nomic-embed-text
[skip] credentials          no hosted provider is selected
[ ok ] stockfish            Stockfish 16 at /usr/games/stockfish
[ ok ] DATABASE_URL         postgres://zeitnot:***@localhost:5432/zeitnot
[ ok ] database port        compose and DATABASE_URL both use 5432
[ ok ] database             connected
[ ok ] pgvector             installed
[ ok ] corpus               no tables yet — zeitnot creates them on the first `zeitnot data analyze`
[ ok ] chat provider        reachable, credentials and model accepted
[ ok ] embeddings           reachable, credentials and model accepted

11 ok

Nothing is blocking a run. Next: zeitnot data analyze <username> <year> <month>
```

It runs every check the real commands run, reports all of them rather than
stopping at the first, and changes nothing. Credentials are redacted, so the
output is safe to paste into an issue. It exits non-zero if anything failed.

### 5. Analyze a month of games

```bash
zeitnot data analyze <username> <year> <month>

# Example
zeitnot data analyze cdew4 2026 01
```

**The month needs its leading zero** — `01`, not `1`. Expect roughly a second
per game: every move is searched twice by Stockfish, which dominates the run.

### 6. Ask it something

```bash
zeitnot chat <username> [chat-model]

# Example
zeitnot chat cdew4
```

The optional positional model is passed to whichever chat provider is selected,
and it outranks `CHAT_MODEL` — so pass a model that provider actually offers.

Good opening questions: *"What openings do I lose with most often?"*,
*"Show me games where I threw a winning position"*, *"What should I study to
improve fastest?"*

## If something goes wrong

Run `zeitnot doctor`. It names the failing check and the remedy, and it reports
everything that is wrong in one pass rather than one failure per attempt.

[`docs/troubleshooting.md`](docs/troubleshooting.md) covers the failures that
are correct-but-surprising, and the ones doctor can only point at.

## Providers

Chat and embeddings are selected **independently**, because Anthropic offers no
embeddings API. Both default to Ollama, so an existing setup keeps working
unchanged and the tool still runs with no account, no key, and no network.

| Role | Variable | Values | Default model |
|------|----------|--------|---------------|
| Chat | `CHAT_PROVIDER` | `ollama`, `anthropic`, `openai` | `llama3.2` / `claude-opus-5` / `gpt-5-2025-08-07` |
| Embeddings | `EMBED_PROVIDER` | `ollama`, `openai` | `nomic-embed-text` / `text-embedding-3-small` |

Default model IDs are **pinned, never aliased**: a server-side upgrade behind an
alias would change answers with no code change and no way to notice.

### Choosing a chat model

**Start with the default.** Several chat models were compared informally against
one real corpus and came out close enough that no provider stood out — the local
`llama3.2` default included. There is no measured quality reason to reach for a
hosted API first.

That comparison is more meaningful than it sounds, because the prompt is
deterministic: every provider receives byte-identical retrieved games,
statistics, and instructions, so the model is the only thing that differs. It is
still one person's games and one person's judgment, not a benchmark — which is
exactly why the choice is left to you rather than baked into a recommendation.

Reasons to switch that do **not** depend on answer quality:

- **Hardware.** Local inference wants RAM and CPU that a small laptop or VM may
  not have. A hosted chat provider moves that cost off your machine.
- **Speed.** Hosted models generally return faster than local inference on
  modest hardware.
- **Dropping Ollama entirely.** Only `EMBED_PROVIDER=openai` does that, since
  Anthropic has no embeddings API — and it means re-embedding your index.

Reasons to stay local: no account, no API key, no per-query cost, and **nothing
about your games leaves the machine**.

Because chat and embeddings are selected independently, trying a different chat
model is cheap and reversible — the index is untouched, so switching back costs
nothing. Comparing on your own corpus is the only comparison that is really
about your games; `docs/multi-provider/03-eval-plan.md` describes how to hold
everything else constant if you want to do it properly.

### Using Anthropic for chat

```bash
# in .env
ANTHROPIC_API_KEY=sk-ant-...     # never committed; .env is gitignored
CHAT_PROVIDER=anthropic
```

Embeddings stay on Ollama, so **no re-embedding is needed** and the existing
index keeps working — which is also what makes "same index, different chat
model" an honest comparison. Ollama is still a prerequisite for the embedding
half.

**This sends data off-machine.** Selecting a hosted provider sends your game
summaries and Chess.com username to a third party. Both the startup banner and
`zeitnot doctor` say so whenever a hosted provider is active.

Opponents' usernames are **not** sent. Chess.com's termination strings embed the
winner's handle ("Bolzman0 won by resignation"), so those are normalized to the
outcome and method — "lost by resignation" — before they reach the prompt or a
stored summary. Anything in an unrecognized format collapses to "lost by other
means" rather than being passed through.

If you ingested games before this change, run `zeitnot data refresh-stats
<username>` to rebuild the aggregates. Summaries written earlier still carry the
old text until they are regenerated.

Failures are reported, never silently retried on another provider: an Anthropic
error answered from `llama3.2` would leave you comparing outputs without knowing
which model produced which.

### Using OpenAI

OpenAI is the only provider that serves both halves, so it is the one that can
take Ollama off the prerequisite list entirely:

```bash
# in .env
OPENAI_API_KEY=sk-...
CHAT_PROVIDER=openai
EMBED_PROVIDER=openai
```

`text-embedding-3-small` is asked for **768 dimensions**, which is the width the
`game_summaries` column already declares — so no schema migration is involved.
It is still a *different* embedding model: vectors from two models are not
comparable even at the same width, so switching embed providers on an existing
index is refused at startup, naming the re-embed path (`zeitnot data reembed`).

## Environment Variables

Zeitnot reads `.env` from the working directory on every run. Carriage returns
are stripped, quoting and comments are parsed rather than word-split, and
`${VAR}` references resolve against the whole file regardless of the order lines
appear in. **Anything already exported wins over the file** — `zeitnot doctor`
reports which values it declined to set for that reason.

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | *required* |
| `ZEITNOT_DB_PORT` | Host port `docker-compose.yml` publishes; `DATABASE_URL` is built from it | `5432` |
| `ZEITNOT_ENV_FILE` | Read configuration from another path; set it empty to read no file at all | `.env` |
| `CHAT_PROVIDER` | Chat provider: `ollama`, `anthropic`, or `openai` | `ollama` |
| `CHAT_MODEL` | Chat model (positional CLI arg outranks it) | per provider |
| `EMBED_PROVIDER` | Embedding provider: `ollama` or `openai` | `ollama` |
| `EMBED_MODEL` | Embedding model — must emit **768 dimensions** | per provider |
| `ANTHROPIC_API_KEY` | Required when `CHAT_PROVIDER=anthropic` | — |
| `OPENAI_API_KEY` | Required when either provider is `openai` | — |
| `OLLAMA_URL` | Ollama server URL (honored by both subcommands) | `http://localhost:11434` |
| `OLLAMA_EMBED_MODEL` | Alias for `EMBED_MODEL` when the embed provider is Ollama | `nomic-embed-text` |
| `STOCKFISH_PATH` | Path to the Stockfish binary; overrides the `PATH` lookup | `stockfish` on `PATH` |
| `NUM_WORKERS` | Parallel analysis workers | `4` |
| `NO_COLOR` | Set to any value to print raw markdown instead of styled output | — |
| `ZEITNOT_DEBUG_PROMPT` | Set to any value to dump the assembled prompt to stderr | — |

Credentials come from the environment only, under the provider-standard names.
See `.env.example`.

### Terminal output

The coach answers in markdown, and `zeitnot chat` renders it in place: headings,
bullets, tables, and fenced code blocks are styled to the terminal's width. The
reply streams in as plain text while the model works, then is repainted once as
the finished document — markdown cannot be laid out incrementally, because
wrapping and table widths are properties of the whole answer.

Styling is skipped, and the raw markdown printed instead, whenever stdout is not
a terminal, `NO_COLOR` is set, or `TERM` reports a terminal that cannot render
it. So `zeitnot chat magnus > notes.md` captures clean markdown rather than
escape codes.

`zeitnot doctor` is never styled: it is written to be pasted into an issue.

### Changing the embedding model

The `game_summaries.embedding` column is `vector(768)`, and the provider and
model that built the index are recorded alongside it. Two models of the same
width occupy different vector spaces, so a width check alone would pass while
retrieval silently degraded — startup refuses to run against an index built by a
different embedder instead.

Re-embedding is bounded work: summaries are generated deterministically with no
LLM and no Stockfish, so this reads stored text and updates vectors rather than
re-running analysis.

```bash
zeitnot data reembed
```

## Project Structure

```
zeitnot/
  api.py       # Chess.com API client
  cli.py       # The `zeitnot` command: doctor, data and chat subcommands
  config.py    # Provider selection and startup validation
  doctor.py    # `zeitnot doctor`: every startup check, run together
  engine.py    # Stockfish analysis via python-chess
  envfile.py   # Reads .env, so the shell is not the configuration loader
  ingest.py    # The analysis worker pool
  repl.py      # Terminal chat loop
  summary.py   # Game Summary generation
  chat/        # Query classification, prompt assembly, chat service
  db/          # PostgreSQL/pgvector operations
  llm/         # Provider-neutral chat and embedding protocols
    anthropic.py # Anthropic adapter (chat only)
    ollama.py    # Ollama adapter (chat + embeddings)
    openai.py    # OpenAI adapter (chat + embeddings)
  models/      # Domain types
  search/      # Query parsing, filters, hybrid retrieval
tests/
testdata/golden/    # Regression suite frozen at the cutover — see its MANIFEST.md
docker-compose.yml  # Postgres + pgvector for local development
docs/troubleshooting.md
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) — the short
version is that `ruff check`, `mypy --strict`, and `pytest` must all pass, and
the corpus-backed tests need a database.

If you are looking for somewhere to start, `docs/opensource-readiness/01-roadmap.md`
lists the outstanding work with the reasoning behind each item.

## License

MIT — see [LICENSE](LICENSE).
