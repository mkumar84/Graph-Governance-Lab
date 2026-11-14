"""Lint one or more graph files: python -m app.cli graphs/*.json"""
import json
import sys

from .lint import lint
from .models import Graph


def main(paths: list[str]) -> int:
    exit_code = 0
    for path in paths:
        with open(path) as fh:
            graph = Graph.model_validate(json.load(fh))
        report = lint(graph)
        print(f"{'PASS' if report.passed else 'FAIL'}  {graph.name}")
        for r in report.rules:
            if r.status == "fail":
                exit_code = 1
                for f in r.findings:
                    print(f"   rule {r.id} {r.name}: {f.message}")
    return exit_code


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m app.cli graph.json [graph.json ...]")
    sys.exit(main(sys.argv[1:]))
