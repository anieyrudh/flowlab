"""Canonical bounded 90-degree circular-elbow O-grid topology.

This module owns one deliberately narrow true-3D geometry: a constant-diameter
pipe with a straight inlet leg, one circular 90-degree bend, and a straight
outlet leg.  It is not a CAD importer or a general swept-pipe kernel.
Scientific qualification policy remains in a separately frozen contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .full_ogrid import FullOGridSpec, _cross_section
from .mesh import VTK_HEXAHEDRON


CURVED_ELBOW_PROFILE_SCHEMA = "flowlab.curved-elbow-ogrid-profile.v1"
CURVED_ELBOW_VERIFICATION_SCHEMA = "flowlab.curved-elbow-verification-request.v1"
CURVED_ELBOW_PREVIEW_FORMAT = "flowlab-curved-elbow-ogrid-preview-v1"
CURVED_ELBOW_REPRESENTATION = "canonical-90deg-circular-elbow-fifteen-block-ogrid"
CURVED_ELBOW_COMPONENTS = ("inlet-leg", "elbow", "outlet-leg")


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
class CurvedElbowSpec:
    """Exact SI geometry and logical resolution for the bounded elbow."""

    diameter_m: float
    centreline_radius_m: float
    inlet_leg_m: float
    outlet_leg_m: float
    inlet_axial_cells: int
    bend_axial_cells: int
    outlet_axial_cells: int
    annular_radial_cells: int
    circumferential_cells: int
    core_cells_per_side: int
    bend_angle_degrees: float = 90.0

    def __post_init__(self) -> None:
        _positive_finite(self.diameter_m, "curved elbow diameter")
        _positive_finite(self.centreline_radius_m, "curved elbow centreline radius")
        _positive_finite(self.inlet_leg_m, "curved elbow inlet leg")
        _positive_finite(self.outlet_leg_m, "curved elbow outlet leg")
        _integer_at_least(self.inlet_axial_cells, 4, "curved elbow inletAxialCells")
        _integer_at_least(self.bend_axial_cells, 4, "curved elbow bendAxialCells")
        _integer_at_least(self.outlet_axial_cells, 4, "curved elbow outletAxialCells")
        _integer_at_least(
            self.annular_radial_cells,
            2,
            "curved elbow annularRadialCells",
        )
        _integer_at_least(
            self.circumferential_cells,
            16,
            "curved elbow circumferentialCells",
        )
        _integer_at_least(
            self.core_cells_per_side,
            4,
            "curved elbow coreCellsPerSide",
        )
        if not math.isclose(
            float(self.bend_angle_degrees),
            90.0,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise ValueError("curved elbow bend angle must be exactly 90 degrees.")
        if self.circumferential_cells % 4 != 0:
            raise ValueError("curved elbow circumferentialCells must be divisible by four.")
        if self.core_cells_per_side != self.circumferential_cells // 4:
            raise ValueError(
                "curved elbow coreCellsPerSide must equal circumferentialCells/4 "
                "so every center-to-wall interface is conformal."
            )
        if self.centreline_radius_m <= self.radius_m:
            raise ValueError("curved elbow centreline radius must exceed the pipe radius.")

    @property
    def radius_m(self) -> float:
        return self.diameter_m / 2.0

    @property
    def core_radius_m(self) -> float:
        return self.radius_m / 4.0

    @property
    def centreline_radius_over_diameter(self) -> float:
        return self.centreline_radius_m / self.diameter_m

    @property
    def inlet_leg_over_diameter(self) -> float:
        return self.inlet_leg_m / self.diameter_m

    @property
    def outlet_leg_over_diameter(self) -> float:
        return self.outlet_leg_m / self.diameter_m

    @property
    def bend_arc_length_m(self) -> float:
        return self.centreline_radius_m * math.pi / 2.0

    @property
    def total_centreline_length_m(self) -> float:
        return self.inlet_leg_m + self.bend_arc_length_m + self.outlet_leg_m

    @property
    def circumferential_cells_per_quadrant(self) -> int:
        return self.circumferential_cells // 4

    @property
    def cross_section_cell_count(self) -> int:
        core = self.core_cells_per_side
        return core * core + self.circumferential_cells * self.annular_radial_cells

    @property
    def component_cell_counts(self) -> tuple[int, int, int]:
        cross = self.cross_section_cell_count
        return (
            self.inlet_axial_cells * cross,
            self.bend_axial_cells * cross,
            self.outlet_axial_cells * cross,
        )

    @property
    def cell_count(self) -> int:
        return sum(self.component_cell_counts)

    @property
    def analytic_fluid_volume_m3(self) -> float:
        return math.pi * self.radius_m**2 * self.total_centreline_length_m

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

    def component_regions(self, edge_id: str) -> list[dict[str, Any]]:
        counts = self.component_cell_counts
        starts = (0, counts[0], counts[0] + counts[1])
        axial = (
            self.inlet_axial_cells,
            self.bend_axial_cells,
            self.outlet_axial_cells,
        )
        return [
            {
                "edgeId": edge_id,
                "componentId": component,
                "cellStart": starts[index],
                "cellCount": counts[index],
                "axialCells": axial[index],
            }
            for index, component in enumerate(CURVED_ELBOW_COMPONENTS)
        ]

    def topology_manifest(self) -> dict[str, Any]:
        component_blocks = [
            {
                "id": component,
                "blockCount": 5,
                "axialCells": axial,
                "cellCount": count,
            }
            for component, axial, count in zip(
                CURVED_ELBOW_COMPONENTS,
                (
                    self.inlet_axial_cells,
                    self.bend_axial_cells,
                    self.outlet_axial_cells,
                ),
                self.component_cell_counts,
                strict=True,
            )
        ]
        return {
            "representation": CURVED_ELBOW_REPRESENTATION,
            "spatialDimension": 3,
            "cellTypes": ["hex"],
            "blockCount": 15,
            "componentBlocks": component_blocks,
            "resolution": {
                "inletAxialCells": self.inlet_axial_cells,
                "bendAxialCells": self.bend_axial_cells,
                "outletAxialCells": self.outlet_axial_cells,
                "annularRadialCells": self.annular_radial_cells,
                "circumferentialCells": self.circumferential_cells,
                "circumferentialCellsPerQuadrant": self.circumferential_cells_per_quadrant,
                "coreCellsPerSide": self.core_cells_per_side,
                "crossSectionCellCount": self.cross_section_cell_count,
                "cellCount": self.cell_count,
            },
            "interfaces": {
                "centerWallCountPerComponent": 4,
                "longitudinalComponentCount": 2,
                "treatment": "shared-vertex-conformal-internal-faces",
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
                    "faceCount": self.circumferential_cells
                    * (
                        self.inlet_axial_cells
                        + self.bend_axial_cells
                        + self.outlet_axial_cells
                    ),
                },
            },
            "collapsedAxisCells": 0,
            "coreRadiusM": self.core_radius_m,
            "geometry": {
                "diameterM": self.diameter_m,
                "centrelineRadiusM": self.centreline_radius_m,
                "bendAngleDegrees": self.bend_angle_degrees,
                "inletLegM": self.inlet_leg_m,
                "outletLegM": self.outlet_leg_m,
                "bendArcLengthM": self.bend_arc_length_m,
                "totalCentrelineLengthM": self.total_centreline_length_m,
                "centrelineRadiusOverDiameter": self.centreline_radius_over_diameter,
                "inletLegOverDiameter": self.inlet_leg_over_diameter,
                "outletLegOverDiameter": self.outlet_leg_over_diameter,
                "analyticFluidVolumeM3": self.analytic_fluid_volume_m3,
            },
            "wallGeometry": self.wall_geometry(),
        }


def _foam_number(value: float) -> str:
    return f"{value:.17g}"


def _station_point(
    spec: CurvedElbowSpec,
    station: int,
    u: float,
    v: float,
) -> tuple[float, float, float]:
    if station == 0:
        centre = (0.0, 0.0)
        normal = (0.0, 1.0)
    elif station == 1:
        centre = (spec.inlet_leg_m, 0.0)
        normal = (0.0, 1.0)
    elif station == 2:
        centre = (
            spec.inlet_leg_m + spec.centreline_radius_m,
            spec.centreline_radius_m,
        )
        normal = (-1.0, 0.0)
    elif station == 3:
        centre = (
            spec.inlet_leg_m + spec.centreline_radius_m,
            spec.centreline_radius_m + spec.outlet_leg_m,
        )
        normal = (-1.0, 0.0)
    else:  # pragma: no cover - internal invariant
        raise ValueError("curved elbow station index is out of range")
    return (
        centre[0] + u * normal[0],
        centre[1] + u * normal[1],
        v,
    )


def _bend_point(
    spec: CurvedElbowSpec,
    theta: float,
    u: float,
    v: float,
) -> tuple[float, float, float]:
    radius = spec.centreline_radius_m - u
    return (
        spec.inlet_leg_m + radius * math.cos(theta),
        spec.centreline_radius_m + radius * math.sin(theta),
        v,
    )


def _cross_vertices(spec: CurvedElbowSpec) -> tuple[tuple[float, float], ...]:
    inner = spec.core_radius_m
    radius = spec.radius_m
    return (
        (inner, 0.0),
        (0.0, inner),
        (-inner, 0.0),
        (0.0, -inner),
        (radius, 0.0),
        (0.0, radius),
        (-radius, 0.0),
        (0.0, -radius),
    )


def _format_point(point: tuple[float, float, float]) -> str:
    return f"({_foam_number(point[0])} {_foam_number(point[1])} {_foam_number(point[2])})"


def _segment_blocks(
    low: int,
    high: int,
    *,
    axial: int,
    radial: int,
    quadrant: int,
    core: int,
) -> list[str]:
    return [
        f"    hex ({low} {high} {high + 1} {low + 1} {low + 3} {high + 3} {high + 2} {low + 2}) ({axial} {core} {core}) simpleGrading (1 1 1)",
        f"    hex ({low} {high} {high + 4} {low + 4} {low + 1} {high + 1} {high + 5} {low + 5}) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)",
        f"    hex ({low + 1} {high + 1} {high + 5} {low + 5} {low + 2} {high + 2} {high + 6} {low + 6}) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)",
        f"    hex ({low + 2} {high + 2} {high + 6} {low + 6} {low + 3} {high + 3} {high + 7} {low + 7}) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)",
        f"    hex ({low + 3} {high + 3} {high + 7} {low + 7} {low} {high} {high + 4} {low + 4}) ({axial} {radial} {quadrant}) simpleGrading (1 1 1)",
    ]


def _section_faces(base: int, *, outlet: bool) -> list[str]:
    faces = (
        (
            (0, 3, 2, 1),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        )
        if not outlet
        else (
            (0, 1, 2, 3),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        )
    )
    return [
        "            (" + " ".join(str(base + item) for item in face) + ")"
        for face in faces
    ]


def block_mesh_dict(spec: CurvedElbowSpec) -> str:
    """Return the canonical 15-block all-hex OpenFOAM ``blockMeshDict``."""

    cross_vertices = _cross_vertices(spec)
    vertices = [
        _station_point(spec, station, u, v)
        for station in range(4)
        for u, v in cross_vertices
    ]
    blocks: list[str] = []
    for low, high, axial in (
        (0, 8, spec.inlet_axial_cells),
        (8, 16, spec.bend_axial_cells),
        (16, 24, spec.outlet_axial_cells),
    ):
        blocks.extend(
            _segment_blocks(
                low,
                high,
                axial=axial,
                radial=spec.annular_radial_cells,
                quadrant=spec.circumferential_cells_per_quadrant,
                core=spec.core_cells_per_side,
            )
        )

    edges: list[str] = []
    for station in range(4):
        base = station * 8
        for quadrant in range(4):
            start = 4 + quadrant
            end = 4 + ((quadrant + 1) % 4)
            phi = (quadrant + 0.5) * math.pi / 2.0
            midpoint = _station_point(
                spec,
                station,
                spec.radius_m * math.cos(phi),
                spec.radius_m * math.sin(phi),
            )
            edges.append(
                f"    arc {base + start} {base + end} {_format_point(midpoint)}"
            )
    for index, (u, v) in enumerate(cross_vertices):
        midpoint = _bend_point(spec, -math.pi / 4.0, u, v)
        edges.append(f"    arc {8 + index} {16 + index} {_format_point(midpoint)}")

    wall_faces: list[str] = []
    for low, high in ((0, 8), (8, 16), (16, 24)):
        wall_faces.extend(
            [
                f"            ({low + 4} {high + 4} {high + 5} {low + 5})",
                f"            ({low + 5} {high + 5} {high + 6} {low + 6})",
                f"            ({low + 6} {high + 6} {high + 7} {low + 7})",
                f"            ({low + 7} {high + 7} {high + 4} {low + 4})",
            ]
        )

    return f"""/* FlowLab bounded canonical 90-degree circular elbow O-grid */
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
{chr(10).join(f"    {line}" for line in map(_format_point, vertices))}
);

