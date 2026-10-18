"""Tests for the MCP server tools against a temporary publications database."""

import sqlite3
from pathlib import Path

import pytest
from mcp import Client

from gscholar_mail.database import PUBLICATIONS_SCHEMA
from gscholar_mail.mcp_server import create_server

pytestmark = pytest.mark.asyncio

# (idx, title, year, status, updated_at) — ordering is newest-first.
SEED_ROWS = [
    ("A", "Paper A", 2020, 0, "2024-01-01T00:00:00.000000"),
    ("B", "Paper B", 2021, 0, "2024-01-02T00:00:00.000000"),
    ("C", "Paper C", 2022, 1, "2024-01-03T00:00:00.000000"),
    ("D", "Paper D", None, 0, "2024-01-04T00:00:00.000000"),
]
UNREAD_IDS_BY_UPDATED_AT = [4, 2, 1]


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "publications.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(PUBLICATIONS_SCHEMA)
        connection.executemany(
            """
            INSERT INTO papers (idx, title, data, authors, link, email_title,
                                year, status, created_at, updated_at)
            VALUES (?, ?, '', '', '', '', ?, ?, ?, ?)
            """,
            [
                (idx, title, year, status, updated_at, updated_at)
                for idx, title, year, status, updated_at in SEED_ROWS
            ],
        )
    connection.close()
    return path


async def call(database_path: Path, name: str, arguments: dict | None = None):
    server = create_server(database_path)
    async with Client(server, raise_exceptions=True) as client:
        return await client.call_tool(name, arguments or {})


async def test_create_server_tools_are_registered() -> None:
    server = create_server()
    async with Client(server, raise_exceptions=True) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]

    assert names == [
        "list_unread_papers",
        "count_unread_papers",
        "set_papers_read_status",
    ]


async def test_list_unread_papers_returns_only_summary_fields(
    database_path: Path,
) -> None:
    result = await call(database_path, "list_unread_papers", {"per_page": 2})
    page = result.structured_content

    assert not result.is_error
    assert [paper["id"] for paper in page["papers"]] == [4, 2]
    assert set(page["papers"][0]) == {"id", "title", "year"}
    assert page["papers"][0] == {"id": 4, "title": "Paper D", "year": None}
    assert page["total"] == 3
    assert page["page"] == 1
    assert page["per_page"] == 2
    assert page["total_pages"] == 2
    assert page["has_more"] is True


async def test_list_unread_papers_excludes_read_papers(database_path: Path) -> None:
    result = await call(database_path, "list_unread_papers", {"per_page": 100})
    ids = [paper["id"] for paper in result.structured_content["papers"]]

    assert ids == UNREAD_IDS_BY_UPDATED_AT


async def test_list_unread_papers_last_page(database_path: Path) -> None:
    result = await call(database_path, "list_unread_papers", {"page": 2, "per_page": 2})
    page = result.structured_content

    assert [paper["id"] for paper in page["papers"]] == [1]
    assert page["total_pages"] == 2
    assert page["has_more"] is False


async def test_list_unread_papers_page_past_the_end(database_path: Path) -> None:
    result = await call(database_path, "list_unread_papers", {"page": 50, "per_page": 2})
    page = result.structured_content

    assert page["papers"] == []
    assert page["total"] == 3
    assert page["has_more"] is False


async def test_list_unread_papers_default_page_size(database_path: Path) -> None:
    page = (await call(database_path, "list_unread_papers")).structured_content

    assert page["per_page"] == 25
    assert page["page"] == 1
    assert page["total_pages"] == 1


async def test_list_unread_papers_rejects_out_of_range_page_size(
    database_path: Path,
) -> None:
    result = await call(database_path, "list_unread_papers", {"per_page": 500})

    assert result.is_error
    assert "less than or equal to 100" in result.content[0].text


async def test_count_unread_papers(database_path: Path) -> None:
    result = await call(database_path, "count_unread_papers")

    assert not result.is_error
    assert result.structured_content == {"result": 3}


