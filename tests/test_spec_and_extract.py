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
    (lambda d: d["candidate_rules"][5].__setitem__("executable", True), "no candidate executes"),
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
        assert c["provenance_status"] in ("RECONCILIATION_REQUIRED", "SUPERSEDED_BY_INSTRUCTION")
        assert c["reconciliation"]["status"] in ("RECONCILED", "PARTLY_RECONCILED")
    for r in rules["instruction_rules"]:
        assert r["provenance_status"] == "VERIFIED_INSTRUCTION_SOURCE" and r["instruction_statement"]
        assert r["automation"]["class"] in ("A1", "A2", "A3", "A4") and r["automation"]["class_basis"]


def test_12_1_example_rule_lines_up():
    r = next(x for x in json.loads((ROOT / "rules/rules.json").read_text())["rules"] if x["rule_id"] == "SV-APP-005")
    assert r["classification"] == {"rule_type": "Obligation", "modality": "MUST", "published_type": "OR"}
    assert r["authority"]["provision_id"] == "NZ-IMM-ACT-2009-S58-3-A"          # F.2
    assert r["authority"]["source_version"] == "2026-07-10"                    # 12.1
    assert r["qualifiers"]["condition_ids"] == ["SV-COND-APP-001"]             # 12.1
    assert r["automation"]["class"] == "A2"


def test_funds_thresholds_from_instructions():
    """Decision 3's candidate figures are kept but superseded; U3.20 figures now execute (IF-30, IF-31)."""
    ths = {t["id"]: t for t in json.loads((ROOT / "rules/thresholds.json").read_text())["thresholds"]}
    for tid, v in {"SV-TH-CAND-TERTIARY-YEAR": 20000, "SV-TH-CAND-TERTIARY-MONTH": 1667,
                   "SV-TH-CAND-SCHOOL-YEAR": 17000, "SV-TH-CAND-SCHOOL-MONTH": 1417,
                   "SV-TH-CAND-MATERNITY-TOTAL": 9000}.items():
        assert ths[tid]["value"] == v and not ths[tid]["executable"]
        assert ths[tid]["provenance_status"] == "SUPERSEDED_BY_INSTRUCTION" and ths[tid]["label"] is None
    execd = {(t["rule_id"], t["value"]) for t in ths.values() if t.get("executable")}
    assert execd == {("SV-OM-U3.20.25-A-I", 1667), ("SV-OM-U3.20.25-A-II", 1417), ("SV-OM-U3.20.30-A-I", 20000),
                     ("SV-OM-U3.20.30-A-II", 17000), ("SV-OM-U3.20.35-P1-A", 9000)}
    assert all(ths[t]["provenance_status"] == "VERIFIED_INSTRUCTION_SOURCE" for t in ths if ths[t].get("executable"))


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


def test_flag_review_recorded():
    flags = json.loads((ROOT / "governance/interpretation-flags.json").read_text())["interpretation_flags"]
    open_ = {f["flag_id"] for f in flags if f["status"] != "ACCEPTED_BY_PRODUCT_OWNER"}
    assert not open_, open_   # Operational Manual flags reviewed one by one on 23 Nov 2026
    by_id = {f["flag_id"]: f for f in flags}
    assert by_id["IF-25"]["kind"] == "DEVIATION" and by_id["IF-25"]["status"] == "ACCEPTED_BY_PRODUCT_OWNER"
    assert by_id["IF-01"]["superseded_by"] == "IF-30" and by_id["IF-15"]["superseded_by"] == "IF-30"
    assert "NZD 30,000" in by_id["IF-15"]["resolution"]
    assert by_id["IF-01"]["kind"] == "DEVIATION"


def test_if26_if32_decisions_applied():
    ir = {r["rule_id"]: r for r in json.loads((ROOT / "rules/rules.json").read_text())["instruction_rules"]}
    for rid in ("SV-OM-U3.20.30-A-I", "SV-OM-U3.20.30-A-II", "SV-OM-U3.20.25-A-I"):
        assert ir[rid]["automation"]["class"] == "A2"
    assert ir["SV-OM-U3.20.30-A-I"]["automation"]["class_basis"].startswith("set by SV-CHECK-FIN-001")
    sections = {r["instruction_code"].split(".")[0] for r in ir.values()}
    assert not sections & {"S1", "V2", "V3", "W2", "H2", "H5", "A6", "E2", "E3", "E11"}
    assert sum(1 for r in ir.values() if r["actor_ids"]) / len(ir) > 0.6
