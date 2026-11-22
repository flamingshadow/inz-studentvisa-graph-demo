"""Phase 1: extraction matches the document; checker fails on drift."""
import copy
import json

import pytest

from svkg import check, provref
from svkg.docspec import ROOT
from svkg.extract import APPENDIX_A_FILES, CANDIDATE_LABEL


def test_checker_passes_against_document():
    rep = check.run_checks()
    assert rep.errors == []
    assert len(rep.passed) > 2500


def test_appendix_a_layout_complete():
    for f in APPENDIX_A_FILES:
        assert (ROOT / f).exists(), f


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d["rules"][0].__setitem__("rulespeak_statement", "changed"), "statement"),
    (lambda d: d["rules"].pop(), "verified rule ID set"),
    (lambda d: d["candidate_rules"][5].__setitem__("executable", True), "only decision-3 candidates execute"),
])
def test_checker_detects_drift(monkeypatch, mutate, expect):
    real = check._load

    def fake(rel):
        data = real(rel)
        if rel == "rules/rules.json":
            data = copy.deepcopy(data)
            mutate(data)
        return data
    monkeypatch.setattr(check, "_load", fake)
    rep = check.run_checks()
    assert any(expect in e for e in rep.errors), rep.errors[:5]


def test_rule_12_1_fields_and_provenance():
    rules = json.loads((ROOT / "rules/rules.json").read_text())
    for r in rules["rules"]:
        assert r["product_id"] == "STUDENT-VISA"
        assert r["provenance_status"] == "VERIFIED_PRIMARY_SOURCE"
        assert set(r["authority"]) >= {"regulatory_instrument", "instrument_type", "provision", "source_version"}
        assert set(r["automation"]) >= {"class", "machine_evaluable", "missing_result"}
        assert r["automation"]["missing_result"] == "UNKNOWN"
    for c in rules["candidate_rules"]:
        assert c["provenance_status"] == "RECONCILIATION_REQUIRED"


def test_12_1_example_rule_lines_up():
    r = next(x for x in json.loads((ROOT / "rules/rules.json").read_text())["rules"] if x["rule_id"] == "SV-APP-005")
    assert r["classification"] == {"rule_type": "Obligation", "modality": "MUST", "published_type": "OR"}
    assert r["authority"]["provision_id"] == "NZ-IMM-ACT-2009-S58-3-A"          # F.2
    assert r["authority"]["source_version"] == "2026-07-10"                    # 12.1
    assert r["qualifiers"]["condition_ids"] == ["SV-COND-APP-001"]             # 12.1
    assert r["automation"]["class"] == "A2"


def test_decision3_thresholds_labelled():
    ths = {t["id"]: t for t in json.loads((ROOT / "rules/thresholds.json").read_text())["thresholds"]}
    expect = {"SV-TH-CAND-TERTIARY-YEAR": 20000, "SV-TH-CAND-TERTIARY-MONTH": 1667,
              "SV-TH-CAND-SCHOOL-YEAR": 17000, "SV-TH-CAND-SCHOOL-MONTH": 1417, "SV-TH-CAND-MATERNITY-TOTAL": 9000}
    for tid, v in expect.items():
        assert ths[tid]["value"] == v
        assert ths[tid]["label"] == CANDIDATE_LABEL
        assert ths[tid]["provenance_status"] == "RECONCILIATION_REQUIRED"
        assert ths[tid]["executable"] is True
    assert ths["SV-TH-CAND-WORK-HOURS"]["executable"] is False


def test_deviation_recorded():
    flags = {f["flag_id"]: f for f in json.loads((ROOT / "governance/interpretation-flags.json").read_text())["interpretation_flags"]}
    assert flags["IF-01"]["kind"] == "DEVIATION" and "TC-06" in flags["IF-01"]["applies_to"]


@pytest.mark.parametrize("ref,ids", [
    ("s 58(3)(a)", ["X-S58-3-A"]),
    ("s 60(1),(3)", ["X-S60-1", "X-S60-3"]),
    ("s 15(1)(d)-(f), s 17", ["X-S15-1-D", "X-S15-1-E", "X-S15-1-F", "X-S17"]),
    ("r 23AA(4); r 23A(3)", ["X-R23AA-4", "X-R23A-3"]),
    ("cls 53(1)(b), 54", ["X-CL53-1-B", "X-CL54"]),
    ("s 22, IPP 3A", ["X-S22", "X-S22-IPP3A"]),
    ("ss 114-115", ["X-S114", "X-S115"]),
])
def test_provision_reference_parser(ref, ids):
    assert [p.provision_id for p in provref.parse(ref, "X")] == ids


def test_provision_parent_chain():
    p = provref.parse("s 58(3)(a)", "NZ-IMM-ACT-2009")[0]
    assert [x.provision_id for x in p.lineage()] == ["NZ-IMM-ACT-2009-S58", "NZ-IMM-ACT-2009-S58-3",
                                                     "NZ-IMM-ACT-2009-S58-3-A"]
