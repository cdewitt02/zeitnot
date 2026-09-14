# ADR 0001 — Containerize Postgres rather than replace it with SQLite

**Status:** Accepted · 2026-08-25 · amended 2026-08-29 (see [Amendment](#amendment--2026-08-29))

## Context

zeitnot's setup is the main barrier to anyone using it. A new user must install Go, PostgreSQL,
pgvector, Stockfish, and Ollama, then pull ~1.5 GB of models. A cold-start audit measured this at
**45–90 minutes, ending in failure** — the README's ingestion command did not compile, so the run failed
at the first step where the user would have seen the project do anything. pgvector is the worst single
step at 10–20 minutes: it commonly requires building from source against local PG headers.

The goal driving this decision is an easy on-ramp to chatting for *all* users, not only developers.

Two structurally different routes were available.

**Replace the data store.** pgvector is not load-bearing at this project's scale. Only four queries use
the `<=>` operator, all in `internal/db/summaries.go`. A personal archive is roughly 1k–20k games, i.e.
3–60 MB of vectors, over which a brute-force cosine scan in Go runs in 10–20 ms. The existing
`ivfflat WITH (lists = 100)` index (`internal/db/schema.go:71-73`) is *approximate* and at a few hundred
rows can return worse results than a sequential scan while buying no measurable speed. SQLite with a
pure-Go driver plus an in-process vector scan would remove Postgres and pgvector entirely and permit a
single distributable binary.

**Containerize what exists.** A multi-stage app image plus `pgvector/pgvector:pg17` absorbs Postgres,
pgvector, the Go toolchain, and Stockfish in roughly sixty lines of Dockerfile and Compose.
`docs/opensource-readiness/01-roadmap.md` P3-1 already proposed containerizing Postgres, but not the
application, which is the part that also absorbs Go and Stockfish.

Time to first chat was estimated for both, holding ingestion constant at ~12 minutes:

| Route | Total |
|---|---|
| Today | ~69 min |
| Full-stack Compose + hosted providers | ~31 min |
| Binary + SQLite + hosted providers | ~20 min |

## Decision

**Containerize the application and Postgres. Do not replace the data store.** The SQLite route is
shelved, possibly permanently.

The deciding comparison is 11 minutes of user time against roughly 1300 lines of rewritten
`internal/db` — including `player_stats.go` (464 lines) and the dynamic WHERE builder that
`internal/search/filters.go` depends on — plus the migration of existing data and the permanent loss of
real SQL for stats aggregation. Sixty lines of configuration capture most of the available benefit.

Ollama is deliberately **not** containerized. Docker Desktop on Apple Silicon offers no GPU passthrough,
so a containerized Ollama would run CPU-only and far slower than a native install using Metal. Users
wanting local inference run Ollama on the host and the container reaches it via `host.docker.internal`.

## Consequences

**Accepted:**

- Docker Desktop becomes a prerequisite for the documented on-ramp — a ~1 GB install requiring
  virtualization, and on Windows, WSL2. Justified as one guided GUI installer replacing four separate
  installs including a source build, but it is a real cost and not zero.
- Stockfish runs 10–30% slower inside the Docker VM on macOS and Windows, lengthening ingestion — the
  step that already dominates time to first chat.
- The on-ramp is "one install plus two commands," never "double-click an app." A downloadable
  single binary remains impossible while Postgres is required.
- `vector(768)` (`internal/db/schema.go:68`) survives. Under SQLite the declared width would have ceased
  to exist and the embedding-dimension constraint would have dissolved; instead it remains, and is
  managed via the `dimensions` parameter and an index provenance stamp
  ([`docs/multi-provider/01-design.md`](../multi-provider/01-design.md) §2.1).
- Two runtime stories persist for advanced users: containerized app with host Ollama, and fully native.

**Gained:**

- The worst setup step is eliminated for every user, not only new ones.
- `internal/db` is untouched, so no migration and no regression risk in the stats aggregation.
- Postgres remains available for archives large enough to actually need an ANN index.

**Revisit if:** a single distributable artifact becomes a product goal in its own right, or Docker
Desktop's licensing or footprint becomes an obstacle for real users.

*(The third original trigger — `internal/db` being substantially rewritten for another reason — has
since fired and been spent. See the Amendment.)*

## Alternatives considered

- **Compose for Postgres only** (the original P3-1 scope). Rejected: it trades ~27 minutes of pgvector
  pain for ~15 minutes of Docker install and still leaves Go, Stockfish, and Ollama as host steps —
  roughly 57 minutes total, which does not constitute an on-ramp.
- **SQLite alongside Postgres, selected by config.** Rejected: two backends means two setup stories that
  drift, two test matrices, and every `internal/db` change written twice — the exact cost
  `docs/opensource-readiness/02-open-questions.md` Q3 already identifies for dual documentation paths.
- **A hosted backend.** Rejected: converts a local-first tool into a service with ongoing cost and
  custody of users' data.

---

## Amendment — 2026-08-29

**The third revisit trigger fired.** [`docs/python-rewrite/00-plan.md`](../python-rewrite/00-plan.md)
rewrites `internal/db` in full — 1,453 lines — for an unrelated reason. The condition this ADR set for
reopening the SQLite question was met four days after it was accepted.

**Re-evaluated, the decision stands: Postgres. The grounds have changed.**

What moved *toward* SQLite:

- **The deciding number is now sunk.** The original comparison weighed ~11 minutes of user time against
  ~1300 lines of rewritten `internal/db`. Those lines are being rewritten regardless, so the marginal
  cost of the SQLite route did collapse, exactly as anticipated.
- **The brute-force scan gets easier, not harder.** A cosine scan over 3–60 MB is a few lines of numpy.
  Only three queries use the `<=>` operator, all in `internal/db/summaries.go` (`:62`, `:95`, `:156`) —
  the original text said four, which was correct when written.

What moved *away* from it, and decided the matter:

- **The prize was Go-specific and has evaporated.** The SQLite route's headline benefit was permitting
  *a single distributable binary*. Python ships a wheel or a `pipx` install; removing Postgres would no
  longer buy a double-clickable artifact, only the removal of Docker Desktop. That is a difference of
  ~11 minutes, not a difference in kind — and ~11 minutes was already judged insufficient here.
- **The unchanged cost is the one that always mattered.** `player_stats.go` is 464 lines across 7
  aggregate queries, and the permanent loss of real SQL for stats aggregation applies identically in
  Python. Nothing about the language change makes hand-rolled aggregation more attractive.
- **Timing makes it actively harmful.** The rewrite plan's safety property is that both implementations
  run against one corpus and their outputs are diffed. Changing the data store in the same pass would
  mean a failing parity diff could no longer be attributed to the port or to the store. A storage
  migration and a language migration must not share a phase.

**Consequence for the rewrite:** Phase 2 ports `internal/db` to `psycopg3` + `pgvector` against the
existing database, with no schema change. `vector(768)` survives a second time, for a second reason.

The queries survive too, but *not* unedited: psycopg3 does not accept PostgreSQL's native `$1`
placeholders, so all 99 of them plus the 20 built dynamically in `internal/search/filters.go` become
positional `%s`, and `BuildWHERE`'s parameter-numbering machinery is dropped rather than ported. That is
a syntax change, not a semantic one — the queries, the schema, and the stored data are untouched, which
is what this decision turned on.

**Also amended:** this ADR's app image absorbs "the Go toolchain," which is language-specific. The
containerization work it authorizes (readiness P3-1, still unimplemented — there is no
`docker-compose.yml` in the repo) should be built against Python after the rewrite cuts over, not before.

---

## Status note — 2026-09-02

**Half implemented.** `docker-compose.yml` now exists and covers Postgres with pgvector. The
**application image this ADR decided on does not** — there is no `Dockerfile` in the tree — so what ships
today is the "Compose for Postgres only" alternative this ADR rejected under *Alternatives considered*,
and which [`../multi-provider/04-onboarding.md`](../multi-provider/04-onboarding.md) §2.3 rejected again
as not constituting an on-ramp.

The decision is unchanged; only its implementation is outstanding. Tracked in
[`../opensource-readiness/01-roadmap.md`](../opensource-readiness/01-roadmap.md) P3-1, which was
incorrectly marked Done on 2026-08-31 and has been corrected.
