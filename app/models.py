"""Graph definition schema for the Graph Governance Lab.

A graph is a set of nodes (agents, verifiers, gates, actions) joined by edges
that say which context keys travel between them. Loops and heal policies are
declared explicitly so the lint engine can check them before anything runs.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Role = Literal["producer", "verifier", "gate", "action"]
ActionTier = Literal["standard", "prohibited"]

# Things a heal policy may never write to (lint rule 6).
PROTECTED_TARGETS = {"gates", "thresholds", "lint_rules", "heal_policy"}
MAX_HEAL_ATTEMPTS = 5


class Envelope(BaseModel):
    """Quality envelope: score bands for a node (starting values, to calibrate)."""

    pass_at: float = Field(0.85, ge=0, le=1)
    escalate_below: float = Field(0.60, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "Envelope":
        if self.escalate_below >= self.pass_at:
            raise ValueError("escalate_below must be lower than pass_at")
        return self


class HealPolicy(BaseModel):
    max_attempts: int = 0
    budget_usd: float = 0
    escalate_to: Optional[str] = None
    can_write: list[str] = Field(default_factory=list)


class Node(BaseModel):
    id: str
    name: str = ""
    role: Role
    model_family: Optional[str] = None
    # None means "not declared" and fails lint rule 4.
    context_allowlist: Optional[list[str]] = None
    action_tier: ActionTier = "standard"
    # Verifier nodes list the producer node ids they check.
    verifies: list[str] = Field(default_factory=list)
    envelope: Envelope = Field(default_factory=Envelope)
    heal_policy: Optional[HealPolicy] = None


class Edge(BaseModel):
    source: str
    target: str
    # Context keys passed along this edge.
    context: list[str] = Field(default_factory=list)


class LoopSpec(BaseModel):
    nodes: list[str]
    stop_condition: str = ""
    max_iterations: int = 0
    budget_usd: float = 0


class Graph(BaseModel):
    name: str
    description: str = ""
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)
    loops: list[LoopSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_exist(self) -> "Graph":
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        known = set(ids)
        for e in self.edges:
            for ref in (e.source, e.target):
                if ref not in known:
                    raise ValueError(f"edge references unknown node '{ref}'")
        for n in self.nodes:
            for ref in n.verifies:
                if ref not in known:
                    raise ValueError(f"node '{n.id}' verifies unknown node '{ref}'")
        for lp in self.loops:
            for ref in lp.nodes:
                if ref not in known:
                    raise ValueError(f"loop references unknown node '{ref}'")
        return self

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)


class Finding(BaseModel):
    node: Optional[str] = None
    message: str


class RuleResult(BaseModel):
    id: int
    name: str
    status: Literal["pass", "fail"]
    findings: list[Finding] = Field(default_factory=list)


class LintReport(BaseModel):
    graph: str
    passed: bool
    rules: list[RuleResult]

    @property
    def failed_rule_ids(self) -> list[int]:
        return [r.id for r in self.rules if r.status == "fail"]
