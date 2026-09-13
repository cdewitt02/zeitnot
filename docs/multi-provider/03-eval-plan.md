# Multi-Provider Support — Evaluation Plan

> **Status: not being run. Decided 2026-08-31.**
>
> The formal comparison described below was **deliberately not carried out**, and no document should
> treat it as pending work.
>
> **What replaced it.** Several chat models were run manually against the same corpus and found
> comparable in quality, with no material drop from the local default. That observation is weaker than
> the rubric below, but it is not merely impressionistic: because the Assembled Prompt is deterministic
> and identical across providers, the retrieved games, aggregate statistics, and instruction block were
> byte-for-byte the same in every run. The chat model genuinely was the only variable, which is the
> property this plan was built to guarantee.
>
> **Why stop there.** The finding — no provider is clearly better — is the answer that makes a scored
> comparison unnecessary rather than incomplete. Model preference for open-ended coaching prose is
> partly taste, and a rubric score derived from one maintainer's 74 games would not transfer to another
> player's corpus anyway. **Model choice is deliberately left to the user of the repository**, with the
> local default documented as a genuinely adequate starting point (see the README's *Choosing a chat
> model*).
>
> **What this document is still good for.** The question set in §2 remains a useful manual smoke-test
> checklist — two questions per query type, exercising every classifier branch — and §1's controls are
> the correct method for anyone who *does* want to run a scored comparison on their own corpus. The
> file-and-line references throughout still point at the Go tree and would need remapping first.

A lightweight way to compare answer quality across providers once
[`02-migration-plan.md`](./02-migration-plan.md) lands. Deliberately small: a fixed question set, a
human-judged rubric, and a spreadsheet. No scoring model, no eval framework, no new dependencies.

**Why not automate.** Zeitnot's answers are open-ended coaching prose grounded in one player's game
history. There is no reference answer to match against and no labeled dataset. An LLM-as-judge would
itself be a provider whose reliability is exactly what is in question — circular for the one comparison
that matters most. Building the infrastructure to do this properly costs more than the decision it
informs. Start with human judgment on ten questions; automate only the parts that prove tedious.

---

## 1. What is being compared

**Hold everything constant except the chat provider.** Specifically:

- **Same database, same corpus, frozen.** No ingestion between runs.
- **Same embeddings.** Changing the embedding provider changes *retrieval* — different games reach the
  prompt — which confounds chat quality with retrieval quality entirely. Keep `EMBED_PROVIDER=ollama`
  for all chat comparisons. This is the practical payoff of splitting the interfaces
  ([`01-design.md` §2](./01-design.md)).
- **Same `NumSimilar` and `DetailLimit`** (`cmd/chat/main.go:21-22`).
- **Fresh session per question.** Conversation history (`service.go:105-108`) makes answers
  order-dependent; use `/clear` between questions, or one process per question.

Because the system prompt is deterministic given the same corpus, question, and settings, the retrieved
context is **identical across providers**. Every difference in output is attributable to the model. That
is what makes a ten-question comparison meaningful despite its size.

Capture per run: provider, model, full system prompt (already printed at `service.go:111-114`),
question, answer, wall-clock latency, and — once `Usage` exists ([`01-design.md` §3](./01-design.md)) —
input/output tokens.

---

## 2. The question set

Ten questions, two per `QueryType` from `internal/chat/classifier.go:9-29`, since query type determines
which context the router assembles (`router.go:346-366`) and therefore what the model is being asked to
do. A provider that is strong on statistics may be weak at synthesis; a single-category set would hide
that.

| # | Type | Question |
|---|---|---|
| 1 | Aggregate | What's my average centipawn loss? |
| 2 | Aggregate | How many games have I played and what's my win rate? |
| 3 | Comparative | Am I better with white or black? |
| 4 | Comparative | Which time control is my best? |
| 5 | SpecificGames | Show me games where I threw a winning position |
| 6 | SpecificGames | What openings do I lose with most often? |
| 7 | Recommendation | What should I study to improve fastest? |
| 8 | Recommendation | What's my biggest weakness? |
| 9 | Trend | Have I improved over the last month? |
| 10 | Trend | Is my accuracy getting better or worse? |

Verify with `ClassifyQuery` that each lands in its intended bucket before freezing the set — the
classifier is keyword-based (`classifier.go:49-69`) and a rephrasing can silently reroute a question.

**Two adversarial extras**, scored separately, not part of the main comparison:

| # | Purpose | Question |
|---|---|---|
| A1 | Off-topic refusal | What's a good recipe for risotto? |
| A2 | Ungrounded claim | How would I do against Magnus Carlsen? |

A1 tests whether the "only discuss chess" instruction (`router.go:687`, `prompts.go:171-173`) holds
across providers — a plausible divergence, since that instruction was tuned against `llama3.2`. A2 tests
whether a model invents data the corpus does not contain. Both are pass/fail, not rubric-scored.

**Freeze the set** in `docs/multi-provider/eval-questions.md` (or a small fixtures file) when the eval
first runs. Changing questions between runs destroys comparability. Adding new ones is fine; edits are
not.

---

## 3. Rubric

Four criteria, 1–5 each, judged by a human reading answers **blind** — provider labels stripped, order
shuffled per question. Blinding matters: the expectation that the expensive model is better is strong
enough to produce it.

**1. Factual grounding (most important).** Does every number in the answer appear in the system prompt?
The prompt contains exact figures — win rates, CPL, opening records (`router.go:430-509`) — so this is
*checkable*, not a judgment call. It is the criterion most likely to separate a small local model from a
large hosted one.
> 5 = every figure correct and correctly attributed · 3 = mostly right, one loose or unattributed
> number · 1 = fabricated statistics

