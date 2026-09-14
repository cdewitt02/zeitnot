# Multi-Provider Support — Design

Proposal for letting zeitnot use Anthropic, OpenAI, Ollama, or others.

**The constraints this design answers to**, from an audit of the pre-multi-provider codebase:

1. Anthropic has no embeddings API → chat and embeddings **cannot** share one provider concept.
2. `vector(768)` is survivable via `dimensions=768`, but same-width/different-space vectors degrade
   retrieval silently — the index needs a provenance stamp, not a schema migration.
3. `cmd/data` hardcoded the Ollama endpoint → had to be fixed before selection meant anything.
4. No `context.Context` on either method → the interface should add it, a signature change at every
   call site.
5. Anthropic's system-parameter and alternation rules break the existing message assembly.
6. Single-string response parsing breaks on block-based and reasoning responses.
7. The prompt is ~3k tokens — cheap on hosted providers, but likely truncated by Ollama's default
   `num_ctx`, cutting the instructions that are emitted last.
8. Chat model was a CLI positional, embed model was env — provider selection must respect both.
9. Zero test coverage on every call site being changed.

**Setup context.** [`04-onboarding.md`](./04-onboarding.md) measures what this work is worth for
onboarding, and its §2.2 changes one thing here materially: hosted **embeddings** are main-line, not
deferred. Without them, switching chat providers still leaves Ollama a prerequisite and saves a new user
about six minutes. Storage is settled separately in [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md).

---

## 1. Recommendation summary

| Question | Recommendation |
|---|---|
| One provider concept, or two? | **Two.** Separate `ChatModel` and `Embedder` interfaces, independently selectable |
| Where does the code live? | New `internal/llm` package + one subpackage per provider |
| Selection mechanism | **Environment variables**, `CHAT_PROVIDER` and `EMBED_PROVIDER` |
| Credentials | Provider-standard env var names (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), validated at startup |
| Fallback on failure | **None. Fail loudly.** Bounded retries for transient errors only |
| Streaming | **Defer**, but shape the interface so it can be added without a breaking change |
| Hosted embeddings | **Main line, not deferred** — `dimensions=768` plus an index provenance stamp, no migration |
| HTTP clients | **Official SDKs for Anthropic and OpenAI; Ollama stays hand-rolled.** SDK owns retry; adapters never layer their own |

---

## 2. Split chat from embeddings

**Recommendation: two independent interfaces, two independent selections.**

The decisive argument is not tidiness — it is that **Anthropic has no embeddings API**: it offers none
and points users to third parties such as Voyage. A unified `Provider` interface with both `Chat`
and `Embed` would force the Anthropic implementation to return `ErrUnsupported` from `Embed`, i.e. a
type that satisfies an interface it cannot honor. Every caller would then need a capability check
before every call, which is the interface failing at its one job.

Splitting also matches how people will actually use this. The stated motivation is cross-checking a
small local model against a large hosted one. That comparison is only meaningful if **retrieval is held
constant** — same embedding model, same index, same retrieved games — while the chat model varies. A
unified provider makes the honest experiment impossible to configure.

Independent selection additionally means:

- Ollama embeddings (free, local, already indexed) + Anthropic chat is the default upgrade path, and it
  requires **no re-embedding**.
- The `vector(768)` migration is only forced when someone deliberately changes the *embedding*
  provider, decoupled from trying a new chat provider.

**Tradeoffs, honestly.** Two env vars instead of one is marginally more to explain. A user who sets only
`CHAT_PROVIDER=anthropic` and expects everything to move will be mildly surprised that embeddings stayed
local — mitigated by printing the resolved configuration at startup (§7). And there is a real
combinatorial surface: any chat provider × any embed provider. In practice that surface is thin, because
the two never interact — they exchange no data, only `[]float32` that goes into Postgres and text that
goes into a prompt.

The rejected alternative — one `Provider` with capability flags — was considered and is not worth its
cost at this project's size.

