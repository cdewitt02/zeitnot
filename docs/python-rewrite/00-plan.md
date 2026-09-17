# Python Rewrite — Phased Plan

Plan for porting zeitnot from Go to Python without a flag day and without re-ingesting a single game.

> **Executed 2026-08-29. Phases 0–7 are done and this document is now a record rather than a proposal.**
> The decision and what it actually cost are in [ADR 0002](../adr/0002-python-rewrite.md); the outcome of
> each gate is noted inline below. Phase 8 is still outstanding and is the live part of this file.

Each phase is independently reviewable and leaves a working artifact. Phases 0–5 change nothing a user
can see, because the Go program remains the one that runs. The cutover is Phase 7, and it is reversible
until the Go tree is deleted.

## Why

Three drivers, in descending order of how much weight they should carry:

1. **Maintainer fluency.** The roadmap in [`opensource-readiness/01-roadmap.md`](../opensource-readiness/01-roadmap.md)
   has P0 through P4 outstanding. On a solo-maintained project, whether that work happens at all is a
   function of how fast the maintainer moves in the codebase. This is the strongest argument and it
   should be stated plainly rather than dressed up as an architecture decision.
2. **Ecosystem fit.** Chess (`python-chess`) and RAG tooling are Python-first. Two packages —
   `internal/engine` and `internal/render` — are largely replaced by mature libraries rather than
   ported.
3. **Contributor adoption.** Real but weakest. A Python repo with no LICENSE attracts exactly as many
   contributors as a Go one with no LICENSE. **Language is second-order behind P0-2, P1-2, and P1-3**,
   and this plan should not be used to justify deferring those.

**What Go was actually good at here, for the record:** compiler-verified refactors across a wide domain
model with almost no test coverage. That safety net disappears. Phase 0 and the ported test suites are
what replace it — not optimism.

### What this does not improve

**Time to First Chat is unaffected. Do not argue for this rewrite on that basis.**

Worth stating plainly, because every other document in `docs/` is organized around that metric
([`04-onboarding.md`](../multi-provider/04-onboarding.md) §1,
[`CONTEXT.md`](../../CONTEXT.md)) and [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md) turned
on an 11-minute difference in it. A reader arriving from those will assume a rewrite moves the number.
It does not:

- **The documented on-ramp (row 6, ~31 min) is exactly neutral.** That path is full-stack Compose, so
  the language lives inside the image and is invisible to the user. Docker Desktop plus two commands,
  either way.
- **The native path is a wash.** Installing Go is replaced by installing Python and managing a
  virtualenv. `uv` makes that quick and an interpreter is often already present — but "already present"
  usually means a system interpreter that should not be installed into, which is a support burden Go
  does not have.
- **Ingestion is untouched at ~12 minutes**, and it dominates every row in the table.

The two changes that *would* move the metric — full-stack Compose (readiness P3-1) and the
double-analysis fix ([`ingestion-performance.md`](../ingestion-performance.md) §1) — are both deferred
and both language-independent. Neither becomes easier or harder in Python.

The one real user-visible gain is ergonomic, not temporal: `zeitnot data analyze magnus 2026 08` rather
than `go run ./cmd/data analyze magnus 2026 08`, and a `pipx` / `uv tool install` route that does not
require the user's toolchain to be a *build* toolchain.

## The fact that shapes every phase

**The database is language-neutral and survives untouched.** Schema, SQL, embeddings, and the existing
corpus carry over with no migration and no re-analysis. [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md)
holds. So does every document in [`multi-provider/`](../multi-provider/) — that is architecture, not Go —
and so does the glossary in [`CONTEXT.md`](../../CONTEXT.md).

This makes the rewrite a **strangler**, not a big bang: the Python code points at the same
`DATABASE_URL` and the same `vector(768)` column, so at every phase both implementations can be run
against one corpus and their outputs diffed. Use that. It is the difference between a port you can
verify and a port you have to trust.

## Scope

Out of scope, listed so they are not smuggled in:

- **No schema changes.** Not one. A rewrite that also migrates the database cannot be bisected.
- **No change of data store.** This plan fires [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md)'s
  third revisit trigger — `internal/db` being substantially rewritten for another reason — and that
  revisit has been carried out and closed in the ADR's 2026-08-29 amendment. Postgres stands, on
  changed grounds: the single-binary prize that motivated SQLite was Go-specific and does not survive
  the move to Python, while the 464-line stats-aggregation cost does. Do not reopen it inside a phase.
- **No re-ingestion.** If a phase appears to require it, something has diverged — stop and find out what.
- **No feature work**, no new providers, no prompt improvements. Every behavioral difference must be a
  bug, so that any diff is a signal.
