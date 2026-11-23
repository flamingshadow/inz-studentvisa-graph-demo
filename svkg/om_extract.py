"""Turn the parsed Operational Manual (data/opsmanual/instructions.json) into graph objects.

Called by extract.Builder. Produces, for the Immigration Instructions source:
  * Provision:Instruction nodes for every instruction unit (e.g. U3.20, U3.20.25) in every
    version, Paragraph nodes for each lettered/roman item of current units, exact text,
    URL, effective_from/effective_to, and SUPERSEDES chains between versions (IF-28);
  * one atomic rule per leaf paragraph of every *current* unit (IF-27), with modality,
    automation class (heuristic, IF-26), thresholds, evidence, actors and concepts;
  * REFERENCES edges for cross-references to other instructions and to Act sections;
  * reconciliation of the Section 6 candidate rules to the instructions that cover them.
"""
from __future__ import annotations

import re

from . import opsmanual as om
from .legislation import clean

SID = om.SOURCE_ID
INSTRUMENT = "Immigration Instructions (INZ Operational Manual)"

# instruction code prefix -> rule domain (longest prefix wins; IF-32)
DOMAINS = {
    "U1": "Objectives", "U2": "Need for a student visa", "U3.1": "Eligibility summary", "U3.5": "Study",
    "U3.7": "Study", "U3.10": "Tuition", "U3.15": "Accommodation", "U3.20": "Maintenance",
    "U3.20.5": "Sponsorship", "U3.20.10": "Sponsorship", "U3.20.15": "Sponsorship", "U3.25": "Travel",
    "U3.30": "Minor/guardian", "U3.35": "Definitions", "U3.40": "Change of study",
    "U3.45": "Education/insurance", "U3.50": "COVID-19 measures", "U4": "Student categories",
    "U5": "Providers and programmes", "U6": "Obtaining student visas", "U6.30": "Visa conditions",
    "U6.35": "Visa conditions", "U6.40": "Visa conditions", "U7": "After grant", "U7.20": "Change of study",
    "U7.25": "Minor/guardian", "U8": "Family", "U9": "Vocational trainees", "U10": "Special categories",
    "U11": "Scholarship students", "U12": "Foreign government-supported students", "U13": "Work rights",
    "U14": "Pathway student visas", "U15": "English language programmes", "U16": "NZQA compliance",
    "A4": "Health", "A5": "Character", "A6": "Procedural", "E2": "Generic temporary entry",
    "E3": "Generic temporary entry", "E4": "Lodgement", "E5": "Bona fide applicants", "E6": "Sponsorship",
    "E7": "Health and character", "E11": "Generic temporary entry", "H2": "Special temporary visas",
    "H5": "COVID-19 measures", "S1": "Residence", "V2": "Visitor", "V3": "Visitor", "W2": "Work",
}
ACTOR_WORDS = [  # role words used as sentence subjects in the instructions -> actor IDs (IF-17, IF-26)
    (r"\b(immigration officers?|officers?)\b", ["ACTOR-IMMIGRATION-OFFICER"]),
    (r"\b(applicants?|principal applicant)\b", ["ACTOR-APPLICANT"]),
    (r"\b(student visa holders?|visa holders?|holders?|students?(?!\s+visas?\b)|trainees?|learners?|children|child)\b",
     ["ACTOR-STUDENT"]),
    (r"\b(sponsors?|third part(?:y|ies)|guarantors?)\b", ["ACTOR-SPONSOR"]),
    (r"\b(education providers?|providers?|schools?|tertiary institutions?|signator(?:y|ies))\b",
     ["ACTOR-EDUCATION-PROVIDER"]),
    (r"\b(legal guardians?|guardians?|parents?)\b", ["ACTOR-LEGAL-GUARDIAN"]),
    (r"\b(the minister)\b", ["ACTOR-MINISTER"]),
]
MODAL_RE = re.compile(r"\b(must not|must|may not|may|will not|will|should|shall|are required to|is required to|"
                      r"need to|needs to|are not eligible|is not eligible|are eligible|is eligible)\b", re.I)
# IF-32 (product owner, 23 Nov 2026): atomic rules only for the student sections and the general requirements
# every student must meet; other cited sections are loaded as quotable text only.
RULE_SECTIONS = ("A4", "A5", "E4", "E5", "E6.5", "E7")


