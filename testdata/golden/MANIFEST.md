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

**`parsing.json` has now been read entry by entry**, which is what
[#40](https://github.com/cdewitt02/zeitnot/issues/40) and
[#41](https://github.com/cdewitt02/zeitnot/issues/41) asked for. Eight of its 37
entries were rewritten by hand and the other 29 were confirmed as the feature
working as designed (`'my games as black'` → `'my games'` + `color: black`). The
four that were defects: `"What's my average centipawn loss?"` and
`"...what's my win rate?"` recorded a filter for a word inside a *metric* name;
`"Show me games where I threw a winning position"` recorded `result: win` for a
question about losses; and `"Am I better with white or black?"` recorded
`color: white`, filtering a comparison to one side of it. The other four dropped
`phase: …` from `extracted_filters`, which announced a filter `build_where`
never applies ([#18](https://github.com/cdewitt02/zeitnot/issues/18)). Those
entries are no longer Go's answers, and `tests/test_parsing.py` asserts the
three rules behind them directly, without reading the table.

So a failure here is now readable the ordinary way: the entries this repository
has reviewed are a specification, and the rest are still a Go capture that
nobody has contradicted. What has *not* changed is that the table cannot be
regenerated to clear a red test — see the rule below.

`classification.json` and `eval_helpers.json` have no known problem of this
kind. `classification.json` has had the same read-through: all 37 entries record
what `classify_query` does, and one of them disagrees with the *intent* recorded
elsewhere — `'Which time control is my best?'` classifies as `specific_games`,
while [`03-eval-plan.md`](../../docs/multi-provider/03-eval-plan.md) §2 lists it
as a Comparative question. `_COMPARATIVE_KEYWORDS` carries `best time control`,
not the inverted phrasing. The table is right about the code; the code and the
plan disagree, and no issue tracks it yet. `eval_helpers.json` is checked
independently: `tests/test_engine.py` asserts every classification boundary by
hand alongside reading the table, so a wrong value in the file would collide
with an assertion that does not come from Go.

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

One claim needs a database and no golden, and it is `corpus`-marked. (A second,
`tests/test_summary_corpus.py`, re-derived every stored summary; it only ever
reported that the corpus predated the tree, and was removed by
[ADR 0004](../../docs/adr/0004-the-corpus-is-ephemeral.md).)

- `tests/test_engine.py::test_move_identity_and_fens_still_match_the_stored_corpus`
  replays every stored PGN and checks `played_move` and `fen_before`. These are
  the half of a `moves` row that comes from parsing and board replay rather than
  from Stockfish, so they stay reproducible against a corpus analyzed by any
  engine build — which the evaluations never were.
