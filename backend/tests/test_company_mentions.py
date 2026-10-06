"""Company/brand mention detection independent of Applicant/Manufacturer roles."""

from __future__ import annotations

from pathlib import Path
import unittest

import fitz

from ul.company_mentions import (
    extract_company_mentions,
    text_supports_applicant_role,
    text_supports_manufacturer_role,
)
from ul.knowledge_graph.normalization import normalize_company

_IPHONE_PDFS = [
    Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16 - Te.pdf"),
    Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-1.pdf"),
    Path(r"C:\Users\aparna\Downloads\ul-documents\iPhone 16-2.pdf"),
]


class CompanyMentionTests(unittest.TestCase):
    def test_copyright_and_testing_by_are_company_not_roles(self) -> None:
        text = (
            "Copyright © 2026 Contoso Inc. All rights reserved. "
            "Testing conducted by Contoso in August 2024 using Widget 16 units. "
            "Contoso products comply with the listed standards. "
            "Widget 16 - Tech Specs - Contoso Support (IN)"
        )
        mentions = extract_company_mentions(text)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(normalize_company(mentions[0].name), "contoso")
        self.assertTrue(mentions[0].name.lower().startswith("contoso"))
        self.assertIn("copyright", mentions[0].kinds)
        self.assertFalse(text_supports_applicant_role(text))
        self.assertFalse(text_supports_manufacturer_role(text))

    def test_legal_suffix_variants_collapse(self) -> None:
        text = (
            "Copyright © 2026 Contoso Inc. All rights reserved. "
            "Testing conducted by CONTOSO INC."
        )
        mentions = extract_company_mentions(text)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(normalize_company(mentions[0].name), "contoso")
        self.assertIn("inc", mentions[0].name.lower())

    def test_does_not_treat_support_heading_as_company(self) -> None:
        text = "USB-C Connector\n Support\n New Zealand. Support\n Contents"
        mentions = extract_company_mentions(text)
        names = {normalize_company(item.name) for item in mentions}
        self.assertNotIn("connector", names)
        self.assertNotIn("zealand", names)

    def test_applicant_and_manufacturer_cues_are_detected_separately(self) -> None:
        applicant_text = "Applicant: Globex Corp. submitted an application for listing."
        manufacturer_text = "Battery Pack manufactured by Globex Cells."
        self.assertTrue(text_supports_applicant_role(applicant_text))
        self.assertTrue(text_supports_manufacturer_role(manufacturer_text))
        self.assertFalse(text_supports_applicant_role(manufacturer_text))
        self.assertFalse(text_supports_manufacturer_role(applicant_text))

    def test_does_not_extract_ul_root_from_copyright(self) -> None:
        text = "Copyright © 2026 UL Solutions. All rights reserved."
        mentions = extract_company_mentions(text)
        self.assertEqual(mentions, [])


@unittest.skipUnless(all(path.exists() for path in _IPHONE_PDFS), "iPhone PDFs not present")
class IPhonePdfCompanyMentionTests(unittest.TestCase):
    def test_iphone_pdfs_yield_one_apple_company_without_roles(self) -> None:
        names: set[str] = set()
        kinds: set[str] = set()
        combined = []
        for path in _IPHONE_PDFS:
            doc = fitz.open(path)
            text = "\n".join(page.get_text("text") or "" for page in doc)
            combined.append(text)
            self.assertFalse(text_supports_applicant_role(text), path.name)
            self.assertFalse(text_supports_manufacturer_role(text), path.name)
            for mention in extract_company_mentions(text, source_file=path.name):
                names.add(normalize_company(mention.name))
                kinds.update(mention.kinds)
        self.assertEqual(names, {"apple"})
        self.assertIn("copyright", kinds)
        self.assertTrue({"testing_by", "products", "support"} & kinds)
        self.assertFalse(text_supports_applicant_role("\n".join(combined)))
        self.assertFalse(text_supports_manufacturer_role("\n".join(combined)))
