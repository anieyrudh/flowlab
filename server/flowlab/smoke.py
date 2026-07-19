from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from .adapters import CASE_MANIFEST_PATH, generate_case
from .execution import JobManager, TERMINAL_STATUSES, runtime_diagnostics
from .schemas import CaseRequest, JobRecord


def openfoam_smoke_project() -> dict:
    return {
        "name": "OpenFOAM smoke Venturi",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}, "pressure": 250000},
            "throat": {"id": "throat", "type": "junction", "position": {"x": 120, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 240, "y": 0}, "pressure": 101325},
        },
        "edges": {
            "inlet_pipe": {
                "id": "inlet_pipe",
                "type": "pipe",
                "from": "source",
                "to": "throat",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 6,
                "shape": {"kind": "circular", "diameter": 0.12},
                "roughness": 0.0001,
                "minorLossK": 0.1,
            },
            "venturi": {
                "id": "venturi",
                "type": "venturi",
                "from": "throat",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 6,
                "shape": {"kind": "circular", "diameter": 0.12},
                "throatDiameter": 0.06,
                "roughness": 0.0001,
                "minorLossK": 0.25,
            },
        },
    }


def mujoco_smoke_project() -> dict:
    return {
        "name": "MuJoCo smoke fluid-force sandbox",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "body": {"id": "body", "type": "custom-body", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "body_pipe": {
                "id": "body_pipe",
                "type": "pipe",
                "from": "body",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 10,
                "shape": {"kind": "circular", "diameter": 0.10},
                "roughness": 0.0001,
                "minorLossK": 0.2,
            },
        },
    }


def code_saturne_smoke_project() -> dict:
    return {
        "name": "Code_Saturne smoke Venturi",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}, "pressure": 250000},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 220, "y": 0}, "pressure": 101325},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 8,
                "shape": {"kind": "circular", "diameter": 0.10},
                "roughness": 0.0001,
                "minorLossK": 0.2,
            },
        },
    }


def su2_smoke_project() -> dict:
    return {
        "name": "SU2 smoke Venturi",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}, "pressure": 250000},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 220, "y": 0}, "pressure": 101325},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 8,
                "shape": {"kind": "circular", "diameter": 0.10},
                "roughness": 0.0001,
                "minorLossK": 0.2,
            },
        },
    }


def _runtime_status_map() -> dict[str, dict]:
    return {status.solver: status.model_dump() for status in runtime_diagnostics()}


def _water_hammer_handoff_summary(case_files: dict[str, str]) -> dict | None:
    preview_text = case_files.get("constant/waterHammerPreview.json")
    if not preview_text:
        return None
    try:
        preview = json.loads(preview_text)
    except json.JSONDecodeError:
        return {"error": "constant/waterHammerPreview.json is not valid JSON"}
    openfoam = preview.get("openfoam") if isinstance(preview.get("openfoam"), dict) else {}
    fluid = preview.get("fluid") if isinstance(preview.get("fluid"), dict) else {}
    edges = preview.get("edges") if isinstance(preview.get("edges"), list) else []
    waveform = preview.get("waveform") if isinstance(preview.get("waveform"), list) else []
    dominant_edge = next(
        (
            edge
            for edge in edges
            if isinstance(edge, dict) and edge.get("edgeId") == preview.get("dominantEdgeId")
        ),
        edges[0] if edges and isinstance(edges[0], dict) else {},
    )
    first_waveform_row = waveform[0] if waveform and isinstance(waveform[0], dict) else {}
    peak_waveform_row = max(
        (row for row in waveform if isinstance(row, dict)),
        key=lambda row: float(row.get("absolutePressure", 0.0) or 0.0),
        default={},
    )
    last_waveform_row = waveform[-1] if waveform and isinstance(waveform[-1], dict) else {}
    return {
        "schema": preview.get("schema"),
        "model": preview.get("model"),
        "cfdCoupling": preview.get("cfdCoupling"),
        "productionReady": preview.get("productionReady"),
        "dominantEdgeId": preview.get("dominantEdgeId"),
        "waveSpeed": fluid.get("waveSpeed"),
        "pressureRise": dominant_edge.get("pressureRise"),
        "kinematicPressureRise": dominant_edge.get("kinematicPressureRise"),
        "criticalClosureTime": dominant_edge.get("criticalClosureTime"),
        "closureTime": peak_waveform_row.get("time"),
        "settleTime": last_waveform_row.get("time"),
        "waveformRows": len(waveform),
        "waveformStart": first_waveform_row,
        "waveformPeak": peak_waveform_row,
        "waveformEnd": last_waveform_row,
        "pressureField": openfoam.get("pressureField"),
        "pressureUnits": openfoam.get("pressureUnits"),
        "csv": openfoam.get("csv"),
        "boundary": openfoam.get("boundary"),
        "boundaryType": openfoam.get("boundaryType"),
    }


