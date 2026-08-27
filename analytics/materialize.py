"""
Materialise MinIO NDJSON data into a local DuckDB file for fast web-app queries.

Reads the S3-backed views defined in analytics.connect, writes them as real
tables into a temporary DuckDB file, then atomically renames it to the output
path so the web app always sees a consistent snapshot.

Usage:
    python -m analytics.materialize [--output PATH]

Required environment variables (same as the pipeline):
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY
    S3_BUCKET   (default: bundeswarehouse)
    S3_REGION   (default: us-east-1)
"""

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from analytics.connect import get_db

logger = logging.getLogger(__name__)

RESOURCES = ["person", "vorgang", "drucksache", "aktivitaet", "plenarprotokoll"]
DEFAULT_OUTPUT = "warehouse.duckdb"


def materialize(output_path: str = DEFAULT_OUTPUT) -> None:
    output = Path(output_path).resolve()
    tmp = output.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)

    logger.info("Opening S3 views...")
    src = get_db(":memory:")

    logger.info("Attaching output file %s...", tmp)
    src.execute(f"ATTACH '{tmp}' AS local")

    for resource in RESOURCES:
        logger.info("Materialising '%s'...", resource)
        try:
            src.execute(
                f"CREATE OR REPLACE TABLE local.{resource} AS SELECT * FROM {resource}"
            )
        except duckdb.Error as e:
            # A resource prefix may not exist yet (e.g. 'person' before the first
            # ingest run that includes it). Skip it instead of failing the snapshot.
            logger.warning("  '%s' skipped (no readable data): %s", resource, e)
            continue
        count = src.execute(f"SELECT COUNT(*) FROM local.{resource}").fetchone()[0]
        logger.info("  '%s': %d rows.", resource, count)

    src.execute("DETACH local")
    src.close()

    tmp.rename(output)
    logger.info("Done → %s", output)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    parser = argparse.ArgumentParser(description="Materialise MinIO data into a local DuckDB file.")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Output DuckDB file path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    materialize(args.output)


if __name__ == "__main__":
    main()
