"""Phase 1: extract the v0.2 specification into the Appendix A file layout.

Everything is read from the .docx. Where the build needs a decision the document
does not make, the choice is recorded in governance/interpretation-flags.json
(IF-xx) and referenced from the affected records via `interpretation_flag_ids`.
Mappings written by hand in this module (domain-by-prefix, evidence-by-domain,
test-implied checks) are each tied to one of those flags.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml

from . import provref
from .docspec import ROOT, Spec, sha256

PRODUCT_ID = "STUDENT-VISA"
SPEC_VERSION = "0.2"
SPEC_DATE = "2026-11-17"
CANDIDATE_LABEL = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"
SOURCE_TEXT_NOT_RETRIEVED = "SOURCE_TEXT_NOT_RETRIEVED"

APPENDIX_A_FILES = [
    "schema/ontology.yaml", "schema/enumerations.yaml", "schema/relationship-types.yaml",
    "schema/agent-contract.yaml", "regulatory/sources.json", "regulatory/provisions.json",
    "regulatory/concepts.json", "regulatory/actors.json", "regulatory/fact-types.json",
    "regulatory/relationships.json", "rules/rules.json", "rules/conditions.json",
    "rules/exceptions.json", "rules/thresholds.json", "rules/temporal-constraints.json",
    "rules/discretions.json", "evidence/evidence-requirements.json",
    "evidence/verification-rules.json", "decisioning/decision-checks.json",
    "decisioning/states.json", "decisioning/state-transitions.json",
    "testing/synthetic-applications.json", "testing/test-cases.json",
    "testing/expected-results.json", "governance/source-coverage.json",
    "governance/provenance.json", "governance/interpretation-flags.json", "README_POC.md",
]

# --------------------------------------------------------------------------- sources
# Source IDs: the document gives NZ-IMM-ACT-2009 (F.1); the others follow its pattern.
SOURCES = [
    # (match text in Table 1, source_id, instrument_type, made_under)
    ("Immigration Act 2009", "NZ-IMM-ACT-2009", "PrincipalAct", None),
    ("Immigration (Visa, Entry Permission, and Related Matters) Regulations 2010",
     "NZ-IMM-VEPRM-REGS-2010", "Regulations", "NZ-IMM-ACT-2009"),
    ("Immigration (Carriers' Information Obligations) Regulations 2010",
     "NZ-IMM-CIO-REGS-2010", "Regulations", "NZ-IMM-ACT-2009"),
    ("Education and Training Act 2020", "NZ-EDU-TRAINING-ACT-2020", "PrincipalAct", None),
    ("Education (Pastoral Care of Tertiary and International Learners) Code of Practice 2021, amended April 2023",
     "NZ-EDU-PASTORAL-CODE-2021", "CodeOfPractice", "NZ-EDU-TRAINING-ACT-2020"),
    ("Privacy Act 2020", "NZ-PRIVACY-ACT-2020", "PrincipalAct", None),
    ("Immigration Instructions / INZ Operational Manual", "NZ-INZ-IMMIGRATION-INSTRUCTIONS",
     "ImmigrationInstruction", "NZ-IMM-ACT-2009"),
    ("Business Rules Analysis AI Prompts Immigration.docx", "DOC-BRA-AI-PROMPTS", "Method", None),
    ("INZ Student Visa Business Rule.docx", "DOC-INZ-SV-BUSINESS-RULE", "OriginalWork", None),
]
# Only the Act's version is stated in the document (12.1 / F.1 source_version 2026-07-10).
DOC_SOURCE_VERSIONS = {"NZ-IMM-ACT-2009": "2026-07-10"}


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("‘", "'").split())


# --------------------------------------------------------------------------- mappings (flagged)
PREFIX_DOMAIN = {  # IF-05
    "APP": "Application", "FORM": "Application", "ID": "Application", "PHOTO": "Application",
    "INSTR": "Application", "FEE": "Application", "IVL": "Application", "ONLINE": "Application",
    "BIO": "Application", "EVID": "Evidence", "MIN": "Minor/guardian", "U18": "Minor/guardian",
    "U10": "Minor/guardian", "ACCOM": "Minor/guardian", "DEC": "Decision", "SPON": "Sponsorship",
    "COND": "Visa conditions", "WORK": "Visa conditions", "STUDY": "Visa conditions",
    "LIFE": "Lifecycle", "ENTRY": "Entry permission", "HEALTH": "Health", "TRAVEL": "Travel",
    "FIN": "Maintenance", "FAM": "Family", "CHAR": "Character", "EDU": "Education provider",
    "SCHOOL": "Education provider", "INS": "Education/insurance", "PRIV": "Privacy",
    "CARRIER": "Carrier",
}

# Rule subjects -> actor IDs (IF-17). Longest prefix wins.
SUBJECT_ACTORS = {
    "Minister or Immigration Officer": ["ACTOR-MINISTER", "ACTOR-IMMIGRATION-OFFICER"],
    "Immigration Officer": ["ACTOR-IMMIGRATION-OFFICER"],
    "Entry Permission Applicant": ["ACTOR-APPLICANT"],
    "Student Visa Applicant": ["ACTOR-APPLICANT"],
    "Visa Applicant": ["ACTOR-APPLICANT"],
    "Applicant": ["ACTOR-APPLICANT"],
    "Sponsor": ["ACTOR-SPONSOR"],
    "Temporary Entry Class Visa Holder": ["ACTOR-VISA-HOLDER"],
    "Visa Holder": ["ACTOR-VISA-HOLDER"],
    "Decision-Maker": ["ACTOR-DECISION-MAKER"],
    "Signatory": ["ACTOR-SIGNATORY"],
    "Carrier": ["ACTOR-CARRIER"],
    "Agency": ["ACTOR-AGENCY"],
    "Individual": ["ACTOR-INDIVIDUAL"],
}
DERIVED_ACTORS = {  # actors named as rule subjects but not in the Table 3 'Parties' group
    "ACTOR-DECISION-MAKER": "Decision-Maker", "ACTOR-AGENCY": "Agency",
    "ACTOR-INDIVIDUAL": "Individual",
}
ACTOR_TYPE_LABEL = {  # Section 3 Domain node types
    "ACTOR-APPLICANT": "Applicant", "ACTOR-IMMIGRATION-OFFICER": "ImmigrationOfficer",
    "ACTOR-SPONSOR": "Sponsor", "ACTOR-EDUCATION-PROVIDER": "Provider",
    "ACTOR-LEGAL-GUARDIAN": "Guardian",
}
CONCEPT_TYPE_LABEL = {
    "Visa Application": "Application", "Online Application": "Application",
    "Programme": "Programme", "Approved Provider": "Provider",
}

# Table 9 'typical rule domain' -> rules (IF-11)
EVIDENCE_TYPES = {
    "Passport / certificate of identity": ("PASSPORT", ["SV-ID-001", "SV-ONLINE-005", "SV-CARRIER-003"]),
    "Offer of Place": ("OFFER-OF-PLACE", ["SV-CAND-ELIGIBILITY"]),
    "Tuition receipt / funding evidence": ("TUITION", ["SV-CAND-ELIGIBILITY"]),
    "Bank statement": ("BANK-STATEMENT", ["SV-FIN-001", "SV-CAND-MAINT-TERTIARY",
                                          "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"]),
    "Sponsorship form/evidence": ("SPONSORSHIP", ["SV-SPON-001", "SV-SPON-003", "SV-SPON-004",
                                                  "SV-SPON-006", "SV-FIN-001"]),
    "Medical certificate / chest X-ray": ("MEDICAL", ["SV-HEALTH-001", "SV-CAND-ELIGIBILITY"]),
    "Police certificate": ("POLICE-CERTIFICATE", ["SV-CHAR-001", "SV-CHAR-002", "SV-CHAR-003",
                                                  "SV-CHAR-004", "SV-CHAR-005"]),
    "Insurance policy/declaration": ("INSURANCE", ["SV-INS-001", "SV-INS-002", "SV-INS-003",
                                                   "SV-INS-004", "SV-INS-005", "SV-SCHOOL-004"]),
    "Parent or guardian evidence": ("GUARDIAN", ["SV-MIN-001", "SV-U10-001", "SV-U18-001",
                                                 "SV-U18-004", "SV-SCHOOL-005"]),
}

CANDIDATE_IDS = {  # Section 6 'Domain' column -> candidate rule IDs
    "Visa products": ("SV-CAND-PRODUCTS", "BusinessRule"),
    "Core eligibility": ("SV-CAND-ELIGIBILITY", "EligibilityRule"),
    "Tertiary maintenance": ("SV-CAND-MAINT-TERTIARY", "Calculation"),
    "School maintenance": ("SV-CAND-MAINT-SCHOOL", "Calculation"),
    "Pregnancy": ("SV-CAND-MATERNITY", "Calculation"),
    "Work rights": ("SV-CAND-WORK", "Permission"),
    "Change of study": ("SV-CAND-CHANGE-STUDY", "BusinessRule"),
    "Genuine intentions": ("SV-CAND-GENUINE", "BusinessRule"),
    "Family support": ("SV-CAND-FAMILY", "BusinessRule"),
    "Guardian": ("SV-CAND-GUARDIAN", "BusinessRule"),
}
APPENDIX_E_LINKS = {
    "Visa product taxonomy": ["SV-CAND-PRODUCTS"], "Core eligibility": ["SV-CAND-ELIGIBILITY"],
    "Financial thresholds": ["SV-CAND-MAINT-TERTIARY", "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"],
    "Work rights": ["SV-CAND-WORK"], "Change of study": ["SV-CAND-CHANGE-STUDY"],
    "Genuine intentions": ["SV-CAND-GENUINE"], "Guardian": ["SV-CAND-GUARDIAN"],
    "Family support": ["SV-CAND-FAMILY"],
}
# Decision 3: these execute (with the candidate label). Everything else in Section 6 does not.
EXECUTABLE_CANDIDATES = {"SV-CAND-MAINT-TERTIARY", "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"}

CLASS_ORDER = ["A1", "A2", "A3", "A4", "A5"]


# --------------------------------------------------------------------------- helpers
def slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


def snake(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")


def rule_prefix(rule_id: str) -> str:
    return rule_id.split("-")[1]


def modality_of(statement: str) -> tuple[str | None, int]:
    hits = []
    for pat, mod in ((r"\bmust not\b", "MUST_NOT"), (r"\bmust\b", "MUST"), (r"\bmay\b", "MAY"),
                     (r"\bis not defined as\b", None), (r"\bis defined as\b", None)):
        m = re.search(pat, statement)
        if m:
            hits.append((m.start(), -(m.end() - m.start()), mod))
    if not hits:
        raise ValueError(f"no modality in {statement!r}")
    i, _, mod = sorted(hits)[0]
    return mod, i + 1


RULE_TYPE_BY_MODALITY = {"MUST": "Obligation", "MUST_NOT": "Prohibition", "MAY": "Permission", None: "Definition"}
EDGE_BY_MODALITY = {"MUST": "IMPOSES_OBLIGATION_ON", "MUST_NOT": "PROHIBITS", "MAY": "PERMITS"}


def subject_of(statement: str, cut: int) -> str:
    subj = statement[:max(cut - 1, 0)].strip()
    return re.sub(r"^(The|A|An|Each)\s+", "", subj)


def actors_for(subject: str) -> list[str]:
    for key in sorted(SUBJECT_ACTORS, key=len, reverse=True):
        if subject.startswith(key):
            return SUBJECT_ACTORS[key]
    return []


def parse_automation(raw: str) -> dict:
    tokens = [t.strip() for t in re.split(r"[/]", raw) if t.strip()]
    classes = [t for t in tokens if t in CLASS_ORDER]
    gap = any(t.lower() in ("gap", "source_coverage_gap") for t in tokens)
    cls = max(classes, key=CLASS_ORDER.index) if classes else None
    return {
        "class": cls,
        "class_as_published": raw,
        "machine_evaluable": cls in ("A1", "A2") and not gap,
        "missing_result": "UNKNOWN",
        "coverage_gap_ids": ["GAP-01"] if gap else [],
        "interpretation_flag_ids": ["IF-04"] if (len(classes) > 1 or gap) else [],
    }


# --------------------------------------------------------------------------- qualifier derivation (IF-06)
COND_PATTERNS = [r"\bonly if (.+?)(?=\s+unless\b|$)", r"(?<!only )\bif (.+?)(?=\s+unless\b|$)",
                 r"\bwhere (.+?)(?=\s+unless\b|$)", r"\bthat may affect (.+?)(?=\s+unless\b|$)"]
EXC_PATTERN = r"\bunless (.+)$"
TIME_PATTERN = (r"\b(when granting [^,.]+|when the [A-Za-z ]+? is made|occurring after Visa grant|"
                r"after the Visa is granted|before determination or grant|"
                r"as soon as practicable after becoming aware of a Notifiable Privacy Breach|"
                r"within the preceding ten years|while the (?:Holder|Person) is outside New Zealand|"
                r"longer than lawfully necessary|before use or disclosure|enrolled for at least two weeks)")
TRIGGER_PATTERN = r"\bwhen (an applicable [^,.]+? occurs)"
NUM_WORDS = {"two": 2, "three": 3, "five": 5, "ten": 10, "twelve": 12}
THRESH_PATTERNS = [
    (r"\baged (\d+) or older", "GREATER_THAN_OR_EQUAL", "years", "age"),
    (r"\bunder (\d+)(?: years old)?", "LESS_THAN", "years", "age"),
    (r"\bat least (two) weeks", "GREATER_THAN_OR_EQUAL", "weeks", "enrolment_duration"),
    (r"\bfor (five|twelve) (?:years|months) or more", "GREATER_THAN_OR_EQUAL", None, "sentence_length"),
    (r"\bwithin the preceding (ten) years", "LESS_THAN_OR_EQUAL", "years", "time_since_sentence"),
    (r"\bprovide (two) Passport-sized Photographs", "GREATER_THAN_OR_EQUAL", "count", "photographs"),
]


def derive_qualifiers(rule_id: str, statement: str) -> dict[str, list[dict]]:
    body = statement.rstrip(".")
    out: dict[str, list[dict]] = {"conditions": [], "exceptions": [], "temporal": [],
                                  "thresholds": [], "triggers": []}
    m = re.search(EXC_PATTERN, body)
    if m:
        out["exceptions"].append({"text": "unless " + m.group(1)})
    seen = set()
    for pat in COND_PATTERNS:
        for m in re.finditer(pat, body):
            span = m.group(0)
            if any(span in s or s in span for s in seen):
                continue
            seen.add(span)
            out["conditions"].append({"text": span})
    for m in re.finditer(TIME_PATTERN, body):
        out["temporal"].append({"text": m.group(1)})
    for m in re.finditer(TRIGGER_PATTERN, body):
        out["triggers"].append({"text": "when " + m.group(1)})
    for pat, comp, unit, subject in THRESH_PATTERNS:
        for m in re.finditer(pat, body):
            raw = m.group(1)
            val = int(raw) if raw.isdigit() else NUM_WORDS[raw]
            u = unit or ("years" if "years" in m.group(0) else "months")
            out["thresholds"].append({"text": m.group(0).strip(), "value": val, "unit": u,
                                      "comparator": comp, "applies_to_attribute": subject})
    return out


# --------------------------------------------------------------------------- builder
class Builder:
    def __init__(self, spec: Spec):
        self.spec = spec
        self.files: dict[str, object] = {}
        self.edges: list[dict] = []
        self.flags: list[dict] = []
        self.provisions: dict[str, dict] = {}
        self.qual_seq: dict[tuple[str, str], int] = defaultdict(int)

    def edge(self, typ, frm, to, **props):
        self.edges.append({"type": typ, "from": frm, "to": to, **({"props": props} if props else {})})

    # ---- flags ------------------------------------------------------------
    def flag(self, fid, title, detail, applies_to, kind="INTERPRETATION", decision=None):
        self.flags.append({
            "flag_id": fid, "kind": kind, "title": title, "detail": detail,
            "applies_to": applies_to, "decision_reference": decision,
            "status": "ACCEPTED_BY_PRODUCT_OWNER" if decision else "OPEN",
            "raised_on": date.today().isoformat(),
        })

    # ---- sources & provisions ----------------------------------------------
    def build_sources(self):
        t1 = self.spec.table_by_header("Level", "2.")
        rows = {norm(r[1]): r for r in t1.rows[1:]}
        out = []
        for match, sid, itype, made_under in SOURCES:
            r = rows[norm(match)]
            src = {
                "source_id": sid, "title": r[1], "instrument_type": itype, "jurisdiction": "NZ",
                "hierarchy_level": r[0], "role": r[2], "poc_coverage": r[3],
                "version_id": DOC_SOURCE_VERSIONS.get(sid), "effective_from": None,
                "effective_to": None,
                "status": "IN_FORCE" if itype in ("PrincipalAct", "Regulations", "CodeOfPractice") else None,
                "authoritative_uri": None,
                "coverage_status": self._coverage_status(sid, r[3]),
                "made_under": made_under,
                "interpretation_flag_ids": ["IF-08", "IF-09"] if sid != "NZ-IMM-ACT-2009" else [],
            }
            out.append(src)
            if made_under:
                self.edge("MADE_UNDER", sid, made_under)
            if itype not in ("Method", "OriginalWork"):
                ver = f"{sid}@{src['version_id'] or 'UNVERSIONED'}"
                src["instrument_version_id"] = ver
                self.edge("CONTAINS", sid, ver)
        self.sources = {s["source_id"]: s for s in out}
        self.source_by_title = {norm(s["title"]): s["source_id"] for s in out}
        self.files["regulatory/sources.json"] = {"sources": out, "instrument_versions": [
            {"instrument_version_id": s["instrument_version_id"], "source_id": s["source_id"],
             "version_id": s["version_id"], "version_basis":
                 "Stated in spec v0.2 (12.1 source_version)" if s["version_id"] else
                 "Not stated in spec; to be set from legislation.govt.nz (Phase 2, deferred)",
             "retrieved_at": None}
            for s in out if "instrument_version_id" in s]}

    @staticmethod
    def _coverage_status(sid, text):
        if sid == "NZ-INZ-IMMIGRATION-INSTRUCTIONS":
            return "NOT_COVERED"
        if sid.startswith("DOC-"):
            return None
        return "PARTIAL"

    def add_provisions(self, reference: str, source_id: str) -> list[str]:
        refs = provref.parse(reference, source_id)
        ids = []
        for ref in refs:
            for node in ref.lineage():
                pid = node.provision_id
                if pid not in self.provisions:
                    par = node.parent()
                    self.provisions[pid] = {
                        "provision_id": pid, "source_id": source_id,
                        "parent_provision_id": par.provision_id if par else None,
                        "provision_type": node.provision_type, "reference": node.reference,
                        "heading": None, "source_text": None,
                        "source_text_status": SOURCE_TEXT_NOT_RETRIEVED,
                        "source_url": None, "source_version": DOC_SOURCE_VERSIONS.get(source_id),
                        "retrieved_at": None, "effective_from": None, "effective_to": None,
                        "interpretation_status": "CONSISTENT",
                        "interpretation_flag_ids": ["IF-07", "IF-10"],
                    }
                    if par:
                        self.edge("PARENT_OF", par.provision_id, pid)
                    else:
                        self.edge("CONTAINS", self.sources[source_id]["instrument_version_id"], pid)
            ids.append(ref.provision_id)
        return ids

    # ---- concepts & actors -------------------------------------------------
    def build_concepts(self):
        t3 = self.spec.table_by_header("Concept group", "4.")
        concepts: dict[str, dict] = {}
        actors = []
        for group, terms in ((r[0], r[1]) for r in t3.rows[1:]):
            for term in [t.strip() for t in terms.split(",")]:
                if group == "Parties":
                    cid = "ACTOR-" + slug(term)
                elif term == "Student Visa":
                    cid = PRODUCT_ID
                else:
                    cid = "CONCEPT-" + slug(term)
                c = concepts.setdefault(cid, {
                    "concept_id": cid, "preferred_term": term, "concept_type": group,
                    "domain": group, "concept_groups": [], "statutory_definition": None,
                    "plain_english_definition": None, "definition_type": "IMPLIED",
                    "source_provision_id": None, "synonyms": [],
                    "type_label": ("Visa" if group == "Product" else
                                   ACTOR_TYPE_LABEL.get(cid) or CONCEPT_TYPE_LABEL.get(term)),
                    "provenance_status": "SPEC_VOCABULARY",
                    "interpretation_flag_ids": ["IF-19"],
                })
                if group not in c["concept_groups"]:
                    c["concept_groups"].append(group)
                if group == "Parties":
                    actors.append({"actor_id": cid, "name": term, "concept_id": cid,
                                   "type_label": ACTOR_TYPE_LABEL.get(cid), "derived_from": "Table 3 Parties"})
        for aid, name in DERIVED_ACTORS.items():
            concepts[aid] = {"concept_id": aid, "preferred_term": name, "concept_type": "Parties",
                             "domain": "Parties", "concept_groups": [], "statutory_definition": None,
                             "plain_english_definition": None, "definition_type": "IMPLIED",
                             "source_provision_id": None, "synonyms": [], "type_label": None,
                             "provenance_status": "SPEC_VOCABULARY",
                             "interpretation_flag_ids": ["IF-17"]}
            actors.append({"actor_id": aid, "name": name, "concept_id": aid, "type_label": None,
                           "derived_from": "rule subject (not in Table 3 Parties)",
                           "interpretation_flag_ids": ["IF-17"]})
        # candidate products named only in Section 6 / Appendix E
        t8 = self.spec.table_by_header("Domain", "6.")
        prod_row = next(r for r in t8.rows[1:] if r[0] == "Visa products")
        names = re.split(r",\s*|\s+and\s+", prod_row[1].rstrip(".").replace(" Student Visa", ""))
        for n in names:
            term = f"{n.strip()} Student Visa"
            cid = "CONCEPT-" + slug(term)
            if cid not in concepts and n.strip() != "Scholarship":
                concepts[cid] = {"concept_id": cid, "preferred_term": term, "concept_type": "Product",
                                 "domain": "Product", "concept_groups": ["Product"],
                                 "statutory_definition": None, "plain_english_definition": None,
                                 "definition_type": "IMPLIED", "source_provision_id": None, "synonyms": [],
                                 "type_label": "Visa", "provenance_status": "RECONCILIATION_REQUIRED",
                                 "interpretation_flag_ids": ["IF-19"]}
        # product hierarchy (IF-19)
        self.edge("PARENT_OF", "CONCEPT-TEMPORARY-ENTRY-CLASS-VISA", PRODUCT_ID)
        for cid, c in concepts.items():
            if c["type_label"] == "Visa" and cid not in (PRODUCT_ID, "CONCEPT-TEMPORARY-ENTRY-CLASS-VISA"):
                self.edge("PARENT_OF", PRODUCT_ID, cid)
        self.concepts = concepts
        self.files["regulatory/concepts.json"] = {"concepts": list(concepts.values())}
        self.files["regulatory/actors.json"] = {"actors": actors}

    def concept_refs(self, statement: str) -> list[str]:
        text = statement
        hits = []
        for cid, c in sorted(self.concepts.items(), key=lambda kv: -len(kv[1]["preferred_term"])):
            pat = r"\b" + re.escape(c["preferred_term"]) + r"\b"
            m = re.search(pat, text, flags=re.I)
            if m:
                hits.append(cid)
                text = text[:m.start()] + "#" * (m.end() - m.start()) + text[m.end():]
        return hits

    # ---- fact types, relationship types -------------------------------------
    def build_fact_types(self):
        t4 = self.spec.table_by_header("Fact type ID", "4.")
        rel = {"FT-004": "SUPPORTS_FACT", "FT-005": "VERIFIED_AS", "FT-006": "INPUT_TO",
               "FT-010": "EVALUATES", "FT-011": "DERIVED_FROM", "FT-012": "CONTAINS",
               "FT-016": "HAS_CONDITION", "FT-017": "PERMITS", "FT-018": "PROHIBITS",
               "FT-002": "APPLIES_TO"}
        self.files["regulatory/fact-types.json"] = {"fact_types": [
            {"fact_type_id": r[0], "statement": r[1], "graph_relationship": rel.get(r[0])}
            for r in t4.rows[1:]]}

    def relationship_vocabulary(self) -> list[str]:
        p = self.spec.paras("3.", "3.1")
        return [x.strip() for x in p[0].text.split(",")]

    # ---- rules -------------------------------------------------------------
    def qid(self, kind: str, rule_id: str) -> str:
        key = (kind, rule_prefix(rule_id))
        self.qual_seq[key] += 1
        return f"SV-{kind}-{rule_prefix(rule_id)}-{self.qual_seq[key]:03d}"

    def build_rules(self):
        rules, conds, excs, ths, temps, discs, trigs = [], [], [], [], [], [], []
        for t in self.spec.rule_tables():
            section = (t.subheading or t.heading).split(" ")[0].rstrip(".")
            header = t.rows[0]
            has_type = header[1] == "Type"
            last_instr = None
            for row_i, r in enumerate(t.rows[1:], start=1):
                if has_type:
                    rid, ptype, stmt, instr, prov, auto = r
                else:
                    rid, stmt, instr, prov, auto = r
                    ptype = None
                instr = last_instr if instr == "same" else instr
                last_instr = instr
                sid = self.source_by_title[norm(instr)]
                pids = self.add_provisions(prov, sid)
                primary = next((p for p in pids if "-IPP" in p), pids[0])
                mod, cut = modality_of(stmt)
                subject = subject_of(stmt, cut)
                actor_ids = actors_for(subject)
                automation = parse_automation(auto)
                q = derive_qualifiers(rid, stmt)
                rec = {
                    "rule_id": rid, "product_id": PRODUCT_ID, "domain": PREFIX_DOMAIN[rule_prefix(rid)],
                    "catalogue": "VERIFIED", "catalogue_section": section,
                    "classification": {"rule_type": RULE_TYPE_BY_MODALITY[mod], "modality": mod,
                                       "published_type": ptype},
                    "rulespeak_statement": stmt,
                    "authority": {"regulatory_instrument": instr,
                                  "instrument_type": self.sources[sid]["instrument_type"],
                                  "provision": prov, "source_version": self.sources[sid]["version_id"],
                                  "source_id": sid, "provision_id": primary, "provision_ids": pids},
                    "qualifiers": {"condition_ids": [], "exception_ids": [], "threshold_ids": [],
                                   "temporal_constraint_ids": [], "discretion_ids": [], "trigger_ids": []},
                    "evidence_requirement_ids": [],
                    "automation": automation,
                    "provenance_status": "VERIFIED_PRIMARY_SOURCE",
                    "subject": subject, "actor_ids": actor_ids,
                    "concept_ids": self.concept_refs(stmt),
                    "rule_group": None,
                    "extracted_from": {"table_index": t.table_index, "row": row_i, "section": section},
                    "interpretation_flag_ids": sorted(set(["IF-05"] + (["IF-02"] if ptype else [])
                                                          + (["IF-03"] if mod is None else [])
                                                          + automation["interpretation_flag_ids"])),
                }
                # qualifiers
                for kind, bucket, store, key in (("COND", "conditions", conds, "condition_ids"),
                                                 ("EXC", "exceptions", excs, "exception_ids"),
                                                 ("TIME", "temporal", temps, "temporal_constraint_ids"),
                                                 ("TH", "thresholds", ths, "threshold_ids"),
                                                 ("TRIG", "triggers", trigs, "trigger_ids")):
                    for item in q[bucket]:
                        qid = self.qid(kind, rid)
                        store.append({"id": qid, "rule_id": rid, **item, "source_provision_id": primary,
                                      "derivation": "Exact span of the RuleSpeak statement (IF-06)",
                                      "provenance_status": "VERIFIED_PRIMARY_SOURCE",
                                      "interpretation_flag_ids": ["IF-06"]})
                        rec["qualifiers"][key].append(qid)
                if automation["class"] == "A4":
                    did = self.qid("DISC", rid)
                    discs.append({"id": did, "rule_id": rid, "holder_actor_ids": actor_ids,
                                  "holder": subject, "power": stmt, "authority": {
                                      "regulatory_instrument": instr, "provision": prov,
                                      "provision_id": primary},
                                  "constraints": rec["qualifiers"]["condition_ids"] + rec["qualifiers"]["exception_ids"],
                                  "routing": "DISCRETION_REQUIRED",
                                  "interpretation_flag_ids": ["IF-06"]})
                    rec["qualifiers"]["discretion_ids"].append(did)
                rules.append(rec)
        # 12.1 template example: SV-APP-005 has condition SV-COND-APP-001 - check it lines up
        self.rules = {r["rule_id"]: r for r in rules}
        self.qualifiers = {"conditions": conds, "exceptions": excs, "thresholds": ths,
                           "temporal": temps, "discretions": discs, "triggers": trigs}

    def build_rule_groups(self):
        t7 = self.spec.table_by_header("Rule group", "5.", "5.2")
        groups = []
        last_instr = None
        for i, r in enumerate(t7.rows[1:], start=1):
            name, key, instr, prov = r
            instr = last_instr if instr == "same" else instr
            last_instr = instr
            sid = self.source_by_title[norm(instr)]
            pids = set(self.add_provisions(prov, sid))
            gid = "SV-GROUP-" + slug(name)
            members = []
            for rule in self.rules.values():
                if rule["authority"]["source_id"] != sid or rule["rule_group"]:
                    continue
                tops = {p for p in rule["authority"]["provision_ids"]}
                tops |= {self._top(p) for p in tops if "-IPP" not in p}
                if tops & pids:
                    rule["rule_group"] = gid
                    members.append(rule["rule_id"])
            groups.append({"rule_group_id": gid, "name": name, "key_rules": key,
                           "regulatory_instrument": instr, "provisions": prov,
                           "source_id": sid, "member_rule_ids": members})
        self.rule_groups = groups

    def _top(self, pid: str) -> str:
        cur = self.provisions[pid]
        while cur["parent_provision_id"] and "-IPP" not in cur["provision_id"]:
            cur = self.provisions[cur["parent_provision_id"]]
        return cur["provision_id"]

    def build_candidates(self):
        t8 = self.spec.table_by_header("Domain", "6.")
        tE = self.spec.table_by_header("Candidate area", "Appendix E")
        e_rows = {r[0]: r for r in tE.rows[1:]}
        cands = []
        for i, (dom, content, logic, status) in enumerate(t8.rows[1:], start=1):
            cid, rtype = CANDIDATE_IDS[dom]
            auto_cls = "A3" if "A3" in status else ("A2" if cid in EXECUTABLE_CANDIDATES else None)
            e_treat = [{"area": a, "content": e_rows[a][1], "governance_treatment": e_rows[a][2]}
                       for a, links in APPENDIX_E_LINKS.items() if cid in links]
            cands.append({
                "rule_id": cid, "product_id": PRODUCT_ID, "domain": dom, "catalogue": "CANDIDATE",
                "catalogue_section": "6",
                "classification": {"rule_type": rtype, "modality": None, "published_type": None},
                "rulespeak_statement": None, "candidate_content": content, "illustrative_logic": logic,
                "authority": {"regulatory_instrument": None, "instrument_type": None, "provision": None,
                              "source_version": None, "source_id": "DOC-INZ-SV-BUSINESS-RULE",
                              "provision_id": None, "provision_ids": []},
                "qualifiers": {"condition_ids": [], "exception_ids": [], "threshold_ids": [],
                               "temporal_constraint_ids": [], "discretion_ids": [], "trigger_ids": []},
                "evidence_requirement_ids": [],
                "automation": {"class": auto_cls, "class_as_published": "A3" if "A3" in status else None,
                               "machine_evaluable": cid in EXECUTABLE_CANDIDATES,
                               "missing_result": "UNKNOWN", "coverage_gap_ids": ["GAP-01"],
                               "interpretation_flag_ids": ["IF-01"] if cid in EXECUTABLE_CANDIDATES else []},
                "provenance_status": "RECONCILIATION_REQUIRED",
                "provenance_status_as_published": status,
                "executable": cid in EXECUTABLE_CANDIDATES,
                "label": CANDIDATE_LABEL if cid in EXECUTABLE_CANDIDATES else None,
                "appendix_e": e_treat, "subject": None, "actor_ids": [],
                "concept_ids": self.concept_refs(content), "rule_group": None,
                "extracted_from": {"table_index": t8.table_index, "row": i, "section": "6"},
                "interpretation_flag_ids": ["IF-01"] if cid in EXECUTABLE_CANDIDATES else [],
            })
        self.candidates = {c["rule_id"]: c for c in cands}
        self.appendix_e = [{"area": r[0], "content": r[1], "governance_treatment": r[2],
                            "candidate_rule_ids": APPENDIX_E_LINKS.get(r[0], [])} for r in tE.rows[1:]]
        # candidate thresholds (Decision 3: funds execute, labelled)
        ths = self.qualifiers["thresholds"]
        money = {"SV-CAND-MAINT-TERTIARY": "TERTIARY", "SV-CAND-MAINT-SCHOOL": "SCHOOL"}
        for c in cands:
            logic = c["illustrative_logic"]
            for m in re.finditer(r"NZD ([\d,]+)(?:/(year|month))?", logic):
                amount = int(m.group(1).replace(",", ""))
                per = m.group(2)
                tag = money.get(c["rule_id"], "MATERNITY")
                tid = f"SV-TH-CAND-{tag}-{(per or 'TOTAL').upper()}"
                ths.append(self._cand_threshold(tid, c["rule_id"], m.group(0), amount, "NZD",
                                                per, executable=True))
                c["qualifiers"]["threshold_ids"].append(tid)
            m = re.search(r"(\d+) hours/week", logic)
            if m:
                tid = "SV-TH-CAND-WORK-HOURS"
                ths.append(self._cand_threshold(tid, c["rule_id"], m.group(0), int(m.group(1)),
                                                "hours", "week", executable=False,
                                                comparator="LESS_THAN_OR_EQUAL"))
                c["qualifiers"]["threshold_ids"].append(tid)
            m = re.search(r"up to (three) programmes", logic)
            if m:
                tid = "SV-TH-CAND-PATHWAY-PROGRAMMES"
                ths.append(self._cand_threshold(tid, c["rule_id"], m.group(0), 3, "programmes",
                                                None, executable=False, comparator="LESS_THAN_OR_EQUAL"))
                c["qualifiers"]["threshold_ids"].append(tid)

    @staticmethod
    def _cand_threshold(tid, rule_id, text, value, unit, per, executable, comparator="GREATER_THAN_OR_EQUAL"):
        return {"id": tid, "rule_id": rule_id, "text": text, "value": value, "unit": unit,
                "per": per, "comparator": comparator, "applies_to_attribute":
                    "available_funds_nzd" if unit == "NZD" else None,
                "version_id": f"spec-v{SPEC_VERSION}-{SPEC_DATE}", "effective_from": None,
                "effective_to": None, "source_provision_id": None,
                "provenance_status": "RECONCILIATION_REQUIRED",
                "label": CANDIDATE_LABEL if executable else None, "executable": executable,
                "derivation": "Section 6 'Illustrative thresholds / logic' column",
                "interpretation_flag_ids": ["IF-01"] if executable else []}

    # ---- evidence ----------------------------------------------------------
    def build_evidence(self):
        t9 = self.spec.table_by_header("Evidence item", "7.")
        reqs, vrules, etypes = [], [], []
        all_rules = {**self.rules, **self.candidates}
        for item, fact, need, dom in t9.rows[1:]:
            code, rule_ids = EVIDENCE_TYPES[item]
            etypes.append({"evidence_type_id": f"EVTYPE-{code}", "evidence_item": item,
                           "supports_fact": fact, "verification_need": need, "typical_rule_domain": dom})
            vrules.append({"verification_rule_id": f"SV-VERIFY-{code}", "evidence_type_id": f"EVTYPE-{code}",
                           "evidence_item": item, "verification_checks": [x.strip() for x in need.split(",")],
                           "verification_need": need, "result_when_unverified": "UNKNOWN",
                           "rule": "Evidence never directly sets a rule result; only facts verified "
                                   "against these checks may be used by A2 checks."})
            for rid in rule_ids:
                rule = all_rules[rid]
                erid = f"SV-EVID-REQ-{rid.removeprefix('SV-')}-{code}"
                reqs.append({
                    "evidence_requirement_id": erid, "rule_id": rid,
                    "description": f"{item} supporting: {fact}",
                    "required_fact_type_id": None, "acceptable_evidence_types": [f"EVTYPE-{code}"],
                    "mandatory": rule["classification"]["modality"] in ("MUST", "MUST_NOT"),
                    "verification_required": True,
                    "source_provision_id": rule["authority"]["provision_id"],
                    "interpretation_flag_ids": ["IF-11"]})
                rule["evidence_requirement_ids"].append(erid)
                self.edge("REFERENCES", erid, f"EVTYPE-{code}")
        # F.5 template instance (SV-APP-005), IF-12
        f5_name, f5 = self.spec.json_template("F.5")
        f5 = {**f5, "acceptable_evidence_types": ["EVTYPE-CHANGE-NOTIFICATION"],
              "interpretation_flag_ids": ["IF-12"]}
        reqs.append(f5)
        self.rules[f5["rule_id"]]["evidence_requirement_ids"].append(f5["evidence_requirement_id"])
        etypes.append({"evidence_type_id": "EVTYPE-CHANGE-NOTIFICATION", "evidence_item": "ChangeNotification",
                       "supports_fact": "material_change_disclosed", "verification_need": None,
                       "typical_rule_domain": "Application", "source": "Appendix F.5/F.6 templates"})
        self.edge("REFERENCES", f5["evidence_requirement_id"], "EVTYPE-CHANGE-NOTIFICATION")
        self.files["evidence/evidence-requirements.json"] = {"evidence_types": etypes,
                                                             "evidence_requirements": reqs}
        self.files["evidence/verification-rules.json"] = {"verification_rules": vrules}
        self.evidence_requirements = reqs
        self.evidence_types = etypes

    # ---- decision checks ----------------------------------------------------
    def build_checks(self):
        t11 = self.spec.table_by_header("Check ID", "7.")
        doc_rows = {r[0]: r for r in t11.rows[1:]}
        _, f8 = self.spec.json_template("F.8")
        P = "application."
        checks = [
            {"check_id": "SV-CHECK-APP-001", "evaluates_rule_id": None,
             "evaluates_rule_ids": ["SV-FORM-001", "SV-FORM-002", "SV-FORM-003", "SV-FORM-004",
                                    "SV-ID-001", "SV-PHOTO-001", "SV-FEE-001", "SV-FEE-002"],
             "evaluates_rule_ids_online": ["SV-ONLINE-001", "SV-ONLINE-002", "SV-ONLINE-003",
                                           "SV-ONLINE-004", "SV-ONLINE-005", "SV-ONLINE-006",
                                           "SV-ONLINE-008", "SV-ONLINE-009", "SV-ONLINE-010",
                                           "SV-ONLINE-011"],
             "automation_class": "PER_RULE",
             "inputs": [{"fact_path": P + a, "required": True} for a in (
                 "approved_form_used", "persons_in_application", "completed_in_english",
                 "application_signed", "identity_document_provided", "photographs_provided",
                 "prescribed_fee_paid", "immigration_levy_paid")],
             "interpretation_flag_ids": ["IF-20"]},
            {"check_id": "SV-CHECK-IVL-001", "evaluates_rule_id": "SV-FEE-003",
             "evaluates_rule_id_online": "SV-ONLINE-012", "exception_rule_id": "SV-IVL-001",
             "automation_class": "A1",
             "inputs": [{"fact_path": P + "scholarship_administrator", "required": False},
                        {"fact_path": P + "ivl_paid", "required": True}],
             "outcomes": {"mfat_scholarship": "NOT_APPLICABLE", "true": "SATISFIED",
                          "false": "NOT_SATISFIED", "missing": "UNKNOWN"}},
            {"check_id": "SV-CHECK-U10-001", "evaluates_rule_id": "SV-U10-001", "automation_class": "A1",
             "inputs": [{"fact_path": P + "applicant_age", "required": True},
                        {"fact_path": P + "lives_with_parent_or_legal_guardian", "required": True}],
             "outcomes": {"age_10_or_over": "NOT_APPLICABLE", "true": "SATISFIED", "false": "NOT_SATISFIED",
                          "missing": "UNKNOWN"},
             "note": "Provider obligation under the Code (RISK-04): reported, not a visa-grant criterion.",
             "interpretation_flag_ids": ["IF-21"]},
            {"check_id": "SV-CHECK-WORK-001", "evaluates_rule_id": "SV-WORK-001", "automation_class": "A2",
             "trigger_fact_paths": [P + "recorded_work_hours_per_week"],
             "inputs": [{"fact_path": P + a, "required": True} for a in (
                 "work_permitted_by_visa_conditions", "visa_condition_max_work_hours_per_week",
                 "recorded_work_hours_per_week", "activity_date")],
             "outcomes": {"within": "SATISFIED", "outside": "NOT_SATISFIED", "missing": "UNKNOWN"}},
            {"check_id": "SV-CHECK-FIN-001", "evaluates_rule_id": "SV-CAND-MAINT-TERTIARY",
             "evaluates_rule_ids": ["SV-CAND-MAINT-TERTIARY", "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"],
             "related_verified_rule_ids": ["SV-FIN-001"], "automation_class": "A2",
             "inputs": [{"fact_path": P + a, "required": r} for a, r in (
                 ("student_type", True), ("study_duration_months", True), ("available_funds_nzd", True),
                 ("pregnant", False), ("publicly_funded_maternity_care_available", False),
                 ("prepaid_accommodation", False))],
             "threshold_ids": ["SV-TH-CAND-TERTIARY-YEAR", "SV-TH-CAND-TERTIARY-MONTH",
                               "SV-TH-CAND-SCHOOL-YEAR", "SV-TH-CAND-SCHOOL-MONTH",
                               "SV-TH-CAND-MATERNITY-TOTAL"],
             "label": CANDIDATE_LABEL, "provenance_status": "RECONCILIATION_REQUIRED",
             "deviation": "Decision 3: executes with candidate figures although the document says "
                          "'Only execute after authoritative product threshold is reconciled'.",
             "interpretation_flag_ids": ["IF-01", "IF-15"]},
            {"check_id": "SV-CHECK-DISC-001", "evaluates_rule_id": "SV-DEC-002", "automation_class": "A4",
             "inputs": [{"fact_path": "assessment.rule_evaluations", "required": False}],
             "context_rule_ids": ["SV-DEC-001", "SV-DEC-003", "SV-DEC-004", "SV-CHAR-001", "SV-CHAR-002",
                                  "SV-CHAR-003", "SV-CHAR-004", "SV-CHAR-005", "SV-CHAR-006"],
             "outcomes": {"always": "DISCRETION_REQUIRED"}},
            {**f8, "origin": "Appendix F.8 template", "trigger_fact_paths": [],
             "inputs": f8["inputs"] + [{"fact_path": P + "material_change_occurred", "required": False}],
             "note": "material_change_occurred=false -> NOT_APPLICABLE (nothing to disclose)."},
            # ---- checks implied by test cases (IF-13) ----
            {"check_id": "SV-CHECK-MIN-001", "evaluates_rule_id": "SV-MIN-001", "automation_class": "A4",
             "origin": "Implied by TC-03 (not in Table 11)",
             "inputs": [{"fact_path": P + a, "required": True} for a in (
                 "applicant_age", "applicant_marital_status", "parental_consent_established")],
             "outcomes": {"adult_or_married": "NOT_APPLICABLE", "consent_verified": "NOT_APPLICABLE",
                          "consent_not_established": "DISCRETION_REQUIRED", "missing_age": "UNKNOWN"},
             "interpretation_flag_ids": ["IF-13"]},
            {"check_id": "SV-CHECK-HEALTH-001", "evaluates_rule_id": "SV-CAND-ELIGIBILITY",
             "related_verified_rule_ids": ["SV-HEALTH-001"], "automation_class": "A3",
             "origin": "Implied by TC-07 (not in Table 11)",
             "inputs": [{"fact_path": "evidence.MedicalCertificate|ChestXray", "required": True}],
             "outcomes": {"evidence_received": "HUMAN_REVIEW_REQUIRED", "missing": "UNKNOWN"},
             "interpretation_flag_ids": ["IF-13", "IF-18"]},
            {"check_id": "SV-CHECK-EDU-009", "evaluates_rule_id": "SV-EDU-009",
             "evaluates_rule_id_school": "SV-SCHOOL-009", "automation_class": "A3",
             "origin": "Implied by TC-09 (not in Table 11)",
             "trigger_fact_paths": [P + "provider_known_visa_condition_breach",
                                    P + "provider_suspected_visa_condition_breach"],
             "inputs": [{"fact_path": P + a, "required": True} for a in (
                 "provider_known_visa_condition_breach", "provider_reported_breach_to_inz")],
             "outcomes": {"known_verified_not_reported": "NOT_SATISFIED",
                          "known_verified_reported": "SATISFIED", "otherwise": "HUMAN_REVIEW_REQUIRED"},
             "interpretation_flag_ids": ["IF-13", "IF-14"]},
            {"check_id": "SV-CHECK-INSTR-001", "evaluates_rule_id": "SV-INSTR-001",
             "evaluates_rule_id_online": "SV-ONLINE-007", "automation_class": "A2",
             "origin": "Rules marked 'A2 / gap' in 5.1 and D.1",
             "inputs": [], "outcomes": {"always": "SOURCE_COVERAGE_GAP"}, "coverage_gap_ids": ["GAP-01"],
             "interpretation_flag_ids": ["IF-04", "IF-13"]},
        ]
        for c in checks:
            row = doc_rows.get(c["check_id"])
            if row:
                c["purpose"] = row[1]
                c["doc_inputs"] = row[2]
                c["doc_routing_logic"] = row[3]
                c.setdefault("origin", "Table 11 (Section 7)")
            c.setdefault("purpose", None)
            rid = c.get("evaluates_rule_id")
            rule = self.rules.get(rid) or self.candidates.get(rid)
            c.setdefault("source_provision_id", rule["authority"]["provision_id"] if rule else None)
            c.setdefault("interpretation_flag_ids", [])
            for target in sorted({x for k in ("evaluates_rule_id", "evaluates_rule_id_online",
                                              "evaluates_rule_id_school", "exception_rule_id")
                                  if c.get(k) for x in [c[k]]}
                                 | set(c.get("evaluates_rule_ids", []))
                                 | set(c.get("evaluates_rule_ids_online", []))):
                self.edge("EVALUATES", c["check_id"], target)
        self.checks = checks
        self.files["decisioning/decision-checks.json"] = {
            "automation_classes": [{"class": r[0], "meaning": r[1], "agent_action": r[2]}
                                   for r in self.spec.table_by_header("Automation class", "7.").rows[1:]],
            "decision_checks": checks}

    # ---- lifecycle ---------------------------------------------------------
    def build_lifecycle(self):
        t12 = self.spec.table_by_header("Entity", "8.")
        states, trans = {}, []
        auth_map = {"Immigration Act 2009 s 62": ["SV-LIFE-001"], "Immigration Act 2009 s 63": ["SV-LIFE-003"],
                    "Immigration Act 2009 ss 64 or 66": ["SV-LIFE-004", "SV-LIFE-005"]}
        actor_map = {"Applicant": "ACTOR-APPLICANT", "Immigration Officer": "ACTOR-IMMIGRATION-OFFICER",
                     "Minister": "ACTOR-MINISTER"}
        ids = ["SV-TRANS-LODGE", "SV-TRANS-ASSESS", "SV-TRANS-FURTHER-INFO", "SV-TRANS-INFO-RECEIVED",
               "SV-TRANS-DECISION-READY", "SV-TRANS-DECISION", "SV-TRANS-VISA-GRANT",
               "SV-TRANS-VISA-CURRENT", "SV-TRANS-VISA-EXPIRE", "SV-TRANS-VISA-CANCEL"]
        for (ent, frm, to, trig, actor), tid in zip(t12.rows[1:], ids):
            etype = "VisaApplication" if ent == "Application" else "Visa"
            for s in (frm, to):
                states.setdefault(snake(s), {"state_id": snake(s), "name": s, "entity_type": etype})
            rules = auth_map.get(actor, [])
            if tid == "SV-TRANS-DECISION":
                rules = ["SV-DEC-002"]          # Appendix F.10
            if tid == "SV-TRANS-FURTHER-INFO":
                rules = ["SV-EVID-001", "SV-EVID-002", "SV-HEALTH-001"]   # r 10(3)(a)-(c)
            actors = [v for k, v in actor_map.items() if k in actor]
            trans.append({"transition_id": tid, "entity_type": etype, "from_state_id": snake(frm),
                          "to_state_id": snake(to), "trigger": trig, "actor_authority": actor,
                          "actor_id": actors[0] if actors else None, "actor_ids": actors,
                          "authorising_rule_ids": rules, "required_fact_ids": [],
                          "audit_event_required": True, "interpretation_flag_ids": ["IF-23"]})
            trig_id = "SV-TRIG-" + tid.removeprefix("SV-TRANS-")
            self.qualifiers["triggers"].append({"id": trig_id, "rule_id": None, "transition_id": tid,
                                                "text": trig, "source_provision_id": None,
                                                "derivation": "Table 12 Trigger column"})
            self.edge("CAUSES_TRANSITION", trig_id, tid)
            self.edge("APPLIES_TO", tid, snake(frm), role="from")
            self.edge("APPLIES_TO", tid, snake(to), role="to")
            for r in rules:
                self.edge("DERIVED_FROM", tid, r)
        self.files["decisioning/states.json"] = {"states": list(states.values())}
        self.files["decisioning/state-transitions.json"] = {"state_transitions": trans}

    # ---- governance --------------------------------------------------------
    def build_governance(self):
        t14 = self.spec.table_by_header("ID", "11.")
        _, f11 = self.spec.json_template("F.11")
        gaps, risks = [], []
        for gid, risk, effect, control in t14.rows[1:]:
            rec = {"id": gid, "subject": risk, "effect": effect, "control": control}
            if gid.startswith("GAP"):
                rec.update({"coverage_gap_id": gid,
                            "reason_type": f11["reason_type"] if gid == "GAP-01" else None,
                            "missing_source_type": f11["missing_source_type"] if gid == "GAP-01" else None,
                            "retrieval_impact": f11["retrieval_impact"] if gid == "GAP-01" else None,
                            "automated_assessment_impact": f11["automated_assessment_impact"] if gid == "GAP-01" else None,
                            "required_result": "SOURCE_COVERAGE_GAP", "must_not_infer": True,
                            "template_example_id": f11["coverage_gap_id"] if gid == "GAP-01" else None})
                gaps.append(rec)
            else:
                risks.append(rec)
        gap_targets = {"GAP-01": ["NZ-INZ-IMMIGRATION-INSTRUCTIONS", "SV-INSTR-001", "SV-ONLINE-007",
                                  *self.candidates.keys()],
                       "GAP-02": ["DOC-INZ-SV-BUSINESS-RULE", *self.candidates.keys()]}
        for g in gaps:
            g["applies_to"] = gap_targets[g["id"]]
            for t in g["applies_to"]:
                self.edge("APPLIES_TO", g["id"], t)
        self.gaps = gaps
        self.files["governance/source-coverage.json"] = {
            "source_coverage": [{"source_id": s["source_id"], "title": s["title"],
                                 "poc_coverage": s["poc_coverage"], "coverage_status": s["coverage_status"]}
                                for s in self.sources.values()],
            "coverage_gaps": gaps, "risks": risks,
            "assurance_checklist": [{"area": r[0], "required_output": r[1], "completion_criterion": r[2]}
                                    for r in self.spec.table_by_header("Assurance area", "Appendix G").rows[1:]],
            "audit_events": [{"event": r[0], "minimum_data": r[1]}
                             for r in self.spec.table_by_header("Event", "Appendix G").rows[1:]],
            "incorporation_matrix": [{"component": r[0], "status": r[1], "treatment": r[2]}
                                     for r in self.spec.table_by_header("Required POC component", "Appendix C").rows[1:]],
        }

    def build_flags(self):
        D3 = "Product owner decision 3 (build plan, 23 Nov 2026)"
        F = self.flag
        F("IF-01", "Candidate funds thresholds execute; TC-06 expectation adjusted",
          "The document says SV-CHECK-FIN-001 should 'only execute after authoritative product threshold is "
          "reconciled' and TC-06 expects SOURCE_COVERAGE_GAP. By product-owner decision the candidate figures "
          "(tertiary NZD 20,000/yr or 1,667/month; school NZD 17,000/yr or 1,417/month; maternity NZD 9,000) are "
          f"loaded and the check runs. Every such Threshold and evaluation carries the label '{CANDIDATE_LABEL}' "
          "and provenance_status RECONCILIATION_REQUIRED. TC-06 now expects that label instead of "
          "SOURCE_COVERAGE_GAP.", ["SV-CHECK-FIN-001", "TC-06", "SV-CAND-MAINT-TERTIARY",
                                   "SV-CAND-MAINT-SCHOOL", "SV-CAND-MATERNITY"], kind="DEVIATION", decision=D3)
        F("IF-02", "Published rule type codes not defined",
          "Section 5 uses type codes OR, PERM, DR and SR without defining them. They are kept verbatim as "
          "classification.published_type; rule_type (Obligation/Prohibition/Permission/Definition) is derived "
          "from the MUST / MUST NOT / MAY / 'is defined as' wording of each statement.", ["rules/rules.json"])
        F("IF-03", "Definitional rules have no deontic modality",
          "Statements of the form 'is (not) defined as' (SV-DEC-001, SV-LIFE-001, SV-ENTRY-001, SV-FAM-004) carry "
          "none of MUST, MUST_NOT or MAY; modality is null and rule_type is Definition.",
          ["SV-DEC-001", "SV-LIFE-001", "SV-ENTRY-001", "SV-FAM-004"])
        F("IF-04", "Compound automation classes",
          "Some rules publish two classes (A3/A4, A2/A3, A1/A3) or a gap marker ('A2 / gap', "
          "'A2 / SOURCE_COVERAGE_GAP'). The engine uses the most restrictive class; a gap marker routes the rule "
          "to SOURCE_COVERAGE_GAP under GAP-01. The published value is kept in automation.class_as_published.",
          ["SV-INSTR-001", "SV-ONLINE-007", "SV-CHAR-001", "SV-CHAR-002", "SV-CHAR-004", "SV-SCHOOL-003",
           "SV-PRIV-004"])
        F("IF-05", "Rule domain derived from rule-ID prefix",
          "The document only shows a domain in the 12.1 example (Application). Each rule's domain is taken from "
          "its ID prefix using the Table 9 'typical rule domain' names where they exist.", ["rules/rules.json"])
        F("IF-06", "Qualifiers derived from RuleSpeak wording",
          "The document defines Condition/Exception/Threshold/TemporalConstraint/Discretion/Trigger node types "
          "but lists no instances. They are derived mechanically: 'unless ...' -> Exception; 'if / only if / "
          "where / that may affect ...' -> Condition; time phrases -> TemporalConstraint; numeric figures in the "
          "statement -> Threshold; every A4 rule -> Discretion. Each qualifier stores the exact text span; no "
          "wording is added.", ["rules/conditions.json", "rules/exceptions.json", "rules/thresholds.json",
                                "rules/temporal-constraints.json", "rules/discretions.json"])
        F("IF-07", "Provision sub-unit types",
          "Section 3 lists Part, Section, Subsection, Regulation, Clause, Instruction and F.2 adds Paragraph. "
          "Sub-units of regulations and Code clauses reuse Subsection / Paragraph / Subparagraph; Privacy Act "
          "IPPs are modelled as Paragraph-level children of s 22. Parts are not modelled (the document never "
          "cites one).", ["regulatory/provisions.json"])
        F("IF-08", "Instrument types for sources other than the Immigration Act",
          "The document gives instrument_type only for the Immigration Act (PrincipalAct / 'Principal Act'). "
          "Regulations, CodeOfPractice, ImmigrationInstruction (from F.11), Method and OriginalWork are used for "
          "the other Table 1 sources.", ["regulatory/sources.json"])
        F("IF-09", "Source versions not stated",
          "Only the Immigration Act version (2026-07-10) is stated. Other version_id values are null until the "
          "legislation fetch (Phase 2) sets them from legislation.govt.nz.", ["regulatory/sources.json"],
          decision="Product owner, 23 Nov 2026: add legislation sources later")
        F("IF-10", "Provision text not yet retrieved",
          "Phase 2 (fetching exact provision wording from legislation.govt.nz) is deferred. Every provision has "
          f"source_text null and source_text_status {SOURCE_TEXT_NOT_RETRIEVED}. The Education (Pastoral Care) "
          "Code is not published on legislation.govt.nz, so its clauses will stay "
          f"{SOURCE_TEXT_NOT_RETRIEVED} under decision 8 even after Phase 2. Appendix G's 'every VERIFIED rule "
          "has ... source text' criterion is therefore not yet met.", ["regulatory/provisions.json"],
          decision="Product owner, 23 Nov 2026: add legislation sources later")
        F("IF-11", "Rule-to-evidence mapping",
          "Table 9 maps evidence items to a 'typical rule domain', not to rules. Evidence requirements are linked "
          "to the rules in that domain (e.g. Police certificate -> SV-CHAR-001..005).",
          ["evidence/evidence-requirements.json"])
        F("IF-12", "Template ID inconsistency for SV-APP-005",
          "12.1 lists evidence_requirement_ids [SV-EVID-CHANGE-001] for SV-APP-005; F.5 defines the same "
          "requirement as SV-EVID-REQ-001. The complete F.5 object is used. Condition IDs follow the 12.1 "
          "pattern SV-COND-<prefix>-NNN, so SV-APP-005's derived condition is SV-COND-APP-001.",
          ["SV-APP-005", "SV-EVID-REQ-001"])
        F("IF-13", "Decision checks implied by test cases",
          "Table 11 defines six checks and F.8 one more. TC-03, TC-07 and TC-09 need checks the table does not "
          "list, so SV-CHECK-MIN-001, SV-CHECK-HEALTH-001 and SV-CHECK-EDU-009 are added. SV-CHECK-INSTR-001 "
          "reports the 'A2 / gap' rules as SOURCE_COVERAGE_GAP in every assessment.",
          ["SV-CHECK-MIN-001", "SV-CHECK-HEALTH-001", "SV-CHECK-EDU-009", "SV-CHECK-INSTR-001"])
        F("IF-14", "TC-09 expects NOT_SATISFIED for an A3 rule",
          "SV-EDU-009 / SV-SCHOOL-009 are A3, yet TC-09 expects NOT_SATISFIED. The check returns NOT_SATISFIED "
          "only when verified facts show the provider knew of a breach and did not report it; any other case "
          "(suspected breach, unverified facts) is HUMAN_REVIEW_REQUIRED.", ["SV-CHECK-EDU-009", "TC-09"])
        F("IF-15", "Maintenance calculation by duration",
          "Section 6 says 'calculations by study duration' with a yearly and monthly figure but no method. The "
          "check uses the monthly figure x months for study under 12 months, otherwise the yearly figure pro rata "
          "(months / 12). Maternity funds are added when the applicant is pregnant and publicly funded maternity "
          "care is unavailable. The 'prepaid accommodation adjustment' (Appendix E) has no figure, so when "
          "prepaid accommodation is declared it is listed as an unresolved requirement, not applied.",
          ["SV-CHECK-FIN-001"])
        F("IF-16", "Overall status aggregation",
          "12.2 shows overall_status but not how it is derived. Precedence used: AMBIGUOUS > NOT_SATISFIED > "
          "UNKNOWN > HUMAN_REVIEW_REQUIRED. Because the grant is always DISCRETION_REQUIRED, an assessment never "
          "reaches a better status than HUMAN_REVIEW_REQUIRED (matching the 12.2 example).", ["engine"])
        F("IF-17", "Actors that are rule subjects but not 'Parties'",
          "Decision-Maker, Agency and Individual are subjects of verified rules but not in Table 3's Parties "
          "group. They are modelled as actors marked derived_from 'rule subject'. Subjects that are objects "
          "(e.g. 'The Insurance', 'The Refund Policy') get no actor edge.", ["regulatory/actors.json"])
        F("IF-18", "Health outcome for TC-07",
          "TC-07 expects HUMAN_REVIEW_REQUIRED for health. Health is part of the candidate core-eligibility rule "
          "(Section 6), which has no published class; the agent contract forbids scoring health, so the check "
          "is A3: medical evidence received -> HUMAN_REVIEW_REQUIRED, none -> UNKNOWN.", ["SV-CHECK-HEALTH-001"])
        F("IF-19", "Concept typing",
          "concept_type and domain are set to the Table 3 concept group (F.3's 'Document' type cannot be derived "
          "for every term). Product concepts are Visa nodes; Temporary Entry Class Visa PARENT_OF Student Visa "
          "PARENT_OF each named Student Visa product. English Language and Foreign Government Supported Student "
          "Visa come only from Section 6 and are RECONCILIATION_REQUIRED.", ["regulatory/concepts.json"])
        F("IF-20", "Online applications",
          "Table 11 lists SV-CHECK-APP-001 inputs matching r 10 (paper form). For applications with "
          "channel 'online' the same check evaluates the D.1 online rules (r 23AA / r 23A) instead.",
          ["SV-CHECK-APP-001"])
        F("IF-21", "Under-10 check is a provider obligation",
          "SV-U10-001 binds the education provider (Code cl 51), not the visa decision (RISK-04). The check runs "
          "as Table 11 requires but its evaluation is marked as a provider obligation.", ["SV-CHECK-U10-001"])
        F("IF-22", "Test types",
          "F.12 has test_type 'PASS' only. Types are taken from Appendix G's testing list (PASS, FAIL, MISSING, "
          "BOUNDARY, EXCEPTION, DISCRETION, SOURCE_GAP) plus HUMAN_REVIEW for TC-07 and EXPLANATION for TC-10.",
          ["testing/test-cases.json"])
        F("IF-23", "Lifecycle IDs and authorising rules",
          "State IDs are the Table 12 state names in UPPER_SNAKE (matching F.10's DECISION_READY). Authorising "
          "rules are linked only where Table 12 or F.10 cites authority: s 62 -> SV-LIFE-001, s 63 -> SV-LIFE-003, "
          "ss 64/66 -> SV-LIFE-004/005, decision -> SV-DEC-002 (F.10), further information -> r 10(3)(a)-(c).",
          ["decisioning/state-transitions.json"])
        F("IF-24", "Operational nodes outside the ontology",
          "The loader writes one LoadLog node per run (build plan Phase 3). It is not part of the Section 3 "
          "ontology. Concepts, actors and fact types use the Domain layer label with a 'kind' property because "
          "Section 3 has no dedicated node type for them.", ["graph"])
        self.files["governance/interpretation-flags.json"] = {"interpretation_flags": self.flags}

    # ---- testing -----------------------------------------------------------
    def build_testing(self):
        from .synthetic import synthetic_applications, expected_results
        t13 = self.spec.table_by_header("Test", "10.")
        types = {"TC-01": "PASS", "TC-02": "MISSING", "TC-03": "DISCRETION", "TC-04": "EXCEPTION",
                 "TC-05": "BOUNDARY", "TC-06": "SOURCE_GAP", "TC-07": "HUMAN_REVIEW", "TC-08": "DISCRETION",
                 "TC-09": "FAIL", "TC-10": "EXPLANATION"}
        exp = expected_results()
        apps = synthetic_applications()
        tcs = []
        for tc, scenario, expected in t13.rows[1:]:
            e = exp[tc]
            tcs.append({"test_case_id": tc, "test_type": types[tc], "scenario": scenario,
                        "expected_outcome_as_published": expected,
                        "rule_id": e["rule_id"], "check_id": e["check_id"],
                        "application_id": apps[tc]["application_id"],
                        "input_facts": [f["fact_id"] for f in apps[tc]["facts"]],
                        "evidence_items": [x["evidence_item_id"] for x in apps[tc]["evidence_items"]],
                        "expected_status": e["expected_status"], "provenance_required": True,
                        "deviation": e.get("deviation"),
                        "interpretation_flag_ids": ["IF-22"] + e.get("interpretation_flag_ids", [])})
            self.edge("EVALUATES", tc, e["check_id"])
        self.files["testing/test-cases.json"] = {"test_cases": tcs}
        self.files["testing/synthetic-applications.json"] = {"synthetic": True,
                                                             "applications": list(apps.values())}
        self.files["testing/expected-results.json"] = {"expected_results": exp}
        self.apps = apps
        self.test_cases = tcs

    # ---- schema ------------------------------------------------------------
    def build_schema(self):
        t2 = self.spec.table_by_header("Layer", "3.")
        templates = {}
        for sub in ("F.1", "F.2", "F.3", "F.4", "F.5", "F.6", "F.7", "F.8", "F.9", "F.10", "F.11", "F.12"):
            name, obj = self.spec.json_template(sub)
            templates[name] = obj
        _, rule_yaml = self.spec.template("12.1")
        _, assess = self.spec.template("12.2")
        self.files["schema/ontology.yaml"] = {
            "ontology": {"product_id": PRODUCT_ID, "spec_version": SPEC_VERSION, "spec_date": SPEC_DATE,
                         "layers": [{"layer": r[0], "node_types": [x.strip() for x in r[1].split(",")],
                                     "purpose": r[2]} for r in t2.rows[1:]],
                         "graph_path": "EvidenceItem -> ObservedFact -> VerifiedFact -> DecisionCheck -> RuleEvaluation",
                         "explanation_chain": "RuleEvaluation -> DecisionCheck -> Rule -> Provision -> Instrument"},
            "templates": {"business_rule_yaml_12_1": yaml.safe_load(rule_yaml),
                          "assessment_json_12_2": json.loads(assess), **templates},
        }
        t10 = self.spec.table_by_header("Automation class", "7.")
        self.files["schema/enumerations.yaml"] = {"enumerations": {
            "evaluation_outcome": ["SATISFIED", "NOT_SATISFIED", "UNKNOWN", "NOT_APPLICABLE",
                                   "HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED", "AMBIGUOUS",
                                   "SOURCE_COVERAGE_GAP"],
            "automation_class": {r[0]: {"meaning": r[1], "agent_action": r[2]} for r in t10.rows[1:]},
            "modality": ["MUST", "MUST_NOT", "MAY"],
            "rule_type": [x.strip() for x in t2.rows[3][1].split(",")],
            "published_rule_type_code": ["OR", "PERM", "DR", "SR"],
            "provenance_status": ["VERIFIED_PRIMARY_SOURCE", "RECONCILIATION_REQUIRED"],
            "verification_status": ["VERIFIED", "UNVERIFIED"],
            "interpretation_status": ["CONSISTENT"],
            "source_status": ["IN_FORCE"],
            "coverage_status": ["PARTIAL", "NOT_COVERED"],
            "source_coverage": ["PARTIAL"],
            "source_text_status": ["RETRIEVED", SOURCE_TEXT_NOT_RETRIEVED],
            "coverage_gap_reason_type": ["EXCLUDED_SOURCE"],
            "assessment_impact": ["PARTIAL", "BLOCKED"],
            "evaluated_by": ["RULE_ENGINE"],
            "threshold_label": [CANDIDATE_LABEL],
            "audit_event": [r[0] for r in self.spec.table_by_header("Event", "Appendix G").rows[1:]],
            "test_type": ["PASS", "FAIL", "MISSING", "BOUNDARY", "EXCEPTION", "DISCRETION", "SOURCE_GAP",
                          "HUMAN_REVIEW", "EXPLANATION"],
        }}
        vocab = self.relationship_vocabulary()
        desc = {
            "MADE_UNDER": ("AuthoritySource", "AuthoritySource", "Subordinate instrument made under its parent Act."),
            "CONTAINS": ("AuthoritySource|InstrumentVersion|DecisionOutcome|Application",
                         "InstrumentVersion|Provision|RuleEvaluation|EvidenceItem",
                         "Source contains version; version contains top-level provisions (FT-012)."),
            "PARENT_OF": ("Provision|Visa", "Provision|Visa", "Provision hierarchy; product hierarchy."),
            "REFERENCES": ("Rule|EvidenceRequirement", "Domain|Evidence", "Rule statement uses a regulatory concept; requirement accepts an evidence type."),
            "AMENDS": ("InstrumentVersion", "InstrumentVersion", "Declared; no instance in spec v0.2."),
            "SUPERSEDES": ("Rule|Threshold|InstrumentVersion", "Rule|Threshold|InstrumentVersion",
                           "Declared; no instance in spec v0.2."),
            "DERIVED_FROM": ("Rule|StateTransition", "Provision|Rule", "Rule derives from provision (FT-011); "
                             "transition authorised by rule."),
            "APPLIES_TO": ("Rule|Application|StateTransition|CoverageGap|InterpretationFlag|RuleEvaluation",
                           "Visa|State|Rule|AuthoritySource|Application|*", "Scope link (FT-002)."),
            "IMPOSES_OBLIGATION_ON": ("Rule", "Domain(actor)", "MUST rule binds an actor."),
            "PROHIBITS": ("Rule", "Domain(actor)", "MUST NOT rule binds an actor (FT-018)."),
            "PERMITS": ("Rule", "Domain(actor)", "MAY rule empowers an actor (FT-017)."),
            "HAS_CONDITION": ("Rule", "Condition", ""), "HAS_EXCEPTION": ("Rule", "Exception", ""),
            "HAS_THRESHOLD": ("Rule", "Threshold", ""), "HAS_TIME_CONSTRAINT": ("Rule", "TemporalConstraint", ""),
            "HAS_DISCRETION": ("Rule", "Discretion", ""),
            "REQUIRES_EVIDENCE": ("Rule", "EvidenceRequirement", ""),
            "SUPPORTS_FACT": ("EvidenceItem", "ObservedFact", "FT-004"),
            "VERIFIED_AS": ("ObservedFact", "VerifiedFact", "FT-005"),
            "INPUT_TO": ("ObservedFact|VerifiedFact", "DecisionCheck|RuleEvaluation", "FT-006"),
            "EVALUATES": ("DecisionCheck|RuleEvaluation|TestCase", "Rule|DecisionCheck", "FT-010"),
            "PRODUCES_OUTCOME": ("DecisionCheck|RuleEvaluation|Application", "RuleEvaluation|DecisionOutcome", ""),
            "CAUSES_TRANSITION": ("Trigger", "StateTransition", ""),
            "EXPLAINED_BY": ("RuleEvaluation", "Explanation", ""),
        }
        self.files["schema/relationship-types.yaml"] = {"relationship_types": [
            {"type": v, "from": desc[v][0], "to": desc[v][1], "description": desc[v][2]} for v in vocab]}
        self.files["schema/agent-contract.yaml"] = {"agent_contract": {
            "poc_scope_notice": self.spec.scope_notice(),
            "rules": self.spec.bullets("9."),
            "automation_actions": {r[0]: r[2] for r in t10.rows[1:]},
            "definition_of_done": self.spec.bullets("13."),
            "product_owner_decisions": [
                "No automated grant or decline: the final grant is always DISCRETION_REQUIRED (SV-CHECK-DISC-001).",
                "Synthetic applications only.",
                f"Candidate funds thresholds execute, labelled '{CANDIDATE_LABEL}' (IF-01).",
            ]}}

    # ---- edges for rules etc -----------------------------------------------
    def build_rule_edges(self):
        all_rules = {**self.rules, **self.candidates}
        for r in all_rules.values():
            rid = r["rule_id"]
            self.edge("APPLIES_TO", rid, PRODUCT_ID)
            for i, pid in enumerate(r["authority"]["provision_ids"]):
                self.edge("DERIVED_FROM", rid, pid, primary=(pid == r["authority"]["provision_id"]))
            rel = EDGE_BY_MODALITY.get(r["classification"]["modality"])
            if rel:
                for a in r["actor_ids"]:
                    self.edge(rel, rid, a)
            for c in r["concept_ids"]:
                self.edge("REFERENCES", rid, c)
            q = r["qualifiers"]
            for key, rel in (("condition_ids", "HAS_CONDITION"), ("exception_ids", "HAS_EXCEPTION"),
                             ("threshold_ids", "HAS_THRESHOLD"), ("temporal_constraint_ids", "HAS_TIME_CONSTRAINT"),
                             ("discretion_ids", "HAS_DISCRETION"), ("trigger_ids", "HAS_CONDITION")):
                for x in q[key]:
                    self.edge(rel, rid, x)
            for er in r["evidence_requirement_ids"]:
                self.edge("REQUIRES_EVIDENCE", rid, er)
        for f in self.flags:
            for t in f["applies_to"]:
                if t in all_rules or t.startswith(("SV-CHECK", "TC-")):
                    self.edge("APPLIES_TO", f["flag_id"], t)

    # ---- assemble ----------------------------------------------------------
    def build(self):
        self.build_sources()
        self.build_concepts()
        self.build_fact_types()
        self.build_rules()
        self.build_rule_groups()
        self.build_candidates()
        self.build_evidence()
        self.build_checks()
        self.build_lifecycle()
        self.build_governance()
        self.build_flags()
        self.build_testing()
        self.build_schema()
        self.build_rule_edges()
        q = self.qualifiers
        self.files["rules/rules.json"] = {
            "product_id": PRODUCT_ID, "rules": list(self.rules.values()),
            "candidate_rules": list(self.candidates.values()),
            "candidate_inventory_appendix_e": self.appendix_e,
            "rule_groups": self.rule_groups}
        self.files["rules/conditions.json"] = {"conditions": q["conditions"], "triggers": q["triggers"]}
        self.files["rules/exceptions.json"] = {"exceptions": q["exceptions"]}
        self.files["rules/thresholds.json"] = {"thresholds": q["thresholds"]}
        self.files["rules/temporal-constraints.json"] = {"temporal_constraints": q["temporal"]}
        self.files["rules/discretions.json"] = {"discretions": q["discretions"]}
        self.files["regulatory/provisions.json"] = {"provisions": list(self.provisions.values())}
        self.files["regulatory/relationships.json"] = {"relationships": self.edges}
        self.files["governance/provenance.json"] = {
            "document": {"file": self.spec.path.name, "sha256": sha256(self.spec.path),
                         "version_line": self.spec.version_line(), "extracted_on": date.today().isoformat()},
            "rule_provenance": [{
                "rule_id": r["rule_id"], "catalogue": r["catalogue"], "provenance_status": r["provenance_status"],
                "source_id": r["authority"]["source_id"], "provision_ids": r["authority"]["provision_ids"],
                "source_version": r["authority"]["source_version"],
                "source_text_status": SOURCE_TEXT_NOT_RETRIEVED if r["authority"]["provision_ids"] else None,
                "extracted_from": r["extracted_from"]} for r in {**self.rules, **self.candidates}.values()]}
        return self.files


def write_files(files: dict, root: Path = ROOT) -> list[Path]:
    written = []
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".yaml"):
            p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110))
        elif rel.endswith(".json"):
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        else:
            continue
        written.append(p)
    return written


def write_application_files(apps: dict, root: Path = ROOT):
    d = root / "testing" / "applications"
    d.mkdir(parents=True, exist_ok=True)
    for tc, app in apps.items():
        (d / f"{tc}.json").write_text(json.dumps(app, indent=2, ensure_ascii=False) + "\n")


def run() -> dict:
    b = Builder(Spec())
    files = b.build()
    write_files(files)
    write_application_files(b.apps)
    return {"rules": len(b.rules), "candidate_rules": len(b.candidates), "provisions": len(b.provisions),
            "relationships": len(b.edges), "checks": len(b.checks), "flags": len(b.flags)}
