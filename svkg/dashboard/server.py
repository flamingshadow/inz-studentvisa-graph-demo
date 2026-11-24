"""Local dashboard: a small web app on 127.0.0.1 for viewing scenarios and running them.

It needs no Claude API key. The engine is ordinary Python; the only thing it needs is the live
`studentvisa` database. By default a run writes nothing; the "save to graph" option records the
assessment exactly as `svkg assess` does.

Safety: it listens on the loopback address only, rejects other Host headers (DNS rebinding), and
requires a custom header on every POST, which a page on another website cannot send.
"""
from __future__ import annotations

import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import rag, scenarios
from ..config import driver, settings

STATIC = Path(__file__).parent / "static"
GUARD = "x-svkg-dashboard"


def _json(data, status=200):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def _need_header(request: Request):
    if request.headers.get(GUARD) != "1":
        raise HTTPException(403, "missing dashboard header")


async def index(request: Request):
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def meta(request: Request):
    s = settings()
    info = {"database": s.database, "connected": False, "spec_nodes": None, "last_load": None, "error": None}
    try:
        with driver(s) as d, d.session(database=s.database) as session:
            info["spec_nodes"] = session.run("MATCH (n) WHERE n.origin='SPEC' RETURN count(n) AS n").single()["n"]
            row = session.run("MATCH (l:LoadLog) RETURN l.loaded_at AS t ORDER BY l.loaded_at DESC LIMIT 1").single()
            info["last_load"] = row["t"] if row else None
            info["connected"] = True
    except Exception as e:                                       # shown in the page; scenarios still browse
        info["error"] = f"{type(e).__name__}: {e}"[:300]
    info["rag"] = {k: {x: v[x] for x in ("rag", "icon", "label")} for k, v in rag.RAG.items()}
    return _json(info)


async def list_scenarios(request: Request):
    return _json(scenarios.list_scenarios())


async def get_scenario(request: Request):
    d = scenarios.scenario_detail(request.path_params["sid"])
    if d is None:
        raise HTTPException(404, "unknown scenario")
    return _json(d)


async def run_scenario(request: Request):
    _need_header(request)
    sid = request.path_params["sid"]
    save = request.query_params.get("save") == "1"
    try:
        return _json(scenarios.run_one(sid, save=save))
    except KeyError:
        raise HTTPException(404, "unknown scenario")
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"[:400]}, 503)


async def run_all(request: Request):
    _need_header(request)
    try:
        views = scenarios.run([s["id"] for s in scenarios.list_scenarios()], save=False)
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"[:400]}, 503)
    rows = [{"id": v["scenario"]["id"], "passed": v["passed"], "overall": v["overall"], "rag_counts": v["rag_counts"],
             "failed": [x["text"] for x in v["expectations"] if not x["passed"]]} for v in views]
    return _json({"results": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows)})


def create_app(port: int = 8765) -> Starlette:
    hosts = ["127.0.0.1", "localhost", "testserver"]
    return Starlette(
        routes=[Route("/", index), Route("/api/meta", meta), Route("/api/scenarios", list_scenarios),
                Route("/api/scenarios/{sid}", get_scenario),
                Route("/api/scenarios/{sid}/run", run_scenario, methods=["POST"]),
                Route("/api/run-all", run_all, methods=["POST"]),
                Mount("/static", StaticFiles(directory=STATIC), name="static")],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts)])


def serve(port: int = 8765, open_browser: bool = False):
    import uvicorn
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    print(f"Student Visa scenario dashboard: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    uvicorn.run(create_app(port), host="127.0.0.1", port=port, log_level="warning")
