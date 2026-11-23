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
    a = E.assess(json.loads((ROOT / "testing/applications/SVP-032.json").read_text()))
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




# ---- SV-CHECK-LODG-001: Operational Manual E4 lodgement requirements (IF-36) ----------------
def test_lodgement_not_triggered_without_route():
    assert res(run(**COMPLETE), "SV-OM-E4.50.1-P1-A") == []


def test_lodgement_paper_satisfied_and_not_satisfied():
    base = {**COMPLETE, "lodgement_route": "paper"}
    a = run(**base)
    for rid in ("SV-OM-E4.50.1-P1-A", "SV-OM-E4.50.1-P1-B", "SV-OM-E4.50.1-P2"):
        assert res(a, rid) == ["SATISFIED"], rid
    b = run(**{**base, "photographs_provided": 1, "application_signed": False})
    assert res(b, "SV-OM-E4.50.1-P1-B") == ["NOT_SATISFIED"]
    assert res(b, "SV-OM-E4.50.1-P2") == ["NOT_SATISFIED"]
    assert check_result(b, "SV-CHECK-LODG-001") == "NOT_SATISFIED"


def test_lodgement_missing_fact_is_unknown_not_failure():
    facts = {k: v for k, v in COMPLETE.items() if k != "photographs_provided"}
    assert res(run(**{**facts, "lodgement_route": "paper"}), "SV-OM-E4.50.1-P1-B") == ["UNKNOWN"]


def test_lodgement_a2_needs_verified_fact():
    a = run(**{**COMPLETE, "lodgement_route": "paper", "application_signed": ("UNVERIFIED", True)})
    assert res(a, "SV-OM-E4.50.1-P2") == ["UNKNOWN"]


def test_lodgement_paper_minor_needs_parent_signature():
    base = {**COMPLETE, "lodgement_route": "paper", "applicant_age": 16}
    assert res(run(**base), "SV-OM-E4.50.1-P2") == ["UNKNOWN"]
    assert res(run(**{**base, "signed_by_parent_or_guardian": True}), "SV-OM-E4.50.1-P2") == ["SATISFIED"]


ONLINE = dict(lodgement_route="online", full_name_stated=True, date_and_place_of_birth_stated=True,
              passport_details_stated=True, form_requires_photograph=False, declaration_acknowledged=True,
              change_notification_agreed=True)


def test_lodgement_online():
    a = run(**{**COMPLETE, **ONLINE})
    for rid in ("SV-OM-E4.50.2-B-I", "SV-OM-E4.50.2-B-II", "SV-OM-E4.50.2-B-III", "SV-OM-E4.50.2-B-VI",
                "SV-OM-E4.50.2-B-VII"):
        assert res(a, rid) == ["SATISFIED"], rid
    assert res(a, "SV-OM-E4.50.2-B-IV") == ["NOT_APPLICABLE"]
    assert res(a, "SV-OM-E4.50.1-P1-A") == []
    b = run(**{**COMPLETE, **ONLINE, "declaration_acknowledged": False})
    assert res(b, "SV-OM-E4.50.2-B-VI") == ["NOT_SATISFIED"]


ICA = dict(lodgement_route="ica")


def test_lodgement_ica_adult():
    a = run(**{**COMPLETE, **ICA})
    assert res(a, "SV-OM-E4.50.35-A-III") == ["SATISFIED"]
    assert res(a, "SV-OM-E4.50.35-A-IV") == ["SATISFIED"]
    assert res(a, "SV-OM-E4.50.35-B-I") == ["NOT_APPLICABLE"]
    assert res(a, "SV-OM-E4.50.35-B-II") == ["NOT_APPLICABLE"]
    b = run(**{**COMPLETE, **ICA, "persons_in_application": 2})
    assert res(b, "SV-OM-E4.50.35-A-III") == ["NOT_SATISFIED"]


def test_lodgement_ica_minor_accompanied_and_not():
    base = {**COMPLETE, **ICA, "applicant_age": 15}
    acc = run(**{**base, "accompanied_by_parent_or_guardian": True, "signed_by_parent_or_guardian": True})
    assert res(acc, "SV-OM-E4.50.35-A-IV") == ["NOT_APPLICABLE"]
    assert res(acc, "SV-OM-E4.50.35-B-I") == ["SATISFIED"]
    assert res(acc, "SV-OM-E4.50.35-B-II") == ["NOT_APPLICABLE"]
    alone = run(**{**base, "accompanied_by_parent_or_guardian": False})
    assert res(alone, "SV-OM-E4.50.35-B-I") == ["NOT_APPLICABLE"]
    assert res(alone, "SV-OM-E4.50.35-B-II") == ["SATISFIED"]


def test_lodgement_unknown_route():
    assert res(run(**{**COMPLETE, "lodgement_route": "fax"}), "SV-OM-E4.50.1-P1-A") == ["UNKNOWN"]