### 2.1 Index provenance

Because chat and embeddings are selected independently, a user can change `EMBED_PROVIDER` while leaving
an existing index in place. Same width, different vector space, silent degradation — `nomic-embed-text`
and OpenAI's `text-embedding-3-small` at 768 dimensions occupy unrelated spaces, and cosine distance
across them is meaningless rather than merely inaccurate.

Record the Embedding Provider and model that produced the stored vectors — a small `index_meta` row
alongside `game_summaries` — and refuse at startup when the configured embedder does not match, naming
both and pointing at the re-embed path. This is what makes `dimensions=768` safe to rely on: the width
check alone would pass while the vectors were meaningless.

Re-embedding is bounded: `game_summaries` already stores `summary_text`, generated deterministically by
`internal/summary` with no LLM and no Stockfish, so a re-embed reads stored text and updates vectors
without re-running analysis.

### 2.2 Rename `internal/embeddings`

The package is currently named `embeddings` but owns chat too — all Ollama traffic in the repo
originates from one 131-line file, `internal/embeddings/ollama.go`, holding both `GetEmbedding` and
`Chat`. Proposed layout:

```
internal/llm/            interfaces, message types, options, errors, registry
internal/llm/ollama/     Ollama adapter   (Chat + Embed)
internal/llm/openai/     OpenAI adapter   (Chat + Embed)
internal/llm/anthropic/  Anthropic adapter (Chat only)
internal/llm/llmtest/    fakes + shared conformance suite
```

`internal/embeddings` goes away once nothing imports it. `internal/search.EmbeddingClient`
(`search.go:8-10`) can stay exactly as it is — it is already the right shape, and Go's structural typing
means `llm.Embedder` satisfies it with no change to `internal/search` at all.

---

## 3. Proposed interfaces

Illustrative signatures, not final code.

```go
package llm

import "context"

// ---------- Chat ----------

type Role string

const (
    RoleUser      Role = "user"
    RoleAssistant Role = "assistant"
)

// Message is one conversational turn. Note there is no RoleSystem: the system
// prompt is a field on ChatRequest, not a message. That matches Anthropic's
// wire format, and adapters for Ollama and OpenAI prepend it as a message
// themselves. Encoding it this way makes the Anthropic constraint structural
// rather than something each caller has to remember.
type Message struct {
    Role    Role
    Content string
}

type ChatRequest struct {
    System   string    // may be empty
    Messages []Message // must alternate, must begin with RoleUser
    Model    string    // empty => adapter's configured default
    // Optional knobs. Zero values mean "provider default"; adapters omit
    // rather than guess, so we never silently impose OpenAI's defaults on
    // Ollama or vice versa.
    MaxTokens   int
    Temperature *float64
    StopAfter   []string
}

type ChatResponse struct {
    Text         string
    Model        string  // model that actually served the request
    FinishReason string  // normalized: "stop" | "length" | "content_filter" | "other"
    Usage        Usage
}

type Usage struct {
    InputTokens  int
    OutputTokens int
}

type ChatModel interface {
    Chat(ctx context.Context, req ChatRequest) (*ChatResponse, error)
    // Name identifies the provider for error messages, startup banners, and
    // eval result labeling.
    Name() string
}

// ---------- Embeddings ----------

type Embedder interface {
    // Embed returns one vector per input, in input order. Adapters that lack
    // native batching loop internally, so callers need only one code path.
    Embed(ctx context.Context, texts []string) ([][]float32, error)
    // Dimensions reports the vector width this embedder produces, so startup
    // can verify it against the vector(N) column instead of discovering a
    // mismatch mid-ingestion.
    Dimensions() int
    Name() string
}

// ---------- Optional capability, added later ----------

type StreamingChatModel interface {
    ChatModel
    ChatStream(ctx context.Context, req ChatRequest, onDelta func(string) error) (*ChatResponse, error)
}
```