- **No ingestion performance work.** [`ingestion-performance.md`](../ingestion-performance.md) §1 is a
  real ~2× win and it is tempting to fold in while touching `AnalyzeGame`. Don't — see Phase 8.

## Parity over correctness

**The port reproduces known-wrong behavior verbatim. Bugs are preserved, marked, and fixed after
cutover — never during.**

This is not deference to the old code. It is what makes every other phase verifiable: the safety
property of Phases 0–6 is that *any* diff is a bug. Permitting deliberate diffs means every future diff
needs adjudication — "is this the intentional one?" — and that judgment gets made at 11pm under time
pressure. One ambiguous diff costs more than the defect it was fixing.

The tempting exception is a defect that is provably unreached on the current corpus, where fixing it
changes no output today. **Reject it.** "Unreached on today's corpus" is not "unreachable" — the first
user with different games hits the branch, the two implementations diverge, and nothing fails, because
the goldens were captured from a corpus where it never fired. That is this project's characteristic
failure mode — silence, where an error status parses cleanly into an empty result and the program
reports success — reintroduced at the exact point built to prevent it.

**What parity is measured on.** Observable outputs, not internal structure. The unit is what leaves the
system: **Game Summaries, `moves` rows, query result sets, and Assembled Prompts.** Function signatures,
SQL placeholder syntax, control flow, and package layout are free to change where the target language
calls for it — and in places they must (see Phase 2). A port that preserves internal shape at the cost
of idiomatic Python has misread this rule.

**Convention.** Each preserved defect carries a `# PARITY:` comment naming what is wrong and why it is
being kept, is listed below, and becomes a Phase 8 entry with its own verification.

**Carve-out 1.** A defect that corrupts data or crashes is fixed immediately, not preserved. Parity is a
means to a verified port, not a commitment to reproduce a crash.

**Carve-out 2 — non-determinism cannot be ported.** A randomized order has no golden to capture and no
Python equivalent to write. Where the Go implementation is non-deterministic, **it is made deterministic
in the Go tree first**, before Phase 0 captures anything. That is not Go-specific work exempted by
sequencing constraint 2 — it is domain logic that must exist in both implementations, and doing it in Go
first is what makes the goldens capturable at all. Fixing it only in Python would create a permanent diff
someone has to remember to excuse.

Rejected alternative: a normalizing comparator that sorts lines before diffing. It would hide genuine
ordering bugs in the port behind the same normalization, and would leave the underlying
eval-comparability defect unfixed.

### Known preserved defects

| # | Site | Defect | Reached on current corpus? |
|---|---|---|---|
| 1 | `internal/summary/generator.go:189` (`weakestPhase`) | `"Endgame was weakest"` is the `else` catch-all, so any *tie* between phase averages is misreported as an endgame weakness | No — 0 of 74 games |
| 2 | ~~`internal/summary/generator.go:52-60` (`ExtractSummaryData`)~~ | ~~**A drawn game is summarized as a loss.**~~ **FIXED 2026-08-31** in `zeitnot/summary.py:43`, post-cutover as its own change, per the plan below. Covered by `tests/test_summary.py` | Was: yes — 5 of 74 |

**Defect 2, found during Phase 3.** It is the more serious of the two, because unlike #1 it is
*reached*: it is visible in the Game Summaries, in the embeddings built from them, in the win/loss/draw
tallies `prompts.go` derives by reading summary text, and it makes four of `detectPattern`'s ten
verdicts unreachable. Preserved exactly regardless — fixing it changes the summary text, which changes
the embedded text, which makes every stored vector stale relative to its own source. Its Phase 8 entry
carries the same regeneration pass #1 does.

Note `player_stats.draws` is computed independently in SQL and is *correct*; only the summary text is
wrong. The prompt therefore states an accurate draw count in one section and an inaccurate one in any
section derived from summaries.

**Already fixed rather than preserved**, under carve-out 2: seven non-deterministic map iterations in
`internal/chat/router.go` (see Phase 0), plus four in `internal/search/parser.go` found while writing
the capture. The parser sites matter for the same reason: the keyword loops take the first match and
stop, so a query matching two keywords in one map resolved differently between runs, and that reaches
retrieval and therefore the prompt. They could not have been ported.

Add rows as the port surfaces them. An empty row is a claim that the package was clean; make it
deliberately.

## Verification reality check

The inverse of the situation the Go migration faced. There are now **2,747 lines of tests across seven
files**: `internal/config`, `internal/search` (parser, hybrid), `internal/render` (render, stream),
`internal/llm` (ollama, anthropic, openai), and `internal/api`. These are executable specifications and
they are the port's single best asset — most translate to `pytest` close to mechanically.

The gap is where it has always been: **`internal/db`, `internal/chat`, `internal/engine`, and
`internal/summary` have no tests at all**, and those are exactly the packages where a divergence is
silent rather than loud. Phase 0 exists because of that sentence.

