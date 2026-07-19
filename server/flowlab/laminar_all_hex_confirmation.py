"""Identical-input confirmation runner for rejected all-hex campaign cells."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    ResourceBudget,
    WeightedScheduler,
    _canonical_sha256,
    _sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    compile_traction_utility,
)


SCHEMA = "flowlab.laminar-all-hex-confirmation.v1"
_ROOT = Path(__file__).resolve().parents[2]
_METRICS = (
    "finalAxialInitialResidual",
    "finalPressureInitialResidual",
    "finalLinearResidual",
    "massRelativeImbalance",
    "velocityRelativeL2Error",
    "pressureRelativeL2Error",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def archived_sources_match(campaign: Path) -> bool:
    register = _load_json(campaign / "source-register.json")
    return all(
        row["exists"] is True
        and (_ROOT / row["path"]).is_file()
        and _sha256(_ROOT / row["path"]) == row["sha256"]
        for row in register["records"]
    )


def _relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1e-300)


def compare_confirmation(
    original: dict[str, Any], confirmation: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {}
    for metric in _METRICS:
        left = float(original["observation"][metric])
        right = float(confirmation["observation"][metric])
        comparisons[metric] = {
            "original": left,
            "confirmation": right,
            "relativeDifference": _relative_difference(left, right),
            "withinTolerance": math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-14),
        }
    checks = {
        "sameScientificStatus": original["status"] == confirmation["status"],
        "sameFailedChecks": original["failedChecks"] == confirmation["failedChecks"],
        "numericSignatureMatches": all(
            row["withinTolerance"] for row in comparisons.values()
        ),
    }
    return {
        "sourceCellId": original["cellId"],
        "confirmationCellId": confirmation["cellId"],
        "status": "confirmed" if all(checks.values()) else "not-confirmed",
        "checks": checks,
        "metrics": comparisons,
    }


def run_confirmation(campaign: Path, output: Path, capacity: int) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    if not archived_sources_match(campaign):
        raise ValueError("workspace source hashes no longer match the archived campaign")
    manifest = _load_json(campaign / "campaign-manifest.json")
    original_reports = {
        path.parent.name: _load_json(path)
        for path in (campaign / "cells").glob("physical*/result.json")
    }
    rejected = [
        cell
        for cell in manifest["cells"]
        if cell["cellId"] in original_reports
        and original_reports[cell["cellId"]]["status"] == "rejected-scientific"
    ]
    if not rejected:
        raise ValueError("source campaign contains no rejected scientific cells")
    cells = []
    source_by_confirmation = {}
    for source in rejected:
        clone = json.loads(json.dumps(source))
        source_id = source["cellId"]
        clone["cellId"] = f"confirmation__{source_id}"
        clone["sourceCellId"] = source_id
        cells.append(clone)
        source_by_confirmation[clone["cellId"]] = source_id
    output.mkdir(parents=True, exist_ok=True)
    confirmation_manifest = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "sourceCampaign": str(campaign),
        "sourceManifestSha256": _canonical_sha256(manifest),
        "method": "identical scientific parameters in fresh cases; no changed gates or solver inputs",
        "cells": cells,
    }
    _write_json(output / "confirmation-manifest.json", confirmation_manifest)
    utility = compile_traction_utility(output)
    budget = ResourceBudget.discover(capacity)
    results = WeightedScheduler(
        budget, output / "execution-events.jsonl"
    ).run(cells, DockerScientificWorker(output, threading.Lock()))
    comparisons = []
    for result in results:
        confirmation = _load_json(output / result["result"])
        source_id = source_by_confirmation[result["cellId"]]
        comparisons.append(
            compare_confirmation(original_reports[source_id], confirmation)
        )
    checks = {
        "archivedSourcesMatchedBeforeExecution": True,
        "allRejectedCellsRepeated": len(comparisons) == len(rejected),
        "allFailureSignaturesConfirmed": bool(comparisons)
        and all(row["status"] == "confirmed" for row in comparisons),
        "noInfrastructureGaps": all(
            result["status"] != "incomplete-infrastructure" for result in results
        ),
    }
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "accepted-confirmation" if all(checks.values()) else "rejected-confirmation",
        "checks": checks,
        "utility": utility,
        "comparisons": comparisons,
        "promotionAuthorized": False,
        "nextStage": "one-change-iteration-budget-diagnostic" if all(checks.values()) else "confirmation-investigation",
    }
    _write_json(output / "confirmation-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = run_confirmation(
        args.campaign.resolve(), args.output.resolve(), args.capacity
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted-confirmation" else 2


if __name__ == "__main__":
    raise SystemExit(main())
