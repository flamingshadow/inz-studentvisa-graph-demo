"""Assess an application against the live graph and record the result."""
from __future__ import annotations

from .config import driver, settings
from .engine import Application, Engine, GraphStore
from .record import audit_assessment, write_assessment


def assess(application: dict, *, write: bool = True, channel: str = "cli") -> dict:
    app = Application.model_validate(application)
    s = settings()
    with driver(s) as d, d.session(database=s.database) as session:
        engine = Engine(GraphStore(session))
        a = engine.assess(app)
        if write:
            write_assessment(session, app, a)
    audit_assessment(app, a, channel=channel)
    return a
