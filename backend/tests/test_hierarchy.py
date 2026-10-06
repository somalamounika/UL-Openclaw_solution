"""Tests for the prompt.py relationship whitelist and hierarchy refinement."""

from __future__ import annotations

import unittest

from ul.hierarchy import enforce_hierarchy


class HierarchyModelTests(unittest.TestCase):
    def test_prefers_model_contains_over_duplicate_product_component(self) -> None:
        triplets = [
            {
                "source_type": "product",
                "source_node": "Laptop Family",
                "relationship": "has_model",
                "destination_type": "model",
                "destination_node": "Laptop Family Pro 2025",
            },
            {
                "source_type": "model",
                "source_node": "Laptop Family Pro 2025",
                "relationship": "has_component",
                "destination_type": "component",
                "destination_node": "Battery",
            },
            {
                "source_type": "product",
                "source_node": "Laptop Family",
                "relationship": "has_component",
                "destination_type": "component",
                "destination_node": "Battery",
            },
        ]

        normalized = enforce_hierarchy(triplets)

        model_edges = [
            t
            for t in normalized
            if t["relationship"] == "contains"
            and t["source_type"] == "model"
            and t["destination_node"] == "Battery"
        ]
        product_edges = [
            t
            for t in normalized
            if t["source_type"] == "product" and t["destination_node"] == "Battery"
        ]
        self.assertEqual(len(model_edges), 1)
        self.assertEqual(len(product_edges), 0)

    def test_retypes_product_source_that_matches_known_model(self) -> None:
        triplets = [
            {
                "source_type": "product",
                "source_node": "Laptop Family",
                "relationship": "has_model",
                "destination_type": "model",
                "destination_node": "Laptop Family Pro 2025",
            },
            {
                "source_type": "product",
                "source_node": "Laptop Family Pro 2025",
                "relationship": "has_component",
                "destination_type": "component",
                "destination_node": "Battery",
            },
        ]

        normalized = enforce_hierarchy(triplets)

        model_edges = [
            t
            for t in normalized
            if t["relationship"] == "contains" and t["source_type"] == "model"
        ]
        self.assertEqual(len(model_edges), 1)
        self.assertEqual(model_edges[0]["source_node"], "Laptop Family Pro 2025")

    def test_does_not_invent_ul_company_links(self) -> None:
        triplets = [
            {
                "source_type": "applicant",
                "source_node": "Acme Corp",
                "relationship": "has_product",
                "destination_type": "product",
                "destination_node": "Widget",
            },
            {
                "source_type": "company",
                "source_node": "Regulator Agency",
                "relationship": "has_location",
                "destination_type": "location",
                "destination_node": "Geneva Site",
            },
        ]
        normalized = enforce_hierarchy(
            triplets, extra_companies=["Acme Corp", "Regulator Agency"]
        )
        serves = [t for t in normalized if t["relationship"] == "serves"]
        self.assertEqual(serves, [])
        self.assertFalse(
            any(t["relationship"] == "has_company" for t in normalized)
        )
        self.assertTrue(
            any(
                t["relationship"] == "party_role_applicant"
                and t["source_type"] == "ul"
                and t["destination_node"] == "Acme Corp"
                for t in normalized
            )
        )
        self.assertFalse(
            any(
                t["relationship"] == "party_role_applicant"
                and t["destination_node"] == "Regulator Agency"
                for t in normalized
            )
        )

    def test_aliases_manufactured_by_to_party_role_manufacturer(self) -> None:
        triplets = [
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "manufactured_by",
                "destination_type": "company",
                "destination_node": "Acme Cells",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        edge = next(t for t in normalized if t["relationship"] == "party_role_manufacturer")
        self.assertEqual(edge["source_node"], "Battery Pack")
        self.assertEqual(edge["source_type"], "component")
        self.assertEqual(edge["destination_node"], "Acme Cells")
        self.assertEqual(edge["destination_type"], "company")
        self.assertFalse(
            any(t["relationship"] == "party_role_applicant" for t in normalized)
        )

    def test_rewrites_component_supplier_to_company_applicant(self) -> None:
        triplets = [
            {
                "source_type": "applicant",
                "source_node": "Contoso Inc.",
                "relationship": "has_product",
                "destination_type": "product",
                "destination_node": "Widget",
            },
            {
                "source_type": "product",
                "source_node": "Widget",
                "relationship": "has_model",
                "destination_type": "model",
                "destination_node": "Widget Model",
            },
            {
                "source_type": "model",
                "source_node": "Widget Model",
                "relationship": "contains",
                "destination_type": "component",
                "destination_node": "Battery Pack",
            },
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "supplied_by",
                "destination_type": "company",
                "destination_node": "Acme Cells",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        edge = next(t for t in normalized if t["relationship"] == "party_role_supplier")
        self.assertEqual(edge["source_type"], "company")
        self.assertEqual(edge["source_node"], "Acme Cells")
        self.assertEqual(edge["destination_type"], "applicant")
        self.assertEqual(edge["destination_node"], "Contoso Inc.")
        self.assertFalse(
            any(
                t["relationship"] == "party_role_supplier" and t["source_type"] == "component"
                for t in normalized
            )
        )

    def test_keeps_component_party_role_manufacturer(self) -> None:
        triplets = [
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "manufactured_by",
                "destination_type": "company",
                "destination_node": "Acme Cells",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        edge = next(t for t in normalized if t["relationship"] == "party_role_manufacturer")
        self.assertEqual(edge["source_type"], "component")
        self.assertEqual(edge["destination_type"], "company")
        self.assertEqual(edge["source_node"], "Battery Pack")
        self.assertEqual(edge["destination_node"], "Acme Cells")

    def test_manufacturer_company_supplies_product_applicant(self) -> None:
        triplets = [
            {
                "source_type": "applicant",
                "source_node": "Contoso Inc.",
                "relationship": "has_product",
                "destination_type": "product",
                "destination_node": "Widget",
            },
            {
                "source_type": "product",
                "source_node": "Widget",
                "relationship": "has_model",
                "destination_type": "model",
                "destination_node": "Widget Model",
            },
            {
                "source_type": "model",
                "source_node": "Widget Model",
                "relationship": "contains",
                "destination_type": "component",
                "destination_node": "Battery Pack",
            },
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "manufactured_by",
                "destination_type": "company",
                "destination_node": "Acme Cells",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        manufacturer = next(t for t in normalized if t["relationship"] == "party_role_manufacturer")
        self.assertEqual(manufacturer["source_type"], "component")
        self.assertEqual(manufacturer["destination_node"], "Acme Cells")
        supplier = next(t for t in normalized if t["relationship"] == "party_role_supplier")
        self.assertEqual(supplier["source_type"], "company")
        self.assertEqual(supplier["source_node"], "Acme Cells")
        self.assertEqual(supplier["destination_type"], "applicant")
        self.assertEqual(supplier["destination_node"], "Contoso Inc.")
        self.assertFalse(
            any(
                t["relationship"] == "party_role_supplier" and t["source_type"] == "component"
                for t in normalized
            )
        )

    def test_drops_component_supplier_without_applicant(self) -> None:
        triplets = [
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "supplied_by",
                "destination_type": "company",
                "destination_node": "Acme Cells",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertFalse(any(t["relationship"] == "party_role_supplier" for t in normalized))

    def test_drops_company_to_product_supplies(self) -> None:
        triplets = [
            {
                "source_type": "company",
                "source_node": "Acme Cells",
                "relationship": "supplies",
                "destination_type": "product",
                "destination_node": "Widget",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertEqual(normalized, [])

    def test_aliases_subject_to_without_aliasing_serves(self) -> None:
        triplets = [
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "subject_to",
                "destination_type": "standard",
                "destination_node": "IEC 62368-1",
            },
            {
                "source_type": "ul",
                "source_node": "UL Solutions",
                "relationship": "serves",
                "destination_type": "company",
                "destination_node": "Acme Corp",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertTrue(any(t["relationship"] == "complies_with" for t in normalized))
        self.assertFalse(any(t["relationship"] == "serves" for t in normalized))
        self.assertFalse(any(t["relationship"] == "has_company" for t in normalized))

    def test_does_not_invent_company_product_supplies_from_roles(self) -> None:
        triplets = [
            {
                "source_type": "company",
                "source_node": "Acme Cells",
                "relationship": "has_party_role",
                "destination_type": "party_role",
                "destination_node": "Supplier",
            },
            {
                "source_type": "product",
                "source_node": "Unrelated Product",
                "relationship": "has_component",
                "destination_type": "component",
                "destination_node": "Gasket",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertFalse(
            any(
                t["relationship"] == "supplies" and t["source_node"] == "Acme Cells"
                for t in normalized
            )
        )
        self.assertFalse(
            any(t["source_type"] == "product" and t["destination_type"] == "component" for t in normalized)
        )

    def test_rejects_unsupported_type_pair(self) -> None:
        triplets = [
            {
                "source_type": "standard",
                "source_node": "IEC 62368-1",
                "relationship": "requires_test",
                "destination_type": "test",
                "destination_node": "TEST-001",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertFalse(any(t["relationship"] == "requires_test" for t in normalized))

    def test_keeps_certification_has_volume(self) -> None:
        triplets = [
            {
                "source_type": "certification",
                "source_node": "UL Certification",
                "relationship": "has_volume",
                "destination_type": "volume",
                "destination_node": "Volume 1",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["relationship"], "has_volume")
        self.assertEqual(normalized[0]["source_type"], "certification")

    def test_manufacturing_location_becomes_location(self) -> None:
        triplets = [
            {
                "source_type": "company",
                "source_node": "Apex Electronics",
                "relationship": "has_manufacturing_location",
                "destination_type": "manufacturing_location",
                "destination_node": "Yen Phong Plant",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["relationship"], "has_location")
        self.assertEqual(normalized[0]["destination_type"], "location")
        self.assertEqual(normalized[0]["destination_node"], "Yen Phong Plant")

    def test_keeps_certification_test_plan_and_test_record(self) -> None:
        triplets = [
            {
                "source_type": "certification",
                "source_node": "DVT1 certification",
                "relationship": "has_test_plan",
                "destination_type": "test_plan",
                "destination_node": "TP-A56R-REG-04 Rev B",
            },
            {
                "source_type": "certification",
                "source_node": "DVT1 certification",
                "relationship": "has_test_record",
                "destination_type": "test_record",
                "destination_node": "TR-A56R-PRE-EMC-017",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        rels = {(t["relationship"], t["destination_type"], t["destination_node"]) for t in normalized}
        self.assertIn(("has_test_plan", "test_plan", "TP-A56R-REG-04 Rev B"), rels)
        self.assertIn(("requires_test", "test", "TR-A56R-PRE-EMC-017"), rels)

    def test_company_has_product_gets_ul_applicant(self) -> None:
        triplets = [
            {
                "source_type": "company",
                "source_node": "Contoso Inc.",
                "relationship": "has_product",
                "destination_type": "product",
                "destination_node": "Widget 16",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertTrue(
            any(
                t["relationship"] == "has_product"
                and t["source_node"] == "Contoso Inc."
                and t["destination_node"] == "Widget 16"
                for t in normalized
            )
        )
        self.assertTrue(
            any(
                t["relationship"] == "party_role_applicant"
                and t["source_type"] == "ul"
                and t["destination_node"] == "Contoso Inc."
                for t in normalized
            )
        )
        self.assertFalse(
            any(
                t["relationship"] == "party_role_manufacturer"
                for t in normalized
            )
        )

    def test_links_standard_to_certification_via_shared_component(self) -> None:
        triplets = [
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "complies_with",
                "destination_type": "standard",
                "destination_node": "IEC 62133-2",
            },
            {
                "source_type": "component",
                "source_node": "Battery Pack",
                "relationship": "has_certification",
                "destination_type": "certification",
                "destination_node": "DVT1 certification",
            },
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertTrue(
            any(
                t["relationship"] == "has_certification"
                and t["source_type"] == "standard"
                and t["source_node"] == "IEC 62133-2"
                and t["destination_type"] == "certification"
                and t["destination_node"] == "DVT1 certification"
                for t in normalized
            )
        )

    def test_does_not_rewrite_company_product_supplies_to_has_product(self) -> None:
        triplets = [
            {
                "source_type": "company",
                "source_node": "Acme Cells",
                "relationship": "supplies",
                "destination_type": "product",
                "destination_node": "Widget",
            }
        ]
        normalized = enforce_hierarchy(triplets)
        self.assertEqual(normalized, [])


if __name__ == "__main__":
    unittest.main()
