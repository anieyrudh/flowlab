"""Canonical bounded full-revolution straight-pipe O-grid topology.

This module is product geometry source, not validation evidence.  It owns the
deterministic five-block topology shared by OpenFOAM case generation and the
pre-solve three-dimensional inspection mesh.  Scientific campaign policy stays
in a separately versioned prospective contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .mesh import VTK_HEXAHEDRON


FULL_OGRID_PROFILE_SCHEMA = "flowlab.full-ogrid-profile.v1"
FULL_OGRID_PATH_PROFILE_SCHEMA = "flowlab.full-ogrid-path-profile.v1"
FULL_OGRID_VERIFICATION_SCHEMA = "flowlab.full-ogrid-verification-contract.v1"
FULL_OGRID_PREVIEW_FORMAT = "flowlab-full-ogrid-preview-v1"
FULL_OGRID_PATH_PREVIEW_FORMAT = "flowlab-full-ogrid-path-preview-v1"
FULL_OGRID_REPRESENTATION = "full-revolution-five-block-ogrid"
FULL_OGRID_PATH_REPRESENTATION = "full-revolution-multi-segment-five-block-ogrid"


def _positive_finite(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be a finite positive SI value.")
    return number


def _integer_at_least(value: int, minimum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}.")
    return value


@dataclass(frozen=True)
class FullOGridSpec:
    """Exact SI geometry and logical resolution for one five-block pipe."""

    length_m: float
    radius_m: float
    axial_cells: int
    annular_radial_cells: int
    circumferential_cells: int
    core_cells_per_side: int

    def __post_init__(self) -> None:
        _positive_finite(self.length_m, "full O-grid length")
        _positive_finite(self.radius_m, "full O-grid radius")
        _integer_at_least(self.axial_cells, 4, "full O-grid axialCells")
        _integer_at_least(
            self.annular_radial_cells,
            2,
            "full O-grid annularRadialCells",
        )
        _integer_at_least(
            self.circumferential_cells,
            16,
            "full O-grid circumferentialCells",
        )
        _integer_at_least(
            self.core_cells_per_side,
            4,
            "full O-grid coreCellsPerSide",
        )
        if self.circumferential_cells % 4 != 0:
            raise ValueError("full O-grid circumferentialCells must be divisible by four.")
        if self.core_cells_per_side != self.circumferential_cells // 4:
            raise ValueError(
                "full O-grid coreCellsPerSide must equal circumferentialCells/4 "
                "so every center-to-wall interface is conformal."
            )

    @property
    def core_radius_m(self) -> float:
        return self.radius_m / 4.0

    @property
    def circumferential_cells_per_quadrant(self) -> int:
        return self.circumferential_cells // 4

    @property
    def cross_section_cell_count(self) -> int:
        core = self.core_cells_per_side
        return core * core + self.circumferential_cells * self.annular_radial_cells

    @property
    def cell_count(self) -> int:
        return self.axial_cells * self.cross_section_cell_count

    def wall_geometry(self) -> dict[str, float | int]:
        angle = 2.0 * math.pi / self.circumferential_cells
        polygon_area = (
            0.5
            * self.circumferential_cells
            * self.radius_m**2
            * math.sin(angle)
        )
        exact_area = math.pi * self.radius_m**2
        return {
            "wallFacetCount": self.circumferential_cells,
            "facetAngleDegrees": math.degrees(angle),
            "polygonAreaM2": polygon_area,
            "analyticCircleAreaM2": exact_area,
            "areaRelativeDeficit": 1.0 - polygon_area / exact_area,
        }

    def topology_manifest(self) -> dict[str, Any]:
        core = self.core_cells_per_side
        return {
            "representation": FULL_OGRID_REPRESENTATION,
            "spatialDimension": 3,
            "cellTypes": ["hex"],
            "blockCount": 5,
            "blocks": [
                {
                    "id": "center",
                    "role": "core",
                    "logicalCells": [
                        self.axial_cells,
                        core,
                        core,
                    ],
                },
                *[
                    {
                        "id": f"wall-{quadrant}",
                        "role": "circumferential-wall",
                        "logicalCells": [
                            self.axial_cells,
                            self.annular_radial_cells,
                            self.circumferential_cells_per_quadrant,
                        ],
                    }
                    for quadrant in range(4)
                ],
            ],
            "resolution": {
                "axialCells": self.axial_cells,
                "annularRadialCells": self.annular_radial_cells,
                "circumferentialCells": self.circumferential_cells,
                "circumferentialCellsPerQuadrant": self.circumferential_cells_per_quadrant,
                "coreCellsPerSide": core,
                "cellCount": self.cell_count,
            },
            "interfaces": {
                "count": 4,
                "treatment": "conformal-internal-faces",
                "boundaryPatchCount": 0,
                "faceCount": 4 * core * self.axial_cells,
            },
            "patches": {
                "inlet": {
                    "role": "inlet",
                    "type": "patch",
                    "faceCount": self.cross_section_cell_count,
                },
                "outlet": {
                    "role": "outlet",
                    "type": "patch",
                    "faceCount": self.cross_section_cell_count,
                },
                "walls": {
                    "role": "wall",
                    "type": "wall",
                    "faceCount": self.circumferential_cells * self.axial_cells,
                },
            },
            "collapsedAxisCells": 0,
            "coreRadiusM": self.core_radius_m,
            "wallGeometry": self.wall_geometry(),
        }


def _foam_number(value: float) -> str:
    return f"{value:.17g}"


def block_mesh_dict(spec: FullOGridSpec) -> str:
    """Return a five-block full-volume OpenFOAM ``blockMeshDict``.

    The four center/wall interfaces share vertices and compatible logical
    counts.  They are omitted from ``boundary`` and therefore become internal
    faces after block merging.
    """

    axial = spec.axial_cells
    radial = spec.annular_radial_cells
    quadrant = spec.circumferential_cells_per_quadrant
    core_cells = spec.core_cells_per_side
    core_radius = _foam_number(spec.core_radius_m)
    radius = _foam_number(spec.radius_m)
    diagonal = _foam_number(spec.radius_m / math.sqrt(2.0))
    length = _foam_number(spec.length_m)
    return f"""/* FlowLab bounded full-revolution O-grid */
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}

