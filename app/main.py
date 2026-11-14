"""FastAPI service: submit a graph definition, get a lint report back."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .lint import RULES, lint
from .models import Graph, LintReport

GRAPHS_DIR = Path(__file__).resolve().parent.parent / "graphs"

app = FastAPI(title="Graph Governance Lab", version="0.1.0")
# Open CORS for the Lovable frontend during Phase 1; tighten before any deploy.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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
