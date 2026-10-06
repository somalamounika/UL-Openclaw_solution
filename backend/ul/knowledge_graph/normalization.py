"""Reusable name/identifier normalization for UL Knowledge Graph entities.

Normalization is conservative: it collapses case, punctuation, and common
legal/standard formatting differences without merging distinct identities.
"""

from __future__ import annotations

import re

from ul.hierarchy import is_ul_root_name

_LEGAL_SUFFIXES = (
    "incorporated",
    "corporation",
    "company",
    "limited",
    "gmbh",
    "ag",
    "inc",
    "llc",
    "ltd",
    "corp",
    "co",
    "plc",
    "lp",
    "sa",
    "sas",
    "bv",
    "nv",
    "oy",
    "ab",
    "kg",
)

_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]+(?:" + "|".join(_LEGAL_SUFFIXES) + r")\.?$",
    re.IGNORECASE,
)

# Trailing industry/org descriptors only. Never brand names.
# Used when one name is a prefix of the other (Acme vs Acme Electronics).
_GENERIC_ORG_TOKENS = frozenset(
    {
        "electronics",
        "electronic",
        "technologies",
        "technology",
        "tech",
        "international",
        "industries",
        "industrial",
        "industry",
        "systems",
        "system",
        "solutions",
        "solution",
        "group",
        "groups",
        "holdings",
        "holding",
        "manufacturing",
        "products",
        "product",
        "services",
        "service",
        "enterprises",
        "enterprise",
        "global",
        "worldwide",
        "software",
        "hardware",
        "communications",
        "communication",
        "devices",
        "device",
        "motors",
        "motor",
        "computers",
        "computer",
        "semiconductors",
        "semiconductor",
    }
)

# Single-token names that are too generic to absorb longer companies.
_WEAK_COMPANY_CORES = frozenset(
    {
        "general",
        "united",
        "american",
        "national",
        "international",
        "pacific",
        "atlantic",
        "global",
        "first",
        "standard",
        "new",
        "north",
        "south",
        "east",
        "west",
        "advanced",
        "universal",
        "digital",
        "smart",
        "micro",
        "mega",
        "great",
        "best",
        "super",
        "city",
        "state",
    }
)

_STANDARD_BODY_DIGITS_RE = re.compile(
    r"\b([a-z]{2,4})[\s\-]*(\d)",
    re.IGNORECASE,
)


def normalize_entity_name(name: str | None) -> str:
    """Lowercase, collapse whitespace/hyphens, strip most punctuation."""
    text = re.sub(r"[\s_\-]+", " ", (name or "").strip().lower())
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slug_identity(text: str | None) -> str:
    """Stable slug used inside canonical_id values."""
    normalized = normalize_entity_name(text)
    slug = re.sub(r"[^a-z0-9]+", "_", normalized)
    return slug.strip("_")


def normalize_company(name: str | None) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    if is_ul_root_name(text):
        return "ul solutions"
    trimmed = text
    for _ in range(3):
        next_trimmed = _LEGAL_SUFFIX_RE.sub("", trimmed).rstrip(" ,.")
        if next_trimmed == trimmed:
            break
        trimmed = next_trimmed
    return normalize_entity_name(trimmed)


def company_tokens(name: str | None) -> list[str]:
    """Normalized company tokens with legal suffixes already removed."""
    normalized = normalize_company(name)
    return [token for token in normalized.split() if token]


def company_names_equivalent(left: str | None, right: str | None) -> bool:
    """True when two names are the same org, including legal and generic-descriptor variants.

    Merges ``Acme`` / ``Acme Inc.`` / ``Acme Electronics Co., Ltd.``.
    Does not merge ``Acme Display`` with ``Acme Electronics``, or a weak
    single token such as ``United`` with ``United Technologies``.
    """
    left_tokens = company_tokens(left)
    right_tokens = company_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    shorter, longer = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    if longer[: len(shorter)] != shorter:
        return False
    extra = longer[len(shorter) :]
    if not extra or not all(token in _GENERIC_ORG_TOKENS for token in extra):
        return False
    if len(shorter) == 1 and shorter[0] in _WEAK_COMPANY_CORES:
        return False
    return True


