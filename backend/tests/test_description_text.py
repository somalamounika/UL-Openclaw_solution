"""Tests for concise graph description normalization."""

from __future__ import annotations

import unittest

from ul.description_text import merge_description_field, normalize_description


class DescriptionTextTests(unittest.TestCase):
    def test_normalize_description_keeps_first_short_sentence(self) -> None:
        text = (
            "Samsung is the main client company for the UL project. "
            "The document also lists many unrelated compliance details."
        )
        self.assertEqual(
            normalize_description(text),
            "Samsung is the main client company for the UL project.",
        )

    def test_normalize_description_trims_long_passage(self) -> None:
        text = " ".join(["word"] * 30)
        normalized = normalize_description(text)
        self.assertLessEqual(len(normalized.split()), 18)

    def test_normalize_description_prefers_shortest_pipe_segment(self) -> None:
        text = (
            "Long copied regulatory excerpt about battery safety requirements "
            "and thermal runaway testing conditions | Battery component."
        )
        self.assertEqual(normalize_description(text), "Battery component.")

    def test_merge_description_field_prefers_shorter_text(self) -> None:
        existing = {
            "description": (
                "This component is a rechargeable lithium-ion battery pack "
                "used in the mobile phone model."
            )
        }
        incoming = {"description": "Battery pack in the model."}
        merge_description_field(existing, incoming, "description")
        self.assertEqual(existing["description"], "Battery pack in the model.")


if __name__ == "__main__":
    unittest.main()
