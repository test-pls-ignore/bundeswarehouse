import { DuckDBInstance, DuckDBValue } from "@duckdb/node-api";
import path from "path";

const DB_PATH =
  process.env.DUCKDB_PATH ??
  path.resolve(process.cwd(), "../warehouse.duckdb");

const REDEN_PATH =
  process.env.REDEN_PATH ??
  path.resolve(process.cwd(), "../reden.duckdb");

let _instance: DuckDBInstance | null = null;
let _redenAttached = false;

async function getInstance(): Promise<DuckDBInstance> {
  if (!_instance) {
    _instance = await DuckDBInstance.create(DB_PATH, {
      access_mode: "read_only",
    });
  }
  return _instance;
}

/**
 * Like `query`, but also exposes reden.duckdb's tables under the `reden`
 * schema (e.g. `reden.rede`), attached lazily on first use — most pages
 * only need `warehouse.duckdb` and shouldn't pay for the extra ATTACH.
 */
export async function queryWithReden<
  T extends Record<string, unknown> = Record<string, unknown>
>(sql: string, params: unknown[] = []): Promise<T[]> {
  const instance = await getInstance();
  const conn = await instance.connect();
  try {
    if (!_redenAttached) {
      await conn.run(`ATTACH '${REDEN_PATH}' AS reden (READ_ONLY)`);
      _redenAttached = true;
    }
    const reader = await conn.runAndReadAll(sql, params.length ? (params as DuckDBValue[]) : undefined);
    return reader.getRowObjects() as T[];
  } finally {
    conn.closeSync();
  }
}

export async function query<
  T extends Record<string, unknown> = Record<string, unknown>
>(sql: string, params: unknown[] = []): Promise<T[]> {
  const instance = await getInstance();
  const conn = await instance.connect();
  try {
    const reader = await conn.runAndReadAll(sql, params.length ? (params as DuckDBValue[]) : undefined);
    return reader.getRowObjects() as T[];
  } finally {
    conn.closeSync();
  }
}
