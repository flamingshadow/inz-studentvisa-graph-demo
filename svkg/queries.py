"""Fixed, parameterised, read-only graph queries used by the MCP server.

No free Cypher is accepted from callers: every query here is a constant string
with bound parameters, run in a READ transaction.
"""
from __future__ import annotations

import json

from neo4j import READ_ACCESS

from .config import driver, settings
from .engine import _unflatten

_driver = None


def _d():
    global _driver
    if _driver is None:
        _driver = driver()
    return _driver


def read(query: str, **params) -> list[dict]:
    s = settings()
    with _d().session(database=s.database, default_access_mode=READ_ACCESS) as session:
        return session.execute_read(lambda tx: tx.run(query, **params).data())


def _props(p: dict | None, drop=("origin", "load_id")) -> dict | None:
    if p is None:
        return None
    return {k: v for k, v in _unflatten(p).items() if k not in drop}


RULE_SUMMARY = """
RETURN r.id AS rule_id, r.catalogue AS catalogue, r.domain AS domain, r.rule_type AS rule_type,
       r.modality AS modality,
       coalesce(r.rulespeak_statement, r.instruction_statement, r.candidate_content) AS statement,
       r.instruction_code AS instruction_code, r.effective_from AS effective_from,
       r.regulatory_instrument AS instrument, r.provision AS provision, r.source_version AS source_version,
       r.automation_class AS automation_class, r.automation_class_as_published AS automation_class_as_published,
       r.provenance_status AS provenance_status, r.label AS label
ORDER BY r.catalogue DESC, r.id"""


def list_products():
    return read("""MATCH (v:Visa) OPTIONAL MATCH (parent:Visa)-[:PARENT_OF]->(v)
                   RETURN v.id AS id, v.name AS name, v.provenance_status AS provenance_status,
                          parent.id AS parent_id ORDER BY v.id""")


def list_domains():
    return read("MATCH (r:Rule) RETURN r.domain AS domain, r.catalogue AS catalogue, count(*) AS rules "
                "ORDER BY domain, catalogue")


def rules_by_domain(domain: str, include_candidates: bool):
    return read("MATCH (r:Rule) WHERE toLower(r.domain) = toLower($d) "
                "AND ($c OR r.catalogue IN ['VERIFIED', 'INSTRUCTION']) "
                + RULE_SUMMARY, d=domain, c=include_candidates)


def resolve_concepts(term: str):
    return read("""CALL db.index.fulltext.queryNodes('concept_terms', $q) YIELD node, score
                   WHERE node.kind IN ['Concept','Actor','Product']
                   RETURN node.id AS concept_id, node.name AS term, node.kind AS kind,
                          node.concept_type AS concept_group, score ORDER BY score DESC LIMIT 5""",
                q=_ft(term))


def rules_by_concept(concept_id: str):
    return read("MATCH (r:Rule)-[:REFERENCES|IMPOSES_OBLIGATION_ON|PERMITS|PROHIBITS]->(c:Domain {id:$c}) "
                "WITH DISTINCT r " + RULE_SUMMARY, c=concept_id)


def _ft(text: str) -> str:
    # escape Lucene syntax so caller text is treated as plain words
    special = '+-&|!(){}[]^"~*?:\\/'
    return " ".join("".join("\\" + ch if ch in special else ch for ch in w) for w in text.split())


def search(text: str, limit: int, include_history: bool = False):
    rules = read("CALL db.index.fulltext.queryNodes('rule_text', $q) YIELD node AS r, score "
                 "WITH r, score ORDER BY score DESC LIMIT $n "
                 "RETURN r.id AS rule_id, coalesce(r.rulespeak_statement, r.instruction_statement, "
                 "r.candidate_content) AS statement, r.instruction_code AS instruction_code, "
                 "r.regulatory_instrument AS instrument, r.provision AS provision, r.automation_class AS "
                 "automation_class, r.catalogue AS catalogue, r.provenance_status AS provenance_status, score",
                 q=_ft(text), n=limit)
    provs = read("CALL db.index.fulltext.queryNodes('provision_text', $q) YIELD node AS p, score "
                 "WHERE $h OR coalesce(p.version_kind, 'CURRENT') <> 'HISTORIC' "
                 "WITH p, score ORDER BY score DESC LIMIT $n "
                 "RETURN p.id AS provision_id, p.reference AS reference, p.source_id AS source_id, "
                 "p.heading AS heading, p.source_text_status AS source_text_status, p.source_url AS source_url, "
                 "p.source_version AS source_version, p.version_kind AS version_kind, "
                 "p.effective_from AS effective_from, p.effective_to AS effective_to, "
                 "p.source_text AS source_text, score", q=_ft(text), n=limit, h=include_history)
    return {"rules": rules, "provisions": provs}


