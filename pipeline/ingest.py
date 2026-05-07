"""
Enhanced ingest module for bundeswarehouse.

Fetches data from the Bundestag DIP API, serialises each resource batch
as newline-delimited JSON (NDJSON), uploads it to MinIO under raw/,
and updates the manifest and state.

Environment variables (in addition to S3_* vars):
  BUNDESTAG_API_KEY       - DIP API key
  DIP_API_KEY_TRANSPORT   - "header" (default) or "query" (apikey param)
  DIP_USER_AGENT          - HTTP User-Agent header (default: bundeswarehouse/1.0 ...)
  DIP_REQUEST_DELAY       - Seconds to sleep between page requests (default: 0.5)
  DIP_MAX_RETRIES         - Max retry attempts for transient 401/429/5xx errors (default: 3)
  DIP_RETRY_BACKOFF_MAX   - Maximum backoff seconds between retries (default: 60)
  DIP_CHALLENGE_COOLDOWN_MIN - Minimum cooldown seconds after WAF challenge (default: 600)
  DIP_CHALLENGE_COOLDOWN_MAX - Maximum cooldown seconds after WAF challenge (default: 1800)
  DIP_INCREMENTAL_OVERLAP_MINUTES - Overlap window for incremental updates (default: 15, minimum: 15)
  DIP_MAX_CONCURRENCY     - Declared request concurrency cap (default: 1, maximum: 25)
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from pipeline.manifest import record_object, save_manifest, sha256_of_bytes
from pipeline.state import clear_cursor, clear_full_load, mark_full_load_resource_done, save_state, set_full_load_run, update_cursor, update_last_seen
from pipeline.storage import PREFIX_RAW, PREFIX_STAGING, PREFIX_CURRENT, copy_object, delete_prefix, list_prefix, upload_bytes

logger = logging.getLogger(__name__)

API_BASE = "https://search.dip.bundestag.de/api/v1"
CHALLENGE_PATH = "/.enodia/challenge"

# Resources to ingest (in order)
RESOURCES = ["vorgang", "drucksache", "plenarprotokoll", "aktivitaet"]

# Defaults for configurable parameters
DEFAULT_USER_AGENT = "bundeswarehouse/1.0 (+https://github.com/test-pls-ignore/bundeswarehouse)"
DEFAULT_REQUEST_DELAY = 0.5
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_MAX = 60.0
DEFAULT_CHALLENGE_COOLDOWN_MIN = 600.0
DEFAULT_CHALLENGE_COOLDOWN_MAX = 1800.0
DEFAULT_API_KEY_TRANSPORT = "header"
DEFAULT_INCREMENTAL_OVERLAP_MINUTES = 15
DEFAULT_MAX_CONCURRENCY = 1
MAX_ALLOWED_CONCURRENCY = 25

# Number of batches between periodic state+manifest checkpoints during a full load.
CHECKPOINT_INTERVAL = 50


class ChallengePageError(RuntimeError):
    """
    Raised when the DIP API returns a WAF/anti-bot challenge page instead of JSON.

    Attributes:
        url:          The originally requested URL.
        final_url:    The URL after any redirects.
        status_code:  HTTP status code of the response.
        content_type: Content-Type header of the response.
        body_snippet: First 200 characters of the response body.
    """

    def __init__(
        self,
        url: str,
        final_url: str,
        status_code: int,
        content_type: str,
        body_snippet: str,
    ) -> None:
        msg = (
            f"Challenge page detected – status={status_code}, "
            f"url={url!r}, final_url={final_url!r}, "
            f"content_type={content_type!r}, body={body_snippet!r}"
        )
        super().__init__(msg)
        self.url = url
        self.final_url = final_url
        self.status_code = status_code
        self.content_type = content_type
        self.body_snippet = body_snippet


def _get_config() -> Dict[str, Any]:
    """Read runtime config from environment variables."""
    api_key_transport = os.environ.get("DIP_API_KEY_TRANSPORT", DEFAULT_API_KEY_TRANSPORT).strip().lower()
    if api_key_transport not in {"header", "query"}:
        raise ValueError(
            "DIP_API_KEY_TRANSPORT must be either 'header' or 'query'."
        )

    max_concurrency = int(os.environ.get("DIP_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY))
    if max_concurrency < 1 or max_concurrency > MAX_ALLOWED_CONCURRENCY:
        raise ValueError(
            f"DIP_MAX_CONCURRENCY must be between 1 and {MAX_ALLOWED_CONCURRENCY}."
        )

    overlap_minutes = max(
        DEFAULT_INCREMENTAL_OVERLAP_MINUTES,
        int(
            os.environ.get(
                "DIP_INCREMENTAL_OVERLAP_MINUTES",
                DEFAULT_INCREMENTAL_OVERLAP_MINUTES,
            )
        ),
    )

    cooldown_min = float(
        os.environ.get("DIP_CHALLENGE_COOLDOWN_MIN", DEFAULT_CHALLENGE_COOLDOWN_MIN)
    )
    cooldown_max = float(
        os.environ.get("DIP_CHALLENGE_COOLDOWN_MAX", DEFAULT_CHALLENGE_COOLDOWN_MAX)
    )
    if cooldown_min <= 0 or cooldown_max <= 0:
        raise ValueError("DIP_CHALLENGE_COOLDOWN_MIN/MAX must be > 0.")
    if cooldown_min > cooldown_max:
        raise ValueError("DIP_CHALLENGE_COOLDOWN_MIN cannot be greater than MAX.")

    return {
        "user_agent": os.environ.get("DIP_USER_AGENT", DEFAULT_USER_AGENT),
        "request_delay": float(os.environ.get("DIP_REQUEST_DELAY", DEFAULT_REQUEST_DELAY)),
        "max_retries": int(os.environ.get("DIP_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
        "retry_backoff_max": float(os.environ.get("DIP_RETRY_BACKOFF_MAX", DEFAULT_RETRY_BACKOFF_MAX)),
        "challenge_cooldown_min": cooldown_min,
        "challenge_cooldown_max": cooldown_max,
        "api_key_transport": api_key_transport,
        "incremental_overlap_minutes": overlap_minutes,
        "max_concurrency": max_concurrency,
    }


def _make_session(user_agent: str) -> requests.Session:
    """Create a hardened requests session with cookies enabled and sensible headers."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Language": "de,en;q=0.9",
        }
    )
    return session


