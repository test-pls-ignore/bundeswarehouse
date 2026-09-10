"""
analytics/embed_reden.py — chunk + embed Reden text (reden.duckdb's `rede`
table) for semantic search, mirroring analytics.extract's Drucksachen
pipeline but without a PDF-download step: the text already lives in
reden.duckdb.

Chunks are stored in a dedicated reden_embeddings.duckdb, kept separate from
reden.duckdb for the same reason embeddings.duckdb is kept separate from
warehouse.duckdb: reden_extract.yml regenerates reden.duckdb wholesale on
every run, so embeddings stored inside it would be wiped every time.

Each rede_id's segments (minus chair interruptions) are joined back into one
full speech before chunking, so a speech interrupted mid-sentence still
embeds as continuous text.

Resumable via rede_embed_log, keyed on rede_id + a content hash — `rede` and
reden_log carry no `aktualisiert` timestamp to diff against.

Usage:
    python -m analytics.embed_reden --reden reden.duckdb --embeddings reden_embeddings.duckdb
    python -m analytics.embed_reden --wahlperiode 20 --limit 20   # smoke run
"""

import argparse
import hashlib
import logging
import os

import duckdb

logger = logging.getLogger(__name__)

REDEN_PATH = "reden.duckdb"
EMBEDDINGS_PATH = "reden_embeddings.duckdb"
DEFAULT_BATCH = 256
# Kept identical to, but intentionally not imported from, analytics.extract:
# that module pulls in PyMuPDF/httpx at import time for its PDF-download
# path, which this module has no use for.
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

PendingRede = tuple[str, str, str]  # (rede_id, full_text, text_hash)


def setup_db(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    # See analytics.extract.setup_db for why this exists: an unbounded HNSW
    # build can OOM-kill co-located services on the shared VPS.
    memory_max = os.environ.get("MEMORY_MAX")
    if memory_max:
        con.execute(f"SET memory_limit='{memory_max}'")
    con.execute("INSTALL vss; LOAD vss")
    con.execute("""
        CREATE TABLE IF NOT EXISTS rede_chunks (
            chunk_id    VARCHAR PRIMARY KEY,
            rede_id     VARCHAR NOT NULL,
            chunk_index INTEGER NOT NULL,
            text        TEXT    NOT NULL,
            embedding   FLOAT[384] NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS rede_embed_log (
            rede_id      VARCHAR PRIMARY KEY,
            status       VARCHAR NOT NULL,
            chunks       INTEGER,
            text_hash    VARCHAR,
            embedded_at  TIMESTAMPTZ DEFAULT now()
        )
    """)
    return con


def build_hnsw_index(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET hnsw_enable_experimental_persistence = true")
    con.execute("""
        CREATE INDEX IF NOT EXISTS rede_chunks_emb_idx
        ON rede_chunks USING HNSW (embedding)
        WITH (metric = 'cosine')
    """)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) >= 50]


def get_pending(
    reden_path: str,
    con: duckdb.DuckDBPyConnection,
    wahlperiode: int | None,
) -> list[PendingRede]:
    rw = duckdb.connect(reden_path, read_only=True)
    wp_filter = "AND wahlperiode = ?" if wahlperiode is not None else ""
    rows = rw.execute(
        f"""
        SELECT rede_id, string_agg(text, chr(10) ORDER BY segment_index) AS full_text
        FROM rede
        WHERE NOT ist_praesidium {wp_filter}
        GROUP BY rede_id
        """,
        [wahlperiode] if wahlperiode is not None else [],
    ).fetchall()
    rw.close()

    done = {
        row[0]: row[1]
        for row in con.execute(
            "SELECT rede_id, text_hash FROM rede_embed_log WHERE status = 'ok'"
        ).fetchall()
    }

    pending: list[PendingRede] = []
    for rede_id, full_text in rows:
        if not full_text:
            continue
        text_hash = _text_hash(full_text)
        if done.get(rede_id) == text_hash:
            continue
        pending.append((rede_id, full_text, text_hash))

    logger.info(
        "%d reden total, %d already embedded & unchanged, %d pending (WP=%s)",
        len(rows),
        len(rows) - len(pending),
        len(pending),
        wahlperiode if wahlperiode is not None else "ALL",
    )
    return pending


