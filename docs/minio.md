# MinIO Setup on the VPS

MinIO provides an S3-compatible object storage server that runs entirely on your own machine.
The bundeswarehouse pipeline stores raw data, processed outputs, and manifest files in MinIO.

---

## 1. Prerequisites

- Docker and Docker Compose installed on the VPS
- At least 50 GB free disk space on `/srv/minio/data` (or another volume)

---

## 2. Create the data directory

```bash
sudo mkdir -p /srv/minio/data
sudo chown $USER:$USER /srv/minio/data
```

---

## 3. Configure credentials

Copy the example environment file and set strong credentials:

```bash
cp .env.example .env
# Edit .env with your favourite editor:
nano .env
```

The important variables in `.env`:

| Variable               | Description                                  |
|------------------------|----------------------------------------------|
| `MINIO_ROOT_USER`      | MinIO administrator username                 |
| `MINIO_ROOT_PASSWORD`  | MinIO administrator password (min 8 chars)   |
| `S3_ACCESS_KEY_ID`     | Access key for the pipeline (can equal root) |
| `S3_SECRET_ACCESS_KEY` | Secret key for the pipeline                  |
| `S3_BUCKET`            | Bucket name (default: `bundeswarehouse`)     |
| `S3_ENDPOINT_URL`      | MinIO API URL (default: `http://127.0.0.1:9000`) |

> **Never commit `.env` to git!** It is already listed in `.gitignore`.

---

## 4. Start MinIO

```bash
docker compose up -d
```

MinIO is now running with:
- **API** on `127.0.0.1:9000` (S3-compatible endpoint)
- **Console** on `127.0.0.1:9001` (browser UI)

Both ports are bound to `localhost` only – they are **not** reachable from the internet.

---

## 5. Create the bucket

Use the MinIO client (`mc`) inside Docker:

```bash
# Configure mc alias
docker run --rm --network host \
  minio/mc alias set local http://127.0.0.1:9000 \
    "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}"

# Create the bucket
docker run --rm --network host \
  minio/mc mb local/bundeswarehouse

# Verify
docker run --rm --network host \
  minio/mc ls local
```

Or, if `mc` is installed on the host:

```bash
mc alias set local http://127.0.0.1:9000 minioadmin changeme_strong_password
mc mb local/bundeswarehouse
```

The pipeline also creates the bucket automatically on first run via `ensure_bucket_exists()`.

---

## 6. Access the MinIO Console via SSH tunnel

Because the console is bound to `127.0.0.1:9001` on the VPS, access it from your laptop with
an SSH tunnel:

```bash
ssh -L 9001:127.0.0.1:9001 your_user@your_vps_ip
```

Then open `http://localhost:9001` in your browser.
Log in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`.

Similarly for the API:

```bash
ssh -L 9000:127.0.0.1:9000 your_user@your_vps_ip
```

---

## 7. (Optional) Expose MinIO via reverse proxy

If you later need direct HTTPS access (e.g. for public datasets), put Caddy or Nginx in front.

**Example Caddy snippet** (`Caddyfile`):
```
minio.example.com {
    reverse_proxy 127.0.0.1:9000
}
```

> Keep the admin console (`9001`) **never** publicly exposed.

---

## 8. Storage layout inside the bucket

```
bundeswarehouse/
  raw/
    vorgang/YYYY-MM-DD/batch_00000.ndjson
    drucksache/YYYY-MM-DD/batch_00000.ndjson
    ...
  processed/
    ...
  manifests/
    latest.json
    YYYY-MM-DD.json
    state.json
```

---

## 9. Verify the pipeline can connect

```bash
export $(grep -v '^#' .env | xargs)
python -m pipeline.cli check-connection
```

Expected output:
```
OK: connected to bucket 'bundeswarehouse'.
```
