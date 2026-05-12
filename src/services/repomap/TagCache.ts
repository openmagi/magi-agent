import type { Tag } from "./types.js";

interface SqlJs {
  Database: new (data?: ArrayLike<number>) => SqlJsDatabase;
}

interface SqlJsDatabase {
  run(sql: string, params?: Record<string, unknown>): void;
  exec(sql: string, params?: Record<string, unknown>): { columns: string[]; values: unknown[][] }[];
  export(): Uint8Array;
  close(): void;
}

let sqlJsModule: SqlJs | null = null;

async function getSqlJs(): Promise<SqlJs> {
  if (sqlJsModule) return sqlJsModule;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const initSqlJs = require("sql.js") as (...args: unknown[]) => Promise<SqlJs>;
  sqlJsModule = await initSqlJs();
  return sqlJsModule;
}

export class TagCache {
  private db: SqlJsDatabase | null = null;
  private dbPath: string;
  private dirty = false;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
  }

  async init(): Promise<void> {
    const SQL = await getSqlJs();
    const fs = await import("node:fs");

    let data: Buffer | undefined;
    try {
      data = fs.readFileSync(this.dbPath);
    } catch {
      // no existing DB
    }

    this.db = data ? new SQL.Database(data) : new SQL.Database();
    this.db.run(`
      CREATE TABLE IF NOT EXISTS tags (
        file TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        line INTEGER NOT NULL,
        lang TEXT NOT NULL,
        mtime_ms INTEGER NOT NULL
      )
    `);
    this.db.run(`CREATE INDEX IF NOT EXISTS idx_tags_file ON tags(file)`);
    this.db.run(`CREATE INDEX IF NOT EXISTS idx_tags_mtime ON tags(file, mtime_ms)`);
  }

  getFileMtime(file: string): number | null {
    if (!this.db) return null;
    const result = this.db.exec(
      "SELECT DISTINCT mtime_ms FROM tags WHERE file = :file LIMIT 1",
      { ":file": file },
    );
    if (!result.length || !result[0]!.values.length) return null;
    return result[0]!.values[0]![0] as number;
  }

  getTags(file: string): Tag[] {
    if (!this.db) return [];
    const result = this.db.exec(
      "SELECT file, name, kind, line, lang FROM tags WHERE file = :file",
      { ":file": file },
    );
    if (!result.length) return [];
    return result[0]!.values.map((row) => ({
      file: row[0] as string,
      name: row[1] as string,
      kind: row[2] as "def" | "ref",
      line: row[3] as number,
      language: row[4] as string,
    }));
  }

  setTags(file: string, tags: Tag[], mtimeMs: number): void {
    if (!this.db) return;
    this.db.run("DELETE FROM tags WHERE file = :file", { ":file": file });
    for (const tag of tags) {
      this.db.run(
        "INSERT INTO tags (file, name, kind, line, lang, mtime_ms) VALUES (:file, :name, :kind, :line, :lang, :mtime)",
        {
          ":file": tag.file,
          ":name": tag.name,
          ":kind": tag.kind,
          ":line": tag.line,
          ":lang": tag.language,
          ":mtime": mtimeMs,
        },
      );
    }
    this.dirty = true;
  }

  removeFile(file: string): void {
    if (!this.db) return;
    this.db.run("DELETE FROM tags WHERE file = :file", { ":file": file });
    this.dirty = true;
  }

  getAllTags(): Tag[] {
    if (!this.db) return [];
    const result = this.db.exec("SELECT file, name, kind, line, lang FROM tags");
    if (!result.length) return [];
    return result[0]!.values.map((row) => ({
      file: row[0] as string,
      name: row[1] as string,
      kind: row[2] as "def" | "ref",
      line: row[3] as number,
      language: row[4] as string,
    }));
  }

  async flush(): Promise<void> {
    if (!this.db || !this.dirty) return;
    const fs = await import("node:fs");
    const path = await import("node:path");
    const dir = path.dirname(this.dbPath);
    fs.mkdirSync(dir, { recursive: true });
    const data = this.db.export();
    fs.writeFileSync(this.dbPath, Buffer.from(data));
    this.dirty = false;
  }

  close(): void {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }
}