**2. Relevance.** Does it answer the question asked, using the right part of the context? A Comparative
question answered with an unrequested general improvement plan scores low even if everything in it is
true.
> 5 = directly answers, right context · 3 = answers but drifts · 1 = generic chess advice ignoring the data

**3. Actionability.** Would the player know what to do next? Weighted highest for Recommendation
questions and lowest for Aggregate ones, where the number *is* the answer.
> 5 = specific and tied to the player's own weaknesses · 3 = correct but generic · 1 = no takeaway

**4. Conciseness.** Length appropriate to the question. "What's my average CPL?" does not need six
paragraphs. Verbosity differs sharply between providers and directly affects both usability and cost.
> 5 = tight and complete · 3 = padded but readable · 1 = wall of text, or too terse to be useful

Alongside the scores, record two facts that need no judgment: **latency** and **token usage/estimated
cost**. A model that scores 4.5 at ten times the cost and five times the latency is a different
recommendation than one that scores 4.5 cheaply — and that tradeoff, not a single winner, is the actual
output of this exercise.

**Also record free-text notes.** With ten questions, the most valuable finding is usually a specific
observed failure mode — "consistently misreads the `→` comparison lines," "ignores the games in
`[vs USERNAME]` brackets when citing" — not the mean score. Those notes are what turn into prompt fixes.

---

## 4. Running it

Manual and deliberately unautomated at first:

1. Freeze the corpus; note the game count and username.
2. For each provider: set `CHAT_PROVIDER` / `CHAT_MODEL`, run each of the twelve questions in a fresh
   session, save the output.
3. Paste into a sheet: one row per (question, provider), columns for the four scores, latency, tokens,
   notes.
4. Score blind, one *question* at a time across all providers — comparative judgment is far more
   reliable than absolute scoring, and it is why the answers are laid out side by side.
5. Write a short summary: mean per criterion per provider, cost and latency, the failure modes observed.

A thin script to loop the questions and dump answers to files is worth writing once step 2 has been done
by hand twice. Do not build it before then — the manual pass is what reveals what the script should
capture.

**When to re-run.** Adding a provider; changing a prompt in `router.go` or `prompts.go`; changing
`DetailLimit`; **bumping a provider's pinned default model**; or before recommending a default model in
the README. Not on a schedule.

Note `NumSimilar` is not on that list: it sets retrieval `TopK` and 90 of its 100 games are discarded
before the prompt is built, so changing it does not change what the model sees. It is wasted query
work per question, not prompt bloat — only `DetailLimit` is a cost lever.

**Pinned defaults are what make re-running meaningful.** Because default model IDs are pinned rather
than aliased ([`01-design.md` §4.3](./01-design.md)), a score change between runs is attributable to
something in this repo. Record the exact model ID with every result — under an alias, the same recorded
label could mean two different models.

---

## 5. How this feeds the model-accuracy work

Multi-provider support is the prerequisite for the accuracy phase, and this eval is the bridge. Concrete
handoffs:

**A ceiling measurement.** Running the same prompts through a large hosted model tells you how much of
the current answer quality is limited by the *model* versus by the *retrieved context*. If a frontier
model scores 3/5 on factual grounding with the same prompt, the problem is upstream — retrieval, summary
generation (`internal/summary/generator.go`), or prompt construction — and no amount of model swapping
fixes it. That distinction is the single most useful thing this eval produces, and it is currently
unmeasurable.

**Prompt-portability findings.** Divergent scores on the same prompt localize provider-specific
assumptions — conventions that were written for a small local model. The pre-computed comparison
strings (`router.go:276-295`, e.g. `(3.2% ABOVE overall)`) exist to spare a weak model from doing
arithmetic; against a strong model they are harmless redundancy, and worth keeping precisely because
they make the comparison fairer. Likewise the uppercase section headers and the `[vs USERNAME]` convention
(`router.go:703`). Instructions that only work on one provider are prompt bugs.

**Check `num_ctx` before trusting any local result.** A realistic prompt is ~3k input tokens (~500
out), against an Ollama default `num_ctx` of 2048–4096.
If the local model is silently receiving a truncated prompt — and `writeInstructions` is emitted last in
`BuildPrompt`, so the instructions are the first thing lost — then every local-vs-hosted score
difference is measuring context length, not model capability. **Record the effective `num_ctx` for each
Ollama run, and re-run with it raised above the prompt size.** Without this the eval's headline
comparison is invalid, and the invalidity is invisible.

**A silent-truncation check.** Today's prompt is unbounded and Ollama has been quietly truncating it to
`num_ctx`. If a hosted model — which receives the
prompt in full — scores markedly higher on questions requiring the tail of the context (openings,
rating-band stats, termination breakdowns), that is evidence local answers have been degraded by
truncation all along, not by model capability. It is a distinct fix (raise `num_ctx`, or trim the
prompt) with a distinct owner.

**A reusable harness.** The question set, the rubric, and the sheet are exactly what the accuracy work
needs to answer "did this change help?" — for retrieval tuning, summary-format changes, and prompt
edits. Whatever gets built here should be built to be re-run against a fixed provider with a varying
prompt, not only the reverse.

**A retrieval-quality follow-on.** Once chat providers are compared with embeddings held fixed, the
mirror experiment becomes available: hold the chat provider fixed and vary `EMBED_PROVIDER`. That one
requires re-embedding the corpus and a schema change for non-768-dim models
([`01-design.md` §10](./01-design.md)), so it is a later, larger project — but the same rubric applies,
and the "factual grounding" criterion becomes a proxy for whether the *right* games were retrieved.
