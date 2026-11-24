"""Turn an assessment into a short, structured digest for a person (or for Claude to put into words).

Everything here is derived from the assessment itself, deterministically: no wording is invented and no
grant or decline is ever recommended. Claude Desktop adds the natural language on top (see WRITING_GUIDANCE).
"""
from __future__ import annotations

from . import rag

POC_NOTICE = ("POC only. Synthetic data. This is decision support, not a production visa decision; "
              "the final grant is always the decision-maker's.")

WRITING_GUIDANCE = (
    "Write a short summary in plain New Zealand English for an immigration officer, about 150 to 200 words. "
    "Use these parts: (1) a one-sentence headline with the overall status; (2) what is met; (3) the key actions, "
    "numbered in priority order from key_actions, each saying who acts and quoting the rule ID and provision in "
    "brackets; (4) who decides. Use only facts in this digest. Never recommend granting or declining. Say it is a "
    "POC with synthetic data. Mention a candidate-figure label if any evaluation carries one. If expectations are "
    "present, say how many passed and name any that failed."
)

# priority: lower number = act first
_OWNER = {
    "NOT_SATISFIED": ("Case officer", 1, "RESOLVE"),
    "AMBIGUOUS": ("Case officer", 1, "ESCALATE"),
    "UNKNOWN": ("Case officer and applicant", 2, "OBTAIN_EVIDENCE"),
    "HUMAN_REVIEW_REQUIRED": ("Case officer", 3, "OFFICER_REVIEW"),
    "SOURCE_COVERAGE_GAP": ("Reference team", 4, "COVERAGE_GAP"),
    "DISCRETION_REQUIRED": ("Decision-maker", 5, "DECISION"),
}


def _short(text: str | None, n: int = 240) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _cite(e: dict) -> str:
    prov = e.get("provision") or "no provision"
    return f"{e['rule_id']}, {prov}"


_BATCH = {"OFFICER_REVIEW": "need officer review", "COVERAGE_GAP": "cannot be assessed in the POC corpus",
          "DECISION": "need a decision from the decision-maker"}


def _consolidate(actions: list[dict]) -> list[dict]:
    """Many similar review/gap/decision items become one action with a count, so the list stays readable.
    Anything that is not met, ambiguous or missing evidence stays as its own action, and so does the final grant."""
    out = [x for x in actions if x["type"] not in _BATCH or x["check_id"] == "SV-CHECK-DISC-001"]
    for kind, phrase in _BATCH.items():
        items = [x for x in actions if x["type"] == kind and x["check_id"] != "SV-CHECK-DISC-001"]
        if len(items) <= 3:
            out += items
            continue
        eg = ", ".join(f"{x['rule_id']} ({x['provision']})" if x["provision"] else x["rule_id"] for x in items[:3])
        first = items[0]
        out.append({"priority": first["priority"], "rag": first["rag"], "type": kind, "owner": first["owner"],
                    "action": f"{len(items)} items {phrase}, across {len({x['check_id'] for x in items})} check(s), "
                              f"e.g. {eg} and {len(items) - 3} more.",
                    "rule_id": first["rule_id"], "check_id": first["check_id"], "provision": first["provision"],
                    "evaluation_id": first["evaluation_id"], "items": [x["rule_id"] for x in items]})
    return out


