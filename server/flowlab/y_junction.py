"""Deterministic bounded true-3D Y-junction geometry.

This module owns one deliberately narrow geometry family: one circular inlet
and two identical circular branches at +/-30 degrees.  The solver mesh is a
Cartesian all-hex realization of the union of the three declared circular
primitives.  It is intentionally not an arbitrary network or CAD mesher.

Cell ownership is assigned while the mesh is constructed and retained as
explicit source-cell ranges.  Consumers must never reconstruct ownership from
cell coordinates.  Cells in the generated junction zone have a dedicated
artifact identity but no schematic edge owner.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from .mesh import VTK_HEXAHEDRON


Y_JUNCTION_PROFILE_SCHEMA = "flowlab.y-junction-profile.v1"
Y_JUNCTION_PREVIEW_FORMAT = "flowlab-y-junction-preview-v1"
Y_JUNCTION_REPRESENTATION = "generated-cartesian-all-hex-y-junction"
Y_JUNCTION_ARTIFACT_SCHEMA = "flowlab.generated-region-artifact.v1"
JUNCTION_ARTIFACT_ID = "generated:y-junction:junction-core:v1"
PATCH_ORDER = ("inlet", "outletUpper", "outletLower", "walls")


def _positive(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be a finite positive SI value.")
    return number


def _safe_zone_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not name or not re.match(r"[A-Za-z_]", name):
        name = f"edge_{name}"
    return name


@dataclass(frozen=True)
class YJunctionSpec:
    """Exact physical geometry and uniform Cartesian resolution."""

    inlet_length_m: float
    branch_length_m: float
    diameter_m: float
    cell_size_m: float
    branch_angle_degrees: float = 30.0

    def __post_init__(self) -> None:
        _positive(self.inlet_length_m, "Y-junction inlet length")
        _positive(self.branch_length_m, "Y-junction branch length")
        _positive(self.diameter_m, "Y-junction diameter")
        _positive(self.cell_size_m, "Y-junction cell size")
        if not math.isclose(
            float(self.branch_angle_degrees),
            30.0,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise ValueError("The bounded Y-junction requires exactly +/-30-degree branches.")
        cells_across = self.diameter_m / self.cell_size_m
        if cells_across < 4.0:
            raise ValueError("The bounded Y-junction requires at least four cells across the diameter.")
        for length, label in (
            (self.inlet_length_m, "inlet length"),
            (self.branch_length_m, "branch length"),
        ):
            ratio = length / self.cell_size_m
            if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1.0e-9):
                raise ValueError(f"Y-junction {label} must be an integer multiple of cell size.")

    @property
    def radius_m(self) -> float:
        return self.diameter_m / 2.0

    @property
    def angle_radians(self) -> float:
        return math.radians(self.branch_angle_degrees)

    @property
    def upper_direction(self) -> tuple[float, float, float]:
        return (math.cos(self.angle_radians), math.sin(self.angle_radians), 0.0)

    @property
    def lower_direction(self) -> tuple[float, float, float]:
        return (math.cos(self.angle_radians), -math.sin(self.angle_radians), 0.0)

    def manifest(self) -> dict[str, Any]:
        return {
            "inletLengthM": self.inlet_length_m,
            "branchLengthM": self.branch_length_m,
            "diameterM": self.diameter_m,
            "radiusM": self.radius_m,
            "cellSizeM": self.cell_size_m,
            "branchAnglesDegrees": [self.branch_angle_degrees, -self.branch_angle_degrees],
            "cellType": "hex",
            "wallRealization": (
                "Cartesian staircase approximation of prospectively declared circular primitives; "
                "geometry and solution discretization refine together."
            ),
        }


def _segment_membership(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    direction: tuple[float, float, float],
    length: float,
    radius: float,
) -> tuple[bool, float, float]:
    delta = tuple(point[index] - start[index] for index in range(3))
    axial = sum(delta[index] * direction[index] for index in range(3))
    radial = tuple(delta[index] - axial * direction[index] for index in range(3))
    radial_squared = sum(value * value for value in radial)
    tolerance = max(1.0e-15, radius * 1.0e-12)
    return (
        -tolerance <= axial <= length + tolerance
        and radial_squared <= radius * radius + tolerance,
        axial,
        radial_squared,
    )


def _connected(cell_keys: set[tuple[int, int, int]]) -> bool:
    if not cell_keys:
        return False
    frontier = [min(cell_keys)]
    visited = {frontier[0]}
    while frontier:
        i, j, k = frontier.pop()
        for neighbour in (
            (i - 1, j, k),
            (i + 1, j, k),
            (i, j - 1, k),
            (i, j + 1, k),
            (i, j, k - 1),
            (i, j, k + 1),
        ):
            if neighbour in cell_keys and neighbour not in visited:
                visited.add(neighbour)
                frontier.append(neighbour)
    return len(visited) == len(cell_keys)


def _face_neighbour_count(
    key: tuple[int, int, int],
    cell_keys: set[tuple[int, int, int]],
) -> int:
    i, j, k = key
    return sum(
        neighbour in cell_keys
        for neighbour in (
            (i - 1, j, k),
            (i + 1, j, k),
            (i, j - 1, k),
            (i, j + 1, k),
            (i, j, k - 1),
            (i, j, k + 1),
        )
    )


def _prune_underconnected_cells(
    cell_keys: set[tuple[int, int, int]],
) -> tuple[set[tuple[int, int, int]], set[tuple[int, int, int]]]:
    """Remove staircase surface cells that cannot form a well-posed hex stencil.

    OpenFOAM's cell-determinant check rejects a Cartesian cell with fewer than
    three face neighbours, even when its geometric volume is positive.  The
    pruning rule is construction-time topology, applied prospectively and
    deterministically before regions or ownership ranges are assigned.
    """

    retained = set(cell_keys)
    removed: set[tuple[int, int, int]] = set()
    while True:
        underconnected = {
            key for key in retained if _face_neighbour_count(key, retained) < 3
        }
        if not underconnected:
            break
        retained.difference_update(underconnected)
        removed.update(underconnected)
        if not retained or not _connected(retained):
            raise ValueError(
                "Y-junction topology pruning could not preserve one face-connected region."
            )
    return retained, removed


def _cell_group(
    memberships: dict[str, tuple[bool, float, float]],
    point: tuple[float, float, float],
    junction_extent_m: float,
) -> str:
    inlet = memberships["inlet"][0]
    upper = memberships["upper"][0]
    lower = memberships["lower"][0]
    if inlet and point[0] <= -junction_extent_m:
        return "inletEdge"
    if upper and memberships["upper"][1] >= junction_extent_m and not lower:
        return "upperEdge"
    if lower and memberships["lower"][1] >= junction_extent_m and not upper:
        return "lowerEdge"
    return "junction"


def _face_patch(
    *,
    group: str,
    face_center: tuple[float, float, float],
    spec: YJunctionSpec,
) -> str:
    band = math.sqrt(3.0) * spec.cell_size_m
    if group == "inletEdge" and face_center[0] <= -spec.inlet_length_m + band:
        return "inlet"
    if group == "upperEdge":
        axial = sum(
            face_center[index] * spec.upper_direction[index]
            for index in range(3)
        )
        if axial >= spec.branch_length_m - band:
            return "outletUpper"
    if group == "lowerEdge":
        axial = sum(
            face_center[index] * spec.lower_direction[index]
            for index in range(3)
        )
        if axial >= spec.branch_length_m - band:
            return "outletLower"
    return "walls"


def generate_mesh(
    spec: YJunctionSpec,
    *,
    inlet_edge_id: str,
    upper_edge_id: str,
    lower_edge_id: str,
) -> dict[str, Any]:
    """Generate the deterministic preview and source-cell provenance artifact."""

    edge_ids = (inlet_edge_id, upper_edge_id, lower_edge_id)
    if any(not isinstance(value, str) or not value for value in edge_ids):
        raise ValueError("Y-junction edge IDs must be non-empty strings.")
    if len(set(edge_ids)) != 3:
        raise ValueError("Y-junction edge IDs must be unique.")

    h = spec.cell_size_m
    radius = spec.radius_m
    branch_x = spec.branch_length_m * spec.upper_direction[0]
    branch_y = spec.branch_length_m * abs(spec.upper_direction[1])
    i_min = math.floor((-spec.inlet_length_m - radius) / h) - 1
    i_max = math.ceil((branch_x + radius) / h) + 1
    j_max = math.ceil((branch_y + radius) / h) + 1
    k_max = math.ceil(radius / h) + 1

    raw: dict[tuple[int, int, int], dict[str, Any]] = {}
    junction_extent = 1.5 * radius
    inlet_start = (-spec.inlet_length_m, 0.0, 0.0)
    inlet_direction = (1.0, 0.0, 0.0)
    origin = (0.0, 0.0, 0.0)
    for i in range(i_min, i_max):
        for j in range(-j_max, j_max):
            for k in range(-k_max, k_max):
                center = ((i + 0.5) * h, (j + 0.5) * h, (k + 0.5) * h)
                memberships = {
                    "inlet": _segment_membership(
                        center,
                        inlet_start,
                        inlet_direction,
                        spec.inlet_length_m,
                        radius,
                    ),
                    "upper": _segment_membership(
                        center,
                        origin,
                        spec.upper_direction,
                        spec.branch_length_m,
                        radius,
                    ),
                    "lower": _segment_membership(
                        center,
                        origin,
                        spec.lower_direction,
                        spec.branch_length_m,
                        radius,
                    ),
                }
                if not any(item[0] for item in memberships.values()):
                    continue
                raw[(i, j, k)] = {
                    "center": center,
                    "memberships": memberships,
                    "group": _cell_group(memberships, center, junction_extent),
                }

    if not _connected(set(raw)):
        raise ValueError("Generated Y-junction fluid cells are not one face-connected region.")
    initial_cell_count = len(raw)
    retained_keys, pruned_keys = _prune_underconnected_cells(set(raw))
    raw = {key: raw[key] for key in retained_keys}
    final_cell_keys = set(raw)
    if not _connected(final_cell_keys):
        raise ValueError("Pruned Y-junction fluid cells are not one face-connected region.")

    groups = ("inletEdge", "upperEdge", "lowerEdge", "junction")
    ordered_keys = [
        key
        for group in groups
        for key in sorted(raw)
        if raw[key]["group"] == group
    ]
    if len(ordered_keys) != len(raw):
        raise ValueError("Generated Y-junction cell grouping is incomplete.")
    counts = {group: sum(raw[key]["group"] == group for key in ordered_keys) for group in groups}
    if any(counts[group] <= 0 for group in groups):
        raise ValueError("Generated Y-junction requires non-empty inlet, branch, and junction regions.")

    point_ids: dict[tuple[int, int, int], int] = {}
    points: list[list[float]] = []

    def point_id(key: tuple[int, int, int]) -> int:
        if key in point_ids:
            return point_ids[key]
        point_ids[key] = len(points)
        points.append([round(coordinate * h, 15) for coordinate in key])
        return point_ids[key]

    cells: list[list[int]] = []
    cell_grid_keys: list[tuple[int, int, int]] = []
    for i, j, k in ordered_keys:
        cells.append(
            [
                point_id((i, j, k)),
                point_id((i + 1, j, k)),
                point_id((i + 1, j + 1, k)),
                point_id((i, j + 1, k)),
                point_id((i, j, k + 1)),
                point_id((i + 1, j, k + 1)),
                point_id((i + 1, j + 1, k + 1)),
                point_id((i, j + 1, k + 1)),
            ]
        )
        cell_grid_keys.append((i, j, k))

    starts: dict[str, int] = {}
    cursor = 0
    for group in groups:
        starts[group] = cursor
        cursor += counts[group]
    regions = [
        {
            "id": "inlet-edge-region",
            "role": "edge",
            "edgeId": inlet_edge_id,
            "cellStart": starts["inletEdge"],
            "cellCount": counts["inletEdge"],
            "ownershipSource": "generated-region-artifact",
        },
        {
            "id": "upper-branch-edge-region",
            "role": "edge",
            "edgeId": upper_edge_id,
            "cellStart": starts["upperEdge"],
            "cellCount": counts["upperEdge"],
            "ownershipSource": "generated-region-artifact",
        },
        {
            "id": "lower-branch-edge-region",
            "role": "edge",
            "edgeId": lower_edge_id,
            "cellStart": starts["lowerEdge"],
            "cellCount": counts["lowerEdge"],
            "ownershipSource": "generated-region-artifact",
        },
        {
            "id": "junction-generated-region",
            "role": "junction",
            "artifactIdentity": {
                "schema": Y_JUNCTION_ARTIFACT_SCHEMA,
                "artifactId": JUNCTION_ARTIFACT_ID,
                "generated": True,
                "schematicOwner": None,
            },
            "cellStart": starts["junction"],
            "cellCount": counts["junction"],
            "ownershipSource": "dedicated-generated-artifact",
        },
    ]

    cell_index_by_key = {key: index for index, key in enumerate(cell_grid_keys)}
    face_directions = (
        ((-1, 0, 0), (0, 4, 7, 3)),
        ((1, 0, 0), (1, 2, 6, 5)),
        ((0, -1, 0), (0, 1, 5, 4)),
        ((0, 1, 0), (3, 7, 6, 2)),
        ((0, 0, -1), (0, 3, 2, 1)),
        ((0, 0, 1), (4, 5, 6, 7)),
    )
    patch_faces: dict[str, list[dict[str, Any]]] = {name: [] for name in PATCH_ORDER}
    internal_face_count = 0
    for cell_index, ((i, j, k), cell) in enumerate(zip(cell_grid_keys, cells, strict=True)):
        group = raw[(i, j, k)]["group"]
        for direction, local_vertices in face_directions:
            neighbour_key = (i + direction[0], j + direction[1], k + direction[2])
            neighbour_index = cell_index_by_key.get(neighbour_key)
            if neighbour_index is not None:
                if cell_index < neighbour_index:
                    internal_face_count += 1
                continue
            vertices = [cell[index] for index in local_vertices]
            face_center = tuple(
                sum(points[vertex][axis] for vertex in vertices) / 4.0
                for axis in range(3)
            )
            patch = _face_patch(group=group, face_center=face_center, spec=spec)
            patch_faces[patch].append(
                {
                    "owner": cell_index,
                    "vertices": vertices,
                    "center": [round(value, 15) for value in face_center],
                }
            )

    if any(not patch_faces[name] for name in PATCH_ORDER):
        raise ValueError("Generated Y-junction does not contain every required boundary patch.")

    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    preview: dict[str, Any] = {
        "format": Y_JUNCTION_PREVIEW_FORMAT,
        "coordinateSystem": "physical-x-y-z-si",
        "spatialDimension": 3,
        "representation": Y_JUNCTION_REPRESENTATION,
        "runtimeSolverMesh": True,
        "proxyGeometry": False,
        "geometry": spec.manifest(),
        "boundsSpanM": [round(value, 15) for value in spans],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_HEXAHEDRON for _ in cells],
        "cellGridKeys": [list(key) for key in cell_grid_keys],
        "regions": regions,
        "patches": {
            name: {
                "type": "wall" if name == "walls" else "patch",
                "faceCount": len(patch_faces[name]),
            }
            for name in PATCH_ORDER
        },
        "topology": {
            "connectedFluidRegions": 1,
            "portPatchCount": 3,
            "portPatches": ["inlet", "outletUpper", "outletLower"],
            "wallPatch": "walls",
            "cellTypes": ["hex"],
            "cellCount": len(cells),
            "initialCellCount": initial_cell_count,
            "prunedUnderconnectedCellCount": len(pruned_keys),
            "minimumFaceNeighbourCount": min(
                _face_neighbour_count(key, final_cell_keys) for key in raw
            ),
            "internalFaceCount": internal_face_count,
            "boundaryFaceCount": sum(len(faces) for faces in patch_faces.values()),
        },
        "volumeQuality": {
            "positiveVolume": True,
            "zeroVolumeCellCount": 0,
            "minimumCellVolumeM3": h**3,
            "maximumCellVolumeM3": h**3,
            "totalCellVolumeM3": len(cells) * h**3,
        },
        "_openfoamPatchFaces": patch_faces,
    }
    digest_view = {key: value for key, value in preview.items() if not key.startswith("_")}
    preview["generationSha256"] = hashlib.sha256(
        json.dumps(digest_view, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return preview


def public_mesh(mesh: dict[str, Any]) -> dict[str, Any]:
    """Remove internal exporter details from the retained preview artifact."""

    return {key: value for key, value in mesh.items() if not key.startswith("_")}


def _foam_header(class_name: str, object_name: str, location: str = "constant/polyMesh") -> str:
    return f"""FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    location    "{location}";
    object      {object_name};
}}

