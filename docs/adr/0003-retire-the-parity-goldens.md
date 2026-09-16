# ADR 0003 — Retire the corpus-derived parity goldens

**Status:** Accepted · 2026-09-15

## Context

[ADR 0002](./0002-python-rewrite.md) ported zeitnot from Go to Python and gated
the cutover on Assembled Prompt parity. The reference for that gate was
`testdata/golden/`, captured by `go run ./cmd/golden cdew4` at commit `efb8f49`
against a 74-game corpus with fingerprint `db9ac094e998b68b`.

Its 2026-08-31 amendment deleted `legacy/`, and `cmd/golden` went with it. That
amendment named the cost precisely — "from here a golden diff means *the Python
tree changed*, never *the two implementations disagree*" — and left the files in
place as a Python-vs-Python regression suite.

Two weeks later that arrangement had stopped working. Three of the five files
were gitignored, so they existed on exactly one machine. The corpus had grown to
195 games and was no longer even a superset of the capture: game
`01194c5e-84b8-11f1-9742-a650e001000f` was gone. A full local run reported

```
FAILED tests/test_parity_summary.py::test_every_summary_matches_the_golden_byte_for_byte
FAILED tests/test_parity_summary.py::test_every_summary_matches_what_is_actually_stored
FAILED tests/test_parity_summary.py::test_every_summary_data_field_matches_the_golden
```

and `test_parity_prompt.py` skipped 14 of its 20 tests on a fingerprint
mismatch. The abort gate the pull request template leans on for prompt and Game
Summary changes could not render a verdict — here or anywhere.

`MANIFEST.md` had already recorded three separate stalenesses: drawn games as of
2026-08-31, phase statistics as of 2026-09-14, and `prompts/` wholesale as of
2026-09-14.

### The argument that decided it

The files' remaining value was cross-language. Go is gone. And the one defect
they were best placed to catch is the one they demonstrably could not:

> every check on the phase buckets ran against a golden, and the golden was
> captured from the implementation that had the bug. **Parity testing cannot
> find a defect both sides share.**

That defect — phase boundaries counted in plies rather than full moves — moved
45% of `weakest_phase` verdicts on a real 195-game corpus, and survived a
byte-for-byte port with every golden green.

## Decision

**Retire the three corpus-derived goldens and the tests that read them. Keep the
three committed tables, rename them away from "parity", and replace the two
assertions that were worth keeping.**

Three tiers, and only the middle one goes.

**Kept.** `eval_helpers.json`, `classification.json`, and `parsing.json` are pure
functions over a fixed input set — table-driven tests whose tables happen to live
in JSON. They are committed, corpus-independent, and still testing behavior.
`tests/test_parity_engine.py` became `tests/test_engine.py`; the classifier and
parser tables moved to `tests/test_parsing.py`.

