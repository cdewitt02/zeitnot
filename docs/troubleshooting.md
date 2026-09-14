# Troubleshooting

**Run `zeitnot doctor` first.** It performs every check the real commands
perform, reports all of them instead of stopping at the first failure, and
changes nothing. Most of what follows is here for the cases where doctor tells
you *what* is wrong and this explains *why*.

Doctor's output is redacted for passwords and API keys, so it is safe to paste
into an issue — and it answers most of what the bug template asks for.

---

## Ingestion

**`Error: invalid month '1': expected 01-12, with the leading zero`**
Chess.com's URL needs two digits. Use `01`, not `1`. Rejected before any network
request, so nothing is wasted.

**`no games found for user "x" in 2026/01 — Chess.com returned 404`**
Either the username is misspelled or that player has no games archived for that
month. A 404 cannot tell those apart, which is why the message names both.

**Ingestion is slow**
Expect roughly a second per game — every move is searched twice by Stockfish,
which dominates the run. `NUM_WORKERS` (default 4) is the knob; each worker
spawns its own Stockfish process, so raising it past your core count will not
help.

---

## Stockfish

**`Error: Stockfish not found`**
Either install it so it lands on `PATH`, or say where it is:

```bash
sudo apt install stockfish      # Debian/Ubuntu
brew install stockfish          # macOS
```

```bash
# in .env
STOCKFISH_PATH=/usr/games/stockfish
```

`STOCKFISH_PATH` is usually the easier of the two, because it goes in the env
file alongside everything else rather than in a shell profile.

Note that `apt` installs the binary to **`/usr/games`**, which is on the `PATH`
of a login shell but frequently not otherwise — so Stockfish can be installed
and still not be found. `zeitnot doctor` prints the path it resolved, which
tells you which case you are in.

Only `zeitnot data analyze` needs the engine; chatting does not. The check runs
before the games are fetched, so a missing engine costs nothing.

**Doctor says "found at … but it did not start"**
The file exists and is executable but is not a working UCI engine — a binary for
another architecture, or a wrapper script. A `PATH` lookup cannot tell those
apart, which is why doctor starts the process rather than only finding it.

---

## The database

**`Error: could not connect to the database after 30s`**
The line under it is what PostgreSQL actually said, and it separates the two
cases:

- *Connection refused* — nothing is listening. Check `docker compose ps`; a
  container that exited leaves the port empty.
- *password authentication failed* — something answered, but it is not the
  zeitnot container. Usually a native PostgreSQL already holds 5432, which also
  stopped `docker compose up` from binding it. Publish another port by setting
  `ZEITNOT_DB_PORT=5433` in `.env` — `DATABASE_URL` is built from it, so both
  sides move together.

Startup waits the full 30 seconds before giving up, because `docker compose up
-d` returns before PostgreSQL accepts connections — a first run that races a
cold container retries rather than failing. Doctor waits exactly as long, on
purpose: a diagnostic that gave up sooner than the tool would report a failure
where a real run succeeds.

**`FATAL:  database "zeitnot" does not exist`**, or doctor reporting
**`the vector extension is not installed`**
The container is running and the credentials work — this is not a connection
problem. The PostgreSQL image runs `POSTGRES_DB` *and* `docker/init-pgvector.sql`
only on a first start with an empty data directory. A first `docker compose up`
that was interrupted leaves the volume non-empty but incomplete, and from then
on every start skips initialization: restarting, re-running `up`, and changing
the port all leave it exactly as broken.

Discard the volume so it initializes again. **This deletes any games you have
already analyzed** — re-run `zeitnot data analyze` afterwards:

```bash
docker compose down -v
docker compose up -d
```

`down -v` is what distinguishes this from `down`, which keeps the volume and so
changes nothing.

**`failed to bind host port 0.0.0.0:5432: address already in use`**
Something else — usually a native PostgreSQL — already holds the port. Set
`ZEITNOT_DB_PORT=5433` in `.env` and run `docker compose up -d` again.

