import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from ul.chatbot import router as chatbot_router
from ul.openclaw_routes import router as openclaw_router
from ul.ul_routes import router as ul_router

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="UL Knowledge Graph API",
    description="Upload PDF, DOCX, or email files, extract entities/relationships, and build a Neo4j knowledge graph.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ul_router)
app.include_router(openclaw_router)
app.include_router(chatbot_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
