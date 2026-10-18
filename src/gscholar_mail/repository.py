"""Parameterized SQLite operations used by the ingestion workflow."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from gscholar_mail.paper_record import PaperRecord


@dataclass(frozen=True)
class MessageState:
    processing_status: str
    deletion_status: str
    retry_count: int


def _now() -> str:
    return datetime.now().isoformat()


def get_message_state(
    connection: sqlite3.Connection, message_id: str
) -> MessageState | None:
    row = connection.execute(
        """
        SELECT processing_status, deletion_status, retry_count
        FROM source_messages WHERE message_id = ?
        """,
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    return MessageState(
        processing_status=row[0], deletion_status=row[1], retry_count=row[2]
    )


def upsert_papers(
    connection: sqlite3.Connection, papers: Iterable[PaperRecord]
) -> None:
    """Insert papers idempotently; the first record wins permanently."""
    connection.executemany(
        """
        INSERT INTO papers (
            idx, title, data, authors, link, email_title, year, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idx) DO NOTHING
        """,
        [
            (
                paper.idx,
                paper.title,
                paper.data,
                paper.authors,
                paper.link,
                paper.email_title,
                paper.year,
                paper.status,
                paper.created_at or _now(),
                paper.updated_at or _now(),
            )
            for paper in papers
        ],
    )


def archive_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    html: str,
    subject: str = "",
    sender: str = "",
    received_at: str | None = None,
) -> None:
    """Archive raw HTML without creating duplicate rows on retry."""
    now = _now()
    connection.execute(
        """
        INSERT INTO raw_messages (
            message_id, received_at, subject, sender, html, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            received_at = excluded.received_at,
            subject = excluded.subject,
            sender = excluded.sender,
            html = excluded.html
        """,
        (message_id, received_at, subject, sender, html, now),
    )


def record_message_result(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    processing_status: str,
    deletion_status: str = "pending",
    subject: str = "",
    sender: str = "",
    received_at: str | None = None,
    error_summary: str | None = None,
    retry_count: int = 0,
) -> None:
    """Record a retryable processing/deletion state for an AgentMail message."""
    now = _now()
    processed_at = now if processing_status == "processed" else None
    connection.execute(
        """
        INSERT INTO source_messages (
            message_id, received_at, subject, sender, processing_status,
            processed_at, deletion_status, error_summary, retry_count,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            received_at = excluded.received_at,
            subject = excluded.subject,
            sender = excluded.sender,
            processing_status = excluded.processing_status,
            processed_at = excluded.processed_at,
            deletion_status = excluded.deletion_status,
            error_summary = excluded.error_summary,
            retry_count = excluded.retry_count,
            updated_at = excluded.updated_at
        """,
        (
            message_id,
            received_at,
            subject,
            sender,
            processing_status,
            processed_at,
            deletion_status,
            error_summary,
            retry_count,
            now,
            now,
        ),
    )


def record_deletion_result(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    deletion_status: str,
    error_summary: str | None = None,
) -> None:
    """Persist the remote deletion outcome; failed attempts increment retry_count."""
    with connection:
        connection.execute(
            """
            UPDATE source_messages SET
                deletion_status = ?,
                error_summary = ?,
                retry_count = retry_count + CASE WHEN ? = 'failed' THEN 1 ELSE 0 END,
                updated_at = ?
            WHERE message_id = ?
            """,
            (deletion_status, error_summary, deletion_status, _now(), message_id),
        )


def commit_publication_import(
    publications: sqlite3.Connection,
    papers: Iterable[PaperRecord],
) -> None:
    """Commit all paper changes as one transaction."""
    with publications:
        upsert_papers(publications, papers)


def commit_mail_import(
    mail: sqlite3.Connection,
    *,
    message_id: str,
    html: str,
    processing_status: str,
    deletion_status: str = "pending",
    subject: str = "",
    sender: str = "",
    received_at: str | None = None,
    error_summary: str | None = None,
    retry_count: int = 0,
) -> None:
    """Commit archive and processing state together in the mail database."""
    with mail:
        archive_message(
            mail,
            message_id=message_id,
            html=html,
            subject=subject,
            sender=sender,
            received_at=received_at,
        )
        record_message_result(
            mail,
            message_id=message_id,
            processing_status=processing_status,
            deletion_status=deletion_status,
            subject=subject,
            sender=sender,
            received_at=received_at,
            error_summary=error_summary,
            retry_count=retry_count,
        )
