"""In-memory login sessions for the web API. Passwords are the demo seed, checked by the auth tool."""

from __future__ import annotations

from typing import Any

_sessions: dict[str, dict[str, Any]] = {}


def remember(profile: dict[str, Any]) -> None:
    _sessions[str(profile["email"])] = profile


def get(user_id: str) -> dict[str, Any] | None:
    return _sessions.get(user_id)


def forget(user_id: str) -> None:
    _sessions.pop(user_id, None)
