"""Tests for generic product normalization (no hardcoded product rules)."""

from __future__ import annotations

import unittest

from ul.product_normalization import (
    apply_product_groups,
    apply_product_identity_to_triplets,
    build_alias_map,
    collect_product_mentions,
    dedupe_produces_relationships,
    norm_key,
    parse_target_products,
    product_identity_key,
    product_matches_any_target,
    product_matches_target,
    resolve_target_product,
    split_compound_product_name,
)


def _make_state(
    *,
    products: list[dict] | None = None,
    components: list[dict] | None = None,
    relationships: list[dict] | None = None,
) -> dict:
    return {
        "products": list(products or []),
        "components": list(components or []),
        "relationships": list(relationships or []),
        "companies": [{"name": "Acme Corp", "source_file": "spec.pdf"}],
    }


class ProductNormalizationTests(unittest.TestCase):
    def test_distinct_products_remain_separate(self) -> None:
        state = _make_state(
            components=[
                {
                    "component_name": "Battery",
                    "product_name": "Product A",
                    "evidence": "Battery for Product A",
                    "source_file": "spec.pdf",
                },
                {
                    "component_name": "Battery",
                    "product_name": "Product B",
                    "evidence": "Battery for Product B",
                    "source_file": "spec.pdf",
                },
            ]
        )
        mentions = collect_product_mentions(state)
        groups = [
            {"canonical_name": "Product A", "aliases": ["Product A"], "evidence": "A"},
            {"canonical_name": "Product B", "aliases": ["Product B"], "evidence": "B"},
        ]
        apply_product_groups(state, groups, mentions)
        self.assertEqual(len(state["products"]), 2)

    def test_aliases_merge_to_one_product(self) -> None:
        state = _make_state(
            components=[
                {
                    "component_name": "Battery",
                    "product_name": "Product A (Model X, 2025)",
                    "evidence": "Official designation",
                    "source_file": "spec.pdf",
                },
                {
                    "component_name": "Display",
                    "product_name": "Model X Product A",
                    "evidence": "Same product in repair manual",
                    "source_file": "repair.pdf",
                },
                {
                    "component_name": "Keyboard",
                    "product_name": "Product A",
                    "evidence": "Family shorthand for same model",
                    "source_file": "safety.pdf",
                },
            ]
        )
        mentions = collect_product_mentions(state)
        groups = [
            {
                "canonical_name": "Product A (Model X, 2025)",
                "aliases": [
                    "Product A (Model X, 2025)",
                    "Model X Product A",
                    "Product A",
                ],
                "evidence": "Same target product across documents",
            }
        ]
        apply_product_groups(state, groups, mentions)
        self.assertEqual(len(state["products"]), 1)
        product = state["products"][0]
        self.assertEqual(product["name"], "Product A (Model X, 2025)")
        self.assertIn("Product A", product["aliases"])
        self.assertIn("Model X Product A", product["aliases"])

    def test_distinct_variant_not_merged_by_substring(self) -> None:
        state = _make_state(
            components=[
                {
                    "component_name": "Battery",
                    "product_name": "Product A",
                    "evidence": "Base model",
                    "source_file": "a.pdf",
                },
                {
                    "component_name": "Battery",
                    "product_name": "Product A Plus",
                    "evidence": "Separate Plus variant",
                    "source_file": "b.pdf",
                },
            ]
        )
        mentions = collect_product_mentions(state)
        groups = [
            {"canonical_name": "Product A", "aliases": ["Product A"], "evidence": "Base"},
            {
                "canonical_name": "Product A Plus",
                "aliases": ["Product A Plus"],
                "evidence": "Plus variant",
            },
        ]
        apply_product_groups(state, groups, mentions)
        self.assertEqual(len(state["products"]), 2)
        self.assertFalse(
            product_matches_target("Product A Plus", "Product A", state),
        )

    def test_shorthand_target_matches_specific_extracted_name(self) -> None:
        state = _make_state(
            products=[
                {
                    "name": "MacBook Air (13-inch, M4, 2025)",
                    "aliases": ["MacBook Air (13-inch, M4, 2025)"],
                    "source_file": "MacBook_Air_13-inch_M4_2025_only.pdf",
                },
                {
                    "name": "Samsung Galaxy S24",
                    "aliases": ["Samsung Galaxy S24"],
                    "source_file": "SG1.pdf",
                },
            ]
        )
        state["product_alias_map"] = build_alias_map(state["products"])
        targets = parse_target_products("MacBook Air, Galaxy S24")
        self.assertTrue(
            product_matches_any_target("MacBook Air (13-inch, M4, 2025)", targets, state)
        )
        self.assertTrue(product_matches_any_target("Samsung Galaxy S24", targets, state))
        self.assertEqual(resolve_target_product("MacBook Air", state), "MacBook Air (13-inch, M4, 2025)")
        self.assertEqual(resolve_target_product("Galaxy S24", state), "Samsung Galaxy S24")

    def test_same_product_across_multiple_pdfs(self) -> None:
        state = _make_state(
            components=[
                {
                    "component_name": "Battery",
                    "product_name": "Product A (Model X, 2025)",
                    "evidence": "Spec",
                    "source_file": "spec.pdf",
                    "page_range": {"start": 1, "end": 2},
                },
                {
                    "component_name": "Battery",
                    "product_name": "Product A",
                    "evidence": "Safety doc",
                    "source_file": "safety.pdf",
                    "page_range": {"start": 3, "end": 4},
                },
            ]
        )
        mentions = collect_product_mentions(state)
        groups = [
            {
                "canonical_name": "Product A (Model X, 2025)",
                "aliases": ["Product A (Model X, 2025)", "Product A"],
                "evidence": "Same product in multiple PDFs",
            }
        ]
        apply_product_groups(state, groups, mentions)
        self.assertEqual(len(state["products"]), 1)
        self.assertGreaterEqual(len(state["products"][0].get("sources") or []), 1)

    def test_single_produces_relationship_for_aliases(self) -> None:
        state = _make_state(
            products=[{"name": "Product A (Model X, 2025)", "source_file": "spec.pdf"}],
            relationships=[
                {
                    "source_node": "Acme Corp",
                    "target_node": "Product A (Model X, 2025)",
                    "relationship": "PRODUCES",
                    "source_type": "company",
                    "target_type": "product",
                    "evidence": "evidence",
                },
                {
                    "source_node": "Acme Corp",
                    "target_node": "Product A",
                    "relationship": "PRODUCES",
                    "source_type": "company",
                    "target_type": "product",
                    "evidence": "evidence",
                },
            ],
        )
        groups = [
            {
                "canonical_name": "Product A (Model X, 2025)",
                "aliases": ["Product A (Model X, 2025)", "Product A"],
                "evidence": "Same product",
            }
        ]
        apply_product_groups(state, groups, collect_product_mentions(state))
        dedupe_produces_relationships(state)
        produces = [
            rel
            for rel in state["relationships"]
            if rel.get("relationship") == "PRODUCES"
        ]
        self.assertEqual(len(produces), 1)
        self.assertEqual(produces[0]["target_node"], "Product A (Model X, 2025)")

    def test_target_product_resolves_via_alias(self) -> None:
        state = _make_state()
        groups = [
            {
                "canonical_name": "Product A (Model X, 2025)",
                "aliases": ["Product A (Model X, 2025)", "Product A"],
                "evidence": "Same product",
            }
        ]
        apply_product_groups(state, groups, [])
        resolved = resolve_target_product("Product A", state)
        self.assertEqual(resolved, "Product A (Model X, 2025)")
        self.assertTrue(product_matches_target("Product A (Model X, 2025)", "Product A", state))

    def test_has_component_points_to_canonical_product(self) -> None:
        state = _make_state(
            components=[
                {
                    "component_name": "Battery",
                    "product_name": "Product A",
                    "evidence": "Battery",
                    "source_file": "spec.pdf",
                }
            ],
            relationships=[
                {
                    "source_node": "Product A",
                    "target_node": "Battery",
                    "relationship": "HAS_COMPONENT",
                    "source_type": "product",
                    "target_type": "component",
                    "evidence": "Battery",
                }
            ],
        )
        groups = [
            {
                "canonical_name": "Product A (Model X, 2025)",
                "aliases": ["Product A (Model X, 2025)", "Product A"],
                "evidence": "Same product",
            }
        ]
        apply_product_groups(state, groups, collect_product_mentions(state))
        rel = state["relationships"][0]
        self.assertEqual(rel["source_node"], "Product A (Model X, 2025)")
        self.assertEqual(state["components"][0]["product_name"], "Product A (Model X, 2025)")

    def test_compound_product_name_splits_generically(self) -> None:
        parts = split_compound_product_name("Product A and Product B")
        self.assertEqual(parts, ["Product A", "Product B"])

    def test_parse_target_products_splits_commas_and_and(self) -> None:
        self.assertEqual(
            parse_target_products("iPhone 16, MacBook Air"),
            ["iPhone 16", "MacBook Air"],
        )
        self.assertEqual(
            parse_target_products("iPhone 16 and MacBook Air"),
            ["iPhone 16", "MacBook Air"],
        )
        self.assertEqual(
            parse_target_products("iPhone 16 / MacBook Air"),
            ["iPhone 16", "MacBook Air"],
        )

    def test_parse_target_products_keeps_commas_inside_parentheses(self) -> None:
        self.assertEqual(
            parse_target_products("MacBook Air (13-inch, M4, 2025)"),
            ["MacBook Air (13-inch, M4, 2025)"],
        )
        self.assertEqual(
            parse_target_products("iPhone 16, MacBook Air (13-inch, M4, 2025)"),
            ["iPhone 16", "MacBook Air (13-inch, M4, 2025)"],
        )

    def test_multiple_targets_match_distinct_products(self) -> None:
        state = _make_state()
        groups = [
            {"canonical_name": "iPhone 16", "aliases": ["iPhone 16"], "evidence": "phone"},
            {
                "canonical_name": "MacBook Air (13-inch, M4, 2025)",
                "aliases": ["MacBook Air (13-inch, M4, 2025)", "MacBook Air"],
                "evidence": "laptop",
            },
        ]
        apply_product_groups(state, groups, [])
        targets = parse_target_products("iPhone 16, MacBook Air")
        self.assertTrue(product_matches_any_target("iPhone 16", targets, state))
        self.assertTrue(
            product_matches_any_target("MacBook Air (13-inch, M4, 2025)", targets, state)
        )
        self.assertEqual(len(state["products"]), 2)

    def test_neo4j_identity_key_on_triplets(self) -> None:
        products = [
            {
                "name": "Product A (Model X, 2025)",
                "aliases": ["Product A"],
                "identity_key": product_identity_key("Product A (Model X, 2025)"),
            }
        ]
        triplets = apply_product_identity_to_triplets(
            [
                {
                    "source_type": "company",
                    "source_node": "Acme Corp",
                    "relationship": "PRODUCES",
                    "destination_type": "product",
                    "destination_node": "Product A",
                }
            ],
            products,
        )
        self.assertEqual(
            triplets[0]["destination_node"],
            "Product A (Model X, 2025)",
        )
        self.assertEqual(
            triplets[0]["destination_identity_key"],
            product_identity_key("Product A (Model X, 2025)"),
        )

    def test_build_alias_map_resolves_names(self) -> None:
        alias_map = build_alias_map(
            [
                {
                    "name": "Product A (Model X, 2025)",
                    "aliases": ["Product A", "Model X Product A"],
                }
            ]
        )
        self.assertEqual(alias_map["Product A"], "Product A (Model X, 2025)")


if __name__ == "__main__":
    unittest.main()
