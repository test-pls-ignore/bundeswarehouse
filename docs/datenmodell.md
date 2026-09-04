# Datenmodell & Roadmap: Reden-zentrierte Analyse

Ziel des Projekts: Parlamentsdaten des Deutschen Bundestages so aufbereiten,
dass ein LLM detaillierte, strukturierte Fragen zu Parteien und Abgeordneten
beantworten kann — insbesondere Analysen über längere Zeiträume
(„Wie sprach Fraktion X zwischen 2021 und 2025 über Thema Y?").

Priorität laut Projektentscheidung (Aug 2026): **Rede-Inhalte zuerst**,
Abstimmungsverhalten später.

## Architektur-Schichten

```
DIP-API ──► raw/current/ (MinIO, NDJSON)      Rohdaten-Schicht (pipeline/)
                │
                ▼ analytics.materialize
        warehouse.duckdb                       Snapshot-Schicht
                │
                ├─► analytics.reden  ──► reden.duckdb      strukturierte Reden
                └─► analytics.extract ─► embeddings.duckdb Volltext-Vektoren (RAG)
```

- **`warehouse.duckdb`** — materialisierter Snapshot der DIP-Ressourcen
  (`person`, `vorgang`, `drucksache`, `aktivitaet`, `plenarprotokoll`).
- **`reden.duckdb`** — von `analytics.reden` erzeugte Faktentabelle `rede`:
  jede Rede aus den Plenarprotokoll-XMLs (WP ≥ 19), segmentiert nach Sprecher.
- **`embeddings.duckdb`** — bestehender RAG-Index (semantische Suche als
  Beleg-/Zitat-Werkzeug, nicht als Analyse-Rückgrat).

## Warum XML statt PDF?

BT-Plenarprotokolle ab WP 19 sind als strukturiertes XML verfügbar
(`fundstelle.xml_url` in DIP, z. B. `https://dserver.bundestag.de/btp/20/20214.xml`).
Das XML taggt jede Rede (`<rede>`) mit Redner-ID, Name, Fraktion bzw.
Regierungsrolle und trennt Redetext von Zwischenrufen (`<kommentar>`) und
Präsidiums-Einwürfen (`<name>`). Das ersetzt das fehleranfällige
PDF-Regex-Parsing vollständig — für WP 19+.

Die `plenarprotokoll`-Ressource enthält auch **Bundesrats**-Protokolle
(`herausgeber = 'BR'`, ohne `xml_url`); die Reden-Extraktion filtert auf
`herausgeber = 'BT'`.

## Tabelle `rede` (reden.duckdb)

Ein Segment = zusammenhängender Redetext eines Sprechers innerhalb einer
`<rede>`. Eine von der Präsidentin unterbrochene Rede ergibt mehrere
Segmente in Originalreihenfolge (`segment_index`).

| Spalte | Bedeutung |
|---|---|
| `id` | `{protokoll_id}_{rede_id}_{segment_index}` |
| `rede_id` | XML-ID der Rede (z. B. `ID2021400100`) |
| `protokoll_id` | DIP-ID des Plenarprotokolls |
| `dokumentnummer`, `wahlperiode`, `datum` | Sitzungskontext |
| `redner_id` | offizielle Bundestags-Redner-ID (NULL bei Präsidium) |
| `titel`, `vorname`, `nachname` | Sprechername |
| `fraktion` | Fraktion (NULL bei Regierungsmitgliedern) |
| `rolle` | Regierungs-/Funktionsrolle (z. B. „Bundesministerin BMF") |
| `redner_label` | Original-Anzeigetext (z. B. „Johannes Vogel (FDP):") |
| `ist_praesidium` | TRUE für Sitzungsleitung-Einwürfe |
| `text`, `wortanzahl` | Redetext ohne Zwischenrufe |

`reden_log` macht die Extraktion resumierbar (analog `extraction_log`
in `analytics.extract`).

## Dimension `person` (warehouse.duckdb)

Die DIP-Ressource `person` (~5 600 Einträge, inkrementell via
`f.aktualisiert`) liefert die Stammdaten: `id`, `vorname`, `nachname`,
`fraktion[]`, `funktion[]`, `wahlperiode[]`. Sie wird seit Aug 2026 mit
ingested. Die Verknüpfung zur `rede`-Tabelle läuft über
Namen + Fraktion (die XML-`redner_id` ist die Bundestags-Stammdaten-ID,
nicht die DIP-`person.id` — ein Mapping-Schritt folgt in Phase 2).

## Roadmap

**Phase 1 — Reden-Fundament (abgeschlossen 2026-08-27)**
- [x] `person` in Ingest + Views + Materialize
- [x] `xml_url`/`herausgeber` in der Plenarprotokoll-View
- [x] `analytics.reden`: XML → `rede`-Tabelle, resumierbar, getestet
- [x] Voller Lauf über WP 20 (214 Protokolle, via „Extract Structured
      Speeches (Reden)"-Workflow)

**Phase 2 — LLM-Zugang (in Arbeit)**
- [x] MCP-Server (`analytics/mcp_server.py`) mit zwei Tools: `query_sql`
      (read-only über warehouse.duckdb + reden.duckdb, Schema-Beschreibung
      im Tool-Docstring) und `search` (bestehende RAG-Suche als
      Beleg-Werkzeug). Deployment als eigener `mcp`-Service in
      docker-compose.yml, gebunden an 127.0.0.1:8765 — Zugriff nur per
      SSH-Tunnel, siehe README.
- [ ] Auf dem VPS deployen (`docker compose up -d --build mcp`) und mit
      einem echten MCP-Client (Claude Desktop/Code) verbinden — noch nicht
      verifiziert.
- [ ] Redner-ID-Mapping `rede.redner_id` ↔ MdB-Stammdaten
      (Open-Data-XML „Stammdaten aller Abgeordneten seit 1949")
- [ ] Reden-Embeddings (Wiederverwendung der extract.py-Maschinerie auf
      `rede.text` statt PDF) für thematische Filterung von Reden

**Phase 3 — optional**
- [ ] WP < 19 über `plenarprotokoll-text` (DIP-Volltext) mit Heuristik-Parser
- [ ] Namentliche Abstimmungen (bundestag.de-XLSX, nicht in DIP enthalten)
- [ ] Graph-Schicht (Kuzu, embedded, liest direkt aus DuckDB) — nur falls
      Netzwerk-/Traversierungsfragen relevant werden

## Betriebsnotizen

- Der öffentliche DIP-API-Key rotiert. Der Key bis Ende Mai 2027:
  `R2BZaee.DjdCyihKZMf8AOjtScubP2EVydegzjmBIQ` (Quelle:
  https://dip.bundestag.de/über-dip/hilfe/api). Der GitHub-Actions-Secret
  `BUNDESTAG_API_KEY` muss entsprechend aktualisiert werden; ein eigener
  Key mit 10 Jahren Laufzeit ist per Mail an
  parlamentsdokumentation@bundestag.de erhältlich (empfohlen).
- Letzte Vollingest laut `state.json`: 2026-06-25. Vor der Reden-Extraktion
  einen frischen Ingest + `materialize` laufen lassen, damit `person` und
  die neuen Plenarprotokoll-Spalten im Snapshot sind.
- `incremental_ingest.yml` und `reden_extract.yml` kopieren ihre
  Ergebnisdateien seit 2026-08-27 explizit nach `/home/christian/bundeswarehouse/`
  (der Ordner, den `docker-compose.yml` und die Next.js-App lesen). Vorher
  bestand hier eine Lücke: Der Self-Hosted-Runner schreibt in sein eigenes
  Workspace-Verzeichnis (`~/actions-runner/_work/...`), nicht in
  `~/bundeswarehouse` — ohne den Publish-Schritt wären `rag`/Web-App/MCP-Server
  nie mit frischen Daten versorgt worden.
- `incremental_ingest.yml` restauriert seit 2026-08-27 vor dem Extract-Schritt
  die vorhandene `embeddings.duckdb` aus `~/bundeswarehouse/`. Ohne das räumt
  `actions/checkout` (`git clean -ffdx`) die Datei bei jedem Lauf weg, wodurch
  `extraction_log` leer aussieht und der Schritt alle ~20 000 WP20-Drucksachen
  neu verarbeitet statt nur der echten Deltas — live beobachtet: ein Lauf hing
  nach 6 Minuten bei „20773 pending, 0 already done".
- `full_ingest.yml`s finaler Merge-Schritt (`merge_embeddings.yml`, läuft auf
  `ubuntu-latest`) hatte einen Schritt „Persist embeddings database to S3",
  der wie ein echter Upload aussah, aber `S3_ENDPOINT_URL` fest auf
  `http://127.0.0.1:9000` gesetzt hatte — einen `services: minio:`-Container,
  der nur für die Job-Laufzeit existiert. Das Ergebnis eines kompletten
  40-Stunden-Laufs landete dadurch nur im 14-Tage-GitHub-Actions-Artifact,
  nie produktiv nutzbar. Seit 2026-08-27 published dieser Schritt stattdessen
  per SSH/SCP nach `~/bundeswarehouse/`, analog zu `web_deploy.yml`.
- Die von `merge_embeddings.yml` published `embeddings.duckdb` hatte nie
  einen HNSW-Index (`--skip-index` im Merge-Schritt). `incremental_ingest.yml`
  war dadurch die erste Stelle, die je einen vollen Index über alle ~20.770
  Dokumente aufbauen wollte — auf dem VPS, der sich RAM mit MinIO/rag/mcp/
  Webapp teilt. Der Prozess wurde vom OOM-Killer gekillt (SIGKILL, kein
  Python-Traceback). Seit 2026-09-04 baut `merge_embeddings.yml` den Index
  selbst (GitHub-gehosteter, exklusiver Runner), und `MEMORY_MAX` (existierte
  vorher nur als toter Env-Var-Text) wird jetzt tatsächlich an
  `SET memory_limit` durchgereicht — in `incremental_ingest.yml` auf `3000M`
  gesetzt als Sicherheitsnetz.
