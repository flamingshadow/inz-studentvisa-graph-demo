"""Phase 5: the Claude Desktop MCP server over real stdio."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from svkg.docspec import ROOT

pytestmark = pytest.mark.neo4j
SVKG = str(Path(sys.executable).parent / "svkg")


async def _session(fn):
    params = StdioServerParameters(command=SVKG, args=["mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def run(fn):
    return asyncio.run(_session(fn))


async def _call(s, name, args):
    res = await s.call_tool(name, args)
    return json.loads(res.content[0].text)


def test_tools_carry_contract_and_only_one_writes(live):
    tools = run(lambda s: s.list_tools()).tools
    names = {t.name for t in tools}
    assert "assess_application" in names and len(names) >= 15
    assert {"instruction_text", "instruction_history", "rules_by_instruction", "list_instructions"} <= names
    for t in tools:
        assert "POC NOTICE" in t.description and "Return SOURCE_COVERAGE_GAP" in t.description
        props = set((t.input_schema or {}).get("properties", {}))
        assert not {"cypher", "query"} & props, t.name           # no free Cypher
        writes = t.name == "assess_application"
        assert t.annotations.read_only_hint is (not writes), t.name


def test_query_and_assess_roundtrip(live):
    app = json.loads((ROOT / "testing/applications/SVP-027.json").read_text())

    async def go(s):
        d = await _call(s, "rule_detail", {"rule_id": "SV-IVL-001"})
        a = await _call(s, "assess_application", {"application": app})
        x = await _call(s, "explain_evaluation",
                        {"evaluation_id": next(e["evaluation_id"] for e in a["result"]["rule_evaluations"]
                                               if e["check_id"] == "SV-CHECK-IVL-001")})
        return d, a, x
    d, a, x = run(go)
    assert d["result"]["provision"] == "r 26AAE(5)(c)" and "POC only" in d["poc_notice"]
    assert a["result"]["scope"]["production_decision"] is False
    assert x["result"]["explanation_chain"][0]["result"] == "NOT_APPLICABLE"
    log = (ROOT / "logs/mcp-calls.jsonl").read_text().splitlines()
    assert {"rule_detail", "assess_application", "explain_evaluation"} <= {json.loads(l)["tool"] for l in log[-10:]}


def test_rejects_non_synthetic(live):
    app = json.loads((ROOT / "testing/applications/SVP-001.json").read_text())
    app["synthetic"] = False
    r = run(lambda s: _call(s, "assess_application", {"application": app}))
    assert "error" in r
