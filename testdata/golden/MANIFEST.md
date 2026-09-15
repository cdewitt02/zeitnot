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

## The two rules that survive

**A golden regenerated from the current tree always matches the current tree.**
That is why the three JSON tables above were *written*, not captured: they are
grids and question sets chosen to reach branches a corpus does not, and reviewing
one means reading it rather than trusting the process that produced it.

`prompt_snapshots/` is the one file set here that *is* rendered from the code it
checks, which makes the rule worth restating precisely. It is safe because it is
rendered from `tests/promptfixtures.py` — six hand-written stat buckets, no
corpus — and because it is committed. A change to the prompt path shows up as a
readable diff in the pull request that caused it, which is the review. Regenerate
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
