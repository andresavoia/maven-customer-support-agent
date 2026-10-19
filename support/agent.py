"""The support agent. Order facts come from tools; memories are preferences only (R-3, R-6)."""

from __future__ import annotations

from uuid import uuid4

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from support.llm import agent_model
from support.tooling import load_agent_tools

APP_NAME = "customer_support"
MEMORY_HEADER = "Relevant memories about this customer (from Mem0):"

INSTRUCTION = """You are the support agent for one online shop. You help the signed-in
customer with their own orders, deliveries, returns, and account preferences.

Rules:
- An order fact (status, items, price, address, date) must come from a tool call in this
  turn. If the tool returns nothing, say you cannot find that order. Do not guess, and
  do not describe anyone else's order.
- Memories are preferences, not order facts. When they disagree with a tool, trust the tool.
- An order number means get-order-status. A product name means find-customer-orders.
  Copy the status word exactly: SHIPPED, PROCESSING, DELIVERED, CANCELLED, RETURNED.
  When listing several orders, name every product. Include price digits when you mention a price.
- Cancel, return, or address change: look the order up first. When that lookup returns
  the order, call action-log in the same turn. Do not ask why. parameters is a JSON
  string with that order_id. action_type is CANCEL_ORDER, RETURN_ORDER, or UPDATE_ADDRESS.
  A phone change is UPDATE_PROFILE and does not need an order.
- After action-log succeeds, say the request was recorded. Do not say the order changed.
"""

_sessions = InMemorySessionService()
_runners: dict[str, Runner] = {}


def compose_input(message: str, memories: list[dict]) -> str:
    inserted = [item["memory"] for item in memories if item.get("inserted")]
    if not inserted:
        return message
    lines = "\n".join(f"- {item}" for item in inserted)
    return f"{MEMORY_HEADER}\n{lines}\n\n{message}"


async def runner_for(email: str) -> Runner:
    if email not in _runners:
        tools = await load_agent_tools(email)
        agent = LlmAgent(
            name="support_agent",
            model=agent_model(),
            instruction=INSTRUCTION,
            tools=tools,
            generate_content_config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=512,
            ),
        )
        _runners[email] = Runner(
            app_name=APP_NAME,
            agent=agent,
            session_service=_sessions,
        )
    return _runners[email]


async def ensure_session(email: str) -> str:
    session_id = f"{email}-{uuid4().hex}"
    await _sessions.create_session(
        app_name=APP_NAME,
        user_id=email,
        session_id=session_id,
    )
    return session_id
