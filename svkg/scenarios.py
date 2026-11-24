"""Scenarios (the SVP personas): list them, show them, run them against the database, and check each
result against its expected outcome. Shared by the local dashboard and the Claude Desktop tools.

A run is a dry run by default: it reads the rules from the live `studentvisa` database, assesses the
synthetic application and returns the result, but writes nothing. `save=True` also records the assessment
in the graph and the audit log, exactly as `svkg assess` does.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import rag
from .config import driver, settings
from .digest import digest
from .docspec import ROOT
from .engine import Application, Engine, GraphStore, check_result

GROUPS = {
    "SV-CHECK-APP-001": "Application form and fees", "SV-CHECK-APP-005": "Disclosure of changes",
    "SV-CHECK-LODG-001": "Lodgement route", "SV-CHECK-FIN-001": "Funds for maintenance",
    "SV-CHECK-MIN-001": "Minors and consent", "SV-CHECK-U10-001": "Children under 10",
    "SV-CHECK-IVL-001": "Immigration levy (IVL)", "SV-CHECK-WORK-001": "Work conditions",
    "SV-CHECK-HEALTH-001": "Health", "SV-CHECK-EDU-009": "Provider duties", "SV-CHECK-FAM-001": "Family",
    "SV-CHECK-PATH-001": "Pathway student visas", "SV-CHECK-SPON-001": "Sponsorship",
    "SV-CHECK-PROV-001": "Providers and programmes", "SV-CHECK-WRK-001": "Work rights",
    "SV-CHECK-DISC-001": "Final grant decision", "SV-CHECK-INSTR-001": "Immigration Instructions coverage",
}


def group_label(check_id: str) -> str:
    if check_id in GROUPS:
        return GROUPS[check_id]
    if check_id.endswith("-RES"):
        return "Residual review"
    return check_id


def _read(rel: str):
    return json.loads((ROOT / rel).read_text())


@lru_cache(maxsize=1)
def _catalogue() -> tuple[dict, dict]:
    tcs = {t["test_case_id"]: t for t in _read("testing/test-cases.json")["test_cases"]}
    exp = _read("testing/expected-results.json")["expected_results"]
    return tcs, exp


def _app_file(sid: str) -> dict:
    p = ROOT / "testing" / "applications" / f"{sid}.json"
    return json.loads(p.read_text())


def _summary(sid: str, tc: dict, exp: dict, app: dict | None = None) -> dict:
    status = tc["expected_status"]
    r = rag.of(status)
    out = {"id": sid, "title": None, "scenario": tc["scenario"], "expected_outcome": tc.get("expected_outcome"),
           "test_type": tc["test_type"], "check_id": tc["check_id"], "group": group_label(tc["check_id"]),
           "expected_status": status, "expected_rag": r["rag"], "expected_icon": r["icon"],
           "expected_label": r["label"], "rule_id": tc.get("rule_id")}
    app = app or _app_file(sid)
    persona, who = app.get("persona", {}), app.get("applicant", {})
    out.update(title=persona.get("title") or tc["scenario"][:60],
               applicant=" ".join(x for x in (who.get("given_name"), who.get("family_name")) if x) or None,
               nationality=who.get("nationality"), facts=len(app.get("facts", [])),
               evidence=len(app.get("evidence_items", [])), channel=app.get("channel"))
    return out


def list_scenarios() -> list[dict]:
    tcs, exp = _catalogue()
    return [_summary(sid, tcs[sid], exp.get(sid, {})) for sid in sorted(tcs)]


# ----------------------------------------------------------------------------------- expectations
def describe(rule: dict) -> str:
    """Plain-language line for one expected-result assertion."""
    if "explanation_chain_for_rule" in rule:
        return (f"The result for {rule['explanation_chain_for_rule']} can be traced "
                f"{' → '.join(rule['chain'])}")
    if "accepted" in rule:
        want = " or ".join(rule["accepted"])
        scope = f" for {rule['rule_id']}" if rule.get("rule_id") else ""
        extra = ""
        if rule.get("provenance_status"):
            extra += f"; provenance {rule['provenance_status']}"
        if rule.get("exception_rule_id"):
            extra += f"; exemption {rule['exception_rule_id']}"
        return f"{group_label(rule['check_id'])} ({rule['check_id']}){scope} is {want}{extra}"
    if "missing_information_includes" in rule:
        return f"The missing fact {rule['missing_information_includes']} is requested"
    if "no_result_for_rule" in rule:
        return f"{rule['no_result_for_rule']} is never {' or '.join(rule['forbidden'])} (missing evidence is not failure)"
    if "human_review_includes" in rule:
        return f"{rule['human_review_includes']} is referred for human review"
    if "production_decision" in rule:
        return "The result is marked as not a production decision"
    if "evidence_used_type" in rule:
        return f"{rule['evidence_used_type']} evidence is listed as used by {rule['check_id']}"
    if rule.get("deterministic_all_pass"):
        return "Every deterministic check (A1/A2) passes or does not apply"
    return json.dumps(rule)


STANDING = [
    {"standing": True, "text": "The final grant is routed to the decision-maker (SV-CHECK-DISC-001 is DISCRETION_REQUIRED)"},
    {"standing": True, "text": "Marked as POC with partial source coverage; never a production decision"},
]


def _chain(a: dict, rule_id: str) -> list[str]:
    ev = next((e for e in a["rule_evaluations"] if e["rule_id"] == rule_id), None)
    if not ev:
        return []
    steps = ["RuleEvaluation", "DecisionCheck", "Rule"]
    if ev.get("provision_id"):
        steps.append("Provision")
    if ev.get("source_id"):
        steps.append("AuthoritySource")
    return steps


def evaluate(a: dict, asserts: list[dict]) -> list[dict]:
    """Check an assessment against its expected-result assertions. Never raises; each line passes or fails."""
    out = []
    for r in asserts:
        text = describe(r)
        try:
            ok, detail = _eval_one(a, r)
        except Exception as e:                      # a malformed assertion fails visibly instead of hiding
            ok, detail = False, f"could not evaluate: {e}"
        out.append({"text": text, "passed": ok, "detail": detail, "standing": False})
    disc = check_result(a, "SV-CHECK-DISC-001")
    out.append({**STANDING[0], "passed": disc == "DISCRETION_REQUIRED", "detail": f"actual: {disc}"})
    scope = a["scope"]
    out.append({**STANDING[1], "passed": scope["production_decision"] is False and scope["source_coverage"] == "PARTIAL",
                "detail": f"production_decision={scope['production_decision']}, source_coverage={scope['source_coverage']}"})
    return out


def _eval_one(a: dict, r: dict) -> tuple[bool, str]:
    evs = a["rule_evaluations"]
    if "explanation_chain_for_rule" in r:
        got = _chain(a, r["explanation_chain_for_rule"])
        want = [s for s in r["chain"] if s != "InstrumentVersion"]
        return got == want, "traced: " + " → ".join(got) if got else "no evaluation for that rule"
    if "accepted" in r:
        got = check_result(a, r["check_id"], r.get("rule_id"))
        if got is None:
            return False, "no evaluation was recorded for this check"
        ok = got in r["accepted"]
        sel = [e for e in evs if e["check_id"] == r["check_id"] and (not r.get("rule_id") or e["rule_id"] == r["rule_id"])]
        notes = []
        if r.get("provenance_status"):
            good = all(e["provenance_status"] == r["provenance_status"] for e in sel)
            ok, notes = ok and good, notes + [f"provenance {'ok' if good else 'differs'}"]
        if r.get("exception_rule_id"):
            good = bool(sel) and sel[0].get("exception_rule_id") == r["exception_rule_id"]
            ok, notes = ok and good, notes + [f"exemption {'ok' if good else 'missing'}"]
        return ok, f"actual: {got}" + ("; " + "; ".join(notes) if notes else "")
    if "missing_information_includes" in r:
        paths = {m["fact_path"] for m in a["missing_information"]}
        return r["missing_information_includes"] in paths, "requested: " + (", ".join(sorted(paths)) or "nothing")
    if "no_result_for_rule" in r:
        bad = {e["result"] for e in evs if e["rule_id"] == r["no_result_for_rule"]} & set(r["forbidden"])
        return not bad, "found: " + (", ".join(sorted(bad)) if bad else "no forbidden result")
    if "human_review_includes" in r:
        ids = {h["rule_id"] for h in a["human_review"]}
        return r["human_review_includes"] in ids, f"{len(ids)} item(s) in human review"
    if "production_decision" in r:
        return a["scope"]["production_decision"] is r["production_decision"], \
            f"production_decision={a['scope']['production_decision']}"
    if "evidence_used_type" in r:
        ev = next((e for e in evs if e["check_id"] == r["check_id"]), None)
        return bool(ev and ev["evidence_item_ids"]), \
            f"{len(ev['evidence_item_ids']) if ev else 0} evidence item(s) listed"
    if r.get("deterministic_all_pass"):
        det = [e for e in evs if e["automation_class"] in ("A1", "A2") and e["result"] != "SOURCE_COVERAGE_GAP"]
        bad = [e["rule_id"] for e in det if e["result"] not in ("SATISFIED", "NOT_APPLICABLE")]
        return bool(det) and not bad, f"{len(det)} deterministic result(s)" + (f"; not passing: {', '.join(bad)}" if bad else "")
    raise ValueError("unknown assertion type")


# ----------------------------------------------------------------------------------- detail and run
def _evidence_types(app: dict) -> dict:
    return {e["evidence_item_id"]: e for e in app.get("evidence_items", [])}


def scenario_detail(sid: str) -> dict | None:
    tcs, exp = _catalogue()
    if sid not in tcs:
        return None
    app = _app_file(sid)
    ev = _evidence_types(app)
    out = _summary(sid, tcs[sid], exp.get(sid, {}), app)
    persona = app.get("persona", {})
    out.update(
        backstory=persona.get("backstory"), synthetic_notice=persona.get("synthetic_notice"),
        deviation=tcs[sid].get("deviation"), interpretation_flag_ids=tcs[sid].get("interpretation_flag_ids", []),
        application_id=app["application_id"], assessment_as_at=app.get("assessment_as_at"),
        applicant={k: v for k, v in app.get("applicant", {}).items()
                   if k not in ("applicant_id", "email", "passport_number", "passport_expiry")},
        facts=[{"attribute": f["attribute"], "value": f["value"], "verified": f["verification_status"] == "VERIFIED",
                "evidence": [{"id": i, "type": ev[i]["evidence_type"], "verified": ev[i]["verification_status"] == "VERIFIED"}
                             for i in f.get("evidence_item_ids", []) if i in ev]} for f in app.get("facts", [])],
        evidence=[{"id": e["evidence_item_id"], "type": e["evidence_type"], "reference": e.get("document_reference"),
                   "verified": e["verification_status"] == "VERIFIED"} for e in app.get("evidence_items", [])],
        expectations=[{"text": describe(r), "passed": None, "detail": "", "standing": False}
                      for r in exp.get(sid, {}).get("assert", [])] + [{**s, "passed": None, "detail": ""} for s in STANDING],
    )
    return out


def _enrich(a: dict, app: dict, store: GraphStore) -> list[dict]:
    ev_by_id = _evidence_types(app)
    out = []
    for e in a["rule_evaluations"]:
        r = rag.of(e["result"])
        prov = store.provisions.get(e.get("provision_id") or "", {})
        src = store.sources.get(e.get("source_id") or "", {})
        item = {**e, "rag": r["rag"], "icon": r["icon"], "rag_label": r["label"], "group": group_label(e["check_id"]),
                "provision_text": prov.get("source_text"), "provision_url": prov.get("source_url"),
                "provision_heading": prov.get("heading"), "provision_effective_from": prov.get("effective_from"),
                "source_title": src.get("title") or e.get("regulatory_instrument"),
                "source_version_id": src.get("version_id")}
        for f in item["facts_used"]:
            f["evidence"] = [{"id": i, "type": ev_by_id[i]["evidence_type"]}
                             for i in f.get("evidence_item_ids", []) if i in ev_by_id]
        item["evidence_used"] = [{"id": i, "type": ev_by_id[i]["evidence_type"],
                                  "verified": ev_by_id[i]["verification_status"] == "VERIFIED"}
                                 for i in e.get("evidence_item_ids", []) if i in ev_by_id]
        out.append(item)
    return out


def _groups(evals: list[dict], store: GraphStore) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for e in evals:
        by.setdefault(e["check_id"], []).append(e)
    groups = []
    for cid, items in by.items():
        results = [x["result"] for x in items]
        groups.append({"check_id": cid, "label": group_label(cid), "purpose": (store.checks.get(cid) or {}).get("purpose"),
                       "rag": rag.overall(results), "counts": rag.counts(results), "evaluations": items})
    groups.sort(key=lambda g: (g["check_id"] == "SV-CHECK-DISC-001", rag.ORDER[g["rag"]], g["label"]))
    return groups


def _view(sid: str, app_raw: dict, a: dict, store: GraphStore, saved: bool) -> dict:
    tcs, exp = _catalogue()
    evals = _enrich(a, app_raw, store)
    expectations = evaluate(a, exp.get(sid, {}).get("assert", []))
    detail = scenario_detail(sid)
    results = [e["result"] for e in evals]
    return {
        "scenario": detail, "saved": saved,
        "assessment": {k: a[k] for k in ("assessment_id", "application_id", "assessment_as_at", "scope", "summary",
                                         "missing_information", "human_review", "source_coverage_gaps", "provenance")},
        "overall": {"rag": rag.overall(results), "status": a["summary"]["overall_status"]},
        "rag_counts": rag.counts(results), "groups": _groups(evals, store),
        "expectations": expectations, "passed": all(x["passed"] for x in expectations),
        "digest": digest(a, expectations, detail),
    }


def run(ids: list[str], save: bool = False, channel: str = "dashboard") -> list[dict]:
    """Run scenarios against the live database. One store load serves every scenario in the call."""
    tcs, _ = _catalogue()
    unknown = [i for i in ids if i not in tcs]
    if unknown:
        raise KeyError(f"unknown scenario(s): {', '.join(unknown)}")
    s = settings()
    out = []
    with driver(s) as d, d.session(database=s.database) as session:
        store = GraphStore(session)
        engine = Engine(store)
        for sid in ids:
            raw = _app_file(sid)
            app = Application.model_validate(raw)
            a = engine.assess(app)
            if save:
                from .record import audit_assessment, write_assessment
                write_assessment(session, app, a)
                audit_assessment(app, a, channel=channel)
            out.append(_view(sid, raw, a, store, save))
    return out


def run_one(sid: str, save: bool = False, channel: str = "dashboard") -> dict:
    return run([sid], save, channel)[0]


def compact(view: dict) -> dict:
    """The small form of a run, for Claude Desktop: counts, expectations, key actions and one line per rule."""
    d = view["digest"]
    rules = []
    for g in view["groups"]:
        for e in g["evaluations"]:
            rules.append({"rule_id": e["rule_id"], "check": g["label"], "result": e["result"], "rag": e["rag"],
                          "provision": e.get("provision"), "instrument": e.get("regulatory_instrument"),
                          "source_version": e.get("source_version"), "automation_class": e.get("automation_class"),
                          "explanation": " ".join((e["explanation"] or "").split())[:300],
                          "unresolved": e.get("unresolved_requirements", [])[:3],
                          "label": e.get("label")})
    return {"poc_notice": d["poc_notice"], "saved": view["saved"],
            "scenario": d.get("scenario"), "overall": d["overall"], "rag_counts": d["rag_counts"],
            "result_counts": d["result_counts"], "expectations": d["expectations"],
            "expected_result_met": view["passed"], "key_actions": d["key_actions"], "met": d["met"],
            "candidate_figure_labels": d["candidate_figure_labels"], "writing_guidance": d["writing_guidance"],
            "rules": rules}
