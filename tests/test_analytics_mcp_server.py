"""Tests for analytics/mcp_server.py — read-only SQL safety and query behavior."""

import duckdb
import pytest

from analytics.mcp_server import query_sql, validate_read_only


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "  select * from vorgang",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM reden.rede WHERE fraktion = 'SPD'",
])
def test_validate_read_only_accepts_select_and_with(sql):
    validate_read_only(sql)  # must not raise


@pytest.mark.parametrize("sql", [
    "DROP TABLE vorgang",
    "INSERT INTO vorgang VALUES (1)",
    "UPDATE vorgang SET titel = 'x'",
    "DELETE FROM vorgang",
    "ATTACH 'evil.duckdb' AS evil",
    "SELECT * FROM vorgang; DROP TABLE vorgang",
    "COPY vorgang TO '/tmp/out.csv'",
    "PRAGMA database_list",
    "INSTALL httpfs",
    "LOAD httpfs",
    "CALL some_extension_function()",
    "SET memory_limit='1GB'",
    "CREATE TABLE x AS SELECT 1",
])
def test_validate_read_only_rejects_writes_and_side_effects(sql):
    with pytest.raises(ValueError):
        validate_read_only(sql)


def test_validate_read_only_rejects_non_select_start():
    with pytest.raises(ValueError, match="Only SELECT or WITH"):
        validate_read_only("EXPLAIN SELECT 1")


def _make_warehouse(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE vorgang (id VARCHAR, titel VARCHAR, wahlperiode INTEGER)")
    con.executemany(
        "INSERT INTO vorgang VALUES (?, ?, ?)",
        [("1", "Erstes Gesetz", 20), ("2", "Zweites Gesetz", 20)],
    )
    con.close()


def _make_reden(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE rede (id VARCHAR, fraktion VARCHAR, wortanzahl INTEGER, ist_praesidium BOOLEAN)")
    con.executemany(
        "INSERT INTO rede VALUES (?, ?, ?, ?)",
        [("a", "SPD", 100, False), ("b", "CDU/CSU", 200, False), ("c", None, 5, True)],
    )
    con.close()


def test_query_sql_reads_across_attached_databases(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse.duckdb"
    reden = tmp_path / "reden.duckdb"
    _make_warehouse(warehouse)
    _make_reden(reden)

    monkeypatch.setattr("analytics.mcp_server.WAREHOUSE_PATH", str(warehouse))
    monkeypatch.setattr("analytics.mcp_server.REDEN_PATH", str(reden))

    rows = query_sql("SELECT id, titel FROM vorgang ORDER BY id")
    assert rows == [
        {"id": "1", "titel": "Erstes Gesetz"},
        {"id": "2", "titel": "Zweites Gesetz"},
    ]

    rows = query_sql(
        "SELECT fraktion, sum(wortanzahl) AS woerter FROM reden.rede "
        "WHERE NOT ist_praesidium GROUP BY 1 ORDER BY 1"
    )
    assert rows == [
        {"fraktion": "CDU/CSU", "woerter": 200},
        {"fraktion": "SPD", "woerter": 100},
    ]


def test_query_sql_rejects_write_before_touching_db(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse.duckdb"
    _make_warehouse(warehouse)
    monkeypatch.setattr("analytics.mcp_server.WAREHOUSE_PATH", str(warehouse))
    monkeypatch.setattr("analytics.mcp_server.REDEN_PATH", str(tmp_path / "missing.duckdb"))

    with pytest.raises(ValueError):
        query_sql("DROP TABLE vorgang")

    # Table must be untouched.
    con = duckdb.connect(str(warehouse), read_only=True)
    count = con.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0]
    con.close()
    assert count == 2


def test_query_sql_caps_row_count(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE TABLE big AS SELECT range AS n FROM range(1000)")
    con.close()
    monkeypatch.setattr("analytics.mcp_server.WAREHOUSE_PATH", str(warehouse))
    monkeypatch.setattr("analytics.mcp_server.REDEN_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setattr("analytics.mcp_server.MAX_ROWS", 10)

    rows = query_sql("SELECT n FROM big ORDER BY n")
    assert len(rows) == 10
    assert rows[0]["n"] == 0


def test_query_sql_missing_warehouse_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("analytics.mcp_server.WAREHOUSE_PATH", str(tmp_path / "nope.duckdb"))
    with pytest.raises(RuntimeError, match="warehouse not found"):
        query_sql("SELECT 1")
