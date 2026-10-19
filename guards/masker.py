"""Replace PII and leave every other character, including case, untouched (K-2)."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-])?(?:\(\d{3}\)[\s.-]|\d{3}[\s.-])?\d{3}[\s.-]\d{4}(?!\d)"
)
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

REPLACEMENTS = {
    "email": "[redacted-email]",
    "phone number": "[redacted-phone]",
    "card number": "[redacted-card]",
}


def mask_text(text: str, own_email: str | None = None) -> tuple[str, str]:
    """Return the masked text and a detail string that names what changed."""
    counts = {"email": 0, "phone number": 0, "card number": 0}
    own = (own_email or "").strip().lower()

    def replace_email(match: re.Match[str]) -> str:
        found = match.group(0)
        if own and found.lower() == own:
            return found
        counts["email"] += 1
        return REPLACEMENTS["email"]

    def replace_kind(kind: str):
        def _replace(_match: re.Match[str]) -> str:
            counts[kind] += 1
            return REPLACEMENTS[kind]

        return _replace

    masked = EMAIL_RE.sub(replace_email, text or "")
    masked = PHONE_RE.sub(replace_kind("phone number"), masked)
    masked = CARD_RE.sub(replace_kind("card number"), masked)

    changed = [f"{count} {kind}" for kind, count in counts.items() if count]
    if not changed:
        return masked, "nothing to mask"
    noun = " and ".join(changed)
    return masked, f"masked {noun}"
