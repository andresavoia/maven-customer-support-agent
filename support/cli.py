"""Terminal front end. It renders pipeline events and does not decide them (P-1, C-1..C-3)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from dotenv import load_dotenv

from support.health import missing
from support.pipeline import run_turn
from support.telemetry import setup_telemetry
from support.tooling import verify_login


def format_event(event: dict) -> str | None:
    kind = event.get("type")
    if kind == "trace":
        return f"[trace] {event['url']}"
    if kind == "stage":
        return f"[{event['key']}] started"
    if kind == "step":
        line = f"[{event['key']}] {event['status']} · {event['ms']} ms · {event['span']}"
        if event.get("detail"):
            line += f" · {event['detail']}"
        memories = event.get("memories") or []
        extra = [
            f"  - {item['score']:.2f} {'kept' if item['inserted'] else 'skipped'}: {item['memory']}"
            for item in memories
        ]
        return "\n".join([line, *extra]) if extra else line
    if kind == "llm":
        return (
            f"[llm] {event['decision']} · {event['ms']} ms · "
            f"in {event['tokens_in']} out {event['tokens_out']}"
        )
    if kind == "tool_call":
        return f"[tool] {event['name']} {json.dumps(event['args'], default=str)}"
    if kind == "tool_result":
        return f"[tool result] {event['name']} {json.dumps(event['result'], default=str)}"
    if kind == "final":
        return event.get("response") or ""
    if kind == "error":
        return f"[error] {event['step']}: {event['error']}"
    return json.dumps(event)


async def _emit(email: str, message: str, events_only: bool) -> None:
    async for event in run_turn(email, message):
        if events_only:
            print(json.dumps(event), flush=True)
        else:
            line = format_event(event)
            if line is not None:
                print(line, flush=True)


async def _loop(email: str, events_only: bool) -> None:
    while True:
        try:
            message = input("You: ")
        except EOFError:
            return
        if message.strip().lower() in {"quit", "exit", "q"}:
            return
        if not message.strip():
            continue
        await _emit(email, message, events_only)
        if not events_only:
            print(flush=True)


async def _amain(args: argparse.Namespace) -> int:
    load_dotenv()
    setup_telemetry()
    down = missing()
    if down:
        print(f"Refusing to start. Unavailable: {', '.join(down)}.", file=sys.stderr)
        return 1
    if args.user:
        email = args.user
    else:
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ")
        profile = await verify_login(email, password)
        if not profile:
            print("Invalid email or password.", file=sys.stderr)
            return 1
        if not args.events:
            print(f"Signed in as {profile.get('full_name') or email}.")
    if not sys.stdin.isatty():
        message = sys.stdin.read().strip()
        if not message:
            print("message is empty", file=sys.stderr)
            return 1
        await _emit(email, message, args.events)
        return 0
    await _loop(email, args.events)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Shop support desk")
    parser.add_argument("--user", help="Signed-in customer email, for a scripted turn")
    parser.add_argument("--events", action="store_true", help="Print raw NDJSON events only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
