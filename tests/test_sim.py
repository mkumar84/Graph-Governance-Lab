import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import changes, sim
from app.main import app
from app.models import Graph

G = Path(__file__).resolve().parent.parent / "graphs"
client = TestClient(app)


def load(name):
    return Graph.model_validate(json.loads((G / f"{name}.json").read_text()))


@pytest.fixture(autouse=True)
def _reset():
    changes.reset()


def test_golden_set_shape_and_disjoint_from_dev():
    g, d = sim.golden_set(), sim.dev_set()
    assert len(g) == 30 and sum(1 for c in g if c.defect) == 10
    assert not {c.id for c in g} & {c.id for c in d}


def test_receipts_chain_valid_and_tamper_detected():
    r = sim.run_graph(load("meridian_claims_triage_clean"), sim.find_case("d01"), 0, True)
    assert r["chain_valid"] and r["receipts"][0]["kind"] == "run_start"
    r["receipts"][2]["score"] = 0.99
    assert not sim.verify_chain(r["receipts"])


def test_runs_are_deterministic():
    g, c = load("meridian_claims_triage_clean"), sim.find_case("d04")
    assert sim.run_graph(g, c, 3, True) == sim.run_graph(g, c, 3, True)


def test_prohibited_action_blocked_without_gate_approval():
    g, c = load("meridian_claims_triage_clean"), sim.find_case("d01")
    assert sim.run_graph(g, c, 0, False)["outcome"] == "awaiting_human"
    assert sim.run_graph(g, c, 0, True)["outcome"] == "executed"


def test_gate_bypass_graph_executes_ungated():
    assert sim.run_graph(load("seeded_5_gate_bypass"), sim.find_case("d01"), 0, False)[
        "outcome"] == "executed_ungated"


def test_independent_judge_beats_same_family():
    r = sim.judge_experiment()
    assert r["independent_beats_same_family"]
    assert r["catch_rate"]["independent"] > r["catch_rate"]["same_family"] + 0.3


def test_heal_is_bounded_and_receipted():
    g = load("meridian_claims_triage_clean")
    r = sim.run_graph(g, sim.find_case("d08"), 0, True, faults={"extractor": 0.2})
    heals = [x for x in r["receipts"] if x["kind"] == "heal"]
    assert len(heals) <= g.node("extractor").heal_policy.max_attempts
    assert all("before" in h and "after" in h and h["diff"] for h in heals)


def test_heal_rate_alert_exposes_upstream_fault():
    g = load("meridian_claims_triage_clean")
    assert sim.batch_metrics(g)["alerts"] == []
    bad = sim.batch_metrics(g, {"extractor": 0.15})
    assert [a["node"] for a in bad["alerts"]] == ["extractor"]


def test_agreement_metric():
    a = sim.agreement([True, False, True, False], [0.9, 0.2, 0.95, 0.9])
    assert a["agreement"] == 0.75
    with pytest.raises(ValueError):
        sim.agreement([True], [0.9, 0.8])


def test_persistent_change_flow_approval_and_rollback():
    g = load("meridian_claims_triage_clean")
    c = changes.propose(g, "extractor", "prompt", "prompt", "cite source lines", 0.05)
    assert c["status"] == "proposed" and c["target_lift"] > 0
    assert changes.active_deltas(g)["extractor"] == 0  # not applied until approved
    changes.approve(c["id"], "A. Analyst")
    assert changes.active_deltas(g)["extractor"] == 0.05
    changes.rollback(c["id"], "A. Analyst")
    assert changes.active_deltas(g)["extractor"] == 0


def test_change_with_collateral_regression_is_rejected():
    g = load("meridian_claims_triage_clean")
    c = changes.propose(g, "extractor", "prompt", "prompt", "risky", 0.1, collateral=-0.1)
    assert c["status"] == "rejected_regression"
    with pytest.raises(ValueError):
        changes.approve(c["id"], "A. Analyst")


@pytest.mark.parametrize("target", ["gates", "thresholds", "lint_rules", "heal_policy"])
def test_healer_cannot_write_immutable_controls(target):
    with pytest.raises(PermissionError):
        changes.propose(load("meridian_claims_triage_clean"), "extractor", "config", target, "", 0.1)


def test_topology_proposals_are_never_applied():
    out = changes.topology_proposals(load("seeded_3_unbounded_loop"))
    assert out["applied"] is False and out["proposals"]
    assert all(p["applied"] is False for p in out["proposals"])


def test_api_run_blocks_failing_graph_unless_overridden():
    r = client.post("/run", json={"example": "seeded_2_same_family_judge"})
    assert r.status_code == 409 and 2 in r.json()["detail"]["failed_rules"]
    r = client.post("/run", json={"example": "seeded_2_same_family_judge", "override_lint": True})
    assert r.status_code == 200 and r.json()["simulated"] is True


def test_api_clean_run_metrics_and_heal_endpoints():
    assert client.post("/run", json={"example": "meridian_claims_triage_clean",
                                     "approve": True}).status_code == 200
    m = client.post("/metrics", json={"example": "meridian_claims_triage_clean",
                                      "faults": {"extractor": 0.15}})
    assert m.json()["alerts"]
    p = client.post("/heal/propose", json={"node_id": "extractor", "node_delta": 0.05})
    cid = p.json()["id"]
    assert client.post(f"/heal/changes/{cid}/approve", json={"by": "Analyst"}).status_code == 200
    assert client.post(f"/heal/changes/{cid}/rollback", json={"by": "Analyst"}).status_code == 200
    assert client.post("/heal/propose", json={"node_id": "extractor",
                                              "target": "thresholds"}).status_code == 403
    assert client.get("/evals/judge").json()["independent_beats_same_family"]
    assert client.get("/cases").status_code == 200
