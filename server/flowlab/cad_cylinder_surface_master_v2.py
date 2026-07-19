#!/usr/bin/env python3
"""Create the versioned v2, patch-tagged cylinder-surface master.

Unlike :mod:`cad_cylinder_surface_master`, which remains the immutable v1
master, v2 uses concentric cap rings and an axial spacing derived from the
outer wall chord.  That removes v1's centre-fan cap triangles and elongated
wall strips before any volume meshing is attempted.

The result deliberately contains only MSH2 boundary triangles.  It is a new
geometry contract: volume tooling must preserve its coordinates, triangles,
and physical IDs exactly after this module freezes its fingerprint.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .gmsh_immutable_surface_probe import msh2_surface_fingerprint


INLET_PHYSICAL_ID = 11
OUTLET_PHYSICAL_ID = 12
WALL_PHYSICAL_ID = 13
FLUID_PHYSICAL_ID = 1
SCHEMA = "flowlab.cad-cylinder-surface-master.v2"


@dataclass(frozen=True)
class SurfaceQualityTargets:
    """Explicit geometric acceptance limits for the v2 frozen surface.

    ``max_*_edge_ratio`` is longest triangle edge divided by shortest edge.
    An equilateral triangle is one; a right isosceles triangle is sqrt(2).
    The defaults permit the intentional wall diagonals while rejecting the
    centre-fan and elongated-wall patterns that made v1 unsuitable here.
    """

    max_wall_edge_ratio: float = 1.5
    max_cap_edge_ratio: float = 2.0
    max_relative_cylinder_area_error: float = 2.0e-4

    def validate(self) -> None:
        values = (
            self.max_wall_edge_ratio,
            self.max_cap_edge_ratio,
            self.max_relative_cylinder_area_error,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("surface quality targets must be finite and positive")
        if self.max_wall_edge_ratio < math.sqrt(2.0):
            raise ValueError("max_wall_edge_ratio must allow a triangulated rectangular wall")


@dataclass(frozen=True)
class SurfaceQualityReport:
    schema: str
    targets: SurfaceQualityTargets
    maximum_wall_edge_ratio: float
    maximum_cap_edge_ratio: float
    relative_cylinder_area_error: float
    accepted: bool
    failures: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        result = asdict(self)
        result["targets"] = asdict(self.targets)
        result["failures"] = list(self.failures)
        return result


def outer_wall_chord_length(radius_m: float, circumferential_chords: int) -> float:
    """Return the side length of one outer regular-polygon chord."""

    return 2.0 * radius_m * math.sin(math.pi / circumferential_chords)


def recommended_axial_cells(
    *, length_m: float, radius_m: float, circumferential_chords: int
) -> int:
    """Choose axial cells so a wall triangle has near-isotropic legs."""

    return max(1, round(length_m / outer_wall_chord_length(radius_m, circumferential_chords)))


def recommended_cap_ring_count(*, radius_m: float, circumferential_chords: int) -> int:
    """Choose rings with radial spacing comparable to the outer chord.

    Eight sectors per ring are used by the deterministic zipper triangulator,
    so the count is bounded at 32 to retain an exact 256-sector outer ring.
    """

    chord = outer_wall_chord_length(radius_m, circumferential_chords)
    return max(1, min(circumferential_chords // 8, round(radius_m / chord)))


def _validate_geometry(
    *, length_m: float, radius_m: float, circumferential_chords: int, axial_cells: int, cap_ring_count: int
) -> None:
    if not math.isfinite(length_m) or not math.isfinite(radius_m) or length_m <= 0.0 or radius_m <= 0.0:
        raise ValueError("length_m and radius_m must be finite and positive")
    if circumferential_chords < 8 or circumferential_chords % 8:
        raise ValueError("circumferential_chords must be a multiple of eight and at least eight")
    if axial_cells < 1 or cap_ring_count < 1:
        raise ValueError("axial_cells and cap_ring_count must be positive")
    if cap_ring_count > circumferential_chords // 8:
        raise ValueError("cap_ring_count exceeds available eight-sector cap topology")


def _append_ring(
    nodes: list[tuple[float, float, float]], *, radius_m: float, z_m: float, count: int
) -> list[int]:
    tags: list[int] = []
    for index in range(count):
        theta = 2.0 * math.pi * index / count
        nodes.append((radius_m * math.cos(theta), radius_m * math.sin(theta), z_m))
        tags.append(len(nodes))
    return tags


def _append_annulus_triangles(
    elements: list[tuple[int, tuple[int, int, int]]], *, physical: int,
    inner: list[int], outer: list[int],
) -> None:
    """Stitch two uniform rings with a deterministic angular zipper.

    The rings share their first angular position.  Moving the next edge with
    the smaller normalized angle produces exactly ``len(inner)+len(outer)``
    triangles without inserting any interior points or remeshing an outer
    boundary edge.
    """

    inner_count, outer_count = len(inner), len(outer)
    inner_index = outer_index = 0
    while inner_index < inner_count or outer_index < outer_count:
        next_inner = (inner_index + 1) / inner_count
        next_outer = (outer_index + 1) / outer_count
        current_inner = inner[inner_index % inner_count]
        current_outer = outer[outer_index % outer_count]
        if math.isclose(next_inner, next_outer, rel_tol=0.0, abs_tol=1.0e-15):
            elements.extend(
                (
                    (physical, (current_inner, inner[(inner_index + 1) % inner_count], current_outer)),
                    (physical, (inner[(inner_index + 1) % inner_count], outer[(outer_index + 1) % outer_count], current_outer)),
                )
            )
            inner_index += 1
            outer_index += 1
        elif next_inner < next_outer:
            elements.append((physical, (current_inner, inner[(inner_index + 1) % inner_count], current_outer)))
            inner_index += 1
        else:
            elements.append((physical, (current_inner, outer[(outer_index + 1) % outer_count], current_outer)))
            outer_index += 1


def _triangle_edge_ratio(points: Iterable[tuple[float, float, float]]) -> float:
    triangle = list(points)
    lengths = []
    for index in range(3):
        first, second = triangle[index], triangle[(index + 1) % 3]
        lengths.append(math.dist(first, second))
    return max(lengths) / min(lengths)


def _oriented_surface_edge_failures(elements: Iterable[tuple[int, tuple[int, int, int]]]) -> int:
    """Count edges which do not have exactly one triangle on each side.

    A closed two-manifold needs two incidences for every undirected edge, with
    opposite directions.  The v2 quality gate enforces this before freezing
    the master, since Gmsh volume meshing requires a consistently oriented
    closed shell rather than merely an undirected watertight graph.
    """

    directions: Counter[tuple[int, int]] = Counter()
    for _, triangle in elements:
        for first, second in zip(triangle, triangle[1:] + triangle[:1]):
            directions[(first, second)] += 1
    undirected = {tuple(sorted(edge)) for edge in directions}
    return sum(
        directions[(first, second)] != 1 or directions[(second, first)] != 1
        for first, second in undirected
    )


def evaluate_surface_quality(
    *, nodes: list[tuple[float, float, float]], elements: list[tuple[int, tuple[int, int, int]]],
    length_m: float, radius_m: float, targets: SurfaceQualityTargets,
) -> SurfaceQualityReport:
    """Evaluate the explicit v2 surface-quality acceptance contract."""

    targets.validate()
    maximums: defaultdict[int, float] = defaultdict(float)
    areas: Counter[int] = Counter()
    area_sums: defaultdict[int, float] = defaultdict(float)
    for physical, triangle in elements:
        xyz = [nodes[tag - 1] for tag in triangle]
        maximums[physical] = max(maximums[physical], _triangle_edge_ratio(xyz))
        a, b, c = xyz
        cross = (
            (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
            (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
            (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
        )
        area_sums[physical] += 0.5 * math.sqrt(sum(value * value for value in cross))
        areas[physical] += 1
    if not all(areas[physical] for physical in (INLET_PHYSICAL_ID, OUTLET_PHYSICAL_ID, WALL_PHYSICAL_ID)):
        raise ValueError("surface must contain inlet, outlet, and wall triangles")
    exact_area = 2.0 * math.pi * radius_m * length_m + 2.0 * math.pi * radius_m**2
    actual_area = sum(area_sums.values())
    relative_area_error = abs(actual_area - exact_area) / exact_area
    failures: list[str] = []
    if maximums[WALL_PHYSICAL_ID] > targets.max_wall_edge_ratio:
        failures.append("wall triangle edge ratio exceeds target")
    cap_ratio = max(maximums[INLET_PHYSICAL_ID], maximums[OUTLET_PHYSICAL_ID])
    if cap_ratio > targets.max_cap_edge_ratio:
        failures.append("cap triangle edge ratio exceeds target")
    if relative_area_error > targets.max_relative_cylinder_area_error:
        failures.append("relative cylinder surface area error exceeds target")
    if _oriented_surface_edge_failures(elements):
        failures.append("surface is not a consistently oriented closed two-manifold")
    return SurfaceQualityReport(
        schema=SCHEMA,
        targets=targets,
        maximum_wall_edge_ratio=maximums[WALL_PHYSICAL_ID],
        maximum_cap_edge_ratio=cap_ratio,
        relative_cylinder_area_error=relative_area_error,
        accepted=not failures,
        failures=tuple(failures),
    )


def write_cylinder_surface_master_v2(
    output: Path,
    *,
    length_m: float,
    radius_m: float,
    circumferential_chords: int,
    axial_cells: int | None = None,
    cap_ring_count: int | None = None,
    quality_targets: SurfaceQualityTargets = SurfaceQualityTargets(),
) -> SurfaceQualityReport:
    """Write a deterministic MSH2 v2 master and return its quality report.

    The file is written only after it passes the supplied surface-quality
    contract.  Passing an explicit elongated axial resolution therefore
    fails fast instead of freezing another unsuitable boundary master.
    """

    if axial_cells is None:
        axial_cells = recommended_axial_cells(
            length_m=length_m, radius_m=radius_m, circumferential_chords=circumferential_chords
        )
    if cap_ring_count is None:
        cap_ring_count = recommended_cap_ring_count(
            radius_m=radius_m, circumferential_chords=circumferential_chords
        )
    _validate_geometry(
        length_m=length_m, radius_m=radius_m, circumferential_chords=circumferential_chords,
        axial_cells=axial_cells, cap_ring_count=cap_ring_count,
    )
    quality_targets.validate()

    nodes: list[tuple[float, float, float]] = []
    wall_rings = [
        _append_ring(nodes, radius_m=radius_m, z_m=length_m * index / axial_cells, count=circumferential_chords)
        for index in range(axial_cells + 1)
    ]
    elements: list[tuple[int, tuple[int, int, int]]] = []
    for axial_index in range(axial_cells):
        lower, upper = wall_rings[axial_index], wall_rings[axial_index + 1]
        for chord_index in range(circumferential_chords):
            next_index = (chord_index + 1) % circumferential_chords
            elements.extend(
                (
                    (WALL_PHYSICAL_ID, (lower[chord_index], lower[next_index], upper[next_index])),
                    (WALL_PHYSICAL_ID, (lower[chord_index], upper[next_index], upper[chord_index])),
                )
            )

    for z_m, physical, outer_ring, reverse in (
        (0.0, INLET_PHYSICAL_ID, wall_rings[0], True),
        (length_m, OUTLET_PHYSICAL_ID, wall_rings[-1], False),
    ):
        center_tag = len(nodes) + 1
        nodes.append((0.0, 0.0, z_m))
        cap_rings: list[list[int]] = []
        base_sectors = circumferential_chords // cap_ring_count
        if base_sectors < 8:
            raise ValueError("cap topology needs at least eight sectors in its first ring")
        for ring_index in range(1, cap_ring_count):
            cap_rings.append(
                _append_ring(
                    nodes,
                    radius_m=radius_m * ring_index / cap_ring_count,
                    z_m=z_m,
                    count=base_sectors * ring_index,
                )
            )
        rings = cap_rings + [outer_ring]
        first_ring = rings[0]
        for index in range(len(first_ring)):
            next_index = (index + 1) % len(first_ring)
            triangle = (center_tag, first_ring[next_index], first_ring[index]) if reverse else (center_tag, first_ring[index], first_ring[next_index])
            elements.append((physical, triangle))
        for inner, outer in zip(rings, rings[1:]):
            before = len(elements)
            _append_annulus_triangles(elements, physical=physical, inner=inner, outer=outer)
            # The zipper's native orientation points toward -z.  The inlet
            # cap is already outward in that orientation; only the outlet
            # annuli need reversal.  This must agree with the centre fan and
            # the wall along every rim edge for Gmsh to recognise a volume.
            if not reverse:
                for element_index in range(before, len(elements)):
                    tag, triangle = elements[element_index]
                    elements[element_index] = (tag, (triangle[0], triangle[2], triangle[1]))

    report = evaluate_surface_quality(
        nodes=nodes, elements=elements, length_m=length_m, radius_m=radius_m, targets=quality_targets
    )
    if not report.accepted:
        raise ValueError("v2 surface quality targets failed: " + "; ".join(report.failures))
    lines = [
        "$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", "4",
        '2 11 "inlet"', '2 12 "outlet"', '2 13 "wall"', '3 1 "fluid"',
        "$EndPhysicalNames", "$Nodes", str(len(nodes)),
    ]
    lines.extend(f"{index} {x:.17g} {y:.17g} {z:.17g}" for index, (x, y, z) in enumerate(nodes, 1))
    lines.extend(("$EndNodes", "$Elements", str(len(elements))))
    lines.extend(
        f"{index} 2 2 {physical} {physical} {a} {b} {c}"
        for index, (physical, (a, b, c)) in enumerate(elements, 1)
    )
    lines.append("$EndElements")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def freeze_report(output: Path, quality: SurfaceQualityReport) -> dict[str, object]:
    """Return the deterministic identity/quality report to retain with v2."""

    return {
        "schema": SCHEMA,
        "master": str(output),
        "fingerprint": msh2_surface_fingerprint(output),
        "quality": quality.as_json(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--length-m", type=float, default=0.05)
    parser.add_argument("--radius-m", type=float, default=0.005)
    parser.add_argument("--circumferential-chords", type=int, default=256)
    parser.add_argument("--axial-cells", type=int)
    parser.add_argument("--cap-ring-count", type=int)
    parser.add_argument("--max-wall-edge-ratio", type=float, default=1.5)
    parser.add_argument("--max-cap-edge-ratio", type=float, default=2.0)
    parser.add_argument("--max-relative-cylinder-area-error", type=float, default=2.0e-4)
    args = parser.parse_args()
    targets = SurfaceQualityTargets(
        max_wall_edge_ratio=args.max_wall_edge_ratio,
        max_cap_edge_ratio=args.max_cap_edge_ratio,
        max_relative_cylinder_area_error=args.max_relative_cylinder_area_error,
    )
    quality = write_cylinder_surface_master_v2(
        args.output,
        length_m=args.length_m,
        radius_m=args.radius_m,
        circumferential_chords=args.circumferential_chords,
        axial_cells=args.axial_cells,
        cap_ring_count=args.cap_ring_count,
        quality_targets=targets,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(freeze_report(args.output, quality), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