def rule_detail(rule_id: str):
    r = read("MATCH (r:Rule {id:$id}) RETURN properties(r) AS p", id=rule_id)
    if not r:
        return None
    rule = _props(r[0]["p"])
    rule["provisions"] = read("""
        MATCH (r:Rule {id:$id})-[d:DERIVED_FROM]->(p:Provision)
        OPTIONAL MATCH (src:AuthoritySource)-[:CONTAINS]->(top:Provision)
                       -[:PARENT_OF*0..]->(p)
        RETURN p.id AS provision_id, p.reference AS reference, p.provision_type AS provision_type,
               d.primary AS primary, p.source_text AS source_text, p.source_text_status AS source_text_status,
               p.source_url AS source_url, p.source_version AS source_version, p.retrieved_at AS retrieved_at,
               src.id AS source_id, src.name AS instrument, src.version_id AS source_version_id
        ORDER BY d.primary DESC, p.id""", id=rule_id)
    rule["qualifiers"] = read("""
        MATCH (r:Rule {id:$id})-[rel:HAS_THRESHOLD]->(q:Threshold)
        RETURN 'Threshold' AS type, q.id AS id, q.text AS text, q.value AS value, q.unit AS unit, q.per AS per,
               q.comparator AS comparator, q.label AS label, q.provenance_status AS provenance_status,
               q.executable AS executable ORDER BY id""", id=rule_id)
    rule["qualifier_phrases"] = {k: rule.get(k) or [] for k in ("conditions", "exceptions", "time_constraints",
                                                                   "triggers")}
    rule["actors"] = read("""MATCH (r:Rule {id:$id})-[rel:IMPOSES_OBLIGATION_ON|PERMITS|PROHIBITS]->(a)
                             RETURN type(rel) AS relationship, a.id AS actor_id, a.name AS actor""", id=rule_id)
    rule["concepts"] = read("MATCH (r:Rule {id:$id})-[:REFERENCES]->(c) RETURN c.id AS concept_id, c.name AS term",
                            id=rule_id)
    rule["evidence_requirements"] = evidence_for_rule(rule_id)
    rule["decision_checks"] = read("MATCH (c:DecisionCheck)-[:EVALUATES]->(r:Rule {id:$id}) "
                                   "RETURN c.id AS check_id, c.purpose AS purpose, c.automation_class AS "
                                   "automation_class, c.defined_in AS defined_in", id=rule_id)
    rule["coverage_gaps"] = read("MATCH (g:CoverageGap)-[:APPLIES_TO]->(r:Rule {id:$id}) "
                                 "RETURN g.id AS id, g.subject AS subject, g.control AS control", id=rule_id)
    rule["interpretation_flags"] = read("MATCH (f:InterpretationFlag) WHERE $id IN f.applies_to OR "
                                        "f.id IN coalesce($flags, []) RETURN f.id AS flag_id, f.title AS title, "
                                        "f.kind AS kind, f.status AS status ORDER BY f.id",
                                        id=rule_id, flags=rule.get("interpretation_flag_ids"))
    rule["transitions_authorised"] = read("MATCH (t:StateTransition)-[:DERIVED_FROM]->(r:Rule {id:$id}) "
                                          "RETURN t.id AS transition_id, t.entity_type AS entity, "
                                          "t.from_state_id AS from_state, t.to_state_id AS to_state", id=rule_id)
    return rule