def key_actions(a: dict) -> list[dict]:
    """One action per distinct thing a person must do, red first, de-duplicated."""
    actions: list[dict] = []
    seen: set[tuple] = set()

    def add(owner, priority, kind, text, e, rag_colour):
        key = (kind, text)
        if key in seen:
            return
        seen.add(key)
        actions.append({"priority": priority, "rag": rag_colour, "type": kind, "owner": owner, "action": text,
                        "rule_id": e["rule_id"], "check_id": e["check_id"], "provision": e.get("provision"),
                        "evaluation_id": e.get("evaluation_id")})

    missing = {}
    for m in a.get("missing_information", []):
        missing.setdefault(m["rule_id"], []).append(m)
    for e in a["rule_evaluations"]:
        res = e["result"]
        if res not in _OWNER:
            continue
        owner, prio, kind = _OWNER[res]
        colour = rag.of(res)["rag"]
        presented = e.get("instruction_rules_presented") or e.get("rules_presented") or []
        if res == "UNKNOWN":
            reqs = [m["request"] for m in missing.get(e["rule_id"], [])] or e.get("unresolved_requirements", [])
            text = "; ".join(dict.fromkeys(reqs)) or "Supply or verify the missing information"
            add(owner, prio, kind, f"{text} ({_cite(e)})", e, colour)
        elif res == "NOT_SATISFIED":
            add(owner, prio, kind, f"Not met: {_short(e['explanation'])} ({_cite(e)}). "
                                   f"Consider the shortfall with the applicant; the engine does not decide the outcome.",
                e, colour)
        elif res == "AMBIGUOUS":
            stmt = _short(e.get("rulespeak_statement"), 150)
            add(owner, prio, kind, f"The engine stopped because this rule is ambiguous or not executable"
                                   f"{': “' + stmt + '”' if stmt else ''} ({_cite(e)})", e, colour)
        elif res == "HUMAN_REVIEW_REQUIRED":
            if presented:
                add(owner, prio, kind, f"Review {len(presented)} requirement(s) that have no executable test "
                                       f"({_cite(e)}), e.g. {', '.join(presented[:3])}"
                                       f"{'…' if len(presented) > 3 else ''}", e, colour)
            else:
                add(owner, prio, kind, f"Officer to review: {_short(e['explanation'])} ({_cite(e)})", e, colour)
        elif res == "SOURCE_COVERAGE_GAP":
            add(owner, prio, kind, f"Cannot be assessed in the POC corpus ({_cite(e)}); check the Operational "
                                   f"Manual directly", e, colour)
        elif res == "DISCRETION_REQUIRED":
            auth = e.get("authority_presented") or {}
            who = auth.get("decision_maker") or "the decision-maker"
            cons = auth.get("constraints") or []
            tail = f" Constraints: {'; '.join(_short(c, 80) for c in cons[:3])}." if cons else ""
            if e["check_id"] == "SV-CHECK-DISC-001":
                add(owner, prio, kind, f"The final grant decision rests with {who} ({_cite(e)}).", e, colour)
            else:
                add(owner, prio, kind, f"{who} to decide ({_cite(e)}): {_short(auth.get('power') or e['explanation'], 160)}."
                    f"{tail}", e, colour)
    actions = _consolidate(actions)
    actions.sort(key=lambda x: (x["priority"], x["check_id"] == "SV-CHECK-DISC-001", x["rule_id"]))
    for i, x in enumerate(actions, 1):
        x["order"] = i
    return actions


def digest(a: dict, expectations: list[dict] | None = None, scenario: dict | None = None) -> dict:
    evs = a["rule_evaluations"]
    results = [e["result"] for e in evs]
    by_result: dict[str, int] = {}
    for r in results:
        by_result[r] = by_result.get(r, 0) + 1
    labels = sorted({e["label"] for e in evs if e.get("label")})
    out = {
        "poc_notice": POC_NOTICE,
        "assessment_id": a.get("assessment_id"),
        "application_id": a.get("application_id"),
        "assessment_as_at": a.get("assessment_as_at"),
        "production_decision": a["scope"]["production_decision"],
        "source_coverage": a["scope"]["source_coverage"],
        "overall": {"rag": rag.overall(results), "status": a["summary"]["overall_status"]},
        "rag_counts": rag.counts(results),
        "result_counts": by_result,
        "rules_evaluated": len(evs),
        "key_actions": key_actions(a),
        "met": [{"rule_id": e["rule_id"], "provision": e.get("provision"), "what": _short(e["explanation"], 140)}
                for e in evs if e["result"] == "SATISFIED"][:12],
        "candidate_figure_labels": labels,
        "writing_guidance": WRITING_GUIDANCE,
    }
    if scenario:
        out["scenario"] = {k: scenario.get(k) for k in ("id", "title", "scenario", "expected_outcome", "test_type")}
    if expectations is not None:
        passed = sum(1 for x in expectations if x["passed"])
        out["expectations"] = {"passed": passed, "total": len(expectations),
                               "failed": [x["text"] + " - " + x["detail"] for x in expectations if not x["passed"]]}
    return out
