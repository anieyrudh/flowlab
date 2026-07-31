"""Run and evaluate the bounded canonical curved-elbow qualification campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import time
from typing import Any, Sequence

from . import adapters
from .curved_elbow import (
    CURVED_ELBOW_PROFILE_SCHEMA,
    CURVED_ELBOW_REPRESENTATION,
    CurvedElbowSpec,
)
from .execution import (
    JOB_RECORD_FILENAME,
    OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH,
    TERMINAL_STATUSES,
    JobManager,
    materialize_case_files,
    validate_solver_case,
)
from .full_ogrid_straight_pipe_campaign import (
    _check_mesh_path,
    _latest_runtime_vtk,
    _mesh_command_exit_codes,
    _patch_contract,
    _patch_metrics,
    _read_json,
    _relative_span,
    _require_float,
    _require_int,
    _run_command,
    _runtime_environment_identity,
    _sha256_file,
    _sha256_text,
    _solver_log_path,
    _surface_metric_history,
    _write_json,
)
from .results import parse_vtk_result
from .schemas import CaseRequest, SolverCase
from .verification import richardson_grid_convergence


CONTRACT_SCHEMA = "flowlab.curved-elbow-qualification-contract.v1"
CAMPAIGN_SCHEMA = "flowlab.curved-elbow-campaign.v1"
LEVEL_SCHEMA = "flowlab.curved-elbow-level-evaluation.v1"
REPORT_SCHEMA = "flowlab.curved-elbow-qualification-report.v1"
ARTIFACT_MANIFEST_SCHEMA = "flowlab.curved-elbow-artifact-manifest.v1"
CASE_ID = "canonical-circular-elbow-re100-v1"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "validation"
    / "curved-elbow-re100"
    / "QUALIFICATION_CONTRACT_V1.json"
)
RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK.md")
FROZEN_SOURCE_PATHS = (
    "server/flowlab/adapters.py",
    "server/flowlab/curved_elbow.py",
    "server/flowlab/curved_elbow_campaign.py",
    "server/flowlab/execution.py",
    "server/flowlab/full_ogrid.py",
    "server/flowlab/full_ogrid_straight_pipe_campaign.py",
    "server/flowlab/mesh.py",
    "server/flowlab/results.py",
    "server/flowlab/schemas.py",
    "server/flowlab/verification.py",
    "src/App.tsx",
    "src/data/presets.ts",
    "src/projectSchema.ts",
    "src/results/vtk.ts",
    "src/types.ts",
    "docs/validation/curved-elbow-re100/QUALIFICATION_CONTRACT_V1.json",
    "docs/validation/curved-elbow-re100/RUNBOOK.md",
)


class CurvedElbowCampaignError(RuntimeError):
    """Raised when a frozen campaign stage cannot complete honestly."""


def load_contract() -> dict[str, Any]:
    contract = _read_json(CONTRACT_PATH)
    if (
        contract.get("schema") != CONTRACT_SCHEMA
        or contract.get("status")
        != "prospective-frozen-before-retained-scientific-execution"
        or contract.get("promotionAuthorized") is not False
    ):
        raise CurvedElbowCampaignError(
            "unsupported or unfrozen curved-elbow qualification contract"
        )
    levels = contract.get("levels")
    if (
        not isinstance(levels, list)
        or [
            row.get("id")
            for row in levels
            if isinstance(row, dict)
        ]
        != ["coarse", "medium", "fine"]
    ):
        raise CurvedElbowCampaignError(
            "curved-elbow contract must freeze coarse, medium, and fine levels"
        )
    physical = contract.get("physicalCase")
    if not isinstance(physical, dict):
        raise CurvedElbowCampaignError("curved-elbow contract is missing physicalCase")
    diameter = float(physical["diameterM"])
    area = math.pi * diameter**2 / 4.0
    mean_velocity = float(physical["volumetricFlowRateM3PerS"]) / area
    reynolds = (
        float(physical["densityKgPerM3"])
        * mean_velocity
        * diameter
        / float(physical["dynamicViscosityPaS"])
    )
    derived = {
        "analyticCircularAreaM2": area,
        "meanVelocityMPerS": mean_velocity,
        "reynoldsNumber": reynolds,
        "centrelineRadiusOverDiameter": float(physical["centrelineRadiusM"])
        / diameter,
        "inletLegOverDiameter": float(physical["inletLegLengthM"]) / diameter,
        "outletLegOverDiameter": float(physical["outletLegLengthM"]) / diameter,
    }
    for key, value in derived.items():
        if not math.isclose(
            value,
            float(physical[key]),
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        ):
            raise CurvedElbowCampaignError(
                f"frozen curved-elbow value {key} is inconsistent with its SI inputs"
            )
    if not math.isclose(reynolds, 100.0, rel_tol=1.0e-12):
        raise CurvedElbowCampaignError("frozen curved-elbow Reynolds number is not 100")
    return contract


def _level_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in contract["levels"] if isinstance(row, dict)]


def _spec(
    contract: dict[str, Any],
    level: dict[str, Any],
) -> CurvedElbowSpec:
    physical = contract["physicalCase"]
    return CurvedElbowSpec(
        diameter_m=float(physical["diameterM"]),
        centreline_radius_m=float(physical["centrelineRadiusM"]),
        inlet_leg_m=float(physical["inletLegLengthM"]),
        outlet_leg_m=float(physical["outletLegLengthM"]),
        inlet_axial_cells=int(level["inletAxialCells"]),
        bend_axial_cells=int(level["bendAxialCells"]),
        outlet_axial_cells=int(level["outletAxialCells"]),
        annular_radial_cells=int(level["annularRadialCells"]),
        circumferential_cells=int(level["circumferentialCells"]),
        core_cells_per_side=int(level["coreCellsPerSide"]),
        bend_angle_degrees=float(physical["bendAngleDegrees"]),
    )


def _project(
    contract: dict[str, Any],
    level: dict[str, Any],
) -> dict[str, Any]:
    physical = contract["physicalCase"]
    spec = _spec(contract, level)
    return {
        "version": 1,
        "name": f"Canonical curved-elbow Re100 qualification ({level['id']})",
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
                "position": {"x": 100.0, "y": 300.0},
                "rotation": 0.0,
                "pressure": 101325.0,
            },
            "sink": {
                "id": "sink",
                "type": "sink",
                "position": {"x": 700.0, "y": 100.0},
                "rotation": 90.0,
                "pressure": 101325.0,
                "flowDemand": float(physical["volumetricFlowRateM3PerS"]),
            },
        },
        "edges": {
            "canonical-elbow": {
                "id": "canonical-elbow",
                "type": "bend",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": spec.total_centreline_length_m,
                "shape": {
                    "kind": "circular",
                    "diameter": float(physical["diameterM"]),
                },
                "roughness": 0.0,
                "minorLossK": 0.0,
            }
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": str(level["id"]),
            "runMode": "steady",
            "meshMode": "curved-elbow-ogrid",
            "meshControls": {
                "curvedElbowInletAxialCells": int(level["inletAxialCells"]),
                "curvedElbowBendAxialCells": int(level["bendAxialCells"]),
                "curvedElbowOutletAxialCells": int(level["outletAxialCells"]),
                "curvedElbowAnnularRadialCells": int(
                    level["annularRadialCells"]
                ),
                "curvedElbowCircumferentialCells": int(
                    level["circumferentialCells"]
                ),
                "curvedElbowCoreCellsPerSide": int(level["coreCellsPerSide"]),
            },
            "curvedElbowVerification": {
                "contractId": contract["productRequest"][
                    "verificationContractId"
                ],
                "boundaryCondition": contract["productRequest"][
                    "verificationBoundaryCondition"
                ],
                "diameterM": float(physical["diameterM"]),
                "centrelineRadiusM": float(physical["centrelineRadiusM"]),
                "inletLegLengthM": float(physical["inletLegLengthM"]),
                "outletLegLengthM": float(physical["outletLegLengthM"]),
                "bendAngleDegrees": float(physical["bendAngleDegrees"]),
                "volumetricFlowRateM3PerS": float(
                    physical["volumetricFlowRateM3PerS"]
                ),
                "qoiHistoryWriteIntervalIterations": int(
                    contract["productRequest"][
                        "qoiHistoryWriteIntervalIterations"
                    ]
                ),
            },
            "maxIterations": int(contract["productRequest"]["maxIterations"]),
            "tolerance": float(
                contract["productRequest"]["residualControl"]["p"]
            ),
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
    level: dict[str, Any],
    contract: dict[str, Any] | None = None,
) -> SolverCase:
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
        raise CurvedElbowCampaignError(
            f"{level['id']} generated case failed validation: "
            + "; ".join(issues)
        )
    try:
        profile = json.loads(
            case.files["constant/flowlab_curved_elbow_profile.json"]
        )
        preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise CurvedElbowCampaignError(
            f"{level['id']} generated case is missing its canonical profile or preview"
        ) from exc
    resolution = profile.get("topology", {}).get("resolution", {})
    expected = {
        "inletAxialCells": int(level["inletAxialCells"]),
        "bendAxialCells": int(level["bendAxialCells"]),
        "outletAxialCells": int(level["outletAxialCells"]),
        "annularRadialCells": int(level["annularRadialCells"]),
        "circumferentialCells": int(level["circumferentialCells"]),
        "coreCellsPerSide": int(level["coreCellsPerSide"]),
        "cellCount": int(level["expectedCellCount"]),
    }
    if (
        profile.get("schema") != CURVED_ELBOW_PROFILE_SCHEMA
        or profile.get("effectiveMeshMode") != CURVED_ELBOW_REPRESENTATION
        or profile.get("verificationContract", {}).get("contractId") != CASE_ID
        or any(int(resolution.get(key, -1)) != value for key, value in expected.items())
        or len(preview.get("cells", [])) != int(level["expectedCellCount"])
    ):
        raise CurvedElbowCampaignError(
            f"{level['id']} generated profile does not match the frozen level"
        )
    component_map = case.resultComponentMap
    if component_map is None or component_map.version != 2:
        raise CurvedElbowCampaignError(
            f"{level['id']} case lacks explicit source-cell provenance"
        )
    for binding in component_map.artifactBindings:
        record = binding.model_dump()
        if (
            record.get("scope") != "cell-ranges"
            or int(record.get("sourceCellCount", -1))
            != int(level["expectedCellCount"])
            or [
                row.get("componentId")
                for row in record.get("cellRanges", [])
                if isinstance(row, dict)
            ]
            != ["inlet-leg", "elbow", "outlet-leg"]
        ):
            raise CurvedElbowCampaignError(
                f"{level['id']} source-cell component map is incomplete"
            )
    if (
        "p               1e-8;" not in case.files["system/fvSolution"]
        or "U               1e-8;" not in case.files["system/fvSolution"]
    ):
        raise CurvedElbowCampaignError(
            "curved-elbow qualification requires the frozen 1e-8 residual control"
        )
    return case


def _case_file_hashes(case: SolverCase) -> dict[str, str]:
    return {
        path: _sha256_text(text)
        for path, text in sorted(case.files.items())
    }


def _source_control_identity() -> dict[str, Any]:
    commit = _run_command(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    if (
        commit.returncode != 0
        or not re.fullmatch(r"[0-9a-f]{40}", commit.stdout.strip())
    ):
        raise CurvedElbowCampaignError(
            "could not resolve the frozen curved-elbow source commit"
        )
    status = _run_command(
        ["git", "status", "--porcelain", "--", *FROZEN_SOURCE_PATHS],
        cwd=REPOSITORY_ROOT,
    )
    if status.returncode != 0:
        raise CurvedElbowCampaignError(
            "could not inspect frozen curved-elbow source state"
        )
    if status.stdout.strip():
        raise CurvedElbowCampaignError(
            "refusing retained execution with uncommitted transitive curved-elbow source"
        )
    return {
        "commit": commit.stdout.strip(),
        "frozenPaths": list(FROZEN_SOURCE_PATHS),
        "frozenPathsClean": True,
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "sourceSha256": {
            path: _sha256_file(REPOSITORY_ROOT / path)
            for path in FROZEN_SOURCE_PATHS
        },
    }


def materialize_campaign(output_dir: Path) -> dict[str, Any]:
    """Materialize and determinism-check all levels without solver execution."""

    contract = load_contract()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise CurvedElbowCampaignError(
            f"refusing to overwrite non-empty output directory: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    level_records: list[dict[str, Any]] = []
    for level in _level_rows(contract):
        first = build_level_case(level, contract)
        second = build_level_case(level, contract)
        first_hashes = _case_file_hashes(first)
        if first_hashes != _case_file_hashes(second):
            raise CurvedElbowCampaignError(
                f"{level['id']} generated case is not deterministic"
            )
        case_dir = output_dir / "cases" / str(level["id"])
        materialize_case_files(first, case_dir)
        spec = _spec(contract, level)
        component_map = first.resultComponentMap
        level_record = {
            "schema": "flowlab.curved-elbow-materialized-level.v1",
            "level": level["id"],
            "status": "materialized-pending-real-run",
            "validated": False,
            "promotionAuthorized": False,
            "caseDirectory": str(case_dir.relative_to(output_dir)),
            "mesh": {
                **{
                    key: int(level[key])
                    for key in (
                        "inletAxialCells",
                        "bendAxialCells",
                        "outletAxialCells",
                        "annularRadialCells",
                        "circumferentialCells",
                        "coreCellsPerSide",
                        "expectedCellCount",
                    )
                },
                "characteristicCellSizeM": (
                    float(contract["physicalCase"]["analyticFluidVolumeM3"])
                    / int(level["expectedCellCount"])
                )
                ** (1.0 / 3.0),
                "wallGeometry": spec.wall_geometry(),
            },
            "expectedPatches": spec.topology_manifest()["patches"],
            "determinism": {
                "independentSecondBuild": True,
                "generatedFileCount": len(first_hashes),
                "generatedFileHashesMatch": True,
                "generatedFileTreeSha256": hashlib.sha256(
                    "".join(
                        f"{path}\0{digest}\n"
                        for path, digest in first_hashes.items()
                    ).encode("utf-8")
                ).hexdigest(),
            },
            "sourceCellProvenance": (
                component_map.model_dump(mode="json")
                if component_map is not None
                else None
            ),
            "generatedCaseManifestSha256": _sha256_file(
                case_dir / adapters.CASE_MANIFEST_PATH
            ),
            "runCommand": first.runCommand,
        }
        level_path = case_dir / "curved-elbow-materialized-level.json"
        _write_json(level_path, level_record)
        level_records.append(
            {
                **level_record,
                "levelManifestSha256": _sha256_file(level_path),
            }
        )
    manifest = {
        "schema": CAMPAIGN_SCHEMA,
        "caseId": CASE_ID,
        "status": "materialized-pending-real-run",
        "scientificStatus": "experimental-qualification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "contractPath": str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "runbookSha256": _sha256_file(RUNBOOK_PATH),
        "physicalCase": contract["physicalCase"],
        "refinementInterpretation": contract["refinementInterpretation"],
        "observationOperators": contract["observationOperators"],
        "gates": contract["gates"],
        "levels": level_records,
    }
    _write_json(output_dir / "campaign-manifest.json", manifest)
    return manifest


def _field(
    vtk: dict[str, Any],
    kind: str,
    name: str,
) -> list[Any]:
    cell_data = (
        vtk.get("cellData")
        if isinstance(vtk.get("cellData"), dict)
        else {}
    )
    fields = (
        cell_data.get(kind)
        if isinstance(cell_data.get(kind), dict)
        else {}
    )
    for key, values in fields.items():
        if str(key).lower() == name.lower() and isinstance(values, list):
            return values
    raise CurvedElbowCampaignError(
        f"solver VTK is missing cell-centred {name}"
    )


def _tetra_volume(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
) -> float:
    ab = [float(b[index]) - float(a[index]) for index in range(3)]
    ac = [float(c[index]) - float(a[index]) for index in range(3)]
    ad = [float(d[index]) - float(a[index]) for index in range(3)]
    cross = [
        ac[1] * ad[2] - ac[2] * ad[1],
        ac[2] * ad[0] - ac[0] * ad[2],
        ac[0] * ad[1] - ac[1] * ad[0],
    ]
    return abs(sum(ab[index] * cross[index] for index in range(3))) / 6.0


def _hexa_volume(coordinates: Sequence[Sequence[float]]) -> float:
    tetrahedra = (
        (0, 1, 3, 4),
        (1, 2, 3, 6),
        (1, 3, 4, 6),
        (1, 4, 5, 6),
        (3, 4, 6, 7),
    )
    return sum(
        _tetra_volume(
            coordinates[a],
            coordinates[b],
            coordinates[c],
            coordinates[d],
        )
        for a, b, c, d in tetrahedra
    )


def _cell_geometry(
    vtk: dict[str, Any],
) -> tuple[list[tuple[float, float, float]], list[float]]:
    points = vtk.get("points") if isinstance(vtk.get("points"), list) else []
    cells = vtk.get("cells") if isinstance(vtk.get("cells"), list) else []
    if not points or not cells:
        raise CurvedElbowCampaignError(
            "runtime VTK has no points or cells"
        )
    centroids: list[tuple[float, float, float]] = []
    volumes: list[float] = []
    for cell in cells:
        if not isinstance(cell, list) or len(cell) != 8:
            raise CurvedElbowCampaignError(
                "curved-elbow runtime VTK must contain only hexahedra"
            )
        coordinates = [points[int(index)] for index in cell]
        centroids.append(
            tuple(
                sum(float(point[axis]) for point in coordinates) / 8.0
                for axis in range(3)
            )
        )
        volume = _hexa_volume(coordinates)
        if not math.isfinite(volume) or volume <= 0.0:
            raise CurvedElbowCampaignError(
                "runtime VTK contains a non-positive hexahedron"
            )
        volumes.append(volume)
    return centroids, volumes


def _relative_error(measured: float, expected: float) -> float:
    return abs(measured - expected) / max(abs(expected), 1.0e-30)


def _geometry_metrics(
    vtk: dict[str, Any],
    contract: dict[str, Any],
    check_mesh_text: str,
) -> dict[str, Any]:
    points = vtk.get("points") if isinstance(vtk.get("points"), list) else []
    if not points:
        raise CurvedElbowCampaignError("runtime VTK contains no geometry")
    physical = contract["physicalCase"]
    diameter = float(physical["diameterM"])
    radius = float(physical["radiusM"])
    inlet_leg = float(physical["inletLegLengthM"])
    outlet_leg = float(physical["outletLegLengthM"])
    centreline = float(physical["centrelineRadiusM"])
    tolerance = diameter * 1.0e-7
    spans = [
        max(float(point[axis]) for point in points)
        - min(float(point[axis]) for point in points)
        for axis in range(3)
    ]

    inlet_planes: dict[float, set[int]] = {}
    outlet_planes: dict[float, set[int]] = {}
    for point in points:
        x, y, z = (float(value) for value in point)
        if abs(z) <= tolerance:
            if abs(y - radius) <= tolerance:
                inlet_planes.setdefault(round(x, 11), set()).add(1)
            if abs(y + radius) <= tolerance:
                inlet_planes.setdefault(round(x, 11), set()).add(-1)
            outlet_x = inlet_leg + centreline
            if abs(x - (outlet_x - radius)) <= tolerance:
                outlet_planes.setdefault(round(y, 11), set()).add(-1)
            if abs(x - (outlet_x + radius)) <= tolerance:
                outlet_planes.setdefault(round(y, 11), set()).add(1)
    inlet_x = sorted(
        value for value, sides in inlet_planes.items() if sides == {-1, 1}
    )
    outlet_y = sorted(
        value for value, sides in outlet_planes.items() if sides == {-1, 1}
    )
    if len(inlet_x) < 2 or len(outlet_y) < 2:
        raise CurvedElbowCampaignError(
            "runtime VTK does not expose both straight-leg transition planes"
        )
    measured_inlet_leg = inlet_x[-1] - inlet_x[0]
    measured_outlet_leg = outlet_y[-1] - outlet_y[0]

    bend_centre = (inlet_leg, centreline)
    bend_points: list[tuple[float, float, float]] = []
    for point in points:
        x, y, z = (float(value) for value in point)
        if (
            abs(z) <= tolerance
            and x >= inlet_leg - tolerance
            and y <= centreline + tolerance
        ):
            bend_points.append((x, y, z))
    radial = [
        math.hypot(point[0] - bend_centre[0], point[1] - bend_centre[1])
        for point in bend_points
    ]
    if not radial:
        raise CurvedElbowCampaignError(
            "runtime VTK has no bend-plane points"
        )
    inner_radius = min(radial)
    outer_radius = max(radial)
    measured_centreline = (inner_radius + outer_radius) / 2.0
    measured_diameter_radial = outer_radius - inner_radius
    wall_tolerance = diameter * 1.0e-5
    wall_angles = [
        math.atan2(point[1] - bend_centre[1], point[0] - bend_centre[0])
        for point, radial_value in zip(bend_points, radial, strict=True)
        if (
            abs(radial_value - inner_radius) <= wall_tolerance
            or abs(radial_value - outer_radius) <= wall_tolerance
        )
    ]
    wall_angles = [
        angle
        for angle in wall_angles
        if -math.pi / 2.0 - 1.0e-8 <= angle <= 1.0e-8
    ]
    if not wall_angles:
        raise CurvedElbowCampaignError(
            "runtime VTK bend wall angles are unavailable"
        )
    measured_angle = math.degrees(max(wall_angles) - min(wall_angles))
    total_volume = _require_float(
        check_mesh_text,
        (r"Total volume\s*=\s*([0-9.eE+-]+)",),
        "total volume",
    )
    errors = {
        "diameterFromZSpan": _relative_error(spans[2], diameter),
        "diameterFromBendRadialSpan": _relative_error(
            measured_diameter_radial,
            diameter,
        ),
        "centrelineRadius": _relative_error(
            measured_centreline,
            centreline,
        ),
        "bendAngle": _relative_error(
            measured_angle,
            float(physical["bendAngleDegrees"]),
        ),
        "inletLeg": _relative_error(measured_inlet_leg, inlet_leg),
        "outletLeg": _relative_error(measured_outlet_leg, outlet_leg),
    }
    return {
        "runtimeVtkSpansM": spans,
        "measured": {
            "diameterFromZSpanM": spans[2],
            "diameterFromBendRadialSpanM": measured_diameter_radial,
            "centrelineRadiusM": measured_centreline,
            "bendAngleDegrees": measured_angle,
            "inletLegLengthM": measured_inlet_leg,
            "outletLegLengthM": measured_outlet_leg,
            "fluidVolumeM3": total_volume,
        },
        "relativeErrors": errors,
        "maximumRelativeDimensionError": max(errors.values()),
        "relativeAnalyticVolumeError": _relative_error(
            total_volume,
            float(physical["analyticFluidVolumeM3"]),
        ),
    }


def _qoi_tail_stability(
    case_dir: Path,
    contract: dict[str, Any],
) -> dict[str, Any]:
    limits = contract["gates"]["solverPerLevel"]["qoiTailStability"]
    sample_count = int(limits["sampleCount"])
    histories = {
        "inletPressure": _surface_metric_history(
            case_dir,
            "patchAverage",
            "inlet",
        ),
        "outletPressure": _surface_metric_history(
            case_dir,
            "patchAverage",
            "outlet",
        ),
        "inletFlow": _surface_metric_history(
            case_dir,
            "patchFlowRate",
            "inlet",
        ),
        "outletFlow": _surface_metric_history(
            case_dir,
            "patchFlowRate",
            "outlet",
        ),
    }
    by_name = {name: dict(rows) for name, rows in histories.items()}
    common_times = sorted(
        set.intersection(*(set(rows) for rows in by_name.values()))
    )
    positive_times = [value for value in common_times if value > 0.0]
    if len(positive_times) < sample_count:
        raise CurvedElbowCampaignError(
            f"retained QoI history has {len(positive_times)} common samples; "
            f"{sample_count} required"
        )
    times = positive_times[-sample_count:]
    expected_interval = int(
        contract["productRequest"]["qoiHistoryWriteIntervalIterations"]
    )
    consecutive = all(
        math.isclose(
            right - left,
            expected_interval,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        for left, right in zip(times, times[1:], strict=False)
    )
    density = float(contract["physicalCase"]["densityKgPerM3"])
    pressure_losses = [
        (
            by_name["inletPressure"][time_value]
            - by_name["outletPressure"][time_value]
        )
        * density
        for time_value in times
    ]
    measured_flows = [
        0.5
        * (
            abs(by_name["inletFlow"][time_value])
            + abs(by_name["outletFlow"][time_value])
        )
        for time_value in times
    ]
    pressure_span = _relative_span(pressure_losses)
    flow_span = _relative_span(measured_flows)
    checks = {
        "sampleCount": len(times) == sample_count,
        "consecutiveSamples": consecutive,
        "pressureLossRelativeSpan": pressure_span
        <= float(limits["maximumPressureLossRelativeSpan"]),
        "measuredFlowRateRelativeSpan": flow_span
        <= float(limits["maximumMeasuredFlowRateRelativeSpan"]),
    }
    return {
        "sampleCount": sample_count,
        "firstIteration": times[0],
        "lastIteration": times[-1],
        "consecutiveSamples": consecutive,
        "pressureLossRelativeSpan": pressure_span,
        "measuredFlowRateRelativeSpan": flow_span,
        "checks": checks,
    }


def _component_ranges(
    case_dir: Path,
    expected_cell_count: int,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    manifest = _read_json(case_dir / adapters.CASE_MANIFEST_PATH)
    component_map = manifest.get("resultComponentMap")
    if (
        not isinstance(component_map, dict)
        or component_map.get("version") != 2
    ):
        raise CurvedElbowCampaignError(
            "retained case lacks the explicit source-cell component map"
        )
    bindings = component_map.get("artifactBindings")
    if not isinstance(bindings, list):
        raise CurvedElbowCampaignError(
            "retained source-cell component bindings are unavailable"
        )
    binding = next(
        (
            row
            for row in bindings
            if isinstance(row, dict)
            and row.get("artifactName") == "VTK/*.vtk"
        ),
        None,
    )
    if (
        not isinstance(binding, dict)
        or int(binding.get("sourceCellCount", -1)) != expected_cell_count
    ):
        raise CurvedElbowCampaignError(
            "retained VTK source-cell count does not match the frozen mesh"
        )
    ranges = binding.get("cellRanges")
    if not isinstance(ranges, list):
        raise CurvedElbowCampaignError(
            "retained VTK source-cell ranges are unavailable"
        )
    ordered = [
        dict(row)
        for row in ranges
        if isinstance(row, dict)
    ]
    if [
        row.get("componentId")
        for row in ordered
    ] != ["inlet-leg", "elbow", "outlet-leg"]:
        raise CurvedElbowCampaignError(
            "retained VTK component sequence is not canonical"
        )
    owner: dict[int, str] = {}
    for row in ordered:
        start = int(row["cellStart"])
        count = int(row["cellCount"])
        component = str(row["componentId"])
        for cell_index in range(start, start + count):
            if cell_index in owner or not 0 <= cell_index < expected_cell_count:
                raise CurvedElbowCampaignError(
                    "retained source-cell component ranges overlap or escape the mesh"
                )
            owner[cell_index] = component
    if len(owner) != expected_cell_count:
        raise CurvedElbowCampaignError(
            "retained source-cell component ranges leave unowned cells"
        )
    return ordered, owner


def _field_physics(
    vtk: dict[str, Any],
    *,
    contract: dict[str, Any],
    pressure_loss_pa: float,
    component_owner: dict[int, str],
) -> dict[str, Any]:
    centroids, volumes = _cell_geometry(vtk)
    pressures = _field(vtk, "scalars", "p")
    velocities = _field(vtk, "vectors", "U")
    if (
        len(pressures) != len(centroids)
        or len(velocities) != len(centroids)
        or len(component_owner) != len(centroids)
    ):
        raise CurvedElbowCampaignError(
            "runtime fields or source-cell provenance do not match the mesh"
        )
    finite = True
    normalized_velocities: list[tuple[float, float, float]] = []
    for pressure, velocity in zip(pressures, velocities, strict=True):
        if (
            not isinstance(velocity, list | tuple)
            or len(velocity) != 3
        ):
            raise CurvedElbowCampaignError("runtime U tuple is malformed")
        vector = tuple(float(value) for value in velocity)
        finite = (
            finite
            and math.isfinite(float(pressure))
            and all(math.isfinite(value) for value in vector)
        )
        normalized_velocities.append(vector)

    inlet_indices = [
        index
        for index, component in component_owner.items()
        if component == "inlet-leg"
    ]
    outlet_indices = [
        index
        for index, component in component_owner.items()
        if component == "outlet-leg"
    ]
    inlet_plane = min(round(centroids[index][0], 11) for index in inlet_indices)
    outlet_plane = max(round(centroids[index][1], 11) for index in outlet_indices)
    inlet_plane_indices = [
        index
        for index in inlet_indices
        if round(centroids[index][0], 11) == inlet_plane
    ]
    outlet_plane_indices = [
        index
        for index in outlet_indices
        if round(centroids[index][1], 11) == outlet_plane
    ]
    density = float(contract["physicalCase"]["densityKgPerM3"])

    def total_pressure(index: int) -> float:
        velocity = normalized_velocities[index]
        speed_squared = sum(value**2 for value in velocity)
        return density * (float(pressures[index]) + 0.5 * speed_squared)

    def weighted_mean(indices: list[int]) -> float:
        denominator = sum(volumes[index] for index in indices)
        return (
            sum(
                total_pressure(index) * volumes[index]
                for index in indices
            )
            / denominator
        )

    inlet_total = weighted_mean(inlet_plane_indices)
    outlet_total = weighted_mean(outlet_plane_indices)
    total_pressure_loss = inlet_total - outlet_total

    mirror_lookup: dict[
        tuple[str, float, float, float],
        dict[int, int],
    ] = {}
    for index, centroid in enumerate(centroids):
        if abs(centroid[2]) <= 1.0e-13:
            continue
        key = (
            component_owner[index],
            round(centroid[0], 10),
            round(centroid[1], 10),
            round(abs(centroid[2]), 10),
        )
        mirror_lookup.setdefault(key, {})[
            1 if centroid[2] > 0.0 else -1
        ] = index
    pressure_errors: list[float] = []
    velocity_errors_squared: list[float] = []
    paired_cells = 0
    for pair in mirror_lookup.values():
        if set(pair) != {-1, 1}:
            continue
        positive = pair[1]
        negative = pair[-1]
        pressure_errors.append(
            float(pressures[positive]) - float(pressures[negative])
        )
        positive_velocity = normalized_velocities[positive]
        negative_velocity = normalized_velocities[negative]
        reflected_negative = (
            negative_velocity[0],
            negative_velocity[1],
            -negative_velocity[2],
        )
        velocity_errors_squared.append(
            sum(
                (positive_velocity[axis] - reflected_negative[axis]) ** 2
                for axis in range(3)
            )
        )
        paired_cells += 2
    if not pressure_errors or not velocity_errors_squared:
        raise CurvedElbowCampaignError(
            "runtime fields do not contain mirror-paired source cells"
        )
    pressure_rms = math.sqrt(
        sum(value**2 for value in pressure_errors) / len(pressure_errors)
    ) * density
    velocity_rms = math.sqrt(
        sum(velocity_errors_squared) / len(velocity_errors_squared)
    )
    pressure_scale = max(
        abs(pressure_loss_pa),
        float(contract["physicalCase"]["inletDynamicPressurePa"]),
    )
    velocity_scale = float(contract["physicalCase"]["meanVelocityMPerS"])
    pressure_symmetry = pressure_rms / pressure_scale
    velocity_symmetry = velocity_rms / velocity_scale
    return {
        "finitePressureAndVelocity": finite,
        "totalPressure": {
            "inletPa": inlet_total,
            "outletPa": outlet_total,
            "lossPa": total_pressure_loss,
            "gainPa": max(0.0, -total_pressure_loss),
            "inletPlaneCellCount": len(inlet_plane_indices),
            "outletPlaneCellCount": len(outlet_plane_indices),
        },
        "symmetry": {
            "pairedCellCount": paired_cells,
            "pressureRmsPa": pressure_rms,
            "velocityRmsMPerS": velocity_rms,
            "pressureNormalizedError": pressure_symmetry,
            "velocityNormalizedError": velocity_symmetry,
            "maximumNormalizedError": max(
                pressure_symmetry,
                velocity_symmetry,
            ),
        },
    }


def evaluate_completed_level(
    case_dir: Path,
    level: dict[str, Any],
    *,
    solver_exit_code: int = 0,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one completed level only from retained frozen artifacts."""

    selected = contract or load_contract()
    case_dir = case_dir.resolve()
    profile_path = (
        case_dir / "constant" / "flowlab_curved_elbow_profile.json"
    )
    profile = _read_json(profile_path)
    if (
        profile.get("schema") != CURVED_ELBOW_PROFILE_SCHEMA
        or profile.get("effectiveMeshMode") != CURVED_ELBOW_REPRESENTATION
    ):
        raise CurvedElbowCampaignError(
            "completed level lacks the canonical curved-elbow profile"
        )
    expected_cell_count = int(level["expectedCellCount"])
    if (
        int(
            profile.get("topology", {})
            .get("resolution", {})
            .get("cellCount", -1)
        )
        != expected_cell_count
    ):
        raise CurvedElbowCampaignError(
            "completed level cell count differs from the frozen contract"
        )

    solver_log_path = _solver_log_path(case_dir)
    check_mesh_path = _check_mesh_path(case_dir)
    solver_log = solver_log_path.read_text(
        encoding="utf-8",
        errors="replace",
    )
    check_mesh = check_mesh_path.read_text(
        encoding="utf-8",
        errors="replace",
    )
    fatal_solver_markers = bool(
        re.search(
            r"^(?:-->\s*)?FOAM FATAL|^Floating point exception(?:\s|$)|"
            r"^Segmentation fault(?:\s|$)|(?:=|\()\s*(?:nan|inf)\b",
            solver_log,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    simple_converged = bool(
        re.search(
            r"SIMPLE solution converged in \d+ iterations",
            solver_log,
        )
    )
    solver_times = [
        float(value)
        for value in re.findall(
            r"^Time = ([0-9.eE+-]+)s?\s*$",
            solver_log,
            re.MULTILINE,
        )
    ]
    if not solver_times:
        raise CurvedElbowCampaignError(
            "retained solver log has no iteration markers"
        )
    last_iteration = solver_times[-1]
    reached_declared_stop = simple_converged or math.isclose(
        last_iteration,
        float(selected["productRequest"]["maxIterations"]),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    )
    normal_termination = (
        bool(re.search(r"^End\s*$", solver_log, re.MULTILINE))
        and not fatal_solver_markers
    )
    qoi_tail = _qoi_tail_stability(case_dir, selected)

    cell_count = _require_int(
        check_mesh,
        (r"^\s*cells:\s+(\d+)\s*$",),
        "cell count",
    )
    hex_count = _require_int(
        check_mesh,
        (r"^\s*hexahedra:\s+(\d+)\s*$",),
        "hexahedron count",
    )
    region_count = _require_int(
        check_mesh,
        (r"Number of regions:\s*(\d+)", r"regions:\s*(\d+)"),
        "connected region count",
    )
    directions = re.search(
        r"Mesh has\s+(\d+)\s+geometric.*?\n.*?Mesh has\s+(\d+)\s+solution",
        check_mesh,
        re.IGNORECASE | re.DOTALL,
    )
    if directions is None:
        raise CurvedElbowCampaignError(
            "checkMesh lacks geometric or solution direction counts"
        )
    quality = {
        "minimumCellVolumeM3": _require_float(
            check_mesh,
            (r"Min volume\s*=\s*([0-9.eE+-]+)",),
            "minimum volume",
        ),
        "maximumAspectRatio": _require_float(
            check_mesh,
            (r"Max aspect ratio\s*=\s*([0-9.eE+-]+)",),
            "maximum aspect ratio",
        ),
        "maximumNonOrthogonalityDegrees": _require_float(
            check_mesh,
            (r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)",),
            "maximum non-orthogonality",
        ),
        "maximumSkewness": _require_float(
            check_mesh,
            (r"Max skewness\s*=\s*([0-9.eE+-]+)",),
            "maximum skewness",
        ),
        "minimumCellDeterminant": _require_float(
            check_mesh,
            (
                r"minimum determinant\s*=\s*([0-9.eE+-]+)",
                r"Cell determinant\b.*?\bminimum:\s*([0-9.eE+-]+)",
            ),
            "minimum determinant",
        ),
        "minimumFaceInterpolationWeight": _require_float(
            check_mesh,
            (
                r"minimum face interpolation weight\s*=\s*([0-9.eE+-]+)",
                r"Face interpolation weight\s*:\s*minimum:\s*([0-9.eE+-]+)",
            ),
            "minimum face interpolation weight",
        ),
        "minimumFaceVolumeRatio": _require_float(
            check_mesh,
            (
                r"minimum face volume ratio\s*=\s*([0-9.eE+-]+)",
                r"Face volume ratio\s*:\s*minimum:\s*([0-9.eE+-]+)",
            ),
            "minimum face volume ratio",
        ),
    }
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    if not boundary_path.is_file():
        raise CurvedElbowCampaignError(
            "completed level lacks the solver polyMesh boundary"
        )
    patches = _patch_contract(
        boundary_path.read_text(encoding="utf-8", errors="replace")
    )
    expected_patches = {
        "inlet": {
            "type": "patch",
            "nFaces": int(level["expectedInletFaces"]),
        },
        "outlet": {
            "type": "patch",
            "nFaces": int(level["expectedOutletFaces"]),
        },
        "walls": {
            "type": "wall",
            "nFaces": int(level["expectedWallFaces"]),
        },
    }
    vtk_path = _latest_runtime_vtk(case_dir)
    vtk = parse_vtk_result(
        vtk_path.read_text(encoding="utf-8", errors="replace")
    )
    geometry = _geometry_metrics(vtk, selected, check_mesh)
    ranges, component_owner = _component_ranges(
        case_dir,
        expected_cell_count,
    )

    patch_metrics = _patch_metrics(case_dir)
    flow_balance = patch_metrics.get("flowBalance")
    pressure_drops = patch_metrics.get("pressureDrops")
    if (
        not isinstance(flow_balance, dict)
        or not isinstance(pressure_drops, list)
        or not pressure_drops
    ):
        raise CurvedElbowCampaignError(
            "retained patch pressure or flow operator is unavailable"
        )
    pressure_record = next(
        (
            row
            for row in pressure_drops
            if isinstance(row, dict)
            and row.get("fromPatch") == "inlet"
            and row.get("toPatch") == "outlet"
        ),
        None,
    )
    if pressure_record is None:
        raise CurvedElbowCampaignError(
            "patch-average operator lacks inlet-to-outlet pressure loss"
        )
    density = float(selected["physicalCase"]["densityKgPerM3"])
    pressure_loss = float(pressure_record["deltaP"]) * density
    inlet_flow = float(flow_balance["inletFlow"])
    outlet_flow = float(flow_balance["outletFlow"])
    measured_flow = 0.5 * (abs(inlet_flow) + abs(outlet_flow))
    mass_imbalance = (
        abs(abs(outlet_flow) - abs(inlet_flow))
        / max(abs(inlet_flow), abs(outlet_flow), 1.0e-30)
    )
    field_physics = _field_physics(
        vtk,
        contract=selected,
        pressure_loss_pa=pressure_loss,
        component_owner=component_owner,
    )

    mesh_limits = selected["gates"]["meshPerLevel"]
    geometry_limit = float(
        selected["gates"]["geometryPerLevel"][
            "maximumRelativeDimensionError"
        ]
    )
    solver_limits = selected["gates"]["solverPerLevel"]
    physics_limits = selected["gates"]["physicsPerLevel"]
    command_codes = _mesh_command_exit_codes(case_dir)
    mesh_checks = {
        "blockMeshExit": command_codes["blockMesh"]
        == int(mesh_limits["blockMeshExitCode"]),
        "checkMeshExit": command_codes["checkMesh"]
        == int(mesh_limits["checkMeshExitCode"]),
        "meshOk": "Mesh OK." in check_mesh,
        "connectedRegion": region_count
        == int(mesh_limits["connectedRegions"]),
        "directions": int(directions.group(1))
        == int(mesh_limits["geometricDirections"])
        and int(directions.group(2))
        == int(mesh_limits["solutionDirections"]),
        "allHex": cell_count == hex_count == expected_cell_count,
        "invalidCellCount": "Mesh OK." in check_mesh
        and quality["minimumCellVolumeM3"] > 0.0,
        "patches": patches == expected_patches,
        "positiveVtkSpans": all(
            value > 0.0
            for value in geometry["runtimeVtkSpansM"]
        ),
        "minimumCellVolume": quality["minimumCellVolumeM3"]
        > float(mesh_limits["minimumCellVolumeM3ExclusiveLowerBound"]),
        "maximumAspectRatio": quality["maximumAspectRatio"]
        <= float(mesh_limits["maximumAspectRatio"]),
        "maximumNonOrthogonality": quality[
            "maximumNonOrthogonalityDegrees"
        ]
        <= float(mesh_limits["maximumNonOrthogonalityDegrees"]),
        "maximumSkewness": quality["maximumSkewness"]
        <= float(mesh_limits["maximumSkewness"]),
        "minimumCellDeterminant": quality["minimumCellDeterminant"]
        >= float(mesh_limits["minimumCellDeterminant"]),
        "minimumFaceInterpolationWeight": quality[
            "minimumFaceInterpolationWeight"
        ]
        >= float(mesh_limits["minimumFaceInterpolationWeight"]),
        "minimumFaceVolumeRatio": quality["minimumFaceVolumeRatio"]
        >= float(mesh_limits["minimumFaceVolumeRatio"]),
    }
    geometry_checks = {
        "profileMatchesContract": all(
            math.isclose(
                float(profile[key]),
                float(selected["physicalCase"][physical_key]),
                rel_tol=1.0e-12,
                abs_tol=1.0e-15,
            )
            for key, physical_key in (
                ("diameterM", "diameterM"),
                ("centrelineRadiusM", "centrelineRadiusM"),
                ("inletLegLengthM", "inletLegLengthM"),
                ("outletLegLengthM", "outletLegLengthM"),
                ("bendAngleDegrees", "bendAngleDegrees"),
            )
        ),
        "runtimeDimensions": geometry["maximumRelativeDimensionError"]
        <= geometry_limit,
    }
    solver_checks = {
        "exitCode": solver_exit_code == int(solver_limits["exitCode"]),
        "normalTermination": normal_termination,
        "reachedDeclaredStop": reached_declared_stop,
        "finitePressureAndVelocity": field_physics[
            "finitePressureAndVelocity"
        ],
        **{
            f"qoiTail.{name}": passed
            for name, passed in qoi_tail["checks"].items()
        },
    }
    total_pressure_gain_allowance = (
        float(
            physics_limits[
                "maximumTotalPressureGainFractionOfInletDynamicPressure"
            ]
        )
        * float(selected["physicalCase"]["inletDynamicPressurePa"])
    )
    physics_checks = {
        "relativeMassFlowImbalance": mass_imbalance
        <= float(physics_limits["maximumRelativeMassFlowImbalance"]),
        "positiveStaticPressureLoss": pressure_loss > 0.0,
        "noUnexplainedTotalPressureGain": field_physics["totalPressure"][
            "gainPa"
        ]
        <= total_pressure_gain_allowance,
        "symmetryPlaneError": field_physics["symmetry"][
            "maximumNormalizedError"
        ]
        <= float(physics_limits["maximumSymmetryPlaneError"]),
    }
    provenance_checks = {
        "explicitSourceCellIdentity": len(component_owner)
        == expected_cell_count,
        "requiredComponents": [
            row["componentId"]
            for row in ranges
        ]
        == ["inlet-leg", "elbow", "outlet-leg"],
        "completeNonOverlappingCoverage": sorted(component_owner)
        == list(range(expected_cell_count)),
    }
    gate_groups = {
        "geometry": geometry_checks,
        "mesh": mesh_checks,
        "solver": solver_checks,
        "physics": physics_checks,
        "provenance": provenance_checks,
    }
    gates = {
        name: {
            "passed": all(checks.values()),
            "checks": checks,
        }
        for name, checks in gate_groups.items()
    }
    return {
        "schema": LEVEL_SCHEMA,
        "caseId": CASE_ID,
        "level": level["id"],
        "status": "captured-evaluated-experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "allPerLevelGatesPassed": all(
            group["passed"]
            for group in gates.values()
        ),
        "gates": gates,
        "geometry": geometry,
        "mesh": {
            "cellCount": cell_count,
            "hexahedronCount": hex_count,
            "connectedRegions": region_count,
            "geometricDirections": int(directions.group(1)),
            "solutionDirections": int(directions.group(2)),
            "invalidCellCount": 0 if "Mesh OK." in check_mesh else None,
            "patches": patches,
            "quality": quality,
            "runtimeVtkPath": str(vtk_path.relative_to(case_dir)),
            "wallGeometry": profile["topology"]["wallGeometry"],
        },
        "solver": {
            "exitCode": solver_exit_code,
            "simpleConverged": simple_converged,
            "lastIteration": last_iteration,
            "reachedDeclaredStop": reached_declared_stop,
            "normalTermination": normal_termination,
            "fatalSolverMarkers": fatal_solver_markers,
            "qoiTailStability": qoi_tail,
        },
        "qoi": {
            "staticPressureLossPa": pressure_loss,
            "inletVolumetricFlowRateM3PerS": inlet_flow,
            "outletVolumetricFlowRateM3PerS": outlet_flow,
            "measuredVolumetricFlowRateM3PerS": measured_flow,
            "relativeMassFlowImbalance": mass_imbalance,
            **field_physics,
        },
        "sourceCellProvenance": {
            "sourceCellCount": len(component_owner),
            "ranges": ranges,
        },
        "characteristicCellSizeM": (
            float(selected["physicalCase"]["analyticFluidVolumeM3"])
            / expected_cell_count
        )
        ** (1.0 / 3.0),
        "provenance": {
            "profileSha256": _sha256_file(profile_path),
            "caseManifestSha256": _sha256_file(
                case_dir / adapters.CASE_MANIFEST_PATH
            ),
            "solverLogSha256": _sha256_file(solver_log_path),
            "checkMeshSha256": _sha256_file(check_mesh_path),
            "polyMeshBoundarySha256": _sha256_file(boundary_path),
            "runtimeVtkSha256": _sha256_file(vtk_path),
            "diagnosticsAcceptanceSha256": (
                _sha256_file(
                    case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH
                )
                if (
                    case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH
                ).is_file()
                else None
            ),
        },
    }


def _sequence_assessment(
    evaluations: dict[str, dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    levels = _level_rows(contract)
    samples = [
        {
            "id": str(level["id"]),
            "source": "solver-produced",
            "sourceArtifactSha256": evaluations[str(level["id"])][
                "provenance"
            ]["runtimeVtkSha256"],
            "characteristicCellSizeM": float(
                evaluations[str(level["id"])][
                    "characteristicCellSizeM"
                ]
            ),
            "value": float(
                evaluations[str(level["id"])]["qoi"][
                    "staticPressureLossPa"
                ]
            ),
        }
        for level in levels
    ]
    pressure_losses = [float(row["value"]) for row in samples]
    monotone = (
        pressure_losses[0] < pressure_losses[1] < pressure_losses[2]
        or pressure_losses[0] > pressure_losses[1] > pressure_losses[2]
    )
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
    limits = contract["gates"]["sequence"]
    gates = {
        "pressureLossMonotone": monotone,
        "gciMathematicallyQualified": bool(
            grid_record.get("qualified")
        ),
        "observedOrder": bool(grid_record.get("qualified"))
        and float(limits["minimumObservedOrder"])
        <= float(grid_record["observedOrder"])
        <= float(limits["maximumObservedOrder"]),
        "fineGridGci": bool(grid_record.get("qualified"))
        and float(grid_record["fineGridGciPercent"])
        <= float(limits["maximumFineGridGciPercent"]),
    }
    return {
        "pressureLossPa": pressure_losses,
        "gridConvergence": grid_record,
        "gates": gates,
        "passed": all(gates.values()),
        "interpretation": contract["refinementInterpretation"][
            "geometryTreatment"
        ],
    }


def _artifact_manifest(campaign_dir: Path) -> dict[str, Any]:
    manifest_path = campaign_dir / "artifact-manifest.json"
    entries: list[dict[str, Any]] = []
    for path in sorted(campaign_dir.rglob("*")):
        if not path.is_file() or path == manifest_path:
            continue
        entries.append(
            {
                "path": str(path.relative_to(campaign_dir)),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    tree_digest = hashlib.sha256(
        "".join(
            f"{row['path']}\0{row['sha256']}\0{row['size']}\n"
            for row in entries
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "caseId": CASE_ID,
        "artifactCount": len(entries),
        "treeDigestSha256": tree_digest,
        "artifacts": entries,
    }
    _write_json(manifest_path, manifest)
    return manifest


def _freeze_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif child.is_dir():
            child.chmod(
                stat.S_IRUSR
                | stat.S_IXUSR
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
    path.chmod(
        stat.S_IRUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )


def execute_campaign(
    output_dir: Path,
    *,
    poll_interval_seconds: float = 0.25,
    timeout_seconds_per_level: float = 7200.0,
    freeze: bool = True,
) -> dict[str, Any]:
    """Run all levels through JobManager and retain any failure in place."""

    if poll_interval_seconds <= 0.0 or timeout_seconds_per_level <= 0.0:
        raise CurvedElbowCampaignError(
            "poll and timeout values must be positive"
        )
    materialized = materialize_campaign(output_dir)
    output_dir = output_dir.resolve()
    contract = load_contract()
    source_control = _source_control_identity()
    runtime_environment = _runtime_environment_identity()
    manager = JobManager(runtime_root=output_dir / "runtime")
    state: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "caseId": CASE_ID,
        "status": "running",
        "scientificStatus": "experimental-qualification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "campaignManifestSha256": _sha256_file(
            output_dir / "campaign-manifest.json"
        ),
        "sourceControl": source_control,
        "runtimeEnvironment": runtime_environment,
        "levels": [],
    }
    _write_json(output_dir / "qualification-report.json", state)
    evaluations: dict[str, dict[str, Any]] = {}
    for level in _level_rows(contract):
        case = build_level_case(level, contract)
        queued = manager.queue_job(case)
        record: dict[str, Any] = {
            "level": level["id"],
            "solverCaseId": case.id,
            "jobId": queued.id,
            "status": queued.status,
            "execution": queued.execution,
            "command": queued.command,
            "caseDirectory": None,
            "evaluationPath": None,
        }
        state["levels"].append(record)
        _write_json(output_dir / "qualification-report.json", state)
        deadline = time.monotonic() + timeout_seconds_per_level
        terminal = queued
        while terminal.status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                manager.cancel_job(terminal.id)
                record["status"] = "cancelled-timeout"
                state["status"] = "failed-retained-infrastructure"
                _write_json(output_dir / "qualification-report.json", state)
                raise CurvedElbowCampaignError(
                    f"{level['id']} exceeded the frozen execution timeout"
                )
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                state["status"] = "failed-retained-infrastructure"
                _write_json(output_dir / "qualification-report.json", state)
                raise CurvedElbowCampaignError(
                    f"JobManager lost {level['id']} job record"
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
        if not terminal.caseDir:
            state["status"] = "failed-retained-infrastructure"
            _write_json(output_dir / "qualification-report.json", state)
            raise CurvedElbowCampaignError(
                f"{level['id']} completed without a retained case directory"
            )
        case_dir = Path(terminal.caseDir).resolve()
        if not case_dir.is_relative_to(output_dir):
            raise CurvedElbowCampaignError(
                f"{level['id']} evidence escaped the campaign directory"
            )
        record["caseDirectory"] = str(case_dir.relative_to(output_dir))
        if terminal.status != "complete" or terminal.exitCode != 0:
            state["status"] = "failed-retained-infrastructure"
            state["finishedAt"] = datetime.now(timezone.utc).isoformat()
            _write_json(output_dir / "qualification-report.json", state)
            _artifact_manifest(output_dir)
            if freeze:
                _freeze_tree(output_dir)
            return state
        evaluation = evaluate_completed_level(
            case_dir,
            level,
            solver_exit_code=int(terminal.exitCode),
            contract=contract,
        )
        evaluation["jobId"] = terminal.id
        evaluation["solverCaseId"] = case.id
        evaluation_path = output_dir / "evaluations" / (
            f"{level['id']}.json"
        )
        _write_json(evaluation_path, evaluation)
        record["evaluationPath"] = str(
            evaluation_path.relative_to(output_dir)
        )
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allPerLevelGatesPassed"] = bool(
            evaluation["allPerLevelGatesPassed"]
        )
        evaluations[str(level["id"])] = evaluation
        _write_json(output_dir / "qualification-report.json", state)
        if not evaluation["allPerLevelGatesPassed"]:
            state["status"] = "scientific-gate-failed-retained"
            state["scientificStatus"] = (
                "curved-elbow-qualification-gates-failed"
            )
            state["failedLevel"] = level["id"]
            state["finishedAt"] = datetime.now(timezone.utc).isoformat()
            _write_json(output_dir / "qualification-report.json", state)
            _artifact_manifest(output_dir)
            if freeze:
                _freeze_tree(output_dir)
            return state

    sequence = _sequence_assessment(evaluations, contract)
    state["sequence"] = sequence
    state["allPerLevelGatesPassed"] = True
    state["qualified"] = bool(sequence["passed"])
    state["status"] = (
        "numerical-qualification-candidate-passed"
        if sequence["passed"]
        else "scientific-sequence-gate-failed-retained"
    )
    state["scientificStatus"] = (
        "bounded-numerical-qualification-candidate"
        if sequence["passed"]
        else "curved-elbow-qualification-gates-failed"
    )
    state["finishedAt"] = datetime.now(timezone.utc).isoformat()
    state["limitations"] = contract["claim"]["excluded"]
    state["promotionAuthorized"] = False
    state["validated"] = False
    _write_json(output_dir / "qualification-report.json", state)
    artifact_manifest = _artifact_manifest(output_dir)
    if freeze:
        _freeze_tree(output_dir)
    return {
        **state,
        "artifactManifestSha256": _sha256_file(
            output_dir / "artifact-manifest.json"
        ),
        "artifactTreeDigestSha256": artifact_manifest[
            "treeDigestSha256"
        ],
        "materializedDeterminism": [
            {
                "level": row["level"],
                "generatedFileTreeSha256": row["determinism"][
                    "generatedFileTreeSha256"
                ],
            }
            for row in materialized["levels"]
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--materialize-only", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument(
        "--no-freeze",
        action="store_true",
        help="test-only: leave a completed campaign writable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.materialize_only:
        result = materialize_campaign(args.output_dir)
    else:
        result = execute_campaign(
            args.output_dir,
            freeze=not args.no_freeze,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
