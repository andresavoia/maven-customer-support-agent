"""Call an A2A service with JSON-RPC message/send. Failures propagate; they are not allows."""

from __future__ import annotations

from uuid import uuid4

import httpx

from support.jsonutil import loads_object


class A2AError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


async def message_send(
    base_url: str,
    text: str,
    *,
    metadata: dict | None = None,
    timeout: float = 20,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": f"msg_{uuid4().hex}",
                "parts": [{"kind": "text", "text": text}],
                "metadata": metadata or {},
            }
        },
    }
    url = base_url.rstrip("/") + "/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise A2AError(f"{base_url} timed out", status=502) from exc
    except httpx.HTTPError as exc:
        raise A2AError(f"{base_url} unreachable: {exc}", status=502) from exc
    if response.status_code >= 500:
        raise A2AError(f"{base_url} returned HTTP {response.status_code}", status=502)
    try:
        body = response.json()
    except ValueError as exc:
        raise A2AError(f"{base_url} returned a non-JSON body", status=502) from exc
    if body.get("error"):
        message = body["error"].get("message") if isinstance(body["error"], dict) else str(body["error"])
        raise A2AError(str(message), status=502)
    result = body.get("result") or {}
    parts = result.get("parts") or []
    raw = ""
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            raw = str(part["text"])
            break
    if not raw:
        raise A2AError(f"{base_url} returned an empty verdict", status=502)
    try:
        parsed = loads_object(raw)
    except (ValueError, TypeError) as exc:
        raise A2AError(f"{base_url} returned an unparseable verdict: {raw!r}", status=502) from exc
    if not isinstance(parsed, dict):
        raise A2AError(f"{base_url} verdict was not an object", status=502)
    return parsed
