#!/usr/bin/env python3
"""Create a patch-tagged, immutable MSH2 cylinder-surface master.

The master contains only boundary triangles.  A separate volume mesher must
preserve these coordinates, triangles, and physical patch identifiers exactly.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def write_cylinder_surface_master(
    output: Path,
    *,
    length_m: float,
    radius_m: float,
    circumferential_chords: int,
    axial_cells: int,
) -> None:
    if length_m <= 0 or radius_m <= 0:
        raise ValueError("length_m and radius_m must be positive")
    if circumferential_chords < 3 or axial_cells < 1:
        raise ValueError("need at least 3 circumferential chords and one axial cell")
    nodes: list[tuple[float, float, float]] = []
    for axial_index in range(axial_cells + 1):
        z = length_m * axial_index / axial_cells
        for chord_index in range(circumferential_chords):
            theta = 2.0 * math.pi * chord_index / circumferential_chords
            nodes.append((radius_m * math.cos(theta), radius_m * math.sin(theta), z))
    inlet_center = len(nodes) + 1
    nodes.append((0.0, 0.0, 0.0))
    outlet_center = len(nodes) + 1
    nodes.append((0.0, 0.0, length_m))

    elements: list[tuple[int, tuple[int, int, int]]] = []
    # MSH2 physical ids: inlet=11, outlet=12, wall=13.  Connectivity is
    # deliberately stable; orientation is immaterial to the identity hash.
    for axial_index in range(axial_cells):
        lower = axial_index * circumferential_chords
        upper = (axial_index + 1) * circumferential_chords
        for chord_index in range(circumferential_chords):
            next_index = (chord_index + 1) % circumferential_chords
            a, b = lower + chord_index + 1, lower + next_index + 1
            c, d = upper + chord_index + 1, upper + next_index + 1
            elements.extend(((13, (a, b, d)), (13, (a, d, c))))
    for chord_index in range(circumferential_chords):
        next_index = (chord_index + 1) % circumferential_chords
        inlet_a, inlet_b = chord_index + 1, next_index + 1
        outlet_a = axial_cells * circumferential_chords + chord_index + 1
        outlet_b = axial_cells * circumferential_chords + next_index + 1
        elements.extend(((11, (inlet_center, inlet_b, inlet_a)), (12, (outlet_center, outlet_a, outlet_b))))

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--length-m", type=float, default=0.05)
    parser.add_argument("--radius-m", type=float, default=0.005)
    parser.add_argument("--circumferential-chords", type=int, default=256)
    parser.add_argument("--axial-cells", type=int, default=40)
    args = parser.parse_args()
    write_cylinder_surface_master(
        args.output,
        length_m=args.length_m,
        radius_m=args.radius_m,
        circumferential_chords=args.circumferential_chords,
        axial_cells=args.axial_cells,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
