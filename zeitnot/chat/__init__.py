"""Query classification, prompt assembly, and the chat service."""

from zeitnot.chat.classifier import QueryType, classify_query, extract_mentioned_openings
from zeitnot.chat.prompts import PromptBuilder, aggregate_game_stats
from zeitnot.chat.router import QueryContext, QueryRouter
from zeitnot.chat.service import NO_DATA_ANSWER, Config, Service

__all__ = [
    "NO_DATA_ANSWER",
    "Config",
    "PromptBuilder",
    "QueryContext",
    "QueryRouter",
    "QueryType",
    "Service",
    "aggregate_game_stats",
    "classify_query",
    "extract_mentioned_openings",
]