def makes_rules(code: str) -> bool:
    return code.startswith("U") or any(code == p or code.startswith(p + ".") for p in RULE_SECTIONS)


def subject_actors(statement: str) -> list[str]:
    """Actor = role word nearest before the first modal verb (IF-26)."""
    m = MODAL_RE.search(statement)
    window = statement[:m.start()][-90:] if m else statement[:90]
    best, end = [], -1
    for pat, ids in ACTOR_WORDS:
        for hit in re.finditer(pat, window, re.I):
            if hit.end() > end:
                best, end = ids, hit.end()
    return best
EVIDENCE_WORDS = [  # keyword -> existing Table 9 evidence type (IF-26)
    (r"offer of (a )?place|confirmation of enrolment", "EVTYPE-OFFER-OF-PLACE"),
    (r"tuition fee", "EVTYPE-TUITION"), (r"medical certificate|chest x-ray|medical examination", "EVTYPE-MEDICAL"),
    (r"police certificate", "EVTYPE-POLICE-CERTIFICATE"), (r"\binsurance\b", "EVTYPE-INSURANCE"),
    (r"\bpassport|certificate of identity", "EVTYPE-PASSPORT"), (r"sponsorship", "EVTYPE-SPONSORSHIP"),
    (r"legal guardian|parent or guardian", "EVTYPE-GUARDIAN"),
    (r"evidence .{0,60}funds|funds .{0,40}available", "EVTYPE-BANK-STATEMENT"),
]
FORM_RE = re.compile(r"((?:[A-Z][\w’'\-]*\s(?:(?:for|of|and|to|the|a|on)\s){0,2}){1,7})\(INZ\s?(\d{3,4}[A-Z]?)\)")
CODE_REF_RE = re.compile(r"\b([A-Z]{1,2}\d+(?:\.\d+){1,3})(?:\.?\(?[a-z]{1,3}\)?)?")
ACT_REF_RE = re.compile(r"Immigration Act 2009,?\s+((?:ss?|sections?)\s+[\d A-Z(),and–-]+)")
MONEY_RE = re.compile(r"NZ\$\s?([\d,]+(?:\.\d{2})?)\s*(per (month|year|week)(?: of study)?)?", re.I)
QTY_RE = re.compile(r"\b(less than|more than|at least|a minimum of|up to|minimum of|over|under|longer than)?\s*"
                    r"(\d+)\s*(weeks?|hours|months?|years?)\b(\s+or (?:longer|more))?", re.I)
AGE_RE = re.compile(r"\b(aged (?:under|over)?\s*\d+|under the age of \d+|under \d+ years)", re.I)
COMPARATOR = {"less than": "LESS_THAN", "under": "LESS_THAN", "more than": "GREATER_THAN", "over": "GREATER_THAN",
              "longer than": "GREATER_THAN", "at least": "GREATER_THAN_OR_EQUAL", "a minimum of": "GREATER_THAN_OR_EQUAL",
              "minimum of": "GREATER_THAN_OR_EQUAL", "up to": "LESS_THAN_OR_EQUAL"}

# Section 6 candidate rules -> instructions that cover them (reconciliation, IF-31)
RECONCILE = {
    "SV-CAND-PRODUCTS": (["U4", "U8.25", "U11", "U12", "U14", "U15"], "RECONCILED",
                         "Student categories and products are set out in U4, U8.25, U11, U12, U14 and U15."),
    "SV-CAND-ELIGIBILITY": (["U3.1", "E5.1", "A4", "A5"], "RECONCILED",
                            "Core requirements are listed in U3.1, with bona fides (E5), health and character (A4, A5)."),
    "SV-CAND-MAINT-TERTIARY": (["U3.20.25", "U3.20.30"], "RECONCILED",
                               "Same figures. The manual's boundary is 36 weeks, and amounts are less prepaid living expenses."),
    "SV-CAND-MAINT-SCHOOL": (["U3.20.25", "U3.20.30"], "RECONCILED",
                             "Same figures for compulsory education (Years 1-13); 36-week boundary."),
    "SV-CAND-MATERNITY": (["U3.20.35"], "RECONCILED",
                          "NZ$9,000 confirmed; applies to applicants due to give birth in New Zealand, with "
                          "sponsorship, undertaking or public eligibility as alternatives."),
    "SV-CAND-WORK": (["U13", "U11.1"], "RECONCILED", "Up to 25 hours in any given week (U13.10, U13.15.1)."),
    "SV-CAND-CHANGE-STUDY": (["U3.40", "U7.20"], "RECONCILED", "Change of programme or provider: U3.40."),
    "SV-CAND-GENUINE": (["E5.1", "U3.1"], "RECONCILED", "Bona fide applicant requirements: E5.1; English ability: U3.1(e)."),
    "SV-CAND-FAMILY": (["U8"], "PARTLY_RECONCILED",
                       "Dependent children are in U8. Partner work and visitor pathways sit in the W and V "
                       "sections, which are outside the loaded scope."),
    "SV-CAND-GUARDIAN": (["U3.30", "U7.25"], "RECONCILED", "Under-10 guardian requirement and exemptions: U3.30, U7.25."),
}


