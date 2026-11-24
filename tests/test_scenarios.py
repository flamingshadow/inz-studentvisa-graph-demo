"""RAG mapping, digest of key actions, and scenario expectations. The first group needs no database."""
import json
import re

import pytest

from svkg import digest as dg
from svkg import rag, scenarios
from svkg.engine import OUTCOMES
from svkg.docspec import ROOT


def ev(rule, result, check="SV-CHECK-X", **kw):
    return {"evaluation_id": f"E-{rule}", "rule_id": rule, "check_id": check, "result": result, "provision": "s 1",
            "explanation": f"{rule} explanation.", "unresolved_requirements": [], "automation_class": "A2",
            "rulespeak_statement": f"Statement of {rule}.", **kw}


def asm(*evs, missing=()):
    return {"assessment_id": "A-1", "application_id": "APP-1", "assessment_as_at": "2026-11-24",
            "scope": {"source_coverage": "PARTIAL", "production_decision": False},
            "summary": {"overall_status": "HUMAN_REVIEW_REQUIRED"}, "rule_evaluations": list(evs),
            "missing_information": list(missing)}


# ---- RAG mapping -------------------------------------------------------------------------
def test_every_outcome_has_a_rag_icon_and_label():
    assert set(OUTCOMES) == set(rag.OUTCOMES)
    for o in OUTCOMES:
        r = rag.of(o)
        assert r["rag"] in ("red", "amber", "green", "grey") and r["icon"] and r["label"]


def test_rag_meaning():
    assert rag.of("SATISFIED")["rag"] == "green" and rag.of("NOT_APPLICABLE")["rag"] == "grey"
    assert {rag.of(o)["rag"] for o in ("NOT_SATISFIED", "AMBIGUOUS")} == {"red"}
    assert {rag.of(o)["rag"] for o in ("UNKNOWN", "HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED",
                                       "SOURCE_COVERAGE_GAP")} == {"amber"}


def test_overall_is_worst_colour_and_never_green_with_a_discretion():
    assert rag.overall(["SATISFIED", "UNKNOWN", "NOT_SATISFIED"]) == "red"
    assert rag.overall(["SATISFIED", "DISCRETION_REQUIRED"]) == "amber"
    assert rag.overall(["SATISFIED", "NOT_APPLICABLE"]) == "green"
    assert rag.counts(["SATISFIED", "SATISFIED", "UNKNOWN"]) == {"red": 0, "amber": 1, "green": 2, "grey": 0}


# ---- digest -----------------------------------------------------------------------------
def test_unknown_becomes_an_evidence_action_using_the_request_text():
    a = asm(ev("SV-ID-001", "UNKNOWN"), missing=[{"rule_id": "SV-ID-001", "request": "Request the passport copy"}])
    act = dg.digest(a)["key_actions"][0]
    assert act["type"] == "OBTAIN_EVIDENCE" and act["rag"] == "amber" and "Request the passport copy" in act["action"]
    assert act["owner"] == "Case officer and applicant"


def test_not_met_is_first_and_never_recommends_an_outcome():
    a = asm(ev("SV-A", "UNKNOWN"), ev("SV-B", "NOT_SATISFIED"),
            ev("SV-DEC-002", "DISCRETION_REQUIRED", check="SV-CHECK-DISC-001", authority_presented={"decision_maker": "Minister"}))
    acts = dg.digest(a)["key_actions"]
    assert [x["type"] for x in acts] == ["RESOLVE", "OBTAIN_EVIDENCE", "DECISION"]
    assert "does not decide" in acts[0]["action"]
    assert acts[-1]["check_id"] == "SV-CHECK-DISC-001" and "rests with" in acts[-1]["action"]
    assert [x["order"] for x in acts] == [1, 2, 3]


def test_many_similar_items_are_consolidated():
    evs = [ev(f"SV-R-{i}", "HUMAN_REVIEW_REQUIRED", check=f"SV-CHECK-{i}") for i in range(7)]
    acts = dg.digest(asm(*evs))["key_actions"]
    assert len(acts) == 1 and len(acts[0]["items"]) == 7 and "7 items need officer review" in acts[0]["action"]
    assert len(dg.digest(asm(*evs[:3]))["key_actions"]) == 3          # three or fewer stay separate


