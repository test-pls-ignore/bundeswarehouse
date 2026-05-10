import { DuckDBInstance, DuckDBValue } from "@duckdb/node-api";
import path from "path";

const DB_PATH =
  process.env.DUCKDB_PATH ??
  path.resolve(process.cwd(), "../warehouse.duckdb");

let _instance: DuckDBInstance | null = null;

async function getInstance(): Promise<DuckDBInstance> {
  if (!_instance) {
    _instance = await DuckDBInstance.create(DB_PATH, {
      access_mode: "read_only",
    });
  }
  return _instance;
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
