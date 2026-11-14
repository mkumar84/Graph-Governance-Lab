"""The six graph lint rules.

Each rule reads only the graph definition, never a run. A graph that fails any
rule cannot be approved for execution.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .models import (
    MAX_HEAL_ATTEMPTS,
    PROTECTED_TARGETS,
    Finding,
    Graph,
    LintReport,
    RuleResult,
)


# ---------------------------------------------------------------- graph helpers
def _adjacency(graph: Graph) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for n in graph.nodes:
        adj[n.id]  # ensure key exists
    for e in graph.edges:
        adj[e.source].add(e.target)
    return adj


def _reachable(adj: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _cycles(graph: Graph) -> list[set[str]]:
    """Strongly connected components that contain a loop (size > 1 or self-loop)."""
    adj = _adjacency(graph)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[set[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj[v]:
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp: set[str] = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1 or v in adj[v]:
                result.append(comp)

    for n in graph.nodes:
        if n.id not in index:
            strong(n.id)
    return result


# ------------------------------------------------------------------------ rules
def rule_verifier_downstream(graph: Graph) -> list[Finding]:
    """1. Every producer has a verifier that checks it, downstream of it."""
    adj = _adjacency(graph)
    findings = []
    for n in graph.nodes:
        if n.role != "producer":
            continue
        downstream = _reachable(adj, n.id)
        checkers = [
            v for v in graph.nodes
            if v.role == "verifier" and n.id in v.verifies and v.id in downstream
        ]
        if not checkers:
            findings.append(Finding(
                node=n.id,
                message=f"Producer '{n.id}' has no verifier downstream that lists it in 'verifies'.",
            ))
    return findings


def rule_independent_verifier(graph: Graph) -> list[Finding]:
    """2. A verifier must not share model family or context with what it checks."""
    findings = []
    for v in graph.nodes:
        if v.role != "verifier":
            continue
        for pid in v.verifies:
            p = graph.node(pid)
            if v.model_family and p.model_family and v.model_family == p.model_family:
                findings.append(Finding(
                    node=v.id,
                    message=(f"Verifier '{v.id}' and producer '{pid}' share model family "
                             f"'{v.model_family}'."),
                ))
            if f"scratchpad:{pid}" in (v.context_allowlist or []):
                findings.append(Finding(
                    node=v.id,
                    message=f"Verifier '{v.id}' can read the private scratchpad of '{pid}'.",
                ))
            if not v.model_family or not p.model_family:
                findings.append(Finding(
                    node=v.id,
                    message=(f"Model family is undeclared for '{v.id}' or '{pid}', "
                             "so independence cannot be shown."),
                ))
    return findings


def rule_loop_bounds(graph: Graph) -> list[Finding]:
    """3. Every cycle has a stop condition, an iteration cap and a budget cap."""
    findings = []
    for comp in _cycles(graph):
        label = ", ".join(sorted(comp))
        spec = next((lp for lp in graph.loops if comp <= set(lp.nodes)), None)
        if spec is None:
            findings.append(Finding(message=f"Cycle [{label}] has no loop spec."))
            continue
        if not spec.stop_condition.strip():
            findings.append(Finding(message=f"Cycle [{label}] has no stop condition."))
        if spec.max_iterations <= 0:
            findings.append(Finding(message=f"Cycle [{label}] has no iteration cap."))
        if spec.budget_usd <= 0:
            findings.append(Finding(message=f"Cycle [{label}] has no budget cap."))
    return findings


def rule_context_allowlist(graph: Graph) -> list[Finding]:
    """4. Every node declares an allowlist and only receives context inside it."""
    findings = []
    for n in graph.nodes:
        if n.context_allowlist is None:
            findings.append(Finding(node=n.id, message=f"Node '{n.id}' declares no context allowlist."))
    for e in graph.edges:
        target = graph.node(e.target)
        if target.context_allowlist is None:
            continue
        for key in e.context:
            if key not in target.context_allowlist:
                findings.append(Finding(
                    node=target.id,
                    message=(f"Edge {e.source} -> {e.target} passes '{key}', "
                             f"which is outside the allowlist of '{target.id}'."),
                ))
    return findings


def rule_human_gate(graph: Graph) -> list[Finding]:
    """5. No path reaches a prohibited-tier action without passing a human gate."""
    adj = _adjacency(graph)
    has_incoming = {e.target for e in graph.edges}
    sources = [n.id for n in graph.nodes if n.id not in has_incoming]
    gates = {n.id for n in graph.nodes if n.role == "gate"}
    prohibited = {n.id for n in graph.nodes if n.action_tier == "prohibited"}

    reached: set[str] = set()
    seen: set[str] = set()
    stack = [s for s in sources if s not in gates]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if cur in prohibited:
            reached.add(cur)
        for nxt in adj[cur]:
            if nxt not in gates:
                stack.append(nxt)
    return [
        Finding(node=pid, message=f"Prohibited-tier node '{pid}' can be reached without a human gate.")
        for pid in sorted(reached)
    ]


def rule_heal_policy(graph: Graph) -> list[Finding]:
    """6. Heal policies are bounded, escalate to a gate, and cannot touch controls."""
    findings = []
    gates = {n.id for n in graph.nodes if n.role == "gate"}
    for n in graph.nodes:
        hp = n.heal_policy
        if hp is None:
            continue
        if hp.max_attempts < 1 or hp.max_attempts > MAX_HEAL_ATTEMPTS:
            findings.append(Finding(
                node=n.id,
                message=f"Heal policy on '{n.id}' needs 1 to {MAX_HEAL_ATTEMPTS} attempts.",
            ))
        if hp.budget_usd <= 0:
            findings.append(Finding(node=n.id, message=f"Heal policy on '{n.id}' has no budget cap."))
        if hp.escalate_to not in gates:
            findings.append(Finding(
                node=n.id,
                message=f"Heal policy on '{n.id}' must escalate to a gate node.",
            ))
        forbidden = sorted(set(hp.can_write) & PROTECTED_TARGETS)
        if forbidden:
            findings.append(Finding(
                node=n.id,
                message=f"Heal policy on '{n.id}' can write to protected controls: {', '.join(forbidden)}.",
            ))
    return findings


RULES: list[tuple[int, str, Callable[[Graph], list[Finding]]]] = [
    (1, "Verifier downstream", rule_verifier_downstream),
    (2, "Independent verifier", rule_independent_verifier),
    (3, "Loop bounds", rule_loop_bounds),
    (4, "Context allowlist", rule_context_allowlist),
    (5, "Human gate", rule_human_gate),
    (6, "Heal policy", rule_heal_policy),
]


def lint(graph: Graph) -> LintReport:
    results = []
    for rid, name, fn in RULES:
        findings = fn(graph)
        results.append(RuleResult(
            id=rid, name=name,
            status="fail" if findings else "pass",
            findings=findings,
        ))
    return LintReport(
        graph=graph.name,
        passed=all(r.status == "pass" for r in results),
        rules=results,
    )