convertToMeters 1;

vertices
(
    (0 {core_radius} 0)
    (0 0 {core_radius})
    (0 -{core_radius} 0)
    (0 0 -{core_radius})
    (0 {radius} 0)
    (0 0 {radius})
    (0 -{radius} 0)
    (0 0 -{radius})
    ({length} {core_radius} 0)
    ({length} 0 {core_radius})
    ({length} -{core_radius} 0)
    ({length} 0 -{core_radius})
    ({length} {radius} 0)
    ({length} 0 {radius})
    ({length} -{radius} 0)
    ({length} 0 -{radius})
);

blocks
(
    hex (0 8 9 1 3 11 10 2) ({axial} {core_cells} {core_cells}) simpleGrading (1 1 1)
    hex (0 8 12 4 1 9 13 5) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)
    hex (1 9 13 5 2 10 14 6) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)
    hex (2 10 14 6 3 11 15 7) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)
    hex (3 11 15 7 0 8 12 4) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)
);

edges
(
    arc 4 5 (0 {diagonal} {diagonal})
    arc 5 6 (0 -{diagonal} {diagonal})
    arc 6 7 (0 -{diagonal} -{diagonal})
    arc 7 4 (0 {diagonal} -{diagonal})
    arc 12 13 ({length} {diagonal} {diagonal})
    arc 13 14 ({length} -{diagonal} {diagonal})
    arc 14 15 ({length} -{diagonal} -{diagonal})
    arc 15 12 ({length} {diagonal} -{diagonal})
);

boundary
(
    inlet
    {{
        type patch;
        faces
        (
            (0 3 2 1)
            (0 1 5 4)
            (1 2 6 5)
            (2 3 7 6)
            (3 0 4 7)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (8 9 10 11)
            (8 12 13 9)
            (9 13 14 10)
            (10 14 15 11)
            (11 15 12 8)
        );
    }}
    walls
    {{
        type wall;
        faces
        (
            (4 12 13 5)
            (5 13 14 6)
            (6 14 15 7)
            (7 15 12 4)
        );
    }}
);

