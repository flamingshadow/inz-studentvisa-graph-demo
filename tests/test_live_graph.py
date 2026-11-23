"""Phase 3 + 6: TC-01..TC-10 and graph invariants against the live `studentvisa` database."""
import json

import pytest
import yaml
from neo4j import READ_ACCESS
from neo4j.exceptions import ClientError

from svkg import queries, service
from svkg.docspec import ROOT
from svkg.engine import check_result
from svkg.graph import LAYERS, SECTION3_TYPES

pytestmark = pytest.mark.neo4j
TCS = [f"TC-{i:02d}" for i in range(1, 11)]
VOCAB = [r["type"] for r in yaml.safe_load((ROOT / "schema/relationship-types.yaml").read_text())["relationship_types"]]


def q(live, cypher, **p):
    with live["driver"].session(database=live["db"]) as s:
        return s.run(cypher, **p).data()


@pytest.fixture(scope="module")
def assessed(live, apps):
    return {tc: service.assess(apps[tc], write=True, channel="pytest") for tc in TCS}


# ---- the ten synthetic test cases -------------------------------------------------
def _check(a, rule):
    if "check_id" in rule and "accepted" in rule:
        got = check_result(a, rule["check_id"], rule.get("rule_id"))
        assert got in rule["accepted"], (rule, got)
        evs = [e for e in a["rule_evaluations"] if e["check_id"] == rule["check_id"]]
        if "label" in rule:
            assert all(e["label"] == rule["label"] for e in evs)
        if "provenance_status" in rule:
            assert all(e["provenance_status"] == rule["provenance_status"] for e in evs)
        if "exception_rule_id" in rule:
            assert evs[0]["exception_rule_id"] == rule["exception_rule_id"]
    if "missing_information_includes" in rule:
        assert rule["missing_information_includes"] in {m["fact_path"] for m in a["missing_information"]}
    if "no_result_for_rule" in rule:
        assert not set(rule["forbidden"]) & {e["result"] for e in a["rule_evaluations"]
                                             if e["rule_id"] == rule["no_result_for_rule"]}
    if "human_review_includes" in rule:
        assert rule["human_review_includes"] in {h["rule_id"] for h in a["human_review"]}
    if "production_decision" in rule:
        assert a["scope"]["production_decision"] is rule["production_decision"]
    if "evidence_used_type" in rule:
        ev = next(e for e in a["rule_evaluations"] if e["check_id"] == rule["check_id"])
        assert ev["evidence_item_ids"], "medical evidence must be listed as used"
    if rule.get("deterministic_all_pass"):
        det = [e for e in a["rule_evaluations"] if e["automation_class"] in ("A1", "A2")
               and e["result"] != "SOURCE_COVERAGE_GAP"]
        assert det and all(e["result"] in ("SATISFIED", "NOT_APPLICABLE") for e in det)


@pytest.mark.parametrize("tc", TCS)
def test_synthetic_case(tc, assessed, expected, live):
    a = assessed[tc]
    assert a["scope"]["production_decision"] is False and a["scope"]["source_coverage"] == "PARTIAL"
    assert check_result(a, "SV-CHECK-DISC-001") == "DISCRETION_REQUIRED"
    for rule in expected[tc]["assert"]:
        if "explanation_chain_for_rule" in rule:
            ev = next(e for e in a["rule_evaluations"] if e["rule_id"] == rule["explanation_chain_for_rule"])
            x = queries.explain_evaluation(ev["evaluation_id"])
            assert [s["step"] for s in x["explanation_chain"]] == rule["chain"]
            assert x["complete"] and x["facts_and_evidence"]
        else:
            _check(a, rule)


def test_tc06_deviation_documented(expected):
    assert "SOURCE_COVERAGE_GAP" in expected["TC-06"]["deviation"] and "IF-30" in expected["TC-06"]["deviation"]
    assert expected["TC-06"]["rule_id"] == "SV-OM-U3.20.30-A-I"


# ---- graph invariants ---------------------------------------------------------------
def test_labels_only_from_section3(live, assessed):
    allowed = set(LAYERS) | {t for ts in SECTION3_TYPES.values() for t in ts} | {"LoadLog"}
    labels = {l for r in q(live, "MATCH (n) RETURN DISTINCT labels(n) AS l") for l in r["l"]}
    assert labels <= allowed, labels - allowed


