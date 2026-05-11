# bundeswarehouse

A data pipeline that ingests open data from the **Bundestag DIP API** and stores
it in MinIO (S3-compatible object storage) as NDJSON files.

It also includes a prototype RAG stack that indexes linked Drucksachen PDFs
for one Wahlperiode (default: WP 20) into vector embeddings.

---

## Quick start

```bash
# 1. Copy and fill in credentials
cp .env.example .env

# 2. Start MinIO
docker compose up -d

# 3. Run a full load
BUNDESTAG_API_KEY=your_key python -m pipeline.cli full-load

# 4. Run an incremental update (resumes from saved state)
BUNDESTAG_API_KEY=your_key python -m pipeline.cli incremental-update
```

---

## S3 layout

```
raw/
  _staging/
    <run_id>/          ← in-progress full-load data (temporary)
      vorgang/
        batch_00001.ndjson
        …
      drucksache/
        …
  current/             ← latest successful full-load snapshot (canonical)
    vorgang/
      batch_00001.ndjson
      …
    drucksache/
      …
  LATEST_RUN.json      ← pointer: run_id + published_at of the last successful run
manifests/
  state.json
  latest.json          ← manifest of objects currently in raw/current/
```

---

## Latest-only publish strategy

The full-load workflow uses a **staging → publish** approach to guarantee that
`raw/current/` always contains a complete, consistent dataset:

1. **Staging write**: all objects are uploaded to
   `raw/_staging/<run_id>/…` during the run.
2. **Atomic-ish publish** (on success):
   - `raw/current/` is cleared.
   - Objects are copied from `raw/_staging/<run_id>/` to `raw/current/`.
   - `raw/LATEST_RUN.json` is updated with the new `run_id` and timestamp.
   - The staging prefix is deleted.
3. **On failure**: `raw/current/` is **not touched** — it still reflects the
   last successful run.  Staging data is kept for inspection.

### Re-running after a failure

Because every run gets a fresh `run_id`, re-running `full_ingest.yml` is safe:

- A new staging prefix (`raw/_staging/<new_run_id>/`) is created from scratch.
- The old failed staging prefix (`raw/_staging/<old_run_id>/`) is left in place
  until you clean it up.
- `raw/current/` is only updated once the new run completes successfully.

**No duplicates are ever created in `raw/current/`.**

