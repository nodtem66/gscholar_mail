"""MCP server exposing unread publication tools over stdio.

Run with ``python -m gscholar_mail.mcp_server``. The server reads the same
publications database as the web app (``DATABASE_PATH``) and only ever changes
the publication read state; it never reads the mail database and never syncs.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from gscholar_mail.config import load_environment, load_publications_path
from gscholar_mail.publications import (
    DEFAULT_PAGE_SIZE,
    MAX_BATCH_SIZE,
    MAX_PAGE_SIZE,
    PublicationsDatabaseError,
    count_unread,
    list_unread,
    open_database,
    set_read_status,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "gscholar_mail"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = """\
Tools for the local Google Scholar publication database.

Every paper has a read state: unread (status 0) or read (status 1). Use
list_unread_papers to browse unread papers and count_unread_papers for the
total. Use set_papers_read_status to mark reviewed papers as read, or to move
them back to unread.

The database is local and single-user. There is no synchronization tool here;
ingestion is a separate manual command.
"""


@dataclass(frozen=True)
class PaperSummaryOut:
    """One unread paper."""

    id: int
    title: str
    year: int | None


@dataclass(frozen=True)
class UnreadPapersPage:
    """One page of unread papers, newest first."""

    papers: list[PaperSummaryOut]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_more: bool


@dataclass(frozen=True)
class ReadStatusOutcome:
    """Result of a batch read-state change."""

    read: bool
    requested: int
    updated: int
    already_in_state: int
    not_found: list[int]
    unread: int


def create_server(database_path: Path | None = None) -> MCPServer:
    """Build the MCP server, optionally pinned to a specific database."""

    def resolve_path() -> Path:
        if database_path is not None:
            return database_path
        load_environment()
        return load_publications_path()

    @contextmanager
    def connection(*, readonly: bool) -> Iterator[sqlite3.Connection]:
        try:
            opened = open_database(resolve_path(), readonly=readonly)
        except PublicationsDatabaseError as error:
            raise ToolError(str(error)) from error
        try:
            yield opened
        except sqlite3.Error as error:
            raise ToolError(f"Publications database error: {error}") from error
        finally:
            opened.close()

    mcp = MCPServer(
        SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS
    )

    @mcp.tool(
        title="List unread papers",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_unread_papers(
        page: Annotated[int, Field(ge=1, description="1-based page number.")] = 1,
        per_page: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_PAGE_SIZE,
                description=f"Papers per page, 1-{MAX_PAGE_SIZE}.",
            ),
        ] = DEFAULT_PAGE_SIZE,
    ) -> UnreadPapersPage:
        """List unread papers, newest first, with only id, title, and year.

        Returns one page plus the total unread count so callers can paginate.
        """
        with connection(readonly=True) as database:
            result = list_unread(database, page=page, per_page=per_page)
        logger.debug(
            "Listed %d of %d unread papers (page %d)",
            len(result.papers),
            result.total,
            result.page,
        )
        return UnreadPapersPage(
            papers=[
                PaperSummaryOut(id=paper.id, title=paper.title, year=paper.year)
                for paper in result.papers
            ],
            total=result.total,
            page=result.page,
            per_page=result.per_page,
            total_pages=result.total_pages,
            has_more=result.has_more,
        )

    @mcp.tool(
        title="Count unread papers",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def count_unread_papers() -> int:
        """Return the number of unread papers."""
        with connection(readonly=True) as database:
            return count_unread(database)

    @mcp.tool(
        title="Set papers read state",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def set_papers_read_status(
        ids: Annotated[
            list[Annotated[int, Field(ge=1)]],
            Field(
                min_length=1,
                max_length=MAX_BATCH_SIZE,
                description="Publication ids to update.",
            ),
        ],
        read: Annotated[
            bool,
            Field(
                description=(
                    "True marks the papers as read; false moves them back to unread."
                )
            ),
        ] = True,
    ) -> ReadStatusOutcome:
        """Mark or unmark a batch of papers as read by publication id.

        Ids that do not exist are reported in not_found instead of failing the
        batch. Ids already in the requested state are counted in
        already_in_state and left untouched.
        """
        with connection(readonly=False) as database:
            result = set_read_status(database, ids, read=read)
        logger.info(
            "Set read=%s for %d paper(s); %d updated, %d not found",
            read,
            result.requested,
            result.updated,
            len(result.not_found),
        )
        return ReadStatusOutcome(
            read=result.read,
            requested=result.requested,
            updated=result.updated,
            already_in_state=result.already_in_state,
            not_found=result.not_found,
            unread=result.unread,
        )

    return mcp


mcp = create_server()


if __name__ == "__main__":
    mcp.run()
