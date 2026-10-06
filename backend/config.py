import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# Always load backend/.env, regardless of the process working directory.
# Do not import backend/key.py — credentials come from this file only.
load_dotenv(BASE_DIR / ".env")
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
REFERENCE_DOCUMENTS_DIR = BASE_DIR / "documents"
# Preferred order when present. Any other PDF/DOCX/email in this folder is also used.
REFERENCE_DOCUMENT_NAMES = (
    "standards.pdf",
    "certifications_tests.pdf",
)

# OpenAI-compatible (default) or Azure OpenAI
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

# Azure OpenAI (used when AZURE_OPENAI_API_KEY is set)
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_VERSION = os.getenv(
    "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
).strip()
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "").strip()
# Optional faster deployment for structured mapping (standards/clauses/certs).
# Falls back to AZURE_OPENAI_CHAT_DEPLOYMENT / LLM_MODEL when unset.
AZURE_OPENAI_MAPPING_DEPLOYMENT = os.getenv(
    "AZURE_OPENAI_MAPPING_DEPLOYMENT", ""
).strip()
LLM_MAPPING_MODEL = os.getenv("LLM_MAPPING_MODEL", "").strip()
MAPPING_CONCURRENCY = max(
    1, int(os.getenv("MAPPING_CONCURRENCY", "4").strip() or "4")
)
EXTRACTION_CONCURRENCY = max(
    1, int(os.getenv("EXTRACTION_CONCURRENCY", "4").strip() or "4")
)
_max_std_raw = os.getenv("MAX_STANDARDS_PER_COMPONENT", "").strip()
MAX_STANDARDS_PER_COMPONENT = int(_max_std_raw) if _max_std_raw else None
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv(
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""
).strip()
# Optional separate embedding resource; falls back to chat Azure OpenAI creds.
AZURE_OPENAI_EMBEDDING_ENDPOINT = os.getenv(
    "AZURE_OPENAI_EMBEDDING_ENDPOINT", ""
).strip() or AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_EMBEDDING_API_KEY = os.getenv(
    "AZURE_OPENAI_EMBEDDING_API_KEY", ""
).strip() or AZURE_OPENAI_API_KEY
AZURE_OPENAI_EMBEDDING_API_VERSION = os.getenv(
    "AZURE_OPENAI_EMBEDDING_API_VERSION", ""
).strip() or AZURE_OPENAI_API_VERSION
AZURE_OPENAI_EMBEDDING_DIMENSIONS = int(
    os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536").strip() or "1536"
)

# LightRAG working directory (chunk embeddings + LightRAG stores)
_lightrag_working_dir = os.getenv("LIGHTRAG_WORKING_DIR", "").strip()
LIGHTRAG_WORKING_DIR = (
    Path(_lightrag_working_dir) if _lightrag_working_dir else BASE_DIR / "rag_storage"
)
if not LIGHTRAG_WORKING_DIR.is_absolute():
    LIGHTRAG_WORKING_DIR = (BASE_DIR / LIGHTRAG_WORKING_DIR).resolve()

# Azure AI Search (semantic chunk index) — optional; local in-memory fallback is used otherwise
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
AZURE_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY", "").strip()
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "ul-chunks").strip()

# Local Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687").strip()
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j").strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()
NEO4J_USE_ROUTING = os.getenv("NEO4J_USE_ROUTING", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def neo4j_driver_uri() -> str:
    """Use bolt:// for single-instance Neo4j; neo4j:// requires cluster routing."""
    uri = NEO4J_URI.strip()
    if NEO4J_USE_ROUTING:
        return uri
    if uri.startswith("neo4j://"):
        return "bolt://" + uri[len("neo4j://") :]
    return uri

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://127.0.0.1:5174").split(",")
    if origin.strip()
]
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").strip().upper()

# OpenClaw triage archives (Azure Blob)
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "openclaw-ul").strip() or "openclaw-ul"
AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING", "").strip()


def azure_search_configured() -> bool:
    """Return True when Azure AI Search credentials are present."""
    return bool(AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY and AZURE_SEARCH_INDEX_NAME)


def mapping_chat_model() -> str | None:
    """Optional mapping-stage model/deployment; None uses the main chat model."""
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
        return AZURE_OPENAI_MAPPING_DEPLOYMENT or None
    return LLM_MAPPING_MODEL or None


UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
