# zeitnot — Context

Glossary of domain terms. Definitions only — no specs, no implementation detail.

When a term here conflicts with usage in a design doc or in code, this file wins; fix the other one.

---

## Chat Provider

A service that turns a prompt into coaching prose. Anthropic, OpenAI, and Ollama are chat providers.
Selected independently of the Embedding Provider.

## Embedding Provider

A service that turns text into a vector for similarity search. OpenAI and Ollama are embedding
providers; **Anthropic is not** — it exposes no embeddings API.

## Provider

Deliberately *not* a term in this project. There is no single "provider" concept, because the set of
chat providers and the set of embedding providers differ. Always say Chat Provider or Embedding
Provider. See [`docs/multi-provider/01-design.md`](docs/multi-provider/01-design.md) §2.

## Index Provenance

The Embedding Provider and model that produced the vectors currently stored in `game_summaries`.
Two vectors of equal width from different models occupy different vector spaces, so comparing them is
meaningless. Provenance is what makes that detectable rather than silent.

## Pinned Default Model

The specific model ID a provider defaults to, chosen deliberately and recorded, never a provider-managed
"latest" alias. Under an alias the same recorded label can mean two different models, so a change in
answers has no attributable cause: the code did not change, and neither did anything the user can see.

Pinning was originally justified by keeping formal eval results comparable across time. That evaluation
is not being run — model choice is left to the user — but pinning is unaffected, because its real subject
is reproducibility rather than scoring. Anyone comparing models on their own corpus, or reporting a
change in answer quality, needs to know which model actually produced the output.

## Ingestion

Fetching a month of games from Chess.com, analyzing every move with Stockfish, generating a Game
Summary, embedding it, and storing all of it. The `cmd/data` entrypoint. Must complete before any
chatting is possible.

## Game Summary

The deterministic natural-language description of one analyzed game, derived only from that game's
Stockfish analysis and its Chess.com metadata. No LLM is involved in generating it, and the same game
always produces the same text. It is what gets embedded for retrieval, and it is stored alongside its
vector in `game_summaries.summary_text`.

Defined by what it is rather than by which package emits it, so the term survives a change of
implementation language. It is *not* the whole of what the Chat Provider sees — see Assembled Prompt.

## Corpus

The set of Game Summaries and their embeddings for one player. What retrieval searches over.

## Assembled Prompt

The complete text handed to the Chat Provider for one question: the retrieved Game Summaries, the
player's aggregate statistics and pre-computed comparisons, and the instruction block. Strictly larger
than the Game Summaries it contains.

It must be **deterministic** — the same question against the same Corpus produces byte-identical text.
That is what makes eval runs comparable across time, and what makes a Go and a Python implementation
comparable to each other. Anything that reaches it in a nondeterministic order is a defect, not a
detail.

## On-Ramp

The path a brand-new user takes from nothing to their first answer. Measured as Time to First Chat.

## Time to First Chat

Wall-clock minutes from "nothing installed" to a first answer about one's own games. Includes
Ingestion, which is why it can never fall below roughly fifteen minutes.

## Preserved Defect

A known-wrong behavior reproduced deliberately rather than fixed, so that a port's output can be
compared byte-for-byte against the implementation it replaces. Each one is marked in the code, listed
in the plan that preserves it, and scheduled to be fixed once the comparison is no longer needed.

Distinct from an ordinary bug in one respect only: fixing it early is *more* expensive than leaving it,
because a deliberate difference makes every other difference ambiguous. Non-determinism can never be a
Preserved Defect — there is nothing stable to preserve.

## Local-First

Runs with no account, no API key, and no network egress beyond the Chess.com public API. It is the code
default and one of the two documented branches of the On-Ramp, not the single recommended one: it costs
about eighteen extra minutes of unattended download, against eight to ten minutes of *active* work —
account, billing, an API key — for the hosted branch. The two costs differ in kind, so the README states
both rather than ranking them. See
[`docs/adr/0001-postgres-in-docker-over-sqlite.md`](docs/adr/0001-postgres-in-docker-over-sqlite.md) and
[`docs/multi-provider/04-onboarding.md`](docs/multi-provider/04-onboarding.md) §4.1.
