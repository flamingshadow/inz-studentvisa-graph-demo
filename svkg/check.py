"""Spec checker: re-read the .docx independently and diff it against the extracted files.

Any mismatch is an ERROR and makes `svkg check` exit non-zero (the build fails).
Known, accepted gaps (e.g. provision text deferred) are reported as WARNINGs.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import yaml

from .docspec import ROOT, Spec


def _load(rel):
    p = ROOT / rel
    return yaml.safe_load(p.read_text()) if rel.endswith(".yaml") else json.loads(p.read_text())


def _n(s):
    return " ".join(str(s).replace("’", "'").split())


class Report:
    def __init__(self):
        self.errors, self.warnings, self.passed = [], [], []

    def check(self, ok, msg):
        (self.passed if ok else self.errors).append(msg)
        return ok

    def eq(self, a, b, msg):
        return self.check(a == b, msg if a == b else f"{msg}: document={a!r} extracted={b!r}")

    def warn(self, msg):
        self.warnings.append(msg)


def run_checks() -> Report:
    spec = Spec()
    rep = Report()

    # 1. Appendix A layout
    listed = spec.bullets("Appendix A")
    for f in listed:
        rep.check((ROOT / f).exists(), f"Appendix A file present: {f}")

    rules_file = _load("rules/rules.json")
    rules = {r["rule_id"]: r for r in rules_file["rules"]}

    # 2. Verified rule catalogue, row by row
    doc_ids = []
    per_section_doc, per_section_ext = Counter(), Counter(r["catalogue_section"] for r in rules.values())
    for t in spec.rule_tables():
        section = (t.subheading or t.heading).split(" ")[0].rstrip(".")
        has_type = t.rows[0][1] == "Type"
        last = None
        for row in t.rows[1:]:
            rid = row[0]
            doc_ids.append(rid)
            per_section_doc[section] += 1
            stmt, instr, prov, auto = (row[2], row[3], row[4], row[5]) if has_type else row[1:5]
            instr = last if instr == "same" else instr
            last = instr
            r = rules.get(rid)
            if not rep.check(r is not None, f"rule {rid} extracted"):
                continue
            rep.eq(_n(stmt), _n(r["rulespeak_statement"]), f"{rid} statement")
            rep.eq(_n(instr), _n(r["authority"]["regulatory_instrument"]), f"{rid} instrument")
            rep.eq(prov, r["authority"]["provision"], f"{rid} provision")
            rep.eq(auto, r["automation"]["class_as_published"], f"{rid} automation")
            if has_type:
                rep.eq(row[1], r["classification"]["published_type"], f"{rid} type")
            rep.eq(section, r["catalogue_section"], f"{rid} section")
            rep.check(bool(r["authority"]["provision_ids"]), f"{rid} has provision IDs")
            rep.check(r["provenance_status"] == "VERIFIED_PRIMARY_SOURCE", f"{rid} provenance status")
            for k in ("rule_id", "product_id", "domain", "classification", "rulespeak_statement", "authority",
                      "qualifiers", "automation", "provenance_status"):
                rep.check(k in r, f"{rid} has 12.1 field {k}")
    rep.eq(len(doc_ids), len(set(doc_ids)), "rule IDs unique in document")
    rep.eq(sorted(doc_ids), sorted(rules), "verified rule ID set")
    for s in sorted(set(per_section_doc) | set(per_section_ext)):
        rep.eq(per_section_doc[s], per_section_ext[s], f"rule count in section {s}")

    # 3. Candidate layer (Section 6) separate, RECONCILIATION_REQUIRED, only decision-3 items execute
    t8 = spec.table_by_header("Domain", "6.")
    cands = rules_file["candidate_rules"]
    rep.eq(len(t8.rows) - 1, len(cands), "Section 6 candidate row count")
    for row, c in zip(t8.rows[1:], cands):
        rep.eq(row[0], c["domain"], f"candidate {c['rule_id']} domain")
        rep.eq(row[3], c["provenance_status_as_published"], f"candidate {c['rule_id']} status")
        rep.check(c["provenance_status"] == "RECONCILIATION_REQUIRED", f"candidate {c['rule_id']} RECONCILIATION_REQUIRED")
        rep.check(c["rule_id"] not in rules, f"candidate {c['rule_id']} kept out of verified catalogue")
    rep.eq({"SV-CAND-MAINT-TERTIARY", "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"},
           {c["rule_id"] for c in cands if c["executable"]}, "only decision-3 candidates execute")
    ths = _load("rules/thresholds.json")["thresholds"]
    label = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"
    money = {t["value"] for t in ths if t.get("unit") == "NZD"}
    rep.eq({20000, 1667, 17000, 1417, 9000}, money, "decision-3 funds figures loaded")
    for t in ths:
        if t.get("unit") == "NZD":
            rep.check(t["label"] == label and t["provenance_status"] == "RECONCILIATION_REQUIRED",
                      f"threshold {t['id']} labelled candidate")
    tE = spec.table_by_header("Candidate area", "Appendix E")
    rep.eq(len(tE.rows) - 1, len(rules_file["candidate_inventory_appendix_e"]), "Appendix E row count")

    # 4. Rule groups (5.2)
    t7 = spec.table_by_header("Rule group", "5.", "5.2")
    rep.eq([r[0] for r in t7.rows[1:]], [g["name"] for g in rules_file["rule_groups"]], "5.2 rule groups")

    # 5. Relationship vocabulary
    vocab = [x.strip() for x in spec.paras("3.", "3.1")[0].text.split(",")]
    rtypes = [r["type"] for r in _load("schema/relationship-types.yaml")["relationship_types"]]
    rep.eq(vocab, rtypes, "relationship vocabulary (3.1)")
    rels = _load("regulatory/relationships.json")["relationships"]
    used = {r["type"] for r in rels}
    rep.check(used <= set(vocab), f"relationships use only 3.1 types (extra: {sorted(used - set(vocab))})")

    # 6. Fact types FT-001..FT-018
    t4 = spec.table_by_header("Fact type ID", "4.")
    fts = {f["fact_type_id"]: f["statement"] for f in _load("regulatory/fact-types.json")["fact_types"]}
    rep.eq([f"FT-{i:03d}" for i in range(1, 19)], [r[0] for r in t4.rows[1:]], "document has FT-001..FT-018")
    rep.eq({r[0]: r[1] for r in t4.rows[1:]}, fts, "fact types")

    # 7. Ontology layers & node types
    t2 = spec.table_by_header("Layer", "3.")
    onto = _load("schema/ontology.yaml")["ontology"]["layers"]
    rep.eq([(r[0], [x.strip() for x in r[1].split(",")]) for r in t2.rows[1:]],
           [(l["layer"], l["node_types"]) for l in onto], "ontology layers / node types")

    # 8. Concepts (Table 3)
    t3 = spec.table_by_header("Concept group", "4.")
    terms = {c["preferred_term"] for c in _load("regulatory/concepts.json")["concepts"]}
    for g, ts in ((r[0], r[1]) for r in t3.rows[1:]):
        for term in [x.strip() for x in ts.split(",")]:
            rep.check(term in terms, f"concept {term!r} ({g})")

    # 9. Closed outcomes
    bullet = next(b for b in spec.bullets("1.") if b.startswith("Closed evaluation outcomes"))
    doc_out = re.findall(r"[A-Z_]{4,}", bullet)
    enums = _load("schema/enumerations.yaml")["enumerations"]
    rep.eq(doc_out, enums["evaluation_outcome"], "closed evaluation outcomes")
    t10 = spec.table_by_header("Automation class", "7.")
    rep.eq([r[0] for r in t10.rows[1:]], list(enums["automation_class"]), "automation classes")

    # 10. Decision checks (Table 11 + F.8)
    t11 = spec.table_by_header("Check ID", "7.")
    checks = {c["check_id"]: c for c in _load("decisioning/decision-checks.json")["decision_checks"]}
    for row in t11.rows[1:]:
        c = checks.get(row[0])
        if rep.check(c is not None, f"decision check {row[0]}"):
            rep.eq(row[3], c.get("doc_routing_logic"), f"{row[0]} routing logic")
    _, f8 = spec.json_template("F.8")
    rep.check(f8["check_id"] in checks, f"F.8 check {f8['check_id']}")
    for cid, c in checks.items():
        if c.get("origin", "").startswith("Implied") or c.get("origin", "").startswith("Rules marked"):
            rep.check(bool(c.get("interpretation_flag_ids")), f"added check {cid} carries an interpretation flag")

    # 11. Lifecycle
    t12 = spec.table_by_header("Entity", "8.")
    trans = _load("decisioning/state-transitions.json")["state_transitions"]
    rep.eq([(r[3]) for r in t12.rows[1:]], [t["trigger"] for t in trans], "lifecycle transitions")
    states = {s["name"] for s in _load("decisioning/states.json")["states"]}
    rep.eq({x for r in t12.rows[1:] for x in (r[1], r[2])}, states, "lifecycle states")

    # 12. Test cases TC-01..TC-10
    t13 = spec.table_by_header("Test", "10.")
    tcs = {t["test_case_id"]: t for t in _load("testing/test-cases.json")["test_cases"]}
    rep.eq([f"TC-{i:02d}" for i in range(1, 11)], [r[0] for r in t13.rows[1:]], "document has TC-01..TC-10")
    apps = {a["application_id"] for a in _load("testing/synthetic-applications.json")["applications"]}
    exp = _load("testing/expected-results.json")["expected_results"]
    for row in t13.rows[1:]:
        t = tcs.get(row[0])
        if rep.check(t is not None, f"test case {row[0]}"):
            rep.eq(row[1], t["scenario"], f"{row[0]} scenario")
            rep.eq(row[2], t["expected_outcome_as_published"], f"{row[0]} expected outcome")
            rep.check(t["application_id"] in apps, f"{row[0]} synthetic application")
            rep.check(row[0] in exp, f"{row[0]} expected results")
            rep.check((ROOT / "testing" / "applications" / f"{row[0]}.json").exists(), f"{row[0]} fixture file")
    rep.check("label" in str(exp["TC-06"]) and exp["TC-06"].get("deviation"), "TC-06 adjusted per decision 3")

    # 13. Agent contract (Section 9) & definition of done (13)
    ac = _load("schema/agent-contract.yaml")["agent_contract"]
    rep.eq(spec.bullets("9."), ac["rules"], "agent contract (Section 9)")
    rep.eq(spec.bullets("13."), ac["definition_of_done"], "definition of done (Section 13)")
    rep.eq(spec.scope_notice(), ac["poc_scope_notice"], "POC scope notice")

    # 14. Gaps & risks (Section 11)
    t14 = spec.table_by_header("ID", "11.")
    cov = _load("governance/source-coverage.json")
    rep.eq([r[0] for r in t14.rows[1:]], [g["id"] for g in cov["coverage_gaps"]] + [r["id"] for r in cov["risks"]],
           "gaps and risks (Section 11)")

    # 15. Referential integrity
    provs = {p["provision_id"]: p for p in _load("regulatory/provisions.json")["provisions"]}
    for r in list(rules.values()):
        for pid in r["authority"]["provision_ids"]:
            rep.check(pid in provs, f"{r['rule_id']} provision {pid} exists")
    known = set(provs) | set(rules) | {c["rule_id"] for c in cands} | set(checks) | set(tcs)
    for f, key, inner in (("regulatory/sources.json", "sources", "source_id"),
                          ("regulatory/concepts.json", "concepts", "concept_id"),
                          ("rules/conditions.json", "conditions", "id"), ("rules/conditions.json", "triggers", "id"),
                          ("rules/exceptions.json", "exceptions", "id"), ("rules/thresholds.json", "thresholds", "id"),
                          ("rules/temporal-constraints.json", "temporal_constraints", "id"),
                          ("rules/discretions.json", "discretions", "id"),
                          ("evidence/evidence-requirements.json", "evidence_requirements", "evidence_requirement_id"),
                          ("decisioning/states.json", "states", "state_id"),
                          ("decisioning/state-transitions.json", "state_transitions", "transition_id"),
                          ("governance/interpretation-flags.json", "interpretation_flags", "flag_id")):
        known |= {x[inner] for x in _load(f)[key]}
    known |= {v["instrument_version_id"] for v in _load("regulatory/sources.json")["instrument_versions"]}
    known |= {g["id"] for g in cov["coverage_gaps"]}
    dangling = [(r["type"], r["from"], r["to"]) for r in rels if r["from"] not in known or r["to"] not in known]
    rep.check(not dangling, f"relationship endpoints resolve ({len(dangling)} dangling: {dangling[:5]})")
    flags = {f["flag_id"] for f in _load("governance/interpretation-flags.json")["interpretation_flags"]}
    for r in list(rules.values()) + cands:
        for fl in r.get("interpretation_flag_ids", []):
            rep.check(fl in flags, f"{r['rule_id']} flag {fl} defined")

    # 16. Accepted gaps -> warnings
    missing_text = [p for p in provs.values() if p["source_text"] is None]
    if missing_text:
        rep.warn(f"{len(missing_text)}/{len(provs)} provisions have no source text "
                 f"(SOURCE_TEXT_NOT_RETRIEVED; Phase 2 deferred, IF-10)")
    return rep


def main() -> int:
    rep = run_checks()
    for w in rep.warnings:
        print(f"WARNING  {w}")
    for e in rep.errors:
        print(f"ERROR    {e}")
    print(f"svkg check: {len(rep.passed)} passed, {len(rep.errors)} failed, {len(rep.warnings)} warnings")
    return 1 if rep.errors else 0
