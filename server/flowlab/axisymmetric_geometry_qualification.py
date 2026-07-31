"""Run the frozen experimental qualification for bounded axisymmetric geometry.

This campaign is deliberately separate from the stricter straight-pipe
verification campaign. Passing it qualifies only the experimental generated
geometry and result-identity software path.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import platform
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Sequence

from . import adapters
from .execution import (
    TERMINAL_STATUSES,
    JobManager,
    materialize_case_files,
    read_case_artifact,
    read_case_artifact_preview,
    validate_solver_case,
)
from .result_identity import (
    SOURCE_IDENTITY_CONTRACT_PATH,
    SOURCE_IDENTITY_REPORT_PATH,
    SOURCE_IDENTITY_REPORT_SCHEMA,
)
from .results import parse_vtk_result
from .schemas import CaseRequest, SolverCase
from .verification import VerificationInputError, richardson_grid_convergence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "validation"
    / "axisymmetric-geometry-experimental-qualification"
    / "EXPERIMENTAL_QUALIFICATION_CONTRACT_V1.json"
)
RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK_V1.md")
CAMPAIGN_SCHEMA = "flowlab.axisymmetric-geometry-experimental-qualification-campaign.v1"
LEVEL_SCHEMA = "flowlab.axisymmetric-geometry-experimental-qualification-level.v1"
RESULT_PIPELINE_SCHEMA = "flowlab.axisymmetric-multi-edge-result-pipeline-proof.v1"
EXPECTED_PATCHES = {
    "inlet": "patch",
    "outlet": "patch",
    "walls": "wall",
    "front": "wedge",
    "back": "wedge",
    "axis": "empty",
}
FROZEN_PATHS = [
    str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
    str(RUNBOOK_PATH.relative_to(REPOSITORY_ROOT)),
    "server/flowlab/adapters.py",
    "server/flowlab/axisymmetric_geometry_qualification.py",
    "server/flowlab/execution.py",
    "server/flowlab/mesh.py",
    "server/flowlab/result_identity.py",
    "server/flowlab/results.py",
    "server/flowlab/schemas.py",
    "server/flowlab/verification.py",
    "server/tests/test_adapters.py",
    "server/tests/test_axisymmetric_geometry_qualification.py",
    "server/tests/test_result_identity.py",
    "src/App.tsx",
    "src/App.resultLink.test.ts",
    "src/results/vtk.ts",
    "src/results/vtk.test.ts",
    "src/types.ts",
    "tests/e2e/editor.spec.ts",
]


class AxisymmetricGeometryQualificationError(RuntimeError):
    """Raised when a prospective qualification condition cannot be satisfied."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AxisymmetricGeometryQualificationError(
            f"could not read required JSON object: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise AxisymmetricGeometryQualificationError(
            f"required JSON root is not an object: {path}"
        )
    return value


def load_frozen_contract() -> tuple[dict[str, Any], str]:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    try:
        contract = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AxisymmetricGeometryQualificationError(
            "experimental qualification contract is invalid JSON"
        ) from exc
    if (
        not isinstance(contract, dict)
        or contract.get("schema")
        != "flowlab.axisymmetric-geometry-experimental-qualification-contract.v1"
        or contract.get("contractId")
        != adapters.AXISYMMETRIC_QUALIFICATION_CONTRACT_ID
        or contract.get("status")
        != "prospective-frozen-before-retained-scientific-execution"
        or contract.get("promotionAuthorized") is not False
    ):
        raise AxisymmetricGeometryQualificationError(
            "experimental qualification contract is unsupported or not prospectively frozen"
        )
    return contract, _sha256_text(text)