### 3.1 Why each signature differs from today

| Change | Reason |
|---|---|
| `ctx context.Context` first arg | Neither current method takes one — both use `httpClient.Post`, so calls are uncancellable; ingestion cancellation is ignored for up to 10s per in-flight call |
| `System` as a field, not `messages[0]` | Anthropic requires it; making it structural stops callers from reintroducing the bug |
| No `RoleSystem` constant | Removes the only way to construct a message shape Anthropic rejects |
| `ChatResponse` struct, not `string` | Carries `FinishReason` (truncation is currently invisible) and `Usage` (cost visibility for hosted providers) |
| `Embed` takes `[]string` | OpenAI batches natively; ingestion embeds one summary per game and could batch later without another signature change |
| `Dimensions()` | Turns the `vector(768)` constraint into a startup check instead of a mid-run Postgres error |
| `Name()` | Needed for error attribution and for labeling eval outputs |

### 3.2 The model-source asymmetry

Today the embed model is bound at construction (`Client.model`) while the chat model is a per-call
argument — an asymmetry the two call sites answer differently. Resolution: **construction-time default,
per-request override.** `Embedder` has no model field at all (a mixed-model index is always a bug);
`ChatRequest.Model` may override, which is exactly what preserves the existing
`go run cmd/chat/main.go <username> [model]` positional argument.

### 3.3 Constructors and errors

```go
// Each adapter takes its own explicit config struct — no shared bag of
// optional fields, no map[string]string.
func ollama.NewChat(cfg ollama.Config) (llm.ChatModel, error)
func ollama.NewEmbedder(cfg ollama.Config) (llm.Embedder, error)
func anthropic.NewChat(cfg anthropic.Config) (llm.ChatModel, error)
func openai.NewChat(cfg openai.Config) (llm.ChatModel, error)
func openai.NewEmbedder(cfg openai.Config) (llm.Embedder, error)

// Sentinel errors every adapter normalizes to, so callers can react to
// categories without string matching.
var (
    ErrNotConfigured = errors.New("llm: provider not configured")  // missing key/URL — startup
    ErrUnauthorized  = errors.New("llm: authentication failed")     // 401/403
    ErrRateLimited   = errors.New("llm: rate limited")              // 429
    ErrUnavailable   = errors.New("llm: provider unavailable")      // 5xx, dial failure, timeout
    ErrBadResponse   = errors.New("llm: malformed or empty response")
    ErrContextLength = errors.New("llm: input exceeds model context window")
)
```

Errors wrap a sentinel plus provider name plus the underlying cause, so
`errors.Is(err, llm.ErrRateLimited)` works while messages stay readable.

---

## 4. Provider selection: environment variables

**Recommendation: env vars, `CHAT_PROVIDER` and `EMBED_PROVIDER`.**

Justification against existing conventions: the project is entirely env-driven for
infrastructure and tuning (`DATABASE_URL`, `OLLAMA_URL`, `OLLAMA_EMBED_MODEL`, `NUM_WORKERS`), with
positional CLI args reserved for per-invocation choices (username, chat model). Provider is
infrastructure — the same across every invocation on a given machine, and it travels with the API key,
which must be an env var regardless. Putting the provider anywhere else splits one decision across two
mechanisms.

Rejected alternatives:

- **Config file.** Introduces a format, a search path, precedence rules, and a parser dependency, for a
  project with four env vars total. It also creates a second place API keys could end up — a file
  someone might commit. Revisit only if the variable count grows past roughly a dozen.
- **CLI flag.** The repo uses no `flag` package anywhere. Adding flags to `cmd/chat` but not `cmd/data`
  reproduces the config split-brain that is already a P0 bug. A flag would also have to be repeated on
  every invocation, which is wrong for a machine-level setting.

### 4.1 Proposed variables