## Sequencing constraints

Four orderings that cut across the phases:

1. **Phase 0 before any Python is written.** Goldens captured from a Go tree that has already been
   modified prove nothing. Capture first.
2. **The rewrite before P1-1, P1-2, P1-3, and P3-1.** The `gofmt` sweep, the Go CI workflow, and a
   CONTRIBUTING documenting `go vet` are all language-specific and would be thrown away. So is the
   containerization work: [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md)'s app image
   absorbs *the Go toolchain*, so a Dockerfile written now is rewritten at cutover. The roadmap's own
   constraint — *do the churn while there is nothing to churn* — applies with far more force to a
   language change than to a reformat.
3. **P0-2 (LICENSE) now, regardless.** It is language-neutral and it is the actual legal blocker on
   anyone using or contributing to this project.
4. **The rewrite before soliciting outside contributors.** Same reasoning as P1-1, one order of
   magnitude larger.

## What shrinks and what does not

Measured at the current commit: **7,639 non-test lines, 2,747 test lines, 50 files.**

| Package | Go LOC | Expectation | Why |
|---|---:|---|---|
| `internal/llm` | 1,952 | **~600** | Exists to normalize three SDKs; the Python SDKs are first-party and equally good. The *abstraction* stays — see Phase 4 |
| `internal/db` | 1,453 | ~800 | SQL carries over verbatim; only row scanning shrinks |
| `internal/chat` | 1,195 | ~800 | Real domain logic. Ports structurally 1:1 |
| `internal/search` | 863 | ~600 | Real logic, and it has tests |
| `cmd/data` | 586 | ~350 | `typer` + `ThreadPoolExecutor` |
| `internal/render` | 296 | **~60** | `rich` replaces glamour and is better |
| `internal/config` | 292 | ~200 | Mechanical, tests included |
| `internal/models` | 260 | ~150 | structs → dataclasses |
| `cmd/chat` | 235 | ~150 | `prompt_toolkit` improves it |
| `internal/summary` | 228 | ~200 | Pure functions. **Must be byte-identical** |
| `internal/engine` | 171 | **~60** | `python-chess`'s `SimpleEngine` replaces the UCI wrapper |
| `internal/api` | 108 | ~80 | `requests`. Freshly rewritten, so cheap to lose |

**Target: ~3,500–4,500 lines of Python.** Roughly 2–3 weeks of focused solo evenings to parity including
tests; 8–12 concentrated days if some coverage loss is accepted up front.

---

## Phase 0 — Goldens and the parity harness

**Prerequisite, not Python.** Small, and everything after it depends on it.

Two packages produce values that are *stored* and then compared against future values. If the Python
port produces even slightly different output, the existing corpus and the new rows stop being
comparable — and nothing errors.

Capture from the current Go tree, into `testdata/golden/`  — *historical: three of these five were retired on 2026-09-15, see [ADR 0003](../adr/0003-retire-the-parity-goldens.md)*:

- **Summary text** for every stored game — `summary.ExtractSummaryData` + `GenerateSummary`
  (`internal/summary/generator.go:39,144`). The summary text *is* the embedded text, so divergence
  makes every stored embedding stale relative to its own source.
- **Eval helpers** over a fixed position set — `getEvaluation`, `normalizeEval`, `classifyMove`
  (`stockfish.go:54,65,72`). These determine stored CPL and move classification.
- **Query classification** over a fixed question set — `ClassifyQuery`, `ExtractMentionedOpenings`
  (`classifier.go:49,247`).
- **Query parsing** — `QueryParser.Parse` (`parser.go:188`) over the same question set.
- **Assembled Prompts** — `QueryRouter.BuildPrompt` (`router.go:137`) for each question against the
  live Corpus, with `ZEITNOT_DEBUG_PROMPT` doing most of the work already. See
  [`CONTEXT.md`](../../CONTEXT.md) for why the Assembled Prompt is a distinct term from the Game
  Summaries it contains — the parity targets in Phases 3 and 5 are different artifacts.

Write a small comparison script that runs the Python equivalents against the same fixtures and diffs.
It is used by Phases 3 and 5 and then by the cutover.

### When the capture freezes

The Go tree is being changed right up to this point, so "the reference" needs a definition sharper than
*whatever HEAD was when the script ran*.

- **Capture at a named commit and record the SHA here.** Gate it on
  [P0-8](../opensource-readiness/01-roadmap.md) having landed, since that changes the Assembled Prompt.
  Any behavior change after that SHA invalidates the goldens and forces a deliberate recapture — which
  is the point. A golden regenerated from the current tree always matches the current tree and proves
  nothing.