async def test_set_papers_read_status_marks_batch(database_path: Path) -> None:
    outcome = (
        await call(
            database_path,
            "set_papers_read_status",
            {"ids": [1, 3, 4], "read": True},
        )
    ).structured_content

    assert outcome["read"] is True
    assert outcome["requested"] == 3
    assert outcome["updated"] == 2
    assert outcome["already_in_state"] == 1
    assert outcome["not_found"] == []
    assert outcome["unread"] == 1


async def test_set_papers_read_status_defaults_to_marking_read(
    database_path: Path,
) -> None:
    outcome = (
        await call(database_path, "set_papers_read_status", {"ids": [1]})
    ).structured_content

    assert outcome["read"] is True
    assert outcome["unread"] == 2


async def test_set_papers_read_status_reports_unknown_ids(database_path: Path) -> None:
    outcome = (
        await call(
            database_path,
            "set_papers_read_status",
            {"ids": [1, 999, 1000], "read": True},
        )
    ).structured_content

    assert outcome["updated"] == 1
    assert outcome["not_found"] == [999, 1000]
    assert outcome["unread"] == 2


async def test_set_papers_read_status_counts_ids_already_in_state(
    database_path: Path,
) -> None:
    outcome = (
        await call(
            database_path,
            "set_papers_read_status",
            {"ids": [1, 3], "read": True},
        )
    ).structured_content

    assert outcome["updated"] == 1
    assert outcome["already_in_state"] == 1
    assert outcome["not_found"] == []


async def test_set_papers_read_status_is_idempotent(database_path: Path) -> None:
    first = (
        await call(database_path, "set_papers_read_status", {"ids": [1], "read": True})
    ).structured_content
    second = (
        await call(database_path, "set_papers_read_status", {"ids": [1], "read": True})
    ).structured_content

    assert first["updated"] == 1
    assert second["updated"] == 0
    assert second["already_in_state"] == 1
    assert second["unread"] == first["unread"]


async def test_set_papers_read_status_unmarks_papers(database_path: Path) -> None:
    await call(database_path, "set_papers_read_status", {"ids": [1], "read": True})
    outcome = (
        await call(database_path, "set_papers_read_status", {"ids": [1, 3], "read": False})
    ).structured_content

    assert outcome["read"] is False
    assert outcome["updated"] == 2
    assert outcome["unread"] == 4
    ids = (await call(database_path, "list_unread_papers")).structured_content["papers"]
    # Re-reading bumps updated_at, so the newly unmarked papers sort newest-first.
    assert [paper["id"] for paper in ids] == [3, 1, 4, 2]


async def test_set_papers_read_status_deduplicates_ids(database_path: Path) -> None:
    outcome = (
        await call(
            database_path,
            "set_papers_read_status",
            {"ids": [1, 1, 1, 1], "read": True},
        )
    ).structured_content

    assert outcome["requested"] == 1
    assert outcome["updated"] == 1


async def test_set_papers_read_status_writes_iso_timestamp(
    database_path: Path,
) -> None:
    original = read_updated_at(database_path, 1)
    await call(database_path, "set_papers_read_status", {"ids": [1], "read": True})
    updated = read_updated_at(database_path, 1)

    assert updated > original
    assert "T" in updated


def read_updated_at(database_path: Path, paper_id: int) -> str:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT updated_at FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
    finally:
        connection.close()
    return row[0]


async def test_set_papers_read_status_rejects_empty_ids(database_path: Path) -> None:
    result = await call(database_path, "set_papers_read_status", {"ids": []})

    assert result.is_error
    assert "at least 1 item" in result.content[0].text


async def test_missing_database_reports_tool_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    result = await call(missing, "count_unread_papers")

    assert result.is_error
    assert "DATABASE_PATH" in result.content[0].text


async def test_missing_database_is_not_created_by_write_tool(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    result = await call(missing, "set_papers_read_status", {"ids": [1], "read": True})

    assert result.is_error
    assert not missing.exists()
