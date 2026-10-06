"""Grounded chatbot using LightRAG native mix retrieval (KG + document chunks)."""
 
from __future__ import annotations

import logging
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
 
from fastapi import APIRouter, HTTPException
from lightrag import QueryParam
from lightrag.constants import GRAPH_FIELD_SEP
from pydantic import BaseModel, Field
 
from ul.lightrag_extractor import _aget_lightrag_instance, _run_on_lightrag_loop
from ul.llm_client import get_chat_client
from ul.knowledge_graph.ingest import GLOBAL_GRAPH_ID
 
logger = logging.getLogger(__name__)
 
router = APIRouter(prefix="/api/ul", tags=["UL Knowledge Graph Chat"])
 
_ENTITY_TYPES = {
    "ul",
    "company",
    "party_role",
    "project",
    "product_family",
    "product",
    "model",
    "variant",
    "component",
    "material",
    "standard",
    "clause",
    "file_number",
    "volume",
    "section",
    "certificate",
    "deliverable",
    "condition_of_acceptability",
    "certification",
    "test_plan",
    "test_record",
    "test",
    "service",
    "opportunity",
    "quote",
    "location",
    "address",
}
 
 
_INTENT_TERMS: dict[str, set[str]] = {
    "company": {"company", "companies", "manufacturer", "supplier", "brand"},
    "product": {"product", "products", "device", "devices"},
    "model": {"model", "models", "variant", "variants"},
    "component": {"component", "components", "part", "parts", "assembly"},
    "standard": {"standard", "standards", "regulation", "regulations"},
    "clause": {"clause", "clauses", "section", "sections", "requirement"},
    "certification": {"certification", "certifications", "certificate", "certified"},
    "test": {"test", "tests", "testing", "evaluation"},
    "relationships": {
        "relationship",
        "relationships",
        "associated",
        "connected",
        "contain",
        "contains",
        "produces",
        "applies",
        "between",
    },
    "compliance": {
        "compliance",
        "compliant",
        "requirement",
        "requirements",
        "required",
        "applicable",
    },
}
_STOP_WORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "associated",
    "at",
    "be",
    "between",
    "by",
    "can",
    "contain",
    "contains",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "information",
    "is",
    "it",
    "me",
    "of",
    "on",
    "present",
    "show",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "why",
    "with",
}
_TYPE_WORDS = set().union(*_INTENT_TERMS.values())
_MAX_GRAPH_ITEMS = 60
_MAX_DOCUMENT_CHUNKS = 8
_MAX_CHUNK_CHARS = 1800
_MAX_GRAPH_DESCRIPTION_CHARS = 400
_FOREIGN_PRODUCT_MARKERS = (
    "iphone",
    "macbook",
    "ipad",
    "pixel",
    "a3240",
    "apple",
    "galaxy",
    "a56r",
    "samsung",
)
_INTENT_CHUNK_TERMS: dict[str, tuple[str, ...]] = {
    "clause": ("clause", "subsection", "38.3", "section"),
    "standard": ("iec", "iso", "ul ", "standard", "en "),
    "certification": ("certif", "csa", "fcc", "ce mark", "file number"),
    "test": ("test", "evaluation", "ipx", "ingress"),
    "component": ("component", "part number", "assembly"),
}
# Cap Neo4j lineage edges shown in View Lineage.
_MAX_LINEAGE_RELATIONSHIPS = 64


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    graph_id: str | None = None
    document_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]
    graph_evidence: dict[str, list[dict[str, Any]]]
    # Paste into Neo4j Browser to confirm it yields the rendered lineage.
    generated_cypher: str = ""
    query_parameters: dict[str, Any] = Field(default_factory=dict)
 
 
@dataclass(frozen=True)
class QueryUnderstanding:
    intents: tuple[str, ...]
    entity_types: tuple[str, ...]
    keywords: tuple[str, ...]
    likely_entities: tuple[str, ...]
 
 
def understand_query(question: str) -> QueryUnderstanding:
    """Extract retrieval hints without making query classification an LLM dependency."""
    lowered = question.lower()
    raw_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", question)
    normalized_tokens = [token.lower().strip("./-_") for token in raw_tokens]
 
    intents = [
        intent
        for intent, terms in _INTENT_TERMS.items()
        if any(token in terms for token in normalized_tokens)
    ]
    entity_types = [intent for intent in intents if intent in _ENTITY_TYPES]
 
    quoted = [
        value.strip()
        for value in re.findall(r"""["']([^"']{2,120})["']""", question)
        if value.strip()
    ]
    capitalized_phrases = [
        match.strip()
        for match in re.findall(
            r"\b(?:[A-Z][A-Za-z0-9._/-]*)(?:\s+[A-Z0-9][A-Za-z0-9._/-]*)*",
            question,
        )
        if match.lower() not in {"what", "which", "show", "why", "how"}
    ]
    likely_entities = _dedupe_strings([*quoted, *capitalized_phrases])[:8]
 
    keywords: list[str] = []
    for raw, token in zip(raw_tokens, normalized_tokens, strict=True):
        if (
            len(token) >= 2
            and token not in _STOP_WORDS
            and token not in _TYPE_WORDS
            and not token.isdigit()
        ):
            keywords.append(raw)
    keywords = _dedupe_strings([*likely_entities, *keywords])[:16]
 
    if not intents:
        intents = ["general_document_information"]
    return QueryUnderstanding(
        intents=tuple(intents),
        entity_types=tuple(entity_types),
        keywords=tuple(keywords),
        likely_entities=tuple(likely_entities),
    )
 
 
def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
 
 
def _dedupe_graph_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        if item.get("kind") == "relationship":
            key = (
                "relationship",
                item["source"]["type"].casefold(),
                item["source"]["name"].casefold(),
                item["relationship"].casefold(),
                item["target"]["type"].casefold(),
                item["target"]["name"].casefold(),
            )
        else:
            key = (
                "entity",
                str(item.get("type") or "").casefold(),
                str(item.get("name") or "").casefold(),
            )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _intent_answer_type_keys(question: str) -> set[str]:
    """Ontology/type keys the question is asking about (for LightRAG selection)."""
    understanding = understand_query(question)
    focus: set[str] = set()
    for intent in understanding.intents:
        if intent == "clause":
            focus.update({"clause", "section", "standard", "requirement"})
        elif intent == "standard":
            focus.update({"standard", "certification", "test", "clause"})
        elif intent == "certification":
            focus.update({"certification", "certificate", "standard", "test"})
        elif intent == "test":
            focus.update({"test", "testplan", "standard", "certification"})
        elif intent == "component":
            focus.update({"component", "material", "model", "product"})
        elif intent == "company":
            focus.update({"company", "product", "model"})
        elif intent == "product":
            focus.update({"product", "model", "component"})
        elif intent == "model":
            focus.update({"model", "product", "component", "standard"})
        elif intent == "compliance":
            focus.update({"standard", "certification", "clause", "test", "component"})
    focus.update({"product", "model", "component"})
    return focus


def _name_looks_like_intent_target(name: str, intents: tuple[str, ...]) -> bool:
    """Heuristic when LightRAG leaves entity types as generic Entity."""
    lowered = _name_key(name)
    if not lowered:
        return False
    intent_set = set(intents)
    if "clause" in intent_set and any(
        marker in lowered
        for marker in ("clause", "subsection", "section ", "part iii", "part ii", "38.3")
    ):
        return True
    if "standard" in intent_set and (
        lowered.startswith("iec")
        or lowered.startswith("iso")
        or lowered.startswith("ul ")
        or "un manual" in lowered
        or "standard" in lowered
    ):
        return True
    if "certification" in intent_set and any(
        marker in lowered for marker in ("certif", "csa", "fcc", "ce mark", "file number")
    ):
        return True
    if "test" in intent_set and any(
        marker in lowered for marker in ("test", "evaluation", "ipx", "ingress")
    ):
        return True
    if "component" in intent_set and any(
        marker in lowered
        for marker in ("battery", "camera", "display", "pcb", "sensor", "module", "mesh", "foam")
    ):
        return True
    return False


