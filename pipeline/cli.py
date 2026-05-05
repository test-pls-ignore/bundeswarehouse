"""
CLI entrypoint for the bundeswarehouse pipeline.

Usage:
  python -m pipeline.cli full-load
  python -m pipeline.cli incremental-update
  python -m pipeline.cli check-connection

Required environment variables:
  S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
  S3_BUCKET (default: bundeswarehouse), BUNDESTAG_API_KEY
"""

import argparse
import logging
import os
import sys

from pipeline.ingest import ChallengePageError, run_full_load, run_incremental
from pipeline.manifest import load_manifest, save_manifest
from pipeline.state import load_state, mark_run_start, save_state
from pipeline.storage import check_connection, delete_prefix, ensure_bucket_exists, get_bucket_name, get_s3_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


def cmd_check_connection(_args) -> int:
    """Smoke-test the S3 connection and bucket access."""
    client = get_s3_client()
    bucket = get_bucket_name()
    ok = check_connection(client, bucket)
    if ok:
        print(f"OK: connected to bucket '{bucket}'.")
        return 0
    print(f"FAILED: cannot connect to bucket '{bucket}'.", file=sys.stderr)
    return 1


def cmd_full_load(_args) -> int:
    """Run a full ingest of all Bundestag data."""
    if not os.environ.get("BUNDESTAG_API_KEY", "").strip():
        logger.error(
            "BUNDESTAG_API_KEY is not set or empty. "
            "Configure it as a repository secret and pass it via the workflow env: "
            "BUNDESTAG_API_KEY: ${{ secrets.BUNDESTAG_API_KEY }}"
        )
        return 1

    client = get_s3_client()
    bucket = get_bucket_name()
    ensure_bucket_exists(client, bucket)

    state = load_state(client, bucket)
    manifest = load_manifest(client, bucket)

    state = mark_run_start(state)
    logger.info("Starting full load (run #%d).", state["run_count"])

    try:
        state, manifest = run_full_load(client, bucket, state, manifest)
    except ChallengePageError as exc:
        logger.error("Full load aborted – WAF challenge page blocked the request: %s", exc)
        return 1

    save_state(state, client, bucket)
    save_manifest(manifest, client, bucket)
    logger.info("Full load complete.")
    return 0


def cmd_incremental_update(_args) -> int:
    """Run an incremental ingest, resuming from saved state."""
    if not os.environ.get("BUNDESTAG_API_KEY", "").strip():
        logger.error(
            "BUNDESTAG_API_KEY is not set or empty. "
            "Configure it as a repository secret and pass it via the workflow env: "
            "BUNDESTAG_API_KEY: ${{ secrets.BUNDESTAG_API_KEY }}"
        )
        return 1

    client = get_s3_client()
    bucket = get_bucket_name()
    ensure_bucket_exists(client, bucket)

    state = load_state(client, bucket)
    manifest = load_manifest(client, bucket)

    state = mark_run_start(state)
    logger.info(
        "Starting incremental update (run #%d, last_seen_update=%s).",
        state["run_count"],
        state.get("last_seen_update"),
    )

    try:
        state, manifest = run_incremental(client, bucket, state, manifest)
    except ChallengePageError as exc:
        logger.error("Incremental update aborted – WAF challenge page blocked the request: %s", exc)
        return 1

    save_state(state, client, bucket)
    save_manifest(manifest, client, bucket)
    logger.info("Incremental update complete.")
    return 0


def cmd_cleanup_raw(args) -> int:
    """Delete raw S3 objects under a given prefix after explicit confirmation."""
    prefix = args.prefix
    confirm = args.confirm

    if confirm != "DELETE":
        logger.error(
            "Cleanup aborted: confirmation string must be exactly 'DELETE' "
            "(got %r). Pass --confirm DELETE to proceed.",
            confirm,
        )
        return 1

    client = get_s3_client()
    bucket = get_bucket_name()

    # List objects first so the operator can see what will be removed.
    paginator = client.get_paginator("list_objects_v2")
    keys_to_delete = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys_to_delete.append(obj["Key"])

    if not keys_to_delete:
        logger.info("No objects found under s3://%s/%s – nothing to delete.", bucket, prefix)
        return 0

    logger.info(
        "About to delete %d object(s) under s3://%s/%s:",
        len(keys_to_delete),
        bucket,
        prefix,
    )
    for key in keys_to_delete:
        logger.info("  WILL DELETE: s3://%s/%s", bucket, key)

    deleted = delete_prefix(client, bucket, prefix)
    logger.info("Cleanup complete: %d object(s) deleted under prefix %r.", deleted, prefix)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="bundeswarehouse data pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-connection", help="Smoke-test S3 connection and bucket access")
    subparsers.add_parser("full-load", help="Run a full ingest of all Bundestag data")
    subparsers.add_parser("incremental-update", help="Run an incremental ingest from saved state")

    cleanup = subparsers.add_parser(
        "cleanup-raw",
        help="Delete raw S3 objects under a prefix (requires explicit --confirm DELETE)",
    )
    cleanup.add_argument(
        "--prefix",
        default="raw/",
        help="S3 key prefix to delete (default: raw/). Example: raw/aktivitaet/",
    )
    cleanup.add_argument(
        "--confirm",
        default="",
        help="Safety confirmation: must be exactly 'DELETE' to proceed.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "check-connection": cmd_check_connection,
        "full-load": cmd_full_load,
        "incremental-update": cmd_incremental_update,
        "cleanup-raw": cmd_cleanup_raw,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
