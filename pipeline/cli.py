"""
CLI entrypoint for the bundeswarehouse pipeline.

Usage:
  python -m pipeline.cli full-load
  python -m pipeline.cli incremental-update
  python -m pipeline.cli backfill-resource --resource <resource>
  python -m pipeline.cli check-connection
  python -m pipeline.cli cleanup-staging
  python -m pipeline.cli cleanup-current [--resource <resource>]
  python -m pipeline.cli warehouse-status [--json] [--warehouse PATH] [--wahlperiode WP]

Required environment variables:
  S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
  S3_BUCKET (default: bundeswarehouse), BUNDESTAG_API_KEY
"""

import argparse
import json
import logging
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from analytics.materialize import materialize as run_materialize
from pipeline.ingest import (
    RESOURCES,
    ChallengePageError,
    ingest_resource,
    load_api_key,
    publish_full_load,
    run_full_load,
    run_incremental,
)
from pipeline.manifest import load_manifest, save_manifest
from pipeline.state import load_state, mark_run_start, save_state
from pipeline.storage import (
    PREFIX_CURRENT,
    PREFIX_RAW,
    PREFIX_STAGING,
    check_connection,
    delete_prefix,
    download_bytes,
    ensure_bucket_exists,
    get_bucket_name,
    get_s3_client,
    list_prefix,
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


def cmd_backfill_resource(args) -> int:
    """One-off full backfill of a single resource directly into raw/current/.

    Use this when a resource is newly added to RESOURCES after the other
    resources already have a populated raw/current/ snapshot (e.g. 'person').
    incremental-update alone would only fetch entries updated since the
    global ``last_seen_update`` watermark, missing historical records that
    predate it.

    Unlike full-load, this does *not* touch any other resource and does not
    go through the staging → publish dance: it fetches straight into
    raw/current/<resource>/. It refuses to run if that prefix already has
    objects, to avoid silently duplicating or corrupting an existing
    snapshot; use cleanup-current --resource <name> first if you really want
    to start over.
    """
    resource = args.resource
    if resource not in RESOURCES:
        logger.error("Unknown resource %r. Valid resources: %s", resource, RESOURCES)
        return 1

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

    existing = list_prefix(client, bucket, f"{PREFIX_CURRENT}{resource}/")
    if existing:
        logger.error(
            "raw/current/%s/ already has %d object(s) — refusing to backfill "
            "over existing data. Run `cleanup-current --resource %s` first "
            "if you really want to start over.",
            resource, len(existing), resource,
        )
        return 1

    state = load_state(client, bucket)
    manifest = load_manifest(client, bucket)
    api_key = load_api_key()

    logger.info("Backfilling resource '%s' into raw/current/%s/…", resource, resource)
    try:
        state, manifest = ingest_resource(
            resource, api_key, client, bucket, state, manifest,
            incremental=False, s3_prefix=PREFIX_CURRENT,
        )
    except ChallengePageError as exc:
        logger.error("Backfill aborted – WAF challenge page blocked the request: %s", exc)
        return 1

    save_state(state, client, bucket)
    save_manifest(manifest, client, bucket)
    logger.info("Backfill of '%s' complete.", resource)
    return 0


def cmd_materialize(args) -> int:
    """Materialise MinIO NDJSON data into a local DuckDB file for fast web-app queries."""
    from analytics.materialize import DEFAULT_OUTPUT
    output = getattr(args, "output", None) or DEFAULT_OUTPUT
    try:
        run_materialize(output)
        return 0
    except Exception as exc:
        logger.error("Materialisation failed: %s", exc, exc_info=True)
        return 1


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


def _count_by_resource(keys: List[str], prefix: str) -> Dict[str, int]:
    """Count S3 objects per resource under *prefix*.

    Keys are expected to follow the pattern ``{prefix}{resource}/…``.
    Returns a dict mapping resource name → object count.
    """
    counts: Dict[str, int] = defaultdict(int)
    for key in keys:
        relative = key[len(prefix):]
        parts = relative.split("/")
        if parts:
            counts[parts[0]] += 1
    return dict(counts)


def _count_staging_by_run(keys: List[str]) -> Dict[str, Dict[str, int]]:
    """Count staging objects grouped by run_id and resource.

    Keys follow ``raw/_staging/{run_id}/{resource}/…``.
    Returns ``{run_id: {resource: count}}``.
    """
    runs: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    staging_len = len(PREFIX_STAGING)
    for key in keys:
        relative = key[staging_len:]
        parts = relative.split("/")
        if len(parts) >= 2:
            run_id, resource = parts[0], parts[1]
            runs[run_id][resource] += 1
    return {run_id: dict(res_counts) for run_id, res_counts in runs.items()}


def cmd_warehouse_status(args) -> int:
    """Query and print current warehouse/storage status.

    Inspects:
    - Storage layer: LATEST_RUN.json, raw/current/ counts, raw/_staging/ counts.
    - State/manifest: run_count, last_seen_update, manifest timestamps.
    - Optional warehouse snapshot: row counts and pdf_url stats (when --warehouse is given).
    """
    as_json = getattr(args, "json", False)
    warehouse_path = getattr(args, "warehouse", None)
    wahlperiode = getattr(args, "wahlperiode", None)

    try:
        client = get_s3_client()
        bucket = get_bucket_name()
    except EnvironmentError as exc:
        logger.error("Cannot connect to S3: %s", exc)
        return 1

    status: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bucket": bucket,
        "storage": {},
        "state": {},
        "manifest": {},
        "warehouse_snapshot": None,
    }

    # ── Storage layer ──────────────────────────────────────────────────────────

    # LATEST_RUN.json
    latest_run_key = f"{PREFIX_RAW}LATEST_RUN.json"
    raw_latest = download_bytes(client, bucket, latest_run_key)
    if raw_latest:
        try:
            latest_run = json.loads(raw_latest.decode())
        except ValueError:
            latest_run = {"error": "malformed JSON"}
    else:
        latest_run = None
    status["storage"]["latest_run"] = latest_run

    # raw/current/ counts per resource
    current_keys = list_prefix(client, bucket, PREFIX_CURRENT)
    status["storage"]["current_objects_by_resource"] = _count_by_resource(current_keys, PREFIX_CURRENT)
    status["storage"]["current_total_objects"] = len(current_keys)

    # raw/_staging/ counts grouped by run
    staging_keys = list_prefix(client, bucket, PREFIX_STAGING)
    status["storage"]["staging_objects_by_run"] = _count_staging_by_run(staging_keys)
    status["storage"]["staging_total_objects"] = len(staging_keys)

    # ── State / manifest ───────────────────────────────────────────────────────

    state = load_state(client, bucket)
    status["state"] = {
        "run_count": state.get("run_count"),
        "last_run_at": state.get("last_run_at"),
        "last_seen_update": state.get("last_seen_update"),
        "full_load_run_id": state.get("full_load_run_id"),
        "full_load_completed_resources": state.get("full_load_completed_resources", []),
        "cursors": state.get("cursors", {}),
    }

    manifest = load_manifest(client, bucket)
    status["manifest"] = {
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "object_count": len(manifest.get("objects", [])),
    }

    # ── Optional warehouse snapshot stats ──────────────────────────────────────

    if warehouse_path:
        try:
            import duckdb

            # Allowed table names and column names are fully controlled by hardcoded
            # lists below; we additionally quote them as SQL identifiers for safety.
            _ALLOWED_TABLES = frozenset(["vorgang", "drucksache", "plenarprotokoll", "aktivitaet"])
            _ALLOWED_TS_COLS = frozenset(["aktualisiert", "datum", "updated_at"])

            def _qi(name: str) -> str:
                """Quote a known-safe identifier with double quotes for DuckDB."""
                return '"' + name.replace('"', '""') + '"'

            con = duckdb.connect(warehouse_path, read_only=True)
            tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
            snapshot: Dict[str, Any] = {"path": warehouse_path, "tables": {}}
            target_tables = ["vorgang", "drucksache", "plenarprotokoll", "aktivitaet"]
            for tbl in target_tables:
                if tbl not in _ALLOWED_TABLES or tbl not in tables:
                    snapshot["tables"][tbl] = {"available": False}
                    continue
                qtbl = _qi(tbl)
                row_count = con.execute(f"SELECT COUNT(*) FROM {qtbl}").fetchone()[0]
                tbl_info: Dict[str, Any] = {"available": True, "row_count": row_count}

                # Timestamp range (look for a common timestamp column)
                for ts_col in ("aktualisiert", "datum", "updated_at"):
                    if ts_col not in _ALLOWED_TS_COLS:
                        continue
                    qts = _qi(ts_col)
                    try:
                        ts_row = con.execute(
                            f"SELECT MIN({qts}), MAX({qts}) FROM {qtbl}"
                        ).fetchone()
                        if ts_row and ts_row[0] is not None:
                            tbl_info["min_timestamp"] = str(ts_row[0])
                            tbl_info["max_timestamp"] = str(ts_row[1])
                            tbl_info["timestamp_column"] = ts_col
                            break
                    except Exception:
                        continue

                # pdf_url stats for drucksache
                if tbl == "drucksache":
                    col_names = [
                        row[0] for row in con.execute(f"DESCRIBE {qtbl}").fetchall()
                    ]
                    if "pdf_url" in col_names:
                        pdf_count = con.execute(
                            f"SELECT COUNT(*) FROM {qtbl} WHERE pdf_url IS NOT NULL AND pdf_url != ''"
                        ).fetchone()[0]
                        tbl_info["rows_with_pdf_url"] = pdf_count

                # Optional wahlperiode filter
                if wahlperiode:
                    col_names = [
                        row[0] for row in con.execute(f"DESCRIBE {qtbl}").fetchall()
                    ]
                    if "wahlperiode" in col_names:
                        wp_count = con.execute(
                            f"SELECT COUNT(*) FROM {qtbl} WHERE wahlperiode = ?",
                            [int(wahlperiode)],
                        ).fetchone()[0]
                        tbl_info[f"row_count_wp{wahlperiode}"] = wp_count

                snapshot["tables"][tbl] = tbl_info

            con.close()
            status["warehouse_snapshot"] = snapshot
        except Exception as exc:
            status["warehouse_snapshot"] = {"error": str(exc)}

    # ── Output ─────────────────────────────────────────────────────────────────

    if as_json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
    else:
        _print_status_human(status)

    return 0


