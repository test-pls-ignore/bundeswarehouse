"""
analytics/mcp_server.py — MCP server exposing the bundeswarehouse dataset to
an LLM client (Claude Desktop/Code) via two tools:

  - query_sql: read-only SQL over warehouse.duckdb (+ reden.duckdb attached
    as `reden`), for aggregate/time-series questions ("word share per
    fraktion per month").
  - search: semantic search over embedded document/speech chunks (existing
    RAG index), for pulling up literal quotes as citation evidence.

Deployment: runs on the VPS, bound to 127.0.0.1 (like the existing `rag`
service and MinIO — see docker-compose.yml). Reach it from elsewhere via an
SSH tunnel:

    ssh -L 8765:127.0.0.1:8765 <user>@<vps-host>

then point an MCP client at http://127.0.0.1:8765/mcp (Streamable HTTP).

Usage:
    python -m analytics.mcp_server
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

import duckdb
from mcp.server import MCPServer

logger = logging.getLogger(__name__)

WAREHOUSE_PATH = os.getenv("DUCKDB_PATH", "warehouse.duckdb")
REDEN_PATH = os.getenv("REDEN_PATH", "reden.duckdb")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8765"))
MAX_ROWS = 500

# Only SELECT/WITH may start the statement, and none of these keywords may
# appear anywhere in it. Belt-and-suspenders alongside read_only=True: the
# read_only flag stops writes to the attached database files themselves, but
# doesn't rule out filesystem side effects (COPY TO a file, EXPORT DATABASE,
# loading arbitrary extensions via INSTALL/LOAD) or session state changes
# (SET, PRAGMA) that have nothing to do with the data itself.
_READ_ONLY_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|COPY|EXPORT|"
    r"IMPORT|PRAGMA|CALL|INSTALL|LOAD|SET|VACUUM)\b",
    re.IGNORECASE,
)


def _open_connection() -> duckdb.DuckDBPyConnection:
    if not Path(WAREHOUSE_PATH).exists():
        raise RuntimeError(f"warehouse not found at '{WAREHOUSE_PATH}'")
    con = duckdb.connect(WAREHOUSE_PATH, read_only=True)
    if Path(REDEN_PATH).exists():
        con.execute(f"ATTACH '{REDEN_PATH}' AS reden (READ_ONLY)")
    else:
        logger.warning("reden.duckdb not found at '%s' — reden.* tables unavailable.", REDEN_PATH)
    return con


def validate_read_only(sql: str) -> None:
    """Raise ValueError if *sql* is not a plain read-only SELECT/WITH query."""
    if not _READ_ONLY_START.match(sql):
        raise ValueError("Only SELECT or WITH statements are allowed.")
    forbidden = _FORBIDDEN_KEYWORDS.search(sql)
    if forbidden:
        raise ValueError(f"Query contains a forbidden keyword: {forbidden.group(0)!r}")


mcp = MCPServer("bundeswarehouse")


@mcp.tool()
def query_sql(sql: str) -> list[dict[str, Any]]:
    """Run a read-only SQL (SELECT/WITH only) query over the Bundestag warehouse.

    Two attached databases:

    main (warehouse.duckdb) — one row per DIP API entity:
      vorgang(id, vorgangstyp, wahlperiode, beratungsstand, titel, abstract,
        datum, aktualisiert, initiative, sachgebiet, deskriptor)
      drucksache(id, drucksachetyp, dokumentnummer, wahlperiode, herausgeber,
        titel, datum, aktualisiert, autoren_anzahl, vorgangsbezug_anzahl,
        autoren_anzeige, urheber, vorgangsbezug, pdf_url)
      plenarprotokoll(id, dokumentnummer, wahlperiode, datum, aktualisiert,
        titel, herausgeber, vorgangsbezug_anzahl, sitzungsbemerkung, pdf_url,
        xml_url) -- herausgeber is 'BT' (Bundestag) or 'BR' (Bundesrat); only
        'BT' rows have xml_url and therefore rows in reden.rede.
      aktivitaet(id, aktivitaetsart, person_id, wahlperiode, datum,
        aktualisiert, person_name, vorgangsbezug_anzahl, vorgangsbezug,
        pdf_url, drucksachetyp, urheber)
      person(id, vorname, nachname, titel, fraktion, funktion, wahlperiode,
        aktualisiert, basisdatum, datum) -- fraktion/funktion/wahlperiode are
        LISTs (a person's fraktion can change across Wahlperioden).

    reden (reden.duckdb) — structured speeches parsed from Plenarprotokoll
    XML, one row per contiguous speaker segment (a speech interrupted by the
    chair yields multiple ordered segments):
      reden.rede(id, rede_id, protokoll_id, dokumentnummer, wahlperiode,
        datum, segment_index, redner_id, titel, vorname, nachname, fraktion,
        rolle, redner_label, ist_praesidium, text, wortanzahl)
        -- ist_praesidium=true for chair interruptions (redner_id NULL then).
        -- fraktion is NULL for government members, who have `rolle` instead
           (e.g. 'Bundesministerin BMF').
        -- redner_id is the Bundestag's own speaker id from the XML, NOT the
           same id space as person.id — there is no join between them yet.
      reden.reden_log(protokoll_id, status, reden, segmente, attempts,
        last_error, last_xml_url, last_aktualisiert, extracted_at)
        -- status='ok' means protokoll_id was successfully parsed.

    Typical query: word count per fraktion per month —
      SELECT date_trunc('month', datum) AS monat, fraktion, sum(wortanzahl)
      FROM reden.rede WHERE NOT ist_praesidium GROUP BY 1, 2 ORDER BY 1, 2

    Results are capped at 500 rows; add your own LIMIT/aggregation for
    larger questions instead of relying on this cap.
    """
    validate_read_only(sql)
    con = _open_connection()
    try:
        result = con.execute(sql)
        columns = [d[0] for d in result.description]
        rows = result.fetchmany(MAX_ROWS)
        return [dict(zip(columns, row)) for row in rows]
    finally:
        con.close()


@mcp.tool()
def search(question: str, wahlperiode: int | None = None, top_k: int = 8) -> list[dict[str, Any]]:
    """Semantic search over embedded Plenarprotokoll/Drucksache chunks.

    Use this to find literal quotes/citations for a topic (e.g. "Was wurde
    zur Klimapolitik gesagt?"). For aggregate or time-series questions about
    fraktion/person behaviour, use query_sql instead — this tool ranks by
    embedding similarity, not by structured fields.

    wahlperiode defaults to RAG_DEFAULT_WAHLPERIODE (currently 20) if not
    given. top_k caps the number of returned chunks (max 50).
    """
    from analytics.rag import retrieve_sources

    top_k = max(1, min(top_k, 50))
    return retrieve_sources(question, top_k=top_k, wahlperiode=wahlperiode)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logger.info("Starting bundeswarehouse MCP server on %s:%d ...", MCP_HOST, MCP_PORT)
    mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
