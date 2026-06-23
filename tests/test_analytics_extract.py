import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from analytics.extract import (
    _migrate_extraction_log,
    build_partition_plan,
    export_shard_parquet,
    filter_pending_docs,
    get_pending,
    main,
    merge_shard_parquets,
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
    class _RecordingConnection:
        def __init__(self, executed: list[str]):
            self._executed = executed

        def execute(self, query: str):
            self._executed.append(query)
            return self

        def close(self):
            return None

    def _write_empty_parquet(self, path: Path) -> None:
        con = duckdb.connect()
        try:
            con.execute("CREATE TABLE t(v INTEGER)")
            escaped_path = str(path).replace("'", "''")
            con.execute(f"COPY t TO '{escaped_path}' (FORMAT PARQUET)")
        finally:
            con.close()

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
                if "information_schema.columns" in sql:
                    return self
                if sql == "SELECT * FROM drucksache_chunks":
                    return self.result
                if sql == "SELECT * REPLACE (extracted_at::VARCHAR AS extracted_at) FROM extraction_log":
                    raise duckdb.IOException("Corrupt database file")
                raise AssertionError(f"Unexpected SQL: {sql}")

            def fetchall(self):
                if self.commands[-1].strip().startswith("SELECT table_name FROM information_schema.tables"):
                    return [("drucksache_chunks",), ("extraction_log",)]
                if "information_schema.columns" in self.commands[-1]:
                    return [("doc_id",), ("status",), ("extracted_at",)]
                raise AssertionError(f"Unexpected fetchall for SQL: {self.commands[-1]}")

            def close(self):
                self.closed = True

        fake_con = FakeConnection()
        with patch("analytics.extract.duckdb.connect", return_value=fake_con):
            with self.assertRaises(RuntimeError) as ctx:
                validate_embeddings_db("corrupt.duckdb")

        self.assertIn("corrupt.duckdb", str(ctx.exception))
        self.assertIn("SELECT * FROM drucksache_chunks", fake_con.commands)
        self.assertIn(
            "SELECT * REPLACE (extracted_at::VARCHAR AS extracted_at) FROM extraction_log",
            fake_con.commands,
        )
        self.assertTrue(fake_con.closed)

    def test_validate_embeddings_db_rejects_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "invalid.duckdb"
            db_path.write_text("not-a-duckdb-file", encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                validate_embeddings_db(str(db_path))

            self.assertIn(str(db_path), str(ctx.exception))

    def _make_shard_parquet(self, directory: Path, prefix: str, doc_ids: list[str]) -> None:
        shard_path = directory / f"{prefix}.duckdb"
        con = duckdb.connect(str(shard_path))
        con.execute("INSTALL vss; LOAD vss")
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
                status VARCHAR NOT NULL,
                chunks INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error VARCHAR,
                last_pdf_url VARCHAR,
                last_aktualisiert VARCHAR,
                extracted_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        for doc_id in doc_ids:
            emb = [0.0] * 384
            con.execute(
                "INSERT INTO drucksache_chunks VALUES (?, ?, ?, ?, ?)",
                (f"{doc_id}_0", doc_id, 0, "sample text", emb),
            )
            con.execute(
                "INSERT INTO extraction_log(doc_id, status, chunks) VALUES (?, 'ok', 1)",
                (doc_id,),
            )
        con.close()
        export_shard_parquet(str(shard_path), str(directory), prefix)
        shard_path.unlink()

    def test_merge_shard_parquets_merges_data_from_multiple_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            self._make_shard_parquet(merge_dir, "2024-01-s01", ["doc-a", "doc-b"])
            self._make_shard_parquet(merge_dir, "2024-01-s02", ["doc-c"])

            output_path = Path(tmpdir) / "embeddings.duckdb"
            merge_shard_parquets(str(output_path), str(merge_dir))

            con = duckdb.connect(str(output_path), read_only=True)
            doc_ids = {r[0] for r in con.execute("SELECT doc_id FROM extraction_log").fetchall()}
            chunk_ids = {r[0] for r in con.execute("SELECT chunk_id FROM drucksache_chunks").fetchall()}
            con.close()

        self.assertEqual(doc_ids, {"doc-a", "doc-b", "doc-c"})
        self.assertEqual(chunk_ids, {"doc-a_0", "doc-b_0", "doc-c_0"})

    def test_merge_shard_parquets_honors_batch_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()

            def _write_empty_parquet(path: Path) -> None:
                con = duckdb.connect()
                try:
                    con.execute("CREATE TABLE t(v INTEGER)")
                    escaped_path = str(path).replace("'", "''")
                    con.execute(f"COPY t TO '{escaped_path}' (FORMAT PARQUET)")
                finally:
                    con.close()

            for idx in range(1, 4):
                _write_empty_parquet(merge_dir / f"2024-01-s0{idx}_chunks.parquet")
                _write_empty_parquet(merge_dir / f"2024-01-s0{idx}_log.parquet")

            output_path = Path(tmpdir) / "embeddings.duckdb"
            executed: list[str] = []

            class FakeConnection:
                def execute(self, query: str):
                    executed.append(query)
                    return self

                def close(self):
                    return None

            with patch("analytics.extract.setup_db", return_value=FakeConnection()):
                merge_shard_parquets(str(output_path), str(merge_dir), merge_batch_size=2)

        chunk_inserts = [query for query in executed if "INSERT OR REPLACE INTO drucksache_chunks" in query]
        log_inserts = [query for query in executed if "INSERT OR REPLACE INTO extraction_log" in query]
        self.assertEqual(len(chunk_inserts), 2)
        self.assertEqual(len(log_inserts), 2)
        self.assertIn("read_parquet([", chunk_inserts[0])
        self.assertIn("2024-01-s01_chunks.parquet", chunk_inserts[0])
        self.assertIn("2024-01-s02_chunks.parquet", chunk_inserts[0])
        self.assertIn("2024-01-s03_chunks.parquet", chunk_inserts[1])

    def test_merge_shard_parquets_builds_index_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            self._write_empty_parquet(merge_dir / "2024-01-s01_chunks.parquet")

            output_path = Path(tmpdir) / "embeddings.duckdb"
            executed: list[str] = []
            with patch(
                "analytics.extract.setup_db",
                return_value=self._RecordingConnection(executed),
            ):
                merge_shard_parquets(str(output_path), str(merge_dir))

        self.assertIn(
            "CREATE INDEX IF NOT EXISTS drucksache_chunks_emb_idx",
            "\n".join(executed),
        )

    def test_merge_shard_parquets_skips_index_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            self._write_empty_parquet(merge_dir / "2024-01-s01_chunks.parquet")

            output_path = Path(tmpdir) / "embeddings.duckdb"
            executed: list[str] = []
            with patch(
                "analytics.extract.setup_db",
                return_value=self._RecordingConnection(executed),
            ):
                merge_shard_parquets(
                    str(output_path),
                    str(merge_dir),
                    build_index=False,
                )

        self.assertNotIn(
            "CREATE INDEX IF NOT EXISTS drucksache_chunks_emb_idx",
            "\n".join(executed),
        )

    def test_merge_shard_parquets_rejects_invalid_batch_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            output_path = Path(tmpdir) / "embeddings.duckdb"
            with self.assertRaises(ValueError):
                merge_shard_parquets(str(output_path), str(merge_dir), merge_batch_size=0)

    def test_merge_shard_parquets_creates_empty_db_when_no_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merge_dir = Path(tmpdir) / "shards"
            merge_dir.mkdir()
            output_path = Path(tmpdir) / "embeddings.duckdb"
            merge_shard_parquets(str(output_path), str(merge_dir))

            con = duckdb.connect(str(output_path), read_only=True)
            count = con.execute("SELECT COUNT(*) FROM drucksache_chunks").fetchone()[0]
            con.close()

        self.assertEqual(count, 0)

    def test_main_passes_merge_batch_size_to_merge_shard_parquets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("analytics.extract.merge_shard_parquets") as merge_mock:
                with patch("sys.argv", ["analytics.extract", "--embeddings", "emb.duckdb", "--merge-dir", tmpdir, "--merge-batch-size", "7"]):
                    main()
        merge_mock.assert_called_once_with(
            "emb.duckdb",
            tmpdir,
            merge_batch_size=7,
            build_index=True,
        )

    def test_main_passes_skip_index_to_merge_shard_parquets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("analytics.extract.merge_shard_parquets") as merge_mock:
                with patch("sys.argv", ["analytics.extract", "--embeddings", "emb.duckdb", "--merge-dir", tmpdir, "--skip-index"]):
                    main()
        merge_mock.assert_called_once_with(
            "emb.duckdb",
            tmpdir,
            merge_batch_size=20,
            build_index=False,
        )


if __name__ == "__main__":
    unittest.main()
