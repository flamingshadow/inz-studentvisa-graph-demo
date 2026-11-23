# Student Visa Knowledge Graph — POC (spec v0.2)

> **POC scope notice.** This build demonstrates search, graph traversal, provenance and controlled
> decision support. It uses **synthetic data only** and is **not a production visa-decision instrument**.
> There is no automated grant or decline: the final grant is always `DISCRETION_REQUIRED`.

The build follows *Student Visa Knowledge Graph — POC Consolidated Export v0.2* (17 November 2026,
`docs/`) and the build plan's product-owner decisions. Since 23 Nov 2026 it also loads the **INZ
Operational Manual** student instructions as the Immigration Instructions source (IF-25, a product-owner
reversal of the build plan's original ground rule). Where a source is silent, the build records an
interpretation flag (listed below) rather than inventing content.

**Design principle: minimal graph (IF-34).** The graph uses the smallest set of node types and relationships
that answers staff questions. The allowed labels and relationship types are declared in `graph_profile`
(`schema/ontology.yaml`), and `svkg load` and `svkg check` reject anything outside it. A node is never created
just to restate text or one value that lives elsewhere; that goes in a property. Objects with no relationships
stay in the JSON files and are not loaded. Changing the profile is a deliberate edit, recorded with a flag.

**No duplicate information (IF-35).** No two nodes or relationships hold the same fact:
- Instruction paragraphs live in their instruction unit's text and in the rule built from each paragraph.
- Condition, exception and time phrases are properties of their rule. Discretion is the rule's A4 class plus
  its link to the decision-maker.
- An evidence requirement is the `REQUIRES_EVIDENCE` relationship itself, carrying the requirement's
  details.
- A source's version is a property of the source.
- A historic instruction version whose text matches the next version is merged into it.
- Each fact has one `INPUT_TO` per decision check, listing the evaluations that used it.

The Appendix A JSON files still hold every object.

## What is here

| Path | Contents |
|---|---|
| `schema/` | Ontology (Section 3 layers + Appendix F templates), enumerations, the 24 relationship types (3.1), agent contract (Section 9) |
| `regulatory/` | 9 sources, 217 provisions (parent chain from section to paragraph, exact wording from legislation.govt.nz), concepts, actors, FT-001..FT-018, and every static relationship |
| `data/legislation/` | Cached legislation.govt.nz pages for the four published instruments, plus `provision-text.json` (wording, URL, version, retrieval date and page SHA-256 per provision) |
| `data/opsmanual/` | Cached Operational Manual pages (Student Visas U1-U16, all versions, plus the general sections they cite) and `instructions.json` (parsed units, paragraphs, effective dates, version chains) |
| `rules/` | 137 verified spec rules (12.1 fields), 731 instruction rules (one per paragraph of each current U1–U16, A4, A5, E4, E5, E6.5 and E7 instruction), 10 candidate rules (Section 6, now reconciled to the manual), 11 rule groups (5.2), and qualifiers (in the graph only thresholds are nodes) |
| `evidence/` | Table 9 evidence types, evidence requirements per rule, verification rules |
| `decisioning/` | 11 decision checks, 11 lifecycle states, 10 transitions |
| `testing/` | 50 synthetic persona test cases (SVP-001..SVP-050), synthetic applications (`testing/applications/*.json`), expected results |
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
.venv/bin/svkg fetch-opsmanual                         # optional: refresh the Operational Manual pages (~2 min)
```

```bash
.venv/bin/svkg build                                   # extract + check + load
```

```bash
.venv/bin/svkg check                                   # diff files against the .docx; exits 1 on any mismatch
```

```bash
.venv/bin/svkg assess testing/applications/SVP-023.json --summary
```

```bash
.venv/bin/svkg assess-batch testing/applications/
```

```bash
.venv/bin/python -m pytest -q                          # 174 tests; needs the live studentvisa database
```

- `svkg load` is idempotent. It MERGEs spec nodes (`origin:'SPEC'`), removes stale ones, never touches
  assessment nodes, and writes one `LoadLog` node per run.
- `svkg assess` writes `Application`, `EvidenceItem`, `ObservedFact` and `VerifiedFact` nodes, one
  `RuleEvaluation` per rule evaluated (result and explanation as properties), and one `DecisionOutcome` plus
  one `Explanation` for the whole assessment (`origin:'ASSESSMENT'`). It also appends G.1 audit events to
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

`synthetic: true` is mandatory. See `testing/applications/SVP-001.json` for a complete example, or call the
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

**Funds (SV-CHECK-FIN-001)** follows Operational Manual U3.20 (IF-30):
- **Under 36 weeks:** NZ$1,667 per month (tertiary or non-compulsory study) or NZ$1,417 per month
  (compulsory study, Years 1–13).
- **36 weeks or longer:** NZ$20,000 or NZ$17,000 per year. One year is the minimum, and the amount is pro
  rata beyond 12 months.
- **Deductions and alternatives:** prepaid living expenses are deducted. The Samoa/Tonga written guarantee
  is accepted.
- **Maternity:** NZ$9,000 is added when the applicant is due to give birth in New Zealand and isn't eligible
  for publicly funded care.

The study length can be given as `programme_duration_weeks` or `study_duration_months`.

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
| `rule_detail` | Thresholds, condition/exception phrases, provisions (with source-text status), actors, evidence, checks, gaps, flags |
| `evidence_for_rule`, `facts_supported_by_evidence` | Rule → evidence, evidence → facts |
| `decision_checks`, `lifecycle_transitions`, `coverage_gaps`, `interpretation_flags`, `list_test_cases` | Model and governance |
| `explain_evaluation` | RuleEvaluation → DecisionCheck → Rule → Provision → AuthoritySource (with its version) |
| `list_assessments`, `get_assessment`, `application_template` | Assessment records |
| `list_instructions`, `instruction_text`, `instruction_history`, `rules_by_instruction` | Operational Manual: exact instruction text with URL and effective date, the version in force on a date (`as_at`), every version, and the rules derived from an instruction |
| `assess_application` | The only writing tool |

Example prompts: *"Which Student Visa rules cover sponsorship, and under which provisions?"*,
*"Assess this synthetic application"* (paste JSON), *"Why did SV-FEE-003 come out NOT_APPLICABLE?"*,
*"What does U3.20.30 say, and what applied on 1 June 2015?"*, *"What are the work conditions for tertiary
students?"*

## Bloom tips

- Open Bloom against the `studentvisa` database and create a perspective that includes all categories.
  Every node has a `name` property; use it as the caption.
- Useful categories: `Rule`, `Provision`, `AuthoritySource`, `DecisionCheck`, `RuleEvaluation`,
  `EvidenceItem`, `VerifiedFact`, `CoverageGap`.
- Colour `Rule` by `catalogue` (VERIFIED, INSTRUCTION, CANDIDATE) and `RuleEvaluation` by `result`.
- For one instruction: search `Provision` with `instruction_code = U3.20`, then expand `PARENT_OF`,
  `REFERENCES` and `SUPERSEDES`.
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
- **Operational Manual scope (GAP-01, partly closed).** Only Student Visas (U1–U16, every version) and the
  general sections they link to directly are loaded (current versions only). Anything beyond that still returns
  `SOURCE_COVERAGE_GAP`. The automation classes on instruction rules are set from their wording, unless a
  check executes the rule (IF-26).
- **Definition of done item 6.** Nine of the ten candidate rules are now reconciled to instructions and marked
  `SUPERSEDED_BY_INSTRUCTION`. Family support is only partly covered, because partner pathways sit in the W and
  V sections. Every assessment still says `production_decision: false`.

## Deviations and interpretation flags

The full text of each flag is in `governance/interpretation-flags.json` and on `InterpretationFlag` nodes.
The product owner reviewed every flag on 23 Nov 2026 and accepted all recommendations. Each flag records
its `status` (`ACCEPTED_BY_PRODUCT_OWNER`), `decision_reference` and `resolution`. A flag raised by a later
build (for example a new version mismatch) stays `OPEN` until reviewed. IF-02, IF-12 and IF-13 are to be
raised with the spec authors for v0.3.

| ID | Kind | Title |
|---|---|---|
| IF-01 | **Deviation (decision 3)** | Candidate funds thresholds execute |
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
| IF-14 | Interpretation | Provider breach check returns NOT_SATISFIED for an A3 rule |
| IF-15 | Interpretation | Maintenance calculation by duration; prepaid accommodation not applied |
| IF-16 | Interpretation | Overall status aggregation |
| IF-17 | Interpretation | Actors that are rule subjects but not "Parties" |
| IF-18 | Interpretation | Health outcome (A3) |
| IF-19 | Interpretation | Concept typing and product hierarchy |
| IF-20 | Interpretation | Online applications use the D.1 rules |
| IF-21 | Interpretation | The under-10 check is a provider obligation |
| IF-22 | Interpretation | Test types |
| IF-23 | Interpretation | Lifecycle IDs and authorising rules |
| IF-24 | Interpretation | Operational nodes (LoadLog, `kind` property) outside the ontology |
| IF-25 | **Deviation (accepted)** | Operational Manual used as the Immigration Instructions source |
| IF-26 | Interpretation (fixed, accepted) | Automation class, evidence and actors for instruction rules are heuristic |
| IF-27 | Interpretation (accepted) | Atomic decomposition of instruction text |
| IF-28 | Interpretation (accepted) | Versions and history |
| IF-29 | Decision (accepted) | Scope of the Operational Manual load |
| IF-30 | Decision (accepted) | Funds check follows U3.20 (supersedes IF-01 and the IF-15 boundary) |
| IF-31 | Interpretation (accepted) | Candidate rules reconciled to instructions |
| IF-32 | Interpretation (changed, accepted) | Domains; rules only for the student sections and core general sections |
| IF-33 | Interpretation (accepted) | SV-INSTR-001 with instructions loaded |
| IF-34 | Principle (accepted) | Minimal graph: fewest node types and relationships |
| IF-35 | Principle (accepted) | Duplicate nodes and relationships removed |

**Decision 3, in short (superseded by IF-30 on 23 Nov 2026).** The candidate figures ran: tertiary NZD 20,000/yr or 1,667/month, school
NZD 17,000/yr or 1,417/month, maternity NZD 9,000. Every Threshold node and every funds evaluation carries
`CANDIDATE_FIGURE – SOURCE_COVERAGE_CONFIRMATION_REQUIRED` and `provenance_status: RECONCILIATION_REQUIRED`.
The figures are still held as candidate thresholds, but they no longer execute.
