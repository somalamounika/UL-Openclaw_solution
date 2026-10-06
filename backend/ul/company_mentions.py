"""Detect company/brand mentions independently of Applicant/Manufacturer roles.

Role assignment stays elsewhere: these helpers only find organization names
from generic ownership/publisher cues such as copyright lines. They do not
hardcode any specific brand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ul.hierarchy import is_ul_root_name
from ul.knowledge_graph.normalization import is_generic_org_name, normalize_company

_LEGAL_SUFFIX_GROUP = (
    r"(?:Inc|Incorporated|Corp|Corporation|Co|Company|Ltd|Limited|"
    r"LLC|LLP|PLC|GmbH|AG|SA|SAS|BV|NV|Oy|AB|KK|Pty)\.?"
)

# Capture an organization heading a copyright line, stopping before the
# rights clause or a new sentence.
_COPYRIGHT_RE = re.compile(
    r"copyright\s*(?:©|\(c\)|&copy;)?\s*(?:\d{4}\s*[-–—]\s*)?(?:\d{4}[^\S\n]+)?"
    r"([A-Z][A-Za-z0-9&'’\-]+(?:[^\S\n]+[A-Z][A-Za-z0-9&'’\-]+|"
    rf"[^\S\n]+{_LEGAL_SUFFIX_GROUP}){{0,6}})"
    r"(?=[^\S\n]*\.|[^\S\n]+all rights reserved|\s*$)",
    re.IGNORECASE,
)

_TESTING_BY_RE = re.compile(
    r"testing conducted by[^\S\n]+"
    r"([A-Z][A-Za-z0-9&'’\-]+"
    rf"(?:[^\S\n]+{_LEGAL_SUFFIX_GROUP})?)",
    re.IGNORECASE,
)

_COMPANY_PRODUCTS_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’\-]+(?:[^\S\n]+" + _LEGAL_SUFFIX_GROUP + r")?)[^\S\n]+products\b"
)

# Publisher footers such as "Acme Support (IN)", not headings like "Connector Support".
_COMPANY_SUPPORT_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’\-]+)[^\S\n]+Support[^\S\n]*\("
)

_APPLICANT_ROLE_RE = re.compile(
    r"\b("
    r"applicants?|listee|certificate holder|"
    r"party[\s_-]*role[\s_-]*applicant|"
    r"filed(?:\s+an)?\s+application|"
    r"submitted(?:\s+an)?\s+application|"
    r"application\s+(?:for|to)\s+(?:ul|listing|certification)"
    r")\b",
    re.IGNORECASE,
)

_MANUFACTURER_ROLE_RE = re.compile(
    r"\b("
    r"manufactured\s+by|manufacturer(?:s)?(?:\s+of)?|"
    r"manufactures|manufactured\s+at"
    r")\b",
    re.IGNORECASE,
)

_STANDARD_NAME_RE = re.compile(
    r"^(?:ul|iec|iso|csa|en|ieee|ansi|un|astm)\s*\d",
    re.IGNORECASE,
)

_GENERIC_NAMES = frozenset(
    {
        "all",
        "this",
        "these",
        "the",
        "our",
        "its",
        "their",
        "documentation",
        "privacy",
        "terms",
        "support",
        "introduction",
        "contents",
        "copyright",
        "india",
        "united",
        "international",
        "institute",
        "standard",
        "safety",
    }
)

_TRAILING_JUNK_RE = re.compile(
    r"(?:\s+all rights reserved|\s+privacy policy|\s+terms of use).*$",
    re.IGNORECASE,
)


@dataclass
class CompanyMention:
    name: str
    evidence: str
    kind: str
    source_file: str = ""
    chunk_id: str = ""
    page_range: dict | None = None
    kinds: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


def text_supports_applicant_role(text: str | None) -> bool:
    return bool(_APPLICANT_ROLE_RE.search(text or ""))


def text_supports_manufacturer_role(text: str | None) -> bool:
    return bool(_MANUFACTURER_ROLE_RE.search(text or ""))


def clean_company_name(raw: str | None) -> str:
    text = _TRAILING_JUNK_RE.sub("", (raw or "").strip())
    text = re.sub(r"[\s]+", " ", text).strip(" ,.;:|-")
    text = re.sub(r"\s+and$", "", text, flags=re.IGNORECASE).strip(" ,.;:")
    return text


def is_plausible_company_name(name: str | None) -> bool:
    cleaned = clean_company_name(name)
    if not cleaned or len(cleaned) < 2:
        return False
    if is_ul_root_name(cleaned):
        return False
    if _STANDARD_NAME_RE.match(cleaned):
        return False
    if normalize_company(cleaned) in {"ul", "ul solutions"}:
        return False
    tokens = cleaned.split()
    if not tokens:
        return False
    if tokens[0].lower() in _GENERIC_NAMES and len(tokens) == 1:
        return False
    if cleaned.lower() in _GENERIC_NAMES:
        return False
    if is_generic_org_name(cleaned):
        return False
    return True


def extract_company_mentions(
    text: str | None,
    *,
    source_file: str = "",
    chunk_id: str = "",
    page_range: dict | None = None,
) -> list[CompanyMention]:
    """Return company/brand mentions from ownership-style cues in ``text``.

    Does not assign Applicant or Manufacturer roles.
    """
    body = text or ""
    if not body.strip():
        return []

    found: dict[str, CompanyMention] = {}

    def _remember(raw: str, evidence: str, kind: str) -> None:
        name = clean_company_name(raw)
        if not is_plausible_company_name(name):
            return
        key = normalize_company(name)
        if not key:
            return
        existing = found.get(key)
        if existing is None:
            found[key] = CompanyMention(
                name=name,
                evidence=evidence,
                kind=kind,
                kinds=[kind],
                source_file=source_file,
                chunk_id=chunk_id,
                page_range=page_range,
            )
            return
        existing.evidence = _join_evidence(existing.evidence, evidence)
        if kind not in existing.kinds:
            existing.kinds.append(kind)
        if _name_rank(name) > _name_rank(existing.name):
            existing.name = name

    for match in _COPYRIGHT_RE.finditer(body):
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        _remember(match.group(1), snippet, "copyright")

    for match in _TESTING_BY_RE.finditer(body):
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        _remember(match.group(1), snippet, "testing_by")

    for match in _COMPANY_PRODUCTS_RE.finditer(body):
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        _remember(match.group(1), snippet, "products")

    for match in _COMPANY_SUPPORT_RE.finditer(body):
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        _remember(match.group(1), snippet, "support")

    return list(found.values())


def _name_rank(name: str) -> tuple[int, int]:
    stripped = re.sub(r"[^\w\s]", "", (name or "").strip().lower())
    stripped = re.sub(r"\s+", " ", stripped).strip()
    core = normalize_company(name)
    has_suffix = bool(core and stripped != core)
    return (int(has_suffix), len((name or "").strip()))


def _join_evidence(prior: str, extra: str) -> str:
    extra = extra.strip()
    if not extra:
        return prior
    if extra in prior:
        return prior
    if not prior:
        return extra
    return f"{prior} | {extra}"
