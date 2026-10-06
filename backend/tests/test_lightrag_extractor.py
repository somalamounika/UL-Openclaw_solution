"""Tests for parallel LightRAG chunk extraction in extract_graph_from_chunks."""

from __future__ import annotations

import json
import inspect
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from ul import lightrag_extractor


def _chunk(chunk_id: str, *, text: str = "sample text", source_file: str = "doc.pdf") -> dict:
    return {
        "chunk_id": chunk_id,
        "source_file": source_file,
        "page_range": {"start": 1, "end": 1},
        "text": text,
    }


def _entity(
    name: str,
    *,
    entity_type: str = "product",
    description: str = "",
    source_file: str = "doc.pdf",
    chunk_id: str = "chunk_001",
) -> dict:
    return {
        "name": name,
        "entity_type": entity_type,
        "description": description,
        "source_file": source_file,
        "page_range": {"start": 1, "end": 1},
        "chunk_id": chunk_id,
    }


def _relationship(
    source: str,
    target: str,
    *,
    relationship: str = "has_component",
    source_type: str = "product",
    target_type: str = "component",
    keywords: str = "kw",
    evidence: str = "evidence text",
    chunk_id: str = "chunk_001",
) -> dict:
    return {
        "source_node": source,
        "source_type": source_type,
        "relationship": relationship,
        "destination_node": target,
        "destination_type": target_type,
        "keywords": keywords,
        "evidence": evidence,
        "source_file": "doc.pdf",
        "page_range": {"start": 1, "end": 1},
        "chunk_id": chunk_id,
    }


class NormalizeEntityTypeTests(unittest.TestCase):
    def test_keeps_prompt_py_entity_types(self) -> None:
        cases = {
            "Applicant": "applicant",
            "FileNumber": "file_number",
            "Volume": "volume",
            "Section": "section",
            "Deliverable": "deliverable",
            "TestPlan": "test_plan",
            "Location": "location",
            "ManufacturingLocation": "location",
            "Address": "address",
            "UL": "ul",
            "Product": "product",
            "Model": "model",
            "Component": "component",
            "Standard": "standard",
            "Clause": "clause",
            "Certification": "certification",
            "Test": "test",
            "TestRecord": "test",
            "Company": "company",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    lightrag_extractor._normalize_entity_type(raw),
                    expected,
                )

    def test_does_not_collapse_section_or_applicant(self) -> None:
        self.assertEqual(lightrag_extractor._normalize_entity_type("Section"), "section")
        self.assertEqual(
            lightrag_extractor._normalize_entity_type("Applicant"),
            "applicant",
        )


