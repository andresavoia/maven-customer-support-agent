"""Offline check: every attack matches a guard pattern, and no real customer message does."""

from __future__ import annotations

import json
from pathlib import Path

from guards.patterns import JUDGE_PATTERNS, first_hit
from guards.sanitizer import sanitize

GOLD = Path(__file__).resolve().parent.parent / "eval" / "gold"


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (GOLD / name).read_text().splitlines() if line.strip()]


def caught(message: str) -> bool:
    if sanitize(message).status == "blocked":
        return True
    return first_hit(message, JUDGE_PATTERNS) is not None


def main() -> None:
    missed = [row["id"] for row in _load("attack.jsonl") if not caught(row["message"])]
    false = []
    for name in ("legit.jsonl", "offtopic.jsonl"):
        for row in _load(name):
            if caught(row["message"]):
                false.append(row["id"])
    x01 = next(row for row in _load("attack.jsonl") if row["id"] == "X01")
    if sanitize(x01["message"]).status == "blocked":
        raise SystemExit("X01 must reach the Judge, not stop at the Sanitizer")
    if missed or false:
        raise SystemExit(f"missed={missed} false={false}")
    print(f"patterns ok: {len(_load('attack.jsonl'))} attacks, no false blocks on legit or off-topic")


if __name__ == "__main__":
    main()
