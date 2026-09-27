"""
Serialization Package for Enterprise Knowledge Agent.

Includes TOON (Token-Oriented Object Notation) for compact, token-dense
representation of chunks, concept bundles, and catalog indexes.
"""

from backend.serialization.toon import (
    calculate_token_savings,
    deserialize_toon_catalog_entry,
    deserialize_toon_chunk,
    deserialize_toon_table,
    estimate_token_count,
    serialize_toon_catalog_entry,
    serialize_toon_chunk,
    serialize_toon_context,
    serialize_toon_table,
)

__all__ = [
    "serialize_toon_chunk",
    "deserialize_toon_chunk",
    "serialize_toon_catalog_entry",
    "deserialize_toon_catalog_entry",
    "serialize_toon_context",
    "serialize_toon_table",
    "deserialize_toon_table",
    "estimate_token_count",
    "calculate_token_savings",
]
