"""Tests for the generic chatbot final-answer formatter (presentation layer only)."""

from __future__ import annotations

import unittest

from ul.chatbot import (
    _deterministic_format_answer,
    _sanitize_answer_markdown,
    format_final_answer,
)


class AnswerFormatterTests(unittest.TestCase):
    def test_simple_entity_question(self) -> None:
        draft = (
            "Product X is a finished product manufactured by Company A. "
            "Unrelated Component Z also appears in the graph."
        )
        context = (
            "GRAPH CONTEXT:\n"
            "[G1] Product: Product X | Description: Finished product.\n"
            "[G2] Company: Company A\n"
            "[G3] Company A -[PRODUCES]-> Product X\n"
            "[G4] Component: Component Z\n"
        )
        answer = format_final_answer(
            "What is Product X?",
            draft,
            context,
        )
        self.assertIn("Product X", answer)
        self.assertNotIn("[G1]", answer)
        self.assertNotIn("retrieved context", answer.casefold())

    def test_relationship_question(self) -> None:
        draft = "Entity A | PRODUCES | Entity B [G2]"
        answer = _deterministic_format_answer(
            "What is the relationship between Entity A and Entity B?",
            draft,
        )
        self.assertIn("Entity A", answer)
        self.assertIn("Entity B", answer)
        self.assertIn("produces", answer.casefold())
        self.assertNotIn("|", answer)
        self.assertNotIn("[G2]", answer)

    def test_list_question(self) -> None:
        draft = (
            "Model M1 contains: Component Alpha, Component Beta, Component Gamma, "
            "and Component Alpha."
        )
        answer = _deterministic_format_answer(
            "What components does Model M1 contain?",
            draft,
        )
        self.assertIn("- Component Alpha", answer)
        self.assertIn("- Component Beta", answer)
        self.assertIn("- Component Gamma", answer)
        self.assertEqual(answer.count("Component Alpha"), 1)

    def test_comparison_question(self) -> None:
        draft = (
            "| Field | Product X | Product Y |\n"
            "| --- | --- | --- |\n"
            "| Standard | Standard S1 | Standard S2 |\n"
            "| Certification | Cert C1 | The available information does not specify |\n"
        )
        answer = format_final_answer(
            "Compare Product X and Product Y",
            draft,
            "GRAPH CONTEXT:\n[G1] Product X -[SUBJECT_TO]-> Standard S1\n"
            "[G2] Product Y -[SUBJECT_TO]-> Standard S2\n"
            "[G3] Product X -[REQUIRES_CERTIFICATION]-> Cert C1\n",
        )
        self.assertIn("|", answer)
        self.assertIn("Product X", answer)
        self.assertIn("Product Y", answer)
        self.assertIn("does not specify", answer.casefold())
        self.assertNotIn("[G1]", answer)

    def test_duplicate_retrieved_evidence(self) -> None:
        draft = (
            "- Component Y is used by Model M1 [D1]\n"
            "- Component Y is used by Model M1 [D2]\n"
            "- Component Y is used by Model M1 [G3]\n"
        )
        answer = _sanitize_answer_markdown(draft)
        self.assertEqual(answer.count("Component Y is used by Model M1"), 1)
        self.assertNotIn("[D1]", answer)
        self.assertNotIn("[G3]", answer)

    def test_irrelevant_retrieved_information(self) -> None:
        # Deterministic formatting does not invent a rewrite; it keeps grounded draft text
        # and only strips retrieval artifacts / unasked product lines.
        draft = "Standard S1 applies to Product X."
        answer = format_final_answer(
            "Which standards apply to Product X?",
            draft,
            "GRAPH CONTEXT:\n"
            "[G1] Product X -[SUBJECT_TO]-> Standard S1\n"
            "[G2] Product Q -[REQUIRES_TEST]-> Test T9\n",
        )
        self.assertIn("Standard S1", answer)
        self.assertNotIn("Test T9", answer)
        self.assertNotIn("[G2]", answer)

    def test_missing_information(self) -> None:
        draft = (
            "Product X is associated with Company A. "
            "The available information does not specify the required certification."
        )
        answer = format_final_answer(
            "What certification is required for Product X?",
            draft,
            "GRAPH CONTEXT:\n[G1] Company A -[PRODUCES]-> Product X\n",
        )
        self.assertIn("does not specify", answer.casefold())
        self.assertNotIn("vector search", answer.casefold())
        self.assertNotIn("retrieval system", answer.casefold())

    def test_internal_retrieval_ids_are_removed(self) -> None:
        draft = (
            "Component Y is linked to Standard S1 [G31] (D4). "
            "chunk_id=chunk_000012 score=0.91 graph_id=kg_abc123"
        )
        answer = _sanitize_answer_markdown(draft)
        self.assertNotIn("[G31]", answer)
        self.assertNotIn("(D4)", answer)
        self.assertNotIn("chunk_id", answer.casefold())
        self.assertNotIn("graph_id", answer.casefold())
        self.assertNotIn("score=", answer.casefold())
        self.assertIn("Component Y", answer)
        self.assertIn("Standard S1", answer)

    def test_large_number_of_results(self) -> None:
        items = [f"Component {chr(65 + i)}" for i in range(12)]
        draft = "Model M1 contains: " + ", ".join(items) + "."
        answer = _deterministic_format_answer(
            "List the components in Model M1",
            draft,
        )
        self.assertIn("- Component A", answer)
        self.assertIn("- Component L", answer)
        self.assertGreaterEqual(answer.count("\n- "), 10)
        # No duplicated component lines after formatting.
        self.assertEqual(answer.count("Component A"), 1)

    def test_graph_and_document_evidence(self) -> None:
        draft = (
            "Based on the retrieved context, Model M1 HAS_COMPONENT Component Y [G2]. "
            "Document evidence also states that Component Y is required for Model M1 [D1]."
        )
        context = (
            "GRAPH CONTEXT:\n"
            "[G2] Model: Model M1 -[HAS_COMPONENT]-> Component: Component Y\n"
            "DOCUMENT EVIDENCE:\n"
            "[D1] Source: manual.pdf; pages: 3-3; chunk: chunk_000003\n"
            "Component Y is required for Model M1 assembly."
        )
        answer = format_final_answer(
            "Why is Component Y associated with Model M1?",
            draft,
            context,
            sources=[
                {
                    "source_file": "manual.pdf",
                    "page_range": {"start": 3, "end": 3},
                    "chunk_id": "chunk_000003",
                    "document_id": "doc-1",
                }
            ],
        )
        self.assertIn("Model M1", answer)
        self.assertIn("Component Y", answer)
        self.assertNotIn("[G2]", answer)
        self.assertNotIn("[D1]", answer)
        self.assertNotIn("based on the retrieved context", answer.casefold())
        self.assertNotIn("chunk_000003", answer)

    def test_drops_unasked_foreign_product_lines(self) -> None:
        draft = (
            "Galaxy A56R uses IEC 62133-2.\n"
            "The iPhone 16 uses UL 62368-1.\n"
            "Battery pack is listed for Galaxy A56R."
        )
        answer = format_final_answer(
            "What standards apply to Galaxy A56R?",
            draft,
            "",
        )
        self.assertIn("Galaxy", answer)
        self.assertIn("62133", answer)
        self.assertNotIn("iPhone", answer)


if __name__ == "__main__":
    unittest.main()
