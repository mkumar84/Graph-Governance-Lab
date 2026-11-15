"""Simulated runner, receipts, healing (levels 1-3) and eval harness.

IMPORTANT: no real model is called. Agents are deterministic simulations, so
every number here is a property of the *simulation assumptions* below, not of
any real model. What it proves is the governance mechanics: lint gating, quality
bands, bounded healing, hash-chained receipts, held-out golden set. Swap
`producer_score` / `verifier_flags` for real calls to measure real models.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Optional

from .lint import lint
from .models import MAX_HEAL_ATTEMPTS, Graph, Node

# ------------------------------------------------------ simulation assumptions
BASE_QUALITY = 0.95          # mean producer score on a clean case
DEFECT_PENALTY = 0.30        # planted defects lower the score by this much
NOISE_SD = 0.06
INDEPENDENT_DETECT = 0.90    # verifier catches a bad output (independent family)
SAME_FAMILY_DETECT = 0.40    # shared blind spots (same family / shared scratchpad)
FALSE_ALARM = 0.05
LEVEL_GAIN = {1: 0.08, 2: 0.14, 3: 0.20}   # mean score lift per heal level
FAULT_GAIN_FACTOR = 0.5      # heals are half as effective against a systematic fault
HEAL_COST_USD = 0.25
MIN_LIFT = 0.03              # two heals below this lift in a row -> escalate
HEAL_RATE_ALERT = 0.35
REGRESSION_TOLERANCE = 0.02

DEFECTS = ["missed_fraud_flag", "fabricated_source", "wrong_amount", "stale_policy",
           "prompt_injection", "pii_leak", "contradiction", "wrong_jurisdiction",
           "duplicate_claim_missed", "bad_date"]


@dataclass(frozen=True)
class Case:
    id: str
    defect: Optional[str]
    refer: bool  # simulated human label: should this claim be referred?


def _rng(*parts) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def golden_set() -> list[Case]:
    """30 labelled cases, 10 with a planted defect. Held out from the healer."""
    out = []
    for i in range(1, 31):
        defect = DEFECTS[(i // 3) - 1] if i % 3 == 0 else None
        out.append(Case(f"g{i:02d}", defect, _rng("label", i).random() < 0.4))
    return out


def dev_set() -> list[Case]:
    """40 cases the healer and batch metrics may use. Disjoint from the golden set."""
    out = []
    for i in range(1, 41):
        defect = DEFECTS[i % len(DEFECTS)] if i % 4 == 0 else None
        out.append(Case(f"d{i:02d}", defect, _rng("dlabel", i).random() < 0.4))
    return out


def find_case(case_id: str) -> Case:
    for c in golden_set() + dev_set():
        if c.id == case_id:
            return c
    raise KeyError(case_id)


# ----------------------------------------------------------------- simulated agents
def producer_score(node_id: str, case: Case, seed: int, deltas: dict, faults: dict,
                   attempt: int = 0) -> float:
    base = BASE_QUALITY + deltas.get(node_id, 0.0) - faults.get(node_id, 0.0)
    if case.defect:
        base -= DEFECT_PENALTY
    return _clip(base + _rng(seed, case.id, node_id, attempt).gauss(0, NOISE_SD))


def is_independent(verifier: Node, producer: Node) -> bool:
    """Mirror of lint rule 2."""
    vf, pf = verifier.model_family, producer.model_family
    if vf is None or pf is None or vf == pf:
        return False
    return f"scratchpad:{producer.id}" not in (verifier.context_allowlist or [])


def verifier_flags(verifier: Node, producer: Node, bad: bool, case: Case, seed: int) -> bool:
    r = _rng(seed, case.id, verifier.id, producer.id, "flag").random()
    if not bad:
        return r < FALSE_ALARM
    return r < (INDEPENDENT_DETECT if is_independent(verifier, producer) else SAME_FAMILY_DETECT)


# ------------------------------------------------------------------- receipts
class Ledger:
    """Hash-chained receipts: editing any earlier receipt breaks every later hash."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, node: str, kind: str, **data) -> None:
        prev = self.items[-1]["hash"] if self.items else "genesis"
        body = {"seq": len(self.items) + 1, "node": node, "kind": kind, **data, "prev": prev}
        body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        self.items.append(body)


def verify_chain(receipts: list[dict]) -> bool:
    prev = "genesis"
    for r in receipts:
        body = {k: v for k, v in r.items() if k != "hash"}
        if body["prev"] != prev:
            return False
        if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16] != r["hash"]:
            return False
        prev = r["hash"]
    return True


# ------------------------------------------------------------------- ordering
def topo_order(graph: Graph) -> list[str]:
    """Reverse postorder; edges that close a cycle (loop back-edges) are ignored."""
    adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        adj[e.source].append(e.target)
    has_in = {e.target for e in graph.edges}
    seen: set[str] = set()
    post: list[str] = []

    def dfs(u: str) -> None:
        seen.add(u)
        for v in adj[u]:
            if v not in seen:
                dfs(v)
        post.append(u)

    for n in graph.nodes:
        if n.id not in has_in and n.id not in seen:
            dfs(n.id)
    for n in graph.nodes:
        if n.id not in seen:
            dfs(n.id)
    return list(reversed(post))


