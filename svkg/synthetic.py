"""Synthetic personas SVP-001..SVP-050 (decision 4: synthetic data only).

Each persona is a fictional applicant with a backstory, a realistic document trail and the facts the decision
checks read. Nothing here describes a real person. Names, passport numbers, contact details and providers are
invented, every application carries synthetic=True, and email addresses use the reserved .example domain.

Shape: application header + F.6 evidence items + F.7 facts. A fact with verification_status VERIFIED is a
VerifiedFact; anything else is only an ObservedFact. Fact attributes are addressed by the checks as
application.<attribute>. The persona narrative sits under "persona" and the applicant profile under "applicant".
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

AS_AT = date(2026, 11, 23)
CANDIDATE_LABEL = "CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED"

_DOC = {
    "ApplicationForm": "INZ 1012 student visa application form", "OnlineApplication": "Online application record",
    "Passport": "Passport bio-data page", "Photograph": "Passport-size photographs",
    "PaymentRecord": "Application fee and levy receipt", "OfferOfPlace": "Offer of place letter",
    "BankStatement": "Bank statements, last 3 months", "MedicalCertificate": "INZ 1007 medical certificate",
    "ScholarshipLetter": "Scholarship award letter", "VisaConditionRecord": "Visa conditions record",
    "Timesheet": "Employer timesheet", "ProviderComplianceRecord": "Provider compliance record",
    "ConsentLetter": "Parental consent letter", "EmploymentAgreement": "Parent employment agreement",
    "FinancialUndertaking": "INZ 1014 financial undertaking", "GuaranteeLetter": "Maintenance guarantee letter",
    "ProviderRecord": "Provider approval record", "BirthCertificate": "Birth certificate",
}


class AppBuilder:
    def __init__(self, pid: str, profile: dict, channel="paper", idx=1):
        n = pid.replace("-", "")
        self.n, self.idx = n, idx
        self.app = {
            "application_id": f"POC-SV-APP-{n}", "product_id": "STUDENT-VISA", "synthetic": True,
            "assessment_as_at": AS_AT.isoformat(), "channel": channel,
            "applicant": {"applicant_id": f"APPLICANT-POC-{n}", **profile},
            "evidence_items": [], "facts": []}
        self._e = self._f = 0
        self._ev: dict[str, str] = {}

    def evidence(self, etype: str, verified=True) -> str:
        if etype in self._ev:
            return self._ev[etype]
        self._e += 1
        eid = f"EVID-{self.n}-{self._e:03d}"
        p = self.app["applicant"]
        received = (date(2026, 10, 12) + timedelta(days=(self.idx * 3 + self._e) % 28)).isoformat()
        ref = {"Passport": f"{p['nationality']} passport {p['passport_number']}",
               "BankStatement": f"Statements Aug to Oct 2026, account ending {4000 + (self.idx * 137) % 5999}",
               "OfferOfPlace": f"{p['provider']}, offer ref OP-{2026}-{self.idx * 41 + 1000}"}.get(etype)
        self.app["evidence_items"].append({
            "evidence_item_id": eid, "application_id": self.app["application_id"], "evidence_type": etype,
            "document_reference": ref or f"{_DOC.get(etype, etype)} ({self.n}-{self._e:02d})",
            "supplied_by_actor_id": "ACTOR-APPLICANT", "received_at": f"{received}T{9 + self._e % 8:02d}:{(self.idx * 7) % 60:02d}:00+13:00",
            "verification_status": "VERIFIED" if verified else "UNVERIFIED", "supports_fact_ids": []})
        self._ev[etype] = eid
        return eid

    def fact(self, attribute, value, etype=None, verified=True, data_type=None):
        self._f += 1
        fid = f"FACT-{self.n}-{self._f:03d}"
        dt = data_type or ("date" if attribute == "activity_date" else
                           {bool: "boolean", int: "integer", float: "number", str: "string"}[type(value)])
        ev = [self.evidence(etype, verified)] if etype else []
        vday = (date(2026, 11, 13) + timedelta(days=self.idx % 6)).isoformat()
        self.app["facts"].append({
            "fact_id": fid, "application_id": self.app["application_id"],
            "subject_id": self.app["applicant"]["applicant_id"], "attribute": attribute, "value": value,
            "data_type": dt, "evidence_item_ids": ev,
            "verification_status": "VERIFIED" if verified else "UNVERIFIED",
            "verified_at": f"{vday}T10:{(self.idx * 11) % 60:02d}:00+13:00" if verified else None,
            "verified_by": f"INZ-VO-{1000 + (self.idx * 13) % 90} (synthetic verifier)" if verified else None})
        for e in ev:
            for item in self.app["evidence_items"]:
                if item["evidence_item_id"] == e and fid not in item["supports_fact_ids"]:
                    item["supports_fact_ids"].append(fid)
        return fid


# --------------------------------------------------------------------------- fact templates
def core(ch="paper", *, age=22, marital="unmarried", stype="tertiary", months=12, funds=25000, identity=True,
         medical=True, ivl=True, photos=2, fee=True, levy=True, signed=True, english=True, material=False,
         route=None, declaration=True, photo_prompt=None):
    """The facts every application carries, for the paper or online channel."""
    F = []
    if ch == "paper":
        F += [("approved_form_used", True, "ApplicationForm"), ("persons_in_application", 1, "ApplicationForm"),
              ("completed_in_english", english, "ApplicationForm"), ("application_signed", signed, "ApplicationForm")]
        if identity:
            F.append(("identity_document_provided", True, "Passport"))
        F.append(("photographs_provided", photos, "Photograph"))
    else:
        F += [("online_form_used", True, "OnlineApplication"), ("completed_in_english", english, "OnlineApplication"),
              ("full_name_stated", True, "OnlineApplication"),
              ("date_and_place_of_birth_stated", True, "OnlineApplication"),
              ("passport_details_stated", True, "Passport"),
              ("declaration_acknowledged", declaration, "OnlineApplication"),
              ("change_notification_agreed", True, "OnlineApplication")]
        if photo_prompt is None:
            F.append(("form_requires_photograph", False, "OnlineApplication"))
        else:
            F += [("form_requires_photograph", True, "OnlineApplication"),
                  ("photograph_uploaded", photo_prompt, "Photograph")]
    F += [("prescribed_fee_paid", fee, "PaymentRecord"), ("immigration_levy_paid", levy, "PaymentRecord")]
    if ivl is not None:
        F.append(("ivl_paid", ivl, "PaymentRecord"))
    F += [("applicant_age", age, "Passport" if identity else None),
          ("applicant_marital_status", marital, "ApplicationForm"), ("student_type", stype, "OfferOfPlace"),
          ("study_duration_months", months, "OfferOfPlace"), ("available_funds_nzd", funds, "BankStatement"),
          ("material_change_occurred", material, "ApplicationForm")]
    if medical:
        F.append(("medical_certificate_received", True, "MedicalCertificate"))
    if route:
        F.append(("lodgement_route", route, "ApplicationForm"))
    return F


def _a(check, accepted, rule=None, **kw):
    d = {"check_id": check, "accepted": accepted if isinstance(accepted, list) else [accepted]}
    if rule:
        d["rule_id"] = rule
    d.update(kw)
    return d


APP, FIN, IVL, MIN, WORK, HEALTH = ("SV-CHECK-APP-001", "SV-CHECK-FIN-001", "SV-CHECK-IVL-001", "SV-CHECK-MIN-001",
                                    "SV-CHECK-WORK-001", "SV-CHECK-HEALTH-001")
LODG, FAM, PATH, SPON, PROV, WRK = ("SV-CHECK-LODG-001", "SV-CHECK-FAM-001", "SV-CHECK-PATH-001",
                                    "SV-CHECK-SPON-001", "SV-CHECK-PROV-001", "SV-CHECK-WRK-001")
OM = "SV-OM-"


def _rule_asserts(check, rules: dict):
    return [_a(check, status, rid) for rid, status in rules.items()]


# --------------------------------------------------------------------------- the personas
P: list[dict] = []


def persona(pid, name, sex, nationality, age, home, passport, provider, programme, title, scenario, backstory,
            facts, check, rule, status, asserts, *, channel="paper", expected="", test_type="PASS", deviation=None,
            flags=(), occupation="Student", extra=None):
    given, family = name.split(" ", 1)
    dob = AS_AT.replace(year=AS_AT.year - age) - timedelta(days=(len(P) * 29 + 40) % 330)
    P.append({"id": pid, "title": title, "scenario": scenario, "backstory": backstory, "channel": channel,
              "profile": {"given_name": given, "family_name": family, "sex": sex, "date_of_birth": dob.isoformat(),
                          "nationality": nationality, "country_of_residence": home.split(", ")[-1],
                          "city_of_residence": home.split(", ")[0], "passport_number": passport,
                          "passport_expiry": (AS_AT.replace(year=AS_AT.year + 3 + len(P) % 6)
                                              - timedelta(days=(len(P) * 17) % 300)).isoformat(),
                          "email": f"{given.lower()}.{family.lower().replace(' ', '')}@mail.example",
                          "occupation": occupation, "provider": provider, "programme": programme,
                          **(extra or {})},
              "facts": facts, "check_id": check, "rule_id": rule, "expected_status": status, "assert": asserts,
              "expected": expected or f"{check} returns {status}.", "test_type": test_type,
              "deviation": deviation, "interpretation_flag_ids": list(flags)})


HIT = "Harbourview Institute of Technology"
KVU = "Kōwhai Valley University"
SRP = "Southern Ranges Polytechnic"
PRC = "Pacific Rim Language College"
OTH = "Otago Heights Secondary College"
TMC = "Tāmaki Makaurau Business College"
WGC = "Waitemata Grammar College"

# ---- Group A: core application, paper lodgement -----------------------------------------------------
persona("SVP-001", "Priya Raghunathan", "F", "India", 24, "Pune, India", "Z4471093", KVU,
        "Master of Engineering (Civil), 2 years", "Complete paper application, no issues",
        "Postgraduate engineering student with a complete, verified paper application.",
        "Priya finished a BTech in civil engineering in Pune and works two years at a structural consultancy. Her "
        "father, a retired bank manager, holds her tuition funds in a joint savings account. She applied on the "
        "paper form through an education agent and attached every document.",
        core(age=24, months=24, funds=48000), APP, None, "SATISFIED",
        [_a(APP, "SATISFIED"), _a("SV-CHECK-DISC-001", "DISCRETION_REQUIRED"), {"deterministic_all_pass": True}],
        expected="Every deterministic check passes and the grant is still routed to the decision-maker.")
persona("SVP-002", "Nguyen Thi Lan", "F", "Vietnam", 19, "Da Nang, Vietnam", "C8120446", HIT,
        "Diploma in Hospitality Management, 18 months", "Passport pages not supplied",
        "The applicant did not include a passport copy, so identity cannot be established.",
        "Lan applied through a small agent who forgot to scan her passport. She holds a valid passport but it was "
        "never attached, so the identity check cannot run.",
        core(age=19, months=18, funds=33000, identity=False), APP, OM and "SV-ID-001", "UNKNOWN",
        [_a(APP, "UNKNOWN", "SV-ID-001"), {"missing_information_includes": "application.identity_document_provided"},
         {"no_result_for_rule": "SV-ID-001", "forbidden": ["NOT_SATISFIED"]}], test_type="MISSING",
        expected="Missing evidence returns UNKNOWN for SV-ID-001, never a failure.")
persona("SVP-003", "Carlos Eduardo Mendes", "M", "Brazil", 27, "Curitiba, Brazil", "FJ830215", SRP,
        "Graduate Diploma in Information Technology, 12 months", "Only one passport photograph supplied",
        "Paper application with a single photograph where two are required.",
        "Carlos worked as a software tester in Curitiba before saving for the conversion diploma. The photo studio "
        "returned one print and he did not notice that the form asks for two.",
        core(age=27, months=12, funds=27500, photos=1), APP, "SV-PHOTO-001", "NOT_SATISFIED",
        [_a(APP, "NOT_SATISFIED", "SV-PHOTO-001")], test_type="FAIL")
persona("SVP-004", "Mohammad Tariq Chowdhury", "M", "Bangladesh", 21, "Dhaka, Bangladesh", "A04418273", HIT,
        "Bachelor of Business, 3 years", "Application fee unpaid",
        "The application arrives with no proof that the prescribed fee was paid.",
        "Tariq's agent submitted the form before the family's bank transfer cleared, so there is no payment receipt "
        "for the prescribed application fee.",
        core(age=21, months=36, funds=62000, fee=False), APP, "SV-FEE-001", "NOT_SATISFIED",
        [_a(APP, "NOT_SATISFIED", "SV-FEE-001")], test_type="FAIL")
persona("SVP-005", "Maria Lourdes Santos", "F", "Philippines", 26, "Cebu City, Philippines", "P8812345A", PRC,
        "Certificate in English for Academic Purposes, 6 months", "Immigration levy unpaid",
        "Fee paid but the international visitor levy line on the receipt is unpaid.",
        "Maria is a nurse who wants to complete an academic English course before bridging to a New Zealand nursing "
        "pathway. She paid the application fee but not the immigration levy.",
        core(age=26, months=6, funds=14000, levy=False), APP, "SV-FEE-002", "NOT_SATISFIED",
        [_a(APP, "NOT_SATISFIED", "SV-FEE-002")], test_type="FAIL")
persona("SVP-006", "Hiroshi Tanabe", "M", "Japan", 31, "Nagoya, Japan", "TR9033817", KVU,
        "Master of Management, 2 years", "Application form not signed",
        "Paper application where the adult applicant forgot to sign.",
        "Hiroshi is a mid-career logistics planner. He completed the form in English but left the declaration "
        "unsigned before the agent couriered the pack.",
        core(age=31, months=24, funds=52000, signed=False) + [("minor_signature_rule_applies", False, "ApplicationForm")], APP, "SV-FORM-004", "NOT_SATISFIED",
        [_a(APP, "NOT_SATISFIED", "SV-FORM-004")], test_type="FAIL")

# ---- Group B: online lodgement ------------------------------------------------------------------------
persona("SVP-007", "Aisha Bint Rahman", "F", "Malaysia", 22, "Kuala Lumpur, Malaysia", "A41907625", KVU,
        "Bachelor of Science (Biotechnology), 3 years", "Complete online application",
        "Online application where every mandatory field and declaration is complete.",
        "Aisha applied through the INZ online portal after an offer from Kōwhai Valley. She completed every field, "
        "acknowledged the declaration and paid online.",
        core("online", age=22, months=36, funds=63000, route="online"), APP, None, "SATISFIED",
        [_a(APP, "SATISFIED"), _a(LODG, ["SATISFIED", "NOT_APPLICABLE"])], channel="online")
persona("SVP-008", "Dmitri Volkov", "M", "Kazakhstan", 25, "Almaty, Kazakhstan", "N10492837", HIT,
        "Postgraduate Diploma in Data Science, 1 year", "Online declaration not acknowledged",
        "Online application where the truth-and-correctness declaration was not acknowledged.",
        "Dmitri submitted through the portal but un-ticked the declaration box when correcting an address, and "
        "submitted without re-ticking it.",
        core("online", age=25, months=12, funds=26000, declaration=False, route="online"), APP, "SV-ONLINE-008",
        "NOT_SATISFIED", [_a(APP, "NOT_SATISFIED", "SV-ONLINE-008"), _a(LODG, "NOT_SATISFIED", OM + "E4.50.2-B-VI")],
        channel="online", test_type="FAIL")
persona("SVP-009", "Lindiwe Nkosi", "F", "South Africa", 23, "Durban, South Africa", "M00812745", SRP,
        "Bachelor of Design, 3 years", "Online photograph prompted but not uploaded",
        "The portal prompted for a photograph and none was uploaded.",
        "Lindiwe's portal session timed out during the photo upload step. The form was submitted without the "
        "compliant photograph even though the system asked for one.",
        core("online", age=23, months=36, funds=60000, photo_prompt=False, route="online"), APP, "SV-ONLINE-006",
        "NOT_SATISFIED", [_a(APP, "NOT_SATISFIED", "SV-ONLINE-006"), _a(LODG, "NOT_SATISFIED", OM + "E4.50.2-B-IV")],
        channel="online", test_type="FAIL")

# ---- Group C: E4 lodgement routes ------------------------------------------------------------------
persona("SVP-010", "Sofia Alejandra Reyes", "F", "Chile", 28, "Valparaíso, Chile", "F12294776", KVU,
        "Master of Applied Linguistics, 18 months", "Paper lodgement, E4.50.1 mandatory requirements met",
        "Adult paper application that satisfies every E4.50.1 requirement.",
        "Sofia teaches English in Valparaíso and lodged a paper application at the visa application centre in "
        "Santiago with signed form, two photographs and certified passport.",
        core(age=28, months=18, funds=40000, route="paper"), LODG, OM + "E4.50.1-P1-A", "SATISFIED",
        [_a(LODG, "SATISFIED", OM + "E4.50.1-P1-A"), _a(LODG, "SATISFIED", OM + "E4.50.1-P1-B"),
         _a(LODG, "SATISFIED", OM + "E4.50.1-P2")], test_type="BOUNDARY")
persona("SVP-011", "Arjun Mehta", "M", "India", 16, "Ahmedabad, India", "Z7715420", OTH,
        "NCEA Level 2, secondary school, 1 year", "Minor paper application signed by a parent",
        "Under-18 applicant whose parent signed the paper form.",
        "Arjun is a Year 11 student whose parents want him to complete a year at a New Zealand secondary school. "
        "His mother signed the application on his behalf.",
        core(age=16, stype="school", months=12, funds=20500, route="paper") +
        [("signed_by_parent_or_guardian", True, "ApplicationForm"),
         ("parental_consent_established", True, "ConsentLetter")], LODG, OM + "E4.50.1-P2", "SATISFIED",
        [_a(LODG, "SATISFIED", OM + "E4.50.1-P2"), _a(MIN, "NOT_APPLICABLE", "SV-MIN-001")], test_type="BOUNDARY")
persona("SVP-012", "Yuki Nakamura", "F", "Japan", 17, "Sapporo, Japan", "TK4408129", OTH,
        "Secondary school exchange year", "Minor paper application with no parental signature recorded",
        "Under-18 applicant for whom no parent or guardian signature fact is recorded.",
        "Yuki is a 17-year-old exchange student. Her host organisation supplied the form, but the section for the "
        "parent signature was never returned.",
        core(age=17, stype="school", months=12, funds=19000, route="paper") +
        [("parental_consent_established", True, "ConsentLetter")], LODG, OM + "E4.50.1-P2", "UNKNOWN",
        [_a(LODG, "UNKNOWN", OM + "E4.50.1-P2"), {"missing_information_includes": "application.signed_by_parent_or_guardian"}],
        test_type="MISSING")
persona("SVP-013", "Kwame Asante", "M", "Ghana", 29, "Accra, Ghana", "G0094417", HIT,
        "Graduate Diploma in Project Management, 1 year", "Lodged at the airport immigration control area",
        "Adult applicant who lodged at an immigration control area.",
        "Kwame was visiting a relative in Auckland on a visitor visa when his study plans firmed up. A border "
        "officer processed his student visa application on the approved form at the immigration control area.",
        core(age=29, months=12, funds=27000, route="ica"), LODG, OM + "E4.50.35-A-IV", "SATISFIED",
        [_a(LODG, "SATISFIED", OM + "E4.50.35-A-I"), _a(LODG, "SATISFIED", OM + "E4.50.35-A-III"),
         _a(LODG, "SATISFIED", OM + "E4.50.35-A-IV"), _a(LODG, "NOT_APPLICABLE", OM + "E4.50.35-B-I")])
persona("SVP-014", "Mei Ling Chen", "F", "China", 15, "Chengdu, China", "EJ2208841", WGC,
        "Year 10 secondary school, 1 year", "Minor accompanied by a parent at the immigration control area",
        "Under-18 applicant accompanied by a parent who signed the arrival card.",
        "Mei Ling arrived with her mother, who signed the arrival card at the immigration control area while "
        "the school confirmed her enrolment.",
        core(age=15, stype="school", months=12, funds=20000, route="ica") +
        [("accompanied_by_parent_or_guardian", True, "ApplicationForm"),
         ("signed_by_parent_or_guardian", True, "ApplicationForm"),
         ("parental_consent_established", True, "ConsentLetter")], LODG, OM + "E4.50.35-B-I", "SATISFIED",
        [_a(LODG, "SATISFIED", OM + "E4.50.35-B-I"), _a(LODG, "NOT_APPLICABLE", OM + "E4.50.35-B-II"),
         _a(LODG, "NOT_APPLICABLE", OM + "E4.50.35-A-IV")], test_type="BOUNDARY")
persona("SVP-015", "Tomás Herrera", "M", "Colombia", 17, "Medellín, Colombia", "AT3305912", WGC,
        "Year 12 secondary school, 1 year", "Unaccompanied minor at the immigration control area",
        "Under-18 applicant arriving without a parent, so the arrival card need not be signed.",
        "Tomás travelled alone on a school-arranged flight. His guardian had signed a consent letter beforehand, "
        "and the school's homestay coordinator met him at the airport.",
        core(age=17, stype="school", months=12, funds=19500, route="ica") +
        [("accompanied_by_parent_or_guardian", False, "ApplicationForm"),
         ("parental_consent_established", True, "ConsentLetter")], LODG, OM + "E4.50.35-B-II", "SATISFIED",
        [_a(LODG, "SATISFIED", OM + "E4.50.35-B-II"), _a(LODG, "NOT_APPLICABLE", OM + "E4.50.35-B-I")],
        test_type="BOUNDARY")

# ---- Group D: maintenance funds --------------------------------------------------------------------
persona("SVP-016", "Ananya Krishnan", "F", "India", 23, "Chennai, India", "Z3380177", KVU,
        "Bachelor of Commerce, 3 years", "Funds exactly at the one-year threshold",
        "Funds equal the NZ$20,000 annual figure for a programme of 36 weeks or longer.",
        "Ananya's parents transferred exactly the NZ$20,000 required for the first year and will remit the "
        "next instalment later.",
        core(age=23, months=12, funds=20000), FIN, OM + "U3.20.30-A-I", "SATISFIED",
        [_a(FIN, "SATISFIED", OM + "U3.20.30-A-I")], test_type="BOUNDARY")
persona("SVP-017", "Ravi Shankar Iyer", "M", "India", 24, "Hyderabad, India", "Z5551204", KVU,
        "Master of Information Systems, 18 months", "Funds one dollar short of the annual figure",
        "Funds fall NZ$1 short of the annual requirement.",
        "Ravi's savings statement shows NZ$19,999 after a small currency conversion loss.",
        core(age=24, months=12, funds=19999), FIN, OM + "U3.20.30-A-I", "NOT_SATISFIED",
        [_a(FIN, "NOT_SATISFIED", OM + "U3.20.30-A-I")], test_type="BOUNDARY")
persona("SVP-018", "Thanh Huong Pham", "F", "Vietnam", 20, "Ho Chi Minh City, Vietnam", "C9917520", PRC,
        "General English, 24 weeks", "Short programme under 36 weeks",
        "Programme under 36 weeks, tested against the monthly rate.",
        "Huong is taking a six-month English course before a hospitality diploma. Her aunt in Saigon funded "
        "NZ$12,000.",
        core(age=20, months=6, funds=12000), FIN, OM + "U3.20.25-A-I", "SATISFIED",
        [_a(FIN, "SATISFIED", OM + "U3.20.25-A-I")])
persona("SVP-019", "Oliver Schmidt", "M", "Germany", 15, "Hamburg, Germany", "C01X43M7Y", OTH,
        "Year 11 secondary school, 1 year", "Compulsory-education student, annual figure for school learners",
        "A 15-year-old school student tested against the NZ$17,000 compulsory-education figure.",
        "Oliver's family lives in Hamburg. He will attend a state secondary school as an international student, "
        "and his parents hold NZ$18,500 for living costs.",
        core(age=15, stype="school", months=12, funds=18500) + [("parental_consent_established", True, "ConsentLetter")],
        FIN, OM + "U3.20.30-A-II", "SATISFIED", [_a(FIN, "SATISFIED", OM + "U3.20.30-A-II"),
                                                  _a(MIN, "NOT_APPLICABLE", "SV-MIN-001")])
persona("SVP-020", "Elena Kowalczyk", "F", "Poland", 29, "Kraków, Poland", "EA7702938", KVU,
        "Master of Public Health, 12 months", "Pregnant applicant due to give birth in New Zealand",
        "Pregnant applicant who must show the extra NZ$9,000 for maternity health services.",
        "Elena is 28 weeks pregnant and expects to give birth in New Zealand while completing a one-year degree. "
        "She and her husband have saved NZ$29,000.",
        core(age=29, months=12, funds=29000) + [("pregnant_due_to_give_birth_in_nz", True, "MedicalCertificate"),
                                                 ("eligible_for_publicly_funded_maternity_services", False, "MedicalCertificate")],
        FIN, OM + "U3.20.35-P1-A", "SATISFIED",
        [_a(FIN, "SATISFIED", OM + "U3.20.35-P1-A"), _a(FIN, "SATISFIED", OM + "U3.20.30-A-I")], test_type="BOUNDARY")
persona("SVP-021", "Sione Fifita", "M", "Tonga", 20, "Nukuʻalofa, Tonga", "T0054172", SRP,
        "Diploma in Automotive Engineering, 2 years", "Tongan citizen supported by a relative in New Zealand",
        "Citizen of Tonga relying on a written maintenance guarantee from a New Zealand resident relative.",
        "Sione's uncle, a New Zealand resident in Māngere, signed a written maintenance guarantee. Sione holds "
        "only NZ$6,000 himself.",
        core(age=20, months=24, funds=6000) + [("citizenship", "Tonga", "Passport"),
                                                ("maintenance_guarantee_from_nz_relative", True, "GuaranteeLetter")],
        FIN, OM + "U3.20.30-A-III", "SATISFIED", [_a(FIN, "SATISFIED", OM + "U3.20.30-A-III")])
persona("SVP-022", "Fatima Zahra El Idrissi", "F", "Morocco", 25, "Casablanca, Morocco", "KB6620931", HIT,
        "Postgraduate Diploma in Marketing, 1 year", "Bank statement not yet verified",
        "Funds are declared but the bank statement has not been verified.",
        "Fatima uploaded a bank statement showing NZ$26,000. The verification step has not run, so the figure is "
        "only an observed fact.",
        core(age=25, months=12, funds=26000)[:-0 or None], FIN, OM + "U3.20.30-A-I", "UNKNOWN",
        [_a(FIN, "UNKNOWN", OM + "U3.20.30-A-I")], test_type="MISSING",
        expected="An unverified bank statement gives UNKNOWN, not failure.")
# the funds fact of SVP-022 is replaced by an unverified one below
P[-1]["facts"] = [f if f[0] != "available_funds_nzd" else ("available_funds_nzd", 26000, "BankStatement", False)
                  for f in P[-1]["facts"]]

# ---- Group E: minors ----------------------------------------------------------------------------
persona("SVP-023", "Isabella Romano", "F", "Italy", 16, "Bologna, Italy", "YA4410029", OTH,
        "Year 12 secondary school, 1 year", "Minor with no parental consent evidence",
        "Unmarried 16-year-old; parental or guardian consent is missing.",
        "Isabella's parents are separated and her mother submitted the application without the father's written "
        "consent, so the consent evidence is missing.",
        core(age=16, stype="school", months=12, funds=19000), MIN, "SV-MIN-001", "DISCRETION_REQUIRED",
        [_a(MIN, ["HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED"], "SV-MIN-001"), {"human_review_includes": "SV-MIN-001"}],
        test_type="DISCRETION")
persona("SVP-024", "Lucas Martin", "M", "France", 16, "Lyon, France", "20AB44109", OTH,
        "Year 12 secondary school, 1 year", "Minor with verified parental consent",
        "Unmarried 16-year-old whose parental consent is verified.",
        "Both Lucas's parents signed a notarised consent letter, which the verifier matched to their passports.",
        core(age=16, stype="school", months=12, funds=19000) + [("parental_consent_established", True, "ConsentLetter")],
        MIN, "SV-MIN-001", "NOT_APPLICABLE", [_a(MIN, "NOT_APPLICABLE", "SV-MIN-001")])
persona("SVP-025", "Aiko Sato", "F", "Japan", 8, "Osaka, Japan", "TR5502216", WGC,
        "Year 4 primary school, 1 year", "Child under 10 living with a parent",
        "Child under 10 who lives with a parent in New Zealand.",
        "Aiko will live with her mother, who is studying a master's degree at the same time. Her father stays in "
        "Osaka.",
        core(age=8, stype="school", months=12, funds=19000) +
        [("lives_with_parent_or_legal_guardian", True, "ConsentLetter"),
         ("parental_consent_established", True, "ConsentLetter")], "SV-CHECK-U10-001", "SV-U10-001", "SATISFIED",
        [_a("SV-CHECK-U10-001", "SATISFIED", "SV-U10-001")])
persona("SVP-026", "Diego Fernandez", "M", "Argentina", 9, "Córdoba, Argentina", "AAB320118", WGC,
        "Year 5 primary school, 1 year", "Child under 10 not living with a parent",
        "Child under 10 with no parent or legal guardian in New Zealand.",
        "Diego's family arranged a homestay with a school family. No parent or legal guardian is in the country.",
        core(age=9, stype="school", months=12, funds=19000) +
        [("lives_with_parent_or_legal_guardian", False, "ConsentLetter"),
         ("parental_consent_established", True, "ConsentLetter")], "SV-CHECK-U10-001", "SV-U10-001", "NOT_SATISFIED",
        [_a("SV-CHECK-U10-001", "NOT_SATISFIED", "SV-U10-001")], test_type="FAIL")

# ---- Group F: IVL -------------------------------------------------------------------------------
persona("SVP-027", "Tevita Tupou", "M", "Fiji", 26, "Suva, Fiji", "FJ1189204", KVU,
        "Master of Public Policy, 2 years", "MFAT scholarship holder exempt from the IVL",
        "Scholar supported by an MFAT-administered scholarship.",
        "Tevita works in Fiji's Ministry of Finance and holds a New Zealand Scholarship administered by MFAT. His "
        "award letter lists tuition and living costs, and the IVL does not apply to him.",
        [f for f in core(age=26, months=24, funds=0, ivl=None) if f[0] != "available_funds_nzd"] +
        [("scholarship_administrator", "MFAT", "ScholarshipLetter"), ("available_funds_nzd", 45000, "ScholarshipLetter")],
        IVL, "SV-FEE-003", "NOT_APPLICABLE", [_a(IVL, "NOT_APPLICABLE", "SV-FEE-003", exception_rule_id="SV-IVL-001")],
        test_type="EXCEPTION")
persona("SVP-028", "Grace Wanjiru", "F", "Kenya", 24, "Nairobi, Kenya", "AK0371855", HIT,
        "Master of Professional Accounting, 2 years", "IVL unpaid for a self-funded student",
        "Self-funded student with no scholarship who has not paid the IVL.",
        "Grace is self-funded and her receipt shows the fee and levy but not the IVL line.",
        core(age=24, months=24, funds=44000, ivl=False), IVL, "SV-FEE-003", "NOT_SATISFIED",
        [_a(IVL, "NOT_SATISFIED", "SV-FEE-003")], test_type="FAIL")

# ---- Group G: work permission ------------------------------------------------------------------
def _work(h, permitted=True, limit=25):
    return [("work_permitted_by_visa_conditions", permitted, "VisaConditionRecord"),
            ("visa_condition_max_work_hours_per_week", limit, "VisaConditionRecord"),
            ("recorded_work_hours_per_week", h, "Timesheet"), ("activity_date", "2026-11-03", "Timesheet")]


persona("SVP-029", "Jun Seo-yeon", "F", "South Korea", 22, "Daejeon, South Korea", "M93018842", HIT,
        "Bachelor of Tourism, 3 years", "Part-time work within the 25-hour limit",
        "Student working 20 hours a week against a 25-hour condition.",
        "Seo-yeon works part-time at a café near campus. Her timesheet for the first week of November shows "
        "20 hours.", core(age=22, months=36, funds=62000) + _work(20), WORK, "SV-WORK-001", "SATISFIED",
        [_a(WORK, "SATISFIED", "SV-WORK-001")], test_type="BOUNDARY")
persona("SVP-030", "Rohan Desai", "M", "India", 25, "Mumbai, India", "Z8830047", HIT,
        "Master of Business Analytics, 18 months", "Work above the 25-hour limit",
        "Student recorded at 28 hours in a week.",
        "Rohan picked up extra delivery shifts before an exam break and logged 28 hours in the week of 3 November.",
        core(age=25, months=18, funds=40000) + _work(28), WORK, "SV-WORK-001", "NOT_SATISFIED",
        [_a(WORK, "NOT_SATISFIED", "SV-WORK-001")], test_type="FAIL")
persona("SVP-031", "Wei Zhang", "M", "China", 20, "Shenyang, China", "EK9981023", SRP,
        "Certificate in Trade Skills, 1 year", "Work recorded where conditions do not permit work",
        "Student working although the visa conditions do not allow work.",
        "Wei's short certificate course does not qualify for work rights, but he accepted weekend kitchen shifts.",
        core(age=20, months=12, funds=26000) + _work(12, permitted=False), WORK, "SV-WORK-001", "NOT_SATISFIED",
        [_a(WORK, "NOT_SATISFIED", "SV-WORK-001")], test_type="FAIL")

# ---- Group H: health, disclosure, provider duties ----------------------------------------------
persona("SVP-032", "Ingrid Larsen", "F", "Norway", 23, "Bergen, Norway", "36A400712", KVU,
        "Bachelor of Marine Science, 3 years", "Medical certificate received, referred to a person",
        "Medical certificate received; health is never scored and is referred to a human.",
        "Ingrid completed a medical certificate with a Bergen clinic. The certificate is attached and awaits an "
        "immigration health assessment.", core(age=23, months=36, funds=65000), HEALTH, OM + "U3.1-B-III",
        "HUMAN_REVIEW_REQUIRED", [_a(HEALTH, "HUMAN_REVIEW_REQUIRED"), {"check_id": HEALTH, "evidence_used_type": "MedicalCertificate"}],
        test_type="HUMAN_REVIEW")
persona("SVP-033", "Pedro Alves", "M", "Portugal", 27, "Porto, Portugal", "NA1021847", HIT,
        "Postgraduate Certificate in Education, 1 year", "No medical evidence received",
        "No medical certificate or X-ray has been received.",
        "Pedro's clinic appointment was delayed, so the application was lodged without a medical certificate.",
        core(age=27, months=12, funds=26000, medical=False), HEALTH, OM + "U3.1-B-III", "UNKNOWN",
        [_a(HEALTH, "UNKNOWN"), {"missing_information_includes": "evidence.MedicalCertificate"}], test_type="MISSING")
persona("SVP-034", "Samira Haddad", "F", "Lebanon", 26, "Beirut, Lebanon", "RL1234098", KVU,
        "Master of Architecture, 2 years", "Material change not disclosed",
        "Applicant's circumstances changed but the change was not disclosed.",
        "Samira's employer relocated her family after the form was signed. The new address and sponsor were never "
        "notified to the case officer.",
        core(age=26, months=24, funds=47000, material=True) + [("material_change_disclosed", False, "ApplicationForm")],
        "SV-CHECK-APP-005", "SV-APP-005", "NOT_SATISFIED", [_a("SV-CHECK-APP-005", "NOT_SATISFIED", "SV-APP-005")],
        test_type="FAIL")
persona("SVP-035", "Greg Thompson", "M", "United States", 30, "Portland, United States", "56102739", PRC,
        "Postgraduate Diploma in Tourism, 1 year", "Provider knew of a breach and did not report it",
        "Verified provider compliance record: known visa-condition breach, not reported to INZ.",
        "A compliance audit confirmed that the provider knew Greg was working beyond his visa conditions and "
        "did not report it. This is the provider's obligation, not Greg's.",
        core(age=30, months=12, funds=28000) + [("provider_known_visa_condition_breach", True, "ProviderComplianceRecord"),
                                                 ("provider_reported_breach_to_inz", False, "ProviderComplianceRecord")],
        "SV-CHECK-EDU-009", "SV-EDU-009", "NOT_SATISFIED", [_a("SV-CHECK-EDU-009", "NOT_SATISFIED", "SV-EDU-009")],
        test_type="FAIL", flags=("IF-14",))
persona("SVP-036", "Anika Bose", "F", "India", 22, "Kolkata, India", "Z2204718", PRC,
        "Diploma in Business, 1 year", "Provider suspects a breach",
        "Provider suspects a breach that is not yet verified, so it goes to human review.",
        "Anika's tutor suspected she was working too many hours but had no evidence. The provider recorded the "
        "suspicion.", core(age=22, months=12, funds=26000) +
        [("provider_suspected_visa_condition_breach", True, "ProviderComplianceRecord", False)],
        "SV-CHECK-EDU-009", "SV-EDU-009", "HUMAN_REVIEW_REQUIRED", [_a("SV-CHECK-EDU-009", "HUMAN_REVIEW_REQUIRED", "SV-EDU-009")],
        test_type="HUMAN_REVIEW", flags=("IF-14",))

# ---- Group I: family ------------------------------------------------------------------------------
def _fam(basis, vtype=None, income=None):
    f = [("family_basis", basis, "EmploymentAgreement")]
    if vtype:
        f += [("supporting_work_visa_type", vtype, "EmploymentAgreement"),
              ("supporting_parent_gross_income_nzd", income, "EmploymentAgreement")]
    return f


persona("SVP-037", "Rahul Verma", "M", "India", 14, "Bengaluru, India", "Z6609312", OTH,
        "Year 10 secondary school, 1 year", "Dependent child of an Accredited Employer work visa holder",
        "Child of a parent on an Accredited Employer work visa earning above the income threshold.",
        "Rahul's father is a senior software engineer on an Accredited Employer work visa with a gross salary of "
        "NZ$62,000 a year.",
        core(age=14, stype="school", months=12, funds=19000) + [("parental_consent_established", True, "ConsentLetter")] +
        _fam("dependent_child_of_work_visa_holder", "Accredited Employer", 62000), FAM, OM + "U8.20.6-A", "SATISFIED",
        [_a(FAM, "SATISFIED", OM + "U8.20.6-A"), _a(FAM, "NOT_APPLICABLE", OM + "U8.20.10-B-I")])
persona("SVP-038", "Angelica Cruz", "F", "Philippines", 13, "Quezon City, Philippines", "P5547823B", OTH,
        "Year 9 secondary school, 1 year", "Parent income below the threshold",
        "Dependent child whose parent earns NZ$54,000, under the NZ$58,240 threshold.",
        "Angelica's mother is a hospitality supervisor on an Accredited Employer work visa at NZ$54,000 a year.",
        core(age=13, stype="school", months=12, funds=18000) + [("parental_consent_established", True, "ConsentLetter")] +
        _fam("dependent_child_of_work_visa_holder", "Accredited Employer", 54000), FAM, OM + "U8.20.6-A", "NOT_SATISFIED",
        [_a(FAM, "NOT_SATISFIED", OM + "U8.20.6-A")], test_type="FAIL")
persona("SVP-039", "Daniel Okafor", "M", "Nigeria", 12, "Lagos, Nigeria", "A09871233", OTH,
        "Year 8 secondary school, 1 year", "Child of a Religious Worker visa holder",
        "Child whose parents hold Religious Worker visas and earn exactly the NZ$58,240 threshold.",
        "Daniel's parents serve a church community in Manurewa. Their combined stipend is NZ$58,240.",
        core(age=12, stype="school", months=12, funds=18000) + [("parental_consent_established", True, "ConsentLetter")] +
        _fam("dependent_child_of_work_visa_holder", "religious worker", 58240), FAM, OM + "U8.20.10-B-I", "SATISFIED",
        [_a(FAM, "SATISFIED", OM + "U8.20.10-B-I"), _a(FAM, "NOT_APPLICABLE", OM + "U8.20.6-A")], test_type="BOUNDARY")
persona("SVP-040", "Chloe Anderson", "F", "United Kingdom", 11, "Leeds, United Kingdom", "533412087", WGC,
        "Year 7 intermediate school, 1 year", "Child of a New Zealand citizen, in New Zealand",
        "Child of a New Zealand citizen granted a 12-month visa while in New Zealand.",
        "Chloe moved with her mother, a New Zealand citizen returning from London. The visa is granted for 12 months "
        "from grant while she settles.",
        core(age=11, stype="school", months=12, funds=18000) + [("parental_consent_established", True, "ConsentLetter"),
                                                                 ("applicant_in_new_zealand", True, "ApplicationForm"),
                                                                 ("visa_duration_months", 12, "ApplicationForm")] +
        _fam("child_of_nz_citizen"), FAM, OM + "U8.30-B-II", "SATISFIED",
        [_a(FAM, "SATISFIED", OM + "U8.30-B-II"), _a(FAM, "NOT_APPLICABLE", OM + "U8.30-B-I")])

# ---- Group J: pathway ------------------------------------------------------------------------------
def _path(n, months, brk, eng, rate):
    return [("pathway_programme_count", n, "ProviderRecord"), ("pathway_visa_duration_months", months, "ProviderRecord"),
            ("max_break_between_programmes_weeks", brk, "ProviderRecord"), ("english_language_study_weeks", eng, "ProviderRecord"),
            ("provider_visa_approval_rate_percent", rate, "ProviderRecord")]


persona("SVP-041", "Hana Kim", "F", "South Korea", 18, "Busan, South Korea", "M71034406", SRP,
        "Pathway: English, Diploma, Bachelor (54 months)", "Eligible three-programme pathway",
        "Pathway with three programmes, a 12-week break, 24 weeks of English and a 93% provider approval rate.",
        "Hana is a school leaver on a pathway from English study through a diploma to a business degree, all with "
        "pilot-participant providers.", core(age=18, months=54, funds=91500) + _path(3, 54, 12, 24, 93), PATH,
        OM + "U14.20.5-C", "SATISFIED", [_a(PATH, "SATISFIED", OM + "U14-A"), _a(PATH, "SATISFIED", OM + "U14.20.5-C"),
                                         _a(PATH, "SATISFIED", OM + "U14.20.5-D"), _a(PATH, "SATISFIED", OM + "U14.20.1-P1-D")])
persona("SVP-042", "Bao Tran", "M", "Vietnam", 19, "Hanoi, Vietnam", "C7745120", PRC,
        "Pathway: four stages (60 months)", "Pathway that breaks the limits",
        "Four programmes, a 20-week break, 34 weeks of English and a provider at 88% approval.",
        "Bao's agent stacked four programmes with long gaps and an extended English stage.",
        core(age=19, months=60, funds=101000) + _path(4, 60, 20, 34, 88), PATH, OM + "U14.20.5-C", "NOT_SATISFIED",
        [_a(PATH, "NOT_SATISFIED", OM + "U14-A"), _a(PATH, "NOT_SATISFIED", OM + "U14.20.5-C"),
         _a(PATH, "NOT_SATISFIED", OM + "U14.20.5-D"), _a(PATH, "NOT_SATISFIED", OM + "U14.20.1-P1-D")], test_type="FAIL")

# ---- Group K: sponsorship ------------------------------------------------------------------------
persona("SVP-043", "Chanel Dubois", "F", "France", 21, "Toulouse, France", "19CD70221", HIT,
        "Bachelor of Arts, 3 years", "Financial undertaking from an uncle",
        "Third party is a person who is a relative.",
        "Chanel's uncle, a New Zealand resident in Christchurch, completed an INZ 1014 financial undertaking.",
        core(age=21, months=36, funds=15000) + [("third_party_type", "person", "FinancialUndertaking"),
                                                 ("third_party_relationship", "relative", "FinancialUndertaking"),
                                                 ("sponsor_is_natural_person", True, "FinancialUndertaking")],
        SPON, OM + "U3.20.10-B", "SATISFIED", [_a(SPON, "SATISFIED", OM + "U3.20.10-B"),
                                               _a(SPON, "NOT_APPLICABLE", OM + "U3.20.10-C")])
persona("SVP-044", "Pierre Leclerc", "M", "Canada", 24, "Montréal, Canada", "HK881204", TMC,
        "Master of Business Administration, 18 months", "Corporate sponsor without an authorised contact",
        "Organisation sponsor that has not nominated an authorised contact.",
        "Pierre's employer in Montréal pays his tuition, but the financial undertaking form did not name an "
        "authorised individual.", core(age=24, months=18, funds=22000) +
        [("third_party_type", "organisation", "FinancialUndertaking"), ("sponsor_is_natural_person", False, "FinancialUndertaking"),
         ("authorised_contact_nominated", False, "FinancialUndertaking")], SPON, OM + "E6.5-E", "NOT_SATISFIED",
        [_a(SPON, "NOT_SATISFIED", OM + "E6.5-E"), _a(SPON, "NOT_SATISFIED", OM + "U3.20.10-C")], test_type="FAIL")

# ---- Group L: providers -------------------------------------------------------------------------
persona("SVP-045", "Zainab Yusuf", "F", "Nigeria", 25, "Abuja, Nigeria", "A12455680", SRP,
        "Diploma in Nursing Studies, 2 years", "Compliant provider",
        "Provider is a Code signatory, certified the programme and holds NZQA EER Category 2.",
        "Zainab's provider is in good standing, which the provider record confirms.",
        core(age=25, months=24, funds=44000) + [("provider_eer_category", 2, "ProviderRecord"),
                                                 ("provider_signatory_to_code", True, "ProviderRecord"),
                                                 ("provider_certified_programme_compliance", True, "ProviderRecord")],
        PROV, OM + "U5.1-D", "SATISFIED", [_a(PROV, "SATISFIED", OM + "U5.1-A"), _a(PROV, "SATISFIED", OM + "U5.1-B"),
                                          _a(PROV, "SATISFIED", OM + "U5.1-D")])
persona("SVP-046", "Mateo Gutierrez", "M", "Mexico", 28, "Guadalajara, Mexico", "G47710935", TMC,
        "Diploma in Hospitality, 1 year", "Provider holds NZQA Category Four",
        "Provider with Category Four status, so a student visa cannot be granted for study there.",
        "Mateo accepted an offer from a provider that NZQA rated Category Four in its external evaluation review.",
        core(age=28, months=12, funds=26500) + [("provider_eer_category", 4, "ProviderRecord"),
                                                 ("provider_signatory_to_code", True, "ProviderRecord"),
                                                 ("provider_certified_programme_compliance", True, "ProviderRecord")],
        PROV, OM + "U5.1-D", "NOT_SATISFIED", [_a(PROV, "NOT_SATISFIED", OM + "U5.1-D")], test_type="FAIL")

# ---- Group M: work rights for 16 and 17 year olds -----------------------------------------------
def _wr(prov, parent):
    return [("work_conditions_requested", True, "ApplicationForm"), ("enrolled_at_tertiary_or_pte", True, "OfferOfPlace"),
            ("provider_written_permission", prov, "ProviderRecord"), ("parental_written_consent", parent, "ConsentLetter")]


persona("SVP-047", "Noah Williams", "M", "Australia", 17, "Brisbane, Australia", "PA8832106", HIT,
        "Certificate in Carpentry, 1 year", "17-year-old with provider and parent permission to work",
        "17-year-old at a polytechnic with written permission from the provider and a parent.",
        "Noah's polytechnic and his father both gave written permission for part-time work.",
        core(age=17, months=12, funds=25000) + [("parental_consent_established", True, "ConsentLetter")] + _wr(True, True),
        WRK, OM + "U13.15-B", "SATISFIED", [_a(WRK, "SATISFIED", OM + "U13.15-B")])
persona("SVP-048", "Layla Haddad", "F", "Jordan", 16, "Amman, Jordan", "J2204119", SRP,
        "Certificate in Early Childhood, 1 year", "16-year-old without parental consent to work",
        "16-year-old requesting work conditions without written parental consent.",
        "Layla's provider gave permission but her parents did not respond to the consent request.",
        core(age=16, months=12, funds=25000) + [("parental_consent_established", True, "ConsentLetter")] + _wr(True, False),
        WRK, OM + "U13.15-B", "NOT_SATISFIED", [_a(WRK, "NOT_SATISFIED", OM + "U13.15-B")], test_type="FAIL")

# ---- Group N: residual domain review and explanation ---------------------------------------------
persona("SVP-049", "Cristina Rossi", "F", "Italy", 34, "Milan, Italy", "YB3300152", KVU,
        "Master of Education, 2 years, with two children", "Full multi-domain review of a complex family case",
        "Complex case reviewed across every domain: the residual checks present all remaining rules.",
        "Cristina is relocating with her partner and two children and has requested a review across every rule "
        "domain.", core(age=34, months=24, funds=60000) +
        [("review_all_domains", True, "ApplicationForm"), ("dependent_child_included", True, "BirthCertificate"),
         ("child_age", 19, "BirthCertificate"), ("part_time_study_months", 9, "OfferOfPlace")],
        "SV-CHECK-FAMILY-RES", "SV-FAM-002", "SATISFIED",
        [_a("SV-CHECK-FAMILY-RES", "SATISFIED", "SV-FAM-002"), _a("SV-CHECK-CHARACTER-RES", ["HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED"]),
         _a("SV-CHECK-LODGEMENT-RES", ["HUMAN_REVIEW_REQUIRED", "DISCRETION_REQUIRED"])], test_type="HUMAN_REVIEW")
persona("SVP-050", "Omar Khalid", "M", "Pakistan", 26, "Lahore, Pakistan", "AB7712035", KVU,
        "Master of Computer Science, 2 years", "Explanation chain for the identity rule",
        "Complete application used to trace the explanation chain from an evaluation to its authority.",
        "Omar's application is complete and every rule can be traced from the evaluation back to the provision "
        "and the authority source.", core(age=26, months=24, funds=47000), APP, "SV-ID-001", "EXPLANATION_CHAIN_COMPLETE",
        [{"explanation_chain_for_rule": "SV-ID-001",
          "chain": ["RuleEvaluation", "DecisionCheck", "Rule", "Provision", "AuthoritySource"]}],
        test_type="EXPLANATION")

# a recorded deviation for the funds persona (IF-30): instruction figures replace the spec's candidate figures
for _p in P:
    if _p["id"] == "SVP-016":
        _p["deviation"] = ("Instruction figures from Operational Manual U3.20 replace the spec's candidate funds "
                           "figures (IF-30); the U3.20 source text is retrieved, while the wider Operational Manual "
                           "remains a SOURCE_COVERAGE_GAP in scope (GAP-01).")
        _p["interpretation_flag_ids"] = ["IF-30"]
        _p["assert"][0]["provenance_status"] = "VERIFIED_INSTRUCTION_SOURCE"


# --------------------------------------------------------------------------- public API
def synthetic_applications() -> dict[str, dict]:
    apps = {}
    for i, p in enumerate(P, 1):
        b = AppBuilder(p["id"], p["profile"], p["channel"], i)
        for f in p["facts"]:
            attr, value, etype, *rest = f
            b.fact(attr, value, etype, verified=rest[0] if rest else True)
        b.app["scenario"] = p["scenario"]
        b.app["persona"] = {"persona_id": p["id"], "title": p["title"], "backstory": p["backstory"],
                            "synthetic_notice": "Fictional person created for the POC. Any resemblance to a real "
                                                "person is coincidental."}
        apps[p["id"]] = b.app
    return copy.deepcopy(apps)


def expected_results() -> dict[str, dict]:
    out = {}
    for p in P:
        e = {"check_id": p["check_id"], "rule_id": p["rule_id"], "expected_status": p["expected_status"],
             "assert": p["assert"], "test_type": p["test_type"], "scenario": p["scenario"], "expected": p["expected"]}
        if p["deviation"]:
            e["deviation"] = p["deviation"]
        if p["interpretation_flag_ids"]:
            e["interpretation_flag_ids"] = p["interpretation_flag_ids"]
        out[p["id"]] = e
    return copy.deepcopy(out)
