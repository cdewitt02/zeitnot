# zeitnot — Prioritized Roadmap

Derived from a readiness audit of commit `2bcd4cb`, against the **Go** implementation. **Remapped to
Python on 2026-08-31**, after the rewrite ([ADR 0002](../adr/0002-python-rewrite.md)) and the deletion of
`legacy/`; the audit document itself was retired at the same time, its surviving findings folded into the
items below.

Every open item states **Problem → Fix → Effort → Risk/tradeoff**. Effort is calendar-ish for one person familiar with the code: **S** ≤ 1 hour, **M** a few hours to a day, **L** multiple days.

**P0 is ordered by severity.** **P1–P4 are ordered by what unblocks an outside contributor fastest**, which is not always the same as what is most broken. Where an item's placement is driven by sequencing rather than importance, that is called out.

---

## What the rewrite closed

The port was not undertaken to work this roadmap, but it closed sixteen of its thirty items on the way
past — several because the Python tree simply had to solve them to exist at all. They are recorded here
rather than deleted, so the audit's findings stay traceable to their resolution.

| Item | Status | Closed by |
|---|---|---|
| P0-1 Module path | **Moot** | No `go.mod`. Distribution naming is now a packaging question — see P4-1 |
| P0-3 Broken ingestion command | **Done** | `zeitnot data analyze` in README. `refresh-stats` was documented (P0-9), then removed by [ADR 0004](../adr/0004-the-corpus-is-ephemeral.md) |
| P0-4 Config divergence | **Done** | `zeitnot/config.py` resolves providers once for both entrypoints; `NUM_WORKERS` and the 768-dim constraint are documented |
| P0-5 Chess.com status check | **Done** | `zeitnot/api.py` errors on every non-2xx, sets a User-Agent, carries a timeout |
| P0-6 Embedding status check | **Done** | `zeitnot/llm/ollama.py:104` checks status and rejects an empty embedding |
| P0-7 Prompt dump gating | **Done** | `ZEITNOT_DEBUG_PROMPT`, off by default, dumps to stderr |
| P1-1 `gofmt` sweep | **Moot** | `ruff format` — the tree was born formatted, so there is no sweep to sequence |
| P1-2 CI | **Done** | `.github/workflows/ci.yml`: ruff, `mypy --strict`, pytest on 3.11 and 3.13 |
| P1-3 CONTRIBUTING | **Done** | Includes the honest testing matrix the audit asked for (markers, not prose) |
| P2-1 Test the summary package | **Done** | `tests/test_summary.py` — and it *did* surface real bugs, the two Preserved Defects (one since fixed, the other now #32). `tests/test_summary_corpus.py` was removed by ADR 0004 |
| P2-2 Make the engine testable | **Done** | `python-chess`'s `SimpleEngine` replaced the UCI wrapper; `tests/test_engine.py` covers the sign and CPL logic |
| P2-3 Startup preflight | **Done** | `config.preflight` runs before the banner; `config.check_index` guards provenance |
| P2-4 Actionable errors | **Done** | `zeitnot/llm/errors.py` classifies by kind; messages name the remedy |
| P2-7 Linting (half) | **Done** | `ruff check` in CI. *Dependency vulnerability scanning is still open* — see P2-7 below |
| P4-3 Promote the engine package | **Moot** | The reusable part was the UCI wrapper, and that is now `python-chess`'s job, not ours |
| P4-4 Swappable LLM backend | **Done** | `zeitnot/llm/` protocols with three adapters and a conformance suite |
| P0-8 Opponent usernames | **Done** 2026-08-31, **completed** 2026-09-14 | `normalize_termination` in `zeitnot/models/game.py` at the two write paths; two further paths closed at assembly 2026-09-14 — see the amendment below. `tests/test_termination.py`, `tests/test_router_prompt.py` |
| P0-2 LICENSE | **Done** 2026-08-31 | MIT; `pyproject.toml` metadata corrected from `UNLICENSED` |
| P3-5 README demo | **Done** 2026-08-31, **replaced** 2026-09-14 | Real session at the top. The first one was not reproducible on the model its banner named — see the amendment below |
| P3-1 Docker Compose | **Partly done** 2026-08-31 | `pgvector/pgvector:pg17` + an init script. This is the *database half only* — see the corrected entry below |
| P1-4 Issue/PR templates | **Done** 2026-08-31 | `.github/ISSUE_TEMPLATE/`, provider fields required |
| P2-5 Credential redaction | **Done** 2026-08-31 | `redact_secrets`, plus a filter on psycopg's own pool logger — the path that actually leaked |
| P3-4 Troubleshooting | **Done** 2026-08-31 | Keyed to reproduced failures; moved to [`../troubleshooting.md`](../troubleshooting.md) 2026-09-02 |

**What this leaves.** No P0s. The last one, P0-8, was closed on 2026-08-31 — see below for what it
turned out to involve.

---

## Sequencing constraints

The audit's three orderings were all Go-specific (module rename, `gofmt` sweep, both before outside PRs)
and are gone. Three replace them:

1. ~~**P0-8 before any golden recapture.**~~ ~~**The Python golden capture tool before P0-8 or
   either Preserved Defect.**~~ **Both lifted 2026-09-15.** The corpus-derived goldens were retired
   ([ADR 0003](../adr/0003-retire-the-parity-goldens.md)), so there is nothing to recapture and no
   capture tool in front of anything. P0-8 and both Preserved Defects are now sequenced only by their
   own merits. A change to the Assembled Prompt shows up as a diff in
   `testdata/golden/prompt_snapshots/`, committed and regenerated in the same change.
2. ~~**The Game Summary regeneration pass before either Preserved Defect.**~~ **Lifted 2026-09-26.**
   The corpus is disposable ([ADR 0004](../adr/0004-the-corpus-is-ephemeral.md)): a change to summary
   text is taken up by re-ingesting, so there is no regeneration pass (#10, closed) and nothing in front
   of #32. The original constraint, for the record: This replaces the capture
   tool as the real blocker, and it always was the real one: changing summary text makes every stored
   vector stale against its own source, and no command repairs that today. The corpus is *already*
   spanning three such changes — `tests/test_summary_corpus.py` now fails loudly rather than letting it
   pass unnoticed — so the pass is overdue rather than speculative. It needs only `games` and `moves`,
   both stored, and no Stockfish.
3. **P0-2 (license) before P1-5 (good-first-issue).** Unchanged from the audit: soliciting contributions
   to a repository nobody may legally fork is not a coherent ask.

---

# P0 — Blockers to any public release

### P0-2 · Add a LICENSE

**Problem.** No LICENSE file, and `pyproject.toml:7` declares `license = { text = "UNLICENSED" }` —
which is not merely absent but an *assertion* that the code is not licensed, carried into the package
metadata of any wheel built from it. Under default copyright, "public on GitHub" grants no rights.

**Fix.** Choose MIT or Apache-2.0 (see [`02-open-questions.md`](./02-open-questions.md) Q1), add the file
verbatim with the correct holder and year, replace the `pyproject.toml` declaration, and add a License
section to the README.

**Effort.** S — minutes, once the choice is made.

**Risk/tradeoff.** Effectively irreversible once contributors submit under it. The dependency licenses no
longer match the audit's list — the Go dependencies are gone. The current set (`anthropic`, `chess`,
`openai`, `pgvector`, `prompt-toolkit`, `psycopg`, `requests`, `rich`, `typer`) is uniformly permissive
(MIT / Apache-2.0 / BSD) and constrains nothing, but that is worth a re-check rather than an assumption
before the file lands.

---

### P0-8 · Stop sending opponents' usernames to hosted providers — **DONE 2026-08-31**

**What it was.** `zeitnot/chat/router.py` emitted the "Game endings" section one line per distinct
`termination_type`, and Chess.com's termination strings embed the winner's username. Two problems in
one section: a disclosure the README's promise did not cover, and an aggregation that never aggregated
— **90 distinct values across 195 games**, nearly all of them a single game.

**What the fix turned out to involve.** The audit named one leak path. There were **two**:

1. The aggregate section, as filed.
2. **Every Game Summary** — `generate_summary` writes `Termination type: {termination_type}.` into
   `summary_text`, which is retrieved and placed in the prompt as context. The original note claimed
   summaries were unaffected because "the opponent's name is already implied by the game itself." That
   is wrong: the summary is otherwise anonymous — result, colour, time class, opening, blunder counts,
   opponent *rating* — so the handle appeared nowhere else in it and was genuinely new information.

**How.** `normalize_termination(termination, result)` in `zeitnot/models/game.py` reduces a string to
outcome-and-method from the player's own perspective: "Bolzman0 won by resignation" → "lost by
resignation". Applied at the aggregation site in `zeitnot/db/__init__.py` (which fixes the prompt and
`zeitnot data refresh-stats` together) and in `zeitnot/summary.py`. Unrecognized shapes collapse to
"<result> by other means" rather than passing through, because an unparsed string is exactly where a
handle might still hide.