def test_relationships_only_from_vocabulary(live, assessed):
    types = {r["t"] for r in q(live, "MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert types <= set(VOCAB), types - set(VOCAB)


def test_constraints_and_fulltext(live):
    cons = {r["name"] for r in q(live, "SHOW CONSTRAINTS YIELD name")}
    assert {f"{l.lower()}_id" for l in LAYERS} <= cons
    idx = {r["name"] for r in q(live, "SHOW FULLTEXT INDEXES YIELD name")}
    assert {"rule_text", "provision_text"} <= idx


def test_graph_counts_match_files(live):
    rules = json.loads((ROOT / "rules/rules.json").read_text())
    n = q(live, "MATCH (r:Rule) WHERE r.origin='SPEC' RETURN count(r) AS n")[0]["n"]
    assert n == len(rules["rules"]) + len(rules["candidate_rules"]) + len(rules.get("instruction_rules", []))
    rels = json.loads((ROOT / "regulatory/relationships.json").read_text())["relationships"]
    assert live["load"]["spec_relationships"] == len(rels)


def test_load_is_idempotent(live):
    from svkg import graph
    before = q(live, "MATCH (n) WHERE n.origin='SPEC' RETURN count(n) AS n")[0]["n"]
    logs = q(live, "MATCH (l:LoadLog) RETURN count(l) AS n")[0]["n"]
    graph.load()
    assert q(live, "MATCH (n) WHERE n.origin='SPEC' RETURN count(n) AS n")[0]["n"] == before
    assert q(live, "MATCH (l:LoadLog) RETURN count(l) AS n")[0]["n"] == logs + 1


def test_evidence_to_evaluation_path(live, assessed):
    aid = assessed["TC-01"]["assessment_id"]
    rows = q(live, """MATCH p=(:EvidenceItem)-[:SUPPORTS_FACT]->(:ObservedFact)-[:VERIFIED_AS]->(v:VerifiedFact)
                      -[i:INPUT_TO]->(:DecisionCheck)-[:PRODUCES_OUTCOME]->(ev:RuleEvaluation {assessment_id:$a})
                      WHERE i.evaluation_id = ev.id RETURN count(p) AS n""", a=aid)
    assert rows[0]["n"] > 0


def test_rule_to_instrument_path(live):
    rows = q(live, """MATCH (r:Rule {catalogue:'VERIFIED'})
                      OPTIONAL MATCH (r)-[:DERIVED_FROM {primary:true}]->(p:Provision)
                      OPTIONAL MATCH (s:AuthoritySource)-[:CONTAINS]->(:InstrumentVersion)-[:CONTAINS]->(:Provision)
                                     -[:PARENT_OF*0..]->(p)
                      WITH r, count(s) AS n WHERE n = 0 RETURN r.id AS id""")
    assert rows == []


def test_assessment_nodes_written(live, assessed):
    """IF-34: one RuleEvaluation per rule; one DecisionOutcome and one Explanation per assessment."""
    aid = assessed["TC-09"]["assessment_id"]
    r = q(live, """MATCH (:Application)-[:PRODUCES_OUTCOME]->(o:DecisionOutcome {id:$a})-[:CONTAINS]->(ev:RuleEvaluation)
                   MATCH (o)-[:EXPLAINED_BY]->(x:Explanation)
                   RETURN o.production_decision AS pd, count(DISTINCT ev) AS n, count(DISTINCT x) AS nx,
                          collect(DISTINCT ev.result) AS results""", a=aid)[0]
    assert r["pd"] is False and r["n"] == len(assessed["TC-09"]["rule_evaluations"]) and r["nx"] == 1
    assert "NOT_SATISFIED" in r["results"]
    extra = q(live, "MATCH (ev:RuleEvaluation {assessment_id:$a})-[:PRODUCES_OUTCOME|EXPLAINED_BY]->() "
                    "RETURN count(*) AS n", a=aid)[0]["n"]
    assert extra == 0


def test_graph_is_minimal(live):
    """IF-34: only profile labels/relationships, and no isolated spec nodes except interpretation flags."""
    from svkg.graph import profile
    prof = profile()
    labels = {l for r in q(live, "MATCH (n) RETURN DISTINCT labels(n) AS l") for l in r["l"]}
    assert labels <= set(prof["node_labels"])
    types = {r["t"] for r in q(live, "MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert types <= set(prof["relationship_types"])
    iso = q(live, "MATCH (n) WHERE n.origin='SPEC' AND NOT (n)--() AND NOT n:InterpretationFlag RETURN count(n) AS n")
    assert iso[0]["n"] == 0


def test_audit_log_appended(assessed):
    lines = (ROOT / "logs/audit-log.jsonl").read_text().splitlines()
    aid = assessed["TC-05"]["assessment_id"]
    events = {json.loads(l)["event"] for l in lines if aid in l}
    assert {"APPLICATION_SUBMITTED", "EVIDENCE_RECEIVED", "FACT_VERIFIED", "RULE_EVALUATED",
            "HUMAN_REVIEW_REQUESTED"} <= events


def test_read_session_rejects_writes(live):
    with live["driver"].session(database=live["db"], default_access_mode=READ_ACCESS) as s:
        with pytest.raises(ClientError):
            s.execute_read(lambda tx: tx.run("CREATE (:Governance {id:'SHOULD-NOT-EXIST'})").consume())
    assert q(live, "MATCH (n {id:'SHOULD-NOT-EXIST'}) RETURN count(n) AS n")[0]["n"] == 0


def test_check_provenance_survives_load(live):
    rows = {r["id"]: r["d"] for r in q(live, "MATCH (c:DecisionCheck) RETURN c.id AS id, c.defined_in AS d")}
    assert rows["SV-CHECK-APP-001"] == "Table 11 (Section 7)"
    assert rows["SV-CHECK-MIN-001"].startswith("Implied by TC-03")
    assert all(rows.values())


def test_discretion_text_returned(live):
    from svkg import queries
    d = next(x for x in queries.rule_detail("SV-MIN-001")["qualifiers"] if x["type"] == "Discretion")
    assert d["text"].startswith("The Immigration Officer may decline")
