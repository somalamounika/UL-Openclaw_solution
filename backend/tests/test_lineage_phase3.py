"""Phase 3: prove graph_evidence is exactly the Neo4j query result.

These tests run the production pipeline, take the ``generated_cypher`` it
returned, execute that query again independently, and convert it with the
*same* ``paths_to_graph_evidence`` production uses. The two graphs must be
structurally identical -- full node and relationship dicts, not counts.

They require a live Neo4j; the whole module skips when one is not reachable.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import config
import ul.chatbot as chatbot_mod
from ul.knowledge_graph.queries import (
    LineagePlan,
    build_lineage_cypher,
    execute_lineage_cypher,
    paths_to_graph_evidence,
)

# entity, label, path -- one per lineage type exercised (Task 7).
LINEAGE_CASES = [
    ("components", "iPhone 16", "Product"),
    ("models", "iPhone 16", "Product"),
    ("standards", "iPhone 16", "Product"),
    ("owner_company", "iPhone 16", "Product"),
    ("certifications", "iPhone 16", "Product"),
]


def neo4j_available() -> bool:
    if not config.NEO4J_PASSWORD:
        return False
    try:
        from ul.neo4j_service import _get_driver

        driver = _get_driver()
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True
    except Exception:
        return False


def sorted_nodes(evidence: dict) -> list[dict]:
    return sorted(evidence["nodes"], key=lambda n: n["id"])


def sorted_relationships(evidence: dict) -> list[dict]:
    return sorted(
        evidence["relationships"],
        key=lambda r: (r["source"], r["relationship"], r["target"]),
    )


def run_pipeline(plan: LineagePlan) -> dict:
    """Run the production graph pipeline with the planner pinned to ``plan``."""
    with patch(
        "ul.knowledge_graph.queries.plan_lineage_query", return_value=plan
    ):
        return chatbot_mod._run_graph_pipeline("does not matter, planner is pinned")


def rerun_generated_cypher(result: dict) -> dict:
    """Execute the returned Cypher independently, as Neo4j Browser would."""
    from ul.neo4j_service import _get_driver

    driver = _get_driver()
    try:
        records = execute_lineage_cypher(
            driver, result["generated_cypher"], result["query_parameters"]
        )
    finally:
        driver.close()
    return paths_to_graph_evidence(records)


@unittest.skipUnless(neo4j_available(), "Neo4j is not reachable")
class GraphEvidenceEqualsNeo4jResultTests(unittest.TestCase):
    """generated_cypher re-executed == graph_evidence, for every lineage type."""

    def _assert_exact_equality(self, path_name: str, entity: str, label: str) -> None:
        plan = LineagePlan(entity_name=entity, entity_label=label, path=path_name)
        result = run_pipeline(plan)
        self.assertTrue(
            result["generated_cypher"],
            f"{path_name}: pipeline returned no generated_cypher",
        )

        produced = result["graph_evidence"]
        independent = rerun_generated_cypher(result)

        produced_nodes = sorted_nodes(produced)
        independent_nodes = sorted_nodes(independent)
        produced_rels = sorted_relationships(produced)
        independent_rels = sorted_relationships(independent)

        # Whole-structure equality: ids, names, types and descriptions.
        self.assertEqual(
            produced_nodes,
            independent_nodes,
            f"{path_name}: node sets differ",
        )
        self.assertEqual(
            produced_rels,
            independent_rels,
            f"{path_name}: relationship sets differ",
        )

        # Field-level assertions, so a future dict-shape change cannot mask drift.
        for produced_node, independent_node in zip(produced_nodes, independent_nodes):
            self.assertEqual(produced_node["id"], independent_node["id"])
            self.assertEqual(produced_node["name"], independent_node["name"])
            self.assertEqual(produced_node["type"], independent_node["type"])
            self.assertEqual(
                produced_node["description"], independent_node["description"]
            )
        for produced_rel, independent_rel in zip(produced_rels, independent_rels):
            self.assertEqual(produced_rel["source"], independent_rel["source"])
            self.assertEqual(produced_rel["target"], independent_rel["target"])
            self.assertEqual(
                produced_rel["relationship"], independent_rel["relationship"]
            )
            self.assertEqual(
                produced_rel["source_type"], independent_rel["source_type"]
            )
            self.assertEqual(
                produced_rel["target_type"], independent_rel["target_type"]
            )
            self.assertEqual(
                produced_rel["description"], independent_rel["description"]
            )

    def test_components(self):
        self._assert_exact_equality(*LINEAGE_CASES[0])

    def test_models(self):
        self._assert_exact_equality(*LINEAGE_CASES[1])

    def test_standards(self):
        self._assert_exact_equality(*LINEAGE_CASES[2])

    def test_owner_company(self):
        self._assert_exact_equality(*LINEAGE_CASES[3])

    def test_certifications(self):
        self._assert_exact_equality(*LINEAGE_CASES[4])

    def test_every_case_uses_the_same_conversion_function(self):
        """The test must not build its own converter."""
        import tests.test_lineage_phase3 as module

        self.assertIs(module.paths_to_graph_evidence, paths_to_graph_evidence)


@unittest.skipUnless(neo4j_available(), "Neo4j is not reachable")
class ReferenceGraphTests(unittest.TestCase):
    """The documented iPhone 16 components graph, asserted exactly."""

    EXPECTED_NODES = {
        ("Product:iPhone 16", "iPhone 16", "Product"),
        ("Model:Model A3287", "Model A3287", "Model"),
        ("Model:Model A3290", "Model A3290", "Model"),
        (
            "Component:Built-in rechargeable lithium-ion battery",
            "Built-in rechargeable lithium-ion battery",
            "Component",
        ),
        (
            "Component:USB-C Charge Cable (1m)",
            "USB-C Charge Cable (1m)",
            "Component",
        ),
    }

    EXPECTED_RELATIONSHIPS = {
        ("Product:iPhone 16", "HAS_MODEL", "Model:Model A3287"),
        ("Product:iPhone 16", "HAS_MODEL", "Model:Model A3290"),
        (
            "Model:Model A3287",
            "CONTAINS",
            "Component:Built-in rechargeable lithium-ion battery",
        ),
        ("Model:Model A3287", "CONTAINS", "Component:USB-C Charge Cable (1m)"),
        (
            "Model:Model A3290",
            "CONTAINS",
            "Component:Built-in rechargeable lithium-ion battery",
        ),
        ("Model:Model A3290", "CONTAINS", "Component:USB-C Charge Cable (1m)"),
    }

    def setUp(self):
        plan = LineagePlan("iPhone 16", "Product", "components")
        self.result = run_pipeline(plan)
        self.evidence = self.result["graph_evidence"]

    def test_node_count_and_identity(self):
        self.assertEqual(len(self.evidence["nodes"]), 5)
        self.assertEqual(
            {(n["id"], n["name"], n["type"]) for n in self.evidence["nodes"]},
            self.EXPECTED_NODES,
        )

    def test_relationship_count_and_identity(self):
        self.assertEqual(len(self.evidence["relationships"]), 6)
        self.assertEqual(
            {
                (r["source"], r["relationship"], r["target"])
                for r in self.evidence["relationships"]
            },
            self.EXPECTED_RELATIONSHIPS,
        )

    def test_relationship_type_breakdown(self):
        types = [r["relationship"] for r in self.evidence["relationships"]]
        self.assertEqual(types.count("HAS_MODEL"), 2)
        self.assertEqual(types.count("CONTAINS"), 4)

    def test_no_unrelated_company_or_product_leaked(self):
        names = " ".join(n["name"] for n in self.evidence["nodes"])
        for forbidden in ("Samsung", "Galaxy", "Sony", "UL Solutions", "Apple"):
            self.assertNotIn(forbidden, names)

    def test_generated_cypher_is_returned_and_parameterized(self):
        self.assertIn("$entity_name", self.result["generated_cypher"])
        self.assertNotIn("iPhone 16", self.result["generated_cypher"])
        self.assertEqual(
            self.result["query_parameters"],
            {"entity_name": "iPhone 16", "limit": 120},
        )


@unittest.skipUnless(neo4j_available(), "Neo4j is not reachable")
class ParallelBranchTests(unittest.TestCase):
    """The answer branch must not wait on the graph branch."""

    def test_branches_are_submitted_before_either_is_awaited(self):
        import inspect

        source = inspect.getsource(chatbot_mod.answer_question)
        submit_lightrag = source.index("pool.submit(_load_lightrag)")
        submit_graph = source.index("pool.submit(_load_graph)")
        first_result = source.index(".result()")
        self.assertLess(submit_lightrag, first_result)
        self.assertLess(
            submit_graph,
            first_result,
            "graph branch must be submitted before any .result() call",
        )

    def test_graph_branch_failure_does_not_break_the_answer(self):
        with patch.object(
            chatbot_mod,
            "_run_graph_pipeline",
            side_effect=RuntimeError("neo4j down"),
        ):
            with patch(
                "ul.chatbot._retrieve_lightrag_context", return_value=([], [])
            ):
                result = chatbot_mod.answer_question("anything")
        self.assertEqual(result["graph_evidence"], {"nodes": [], "relationships": []})
        self.assertEqual(result["generated_cypher"], "")
        self.assertIn("answer", result)


if __name__ == "__main__":
    unittest.main()