def embed_batch(batch: list[PendingRede], model, con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    chunk_rows: list[tuple] = []
    log_rows: list[tuple] = []
    all_chunks: list[str] = []
    chunk_meta: list[tuple[str, int]] = []

    for rede_id, text, text_hash in batch:
        chunks = chunk_text(text)
        if not chunks:
            log_rows.append((rede_id, "empty", 0, text_hash))
            continue
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            chunk_meta.append((rede_id, i))
        log_rows.append((rede_id, "ok", len(chunks), text_hash))

    if all_chunks:
        embeddings = model.encode(all_chunks, normalize_embeddings=True, show_progress_bar=False)
        for (rede_id, i), chunk, emb in zip(chunk_meta, all_chunks, embeddings):
            chunk_rows.append((f"{rede_id}_{i}", rede_id, i, chunk, emb.tolist()))

    replaced_ids = sorted({rede_id for rede_id, status, *_ in log_rows if status == "ok"})
    if replaced_ids:
        con.executemany("DELETE FROM rede_chunks WHERE rede_id = ?", [(r,) for r in replaced_ids])
    if chunk_rows:
        con.executemany("INSERT OR REPLACE INTO rede_chunks VALUES (?, ?, ?, ?, ?)", chunk_rows)
    if log_rows:
        con.executemany(
            "INSERT OR REPLACE INTO rede_embed_log(rede_id, status, chunks, text_hash) VALUES (?, ?, ?, ?)",
            log_rows,
        )

    ok = sum(1 for _, status, *_ in log_rows if status == "ok")
    return ok, len(log_rows) - ok


def run(
    reden_path: str,
    embeddings_path: str,
    wahlperiode: int | None,
    batch_size: int,
    limit: int | None,
    build_index: bool = True,
) -> None:
    from sentence_transformers import SentenceTransformer

    con = setup_db(embeddings_path)
    model = SentenceTransformer(EMBED_MODEL)
    pending = get_pending(reden_path, con, wahlperiode=wahlperiode)
    if limit is not None:
        pending = pending[:limit]

    if not pending:
        logger.info("Nothing to do.")
        con.close()
        return

    total = len(pending)
    total_ok = total_failed = 0
    for i in range(0, total, batch_size):
        batch = pending[i : i + batch_size]
        ok, failed = embed_batch(batch, model, con)
        total_ok += ok
        total_failed += failed
        logger.info(
            "[%d/%d] ok=%d failed=%d (cumulative ok=%d failed=%d)",
            min(i + batch_size, total), total, ok, failed, total_ok, total_failed,
        )

    if build_index:
        logger.info("Embedding done. Building HNSW index...")
        build_hnsw_index(con)
        logger.info("Index built.")
    logger.info("Total: ok=%d failed=%d", total_ok, total_failed)
    con.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    parser = argparse.ArgumentParser(description="Chunk and embed Reden text for semantic search.")
    parser.add_argument("--reden", default=REDEN_PATH)
    parser.add_argument("--embeddings", default=EMBEDDINGS_PATH)
    parser.add_argument("--wahlperiode", type=int, default=None, help="Filter by Wahlperiode (default: all).")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="Reden per embedding batch.")
    parser.add_argument("--limit", type=int, default=None, help="Max reden to process (smoke test).")
    parser.add_argument("--skip-index", action="store_true", help="Skip HNSW index creation after embedding.")
    args = parser.parse_args()

    run(
        args.reden,
        args.embeddings,
        args.wahlperiode,
        args.batch,
        args.limit,
        build_index=not args.skip_index,
    )


if __name__ == "__main__":
    main()