def _graph_item_relevance_score(
    item: dict[str, Any],
    *,
    question: str,
    seeds: set[str],
    intents: tuple[str, ...],
    preferred_types: set[str],
    keywords: list[str],
) -> int:
    score = 0
    question_key = _name_key(question)
    keyword_keys = [_name_key(token) for token in keywords if len(_name_key(token)) >= 3]

    def touch_name(name: str, entity_type: str = "") -> None:
        nonlocal score
        key = _name_key(name)
        type_key = re.sub(r"[^a-z0-9]", "", str(entity_type or "").casefold())
        if key in seeds:
            score += 12
        elif any(seed in key or key in seed for seed in seeds if len(seed) >= 4):
            score += 8
        if type_key and type_key in preferred_types:
            score += 10
        if _name_looks_like_intent_target(name, intents):
            score += 14
        for needle in keyword_keys:
            if needle in key:
                score += 3

    if item.get("kind") == "entity":
        touch_name(str(item.get("name") or ""), str(item.get("type") or ""))
        description = _name_key(item.get("description") or "")
        for needle in keyword_keys:
            if needle in description:
                score += 1
        return score

    if item.get("kind") != "relationship":
        return score

    source = item.get("source") or {}
    target = item.get("target") or {}
    touch_name(str(source.get("name") or ""), str(source.get("type") or ""))
    touch_name(str(target.get("name") or ""), str(target.get("type") or ""))
    rel = _name_key(item.get("relationship") or "")
    desc = _name_key(item.get("description") or "")
    keywords_field = _name_key(item.get("keywords") or "")
    blob = f"{rel} {desc} {keywords_field}"
    for intent in intents:
        if intent and intent in blob:
            score += 6
    if any(
        marker in blob
        for marker in (
            "has_clause",
            "clause",
            "complies_with",
            "subject_to",
            "has_certification",
            "requires_test",
            "contains",
            "has_model",
        )
    ):
        # Prefer compliance/structure edges over vague RELATED_TO noise.
        if any(marker in blob for marker in ("clause", "complies", "subject_to", "certif", "test")):
            score += 8
        elif "contains" in blob or "has_model" in blob:
            score += 3
    for needle in keyword_keys:
        if needle in blob:
            score += 2
    if question_key and any(token in blob for token in question_key.split() if len(token) >= 5):
        score += 1
    # Keep bridge edges between a seed and an intent-looking endpoint.
    source_key = _name_key(source.get("name") or "")
    target_key = _name_key(target.get("name") or "")
    if (source_key in seeds and _name_looks_like_intent_target(str(target.get("name") or ""), intents)) or (
        target_key in seeds and _name_looks_like_intent_target(str(source.get("name") or ""), intents)
    ):
        score += 16
    return score


def _question_graph_seeds(
    question: str,
    candidates: list[str],
) -> set[str]:
    """Entity-like seeds from the question; drop stopwords like 'apply'."""
    understanding = understand_query(question)
    banned = {
        *_STOP_WORDS,
        "apply",
        "applies",
        "applicable",
        "identify",
        "identified",
        "list",
        "show",
        "tell",
        "give",
        "find",
        "related",
        "regarding",
    }
    raw = _dedupe_strings(
        [*understanding.likely_entities, *understanding.keywords, *candidates]
    )
    usable = [
        token
        for token in raw
        if len(_name_key(token)) >= 3 and _name_key(token) not in banned
    ]
    seeds = _names_mentioned_in_text(question, usable)
    seeds |= _names_mentioned_in_text(question, candidates)
    if not seeds:
        for needle in usable:
            key = _name_key(needle)
            if len(key) < 4:
                continue
            for name in candidates:
                name_key = _name_key(name)
                if key == name_key or (len(key) >= 5 and (key in name_key or name_key in key)):
                    seeds.add(name_key)
    return {seed for seed in seeds if seed not in banned}


def _foreign_product_penalty(text: str, question: str) -> int:
    """Penalize other-product graph noise when the question names a different product."""
    question_cf = question.casefold()
    mentioned = [marker for marker in _FOREIGN_PRODUCT_MARKERS if marker in question_cf]
    blob = text.casefold()
    penalty = 0
    for marker in _FOREIGN_PRODUCT_MARKERS:
        if marker in blob and marker not in mentioned:
            penalty += 18
    return penalty


def _expand_seeds_against_candidates(seeds: set[str], candidates: list[str]) -> set[str]:
    """Map question seeds onto actual graph names (hyphen/spacing variants)."""
    if not seeds:
        return set()
    expanded = set(seeds)
    normalized_candidates = [
        (_name_key(name), re.sub(r"[^a-z0-9]", "", _name_key(name)), name)
        for name in candidates
    ]
    for seed in list(seeds):
        seed_key = _name_key(seed)
        seed_norm = re.sub(r"[^a-z0-9]", "", seed_key)
        for name_key, name_norm, _raw in normalized_candidates:
            if not name_key:
                continue
            if seed_key == name_key or seed_norm == name_norm:
                expanded.add(name_key)
            elif len(seed_norm) >= 5 and (
                seed_norm in name_norm or name_norm in seed_norm
            ):
                expanded.add(name_key)
            elif len(seed_key) >= 5 and (seed_key in name_key or name_key in seed_key):
                expanded.add(name_key)
    return expanded


