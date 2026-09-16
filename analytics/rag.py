"""
analytics/rag.py — RAG query layer over embedded chunks.

Searches Reden chunks (reden_embeddings.duckdb, joined against reden.duckdb
for speaker/fraktion/date) and Drucksachen chunks (embeddings.duckdb),
merges results by cosine similarity score, then answers via Claude with
source citations.

Usage as a library:
    from analytics.rag import ask
    answer = ask("Was hat der Bundestag zur Klimapolitik beschlossen?")
    print(answer.text)
    for src in answer.sources:
        print(src)

Usage from CLI:
    python -m analytics.rag "Frage hier"
"""

import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

WAREHOUSE_PATH = os.getenv("DUCKDB_PATH", "warehouse.duckdb")
EMBEDDINGS_PATH = os.getenv("EMBEDDINGS_PATH", "embeddings.duckdb")
REDEN_PATH = os.getenv("REDEN_PATH", "reden.duckdb")
REDEN_EMBEDDINGS_PATH = os.getenv("REDEN_EMBEDDINGS_PATH", "reden_embeddings.duckdb")
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K = 8
CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_WAHLPERIODE = int(os.getenv("RAG_DEFAULT_WAHLPERIODE", "20"))


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)


class RetrievalError(RuntimeError):
    pass


def _get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


def _retrieve_reden(q_vec: list[float], top_k: int, wahlperiode: int | None) -> list[dict]:
    if not Path(REDEN_EMBEDDINGS_PATH).exists():
        raise RetrievalError(f"Reden embeddings database not found at '{REDEN_EMBEDDINGS_PATH}'")

    try:
        con = duckdb.connect(REDEN_EMBEDDINGS_PATH, read_only=True)
        con.execute("LOAD vss")
        con.execute(f"ATTACH '{REDEN_PATH}' AS reden (READ_ONLY)")
        con.execute(f"ATTACH '{WAREHOUSE_PATH}' AS warehouse (READ_ONLY)")
    except Exception as e:
        raise RetrievalError(
            f"Failed to open reden vector index at '{REDEN_EMBEDDINGS_PATH}' "
            f"(reden '{REDEN_PATH}', warehouse '{WAREHOUSE_PATH}'): {e}"
        ) from e

    try:
        # wahlperiode is passed twice: first placeholder checks NULL, second applies equality
        # in the SQL predicate "(? IS NULL OR column = ?)".
        params: list = [q_vec, wahlperiode, wahlperiode]
        rows = con.execute(f"""
            SELECT
                c.chunk_id,
                c.rede_id AS doc_id,
                r.redner_label AS speaker,
                r.fraktion,
                c.text,
                r.datum::VARCHAR AS datum,
                r.wahlperiode,
                p.pdf_url,
                array_cosine_similarity(c.embedding, ?::FLOAT[384]) AS score
            FROM rede_chunks c
            JOIN (
                SELECT rede_id,
                       ANY_VALUE(redner_label) AS redner_label,
                       ANY_VALUE(fraktion)     AS fraktion,
                       ANY_VALUE(datum)        AS datum,
                       ANY_VALUE(wahlperiode)  AS wahlperiode,
                       ANY_VALUE(protokoll_id) AS protokoll_id
                FROM reden.rede
                WHERE NOT ist_praesidium
                GROUP BY rede_id
            ) r ON r.rede_id = c.rede_id
            LEFT JOIN warehouse.plenarprotokoll p ON p.id = r.protokoll_id
            WHERE score > 0.3
              AND (? IS NULL OR r.wahlperiode = ?)
            ORDER BY score DESC
            LIMIT {top_k}
        """, params).fetchall()
    except Exception as e:
        raise RetrievalError(f"Failed to query rede chunks: {e}") from e
    finally:
        con.close()

    cols = ["chunk_id", "doc_id", "speaker", "fraktion", "text", "datum", "wahlperiode", "pdf_url", "score"]
    return [{"source_type": "plenarprotokoll", **dict(zip(cols, r))} for r in rows]


def _retrieve_drucksachen(q_vec: list[float], top_k: int, wahlperiode: int | None) -> list[dict]:
    if not Path(EMBEDDINGS_PATH).exists():
        raise RetrievalError(f"Embeddings database not found at '{EMBEDDINGS_PATH}'")

    try:
        con = duckdb.connect(EMBEDDINGS_PATH, read_only=True)
        con.execute("LOAD vss")
        con.execute(f"ATTACH '{WAREHOUSE_PATH}' AS warehouse (READ_ONLY)")
    except Exception as e:
        raise RetrievalError(
            f"Failed to open drucksache vector index at '{EMBEDDINGS_PATH}' (warehouse '{WAREHOUSE_PATH}'): {e}"
        ) from e

    try:
        # wahlperiode is passed twice: first placeholder checks NULL, second applies equality
        # in the SQL predicate "(? IS NULL OR column = ?)".
        params: list = [q_vec, wahlperiode, wahlperiode]
        rows = con.execute(f"""
            SELECT
                c.chunk_id,
                c.doc_id,
                c.text,
                d.titel,
                d.drucksachetyp,
                d.datum::VARCHAR  AS datum,
                d.wahlperiode,
                d.pdf_url,
                array_cosine_similarity(c.embedding, ?::FLOAT[384]) AS score
            FROM drucksache_chunks c
            JOIN warehouse.drucksache d ON d.id = c.doc_id
            WHERE score > 0.3
              AND (? IS NULL OR d.wahlperiode = ?)
            ORDER BY score DESC
            LIMIT {top_k}
        """, params).fetchall()
    except Exception as e:
        raise RetrievalError(f"Failed to query drucksache chunks: {e}") from e
    finally:
        con.close()

    cols = ["chunk_id", "doc_id", "text", "titel", "drucksachetyp", "datum", "wahlperiode", "pdf_url", "score"]
    return [{"source_type": "drucksache", "speaker": None, "fraktion": None, **dict(zip(cols, r))} for r in rows]


