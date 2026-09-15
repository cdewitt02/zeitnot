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
- [ ] **Changes Game Summary text.** The summary text *is* the embedded text, so
      stored vectors go stale relative to their own source — and **there is no
      command that repairs an existing corpus.** `zeitnot data reembed` rebuilds
      vectors from the *stored* text without regenerating it, and
      `zeitnot data analyze` skips games it has already seen. Say so in the PR
      rather than naming a remedy; the regeneration pass is unbuilt work, not a
      step you forgot.
- [ ] **Touches a provider adapter.** Adapters must never send a parameter the
      caller did not set, and retries belong to the SDK — never a loop on top.
- [ ] **Changes the database schema.** Nothing has yet; it is not off limits, but
      it needs its own discussion.
