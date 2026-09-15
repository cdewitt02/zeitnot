# Golden reference — Phase 0

Captured by `go run ./cmd/golden cdew4` from the Go implementation, so the Python
port can be **diffed** against it rather than trusted
([`docs/python-rewrite/00-plan.md`](../../docs/python-rewrite/00-plan.md) Phase 0).

## Capture point

| | |
|---|---|
| Capture commit | `efb8f49` (`determinism: sort keyword-map iteration in QueryParser`) |
| `ANALYSIS_DEPTH` | **12** — frozen for the duration of the rewrite |
| `NumSimilar` / `DetailLimit` | 100 / 10, mirroring `cmd/chat/main.go` |
| Username | `cdew4` |
| Corpus fingerprint | `db9ac094e998b68b` |
| Game count | 74 |
| Embedder at capture | `ollama` / `nomic-embed-text` |
| Stockfish at capture | **Stockfish 16** — `analysis.json` only |

Any behavior change after that commit invalidates these files and forces a
deliberate recapture. **A golden regenerated from the current tree always matches
the current tree and proves nothing** — regenerate only when a behavior change is
intended, and update this table when you do.

`ANALYSIS_DEPTH` is a hardcoded constant today. Changing it rewrites every `cpl`
and `classification`, so it must not be touched while the port is in flight.

Verified: three consecutive captures produce byte-identical output.

## The two sets have different validity conditions

| File | Scope | Survives a growing corpus? |
|---|---|---|
| `eval_helpers.json` | Pure arithmetic over a fixed grid | **Yes** — no corpus involved |
| `classification.json`, `parsing.json` | Pure functions over a fixed question set | **Yes** |
| `summaries.json` | Keyed per game UUID | **Yes** — each game's values depend only on that game |
| `analysis.json` | Five games, re-analyzed | Yes, but **only for one Stockfish build** — see below |
| `prompts/` | Whole-corpus | **No** — every added game shifts win rates, CPL averages, and the comparison strings built from them |

`prompts/manifest.json` carries the corpus fingerprint. The harness compares
fingerprints first and refuses to run when they differ, reporting *corpus
changed, recapture required* rather than emitting a diff that looks like a port
bug.

## What is and is not committed

### `analysis.json` is keyed to a Stockfish version, not to a commit

The stored `moves` rows are **not reproducible**. They were written by a different Stockfish build, and
the Go tree at this commit does not reproduce them either: on the two shortest games, 12 of 12 and 17 of
17 evaluations differ, and 3 of 17 classifications. So "does the port match the corpus?" is not a
question any implementation can answer.

`analysis.json` answers the question that can be: it records what **Go** produced from the five shortest
games with the engine on PATH at capture time. Matching it means new rows from Python are
interchangeable with new rows from Go, which is what the port is actually on the hook for.

Two fields do survive an engine change and are still checked against the live corpus — `played_move` and
`fen_before` come from PGN parsing and board replay, not from Stockfish.

If your Stockfish differs from the one above, that one test will fail and it is not a port regression.
Recapture, or skip it.

**Committed:** `eval_helpers.json`, `classification.json`, `parsing.json`. These
are corpus-independent — a grid of integers and a fixed question set — so they
are portable, reviewable, and are the regression suite `internal/engine`,
`internal/chat`, and `internal/search` have never had.

**Gitignored:** `summaries.json`, `analysis.json`, and `prompts/`. All three derive from one person's
real corpus, and the first two embed **other players' Chess.com usernames**: Chess.com's termination
strings are of the form `"Bolzman0 won by resignation"`, so a single assembled prompt carries 31
third-party handles. That is [readiness P0-8](../../docs/opensource-readiness/01-roadmap.md) — a live
disclosure problem in the prompt path — and committing the capture would move it from *sent to a
provider* to *published in a repository*. `analysis.json` is excluded for the separate reason above:
it is valid only for one Stockfish build, so a committed copy would fail on most machines.

They are regenerated locally from the database in seconds, which is the whole point: the database is the
source of truth, and it is language-neutral.

## Note on `parsing.json`

`date_from` is recorded as a boolean flag, never a timestamp. It is `now()` minus
a duration, so a captured value would fail one second after capture while telling
you nothing about the port.

