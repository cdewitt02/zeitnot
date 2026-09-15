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

Based on the provided data, it appears that your best time control is blitz.
You have a win rate of 53.4% in blitz games, which is 1.5% below your overall
win rate. However, this is still a respectable performance, and the difference
is relatively small.

Additionally, your average centipawn loss (CPL) is 152.9 in blitz games, which
is 10.3 points better than your overall average CPL. This suggests that you are
able to make more accurate decisions under the time pressure of blitz, which is
a skill that is valuable in many types of chess.

It's worth noting that while you have a strong performance in blitz, you also
have a strong performance in rapid games, with a 100.0% win rate in a single
game (Game 9). However, the sample size is too small to draw any reliable
conclusions.

The fact that you perform less well in bullet games compared to blitz and rapid
games suggests that you may struggle with the extremely short time controls.
The bullet game with the lowest win rate and highest average CPL is Game 2,
where you lost as white in a long game. This may indicate that the time
pressure and complexity of the game overwhelmed you, and that you need more
time to think through your moves.

Overall, while there may be some variation in your performance depending on the
specific time control, blitz appears to be your strongest suit.

You: exit
Goodbye!
```

That answer is built from 195 real analyzed games, and every number in it comes
from your own database. It is an **unedited** session on the configuration the
banner names — `llama3.2`, running locally, the default. A 3B model reasons
about as well as a 3B model reasons; a hosted one writes better prose over the
same numbers. What does not vary is where the numbers come from.

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

**The wait is the download, not the analysis.** A first run pulls about 2.7 GB
once — the Postgres image (443 MB) and the two Ollama models (2.0 GB and
270 MB) — and how long that takes is between you and your connection. The
analysis is the fast part: one real month, 195 games, took **85 seconds** with
`NUM_WORKERS=4` pinned to four cores, embedding included. It does saturate those
cores while it runs.

Chess.com archives games one month at a time, so step 5 is per month. Two or
three months is enough for the aggregates to say anything.

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
party**. Opponents' usernames are not among them. They are stripped when a game
is analyzed, and withheld again when the prompt is assembled — so a corpus
analyzed by an older version is covered too, without re-ingesting.
`tests/test_router_prompt.py` asserts it rather than leaving it to review.

**[Providers, and how to choose →](docs/providers.md)**

## If something goes wrong

Run `zeitnot doctor`. It names the failing check and the remedy, and it reports
everything that is wrong in one pass rather than one failure per attempt.

[`docs/troubleshooting.md`](docs/troubleshooting.md) covers the failures that are
correct-but-surprising, and the ones doctor can only point at.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The short
version is that `ruff check`, `mypy --strict`, `pyright`, and `pytest` must all
pass.

Looking for somewhere to start?
[`docs/opensource-readiness/01-roadmap.md`](docs/opensource-readiness/01-roadmap.md)
lists the outstanding work with the reasoning behind each item.

## License

MIT — see [LICENSE](LICENSE).