class ExtractGraphFromChunksTests(unittest.TestCase):
    def test_empty_chunks_returns_empty_graph(self) -> None:
        result = lightrag_extractor.extract_graph_from_chunks([])
        self.assertEqual(result["companies"], [])
        self.assertEqual(result["products"], [])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["relationships"], [])
        self.assertEqual(result["file_numbers"], [])
        self.assertEqual(result["sections"], [])

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_keeps_prompt_py_node_types_in_graph(self, mock_extract) -> None:
        mock_extract.return_value = (
            [
                _entity("E123456", entity_type="file_number"),
                _entity("Volume 1", entity_type="volume"),
                _entity("Section 4", entity_type="section"),
                _entity("Applicant", entity_type="applicant"),
                _entity("Samsung SDI", entity_type="company"),
            ],
            [
                _relationship(
                    "E123456",
                    "Volume 1",
                    relationship="has_volume",
                    source_type="file_number",
                    target_type="volume",
                )
            ],
        )

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])

        self.assertEqual(result["file_numbers"][0]["name"], "E123456")
        self.assertEqual(result["volumes"][0]["name"], "Volume 1")
        self.assertEqual(result["sections"][0]["name"], "Section 4")
        self.assertEqual(result["applicants"][0]["name"], "Applicant")
        self.assertEqual(result["companies"][0]["name"], "Samsung SDI")
        self.assertEqual(result["relationships"][0]["source_type"], "file_number")
        self.assertEqual(result["relationships"][0]["destination_type"], "volume")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_submits_all_chunks_through_executor(self, mock_extract) -> None:
        chunks = [_chunk("c1"), _chunk("c2"), _chunk("c3")]
        mock_extract.side_effect = [
            ([_entity("A")], []),
            ([_entity("B")], []),
            ([_entity("C")], []),
        ]

        result = lightrag_extractor.extract_graph_from_chunks(chunks)

        self.assertEqual(mock_extract.call_count, 3)
        self.assertEqual(len(result["products"]), 3)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_merge_preserves_original_chunk_order(self, mock_extract) -> None:
        """First chunk wins provenance even when later chunks finish first."""

        def _slow_first(chunk: dict) -> tuple[list[dict], list[dict]]:
            if chunk["chunk_id"] == "first":
                time.sleep(0.05)
                return (
                    [
                        _entity(
                            "Widget",
                            description="from first",
                            source_file="first.pdf",
                            chunk_id="first",
                        )
                    ],
                    [],
                )
            return (
                [
                    _entity(
                        "Widget",
                        description="from second",
                        source_file="second.pdf",
                        chunk_id="second",
                    )
                ],
                [],
            )

        mock_extract.side_effect = _slow_first
        chunks = [_chunk("first", source_file="first.pdf"), _chunk("second", source_file="second.pdf")]

        result = lightrag_extractor.extract_graph_from_chunks(chunks)

        self.assertEqual(len(result["products"]), 1)
        product = result["products"][0]
        self.assertEqual(product["source_file"], "first.pdf")
        self.assertEqual(product["chunk_id"], "first")
        self.assertIn("from first", product["evidence"])

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_duplicate_entity_keeps_first_chunk_evidence(self, mock_extract) -> None:
        mock_extract.side_effect = [
            ([_entity("Widget", description="part one")], []),
            ([_entity("Widget", description="part two")], []),
        ]
        chunks = [_chunk("c1"), _chunk("c2")]

        result = lightrag_extractor.extract_graph_from_chunks(chunks)

        self.assertEqual(len(result["products"]), 1)
        # Without the validation layer, bucketing keeps the first occurrence.
        self.assertEqual(result["products"][0]["evidence"], "part one")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_relationship_descriptions_and_keywords_preserved(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [],
                [
                    _relationship(
                        "Phone",
                        "Battery",
                        keywords="battery, component",
                        evidence="Battery is a component of Phone.",
                    )
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])

        self.assertEqual(len(result["relationships"]), 1)
        rel = result["relationships"][0]
        self.assertEqual(rel["keywords"], "battery, component")
        self.assertEqual(rel["evidence"], "Battery is a component of Phone.")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_relationship_deduplication_merges_evidence(self, mock_extract) -> None:
        dup = _relationship("Phone", "Battery", evidence="first evidence", keywords="first-kw")
        dup_later = _relationship(
            "Phone",
            "Battery",
            evidence="second evidence",
            keywords="second-kw",
        )
        mock_extract.side_effect = [
            ([], [dup]),
            ([], [dup_later]),
        ]
        chunks = [_chunk("c1"), _chunk("c2")]

        result = lightrag_extractor.extract_graph_from_chunks(chunks)

        self.assertEqual(len(result["relationships"]), 1)
        rel = result["relationships"][0]
        self.assertIn("first evidence", rel["evidence"])
        self.assertIn("second evidence", rel["evidence"])
        self.assertIn("first-kw", rel["keywords"])
        self.assertIn("second-kw", rel["keywords"])

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_failed_extraction_propagates_exception(self, mock_extract) -> None:
        mock_extract.side_effect = RuntimeError("LLM failure")
        chunks = [_chunk("c1"), _chunk("c2")]

        with self.assertRaises(RuntimeError):
            lightrag_extractor.extract_graph_from_chunks(chunks)

    @patch.object(lightrag_extractor, "ThreadPoolExecutor")
    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_uses_bounded_worker_count(
        self,
        mock_extract,
        mock_executor_cls,
    ) -> None:
        mock_extract.return_value = ([], [])
        mock_executor = mock_executor_cls.return_value.__enter__.return_value
        mock_executor.submit.side_effect = lambda fn, chunk: Mock(
            result=lambda: fn(chunk)
        )

        chunks = [_chunk(f"c{i}") for i in range(6)]
        with patch.object(lightrag_extractor.config, "EXTRACTION_CONCURRENCY", 4):
            lightrag_extractor.extract_graph_from_chunks(chunks)

        mock_executor_cls.assert_called_once_with(max_workers=4)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_models_bucketed_separately_from_products(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Laptop Family", entity_type="product"),
                    _entity("Laptop Family Pro 2025", entity_type="model"),
                ],
                [
                    _relationship(
                        "Laptop Family",
                        "Laptop Family Pro 2025",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Laptop Family Pro 2025",
                        "Battery",
                        source_type="model",
                        target_type="component",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])

        self.assertEqual(len(result["products"]), 1)
        self.assertEqual(result["products"][0]["name"], "Laptop Family")
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "Laptop Family Pro 2025")
        self.assertEqual(result["models"][0]["identity_key"], "model:laptop family pro 2025")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_company_legal_form_variants_merge(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Acme", entity_type="company"),
                    _entity("Acme Inc.", entity_type="company"),
                    _entity("Acme Incorporated", entity_type="company"),
                    _entity("Acme Support", entity_type="company"),
                ],
                [
                    _relationship(
                        "Acme Inc.",
                        "Laptop Family",
                        relationship="produces",
                        source_type="company",
                        target_type="product",
                    ),
                    _relationship(
                        "Acme",
                        "Laptop Family",
                        relationship="produces",
                        source_type="company",
                        target_type="product",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        company_names = sorted(
            c["name"]
            for c in result["companies"]
            if c["name"] != "UL Solutions"
        )
        applicant_names = [a["name"] for a in result.get("applicants") or []]
        self.assertEqual(company_names, ["Acme Support"])
        self.assertEqual(len(applicant_names), 1)
        self.assertTrue(applicant_names[0].startswith("Acme") and applicant_names[0] != "Acme Support")
        produces = [
            rel
            for rel in result["relationships"]
            if rel["relationship"] == "has_product"
        ]
        self.assertEqual(len(produces), 1)
        self.assertNotEqual(produces[0]["source_node"], "Acme Support")
        self.assertEqual(produces[0]["source_type"], "applicant")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_company_generic_descriptor_variants_merge(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Contoso", entity_type="applicant"),
                    _entity("Contoso Electronics Co., Ltd.", entity_type="applicant"),
                    _entity("Contoso Display", entity_type="applicant"),
                ],
                [
                    _relationship(
                        "UL Solutions",
                        "Contoso",
                        relationship="party_role_applicant",
                        source_type="ul",
                        target_type="applicant",
                    ),
                    _relationship(
                        "UL Solutions",
                        "Contoso Electronics Co., Ltd.",
                        relationship="party_role_applicant",
                        source_type="ul",
                        target_type="applicant",
                    ),
                    _relationship(
                        "UL Solutions",
                        "Contoso Display",
                        relationship="party_role_applicant",
                        source_type="ul",
                        target_type="applicant",
                    ),
                    _relationship(
                        "Contoso",
                        "Widget 16",
                        relationship="has_product",
                        source_type="applicant",
                        target_type="product",
                    ),
                    _relationship(
                        "Contoso Electronics Co., Ltd.",
                        "Widget 16",
                        relationship="has_product",
                        source_type="applicant",
                        target_type="product",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        applicant_names = sorted(item["name"] for item in result.get("applicants") or [])
        self.assertEqual(len(applicant_names), 2)
        self.assertTrue(
            any("electronics" in name.lower() or name == "Contoso" for name in applicant_names)
        )
        self.assertTrue(any("display" in name.lower() for name in applicant_names))
        applicant_edges = [
            rel
            for rel in result["relationships"]
            if rel["relationship"] == "party_role_applicant"
        ]
        dests = {rel["destination_node"] for rel in applicant_edges}
        self.assertEqual(len(dests), 2)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_model_aliases_of_same_product_merge(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Laptop Family", entity_type="product"),
                    _entity("Laptop Family Pro (13-inch, 2025)", entity_type="model"),
                    _entity("13-inch Laptop Family Pro", entity_type="model"),
                    _entity("Laptop Family Max 2025", entity_type="model"),
                ],
                [
                    _relationship(
                        "Laptop Family",
                        "Laptop Family Pro (13-inch, 2025)",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Laptop Family",
                        "13-inch Laptop Family Pro",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Laptop Family",
                        "Laptop Family Max 2025",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "13-inch Laptop Family Pro",
                        "Battery",
                        source_type="model",
                        target_type="component",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        model_names = sorted(m["name"] for m in result["models"])
        self.assertEqual(len(model_names), 2)
        self.assertIn("Laptop Family Max 2025", model_names)
        self.assertTrue(
            any("Laptop Family Pro" in name and "Max" not in name for name in model_names)
        )
        has_model = [
            rel for rel in result["relationships"] if rel["relationship"] == "has_model"
        ]
        self.assertEqual(len(has_model), 2)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_generic_company_and_model_dedup_for_non_apple_pdf(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Contoso", entity_type="company"),
                    _entity("Contoso Ltd.", entity_type="company"),
                    _entity("Widget Pro", entity_type="product"),
                    _entity("Widget Pro (Gen 2, 2025)", entity_type="model"),
                    _entity("Gen 2 Widget Pro", entity_type="model"),
                    _entity("Widget Pro Ultra", entity_type="model"),
                ],
                [
                    _relationship(
                        "Contoso Ltd.",
                        "Widget Pro",
                        relationship="produces",
                        source_type="company",
                        target_type="product",
                    ),
                    _relationship(
                        "Widget Pro",
                        "Widget Pro (Gen 2, 2025)",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Widget Pro",
                        "Gen 2 Widget Pro",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Widget Pro",
                        "Widget Pro Ultra",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])

        brand_companies = [
            c for c in result["applicants"] if c["name"] != "UL Solutions"
        ]
        self.assertEqual(len(brand_companies), 1)
        self.assertEqual(brand_companies[0]["name"], "Contoso Ltd.")
        model_names = sorted(m["name"] for m in result["models"])
        self.assertEqual(len(model_names), 2)
        self.assertIn("Widget Pro Ultra", model_names)
        self.assertTrue(any("Gen 2" in name for name in model_names))

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_same_name_product_and_model_remain_distinct(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Widget X", entity_type="product"),
                    _entity("Widget X", entity_type="model"),
                    _entity("Battery", entity_type="component"),
                ],
                [
                    _relationship(
                        "Widget X",
                        "Widget X",
                        relationship="has_model",
                        source_type="product",
                        target_type="model",
                    ),
                    _relationship(
                        "Widget X",
                        "Battery",
                        source_type="model",
                        target_type="component",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        # Without the validation layer, typed same-name entities stay in both buckets.
        self.assertEqual(len(result["products"]), 1)
        self.assertEqual(result["products"][0]["name"], "Widget X")
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "Widget X")
        self.assertTrue(
            any(
                rel["relationship"] == "has_model"
                and rel["source_type"] == "product"
                and rel["destination_type"] == "model"
                and rel["source_node"] == "Widget X"
                and rel["destination_node"] == "Widget X"
                for rel in result["relationships"]
            )
        )
        self.assertTrue(
            any(
                rel["source_type"] == "model"
                and rel["source_node"] == "Widget X"
                and rel["destination_node"] == "Battery"
                for rel in result["relationships"]
            )
        )

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_company_descriptive_suffix_not_merged(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Example Corporation", entity_type="company"),
                    _entity("Example Consulting Corporation", entity_type="company"),
                ],
                [],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        names = sorted(
            c["name"]
            for c in result["companies"]
            if c["name"] != "UL Solutions"
        )
        self.assertEqual(len(names), 2)
        self.assertEqual(
            names,
            ["Example Consulting Corporation", "Example Corporation"],
        )

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_component_singular_plural_merges_but_not_partial_names(
        self, mock_extract
    ) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Printed Circuit Board", entity_type="component"),
                    _entity("Printed Circuit Boards", entity_type="component"),
                    _entity("Battery", entity_type="component"),
                    _entity("Battery Connector", entity_type="component"),
                    _entity("Left Speaker", entity_type="component"),
                    _entity("Right Speaker", entity_type="component"),
                ],
                [],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        names = sorted(_component_name(c) for c in result["components"])
        self.assertEqual(len(names), 5)
        self.assertTrue(
            any(n.startswith("Printed Circuit Board") for n in names)
        )
        self.assertIn("Battery", names)
        self.assertIn("Battery Connector", names)
        self.assertIn("Left Speaker", names)
        self.assertIn("Right Speaker", names)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_hierarchy_hops_ul_company_product_model_component(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Contoso Ltd.", entity_type="company"),
                    _entity("Widget Pro", entity_type="product"),
                    _entity("Widget Pro Gen 2", entity_type="model"),
                    _entity("Battery", entity_type="component"),
                ],
                [
                    _relationship(
                        "Widget Pro",
                        "Battery",
                        source_type="product",
                        target_type="component",
                    ),
                ],
            ),
        ]

        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        rels = result["relationships"]

        serves = [
            r
            for r in rels
            if r["relationship"] in {"serves", "has_company"}
            and r["source_node"] == "UL Solutions"
        ]
        produces = [
            r
            for r in rels
            if r["relationship"] in {"produces", "has_product"}
            and r["source_node"] == "Contoso Ltd."
            and r["destination_node"] == "Widget Pro"
        ]
        has_model = [
            r
            for r in rels
            if r["relationship"] == "has_model"
            and r["source_node"] == "Widget Pro"
            and r["destination_node"] == "Widget Pro Gen 2"
        ]
        model_component = [
            r
            for r in rels
            if r["relationship"] == "contains"
            and r["source_type"] == "model"
            and r["source_node"] == "Widget Pro Gen 2"
            and r["destination_node"] == "Battery"
        ]

        self.assertEqual(len(serves), 0)
        self.assertEqual(len(produces), 0)
        self.assertEqual(len(has_model), 1)
        self.assertEqual(len(model_component), 1)

    def test_relationship_keywords_preserve_has_model_token(self) -> None:
        rel = lightrag_extractor._relationship_from_keywords(
            "product hierarchy, HAS_MODEL, variant",
            "product",
            "model",
        )
        self.assertEqual(rel, "has_model")

    def test_relationship_keywords_map_prompt_py_tokens(self) -> None:
        cases = [
            ("PARTY_ROLE_APPLICANT", "ul", "applicant", "party_role_applicant"),
            ("HAS_PRODUCT", "applicant", "product", "has_product"),
            ("CONTAINS", "model", "component", "contains"),
            ("HAS_COMPONENT", "model", "component", "contains"),
            ("COMPLIES_WITH", "component", "standard", "complies_with"),
            ("PARTY_ROLE_MANUFACTURER", "component", "company", "party_role_manufacturer"),
            ("PARTY_ROLE_SUPPLIER", "company", "applicant", "party_role_supplier"),
            ("HAS_CERTIFICATION", "component", "certification", "has_certification"),
        ]
        for keywords, source_type, target_type, expected in cases:
            with self.subTest(keywords=keywords):
                self.assertEqual(
                    lightrag_extractor._relationship_from_keywords(
                        keywords, source_type, target_type
                    ),
                    expected,
                )

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_standard_name_variants_merge_safely(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("UL/CSA/IEC 62368-1", entity_type="standard"),
                    _entity("IEC/UL 62368-1", entity_type="standard"),
                    _entity("UL/CSA 62368-1", entity_type="standard"),
                    _entity("UL 1642", entity_type="standard"),
                    _entity("UL 2054", entity_type="standard"),
                    _entity("UL 62133-2", entity_type="standard"),
                    _entity("IEC 62133-2", entity_type="standard"),
                    _entity("UL/IEC 62133-2", entity_type="standard"),
                ],
                [],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        names = sorted(s["name"] for s in result["standards"])
        # Multi-body 62368-1 variants collapse; single-body 62133-2 citations stay distinct.
        self.assertEqual(len(names), 6)
        self.assertIn("UL 1642", names)
        self.assertIn("UL 2054", names)
        self.assertIn("UL 62133-2", names)
        self.assertIn("IEC 62133-2", names)
        self.assertIn("UL/IEC 62133-2", names)
        self.assertEqual(sum(1 for n in names if "62368-1" in n), 1)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_component_part_numbers_merge_without_merging_distinct_pns(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Battery pack GH82-41001A / EB-BA568ABY", entity_type="component"),
                    _entity("GH82-41001A / EB-BA568ABY - BATTERY PACK", entity_type="component"),
                    _entity("Battery Pack EB-BA568ABY", entity_type="component"),
                    _entity("Battery EB-BA568ABY Rev B", entity_type="component"),
                    _entity("USB-C Receptacle 12401954E4A2A", entity_type="component"),
                ],
                [],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        names = sorted(_component_name(c) for c in result["components"])
        self.assertEqual(len(names), 2)
        self.assertTrue(any("EB-BA568ABY" in name for name in names))
        self.assertTrue(any("USB-C" in name or "12401954E4A2A" in name for name in names))

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_volume_and_section_names_are_normalized(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("Vol. 1", entity_type="volume"),
                    _entity("Sec. 3", entity_type="section"),
                ],
                [
                    _relationship(
                        "E1",
                        "Vol. 1",
                        relationship="has_volume",
                        source_type="file_number",
                        target_type="volume",
                    ),
                ],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        self.assertEqual(result["volumes"][0]["name"], "Volume 1")
        self.assertEqual(result["sections"][0]["name"], "Section 3")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_development_stage_is_not_kept_as_variant(self, mock_extract) -> None:
        mock_extract.side_effect = [
            (
                [
                    _entity("SM-A568B/DS", entity_type="model"),
                    _entity("DVT1", entity_type="variant"),
                    _entity("EU/UK Dual-SIM Configuration", entity_type="variant"),
                ],
                [
                    _relationship(
                        "SM-A568B/DS",
                        "DVT1",
                        relationship="has_variant",
                        source_type="model",
                        target_type="variant",
                    ),
                    _relationship(
                        "Phone",
                        "EU/UK Dual-SIM Configuration",
                        relationship="has_variant",
                        source_type="product",
                        target_type="variant",
                    ),
                ],
            ),
        ]
        result = lightrag_extractor.extract_graph_from_chunks([_chunk("c1")])
        names = {item["name"] for item in result.get("variants") or []}
        self.assertNotIn("DVT1", names)
        self.assertFalse(
            any(
                rel["destination_node"] == "DVT1"
                or rel.get("relationship") == "has_variant"
                for rel in result["relationships"]
            )
        )


class ProjectIsolationTests(unittest.TestCase):
    def _project_chunk(
        self,
        chunk_id: str,
        *,
        project_id: str,
        text: str,
        source_file: str = "doc.pdf",
    ) -> dict:
        return {
            "chunk_id": chunk_id,
            "project_id": project_id,
            "metadata": {"project_id": project_id},
            "source_file": source_file,
            "page_range": {"start": 1, "end": 1},
            "text": text,
        }

    def _extract_from_chunk_text(self, chunk: dict) -> tuple[list[dict], list[dict]]:
        text = str(chunk.get("text") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        source_file = str(chunk.get("source_file") or "doc.pdf")
        entities: list[dict] = []
        relationships: list[dict] = []

        def add_entity(name: str, entity_type: str) -> None:
            entities.append(
                _entity(
                    name,
                    entity_type=entity_type,
                    source_file=source_file,
                    chunk_id=chunk_id,
                )
            )

        if "Apple" in text:
            add_entity("Apple", "company")
        if "iPhone" in text:
            add_entity("iPhone 16", "product")
        if "Apple" in text and "iPhone" in text:
            relationships.append(
                _relationship(
                    "Apple",
                    "iPhone 16",
                    relationship="produces",
                    source_type="company",
                    target_type="product",
                    chunk_id=chunk_id,
                )
            )
        if "Galaxy A56R 5G" in text:
            add_entity("Galaxy A56R 5G", "product")
        if "IEC 62368-1:2023" in text:
            add_entity("IEC 62368-1:2023", "standard")
        if "Galaxy A56R 5G" in text and "IEC 62368-1:2023" in text:
            relationships.append(
                _relationship(
                    "Galaxy A56R 5G",
                    "IEC 62368-1:2023",
                    relationship="subject_to",
                    source_type="product",
                    target_type="standard",
                    chunk_id=chunk_id,
                )
            )
        if "TEST-001" in text:
            add_entity("TEST-001", "test")
        if "CERT-001" in text:
            add_entity("CERT-001", "certification")
        if "Clause 6" in text:
            add_entity("Clause 6", "clause")
        if "Annex G" in text:
            add_entity("Annex G", "clause")
        if "TP-A56R-REG-04 Rev B" in text:
            add_entity("TP-A56R-REG-04 Rev B", "test_plan")
        if "TR-A56R-BAT-009 Rev A" in text:
            add_entity("TR-A56R-BAT-009 Rev A", "test")
        return entities, relationships

    def _graph_names(self, graph: dict) -> set[str]:
        names: set[str] = set()
        for key, items in graph.items():
            if key == "relationships" or not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                for field in ("name", "company_name", "canonical_name", "component_name"):
                    value = str(item.get(field) or "").strip()
                    if value:
                        names.add(value)
        for rel in graph.get("relationships") or []:
            for field in ("source_node", "destination_node"):
                value = str(rel.get(field) or "").strip()
                if value:
                    names.add(value)
        return names

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_cross_project_chunks_are_excluded_from_extraction(self, mock_extract) -> None:
        mock_extract.side_effect = self._extract_from_chunk_text
        project_a = self._project_chunk(
            "a1",
            project_id="project_a",
            text="Apple manufactures the iPhone 16.",
            source_file="iphone.pdf",
        )
        project_b = self._project_chunk(
            "b1",
            project_id="project_b",
            text="Galaxy A56R 5G is evaluated against IEC 62368-1:2023.",
            source_file="galaxy.pdf",
        )

        result = lightrag_extractor.extract_graph_from_chunks(
            [project_a, project_b],
            project_id="project_b",
        )

        submitted = [call.args[0] for call in mock_extract.call_args_list]
        self.assertEqual([c["chunk_id"] for c in submitted], ["b1"])
        self.assertEqual({_chunk_project_id(c) for c in submitted}, {"project_b"})
        names = self._graph_names(result)
        self.assertIn("Galaxy A56R 5G", names)
        self.assertIn("IEC 62368-1:2023", names)
        self.assertNotIn("Apple", names)
        self.assertTrue(all("iPhone" not in name for name in names))

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_catalogue_chunks_are_not_extraction_evidence(self, mock_extract) -> None:
        mock_extract.side_effect = self._extract_from_chunk_text
        project_b = self._project_chunk(
            "b1",
            project_id="project_b",
            text="Galaxy A56R 5G is evaluated against IEC 62368-1:2023.",
        )
        catalogue = self._project_chunk(
            "cat1",
            project_id="catalogue",
            text="Catalogue maps IEC 62368-1 to TEST-001, CERT-001, Clause 6, and Annex G.",
            source_file="standards.pdf",
        )

        result = lightrag_extractor.extract_graph_from_chunks(
            [catalogue, project_b],
            project_id="project_b",
        )

        submitted_text = " ".join(
            call.args[0]["text"] for call in mock_extract.call_args_list
        )
        self.assertIn("Galaxy A56R 5G", submitted_text)
        self.assertNotIn("TEST-001", submitted_text)
        names = self._graph_names(result)
        self.assertIn("Galaxy A56R 5G", names)
        self.assertIn("IEC 62368-1:2023", names)
        self.assertTrue(
            any(rel["relationship"] == "complies_with" for rel in result["relationships"])
        )
        self.assertNotIn("TEST-001", names)
        self.assertNotIn("CERT-001", names)
        self.assertNotIn("Clause 6", names)
        self.assertNotIn("Annex G", names)

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_current_project_test_plan_and_record_are_kept(self, mock_extract) -> None:
        mock_extract.side_effect = self._extract_from_chunk_text
        project_b = self._project_chunk(
            "b1",
            project_id="project_b",
            text=(
                "Galaxy A56R 5G testing uses TP-A56R-REG-04 Rev B and "
                "TR-A56R-BAT-009 Rev A."
            ),
        )

        result = lightrag_extractor.extract_graph_from_chunks(
            [project_b],
            project_id="project_b",
        )

        names = self._graph_names(result)
        self.assertIn("TP-A56R-REG-04 Rev B", names)
        self.assertIn("TR-A56R-BAT-009 Rev A", names)
        self.assertEqual(result["test_plans"][0]["name"], "TP-A56R-REG-04 Rev B")
        self.assertEqual(result["tests"][0]["name"], "TR-A56R-BAT-009 Rev A")

    @patch.object(lightrag_extractor, "extract_chunk_with_lightrag")
    def test_mixed_project_chunks_are_filtered_and_logged(self, mock_extract) -> None:
        mock_extract.side_effect = self._extract_from_chunk_text
        project_a = self._project_chunk(
            "a1",
            project_id="project_a",
            text="Apple manufactures the iPhone 16.",
        )
        project_b = self._project_chunk(
            "b1",
            project_id="project_b",
            text="Galaxy A56R 5G is evaluated against IEC 62368-1:2023.",
        )

        with self.assertLogs("ul.lightrag_extractor", level="WARNING") as logs:
            result = lightrag_extractor.extract_graph_from_chunks(
                [project_a, project_b],
                project_id="project_b",
            )

        submitted = [call.args[0] for call in mock_extract.call_args_list]
        self.assertEqual([c["project_id"] for c in submitted], ["project_b"])
        self.assertTrue(
            any(
                "Ignoring chunk from project project_a while extracting project project_b"
                in message
                for message in logs.output
            )
        )
        names = self._graph_names(result)
        self.assertNotIn("Apple", names)
        self.assertNotIn("iPhone 16", names)

    def test_mixed_chunks_without_current_project_id_fail_closed(self) -> None:
        project_a = self._project_chunk("a1", project_id="project_a", text="Project A fact.")
        project_b = self._project_chunk("b1", project_id="project_b", text="Project B fact.")
        with self.assertRaises(ValueError) as ctx:
            lightrag_extractor.extract_graph_from_chunks([project_a, project_b])
        self.assertIn("Mixed-project chunks", str(ctx.exception))

    @patch.object(lightrag_extractor, "chat_json")
    def test_continuation_uses_same_chunk_not_another_project(self, mock_chat) -> None:
        first_entities = [
            {
                "name": f"Entity-{index}",
                "type": "component",
                "description": "present in the current chunk",
            }
            for index in range(lightrag_extractor._MAX_ENTITY_RECORDS)
        ]
        mock_chat.side_effect = [
            json.dumps({"entities": first_entities, "relationships": []}),
            json.dumps(
                {
                    "entities": [
                        {
                            "name": "Battery Assembly",
                            "type": "component",
                            "description": "named in the current chunk",
                        }
                    ],
                    "relationships": [],
                }
            ),
        ]
        chunk = self._project_chunk(
            "b1",
            project_id="project_b",
            text="Current project document mentions Battery Assembly.",
        )

        entities, _relationships = lightrag_extractor.extract_chunk_with_lightrag(chunk)

        self.assertEqual(mock_chat.call_count, 2)
        _system, continue_user = mock_chat.call_args_list[1].args
        self.assertIn("Current project document mentions Battery Assembly.", continue_user)
        self.assertIn("bookkeeping only", continue_user)
        self.assertNotIn("iPhone", continue_user)
        self.assertTrue(any(item["name"] == "Battery Assembly" for item in entities))


def _chunk_project_id(chunk: dict) -> str:
    return lightrag_extractor._chunk_project_id(chunk)


class IndexChunksWithLightragTests(unittest.TestCase):
    def test_empty_chunks_returns_zero_without_indexing(self) -> None:
        with patch.object(lightrag_extractor, "_aget_lightrag_instance") as mock_get:
            self.assertEqual(lightrag_extractor.index_chunks_with_lightrag([]), 0)
            mock_get.assert_not_called()

    def test_payload_skips_empty_text_and_keeps_lists_aligned(self) -> None:
        chunks = [
            {"chunk_id": "c1", "text": "hello", "source_file": "a.pdf"},
            {"chunk_id": "c2", "text": "   ", "source_file": "a.pdf"},
            {"chunk_id": "c3", "text": "world", "source_file": "b.pdf"},
        ]
        texts, ids, file_paths = lightrag_extractor._prepare_lightrag_index_payload(
            chunks,
            run_id="run-a",
        )
        self.assertEqual(texts, ["hello", "world"])
        self.assertEqual(ids, ["run-a:c1", "run-a:c3"])
        self.assertEqual(file_paths, ["a.pdf", "b.pdf"])
        self.assertEqual(len(texts), len(ids))
        self.assertEqual(len(ids), len(file_paths))
        self.assertEqual(chunks[0]["chunk_id"], "c1")

    def test_ids_are_unique_across_process_runs(self) -> None:
        chunks = [_chunk("chunk_000001"), _chunk("ref_standards_000001")]
        _, ids_a, _ = lightrag_extractor._prepare_lightrag_index_payload(
            chunks, run_id="doc-1"
        )
        _, ids_b, _ = lightrag_extractor._prepare_lightrag_index_payload(
            chunks, run_id="doc-2"
        )
        self.assertEqual(ids_a, ["doc-1:chunk_000001", "doc-1:ref_standards_000001"])
        self.assertEqual(ids_b, ["doc-2:chunk_000001", "doc-2:ref_standards_000001"])
        self.assertTrue(set(ids_a).isdisjoint(ids_b))

    @patch.object(lightrag_extractor, "_aget_lightrag_instance", new_callable=AsyncMock)
    def test_batch_enqueues_once_with_native_kg(self, mock_get) -> None:
        rag = AsyncMock()
        mock_get.return_value = rag
        chunks = [
            {"chunk_id": "c1", "text": "hello", "source_file": "a.pdf"},
            {"chunk_id": "c2", "text": "   ", "source_file": "a.pdf"},
            {"chunk_id": "c3", "text": "world", "source_file": "b.pdf"},
        ]

        indexed = lightrag_extractor.index_chunks_with_lightrag(chunks, run_id="run-a")

        self.assertEqual(indexed, 2)
        rag.apipeline_enqueue_documents.assert_awaited_once_with(
            ["hello", "world"],
            ids=["run-a:c1", "run-a:c3"],
            file_paths=["a.pdf", "b.pdf"],
        )
        rag.apipeline_process_enqueue_documents.assert_awaited_once()
        rag.insert.assert_not_called()
        self.assertNotIn(
            "process_options",
            rag.apipeline_enqueue_documents.await_args.kwargs,
        )
        self.assertFalse(hasattr(lightrag_extractor, "PROCESS_OPTION_SKIP_KG"))
        source = inspect.getsource(lightrag_extractor._aindex_chunks_with_lightrag)
        self.assertNotIn("PROCESS_OPTION_SKIP_KG", source)
        self.assertNotIn('process_options="!"', source)

    @patch.object(lightrag_extractor, "_aget_lightrag_instance", new_callable=AsyncMock)
    def test_second_index_call_reuses_loop(self, mock_get) -> None:
        rag = AsyncMock()
        mock_get.return_value = rag
        chunk = _chunk("c1")

        first = lightrag_extractor.index_chunks_with_lightrag([chunk], run_id="run-1")
        loop_after_first = lightrag_extractor._LIGHTRAG_LOOP
        thread_after_first = lightrag_extractor._LIGHTRAG_THREAD
        second = lightrag_extractor.index_chunks_with_lightrag([chunk], run_id="run-2")

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertIs(lightrag_extractor._LIGHTRAG_LOOP, loop_after_first)
        self.assertIs(lightrag_extractor._LIGHTRAG_THREAD, thread_after_first)
        self.assertEqual(rag.apipeline_enqueue_documents.await_count, 2)

    @patch.object(lightrag_extractor, "chat_json", return_value='{"entities":[],"relationships":[]}')
    def test_ul_extraction_still_uses_chat_json_not_insert(self, mock_chat) -> None:
        with patch.object(lightrag_extractor, "_aget_lightrag_instance") as mock_get:
            lightrag_extractor.extract_chunk_with_lightrag(_chunk("c1"))
            mock_chat.assert_called_once()
            mock_get.assert_not_called()


def _component_name(item: dict) -> str:
    return str(
        item.get("name")
        or item.get("component_name")
        or item.get("canonical_name")
        or ""
    ).strip()


class CompanyBrandExtractionTests(unittest.TestCase):
    def test_copyright_brand_owner_becomes_applicant_not_manufacturer(self) -> None:
        chunk = _chunk(
            "c1",
            text=(
                "Copyright © 2026 Contoso Inc. All rights reserved. "
                "Widget 16 technical specifications."
            ),
            source_file="widget-specs.pdf",
        )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity(
                        "Widget 16",
                        entity_type="product",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    )
                ],
                [],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks([chunk])

        self.assertEqual(result.get("companies") or [], [])
        self.assertEqual(len(result["applicants"]), 1)
        self.assertTrue(result["applicants"][0]["name"].lower().startswith("contoso"))
        self.assertTrue(
            any(
                rel["relationship"] == "has_product"
                and rel["source_type"] == "applicant"
                and rel["destination_node"] == "Widget 16"
                for rel in result["relationships"]
            )
        )
        self.assertFalse(
            any(rel["relationship"] == "party_role_manufacturer" for rel in result["relationships"])
        )

    def test_keeps_brand_owner_as_applicant_from_copyright(self) -> None:
        chunk = _chunk(
            "c1",
            text="Copyright © 2026 Contoso Inc. All rights reserved. Widget 16 specs.",
            source_file="widget.pdf",
        )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity(
                        "Contoso Inc.",
                        entity_type="applicant",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    ),
                    _entity(
                        "Widget 16",
                        entity_type="product",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    ),
                ],
                [
                    _relationship(
                        "UL Solutions",
                        "Contoso Inc.",
                        relationship="party_role_applicant",
                        source_type="ul",
                        target_type="applicant",
                        chunk_id=item["chunk_id"],
                    ),
                    _relationship(
                        "Contoso Inc.",
                        "Widget 16",
                        relationship="has_product",
                        source_type="applicant",
                        target_type="product",
                        chunk_id=item["chunk_id"],
                    ),
                ],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks([chunk])

        self.assertEqual(result["applicants"][0]["name"], "Contoso Inc.")
        self.assertEqual(result.get("companies") or [], [])
        self.assertTrue(
            any(
                rel["relationship"] == "party_role_applicant"
                and rel["destination_node"] == "Contoso Inc."
                for rel in result["relationships"]
            )
        )

    def test_keeps_applicant_when_text_supports_the_role(self) -> None:
        chunk = _chunk(
            "c1",
            text="Applicant: Globex Corp. submitted an application for listing of Widget 16.",
            source_file="application.pdf",
        )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity(
                        "Globex Corp.",
                        entity_type="applicant",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    ),
                    _entity(
                        "Widget 16",
                        entity_type="product",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    ),
                ],
                [
                    _relationship(
                        "Globex Corp.",
                        "Widget 16",
                        relationship="has_product",
                        source_type="applicant",
                        target_type="product",
                    )
                ],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks([chunk])

        self.assertEqual(result["applicants"][0]["name"], "Globex Corp.")
        self.assertFalse(
            any(c["name"] == "Globex Corp." for c in result["companies"])
        )

    def test_drops_manufacturer_without_manufacturing_language(self) -> None:
        chunk = _chunk(
            "c1",
            text="Copyright © 2026 Contoso Inc. All rights reserved. Widget 16 specs.",
            source_file="widget.pdf",
        )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity("Contoso Inc.", entity_type="company"),
                    _entity("Battery", entity_type="component"),
                ],
                [
                    _relationship(
                        "Battery",
                        "Contoso Inc.",
                        relationship="party_role_manufacturer",
                        source_type="component",
                        target_type="company",
                    )
                ],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks([chunk])

        self.assertFalse(
            any(rel["relationship"] == "party_role_manufacturer" for rel in result["relationships"])
        )

    def test_keeps_manufacturer_when_text_says_manufactured_by(self) -> None:
        chunk = _chunk(
            "c1",
            text="Battery Pack manufactured by Globex Cells.",
            source_file="bom.pdf",
        )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity("Globex Cells", entity_type="company"),
                    _entity("Battery Pack", entity_type="component"),
                ],
                [
                    _relationship(
                        "Battery Pack",
                        "Globex Cells",
                        relationship="party_role_manufacturer",
                        source_type="component",
                        target_type="company",
                    )
                ],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks([chunk])

        self.assertTrue(
            any(
                rel["relationship"] == "party_role_manufacturer"
                and rel["destination_node"] == "Globex Cells"
                for rel in result["relationships"]
            )
        )

    def test_merges_company_variants_across_files(self) -> None:
        chunks = [
            _chunk(
                "c1",
                text="Copyright © 2026 Contoso. All rights reserved. Widget 16.",
                source_file="a.pdf",
            ),
            _chunk(
                "c2",
                text="Copyright © 2026 Contoso Inc. All rights reserved. Widget 16.",
                source_file="b.pdf",
            ),
            _chunk(
                "c3",
                text="Copyright © 2026 CONTOSO INC. All rights reserved. Widget 16.",
                source_file="c.pdf",
            ),
        ]

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            return (
                [
                    _entity(
                        "Widget 16",
                        entity_type="product",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    )
                ],
                [],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks(chunks)

        self.assertEqual(len(result["applicants"]), 1)
        self.assertEqual(result.get("companies") or [], [])
        self.assertIn("inc", result["applicants"][0]["name"].lower())

    @unittest.skipUnless(
        all(
            Path(path).exists()
            for path in (
                r"C:\Users\aparna\Downloads\ul-documents\iPhone 16 - Te.pdf",
                r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-1.pdf",
                r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-2.pdf",
            )
        ),
        "iPhone PDFs not present",
    )
    def test_iphone_pdfs_extract_apple_as_applicant_not_manufacturer(self) -> None:
        import fitz

        paths = [
            Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16 - Te.pdf"),
            Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-1.pdf"),
            Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-2.pdf"),
        ]
        chunks: list[dict] = []
        for path in paths:
            doc = fitz.open(path)
            for index, page in enumerate(doc, start=1):
                chunks.append(
                    _chunk(
                        f"{path.stem}-{index}",
                        text=page.get_text("text") or "",
                        source_file=path.name,
                    )
                )

        def fake_extract(item: dict) -> tuple[list[dict], list[dict]]:
            text = str(item.get("text") or "")
            if "iPhone 16" not in text:
                return [], []
            return (
                [
                    _entity(
                        "iPhone 16",
                        entity_type="product",
                        source_file=item["source_file"],
                        chunk_id=item["chunk_id"],
                    )
                ],
                [],
            )

        with patch.object(
            lightrag_extractor, "extract_chunk_with_lightrag", side_effect=fake_extract
        ):
            result = lightrag_extractor.extract_graph_from_chunks(chunks)

        company_names = [c["name"] for c in result["applicants"]]
        self.assertEqual(len(company_names), 1)
        self.assertEqual(
            lightrag_extractor._company_core_key(company_names[0]),
            "apple",
        )
        self.assertEqual(result.get("companies") or [], [])
        self.assertTrue(
            any(
                rel["relationship"] == "has_product"
                and rel["source_type"] == "applicant"
                and rel["destination_node"] == "iPhone 16"
                for rel in result["relationships"]
            )
        )
        self.assertFalse(
            any(rel["relationship"] == "party_role_manufacturer" for rel in result["relationships"])
        )


if __name__ == "__main__":
    unittest.main()