def _base_project(name: str) -> dict[str, Any]:
    return {
        "version": 1,
        "name": name,
        "fluid": {
            "density": 1000.0,
            "dynamicViscosity": 0.001,
            "temperature": 293.15,
            "vaporPressure": 2340.0,
            "bulkModulus": 2.2e9,
        },
        "nodes": {},
        "edges": {},
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": "coarse",
            "runMode": "steady",
            "meshMode": "axisymmetric",
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


def _single_edge_project(case_spec: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case_spec["id"])
    edge_type = str(case_spec["edgeType"])
    inlet = float(case_spec["inletDiameterM"])
    outlet = float(case_spec["outletDiameterM"])
    project = _base_project(f"Axisymmetric generation-only {case_id}")
    project["nodes"] = {
        "source": {
            "id": "source",
            "type": "source",
            "position": {"x": 0.0, "y": 0.0},
            "rotation": 0.0,
            "pressure": 120000.0,
        },
        "sink": {
            "id": "sink",
            "type": "sink",
            "position": {"x": 1000.0, "y": 0.0},
            "rotation": 0.0,
            "pressure": 101325.0,
            "flowDemand": 5.0e-6,
        },
    }
    edge: dict[str, Any] = {
        "id": case_id,
        "type": edge_type,
        "from": "source",
        "to": "sink",
        "fromPort": "outlet",
        "toPort": "inlet",
        "length": 0.06,
        "shape": {"kind": "circular", "diameter": inlet},
        "outletDiameter": outlet,
    }
    if edge_type == "venturi":
        edge.update(
            {
                "throatDiameter": float(case_spec["throatDiameterM"]),
                "throatPosition": 0.5,
                "throatLength": 0.012,
            }
        )
    project["edges"] = {case_id: edge}
    project["solver"]["meshControls"] = {
        "axisymmetricAxialCells": 12,
        "axisymmetricRadialCells": 4,
        "transverseDistribution": "uniform",
    }
    return project


def _runtime_project(
    contract: dict[str, Any],
    contract_sha256: str,
    level: dict[str, Any],
) -> dict[str, Any]:
    physical = contract["physicalCase"]
    project = _base_project(
        f"Axisymmetric contraction-throat-recovery qualification ({level['id']})"
    )
    nodes: dict[str, Any] = {}
    edges: dict[str, Any] = {}
    edge_specs = physical["edges"]
    for index in range(len(edge_specs) + 1):
        node_id = f"node-{index}"
        nodes[node_id] = {
            "id": node_id,
            "type": "source" if index == 0 else "sink" if index == len(edge_specs) else "junction",
            "position": {"x": float(index * 300), "y": 0.0},
            "rotation": 0.0,
            **({"pressure": 120000.0} if index == 0 else {}),
            **(
                {
                    "pressure": 101325.0,
                    "flowDemand": float(physical["volumetricFlowRateM3PerS"]),
                }
                if index == len(edge_specs)
                else {}
            ),
        }
    for index, spec in enumerate(edge_specs):
        edge_id = str(spec["id"])
        edges[edge_id] = {
            "id": edge_id,
            "type": str(spec["type"]),
            "from": f"node-{index}",
            "to": f"node-{index + 1}",
            "fromPort": "outlet",
            "toPort": "inlet",
            "length": float(spec["lengthM"]),
            "shape": {
                "kind": "circular",
                "diameter": float(spec["inletDiameterM"]),
            },
            "outletDiameter": float(spec["outletDiameterM"]),
        }
    project["nodes"] = nodes
    project["edges"] = edges
    project["solver"].update(
        {
            "meshResolution": str(level["id"]),
            "meshControls": {
                "axisymmetricAxialCellsByEdge": {
                    str(key): int(value)
                    for key, value in level["axialCellsByEdge"].items()
                },
                "axisymmetricRadialCells": int(level["radialCells"]),
                "transverseDistribution": "uniform",
            },
            "axisymmetricQualification": {
                "contractId": contract["contractId"],
                "contractSha256": contract_sha256,
                "caseId": physical["id"],
                "qoiHistoryWriteIntervalIterations": 1,
            },
        }
    )
    return project


def _build_case(project: dict[str, Any]) -> SolverCase:
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    issues = validate_solver_case(case)
    if issues:
        raise AxisymmetricGeometryQualificationError(
            "generated case failed validation: " + "; ".join(issues)
        )
    return case


def _file_hashes(case: SolverCase) -> dict[str, str]:
    return {
        path: _sha256_text(content)
        for path, content in sorted(case.files.items())
    }


def _cell_volume(points: list[list[float]], cell: list[int]) -> float:
    if len(cell) != 8:
        raise AxisymmetricGeometryQualificationError(
            "axisymmetric preview requires eight-node hexahedral cells"
        )

    def tetra(a: int, b: int, c: int, d: int) -> float:
        pa, pb, pc, pd = (points[cell[index]] for index in (a, b, c, d))
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        w = [pd[i] - pa[i] for i in range(3)]
        determinant = (
            u[0] * (v[1] * w[2] - v[2] * w[1])
            - u[1] * (v[0] * w[2] - v[2] * w[0])
            + u[2] * (v[0] * w[1] - v[1] * w[0])
        )
        return abs(determinant) / 6.0

    return sum(
        tetra(*indices)
        for indices in (
            (0, 1, 3, 4),
            (1, 2, 3, 6),
            (1, 3, 4, 6),
            (1, 4, 5, 6),
            (3, 4, 6, 7),
        )
    )


def _generation_evaluation(
    case: SolverCase,
    expected_case: dict[str, Any],
) -> dict[str, Any]:
    profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
    preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    points = preview.get("points")
    cells = preview.get("cells")
    if not isinstance(points, list) or not isinstance(cells, list) or not cells:
        raise AxisymmetricGeometryQualificationError("generated preview is missing volume cells")
    volumes = [_cell_volume(points, cell) for cell in cells]
    radii = [float(station["radiusM"]) for station in profile["stations"]]
    inlet_radius = float(expected_case["inletDiameterM"]) / 2.0
    outlet_radius = float(expected_case["outletDiameterM"]) / 2.0
    geometry_passed = (
        profile.get("schema") == adapters.AXISYMMETRIC_PROFILE_SCHEMA
        and profile.get("pathEdgeIds") == [expected_case["id"]]
        and math.isclose(radii[0], inlet_radius, rel_tol=1.0e-12)
        and math.isclose(radii[-1], outlet_radius, rel_tol=1.0e-12)
        and (
            expected_case["edgeType"] != "venturi"
            or math.isclose(
                min(radii),
                float(expected_case["throatDiameterM"]) / 2.0,
                rel_tol=1.0e-12,
            )
        )
    )
    return {
        "caseId": expected_case["id"],
        "edgeType": expected_case["edgeType"],
        "generated": True,
        "spatialDimension": preview.get("spatialDimension"),
        "cellCount": len(cells),
        "minimumCellVolumeM3": min(volumes),
        "allCellsPositiveVolume": all(volume > 0.0 for volume in volumes),
        "boundaryRoles": profile.get("boundaryRoles"),
        "geometryPassed": geometry_passed,
        "passed": (
            preview.get("spatialDimension") == 3
            and all(volume > 0.0 for volume in volumes)
            and profile.get("boundaryRoles") == EXPECTED_PATCHES
            and geometry_passed
        ),
    }


def materialize_preflight(
    output_dir: Path,
    contract: dict[str, Any],
    contract_sha256: str,
) -> tuple[dict[str, SolverCase], dict[str, Any]]:
    cases: dict[str, SolverCase] = {}
    generation: list[dict[str, Any]] = []
    determinism: list[dict[str, Any]] = []
    for case_spec in contract["generationOnlyCases"]:
        first = _build_case(_single_edge_project(case_spec))
        second = _build_case(_single_edge_project(case_spec))
        first_hashes = _file_hashes(first)
        second_hashes = _file_hashes(second)
        matched = first_hashes == second_hashes
        if not matched:
            raise AxisymmetricGeometryQualificationError(
                f"{case_spec['id']} generated-file hashes differ across independent builds"
            )
        generation.append(_generation_evaluation(first, case_spec))
        determinism.append(
            {
                "caseId": case_spec["id"],
                "hashesMatch": True,
                "fileCount": len(first_hashes),
                "fileSetDigestSha256": _sha256_text(
                    "\n".join(f"{path} {digest}" for path, digest in first_hashes.items())
                    + "\n"
                ),
            }
        )

    for level in contract["levels"]:
        first = _build_case(_runtime_project(contract, contract_sha256, level))
        second = _build_case(_runtime_project(contract, contract_sha256, level))
        first_hashes = _file_hashes(first)
        second_hashes = _file_hashes(second)
        if first_hashes != second_hashes:
            raise AxisymmetricGeometryQualificationError(
                f"{level['id']} generated-file hashes differ across independent builds"
            )
        identity_contract = json.loads(first.files[SOURCE_IDENTITY_CONTRACT_PATH])
        if (
            identity_contract.get("orderingAssumptionAllowed") is not False
            or identity_contract.get("sourceCellCount", 0) <= 0
        ):
            raise AxisymmetricGeometryQualificationError(
                f"{level['id']} source-cell identity contract is not fail closed"
            )
        cases[str(level["id"])] = first
        determinism.append(
            {
                "caseId": level["id"],
                "hashesMatch": True,
                "fileCount": len(first_hashes),
                "fileSetDigestSha256": _sha256_text(
                    "\n".join(f"{path} {digest}" for path, digest in first_hashes.items())
                    + "\n"
                ),
                "sourceIdentityContractSha256": _sha256_text(
                    first.files[SOURCE_IDENTITY_CONTRACT_PATH]
                ),
            }
        )
        for build_name, case in (("build-a", first), ("build-b", second)):
            materialize_case_files(
                case,
                output_dir
                / "preflight"
                / build_name
                / str(level["id"]),
            )

    report = {
        "schema": "flowlab.axisymmetric-geometry-experimental-qualification-preflight.v1",
        "contractSha256": contract_sha256,
        "generationOnly": generation,
        "determinism": determinism,
        "allGenerationCasesPassed": all(item["passed"] for item in generation),
        "allGeneratedFileHashesMatch": all(
            item["hashesMatch"] for item in determinism
        ),
        "solverExecuted": False,
        "promotionAuthorized": False,
    }
    _write_json(output_dir / "preflight-report.json", report)
    if not report["allGenerationCasesPassed"]:
        raise AxisymmetricGeometryQualificationError(
            "a generation-only geometry gate failed"
        )
    return cases, report


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
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
        raise AxisymmetricGeometryQualificationError(
            f"provenance command failed: {' '.join(command)}"
        ) from exc


def _source_control_identity() -> dict[str, Any]:
    commit = _run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    status = _run(
        ["git", "status", "--porcelain", "--", *FROZEN_PATHS],
        cwd=REPOSITORY_ROOT,
    )
    if commit.returncode != 0 or not commit.stdout.strip() or status.returncode != 0:
        raise AxisymmetricGeometryQualificationError(
            "could not resolve the frozen source identity"
        )
    if status.stdout.strip():
        raise AxisymmetricGeometryQualificationError(
            "refusing solver execution with uncommitted qualification or transitive source"
        )
    return {
        "commit": commit.stdout.strip(),
        "frozenPaths": FROZEN_PATHS,
        "frozenPathsClean": True,
    }


def _runtime_identity(expected_tag: str) -> dict[str, Any]:
    if adapters._openfoam_image() != expected_tag:
        raise AxisymmetricGeometryQualificationError(
            "configured OpenFOAM image tag differs from the frozen contract"
        )
    inspect = _run(["docker", "image", "inspect", expected_tag])
    if inspect.returncode != 0:
        detail = inspect.stderr.strip() or inspect.stdout.strip()
        raise AxisymmetricGeometryQualificationError(
            f"OpenFOAM image is unavailable: {detail}"
        )
    try:
        records = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise AxisymmetricGeometryQualificationError(
            "docker image inspection returned invalid JSON"
        ) from exc
    if not isinstance(records, list) or len(records) != 1:
        raise AxisymmetricGeometryQualificationError(
            "docker image inspection did not resolve exactly one image"
        )
    image_id = records[0].get("Id")
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
        raise AxisymmetricGeometryQualificationError(
            "docker image inspection did not return an immutable image ID"
        )
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "container": {
            "imageTag": expected_tag,
            "imageId": image_id,
            "architecture": records[0].get("Architecture"),
            "os": records[0].get("Os"),
        },
    }


