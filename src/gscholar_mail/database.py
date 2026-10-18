"""SQLite schema and migrations for publications and archived mail."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

PUBLICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    idx TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    data TEXT NOT NULL,
    authors TEXT NOT NULL,
    link TEXT NOT NULL,
    email_title TEXT NOT NULL,
    year INTEGER,
    status INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Supports status filtering and stable newest-first ordering.
CREATE INDEX IF NOT EXISTS idx_papers_status_updated
    ON papers (status, updated_at DESC);
"""

MAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_messages (
    message_id TEXT PRIMARY KEY,
    received_at TEXT,
    subject TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processed_at TEXT,
    deletion_status TEXT NOT NULL DEFAULT 'pending',
    error_summary TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_messages_processing
    ON source_messages (processing_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_messages_deletion
    ON source_messages (deletion_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS raw_messages (
    message_id TEXT PRIMARY KEY,
    received_at TEXT,
    subject TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    html TEXT NOT NULL,
    archived_at TEXT NOT NULL
);
"""


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _apply_schema(connection: sqlite3.Connection, schema: str) -> None:
    with connection:
        connection.executescript(schema)
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif row[0] > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {row[0]} is newer than supported version "
                f"{SCHEMA_VERSION}"
            )
        elif row[0] < SCHEMA_VERSION:
            raise RuntimeError("Database migration is required before startup")


def initialize_databases(publications_path: Path, mail_path: Path) -> None:
    """Create or validate databases without storing API credentials.

    The default rollback journal is retained. Backups should be made while the
    application is stopped or idle, after pending transactions complete; no
    WAL or sidecar files need to be copied.
    """
    with _open_database(publications_path) as publications:
        _apply_schema(publications, PUBLICATIONS_SCHEMA)
    with _open_database(mail_path) as mail:
        _apply_schema(mail, MAIL_SCHEMA)