Kept is not the same as vindicated. All three are still Go captures from the same
Phase 0 commit, so their expected values are what Go returned rather than what
anyone decided was right — see [Known debt](#known-debt-the-kept-tables-are-still-go-output)
below.

**Retired.** `summaries.json` with all of `tests/test_parity_summary.py`;
`prompts/` with the corpus-dependent half of `tests/test_parity_prompt.py`;
`analysis.json` with the two engine tests that read it.

**Replaced.** Nothing was deleted before its replacement landed:

| Retired assertion | Where it lives now |
|---|---|
| Summary text matches Go, byte for byte | gone — Go is gone, and this was the only claim that was purely cross-language |
| Summary text matches the stored `summary_text` | `tests/test_summary_corpus.py`, from the database alone |
| `GameSummaryData` fields, draw and phase behavior | `tests/test_summary.py`, live and needing nothing |
| The assembled prompt, byte for byte | `tests/test_prompt_snapshot.py`, against committed synthetic fixtures |
| Four deliberate prompt changes | `tests/test_router_prompt.py`, already there |
| Move analysis matches Go, field for field | gone — it was only ever valid for one Stockfish build |
| `played_move` and `fen_before` match the corpus | `tests/test_engine.py`, now over every game rather than five |

The `golden` pytest marker is removed. Every remaining expected-output file is
committed, so no test can fail to find the file it reads, and the marker had
nothing left to guard.

## Consequences

**CI gained coverage it was already entitled to.** `tests/test_parity_prompt.py`
set `pytestmark = [corpus, golden]` at module scope, which swept three
pure-function tests into the corpus subset. `zeitnot/search/parser.py` and
`zeitnot/chat/classifier.py` — 477 lines that decide which games are retrieved
and which context the router assembles — were covered by nothing on any pull
request. That was [#25](https://github.com/cdewitt02/zeitnot/issues/25), and the
split fixed it: `pytest -m "not corpus"` went from 388 tests to 419.

**The re-derivation check is the real prize, and it is red.** The Go capture tool
re-derived every summary from stored `games` and `moves` rows and compared it
against the stored `summary_text`; 74 of 74 matched at the capture commit. That
claim needs no golden at all, only the database, and it is the only thing that
catches summary text drifting away from the vectors it was embedded from.
Rebuilt as `tests/test_summary_corpus.py`, it reports on the maintainer's corpus:

```
195 of 195 stored summaries no longer match what this tree derives from the same rows

Lines that differ:
   195  termination type
    87  weakest phase
     7  game pattern
     7  result, colour and time class
     1  opening name
```

Every one of those maps to a change this repository made on purpose —
termination normalization (P0-8, 2026-08-31), the phase-boundary fix
(2026-09-14), and the draw fix (2026-08-31, which is why the count is 7, the
number of drawn games). **The test is correct and the corpus is stale.** There
is no command that repairs it: `zeitnot data reembed` rebuilds vectors from the
*stored* text without regenerating it, and `zeitnot data analyze` skips any game
`game_exists()` is true for. The regeneration pass is unbuilt work that every
document in this repository has referred to for weeks; this is the first check
that fails until it exists, rather than a paragraph saying it should.

The failure names the lines that moved rather than dumping 195 pairs of text,
because a corpus test that fails without saying why is
[#26](https://github.com/cdewitt02/zeitnot/issues/26).

**A latent ordering defect surfaced immediately.** Rebuilding the move-identity
check over the whole corpus rather than five games caught
`get_moves_for_game` ordering by `move_number` alone. That column holds the
*full* move number, so two rows share every value and the tie was left to the
planner — which broke it the wrong way on real rows, returning Black's move 4
ahead of White's. Every caller reads that list as plies and takes the side from
index parity, so a swapped pair credits one player's mistakes to the other. The
fix is a second sort key. This is the class of defect the retired suite could not
see: the golden was captured through the same ambiguous query.

**The whole-prompt gate is weaker in reach and stronger in review.** The retired
`prompts/` rendered twelve questions against a real 195-game corpus; the
snapshots render seven contexts against six hand-written stat buckets. Less
realistic input, and a real loss. In exchange they are committed rather than
gitignored, they survive a growing corpus because no corpus is involved, and a
change to the assembled prompt appears as a readable diff in the pull request
that caused it. A golden nobody can see proves nothing.

**Recapture is no longer a prerequisite for anything.** ADR 0002's amendment made
a Python capture tool the blocking dependency for P0-8 and both Preserved
Defects, and the readiness roadmap sequenced work behind it. That constraint is
lifted: there is nothing left to recapture.

## Known debt: the kept tables are still Go output

Retiring the corpus-derived captures does not make the remaining three
independent. Their inputs were hand-chosen; their expected values were recorded
from the Go implementation, and nothing has re-derived them from a
specification.

**`parsing.json` demonstrably encodes defects.** Four of the twelve frozen eval
questions parse wrongly, and the file records the wrong answers as correct:

| Question | Recorded | Problem |
|---|---|---|
| `What's my average centipawn loss?` | `result: loss` | a metric name read as a result filter; retrieval sees only lost games |
| `How many games have I played and what's my win rate?` | `result: win` | same, for wins |
| `Show me games where I threw a winning position` | `result: win` | the question is about losses |
| `Am I better with white or black?` | `color: white` | a comparison filtered to one side of itself |

The matched keyword is also removed from the semantic query, so the text handed
to the embedder is mangled on top of the wrong filter — `Am I better with white
or black?` becomes `Am I better or black`. A query that strips away to *nothing*
is already handled: `zeitnot/search/hybrid.py:70` falls back to the original
text rather than embedding an empty string. The partially stripped case is not.

29 of 37 entries strip something. Most are the intended design: `my games as
black` becoming `my games` plus a colour filter is the feature working. Nothing
in the file distinguishes the two groups, and deciding which is which is a
specification question about the parser rather than something a diff can settle.

**This retirement made the problem more acute, not less.** Moving those tests out
of the corpus subset (#25) was right — the parser had no pull request coverage at
all — but it means CI now enforces these values on every change. The defects are
[#40](https://github.com/cdewitt02/zeitnot/issues/40); the table split is
[#41](https://github.com/cdewitt02/zeitnot/issues/41). Until those land, a failure
in `tests/test_parsing.py` means "the parser changed", not "the parser broke".

`classification.json` has no known problem of this kind. `eval_helpers.json` is
partly self-checking: `tests/test_engine.py` asserts every classification
boundary by hand alongside reading the table, so a wrong value would collide with
an assertion that did not come from Go.

## Alternatives considered

**(a) Build the Python capture tool and recapture.** Rejected. It would freeze
the current tree against itself, which `MANIFEST.md` already ruled out: *"A
golden regenerated from the current tree always matches the current tree and
proves nothing."* It would also have to reproduce the existing files
byte-for-byte to be trusted, which is impossible now that the corpus they
describe is gone.

**(b) Pin a small fixture corpus and capture against that.** Probably the right
long-term shape, and the snapshots are a first step toward it. Rejected *as the
disposition of these files*: a pinned corpus is a new corpus, and building one
should not be gated on preserving a Go artifact. Worth its own issue.

**(c) Retire them.** Taken.

**Leave them in place and keep skipping.** The status quo, and the reason this
ADR exists. A gate that cannot render a verdict anywhere is not a gate, and the
pull request template was pointing contributors at it.
