"""Govern the bounded full-revolution O-grid straight-pipe campaign.

The frozen v1 campaign uses the same adapter and ``JobManager`` path as the
desktop product. Materialization, execution, numerical assessment, immutable
packaging, independent review, and any later fixture mutation remain separate
states.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
from typing import Any, Sequence

from . import adapters
from .execution import (
    JOB_RECORD_FILENAME,
    OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH,
    TERMINAL_STATUSES,
    JobManager,
    collect_patch_metrics,
    materialize_case_files,
    validate_solver_case,
)
from .full_ogrid import FULL_OGRID_PROFILE_SCHEMA, FullOGridSpec
from .results import parse_vtk_result
from .schemas import CaseRequest, SolverCase
from .verification import (
    StraightPipeSpec,
    richardson_grid_convergence,
    straight_pipe_reference,
)


CAMPAIGN_SCHEMA = "flowlab.full-ogrid-straight-pipe-campaign.v1"
LEVEL_SCHEMA = "flowlab.full-ogrid-straight-pipe-level.v1"
RUN_RESULT_SCHEMA = "flowlab.full-ogrid-straight-pipe-run-result.v1"
PACKAGE_MANIFEST_SCHEMA = "flowlab.full-ogrid-straight-pipe-package-manifest.v1"
CONTRACT_SCHEMA = "flowlab.full-ogrid-straight-pipe-verification-contract.v1"
CASE_ID = "full-ogrid-straight-pipe-v1"
CONVERGENCE_TAIL_SAMPLES = 50

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "validation"
    / "full-ogrid-straight-pipe"
    / "VERIFICATION_CONTRACT_V1.json"
)
RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK.md")
FROZEN_SOURCE_PATHS = (
    "server/flowlab/adapters.py",
    "server/flowlab/execution.py",
    "server/flowlab/full_ogrid.py",
    "server/flowlab/full_ogrid_straight_pipe_campaign.py",
    "server/flowlab/mesh.py",
    "server/flowlab/results.py",
    "server/flowlab/schemas.py",
    "server/flowlab/verification.py",
    "docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V1.json",
    "docs/validation/full-ogrid-straight-pipe/RUNBOOK.md",
)


class FullOGridCampaignError(RuntimeError):
    """Raised when a frozen campaign stage cannot complete honestly."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FullOGridCampaignError(f"could not read required JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise FullOGridCampaignError(f"required JSON artifact is not an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_contract() -> dict[str, Any]:
    contract = _read_json(CONTRACT_PATH)
    if (
        contract.get("schema") != CONTRACT_SCHEMA
        or contract.get("status") != "prospective-frozen-before-retained-scientific-execution"
        or contract.get("promotionAuthorized") is not False
    ):
        raise FullOGridCampaignError("unsupported or unfrozen full O-grid verification contract")
    levels = contract.get("levels")
    if not isinstance(levels, list) or [item.get("id") for item in levels if isinstance(item, dict)] != [
        "coarse",
        "medium",
        "fine",
    ]:
        raise FullOGridCampaignError("the full O-grid contract must freeze coarse, medium, and fine levels")
    return contract


def _reference(contract: dict[str, Any]) -> dict[str, float]:
    physical = contract["physicalCase"]
    reference = straight_pipe_reference(
        StraightPipeSpec(
            length_m=float(physical["lengthM"]),
            radius_m=float(physical["radiusM"]),
            density_kg_m3=float(physical["densityKgPerM3"]),
            dynamic_viscosity_pa_s=float(physical["dynamicViscosityPaS"]),
            volumetric_flow_rate_m3_s=float(physical["volumetricFlowRateM3PerS"]),
        )
    )
    reference["centerlineVelocityMPerS"] = 2.0 * reference["meanVelocityMPerS"]
    declared = {
        "pressureDropPa": float(physical["analyticPressureDropPa"]),
        "meanVelocityMPerS": float(physical["analyticMeanVelocityMPerS"]),
        "centerlineVelocityMPerS": float(physical["analyticCenterlineVelocityMPerS"]),
        "reynoldsNumber": float(physical["reynoldsNumber"]),
    }
    for key, expected in declared.items():
        if not math.isclose(float(reference[key]), expected, rel_tol=1.0e-12, abs_tol=1.0e-15):
            raise FullOGridCampaignError(f"frozen analytical value {key} is inconsistent with its SI inputs")
    if reference["reynoldsNumber"] >= float(physical["laminarUpperBound"]):
        raise FullOGridCampaignError("the frozen campaign is outside its laminar Reynolds-number bound")
    return reference


def _level_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in contract["levels"] if isinstance(item, dict)]


def _project(contract: dict[str, Any], level: dict[str, Any]) -> dict[str, Any]:
    physical = contract["physicalCase"]
    length = float(physical["lengthM"])
    radius = float(physical["radiusM"])
    return {
        "version": 1,
        "name": f"Full O-grid straight-pipe verification candidate ({level['id']})",
        "fluid": {
            "density": float(physical["densityKgPerM3"]),
            "dynamicViscosity": float(physical["dynamicViscosityPaS"]),
            "temperature": 293.15,
            "vaporPressure": 2340.0,
            "bulkModulus": 2.2e9,
        },
        "nodes": {
            "source": {
                "id": "source",
                "type": "source",
                "position": {"x": 0.0, "y": 0.0},
                "rotation": 0.0,
                "pressure": 101325.0,
            },
            "sink": {
                "id": "sink",
                "type": "sink",
                "position": {"x": 1000.0, "y": 0.0},
                "rotation": 0.0,
                "pressure": 101325.0,
                "flowDemand": float(physical["volumetricFlowRateM3PerS"]),
            },
        },
        "edges": {
            "straight-pipe": {
                "id": "straight-pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": length,
                "shape": {"kind": "circular", "diameter": 2.0 * radius},
            }
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": str(level["id"]),
            "runMode": "steady",
            "meshMode": "full-ogrid",
            "meshControls": {
                "fullOGridAxialCells": int(level["axialCells"]),
                "fullOGridAnnularRadialCells": int(level["annularRadialCells"]),
                "fullOGridCircumferentialCells": int(level["circumferentialCells"]),
                "fullOGridCoreCellsPerSide": int(level["coreCellsPerSide"]),
            },
            "fullOGridVerification": {
                "contractId": contract["productRequest"]["verificationContractId"],
                "boundaryCondition": contract["productRequest"]["verificationBoundaryCondition"],
                "lengthM": length,
                "volumetricFlowRateM3PerS": float(physical["volumetricFlowRateM3PerS"]),
            },
            "maxIterations": int(contract["productRequest"]["maxIterations"]),
            "tolerance": float(contract["productRequest"]["residualControl"]["p"]),
        },
        "visualization": {
            "mode": "simulate",
            "overlay": "pressure",
            "particles": False,
            "streamlines": True,
            "grid": True,
        },
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
    }


