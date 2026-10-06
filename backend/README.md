# UL Knowledge Graph Backend

FastAPI backend for the UL compliance knowledge graph pipeline:

**PDF/DOCX/email → page extraction → semantic chunking → component extraction → standards/clauses/certs/tests → hierarchy → CSV → local Neo4j**

Azure AI Search is optional. If it is not configured, chunks stay in memory and retrieval uses lexical ranking.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` with your LLM API key and local Neo4j credentials. Azure Search settings are optional.

## Run

```powershell
uvicorn main:app --reload --port 8000
```

Open Swagger UI: http://localhost:8000/docs

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ul/upload` | Upload company/product PDF, DOCX, or email files |
| POST | `/api/ul/process` | Run pipeline (user docs + backend reference docs) then ingest into local Neo4j |
| GET | `/api/ul/graph` | Get graph statistics and nodes/edges from local Neo4j |
| GET | `/health` | Health check |

### Upload / process inputs

Upload company/supplier/product **PDF, DOCX, or email** files (1 or more).

Backend automatically includes every supported file in `backend/documents/`
(for example `standards.pdf` and `certifications_tests.pdf`). Those catalogues
are used for **graph extraction** (standards, certifications, tests) and for
chatbot retrieval.

## Project Structure

```
backend/
├── main.py                 # FastAPI app entry point
├── config.py               # Environment configuration
├── ul/
│   ├── ul_routes.py        # API routes
│   ├── ul_service.py       # Pipeline orchestration + Neo4j ingest
│   ├── document_chunking.py
│   ├── component_extractor.py
│   ├── llm_client.py
│   ├── llm_extractor.py
│   ├── hierarchy.py        # Local hierarchy normalization
│   ├── triplet_generator.py
│   ├── csv_generator.py
│   └── neo4j_service.py    # Local Neo4j ingest / graph summary
├── uploads/                # Saved source-document uploads
└── output/                 # Generated CSV files
```
