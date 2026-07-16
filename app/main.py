"""FastAPI application: serves the agora frontend and streams the council.

The ``/api/council`` endpoint streams Server-Sent Events. The browser consumes
it with ``fetch`` + a stream reader (rather than ``EventSource``) so it can POST
the question and the two model rosters in the request body.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .council import run_council
from .openrouter import OpenRouterClient, OpenRouterError

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = OpenRouterClient(settings)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="AI Council", lifespan=lifespan)


class CouncilRequest(BaseModel):
    question: str = Field(..., min_length=1)
    assembly: list[str] = Field(default_factory=list)
    governing: list[str] = Field(default_factory=list)


@app.get("/api/models")
async def get_models():
    """Return the free models available to build a council from."""
    try:
        models = await app.state.client.list_free_models()
    except OpenRouterError as exc:
        # Most commonly: no API key configured. Surface a friendly message.
        return JSONResponse(status_code=400, content={"error": str(exc), "models": []})
    return {"models": [{"id": m.id, "name": m.name} for m in models]}


@app.post("/api/council")
async def council(req: CouncilRequest):
    if len(req.assembly) < 2:
        raise HTTPException(422, "Pick at least two Assembly (debater) models.")
    if len(req.governing) < 1:
        raise HTTPException(422, "Pick at least one Governing Body (judge) model.")

    async def event_stream():
        try:
            async for event in run_council(
                req.question, req.assembly, req.governing, app.state.client.chat
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except OpenRouterError as exc:
            yield f"data: {json.dumps({'type': 'aborted', 'message': str(exc)})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# Serve CSS/JS/assets. Mounted last so it doesn't shadow the API routes.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
