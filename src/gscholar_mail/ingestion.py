"""Ingest forwarded Google Scholar alerts from AgentMail into local SQLite.

Order of operations per message:
1. Parse the HTML and write papers to the publications database.
2. Archive the raw HTML and record ``processed`` in ``mail.db``.
3. Only then delete the AgentMail message. Deletion failures are recorded and
   retried on the next run without re-importing papers.

Messages that cannot be parsed into complete papers are archived as ``failed``
and kept in AgentMail so they remain visible and retryable.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from gscholar_mail.agentmail_client import (
    AgentMailClient,
    AgentMailError,
    AgentMailMessage,
)
from gscholar_mail.html_parser import HTMLParser
from gscholar_mail.paper_record import PaperRecord
from gscholar_mail.repository import (
    commit_mail_import,
    commit_publication_import,
    get_message_state,
    record_deletion_result,
)

logger = logging.getLogger(__name__)

PROCESSED = "processed"
FAILED = "failed"
DELETED = "deleted"
PENDING = "pending"

_HTTP_LINK = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class IngestionSummary:
    listed: int = 0
    imported_messages: int = 0
    failed_messages: int = 0
    deleted_messages: int = 0
    deletion_failures: int = 0
    skipped_messages: int = 0


def _is_complete(paper: PaperRecord) -> bool:
    """Strict check: every field needed by the UI and the legacy index must be present."""
    return all((paper.title, paper.authors, paper.data, paper.idx)) and bool(
        _HTTP_LINK.match(paper.link)
    )


def parse_papers(subject: str, html: str) -> list[PaperRecord]:
    """Return complete papers in message order; first-record-wins is applied on insert."""
    parser = HTMLParser(subject)
    parser.parse(html)
    return [paper for paper in parser.papers if _is_complete(paper)]


def _import_message(
    message: AgentMailMessage,
    publications: sqlite3.Connection,
    mail: sqlite3.Connection,
    retry_count: int,
) -> str:
    """Import one fetched message; return PROCESSED or FAILED."""
    error_summary: str | None = None
    papers: list[PaperRecord] = []
    try:
        papers = parse_papers(message.subject, message.html)
    except Exception as error:  # Untrusted input: record the failure and continue.
        error_summary = f"parse error: {type(error).__name__}"

    if not papers:
        commit_mail_import(
            mail,
            message_id=message.message_id,
            html=message.html,
            processing_status=FAILED,
            subject=message.subject,
            sender=message.sender,
            received_at=message.received_at,
            error_summary=error_summary or "no complete Scholar entries",
            retry_count=retry_count + 1,
        )
        logger.warning("Message %s archived as failed", message.message_id)
        return FAILED

    # Publications first: a crash here leaves no processed record, so the retry
    # re-inserts the same papers, which ON CONFLICT(idx) DO NOTHING ignores.
    commit_publication_import(publications, papers)
    commit_mail_import(
        mail,
        message_id=message.message_id,
        html=message.html,
        processing_status=PROCESSED,
        deletion_status=PENDING,
        subject=message.subject,
        sender=message.sender,
        received_at=message.received_at,
    )
    return PROCESSED


def _delete_remote(
    client: AgentMailClient, mail: sqlite3.Connection, message_id: str
) -> bool:
    """Delete after local success and record the outcome; return True when deleted."""
    try:
        client.soft_delete_message(message_id)
    except AgentMailError as error:
        record_deletion_result(
            mail,
            message_id=message_id,
            deletion_status="failed",
            error_summary=str(error),
        )
        logger.warning("Remote deletion failed for message %s; will retry", message_id)
        return False
    record_deletion_result(mail, message_id=message_id, deletion_status=DELETED)
    return True


def _redacted_summary(summary: IngestionSummary) -> str:
    return json.dumps(summary.__dict__, sort_keys=True)


def run_ingestion(
    client: AgentMailClient,
    publications: sqlite3.Connection,
    mail: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> IngestionSummary:
    """Fetch, import, archive, and then delete AgentMail messages.

    The inbox listing is fully read before any deletion so pagination is not
    affected by removals. Already-processed messages are not re-imported; only
    their pending remote deletion is retried.
    """
    summary = IngestionSummary()
    listed = [item.get("message_id") for item in client.list_messages()]
    message_ids = [message_id for message_id in listed if isinstance(message_id, str)]
    summary.listed = len(message_ids)

    seen: set[str] = set()
    dry_run_papers: list[PaperRecord] = []
    for message_id in message_ids:
        if message_id in seen:
            continue
        seen.add(message_id)

        state = get_message_state(mail, message_id)
        if state is not None and state.deletion_status == DELETED:
            summary.skipped_messages += 1
            continue

        if state is not None and state.processing_status == PROCESSED:
            # Local import already committed; only the remote deletion is outstanding.
            if _delete_remote(client, mail, message_id):
                summary.deleted_messages += 1
            else:
                summary.deletion_failures += 1
            continue

        try:
            message = client.get_message(message_id)
        except (AgentMailError, KeyError, ValueError) as error:
            logger.warning(
                "Could not fetch message %s: %s", message_id, type(error).__name__
            )
            summary.skipped_messages += 1
            continue

        if dry_run:
            try:
                papers = parse_papers(message.subject, message.html)
                dry_run_papers.extend(papers)
            except Exception:
                papers = []
            if papers:
                summary.imported_messages += 1
            else:
                summary.failed_messages += 1
            continue

        retry_count = state.retry_count if state is not None else 0
        try:
            outcome = _import_message(message, publications, mail, retry_count)
        except sqlite3.Error as error:
            # Nothing is deleted remotely; the message stays in AgentMail for retry.
            logger.error(
                "Local write failed for message %s: %s",
                message_id,
                type(error).__name__,
            )
            summary.skipped_messages += 1
            continue

        if outcome == FAILED:
            summary.failed_messages += 1
            continue

        summary.imported_messages += 1
        if _delete_remote(client, mail, message_id):
            summary.deleted_messages += 1
        else:
            summary.deletion_failures += 1

    if dry_run:
        for paper in dry_run_papers:
            logger.info(paper)
    return summary
