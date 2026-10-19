"""MCP Toolbox access. The agent toolset never includes a tool that updates orders (M-2, M-6)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from toolbox_core import ToolboxClient

from support.config import ROOT, TOOLBOX_URL
from support.jsonutil import unwrap_tool_result

_client: ToolboxClient | None = None


async def toolbox() -> ToolboxClient:
    global _client
    if _client is None:
        _client = ToolboxClient(TOOLBOX_URL)
    return _client


@lru_cache(maxsize=1)
def tool_catalog() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load((ROOT / "mcp_toolbox" / "tools.yaml").read_text())
    catalog: dict[str, dict[str, Any]] = {}
    for name, spec in (document.get("tools") or {}).items():
        params = [item["name"] for item in spec.get("parameters") or []]
        catalog[name] = {
            "kind": "MCP",
            "access": "WRITE" if name == "action-log" else "READ",
            "statement": str(spec.get("statement") or "").strip(),
            "params": params,
            "bound": [param for param in params if param in {"customer_email", "user_email"}],
        }
    return catalog


async def verify_login(email: str, password: str) -> dict[str, Any] | None:
    client = await toolbox()
    tool = await client.load_tool("verify-login")
    raw = await tool(email=email, password=password)
    rows = unwrap_tool_result(raw)
    if isinstance(rows, list):
        return rows[0] if rows else None
    if isinstance(rows, dict) and rows.get("email"):
        return rows
    return None


# Short docs. Gemini on OpenRouter returns MALFORMED_FUNCTION_CALL when the
# system prompt, the memory header and the Toolbox "Args:" docstrings are all
# long at once. The parameter schema still tells the model what to pass.
_TOOL_DOCS = {
    "get-order-status": "Look up one order that belongs to this customer.",
    "find-customer-orders": "List this customer's orders, newest first.",
    "action-log": "Record a request for a person to handle later. It does not change an order.",
}


async def load_agent_tools(email: str):
    """Bind the session email so the model cannot supply a different one (M-5)."""
    client = await toolbox()
    tools = await client.load_toolset(
        "support-agent",
        bound_params={"customer_email": email, "user_email": email},
    )
    for tool in tools:
        name = getattr(tool, "__name__", "")
        if name in _TOOL_DOCS:
            tool.__doc__ = _TOOL_DOCS[name]
    return tools
