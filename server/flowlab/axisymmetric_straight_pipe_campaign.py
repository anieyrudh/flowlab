"""Run the governed product axisymmetric straight-pipe verification campaign.

The campaign freezes three exact logical refinements through the same
``generate_case`` and ``JobManager.queue_job`` path used by the application.
Materialization, execution, evaluation, immutable packaging, independent
review, and fixture promotion remain separate explicit stages.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
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
    TERMINAL_STATUSES,
    JobManager,
    materialize_case_files,
    validate_solver_case,
)
from .results import parse_vtk_result
from .schemas import CaseRequest, SolverCase
from .verification import (
    StraightPipeSpec,
    relative_mass_flow_imbalance,
    richardson_grid_convergence,
    straight_pipe_reference,
)


CAMPAIGN_SCHEMA = "flowlab.axisymmetric-straight-pipe-campaign.v1"
LEVEL_SCHEMA = "flowlab.axisymmetric-straight-pipe-level.v1"
RUN_RESULT_SCHEMA = "flowlab.axisymmetric-straight-pipe-run-result.v1"
PACKAGE_MANIFEST_SCHEMA = "flowlab.axisymmetric-straight-pipe-package-manifest.v1"
FIXTURE_ID = "straight-pipe"
LEVELS: tuple[tuple[str, int, int], ...] = (
    ("coarse", 16, 4),
    ("medium", 32, 8),
    ("fine", 64, 16),
)
CONVERGENCE_TAIL_SAMPLES = 50
MEAN_VELOCITY_RELATIVE_TOLERANCE = 1.0e-8
PRESSURE_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE = 1.0e-6
GLOBAL_CONTINUITY_TOLERANCE = 1.0e-10
FINAL_LINEAR_RESIDUAL_TOLERANCE = 1.0e-8
PRESSURE_DROP_RELATIVE_ERROR_LIMIT = 0.05
FINE_GRID_GCI_PERCENT_LIMIT = 1.0
MASS_FLOW_RELATIVE_IMBALANCE_LIMIT = 0.001


class AxisymmetricStraightPipeCampaignError(RuntimeError):
    """Raised when the frozen product-path campaign cannot be materialized."""


@dataclass(frozen=True)
class AxisymmetricStraightPipeSpec:
    length_m: float = 0.024
    radius_m: float = 0.006
    density_kg_m3: float = 1000.0
    dynamic_viscosity_pa_s: float = 0.001
    volumetric_flow_rate_m3_s: float = 1.0e-5

    def __post_init__(self) -> None:
        for label, value in asdict(self).items():
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise AxisymmetricStraightPipeCampaignError(f"{label} must be a finite positive SI value")
        reference = self.reference()
        if reference["reynoldsNumber"] >= 2100.0:
            raise AxisymmetricStraightPipeCampaignError("the frozen campaign must remain below Reynolds number 2100")

    def reference(self) -> dict[str, float]:
        return straight_pipe_reference(
            StraightPipeSpec(
                length_m=self.length_m,
                radius_m=self.radius_m,
                density_kg_m3=self.density_kg_m3,
                dynamic_viscosity_pa_s=self.dynamic_viscosity_pa_s,
                volumetric_flow_rate_m3_s=self.volumetric_flow_rate_m3_s,
            )
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _project(spec: AxisymmetricStraightPipeSpec, level: str, axial_cells: int, radial_cells: int) -> dict[str, Any]:
    return {
        "version": 1,
        "name": f"Axisymmetric straight-pipe benchmark candidate ({level})",
        "fluid": {
            "density": spec.density_kg_m3,
            "dynamicViscosity": spec.dynamic_viscosity_pa_s,
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
                "flowDemand": spec.volumetric_flow_rate_m3_s,
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
                "length": spec.length_m,
                "shape": {"kind": "circular", "diameter": 2.0 * spec.radius_m},
            }
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": level,
            "runMode": "steady",
            "meshMode": "axisymmetric",
            "meshControls": {
                "axisymmetricAxialCells": axial_cells,
                "axisymmetricRadialCells": radial_cells,
                "transverseDistribution": "uniform",
            },
            "axisymmetricBenchmark": {
                "fixtureId": FIXTURE_ID,
                "boundaryCondition": "periodic-pressure-gradient",
                "lengthM": spec.length_m,
                "volumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
            },
            "maxIterations": 2000,
            "tolerance": 1.0e-8,
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


def build_level_case(
    spec: AxisymmetricStraightPipeSpec,
    *,
    level: str,
    axial_cells: int,
    radial_cells: int,
) -> SolverCase:
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_project(spec, level, axial_cells, radial_cells),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    issues = validate_solver_case(case)
    if issues:
        raise AxisymmetricStraightPipeCampaignError(
            f"{level} product-path case failed generated-case validation: " + "; ".join(issues)
        )
    return case


def materialize_campaign(
    output_dir: Path,
    spec: AxisymmetricStraightPipeSpec | None = None,
) -> dict[str, Any]:
    """Materialize, but never execute or promote, the three-level campaign."""

    selected = spec or AxisymmetricStraightPipeSpec()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise AxisymmetricStraightPipeCampaignError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    level_records: list[dict[str, Any]] = []
    for level, axial_cells, radial_cells in LEVELS:
        case = build_level_case(
            selected,
            level=level,
            axial_cells=axial_cells,
            radial_cells=radial_cells,
        )
        case_dir = output_dir / "cases" / level
        materialize_case_files(case, case_dir)
        profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
        characteristic_size = max(selected.length_m / axial_cells, selected.radius_m / radial_cells)
        level_manifest = {
            "schema": LEVEL_SCHEMA,
            "fixtureId": FIXTURE_ID,
            "level": level,
            "status": "materialized-pending-real-run",
            "scientificStatus": "experimental-candidate",
            "validated": False,
            "caseId": case.id,
            "caseDirectory": str(Path("cases") / level),
            "mesh": {
                "representation": profile["effectiveMeshMode"],
                "spatialDimension": 3,
                "axialCells": axial_cells,
                "radialCells": radial_cells,
                "wedgeCells": axial_cells * radial_cells,
                "characteristicCellSizeM": characteristic_size,
                "refinementRatioFromPrevious": None if level == "coarse" else 2.0,
            },
            "benchmarkContract": profile["benchmarkContract"],
            "generatedCaseManifestSha256": _sha256_file(case_dir / adapters.CASE_MANIFEST_PATH),
            "executionPath": [
                "server.flowlab.adapters.generate_case",
                "server.flowlab.execution.JobManager.queue_job",
            ],
            "runCommand": case.runCommand,
        }
        level_path = case_dir / "axisymmetric-benchmark-level.json"
        _write_json(level_path, level_manifest)
        level_records.append({**level_manifest, "levelManifestSha256": _sha256_file(level_path)})

    module_path = Path(__file__).resolve()
    adapter_path = Path(adapters.__file__).resolve()
    fixture_path = Path(__file__).resolve().parents[2] / "benchmarks" / "cases" / FIXTURE_ID / "benchmark.json"
    manifest = {
        "schema": CAMPAIGN_SCHEMA,
        "fixtureId": FIXTURE_ID,
        "fixtureStatus": "pending-real-run",
        "status": "materialized-pending-real-run",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "spec": asdict(selected),
        "analyticReference": selected.reference(),
        "levels": level_records,
        "sourceSha256": {
            "campaignModule": _sha256_file(module_path),
            "adapter": _sha256_file(adapter_path),
            "pendingFixture": _sha256_file(fixture_path),
        },
        "requiredNextActions": [
            "execute every level through FlowLab JobManager with immutable runtime provenance",
            "extract pressure drop and signed full-circle mass flow from retained solver outputs",
            "recompute the analytic-error, conservation, and three-grid GCI gates",
            "obtain independent review before any benchmark fixture status change",
        ],
    }
    _write_json(output_dir / "campaign-manifest.json", manifest)
    return manifest


def _latest_runtime_vtk(case_dir: Path) -> Path:
    candidates = sorted((case_dir / "VTK").glob("case_*.vtk"))
    if not candidates:
        raise AxisymmetricStraightPipeCampaignError(f"solver-produced runtime VTK is missing: {case_dir}")
    return candidates[-1]


def evaluate_completed_level(case_dir: Path) -> dict[str, Any]:
    """Recompute one level's product-path QoIs and physical convergence gates.

    A zero solver exit is deliberately insufficient. The controller, signed
    fluxes, continuity, final linear solves, checkMesh report, and runtime VTK
    must all be present and pass their prospective gates.
    """

    case_dir = case_dir.resolve()
    try:
        profile = json.loads(
            (case_dir / "constant" / "flowlab_axisymmetric_profile.json").read_text(encoding="utf-8")
        )
        contract = profile["benchmarkContract"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AxisymmetricStraightPipeCampaignError(
            f"could not read the frozen axisymmetric benchmark contract from {case_dir}"
        ) from exc
    if (
        profile.get("schema") != adapters.AXISYMMETRIC_PROFILE_SCHEMA
        or not isinstance(contract, dict)
        or contract.get("schema") != adapters.AXISYMMETRIC_BENCHMARK_SCHEMA
    ):
        raise AxisymmetricStraightPipeCampaignError("unsupported or missing axisymmetric benchmark contract")

    solver_log_path = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    if not solver_log_path.is_file():
        fallback = case_dir / "smoke.log"
        solver_log_path = fallback if fallback.is_file() else solver_log_path
    check_mesh_path = case_dir / "log.checkMesh"
    if not check_mesh_path.is_file() and solver_log_path.is_file():
        # Direct Allrun smoke output may contain the checkMesh section in the
        # combined retained log. JobManager runs retain a dedicated file.
        check_mesh_path = solver_log_path
    try:
        solver_log = solver_log_path.read_text(encoding="utf-8", errors="replace")
        check_mesh = check_mesh_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AxisymmetricStraightPipeCampaignError(
            "completed level requires retained solver and checkMesh logs"
        ) from exc

    controller_history = [
        (float(mean_velocity), float(gradient))
        for mean_velocity, gradient in re.findall(
            r"Pressure gradient source:\s+uncorrected Ubar\s*=\s*([0-9.eE+-]+),\s+"
            r"pressure gradient\s*=\s*([0-9.eE+-]+)",
            solver_log,
        )
    ]
    continuity_history = [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"time step continuity errors : sum local = ([0-9.eE+-]+), global = "
            r"([0-9.eE+-]+), cumulative = ([0-9.eE+-]+)",
            solver_log,
        )
    ]
    final_residuals = [
        float(value)
        for value in re.findall(
            r"(?:smoothSolver|GAMG|DICPCG|PCG):\s+Solving for (?:Ux|Uy|Uz|p),\s+"
            r"Initial residual = [0-9.eE+-]+,\s+Final residual = ([0-9.eE+-]+)",
            solver_log,
        )
    ]
    signed_patch_flows: dict[str, float] = {}
    for patch, value in re.findall(r"sum\((inlet|outlet)\) of phi = ([0-9.eE+-]+)", solver_log):
        signed_patch_flows[patch] = float(value)
    if (
        len(controller_history) < CONVERGENCE_TAIL_SAMPLES
        or len(continuity_history) < CONVERGENCE_TAIL_SAMPLES
        or len(final_residuals) < CONVERGENCE_TAIL_SAMPLES
        or set(signed_patch_flows) != {"inlet", "outlet"}
    ):
        raise AxisymmetricStraightPipeCampaignError(
            "completed level is missing the retained controller, continuity, linear-residual, or signed-flow history"
        )

    controller_tail = controller_history[-CONVERGENCE_TAIL_SAMPLES:]
    continuity_tail = continuity_history[-CONVERGENCE_TAIL_SAMPLES:]
    residual_tail = final_residuals[-CONVERGENCE_TAIL_SAMPLES:]
    target_mean_velocity = float(contract["meanVelocityTargetMPerS"])
    mean_velocity_error = max(
        abs(value - target_mean_velocity) / target_mean_velocity for value, _gradient in controller_tail
    )
    gradients = [gradient for _velocity, gradient in controller_tail]
    gradient_scale = max(abs(value) for value in gradients)
    gradient_relative_span = (
        (max(gradients) - min(gradients)) / gradient_scale if gradient_scale > 0.0 else math.inf
    )
    maximum_global_continuity = max(abs(global_value) for _local, global_value, _cumulative in continuity_tail)
    maximum_final_residual = max(abs(value) for value in residual_tail)

    vtk_path = _latest_runtime_vtk(case_dir)
    try:
        vtk = parse_vtk_result(vtk_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise AxisymmetricStraightPipeCampaignError(f"runtime VTK could not be parsed: {vtk_path}") from exc
    points = vtk.get("points") if isinstance(vtk.get("points"), list) else []
    if not points:
        raise AxisymmetricStraightPipeCampaignError("runtime VTK contains no points")
    spans = [
        max(float(point[axis]) for point in points) - min(float(point[axis]) for point in points)
        for axis in range(3)
    ]
    mesh_ok = (
        "Mesh OK." in check_mesh
        and "Mesh has 3 solution (non-empty) directions" in check_mesh
        and all(patch in check_mesh for patch in ("inlet", "outlet", "walls", "front", "back", "axis"))
        and all(value > 0.0 for value in spans)
    )
    cell_match = re.search(r"^\s*cells:\s+(\d+)\s*$", check_mesh, flags=re.MULTILINE)
    if cell_match is None:
        raise AxisymmetricStraightPipeCampaignError("checkMesh report does not contain a cell count")
    cell_count = int(cell_match.group(1))

    full_circle_scale = float(contract["fullCircleScale"])
    density = float(contract["densityKgPerM3"])
    inlet_full_flow = signed_patch_flows["inlet"] * full_circle_scale
    outlet_full_flow = signed_patch_flows["outlet"] * full_circle_scale
    inlet_mass_flow = inlet_full_flow * density
    outlet_mass_flow = outlet_full_flow * density
    mass_imbalance = relative_mass_flow_imbalance(inlet_mass_flow, outlet_mass_flow)
    measured_full_flow = 0.5 * (abs(inlet_full_flow) + abs(outlet_full_flow))
    target_full_flow = float(contract["targetFullCircleVolumetricFlowRateM3PerS"])
    flow_error = abs(measured_full_flow - target_full_flow) / target_full_flow
    final_gradient = sum(gradients) / len(gradients)
    pressure_drop = abs(final_gradient) * float(contract["physicalLengthM"]) * density
    reference_inputs = {
        "lengthM": float(contract["physicalLengthM"]),
        "diameterM": 2.0 * float(profile["stations"][0]["radiusM"]),
        "densityKgPerM3": density,
        "dynamicViscosityPaS": float(contract["dynamicViscosityPaS"]),
        "volumetricFlowRateM3PerS": target_full_flow,
    }
    reference = straight_pipe_reference(
        StraightPipeSpec(
            length_m=reference_inputs["lengthM"],
            radius_m=reference_inputs["diameterM"] / 2.0,
            density_kg_m3=reference_inputs["densityKgPerM3"],
            dynamic_viscosity_pa_s=reference_inputs["dynamicViscosityPaS"],
            volumetric_flow_rate_m3_s=reference_inputs["volumetricFlowRateM3PerS"],
        )
    )
    pressure_error = abs(pressure_drop - reference["pressureDropPa"]) / reference["pressureDropPa"]
    gates = {
        "runtimeMesh3d": {"passed": mesh_ok, "spansM": spans},
        "meanVelocityController": {
            "passed": mean_velocity_error <= MEAN_VELOCITY_RELATIVE_TOLERANCE,
            "value": mean_velocity_error,
            "limit": MEAN_VELOCITY_RELATIVE_TOLERANCE,
        },
        "pressureGradientTail": {
            "passed": gradient_relative_span <= PRESSURE_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE,
            "value": gradient_relative_span,
            "limit": PRESSURE_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE,
        },
        "globalContinuity": {
            "passed": maximum_global_continuity <= GLOBAL_CONTINUITY_TOLERANCE,
            "value": maximum_global_continuity,
            "limit": GLOBAL_CONTINUITY_TOLERANCE,
        },
        "finalLinearResidual": {
            "passed": maximum_final_residual <= FINAL_LINEAR_RESIDUAL_TOLERANCE,
            "value": maximum_final_residual,
            "limit": FINAL_LINEAR_RESIDUAL_TOLERANCE,
        },
    }
    return {
        "schema": LEVEL_SCHEMA,
        "status": "captured-experimental-candidate",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "allNumericalConvergenceGatesPassed": all(bool(gate["passed"]) for gate in gates.values()),
        "gates": gates,
        "mesh": {
            "cellCount": cell_count,
            "runtimeVtkPath": str(vtk_path.relative_to(case_dir)),
            "runtimeVtkSpansM": spans,
        },
        "referenceInputs": reference_inputs,
        "reference": reference,
        "qoi": {
            "pressureDropPa": pressure_drop,
            "pressureDropRelativeError": pressure_error,
            "inletMassFlowRateKgPerS": inlet_mass_flow,
            "outletMassFlowRateKgPerS": outlet_mass_flow,
            "relativeMassFlowImbalance": mass_imbalance,
            "measuredFullCircleVolumetricFlowRateM3PerS": measured_full_flow,
            "targetFullCircleVolumetricFlowRateM3PerS": target_full_flow,
            "flowRateRelativeError": flow_error,
            "kinematicPressureGradientMPerS2": final_gradient,
        },
        "geometryRealization": {
            "representation": "axisymmetric-wedge",
            "spatialDimension": 3,
            "solutionDirections": 3,
            "nonzeroCellVolume": mesh_ok,
            "runtimeMeshSource": "solver-produced-polyMesh",
            "wedgeAngleDegrees": float(profile["wedge"]["totalAngleDeg"]),
            "fullCircleScale": full_circle_scale,
            "fullCircleScaling": "wedge-integral-times-360-over-wedge-angle",
            "runtimePatches": ["inlet", "outlet", "walls", "front", "back", "axis"],
            "productExecutionPath": [
                "server.flowlab.adapters.generate_case",
                "server.flowlab.execution.JobManager.queue_job",
            ],
        },
        "provenance": {
            "profileSha256": _sha256_file(case_dir / "constant" / "flowlab_axisymmetric_profile.json"),
            "solverLogSha256": _sha256_file(solver_log_path),
            "checkMeshSha256": _sha256_file(check_mesh_path),
            "runtimeVtkSha256": _sha256_file(vtk_path),
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AxisymmetricStraightPipeCampaignError(f"could not read required JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise AxisymmetricStraightPipeCampaignError(f"required JSON artifact is not an object: {path}")
    return value


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
        raise AxisymmetricStraightPipeCampaignError(
            f"campaign provenance command could not run: {' '.join(command)}"
        ) from exc


def _source_control_identity() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[2]
    commit = _run_command(["git", "rev-parse", "HEAD"], cwd=repository_root)
    if commit.returncode != 0 or not commit.stdout.strip():
        raise AxisymmetricStraightPipeCampaignError("could not resolve the frozen campaign source commit")
    frozen_paths = [
        "server/flowlab/adapters.py",
        "server/flowlab/execution.py",
        "server/flowlab/mesh.py",
        "server/flowlab/results.py",
        "server/flowlab/schemas.py",
        "server/flowlab/verification.py",
        "server/flowlab/axisymmetric_straight_pipe_campaign.py",
        "benchmarks/cases/straight-pipe/benchmark.json",
    ]
    status = _run_command(["git", "status", "--porcelain", "--", *frozen_paths], cwd=repository_root)
    if status.returncode != 0:
        raise AxisymmetricStraightPipeCampaignError("could not inspect the frozen campaign source state")
    if status.stdout.strip():
        raise AxisymmetricStraightPipeCampaignError(
            "refusing scientific execution with uncommitted campaign or transitive scientific source"
        )
    return {
        "commit": commit.stdout.strip(),
        "repositoryRootName": repository_root.name,
        "frozenPaths": frozen_paths,
        "frozenPathsClean": True,
    }


def _runtime_environment_identity() -> dict[str, Any]:
    image = adapters._openfoam_image()
    inspect = _run_command(["docker", "image", "inspect", image])
    if inspect.returncode != 0:
        detail = inspect.stderr.strip() or inspect.stdout.strip() or f"exit {inspect.returncode}"
        raise AxisymmetricStraightPipeCampaignError(
            f"the pinned OpenFOAM image must be locally inspectable before execution: {detail}"
        )
    try:
        records = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise AxisymmetricStraightPipeCampaignError("docker image inspect returned invalid JSON") from exc
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise AxisymmetricStraightPipeCampaignError("docker image inspect did not resolve exactly one image")
    record = records[0]
    image_id = record.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise AxisymmetricStraightPipeCampaignError("docker image inspect did not return an immutable image ID")
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


def execute_campaign(
    output_dir: Path,
    spec: AxisymmetricStraightPipeSpec | None = None,
    *,
    poll_interval_seconds: float = 0.25,
    timeout_seconds_per_level: float = 7200.0,
) -> dict[str, Any]:
    """Execute all three frozen levels through ``JobManager`` and evaluate them.

    The caller must provide a new or empty campaign directory. Partial or failed
    output is retained in place and never overwritten by a retry.
    """

    if poll_interval_seconds <= 0.0 or timeout_seconds_per_level <= 0.0:
        raise AxisymmetricStraightPipeCampaignError("poll and timeout values must be positive")
    selected = spec or AxisymmetricStraightPipeSpec()
    manifest = materialize_campaign(output_dir, selected)
    output_dir = output_dir.resolve()
    source_control = _source_control_identity()
    runtime_environment = _runtime_environment_identity()
    manager = JobManager(runtime_root=output_dir / "runtime")
    started_at = datetime.now(timezone.utc).isoformat()
    state: dict[str, Any] = {
        "schema": RUN_RESULT_SCHEMA,
        "fixtureId": FIXTURE_ID,
        "status": "running",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "startedAt": started_at,
        "campaignManifestSha256": _sha256_file(output_dir / "campaign-manifest.json"),
        "sourceControl": source_control,
        "runtimeEnvironment": runtime_environment,
        "levels": [],
    }
    _write_json(output_dir / "campaign-run-state.json", state)

    for level, axial_cells, radial_cells in LEVELS:
        case = build_level_case(
            selected,
            level=level,
            axial_cells=axial_cells,
            radial_cells=radial_cells,
        )
        queued = manager.queue_job(case)
        record: dict[str, Any] = {
            "level": level,
            "axialCells": axial_cells,
            "radialCells": radial_cells,
            "caseId": case.id,
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
                raise AxisymmetricStraightPipeCampaignError(record["error"])
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                state["status"] = "failed-retained"
                record["status"] = "missing-job-record"
                _write_json(output_dir / "campaign-run-state.json", state)
                raise AxisymmetricStraightPipeCampaignError(
                    f"JobManager lost the retained {level} job record"
                )
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
            try:
                record["caseDirectory"] = str(case_dir.relative_to(output_dir))
            except ValueError as exc:
                raise AxisymmetricStraightPipeCampaignError(
                    f"JobManager placed {level} evidence outside the campaign directory"
                ) from exc
        else:
            case_dir = output_dir / "missing-case-directory"
        _write_json(output_dir / "campaign-run-state.json", state)
        if terminal.status != "complete" or terminal.exitCode != 0 or not terminal.caseDir:
            state["status"] = "failed-retained"
            _write_json(output_dir / "campaign-run-state.json", state)
            raise AxisymmetricStraightPipeCampaignError(
                f"{level} product-path level did not complete successfully: "
                f"status={terminal.status}, exitCode={terminal.exitCode}, error={terminal.error}"
            )

        evaluation = evaluate_completed_level(case_dir)
        evaluation["level"] = level
        evaluation["jobId"] = terminal.id
        evaluation["caseId"] = case.id
        evaluation["characteristicCellSizeM"] = next(
            float(item["mesh"]["characteristicCellSizeM"])
            for item in manifest["levels"]
            if item["level"] == level
        )
        evaluation_path = output_dir / "evaluations" / f"{level}.json"
        _write_json(evaluation_path, evaluation)
        record["evaluationPath"] = str(evaluation_path.relative_to(output_dir))
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allNumericalConvergenceGatesPassed"] = bool(
            evaluation["allNumericalConvergenceGatesPassed"]
        )
        _write_json(output_dir / "campaign-run-state.json", state)

    result = {
        **state,
        "status": "completed-evaluated-experimental-candidate",
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "allLevelGatesPassed": all(
            bool(level.get("allNumericalConvergenceGatesPassed"))
            for level in state["levels"]
        ),
        "requiredNextActions": [
            "calculate the prospective three-grid Richardson/GCI result",
            "build and independently hash the immutable evidence package",
            "obtain controlled independent review before fixture or registry mutation",
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


def _write_deterministic_tar(
    output_path: Path,
    entries: Sequence[tuple[Path, str]],
) -> None:
    if not entries:
        raise AxisymmetricStraightPipeCampaignError(
            f"cannot create empty campaign evidence archive: {output_path.name}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for source, archive_name in sorted(entries, key=lambda item: item[1]):
            if not source.is_file():
                raise AxisymmetricStraightPipeCampaignError(
                    f"required campaign evidence file is missing: {source}"
                )
            archive.add(
                source,
                arcname=archive_name,
                recursive=False,
                filter=_tar_filter,
            )


def _solver_log_path(case_dir: Path) -> Path:
    path = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    if path.is_file():
        return path
    fallback = case_dir / "smoke.log"
    if fallback.is_file():
        return fallback
    raise AxisymmetricStraightPipeCampaignError(f"retained solver log is missing: {case_dir}")


def _residual_history(case_dirs: dict[str, Path]) -> dict[str, Any]:
    levels: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?:smoothSolver|GAMG|DICPCG|PCG):\s+Solving for ([^,]+),\s+"
        r"Initial residual = ([0-9.eE+-]+),\s+Final residual = ([0-9.eE+-]+),\s+"
        r"No Iterations (\d+)"
    )
    for level, _axial_cells, _radial_cells in LEVELS:
        log_path = _solver_log_path(case_dirs[level])
        rows = [
            {
                "field": field.strip(),
                "initialResidual": float(initial),
                "finalResidual": float(final),
                "linearIterations": int(iterations),
            }
            for field, initial, final, iterations in pattern.findall(
                log_path.read_text(encoding="utf-8", errors="replace")
            )
        ]
        if len(rows) < CONVERGENCE_TAIL_SAMPLES:
            raise AxisymmetricStraightPipeCampaignError(
                f"{level} retained solver log has insufficient residual history"
            )
        levels.append(
            {
                "level": level,
                "sourceLogSha256": _sha256_file(log_path),
                "sampleCount": len(rows),
                "samples": rows,
            }
        )
    return {
        "schema": "flowlab.axisymmetric-straight-pipe-residual-history.v1",
        "fixtureId": FIXTURE_ID,
        "levels": levels,
    }


def _freeze_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(child, 0o555 if child.is_dir() else 0o444)
    os.chmod(path, 0o555)


def build_evidence_package(
    campaign_dir: Path,
    *,
    freeze: bool = True,
) -> dict[str, Any]:
    """Create a content-addressed, read-only candidate package from completed runs."""

    campaign_dir = campaign_dir.resolve()
    run_result = _read_json(campaign_dir / "campaign-result.json")
    if (
        run_result.get("schema") != RUN_RESULT_SCHEMA
        or run_result.get("status") != "completed-evaluated-experimental-candidate"
        or len(run_result.get("levels", [])) != len(LEVELS)
    ):
        raise AxisymmetricStraightPipeCampaignError(
            "immutable packaging requires one completed evaluated three-level campaign"
        )
    package_dir = campaign_dir / "immutable-evidence-package"
    if package_dir.exists():
        raise AxisymmetricStraightPipeCampaignError(
            f"refusing to overwrite existing evidence package: {package_dir}"
        )
    package_dir.mkdir(parents=True)

    run_levels = {
        str(record["level"]): record
        for record in run_result["levels"]
        if isinstance(record, dict) and record.get("level") in {item[0] for item in LEVELS}
    }
    if set(run_levels) != {item[0] for item in LEVELS}:
        raise AxisymmetricStraightPipeCampaignError("campaign result is missing a frozen mesh level")
    case_dirs: dict[str, Path] = {}
    evaluations: dict[str, dict[str, Any]] = {}
    for level, _axial_cells, _radial_cells in LEVELS:
        record = run_levels[level]
        relative_case_dir = record.get("caseDirectory")
        relative_evaluation = record.get("evaluationPath")
        if not isinstance(relative_case_dir, str) or not isinstance(relative_evaluation, str):
            raise AxisymmetricStraightPipeCampaignError(
                f"{level} campaign result does not bind retained case and evaluation paths"
            )
        case_dir = (campaign_dir / relative_case_dir).resolve()
        evaluation_path = (campaign_dir / relative_evaluation).resolve()
        if not case_dir.is_relative_to(campaign_dir) or not evaluation_path.is_relative_to(campaign_dir):
            raise AxisymmetricStraightPipeCampaignError(
                f"{level} retained evidence path escapes the campaign directory"
            )
        evaluation = _read_json(evaluation_path)
        if (
            evaluation.get("level") != level
            or evaluation.get("allNumericalConvergenceGatesPassed") is not True
        ):
            raise AxisymmetricStraightPipeCampaignError(
                f"{level} cannot enter the package because a frozen numerical gate did not pass"
            )
        case_dirs[level] = case_dir
        evaluations[level] = evaluation

    archive_specs: dict[str, list[tuple[Path, str]]] = {
        "case-manifest": [],
        "mesh-artifact": [],
        "solver-log": [],
        "raw-result-fields": [],
        "runtime-provenance": [],
    }
    for level, _axial_cells, _radial_cells in LEVELS:
        case_dir = case_dirs[level]
        archive_specs["case-manifest"].append(
            (case_dir / adapters.CASE_MANIFEST_PATH, f"{level}/{adapters.CASE_MANIFEST_PATH}")
        )
        poly_mesh = case_dir / "constant" / "polyMesh"
        for path in sorted(poly_mesh.rglob("*")):
            if path.is_file():
                archive_specs["mesh-artifact"].append(
                    (path, f"{level}/constant/polyMesh/{path.relative_to(poly_mesh).as_posix()}")
                )
        archive_specs["mesh-artifact"].append(
            (case_dir / "log.checkMesh", f"{level}/log.checkMesh")
        )
        solver_log = _solver_log_path(case_dir)
        archive_specs["solver-log"].append((solver_log, f"{level}/solve.log"))
        runtime_vtk = _latest_runtime_vtk(case_dir)
        archive_specs["raw-result-fields"].append(
            (runtime_vtk, f"{level}/VTK/{runtime_vtk.name}")
        )
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

    artifact_paths: dict[str, Path] = {}
    for kind, entries in archive_specs.items():
        path = package_dir / f"{kind}.tar"
        _write_deterministic_tar(path, entries)
        artifact_paths[kind] = path

    residual_history_path = package_dir / "residual-history.json"
    _write_json(residual_history_path, _residual_history(case_dirs))
    artifact_paths["residual-history"] = residual_history_path

    mesh_quality = {
        "schema": "flowlab.axisymmetric-straight-pipe-mesh-quality.v1",
        "fixtureId": FIXTURE_ID,
        "levels": [
            {
                "level": level,
                "cellCount": evaluations[level]["mesh"]["cellCount"],
                "runtimeVtkSpansM": evaluations[level]["mesh"]["runtimeVtkSpansM"],
                "runtimeMesh3d": evaluations[level]["gates"]["runtimeMesh3d"],
                "checkMeshSha256": evaluations[level]["provenance"]["checkMeshSha256"],
            }
            for level, _axial_cells, _radial_cells in LEVELS
        ],
    }
    mesh_quality_path = package_dir / "mesh-quality-report.json"
    _write_json(mesh_quality_path, mesh_quality)
    artifact_paths["mesh-quality-report"] = mesh_quality_path

    raw_result_sha = _sha256_file(artifact_paths["raw-result-fields"])
    mesh_samples = [
        {
            "id": level,
            "source": "solver-produced",
            "sourceArtifactSha256": raw_result_sha,
            "characteristicCellSizeM": float(evaluations[level]["characteristicCellSizeM"]),
            "pressureDropPa": float(evaluations[level]["qoi"]["pressureDropPa"]),
        }
        for level, _axial_cells, _radial_cells in LEVELS
    ]
    fine = evaluations["fine"]
    qoi_extraction = {
        "schema": "flowlab.straight-pipe-qoi-extraction.v1",
        "caseId": FIXTURE_ID,
        "referenceInputs": fine["referenceInputs"],
        "meshLevels": mesh_samples,
        "conservation": {
            "inletMassFlowRateKgPerS": fine["qoi"]["inletMassFlowRateKgPerS"],
            "outletMassFlowRateKgPerS": fine["qoi"]["outletMassFlowRateKgPerS"],
        },
    }
    qoi_path = package_dir / "qoi-extraction.json"
    _write_json(qoi_path, qoi_extraction)
    artifact_paths["qoi-extraction-table"] = qoi_path

    grid_samples = [
        {
            "id": sample["id"],
            "source": "solver-produced",
            "sourceArtifactSha256": raw_result_sha,
            "characteristicCellSizeM": sample["characteristicCellSizeM"],
            "value": sample["pressureDropPa"],
        }
        for sample in mesh_samples
    ]
    grid = richardson_grid_convergence(grid_samples)
    reference = fine["reference"]
    fine_pressure = float(fine["qoi"]["pressureDropPa"])
    reference_pressure = float(reference["pressureDropPa"])
    pressure_error = abs(fine_pressure - reference_pressure) / reference_pressure
    inlet_mass_flow = float(fine["qoi"]["inletMassFlowRateKgPerS"])
    outlet_mass_flow = float(fine["qoi"]["outletMassFlowRateKgPerS"])
    mass_imbalance = relative_mass_flow_imbalance(inlet_mass_flow, outlet_mass_flow)
    runtime_versions = [
        _read_json(case_dirs[level] / "constant" / "flowlab_openfoam_runtime.json")
        for level, _axial_cells, _radial_cells in LEVELS
    ]
    solver_versions = sorted(
        {
            str(value.get("detectedVersion") or value.get("detectedStyle") or "unknown")
            for value in runtime_versions
        }
    )
    evidence = {
        "schema": "flowlab.straight-pipe-evidence.v1",
        "caseId": FIXTURE_ID,
        "scientificStatus": "verification-candidate-awaiting-independent-review",
        "validated": False,
        "promotionAuthorized": False,
        "applicability": {
            "reynoldsNumber": reference["reynoldsNumber"],
            "boundaryCondition": "periodic-pressure-gradient",
        },
        "geometryRealization": fine["geometryRealization"],
        "meshRefinement": {
            "levels": [
                {
                    "id": sample["id"],
                    "characteristicCellSizeM": sample["characteristicCellSizeM"],
                    "pressureDropPa": sample["pressureDropPa"],
                }
                for sample in mesh_samples
            ],
            "refinementRatio": grid["refinementRatio"],
            "observedOrder": grid["observedOrder"],
            "richardsonExtrapolatedPressureDropPa": grid["richardsonExtrapolatedValue"],
            "fineGridGciPercent": grid["fineGridGciPercent"],
        },
        "timeRefinement": {
            "method": "direct-steady",
            "solverMethod": "steady SIMPLE",
            "temporalDiscretization": "none",
            "rationale": (
                "All three captured product cases use a direct steady SIMPLE formulation; "
                "no time-step discretization contributes to the reported QoIs."
            ),
        },
        "errorAssessment": {
            "analyticalReferencePressureDropPa": reference_pressure,
            "fineMeshPressureDropPa": fine_pressure,
            "pressureDropRelativeError": pressure_error,
        },
        "conservation": {
            "inletMassFlowRateKgPerS": inlet_mass_flow,
            "outletMassFlowRateKgPerS": outlet_mass_flow,
            "relativeImbalance": mass_imbalance,
        },
        "provenance": {
            "caseManifestSha256": _sha256_file(artifact_paths["case-manifest"]),
            "meshArtifactSha256": _sha256_file(artifact_paths["mesh-artifact"]),
            "solverLogSha256": _sha256_file(artifact_paths["solver-log"]),
            "rawResultSha256": raw_result_sha,
            "qoiExtractionSha256": _sha256_file(qoi_path),
            "solverVersion": ", ".join(solver_versions),
            "solverCommand": "FlowLab JobManager -> bash Allrun -> foamRun -solver incompressibleFluid",
            "sourceCommit": run_result["sourceControl"]["commit"],
            "runtimeEnvironment": run_result["runtimeEnvironment"],
            "postprocessingMethod": (
                "server.flowlab.axisymmetric_straight_pipe_campaign."
                "evaluate_completed_level/build_evidence_package"
            ),
        },
    }
    evidence_path = package_dir / "evidence.json"
    _write_json(evidence_path, evidence)
    artifact_paths["evidence-package"] = evidence_path

    candidate_gates = {
        "allLevelConvergence": all(
            bool(evaluations[level]["allNumericalConvergenceGatesPassed"])
            for level, _axial_cells, _radial_cells in LEVELS
        ),
        "fineGridGci": float(grid["fineGridGciPercent"]) <= FINE_GRID_GCI_PERCENT_LIMIT,
        "finePressureDropError": pressure_error <= PRESSURE_DROP_RELATIVE_ERROR_LIMIT,
        "fineMassBalance": mass_imbalance <= MASS_FLOW_RELATIVE_IMBALANCE_LIMIT,
    }
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
            "schema": "flowlab.axisymmetric-straight-pipe-artifact-index.v1",
            "fixtureId": FIXTURE_ID,
            "artifacts": artifact_records,
        },
    )
    included_paths = sorted(
        [
            *artifact_paths.values(),
            artifact_index_path,
        ],
        key=lambda path: path.name,
    )
    package_tree_lines = [
        f"{path.name} {_sha256_file(path)} {path.stat().st_size}" for path in included_paths
    ]
    tree_digest = hashlib.sha256(("\n".join(package_tree_lines) + "\n").encode("utf-8")).hexdigest()
    package_manifest = {
        "schema": PACKAGE_MANIFEST_SCHEMA,
        "fixtureId": FIXTURE_ID,
        "status": (
            "candidate-gates-passed-awaiting-independent-review"
            if all(candidate_gates.values())
            else "candidate-gates-failed"
        ),
        "scientificStatus": "verification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "campaignResultSha256": _sha256_file(campaign_dir / "campaign-result.json"),
        "sourceCommit": run_result["sourceControl"]["commit"],
        "runtimeEnvironment": run_result["runtimeEnvironment"],
        "candidateGates": candidate_gates,
        "allCandidateGatesPassed": all(candidate_gates.values()),
        "gridConvergence": grid,
        "finePressureDropRelativeError": pressure_error,
        "fineMassFlowRelativeImbalance": mass_imbalance,
        "artifactIndexSha256": _sha256_file(artifact_index_path),
        "treeDigestSha256": tree_digest,
        "immutable": True,
        "reviewStatus": "pending-controlled-independent-review",
        "fixtureMutationAuthorized": False,
    }
    package_manifest_path = package_dir / "package-manifest.json"
    _write_json(package_manifest_path, package_manifest)

    review_request = {
        "schema": "flowlab.axisymmetric-straight-pipe-review-request.v1",
        "fixtureId": FIXTURE_ID,
        "status": "pending-independent-review",
        "packagePath": str(package_dir.relative_to(campaign_dir)),
        "packageManifestSha256": _sha256_file(package_manifest_path),
        "packageTreeDigestSha256": tree_digest,
        "reviewMustVerify": [
            "package manifest and every artifact digest",
            "source commit and Docker image identity",
            "all three JobManager records and terminal solver states",
            "runtime 3D wedge mesh and patch contract at every level",
            "QoI extraction, signed conservation, Richardson order, and fine-grid GCI",
            "claim scope and absence of fixture or registry promotion before approval",
        ],
        "fixtureMutationAuthorized": False,
    }
    _write_json(campaign_dir / "independent-review-request.json", review_request)
    if freeze:
        _freeze_tree(package_dir)
    return package_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--materialize-only",
        action="store_true",
        help="Write the frozen generated cases without running a solver.",
    )
    actions.add_argument(
        "--run-and-package",
        action="store_true",
        help=(
            "Execute all three levels through JobManager in a new directory, "
            "evaluate them, and build the read-only candidate evidence package."
        ),
    )
    actions.add_argument(
        "--package-completed",
        action="store_true",
        help="Package an already completed and evaluated campaign without rerunning it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.materialize_only:
        manifest = materialize_campaign(args.output_dir)
    elif args.run_and_package:
        execute_campaign(args.output_dir)
        manifest = build_evidence_package(args.output_dir)
    else:
        manifest = build_evidence_package(args.output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
