"""Repair an infrastructure-interrupted common-floor cell without rerunning valid cells."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    ResourceBudget,
    WeightedScheduler,
    _sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    _aggregate_physical,
    compile_traction_utility,
)


SCHEMA = "flowlab.laminar-all-hex-common-floor-repair.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repair(source: Path, output: Path, capacity: int) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    manifest = _load(source / "diagnostic-manifest.json")
    source_reports = {
        path.parent.name: _load(path)
        for path in sorted((source / "cells").glob("*/result.json"))
    }
    failed_ids = [
        cell_id
        for cell_id, report in source_reports.items()
        if report.get("status") != "accepted"
    ]
    if len(source_reports) != 12 or len(failed_ids) != 1:
        raise ValueError("repair requires exactly 12 source reports and one failed cell")
    failed_id = failed_ids[0]
    cells = [cell for cell in manifest["cells"] if cell["cellId"] == failed_id]
    if len(cells) != 1:
        raise ValueError("failed cell is not unique in the diagnostic manifest")

    output.mkdir(parents=True, exist_ok=True)
    utility = compile_traction_utility(output)
    results = WeightedScheduler(
        ResourceBudget.discover(capacity), output / "execution-events.jsonl"
    ).run(cells, DockerScientificWorker(output, threading.Lock()))
    if len(results) != 1 or not results[0].get("result"):
        raise ValueError("repair worker did not produce one result")
    repeated = _load(output / results[0]["result"])
    combined = [
        report
        for cell_id, report in source_reports.items()
        if cell_id != failed_id
    ] + [repeated]
    physical = _aggregate_physical(combined) if repeated.get("status") == "accepted" else None
    groups = physical.get("groups", []) if physical else []
    checks = {
        "exactlyOneSourceFailureSelected": True,
        "identicalCellManifestReused": cells[0] == next(
            cell for cell in manifest["cells"] if cell["cellId"] == failed_id
        ),
        "repairCellAccepted": repeated.get("status") == "accepted",
        "allTwelveCombinedCellsAccepted": len(combined) == 12
        and all(row.get("status") == "accepted" for row in combined),
        "allFourGroupsPassExistingGciGates": len(groups) == 4
        and all(row.get("status") == "accepted" for row in groups),
        "noRepairInfrastructureGap": results[0].get("status")
        != "incomplete-infrastructure",
    }
    report = {
        "schema": SCHEMA,
        "campaignId": manifest["campaignId"],
        "status": "supports-common-floor" if all(checks.values()) else "common-floor-not-supported",
        "checks": checks,
        "sourceDiagnostic": str(source),
        "sourceDiagnosticManifestSha256": _sha256(source / "diagnostic-manifest.json"),
        "repairedCellId": failed_id,
        "sourceFailure": {
            "status": source_reports[failed_id]["status"],
            "failedChecks": source_reports[failed_id]["failedChecks"],
            "solverExitCode": source_reports[failed_id]["observation"]["solverExitCode"],
            "classification": "transient-storage-io-under-test",
        },
        "repairResult": results[0],
        "physicalAggregation": physical,
        "utility": utility,
        "interpretationBoundary": (
            "An accepted identical rerun supports transient storage I/O as the "
            "original failure; this diagnostic can justify a new campaign but "
            "cannot alter or promote v3."
        ),
        "promotionAuthorized": False,
    }
    _write_json(output / "repair-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = repair(args.source.resolve(), args.output.resolve(), args.capacity)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "supports-common-floor" else 2


if __name__ == "__main__":
    raise SystemExit(main())
