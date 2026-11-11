"""Kestrel fleet reporting API (Phase 7).

    kestrel-app                       # or: python -m api.main   -> http://127.0.0.1:8000

Read-only HTTP layer over the governed FleetTools: every data route is one of the same
tools Claude Desktop uses, with the same validation, suppression, identifier checks and
audit log. Also: live refresh events, refresh history and manual refresh requests, an
"Ask the fleet" chat that runs Claude over the same tools, and the React app in web/dist.
"""

import asyncio
import inspect
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from anthropic.lib.credentials import default_credentials
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from automation import llm
from mcp_server import audit
from mcp_server.guardrails import IdentifierLeak, ToolInputError
from mcp_server.server import INSTRUCTIONS
from mcp_server.tools import FleetTools
from pipeline import config
from refresh import watcher

WEB_DIST = config.ROOT / "web" / "dist"
BLOCKED = ("I can't show that answer: it contained a device or person identifier, which this assistant "
           "never returns. Ask for an aggregate instead (by plant, class, model or OS release).")

# Public route name -> FleetTools method. Every data route is a governed tool.
TOOLS = {
    "allowed-values": "list_allowed_values", "metric-definition": "get_metric_definition",
    "kpi": "get_kpi", "compare": "compare_cohorts", "coverage": "get_coverage",
    "data-quality": "get_data_quality_status", "breaches": "list_threshold_breaches",
    "rollout": "get_rollout_status", "top-events": "get_top_events",
    "business-impact": "get_business_impact", "tickets": "get_ticket_metrics",
    "changes": "get_change_correlation", "remediation": "get_remediation_status",
    "timeline": "get_timeline", "freshness": "get_data_freshness",
}


# --------------------------------------------------------------------------- warehouse access

class _Locked:
    """FleetTools proxy that serialises calls: one DuckDB connection, many request threads."""

    def __init__(self, tools: FleetTools, lock: threading.RLock):
        self._tools, self._lock = tools, lock

    def __getattr__(self, name):
        attr = getattr(self._tools, name)
        if not callable(attr):
            return attr

        def call(*a, **k):
            with self._lock:
                return attr(*a, **k)
        return call


class ToolsProvider:
    """Opens FleetTools per caller and reopens them when a refresh swaps the warehouse file."""

    def __init__(self):
        self._lock = threading.RLock()
        self._version = None
        self._tools: dict[str, FleetTools] = {}

    @staticmethod
    def version() -> str:
        st = Path(config.DB_PATH).stat()
        return f"{st.st_ino}-{st.st_mtime_ns}"

    def get(self, caller: str = "app") -> _Locked:
        v = self.version()
        with self._lock:
            if v != self._version:
                self._tools, self._version = {}, v     # old connections close when unreferenced
            if caller not in self._tools:
                self._tools[caller] = FleetTools(config.DB_PATH, caller=caller)
            return _Locked(self._tools[caller], self._lock)


provider = ToolsProvider()


def ai_enabled() -> bool:
    """An API key, or the sign-in that `ant auth login` stores (the SDK reads it from the active profile)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        return default_credentials() is not None
    except anthropic.AnthropicError:                  # a broken profile on disk: report disabled, don't crash
        return False


def make_client():
    return anthropic.Anthropic()


def _clock() -> str | None:
    p = config.STATE_DIR / "clock.json"
    return json.loads(p.read_text())["now"] if p.exists() else None


# --------------------------------------------------------------------------- app

app = FastAPI(title="Kestrel fleet reporting API",
              description="Read-only, governed fleet metrics for a fictional client (synthetic data).",
              version="0.7.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.exception_handler(ToolInputError)
def _bad_input(_, exc: ToolInputError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(IdentifierLeak)
def _leak(_, exc: IdentifierLeak):
    return JSONResponse(status_code=500, content={"detail": "Response blocked by the identifier guardrail."})


def _coerce(method, params: dict) -> dict:
    """Query strings -> the tool's parameter types; unknown parameters are rejected."""
    sig = inspect.signature(method)
    allowed = {n: p for n, p in sig.parameters.items() if n != "self"}
    out = {}
    for k, v in params.items():
        if k not in allowed:
            raise ToolInputError(f"Unknown parameter {k!r}. Allowed: {', '.join(allowed) or 'none'}")
        ann = str(allowed[k].annotation)
        if "bool" in ann:
            if v.lower() not in ("true", "false"):
                raise ToolInputError(f"{k} must be true or false.")
            out[k] = v.lower() == "true"
        elif "int" in ann and k == "limit":
            if not v.isdigit():
                raise ToolInputError("limit must be an integer.")
            out[k] = int(v)
        else:
            out[k] = v
    return out


@app.get("/api/tools", summary="List the governed tools behind /api/tools/{name}")
def list_tools():
    return {name: (inspect.getdoc(getattr(FleetTools, m)) or "").split("\n")[0] or m for name, m in TOOLS.items()}


