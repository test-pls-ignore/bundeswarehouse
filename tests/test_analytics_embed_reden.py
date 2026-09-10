"""Tests for analytics/embed_reden.py — chunking/embedding of Reden text."""

from unittest.mock import MagicMock

import duckdb
import numpy as np
import pytest

from analytics.embed_reden import embed_batch, get_pending, setup_db


def _make_reden_db(path, rows):
    """rows: list of (rede_id, segment_index, ist_praesidium, wahlperiode, text)"""
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE rede (
            rede_id VARCHAR, segment_index INTEGER, ist_praesidium BOOLEAN,
            wahlperiode INTEGER, text VARCHAR
        )
    """)
    con.executemany("INSERT INTO rede VALUES (?, ?, ?, ?, ?)", rows)
    con.close()


class _FakeModel:
    """Deterministic stand-in for SentenceTransformer.encode."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        return np.zeros((len(texts), 384), dtype="float32")


def test_get_pending_joins_segments_in_order_and_skips_chair(tmp_path):
    reden_db = tmp_path / "reden.duckdb"
    _make_reden_db(reden_db, [
        ("ID1", 0, False, 20, "Erster Teil."),
        ("ID1", 1, True, 20, "Praesidiums-Einwurf, sollte fehlen."),
        ("ID1", 2, False, 20, "Zweiter Teil."),
        ("ID2", 0, False, 19, "Andere Wahlperiode."),
    ])
    con = setup_db(str(tmp_path / "reden_embeddings.duckdb"))

    pending_wp20 = get_pending(str(reden_db), con, wahlperiode=20)
    assert len(pending_wp20) == 1
    rede_id, full_text, _ = pending_wp20[0]
    assert rede_id == "ID1"
    assert full_text == "Erster Teil.\nZweiter Teil."
    assert "Praesidiums" not in full_text

    pending_all = get_pending(str(reden_db), con, wahlperiode=None)
    assert {r[0] for r in pending_all} == {"ID1", "ID2"}
    con.close()


def test_embed_batch_writes_chunks_and_log(tmp_path):
    con = setup_db(str(tmp_path / "reden_embeddings.duckdb"))
    batch = [("ID1", "Ein Beispieltext fuer eine Rede, lang genug fuer einen Chunk.", "hash1")]

    ok, failed = embed_batch(batch, _FakeModel(), con)
    assert (ok, failed) == (1, 0)

    chunks = con.execute("SELECT rede_id, chunk_index, text FROM rede_chunks").fetchall()
    assert len(chunks) == 1
    assert chunks[0][0] == "ID1"

    log = con.execute("SELECT status, chunks, text_hash FROM rede_embed_log WHERE rede_id = 'ID1'").fetchone()
    assert log == ("ok", 1, "hash1")
    con.close()


def test_get_pending_skips_unchanged_reembeds_on_change(tmp_path):
    reden_db = tmp_path / "reden.duckdb"
    _make_reden_db(reden_db, [("ID1", 0, False, 20, "Originaltext, lang genug um gechunkt zu werden hier.")])
    con = setup_db(str(tmp_path / "reden_embeddings.duckdb"))

    pending = get_pending(str(reden_db), con, wahlperiode=None)
    assert len(pending) == 1
    embed_batch(pending, _FakeModel(), con)

    # Unchanged text: nothing pending on the next pass.
    assert get_pending(str(reden_db), con, wahlperiode=None) == []

    # Text changed under the same rede_id: picked up again.
    rw = duckdb.connect(str(reden_db))
    rw.execute("UPDATE rede SET text = 'Geaenderter Text.' WHERE rede_id = 'ID1'")
    rw.close()
    pending_again = get_pending(str(reden_db), con, wahlperiode=None)
    assert len(pending_again) == 1
    assert pending_again[0][1] == "Geaenderter Text."
    con.close()


def test_embed_batch_empty_text_logs_without_chunks(tmp_path):
    con = setup_db(str(tmp_path / "reden_embeddings.duckdb"))
    # Below chunk_text's 50-char minimum, so it yields no chunks.
    ok, failed = embed_batch([("ID1", "zu kurz", "h")], _FakeModel(), con)
    assert (ok, failed) == (0, 1)
    assert con.execute("SELECT COUNT(*) FROM rede_chunks").fetchone()[0] == 0
    status = con.execute("SELECT status FROM rede_embed_log WHERE rede_id = 'ID1'").fetchone()[0]
    assert status == "empty"
    con.close()
