# Graph Governance Lab

Checks a multi-agent graph against governance rules before it runs, records every handoff as an auditable receipt, and lets the graph repair itself only inside limits it cannot change.

All companies, claims and data in this repo are fictional (Meridian Insurance Group).

## Status

Phase 1 (lint engine and schema) is in place. Later phases are gated by assumption tests from the Phase 0 PRD.

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Discovery, opportunity solution tree, evals-first PRD | Done |
| 1 | Graph schema, six lint rules, Meridian graph, seeded-defect tests | Backend done; Lovable canvas next |
| 2 | Runner, receipts, eval harness, independent judge | Not started |
| 3 | Self-healing levels 1-2, threshold bands | Not started |
| 4 | Self-healing levels 3-4, approval and rollback | Not started |
| 5 | Bad-vs-good graph demo, metrics, content | Not started |

## The six lint rules

1. **Verifier downstream:** every producer has a verifier after it that lists it in `verifies`.
2. **Independent verifier:** a verifier shares neither model family nor private context with what it checks.
3. **Loop bounds:** every cycle has a stop condition, an iteration cap and a budget cap.
4. **Context allowlist:** every node declares an allowlist and only receives context inside it.
5. **Human gate:** no path reaches a prohibited-tier action without passing a human gate.
6. **Heal policy:** attempts and budget are bounded, escalation goes to a gate, and the policy cannot write to gates, thresholds, lint rules or heal policies.

## Run it

```bash
pip install -r requirements.txt
pytest -q                                   # 18 tests
python -m app.cli graphs/*.json             # lint every example graph
uvicorn app.main:app --reload               # API on :8000
```

API: `POST /lint` (graph JSON in, report out), `GET /rules`, `GET /examples`, `GET /examples/{name}/lint`.

## Assumption test 4

Lint rules can be checked from the graph definition alone. `graphs/` holds one clean Meridian claims-triage graph and six variants, each with one seeded defect. `tests/test_lint.py` requires the clean graph to pass every rule and each variant to fail on exactly its own rule. Regenerate the graphs with `python scripts/build_examples.py`.

## Layout

```
app/        schema (models.py), lint rules (lint.py), API (main.py), CLI (cli.py)
graphs/     example graphs as JSON
scripts/    example graph generator
tests/      pytest suite
```
