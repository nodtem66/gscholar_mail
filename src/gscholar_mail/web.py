"""FastAPI interface for browsing and updating publication read state.

Pages are server-rendered with explicit HTML escaping. The application writes
only the publication read state, never reads the mail database, and never
triggers sync.
"""

import hashlib
import hmac
import os
import sqlite3
import time
from collections import OrderedDict
from contextlib import closing
from html import escape
from math import ceil
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import FastAPI, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from gscholar_mail.config import (
    load_bind_address,
    load_environment,
    load_publications_path,
)
from gscholar_mail.publications import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    READ_STATUS_READ,
    READ_STATUS_UNREAD,
    PublicationsDatabaseError,
    open_database,
    set_read_status,
)
from gscholar_mail.publications import (
    count_unread as count_unread_papers,
)

PAGE_SIZES = (10, 25, 50, 100)
LOGIN_PATH = "/login"
LOGIN_FAILURE_LIMIT = 5
LOGIN_COOLDOWN_SECONDS = 300
LOGIN_CLIENT_LIMIT = 1024
DATABASE_UNAVAILABLE = (
    "The publications database is unavailable. Run the ingestion command first "
    "and check DATABASE_PATH."
)

# Static scripts: no user data is interpolated, so they are safe to inline.
PAGE_SIZE_SCRIPT = """
document.getElementById("per_page").addEventListener("change", function (event) {
  event.target.form.submit();
});
"""

# Upgrades the per-row mark-as-read form to a fetch request so the row can be
# removed without a page reload. Without JavaScript the form still posts.
MARK_READ_SCRIPT = """
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form.classList || !form.classList.contains("js-mark-read")) {
    return;
  }
  event.preventDefault();
  var button = form.querySelector("button");
  var row = form.closest("li");
  var list = row ? row.parentElement : null;
  button.disabled = true;
  fetch(form.action, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams(new FormData(form)).toString(),
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    })
    .then(function (payload) {
      if (row) {
        row.remove();
      }
      var total = document.getElementById("publication-total");
      if (total && typeof payload.unread === "number") {
        total.textContent = payload.unread;
      }
      if (list && list.querySelectorAll("li").length === 0) {
        window.location.reload();
      }
    })
    .catch(function () {
      button.disabled = false;
      if (!row || row.querySelector(".js-mark-read-error")) {
        return;
      }
      var message = document.createElement("p");
      message.className = "js-mark-read-error shrink-0 pr-1 text-xs text-red-700";
      message.textContent = "Could not mark as read.";
      row.appendChild(message);
    });
});
"""


