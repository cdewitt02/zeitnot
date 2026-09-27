# Ingestion Performance — Findings

Parked findings from the multi-provider design session. Not provider work — no dependency on
[`multi-provider/`](./multi-provider/) — but ingestion is the largest single component of Time to First
Chat, so it belongs on the same roadmap.

**Why this matters.** [`multi-provider/04-onboarding.md`](./multi-provider/04-onboarding.md) §1 puts the
best achievable on-ramp at ~31 minutes, of which **~12 is ingestion**. It is the one cost no provider or
storage decision touches.

---

## 1. Every interior position is analyzed twice

**Fixed ([#11](https://github.com/cdewitt02/zeitnot/issues/11)). Measured ~1.5× on analysis.**

`analyze_game` (`zeitnot/engine.py`) used to make two engine calls per half-move — the position before
and the position after — so iteration `i + 1` re-searched the position iteration `i` had just searched.
It now carries each after-search forward as the next before-search: `2N` searches become `N + 1`, or `N`
when the game ends in mate or stalemate, since a terminal position is scored without a search and
carries nothing forward. The carry is local to one call, so it resets between games and never crosses
the workers in `zeitnot/ingest.py`, each of which owns its own engine.

**Measured.** 40 games (3,077 moves) at depth 12: 90.1s → 60.2s with a fresh engine per game, 77.0s →
48.4s with one engine reused across games as a worker does — 1.50× and 1.59×, not the 2× first
estimated.

**Not byte-identical, and that is expected.** This section originally claimed the stored values would
not change, on the grounds that a fixed-depth search of a fixed position is deterministic. It is only
deterministic from the same engine state, and Stockfish keeps its hash table between searches. The
old loop's second search of each position ran with its own first search already in the table, so it
was never a clean single search; removing it changes the table's contents and shifts `evaluation`,
`best_move`, `cpl` and `classification` on most rows. Neither set is more correct — the new values
come from one search per position rather than a repeated one. Byte-identical output would need the
hash cleared before every search, which costs speed and changes every stored value anyway.

The pure normalization, evaluation, and classification helpers have regression coverage in
`tests/test_engine.py`, which also checks that each position is searched once and that each move gets
its own position's search.

---

## 2. `ANALYSIS_DEPTH` is a hardcoded constant

`ANALYSIS_DEPTH = 12` (`zeitnot/engine.py`) is not configurable, and nothing documents its cost.
Engine time scales steeply with depth, so this is the second-largest lever after §1 — but unlike §1 it is
a real quality trade-off, not free.

Blunder and mistake classification (`classify_move` in `zeitnot/engine.py`) is comparatively
robust at lower depth, since it keys off large centipawn swings. Best-move agreement, which drives the
`"best"` classification in the same function, degrades faster.

**Suggested:** expose as `ANALYSIS_DEPTH`, keep 12 as the default, and document the trade honestly. Do
**not** lower the default without measuring — the Game Summary is the retrieval corpus, so degraded
analysis degrades every future answer, invisibly and permanently.

---

## 3. `NumSimilar` discards 90% of what it retrieves

Not an ingestion cost — a per-question one — but the same shape of waste.

`DEFAULT_NUM_SIMILAR = 100` and `DEFAULT_DETAIL_LIMIT = 10` (`zeitnot/cli.py`) configure retrieval and
prompt detail respectively. `_write_game_context` in `zeitnot/chat/router.py` shows at most the detail
limit, and aggregate and comparative queries truncate to 3 first. Nothing else reads the remainder
beyond checking whether any games were returned.

So 100 games are fetched from Postgres with full record joins and 90 are discarded, every question.
Either lower `NumSimilar` toward `DetailLimit`, or establish what the wider retrieval is for — a
re-ranking step would justify it, but none exists today.

---

## 4. Chess.com already returns accuracy data, unused

`Game.accuracies` (`zeitnot/models/game.py`) is parsed from the API response and referenced
**nowhere else in the repo**.

It cannot replace Stockfish — it is one number per player per game, with no per-move CPL, no blunder
counts, and no phase breakdown, so summary generation would lose weakest-phase and pattern detection.
But it is free, already fetched, and could serve as a
cross-check on computed CPL or as a fast-path preview. Noted rather than recommended.
