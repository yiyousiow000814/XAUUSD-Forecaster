import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

class BoundStatement {
  constructor(database, sql, bindings = []) {
    this.database = database;
    this.sql = sql;
    this.bindings = bindings;
  }

  bind(...bindings) {
    return new BoundStatement(this.database, this.sql, bindings);
  }

  execute() {
    const statement = this.database.prepare(this.sql);
    if (statement.columns().length > 0) {
      return { success: true, results: statement.all(...this.bindings), meta: { changes: 0 } };
    }
    const result = statement.run(...this.bindings);
    return { success: true, results: [], meta: { changes: Number(result.changes) } };
  }

  async first() {
    return this.execute().results[0] ?? null;
  }

  async all() {
    return this.execute();
  }

  async run() {
    return this.execute();
  }
}

export class D1TestDatabase {
  constructor(migrations = [
    "0008_news_questions.sql",
    "0009_assistant_conversations.sql",
    "0010_assistant_memory_compaction.sql",
    "0011_assistant_chat_runtime.sql",
    "0012_assistant_turn_lease_bound.sql",
    "0013_assistant_turn_conversation_recovery.sql",
    "0014_assistant_structured_content.sql",
  ]) {
    this.database = new DatabaseSync(":memory:");
    this.database.exec("PRAGMA foreign_keys=ON");
    for (const migrationName of migrations) {
      this.applyMigration(migrationName);
    }
  }

  applyMigration(migrationName) {
    if (!/^\d{4}_[a-z0-9_]+\.sql$/.test(migrationName)) {
      throw new Error("invalid migration name");
    }
    const migration = readFileSync(
      new URL(`../drizzle/${migrationName}`, import.meta.url),
      "utf8",
    );
    this.database.exec(migration);
  }

  prepare(sql) {
    return new BoundStatement(this.database, sql);
  }

  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = statements.map(statement => statement.execute());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }

  row(id, table = "news_questions") {
    if (!/^[a-z_]+$/.test(table)) throw new Error("invalid test table");
    return this.database.prepare(`SELECT * FROM ${table} WHERE id=?`).get(id);
  }
}