def retrieve(question: str, top_k: int = TOP_K, wahlperiode: int | None = None) -> list[dict]:
    if wahlperiode is None:
        wahlperiode = DEFAULT_WAHLPERIODE
    model = _get_model()
    q_vec = model.encode([question], normalize_embeddings=True)[0].tolist()

    results = (
        _retrieve_reden(q_vec, top_k, wahlperiode)
        + _retrieve_drucksachen(q_vec, top_k, wahlperiode)
    )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


def _discover_wahlperioden() -> list[int]:
    """List every Wahlperiode present in either the Drucksachen or Reden data.

    Used as the default scope for retrieve_timeline()/ask_timeline() so
    that backfilling more Wahlperioden (e.g. a future pre-WP19 extension)
    automatically widens timeline coverage without any code change here.
    """
    wps: set[int] = set()

    if Path(WAREHOUSE_PATH).exists():
        con = duckdb.connect(WAREHOUSE_PATH, read_only=True)
        try:
            wps.update(
                r[0] for r in con.execute(
                    "SELECT DISTINCT wahlperiode FROM drucksache WHERE wahlperiode IS NOT NULL"
                ).fetchall()
            )
        finally:
            con.close()

    if Path(REDEN_PATH).exists():
        con = duckdb.connect(REDEN_PATH, read_only=True)
        try:
            wps.update(
                r[0] for r in con.execute(
                    "SELECT DISTINCT wahlperiode FROM rede WHERE wahlperiode IS NOT NULL"
                ).fetchall()
            )
        finally:
            con.close()

    return sorted(wps)


def retrieve_timeline(
    question: str,
    wahlperioden: list[int] | None = None,
    top_k_per_period: int = 5,
) -> dict[int, list[dict]]:
    """Retrieve chunks bucketed per Wahlperiode instead of one flat top-K.

    A single flat retrieve() over all WPs returns whichever chunks score
    highest overall, which tends to cluster around whichever period
    discussed the topic most - not an even sample across time. This buckets
    retrieval per WP so a "how did X change over time" synthesis has
    material from every period, not just the loudest one.
    """
    if wahlperioden is None:
        wahlperioden = _discover_wahlperioden()
    if not wahlperioden:
        return {}

    model = _get_model()
    q_vec = model.encode([question], normalize_embeddings=True)[0].tolist()

    timeline: dict[int, list[dict]] = {}
    for wp in wahlperioden:
        chunks = (
            _retrieve_reden(q_vec, top_k_per_period, wp)
            + _retrieve_drucksachen(q_vec, top_k_per_period, wp)
        )
        chunks.sort(key=lambda r: r["score"], reverse=True)
        chunks = chunks[:top_k_per_period]
        if chunks:
            timeline[wp] = chunks
    return timeline


def retrieve_timeline_sources(
    question: str,
    wahlperioden: list[int] | None = None,
    top_k_per_period: int = 5,
) -> dict[int, list[dict]]:
    timeline = retrieve_timeline(question, wahlperioden=wahlperioden, top_k_per_period=top_k_per_period)
    return {wp: _sources_from_chunks(chunks) for wp, chunks in timeline.items()}


def retrieve_sources(question: str, top_k: int = TOP_K, wahlperiode: int | None = None) -> list[dict]:
    chunks = retrieve(question, top_k=top_k, wahlperiode=wahlperiode)
    return _sources_from_chunks(chunks)


