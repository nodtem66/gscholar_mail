"""Read and update publication read state in the publications database.

Shared by the web app and the MCP server so both use the same status values,
ordering, and timestamp format.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

READ_STATUS_UNREAD = 0
READ_STATUS_READ = 1

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_BATCH_SIZE = 1000


class PublicationsDatabaseError(RuntimeError):
    """Raised when the publications database is missing or cannot be queried."""


def now_iso() -> str:
    """Timestamp format used by ingestion and both writers."""
    return datetime.now().isoformat()


def open_database(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open an existing publications database without creating it.

    Read-only connections use SQLite's ``mode=ro`` URI so a missing or
    unwritable file fails instead of being created or modified.
    """
    if not path.is_file():
        raise PublicationsDatabaseError(
            f"Publications database not found at {path}. Run the ingestion "
            "command first and check DATABASE_PATH."
        )
    try:
        if readonly:
            connection = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro", uri=True
            )
        else:
            connection = sqlite3.connect(path)
    except sqlite3.Error as error:
        raise PublicationsDatabaseError(
            f"Cannot open publications database at {path}: {error}"
        ) from error
    connection.row_factory = sqlite3.Row
    return connection


@dataclass(frozen=True)
class PaperSummary:
    id: int
    title: str
    year: int | None


@dataclass(frozen=True)
class UnreadPage:
    papers: list[PaperSummary]
    total: int
    page: int
    per_page: int

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.per_page)

    @property
    def has_more(self) -> bool:
        return self.page < self.total_pages


@dataclass(frozen=True)
class ReadStatusResult:
    read: bool
    requested: int
    updated: int
    already_in_state: int
    not_found: list[int]
    unread: int


def count_unread(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM papers WHERE status = ?", (READ_STATUS_UNREAD,)
    ).fetchone()
    return int(row[0])


def list_unread(
    connection: sqlite3.Connection, *, page: int, per_page: int
) -> UnreadPage:
    """Return one page of unread papers, newest first."""
    total = count_unread(connection)
    rows = connection.execute(
        """
        SELECT id, title, year
        FROM papers
        WHERE status = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (READ_STATUS_UNREAD, per_page, (page - 1) * per_page),
    ).fetchall()
    return UnreadPage(
        papers=[
            PaperSummary(id=row["id"], title=row["title"], year=row["year"])
            for row in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


def set_read_status(
    connection: sqlite3.Connection, ids: Sequence[int], *, read: bool
) -> ReadStatusResult:
    """Set the read state for a batch of papers in one transaction.

    Ids that do not exist are reported instead of failing the whole batch, and
    ids already in the requested state are counted but not rewritten.
    """
    requested = list(dict.fromkeys(ids))
    target = READ_STATUS_READ if read else READ_STATUS_UNREAD
    placeholders = ", ".join("?" for _ in requested)
    with connection:
        rows = connection.execute(
            f"SELECT id, status FROM papers WHERE id IN ({placeholders})",
            requested,
        ).fetchall()
        existing = {row["id"]: row["status"] for row in rows}
        to_update = [paper_id for paper_id, status in existing.items() if status != target]
        if to_update:
            update_placeholders = ", ".join("?" for _ in to_update)
            connection.execute(
                f"UPDATE papers SET status = ?, updated_at = ? "
                f"WHERE id IN ({update_placeholders})",
                [target, now_iso(), *to_update],
            )
        unread = count_unread(connection)
    return ReadStatusResult(
        read=read,
        requested=len(requested),
        updated=len(to_update),
        already_in_state=len(existing) - len(to_update),
        not_found=[paper_id for paper_id in requested if paper_id not in existing],
        unread=unread,
    )
