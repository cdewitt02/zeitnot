# Configuration

Zeitnot reads its environment file from the working directory on every run —
there is nothing to source. Carriage returns are stripped, quoting and comments
are parsed rather than word-split, and `${VAR}` references resolve against the
whole file regardless of the order lines appear in. **Anything already exported
wins over the file** — `zeitnot doctor` reports which values it declined to set
for that reason.

Credentials come from the environment only, under the provider-standard names.
Start from `.env.example`.

## Every variable

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

Provider selection is covered in [Providers](providers.md).

## The database port

Already running PostgreSQL on port 5432? Change `ZEITNOT_DB_PORT`. Docker
Compose reads the same file, and `DATABASE_URL` is built from that same
variable, so both sides move together — and `zeitnot doctor` compares them.

## Using your own PostgreSQL

```bash
psql -c "CREATE DATABASE zeitnot;"
psql -d zeitnot -c "CREATE EXTENSION vector;"
```

You will need pgvector installed, which often means building it from source
against your local PostgreSQL headers. The container exists to avoid exactly
this step.

## Terminal output

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

## Changing the embedding model

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
