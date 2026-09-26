# ADR 0004 — The corpus is ephemeral

**Status:** Accepted · 2026-09-26 · tracked in [#49](https://github.com/cdewitt02/zeitnot/issues/49)

## Context

The corpus is the set of games in a zeitnot database: `games`, `moves`,
`game_summaries` and `player_stats`, all derived from Chess.com's archive plus a
Stockfish pass. Everything in it can be rebuilt with `zeitnot data analyze`.

Until now the repository has treated an existing corpus as something every change
must carry forward. That stance was right during the port. [ADR 0002](./0002-python-rewrite.md)
gated the cutover on byte-for-byte parity against a captured corpus, and a
Preserved Defect existed so that a diff against that capture would mean a porting
error rather than an improvement. [ADR 0003](./0003-retire-the-parity-goldens.md)
retired the goldens, but it kept the stance: it rebuilt the re-derivation check as
`tests/test_summary_corpus.py`, which fails on any corpus whose stored summaries
predate a change to `zeitnot/summary.py`.

That stance now costs more than it protects:

- **A change to Game Summary text is blocked.** [#32](https://github.com/cdewitt02/zeitnot/issues/32)
  is a two-line fix to `weakest_phase` and a threshold change in
  `classify_game_length`. It is labelled `blocked` because
  [#10](https://github.com/cdewitt02/zeitnot/issues/10), a regeneration command
  that has never been built, doesn't exist yet.
- **Tests fail on correct code.** On the maintainer's 195-game corpus,
  `tests/test_summary_corpus.py` reports 195 of 195 summaries drifted. Every line
  it names is a deliberate fix. [#26](https://github.com/cdewitt02/zeitnot/issues/26)
  asks for a skip message for a second test that fails the same way.
- **Documentation describes remedies instead of the code.** The pull request
  template, `CONTRIBUTING.md`, `docs/codebase-invariants.md` and
  `docs/troubleshooting.md` each explain how a stale corpus behaves and why no
  command repairs it.

Rebuilding a corpus is cheap. One month of about 195 games ingests in roughly 85
seconds at the default worker count. The maintainer's whole corpus is one month.

## Decision

**A corpus is disposable. When a change alters anything derived at ingest time,
the remedy is to drop the database and ingest again. No code, test or document
works around data written by an older tree.**

In practice:

1. **The remedy is always re-ingestion.** Drop the volume with
   `docker compose down -v`, bring the database back up, and run
   `zeitnot data analyze` for each month. A pull request that changes Game Summary
   text, aggregate derivation, move analysis or the schema says so in one line.
   It doesn't have to ship a migration or a repair command.

2. **No regeneration pass.** #10 is closed as not planned.

3. **Tests against a real database assume a fresh ingest by the current tree.**
   The `corpus` marker stays, and its meaning narrows to "needs a populated
   database". A corpus test that fails on an older database is answered by
   re-ingesting, not by a skip. #26 is closed as not planned.
   - `tests/test_summary_corpus.py` is deleted. Its only finding is "the corpus
     predates the tree", and under this ADR that isn't a defect.
   - `tests/test_db_corpus.py` stays. Its assertions hold on any fresh ingest.

4. **"Preserved Defect" is retired as a live term.** No port is in flight, so
   there is nothing to preserve a defect for. The `weakest_phase` tie becomes an
   ordinary bug, and #32 is unblocked. `CONTEXT.md` keeps the entry, marked
   historical, because ADR 0002 and ADR 0003 use the term.

5. **`zeitnot data refresh-stats` is removed.** It exists to realign
   `player_stats` with games whose aggregates were derived by older code, or with
   rows edited by hand. `analyze` already refreshes stats when it finishes, and
   both remaining cases are covered by re-ingesting.

6. **Schema changes need no migration.** `migrate()` stays idempotent
   `CREATE ... IF NOT EXISTS`. A schema change drops and recreates rather than
   altering in place, and its pull request says to re-ingest.

### What stays, and why

**`zeitnot data reembed` stays.** Switching `EMBED_PROVIDER` or the embedding
model changes only the vectors. Re-embedding the stored text costs one call per
summary and needs no Stockfish, while re-ingesting repeats the whole engine
pass. That makes it a configuration tool, not a repair for old data. The index
provenance check (`config.check_index`) stays with it, as do
[#20](https://github.com/cdewitt02/zeitnot/issues/20) and
[#21](https://github.com/cdewitt02/zeitnot/issues/21).

**Guards whose failure is a privacy leak stay.**
`strip_unnormalized_termination` and the termination check in
`zeitnot/chat/router.py` both exist to accommodate pre-2026-08-31 rows. By the
rule above they would go. They are kept because the failure they prevent is not
a wrong answer. It is an opponent's Chess.com handle sent to a hosted Chat
Provider, and a user who skips a re-ingest shouldn't pay for that with someone
else's privacy. They are now framed as defence in depth rather than
compatibility, and they cost one pass over a few lines of text per game.

## Consequences

**#32 can land on its own**, with its tests in `tests/test_summary.py`, which
needs no database.

**Every user pays the re-ingest, not just the maintainer.** zeitnot is headed
for open source ([readiness roadmap](../opensource-readiness/01-roadmap.md)), so
this ADR decides for anyone with a database. An upgrade that changes derived
data has to say so in its release notes and changelog entry. Otherwise the user
finds out from a wrong answer in chat. The cost is wall-clock time, and money
only on a hosted Embed Provider, where re-ingesting re-embeds every summary.

**Nothing detects a stale corpus any more.** `tests/test_summary_corpus.py` was
the only thing that did. A user who upgrades and doesn't re-ingest gets answers
built from summaries written by older code, and nothing fails. This is accepted
on purpose: the check only ever told the user to re-ingest, and a release note
does the same without a red test. If it turns out to matter, a cheap follow-up
is to stamp the database with a summary-format version and have `zeitnot doctor`
compare it. That is out of scope here.

**`analyze` still skips games it has already stored.** Re-ingesting one month
into a live database therefore changes nothing. The only supported path is a
full reset. A `--force` flag on `analyze` would be a reasonable follow-up, but
this ADR doesn't require one.

## Alternatives considered

**(a) Build the regeneration pass (#10).** It is small: read `games` and `moves`,
re-derive, write `summary_text`, then prompt for `reembed`. Rejected, because it
would be one more path to maintain alongside ingest. Every later change to
derived data would need either its own repair step or a proof that this one
covers it. It also fixes only summaries, not `moves` or `player_stats`.

**(b) Versioned migrations for derived data.** Rejected. This is the most work,
and it is justified only when data can't be rebuilt. Everything in a zeitnot
database can be rebuilt.

**(c) Keep the status quo.** Rejected. This is the stance described in Context:
it blocks correct changes behind unbuilt tooling and keeps a test red on correct
code.

**(d) Treat the corpus as ephemeral.** Taken.