def _cht_interface_summary(case_files: dict[str, str]) -> dict | None:
    interface_text = case_files.get("constant/flowlab_cht_interface.json")
    if not interface_text:
        return None
    try:
        interface = json.loads(interface_text)
    except json.JSONDecodeError:
        return {"error": "constant/flowlab_cht_interface.json is not valid JSON"}
    checks = interface.get("readinessChecks") if isinstance(interface.get("readinessChecks"), list) else []
    patches = interface.get("patches") if isinstance(interface.get("patches"), dict) else {}
    return {
        "schema": interface.get("schema"),
        "interfaceApproximation": interface.get("interfaceApproximation"),
        "productionReady": interface.get("productionReady"),
        "blockingReasons": interface.get("blockingReasons", []),
        "readiness": {str(check.get("id")): check.get("status") for check in checks if isinstance(check, dict)},
        "patches": patches,
        "regionMeshChecks": interface.get("regionMeshChecks", {}),
        "sourceMesh": interface.get("sourceMesh", {}),
        "solidJacket": interface.get("solidJacket", {}),
    }


def _openfoam_mesh_review_summary(case_files: dict[str, str]) -> dict | None:
    review_text = case_files.get("mesh/openfoam_review.json")
    if not review_text:
        return None
    try:
        review = json.loads(review_text)
    except json.JSONDecodeError:
        return {"error": "mesh/openfoam_review.json is not valid JSON"}
    checks = review.get("readinessChecks") if isinstance(review.get("readinessChecks"), list) else []
    return {
        "schema": review.get("schema"),
        "productionReady": review.get("productionReady"),
        "meshGenerated": review.get("meshGenerated"),
        "meshType": review.get("meshType"),
        "readiness": {str(check.get("id")): check.get("status") for check in checks if isinstance(check, dict)},
        "blockingReasons": review.get("blockingReasons", []),
        "nativeEvidence": review.get("nativeEvidence", {}),
    }


def _wait_for_terminal(manager: JobManager, job_id: str, timeout_seconds: float) -> JobRecord:
    deadline = time.monotonic() + timeout_seconds
    last_job = manager.get_job(job_id)
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job:
            last_job = job
            if job.status in TERMINAL_STATUSES:
                return job
        time.sleep(0.25)
    if last_job:
        return last_job
    raise RuntimeError(f"Job {job_id} disappeared during smoke run.")


