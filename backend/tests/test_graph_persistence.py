"""Tests for description/keyword persistence through triplets, CSV, and Neo4j payloads."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ul.csv_generator import CSV_COLUMNS, read_triplets_csv, write_triplets_csv
from ul.hierarchy import UL_ROOT_NAME, enforce_hierarchy
from ul.knowledge_graph.ingest import MemoryGraph, ingest_triplets_incremental
from ul.neo4j_service import _entity_description_lookup
from ul.product_normalization import apply_product_identity_to_triplets
from ul.ul_service import (
    _build_entity_description_map,
    _relationships_to_triplets,
)


class GraphPersistenceTests(unittest.TestCase):
    def test_relationships_to_triplets_preserves_description_and_keywords(self) -> None:
        relationships = [
            {
                "source_node": "iPhone 16",
                "source_type": "product",
                "relationship": "has_component",
                "destination_node": "Battery Kit",
                "destination_type": "component",
                "evidence": "iPhone 16 contains the Battery Kit as one of its components.",
                "keywords": "HAS_COMPONENT",
            }
        ]

        triplets = _relationships_to_triplets(relationships)

        self.assertEqual(len(triplets), 1)
        triplet = triplets[0]
        self.assertEqual(triplet["source_node"], "iPhone 16")
        self.assertEqual(triplet["source_type"], "product")
        self.assertEqual(triplet["relationship"], "has_component")
        self.assertEqual(triplet["destination_node"], "Battery Kit")
        self.assertEqual(triplet["destination_type"], "component")
        self.assertEqual(
            triplet["description"],
            "iPhone 16 contains the Battery Kit as one of its components.",
        )
        self.assertEqual(triplet["keywords"], "HAS_COMPONENT")

    def test_apply_product_identity_preserves_metadata(self) -> None:
        triplets = [
            {
                "source_node": "iphone 16",
                "source_type": "product",
                "relationship": "has_component",
                "destination_node": "Battery Kit",
                "destination_type": "component",
                "description": "Relationship evidence",
                "keywords": "HAS_COMPONENT",
            }
        ]
        products = [
            {
                "name": "iPhone 16",
                "aliases": ["iphone 16"],
                "identity_key": "product:iphone 16",
            }
        ]

        enriched = apply_product_identity_to_triplets(triplets, products)

        self.assertEqual(enriched[0]["source_node"], "iPhone 16")
        self.assertEqual(enriched[0]["source_identity_key"], "product:iphone 16")
        self.assertEqual(enriched[0]["description"], "Relationship evidence")
        self.assertEqual(enriched[0]["keywords"], "HAS_COMPONENT")

    def test_enforce_hierarchy_preserves_metadata_without_invented_ul_company_links(self) -> None:
        triplets = [
            {
                "source_node": "Acme Corp",
                "source_type": "applicant",
                "relationship": "produces",
                "destination_node": "Widget",
                "destination_type": "product",
                "description": "Acme produces Widget.",
                "keywords": "PRODUCES",
            }
        ]

        normalized = enforce_hierarchy(triplets, extra_companies=["Acme Corp"])

        produce = next(
            t for t in normalized if t["relationship"] == "has_product"
        )
        self.assertEqual(produce["description"], "Acme produces Widget.")
        self.assertEqual(produce["keywords"], "PRODUCES")

        invented = [
            t
            for t in normalized
            if t["source_node"] == UL_ROOT_NAME and t["relationship"] == "serves"
        ]
        self.assertEqual(invented, [])
        self.assertFalse(
            any(t["relationship"] == "has_company" for t in normalized)
        )
        self.assertTrue(
            any(
                t["source_node"] == UL_ROOT_NAME
                and t["relationship"] == "party_role_applicant"
                and t["destination_node"] == "Acme Corp"
                for t in normalized
            )
        )

    def test_enforce_hierarchy_drops_ul_serves_and_does_not_add_has_company(self) -> None:
        triplets = [
            {
                "source_node": UL_ROOT_NAME,
                "source_type": "company",
                "relationship": "serves",
                "destination_node": "Acme Corp",
                "destination_type": "company",
                "description": "Source states UL serves Acme.",
                "keywords": "SERVES",
            }
        ]

        normalized = enforce_hierarchy(triplets, extra_companies=["Acme Corp"])
        self.assertFalse(any(t["relationship"] == "serves" for t in normalized))
        self.assertFalse(any(t["relationship"] == "has_company" for t in normalized))

    def test_enforce_hierarchy_merges_metadata_on_dedupe(self) -> None:
        triplets = [
            {
                "source_node": "iPhone 16",
                "source_type": "model",
                "relationship": "contains",
                "destination_node": "Battery Kit",
                "destination_type": "component",
            },
            {
                "source_node": "iPhone 16",
                "source_type": "model",
                "relationship": "contains",
                "destination_node": "Battery Kit",
                "destination_type": "component",
                "description": "Duplicate with evidence",
                "keywords": "CONTAINS",
            },
        ]

        normalized = enforce_hierarchy(triplets)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["description"], "Duplicate with evidence")
        self.assertEqual(normalized[0]["keywords"], "CONTAINS")

    def test_csv_generation_includes_description_and_keywords(self) -> None:
        triplets = [
            {
                "source_type": "product",
                "source_node": "iPhone 16",
                "relationship": "has_component",
                "destination_type": "component",
                "destination_node": "Battery Kit",
                "description": "iPhone 16 contains the Battery Kit as one of its components.",
                "keywords": "HAS_COMPONENT",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("ul.csv_generator.config.OUTPUT_DIR", Path(tmp_dir)):
                csv_path = write_triplets_csv(triplets, filename="test.csv")

                with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))

                roundtrip = read_triplets_csv(csv_path)

        self.assertEqual(CSV_COLUMNS[-2:], ["description", "keywords"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_node"], "iPhone 16")
        self.assertEqual(
            rows[0]["description"],
            "iPhone 16 contains the Battery Kit as one of its components.",
        )
        self.assertEqual(rows[0]["keywords"], "HAS_COMPONENT")
        self.assertEqual(roundtrip[0]["description"], rows[0]["description"])
        self.assertEqual(roundtrip[0]["keywords"], rows[0]["keywords"])

    def test_build_entity_description_map_from_graph_data(self) -> None:
        graph_data = {
            "products": [
                {
                    "name": "iPhone 16",
                    "aliases": ["iphone 16"],
                    "evidence": "Apple smartphone product.",
                }
            ],
            "components": [
                {
                    "name": "Battery Kit",
                    "component_description": "Rechargeable battery assembly for the phone.",
                    "aliases": ["Battery Kit"],
                }
            ],
            "companies": [],
            "relationships": [],
        }

        mapping = _build_entity_description_map(graph_data)

        self.assertEqual(
            mapping["product:iphone 16"],
            "Apple smartphone product.",
        )
        self.assertEqual(
            mapping["component:battery kit"],
            "Rechargeable battery assembly for the phone.",
        )

    def test_entity_description_lookup(self) -> None:
        mapping = {"component:battery kit": "Rechargeable battery assembly for the phone."}
        self.assertEqual(
            _entity_description_lookup(mapping, "component", "Battery Kit"),
            "Rechargeable battery assembly for the phone.",
        )
        self.assertEqual(_entity_description_lookup(mapping, "product", "Missing"), "")

    def test_ingest_triplets_does_not_store_evidence_nodes(self) -> None:
        triplets = [
            {
                "source_node": "iPhone 16",
                "source_type": "model",
                "relationship": "contains",
                "destination_node": "Battery Kit",
                "destination_type": "component",
                "description": "Relationship evidence",
                "keywords": "CONTAINS",
                "chunk_id": "chunk_000001",
                "page_number": 3,
            }
        ]
        entity_descriptions = {
            "model:iphone 16": "Apple smartphone product.",
            "component:battery kit": "Rechargeable battery assembly for the phone.",
        }
        store = MemoryGraph()
        count = ingest_triplets_incremental(
            triplets,
            document_id="doc_test",
            graph_id="kg_test",
            entity_descriptions=entity_descriptions,
            store=store,
        )

        self.assertEqual(count, 1)
        self.assertEqual(store.evidence, {})
        rels = list(store.relationships.values())
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["relationship"], "CONTAINS")

    def test_ingest_triplets_skips_unsupported_pairs(self) -> None:
        store = MemoryGraph()
        triplets = [
            {
                "source_node": "IEC 62368-1",
                "source_type": "standard",
                "relationship": "requires_test",
                "destination_node": "TEST-001",
                "destination_type": "test",
            },
            {
                "source_node": "Widget",
                "source_type": "model",
                "relationship": "contains",
                "destination_node": "Battery Kit",
                "destination_type": "component",
            },
        ]
        count = ingest_triplets_incremental(
            triplets,
            document_id="doc_test",
            graph_id="kg_test",
            store=store,
        )

        self.assertEqual(count, 1)
        self.assertEqual(store.count("component"), 1)
        self.assertEqual(store.count("test"), 0)


if __name__ == "__main__":
    unittest.main()
