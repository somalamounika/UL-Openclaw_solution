import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ul import openclaw_blob_service, ul_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/openclaw", tags=["OpenClaw Ingest"])


class BuildGraphRequest(BaseModel):
    metadata_blob: str = Field(..., description="Blob path to department metadata.json")


@router.get("/runs")
def list_openclaw_runs():
    """List triage department runs from Azure Blob Storage (newest first)."""
    if not openclaw_blob_service.azure_blob_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure Blob Storage is not configured (AZURE_BLOB_CONTAINER, AZURE_BLOB_CONNECTION_STRING).",
        )
    try:
        runs = openclaw_blob_service.list_department_runs()
        return {"status": "ok", "count": len(runs), "runs": runs}
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Install azure-storage-blob in the backend venv: pip install azure-storage-blob",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to list OpenClaw runs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runs/build-graph")
def build_graph_from_openclaw_run(body: BuildGraphRequest):
    """
    Download attachments for one department run and run the standard UL graph pipeline.
    """
    if not openclaw_blob_service.azure_blob_configured():
        raise HTTPException(status_code=503, detail="Azure Blob Storage is not configured.")

    metadata_blob = (body.metadata_blob or "").strip()
    if not metadata_blob or not metadata_blob.endswith("metadata.json"):
        raise HTTPException(status_code=400, detail="metadata_blob must point to a metadata.json blob path.")

    try:
        with tempfile.TemporaryDirectory(prefix="openclaw-ingest-") as tmp:
            tmp_dir = Path(tmp)
            file_paths = openclaw_blob_service.download_run_attachments(metadata_blob, tmp_dir)
            result = ul_service.process_documents(file_paths=file_paths)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OpenClaw build-graph failed for %s", metadata_blob)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
