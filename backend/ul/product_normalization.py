"""Generic product identity, alias normalization, and deduplication."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ul.llm_client import chat_json

logger = logging.getLogger(__name__)

PRODUCT_ALIAS_SYSTEM_PROMPT = """You group product names that refer to the SAME physical product or model.

You receive product mention records with name, evidence, source_file, and page_range.

Rules:
- Use ONLY the supplied mention records. Do not use outside knowledge.
- Merge names only when the evidence/context indicates they designate the same target product.
- Keep DISTINCT products separate when the source identifies separate models, variants, sizes, generations, or product lines.
- Do NOT merge merely because one name is a substring of another.
- Do NOT merge "Product A" with "Product A Plus/Pro/Ultra/Max" unless evidence says they are the same product.
- Do NOT create canonical names that concatenate multiple distinct products (no "A and B", "A / B", "A + B").
- If a mention appears to list multiple distinct products, put each in a separate group.
- Prefer the most authoritative product designation as canonical_name:
  official product title, document title, product identification heading, model designation.
- Do not always choose the shortest name.
- When uncertain, keep names in separate singleton groups. False merging is worse than duplicates.
- Return ONLY JSON:
{"groups":[{"canonical_name":"","aliases":["..."],"evidence":""}]}
- Every input name must appear in exactly one group's aliases list.
- aliases must include the canonical_name."""


def norm_key(name: str) -> str:
    text = re.sub(r"[\s_\-]+", " ", (name or "").strip().lower())
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def product_identity_key(canonical_name: str) -> str:
    """Stable Neo4j merge key for a canonical product."""
    return f"product:{norm_key(canonical_name)}"


def model_identity_key(canonical_name: str) -> str:
    """Stable Neo4j merge key for a canonical model."""
    return f"model:{norm_key(canonical_name)}"


def split_compound_product_name(name: str) -> list[str]:
    """Split generic compound product strings into separate candidate names."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        return []
    for pattern in (r"\s+and\s+", r"\s*/\s*", r"\s+\+\s+"):
        parts = re.split(pattern, cleaned, flags=re.IGNORECASE)
        if len(parts) > 1:
            candidates = [part.strip(" \"'") for part in parts if part.strip(" \"'")]
            if len(candidates) > 1 and all(len(part) >= 2 for part in candidates):
                return candidates
    return [cleaned]


