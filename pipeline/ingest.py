"""
Enhanced ingest module for bundeswarehouse.

Fetches data from the Bundestag DIP API, serialises each resource batch
as newline-delimited JSON (NDJSON), uploads it to MinIO under raw/,
and updates the manifest and state.

Environment variables (in addition to S3_* vars):
  BUNDESTAG_API_KEY  - DIP API key
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from pipeline.manifest import record_object, sha256_of_bytes
from pipeline.state import clear_cursor, update_cursor, update_last_seen
from pipeline.storage import PREFIX_RAW, upload_bytes

logger = logging.getLogger(__name__)

API_BASE = "https://search.dip.bundestag.de/api/v1"

# Resources to ingest (in order)
RESOURCES = ["vorgang", "drucksache", "plenarprotokoll", "aktivitaet"]


def load_api_key() -> str:
    """Load the Bundestag API key from env or fall back to api_key.txt."""
    key = os.environ.get("BUNDESTAG_API_KEY")
    if key:
        return key
    try:
        with open("api_key.txt", "r") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        logger.error(
            "BUNDESTAG_API_KEY env var not set and api_key.txt not found."
        )
        sys.exit(1)


def fetch_page(
    resource: str,
    api_key: str,
    cursor: Optional[str] = None,
    limit: int = 100,
    updated_after: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Fetch one page of results from the DIP API.

    Returns (documents, next_cursor).
    next_cursor is None when no more pages are available.
    """
    headers = {"Authorization": f"ApiKey {api_key}"}
    url = f"{API_BASE}/{resource}"
    params: Dict[str, Any] = {"format": "json", "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if updated_after:
        params["f.datum.start"] = updated_after

    try:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        documents = data.get("documents", [])
        next_cursor = data.get("cursor") or None
        return documents, next_cursor
    except requests.RequestException as exc:
        logger.error("Error fetching %s: %s", resource, exc)
        return [], None


def _make_s3_key(resource: str, batch_index: int) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{PREFIX_RAW}{resource}/{date_str}/batch_{batch_index:05d}.ndjson"


def ingest_resource(
    resource: str,
    api_key: str,
    client,
    bucket: str,
    state: Dict[str, Any],
    manifest: Dict[str, Any],
    incremental: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Fetch all pages of a resource, upload each batch to MinIO, and update state/manifest.

    Returns (updated_state, updated_manifest).
    """
    updated_after = state.get("last_seen_update") if incremental else None
    cursor = state.get("cursors", {}).get(resource) if incremental else None
    batch_index = 0
    total_docs = 0

    logger.info(
        "Ingesting resource '%s' (incremental=%s, cursor=%s, updated_after=%s).",
        resource,
        incremental,
        cursor,
        updated_after,
    )

    while True:
        docs, next_cursor = fetch_page(
            resource, api_key, cursor=cursor, updated_after=updated_after
        )

        if not docs:
            logger.info("No documents returned for '%s', stopping.", resource)
            break

        # Serialise as NDJSON
        ndjson_bytes = (
            "\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n"
        ).encode()
        checksum = sha256_of_bytes(ndjson_bytes)
        key = _make_s3_key(resource, batch_index)

        upload_bytes(client, bucket, key, ndjson_bytes, content_type="application/x-ndjson")
        manifest = record_object(manifest, key, len(ndjson_bytes), checksum)

        total_docs += len(docs)
        batch_index += 1

        # Track newest update timestamp seen
        for doc in docs:
            doc_date = doc.get("aktualisiert") or doc.get("datum")
            if doc_date and (
                state.get("last_seen_update") is None
                or doc_date > state["last_seen_update"]
            ):
                state = update_last_seen(state, doc_date)

        if next_cursor:
            state = update_cursor(state, resource, next_cursor)
            cursor = next_cursor
        else:
            # All pages consumed for this resource – clear saved cursor
            state = clear_cursor(state, resource)
            break

    logger.info("Finished '%s': %d documents in %d batches.", resource, total_docs, batch_index)
    return state, manifest


def run_full_load(
    client,
    bucket: str,
    state: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run a full ingest of all resources."""
    api_key = load_api_key()
    for resource in RESOURCES:
        state, manifest = ingest_resource(
            resource, api_key, client, bucket, state, manifest, incremental=False
        )
    return state, manifest


def run_incremental(
    client,
    bucket: str,
    state: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run an incremental ingest, resuming from saved cursors / last_seen_update."""
    api_key = load_api_key()
    for resource in RESOURCES:
        state, manifest = ingest_resource(
            resource, api_key, client, bucket, state, manifest, incremental=True
        )
    return state, manifest