def ungated_nodes(graph: Graph) -> set[str]:
    """Nodes reachable from the sources without passing through a gate."""
    adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        adj[e.source].append(e.target)
    has_in = {e.target for e in graph.edges}
    stack = [n.id for n in graph.nodes if n.id not in has_in and n.role != "gate"]
    seen: set[str] = set()
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for v in adj[u]:
            if graph.node(v).role != "gate":
                stack.append(v)
    return seen


# --------------------------------------------------------------------- healing
def _ladder(node: Node) -> list[int]:
    ladder = [1]
    allowed = set(node.heal_policy.can_write) if node.heal_policy else set()
    if "prompt_patch" in allowed:
        ladder.append(2)
    if "route" in allowed:
        ladder.append(3)
    return ladder


_DIFFS = {
    1: "returned verifier critique to producer (no prompt change)",
    2: "temporary prompt patch for this run only: cite a source line for every flag",
    3: "route repair: re-ran on a different model family",
}


def heal_node(node: Node, case: Case, seed: int, start: float, faults: dict,
              ledger: Ledger) -> tuple[float, str]:
    """Run bounded heal attempts. Returns (final score, status)."""
    pol = node.heal_policy
    envelope = node.envelope
    ladder = _ladder(node)
    score, spent, weak_streak = start, 0.0, 0
    cap = min(pol.max_attempts, MAX_HEAL_ATTEMPTS)
    for attempt in range(1, cap + 1):
        if spent + HEAL_COST_USD > pol.budget_usd:
            ledger.add(node.id, "heal_stop", reason="budget cap", spent_usd=round(spent, 2))
            return score, "exhausted"
        level = ladder[min(attempt - 1, len(ladder) - 1)]
        r = _rng(seed, case.id, node.id, "heal", attempt)
        factor = FAULT_GAIN_FACTOR if faults.get(node.id) else 1.0
        new = _clip(score + LEVEL_GAIN[level] * factor + r.gauss(0, 0.04))
        spent += HEAL_COST_USD
        ledger.add(node.id, "heal", level=level, attempt=attempt, before=round(score, 3),
                   after=round(new, 3), diff=_DIFFS[level], spent_usd=round(spent, 2))
        weak_streak = weak_streak + 1 if new - score < MIN_LIFT else 0
        score = new
        if score >= envelope.pass_at:
            return score, "healed"
        if weak_streak >= 2:
            ledger.add(node.id, "heal_stop", reason="two heals below minimum lift")
            return score, "escalated"
    return score, "exhausted"


# ------------------------------------------------------------------------ run
def run_graph(graph: Graph, case: Case, seed: int = 0, approve: bool = False,
              deltas: Optional[dict] = None, faults: Optional[dict] = None,
              heal: bool = True) -> dict:
    deltas, faults = deltas or {}, faults or {}
    ledger = Ledger()
    scores: dict[str, float] = {}
    final: dict[str, float] = {}
    heals: dict[str, str] = {}
    escapes: list[str] = []
    outcome = "completed"
    gate_passed = False
    open_paths = ungated_nodes(graph)
    ledger.add("-", "run_start", graph=graph.name, case=case.id, seed=seed)

    for nid in topo_order(graph):
        node = graph.node(nid)
        if node.role == "producer":
            s = producer_score(nid, case, seed, deltas, faults)
            scores[nid] = final[nid] = s
            env = node.envelope
            band = "pass" if s >= env.pass_at else ("heal" if s >= env.escalate_below else "escalate")
            ledger.add(nid, "score", score=round(s, 3), band=band)
            if band == "heal" and heal and node.heal_policy and node.heal_policy.max_attempts > 0:
                s, status = heal_node(node, case, seed, s, faults, ledger)
                final[nid] = s
                heals[nid] = status
                if status != "healed":
                    outcome = "escalated"
                    ledger.add(nid, "escalate", to=node.heal_policy.escalate_to or "human_gate",
                               score=round(s, 3))
                    break
            elif band == "escalate":
                outcome = "escalated"
                ledger.add(nid, "escalate", to=(node.heal_policy.escalate_to if node.heal_policy
                                                 else "human_gate"), score=round(s, 3))
                break
        elif node.role == "verifier":
            for pid in node.verifies:
                if pid not in final:
                    continue
                prod = graph.node(pid)
                bad = final[pid] < prod.envelope.pass_at
                flagged = verifier_flags(node, prod, bad, case, seed)
                ledger.add(nid, "verify", target=pid, output_bad=bad, flagged=flagged,
                           independent=is_independent(node, prod))
                if flagged and bad:
                    outcome = "escalated"
                    ledger.add(nid, "escalate", to="human_gate", reason="verifier flagged output")
                    break
            if outcome == "escalated":
                break
        elif node.role == "gate":
            if approve:
                gate_passed = True
                ledger.add(nid, "gate", decision="approved")
            else:
                outcome = "awaiting_human"
                ledger.add(nid, "gate", decision="pending")
        elif node.role == "action":
            ungated = node.action_tier == "prohibited" and nid in open_paths
            if gate_passed or ungated or node.action_tier != "prohibited":
                ledger.add(nid, "action", executed=True, tier=node.action_tier, ungated=ungated)
                outcome = "executed_ungated" if ungated else "executed"
            else:
                ledger.add(nid, "action", executed=False, tier=node.action_tier,
                           reason="blocked until human gate approves")

    # Bad outputs nobody checked: a producer below pass band, not healed, not flagged.
    verified = {pid for n in graph.nodes if n.role == "verifier" for pid in n.verifies}
    for pid, s in final.items():
        if s < graph.node(pid).envelope.pass_at and outcome not in ("escalated",):
            escapes.append(pid)
    for pid in escapes:
        ledger.add(pid, "escape", verified=pid in verified,
                   note="below-band output passed downstream")
    ledger.add("-", "run_end", outcome=outcome)
    return {"graph": graph.name, "case": case.id, "outcome": outcome,
            "scores": {k: round(v, 3) for k, v in final.items()},
            "first_scores": {k: round(v, 3) for k, v in scores.items()},
            "heals": heals, "escaped": escapes, "receipts": ledger.items,
            "chain_valid": verify_chain(ledger.items)}


