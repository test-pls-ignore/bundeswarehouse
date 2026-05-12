import tempfile
import unittest
from pathlib import Path

import duckdb

from analytics.extract import _migrate_extraction_log, get_pending


class TestAnalyticsExtractMigrations(unittest.TestCase):
    def test_migrate_adds_attempts_column_and_backfills_existing_rows(self):
        con = duckdb.connect(":memory:")
        con.execute("""
            CREATE TABLE extraction_log (
                doc_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                chunks INTEGER,
                extracted_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        con.execute(
            "INSERT INTO extraction_log (doc_id, status, chunks) VALUES ('doc-1', 'failed', 0)"
        )

        _migrate_extraction_log(con)

        attempts = con.execute(
            "SELECT attempts FROM extraction_log WHERE doc_id = 'doc-1'"
        ).fetchone()[0]
        self.assertEqual(attempts, 0)
        con.close()

    def test_get_pending_treats_null_attempts_as_zero(self):
        con = duckdb.connect(":memory:")
        con.execute("""
            CREATE TABLE extraction_log (
                doc_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                chunks INTEGER,
                extracted_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        con.execute(
            "INSERT INTO extraction_log (doc_id, status, chunks) VALUES ('doc-1', 'failed', 0)"
        )
        _migrate_extraction_log(con)
        con.execute("UPDATE extraction_log SET attempts = NULL WHERE doc_id = 'doc-1'")
        con.execute("""
            UPDATE extraction_log
            SET last_pdf_url = 'https://example.com/doc-1.pdf',
                last_aktualisiert = '2026-01-01'
            WHERE doc_id = 'doc-1'
        """)

        with tempfile.TemporaryDirectory() as tmpdir:
            warehouse_path = Path(tmpdir) / "warehouse.duckdb"
            wh = duckdb.connect(str(warehouse_path))
            wh.execute("""
                CREATE TABLE drucksache (
                    id VARCHAR,
                    pdf_url VARCHAR,
                    aktualisiert TIMESTAMP,
                    wahlperiode INTEGER
                )
            """)
            wh.execute("""
                INSERT INTO drucksache (id, pdf_url, aktualisiert, wahlperiode)
                VALUES ('doc-1', 'https://example.com/doc-1.pdf', '2026-01-01', 20)
            """)
            wh.close()

            pending = get_pending(
                str(warehouse_path),
                con,
                wahlperiode=20,
                max_attempts=3,
            )

        self.assertEqual(pending, [("doc-1", "https://example.com/doc-1.pdf", "2026-01-01 00:00:00", 0)])
        con.close()


if __name__ == "__main__":
    unittest.main()
