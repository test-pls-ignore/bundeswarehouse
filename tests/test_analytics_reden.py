"""Tests for analytics/reden.py — Plenarprotokoll XML speech extraction."""

import duckdb
import pytest

from analytics.reden import (
    RedeSegment,
    get_pending,
    parse_protokoll_xml,
    setup_db,
    store_protokoll,
)

# Modelled on the real dbtplenarprotokoll DTD (WP >= 19), cf. btp/20/20214.xml.
FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<dbtplenarprotokoll wahlperiode="20" sitzung-nr="214">
  <sitzungsverlauf>
    <tagesordnungspunkt top-id="Tagesordnungspunkt 1">
      <rede id="ID2021400100">
        <p klasse="redner"><a id="r1"/><redner id="11004179"><name><vorname>Johannes</vorname><nachname>Vogel</nachname><fraktion>FDP</fraktion></name></redner>Johannes Vogel (FDP):</p>
        <p klasse="J_1">Frau Präsidentin! Liebe Kolleginnen und Kollegen!</p>
        <p klasse="J">Erstens: ein inhaltlicher Punkt.</p>
        <kommentar>(Beifall bei der FDP)</kommentar>
        <p klasse="J">Zweitens: noch ein Punkt.</p>
        <name>Präsidentin Bärbel Bas:</name>
        <p klasse="J_1">Herr Vogel, sprechen Sie noch zur Geschäftsordnung?</p>
        <p klasse="redner"><redner id="11004179"><name><vorname>Johannes</vorname><nachname>Vogel</nachname><fraktion>FDP</fraktion></name></redner>Johannes Vogel (FDP):</p>
        <p klasse="J_1">Ich spreche zur Geschäftsordnung.</p>
      </rede>
      <rede id="ID2021400200">
        <p klasse="redner"><redner id="11005123"><name><titel>Dr.</titel><vorname>Erika</vorname><nachname>Beispiel</nachname><rolle><rolle_lang>Bundesministerin der Finanzen</rolle_lang><rolle_kurz>Bundesministerin BMF</rolle_kurz></rolle></name></redner>Dr. Erika Beispiel, Bundesministerin der Finanzen:</p>
        <p klasse="J">Sehr geehrte Damen und Herren, die Regierung antwortet.</p>
        <kommentar>(Zuruf von der AfD)</kommentar>
      </rede>
    </tagesordnungspunkt>
  </sitzungsverlauf>
</dbtplenarprotokoll>
"""


@pytest.fixture
def segments() -> list[RedeSegment]:
    return parse_protokoll_xml(FIXTURE_XML.encode("utf-8"))


def test_segments_split_at_chair_interruption(segments):
    first_rede = [s for s in segments if s.rede_id == "ID2021400100"]
    assert [s.segment_index for s in first_rede] == [0, 1, 2]
    assert [s.ist_praesidium for s in first_rede] == [False, True, False]


def test_speaker_attribution(segments):
    opener = segments[0]
    assert opener.redner_id == "11004179"
    assert opener.vorname == "Johannes"
    assert opener.nachname == "Vogel"
    assert opener.fraktion == "FDP"
    assert opener.redner_label == "Johannes Vogel (FDP):"
    assert opener.rolle is None


def test_chair_segment_has_label_but_no_id(segments):
    chair = next(s for s in segments if s.ist_praesidium)
    assert chair.redner_id is None
    assert chair.redner_label == "Präsidentin Bärbel Bas:"
    assert "Geschäftsordnung" in chair.text


def test_kommentar_interjections_are_skipped(segments):
    all_text = "\n".join(s.text for s in segments)
    assert "Beifall" not in all_text
    assert "Zuruf" not in all_text


def test_paragraphs_joined_in_order(segments):
    opener = segments[0]
    assert opener.text.splitlines() == [
        "Frau Präsidentin! Liebe Kolleginnen und Kollegen!",
        "Erstens: ein inhaltlicher Punkt.",
        "Zweitens: noch ein Punkt.",
    ]


def test_government_member_has_rolle_instead_of_fraktion(segments):
    minister = next(s for s in segments if s.rede_id == "ID2021400200")
    assert minister.titel == "Dr."
    assert minister.nachname == "Beispiel"
    assert minister.fraktion is None
    assert minister.rolle == "Bundesministerin BMF"


def test_label_fraktion_overrides_merged_stammdaten():
    # Observed live in btp/20/20091.xml: redner id 11005304's <name> block
    # concatenates two different MdBs' data ("SPDCDU/CSU") after one replaced
    # the other under the same id. The label text stays correct per speech.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <dbtplenarprotokoll wahlperiode="20" sitzung-nr="91">
      <sitzungsverlauf>
        <rede id="ID209110200">
          <p klasse="redner"><redner id="11005304"><name><vorname>Dirk-UlrichAlexander</vorname><nachname>Mende Föhr</nachname><fraktion>SPDCDU/CSU</fraktion></name></redner>Alexander Föhr (CDU/CSU):</p>
          <p klasse="J_1">Vielen Dank fuer die freundliche Begruessung.</p>
        </rede>
      </sitzungsverlauf>
    </dbtplenarprotokoll>
    """
    segs = parse_protokoll_xml(xml.encode("utf-8"))
    assert segs[0].fraktion == "CDU/CSU"