@app.get("/api/tools/{name}", summary="Call a governed tool (query string = tool arguments)")
def call_tool(name: str, request: Request):
    method = TOOLS.get(name)
    if method is None:
        raise HTTPException(404, f"Unknown tool {name!r}. See /api/tools.")
    t = provider.get("app")
    fn = getattr(t, method)
    return fn(**_coerce(getattr(FleetTools, method), dict(request.query_params)))


@app.get("/api/status", summary="Warehouse version, simulated clock, freshness and AI availability")
def status():
    t = provider.get("app")
    fresh = t.get_data_freshness()
    weeks = t.v.weeks
    last = watcher.read_log(1)
    return {"version": provider.version(), "as_of": _clock() or str(config.AS_OF), "replay": _clock() is not None,
            "weeks": weeks, "data_to": t.v.week_dates[weeks[-1]][1].isoformat() if weeks else None,
            "freshness": fresh["data_freshness"], "ai_enabled": ai_enabled(),
            "last_refresh": last[-1] if last else None}


@app.get("/api/refresh/history", summary="Recent refresh runs from the watcher log")
def refresh_history(limit: int = 20):
    return {"runs": list(reversed(watcher.read_log(max(1, min(limit, 200)))))}


@app.post("/api/refresh", status_code=202, summary="Ask the refresh watcher to rebuild now")
def request_refresh():
    watcher.REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    watcher.REQUEST_PATH.write_text(json.dumps({"requested_at": datetime.now(timezone.utc).isoformat()}))
    return {"requested": True, "note": "The refresh watcher picks this up within a few seconds if it is running."}


async def refresh_events(request: Request, poll: float = 2.0, max_events: int | None = None):
    """Server-sent events: 'hello' on connect, 'refreshed' whenever the warehouse file changes."""
    last, sent = None, 0
    while max_events is None or sent < max_events:
        if await request.is_disconnected():
            return
        try:
            v = provider.version()
        except FileNotFoundError:
            v = None
        if v != last:
            yield {"event": "hello" if last is None else "refreshed",
                   "data": json.dumps({"version": v, "as_of": _clock() or str(config.AS_OF)})}
            last, sent = v, sent + 1
        await asyncio.sleep(poll)


@app.get("/api/events", summary="Live refresh notifications (server-sent events)")
async def events(request: Request):
    return EventSourceResponse(refresh_events(request))


# --------------------------------------------------------------------------- Ask the fleet

class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


_chat_slots = threading.BoundedSemaphore(2)       # bounds concurrent model calls (cost and load)


def answer_events(q: Question, emit) -> None:
    """Runs in a worker thread; reports progress through emit(dict)."""
    t0 = time.perf_counter()
    if not ai_enabled():
        emit({"type": "error", "message": "Ask the fleet is disabled: set ANTHROPIC_API_KEY or run `ant auth login` on the server. "
                                          "Every other page works without it."})
        return
    if not _chat_slots.acquire(blocking=False):
        emit({"type": "error", "message": "The assistant is busy with other questions; try again in a moment."})
        return
    try:
        tools = provider.get("app-chat")
        res = llm.run(INSTRUCTIONS, q.question, tools=tools, client=make_client(),
                      history=[t.model_dump() for t in q.history[-8:]], on_event=emit)
        text, ok, err = res.text, True, None
        try:
            tools._out.check(text)
        except IdentifierLeak as e:
            text, ok, err = BLOCKED, False, str(e)
        audit.write("app-chat", "chat_answer", {"question_chars": len(q.question), "history_turns": len(q.history)},
                    t0, ok=ok, rows=len(res.tool_calls), error=err)
        emit({"type": "answer", "text": text, "model": res.model, "tool_calls": len(res.tool_calls)})
    except llm.BriefingModelError as e:
        emit({"type": "error", "message": str(e)})
    except Exception as e:                                            # never leak internals to the client
        audit.write("app-chat", "chat_answer", {"question_chars": len(q.question)}, t0, ok=False,
                    error=f"{type(e).__name__}: {e}")
        emit({"type": "error", "message": "The assistant failed on this question; the server log has details."})
    finally:
        _chat_slots.release()


@app.post("/api/ask", summary="Ask the fleet: Claude answers using only the governed tools (SSE stream)")
async def ask(q: Question):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(e: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, e)

    def work():
        try:
            answer_events(q, emit)
        finally:
            emit({"type": "end"})

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while True:
            e = await queue.get()
            if e["type"] == "end":
                return
            yield {"event": e["type"], "data": json.dumps(e, default=str)}

    return EventSourceResponse(stream())


# --------------------------------------------------------------------------- React app

if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    index = WEB_DIST / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "The web app is not built yet: run `npm run build` in web/. "
                                       "The API is available under /api (docs at /docs)."}, status_code=404)
    target = (WEB_DIST / path).resolve()
    inside = WEB_DIST.resolve() in target.parents          # never serve files outside web/dist
    return FileResponse(target if path and inside and target.is_file() else index)


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=os.environ.get("KESTREL_HOST", "127.0.0.1"), port=int(os.environ.get("KESTREL_PORT", "8000")))


if __name__ == "__main__":
    run()
