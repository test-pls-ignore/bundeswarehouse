"""
analytics/reden.py — structured speech extraction from Plenarprotokoll XML.

Bundestag plenary protocols (WP >= 19) are published as structured XML
(fundstelle.xml_url in DIP) with per-speech tagging: <rede> elements contain
<redner id=...> (the official MdB/speaker id) with name, fraktion and rolle,
speech paragraphs (<p klasse="J">...), interjections (<kommentar>), and
presiding-officer interruptions (<name>Präsidentin ...:</name>).

This module downloads the XML transiently, parses each <rede> into
speaker-attributed segments, and stores them in a DuckDB file (default:
reden.duckdb) as the `rede` table — the central fact table for
"who said what, when, for which fraktion" analyses over time.

Resumable via `reden_log`, mirroring analytics/extract.py:
- already-processed protocols are skipped
- failed protocols are retried up to --max-attempts
- changed xml_url/aktualisiert values are reprocessed

Usage:
    python -m analytics.reden --warehouse warehouse.duckdb --reden reden.duckdb
    python -m analytics.reden --wahlperiode 20
    python -m analytics.reden --limit 5          # smoke run on 5 protocols
"""

import argparse
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import duckdb
import httpx

logger = logging.getLogger(__name__)

WAREHOUSE_PATH = "warehouse.duckdb"
REDEN_PATH = "reden.duckdb"
DEFAULT_WORKERS = 4
DEFAULT_MAX_ATTEMPTS = 3
COMMIT_BATCH = 20

PendingProtokoll = tuple[str, str, int, str, str, Optional[str], int]
# (id, dokumentnummer, wahlperiode, datum, xml_url, aktualisiert, attempts)

_WHITESPACE_RE = re.compile(r"\s+")
# Bundestag's own Stammdaten sometimes carry stale/merged <fraktion> text for a
# redner id (observed e.g. for id 11005304, whose <name> block concatenates
# two different MdBs after one replaced the other under the same id). The
# trailing "(FRAKTION):" in the speaker label is authored fresh per speech and
# doesn't share that failure mode, so prefer it over the structured field.
_FRAKTION_IN_LABEL_RE = re.compile(r"\(([^()]+)\)\s*:?\s*$")


@dataclass
class RedeSegment:
    """One contiguous, speaker-attributed block of spoken text inside a <rede>."""

    rede_id: str
    segment_index: int
    redner_id: Optional[str]
    titel: Optional[str]
    vorname: Optional[str]
    nachname: Optional[str]
    fraktion: Optional[str]
    rolle: Optional[str]
    redner_label: Optional[str]
    ist_praesidium: bool
    text: str


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    return cleaned or None


def _element_text(el: ET.Element) -> str:
    return _clean("".join(el.itertext())) or ""


def _parse_redner(redner_el: Optional[ET.Element]) -> dict:
    """Extract speaker attributes from a <redner> element (may be malformed)."""
    info: dict = {
        "redner_id": None,
        "titel": None,
        "vorname": None,
        "nachname": None,
        "fraktion": None,
        "rolle": None,
    }
    if redner_el is None:
        return info
    info["redner_id"] = _clean(redner_el.get("id"))
    name_el = redner_el.find("name")
    if name_el is None:
        return info
    info["titel"] = _clean(name_el.findtext("titel"))
    info["vorname"] = _clean(name_el.findtext("vorname"))
    nachname = _clean(name_el.findtext("nachname"))
    namenszusatz = _clean(name_el.findtext("namenszusatz"))
    if nachname and namenszusatz:
        nachname = f"{namenszusatz} {nachname}"
    info["nachname"] = nachname or namenszusatz
    info["fraktion"] = _clean(name_el.findtext("fraktion"))
    rolle_el = name_el.find("rolle")
    if rolle_el is not None:
        info["rolle"] = _clean(rolle_el.findtext("rolle_kurz")) or _clean(
            rolle_el.findtext("rolle_lang")
        )
    return info


