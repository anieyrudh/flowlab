"""Host-side Docker runner and aggregator for laminar-all-hex-v2."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any

from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    IMAGE,
    IMAGE_DIGEST,
    INSTRUMENTED_LIBRARY_RELATIVE,
    INSTRUMENTED_LIBRARY_SHA256,
    LEVELS,
    MAXIMUM_ORDER_SPREAD,
    MINIMUM_OBSERVED_ORDER,
    FINE_GCI_LIMIT,
    ResourceBudget,
    SCIENTIFIC_LANES,
    WeightedScheduler,
    _append_jsonl,
    _canonical_sha256,
    _sha256,
    _write_json,
    build_manifest,
    initial_issues,
    make_issue,
    validate_manifest,
)
from .open_boundary_affine_grid_invariance import _level_summary
from .open_boundary_non_affine_mms import _convergence


SCHEMA = "flowlab.laminar-all-hex-execution.v1"
_ROOT = Path(__file__).resolve().parents[2]
_UTILITY = _ROOT / "benchmarks/tools/flowlabPatchTractionAudit"


def _run_command(command: list[str], log_path: Path, *, timeout: int | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    return completed.returncode


def _docker_image_id() -> str:
    completed = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Docker image inspection failed")
    return completed.stdout.strip()


def compile_traction_utility(output: Path) -> dict[str, Any]:
    image_id = _docker_image_id()
    if image_id != IMAGE_DIGEST:
        raise RuntimeError(
            f"pinned image digest mismatch: expected {IMAGE_DIGEST}, observed {image_id}"
        )
    instrumented_library = _ROOT / INSTRUMENTED_LIBRARY_RELATIVE
    if not instrumented_library.is_file():
        raise RuntimeError(
            f"pinned instrumented library is missing: {instrumented_library}"
        )
    instrumented_sha256 = _sha256(instrumented_library)
    if instrumented_sha256 != INSTRUMENTED_LIBRARY_SHA256:
        raise RuntimeError(
            "pinned instrumented library digest mismatch: "
            f"expected {INSTRUMENTED_LIBRARY_SHA256}, observed {instrumented_sha256}"
        )
    source = output / "preflight/utility-source"
    shutil.copytree(_UTILITY, source)
    (output / "preflight/bin").mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-v",
        f"{output.resolve()}:/campaign",
        "--entrypoint",
        "/bin/bash",
        IMAGE,
        "-lc",
        (
            "source /opt/openfoam11/etc/bashrc && "
            "cd /campaign/preflight/utility-source && wmake && "
            "cp \"$FOAM_USER_APPBIN/flowlabPatchTractionAudit\" "
            "/campaign/preflight/bin/flowlabPatchTractionAudit"
        ),
    ]
    status = _run_command(command, output / "preflight/wmake.log", timeout=600)
    binary = output / "preflight/bin/flowlabPatchTractionAudit"
    report = {
        "schema": "flowlab.laminar-all-hex-utility-preflight.v1",
        "status": "accepted" if status == 0 and binary.is_file() else "rejected",
        "image": IMAGE,
        "imageDigest": image_id,
        "compileExitCode": status,
        "source": {
            "path": str(_UTILITY.relative_to(_ROOT)),
            "sha256": _sha256(_UTILITY / "flowlabPatchTractionAudit.C"),
        },
        "binary": {
            "path": "preflight/bin/flowlabPatchTractionAudit",
            "sha256": _sha256(binary) if binary.is_file() else None,
        },
        "instrumentedLibrary": {
            "path": INSTRUMENTED_LIBRARY_RELATIVE,
            "sha256": instrumented_sha256,
        },
    }
    _write_json(output / "preflight/utility-report.json", report)
    if report["status"] != "accepted":
        raise RuntimeError("traction utility preflight failed")
    return report


class DockerScientificWorker:
    def __init__(
        self,
        output: Path,
        issue_lock: threading.Lock,
        *,
        worker_module: str = "server.flowlab.laminar_all_hex_campaign_worker",
    ) -> None:
        self.output = output
        self.issue_lock = issue_lock
        self.worker_module = worker_module

    def __call__(self, cell: dict[str, Any]) -> dict[str, Any]:
        cell_root = self.output / "cells" / cell["cellId"]
        cell_root.mkdir(parents=True, exist_ok=True)
        _write_json(cell_root / "cell-manifest.json", cell)
        relative = Path("cells") / cell["cellId"]
        container_name = "flowlab-" + _canonical_sha256(cell["cellId"])[:18]
        attempts = []
        for attempt in (1, 2):
            attempt_relative = relative / f"execution-attempt-{attempt}"
            affine_instrumentation = cell["lane"] == "affine"
            instrumentation_environment = (
                [
                    "--env",
                    f"FLOWLAB_INSTRUMENTED_LIBRARY=/workspace/{INSTRUMENTED_LIBRARY_RELATIVE}",
                ]
                if affine_instrumentation
                else []
            )
            library_path_export = (
                f"export LD_LIBRARY_PATH=/workspace/{Path(INSTRUMENTED_LIBRARY_RELATIVE).parent}:$LD_LIBRARY_PATH && "
                if affine_instrumentation
                else ""
            )
            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--platform",
                "linux/amd64",
                "--cpus",
                str(cell["resourceWeight"]),
                "--env",
                "OMP_NUM_THREADS=1",
                *instrumentation_environment,
                "-v",
                f"{_ROOT.resolve()}:/workspace:ro",
                "-v",
                f"{self.output.resolve()}:/campaign",
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
                    f"{library_path_export}"
                    f"python3 -m {self.worker_module} "
                    f"--cell /campaign/{relative}/cell-manifest.json "
                    f"--output /campaign/{attempt_relative}"
                ),
            ]
            log = cell_root / f"container-attempt-{attempt}.log"
            exit_code = _run_command(command, log, timeout=7200)
            report_path = self.output / attempt_relative / "worker-report.json"
            attempts.append(
                {
                    "attempt": attempt,
                    "exitCode": exit_code,
                    "log": str(log.relative_to(self.output)),
                    "logSha256": _sha256(log),
                    "output": str(attempt_relative),
                    "reportPresent": report_path.is_file(),
                }
            )
            if report_path.is_file():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["attempts"] = attempts
                _write_json(cell_root / "result.json", report)
                return {
                    "cellId": cell["cellId"],
                    "lane": cell["lane"],
                    "status": report["status"],
                    "result": str((relative / "result.json")),
                    "attempts": attempts,
                }
            if attempt == 1:
                continue
        issue = make_issue(
            f"INF-{_canonical_sha256(cell['cellId'])[:10].upper()}",
            kind="infrastructure",
            severity="P1",
            summary="Scientific worker produced no worker report after one identical retry.",
            lane=cell["lane"],
            cell_id=cell["cellId"],
            affected_claims=("campaign completeness",),
            affected_gates=("crossLane.everyPlannedCellAccountedFor",),
            evidence=tuple(attempts),
            minimal_reproducer=str(relative / "cell-manifest.json"),
            blocked_work=("lane aggregation", "campaign promotion"),
            next_diagnostic="Inspect the retained container logs without changing the scientific cell manifest.",
        )
        _append_jsonl(self.output / "issues.jsonl", issue, self.issue_lock)
        return {
            "cellId": cell["cellId"],
            "lane": cell["lane"],
            "status": "incomplete-infrastructure",
            "attempts": attempts,
        }


def _load_worker_reports(output: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted((output / "cells").glob("*/result.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        reports[report["cellId"]] = report
    return reports


def _aggregate_affine(reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_level = {report["parameters"]["level"]: report for report in reports}
    summaries = []
    for level, n, _ in LEVELS:
        report = by_level.get(level)
        if report is None:
            continue
        summaries.append(_level_summary(level, n, report["observation"]))
    checks = {
        "everyLevelPresent": len(summaries) == 3,
        "everyLevelAccepted": len(reports) == 3
        and all(report["status"] == "accepted" for report in reports),
        "resolutionSequence12_24_48": [row["cellsPerAxis"] for row in summaries]
        == [12, 24, 48],
        "allMetricsFinite": bool(summaries)
        and all(
            value is not None and math.isfinite(float(value))
            for row in summaries
            for key, value in row.items()
            if key not in {"level", "status", "allChecksPassed"}
        ),
    }
    return {
        "schema": "flowlab.laminar-all-hex-affine-lane.v1",
        "status": "accepted" if all(checks.values()) else "rejected",
        "checks": checks,
        "levels": summaries,
    }


def _convergence_checks(name: str, result: dict[str, Any]) -> dict[str, bool]:
    orders = result["observedOrder"]
    errors = result["relativeL2Errors"]
    return {
        f"{name}ErrorsMonotonicallyDecrease": all(
            math.isfinite(value) and value > 0.0 for value in errors
        )
        and errors[0] > errors[1] > errors[2],
        f"{name}ObservedOrder": min(orders.values()) >= MINIMUM_OBSERVED_ORDER,
        f"{name}OrderConsistency": result["orderSpread"] <= MAXIMUM_ORDER_SPREAD,
        f"{name}FineGci": result["gciRelativeToAnalyticFieldNorm"]["fine"]
        <= FINE_GCI_LIMIT,
    }


def _aggregate_mms(reports: list[dict[str, Any]]) -> dict[str, Any]:
    order = {level: index for index, (level, _, _) in enumerate(LEVELS)}
    reports = sorted(reports, key=lambda row: order[row["parameters"]["level"]])
    observations = [report["observation"] for report in reports]
    velocity = _convergence(
        [row["velocityRelativeL2Error"] for row in observations]
    ) if len(observations) == 3 else None
    pressure = _convergence(
        [row["pressureRelativeL2Error"] for row in observations]
    ) if len(observations) == 3 else None
    checks = {
        "everyLevelPresent": len(observations) == 3,
        "everyCellAccepted": len(reports) == 3
        and all(report["status"] == "accepted" for report in reports),
    }
    if velocity and pressure:
        checks.update(_convergence_checks("velocity", velocity))
        checks.update(_convergence_checks("pressure", pressure))
    return {
        "schema": "flowlab.laminar-all-hex-mms-lane.v1",
        "status": "accepted" if all(checks.values()) else "rejected",
        "checks": checks,
        "convergence": {"velocity": velocity, "pressure": pressure},
        "observations": observations,
    }


def _physical_group_key(report: dict[str, Any]) -> tuple[Any, ...]:
    p = report["parameters"]
    return (
        p["reynoldsNumberHeightBased"],
        p["flowDirection"],
        p["lengthToHeightRatio"],
        p["axialCellAspectRatio"],
    )


def _aggregate_physical(reports: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        grouped[_physical_group_key(report)].append(report)
    level_order = {level: index for index, (level, _, _) in enumerate(LEVELS)}
    groups = []
    for key in sorted(grouped, key=lambda value: tuple(str(v) for v in value)):
        rows = sorted(
            grouped[key], key=lambda row: level_order[row["parameters"]["level"]]
        )
        observations = [row["observation"] for row in rows]
        velocity = _convergence(
            [row["velocityRelativeL2Error"] for row in observations]
        ) if len(rows) == 3 else None
        pressure = _convergence(
            [row["pressureRelativeL2Error"] for row in observations]
        ) if len(rows) == 3 else None
        checks = {
            "threeLevelsPresent": len(rows) == 3,
            "everyCellAccepted": len(rows) == 3
            and all(row["status"] == "accepted" for row in rows),
        }
        if velocity:
            checks.update(_convergence_checks("velocity", velocity))
        pressure_assessment = {
            "convergence": pressure,
            "hardGate": "recorded-not-added-post-hoc",
            "note": "Pressure convergence is recorded for conflict analysis; the frozen physical gate remains analytic pressure-force and field accuracy.",
        }
        groups.append(
            {
                "parameters": {
                    "reynoldsNumberHeightBased": key[0],
                    "flowDirection": key[1],
                    "lengthToHeightRatio": key[2],
                    "axialCellAspectRatio": key[3],
                },
                "status": "accepted" if all(checks.values()) else "rejected",
                "checks": checks,
                "velocityConvergence": velocity,
                "pressureAssessment": pressure_assessment,
                "cellIds": [row["cellId"] for row in rows],
            }
        )
    checks = {
        "all24OperatingPointsPresent": len(groups) == 24,
        "all72CellsPresent": len(reports) == 72,
        "allOperatingPointsAccepted": len(groups) == 24
        and all(group["status"] == "accepted" for group in groups),
    }
    return {
        "schema": "flowlab.laminar-all-hex-physical-lane.v1",
        "status": "accepted" if all(checks.values()) else "rejected",
        "checks": checks,
        "groups": groups,
    }


def _response_rows(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for report in reports:
        observation = report["observation"]
        force = observation.get("forceComparison", {})
        face = observation.get("faceComparison", {})
        rows.append(
            {
                **report["parameters"],
                "cellId": report["cellId"],
                "velocityRelativeL2Error": observation.get("velocityRelativeL2Error"),
                "pressureRelativeL2Error": observation.get("pressureRelativeL2Error"),
                "massRelativeImbalance": observation.get("massRelativeImbalance"),
                "wallViscousForceRelativeError": force.get("wallViscousForceRelativeError"),
                "faceViscousTractionRelativeError": face.get("maxViscousTractionRelativeError"),
            }
        )
    return rows


def aggregate(
    output: Path,
    manifest: dict[str, Any],
    run_results: list[dict[str, Any]],
    *,
    campaign_id: str = CAMPAIGN_ID,
) -> dict[str, Any]:
    reports_by_id = _load_worker_reports(output)
    scientific_cells = [
        cell for cell in manifest["cells"] if cell["lane"] in SCIENTIFIC_LANES
    ]
    reports = [reports_by_id[cell["cellId"]] for cell in scientific_cells if cell["cellId"] in reports_by_id]
    by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        by_lane[report["lane"]].append(report)
    affine = _aggregate_affine(by_lane["affine"])
    mms = _aggregate_mms(by_lane["non-affine-mms"])
    physical = _aggregate_physical(by_lane["physical-envelope"])
    _write_json(output / "lanes/affine/lane-report.json", affine)
    _write_json(output / "lanes/non-affine-mms/lane-report.json", mms)
    _write_json(output / "lanes/physical-envelope/lane-report.json", physical)
    interaction = {
        "schema": "flowlab.laminar-all-hex-interaction-evidence.v1",
        "status": "recorded",
        "method": "raw multi-factor response table; causal conclusions require independent diagnostics",
        "responses": _response_rows(by_lane["physical-envelope"]),
    }
    _write_json(output / "interaction-report.json", interaction)
    issue_lock = threading.Lock()
    issue_index = 1
    for result in run_results:
        if result["status"] != "rejected-scientific":
            continue
        report = reports_by_id.get(result["cellId"], {})
        issue = make_issue(
            f"SCI-{issue_index:04d}",
            kind="error",
            severity="P1",
            summary="A predeclared scientific cell failed one or more immutable gates.",
            lane=result.get("lane"),
            cell_id=result["cellId"],
            affected_claims=(f"{campaign_id} envelope",),
            affected_gates=tuple(report.get("failedChecks", [])),
            evidence=({"result": result.get("result")},),
            interacting_factors=tuple(
                str(key) for key in report.get("parameters", {}).keys()
            ),
            minimal_reproducer=str(Path("cells") / result["cellId"] / "cell-manifest.json"),
            blocked_work=("affected operating-point acceptance", "outer envelope promotion"),
            next_diagnostic="Confirm the same cell independently, then cluster it with matching failure signatures before changing inputs.",
        )
        _append_jsonl(output / "issues.jsonl", issue, issue_lock)
        issue_index += 1
    accounted = len(run_results) == len(scientific_cells)
    infra_complete = all(result["status"] != "incomplete-infrastructure" for result in run_results)
    checks = {
        "everyScientificCellAccountedFor": accounted,
        "noInfrastructureGaps": infra_complete,
        "affineAccepted": affine["status"] == "accepted",
        "nonAffineMmsAccepted": mms["status"] == "accepted",
        "physicalEnvelopeAccepted": physical["status"] == "accepted",
        "experimentalDatasetPinned": False,
        "reproducibilityAccepted": False,
        "negativeControlsAccepted": False,
        "productContractAccepted": False,
    }
    numerical_accepted = all(
        checks[name]
        for name in (
            "everyScientificCellAccountedFor",
            "noInfrastructureGaps",
            "affineAccepted",
            "nonAffineMmsAccepted",
            "physicalEnvelopeAccepted",
        )
    )
    report = {
        "schema": "flowlab.laminar-all-hex-campaign-report.v1",
        "campaignId": campaign_id,
        "status": "numerical-lanes-accepted" if numerical_accepted else "numerical-lanes-rejected-or-incomplete",
        "checks": checks,
        "scientificCellCount": len(scientific_cells),
        "accountedCellCount": len(run_results),
        "workerReportCount": len(reports),
        "promotionAuthorized": False,
        "nextStage": "reproducibility-negative-controls-and-experimental-data" if numerical_accepted else "issue-clustering-and-independent-confirmation",
    }
    _write_json(output / "campaign-report.json", report)
    return report


def run_execution(
    output: Path,
    *,
    capacity: int | None = None,
    campaign_id: str = CAMPAIGN_ID,
    manifest_builder: Any = build_manifest,
    manifest_validator: Any = validate_manifest,
    worker_module: str = "server.flowlab.laminar_all_hex_campaign_worker",
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty campaign output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = manifest_builder()
    manifest_checks = manifest_validator(manifest)
    if not all(manifest_checks.values()):
        raise ValueError(f"campaign manifest failed: {manifest_checks}")
    _write_json(output / "campaign-manifest.json", manifest)
    (output / "campaign-manifest.sha256").write_text(
        _canonical_sha256(manifest) + "  campaign-manifest.json\n",
        encoding="utf-8",
    )
    _write_json(output / "gate-catalog.json", manifest["gateCatalog"])
    _write_json(output / "source-register.json", manifest["sourceRegister"])
    for issue in initial_issues():
        _append_jsonl(output / "issues.jsonl", issue)
    utility = compile_traction_utility(output)
    cells = [cell for cell in manifest["cells"] if cell["lane"] in SCIENTIFIC_LANES]
    budget = ResourceBudget.discover(capacity)
    scheduler = WeightedScheduler(budget, output / "execution-events.jsonl")
    run_results = scheduler.run(
        cells,
        DockerScientificWorker(
            output,
            threading.Lock(),
            worker_module=worker_module,
        ),
    )
    _write_json(
        output / "scientific-run-results.json",
        {
            "schema": SCHEMA,
            "campaignId": campaign_id,
            "utility": utility,
            "scheduler": {
                "capacity": budget.capacity,
                "maxWorkers": budget.max_workers,
                "maxFineWorkers": budget.max_fine_workers,
            },
            "results": run_results,
        },
    )
    return aggregate(output, manifest, run_results, campaign_id=campaign_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int)
    args = parser.parse_args()
    report = run_execution(args.output.resolve(), capacity=args.capacity)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "numerical-lanes-accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
