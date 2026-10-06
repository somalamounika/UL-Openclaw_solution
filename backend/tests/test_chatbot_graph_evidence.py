"""Tests for chatbot graph evidence derived from LightRAG mix retrieval."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from ul.chatbot import (
    _build_graph_evidence,
    _graph_items_from_lightrag,
    _retrieve_lightrag_context,
    _scope_document_id,
    answer_question,
)
import ul.chatbot as chatbot_mod


def graph_result(items: list[dict] | None = None) -> dict:
    """Stub for the planned-lineage branch of ``answer_question``.

    Mirrors ``_run_graph_pipeline``: graph evidence plus the query that
    produced it.
    """
    return {
        "graph_evidence": _build_graph_evidence(items or []),
        "generated_cypher": "MATCH path = (root:Product) RETURN path" if items else "",
        "query_parameters": {"entity_name": "Product X"} if items else {},
    }


def entity(entity_type: str, name: str, description: str = "") -> dict:
    return {
        "kind": "entity",
        "type": entity_type,
        "name": name,
        "description": description,
    }


def relationship(
    source_type: str,
    source_name: str,
    rel: str,
    target_type: str,
    target_name: str,
) -> dict:
    return {
        "kind": "relationship",
        "source": {"type": source_type, "name": source_name},
        "relationship": rel,
        "target": {"type": target_type, "name": target_name},
        "description": "Retrieved relationship evidence.",
        "keywords": rel,
    }


class ChatbotGraphEvidenceTests(unittest.TestCase):
    def test_retrieved_relationship_becomes_graph_evidence(self) -> None:
        context = [
            relationship("Entity", "Entity A", "RELATES_TO", "Entity", "Entity B")
        ]

        evidence = _build_graph_evidence(context)

        self.assertEqual([node["name"] for node in evidence["nodes"]], ["Entity A", "Entity B"])
        self.assertEqual(len(evidence["relationships"]), 1)
        self.assertEqual(evidence["relationships"][0]["relationship"], "RELATES_TO")

    def test_multiple_paths_deduplicate_nodes(self) -> None:
        context = [
            relationship("Product", "Product X", "HAS_MODEL", "Model", "Model M"),
            relationship("Model", "Model M", "HAS_COMPONENT", "Component", "Component Y"),
            entity("Model", "Model M", "A retrieved model."),
        ]

        evidence = _build_graph_evidence(context)

        self.assertEqual(len(evidence["nodes"]), 3)
        model = next(node for node in evidence["nodes"] if node["name"] == "Model M")
        self.assertEqual(model["description"], "A retrieved model.")

    def test_duplicate_relationships_are_removed(self) -> None:
        edge = relationship(
            "Product", "Product X", "HAS_COMPONENT", "Component", "Component Y"
        )

        evidence = _build_graph_evidence([edge, dict(edge), edge])

        self.assertEqual(len(evidence["relationships"]), 1)
        self.assertEqual(len(evidence["nodes"]), 2)

    def test_no_graph_context_returns_empty_evidence(self) -> None:
        self.assertEqual(
            _build_graph_evidence([]),
            {"nodes": [], "relationships": []},
        )

    def test_hybrid_answer_keeps_graph_and_document_evidence_separate(self) -> None:
        graph_context = [
            relationship("Product", "Product X", "SUBJECT_TO", "Standard", "Standard S")
        ]
        document_context = [
            {
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "source_file": "document.pdf",
                "page_range": {"start": 4, "end": 4},
                "text": "A supporting document statement.",
            }
        ]
        with (
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=(graph_context, document_context),
            ),
            patch("ul.chatbot._run_graph_pipeline", return_value=graph_result()),
            patch("ul.chatbot._generate_answer", return_value="Grounded draft."),
            patch("ul.chatbot.format_final_answer", return_value="Standard S applies to Product X."),
        ):
            result = answer_question("Which standard applies to Product X?")

        self.assertEqual(result["answer"], "Standard S applies to Product X.")
        self.assertEqual(len(result["sources"]), 1)
        # No Neo4j lineage → empty View Lineage; LightRAG still answers from docs/graph.
        self.assertEqual(result["graph_evidence"], {"nodes": [], "relationships": []})
        self.assertEqual(result["graph_context"], graph_context)

    def test_graph_evidence_contains_only_retrieved_graph_items(self) -> None:
        context = [
            entity("Entity", "Entity A"),
            relationship("Entity", "Entity A", "CONNECTS_TO", "Entity", "Entity B"),
        ]

        evidence = _build_graph_evidence(context)

        names = {node["name"] for node in evidence["nodes"]}
        self.assertEqual(names, {"Entity A", "Entity B"})
        self.assertNotIn("Entity C", names)

    def test_chatbot_document_retrieval_does_not_call_azure_search(self) -> None:
        with (
            patch("ul.document_chunking.search_relevant_chunks") as mock_search,
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=([], []),
            ),
            patch("ul.chatbot._run_graph_pipeline", return_value=graph_result()),
        ):
            result = answer_question("What is Product X?", document_id="doc-1")
            mock_search.assert_not_called()
        self.assertEqual(result["sources"], [])
        self.assertNotIn("reasoning", result)

    def test_answer_question_uses_graph_context_without_azure_search(self) -> None:
        graph_context = [
            relationship("Entity", "Entity A", "CONNECTS_TO", "Entity", "Entity B")
        ]
        with (
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=(graph_context, []),
            ),
            patch("ul.chatbot._run_graph_pipeline", return_value=graph_result()),
            patch("ul.document_chunking.search_relevant_chunks") as mock_search,
            patch("ul.chatbot._generate_answer", return_value="Draft."),
            patch("ul.chatbot.format_final_answer", return_value="Entity A connects to Entity B."),
        ):
            result = answer_question("How is Entity A connected?")

        mock_search.assert_not_called()
        self.assertEqual(result["answer"], "Entity A connects to Entity B.")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["graph_context"], graph_context)
        self.assertEqual(result["graph_evidence"], {"nodes": [], "relationships": []})

    def test_formatter_cannot_invent_visualized_nodes_or_edges(self) -> None:
        graph_context = [
            relationship("Entity", "Entity A", "CONNECTS_TO", "Entity", "Entity B")
        ]
        with (
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=(graph_context, []),
            ),
            patch("ul.chatbot._run_graph_pipeline", return_value=graph_result()),
            patch("ul.chatbot._generate_answer", return_value="Draft."),
            patch(
                "ul.chatbot.format_final_answer",
                return_value="Entity A also connects to invented Entity C.",
            ),
        ):
            result = answer_question("How is Entity A connected?")

        names = {node["name"] for node in result["graph_evidence"]["nodes"]}
        self.assertEqual(names, set())
        self.assertEqual(result["graph_evidence"]["relationships"], [])
        # Empty Neo4j → View Lineage empty; answer still uses LightRAG context.
        self.assertEqual(result["graph_context"], graph_context)

    def test_chatbot_uses_lightrag_mix_with_neo4j_lineage(self) -> None:
        source = inspect.getsource(chatbot_mod)
        self.assertIn('mode="mix"', source)
        self.assertIn("_retrieve_neo4j_lineage", source)
        self.assertNotIn("_retrieve_neo4j_context", source)
        self.assertIn("_scope_document_id", source)
        self.assertNotIn('mode="naive"', source)
        self.assertNotIn('mode="hybrid"', inspect.getsource(chatbot_mod._retrieve_lightrag_context))
        self.assertIsNone(_scope_document_id("ul_global", None))
        self.assertIsNone(_scope_document_id("kg_abc123", None))
        self.assertEqual(_scope_document_id(None, "doc_a"), "doc_a")
        self.assertEqual(_scope_document_id("doc_a", None), "doc_a")

    def test_lightrag_entities_map_to_graph_context(self) -> None:
        items = _graph_items_from_lightrag(
            [
                {
                    "entity_name": "Product X",
                    "entity_type": "product",
                    "description": "A product",
                    "source_id": "doc-1:chunk_000001",
                }
            ],
            [
                {
                    "src_id": "Product X",
                    "tgt_id": "Standard S",
                    "keywords": "SUBJECT_TO",
                    "description": "Applies to the product.",
                    "source_id": "doc-1:chunk_000001",
                }
            ],
            {},
            "doc-1",
        )
        evidence = _build_graph_evidence(items)
        self.assertEqual({node["name"] for node in evidence["nodes"]}, {"Product X", "Standard S"})
        self.assertEqual(evidence["relationships"][0]["relationship"], "SUBJECT_TO")

    def test_answer_question_uses_mix_and_does_not_call_neo4j(self) -> None:
        rag = AsyncMock()
        rag.aquery_data = AsyncMock(
            return_value={
                "status": "success",
                "data": {
                    "entities": [
                        {
                            "entity_name": "Product X",
                            "entity_type": "product",
                            "description": "A product",
                            "source_id": "doc-1:chunk_000001",
                        }
                    ],
                    "relationships": [
                        {
                            "src_id": "Product X",
                            "tgt_id": "Standard S",
                            "keywords": "SUBJECT_TO",
                            "description": "Applies",
                            "source_id": "doc-1:chunk_000001",
                        }
                    ],
                    "chunks": [
                        {
                            "chunk_id": "doc-1:chunk_000001-chunk-000",
                            "content": "A supporting document statement.",
                            "file_path": "document.pdf",
                        }
                    ],
                },
            }
        )
        rag.text_chunks.get_by_ids = AsyncMock(
            return_value=[
                {
                    "_id": "doc-1:chunk_000001-chunk-000",
                    "full_doc_id": "doc-1:chunk_000001",
                    "content": "A supporting document statement.",
                    "file_path": "document.pdf",
                    "page_range": {"start": 4, "end": 4},
                }
            ]
        )
        rag.chunks_vdb.get_by_ids = AsyncMock(return_value=[])

        with (
            patch("ul.chatbot._aget_lightrag_instance", new=AsyncMock(return_value=rag)),
            patch("ul.chatbot._run_graph_pipeline", return_value=graph_result()),
            patch("ul.neo4j_service._get_driver") as mock_driver,
            patch("ul.chatbot._generate_answer", return_value="Grounded draft."),
            patch(
                "ul.chatbot.format_final_answer",
                return_value="Standard S applies to Product X.",
            ),
        ):
            result = answer_question(
                "Which standard applies to Product X?",
                graph_id="kg-test",
                document_id="doc-1",
            )

        mock_driver.assert_not_called()
        rag.aquery_data.assert_awaited()
        param = rag.aquery_data.await_args.kwargs["param"]
        self.assertEqual(param.mode, "mix")
        self.assertEqual(result["answer"], "Standard S applies to Product X.")
        self.assertEqual(len(result["sources"]), 1)
        # Empty Neo4j lineage → empty View Lineage payload.
        self.assertEqual(result["graph_evidence"], {"nodes": [], "relationships": []})

    def test_neo4j_lineage_preferred_for_graph_evidence(self) -> None:
        lightrag_context = [
            relationship("Entity", "Noise A", "RELATED_TO", "Entity", "Noise B"),
        ]
        # A scoped Cypher query returns only the asked-for entity's paths, so
        # unrelated products never reach the response in the first place.
        neo4j_lineage = [
            relationship("Product", "Product X", "HAS_MODEL", "Model", "Model M"),
            relationship("Model", "Model M", "COMPLIES_WITH", "Standard", "Standard S"),
            relationship("Model", "Model M", "CONTAINS", "Component", "Component C"),
            relationship("Component", "Component C", "COMPLIES_WITH", "Standard", "Standard T"),
        ]
        with (
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=(lightrag_context, []),
            ),
            patch(
                "ul.chatbot._run_graph_pipeline",
                return_value=graph_result(neo4j_lineage),
            ),
            patch("ul.chatbot._generate_answer", return_value="Draft."),
            patch(
                "ul.chatbot.format_final_answer",
                return_value="Standard S and Standard T apply to Model M of Product X.",
            ),
        ):
            result = answer_question("Which standards apply to Product X Model M?")

        names = {node["name"] for node in result["graph_evidence"]["nodes"]}
        self.assertIn("Product X", names)
        self.assertIn("Model M", names)
        self.assertIn("Standard S", names)
        self.assertIn("Standard T", names)
        self.assertIn("Component C", names)
        self.assertNotIn("Other Phone", names)
        self.assertNotIn("Noise A", names)
        # Answer context remains LightRAG-only.
        self.assertEqual(result["graph_context"], lightrag_context)

    def test_lineage_not_truncated_by_lightrag_answer_text(self) -> None:
        from ul.chatbot import _lineage_for_question

        neo4j_lineage = [
            relationship("Product", "Product X", "HAS_MODEL", "Model", "Model M"),
            relationship("Model", "Model M", "CONTAINS", "Component", "Comp A"),
            relationship("Component", "Comp A", "COMPLIES_WITH", "Standard", "Standard A"),
            relationship("Component", "Comp A", "COMPLIES_WITH", "Standard", "Standard B"),
        ]
        # Even if an answer only mentioned Standard A, lineage keeps question-relevant Neo4j path.
        lineage = _lineage_for_question(
            neo4j_lineage,
            "Which standards apply to Product X Model M?",
        )
        evidence = _build_graph_evidence(lineage)
        names = {node["name"] for node in evidence["nodes"]}
        self.assertEqual(
            names,
            {"Product X", "Model M", "Comp A", "Standard A", "Standard B"},
        )

    def test_answer_uses_lightrag_even_when_neo4j_lineage_present(self) -> None:
        lightrag_context = [
            relationship("Product", "Product X", "SUBJECT_TO", "Standard", "From LightRAG"),
        ]
        neo4j_lineage = [
            relationship("Product", "Product X", "HAS_MODEL", "Model", "Model M"),
            relationship("Model", "Model M", "COMPLIES_WITH", "Standard", "From Neo4j"),
        ]
        with (
            patch(
                "ul.chatbot._retrieve_lightrag_context",
                return_value=(lightrag_context, []),
            ),
            patch(
                "ul.chatbot._run_graph_pipeline",
                return_value=graph_result(neo4j_lineage),
            ) as mock_graph,
            patch("ul.chatbot._generate_answer", return_value="Draft."),
            patch(
                "ul.chatbot.format_final_answer",
                return_value="From LightRAG applies to Product X.",
            ),
            patch("ul.chatbot._format_hybrid_context", wraps=chatbot_mod._format_hybrid_context) as mock_fmt,
        ):
            result = answer_question("Which standards apply to Product X Model M?")

        mock_graph.assert_called_once()
        fmt_graph = mock_fmt.call_args.args[0]
        self.assertEqual(fmt_graph, lightrag_context)
        self.assertEqual(result["graph_context"], lightrag_context)
        evidence_names = {node["name"] for node in result["graph_evidence"]["nodes"]}
        self.assertIn("From Neo4j", evidence_names)
        self.assertNotIn("From LightRAG", evidence_names)

    def test_prefer_question_relevant_sources_ranks_matching_files(self) -> None:
        from ul.chatbot import _prefer_question_relevant_sources

        chunks = [
            {
                "document_id": "a",
                "chunk_id": "1",
                "source_file": "iPhone 16 - Te.pdf",
                "page_range": {"start": 1, "end": 1},
                "text": "Apple battery notes",
            },
            {
                "document_id": "b",
                "chunk_id": "2",
                "source_file": "02_Product_Technical_Specification_Galaxy_A56R_RevC.pdf",
                "page_range": {"start": 4, "end": 4},
                "text": "Galaxy A56R SM-A568U1 IEC 60529",
            },
        ]
        ranked = _prefer_question_relevant_sources(
            chunks,
            "Which standards apply to the Galaxy A56R 5G SM-A568U1?",
            limit=2,
        )
        self.assertEqual(
            ranked[0]["source_file"],
            "02_Product_Technical_Specification_Galaxy_A56R_RevC.pdf",
        )

    def test_select_graph_items_keeps_clause_path_over_noise(self) -> None:
        from ul.chatbot import _select_question_relevant_graph_items

        items = []
        for index in range(80):
            items.append(
                relationship(
                    "Entity",
                    "Noise Product",
                    "RELATED_TO",
                    "Entity",
                    f"Noise Entity {index}",
                )
            )
        # Relevant multi-hop evidence placed late in the LightRAG dump.
        items.extend(
            [
                relationship("Entity", "SM-A568U1", "CONTAINS", "Entity", "Battery EB-BA568ALY"),
                relationship(
                    "Entity",
                    "Battery EB-BA568ALY",
                    "COMPLIES_WITH",
                    "Entity",
                    "UN Manual of Tests and Criteria",
                ),
                relationship(
                    "Entity",
                    "UN Manual of Tests and Criteria",
                    "HAS_CLAUSE",
                    "Entity",
                    "Part III, subsection 38.3",
                ),
            ]
        )
        selected = _select_question_relevant_graph_items(
            items,
            "What clauses apply to SM-A568U1?",
            limit=20,
        )
        blob = " | ".join(
            f"{item['source']['name']}->{item['target']['name']}"
            for item in selected
            if item.get("kind") == "relationship"
        )
        self.assertIn("Part III, subsection 38.3", blob)
        self.assertIn("UN Manual of Tests and Criteria", blob)
        self.assertIn("SM-A568U1", blob)
        self.assertLessEqual(len(selected), 20)

    def test_select_graph_items_keeps_standards_and_components(self) -> None:
        from ul.chatbot import _select_question_relevant_graph_items

        items = [
            relationship("Entity", "Other Phone", "RELATED_TO", "Entity", "Noise"),
            relationship("Entity", "SM-A568U1", "CONTAINS", "Entity", "Acoustic mesh"),
            relationship("Entity", "Acoustic mesh", "COMPLIES_WITH", "Entity", "IEC 60529"),
            relationship("Entity", "SM-A568U1", "CONTAINS", "Entity", "Battery Pack"),
            relationship(
                "Entity",
                "Battery Pack",
                "COMPLIES_WITH",
                "Entity",
                "IEC 62133-2:2017 + A1:2021",
            ),
        ]
        standards = _select_question_relevant_graph_items(
            items * 15,
            "What standards apply to SM-A568U1?",
            limit=12,
        )
        standards_blob = str(standards)
        self.assertIn("IEC 60529", standards_blob)
        self.assertIn("IEC 62133-2:2017 + A1:2021", standards_blob)

        components = _select_question_relevant_graph_items(
            items * 15,
            "What components are in SM-A568U1?",
            limit=12,
        )
        components_blob = str(components)
        self.assertIn("Acoustic mesh", components_blob)
        self.assertIn("Battery Pack", components_blob)

        # Small dumps are also ranked when the question names a seed entity.
        small = _select_question_relevant_graph_items(
            items,
            "What standards apply to SM-A568U1?",
            limit=12,
        )
        small_blob = str(small)
        self.assertIn("IEC 60529", small_blob)
        self.assertNotIn("Other Phone", small_blob)

    def test_document_chunks_map_all_before_relevance_rank(self) -> None:
        from ul.chatbot import _document_chunks_from_lightrag, _prefer_question_relevant_sources

        raw_chunks = [
            {
                "chunk_id": f"id-{index}",
                "content": f"noise chunk {index}",
                "file_path": "Other_Product.pdf",
            }
            for index in range(12)
        ]
        raw_chunks.append(
            {
                "chunk_id": "id-clause",
                "content": "SM-A568U1 battery clause Part III subsection 38.3",
                "file_path": "02_Product_Technical_Specification_Galaxy_A56R_RevC.pdf",
            }
        )
        mapped = _document_chunks_from_lightrag(raw_chunks, {})
        self.assertEqual(len(mapped), 13)
        ranked = _prefer_question_relevant_sources(
            mapped,
            "What clauses apply to SM-A568U1?",
            limit=8,
        )
        self.assertLessEqual(len(ranked), 8)
        self.assertTrue(
            any("38.3" in str(chunk.get("text") or "") for chunk in ranked)
        )

    def test_select_graph_items_drops_unrelated_when_seed_missing_from_dump(self) -> None:
        from ul.chatbot import _select_question_relevant_graph_items

        items = [
            relationship("Entity", "iPhone 16", "HAS_COMPONENT", "Entity", "Battery"),
            relationship("Entity", "UL 62368-1", "APPLIES_IN", "Entity", "United States"),
            relationship("Entity", "A3240", "COMPLIES_WITH", "Entity", "IEC 62368-1"),
        ]
        selected = _select_question_relevant_graph_items(
            items * 30,
            "What clauses apply to SM-A568U1?",
            limit=20,
        )
        blob = str(selected).lower()
        self.assertNotIn("iphone", blob)
        self.assertNotIn("a3240", blob)
        self.assertEqual(selected, [])

    def test_empty_question_does_not_query_lightrag(self) -> None:
        with patch("ul.chatbot._aget_lightrag_instance") as mock_get:
            graph_context, document_context = _retrieve_lightrag_context("   ", "doc-1")
            mock_get.assert_not_called()
        self.assertEqual(graph_context, [])
        self.assertEqual(document_context, [])


if __name__ == "__main__":
    unittest.main()
