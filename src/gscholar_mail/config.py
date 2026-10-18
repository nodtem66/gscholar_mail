"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    agentmail_api_key: str
    agentmail_inbox: str
    database_path: Path
    mail_database_path: Path
    bind_host: str
    bind_port: int
    polling_interval_seconds: int
    export_enabled: bool


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name, str(default)).strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def load_environment(dotenv_path: Path | None = None) -> None:
    """Load simple KEY=VALUE entries from a dotenv file if it exists."""
    path = dotenv_path or Path(".env")
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and name not in os.environ:
            os.environ[name] = value


def load_publications_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "data/publications.db"))


def load_bind_address() -> tuple[str, int]:
    host = os.getenv("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return host, _positive_int("BIND_PORT", 8000)


def load_settings() -> Settings:
    """Load settings without reading secrets from either SQLite database."""
    load_environment()
    bind_host, bind_port = load_bind_address()
    return Settings(
        agentmail_api_key=_required("AGENTMAIL_API_KEY"),
        agentmail_inbox=_required("AGENTMAIL_INBOX"),
        database_path=load_publications_path(),
        mail_database_path=Path(os.getenv("MAIL_DATABASE_PATH", "data/mail.db")),
        bind_host=bind_host,
        bind_port=bind_port,
        polling_interval_seconds=_positive_int("POLLING_INTERVAL_SECONDS", 300),
        export_enabled=_boolean("EXPORT_ENABLED"),
    )
