"""Incremental UL Knowledge Graph: canonical identity, resolution, and provenance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ul.knowledge_graph.canonical_ids import UL_CANONICAL_ID, generate_canonical_id
from ul.knowledge_graph.entity_resolution import ExtractedEntity, resolve_entity
from ul.knowledge_graph.ingest import GLOBAL_GRAPH_ID, MemoryGraph, collapse_equivalent_entities, ingest_to_store
from ul.knowledge_graph.normalization import (
    company_names_equivalent,
    is_ul_entity,
    normalize_company,
    normalize_component,
    normalize_standard,
    preferred_company_name,
)
from ul.knowledge_graph.provenance import compute_content_hash, make_evidence_id


def _triplet(src_type, src, rel, dst_type, dst, **extra) -> dict:
    row = {
        "source_type": src_type,
        "source_node": src,
        "relationship": rel,
        "destination_type": dst_type,
        "destination_node": dst,
    }
    row.update(extra)
    return row


def document_triplets(
    *,
    company: str,
    product: str,
    component: str,
    standard: str,
    model: str | None = None,
    description: str = "",
    chunk_id: str = "",
    page_number: int | None = None,
) -> list[dict]:
    model_name = model or f"{product} Model"
    meta = {}
    if description:
        meta["description"] = description
    if chunk_id:
        meta["chunk_id"] = chunk_id
    if page_number is not None:
        meta["page_number"] = page_number
    return [
        _triplet("ul", "UL Solutions", "party_role_applicant", "applicant", company, **meta),
        _triplet("applicant", company, "has_product", "product", product, **meta),
        _triplet("product", product, "has_model", "model", model_name, **meta),
        _triplet("model", model_name, "contains", "component", component, **meta),
        _triplet("component", component, "complies_with", "standard", standard, **meta),
    ]


class NormalizationTests(unittest.TestCase):
    def test_company_legal_suffix_and_case(self) -> None:
        self.assertEqual(normalize_company("UL Solutions, Inc."), "ul solutions")
        self.assertEqual(normalize_company("UL SOLUTIONS"), "ul solutions")
        self.assertTrue(is_ul_entity("UL Solutions, Inc."))
        self.assertTrue(is_ul_entity("UL"))

    def test_company_descriptor_variants_are_equivalent_without_brand_lists(self) -> None:
        self.assertTrue(
            company_names_equivalent("Contoso", "Contoso Electronics Co., Ltd.")
        )
        self.assertTrue(company_names_equivalent("Acme", "Acme Technologies"))
        self.assertEqual(
            preferred_company_name("Contoso", "Contoso Electronics Co., Ltd."),
            "Contoso Electronics Co., Ltd.",
        )
        self.assertFalse(
            company_names_equivalent("Contoso Electronics Co., Ltd.", "Contoso Display")
        )
        self.assertFalse(company_names_equivalent("United", "United Technologies"))
        self.assertFalse(company_names_equivalent("Acme", "Acme Support"))

    def test_standard_code_variants(self) -> None:
        self.assertEqual(normalize_standard("IEC 61010-1"), "iec 61010 1")
        self.assertEqual(normalize_standard("IEC 61010 1"), "iec 61010 1")
        self.assertEqual(normalize_standard("IEC61010-1"), "iec 61010 1")

    def test_component_casefold(self) -> None:
        self.assertEqual(normalize_component("Component X"), "component x")
        self.assertEqual(normalize_component("COMPONENT X"), "component x")

    def test_canonical_ids_are_stable_and_typed(self) -> None:
        self.assertEqual(
            generate_canonical_id("standard", "iec 61010 1"),
            "standard_iec_61010_1",
        )
        self.assertEqual(
            generate_canonical_id("company", "samsung"),
            "company_samsung",
        )
        self.assertEqual(UL_CANONICAL_ID, "ul_solutions")


class IncrementalIngestTests(unittest.TestCase):
    def test_first_document_creates_canonical_graph(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Company A",
                product="Product A",
                component="Component X",
                standard="Standard X",
            ),
            store,
            document_id="doc_a",
            content_hash="hash_a",
        )
        self.assertEqual(store.count("ul"), 1)
        self.assertEqual(store.count("company"), 1)
        self.assertEqual(store.count("product"), 1)
        self.assertEqual(store.count("component"), 1)
        self.assertEqual(store.count("standard"), 1)
        self.assertEqual(len(store.documents), 1)

    def test_second_document_shares_component_and_standard(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Company A",
                product="Product A",
                component="Component X",
                standard="Standard X",
            ),
            store,
            document_id="doc_a",
            content_hash="hash_a",
        )
        ingest_to_store(
            document_triplets(
                company="Company B",
                product="Product B",
                component="Component X",
                standard="Standard X",
            ),
            store,
            document_id="doc_b",
            content_hash="hash_b",
        )
        self.assertEqual(store.count("ul"), 1)
        self.assertEqual(store.count("company"), 2)
        self.assertEqual(store.count("product"), 2)
        self.assertEqual(store.count("component"), 1)
        self.assertEqual(store.count("standard"), 1)
        self.assertEqual(len(store.documents), 2)

    def test_same_document_uploaded_twice_is_idempotent(self) -> None:
        store = MemoryGraph()
        triplets = document_triplets(
            company="Company A",
            product="Product A",
            component="Component X",
            standard="Standard X",
            description="Once",
            chunk_id="chunk_000001",
            page_number=12,
        )
        kwargs = {
            "document_id": "doc_a",
            "content_hash": "same-bytes",
            "source_files": ["document_a.pdf"],
        }
        ingest_to_store(triplets, store, **kwargs)
        ingest_to_store(triplets, store, **kwargs)

        self.assertEqual(store.count("ul"), 1)
        self.assertEqual(store.count("company"), 1)
        self.assertEqual(store.count("product"), 1)
        self.assertEqual(store.count("component"), 1)
        self.assertEqual(store.count("standard"), 1)
        self.assertEqual(len(store.documents), 1)
        self.assertEqual(store.evidence, {})
        first_rel_count = len(store.relationships)
        ingest_to_store(triplets, store, **kwargs)
        self.assertEqual(len(store.relationships), first_rel_count)
        self.assertEqual(len(store.documents), 1)

    def test_entity_description_is_stored_on_nodes(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Apex Electronics",
                product="Widget",
                component="Adhesive die-cut kit",
                standard="IEC 62368-1",
                description="The approved AEMS scope includes production of the adhesive die-cut kit.",
            ),
            store,
            document_id="doc_desc",
            entity_descriptions={
                "company:apex electronics": "AEMS manufactures adhesive die-cut kits.",
            },
        )
        company = next(
            entity
            for entity in store.entities.values()
            if entity["entity_type"] == "company"
        )
        self.assertEqual(
            company.get("description"),
            "AEMS manufactures adhesive die-cut kits.",
        )
        component = next(
            entity
            for entity in store.entities.values()
            if entity["entity_type"] == "component"
        )
        # Falls back to relationship description when entity text is missing.
        self.assertIn("adhesive die-cut kit", str(component.get("description") or "").lower())

    def test_ingest_does_not_create_evidence_nodes(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Company A",
                product="Product A",
                component="Component X",
                standard="Standard X",
                description="Description A",
                chunk_id="chunk_45",
                page_number=12,
            ),
            store,
            document_id="doc_a",
            entity_descriptions={"component:component x": "Description A"},
        )
        ingest_to_store(
            document_triplets(
                company="Company B",
                product="Product B",
                component="Component X",
                standard="Standard X",
                description="Description B",
                chunk_id="chunk_18",
                page_number=7,
            ),
            store,
            document_id="doc_b",
            entity_descriptions={"component:component x": "Description B"},
        )

        self.assertEqual(store.count("component"), 1)
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.evidence_for("component_component_x"), [])

    def test_same_clause_number_under_different_standards(self) -> None:
        store = MemoryGraph()
        triplets = [
            _triplet("standard", "IEC 61010-1", "has_clause", "clause", "4.4.2.2"),
            _triplet("standard", "ISO XXXX", "has_clause", "clause", "4.4.2.2"),
        ]
        ingest_to_store(triplets, store, document_id="doc_clauses")
        self.assertEqual(store.count("standard"), 2)
        self.assertEqual(store.count("clause"), 2)
        clause_ids = {
            cid
            for (kind, cid) in store.entities
            if kind == "clause"
        }
        self.assertEqual(len(clause_ids), 2)

    def test_company_name_variants_share_one_applicant_and_children(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Contoso",
                product="Widget 16",
                component="Battery Pack",
                standard="IEC 62368-1",
            ),
            store,
            document_id="doc_short",
        )
        ingest_to_store(
            document_triplets(
                company="Contoso Electronics Co., Ltd.",
                product="Widget 16",
                component="Battery Pack",
                standard="IEC 62368-1",
            ),
            store,
            document_id="doc_legal",
        )
        self.assertEqual(store.count("company"), 1)
        self.assertEqual(store.count("product"), 1)
        self.assertEqual(store.count("model"), 1)
        self.assertEqual(store.count("component"), 1)
        company = next(entity for entity in store.entities.values() if entity["entity_type"] == "company")
        aliases = {normalize_company(alias) for alias in company["aliases"]}
        self.assertTrue("contoso" in aliases or normalize_company(company["name"]) in {"contoso", "contoso electronics"})
        self.assertEqual(
            len(
                [
                    rel
                    for rel in store.relationships.values()
                    if rel["relationship"] == "PARTY_ROLE_APPLICANT"
                ]
            ),
            1,
        )

    def test_collapse_merges_already_stored_company_variants_and_products(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Contoso",
                product="Widget 16",
                component="Battery Pack",
                standard="IEC 62368-1",
            ),
            store,
            document_id="doc_a",
        )
        store.merge_entity(
            "company",
            "company_contoso_electronics",
            name="Contoso Electronics Co., Ltd.",
            normalized_name="contoso electronics",
            aliases=["Contoso Electronics Co., Ltd."],
        )
        store.merge_entity(
            "product",
            "product_company_contoso_electronics_widget_16",
            name="Widget 16",
            normalized_name="widget 16",
            extra={"company_canonical_id": "company_contoso_electronics"},
        )
        store.merge_relationship(
            "ul",
            UL_CANONICAL_ID,
            "party_role_applicant",
            "company",
            "company_contoso_electronics",
        )
        store.merge_relationship(
            "company",
            "company_contoso_electronics",
            "has_product",
            "product",
            "product_company_contoso_electronics_widget_16",
        )
        self.assertEqual(store.count("company"), 2)
        self.assertEqual(store.count("product"), 2)
        collapse_equivalent_entities(store)
        self.assertEqual(store.count("company"), 1)
        self.assertEqual(store.count("product"), 1)

    def test_distinct_org_descriptors_are_not_collapsed(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            [
                _triplet("ul", "UL Solutions", "party_role_applicant", "applicant", "Contoso Electronics"),
                _triplet("ul", "UL Solutions", "party_role_applicant", "applicant", "Contoso Display"),
            ],
            store,
            document_id="doc_subsidiaries",
        )
        self.assertEqual(store.count("company"), 2)

    def test_component_supplier_edges_are_dropped_manufacturer_kept(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            [
                _triplet("ul", "UL Solutions", "party_role_applicant", "applicant", "Contoso"),
                _triplet("model", "Widget Model", "contains", "component", "Battery Pack"),
                _triplet(
                    "component",
                    "Battery Pack",
                    "party_role_manufacturer",
                    "company",
                    "Acme Cells",
                ),
            ],
            store,
            document_id="doc_roles",
        )
        store.merge_relationship(
            "component",
            next(
                entity["canonical_id"]
                for entity in store.entities.values()
                if entity["entity_type"] == "component"
            ),
            "party_role_supplier",
            "company",
            next(
                entity["canonical_id"]
                for entity in store.entities.values()
                if entity["entity_type"] == "company" and entity["name"] == "Acme Cells"
            ),
        )
        self.assertTrue(
            any(
                rel["relationship"] == "PARTY_ROLE_SUPPLIER" and rel["source_type"] == "component"
                for rel in store.relationships.values()
            )
        )
        collapse_equivalent_entities(store)
        self.assertFalse(
            any(
                rel["relationship"] == "PARTY_ROLE_SUPPLIER" and rel["source_type"] == "component"
                for rel in store.relationships.values()
            )
        )
        self.assertTrue(
            any(
                rel["relationship"] == "PARTY_ROLE_MANUFACTURER" and rel["source_type"] == "component"
                for rel in store.relationships.values()
            )
        )

    def test_manufacturer_is_supplier_of_product_applicant(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Contoso Inc.",
                product="Widget 16",
                component="Battery Pack",
                standard="IEC 62368-1",
            )
            + [
                _triplet(
                    "component",
                    "Battery Pack",
                    "party_role_manufacturer",
                    "company",
                    "Acme Cells",
                )
            ],
            store,
            document_id="doc_mfr_supplier",
        )
        supplier_edges = [
            rel
            for rel in store.relationships.values()
            if rel["relationship"] == "PARTY_ROLE_SUPPLIER"
        ]
        self.assertEqual(len(supplier_edges), 1)
        self.assertEqual(supplier_edges[0]["source_type"], "company")
        self.assertEqual(supplier_edges[0]["dest_type"], "company")
        names = {entity["canonical_id"]: entity["name"] for entity in store.entities.values()}
        self.assertEqual(names[supplier_edges[0]["source_canonical_id"]], "Acme Cells")
        self.assertEqual(names[supplier_edges[0]["dest_canonical_id"]], "Contoso Inc.")
        self.assertFalse(
            any(
                rel["relationship"] == "PARTY_ROLE_SUPPLIER" and rel["source_type"] == "component"
                for rel in store.relationships.values()
            )
        )

    def test_similar_but_different_components_are_not_merged(self) -> None:
        store = MemoryGraph()
        triplets = [
            _triplet("model", "Model A", "contains", "component", "Cooling Fan"),
            _triplet("model", "Model A", "contains", "component", "Cooling Fan Assembly"),
        ]
        ingest_to_store(triplets, store, document_id="doc_fans")
        self.assertEqual(store.count("component"), 2)

    def test_ul_aliases_resolve_to_one_canonical_node(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            [_triplet("ul", "UL Solutions, Inc.", "party_role_applicant", "applicant", "Acme")],
            store,
            document_id="doc_1",
        )
        ingest_to_store(
            [_triplet("ul", "UL", "party_role_applicant", "applicant", "Acme")],
            store,
            document_id="doc_2",
        )
        ingest_to_store(
            [_triplet("ul", "UL Solutions", "party_role_applicant", "applicant", "Acme")],
            store,
            document_id="doc_3",
        )
        self.assertEqual(store.count("ul"), 1)
        ul_node = store.entities[("ul", UL_CANONICAL_ID)]
        aliases = {item.lower() for item in ul_node["aliases"]}
        self.assertTrue({"ul solutions", "ul", "ul solutions, inc."} & aliases or "UL" in ul_node["aliases"])
        self.assertEqual(ul_node["canonical_id"], UL_CANONICAL_ID)

    def test_graph_id_is_stable_across_documents(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            document_triplets(
                company="Company A",
                product="Product A",
                component="Component X",
                standard="Standard X",
            ),
            store,
            document_id="doc_a",
            graph_id="kg_aaaa",
        )
        ingest_to_store(
            document_triplets(
                company="Company A",
                product="Product A",
                component="Component X",
                standard="Standard X",
            ),
            store,
            document_id="doc_b",
            graph_id="kg_bbbb",
        )
        self.assertEqual(store.count("company"), 1)
        company = next(entity for entity in store.entities.values() if entity["entity_type"] == "company")
        self.assertEqual(company["graph_id"], GLOBAL_GRAPH_ID)
        self.assertTrue(company["canonical_id"].startswith("company_"))
        ul_node = store.entities[("ul", UL_CANONICAL_ID)]
        self.assertEqual(ul_node["graph_id"], GLOBAL_GRAPH_ID)
        self.assertTrue(store.relationships)
        for rel in store.relationships.values():
            self.assertEqual(rel["graph_id"], GLOBAL_GRAPH_ID)

    def test_low_confidence_similar_names_are_not_merged(self) -> None:
        existing = [
            {
                "entity_type": "component",
                "canonical_id": "component_cooling_fan",
                "name": "Cooling Fan",
                "normalized_name": "cooling fan",
                "aliases": ["Cooling Fan"],
            }
        ]
        resolution = resolve_entity(
            ExtractedEntity(entity_type="component", name="Cooling Fan Assembly"),
            existing,
        )
        self.assertEqual(resolution.canonical_id, "component_cooling_fan_assembly")
        self.assertTrue(resolution.is_new)

    def test_content_hash_and_evidence_ids_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(b"%PDF-test")
            first = compute_content_hash([path])
            second = compute_content_hash([path])
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertEqual(
            make_evidence_id(
                document_id="doc_a",
                kind="entity",
                canonical_id="component_x",
                chunk_id="chunk_45",
                page_number=12,
            ),
            make_evidence_id(
                document_id="doc_a",
                kind="entity",
                canonical_id="component_x",
                chunk_id="chunk_45",
                page_number=12,
            ),
        )
        self.assertTrue(
            make_evidence_id(
                document_id="doc_a",
                kind="entity",
                canonical_id="component_x",
            ).startswith("ev_")
        )

    def test_certification_details_and_address_are_canonical(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            [
                _triplet("component", "Battery Pack", "complies_with", "standard", "IEC 62133-2"),
                _triplet(
                    "component",
                    "Battery Pack",
                    "has_certification",
                    "certification",
                    "DVT1 certification",
                ),
                _triplet(
                    "certification",
                    "DVT1 certification",
                    "has_file_number",
                    "file_number",
                    "E512847",
                ),
                _triplet(
                    "certification",
                    "DVT1 certification",
                    "has_test_plan",
                    "test_plan",
                    "TP-A56R-REG-04",
                ),
                _triplet(
                    "certification",
                    "DVT1 certification",
                    "requires_test",
                    "test",
                    "TR-A56R-PRE-EMC-017",
                ),
                _triplet(
                    "location",
                    "Global commercial office",
                    "has_address",
                    "address",
                    "26 Science Park Drive, Singapore",
                ),
            ],
            store,
            document_id="doc_cert",
            source_files=["email.eml"],
        )
        collapse_equivalent_entities(store)
        self.assertTrue(store.count("file_number") >= 1)
        self.assertTrue(store.count("test_plan") >= 1)
        self.assertTrue(store.count("test") >= 1)
        self.assertTrue(store.count("address") >= 1)
        self.assertTrue(
            any(
                rel.get("relationship") == "HAS_CERTIFICATION"
                and rel.get("source_type") == "standard"
                for rel in store.relationships.values()
            )
        )
        self.assertTrue(
            any(
                rel.get("relationship") == "HAS_ADDRESS"
                for rel in store.relationships.values()
            )
        )

    def test_manufacturing_location_is_stored_as_location(self) -> None:
        store = MemoryGraph()
        ingest_to_store(
            [
                _triplet(
                    "company",
                    "Apex Electronics",
                    "has_manufacturing_location",
                    "manufacturing_location",
                    "Yen Phong Plant",
                )
            ],
            store,
            document_id="doc_loc",
        )
        collapse_equivalent_entities(store)
        self.assertFalse(any(kind == "manufacturing_location" for kind, _cid in store.entities))
        self.assertEqual(store.count("location"), 1)
        self.assertTrue(
            any(
                rel.get("relationship") == "HAS_LOCATION"
                and rel.get("dest_type") == "location"
                for rel in store.relationships.values()
            )
        )
        self.assertFalse(
            any(
                rel.get("relationship") == "HAS_MANUFACTURING_LOCATION"
                for rel in store.relationships.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