To clean up leftover staging data from failed runs, use the
[Cleanup S3 Prefixes](#cleanup-workflow) workflow.

---

## Environment variables

### Required

| Variable | Description |
|---|---|
| `BUNDESTAG_API_KEY` | API key for the Bundestag DIP API. Obtain from [dip.bundestag.de](https://dip.bundestag.de/über-dip/hilfe/api). |
| `S3_ENDPOINT_URL` | S3 / MinIO endpoint URL (e.g. `http://127.0.0.1:9000`). |
| `S3_ACCESS_KEY_ID` | S3 access key ID. |
| `S3_SECRET_ACCESS_KEY` | S3 secret access key. |

### Optional – S3

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKET` | `bundeswarehouse` | Target S3 bucket name. |
| `S3_REGION` | `us-east-1` | S3 region. |

### Optional – DIP fetch tuning

| Variable | Default | Description |
|---|---|---|
| `DIP_USER_AGENT` | `bundeswarehouse/1.0 (+https://github.com/test-pls-ignore/bundeswarehouse)` | HTTP `User-Agent` header sent with every API request. |
| `DIP_REQUEST_DELAY` | `0.5` | Seconds to sleep between page requests. Increase to reduce the risk of triggering rate limits or WAF blocks. |
| `DIP_MAX_RETRIES` | `3` | Maximum retry attempts for transient `429` / `5xx` responses (exponential backoff with jitter). |
| `DIP_RETRY_BACKOFF_MAX` | `60` | Maximum backoff duration in seconds between retry attempts. |
| `DIP_CHALLENGE_COOLDOWN_MIN` | `600` | Minimum cooldown in seconds after detecting a WAF/anti-bot challenge page before retrying. |
| `DIP_CHALLENGE_COOLDOWN_MAX` | `1800` | Maximum cooldown in seconds after challenge detection; retry sleep is randomized between min/max. |
| `DIP_API_KEY_TRANSPORT` | `header` | API key transport mode: `header` (Authorization) or `query` (adds `apikey` query parameter). |
| `DIP_INCREMENTAL_OVERLAP_MINUTES` | `15` | Overlap window applied to incremental `f.aktualisiert.start` lower bound (minimum enforced: 15). |
| `DIP_MAX_CONCURRENCY` | `1` | Declared request concurrency cap. Must stay within `1..25` per DIP guidance; pipeline executes requests single-threaded. |

### Optional – RAG indexing

| Variable | Default | Description |
|---|---|---|
| `RAG_DEFAULT_WAHLPERIODE` | `20` | Default Wahlperiode filter for retrieval when none is passed. |

---

## Document indexing (RAG MVP)

Index linked Drucksachen PDFs for a single Wahlperiode (default WP 20):

```bash
# 1) Build a local warehouse snapshot from MinIO
python -m pipeline.cli materialize --output warehouse.duckdb

# 2) Download PDFs transiently, extract/chunk/embed, store vectors
python -m analytics.extract --warehouse warehouse.duckdb --embeddings embeddings.duckdb --wahlperiode 20
```

The extractor stores only chunk text + metadata + vectors in `embeddings.duckdb`.
PDF bytes are processed in-memory and are not persisted.

You can also trigger the GitHub workflow **Index Documents for RAG**.

---

## Cleanup workflow

The **Cleanup S3 Prefixes** workflow (`cleanup_staging.yml`) lets you safely
delete S3 prefixes via GitHub Actions without needing direct S3 access.

### How to trigger

1. Go to **Actions → Cleanup S3 Prefixes → Run workflow**.
2. Fill in the inputs:

| Input | Description |
|---|---|
| `confirm` | Must be exactly `DELETE` (all caps). Anything else aborts the job. |
| `target` | What to delete (see table below). |

### Target values

| Target | Prefix deleted | When to use |
|---|---|---|
| `staging` *(default)* | `raw/_staging/` | Clean up leftover staging data from failed runs. |
| `current` | `raw/current/` | Wipe the entire published dataset (data will be empty until next successful full-load). |
| `current/<resource>` | `raw/current/<resource>/` | Wipe one resource, e.g. `current/aktivitaet`. |

### CLI equivalents (local use)

```bash
# Delete all staging data
python -m pipeline.cli cleanup-staging

# Delete the entire published dataset
python -m pipeline.cli cleanup-current

# Delete one resource from the published dataset
python -m pipeline.cli cleanup-current --resource aktivitaet
```

---

## Failure modes

### WAF / anti-bot challenge page (`ChallengePageError`)

The DIP API is fronted by an anti-bot gateway (`.enodia/challenge`). If requests
are blocked, the API redirects to a challenge URL and returns an HTML page instead
of JSON. The pipeline detects this by checking:

- Whether the final URL contains `/.enodia/challenge`.
- Whether the response `Content-Type` is `text/html`.

When detected, the pipeline applies a long cooldown (`DIP_CHALLENGE_COOLDOWN_MIN/MAX`)
between retries to avoid hammering while blocked. If retries are exhausted, it raises
`ChallengePageError` and **exits with a non-zero status code** so the failure is visible
in CI / alerting. It does **not** silently treat the block as "no documents found".

To reduce the likelihood of being blocked:

- Set `DIP_REQUEST_DELAY` to a higher value (e.g. `1.0` or `2.0`).
- Make sure `DIP_USER_AGENT` identifies your bot appropriately.
- Use an authenticated API key with appropriate quotas.

### Transient errors (429 / 5xx / 401)

The pipeline automatically retries up to `DIP_MAX_RETRIES` times with exponential
backoff capped at `DIP_RETRY_BACKOFF_MAX` seconds. If all retries fail, the
exception propagates and the run exits with a non-zero status code.  `401`
responses are treated as transient (some gateways return `401` instead of `429`
when rate-limiting or applying quota controls).

### Pagination stop condition

Per DIP cursor semantics, pagination continues until either:

- `cursor` is missing/empty, or
- `cursor` repeats (no progress).

`documents: []` alone does **not** end pagination if a new cursor is still present.
Any HTTP error, invalid JSON, or unexpected response causes an exception rather than
silently ending pagination.

### Incremental overlap and dedupe

Incremental fetches use `f.aktualisiert.start` and always apply at least a 15-minute
overlap window to account for DIP’s documented indexing delay. Because overlap can
return repeated entities, the pipeline deduplicates document IDs within each
incremental run before upload.