| Variable | Default | Notes |
|---|---|---|
| `CHAT_PROVIDER` | `ollama` | `ollama` \| `anthropic` \| `openai` |
| `CHAT_MODEL` | provider-specific | Overridden by the positional CLI arg when given |
| `EMBED_PROVIDER` | `ollama` | `ollama` \| `openai` (no `anthropic`) |
| `EMBED_MODEL` | provider-specific | Replaces `OLLAMA_EMBED_MODEL` (which keeps working — §5) |
| `ANTHROPIC_API_KEY` | — | Required iff `CHAT_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | — | Required iff either provider is `openai` |
| `OLLAMA_URL` | `http://localhost:11434` | Unchanged; must be honored by `cmd/data` too |

Precedence for the chat model, most specific first:
**positional CLI arg → `CHAT_MODEL` → provider default.**

### 4.2 Code default vs. documented default

The on-ramp goal and the backward-compatibility constraint in §5 pull opposite ways: existing users need
`ollama` to stay the default, new users should not have to install it.

**Both defaults in code stay `ollama`.** No existing configuration changes behavior, and the tool never
starts spending money or sending data off-machine because a default moved. **The documented quick-start
is the hosted path** — README leads with Compose plus an API key, with local-first presented as a
labeled alternative and its ~18-minute cost stated. Only the recommended reading order changes; the
binary's default behavior stays account-free. Rationale and the open question in
[`04-onboarding.md`](./04-onboarding.md) §4.

### 4.3 Default models are pinned, never aliased

Each provider carries an explicit default model ID, recorded in the README table. **Do not point
defaults at a provider's "latest" alias.**

**Reproducibility is what decides this.** An alias lets a server-side model upgrade change answers with
no code change, no changelog entry, and no way to notice. A pinned ID that goes visibly stale is strictly
better than one that drifts invisibly.

*(This was originally justified by [`03-eval-plan.md`](./03-eval-plan.md) needing runs comparable across
time. That evaluation is not being run — see its status header — and the conclusion is unchanged: the
argument was always about attribution, and it now serves users comparing models on their own corpora.)*

**Cost does not constrain the choice.** At ~3k input and ~500 output tokens per question (§8), any
current-generation model costs a fraction of a cent per answer. Default to answer quality, not price.

**Accepted cost:** pinned IDs go stale as providers deprecate models. Bumping a default becomes routine
maintenance with a changelog line, and the eval question set should be re-run when one changes — which
is exactly the signal an alias would have hidden.

Fill in the concrete IDs at implementation time from current provider documentation rather than from a
design document written months earlier. The decision recorded here is *pinned, not aliased*.

### 4.4 Startup model validation

A quick-start user who sets `CHAT_PROVIDER=anthropic` and then copies the README example verbatim —
`go run cmd/chat/main.go magnus llama3.2` (`README.md:50`) — sends `model: "llama3.2"` to Anthropic,
because the positional argument outranks `CHAT_MODEL` (§3.2). The usage text at `cmd/chat/main.go:31`
(*"chat-model  Ollama model for chat"*) actively encourages it. The failure lands at first question,
naming a model the user never deliberately chose.

**Validate the configured model against the provider at startup**, folded into the reachability preflight
rather than added as a separate step:

| Provider | Call | Catches |
|---|---|---|
| Ollama | `GET /api/tags` | server down; **model not pulled** |
| OpenAI | models list | bad key; unknown model |
| Anthropic | models list | bad key; unknown model |

The Ollama case is worth having on its own merits — "model not pulled" is a top setup failure, and
readiness P3-4 already wanted this check. Here it does double duty.

**Authority from the live check; helpfulness from a heuristic.** When the model is absent from the
provider's list, a name-shape heuristic may *enrich the error message* — "`llama3.2` is not available on
Anthropic; that looks like an Ollama model. Did you mean `CHAT_PROVIDER=ollama`? Note the README example
passes a model positionally." A heuristic must never **gate** startup, only explain a failure the live
check already established.