def run_openfoam_smoke(
    runtime_root: Path,
    timeout_seconds: float = 120.0,
    advanced_mode: str = "incompressible-navier-stokes",
    manager_factory: Callable[[Path], JobManager] | None = None,
) -> dict:
    runtime_statuses = _runtime_status_map()
    case = generate_case(
        CaseRequest.model_construct(
            project=openfoam_smoke_project(),
            solver="openfoam",
            advancedMode=advanced_mode,
        )
    )
    validation = {
        "requiredFilesPresent": [
            path
            for path in (
                CASE_MANIFEST_PATH,
                "Allrun",
                "AllmeshCheck",
                "system/controlDict",
                "constant/polyMesh/points",
                "constant/polyMesh/faces",
                "constant/polyMesh/owner",
                "constant/polyMesh/neighbour",
                "constant/polyMesh/boundary",
                "constant/flowlab_cht_interface.json",
                "constant/waterHammerPreview.json",
                "constant/waterHammerWaveform.csv",
                "constant/fluid/polyMesh/points",
                "constant/fluid/polyMesh/boundary",
                "constant/solid/polyMesh/points",
                "constant/solid/polyMesh/boundary",
                "system/blockMeshDict",
                "mesh/controls.json",
                "mesh/quality.json",
                "mesh/boundary_layer_plan.json",
                "mesh/adaptation_plan.json",
                "mesh/production_mesh_plan.json",
                "mesh/production_mesh_acceptance.json",
                "mesh/openfoam_review.json",
                "mesh/openfoam_native_mesh_preflight.py",
                "mesh/flowlab_mesh.vtk",
                "mesh/flowlab_mesh.vtu",
            )
            if path in case.files
        ],
        "manifest": json.loads(case.files[CASE_MANIFEST_PATH]) if CASE_MANIFEST_PATH in case.files else None,
        "runCommand": case.runCommand,
        "provenance": case.provenance,
    }
    validation["openfoamMeshReview"] = _openfoam_mesh_review_summary(case.files)
    if advanced_mode == "water-hammer":
        validation["waterHammerHandoff"] = _water_hammer_handoff_summary(case.files)
    if advanced_mode == "conjugate-heat-transfer":
        validation["chtInterface"] = _cht_interface_summary(case.files)
    manager = manager_factory(runtime_root) if manager_factory else JobManager(runtime_root=runtime_root)
    job = manager.queue_job(case)
    terminal_job = _wait_for_terminal(manager, job.id, timeout_seconds)
    result = terminal_job.result or {}
    completed = terminal_job.status == "complete" and terminal_job.exitCode == 0
    report = {
        "smoke": f"openfoam-{advanced_mode}-solve-through",
        "advancedMode": advanced_mode,
        "completed": completed,
        "status": terminal_job.status,
        "caseId": case.id,
        "jobId": terminal_job.id,
        "runtimeRoot": str(runtime_root),
        "caseDir": terminal_job.caseDir,
        "execution": terminal_job.execution,
        "command": terminal_job.command,
        "error": terminal_job.error,
        "exitCode": terminal_job.exitCode,
        "runtime": runtime_statuses.get("openfoam"),
        "caseValidation": validation,
        "logsTail": terminal_job.logs[-12:],
        "logsCaptured": result.get("logsCaptured", len(terminal_job.logs)),
        "solverLogPath": result.get("solverLogPath"),
        "resultFiles": result.get("resultFiles", []),
        "diagnosticFiles": result.get("diagnosticFiles", []),
        "diagnosticSummary": result.get("diagnosticSummary", []),
        "patchMetrics": result.get("patchMetrics"),
        "diagnosticsAcceptance": result.get("diagnosticsAcceptance"),
        "logSummary": result.get("logSummary"),
    }
    if not completed and terminal_job.status == "blocked":
        report["blockedReason"] = terminal_job.error or "OpenFOAM runtime unavailable."
    return report


