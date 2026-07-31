from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


SOURCE_IDENTITY_CONTRACT_SCHEMA = "flowlab.source-cell-identity-contract.v1"
SOURCE_IDENTITY_REPORT_SCHEMA = "flowlab.openfoam-source-cell-identity.v1"
SOURCE_IDENTITY_ALGORITHM_V1 = "polyMesh-cell-vertex-signature-v1"
SOURCE_IDENTITY_ALGORITHM = "axisymmetric-logical-cell-vertex-signature-v2"
SUPPORTED_SOURCE_IDENTITY_ALGORITHMS = {
    SOURCE_IDENTITY_ALGORITHM_V1,
    SOURCE_IDENTITY_ALGORITHM,
}
SOURCE_CELL_ID_FIELD = "flowlabSourceCellId"
SOURCE_IDENTITY_CONTRACT_PATH = "constant/flowlab_result_identity_contract.json"
SOURCE_IDENTITY_REPORT_PATH = "postProcessing/flowlab_result_identity.json"


class ResultIdentityError(RuntimeError):
    """Raised when solver cells cannot be bound uniquely to generated source cells."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_coordinate(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ResultIdentityError("cell identity coordinates must be finite")
    if abs(number) < 1.0e-14:
        number = 0.0
    return format(number, ".9g")


def _cell_signature(
    points: list[Any],
    cell: list[Any],
    *,
    projection: str | None = None,
    coordinate_scale: float = 1.0,
) -> str:
    coordinates: set[tuple[str, str, str]] = set()
    for raw_index in cell:
        if not isinstance(raw_index, int) or isinstance(raw_index, bool):
            raise ResultIdentityError("cell connectivity indices must be integers")
        if raw_index < 0 or raw_index >= len(points):
            raise ResultIdentityError("cell connectivity escapes the point array")
        point = points[raw_index]
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise ResultIdentityError("cell identity points must have exactly three coordinates")
        coordinate = tuple(
            _canonical_coordinate(float(value) * coordinate_scale)
            for value in point
        )
        if projection == "xy":
            coordinate = (coordinate[0], coordinate[1], "0")
        coordinates.add(coordinate)
    if len(coordinates) < 4:
        raise ResultIdentityError("a volume cell identity requires at least four unique vertices")
    return "|".join(",".join(point) for point in sorted(coordinates))


def _source_signatures(
    mesh: dict[str, Any],
    *,
    projection: str | None = None,
    coordinate_scale: float = 1.0,
) -> list[str]:
    points = mesh.get("points")
    cells = mesh.get("cells")
    if not isinstance(points, list) or not points or not isinstance(cells, list) or not cells:
        raise ResultIdentityError("generated mesh identity requires non-empty points and cells")
    signatures = [
        _cell_signature(
            points,
            cell,
            projection=projection,
            coordinate_scale=coordinate_scale,
        )
        for cell in cells
        if isinstance(cell, list)
    ]
    if len(signatures) != len(cells):
        raise ResultIdentityError("generated mesh contains a non-list cell")
    if len(set(signatures)) != len(signatures):
        raise ResultIdentityError("generated mesh cell vertex signatures are not unique")
    return signatures


def _clustered_ranks(values: list[float]) -> tuple[list[int], int]:
    if not values or any(not math.isfinite(value) for value in values):
        raise ResultIdentityError("logical identity coordinates must be finite")
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    scale = max(1.0, max(abs(value) for value in values))
    tolerance = 1.0e-9 * scale
    ranks = [0] * len(values)
    representatives: list[float] = []
    for original_index, value in ordered:
        if not representatives or abs(value - representatives[-1]) > tolerance:
            representatives.append(value)
        ranks[original_index] = len(representatives) - 1
    return ranks, len(representatives)


def _axisymmetric_logical_signatures(
    points: list[Any],
    cells: list[Any],
) -> list[str]:
    parsed_points: list[tuple[float, float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise ResultIdentityError(
                "axisymmetric logical identity points require three coordinates"
            )
        coordinate = tuple(float(value) for value in point)
        if any(not math.isfinite(value) for value in coordinate):
            raise ResultIdentityError(
                "axisymmetric logical identity coordinates must be finite"
            )
        parsed_points.append(coordinate)
    x_ranks, x_count = _clustered_ranks([point[0] for point in parsed_points])
    if x_count < 2:
        raise ResultIdentityError(
            "axisymmetric logical identity requires multiple axial stations"
        )

    radii = [math.hypot(point[1], point[2]) for point in parsed_points]
    radial_ranks = [0] * len(parsed_points)
    for x_rank in range(x_count):
        point_indices = [
            index for index, candidate in enumerate(x_ranks) if candidate == x_rank
        ]
        station_ranks, radial_count = _clustered_ranks(
            [radii[index] for index in point_indices]
        )
        if radial_count < 2:
            raise ResultIdentityError(
                "axisymmetric logical identity requires radial volume cells"
            )
        for index, radial_rank in zip(point_indices, station_ranks, strict=True):
            radial_ranks[index] = radial_rank

    logical_points: list[tuple[int, int, int]] = []
    scale = max(1.0, max(radii, default=0.0))
    axis_tolerance = 1.0e-12 * scale
    for index, point in enumerate(parsed_points):
        radius = radii[index]
        if radius <= axis_tolerance:
            wedge_side = 0
        elif point[2] < -axis_tolerance:
            wedge_side = -1
        elif point[2] > axis_tolerance:
            wedge_side = 1
        else:
            raise ResultIdentityError(
                "a non-axis wedge point has no explicit front/back side"
            )
        logical_points.append(
            (x_ranks[index], radial_ranks[index], wedge_side)
        )

    signatures: list[str] = []
    for cell in cells:
        if not isinstance(cell, list):
            raise ResultIdentityError(
                "axisymmetric logical identity contains a non-list cell"
            )
        labels: set[tuple[int, int, int]] = set()
        for raw_index in cell:
            if (
                not isinstance(raw_index, int)
                or isinstance(raw_index, bool)
                or raw_index < 0
                or raw_index >= len(logical_points)
            ):
                raise ResultIdentityError(
                    "axisymmetric logical cell connectivity escapes the point array"
                )
            labels.add(logical_points[raw_index])
        if len(labels) < 6:
            raise ResultIdentityError(
                "axisymmetric wedge cell identity requires at least six logical vertices"
            )
        signatures.append(
            "|".join(",".join(str(value) for value in label) for label in sorted(labels))
        )
    if len(set(signatures)) != len(signatures):
        raise ResultIdentityError(
            "axisymmetric logical cell vertex signatures are not unique"
        )
    return signatures


def source_cell_identity_contract(mesh_snapshot: str) -> dict[str, Any]:
    try:
        mesh = json.loads(mesh_snapshot)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ResultIdentityError("generated mesh JSON is invalid") from exc
    if not isinstance(mesh, dict):
        raise ResultIdentityError("generated mesh identity root must be an object")
    points = mesh.get("points") if isinstance(mesh.get("points"), list) else []
    projection = (
        "xy"
        if points
        and all(
            isinstance(point, (list, tuple))
            and len(point) == 3
            and math.isclose(float(point[2]), 0.0, abs_tol=1.0e-14)
            for point in points
        )
        else None
    )
    snappy = (
        mesh.get("openfoamSnappyHandoff")
        if isinstance(mesh.get("openfoamSnappyHandoff"), dict)
        else {}
    )
    starter = (
        snappy.get("starterGeometry")
        if isinstance(snappy.get("starterGeometry"), dict)
        else {}
    )
    coordinate_scale = (
        float(starter.get("scale", 1.0))
        if projection == "xy"
        else 1.0
    )
    if not math.isfinite(coordinate_scale) or coordinate_scale <= 0.0:
        raise ResultIdentityError("generated mesh identity coordinate scale is invalid")
    algorithm = (
        SOURCE_IDENTITY_ALGORITHM
        if mesh.get("profileSchema") == "flowlab.axisymmetric-profile.v1"
        else SOURCE_IDENTITY_ALGORITHM_V1
    )
    signatures = (
        _axisymmetric_logical_signatures(points, mesh.get("cells", []))
        if algorithm == SOURCE_IDENTITY_ALGORITHM
        else _source_signatures(
            mesh,
            projection=projection,
            coordinate_scale=coordinate_scale,
        )
    )
    regions = mesh.get("regions") if isinstance(mesh.get("regions"), list) else []
    edge_ranges: list[dict[str, Any]] = []
    unowned_ranges: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        start = region.get("cellStart")
        count = region.get("cellCount")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or start < 0
            or count <= 0
            or start + count > len(signatures)
        ):
            continue
        record = {
            "cellStart": start,
            "cellCount": count,
            "edgeType": str(region.get("edgeType") or "unknown"),
        }
        edge_id = region.get("edgeId")
        if isinstance(edge_id, str) and edge_id and region.get("edgeType") != "connector":
            edge_ranges.append({**record, "edgeId": edge_id})
        else:
            unowned_ranges.append(record)
    signature_text = "\n".join(signatures) + "\n"
    return {
        "schema": SOURCE_IDENTITY_CONTRACT_SCHEMA,
        "algorithm": algorithm,
        "identityField": SOURCE_CELL_ID_FIELD,
        "generatedMeshSha256": _sha256_text(mesh_snapshot),
        "sourceCellCount": len(signatures),
        "sourceCellSignaturesSha256": _sha256_text(signature_text),
        "solverCellVertexProjection": projection,
        "generatedToSolverCoordinateScale": coordinate_scale,
        "edgeRanges": edge_ranges,
        "unownedRanges": unowned_ranges,
        "orderingAssumptionAllowed": False,
        "status": "generated-pending-solver-topology-verification",
    }


def _strip_foam_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _foam_list_body(text: str, label: str) -> tuple[int, str]:
    clean = _strip_foam_comments(text)
    matches = list(
        re.finditer(
            r"(?:^|\n)\s*(\d+)\s*\n\s*\(\s*\n(.*?)\n\s*\)\s*;?\s*(?:\n|$)",
            clean,
            flags=re.DOTALL,
        )
    )
    if not matches:
        raise ResultIdentityError(f"OpenFOAM {label} list is missing")
    count = int(matches[-1].group(1))
    return count, matches[-1].group(2)


def _parse_poly_points(text: str) -> list[list[float]]:
    count, body = _foam_list_body(text, "points")
    points = [
        [float(value) for value in match.group(1).split()]
        for match in re.finditer(r"\(([^()]+)\)", body)
    ]
    if len(points) != count or any(len(point) != 3 for point in points):
        raise ResultIdentityError("OpenFOAM points count or coordinate width is invalid")
    return points


def _parse_poly_faces(text: str) -> list[list[int]]:
    count, body = _foam_list_body(text, "faces")
    faces = [
        [int(value) for value in match.group(2).split()]
        for match in re.finditer(r"(\d+)\s*\(([^()]*)\)", body)
    ]
    if len(faces) != count or any(len(face) < 3 for face in faces):
        raise ResultIdentityError("OpenFOAM faces count or connectivity is invalid")
    return faces


def _parse_label_list(text: str, label: str) -> list[int]:
    count, body = _foam_list_body(text, label)
    values = [int(value) for value in re.findall(r"[-+]?\d+", body)]
    if len(values) != count:
        raise ResultIdentityError(f"OpenFOAM {label} count is invalid")
    return values


def _solver_cells(poly_mesh: Path) -> tuple[list[list[float]], list[list[int]]]:
    required = {
        "points": poly_mesh / "points",
        "faces": poly_mesh / "faces",
        "owner": poly_mesh / "owner",
        "neighbour": poly_mesh / "neighbour",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ResultIdentityError(
            "OpenFOAM source-cell identity is missing polyMesh files: " + ", ".join(missing)
        )
    points = _parse_poly_points(required["points"].read_text(encoding="utf-8", errors="replace"))
    faces = _parse_poly_faces(required["faces"].read_text(encoding="utf-8", errors="replace"))
    owners = _parse_label_list(required["owner"].read_text(encoding="utf-8", errors="replace"), "owner")
    neighbours = _parse_label_list(
        required["neighbour"].read_text(encoding="utf-8", errors="replace"),
        "neighbour",
    )
    if len(owners) != len(faces) or len(neighbours) > len(faces):
        raise ResultIdentityError("OpenFOAM owner/neighbour topology does not match faces")
    cell_count = max([*owners, *neighbours], default=-1) + 1
    if cell_count <= 0:
        raise ResultIdentityError("OpenFOAM polyMesh contains no cells")
    cell_points: list[set[int]] = [set() for _ in range(cell_count)]
    for face_index, face in enumerate(faces):
        owner = owners[face_index]
        if owner < 0 or owner >= cell_count:
            raise ResultIdentityError("OpenFOAM owner index escapes the cell range")
        cell_points[owner].update(face)
        if face_index < len(neighbours):
            neighbour = neighbours[face_index]
            if neighbour < 0 or neighbour >= cell_count:
                raise ResultIdentityError("OpenFOAM neighbour index escapes the cell range")
            cell_points[neighbour].update(face)
    if any(len(indices) < 4 for indices in cell_points):
        raise ResultIdentityError("OpenFOAM polyMesh contains a cell with fewer than four vertices")
    return points, [sorted(indices) for indices in cell_points]


def resolve_openfoam_source_cell_identity(
    case_dir: Path,
    generated_mesh: dict[str, Any],
) -> dict[str, Any] | None:
    contract_path = case_dir / SOURCE_IDENTITY_CONTRACT_PATH
    if not contract_path.is_file():
        return None
    mesh_path = case_dir / "mesh" / "flowlab_mesh.json"
    if not mesh_path.is_file():
        raise ResultIdentityError("generated mesh JSON is missing from the materialized case")
    contract_text = contract_path.read_text(encoding="utf-8")
    try:
        contract = json.loads(contract_text)
    except json.JSONDecodeError as exc:
        raise ResultIdentityError("source-cell identity contract is invalid JSON") from exc
    if (
        not isinstance(contract, dict)
        or contract.get("schema") != SOURCE_IDENTITY_CONTRACT_SCHEMA
        or contract.get("algorithm") not in SUPPORTED_SOURCE_IDENTITY_ALGORITHMS
        or contract.get("identityField") != SOURCE_CELL_ID_FIELD
        or contract.get("orderingAssumptionAllowed") is not False
    ):
        raise ResultIdentityError("source-cell identity contract is unsupported")
    mesh_text = mesh_path.read_text(encoding="utf-8")
    if _sha256_text(mesh_text) != contract.get("generatedMeshSha256"):
        raise ResultIdentityError("generated mesh hash does not match the source-cell identity contract")
    projection = contract.get("solverCellVertexProjection")
    if projection not in {None, "xy"}:
        raise ResultIdentityError("source-cell identity projection is unsupported")
    coordinate_scale = contract.get("generatedToSolverCoordinateScale", 1.0)
    if (
        not isinstance(coordinate_scale, (int, float))
        or isinstance(coordinate_scale, bool)
        or not math.isfinite(float(coordinate_scale))
        or float(coordinate_scale) <= 0.0
    ):
        raise ResultIdentityError("source-cell identity coordinate scale is invalid")
    algorithm = str(contract["algorithm"])
    source_signatures = (
        _axisymmetric_logical_signatures(
            generated_mesh.get("points", []),
            generated_mesh.get("cells", []),
        )
        if algorithm == SOURCE_IDENTITY_ALGORITHM
        else _source_signatures(
            generated_mesh,
            projection=projection,
            coordinate_scale=float(coordinate_scale),
        )
    )
    if len(source_signatures) != contract.get("sourceCellCount"):
        raise ResultIdentityError("generated source-cell count does not match the identity contract")
    if _sha256_text("\n".join(source_signatures) + "\n") != contract.get(
        "sourceCellSignaturesSha256"
    ):
        raise ResultIdentityError("generated source-cell signatures do not match the identity contract")

    solver_points, solver_cells = _solver_cells(case_dir / "constant" / "polyMesh")
    solver_signatures = (
        _axisymmetric_logical_signatures(solver_points, solver_cells)
        if algorithm == SOURCE_IDENTITY_ALGORITHM
        else [
            _cell_signature(solver_points, cell, projection=projection)
            for cell in solver_cells
        ]
    )
    if len(solver_signatures) != len(source_signatures):
        raise ResultIdentityError(
            "OpenFOAM solver cell count does not match the generated source-cell count"
        )
    source_by_signature = {signature: index for index, signature in enumerate(source_signatures)}
    if len(source_by_signature) != len(source_signatures):
        raise ResultIdentityError("generated source-cell signatures are ambiguous")
    solver_to_source: list[int] = []
    for signature in solver_signatures:
        source_index = source_by_signature.get(signature)
        if source_index is None:
            raise ResultIdentityError(
                "an OpenFOAM solver cell has no matching generated source-cell signature"
            )
        solver_to_source.append(source_index)
    if sorted(solver_to_source) != list(range(len(source_signatures))):
        raise ResultIdentityError("OpenFOAM solver-to-source mapping is not one-to-one")

    mapping_text = "\n".join(
        f"{solver_index} {source_index}"
        for solver_index, source_index in enumerate(solver_to_source)
    ) + "\n"
    topology_paths = [
        case_dir / "constant" / "polyMesh" / name
        for name in ("points", "faces", "owner", "neighbour", "boundary")
    ]
    report = {
        "schema": SOURCE_IDENTITY_REPORT_SCHEMA,
        "contractSchema": SOURCE_IDENTITY_CONTRACT_SCHEMA,
        "contractSha256": _sha256_text(contract_text),
        "algorithm": algorithm,
        "solverCellVertexProjection": projection,
        "generatedToSolverCoordinateScale": coordinate_scale,
        "identityField": SOURCE_CELL_ID_FIELD,
        "generatedMeshSha256": _sha256_text(mesh_text),
        "sourceCellCount": len(source_signatures),
        "solverCellCount": len(solver_signatures),
        "solverToSourceCell": solver_to_source,
        "solverToSourceCellSha256": _sha256_text(mapping_text),
        "polyMeshSha256": {
            path.name: _sha256_file(path)
            for path in topology_paths
            if path.is_file()
        },
        "uniqueExplicitIdentity": True,
        "orderingAssumptionUsed": False,
        "verified": True,
    }
    report_path = case_dir / SOURCE_IDENTITY_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def reorder_solver_values_to_source(
    values: list[Any],
    identity: dict[str, Any],
) -> list[Any]:
    mapping = identity.get("solverToSourceCell")
    source_count = identity.get("sourceCellCount")
    if (
        identity.get("verified") is not True
        or not isinstance(mapping, list)
        or not isinstance(source_count, int)
        or len(values) != len(mapping)
        or source_count != len(mapping)
    ):
        raise ResultIdentityError("solver values cannot be reordered without a verified identity map")
    reordered: list[Any | None] = [None] * source_count
    for solver_index, source_index in enumerate(mapping):
        if (
            not isinstance(source_index, int)
            or isinstance(source_index, bool)
            or source_index < 0
            or source_index >= source_count
            or reordered[source_index] is not None
        ):
            raise ResultIdentityError("solver-to-source identity map is invalid or ambiguous")
        reordered[source_index] = values[solver_index]
    if any(value is None for value in reordered):
        raise ResultIdentityError("solver-to-source identity map is incomplete")
    return list(reordered)