**The failure mode to get right: do not hard-fail when the *list call itself* fails.** A models endpoint
can be missing or flaky — notably behind OpenAI-compatible gateways (LiteLLM, OpenRouter, local vLLM)
reached via a base-URL override, which often do not implement it. Blocking startup there would break
valid setups over an auxiliary call.

- Model absent from a **successful** list → hard fail, with the enriched message.
- List call **fails for any non-auth reason** → warn and continue; the real call will report the truth.
- List call returns **401/403** → hard fail. That is the credential check, not the model check (§7.1).

An unknown provider name fails at startup listing the valid values. `EMBED_PROVIDER=anthropic` fails
with a message that says Anthropic offers no embeddings API and suggests keeping `ollama` or using
`openai` — never a silent fallback.

---

## 5. Backward compatibility

Both defaults are `ollama`, so an existing user with `OLLAMA_URL` and `OLLAMA_EMBED_MODEL` set and
nothing else keeps working with zero changes.

`OLLAMA_EMBED_MODEL` should be **kept as a working alias** for `EMBED_MODEL` when
`EMBED_PROVIDER=ollama`, resolved as `EMBED_MODEL` → `OLLAMA_EMBED_MODEL` → default. It is documented in
the README today and costs one line to honor. Do not print a deprecation warning yet; the project has no
released versions, and a warning on a documented variable is noise.

**Where this constrains the design:** it rules out a clean single `LLM_*` namespace, and it means the
Ollama adapter must accept its endpoint from `OLLAMA_URL` rather than a uniform `<PROVIDER>_URL`. That
asymmetry is worth it. The alternative — renaming the two variables the README already documents — is a
gratuitous break for users of a project whose setup is already hard.

---

## 6. Credentials

The project handles **no secrets at all today** — Chess.com's public API is unauthenticated and Ollama
is local, so an API key would be the first credential this project has ever held. This introduces it, so
the posture should be explicit.

- **Env vars only.** No key file, no keyring, no CLI flag (flags land in shell history and `ps` output).
- **Provider-standard names** — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. Users likely have them exported
  already, and the standard names are what every provider doc, SDK, and troubleshooting answer uses. Do
  not invent `ZEITNOT_ANTHROPIC_KEY`.
- **`.env` stays gitignored** (it already is). Add `.env.example` with **empty** values and a comment
  pointing at each provider's console. Never commit a key-shaped placeholder.
- **Local-first stays the default.** `ollama`/`ollama` means zeitnot still runs with no account, no key,
  and no network. Hosted providers are opt-in, and the README should say so plainly rather than
  presenting them as the recommended path.
- **Never log the key.** No key in error messages, no key in the startup banner. When a key is missing,
  name the *variable*, not its value: `CHAT_PROVIDER=anthropic requires ANTHROPIC_API_KEY`.
- **Validate at startup, not first use.** `cmd/chat` currently prints a full welcome banner and accepts
  a question before revealing that Ollama was unreachable
  ([`opensource-readiness/01-roadmap.md:241`](../opensource-readiness/01-roadmap.md)). Do not extend that
  pattern to an auth failure. Presence-check keys and resolve config **before** the banner.
- **Say what egress means.** Selecting a hosted provider sends game summaries and the player's username
  to a third party. One sentence in the README and one line in the startup banner. Users choosing this
  tool for local-first reasons deserve to see the moment that changes.

---

## 7. Error handling and fallback

**Recommendation: fail loudly. No automatic fallback to Ollama.**

Reasoning specific to this project:

1. **Silent fallback destroys the stated motivation.** The point of multi-provider support is
   cross-checking a small local model against a large hosted one. If an Anthropic failure silently
   answers from `llama3.2`, the user is comparing outputs without knowing which model produced which —
   the exact failure mode the feature exists to prevent.
