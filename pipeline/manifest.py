"""
Manifest management for bundeswarehouse.

The manifest is a JSON file that lists all objects uploaded to MinIO,
with their keys, sizes, checksums, and a timestamp.

It is stored at manifests/latest.json and archived as
manifests/{YYYY-MM-DD}.json on each run.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pipeline.storage import PREFIX_MANIFESTS, download_bytes, upload_bytes

logger = logging.getLogger(__name__)

MANIFEST_LATEST_KEY = f"{PREFIX_MANIFESTS}latest.json"


def _date_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_manifest(client, bucket: str) -> Dict[str, Any]:
    """Load the latest manifest from MinIO. Returns an empty manifest if none exists."""
    raw = download_bytes(client, bucket, MANIFEST_LATEST_KEY)
    if raw:
        logger.info("Loaded manifest from s3://%s/%s", bucket, MANIFEST_LATEST_KEY)
        return json.loads(raw.decode())
    logger.info("No existing manifest found, starting fresh.")
    return {"created_at": None, "updated_at": None, "objects": []}


def save_manifest(
    manifest: Dict[str, Any],
    client,
    bucket: str,
) -> None:
    """Save the manifest to manifests/latest.json and archive it with today's date."""
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    if manifest.get("created_at") is None:
        manifest["created_at"] = manifest["updated_at"]

    raw = json.dumps(manifest, indent=2, ensure_ascii=False).encode()

    # Write latest
    upload_bytes(client, bucket, MANIFEST_LATEST_KEY, raw, content_type="application/json")

    # Archive copy
    archive_key = f"{PREFIX_MANIFESTS}{_date_key()}.json"
    upload_bytes(client, bucket, archive_key, raw, content_type="application/json")
    logger.info("Manifest saved (latest + archive %s).", archive_key)


def record_object(
    manifest: Dict[str, Any],
    key: str,
    size_bytes: int,
    checksum_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or update a record for a single S3 object in the manifest."""
    objects: List[Dict[str, Any]] = manifest.get("objects", [])
    # Find existing entry by key
    existing = next((o for o in objects if o["key"] == key), None)
    entry: Dict[str, Any] = {
        "key": key,
        "size_bytes": size_bytes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if checksum_sha256:
        entry["sha256"] = checksum_sha256

    if existing:
        existing.update(entry)
    else:
        objects.append(entry)

    updated = dict(manifest)
    updated["objects"] = objects
    return updated


def sha256_of_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of a byte string."""
    return hashlib.sha256(data).hexdigest()
