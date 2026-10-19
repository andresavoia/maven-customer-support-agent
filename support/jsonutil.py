"""Decode the JSON strings the model and the Toolbox wrap around real values."""

from __future__ import annotations

import json
from typing import Any


def loads_object(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3].strip()
    return json.loads(cleaned)


def decode_args(args: dict[str, Any] | None) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str):
            stripped = value.strip()
            if stripped[:1] in "{[":
                try:
                    decoded[key] = json.loads(stripped)
                    continue
                except json.JSONDecodeError:
                    pass
        decoded[key] = value
    return decoded


def unwrap_tool_result(raw: Any) -> Any:
    """Unwrap Toolbox's {"result": "<json>"} envelope (SPEC §7.1)."""
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped == "null":
            return []
        if stripped[:1] in "{[":
            try:
                return unwrap_tool_result(json.loads(stripped))
            except json.JSONDecodeError:
                return raw
        return raw
    if isinstance(raw, dict) and set(raw.keys()) == {"result"} and isinstance(raw["result"], str):
        return unwrap_tool_result(raw["result"])
    if isinstance(raw, dict) and "result" in raw and len(raw) == 1:
        return unwrap_tool_result(raw["result"])
    return raw
