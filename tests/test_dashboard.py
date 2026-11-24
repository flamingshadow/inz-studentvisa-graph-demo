"""The local dashboard: API, safety checks and static assets."""
import re

import pytest
from starlette.testclient import TestClient

from svkg.dashboard.server import STATIC, create_app

H = {"X-SVKG-Dashboard": "1"}
client = TestClient(create_app())


def test_page_and_assets_are_served():
    r = client.get("/")
    assert r.status_code == 200 and "Scenario lab" in r.text and r.headers["cache-control"] == "no-store"
    for f in ("style.css", "app.js"):
        assert client.get(f"/static/{f}").status_code == 200
    assert "color-scheme" in r.text and "prefers-color-scheme" in r.text            # light and dark


def test_scenario_list_and_detail():
    lst = client.get("/api/scenarios").json()
    assert len(lst) == 50 and {"id", "title", "expected_rag", "expected_icon", "group"} <= set(lst[0])
    d = client.get("/api/scenarios/SVP-016").json()
    assert d["id"] == "SVP-016" and d["facts"] and d["expectations"]
    assert client.get("/api/scenarios/NOPE-1").status_code == 404


def test_posts_need_the_dashboard_header_and_a_local_host():
    assert client.post("/api/scenarios/SVP-016/run").status_code == 403
    assert client.post("/api/run-all").status_code == 403
    assert TestClient(create_app(), base_url="http://evil.example").get("/api/meta").status_code == 400
    assert client.get("/api/scenarios/SVP-016/run").status_code == 405           # running is POST only


def test_front_end_never_inserts_html():
    js = (STATIC / "app.js").read_text()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", js)
    assert "http://" not in (STATIC / "index.html").read_text().replace("http://www.w3.org", "")   # no external loads


@pytest.mark.neo4j
def test_meta_reports_the_database(live):
    m = client.get("/api/meta").json()
    assert m["connected"] and m["database"] == "studentvisa" and m["spec_nodes"] > 1000 and m["rag"]["SATISFIED"]["rag"] == "green"


@pytest.mark.neo4j
def test_run_one_and_run_all(live):
    r = client.post("/api/scenarios/SVP-017/run", headers=H)
    assert r.status_code == 200
    v = r.json()
    assert v["overall"]["rag"] == "red" and v["passed"] is True and v["saved"] is False and v["digest"]["key_actions"]
    assert client.post("/api/scenarios/NOPE-1/run", headers=H).status_code == 404
    a = client.post("/api/run-all", headers=H).json()
    assert a["total"] == 50 and a["passed"] == 50 and all("overall" in x for x in a["results"])


@pytest.mark.neo4j
def test_save_option_records_the_assessment_and_nothing_else(live):
    v = client.post("/api/scenarios/SVP-016/run?save=1", headers=H).json()
    aid = v["assessment"]["assessment_id"]
    try:
        assert v["saved"] is True
        with live["driver"].session(database=live["db"]) as s:
            n = s.run("MATCH (o:DecisionOutcome {id:$a})-[:CONTAINS]->(e:RuleEvaluation) RETURN count(e) AS n", a=aid).single()["n"]
        assert n == sum(len(g["evaluations"]) for g in v["groups"])
    finally:                                                                  # leave the database as found
        with live["driver"].session(database=live["db"]) as s:
            s.run("MATCH (n) WHERE n.assessment_id = $a DETACH DELETE n", a=aid).consume()
