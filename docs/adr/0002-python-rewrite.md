# ADR 0002 — Rewrite the application in Python, keeping the database untouched

**Status:** Accepted · 2026-08-29 · amended 2026-08-31 (see [Amendment](#amendment--2026-08-31))

## Context

zeitnot was written in Go. The rewrite plan is
[`docs/python-rewrite/00-plan.md`](../python-rewrite/00-plan.md); this ADR records the decision and what
executing it actually cost, because a contributor arriving at `legacy/` will otherwise ask.

Three drivers, in descending order of how much weight they carried:

1. **Maintainer fluency.** [`opensource-readiness/01-roadmap.md`](../opensource-readiness/01-roadmap.md)
   has P0 through P4 outstanding. On a solo-maintained project, whether that work happens at all is a
   function of how fast the maintainer moves in the codebase. This is the strongest argument and it is
   stated plainly rather than dressed up as an architecture decision.
2. **Ecosystem fit.** Chess and RAG tooling are Python-first. Two packages were largely *replaced* by
   mature libraries rather than ported: `internal/engine`'s UCI wrapper became `python-chess`'s
   `SimpleEngine`, and `internal/render`'s 296 lines of hand-rolled soft-wrapping stream writer became
   `rich.live`.
3. **Contributor adoption.** Real but weakest. A Python repo with no LICENSE attracts exactly as many
   contributors as a Go one with no LICENSE.

**What Go was actually good at here, for the record:** compiler-verified refactors across a wide domain
model with almost no test coverage. That safety net is gone. `mypy --strict` from the first commit and
the goldens are what replace it.

### What this did not improve

**Time to First Chat is unchanged.** Every other document in `docs/` is organized around that metric and
[ADR 0001](./0001-postgres-in-docker-over-sqlite.md) turned on an 11-minute difference in it, so a reader
arriving from those will assume a rewrite moved the number. It did not: the documented on-ramp is
full-stack Compose, where the language lives inside the image; installing Go is replaced by installing
Python and managing a virtualenv; and ingestion is untouched at ~12 minutes and dominates every row.

The one real user-visible gain is ergonomic: `zeitnot data analyze magnus 2026 08` rather than
`go run ./cmd/data analyze magnus 2026 08`, and a `pipx` / `uv tool install` route that does not require
the user's toolchain to be a *build* toolchain.

## Decision

**Port the application to Python. Change nothing about the database.**

Same `DATABASE_URL`, same schema, same `vector(768)` column, same corpus. No migration, no re-analysis,
not one schema change. That is what made the rewrite a strangler rather than a big bang: at every phase
both implementations ran against one corpus and their outputs were diffed.

[ADR 0001](./0001-postgres-in-docker-over-sqlite.md) holds, on changed grounds. This rewrite fired its
third revisit trigger — `internal/db` being substantially rewritten for another reason — and that
revisit is recorded in its 2026-08-29 amendment: the single-binary prize that motivated SQLite was
Go-specific and does not survive the move to Python, while the stats-aggregation cost does.

### The gate

**Assembled Prompt parity, and nothing else.** For all twelve frozen questions in
[`multi-provider/03-eval-plan.md`](../multi-provider/03-eval-plan.md), at the recorded corpus
fingerprint, both implementations produce byte-identical prompts. Every input the chat provider receives
is the prompt, so a matching prompt leaves nothing downstream for the port to have gotten wrong.

**Comparing answers was explicitly not a gate.** No call site sets a temperature, so every provider
samples at its own default and the Go implementation already answers the same question differently on
consecutive runs. A couple of answers were read end to end as a smoke test with no pass/fail authority.

## Consequences

**Verified, not believed.** The port cleared four independent checks, each against real data:

| Phase | Claim | Result |
|---|---|---|
| 3 | Game Summaries, all 74 stored games | byte-identical to Go *and* to the stored `summary_text` |
| 3 | Move analysis, five games re-analyzed | identical field for field across 90 moves |
| 5 | Assembled Prompts, twelve frozen questions | byte-identical |
| 6 | A month ingested into a scratch database | every table identical, including all 15 embedding vectors |

**Parity was measured on outputs, not structure.** `BuildWHERE` lost its parameter-numbering machinery
because psycopg's positional placeholders have no indices; a faithful port would have carried dead
complexity forever in the one function ADR 0001 singled out as load-bearing. The result-set assertions
are what prove it correct.

**Two defects are preserved on purpose**, marked `PARITY:` and listed in the plan. The more serious one
was found *during* the port: a drawn game is summarized as a loss, on 5 of 74 stored games. Both are
Phase 8 items, because fixing either changes the Game Summary text and therefore makes every stored
vector stale relative to its own source.

**Several defects were fixed rather than ported**, all in the same category — non-determinism, which has
no golden to capture and no Python equivalent to write. Eleven map-iteration sites (seven in the router,
four in the query parser) and one order-dependent floating-point aggregation. The parser sites mattered
most: its keyword loops take the first match and stop, so a query matching two keywords resolved
differently between runs, and that reached retrieval and therefore the prompt.

**Things the diff caught that no reasonable review would have.** Recorded because they are the argument
for building the harness before writing the port:

- `python-chess` reports scores from the side to move; reading `score.white()` instead type-checks,
  runs, and silently negates every Black-to-move evaluation.
- `board.fen()` omits the en-passant square unless a capture is legal, where Go emits it after any
  double pawn push.
- A plain list is adapted as `double precision[]`, which has no `<=>` operator.
- Go's `encoding/json` sorts *map* keys but not *struct* fields, HTML-escapes `<`, and renders an
  integral float as `25` rather than `25.0`.
- A `REAL` column read through psycopg's text mode lands on a different double than pgx's binary read.

**The stored `moves` rows turned out not to be reproducible by anything.** They were written by a
Stockfish build that is no longer on `PATH`, and the Go tree diverges from them too — 12 of 12 and 17 of
17 evaluations on the two shortest games. The plan's "diff the moves rows against the database" was
therefore unachievable for any implementation, and the check became "does Python match Go, given the
same engine", which is what actually decides whether new rows are interchangeable. Two fields survive an
engine change and are still checked against the corpus: `played_move` and `fen_before`.

**The size claim held, roughly, and only on the right measure.** Counting statements rather than lines —
excluding comments, docstrings, and blanks — 5,675 lines of Go became 4,308 of Python, a 24% reduction
against a predicted 40–55%. Counting raw lines the two are closer still (7,335 to 6,273), because this
tree is commented far more heavily than the Go one was: much of what was learned during the port is
recorded next to the code it applies to, and that is deliberate.

The prediction was too optimistic mainly about `internal/llm`, which was supposed to fall from 1,952 to
~600. It did not. The adapters exist to normalize three SDKs, and the Python SDKs need about as much
normalizing as the Go ones — plus a little more, because two of them removed a parameter the interface
still exposes. The two packages that *were* predicted to be replaced rather than ported both delivered:
the UCI wrapper and the terminal renderer are gone.

Test coverage went up rather than down. `internal/db`, `internal/chat`, `internal/engine`, and
`internal/summary` had no tests at all in Go; 2,125 lines of test code now cover them, and the suite is
246 tests.

**`legacy/` stays for one release.** It is not a fallback — the Python tree owns the database — but it is
what regenerates the goldens from the implementation that produced them, and it keeps the cutover
revertible. *(Superseded: it was deleted on 2026-08-31 without a release being cut. See the
[Amendment](#amendment--2026-08-31).)*

---

## Amendment — 2026-08-31

**`legacy/` has been deleted**, two days after the cutover and without the release this ADR said it
would wait for. The Consequences section above is left as written; this records what actually happened
and what it cost.

**Why now.** The condition in `legacy/README.md` — a real ingestion cycle and some chat sessions
without surprises — was judged met on the strength of the validation table above plus manual testing of
the ingest, provenance, and chat paths. "One release" was never satisfied, because no release has been
cut; that condition was dropped deliberately rather than met.

**What it cost, precisely.** Two things, both known and accepted:

1. **The goldens stop being a cross-language reference.** `cmd/golden` went with the directory, so
   `testdata/golden/` is now the last output of an implementation that no longer exists. Three of the
   five files are gitignored and therefore live on one machine only. From here a golden diff means "the
   Python tree changed", never "the two implementations disagree". `testdata/golden/MANIFEST.md` states
   this at the point of use.
2. **The cutover is no longer revertible by `git revert`.** It is still recoverable — the Go tree is
   intact at commit `71211ca` and its parents — but recovery is now an explicit archaeology step rather
   than a one-command undo.

**What did not change.** Nothing operational. `legacy/` was never built or tested by CI, nothing outside
it imported it, and the Python tree already owned the database. No runtime behavior depends on this
commit.

**A Python capture tool is now the blocking prerequisite** for any future golden recapture, and it was
already a Phase 8 item in [`docs/python-rewrite/00-plan.md`](../python-rewrite/00-plan.md). Until it
exists, the goldens are frozen: regenerating one is impossible rather than merely discouraged, which is
a stricter and in some ways safer position than the one this ADR described.

**Historical references to Go are kept on purpose.** This ADR, [ADR 0001](./0001-postgres-in-docker-over-sqlite.md),
and the rewrite plan all cite `internal/*.go` paths. They are records of decisions made when that code
existed and are not edited to pretend otherwise. Working documents that pointed contributors at a
runnable Go command were updated, because those were instructions rather than history.

## Alternatives considered

**Stay in Go and work the roadmap.** The honest counterfactual, and the reason driver 1 is stated first:
the roadmap items are what matter, and this decision is a bet that they get done faster afterwards. If
that bet is wrong, this ADR is the record of it.

**Port incrementally, running both in production.** Rejected. The two implementations share one database
and no schema boundary between them; "which one wrote this row" is a question with no good answer.
Instead they ran side by side only in *verification*, against scratch databases and captured goldens.

**Fix the known defects while porting.** Rejected explicitly, and it was the most tempting one. Permitting
deliberate diffs means every future diff needs adjudication — "is this the intentional one?" — and that
judgment gets made at 11pm under time pressure. One ambiguous diff costs more than the defect it was
fixing.

**Abort at the Phase 3 gate.** The plan pre-committed to stopping if byte-identical summaries could not
be reached within a weekend. They were reached the same day, so the gate was never exercised — but it is
worth recording that it existed, because Phases 0 and 3 were worth doing whether or not the rewrite
proceeded.
