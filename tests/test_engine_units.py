"""Phase 4 unit tests: every outcome type and automation class (file-backed spec store)."""
import json

import pytest

from svkg.docspec import ROOT
from svkg.engine import CANDIDATE_LABEL, OUTCOMES, Ctx, Application, Engine, check_result

from conftest import COMPLETE, base_app

E = Engine()


def run(**facts):
    return E.assess(base_app(**facts))


def res(a, rule_id):
    return [e["result"] for e in a["rule_evaluations"] if e["rule_id"] == rule_id]


# ---- outcome types ---------------------------------------------------------------
def test_satisfied():
    assert res(run(**COMPLETE), "SV-FORM-003") == ["SATISFIED"]


def test_not_satisfied():
    assert res(run(**{**COMPLETE, "completed_in_english": False}), "SV-FORM-003") == ["NOT_SATISFIED"]


def test_unknown_when_missing():
    f = dict(COMPLETE)
    del f["completed_in_english"]
    a = run(**f)
    assert res(a, "SV-FORM-003") == ["UNKNOWN"]
    assert any(m["fact_path"] == "application.completed_in_english" for m in a["missing_information"])


def test_not_applicable_exception():
    a = run(**{**COMPLETE, "scholarship_administrator": "MFAT"})
    ev = next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-IVL-001")
    assert ev["result"] == "NOT_APPLICABLE" and ev["exception_rule_id"] == "SV-IVL-001"


def test_human_review_required_a3():
    ctx = Ctx.build(Application.model_validate(base_app()))
    ev = E.route("ASSESS-UNIT", ctx, "SV-EDU-001", {"check_id": "SV-CHECK-UNIT"})
    assert ev["automation_class"] == "A3" and ev["result"] == "HUMAN_REVIEW_REQUIRED"


def test_discretion_required_a4_presents_authority():
    ctx = Ctx.build(Application.model_validate(base_app()))
    ev = E.route("ASSESS-UNIT", ctx, "SV-APP-007", {"check_id": "SV-CHECK-UNIT"})
    assert ev["result"] == "DISCRETION_REQUIRED"
    auth = ev["authority_presented"]
    assert auth["provision"] == "s 58(6)(a)" and auth["regulatory_instrument"] == "Immigration Act 2009"
    assert auth["constraints"]   # the 'if false or misleading...' condition


def test_ambiguous_a5_stops():
    ctx = Ctx.build(Application.model_validate(base_app()))
    ev = E.route("ASSESS-UNIT", ctx, "SV-DEC-001", {"check_id": "SV-CHECK-UNIT"})
    assert ev["automation_class"] == "A5" and ev["result"] == "AMBIGUOUS"
    a = E._assemble("ASSESS-UNIT", ctx.app, ctx, [ev], [], __import__("datetime").datetime.now())
    assert a["summary"]["overall_status"] == "AMBIGUOUS"


def test_source_coverage_gap():
    a = run(**COMPLETE)
    assert res(a, "SV-INSTR-001") == ["HUMAN_REVIEW_REQUIRED", "SOURCE_COVERAGE_GAP"]   # IF-33
    hr = next(e for e in a["rule_evaluations"] if e["rule_id"] == "SV-INSTR-001" and e["result"] != "SOURCE_COVERAGE_GAP")
    assert "SV-OM-U3.1-A-I" in hr["instruction_rules_presented"]
    gap = next(g for g in a["source_coverage_gaps"] if g["kind"] == "SOURCE_COVERAGE_GAP")
    assert gap["coverage_gap_id"] == "GAP-01" and gap["must_not_infer"] is True


def test_online_gap_rule():
    a = E.assess({**base_app(**COMPLETE), "channel": "online"})
    assert res(a, "SV-ONLINE-007") == ["HUMAN_REVIEW_REQUIRED", "SOURCE_COVERAGE_GAP"]
    assert "SV-ONLINE-001" in {e["rule_id"] for e in a["rule_evaluations"]}


