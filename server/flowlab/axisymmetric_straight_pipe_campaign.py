"""Prospectively materialize the product axisymmetric straight-pipe mesh family.

This module deliberately stops before solver execution or fixture mutation.
It freezes three exact logical refinements through the same ``generate_case``
path used by the application, validates every generated case, and records the
inputs and source hashes needed for a later governed real-run campaign.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Sequence

from . import adapters
from .execution import materialize_case_files, validate_solver_case
from .results import parse_vtk_result
from .schemas import CaseRequest, SolverCase
from .verification import StraightPipeSpec, relative_mass_flow_imbalance, straight_pipe_reference


CAMPAIGN_SCHEMA = "flowlab.axisymmetric-straight-pipe-campaign.v1"
LEVEL_SCHEMA = "flowlab.axisymmetric-straight-pipe-level.v1"
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--materialize-only",
        action="store_true",
        help="Required safety acknowledgement: write generated cases without running a solver.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.materialize_only:
        raise AxisymmetricStraightPipeCampaignError(
            "this campaign entry point is materialization-only; solver execution must use FlowLab JobManager"
        )
    manifest = materialize_campaign(args.output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