def _print_status_human(status: Dict[str, Any]) -> None:
    """Print a human-readable warehouse status report to stdout."""
    print("=" * 60)
    print(f"Warehouse Status  ({status['generated_at']})")
    print(f"Bucket: {status['bucket']}")
    print("=" * 60)

    # Storage layer
    print("\n── Storage layer ──────────────────────────────────────────")
    lr = status["storage"].get("latest_run")
    if lr:
        print(f"  LATEST_RUN.json:")
        print(f"    run_id       : {lr.get('run_id', 'n/a')}")
        print(f"    published_at : {lr.get('published_at', 'n/a')}")
        print(f"    run_count    : {lr.get('run_count', 'n/a')}")
    else:
        print("  LATEST_RUN.json: not found (no successful full-load yet)")

    current_by_res = status["storage"].get("current_objects_by_resource", {})
    total_current = status["storage"].get("current_total_objects", 0)
    print(f"\n  raw/current/  ({total_current} total objects)")
    if current_by_res:
        for resource, count in sorted(current_by_res.items()):
            print(f"    {resource:<20} {count:>6} objects")
    else:
        print("    (empty)")

    staging_by_run = status["storage"].get("staging_objects_by_run", {})
    total_staging = status["storage"].get("staging_total_objects", 0)
    print(f"\n  raw/_staging/ ({total_staging} total objects across {len(staging_by_run)} run(s))")
    if staging_by_run:
        for run_id, res_counts in sorted(staging_by_run.items()):
            total_for_run = sum(res_counts.values())
            print(f"    run {run_id}  ({total_for_run} objects)")
            for resource, count in sorted(res_counts.items()):
                print(f"      {resource:<18} {count:>6} objects")
    else:
        print("    (empty)")

    # State
    print("\n── State ──────────────────────────────────────────────────")
    s = status.get("state", {})
    print(f"  run_count          : {s.get('run_count', 'n/a')}")
    print(f"  last_run_at        : {s.get('last_run_at', 'n/a')}")
    print(f"  last_seen_update   : {s.get('last_seen_update', 'n/a')}")
    active_run = s.get("full_load_run_id")
    if active_run:
        completed = s.get("full_load_completed_resources", [])
        print(f"  active full-load   : {active_run}")
        print(f"    completed so far : {', '.join(completed) if completed else '(none)'}")
    else:
        print("  active full-load   : none")

    # Manifest
    print("\n── Manifest ───────────────────────────────────────────────")
    m = status.get("manifest", {})
    print(f"  created_at   : {m.get('created_at', 'n/a')}")
    print(f"  updated_at   : {m.get('updated_at', 'n/a')}")
    print(f"  object_count : {m.get('object_count', 'n/a')}")

    # Warehouse snapshot
    snap = status.get("warehouse_snapshot")
    if snap:
        print("\n── Warehouse snapshot ─────────────────────────────────────")
        if "error" in snap:
            print(f"  ERROR: {snap['error']}")
        else:
            print(f"  path: {snap.get('path', 'n/a')}")
            for tbl, info in snap.get("tables", {}).items():
                if not info.get("available"):
                    print(f"  {tbl:<20} (table not found in snapshot)")
                    continue
                row_count = info.get("row_count", "n/a")
                print(f"  {tbl:<20} {row_count:>8} rows", end="")
                if info.get("timestamp_column"):
                    print(
                        f"  [{info['timestamp_column']}:"
                        f" {info.get('min_timestamp', '?')} … {info.get('max_timestamp', '?')}]",
                        end="",
                    )
                if "rows_with_pdf_url" in info:
                    print(f"  (pdf_url non-null: {info['rows_with_pdf_url']})", end="")
                for k, v in info.items():
                    if k.startswith("row_count_wp"):
                        wp = k[len("row_count_wp"):]
                        print(f"  [WP{wp}: {v}]", end="")
                print()

    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="bundeswarehouse data pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-connection", help="Smoke-test S3 connection and bucket access")
    subparsers.add_parser("full-load", help="Run a full ingest of all Bundestag data (staging → publish)")
    subparsers.add_parser("incremental-update", help="Run an incremental ingest from saved state")

    backfill_cmd = subparsers.add_parser(
        "backfill-resource",
        help="One-off full fetch of a single new resource straight into raw/current/",
    )
    backfill_cmd.add_argument(
        "--resource",
        required=True,
        metavar="RESOURCE",
        help=f"Resource to backfill. One of: {', '.join(RESOURCES)}",
    )

    materialize_cmd = subparsers.add_parser(
        "materialize",
        help="Materialise MinIO NDJSON into a local DuckDB file (warehouse.duckdb)",
    )
    materialize_cmd.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Output DuckDB file path (default: warehouse.duckdb)",
    )

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

    status_cmd = subparsers.add_parser(
        "warehouse-status",
        help="Report current state of the warehouse storage layers and S3 data",
    )
    status_cmd.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output status as JSON instead of human-readable text",
    )
    status_cmd.add_argument(
        "--warehouse",
        metavar="PATH",
        default=None,
        help=(
            "Path to a local warehouse.duckdb snapshot. "
            "When provided, row counts and pdf_url stats are included in the report."
        ),
    )
    status_cmd.add_argument(
        "--wahlperiode",
        metavar="WP",
        default=None,
        help="If given, also report per-Wahlperiode row counts in the warehouse snapshot",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "check-connection": cmd_check_connection,
        "full-load": cmd_full_load,
        "incremental-update": cmd_incremental_update,
        "backfill-resource": cmd_backfill_resource,
        "materialize": cmd_materialize,
        "cleanup-staging": cmd_cleanup_staging,
        "cleanup-current": cmd_cleanup_current,
        "warehouse-status": cmd_warehouse_status,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