# ---- automation classes ------------------------------------------------------------
def test_a1_runs_on_unverified_structured_data():
    a = run(**{**COMPLETE, "approved_form_used": ("UNVERIFIED", True)})
    ev = next(e for e in a["rule_evaluations"] if e["rule_id"] == "SV-FORM-001")
    assert ev["automation_class"] == "A1" and ev["result"] == "SATISFIED"


def test_a2_requires_verified_fact():
    a = run(**{**COMPLETE, "identity_document_provided": ("UNVERIFIED", True)})
    ev = next(e for e in a["rule_evaluations"] if e["rule_id"] == "SV-ID-001")
    assert ev["automation_class"] == "A2" and ev["result"] == "UNKNOWN"
    assert any(m["reason"] == "UNVERIFIED" for m in a["missing_information"])


def test_missing_evidence_never_fails():
    a = E.assess(base_app())            # no facts at all
    assert "NOT_SATISFIED" not in {e["result"] for e in a["rule_evaluations"]}
    assert a["summary"]["not_satisfied"] == 0


def test_final_grant_always_discretion():
    for facts in (COMPLETE, {}, {**COMPLETE, "completed_in_english": False}):
        a = run(**facts)
        grant = [e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-DISC-001"]
        assert [g["result"] for g in grant] == ["DISCRETION_REQUIRED"]
        assert a["summary"]["overall_status"] != "SATISFIED"
        assert a["scope"] == {"source_coverage": "PARTIAL", "production_decision": False}


def test_no_scores_for_health_character_or_genuine_intentions():
    a = E.assess(json.loads((ROOT / "testing/applications/TC-07.json").read_text()))
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not [k for k in keys(a) if "score" in k.lower()]
    health = [e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-HEALTH-001"]
    assert [h["result"] for h in health] == ["HUMAN_REVIEW_REQUIRED"]
    rules = json.loads((ROOT / "rules/rules.json").read_text())
    genuine = next(c for c in rules["candidate_rules"] if c["rule_id"] == "SV-CAND-GENUINE")
    assert genuine["automation"]["class"] == "A3" and not genuine["executable"]


def test_every_evaluation_carries_section9_fields():
    a = run(**COMPLETE)
    for e in a["rule_evaluations"]:
        for k in ("rule_id", "facts_used", "evidence_item_ids", "unresolved_requirements", "automation_class",
                  "regulatory_instrument", "provision", "source_version", "result", "check_id"):
            assert k in e, (e["rule_id"], k)
        assert e["result"] in OUTCOMES


def test_assessment_matches_12_2_shape():
    tmpl = json.loads(json.dumps(__import__("yaml").safe_load(
        (ROOT / "schema/ontology.yaml").read_text())["templates"]["assessment_json_12_2"]))
    a = run(**COMPLETE)
    assert list(a) == list(tmpl)
    assert list(a["summary"]) == list(tmpl["summary"])
    assert list(a["scope"]) == list(tmpl["scope"])
    assert list(a["provenance"]) == list(tmpl["provenance"])


# ---- check-specific boundaries ------------------------------------------------------
@pytest.mark.parametrize("funds,months,stype,expect,rule", [
    (20000, 12, "tertiary", "SATISFIED", "SV-OM-U3.20.30-A-I"),      # 52 weeks: yearly figure
    (19999, 12, "tertiary", "NOT_SATISFIED", "SV-OM-U3.20.30-A-I"),
    (1667 * 6, 6, "tertiary", "SATISFIED", "SV-OM-U3.20.25-A-I"),    # 26 weeks: monthly figure
    (17000, 12, "school", "SATISFIED", "SV-OM-U3.20.30-A-II"),
    (1417 * 3 - 1, 3, "school", "NOT_SATISFIED", "SV-OM-U3.20.25-A-II"),
    (1667 * 8, 8, "tertiary", "SATISFIED", "SV-OM-U3.20.25-A-I"),    # 34.7 weeks: still under 36
    (19999, 9, "tertiary", "NOT_SATISFIED", "SV-OM-U3.20.30-A-I"),   # 39 weeks: one full year required
    (29999, 18, "tertiary", "NOT_SATISFIED", "SV-OM-U3.20.30-A-I"),  # pro rata beyond 12 months: 30,000
])
def test_funds_u3_20(funds, months, stype, expect, rule):
    a = run(**{**COMPLETE, "available_funds_nzd": funds, "study_duration_months": months, "student_type": stype})
    ev = next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-FIN-001")
    assert (ev["rule_id"], ev["result"]) == (rule, expect)
    assert ev["provenance_status"] == "VERIFIED_INSTRUCTION_SOURCE" and "label" not in ev
    assert ev["provision"].startswith(("U3.20.25", "U3.20.30")) and ev["source_version"] == "2022-09-26"


def test_funds_36_week_boundary_in_weeks():
    f = {k: v for k, v in COMPLETE.items() if k != "study_duration_months"}
    a = run(**{**f, "programme_duration_weeks": 35, "available_funds_nzd": 1667 * 9})
    assert next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-FIN-001")["rule_id"] == "SV-OM-U3.20.25-A-I"
    a = run(**{**f, "programme_duration_weeks": 36, "available_funds_nzd": 19000})
    ev = next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-FIN-001")
    assert (ev["rule_id"], ev["result"]) == ("SV-OM-U3.20.30-A-I", "NOT_SATISFIED")


def test_funds_less_prepaid_living_expenses():
    a = run(**{**COMPLETE, "available_funds_nzd": 15000, "prepaid_living_expenses_nzd": 5000})
    ev = next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-FIN-001")
    assert ev["result"] == "SATISFIED" and ev["calculation"]["required_nzd"] == 15000


def test_funds_samoa_tonga_guarantee():
    a = run(**{**COMPLETE, "available_funds_nzd": 100, "citizenship": "Tonga",
               "maintenance_guarantee_from_nz_relative": True})
    ev = next(e for e in a["rule_evaluations"] if e["check_id"] == "SV-CHECK-FIN-001")
    assert (ev["rule_id"], ev["result"]) == ("SV-OM-U3.20.30-A-III", "SATISFIED")


def test_funds_unverified_is_unknown():
    a = run(**{**COMPLETE, "available_funds_nzd": ("UNVERIFIED", 50000)})
    assert check_result(a, "SV-CHECK-FIN-001") == "UNKNOWN"


def test_maternity_u3_20_35():
    base = {**COMPLETE, "pregnant_due_to_give_birth_in_nz": True,
            "eligible_for_publicly_funded_maternity_services": False}
    assert res(run(**{**base, "available_funds_nzd": 28000}), "SV-OM-U3.20.35-P1-A") == ["NOT_SATISFIED"]
    assert res(run(**{**base, "available_funds_nzd": 29000}), "SV-OM-U3.20.35-P1-A") == ["SATISFIED"]
    assert res(run(**{**base, "eligible_for_publicly_funded_maternity_services": True}),
               "SV-OM-U3.20.35-P1-A") == ["NOT_APPLICABLE"]


@pytest.mark.parametrize("hours,expect", [(25, "SATISFIED"), (26, "NOT_SATISFIED")])
def test_work_boundary(hours, expect):
    a = run(**{**COMPLETE, "work_permitted_by_visa_conditions": True, "visa_condition_max_work_hours_per_week": 25,
               "recorded_work_hours_per_week": hours, "activity_date": "2026-11-03"})
    assert res(a, "SV-WORK-001") == [expect]


def test_work_not_triggered_without_activity():
    assert res(run(**COMPLETE), "SV-WORK-001") == []


def test_under_10():
    assert res(run(**{**COMPLETE, "applicant_age": 8, "lives_with_parent_or_legal_guardian": False}),
               "SV-U10-001") == ["NOT_SATISFIED"]
    assert res(run(**{**COMPLETE, "applicant_age": 8}), "SV-U10-001") == ["UNKNOWN"]


def test_minor_with_verified_consent_not_applicable():
    a = run(**{**COMPLETE, "applicant_age": 16, "parental_consent_established": True})
    assert res(a, "SV-MIN-001") == ["NOT_APPLICABLE"]


def test_provider_suspected_breach_routes_to_human():
    a = run(**{**COMPLETE, "provider_suspected_visa_condition_breach": True})
    assert res(a, "SV-EDU-009") == ["HUMAN_REVIEW_REQUIRED"]


