"""Tests for the planned lineage pipeline (Phase 1: hardcoded plan).

Covers the controlled Cypher builder, plan validation, and the direct
path-to-graph-evidence conversion. No Neo4j connection is required: path
objects are stubbed so the conversion is exercised in isolation.
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

import ul.chatbot as chatbot_mod
from ul.knowledge_graph.queries import (
    KNOWN_LABELS,
    LINEAGE_PATHS,
    LINEAGE_SCHEMA,
    LineagePlan,
    LineagePlanError,
    build_lineage_cypher,
    paths_to_graph_evidence,
    validate_lineage_plan,
)


def path_pattern(cypher: str) -> str:
    """The hop chain from the MATCH path clause, with layout whitespace removed."""
    block = cypher.split("MATCH path =", 1)[1].split("\n\n", 1)[0]
    return re.sub(r"\s+", "", block)


class StubNode:
    def __init__(self, label: str, name: str, description: str = "") -> None:
        self.labels = {label}
        self._props = {"name": name, "description": description}

    def get(self, key, default=None):
        return self._props.get(key, default)


class StubRelationship:
    def __init__(self, start: StubNode, rel_type: str, end: StubNode) -> None:
        self.start_node = start
        self.type = rel_type
        self.end_node = end
        self._props: dict = {}

    def get(self, key, default=None):
        return self._props.get(key, default)


class StubPath:
    def __init__(self, relationships: list[StubRelationship]) -> None:
        self.relationships = relationships
        seen: list[StubNode] = []
        for rel in relationships:
            for node in (rel.start_node, rel.end_node):
                if node not in seen:
                    seen.append(node)
        self.nodes = seen


def stub_record(path: StubPath) -> dict:
    return {"path": path}


def iphone_records() -> list[dict]:
    """Two models, two shared components: the Phase 1 reference result."""
    product = StubNode("Product", "iPhone 16")
    battery = StubNode("Component", "Built-in rechargeable lithium-ion battery")
    cable = StubNode("Component", "USB-C Charge Cable (1m)")
    records = []
    for model_name in ("Model A3287", "Model A3290"):
        model = StubNode("Model", model_name)
        for component in (battery, cable):
            records.append(
                stub_record(
                    StubPath(
                        [
                            StubRelationship(product, "HAS_MODEL", model),
                            StubRelationship(model, "CONTAINS", component),
                        ]
                    )
                )
            )
    return records


class SchemaTests(unittest.TestCase):
    def test_every_path_root_matches_first_hop(self):
        for name, spec in LINEAGE_PATHS.items():
            first = spec.hops[0]
            start, end = LINEAGE_SCHEMA[first.relationship]
            expected = start if first.outgoing else end
            self.assertEqual(
                spec.root_label, expected, f"{name} root disagrees with its first hop"
            )

    def test_hops_chain_through_declared_labels(self):
        for name, spec in LINEAGE_PATHS.items():
            current = spec.root_label
            for hop in spec.hops:
                start, end = LINEAGE_SCHEMA[hop.relationship]
                tail, head = (start, end) if hop.outgoing else (end, start)
                self.assertEqual(tail, current, f"{name} has a broken hop chain")
                current = head

    def test_labels_are_known(self):
        for spec in LINEAGE_PATHS.values():
            self.assertIn(spec.root_label, KNOWN_LABELS)

    def test_applicant_hop_is_confined_to_the_ul_path(self):
        """Blocks Product -> Company -> UL -> Company -> Product."""
        for name, spec in LINEAGE_PATHS.items():
            if name == "ul_applicants":
                continue
            self.assertNotIn(
                "PARTY_ROLE_APPLICANT",
                [hop.relationship for hop in spec.hops],
                f"{name} can reach the UL hub",
            )


class ValidationTests(unittest.TestCase):
    def test_accepts_a_valid_plan(self):
        spec = validate_lineage_plan(
            LineagePlan("iPhone 16", "Product", "components")
        )
        self.assertEqual(spec.root_label, "Product")

    def test_rejects_unknown_path(self):
        with self.assertRaises(LineagePlanError):
            validate_lineage_plan(
                LineagePlan("iPhone 16", "Product", "delete_everything")
            )

    def test_rejects_unknown_label(self):
        with self.assertRaises(LineagePlanError):
            validate_lineage_plan(LineagePlan("iPhone 16", "Sandwich", "components"))

    def test_rejects_label_path_mismatch(self):
        with self.assertRaises(LineagePlanError):
            validate_lineage_plan(LineagePlan("Apple", "Company", "components"))

    def test_rejects_empty_entity_name(self):
        with self.assertRaises(LineagePlanError):
            validate_lineage_plan(LineagePlan("   ", "Product", "components"))

    def test_rejects_out_of_range_limit(self):
        with self.assertRaises(LineagePlanError):
            validate_lineage_plan(
                LineagePlan("iPhone 16", "Product", "components", limit=0)
            )


class BuilderTests(unittest.TestCase):
    def test_components_query_is_directed_and_parameterized(self):
        cypher, parameters = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "components")
        )
        self.assertEqual(
            path_pattern(cypher),
            "(root)-[:HAS_MODEL]->(n0:Model)-[:CONTAINS]->(n1:Component)",
        )
        self.assertEqual(parameters["entity_name"], "iPhone 16")
        self.assertNotIn("iPhone 16", cypher)

    def test_incoming_hop_reverses_the_arrow(self):
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "owner_company")
        )
        self.assertIn("(root)<-[:HAS_PRODUCT]-(n0:Company)", cypher)

    def test_no_undirected_hops_in_any_path(self):
        """Every hop must be `-[:X]->` or `<-[:X]-`; a bare `-[:X]-` would
        reopen the cross-company traversal the planner exists to prevent."""
        for name, spec in LINEAGE_PATHS.items():
            cypher, _ = build_lineage_cypher(LineagePlan("x", spec.root_label, name))
            pattern = path_pattern(cypher)
            directed = re.findall(r"<-\[:\w+\]-(?!>)|-\[:\w+\]->", pattern)
            self.assertEqual(
                len(directed),
                pattern.count("[:"),
                f"{name} contains an undirected hop: {pattern}",
            )

    def test_query_is_read_only(self):
        for name, spec in LINEAGE_PATHS.items():
            cypher, _ = build_lineage_cypher(
                LineagePlan("x", spec.root_label, name)
            )
            upper = cypher.upper()
            for keyword in ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP"):
                self.assertNotIn(keyword, upper, f"{name} is not read-only")

    def test_exact_match_is_ranked_before_fuzzy(self):
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "components")
        )
        self.assertIn("toLower(root.name) = toLower($entity_name)", cypher)
        self.assertIn("toLower(root.name) CONTAINS toLower($entity_name)", cypher)
        # Ranking happens after MATCH path so dead-end exact nodes fall through.
        self.assertLess(cypher.index("MATCH path"), cypher.index("AS rank"))

    def test_terminal_filter_is_parameterized(self):
        cypher, parameters = build_lineage_cypher(
            LineagePlan(
                "iPhone 16", "Product", "components", terminal_contains="battery"
            )
        )
        self.assertIn("toLower(n1.name) CONTAINS toLower($terminal_contains)", cypher)
        self.assertEqual(parameters["terminal_contains"], "battery")
        self.assertNotIn("battery", cypher)

    def test_invalid_plan_never_reaches_the_builder(self):
        with self.assertRaises(LineagePlanError):
            build_lineage_cypher(LineagePlan("x", "Product", "nope"))


class CypherFormattingTests(unittest.TestCase):
    """Readable in Neo4j Browser, semantically unchanged."""

    def test_clauses_are_separated_into_blocks(self):
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "components")
        )
        self.assertIn("\n\n", cypher)
        for clause in ("MATCH (root:", "WHERE ", "MATCH path =", "WITH path,", "UNWIND ", "RETURN "):
            self.assertIn(clause, cypher)

    def test_multi_hop_pattern_is_indented_one_hop_per_line(self):
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "standards")
        )
        block = cypher.split("MATCH path =", 1)[1].split("\n\n", 1)[0]
        hop_lines = [line for line in block.splitlines() if line.strip()]
        self.assertEqual(len(hop_lines), 3)
        for line in hop_lines:
            self.assertTrue(line.startswith("    "), f"hop line not indented: {line!r}")

    def test_case_expression_is_expanded(self):
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "components")
        )
        self.assertIn("     CASE\n", cypher)
        self.assertIn("         WHEN toLower(root.name) = toLower($entity_name) THEN 0", cypher)
        self.assertIn("         ELSE 1", cypher)
        self.assertIn("     END AS rank", cypher)

    def test_formatting_preserves_semantics(self):
        """Whitespace-stripped output equals the compact single-line form."""
        cypher, _ = build_lineage_cypher(
            LineagePlan("iPhone 16", "Product", "components")
        )
        collapsed = re.sub(r"\s+", " ", cypher).strip()
        self.assertEqual(
            collapsed,
            "MATCH (root:Product) "
            "WHERE toLower(root.name) = toLower($entity_name) "
            "OR toLower(root.name) CONTAINS toLower($entity_name) "
            "MATCH path = (root)-[:HAS_MODEL]->(n0:Model) -[:CONTAINS]->(n1:Component) "
            "WITH path, CASE "
            "WHEN toLower(root.name) = toLower($entity_name) THEN 0 ELSE 1 END AS rank "
            "WITH min(rank) AS best_rank, "
            "collect({ path: path, rank: rank }) AS candidates "
            "UNWIND [candidate IN candidates WHERE candidate.rank = best_rank] AS candidate "
            "RETURN candidate.path AS path LIMIT $limit",
        )

    def test_entity_name_is_never_interpolated_in_any_path(self):
        for name, spec in LINEAGE_PATHS.items():
            cypher, parameters = build_lineage_cypher(
                LineagePlan("Sentinel Entity Name", spec.root_label, name)
            )
            self.assertNotIn("Sentinel", cypher, f"{name} inlined the entity name")
            self.assertEqual(parameters["entity_name"], "Sentinel Entity Name")
            self.assertIn("$entity_name", cypher)


class EvidenceConversionTests(unittest.TestCase):
    def test_reference_result_shape(self):
        evidence = paths_to_graph_evidence(iphone_records())
        # 1 product + 2 models + 2 shared components; 2 HAS_MODEL + 4 CONTAINS.
        self.assertEqual(len(evidence["nodes"]), 5)
        self.assertEqual(len(evidence["relationships"]), 6)

    def test_nodes_are_deduplicated_across_paths(self):
        evidence = paths_to_graph_evidence(iphone_records())
        names = [node["name"] for node in evidence["nodes"]]
        self.assertEqual(len(names), len(set(names)))

    def test_payload_keys_match_the_frontend_contract(self):
        evidence = paths_to_graph_evidence(iphone_records())
        self.assertEqual(
            set(evidence["nodes"][0]), {"id", "name", "type", "description"}
        )
        self.assertEqual(
            set(evidence["relationships"][0]),
            {
                "source",
                "source_name",
                "source_type",
                "relationship",
                "target",
                "target_name",
                "target_type",
                "description",
                "keywords",
            },
        )

    def test_adds_nothing_beyond_the_returned_paths(self):
        evidence = paths_to_graph_evidence(iphone_records())
        self.assertEqual(
            {node["name"] for node in evidence["nodes"]},
            {
                "iPhone 16",
                "Model A3287",
                "Model A3290",
                "Built-in rechargeable lithium-ion battery",
                "USB-C Charge Cable (1m)",
            },
        )

    def test_empty_records_produce_empty_evidence(self):
        self.assertEqual(
            paths_to_graph_evidence([]), {"nodes": [], "relationships": []}
        )


class GraphPipelineTests(unittest.TestCase):
    def test_hardcoded_plan_is_the_verification_plan(self):
        plan = chatbot_mod._hardcoded_plan("anything at all")
        self.assertEqual(plan.entity_name, "iPhone 16")
        self.assertEqual(plan.entity_label, "Product")
        self.assertEqual(plan.path, "components")

    def test_returns_empty_result_without_neo4j_password(self):
        with patch("config.NEO4J_PASSWORD", ""):
            result = chatbot_mod._run_graph_pipeline("any question")
        self.assertEqual(result, chatbot_mod._empty_graph_result())

    def test_returns_empty_result_for_blank_question(self):
        result = chatbot_mod._run_graph_pipeline("   ")
        self.assertEqual(result["graph_evidence"]["nodes"], [])
        self.assertEqual(result["generated_cypher"], "")

    def test_empty_result_is_not_shared_between_calls(self):
        first = chatbot_mod._empty_graph_result()
        first["graph_evidence"]["nodes"].append({"id": "x"})
        self.assertEqual(chatbot_mod._empty_graph_result()["graph_evidence"]["nodes"], [])

    def test_response_model_exposes_the_generated_query(self):
        fields = chatbot_mod.ChatResponse.model_fields
        self.assertIn("generated_cypher", fields)
        self.assertIn("query_parameters", fields)


class PreservedLineageApiTests(unittest.TestCase):
    """Phase 1 is additive: the old lineage helpers must still exist."""

    def test_existing_functions_are_preserved(self):
        for name in (
            "_retrieve_neo4j_lineage",
            "_lineage_for_question",
            "_narrow_lineage_for_display",
            "_intent_focus_types",
            "_neo4j_subgraph_to_graph_items",
            "_build_graph_evidence",
        ):
            self.assertTrue(hasattr(chatbot_mod, name), f"{name} was removed")


if __name__ == "__main__":
    unittest.main()
