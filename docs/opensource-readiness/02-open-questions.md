# zeitnot — Open Questions

Decisions that need a maintainer call before the corresponding roadmap work starts. Each states what is being asked, why it is genuinely ambiguous rather than a default, what it blocks, and a recommendation.

Ordered by what blocks the earliest roadmap work.

**Remapped to Python on 2026-08-31** ([ADR 0002](../adr/0002-python-rewrite.md)). Three of the original
eight questions were answered or made moot by the rewrite and are recorded as such at the bottom rather
than deleted — a question that stops being asked should say why.

---

## Q1 · Which license?

**Blocks:** P0-2 — and transitively every other contribution, since without a license nobody may legally fork or modify the code.

**Why it is a real question.** Both realistic options are permissive and neither is wrong:

- **MIT** — shortest, near-universally understood. No explicit patent grant.
- **Apache-2.0** — adds an express patent grant and a trademark clause. Common where corporate contribution or adoption is expected; slightly heavier to read.

**What changed with the rewrite.** The dependency set is entirely different — the audit's reasoning cited
pgx, notnil/chess, and pgvector-go, none of which exist here any more. The current runtime dependencies
are `anthropic`, `chess`, `openai`, `pgvector`, `prompt-toolkit`, `psycopg`, `requests`, `rich`, and
`typer`. They are uniformly permissive (MIT / Apache-2.0 / BSD) and still constrain nothing, but the
conclusion should be re-verified rather than inherited.

One new wrinkle: `pyproject.toml:7` currently declares `license = { text = "UNLICENSED" }`. That is not a
neutral absence — it is a positive claim, and it is baked into the metadata of any wheel built today. It
has to change with the same commit that adds the file.

**What makes it consequential.** Practically irreversible. Relicensing after contributions arrive requires consent from every contributor, which in practice means it does not happen.

**Recommendation: MIT.** It matches the ecosystem norm for a project of this size and shape, and the patent-grant advantage of Apache-2.0 has little purchase on a chess analysis tool. Choose Apache-2.0 only if corporate adoption is a specific goal.

---

## Q3 · Does Docker Compose become the primary onboarding path, or sit alongside native install?

**Blocks:** P3-1, and shapes P3-2 (Makefile) and any CONTRIBUTING setup revision.

**Why it is a real question.** Compose eliminates the worst onboarding step — building pgvector from
source — but only for Postgres. Ollama and Stockfish stay native regardless: containerizing Ollama
complicates GPU passthrough, and Stockfish wants host CPU. So the choice is between two imperfect stories:

- **Compose primary:** `docker compose up -d` replaces three steps, but the docs must explain that only *part* of the stack is containerized, and Docker becomes a prerequisite for the recommended path.
- **Native primary, Compose as an alternative:** no new prerequisite, but the documented default keeps the step most likely to make people give up.

**What changed with the rewrite.** Two things, both mildly favoring Compose. A Python app image no longer
needs to be a *build* toolchain, so full-stack containerization is cheaper than it was under Go. And with
`CHAT_PROVIDER=openai` and `EMBED_PROVIDER=openai`, Ollama leaves the prerequisite list entirely — meaning
a Compose-plus-hosted-API path is now a genuine two-prerequisite story (Docker and Stockfish), which the
Go tree could not offer.

Note also that ADR 0001 and ADR 0002 both already *describe* Compose as the documented on-ramp, and no
`docker-compose.yml` exists. Whatever is decided, those documents and the tree currently disagree.

**Recommendation:** make Compose the **documented default for the database only**, with native install kept as a clearly-labeled alternative. Be explicit up front that Stockfish is a host install either way, and that Ollama is one unless both providers are hosted. The partial containerization is worth being loud about rather than discovering.

**Answered 2026-09-02, as recommended.** The README's Setup section lists Docker first with "or PostgreSQL
with pgvector, if you would rather run your own", keeps the native route in a labelled `<details>` block,
and names Stockfish as a host install in the prerequisites that precede the provider choice. What the
recommendation did *not* anticipate is that the partial containerization is currently more partial than
intended — the application image ADR 0001 decided on was never built, so Python and `uv` are host
prerequisites too. See P3-1 in [`01-roadmap.md`](./01-roadmap.md), corrected the same day.

---

## Q6 · Discussions or issues-only?

