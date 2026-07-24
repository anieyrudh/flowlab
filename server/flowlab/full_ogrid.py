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
FULL_OGRID_VERIFICATION_SCHEMA = "flowlab.full-ogrid-verification-contract.v1"
FULL_OGRID_PREVIEW_FORMAT = "flowlab-full-ogrid-preview-v1"
FULL_OGRID_REPRESENTATION = "full-revolution-five-block-ogrid"


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
        "volumeQuality": {
            "positiveVolume": True,
            "zeroVolumeCellCount": 0,
            "minimumCellVolumeM3": min(volumes),
            "maximumCellVolumeM3": max(volumes),
            "totalCellVolumeM3": sum(volumes),
        },
    }