def parse_target_products(raw: str | None) -> list[str]:
    """Split a target-product field into distinct product names.

    Commas inside parentheses are kept, so names like
    ``MacBook Air (13-inch, M4, 2025)`` stay as one product.
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        return []

    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            part = "".join(buf).strip(" \"'")
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip(" \"'")
    if tail:
        parts.append(tail)

    split_parts: list[str] = []
    for part in parts:
        split_parts.extend(split_compound_product_name(part))

    seen: set[str] = set()
    unique: list[str] = []
    for name in split_parts:
        cleaned = name.strip(" \"'")
        key = norm_key(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def _source_meta(item: dict) -> dict:
    return {
        "source_file": item.get("source_file"),
        "page_range": item.get("page_range"),
        "chunk_id": item.get("chunk_id"),
    }


def _mention_key(mention: dict) -> tuple[str, str, str]:
    page = mention.get("page_range") or {}
    return (
        str(mention.get("name") or ""),
        str(mention.get("source_file") or ""),
        f"{page.get('start')}-{page.get('end')}",
    )


def collect_product_mentions(state: dict) -> list[dict]:
    """Gather raw product mentions from products, components, and relationships."""
    mentions: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(name: str, *, evidence: str = "", source: dict | None = None) -> None:
        for part in split_compound_product_name(name):
            mention = {
                "name": part,
                "evidence": evidence,
                **(source or {}),
            }
            if "source_file" not in mention:
                mention.update(_source_meta(mention))
            key = _mention_key(mention)
            if key in seen:
                continue
            seen.add(key)
            mentions.append(mention)

    for product in state.get("products") or []:
        name = str(product.get("name") or product.get("original_name") or "").strip()
        if not name:
            continue
        _add(
            name,
            evidence=str(product.get("evidence") or "").strip(),
            source=_source_meta(product),
        )
        for alias in product.get("aliases") or []:
            alias_name = str(alias or "").strip()
            if alias_name:
                _add(alias_name, evidence=str(product.get("evidence") or ""), source=_source_meta(product))

    for component in state.get("components") or []:
        product_name = str(component.get("product_name") or "").strip()
        if not product_name:
            continue
        _add(
            product_name,
            evidence=str(component.get("evidence") or "").strip(),
            source=_source_meta(component),
        )

    return mentions


def _parse_json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return data


def _llm_product_groups(mentions: list[dict]) -> list[dict]:
    payload = [
        {
            "name": m.get("name"),
            "evidence": m.get("evidence"),
            "source_file": m.get("source_file"),
            "page_range": m.get("page_range"),
        }
        for m in mentions
    ]
    try:
        raw = chat_json(
            PRODUCT_ALIAS_SYSTEM_PROMPT,
            "Product mentions:\n"
            + json.dumps(payload, ensure_ascii=False)
            + '\nReturn JSON {"groups":[{"canonical_name":"","aliases":[],"evidence":""}]}',
        )
        data = _parse_json_object(raw)
    except Exception as exc:
        logger.warning("Product alias LLM grouping failed: %s", exc)
        return []

    groups: list[dict] = []
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        canonical = str(group.get("canonical_name") or "").strip()
        aliases = group.get("aliases") or []
        if not canonical or not isinstance(aliases, list):
            continue
        cleaned_aliases = sorted(
            {
                str(alias or "").strip()
                for alias in aliases
                if str(alias or "").strip()
            }
            | {canonical}
        )
        if not cleaned_aliases:
            continue
        if len(cleaned_aliases) > 1 and any(
            sep in canonical.lower() for sep in (" and ", " / ", " + ")
        ):
            logger.info("Skipping compound canonical product name: %s", canonical)
            continue
        groups.append(
            {
                "canonical_name": canonical,
                "aliases": cleaned_aliases,
                "evidence": str(group.get("evidence") or "").strip(),
            }
        )
    return groups


def _singleton_groups(mentions: list[dict]) -> list[dict]:
    groups: list[dict] = []
    seen: set[str] = set()
    for mention in mentions:
        name = str(mention.get("name") or "").strip()
        key = norm_key(name)
        if not name or key in seen:
            continue
        seen.add(key)
        groups.append(
            {
                "canonical_name": name,
                "aliases": [name],
                "evidence": str(mention.get("evidence") or "").strip(),
            }
        )
    return groups


def _merge_product_sources(product: dict, mention: dict) -> None:
    sources = product.setdefault("sources", [])
    source = _source_meta(mention)
    if source.get("source_file") or source.get("chunk_id"):
        if source not in sources:
            sources.append(source)
    evidence = str(mention.get("evidence") or "").strip()
    if evidence and evidence not in product.get("evidence_list", []):
        product.setdefault("evidence_list", []).append(evidence)
        if not product.get("evidence"):
            product["evidence"] = evidence


def build_alias_map(products: list[dict]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for product in products:
        canonical = str(product.get("name") or product.get("canonical_name") or "").strip()
        if not canonical:
            continue
        for alias in [canonical, *(product.get("aliases") or [])]:
            alias_s = str(alias or "").strip()
            if alias_s:
                alias_map[alias_s] = canonical
                alias_map[norm_key(alias_s)] = canonical
    return alias_map


def _tokens(name: str) -> list[str]:
    return [token for token in norm_key(name).split() if token]


def contains_product_tokens(name: str, target: str) -> bool:
    """True when target tokens appear consecutively in name (or vice versa)."""
    name_tokens = _tokens(name)
    target_tokens = _tokens(target)
    if not name_tokens or not target_tokens:
        return False
    if name_tokens == target_tokens:
        return True
    haystack, needle = name_tokens, target_tokens
    if len(needle) > len(haystack):
        haystack, needle = needle, haystack
    length = len(needle)
    return any(haystack[i : i + length] == needle for i in range(len(haystack) - length + 1))


def resolve_product_name(name: str, state: dict) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return cleaned
    alias_map: dict[str, str] = state.get("product_alias_map") or {}
    if cleaned in alias_map:
        return alias_map[cleaned]
    key = norm_key(cleaned)
    if key in alias_map:
        return alias_map[key]
    return cleaned


def resolve_target_product(target: str, state: dict) -> str | None:
    target = str(target or "").strip()
    if not target:
        return None
    alias_map: dict[str, str] = state.get("product_alias_map") or {}
    if target in alias_map:
        return alias_map[target]
    tk = norm_key(target)
    for alias, canonical in alias_map.items():
        if norm_key(alias) == tk:
            return canonical
    for product in state.get("products") or []:
        canonical = str(product.get("name") or product.get("canonical_name") or "").strip()
        if norm_key(canonical) == tk:
            return canonical
        for alias in product.get("aliases") or []:
            if norm_key(str(alias)) == tk:
                return canonical

    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for product in state.get("products") or []:
        canonical = str(product.get("name") or product.get("canonical_name") or "").strip()
        if not canonical:
            continue
        names = [canonical, *(product.get("aliases") or [])]
        source_stem = str(product.get("source_file") or "").rsplit(".", 1)[0].replace("_", " ")
        if source_stem:
            names.append(source_stem)
        if not any(contains_product_tokens(str(name), target) for name in names if name):
            continue
        key = norm_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        extra = abs(len(_tokens(canonical)) - len(_tokens(target)))
        candidates.append((extra, canonical))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], len(item[1])))
    return candidates[0][1]


def product_matches_target(name: str, target: str, state: dict) -> bool:
    resolved_name = resolve_product_name(name, state)
    resolved_target = resolve_target_product(target, state)
    if resolved_name and resolved_target:
        if norm_key(resolved_name) == norm_key(resolved_target):
            return True
        # Exact target identity exists as a different product — do not steal variants.
        exact_target = False
        for product in state.get("products") or []:
            product_name = str(product.get("name") or "").strip()
            aliases = [product_name, *(product.get("aliases") or [])]
            if any(norm_key(str(alias)) == norm_key(target) for alias in aliases if alias):
                exact_target = True
                break
        if exact_target:
            return False
        return contains_product_tokens(resolved_name, resolved_target)
    return contains_product_tokens(name, target)


def product_matches_any_target(name: str, targets: list[str], state: dict) -> bool:
    """True when name matches any of the requested target products."""
    return any(product_matches_target(name, target, state) for target in targets if target)


def rewrite_relationships_for_products(state: dict, alias_map: dict[str, str]) -> None:
    """Point product endpoints in relationships to canonical product names."""
    for rel in state.get("relationships") or []:
        rel_type = str(rel.get("relationship") or "").upper()
        source_type = str(rel.get("source_type") or "").lower()
        target_type = str(rel.get("target_type") or "").lower()

        if source_type == "product":
            source = _rel_source(rel)
            canonical = alias_map.get(source) or alias_map.get(norm_key(source), source)
            if canonical and canonical != source:
                rel["source"] = canonical
                rel["source_node"] = canonical
                rel["relationship_description"] = rel.get("relationship_description")

        if target_type == "product":
            target = _rel_target(rel)
            canonical = alias_map.get(target) or alias_map.get(norm_key(target), target)
            if canonical and canonical != target:
                rel["target"] = canonical
                rel["target_node"] = canonical
                if rel.get("entity_name") in (None, "", target):
                    rel["entity_name"] = canonical

        if rel_type == "HAS_COMPONENT" and source_type == "product":
            rel["relationship_description"] = rel.get("relationship_description")


def _rel_source(rel: dict) -> str:
    return str(rel.get("source_node") or rel.get("source") or "")


def _rel_target(rel: dict) -> str:
    return str(rel.get("target_node") or rel.get("target") or "")


def dedupe_produces_relationships(state: dict) -> None:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for rel in state.get("relationships") or []:
        if str(rel.get("relationship") or "").upper() != "PRODUCES":
            deduped.append(rel)
            continue
        key = (norm_key(_rel_source(rel)), norm_key(_rel_target(rel)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rel)
    state["relationships"] = deduped


def validate_products(products: list[dict]) -> list[dict]:
    """Keep products with a name and at least one source or evidence reference."""
    validated: list[dict] = []
    for product in products:
        name = str(product.get("name") or product.get("canonical_name") or "").strip()
        if not name:
            continue
        has_source = bool(product.get("source_file") or (product.get("sources") or []))
        has_evidence = bool(product.get("evidence") or (product.get("evidence_list") or []))
        if not has_source and not has_evidence:
            logger.info("Skipping product without source evidence: %s", name)
            continue
        validated.append(product)
    return validated


def apply_product_groups(state: dict, groups: list[dict], mentions: list[dict]) -> None:
    """Apply alias groups to pipeline state."""
    mention_by_name: dict[str, list[dict]] = {}
    for mention in mentions:
        name = str(mention.get("name") or "").strip()
        if name:
            mention_by_name.setdefault(norm_key(name), []).append(mention)

    products: list[dict] = []
    for group in groups:
        canonical = str(group.get("canonical_name") or "").strip()
        aliases = [str(a).strip() for a in (group.get("aliases") or []) if str(a).strip()]
        if not canonical:
            continue
        if not aliases:
            aliases = [canonical]
        product: dict[str, Any] = {
            "name": canonical,
            "canonical_name": canonical,
            "original_name": aliases[0] if aliases else canonical,
            "aliases": sorted(set(aliases)),
            "identity_key": product_identity_key(canonical),
            "evidence": str(group.get("evidence") or "").strip(),
            "sources": [],
            "evidence_list": [],
        }
        group_evidence = str(group.get("evidence") or "").strip()
        if group_evidence:
            product["evidence_list"].append(group_evidence)
        for alias in product["aliases"]:
            for mention in mention_by_name.get(norm_key(alias), []):
                _merge_product_sources(product, mention)
                if mention.get("source_file") and not product.get("source_file"):
                    product.update(_source_meta(mention))
        products.append(product)

    products = validate_products(products)
    alias_map = build_alias_map(products)
    state["products"] = products
    state["product_alias_map"] = alias_map

    for component in state.get("components") or []:
        product_name = str(component.get("product_name") or "").strip()
        if product_name:
            component["product_name"] = resolve_product_name(product_name, state)
            component["original_product_name"] = product_name

    rewrite_relationships_for_products(state, alias_map)
    dedupe_produces_relationships(state)

    logger.info(
        "Normalized to %d canonical product(s) from %d mention(s)",
        len(products),
        len(mentions),
    )


def _split_cross_file_groups(groups: list[dict], mentions: list[dict]) -> list[dict]:
    """Keep distinct products from different PDFs from being merged into one node."""
    files_by_name: dict[str, set[str]] = {}
    for mention in mentions:
        name = str(mention.get("name") or "").strip()
        source_file = str(mention.get("source_file") or "").strip()
        if name:
            files_by_name.setdefault(norm_key(name), set()).add(source_file)

    cleaned: list[dict] = []
    for group in groups:
        aliases = [str(alias).strip() for alias in (group.get("aliases") or []) if str(alias).strip()]
        canonical = str(group.get("canonical_name") or "").strip()
        if canonical and canonical not in aliases:
            aliases = [canonical, *aliases]
        files: set[str] = set()
        keys: set[str] = set()
        for alias in aliases:
            key = norm_key(alias)
            keys.add(key)
            files.update(files_by_name.get(key) or set())
        files.discard("")
        if len(files) > 1 and len(keys) > 1:
            logger.info(
                "Splitting product group that mixed source files: %s",
                aliases,
            )
            for alias in aliases:
                cleaned.append(
                    {
                        "canonical_name": alias,
                        "aliases": [alias],
                        "evidence": str(group.get("evidence") or "").strip(),
                    }
                )
            continue
        cleaned.append(group)
    return cleaned


def normalize_products(state: dict) -> None:
    """Group product aliases, choose canonical names, and rewrite relationships."""
    mentions = collect_product_mentions(state)
    if not mentions:
        state["product_alias_map"] = {}
        return

    groups: list[dict]
    if len(mentions) >= 2:
        groups = _llm_product_groups(mentions)
        groups = _split_cross_file_groups(groups, mentions)
    else:
        groups = []
    if not groups:
        groups = _singleton_groups(mentions)

    assigned: set[str] = set()
    for group in groups:
        for alias in group.get("aliases") or []:
            assigned.add(norm_key(str(alias)))
    for mention in mentions:
        name = str(mention.get("name") or "").strip()
        if name and norm_key(name) not in assigned:
            groups.append(
                {
                    "canonical_name": name,
                    "aliases": [name],
                    "evidence": str(mention.get("evidence") or "").strip(),
                }
            )

    apply_product_groups(state, groups, mentions)


def products_by_alias(state: dict) -> dict[str, dict]:
    """Map normalized alias keys and raw names to canonical product records."""
    lookup: dict[str, dict] = {}
    for product in state.get("products") or []:
        canonical = str(product.get("name") or "").strip()
        if not canonical:
            continue
        lookup[norm_key(canonical)] = product
        lookup[canonical] = product
        for alias in product.get("aliases") or []:
            alias_s = str(alias or "").strip()
            if alias_s:
                lookup[norm_key(alias_s)] = product
                lookup[alias_s] = product
    return lookup


def apply_product_identity_to_triplets(
    triplets: list[dict],
    products: list[dict],
    models: list[dict] | None = None,
) -> list[dict]:
    """Attach identity_key fields for Product and Model nodes before Neo4j ingest."""
    lookup = build_alias_map(products)
    identity_by_canonical = {
        str(p.get("name") or ""): str(p.get("identity_key") or product_identity_key(p["name"]))
        for p in products
        if p.get("name")
    }

    model_lookup = build_alias_map(models or [])
    model_identity_by_canonical = {
        str(m.get("name") or ""): str(m.get("identity_key") or model_identity_key(m["name"]))
        for m in models or []
        if m.get("name")
    }

    enriched: list[dict] = []
    for triplet in triplets:
        row = dict(triplet)
        src_type = str(row.get("source_type") or "").lower()
        dst_type = str(row.get("destination_type") or "").lower()
        src_name = str(row.get("source_node") or "")
        dst_name = str(row.get("destination_node") or "")

        if src_type == "product":
            canonical = lookup.get(src_name) or lookup.get(norm_key(src_name), src_name)
            row["source_node"] = canonical
            row["source_identity_key"] = identity_by_canonical.get(
                canonical, product_identity_key(canonical)
            )
        elif src_type == "model":
            canonical = model_lookup.get(src_name) or model_lookup.get(norm_key(src_name), src_name)
            row["source_node"] = canonical
            row["source_identity_key"] = model_identity_by_canonical.get(
                canonical, model_identity_key(canonical)
            )
        if dst_type == "product":
            canonical = lookup.get(dst_name) or lookup.get(norm_key(dst_name), dst_name)
            row["destination_node"] = canonical
            row["destination_identity_key"] = identity_by_canonical.get(
                canonical, product_identity_key(canonical)
            )
        elif dst_type == "model":
            canonical = model_lookup.get(dst_name) or model_lookup.get(norm_key(dst_name), dst_name)
            row["destination_node"] = canonical
            row["destination_identity_key"] = model_identity_by_canonical.get(
                canonical, model_identity_key(canonical)
            )
        enriched.append(row)
    return enriched
