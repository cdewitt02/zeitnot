# Expected-output tables

Four committed files. Every one of them is reviewable in a diff, survives a
growing corpus, and is read by a test that runs in CI with no database.

| File | Read by | What it pins |
|---|---|---|
| `eval_helpers.json` | `tests/test_engine.py` | `get_evaluation`, `normalize_eval`, `classify_move` over a grid |
| `classification.json` | `tests/test_parsing.py` | which context the router assembles for a question |
| `parsing.json` | `tests/test_parsing.py` | which games a question retrieves |
| `prompt_snapshots/*.txt` | `tests/test_prompt_snapshot.py` | the whole assembled prompt, byte for byte |

## What used to be here

`summaries.json`, `analysis.json`, and `prompts/` were captured by
`go run ./cmd/golden cdew4` at commit `efb8f49`, against a 74-game corpus with
fingerprint `db9ac094e998b68b`. **They were retired on 2026-09-15** — see
[ADR 0003](../../docs/adr/0003-retire-the-parity-goldens.md) for the full
argument. In short: they were a cross-language reference, the other language was
deleted, and the corpus they described no longer existed. They were gitignored,
so they lived on one machine; nothing in the tree could reproduce them; and the
one defect they were best placed to catch — halved phase boundaries — is the one
they demonstrably could not, because the capture came from the implementation
that had the bug.

Nothing was deleted without its replacement landing first. The table in the ADR
maps each retired assertion to where it lives now.

## The three JSON tables are Go captures, and that still matters

All three came out of the same `c132190 Phase 0: capture goldens and freeze the
parity reference` commit as the files that were retired. Their *inputs* were
chosen by hand — a grid of evaluations, a fixed question set — but their
**expected values are what the Go implementation returned**, not what anybody
decided was correct.

That is a weaker claim than corpus-independence, and it is the one that matters
now that the goldens they were captured alongside are gone. They survive the
retirement because they are committed, reviewable, and cheap to run in CI, not
because anybody has re-derived their contents from a specification.

**`parsing.json` is known to encode defects.** `"What's my average centipawn
loss?"` records `result: loss`, so a question about average accuracy filters to
lost games; `"...what's my win rate?"` records `result: win`; and `"Show me
games where I threw a winning position"` records `result: win` for a question
about losses. Two entries reduce the semantic query to the empty string. These
are Go's answers, asserted as correct by `tests/test_parsing.py`, which runs on
every pull request. Splitting the intended behavior from the defects needs a
maintainer's call on the parser's specification, and is tracked separately —
until then, read a failure here as "the parser changed", never as "the parser
broke".

`classification.json` and `eval_helpers.json` have no known problem of this
kind, and `eval_helpers.json` is checked independently: `tests/test_engine.py`
asserts every classification boundary by hand alongside reading the table, so a
wrong value in the file would collide with an assertion that does not come from
Go.

## The two rules that survive

**A golden regenerated from the current tree always matches the current tree.**
Regenerating any of these to make a test pass destroys the only thing it was
telling you.

`prompt_snapshots/` is the one file set here that *is* rendered from the code it
checks, which makes the rule worth restating precisely. It is safe because it is
rendered from `tests/promptfixtures.py` — hand-written stat buckets, no corpus —
and because it is committed. A change to the prompt path shows up as a readable
diff in the pull request that caused it, which is the review. It pins
`Service.build_prompt`, the whole prompt a provider receives, rather than
`QueryRouter.build_prompt`, which is that minus the filter note. Regenerate
with:

```bash
ZEITNOT_UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshot.py
```

and commit the result in the same change as the code that moved it. Regenerating
to clear a red test in an unrelated change is the failure this arrangement exists
to make visible.

**No opponent's Chess.com username may be committed here.** That is what made
three of the old files gitignored: Chess.com's termination strings have the form
`"Bolzman0 won by resignation"`, so a single assembled prompt carried 31
third-party handles ([readiness P0-8](../../docs/opensource-readiness/01-roadmap.md)).
The snapshots use one fixed fictional opponent and assert it never appears in
the output, so the property is now tested rather than avoided.

## Note on `parsing.json`

`date_from` is recorded as a boolean flag, never a timestamp. It is `now()` minus
a duration, so a captured value would fail one second after capture while telling
you nothing.

## What is checked against the live corpus instead

Two claims need a database and no golden, and both are `corpus`-marked:

- `tests/test_summary_corpus.py` re-derives every stored summary from the stored
  `games` and `moves` rows and compares it against the stored `summary_text`.
  The Go capture did this too — 74 of 74 matched at the capture commit — and it
  is the only check that catches summary text drifting away from the vectors it
  was embedded from.
- `tests/test_engine.py::test_move_identity_and_fens_still_match_the_stored_corpus`
  replays every stored PGN and checks `played_move` and `fen_before`. These are
  the half of a `moves` row that comes from parsing and board replay rather than
  from Stockfish, so they stay reproducible against a corpus analyzed by any
  engine build — which the evaluations never were.
