"""Heal levels 4 and 5.

Level 4: a persistent change is proposed, tested on the held-out golden set,
and only applied after a human approves it. Every change can be rolled back.
Level 5: topology suggestions are text only and are never applied.

State is in memory (a restart clears it). A database comes with a later phase.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from .lint import lint
from .models import PROTECTED_TARGETS, Graph
from .sim import REGRESSION_TOLERANCE, golden_set, node_means

_LOCK = threading.Lock()
_CHANGES: dict[str, dict] = {}
_COUNTER = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def active_deltas(graph: Graph) -> dict[str, float]:
    """Net effect of approved changes on each producer node's mean quality."""
    prods = [n.id for n in graph.nodes if n.role == "producer"]
    out = {p: 0.0 for p in prods}
    for c in _CHANGES.values():
        if c["status"] != "approved" or c["graph"] != graph.name:
            continue
        for p in prods:
            out[p] += c["node_delta"] if p == c["node_id"] else c["collateral"]
    return out


def list_changes() -> list[dict]:
    with _LOCK:
        return list(_CHANGES.values())


def propose(graph: Graph, node_id: str, kind: str, target: str, note: str,
            node_delta: float, collateral: float = 0.0) -> dict:
    """Test a proposed persistent change on the golden set. Never applies it."""
    global _COUNTER
    if target in PROTECTED_TARGETS:
        raise PermissionError(f"'{target}' is an immutable control; the healer cannot write to it")
    node = next((n for n in graph.nodes if n.id == node_id), None)
    if node is None or node.role != "producer":
        raise ValueError("a persistent change must target a producer node in this graph")
    cases = golden_set()
    with _LOCK:
        base_d = active_deltas(graph)
        cand_d = dict(base_d)
        for p in cand_d:
            cand_d[p] += node_delta if p == node_id else collateral
        before = node_means(graph, cases, base_d, {})
        after = node_means(graph, cases, cand_d, {})
        lift = round(after[node_id] - before[node_id], 4)
        worst_other = round(max([before[p] - after[p] for p in before if p != node_id] + [0.0]), 4)
        regressed = worst_other > REGRESSION_TOLERANCE
        _COUNTER += 1
        cid = f"chg-{_COUNTER:03d}"
        _CHANGES[cid] = {
            "id": cid, "graph": graph.name, "node_id": node_id, "kind": kind, "target": target,
            "note": note, "node_delta": node_delta, "collateral": collateral,
            "status": "rejected_regression" if regressed else "proposed",
            "golden_before": before, "golden_after": after,
            "target_lift": lift, "worst_other_drop": worst_other,
            "tolerance": REGRESSION_TOLERANCE, "created": _now(),
            "history": [{"at": _now(), "event": "proposed"}],
        }
        return _CHANGES[cid]


def approve(change_id: str, approver: str) -> dict:
    with _LOCK:
        c = _CHANGES.get(change_id)
        if c is None:
            raise KeyError(change_id)
        if c["status"] != "proposed":
            raise ValueError(f"cannot approve a change that is {c['status']}")
        if not approver.strip():
            raise ValueError("an approver name is required")
        c["status"] = "approved"
        c["history"].append({"at": _now(), "event": "approved", "by": approver})
        return c


def rollback(change_id: str, by: str) -> dict:
    with _LOCK:
        c = _CHANGES.get(change_id)
        if c is None:
            raise KeyError(change_id)
        if c["status"] != "approved":
            raise ValueError(f"only approved changes can be rolled back (this one is {c['status']})")
        c["status"] = "rolled_back"
        c["history"].append({"at": _now(), "event": "rolled_back", "by": by})
        return c


def reset() -> None:
    global _COUNTER
    with _LOCK:
        _CHANGES.clear()
        _COUNTER = 0


def topology_proposals(graph: Graph) -> dict:
    """Level 5: suggestions derived from lint findings. Proposal only, never applied."""
    advice = {
        1: "Add a verifier after each unchecked producer.",
        2: "Run the verifier on a different model family and remove shared scratchpad context.",
        3: "Give every loop a stop condition, an iteration cap and a budget.",
        4: "Declare a context allowlist for each node and trim edges to it.",
        5: "Insert a human gate before every prohibited-tier action.",
        6: "Give heal policies an attempt limit and an escalation target, and remove writes to controls.",
    }
    report = lint(graph)
    items = [{"rule": r.id, "name": r.name, "suggestion": advice[r.id],
              "evidence": [f.message for f in r.findings], "applied": False}
             for r in report.rules if r.status == "fail"]
    return {"graph": graph.name, "level": 5, "applied": False, "proposals": items,
            "note": "Topology rewrites are suggestions for a human to make. The lab never applies them."}
