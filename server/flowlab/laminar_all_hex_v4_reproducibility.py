"""Cold-repeat and MPI evidence under the frozen v4 campaign contract."""
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
    _sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    compile_traction_utility,
)
from .laminar_all_hex_confirmation import compare_confirmation
from .laminar_all_hex_reproducibility import (
    SOURCE_CELL_ID,
    _run_mpi_cell,
    _workspace_source_drift,
)
from .laminar_all_hex_v3_contract import mpi_equivalence_contract
from .laminar_all_hex_v3_reproducibility import (
    field_equivalence,
    primary_qoi_equivalence,
)
from .laminar_all_hex_v4_contract import CAMPAIGN_ID


SCHEMA = "flowlab.laminar-all-hex-v4-reproducibility.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_reproducibility(campaign: Path, output: Path, capacity: int) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    manifest = _load(campaign / "campaign-manifest.json")
    if manifest.get("campaignId") != CAMPAIGN_ID:
        raise ValueError("reproducibility source is not laminar-all-hex-v4")
    source_cell = next(
        cell for cell in manifest["cells"] if cell["cellId"] == SOURCE_CELL_ID
    )
    source_report = _load(campaign / "cells" / SOURCE_CELL_ID / "result.json")
    if source_report.get("status") != "accepted":
        raise ValueError("v4 reproducibility source cell is not accepted")
    if _workspace_source_drift(campaign):
        raise ValueError("workspace source hashes no longer match the v4 campaign")
    output.mkdir(parents=True, exist_ok=True)
    utility = compile_traction_utility(output)
    cold_cells = []
    for repeat in ("a", "b"):
        clone = json.loads(json.dumps(source_cell))
        clone["cellId"] = f"reproducibility__cold-repeat-{repeat}"
        clone["sourceCellId"] = SOURCE_CELL_ID
        cold_cells.append(clone)
    budget = ResourceBudget.discover(capacity)
    cold_results = WeightedScheduler(
        budget, output / "cold-execution-events.jsonl"
    ).run(
        cold_cells,
        DockerScientificWorker(
            output,
            threading.Lock(),
            worker_module="server.flowlab.laminar_all_hex_v4_worker",
        ),
    )
    cold_reports = [
        _load(output / result["result"])
        for result in cold_results
        if result.get("result")
    ]
    cold_source_comparisons = [
        compare_confirmation(source_report, report) for report in cold_reports
    ]
    cold_pair = (
        compare_confirmation(cold_reports[0], cold_reports[1])
        if len(cold_reports) == 2
        else None
    )

    fixed_iteration_cell = json.loads(json.dumps(source_cell))
    fixed_iteration_cell["parameters"]["iterations"] = int(
        source_report["observation"]["iterations"]
    )
    mpi_results = [
        _run_mpi_cell(output, fixed_iteration_cell, ranks) for ranks in (2, 4)
    ]
    serial_case = (
        campaign
        / "cells"
        / SOURCE_CELL_ID
        / "execution-attempt-1"
        / "run"
        / source_cell["parameters"]["level"]
        / "case"
    )
    mpi_records = []
    for result in mpi_results:
        record: dict[str, Any] = dict(result)
        if result.get("result"):
            mpi_report = _load(output / result["result"])
            parallel_case = (
                output
                / "cells"
                / result["cellId"]
                / "execution-attempt-1"
                / "run"
                / source_cell["parameters"]["level"]
                / "case"
            )
            record["scientificChecks"] = mpi_report["checks"]
            record["failedChecks"] = mpi_report["failedChecks"]
            record["inputTreeSha256"] = mpi_report["inputTreeSha256"]
            record["primaryQoiEquivalence"] = primary_qoi_equivalence(
                source_report["observation"], mpi_report["observation"]
            )
            record["fieldEquivalence"] = field_equivalence(
                serial_case,
                parallel_case,
                iteration=int(source_report["observation"]["iterations"]),
                parameters=source_cell["parameters"],
            )
            record["derivedErrorNormDiagnostics"] = {
                name: {
                    "serial": source_report["observation"][name],
                    "parallel": mpi_report["observation"][name],
                    "absoluteDifference": abs(
                        float(source_report["observation"][name])
                        - float(mpi_report["observation"][name])
                    ),
                    "promotionGate": False,
                }
                for name in (
                    "velocityRelativeL2Error",
                    "pressureRelativeL2Error",
                )
            }
        mpi_records.append(record)
    checks = {
        "sourceCellAccepted": True,
        "sourceHashesMatchedBeforeExecution": True,
        "twoColdRepeatsCompleted": len(cold_reports) == 2,
        "coldRepeatsPassFrozenScientificGates": len(cold_reports) == 2
        and all(report["status"] == "accepted" for report in cold_reports),
        "coldRepeatsMatchSourceSignature": len(cold_source_comparisons) == 2
        and all(row["status"] == "confirmed" for row in cold_source_comparisons),
        "coldRepeatsMatchEachOther": cold_pair is not None
        and cold_pair["status"] == "confirmed",
        "mpiTwoAndFourCompleted": len(mpi_records) == 2
        and all(
            row["status"] != "incomplete-infrastructure" for row in mpi_records
        ),
        "mpiPassFrozenScientificGates": len(mpi_records) == 2
        and all(row["status"] == "accepted" for row in mpi_records),
        "mpiPrimaryQoisEquivalentToSerial": len(mpi_records) == 2
        and all(
            row.get("primaryQoiEquivalence", {}).get("passed") is True
            for row in mpi_records
        ),
        "mpiFieldsEquivalentOnAnalyticScale": len(mpi_records) == 2
        and all(
            row.get("fieldEquivalence", {}).get("passed") is True
            for row in mpi_records
        ),
        "pinnedImageIdentityPreserved": utility["imageDigest"]
        == manifest["solver"]["digest"],
    }
    accepted = all(checks.values())
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "accepted" if accepted else "rejected-or-incomplete",
        "sourceCampaign": str(campaign),
        "sourceManifestSha256": _canonical_sha256(manifest),
        "sourceCellId": SOURCE_CELL_ID,
        "sourceResultSha256": _sha256(
            campaign / "cells" / SOURCE_CELL_ID / "result.json"
        ),
        "workspaceSourceDriftAfterCampaign": _workspace_source_drift(campaign),
        "equivalenceContract": mpi_equivalence_contract(),
        "utility": utility,
        "checks": checks,
        "coldRepeats": {
            "results": cold_results,
            "sourceComparisons": cold_source_comparisons,
            "repeatPairComparison": cold_pair,
        },
        "mpi": {
            "method": "fixed accepted serial stop iteration; Scotch decomposition; parallel solve and reconstruction",
            "records": mpi_records,
        },
        "promotionAuthorized": False,
    }
    _write_json(output / "reproducibility-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = run_reproducibility(
        args.campaign.resolve(), args.output.resolve(), args.capacity
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
