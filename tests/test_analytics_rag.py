"""Tests for analytics/rag.py — timeline (bucketed-by-Wahlperiode) retrieval."""

import duckdb
import numpy as np
import pytest

from analytics import rag


class _FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        return np.zeros((len(texts), 384))


def test_discover_wahlperioden_unions_drucksache_and_rede(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE TABLE drucksache (id VARCHAR, wahlperiode INTEGER)")
    con.executemany("INSERT INTO drucksache VALUES (?, ?)", [("a", 19), ("b", 20)])
    con.close()

    reden = tmp_path / "reden.duckdb"
    con = duckdb.connect(str(reden))
    con.execute("CREATE TABLE rede (id VARCHAR, wahlperiode INTEGER)")
    con.executemany("INSERT INTO rede VALUES (?, ?)", [("x", 20), ("y", 21)])
    con.close()

    monkeypatch.setattr(rag, "WAREHOUSE_PATH", str(warehouse))
    monkeypatch.setattr(rag, "REDEN_PATH", str(reden))

    assert rag._discover_wahlperioden() == [19, 20, 21]


def test_discover_wahlperioden_missing_files_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "WAREHOUSE_PATH", str(tmp_path / "nope.duckdb"))
    monkeypatch.setattr(rag, "REDEN_PATH", str(tmp_path / "nope2.duckdb"))

    assert rag._discover_wahlperioden() == []


def test_retrieve_timeline_buckets_per_period_caps_top_k_and_drops_empty_periods(monkeypatch):
    monkeypatch.setattr(rag, "_get_model", lambda: _FakeModel())

    def fake_retrieve_reden(q_vec, top_k, wahlperiode):
        if wahlperiode == 19:
            return [{"source_type": "plenarprotokoll", "score": 0.9, "wahlperiode": 19}]
        return []

    def fake_retrieve_drucksachen(q_vec, top_k, wahlperiode):
        if wahlperiode == 20:
            return [
                {"source_type": "drucksache", "score": 0.5, "wahlperiode": 20},
                {"source_type": "drucksache", "score": 0.8, "wahlperiode": 20},
                {"source_type": "drucksache", "score": 0.3, "wahlperiode": 20},
            ]
        return []

    monkeypatch.setattr(rag, "_retrieve_reden", fake_retrieve_reden)
    monkeypatch.setattr(rag, "_retrieve_drucksachen", fake_retrieve_drucksachen)

    timeline = rag.retrieve_timeline("frage", wahlperioden=[19, 20, 21], top_k_per_period=2)

    # WP21 produced no chunks from either source and must not appear at all.
    assert set(timeline) == {19, 20}
    # WP20's 3 candidates are sorted by score desc and capped to top_k_per_period=2.
    assert [c["score"] for c in timeline[20]] == [0.8, 0.5]


def test_retrieve_timeline_uses_discovery_when_wahlperioden_omitted(monkeypatch):
    monkeypatch.setattr(rag, "_get_model", lambda: _FakeModel())
    monkeypatch.setattr(rag, "_discover_wahlperioden", lambda: [19])
    monkeypatch.setattr(
        rag, "_retrieve_reden",
        lambda q_vec, top_k, wahlperiode: [{"source_type": "plenarprotokoll", "score": 0.7, "wahlperiode": wahlperiode}],
    )
    monkeypatch.setattr(rag, "_retrieve_drucksachen", lambda q_vec, top_k, wahlperiode: [])

    timeline = rag.retrieve_timeline("frage")

    assert set(timeline) == {19}


def test_retrieve_timeline_returns_empty_when_no_periods_found(monkeypatch):
    monkeypatch.setattr(rag, "_discover_wahlperioden", lambda: [])

    assert rag.retrieve_timeline("frage") == {}


if __name__ == "__main__":
    import unittest
    unittest.main()
