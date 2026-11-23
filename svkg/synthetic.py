"""Synthetic applications for TC-01..TC-10 (decision 4: synthetic data only).

Shape: application header + F.6 evidence items + F.7 facts. A fact with
verification_status VERIFIED is a VerifiedFact; anything else is only an
ObservedFact. Fact attributes are addressed by the checks as application.<attribute>.
"""
from __future__ import annotations

import copy

CANDIDATE_LABEL = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"


class AppBuilder:
    def __init__(self, tc: str, **header):
        n = tc.replace("-", "")
        self.n = n
        self.app = {
            "application_id": f"POC-SV-APP-{n}", "product_id": "STUDENT-VISA", "synthetic": True,
            "assessment_as_at": "2026-11-17", "channel": "paper",
            "applicant": {"applicant_id": f"APPLICANT-POC-{n}", "synthetic_name": f"Synthetic Applicant {n}"},
            "evidence_items": [], "facts": [], **header,
        }
        self._e = self._f = 0

    def evidence(self, etype: str, verified=True, ref=None) -> str:
        self._e += 1
        eid = f"EVID-{self.n}-{self._e:03d}"
        self.app["evidence_items"].append({
            "evidence_item_id": eid, "application_id": self.app["application_id"], "evidence_type": etype,
            "document_reference": ref or f"SYNTHETIC-{etype.upper()}-{self._e:03d}",
            "supplied_by_actor_id": "ACTOR-APPLICANT", "received_at": "2026-11-10T09:00:00+13:00",
            "verification_status": "VERIFIED" if verified else "UNVERIFIED", "supports_fact_ids": []})
        return eid

    def fact(self, attribute, value, data_type=None, evidence=(), verified=True):
        self._f += 1
        fid = f"FACT-{self.n}-{self._f:03d}"
        dt = data_type or {bool: "boolean", int: "integer", float: "number", str: "string"}[type(value)]
        self.app["facts"].append({
            "fact_id": fid, "application_id": self.app["application_id"],
            "subject_id": self.app["applicant"]["applicant_id"], "attribute": attribute, "value": value,
            "data_type": dt, "evidence_item_ids": list(evidence),
            "verification_status": "VERIFIED" if verified else "UNVERIFIED",
            "verified_at": "2026-11-12T10:00:00+13:00" if verified else None,
            "verified_by": "SYNTHETIC-VERIFIER" if verified else None})
        for e in evidence:
            for item in self.app["evidence_items"]:
                if item["evidence_item_id"] == e:
                    item["supports_fact_ids"].append(fid)
        return fid

    def drop(self, attribute):
        self.app["facts"] = [f for f in self.app["facts"] if f["attribute"] != attribute]


def base(tc: str, *, age=22, student_type="tertiary", identity=True, medical=True,
         funds=25000, months=12) -> AppBuilder:
    b = AppBuilder(tc)
    form = b.evidence("ApplicationForm")
    b.fact("approved_form_used", True, evidence=[form])
    b.fact("persons_in_application", 1, evidence=[form])
    b.fact("completed_in_english", True, evidence=[form])
    b.fact("application_signed", True, evidence=[form])
    if identity:
        pp = b.evidence("Passport")
        b.fact("identity_document_provided", True, evidence=[pp])
    ph = b.evidence("Photograph")
    b.fact("photographs_provided", 2, evidence=[ph])
    pay = b.evidence("PaymentRecord")
    b.fact("prescribed_fee_paid", True, evidence=[pay])
    b.fact("immigration_levy_paid", True, evidence=[pay])
    b.fact("ivl_paid", True, evidence=[pay])
    b.fact("applicant_age", age, evidence=[pp] if identity else [])
    b.fact("applicant_marital_status", "unmarried", evidence=[form])
    offer = b.evidence("OfferOfPlace")
    b.fact("student_type", student_type, evidence=[offer])
    b.fact("study_duration_months", months, evidence=[offer])
    bank = b.evidence("BankStatement")
    b.fact("available_funds_nzd", funds, evidence=[bank])
    b.fact("material_change_occurred", False, evidence=[form])
    if medical:
        med = b.evidence("MedicalCertificate")
        b.fact("medical_certificate_received", True, evidence=[med])
    return b


def synthetic_applications() -> dict[str, dict]:
    apps = {}

    b = base("TC-01")
    apps["TC-01"] = b.app

    b = base("TC-02", identity=False)
    b.app["scenario"] = "Passport details missing."
    apps["TC-02"] = b.app

    b = base("TC-03", age=16, student_type="school", funds=18000)
    b.app["scenario"] = "Applicant under 18; parental consent evidence missing."
    apps["TC-03"] = b.app

    b = base("TC-04")
    b.drop("ivl_paid")
    sch = b.evidence("ScholarshipLetter")
    b.fact("scholarship_administrator", "MFAT", evidence=[sch])
    b.app["scenario"] = "MFAT-administered scholarship Student Visa applicant."
    apps["TC-04"] = b.app

    b = base("TC-05")
    vc = b.evidence("VisaConditionRecord")
    b.fact("work_permitted_by_visa_conditions", True, evidence=[vc])
    b.fact("visa_condition_max_work_hours_per_week", 25, evidence=[vc])
    ts = b.evidence("Timesheet")
    b.fact("recorded_work_hours_per_week", 20, evidence=[ts])
    b.fact("activity_date", "2026-11-03", data_type="date", evidence=[ts])
    b.app["scenario"] = "Visa conditions permit work for 25 hours; recorded activity is 20 hours."
    apps["TC-05"] = b.app

    b = base("TC-06", funds=21000)
    b.app["scenario"] = "Candidate maintenance amount supplied, but Immigration Instruction provenance is absent."
    apps["TC-06"] = b.app

    b = base("TC-07")
    b.app["scenario"] = "Medical certificate supplied."
    apps["TC-07"] = b.app

    b = base("TC-08")
    b.app["scenario"] = "All deterministic checks pass, but visa grant decision remains."
    apps["TC-08"] = b.app

    b = base("TC-09")
    rep = b.evidence("ProviderComplianceRecord")
    b.fact("provider_known_visa_condition_breach", True, evidence=[rep])
    b.fact("provider_reported_breach_to_inz", False, evidence=[rep])
    b.app["scenario"] = "Education provider knows of a visa-condition breach and has not reported it."
    apps["TC-09"] = b.app

    b = base("TC-10")
    b.app["scenario"] = "Applicant asks why the rule applied."
    apps["TC-10"] = b.app

    apps["TC-01"]["scenario"] = "Complete application; identity verified; required fields and payments present."
    return copy.deepcopy(apps)


