"""Incremental UL Knowledge Graph: canonical identity, resolution, and provenance."""

from ul.knowledge_graph.canonical_ids import (
    UL_CANONICAL_ID,
    generate_canonical_id,
)
from ul.knowledge_graph.ingest import (
    GLOBAL_GRAPH_ID,
    ingest_triplets_incremental,
)

__all__ = [
    "GLOBAL_GRAPH_ID",
    "UL_CANONICAL_ID",
    "generate_canonical_id",
    "ingest_triplets_incremental",
]