def _select_question_relevant_graph_items(
    items: list[dict[str, Any]],
    question: str,
    *,
    limit: int = _MAX_GRAPH_ITEMS,
) -> list[dict[str, Any]]:
    """Rank/select LightRAG graph items so answer context keeps question-relevant paths.

    Prefers seed entities from the question and multi-hop edges toward intent
    targets (clause/standard/certification/...), instead of first-N truncation.
    """
    if not items:
        return []

    understanding = understand_query(question)
    preferred_types = _intent_answer_type_keys(question)
    candidates = _collect_graph_names(items)
    seeds = _expand_seeds_against_candidates(
        _question_graph_seeds(question, candidates),
        candidates,
    )
    # Rank whenever the question names entities, even if the dump is small.
    # If nothing is scoped, a short dump is already the full evidence set.
    if not seeds and len(items) <= limit:
        return items

    relationships = [item for item in items if item.get("kind") == "relationship"]
    entities = [item for item in items if item.get("kind") == "entity"]

    adjacency: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for item in relationships:
        ends = _relationship_endpoints(item)
        if ends is None:
            continue
        _, source_name, _, _, target_name = ends
        source_key = _name_key(source_name)
        target_key = _name_key(target_name)
        adjacency.setdefault(source_key, []).append((item, target_key))
        adjacency.setdefault(target_key, []).append((item, source_key))

    # Restrict to the seed neighborhood first so other-product edges cannot fill the budget.
    neighborhood_edges: set[int] = set()
    neighborhood_nodes: set[str] = set(seeds)
    if seeds:
        queue = deque(seeds)
        depth = {seed: 0 for seed in seeds}
        while queue:
            current = queue.popleft()
            if depth.get(current, 0) >= 3:
                continue
            for edge, neighbor in adjacency.get(current, []):
                neighborhood_edges.add(id(edge))
                if neighbor not in neighborhood_nodes:
                    neighborhood_nodes.add(neighbor)
                    depth[neighbor] = depth.get(current, 0) + 1
                    queue.append(neighbor)

    # If seeds did not connect to any edges, fall back to items that at least
    # mention a seed token — do not fill the budget with unrelated products.
    use_neighborhood = bool(seeds) and bool(neighborhood_edges)
    seed_token_norms = {
        re.sub(r"[^a-z0-9]", "", seed) for seed in seeds if len(re.sub(r"[^a-z0-9]", "", seed)) >= 4
    }

    def mentions_seed(blob: str) -> bool:
        norm = re.sub(r"[^a-z0-9]", "", blob.casefold())
        return any(token in norm for token in seed_token_norms)

    def score_item(item: dict[str, Any], *, in_neighborhood: bool) -> int:
        score = _graph_item_relevance_score(
            item,
            question=question,
            seeds=seeds,
            intents=understanding.intents,
            preferred_types=preferred_types,
            keywords=[
                token
                for token in understanding.keywords
                if _name_key(token) not in _STOP_WORDS and _name_key(token) != "apply"
            ],
        )
        if item.get("kind") == "relationship":
            source = item.get("source") or {}
            target = item.get("target") or {}
            blob = (
                f"{source.get('name')} {target.get('name')} "
                f"{item.get('relationship')} {item.get('description')} {item.get('keywords')}"
            )
        else:
            blob = f"{item.get('name')} {item.get('description')}"
        score -= _foreign_product_penalty(blob, question)
        if use_neighborhood:
            if in_neighborhood:
                score += 20
            else:
                score -= 25
        elif seeds:
            # No seed-linked edges in the LightRAG dump: keep only seed-mentioning rows.
            if mentions_seed(blob):
                score += 25
            else:
                score -= 40
        return score

    scored_rels: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(relationships):
        in_neighborhood = (not use_neighborhood) or (id(item) in neighborhood_edges)
        scored_rels.append((score_item(item, in_neighborhood=in_neighborhood), index, item))
    scored_rels.sort(key=lambda row: (-row[0], row[1]))

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[Any, ...]] = set()
    selected_names: set[str] = set(seeds)

    def item_key(item: dict[str, Any]) -> tuple[Any, ...]:
        if item.get("kind") == "relationship":
            source = item.get("source") or {}
            target = item.get("target") or {}
            return (
                "relationship",
                _name_key(source.get("name")),
                _name_key(item.get("relationship")),
                _name_key(target.get("name")),
            )
        return ("entity", _name_key(item.get("type")), _name_key(item.get("name")))

    def add_item(item: dict[str, Any]) -> bool:
        key = item_key(item)
        if key in selected_keys or len(selected) >= limit:
            return False
        selected_keys.add(key)
        selected.append(item)
        if item.get("kind") == "relationship":
            source = item.get("source") or {}
            target = item.get("target") or {}
            selected_names.add(_name_key(source.get("name")))
            selected_names.add(_name_key(target.get("name")))
        else:
            selected_names.add(_name_key(item.get("name")))
        return True

    for score, _, item in scored_rels:
        in_neighborhood = (not use_neighborhood) or (id(item) in neighborhood_edges)
        if use_neighborhood and not in_neighborhood and score < 10:
            continue
        if score <= 0:
            continue
        add_item(item)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        entity_scores: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(entities):
            name_key = _name_key(item.get("name"))
            in_neighborhood = (not use_neighborhood) or (name_key in neighborhood_nodes)
            score = score_item(item, in_neighborhood=in_neighborhood)
            if name_key in selected_names:
                score += 5
            entity_scores.append((score, index, item))
        entity_scores.sort(key=lambda row: (-row[0], row[1]))
        for score, _, item in entity_scores:
            name_key = _name_key(item.get("name"))
            if score <= 0 and name_key not in selected_names:
                continue
            if not add_item(item):
                break

    # Do not pad with unrelated graph rows when LightRAG missed the seed neighborhood.
    logger.info(
        "LightRAG graph selection: in=%d out=%d seeds=%s intents=%s neighborhood_edges=%d",
        len(items),
        len(selected),
        list(seeds)[:4],
        understanding.intents,
        len(neighborhood_edges),
    )
    return selected
 
 
def _graph_evidence_node_id(entity_type: str, name: str) -> str:
    """Stable response-local identity without exposing a Neo4j database ID."""
    return f"{entity_type.strip() or 'Entity'}:{name.strip()}"
 
 