def _latest_native_result(case_dir: Path) -> Path:
    candidates = sorted((case_dir / "postProcessing" / "flowlabNative").glob("*.vtk"))
    if not candidates:
        raise AxisymmetricGeometryQualificationError(
            f"FlowLab-native OpenFOAM result artifact is missing: {case_dir}"
        )
    return candidates[-1]


def _relative_span(values: list[float]) -> float:
    if not values:
        return math.inf
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / abs(mean) if mean != 0.0 else math.inf


def _patch_histories(solver_log: str, sample_count: int) -> dict[str, Any]:
    flows: dict[str, list[float]] = {"inlet": [], "outlet": []}
    pressures: dict[str, list[float]] = {"inlet": [], "outlet": []}
    for patch, value in re.findall(
        r"sum\((inlet|outlet)\) of phi =\s*([-+0-9.eE]+)",
        solver_log,
    ):
        flows[patch].append(float(value))
    for patch, value in re.findall(
        r"(?:areaAverage|average)\((inlet|outlet)\) of p =\s*([-+0-9.eE]+)",
        solver_log,
    ):
        pressures[patch].append(float(value))
    if any(len(values) < sample_count for values in [*flows.values(), *pressures.values()]):
        raise AxisymmetricGeometryQualificationError(
            "solver log does not contain the frozen 100-sample inlet/outlet pressure and flow tails"
        )
    inlet_flow = flows["inlet"][-sample_count:]
    outlet_flow = flows["outlet"][-sample_count:]
    inlet_pressure = pressures["inlet"][-sample_count:]
    outlet_pressure = pressures["outlet"][-sample_count:]
    measured_flow = [
        0.5 * (abs(left) + abs(right))
        for left, right in zip(inlet_flow, outlet_flow, strict=True)
    ]
    pressure_drop = [
        left - right
        for left, right in zip(inlet_pressure, outlet_pressure, strict=True)
    ]
    final_mass_imbalance = (
        abs(abs(inlet_flow[-1]) - abs(outlet_flow[-1]))
        / max(abs(inlet_flow[-1]), abs(outlet_flow[-1]))
    )
    return {
        "sampleCount": sample_count,
        "pressureDrop": pressure_drop,
        "measuredFlow": measured_flow,
        "pressureDropRelativeSpan": _relative_span(pressure_drop),
        "measuredFlowRelativeSpan": _relative_span(measured_flow),
        "finalRelativeMassFlowImbalance": final_mass_imbalance,
    }