def roman_path(path) -> str:
    return "".join(f"({p})" for p in path if not p.startswith(("p", "note", "table", "h")))


def domain_for(code: str) -> str:
    best = None
    for k in DOMAINS:
        if code == k or code.startswith(k + "."):
            if best is None or len(k) > len(best):
                best = k
    return DOMAINS.get(best) or DOMAINS.get(code[:2]) or DOMAINS.get(code[:1] + code[1:].split(".")[0]) or \
        "Immigration Instructions"


def automation_class(text: str) -> tuple[str, str]:
    t = text.lower()
    if re.search(r"special direction|absolute discretion|at the discretion|immigration officers? may (decline|grant|"
                 r"waive|approve|vary|refuse)|may be granted|may grant|may decline", t):
        return "A4", "power or discretion wording"
    if re.search(r"satisf|genuine|bona fide|consider|assess|reasonable|appropriate|sufficient|likely to|"
                 r"acceptable|credible|exceptional", t):
        return "A3", "evaluative wording"
    if re.search(r"\bmeans\b|is defined|are defined|for the purposes of", t):
        return "A1", "definition"
    return "A2", "deterministic once the facts are verified"


def compose(chain: list[str]) -> str:
    parts = []
    for i, t in enumerate(chain):
        t = t.strip()
        if i < len(chain) - 1:
            t = t.rstrip()
        else:
            t = re.sub(r"(;|,)?\s*(or|and)$", "", t).rstrip(" ;,")
        parts.append(t)
    return clean(" ".join(parts))


