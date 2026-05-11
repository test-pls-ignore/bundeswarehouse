"""
analytics/server.py — FastAPI server exposing the RAG ask() function.

Usage:
    uvicorn analytics.server:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from analytics.rag import ask, retrieve_sources

app = FastAPI(title="bundeswarehouse RAG API")


class AskRequest(BaseModel):
    question: str
    wahlperiode: int | None = None


class SearchRequest(BaseModel):
    question: str
    wahlperiode: int | None = None
    top_k: int = 8


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest):
    try:
        sources = retrieve_sources(req.question, top_k=req.top_k, wahlperiode=req.wahlperiode)
        return {"sources": sources}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
