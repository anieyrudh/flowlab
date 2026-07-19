"""One-change nonlinear-iteration diagnostic for confirmed campaign failures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    ResourceBudget,
    WeightedScheduler,
    _canonical_sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    compile_traction_utility,
)


SCHEMA = "flowlab.laminar-all-hex-iteration-diagnostic.v1"
BASELINE_ITERATIONS = 1000
DIAGNOSTIC_ITERATIONS = 1250


def residual_history(log_path: Path) -> dict[str, Any]:
    current: int | None = None
    rows: dict[int, dict[str, float]] = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"Time = (\d+)s", line)
        if match:
            current = int(match.group(1))
            rows.setdefault(current, {})
            continue
        match = re.search(
            r"Solving for (Ux|p), Initial residual = ([0-9.eE+-]+)", line
        )
        if match and current is not None:
            rows[current][match.group(1)] = float(match.group(2))
    ordered = [
        {"iteration": iteration, **values}
        for iteration, values in sorted(rows.items())
        if {"Ux", "p"}.issubset(values)
    ]

    def sustained(field: str, limit: float) -> int | None:
        first = None
        for row in reversed(ordered):
            if row[field] <= limit:
                first = row["iteration"]
            else:
                break
        return first

    checkpoints = {1, 100, 250, 500, 750, 1000, 1100, 1150, 1200, 1250}
    return {
        "sampled": [row for row in ordered if row["iteration"] in checkpoints],
        "firstSustainedAxialPassIteration": sustained("Ux", 1.0e-6),
        "firstSustainedPressurePassIteration": sustained("p", 1.0e-8),
        "final": ordered[-1] if ordered else None,
    }


def run_diagnostic(
    campaign: Path,
    confirmation: Path,
    output: Path,
    capacity: int,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    confirmation_report = json.loads(
        (confirmation / "confirmation-report.json").read_text(encoding="utf-8")
    )
    if confirmation_report.get("status") != "accepted-confirmation":
        raise ValueError("identical-input confirmation is not accepted")
    manifest = json.loads(
        (campaign / "campaign-manifest.json").read_text(encoding="utf-8")
    )
    source_ids = {
        row["sourceCellId"] for row in confirmation_report["comparisons"]
    }
    cells = []
    source_by_diagnostic = {}
    for source in manifest["cells"]:
        if source["cellId"] not in source_ids:
            continue
        clone = json.loads(json.dumps(source))
        source_id = clone["cellId"]
        clone["cellId"] = f"iteration-1250__{source_id}"
        clone["sourceCellId"] = source_id
        clone["parameters"]["iterations"] = DIAGNOSTIC_ITERATIONS
        cells.append(clone)
        source_by_diagnostic[clone["cellId"]] = source_id
    output.mkdir(parents=True, exist_ok=True)
    diagnostic_manifest = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "sourceCampaign": str(campaign),
        "sourceManifestSha256": _canonical_sha256(manifest),
        "sourceConfirmation": str(confirmation),
        "oneChangedFactor": {
            "name": "nonlinearIterationBudget",
            "baseline": BASELINE_ITERATIONS,
            "diagnostic": DIAGNOSTIC_ITERATIONS,
        },
        "unchanged": [
            "mesh",
            "physics",
            "boundary conditions",
            "schemes",
            "linear solvers and tolerances",
            "relaxation",
            "analytic definitions",
            "acceptance gates",
        ],
        "cells": cells,
    }
    _write_json(output / "diagnostic-manifest.json", diagnostic_manifest)
    utility = compile_traction_utility(output)
    budget = ResourceBudget.discover(capacity)
    results = WeightedScheduler(
        budget, output / "execution-events.jsonl"
    ).run(cells, DockerScientificWorker(output, threading.Lock()))
    observations = []
    for result in results:
        report = json.loads((output / result["result"]).read_text(encoding="utf-8"))
        level = report["parameters"]["level"]
        log_path = (
            output
            / "cells"
            / report["cellId"]
            / "execution-attempt-1"
            / "run"
            / level
            / "artifacts"
            / "foamRun.log"
        )
        observations.append(
            {
                "cellId": report["cellId"],
                "sourceCellId": source_by_diagnostic[report["cellId"]],
                "status": report["status"],
                "failedChecks": report["failedChecks"],
                "finalAxialInitialResidual": report["observation"]["finalAxialInitialResidual"],
                "finalPressureInitialResidual": report["observation"]["finalPressureInitialResidual"],
                "residualHistory": residual_history(log_path),
                "result": result["result"],
            }
        )
    checks = {
        "allConfirmedFailuresRepeated": len(observations) == len(source_ids) == 6,
        "onlyIterationBudgetChanged": True,
        "allFrozenGatesNowPass": bool(observations)
        and all(row["status"] == "accepted" for row in observations),
        "noInfrastructureGaps": all(
            result["status"] != "incomplete-infrastructure" for result in results
        ),
    }
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "supports-iteration-budget-root-cause" if all(checks.values()) else "does-not-resolve-all-failures",
        "checks": checks,
        "oneChangedFactor": diagnostic_manifest["oneChangedFactor"],
        "utility": utility,
        "observations": observations,
        "interpretation": (
            "Passing this diagnostic supports insufficient nonlinear iteration budget "
            "as the immediate cause; it does not authorize changing the completed v2 campaign."
        ),
        "promotionAuthorized": False,
    }
    _write_json(output / "diagnostic-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = run_diagnostic(
        args.campaign.resolve(),
        args.confirmation.resolve(),
        args.output.resolve(),
        args.capacity,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "supports-iteration-budget-root-cause" else 2


if __name__ == "__main__":
    raise SystemExit(main())