def evidence_for_rule(rule_id: str):
    return read("""MATCH (r:Rule {id:$id})-[q:REQUIRES_EVIDENCE]->(et:Evidence)
                   RETURN q.requirement_id AS evidence_requirement_id, q.description AS description,
                          q.mandatory AS mandatory, q.verification_required AS verification_required,
                          et.id AS evidence_type_id, et.name AS evidence_item, et.supports_fact AS supports_fact,
                          et.verification_checks AS verification_checks ORDER BY et.id""", id=rule_id)


def facts_supported_by_evidence(evidence: str):
    types = read("""MATCH (et:Evidence) WHERE et.kind='EvidenceType' AND
                      (toLower(et.id) = toLower($e) OR toLower(et.name) CONTAINS toLower($e)
                       OR toLower($e) CONTAINS toLower(et.name))
                    OPTIONAL MATCH (r:Rule)-[:REQUIRES_EVIDENCE]->(et)
                    RETURN et.id AS evidence_type_id, et.name AS evidence_item, et.supports_fact AS supports_fact,
                           et.verification_need AS verification_need, et.verification_checks AS verification_checks,
                           et.typical_rule_domain AS typical_rule_domain, collect(DISTINCT r.id) AS rules""", e=evidence)
    observed = read("""MATCH (e:EvidenceItem)-[:SUPPORTS_FACT]->(o:ObservedFact)
                       WHERE toLower(e.evidence_type) CONTAINS toLower($e) OR toLower($e) CONTAINS toLower(e.evidence_type)
                       OPTIONAL MATCH (o)-[:VERIFIED_AS]->(v:VerifiedFact)
                       RETURN e.evidence_type AS evidence_type, o.attribute AS attribute,
                              count(DISTINCT o) AS observed, count(DISTINCT v) AS verified
                       ORDER BY evidence_type, attribute LIMIT 50""", e=evidence)
    return {"evidence_types": types, "facts_seen_in_synthetic_assessments": observed}


def decision_checks():
    rows = read("""MATCH (c:DecisionCheck) OPTIONAL MATCH (c)-[:EVALUATES]->(r:Rule)
                   RETURN c.id AS check_id, c.purpose AS purpose, c.automation_class AS automation_class,
                          c.doc_routing_logic AS routing_logic_as_published, c.defined_in AS defined_in,
                          c.label AS label, c.deviation AS deviation, c.interpretation_flag_ids AS flags,
                          collect(r.id) AS evaluates ORDER BY c.id""")
    return rows


def lifecycle(entity_type: str | None):
    return read("""MATCH (t:StateTransition) WHERE $e IS NULL OR toLower(t.entity_type) CONTAINS toLower($e)
                   OPTIONAL MATCH (t)-[:DERIVED_FROM]->(r:Rule)
                   OPTIONAL MATCH (trig:Trigger)-[:CAUSES_TRANSITION]->(t)
                   RETURN t.id AS transition_id, t.entity_type AS entity, t.from_state_id AS from_state,
                          t.to_state_id AS to_state, trig.text AS trigger, t.actor_authority AS actor_authority,
                          collect(DISTINCT r.id + ' (' + r.provision + ')') AS authorising_rules
                   ORDER BY t.entity_type DESC, t.id""", e=entity_type)


def coverage_gaps():
    gaps = read("""MATCH (g:CoverageGap) OPTIONAL MATCH (g)-[:APPLIES_TO]->(x)
                   RETURN g.id AS id, g.subject AS subject, g.effect AS effect, g.control AS control,
                          g.required_result AS required_result, g.must_not_infer AS must_not_infer,
                          collect(x.id) AS applies_to ORDER BY g.id""")
    sources = read("MATCH (s:AuthoritySource) RETURN s.id AS source_id, s.name AS title, "
                   "s.coverage_status AS coverage_status, s.poc_coverage AS poc_coverage ORDER BY s.hierarchy_level")
    text = read("MATCH (p:Provision) RETURN p.source_text_status AS status, p.source_id AS source_id, "
                "count(*) AS provisions ORDER BY source_id")
    return {"coverage_gaps": gaps, "sources": sources, "provision_text_status": text}


def interpretation_flags():
    return read("MATCH (f:InterpretationFlag) RETURN f.id AS flag_id, f.kind AS kind, f.title AS title, "
                "f.detail AS detail, f.status AS status, f.decision_reference AS decision, f.resolution AS resolution, "
                "f.applies_to AS applies_to ORDER BY f.id")


