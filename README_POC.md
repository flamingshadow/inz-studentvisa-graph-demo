# Student Visa Knowledge Graph — POC (spec v0.2)

> **POC scope notice.** This build demonstrates search, graph traversal, provenance and controlled
> decision support. It uses **synthetic data only** and is **not a production visa-decision instrument**.
> There is no automated grant or decline: the final grant is always `DISCRETION_REQUIRED`.

The build follows *Student Visa Knowledge Graph — POC Consolidated Export v0.2* (17 November 2026,
`docs/`) and the build plan's product-owner decisions. It does not use the INZ Operational Manual or any
earlier project. Where the document is silent, the build records an interpretation flag (listed below)
rather than inventing content.

## What is here

| Path | Contents |
|---|---|
| `schema/` | Ontology (Section 3 layers + Appendix F templates), enumerations, the 24 relationship types (3.1), agent contract (Section 9) |
| `regulatory/` | 9 sources, 217 provisions (parent chain from section to paragraph, exact wording from legislation.govt.nz), concepts, actors, FT-001..FT-018, and every static relationship |
| `data/legislation/` | Cached legislation.govt.nz pages for the four published instruments, plus `provision-text.json` (wording, URL, version, retrieval date and page SHA-256 per provision) |
| `rules/` | 137 verified rules (12.1 fields), 10 candidate rules (Section 6, `RECONCILIATION_REQUIRED`), 11 rule groups (5.2), and derived qualifiers |
| `evidence/` | Table 9 evidence types, evidence requirements per rule, verification rules |
| `decisioning/` | 11 decision checks, 11 lifecycle states, 10 transitions |
| `testing/` | TC-01..TC-10 test cases, synthetic applications (`testing/applications/*.json`), expected results |
| `governance/` | Source coverage, GAP-01/02, RISK-01..05, provenance (document SHA-256), interpretation flags |
| `svkg/` | Extractor, checker, loader, decision engine, MCP server, CLI |

## Setup (macOS)

```bash
cd inz-studentvisa-graph-demo
uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[test]"
cp .env.example .env    # then set NEO4J_PASSWORD; never commit .env
```

In Neo4j Desktop (Enterprise developer instance, `bolt://localhost:7687`), run this against the `system` database once:

```cypher
CREATE DATABASE studentvisa IF NOT EXISTS;
```

`.env` keys: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE=studentvisa`.

## Build, load, assess

```bash
.venv/bin/svkg fetch-legislation                       # optional: refresh provision wording from legislation.govt.nz
```

```bash
.venv/bin/svkg build                                   # extract + check + load
```

```bash
.venv/bin/svkg check                                   # diff files against the .docx; exits 1 on any mismatch
```

```bash
.venv/bin/svkg assess testing/applications/TC-03.json --summary
```

```bash
.venv/bin/svkg assess-batch testing/applications/
```

```bash
.venv/bin/python -m pytest -q                          # 76 tests; needs the live studentvisa database
```

- `svkg load` is idempotent. It MERGEs spec nodes (`origin:'SPEC'`), removes stale ones, never touches
  assessment nodes, and writes one `LoadLog` node per run.
- `svkg assess` writes `Application`, `EvidenceItem`, `ObservedFact`, `VerifiedFact`, `RuleEvaluation`,
  `DecisionOutcome` and `Explanation` nodes (`origin:'ASSESSMENT'`). It also appends G.1 audit events to
  `logs/audit-log.jsonl`. Use `--no-write` for a dry run.

### Application JSON

An application has a header, F.6 evidence items and F.7 facts. Each fact says whether it is verified:

```json
{"application_id": "POC-SV-APP-X", "product_id": "STUDENT-VISA", "synthetic": true, "channel": "paper",
 "applicant": {"applicant_id": "APPLICANT-X"},
 "evidence_items": [{"evidence_item_id": "EVID-X-1", "evidence_type": "Passport", "verification_status": "VERIFIED"}],
 "facts": [{"fact_id": "FACT-X-1", "attribute": "identity_document_provided", "value": true,
            "evidence_item_ids": ["EVID-X-1"], "verification_status": "VERIFIED"}]}