**Doctor warns that compose and `DATABASE_URL` use different ports**
The two are configured independently, and only doctor compares them. Left alone
this is slow to diagnose, because connecting to the wrong port usually reaches
*some* PostgreSQL and the failure then talks about credentials. Setting
`ZEITNOT_DB_PORT` in `.env` and leaving `DATABASE_URL` to expand it keeps one
place to change.

---

## Configuration

Zeitnot reads `.env` itself, so the file's line endings, quoting, and the order
its lines appear in no longer matter. What still matters is the **shell**: a
variable already exported outranks the file, and the shell does none of that
tidying. Doctor prints which values came from the environment rather than the
file, which is usually the whole answer.

**`Error: DATABASE_URL environment variable is required`**
No file was found and nothing was exported. `zeitnot doctor` prints the absolute
path it looked at — most often the answer is that the command was run from
another directory. Copy the template if you have not:

```bash
cp .env.example .env
```

Point zeitnot at a file somewhere else with `ZEITNOT_ENV_FILE`, or set that to
the empty string to read no file at all.

**`Error: DATABASE_URL contains a control character`**
A carriage return, which is invisible and never valid. Zeitnot strips those from
the file it reads, so this one came from the shell — a file with Windows (CRLF)
line endings that was *sourced*, whose value then outranks the file's. Drop the
shell's copy:

```bash
unset DATABASE_URL
```

Sourcing is no longer necessary at all. If you would rather keep doing it,
convert the file once with `sed -i 's/\r$//' .env`.

**`Error: DATABASE_URL has an empty port`**
`ZEITNOT_DB_PORT` expanded to nothing, so libpq would silently be asked for
5432. Zeitnot resolves the file's own references whatever order they are written
in, so this came from the shell too: the URL was built before the port was set.
`unset DATABASE_URL` and let zeitnot build it from the file.

**`Error: DATABASE_URL still contains an unexpanded variable`**
Single quotes prevent substitution, in the env file as well as in the shell. Use
double quotes.

**`invalid hostPort: 5433`** from `docker compose config` or `up`
`ZEITNOT_DB_PORT` has a stray character — a trailing space, or a zero-width
space picked up by copy-paste. Compose strips whitespace from values in the env
file but *not* from the shell environment, so an exported variable is the usual
culprit, and the error prints nothing visible after the number. Show it exactly:

```bash
printf '%q\n' "$ZEITNOT_DB_PORT"
```

Anything other than a bare number is the problem. `unset ZEITNOT_DB_PORT` and
set it in the env file instead, where the whitespace is tolerated.

---

## Providers

**`Error: embedding provider mismatch: the index was built with ollama/...`**
Working as designed, not a bug. Vectors from different embedding models are not
comparable even at the same width, so retrieval would silently degrade rather
than fail. Either restore the previous `EMBED_PROVIDER` and `EMBED_MODEL`, or
re-embed:

```bash
zeitnot data reembed
```

That reads stored summary text and rebuilds vectors — no Stockfish, no
re-analysis, so it is fast.

**Ollama connection refused, or a model 404**
Check `ollama list` — the chat model and `nomic-embed-text` both need pulling.
Startup checks run before the banner, so this surfaces immediately rather than
after your first question.

**Missing API key for a hosted provider**
`ANTHROPIC_API_KEY` for `CHAT_PROVIDER=anthropic`, `OPENAI_API_KEY` for either
role set to `openai`. Resolved before the welcome banner, so an auth failure is
never revealed only after your first question. Note that a chat provider is
needed only by `zeitnot chat`; `zeitnot data analyze` runs without one.

---

## Something else

Error output is redacted for passwords and API keys before printing, so it is
safe to paste into an issue. Include the output of `zeitnot doctor` — it answers
most of what the bug template asks for, and most reports are unanswerable
without it.
