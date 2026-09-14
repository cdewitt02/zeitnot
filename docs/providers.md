# Providers

Chat and embeddings are selected **independently**, because Anthropic offers no
embeddings API. Both default to Ollama, so an existing setup keeps working
unchanged and the tool still runs with no account, no key, and no network.

| Role | Variable | Values | Default model |
|------|----------|--------|---------------|
| Chat | `CHAT_PROVIDER` | `ollama`, `anthropic`, `openai` | `llama3.2` / `claude-opus-5` / `gpt-5-2025-08-07` |
| Embeddings | `EMBED_PROVIDER` | `ollama`, `openai` | `nomic-embed-text` / `text-embedding-3-small` |

Default model IDs are **pinned, never aliased**: a server-side upgrade behind an
alias would change answers with no code change and no way to notice.

## Local or hosted

| | **Local** (Ollama) | **Hosted** (Anthropic / OpenAI) |
|---|---|---|
| Getting started | ~18 minutes of unattended download, ~1.5 GB | ~8–10 minutes of *active* work: account, billing, an API key |
| Per question | free | billed per token |
| Your games | never leave the machine | summaries and your username go to the provider |
| Hardware | wants RAM and spare cores | none |

Neither wins on answer quality in any way that has been measured — see
[Choosing a chat model](#choosing-a-chat-model). Hosted reaches a first answer
sooner; local needs no account, no key, and no network. Both are fully
supported, and switching chat providers later is cheap and reversible.

**Whichever you pick, expect fifteen minutes minimum before a first answer.**
Ingestion runs Stockfish over every move at roughly a second per game, and no
choice here changes that.

## Choosing a chat model

**Start with the default.** Several chat models were compared informally against
one real corpus and came out close enough that no provider stood out — the local
`llama3.2` default included. There is no measured quality reason to reach for a
hosted API first.

That comparison is more meaningful than it sounds, because the prompt is
deterministic: every provider receives byte-identical retrieved games,
statistics, and instructions, so the model is the only thing that differs. It is
still one person's games and one person's judgment, not a benchmark — which is
exactly why the choice is left to you rather than baked into a recommendation.

Reasons to switch that do **not** depend on answer quality:

- **Hardware.** Local inference wants RAM and CPU that a small laptop or VM may
  not have. A hosted chat provider moves that cost off your machine.
- **Speed.** Hosted models generally return faster than local inference on
  modest hardware.
- **Dropping Ollama entirely.** Only `EMBED_PROVIDER=openai` does that, since
  Anthropic has no embeddings API — and it means re-embedding your index.

Reasons to stay local: no account, no API key, no per-query cost, and **nothing
about your games leaves the machine**.

Because chat and embeddings are selected independently, trying a different chat
model is cheap and reversible — the index is untouched, so switching back costs
nothing. Comparing on your own corpus is the only comparison that is really
about your games; [`multi-provider/03-eval-plan.md`](multi-provider/03-eval-plan.md)
describes how to hold everything else constant if you want to do it properly.

## Using Anthropic for chat

```bash
# in the environment file
ANTHROPIC_API_KEY=sk-ant-...     # never committed; the file is gitignored
CHAT_PROVIDER=anthropic
```

Embeddings stay on Ollama, so **no re-embedding is needed** and the existing
index keeps working — which is also what makes "same index, different chat
model" an honest comparison. Ollama is still a prerequisite for the embedding
half.

**This sends data off-machine.** Selecting a hosted provider sends your game
summaries and Chess.com username to a third party. Both the startup banner and
`zeitnot doctor` say so whenever a hosted provider is active.

Opponents' usernames are **not** sent. Chess.com's termination strings embed the
winner's handle ("Bolzman0 won by resignation"), so those are normalized to the
outcome and method — "lost by resignation" — before they reach the prompt or a
stored summary. Anything in an unrecognized format collapses to "lost by other
means" rather than being passed through.

If you ingested games before this change, run `zeitnot data refresh-stats
<username>` to rebuild the aggregates. Summaries written earlier still carry the
old text until they are regenerated.

Failures are reported, never silently retried on another provider: an Anthropic
error answered from `llama3.2` would leave you comparing outputs without knowing
which model produced which.

## Using OpenAI

OpenAI is the only provider that serves both halves, so it is the one that can
take Ollama off the prerequisite list entirely:

```bash
OPENAI_API_KEY=sk-...
CHAT_PROVIDER=openai
EMBED_PROVIDER=openai
```

`text-embedding-3-small` is asked for **768 dimensions**, which is the width the
`game_summaries` column already declares — so no schema migration is involved.
It is still a *different* embedding model: vectors from two models are not
comparable even at the same width, so switching embed providers on an existing
index is refused at startup, naming the re-embed path. See
[Changing the embedding model](configuration.md#changing-the-embedding-model).
