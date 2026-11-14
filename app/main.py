"""FastAPI service: submit a graph definition, get a lint report back."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .lint import RULES, lint
from .models import Graph, LintReport

GRAPHS_DIR = Path(__file__).resolve().parent.parent / "graphs"

app = FastAPI(title="Graph Governance Lab", version="0.1.0")
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
