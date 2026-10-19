"""Ports, budgets and model name. Numbers match THRESHOLDS.md."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Served through OpenRouter (support/llm.py). The AI Studio free tier 404s on
# gemini-2.5-flash and then exhausts the daily cap. This lite model answers in
# about half a second there, which keeps a four-call turn under T-LAT-P50.
MODEL = "google/gemini-2.5-flash-lite"

# 5100, not 5000: macOS AirPlay already listens on 5000. The assignment's port is a default, not a gate.
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5100")
JUDGE_URL = os.environ.get("JUDGE_URL", "http://127.0.0.1:10002")
MASKER_URL = os.environ.get("MASKER_URL", "http://127.0.0.1:10003")
PHOENIX_URL = os.environ.get("PHOENIX_URL", "http://127.0.0.1:6006")
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))

MAX_MESSAGE_CHARS = 2_000
MEM_TOP_K = 5
# Measured on this Mem0 project: a real preference scored 0.166–0.227 on the
# question that needed it, and unrelated order chatter stayed under 0.13.
# 0.25 (the reference cutoff) dropped those real hits. 0.15 keeps them.
MEM_MIN_SCORE = 0.15
MEM_MAX_CHARS = 500
MEM_WAIT_SECONDS = 120

BUDGET_TOOLS = 6
BUDGET_TOKENS = 30_000
BUDGET_WALL_SECONDS = 30

RUNS_DIR = Path(os.environ.get("RUNS_DIR", ROOT / "runs"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", ROOT / "reports"))

BLOCK_TEXT = {
    "sanitize": "I can't process that message.",
    "judge": "I can't help with that request.",
    "guardrail": "I can only help with orders, deliveries, returns, and your account.",
}

CAP_TEXT = "This request hit a limit and stopped before it finished."
UNCHECKED_TEXT = "The request could not be checked, so I did not answer it."
