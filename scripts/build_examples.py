"""Regenerate graphs/*.json: one clean Meridian claims-triage graph and one
seeded-defect variant per lint rule. Run: python scripts/build_examples.py
"""
import copy
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "graphs"

CLEAN = {
    "name": "meridian_claims_triage_clean",
    "description": (
        "Fictional Meridian Insurance Group claims triage. Producers draft, "
        "verifiers on a different model family check, a human gate precedes "
        "any prohibited-tier action."
    ),
    "nodes": [
        {"id": "intake", "name": "Intake", "role": "producer", "model_family": "family_a",
         "context_allowlist": ["claim_documents"]},
        {"id": "extractor", "name": "Signal extractor", "role": "producer", "model_family": "family_a",
         "context_allowlist": ["claim_facts"],
         "heal_policy": {"max_attempts": 2, "budget_usd": 1.0, "escalate_to": "human_gate",
                         "can_write": ["prompt_patch"]}},
        {"id": "fact_check", "name": "Fact check", "role": "verifier", "model_family": "family_b",
         "verifies": ["intake", "extractor"], "context_allowlist": ["claim_facts", "flags"]},
        {"id": "skeptic", "name": "Skeptic", "role": "producer", "model_family": "family_a",
         "context_allowlist": ["claim_facts", "flags", "judge_feedback"],
         "heal_policy": {"max_attempts": 2, "budget_usd": 1.0, "escalate_to": "human_gate",
                         "can_write": ["prompt_patch"]}},
        {"id": "advocate", "name": "Advocate", "role": "producer", "model_family": "family_a",
         "context_allowlist": ["claim_facts", "flags"]},
        {"id": "judge", "name": "Independent judge", "role": "verifier", "model_family": "family_b",
         "verifies": ["skeptic", "advocate"],
         "context_allowlist": ["skeptic_argument", "advocate_argument", "flags"]},
        {"id": "human_gate", "name": "Analyst gate", "role": "gate",
         "context_allowlist": ["judge_ruling"]},
        {"id": "refer_siu", "name": "Refer to investigations", "role": "action",
         "action_tier": "prohibited", "context_allowlist": ["judge_ruling"]},
    ],
    "edges": [
        {"source": "intake", "target": "extractor", "context": ["claim_facts"]},
        {"source": "extractor", "target": "fact_check", "context": ["claim_facts", "flags"]},
        {"source": "fact_check", "target": "skeptic", "context": ["claim_facts", "flags"]},
        {"source": "fact_check", "target": "advocate", "context": ["claim_facts", "flags"]},
        {"source": "skeptic", "target": "judge", "context": ["skeptic_argument"]},
        {"source": "advocate", "target": "judge", "context": ["advocate_argument"]},
        {"source": "judge", "target": "skeptic", "context": ["judge_feedback"]},
        {"source": "judge", "target": "human_gate", "context": ["judge_ruling"]},
        {"source": "human_gate", "target": "refer_siu", "context": ["judge_ruling"]},
    ],
    "loops": [
        {"nodes": ["skeptic", "judge"], "stop_condition": "judge score >= 0.85",
         "max_iterations": 3, "budget_usd": 2.0},
    ],
}


def variant(name, description, mutate):
    g = copy.deepcopy(CLEAN)
    g["name"] = name
    g["description"] = description
    mutate(g)
    return g


def node(g, node_id):
    return next(n for n in g["nodes"] if n["id"] == node_id)


def m1(g):  # rule 1: advocate loses its verifier
    node(g, "judge")["verifies"] = ["skeptic"]


def m2(g):  # rule 2: judge moves to the producers' model family
    node(g, "judge")["model_family"] = "family_a"


def m3(g):  # rule 3: the retry loop loses its spec
    g["loops"] = []


def m4(g):  # rule 4: a PII key leaks to a node that should not see it
    for e in g["edges"]:
        if e["source"] == "fact_check" and e["target"] == "advocate":
            e["context"].append("claimant_pii")


def m5(g):  # rule 5: the gate is bypassed on the way to the prohibited action
    g["edges"].append({"source": "judge", "target": "refer_siu", "context": ["judge_ruling"]})


def m6(g):  # rule 6: a heal policy that can rewrite thresholds
    node(g, "extractor")["heal_policy"]["can_write"] = ["prompt_patch", "thresholds"]


EXAMPLES = [
    CLEAN,
    variant("seeded_1_no_verifier",
            "Advocate has no verifier downstream.", m1),
    variant("seeded_2_same_family_judge",
            "Independent judge runs on the same model family as the producers.", m2),
    variant("seeded_3_unbounded_loop",
            "Skeptic and judge loop with no stop condition or budget.", m3),
    variant("seeded_4_context_leak",
            "Claimant PII is passed to the advocate, outside its allowlist.", m4),
    variant("seeded_5_gate_bypass",
            "The judge can trigger a prohibited referral without the analyst gate.", m5),
    variant("seeded_6_heal_writes_thresholds",
            "The extractor heal policy may rewrite thresholds.", m6),
]

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for g in EXAMPLES:
        (OUT / f"{g['name']}.json").write_text(json.dumps(g, indent=2) + "\n")
    print(f"wrote {len(EXAMPLES)} graphs to {OUT}")