# ---- Domain checks (IF-37): Family, Pathway, Sponsorship, Providers, Work rights --------------
def _dom(a, check_id):
    return [e for e in a["rule_evaluations"] if e["check_id"] == check_id]


def test_domain_checks_not_triggered_without_trigger_facts():
    a = run(**COMPLETE)
    for cid in ("SV-CHECK-FAM-001", "SV-CHECK-PATH-001", "SV-CHECK-SPON-001", "SV-CHECK-PROV-001",
                "SV-CHECK-WRK-001"):
        assert _dom(a, cid) == [], cid


def test_domain_check_rules_match_domains_and_executors_exist():
    from svkg import om_extract
    t = E._dom_tests()
    for cid, spec in om_extract.DOMAIN_CHECKS.items():
        c = E.store.check(cid)
        assert set(c["executable_rule_ids"]) <= set(t)
        assert set(spec["executes"]) == set(t) & set(spec["executes"])
        assert all(E.store.rule(r)["domain"] == spec["domain"] for r in c["evaluates_rule_ids"]), cid
        assert all(E.store.rule(r)["automation"]["class"] in ("A1", "A2") for r in c["executable_rule_ids"]), cid


def test_domain_every_rule_is_accounted_for():
    a = run(**{**COMPLETE, "pathway_programme_count": 2})
    ev = _dom(a, "SV-CHECK-PATH-001")
    c = E.store.check("SV-CHECK-PATH-001")
    seen = {e["rule_id"] for e in ev if e["result"] in ("SATISFIED", "NOT_SATISFIED", "NOT_APPLICABLE", "UNKNOWN")}
    seen |= {r for e in ev for r in e.get("instruction_rules_presented", [])}
    assert seen == set(c["evaluates_rule_ids"])
    assert {e["result"] for e in ev} >= {"HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED"}


def test_pathway_limits():
    base = {**COMPLETE, "pathway_programme_count": 3, "pathway_visa_duration_months": 60,
            "max_break_between_programmes_weeks": 16, "english_language_study_weeks": 30,
            "provider_visa_approval_rate_percent": 90}
    a = run(**base)
    for rid in ("SV-OM-U14-A", "SV-OM-U14.10-A", "SV-OM-U14.20.5-C", "SV-OM-U14.20.5-D", "SV-OM-U14.20.1-P1-D"):
        assert res(a, rid) == ["SATISFIED"], rid
    b = run(**{**base, "pathway_programme_count": 4, "max_break_between_programmes_weeks": 17,
               "english_language_study_weeks": 31, "provider_visa_approval_rate_percent": 89})
    for rid in ("SV-OM-U14-A", "SV-OM-U14.20.5-C", "SV-OM-U14.20.5-D", "SV-OM-U14.20.1-P1-D"):
        assert res(b, rid) == ["NOT_SATISFIED"], rid


def test_pathway_missing_fact_is_unknown():
    assert res(run(**{**COMPLETE, "pathway_programme_count": 2}), "SV-OM-U14.20.5-C") == ["UNKNOWN"]


def test_family_income_threshold():
    base = {**COMPLETE, "family_basis": "dependent_child", "supporting_work_visa_type": "Accredited Employer"}
    assert res(run(**{**base, "supporting_parent_gross_income_nzd": 58240}), "SV-OM-U8.20.6-A") == ["SATISFIED"]
    assert res(run(**{**base, "supporting_parent_gross_income_nzd": 58239}), "SV-OM-U8.20.6-A") == ["NOT_SATISFIED"]
    assert res(run(**{**base, "supporting_parent_gross_income_nzd": 58239}), "SV-OM-U8.20.10-B-I") == ["NOT_APPLICABLE"]
    rel = {**base, "supporting_work_visa_type": "religious worker", "supporting_parent_gross_income_nzd": 60000}
    assert res(run(**rel), "SV-OM-U8.20.10-B-I") == ["SATISFIED"]


def test_family_nz_parent_visa_duration():
    base = {**COMPLETE, "family_basis": "nz_citizen_parent", "visa_duration_months": 12}
    a = run(**{**base, "applicant_in_new_zealand": True})
    assert res(a, "SV-OM-U8.30-B-II") == ["SATISFIED"] and res(a, "SV-OM-U8.30-B-I") == ["NOT_APPLICABLE"]
    b = run(**{**base, "applicant_in_new_zealand": False, "visa_duration_months": 13})
    assert res(b, "SV-OM-U8.30-B-I") == ["NOT_SATISFIED"]


