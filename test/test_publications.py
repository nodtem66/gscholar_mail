"""Tests for the shared publications data-access layer."""

import sqlite3
from pathlib import Path

import pytest

from gscholar_mail.database import PUBLICATIONS_SCHEMA
from gscholar_mail.publications import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    READ_STATUS_READ,
    READ_STATUS_UNREAD,
    PublicationsDatabaseError,
    UnreadPage,
    count_unread,
    list_unread,
    now_iso,
    open_database,
    set_read_status,
)

SEED_ROWS = [
    # (id, status, updated_at)
    (1, READ_STATUS_UNREAD, "2024-01-01T00:00:00.000000"),
    (2, READ_STATUS_UNREAD, "2024-01-02T00:00:00.000000"),
    (3, READ_STATUS_READ, "2024-01-03T00:00:00.000000"),
    (4, READ_STATUS_UNREAD, "2024-01-04T00:00:00.000000"),
]


def build_database(path: Path, rows: list[tuple[int, int, str]] = SEED_ROWS) -> Path:
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(PUBLICATIONS_SCHEMA)
        for paper_id, status, updated_at in rows:
            connection.execute(
                """
                INSERT INTO papers (id, idx, title, data, authors, link,
                                    email_title, year, status, created_at, updated_at)
                VALUES (?, ?, ?, '', '', '', '', 2024, ?, ?, ?)
                """,
                (paper_id, f"IDX{paper_id}", f"Paper {paper_id}", status,
                 updated_at, updated_at),
            )
    connection.close()
    return path


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return build_database(tmp_path / "publications.db")


def test_page_geometry_helpers() -> None:
    assert UnreadPage([], total=0, page=1, per_page=25).total_pages == 0
    assert UnreadPage([], total=0, page=1, per_page=25).has_more is False
    assert UnreadPage([], total=100, page=4, per_page=25).total_pages == 4
    assert UnreadPage([], total=100, page=4, per_page=25).has_more is False
    assert UnreadPage([], total=101, page=4, per_page=25).has_more is True


def test_open_database_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PublicationsDatabaseError, match="DATABASE_PATH"):
        open_database(tmp_path / "nope.db")


def test_open_database_readonly_rejects_writes(database: Path) -> None:
    with open_database(database, readonly=True) as connection:
        assert count_unread(connection) == 3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE papers SET status = 1")


def test_open_database_readonly_does_not_create_rows(database: Path) -> None:
    with open_database(database, readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 4


def test_list_unread_orders_newest_first_and_limits_columns(
    database: Path,
) -> None:
    with open_database(database, readonly=True) as connection:
        page = list_unread(connection, page=1, per_page=2)

    assert [paper.id for paper in page.papers] == [4, 2]
    assert page.papers[0].title == "Paper 4"
    assert page.papers[0].year == 2024
    assert page.total == 3
    assert page.total_pages == 2
    assert page.has_more is True


def test_list_unread_empty_database_reports_no_pages(tmp_path: Path) -> None:
    empty = build_database(tmp_path / "empty.db", rows=[])
    with open_database(empty, readonly=True) as connection:
        page = list_unread(connection, page=1, per_page=DEFAULT_PAGE_SIZE)

    assert page.papers == []
    assert page.total == 0
    assert page.total_pages == 0
    assert page.has_more is False


def test_list_unread_accepts_max_page_size(database: Path) -> None:
    with open_database(database, readonly=True) as connection:
        page = list_unread(connection, page=1, per_page=MAX_PAGE_SIZE)

    assert len(page.papers) == 3


def test_set_read_status_preserves_not_found_order(database: Path) -> None:
    with open_database(database) as connection:
        result = set_read_status(connection, [9, 7, 1, 8], read=True)

    assert result.not_found == [9, 7, 8]
    assert result.updated == 1
    assert result.requested == 4


def test_set_read_status_ignores_duplicate_ids(database: Path) -> None:
    with open_database(database) as connection:
        result = set_read_status(connection, [2, 2, 2], read=True)

    assert result.requested == 1
    assert result.updated == 1
    assert result.unread == 2


def test_set_read_status_reports_new_unread_count(database: Path) -> None:
    with open_database(database) as connection:
        assert set_read_status(connection, [1, 2, 4], read=True).unread == 0
        assert set_read_status(connection, [3], read=False).unread == 1


def test_set_read_status_rolls_back_on_failure(tmp_path: Path) -> None:
    path = build_database(tmp_path / "broken.db")
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE papers")
    connection.commit()
    try:
        with pytest.raises(sqlite3.Error):
            set_read_status(connection, [1], read=True)
    finally:
        connection.close()


def test_set_read_status_records_iso_timestamp(database: Path) -> None:
    before = now_iso()
    with open_database(database) as connection:
        set_read_status(connection, [1], read=True)
    with open_database(database, readonly=True) as connection:
        (updated_at,) = connection.execute(
            "SELECT updated_at FROM papers WHERE id = 1"
        ).fetchone()

    assert updated_at >= before
    assert "T" in updated_at