def explain_evaluation(evaluation_id: str):
    rows = read("""
        MATCH (ev:RuleEvaluation {id:$id})-[:EVALUATES]->(r:Rule)
        MATCH (c:DecisionCheck)-[:PRODUCES_OUTCOME]->(ev)
        OPTIONAL MATCH (o:DecisionOutcome {kind:'ASSESSMENT'})-[:CONTAINS]->(ev)
        OPTIONAL MATCH (r)-[:DERIVED_FROM {primary:true}]->(p:Provision)
        OPTIONAL MATCH (src:AuthoritySource)-[:CONTAINS]->(top:Provision)
                       -[:PARENT_OF*0..]->(p)
        RETURN properties(ev) AS ev, c.id AS check_id, c.purpose AS check_purpose, c.defined_in AS check_defined_in,
               r.id AS rule_id, coalesce(r.rulespeak_statement, r.instruction_statement, r.candidate_content)
               AS statement,
               r.catalogue AS catalogue, r.provenance_status AS provenance_status,
               p.id AS provision_id, p.reference AS provision, p.source_text AS source_text,
               p.source_text_status AS source_text_status, src.version_id AS source_version_id, src.id AS source_id,
               src.name AS instrument, ev.explanation AS explanation, ev.result AS outcome,
               o.id AS assessment_id""", id=evaluation_id)
    if not rows:
        return None
    r = rows[0]
    ev = _props(r["ev"])
    facts = read("""MATCH (f)-[i:INPUT_TO]->(:DecisionCheck) WHERE $id IN i.evaluation_ids
                    OPTIONAL MATCH (e:EvidenceItem)-[:SUPPORTS_FACT]->(o:ObservedFact)
                    WHERE o = f OR (o)-[:VERIFIED_AS]->(f)
                    RETURN DISTINCT [l IN labels(f) WHERE l <> 'Evidence'][0] AS fact_kind, f.id AS fact_id,
                           f.attribute AS attribute, f.value AS value_json, f.verification_status AS verification_status,
                           collect(DISTINCT e.id + ' (' + e.evidence_type + ')') AS evidence""", id=evaluation_id)
    chain = [
        {"step": "RuleEvaluation", "id": evaluation_id, "result": ev["result"], "explanation": r["explanation"]},
        {"step": "DecisionCheck", "id": r["check_id"], "purpose": r["check_purpose"], "defined_in": r["check_defined_in"]},
        {"step": "Rule", "id": r["rule_id"], "statement": r["statement"], "catalogue": r["catalogue"],
         "provenance_status": r["provenance_status"]},
    ]
    if r["provision_id"]:
        chain += [
            {"step": "Provision", "id": r["provision_id"], "reference": r["provision"],
             "source_text": r["source_text"], "source_text_status": r["source_text_status"]},
            {"step": "AuthoritySource", "id": r["source_id"], "instrument": r["instrument"],
             "version": r["source_version_id"]},
        ]
    else:
        chain.append({"step": "CoverageGap", "id": "GAP-01",
                      "note": "Candidate rule: no authoritative provision in the POC corpus (RECONCILIATION_REQUIRED)."})
    for f in facts:
        f["value"] = json.loads(f.pop("value_json")) if f.get("value_json") else None
    return {"explanation_chain": chain, "facts_and_evidence": facts, "evaluation": ev,
            "complete": bool(r["provision_id"] and r["source_id"])}


def list_assessments(application_id: str | None, limit: int):
    return read("""MATCH (ap:Application)-[:PRODUCES_OUTCOME]->(o:DecisionOutcome {kind:'ASSESSMENT'})
                   WHERE $a IS NULL OR ap.id = $a
                   RETURN o.id AS assessment_id, ap.id AS application_id, o.overall_status AS overall_status,
                          o.written_at AS written_at, o.production_decision AS production_decision
                   ORDER BY o.written_at DESC LIMIT $n""", a=application_id, n=limit)


