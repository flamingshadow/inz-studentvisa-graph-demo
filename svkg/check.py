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

    # 3. Candidate layer (Section 6) separate from the verified catalogue; reconciled to the manual (IF-31)
    t8 = spec.table_by_header("Domain", "6.")
    cands = rules_file["candidate_rules"]
    om_rules = {r["rule_id"]: r for r in rules_file.get("instruction_rules", [])}
    rep.eq(len(t8.rows) - 1, len(cands), "Section 6 candidate row count")
    allowed = {"RECONCILIATION_REQUIRED"} | ({"SUPERSEDED_BY_INSTRUCTION"} if om_rules else set())
    for row, c in zip(t8.rows[1:], cands):
        rep.eq(row[0], c["domain"], f"candidate {c['rule_id']} domain")
        rep.eq(row[3], c["provenance_status_as_published"], f"candidate {c['rule_id']} status")
        rep.check(c["provenance_status"] in allowed, f"candidate {c['rule_id']} provenance {c['provenance_status']}")
        rep.check(c["rule_id"] not in rules, f"candidate {c['rule_id']} kept out of verified catalogue")
        if om_rules:
            rep.check(bool(c.get("reconciliation")), f"candidate {c['rule_id']} has a reconciliation record")
    ths = _load("rules/thresholds.json")["thresholds"]
    label = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"
    cand_money = {t["value"] for t in ths if t.get("unit") == "NZD" and t["id"].startswith("SV-TH-CAND-")}
    rep.eq({20000, 1667, 17000, 1417, 9000}, cand_money, "decision-3 candidate funds figures loaded")
    if om_rules:
        rep.eq(set(), {c["rule_id"] for c in cands if c["executable"]}, "no candidate executes once the manual is loaded")
        execd = {t["value"] for t in ths if t.get("executable") and t.get("unit") == "NZD"}
        rep.eq({20000, 1667, 17000, 1417, 9000}, execd, "executable funds figures come from U3.20")
        for t in ths:
            if t.get("executable"):
                rep.check(t["provenance_status"] == "VERIFIED_INSTRUCTION_SOURCE", f"threshold {t['id']} from instructions")
    else:
        rep.eq({"SV-CAND-MAINT-TERTIARY", "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"},
               {c["rule_id"] for c in cands if c["executable"]}, "only decision-3 candidates execute")
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
        if c.get("defined_in", "").startswith("Implied") or c.get("defined_in", "").startswith("Rules marked"):
            rep.check(bool(c.get("interpretation_flag_ids")), f"added check {cid} carries an interpretation flag")

    # 11. Lifecycle
    t12 = spec.table_by_header("Entity", "8.")
    trans = _load("decisioning/state-transitions.json")["state_transitions"]
    rep.eq([(r[3]) for r in t12.rows[1:]], [t["trigger"] for t in trans], "lifecycle transitions")
    states = {s["name"] for s in _load("decisioning/states.json")["states"]}
    rep.eq({x for r in t12.rows[1:] for x in (r[1], r[2])}, states, "lifecycle states")

    # 12. Synthetic persona test cases SVP-001..
    tcs = {t["test_case_id"]: t for t in _load("testing/test-cases.json")["test_cases"]}
    apps = {a["application_id"]: a for a in _load("testing/synthetic-applications.json")["applications"]}
    exp = _load("testing/expected-results.json")["expected_results"]
    rep.check(len(tcs) >= 50, "at least 50 synthetic personas")
    rep.eq(sorted(tcs), sorted(exp), "test cases and expected results match")
    for tc, t in tcs.items():
        rep.check(t["application_id"] in apps and apps[t["application_id"]].get("synthetic") is True,
                  f"{tc} synthetic application")
        rep.check(t["check_id"] in checks, f"{tc} evaluates a known check")
        rep.check((ROOT / "testing" / "applications" / f"{tc}.json").exists(), f"{tc} fixture file")
    on_disk = {p.stem for p in (ROOT / "testing" / "applications").glob("*.json")}
    rep.eq(sorted(on_disk), sorted(tcs), "fixture files match test cases")
    covered = {t["check_id"] for t in tcs.values()}
    rep.check(set(checks) - {"SV-CHECK-INSTR-001"} - {c for c in checks if c.endswith("-RES")} <= covered
              | {"SV-CHECK-DISC-001"}, "every non-residual decision check has a persona")

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
    known = set(provs) | set(rules) | {c["rule_id"] for c in cands} | set(om_rules) | set(checks) | set(tcs)
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
    known |= {e["evidence_type_id"] for e in _load("evidence/evidence-requirements.json")["evidence_types"]}
    dangling = [(r["type"], r["from"], r["to"]) for r in rels if r["from"] not in known or r["to"] not in known]
    rep.check(not dangling, f"relationship endpoints resolve ({len(dangling)} dangling: {dangling[:5]})")
    flags = {f["flag_id"] for f in _load("governance/interpretation-flags.json")["interpretation_flags"]}
    # 15b. Operational Manual integrity (IF-25..IF-33)
    undated = set()
    if om_rules:
        for r in om_rules.values():
            p = provs.get(r["authority"]["provision_id"])
            ok = p is not None and p["source_text_status"] == "RETRIEVED" and p["source_text"] and \
                p["source_url"] and r["instruction_statement"]
            rep.check(bool(ok), f"{r['rule_id']} has instruction text and URL")
            if not r["effective_from"]:
                undated.add(r["instruction_code"])
        om_provs = [p for p in provs.values() if p["source_id"] == "NZ-INZ-IMMIGRATION-INSTRUCTIONS"]
        rep.check(all(p["source_url"] for p in om_provs), "every instruction provision has a URL")
        hist = [p for p in om_provs if p.get("version_kind") == "HISTORIC" and "@" in p["provision_id"]]
        rep.check(all(p["effective_from"] for p in hist), "historic instruction versions carry an effective date")
        rep.check(any(r["rule_id"] == "SV-OM-U3.20.30-A-I" for r in om_rules.values()), "U3.20.30(a)(i) rule present")
    for r in list(rules.values()) + cands + list(om_rules.values()):
        for fl in r.get("interpretation_flag_ids", []):
            rep.check(fl in flags, f"{r['rule_id']} flag {fl} defined")

    # 16. Provision text (Phase 2, decision 8)
    cache = ROOT / "data" / "legislation" / "provision-text.json"
    code = "NZ-EDU-PASTORAL-CODE-2021"
    for p in provs.values():
        pid = p["provision_id"]
        rep.check(p["source_text_status"] in enums["source_text_status"], f"{pid} source_text_status valid")
        om_prov = p["source_id"] == "NZ-INZ-IMMIGRATION-INSTRUCTIONS"
        if p["source_text_status"] == "RETRIEVED":
            rep.check(bool(p["source_text"]) and bool(p["source_url"]) and (om_prov or bool(p["source_version"]))
                      and bool(p["retrieved_at"]), f"{pid} retrieved text has text, URL, version and retrieved_at")
            host = "https://www.immigration.govt.nz/opsmanual/" if om_prov else "https://www.legislation.govt.nz/"
            rep.check(p["source_url"].startswith(host), f"{pid} URL is {host}")
        else:
            rep.check(p["source_text"] is None, f"{pid} has no text unless RETRIEVED (never paraphrased)")
            if cache.exists() and p["source_id"] != code:
                rep.check(False, f"{pid} not retrieved: {p.get('source_text_reason')}")
    srcs = {s["source_id"]: s for s in _load("regulatory/sources.json")["sources"]}
    for r in rules.values():
        sid = r["authority"]["source_id"]
        rep.eq(srcs[sid]["version_id"], r["authority"]["source_version"], f"{r['rule_id']} source_version")
    act = srcs["NZ-IMM-ACT-2009"]
    if act.get("version_as_published"):
        rep.eq("2026-07-10", act["version_id"], "Immigration Act version on legislation.govt.nz matches spec 12.1")
    missing_text = [p for p in provs.values() if p["source_text"] is None]
    if missing_text:
        codes = sorted(p["reference"] for p in missing_text if p["source_id"] == code)
        others = [p["provision_id"] for p in missing_text if p["source_id"] != code]
        rep.warn(f"{len(missing_text)}/{len(provs)} provisions have no source text. Pastoral Care Code "
                 f"({len(codes)}; not published on legislation.govt.nz, decision 8): {', '.join(codes)}"
                 + (f". Others: {', '.join(others)}" if others else ""))
    if undated:
        rep.warn(f"Instructions with no published effective date: {', '.join(sorted(undated))}")

    # Minimal-graph principle (IF-34): declared profile, no isolated nodes loaded
    from . import graph as _graph
    gnodes = _graph.graph_nodes(rels)
    for e in _graph.check_profile(gnodes, rels):
        rep.check(False, e)
    rep.check(True, "graph_profile respected")
    om_q = [q for f, k in (("rules/conditions.json", "conditions"), ("rules/exceptions.json", "exceptions"),
                           ("rules/discretions.json", "discretions"))
            for q in _load(f).get(k, []) if str(q.get("rule_id") or "").startswith("SV-OM-")]
    rep.check(not om_q, f"no derived condition/exception/discretion nodes for instruction rules ({len(om_q)})")
    return rep


def main() -> int:
    rep = run_checks()
    for w in rep.warnings:
        print(f"WARNING  {w}")
    for e in rep.errors:
        print(f"ERROR    {e}")
    print(f"svkg check: {len(rep.passed)} passed, {len(rep.errors)} failed, {len(rep.warnings)} warnings")
    return 1 if rep.errors else 0