mergePatchPairs
(
);
"""


def _bilinear(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
    u: float,
    v: float,
) -> tuple[float, float]:
    return (
        (1.0 - u) * (1.0 - v) * a[0]
        + u * (1.0 - v) * b[0]
        + u * v * c[0]
        + (1.0 - u) * v * d[0],
        (1.0 - u) * (1.0 - v) * a[1]
        + u * (1.0 - v) * b[1]
        + u * v * c[1]
        + (1.0 - u) * v * d[1],
    )


def _cross_section(
    spec: FullOGridSpec,
) -> tuple[list[tuple[float, float]], list[list[int]], list[float]]:
    """Return unique ``(y,z)`` points, quad cells, and positive quad areas."""

    points: list[tuple[float, float]] = []
    point_lookup: dict[tuple[float, float], int] = {}
    cells: list[list[int]] = []
    areas: list[float] = []

    def point_index(point: tuple[float, float]) -> int:
        key = (round(point[0], 15), round(point[1], 15))
        existing = point_lookup.get(key)
        if existing is not None:
            return existing
        index = len(points)
        point_lookup[key] = index
        points.append(key)
        return index

    def add_quad(coordinates: list[tuple[float, float]]) -> None:
        indices = [point_index(point) for point in coordinates]
        if len(set(indices)) != 4:
            raise ValueError("full O-grid preview produced a collapsed cross-section cell.")
        twice_area = 0.0
        for index, current in enumerate(coordinates):
            following = coordinates[(index + 1) % len(coordinates)]
            twice_area += current[0] * following[1] - current[1] * following[0]
        area = abs(twice_area) / 2.0
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError("full O-grid preview produced a non-positive cross-section cell.")
        cells.append(indices)
        areas.append(area)

    inner = spec.core_radius_m
    north = (inner, 0.0)
    east = (0.0, inner)
    south = (-inner, 0.0)
    west = (0.0, -inner)
    core = spec.core_cells_per_side
    for v_index in range(core):
        v0 = v_index / core
        v1 = (v_index + 1) / core
        for u_index in range(core):
            u0 = u_index / core
            u1 = (u_index + 1) / core
            add_quad(
                [
                    _bilinear(north, east, south, west, u0, v0),
                    _bilinear(north, east, south, west, u1, v0),
                    _bilinear(north, east, south, west, u1, v1),
                    _bilinear(north, east, south, west, u0, v1),
                ]
            )

    inner_corners = (north, east, south, west, north)
    quadrant_cells = spec.circumferential_cells_per_quadrant
    radial_cells = spec.annular_radial_cells
    for quadrant in range(4):
        theta_start = quadrant * math.pi / 2.0
        inner_start = inner_corners[quadrant]
        inner_end = inner_corners[quadrant + 1]

        def mapped(theta_fraction: float, radial_fraction: float) -> tuple[float, float]:
            inner_point = (
                inner_start[0] + theta_fraction * (inner_end[0] - inner_start[0]),
                inner_start[1] + theta_fraction * (inner_end[1] - inner_start[1]),
            )
            theta = theta_start + theta_fraction * math.pi / 2.0
            outer_point = (
                spec.radius_m * math.cos(theta),
                spec.radius_m * math.sin(theta),
            )
            return (
                inner_point[0] + radial_fraction * (outer_point[0] - inner_point[0]),
                inner_point[1] + radial_fraction * (outer_point[1] - inner_point[1]),
            )

        for radial_index in range(radial_cells):
            r0 = radial_index / radial_cells
            r1 = (radial_index + 1) / radial_cells
            for theta_index in range(quadrant_cells):
                t0 = theta_index / quadrant_cells
                t1 = (theta_index + 1) / quadrant_cells
                add_quad(
                    [
                        mapped(t0, r0),
                        mapped(t1, r0),
                        mapped(t1, r1),
                        mapped(t0, r1),
                    ]
                )

    if len(cells) != spec.cross_section_cell_count:
        raise ValueError("full O-grid preview cross-section cell count is inconsistent.")
    return points, cells, areas


def preview_mesh(spec: FullOGridSpec, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a genuine three-dimensional, full-volume hexahedral preview."""

    cross_points, cross_cells, cross_areas = _cross_section(spec)
    points: list[list[float]] = []
    for axial_index in range(spec.axial_cells + 1):
        x = spec.length_m * axial_index / spec.axial_cells
        points.extend([[x, y, z] for y, z in cross_points])

    points_per_slice = len(cross_points)
    cells: list[list[int]] = []
    volumes: list[float] = []
    dx = spec.length_m / spec.axial_cells
    for axial_index in range(spec.axial_cells):
        low_offset = axial_index * points_per_slice
        high_offset = (axial_index + 1) * points_per_slice
        for cross_cell, area in zip(cross_cells, cross_areas, strict=True):
            cells.append(
                [
                    *(low_offset + point for point in cross_cell),
                    *(high_offset + point for point in cross_cell),
                ]
            )
            volumes.append(area * dx)

    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    if len(cells) != spec.cell_count or any(volume <= 0.0 for volume in volumes):
        raise ValueError("full O-grid preview failed its positive-volume contract.")
    cross_cell_count = len(cross_cells)

    def boundary_face(source_cell_id: int, point_ids: list[int]) -> dict[str, Any]:
        return {
            "sourceCellId": source_cell_id,
            "pointIds": point_ids,
            "center": [
                sum(points[point_id][axis] for point_id in point_ids) / len(point_ids)
                for axis in range(3)
            ],
        }

    inlet_faces = [
        boundary_face(source_cell_id, cells[source_cell_id][:4])
        for source_cell_id in range(cross_cell_count)
    ]
    outlet_start = (spec.axial_cells - 1) * cross_cell_count
    outlet_faces = [
        boundary_face(source_cell_id, cells[source_cell_id][4:])
        for source_cell_id in range(outlet_start, outlet_start + cross_cell_count)
    ]
    return {
        "format": FULL_OGRID_PREVIEW_FORMAT,
        "coordinateSystem": "physical-x-y-z-si",
        "spatialDimension": 3,
        "representation": "pre-solve-blockMesh-equivalent-full-ogrid",
        "runtimeSolverMesh": False,
        "proxyGeometry": False,
        "profileSchema": None if profile is None else profile.get("schema"),
        "boundsSpanM": [round(value, 12) for value in spans],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_HEXAHEDRON for _ in cells],
        "topology": spec.topology_manifest(),
        "boundaryFaceManifest": {
            "schema": "flowlab.boundary_faces.v1",
            "authorship": "generator",
            "cellIdentity": "flowlab_mesh_order",
            "patches": [
                {"name": "inlet", "role": "inlet", "faces": inlet_faces},
                {"name": "outlet", "role": "outlet", "faces": outlet_faces},
            ],
        },
        "volumeQuality": {
            "positiveVolume": True,
            "zeroVolumeCellCount": 0,
            "minimumCellVolumeM3": min(volumes),
            "maximumCellVolumeM3": max(volumes),
            "totalCellVolumeM3": sum(volumes),
        },
    }