def _safe_external_url(value: str) -> str | None:
    """Return the URL only when it is an absolute http(s) link.

    Link values originate from email content and are untrusted.
    """
    parsed = urlparse(value.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value.strip()
    return None


def _layout(
    title: str, body: str, status_code: int = 200, *, show_logout: bool = False
) -> HTMLResponse:
    header_action = (
        '<form method="post" action="/logout"><button type="submit" '
        'class="text-xs text-slate-300 hover:text-white">Sign out</button></form>'
        if show_logout
        else '<span class="text-xs text-slate-300">Unread publications</span>'
    )
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Publications</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-slate-100 text-slate-900">
<header class="bg-slate-900 text-white shadow">
<div class="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
<a href="/publications" class="text-lg font-semibold">Publications</a>
{header_action}
</div>
</header>
<main class="mx-auto max-w-5xl px-4 py-6">
{body}
</main>
<script>{PAGE_SIZE_SCRIPT}</script>
<script>{MARK_READ_SCRIPT}</script>
</body>
</html>
"""
    return HTMLResponse(document, status_code=status_code)


def _list_url(page: int, per_page: int) -> str:
    return "/publications?" + urlencode({"page": page, "per_page": per_page})


def _nav_link(label: str, href: str | None) -> str:
    if href is None:
        return (
            f'<span class="rounded border border-slate-200 px-3 py-1 text-slate-400">'
            f"{escape(label)}</span>"
        )
    return (
        f'<a href="{escape(href)}" class="rounded border border-slate-300 bg-white '
        f'px-3 py-1 hover:bg-slate-50">{escape(label)}</a>'
    )


def _render_list(
    rows: list[sqlite3.Row], total: int, page: int, per_page: int, csrf_token: str
) -> str:
    total_pages = max(1, ceil(total / per_page))
    options = "".join(
        f'<option value="{size}"{" selected" if size == per_page else ""}>{size}</option>'
        for size in PAGE_SIZES
    )
    items = "".join(
        f"""<li class="flex items-center gap-2">
<a href="/publications/{int(row["id"])}" class="min-w-0 flex-1 px-1 py-3 hover:bg-slate-50">
<p class="font-medium">{escape(row["title"])}</p>
<p class="text-sm text-slate-600">{escape(row["authors"])}</p>
<p class="text-xs text-slate-500">Year {escape(str(row["year"] or "—"))} · Status {escape(str(row["status"]))} · Updated {escape(row["updated_at"][:10])}</p>
</a>
<form method="post" action="/publications/{int(row["id"])}" class="js-mark-read shrink-0 pr-1">
<input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
<button type="submit" title="Mark as read" aria-label="Mark as read" class="bg-blue-500 hover:bg-blue-700 text-white font-bold px-2 py-2 rounded-full"><svg xmlns="http://w3.org" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="20 6 9 17 4 12"></polyline>
</svg></button>
</form>
</li>"""
        for row in rows
    )
    if not items:
        items = '<li class="py-6 text-slate-600">No publications on this page.</li>'

    previous_href = _list_url(page - 1, per_page) if page > 1 else None
    next_href = _list_url(page + 1, per_page) if page < total_pages else None

    return f"""
<h1 class="text-2xl font-semibold">Publications</h1>
<p class="mt-1 text-sm text-slate-600"><span id="publication-total">{total}</span> total · Page {page} of {total_pages}</p>
<form method="get" action="/publications" class="mt-4 flex items-center gap-2 text-sm">
<label for="per_page">Per page</label>
<select id="per_page" name="per_page" class="rounded border border-slate-300 bg-white px-2 py-1">{options}</select>
<input type="hidden" name="page" value="1">
<noscript><button type="submit" class="rounded border border-slate-300 bg-white px-3 py-1">Apply</button></noscript>
</form>
<ul class="mt-4 divide-y divide-slate-200 rounded-lg bg-white px-4 shadow-sm">
{items}
</ul>
<nav class="mt-4 flex items-center justify-between text-sm">
{_nav_link("← Previous", previous_href)}
{_nav_link("Next →", next_href)}
</nav>
"""


def _field(label: str, value_html: str) -> str:
    return (
        f'<div><dt class="text-xs font-semibold uppercase text-slate-500">{escape(label)}</dt>'
        f'<dd class="mt-1 text-sm">{value_html}</dd></div>'
    )


def _render_detail(row: sqlite3.Row, csrf_token: str) -> str:
    link = _safe_external_url(row["link"])
    if link is None:
        link_html = escape(row["link"] or "—")
    else:
        link_html = (
            f'<a href="{escape(link)}" target="_blank" rel="noopener noreferrer" '
            f'class="text-blue-700 underline break-all">{escape(link)}</a>'
        )

    fields = "".join(
        [
            _field("Authors", escape(row["authors"] or "—")),
            _field("Year", escape(str(row["year"] or "—"))),
            _field("Status", "Read" if row["status"] == READ_STATUS_READ else "Unread"),
            _field("Source email subject", escape(row["email_title"] or "—")),
            _field("Link", link_html),
            _field("Created", escape(row["created_at"])),
            _field("Updated", escape(row["updated_at"])),
        ]
    )
    read_action = (
        ""
        if row["status"] == READ_STATUS_READ
        else f"""
<form method="post" action="/publications/{int(row["id"])}" class="mt-4">
<input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
<button type="submit" class="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800">Mark as read</button>
</form>
"""
    )
    return f"""
<a href="/publications" class="text-sm text-blue-700 underline">← Back to unread publications</a>
<h1 class="mt-3 text-2xl font-semibold">{escape(row["title"])}</h1>
<dl class="mt-4 grid grid-cols-1 gap-4 rounded-lg bg-white p-4 shadow-sm sm:grid-cols-2">
{fields}
</dl>
{read_action}
<section class="mt-4 rounded-lg bg-white p-4 shadow-sm">
<h2 class="text-xs font-semibold uppercase text-slate-500">Summary</h2>
<p class="mt-2 whitespace-pre-wrap text-sm">{escape(row["data"] or "—")}</p>
</section>
"""


def _render_login(error: str = "") -> HTMLResponse:
    error_html = (
        f'<p class="mt-3 text-sm text-red-700" role="alert">{escape(error)}</p>'
        if error
        else ""
    )
    body = f"""
<h1 class="text-2xl font-semibold">Sign in</h1>
<p class="mt-1 text-sm text-slate-600">Enter the dashboard password to continue.</p>
{error_html}
<form method="post" action="{LOGIN_PATH}" class="mt-4 space-y-3">
<label class="block text-sm font-medium" for="password">Password</label>
<input id="password" name="password" type="password" required autocomplete="current-password" class="w-full rounded border border-slate-300 bg-white px-3 py-2">
<button type="submit" class="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800">Sign in</button>
</form>
"""
    return _layout("Sign in", body)


def create_app(publications_path: Path | None = None) -> FastAPI:
    load_environment()
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        raise ValueError("Missing required environment variable: DASHBOARD_PASSWORD")

    db_path = publications_path or load_publications_path()
    app = FastAPI(
        title="gscholar_mail", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.login_password_hash = hashlib.sha256(password.encode("utf-8")).digest()
    app.state.login_failures = OrderedDict()
    app.state.login_cooldown_seconds = LOGIN_COOLDOWN_SECONDS

    csrf_token = token_urlsafe(32)
    session_token = token_urlsafe(32)

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def is_authenticated(request: Request) -> bool:
        return hmac.compare_digest(
            request.cookies.get("dashboard_session", ""), session_token
        )

    def require_auth(request: Request) -> None:
        if not is_authenticated(request):
            raise StarletteHTTPException(401, "Please sign in to access the dashboard.")

    def login_remaining(request: Request, now: float | None = None) -> int:
        key = client_key(request)
        timestamp = time.monotonic() if now is None else now
        failures = app.state.login_failures
        entry = failures.get(key)
        if entry is None:
            return 0
        count, blocked_until = entry
        if blocked_until <= timestamp:
            if count == 0:
                failures.pop(key, None)
            return 0
        return int(blocked_until - timestamp + 0.999)

    def record_login_failure(request: Request) -> None:
        failures = app.state.login_failures
        key = client_key(request)
        now = time.monotonic()
        count, blocked_until = failures.get(key, (0, 0.0))
        if blocked_until > now:
            return
        count += 1
        if count >= LOGIN_FAILURE_LIMIT:
            blocked_until = now + app.state.login_cooldown_seconds
            count = 0
        failures[key] = (count, blocked_until)
        failures.move_to_end(key)
        while len(failures) > LOGIN_CLIENT_LIMIT:
            failures.popitem(last=False)

    def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        try:
            with closing(open_database(db_path)) as connection:
                return connection.execute(sql, params).fetchall()
        except (PublicationsDatabaseError, sqlite3.Error) as error:
            raise StarletteHTTPException(503, DATABASE_UNAVAILABLE) from error

    def mark_read(paper_id: int) -> tuple[bool, int]:
        """Mark one paper as read; the flag is False when it was already read."""
        try:
            with closing(open_database(db_path)) as connection:
                result = set_read_status(connection, [paper_id], read=True)
        except (PublicationsDatabaseError, sqlite3.Error) as error:
            raise StarletteHTTPException(503, DATABASE_UNAVAILABLE) from error
        return result.updated > 0, result.unread

    def unread_count() -> int:
        try:
            with closing(open_database(db_path)) as connection:
                return count_unread_papers(connection)
        except (PublicationsDatabaseError, sqlite3.Error) as error:
            raise StarletteHTTPException(503, DATABASE_UNAVAILABLE) from error

    def paper_exists(paper_id: int) -> bool:
        return bool(query("SELECT 1 FROM papers WHERE id = ?", (paper_id,)))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 401 and request.url.path != LOGIN_PATH:
            return RedirectResponse(LOGIN_PATH, status_code=303)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                {"detail": str(exc.detail)}, status_code=exc.status_code
            )
        body = (
            f'<h1 class="text-2xl font-semibold">Error {exc.status_code}</h1>'
            f'<p class="mt-2 text-slate-700">{escape(str(exc.detail))}</p>'
            f'<a href="/publications" class="mt-4 inline-block text-blue-700 underline">'
            f"Back to unread publications</a>"
        )
        return _layout("Error", body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> HTMLResponse:
        return _layout(
            "Invalid request",
            '<h1 class="text-2xl font-semibold">Invalid request</h1>'
            '<p class="mt-2 text-slate-700">Check the page and per-page values.</p>',
            status_code=422,
        )

    @app.get(LOGIN_PATH, response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request) -> Response:
        if is_authenticated(request):
            return RedirectResponse("/publications", status_code=303)
        return _render_login()

    @app.post(LOGIN_PATH, include_in_schema=False)
    async def login(request: Request) -> Response:
        remaining = login_remaining(request)
        if remaining:
            response = _render_login(
                f"Too many failed attempts. Try again in {remaining} seconds."
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(remaining)
            return response

        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        supplied_password = form.get("password", [""])[0].encode("utf-8")
        supplied_hash = hashlib.sha256(supplied_password).digest()
        if hmac.compare_digest(supplied_hash, app.state.login_password_hash):
            app.state.login_failures.pop(client_key(request), None)
            response = RedirectResponse("/publications", status_code=303)
            response.set_cookie(
                "dashboard_session",
                session_token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/",
            )
            return response

        record_login_failure(request)
        remaining = login_remaining(request)
        if remaining:
            response = _render_login(
                f"Too many failed attempts. Try again in {remaining} seconds."
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(remaining)
            return response
        return _render_login("Incorrect password.")

    @app.post("/logout", include_in_schema=False)
    def logout() -> RedirectResponse:
        response = RedirectResponse(LOGIN_PATH, status_code=303)
        response.delete_cookie("dashboard_session", path="/")
        return response

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/publications", status_code=307)

    @app.get("/publications", response_class=HTMLResponse, include_in_schema=False)
    def list_publications(
        request: Request,
        page: int = Query(1, ge=1, le=1_000_000),
        per_page: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> HTMLResponse:
        require_auth(request)
        total = unread_count()
        rows = query(
            """
            SELECT id, title, authors, year, status, updated_at
            FROM papers
            WHERE status = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (READ_STATUS_UNREAD, per_page, (page - 1) * per_page),
        )
        return _layout(
            "Unread publications",
            _render_list(rows, total, page, per_page, csrf_token),
            show_logout=True,
        )

    @app.get(
        "/publications/{paper_id}", response_class=HTMLResponse, include_in_schema=False
    )
    def publication_detail(
        request: Request, paper_id: int = PathParam(ge=1)
    ) -> HTMLResponse:
        require_auth(request)
        rows = query(
            """
            SELECT id, title, data, authors, link, email_title, year, status,
                   created_at, updated_at
            FROM papers WHERE id = ? AND status = ?
            """,
            (paper_id, READ_STATUS_UNREAD),
        )
        if not rows:
            raise StarletteHTTPException(404, "Unread publication not found.")
        return _layout(
            rows[0]["title"], _render_detail(rows[0], csrf_token), show_logout=True
        )

    @app.post("/publications/{paper_id}", include_in_schema=False)
    async def mark_publication_read(
        request: Request, paper_id: int = PathParam(ge=1)
    ) -> Response:
        require_auth(request)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if form.get("csrf_token", [None])[0] != csrf_token:
            raise StarletteHTTPException(403, "Invalid form token.")
        marked, unread = mark_read(paper_id)
        if "application/json" in request.headers.get("accept", ""):
            # The list page removes the row in place, so it only needs the new count.
            if not marked and not paper_exists(paper_id):
                raise StarletteHTTPException(404, "Publication not found.")
            return JSONResponse({"unread": unread})
        if not marked:
            raise StarletteHTTPException(404, "Unread publication not found.")
        return RedirectResponse("/publications", status_code=303)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host, port = load_bind_address()
    uvicorn.run(app, host=host, port=port)