```

`synthetic: true` is mandatory. See `testing/applications/TC-01.json` for a complete example, or call the
`application_template` tool.

### Engine behaviour

| Class | Engine action |
|---|---|
| A1 | Runs on the structured value, verified or not |
| A2 | Runs only on `VERIFIED` facts. An unverified or missing fact gives `UNKNOWN` plus a missing-information entry |
| A3 | `HUMAN_REVIEW_REQUIRED`, with rules and evidence presented |
| A4 | `DISCRETION_REQUIRED`, with power, decision-maker, provision and constraints |
| A5 | `AMBIGUOUS`: the engine stops |
| gap | `SOURCE_COVERAGE_GAP` (GAP-01). Nothing is inferred from the Act or Regulations |

Checks: SV-CHECK-APP-001, IVL-001, U10-001, WORK-001, FIN-001 and DISC-001 come from Table 11;
SV-CHECK-APP-005 comes from F.8. MIN-001, HEALTH-001, EDU-009 and INSTR-001 were added for test cases (IF-13).
Every evaluation returns the rule, facts used, evidence used, unresolved requirements, automation class,
instrument, provision and version.

## Claude Desktop

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json` under `mcpServers`, then
restart Claude Desktop. Keep any servers already there.

```json
{
  "mcpServers": {
    "student-visa-knowledge-graph": {
      "command": "/Users/rajatgoyal/INZ-Knowledge/inz-studentvisa-graph-demo/.venv/bin/svkg",
      "args": ["mcp"]
    }
  }
}
```

The server reads the Neo4j credentials from the repo's `.env`, so no secret goes into the config.

**Tools.** Every tool's description carries the POC notice and the Section 9 agent contract. Only
`assess_application` writes; all the others run in READ transactions with fixed Cypher. Calls are logged to
`logs/mcp-calls.jsonl`.

| Tool | Purpose |
|---|---|
| `list_products`, `list_rule_domains` | Orientation |
| `rules_by_domain`, `rules_by_concept`, `search_rules` | Find applicable rules with instrument and exact provision |
| `rule_detail` | Qualifiers, provisions (with source-text status), actors, evidence, checks, gaps, flags |
| `evidence_for_rule`, `facts_supported_by_evidence` | Rule → evidence, evidence → facts |
| `decision_checks`, `lifecycle_transitions`, `coverage_gaps`, `interpretation_flags`, `list_test_cases` | Model and governance |
| `explain_evaluation` | RuleEvaluation → DecisionCheck → Rule → Provision → InstrumentVersion → AuthoritySource |
| `list_assessments`, `get_assessment`, `application_template` | Assessment records |
| `assess_application` | The only writing tool |

Example prompts: *"Which Student Visa rules cover sponsorship, and under which provisions?"*,
*"Assess this synthetic application"* (paste JSON), *"Why did SV-FEE-003 come out NOT_APPLICABLE?"*

## Bloom tips

- Open Bloom against the `studentvisa` database and create a perspective that includes all categories.
  Every node has a `name` property; use it as the caption.
- Useful categories: `Rule`, `Provision`, `AuthoritySource`, `DecisionCheck`, `RuleEvaluation`,
  `EvidenceItem`, `VerifiedFact`, `CoverageGap`.
- Colour `Rule` by `catalogue` (VERIFIED vs CANDIDATE) and `RuleEvaluation` by `result`.
- Search phrase ideas: `Rule DERIVED_FROM Provision`; `DecisionCheck PRODUCES_OUTCOME RuleEvaluation`;
  `EvidenceItem SUPPORTS_FACT ObservedFact VERIFIED_AS VerifiedFact`.
- To hide assessment data, filter on `origin = 'SPEC'`.

## Known gaps