def expected_results() -> dict[str, dict]:
    """Machine-checkable expectations. 'accepted' lists every outcome the document allows."""
    return {
        "TC-01": {"check_id": "SV-CHECK-APP-001", "rule_id": None, "expected_status": "SATISFIED",
                  "assert": [{"check_id": "SV-CHECK-APP-001", "accepted": ["SATISFIED"]},
                             {"check_id": "SV-CHECK-DISC-001", "accepted": ["DISCRETION_REQUIRED"]}]},
        "TC-02": {"check_id": "SV-CHECK-APP-001", "rule_id": "SV-ID-001", "expected_status": "UNKNOWN",
                  "assert": [{"check_id": "SV-CHECK-APP-001", "rule_id": "SV-ID-001", "accepted": ["UNKNOWN"]},
                             {"check_id": "SV-CHECK-APP-001", "accepted": ["UNKNOWN"]},
                             {"missing_information_includes": "application.identity_document_provided"},
                             {"no_result_for_rule": "SV-ID-001", "forbidden": ["NOT_SATISFIED"]}]},
        "TC-03": {"check_id": "SV-CHECK-MIN-001", "rule_id": "SV-MIN-001", "expected_status": "DISCRETION_REQUIRED",
                  "assert": [{"check_id": "SV-CHECK-MIN-001",
                              "accepted": ["HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED"]},
                             {"human_review_includes": "SV-MIN-001"}]},
        "TC-04": {"check_id": "SV-CHECK-IVL-001", "rule_id": "SV-FEE-003", "expected_status": "NOT_APPLICABLE",
                  "assert": [{"check_id": "SV-CHECK-IVL-001", "accepted": ["NOT_APPLICABLE"],
                              "exception_rule_id": "SV-IVL-001"}]},
        "TC-05": {"check_id": "SV-CHECK-WORK-001", "rule_id": "SV-WORK-001", "expected_status": "SATISFIED",
                  "assert": [{"check_id": "SV-CHECK-WORK-001", "accepted": ["SATISFIED"]}]},
        "TC-06": {"check_id": "SV-CHECK-FIN-001", "rule_id": "SV-OM-U3.20.30-A-I",
                  "expected_status": "SATISFIED",
                  "deviation": "Document expects SOURCE_COVERAGE_GAP because Immigration Instruction provenance was "
                               "absent. The Operational Manual is now loaded (IF-25), so the check applies U3.20.30 "
                               "with VERIFIED_INSTRUCTION_SOURCE provenance and no candidate label (IF-30, "
                               "superseding decision 3 / IF-01).",
                  "interpretation_flag_ids": ["IF-30"],
                  "assert": [{"check_id": "SV-CHECK-FIN-001", "accepted": ["SATISFIED"],
                              "rule_id": "SV-OM-U3.20.30-A-I", "provenance_status": "VERIFIED_INSTRUCTION_SOURCE"},
                             {"production_decision": False}]},
        "TC-07": {"check_id": "SV-CHECK-HEALTH-001", "rule_id": "SV-OM-U3.1-B-III",
                  "expected_status": "HUMAN_REVIEW_REQUIRED",
                  "assert": [{"check_id": "SV-CHECK-HEALTH-001", "accepted": ["HUMAN_REVIEW_REQUIRED"]},
                             {"evidence_used_type": "MedicalCertificate", "check_id": "SV-CHECK-HEALTH-001"}]},
        "TC-08": {"check_id": "SV-CHECK-DISC-001", "rule_id": "SV-DEC-002", "expected_status": "DISCRETION_REQUIRED",
                  "assert": [{"check_id": "SV-CHECK-DISC-001", "accepted": ["DISCRETION_REQUIRED"]},
                             {"deterministic_all_pass": True}]},
        "TC-09": {"check_id": "SV-CHECK-EDU-009", "rule_id": "SV-EDU-009", "expected_status": "NOT_SATISFIED",
                  "interpretation_flag_ids": ["IF-14"],
                  "assert": [{"check_id": "SV-CHECK-EDU-009", "accepted": ["NOT_SATISFIED"]}]},
        "TC-10": {"check_id": "SV-CHECK-APP-001", "rule_id": "SV-ID-001",
                  "expected_status": "EXPLANATION_CHAIN_COMPLETE",
                  "assert": [{"explanation_chain_for_rule": "SV-ID-001",
                              "chain": ["RuleEvaluation", "DecisionCheck", "Rule", "Provision",
                                        "AuthoritySource"]}]},
    }
