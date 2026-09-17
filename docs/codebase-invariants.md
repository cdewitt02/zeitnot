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
  type, and `tests/test_prompt_snapshot.py` would show any handle that reached
  the prompt as a diff. Both run without a database.
- **A loop index over `MoveAnalysis` is a ply, and every boundary written in
  moves must convert.** `i // 2 + 1` is the full move number, as
  `to_move_records` in `zeitnot/ingest.py` has always had it. Reading the index
  directly against `OPENING_END` halved both phase boundaries for as long as the
  Go tree existed, so "opening" meant full moves 1-5 and the endgame bucket
  collected everything from move 13 — 45% of `weakest_phase` verdicts on a real
  195-game corpus. Fixed 2026-09-14; see
  [`opensource-readiness/02-open-questions.md`](opensource-readiness/02-open-questions.md)
  Q8. `classify_game_length` still takes `total_moves`, which is a **ply** count,
  and nothing records which was meant.
- **No superlative rests on a sample that cannot support it.** Below
  `MIN_GAMES_FOR_COMPARISON` a bucket keeps its numbers and loses its comparison
  string. A one-game bucket rendered as "100.0% win rate (45.1% ABOVE overall)"
  is true and was read as a finding by every model tried.
- **Every dict that reaches the assembled prompt is iterated sorted.** The
  prompt must be reproducible across runs;
  [`multi-provider/03-eval-plan.md`](multi-provider/03-eval-plan.md) depends on
  it, and so does `tests/test_prompt_snapshot.py`. `tests/test_parsing.py` pins
  the parser half, and it runs in CI — which it did not until ADR 0003, because
  a module-level marker had swept it into the corpus subset.
- **Adapters never send a parameter the caller did not set** — a stray
  temperature default would change answer distributions with prompt parity fully
  intact. The conformance suite asserts this.
- **Retry ownership sits with the provider SDKs.** Never layer an adapter-level
  loop on top; three attempts become nine against an endpoint that just said
  429.
- **An empty or missing result is an error, not a success.** This project's
  characteristic bug is silence: an empty embedding reaching a `vector(768)`
  column, an error body parsing into an empty game list.

## The expected-output tables

`testdata/golden/` holds four committed files: three JSON tables of expected
values, and the assembled-prompt snapshots. All of them are reviewable in a
diff, all survive a growing corpus, and every one is read by a test that runs in
CI with no database. [`testdata/golden/MANIFEST.md`](../testdata/golden/MANIFEST.md)
says what each one pins.

**The corpus-derived goldens are gone.** `summaries.json`, `analysis.json`, and
`prompts/` were the Go implementation's last output, frozen at the cutover and
gitignored because they embedded other players' Chess.com usernames. They were
retired on 2026-09-15 —
[ADR 0003](adr/0003-retire-the-parity-goldens.md) has the argument, and the
short version is that their remaining value was cross-language, Go is gone, and
the corpus they described no longer existed. There is nothing left to recapture
and no capture tool gating any other work.

**The three JSON tables are still Go captures.** Their inputs were chosen by
hand, but their expected values are what the Go implementation returned — they
came out of the same Phase 0 commit as the files that were retired. They survive
because they are committed, reviewable and cheap to run, not because anyone
re-derived them from a specification. **`parsing.json` is known to encode
defects** (a question about average centipawn *loss* records a filter for lost
games) — [#40](https://github.com/cdewitt02/zeitnot/issues/40), with the table
itself tracked as [#41](https://github.com/cdewitt02/zeitnot/issues/41). Read a
failure there as "the parser changed", never as "the parser broke".

**One rule carried over intact: a golden regenerated from the current tree
always matches the current tree.** `prompt_snapshots/` is the one set here that
*is* rendered from the code it checks, and it is safe only because it renders
from hand-written stat buckets and is committed, so the diff lands in the pull
request that caused it. It pins `Service.build_prompt` — the whole prompt a
provider receives, including the filter note the router does not add. Regenerate
it with `ZEITNOT_UPDATE_PROMPT_SNAPSHOTS=1` and commit the result alongside the
change that moved it, never on its own to clear a red test.

## What the database checks that no file can

Two `corpus`-marked tests compare the tree against the live database, needing no
golden at all:

- **Every stored summary must re-derive from the stored rows.**
  `tests/test_summary_corpus.py`. The summary text *is* the embedded text, so a
  mismatch means the stored vector no longer corresponds to its own source and
  nothing anywhere errors. **This currently fails on any corpus analyzed before
  2026-09-14**, and correctly — see the next section.
- **`played_move` and `fen_before` must match a fresh replay.**
  `tests/test_engine.py`. These come from PGN parsing and board replay rather
  than from Stockfish, so they stay reproducible whatever engine build wrote the
  rows. This is what caught `board.fen()` defaulting to `en_passant="legal"`,
  and what caught `get_moves_for_game` ordering by a column with two rows per
  value.

## One preserved defect

`zeitnot/summary.py` carried two bugs on purpose, preserved through the port so
that any diff meant a porting error rather than a deliberate improvement. **One
is now fixed**; one remains.

**Fixed 2026-08-31 — a drawn game was summarized as a loss.** `game_result()`
returns `"draw"` and never `""`, so the `drew` branch was dead. Covered by
`tests/test_summary.py`, which runs with no database and no goldens.

**Still preserved — `weakest_phase` reports "Endgame was weakest" on any tie**,
because the endgame is the `else` catch-all. It was unreached on the 74 games of
the retired capture, which is not the same as unreachable. Fixing it changes Game
Summary text, so it needs its own change with its own verification;
`tests/test_summary.py` guards against it being fixed incidentally.

**Anything that changes summary text changes the embedded text**, which makes
stored vectors stale relative to their own source. A fresh clone is unaffected,
since it ingests from scratch — **an existing corpus currently has no way
forward.** `zeitnot data reembed` rebuilds vectors from the stored text without
regenerating it, and `zeitnot data analyze` skips any game where `game_exists()`
is true, so the regeneration pass every doc in this repository refers to has
never been built. It needs only `games` and `moves`, both stored, and no
Stockfish.

**Since 2026-09-15 something does detect it.**
`tests/test_summary_corpus.py` re-derives every stored summary and fails when it
no longer matches, naming the summary lines that moved. On the maintainer's
195-game corpus it reports all 195 drifted: 195 termination lines, 87
weakest-phase lines, and 7 each of the result and pattern lines — the
normalization, phase-boundary, and draw fixes respectively. That is the check
failing correctly on a stale corpus, not a regression, and it stays red until the
regeneration pass exists. It is `corpus`-marked, so CI is unaffected.