- **Pastoral Care Code wording.** 167 of 217 provisions carry the exact published wording from
  legislation.govt.nz, with URL, version and retrieval date. The 50 clauses of the Education (Pastoral Care
  of Tertiary and International Learners) Code of Practice 2021 stay `SOURCE_TEXT_NOT_RETRIEVED`, because the
  Code is not published on legislation.govt.nz (decision 8). `svkg check` lists them as its one warning.
- **Instrument versions.** The page version lines are: Immigration Act 10 July 2026 (matches the spec),
  Visa, Entry Permission, and Related Matters Regulations 20 October 2026, Privacy Act 8 October 2026,
  Carriers' Information Obligations Regulations "Reprint as at 30 September 2020". Re-run
  `svkg fetch-legislation` to pick up new versions; `svkg check` fails if the Act's version moves away from
  the spec's 2026-07-10 without a flag.
- **Definition of done item 6.** Candidate rules are not reconciled to Immigration Instructions. That is
  outside this POC by design; the guard is that every assessment says `production_decision: false`.

## Deviations and interpretation flags

The full text of each flag is in `governance/interpretation-flags.json` and on `InterpretationFlag` nodes.
The product owner reviewed every flag on 23 Nov 2026 and accepted all recommendations. Each flag records
its `status` (`ACCEPTED_BY_PRODUCT_OWNER`), `decision_reference` and `resolution`. A flag raised by a later
build (for example a new version mismatch) stays `OPEN` until reviewed. IF-02, IF-12 and IF-13 are to be
raised with the spec authors for v0.3.

| ID | Kind | Title |
|---|---|---|
| IF-01 | **Deviation (decision 3)** | Candidate funds thresholds execute; TC-06 expectation adjusted |
| IF-02 | Interpretation | Published rule type codes (OR/PERM/DR/SR) not defined |
| IF-03 | Interpretation | Definitional rules have no deontic modality |
| IF-04 | Interpretation | Compound automation classes: the most restrictive is used, and a gap marker gives SOURCE_COVERAGE_GAP |
| IF-05 | Interpretation | Rule domain derived from rule-ID prefix |
| IF-06 | Interpretation | Qualifiers derived from RuleSpeak wording (exact spans only) |
| IF-07 | Interpretation | Provision sub-unit types; IPPs taken from the s 22 table |
| IF-08 | Interpretation | Instrument types for sources other than the Immigration Act |
| IF-09 | Interpretation (accepted) | Source versions taken from legislation.govt.nz |
| IF-10 | Interpretation (accepted) | Provision text retrieved except the Pastoral Care Code |
| IF-11 | Interpretation | Rule-to-evidence mapping via Table 9 "typical rule domain" |
| IF-12 | Interpretation | 12.1 vs F.5 ID inconsistency for SV-APP-005 |
| IF-13 | Interpretation | Decision checks implied by test cases |
| IF-14 | Interpretation | TC-09 expects NOT_SATISFIED for an A3 rule |
| IF-15 | Interpretation | Maintenance calculation by duration; prepaid accommodation not applied |
| IF-16 | Interpretation | Overall status aggregation |
| IF-17 | Interpretation | Actors that are rule subjects but not "Parties" |
| IF-18 | Interpretation | Health outcome for TC-07 (A3) |
| IF-19 | Interpretation | Concept typing and product hierarchy |
| IF-20 | Interpretation | Online applications use the D.1 rules |
| IF-21 | Interpretation | The under-10 check is a provider obligation |
| IF-22 | Interpretation | Test types |
| IF-23 | Interpretation | Lifecycle IDs and authorising rules |
| IF-24 | Interpretation | Operational nodes (LoadLog, `kind` property) outside the ontology |

**Decision 3, in short.** The candidate figures run: tertiary NZD 20,000/yr or 1,667/month, school
NZD 17,000/yr or 1,417/month, maternity NZD 9,000. Every Threshold node and every funds evaluation carries
`CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED` and `provenance_status: RECONCILIATION_REQUIRED`.
TC-06 now expects that label instead of `SOURCE_COVERAGE_GAP`.