- **Freeze `ANALYSIS_DEPTH`** ([`ingestion-performance.md`](../ingestion-performance.md) §2) for the
  duration. It is a hardcoded constant today, and changing it rewrites every `cpl` and `classification`
  — a plausible "small tuning tweak" that would quietly void the Phase 3 goldens.

**The two golden sets have different validity conditions, and the plan treats them separately:**

| Set | Scope | Survives a growing Corpus? |
|---|---|---|
| Phase 3 — summaries, eval helpers, move rows | Keyed per game UUID | **Yes.** Each game's values depend only on that game |
| Phase 5 — Assembled Prompts | Whole-Corpus | **No.** Every added game shifts win rates, CPL averages, and the comparison strings built from them |

So the Phase 5 goldens record a **Corpus fingerprint** in their header — the game count plus a hash of
the sorted game UUIDs. The harness compares fingerprints first and refuses to run when they differ,
reporting "corpus changed, recapture required" instead of emitting a diff that looks like a port bug.
No database contents are committed; the fingerprint is enough to tell a stale reference from a real
regression.

### Prerequisite: the Go prompt was not deterministic (fixed 2026-08-29)

Two identical runs of the same binary against the same corpus produced different prompts. Go randomizes
map iteration, and seven sites in `internal/chat/router.go` let that reach the output:

| Site | How it leaked |
|---|---|
| `:183` colors, `:201` time classes, `:241` terminations | Ranged the map and emitted directly |
| `:283` best/worst time control, `:315` best/worst rating band | Ties in win rate resolved by iteration order |
| `:342` opening entries | `sort.Slice` is not stable and the comparator was not a total order, so equal game counts reordered |
| `:521` mentioned openings | Ranged the map and emitted matches directly |

All seven now route through a `sortedKeys` helper, and `:342`'s comparator breaks ties on ECO code.
`:218` already did this correctly and was the template. Verified: three consecutive runs now produce
byte-identical prompts.

**This was a live defect, not merely a porting obstacle.** [`03-eval-plan.md`](../multi-provider/03-eval-plan.md)
depends on runs being comparable across time; a prompt that reorders between runs breaks that for
reasons unrelated to the model. Capturing goldens before this fix would have frozen the randomness into
the reference.

**Related, filed separately:** the same section exposed
[readiness P0-8](../opensource-readiness/01-roadmap.md) — Chess.com termination strings embed the
opponent's username, so the "Game endings" section ships 51 third-party handles to a hosted provider on
every aggregate query. Fixing it changes prompts, so it lands **before** Phase 0's capture or **after**
cutover, never in between.

**Effort.** M. **Verification.** The harness diffs Go against Go and reports zero.

**Rollback.** Nothing to roll back; adds files only.

---

## Phase 1 — Skeleton, models, config

Project scaffolding and the two most mechanical packages.

- `pyproject.toml`, a `zeitnot/` package mirroring `internal/` one-for-one, `ruff` + `mypy --strict`
  configured from the first commit. **Strict from the start**: gradual typing that is retrofitted never
  gets retrofitted, and the Go compiler is what is being replaced.
- `zeitnot/models/` — the ~10 types in `internal/models/`, as dataclasses. `GameRecord`'s 20+ fields are
  the reason `mypy --strict` is not optional.
- `zeitnot/config.py` — port `Resolve`, `NewChatModel`, `NewEmbedder`, `Summary`, `Preflight`,
  `CheckIndex` (`internal/config/`). Provider defaults, the `OLLAMA_EMBED_MODEL` alias scoped to Ollama,
  the positional-argument precedence rule.

**Verification.** Port `internal/config/config_test.go` — all 12 precedence cases and both error cases.
It is a table over an env map with no network, and it is the highest-value test in the config layer for
the same reason it was in Go: backward compatibility lives there.

**Rollback.** Delete the directory. Nothing references it.

---

## Phase 2 — Database layer

`psycopg3` + `pgvector`'s Python bindings, against the **existing** database.

- The SQL moves across nearly intact — the queries themselves are the package's value and are not
  Go-specific — but **not verbatim**. pgx uses PostgreSQL's native `$1, $2` placeholders and psycopg3
  does not support them, taking `%s` positional or `%(name)s` named instead. That is 99 `$N`
  occurrences across `internal/db` plus 20 dynamically constructed `$%d` in
  `internal/search/filters.go`.
- **`BuildWHERE(startParam int)` loses its numbering machinery, deliberately.** `startParam` and
  `paramNum` exist solely to thread a parameter *index* through composable fragments so filters can be
  appended to a query that already has parameters. Under positional `%s` there are no indices —
  ordering is implicit in the argument sequence. The Python version returns fragments plus an ordered
  argument list and nothing else.

  This is the clearest case of parity being measured on **outputs, not structure**: a faithful port here
  would carry dead complexity forever, in the one function [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md)
  singled out as load-bearing. The result-set assertion below is what proves it correct.