def _is_challenge_response(response: requests.Response) -> bool:
    """Return True if the response looks like a WAF/anti-bot challenge page."""
    if CHALLENGE_PATH in response.url:
        return True
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        return True
    return False


def _redact_url(url: str) -> str:
    """Return URL with sensitive query values (e.g. apikey) redacted."""
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    redacted = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "apikey" and value:
            redacted.append((key, "***"))
        else:
            redacted.append((key, value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(redacted, doseq=True), parts.fragment)
    )


def _apply_incremental_overlap(updated_after: Optional[str], overlap_minutes: int) -> Optional[str]:
    """Apply the DIP-recommended overlap window to incremental lower bounds."""
    if not updated_after:
        return None
    try:
        dt = datetime.fromisoformat(updated_after.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid last_seen_update timestamp %r, using as-is.", updated_after)
        return updated_after
    return (dt - timedelta(minutes=overlap_minutes)).isoformat()


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
    session: requests.Session,
    resource: str,
    api_key: str,
    cursor: Optional[str] = None,
    limit: int = 100,
    updated_after: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_max: float = DEFAULT_RETRY_BACKOFF_MAX,
    challenge_cooldown_min: float = DEFAULT_CHALLENGE_COOLDOWN_MIN,
    challenge_cooldown_max: float = DEFAULT_CHALLENGE_COOLDOWN_MAX,
    api_key_transport: str = DEFAULT_API_KEY_TRANSPORT,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Fetch one page of results from the DIP API.

    Returns (documents, next_cursor).
    next_cursor is None when no more pages are available.

    Raises:
        ChallengePageError: if retries are exhausted while receiving challenge pages.
        ValueError: if a non-JSON response is received from the API.
        requests.HTTPError: for unrecoverable HTTP errors after retries.
        requests.RequestException: for network-level errors after retries.
    """
    url = f"{API_BASE}/{resource}"
    params: Dict[str, Any] = {"format": "json", "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if updated_after:
        params["f.aktualisiert.start"] = updated_after

    if api_key_transport == "query":
        params["apikey"] = api_key
        auth_headers: Dict[str, str] = {}
    else:
        auth_headers = {"Authorization": f"ApiKey {api_key}"}
    last_exc: Optional[Exception] = None
    next_wait_seconds: Optional[float] = None

    for attempt in range(max_retries + 1):
        if attempt > 0 and next_wait_seconds is not None:
            logger.warning(
                "Retry %d/%d for %s after %.1fs backoff.",
                attempt,
                max_retries,
                resource,
                next_wait_seconds,
            )
            time.sleep(next_wait_seconds)
            next_wait_seconds = None

        try:
            response = session.get(url, headers=auth_headers, params=params, timeout=60)
        except requests.RequestException as exc:
            logger.error(
                "Network error for %s (attempt %d/%d, url=%r): %s",
                resource,
                attempt + 1,
                max_retries + 1,
                _redact_url(url),
                exc,
            )
            last_exc = exc
            next_wait_seconds = min(
                retry_backoff_max, (2 ** min(attempt + 1, 10)) + random.uniform(0, 1)
            )
            continue

        final_url = _redact_url(response.url)
        safe_url = _redact_url(url)
        content_type = response.headers.get("Content-Type", "")

        # Check for challenge page before inspecting the status code, because challenge
        # pages are sometimes served with 4xx codes that would otherwise be misleading.
        if _is_challenge_response(response):
            body_snippet = response.text[:200]
            retry_msg = "will retry" if attempt < max_retries else "retries exhausted"
            logger.warning(
                "Challenge page detected for %s: status=%d, url=%r, final_url=%r "
                "(attempt %d/%d); %s. body_snippet=%r",
                resource,
                response.status_code,
                safe_url,
                final_url,
                attempt + 1,
                max_retries + 1,
                retry_msg,
                body_snippet,
            )
            if attempt < max_retries:
                next_wait_seconds = random.uniform(
                    challenge_cooldown_min, challenge_cooldown_max
                )
                logger.warning(
                    "Applying WAF cooldown for %s: %.1fs before next retry.",
                    resource,
                    next_wait_seconds,
                )
            last_exc = ChallengePageError(
                url=safe_url,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                body_snippet=body_snippet,
            )
            continue

        # Retry on 401 (temporary auth/quota block), 429 (rate-limited), or any 5xx (server error).
        if response.status_code in (401, 429) or response.status_code >= 500:
            logger.warning(
                "Transient error for %s: status=%d, url=%r (attempt %d/%d), body_snippet=%r",
                resource,
                response.status_code,
                final_url,
                attempt + 1,
                max_retries + 1,
                response.text[:200],
            )
            last_exc = requests.HTTPError(response=response)
            next_wait_seconds = min(
                retry_backoff_max, (2 ** min(attempt + 1, 10)) + random.uniform(0, 1)
            )
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error(
                "HTTP error for %s: status=%d, url=%r, final_url=%r, content_type=%r, body_snippet=%r",
                resource,
                response.status_code,
                safe_url,
                final_url,
                content_type,
                response.text[:200],
            )
            raise

        # Validate that we actually received JSON (not a stealthy HTML redirect).
        if "application/json" not in content_type and "application/x-ndjson" not in content_type:
            body_snippet = response.text[:200]
            logger.error(
                "Non-JSON response for %s: status=%d, url=%r, final_url=%r, "
                "content_type=%r, body=%r",
                resource,
                response.status_code,
                safe_url,
                final_url,
                content_type,
                body_snippet,
            )
            raise ValueError(
                f"Expected JSON response for {resource!r}, got content_type={content_type!r}: "
                f"{body_snippet!r}"
            )

        data = response.json()
        documents = data.get("documents", [])
        next_cursor = data.get("cursor") or None
        return documents, next_cursor

    # All retry attempts exhausted.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"fetch_page for {resource!r} failed after {max_retries} retries")


def _make_s3_key(resource: str, batch_index: int, prefix: Optional[str] = None) -> str:
    """Return the S3 key for a batch file.

    If *prefix* is given, the key is ``{prefix}{resource}/batch_{batch_index:05d}.ndjson``
    (used for staging runs).  Otherwise the legacy date-partitioned layout under
    ``raw/`` is used.
    """
    if prefix is not None:
        return f"{prefix}{resource}/batch_{batch_index:05d}.ndjson"
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
    s3_prefix: Optional[str] = None,
    checkpoint_fn: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Fetch all pages of a resource, upload each batch to MinIO, and update state/manifest.

    If *s3_prefix* is given, objects are uploaded under that prefix (e.g.
    ``raw/_staging/<run_id>/``).  When *s3_prefix* is ``None``, the default
    date-partitioned layout under ``raw/`` is used.

    *checkpoint_fn*, if provided, is called with the current (state, manifest) every
    :data:`CHECKPOINT_INTERVAL` batches.  Use this to persist progress to S3 so that an
    interrupted full-load run can be resumed.

    Returns (updated_state, updated_manifest).

    Raises:
        ChallengePageError: if page requests remain blocked by challenge pages after retries.
        Other exceptions from fetch_page propagate after retries are exhausted.
    """
    config = _get_config()
    session = _make_session(config["user_agent"])
    request_delay = config["request_delay"]
    max_retries = config["max_retries"]
    retry_backoff_max = config["retry_backoff_max"]
    challenge_cooldown_min = config["challenge_cooldown_min"]
    challenge_cooldown_max = config["challenge_cooldown_max"]
    api_key_transport = config["api_key_transport"]
    incremental_overlap_minutes = config["incremental_overlap_minutes"]
    max_concurrency = config["max_concurrency"]

    logger.info(
        "DIP fetch mode: single-threaded (active request concurrency=1, configured cap=%d/%d).",
        max_concurrency,
        MAX_ALLOWED_CONCURRENCY,
    )

    updated_after = (
        _apply_incremental_overlap(
            state.get("last_seen_update"), incremental_overlap_minutes
        )
        if incremental
        else None
    )
    # Always read any saved cursor for this resource; for a fresh full load the cursor
    # will be None (cleared before the run starts), and for a resumed run it holds the
    # last confirmed position.
    cursor = state.get("cursors", {}).get(resource)

    # When resuming a partially-uploaded resource under a staging prefix, start
    # batch numbering after the objects that are already in S3.
    if s3_prefix is not None and cursor is not None:
        existing = list_prefix(client, bucket, f"{s3_prefix}{resource}/")
        batch_index = len(existing)
        logger.info(
            "Resuming resource '%s' at batch %d (cursor=%s).",
            resource, batch_index, cursor,
        )
    else:
        batch_index = 0

    total_docs = 0
    seen_doc_ids: Set[str] = set()

    logger.info(
        "Ingesting resource '%s' (incremental=%s, cursor=%s, updated_after=%s).",
        resource,
        incremental,
        cursor,
        updated_after,
    )

    while True:
        # ChallengePageError and other fatal errors propagate to the caller.
        docs, next_cursor = fetch_page(
            session,
            resource,
            api_key,
            cursor=cursor,
            updated_after=updated_after,
            max_retries=max_retries,
            retry_backoff_max=retry_backoff_max,
            challenge_cooldown_min=challenge_cooldown_min,
            challenge_cooldown_max=challenge_cooldown_max,
            api_key_transport=api_key_transport,
        )

        if incremental and docs:
            deduped_docs: List[Dict[str, Any]] = []
            for doc in docs:
                doc_id = doc.get("id")
                if doc_id is None:
                    deduped_docs.append(doc)
                    continue
                doc_id_key = str(doc_id)
                if doc_id_key in seen_doc_ids:
                    continue
                seen_doc_ids.add(doc_id_key)
                deduped_docs.append(doc)
            docs = deduped_docs

        if docs:
            # Serialise as NDJSON
            ndjson_bytes = (
                "\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n"
            ).encode()
            checksum = sha256_of_bytes(ndjson_bytes)
            key = _make_s3_key(resource, batch_index, prefix=s3_prefix)

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

            # Periodic checkpoint: persist progress so the run can be resumed if interrupted.
            # This is checked before the cursor-based exit so it fires even on the last batch.
            if checkpoint_fn is not None and batch_index > 0 and batch_index % CHECKPOINT_INTERVAL == 0:
                checkpoint_fn(state, manifest)

        if next_cursor and next_cursor != cursor:
            state = update_cursor(state, resource, next_cursor)
            cursor = next_cursor
            if request_delay > 0:
                time.sleep(request_delay)
            continue

        if not docs and next_cursor:
            logger.info(
                "Stopping '%s' pagination because cursor did not advance (%r).",
                resource,
                next_cursor,
            )
            state = clear_cursor(state, resource)
            break
        else:
            logger.info(
                "Stopping '%s' pagination (next_cursor=%r, current_cursor=%r).",
                resource,
                next_cursor,
                cursor,
            )
            state = clear_cursor(state, resource)
            break

    logger.info("Finished '%s': %d documents in %d batches.", resource, total_docs, batch_index)
    return state, manifest


def run_full_load(
    client,
    bucket: str,
    state: Dict[str, Any],
    manifest: Dict[str, Any],
    run_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run a full ingest of all resources, with support for resuming interrupted runs.

    On the first invocation *run_id* is used as the staging prefix
    ``raw/_staging/<run_id>/``.  Progress (cursors and completed-resource list)
    is saved to S3 after every :data:`CHECKPOINT_INTERVAL` batches **and** after
    each resource finishes, so a run that is interrupted (e.g. by a job timeout)
    can be continued by simply re-triggering the workflow.

    On resume the in-progress ``run_id`` is read from ``state["full_load_run_id"]``,
    resources listed in ``state["full_load_completed_resources"]`` are skipped, and
    the cursor stored in ``state["cursors"][resource]`` is used to pick up the
    partially-fetched resource.

    Call :func:`publish_full_load` after a successful run to promote the staging
    data to ``raw/current/``.
    """
    api_key = load_api_key()

    # Determine whether we are resuming an interrupted run or starting fresh.
    saved_run_id = state.get("full_load_run_id")
    if saved_run_id is not None:
        effective_run_id = saved_run_id
        completed = list(state.get("full_load_completed_resources", []))
        logger.info(
            "Resuming full load run_id=%s (already completed: %s).",
            effective_run_id,
            completed,
        )
    else:
        effective_run_id = run_id
        completed = []
        # Clear any stale per-resource cursors left over from a previous run.
        for resource in RESOURCES:
            state = clear_cursor(state, resource)
        state = set_full_load_run(state, effective_run_id)
        logger.info("Starting fresh full load (run_id=%s).", effective_run_id)

    s3_prefix = f"{PREFIX_STAGING}{effective_run_id}/"

    def _checkpoint(s: Dict[str, Any], m: Dict[str, Any]) -> None:
        """Persist state and manifest to S3 so the run can be resumed if interrupted."""
        save_state(s, client, bucket)
        save_manifest(m, client, bucket)

    for resource in RESOURCES:
        if resource in completed:
            logger.info("Skipping already-completed resource '%s'.", resource)
            continue
        state, manifest = ingest_resource(
            resource, api_key, client, bucket, state, manifest,
            incremental=False, s3_prefix=s3_prefix,
            checkpoint_fn=_checkpoint,
        )
        # Record completion and save progress before moving to the next resource.
        state = mark_full_load_resource_done(state, resource)
        _checkpoint(state, manifest)

    # All resources done – clear the in-progress tracking fields.
    state = clear_full_load(state)
    return state, manifest


def publish_full_load(
    client,
    bucket: str,
    run_id: str,
    state: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Promote a completed staging run to the canonical ``raw/current/`` prefix.

    Steps:
    1. Delete all existing objects under ``raw/current/``.
    2. Copy all objects from ``raw/_staging/<run_id>/`` to ``raw/current/``.
    3. Write the ``raw/LATEST_RUN.json`` pointer file.
    4. Remap the in-memory manifest keys from staging to current.
    5. Delete the staging objects.

    Returns the updated manifest (with current/ keys).
    """
    staging_prefix = f"{PREFIX_STAGING}{run_id}/"

    # 1. Clear existing current/
    deleted_current = delete_prefix(client, bucket, PREFIX_CURRENT)
    logger.info("Cleared %d existing objects from %r.", deleted_current, PREFIX_CURRENT)

    # 2. Enumerate staging objects once (reused for copy and delete)
    staging_keys = list_prefix(client, bucket, staging_prefix)
    logger.info(
        "Publishing %d objects from staging run %r to %r.",
        len(staging_keys),
        run_id,
        PREFIX_CURRENT,
    )
    for key in staging_keys:
        relative = key[len(staging_prefix):]
        dst_key = f"{PREFIX_CURRENT}{relative}"
        copy_object(client, bucket, key, dst_key)
    logger.info("Copied %d objects to %r.", len(staging_keys), PREFIX_CURRENT)

    # 3. Write LATEST_RUN.json pointer
    pointer: Dict[str, Any] = {
        "run_id": run_id,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "run_count": state.get("run_count"),
    }
    upload_bytes(
        client,
        bucket,
        f"{PREFIX_RAW}LATEST_RUN.json",
        json.dumps(pointer, indent=2).encode(),
        content_type="application/json",
    )
    logger.info("Wrote LATEST_RUN.json (run_id=%r).", run_id)

    # 4. Remap manifest keys staging → current
    updated_objects = []
    for obj in manifest.get("objects", []):
        key = obj["key"]
        if key.startswith(staging_prefix):
            relative = key[len(staging_prefix):]
            updated_objects.append({**obj, "key": f"{PREFIX_CURRENT}{relative}"})
        else:
            updated_objects.append(obj)
    manifest = {**manifest, "objects": updated_objects}

    # 5. Delete staging objects (we already have the key list)
    if staging_keys:
        for i in range(0, len(staging_keys), 1000):
            batch = [{"Key": k} for k in staging_keys[i : i + 1000]]
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        logger.info("Deleted %d staging objects for run %r.", len(staging_keys), run_id)

    return manifest


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
