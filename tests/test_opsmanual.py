"""Operational Manual integration (IF-25..IF-33)."""
import json

import pytest

from svkg import opsmanual, queries
from svkg.docspec import ROOT

DATA = opsmanual.load()
RULES = {r["rule_id"]: r for r in json.loads((ROOT / "rules/rules.json").read_text())["instruction_rules"]}
PROVS = {p["provision_id"]: p for p in json.loads((ROOT / "regulatory/provisions.json").read_text())["provisions"]}


def test_scope_loaded():
    kinds = {(p["kind"], p["version_kind"]) for p in DATA["pages"]}
    assert ("STUDENT", "CURRENT") in kinds and ("STUDENT", "HISTORIC") in kinds and ("REFERENCE", "CURRENT") in kinds
    codes = {p["code"] for p in DATA["pages"]}
    assert {f"U{i}" for i in range(1, 17)} <= codes
    assert {"A4", "A5", "E5.1", "E6.5"} <= codes


def test_exact_text_and_effective_date():
    p = PROVS["NZ-INZ-OM-U3.20.30"]
    assert "(i) they have NZ$20,000.00 per year available to maintain themselves" in p["source_text"]
    assert p["effective_from"] == "2022-09-26" and p["source_url"].endswith("#43653.htm")
    assert not any(k.startswith("NZ-INZ-OM-U3.20.30-") for k in PROVS)      # no paragraph duplicates (IF-35)
    assert RULES["SV-OM-U3.20.30-A-I"]["authority"]["provision_id"] == "NZ-INZ-OM-U3.20.30"


def test_atomic_rule_composition():
    r = RULES["SV-OM-U3.20.30-A-I"]
    assert r["instruction_statement"].startswith("Students taking programmes of study lasting 36 weeks or longer "
                                                 "must provide evidence")
    assert r["instruction_statement"].endswith("tertiary or non-compulsory education study")
    assert r["classification"]["modality"] == "MUST" and r["authority"]["provision"] == "U3.20.30(a)(i)"


def test_identical_versions_merged():
    hist = [p for p in PROVS.values() if p["source_id"] == "NZ-INZ-IMMIGRATION-INSTRUCTIONS"
            and p.get("merged_identical_versions")]
    assert hist, "expected some identical historic versions to be merged"
    for p in hist:
        assert p["effective_from"] <= min(p["merged_identical_versions"])


def test_version_chain_has_no_overlaps():
    hist = sorted((p for p in DATA["pages"] if p["code"] == "U3.20"), key=lambda p: p["effective_from"])
    for older, newer in zip(hist, hist[1:]):
        assert older["effective_to"] == newer["effective_from"]
    assert hist[-1]["version_kind"] == "CURRENT" and hist[-1]["effective_to"] is None


def test_candidates_reconciled():
    rules = json.loads((ROOT / "rules/rules.json").read_text())
    by = {c["rule_id"]: c for c in rules["candidate_rules"]}
    assert by["SV-CAND-MAINT-TERTIARY"]["reconciliation"]["instruction_codes"] == ["U3.20.25", "U3.20.30"]
    assert by["SV-CAND-FAMILY"]["provenance_status"] == "RECONCILIATION_REQUIRED"


@pytest.mark.neo4j
def test_instruction_as_at(live):
    r = queries.instruction_text("U3.20.30", "2015-06-01")
    assert (r["provision_id"], r["effective_from"], r["effective_to"]) == \
        ("NZ-INZ-OM-U3.20.30@2013-12-02", "2013-12-02", "2021-02-11")     # text unchanged 2013-2021 (IF-35)
    assert queries.instruction_text("U3.20", "2015-06-01")["effective_from"] == "2011-07-25"
    cur = queries.instruction_text("U3.20", None)
    assert cur["page_effective_date"] == "2022-09-26" and cur["text_in_force_from"] == "2011-07-25"
    assert len(cur["sub_instructions"]) == 7
    assert any(x["ref"] == "E6.5" for x in cur["references"])


@pytest.mark.neo4j
def test_instruction_rules_and_history(live):
    assert len(queries.rules_by_instruction("U3.20")) >= 25
    hist = queries.instruction_history("U3.20.30")
    assert len(hist) >= 8 and hist[0]["version_kind"] == "CURRENT"
    assert all(a["effective_from"] == b["effective_to"] for a, b in zip(hist, hist[1:]))


@pytest.mark.neo4j
def test_see_also_links_to_act(live):
    rows = queries.read("MATCH (:Provision {id:'NZ-INZ-OM-U3.20'})-[:REFERENCES]->(a:Provision) RETURN a.id AS id, "
                        "a.source_text_status AS s")
    assert {"NZ-IMM-ACT-2009-S48", "NZ-IMM-ACT-2009-S55"} <= {r["id"] for r in rows}
    assert all(r["s"] == "RETRIEVED" for r in rows)
