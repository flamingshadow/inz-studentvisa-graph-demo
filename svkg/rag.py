"""One definition of how the eight closed outcomes map to a red / amber / green status.

Used by the local dashboard and by the Claude Desktop tools, so a result looks the same everywhere.
Colour is never the only signal: every outcome also has an icon name and a plain-language label.

Green  = a requirement is met.
Amber  = nothing has failed, but a person has to act: evidence is missing, an officer must review,
         the decision-maker must decide, or the POC corpus does not cover the point.
Red    = a requirement is not met, or the engine stopped because the rule is ambiguous.
Grey   = the rule does not apply to this case (not a status to act on).
"""
from __future__ import annotations

RAG: dict[str, dict] = {
    "SATISFIED":             {"rag": "green", "icon": "check",    "label": "Met",                     "rank": 4},
    "NOT_APPLICABLE":        {"rag": "grey",  "icon": "minus",    "label": "Not applicable",          "rank": 5},
    "NOT_SATISFIED":         {"rag": "red",   "icon": "x",        "label": "Not met",                 "rank": 0},
    "AMBIGUOUS":             {"rag": "red",   "icon": "alert",    "label": "Ambiguous, stopped",      "rank": 0},
    "UNKNOWN":               {"rag": "amber", "icon": "help",     "label": "Evidence needed",         "rank": 1},
    "HUMAN_REVIEW_REQUIRED": {"rag": "amber", "icon": "user",     "label": "Officer review",          "rank": 2},
    "DISCRETION_REQUIRED":   {"rag": "amber", "icon": "scale",    "label": "Decision-maker",          "rank": 3},
    "SOURCE_COVERAGE_GAP":   {"rag": "amber", "icon": "gap",      "label": "Outside the corpus",      "rank": 2},
    # a scenario expectation that is about the shape of the answer rather than a rule outcome
    "EXPLANATION_CHAIN_COMPLETE": {"rag": "green", "icon": "check", "label": "Explanation chain complete", "rank": 4},
}
OUTCOMES = [k for k in RAG if k != "EXPLANATION_CHAIN_COMPLETE"]
ORDER = {"red": 0, "amber": 1, "green": 2, "grey": 3}


def of(result: str) -> dict:
    """RAG fields for one outcome; an unknown outcome is shown amber rather than hidden."""
    return RAG.get(result, {"rag": "amber", "icon": "help", "label": result, "rank": 1})


def overall(results: list[str]) -> str:
    """Worst colour wins. Because the final grant is always DISCRETION_REQUIRED, an assessment is never green."""
    colours = {of(r)["rag"] for r in results}
    for c in ("red", "amber", "green"):
        if c in colours:
            return c
    return "grey"


def counts(results: list[str]) -> dict:
    out = {"red": 0, "amber": 0, "green": 0, "grey": 0}
    for r in results:
        out[of(r)["rag"]] += 1
    return out
