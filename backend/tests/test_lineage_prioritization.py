"""Tests for question-aware Neo4j lineage prioritization."""

from __future__ import annotations

import unittest

from ul.knowledge_graph.queries import (
    assemble_prioritized_lineage,
    resolve_lineage_targets,
)


def _edge(
    priority: int,
    source_label: str,
    source: str,
    relationship: str,
    destination_label: str,
    destination: str,
) -> dict:
    return {
        "priority": priority,
        "source_label": source_label,
        "source": source,
        "source_canonical_id": f"id:{source}",
        "source_description": "",
        "relationship": relationship,
        "destination_label": destination_label,
        "destination": destination,
        "destination_canonical_id": f"id:{destination}",
        "destination_description": "",
        "description": "",
        "keywords": relationship,
    }


class LineagePrioritizationTests(unittest.TestCase):
    def test_resolve_clause_targets(self) -> None:
        labels, rels = resolve_lineage_targets("What clauses apply to SM-A568U1?")
        self.assertIn("Clause", labels)
        self.assertIn("HAS_CLAUSE", rels)

    def test_resolve_standard_targets(self) -> None:
        labels, rels = resolve_lineage_targets("What standards apply to this model?")
        self.assertIn("Standard", labels)
        self.assertIn("COMPLIES_WITH", rels)

    def test_resolve_certification_targets(self) -> None:
        labels, _rels = resolve_lineage_targets("What certifications does the company have?")
        self.assertIn("Certification", labels)

    def test_clause_path_survives_contains_flood(self) -> None:
        """Priority-0 Standard→Clause path wins even when many CONTAINS rows come first."""
        seed = {
            "label": "Model",
            "name": "Model M",
            "canonical_id": "id:Model M",
            "description": "",
        }
        # Simulate unordered flood: dozens of generic CONTAINS before the relevant path.
        rows: list[dict] = []
        for index in range(40):
            rows.append(
                _edge(
                    2,
                    "Model",
                    "Model M",
                    "CONTAINS",
                    "Component",
                    f"Noise Component {index}",
                )
            )
        # Relevant path edges placed AFTER the flood; assembly must still prefer them.
        relevant = [
            _edge(0, "Model", "Model M", "CONTAINS", "Component", "Battery Pack"),
            _edge(0, "Component", "Battery Pack", "COMPLIES_WITH", "Standard", "UN Manual"),
            _edge(0, "Standard", "UN Manual", "HAS_CLAUSE", "Clause", "Part III subsection"),
            _edge(0, "Product", "Product X", "HAS_MODEL", "Model", "Model M"),
        ]
        ordered = [*rows, *relevant]

        subgraph = assemble_prioritized_lineage(
            ordered,
            seed_nodes=[seed],
            max_nodes=20,
            max_edges=16,
            max_generic_contains=4,
        )

        labels_by_name = {node["name"]: node["label"] for node in subgraph["nodes"]}
        self.assertEqual(labels_by_name.get("Battery Pack"), "Component")
        self.assertEqual(labels_by_name.get("UN Manual"), "Standard")
        self.assertEqual(labels_by_name.get("Part III subsection"), "Clause")
        self.assertEqual(labels_by_name.get("Product X"), "Product")

        rel_types = {edge["relationship"] for edge in subgraph["edges"]}
        self.assertIn("HAS_CLAUSE", rel_types)
        self.assertIn("COMPLIES_WITH", rel_types)

        generic_contains = [
            edge
            for edge in subgraph["edges"]
            if edge["relationship"] == "CONTAINS"
            and str(edge["destination_name"]).startswith("Noise Component")
        ]
        self.assertLessEqual(len(generic_contains), 4)
        self.assertTrue(
            any(edge["destination_name"] == "Battery Pack" for edge in subgraph["edges"])
        )

    def test_generic_contains_capped_when_no_priority_path(self) -> None:
        seed = {
            "label": "Model",
            "name": "Model M",
            "canonical_id": "id:Model M",
            "description": "",
        }
        rows = [
            _edge(2, "Model", "Model M", "CONTAINS", "Component", f"Part {index}")
            for index in range(30)
        ]
        subgraph = assemble_prioritized_lineage(
            rows,
            seed_nodes=[seed],
            max_nodes=40,
            max_edges=40,
            max_generic_contains=8,
        )
        self.assertLessEqual(len(subgraph["edges"]), 8)


if __name__ == "__main__":
    unittest.main()
