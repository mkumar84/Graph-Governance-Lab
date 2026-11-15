"""Assumption test 4: lint rules can be checked from the graph definition alone.

Pass line: at least 4 of 5 seeded defects flagged, and the clean graph raises
no failure. Here all six seeded graphs must be flagged on exactly their own
rule, and the clean graph must pass every rule.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.lint import lint
from app.main import app
from app.models import Graph

GRAPHS = Path(__file__).resolve().parent.parent / "graphs"

SEEDED = {
    "seeded_1_no_verifier": 1,
    "seeded_2_same_family_judge": 2,
    "seeded_3_unbounded_loop": 3,
    "seeded_4_context_leak": 4,
    "seeded_5_gate_bypass": 5,
    "seeded_6_heal_writes_thresholds": 6,
}


def load(name: str) -> Graph:
    return Graph.model_validate(json.loads((GRAPHS / f"{name}.json").read_text()))


def test_clean_graph_passes_every_rule():
    report = lint(load("meridian_claims_triage_clean"))
    assert report.passed, [(r.id, [f.message for f in r.findings]) for r in report.rules if r.findings]


@pytest.mark.parametrize("name,rule_id", SEEDED.items())
def test_seeded_defect_is_flagged_on_its_own_rule_only(name, rule_id):
    report = lint(load(name))
    assert not report.passed
    assert report.failed_rule_ids == [rule_id]


def test_catch_rate_meets_pass_line():
    caught = sum(1 for name in SEEDED if not lint(load(name)).passed)
    assert caught >= 4  # backlog pass line; this suite expects all 6


def test_findings_name_the_offending_node():
    report = lint(load("seeded_2_same_family_judge"))
    rule2 = next(r for r in report.rules if r.id == 2)
    assert any(f.node == "judge" for f in rule2.findings)


def test_scratchpad_access_breaks_independence():
    g = load("meridian_claims_triage_clean")
    g.node("judge").context_allowlist.append("scratchpad:skeptic")
    assert lint(g).failed_rule_ids == [2]


def test_loop_spec_missing_budget_fails_rule_3():
    g = load("meridian_claims_triage_clean")
    g.loops[0].budget_usd = 0
    assert lint(g).failed_rule_ids == [3]


def test_heal_policy_must_escalate_to_a_gate():
    g = load("meridian_claims_triage_clean")
    g.node("extractor").heal_policy.escalate_to = "judge"
    assert lint(g).failed_rule_ids == [6]


def test_undeclared_allowlist_fails_rule_4():
    g = load("meridian_claims_triage_clean")
    g.node("advocate").context_allowlist = None
    assert lint(g).failed_rule_ids == [4]


def test_unknown_edge_target_is_rejected_by_schema():
    data = json.loads((GRAPHS / "meridian_claims_triage_clean.json").read_text())
    data["edges"].append({"source": "intake", "target": "ghost", "context": []})
    with pytest.raises(ValidationError):
        Graph.model_validate(data)


def test_envelope_bands_must_be_ordered():
    data = json.loads((GRAPHS / "meridian_claims_triage_clean.json").read_text())
    data["nodes"][0]["envelope"] = {"pass_at": 0.5, "escalate_below": 0.7}
    with pytest.raises(ValidationError):
        Graph.model_validate(data)


# ---------------------------------------------------------------------- API
client = TestClient(app)


def test_api_lints_a_posted_graph():
    data = json.loads((GRAPHS / "seeded_5_gate_bypass.json").read_text())
    resp = client.post("/lint", json=data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert [r["id"] for r in body["rules"] if r["status"] == "fail"] == [5]


def test_api_rejects_invalid_graph_with_422():
    resp = client.post("/lint", json={"name": "bad", "nodes": [{"id": "a", "role": "wizard"}]})
    assert resp.status_code == 422


def test_api_lists_rules_and_examples():
    assert len(client.get("/rules").json()) == 6
    assert len(client.get("/examples").json()) == 7
    assert client.get("/examples/meridian_claims_triage_clean/lint").json()["passed"] is True
    assert client.get("/examples/nope").status_code == 404


def test_cors_allows_the_published_canvas_only():
    ok = client.get("/health", headers={"Origin": "https://graph-gov-lab.lovable.app"})
    assert ok.headers.get("access-control-allow-origin") == "https://graph-gov-lab.lovable.app"
    other = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_preflight_for_lint_post():
    resp = client.options(
        "/lint",
        headers={
            "Origin": "https://graph-gov-lab.lovable.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://graph-gov-lab.lovable.app"


def test_null_envelope_and_verifies_accepted():
    from fastapi.testclient import TestClient
    from app.main import app
    g = {"name": "t", "nodes": [{"id": "a", "role": "producer", "envelope": None,
                                  "verifies": None, "context_allowlist": []}]}
    r = TestClient(app).post("/lint", json=g)
    assert r.status_code == 200, r.text
