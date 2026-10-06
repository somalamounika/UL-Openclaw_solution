"""Relationship identity and idempotent merge keys."""

from __future__ import annotations

from ul.knowledge_graph.labels import to_relationship_type


def relationship_key(
    source_canonical_id: str,
    relationship: str,
    destination_canonical_id: str,
) -> str:
    rel = to_relationship_type(relationship)
    return f"{source_canonical_id}|{rel}|{destination_canonical_id}"
