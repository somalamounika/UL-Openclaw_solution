# UL OpenClaw

**Compliance knowledge graph + OpenClaw triage ingest.**  
Engineered by AFFINE

Upload PDFs (or pull archived email triage runs from Azure Blob Storage) → process → build and explore a product knowledge graph with an assistant.

## Prerequisites

- Python 3.11+ (3.12 recommended)
- Node.js 20+
- Neo4j (local or Aura)
- Azure OpenAI (chat + embeddings) for full pipeline; optional Azure AI Search and Azure Blob for OpenClaw archives

## Local development

Default host and ports:

| Service   | URL |
|-----------|-----|
| Frontend  | http://127.0.0.1:5174 |
| Backend   | http://127.0.0.1:8000 |
| API docs  | http://127.0.0.1:8000/docs |

### Backend

```powershell
cd backend
python -m venv .venv_ul
.\.venv_ul\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env: Neo4j, Azure OpenAI, optional Search + Blob (OpenClaw)
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Restart Uvicorn after changing `.env` (settings load at startup).

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5174. Vite proxies `/api` and `/health` to the backend on port 8000.

`VITE_API_BASE_URL` is only needed when serving a production build from a different origin than the API.

## Environment

Copy `backend/.env.example` → `backend/.env`. Never commit `.env` (see `.gitignore`).

| Area | Variables |
|------|-----------|
| LLM | `AZURE_OPENAI_*` or `LLM_*` |
| Graph DB | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` |
| Chunk search (optional) | `AZURE_SEARCH_*` |
| OpenClaw archives | `AZURE_BLOB_CONTAINER`, `AZURE_BLOB_CONNECTION_STRING` |
| CORS | `CORS_ORIGINS` — must include the frontend origin (default `http://127.0.0.1:5174`) |

## App flow

1. **Upload** — load demo documents or drag PDFs, then **Build Knowledge Graph**.
2. **Graph** — explore nodes, evidence, and relationships.
3. **OpenClaw** — list triage runs from blob storage and build a graph from a run's attachments.
4. **Assistant** — hybrid RAG chat grounded on retrieved evidence.

## Repository layout

```
backend/     FastAPI, Neo4j, LightRAG, OpenClaw blob ingest
frontend/    React + Vite UI
docs/        Architecture and chatbot notes
```
