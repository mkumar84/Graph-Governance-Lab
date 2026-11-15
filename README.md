# Graph Governance Lab

Checks a multi-agent graph against governance rules before it runs, records every handoff as an auditable receipt, and lets the graph repair itself only inside limits it cannot change.

All companies, claims and data in this repo are fictional (Meridian Insurance Group).

## Status

Phases 1 to 4 are built in the backend. Agents are **simulated** (no model is called), so every score here comes from the simulation assumptions in `app/sim.py`, not from a real model. What the lab proves is the governance mechanics. Swap `producer_score` and `verifier_flags` for real model calls to measure real models.

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Discovery, opportunity solution tree, evals-first PRD | Done |
| 1 | Graph schema, six lint rules, Meridian graph, seeded-defect tests | Done (canvas live) |
| 2 | Runner, hash-chained receipts, golden set, judge experiment | Done (simulated agents) |
| 3 | Self-healing levels 1-3, quality bands, heal-rate alert | Done (simulated agents) |
| 4 | Level 4 persistent change with golden-set test, approval, rollback; level 5 proposals | Done (in-memory state) |
| 5 | Bad-vs-good demo, metrics, content | In progress |

## Live demo

- Canvas (Lovable): https://graph-gov-lab.lovable.app
- API (Railway): https://api-production-4f960576.up.railway.app

The API accepts browser requests only from the canvas origins listed in `app/main.py`. Add more with the `CORS_ORIGINS` environment variable (comma-separated).

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
pytest -q                                   # 39 tests
python -m app.cli graphs/*.json             # lint every example graph
uvicorn app.main:app --reload               # API on :8000
```

API:

| Endpoint | What it does |
| --- | --- |
| `POST /lint`, `GET /rules`, `GET /examples[/{name}[/lint]]` | Phase 1 lint |
| `POST /run` | Simulated run with receipts. A graph that fails lint gets 409 unless `override_lint` is set (demo only) |
| `GET /cases` | Golden set (30, held out from the healer) and dev set (40) |
| `POST /metrics` | First-pass, heal-success and escalation rates, per-node heal rate, heal-rate alerts. Accepts `faults` to seed an upstream defect |
| `GET /evals/judge` | Assumption test 1: independent vs same-family judge catch rate on planted defects (simulated) |
| `POST /evals/agreement` | Assumption test 2: judge-vs-human agreement and kappa from real labels |
| `POST /heal/propose`, `/heal/changes/{id}/approve`, `/heal/changes/{id}/rollback`, `GET /heal/changes` | Level 4 persistent change. Tested on the golden set, needs a named approver, reversible. Writes to gates, thresholds, lint rules or heal policy get 403 |
| `POST /heal/topology` | Level 5 suggestions from lint findings. Never applied |

## Assumption test 4

Lint rules can be checked from the graph definition alone. `graphs/` holds one clean Meridian claims-triage graph and six variants, each with one seeded defect. `tests/test_lint.py` requires the clean graph to pass every rule and each variant to fail on exactly its own rule. Regenerate the graphs with `python scripts/build_examples.py`.

## Layout

```
app/        schema (models.py), lint (lint.py), simulator/runner/healing (sim.py), levels 4-5 (changes.py), API (main.py), CLI (cli.py)
graphs/     example graphs as JSON
scripts/    example graph generator
tests/      pytest suite
```
