"""Connection settings, read from .env (never committed)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase, NotificationDisabledClassification

from .docspec import ROOT

load_dotenv(ROOT / ".env")

LOG_DIR = ROOT / "logs"


@dataclass(frozen=True)
class Settings:
    uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user: str = os.getenv("NEO4J_USER", "neo4j")
    password: str = os.getenv("NEO4J_PASSWORD", "")
    database: str = os.getenv("NEO4J_DATABASE", "studentvisa")


def settings() -> Settings:
    return Settings()


def driver(s: Settings | None = None) -> Driver:
    s = s or settings()
    if not s.password:
        raise RuntimeError("NEO4J_PASSWORD is not set; copy .env.example to .env and fill it in")
    return GraphDatabase.driver(
        s.uri, auth=(s.user, s.password),
        notifications_disabled_classifications=[NotificationDisabledClassification.DEPRECATION])
