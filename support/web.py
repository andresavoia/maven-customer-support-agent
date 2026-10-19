"""Web front end. POST /api/chat streams the same events as the CLI (W-1, W-2)."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from support.config import MAX_MESSAGE_CHARS, MODEL, WEB_PORT
from support.health import snapshot
from support.pipeline import run_turn
from support.sessions import forget, get, remember
from support.telemetry import setup_telemetry
from support.tooling import verify_login

load_dotenv()
setup_telemetry()

app = FastAPI(title="Customer Support")
STATIC = Path(__file__).resolve().parent / "static" / "index.html"


class LoginBody(BaseModel):
    email: str
    password: str


class ChatBody(BaseModel):
    user_id: str
    message: str


class LogoutBody(BaseModel):
    user_id: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC)


@app.get("/health")
def health() -> JSONResponse:
    values = snapshot()
    body = {"status": "ok", "model": MODEL, **values}
    if any(value != "ok" for value in values.values()):
        body["status"] = "degraded"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)


@app.post("/api/login")
async def login(body: LoginBody) -> JSONResponse:
    profile = await verify_login(body.email.strip(), body.password)
    if not profile:
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)
    remember(profile)
    return JSONResponse(
        {
            "user_id": profile["email"],
            "full_name": profile.get("full_name") or profile["email"],
            "is_premium": bool(profile.get("is_premium_customer")),
        }
    )


@app.post("/api/logout")
def logout(body: LogoutBody) -> dict:
    forget(body.user_id)
    return {"ok": True}


@app.post("/api/chat", response_model=None)
async def chat(body: ChatBody) -> JSONResponse | StreamingResponse:
    if not body.message or not body.message.strip():
        return JSONResponse({"error": "message is empty"}, status_code=400)
    if len(body.message) > MAX_MESSAGE_CHARS:
        return JSONResponse({"error": "message too long"}, status_code=413)
    if get(body.user_id) is None:
        return JSONResponse({"error": "Not logged in."}, status_code=401)

    async def stream():
        async for event in run_turn(body.user_id, body.message):
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main()