def run_mujoco_smoke(
    runtime_root: Path,
    timeout_seconds: float = 60.0,
    advanced_mode: str = "rigid-body-fluid-forces",
    manager_factory: Callable[[Path], JobManager] | None = None,
) -> dict:
    runtime_statuses = _runtime_status_map()
    case = generate_case(
        CaseRequest.model_construct(
            project=mujoco_smoke_project(),
            solver="mujoco",
            advancedMode=advanced_mode,
        )
    )
    validation = {
        "requiredFilesPresent": [
            path
            for path in (
                CASE_MANIFEST_PATH,
                "model.xml",
                "run_mujoco.py",
                "mesh/flowlab_mesh.json",
                "mesh/flowlab_mesh.vtk",
            )
            if path in case.files
        ],
        "manifest": json.loads(case.files[CASE_MANIFEST_PATH]) if CASE_MANIFEST_PATH in case.files else None,
        "runCommand": case.runCommand,
        "provenance": case.provenance,
    }
    manager = manager_factory(runtime_root) if manager_factory else JobManager(runtime_root=runtime_root)
    job = manager.queue_job(case)
    terminal_job = _wait_for_terminal(manager, job.id, timeout_seconds)
    result = terminal_job.result or {}
    completed = terminal_job.status == "complete" and terminal_job.exitCode == 0
    report = {
        "smoke": f"mujoco-{advanced_mode}-solve-through",
        "advancedMode": advanced_mode,
        "completed": completed,
        "status": terminal_job.status,
        "caseId": case.id,
        "jobId": terminal_job.id,
        "runtimeRoot": str(runtime_root),
        "caseDir": terminal_job.caseDir,
        "execution": terminal_job.execution,
        "command": terminal_job.command,
        "error": terminal_job.error,
        "exitCode": terminal_job.exitCode,
        "runtime": runtime_statuses.get("mujoco"),
        "caseValidation": validation,
        "logsTail": terminal_job.logs[-12:],
        "logsCaptured": result.get("logsCaptured", len(terminal_job.logs)),
        "resultFiles": result.get("resultFiles", []),
        "diagnosticFiles": result.get("diagnosticFiles", []),
        "diagnosticSummary": result.get("diagnosticSummary", []),
        "logSummary": result.get("logSummary"),
    }
    if not completed and terminal_job.status == "blocked":
        report["blockedReason"] = terminal_job.error or "MuJoCo runtime unavailable."
    return report


def run_code_saturne_smoke(
    runtime_root: Path,
    timeout_seconds: float = 180.0,
    advanced_mode: str = "incompressible-navier-stokes",
    manager_factory: Callable[[Path], JobManager] | None = None,
) -> dict:
    runtime_statuses = _runtime_status_map()
    case = generate_case(
        CaseRequest.model_construct(
            project=code_saturne_smoke_project(),
            solver="code-saturne",
            advancedMode=advanced_mode,
        )
    )
    validation = {
        "requiredFilesPresent": [
            path
            for path in (
                CASE_MANIFEST_PATH,
                "DATA/setup.xml",
                "DATA/flowlab_physics_preset.json",
                "DATA/flowlab_native_setup_checklist.json",
                "DATA/flowlab_code_saturne_capability_matrix.json",
                "DATA/run.cfg",
                "DATA/cs_user_scripts.py",
                "DATA/cs_user_physics.py",
                "SRC/cs_user_boundary_conditions.f90",
                "MESH/flowlab_mesh.msh",
                "mesh/quality.json",
            )
            if path in case.files
        ],
        "manifest": json.loads(case.files[CASE_MANIFEST_PATH]) if CASE_MANIFEST_PATH in case.files else None,
        "runCommand": case.runCommand,
        "provenance": case.provenance,
    }
    manager = manager_factory(runtime_root) if manager_factory else JobManager(runtime_root=runtime_root)
    job = manager.queue_job(case)
    terminal_job = _wait_for_terminal(manager, job.id, timeout_seconds)
    result = terminal_job.result or {}
    completed = terminal_job.status == "complete" and terminal_job.exitCode == 0
    report = {
        "smoke": f"code-saturne-{advanced_mode}-solve-through",
        "advancedMode": advanced_mode,
        "completed": completed,
        "status": terminal_job.status,
        "caseId": case.id,
        "jobId": terminal_job.id,
        "runtimeRoot": str(runtime_root),
        "caseDir": terminal_job.caseDir,
        "execution": terminal_job.execution,
        "command": terminal_job.command,
        "error": terminal_job.error,
        "exitCode": terminal_job.exitCode,
        "runtime": runtime_statuses.get("code-saturne"),
        "caseValidation": validation,
        "logsTail": terminal_job.logs[-12:],
        "logsCaptured": result.get("logsCaptured", len(terminal_job.logs)),
        "resultFiles": result.get("resultFiles", []),
        "diagnosticFiles": result.get("diagnosticFiles", []),
        "diagnosticSummary": result.get("diagnosticSummary", []),
        "logSummary": result.get("logSummary"),
    }
    if not completed and terminal_job.status == "blocked":
        report["blockedReason"] = terminal_job.error or "Code_Saturne runtime unavailable."
    return report


