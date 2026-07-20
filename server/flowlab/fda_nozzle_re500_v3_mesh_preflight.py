"""Prospective mesh-only preflight for the FDA nozzle Re=500 successor.

The runner may invoke only blockMesh and checkMesh.  It never runs a CFD
solver, postprocessing, scientific assessment, or product promotion path.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .fda_nozzle_re500 import (
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_DIGEST,
    FdaNozzleDefinition,
    _BlockMeshBuilder,
    _container_command,
    _foam_faces,
    _foam_points,
    _header,
    _now,
    _patch_face_areas,
    _polygon_area,
    _quads,
    _sha256,
    _write,
    _write_json,
    _plane,
    run_command,
)
from .fda_nozzle_re500_successor import parse_check_mesh
from .fda_nozzle_re500_v2_preflight import _render_block_mesh


SCHEMA = "flowlab.fda-nozzle-re500-v3-mesh-preflight.v1"
CASE_SCHEMA = "flowlab.fda-nozzle-re500-v3-mesh-case.v1"
CONTRACT_SCHEMA = "flowlab.fda-nozzle-re500-v3-mesh-preflight-contract.v1"
PRESSURE_SCHEMA = "flowlab.fda-nozzle-re500.pressure-reference-disposition.v1"
CAMPAIGN_ID = "2026-07-20-re500-v3-mesh-preflight"
CONTRACT_SHA256 = "8775ca7ce86ef22d8172bbec346adae21b3301dec20558d8ec6e9e66e20a1d34"
PRESSURE_DISPOSITION_SHA256 = (
    "96c390c95583fcdbb15e8cc41a31cd520c4d86e33cdb463c9a53a23150f09734"
)
LEVELS = ("coarse", "medium", "fine")
LEVEL_CELLS: dict[str, dict[str, int]] = {
    "coarse": {
        "coreTangential": 4,
        "annularTangential": 4,
        "upstreamAnnularRadial": 1,
        "inletAxial": 58,
        "contractionAxial": 45,
        "throatAxial": 80,
        "downstreamAxial": 240,
        "downstreamOuterRadial": 8,
    },
    "medium": {
        "coreTangential": 8,
        "annularTangential": 8,
        "upstreamAnnularRadial": 2,
        "inletAxial": 116,
        "contractionAxial": 90,
        "throatAxial": 160,
        "downstreamAxial": 480,
        "downstreamOuterRadial": 16,
    },
    "fine": {
        "coreTangential": 16,
        "annularTangential": 16,
        "upstreamAnnularRadial": 4,
        "inletAxial": 232,
        "contractionAxial": 180,
        "throatAxial": 320,
        "downstreamAxial": 960,
        "downstreamOuterRadial": 32,
    },
}
EXPECTED_CELLS = {"coarse": 44_256, "medium": 354_048, "fine": 2_832_384}


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_new(path: Path, description: str) -> None:
    if path.exists():
        if path.is_dir() and not any(path.iterdir()):
            return
        raise ValueError(f"refusing to overwrite existing {description}: {path}")


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(_root()).as_posix()
    except ValueError as error:
        raise ValueError(f"path must remain inside repository: {path}") from error


def _verify_contract(contract_path: Path, pressure_path: Path) -> dict[str, Any]:
    if not contract_path.is_file() or not pressure_path.is_file():
        raise ValueError("missing frozen mesh-preflight contract or pressure disposition")
    if _sha256(contract_path) != CONTRACT_SHA256:
        raise ValueError("frozen V3 mesh-preflight contract hash mismatch")
    if _sha256(pressure_path) != PRESSURE_DISPOSITION_SHA256:
        raise ValueError("frozen pressure-disposition hash mismatch")
    contract = _read_json(contract_path)
    pressure = _read_json(pressure_path)
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("unexpected V3 mesh-preflight contract schema")
    if pressure.get("schema") != PRESSURE_SCHEMA:
        raise ValueError("unexpected pressure-disposition schema")
    if pressure.get("status") != "not-qualified-nonpromotional":
        raise ValueError("pressure reference has not been formally demoted")
    authorization = contract.get("authorization", {})
    if not all(
        authorization.get(name) is True
        for name in ("prepareMeshCases", "runMeshOnlyCommands", "assessMeshPreflight")
    ):
        raise ValueError("contract does not authorize the mesh-only stages")
    if any(
        authorization.get(name) is not False
        for name in (
            "runSolver",
            "runFullSuccessorCampaign",
            "scientificPromotion",
            "desktopPromotion",
        )
    ):
        raise ValueError("contract does not fail closed outside mesh-only work")
    return contract


def v3_block_mesh(level: str) -> str:
    """Render the frozen V3 16/32/64-segment strict-all-hex family."""
    if level not in LEVEL_CELLS:
        raise ValueError(f"unsupported V3 mesh level: {level}")
    cells = LEVEL_CELLS[level]
    spec = FdaNozzleDefinition()
    builder = _BlockMeshBuilder()

    upstream_x = (
        spec.inlet_x_m,
        spec.contraction_start_x_m,
        spec.throat_start_x_m,
        spec.sudden_expansion_x_m,
    )
    upstream_planes: list[dict[str, list[int]]] = []
    for index, x in enumerate(upstream_x):
        radius = spec.radius(x - (1.0e-12 if x == 0.0 else 0.0))
        upstream_planes.append(
            _plane(
                builder,
                x,
                radius,
                core_half_width=radius / 2.0,
                prefix=f"u{index}",
            )
        )

    upstream_axial = (
        cells["inletAxial"],
        cells["contractionAxial"],
        cells["throatAxial"],
    )
    for left, right, axial in zip(
        upstream_planes, upstream_planes[1:], upstream_axial
    ):
        left_quads = _quads(left)
        right_quads = _quads(right)
        builder.block(
            left_quads[0],
            right_quads[0],
            (cells["coreTangential"], cells["coreTangential"], axial),
        )
        for left_quad, right_quad in zip(left_quads[1:], right_quads[1:]):
            builder.block(
                left_quad,
                right_quad,
                (
                    cells["upstreamAnnularRadial"],
                    cells["annularTangential"],
                    axial,
                ),
            )
            builder.boundary["wall"].append(
                (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
            )
    builder.boundary["inlet"].extend(
        tuple(quad) for quad in _quads(upstream_planes[0])
    )

    inner_start = upstream_planes[-1]
    outer_start = _plane(
        builder,
        0.0,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do0",
    )
    outer_start["core"] = inner_start["ring"]
    inner_end = _plane(
        builder,
        spec.outlet_x_m,
        spec.throat_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="di1",
    )
    outer_end = _plane(
        builder,
        spec.outlet_x_m,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do1",
    )
    outer_end["core"] = inner_end["ring"]
    axial = cells["downstreamAxial"]
    inner_left = _quads(inner_start)
    inner_right = _quads(inner_end)
    builder.block(
        inner_left[0],
        inner_right[0],
        (cells["coreTangential"], cells["coreTangential"], axial),
    )
    for left_quad, right_quad in zip(inner_left[1:], inner_right[1:]):
        builder.block(
            left_quad,
            right_quad,
            (
                cells["upstreamAnnularRadial"],
                cells["annularTangential"],
                axial,
            ),
        )
    for left_quad, right_quad in zip(
        _quads(outer_start)[1:], _quads(outer_end)[1:]
    ):
        builder.block(
            left_quad,
            right_quad,
            (
                cells["downstreamOuterRadial"],
                cells["annularTangential"],
                axial,
            ),
        )
        builder.boundary["wall"].append(tuple(left_quad))
        builder.boundary["wall"].append(
            (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
        )
    builder.boundary["outlet"].extend(
        tuple(reversed(quad))
        for quad in _quads(inner_end) + _quads(outer_end)[1:]
    )
    return _render_block_mesh(builder)


def _declared_cells(mesh: str) -> int:
    return sum(
        int(a) * int(b) * int(c)
        for a, b, c in re.findall(
            r"hex \([^)]*\) \((\d+) (\d+) (\d+)\) simpleGrading", mesh
        )
    )


def _nominal_volume(spec: FdaNozzleDefinition) -> float:
    inlet_length = spec.contraction_start_x_m - spec.inlet_x_m
    contraction_length = spec.throat_start_x_m - spec.contraction_start_x_m
    throat_length = spec.sudden_expansion_x_m - spec.throat_start_x_m
    downstream_length = spec.outlet_x_m - spec.sudden_expansion_x_m
    outer = spec.inlet_radius_m
    inner = spec.throat_radius_m
    return (
        math.pi * outer**2 * inlet_length
        + math.pi
        * contraction_length
        * (outer**2 + outer * inner + inner**2)
        / 3.0
        + math.pi * inner**2 * throat_length
        + math.pi * outer**2 * downstream_length
    )


def _mesh_only_control_dict() -> str:
    return _header("system", "controlDict") + """application blockMesh;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 0;
