"""Persist assessments: graph nodes (origin='ASSESSMENT') and an append-only audit log.

Graph pattern written per assessment (3.1 vocabulary only):
  (Application)-[:APPLIES_TO]->(Visa STUDENT-VISA)                         FT-002
  (Application)-[:CONTAINS]->(EvidenceItem)-[:SUPPORTS_FACT]->(ObservedFact) FT-004
  (ObservedFact)-[:VERIFIED_AS]->(VerifiedFact)                            FT-005
  (ObservedFact|VerifiedFact)-[:INPUT_TO {evaluation_id}]->(DecisionCheck)  FT-006
  (DecisionCheck)-[:PRODUCES_OUTCOME]->(RuleEvaluation)-[:EVALUATES]->(Rule)
  (Application)-[:PRODUCES_OUTCOME]->(DecisionOutcome {kind:'ASSESSMENT'})-[:CONTAINS]->(RuleEvaluation)
  (DecisionOutcome)-[:EXPLAINED_BY]->(Explanation)

Minimal-graph principle (IF-34): an evaluation's result and explanation are properties of the RuleEvaluation;
there is one DecisionOutcome and one Explanation per assessment, not per evaluation.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .config import LOG_DIR
from .engine import POC_NOTICE, Application
from .graph import flatten

AUDIT_LOG = LOG_DIR / "audit-log.jsonl"
_lock = threading.Lock()


def audit(event: str, **data):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
    with _lock, AUDIT_LOG.open("a", encoding="utf-8") as f:       # append-only
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def audit_assessment(app: Application, a: dict, actor="RULE_ENGINE", channel="cli"):
    aid = a["assessment_id"]
    audit("APPLICATION_SUBMITTED", assessment_id=aid, application_id=app.application_id,
          applicant_id=app.applicant.get("applicant_id"), channel=channel, synthetic=True,
          source_rule_version="spec-v0.2-2026-11-17")
    for e in app.evidence_items:
        audit("EVIDENCE_RECEIVED", assessment_id=aid, evidence_item_id=e.evidence_item_id,
              evidence_type=e.evidence_type, source=e.supplied_by_actor_id, received_at=e.received_at,
              linked_fact_ids=e.supports_fact_ids)
    for f in app.facts:
        if f.verification_status == "VERIFIED":
            audit("FACT_VERIFIED", assessment_id=aid, fact_id=f.fact_id, attribute=f.attribute, value=f.value,
                  verifier=getattr(f, "verified_by", None), method="recorded in synthetic application",
                  evidence_item_ids=f.evidence_item_ids, verified_at=f.verified_at)
    for e in a["rule_evaluations"]:
        audit("RULE_EVALUATED", assessment_id=aid, evaluation_id=e["evaluation_id"], rule_id=e["rule_id"],
              check_id=e["check_id"], source_version=e["source_version"],
              inputs=[x["fact_id"] for x in e["facts_used"]], result=e["result"], automated=True, actor=actor)
    for h in a["human_review"]:
        audit("HUMAN_REVIEW_REQUESTED", assessment_id=aid, evaluation_id=h["evaluation_id"], reason=h["reason"],
              rules=[h["rule_id"]], evidence=h["material_presented"], unresolved_facts=h["unresolved"],
              assigned_role=h["decision_maker"])


def write_assessment(session, app: Application, a: dict) -> dict:
    aid, appid = a["assessment_id"], app.application_id
    ts = datetime.now(timezone.utc).isoformat()
    base = {"origin": "ASSESSMENT", "assessment_id": aid, "written_at": ts, "synthetic": True}

    def tx(t):
        t.run("MERGE (ap:Domain:Application {id:$id}) SET ap += $p "
              "WITH ap MATCH (v:Domain {id:'STUDENT-VISA'}) MERGE (ap)-[:APPLIES_TO]->(v)",
              id=appid, p={**base, "name": appid, "kind": "ApplicationRecord", "channel": app.channel,
                           "applicant_id": app.applicant.get("applicant_id")})
        t.run("UNWIND $rows AS r MERGE (e:Evidence:EvidenceItem {id:r.id}) SET e += r "
              "WITH e MATCH (ap:Application {id:$app}) MERGE (ap)-[:CONTAINS]->(e)",
              app=appid, rows=[{**flatten(e.model_dump()), **base, "id": e.evidence_item_id,
                                "name": e.evidence_type} for e in app.evidence_items])
        facts = [{**flatten(f.model_dump(), value=json.dumps(f.value)), **base, "obs_id": f"OBS-{f.fact_id}",
                  "fid": f.fact_id, "name": f"{f.attribute}={f.value}"} for f in app.facts]
        t.run("UNWIND $rows AS r MERGE (o:Evidence:ObservedFact {id:r.obs_id}) SET o += r, o.id=r.obs_id "
              "WITH o, r UNWIND r.evidence_item_ids AS eid MATCH (e:EvidenceItem {id:eid}) "
              "MERGE (e)-[:SUPPORTS_FACT]->(o)", rows=facts)
        t.run("UNWIND $rows AS r MATCH (o:ObservedFact {id:r.obs_id}) "
              "MERGE (v:Evidence:VerifiedFact {id:r.fid}) SET v += r, v.id=r.fid MERGE (o)-[:VERIFIED_AS]->(v)",
              rows=[f for f in facts if f["verification_status"] == "VERIFIED"])
        t.run("CREATE (o:Decision:DecisionOutcome {id:$id}) SET o += $p "
              "WITH o MATCH (ap:Application {id:$app}) MERGE (ap)-[:PRODUCES_OUTCOME]->(o)",
              id=aid, app=appid, p={**base, "name": aid, "kind": "ASSESSMENT",
                                    "overall_status": a["summary"]["overall_status"],
                                    "production_decision": False, "source_coverage": a["scope"]["source_coverage"],
                                    "summary_json": json.dumps(a["summary"]), "poc_notice": POC_NOTICE})
        rows = []
        for e in a["rule_evaluations"]:
            rows.append({"props": {**flatten({k: v for k, v in e.items()}), **base, "id": e["evaluation_id"],
                                   "name": f"{e['rule_id']}: {e['result']}"},
                         "id": e["evaluation_id"], "rule_id": e["rule_id"], "check_id": e["check_id"],
                         "result": e["result"],
                         "facts": [x["fact_id"] if x["verification_status"] == "VERIFIED" else f"OBS-{x['fact_id']}"
                                   for x in e["facts_used"]]})
        t.run("""UNWIND $rows AS r
                 MATCH (asmt:DecisionOutcome {id:$aid})
                 MATCH (rule:Rule {id:r.rule_id}) MATCH (chk:DecisionCheck {id:r.check_id})
                 CREATE (ev:Decision:RuleEvaluation) SET ev = r.props
                 CREATE (ev)-[:EVALUATES]->(rule)
                 CREATE (chk)-[:PRODUCES_OUTCOME]->(ev)
                 CREATE (asmt)-[:CONTAINS]->(ev)
                 WITH ev, chk, r
                 CALL (chk, r) {
                   UNWIND r.facts AS fid MATCH (f:Evidence {id:fid}) WHERE f:ObservedFact OR f:VerifiedFact
                   CREATE (f)-[:INPUT_TO {evaluation_id: r.id}]->(chk)
                 }""", rows=rows, aid=aid)
        t.run("""MATCH (o:DecisionOutcome {id:$aid})
                 CREATE (x:Decision:Explanation {id: $aid + '-EXPL', name: 'Explanation ' + $aid, text: $text,
                         origin:'ASSESSMENT', assessment_id:$aid})
                 CREATE (o)-[:EXPLAINED_BY]->(x)""", aid=aid, text=summary_text(a))
    session.execute_write(tx)
    return {"assessment_id": aid, "evaluations_written": len(a["rule_evaluations"])}


def summary_text(a: dict) -> str:
    sm = a["summary"]
    lines = [f"Assessment {a['assessment_id']} of {a['application_id']} (POC, synthetic, production_decision=false): "
             f"overall {sm['overall_status']}; {sm['satisfied']} satisfied, {sm['not_satisfied']} not satisfied, "
             f"{sm['unknown']} unknown, {sm['human_review_required']} for human review, "
             f"{sm['discretion_required']} for the decision-maker, {sm['source_coverage_gaps']} source coverage gaps."]
    for e in a["rule_evaluations"]:
        lines.append(f"{e['rule_id']} ({e['provision'] or 'no provision'}): {e['result']}. {e['explanation']}")
    return "\n".join(lines)