def build(b) -> None:
    data = b.om
    if not data:
        return
    pages = data["pages"]
    current_codes = {u["code"] for p in pages if p["version_kind"] != "HISTORIC" for u in p["units"]}
    b.om_rules, b.om_evidence_types, b.om_reqs, b.om_duplicates = {}, {}, [], []
    seq = {"req": 0}

    def unit_pid(code, version=None):
        return f"NZ-INZ-OM-{code}" + (f"@{version}" if version else "")

    def parent_code(code):
        parts = code.split(".")
        while len(parts) > 1:
            parts = parts[:-1]
            c = ".".join(parts)
            if c in current_codes:
                return c
        return None

    def add_prov(pid, **kw):
        b.provisions[pid] = {
            "provision_id": pid, "source_id": SID, "parent_provision_id": kw.get("parent"),
            "provision_type": kw["ptype"], "reference": kw["reference"], "heading": kw.get("heading"),
            "source_text": kw["text"] or None, "source_text_status": "RETRIEVED" if kw["text"] else
            "SOURCE_TEXT_NOT_RETRIEVED", "source_text_reason": None if kw["text"] else "No body text on the page",
            "source_url": kw["url"], "source_version": kw.get("effective_from"),
            "version_as_published": f"Effective {kw['effective_from']}" if kw.get("effective_from") else None,
            "retrieved_at": kw.get("retrieved_at"), "html_sha256": kw.get("sha256"),
            "html_cache": f"data/opsmanual/pages/{kw['page']}", "effective_from": kw.get("effective_from"),
            "effective_to": kw.get("effective_to"), "version_kind": kw["version_kind"],
            "published_effective_from": kw.get("published_effective_from"),
            "merged_identical_versions": kw.get("merged") or [],
            "instruction_code": kw.get("code"), "interpretation_status": "CONSISTENT",
            "interpretation_flag_ids": ["IF-25", "IF-28"],
        }

    def unit_text(p, ui):
        u = p["units"][ui]
        text = om.render(u["blocks"])
        if not text:                      # heading with sub-units only: its published sub-unit text
            subs = [x for x in p["units"][ui + 1:] if x["code"].startswith(u["code"] + ".")]
            text = clean(" ".join(f"{x['code']} {x['heading']} {om.render(x['blocks'])}" for x in subs))
        if not text and ui == 0 and len(p["units"]) == 1 and p.get("section_listing"):
            text = "In this section: " + "; ".join(p["section_listing"])   # index page: its published listing
        return text

    # IF-35: per instruction unit, a historic version whose text is identical to the next newer kept version is
    # the same information. It is merged into that version, which then covers both periods.
    chains: dict[str, list[tuple[dict, int]]] = {}
    for p in pages:
        for ui, u in enumerate(p["units"]):
            chains.setdefault(u["code"], []).append((p, ui))
    keep, eff_from, eff_to, merged, supersedes = set(), {}, {}, {}, []
    for code, items in chains.items():
        cur = [(p, ui) for p, ui in items if p["version_kind"] != "HISTORIC"][:1]
        hist = sorted([(p, ui) for p, ui in items if p["version_kind"] == "HISTORIC"],
                      key=lambda x: x[0]["effective_from"] or "", reverse=True)
        kept = None
        for p, ui in cur + hist:
            key = (p["page"], code)
            eff_from[key] = p["effective_from"]
            if kept and unit_text(p, ui) == kept[1]:
                eff_from[kept[0]] = p["effective_from"]
                merged.setdefault(kept[0], []).append(p["effective_from"])
                continue
            if kept:
                supersedes.append((kept[0], key))
                eff_to[key] = eff_from[kept[0]]
            keep.add(key)
            kept = (key, unit_text(p, ui))
    unit_ids: dict[tuple[str, str], str] = {}

    for p in sorted(pages, key=lambda x: (x["version_kind"] == "HISTORIC", x["code"])):
        hist = p["version_kind"] == "HISTORIC"
        for ui, u in enumerate(p["units"]):
            key = (p["page"], u["code"])
            if key not in keep:
                continue
            ver = eff_from[key] if hist else None        # id carries the start of the period the text covers
            pid = unit_pid(u["code"], ver)
            if pid in b.provisions:
                if not hist:
                    b.om_duplicates.append((p["page"], u["code"]))   # same unit on an index and a child page
                    continue
                pid = unit_pid(u["code"], f"{ver}-{p['page'][:-4]}")
            unit_ids[key] = pid
            text = unit_text(p, ui)
            par = None
            if hist:
                par = None                     # historic versions hang off their newer version via SUPERSEDES
            elif ui > 0:
                prev = [x["code"] for x in p["units"][:ui] if u["code"].startswith(x["code"] + ".")]
                par = unit_pid(max(prev, key=len) if prev else p["units"][0]["code"], ver)
            else:
                pc = parent_code(u["code"])
                par = unit_pid(pc) if pc else None
            add_prov(pid, parent=par, ptype="Instruction", reference=u["code"], heading=u["heading"],
                     text=text, url=p["url"], effective_from=eff_from[key],
                     effective_to=eff_to.get(key, None if hist else p["effective_to"]),
                     published_effective_from=p["effective_from"], merged=merged.get(key),
                     version_kind=p["version_kind"], code=u["code"], page=p["page"],
                     retrieved_at=p["retrieved_at"], sha256=p["sha256"])
            if par:
                b.edge("PARENT_OF", par, pid)
            elif not hist:
                b.edge("CONTAINS", SID, pid)
            for sa in u["see_also"]:
                for m in ACT_REF_RE.finditer(sa):
                    ref = re.sub(r"\band\b", ",", m.group(1)).replace("sections", "ss").replace("section", "s")
                    try:
                        for apid in b.add_provisions(ref.strip(" ,"), "NZ-IMM-ACT-2009"):
                            b.edge("REFERENCES", pid, apid, kind="see_also")
                    except ValueError:
                        pass
            if hist or p["version_kind"] == "EXPIRED":
                continue
            # atomic rules for current units. Paragraphs are not separate nodes (IF-35): their text is in the
            # unit's source_text and in the rule built from each paragraph.
            lead, refs = {}, set()
            for path, n in om.walk(u["blocks"]):
                lead[path] = n["text"]
                refs |= set(n["links"]) | {m.group(1) for m in CODE_REF_RE.finditer(n["text"])}
                if n["children"] or not n["text"] or path[-1].startswith("h") or not makes_rules(u["code"]):
                    continue
                chain = [lead[path[:i]] for i in range(1, len(path))] + [n["text"]]
                _rule(b, u, p, path, pid, compose(chain), seq)
            for code in sorted(refs):
                if code != u["code"] and code in current_codes and not code.startswith(u["code"] + "."):
                    b.edge("REFERENCES", pid, unit_pid(code), kind="cross_reference")
    for newer, older in supersedes:
        if newer in unit_ids and older in unit_ids:
            b.edge("SUPERSEDES", unit_ids[newer], unit_ids[older])
    _reconcile(b)


