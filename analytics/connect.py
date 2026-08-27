"""
DuckDB connection for bundeswarehouse analytics.

Reads S3/MinIO credentials from environment variables (same vars as the pipeline):
    S3_ENDPOINT_URL      e.g. http://127.0.0.1:9000
    S3_ACCESS_KEY_ID
    S3_SECRET_ACCESS_KEY
    S3_BUCKET            default: bundeswarehouse
    S3_REGION            default: us-east-1

Usage:
    from analytics.connect import get_db
    con = get_db()
    con.sql("SELECT vorgangstyp, COUNT(*) FROM vorgang GROUP BY 1 ORDER BY 2 DESC").show()
"""

import os
from urllib.parse import urlparse

import duckdb


def get_db(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Return a configured DuckDB connection with views over MinIO NDJSON data."""
    endpoint_url = os.environ["S3_ENDPOINT_URL"]
    parsed = urlparse(endpoint_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    con = duckdb.connect(path)
    con.execute("INSTALL httpfs; LOAD httpfs")
    con.execute(f"SET s3_endpoint='{host}:{port}'")
    con.execute(f"SET s3_access_key_id='{os.environ['S3_ACCESS_KEY_ID']}'")
    con.execute(f"SET s3_secret_access_key='{os.environ['S3_SECRET_ACCESS_KEY']}'")
    con.execute("SET s3_url_style='path'")
    con.execute(f"SET s3_use_ssl={'true' if parsed.scheme == 'https' else 'false'}")
    con.execute(f"SET s3_region='{os.getenv('S3_REGION', 'us-east-1')}'")

    bucket = os.getenv("S3_BUCKET", "bundeswarehouse")
    _create_views(con, bucket)
    return con


def _create_views(con: duckdb.DuckDBPyConnection, bucket: str) -> None:
    con.execute(f"""
        CREATE OR REPLACE VIEW vorgang AS
        SELECT
            id,
            vorgangstyp,
            CAST(wahlperiode   AS INTEGER)     AS wahlperiode,
            beratungsstand,
            titel,
            abstract,
            CAST(datum         AS DATE)        AS datum,
            CAST(aktualisiert  AS TIMESTAMPTZ) AS aktualisiert,
            initiative,
            sachgebiet,
            deskriptor,
        FROM read_ndjson(
            's3://{bucket}/raw/current/vorgang/**/*.ndjson',
            ignore_errors = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW drucksache AS
        SELECT
            id,
            drucksachetyp,
            dokumentnummer,
            CAST(wahlperiode   AS INTEGER)     AS wahlperiode,
            herausgeber,
            titel,
            CAST(datum         AS DATE)        AS datum,
            CAST(aktualisiert  AS TIMESTAMPTZ) AS aktualisiert,
            autoren_anzahl,
            vorgangsbezug_anzahl,
            autoren_anzeige,
            urheber,
            vorgangsbezug,
            fundstelle.pdf_url                 AS pdf_url,
        FROM read_ndjson(
            's3://{bucket}/raw/current/drucksache/**/*.ndjson',
            ignore_errors = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW aktivitaet AS
        SELECT
            id,
            aktivitaetsart,
            person_id,
            CAST(wahlperiode   AS INTEGER)     AS wahlperiode,
            CAST(datum         AS DATE)        AS datum,
            CAST(aktualisiert  AS TIMESTAMPTZ) AS aktualisiert,
            titel                              AS person_name,
            vorgangsbezug_anzahl,
            vorgangsbezug,
            fundstelle.pdf_url                 AS pdf_url,
            fundstelle.drucksachetyp           AS drucksachetyp,
            fundstelle.urheber                 AS urheber,
        FROM read_ndjson(
            's3://{bucket}/raw/current/aktivitaet/**/*.ndjson',
            ignore_errors = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW plenarprotokoll AS
        SELECT
            id,
            dokumentnummer,
            CAST(wahlperiode   AS INTEGER)     AS wahlperiode,
            CAST(datum         AS DATE)        AS datum,
            CAST(aktualisiert  AS TIMESTAMPTZ) AS aktualisiert,
            titel,
            herausgeber,
            vorgangsbezug_anzahl,
            sitzungsbemerkung,
            fundstelle.pdf_url                 AS pdf_url,
            fundstelle.xml_url                 AS xml_url,
        FROM read_ndjson(
            's3://{bucket}/raw/current/plenarprotokoll/**/*.ndjson',
            ignore_errors = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW person AS
        SELECT
            id,
            vorname,
            nachname,
            titel,
            fraktion,
            funktion,
            wahlperiode,
            CAST(aktualisiert  AS TIMESTAMPTZ) AS aktualisiert,
            CAST(basisdatum    AS DATE)        AS basisdatum,
            CAST(datum         AS DATE)        AS datum,
        FROM read_ndjson(
            's3://{bucket}/raw/current/person/**/*.ndjson',
            ignore_errors = true
        )
    """)