- Named placeholders (`%(p1)s`) were the alternative — they would have preserved the numbering scheme
  exactly. Rejected: it keeps a mechanism whose only purpose was satisfying a constraint that no longer
  exists, and makes every query noisier to read.
- **No `%%`-escaping sweep is needed.** psycopg3 requires literal `%` in SQL to be doubled, but every
  `%` wildcard here lives in the *argument* (`*f.ECOPrefix+"%"`, `"%"+name+"%"`), never in the SQL text.
- Port read paths first — `GetGame`, `GameExists`, `FindSimilarGamesWithFilters`, `GetPlayerStats`,
  `GetIndexMeta`, `EmbeddingDimensions` — then writes.
- Keep `Migrate` idempotent and identical, including `CREATE EXTENSION IF NOT EXISTS vector`.

**Verification.** Against the live corpus, not fixtures: row counts match `psql`, a known game round-trips
field for field, and `FindSimilarGamesWithFilters` returns the **same neighbours in the same order** as
the Go implementation for a fixed query vector. Vector ordering is where a float conversion bug would
hide.

**Backward compatibility.** Read-only until the write paths are verified. The Go tree still owns
ingestion.

**Rollback.** Delete. The database is untouched by definition.

---

## Phase 3 — Engine and summary: the parity phase

**The phase where a rewrite can silently corrupt a corpus**, and the reason Phase 0 exists.

- `zeitnot/engine.py` — `python-chess`'s `SimpleEngine` replaces `StartEngine`/`StopEngine`/
  `AnalyzePosition` (`stockfish.go:11,19,34`) outright. What must be ported *exactly* is the arithmetic:
  `getEvaluation`, `normalizeEval`, `classifyMove`, and the structure of `AnalyzeGame`, including its
  terminal-position branch.
- `zeitnot/summary.py` — `ExtractSummaryData`, `GenerateSummary`, `classifyGameLength`, `weakestPhase`,
  `detectPattern`. Pure functions over data, no I/O, no LLM.

**Verification.** The Phase 0 goldens, byte for byte. A summary that differs by one space is a failure,
not a nit — it means the stored embedding no longer corresponds to its text. Additionally: re-analyze a
handful of games with the Python engine and diff the `moves` rows — `cpl`, `classification`,
`evaluation`, `best_move` must be identical.

**Amended 2026-08-29: that diff is against Go's output, not against the database.** The stored `moves`
rows were written by a Stockfish build that is no longer the one on PATH, and **the current Go tree does
not reproduce them either** — on the two shortest games, 12 of 12 and 17 of 17 evaluations differ, along
with 3 of 17 classifications. So "does Python match the corpus?" has no answer for any implementation,
and the question the port is actually on the hook for is "does Python match Go, given the same engine?"
That is what decides whether new rows from Python are interchangeable with new rows from Go.

Phase 0 therefore also captures `analysis.json`: Go's re-analysis of the five shortest games at the
capture commit. It is the one golden keyed to a *Stockfish version* rather than to a commit, and the
version is recorded in the manifest.

Two fields survive the version change and are still checked against the live corpus: `played_move` and
`fen_before` come from PGN parsing and board replay, not from the engine. That check earned its keep —
it caught python-chess's `fen()` defaulting to `en_passant="legal"`, which omits the en-passant square
unless a capture is available, where Go prints it after any double pawn push.

**Note.** Stockfish at a fixed depth on a fixed position is deterministic *for a given build*, so
"identical" is a reasonable bar rather than an aspirational one. If it cannot be met, the cause is in
the ported arithmetic and it must be found here, not later.

### Float inventory (audited 2026-08-29)

The concern that motivated this audit — cross-language float *formatting* drift — does not apply to this
phase. **No float reaches a string anywhere in the Phase 3 surface.** `GenerateSummary`
(`generator.go:144-159`) uses `%s` and `%d` exclusively.

Floats appear in exactly three places, none of them printed into stored text:

| Site | Use | Port risk |
|---|---|---|
| `generator.go:174-183` (`weakestPhase`) | Three `float64` divisions, **comparison only** | None. Same int→float conversion and IEEE-754 division in both languages, so the bits and the `>` results are identical |
| `worker.go:45,48` (`avgCPLWhite/Black`) | Stored to `avg_cpl_white/black` | None. The columns are `REAL` (`schema.go:26-27`), so Postgres narrows to float32 on insert — a language-neutral loss that already happens |
| `main.go:370,383` | Progress output (games/sec, win %) | None. Never stored, never embedded |

Float *formatting* is confined to the prompt path — 17 `%.1f` sites in `router.go` plus `main.go:271` —
which is Phase 5's problem, not this one. And it is not much of a problem: Go's `fmt` and Python's format
spec both round the exact binary value half-to-even, verified identical across half-way cases
(`0.25 → 0.2`, `1.25 → 1.2` in both). **No formatting-normalization step is required in either phase.**

