"""Hybrid retrieval: structured filters from the query, plus vector similarity."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from zeitnot.db.records import SimilarGameResult
from zeitnot.search.filters import GameFilters
from zeitnot.search.parser import QueryParser


class EmbeddingClient(Protocol):
    """The embedding capability HybridSearcher needs. llm.Embedder satisfies it."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class GameSearcher(Protocol):
    def find_similar_games_with_filters(
        self, query_embedding: Sequence[float], filters: GameFilters, limit: int
    ) -> list[SimilarGameResult]: ...

    def count_games_matching_filters(self, filters: GameFilters) -> int: ...


@dataclass(slots=True)
class SearchQuery:
    query: str
    explicit_filters: GameFilters | None = None
    top_k: int = 0
    max_distance: float = 0.0


@dataclass(slots=True)
class SearchResult:
    # ranked by similarity
    games: list[SimilarGameResult] = field(default_factory=list[SimilarGameResult])
    applied_filters: GameFilters | None = None
    semantic_query: str = ""
    extracted_filters: list[str] = field(default_factory=list[str])
    matching_games_count: int = 0


class HybridSearcher:
    def __init__(
        self, embedder: EmbeddingClient, searcher: GameSearcher, default_k: int = 5
    ) -> None:
        self._parser = QueryParser()
        self._embedder = embedder
        self._searcher = searcher
        self._default_k = default_k

    @property
    def parser(self) -> QueryParser:
        return self._parser

    def search(self, query: SearchQuery, username: str) -> SearchResult:
        top_k = query.top_k if query.top_k > 0 else self._default_k

        parse_result = self._parser.parse(query.query, username)
        merged = _merge_filters(parse_result.filters, query.explicit_filters)

        match_count = self._searcher.count_games_matching_filters(merged)

        # A query that parsed away to nothing embeds the original text rather
        # than an empty string — "my blitz losses" is entirely filters, and
        # embedding "" would retrieve an arbitrary neighbourhood.
        semantic_query = parse_result.semantic_query or query.query

        embeddings = self._embedder.embed([semantic_query])
        if len(embeddings) != 1:
            raise RuntimeError(f"expected 1 query embedding, got {len(embeddings)}")

        games = self._searcher.find_similar_games_with_filters(embeddings[0], merged, top_k)

        if query.max_distance > 0:
            games = [g for g in games if g.distance <= query.max_distance]

        return SearchResult(
            games=games,
            applied_filters=merged,
            semantic_query=semantic_query,
            extracted_filters=parse_result.extracted_filters,
            matching_games_count=match_count,
        )


def _merge_filters(parsed: GameFilters, explicit: GameFilters | None) -> GameFilters:
    """Overlay explicitly supplied filters onto the parsed ones.

    Explicit always wins, field by field: a caller that set something meant it,
    and a keyword the parser happened to notice should not override it.
    """
    if explicit is None:
        return parsed

    merged = parsed.clone()
    for name in (
        "result",
        "user_color",
        "time_class",
        "weak_phase",
        "eco_prefix",
        "opening_name",
        "min_blunders",
        "max_blunders",
        "min_mistakes",
        "min_rating",
        "max_rating",
        "date_from",
        "date_to",
    ):
        value = getattr(explicit, name)
        if value is not None:
            setattr(merged, name, value)
    if explicit.username:
        merged.username = explicit.username
    return merged
