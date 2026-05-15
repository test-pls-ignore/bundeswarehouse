import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from analytics.extract import (
    _migrate_extraction_log,
    build_partition_plan,
    filter_pending_docs,
    get_pending,
    merge_shard_dbs,
    validate_embeddings_db,
)


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


class TestAnalyticsExtractPartitioning(unittest.TestCase):
    def test_filter_pending_docs_applies_month_and_shard(self):
        pending = [
            ("doc-1", "https://example.com/1.pdf", "2026-01-01 00:00:00", 0),
            ("doc-2", "https://example.com/2.pdf", "2026-01-02 00:00:00", 0),
            ("doc-3", "https://example.com/3.pdf", "2026-01-03 00:00:00", 0),
            ("doc-4", "https://example.com/4.pdf", "2026-02-01 00:00:00", 0),
        ]

        selected = filter_pending_docs(
            pending,
            updated_month="2026-01",
            shard_index=1,
            shard_count=2,
        )

        self.assertEqual(
            selected,
            [("doc-2", "https://example.com/2.pdf", "2026-01-02 00:00:00", 0)],
        )

    def test_build_partition_plan_splits_large_month_into_multiple_shards(self):
        pending = [
            ("doc-1", "https://example.com/1.pdf", "2026-01-01 00:00:00", 0),
            ("doc-2", "https://example.com/2.pdf", "2026-01-02 00:00:00", 0),
            ("doc-3", "https://example.com/3.pdf", "2026-01-03 00:00:00", 0),
            ("doc-4", "https://example.com/4.pdf", "2026-02-01 00:00:00", 0),
        ]

        plan = build_partition_plan(pending, target_docs_per_partition=2)

        self.assertEqual(plan["total_docs"], 4)
        self.assertEqual(plan["partition_count"], 3)
        self.assertEqual(
            [partition["partition_id"] for partition in plan["partitions"]],
            ["2026-01-s01", "2026-01-s02", "2026-02-s01"],
        )
        self.assertEqual(
            [partition["doc_count"] for partition in plan["partitions"]],
            [2, 1, 1],
        )
        self.assertEqual(plan["partitions"][0]["first_doc_id"], "doc-1")
        self.assertEqual(plan["partitions"][0]["last_doc_id"], "doc-3")


class TestAnalyticsExtractMerge(unittest.TestCase):
    def test_validate_embeddings_db_accepts_valid_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "embeddings.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute("""
                CREATE TABLE drucksache_chunks (
                    chunk_id VARCHAR PRIMARY KEY,
                    doc_id VARCHAR NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding FLOAT[384] NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE extraction_log (
                    doc_id VARCHAR PRIMARY KEY,
                    status VARCHAR NOT NULL
                )
            """)
            con.close()

            validate_embeddings_db(str(db_path))

    def test_validate_embeddings_db_requires_full_table_reads(self):
        class FakeResult:
            def __init__(self):
                self.calls = 0

            def fetchmany(self, _size: int):
                self.calls += 1
                if self.calls == 1:
                    return [("row",)]
                return []

        class FakeConnection:
            def __init__(self):
                self.commands: list[str] = []
                self.closed = False
                self.result = FakeResult()

            def execute(self, sql: str):
                self.commands.append(sql)
                if "information_schema.tables" in sql:
                    return self
                if sql == "SELECT * FROM drucksache_chunks":
                    return self.result
                if sql == "SELECT * FROM extraction_log":
                    raise duckdb.IOException("Corrupt database file")
                raise AssertionError(f"Unexpected SQL: {sql}")

            def fetchall(self):
                return [("drucksache_chunks",), ("extraction_log",)]

            def close(self):
                self.closed = True

        fake_con = FakeConnection()
        with patch("analytics.extract.duckdb.connect", return_value=fake_con):
            with self.assertRaises(RuntimeError) as ctx:
                validate_embeddings_db("corrupt.duckdb")

        self.assertIn("corrupt.duckdb", str(ctx.exception))
        self.assertIn("SELECT * FROM drucksache_chunks", fake_con.commands)
        self.assertIn("SELECT * FROM extraction_log", fake_con.commands)
        self.assertTrue(fake_con.closed)

    def test_validate_embeddings_db_rejects_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "invalid.duckdb"
            db_path.write_text("not-a-duckdb-file", encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                validate_embeddings_db(str(db_path))

            self.assertIn(str(db_path), str(ctx.exception))

    def test_merge_shard_dbs_reports_shard_path_and_rolls_back(self):
        class FakeConnection:
            def __init__(self):
                self.commands: list[str] = []

            def execute(self, sql: str):
                self.commands.append(sql)
                if "INSERT OR REPLACE INTO drucksache_chunks" in sql:
                    raise duckdb.IOException("Corrupt database file")
                return self

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            shard_path = merge_dir / "part-01.duckdb"
            shard_path.touch()
            output_path = Path(tmpdir) / "embeddings.duckdb"
            output_path.write_text("old-db", encoding="utf-8")

            fake_con = FakeConnection()
            with patch("analytics.extract.setup_db", return_value=fake_con), patch(
                "analytics.extract.validate_embeddings_db"
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    merge_shard_dbs(str(output_path), str(merge_dir))

            self.assertIn(str(shard_path), str(ctx.exception))
            self.assertIn("copy drucksache_chunks", str(ctx.exception))
            self.assertIn("ROLLBACK", fake_con.commands)
            self.assertIn("DETACH shard_0", fake_con.commands)


if __name__ == "__main__":
    unittest.main()