def _rule(b, u, p, path, npid, statement, seq):
    from .extract import PRODUCT_ID, modality_of, RULE_TYPE_BY_MODALITY
    rid = f"SV-OM-{u['code']}-" + "-".join(x.upper() for x in path)
    try:
        mod, _ = modality_of(statement)
        rtype = RULE_TYPE_BY_MODALITY[mod]
    except ValueError:
        mod, rtype = None, "BusinessRule"
    if mod is None and not re.search(r"\b(is|are) (defined|treated)|\bmeans\b", statement):
        rtype = "BusinessRule"
    cls, why = automation_class(statement)
    actors = subject_actors(statement)
    rec = {
        "rule_id": rid, "product_id": PRODUCT_ID, "domain": domain_for(u["code"]), "catalogue": "INSTRUCTION",
        "catalogue_section": u["code"],
        "classification": {"rule_type": rtype, "modality": mod, "published_type": None},
        "rulespeak_statement": None, "instruction_statement": statement,
        "instruction_code": u["code"], "instruction_heading": u["heading"],
        "authority": {"regulatory_instrument": INSTRUMENT, "instrument_type": "ImmigrationInstruction",
                      "provision": f"{u['code']}{roman_path(path)}" or u["code"],
                      "source_version": p["effective_from"], "source_id": SID, "provision_id": npid,
                      "provision_ids": [npid]},
        "qualifiers": {"condition_ids": [], "exception_ids": [], "threshold_ids": [], "temporal_constraint_ids": [],
                       "discretion_ids": [], "trigger_ids": []},
        "evidence_requirement_ids": [],
        "automation": {"class": cls, "class_as_published": None, "class_basis": why,
                       "machine_evaluable": cls in ("A1", "A2"), "missing_result": "UNKNOWN",
                       "coverage_gap_ids": [], "interpretation_flag_ids": ["IF-26"]},
        "provenance_status": "VERIFIED_INSTRUCTION_SOURCE", "effective_from": p["effective_from"],
        "effective_to": None, "source_url": p["url"], "subject": None, "actor_ids": actors,
        "concept_ids": b.concept_refs(statement), "rule_group": f"OM-{u['code'].split('.')[0]}",
        "extracted_from": {"page": p["page"], "instruction": u["code"], "path": list(path)},
        "interpretation_flag_ids": ["IF-25", "IF-26", "IF-27", "IF-32"],
    }
    # Minimal-graph principle (IF-34): conditions, exceptions and time constraints are already in the rule's own
    # instruction_statement, so no separate qualifier nodes are created for instruction rules. Thresholds are
    # kept because staff query the figures directly.
    for th in thresholds(statement):
        tid = b.qid("TH", rid)
        b.qualifiers["thresholds"].append({"id": tid, "rule_id": rid, **th, "source_provision_id": npid,
                                           "version_id": p["effective_from"], "effective_from": p["effective_from"],
                                           "effective_to": None, "provenance_status": "VERIFIED_INSTRUCTION_SOURCE",
                                           "label": None, "executable": False,
                                           "derivation": "Figure in the instruction text",
                                           "interpretation_flag_ids": ["IF-25"]})
        rec["qualifiers"]["threshold_ids"].append(tid)
    etypes = set()
    for m in FORM_RE.finditer(statement):
        name = clean(re.sub(r"^(a|an|the|completed|signed)\s+", "", m.group(1).strip(), flags=re.I))
        etid = f"EVTYPE-INZ-{m.group(2)}"
        b.om_evidence_types.setdefault(etid, {"evidence_type_id": etid, "evidence_item": f"{name} (INZ {m.group(2)})",
                                              "supports_fact": None, "verification_need": None,
                                              "typical_rule_domain": rec["domain"],
                                              "source": f"Operational Manual {u['code']}"})
        etypes.add(etid)
    for pat, etid in EVIDENCE_WORDS:
        if re.search(pat, statement, re.I):
            etypes.add(etid)
    for etid in sorted(etypes):
        seq["req"] += 1
        erid = f"SV-EVID-REQ-OM-{seq['req']:04d}"
        b.om_reqs.append({"evidence_requirement_id": erid, "rule_id": rid,
                          "description": f"Evidence named or implied in {rec['authority']['provision']}",
                          "required_fact_type_id": None, "acceptable_evidence_types": [etid],
                          "mandatory": mod in ("MUST", "MUST_NOT"), "verification_required": True,
                          "source_provision_id": npid, "interpretation_flag_ids": ["IF-26"]})
        rec["evidence_requirement_ids"].append(erid)
    b.om_rules[rid] = rec