def _boundary_types(text: str) -> dict[str, str]:
    clean = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    clean = "\n".join(line.split("//", 1)[0] for line in clean.splitlines())
    result: dict[str, str] = {}
    for name, body in re.findall(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*\{(.*?)\}", clean, re.DOTALL):
        type_match = re.search(r"\btype\s+([A-Za-z_][A-Za-z0-9_.-]*)\s*;", body)
        if type_match and re.search(r"\bnFaces\s+\d+\s*;", body):
            result[name] = type_match.group(1)
    return result


def _edge_means(
    dataset: dict[str, Any],
    component_map: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, list[int]]]:
    pressure = dataset.get("cellData", {}).get("scalars", {}).get("p")
    velocity = dataset.get("cellData", {}).get("vectors", {}).get("U")
    source_ids = dataset.get("sourceCellIndices")
    if (
        not isinstance(pressure, list)
        or not isinstance(velocity, list)
        or not isinstance(source_ids, list)
        or len(pressure) != len(velocity)
        or len(pressure) != len(source_ids)
    ):
        raise AxisymmetricGeometryQualificationError(
            "result artifact lacks explicit cell p/U/source identity"
        )
    binding = next(
        (
            item
            for item in component_map.get("artifactBindings", [])
            if item.get("scope") == "cell-ranges"
        ),
        None,
    )
    if not isinstance(binding, dict):
        raise AxisymmetricGeometryQualificationError(
            "result component map lacks cell ranges"
        )
    positions = {source_id: index for index, source_id in enumerate(source_ids)}
    means: dict[str, dict[str, float]] = {}
    indices_by_edge: dict[str, list[int]] = {}
    for cell_range in binding["cellRanges"]:
        edge_id = str(cell_range["edgeId"])
        source_range = range(
            int(cell_range["cellStart"]),
            int(cell_range["cellStart"]) + int(cell_range["cellCount"]),
        )
        indices = [positions[source_id] for source_id in source_range]
        if len(indices) != int(cell_range["cellCount"]):
            raise AxisymmetricGeometryQualificationError(
                f"explicit source identity is incomplete for edge {edge_id}"
            )
        indices_by_edge[edge_id] = indices
        means[edge_id] = {
            "pressure": sum(float(pressure[index]) for index in indices) / len(indices),
            "axialVelocity": sum(float(velocity[index][0]) for index in indices)
            / len(indices),
        }
    return means, indices_by_edge