"""


def _foam_label_list(class_name: str, object_name: str, values: list[int]) -> str:
    return (
        _foam_header(class_name, object_name)
        + f"{len(values)}\n(\n"
        + "".join(f"{value}\n" for value in values)
        + ")\n"
    )


def mesh_to_openfoam_polymesh(
    mesh: dict[str, Any],
    *,
    root: str = "constant/polyMesh",
) -> dict[str, str]:
    """Export the exact preview cells as a deterministic OpenFOAM polyMesh."""

    points = mesh["points"]
    cells = mesh["cells"]
    grid_keys = [tuple(int(value) for value in key) for key in mesh["cellGridKeys"]]
    cell_index_by_key = {key: index for index, key in enumerate(grid_keys)}
    face_directions = (
        ((-1, 0, 0), (0, 4, 7, 3)),
        ((1, 0, 0), (1, 2, 6, 5)),
        ((0, -1, 0), (0, 1, 5, 4)),
        ((0, 1, 0), (3, 7, 6, 2)),
        ((0, 0, -1), (0, 3, 2, 1)),
        ((0, 0, 1), (4, 5, 6, 7)),
    )
    internal_faces: list[tuple[int, int, list[int]]] = []
    boundary_faces: dict[str, list[tuple[int, list[int]]]] = {name: [] for name in PATCH_ORDER}
    patch_lookup: dict[tuple[int, tuple[int, ...]], str] = {}
    for patch_name, records in mesh["_openfoamPatchFaces"].items():
        for record in records:
            patch_lookup[(int(record["owner"]), tuple(sorted(record["vertices"])))] = patch_name

    for owner, (key, cell) in enumerate(zip(grid_keys, cells, strict=True)):
        for direction, local_vertices in face_directions:
            neighbour_key = (
                key[0] + direction[0],
                key[1] + direction[1],
                key[2] + direction[2],
            )
            neighbour = cell_index_by_key.get(neighbour_key)
            vertices = [cell[index] for index in local_vertices]
            if neighbour is not None:
                if owner < neighbour:
                    internal_faces.append((owner, neighbour, vertices))
                continue
            patch_name = patch_lookup.get((owner, tuple(sorted(vertices))))
            if patch_name is None:
                raise ValueError("Y-junction polyMesh boundary provenance is incomplete.")
            boundary_faces[patch_name].append((owner, vertices))

    face_values: list[list[int]] = []
    owner_values: list[int] = []
    neighbour_values: list[int] = []
    for owner, neighbour, vertices in internal_faces:
        face_values.append(vertices)
        owner_values.append(owner)
        neighbour_values.append(neighbour)

    boundary_specs: list[tuple[str, str, int, int]] = []
    for patch_name in PATCH_ORDER:
        start_face = len(face_values)
        for owner, vertices in boundary_faces[patch_name]:
            face_values.append(vertices)
            owner_values.append(owner)
        boundary_specs.append(
            (
                patch_name,
                "wall" if patch_name == "walls" else "patch",
                len(boundary_faces[patch_name]),
                start_face,
            )
        )

    points_text = (
        _foam_header("vectorField", "points")
        + f"{len(points)}\n(\n"
        + "".join(f"({point[0]:.17g} {point[1]:.17g} {point[2]:.17g})\n" for point in points)
        + ")\n"
    )
    faces_text = (
        _foam_header("faceList", "faces")
        + f"{len(face_values)}\n(\n"
        + "".join(f"{len(face)}(" + " ".join(str(value) for value in face) + ")\n" for face in face_values)
        + ")\n"
    )
    boundary_text = (
        _foam_header("polyBoundaryMesh", "boundary")
        + f"{len(boundary_specs)}\n(\n"
        + "".join(
            f"{name}\n{{\n    type {patch_type};\n    nFaces {count};\n    startFace {start};\n}}\n"
            for name, patch_type, count, start in boundary_specs
        )
        + ")\n"
    )

    zone_entries: list[str] = []
    for zone_index, region in enumerate(mesh["regions"]):
        start = int(region["cellStart"])
        count = int(region["cellCount"])
        if region["role"] == "junction":
            zone_name = "junction_generated"
        else:
            zone_name = f"edge_{_safe_zone_name(str(region['edgeId']))}"
        labels = "\n".join(str(value) for value in range(start, start + count))
        zone_entries.append(
            f"{zone_name}\n{{\n    type cellZone;\n    cellLabels List<label>\n"
            f"    {count}\n    (\n{labels}\n    );\n}}\n"
        )
    cell_zones = (
        _foam_header("regIOobject", "cellZones")
        + f"{len(zone_entries)}\n(\n"
        + "".join(zone_entries)
        + ")\n"
    )
    return {
        f"{root}/points": points_text,
        f"{root}/faces": faces_text,
        f"{root}/owner": _foam_label_list("labelList", "owner", owner_values),
        f"{root}/neighbour": _foam_label_list("labelList", "neighbour", neighbour_values),
        f"{root}/boundary": boundary_text,
        f"{root}/cellZones": cell_zones,
    }