### One latent defect this audit found

`weakestPhase` returns `"Endgame was weakest"` from its `else` branch, so it is the catch-all for *any*
tie rather than a verdict about the endgame. Reconstructing the function from the stored `moves` rows
across all 74 games: 53 endgame verdicts are strictly correct, 20 middlegame, 1 opening, and **zero
reach the tie-fallback**. The defect is real and unreached.

The port must reproduce it exactly regardless — see **Parity over correctness** above, where it is
recorded as preserved defect #1.

**Rollback.** Delete. Nothing writes yet.

---

## Phase 4 — Provider adapters

**Keep the abstraction.** [`multi-provider/01-design.md`](../multi-provider/01-design.md) reasons about
the chat/embedding split, sentinel errors, retry ownership, and index provenance — none of which is
about Go. Reaching for a framework here would discard a design that is better than the default and
replace it with more code you do not control.

Port, per provider (Ollama, Anthropic, OpenAI):

- The `ChatModel` / `Embedder` split — Anthropic implements only the first.
- Sentinel errors and status classification (`internal/llm/errors.go`).
- **Retry ownership: the SDK retries for hosted providers, the adapter never layers a loop on top**
  ([`01-design.md` §7.2](../multi-provider/01-design.md)). This is still the single most likely
  implementation mistake, and it is easier to make in Python where the SDK defaults are less visible.
- Streaming, message validation, system-prompt placement, `dimensions=768` on OpenAI embeddings,
  and preflight.

**Verification.** Port `llmtest` — the shared conformance suite is the artifact that keeps adapters
honest, and it is the most valuable single file in the Go tree. `pytest` parametrization over the same
scenario table, with `responses`/`respx` in place of `httptest.Server`. The streaming equivalence
assertion — deltas concatenate to exactly the buffered text — must survive the port intact.

**Add here what the cutover deliberately does not test.** Phase 7 gates on prompt parity alone, which
covers everything the provider *reads* but nothing about how the request is *shaped*. So this suite must
assert on the outbound request itself: the model ID actually sent, `max_completion_tokens` present and
`max_tokens` absent for OpenAI, the system prompt as a top-level parameter for Anthropic and as
`messages[0]` elsewhere, and **no `temperature` field when the caller did not set one** — a stray
default here would change answer distributions invisibly, since prompt parity would still pass. The Go
suite already asserts most of this; the temperature case is worth adding on both sides.

**Rollback.** Delete the package.

---

## Phase 5 — Search and chat

The largest phase, and the one with the least existing test coverage.

- `zeitnot/search/` — `QueryParser.Parse`, `GameFilters.BuildWHERE`, `HybridSearcher.Search`. Port
  `parser_test.go` and `hybrid_test.go` alongside.
- `zeitnot/chat/` — `ClassifyQuery` and its five predicates, `QueryRouter` and its eleven `write*`
  methods, `PromptBuilder`, and `Service` with history truncation and `AskStream`.

**Verification.** The parity target is **the same Assembled Prompt, not the same answer.** Answers are
non-deterministic; the Assembled Prompt is required not to be ([`CONTEXT.md`](../../CONTEXT.md)). Diff
`BuildPrompt` output against the Phase 0 goldens for every question in the set. A prompt that matches
means retrieval, routing, classification, and stats formatting all match — one diff covers the whole
path.

**There is no allowance for a legitimate difference here.** The seven ordering defects that would have
produced one were fixed in the Go tree before Phase 0 (see that phase). If a new ordering difference
appears, it is a bug in the port, not a known quirk to excuse.

**Rollback.** Delete.

---

## Phase 6 — Entrypoints

- `zeitnot data` — `analyze`, `refresh-stats`, `reembed` via `typer`. The worker pool becomes a
  `ThreadPoolExecutor`: the workload is subprocess I/O, so the GIL is not a factor, but **the
  fail-fast semantics must be preserved** — first error cancels the run (`worker.go:165-175`), each
  worker owns its own engine process, and ingestion stays resumable via the already-analyzed filter.
- `zeitnot chat` — the REPL, with `rich` for markdown rendering and `prompt_toolkit` for input.
  `/clear`, `exit`/`quit`, and Ctrl-C behave identically. Streaming-then-repaint becomes `rich.live`;
  the non-styled path (a pipe, a file, `NO_COLOR`) must still print raw markdown once with no cursor
  escapes.
- Port the freshly-hardened Chess.com client (`internal/api/data.go`): status checks per class, the
  `User-Agent`, the explicit timeout, and `data_test.go` as its spec.