blocks
(
{chr(10).join(blocks)}
);

edges
(
{chr(10).join(edges)}
);

boundary
(
    inlet
    {{
        type patch;
        faces
        (
{chr(10).join(_section_faces(0, outlet=False))}
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
{chr(10).join(_section_faces(24, outlet=True))}
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


def _axial_planes(
    spec: CurvedElbowSpec,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return centre and in-plane normal for every logical cross-section."""

    planes: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index in range(spec.inlet_axial_cells + 1):
        fraction = index / spec.inlet_axial_cells
        planes.append(((spec.inlet_leg_m * fraction, 0.0), (0.0, 1.0)))
    for index in range(1, spec.bend_axial_cells + 1):
        fraction = index / spec.bend_axial_cells
        theta = -math.pi / 2.0 + fraction * math.pi / 2.0
        centre = (
            spec.inlet_leg_m + spec.centreline_radius_m * math.cos(theta),
            spec.centreline_radius_m + spec.centreline_radius_m * math.sin(theta),
        )
        normal = (-math.cos(theta), -math.sin(theta))
        planes.append((centre, normal))
    outlet_start_y = spec.centreline_radius_m
    for index in range(1, spec.outlet_axial_cells + 1):
        fraction = index / spec.outlet_axial_cells
        planes.append(
            (
                (
                    spec.inlet_leg_m + spec.centreline_radius_m,
                    outlet_start_y + spec.outlet_leg_m * fraction,
                ),
                (-1.0, 0.0),
            )
        )
    return planes


def preview_mesh(
    spec: CurvedElbowSpec,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a genuine three-dimensional hexahedral elbow inspection mesh."""

    straight_spec = FullOGridSpec(
        length_m=1.0,
        radius_m=spec.radius_m,
        axial_cells=4,
        annular_radial_cells=spec.annular_radial_cells,
        circumferential_cells=spec.circumferential_cells,
        core_cells_per_side=spec.core_cells_per_side,
    )
    cross_points, cross_cells, cross_areas = _cross_section(straight_spec)
    planes = _axial_planes(spec)
    points: list[list[float]] = []
    for centre, normal in planes:
        points.extend(
            [
                [
                    centre[0] + u * normal[0],
                    centre[1] + u * normal[1],
                    v,
                ]
                for u, v in cross_points
            ]
        )

    points_per_plane = len(cross_points)
    cells: list[list[int]] = []
    volumes: list[float] = []
    component_steps = (
        (
            0,
            spec.inlet_axial_cells,
            spec.inlet_leg_m / spec.inlet_axial_cells,
            "straight",
        ),
        (
            spec.inlet_axial_cells,
            spec.bend_axial_cells,
            (math.pi / 2.0) / spec.bend_axial_cells,
            "bend",
        ),
        (
            spec.inlet_axial_cells + spec.bend_axial_cells,
            spec.outlet_axial_cells,
            spec.outlet_leg_m / spec.outlet_axial_cells,
            "straight",
        ),
    )
    for plane_start, axial_cells, step, kind in component_steps:
        for local_axial in range(axial_cells):
            low_offset = (plane_start + local_axial) * points_per_plane
            high_offset = (plane_start + local_axial + 1) * points_per_plane
            for cross_cell, area in zip(cross_cells, cross_areas, strict=True):
                cells.append(
                    [
                        *(low_offset + point for point in cross_cell),
                        *(high_offset + point for point in cross_cell),
                    ]
                )
                if kind == "bend":
                    mean_u = sum(cross_points[index][0] for index in cross_cell) / 4.0
                    volumes.append(area * (spec.centreline_radius_m - mean_u) * step)
                else:
                    volumes.append(area * step)

    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    if (
        len(cells) != spec.cell_count
        or len(volumes) != spec.cell_count
        or any(not math.isfinite(volume) or volume <= 0.0 for volume in volumes)
    ):
        raise ValueError("curved elbow preview failed its positive-volume contract.")
    edge_ids = (
        profile.get("pathEdgeIds")
        if isinstance(profile, dict) and isinstance(profile.get("pathEdgeIds"), list)
        else []
    )
    edge_id = str(edge_ids[0]) if len(edge_ids) == 1 else "canonical-elbow"
    return {
        "format": CURVED_ELBOW_PREVIEW_FORMAT,
        "coordinateSystem": "physical-x-y-z-si",
        "spatialDimension": 3,
        "representation": "pre-solve-blockMesh-equivalent-curved-elbow-ogrid",
        "runtimeSolverMesh": False,
        "proxyGeometry": False,
        "requiresExplicitSourceCellProvenance": True,
        "profileSchema": None if profile is None else profile.get("schema"),
        "boundsSpanM": [round(value, 12) for value in spans],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_HEXAHEDRON for _ in cells],
        "regions": spec.component_regions(edge_id),
        "topology": spec.topology_manifest(),
        "geometryAudit": {
            "diameterM": spec.diameter_m,
            "centrelineRadiusM": spec.centreline_radius_m,
            "bendAngleDegrees": spec.bend_angle_degrees,
            "inletLegM": spec.inlet_leg_m,
            "outletLegM": spec.outlet_leg_m,
            "centrelineRadiusOverDiameter": spec.centreline_radius_over_diameter,
            "inletLegOverDiameter": spec.inlet_leg_over_diameter,
            "outletLegOverDiameter": spec.outlet_leg_over_diameter,
            "analyticFluidVolumeM3": spec.analytic_fluid_volume_m3,
        },
        "volumeQuality": {
            "positiveVolume": True,
            "zeroVolumeCellCount": 0,
            "minimumCellVolumeM3": min(volumes),
            "maximumCellVolumeM3": max(volumes),
            "totalCellVolumeM3": sum(volumes),
        },
    }