**Operator action for an existing corpus.** *(Superseded by ADR 0004: re-ingest.)*
`zeitnot data refresh-stats <username>` rebuilds the aggregates. Summaries written before the change keep the old text until regenerated, then re-embedded.
A fresh clone is unaffected.

**Amended 2026-09-14 — there were four paths, not two, and "operator action" was the wrong remedy.**
Two more were found by dumping a real assembled prompt with `ZEITNOT_DEBUG_PROMPT=1` and grepping it
against the 193 opponent handles in the corpus, which is the check this entry should have ended with:

3. **The retrieved-game label.** `router.py` prefixed every retrieved game with `[vs USERNAME]`, and the
   instruction block told the model to cite games by that handle — which both local models tested duly
   did, in their answers. Attached at assembly rather than stored, so nothing about the corpus revealed
   it. Now `Game 1:`, with `Opponent rating` (already in the summary) as the discriminator.
4. **Both stored paths, on any corpus analyzed before 2026-08-31.** Normalization runs when a row is
   *written*, so the aggregate keys and the `Termination type:` line in `summary_text` still hold raw
   strings, and no session can repair them. Making that the operator's problem meant the promise held
   only for people who had read this file. Both are now withheld at assembly: the Game endings section
   is dropped whole if any key is unnormalized, and a stale termination line is dropped from the summary.

