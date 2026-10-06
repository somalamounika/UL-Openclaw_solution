"""List OpenClaw triage archives and download attachments from Azure Blob Storage."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

KNOWN_ORG_TOKENS = frozenset(
    {
        "apple",
        "samsung",
        "amphenol",
        "cisco",
        "dell",
        "sony",
        "intel",
        "microsoft",
        "google",
        "amazon",
        "siemens",
        "bosch",
        "ge",
        "honeywell",
        "3m",
        "ul",
        "affine",
    }
)

COMMON_WEBMAIL = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "proton.me",
        "live.com",
    }
)


def azure_blob_configured() -> bool:
    return bool(config.AZURE_BLOB_CONNECTION_STRING.strip())


def _container_client():
    from azure.storage.blob import BlobServiceClient

    if not azure_blob_configured():
        raise RuntimeError("Azure Blob Storage is not configured")
    client = BlobServiceClient.from_connection_string(config.AZURE_BLOB_CONNECTION_STRING)
    return client.get_container_client(config.AZURE_BLOB_CONTAINER)


def resolve_client_name(
    metadata: dict[str, Any] | None,
    overview: dict[str, Any] | None,
    sender_raw: str = "",
    filenames: list[str] | None = None,
) -> str:
    metadata = metadata or {}
    overview = overview or {}
    filenames = filenames or []

    candidate = metadata.get("client_name") or overview.get("client_name")
    if isinstance(candidate, str) and candidate.strip().lower() not in ("unknown", "n/a", "none", ""):
        return candidate.strip()

    sender = sender_raw or metadata.get("from") or overview.get("from") or ""

    domain_match = re.search(r"@([\w.-]+)", sender)
    if domain_match:
        domain = domain_match.group(1).lower()
        if domain not in COMMON_WEBMAIL:
            org = domain.split(".")[0]
            if org:
                return org.capitalize() if org.islower() else org

    name_match = re.match(r"^([^<@]+)", sender)
    if name_match:
        clean_name = name_match.group(1).strip().strip('"').strip("'")
        if clean_name and clean_name.lower() not in ("unknown", "service", "admin", "noreply", "no-reply"):
            return clean_name

    for attachment in filenames:
        name = attachment
        if isinstance(attachment, dict):
            name = attachment.get("original_filename") or attachment.get("filename") or ""
        base = re.sub(r"^[0-9a-fA-F]{12,}_", "", str(name)).split(".")[0]
        tokens = [t for t in re.split(r"[_\s-]+", base) if len(t) > 2]
        for token in tokens:
            if token.lower() in KNOWN_ORG_TOKENS:
                return token.capitalize() if token.islower() else token
        if tokens:
            first = tokens[0]
            if first.lower() not in ("new", "pdf", "document", "report", "file", "test", "sample"):
                return first.capitalize()

    body = metadata.get("body_snippet") or overview.get("subject") or ""
    for org in ("Apple", "Samsung", "Amphenol", "UL", "General Electric", "Siemens"):
        if org.lower() in body.lower():
            return org

    return "General Client"


def _safe_json(raw: bytes, blob_name: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("Skipping corrupt JSON at %s: %s", blob_name, exc)
        return None


def _attachment_filenames(attachments: list) -> list[str]:
    names: list[str] = []
    for item in attachments:
        if isinstance(item, dict):
            names.append(item.get("original_filename") or Path(str(item.get("blob_path") or "")).name)
        elif item:
            names.append(str(item))
    return [n for n in names if n]


def list_department_runs() -> list[dict[str, Any]]:
    """Return one table row per department metadata.json under date/email_* layout."""
    container = _container_client()
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for blob in container.list_blobs():
        name = blob.name
        if not name.endswith("/metadata.json"):
            continue
        parts = name.split("/")
        if len(parts) < 4:
            continue
        date_folder, email_folder, dept_folder = parts[-4], parts[-3], parts[-2]
        if not email_folder.startswith("email_"):
            continue
        if name in seen:
            continue
        seen.add(name)

        metadata = _safe_json(container.download_blob(name).readall(), name)
        if not metadata:
            continue

        overview_path = f"{date_folder}/{email_folder}/email_overview.json"
        overview = None
        try:
            overview = _safe_json(container.download_blob(overview_path).readall(), overview_path)
        except Exception:
            overview = None

        attachments = metadata.get("attachments")
        if not isinstance(attachments, list):
            attachments = []

        intent_block = metadata.get("intent_analysis") if isinstance(metadata.get("intent_analysis"), dict) else {}
        intent = intent_block.get("primary_intent") or metadata.get("primary_intent") or "General Inquiry"

        archived_at = metadata.get("archived_at") or (overview.get("processed_at") if overview else None)
        sort_key = archived_at or f"{date_folder}T00:00:00"
        file_names = _attachment_filenames(attachments)

        runs.append(
            {
                "id": metadata.get("dispatch_id") or name,
                "metadata_blob": name,
                "department_blob_prefix": "/".join(parts[:-1]),
                "date_folder": date_folder,
                "email_folder": email_folder,
                "department_folder": dept_folder,
                "client_name": resolve_client_name(metadata, overview, metadata.get("from") or "", file_names),
                "message_id": metadata.get("message_id"),
                "category": metadata.get("category") or dept_folder.replace("_", " "),
                "doc_count": len(attachments),
                "intent": intent,
                "recipient": metadata.get("recipient"),
                "subject": metadata.get("subject"),
                "body_snippet": metadata.get("body_snippet") or "",
                "executive_summary": metadata.get("executive_summary") or [],
                "recommended_action": metadata.get("recommended_action") or "",
                "intent_analysis": intent_block,
                "attachments": attachments,
                "archived_at": archived_at,
                "email_overview": overview,
                "_sort_key": sort_key,
            }
        )

    runs.sort(key=lambda item: item.get("_sort_key") or "", reverse=True)
    for item in runs:
        item.pop("_sort_key", None)
    return runs


def download_run_attachments(metadata_blob: str, dest_dir: Path) -> list[Path]:
    """Download attachment blobs for one department run into dest_dir."""
    container = _container_client()
    metadata = _safe_json(container.download_blob(metadata_blob).readall(), metadata_blob)
    if not metadata:
        raise ValueError(f"Could not read metadata: {metadata_blob}")

    attachments = metadata.get("attachments") or []
    if not attachments:
        raise ValueError("This run has no attachments to process")

    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        blob_path = attachment.get("blob_path")
        if not blob_path:
            continue
        local_name = Path(str(blob_path)).name
        if not local_name:
            local_name = attachment.get("original_filename") or "attachment.bin"
        local_path = dest_dir / re.sub(r"[^\w.\- ]", "_", local_name)
        data = container.download_blob(blob_path).readall()
        local_path.write_bytes(data)
        saved.append(local_path)

    if not saved:
        raise ValueError("Failed to download any attachment blobs")
    return saved


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