def _build_graph_evidence(
    graph_context: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build a visualization payload exclusively from retrieved graph context."""
    nodes: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    nodes_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    relationship_keys: set[tuple[str, str, str, str, str]] = set()
 
    def add_node(entity_type: object, name: object, description: object = "") -> None:
        clean_type = str(entity_type or "Entity").strip() or "Entity"
        clean_name = str(name or "").strip()
        if not clean_name:
            return
        key = (clean_type.casefold(), clean_name.casefold())
        clean_description = str(description or "").strip()
        existing = nodes_by_key.get(key)
        if existing is not None:
            if not existing["description"] and clean_description:
                existing["description"] = clean_description
            return
        node = {
            "id": _graph_evidence_node_id(clean_type, clean_name),
            "name": clean_name,
            "type": clean_type,
            "description": clean_description,
        }
        nodes_by_key[key] = node
        nodes.append(node)
 
    for item in graph_context:
        if item.get("kind") == "entity":
            add_node(item.get("type"), item.get("name"), item.get("description"))
            continue
        if item.get("kind") != "relationship":
            continue
 
        source = item.get("source") or {}
        target = item.get("target") or {}
        source_type = str(source.get("type") or "Entity").strip() or "Entity"
        source_name = str(source.get("name") or "").strip()
        target_type = str(target.get("type") or "Entity").strip() or "Entity"
        target_name = str(target.get("name") or "").strip()
        relationship = str(item.get("relationship") or "RELATED_TO").strip()
        if not source_name or not target_name or not relationship:
            continue
 
        add_node(source_type, source_name)
        add_node(target_type, target_name)
        key = (
            source_type.casefold(),
            source_name.casefold(),
            relationship.casefold(),
            target_type.casefold(),
            target_name.casefold(),
        )
        if key in relationship_keys:
            continue
        relationship_keys.add(key)
        relationships.append(
            {
                "source": _graph_evidence_node_id(source_type, source_name),
                "source_name": source_name,
                "source_type": source_type,
                "relationship": relationship,
                "target": _graph_evidence_node_id(target_type, target_name),
                "target_name": target_name,
                "target_type": target_type,
                "description": str(item.get("description") or "").strip(),
                "keywords": str(item.get("keywords") or "").strip(),
            }
        )
 
    return {"nodes": nodes, "relationships": relationships}


def _name_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _collect_graph_names(graph_context: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in graph_context:
        if item.get("kind") == "entity":
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
            continue
        if item.get("kind") != "relationship":
            continue
        source = item.get("source") or {}
        target = item.get("target") or {}
        for raw in (source.get("name"), target.get("name")):
            name = str(raw or "").strip()
            if name:
                names.append(name)
    return _dedupe_strings(names)


def _names_mentioned_in_text(text: str, candidates: list[str]) -> set[str]:
    """Match known entity names inside free text (longest-first, case-insensitive)."""
    haystack = _name_key(text)
    if not haystack:
        return set()
    mentioned: set[str] = set()
    for name in sorted(candidates, key=lambda value: len(value), reverse=True):
        key = _name_key(name)
        if len(key) < 2 or key not in haystack:
            continue
        # Avoid tiny alphanumeric tokens matching inside larger identifiers.
        if len(key) < 4 and not re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack):
            continue
        mentioned.add(key)
    return mentioned


def _entity_type_by_name(graph_context: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for item in graph_context:
        if item.get("kind") != "entity":
            continue
        name = str(item.get("name") or "").strip()
        entity_type = str(item.get("type") or "Entity").strip() or "Entity"
        key = _name_key(name)
        if not key:
            continue
        # Prefer a concrete ontology type over a generic Entity placeholder.
        if key not in lookup or (
            lookup[key].casefold() == "entity" and entity_type.casefold() != "entity"
        ):
            lookup[key] = entity_type
    return lookup


def _relationship_endpoints(
    item: dict[str, Any],
) -> tuple[str, str, str, str, str] | None:
    source = item.get("source") or {}
    target = item.get("target") or {}
    source_name = str(source.get("name") or "").strip()
    target_name = str(target.get("name") or "").strip()
    relationship = str(item.get("relationship") or "").strip()
    if not source_name or not target_name or not relationship:
        return None
    source_type = str(source.get("type") or "Entity").strip() or "Entity"
    target_type = str(target.get("type") or "Entity").strip() or "Entity"
    return source_type, source_name, relationship, target_type, target_name


def _split_lightrag_source_ids(source_id: str) -> list[str]:
    text = str(source_id or "").strip()
    if not text:
        return []
    parts = re.split(rf"{re.escape(GRAPH_FIELD_SEP)}|,", text)
    return [part.strip() for part in parts if part.strip()]
 
 
def _graph_items_from_lightrag(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    stored_by_id: dict[str, dict[str, Any]],
    scope: str = "",
) -> list[dict[str, Any]]:
    del stored_by_id, scope  # Mix retrieval is not limited to one document_id.
    items: list[dict[str, Any]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("entity_name") or entity.get("name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "kind": "entity",
                "type": str(entity.get("entity_type") or "Entity").strip() or "Entity",
                "name": name,
                "description": str(entity.get("description") or "").strip(),
            }
        )
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        source_name = str(rel.get("src_id") or rel.get("source") or "").strip()
        target_name = str(rel.get("tgt_id") or rel.get("target") or "").strip()
        if not source_name or not target_name:
            continue
        keywords = str(rel.get("keywords") or "").strip()
        relationship = keywords.split(",")[0].strip() if keywords else "RELATED_TO"
        items.append(
            {
                "kind": "relationship",
                "source": {"type": "Entity", "name": source_name},
                "relationship": relationship or "RELATED_TO",
                "target": {"type": "Entity", "name": target_name},
                "description": str(rel.get("description") or "").strip(),
                "keywords": keywords,
            }
        )
    return _dedupe_graph_items(items)
 
 
def _document_chunks_from_lightrag(
    raw_chunks: list[dict[str, Any]],
    stored_by_id: dict[str, dict[str, Any]],
    scope: str = "",
) -> list[dict[str, Any]]:
    del scope  # Mix retrieval is not limited to one document_id.
    mapped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in raw_chunks:
        if not isinstance(chunk, dict):
            continue
        lightrag_chunk_id = str(chunk.get("chunk_id") or "").strip()
        stored = stored_by_id.get(lightrag_chunk_id) or {}
        full_doc_id = str(stored.get("full_doc_id") or "").strip()
        if ":" in full_doc_id:
            source_document_id, source_chunk_id = full_doc_id.split(":", 1)
        else:
            source_document_id = full_doc_id
            source_chunk_id = lightrag_chunk_id
        source_chunk_id = source_chunk_id.strip() or lightrag_chunk_id
        seen_key = full_doc_id or lightrag_chunk_id
        if not source_chunk_id or seen_key in seen:
            continue
        seen.add(seen_key)
        text = str(chunk.get("content") or stored.get("content") or "").strip()
        if not text:
            continue
        page_range = stored.get("page_range")
        if not isinstance(page_range, dict):
            page_range = {}
        mapped.append(
            {
                "text": text,
                "source_file": str(
                    chunk.get("file_path") or stored.get("file_path") or ""
                ).strip(),
                "page_range": page_range,
                "chunk_id": source_chunk_id,
                "document_id": source_document_id,
            }
        )
    return mapped
 
 
async def _aload_stored_rows(rag: Any, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    stored_by_id: dict[str, dict[str, Any]] = {}
    unique_ids = _dedupe_strings(chunk_ids)
    if not unique_ids:
        return stored_by_id
    for row in await rag.text_chunks.get_by_ids(unique_ids):
        if not row:
            continue
        row_id = str(row.get("_id") or row.get("id") or "").strip()
        if row_id:
            stored_by_id[row_id] = row
    missing_ids = [chunk_id for chunk_id in unique_ids if chunk_id not in stored_by_id]
    if missing_ids:
        for row in await rag.chunks_vdb.get_by_ids(missing_ids):
            if not row:
                continue
            row_id = str(row.get("id") or row.get("_id") or "").strip()
            if row_id and row_id not in stored_by_id:
                stored_by_id[row_id] = row
    return stored_by_id
 
 
def _retrieve_lightrag_context(
    question: str,
    document_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retrieve native LightRAG mix context: local KG + global KG + chunks.
 
    LightRAG 1.5.6 ``mix`` mode is KG local+global retrieval plus chunk vector
    search. ``hybrid`` is KG-only and is not used. Results are mapped into the
    existing graph_context / document_context shapes. Retrieval is not limited
    to the current document_id; any previously indexed LightRAG document may
    be used.
    """
    del document_id  # API compatibility only; search all indexed LightRAG docs.
    cleaned_question = (question or "").strip()
    if not cleaned_question:
        return [], []
 
    async def _aretrieve() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rag = await _aget_lightrag_instance()
        result = await rag.aquery_data(
            cleaned_question,
            param=QueryParam(
                mode="mix",
                only_need_context=True,
                enable_rerank=False,
                chunk_top_k=20,
            ),
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            return [], []
 
        raw_entities = data.get("entities") or []
        raw_relationships = data.get("relationships") or []
        raw_chunks = data.get("chunks") or []
        lookup_ids = [
            str(chunk.get("chunk_id") or "").strip()
            for chunk in raw_chunks
            if isinstance(chunk, dict) and str(chunk.get("chunk_id") or "").strip()
        ]
        for item in [*raw_entities, *raw_relationships]:
            if isinstance(item, dict):
                lookup_ids.extend(_split_lightrag_source_ids(str(item.get("source_id") or "")))
        stored_by_id = await _aload_stored_rows(rag, lookup_ids)
        graph_context = _select_question_relevant_graph_items(
            _graph_items_from_lightrag(
                raw_entities if isinstance(raw_entities, list) else [],
                raw_relationships if isinstance(raw_relationships, list) else [],
                stored_by_id,
            ),
            cleaned_question,
        )
        document_context = _prefer_question_relevant_sources(
            _document_chunks_from_lightrag(
                raw_chunks if isinstance(raw_chunks, list) else [],
                stored_by_id,
            ),
            cleaned_question,
        )
        return graph_context, document_context
 
    try:
        return _run_on_lightrag_loop(_aretrieve())
    except Exception as exc:
        logger.warning("LightRAG mix retrieval unavailable: %s", exc)
        return [], []
 
 
def _question_focus_needles(question: str) -> list[str]:
    understanding = understand_query(question)
    return [
        token.casefold()
        for token in _dedupe_strings(
            [*understanding.likely_entities, *understanding.keywords]
        )
        if len(token.strip()) >= 3
    ]


def _truncate_evidence_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _excerpt_relevant_chunk_text(text: str, question: str) -> str:
    """Keep question-relevant sentences from a chunk; fall back to the start."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= _MAX_CHUNK_CHARS:
        return cleaned

    needles = _question_focus_needles(question)
    lowered = cleaned.casefold()
    if not needles or not any(needle in lowered for needle in needles):
        return cleaned[:_MAX_CHUNK_CHARS]

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    keep: set[int] = set()
    for index, part in enumerate(parts):
        blob = part.casefold()
        mentions_question = any(needle in blob for needle in needles)
        looks_like_fact = bool(
            re.search(r"\b(?:iec|iso|ul|en)\s*[\d:-]+", blob)
            or re.search(r"\b(?:clause|subsection|certif|test)\b", blob)
        )
        if mentions_question or looks_like_fact:
            for neighbor in (index - 1, index, index + 1):
                if 0 <= neighbor < len(parts):
                    keep.add(neighbor)
    if not keep:
        return cleaned[:_MAX_CHUNK_CHARS]
    excerpt = " ".join(parts[index] for index in sorted(keep))
    return excerpt[:_MAX_CHUNK_CHARS]


def _drop_unasked_product_lines(text: str, question: str) -> str:
    """Drop lines about other products when the question already names one."""
    if not text or "|" in text:
        return text
    question_cf = question.casefold()
    mentioned = [marker for marker in _FOREIGN_PRODUCT_MARKERS if marker in question_cf]
    if not mentioned:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        blob = line.casefold()
        has_foreign = any(
            marker in blob and marker not in mentioned
            for marker in _FOREIGN_PRODUCT_MARKERS
        )
        has_asked = any(marker in blob for marker in mentioned)
        if has_foreign and not has_asked:
            continue
        kept.append(line)
    if not any(line.strip() for line in kept):
        return text
    return "\n".join(kept)


def _format_hybrid_context(
    graph_context: list[dict[str, Any]],
    document_context: list[dict[str, Any]],
    question: str = "",
) -> str:
    graph_lines: list[str] = []
    for index, item in enumerate(graph_context, start=1):
        if item["kind"] == "relationship":
            line = (
                f"[G{index}] {item['source']['type']}: {item['source']['name']} "
                f"-[{item['relationship']}]-> "
                f"{item['target']['type']}: {item['target']['name']}"
            )
            if item.get("description"):
                line += (
                    " | Evidence: "
                    + _truncate_evidence_text(
                        str(item.get("description") or ""),
                        _MAX_GRAPH_DESCRIPTION_CHARS,
                    )
                )
        else:
            line = f"[G{index}] {item['type']}: {item['name']}"
            if item.get("description"):
                line += (
                    " | Description: "
                    + _truncate_evidence_text(
                        str(item.get("description") or ""),
                        _MAX_GRAPH_DESCRIPTION_CHARS,
                    )
                )
        graph_lines.append(line)

    document_blocks: list[str] = []
    for index, chunk in enumerate(document_context, start=1):
        pages = chunk.get("page_range") or {}
        header = (
            f"[D{index}] Source: {chunk.get('source_file') or 'unknown'}; "
            f"pages: {pages.get('start') or '?'}-{pages.get('end') or '?'}; "
            f"chunk: {chunk.get('chunk_id') or '?'}"
        )
        body = _excerpt_relevant_chunk_text(str(chunk.get("text") or ""), question)
        document_blocks.append(f"{header}\n{body}")

    return (
        "GRAPH CONTEXT:\n"
        + ("\n".join(graph_lines) if graph_lines else "(none)")
        + "\n\nDOCUMENT EVIDENCE:\n"
        + ("\n\n".join(document_blocks) if document_blocks else "(none)")
    )
 
 
def _generate_answer(question: str, context: str) -> str:
    system_prompt = """You are the UL Knowledge Assistant for a generic compliance knowledge graph.
Answer only from the supplied GRAPH CONTEXT and DOCUMENT EVIDENCE.

Grounding rules:
- Every claim must be directly supported by a GRAPH CONTEXT or DOCUMENT EVIDENCE span. If a detail is not present there, omit it. Never invent an entity, relationship, hierarchy, compliance claim, clause, certification, test, or number.
- Do not assume similarly named entities are identical unless the context explicitly supports it.
- Keep Product, Model, and Component distinct.
- Prefer GRAPH CONTEXT relationships for structural questions (what applies, contains, connects, supplies).
- Prefer DOCUMENT EVIDENCE for detailed factual wording, requirements, and narrative detail.
- Combine both sources when both are relevant.
- Include every retrieved fact that directly answers this question. Do not drop a supported list item (standard, component, certification, test, or clause) that answers the question. Do not include same-type items from the context that do not answer this question (for example another product's standards).
- Prefer facts that mention the asked entity and the asked intent. Ignore unrelated products, metadata, file notices, authenticity or status boilerplate, and other retrieved text that is not needed.
- Only claim exclusivity when the supplied context explicitly supports that nothing else applies.
- Missing retrieved evidence is not proof that something does not exist. If the supplied context does not establish an answer, say that the retrieved evidence does not identify it — do not make a negative factual claim such as "no clauses apply" or "none exist".
- Answer the user's question only. Do not add extra background.
- If the retrieved evidence answers the question, do not add a remark that information is missing or unavailable.
- Use the simplest natural form that fits the question: ordinary prose by default; a list only for multiple distinct items; a table only when a comparison is useful. Do not use a labeled field outline.
- Keep the answer concise and fact-based. No speculation and no retrieval commentary.
- Do not include retrieval markers such as [G1], [D1], chunk IDs, node IDs, or scores.
- Do not mention retrieval implementation details."""
    understanding = understand_query(question)
    focus = ", ".join(
        understanding.likely_entities or understanding.keywords[:8]
    ) or "the asked subject"
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Answer this question only, about: {focus}.\n"
        "Use only GRAPH CONTEXT and DOCUMENT EVIDENCE below.\n"
        "Do not add information that is not present in that evidence.\n"
        "Include every evidence fact that directly answers the question; omit unrelated details.\n"
        "Keep the answer concise and fact-based.\n\n"
        f"Retrieved context:\n{context}"
    )
    client, model = get_chat_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if not model.lower().startswith("gpt-5"):
        kwargs["temperature"] = 0
    response = client.chat.completions.create(**kwargs)
    answer = str(response.choices[0].message.content or "").strip()
    if not answer:
        raise ValueError("The language model returned an empty answer")
    return answer
 
 
_INTERNAL_REF_RE = re.compile(
    r"""
    \[\s*[GD]\s*\d+\s*\]          # [G1], [D12]
  | \(\s*[GD]\s*\d+\s*\)          # (G1), (D12)
  | \bchunk[_-]?id\s*[:=]?\s*\S+  # chunk_id=...
  | \bnode[_-]?id\s*[:=]?\s*\S+
  | \brelationship[_-]?id\s*[:=]?\s*\S+
  | \bgraph[_-]?id\s*[:=]?\s*\S+
  | \bembedding[_-]?id\s*[:=]?\s*\S+
  | \bdocument[_-]?id\s*[:=]?\s*\S+
  | \bscore\s*[:=]\s*[-+]?\d+(?:\.\d+)?
  | \brank(?:ing)?\s*[:=]\s*\d+
    """,
    re.IGNORECASE | re.VERBOSE,
)
_META_PHRASE_RE = re.compile(
    r"(?im)^\s*(?:"
    r"the retrieved context (?:indicates|shows|suggests)|"
    r"based on the retrieved context|"
    r"according to the retrieval|"
    r"the graph evidence shows|"
    r"the system found|"
    r"from the retrieved (?:graph|document|context)|"
    r"the (?:vector|graph) (?:search|query) (?:did not work|returned nothing|failed)|"
    r"the retrieval system failed"
    r")[,:\s]*"
)
_RAW_GRAPH_EDGE_RE = re.compile(
    r"(?im)^\s*(?P<source>.+?)\s*[|\u2502]\s*(?P<rel>[A-Za-z0-9_ -]+)\s*[|\u2502]\s*(?P<target>.+?)\s*$"
)
 
 
def _normalize_space(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
 
 
def _strip_internal_artifacts(text: str) -> str:
    """Remove retrieval/implementation identifiers from user-facing text."""
    cleaned = _INTERNAL_REF_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    return _normalize_space(cleaned)
 
 
def _strip_meta_phrases(text: str) -> str:
    cleaned = _META_PHRASE_RE.sub("", text)
    return _normalize_space(cleaned)
 
 
def _relationship_to_readable(rel: str) -> str:
    token = re.sub(r"[_\-]+", " ", (rel or "").strip())
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return "is related to"
    if token.isupper() or "_" in (rel or ""):
        return token.lower()
    return token
 
 
def _humanize_raw_graph_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        match = _RAW_GRAPH_EDGE_RE.match(line)
        if not match:
            lines.append(line)
            continue
        source = match.group("source").strip()
        rel = _relationship_to_readable(match.group("rel"))
        target = match.group("target").strip()
        lines.append(f"- {source} {rel} {target}")
    return "\n".join(lines)
 
 
def _dedupe_repeated_lines(text: str) -> str:
    """Drop exact duplicate bullet/paragraph lines while preserving order."""
    result: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if result and result[-1] != "":
                result.append("")
            continue
        key = re.sub(r"^[-*+\d.)\s]+", "", stripped).casefold()
        key = re.sub(r"\s+", " ", key).strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(line.rstrip())
    return _normalize_space("\n".join(result))
 
 
def _question_format_hint(question: str) -> str:
    understanding = understand_query(question)
    intents = set(understanding.intents)
    lowered = question.casefold()
    if any(token in lowered for token in ("compare", "comparison", "versus", " vs ", "difference")):
        return "comparison"
 
    list_markers = (
        "list",
        "what are",
        "which are",
        "contain",
        "contains",
        "present in",
    )
    relationship_markers = (
        "relationship",
        "between",
        "associated with",
        "connected to",
        "how is",
        "why is",
        "how are",
        "why are",
    )
    is_listy = any(token in lowered for token in list_markers)
    is_relationship = any(token in lowered for token in relationship_markers)
 
    # Prefer list structure for "what/which … contain/list" questions even when
    # relationship keywords like "contains" also appear in intent detection.
    if is_listy and not is_relationship:
        return "list"
    if is_relationship or ("relationships" in intents and not is_listy):
        return "relationship"
    return "prose"
 
 
def _sanitize_answer_markdown(text: str) -> str:
    cleaned = _strip_internal_artifacts(text)
    cleaned = _strip_meta_phrases(cleaned)
    cleaned = _humanize_raw_graph_lines(cleaned)
    cleaned = _dedupe_repeated_lines(cleaned)
    cleaned = re.sub(r"(?m)^\s*```.*$", "", cleaned)
    cleaned = cleaned.replace("```", "")
    return _normalize_space(cleaned)
 
 
def _deterministic_format_answer(
    user_question: str,
    generated_answer: str,
    *,
    format_hint: str | None = None,
) -> str:
    """Presentation-only cleanup used as a safe fallback and final polish."""
    hint = format_hint or _question_format_hint(user_question)
    text = _sanitize_answer_markdown(generated_answer)
    if not text:
        return "The retrieved evidence does not identify an answer to this question."

    # Promote a dense comma-separated item run into bullets only for list questions.
    if hint == "list" and "\n" not in text and "," in text and len(text) > 80:
        lead, _, rest = text.partition(":")
        body = rest.strip() if rest else text
        parts = [part.strip(" .;") for part in re.split(r",| and ", body) if part.strip(" .;")]
        if len(parts) >= 3:
            intro = f"{lead.strip()}:" if rest else "Relevant items:"
            bullets = "\n".join(f"- {part}" for part in _dedupe_strings(parts))
            text = f"{intro}\n\n{bullets}"
            text = _sanitize_answer_markdown(text)

    text = _drop_unasked_product_lines(text, user_question)
    return _normalize_space(text)


def format_final_answer(
    user_question: str,
    generated_answer: str,
    retrieved_context: str,
    *,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Convert a grounded draft answer into a clean user-facing Markdown response.

    Presentation-only: strip retrieval artifacts and apply light structure.
    Do not call a second LLM — a rewrite can add ungrounded facts or drop
    supported ones.

    ``sources`` is accepted so citation metadata can remain associated with the
    response payload; it is intentionally not rendered into the natural-language
    answer (the frontend displays sources separately).
    """
    del sources  # preserved for API compatibility; not shown inline
    question = (user_question or "").strip()
    draft = (generated_answer or "").strip()
    context = (retrieved_context or "").strip()
    if not draft and not context:
        return "The retrieved evidence does not identify an answer to this question."

    return _deterministic_format_answer(
        question,
        draft or "The retrieved evidence does not identify an answer to this question.",
        format_hint=_question_format_hint(question),
    )
 
 
def _public_sources(document_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_file": chunk["source_file"],
            "page_range": chunk["page_range"],
            "chunk_id": chunk["chunk_id"],
            "document_id": chunk["document_id"],
        }
        for chunk in document_context
    ]
 
 
def _neo4j_subgraph_to_graph_items(subgraph: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a Neo4j lineage subgraph into chatbot graph_context items."""
    items: list[dict[str, Any]] = []
    for node in subgraph.get("nodes") or []:
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "kind": "entity",
                "type": str(node.get("label") or node.get("type") or "Entity").strip() or "Entity",
                "name": name,
                "description": str(node.get("description") or "").strip(),
                "canonical_id": str(node.get("canonical_id") or "").strip(),
            }
        )
    for edge in subgraph.get("edges") or []:
        source_name = str(edge.get("source_name") or "").strip()
        target_name = str(edge.get("destination_name") or "").strip()
        relationship = str(edge.get("relationship") or "").strip()
        if not source_name or not target_name or not relationship:
            continue
        items.append(
            {
                "kind": "relationship",
                "source": {
                    "type": str(edge.get("source_label") or "Entity").strip() or "Entity",
                    "name": source_name,
                },
                "relationship": relationship,
                "target": {
                    "type": str(edge.get("destination_label") or "Entity").strip() or "Entity",
                    "name": target_name,
                },
                "description": str(edge.get("description") or "").strip(),
                "keywords": str(edge.get("keywords") or relationship).strip(),
            }
        )
    return _dedupe_graph_items(items)


def _retrieve_neo4j_lineage(question: str, answer: str = "") -> list[dict[str, Any]]:
    """Fetch a question-aware Neo4j neighborhood for View Lineage only.

    Seeded from the question (``answer`` is ignored). Runs in parallel with
    LightRAG; View Lineage reuses this payload — no second Neo4j query.
    """
    import config

    if not config.NEO4J_PASSWORD:
        return []
    cleaned = (question or "").strip()
    if not cleaned:
        return []
    try:
        from ul.knowledge_graph.queries import fetch_lineage_subgraph
        from ul.neo4j_service import _get_driver
    except Exception as exc:
        logger.warning("Neo4j lineage import unavailable: %s", exc)
        return []

    del answer  # Parallel path has no answer yet; never seed from LightRAG text.
    understanding = understand_query(cleaned)
    seed_names = _dedupe_strings(
        [
            *understanding.likely_entities,
            *understanding.keywords[:8],
            *re.findall(r"\b[A-Z]{1,4}-?[A-Z0-9]{3,}\b", cleaned),
            *re.findall(r"\bIEC\s*[\d:-]+\b", cleaned, flags=re.IGNORECASE),
            *re.findall(r"\bUN\s+Manual[^\n,]{0,40}", cleaned, flags=re.IGNORECASE),
        ]
    )
    if not seed_names:
        return []

    driver = None
    try:
        driver = _get_driver()
        with driver.session() as session:
            subgraph = fetch_lineage_subgraph(session, seed_names, question=cleaned)
        items = _neo4j_subgraph_to_graph_items(subgraph)
        logger.info(
            "Neo4j lineage seeds=%s nodes=%d relationships=%d",
            seed_names[:6],
            sum(1 for item in items if item.get("kind") == "entity"),
            sum(1 for item in items if item.get("kind") == "relationship"),
        )
        return items
    except Exception as exc:
        logger.warning("Neo4j lineage retrieval failed: %s", exc)
        return []
    finally:
        if driver is not None:
            driver.close()


# The LLM planner selects an approved traversal. Setting this to False falls
# back to the hardcoded verification plan.
_LINEAGE_PLANNER_ENABLED = True

_EMPTY_GRAPH_RESULT: dict[str, Any] = {
    "graph_evidence": {"nodes": [], "relationships": []},
    "generated_cypher": "",
    "query_parameters": {},
}


def _empty_graph_result() -> dict[str, Any]:
    return {
        "graph_evidence": {"nodes": [], "relationships": []},
        "generated_cypher": "",
        "query_parameters": {},
    }


def _hardcoded_plan(question: str) -> Any:
    """Phase 1 stand-in for the LLM planner."""
    del question
    from ul.knowledge_graph.queries import LineagePlan

    return LineagePlan(
        entity_name="iPhone 16",
        entity_label="Product",
        path="components",
    )


def _run_graph_pipeline(question: str) -> dict[str, Any]:
    """Plan -> validate -> build -> execute -> graph_evidence.

    Runs in parallel with the answer pipeline and never reads the answer. The
    executed Cypher is returned alongside the evidence so the same query can be
    pasted into Neo4j Browser and compared against the rendered lineage.
    """
    import config

    if not config.NEO4J_PASSWORD or not (question or "").strip():
        return _empty_graph_result()

    try:
        from ul.knowledge_graph.queries import (
            LineagePlanError,
            build_lineage_cypher,
            execute_lineage_cypher,
            paths_to_graph_evidence,
        )
        from ul.neo4j_service import _get_driver
    except Exception as exc:
        logger.warning("Lineage pipeline import unavailable: %s", exc)
        return _empty_graph_result()

    try:
        if _LINEAGE_PLANNER_ENABLED:
            from ul.knowledge_graph.queries import plan_lineage_query

            plan = plan_lineage_query(question)
        else:
            plan = _hardcoded_plan(question)
    except LineagePlanError as exc:
        logger.warning("Lineage planner rejected the question: %s", exc)
        return _empty_graph_result()
    except Exception as exc:
        logger.warning("Lineage planner failed: %s", exc)
        return _empty_graph_result()

    try:
        cypher, parameters = build_lineage_cypher(plan)
    except LineagePlanError as exc:
        logger.warning("Lineage plan failed validation: %s", exc)
        return _empty_graph_result()

    driver = None
    try:
        driver = _get_driver()
        records = execute_lineage_cypher(driver, cypher, parameters)
        evidence = paths_to_graph_evidence(records)
        logger.info(
            "Lineage plan=%s entity=%r paths=%d nodes=%d relationships=%d",
            plan.path,
            plan.entity_name,
            len(records),
            len(evidence["nodes"]),
            len(evidence["relationships"]),
        )
        return {
            "graph_evidence": evidence,
            "generated_cypher": cypher,
            "query_parameters": parameters,
        }
    except Exception as exc:
        logger.warning("Lineage query execution failed: %s", exc)
        return _empty_graph_result()
    finally:
        if driver is not None:
            driver.close()


def _narrow_lineage_for_display(
    neo4j_items: list[dict[str, Any]],
    question: str,
    answer: str = "",
) -> list[dict[str, Any]]:
    """Keep the question-relevant Neo4j neighborhood (not answer-truncated).

    ``answer`` is accepted for API compatibility but intentionally ignored so
    View Lineage is not cut down to whatever LightRAG happened to mention.
    """
    del answer
    return _lineage_for_question(neo4j_items, question)


def _intent_focus_types(question: str) -> set[str]:
    """Map question intents to Neo4j labels worth keeping as lineage endpoints."""
    understanding = understand_query(question)
    focus: set[str] = set()
    for intent in understanding.intents:
        if intent == "standard":
            focus.update({"standard", "certification", "test", "testplan", "clause"})
        elif intent == "clause":
            focus.update({"clause", "section", "standard", "requirement"})
        elif intent == "certification":
            focus.update({"certification", "standard", "test", "testplan", "filenumber", "file_number"})
        elif intent == "test":
            focus.update({"test", "testplan", "standard", "certification"})
        elif intent == "component":
            focus.update({"component", "material", "company"})
        elif intent == "company":
            focus.update({"company", "product", "model", "applicant"})
        elif intent == "product":
            focus.update({"product", "model", "component", "standard"})
        elif intent == "model":
            focus.update({"model", "product", "component", "standard", "variant"})
        elif intent == "compliance":
            focus.update({"standard", "certification", "test", "clause", "component"})
    # Structural intermediates are always useful for readable lineage.
    focus.update({"product", "model", "component", "ul", "company", "applicant"})
    if not focus:
        focus.update(
            {
                "product",
                "model",
                "component",
                "standard",
                "certification",
                "test",
                "clause",
                "company",
            }
        )
    return {item.casefold().replace("_", "") for item in focus}


def _normalize_type_key(entity_type: object) -> str:
    return str(entity_type or "Entity").strip().casefold().replace("_", "")


def _lineage_for_question(
    neo4j_items: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    """Select the complete question-relevant Neo4j subgraph for View Lineage.

    Starts from entities named in the question and keeps connected hops that
    stay on focus types for that question. Does not use the LightRAG answer.
    """
    if not neo4j_items:
        return []

    candidates = _collect_graph_names(neo4j_items)
    understanding = understand_query(question)
    seeds = _names_mentioned_in_text(
        question,
        _dedupe_strings([*candidates, *understanding.likely_entities, *understanding.keywords]),
    )
    seeds |= _names_mentioned_in_text(question, candidates)
    if not seeds:
        # Fall back to best fuzzy match on likely entities / keywords against graph names.
        for needle in _dedupe_strings([*understanding.likely_entities, *understanding.keywords]):
            key = _name_key(needle)
            if len(key) < 3:
                continue
            for name in candidates:
                name_key = _name_key(name)
                if key == name_key or (len(key) >= 5 and (key in name_key or name_key in key)):
                    seeds.add(name_key)
    if not seeds:
        return []

    focus_types = _intent_focus_types(question)
    # Always drop pure geography unless the question is about location.
    drop_types = set()
    if "location" not in " ".join(understanding.intents):
        drop_types.update({"location", "address"})

    type_lookup = _entity_type_by_name(neo4j_items)
    relationships = [item for item in neo4j_items if item.get("kind") == "relationship"]
    entities = [item for item in neo4j_items if item.get("kind") == "entity"]

    adjacency: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for item in relationships:
        ends = _relationship_endpoints(item)
        if ends is None:
            continue
        source_type, source_name, _, target_type, target_name = ends
        if _normalize_type_key(source_type) in drop_types or _normalize_type_key(target_type) in drop_types:
            continue
        source_key = _name_key(source_name)
        target_key = _name_key(target_name)
        adjacency.setdefault(source_key, []).append((item, target_key))
        adjacency.setdefault(target_key, []).append((item, source_key))

    kept_nodes: set[str] = set(seeds)
    kept_edges: list[dict[str, Any]] = []
    seen_edge_keys: set[tuple[str, str, str, str, str]] = set()
    queue: deque[str] = deque(seeds)

    while queue:
        current = queue.popleft()
        for item, neighbor in adjacency.get(current, []):
            ends = _relationship_endpoints(item)
            if ends is None:
                continue
            source_type, source_name, relationship, target_type, target_name = ends
            source_key = _name_key(source_name)
            target_key = _name_key(target_name)
            neighbor_type = (
                type_lookup.get(neighbor)
                or (target_type if neighbor == target_key else source_type)
            )
            neighbor_type_key = _normalize_type_key(neighbor_type)
            # Keep intermediate/focus types; skip off-topic branches.
            if neighbor not in kept_nodes and neighbor_type_key not in focus_types:
                continue
            edge_key = (
                source_type.casefold(),
                source_key,
                relationship.casefold(),
                target_type.casefold(),
                target_key,
            )
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                kept_edges.append(item)
            if neighbor not in kept_nodes:
                kept_nodes.add(neighbor)
                queue.append(neighbor)

    if not kept_edges and not kept_nodes:
        return []

    selected: list[dict[str, Any]] = []
    for item in entities:
        name = str(item.get("name") or "").strip()
        if _name_key(name) in kept_nodes:
            selected.append(item)
    selected.extend(kept_edges[:_MAX_LINEAGE_RELATIONSHIPS])
    # Allow a larger lineage display than the old answer-truncated chip dump.
    return selected


def answer_question(
    question: str,
    *,
    graph_id: str | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Run LightRAG mix and the planned lineage query in parallel.

    - LightRAG ``mode="mix"`` → answer + sources
    - Planned Cypher → Neo4j paths → ``graph_evidence`` for View Lineage
    - Neo4j is not merged into the answer prompt
    """
    scoped_document_id = _scope_document_id(graph_id, document_id)

    lightrag_graph: list[dict[str, Any]] = []
    document_context: list[dict[str, Any]] = []

    def _load_lightrag() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            return _retrieve_lightrag_context(question, scoped_document_id)
        except Exception as exc:
            logger.warning("LightRAG mix retrieval unavailable: %s", exc)
            return [], []

    def _load_graph() -> dict[str, Any]:
        try:
            return _run_graph_pipeline(question)
        except Exception as exc:
            logger.warning("Lineage pipeline unavailable: %s", exc)
            return _empty_graph_result()

    with ThreadPoolExecutor(max_workers=2) as pool:
        lightrag_future = pool.submit(_load_lightrag)
        graph_future = pool.submit(_load_graph)
        lightrag_graph, document_context = lightrag_future.result()
        graph_result = graph_future.result()

    if scoped_document_id:
        document_context = _filter_chunks_for_document(document_context, scoped_document_id)
    document_context = _prefer_question_relevant_sources(document_context, question)

    # Answer generation uses LightRAG output exclusively.
    graph_context = lightrag_graph

    if not graph_context and not document_context:
        return {
            "answer": "No relevant information was found in the selected knowledge graph or its documents.",
            "sources": [],
            "graph_context": [],
            "graph_evidence": graph_result["graph_evidence"],
            "generated_cypher": graph_result["generated_cypher"],
            "query_parameters": graph_result["query_parameters"],
        }

    context = _format_hybrid_context(graph_context, document_context, question)
    draft_answer = _generate_answer(question, context)
    answer = format_final_answer(
        question,
        draft_answer,
        context,
        sources=_public_sources(document_context),
    )

    return {
        "answer": answer,
        "sources": _public_sources(document_context),
        "graph_context": graph_context,
        # Direct passthrough: the Neo4j result is the lineage graph.
        "graph_evidence": graph_result["graph_evidence"],
        "generated_cypher": graph_result["generated_cypher"],
        "query_parameters": graph_result["query_parameters"],
    }


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> dict[str, Any]:
    """Answer a question using LightRAG native mix retrieval.

    The planned Neo4j lineage query runs in parallel for View Lineage
    visualization and is not used to generate the answer.
    """
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")
    try:
        return answer_question(
            question,
            graph_id=(request.graph_id or "").strip() or None,
            document_id=(request.document_id or "").strip() or None,
        )
    except Exception as exc:
        logger.exception("UL chatbot request failed")
        raise HTTPException(
            status_code=503,
            detail="The UL knowledge assistant is temporarily unavailable.",
        ) from exc


def _prefer_question_relevant_sources(
    document_context: list[dict[str, Any]],
    question: str,
    *,
    limit: int = _MAX_DOCUMENT_CHUNKS,
) -> list[dict[str, Any]]:
    """Rank document chunks so unrelated product PDFs do not dominate Sources."""
    if not document_context:
        return []
    understanding = understand_query(question)
    needles = _question_focus_needles(question)
    question_cf = question.casefold()
    mentioned_markers = [
        marker for marker in _FOREIGN_PRODUCT_MARKERS if marker in question_cf
    ]

    def score(chunk: dict[str, Any]) -> tuple[int, int]:
        source = str(chunk.get("source_file") or "").casefold()
        text = str(chunk.get("text") or "").casefold()[:2000]
        hits = 0
        for needle in needles:
            if needle in source:
                hits += 4
            if needle in text:
                hits += 1
        for intent in understanding.intents:
            for term in _INTENT_CHUNK_TERMS.get(intent, ()):
                if term in text:
                    hits += 1
                    break
        if mentioned_markers:
            for marker in _FOREIGN_PRODUCT_MARKERS:
                if marker not in mentioned_markers:
                    if marker in source:
                        hits -= 5
                    if marker in text:
                        hits -= 2
        pages = chunk.get("page_range") or {}
        start = pages.get("start")
        has_page = 1 if isinstance(start, int) or (isinstance(start, str) and start.isdigit()) else 0
        return (hits, has_page)

    ranked = sorted(
        enumerate(document_context),
        key=lambda pair: (-score(pair[1])[0], -score(pair[1])[1], pair[0]),
    )
    if needles:
        relevant = [chunk for _, chunk in ranked if score(chunk)[0] > 0]
        if relevant:
            return relevant[:limit]
    return [chunk for _, chunk in ranked[:limit]]


def _scope_document_id(
    graph_id: str | None,
    document_id: str | None,
) -> str | None:
    """Document provenance scope. ``ul_global`` / legacy ``kg_`` ids are global mode."""
    scoped = (document_id or "").strip() or None
    if scoped:
        return scoped
    gid = (graph_id or "").strip()
    if not gid or gid == GLOBAL_GRAPH_ID or gid.startswith("kg_"):
        return None
    return gid


def _filter_chunks_for_document(
    chunks: list[dict[str, Any]],
    document_id: str | None,
) -> list[dict[str, Any]]:
    current = (document_id or "").strip()
    if not current:
        return chunks
    return [chunk for chunk in chunks if str(chunk.get("document_id") or "").strip() == current]