def thresholds(text: str) -> list[dict]:
    out = []
    for m in MONEY_RE.finditer(text):
        val = float(m.group(1).replace(",", ""))
        out.append({"text": m.group(0).strip(), "value": int(val) if val.is_integer() else val, "unit": "NZD",
                    "per": (m.group(3) or None), "comparator": "GREATER_THAN_OR_EQUAL"
                    if re.search(r"at least|have|funds of", text[max(0, m.start() - 40):m.start()], re.I) else None,
                    "applies_to_attribute": "available_funds_nzd"})
    for m in QTY_RE.finditer(text):
        word = (m.group(1) or "").lower()
        comp = COMPARATOR.get(word) or ("GREATER_THAN_OR_EQUAL" if m.group(4) else None)
        unit = m.group(3).lower().rstrip("s") + "s"
        out.append({"text": clean(m.group(0)), "value": int(m.group(2)), "unit": unit, "per": None,
                    "comparator": comp, "applies_to_attribute": None})
    return out


def _reconcile(b) -> None:
    for cid, (codes, status, note) in RECONCILE.items():
        c = b.candidates.get(cid)
        if not c:
            continue
        found = [code for code in codes if f"NZ-INZ-OM-{code}" in b.provisions]
        c["reconciliation"] = {"status": status if found else "NOT_FOUND", "instruction_codes": found,
                               "note": note, "interpretation_flag_ids": ["IF-31"]}
        if found:
            c["provenance_status"] = "SUPERSEDED_BY_INSTRUCTION" if status == "RECONCILED" else \
                "RECONCILIATION_REQUIRED"
            for code in found:
                b.edge("SUPERSEDES", f"NZ-INZ-OM-{code}", cid)
        c["executable"] = False
        c["label"] = None
        c["automation"]["machine_evaluable"] = False
    for t in b.qualifiers["thresholds"]:
        if t["id"].startswith("SV-TH-CAND-"):
            t["executable"] = False
            t["label"] = None
            t["provenance_status"] = "SUPERSEDED_BY_INSTRUCTION"
    # thresholds the funds check executes (IF-30)
    for t in b.qualifiers["thresholds"]:
        if t["rule_id"] in FUNDS_RULES and t["unit"] == "NZD":
            t["executable"] = True


FUNDS_RULES = {
    "SV-OM-U3.20.25-A-I": ("tertiary", "month"), "SV-OM-U3.20.25-A-II": ("compulsory", "month"),
    "SV-OM-U3.20.30-A-I": ("tertiary", "year"), "SV-OM-U3.20.30-A-II": ("compulsory", "year"),
    "SV-OM-U3.20.35-P1-A": ("maternity", None),
}
SAMOA_TONGA_RULE = "SV-OM-U3.20.30-A-III"

