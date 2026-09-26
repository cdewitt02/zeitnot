## What changed

<!-- One or two sentences. The commit message is the place for the reasoning; this is
     the place for the summary. -->

## Why

<!-- What problem this solves. If it closes an issue or a roadmap item, link it:
     "Closes #12", "Readiness P2-5". -->

## How it was verified

<!-- What you actually ran, not what you believe to be true. If you could not verify
     something (no Stockfish, no hosted API key), say so — that is useful, and a
     reviewer can cover it. -->

- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy`
- [ ] `pyright`
- [ ] `pytest -m "not corpus"` — the portable subset, what CI runs
- [ ] `pytest` — everything this machine supports, if you have a database

## Things worth a second look

<!-- Delete any that do not apply. Each of these is easy to break with a change that
     looks like a cleanup — see CONTRIBUTING's "Things that are load-bearing". -->

- [ ] **Changes the assembled prompt.** Any dict reaching it must be iterated
      sorted, or the prompt stops being reproducible between runs.
- [ ] **Changes data derived at ingest** — Game Summary text, move analysis, the
      aggregate stats. An existing corpus does not take the change up; say in the
      PR that users must re-ingest. No repair command or migration is needed
      (ADR 0004). The summary text *is* the embedded text, so `zeitnot data
      reembed` alone is not enough.
- [ ] **Touches a provider adapter.** Adapters must never send a parameter the
      caller did not set, and retries belong to the SDK — never a loop on top.
- [ ] **Changes the database schema.** No migration is needed — an existing
      corpus is dropped and re-ingested (ADR 0004) — but say so in the PR.
