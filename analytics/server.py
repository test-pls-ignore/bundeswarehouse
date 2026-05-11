"""
analytics/server.py — FastAPI server exposing the RAG ask() function.

Usage:
    uvicorn analytics.server:app --host 0.0.0.0 --port 8000
"""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from analytics.rag import RetrievalError, ask, retrieve_sources

app = FastAPI(title="bundeswarehouse RAG API")
logger = logging.getLogger(__name__)
MAX_SEARCH_TOP_K = 50


class AskRequest(BaseModel):
    question: str
    wahlperiode: int | None = None


class SearchRequest(BaseModel):
    question: str
    wahlperiode: int | None = None
    top_k: int = Field(default=8, ge=1, le=MAX_SEARCH_TOP_K)


class Source(BaseModel):
    chunk_id: str
    doc_id: str
    source_type: str
    speaker: str | None
    titel: str | None
    datum: str
    wahlperiode: int
    pdf_url: str | None
    score: float
    snippet: str


class AskResponse(BaseModel):
    text: str
    sources: list[Source]


class SearchResponse(BaseModel):
    sources: list[Source]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest):
    try:
        answer = ask(req.question, req.wahlperiode)
        return {"text": answer.text, "sources": answer.sources}
    except RetrievalError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error in /ask")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest):
    try:
        sources = retrieve_sources(req.question, top_k=req.top_k, wahlperiode=req.wahlperiode)
        return {"sources": sources}
    except RetrievalError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error in /search")
        raise HTTPException(status_code=500, detail="Internal server error") from e
