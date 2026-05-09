"""
analytics/extract.py — PDF extraction + embedding pipeline for Drucksachen.

Downloads PDFs from dserver.bundestag.de, extracts text with PyMuPDF,
chunks, embeds with sentence-transformers, and stores vectors in embeddings.duckdb.

Resumable: already-processed doc IDs in extraction_log are skipped on re-run.

Usage:
    python -m analytics.extract
    python -m analytics.extract --workers 16 --batch 128
    python -m analytics.extract --warehouse /path/to/warehouse.duckdb --embeddings /path/to/embeddings.duckdb
"""

import argparse
import asyncio
import logging
from pathlib import Path

import duckdb
import fitz  # PyMuPDF
import httpx
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

WAREHOUSE_PATH = "warehouse.duckdb"
EMBEDDINGS_PATH = "embeddings.duckdb"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
DEFAULT_WORKERS = 8
DEFAULT_BATCH = 256


def setup_db(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("INSTALL vss; LOAD vss")
    con.execute("""
        CREATE TABLE IF NOT EXISTS drucksache_chunks (
            chunk_id    VARCHAR PRIMARY KEY,
            doc_id      VARCHAR NOT NULL,
            chunk_index INTEGER NOT NULL,
            text        TEXT    NOT NULL,
            embedding   FLOAT[384] NOT NULL,
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS extraction_log (
            doc_id       VARCHAR PRIMARY KEY,
            status       VARCHAR NOT NULL,
            chunks       INTEGER,
            extracted_at TIMESTAMPTZ DEFAULT now(),
        )
    """)
    return con


def get_pending(warehouse: str, con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    wh = duckdb.connect(warehouse, read_only=True)
    all_docs = wh.execute(
        "SELECT id, pdf_url FROM drucksache WHERE pdf_url IS NOT NULL AND pdf_url != ''"
    ).fetchall()
    wh.close()

    done = {row[0] for row in con.execute("SELECT doc_id FROM extraction_log").fetchall()}
    pending = [(id_, url) for id_, url in all_docs if id_ not in done]
    logger.info("%d total docs, %d already done, %d pending", len(all_docs), len(done), len(pending))
    return pending


def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) >= 50]


async def fetch_pdf(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    retries: int = 2,
) -> bytes | None:
    async with sem:
        for attempt in range(retries + 1):
            try:
                r = await client.get(url, timeout=30)
                r.raise_for_status()
                return r.content
            except Exception as e:
                if attempt == retries:
                    logger.debug("Fetch failed %s: %s", url, e)
                    return None
                await asyncio.sleep(1)
    return None


def extract_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc).strip()


async def process_batch(
    batch: list[tuple[str, str]],
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    model: SentenceTransformer,
    con: duckdb.DuckDBPyConnection,
) -> tuple[int, int]:
    pdfs = await asyncio.gather(*[fetch_pdf(client, sem, url) for _, url in batch])

    chunk_rows: list[tuple] = []
    log_rows: list[tuple] = []
    all_chunks: list[str] = []
    chunk_meta: list[tuple[str, int]] = []  # (doc_id, local_chunk_index)

    for (doc_id, _), pdf_bytes in zip(batch, pdfs):
        if pdf_bytes is None:
            log_rows.append((doc_id, "failed", 0))
            continue
        try:
            text = extract_text(pdf_bytes)
        except Exception as e:
            logger.warning("Extract failed %s: %s", doc_id, e)
            log_rows.append((doc_id, "failed", 0))
            continue

        chunks = chunk_text(text)
        if not chunks:
            log_rows.append((doc_id, "empty", 0))
            continue

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            chunk_meta.append((doc_id, i))
        log_rows.append((doc_id, "ok", len(chunks)))

    if all_chunks:
        embeddings = model.encode(all_chunks, normalize_embeddings=True, show_progress_bar=False)
        for (doc_id, i), chunk, emb in zip(chunk_meta, all_chunks, embeddings):
            chunk_rows.append((f"{doc_id}_{i}", doc_id, i, chunk, emb.tolist()))

    if chunk_rows:
        con.executemany(
            "INSERT OR IGNORE INTO drucksache_chunks VALUES (?, ?, ?, ?, ?)",
            chunk_rows,
        )
    if log_rows:
        con.executemany(
            "INSERT OR REPLACE INTO extraction_log(doc_id, status, chunks) VALUES (?, ?, ?)",
            log_rows,
        )

    ok = sum(1 for _, s, _ in log_rows if s == "ok")
    return ok, len(log_rows) - ok


async def run(warehouse: str, embeddings_path: str, workers: int, batch_size: int) -> None:
    con = setup_db(embeddings_path)
    model = SentenceTransformer(EMBED_MODEL)
    pending = get_pending(warehouse, con)

    if not pending:
        logger.info("Nothing to do.")
        con.close()
        return

    total = len(pending)
    total_ok = total_failed = 0
    sem = asyncio.Semaphore(workers)

    async with httpx.AsyncClient(
        headers={"User-Agent": "bundeswarehouse-indexer/1.0"},
        follow_redirects=True,
    ) as client:
        for i in range(0, total, batch_size):
            batch = pending[i : i + batch_size]
            ok, failed = await process_batch(batch, client, sem, model, con)
            total_ok += ok
            total_failed += failed
            logger.info(
                "[%d/%d] ok=%d failed=%d (cumulative ok=%d failed=%d)",
                min(i + batch_size, total), total, ok, failed, total_ok, total_failed,
            )

    logger.info("Extraction done. Building HNSW index...")
    con.execute("""
        CREATE INDEX IF NOT EXISTS drucksache_chunks_emb_idx
        ON drucksache_chunks USING HNSW (embedding)
        WITH (metric = 'cosine')
    """)
    logger.info("Index built. Total: ok=%d failed=%d", total_ok, total_failed)
    con.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    parser = argparse.ArgumentParser(description="Extract and embed Drucksachen PDFs.")
    parser.add_argument("--warehouse", default=WAREHOUSE_PATH)
    parser.add_argument("--embeddings", default=EMBEDDINGS_PATH)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent downloads")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="Docs per processing batch")
    args = parser.parse_args()
    asyncio.run(run(args.warehouse, args.embeddings, args.workers, args.batch))


if __name__ == "__main__":
    main()