2. **It contradicts the existing failure philosophy** — or rather, it repeats the bug the codebase
   already has. `GetEmbedding` returning `(nil, nil)` on an error response
   is precisely "degrade silently," and it can
   poison the vector index. Fallback would be the same mistake with better intentions.
3. **The fallback may not exist.** A user who chose Anthropic may have no Ollama running and no
   `llama3.2` pulled. "Fall back" would mean a connection-refused error attributed to the wrong
   provider.
4. **Interactive REPL, cheap retry.** The user is sitting at a prompt. Reporting the error and letting
   them re-ask costs seconds.

### 7.1 Failure taxonomy

| Failure | When detected | Behavior |
|---|---|---|
| Unknown provider name | Startup | Exit with valid values listed |
| Missing API key | Startup | Exit naming the variable |
| Configured model unknown to provider | Startup | Hard fail with the enriched message (§4.4). If the model-list call itself fails for a non-auth reason, warn and continue instead |
| Provider unreachable / dial refused | Startup preflight, and per call | Preflight fails with the remedy ("is Ollama running?" / "check network"); at call time, error to the user, REPL continues |
| 401 / 403 | Any | No retry. Error names the variable to check |
| 429 rate limited | Per call | Retry up to 3 times, exponential backoff with jitter, honoring `Retry-After`; then surface `ErrRateLimited` |
| 5xx / timeout | Per call | Same bounded retry, then `ErrUnavailable` |
| Malformed or empty response | Per call | **No retry** (deterministic). `ErrBadResponse`. Explicitly includes empty content — the case nothing checks for today |
| Context-length exceeded | Per call | `ErrContextLength` with the remedy: lower `DetailLimit` (not `NumSimilar` — it does not affect prompt size), or `/clear` |
| Embedding dimension mismatch | Startup | Compare `Embedder.Dimensions()` against the `vector(N)` column and refuse to start. This is the check that turns the §4.2 landmine into a clear message |
| Index provenance mismatch | Startup | Configured embedder differs from the one that built the index (§2.1). Refuse to start, naming both and the re-embed path. Width alone would pass |

### 7.2 Retry ownership — exactly one layer

Retries belong **inside the adapter layer**, never at call sites, so `internal/chat` stays free of retry
logic. But with SDKs in play (§11), "inside the adapter" is ambiguous, and getting it wrong is worse than
not retrying at all.

**The rule: exactly one component retries a given request. For Anthropic and OpenAI, that component is
the SDK.**

Both SDKs retry internally by default. An adapter that wraps an SDK call in its own 3-attempt loop
produces **3 × 3 = 9 requests** against an endpoint that just said 429 — the precise behavior a rate
limit exists to prevent, and a good way to earn a longer backoff or a suspended key. So:

- **Anthropic, OpenAI:** configure the SDK's max-retries to the project's policy (3 attempts total) and
  let it own backoff and `Retry-After`. The adapter adds **no retry loop**. It maps the final error onto
  the sentinels in §3.3 once retries are exhausted.
- **Ollama:** hand-rolled, so the adapter owns retry — but it should be **minimal or absent**. Ollama is
  a local process: 429 does not occur, a dial failure means it is not running, and a 5xx usually means
  the model failed to load. Retrying a local service that is down wastes the user's time instead of
  telling them to start it. Fail fast with the remedy.

**This makes retry behavior deliberately non-uniform across providers**, which is fine as long as it is
explicit. The shared conformance suite (see [`02-migration-plan.md`](./02-migration-plan.md) Phase 2)
must therefore assert on **error classification** — 429 maps to `ErrRateLimited`, empty content maps to
`ErrBadResponse` — and **not** on attempt counts, which legitimately differ.

**Context deadlines bound the whole thing.** Because `Chat` and `Embed` take a `context.Context` (§3.1),
a caller's timeout caps total elapsed retry time regardless of which layer is counting. That is the
backstop that makes a misconfiguration survivable rather than a hang.

