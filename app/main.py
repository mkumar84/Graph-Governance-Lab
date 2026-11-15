"""FastAPI service: submit a graph definition, get a lint report back."""
from __future__ import annotations

import json
import os
from pathlib import Path

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .lint import RULES, lint
from pydantic import BaseModel, Field

from . import changes, sim
from .models import Graph, LintReport

GRAPHS_DIR = Path(__file__).resolve().parent.parent / "graphs"

app = FastAPI(title="Graph Governance Lab", version="0.2.0")
# Browsers may call this API only from these origins. Add more with the
# CORS_ORIGINS environment variable (comma-separated, exact origins).
DEFAULT_ORIGINS = [
    "https://graph-gov-lab.lovable.app",
    "https://id-preview--0cf79d66-cfd5-4bf6-9859-52bf26948c5c.lovable.app",
    "http://localhost:8080",
    "http://localhost:5173",
]
ALLOWED_ORIGINS = DEFAULT_ORIGINS + [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/rules")
def rules() -> list[dict]:
    return [{"id": rid, "name": name, "description": (fn.__doc__ or "").strip()}
            for rid, name, fn in RULES]


@app.post("/lint", response_model=LintReport)
def lint_graph(graph: Graph) -> LintReport:
    return lint(graph)


@app.get("/examples")
def list_examples() -> list[str]:
    return sorted(p.stem for p in GRAPHS_DIR.glob("*.json"))


@app.get("/examples/{name}")
def get_example(name: str) -> dict:
    path = GRAPHS_DIR / f"{name}.json"
    if not path.is_file() or path.parent != GRAPHS_DIR:
        raise HTTPException(status_code=404, detail="example not found")
    return json.loads(path.read_text())


@app.get("/examples/{name}/lint", response_model=LintReport)
def lint_example(name: str) -> LintReport:
    return lint(Graph.model_validate(get_example(name)))


# ------------------------------------------------------------ Phases 2-4
def _resolve(example: Optional[str], graph: Optional[Graph]) -> Graph:
    if graph is not None:
        return graph
    if example:
        return Graph.model_validate(get_example(example))
    raise HTTPException(status_code=422, detail="give either 'example' or 'graph'")


class RunRequest(BaseModel):
    example: Optional[str] = None
    graph: Optional[Graph] = None
    case_id: str = "d01"
    seed: int = 0
    approve: bool = False          # simulated human analyst decision at the gate
    heal: bool = True
    faults: dict[str, float] = Field(default_factory=dict)
    override_lint: bool = False    # demo only: run a graph that failed lint


@app.get("/cases")
def cases() -> dict:
    return {"golden": [c.__dict__ for c in sim.golden_set()],
            "dev": [c.__dict__ for c in sim.dev_set()],
            "note": "Golden set is held out from the healer. Dev set feeds batch metrics."}


@app.post("/run")
def run(req: RunRequest) -> dict:
    g = _resolve(req.example, req.graph)
    report = lint(g)
    if not report.passed and not req.override_lint:
        raise HTTPException(status_code=409, detail={
            "message": "Graph failed lint and cannot be approved for execution.",
            "failed_rules": report.failed_rule_ids})
    try:
        case = sim.find_case(req.case_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown case_id")
    result = sim.run_graph(g, case, req.seed, req.approve, changes.active_deltas(g),
                           req.faults, req.heal)
    result["lint_passed"] = report.passed
    result["simulated"] = True
    return result


class MetricsRequest(BaseModel):
    example: Optional[str] = None
    graph: Optional[Graph] = None
    faults: dict[str, float] = Field(default_factory=dict)
    seed: int = 0


@app.post("/metrics")
def metrics(req: MetricsRequest) -> dict:
    g = _resolve(req.example, req.graph)
    out = sim.batch_metrics(g, req.faults, req.seed, changes.active_deltas(g))
    out["simulated"] = True
    return out


@app.get("/evals/judge")
def eval_judge(trials: int = 200) -> dict:
    return sim.judge_experiment(max(1, min(trials, 2000)))


class AgreementRequest(BaseModel):
    human_pass: list[bool]
    judge_scores: list[float]
    threshold: float = 0.85


@app.post("/evals/agreement")
def eval_agreement(req: AgreementRequest) -> dict:
    try:
        return sim.agreement(req.human_pass, req.judge_scores, req.threshold)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class ProposeRequest(BaseModel):
    example: Optional[str] = "meridian_claims_triage_clean"
    graph: Optional[Graph] = None
    node_id: str
    kind: str = "prompt"
    target: str = "prompt"
    note: str = ""
    node_delta: float = Field(0.0, ge=-0.3, le=0.3)   # simulated effect on the node
    collateral: float = Field(0.0, ge=-0.3, le=0.3)   # simulated effect on other nodes


@app.post("/heal/propose")
def heal_propose(req: ProposeRequest) -> dict:
    g = _resolve(req.example, req.graph)
    try:
        return changes.propose(g, req.node_id, req.kind, req.target, req.note,
                               req.node_delta, req.collateral)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class ApprovalRequest(BaseModel):
    by: str


@app.get("/heal/changes")
def heal_changes() -> list[dict]:
    return changes.list_changes()


@app.post("/heal/changes/{change_id}/approve")
def heal_approve(change_id: str, req: ApprovalRequest) -> dict:
    try:
        return changes.approve(change_id, req.by)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown change")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/heal/changes/{change_id}/rollback")
def heal_rollback(change_id: str, req: ApprovalRequest) -> dict:
    try:
        return changes.rollback(change_id, req.by)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown change")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/heal/topology")
def heal_topology(req: MetricsRequest) -> dict:
    return changes.topology_proposals(_resolve(req.example, req.graph))