# Domain checks (IF-37): one check per Operational Manual domain. Every rule of the domain is linked to the check.
# "executes" lists the rules with a clear pass/fail test and the application facts each needs. The other
# A1/A2/A3 rules are presented for human review and the A4 rules for the decision-maker.
DOMAIN_CHECKS = {
    "SV-CHECK-FAM-001": {
        "domain": "Family", "trigger_facts": ["family_basis"],
        "executes": {
            "SV-OM-U8.20.6-A": ["supporting_work_visa_type", "supporting_parent_gross_income_nzd"],
            "SV-OM-U8.20.10-B-I": ["supporting_work_visa_type", "supporting_parent_gross_income_nzd"],
            "SV-OM-U8.30-B-I": ["applicant_in_new_zealand", "visa_duration_months"],
            "SV-OM-U8.30-B-II": ["applicant_in_new_zealand", "visa_duration_months"]}},
    "SV-CHECK-PATH-001": {
        "domain": "Pathway student visas", "trigger_facts": ["pathway_programme_count"],
        "executes": {
            "SV-OM-U14-A": ["pathway_programme_count"],
            "SV-OM-U14.10-A": ["pathway_visa_duration_months"],
            "SV-OM-U14.20.5-C": ["max_break_between_programmes_weeks"],
            "SV-OM-U14.20.5-D": ["english_language_study_weeks"],
            "SV-OM-U14.20.1-P1-D": ["provider_visa_approval_rate_percent"]}},
    "SV-CHECK-SPON-001": {
        "domain": "Sponsorship", "trigger_facts": ["third_party_type", "sponsor_is_natural_person"],
        "executes": {
            "SV-OM-U3.20.10-B": ["third_party_type", "third_party_relationship"],
            "SV-OM-U3.20.10-C": ["third_party_type", "authorised_contact_nominated"],
            "SV-OM-E6.5-E": ["sponsor_is_natural_person", "authorised_contact_nominated"]}},
    "SV-CHECK-PROV-001": {
        "domain": "Providers and programmes",
        "trigger_facts": ["provider_eer_category", "provider_signatory_to_code",
                          "provider_certified_programme_compliance"],
        "executes": {
            "SV-OM-U5.1-A": ["provider_certified_programme_compliance"],
            "SV-OM-U5.1-B": ["provider_signatory_to_code"],
            "SV-OM-U5.1-D": ["provider_eer_category"]}},
    "SV-CHECK-WRK-001": {
        "domain": "Work rights", "trigger_facts": ["work_conditions_requested"],
        "executes": {
            "SV-OM-U13.15-B": ["applicant_age", "enrolled_at_tertiary_or_pte", "provider_written_permission",
                               "parental_written_consent"]}},
}

# Rules with a clear test that the residual domain checks execute (IF-38), with the facts each needs.
RESIDUAL_EXECUTES = {
    "SV-FAM-002": ["dependent_child_included", "child_age"],
    "SV-U18-003": ["applicant_age", "provider_designated_staff_for_under_18s"],
    "SV-OM-U7.20.1-A": ["part_time_study_months"],
    "SV-OM-U7.20.1-B": ["part_time_study_months"],
}

# Lodgement rules (E4) the lodgement check executes (IF-36), by lodgement route. Rules in E4 that are A3/A4
# stay on the human-review / discretion routes and are not listed here.
LODGEMENT_RULES = {
    "paper": ["SV-OM-E4.50.1-P1-A", "SV-OM-E4.50.1-P1-B", "SV-OM-E4.50.1-P2"],
    "online": ["SV-OM-E4.50.2-B-I", "SV-OM-E4.50.2-B-II", "SV-OM-E4.50.2-B-III", "SV-OM-E4.50.2-B-IV",
               "SV-OM-E4.50.2-B-VI", "SV-OM-E4.50.2-B-VII"],
    "ica": ["SV-OM-E4.50.35-A-I", "SV-OM-E4.50.35-A-II", "SV-OM-E4.50.35-A-III", "SV-OM-E4.50.35-A-IV",
            "SV-OM-E4.50.35-B-I", "SV-OM-E4.50.35-B-II"],
}