def preferred_company_name(left: str | None, right: str | None) -> str:
    """Prefer the more complete legal/org form as the surviving display name."""
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text:
        return right_text
    if not right_text:
        return left_text

    def _rank(name: str) -> tuple[int, int, int]:
        stripped = normalize_entity_name(name)
        core = normalize_company(name)
        has_suffix = int(bool(core and stripped != core))
        extra = sum(1 for token in company_tokens(name) if token in _GENERIC_ORG_TOKENS)
        return (has_suffix, extra, len(name))

    return left_text if _rank(left_text) >= _rank(right_text) else right_text


def normalize_product(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_component(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_model(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_standard(name: str | None) -> str:
    """Collapse IEC 61010-1 / IEC 61010 1 / IEC61010-1 to the same key."""
    text = (name or "").strip()
    if not text:
        return ""
    text = _STANDARD_BODY_DIGITS_RE.sub(r"\1 \2", text)
    text = re.sub(r"[\s_\-]+", " ", text)
    return normalize_entity_name(text)


def normalize_clause(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_clause_number(name: str | None) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d+(?:\.\d+)+|\d+)", text)
    if match:
        return match.group(1)
    return normalize_entity_name(text)


def normalize_certification(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_test(name: str | None) -> str:
    return normalize_entity_name(name)


def normalize_requirement(name: str | None) -> str:
    return normalize_entity_name(name)


def is_ul_entity(name: str | None) -> bool:
    """True for UL Solutions including common legal-suffix variants."""
    text = (name or "").strip()
    if not text:
        return False
    if is_ul_root_name(text):
        return True
    return normalize_company(text) in {"ul", "ul solutions"}


def is_generic_org_name(name: str | None) -> bool:
    """True for industry/descriptor-only names that are not real organizations.

    Rejects standalone tokens such as ``Electronic`` / ``Electronics`` while
    keeping multi-token names like ``Samsung Electronics``.
    """
    tokens = company_tokens(name)
    if len(tokens) != 1:
        return False
    token = tokens[0]
    return token in _GENERIC_ORG_TOKENS or token in _WEAK_COMPANY_CORES


def entity_names_equivalent(left: str | None, right: str | None) -> bool:
    """True when two labels name the same entity via normalization or token suffix.

    Examples that merge:
    - ``Acme`` / ``Acme Electronics Co., Ltd.`` (company legal/descriptor variants)
    - ``Global commercial office`` / ``AEMS Global Commercial Office`` (suffix alias)

    Does not merge ``iPhone 16`` with ``iPhone 16 Plus`` (not a suffix match).
    Single-token short names are never absorbed via the suffix rule.
    """
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    left_norm = normalize_entity_name(left_text)
    right_norm = normalize_entity_name(right_text)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if company_names_equivalent(left_text, right_text):
        return True

    left_tokens = left_norm.split()
    right_tokens = right_norm.split()
    shorter, longer = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    if len(shorter) < 2 or len(shorter) == len(longer):
        return False
    # Contiguous suffix only: brand/site prefix on an otherwise identical label.
    return longer[-len(shorter) :] == shorter


def preferred_entity_name(left: str | None, right: str | None) -> str:
    """Prefer the more complete display name when two entity labels are equivalent."""
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    if company_names_equivalent(left_text, right_text):
        return preferred_company_name(left_text, right_text)

    def _rank(name: str) -> tuple[int, int]:
        tokens = normalize_entity_name(name).split()
        return (len(tokens), len(name))

    return left_text if _rank(left_text) >= _rank(right_text) else right_text


def normalize_for_type(entity_type: str, name: str | None) -> str:
    from ul.knowledge_graph.labels import canonical_entity_type

    kind = canonical_entity_type(entity_type)
    dispatch = {
        "ul": lambda value: "ul solutions" if is_ul_root_name(value or "") else normalize_entity_name(value),
        "company": normalize_company,
        "product": normalize_product,
        "model": normalize_model,
        "component": normalize_component,
        "standard": normalize_standard,
        "clause": normalize_clause,
        "certification": normalize_certification,
        "test": normalize_test,
        "requirement": normalize_requirement,
    }
    func = dispatch.get(kind, normalize_entity_name)
    return func(name)
