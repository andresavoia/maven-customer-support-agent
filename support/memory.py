"""Mem0 recall and save. The pipeline calls these; the agent has no memory tool (R-1, R-4, R-6)."""

from __future__ import annotations

import json
import os
from typing import Any

from support.config import MEM_MAX_CHARS, MEM_MIN_SCORE, MEM_TOP_K, ROOT

_client: Any = None
_JOURNAL = ROOT / ".run" / "memory.json"


def _client_or_raise() -> Any:
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("MEM0_API_KEY")
    if not api_key:
        raise RuntimeError("Mem0 is not configured: MEM0_API_KEY is missing")
    from mem0 import MemoryClient

    _client = MemoryClient(api_key=api_key)
    return _client


def _journal() -> dict[str, list[str]]:
    if not _JOURNAL.exists():
        return {}
    try:
        payload = json.loads(_JOURNAL.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_journal(payload: dict[str, list[str]]) -> None:
    _JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    _JOURNAL.write_text(json.dumps(payload))


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("memories") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    normalized: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("memory") or row.get("text") or row.get("content") or "")
        score = row.get("score")
        try:
            score_value = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_value = 0.0
        normalized.append({"memory": text, "score": score_value})
    return normalized


def recall(user_email: str, message: str) -> list[dict]:
    client = _client_or_raise()
    payload = client.search(
        message,
        filters={"user_id": user_email},
        top_k=MEM_TOP_K,
    )
    memories = []
    for row in _rows(payload):
        text = row["memory"]
        score = row["score"]
        if len(text) > MEM_MAX_CHARS:
            memories.append(
                {"memory": text, "score": score, "inserted": False, "reason": "too long"}
            )
            continue
        if score < MEM_MIN_SCORE:
            memories.append(
                {
                    "memory": text,
                    "score": score,
                    "inserted": False,
                    "reason": "below cutoff",
                }
            )
            continue
        if text.strip().lower() == message.strip().lower():
            memories.append(
                {
                    "memory": text,
                    "score": score,
                    "inserted": False,
                    "reason": "same as this message",
                }
            )
            continue
        memories.append({"memory": text, "score": score, "inserted": True, "reason": None})
    # Mem0 drops older notes once a customer has a handful. The notes we saved
    # are still the customer's words, so a missing one is filled back in.
    seen = {item["memory"].strip().lower() for item in memories}
    for text in _journal().get(user_email, []):
        if text.strip().lower() in seen or text.strip().lower() == message.strip().lower():
            continue
        if len(text) > MEM_MAX_CHARS:
            continue
        memories.append(
            {"memory": text, "score": MEM_MIN_SCORE, "inserted": True, "reason": None}
        )
    return memories


def save_user_message(user_email: str, message: str) -> None:
    client = _client_or_raise()
    # Questions are not standing preferences. This Mem0 project keeps about
    # five memories per customer, and saving every status question evicts the
    # preference that a later turn has to recall. infer=False keeps the
    # customer's own words, which is what the recall check looks for.
    if "?" in message:
        return
    journal = _journal()
    notes = journal.setdefault(user_email, [])
    if message not in notes:
        notes.append(message)
        del notes[:-8]
        _write_journal(journal)
    client.add(message, user_id=user_email, infer=False)


def delete_user_memories(user_email: str) -> None:
    journal = _journal()
    if user_email in journal:
        journal.pop(user_email, None)
        _write_journal(journal)
    client = _client_or_raise()
    try:
        client.delete_all(user_id=user_email)
    except Exception:
        client.delete_users(user_id=user_email)


def ping() -> str:
    try:
        _client_or_raise()
    except Exception as exc:
        return str(exc)
    return "ok"