The corresponding prompts are stable for a different reason: every row's
`created_at` falls inside even the narrowest window any frozen question opens, so
the date filter currently selects the whole corpus regardless of when the harness
runs. That will stop being true once the corpus contains rows older than
60 days — at which point `prompts/09.txt` needs recapturing.

## Known staleness

**`summaries.json` and `prompts/` are stale for drawn games as of 2026-08-31.**
The draw defect (a drawn game summarized as a loss) was fixed that day, so the
current tree emits `drew as ...` where these files record `lost as ...`. That is
a deliberate divergence, not a regression: the goldens are frozen at the cutover
and predate the fix.

`tests/test_parity_summary.py` skips without a database and captured goldens, so
this does not affect CI. If you have both locally, expect the byte-for-byte
summary assertions to fail on drawn games and nothing else. Live coverage of the
fixed behavior is in `tests/test_summary.py`.

**`summaries.json` is also stale for phase statistics as of 2026-09-14.** The
phase boundaries were corrected from plies to full moves
([Q8](../../docs/opensource-readiness/02-open-questions.md)), so
`Opening`/`Middlegame`/`Endgame` `MoveCount` and `TotalCPL` differ on almost
every game, and the `weakest_phase` line in the rendered text differs on roughly
45% of them. `test_every_summary_data_field_matches_the_golden` and the
byte-comparison above both fail against this file wherever a database and the
golden are present — expected, not a regression.

That is the second reason this file records history rather than behavior, and
it is worth being precise about why the defect survived a byte-for-byte port:
**every check on the phase buckets ran against a golden, and the golden was
captured from the implementation that had the bug.** Parity testing cannot
find a defect both sides share. Live coverage that needs no corpus is in
`tests/test_summary.py`.

**`prompts/` is stale wholesale as of 2026-09-14.** The assembled prompt was
changed deliberately, in four places:

1. Retrieved games are labelled `Game 1:` rather than `1. [vs OPPONENT]`, and
   the instruction to cite the opponent by username is gone.
2. A stored summary or aggregate that predates termination normalization has the
   offending part withheld at assembly rather than echoed.
3. `STRONGEST`/`WEAKEST` on a dimension became `HIGHEST`/`LOWEST win rate`, with
   the game count and the CPL alongside, and a note when the two measures
   disagree. Buckets under three games lose their comparison string.
4. Opening aggregates are written whenever the question is about openings, not
   only when it classifies as comparative or recommendation.

These files therefore record the *previous* prompt. That is not a reason to
recapture them on its own — the corpus fingerprint already makes them skip
everywhere — but it does mean they are no longer a description of current
behavior. Live coverage of all four is in `tests/test_router_prompt.py`, which
needs neither a database nor a golden and runs in CI.

Classification is deliberately unchanged, so `classification.json` is still
valid: point 4 keys off a separate predicate (`mentions_openings`) rather than
rerouting the question.

## Regenerating — you cannot, yet

**`legacy/` and its `cmd/golden` capture tool were deleted after the port was validated**
([ADR 0002](../../docs/adr/0002-python-rewrite.md)). These files are the last output of the Go
implementation and nothing in the tree can reproduce them.

That changes what they mean. They are no longer a cross-language reference; they are a
Python-vs-Python regression suite frozen at the cutover — still the only coverage `summary`, `engine`,
`chat`, and `search` have for their exact output, but from here a diff means "the Python tree changed",
not "the two implementations disagree".

Recapturing requires **writing a Python capture tool first** (a Phase 8 item in
[`docs/python-rewrite/00-plan.md`](../../docs/python-rewrite/00-plan.md)). It would need Postgres,
Stockfish (for `analysis.json`), and Ollama (for `prompts/`), and it must reproduce these files
byte-for-byte before it is trusted to replace them. Treat that as a deliberate change with its own
verification — never as a way to clear a failing test. Record it here when it exists.

The Go capture also re-derived every summary from stored `games` and `moves` rows
and compared it against the stored `summary_text`. A mismatch was reported loudly:
it meant the Go tree had drifted from what produced the stored embeddings, and
the goldens would freeze that drift. At the capture commit, **74 of 74 matched** —
a property any Python replacement should reproduce.
