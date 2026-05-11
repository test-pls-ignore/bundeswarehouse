# bundeswarehouse web app

This directory contains the Next.js frontend for browsing and querying Bundestag data prepared by the pipeline in the repository root.

## Features

- Search across `vorgang`, `drucksache`, and `aktivitaet` records.
- Show content-based document hits from the RAG API in search results.
- Browse `plenarprotokoll` rows with filters for Wahlperiode, year, and special sessions.
- Send questions to the optional RAG API exposed by the `/ask` page.
- Analyze Wahlperiode data via the `/analytics` page.

## Requirements

- Node.js 20+ for local development and production builds.
- A readable DuckDB database file. By default the app opens `../warehouse.duckdb`.
- Optional: a running RAG API if you want to use the `/ask` feature.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `DUCKDB_PATH` | `../warehouse.duckdb` | Path to the DuckDB file opened by `web/lib/db.ts`. |
| `RAG_API_URL` | `http://localhost:8000` | Base URL for the optional RAG API used by `app/ask/actions.ts`. |

## Local development

```bash
cd web
npm ci
npm run dev
```

Then open `http://localhost:3000`.

## Useful commands

```bash
npm run lint
npm run build
npm run start
```

## Deployment

Production deployment is handled by `../.github/workflows/web_deploy.yml`.

That workflow:

1. Builds the app on `ubuntu-latest`.
2. Packages `.next`, `public`, `package.json`, `package-lock.json`, and `next.config.ts`.
3. Uploads the tarball to the VPS over SSH on port `2225`.
4. Installs production dependencies on the VPS and restarts the `bundeswarehouse-web` systemd service.

See `../docs/runner.md` for the required deployment secrets and server assumptions.
