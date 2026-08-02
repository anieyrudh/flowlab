"""Run the frozen full-O-grid experimental geometry qualification v3.

This campaign is independent of the blocked wedge v1/v2 path and of both
straight-pipe verification campaigns. A pass qualifies only bounded
experimental generated-geometry software.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import platform
from pathlib import Path
import re
import sys
import time
from typing import Any, Sequence

from . import adapters
from .axisymmetric_geometry_qualification import (
    AxisymmetricGeometryQualificationError as FullOGridGeometryQualificationError,
    _boundary_types,
    _cell_volume,
    _check_mesh_cell_count_and_minimum_volume,
    _edge_means,
    _file_hashes,
    _latest_native_result,
    _patch_histories,
    _read_json,
    _run,
    _sha256_file,
    _sha256_text,
    _write_json,
)
from .execution import (
    TERMINAL_STATUSES,
    JobManager,
    materialize_case_files,
    read_case_artifact,
    read_case_artifact_preview,
    validate_solver_case,
)
from .result_identity import (
    SOURCE_IDENTITY_ALGORITHM_FULL_OGRID_PATH,
    SOURCE_IDENTITY_ALGORITHM_FULL_OGRID_PATH_V4,
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
    / "full-ogrid-geometry-experimental-qualification"
    / "EXPERIMENTAL_QUALIFICATION_CONTRACT_V5.json"
)
BASE_CONTRACT_PATH = CONTRACT_PATH.with_name(
    "EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json"
)
# V4 is retained unchanged. It is listed as frozen so its digest stays provable,
# but it is no longer the active revision.
PRIOR_CONTRACT_PATH = CONTRACT_PATH.with_name(
    "EXPERIMENTAL_QUALIFICATION_CONTRACT_V4.json"
)
RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK_V5.md")
BASE_RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK_V3.md")
PRIOR_RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK_V4.md")
CAMPAIGN_SCHEMA = (
    "flowlab.full-ogrid-geometry-experimental-qualification-campaign.v5"
)
LEVEL_SCHEMA = (
    "flowlab.full-ogrid-geometry-experimental-qualification-level.v5"
)
RESULT_PIPELINE_SCHEMA = (
    "flowlab.full-ogrid-multi-edge-result-pipeline-proof.v5"
)
EXPECTED_PATCHES = {"inlet": "patch", "outlet": "patch", "walls": "wall"}
FROZEN_PATHS = [
    str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
    str(BASE_CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
    str(PRIOR_CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
    str(RUNBOOK_PATH.relative_to(REPOSITORY_ROOT)),
    str(BASE_RUNBOOK_PATH.relative_to(REPOSITORY_ROOT)),
    str(PRIOR_RUNBOOK_PATH.relative_to(REPOSITORY_ROOT)),
    "server/flowlab/adapters.py",
    "server/flowlab/execution.py",
    "server/flowlab/full_ogrid.py",
    "server/flowlab/full_ogrid_geometry_qualification.py",
    "server/flowlab/result_identity.py",
    "server/flowlab/results.py",
    "server/flowlab/schemas.py",
    "server/flowlab/verification.py",
    "server/tests/test_adapters.py",
    "server/tests/test_full_ogrid.py",
    "server/tests/test_full_ogrid_geometry_qualification.py",
    "server/tests/test_result_identity.py",
    "src/App.tsx",
    "src/App.resultLink.test.ts",
    "src/results/vtk.ts",
    "src/results/vtk.test.ts",
    "src/types.ts",
    "tests/e2e/editor.spec.ts",
]


def load_frozen_contract() -> tuple[dict[str, Any], str]:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    try:
        revision = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FullOGridGeometryQualificationError(
            "full-O-grid experimental qualification contract is invalid JSON"
        ) from exc
    if (
        not isinstance(revision, dict)
        or revision.get("schema")
        != (
            "flowlab.full-ogrid-geometry-experimental-qualification-"
            "contract-revision.v1"
        )
        or revision.get("revisionId")
        != "full-ogrid-generated-geometry-experimental-qualification-v5"
        or revision.get("status")
        != "prospective-frozen-before-v5-retained-scientific-execution"
    ):
        raise FullOGridGeometryQualificationError(
            "full-O-grid experimental qualification revision is unsupported "
            "or not prospectively frozen"
        )
    base_reference = revision.get("baseContract")
    if (
        not isinstance(base_reference, dict)
        or base_reference.get("path") != BASE_CONTRACT_PATH.name
        or base_reference.get("sha256") != _sha256_file(BASE_CONTRACT_PATH)
    ):
        raise FullOGridGeometryQualificationError(
            "full-O-grid qualification base contract digest does not match v5"
        )
    contract = json.loads(BASE_CONTRACT_PATH.read_text(encoding="utf-8"))

    def merge_patch(
        target: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(target)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = merge_patch(merged[key], value)
            else:
                merged[key] = value
        return merged

    patch = revision.get("mergePatch")
    if not isinstance(patch, dict):
        raise FullOGridGeometryQualificationError(
            "full-O-grid qualification revision lacks its merge patch"
        )
    contract = merge_patch(contract, patch)
    if (
        contract.get("schema")
        != "flowlab.full-ogrid-geometry-experimental-qualification-contract.v5"
        or contract.get("contractId")
        != adapters.FULL_OGRID_QUALIFICATION_CONTRACT_ID
        or contract.get("status")
        != "prospective-frozen-before-v5-retained-scientific-execution"
        or contract.get("identity", {}).get("algorithm")
        != SOURCE_IDENTITY_ALGORITHM_FULL_OGRID_PATH_V4
        or contract.get("promotionAuthorized") is not False
    ):
        raise FullOGridGeometryQualificationError(
            "full-O-grid experimental qualification contract is unsupported "
            "or not prospectively frozen"
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
            "meshMode": "full-ogrid",
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


def _path_project(
    *,
    name: str,
    edge_specs: list[dict[str, Any]],
    contract_sha256: str,
    case_id: str,
    axial_cells_by_edge: dict[str, int],
    annular_radial_cells: int,
    circumferential_cells: int,
    core_cells_per_side: int,
    volumetric_flow_rate: float,
) -> dict[str, Any]:
    project = _base_project(name)
    nodes: dict[str, Any] = {}
    edges: dict[str, Any] = {}
    for index in range(len(edge_specs) + 1):
        node_id = f"node-{index}"
        nodes[node_id] = {
            "id": node_id,
            "type": (
                "source"
                if index == 0
                else "sink"
                if index == len(edge_specs)
                else "junction"
            ),
            "position": {"x": float(index * 300), "y": 0.0},
            "rotation": 0.0,
            **({"pressure": 120000.0} if index == 0 else {}),
            **(
                {
                    "pressure": 101325.0,
                    "flowDemand": volumetric_flow_rate,
                }
                if index == len(edge_specs)
                else {}
            ),
        }
    for index, spec in enumerate(edge_specs):
        edge_id = str(spec["id"])
        edge: dict[str, Any] = {
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
        if edge["type"] == "venturi":
            edge.update(
                {
                    "throatDiameter": float(spec["throatDiameterM"]),
                    "throatPosition": 0.5,
                    "throatLength": 0.012,
                }
            )
        edges[edge_id] = edge
    project["nodes"] = nodes
    project["edges"] = edges
    project["solver"].update(
        {
            "meshControls": {
                "fullOGridAxialCellsByEdge": axial_cells_by_edge,
                "fullOGridAnnularRadialCells": annular_radial_cells,
                "fullOGridCircumferentialCells": circumferential_cells,
                "fullOGridCoreCellsPerSide": core_cells_per_side,
            },
            "fullOGridQualification": {
                "contractId": adapters.FULL_OGRID_QUALIFICATION_CONTRACT_ID,
                "contractSha256": contract_sha256,
                "caseId": case_id,
                "qoiHistoryWriteIntervalIterations": 1,
                "volumetricFlowRateM3PerS": volumetric_flow_rate,
            },
        }
    )
    return project


def _single_edge_project(
    case_spec: dict[str, Any],
    contract_sha256: str,
) -> dict[str, Any]:
    edge_id = str(case_spec["id"])
    return _path_project(
        name=f"Full-O-grid generation-only {edge_id}",
        edge_specs=[
            {
                "id": edge_id,
                "type": case_spec["edgeType"],
                "lengthM": case_spec["lengthM"],
                "inletDiameterM": case_spec["inletDiameterM"],
                "outletDiameterM": case_spec["outletDiameterM"],
                **(
                    {"throatDiameterM": case_spec["throatDiameterM"]}
                    if case_spec["edgeType"] == "venturi"
                    else {}
                ),
            }
        ],
        contract_sha256=contract_sha256,
        case_id=edge_id,
        axial_cells_by_edge={edge_id: 12},
        annular_radial_cells=2,
        circumferential_cells=16,
        core_cells_per_side=4,
        volumetric_flow_rate=5.0e-6,
    )


def _runtime_project(
    contract: dict[str, Any],
    contract_sha256: str,
    level: dict[str, Any],
) -> dict[str, Any]:
    physical = contract["physicalCase"]
    project = _path_project(
        name=f"Full-O-grid geometry qualification ({level['id']})",
        edge_specs=physical["edges"],
        contract_sha256=contract_sha256,
        case_id=str(physical["id"]),
        axial_cells_by_edge={
            str(key): int(value)
            for key, value in level["axialCellsByEdge"].items()
        },
        annular_radial_cells=int(level["annularRadialCells"]),
        circumferential_cells=int(level["circumferentialCells"]),
        core_cells_per_side=int(level["coreCellsPerSide"]),
        volumetric_flow_rate=float(physical["volumetricFlowRateM3PerS"]),
    )
    project["solver"]["meshResolution"] = str(level["id"])
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
        raise FullOGridGeometryQualificationError(
            "generated case failed validation: " + "; ".join(issues)
        )
    return case


def _generation_evaluation(
    case: SolverCase,
    expected_case: dict[str, Any],
) -> dict[str, Any]:
    profile = json.loads(
        case.files["constant/flowlab_full_ogrid_profile.json"]
    )
    preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    points = preview.get("points")
    cells = preview.get("cells")
    if not isinstance(points, list) or not isinstance(cells, list) or not cells:
        raise FullOGridGeometryQualificationError(
            "generated preview is missing volume cells"
        )
    volumes = [_cell_volume(points, cell) for cell in cells]
    radii = [float(station["radiusM"]) for station in profile["stations"]]
    expected_minimum_radius = (
        float(expected_case["throatDiameterM"]) / 2.0
        if expected_case["edgeType"] == "venturi"
        else min(
            float(expected_case["inletDiameterM"]),
            float(expected_case["outletDiameterM"]),
        )
        / 2.0
    )
    geometry_passed = (
        profile.get("schema") == adapters.FULL_OGRID_PATH_PROFILE_SCHEMA
        and profile.get("pathEdgeIds") == [expected_case["id"]]
        and math.isclose(
            float(profile["totalLengthM"]),
            float(expected_case["lengthM"]),
            rel_tol=1.0e-12,
        )
        and math.isclose(min(radii), expected_minimum_radius, rel_tol=1.0e-12)
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
        project = _single_edge_project(case_spec, contract_sha256)
        first = _build_case(project)
        second = _build_case(project)
        first_hashes = _file_hashes(first)
        if first_hashes != _file_hashes(second):
            raise FullOGridGeometryQualificationError(
                f"{case_spec['id']} generated-file hashes differ across builds"
            )
        generation.append(_generation_evaluation(first, case_spec))
        determinism.append(
            {
                "caseId": case_spec["id"],
                "hashesMatch": True,
                "fileCount": len(first_hashes),
                "fileSetDigestSha256": _sha256_text(
                    "\n".join(
                        f"{path} {digest}"
                        for path, digest in first_hashes.items()
                    )
                    + "\n"
                ),
            }
        )
    for level in contract["levels"]:
        first = _build_case(_runtime_project(contract, contract_sha256, level))
        second = _build_case(_runtime_project(contract, contract_sha256, level))
        first_hashes = _file_hashes(first)
        if first_hashes != _file_hashes(second):
            raise FullOGridGeometryQualificationError(
                f"{level['id']} generated-file hashes differ across builds"
            )
        profile = json.loads(
            first.files["constant/flowlab_full_ogrid_profile.json"]
        )
        identity = json.loads(first.files[SOURCE_IDENTITY_CONTRACT_PATH])
        expected_count = int(level["expectedCellCount"])
        if (
            profile.get("topology", {}).get("resolution", {}).get("cellCount")
            != expected_count
            or identity.get("sourceCellCount") != expected_count
            or identity.get("algorithm") != contract["identity"]["algorithm"]
            or identity.get("orderingAssumptionAllowed") is not False
            or identity.get("unownedRanges") != []
        ):
            raise FullOGridGeometryQualificationError(
                f"{level['id']} topology or source identity does not match the contract"
            )
        cases[str(level["id"])] = first
        determinism.append(
            {
                "caseId": level["id"],
                "hashesMatch": True,
                "fileCount": len(first_hashes),
                "fileSetDigestSha256": _sha256_text(
                    "\n".join(
                        f"{path} {digest}"
                        for path, digest in first_hashes.items()
                    )
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
                output_dir / "preflight" / build_name / str(level["id"]),
            )
    report = {
        "schema": (
            "flowlab.full-ogrid-geometry-experimental-qualification-preflight.v4"
        ),
        "contractSha256": contract_sha256,
        "generationOnly": generation,
        "determinism": determinism,
        "allGenerationCasesPassed": all(item["passed"] for item in generation),
        "allGeneratedFileHashesMatch": all(
            item["hashesMatch"] for item in determinism
        ),
        "solverExecuted": False,
        "validated": False,
        "promotionAuthorized": False,
    }
    _write_json(output_dir / "preflight-report.json", report)
    if not report["allGenerationCasesPassed"]:
        raise FullOGridGeometryQualificationError(
            "a generation-only geometry gate failed"
        )
    return cases, report


def _source_control_identity() -> dict[str, Any]:
    commit = _run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    status = _run(
        ["git", "status", "--porcelain", "--", *FROZEN_PATHS],
        cwd=REPOSITORY_ROOT,
    )
    if commit.returncode != 0 or not commit.stdout.strip() or status.returncode != 0:
        raise FullOGridGeometryQualificationError(
            "could not resolve the frozen source identity"
        )
    if status.stdout.strip():
        raise FullOGridGeometryQualificationError(
            "refusing solver execution with uncommitted qualification or "
            "transitive source"
        )
    return {
        "commit": commit.stdout.strip(),
        "frozenPaths": FROZEN_PATHS,
        "frozenPathsClean": True,
    }


def _runtime_identity(expected_tag: str) -> dict[str, Any]:
    if adapters._openfoam_image() != expected_tag:
        raise FullOGridGeometryQualificationError(
            "configured OpenFOAM image tag differs from the frozen contract"
        )
    inspect = _run(["docker", "image", "inspect", expected_tag])
    if inspect.returncode != 0:
        detail = inspect.stderr.strip() or inspect.stdout.strip()
        raise FullOGridGeometryQualificationError(
            f"OpenFOAM image is unavailable: {detail}"
        )
    try:
        records = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise FullOGridGeometryQualificationError(
            "docker image inspection returned invalid JSON"
        ) from exc
    if not isinstance(records, list) or len(records) != 1:
        raise FullOGridGeometryQualificationError(
            "docker image inspection did not resolve exactly one image"
        )
    image_id = records[0].get("Id")
    if (
        not isinstance(image_id, str)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
    ):
        raise FullOGridGeometryQualificationError(
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


def _trend_gates(
    dataset: dict[str, Any],
    component_map: dict[str, Any],
) -> dict[str, Any]:
    means, _indices = _edge_means(dataset, component_map)
    gates = {
        "contractionVelocityAboveInlet": (
            means["contraction"]["axialVelocity"]
            > means["inlet-pipe"]["axialVelocity"]
        ),
        "contractionPressureBelowInlet": (
            means["contraction"]["pressure"]
            < means["inlet-pipe"]["pressure"]
        ),
        "throatVelocityAboveInlet": (
            means["throat"]["axialVelocity"]
            > means["inlet-pipe"]["axialVelocity"]
        ),
        "throatPressureBelowInlet": (
            means["throat"]["pressure"]
            < means["inlet-pipe"]["pressure"]
        ),
        "recoveryVelocityBelowThroat": (
            means["recovery"]["axialVelocity"]
            < means["throat"]["axialVelocity"]
        ),
        "recoveryPressureAboveThroat": (
            means["recovery"]["pressure"] > means["throat"]["pressure"]
        ),
    }
    return {
        "edgeMeans": means,
        "gates": gates,
        "allExpectedTrendsPassed": all(gates.values()),
    }


def _check_mesh_directions(check_mesh: str) -> tuple[int | None, int | None]:
    """Parse the geometric and solution direction counts from a checkMesh log.

    OpenFOAM 11 always writes the geometric line with the ``(non-empty/wedge)``
    classification, for example::

        Mesh has 3 geometric (non-empty/wedge) directions (1 1 1)
        Mesh has 3 solution (non-empty) directions (1 1 1)

    Only the solution line uses the bare ``(non-empty)`` form. A literal substring
    test for ``"Mesh has 3 geometric (non-empty) directions"`` therefore matches no
    mesh of any topology, which left the geometric-direction gate permanently
    false and unevaluatable rather than merely failing.

    Returning the observed integers lets the gate compare counts and lets retained
    evidence record what OpenFOAM actually reported. ``None`` means the line was
    absent, which the caller must treat as a failure rather than a pass.
    """

    geometric = re.search(
        r"Mesh has\s+(\d+)\s+geometric\s+\(non-empty(?:/wedge)?\)\s+directions",
        check_mesh,
    )
    solution = re.search(
        r"Mesh has\s+(\d+)\s+solution\s+\(non-empty\)\s+directions",
        check_mesh,
    )
    return (
        int(geometric.group(1)) if geometric else None,
        int(solution.group(1)) if solution else None,
    )


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
            raise FullOGridGeometryQualificationError(
                f"required retained runtime artifact is missing: {path}"
            )
    check_mesh = check_mesh_path.read_text(
        encoding="utf-8", errors="replace"
    )
    solver_log = solver_log_path.read_text(
        encoding="utf-8", errors="replace"
    )
    boundary_types = _boundary_types(
        boundary_path.read_text(encoding="utf-8", errors="replace")
    )
    cell_count, minimum_cell_volume = _check_mesh_cell_count_and_minimum_volume(
        check_mesh
    )
    expected_count = int(level["expectedCellCount"])
    geometric_directions, solution_directions = _check_mesh_directions(check_mesh)
    mesh_gate = {
        "checkMeshPassed": "Mesh OK." in check_mesh,
        "geometricDirections": geometric_directions,
        "solutionDirections": solution_directions,
        "solutionDirections3": solution_directions == 3,
        "geometricDirections3": geometric_directions == 3,
        # OpenFOAM writes `Number of regions: 1 (OK).`; the previous anchored
        # pattern `^\s*regions:\s+1\s*$` matched no log line of any mesh.
        "oneConnectedRegion": re.search(
            r"Number of regions:\s*1\b", check_mesh
        )
        is not None,
        "allCellsHex": re.search(
            rf"^\s*hexahedra:\s+{expected_count}\s*$",
            check_mesh,
            re.MULTILINE,
        )
        is not None,
        "cellCount": cell_count,
        "expectedCellCount": expected_count,
        "minimumCellVolumeM3": minimum_cell_volume,
        "boundaryTypes": boundary_types,
        "exactPatches": boundary_types == EXPECTED_PATCHES,
    }
    mesh_gate["passed"] = (
        mesh_gate["checkMeshPassed"]
        and mesh_gate["solutionDirections3"]
        and mesh_gate["geometricDirections3"]
        and mesh_gate["oneConnectedRegion"]
        and mesh_gate["allCellsHex"]
        and mesh_gate["cellCount"] == expected_count
        and mesh_gate["minimumCellVolumeM3"] > 0.0
        and mesh_gate["exactPatches"]
    )

    result_path = _latest_native_result(case_dir)
    dataset = parse_vtk_result(
        result_path.read_text(encoding="utf-8", errors="replace")
    )
    pressure = dataset.get("cellData", {}).get("scalars", {}).get("p")
    velocity = dataset.get("cellData", {}).get("vectors", {}).get("U")
    finite_fields = (
        isinstance(pressure, list)
        and isinstance(velocity, list)
        and len(pressure) == len(velocity) == cell_count
        and all(math.isfinite(float(value)) for value in pressure)
        and all(
            isinstance(vector, list)
            and len(vector) == 3
            and all(math.isfinite(float(value)) for value in vector)
            for vector in velocity
        )
    )
    histories = _patch_histories(
        solver_log,
        int(contract["gates"]["solverPerLevel"]["tailSampleCount"]),
    )
    solver_gate = {
        "normalTermination": (
            re.search(r"(?:^|\n)End(?:\n|$)", solver_log) is not None
            and "FOAM FATAL" not in solver_log
            and re.search(r"\b(?:nan|inf)\b", solver_log, re.IGNORECASE)
            is None
        ),
        "finitePressureAndVelocity": finite_fields,
        "pressureDropRelativeSpan": histories["pressureDropRelativeSpan"],
        "pressureDropRelativeSpanLimit": 0.005,
        "measuredFlowRelativeSpan": histories["measuredFlowRelativeSpan"],
        "measuredFlowRelativeSpanLimit": 0.001,
        "relativeMassFlowImbalance": (
            histories["finalRelativeMassFlowImbalance"]
        ),
        "relativeMassFlowImbalanceLimit": 0.001,
    }
    solver_gate["passed"] = (
        solver_gate["normalTermination"]
        and solver_gate["finitePressureAndVelocity"]
        and solver_gate["pressureDropRelativeSpan"] <= 0.005
        and solver_gate["measuredFlowRelativeSpan"] <= 0.001
        and solver_gate["relativeMassFlowImbalance"] <= 0.001
    )

    identity_report = _read_json(case_dir / SOURCE_IDENTITY_REPORT_PATH)
    component_map = (
        case.resultComponentMap.model_dump(mode="json")
        if case.resultComponentMap
        else {}
    )
    full_payload = read_case_artifact(
        case_dir, str(result_path.relative_to(case_dir))
    )
    preview_payload = read_case_artifact_preview(
        case_dir,
        str(result_path.relative_to(case_dir)),
        point_limit=500,
        cell_limit=min(500, cell_count),
    )
    binding = next(
        (
            item
            for item in component_map.get("artifactBindings", [])
            if item.get("scope") == "cell-ranges"
        ),
        {},
    )
    ranges = binding.get("cellRanges", [])
    covered = sorted(
        source_id
        for cell_range in ranges
        for source_id in range(
            int(cell_range["cellStart"]),
            int(cell_range["cellStart"]) + int(cell_range["cellCount"]),
        )
    )
    identity_gate = {
        "reportSchema": identity_report.get("schema"),
        "reportVerified": identity_report.get("verified"),
        "orderingAssumptionUsed": identity_report.get(
            "orderingAssumptionUsed"
        ),
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
            preview_payload.get("sourceCellIdentity", {}).get("verified")
            is True
        ),
        "componentMapVersion": component_map.get("version"),
        "uniqueCompleteEdgeOwnership": (
            covered == list(range(cell_count))
            and len(covered) == len(set(covered))
        ),
        "connectorCellCount": 0,
    }
    identity_gate["passed"] = (
        identity_gate["reportSchema"] == SOURCE_IDENTITY_REPORT_SCHEMA
        and identity_gate["reportVerified"] is True
        and identity_gate["orderingAssumptionUsed"] is False
        and identity_gate["solverCellCount"]
        == identity_gate["sourceCellCount"]
        == cell_count
        and identity_gate["fullLoadExplicitIdentity"]
        and identity_gate["previewLoadExplicitIdentity"]
        and identity_gate["componentMapVersion"] == 2
        and identity_gate["uniqueCompleteEdgeOwnership"]
    )
    pressure_drop = abs(
        sum(histories["pressureDrop"]) / len(histories["pressureDrop"])
    )
    characteristic_size = (
        math.pi
        * sum(
            float(edge["lengthM"])
            * (
                (
                    float(edge["inletDiameterM"])
                    + float(edge["outletDiameterM"])
                )
                / 4.0
            )
            ** 2
            for edge in contract["physicalCase"]["edges"]
        )
        / cell_count
    ) ** (1.0 / 3.0)
    all_gates = (
        mesh_gate["passed"]
        and solver_gate["passed"]
        and identity_gate["passed"]
    )
    return {
        "schema": LEVEL_SCHEMA,
        "level": level["id"],
        "scientificStatus": "experimental-software-geometry-only",
        "validated": False,
        "promotionAuthorized": False,
        "mesh": mesh_gate,
        "solver": solver_gate,
        "identity": identity_gate,
        "trends": _trend_gates(dataset, component_map),
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
    selections: list[dict[str, Any]] = []
    for cell_range in binding["cellRanges"]:
        source_cell_id = int(cell_range["cellStart"])
        owners = [
            candidate["edgeId"]
            for candidate in binding["cellRanges"]
            if int(candidate["cellStart"])
            <= source_cell_id
            < int(candidate["cellStart"]) + int(candidate["cellCount"])
        ]
        selections.append(
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
        "sourceCellIdentityVerified": (
            dataset.get("sourceCellIdentity", {}).get("verified") is True
        ),
        "sourceCellCount": dataset.get("sourceCellCount"),
        "sourceIdsComplete": (
            isinstance(source_ids, list)
            and sorted(source_ids) == list(range(len(source_ids)))
        ),
        "fullLoadVerified": fine_evaluation["identity"][
            "fullLoadExplicitIdentity"
        ],
        "previewLoadVerified": fine_evaluation["identity"][
            "previewLoadExplicitIdentity"
        ],
        "schematicSelections": selections,
        "uniqueExplicitEdgeOwnership": all(
            item["uniqueExpectedOwner"] for item in selections
        ),
        "connectorCellCount": 0,
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
            proof["connectorCellCount"] == 0,
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
        raise FullOGridGeometryQualificationError(
            f"refusing to overwrite non-empty campaign directory: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    contract, contract_sha256 = load_frozen_contract()
    source_control = _source_control_identity()
    runtime = _runtime_identity(contract["runtime"]["imageTag"])
    cases, preflight = materialize_preflight(
        output_dir, contract, contract_sha256
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
        "preflightSha256": _sha256_file(
            output_dir / "preflight-report.json"
        ),
        "levels": [],
    }
    _write_json(output_dir / "campaign-state.json", state)
    evaluations: list[dict[str, Any]] = []
    case_dirs: dict[str, Path] = {}
    for level in contract["levels"]:
        level_id = str(level["id"])
        case = cases[level_id]
        terminal = manager.queue_job(case)
        record: dict[str, Any] = {
            "level": level_id,
            "caseId": case.id,
            "jobId": terminal.id,
            "status": terminal.status,
            "execution": terminal.execution,
            "command": terminal.command,
        }
        state["levels"].append(record)
        _write_json(output_dir / "campaign-state.json", state)
        deadline = time.monotonic() + timeout_seconds_per_level
        while terminal.status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                manager.cancel_job(terminal.id)
                record["status"] = "cancelled-timeout"
                state["status"] = "infrastructure-failed-retained"
                _write_json(output_dir / "campaign-state.json", state)
                raise FullOGridGeometryQualificationError(
                    f"{level_id} exceeded the frozen execution timeout"
                )
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                raise FullOGridGeometryQualificationError(
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
        if (
            terminal.status != "complete"
            or terminal.exitCode != 0
            or not terminal.caseDir
        ):
            state["status"] = "infrastructure-or-solver-failed-retained"
            _write_json(output_dir / "campaign-state.json", state)
            raise FullOGridGeometryQualificationError(
                f"{level_id} did not complete: status={terminal.status}, "
                f"exitCode={terminal.exitCode}, error={terminal.error}"
            )
        case_dir = Path(terminal.caseDir).resolve()
        if not case_dir.is_relative_to(output_dir):
            raise FullOGridGeometryQualificationError(
                f"{level_id} runtime evidence escaped the campaign directory"
            )
        case_dirs[level_id] = case_dir
        try:
            evaluation = evaluate_level(case_dir, case, level, contract)
        except Exception as exc:
            record["caseDirectory"] = str(case_dir.relative_to(output_dir))
            record["evaluatorFailure"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            state["status"] = "evidence-evaluator-failed-retained"
            state["finishedAt"] = datetime.now(timezone.utc).isoformat()
            _write_json(output_dir / "campaign-state.json", state)
            raise
        evaluation.update({"caseId": case.id, "jobId": terminal.id})
        evaluation_path = output_dir / "evaluations" / f"{level_id}.json"
        _write_json(evaluation_path, evaluation)
        record["caseDirectory"] = str(case_dir.relative_to(output_dir))
        record["evaluationPath"] = str(
            evaluation_path.relative_to(output_dir)
        )
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allMandatoryPerLevelGatesPassed"] = evaluation[
            "allMandatoryPerLevelGatesPassed"
        ]
        evaluations.append(evaluation)
        _write_json(output_dir / "campaign-state.json", state)
        if not evaluation["allMandatoryPerLevelGatesPassed"]:
            state["status"] = "scientific-gate-failed-retained"
            _write_json(output_dir / "campaign-state.json", state)
            raise FullOGridGeometryQualificationError(
                f"{level_id} failed a frozen mesh, solver, or identity gate"
            )

    samples = [
        {
            "id": evaluation["level"],
            "source": "solver-produced",
            "sourceArtifactSha256": evaluation["resultArtifact"]["sha256"],
            "characteristicCellSizeM": evaluation[
                "characteristicCellSizeM"
            ],
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
        and all(
            item["allMandatoryPerLevelGatesPassed"]
            for item in evaluations
        )
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
        help="Run deterministic generation and preflight without a solver.",
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
            raise FullOGridGeometryQualificationError(
                f"refusing to overwrite non-empty output directory: {output}"
            )
        output.mkdir(parents=True, exist_ok=True)
        contract, digest = load_frozen_contract()
        _cases, result = materialize_preflight(output, contract, digest)
    else:
        result = execute_campaign(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0
        if result.get("allExperimentalQualificationGatesPassed", True)
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
