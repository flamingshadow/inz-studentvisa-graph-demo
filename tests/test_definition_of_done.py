"""Section 13 POC definition of done, item by item, against the live graph."""
import json

import pytest

from svkg import queries, service
from svkg.docspec import ROOT

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def batch(live, apps):
    return [service.assess(a, write=True, channel="pytest-dod") for a in apps.values()]


def test_dod1_rules_with_instrument_and_exact_provision(live):
    """The agent can answer what Student Visa rules apply and return the governing instrument and exact provision."""
    rules = {r["rule_id"]: r for r in json.loads((ROOT / "rules/rules.json").read_text())["rules"]}
    seen = 0
    for d in {r["domain"] for r in rules.values()}:
        for row in queries.rules_by_domain(d, include_candidates=False):
            src = rules[row["rule_id"]]
            assert row["instrument"] == src["authority"]["regulatory_instrument"]
            assert row["provision"] == src["authority"]["provision"]
            seen += 1
    assert seen == len(rules)
    detail = queries.rule_detail("SV-APP-005")
    primary = next(p for p in detail["provisions"] if p["primary"])
    assert (primary["reference"], primary["instrument"]) == ("s 58(3)(a)", "Immigration Act 2009")


def test_dod2_rule_to_evidence_and_evidence_to_facts(live):
    ev = queries.evidence_for_rule("SV-CHAR-001")
    assert any(e["evidence_item"] == "Police certificate" for e in ev)
    facts = queries.facts_supported_by_evidence("Police certificate")["evidence_types"]
    assert facts[0]["supports_fact"] == "Character information" and "SV-CHAR-001" in facts[0]["rules"]


def test_dod3_deterministic_checks_on_synthetic_applications(batch):
    det = [e for a in batch for e in a["rule_evaluations"] if e["automation_class"] in ("A1", "A2")]
    assert {e["result"] for e in det} >= {"SATISFIED", "NOT_APPLICABLE", "UNKNOWN"}


def test_dod4_distinguishes_outcomes(batch):
    results = {e["result"] for a in batch for e in a["rule_evaluations"]}
    assert {"NOT_SATISFIED", "UNKNOWN", "HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED",
            "SOURCE_COVERAGE_GAP"} <= results


def test_dod5_every_result_explainable(batch):
    a = batch[0]
    for e in a["rule_evaluations"]:
        x = queries.explain_evaluation(e["evaluation_id"])
        steps = [s["step"] for s in x["explanation_chain"]]
        assert steps[:3] == ["RuleEvaluation", "DecisionCheck", "Rule"]
        if e["catalogue"] == "VERIFIED":
            assert steps[3:] == ["Provision", "InstrumentVersion", "AuthoritySource"] and x["complete"]
        else:
            assert steps[3:] == ["CoverageGap"]          # candidate: says so, never invents a provision


def test_dod6_candidate_rules_not_presented_as_production(batch):
    """Reconciliation to Immigration Instructions is outstanding by design; the guard is that nothing claims production."""
    rules = json.loads((ROOT / "rules/rules.json").read_text())
    assert all(c["provenance_status"] == "RECONCILIATION_REQUIRED" for c in rules["candidate_rules"])
    for a in batch:
        assert a["scope"]["production_decision"] is False
        for e in a["rule_evaluations"]:
            if e["catalogue"] == "CANDIDATE":
                assert e["provenance_status"] == "RECONCILIATION_REQUIRED"