@dataclass(frozen=True)
class FullOGridPathSegment:
    """One linear-radius portion of a conformal full-revolution path."""

    edge_id: str
    edge_type: str
    length_m: float
    inlet_radius_m: float
    outlet_radius_m: float
    axial_cells: int

    def __post_init__(self) -> None:
        if not self.edge_id.strip():
            raise ValueError("full O-grid path segments require non-empty edge IDs.")
        _positive_finite(self.length_m, "full O-grid path segment length")
        _positive_finite(self.inlet_radius_m, "full O-grid path inlet radius")
        _positive_finite(self.outlet_radius_m, "full O-grid path outlet radius")
        _integer_at_least(
            self.axial_cells,
            1,
            "full O-grid path segment axialCells",
        )


@dataclass(frozen=True)
class FullOGridPathSpec:
    """Conformal full-revolution O-grid for a straight multi-edge path."""

    segments: tuple[FullOGridPathSegment, ...]
    annular_radial_cells: int
    circumferential_cells: int
    core_cells_per_side: int

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("full O-grid path requires at least one segment.")
        _integer_at_least(
            self.annular_radial_cells,
            2,
            "full O-grid path annularRadialCells",
        )
        _integer_at_least(
            self.circumferential_cells,
            16,
            "full O-grid path circumferentialCells",
        )
        _integer_at_least(
            self.core_cells_per_side,
            4,
            "full O-grid path coreCellsPerSide",
        )
        if self.circumferential_cells % 4 != 0:
            raise ValueError(
                "full O-grid path circumferentialCells must be divisible by four."
            )
        if self.core_cells_per_side != self.circumferential_cells // 4:
            raise ValueError(
                "full O-grid path coreCellsPerSide must equal "
                "circumferentialCells/4."
            )
        for previous, current in zip(self.segments, self.segments[1:], strict=False):
            if not math.isclose(
                previous.outlet_radius_m,
                current.inlet_radius_m,
                rel_tol=1.0e-9,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    "full O-grid path radii must be continuous between segments."
                )

    @property
    def cross_section_cell_count(self) -> int:
        core = self.core_cells_per_side
        return (
            core * core
            + self.circumferential_cells * self.annular_radial_cells
        )

    @property
    def total_axial_cells(self) -> int:
        return sum(segment.axial_cells for segment in self.segments)

    @property
    def total_length_m(self) -> float:
        return sum(segment.length_m for segment in self.segments)

    @property
    def cell_count(self) -> int:
        return self.cross_section_cell_count * self.total_axial_cells

    def topology_manifest(self) -> dict[str, Any]:
        edge_ids = list(dict.fromkeys(segment.edge_id for segment in self.segments))
        return {
            "representation": FULL_OGRID_PATH_REPRESENTATION,
            "spatialDimension": 3,
            "cellTypes": ["hex"],
            "geometrySegmentCount": len(self.segments),
            "blockCount": 5 * len(self.segments),
            "pathEdgeIds": edge_ids,
            "resolution": {
                "totalAxialCells": self.total_axial_cells,
                "annularRadialCells": self.annular_radial_cells,
                "circumferentialCells": self.circumferential_cells,
                "circumferentialCellsPerQuadrant": (
                    self.circumferential_cells // 4
                ),
                "coreCellsPerSide": self.core_cells_per_side,
                "crossSectionCellCount": self.cross_section_cell_count,
                "cellCount": self.cell_count,
            },
            "interfaces": {
                "crossSectionBlockInterfaces": 4 * len(self.segments),
                "axialSegmentInterfaces": len(self.segments) - 1,
                "treatment": "conformal-internal-faces",
                "boundaryPatchCount": 0,
            },
            "patches": {
                "inlet": {
                    "role": "inlet",
                    "type": "patch",
                    "faceCount": self.cross_section_cell_count,
                },
                "outlet": {
                    "role": "outlet",
                    "type": "patch",
                    "faceCount": self.cross_section_cell_count,
                },
                "walls": {
                    "role": "wall",
                    "type": "wall",
                    "faceCount": (
                        self.circumferential_cells * self.total_axial_cells
                    ),
                },
            },
            "collapsedAxisCells": 0,
            "connectorCellCount": 0,
        }