**Blocks:** P1-4 (the issue template's `config.yml` routes traffic based on this).

**Why it is a real question.** Setup for this project is genuinely hard — Postgres, pgvector, Stockfish,
and either Ollama with two models or API keys for a hosted provider. A predictable share of early inbound
will be "my setup doesn't work," which is support, not bug reports. Enabling Discussions routes that
traffic away from Issues and keeps the bug tracker meaningful. But Discussions is a second inbox, and an
unanswered Discussions tab signals neglect as loudly as an unanswered issue tracker.

**What changed with the rewrite.** The support surface got *wider*, not narrower. Provider selection adds
a whole class of question the audit never anticipated — wrong API key, a model the provider does not
offer, and especially the index provenance guard, which refuses to start and will read as a bug. That
argues for a strong troubleshooting section (P3-4) more than for a second inbox.

**Recommendation:** **issues-only initially**, with a well-built bug template (P1-4) that front-loads environment *and provider* questions, plus the troubleshooting section (P3-4) to deflect the common cases. Enable Discussions only if support volume actually materializes. One neglected inbox is better than two.

---

## Q7 · Is `FUNDING.yml` wanted?

**Blocks:** nothing. Listed because the audit brief asked.

**Why it is a real question.** Purely a maintainer preference with no technical bearing. A sponsor button on a young project with no users can read as premature; on the other hand it costs nothing and is trivially removed.

**Recommendation:** skip for now. Revisit if the project gains real users.

---

## Q8 · Is the phase boundary counting plies or moves?

**Blocks:** the Preserved Defect work in [`../python-rewrite/00-plan.md`](../python-rewrite/00-plan.md)
Phase 8. *(It formerly blocked P2-1, which is now done — see below.)*

**Why it is a real question.** `extract_summary_data` categorizes moves by phase using `i < OPENING_END`
where `OPENING_END = 10` (`zeitnot/summary.py:15, 94`). But `i` indexes **plies** (individual half-moves),
while the constant's comment still reads `# moves 1-10`. If "moves" means full moves, the boundary should
be at ply 20, not 10 — meaning the "opening" phase currently covers only the first 5 full moves.

This may well be intentional. It also may be an off-by-factor-of-two that has been silently skewing every
phase statistic, every `weakest_phase` result, and therefore every generated summary and embedding.

**What changed with the rewrite — the question got sharper, not softer.** The audit could only say "this
looks ambiguous." Three things are now known:

1. **The behavior is confirmed identical to Go**, byte-for-byte across all 74 stored summaries. So
   whatever this is, it is not a porting error — it is original intent or an original bug.
2. **It sits next to two confirmed bugs.** The port surfaced two genuine defects in the same file, both
   preserved on purpose and marked `PARITY:`. That a third oddity in `summary.py` turns out to be real is
   now more plausible than it was.
3. **The remedy is no longer vague.** The audit worried that fixing it would strand stored embeddings.
   That is still true, but the fix path is now a known, bounded, documented sequence: regenerate summaries
   from stored `games` and `moves` (no Stockfish), then `zeitnot data reembed`. It is the same path the
   Preserved Defect fixes need.

**Why it still needs a maintainer, not a fix.** Only the author knows which was meant. It is a
specification question, and no amount of reading the code answers it.

**Recommendation:** answer it **together with the Preserved Defects**, not separately. All three change
Game Summary text, all three require the same regeneration pass, and doing them as one change means
paying that cost once. If it is a bug, the CHANGELOG (P4-2) entry is the same "re-embedding required"
note the other two need.

**Amended 2026-09-15.** This used to say "sequence it after the Python golden capture tool exists —
otherwise there is no way to verify the new output against anything". That tool was never built and is
now never going to be: the goldens it would have verified against were retired
([ADR 0003](../adr/0003-retire-the-parity-goldens.md)). The verification that was actually wanted needs no
capture at all — `tests/test_summary.py` pins the behavior live, and
`tests/test_summary_corpus.py` says whether the stored corpus still agrees with the tree. The real
sequencing constraint was always the regeneration pass, not the capture tool.

---

**Answered 2026-09-14: it was a bug, and the boundaries now count full moves.**

The decisive evidence was not in `summary.py` at all. `to_move_records`
(`zeitnot/ingest.py`) converts the same loop index with
`move_number=(i // 2) + 1`, under the comment *"Chess numbering: indices 0,1 are
move 1"* — so the distinction was understood, written down, and applied
correctly to the `moves` table by the same author who wrote `i < OPENING_END`
forty lines away. Two readings of one index in one codebase is a defect, not a
specification.

**What it was costing.** `i` indexes plies, so `OPENING_END = 10` meant full
moves 1-5, `MIDDLEGAME_END = 25` meant 6-12, and the endgame bucket collected
everything from full move 13 on — most of a median game, which on the corpus
below is 30 full moves. That is why the distribution recorded against the
captured corpus (53 endgame / 20 middlegame / 1 opening across 74 games) looked
the way it did: it is the signature of a bucket that has swallowed the
middlegame, not a finding about the player.

Measured on a 195-game corpus, before and after:

| | Opening | Middlegame | Endgame |
|---|---|---|---|
| Ply boundaries (before) | 7 | 60 | 128 |
| Full-move boundaries (after) | 30 | 93 | 72 |

**45% of games change their `weakest_phase` verdict.** The verdict reaches the
Assembled Prompt once, through the retrieved Game Summary in `_write_game_context`.

**The sequencing constraint was not binding.** The recommendation above put this
behind the Python golden capture tool. That tool never existed, and the goldens
it would have verified against were already stale wholesale — `prompts/` since
the 2026-09-14 prompt change, `summaries.json` for drawn games since
2026-08-31 — so there was nothing left for them to verify. They were retired
outright on 2026-09-15 ([ADR 0003](../adr/0003-retire-the-parity-goldens.md)),
with this answer as the central exhibit. The replacement is
coverage that needs no corpus at all: `tests/test_summary.py` now pins both
boundaries from both sides and for both colours, and each new assertion was
checked against the old code to confirm it fails there. **The absence of exactly
that test is why this survived a port that was otherwise diffed byte for byte** —
every check on the phase buckets ran against a golden, and a golden captured
from the buggy implementation agrees with it.

**Not folded in:** the remaining Preserved Defect (`weakest_phase`'s tie
catch-all) and `classify_game_length`, which has the same ply/move ambiguity
with no comment stating which was meant. Both still want a maintainer's call.

**Operator action — and there is no command for it.** A fresh clone is
unaffected; an existing corpus is stuck. `zeitnot data reembed` rebuilds vectors
*from the stored summary text* and does not regenerate that text, and
`zeitnot data analyze` skips any game where `game_exists()` is true, so
re-running it changes nothing. The only paths available today are deleting the
games and re-analyzing from scratch — full Stockfish cost — or building the
regeneration pass, which has been an unbuilt Phase 8 item in
[`../python-rewrite/00-plan.md`](../python-rewrite/00-plan.md) since the cutover
and is what every "regenerate summaries from `games` and `moves`" sentence in
these docs has been quietly assuming.

Until it exists, a corpus spanning the fix carries both conventions at once, and
nothing detects it. That is the
same shape as the P0-8 lesson in [`01-roadmap.md`](./01-roadmap.md): **a change
enforced only at write time does not reach a corpus that already exists.**

---

# Retired questions

Kept for traceability. None of these were *answered by a maintainer decision* — the rewrite dissolved them.

### Q2 · Is `NUM_WORKERS` supposed to default to 4 or 8? — **Resolved**

The audit found the README saying `8 (4 for less compute)` while `cmd/data/main.go:112` returned `4`. The
rewrite settled it in the recommended direction: `zeitnot/cli.py` defaults to `4` and the README's
environment table now says `4` plainly, with no parenthetical advice masquerading as a value. The
*guidance* half of the recommendation is still outstanding and lives in P3-3.

A hardware-aware default (`os.cpu_count()`) remains available and remains a deliberate behavior change,
not a doc fix. Nobody has asked for it.

### Q4 · Should `internal/engine` be promoted to a public package? — **Moot**

The question was whether to publish zeitnot's Stockfish UCI wrapper for other Go chess projects to import.
That wrapper no longer exists: `python-chess`'s `SimpleEngine` replaced it during the rewrite, which is
one of the two places the port genuinely deleted code rather than translating it. What remains in
`zeitnot/engine.py` is CPL computation and move classification — zeitnot's own domain logic, with no
general-purpose UCI layer under it to promote.

The recommendation's substance was achieved anyway, by a different route: the analyzer *is* testable now
(`tests/test_engine.py`), which was the part worth doing regardless of publication.

### Q5 · Should the `gofmt` sweep use `.git-blame-ignore-revs`? — **Moot**

There was no sweep. The Python tree was formatted by `ruff format` from its first commit, so no repo-wide
reformat ever landed and `git blame` was never polluted. The sharper half of the question — "is anything
in flight that a reformat would invalidate?" — is worth remembering as a general habit before any future
mechanical change, but it has no standing item.
