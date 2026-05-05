"""
State management for bundeswarehouse incremental ingest.

The state is a small JSON object persisted both:
  - locally as  state.json  (committed to git for visibility)
  - in MinIO as manifests/state.json (source of truth for the runner)

Schema:
  {
    "last_run_at":       "<ISO-8601 UTC>",
    "last_seen_update":  "<ISO-8601 UTC>",    # newest document update seen
    "cursors": {                               # per-resource API cursors
      "vorgang":         "<cursor>",
      "drucksache":      "<cursor>",
      ...
    },
    "run_count": <int>
  }
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pipeline.storage import (
    PREFIX_MANIFESTS,
    download_bytes,
    upload_bytes,
)

logger = logging.getLogger(__name__)

STATE_LOCAL_PATH = "state.json"
STATE_S3_KEY = f"{PREFIX_MANIFESTS}state.json"

_DEFAULT_STATE: Dict[str, Any] = {
    "last_run_at": None,
    "last_seen_update": None,
    "cursors": {},
    "run_count": 0,
    "full_load_run_id": None,
    "full_load_completed_resources": [],
}


def load_state(client=None, bucket: Optional[str] = None) -> Dict[str, Any]:
    """
    Load state, preferring MinIO over local file.

    If a client and bucket are provided, the MinIO copy is tried first.
    Falls back to the local state.json file.
    """
    if client and bucket:
        raw = download_bytes(client, bucket, STATE_S3_KEY)
        if raw:
            logger.info("Loaded state from s3://%s/%s", bucket, STATE_S3_KEY)
            return {**_DEFAULT_STATE, **json.loads(raw.decode())}

    if os.path.exists(STATE_LOCAL_PATH):
        with open(STATE_LOCAL_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        logger.info("Loaded state from local file '%s'.", STATE_LOCAL_PATH)
        return {**_DEFAULT_STATE, **data}

    logger.info("No existing state found, starting fresh.")
    return dict(_DEFAULT_STATE)


def save_state(
    state: Dict[str, Any],
    client=None,
    bucket: Optional[str] = None,
) -> None:
    """
    Persist state locally (state.json) and optionally to MinIO.
    """
    # Always write locally for git tracking
    with open(STATE_LOCAL_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    logger.info("Saved state to local file '%s'.", STATE_LOCAL_PATH)

    if client and bucket:
        raw = json.dumps(state, indent=2, ensure_ascii=False).encode()
        upload_bytes(client, bucket, STATE_S3_KEY, raw, content_type="application/json")


def mark_run_start(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of state with last_run_at set to now (UTC)."""
    updated = dict(state)
    updated["last_run_at"] = datetime.now(timezone.utc).isoformat()
    updated["run_count"] = state.get("run_count", 0) + 1
    return updated


def update_cursor(state: Dict[str, Any], resource: str, cursor: str) -> Dict[str, Any]:
    """Return a copy of state with the cursor for a resource updated."""
    updated = dict(state)
    updated["cursors"] = {**state.get("cursors", {}), resource: cursor}
    return updated


def clear_cursor(state: Dict[str, Any], resource: str) -> Dict[str, Any]:
    """Return a copy of state with the cursor for a resource removed."""
    updated = dict(state)
    cursors = dict(state.get("cursors", {}))
    cursors.pop(resource, None)
    updated["cursors"] = cursors
    return updated


def update_last_seen(state: Dict[str, Any], timestamp: str) -> Dict[str, Any]:
    """Return a copy of state with last_seen_update set to timestamp."""
    updated = dict(state)
    updated["last_seen_update"] = timestamp
    return updated


def set_full_load_run(state: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Return a copy of state recording the start of a full-load run."""
    updated = dict(state)
    updated["full_load_run_id"] = run_id
    updated["full_load_completed_resources"] = list(state.get("full_load_completed_resources", []))
    return updated


def mark_full_load_resource_done(state: Dict[str, Any], resource: str) -> Dict[str, Any]:
    """Return a copy of state with *resource* recorded as completed in the current full load."""
    updated = dict(state)
    completed = list(state.get("full_load_completed_resources", []))
    if resource not in completed:
        completed.append(resource)
    updated["full_load_completed_resources"] = completed
    return updated


def clear_full_load(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of state with full-load tracking fields reset."""
    updated = dict(state)
    updated["full_load_run_id"] = None
    updated["full_load_completed_resources"] = []
    return updated
