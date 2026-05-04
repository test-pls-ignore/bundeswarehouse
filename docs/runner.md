# Self-Hosted GitHub Actions Runner Setup

This document explains how to configure a self-hosted GitHub Actions runner on the VPS so that
the bundeswarehouse ingestion workflows can run there.

---

## 1. Why a self-hosted runner?

- The initial full Bundestag download can take several hours and produce GB of data.
  GitHub-hosted runners have a 6-hour job limit and limited disk space.
- With a self-hosted runner the job can access MinIO directly via `localhost` – no public
  network exposure required.
- You control hardware resources and can attach extra storage easily.

---

## 2. Runner labels

All bundeswarehouse workflows target the label set `[self-hosted, vps]`.
Make sure you add **both** labels when registering the runner (step 4 below).

---

## 3. Prerequisites

- The VPS must have outbound internet access (to reach GitHub and the Bundestag API).
- Docker and Docker Compose must already be installed (needed for MinIO).
- Python 3.12 or later must be available on the runner host (or managed via `actions/setup-python`).

---

## 4. Register the runner

1. Go to your repository on GitHub:  
   **Settings → Actions → Runners → New self-hosted runner**
2. Choose **Linux** and the correct architecture (usually `x64`).
3. Follow the on-screen instructions to download and configure the runner.  
   When prompted for labels, add: `self-hosted,vps`

```bash
# Example (replace URL and TOKEN with the values shown by GitHub):
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.317.0/actions-runner-linux-x64-2.317.0.tar.gz
# Note: replace v2.317.0 with the latest runner version shown on the GitHub setup page
tar xzf actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/test-pls-ignore/bundeswarehouse \
            --token YOUR_TOKEN \
            --labels vps \
            --name vps-runner \
            --unattended
```

---

## 5. Run the runner as a systemd service

```bash
# Inside the runner directory:
sudo ./svc.sh install
sudo ./svc.sh start
sudo systemctl status actions.runner.test-pls-ignore-bundeswarehouse.vps-runner
```

The runner will now start automatically on boot.

---

## 6. Required GitHub Actions secrets

Configure these in **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name            | Description                                                  |
|------------------------|--------------------------------------------------------------|
| `S3_ENDPOINT_URL`      | MinIO API URL, e.g. `http://127.0.0.1:9000`                  |
| `S3_ACCESS_KEY_ID`     | MinIO access key                                             |
| `S3_SECRET_ACCESS_KEY` | MinIO secret key                                             |
| `S3_BUCKET`            | Bucket name, e.g. `bundeswarehouse`                          |
| `BUNDESTAG_API_KEY`    | DIP Bundestag API key                                        |
| `SECRET_UPDATER_TOKEN` | PAT with `repo` scope (used by the API key updater workflow) |

---

## 7. Run the workflows

### Initial full load (manual)

1. Go to **Actions → Full Ingest – Bundestag Data**.
2. Click **Run workflow** → select branch `main` → click **Run workflow**.
3. Monitor the run in the Actions tab.

### Incremental updates (automatic)

The `Incremental Ingest` workflow runs automatically every day at 03:00 UTC.
You can also trigger it manually via **Actions → Incremental Ingest – Bundestag Data → Run workflow**.

---

## 8. Environment variables used by the pipeline

All pipeline configuration is done via environment variables (set as GitHub Actions secrets or
in a local `.env` file):

| Variable               | Default                          | Description                  |
|------------------------|----------------------------------|------------------------------|
| `S3_ENDPOINT_URL`      | `http://127.0.0.1:9000`          | MinIO / S3 endpoint          |
| `S3_ACCESS_KEY_ID`     | –                                | Access key (required)        |
| `S3_SECRET_ACCESS_KEY` | –                                | Secret key (required)        |
| `S3_BUCKET`            | `bundeswarehouse`                | Bucket name                  |
| `S3_REGION`            | `us-east-1`                      | Region (cosmetic for MinIO)  |
| `BUNDESTAG_API_KEY`    | –                                | DIP API key (required)       |

---

## 9. Testing the connection locally

```bash
# On the VPS, with the .env file loaded:
export $(grep -v '^#' .env | xargs)
python -m pipeline.cli check-connection
```

---

## 10. Security notes

- Keep MinIO bound to `127.0.0.1` (the default in `docker-compose.yml`).
- Never commit credentials to git. Use `.env` locally and GitHub Secrets in CI.
- The runner process should run as a dedicated non-root user.
- Regularly rotate MinIO credentials and update the GitHub Secrets accordingly.
