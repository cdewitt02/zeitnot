# Codebase invariants

Context you need before touching the prompt path, the provider adapters, or
`summary.py`. Not required reading before your first pull request — see
[CONTRIBUTING.md](../CONTRIBUTING.md) for that.

## Things that are load-bearing

A few properties are easy to break with a change that looks like a cleanup:

- **No opponent's Chess.com username reaches the assembled prompt.** A hosted
  Chat Provider receives that text verbatim, and the opponent never chose this
  tool. Normalization at write time is not sufficient on its own: a corpus
  analyzed before 2026-08-31 still has handles in stored `summary_text` and in
  `player_stats.stats_by_termination`, so `zeitnot/chat/router.py` withholds
  both at assembly. `tests/test_router_prompt.py` asserts it for every query
  type — it is the only coverage that runs without a database.
- **No superlative rests on a sample that cannot support it.** Below
  `MIN_GAMES_FOR_COMPARISON` a bucket keeps its numbers and loses its comparison
  string. A one-game bucket rendered as "100.0% win rate (45.1% ABOVE overall)"
  is true and was read as a finding by every model tried.
- **Every dict that reaches the assembled prompt is iterated sorted.** The
  prompt must be reproducible across runs;
  [`multi-provider/03-eval-plan.md`](multi-provider/03-eval-plan.md) depends on
  it, and so does every golden.
- **Adapters never send a parameter the caller did not set** — a stray
  temperature default would change answer distributions with prompt parity fully
  intact. The conformance suite asserts this.
- **Retry ownership sits with the provider SDKs.** Never layer an adapter-level
  loop on top; three attempts become nine against an endpoint that just said
  429.
- **An empty or missing result is an error, not a success.** This project's
  characteristic bug is silence: an empty embedding reaching a `vector(768)`
  column, an error body parsing into an empty game list.

## The goldens

`testdata/golden/` began as the parity reference from the Python rewrite and is
now the regression suite for the packages that never had one. **Read
[`testdata/golden/MANIFEST.md`](../testdata/golden/MANIFEST.md) before touching
anything in it** — a golden regenerated from the current tree always matches the
current tree and proves nothing.

**They are frozen at the cutover and there is no capture tool.** The Go
implementation that produced them was deleted, so they are no longer a
cross-language reference: they are a Python-vs-Python regression suite pinned to
the behavior that shipped. Regenerating any of them now requires first writing a
Python capture tool, which is a deliberate change with its own verification, not
a step you take to make a red test green.

Three of the five files are gitignored because they derive from one person's
real corpus and embed other players' usernames (readiness P0-8):
`summaries.json`, `analysis.json`, and `prompts/`. A fresh clone will not have
them, and the tests that need them skip with a message saying so — that is
expected, not a broken checkout.

## One preserved defect

`zeitnot/summary.py` carried two bugs on purpose, preserved through the port so
that any diff meant a porting error rather than a deliberate improvement. **One
is now fixed**; one remains.

**Fixed 2026-08-31 — a drawn game was summarized as a loss.** `game_result()`
returns `"draw"` and never `""`, so the `drew` branch was dead. Covered by
`tests/test_summary.py`, which runs with no database and no goldens.

**Still preserved — `weakest_phase` reports "Endgame was weakest" on any tie**,
because the endgame is the `else` catch-all. Unreached on the captured corpus.
Fixing it changes Game Summary text, so it needs its own change with its own
verification.

**Anything that changes summary text changes the embedded text**, which makes
stored vectors stale relative to their own source. The remedy is to regenerate
summaries and then run `zeitnot data reembed`; a fresh clone is unaffected,
since it ingests from scratch.
