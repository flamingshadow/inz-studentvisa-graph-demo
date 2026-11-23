"""Phase 2: exact provision wording from legislation.govt.nz (decision 8)."""
import json

import pytest

from svkg import legislation, queries
from svkg.docspec import ROOT

PROVS = {p["provision_id"]: p for p in json.loads((ROOT / "regulatory/provisions.json").read_text())["provisions"]}
CODE = "NZ-EDU-PASTORAL-CODE-2021"


def test_s58_3_a_wording():
    p = PROVS["NZ-IMM-ACT-2009-S58-3-A"]
    assert p["source_text_status"] == "RETRIEVED"
    assert "may affect the decision on the application" in p["source_text"]
    assert p["source_url"].startswith("https://www.legislation.govt.nz/act/public/2009/51/")
    assert p["source_version"] == "2026-07-10" and p["retrieved_at"]


def test_parent_subsection_contains_child_wording():
    child = PROVS["NZ-IMM-ACT-2009-S58-3-A"]["source_text"]
    assert child in PROVS["NZ-IMM-ACT-2009-S58-3"]["source_text"]
    assert PROVS["NZ-IMM-ACT-2009-S58-3"]["source_text"].startswith("(3) It is also the responsibility")


def test_ipp1_wording():
    t = PROVS["NZ-PRIVACY-ACT-2020-S22-IPP1"]["source_text"]
    body = t.split("(1)", 1)[1].strip()
    assert body.startswith("Personal information must not be collected")
    assert PROVS["NZ-PRIVACY-ACT-2020-S22-IPP3A"]["source_text_status"] == "RETRIEVED"


def test_schedule_duplicates_ignored():
    # r 10 and s 15/16/17/48 also appear in schedules; the body provision must be used
    assert PROVS["NZ-IMM-VEPRM-REGS-2010-R10-2-A"]["source_text"].startswith("(a)")
    assert "Despite sections 15 and 16, a visa and entry permission may be granted" in PROVS["NZ-IMM-ACT-2009-S17"]["source_text"]


def test_mfat_ivl_exemption_wording():
    t = PROVS["NZ-IMM-VEPRM-REGS-2010-R26AAE-5-C"]["source_text"]
    assert "scholarship administered by the Ministry of Foreign Affairs and Trade" in t


def test_code_clauses_not_retrieved():
    code = [p for p in PROVS.values() if p["source_id"] == CODE]
    assert code and all(p["source_text_status"] == "SOURCE_TEXT_NOT_RETRIEVED" and p["source_text"] is None
                        for p in code)
    assert all("not published on legislation.govt.nz" in p["source_text_reason"].lower() for p in code)


def test_every_other_provision_retrieved_with_provenance():
    for p in PROVS.values():
        if p["source_id"] not in (CODE, "NZ-INZ-IMMIGRATION-INSTRUCTIONS"):
            assert p["source_text_status"] == "RETRIEVED", p["provision_id"]
            assert p["source_text"] and p["source_url"] and p["source_version"] and p["html_sha256"]


def test_text_is_clean():
    for p in PROVS.values():
        t = p["source_text"] or ""
        assert "﻿" not in t and "  " not in t and " ," not in t and "Compare:" not in t


def test_version_parsing():
    assert legislation.version_date('<p class="reprint-date">Version as at 10 July 2026</p>')[0] == "2026-07-10"
    assert legislation.version_date('<p class="reprint-date">Reprint as at 30 September 2020</p>')[0] == "2020-09-30"


def test_rule_source_versions_follow_instrument():
    rules = {r["rule_id"]: r for r in json.loads((ROOT / "rules/rules.json").read_text())["rules"]}
    assert rules["SV-FORM-001"]["authority"]["source_version"] == "2026-10-20"
    assert rules["SV-PRIV-001"]["authority"]["source_version"] == "2026-10-08"
    assert rules["SV-EDU-001"]["authority"]["source_version"] is None


@pytest.mark.neo4j
def test_provision_fulltext_search_finds_phrase(live):
    hits = queries.search("material change in circumstances", 50)["provisions"]
    assert any(h["provision_id"].startswith("NZ-IMM-ACT-2009-S58") for h in hits)
    assert all(h["source_text_status"] == "RETRIEVED" for h in hits)


@pytest.mark.neo4j
def test_rule_detail_returns_wording(live):
    p = next(x for x in queries.rule_detail("SV-APP-005")["provisions"] if x["primary"])
    assert p["source_text_status"] == "RETRIEVED" and "may affect the decision" in p["source_text"]
    assert p["source_url"] and p["source_version"] == "2026-07-10"
