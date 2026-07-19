#!/usr/bin/env python3
"""Tetrahedralize an imported MSH2 surface without permitting boundary remeshing.

This is an experimental acceptance probe for the CAD-cylinder geometry gate.
It intentionally accepts a pre-existing physical-tagged closed surface mesh,
creates an explicit discrete volume around its imported surface entities, and
then requires the exported boundary triangles to be byte-identical under a
coordinate/physical-tag canonicalization.  It is not a CFD runner and must
not promote an image or a mesh family on its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _section(lines: list[str], marker: str) -> list[str]:
    start = lines.index(marker) + 1
    return lines[start : lines.index("$End" + marker[1:], start)]


def _coordinate_key(value: float) -> str:
    return (0.0 if value == 0.0 else value).hex()


def _cyclic_triangle_key(vertices: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, str, str], ...]:
    """Canonicalize a directed triangle up to cyclic rotation, not reversal."""

    return min(vertices[index:] + vertices[:index] for index in range(3))


def msh2_surface_fingerprint(path: Path) -> dict[str, Any]:
    """Return geometric and orientation-sensitive patch-aware surface identities."""

    lines = path.read_text(encoding="utf-8").splitlines()
    mesh_format = _section(lines, "$MeshFormat")[0].split()
    if mesh_format[:2] != ["2.2", "0"]:
        raise ValueError(f"{path}: expected ASCII MSH2, got {mesh_format!r}")
    node_rows = _section(lines, "$Nodes")
    nodes = {
        int(row.split()[0]): tuple(float(value) for value in row.split()[1:4])
        for row in node_rows[1:]
    }
    records: list[tuple[int, tuple[tuple[str, str, str], ...]]] = []
    oriented_records: list[tuple[int, tuple[tuple[str, str, str], ...]]] = []
    triangles_by_physical: Counter[int] = Counter()
    area_by_physical: defaultdict[int, float] = defaultdict(float)
    for row in _section(lines, "$Elements")[1:]:
        fields = [int(value) for value in row.split()]
        element_type, tag_count = fields[1], fields[2]
        if element_type != 2:
            continue
        physical = fields[3] if tag_count else 0
        vertex_ids = fields[3 + tag_count :]
        if len(vertex_ids) != 3:
            raise ValueError(f"{path}: triangle has {len(vertex_ids)} vertices")
        xyz = [nodes[node_id] for node_id in vertex_ids]
        directed_vertices = tuple(tuple(_coordinate_key(value) for value in point) for point in xyz)
        vertices = tuple(sorted(directed_vertices))
        records.append((physical, vertices))
        oriented_records.append((physical, _cyclic_triangle_key(directed_vertices)))
        triangles_by_physical[physical] += 1
        a, b, c = xyz
        cross = (
            (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
            (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
            (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
        )
        area_by_physical[physical] += 0.5 * math.sqrt(sum(value * value for value in cross))
    canonical = "\n".join(
        f"{physical}:" + ";".join(",".join(point) for point in vertices)
        for physical, vertices in sorted(records)
    )
    oriented_canonical = "\n".join(
        f"{physical}:" + ";".join(",".join(point) for point in vertices)
        for physical, vertices in sorted(oriented_records)
    )
    return {
        "surfaceSha256": hashlib.sha256(canonical.encode("ascii")).hexdigest(),
        "orientedSurfaceSha256": hashlib.sha256(oriented_canonical.encode("ascii")).hexdigest(),
        "surfaceTriangles": len(records),
        "trianglesByPhysicalId": {str(key): value for key, value in sorted(triangles_by_physical.items())},
        "surfaceAreaByPhysicalId": {str(key): value for key, value in sorted(area_by_physical.items())},
    }


def tetrahedron_count(path: Path) -> int:
    return sum(
        1
        for row in _section(path.read_text(encoding="utf-8").splitlines(), "$Elements")[1:]
        if row.split()[1] == "4"
    )


def run_probe(surface_msh: Path, output_msh: Path, interior_size_m: float) -> dict[str, Any]:
    import gmsh  # Loaded only inside the pinned candidate runtime.

    if interior_size_m <= 0.0 or not math.isfinite(interior_size_m):
        raise ValueError("interior_size_m must be finite and positive")
    before = msh2_surface_fingerprint(surface_msh)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", interior_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", interior_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 0)
        gmsh.model.add("flowlab_immutable_surface_probe")
        gmsh.merge(str(surface_msh))
        surface_tags = [tag for dimension, tag in gmsh.model.getEntities(2) if dimension == 2]
        if not surface_tags:
            raise RuntimeError("input contains no discrete surface entities")
        # The imported 2-D elements are the immutable PLC boundary.  Creating
        # the volume explicitly is the operation missing from the failed
        # command-line `Merge; CreateTopology; Mesh 3` experiment.
        surface_loop = gmsh.model.geo.addSurfaceLoop(surface_tags)
        volume_tag = gmsh.model.geo.addVolume([surface_loop])
        gmsh.model.geo.synchronize()
        # The imported MSH2 has a named but entity-less physical volume.  MSH2
        # omits unphysical volume elements by default, so replace that empty
        # group with the explicit discrete volume before exporting.
        for dimension, tag in gmsh.model.getPhysicalGroups(3):
            gmsh.model.removePhysicalGroups([(dimension, tag)])
        gmsh.model.addPhysicalGroup(3, [volume_tag], 1, "fluid")
        gmsh.model.mesh.generate(3)
        gmsh.write(str(output_msh))
    finally:
        gmsh.finalize()
    after = msh2_surface_fingerprint(output_msh)
    tets = tetrahedron_count(output_msh)
    report = {
        "schema": "flowlab.gmsh-immutable-surface-probe.v1",
        "input": str(surface_msh),
        "output": str(output_msh),
        "interiorSizeM": interior_size_m,
        "before": before,
        "after": after,
        "tetrahedra": tets,
        "surfaceHashEqual": before["surfaceSha256"] == after["surfaceSha256"],
        "surfaceTriangleCountEqual": before["surfaceTriangles"] == after["surfaceTriangles"],
        "accepted": bool(
            tets > 0
            and before["surfaceSha256"] == after["surfaceSha256"]
            and before["surfaceTriangles"] == after["surfaceTriangles"]
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interior-size-m", type=float, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = run_probe(args.surface, args.output, args.interior_size_m)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