def _trend_gates(
    dataset: dict[str, Any],
    component_map: dict[str, Any],
) -> dict[str, Any]:
    means, _indices = _edge_means(dataset, component_map)
    gates = {
        "contractionVelocityIncrease": means["contraction"]["axialVelocity"]
        > means["inlet-pipe"]["axialVelocity"],
        "contractionPressureDecrease": means["contraction"]["pressure"]
        < means["inlet-pipe"]["pressure"],
        "throatVelocityAboveInlet": means["throat"]["axialVelocity"]
        > means["inlet-pipe"]["axialVelocity"],
        "throatPressureBelowInlet": means["throat"]["pressure"]
        < means["inlet-pipe"]["pressure"],
        "recoveryVelocityBelowThroat": means["recovery"]["axialVelocity"]
        < means["throat"]["axialVelocity"],
        "recoveryPressureAboveThroat": means["recovery"]["pressure"]
        > means["throat"]["pressure"],
    }
    return {
        "edgeMeans": means,
        "gates": gates,
        "allExpectedTrendsPassed": all(gates.values()),
    }


def evaluate_level(
    case_dir: Path,
    case: SolverCase,
    level: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    check_mesh_path = case_dir / "log.checkMesh"
    solver_log_path = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    for path in (check_mesh_path, solver_log_path, boundary_path):
        if not path.is_file():
            raise AxisymmetricGeometryQualificationError(
                f"required retained runtime artifact is missing: {path}"
            )
    check_mesh = check_mesh_path.read_text(encoding="utf-8", errors="replace")
    solver_log = solver_log_path.read_text(encoding="utf-8", errors="replace")
    boundary_types = _boundary_types(
        boundary_path.read_text(encoding="utf-8", errors="replace")
    )
    cell_match = re.search(r"^\s*cells:\s+(\d+)\s*$", check_mesh, re.MULTILINE)
    minimum_volume_match = re.search(
        r"min volume\s*=\s*([-+0-9.eE]+)",
        check_mesh,
        re.IGNORECASE,
    )
    if cell_match is None or minimum_volume_match is None:
        raise AxisymmetricGeometryQualificationError(
            "checkMesh output is missing cell count or minimum volume"
        )
    mesh_gate = {
        "checkMeshPassed": "Mesh OK." in check_mesh,
        "solutionDirections3": "Mesh has 3 solution (non-empty) directions" in check_mesh,
        "geometricDirections3": "Mesh has 3 geometric (non-empty) directions" in check_mesh,
        "cellCount": int(cell_match.group(1)),
        "minimumCellVolumeM3": float(minimum_volume_match.group(1)),
        "boundaryTypes": boundary_types,
        "exactPatches": boundary_types == EXPECTED_PATCHES,
    }
    mesh_gate["passed"] = (
        mesh_gate["checkMeshPassed"]
        and mesh_gate["solutionDirections3"]
        and mesh_gate["geometricDirections3"]
        and mesh_gate["minimumCellVolumeM3"] > 0.0
        and mesh_gate["exactPatches"]
    )

    result_path = _latest_native_result(case_dir)
    dataset = parse_vtk_result(result_path.read_text(encoding="utf-8", errors="replace"))
    pressure = dataset.get("cellData", {}).get("scalars", {}).get("p")
    velocity = dataset.get("cellData", {}).get("vectors", {}).get("U")
    finite_fields = (
        isinstance(pressure, list)
        and isinstance(velocity, list)
        and len(pressure) == len(velocity) == mesh_gate["cellCount"]
        and all(math.isfinite(float(value)) for value in pressure)
        and all(
            isinstance(vector, list)
            and len(vector) == 3
            and all(math.isfinite(float(value)) for value in vector)
            for vector in velocity
        )
    )
    tail_count = int(contract["gates"]["solverPerLevel"]["tailSampleCount"])
    histories = _patch_histories(solver_log, tail_count)
    solver_gate = {
        "normalTermination": (
            re.search(r"(?:^|\n)End(?:\n|$)", solver_log) is not None
            and "FOAM FATAL" not in solver_log
            and re.search(r"\b(?:nan|inf)\b", solver_log, re.IGNORECASE) is None
        ),
        "finitePressureAndVelocity": finite_fields,
        "pressureDropRelativeSpan": histories["pressureDropRelativeSpan"],
        "pressureDropRelativeSpanLimit": 0.005,
        "measuredFlowRelativeSpan": histories["measuredFlowRelativeSpan"],
        "measuredFlowRelativeSpanLimit": 0.001,
        "relativeMassFlowImbalance": histories["finalRelativeMassFlowImbalance"],
        "relativeMassFlowImbalanceLimit": 0.001,
    }
    solver_gate["passed"] = (
        solver_gate["normalTermination"]
        and solver_gate["finitePressureAndVelocity"]
        and solver_gate["pressureDropRelativeSpan"]
        <= solver_gate["pressureDropRelativeSpanLimit"]
        and solver_gate["measuredFlowRelativeSpan"]
        <= solver_gate["measuredFlowRelativeSpanLimit"]
        and solver_gate["relativeMassFlowImbalance"]
        <= solver_gate["relativeMassFlowImbalanceLimit"]
    )

    identity_report = _read_json(case_dir / SOURCE_IDENTITY_REPORT_PATH)
    binding = case.resultComponentMap.model_dump(mode="json") if case.resultComponentMap else {}
    full_payload = read_case_artifact(
        case_dir,
        str(result_path.relative_to(case_dir)),
    )
    preview_payload = read_case_artifact_preview(
        case_dir,
        str(result_path.relative_to(case_dir)),
        point_limit=500,
        cell_limit=min(500, mesh_gate["cellCount"]),
    )
    identity_gate = {
        "reportSchema": identity_report.get("schema"),
        "reportVerified": identity_report.get("verified"),
        "orderingAssumptionUsed": identity_report.get("orderingAssumptionUsed"),
        "solverCellCount": identity_report.get("solverCellCount"),
        "sourceCellCount": identity_report.get("sourceCellCount"),
        "mappingSha256": identity_report.get("solverToSourceCellSha256"),
        "fullLoadExplicitIdentity": (
            full_payload.get("fieldSummary", {})
            .get("sourceCellIdentity", {})
            .get("verified")
            is True
        ),
        "previewLoadExplicitIdentity": (
            preview_payload.get("sourceCellIdentity", {}).get("verified") is True
        ),
        "componentMapVersion": binding.get("version"),
    }
    identity_gate["passed"] = (
        identity_gate["reportSchema"] == SOURCE_IDENTITY_REPORT_SCHEMA
        and identity_gate["reportVerified"] is True
        and identity_gate["orderingAssumptionUsed"] is False
        and identity_gate["solverCellCount"] == identity_gate["sourceCellCount"]
        == mesh_gate["cellCount"]
        and identity_gate["fullLoadExplicitIdentity"]
        and identity_gate["previewLoadExplicitIdentity"]
        and identity_gate["componentMapVersion"] == 2
    )

    trend = _trend_gates(dataset, binding)
    pressure_drop = abs(sum(histories["pressureDrop"]) / len(histories["pressureDrop"]))
    characteristic_size = max(
        max(
            float(spec["lengthM"]) / int(level["axialCellsByEdge"][spec["id"]])
            for spec in contract["physicalCase"]["edges"]
        ),
        max(
            float(spec["inletDiameterM"]) / 2.0
            for spec in contract["physicalCase"]["edges"]
        )
        / int(level["radialCells"]),
    )
    all_gates = mesh_gate["passed"] and solver_gate["passed"] and identity_gate["passed"]
    return {
        "schema": LEVEL_SCHEMA,
        "level": level["id"],
        "scientificStatus": "experimental-software-geometry-only",
        "validated": False,
        "promotionAuthorized": False,
        "mesh": mesh_gate,
        "solver": solver_gate,
        "identity": identity_gate,
        "trends": trend,
        "qoi": {"pressureDropKinematic": pressure_drop},
        "characteristicCellSizeM": characteristic_size,
        "resultArtifact": {
            "path": str(result_path.relative_to(case_dir)),
            "sha256": _sha256_file(result_path),
        },
        "allMandatoryPerLevelGatesPassed": all_gates,
    }


def _pipeline_proof(
    fine_evaluation: dict[str, Any],
    fine_case: SolverCase,
    fine_case_dir: Path,
) -> dict[str, Any]:
    result_path = fine_case_dir / fine_evaluation["resultArtifact"]["path"]
    dataset = parse_vtk_result(result_path.read_text(encoding="utf-8"))
    component_map = fine_case.resultComponentMap.model_dump(mode="json")
    binding = component_map["artifactBindings"][0]
    selected: list[dict[str, Any]] = []
    for cell_range in binding["cellRanges"]:
        source_cell_id = int(cell_range["cellStart"])
        owners = [
            candidate["edgeId"]
            for candidate in binding["cellRanges"]
            if int(candidate["cellStart"])
            <= source_cell_id
            < int(candidate["cellStart"]) + int(candidate["cellCount"])
        ]
        selected.append(
            {
                "sourceCellId": source_cell_id,
                "expectedEdgeId": cell_range["edgeId"],
                "owners": owners,
                "uniqueExpectedOwner": owners == [cell_range["edgeId"]],
            }
        )
    source_ids = dataset.get("sourceCellIndices")
    proof = {
        "schema": RESULT_PIPELINE_SCHEMA,
        "generatedCase": True,
        "completedJobArtifact": result_path.is_file(),
        "artifactSha256": _sha256_file(result_path),
        "sourceCellIdentityVerified": dataset.get("sourceCellIdentity", {}).get("verified")
        is True,
        "sourceCellCount": dataset.get("sourceCellCount"),
        "sourceIdsComplete": (
            isinstance(source_ids, list)
            and sorted(source_ids) == list(range(len(source_ids)))
        ),
        "fullLoadVerified": fine_evaluation["identity"]["fullLoadExplicitIdentity"],
        "previewLoadVerified": fine_evaluation["identity"][
            "previewLoadExplicitIdentity"
        ],
        "schematicSelections": selected,
        "uniqueExplicitEdgeOwnership": all(
            item["uniqueExpectedOwner"] for item in selected
        ),
        "connectorPolicy": "unowned-probe-only",
        "orderingAssumptionUsed": False,
    }
    proof["passed"] = all(
        [
            proof["generatedCase"],
            proof["completedJobArtifact"],
            proof["sourceCellIdentityVerified"],
            proof["sourceIdsComplete"],
            proof["fullLoadVerified"],
            proof["previewLoadVerified"],
            proof["uniqueExplicitEdgeOwnership"],
            not proof["orderingAssumptionUsed"],
        ]
    )
    return proof


def execute_campaign(
    output_dir: Path,
    *,
    poll_interval_seconds: float = 0.25,
    timeout_seconds_per_level: float = 7200.0,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise AxisymmetricGeometryQualificationError(
            f"refusing to overwrite non-empty campaign directory: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    contract, contract_sha256 = load_frozen_contract()
    source_control = _source_control_identity()
    runtime = _runtime_identity(contract["runtime"]["imageTag"])
    cases, preflight = materialize_preflight(
        output_dir,
        contract,
        contract_sha256,
    )
    manager = JobManager(runtime_root=output_dir / "runtime")
    state: dict[str, Any] = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "running",
        "scientificStatus": "experimental-software-geometry-only",
        "validated": False,
        "promotionAuthorized": False,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "contractPath": str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
        "contractSha256": contract_sha256,
        "sourceControl": source_control,
        "runtimeEnvironment": runtime,
        "preflightSha256": _sha256_file(output_dir / "preflight-report.json"),
        "levels": [],
    }
    _write_json(output_dir / "campaign-state.json", state)

    evaluations: list[dict[str, Any]] = []
    case_dirs: dict[str, Path] = {}
    for level in contract["levels"]:
        level_id = str(level["id"])
        case = cases[level_id]
        queued = manager.queue_job(case)
        record: dict[str, Any] = {
            "level": level_id,
            "caseId": case.id,
            "jobId": queued.id,
            "status": queued.status,
            "execution": queued.execution,
            "command": queued.command,
        }
        state["levels"].append(record)
        _write_json(output_dir / "campaign-state.json", state)
        deadline = time.monotonic() + timeout_seconds_per_level
        terminal = queued
        while terminal.status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                manager.cancel_job(terminal.id)
                record["status"] = "cancelled-timeout"
                state["status"] = "infrastructure-failed-retained"
                _write_json(output_dir / "campaign-state.json", state)
                raise AxisymmetricGeometryQualificationError(
                    f"{level_id} exceeded the frozen execution timeout"
                )
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                raise AxisymmetricGeometryQualificationError(
                    f"JobManager lost {level_id} job state"
                )
            terminal = refreshed
        record.update(
            {
                "status": terminal.status,
                "exitCode": terminal.exitCode,
                "error": terminal.error,
                "finishedAt": terminal.finishedAt,
            }
        )
        _write_json(output_dir / "campaign-state.json", state)
        if terminal.status != "complete" or terminal.exitCode != 0 or not terminal.caseDir:
            state["status"] = "infrastructure-or-solver-failed-retained"
            _write_json(output_dir / "campaign-state.json", state)
            raise AxisymmetricGeometryQualificationError(
                f"{level_id} did not complete: status={terminal.status}, "
                f"exitCode={terminal.exitCode}, error={terminal.error}"
            )
        case_dir = Path(terminal.caseDir).resolve()
        if not case_dir.is_relative_to(output_dir):
            raise AxisymmetricGeometryQualificationError(
                f"{level_id} runtime evidence escaped the campaign directory"
            )
        case_dirs[level_id] = case_dir
        evaluation = evaluate_level(case_dir, case, level, contract)
        evaluation.update({"caseId": case.id, "jobId": terminal.id})
        evaluation_path = output_dir / "evaluations" / f"{level_id}.json"
        _write_json(evaluation_path, evaluation)
        record["caseDirectory"] = str(case_dir.relative_to(output_dir))
        record["evaluationPath"] = str(evaluation_path.relative_to(output_dir))
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allMandatoryPerLevelGatesPassed"] = evaluation[
            "allMandatoryPerLevelGatesPassed"
        ]
        evaluations.append(evaluation)
        _write_json(output_dir / "campaign-state.json", state)
        if not evaluation["allMandatoryPerLevelGatesPassed"]:
            state["status"] = "scientific-gate-failed-retained"
            _write_json(output_dir / "campaign-state.json", state)
            raise AxisymmetricGeometryQualificationError(
                f"{level_id} failed a frozen mesh, solver, or identity gate"
            )

    samples = [
        {
            "id": evaluation["level"],
            "source": "solver-produced",
            "sourceArtifactSha256": evaluation["resultArtifact"]["sha256"],
            "characteristicCellSizeM": evaluation["characteristicCellSizeM"],
            "value": evaluation["qoi"]["pressureDropKinematic"],
        }
        for evaluation in evaluations
    ]
    try:
        grid = richardson_grid_convergence(samples)
    except VerificationInputError as exc:
        grid = {"qualified": False, "reason": str(exc)}
    grid_gates = {
        "mathematicallyValid": "observedOrder" in grid,
        "observedOrder": grid.get("observedOrder"),
        "observedOrderWithinRange": (
            isinstance(grid.get("observedOrder"), (int, float))
            and 0.5 <= float(grid["observedOrder"]) <= 4.0
        ),
        "fineGridGciPercent": grid.get("fineGridGciPercent"),
        "fineGridGciWithinLimit": (
            isinstance(grid.get("fineGridGciPercent"), (int, float))
            and float(grid["fineGridGciPercent"]) <= 5.0
        ),
    }
    grid_gates["passed"] = all(
        [
            grid_gates["mathematicallyValid"],
            grid_gates["observedOrderWithinRange"],
            grid_gates["fineGridGciWithinLimit"],
        ]
    )
    fine = next(item for item in evaluations if item["level"] == "fine")
    pipeline = _pipeline_proof(fine, cases["fine"], case_dirs["fine"])
    _write_json(output_dir / "result-pipeline-proof.json", pipeline)
    all_passed = (
        preflight["allGenerationCasesPassed"]
        and preflight["allGeneratedFileHashesMatch"]
        and all(item["allMandatoryPerLevelGatesPassed"] for item in evaluations)
        and fine["trends"]["allExpectedTrendsPassed"]
        and grid_gates["passed"]
        and pipeline["passed"]
    )
    result = {
        **state,
        "status": (
            "experimental-geometry-qualified"
            if all_passed
            else "experimental-geometry-qualification-failed"
        ),
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "gridConvergence": grid,
        "gridGates": grid_gates,
        "fineGeometryTrends": fine["trends"],
        "resultPipelineProofSha256": _sha256_file(
            output_dir / "result-pipeline-proof.json"
        ),
        "allExperimentalQualificationGatesPassed": all_passed,
        "validated": False,
        "promotionAuthorized": False,
        "releaseAuthorized": False,
        "fixturePointerMutationAuthorized": False,
        "retainedEvidenceMutationAuthorized": False,
    }
    _write_json(output_dir / "campaign-result.json", result)
    state["status"] = result["status"]
    state["finishedAt"] = result["finishedAt"]
    _write_json(output_dir / "campaign-state.json", state)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--materialize-only",
        action="store_true",
        help="Run deterministic generation and contract preflight without a solver.",
    )
    actions.add_argument(
        "--run",
        action="store_true",
        help="Run the full frozen three-level experimental qualification.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.materialize_only:
        output = args.output_dir.resolve()
        if output.exists() and any(output.iterdir()):
            raise AxisymmetricGeometryQualificationError(
                f"refusing to overwrite non-empty output directory: {output}"
            )
        output.mkdir(parents=True, exist_ok=True)
        contract, digest = load_frozen_contract()
        _cases, report = materialize_preflight(output, contract, digest)
        result = report
    else:
        result = execute_campaign(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("allExperimentalQualificationGatesPassed", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