def run_su2_smoke(
    runtime_root: Path,
    timeout_seconds: float = 120.0,
    advanced_mode: str = "incompressible-navier-stokes",
    manager_factory: Callable[[Path], JobManager] | None = None,
) -> dict:
    runtime_statuses = _runtime_status_map()
    case = generate_case(
        CaseRequest.model_construct(
            project=su2_smoke_project(),
            solver="su2",
            advancedMode=advanced_mode,
        )
    )
    validation = {
        "requiredFilesPresent": [
            path
            for path in (
                CASE_MANIFEST_PATH,
                "case.cfg",
                "flowlab_su2_mode_preset.json",
                "flowlab_su2_native_setup_checklist.json",
                "flowlab_su2_capability_matrix.json",
                "mesh/flowlab_mesh.su2",
                "mesh/flowlab_mesh.vtk",
                "mesh/flowlab_mesh.vtu",
                "mesh/quality.json",
            )
            if path in case.files
        ],
        "manifest": json.loads(case.files[CASE_MANIFEST_PATH]) if CASE_MANIFEST_PATH in case.files else None,
        "runCommand": case.runCommand,
        "provenance": case.provenance,
    }
    manager = manager_factory(runtime_root) if manager_factory else JobManager(runtime_root=runtime_root)
    job = manager.queue_job(case)
    terminal_job = _wait_for_terminal(manager, job.id, timeout_seconds)
    result = terminal_job.result or {}
    completed = terminal_job.status == "complete" and terminal_job.exitCode == 0
    report = {
        "smoke": f"su2-{advanced_mode}-solve-through",
        "advancedMode": advanced_mode,
        "completed": completed,
        "status": terminal_job.status,
        "caseId": case.id,
        "jobId": terminal_job.id,
        "runtimeRoot": str(runtime_root),
        "caseDir": terminal_job.caseDir,
        "execution": terminal_job.execution,
        "command": terminal_job.command,
        "error": terminal_job.error,
        "exitCode": terminal_job.exitCode,
        "runtime": runtime_statuses.get("su2"),
        "caseValidation": validation,
        "logsTail": terminal_job.logs[-12:],
        "logsCaptured": result.get("logsCaptured", len(terminal_job.logs)),
        "resultFiles": result.get("resultFiles", []),
        "diagnosticFiles": result.get("diagnosticFiles", []),
        "diagnosticSummary": result.get("diagnosticSummary", []),
        "logSummary": result.get("logSummary"),
    }
    if not completed and terminal_job.status == "blocked":
        report["blockedReason"] = terminal_job.error or "SU2 runtime unavailable."
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a FlowLab solver solve-through smoke check.")
    parser.add_argument(
        "--solver",
        choices=("openfoam", "mujoco", "code-saturne", "su2"),
        default="openfoam",
        help="Solver smoke to run.",
    )
    parser.add_argument("--runtime-root", default="runtime/flowlab-smoke", help="Directory for generated smoke job artifacts.")
    parser.add_argument("--output", default="", help="Optional JSON report output path.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for the solver job to finish.")
    parser.add_argument(
        "--advanced-mode",
        default="",
        help="Override the solver advanced mode for smoke validation, for example heat-transfer or compressible-flow.",
    )
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    if args.solver == "mujoco":
        report = run_mujoco_smoke(
            runtime_root,
            timeout_seconds=args.timeout,
            advanced_mode=args.advanced_mode or "rigid-body-fluid-forces",
        )
    elif args.solver == "code-saturne":
        report = run_code_saturne_smoke(
            runtime_root,
            timeout_seconds=args.timeout,
            advanced_mode=args.advanced_mode or "incompressible-navier-stokes",
        )
    elif args.solver == "su2":
        report = run_su2_smoke(
            runtime_root,
            timeout_seconds=args.timeout,
            advanced_mode=args.advanced_mode or "incompressible-navier-stokes",
        )
    else:
        report = run_openfoam_smoke(
            runtime_root,
            timeout_seconds=args.timeout,
            advanced_mode=args.advanced_mode or "incompressible-navier-stokes",
        )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
