"""
CLI entrypoint for the bundeswarehouse pipeline.

Usage:
  python -m pipeline.cli full-load
  python -m pipeline.cli incremental-update
  python -m pipeline.cli check-connection
  python -m pipeline.cli cleanup-staging
  python -m pipeline.cli cleanup-current [--resource <resource>]

Required environment variables:
  S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
  S3_BUCKET (default: bundeswarehouse), BUNDESTAG_API_KEY
"""

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from pipeline.ingest import ChallengePageError, publish_full_load, run_full_load, run_incremental
from pipeline.manifest import load_manifest, save_manifest
from pipeline.state import load_state, mark_run_start, save_state
from pipeline.storage import (
    PREFIX_CURRENT,
    PREFIX_STAGING,
    check_connection,
    delete_prefix,
    ensure_bucket_exists,
    get_bucket_name,
    get_s3_client,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


def _generate_run_id() -> str:
    """Return a short, sortable, unique identifier for a pipeline run.

    Format: ``YYYYMMDDTHHMMSSZ-<8 hex chars>``
    Example: ``20260505T095848Z-3f8a1c02``
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{ts}-{short}"


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
    """Run a full ingest of all Bundestag data using a staging → publish strategy.

    Objects are written to ``raw/_staging/<run_id>/`` during the run.  Only
    after **all** resources finish successfully are they copied to
    ``raw/current/`` and the staging prefix is deleted.

    If the run fails mid-way (or is interrupted by a job timeout):
    - ``raw/current/`` is **not** modified (it still reflects the last
      successful run).
    - The staging data is kept under ``raw/_staging/<run_id>/`` and the
      ``run_id``, completed resources, and per-resource cursors are saved to
      state so that re-triggering the workflow **resumes** from where the run
      was interrupted rather than starting over.
    - Use the ``cleanup-staging`` action or the *Cleanup S3 Prefixes* workflow
      to remove leftover staging data after an abandoned run.
    """
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

    # Detect whether we are resuming an interrupted run.
    existing_run_id = state.get("full_load_run_id")
    if existing_run_id:
        run_id = existing_run_id
        # Load the manifest that was saved during the previous attempt so that
        # objects already uploaded are not lost.
        manifest = load_manifest(client, bucket)
        logger.info(
            "Resuming interrupted full load (run_id=%s, run #%d).",
            run_id,
            state.get("run_count", 0),
        )
    else:
        state = mark_run_start(state)
        run_id = _generate_run_id()
        manifest: dict = {"created_at": None, "updated_at": None, "objects": []}
        logger.info(
            "Starting full load (run #%d, run_id=%s). "
            "Staging prefix: raw/_staging/%s/",
            state["run_count"],
            run_id,
            run_id,
        )

    try:
        state, manifest = run_full_load(client, bucket, state, manifest, run_id=run_id)
    except ChallengePageError as exc:
        logger.error("Full load aborted – WAF challenge page blocked the request: %s", exc)
        logger.info(
            "Staging data for run %s kept at raw/_staging/%s/ for inspection. "
            "raw/current/ is unchanged. Re-run the workflow to resume.",
            run_id,
            run_id,
        )
        return 1
    except Exception as exc:  # Intentionally broad: any unhandled error (network, S3, etc.)
        # preserves staging data so the operator can inspect what was downloaded.
        logger.error("Full load failed: %s", exc, exc_info=True)
        logger.info(
            "Staging data for run %s kept at raw/_staging/%s/ for inspection. "
            "raw/current/ is unchanged. Re-run the workflow to resume.",
            run_id,
            run_id,
        )
        return 1

    # All resources fetched successfully – publish staging → current.
    logger.info("Ingest complete. Publishing staging run %s to raw/current/…", run_id)
    manifest = publish_full_load(client, bucket, run_id, state, manifest)

    save_state(state, client, bucket)
    save_manifest(manifest, client, bucket)
    logger.info("Full load complete (run_id=%s).", run_id)
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


def cmd_cleanup_staging(_args) -> int:
    """Delete all objects under the staging prefix (``raw/_staging/``).

    Safe to run at any time – it does not touch ``raw/current/``.
    """
    client = get_s3_client()
    bucket = get_bucket_name()
    count = delete_prefix(client, bucket, PREFIX_STAGING)
    logger.info("cleanup-staging: deleted %d objects from %r in bucket %r.", count, PREFIX_STAGING, bucket)
    return 0


def cmd_cleanup_current(args) -> int:
    """Delete objects under the current prefix (``raw/current/``).

    If ``--resource`` is given, only ``raw/current/<resource>/`` is deleted.
    Without ``--resource``, the entire ``raw/current/`` tree is deleted.

    **Warning:** this removes the published dataset.  The next successful
    full-load run will repopulate ``raw/current/``.
    """
    client = get_s3_client()
    bucket = get_bucket_name()
    resource = getattr(args, "resource", None)
    if resource:
        prefix = f"{PREFIX_CURRENT}{resource}/"
    else:
        prefix = PREFIX_CURRENT
    count = delete_prefix(client, bucket, prefix)
    logger.info("cleanup-current: deleted %d objects from %r in bucket %r.", count, prefix, bucket)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="bundeswarehouse data pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-connection", help="Smoke-test S3 connection and bucket access")
    subparsers.add_parser("full-load", help="Run a full ingest of all Bundestag data (staging → publish)")
    subparsers.add_parser("incremental-update", help="Run an incremental ingest from saved state")

    subparsers.add_parser(
        "cleanup-staging",
        help="Delete all objects under raw/_staging/ (does not touch raw/current/)",
    )

    cleanup_current = subparsers.add_parser(
        "cleanup-current",
        help="Delete objects under raw/current/ (optionally scoped to a single resource)",
    )
    cleanup_current.add_argument(
        "--resource",
        metavar="RESOURCE",
        default=None,
        help=(
            "If given, delete only raw/current/<RESOURCE>/. "
            "Without this flag the entire raw/current/ tree is deleted."
        ),
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "check-connection": cmd_check_connection,
        "full-load": cmd_full_load,
        "incremental-update": cmd_incremental_update,
        "cleanup-staging": cmd_cleanup_staging,
        "cleanup-current": cmd_cleanup_current,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
