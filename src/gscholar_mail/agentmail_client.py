"""Small HTTP client for the AgentMail inbox API."""

import base64
import quopri
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class AgentMailMessage:
    message_id: str
    subject: str
    sender: str
    received_at: str | None
    html: str


class AgentMailError(RuntimeError):
    """Redacted AgentMail API failure."""


class AgentMailClient:
    def __init__(
        self,
        api_key: str,
        inbox_id: str,
        *,
        base_url: str = "https://api.agentmail.to/v0",
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        self.inbox_id = inbox_id
        self.max_retries = max_retries
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = requests.Session()
        self._client.headers.update({"Authorization": f"Bearer {api_key}"})

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(
            method,
            f"{self._base_url}{path}",
            timeout=self._timeout_seconds,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def list_messages(self, limit: int = 100) -> Iterator[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": min(limit, 100), "labels": ["unread"]}
            if page_token:
                params["page_token"] = page_token
            payload = self._request(
                "GET", f"/inboxes/{self.inbox_id}/messages", params=params
            )
            if not isinstance(payload, dict):
                raise AgentMailError("AgentMail returned an invalid message list")
            if "error" in payload:
                raise AgentMailError("AgentMail returned an error message")

            messages = payload.get("messages", [])
            if not isinstance(messages, list) or not all(
                isinstance(message, dict) for message in messages
            ):
                raise AgentMailError("AgentMail returned invalid messages")
            yield from messages

            next_token = payload.get("next_page_token")
            if next_token is None or next_token == "":
                break
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise AgentMailError("AgentMail returned an invalid pagination token")
            seen_tokens.add(next_token)
            page_token = next_token

    @staticmethod
    def _decode_body(value: object, encoding: str | None = None) -> str:
        if not isinstance(value, str):
            return ""
        raw = value.encode("utf-8")
        if encoding and encoding.lower() == "base64":
            try:
                raw = base64.b64decode(value, validate=False)
            except ValueError:
                return ""
        elif encoding and encoding.lower() in {"quoted-printable", "quopri"}:
            raw = quopri.decodestring(raw)
        return raw.decode("utf-8", errors="replace")

    def get_message(self, message_id: str) -> AgentMailMessage:
        payload = self._request(
            "GET", f"/inboxes/{self.inbox_id}/messages/{message_id}"
        )
        body = payload.get("html") or payload.get("text") or ""
        if isinstance(body, dict):
            encoding = body.get("encoding")
            body = body.get("data", "")
        else:
            encoding = payload.get("content_transfer_encoding")
        return AgentMailMessage(
            message_id=payload["message_id"],
            subject=str(payload.get("subject", "")),
            sender=str(payload.get("from", "")),
            received_at=payload.get("timestamp"),
            html=self._decode_body(body, encoding),
        )

    def soft_delete_message(self, message_id: str) -> None:
        # Mark a message as read and move it to the trash
        labels = {"add_labels": "trash", "remove_labels": "unread"}
        self._request("PATCH", f"/inboxes/{self.inbox_id}/messages/{message_id}", json=labels)