def _path_slice_vertices(x_m: float, radius_m: float) -> list[tuple[float, float, float]]:
    core_radius = radius_m / 4.0
    return [
        (x_m, core_radius, 0.0),
        (x_m, 0.0, core_radius),
        (x_m, -core_radius, 0.0),
        (x_m, 0.0, -core_radius),
        (x_m, radius_m, 0.0),
        (x_m, 0.0, radius_m),
        (x_m, -radius_m, 0.0),
        (x_m, 0.0, -radius_m),
    ]


def path_block_mesh_dict(spec: FullOGridPathSpec) -> str:
    """Return a conformal five-block-per-segment OpenFOAM dictionary."""

    boundary_stations: list[tuple[float, float]] = [(0.0, spec.segments[0].inlet_radius_m)]
    cumulative_x = 0.0
    for segment in spec.segments:
        cumulative_x += segment.length_m
        boundary_stations.append((cumulative_x, segment.outlet_radius_m))

    vertices = [
        point
        for x_m, radius_m in boundary_stations
        for point in _path_slice_vertices(x_m, radius_m)
    ]
    vertex_text = "\n".join(
        f"    ({_foam_number(x)} {_foam_number(y)} {_foam_number(z)})"
        for x, y, z in vertices
    )

    quadrant = spec.circumferential_cells // 4
    blocks: list[str] = []
    for index, segment in enumerate(spec.segments):
        low = 8 * index
        high = low + 8
        axial = segment.axial_cells
        radial = spec.annular_radial_cells
        core = spec.core_cells_per_side
        blocks.extend(
            [
                (
                    f"    hex ({low} {high} {high + 1} {low + 1} "
                    f"{low + 3} {high + 3} {high + 2} {low + 2}) "
                    f"({axial} {core} {core}) simpleGrading (1 1 1)"
                ),
                (
                    f"    hex ({low} {high} {high + 4} {low + 4} "
                    f"{low + 1} {high + 1} {high + 5} {low + 5}) "
                    f"({axial} {radial} {quadrant}) simpleGrading (1 1 1)"
                ),
                (
                    f"    hex ({low + 1} {high + 1} {high + 5} {low + 5} "
                    f"{low + 2} {high + 2} {high + 6} {low + 6}) "
                    f"({axial} {radial} {quadrant}) simpleGrading (1 1 1)"
                ),
                (
                    f"    hex ({low + 2} {high + 2} {high + 6} {low + 6} "
                    f"{low + 3} {high + 3} {high + 7} {low + 7}) "
                    f"({axial} {radial} {quadrant}) simpleGrading (1 1 1)"
                ),
                (
                    f"    hex ({low + 3} {high + 3} {high + 7} {low + 7} "
                    f"{low} {high} {high + 4} {low + 4}) "
                    f"({axial} {radial} {quadrant}) simpleGrading (1 1 1)"
                ),
            ]
        )

    arcs: list[str] = []
    for index, (x_m, radius_m) in enumerate(boundary_stations):
        base = 8 * index
        diagonal = radius_m / math.sqrt(2.0)
        x = _foam_number(x_m)
        d = _foam_number(diagonal)
        arcs.extend(
            [
                f"    arc {base + 4} {base + 5} ({x} {d} {d})",
                f"    arc {base + 5} {base + 6} ({x} -{d} {d})",
                f"    arc {base + 6} {base + 7} ({x} -{d} -{d})",
                f"    arc {base + 7} {base + 4} ({x} {d} -{d})",
            ]
        )

    first = 0
    last = 8 * (len(boundary_stations) - 1)
    wall_faces: list[str] = []
    for index in range(len(spec.segments)):
        low = 8 * index
        high = low + 8
        wall_faces.extend(
            [
                f"            ({low + 4} {high + 4} {high + 5} {low + 5})",
                f"            ({low + 5} {high + 5} {high + 6} {low + 6})",
                f"            ({low + 6} {high + 6} {high + 7} {low + 7})",
                f"            ({low + 7} {high + 7} {high + 4} {low + 4})",
            ]
        )
    return f"""/* FlowLab bounded full-revolution multi-segment O-grid */
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}

convertToMeters 1;

vertices
(
{vertex_text}
);

blocks
(
{chr(10).join(blocks)}
);

edges
(
{chr(10).join(arcs)}
);

boundary
(
    inlet
    {{
        type patch;
        faces
        (
            ({first} {first + 3} {first + 2} {first + 1})
            ({first} {first + 1} {first + 5} {first + 4})
            ({first + 1} {first + 2} {first + 6} {first + 5})
            ({first + 2} {first + 3} {first + 7} {first + 6})
            ({first + 3} {first} {first + 4} {first + 7})
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            ({last} {last + 1} {last + 2} {last + 3})
            ({last} {last + 4} {last + 5} {last + 1})
            ({last + 1} {last + 5} {last + 6} {last + 2})
            ({last + 2} {last + 6} {last + 7} {last + 3})
            ({last + 3} {last + 7} {last + 4} {last})
        );
    }}
    walls
    {{
        type wall;
        faces
        (
{chr(10).join(wall_faces)}
        );
    }}
);

mergePatchPairs
(
);
"""