def _sources_from_chunks(chunks: list[dict]) -> list[dict]:
    return [{
        "chunk_id": c["chunk_id"],
        "doc_id": c["doc_id"],
        "source_type": c["source_type"],
        "speaker": c.get("speaker"),
        "fraktion": c.get("fraktion"),
        "titel": c.get("titel"),
        "datum": c["datum"],
        "wahlperiode": c["wahlperiode"],
        "pdf_url": c["pdf_url"],
        "score": round(c["score"], 3),
        "snippet": c["text"][:200],
    } for c in chunks]


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        if c["source_type"] == "plenarprotokoll":
            header = (
                f"[{i}] Plenarprotokoll {c['doc_id']} vom {c['datum']} (WP {c['wahlperiode']})"
            )
            if c.get("speaker"):
                # speaker (redner_label) already reads e.g. "Vogel (FDP):" —
                # the fraktion is repeated as a separate structured field for
                # callers, not duplicated here.
                header += f"\nRedner: {c['speaker']}"
        else:
            header = (
                f"[{i}] {c.get('drucksachetyp', 'Drucksache')} {c['doc_id']}"
                f" — {c.get('titel', '')} vom {c['datum']} (WP {c['wahlperiode']})"
            )
        parts.append(f"{header}\n{c['text'][:800]}")
    return "\n\n---\n\n".join(parts)


def _format_timeline_context(timeline: dict[int, list[dict]]) -> str:
    """Like _format_context, but grouped under a Wahlperiode header per period.

    Citation numbers [N] run continuously across the whole flattened set (in
    WP order) so they line up 1:1 with the flattened `sources` list
    ask_timeline() returns.
    """
    sections = []
    index = 1
    for wp in sorted(timeline):
        numbered = []
        for c in timeline[wp]:
            if c["source_type"] == "plenarprotokoll":
                header = f"[{index}] Plenarprotokoll {c['doc_id']} vom {c['datum']}"
                if c.get("speaker"):
                    header += f"\nRedner: {c['speaker']}"
            else:
                header = (
                    f"[{index}] {c.get('drucksachetyp', 'Drucksache')} {c['doc_id']}"
                    f" — {c.get('titel', '')} vom {c['datum']}"
                )
            numbered.append(f"{header}\n{c['text'][:800]}")
            index += 1
        sections.append(f"=== Wahlperiode {wp} ===\n\n" + "\n\n---\n\n".join(numbered))
    return "\n\n".join(sections)


def ask(question: str, wahlperiode: int | None = None) -> Answer:
    import anthropic

    chunks = retrieve(question, wahlperiode=wahlperiode)
    if not chunks:
        return Answer(text="Keine relevanten Quellen gefunden.")

    context = _format_context(chunks)

    system = textwrap.dedent("""
        Du bist ein Assistent für parlamentarische Recherche.
        Beantworte die Frage ausschließlich auf Basis der bereitgestellten Auszüge aus
        Plenarprotokollen und Drucksachen des Deutschen Bundestages. Zitiere die Quellen mit [N].
        Falls die Auszüge keine ausreichende Grundlage bieten, sage das klar.
    """).strip()

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": f"Auszüge:\n\n{context}\n\nFrage: {question}"}],
    )

    sources = _sources_from_chunks(chunks)

    return Answer(text=message.content[0].text, sources=sources)


def ask_timeline(
    question: str,
    wahlperioden: list[int] | None = None,
    top_k_per_period: int = 5,
) -> Answer:
    """Answer a "how did X change over time" question with per-period retrieval.

    Unlike ask(), which does one flat top-K search, this buckets retrieval
    per Wahlperiode (retrieve_timeline()) so every period gets a fair chance
    to contribute evidence, then makes a single synthesis call over all
    periods' context asking for a chronological answer. One LLM call
    regardless of period count keeps cost flat for the WP19-21 scope this
    was built for; a much larger period count (e.g. a future pre-2017
    backfill) may need a two-stage map-reduce summary instead.
    """
    import anthropic

    timeline = retrieve_timeline(question, wahlperioden=wahlperioden, top_k_per_period=top_k_per_period)
    if not timeline:
        return Answer(text="Keine relevanten Quellen gefunden.")

    context = _format_timeline_context(timeline)
    periods = ", ".join(f"WP {wp}" for wp in sorted(timeline))

    system = textwrap.dedent(f"""
        Du bist ein Assistent für parlamentarische Recherche. Die Auszüge unten sind nach
        Wahlperiode gruppiert ({periods}), chronologisch aufsteigend. Beantworte die Frage,
        indem du die zeitliche Entwicklung über die Wahlperioden hinweg beschreibst — nicht nur
        eine einzelne Momentaufnahme. Zitiere die Quellen mit [N]. Falls die Auszüge keine
        ausreichende Grundlage für eine Entwicklung über Zeit bieten, sage das klar.
    """).strip()

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1536,
        system=system,
        messages=[{"role": "user", "content": f"Auszüge nach Wahlperiode:\n\n{context}\n\nFrage: {question}"}],
    )

    sources = [source for wp in sorted(timeline) for source in _sources_from_chunks(timeline[wp])]

    return Answer(text=message.content[0].text, sources=sources)


if __name__ == "__main__":
    import sys
    import json
    question = " ".join(sys.argv[1:]) or "Was wurde zur Klimapolitik debattiert?"
    result = ask(question)
    print(result.text)
    print("\n--- Quellen ---")
    for s in result.sources:
        label = s["titel"] or s["doc_id"]
        print(f"  [{s['source_type']}] {label}  score={s['score']}  {s['snippet'][:80]}…")