def parse_protokoll_xml(xml_bytes: bytes) -> list[RedeSegment]:
    """Parse a WP>=19 dbtplenarprotokoll XML document into speech segments.

    Segmentation rules within each <rede>:
    - <p klasse="redner"> switches the current speaker to the embedded <redner>.
    - <name> switches to the presiding officer (no redner id available).
    - Other <p> elements accumulate as text for the current speaker.
    - <kommentar> (interjections/applause) is skipped entirely.
    Every speaker switch flushes the accumulated text as one segment, so a
    speech interrupted by the chair yields multiple ordered segments.
    """
    root = ET.fromstring(xml_bytes)
    segments: list[RedeSegment] = []

    for rede_el in root.iter("rede"):
        rede_id = rede_el.get("id") or ""
        segment_index = 0
        current: Optional[dict] = None
        paragraphs: list[str] = []

        def flush() -> None:
            nonlocal segment_index, paragraphs
            if current is None:
                paragraphs = []
                return
            text = "\n".join(p for p in paragraphs if p)
            paragraphs = []
            if not text:
                return
            segments.append(
                RedeSegment(
                    rede_id=rede_id,
                    segment_index=segment_index,
                    text=text,
                    **current,
                )
            )
            segment_index += 1

        for child in rede_el:
            tag = child.tag
            if tag == "p" and child.get("klasse") == "redner":
                flush()
                redner_el = child.find("redner")
                info = _parse_redner(redner_el)
                label = None
                if redner_el is not None:
                    label = _clean(redner_el.tail)
                if not label:
                    label = _clean(
                        "".join(
                            part
                            for el in child
                            if el is not redner_el
                            for part in el.itertext()
                        )
                    )
                if label and info["fraktion"] is not None:
                    # Only *correct* an existing (possibly corrupted) value —
                    # speakers who legitimately have no fraktion (government
                    # members, Land representatives, interpreters) keep None
                    # rather than having one invented from their label.
                    label_match = _FRAKTION_IN_LABEL_RE.search(label)
                    if label_match:
                        label_fraktion = _clean(label_match.group(1))
                        if label_fraktion and label_fraktion != info["fraktion"]:
                            info["fraktion"] = label_fraktion
                current = {**info, "redner_label": label, "ist_praesidium": False}
            elif tag == "name":
                flush()
                current = {
                    "redner_id": None,
                    "titel": None,
                    "vorname": None,
                    "nachname": None,
                    "fraktion": None,
                    "rolle": None,
                    "redner_label": _element_text(child) or None,
                    "ist_praesidium": True,
                }
            elif tag == "kommentar":
                continue
            elif tag == "p":
                paragraph = _element_text(child)
                if paragraph:
                    paragraphs.append(paragraph)
            # anything else (<a>, <fussnote>, ...) is ignored

        flush()

    return segments


