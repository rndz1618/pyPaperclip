from __future__ import annotations

import sqlite3
from typing import Callable


class MigrationRunner:
    """Small idempotent SQLite migration runner for the single-file deployment."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def run(self) -> int:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        migrations: list[tuple[int, Callable[[], None]]] = [
            (1, self._migration_reliability_columns),
            (2, self._migration_agent_config),
            (3, self._migration_dead_letter_queue),
        ]
        applied_count = 0
        for version, migration in migrations:
            if version in applied:
                continue
            migration()
            self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))", (version,))
            applied_count += 1
        self.conn.commit()
        return applied_count

    def _migration_reliability_columns(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(tasks)")}
        statements = {
            "idempotency_key": "ALTER TABLE tasks ADD COLUMN idempotency_key TEXT",
            "attempt_count": "ALTER TABLE tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "ALTER TABLE tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
            "lease_id": "ALTER TABLE tasks ADD COLUMN lease_id TEXT",
            "lease_expires_at": "ALTER TABLE tasks ADD COLUMN lease_expires_at TEXT",
            "available_at": "ALTER TABLE tasks ADD COLUMN available_at TEXT",
            "last_error": "ALTER TABLE tasks ADD COLUMN last_error TEXT",
        }
        for column, statement in statements.items():
            if column not in columns:
                self.conn.execute(statement)
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(company_id, idempotency_key) WHERE idempotency_key IS NOT NULL")

    def _migration_agent_config(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(agents)")}
        if "config_json" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'")

    def _migration_dead_letter_queue(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS dead_letter_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dead_letter_company ON dead_letter_tasks(company_id, created_at DESC);
        """)
