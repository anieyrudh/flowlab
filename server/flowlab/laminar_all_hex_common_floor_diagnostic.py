"""Test a common 1,300-iteration floor against the frozen physical GCI gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    ResourceBudget,
    WeightedScheduler,
    _canonical_sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    _aggregate_physical,
    compile_traction_utility,
)
from .laminar_all_hex_v3_contract import CAMPAIGN_ID


SCHEMA = "flowlab.laminar-all-hex-common-floor-diagnostic.v1"
COMMON_ITERATION_FLOOR = 1300
_SELECTED_GROUPS = {
    (4.17, "forward", 1.0, 1.0),
    (16.67, "forward", 4.0, 2.0),
    (66.7, "forward", 1.0, 1.0),
    (66.7, "forward", 1.0, 2.0),
}


def _group_key(cell: dict[str, Any]) -> tuple[Any, ...]:
    parameters = cell.get("parameters", {})
    return (
        parameters.get("reynoldsNumberHeightBased"),
        parameters.get("flowDirection"),
        parameters.get("lengthToHeightRatio"),
        parameters.get("axialCellAspectRatio"),
    )


def selected_cells(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    cells = []
    for source in manifest.get("cells", []):
        if source.get("lane") != "physical-envelope":
            continue
        if _group_key(source) not in _SELECTED_GROUPS:
            continue
        clone = json.loads(json.dumps(source))
        source_id = clone["cellId"]
        clone["cellId"] = f"common-floor-1300__{source_id}"
        clone["sourceCellId"] = source_id
        clone["parameters"]["iterations"] = COMMON_ITERATION_FLOOR
        clone["parameters"].pop("convergenceControl", None)
        cells.append(clone)
    return cells


def run_diagnostic(campaign: Path, output: Path, capacity: int) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    manifest = json.loads(
        (campaign / "campaign-manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("campaignId") != CAMPAIGN_ID:
        raise ValueError("common-floor diagnostic source is not laminar-all-hex-v3")
    cells = selected_cells(manifest)
    if len(cells) != 12:
        raise ValueError("common-floor diagnostic must contain four three-grid groups")
    output.mkdir(parents=True, exist_ok=True)
    _write_json(
        output / "diagnostic-manifest.json",
        {
            "schema": SCHEMA,
            "campaignId": CAMPAIGN_ID,
            "sourceCampaign": str(campaign),
            "sourceManifestSha256": _canonical_sha256(manifest),
            "hypothesis": (
                "A common 1,300-iteration floor makes iterative error comparable "
                "across 12/24/48 grids while retaining the unchanged residual, "
                "field, force, mesh, and GCI gates."
            ),
            "oneChangedFactor": {
                "name": "commonNonlinearIterationFloor",
                "v3Minimum": 300,
                "diagnosticMinimum": COMMON_ITERATION_FLOOR,
            },
            "selectedGroups": [list(row) for row in sorted(_SELECTED_GROUPS)],
            "cells": cells,
        },
    )
    utility = compile_traction_utility(output)
    results = WeightedScheduler(
        ResourceBudget.discover(capacity), output / "execution-events.jsonl"
    ).run(cells, DockerScientificWorker(output, threading.Lock()))
    reports = [
        json.loads((output / row["result"]).read_text(encoding="utf-8"))
        for row in results
        if row.get("result")
    ]
    physical = _aggregate_physical(reports)
    checks = {
        "fourThreeGridGroupsPresent": len(physical.get("groups", [])) == 4,
        "allTwelveCellsAccepted": len(reports) == 12
        and all(row.get("status") == "accepted" for row in reports),
        "allFourGroupsPassExistingGciGates": len(physical.get("groups", [])) == 4
        and all(row.get("status") == "accepted" for row in physical["groups"]),
        "noInfrastructureGaps": len(results) == 12
        and all(row.get("status") != "incomplete-infrastructure" for row in results),
    }
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "supports-common-floor" if all(checks.values()) else "common-floor-not-supported",
        "checks": checks,
        "commonIterationFloor": COMMON_ITERATION_FLOOR,
        "utility": utility,
        "physicalAggregation": physical,
        "executionResults": results,
        "interpretationBoundary": (
            "This representative diagnostic can justify a new campaign contract; "
            "it cannot change or promote the rejected v3 campaign."
        ),
        "promotionAuthorized": False,
    }
    _write_json(output / "diagnostic-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = run_diagnostic(
        args.campaign.resolve(), args.output.resolve(), args.capacity
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "supports-common-floor" else 2


if __name__ == "__main__":
    raise SystemExit(main())