def build_level_case(level: dict[str, Any], contract: dict[str, Any] | None = None) -> SolverCase:
    selected = contract or load_contract()
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_project(selected, level),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    issues = validate_solver_case(case)
    if issues:
        raise FullOGridCampaignError(
            f"{level['id']} product-path case failed generated-case validation: " + "; ".join(issues)
        )
    profile = json.loads(case.files["constant/flowlab_full_ogrid_profile.json"])
    topology = profile.get("topology", {})
    resolution = topology.get("resolution", {}) if isinstance(topology, dict) else {}
    expected = {
        "axialCells": int(level["axialCells"]),
        "annularRadialCells": int(level["annularRadialCells"]),
        "circumferentialCells": int(level["circumferentialCells"]),
        "coreCellsPerSide": int(level["coreCellsPerSide"]),
        "cellCount": int(level["expectedCellCount"]),
    }
    if (
        profile.get("schema") != FULL_OGRID_PROFILE_SCHEMA
        or profile.get("verificationContract", {}).get("contractId")
        != selected["productRequest"]["verificationContractId"]
        or any(int(resolution.get(key, -1)) != value for key, value in expected.items())
    ):
        raise FullOGridCampaignError(f"{level['id']} generated profile does not match the frozen level")
    if "p               1e-8;" not in case.files["system/fvSolution"] or "U               1e-8;" not in case.files[
        "system/fvSolution"
    ]:
        raise FullOGridCampaignError("full O-grid verification cases require the frozen 1e-8 residual control")
    return case


def _case_file_hashes(case: SolverCase) -> dict[str, str]:
    return {path: _sha256_text(text) for path, text in sorted(case.files.items())}


def materialize_campaign(output_dir: Path) -> dict[str, Any]:
    """Materialize and determinism-check the frozen cases without executing."""

    contract = load_contract()
    reference = _reference(contract)
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FullOGridCampaignError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    level_records: list[dict[str, Any]] = []
    for level in _level_rows(contract):
        first = build_level_case(level, contract)
        second = build_level_case(level, contract)
        first_hashes = _case_file_hashes(first)
        second_hashes = _case_file_hashes(second)
        if first_hashes != second_hashes:
            raise FullOGridCampaignError(f"{level['id']} generated case is not deterministic")
        case_dir = output_dir / "cases" / str(level["id"])
        materialize_case_files(first, case_dir)
        profile = json.loads(first.files["constant/flowlab_full_ogrid_profile.json"])
        topology = profile["topology"]
        cell_count = int(level["expectedCellCount"])
        analytic_volume = math.pi * float(contract["physicalCase"]["radiusM"]) ** 2 * float(
            contract["physicalCase"]["lengthM"]
        )
        level_manifest = {
            "schema": LEVEL_SCHEMA,
            "caseId": CASE_ID,
            "level": level["id"],
            "status": "materialized-pending-real-run",
            "scientificStatus": "experimental-candidate",
            "validated": False,
            "promotionAuthorized": False,
            "solverCaseId": first.id,
            "caseDirectory": str(Path("cases") / str(level["id"])),
            "mesh": {
                "representation": profile["effectiveMeshMode"],
                "spatialDimension": 3,
                "axialCells": int(level["axialCells"]),
                "annularRadialCells": int(level["annularRadialCells"]),
                "circumferentialCells": int(level["circumferentialCells"]),
                "coreCellsPerSide": int(level["coreCellsPerSide"]),
                "cellCount": cell_count,
                "characteristicCellSizeM": (analytic_volume / cell_count) ** (1.0 / 3.0),
                "refinementRatioFromPrevious": level["refinementRatioFromPrevious"],
                "wallGeometry": topology["wallGeometry"],
            },
            "expectedPatches": topology["patches"],
            "collapsedAxisCells": topology["collapsedAxisCells"],
            "determinism": {
                "independentSecondBuild": True,
                "generatedFileCount": len(first_hashes),
                "generatedFileHashesMatch": True,
                "generatedFileTreeSha256": hashlib.sha256(
                    "".join(f"{path}\0{digest}\n" for path, digest in first_hashes.items()).encode("utf-8")
                ).hexdigest(),
            },
            "generatedCaseManifestSha256": _sha256_file(case_dir / adapters.CASE_MANIFEST_PATH),
            "runCommand": first.runCommand,
            "executionPath": [
                "server.flowlab.adapters.generate_case",
                "server.flowlab.execution.JobManager.queue_job",
            ],
        }
        level_path = case_dir / "full-ogrid-verification-level.json"
        _write_json(level_path, level_manifest)
        level_records.append({**level_manifest, "levelManifestSha256": _sha256_file(level_path)})

    manifest = {
        "schema": CAMPAIGN_SCHEMA,
        "caseId": CASE_ID,
        "status": "materialized-pending-real-run",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "contractPath": str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "runbookSha256": _sha256_file(RUNBOOK_PATH),
        "physicalCase": contract["physicalCase"],
        "analyticReference": reference,
        "refinementInterpretation": contract["refinementInterpretation"],
        "observationOperators": contract["observationOperators"],
        "gates": contract["gates"],
        "levels": level_records,
        "sourceSha256": {
            path: _sha256_file(REPOSITORY_ROOT / path) for path in FROZEN_SOURCE_PATHS
        },
        "requiredNextActions": [
            "execute every level through FlowLab JobManager with immutable runtime provenance",
            "evaluate every retained level with the frozen operators and thresholds",
            "calculate pressure-drop observed order and GCI only where mathematically valid",
            "build and independently review the immutable evidence package",
        ],
    }
    _write_json(output_dir / "campaign-manifest.json", manifest)
    return manifest


def _run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FullOGridCampaignError(
            f"campaign provenance command could not run: {' '.join(command)}"
        ) from exc


def _source_control_identity() -> dict[str, Any]:
    commit = _run_command(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    if commit.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit.stdout.strip()):
        raise FullOGridCampaignError("could not resolve the frozen campaign source commit")
    status = _run_command(
        ["git", "status", "--porcelain", "--", *FROZEN_SOURCE_PATHS],
        cwd=REPOSITORY_ROOT,
    )
    if status.returncode != 0:
        raise FullOGridCampaignError("could not inspect the frozen campaign source state")
    if status.stdout.strip():
        raise FullOGridCampaignError(
            "refusing scientific execution with uncommitted campaign or transitive scientific source"
        )
    return {
        "commit": commit.stdout.strip(),
        "repositoryRootName": REPOSITORY_ROOT.name,
        "frozenPaths": list(FROZEN_SOURCE_PATHS),
        "frozenPathsClean": True,
        "contractSha256": _sha256_file(CONTRACT_PATH),
    }


def _runtime_environment_identity() -> dict[str, Any]:
    image = adapters._openfoam_image()
    inspect = _run_command(["docker", "image", "inspect", image])
    if inspect.returncode != 0:
        detail = inspect.stderr.strip() or inspect.stdout.strip() or f"exit {inspect.returncode}"
        raise FullOGridCampaignError(
            f"the pinned OpenFOAM image must be locally inspectable before execution: {detail}"
        )
    try:
        records = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise FullOGridCampaignError("docker image inspect returned invalid JSON") from exc
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise FullOGridCampaignError("docker image inspect did not resolve exactly one image")
    record = records[0]
    image_id = record.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise FullOGridCampaignError("docker image inspect did not return an immutable image ID")
    repo_digests = record.get("RepoDigests")
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "container": {
            "engine": "docker",
            "imageTag": image,
            "imageId": image_id,
            "repoDigests": sorted(str(value) for value in repo_digests)
            if isinstance(repo_digests, list)
            else [],
            "architecture": record.get("Architecture"),
            "os": record.get("Os"),
        },
    }


