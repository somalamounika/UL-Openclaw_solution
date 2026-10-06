"""Overlay LightRAG extraction prompts so company detection is independent of roles.

Applicant and Manufacturer remain role-only. This module does not hardcode
any brand or applicant name.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_COMPANY_ROLE_OVERRIDE = """
====================================================
COMPANY VS ROLE (MANDATORY)
====================================================

Company/Brand detection is INDEPENDENT of Applicant and Manufacturer roles.

Extract a Company when the current input names an organization as a legal entity,
brand owner, publisher, or product brand. Explicit role labels are NOT required.
Sufficient evidence includes:
- a copyright / "all rights reserved" line naming the organization
- "testing conducted by <organization>"
- "<organization> products"
- "<organization> Support" as the publisher of the page
- a branded product name that includes the organization as a prefix
  (emit the organization as Company AND the product as Product)

Normalize legal-form variants of the same organization to one Company
(for example "<Name>", "<Name> Inc.", "<NAME> INC."). Do not create duplicate
Company nodes for those variants. Do not create a separate Company for
"<Name> Support" when that phrase is only the support-site publisher; use
the organization name.

Do NOT emit Component -> PARTY_ROLE_MANUFACTURER -> Company unless the current
input explicitly or semantically supports manufacturing
(for example "manufactured by <Name>" or "manufacturer: <Name>").

When a Company is the brand/owner of a Product (copyright, testing, branding,
or Company -> HAS_PRODUCT -> Product), that organization is the UL Applicant
for those products. Emit:
Company -> HAS_PRODUCT -> Product
and treat that Company as Applicant so the graph can include
UL -> PARTY_ROLE_APPLICANT -> Applicant.
This is brand/owner association, not a manufacturer role.

Do not make component manufacturers or suppliers into Applicants.
Those companies stay Company-only:
Component -> PARTY_ROLE_MANUFACTURER -> Company
Company -> PARTY_ROLE_SUPPLIER -> Applicant

Applicant remains valid when the applicant role is named explicitly:
Applicant -> HAS_PRODUCT -> Product
UL -> PARTY_ROLE_APPLICANT -> Applicant

Do not type the same organization as both Applicant and Company.
If the organization owns the product, keep it as Applicant.
Manufacturer/supplier-only organizations stay Company.

Do NOT extract industry/category words alone as Company or Applicant
(for example "Electronic", "Electronics", "Technology", "Solutions").
Those are descriptors, not organizations. Prefer the full legal/brand name.

Model must be a model/SKU/version identifier (for example A3287, SM-A568B/DS),
not a second copy of the Product marketing name. When distinct model IDs exist,
do not also emit the Product name as a Model. Never hang components off a
Product-named Model when real model IDs are present.
""".strip()

_NEW_COMPANY_SECTION = """Company:
Company is an organization or brand named in the current input. Detecting a
Company does NOT require Applicant, Manufacturer, Supplier, or Brand labels.

Extract Company from copyright lines, "testing conducted by", "<name> products",
publisher phrases such as "<name> Support", and branded product names.

Company is independent of manufacturer/supplier roles:
- Applicant is the UL party role for the brand/owner of the product.
- A Company that owns or brands a Product is the Applicant for that Product.
- Manufacturer and Supplier are relationship roles of Company, not entity types.
- Do not emit PARTY_ROLE_MANUFACTURER from copyright or branding alone.
- Do not make a component manufacturer or supplier into the Applicant.

The Applicant must NOT be duplicated as Company when it is the product brand/owner.
Component manufacturer/supplier organizations stay Company only.

The same Company may receive PARTY_ROLE_MANUFACTURER (from a Component) and
PARTY_ROLE_SUPPLIER (to the Applicant) when the source explicitly supports both
roles. Do not duplicate the Company.

Allowed Company relationships:
Company -> HAS_PRODUCT -> Product
Component -> PARTY_ROLE_MANUFACTURER -> Company
Company -> PARTY_ROLE_SUPPLIER -> Applicant
Company -> HAS_LOCATION -> Location
Never connect Company directly to UL or Model.
""".strip()

_CERTIFICATION_DETAIL_OVERRIDE = """
====================================================
CERTIFICATION AND ADDRESS DETAILS (MANDATORY)
====================================================

