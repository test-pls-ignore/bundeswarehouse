"""
analytics/extract.py — PDF extraction + embedding pipeline for Drucksachen.

Downloads linked PDFs, extracts text with PyMuPDF, chunks + embeds with
sentence-transformers, and stores vectors in embeddings.duckdb.

Resumable with retry metadata in extraction_log:
- already-processed docs are skipped
- failed docs can be retried
- changed pdf_url/aktualisiert values are reprocessed

Usage:
    python -m analytics.extract
    # defaults to WP 20; pass --wahlperiode to change scope
    python -m analytics.extract --wahlperiode 20
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
DEFAULT_WAHLPERIODE = 20
DEFAULT_MAX_ATTEMPTS = 3


def setup_db(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("INSTALL vss; LOAD vss")
    con.execute("""
        CREATE TABLE IF NOT EXISTS drucksache_chunks (
            chunk_id    VARCHAR PRIMARY KEY,
            doc_id      VARCHAR NOT NULL,
            chunk_index INTEGER NOT NULL,
            text        TEXT    NOT NULL,
            embedding   FLOAT[384] NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS extraction_log (
            doc_id       VARCHAR PRIMARY KEY,
            status       VARCHAR NOT NULL,
            chunks       INTEGER,
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   VARCHAR,
            last_pdf_url VARCHAR,
            last_aktualisiert VARCHAR,
            extracted_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    for migration in [
        "ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS last_error VARCHAR",
        "ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS last_pdf_url VARCHAR",
        "ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS last_aktualisiert VARCHAR",
    ]:
        con.execute(migration)
    return con


def _truncate_error(err: str, limit: int = 400) -> str:
    return err if len(err) <= limit else f"{err[:limit]}..."


def get_pending(
    warehouse: str,
    con: duckdb.DuckDBPyConnection,
    wahlperiode: int | None,
    max_attempts: int,
) -> list[tuple[str, str, str | None, int]]:
    wh = duckdb.connect(warehouse, read_only=True)
    wp_filter = "AND wahlperiode = ?" if wahlperiode is not None else ""
    all_docs = wh.execute(
        f"""
        SELECT id, pdf_url, aktualisiert::VARCHAR AS aktualisiert
        FROM drucksache
        WHERE pdf_url IS NOT NULL AND pdf_url != ''
        {wp_filter}
        ORDER BY aktualisiert DESC NULLS LAST
        """,
        [wahlperiode] if wahlperiode is not None else [],
    ).fetchall()
    wh.close()

    log_rows = con.execute(
        """
        SELECT doc_id, status, attempts, last_pdf_url, last_aktualisiert
        FROM extraction_log
        """
    ).fetchall()
    logs = {
        row[0]: {
            "status": row[1],
            "attempts": int(row[2] or 0),
            "last_pdf_url": row[3],
            "last_aktualisiert": row[4],
        }
        for row in log_rows
    }

    deduped_docs: list[tuple[str, str, str | None]] = []
    seen_urls: set[str] = set()
    for doc_id, url, aktualisiert in all_docs:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped_docs.append((doc_id, url, aktualisiert))

    pending: list[tuple[str, str, str | None, int]] = []
    for doc_id, url, aktualisiert in deduped_docs:
        entry = logs.get(doc_id)
        if entry is None:
            pending.append((doc_id, url, aktualisiert, 0))
            continue
        signature_changed = (
            entry["last_pdf_url"] != url
            or entry["last_aktualisiert"] != aktualisiert
        )
        if entry["status"] == "ok" and not signature_changed:
            continue
        if entry["attempts"] >= max_attempts and not signature_changed:
            continue
        pending.append((doc_id, url, aktualisiert, int(entry["attempts"])))

    already_done = sum(1 for row in logs.values() if row["status"] == "ok")
    logger.info(
        "%d total docs (%d deduped by URL), %d already done, %d pending (WP=%s)",
        len(all_docs),
        len(deduped_docs),
        already_done,
        len(pending),
        wahlperiode if wahlperiode is not None else "ALL",
    )
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
    batch: list[tuple[str, str, str | None, int]],
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    model: SentenceTransformer,
    con: duckdb.DuckDBPyConnection,
) -> tuple[int, int]:
    pdfs = await asyncio.gather(*[fetch_pdf(client, sem, url) for _, url, _, _ in batch])

    chunk_rows: list[tuple] = []
    log_rows: list[tuple] = []
    all_chunks: list[str] = []
    chunk_meta: list[tuple[str, int]] = []  # (doc_id, local_chunk_index)
    # Only docs with successful fresh chunks should replace existing chunk rows.
    docs_to_replace: set[str] = set()

    for (doc_id, url, aktualisiert, previous_attempts), pdf_bytes in zip(batch, pdfs):
        attempts = previous_attempts + 1
        if pdf_bytes is None:
            log_rows.append((doc_id, "failed", 0, attempts, "download_failed", url, aktualisiert))
            continue
        try:
            text = extract_text(pdf_bytes)
        except Exception as e:
            logger.warning("Extract failed %s: %s", doc_id, e)
            log_rows.append((doc_id, "failed", 0, attempts, _truncate_error(str(e)), url, aktualisiert))
            continue

        chunks = chunk_text(text)
        if not chunks:
            log_rows.append((doc_id, "empty", 0, attempts, "empty_text", url, aktualisiert))
            continue

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            chunk_meta.append((doc_id, i))
        docs_to_replace.add(doc_id)
        log_rows.append((doc_id, "ok", len(chunks), attempts, None, url, aktualisiert))

    if all_chunks:
        embeddings = model.encode(all_chunks, normalize_embeddings=True, show_progress_bar=False)
        for (doc_id, i), chunk, emb in zip(chunk_meta, all_chunks, embeddings):
            chunk_rows.append((f"{doc_id}_{i}", doc_id, i, chunk, emb.tolist()))

    if chunk_rows:
        con.executemany(
            "DELETE FROM drucksache_chunks WHERE doc_id = ?",
            [(doc_id,) for doc_id in sorted(docs_to_replace)],
        )
        con.executemany(
            "INSERT OR REPLACE INTO drucksache_chunks VALUES (?, ?, ?, ?, ?)",
            chunk_rows,
        )
    if log_rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO extraction_log(
                doc_id, status, chunks, attempts, last_error, last_pdf_url, last_aktualisiert
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            log_rows,
        )

    ok = sum(1 for _, status, *_ in log_rows if status == "ok")
    return ok, len(log_rows) - ok


async def run(
    warehouse: str,
    embeddings_path: str,
    workers: int,
    batch_size: int,
    wahlperiode: int | None,
    max_attempts: int,
) -> None:
    con = setup_db(embeddings_path)
    model = SentenceTransformer(EMBED_MODEL)
    pending = get_pending(warehouse, con, wahlperiode=wahlperiode, max_attempts=max_attempts)

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
    parser.add_argument("--wahlperiode", type=int, default=DEFAULT_WAHLPERIODE, help="Filter by Wahlperiode (default: 20).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent downloads")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="Docs per processing batch")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Skip unchanged failed docs after this many attempts (default: 3).",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            args.warehouse,
            args.embeddings,
            args.workers,
            args.batch,
            args.wahlperiode,
            args.max_attempts,
        )
    )


if __name__ == "__main__":
    main()