def _solver_log_path(case_dir: Path) -> Path:
    candidates = (
        case_dir / "postProcessing" / "solverLogs" / "solve.log",
        case_dir / "smoke.log",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FullOGridCampaignError(f"retained solver log is missing: {case_dir}")


def _check_mesh_path(case_dir: Path) -> Path:
    direct = case_dir / "log.checkMesh"
    if direct.is_file():
        return direct
    acceptance_path = case_dir / "mesh" / "production_mesh_acceptance.json"
    if acceptance_path.is_file():
        acceptance = _read_json(acceptance_path)
        native = acceptance.get("nativeQualityEvidence", {})
        reports = native.get("solverReports", {}) if isinstance(native, dict) else {}
        openfoam = reports.get("openfoam", {}) if isinstance(reports, dict) else {}
        runs = openfoam.get("commandRuns", []) if isinstance(openfoam, dict) else []
        for run in runs if isinstance(runs, list) else []:
            if not isinstance(run, dict) or str(run.get("command")) != "checkMesh":
                continue
            relative = run.get("logPath")
            if isinstance(relative, str) and (case_dir / relative).is_file():
                return case_dir / relative
    raise FullOGridCampaignError(f"retained checkMesh log is missing: {case_dir}")


def _latest_runtime_vtk(case_dir: Path) -> Path:
    candidates = sorted(
        path
        for path in (case_dir / "VTK").glob("case_*.vtk")
        if path.is_file()
    )
    if not candidates:
        raise FullOGridCampaignError(f"solver-produced runtime VTK is missing: {case_dir}")
    return candidates[-1]


def _require_float(text: str, patterns: Sequence[str], label: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return float(match.group(1).rstrip("."))
    raise FullOGridCampaignError(f"checkMesh report is missing {label}")


def _require_int(text: str, patterns: Sequence[str], label: str) -> int:
    return int(_require_float(text, patterns, label))


def _patch_contract(boundary_text: str) -> dict[str, dict[str, Any]]:
    patches: dict[str, dict[str, Any]] = {}
    for name, body in re.findall(
        r"(?m)^\s*([A-Za-z][A-Za-z0-9_.-]*)\s*\n\s*\{(.*?)^\s*\}",
        boundary_text,
        re.DOTALL,
    ):
        type_match = re.search(r"\btype\s+([A-Za-z][A-Za-z0-9_.-]*)\s*;", body)
        faces_match = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        if type_match and faces_match:
            patches[name] = {"type": type_match.group(1), "nFaces": int(faces_match.group(1))}
    return patches


def _mesh_command_exit_codes(case_dir: Path) -> dict[str, int | None]:
    acceptance_path = case_dir / "mesh" / "production_mesh_acceptance.json"
    if not acceptance_path.is_file():
        return {"blockMesh": None, "checkMesh": None}
    acceptance = _read_json(acceptance_path)
    native = acceptance.get("nativeQualityEvidence", {})
    reports = native.get("solverReports", {}) if isinstance(native, dict) else {}
    openfoam = reports.get("openfoam", {}) if isinstance(reports, dict) else {}
    runs = openfoam.get("commandRuns", []) if isinstance(openfoam, dict) else []
    output: dict[str, int | None] = {"blockMesh": None, "checkMesh": None}
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        command = str(run.get("command") or "").split()[0]
        if command in output:
            code = run.get("exitCode")
            output[command] = int(code) if isinstance(code, int) else None
    return output


def _field(data: dict[str, Any], kind: str, name: str) -> list[Any]:
    location = data.get("cellData") if isinstance(data.get("cellData"), dict) else {}
    fields = location.get(kind) if isinstance(location.get(kind), dict) else {}
    for key, values in fields.items():
        if str(key).lower() == name.lower() and isinstance(values, list):
            return values
    raise FullOGridCampaignError(f"solver VTK is missing cell-centred {name}")


def _velocity_profile(
    vtk: dict[str, Any],
    *,
    length_m: float,
    radius_m: float,
    mean_velocity: float,
) -> dict[str, Any]:
    points = vtk.get("points") if isinstance(vtk.get("points"), list) else []
    cells = vtk.get("cells") if isinstance(vtk.get("cells"), list) else []
    velocities = _field(vtk, "vectors", "U")
    if not points or not cells or len(velocities) != len(cells):
        raise FullOGridCampaignError("solver VTK has inconsistent points, cells, or U tuples")
    centroids: list[tuple[float, float, float]] = []
    for cell in cells:
        if not isinstance(cell, list) or len(cell) != 8:
            raise FullOGridCampaignError("full O-grid runtime VTK must contain only hexahedra")
        coordinates = [points[int(index)] for index in cell]
        centroids.append(
            tuple(sum(float(point[axis]) for point in coordinates) / 8.0 for axis in range(3))
        )
    distinct_x = sorted({round(center[0], 12) for center in centroids})
    if not distinct_x:
        raise FullOGridCampaignError("solver VTK has no axial cell-centroid planes")
    selected_x = min(distinct_x, key=lambda value: abs(value - length_m / 2.0))
    selected = [index for index, center in enumerate(centroids) if math.isclose(center[0], selected_x, abs_tol=1e-11)]
    if not selected:
        raise FullOGridCampaignError("mid-plane velocity-profile operator selected no cells")
    errors: list[float] = []
    analytic_values: list[float] = []
    axial_values: list[float] = []
    transverse_squared: list[float] = []
    for index in selected:
        center = centroids[index]
        vector = velocities[index]
        if not isinstance(vector, list | tuple) or len(vector) != 3:
            raise FullOGridCampaignError("solver VTK U tuple is malformed")
        radial_squared = center[1] ** 2 + center[2] ** 2
        analytic = 2.0 * mean_velocity * max(0.0, 1.0 - radial_squared / radius_m**2)
        axial = float(vector[0])
        analytic_values.append(analytic)
        axial_values.append(axial)
        errors.append(axial - analytic)
        transverse_squared.append(float(vector[1]) ** 2 + float(vector[2]) ** 2)
    analytic_rms = math.sqrt(sum(value**2 for value in analytic_values) / len(analytic_values))
    axial_rms = math.sqrt(sum(value**2 for value in axial_values) / len(axial_values))
    error_rms = math.sqrt(sum(value**2 for value in errors) / len(errors))
    transverse_rms = math.sqrt(sum(transverse_squared) / len(transverse_squared))
    return {
        "axialPlaneM": selected_x,
        "sampleCount": len(selected),
        "relativeL2": error_rms / analytic_rms,
        "relativeLinf": max(abs(value) for value in errors) / (2.0 * mean_velocity),
        "transverseVelocityRmsRatio": transverse_rms / max(axial_rms, 1.0e-30),
        "analyticAxialVelocityRmsMPerS": analytic_rms,
        "measuredAxialVelocityRmsMPerS": axial_rms,
    }


def _patch_metrics(case_dir: Path) -> dict[str, Any]:
    acceptance_path = case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH
    if acceptance_path.is_file():
        acceptance = _read_json(acceptance_path)
        metrics = acceptance.get("patchMetrics")
        if isinstance(metrics, dict):
            return metrics
    return collect_patch_metrics(case_dir)


def evaluate_completed_level(
    case_dir: Path,
    level: dict[str, Any],
    *,
    solver_exit_code: int = 0,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute a completed level exclusively from frozen retained artifacts."""

    selected = contract or load_contract()
    reference = _reference(selected)
    mesh_limits = selected["gates"]["meshPerLevel"]
    solver_limits = selected["gates"]["solverPerLevel"]
    physics_limits = selected["gates"]["physicsPerLevel"]
    case_dir = case_dir.resolve()

    profile_path = case_dir / "constant" / "flowlab_full_ogrid_profile.json"
    profile = _read_json(profile_path)
    if profile.get("schema") != FULL_OGRID_PROFILE_SCHEMA:
        raise FullOGridCampaignError("completed level has no supported full O-grid profile")
    resolution = profile.get("topology", {}).get("resolution", {})
    if int(resolution.get("cellCount", -1)) != int(level["expectedCellCount"]):
        raise FullOGridCampaignError("completed level cell count does not match the frozen contract")

    solver_log_path = _solver_log_path(case_dir)
    check_mesh_path = _check_mesh_path(case_dir)
    solver_log = solver_log_path.read_text(encoding="utf-8", errors="replace")
    check_mesh = check_mesh_path.read_text(encoding="utf-8", errors="replace")
    final_residuals = [
        float(value)
        for value in re.findall(
            r"(?:smoothSolver|GAMG|DICPCG|PCG):\s+Solving for [^,]+,\s+"
            r"Initial residual = [0-9.eE+-]+,\s+Final residual = ([0-9.eE+-]+)",
            solver_log,
        )
    ]
    global_continuity = [
        abs(float(value))
        for value in re.findall(
            r"time step continuity errors\s*:\s*sum local = [0-9.eE+-]+,\s*global = "
            r"([0-9.eE+-]+),",
            solver_log,
        )
    ]
    if not final_residuals or not global_continuity:
        raise FullOGridCampaignError("retained solver log is missing residual or continuity history")
    maximum_final_residual = max(final_residuals[-CONVERGENCE_TAIL_SAMPLES:])
    maximum_global_continuity = max(global_continuity[-CONVERGENCE_TAIL_SAMPLES:])
    simple_converged = bool(re.search(r"SIMPLE solution converged in \d+ iterations", solver_log))

    cell_count = _require_int(check_mesh, (r"^\s*cells:\s+(\d+)\s*$",), "cell count")
    hex_count = _require_int(check_mesh, (r"^\s*hexahedra:\s+(\d+)\s*$",), "hexahedron count")
    region_count = _require_int(
        check_mesh,
        (r"Number of regions:\s*(\d+)", r"regions:\s*(\d+)"),
        "connected region count",
    )
    quality = {
        "minimumCellVolumeM3": _require_float(check_mesh, (r"Min volume\s*=\s*([0-9.eE+-]+)",), "minimum volume"),
        "maximumAspectRatio": _require_float(check_mesh, (r"Max aspect ratio\s*=\s*([0-9.eE+-]+)",), "maximum aspect ratio"),
        "maximumNonOrthogonalityDegrees": _require_float(
            check_mesh,
            (r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)",),
            "maximum non-orthogonality",
        ),
        "maximumSkewness": _require_float(check_mesh, (r"Max skewness\s*=\s*([0-9.eE+-]+)",), "maximum skewness"),
        "minimumCellDeterminant": _require_float(
            check_mesh,
            (r"minimum determinant\s*=\s*([0-9.eE+-]+)",),
            "minimum determinant",
        ),
        "minimumFaceInterpolationWeight": _require_float(
            check_mesh,
            (r"minimum face interpolation weight\s*=\s*([0-9.eE+-]+)",),
            "minimum face interpolation weight",
        ),
        "minimumFaceVolumeRatio": _require_float(
            check_mesh,
            (r"minimum face volume ratio\s*=\s*([0-9.eE+-]+)",),
            "minimum face volume ratio",
        ),
    }
    directions = re.search(
        r"Mesh has\s+(\d+)\s+geometric.*?\n.*?Mesh has\s+(\d+)\s+solution",
        check_mesh,
        re.IGNORECASE | re.DOTALL,
    )
    if directions is None:
        raise FullOGridCampaignError("checkMesh report is missing geometric or solution direction counts")
    geometric_directions = int(directions.group(1))
    solution_directions = int(directions.group(2))
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    if not boundary_path.is_file():
        raise FullOGridCampaignError("completed level is missing the solver polyMesh boundary")
    patches = _patch_contract(boundary_path.read_text(encoding="utf-8", errors="replace"))
    expected_patches = {
        "inlet": {"type": "patch", "nFaces": int(level["expectedInletFaces"])},
        "outlet": {"type": "patch", "nFaces": int(level["expectedOutletFaces"])},
        "walls": {"type": "wall", "nFaces": int(level["expectedWallFaces"])},
    }

    vtk_path = _latest_runtime_vtk(case_dir)
    vtk = parse_vtk_result(vtk_path.read_text(encoding="utf-8", errors="replace"))
    points = vtk.get("points") if isinstance(vtk.get("points"), list) else []
    if not points:
        raise FullOGridCampaignError("runtime VTK contains no points")
    spans = [
        max(float(point[axis]) for point in points) - min(float(point[axis]) for point in points)
        for axis in range(3)
    ]
    profile_metrics = _velocity_profile(
        vtk,
        length_m=float(selected["physicalCase"]["lengthM"]),
        radius_m=float(selected["physicalCase"]["radiusM"]),
        mean_velocity=float(reference["meanVelocityMPerS"]),
    )

    patch_metrics = _patch_metrics(case_dir)
    flow_balance = patch_metrics.get("flowBalance")
    pressure_drops = patch_metrics.get("pressureDrops")
    if not isinstance(flow_balance, dict) or not isinstance(pressure_drops, list) or not pressure_drops:
        raise FullOGridCampaignError("frozen flow-rate or patch-average pressure operator is unavailable")
    pressure_record = next(
        (
            item
            for item in pressure_drops
            if isinstance(item, dict)
            and item.get("fromPatch") == "inlet"
            and item.get("toPatch") == "outlet"
        ),
        None,
    )
    if pressure_record is None:
        raise FullOGridCampaignError("patch-average operator did not produce inlet-to-outlet pressure drop")
    density = float(selected["physicalCase"]["densityKgPerM3"])
    pressure_drop = float(pressure_record["deltaP"]) * density
    inlet_flow = float(flow_balance["inletFlow"])
    outlet_flow = float(flow_balance["outletFlow"])
    measured_flow = 0.5 * (abs(inlet_flow) + abs(outlet_flow))
    target_flow = float(selected["physicalCase"]["volumetricFlowRateM3PerS"])
    flow_error = abs(measured_flow - target_flow) / target_flow
    mass_imbalance = abs(abs(outlet_flow) - abs(inlet_flow)) / max(
        abs(inlet_flow),
        abs(outlet_flow),
        1.0e-30,
    )
    pressure_error = abs(pressure_drop - float(reference["pressureDropPa"])) / float(reference["pressureDropPa"])

    command_codes = _mesh_command_exit_codes(case_dir)
    mesh_gate_values = {
        "blockMeshExit": command_codes["blockMesh"] == int(mesh_limits["blockMeshExitCode"]),
        "checkMeshExit": command_codes["checkMesh"] == int(mesh_limits["checkMeshExitCode"]),
        "meshOk": "Mesh OK." in check_mesh,
        "connectedRegion": region_count == int(mesh_limits["connectedRegions"]),
        "directions": geometric_directions == int(mesh_limits["geometricDirections"])
        and solution_directions == int(mesh_limits["solutionDirections"]),
        "allHex": cell_count == hex_count == int(level["expectedCellCount"]),
        "patches": patches == expected_patches,
        "positiveVtkSpans": all(value > 0.0 for value in spans),
        "minimumCellVolume": quality["minimumCellVolumeM3"]
        > float(mesh_limits["minimumCellVolumeM3ExclusiveLowerBound"]),
        "maximumAspectRatio": quality["maximumAspectRatio"] <= float(mesh_limits["maximumAspectRatio"]),
        "maximumNonOrthogonality": quality["maximumNonOrthogonalityDegrees"]
        <= float(mesh_limits["maximumNonOrthogonalityDegrees"]),
        "maximumSkewness": quality["maximumSkewness"] <= float(mesh_limits["maximumSkewness"]),
        "minimumCellDeterminant": quality["minimumCellDeterminant"]
        >= float(mesh_limits["minimumCellDeterminant"]),
        "minimumFaceInterpolationWeight": quality["minimumFaceInterpolationWeight"]
        >= float(mesh_limits["minimumFaceInterpolationWeight"]),
        "minimumFaceVolumeRatio": quality["minimumFaceVolumeRatio"]
        >= float(mesh_limits["minimumFaceVolumeRatio"]),
        "collapsedAxisCells": int(profile["topology"]["collapsedAxisCells"])
        == int(mesh_limits["collapsedAxisCells"]),
    }
    solver_gate_values = {
        "exitCode": solver_exit_code == int(solver_limits["exitCode"]),
        "simpleConvergence": simple_converged,
        "maximumFinalLinearResidual": maximum_final_residual
        <= float(solver_limits["maximumFinalLinearResidual"]),
        "maximumAbsoluteGlobalContinuityError": maximum_global_continuity
        <= float(solver_limits["maximumAbsoluteGlobalContinuityError"]),
    }
    physics_gate_values = {
        "relativeFlowImbalance": mass_imbalance <= float(physics_limits["maximumRelativeFlowImbalance"]),
        "relativeFlowRateError": flow_error <= float(physics_limits["maximumRelativeFlowRateError"]),
        "pressureDropRelativeError": pressure_error <= float(physics_limits["maximumPressureDropRelativeError"]),
        "velocityProfileRelativeL2": profile_metrics["relativeL2"]
        <= float(physics_limits["maximumVelocityProfileRelativeL2"]),
        "velocityProfileRelativeLinf": profile_metrics["relativeLinf"]
        <= float(physics_limits["maximumVelocityProfileRelativeLinf"]),
        "transverseVelocityRmsRatio": profile_metrics["transverseVelocityRmsRatio"]
        <= float(physics_limits["maximumTransverseVelocityRmsRatio"]),
    }
    gates = {
        "mesh": {
            "passed": all(mesh_gate_values.values()),
            "checks": mesh_gate_values,
        },
        "solver": {
            "passed": all(solver_gate_values.values()),
            "checks": solver_gate_values,
        },
        "physics": {
            "passed": all(physics_gate_values.values()),
            "checks": physics_gate_values,
        },
    }
    return {
        "schema": LEVEL_SCHEMA,
        "caseId": CASE_ID,
        "level": level["id"],
        "status": "captured-evaluated-experimental-candidate",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "allPerLevelGatesPassed": all(bool(group["passed"]) for group in gates.values()),
        "gates": gates,
        "mesh": {
            "cellCount": cell_count,
            "hexahedronCount": hex_count,
            "connectedRegions": region_count,
            "geometricDirections": geometric_directions,
            "solutionDirections": solution_directions,
            "patches": patches,
            "quality": quality,
            "runtimeVtkPath": str(vtk_path.relative_to(case_dir)),
            "runtimeVtkSpansM": spans,
            "wallGeometry": profile["topology"]["wallGeometry"],
        },
        "solver": {
            "exitCode": solver_exit_code,
            "simpleConverged": simple_converged,
            "maximumFinalLinearResidual": maximum_final_residual,
            "maximumAbsoluteGlobalContinuityError": maximum_global_continuity,
            "parsedFinalResidualCount": len(final_residuals),
            "parsedContinuityCount": len(global_continuity),
        },
        "qoi": {
            "pressureDropPa": pressure_drop,
            "pressureDropRelativeError": pressure_error,
            "inletVolumetricFlowRateM3PerS": inlet_flow,
            "outletVolumetricFlowRateM3PerS": outlet_flow,
            "measuredVolumetricFlowRateM3PerS": measured_flow,
            "targetVolumetricFlowRateM3PerS": target_flow,
            "flowRateRelativeError": flow_error,
            "relativeMassFlowImbalance": mass_imbalance,
            "velocityProfile": profile_metrics,
        },
        "reference": reference,
        "provenance": {
            "profileSha256": _sha256_file(profile_path),
            "caseManifestSha256": _sha256_file(case_dir / adapters.CASE_MANIFEST_PATH),
            "solverLogSha256": _sha256_file(solver_log_path),
            "checkMeshSha256": _sha256_file(check_mesh_path),
            "polyMeshBoundarySha256": _sha256_file(boundary_path),
            "runtimeVtkSha256": _sha256_file(vtk_path),
            "diagnosticsAcceptanceSha256": (
                _sha256_file(case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH)
                if (case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH).is_file()
                else None
            ),
        },
    }


def execute_campaign(
    output_dir: Path,
    *,
    poll_interval_seconds: float = 0.25,
    timeout_seconds_per_level: float = 7200.0,
) -> dict[str, Any]:
    """Run all levels through JobManager, retaining partial failures in place."""

    if poll_interval_seconds <= 0.0 or timeout_seconds_per_level <= 0.0:
        raise FullOGridCampaignError("poll and timeout values must be positive")
    manifest = materialize_campaign(output_dir)
    output_dir = output_dir.resolve()
    contract = load_contract()
    source_control = _source_control_identity()
    runtime_environment = _runtime_environment_identity()
    manager = JobManager(runtime_root=output_dir / "runtime")
    state: dict[str, Any] = {
        "schema": RUN_RESULT_SCHEMA,
        "caseId": CASE_ID,
        "status": "running",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "campaignManifestSha256": _sha256_file(output_dir / "campaign-manifest.json"),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "sourceControl": source_control,
        "runtimeEnvironment": runtime_environment,
        "levels": [],
    }
    _write_json(output_dir / "campaign-run-state.json", state)

    for level in _level_rows(contract):
        case = build_level_case(level, contract)
        queued = manager.queue_job(case)
        record: dict[str, Any] = {
            "level": level["id"],
            "resolution": {
                key: level[key]
                for key in (
                    "axialCells",
                    "annularRadialCells",
                    "circumferentialCells",
                    "coreCellsPerSide",
                    "expectedCellCount",
                )
            },
            "solverCaseId": case.id,
            "jobId": queued.id,
            "status": queued.status,
            "execution": queued.execution,
            "command": queued.command,
            "caseDirectory": None,
            "evaluationPath": None,
        }
        state["levels"].append(record)
        _write_json(output_dir / "campaign-run-state.json", state)
        deadline = time.monotonic() + timeout_seconds_per_level
        terminal = queued
        while terminal.status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                manager.cancel_job(terminal.id)
                record["status"] = "cancelled-timeout"
                record["error"] = (
                    f"level exceeded the frozen {timeout_seconds_per_level:g}-second execution timeout"
                )
                state["status"] = "failed-retained"
                _write_json(output_dir / "campaign-run-state.json", state)
                raise FullOGridCampaignError(record["error"])
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                record["status"] = "missing-job-record"
                state["status"] = "failed-retained"
                _write_json(output_dir / "campaign-run-state.json", state)
                raise FullOGridCampaignError(f"JobManager lost the retained {level['id']} job record")
            terminal = refreshed
        record.update(
            {
                "status": terminal.status,
                "exitCode": terminal.exitCode,
                "finishedAt": terminal.finishedAt,
                "execution": terminal.execution,
                "command": terminal.command,
                "error": terminal.error,
            }
        )
        if terminal.caseDir:
            case_dir = Path(terminal.caseDir).resolve()
            if not case_dir.is_relative_to(output_dir):
                raise FullOGridCampaignError(
                    f"JobManager placed {level['id']} evidence outside the campaign directory"
                )
            record["caseDirectory"] = str(case_dir.relative_to(output_dir))
        else:
            case_dir = output_dir / "missing-case-directory"
        _write_json(output_dir / "campaign-run-state.json", state)
        if terminal.status != "complete" or terminal.exitCode != 0 or not terminal.caseDir:
            state["status"] = "failed-retained"
            _write_json(output_dir / "campaign-run-state.json", state)
            raise FullOGridCampaignError(
                f"{level['id']} product-path level did not complete: status={terminal.status}, "
                f"exitCode={terminal.exitCode}, error={terminal.error}"
            )
        evaluation = evaluate_completed_level(
            case_dir,
            level,
            solver_exit_code=int(terminal.exitCode),
            contract=contract,
        )
        evaluation["jobId"] = terminal.id
        evaluation["solverCaseId"] = case.id
        evaluation["characteristicCellSizeM"] = next(
            float(item["mesh"]["characteristicCellSizeM"])
            for item in manifest["levels"]
            if item["level"] == level["id"]
        )
        evaluation_path = output_dir / "evaluations" / f"{level['id']}.json"
        _write_json(evaluation_path, evaluation)
        record["evaluationPath"] = str(evaluation_path.relative_to(output_dir))
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allPerLevelGatesPassed"] = bool(evaluation["allPerLevelGatesPassed"])
        _write_json(output_dir / "campaign-run-state.json", state)

    result = {
        **state,
        "status": "completed-evaluated-experimental-candidate",
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "allPerLevelGatesPassed": all(
            bool(level.get("allPerLevelGatesPassed")) for level in state["levels"]
        ),
        "requiredNextActions": [
            "apply the frozen three-level sequence and fine-level gates",
            "build and independently hash the immutable evidence package",
            "obtain controlled independent review before any fixture, registry, validation, or claim mutation",
        ],
    }
    _write_json(output_dir / "campaign-result.json", result)
    state["status"] = result["status"]
    state["finishedAt"] = result["finishedAt"]
    _write_json(output_dir / "campaign-run-state.json", state)
    return result


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def _write_deterministic_tar(output_path: Path, entries: Sequence[tuple[Path, str]]) -> None:
    if not entries:
        raise FullOGridCampaignError(f"cannot create empty evidence archive: {output_path.name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for source, archive_name in sorted(entries, key=lambda item: item[1]):
            if not source.is_file():
                raise FullOGridCampaignError(f"required campaign evidence file is missing: {source}")
            archive.add(source, arcname=archive_name, recursive=False, filter=_tar_filter)


def _freeze_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(child, 0o555 if child.is_dir() else 0o444)
    os.chmod(path, 0o555)


def _residual_history(case_dirs: dict[str, Path]) -> dict[str, Any]:
    pattern = re.compile(
        r"(?:smoothSolver|GAMG|DICPCG|PCG):\s+Solving for ([^,]+),\s+"
        r"Initial residual = ([0-9.eE+-]+),\s+Final residual = ([0-9.eE+-]+),\s+"
        r"No Iterations (\d+)"
    )
    levels = []
    for level in ("coarse", "medium", "fine"):
        path = _solver_log_path(case_dirs[level])
        rows = [
            {
                "field": field.strip(),
                "initialResidual": float(initial),
                "finalResidual": float(final),
                "linearIterations": int(iterations),
            }
            for field, initial, final, iterations in pattern.findall(
                path.read_text(encoding="utf-8", errors="replace")
            )
        ]
        if not rows:
            raise FullOGridCampaignError(f"{level} retained solver log has no residual history")
        levels.append(
            {
                "level": level,
                "sourceLogSha256": _sha256_file(path),
                "sampleCount": len(rows),
                "samples": rows,
            }
        )
    return {
        "schema": "flowlab.full-ogrid-straight-pipe-residual-history.v1",
        "caseId": CASE_ID,
        "levels": levels,
    }


def _sequence_assessment(
    evaluations: dict[str, dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    level_rows = _level_rows(contract)
    samples = [
        {
            "id": str(level["id"]),
            "source": "solver-produced",
            "sourceArtifactSha256": evaluations[str(level["id"])]["provenance"]["runtimeVtkSha256"],
            "characteristicCellSizeM": float(evaluations[str(level["id"])]["characteristicCellSizeM"]),
            "value": float(evaluations[str(level["id"])]["qoi"]["pressureDropPa"]),
        }
        for level in level_rows
    ]
    try:
        grid = richardson_grid_convergence(samples)
        grid_record: dict[str, Any] = {"qualified": True, **grid}
    except Exception as exc:
        grid_record = {
            "qualified": False,
            "method": "three-grid-Richardson-extrapolation-with-GCI",
            "reason": str(exc),
            "samples": samples,
        }
    sequence_limits = contract["gates"]["sequence"]
    deficits = [
        float(evaluations[str(level["id"])]["mesh"]["wallGeometry"]["areaRelativeDeficit"])
        for level in level_rows
    ]
    ratios = [deficits[index] / deficits[index + 1] for index in range(2)]
    pressure_values = [float(sample["value"]) for sample in samples]
    monotone_pressure = (
        pressure_values[0] < pressure_values[1] < pressure_values[2]
        or pressure_values[0] > pressure_values[1] > pressure_values[2]
    )
    gates = {
        "pressureDropMonotone": monotone_pressure,
        "gciMathematicallyQualified": bool(grid_record.get("qualified")),
        "observedOrder": bool(grid_record.get("qualified"))
        and float(sequence_limits["minimumObservedOrder"])
        <= float(grid_record["observedOrder"])
        <= float(sequence_limits["maximumObservedOrder"]),
        "fineGridGci": bool(grid_record.get("qualified"))
        and float(grid_record["fineGridGciPercent"])
        <= float(sequence_limits["maximumFineGridGciPercent"]),
        "polygonAreaDeficitMonotone": deficits[0] > deficits[1] > deficits[2] > 0.0,
        "polygonAreaDeficitRatios": all(
            float(sequence_limits["polygonAreaDeficitRatioMinimum"])
            <= ratio
            <= float(sequence_limits["polygonAreaDeficitRatioMaximum"])
            for ratio in ratios
        ),
    }
    return {
        "gridConvergence": grid_record,
        "pressureDropPa": pressure_values,
        "polygonAreaRelativeDeficits": deficits,
        "polygonAreaDeficitRatios": ratios,
        "gates": gates,
        "passed": all(gates.values()),
        "interpretation": contract["refinementInterpretation"]["geometryTreatment"],
    }


def build_evidence_package(campaign_dir: Path, *, freeze: bool = True) -> dict[str, Any]:
    """Build a content-addressed candidate package, preserving pass or failure."""

    campaign_dir = campaign_dir.resolve()
    run_result = _read_json(campaign_dir / "campaign-result.json")
    if (
        run_result.get("schema") != RUN_RESULT_SCHEMA
        or run_result.get("status") != "completed-evaluated-experimental-candidate"
        or len(run_result.get("levels", [])) != 3
    ):
        raise FullOGridCampaignError(
            "immutable packaging requires one completed evaluated three-level campaign"
        )
    package_dir = campaign_dir / "immutable-evidence-package"
    if package_dir.exists():
        raise FullOGridCampaignError(f"refusing to overwrite existing evidence package: {package_dir}")
    package_dir.mkdir(parents=True)
    contract = load_contract()
    run_levels = {
        str(item["level"]): item
        for item in run_result["levels"]
        if isinstance(item, dict) and item.get("level") in {"coarse", "medium", "fine"}
    }
    if set(run_levels) != {"coarse", "medium", "fine"}:
        raise FullOGridCampaignError("campaign result is missing a frozen mesh level")
    case_dirs: dict[str, Path] = {}
    evaluations: dict[str, dict[str, Any]] = {}
    for level in ("coarse", "medium", "fine"):
        record = run_levels[level]
        case_dir = (campaign_dir / str(record["caseDirectory"])).resolve()
        evaluation_path = (campaign_dir / str(record["evaluationPath"])).resolve()
        if not case_dir.is_relative_to(campaign_dir) or not evaluation_path.is_relative_to(campaign_dir):
            raise FullOGridCampaignError(f"{level} retained evidence path escapes the campaign")
        evaluation = _read_json(evaluation_path)
        if evaluation.get("schema") != LEVEL_SCHEMA or evaluation.get("level") != level:
            raise FullOGridCampaignError(f"{level} evaluation does not match the frozen campaign")
        case_dirs[level] = case_dir
        evaluations[level] = evaluation

    archive_specs: dict[str, list[tuple[Path, str]]] = {
        "case-manifest": [],
        "mesh-artifact": [],
        "solver-log": [],
        "raw-result-fields": [],
        "runtime-provenance": [],
        "patch-metrics": [],
    }
    for level in ("coarse", "medium", "fine"):
        case_dir = case_dirs[level]
        archive_specs["case-manifest"].append(
            (case_dir / adapters.CASE_MANIFEST_PATH, f"{level}/{adapters.CASE_MANIFEST_PATH}")
        )
        archive_specs["case-manifest"].append(
            (
                campaign_dir / "cases" / level / "full-ogrid-verification-level.json",
                f"{level}/full-ogrid-verification-level.json",
            )
        )
        poly_mesh = case_dir / "constant" / "polyMesh"
        for path in sorted(poly_mesh.rglob("*")):
            if path.is_file():
                archive_specs["mesh-artifact"].append(
                    (path, f"{level}/constant/polyMesh/{path.relative_to(poly_mesh).as_posix()}")
                )
        check_mesh = _check_mesh_path(case_dir)
        archive_specs["mesh-artifact"].append((check_mesh, f"{level}/log.checkMesh"))
        solver_log = _solver_log_path(case_dir)
        archive_specs["solver-log"].append((solver_log, f"{level}/solve.log"))
        runtime_vtk = _latest_runtime_vtk(case_dir)
        archive_specs["raw-result-fields"].append((runtime_vtk, f"{level}/VTK/{runtime_vtk.name}"))
        job_dir = case_dir.parent
        for path, archive_name in (
            (job_dir / JOB_RECORD_FILENAME, f"{level}/{JOB_RECORD_FILENAME}"),
            (job_dir / "flowlab_case_record.json", f"{level}/flowlab_case_record.json"),
            (
                case_dir / "constant" / "flowlab_openfoam_runtime.json",
                f"{level}/flowlab_openfoam_runtime.json",
            ),
            (
                campaign_dir / str(run_levels[level]["evaluationPath"]),
                f"{level}/evaluation.json",
            ),
        ):
            archive_specs["runtime-provenance"].append((path, archive_name))
        diagnostics = case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH
        archive_specs["patch-metrics"].append(
            (diagnostics, f"{level}/{OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH}")
        )
        post_root = case_dir / "postProcessing"
        for path in sorted(post_root.rglob("*")):
            if path.is_file() and any(
                token in str(path.relative_to(case_dir)).lower()
                for token in ("patchflowrate", "patchaverage", "flowratepatch")
            ):
                archive_specs["patch-metrics"].append(
                    (path, f"{level}/{path.relative_to(case_dir).as_posix()}")
                )

    artifact_paths: dict[str, Path] = {}
    for kind, entries in archive_specs.items():
        path = package_dir / f"{kind}.tar"
        _write_deterministic_tar(path, entries)
        artifact_paths[kind] = path
    residual_path = package_dir / "residual-history.json"
    _write_json(residual_path, _residual_history(case_dirs))
    artifact_paths["residual-history"] = residual_path

    sequence = _sequence_assessment(evaluations, contract)
    fine = evaluations["fine"]
    fine_limits = contract["gates"]["fineLevel"]
    fine_gates = {
        "pressureDropRelativeError": float(fine["qoi"]["pressureDropRelativeError"])
        <= float(fine_limits["maximumPressureDropRelativeError"]),
        "velocityProfileRelativeL2": float(fine["qoi"]["velocityProfile"]["relativeL2"])
        <= float(fine_limits["maximumVelocityProfileRelativeL2"]),
        "velocityProfileRelativeLinf": float(fine["qoi"]["velocityProfile"]["relativeLinf"])
        <= float(fine_limits["maximumVelocityProfileRelativeLinf"]),
        "polygonAreaRelativeDeficit": float(fine["mesh"]["wallGeometry"]["areaRelativeDeficit"])
        <= float(fine_limits["maximumPolygonAreaRelativeDeficit"]),
    }
    campaign_gates = {
        "deterministicGeneration": all(
            bool(item["determinism"]["generatedFileHashesMatch"])
            for item in _read_json(campaign_dir / "campaign-manifest.json")["levels"]
        ),
        "allPerLevelGates": all(
            bool(evaluations[level]["allPerLevelGatesPassed"])
            for level in ("coarse", "medium", "fine")
        ),
        "allFineLevelGates": all(fine_gates.values()),
        "sequence": bool(sequence["passed"]),
    }
    evidence = {
        "schema": "flowlab.full-ogrid-straight-pipe-evidence.v1",
        "caseId": CASE_ID,
        "scientificStatus": (
            "verification-candidate-awaiting-independent-review"
            if all(campaign_gates.values())
            else "verification-candidate-gates-failed"
        ),
        "validated": False,
        "promotionAuthorized": False,
        "claim": contract["claim"],
        "physicalCase": contract["physicalCase"],
        "refinementInterpretation": contract["refinementInterpretation"],
        "observationOperators": contract["observationOperators"],
        "levels": [
            {
                "level": level,
                "characteristicCellSizeM": evaluations[level]["characteristicCellSizeM"],
                "allPerLevelGatesPassed": evaluations[level]["allPerLevelGatesPassed"],
                "mesh": evaluations[level]["mesh"],
                "solver": evaluations[level]["solver"],
                "qoi": evaluations[level]["qoi"],
            }
            for level in ("coarse", "medium", "fine")
        ],
        "fineLevelGates": fine_gates,
        "sequenceAssessment": sequence,
        "campaignGates": campaign_gates,
        "allCandidateGatesPassed": all(campaign_gates.values()),
        "provenance": {
            "contractSha256": _sha256_file(CONTRACT_PATH),
            "runbookSha256": _sha256_file(RUNBOOK_PATH),
            "sourceCommit": run_result["sourceControl"]["commit"],
            "runtimeEnvironment": run_result["runtimeEnvironment"],
            "postprocessingMethod": (
                "server.flowlab.full_ogrid_straight_pipe_campaign."
                "evaluate_completed_level/build_evidence_package"
            ),
        },
    }
    evidence_path = package_dir / "evidence.json"
    _write_json(evidence_path, evidence)
    artifact_paths["evidence"] = evidence_path

    contract_copy = package_dir / CONTRACT_PATH.name
    contract_copy.write_bytes(CONTRACT_PATH.read_bytes())
    artifact_paths["verification-contract"] = contract_copy
    runbook_copy = package_dir / RUNBOOK_PATH.name
    runbook_copy.write_bytes(RUNBOOK_PATH.read_bytes())
    artifact_paths["runbook"] = runbook_copy
    for source_name in ("campaign-manifest.json", "campaign-result.json"):
        destination = package_dir / source_name
        destination.write_bytes((campaign_dir / source_name).read_bytes())
        artifact_paths[source_name.removesuffix(".json")] = destination

    artifact_records = [
        {
            "kind": kind,
            "path": path.name,
            "sizeBytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for kind, path in sorted(artifact_paths.items())
    ]
    artifact_index_path = package_dir / "artifact-index.json"
    _write_json(
        artifact_index_path,
        {
            "schema": "flowlab.full-ogrid-straight-pipe-artifact-index.v1",
            "caseId": CASE_ID,
            "artifacts": artifact_records,
        },
    )
    included = sorted([*artifact_paths.values(), artifact_index_path], key=lambda path: path.name)
    tree_lines = [f"{path.name} {_sha256_file(path)} {path.stat().st_size}" for path in included]
    tree_digest = hashlib.sha256(("\n".join(tree_lines) + "\n").encode("utf-8")).hexdigest()
    package_manifest = {
        "schema": PACKAGE_MANIFEST_SCHEMA,
        "caseId": CASE_ID,
        "status": (
            "candidate-gates-passed-awaiting-independent-review"
            if all(campaign_gates.values())
            else "candidate-gates-failed"
        ),
        "scientificStatus": "verification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "campaignResultSha256": _sha256_file(campaign_dir / "campaign-result.json"),
        "sourceCommit": run_result["sourceControl"]["commit"],
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "runtimeEnvironment": run_result["runtimeEnvironment"],
        "campaignGates": campaign_gates,
        "allCandidateGatesPassed": all(campaign_gates.values()),
        "fineLevelGates": fine_gates,
        "sequenceAssessment": sequence,
        "artifactIndexSha256": _sha256_file(artifact_index_path),
        "treeDigestSha256": tree_digest,
        "immutable": True,
        "reviewStatus": "pending-controlled-independent-review",
        "fixtureMutationAuthorized": False,
        "registryMutationAuthorized": False,
        "validationStateChangeAuthorized": False,
        "productClaimChangeAuthorized": False,
    }
    package_manifest_path = package_dir / "package-manifest.json"
    _write_json(package_manifest_path, package_manifest)
    review_request = {
        "schema": "flowlab.full-ogrid-straight-pipe-review-request.v1",
        "caseId": CASE_ID,
        "status": "pending-controlled-independent-review",
        "reviewerIndependenceRequired": True,
        "packagePath": str(package_dir.relative_to(campaign_dir)),
        "packageManifestSha256": _sha256_file(package_manifest_path),
        "packageTreeDigestSha256": tree_digest,
        "reviewMustVerify": [
            "contract prospectivity and exact contract digest",
            "source commit and clean frozen transitive paths",
            "Docker image identity and detected OpenFOAM version",
            "all three JobManager records and terminal solver states",
            "deterministic generated-case hashes",
            "full-volume all-hex topology, patch roles, and mesh quality at every level",
            "frozen pressure, conservation, profile, geometry, Richardson, and GCI operators",
            "every package artifact hash and the package tree digest",
            "bounded claim and absence of fixture, registry, validation, promotion, or release mutation",
        ],
        "fixtureMutationAuthorized": False,
        "registryMutationAuthorized": False,
        "validationStateChangeAuthorized": False,
        "productClaimChangeAuthorized": False,
    }
    _write_json(campaign_dir / "independent-review-request.json", review_request)
    if freeze:
        _freeze_tree(package_dir)
    return package_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--materialize-only", action="store_true")
    actions.add_argument("--run-and-package", action="store_true")
    actions.add_argument("--package-completed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.materialize_only:
        result = materialize_campaign(args.output_dir)
    elif args.run_and_package:
        execute_campaign(args.output_dir)
        result = build_evidence_package(args.output_dir)
    else:
        result = build_evidence_package(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
