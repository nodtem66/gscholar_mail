"""Command-line entry point for ingestion and the local web app."""

import argparse
import logging
import sqlite3

import uvicorn

from gscholar_mail.agentmail_client import AgentMailClient
from gscholar_mail.config import load_settings
from gscholar_mail.database import initialize_databases
from gscholar_mail.ingestion import run_ingestion


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gscholar_mail", description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse messages without local writes or deletion",
    )
    parser.add_argument(
        "--app", action="store_true", help="Start the local web app after ingestion"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level), format="%(levelname)s %(message)s"
    )
    settings = load_settings()
    initialize_databases(settings.database_path, settings.mail_database_path)

    with (
        sqlite3.connect(settings.database_path) as publications,
        sqlite3.connect(settings.mail_database_path) as mail,
    ):
        client = AgentMailClient(settings.agentmail_api_key, settings.agentmail_inbox)
        try:
            summary = run_ingestion(client, publications, mail, dry_run=args.dry_run)
        finally:
            client.close()
    print(summary)

    if args.app:
        from gscholar_mail.web import create_app

        uvicorn.run(
            create_app(settings.database_path),
            host=settings.bind_host,
            port=settings.bind_port,
        )


if __name__ == "__main__":
    main()