def test_label_fraktion_not_invented_when_structured_value_absent():
    # A Land representative (Bundesrat) or interpreter has no <fraktion> in
    # <name>, only a label like "Name (Hessen):". That must stay None rather
    # than picking up "Hessen" as if it were a parliamentary fraktion.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <dbtplenarprotokoll wahlperiode="20" sitzung-nr="91">
      <sitzungsverlauf>
        <rede id="ID209110300">
          <p klasse="redner"><redner id="99999999"><name><vorname>Boris</vorname><nachname>Rhein</nachname></name></redner>Boris Rhein (Hessen):</p>
          <p klasse="J_1">Sehr geehrte Damen und Herren.</p>
        </rede>
      </sitzungsverlauf>
    </dbtplenarprotokoll>
    """
    segs = parse_protokoll_xml(xml.encode("utf-8"))
    assert segs[0].fraktion is None


def test_store_and_reprocess_protokoll(tmp_path):
    con = setup_db(str(tmp_path / "reden.duckdb"))
    protokoll = ("5701", "20/214", 20, "2025-03-18", "https://example/20214.xml", "2025-03-19T00:00:00", 0)
    segs = parse_protokoll_xml(FIXTURE_XML.encode("utf-8"))

    store_protokoll(con, protokoll, segs)
    count1 = con.execute("SELECT COUNT(*) FROM rede WHERE protokoll_id = '5701'").fetchone()[0]
    assert count1 == len(segs)
    status, reden, segmente = con.execute(
        "SELECT status, reden, segmente FROM reden_log WHERE protokoll_id = '5701'"
    ).fetchone()
    assert status == "ok"
    assert reden == 2
    assert segmente == len(segs)

    # Re-storing must replace, not duplicate.
    store_protokoll(con, protokoll, segs)
    count2 = con.execute("SELECT COUNT(*) FROM rede WHERE protokoll_id = '5701'").fetchone()[0]
    assert count2 == count1

    wortanzahl = con.execute(
        "SELECT wortanzahl FROM rede WHERE protokoll_id = '5701' AND segment_index = 0 ORDER BY id LIMIT 1"
    ).fetchone()[0]
    assert wortanzahl > 0
    con.close()


def _make_warehouse(path, rows):
    wh = duckdb.connect(str(path))
    wh.execute("""
        CREATE TABLE plenarprotokoll (
            id VARCHAR, dokumentnummer VARCHAR, wahlperiode INTEGER,
            datum DATE, aktualisiert TIMESTAMPTZ,
            herausgeber VARCHAR, xml_url VARCHAR
        )
    """)
    wh.executemany("INSERT INTO plenarprotokoll VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    wh.close()


def test_get_pending_filters_and_resumes(tmp_path):
    warehouse = tmp_path / "warehouse.duckdb"
    _make_warehouse(warehouse, [
        ("1", "20/1", 20, "2021-10-26", "2021-10-27T00:00:00", "BT", "https://example/1.xml"),
        ("2", "1052", 20, "2025-03-21", "2025-03-22T00:00:00", "BR", "https://example/2.xml"),
        ("3", "20/2", 20, "2021-11-11", "2021-11-12T00:00:00", "BT", None),
        ("4", "19/1", 19, "2017-10-24", "2017-10-25T00:00:00", "BT", "https://example/4.xml"),
    ])
    con = setup_db(str(tmp_path / "reden.duckdb"))

    # BR protocols and rows without xml_url are excluded; WP filter applies.
    pending = get_pending(str(warehouse), con, wahlperiode=None, max_attempts=3)
    assert [p[0] for p in pending] == ["4", "1"]
    pending_wp20 = get_pending(str(warehouse), con, wahlperiode=20, max_attempts=3)
    assert [p[0] for p in pending_wp20] == ["1"]

    # A successfully processed protocol with unchanged signature is skipped …
    protokoll = pending_wp20[0]
    store_protokoll(con, protokoll, parse_protokoll_xml(FIXTURE_XML.encode("utf-8")))
    assert get_pending(str(warehouse), con, wahlperiode=20, max_attempts=3) == []

    # … but a changed aktualisiert timestamp triggers reprocessing.
    wh = duckdb.connect(str(warehouse))
    wh.execute("UPDATE plenarprotokoll SET aktualisiert = '2025-06-01T00:00:00' WHERE id = '1'")
    wh.close()
    assert [p[0] for p in get_pending(str(warehouse), con, wahlperiode=20, max_attempts=3)] == ["1"]
    con.close()


def test_get_pending_respects_max_attempts(tmp_path):
    from analytics.reden import log_failure

    warehouse = tmp_path / "warehouse.duckdb"
    _make_warehouse(warehouse, [
        ("1", "20/1", 20, "2021-10-26", "2021-10-27T00:00:00", "BT", "https://example/1.xml"),
    ])
    con = setup_db(str(tmp_path / "reden.duckdb"))

    for _ in range(3):
        pending = get_pending(str(warehouse), con, wahlperiode=None, max_attempts=3)
        assert len(pending) == 1
        log_failure(con, pending[0], "download_failed")

    assert get_pending(str(warehouse), con, wahlperiode=None, max_attempts=3) == []
    con.close()
