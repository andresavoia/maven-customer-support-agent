"""A2A Data Masker. The replacement is deterministic and never changes case (K-1, K-2)."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from guards.masker import mask_text

MASKER_PORT = int(os.environ.get("MASKER_PORT", "10003"))
app = FastAPI(title="Data Masker")


def _card() -> dict:
    return {
        "name": "Data Masker",
        "description": "Masks emails, phone numbers and card numbers in a support reply.",
        "url": f"http://127.0.0.1:{MASKER_PORT}/",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "mask",
                "name": "Mask a reply",
                "description": "Return masked text and what changed.",
            }
        ],
    }


def _message(body: dict) -> tuple[str, str]:
    params = body.get("params") or {}
    message = params.get("message") or {}
    metadata = message.get("metadata") or params.get("metadata") or {}
    parts = message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            chunks.append(str(part["text"]))
    text = "\n".join(chunks) if chunks else str(params.get("text") or "")
    own_email = str(metadata.get("own_email") or "")
    return text, own_email


@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
def agent_card() -> dict:
    return _card()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/")
async def rpc(request: Request) -> JSONResponse:
    body = await request.json()
    method = body.get("method")
    rpc_id = body.get("id")
    if method not in ("message/send", "tasks/send"):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )
    text, own_email = _message(body)
    masked, detail = mask_text(text, own_email)
    payload = {"text": masked, "detail": detail}
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": f"msg_{uuid4().hex}",
                "parts": [{"kind": "text", "text": json.dumps(payload)}],
            },
        }
    )


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=MASKER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
