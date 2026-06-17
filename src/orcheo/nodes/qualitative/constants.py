"""Shared constants for the qualitative-analysis node family."""

from __future__ import annotations


DEFAULT_BATCH_SIZE = 25
MAX_CODING_BATCHES = 200

# Each coding/recoding batch traverses three sub-graph nodes (prepare -> LLM ->
# finalize), so the batch count is bounded by the sub-graph recursion limit.
OPEN_CODING_RECURSION_LIMIT = 3 * MAX_CODING_BATCHES + 40
RECODING_RECURSION_LIMIT = 3 * MAX_CODING_BATCHES + 40

DEFAULT_PER_TURN_BATCH_BUDGET = 1000
DEFAULT_QUOTES_PER_THEME = 3


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_PER_TURN_BATCH_BUDGET",
    "DEFAULT_QUOTES_PER_THEME",
    "MAX_CODING_BATCHES",
    "OPEN_CODING_RECURSION_LIMIT",
    "RECODING_RECURSION_LIMIT",
]
