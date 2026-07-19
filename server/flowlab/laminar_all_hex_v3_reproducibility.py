"""Cold-repeat and MPI evidence under the frozen v3 equivalence contract."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import threading
from typing import Any, Iterable

from .cad_parabolic_smoke import _read_cell_centres
from .laminar_all_hex_campaign import ResourceBudget, WeightedScheduler, _canonical_sha256, _sha256, _write_json
from .laminar_all_hex_campaign_runner import DockerScientificWorker, compile_traction_utility
from .laminar_all_hex_confirmation import compare_confirmation
from .laminar_all_hex_reproducibility import (
    SOURCE_CELL_ID,
    _run_mpi_cell,
    _workspace_source_drift,
)
from .laminar_all_hex_campaign_worker import physical_spec
from .laminar_all_hex_v3_contract import (
    CAMPAIGN_ID,
    FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT,
    PRIMARY_QOI_RELATIVE_TOLERANCE,
    mpi_equivalence_contract,
)
from .open_boundary_mms_runner import _values


SCHEMA = "flowlab.laminar-all-hex-v3-reproducibility.v1"
_PRIMARY_QOI_PATHS = (
    ("directOpenPressureX", ("directFaceIntegration", "open", "pressure", 0)),
    ("directWallViscousX", ("directFaceIntegration", "walls", "viscous", 0)),
    ("openFoamOpenPressureX", ("openFoamForces", "open", "pressure", 0)),
    ("openFoamWallViscousX", ("openFoamForces", "walls", "viscous", 0)),
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _value(value: Any, path: tuple[str | int, ...]) -> float:
    current = value
    for key in path:
        current = current[key]
    return float(current)


def primary_qoi_equivalence(
    serial: dict[str, Any], parallel: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {}
    for name, path in _PRIMARY_QOI_PATHS:
        left = _value(serial, path)
        right = _value(parallel, path)
        relative = abs(right - left) / max(abs(left), abs(right), 1.0e-300)
        comparisons[name] = {
            "serial": left,
            "parallel": right,
            "relativeDifference": relative,
            "limit": PRIMARY_QOI_RELATIVE_TOLERANCE,
            "passed": math.isfinite(relative)
            and relative <= PRIMARY_QOI_RELATIVE_TOLERANCE,
        }
    return {
        "comparisons": comparisons,
        "passed": bool(comparisons)
        and all(row["passed"] for row in comparisons.values()),
    }


def analytic_scaled_field_difference(
    serial: Iterable[tuple[float, ...]],
    parallel: Iterable[tuple[float, ...]],
    analytic: Iterable[tuple[float, ...]],
) -> float:
    serial_rows = list(serial)
    parallel_rows = list(parallel)
    analytic_rows = list(analytic)
    if not serial_rows or not (
        len(serial_rows) == len(parallel_rows) == len(analytic_rows)
    ):
        return math.inf
    numerator = sum(
        (left - right) ** 2
        for serial_row, parallel_row in zip(serial_rows, parallel_rows)
        for left, right in zip(serial_row, parallel_row)
    )
    denominator = sum(
        value**2 for row in analytic_rows for value in row
    )
    return math.sqrt(numerator / max(denominator, 1.0e-300))


def field_equivalence(
    serial_case: Path,
    parallel_case: Path,
    *,
    iteration: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    centres = _read_cell_centres(serial_case / "0/C")
    spec = physical_spec(parameters)
    serial_u = _values(serial_case / str(iteration) / "U", True)
    parallel_u = _values(parallel_case / str(iteration) / "U", True)
    serial_p = _values(serial_case / str(iteration) / "p", False)
    parallel_p = _values(parallel_case / str(iteration) / "p", False)
    exact_u = [spec.velocity(*point) for point in centres]
    exact_p = [(spec.pressure(*point),) for point in centres]
    velocity = analytic_scaled_field_difference(serial_u, parallel_u, exact_u)
    pressure = analytic_scaled_field_difference(serial_p, parallel_p, exact_p)
    return {
        "normalization": "analytic-field-L2-norm",
        "limit": FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT,
        "velocity": velocity,
        "pressure": pressure,
        "passed": max(velocity, pressure)
        <= FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT,
    }


def run_reproducibility(campaign: Path, output: Path, capacity: int) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    manifest = _load(campaign / "campaign-manifest.json")
    if manifest.get("campaignId") != CAMPAIGN_ID:
        raise ValueError("reproducibility source is not laminar-all-hex-v3")
    source_cell = next(
        cell for cell in manifest["cells"] if cell["cellId"] == SOURCE_CELL_ID
    )
    source_report = _load(campaign / "cells" / SOURCE_CELL_ID / "result.json")
    if source_report.get("status") != "accepted":
        raise ValueError("v3 reproducibility source cell is not accepted")
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
            worker_module="server.flowlab.laminar_all_hex_v3_worker",
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
        "twoColdRepeatsCompleted": len(cold_reports) == 2,
        "coldRepeatsPassFrozenScientificGates": len(cold_reports) == 2
        and all(report["status"] == "accepted" for report in cold_reports),
        "coldRepeatsMatchSourceSignature": len(cold_source_comparisons) == 2
        and all(row["status"] == "confirmed" for row in cold_source_comparisons),
        "coldRepeatsMatchEachOther": cold_pair is not None
        and cold_pair["status"] == "confirmed",
        "mpiTwoAndFourCompleted": len(mpi_records) == 2
        and all(row["status"] != "incomplete-infrastructure" for row in mpi_records),
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