When the current input names certification program details, extract them as
typed nodes and keep them attached to Certification:

Standard -> HAS_CERTIFICATION -> Certification
Certification -> REQUIRES_TEST -> Test
Certification -> HAS_TEST_PLAN -> TestPlan
Certification -> HAS_FILE_NUMBER -> FileNumber
Certification -> HAS_VOLUME -> Volume
Certification -> HAS_DELIVERABLE -> Deliverable
Location -> HAS_ADDRESS -> Address

Extract these even when the chunk is already dense with companies and products.
Do not leave file numbers, volumes, test plans, tests, deliverables,
or street addresses only in descriptions.
""".strip()

_DESCRIPTION_STYLE_RULE = """11. **Entity and relationship descriptions**
   - Keep every `description` short, plain, and source-grounded.
   - Entity descriptions: one brief phrase stating what the entity is (type or role).
     Use at most 15 words. Do not copy long passages from the input.
   - Relationship descriptions: one brief phrase stating the link between source and target.
     Use at most 12 words. Examples: "Samsung is the Applicant.",
     "The model contains the battery component."
   - Do not write paragraphs, quoted evidence, regulatory excerpts, or bullet lists.
   - Do not fabricate source_file, page_number, page_range, chunk_id, extraction_date,
     or document_version. The backend attaches chunk provenance separately.
   - Do not create Document, DocumentSection, Source, page, file-path, or chunk nodes for
     ingestion provenance."""

_DESCRIPTION_STYLE_MARKER = "Entity and relationship descriptions"

_APPLICANT_HAS_PRODUCT_TRIPLE = "Applicant -> HAS_PRODUCT -> Product"
_COMPANY_HAS_PRODUCT_TRIPLE = "Company -> HAS_PRODUCT -> Product"
_JSON_RULE_APPLIED_MARKER = "Company/Brand detection does not require explicit Applicant"
_COMPANY_SECTION_APPLIED_MARKER = "Detecting a\nCompany does NOT require Applicant"


def _replace_between(
    text: str,
    *,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> tuple[str, bool]:
    """Replace the inclusive start..exclusive end span when both markers exist."""
    start = text.find(start_marker)
    if start < 0:
        return text, False
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        return text, False
    return text[:start] + replacement + text[end:], True


def overlay_entity_types_guidance(guidance: str) -> str:
    """Rewrite Company semantics so brand detection does not require a role label."""
    text = guidance or ""
    if _COMPANY_SECTION_APPLIED_MARKER not in text:
        text, replaced = _replace_between(
            text,
            start_marker="Company:\nCompany is used for companies that are NOT the Applicant",
            end_marker="\n\nManufacturingLocation:",
            replacement=_NEW_COMPANY_SECTION,
        )
        if not replaced:
            # Tolerate single-newline heading variants from older prompt packs.
            text, replaced = _replace_between(
                text,
                start_marker="Company:\nCompany is used for companies that are NOT the Applicant",
                end_marker="\nManufacturingLocation:",
                replacement=_NEW_COMPANY_SECTION,
            )
        if not replaced:
            logger.warning(
                "Could not replace Company section in entity-type guidance; appending override"
            )

    if _COMPANY_HAS_PRODUCT_TRIPLE not in text:
        text = text.replace(
            _APPLICANT_HAS_PRODUCT_TRIPLE,
            f"{_APPLICANT_HAS_PRODUCT_TRIPLE}\n{_COMPANY_HAS_PRODUCT_TRIPLE}",
            1,
        )

    if "COMPANY VS ROLE (MANDATORY)" not in text:
        text = f"{text.rstrip()}\n\n{_COMPANY_ROLE_OVERRIDE}\n"
    if "CERTIFICATION AND ADDRESS DETAILS (MANDATORY)" not in text:
        text = f"{text.rstrip()}\n\n{_CERTIFICATION_DETAIL_OVERRIDE}\n"
    return text


_NEW_APPLICANT_COMPANY_JSON_RULE = """5. **Applicant and Company rule**
   - Company/Brand detection does not require explicit Applicant, Manufacturer, or Brand labels.
   - Extract Company from copyright, testing-conducted-by, branded products, and
     publisher phrases such as \"<name> Support\".
   - The brand/owner of a Product is the UL Applicant for that Product. Copyright,
     branding, or Company -> HAS_PRODUCT is enough. Do not require the word Applicant.
   - The ONLY UL relationship is UL -> PARTY_ROLE_APPLICANT -> Applicant for that
     brand/owner. Do not link component manufacturers or suppliers to UL.
   - Do not create multiple Applicant nodes for the same company.
   - Do not create Applicant nodes for suppliers, manufacturers, component companies,
     or certification bodies.
   - Do not additionally create a Company node for the same organization merely because
     it is the Applicant.
   - Manufacturer/Supplier are relationship roles, not entity types. Do not emit
     PARTY_ROLE_MANUFACTURER from copyright or branding alone.
   - Allowed Company relationships: Company -> HAS_PRODUCT -> Product,
     Component -> PARTY_ROLE_MANUFACTURER -> Company,
     Company -> PARTY_ROLE_SUPPLIER -> Applicant,
     Company -> HAS_LOCATION -> Location.
   - Never connect Company directly to UL or Model.
   - Do not extract industry/category words alone as Company or Applicant
     (for example \"Electronic\", \"Electronics\", \"Technology\").
   - Model must be a model/SKU/version identifier, not a duplicate of the
     Product marketing name when distinct model IDs are present.
   - Extract Address only for an explicitly stated physical/postal address and connect it
     Location -> HAS_ADDRESS when the address belongs to that site.
   - Keep Address separate from Location when both are present."""


def overlay_json_system_prompt(system_prompt: str) -> str:
    """Loosen Applicant/Company instruction 5 without dropping role rules."""
    text = system_prompt or ""
    if _JSON_RULE_APPLIED_MARKER not in text:
        # Replace by section markers so LightRAG wording drift (e.g. ManufacturingLocation /
        # Address bullets) does not force an append-only fallback every chunk.
        text, replaced = _replace_between(
            text,
            start_marker="5. **Applicant and Company rule**",
            end_marker="\n\n6. **Certification structure rule**",
            replacement=_NEW_APPLICANT_COMPANY_JSON_RULE,
        )
        if not replaced:
            # Fallback: normalize whitespace and try a compact match on older packs.
            compact = re.sub(r"[ \t]+\n", "\n", text)
            if "5. **Applicant and Company rule**" in compact:
                compact, replaced = _replace_between(
                    compact,
                    start_marker="5. **Applicant and Company rule**",
                    end_marker="\n\n6. **Certification structure rule**",
                    replacement=_NEW_APPLICANT_COMPANY_JSON_RULE,
                )
                if replaced:
                    text = compact
        if not replaced:
            logger.warning(
                "Could not replace Applicant/Company JSON rule; appending override"
            )
            text = f"{text.rstrip()}\n\n{_COMPANY_ROLE_OVERRIDE}\n"

    if _COMPANY_HAS_PRODUCT_TRIPLE not in text:
        text = text.replace(
            _APPLICANT_HAS_PRODUCT_TRIPLE,
            f"{_APPLICANT_HAS_PRODUCT_TRIPLE}\n     {_COMPANY_HAS_PRODUCT_TRIPLE}",
            1,
        )
    if "CERTIFICATION AND ADDRESS DETAILS (MANDATORY)" not in text:
        text = f"{text.rstrip()}\n\n{_CERTIFICATION_DETAIL_OVERRIDE}\n"
    if _DESCRIPTION_STYLE_MARKER not in text:
        text, replaced = _replace_between(
            text,
            start_marker="11. **Relationship description**",
            end_marker="\n\n12. **Entity naming and identity**",
            replacement=_DESCRIPTION_STYLE_RULE,
        )
        if not replaced:
            text = f"{text.rstrip()}\n\n{_DESCRIPTION_STYLE_RULE}\n"
    return text
