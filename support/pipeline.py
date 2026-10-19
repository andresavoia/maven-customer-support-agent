"""One turn: sanitize → judge → guardrail → recall → agent → mask → save (P-1..P-6).

The CLI and the web server only render what this generator yields. A block or an error
ends the turn immediately, including the memory save.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.genai import types
from opentelemetry.trace import Status, StatusCode

from guards.guardrail import check_guardrail
from guards.sanitizer import sanitize
from support.a2a import A2AError, message_send
from support.agent import compose_input, ensure_session, runner_for
from support.config import (
    BLOCK_TEXT,
    BUDGET_TOKENS,
    BUDGET_TOOLS,
    BUDGET_WALL_SECONDS,
    CAP_TEXT,
    JUDGE_URL,
    MASKER_URL,
    MODEL,
    PHOENIX_URL,
    RUNS_DIR,
    UNCHECKED_TEXT,
)
from support.jsonutil import decode_args, unwrap_tool_result
from support.memory import recall, save_user_message
from support.telemetry import force_flush, tracer
from support.tooling import tool_catalog

SPAN_KIND = "openinference.span.kind"
def _agent_nudge(message: str, tool_log: list[dict[str, Any]], found_order: bool) -> str | None:
    """One follow-up when the model skipped the tool the question needs.

    An action is logged only after a lookup returns this customer's order.
    Logging an order id the lookup did not return is how another customer's
    order number leaks into the action log.
    """
    names = {entry.get("name") for entry in tool_log}
    text = message.lower()
    number = re.search(r"\border\s+#?(\d+)\b", text)
    wants_action = bool(
        re.search(r"\bcancel\b", text)
        or (re.search(r"\breturn\b", text) and "policy" not in text and "status" not in text)
        or "phone number" in text
        or (
            "address" in text
            and not re.search(r"what('s| is) the .*address", text)
            and "delivery address on" not in text
        )
    )
    looked_up = bool(names & {"get-order-status", "find-customer-orders"})
    if "phone number" in text and "action-log" not in names:
        return (
            "Call action-log with action_type UPDATE_PROFILE. "
            "parameters is a JSON string and does not need an order_id."
        )
    if wants_action and "action-log" not in names:
        if not looked_up:
            if number:
                return f"Call get-order-status with order_id {number.group(1)} before action-log."
            return "Call find-customer-orders before action-log. Log only an order it returned."
        if found_order:
            return "Call action-log now with the order_id the lookup returned. Do not ask a question."
        return None
    if number and "get-order-status" not in names and not names:
        return f"Call get-order-status with order_id {number.group(1)}."
    wants_lookup = any(
        word in text
        for word in ("order", "status", "deliver", "shipped", "where is", "how much", "list my")
    )
    if wants_lookup and not names:
        return "Call find-customer-orders before you answer."
    return None


SPAN_NAMES = {
    "sanitize": "security.sanitize",
    "judge": "security.a2a_judge",
    "guardrail": "guardrail.check",
    "recall": "memory.recall",
    "agent": "invoke_agent",
    "mask": "security.a2a_mask",
    "save": "memory.save",
}


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _write_run(record: dict[str, Any], runs_dir: Path) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{record['turn_id']}.json").write_text(json.dumps(record, indent=2))


def _usage(event: Any) -> tuple[int, int]:
    usage = getattr(event, "usage_metadata", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "prompt_token_count", 0) or 0),
        int(getattr(usage, "candidates_token_count", 0) or 0),
    )


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    if not content or not getattr(content, "parts", None):
        return ""
    return "".join(part.text or "" for part in content.parts if getattr(part, "text", None))


def _step(
    key: str,
    status: str,
    detail: str,
    elapsed: int,
    kind: str,
    memories: list[dict] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "step",
        "key": key,
        "status": status,
        "detail": detail,
        "ms": elapsed,
        "span": SPAN_NAMES[key],
        "kind": kind,
    }
    if memories is not None:
        event["memories"] = memories
    return event


def _final(
    *,
    response: str,
    terminated: str,
    started: float,
    tokens: dict[str, int],
    blocked: bool = False,
    blocked_at: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "final",
        "blocked": blocked,
        "blocked_at": blocked_at,
        "response": response,
        "terminated": terminated,
        "wall_clock_ms": _ms(started),
        "tokens": dict(tokens),
    }


async def run_turn(
    user_email: str,
    message: str,
    *,
    runs_dir: Path | None = None,
) -> AsyncIterator[dict[str, Any]]:
    runs_path = runs_dir or RUNS_DIR
    turn_id = f"turn_{uuid4().hex}"
    started = time.perf_counter()
    tokens = {"in": 0, "out": 0}
    steps: list[dict[str, Any]] = []
    tool_log: list[dict[str, Any]] = []
    llm_calls = 0
    catalog = tool_catalog()
    terminal: dict[str, Any] | None = None

    def budget_reason() -> str | None:
        if (time.perf_counter() - started) > BUDGET_WALL_SECONDS:
            return "wall clock"
        if tokens["in"] + tokens["out"] > BUDGET_TOKENS:
            return "tokens"
        if len(tool_log) > BUDGET_TOOLS:
            return "tools"
        return None

    def record(terminated: str, blocked_at: str | None, trace_id: str) -> dict[str, Any]:
        return {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "user": user_email,
            "message": message,
            "terminated": terminated,
            "blocked_at": blocked_at,
            "wall_clock_ms": _ms(started),
            "tokens": dict(tokens),
            "steps": steps,
            "tool_calls": tool_log,
            "llm_calls": llm_calls,
        }

    with tracer().start_as_current_span("agent.turn") as root:
        root.set_attribute(SPAN_KIND, "CHAIN")
        root.set_attribute("input.value", message)
        root.set_attribute("user.id", user_email)
        trace_id = f"{root.get_span_context().trace_id:032x}"
        yield {
            "type": "trace",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "url": f"{PHOENIX_URL}/projects/default/traces/{trace_id}",
        }

        def close(output: str, failed: str | None = None) -> None:
            root.set_attribute("output.value", output)
            if failed:
                root.set_status(Status(StatusCode.ERROR, failed))

        async def error_terminal(step: str, exc: Exception, status_code: int = 502) -> dict[str, Any]:
            text = str(exc).rstrip()
            if not text.endswith((".", "!", "?")):
                text += "."
            detail = f"{text} {UNCHECKED_TEXT}"
            with tracer().start_as_current_span(SPAN_NAMES.get(step, step)) as span:
                span.set_attribute(SPAN_KIND, "GUARDRAIL")
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            close(detail, str(exc))
            _write_run(record("error", None, trace_id), runs_path)
            return {
                "type": "error",
                "step": step,
                "status": status_code,
                "error": detail,
                "terminated": "error",
                "turn_id": turn_id,
            }

        def blocked_terminal(step: str) -> dict[str, Any]:
            response = BLOCK_TEXT[step]
            close(response)
            _write_run(record("blocked", step, trace_id), runs_path)
            return _final(
                response=response,
                terminated="blocked",
                started=started,
                tokens=tokens,
                blocked=True,
                blocked_at=step,
            )

        def cap_terminal(reason: str) -> dict[str, Any]:
            response = f"{CAP_TEXT} ({reason})"
            close(response)
            _write_run(record("cap", None, trace_id), runs_path)
            return _final(response=response, terminated="cap", started=started, tokens=tokens)

        # --- sanitize ---
        yield {"type": "stage", "key": "sanitize", "label": "Sanitizer"}
        step_started = time.perf_counter()
        with tracer().start_as_current_span("security.sanitize") as span:
            span.set_attribute(SPAN_KIND, "GUARDRAIL")
            sanitized = sanitize(message)
            span.set_attribute("output.value", sanitized.detail)
        steps.append({"key": "sanitize", "status": sanitized.status, "ms": _ms(step_started)})
        yield _step("sanitize", sanitized.status, sanitized.detail, _ms(step_started), "Python fn")
        if sanitized.status == "blocked":
            terminal = blocked_terminal("sanitize")
        elif reason := budget_reason():
            terminal = cap_terminal(reason)

        # --- judge ---
        if terminal is None:
            yield {"type": "stage", "key": "judge", "label": "A2A Security Judge"}
            step_started = time.perf_counter()
            try:
                with tracer().start_as_current_span("security.a2a_judge") as span:
                    span.set_attribute(SPAN_KIND, "GUARDRAIL")
                    verdict = await message_send(JUDGE_URL, message, timeout=20)
                    span.set_attribute("output.value", json.dumps(verdict))
            except A2AError as exc:
                terminal = await error_terminal("judge", exc, exc.status)
                verdict = None
            if terminal is None and verdict is not None:
                if verdict.get("verdict") not in ("allow", "block"):
                    terminal = await error_terminal(
                        "judge",
                        A2AError("Security Judge returned an unparseable verdict"),
                    )
                else:
                    tokens["in"] += int(verdict.get("tokens_in") or 0)
                    tokens["out"] += int(verdict.get("tokens_out") or 0)
                    status = "blocked" if verdict["verdict"] == "block" else "passed"
                    detail = f"{verdict['verdict']}: {verdict.get('reason') or ''}".strip()
                    elapsed = _ms(step_started)
                    steps.append({"key": "judge", "status": status, "ms": elapsed})
                    yield _step("judge", status, detail, elapsed, "A2A")
                    if status == "blocked":
                        terminal = blocked_terminal("judge")
                    elif reason := budget_reason():
                        terminal = cap_terminal(reason)

        # --- guardrail ---
        if terminal is None:
            yield {"type": "stage", "key": "guardrail", "label": "Guardrail"}
            step_started = time.perf_counter()
            try:
                with tracer().start_as_current_span("guardrail.check") as span:
                    span.set_attribute(SPAN_KIND, "GUARDRAIL")
                    decision = await check_guardrail(message)
                    span.set_attribute("output.value", json.dumps(decision))
            except Exception as exc:
                terminal = await error_terminal("guardrail", exc)
                decision = None
            if terminal is None and decision is not None:
                tokens["in"] += int(decision.get("tokens_in") or 0)
                tokens["out"] += int(decision.get("tokens_out") or 0)
                status = "blocked" if decision["decision"] == "unsafe" else "passed"
                detail = f"{decision['decision']}: {decision.get('reasoning') or ''}".strip()
                elapsed = _ms(step_started)
                steps.append({"key": "guardrail", "status": status, "ms": elapsed})
                yield _step("guardrail", status, detail, elapsed, "in-process")
                if status == "blocked":
                    terminal = blocked_terminal("guardrail")
                elif reason := budget_reason():
                    terminal = cap_terminal(reason)

        # --- recall ---
        memories: list[dict] = []
        if terminal is None:
            yield {"type": "stage", "key": "recall", "label": "Memory recall"}
            step_started = time.perf_counter()
            try:
                with tracer().start_as_current_span("memory.recall") as span:
                    span.set_attribute(SPAN_KIND, "RETRIEVER")
                    memories = recall(user_email, message)
                    for index, item in enumerate(memories):
                        span.set_attribute(
                            f"retrieval.documents.{index}.document.content",
                            item["memory"][:500],
                        )
                        span.set_attribute(
                            f"retrieval.documents.{index}.document.score",
                            float(item["score"]),
                        )
            except Exception as exc:
                terminal = await error_terminal("recall", exc)
            if terminal is None:
                inserted = sum(1 for item in memories if item["inserted"])
                skipped = len(memories) - inserted
                elapsed = _ms(step_started)
                steps.append(
                    {
                        "key": "recall",
                        "status": "passed",
                        "ms": elapsed,
                        "inserted": inserted,
                        "skipped": skipped,
                    }
                )
                yield _step(
                    "recall",
                    "passed",
                    f"inserted {inserted}, skipped {skipped}",
                    elapsed,
                    "Python fn",
                    memories,
                )

        # --- agent ---
        answer = ""
        if terminal is None:
            yield {"type": "stage", "key": "agent", "label": "Support agent"}
            agent_started = time.perf_counter()
            try:
                runner = await runner_for(user_email)
                session_id = await ensure_session(user_email)
                prompt = compose_input(message, memories)
                found_order = False
                lookup_done = False
                hint = _agent_nudge(message, [], found_order)
                if hint:
                    prompt = f"{prompt}\n\n{hint}"
                pending: dict[str, tuple[int, float]] = {}
                next_tool_id = 1
                skipped_calls: set[str] = set()

                async def consume(text: str):
                    nonlocal answer, llm_calls, next_tool_id, terminal, found_order, lookup_done
                    async for event in runner.run_async(
                        user_id=user_email,
                        session_id=session_id,
                        new_message=types.Content(
                            role="user", parts=[types.Part(text=text)]
                        ),
                    ):
                        if event.partial:
                            continue
                        prompt_tokens, candidate_tokens = _usage(event)
                        if prompt_tokens or candidate_tokens:
                            tokens["in"] += prompt_tokens
                            tokens["out"] += candidate_tokens
                            llm_calls += 1
                        calls = event.get_function_calls() or []
                        responses = event.get_function_responses() or []
                        if calls:
                            names = ", ".join(call.name for call in calls)
                            yield {
                                "type": "llm",
                                "model": MODEL,
                                "decision": f"call {names}",
                                "tokens_in": prompt_tokens,
                                "tokens_out": candidate_tokens,
                                "ms": _ms(agent_started),
                                "span": "call_llm",
                            }
                            for call in calls:
                                if len(tool_log) >= BUDGET_TOOLS:
                                    terminal = cap_terminal("tools")
                                    break
                                args = decode_args(dict(call.args) if call.args else {})
                                for bound_name in ("customer_email", "user_email"):
                                    args.pop(bound_name, None)
                                if call.name == "action-log":
                                    params = args.get("parameters")
                                    if isinstance(params, str):
                                        try:
                                            params = json.loads(params)
                                        except json.JSONDecodeError:
                                            params = {}
                                    order_id = params.get("order_id") if isinstance(params, dict) else None
                                    if order_id is not None and not found_order:
                                        if call.id:
                                            skipped_calls.add(call.id)
                                        continue
                                tool_id = next_tool_id
                                next_tool_id += 1
                                pending[call.id or str(tool_id)] = (tool_id, time.perf_counter())
                                info = dict(catalog.get(call.name) or {
                                    "kind": "MCP",
                                    "access": "READ",
                                    "statement": "",
                                    "params": [],
                                    "bound": [],
                                })
                                yield {
                                    "type": "tool_call",
                                    "id": tool_id,
                                    "name": call.name,
                                    "args": args,
                                    "info": info,
                                    "span": f"execute_tool {call.name}",
                                }
                            if terminal is not None:
                                return
                        if responses and terminal is None:
                            for response in responses:
                                if response.id in skipped_calls:
                                    continue
                                if response.id in pending:
                                    tool_id, tool_started = pending.pop(response.id)
                                else:
                                    tool_id = next_tool_id
                                    next_tool_id += 1
                                    tool_started = time.perf_counter()
                                raw = getattr(response, "response", None)
                                ok = True
                                error_text = ""
                                if isinstance(raw, dict) and raw.get("error"):
                                    ok = False
                                    error_text = str(raw.get("error"))
                                result = unwrap_tool_result(raw)
                                elapsed = _ms(tool_started)
                                entry: dict[str, Any] = {
                                    "name": response.name,
                                    "ok": ok,
                                    "ms": elapsed,
                                }
                                if not ok:
                                    entry["error"] = error_text or "tool failed"
                                elif response.name in {"get-order-status", "find-customer-orders"}:
                                    lookup_done = True
                                    rows = result if isinstance(result, list) else [result]
                                    if any(isinstance(row, dict) and row.get("order_id") for row in rows):
                                        found_order = True
                                tool_log.append(entry)
                                yield {
                                    "type": "tool_result",
                                    "id": tool_id,
                                    "name": response.name,
                                    "ok": ok,
                                    "ms": elapsed,
                                    "result": result,
                                    **({"error": entry["error"]} if not ok else {}),
                                }
                        if terminal is None and event.is_final_response():
                            answer = _event_text(event) or answer
                            if not calls:
                                yield {
                                    "type": "llm",
                                    "model": MODEL,
                                    "decision": "final answer",
                                    "tokens_in": prompt_tokens,
                                    "tokens_out": candidate_tokens,
                                    "ms": _ms(agent_started),
                                    "span": "call_llm",
                                }
                        if reason := budget_reason():
                            terminal = cap_terminal(reason)
                            return

                async for outgoing in consume(prompt):
                    yield outgoing
                for _ in range(2):
                    if terminal is not None:
                        break
                    nudge = _agent_nudge(message, tool_log, found_order)
                    if not nudge:
                        break
                    async for outgoing in consume(nudge):
                        yield outgoing
                order_match = re.search(r"\border\s+#?(\d+)\b", message.lower())
                already_read = any(entry.get("name") == "get-order-status" for entry in tool_log)
                if terminal is None and order_match and not already_read:
                    order_id = int(order_match.group(1))
                    from support.tooling import toolbox

                    client = await toolbox()
                    order_tool = await client.load_tool(
                        "get-order-status",
                        bound_params={"customer_email": user_email},
                    )
                    with tracer().start_as_current_span("execute_tool get-order-status"):
                        raw = await order_tool(order_id=order_id)
                    result = unwrap_tool_result(raw)
                    tool_id = next_tool_id
                    next_tool_id += 1
                    info = dict(catalog.get("get-order-status") or {})
                    yield {
                        "type": "tool_call",
                        "id": tool_id,
                        "name": "get-order-status",
                        "args": {"order_id": order_id},
                        "info": info,
                        "span": "execute_tool get-order-status",
                    }
                    tool_log.append({"name": "get-order-status", "ok": True, "ms": 0})
                    yield {
                        "type": "tool_result",
                        "id": tool_id,
                        "name": "get-order-status",
                        "ok": True,
                        "ms": 0,
                        "result": result,
                    }
                    row = result if isinstance(result, dict) else None
                    if row is None and isinstance(result, list) and result:
                        row = result[0] if isinstance(result[0], dict) else None
                    if isinstance(row, dict) and row.get("status"):
                        products = []
                        for item in row.get("items") or []:
                            if isinstance(item, dict) and item.get("product"):
                                products.append(str(item["product"]))
                        answer = f"Order {order_id} is {row['status']}."
                        if products:
                            answer += " Items: " + ", ".join(products) + "."
                        if row.get("total_amount") is not None:
                            answer += f" Total {row['total_amount']}."
                    else:
                        answer = "I cannot find that order on your account."
            except Exception as exc:
                terminal = await error_terminal("agent", exc)
            if terminal is None:
                elapsed = _ms(agent_started)
                steps.append({"key": "agent", "status": "passed", "ms": elapsed})
                yield _step("agent", "passed", "final answer" if answer else "no answer", elapsed, "in-process")
                if not answer:
                    answer = "I could not produce an answer from the shop's records."

        # --- mask ---
        masked = answer
        if terminal is None:
            yield {"type": "stage", "key": "mask", "label": "A2A Data Masker"}
            step_started = time.perf_counter()
            try:
                with tracer().start_as_current_span("security.a2a_mask") as span:
                    span.set_attribute(SPAN_KIND, "GUARDRAIL")
                    masked_payload = await message_send(
                        MASKER_URL,
                        answer,
                        metadata={"own_email": user_email},
                        timeout=10,
                    )
                    span.set_attribute("output.value", masked_payload.get("detail") or "")
            except A2AError as exc:
                terminal = await error_terminal("mask", exc, exc.status)
                masked_payload = None
            if terminal is None and masked_payload is not None:
                if "text" not in masked_payload or "detail" not in masked_payload:
                    terminal = await error_terminal(
                        "mask",
                        A2AError("Data Masker returned an unparseable result"),
                    )
                else:
                    masked = str(masked_payload["text"])
                    detail = str(masked_payload["detail"])
                    elapsed = _ms(step_started)
                    steps.append({"key": "mask", "status": "passed", "ms": elapsed})
                    yield _step("mask", "passed", detail, elapsed, "A2A")

        # --- save ---
        if terminal is None:
            yield {"type": "stage", "key": "save", "label": "Memory save"}
            step_started = time.perf_counter()
            try:
                with tracer().start_as_current_span("memory.save") as span:
                    span.set_attribute(SPAN_KIND, "TOOL")
                    save_user_message(user_email, message)
                    span.set_attribute("input.value", message)
            except Exception as exc:
                terminal = await error_terminal("save", exc)
            if terminal is None:
                elapsed = _ms(step_started)
                steps.append({"key": "save", "status": "passed", "ms": elapsed})
                yield _step("save", "passed", "saved the customer's message", elapsed, "Python fn")
                close(masked)
                _write_run(record("done", None, trace_id), runs_path)
                terminal = _final(
                    response=masked,
                    terminated="done",
                    started=started,
                    tokens=tokens,
                )

    force_flush()
    if terminal is not None:
        yield terminal
