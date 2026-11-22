import json

import pytest

from svkg.docspec import ROOT


@pytest.fixture(scope="session")
def live():
    """Live `studentvisa` database, freshly loaded. Tests needing it fail (not skip) if it is down."""
    from svkg import graph
    from svkg.config import driver, settings
    info = graph.load()
    s = settings()
    d = driver(s)
    yield {"driver": d, "db": s.database, "load": info}
    d.close()


@pytest.fixture(scope="session")
def apps():
    return {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "testing/applications").glob("TC-*.json"))}


@pytest.fixture(scope="session")
def expected():
    return json.loads((ROOT / "testing/expected-results.json").read_text())["expected_results"]


def base_app(**facts):
    """Minimal synthetic application with the given facts (all VERIFIED unless value is a ('UNVERIFIED', v) tuple)."""
    app = {"application_id": "POC-SV-APP-UNIT", "synthetic": True, "channel": "paper",
           "applicant": {"applicant_id": "APPLICANT-UNIT"}, "evidence_items": [], "facts": []}
    for i, (k, v) in enumerate(facts.items(), 1):
        status = "VERIFIED"
        if isinstance(v, tuple) and v and v[0] == "UNVERIFIED":
            status, v = "UNVERIFIED", v[1]
        app["facts"].append({"fact_id": f"FACT-UNIT-{i:03d}", "attribute": k, "value": v,
                             "verification_status": status, "evidence_item_ids": []})
    return app


COMPLETE = dict(approved_form_used=True, persons_in_application=1, completed_in_english=True,
                application_signed=True, identity_document_provided=True, photographs_provided=2,
                prescribed_fee_paid=True, immigration_levy_paid=True, ivl_paid=True, applicant_age=22,
                applicant_marital_status="unmarried", student_type="tertiary", study_duration_months=12,
                available_funds_nzd=25000, material_change_occurred=False)