deltaT 1;
writeControl timeStep;
writeInterval 1;
purgeWrite 0;
writeFormat ascii;
writePrecision 16;
runTimeModifiable false;
functions {}
"""


def _foam_labels(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\n\s*(\d+)\s*\n\s*\(\s*([\d\s]+?)\s*\)", text)
    if match is None:
        raise ValueError(f"cannot parse OpenFOAM label list: {path}")
    expected = int(match.group(1))
    values = [int(value) for value in re.findall(r"\b\d+\b", match.group(2))]
    if len(values) != expected:
        raise ValueError(
            f"OpenFOAM label-list length mismatch in {path}: {len(values)} != {expected}"
        )
    return values


def _internal_plane_area(case: Path, x_coordinate: float) -> float:
    poly_mesh = case / "constant" / "polyMesh"
    points = _foam_points(poly_mesh / "points")
    faces = _foam_faces(poly_mesh / "faces")
    internal_count = len(_foam_labels(poly_mesh / "neighbour"))
    area = 0.0
    face_count = 0
    for face in faces[:internal_count]:
        face_points = [points[index] for index in face]
        xs = [point[0] for point in face_points]
        if max(xs) - min(xs) > 1.0e-12:
            continue
        if abs(sum(xs) / len(xs) - x_coordinate) > 1.0e-12:
            continue
        area += _polygon_area(face_points)
        face_count += 1
    if face_count == 0 or area <= 0.0:
        raise ValueError(f"no internal cross-section faces found at x={x_coordinate}")
    return area


def _geometry_metrics(case: Path, check_mesh: dict[str, Any]) -> dict[str, Any]:
    spec = FdaNozzleDefinition()
    actual = {
        "totalDomainVolume": float(check_mesh["totalVolumeM3"]),
        "inletCrossSectionArea": sum(_patch_face_areas(case, "inlet")),
        "outletCrossSectionArea": sum(_patch_face_areas(case, "outlet")),
        "throatCrossSectionArea": _internal_plane_area(case, spec.throat_start_x_m),
    }
    nominal = {
        "totalDomainVolume": _nominal_volume(spec),
        "inletCrossSectionArea": math.pi * spec.inlet_radius_m**2,
        "outletCrossSectionArea": math.pi * spec.inlet_radius_m**2,
        "throatCrossSectionArea": math.pi * spec.throat_radius_m**2,
    }
    return {
        name: {
            "actual": actual[name],
            "nominal": nominal[name],
            "relativeError": actual[name] / nominal[name] - 1.0,
        }
        for name in actual
    }


def prepare_campaign(
    output: Path, contract_path: Path, pressure_path: Path
) -> dict[str, Any]:
    contract = _verify_contract(contract_path, pressure_path)
    expected_output = contract["outputPolicy"]["rawCampaign"]
    if _relative_to_root(output) != expected_output:
        raise ValueError(f"output must match frozen contract path: {expected_output}")
    _require_new(output, "mesh-preflight campaign")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(contract_path, output / "mesh-preflight-contract.json")
    shutil.copy2(pressure_path, output / "pressure-reference-disposition.json")

    case_rows: list[dict[str, Any]] = []
    for label in LEVELS:
        case = output / "cases" / label
        case.mkdir(parents=True, exist_ok=False)
        mesh = v3_block_mesh(label)
        if _declared_cells(mesh) != EXPECTED_CELLS[label]:
            raise ValueError(f"declared cell budget mismatch for {label}")
        _write(case / "system" / "blockMeshDict", mesh)
        _write(case / "system" / "controlDict", _mesh_only_control_dict())
        definition = {
            "schema": CASE_SCHEMA,
            "label": label,
            "meshOnly": True,
            "expectedCells": EXPECTED_CELLS[label],
            "circumferentialBoundarySegments": 4
            * LEVEL_CELLS[label]["annularTangential"],
            "cells": LEVEL_CELLS[label],
            "frozenContractSha256": CONTRACT_SHA256,
            "solverExecutionAuthorized": False,
            "scientificPromotionAuthorized": False,
            "desktopPromotionAuthorized": False,
            "promotionAuthorized": False,
        }
        _write_json(case / "case-definition.json", definition)
        case_rows.append(
            {
                "label": label,
                "expectedCells": EXPECTED_CELLS[label],
                "caseDefinitionSha256": _sha256(case / "case-definition.json"),
                "blockMeshDictSha256": _sha256(case / "system" / "blockMeshDict"),
                "controlDictSha256": _sha256(case / "system" / "controlDict"),
            }
        )

    source_paths = {
        "meshPreflightSource": Path(__file__).resolve(),
        "baseFdaSource": _root() / "server/flowlab/fda_nozzle_re500.py",
        "meshRendererSource": _root()
        / "server/flowlab/fda_nozzle_re500_v2_preflight.py",
        "successorAuditSource": _root()
        / "server/flowlab/fda_nozzle_re500_successor.py",
    }
    manifest = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "createdAt": _now(),
        "status": "prepared-awaiting-mesh-only-execution",
        "contractSha256": _sha256(output / "mesh-preflight-contract.json"),
        "pressureDispositionSha256": _sha256(
            output / "pressure-reference-disposition.json"
        ),
        "sourceSha256": {
            name: _sha256(path) for name, path in sorted(source_paths.items())
        },
        "image": DEFAULT_IMAGE,
        "imageDigest": DEFAULT_IMAGE_DIGEST,
        "cases": case_rows,
        "solverExecutionAuthorized": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output / "campaign-manifest.json", manifest)
    return manifest


def _verify_prepared(output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = output / "campaign-manifest.json"
    contract_path = output / "mesh-preflight-contract.json"
    pressure_path = output / "pressure-reference-disposition.json"
    if not all(path.is_file() for path in (manifest_path, contract_path, pressure_path)):
        raise ValueError("mesh-preflight campaign is not prepared")
    contract = _verify_contract(contract_path, pressure_path)
    manifest = _read_json(manifest_path)
    if manifest.get("contractSha256") != CONTRACT_SHA256:
        raise ValueError("prepared manifest contract hash mismatch")
    if manifest.get("pressureDispositionSha256") != PRESSURE_DISPOSITION_SHA256:
        raise ValueError("prepared manifest pressure-disposition hash mismatch")
    if manifest.get("solverExecutionAuthorized") is not False:
        raise ValueError("prepared campaign does not fail closed for solver execution")
    return manifest, contract


def _image_identity(image: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}} {{.Architecture}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot inspect pinned image: {result.stderr.strip()}")
    fields = result.stdout.strip().split()
    if len(fields) != 2:
        raise ValueError("unexpected Docker image identity output")
    identity = {"id": fields[0], "architecture": fields[1]}
    if identity != {"id": DEFAULT_IMAGE_DIGEST, "architecture": "arm64"}:
        raise ValueError(f"pinned Docker image identity mismatch: {identity}")
    return identity


def mesh_case(output: Path, label: str, image: str = DEFAULT_IMAGE) -> dict[str, Any]:
    if label not in LEVELS:
        raise ValueError(f"unsupported V3 mesh level: {label}")
    case = output / "cases" / label
    result_path = output / "results" / label / "mesh-preflight.json"
    logs = output / "logs" / label
    if result_path.exists() or logs.exists() or (case / "constant/polyMesh").exists():
        raise ValueError(f"refusing to overwrite mesh evidence for {label}")
    definition = _read_json(case / "case-definition.json")
    codes: dict[str, int] = {}
    workspace = _root()
    for name, command in (
        ("blockMesh", "blockMesh"),
        ("checkMesh", "checkMesh -allTopology -allGeometry"),
    ):
        codes[name] = run_command(
            _container_command(image, workspace, case, command),
            case,
            logs / f"{name}.log",
        )
        if codes[name] != 0:
            break
    check_log = logs / "checkMesh.log"
    mesh = parse_check_mesh(check_log.read_text(encoding="utf-8")) if check_log.is_file() else {}
    geometry = _geometry_metrics(case, mesh) if mesh.get("checkMeshPassed") else {}
    checks = {
        "commandsComplete": codes == {"blockMesh": 0, "checkMesh": 0},
        "meshOk": bool(mesh.get("checkMeshPassed")),
        "strictAllHex": bool(mesh.get("strictAllHex")),
        "expectedCellCount": mesh.get("cells") == definition["expectedCells"],
        "geometryMetricsComplete": set(geometry)
        == {
            "totalDomainVolume",
            "inletCrossSectionArea",
            "outletCrossSectionArea",
            "throatCrossSectionArea",
        },
    }
    report = {
        "schema": SCHEMA,
        "label": label,
        "checkedAt": _now(),
        "exitCodes": codes,
        "mesh": mesh,
        "geometry": geometry,
        "checks": checks,
        "passesCasePreflight": all(checks.values()),
        "solverExecutionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(result_path, report)
    return report


def mesh_all(
    output: Path, image: str = DEFAULT_IMAGE, max_workers: int = 1
) -> dict[str, Any]:
    _verify_prepared(output)
    if max_workers != 1:
        raise ValueError("frozen contract requires exactly one mesh worker")
    execution_path = output / "mesh-execution.json"
    _require_new(execution_path, "mesh-execution record")
    identity = _image_identity(image)
    reports: dict[str, Any] = {}
    for label in LEVELS:
        reports[label] = mesh_case(output, label, image)
        if not reports[label]["passesCasePreflight"]:
            break
    result = {
        "schema": SCHEMA,
        "checkedAt": _now(),
        "image": image,
        "imageIdentity": identity,
        "maximumMeshWorkers": max_workers,
        "commands": ["blockMesh", "checkMesh -allTopology -allGeometry"],
        "solverCommandsInvoked": [],
        "cases": reports,
        "passesExecutionStage": len(reports) == len(LEVELS)
        and all(report["passesCasePreflight"] for report in reports.values()),
        "solverExecutionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(execution_path, result)
    return result


def _strictly_decreasing(values: list[float]) -> bool:
    return all(right < left for left, right in zip(values, values[1:]))


def geometry_acceptance(
    levels: dict[str, Any], quantities: tuple[str, ...], tolerance: float
) -> tuple[dict[str, bool], dict[str, bool]]:
    """Evaluate only the prospectively frozen geometry gates."""
    monotonic = {
        quantity: _strictly_decreasing(
            [
                abs(levels[label]["geometry"][quantity]["relativeError"])
                for label in LEVELS
            ]
        )
        for quantity in quantities
    }
    fine = {
        quantity: abs(levels["fine"]["geometry"][quantity]["relativeError"])
        <= tolerance
        for quantity in quantities
    }
    return monotonic, fine


def _compact_report(assessment: dict[str, Any]) -> str:
    lines = [
        "# FDA Re=500 V3 mesh-only preflight",
        "",
        f"Status: **{assessment['status']}**",
        "",
        "Pressure is formally nonpromotional. This preflight assesses only the new strict-all-hex mesh family; it does not run a solver or authorize scientific or desktop promotion.",
        "",
        "| Level | Status | Cells | Boundary segments | Volume error | Inlet error | Outlet error | Throat error |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label in LEVELS:
        row = assessment["levels"][label]
        geometry = row["geometry"]
        def error(name: str) -> str:
            item = geometry.get(name)
            return f"{item['relativeError']:.6%}" if item else "not available"

        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    row["status"],
                    f"{row['cells']:,}" if row["cells"] is not None else "not available",
                    str(row["circumferentialBoundarySegments"]),
                    error("totalDomainVolume"),
                    error("inletCrossSectionArea"),
                    error("outletCrossSectionArea"),
                    error("throatCrossSectionArea"),
                ]
            )
            + " |"
        )
    failure = assessment.get("failure")
    if failure:
        lines.extend(
            [
                "",
                "## Failure classification",
                "",
                f"- Classification: `{failure['classification']}`",
                f"- Stage: `{failure['stage']}`",
                f"- Reason: `{failure['reason']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            *[
                f"- `{name}`: {'pass' if passed else 'fail'}"
                for name, passed in assessment["gates"].items()
            ],
            "",
            "Later numerical uncertainty must be labelled `combined-geometry-and-solution-discretization`. A passing mesh preflight authorizes only design of the next numerical-verification campaign.",
            "",
        ]
    )
    return "\n".join(lines)


def assess(
    output: Path, compact_output: Path, compact_report: Path
) -> dict[str, Any]:
    manifest, contract = _verify_prepared(output)
    expected_compact = contract["outputPolicy"]["compactAssessment"]
    expected_report = contract["outputPolicy"]["compactReport"]
    if _relative_to_root(compact_output) != expected_compact:
        raise ValueError(f"compact output must match frozen path: {expected_compact}")
    if _relative_to_root(compact_report) != expected_report:
        raise ValueError(f"compact report must match frozen path: {expected_report}")
    raw_assessment = output / "assessment.json"
    raw_report = output / "REPORT.md"
    for path, description in (
        (raw_assessment, "raw assessment"),
        (raw_report, "raw report"),
        (compact_output, "compact assessment"),
        (compact_report, "compact report"),
    ):
        _require_new(path, description)
    execution_path = output / "mesh-execution.json"
    if not execution_path.is_file():
        raise ValueError("missing mesh-execution record")
    execution = _read_json(execution_path)
    reports = {
        label: (
            _read_json(output / "results" / label / "mesh-preflight.json")
            if (output / "results" / label / "mesh-preflight.json").is_file()
            else None
        )
        for label in LEVELS
    }
    levels: dict[str, Any] = {}
    for label in LEVELS:
        definition = _read_json(output / "cases" / label / "case-definition.json")
        report = reports[label]
        mesh = report.get("mesh", {}) if report else {}
        geometry = report.get("geometry", {}) if report else {}
        levels[label] = {
            "status": (
                "passed"
                if report and report.get("passesCasePreflight")
                else "failed"
                if report
                else "not-run"
            ),
            "cells": mesh.get("cells"),
            "hexahedra": mesh.get("hexahedra"),
            "circumferentialBoundarySegments": definition[
                "circumferentialBoundarySegments"
            ],
            "maximumNonOrthogonalityDegrees": mesh.get(
                "maximumNonOrthogonalityDegrees"
            ),
            "geometry": geometry,
            "caseChecks": report.get("checks", {}) if report else {},
        }

    quantities = tuple(contract["geometryAcceptance"]["quantities"])
    tolerance = float(
        contract["geometryAcceptance"]["fineMaximumAbsoluteRelativeError"]
    )
    geometry_complete = all(
        quantity in levels[label]["geometry"]
        for label in LEVELS
        for quantity in quantities
    )
    if geometry_complete:
        geometry_monotonic, fine_geometry = geometry_acceptance(
            levels, quantities, tolerance
        )
    else:
        geometry_monotonic = {quantity: False for quantity in quantities}
        fine_geometry = {quantity: False for quantity in quantities}
    actual_cells = [levels[label]["cells"] for label in LEVELS]
    cells_complete = all(value is not None for value in actual_cells)
    cell_ratios = (
        [
            int(actual_cells[index + 1]) / int(actual_cells[index])
            for index in range(len(actual_cells) - 1)
        ]
        if cells_complete
        else []
    )
    gates = {
        "pressureDispositionResolvedNonpromotional": (
            manifest["pressureDispositionSha256"] == PRESSURE_DISPOSITION_SHA256
        ),
        "allMeshCommandsComplete": all(
            bool(report and report.get("checks", {}).get("commandsComplete"))
            for report in reports.values()
        ),
        "allMeshesOpenFoamOk": all(
            bool(report and report.get("checks", {}).get("meshOk"))
            for report in reports.values()
        ),
        "allMeshesStrictHex": all(
            bool(report and report.get("checks", {}).get("strictAllHex"))
            for report in reports.values()
        ),
        "allExpectedCellCountsExact": cells_complete
        and actual_cells == [EXPECTED_CELLS[label] for label in LEVELS],
        "volumetricCellRatioEight": cell_ratios == [8.0, 8.0],
        "allGeometryQuantitiesConvergeMonotonically": all(
            geometry_monotonic.values()
        ),
        "allFineGeometryErrorsWithinOnePercent": all(fine_geometry.values()),
        "onlyMeshCommandsInvoked": execution.get("solverCommandsInvoked") == [],
        "frozenContractIntegrity": manifest["contractSha256"] == CONTRACT_SHA256,
    }
    passed = all(gates.values())
    failure = None
    if not execution.get("passesExecutionStage"):
        coarse_log = output / "logs" / "coarse" / "blockMesh.log"
        log_text = (
            coarse_log.read_text(encoding="utf-8", errors="replace")
            if coarse_log.is_file()
            else ""
        )
        missing_control = "cannot find file" in log_text and "controlDict" in log_text
        failure = {
            "classification": "infrastructure-preparation-failure",
            "stage": "coarse-blockMesh",
            "reason": (
                "missing-required-controlDict"
                if missing_control
                else "mesh-execution-gate-failed"
            ),
            "scientificGateEvaluated": False,
        }
    evidence_paths = {
        "contract": output / "mesh-preflight-contract.json",
        "pressureDisposition": output / "pressure-reference-disposition.json",
        "manifest": output / "campaign-manifest.json",
        "meshExecution": output / "mesh-execution.json",
        "source": Path(__file__).resolve(),
    }
    for label in LEVELS:
        evidence_paths[f"{label}BlockMeshDict"] = (
            output / "cases" / label / "system" / "blockMeshDict"
        )
        for suffix, path in (
            ("BlockMeshLog", output / "logs" / label / "blockMesh.log"),
            ("CheckMeshLog", output / "logs" / label / "checkMesh.log"),
            (
                "CaseReport",
                output / "results" / label / "mesh-preflight.json",
            ),
        ):
            if path.is_file():
                evidence_paths[f"{label}{suffix}"] = path
    assessment = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "assessedAt": _now(),
        "status": "mesh-preflight-passed" if passed else "mesh-preflight-blocked",
        "pressureReferenceStatus": "not-qualified-nonpromotional",
        "successorContextOfUse": contract["contextOfUse"],
        "geometryTolerance": tolerance,
        "geometryUncertaintyLabel": contract["geometryAcceptance"][
            "laterNumericalUncertaintyLabel"
        ],
        "levels": levels,
        "cellRatios": cell_ratios,
        "geometryMonotonic": geometry_monotonic,
        "fineGeometryWithinTolerance": fine_geometry,
        "gates": gates,
        "failure": failure,
        "preparedSourceSha256": manifest["sourceSha256"],
        "assessmentSourceSha256": _sha256(Path(__file__).resolve()),
        "evidenceSha256": {
            name: _sha256(path) for name, path in sorted(evidence_paths.items())
        },
        "authorization": {
            "nextNumericalVerificationCampaignDesign": passed,
            "runSolver": False,
            "runFullSuccessorCampaign": False,
            "scientificPromotion": False,
            "desktopPromotion": False,
        },
        "solverExecutionAuthorized": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    report_text = _compact_report(assessment)
    _write_json(raw_assessment, assessment)
    _write(raw_report, report_text)
    _write_json(compact_output, assessment)
    _write(compact_report, report_text)
    return assessment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--contract", type=Path, required=True)
    prepare.add_argument("--pressure-disposition", type=Path, required=True)
    mesh = sub.add_parser("mesh-all")
    mesh.add_argument("--output", type=Path, required=True)
    mesh.add_argument("--image", default=DEFAULT_IMAGE)
    mesh.add_argument("--max-workers", type=int, default=1)
    assessment = sub.add_parser("assess")
    assessment.add_argument("--output", type=Path, required=True)
    assessment.add_argument("--compact-output", type=Path, required=True)
    assessment.add_argument("--compact-report", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        result = prepare_campaign(
            args.output.resolve(),
            args.contract.resolve(),
            args.pressure_disposition.resolve(),
        )
        success = result["status"] == "prepared-awaiting-mesh-only-execution"
    elif args.command == "mesh-all":
        result = mesh_all(
            args.output.resolve(), image=args.image, max_workers=args.max_workers
        )
        success = bool(result["passesExecutionStage"])
    else:
        result = assess(
            args.output.resolve(),
            args.compact_output.resolve(),
            args.compact_report.resolve(),
        )
        success = result["status"] == "mesh-preflight-passed"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