def path_preview_mesh(
    spec: FullOGridPathSpec,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the full-volume preview with explicit contiguous edge ranges."""

    axial_stations: list[tuple[float, float]] = []
    intervals: list[tuple[str, str]] = []
    cumulative_x = 0.0
    for segment_index, segment in enumerate(spec.segments):
        for local_index in range(segment.axial_cells + 1):
            if segment_index > 0 and local_index == 0:
                continue
            fraction = local_index / segment.axial_cells
            axial_stations.append(
                (
                    cumulative_x + fraction * segment.length_m,
                    segment.inlet_radius_m
                    + fraction
                    * (segment.outlet_radius_m - segment.inlet_radius_m),
                )
            )
        intervals.extend(
            [(segment.edge_id, segment.edge_type)] * segment.axial_cells
        )
        cumulative_x += segment.length_m

    slice_points: list[list[tuple[float, float]]] = []
    slice_cells: list[list[list[int]]] = []
    slice_areas: list[list[float]] = []
    points: list[list[float]] = []
    for x_m, radius_m in axial_stations:
        local_spec = FullOGridSpec(
            length_m=1.0,
            radius_m=radius_m,
            axial_cells=4,
            annular_radial_cells=spec.annular_radial_cells,
            circumferential_cells=spec.circumferential_cells,
            core_cells_per_side=spec.core_cells_per_side,
        )
        cross_points, cross_cells, cross_areas = _cross_section(local_spec)
        slice_points.append(cross_points)
        slice_cells.append(cross_cells)
        slice_areas.append(cross_areas)
        points.extend([[x_m, y, z] for y, z in cross_points])

    points_per_slice = len(slice_points[0])
    if any(len(candidate) != points_per_slice for candidate in slice_points):
        raise ValueError("full O-grid path cross-section topology is inconsistent.")
    cells: list[list[int]] = []
    volumes: list[float] = []
    regions: list[dict[str, Any]] = []
    for interval_index, (edge_id, edge_type) in enumerate(intervals):
        low_offset = interval_index * points_per_slice
        high_offset = (interval_index + 1) * points_per_slice
        dx = axial_stations[interval_index + 1][0] - axial_stations[interval_index][0]
        interval_cell_start = len(cells)
        for local_cell_index, low_cell in enumerate(slice_cells[interval_index]):
            high_cell = slice_cells[interval_index + 1][local_cell_index]
            cells.append(
                [
                    *(low_offset + point for point in low_cell),
                    *(high_offset + point for point in high_cell),
                ]
            )
            volumes.append(
                0.5
                * (
                    slice_areas[interval_index][local_cell_index]
                    + slice_areas[interval_index + 1][local_cell_index]
                )
                * dx
            )
        interval_count = len(cells) - interval_cell_start
        if regions and regions[-1]["edgeId"] == edge_id:
            regions[-1]["cellCount"] += interval_count
            regions[-1]["segmentCount"] += 1
        else:
            regions.append(
                {
                    "edgeId": edge_id,
                    "edgeType": edge_type,
                    "cellStart": interval_cell_start,
                    "cellCount": interval_count,
                    "segmentCount": 1,
                    "transverseDivisions": spec.cross_section_cell_count,
                }
            )

    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    if len(cells) != spec.cell_count or any(volume <= 0.0 for volume in volumes):
        raise ValueError("full O-grid path preview failed its positive-volume contract.")
    cross_cell_count = spec.cross_section_cell_count

    def boundary_face(source_cell_id: int, point_ids: list[int]) -> dict[str, Any]:
        return {
            "sourceCellId": source_cell_id,
            "pointIds": point_ids,
            "center": [
                sum(points[point_id][axis] for point_id in point_ids) / len(point_ids)
                for axis in range(3)
            ],
        }

    inlet_faces = [
        boundary_face(source_cell_id, cells[source_cell_id][:4])
        for source_cell_id in range(cross_cell_count)
    ]
    outlet_start = len(cells) - cross_cell_count
    outlet_faces = [
        boundary_face(source_cell_id, cells[source_cell_id][4:])
        for source_cell_id in range(outlet_start, len(cells))
    ]
    return {
        "format": FULL_OGRID_PATH_PREVIEW_FORMAT,
        "coordinateSystem": "physical-x-y-z-si",
        "spatialDimension": 3,
        "representation": "pre-solve-blockMesh-equivalent-full-ogrid-path",
        "runtimeSolverMesh": False,
        "proxyGeometry": False,
        "profileSchema": None if profile is None else profile.get("schema"),
        "boundsSpanM": [round(value, 12) for value in spans],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_HEXAHEDRON for _ in cells],
        "regions": regions,
        "topology": spec.topology_manifest(),
        "boundaryFaceManifest": {
            "schema": "flowlab.boundary_faces.v1",
            "authorship": "generator",
            "cellIdentity": "flowlab_mesh_order",
            "patches": [
                {"name": "inlet", "role": "inlet", "faces": inlet_faces},
                {"name": "outlet", "role": "outlet", "faces": outlet_faces},
            ],
        },
        "volumeQuality": {
            "positiveVolume": True,
            "zeroVolumeCellCount": 0,
            "minimumCellVolumeM3": min(volumes),
            "maximumCellVolumeM3": max(volumes),
            "totalCellVolumeM3": sum(volumes),
        },
    }