# ---------------------------------------------------------------------- evals
def judge_experiment(trials: int = 200) -> dict:
    """Assumption test 1 (simulated): planted-defect catch rate, independent vs same family."""
    planted = [c for c in golden_set() if c.defect]
    out = {}
    for label, p in (("same_family", SAME_FAMILY_DETECT), ("independent", INDEPENDENT_DETECT)):
        caught = 0
        for t in range(trials):
            for c in planted:
                caught += _rng("judge", label, t, c.id).random() < p
        out[label] = round(caught / (trials * len(planted)), 3)
    return {"planted_defects": len(planted), "trials": trials, "catch_rate": out,
            "independent_beats_same_family": out["independent"] > out["same_family"],
            "caveat": "Simulated: detection probabilities are assumptions, not model measurements."}


def agreement(human: list[bool], judge_scores: list[float], threshold: float = 0.85) -> dict:
    """Assumption test 2: pass/fail agreement and Cohen's kappa between judge and human."""
    if len(human) != len(judge_scores) or not human:
        raise ValueError("human labels and judge scores must be equal-length and non-empty")
    j = [s >= threshold for s in judge_scores]
    n = len(human)
    po = sum(a == b for a, b in zip(human, j)) / n
    ph, pj = sum(human) / n, sum(j) / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    kappa = 1.0 if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "agreement": round(po, 3), "kappa": round(kappa, 3), "threshold": threshold}


def node_means(graph: Graph, cases: list[Case], deltas: dict, faults: dict, seed: int = 0) -> dict:
    prods = [n.id for n in graph.nodes if n.role == "producer"]
    return {p: round(sum(producer_score(p, c, seed, deltas, faults) for c in cases) / len(cases), 4)
            for p in prods}


def batch_metrics(graph: Graph, faults: Optional[dict] = None, seed: int = 0,
                  deltas: Optional[dict] = None) -> dict:
    """Run the dev set with healing; report the five PRD metrics plus heal-rate alerts."""
    faults = faults or {}
    runs = [run_graph(graph, c, seed, approve=True, deltas=deltas, faults=faults)
            for c in dev_set()]
    n = len(runs)
    per: dict[str, dict] = {}
    for node in graph.nodes:
        if node.role != "producer":
            continue
        healed = sum(1 for r in runs if node.id in r["heals"])
        ok = sum(1 for r in runs if r["heals"].get(node.id) == "healed")
        per[node.id] = {"runs": n, "heals": healed, "heal_rate": round(healed / n, 3),
                        "heal_success": round(ok / healed, 3) if healed else None}
    heals_total = sum(v["heals"] for v in per.values())
    heals_ok = sum(1 for r in runs for s in r["heals"].values() if s == "healed")
    first_pass = sum(1 for r in runs if not r["heals"] and r["outcome"] != "escalated")
    escalated = sum(1 for r in runs if r["outcome"] == "escalated")
    alerts = [{"node": k, "heal_rate": v["heal_rate"],
               "message": "Heal rate above alert level: investigate upstream, do not just heal."}
              for k, v in per.items() if v["heal_rate"] > HEAL_RATE_ALERT]
    return {"graph": graph.name, "cases": n, "faults": faults,
            "first_pass_rate": round(first_pass / n, 3),
            "heal_success_rate": round(heals_ok / heals_total, 3) if heals_total else None,
            "escalation_rate": round(escalated / n, 3),
            "per_node": per, "alerts": alerts,
            "lint_passed": lint(graph).passed}
