"""Request validation for the web API: every field is checked for type, length and allowed values.

Framework-free, so the rules are tested on their own. A rejected request raises RequestError with the HTTP status
and a message that is safe to show the shopper.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# session ids are minted by the server (secrets.token_urlsafe); anything else is rejected, not trusted
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
JSON_TYPES = ("application/json",)


class RequestError(ValueError):
    """A request the API refuses; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ChatRequest:
    message: str
    session_id: str | None


def read_json_body(content_type: str | None, content_length: str | None, body: bytes, max_bytes: int) -> dict:
    """Check the declared type and size, then parse the body as a JSON object."""
    media_type = (content_type or "").split(";")[0].strip().lower()
    if media_type not in JSON_TYPES:
        raise RequestError(415, "Send the request as JSON (Content-Type: application/json)")
    try:
        length = int(content_length or "")
    except ValueError as exc:
        raise RequestError(411, "The request needs a Content-Length") from exc
    if length < 0:
        raise RequestError(400, "Invalid Content-Length")
    if length > max_bytes or len(body) > max_bytes:
        raise RequestError(413, f"The request is too large (at most {max_bytes} bytes)")
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError(400, "The request body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise RequestError(400, "The request body must be a JSON object")
    return data


def _only_keys(data: dict, allowed: set[str]) -> None:
    """Reject a request carrying fields the API doesn't know (400)."""
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise RequestError(400, f"Unknown field(s): {', '.join(unknown)}")


def session_id(data: dict) -> str | None:
    """The optional session id: absent / null, or a server-minted id."""
    value = data.get("session_id")
    if value is None:
        return None
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise RequestError(400, "Invalid session_id")
    return value


def chat_request(data: dict, max_chars: int) -> ChatRequest:
    """A chat message: a non-empty string of at most `max_chars` characters, with no control characters."""
    _only_keys(data, {"message", "session_id"})
    message = data.get("message")
    if not isinstance(message, str):
        raise RequestError(400, "message must be text")
    message = message.strip()
    if not message:
        raise RequestError(400, "Type a question first")
    if len(message) > max_chars:
        raise RequestError(413, f"Please keep the question under {max_chars} characters")
    if any(ord(c) < 32 and c not in "\t\n" for c in message):
        raise RequestError(400, "The question contains control characters")
    return ChatRequest(message, session_id(data))


def reset_request(data: dict) -> str | None:
    """A reset request: only an optional session id."""
    _only_keys(data, {"session_id"})
    return session_id(data)
