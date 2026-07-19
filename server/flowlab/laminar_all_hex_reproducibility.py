"""Cold-repeat and decomposed-MPI evidence for the all-hex campaign."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    IMAGE,
    IMAGE_DIGEST,
    ResourceBudget,
    WeightedScheduler,
    _canonical_sha256,
    _sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    _run_command,
    compile_traction_utility,
)
from .laminar_all_hex_confirmation import compare_confirmation


SCHEMA = "flowlab.laminar-all-hex-reproducibility.v1"
SOURCE_CELL_ID = "physical__re-16p67__dir-forward__lh-1__ax-1__fine"
PARALLEL_QOI_RELATIVE_TOLERANCE = 1.0e-6
_ROOT = Path(__file__).resolve().parents[2]
_QOI_PATHS = (
    ("directOpenPressureX", ("directFaceIntegration", "open", "pressure", 0)),
    ("directWallViscousX", ("directFaceIntegration", "walls", "viscous", 0)),
    ("openFoamOpenPressureX", ("openFoamForces", "open", "pressure", 0)),
    ("openFoamWallViscousX", ("openFoamForces", "walls", "viscous", 0)),
    ("velocityRelativeL2Error", ("velocityRelativeL2Error",)),
    ("pressureRelativeL2Error", ("pressureRelativeL2Error",)),
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _value(value: Any, path: tuple[str | int, ...]) -> float:
    current = value
    for key in path:
        current = current[key]
    return float(current)


def qoi_equivalence(
    serial_observation: dict[str, Any], parallel_observation: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {}
    for name, path in _QOI_PATHS:
        serial = _value(serial_observation, path)
        parallel = _value(parallel_observation, path)
        relative = abs(parallel - serial) / max(
            abs(serial), abs(parallel), 1.0e-300
        )
        comparisons[name] = {
            "serial": serial,
            "parallel": parallel,
            "relativeDifference": relative,
            "limit": PARALLEL_QOI_RELATIVE_TOLERANCE,
            "passed": math.isfinite(relative)
            and relative <= PARALLEL_QOI_RELATIVE_TOLERANCE,
        }
    return {
        "relativeTolerance": PARALLEL_QOI_RELATIVE_TOLERANCE,
        "comparisons": comparisons,
        "passed": bool(comparisons)
        and all(row["passed"] for row in comparisons.values()),
    }


def _workspace_source_drift(campaign: Path) -> list[dict[str, str | None]]:
    register = _load_json(campaign / "source-register.json")
    drift = []
    for row in register["records"]:
        path = _ROOT / row["path"]
        current = _sha256(path) if path.is_file() else None
        if current != row["sha256"]:
            drift.append(
                {
                    "path": row["path"],
                    "campaignSha256": row["sha256"],
                    "currentSha256": current,
                }
            )
    return drift


def _run_mpi_cell(
    output: Path, cell: dict[str, Any], ranks: int
) -> dict[str, Any]:
    cell_id = f"reproducibility__mpi-{ranks}"
    cell_root = output / "cells" / cell_id
    execution = cell_root / "execution-attempt-1"
    clone = json.loads(json.dumps(cell))
    clone["cellId"] = cell_id
    clone["sourceCellId"] = SOURCE_CELL_ID
    clone["lane"] = "physical-envelope"
    cell_root.mkdir(parents=True, exist_ok=True)
    _write_json(cell_root / "cell-manifest.json", clone)
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"flowlab-laminar-mpi-{ranks}",
        "--platform",
        "linux/amd64",
        "--cpus",
        str(ranks),
        "--env",
        "OMP_NUM_THREADS=1",
        "-v",
        f"{_ROOT.resolve()}:/workspace:ro",
        "-v",
        f"{output.resolve()}:/campaign",
        "-w",
        "/workspace",
        "--entrypoint",
        "/bin/bash",
        IMAGE,
        "-lc",
        (
            "source /opt/openfoam11/etc/bashrc && "
            "export PYTHONPATH=/workspace && "
            "export PATH=/campaign/preflight/bin:$PATH && "
            "python3 -m server.flowlab.laminar_all_hex_mpi_worker "
            f"--cell /campaign/cells/{cell_id}/cell-manifest.json "
            f"--output /campaign/cells/{cell_id}/execution-attempt-1 "
            f"--ranks {ranks}"
        ),
    ]
    log = cell_root / "container-attempt-1.log"
    exit_code = _run_command(command, log, timeout=10800)
    report_path = execution / "worker-report.json"
    if not report_path.is_file():
        return {
            "cellId": cell_id,
            "ranks": ranks,
            "status": "incomplete-infrastructure",
            "exitCode": exit_code,
            "log": str(log.relative_to(output)),
            "logSha256": _sha256(log),
        }
    report = _load_json(report_path)
    report["containerExitCode"] = exit_code
    report["containerLog"] = str(log.relative_to(output))
    report["containerLogSha256"] = _sha256(log)
    _write_json(cell_root / "result.json", report)
    return {
        "cellId": cell_id,
        "ranks": ranks,
        "status": report["status"],
        "result": str((cell_root / "result.json").relative_to(output)),
    }


def run_reproducibility(campaign: Path, output: Path, capacity: int) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    manifest = _load_json(campaign / "campaign-manifest.json")
    source_cell = next(
        cell for cell in manifest["cells"] if cell["cellId"] == SOURCE_CELL_ID
    )
    source_report = _load_json(campaign / "cells" / SOURCE_CELL_ID / "result.json")
    if source_report.get("status") != "accepted":
        raise ValueError("reproducibility source cell is not accepted")
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
    ).run(cold_cells, DockerScientificWorker(output, threading.Lock()))
    cold_reports = [
        _load_json(output / result["result"])
        for result in cold_results
        if result.get("result")
    ]
    cold_comparisons = [
        compare_confirmation(source_report, report) for report in cold_reports
    ]
    repeat_pair = (
        compare_confirmation(cold_reports[0], cold_reports[1])
        if len(cold_reports) == 2
        else None
    )
    mpi_results = [_run_mpi_cell(output, source_cell, ranks) for ranks in (2, 4)]
    mpi_records = []
    for result in mpi_results:
        record: dict[str, Any] = dict(result)
        if result.get("result"):
            report = _load_json(output / result["result"])
            record["scientificChecks"] = report["checks"]
            record["failedChecks"] = report["failedChecks"]
            record["inputTreeSha256"] = report["inputTreeSha256"]
            record["qoiEquivalence"] = qoi_equivalence(
                source_report["observation"], report["observation"]
            )
        mpi_records.append(record)
    checks = {
        "sourceCellAccepted": True,
        "twoColdRepeatsCompleted": len(cold_reports) == 2,
        "coldRepeatsPassFrozenScientificGates": len(cold_reports) == 2
        and all(report["status"] == "accepted" for report in cold_reports),
        "coldRepeatsMatchSourceSignature": len(cold_comparisons) == 2
        and all(row["status"] == "confirmed" for row in cold_comparisons),
        "coldRepeatsMatchEachOther": repeat_pair is not None
        and repeat_pair["status"] == "confirmed",
        "mpiTwoAndFourCompleted": len(mpi_records) == 2
        and all(row["status"] != "incomplete-infrastructure" for row in mpi_records),
        "mpiPassFrozenScientificGates": len(mpi_records) == 2
        and all(row["status"] == "accepted" for row in mpi_records),
        "mpiQoiEquivalentToSerial": len(mpi_records) == 2
        and all(row.get("qoiEquivalence", {}).get("passed") is True for row in mpi_records),
        "pinnedImageIdentityPreserved": utility["imageDigest"] == IMAGE_DIGEST,
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
        "sourceDriftInterpretation": (
            "Archived campaign evidence remains hash-bound. Follow-up code changes are "
            "recorded here and cannot retroactively alter the completed campaign."
        ),
        "utility": utility,
        "checks": checks,
        "coldRepeats": {
            "results": cold_results,
            "sourceComparisons": cold_comparisons,
            "repeatPairComparison": repeat_pair,
        },
        "mpi": {
            "method": "Scotch decomposition, parallel checkMesh and foamRun, latest-time reconstruction, then the serial direct-face audit",
            "qoiRelativeTolerance": PARALLEL_QOI_RELATIVE_TOLERANCE,
            "toleranceSource": "existing FlowLab straight-pipe serial/parallel equivalence contract",
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
