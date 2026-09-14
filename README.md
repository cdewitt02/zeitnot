# Zeitnot

[![CI](https://github.com/cdewitt02/zeitnot/actions/workflows/ci.yml/badge.svg)](https://github.com/cdewitt02/zeitnot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**Chat with your own Chess.com games** — grounded in Stockfish analysis of every
move you have actually played, not in generic chess advice.

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

## Quick start

Runs fully locally by default — no account, no API key, no network beyond the
Chess.com public API. You need **Docker**, **Python 3.11+**,
**[uv](https://docs.astral.sh/uv/getting-started/installation/)**,
**[Ollama](https://ollama.com)**, and **Stockfish**
(`sudo apt install stockfish`, or `brew install stockfish`).

```bash
# 1. Configure and start the database (Postgres + pgvector, no further setup)
cp .env.example .env
docker compose up -d

# 2. Install the command
uv tool install .

# 3. Pull the local models
ollama pull nomic-embed-text
ollama pull llama3.2

# 4. Check everything before you wait on a long run
zeitnot doctor

# 5. Analyze a month of games — the month needs its leading zero
zeitnot data analyze cdew4 2026 01

# 6. Ask it something
zeitnot chat cdew4
```

**Expect fifteen minutes minimum before a first answer.** Ingestion runs
Stockfish over every move at roughly a second per game, and that dominates the
run.

Prefer hosted models, or already have Postgres on port 5432? See
[Providers](docs/providers.md) and [Configuration](docs/configuration.md).

<details>
<summary>What <code>zeitnot doctor</code> looks like</summary>

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
</details>

## What you can ask

> *What openings do I lose with most often?*
>
> *Show me games where I threw a winning position.*
>
> *What should I study to improve fastest?*

Answers cite your own games. The retrieved games, your aggregate statistics, and
the instruction block are assembled deterministically, so the same question
against the same corpus produces byte-identical input to the model.

## How it works

1. **Fetch** a month of your games from the Chess.com public API.
2. **Analyze** every move with Stockfish — centipawn loss, blunders, phase.
3. **Summarize** each game into deterministic prose, with no LLM involved, and
   embed it into Postgres/pgvector.
4. **Retrieve and answer**: your question selects relevant games by hybrid
   search, and a chat model answers over those games and your statistics.

## Configuration

Zeitnot reads `.env` from the working directory on every run — there is nothing
to source, and anything already exported wins over the file.

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | *required* |
| `ZEITNOT_DB_PORT` | Host port Compose publishes; `DATABASE_URL` is built from it | `5432` |
| `CHAT_PROVIDER` | `ollama`, `anthropic`, or `openai` | `ollama` |
| `EMBED_PROVIDER` | `ollama` or `openai` | `ollama` |
| `STOCKFISH_PATH` | Path to the Stockfish binary | `stockfish` on `PATH` |
| `NUM_WORKERS` | Parallel analysis workers | `4` |

**[Full variable reference →](docs/configuration.md)**

## Providers

Chat and embeddings are selected **independently**, because Anthropic offers no
embeddings API. Both default to Ollama.

| Role | Variable | Values | Default model |
|------|----------|--------|---------------|
| Chat | `CHAT_PROVIDER` | `ollama`, `anthropic`, `openai` | `llama3.2` / `claude-opus-5` / `gpt-5-2025-08-07` |
| Embeddings | `EMBED_PROVIDER` | `ollama`, `openai` | `nomic-embed-text` / `text-embedding-3-small` |

Start with the default. Several chat models were compared informally on one real
corpus and came out close enough that none stood out, so there is no measured
quality reason to reach for a hosted API first. Switching the chat provider later
is cheap and reversible — the index is untouched.

Selecting a hosted provider **sends your game summaries and username to a third
party**; opponents' usernames are stripped before anything leaves the machine.

**[Providers, and how to choose →](docs/providers.md)**

## If something goes wrong

Run `zeitnot doctor`. It names the failing check and the remedy, and it reports
everything that is wrong in one pass rather than one failure per attempt.

[`docs/troubleshooting.md`](docs/troubleshooting.md) covers the failures that are
correct-but-surprising, and the ones doctor can only point at.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The short
version is that `ruff check`, `mypy --strict`, and `pytest` must all pass.

Looking for somewhere to start?
[`docs/opensource-readiness/01-roadmap.md`](docs/opensource-readiness/01-roadmap.md)
lists the outstanding work with the reasoning behind each item.

## License

MIT — see [LICENSE](LICENSE).
