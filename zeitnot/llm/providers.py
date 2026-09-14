"""Provider names, default models, and credential variable names.

These live apart from the adapters so `zeitnot.config` can resolve a
configuration — and report an unknown provider — without importing three vendor
SDKs. Each adapter imports its own constants from here, so there is still one
definition of each.

**Default model IDs are pinned to dated snapshots, never floating aliases.** An
alias lets a server-side upgrade change eval results with no code change and no
way to notice; a pinned ID that goes visibly stale is strictly better. Bumping
one is routine maintenance that should re-run the eval question set.
"""

from __future__ import annotations

OLLAMA = "ollama"
ANTHROPIC = "anthropic"
OPENAI = "openai"

# The two lists differ, which is the whole reason chat and embeddings are
# selected separately: Anthropic exposes no embeddings API.
CHAT_PROVIDERS = (OLLAMA, ANTHROPIC, OPENAI)
EMBED_PROVIDERS = (OLLAMA, OPENAI)

# ---------- Ollama ----------

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OLLAMA_DEFAULT_CHAT_MODEL = "llama3.2"
OLLAMA_DEFAULT_EMBED_MODEL = "nomic-embed-text"

# ---------- Anthropic ----------

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

# ---------- OpenAI ----------

OPENAI_DEFAULT_CHAT_MODEL = "gpt-5-2025-08-07"
# The 3-series small model supports the dimensions parameter — the property that
# lets its vectors fit the existing vector(768) column with no migration.
OPENAI_DEFAULT_EMBED_MODEL = "text-embedding-3-small"
# Matches the vector(768) column the schema already declares. The model's native
# width is 1536; requesting 768 truncates server-side.
OPENAI_DEFAULT_EMBED_DIMENSIONS = 768
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

# ---------- shared ----------

# A ceiling, not a target. Answers run ~500 tokens; this only exists so a long
# one is never truncated mid-sentence. On OpenAI it also has to cover reasoning
# tokens, which count against the same budget.
DEFAULT_MAX_TOKENS = 16000

# The project's retry policy, handed to each SDK. Never layer an adapter-level
# loop on top of it.
MAX_RETRIES = 3