def setup_db(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS rede (
            id             VARCHAR PRIMARY KEY,
            rede_id        VARCHAR NOT NULL,
            protokoll_id   VARCHAR NOT NULL,
            dokumentnummer VARCHAR,
            wahlperiode    INTEGER,
            datum          DATE,
            segment_index  INTEGER NOT NULL,
            redner_id      VARCHAR,
            titel          VARCHAR,
            vorname        VARCHAR,
            nachname       VARCHAR,
            fraktion       VARCHAR,
            rolle          VARCHAR,
            redner_label   VARCHAR,
            ist_praesidium BOOLEAN NOT NULL,
            text           TEXT NOT NULL,
            wortanzahl     INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reden_log (
            protokoll_id      VARCHAR PRIMARY KEY,
            status            VARCHAR NOT NULL,
            reden             INTEGER,
            segmente          INTEGER,
            attempts          INTEGER NOT NULL DEFAULT 0,
            last_error        VARCHAR,
            last_xml_url      VARCHAR,
            last_aktualisiert VARCHAR,
            extracted_at      TIMESTAMPTZ DEFAULT now()
        )
    """)
    return con


def get_pending(
    warehouse: str,
    con: duckdb.DuckDBPyConnection,
    wahlperiode: Optional[int],
    max_attempts: int,
) -> list[PendingProtokoll]:
    wh = duckdb.connect(warehouse, read_only=True)
    wp_filter = "AND wahlperiode = ?" if wahlperiode is not None else ""
    try:
        rows = wh.execute(
            f"""
            SELECT id, dokumentnummer, wahlperiode, datum::VARCHAR,
                   xml_url, aktualisiert::VARCHAR
            FROM plenarprotokoll
            WHERE xml_url IS NOT NULL AND xml_url != ''
              AND herausgeber = 'BT'
            {wp_filter}
            ORDER BY datum
            """,
            [wahlperiode] if wahlperiode is not None else [],
        ).fetchall()
    except duckdb.Error as e:
        raise RuntimeError(
            f"Failed to read plenarprotokoll from '{warehouse}'. The snapshot may "
            "predate the xml_url/herausgeber columns — rebuild it with "
            "`python -m analytics.materialize`."
        ) from e
    finally:
        wh.close()

    log_rows = con.execute(
        "SELECT protokoll_id, status, attempts, last_xml_url, last_aktualisiert FROM reden_log"
    ).fetchall()
    logs = {
        row[0]: {
            "status": row[1],
            "attempts": int(row[2] or 0),
            "last_xml_url": row[3],
            "last_aktualisiert": row[4],
        }
        for row in log_rows
    }

    pending: list[PendingProtokoll] = []
    for pid, nummer, wp, datum, xml_url, aktualisiert in rows:
        entry = logs.get(pid)
        if entry is None:
            pending.append((pid, nummer, wp, datum, xml_url, aktualisiert, 0))
            continue
        signature_changed = (
            entry["last_xml_url"] != xml_url
            or entry["last_aktualisiert"] != aktualisiert
        )
        if entry["status"] == "ok" and not signature_changed:
            continue
        if entry["attempts"] >= max_attempts and not signature_changed:
            continue
        pending.append((pid, nummer, wp, datum, xml_url, aktualisiert, entry["attempts"]))

    done = sum(1 for row in logs.values() if row["status"] == "ok")
    logger.info(
        "%d BT protocols with xml_url, %d already done, %d pending (WP=%s)",
        len(rows), done, len(pending),
        wahlperiode if wahlperiode is not None else "ALL",
    )
    return pending


async def fetch_xml(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    retries: int = 2,
) -> Optional[bytes]:
    async with sem:
        for attempt in range(retries + 1):
            try:
                r = await client.get(url, timeout=60)
                r.raise_for_status()
                return r.content
            except Exception as e:
                if attempt == retries:
                    logger.debug("Fetch failed %s: %s", url, e)
                    return None
                await asyncio.sleep(1)
    return None


def _truncate_error(err: str, limit: int = 400) -> str:
    return err if len(err) <= limit else f"{err[: limit - 3]}..."


def store_protokoll(
    con: duckdb.DuckDBPyConnection,
    protokoll: PendingProtokoll,
    segments: list[RedeSegment],
) -> None:
    pid, nummer, wp, datum, xml_url, aktualisiert, previous_attempts = protokoll
    rede_rows = [
        (
            f"{pid}_{s.rede_id}_{s.segment_index:03d}",
            s.rede_id, pid, nummer, wp, datum,
            s.segment_index, s.redner_id, s.titel, s.vorname, s.nachname,
            s.fraktion, s.rolle, s.redner_label, s.ist_praesidium,
            s.text, len(s.text.split()),
        )
        for s in segments
    ]
    con.execute("DELETE FROM rede WHERE protokoll_id = ?", [pid])
    if rede_rows:
        con.executemany(
            "INSERT OR REPLACE INTO rede VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rede_rows,
        )
    reden_count = len({s.rede_id for s in segments})
    con.execute(
        """
        INSERT OR REPLACE INTO reden_log(
            protokoll_id, status, reden, segmente, attempts,
            last_error, last_xml_url, last_aktualisiert
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [pid, "ok" if segments else "empty", reden_count, len(rede_rows),
         previous_attempts + 1, None, xml_url, aktualisiert],
    )


def log_failure(
    con: duckdb.DuckDBPyConnection,
    protokoll: PendingProtokoll,
    error: str,
) -> None:
    pid, _, _, _, xml_url, aktualisiert, previous_attempts = protokoll
    con.execute(
        """
        INSERT OR REPLACE INTO reden_log(
            protokoll_id, status, reden, segmente, attempts,
            last_error, last_xml_url, last_aktualisiert
        ) VALUES (?, 'failed', 0, 0, ?, ?, ?, ?)
        """,
        [pid, previous_attempts + 1, _truncate_error(error), xml_url, aktualisiert],
    )


async def run(
    warehouse: str,
    reden_path: str,
    workers: int,
    wahlperiode: Optional[int],
    max_attempts: int,
    limit: Optional[int] = None,
) -> None:
    con = setup_db(reden_path)
    pending = get_pending(warehouse, con, wahlperiode=wahlperiode, max_attempts=max_attempts)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        logger.info("Nothing to do.")
        con.close()
        return

    total = len(pending)
    ok = failed = 0
    sem = asyncio.Semaphore(workers)

    async with httpx.AsyncClient(
        headers={"User-Agent": "bundeswarehouse-reden/1.0"},
        follow_redirects=True,
    ) as client:
        for i in range(0, total, COMMIT_BATCH):
            batch = pending[i : i + COMMIT_BATCH]
            payloads = await asyncio.gather(
                *[fetch_xml(client, sem, p[4]) for p in batch]
            )
            for protokoll, xml_bytes in zip(batch, payloads):
                pid = protokoll[0]
                if xml_bytes is None:
                    log_failure(con, protokoll, "download_failed")
                    failed += 1
                    continue
                try:
                    segments = parse_protokoll_xml(xml_bytes)
                except ET.ParseError as e:
                    logger.warning("XML parse failed for %s: %s", pid, e)
                    log_failure(con, protokoll, f"parse_error: {e}")
                    failed += 1
                    continue
                store_protokoll(con, protokoll, segments)
                ok += 1
            logger.info(
                "[%d/%d] cumulative ok=%d failed=%d",
                min(i + COMMIT_BATCH, total), total, ok, failed,
            )

    logger.info("Done. Total: ok=%d failed=%d", ok, failed)
    con.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Extract structured speeches from Plenarprotokoll XML.")
    parser.add_argument("--warehouse", default=WAREHOUSE_PATH)
    parser.add_argument("--reden", default=REDEN_PATH, help="Output DuckDB file (default: reden.duckdb)")
    parser.add_argument("--wahlperiode", type=int, default=None, help="Filter by Wahlperiode (default: all with XML).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent downloads")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                        help="Skip unchanged failed protocols after this many attempts (default: 3).")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N protocols (smoke runs).")
    args = parser.parse_args()

    asyncio.run(
        run(
            args.warehouse,
            args.reden,
            args.workers,
            args.wahlperiode,
            args.max_attempts,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