def test_sponsorship_third_party():
    base = {**COMPLETE, "third_party_type": "person", "third_party_relationship": "friend"}
    assert res(run(**base), "SV-OM-U3.20.10-B") == ["SATISFIED"]
    assert res(run(**{**base, "third_party_relationship": "employer"}), "SV-OM-U3.20.10-B") == ["NOT_SATISFIED"]
    assert res(run(**base), "SV-OM-U3.20.10-C") == ["NOT_APPLICABLE"]
    org = {**COMPLETE, "third_party_type": "organisation", "authorised_contact_nominated": False}
    assert res(run(**org), "SV-OM-U3.20.10-C") == ["NOT_SATISFIED"]
    assert res(run(**org), "SV-OM-U3.20.10-B") == ["NOT_APPLICABLE"]


def test_providers():
    base = {**COMPLETE, "provider_eer_category": 2, "provider_signatory_to_code": True,
            "provider_certified_programme_compliance": True}
    a = run(**base)
    assert res(a, "SV-OM-U5.1-B") == ["SATISFIED"] and res(a, "SV-OM-U5.1-D") == ["SATISFIED"]
    b = run(**{**base, "provider_eer_category": 4, "provider_signatory_to_code": False})
    assert res(b, "SV-OM-U5.1-D") == ["NOT_SATISFIED"] and res(b, "SV-OM-U5.1-B") == ["NOT_SATISFIED"]


def test_work_rights_minor_consent():
    base = {**COMPLETE, "work_conditions_requested": True, "applicant_age": 17, "enrolled_at_tertiary_or_pte": True,
            "provider_written_permission": True, "parental_written_consent": True}
    assert res(run(**base), "SV-OM-U13.15-B") == ["SATISFIED"]
    assert res(run(**{**base, "parental_written_consent": False}), "SV-OM-U13.15-B") == ["NOT_SATISFIED"]
    assert res(run(**{**base, "applicant_age": 19}), "SV-OM-U13.15-B") == ["NOT_APPLICABLE"]


# ---- Residual domain checks (IF-38): no rule is left without a check ---------------------------
def test_every_rule_has_a_check():
    checks = E.store.checks.values()
    covered = set()
    for c in checks:
        for k in ("evaluates_rule_id", "evaluates_rule_id_online", "evaluates_rule_id_school", "exception_rule_id"):
            if c.get(k):
                covered.add(c[k])
        covered |= set(c.get("evaluates_rule_ids", [])) | set(c.get("evaluates_rule_ids_online", []))
    assert [r for r in E.store.rules if r not in covered] == []


def test_residual_checks_do_not_run_without_trigger():
    a = run(**COMPLETE)
    assert [e for e in a["rule_evaluations"] if e["check_id"].endswith("-RES")] == []


def test_residual_domain_presents_every_rule_once():
    a = run(**{**COMPLETE, "review_character": True})
    c = E.store.check("SV-CHECK-CHARACTER-RES")
    ev = [e for e in a["rule_evaluations"] if e["check_id"] == c["check_id"]]
    seen = {e["rule_id"] for e in ev if not e.get("instruction_rules_presented") and not e.get("rules_presented")}
    seen |= {r for e in ev for r in (e.get("instruction_rules_presented") or []) + (e.get("rules_presented") or [])}
    assert seen == set(c["evaluates_rule_ids"])
    assert "HUMAN_REVIEW_REQUIRED" in {e["result"] for e in ev}


def test_review_all_domains_triggers_every_residual_check():
    a = run(**{**COMPLETE, "review_all_domains": True})
    ran = {e["check_id"] for e in a["rule_evaluations"] if e["check_id"].endswith("-RES")}
    assert ran == {cid for cid, c in E.store.checks.items() if c.get("residual")}


def test_residual_candidates_return_coverage_gap():
    a = run(**{**COMPLETE, "review_all_domains": True})
    gaps = [e for e in a["rule_evaluations"] if e["result"] == "SOURCE_COVERAGE_GAP" and e.get("rules_presented")]
    assert gaps and all(e["check_id"].endswith("-RES") for e in gaps)


def test_residual_executed_rules():
    base = {**COMPLETE, "review_all_domains": True}
    assert res(run(**{**base, "dependent_child_included": True, "child_age": 19}), "SV-FAM-002") == ["SATISFIED"]
    assert res(run(**{**base, "dependent_child_included": True, "child_age": 20}), "SV-FAM-002") == ["NOT_SATISFIED"]
    assert res(run(**{**base, "dependent_child_included": False}), "SV-FAM-002") == ["NOT_APPLICABLE"]
    assert res(run(**{**base, "provider_designated_staff_for_under_18s": False, "applicant_age": 16}),
               "SV-U18-003") == ["NOT_SATISFIED"]
    assert res(run(**{**base, "part_time_study_months": 9}), "SV-OM-U7.20.1-A") == ["SATISFIED"]
    assert res(run(**{**base, "part_time_study_months": 10}), "SV-OM-U7.20.1-B") == ["NOT_SATISFIED"]
