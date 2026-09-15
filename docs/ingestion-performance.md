# Ingestion Performance — Findings

Parked findings from the multi-provider design session. Not provider work — no dependency on
[`multi-provider/`](./multi-provider/) — but ingestion is the largest single component of Time to First
Chat, so it belongs on the same roadmap.

**Why this matters.** [`multi-provider/04-onboarding.md`](./multi-provider/04-onboarding.md) §1 puts the
best achievable on-ramp at ~31 minutes, of which **~12 is ingestion**. It is the one cost no provider or
storage decision touches.

---

## 1. Every interior position is analyzed twice

**Free ~2× speedup. No quality change, no configuration, no trade-off.**

`analyze_game` (`zeitnot/engine.py`) loops over half-moves and makes two engine calls per
iteration:

```python
before = engine.analyze_position(positions[i], depth)
after_pos = positions[i + 1]
after = engine.analyze_position(after_pos, depth)
```

Iteration `i` analyzes `gamePositions[i]` and `gamePositions[i+1]`. Iteration `i+1` then analyzes
`gamePositions[i+1]` again at line 99 — same position, same depth, same function. **Every interior
position is evaluated exactly twice.**

**Fix.** Carry the previous iteration's `afterAnalysis` forward as the next `beforeAnalysis`. Analyses
drop from `2N` to `N+1`. Stockfish at a fixed depth on a fixed position is deterministic, so the results
are identical — this is caching, not approximation.

**Effect.** Ingestion roughly halves: ~12 min → ~6 min for a month of games. That is a larger cut to
Time to First Chat than every decision in [`multi-provider/`](./multi-provider/) combined.

**Caveats to handle:** the terminal-position branch in `zeitnot/engine.py` skips the after-analysis and
must not poison the cache; iteration 0 still needs a fresh before-analysis; and the cache is per-game, so
it resets between games and stays compatible with `WorkerPool` in `zeitnot/ingest.py`, which
gives each worker its own engine process.

**Verification.** Analyze the same games before and after and diff the stored `moves` rows — `cpl`,
`classification`, `evaluation`, and `best_move` must be byte-identical. The pure normalization,
evaluation, and classification helpers already have regression coverage in
`tests/test_parity_engine.py`, providing a safety net before this loop is changed.

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
