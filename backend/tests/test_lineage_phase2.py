"""Tests for the Phase 2 LLM lineage planner.

The planner selects an approved traversal; it never writes Cypher. These tests
stub the LLM so they run offline, and assert both that valid plans flow through
to a built query and that off-schema or malformed output is rejected before it
can reach the builder.
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

import ul.chatbot as chatbot_mod
from ul.knowledge_graph.queries import (
    KNOWN_LABELS,
    LINEAGE_PATHS,
    LineagePlan,
    LineagePlanError,
    _approved_labels_text,
    _approved_paths_text,
    _parse_plan_json,
    build_lineage_cypher,
    plan_lineage_query,
)

PLANNER = "ul.llm_client.chat_json"


def llm(payload: str):
    """Patch the shared chat client to return a canned planner response."""
    return patch(PLANNER, return_value=payload)


class PromptTests(unittest.TestCase):
    def test_prompt_lists_every_approved_path(self):
        text = _approved_paths_text()
        for name, spec in LINEAGE_PATHS.items():
            self.assertIn(f'"{name}"', text)
            self.assertIn(spec.root_label, text)

    def test_prompt_lists_every_approved_label(self):
        text = _approved_labels_text()
        for label in KNOWN_LABELS:
            self.assertIn(label, text)

    def test_prompt_is_derived_from_code_not_hardcoded(self):
        """Adding a path must surface in the prompt without editing it."""
        spec = LINEAGE_PATHS["components"]
        with patch.dict(
            LINEAGE_PATHS, {"unit_test_only_path": spec}, clear=False
        ):
            self.assertIn('"unit_test_only_path"', _approved_paths_text())


class PlannerSuccessTests(unittest.TestCase):
    def test_components_plan(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "components"}'):
            plan = plan_lineage_query("What parts are inside iPhone 16?")
        self.assertEqual(plan.entity_name, "iPhone 16")
        self.assertEqual(plan.entity_label, "Product")
        self.assertEqual(plan.path, "components")

    def test_standards_plan_builds_three_hop_query(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "standards"}'):
            plan = plan_lineage_query("Which standards apply to iPhone 16?")
        cypher, parameters = build_lineage_cypher(plan)
        collapsed = re.sub(r"\s+", "", cypher)
        self.assertIn(
            "(root)-[:HAS_MODEL]->(n0:Model)-[:CONTAINS]->(n1:Component)"
            "-[:COMPLIES_WITH]->(n2:Standard)",
            collapsed,
        )
        self.assertEqual(parameters["entity_name"], "iPhone 16")

    def test_certifications_plan_builds_expected_query(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "certifications"}'):
            plan = plan_lineage_query("What certifications does iPhone 16 hold?")
        cypher, _ = build_lineage_cypher(plan)
        self.assertIn("-[:HAS_CERTIFICATION]->(n2:Certification)", cypher)

    def test_incoming_hop_plan_keeps_direction(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "owner_company"}'):
            plan = plan_lineage_query("Which company makes iPhone 16?")
        cypher, _ = build_lineage_cypher(plan)
        self.assertIn("(root)<-[:HAS_PRODUCT]-(n0:Company)", cypher)

    def test_entity_name_is_a_parameter_not_inlined(self):
        with llm('{"entity_name": "Galaxy A56R 5G", "entity_label": "Product", "path": "components"}'):
            plan = plan_lineage_query("What is in the Galaxy A56R 5G?")
        cypher, parameters = build_lineage_cypher(plan)
        self.assertNotIn("Galaxy", cypher)
        self.assertEqual(parameters["entity_name"], "Galaxy A56R 5G")

    def test_whitespace_is_stripped(self):
        with llm('{"entity_name": "  iPhone 16  ", "entity_label": " Product ", "path": " components "}'):
            plan = plan_lineage_query("parts of iPhone 16")
        self.assertEqual(plan.entity_name, "iPhone 16")
        self.assertEqual(plan.entity_label, "Product")
        self.assertEqual(plan.path, "components")

    def test_accepts_fenced_json(self):
        with llm('```json\n{"entity_name": "iPhone 16", "entity_label": "Product", "path": "components"}\n```'):
            plan = plan_lineage_query("parts of iPhone 16")
        self.assertEqual(plan.path, "components")

    def test_accepts_surrounding_chatter(self):
        with llm('Sure! Here is the plan:\n{"entity_name": "iPhone 16", "entity_label": "Product", "path": "models"}\nHope that helps.'):
            plan = plan_lineage_query("which models are there")
        self.assertEqual(plan.path, "models")

    def test_planner_receives_the_question(self):
        with patch(
            PLANNER,
            return_value='{"entity_name": "iPhone 16", "entity_label": "Product", "path": "components"}',
        ) as mock_llm:
            plan_lineage_query("Which components are in iPhone 16?")
        user_prompt = mock_llm.call_args.args[1]
        self.assertIn("Which components are in iPhone 16?", user_prompt)
        self.assertIn('"components"', user_prompt)


class PlannerRejectionTests(unittest.TestCase):
    def test_rejects_unknown_path(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "drop_database"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_unknown_label(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Sandwich", "path": "components"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_label_path_mismatch(self):
        with llm('{"entity_name": "Apple", "entity_label": "Company", "path": "components"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_malformed_json(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": '):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_non_object_json(self):
        with llm('["iPhone 16", "Product", "components"]'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_empty_response(self):
        with llm(""):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_missing_fields(self):
        with llm('{"entity_name": "iPhone 16"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_empty_entity_name(self):
        with llm('{"entity_name": "", "entity_label": "Product", "path": "components"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_raw_cypher_in_response(self):
        with llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "MATCH (n) DETACH DELETE n"}'):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")

    def test_rejects_blank_question_without_calling_llm(self):
        with patch(PLANNER) as mock_llm:
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("   ")
        mock_llm.assert_not_called()

    def test_wraps_llm_transport_errors(self):
        with patch(PLANNER, side_effect=RuntimeError("azure exploded")):
            with self.assertRaises(LineagePlanError):
                plan_lineage_query("anything")


class ParseHelperTests(unittest.TestCase):
    def test_parses_plain_object(self):
        self.assertEqual(_parse_plan_json('{"a": 1}'), {"a": 1})

    def test_raises_on_garbage(self):
        with self.assertRaises(LineagePlanError):
            _parse_plan_json("not json at all")

    def test_raises_on_none(self):
        with self.assertRaises(LineagePlanError):
            _parse_plan_json(None)


class PipelineIntegrationTests(unittest.TestCase):
    """Planner failures must degrade to an empty graph, never break the answer."""

    def test_pipeline_returns_empty_result_when_planner_rejects(self):
        with (
            patch.object(chatbot_mod, "_LINEAGE_PLANNER_ENABLED", True),
            llm('{"entity_name": "x", "entity_label": "Nope", "path": "components"}'),
        ):
            result = chatbot_mod._run_graph_pipeline("anything")
        self.assertEqual(result, chatbot_mod._empty_graph_result())

    def test_pipeline_returns_empty_result_on_malformed_json(self):
        with (
            patch.object(chatbot_mod, "_LINEAGE_PLANNER_ENABLED", True),
            llm("{{{"),
        ):
            result = chatbot_mod._run_graph_pipeline("anything")
        self.assertEqual(result["graph_evidence"], {"nodes": [], "relationships": []})
        self.assertEqual(result["generated_cypher"], "")

    def test_valid_plan_reaches_the_builder(self):
        """Plan -> Cypher without touching Neo4j."""
        captured: dict = {}

        def fake_execute(driver, cypher, parameters):
            captured["cypher"] = cypher
            captured["parameters"] = parameters
            return []

        with (
            patch.object(chatbot_mod, "_LINEAGE_PLANNER_ENABLED", True),
            llm('{"entity_name": "iPhone 16", "entity_label": "Product", "path": "standards"}'),
            patch("ul.neo4j_service._get_driver"),
            patch(
                "ul.knowledge_graph.queries.execute_lineage_cypher",
                side_effect=fake_execute,
            ),
        ):
            result = chatbot_mod._run_graph_pipeline("Which standards apply to iPhone 16?")

        self.assertIn("-[:COMPLIES_WITH]->(n2:Standard)", captured["cypher"])
        self.assertEqual(captured["parameters"]["entity_name"], "iPhone 16")
        self.assertEqual(result["generated_cypher"], captured["cypher"])

    def test_hardcoded_plan_still_used_when_flag_is_off(self):
        with (
            patch.object(chatbot_mod, "_LINEAGE_PLANNER_ENABLED", False),
            patch(PLANNER) as mock_llm,
            patch("config.NEO4J_PASSWORD", ""),
        ):
            chatbot_mod._run_graph_pipeline("anything")
        mock_llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
