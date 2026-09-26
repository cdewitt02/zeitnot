"""Ported from internal/search/hybrid_test.go, plus the chat service.

Search was previously untestable in Go: it depended on a concrete client that
required a live Ollama. The fakes are what make this — and the chat service
tests below it — possible at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from tests.llmtest import FakeChatModel, FakeEmbedder, FakeStreamingChatModel
from zeitnot.chat.classifier import QueryType
from zeitnot.chat.service import NO_DATA_ANSWER, Config, Service
from zeitnot.db.records import GameRecord, SimilarGameResult
from zeitnot.models import ColorStats, PlayerStats
from zeitnot.search import GameFilters, HybridSearcher, SearchQuery


@dataclass
class StubSearcher:
    """Stands in for the database."""

    count: int = 0
    results: list[SimilarGameResult] = field(default_factory=list[SimilarGameResult])
    error: Exception | None = None

    got_embedding: Sequence[float] = ()
    got_limit: int = 0
    got_filters: GameFilters | None = None

    def find_similar_games_with_filters(
        self, query_embedding: Sequence[float], filters: GameFilters, limit: int
    ) -> list[SimilarGameResult]:
        self.got_embedding, self.got_filters, self.got_limit = query_embedding, filters, limit
        if self.error is not None:
            raise self.error
        return self.results

    def count_games_matching_filters(self, filters: GameFilters) -> int:
        return self.count


def test_search_embeds_the_semantic_remainder() -> None:
    embedder = FakeEmbedder(768)
    searcher = StubSearcher(
        count=12,
        results=[
            SimilarGameResult(game_uuid="a", summary_text="won with the Sicilian", distance=0.1),
            SimilarGameResult(game_uuid="b", summary_text="lost on time", distance=0.4),
        ],
    )

    result = HybridSearcher(embedder, searcher).search(
        SearchQuery(query="my losses as black in blitz", top_k=7), "magnus"
    )

    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 1
    # The filters were parsed out, so what gets embedded is the remainder —
    # not the whole question, and not an empty string.
    assert "blitz" not in embedder.calls[0][0]
    assert "black" not in embedder.calls[0][0]
    assert len(searcher.got_embedding) == 768
    assert searcher.got_limit == 7, "the requested top_k must reach the searcher"
    assert len(result.games) == 2
    assert result.matching_games_count == 12

    assert searcher.got_filters is not None
    assert searcher.got_filters.result == "loss"
    assert searcher.got_filters.user_color == "black"
    assert searcher.got_filters.time_class == "blitz"
    assert searcher.got_filters.username == "magnus"


def test_search_falls_back_to_the_whole_query_when_nothing_semantic_remains() -> None:
    """ "blitz losses" is entirely filters, so the remainder is empty.

    Embedding the empty string would retrieve an arbitrary neighbourhood, which
    is worse than embedding a question whose every term is also a filter.
    """
    embedder = FakeEmbedder(768)
    HybridSearcher(embedder, StubSearcher(count=1)).search(
        SearchQuery(query="blitz losses"), "magnus"
    )
    assert embedder.calls[0][0] == "blitz losses"

    # One surviving word is enough to keep the remainder: the fallback is for
    # an empty string, not for a short one.
    embedder = FakeEmbedder(768)
    HybridSearcher(embedder, StubSearcher(count=1)).search(
        SearchQuery(query="my blitz losses"), "magnus"
    )
    assert embedder.calls[0][0] == "my"


def test_search_applies_max_distance() -> None:
    searcher = StubSearcher(
        count=2,
        results=[
            SimilarGameResult(game_uuid="near", distance=0.1),
            SimilarGameResult(game_uuid="far", distance=0.9),
        ],
    )
    result = HybridSearcher(FakeEmbedder(768), searcher).search(
        SearchQuery(query="endgames", max_distance=0.5), "magnus"
    )
    assert [g.game_uuid for g in result.games] == ["near"]


def test_search_propagates_an_embedding_failure() -> None:
    """An embedding failure must surface, not produce a search against a zero
    vector."""
    embedder = FakeEmbedder(768, error=RuntimeError("embedder is down"))
    with pytest.raises(RuntimeError, match="embedder is down"):
        HybridSearcher(embedder, StubSearcher(count=3)).search(
            SearchQuery(query="anything"), "magnus"
        )


def test_explicit_filters_beat_parsed_ones() -> None:
    """A caller that set something meant it; a keyword the parser happened to
    notice must not override it."""
    searcher = StubSearcher(count=1)
    HybridSearcher(FakeEmbedder(768), searcher).search(
        SearchQuery(
            query="my blitz wins",
            explicit_filters=GameFilters(time_class="rapid", result="loss"),
        ),
        "magnus",
    )
    assert searcher.got_filters is not None
    assert searcher.got_filters.time_class == "rapid"
    assert searcher.got_filters.result == "loss"


def test_default_top_k_applies_when_none_is_requested() -> None:
    searcher = StubSearcher(count=1)
    HybridSearcher(FakeEmbedder(768), searcher).search(SearchQuery(query="anything"), "magnus")
    assert searcher.got_limit == 5


# ---------- chat service ----------


class StubDB:
    """The two DB methods Service and QueryRouter actually reach for."""

    def __init__(self, stats: PlayerStats | None, results: list[SimilarGameResult]) -> None:
        self._stats = stats
        self._results = results

    def get_player_stats(self, username: str) -> PlayerStats | None:
        return self._stats

    def find_similar_games_with_filters(
        self, query_embedding: Sequence[float], filters: GameFilters, limit: int
    ) -> list[SimilarGameResult]:
        return self._results

    def count_games_matching_filters(self, filters: GameFilters) -> int:
        return len(self._results)


def _stats() -> PlayerStats:
    stats = PlayerStats(username="magnus", total_games=10, wins=6, losses=3, draws=1, avg_cpl=42.0)
    stats.stats_by_color = {
        "white": ColorStats(games=5, wins=4, win_rate=80.0, avg_cpl=40.0),
        "black": ColorStats(games=5, wins=2, win_rate=40.0, avg_cpl=44.0),
    }
    return stats


def _game(uuid: str, summary: str) -> SimilarGameResult:
    return SimilarGameResult(
        game_uuid=uuid,
        summary_text=summary,
        distance=0.1,
        game=GameRecord(uuid=uuid, white_username="magnus", black_username="opponent"),
    )


def _service(
    chat: FakeChatModel, stats: PlayerStats | None, results: list[SimilarGameResult]
) -> Service:
    return Service(
        StubDB(stats, results),  # type: ignore[arg-type]
        chat,
        FakeEmbedder(768),
        Config(username="magnus", num_similar=5, detail_limit=5),
    )


def test_an_empty_corpus_answers_without_calling_the_provider() -> None:
    """Not streamed and not a provider call: there is nothing to stream, and
    emitting it as a delta would make the caller erase and repaint identical
    text."""
    chat = FakeChatModel()
    assert _service(chat, None, []).ask("anything") == NO_DATA_ANSWER
    assert chat.requests == [], "the provider must not be called with no data"


def test_the_system_prompt_is_a_field_not_a_message() -> None:
    """The rule the whole llm.Message design exists to enforce: callers cannot
    construct a shape Anthropic rejects."""
    chat = FakeChatModel()
    _service(chat, _stats(), [_game("a", "won as white in blitz\n")]).ask("Am I better with white?")

    req = chat.requests[0]
    assert req.system.startswith("You are a chess coach for magnus.")
    assert [m.role for m in req.messages] == ["user"]
    assert all(m.role != "system" for m in req.messages)


def test_history_alternates_and_is_truncated_to_the_configured_pairs() -> None:
    """Truncation keeps the *most recent* pairs, and the result must still begin
    with a user turn — every adapter rejects anything else."""
    chat = FakeChatModel()
    service = Service(
        StubDB(_stats(), [_game("a", "won as white in blitz\n")]),  # type: ignore[arg-type]
        chat,
        FakeEmbedder(768),
        Config(username="magnus", num_similar=5, detail_limit=5, max_history_pairs=2),
    )

    for i in range(5):
        service.ask(f"question {i}")

    last = chat.requests[-1]
    # Two kept pairs (four messages) plus the current question.
    assert len(last.messages) == 5
    assert last.messages[0].role == "user"
    assert [m.role for m in last.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert last.messages[-1].content == "question 4"


def test_clear_history_resets_the_conversation() -> None:
    chat = FakeChatModel()
    service = _service(chat, _stats(), [_game("a", "won as white in blitz\n")])
    service.ask("first")
    service.ask("second")
    assert len(chat.requests[-1].messages) == 3

    service.clear_history()
    service.ask("third")
    assert len(chat.requests[-1].messages) == 1


def test_streaming_and_buffered_answers_are_the_same_text() -> None:
    """The equivalence the caller depends on: what is displayed and what is
    remembered must be one answer."""
    chat = FakeStreamingChatModel()
    service = _service(chat, _stats(), [_game("a", "won as white in blitz\n")])

    deltas: list[str] = []
    answer = service.ask_stream("Am I better with white?", deltas.append)
    assert "".join(deltas) == answer
    assert len(deltas) >= 2


def test_a_non_streaming_model_still_works_through_ask_stream() -> None:
    """A chat model that does not implement StreamingChatModel still works: the
    whole answer arrives as one delta, so callers never have to ask which
    provider is configured."""
    chat = FakeChatModel()
    service = _service(chat, _stats(), [_game("a", "won as white in blitz\n")])

    deltas: list[str] = []
    answer = service.ask_stream("Am I better with white?", deltas.append)
    assert deltas == [answer]


def test_debug_prompt_is_printed_before_provider_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chat = FakeChatModel(error=RuntimeError("provider is down"))
    service = _service(chat, _stats(), [_game("a", "won as white in blitz\n")])
    monkeypatch.setenv("ZEITNOT_DEBUG_PROMPT", "1")

    with pytest.raises(RuntimeError, match="provider is down"):
        service.ask("Am I better with white?")

    stderr = capsys.readouterr().err
    assert "=== SYSTEM PROMPT ===" in stderr
    assert "You are a chess coach for magnus" in stderr
    assert "=== END PROMPT ===" in stderr


def test_the_filter_note_is_appended_in_gos_slice_format() -> None:
    """`%v` on a []string is space-separated inside brackets. It looks odd in
    Python and it is part of the assembled prompt, so it is preserved."""
    chat = FakeChatModel()
    service = _service(chat, _stats(), [_game("a", "lost as black in blitz\n")])
    service.ask("my blitz losses as black")

    system = chat.requests[0].system
    assert "Note: The search was filtered by: [" in system
    assert "color: black" in system
    assert "result: loss" in system
    assert "time control: blitz" in system


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("What's my average centipawn loss?", QueryType.AGGREGATE),
        ("Am I better with white or black?", QueryType.COMPARATIVE),
        ("Show me games where I threw a winning position", QueryType.SPECIFIC_GAMES),
        ("What should I study to improve fastest?", QueryType.RECOMMENDATION),
        ("Have I improved over the last month?", QueryType.TREND),
        # Precedence: this is both a recommendation and a comparison, and
        # recommendation is the more specific intent, so it wins.
        ("What's my biggest weakness?", QueryType.RECOMMENDATION),
    ],
)
def test_classification_precedence(question: str, want: QueryType) -> None:
    from zeitnot.chat.classifier import classify_query

    assert classify_query(question) is want