**Ingestion is the one place to reconsider.** A 429 partway through a thousand-game run is expensive to
restart. But the existing `WorkerPool` already cancels the whole run on any worker error
(`worker.go:167-175`) and ingestion is resumable — `cmd/data/main.go:231-240` skips already-analyzed
games. So failing loudly and re-running is already the designed recovery path; no fallback needed here
either.

---

## 8. Model and provider quirks

| Concern | Ollama | OpenAI | Anthropic |
|---|---|---|---|
| System prompt | `messages[0]` | `messages[0]` (or `instructions`) | **Top-level `system` param** — never a message |
| Message ordering | Lenient | Lenient | Must start `user`, must alternate |
| Response shape | `message.content` string | `choices[0].message.content` | **Array of content blocks** — filter to text |
| Embeddings | Yes | Yes | **None** |
| Native dimensions | 768 (`nomic-embed-text`) | 1536 (`3-small`), truncatable | n/a |
| Effective context | **Silently truncated to `num_ctx`** | Errors when exceeded | Errors when exceeded |
| Cost of a long prompt | Free | Billed per token | Billed per token |
| Tool calling | Model-dependent | Yes | Yes |
| Streaming | Yes | Yes | Yes |

Design consequences:

- **The system-prompt and block-response differences are adapter responsibilities, full stop.**
  `internal/chat` must never learn which provider it is talking to. The interface in §3 is shaped to
  make that automatic.
- **Silent truncation is the sharpest behavioral difference — not cost.** A realistic prompt is ~3k
  input tokens (~500 out), which is a fraction of a cent per
  question on any hosted provider. Bill shock is not a real risk and **token budgeting does not merit
  priority**. What matters is the other side: ~3k plus history likely exceeds Ollama's default
  `num_ctx`, so the local path is being truncated today — and the instructions are emitted last, so they
  go first. Populating `Usage` in `ChatResponse` gives real numbers for the first time and is the cheap
  fix worth doing when the first hosted adapter lands. Note `NumSimilar` is **not** a cost lever: it
  sets retrieval `TopK`, and 90 of its 100 games are discarded before the prompt is built. Only
  `DetailLimit` moves prompt size.
- **Tool calling is out of scope now.** No feature uses it. `ChatRequest`/`ChatResponse` being structs
  rather than strings means tools can be added as new fields without breaking any caller — that is the
  extent of the accommodation it deserves today.
- **Reasoning models** need adapters to drop thinking blocks from `Text`. Cheap to handle in the
  Anthropic and OpenAI adapters from day one; expensive to retrofit after users report empty answers.
- **Prompt portability.** The pre-computed comparison strings (`router.go:276-295`) and the uppercase
  section headers are portable and should stay — they make cross-provider comparison fairer, not less.
  The bracketed in-user-turn wrapper (`prompts.go:168-178`) should **not** be carried into any new path;
  it is a small-model workaround, its only caller is dead code, and on Anthropic it can read as an
  injection attempt.

---

## 9. Streaming

**Recommendation: defer. Do not build it in the first pass.**

- It changes nothing about correctness — the same tokens arrive either way.
- The current UX already accommodates the wait: `cmd/chat/main.go:127` prints `Thinking...` and blocks
  on a single call.
- Hosted providers are typically *faster* than local inference, so adding hosted support **reduces** the
  latency pressure that would justify streaming.