The lesson generalizes past this item: **a guarantee enforced only at write time is not a guarantee**,
because the corpus outlives the code that wrote it. `docs/codebase-invariants.md` carries it as an
invariant now, and `tests/test_router_prompt.py` asserts it without needing a database.

---

### P0-9 · Document the `refresh-stats` subcommand

*Done 2026-09-25 (#48), then superseded: the subcommand was removed by
[ADR 0004](../adr/0004-the-corpus-is-ephemeral.md).*

**Problem.** The audit's P0-3 asked for two things: fix the broken ingestion command, and document the
undocumented `refresh-stats` subcommand. The rewrite delivered the first — `zeitnot data analyze` is
correct and runnable — but `refresh-stats` is still absent from the README, while `analyze` and `reembed`
are both documented. It is reachable only by running `zeitnot data --help`.

**Fix.** Add it to the README's data section, noting that `analyze` already refreshes stats on completion,
so the standalone command is for recomputing after a manual database change.

**Effort.** S — one short section.

**Risk/tradeoff.** None. Documentation-only.

---

# P1 — Contribution readiness

### P1-4 · Issue and PR templates

**Problem.** No templates. Bug reports for this project are useless without the environment, and given the
setup complexity, most early issues will be environment problems that are unanswerable without exactly
those fields.

**Fix.** `.github/ISSUE_TEMPLATE/bug_report.yml` as a structured form. The required fields have changed
with the language: **OS, Python version, install method (`uv tool` / `pipx` / editable), PostgreSQL and
pgvector versions, Stockfish version, and — new since the multi-provider work — `CHAT_PROVIDER` and
`EMBED_PROVIDER`**, since a report is unactionable without knowing whether a hosted API was involved.
Ollama model only where the provider is Ollama. `feature_request.yml` lighter; `config.yml` routing setup
questions per Q6. Short PR template: what changed, how it was verified, and the `ruff` / `mypy` / `pytest`
checklist from CONTRIBUTING.

**Effort.** S.

**Risk/tradeoff.** Heavy templates deter drive-by reports. Keep required fields to what is genuinely
necessary for triage.

---

### P1-5 · `good-first-issue` labels *(cheap, high leverage)*

**Problem.** No labels beyond GitHub defaults. A contributor who wants to help has no entry point.

**Fix.** File a handful of genuinely small, genuinely useful issues and label them. **The audit's
suggestions are all spent** — the summary tests exist, the dead `TestData` struct went with the Go tree,
the Stockfish error message is fixed, and the `for`-loop wart is gone. Current candidates from the live
docs instead:

- P3-4's troubleshooting section, one entry at a time.
- The `NUM_WORKERS` guidance in P3-3.

**Effort.** S.

**Risk/tradeoff.** Only works once the repo is discoverable and contributable — i.e. after P0-2 and P1-2
(the latter is now done). Stale unclaimed issues are mildly off-putting, so file few and keep them real.

---

### P1-6 · CODEOWNERS — recommend deferring

**Problem.** No `CODEOWNERS`.

**Fix.** A single-line file assigning everything to `@cdewitt02`.

**Effort.** S.

**Risk/tradeoff.** **Genuinely low value at one maintainer.** Recommendation is to skip until there is a
second regular contributor. Unchanged by the rewrite.

---

# P2 — Quality & trust signals

### P2-5 · Redact connection strings in error output

**Problem.** Carried across the port intact. `zeitnot/db/` wraps psycopg errors, and a psycopg connection
error can embed the connection string including the password. `zeitnot/cli.py:41` prints whatever it is
given to stderr. A contributor pasting a failure into an issue publishes their password — and P1-4 is
about to *invite* people to paste failures into issues.

**Fix.** Parse the URL and blank the password component before including it in any wrapped error. A single
helper at the point where `DATABASE_URL` is read (`cli.py:46`) covers every path that can surface it.

**Effort.** S.

**Risk/tradeoff.** Narrow exposure — needs a malformed URL, not merely a wrong password — which is why it
is P2 and not P0. But the cost of the fix is ~10 lines and the cost of the leak lands on users. **Its
priority rises once P1-4 lands**, since a bug template actively solicits pasted error output.

---

### P2-6 · Adopt the `logging` module

**Problem.** The audit's finding survives the port in Python form. Output is split between `print()` to
stdout for progress (`zeitnot/cli.py`, `zeitnot/ingest.py`), `print(..., file=sys.stderr)` for warnings
(`zeitnot/config.py`), and `rich` for the chat UI. There are no levels, so `ZEITNOT_DEBUG_PROMPT` is a
bespoke env var where it could be one debug logger. In a concurrent 4-worker run there is still no way to
correlate a failure to a game UUID or worker.

**Fix.** `logging` with a level from `ZEITNOT_LOG_LEVEL`, worker ID and game UUID as structured extras.
**Keep user-facing CLI output as plain `print`/`rich` on stdout** — the REPL banner, prompts, and progress
are UI, not logs.

*Two things the port already fixed for free:* `log.Fatalf` skipping deferred cleanup has no Python
equivalent (`DB` is a context manager, so `typer.Exit` unwinds it correctly), and error context is now
carried by `LLMError` rather than by string wrapping.

**Effort.** M.

**Risk/tradeoff.** The real trap is over-converting: turning the chat REPL's output into structured logs
would make the tool worse. The line between "log" and "UI output" needs to be drawn deliberately.

---

### P2-7 · Dependency vulnerability scanning

**Problem.** The linting half of this item is done — `ruff check` and `mypy --strict` both gate CI, and
they are stricter than the `golangci-lint` starting set the audit proposed. The **security half was never
done in either language**: nothing has ever scanned this project's dependencies for known
vulnerabilities, and the surface grew materially with the rewrite — nine runtime dependencies including
three vendor SDKs that make authenticated network calls.

**Fix.** `pip-audit` (or `uv pip audit`) as its own CI job, plus Dependabot or Renovate on
`pyproject.toml`. Keep it a separate job from the `check` job so a new advisory does not turn the whole
matrix red on an unrelated PR.

**Effort.** S — much cheaper than the `golangci-lint` adoption this replaces, since there is no wall of
initial findings to triage.

**Risk/tradeoff.** Advisory noise on transitive dependencies is the usual failure mode; scoping alerts to
direct dependencies first keeps it actionable.

---

# P3 — Ease of adoption

### P3-1 · Docker Compose — **database half done, application half not built**

**Corrected 2026-09-02.** This item was marked Done on 2026-08-31. Only half of it had landed, and the
half that did is the half [ADR 0001](../adr/0001-postgres-in-docker-over-sqlite.md) explicitly said was
insufficient on its own.

**What shipped.** `docker-compose.yml` with `pgvector/pgvector:pg17`, a named volume, a healthcheck, and
`docker/init-pgvector.sql`. This is the *original* P3-1 scope — Postgres only — and it does eliminate the
pgvector source build.

**What did not.** ADR 0001 decided to **containerize the application as well**, and
[`../multi-provider/04-onboarding.md`](../multi-provider/04-onboarding.md) §3 made that the target
architecture (its row 6, ~31 minutes). There is no `Dockerfile` in the tree. What is running today is that
document's **row 2** — "Compose for Postgres only", ~57 minutes — which §2.3 rejects by name:

> Row 2 moves 69 to 57. … **Neither track delivers an on-ramp by itself.** They have to land together.

So the decision is recorded, the database half is built, and the tree still delivers the on-ramp the
analysis rejected. That gap is what a maintainer setting up on a clean machine actually runs into.

**Fix.** A `Dockerfile` for the app image with Stockfish installed, added as a service to the existing
Compose file, and a wrapper for the REPL — §3 notes `zeitnot chat` is interactive and needs
`docker compose run --rm -it`, "worth wrapping in a script rather than putting in front of a new user".
That wrapper is the one shell script this project has a clear case for.

**Effort.** M.

**Risk/tradeoff.** The container covers Postgres and the application: Ollama and Stockfish stay native for
anyone not using the image, and Ollama is deliberately never containerized (no GPU passthrough on Apple
Silicon). Docker Desktop becomes a prerequisite for the documented path, which ADR 0001 accepted
explicitly. Stockfish runs 10–30% slower in the VM on macOS and Windows, lengthening the step that already
dominates.

---

### P3-2 · Makefile (or `just`)

**Problem.** No task runner. Every gate is typed from memory.

**Fix.** Targets wrapping the real commands: `lint` (`ruff check`), `format`, `typecheck` (`mypy`), `test`
(`pytest -m "not corpus"`), `test-all`, `check` (all gates, matching CI exactly), `db-up`/`db-down` around
Compose, and `dev` for `uv venv && uv pip install -e ".[dev]"`. Have CI invoke the same targets so local
and CI cannot diverge.

**Effort.** S.

**Risk/tradeoff.** Make is not universal on Windows; `just` is an alternative but adds an install step.
Keep targets thin wrappers so anyone can read the file and run the commands directly.

---

### P3-3 · Document hardware requirements

**Problem.** Nothing warns that ingestion is heavy. `ANALYSIS_DEPTH = 12` (`zeitnot/engine.py:28`) with 4
default workers, each spawning its own Stockfish process, saturates 4 cores for the run, while Ollama
concurrently holds an embedding model resident. On a 2-core laptop this is unusably slow, with no warning
and no hint that `NUM_WORKERS` is the knob.

**Fix.** A short section: recommended cores and RAM, the ~1.5 GB model download, rough throughput
expectations, and explicit `NUM_WORKERS` guidance. `zeitnot/cli.py` already prints games/sec, so the data
is free. **Note the hosted-provider path changes the picture** — with `EMBED_PROVIDER=openai` there is no
resident embedding model, so the RAM figure needs stating per configuration rather than once.

**Effort.** S.

**Risk/tradeoff.** Numbers vary wildly by hardware and are easy to misread as guarantees. Frame as observed
ranges with the machine described.

---

### P3-4 · Troubleshooting section

**Problem.** The failure modes are known and enumerable but nowhere documented.

**Fix.** A README section keyed to the actual failures. **The list has changed since the audit** — the
silent ones are fixed, and the rewrite introduced new failure modes that are correct but surprising:

- `no games found ... 404` — username or an empty month (and note the leading-zero requirement).
- Ollama unreachable, Stockfish not on PATH, pgvector extension missing.
- **`embedding provider mismatch`** — the provenance guard refusing a changed `EMBED_PROVIDER`, with
  `zeitnot data reembed` as the remedy. This is new, it is working as designed, and it will read as a
  bug to anyone who meets it cold.
- **Missing API key for a hosted provider**, and what the off-machine data warning means.
- Slow ingestion → tune `NUM_WORKERS`.

**Effort.** S.

**Risk/tradeoff.** Cheap to keep current if entries are added as issues arrive.

---

### P3-5 · README demo

**Problem.** The README opens with an install wall and never shows the tool working. For a project whose
value proposition is "ask questions about your chess games and get real answers," this is the biggest
missed opportunity in the documentation.

**Fix.** A pasted terminal session immediately after the one-line description, showing a real question and
a real answer. A plain fenced code block is 90% of the value at 10% of the effort of a recording.

**Effort.** S.

**Risk/tradeoff.** Needs a working setup and a real dataset. **Check a real transcript for third-party
handles before pasting it.** P0-8 is closed and the prompt no longer carries them (see its amendment
above), but the ingestion progress output still prints `<white> vs <black>` per game.

**Amended 2026-09-14.** The demo that landed was not reproducible on the configuration its own banner
named: the prose was better than `llama3.2` produces, and on a real corpus that model answered the same
question wrongly. Replaced with an unedited session on the default, with the model named underneath.
A demo that oversells is worse than no demo — the first thing a reader does is run it.

---

### P3-6 · Read the environment file — **DONE 2026-09-02**

**What it was.** Nothing loaded `.env`. The file was documented as something the *shell* sourced, which
made the shell zeitnot's configuration loader and every shell pathology a zeitnot bug. Five of P3-4's
troubleshooting entries were one missing step: a file saved with CRLF put a carriage return in the middle
of `DATABASE_URL`; a port defined below the URL that used it expanded to nothing, which libpq reads as
5432; `export $(cat .env | xargs)` split the comments into arguments; and sourcing silently overwrote a
value just exported by hand, which is why a port change so often appeared not to take.

**How.** `zeitnot/envfile.py`, called from a Typer callback so it runs before any subcommand reads the
environment. Carriage returns stripped on read, a byte-order mark dropped, comments and quoting parsed
rather than word-split, and `${VAR}` references resolved against the whole file regardless of line order.
`ZEITNOT_ENV_FILE` redirects it; empty switches it off.

**The precedence is deliberate and inverted from the old behavior:** anything already exported wins over
the file. That is the value the user most recently and most deliberately set, and it is what `docker
compose` sees too. Because the old surprise ran the other way, being right silently is not enough — the
loader records the names it declined to set, and `zeitnot doctor` reports them.

---

### P3-7 · `zeitnot doctor` — **DONE 2026-09-02**

**What it was.** Every startup check existed; none could be run without doing the expensive thing it
guards. The first honest verification of a setup was `zeitnot data analyze`, which needs the network,
Chess.com, Stockfish, PostgreSQL and an embedding provider at once and runs at about a second per game. So
every misconfiguration was found at the most expensive possible moment, **one per attempt**, because each
check correctly aborts at the first failure. With P3-4 at sixteen entries, that is a lot of round trips.

**How.** `zeitnot/doctor.py` runs eleven checks in order of how fast they answer, never stopping at a
failure and changing nothing — no migration, no ingestion, no index adoption, no chat request. Existing
failure messages are reported verbatim rather than re-worded, since they were written against reproduced
failures and already name their remedies. Output is plain ASCII and passes through `redact_secrets`,
because it is written to be pasted into an issue.

Two checks are new rather than re-composed. **The port invariant** — P3-4 documented that
`ZEITNOT_DB_PORT` and `DATABASE_URL` are configured independently and that "nothing checks that they
agree"; doctor is the something. And **Stockfish is started, not merely found**: a `PATH` lookup answers
"is there a file", which a binary for the wrong architecture also passes.

**Consequence for P1-4.** The bug template's environment fields are now one command's output.

---

# P4 — Longer-term maturity

### P4-1 · Versioning and releases

**Problem.** No tags. `pyproject.toml` says `version = "0.1.0"` and nothing has ever been released under
it. ADR 0002's "keep `legacy/` for one release" condition was dropped precisely because no release exists
to hang it on.

**Fix.** Tag `v0.1.0` once P0 is complete, then semver. **The Go-specific hazards are gone** — there is no
module proxy to permanently record a bad tag, so tagging is now recoverable rather than one-way. Two new
decisions replace them: whether to publish to **PyPI** (the name `zeitnot` may not be free) and whether to
ship a **`uv tool` / `pipx` install path** as the documented default, which the rewrite made viable by
removing the build toolchain from the user's prerequisites.

**Effort.** S for tagging; M for PyPI publication with a release workflow.

**Risk/tradeoff.** Tagging `v0.x` deliberately signals an unstable API, which is honest here. Publishing
to PyPI is a name commitment and an ongoing release obligation — worth deferring until P0 is clear.

---

### P4-2 · CHANGELOG

**Problem.** No changelog. **The backlog it would need to cover has grown considerably**: the rewrite
itself, the multi-provider work, the index provenance guard (which changes behavior for anyone switching
embedders), and the two Preserved Defects with their eventual regeneration requirement.

**Fix.** Keep-a-Changelog format, starting at the `v0.1.0` tag, with the rewrite as the first entry.

**Effort.** S, plus ongoing discipline.

**Risk/tradeoff.** Only useful if maintained. Note that the Preserved Defect fixes will each need a
"re-ingestion recommended" note, and the provenance guard means those notes now have a concrete command
attached (`zeitnot data reembed`) rather than vague advice.

---

## Suggested execution order

| Phase | Items | Rationale |
|---|---|---|
| 1 | P0-2, P0-9 | License unblocks everything social; P0-9 is a one-section doc fix |
| 2 | Game Summary regeneration pass | Not a roadmap item — but it *gates* both Preserved Defects, and one `corpus` test now fails until it exists |
| 3 | P0-8 | The last real P0, and the one with a disclosure promise riding on it |
| 4 | P1-4, P2-5 | Templates invite pasted errors, so redaction lands with them, not after |
| 5 | P3-1, P3-2, P3-4 | Biggest onboarding wins, all small |
| 6 | P1-5, P3-3, P3-5 | Contributor entry points and README polish, once the commands are stable |
| 7 | P2-6, P2-7, P4-1, P4-2 | Logging, supply-chain scanning, and the release apparatus |

**Amended 2026-09-02.** Phase 5 was executed out of order and incompletely: P3-4 landed, P3-1 landed
half-way (see its corrected entry), and P3-2 is untouched. Two items that were never on this roadmap were
done instead — P3-6 and P3-7 above — because a setup audit found the on-ramp's real cost was not the
number of steps but that **nothing verified any of them until the most expensive one**. What remains for
onboarding is the application container (P3-1) and the task runner (P3-2), in that order: the wrapper
script P3-1 needs is the natural home for P3-2's targets.

The audit's original estimate was "two to three focused days" to reach contributor-ready. **Most of that
has been spent** — sixteen items closed, and the remainder is roughly one focused day, still mostly
documentation. The exception is P0-8, which is real code — though as of 2026-09-15 it no longer has a
golden-recapture dependency in front of it.
