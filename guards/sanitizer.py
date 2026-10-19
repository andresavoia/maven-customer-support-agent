"""Local, model-free check that runs before the Judge (S-1, S-2)."""

from __future__ import annotations

from dataclasses import dataclass

from support.config import MAX_MESSAGE_CHARS

from .patterns import SANITIZER_PATTERNS, first_hit

ALLOWED_EXTRA = set(".,!?'\"()-:;@#$%&/+*=_ \t\n\r")


@dataclass(frozen=True)
class SanitizeResult:
    status: str
    detail: str
    too_long: bool = False


def _char_allowed(char: str) -> bool:
    return char.isalnum() or char in ALLOWED_EXTRA


def sanitize(message: str) -> SanitizeResult:
    text = message or ""
    if len(text) > MAX_MESSAGE_CHARS:
        return SanitizeResult(
            status="blocked",
            detail=f"block: message is longer than {MAX_MESSAGE_CHARS} characters",
            too_long=True,
        )
    for char in text:
        if not _char_allowed(char):
            return SanitizeResult(
                status="blocked",
                detail=f"block: character {char!r} is not allowed",
            )
    hit = first_hit(text, SANITIZER_PATTERNS)
    if hit:
        return SanitizeResult(status="blocked", detail=f"block: {hit}")
    return SanitizeResult(status="passed", detail="allow: length, characters and cheap patterns")
