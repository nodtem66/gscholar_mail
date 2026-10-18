# gscholar_mail

Local application for ingesting Google Scholar alerts forwarded to a dedicated AgentMail inbox. The workflow is intentionally split: run the manual AgentMail sync first, then start the local web app to browse the SQLite databases. The web app does not perform synchronization.

## Development setup

Requires Python 3.11–3.13. Install runtime and development dependencies with:

```powershell
python -m pip install -e ".[dev]"
```

Run the tests with `pytest` (or, in a pixi checkout, `pixi run -e dev python -m pytest`), which uses the `dev` extra where `pytest` is installed.

The legacy Gmail/Google Sheets service remains available only for migration reference and is not part of the default runtime. The migrated application must not request Gmail or Google OAuth access. Before cutover, revoke the old OAuth grant in Google Account → Security → Third-party connections, then securely delete local `token.json`, `credentials.json`, and any copied credentials from Drive or Colab after confirming they are no longer needed.

Copy `.env.example` to `.env` and replace the AgentMail placeholder before running the migrated application. `load_settings()` automatically loads `.env` from the current project or working directory; explicitly set environment variables take precedence. Configuration is loaded from environment variables; `.env` is ignored by Git. The defaults bind the future web interface to `127.0.0.1` and keep publications and raw mail in separate SQLite files under `data/`.

The legacy workbook import utility has been removed after migration. Keep a backup of the workbook and databases for rollback.

Run ingestion and optionally start the local FastAPI app with:

```powershell
python -m gscholar_mail
python -m gscholar_mail --dry-run
python -m gscholar_mail --app
```

The CLI initializes both databases, runs one manual AgentMail ingestion, and starts the web app after ingestion when `--app` is provided. `--dry-run` performs no local writes or remote deletion.

The intended workflow is:

1. Run the manual AgentMail ingestion command.
2. Start the local FastAPI web app against the SQLite databases, or use `--app` to do both.
3. Browse publications in the browser; synchronization remains outside the web app.

The web app never reads `mail.db` and does not require AgentMail credentials. It shows unread publications and lets the user mark them as read. To start only the web app, run:

```powershell
python -m gscholar_mail --app
```

Then open <http://127.0.0.1:8000/publications>. The list is paginated (`page`, `per_page`), and each publication has a detail page at `/publications/{id}`. Only `http(s)` links from email content are rendered as clickable anchors. Each row has a `✓` button that marks the publication as read without leaving the page; without JavaScript it falls back to a normal form post.

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