def test_digest_carries_notice_guidance_and_counts():
    d = dg.digest(asm(ev("SV-A", "SATISFIED"), ev("SV-B", "SATISFIED", label="CANDIDATE_FIGURE – X"), ev("SV-C", "UNKNOWN")),
                  expectations=[{"text": "t", "passed": False, "detail": "d"}])
    assert d["production_decision"] is False and "synthetic" in d["poc_notice"].lower()
    assert d["rag_counts"]["green"] == 2 and d["overall"]["rag"] == "amber" and len(d["met"]) == 2
    assert d["candidate_figure_labels"] == ["CANDIDATE_FIGURE – X"]
    assert "Never recommend granting or declining" in d["writing_guidance"]
    assert d["expectations"]["failed"] and d["expectations"]["passed"] == 0


# ---- expectations ------------------------------------------------------------------------
def test_expectations_pass_and_fail_visibly():
    a = asm(ev("SV-ID-001", "UNKNOWN", check="SV-CHECK-APP-001"),
            ev("SV-DEC-002", "DISCRETION_REQUIRED", check="SV-CHECK-DISC-001"),
            missing=[{"rule_id": "SV-ID-001", "fact_path": "application.identity_document_provided", "request": "r"}])
    ok = scenarios.evaluate(a, [{"check_id": "SV-CHECK-APP-001", "accepted": ["UNKNOWN"], "rule_id": "SV-ID-001"},
                                {"missing_information_includes": "application.identity_document_provided"}])
    assert all(x["passed"] for x in ok) and len(ok) == 4                    # two asserted + two standing
    bad = scenarios.evaluate(a, [{"check_id": "SV-CHECK-APP-001", "accepted": ["SATISFIED"], "rule_id": "SV-ID-001"},
                                 {"check_id": "SV-CHECK-NONE", "accepted": ["SATISFIED"]}, {"nonsense": 1}])
    assert [x["passed"] for x in bad[:3]] == [False, False, False]
    assert "actual: UNKNOWN" in bad[0]["detail"] and "no evaluation" in bad[1]["detail"] and "could not evaluate" in bad[2]["detail"]


def test_catalogue_covers_fifty_personas_with_a_rag_each():
    sc = scenarios.list_scenarios()
    assert len(sc) == 50 and sc[0]["id"] == "SVP-001" and sc[-1]["id"] == "SVP-050"
    assert all(s["expected_rag"] in ("red", "amber", "green", "grey") and s["title"] and s["group"] for s in sc)
    d = scenarios.scenario_detail("SVP-016")
    assert d["facts"] and d["evidence"] and d["expectations"] and all(x["passed"] is None for x in d["expectations"])
    assert not {"email", "passport_number"} & set(d["applicant"])           # personal-looking fields are not shown
    assert scenarios.scenario_detail("TC-01") is None


# ---- against the live database ---------------------------------------------------------
@pytest.mark.neo4j
def test_all_fifty_scenarios_run_and_meet_expectations(live):
    views = scenarios.run([s["id"] for s in scenarios.list_scenarios()])
    failed = [(v["scenario"]["id"], [x["text"] for x in v["expectations"] if not x["passed"]]) for v in views if not v["passed"]]
    assert failed == [] and len(views) == 50
    assert all(v["saved"] is False for v in views)


@pytest.mark.neo4j
def test_a_dry_run_writes_nothing(live):
    def count():
        with live["driver"].session(database=live["db"]) as s:
            return s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
    before = count()
    scenarios.run_one("SVP-016")
    assert count() == before


@pytest.mark.neo4j
def test_run_view_has_everything_the_page_shows(live):
    v = scenarios.run_one("SVP-016")
    fin = next(e for g in v["groups"] for e in g["evaluations"] if e["rule_id"] == "SV-OM-U3.20.30-A-I")
    assert fin["rag"] == "green" and fin["icon"] == "check" and fin["explanation"] and fin["provision"] == "U3.20.30(a)(i)"
    assert "36 weeks or longer" in fin["provision_text"] and fin["provision_url"].startswith("https://www.immigration.govt.nz/")
    assert {f["attribute"] for f in fin["facts_used"]} >= {"student_type", "available_funds_nzd"}
    assert all("evidence" in f for f in fin["facts_used"]) and fin["evidence_used"]
    assert v["groups"][-1]["check_id"] == "SV-CHECK-DISC-001"                 # final decision always last
    c = scenarios.compact(v)
    assert c["expected_result_met"] and c["key_actions"] and len(c["rules"]) == 17 and len(json.dumps(c)) < 40000
