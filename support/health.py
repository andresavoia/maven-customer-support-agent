"""Dependency checks for /health and the CLI startup refusal (C-4)."""

from __future__ import annotations

import socket

import httpx

from support.config import JUDGE_URL, MASKER_URL, PHOENIX_URL, POSTGRES_PORT, TOOLBOX_URL


def _tcp(port: int) -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), 1.5):
            return "ok"
    except OSError as exc:
        return str(exc)


def _http(url: str) -> str:
    try:
        response = httpx.get(url, timeout=2.0)
    except httpx.HTTPError as exc:
        return str(exc)
    if response.status_code >= 500:
        return f"HTTP {response.status_code}"
    return "ok"


def snapshot() -> dict[str, str]:
    values = {
        "db": _tcp(POSTGRES_PORT),
        "toolbox": _http(TOOLBOX_URL),
        "judge": _http(f"{JUDGE_URL}/health"),
        "masker": _http(f"{MASKER_URL}/health"),
        "phoenix": _http(PHOENIX_URL),
        "mem0": "ok",
    }
    try:
        from support.memory import ping

        values["mem0"] = ping()
    except Exception as exc:
        values["mem0"] = str(exc)
    return values


def missing(required: tuple[str, ...] = ("db", "toolbox", "judge", "masker", "phoenix")) -> list[str]:
    current = snapshot()
    return [name for name in required if current.get(name) != "ok"]