def get_assessment(assessment_id: str):
    o = read("MATCH (o:DecisionOutcome {id:$id, kind:'ASSESSMENT'}) RETURN properties(o) AS p", id=assessment_id)
    if not o:
        return None
    evs = read("""MATCH (:DecisionOutcome {id:$id})-[:CONTAINS]->(ev:RuleEvaluation)
                  RETURN ev.id AS evaluation_id, ev.rule_id AS rule_id, ev.check_id AS check_id, ev.result AS result,
                         ev.automation_class AS automation_class, ev.provision AS provision,
                         ev.regulatory_instrument AS instrument, ev.label AS label,
                         ev.unresolved_requirements AS unresolved ORDER BY ev.id""", id=assessment_id)
    return {"assessment": _props(o[0]["p"]), "rule_evaluations": evs}


def test_cases():
    return read("MATCH (t:TestCase) OPTIONAL MATCH (t)-[:EVALUATES]->(c:DecisionCheck) "
                "RETURN t.id AS test_case_id, t.scenario AS scenario, t.expected_outcome AS "
                "expected_outcome, t.test_type AS test_type, t.expected_status AS expected_status, t.deviation AS deviation, "
                "c.id AS check_id ORDER BY t.id")


# --------------------------------------------------------------------------- Operational Manual
INSTR = """RETURN p.id AS provision_id, p.instruction_code AS code, p.heading AS heading,
       p.version_kind AS version_kind, p.effective_from AS text_in_force_from, p.effective_to AS effective_to,
       p.published_effective_from AS page_effective_date, p.effective_from AS effective_from,
       p.source_text AS text, p.source_url AS url, p.retrieved_at AS retrieved_at"""


def list_instructions(prefix: str):
    return read("MATCH (p:Provision:Instruction) WHERE p.version_kind IN ['CURRENT','EXPIRED'] "
                "AND p.instruction_code STARTS WITH $x RETURN p.instruction_code AS code, p.heading AS heading, "
                "p.version_kind AS version_kind, p.effective_from AS effective_from ORDER BY code", x=prefix)


def instruction_text(code: str, as_at: str | None):
    if as_at:
        rows = read("MATCH (p:Provision:Instruction {instruction_code:$c}) "
                    "WHERE p.effective_from IS NULL OR p.effective_from <= $d "
                    "WITH p WHERE p.effective_to IS NULL OR p.effective_to > $d " + INSTR +
                    " ORDER BY p.effective_from DESC LIMIT 1", c=code, d=as_at)
    else:
        rows = read("MATCH (p:Provision:Instruction {instruction_code:$c}) "
                    "WHERE p.version_kind IN ['CURRENT','EXPIRED'] " + INSTR, c=code)
    if not rows:
        return None
    r = rows[0]
    r["sub_instructions"] = read("MATCH (:Provision {id:$id})-[:PARENT_OF]->(c:Instruction) "
                                 "RETURN c.instruction_code AS code, c.heading AS heading ORDER BY c.id",
                                 id=r["provision_id"])
    r["references"] = read("""MATCH (:Provision {id:$id})-[:PARENT_OF*0..4]->(x)-[:REFERENCES]->(t:Provision)
                              RETURN DISTINCT t.id AS provision_id, coalesce(t.instruction_code, t.reference) AS ref,
                                     t.heading AS heading ORDER BY ref""", id=r["provision_id"])
    r["rules"] = read("""MATCH (:Provision {id:$id})-[:PARENT_OF*0..4]->(x)<-[:DERIVED_FROM]-(rule:Rule)
                         RETURN rule.provision AS paragraph, rule.id AS rule_id,
                                rule.instruction_statement AS statement, rule.automation_class AS automation_class,
                                rule.modality AS modality ORDER BY rule.id""",
                      id=r["provision_id"])
    r["as_at"] = as_at
    return r


def instruction_history(code: str):
    return read("MATCH (p:Provision:Instruction {instruction_code:$c}) " + INSTR.replace(
        "p.source_text AS text", "size(p.source_text) AS text_length") + " ORDER BY p.effective_from DESC", c=code)


def rules_by_instruction(code: str):
    return read("MATCH (r:Rule) WHERE r.instruction_code = $c OR r.instruction_code STARTS WITH $c + '.' "
                + RULE_SUMMARY.replace("ORDER BY r.catalogue DESC, r.id", "ORDER BY r.id"), c=code)