**Verification.** Ingest a month into a **scratch database** with the Python tree and diff every table
against the same month ingested by Go. This is the first phase that writes, and the scratch database is
what makes that safe.

**Rollback.** The Go entrypoints still exist and still work.

---

## Phase 7 — Cutover

1. **The gate: Assembled Prompt parity, and nothing else.** For all ten frozen questions in
   [`multi-provider/03-eval-plan.md`](../multi-provider/03-eval-plan.md), at the recorded Corpus
   fingerprint, both implementations must produce byte-identical Assembled Prompts. This is objective,
   automatable, and sufficient — every input the Chat Provider receives is the prompt, so a matching
   prompt leaves nothing for the port to have gotten wrong downstream.

   **Comparing answers is explicitly not a gate.** No call site sets `ChatRequest.Temperature`, so every
   provider samples at its own default and the *Go* implementation already answers the same question
   differently on consecutive runs. Diffing Python's answers against Go's compares two samples from one
   distribution and dresses the difference up as a finding.

   Do read a couple of answers end to end — but as a smoke test with **no pass/fail authority**, checking
   that the process runs, streaming renders, and nothing crashes. Name it that in the release notes so
   nobody later mistakes it for evidence of answer quality.
2. Move the Go tree to `legacy/` in one commit, delete it in a later one. Two commits, so the revert is
   trivial for as long as anyone wants it. *(Done. Moved in `71211ca`; deleted 2026-08-31, ahead of the
   "one release" wait ADR 0002 described — see that ADR's 2026-08-31 amendment.)*
3. Rebuild what was deliberately skipped: README quick-start, CONTRIBUTING with a Python testing matrix,
   and CI running `ruff`, `mypy --strict`, and `pytest` — the P1-2 work, now against the right language.
4. Record the decision as **ADR 0002**. It meets the bar: hard to reverse, and the reasoning is
   non-obvious enough that a contributor will otherwise ask.

**Rollback.** Until step 2's second commit, `git revert` and the Go tree is back.

---

## Phase 8 — Deferred, explicitly not in this rewrite

- **The double-analysis fix** ([`ingestion-performance.md`](../ingestion-performance.md) §1). A ~2×
  ingestion win and the largest single improvement available anywhere in the docs — do it in Python,
  *after* Phase 7, as its own change with its own verification. Folding an optimization into a port
  means a diff that fails can no longer be attributed.
- **Every entry in "Known preserved defects."** Each is fixed post-cutover as its own change with its
  own verification. **Defect 2 is done** (2026-08-31); Defect 1 remains. Fixing either makes the stored corpus internally inconsistent until summaries are
  regenerated — which is cheap, since `ExtractSummaryData` needs only `games` and `moves`, both stored,
  so no Stockfish re-analysis is involved. That regeneration pass is itself a Phase 8 item, and it must
  be followed by `zeitnot data reembed`: the summary text *is* the embedded text.

  **Defect 2 first.** It is reached on 5 of 74 games where #1 is reached on none, it corrupts the
  win/loss/draw tallies `prompts.py` derives, and it makes four of `detectPattern`'s ten verdicts
  unreachable — so fixing it changes more stored text than any other item here, and every later change
  is cheaper once the regeneration pass has been done once.

- **The phase boundary (readiness [Q8](../opensource-readiness/02-open-questions.md)) — DONE
  2026-09-14.** It was a bug: the loop index is a ply and the constants are full moves, so both
  boundaries were halved. Taken *ahead* of the capture tool below — now dropped entirely — because the
  goldens that tool would verify against were themselves stale — the replacement verification is corpus-free tests in
  `tests/test_summary.py`, each checked against the old code to confirm it fails there. Stored summaries
  need the regeneration pass plus `zeitnot data reembed`; a fresh clone does not.

- ~~**Regenerating the goldens as a Python-native harness.**~~ **Dropped 2026-09-15.** This item said
  the capture tool gated both Preserved Defect fixes, because both change Game Summary text and would
  therefore need a recapture. The corpus-derived goldens were retired instead
  ([ADR 0003](../adr/0003-retire-the-parity-goldens.md)): their remaining value was cross-language, Go
  was gone, and the corpus they described had grown from 74 games to 195 and was no longer a superset of
  the capture. There is nothing to recapture and nothing gated on it.

  What replaced each of them is in the ADR's table. The two worth naming here: the whole assembled
  prompt is pinned byte-for-byte in `tests/test_prompt_snapshot.py` against committed synthetic
  fixtures, and the Go capture's re-derivation check — every stored summary regenerated from stored
  `games` and `moves` and compared against `summary_text`, 74 of 74 at the capture commit — is
  `tests/test_summary_corpus.py`, which needs the database and no golden.

- **The Game Summary regeneration pass.** The real blocker in front of the Preserved Defects, and the
  one this item was standing in for. Changing summary text makes every stored vector stale against its
  own source, and no command repairs that: `zeitnot data reembed` rebuilds vectors from the stored text,
  and `zeitnot data analyze` skips games it has already seen. It needs only `games` and `moves`, both
  stored, and no Stockfish.

- **[Readiness P0-8](../opensource-readiness/01-roadmap.md).** Deferred to *after* cutover under the
  plan's own rule — a change to the Assembled Prompt lands before the capture or after the cutover,
  never in between — and it is now the largest outstanding correctness-and-disclosure item. It was also
  why two of the five goldens were gitignored, which is a large part of why they are now retired.
- **`ANALYSIS_DEPTH` as configuration**, **`NumSimilar`/`DetailLimit` token budgeting**, and the
  **unused Chess.com accuracy data** — same reasoning.
- **Voyage adapter**, **tool calling**, **parameterized `vector(N)`** — unchanged from
  [`multi-provider/02-migration-plan.md`](../multi-provider/02-migration-plan.md) Phase 6.
- **Anything that changes the schema.**

---

## Phase summary

| Phase | Deliverable | Effort | User-visible? | Rollback |
|---|---|---|---|---|
| 0 | Goldens + parity harness | M | No | Delete files |
| 1 | Skeleton, models, config | M | No | Delete package |
| 2 | Database layer | L | No | Delete; DB untouched |
| 3 | Engine + summary, byte-identical | L | No | Delete |
| 4 | Provider adapters + conformance suite | L | No | Delete |
| 5 | Search + chat | L | No | Delete |
| 6 | Entrypoints; first writes, scratch DB | L | No | Go tree still works |
| 7 | Cutover, docs, CI, ADR 0002 | M | **Yes** | Revert until Go is deleted |

Phases 2, 3, and 4 are independent of one another and can be reordered. Phase 5 depends on 2 and 4;
Phase 6 depends on everything.

## Commitment and abort

Nine phases and two to three weeks of evenings on a solo project with no deadline. Nothing external will
force the go/no-go call, so it is pre-committed here rather than left to be decided by feel at the point
where it is hardest to make.

### The abort gate is Phase 3

**If Phase 3 cannot reach byte-identical goldens within one weekend of debugging, stop.**

Phase 3 is the cheapest phase that tests the plan's core premise — that two implementations can be
*proven* equivalent on real outputs rather than believed equivalent. If the Game Summaries and `moves`
rows will not match, every later phase's verification rests on nothing and the remaining work is a port
on faith with no suite underneath it. That verdict arrives early: before the two largest phases, and
before a single row is written.

Secondary trigger, one phase later and more expensively: **if Phase 5's Assembled Prompt diff cannot be
closed**, the same reasoning applies.

### Past Phase 4, the project is committed

Phases 0 and 3 are worth doing whether or not the rewrite proceeds (see below), so the first genuinely
irreversible investment is Phase 4. Beyond it, finish. A repo carrying a half-ported `zeitnot/` package
that nobody deletes and nobody completes is a worse outcome than either finishing or having stopped at
Phase 3 — it is the state this section exists to make impossible.

### What stands if it aborts

Stopping is not "nothing gained," and knowing that in advance is what makes the gate usable:

- **The seven determinism fixes stay.** They repaired a live eval-comparability defect
  ([`03-eval-plan.md`](../multi-provider/03-eval-plan.md)) and were never Go-specific.
- **The goldens stay**, and become the regression suite `internal/summary`, `internal/engine`, and
  `internal/chat` have never had. That is arguably the most valuable artifact this plan produces, and it
  survives independently of the rewrite.
- **[P0-8](../opensource-readiness/01-roadmap.md) stays fixed**, having landed before the capture.
- The Python tree is deleted — not parked — and the queue resumes at P1-1 → P1-2 → P1-3 → P3-1,
  unchanged from today.

**Consequence worth reading twice: Phase 0 and Phase 3 should be executed regardless.** They are the
cheapest way to buy a regression suite for the four untested packages, and they happen to also be the
gate. Starting is therefore a small decision, not a large one.

## The two irreducible risks

1. **Silent corpus divergence** — a summary or an eval that differs slightly, poisoning comparability
   between old and new rows with no error anywhere. Mitigated by Phase 0 and gated in Phase 3. This is
   the risk the plan is shaped around.
2. **Prompt drift** — retrieval or routing that differs subtly, producing worse answers with nothing
   failing. Mitigated by diffing assembled prompts rather than answers in Phase 5.

Both share a property worth naming: **they fail quietly.** That is the same failure mode as the two
worst defects the audit found — `([], nil)` from the Chess.com client and `(nil, nil)` from the old
embeddings client. This project's characteristic bug is silence, and the plan should be read with that
in mind.
