"""Deterministic attack patterns for the Sanitizer and the Security Judge.

Legitimate shop messages use words like drop, select, delete, update and ignore.
Patterns match attack shapes, not those words alone (S-2, T-LEGIT-FALSE-BLOCK).
"""

from __future__ import annotations

import re

SANITIZER_PATTERNS: list[tuple[str, str]] = [
    (r"javascript\s*:", "javascript URL"),
    (r"\$\(", "command substitution"),
    (r"\.\./", "path traversal"),
    (r"\bcat\s+/etc/", "shell file read"),
    (r"/etc/passwd", "passwd read"),
    (r"/etc/shadow", "shadow read"),
]

JUDGE_PATTERNS: list[tuple[str, str]] = [
    (r"\bdrop\s+table\b", "SQL DROP TABLE"),
    (r"\bunion\s+select\b", "SQL UNION SELECT"),
    (r"\bor\s+1\s*=\s*1\b", "SQL tautology"),
    (r"\bor\s+'", "SQL tautology"),
    (r"\bdelete\s+from\b", "SQL DELETE"),
    (r"\bselect\s+\*\s+from\b", "SQL SELECT star"),
    (r"\bfrom\s+users\b", "SQL read users"),
    (r"\bupdate\s+[a-z_][a-z0-9_]*\s+set\b", "SQL UPDATE"),
    (r"admin'\s*--", "SQL comment auth bypass"),
    (r"'\s*;", "SQL statement break"),
    (r"--", "SQL line comment"),
    (r"<\s*script\b", "script tag"),
    (r"<\s*iframe\b", "iframe tag"),
    (r"<\s*img\b", "image tag"),
    (r"onerror\s*=", "inline event handler"),
    (r"javascript\s*:", "javascript URL"),
    (r"\{\{", "template injection"),
    (r"\$\(", "command substitution"),
    (r"\.\./", "path traversal"),
    (r"\bcat\s+/etc/", "shell file read"),
    (r"/etc/passwd", "passwd read"),
    (r"/etc/shadow", "shadow read"),
    (r"ignore all previous", "prompt override"),
    (r"ignore the above", "prompt override"),
    (r"forget your rules", "prompt override"),
    (r"you are now dan", "jailbreak"),
    (r"without rules", "jailbreak"),
    (r"new instructions", "prompt override"),
    (r"system prompt", "prompt extraction"),
    (r"print your instructions", "prompt extraction"),
    (r"repeat everything above", "prompt extraction"),
    (r"full users table", "data exfiltration"),
    (r"database administrator", "privilege escalation"),
    (r"all orders in the database", "data exfiltration"),
    (r"dump customer_orders", "data exfiltration"),
    (r"update-order-status", "forbidden tool"),
    (r"passwords of all users", "credential theft"),
    (r"export all customer", "data exfiltration"),
    (r"every customer", "cross-customer query"),
    (r"set my account to premium", "privilege escalation"),
    (r"100%\s*discount", "unauthorized discount"),
]


def first_hit(text: str, patterns: list[tuple[str, str]]) -> str | None:
    haystack = text or ""
    for pattern, reason in patterns:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            return reason
    return None
