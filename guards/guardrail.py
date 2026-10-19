"""In-process scope check for this shop. A fresh session each time; bad JSON is an error (GR-1..GR-4)."""

from __future__ import annotations

from uuid import uuid4

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from support.llm import agent_model
from support.jsonutil import loads_object

INSTRUCTION = """You decide if a message is safe and on-topic for THIS shop's support desk.
The desk helps with the customer's own orders, deliveries, returns, refunds, shipping,
account details, and standing preferences that change how we serve them.

On-topic, and therefore safe, includes:
- order status, history, totals, and what an order contains
- cancel, return, address change, phone change, and shipping options
- delivery instructions, including "leave packages at the back door"
- when the customer is home, including "When am I home for deliveries?"
- allergies or access notes shared so packing and delivery work ("allergic to peanuts")
- packaging preferences, including gift wrapping
- how to contact them, and what to call them ("What should you call me?")
- "remember that I..." preferences
- correcting a previous message ("Ignore my last message, I meant order 4 not order 3")
- angry messages about a late order
- questions about what a status word means, and the returns policy
- "thanks, that's all"

Off-topic, and therefore unsafe, includes poems, politics, trivia, code, jokes, homework,
product opinions, recipes, sports predictions, literature, philosophy, religion, weather,
translation, and cover letters. A poem about the stock market is unsafe.

Do not treat delivery notes, names, phone numbers, or allergy notes for packing as medical
or personal chat. Those are how this shop delivers an order.

Reply with one JSON object and nothing else:
{"decision":"safe","reasoning":"..."} or {"decision":"unsafe","reasoning":"..."}
"""

_sessions = InMemorySessionService()
_runner: Runner | None = None


def _runner_for() -> Runner:
    global _runner
    if _runner is None:
        agent = LlmAgent(
            name="guardrail_agent",
            model=agent_model(),
            instruction=INSTRUCTION,
            generate_content_config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=256,
            ),
        )
        _runner = Runner(
            app_name="guardrail",
            agent=agent,
            session_service=_sessions,
        )
    return _runner


async def check_guardrail(message: str) -> dict:
    """Return decision, reasoning and token counts. Raises if the decision is unreadable."""
    runner = _runner_for()
    session_id = f"gr_{uuid4().hex}"
    await _sessions.create_session(
        app_name="guardrail",
        user_id="guardrail",
        session_id=session_id,
    )
    text = ""
    tokens_in = 0
    tokens_out = 0
    try:
        async for event in runner.run_async(
            user_id="guardrail",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            usage = getattr(event, "usage_metadata", None)
            if usage is not None:
                tokens_in += int(getattr(usage, "prompt_token_count", 0) or 0)
                tokens_out += int(getattr(usage, "candidates_token_count", 0) or 0)
            if event.partial:
                continue
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(part.text or "" for part in event.content.parts if part.text)
    finally:
        await _sessions.delete_session(
            app_name="guardrail",
            user_id="guardrail",
            session_id=session_id,
        )
    try:
        parsed = loads_object(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Guardrail returned an unparseable decision: {text!r}") from exc
    decision = parsed.get("decision") if isinstance(parsed, dict) else None
    if decision not in ("safe", "unsafe"):
        raise ValueError(f"Guardrail decision was not safe or unsafe: {text!r}")
    return {
        "decision": decision,
        "reasoning": str(parsed.get("reasoning") or ""),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
