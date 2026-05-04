# bundeswarehouse

A data pipeline that ingests open data from the **Bundestag DIP API** and stores
it in MinIO (S3-compatible object storage) as NDJSON files.

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

---

## Failure modes

### WAF / anti-bot challenge page (`ChallengePageError`)

The DIP API is fronted by an anti-bot gateway (`.enodia/challenge`). If requests
are blocked, the API redirects to a challenge URL and returns an HTML page instead
of JSON. The pipeline detects this by checking:

- Whether the final URL contains `/.enodia/challenge`.
- Whether the response `Content-Type` is `text/html`.

When detected, the pipeline raises `ChallengePageError` and **exits with a non-zero
status code** so the failure is visible in CI / alerting. It does **not** silently
treat the block as "no documents found".

To reduce the likelihood of being blocked:

- Set `DIP_REQUEST_DELAY` to a higher value (e.g. `1.0` or `2.0`).
- Make sure `DIP_USER_AGENT` identifies your bot appropriately.
- Use an authenticated API key with appropriate quotas.

### Transient errors (429 / 5xx)

The pipeline automatically retries up to `DIP_MAX_RETRIES` times with exponential
backoff capped at `DIP_RETRY_BACKOFF_MAX` seconds. If all retries fail, the
exception propagates and the run exits with a non-zero status code.

### Incorrect stop condition

Pagination only stops when a **valid JSON response** is received that contains no
documents (or no cursor). Any HTTP error, invalid JSON, or unexpected response
causes an exception rather than silently ending pagination.
