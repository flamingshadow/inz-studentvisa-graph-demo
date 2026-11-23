"""Phase 4: controlled decision engine.

Input: an application JSON (header + F.6 evidence items + F.7 facts).
Output: an assessment in the 12.2 shape. Always production_decision=false.

Safeguards (Section 9 + product-owner decisions):
  * A1 runs on structured data; A2 runs only on VERIFIED facts, otherwise UNKNOWN.
  * A3 -> HUMAN_REVIEW_REQUIRED; A4 -> DISCRETION_REQUIRED with authority and constraints;
    A5 -> stop, AMBIGUOUS.
  * Missing evidence is UNKNOWN, never NOT_SATISFIED.
  * Health, character and genuine intentions are never scored.
  * The final grant is always DISCRETION_REQUIRED (SV-CHECK-DISC-001); no automated grant or decline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .docspec import ROOT

OUTCOMES = ["SATISFIED", "NOT_SATISFIED", "UNKNOWN", "NOT_APPLICABLE", "HUMAN_REVIEW_REQUIRED",
            "DISCRETION_REQUIRED", "AMBIGUOUS", "SOURCE_COVERAGE_GAP"]
ROUTE = {"A3": "HUMAN_REVIEW_REQUIRED", "A4": "DISCRETION_REQUIRED", "A5": "AMBIGUOUS"}
CANDIDATE_LABEL = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"
POC_NOTICE = ("POC only - synthetic data - not a production visa decision. The final grant is always "
              "DISCRETION_REQUIRED and rests with the Minister or an Immigration Officer.")


# --------------------------------------------------------------------------- input models
class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_item_id: str
    application_id: str | None = None
    evidence_type: str
    document_reference: str | None = None
    supplied_by_actor_id: str | None = None
    received_at: str | None = None
    verification_status: Literal["VERIFIED", "UNVERIFIED"] = "UNVERIFIED"
    supports_fact_ids: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    model_config = ConfigDict(extra="allow")
    fact_id: str
    application_id: str | None = None
    subject_id: str | None = None
    attribute: str
    value: Any
    data_type: str | None = None
    evidence_item_ids: list[str] = Field(default_factory=list)
    verification_status: Literal["VERIFIED", "UNVERIFIED"] = "UNVERIFIED"
    verified_at: str | None = None


class Application(BaseModel):
    model_config = ConfigDict(extra="allow")
    application_id: str
    product_id: Literal["STUDENT-VISA"] = "STUDENT-VISA"
    synthetic: Literal[True] = Field(description="Decision 4: synthetic applications only")
    assessment_as_at: str | None = None
    channel: Literal["paper", "online"] = "paper"
    applicant: dict = Field(default_factory=dict)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


# --------------------------------------------------------------------------- spec stores
def _unflatten(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        if k.endswith("_json"):
            out[k[:-5]] = json.loads(v)
        else:
            out[k] = v
    return out


class FileStore:
    """Reads spec objects from the Appendix A files."""

    def __init__(self):
        j = lambda rel: json.loads((ROOT / rel).read_text())
        rules = j("rules/rules.json")
        self.rules = {r["rule_id"]: r for r in rules["rules"] + rules["candidate_rules"]
                      + rules.get("instruction_rules", [])}
        self.checks = {c["check_id"]: c for c in j("decisioning/decision-checks.json")["decision_checks"]}
        self.thresholds = {t["id"]: t for t in j("rules/thresholds.json")["thresholds"]}
        self.sources = {s["source_id"]: s for s in j("regulatory/sources.json")["sources"]}
        self.provisions = {p["provision_id"]: p for p in j("regulatory/provisions.json")["provisions"]}
        self.gaps = {g["id"]: g for g in j("governance/source-coverage.json")["coverage_gaps"]}
        self.discretions = {d["id"]: d for d in j("rules/discretions.json")["discretions"]}
        self.exceptions = {d["id"]: d for d in j("rules/exceptions.json")["exceptions"]}
        self.conditions = {d["id"]: d for d in j("rules/conditions.json")["conditions"]}
        from .graph import qualifier_texts
        for rid, qt in qualifier_texts().items():     # same rule properties the graph loader writes (IF-35)
            if rid in self.rules:
                self.rules[rid].update(conditions=qt.get("conditions"), exceptions=qt.get("exceptions"),
                                       discretion_holder=qt.get("discretion_holder"))
        self.origin = "files"

    def rule(self, rid):
        return self.rules[rid]

    def check(self, cid):
        return self.checks[cid]


class GraphStore(FileStore):
    """Reads the same objects from the live `studentvisa` database (read transaction)."""

    def __init__(self, session):
        def fetch(label):
            res = session.execute_read(lambda tx: tx.run(
                f"MATCH (n:{label}) WHERE n.origin='SPEC' RETURN properties(n) AS p").data())
            return {r["p"]["id"]: _unflatten(r["p"]) for r in res}
        self.rules = fetch("Rule")
        self.checks = fetch("DecisionCheck")
        self.thresholds = fetch("Threshold")
        self.sources = {k: v for k, v in fetch("AuthoritySource").items()}
        self.provisions = fetch("Provision")
        self.gaps = fetch("CoverageGap")
        self.discretions, self.exceptions, self.conditions = {}, {}, {}   # rule properties since IF-35
        if not self.rules or not self.checks:
            raise RuntimeError("studentvisa database has no spec nodes; run `svkg load` first")
        self.origin = "graph"


# --------------------------------------------------------------------------- evaluation context
@dataclass
class Ctx:
    app: Application
    facts: dict[str, Fact]
    evidence: dict[str, EvidenceItem]
    missing: list[dict] = field(default_factory=list)

    @classmethod
    def build(cls, app: Application):
        return cls(app, {f.attribute: f for f in app.facts}, {e.evidence_item_id: e for e in app.evidence_items})


class Missing(Exception):
    pass


class Eval:
    """Collects facts/evidence used and unresolved requirements for one rule evaluation."""

    def __init__(self, ctx: Ctx, rule: dict, check: dict, cls: str | None):
        self.ctx, self.rule, self.check, self.cls = ctx, rule, check, cls
        self.used: list[Fact] = []
        self.unresolved: list[str] = []
        self.notes: list[str] = []
        self.extra: dict = {}
        self.evidence_ids: set[str] = set()

    def get(self, attr: str, *, required=True, verified: bool | None = None):
        """Return a fact value. A2 (or verified=True) needs a VERIFIED fact."""
        need_verified = (self.cls == "A2") if verified is None else verified
        f = self.ctx.facts.get(attr)
        path = f"application.{attr}"
        if f is None:
            if required:
                self.unresolved.append(f"Provide {path}")
                self.ctx.missing.append({"rule_id": self.rule["rule_id"], "check_id": self.check["check_id"],
                                         "fact_path": path, "reason": "MISSING",
                                         "request": f"Request evidence establishing {attr.replace('_', ' ')}"})
                raise Missing(path)
            return None
        self.used.append(f)
        self.evidence_ids.update(f.evidence_item_ids)
        if need_verified and f.verification_status != "VERIFIED":
            self.unresolved.append(f"Verify {path} (fact {f.fact_id} is {f.verification_status})")
            self.ctx.missing.append({"rule_id": self.rule["rule_id"], "check_id": self.check["check_id"],
                                     "fact_path": path, "reason": "UNVERIFIED",
                                     "request": f"Verify fact {f.fact_id} against its evidence"})
            raise Missing(path)
        return f.value


def _cls(rule: dict, check: dict) -> str | None:
    c = check.get("automation_class")
    return rule["automation"]["class"] if c in (None, "PER_RULE") else c


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, store: FileStore | None = None):
        self.store = store or FileStore()
        self._n = 0

    # ---- record builder --------------------------------------------------------------
    def _record(self, assessment_id, ev: Eval, result: str, explanation: str, **extra) -> dict:
        assert result in OUTCOMES, result
        self._n += 1
        rule, check = ev.rule, ev.check
        a = rule["authority"]
        pid = a.get("provision_id")
        prov = self.store.provisions.get(pid) if pid else None
        src = self.store.sources.get(a.get("source_id")) if a.get("source_id") else None
        rec = {
            "evaluation_id": f"EVAL-{assessment_id.removeprefix('ASSESS-')}-{self._n:03d}",
            "application_id": ev.ctx.app.application_id,
            "rule_id": rule["rule_id"], "check_id": check["check_id"], "result": result,
            "verified_fact_ids": [f.fact_id for f in ev.used if f.verification_status == "VERIFIED"],
            "evidence_item_ids": sorted(ev.evidence_ids),
            "evaluated_by": "RULE_ENGINE",
            "explanation": explanation,
            "source_id": a.get("source_id"), "provision_id": pid,
            # Section 9: rule, facts, evidence, unresolved, class, instrument, provision, version
            "facts_used": [{"fact_id": f.fact_id, "attribute": f.attribute, "value": f.value,
                            "verification_status": f.verification_status,
                            "evidence_item_ids": f.evidence_item_ids} for f in ev.used],
            "observed_fact_ids": [f.fact_id for f in ev.used if f.verification_status != "VERIFIED"],
            "unresolved_requirements": ev.unresolved,
            "automation_class": ev.cls,
            "automation_class_as_published": rule["automation"].get("class_as_published"),
            "rulespeak_statement": rule.get("rulespeak_statement") or rule.get("instruction_statement")
                                   or rule.get("candidate_content"),
            "regulatory_instrument": a.get("regulatory_instrument") or (src or {}).get("title"),
            "provision": a.get("provision"), "provision_ids": a.get("provision_ids", []),
            "source_version": a.get("source_version"),
            "source_text_status": (prov or {}).get("source_text_status"),
            "provenance_status": rule["provenance_status"],
            "catalogue": rule.get("catalogue"),
            "notes": ev.notes,
            **ev.extra, **extra,
        }
        return rec

    def _missing(self, assessment_id, ev: Eval, path: str):
        return self._record(assessment_id, ev, "UNKNOWN",
                            f"Required fact {path} is missing or unverified, so {ev.rule['rule_id']} cannot be "
                            f"evaluated. Missing evidence is UNKNOWN, not failure.")

    # ---- generic routing (A3/A4/A5/gap) ------------------------------------------------
    def route(self, aid, ctx, rule_id, check, reason=None, **extra):
        rule = self.store.rule(rule_id)
        cls = _cls(rule, check) if check.get("evaluates_rule_id") == rule_id else rule["automation"]["class"]
        ev = Eval(ctx, rule, check, cls)
        if rule["automation"].get("coverage_gap_ids") and rule.get("catalogue") == "VERIFIED" and \
                "gap" in (rule["automation"].get("class_as_published") or "").lower():
            return self._gap(aid, ev)
        res = ROUTE.get(cls)
        if res is None:
            raise ValueError(f"{rule_id} is {cls}; route() is for A3/A4/A5")
        expl = {"A3": "Assisted human assessment (A3): rules and evidence retrieved and referred to a human.",
                "A4": "Statutory discretion (A4): authority and constraints presented; the decision-maker decides.",
                "A5": "Ambiguous or non-executable (A5): evaluation stopped and escalated."}[cls]
        if res == "DISCRETION_REQUIRED":
            extra.setdefault("authority_presented", self._authority(rule))
        return self._record(aid, ev, res, (reason + " " if reason else "") + expl, **extra)

    def _authority(self, rule):
        """Discretion details come from the rule itself (IF-35): its subject, conditions and exceptions."""
        return {"rule_id": rule["rule_id"],
                "power": rule.get("rulespeak_statement") or rule.get("instruction_statement"),
                "decision_maker": rule.get("discretion_holder") or rule.get("subject"),
                "regulatory_instrument": rule["authority"]["regulatory_instrument"],
                "provision": rule["authority"]["provision"],
                "constraints": list(rule.get("conditions") or []) + list(rule.get("exceptions") or [])}

    def _gap(self, aid, ev: Eval):
        gid = (ev.rule["automation"].get("coverage_gap_ids") or ["GAP-01"])[0]
        gap = self.store.gaps.get(gid, {})
        ev.unresolved.append("Authoritative Immigration Instructions are not in the POC corpus (GAP-01)")
        return self._record(aid, ev, "SOURCE_COVERAGE_GAP",
                            f"{ev.rule['rule_id']} depends on Immigration Instructions, which are not in the "
                            f"verified corpus. No rule is inferred from the Act or Regulations.",
                            coverage_gap_id=gid, missing_source_type=gap.get("missing_source_type"),
                            must_not_infer=True)

    # ---- simple A1/A2 boolean helper ---------------------------------------------------
    def _bool(self, aid, ctx, rule_id, check, attr, *, true="SATISFIED", false="NOT_SATISFIED", why=""):
        rule = self.store.rule(rule_id)
        ev = Eval(ctx, rule, check, rule["automation"]["class"])
        try:
            v = ev.get(attr)
        except Missing as m:
            return self._missing(aid, ev, str(m))
        res = true if v is True else false
        return self._record(aid, ev, res, f"application.{attr} = {v!r} -> {res}. {why}".strip())

    # ---- checks ------------------------------------------------------------------------
    def check_app_001(self, aid, ctx, check):
        out = []
        if ctx.app.channel == "online":
            simple = {"SV-ONLINE-001": "online_form_used", "SV-ONLINE-002": "completed_in_english",
                      "SV-ONLINE-003": "full_name_stated", "SV-ONLINE-004": "date_and_place_of_birth_stated",
                      "SV-ONLINE-005": "passport_details_stated", "SV-ONLINE-008": "declaration_acknowledged",
                      "SV-ONLINE-009": "change_notification_agreed", "SV-ONLINE-010": "prescribed_fee_paid",
                      "SV-ONLINE-011": "immigration_levy_paid"}
            for rid, attr in simple.items():
                out.append(self._bool(aid, ctx, rid, check, attr))
            rule = self.store.rule("SV-ONLINE-006")
            ev = Eval(ctx, rule, check, rule["automation"]["class"])
            try:
                if ev.get("form_requires_photograph", required=False, verified=False) is False:
                    out.append(self._record(aid, ev, "NOT_APPLICABLE", "The electronic form does not require a photograph."))
                else:
                    v = ev.get("photograph_uploaded")
                    out.append(self._record(aid, ev, "SATISFIED" if v else "NOT_SATISFIED",
                                            f"Compliant photograph uploaded: {v!r}."))
            except Missing as m:
                out.append(self._missing(aid, ev, str(m)))
            return out
        out.append(self._bool(aid, ctx, "SV-FORM-001", check, "approved_form_used"))
        # SV-FORM-002: one person unless regulation 20 applies (exception)
        rule = self.store.rule("SV-FORM-002")
        ev = Eval(ctx, rule, check, "A1")
        try:
            n = ev.get("persons_in_application")
            if n == 1:
                out.append(self._record(aid, ev, "SATISFIED", "The application relates to one person."))
            else:
                r20 = ev.get("regulation_20_applies")
                out.append(self._record(aid, ev, "NOT_APPLICABLE" if r20 else "NOT_SATISFIED",
                                        f"{n} persons; regulation 20 applies: {r20!r} (exception "
                                        f"{rule['qualifiers']['exception_ids']})."))
        except Missing as m:
            out.append(self._missing(aid, ev, str(m)))
        out.append(self._bool(aid, ctx, "SV-FORM-003", check, "completed_in_english"))
        rule = self.store.rule("SV-FORM-004")
        ev = Eval(ctx, rule, check, "A1")
        try:
            signed = ev.get("application_signed")
            if signed:
                out.append(self._record(aid, ev, "SATISFIED", "The application is signed."))
            else:
                minor = ev.get("minor_signature_rule_applies")
                out.append(self._record(aid, ev, "NOT_APPLICABLE" if minor else "NOT_SATISFIED",
                                        f"Unsigned; minor-signature rule applies: {minor!r}."))
        except Missing as m:
            out.append(self._missing(aid, ev, str(m)))
        out.append(self._bool(aid, ctx, "SV-ID-001", check, "identity_document_provided",
                              why="Identity document must be verified (A2)."))
        rule = self.store.rule("SV-PHOTO-001")
        ev = Eval(ctx, rule, check, "A2")
        th = self.store.thresholds.get(rule["qualifiers"]["threshold_ids"][0]) if rule["qualifiers"]["threshold_ids"] else None
        try:
            n = ev.get("photographs_provided")
            need = th["value"] if th else 2
            out.append(self._record(aid, ev, "SATISFIED" if n >= need else "NOT_SATISFIED",
                                    f"{n} photograph(s) provided; required {need} ({th['id'] if th else 'statement'}).",
                                    threshold_ids=[th["id"]] if th else []))
        except Missing as m:
            out.append(self._missing(aid, ev, str(m)))
        out.append(self._bool(aid, ctx, "SV-FEE-001", check, "prescribed_fee_paid"))
        out.append(self._bool(aid, ctx, "SV-FEE-002", check, "immigration_levy_paid"))
        return out

    def check_ivl_001(self, aid, ctx, check):
        rid = check["evaluates_rule_id_online"] if ctx.app.channel == "online" else check["evaluates_rule_id"]
        rule = self.store.rule(rid)
        exc = self.store.rule(check["exception_rule_id"])
        ev = Eval(ctx, rule, check, "A1")
        admin = ev.get("scholarship_administrator", required=False)
        if isinstance(admin, str) and admin.strip().upper() == "MFAT":
            return [self._record(aid, ev, "NOT_APPLICABLE",
                                 f"Student Visa applicant supported by an MFAT-administered scholarship: the IVL "
                                 f"payment rule does not apply under the exemption {exc['rule_id']} "
                                 f"({exc['authority']['provision']}).",
                                 exception_rule_id=exc["rule_id"],
                                 exception_provision=exc["authority"]["provision"],
                                 provision_ids=rule["authority"]["provision_ids"] + exc["authority"]["provision_ids"])]
        try:
            paid = ev.get("ivl_paid")
        except Missing as m:
            return [self._missing(aid, ev, str(m))]
        return [self._record(aid, ev, "SATISFIED" if paid else "NOT_SATISFIED",
                             f"IVL paid: {paid!r}; no MFAT scholarship exemption recorded.")]

    def check_u10_001(self, aid, ctx, check):
        rule = self.store.rule("SV-U10-001")
        ev = Eval(ctx, rule, check, "A1")
        ev.notes.append("Provider obligation under the Code (RISK-04): reported for information, not a "
                        "visa-grant criterion (IF-21).")
        try:
            age = ev.get("applicant_age")
            th = self.store.thresholds.get("SV-TH-U10-001", {"value": 10})
            if age >= th["value"]:
                return [self._record(aid, ev, "NOT_APPLICABLE", f"Applicant age {age} is not under {th['value']}.")]
            lives = ev.get("lives_with_parent_or_legal_guardian")
        except Missing as m:
            return [self._missing(aid, ev, str(m))]
        return [self._record(aid, ev, "SATISFIED" if lives else "NOT_SATISFIED",
                             f"Learner under 10 lives with a parent or legal guardian: {lives!r} (cl 51).")]

    def check_work_001(self, aid, ctx, check):
        rule = self.store.rule("SV-WORK-001")
        ev = Eval(ctx, rule, check, "A2")
        ev.notes.append("Subject to the authoritative visa-condition record; standard conditions are not assumed.")
        try:
            permitted = ev.get("work_permitted_by_visa_conditions")
            if not permitted:
                return [self._record(aid, ev, "NOT_SATISFIED", "The visa conditions do not permit work.")]
            limit = ev.get("visa_condition_max_work_hours_per_week")
            hours = ev.get("recorded_work_hours_per_week")
            ev.get("activity_date")
        except Missing as m:
            return [self._missing(aid, ev, str(m))]
        res = "SATISFIED" if hours <= limit else "NOT_SATISFIED"
        return [self._record(aid, ev, res, f"Recorded work {hours} h/week against a verified visa-condition limit "
                                           f"of {limit} h/week -> {res}.")]

    def check_fin_001(self, aid, ctx, check):
        if str(check.get("evaluates_rule_id", "")).startswith("SV-OM-"):
            return self._check_fin_opsmanual(aid, ctx, check)
        return self._check_fin_candidate(aid, ctx, check)

    def _nzd(self, rule_id):
        rule = self.store.rule(rule_id)
        for tid in rule["qualifiers"].get("threshold_ids", []):
            t = self.store.thresholds.get(tid)
            if t and t.get("unit") == "NZD":
                return t
        raise ValueError(f"{rule_id} has no NZD threshold")

    def _check_fin_opsmanual(self, aid, ctx, check):
        """Operational Manual U3.20.25 / U3.20.30 / U3.20.35 (IF-30)."""
        import math
        lab = {"provenance_status": "VERIFIED_INSTRUCTION_SOURCE", "interpretation_flag_ids": ["IF-30"],
               "related_verified_rule_ids": check.get("related_verified_rule_ids", [])}
        probe = Eval(ctx, self.store.rule("SV-OM-U3.20.30-A-I"), check, "A2")
        try:
            stype = str(probe.get("student_type")).lower()
            weeks_f = probe.get("programme_duration_weeks", required=False)
            months_f = probe.get("study_duration_months", required=weeks_f is None)
        except Missing as m:
            return [{**self._missing(aid, probe, str(m)), **lab}]
        level = ("compulsory" if stype in ("school", "compulsory", "primary", "secondary", "intermediate") else
                 "tertiary" if stype in ("tertiary", "non-compulsory", "tertiary or non-compulsory") else None)
        if level is None:
            probe.unresolved.append(f"student_type {stype!r} is neither tertiary/non-compulsory nor compulsory")
            return [self._record(aid, probe, "UNKNOWN", f"U3.20 sets figures for tertiary or non-compulsory and "
                                                        f"for compulsory study; {stype!r} is neither.", **lab)]
        weeks = float(weeks_f) if weeks_f is not None else float(months_f) * 52 / 12
        months = float(months_f) if months_f is not None else weeks * 12 / 52
        long = weeks >= 36
        idx = "I" if level == "tertiary" else "II"
        rid = f"SV-OM-U3.20.30-A-{idx}" if long else f"SV-OM-U3.20.25-A-{idx}"
        out = []
        # Samoa / Tonga written guarantee (U3.20.30(a)(iii))
        cit = ctx.facts.get("citizenship")
        if long and cit and str(cit.value).strip().lower() in ("samoa", "tonga", "samoan", "tongan") and \
                "maintenance_guarantee_from_nz_relative" in ctx.facts:
            gev = Eval(ctx, self.store.rule("SV-OM-U3.20.30-A-III"), check, "A2")
            try:
                gev.get("citizenship")
                g = gev.get("maintenance_guarantee_from_nz_relative")
                if g is True:
                    return [self._record(aid, gev, "SATISFIED", "Citizen of Samoa or Tonga with a verified written "
                                                                "guarantee of maintenance from a relative in New "
                                                                "Zealand (U3.20.30(a)(iii)).", **lab)]
            except Missing as m:
                out.append({**self._missing(aid, gev, str(m)), **lab})
        ev = Eval(ctx, self.store.rule(rid), check, "A2")
        try:
            ev.get("student_type")
            ev.get("programme_duration_weeks" if weeks_f is not None else "study_duration_months")
            funds = ev.get("available_funds_nzd")
            prepaid = ev.get("prepaid_living_expenses_nzd", required=False) or 0
        except Missing as m:
            return out + [{**self._missing(aid, ev, str(m)), **lab}]
        th = self._nzd(rid)
        if long:
            years = max(1.0, months / 12)
            required = round(th["value"] * years)
            calc = f"NZ${th['value']:,.0f} per year x {years:g} year(s)"
        else:
            n = math.ceil(round(months, 2))
            required = round(th["value"] * n)
            calc = f"NZ${th['value']:,.0f} per month x {n} month(s)"
        net = max(0, required - prepaid)
        if prepaid:
            calc += f", less prepaid living expenses NZ${prepaid:,.0f}"
        ev.notes.append("Other ways to show funds under U3.20(b): full scholarship, sponsorship (INZ 1025, "
                        "U3.20.5) or financial undertaking (INZ 1014, U3.20.10).")
        res = "SATISFIED" if funds >= net else "NOT_SATISFIED"
        if res == "NOT_SATISFIED":
            ev.unresolved.append("Funds held by the student fall short; check whether a scholarship, sponsorship "
                                 "or financial undertaking is offered instead (U3.20(b))")
        out.append(self._record(aid, ev, res,
                                f"Programme of {weeks:.0f} weeks ({'36 weeks or longer' if long else 'under 36 weeks'})"
                                f", {level} study: verified funds NZ${funds:,.0f} against NZ${net:,.0f} required "
                                f"({calc}) -> {res}. Source: {ev.rule['authority']['provision']}, effective "
                                f"{ev.rule['authority']['source_version']}.",
                                threshold_ids=[th["id"]], instruction_code=ev.rule.get("instruction_code"),
                                calculation={"required_nzd": net, "gross_required_nzd": required, "method": calc,
                                             "available_nzd": funds, "programme_weeks": round(weeks, 1)}, **lab))
        # maternity (U3.20.35), only when recorded
        key = "pregnant_due_to_give_birth_in_nz" if "pregnant_due_to_give_birth_in_nz" in ctx.facts else \
            ("pregnant" if "pregnant" in ctx.facts else None)
        if key:
            mev = Eval(ctx, self.store.rule("SV-OM-U3.20.35-P1-A"), check, "A2")
            try:
                if not mev.get(key):
                    out.append(self._record(aid, mev, "NOT_APPLICABLE",
                                            "Applicant is not due to give birth in New Zealand.", **lab))
                    return out
                pub_key = "eligible_for_publicly_funded_maternity_services" \
                    if "eligible_for_publicly_funded_maternity_services" in ctx.facts else \
                    "publicly_funded_maternity_care_available"
                if mev.get(pub_key, required=False):
                    out.append(self._record(aid, mev, "NOT_APPLICABLE",
                                            "Eligible for publicly funded maternity health services "
                                            "(U3.20.35(d)), so the funds limb (a) is not needed.", **lab))
                    return out
                mth = self._nzd("SV-OM-U3.20.35-P1-A")
                need = net + mth["value"]
                r = "SATISFIED" if funds >= need else "NOT_SATISFIED"
                if r == "NOT_SATISFIED":
                    mev.unresolved.append("Alternatives U3.20.35(b)-(d) not evidenced")
                out.append(self._record(aid, mev, r, f"Funds NZ${funds:,.0f} against maintenance NZ${net:,.0f} plus "
                                                     f"maternity NZ${mth['value']:,.0f} (U3.20.35(a)) -> {r}.",
                                        threshold_ids=[mth["id"]], **lab))
            except Missing as m:
                out.append({**self._missing(aid, mev, str(m)), **lab})
        return out

    def _check_fin_candidate(self, aid, ctx, check):
        out = []
        T = self.store.thresholds
        stype_rule = {"tertiary": "SV-CAND-MAINT-TERTIARY", "school": "SV-CAND-MAINT-SCHOOL"}
        lab = {"label": CANDIDATE_LABEL, "provenance_status": "RECONCILIATION_REQUIRED",
               "interpretation_flag_ids": ["IF-01", "IF-15"],
               "related_verified_rule_ids": check.get("related_verified_rule_ids", [])}
        probe = Eval(ctx, self.store.rule("SV-CAND-MAINT-TERTIARY"), check, "A2")
        try:
            stype = probe.get("student_type")
        except Missing as m:
            return [{**self._missing(aid, probe, str(m)), **lab}]
        rid = stype_rule.get(str(stype).lower())
        if rid is None:
            probe.unresolved.append(f"student_type {stype!r} has no candidate threshold")
            return [self._record(aid, probe, "UNKNOWN", f"No candidate maintenance figure for student type {stype!r}.", **lab)]
        rule = self.store.rule(rid)
        ev = Eval(ctx, rule, check, "A2")
        try:
            ev.get("student_type")
            months = ev.get("study_duration_months")
            funds = ev.get("available_funds_nzd")
        except Missing as m:
            return [{**self._missing(aid, ev, str(m)), **lab}]
        tag = rid.removeprefix("SV-CAND-MAINT-")
        yearly, monthly = T[f"SV-TH-CAND-{tag}-YEAR"], T[f"SV-TH-CAND-{tag}-MONTH"]
        if months < 12:
            required, used, calc = monthly["value"] * months, [monthly["id"]], f"{months} x NZD {monthly['value']:,}"
        else:
            required = round(yearly["value"] * months / 12)
            used, calc = [yearly["id"]], f"NZD {yearly['value']:,} x {months}/12"
        if ev.get("prepaid_accommodation", required=False, verified=False):
            ev.unresolved.append("Prepaid accommodation declared: Appendix E names an adjustment but gives no "
                                 "figure, so it is not applied (IF-15)")
        res = "SATISFIED" if funds >= required else "NOT_SATISFIED"
        out.append(self._record(aid, ev, res,
                                f"[{CANDIDATE_LABEL}] Verified funds NZD {funds:,} against candidate maintenance "
                                f"requirement NZD {required:,} ({calc}) -> {res}. Candidate figure; not a "
                                f"reconciled Immigration Instruction threshold.",
                                threshold_ids=used, calculation={"required_nzd": required, "method": calc,
                                                                 "available_nzd": funds}, **lab))
        # maternity (only when pregnancy is recorded)
        mrule = self.store.rule("SV-CAND-MATERNITY")
        mev = Eval(ctx, mrule, check, "A2")
        if "pregnant" in ctx.facts:
            try:
                preg = mev.get("pregnant")
                if not preg:
                    out.append(self._record(aid, mev, "NOT_APPLICABLE", "Applicant is not pregnant.", **lab))
                else:
                    avail = mev.get("publicly_funded_maternity_care_available")
                    if avail:
                        out.append(self._record(aid, mev, "NOT_APPLICABLE",
                                                "Publicly funded maternity care is available.", **lab))
                    else:
                        mev.get("available_funds_nzd")
                        th = T["SV-TH-CAND-MATERNITY-TOTAL"]
                        need = required + th["value"]
                        r = "SATISFIED" if funds >= need else "NOT_SATISFIED"
                        out.append(self._record(aid, mev, r, f"[{CANDIDATE_LABEL}] Funds NZD {funds:,} against "
                                                             f"maintenance NZD {required:,} + candidate maternity "
                                                             f"NZD {th['value']:,} -> {r}.",
                                                threshold_ids=[th["id"]], **lab))
            except Missing as m:
                out.append({**self._missing(aid, mev, str(m)), **lab})
        return out

    def check_disc_001(self, aid, ctx, check, prior: list[dict]):
        rule = self.store.rule("SV-DEC-002")
        ev = Eval(ctx, rule, check, "A4")
        counts = {}
        for e in prior:
            counts[e["result"]] = counts.get(e["result"], 0) + 1
        context = []
        for rid in check.get("context_rule_ids", []):
            r = self.store.rules.get(rid)
            if r:
                context.append({"rule_id": rid, "statement": r["rulespeak_statement"],
                                "provision": r["authority"]["provision"], "automation_class": r["automation"]["class"]})
        return [self._record(aid, ev, "DISCRETION_REQUIRED",
                             "The final grant is always routed to the authorised decision-maker "
                             "(SV-CHECK-DISC-001). A visa is not an entitlement (SV-DEC-001). The engine makes no "
                             "grant or decline.",
                             authority_presented={**self._authority(rule), "statutory_context": context},
                             evaluations_presented=counts)]

    def check_app_005(self, aid, ctx, check):
        rule = self.store.rule("SV-APP-005")
        ev = Eval(ctx, rule, check, "A2")
        try:
            occurred = ev.get("material_change_occurred")
            if occurred is False:
                return [self._record(aid, ev, "NOT_APPLICABLE", "No material change in circumstances recorded.")]
            disclosed = ev.get("material_change_disclosed")
        except Missing as m:
            return [self._missing(aid, ev, str(m))]
        return [self._record(aid, ev, "SATISFIED" if disclosed is True else "NOT_SATISFIED",
                             f"application.material_change_disclosed EQUALS true: {disclosed is True}.")]

    def check_min_001(self, aid, ctx, check):
        rule = self.store.rule("SV-MIN-001")
        ev = Eval(ctx, rule, check, "A4")
        th = self.store.thresholds.get("SV-TH-MIN-001", {"value": 18})
        try:
            age = ev.get("applicant_age", verified=False)
            if age >= th["value"]:
                return [self._record(aid, ev, "NOT_APPLICABLE", f"Applicant is {age}, not under {th['value']}.")]
            status = ev.get("applicant_marital_status", verified=False)
        except Missing as m:
            return [self._missing(aid, ev, str(m))]
        if str(status).lower() not in ("unmarried", "single", "never married"):
            return [self._record(aid, ev, "NOT_APPLICABLE", f"Applicant marital status {status!r}: s 59 "
                                                            f"applies to unmarried applicants.")]
        consent = ctx.facts.get("parental_consent_established")
        if consent is not None and consent.verification_status == "VERIFIED" and consent.value is True:
            ev.used.append(consent)
            ev.evidence_ids.update(consent.evidence_item_ids)
            return [self._record(aid, ev, "NOT_APPLICABLE", "Parental or guardian consent is established "
                                                            "(verified), so the s 59 power does not arise.")]
        ev.unresolved.append("Request parental or guardian consent evidence")
        ctx.missing.append({"rule_id": "SV-MIN-001", "check_id": check["check_id"],
                            "fact_path": "application.parental_consent_established",
                            "reason": "MISSING" if consent is None else "UNVERIFIED",
                            "request": "Request parental or legal guardian consent evidence"})
        return [self.route(aid, ctx, "SV-MIN-001", check,
                           reason=f"Unmarried applicant aged {age} (under {th['value']}); parental or guardian "
                                  f"consent not established.",
                           unresolved_requirements=ev.unresolved,
                           facts_used=[{"fact_id": f.fact_id, "attribute": f.attribute, "value": f.value,
                                        "verification_status": f.verification_status,
                                        "evidence_item_ids": f.evidence_item_ids} for f in ev.used],
                           evidence_item_ids=sorted(ev.evidence_ids),
                           verified_fact_ids=[f.fact_id for f in ev.used if f.verification_status == "VERIFIED"])]

    def check_health_001(self, aid, ctx, check):
        rule = self.store.rule(check.get("evaluates_rule_id") or "SV-CAND-ELIGIBILITY")
        ev = Eval(ctx, rule, check, "A3")
        med = [e for e in ctx.evidence.values() if e.evidence_type in ("MedicalCertificate", "ChestXray", "ChestXRay")]
        for f in ctx.facts.values():
            if any(e.evidence_item_id in f.evidence_item_ids for e in med):
                ev.used.append(f)
        ev.evidence_ids.update(e.evidence_item_id for e in med)
        ev.notes.append("Health is never scored (Section 9). Related verified rule SV-HEALTH-001 (r 10(3)(c)) "
                        "lets the officer require a medical examination; health requirements are in A4.")
        extra = {"related_verified_rule_ids": ["SV-HEALTH-001"], "interpretation_flag_ids": ["IF-18"]}
        if not med:
            ev.unresolved.append("No medical certificate or chest X-ray received")
            ctx.missing.append({"rule_id": rule["rule_id"], "check_id": check["check_id"],
                                "fact_path": "evidence.MedicalCertificate", "reason": "MISSING",
                                "request": "Request medical certificate / chest X-ray if required"})
            return [self._record(aid, ev, "UNKNOWN", "No medical evidence received; health cannot be referred yet.", **extra)]
        return [self._record(aid, ev, "HUMAN_REVIEW_REQUIRED",
                             f"Medical evidence received ({', '.join(sorted(e.evidence_type for e in med))}); the "
                             f"health assessment is referred to a human (A3). No automated health outcome.", **extra)]

    def check_edu_009(self, aid, ctx, check):
        stype = ctx.facts.get("student_type")
        rid = check["evaluates_rule_id_school"] if stype and str(stype.value).lower() == "school" else check["evaluates_rule_id"]
        rule = self.store.rule(rid)
        ev = Eval(ctx, rule, check, "A3")
        ev.notes.append("Provider obligation (RISK-04), not a visa-grant criterion. IF-14.")
        known = ctx.facts.get("provider_known_visa_condition_breach")
        reported = ctx.facts.get("provider_reported_breach_to_inz")
        for f in (known, reported, ctx.facts.get("provider_suspected_visa_condition_breach")):
            if f is not None:
                ev.used.append(f)
                ev.evidence_ids.update(f.evidence_item_ids)
        verified = lambda f: f is not None and f.verification_status == "VERIFIED"
        if verified(known) and known.value is True and verified(reported):
            res = "NOT_SATISFIED" if reported.value is False else "SATISFIED"
            return [self._record(aid, ev, res, f"Verified: provider knew of a visa-condition breach; reported to "
                                               f"INZ: {reported.value!r} -> {res}.",
                                 interpretation_flag_ids=["IF-14"])]
        return [self.route(aid, ctx, rid, {**check, "automation_class": "A3"},
                           reason="Breach is suspected or the provider facts are not verified.")]

    def check_instr_001(self, aid, ctx, check):
        rid = check["evaluates_rule_id_online"] if ctx.app.channel == "online" else check["evaluates_rule_id"]
        rule = self.store.rule(rid)
        codes = check.get("instruction_codes") or []
        if not codes:
            return [self._gap(aid, Eval(ctx, rule, check, rule["automation"]["class"]))]
        listed = sorted(r["rule_id"] for r in self.store.rules.values()
                        if r.get("catalogue") == "INSTRUCTION" and r.get("instruction_code") in codes)
        hev = Eval(ctx, rule, check, "A3")
        hev.unresolved += [f"Verify {r}" for r in listed]
        out = [self._record(aid, hev, "HUMAN_REVIEW_REQUIRED",
                            f"Immigration Instructions {', '.join(codes)} are in the corpus. Their requirements "
                            f"without an executable check are listed for the officer to verify (IF-33).",
                            instruction_rules_presented=listed)]
        gev = Eval(ctx, rule, check, rule["automation"]["class"])
        out.append({**self._gap(aid, gev), "explanation":
                    "Immigration Instructions outside the loaded scope (Student Visas U1-U16 and the sections they "
                    "cite directly) are not in the corpus. No rule is inferred for them."})
        return out

    def check_lodg_001(self, aid, ctx, check):
        """Operational Manual E4.50.1 / E4.50.2 / E4.50.35 lodgement requirements (IF-36)."""
        lab = {"provenance_status": "VERIFIED_INSTRUCTION_SOURCE", "interpretation_flag_ids": ["IF-36"]}
        pairs = []  # (rule_id, evaluator(ev) -> (result, explanation))

        def rec(rid, fn):
            rule = self.store.rule(rid)
            ev = Eval(ctx, rule, check, rule["automation"]["class"])
            try:
                result, why = fn(ev)
            except Missing as m:
                return {**self._missing(aid, ev, str(m)), **lab}
            return self._record(aid, ev, result, why, **lab)

        def flag(attr, label):
            def fn(ev):
                v = ev.get(attr)
                return ("SATISFIED" if v is True else "NOT_SATISFIED"), f"{label}: application.{attr} = {v!r}."
            return fn

        def all_of(*attrs):
            def fn(ev):
                vals = {a: ev.get(a) for a in attrs}
                ok = all(v is True for v in vals.values())
                return ("SATISFIED" if ok else "NOT_SATISFIED"), \
                       "; ".join(f"{a} = {v!r}" for a, v in vals.items()) + "."
            return fn

        def photos(ev):
            n = ev.get("photographs_provided")
            return ("SATISFIED" if n >= 2 else "NOT_SATISFIED"), f"{n} photograph(s) provided; two are required."

        def online_photo(ev):
            if ev.get("form_requires_photograph", required=False, verified=False) is False:
                return "NOT_APPLICABLE", "The electronic form did not prompt for a photograph."
            v = ev.get("photograph_uploaded")
            return ("SATISFIED" if v else "NOT_SATISFIED"), f"Photograph uploaded after prompt: {v!r}."

        def signed(ev):
            if ev.get("applicant_age") >= 18:
                v = ev.get("application_signed")
                return ("SATISFIED" if v else "NOT_SATISFIED"), f"Adult applicant; signed by applicant: {v!r}."
            v = ev.get("signed_by_parent_or_guardian")
            return ("SATISFIED" if v else "NOT_SATISFIED"), f"Applicant under 18; signed by parent or guardian: {v!r}."

        def one_person(ev):
            n = ev.get("persons_in_application")
            return ("SATISFIED" if n == 1 else "NOT_SATISFIED"), f"The application relates to {n} person(s)."

        def ica_adult_signature(ev):
            if ev.get("applicant_age") >= 18:
                v = ev.get("application_signed")
                return ("SATISFIED" if v else "NOT_SATISFIED"), f"Signed by the applicant: {v!r}."
            return "NOT_APPLICABLE", "Applicant is under 18; E4.50.35(b) applies instead."

        def ica_minor(accompanied_expected):
            def fn(ev):
                if ev.get("applicant_age") >= 18:
                    return "NOT_APPLICABLE", "Applicant is 18 or over; (a)(iv) applies."
                acc = ev.get("accompanied_by_parent_or_guardian")
                if bool(acc) != accompanied_expected:
                    return "NOT_APPLICABLE", f"Accompanied by parent or guardian: {acc!r}; the other limb applies."
                if accompanied_expected:
                    v = ev.get("signed_by_parent_or_guardian")
                    return ("SATISFIED" if v else "NOT_SATISFIED"), f"Accompanied minor; parent or guardian signed: {v!r}."
                return "SATISFIED", "Unaccompanied minor: the arrival card is not required to be signed."
            return fn

        route_ev = Eval(ctx, self.store.rule("SV-OM-E4.50.1-P1-A"), check, "A1")
        try:
            route = str(route_ev.get("lodgement_route")).lower()
        except Missing as m:
            return [{**self._missing(aid, route_ev, str(m)), **lab}]
        if route == "paper":
            pairs = [("SV-OM-E4.50.1-P1-A", all_of("approved_form_used", "completed_in_english",
                                                   "identity_document_provided")),
                     ("SV-OM-E4.50.1-P1-B", photos), ("SV-OM-E4.50.1-P2", signed)]
        elif route == "online":
            pairs = [("SV-OM-E4.50.2-B-I", flag("full_name_stated", "Full name stated")),
                     ("SV-OM-E4.50.2-B-II", flag("date_and_place_of_birth_stated", "Date and place of birth stated")),
                     ("SV-OM-E4.50.2-B-III", flag("passport_details_stated", "Passport details stated")),
                     ("SV-OM-E4.50.2-B-IV", online_photo),
                     ("SV-OM-E4.50.2-B-VI", flag("declaration_acknowledged", "Declaration acknowledged")),
                     ("SV-OM-E4.50.2-B-VII", flag("change_notification_agreed", "Change notification agreed"))]
        elif route == "ica":
            pairs = [("SV-OM-E4.50.35-A-I", flag("approved_form_used", "Approved form used")),
                     ("SV-OM-E4.50.35-A-II", flag("completed_in_english", "Completed in English")),
                     ("SV-OM-E4.50.35-A-III", one_person),
                     ("SV-OM-E4.50.35-A-IV", ica_adult_signature),
                     ("SV-OM-E4.50.35-B-I", ica_minor(True)),
                     ("SV-OM-E4.50.35-B-II", ica_minor(False))]
        else:
            route_ev.unresolved.append(f"lodgement_route {route!r} is not paper, online or ica")
            return [self._record(aid, route_ev, "UNKNOWN", f"No E4 lodgement rules are defined for route {route!r}.", **lab)]
        return [rec(rid, fn) for rid, fn in pairs]

    # ---- domain checks: Family, Pathway, Sponsorship, Providers, Work rights (IF-37) ------------
    def _th(self, rule_id):
        """First numeric threshold value on a rule."""
        for tid in self.store.rule(rule_id)["qualifiers"].get("threshold_ids", []):
            t = self.store.thresholds.get(tid)
            if t and isinstance(t.get("value"), (int, float)):
                return t["value"]
        raise ValueError(f"{rule_id} has no numeric threshold")

    def _dom_tests(self):
        def at_most(attr, limit, unit):
            def fn(ev):
                v = ev.get(attr)
                return ("SATISFIED" if v <= limit else "NOT_SATISFIED"), f"{attr} = {v} {unit}; maximum {limit} {unit}."
            return fn

        def at_least(attr, minimum, unit):
            def fn(ev):
                v = ev.get(attr)
                return ("SATISFIED" if v >= minimum else "NOT_SATISFIED"), f"{attr} = {v} {unit}; minimum {minimum} {unit}."
            return fn

        def truth(attr, label):
            def fn(ev):
                v = ev.get(attr)
                return ("SATISFIED" if v is True else "NOT_SATISFIED"), f"{label}: {attr} = {v!r}."
            return fn

        def income(rid, types):
            def fn(ev):
                t = str(ev.get("supporting_work_visa_type")).strip().lower()
                if t not in types:
                    return "NOT_APPLICABLE", f"The supporting work visa type {t!r} is not one this threshold covers."
                need = self._th(rid)
                v = ev.get("supporting_parent_gross_income_nzd")
                return ("SATISFIED" if v >= need else "NOT_SATISFIED"), \
                       f"Gross income NZ${v:,.0f}; minimum NZ${need:,.0f} per annum."
            return fn

        def duration(rid, in_nz):
            def fn(ev):
                here = ev.get("applicant_in_new_zealand")
                if bool(here) != in_nz:
                    return "NOT_APPLICABLE", f"applicant_in_new_zealand = {here!r}; the other limb applies."
                v, cap = ev.get("visa_duration_months"), self._th(rid)
                return ("SATISFIED" if v <= cap else "NOT_SATISFIED"), f"Visa duration {v} months; maximum {cap:g} months."
            return fn

        def third_party_person(ev):
            if str(ev.get("third_party_type")).strip().lower() != "person":
                return "NOT_APPLICABLE", "The third party is not a person."
            rel = str(ev.get("third_party_relationship")).strip().lower()
            return ("SATISFIED" if rel in ("relative", "friend") else "NOT_SATISFIED"), \
                   f"Third party is a person; relationship to applicant: {rel!r}."

        def contact(attr, applies):
            def fn(ev):
                if not applies(ev.get(attr)):
                    return "NOT_APPLICABLE", f"{attr} = {ev.get(attr)!r}; a nominated contact is not required."
                v = ev.get("authorised_contact_nominated")
                return ("SATISFIED" if v is True else "NOT_SATISFIED"), f"Authorised contact nominated: {v!r}."
            return fn

        def eer4(ev):
            c = ev.get("provider_eer_category")
            return ("NOT_SATISFIED" if str(c).strip() in ("4", "Four", "four") else "SATISFIED"), \
                   f"Provider NZQA EER category: {c!r}; Category Four providers are excluded."

        def minor_work(ev):
            age = ev.get("applicant_age")
            if not (16 <= age <= 17) or ev.get("enrolled_at_tertiary_or_pte") is not True:
                return "NOT_APPLICABLE", f"Age {age}; applies to 16-17 year olds at a tertiary provider or PTE."
            prov, par = ev.get("provider_written_permission"), ev.get("parental_written_consent")
            return ("SATISFIED" if prov is True and par is True else "NOT_SATISFIED"), \
                   f"Provider written permission: {prov!r}; parental written consent: {par!r}."

        def child_under_20(ev):
            if ev.get("dependent_child_included") is not True:
                return "NOT_APPLICABLE", "No dependent child is included in the application."
            age = ev.get("child_age")
            return ("SATISFIED" if age < 20 else "NOT_SATISFIED"), f"Dependent child aged {age}; must be under 20."

        def staff_for_minors(ev):
            if ev.get("applicant_age") >= 18:
                return "NOT_APPLICABLE", "The learner is 18 or over."
            v = ev.get("provider_designated_staff_for_under_18s")
            return ("SATISFIED" if v is True else "NOT_SATISFIED"), f"Provider designated a staff member for under-18s: {v!r}."

        aewv = ("accredited employer", "specific purpose", "essential skills")
        return {
            "SV-FAM-002": child_under_20,
            "SV-U18-003": staff_for_minors,
            "SV-OM-U7.20.1-A": at_most("part_time_study_months", self._th("SV-OM-U7.20.1-A"), "months"),
            "SV-OM-U7.20.1-B": at_most("part_time_study_months", self._th("SV-OM-U7.20.1-B"), "months"),
            "SV-OM-U8.20.6-A": income("SV-OM-U8.20.6-A", aewv),
            "SV-OM-U8.20.10-B-I": income("SV-OM-U8.20.10-B-I", ("religious worker",)),
            "SV-OM-U8.30-B-I": duration("SV-OM-U8.30-B-I", False),
            "SV-OM-U8.30-B-II": duration("SV-OM-U8.30-B-II", True),
            "SV-OM-U14-A": at_most("pathway_programme_count", 3, "programmes"),
            "SV-OM-U14.10-A": at_most("pathway_visa_duration_months", 60, "months"),
            "SV-OM-U14.20.5-C": at_most("max_break_between_programmes_weeks", self._th("SV-OM-U14.20.5-C"), "weeks"),
            "SV-OM-U14.20.5-D": at_most("english_language_study_weeks", self._th("SV-OM-U14.20.5-D"), "weeks"),
            "SV-OM-U14.20.1-P1-D": at_least("provider_visa_approval_rate_percent", 90, "percent"),
            "SV-OM-U3.20.10-B": third_party_person,
            "SV-OM-U3.20.10-C": contact("third_party_type", lambda t: str(t).strip().lower() != "person"),
            "SV-OM-E6.5-E": contact("sponsor_is_natural_person", lambda n: n is False),
            "SV-OM-U5.1-A": truth("provider_certified_programme_compliance", "Provider certified compliance"),
            "SV-OM-U5.1-B": truth("provider_signatory_to_code", "Provider signatory to the Code"),
            "SV-OM-U5.1-D": eer4,
            "SV-OM-U13.15-B": minor_work,
        }

    def _domain_check(self, aid, ctx, check):
        lab = {"provenance_status": "VERIFIED_INSTRUCTION_SOURCE", "interpretation_flag_ids": ["IF-37"]}
        tests = self._dom_tests()
        out = []
        executed = check["executable_rule_ids"]
        for rid in executed:
            rule = self.store.rule(rid)
            ev = Eval(ctx, rule, check, rule["automation"]["class"])
            try:
                result, why = tests[rid](ev)
            except Missing as m:
                out.append({**self._missing(aid, ev, str(m)), **lab})
                continue
            out.append(self._record(aid, ev, result, why, **lab))
        rest = [r for r in check["evaluates_rule_ids"] if r not in executed]
        klass = lambda r: self.store.rule(r)["automation"].get("class")
        cand = [r for r in rest if self.store.rule(r).get("catalogue") == "CANDIDATE"]
        a4 = [r for r in rest if r not in cand and klass(r) == "A4"]
        a5 = [r for r in rest if r not in cand and klass(r) == "A5"]
        review = [r for r in rest if r not in cand and r not in a4 and r not in a5]
        dom = check["domain"]
        if cand:
            gev = Eval(ctx, self.store.rule(cand[0]), check, None)
            out.append({**self._gap(aid, gev), "rules_presented": cand, "interpretation_flag_ids": ["IF-38"],
                        "explanation":
                        f"{len(cand)} {dom} candidate rules are RECONCILIATION_REQUIRED: their source is not an "
                        f"authoritative corpus (GAP-01/GAP-02). No rule is inferred."})
        if a5:
            out.append(self.route(aid, ctx, a5[0], check, reason=f"{len(a5)} {dom} rules are ambiguous (A5).",
                                  instruction_rules_presented=a5, **lab))
        if review:
            ev = Eval(ctx, self.store.rule(review[0]), check, "A3")
            ev.unresolved += [f"Verify {r}" for r in review]
            out.append(self._record(
                aid, ev, "HUMAN_REVIEW_REQUIRED",
                f"{len(review)} {dom} rules have no executable test in the POC (A3, or A1/A2 without a clear "
                f"pass/fail condition). They are listed for the officer to verify (IF-37).",
                instruction_rules_presented=review, **lab))
        if a4:
            out.append(self.route(aid, ctx, a4[0], check,
                                  reason=f"{len(a4)} {dom} rules are discretionary (A4).",
                                  instruction_rules_presented=a4, **lab))
        return out

    def check_fam_001(self, aid, ctx, check):
        return self._domain_check(aid, ctx, check)

    def check_path_001(self, aid, ctx, check):
        return self._domain_check(aid, ctx, check)

    def check_spon_001(self, aid, ctx, check):
        return self._domain_check(aid, ctx, check)

    def check_prov_001(self, aid, ctx, check):
        return self._domain_check(aid, ctx, check)

    def check_wrk_001(self, aid, ctx, check):
        return self._domain_check(aid, ctx, check)

    HANDLERS = {
        "SV-CHECK-APP-001": "check_app_001", "SV-CHECK-IVL-001": "check_ivl_001",
        "SV-CHECK-U10-001": "check_u10_001", "SV-CHECK-WORK-001": "check_work_001",
        "SV-CHECK-FIN-001": "check_fin_001", "SV-CHECK-APP-005": "check_app_005",
        "SV-CHECK-MIN-001": "check_min_001", "SV-CHECK-HEALTH-001": "check_health_001",
        "SV-CHECK-EDU-009": "check_edu_009", "SV-CHECK-INSTR-001": "check_instr_001",
        "SV-CHECK-LODG-001": "check_lodg_001",
        "SV-CHECK-FAM-001": "check_fam_001", "SV-CHECK-PATH-001": "check_path_001",
        "SV-CHECK-SPON-001": "check_spon_001", "SV-CHECK-PROV-001": "check_prov_001",
        "SV-CHECK-WRK-001": "check_wrk_001",
    }

    # ---- assessment ------------------------------------------------------------------
    def assess(self, application: dict | Application, assessment_id: str | None = None) -> dict:
        app = application if isinstance(application, Application) else Application.model_validate(application)
        ctx = Ctx.build(app)
        now = datetime.now(timezone.utc)
        aid = assessment_id or f"ASSESS-{app.application_id}-{now.strftime('%Y%m%dT%H%M%S%f')}"
        self._n = 0
        evals, not_triggered = [], []
        for cid, handler in self.HANDLERS.items():
            if cid not in self.store.checks:  # LODG-001 exists only when the Operational Manual is loaded
                continue
            check = self.store.check(cid)
            triggers = [p.removeprefix("application.") for p in check.get("trigger_fact_paths", [])]
            if triggers and not any(t in ctx.facts for t in triggers):
                not_triggered.append(cid)
                continue
            evals += getattr(self, handler)(aid, ctx, check)
        for cid, check in self.store.checks.items():  # residual domain checks (IF-38)
            if check.get("residual"):
                triggers = [p.removeprefix("application.") for p in check.get("trigger_fact_paths", [])]
                if any(t in ctx.facts for t in triggers):
                    evals += self._domain_check(aid, ctx, check)
                else:
                    not_triggered.append(cid)
        evals += self.check_disc_001(aid, ctx, self.store.check("SV-CHECK-DISC-001"), evals)
        return self._assemble(aid, app, ctx, evals, not_triggered, now)

    def _assemble(self, aid, app, ctx, evals, not_triggered, now):
        c = lambda r: sum(1 for e in evals if e["result"] == r)
        results = {e["result"] for e in evals}
        overall = next((r for r in ("AMBIGUOUS", "NOT_SATISFIED", "UNKNOWN") if r in results),
                       "HUMAN_REVIEW_REQUIRED")
        human = [{"evaluation_id": e["evaluation_id"], "rule_id": e["rule_id"], "check_id": e["check_id"],
                  "result": e["result"], "automation_class": e["automation_class"],
                  "reason": e["explanation"],
                  "decision_maker": (e.get("authority_presented") or {}).get("decision_maker")
                                    or ("Immigration Officer" if e["result"] != "AMBIGUOUS" else "Escalation"),
                  "authority": {"regulatory_instrument": e["regulatory_instrument"], "provision": e["provision"]},
                  "constraints": (e.get("authority_presented") or {}).get("constraints", []),
                  "material_presented": e["evidence_item_ids"], "unresolved": e["unresolved_requirements"]}
                 for e in evals if e["result"] in ("HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED", "AMBIGUOUS")]
        gaps = [{"coverage_gap_id": e.get("coverage_gap_id"), "rule_id": e["rule_id"], "check_id": e["check_id"],
                 "kind": "SOURCE_COVERAGE_GAP", "missing_source_type": e.get("missing_source_type"),
                 "must_not_infer": True}
                for e in evals if e["result"] == "SOURCE_COVERAGE_GAP"]
        gaps += [{"coverage_gap_id": "GAP-01", "rule_id": e["rule_id"], "check_id": e["check_id"],
                  "kind": "CANDIDATE_FIGURE_USED", "label": e["label"], "interpretation_flag_ids": ["IF-01"],
                  "result": e["result"]}
                 for e in evals if e.get("label") == CANDIDATE_LABEL]
        src, prov = set(), set()
        for e in evals:
            if e.get("source_id"):
                src.add(e["source_id"])
            prov.update(e.get("provision_ids") or [])
        return {
            "assessment_id": aid, "application_id": app.application_id, "product_id": app.product_id,
            "assessment_as_at": app.assessment_as_at or now.date().isoformat(),
            "scope": {"source_coverage": "PARTIAL", "production_decision": False},
            "summary": {"overall_status": overall, "satisfied": c("SATISFIED"), "not_satisfied": c("NOT_SATISFIED"),
                        "unknown": c("UNKNOWN"), "human_review_required": c("HUMAN_REVIEW_REQUIRED"),
                        "discretion_required": c("DISCRETION_REQUIRED"),
                        "source_coverage_gaps": c("SOURCE_COVERAGE_GAP")},
            "rule_evaluations": evals,
            "missing_information": ctx.missing,
            "human_review": human,
            "source_coverage_gaps": gaps,
            "provenance": {"source_ids": sorted(src), "provision_ids": sorted(prov)},
        }


def check_result(assessment: dict, check_id: str, rule_id: str | None = None) -> str | None:
    """Aggregate result of one decision check (worst outcome wins)."""
    order = ["AMBIGUOUS", "NOT_SATISFIED", "UNKNOWN", "SOURCE_COVERAGE_GAP", "DISCRETION_REQUIRED",
             "HUMAN_REVIEW_REQUIRED", "SATISFIED", "NOT_APPLICABLE"]
    rs = [e["result"] for e in assessment["rule_evaluations"]
          if e["check_id"] == check_id and (rule_id is None or e["rule_id"] == rule_id)]
    if not rs:
        return None
    return min(rs, key=order.index)
