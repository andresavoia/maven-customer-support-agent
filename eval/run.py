"""Entry point: python -m eval.run"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from eval.runner import DailyQuotaExceeded, run_eval
from support.telemetry import setup_telemetry


def main() -> None:
    load_dotenv()
    setup_telemetry()
    try:
        code = asyncio.run(run_eval())
    except DailyQuotaExceeded as exc:
        print(
            "stopped: daily Gemini quota is exhausted. previous report kept.",
            flush=True,
        )
        print(str(exc)[:400], flush=True)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
