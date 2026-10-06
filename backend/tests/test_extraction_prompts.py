"""Extraction prompt overlays must match the installed LightRAG prompt pack."""

from __future__ import annotations

import unittest

from lightrag.prompt import PROMPTS

from ul.extraction_prompts import (
    overlay_entity_types_guidance,
    overlay_json_system_prompt,
)


class ExtractionPromptOverlayTests(unittest.TestCase):
    def test_json_system_prompt_replaces_applicant_company_rule(self) -> None:
        raw = str(PROMPTS["entity_extraction_json_system_prompt"])
        with self.assertNoLogs("ul.extraction_prompts", level="WARNING"):
            overlaid = overlay_json_system_prompt(raw)
        self.assertIn(
            "Company/Brand detection does not require explicit Applicant",
            overlaid,
        )
        self.assertIn("Company -> HAS_PRODUCT -> Product", overlaid)
        self.assertNotIn(
            "Company is only for non-applicant organizations such as manufacturers and suppliers.",
            overlaid,
        )

    def test_entity_types_guidance_replaces_company_section(self) -> None:
        raw = str(PROMPTS.get("default_entity_types_guidance") or "")
        self.assertTrue(raw)
        with self.assertNoLogs("ul.extraction_prompts", level="WARNING"):
            overlaid = overlay_entity_types_guidance(raw)
        self.assertIn("Detecting a\nCompany does NOT require Applicant", overlaid)
        self.assertIn("ManufacturingLocation:", overlaid)

    def test_json_system_prompt_replaces_description_rule(self) -> None:
        raw = str(PROMPTS["entity_extraction_json_system_prompt"])
        overlaid = overlay_json_system_prompt(raw)
        self.assertIn("Entity and relationship descriptions", overlaid)
        self.assertIn("Use at most 15 words", overlaid)
        self.assertNotIn("11. **Relationship description**", overlaid)


if __name__ == "__main__":
    unittest.main()