- Streaming multiplies the adapter surface: three more wire formats (Ollama's NDJSON, OpenAI's SSE
  deltas, Anthropic's typed SSE events), three more error paths for mid-stream failures, and a REPL
  rewrite.
- It is orthogonal to the eval work ([`03-eval-plan.md`](./03-eval-plan.md)), which compares final
  outputs.

**What to do now so it is cheap later:** keep `ChatModel` non-streaming, and let a provider optionally
implement `StreamingChatModel` (§3). The REPL type-asserts; adapters that do not implement it keep
working. That is a purely additive change with no breakage — which is exactly why it can wait.

> **Update — since shipped.** Streaming landed on both adapters after this document was written, on
> exactly the terms above: `ChatModel` unchanged, `StreamingChatModel` implemented by Anthropic (typed
> SSE events) and Ollama (NDJSON), and `chat.Service.AskStream` type-asserting with a single-delta
> fallback. The "REPL rewrite" cost was smaller than estimated because rendering, not streaming, owns
> the terminal: see `internal/render`. The equivalence between the streamed and buffered paths is
> pinned by `llmtest.StreamConformance`.

---

## 10. Open questions

1. **Delete or port `AskWithDetails`?** — **Decided: delete**, in Phase 1. It is unreferenced
   (`service.go:136`), and deleting it also removes the only caller of `WrapUserQuestion`
   (`prompts.go:168-178`) — the bracketed in-user-turn wrapper that is a small-model steering workaround
   and reads as an injection attempt on Anthropic.
   `BuildFollowUpPrompt` (`prompts.go:158`) goes with it, also unreferenced. Dead code should not be
   ported to a new interface, and this particular dead code would have to be redesigned to survive a
   provider swap.
2. **Voyage as a fourth adapter?** It is the natural embeddings pairing for Anthropic users. Not needed
   for the first pass — Ollama embeddings remain free and already indexed — but it is the most likely
   next adapter, and `Embedder` accommodates it with no change.
3. **Multi-dimension schema — mostly dissolved.** Previously listed here as a blocker requiring a
   parameterized `vector(N)` or per-provider tables. `dimensions=768` plus the provenance stamp (§2.1)
   covers the providers that matter today with no migration. A parameterized column becomes necessary
   only for an embedding model that cannot emit 768 — a real but narrower problem, and one that
   [`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md) accepts as a consequence of keeping
   Postgres.
4. **Hand-rolled HTTP or official SDKs?** — **Decided. See §11.**

---

## 11. HTTP clients: SDKs for hosted providers, hand-rolled for Ollama

**Decision: official SDKs for Anthropic and OpenAI. Ollama keeps the hand-rolled `net/http` client.**

The two cases differ in how fast the code rots.

**Ollama's surface is small and stable** — two endpoints with flat JSON, already written and working in
131 lines. The official `ollama` Go package means
importing the whole application module to reach `/api/chat` and `/api/embeddings`. Not worth it.

**Anthropic's and OpenAI's surface is neither.** Content-block arrays, thinking blocks to skip, and
`finish_reason` variants are exactly what changes
with model releases — and exactly what a hand-rolled client gets subtly wrong and then carries forever.
Add `anthropic-version` and beta headers, `Retry-After` semantics, and SSE parsing if streaming ever
lands (§9).

**Two objections that no longer apply.** Dependency weight was the main one, and
[`ADR 0001`](../adr/0001-postgres-in-docker-over-sqlite.md) removed the single-binary goal that gave it
force — the Docker image already carries a Debian base and Stockfish, so a few MB is noise. Testability
was the other: both SDKs accept a custom base URL and `http.Client`, so the `httptest.Server` fixture
strategy in Phase 2 works unchanged. It in fact **shrinks** the test surface, since the conformance suite
then covers this project's normalization rather than JSON decoding.

**Consequences to accept:**

- `go.mod` gains its first LLM dependencies, in Phase 2 and Phase 3 — the first phases that touch it at
  all.
- SDK major-version bumps become a maintenance item. Contained to one package each.
- Retry ownership moves to the SDK for hosted providers, which is what §7.2 specifies. This is the part
  most likely to be implemented wrong by reflex.
- SDKs retry only what they classify as retryable. Sentinel mapping (§3.3) still happens in the adapter,
  after retries are exhausted.

**Not an ADR:** the adapter boundary makes this per-provider and reversible without touching any caller,
so it fails the hard-to-reverse test.
