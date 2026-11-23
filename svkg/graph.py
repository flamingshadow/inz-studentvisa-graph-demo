"""Phase 3: idempotent loader for the `studentvisa` Neo4j database.

Every node gets its Section 3 layer label (Authority, Provision, Rule, Qualifier,
Domain, Evidence, Decision, Lifecycle, Governance) plus, where one exists, its
Section 3 node-type label (e.g. Rule:Obligation, Qualifier:Threshold). All nodes
carry `id` (unique per layer) and `name` (caption for Bloom). Spec nodes are
marked origin='SPEC'; assessment nodes written by the engine are origin='ASSESSMENT'
and are never touched by `svkg load`.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone

from .config import driver, settings
from .docspec import ROOT, sha256

LAYERS = ["Authority", "Provision", "Rule", "Qualifier", "Domain", "Evidence", "Decision",
          "Lifecycle", "Governance"]
SECTION3_TYPES = {
    "Authority": {"AuthoritySource", "InstrumentVersion"},
    "Provision": {"Part", "Section", "Subsection", "Regulation", "Clause", "Instruction", "Paragraph"},
    "Rule": {"BusinessRule", "EligibilityRule", "Obligation", "Prohibition", "Permission", "Definition",
             "Calculation"},
    "Qualifier": {"Condition", "Exception", "Threshold", "TemporalConstraint", "Discretion", "Trigger"},
    "Domain": {"Visa", "Applicant", "Application", "Provider", "Programme", "Sponsor", "Guardian",
               "ImmigrationOfficer"},
    "Evidence": {"EvidenceRequirement", "EvidenceItem", "ObservedFact", "VerifiedFact"},
    "Decision": {"DecisionCheck", "RuleEvaluation", "DecisionOutcome", "Explanation"},
    "Lifecycle": {"State", "StateTransition"},
    "Governance": {"CoverageGap", "InterpretationFlag", "TestCase"},
}
_IDENT = re.compile(r"^[A-Z][A-Za-z_]*$")


def _j(rel):
    return json.loads((ROOT / rel).read_text())


def flatten(rec: dict, **extra) -> dict:
    """Neo4j properties must be primitives or homogeneous primitive lists."""
    out = {}
    for k, v in {**rec, **extra}.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        else:
            out[f"{k}_json"] = json.dumps(v, ensure_ascii=False)
    return out


def qualifier_texts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f, key in (("rules/conditions.json", "conditions"), ("rules/conditions.json", "triggers"),
                   ("rules/exceptions.json", "exceptions"), ("rules/temporal-constraints.json", "temporal_constraints")):
        for q in _j(f)[key]:
            if q.get("rule_id"):
                out.setdefault(q["rule_id"], {}).setdefault(key, []).append(q["text"])
    for d in _j("rules/discretions.json")["discretions"]:
        out.setdefault(d["rule_id"], {})["discretion_holder"] = d.get("holder")
    return out


def spec_nodes() -> list[tuple[str, str | None, dict]]:
    """(layer, type_label, properties) for every spec object."""
    n: list[tuple[str, str | None, dict]] = []
    src = _j("regulatory/sources.json")
    for s in src["sources"]:
        n.append(("Authority", "AuthoritySource", flatten(s, id=s["source_id"], name=s["title"])))
    for v in src["instrument_versions"]:
        n.append(("Authority", "InstrumentVersion",
                  flatten(v, id=v["instrument_version_id"], name=v["instrument_version_id"])))
    for p in _j("regulatory/provisions.json")["provisions"]:
        label = "Paragraph" if p["provision_type"] == "Subparagraph" else p["provision_type"]
        n.append(("Provision", label, flatten(p, id=p["provision_id"], name=p["reference"])))
    rules = _j("rules/rules.json")
    qtext = qualifier_texts()
    for r in rules["rules"] + rules["candidate_rules"] + rules.get("instruction_rules", []):
        a, c = r["authority"], r["classification"]
        qt = qtext.get(r["rule_id"], {})
        n.append(("Rule", c["rule_type"], flatten(
            r, id=r["rule_id"], name=r["rule_id"], rule_type=c["rule_type"], modality=c["modality"],
            published_type=c["published_type"], automation_class=r["automation"]["class"],
            automation_class_as_published=r["automation"]["class_as_published"],
            machine_evaluable=r["automation"]["machine_evaluable"],
            regulatory_instrument=a["regulatory_instrument"], instrument_type=a["instrument_type"],
            provision=a["provision"], source_id=a["source_id"], provision_id=a["provision_id"],
            provision_ids=a["provision_ids"], source_version=a["source_version"],
            coverage_gap_ids=r["automation"]["coverage_gap_ids"],
            # IF-35: verbatim qualifier phrases are properties of the rule, not separate nodes
            conditions=qt.get("conditions"), exceptions=qt.get("exceptions"),
            time_constraints=qt.get("temporal_constraints"), triggers=qt.get("triggers"),
            discretion_holder=qt.get("discretion_holder"))))
    for f, key, label in (("rules/conditions.json", "conditions", "Condition"),
                          ("rules/conditions.json", "triggers", "Trigger"),
                          ("rules/exceptions.json", "exceptions", "Exception"),
                          ("rules/thresholds.json", "thresholds", "Threshold"),
                          ("rules/temporal-constraints.json", "temporal_constraints", "TemporalConstraint"),
                          ("rules/discretions.json", "discretions", "Discretion")):
        for q in _j(f)[key]:
            n.append(("Qualifier", label, flatten(q, name=q["text"] if "text" in q else q["id"])))
    actors = {a["actor_id"]: a for a in _j("regulatory/actors.json")["actors"]}
    for c in _j("regulatory/concepts.json")["concepts"]:
        a = actors.get(c["concept_id"], {})
        n.append(("Domain", c["type_label"], flatten(
            c, id=c["concept_id"], name=c["preferred_term"],
            kind="Actor" if a else ("Product" if c["type_label"] == "Visa" else "Concept"),
            actor_derived_from=a.get("derived_from"))))
    for ft in _j("regulatory/fact-types.json")["fact_types"]:
        n.append(("Domain", None, flatten(ft, id=ft["fact_type_id"], name=ft["fact_type_id"], kind="FactType")))
    ev = _j("evidence/evidence-requirements.json")
    vr = {v["evidence_type_id"]: v for v in _j("evidence/verification-rules.json")["verification_rules"]}
    for e in ev["evidence_types"]:
        v = vr.get(e["evidence_type_id"], {})
        n.append(("Evidence", None, flatten(e, id=e["evidence_type_id"], name=e["evidence_item"],
                                            kind="EvidenceType",
                                            verification_rule_id=v.get("verification_rule_id"),
                                            verification_checks=v.get("verification_checks"))))
    for e in ev["evidence_requirements"]:
        n.append(("Evidence", "EvidenceRequirement",
                  flatten(e, id=e["evidence_requirement_id"], name=e["evidence_requirement_id"])))
    for c in _j("decisioning/decision-checks.json")["decision_checks"]:
        n.append(("Decision", "DecisionCheck", flatten(c, id=c["check_id"], name=c["check_id"])))
    for s in _j("decisioning/states.json")["states"]:
        n.append(("Lifecycle", "State", flatten(s, id=s["state_id"], name=s["name"])))
    for t in _j("decisioning/state-transitions.json")["state_transitions"]:
        n.append(("Lifecycle", "StateTransition", flatten(t, id=t["transition_id"], name=t["transition_id"])))
    cov = _j("governance/source-coverage.json")
    for g in cov["coverage_gaps"]:
        n.append(("Governance", "CoverageGap", flatten(g, name=g["id"])))
    for r in cov["risks"]:
        n.append(("Governance", None, flatten(r, name=r["id"], kind="Risk")))
    for f in _j("governance/interpretation-flags.json")["interpretation_flags"]:
        n.append(("Governance", "InterpretationFlag", flatten(f, id=f["flag_id"], name=f["flag_id"])))
    for t in _j("testing/test-cases.json")["test_cases"]:
        n.append(("Governance", "TestCase", flatten(t, id=t["test_case_id"], name=t["test_case_id"])))
    for layer, label, _ in n:
        assert layer in LAYERS and (label is None or label in SECTION3_TYPES[layer]), (layer, label)
    return n


KEEP_ISOLATED = {"InterpretationFlag"}   # flags may apply to files rather than nodes


def graph_nodes(rels: list[dict]) -> list[tuple[str, str | None, dict]]:
    """Minimal-graph principle (IF-34): spec nodes with no relationship stay in the JSON files only."""
    linked = {r["from"] for r in rels} | {r["to"] for r in rels}
    return [x for x in spec_nodes() if x[2]["id"] in linked or x[1] in KEEP_ISOLATED]


def profile() -> dict:
    return __import__("yaml").safe_load((ROOT / "schema/ontology.yaml").read_text())["graph_profile"]


def check_profile(nodes, rels) -> list[str]:
    prof = profile()
    allowed_labels = set(prof["node_labels"])
    allowed_rels = set(prof["relationship_types"])
    errs = []
    for layer, label, p in nodes:
        for l in (layer, label):
            if l and l not in allowed_labels:
                errs.append(f"label {l} ({p['id']}) not in graph_profile")
    for r in rels:
        if r["type"] not in allowed_rels:
            errs.append(f"relationship {r['type']} not in graph_profile")
    return sorted(set(errs))


def ensure_schema(session):
    for layer in LAYERS + ["LoadLog"]:
        session.run(f"CREATE CONSTRAINT {layer.lower()}_id IF NOT EXISTS FOR (n:{layer}) REQUIRE n.id IS UNIQUE")
    existing = {r["name"]: r["props"] for r in session.run(
        "SHOW FULLTEXT INDEXES YIELD name, properties AS props").data()}
    if "rule_text" in existing and "instruction_statement" not in existing["rule_text"]:
        session.run("DROP INDEX rule_text")
    session.run("CREATE FULLTEXT INDEX rule_text IF NOT EXISTS FOR (n:Rule) "
                "ON EACH [n.rulespeak_statement, n.instruction_statement, n.candidate_content, n.illustrative_logic]")
    session.run("CREATE FULLTEXT INDEX provision_text IF NOT EXISTS FOR (n:Provision) "
                "ON EACH [n.source_text, n.reference, n.heading]")
    session.run("CREATE FULLTEXT INDEX concept_terms IF NOT EXISTS FOR (n:Domain) ON EACH [n.preferred_term, n.name]")
    session.run("CREATE INDEX rule_domain IF NOT EXISTS FOR (n:Rule) ON (n.domain)")


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def load(verbose=True) -> dict:
    s = settings()
    rels = _j("regulatory/relationships.json")["relationships"]
    nodes = graph_nodes(rels)
    bad = check_profile(nodes, rels)
    if bad:
        raise ValueError("graph_profile violation (minimal-graph principle): " + "; ".join(bad[:10]))
    vocab = {r["type"] for r in __import__("yaml").safe_load(
        (ROOT / "schema/relationship-types.yaml").read_text())["relationship_types"]}
    layer_of = {}
    for layer, _, p in nodes:
        if p["id"] in layer_of:
            raise ValueError(f"duplicate id {p['id']}")
        layer_of[p["id"]] = layer
    load_id = "LOAD-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    groups: dict[tuple[str, str | None], list[dict]] = {}
    for layer, label, p in nodes:
        groups.setdefault((layer, label), []).append({**p, "origin": "SPEC", "load_id": load_id})
    rgroups: dict[tuple[str, str, str], list[dict]] = {}
    for r in rels:
        if r["type"] not in vocab or not _IDENT.match(r["type"]):
            raise ValueError(f"relationship type {r['type']} not in 3.1 vocabulary")
        key = (r["type"], layer_of[r["from"]], layer_of[r["to"]])
        rgroups.setdefault(key, []).append({"from": r["from"], "to": r["to"],
                                            "props": {**r.get("props", {}), "origin": "SPEC", "load_id": load_id}})
    with driver(s) as d, d.session(database=s.database) as session:
        ensure_schema(session)
        for (layer, label), rows in groups.items():
            extra = f" SET n:{label}" if label else ""
            session.run(f"UNWIND $rows AS row MERGE (n:{layer} {{id: row.id}}) "
                        f"SET n = row{extra}", rows=rows).consume()
        for (typ, fl, tl), rows in rgroups.items():
            session.run(f"UNWIND $rows AS row MATCH (a:{fl} {{id: row.from}}) MATCH (b:{tl} {{id: row.to}}) "
                        f"MERGE (a)-[r:{typ}]->(b) SET r = row.props", rows=rows).consume()
        stale_r = session.run("MATCH ()-[r]->() WHERE r.origin='SPEC' AND r.load_id <> $l DELETE r RETURN count(*) AS c",
                              l=load_id).single()["c"]
        stale_n = session.run("MATCH (n) WHERE n.origin='SPEC' AND n.load_id <> $l DETACH DELETE n RETURN count(*) AS c",
                              l=load_id).single()["c"]
        counts = session.run("MATCH (n) WHERE n.origin='SPEC' RETURN count(n) AS n").single()["n"]
        rcount = session.run("MATCH ()-[r]->() WHERE r.origin='SPEC' RETURN count(r) AS n").single()["n"]
        info = {"id": load_id, "name": load_id, "loaded_at": datetime.now(timezone.utc).isoformat(),
                "spec_sha256": sha256(), "git_commit": _git_commit(), "database": s.database,
                "spec_nodes": counts, "spec_relationships": rcount,
                "stale_nodes_removed": stale_n, "stale_relationships_removed": stale_r}
        session.run("CREATE (l:LoadLog) SET l = $p", p={k: v for k, v in info.items() if v is not None}).consume()
    if counts != len(nodes) or rcount != len(rels):
        raise RuntimeError(f"load mismatch: nodes {counts}/{len(nodes)}, relationships {rcount}/{len(rels)}")
    return info
