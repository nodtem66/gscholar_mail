# Gscholar Mail

Local application for ingesting Google Scholar alerts forwarded to a dedicated AgentMail inbox. The workflow is intentionally split: run the manual AgentMail sync first, then start the local web app to browse the SQLite databases. The web app does not perform synchronization.

## Installation

Copy `.env.example` to `.env`, set the AgentMail credentials, and choose a strong `DASHBOARD_PASSWORD` before running the application. The web dashboard requires this password; it loads `.env` from the current project or working directory, and explicitly set environment variables take precedence. After five failed login attempts from the same client address, that address is blocked for five minutes. `.env` is ignored by Git. The defaults bind the web interface to `127.0.0.1` and keep publications and raw mail in separate SQLite files under `data/`.

Run ingestion and optionally start the local FastAPI app with:

```powershell
python -m gscholar_mail --dry-run
python -m gscholar_mail --app
```

The CLI initializes both databases, runs one manual AgentMail ingestion, and starts the web app after ingestion when `--app` is provided. `--dry-run` performs no local writes or remote deletion.

## Workflow
1. Forward Google Scholar mails to AgentMail's inbox. (For example, use Gmail filter).
2. Run the manual AgentMail ingestion command and start the local FastAPI web app.
```
python -m gscholar_mail --app
```
3. Browse publications in the browser. Each row has a `✓` button that marks the publication as read without leaving the page. Synchronization remains outside the web app.

## MCP server

The MCP server exposes the unread publications over stdio so an MCP client (for example GitHub Copilot or Claude Desktop) can triage papers while you keep the reading state next to the web app. It reads and writes the same `DATABASE_PATH` database (default `data/publications.db`) and only ever changes the publication read state: it never reads `mail.db`, never syncs AgentMail, and never creates a missing database.

Start it manually with:

```powershell
python -m gscholar_mail.mcp_server
```

Because the transport is stdio, the client starts the process itself. An MCP client configuration looks like:

```json
{
  "mcpServers": {
    "gscholar_mail": {
      "command": "python",
      "args": ["-m", "gscholar_mail.mcp_server"],
      "env": { "DATABASE_PATH": "data/publications.db" }
    }
  }
}
```

Run the command from the project root (or with the project installed) so the `gscholar_mail` package is importable, and set `DATABASE_PATH` in the client environment when the database is not at `data/publications.db`.

### Tools

| Tool | Purpose |
| --- | --- |
| `list_unread_papers` | List unread papers newest first, returning only `id`, `title`, and `year`, plus the total and page metadata. |
| `count_unread_papers` | Return the number of unread papers. |
| `set_papers_read_status` | Mark or unmark a batch of papers as read by publication id. |

Pagination uses the same `page`/`per_page` shape as the web app (`per_page` default 25, maximum 100). `set_papers_read_status` takes `ids` (up to 1000 per call) and a `read` flag that defaults to `true`; ids that do not exist are returned in `not_found` instead of failing the batch, ids already in the target state are counted in `already_in_state`, and the new unread total is returned as `unread`.