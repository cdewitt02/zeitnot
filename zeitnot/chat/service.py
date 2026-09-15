"""The chat service: one question in, markdown out.

The returned answer is markdown **source**, deliberately unrendered.
Presentation belongs to the caller, so an eval harness and the terminal REPL see
the same text.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from zeitnot.chat.router import QueryContext, QueryRouter
from zeitnot.db import DB
from zeitnot.db.records import SimilarGameResult
from zeitnot.llm.base import (
    ROLE_ASSISTANT,
    ROLE_USER,
    ChatModel,
    ChatRequest,
    Embedder,
    Message,
    StreamingChatModel,
)
from zeitnot.search.filters import GameFilters
from zeitnot.search.hybrid import HybridSearcher

# Returned verbatim when the corpus is empty. It is markdown, like every other
# answer, so the caller can render one thing and not two.
NO_DATA_ANSWER = (
    "I don't have any game data to analyze. "
    "Make sure you've imported and analyzed some games first."
)


@dataclass(slots=True)
class Config:
    chat_model: str = ""
    username: str = ""
    num_similar: int = 0
    detail_limit: int = 0
    max_history_pairs: int = 0


class _DBSearchAdapter:
    """Adapts DB to the GameSearcher protocol.

    A separate object rather than passing DB directly: HybridSearcher should
    depend on two methods, not on the whole database surface.
    """

    def __init__(self, database: DB) -> None:
        self._db = database

    def find_similar_games_with_filters(
        self, query_embedding: Sequence[float], filters: GameFilters, limit: int
    ) -> list[SimilarGameResult]:
        return self._db.find_similar_games_with_filters(query_embedding, filters, limit)

    def count_games_matching_filters(self, filters: GameFilters) -> int:
        return self._db.count_games_matching_filters(filters)


class Service:
    """Wires the two model roles separately.

    They used to be one concrete Ollama client doing double duty; a chat
    provider and an embedding provider are now independently selectable, which
    is what makes "same index, different chat model" a configurable experiment.
    """

    def __init__(
        self,
        database: DB,
        chat_model: ChatModel,
        embedder: Embedder,
        cfg: Config,
    ) -> None:
        num_similar = cfg.num_similar if cfg.num_similar > 0 else 5
        detail_limit = cfg.detail_limit if cfg.detail_limit > 0 else 5
        max_history_pairs = cfg.max_history_pairs if cfg.max_history_pairs > 0 else 4

        self._db = database
        self._chat = chat_model
        self._chat_model_name = cfg.chat_model
        self._username = cfg.username
        self._detail_limit = detail_limit
        self._max_history_pairs = max_history_pairs
        self._history: list[Message] = []

        self._searcher = HybridSearcher(embedder, _DBSearchAdapter(database))
        self._router = QueryRouter(database, self._searcher, cfg.username, num_similar)

    @property
    def router(self) -> QueryRouter:
        """Exposed for the parity harness, which needs the prompt without a
        provider call."""
        return self._router

    def ask(self, question: str) -> str:
        """Answer one question. See `ask_stream` for the streaming form."""
        return self.ask_stream(question, None)

    def ask_stream(self, question: str, on_delta: Callable[[str], None] | None) -> str:
        """`ask` with incremental delivery.

        `on_delta`, when given, receives fragments of the answer as the provider
        produces them; the complete answer is still returned, so a caller that
        streams for display does not have to reassemble it.

        A chat model that does not implement StreamingChatModel still works: the
        whole answer arrives as a single delta. Callers therefore never need to
        ask which provider is configured.

        An exception raised by `on_delta` aborts the request and propagates
        unchanged, so a caller can recognize its own failure.
        """
        qctx = self._router.route(question)

        has_stats = qctx.player_stats is not None and qctx.player_stats.total_games > 0
        if not has_stats and not qctx.games:
            # Not streamed: there is no provider call to stream, and emitting it
            # as a delta would make the caller erase and repaint identical text.
            return NO_DATA_ANSWER

        system_prompt = self.build_prompt(qctx)
        self._debug_prompt(qctx.query_type, system_prompt)

        # The system prompt is a field, not messages[0]: Anthropic takes it as a
        # top-level parameter, and each adapter puts it where its provider wants.
        messages = [*self._history, Message(role=ROLE_USER, content=question)]
        req = ChatRequest(
            system=system_prompt,
            messages=messages,
            model=self._chat_model_name,
        )

        if on_delta is not None and isinstance(self._chat, StreamingChatModel):
            resp = self._chat.chat_stream(req, on_delta)
        elif on_delta is not None:
            resp = self._chat.chat(req)
            on_delta(resp.text)
        else:
            resp = self._chat.chat(req)

        self._history.append(Message(role=ROLE_USER, content=question))
        self._history.append(Message(role=ROLE_ASSISTANT, content=resp.text))
        self._truncate_history()

        return resp.text

    def build_prompt(self, qctx: QueryContext) -> str:
        """The Assembled Prompt, including the filter note.

        Separate from ask_stream so the parity harness can produce exactly what
        the provider would receive without making a provider call. The filter
        note is appended here rather than in the router because it describes the
        search, not the player.
        """
        prompt = self._router.build_prompt(qctx, self._detail_limit)
        if qctx.filters:
            prompt += f"\n\nNote: The search was filtered by: {_format_filters(qctx.filters)}"
        return prompt

    def clear_history(self) -> None:
        self._history.clear()

    def _truncate_history(self) -> None:
        max_messages = self._max_history_pairs * 2
        if len(self._history) > max_messages:
            del self._history[: len(self._history) - max_messages]

    def _debug_prompt(self, query_type: object, system_prompt: str) -> None:
        """Dump the assembled prompt when ZEITNOT_DEBUG_PROMPT is set.

        This used to print unconditionally, which buried every answer under a
        few hundred lines of game summaries. It goes to stderr so that
        redirecting stdout still captures a clean transcript.
        """
        if not os.environ.get("ZEITNOT_DEBUG_PROMPT"):
            return
        print(f"=== QUERY TYPE: {query_type} ===", file=sys.stderr)
        print("=== SYSTEM PROMPT ===", file=sys.stderr)
        print(system_prompt, file=sys.stderr)
        print("=== END PROMPT ===", file=sys.stderr)


def _format_filters(filters: list[str]) -> str:
    """Render the filter list the way Go's `%v` rendered a []string.

    Space-separated inside square brackets. It looks odd in Python and it is
    what the goldens contain, because this string is part of the assembled
    prompt.
    """
    return "[" + " ".join(filters) + "]"
