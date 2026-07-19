#!/usr/bin/env python3
"""Build an experimental Gmsh-core volume behind an immutable MSH2 surface.

The outer MSH2 surface is copied verbatim into the output.  This module only
adds a conformal inward tetrahedral transition shell and a Gmsh-meshed core.
It is deliberately cylinder-specific: the immutable surface contract in this
campaign is a known straight circular pipe, not arbitrary imported CAD.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from server.flowlab.cad_cylinder_surface_master_v2 import SurfaceQualityTargets, write_cylinder_surface_master_v2
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint, tetrahedron_count


REQUIRED_PATCHES = {11: "inlet", 12: "outlet", 13: "wall"}
# Gmsh 4.15.2 on the supported ARM64 functional runtime deterministically
# segfaults in mesh.generate(3) when Algorithm3D=1 is applied to the two-shell
# discrete annular transition.  Algorithm 4 is independently demonstrated on
# both 128- and 192-chord interfaces.  Keep this a hard contract instead of a
# campaign convention so an invalid control cannot silently reappear.
ANNULAR_TRANSITION_ALGORITHM = 4


class GmshCoreMeshingError(RuntimeError):
    """A core-meshing failure which retains Gmsh's diagnostic log."""

    def __init__(self, message: str, gmsh_log: list[str]) -> None:
        super().__init__(message)
        self.gmsh_log = gmsh_log


@dataclass(frozen=True)
class SurfaceTriangle:
    physical_id: int
    vertices: tuple[int, int, int]


@dataclass(frozen=True)
class FrozenSurface:
    nodes: dict[int, tuple[float, float, float]]
    triangles: tuple[SurfaceTriangle, ...]
    physical_names: dict[int, str]


@dataclass(frozen=True)
class LayeredVolumeConfig:
    first_layer_m: float
    layer_count: int
    growth_ratio: float
    core_size_m: float
    algorithm_3d: int = 4
    optimize_netgen: bool = True
    smoothing_steps: int = 20
    # ``None`` preserves the original, full-density inner interface.  An
    # explicit pair selects the experimental two-region path: the dense prism
    # shell transitions to a coarsened *nonphysical* cylindrical interface,
    # then Gmsh meshes the cavity behind it.  Both values are deliberately
    # explicit so a screen cannot silently change its internal strategy.
    core_interface_chords: int | None = None
    transition_thickness_m: float | None = None
    # A topology name is not inferred from numerical controls.  This makes it
    # possible to compare two internal discretizations behind exactly the same
    # frozen surface without later confusing their retained evidence.
    volume_strategy_id: str | None = None
    # The outer-surface contract is unchanged between internal strategies.
    # Carry a separately declared version so downstream evidence can prove
    # which internal-volume policy actually generated a mesh.
    volume_strategy_version: str = "v1"

    def __post_init__(self) -> None:
        if self.first_layer_m <= 0 or self.core_size_m <= 0:
            raise ValueError("first_layer_m and core_size_m must be positive")
        if self.layer_count < 1:
            raise ValueError("layer_count must be at least one")
        if self.growth_ratio < 1:
            raise ValueError("growth_ratio must be at least one")
        if (self.core_interface_chords is None) != (self.transition_thickness_m is None):
            raise ValueError("coarsened core interface requires both chords and transition thickness")
        if self.core_interface_chords is not None:
            if self.core_interface_chords < 16 or self.core_interface_chords % 8:
                raise ValueError("coarsened core-interface chords must be a multiple of eight and at least sixteen")
            if self.transition_thickness_m is None or self.transition_thickness_m <= 0:
                raise ValueError("coarsened core-interface transition thickness must be positive")
            if self.algorithm_3d != ANNULAR_TRANSITION_ALGORITHM:
                raise ValueError(
                    "coarsened annular transition requires "
                    f"Algorithm3D={ANNULAR_TRANSITION_ALGORITHM}; Algorithm3D=1 is permanently rejected"
                )
        if not self.volume_strategy_version.strip():
            raise ValueError("volume strategy version must be non-empty")
        if self.volume_strategy_id is not None and not self.volume_strategy_id.strip():
            raise ValueError("volume strategy id must be non-empty when declared")

    @property
    def shell_thickness_m(self) -> float:
        return self.first_layer_m * sum(self.growth_ratio**index for index in range(self.layer_count))

    @property
    def uses_coarsened_core_interface(self) -> bool:
        return self.core_interface_chords is not None


def _section(lines: list[str], marker: str) -> list[str]:
    start = lines.index(marker) + 1
    return lines[start : lines.index("$End" + marker[1:], start)]


def read_frozen_surface(path: Path) -> FrozenSurface:
    """Read the strict ASCII-MSH2 physical surface contract."""

    lines = path.read_text(encoding="utf-8").splitlines()
    mesh_format = _section(lines, "$MeshFormat")[0].split()
    if mesh_format[:2] != ["2.2", "0"]:
        raise ValueError(f"{path}: expected ASCII MSH2")
    names: dict[int, str] = {}
    if "$PhysicalNames" in lines:
        for row in _section(lines, "$PhysicalNames")[1:]:
            dimension, tag, name = row.split(maxsplit=2)
            if dimension == "2":
                names[int(tag)] = name.strip().strip('"')
    nodes = {
        int(row.split()[0]): tuple(float(value) for value in row.split()[1:4])
        for row in _section(lines, "$Nodes")[1:]
    }
    triangles: list[SurfaceTriangle] = []
    for row in _section(lines, "$Elements")[1:]:
        fields = [int(value) for value in row.split()]
        if fields[1] != 2:
            continue
        tag_count = fields[2]
        if tag_count < 1:
            raise ValueError("each outer triangle requires a physical tag")
        vertices = tuple(fields[3 + tag_count :])
        if len(vertices) != 3:
            raise ValueError("only linear triangular surfaces are supported")
        triangles.append(SurfaceTriangle(fields[3], vertices))
    surface = FrozenSurface(nodes=nodes, triangles=tuple(triangles), physical_names=names)
    _validate_surface_contract(surface)
    return surface


def _validate_surface_contract(surface: FrozenSurface) -> None:
    observed = {triangle.physical_id for triangle in surface.triangles}
    if observed != set(REQUIRED_PATCHES):
        raise ValueError(f"surface physical ids must be {sorted(REQUIRED_PATCHES)}, got {sorted(observed)}")
    if {tag: surface.physical_names.get(tag) for tag in REQUIRED_PATCHES} != REQUIRED_PATCHES:
        raise ValueError("surface must declare inlet/outlet/wall physical names")
    edge_counts: dict[tuple[int, int], int] = {}
    for triangle in surface.triangles:
        for a, b in zip(triangle.vertices, triangle.vertices[1:] + triangle.vertices[:1]):
            edge = tuple(sorted((a, b)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    bad_edges = [edge for edge, count in edge_counts.items() if count != 2]
    if bad_edges:
        raise ValueError(f"surface is not a closed two-manifold ({len(bad_edges)} bad edges)")


def _cylinder_dimensions(surface: FrozenSurface) -> tuple[float, float]:
    radii = [math.hypot(x, y) for x, y, _ in surface.nodes.values()]
    radius = max(radii)
    z_values = [point[2] for point in surface.nodes.values()]
    length = max(z_values) - min(z_values)
    if radius <= 0 or length <= 0:
        raise ValueError("surface is not a positive-radius, positive-length z-axis cylinder")
    return radius, length


def _layer_offsets(config: LayeredVolumeConfig) -> list[float]:
    values: list[float] = []
    total = 0.0
    for index in range(config.layer_count):
        total += config.first_layer_m * config.growth_ratio**index
        values.append(total)
    return values


def volume_strategy(config: LayeredVolumeConfig) -> dict[str, Any]:
    """Return the explicit internal topology identity for evidence binding."""

    if config.uses_coarsened_core_interface:
        return {
            "id": config.volume_strategy_id or "coarsened-inner-interface",
            "version": config.volume_strategy_version,
            "algorithm3D": config.algorithm_3d,
            "optimizeNetgen": config.optimize_netgen,
            "smoothingSteps": config.smoothing_steps,
            "interfaceChords": config.core_interface_chords,
            "transitionThicknessM": config.transition_thickness_m,
        }
    return {
        "id": config.volume_strategy_id or "full-density-shell-interface",
        "version": config.volume_strategy_version,
        "algorithm3D": config.algorithm_3d,
        "optimizeNetgen": config.optimize_netgen,
        "smoothingSteps": config.smoothing_steps,
    }


def _inner_coordinate(point: tuple[float, float, float], radius: float, length: float, offset: float) -> tuple[float, float, float]:
    x, y, z = point
    scale = (radius - offset) / radius
    return (x * scale, y * scale, offset + z * (length - 2.0 * offset) / length)


def build_transition_shell(surface: FrozenSurface, config: LayeredVolumeConfig) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, int, int, int, int, int]], dict[int, int]]:
    """Return added shell nodes, conformal prisms, and final-inner node mapping.

    Prisms preserve every frozen triangle as one exterior face and share
    unsplit quadrilateral faces across neighboring triangles.  The MSH2 prism
    ordering intentionally reverses the source triangle: the source master
    orientation is not guaranteed to be outward, while this order produces
    positive swept cells for the declared cylinder convention.
    """

    radius, length = _cylinder_dimensions(surface)
    if config.shell_thickness_m >= min(radius, length / 2.0):
        raise ValueError("shell thickness closes the inner cavity")
    if config.shell_thickness_m >= radius * 0.5:
        raise ValueError("shell thickness exceeds the bounded half-radius screen")
    node_ids = sorted(surface.nodes)
    added_nodes: dict[int, tuple[float, float, float]] = {}
    mappings: list[dict[int, int]] = [{node_id: node_id for node_id in node_ids}]
    next_tag = max(node_ids) + 1
    for offset in _layer_offsets(config):
        mapping: dict[int, int] = {}
        for node_id in node_ids:
            mapping[node_id] = next_tag
            added_nodes[next_tag] = _inner_coordinate(surface.nodes[node_id], radius, length, offset)
            next_tag += 1
        mappings.append(mapping)
    prisms: list[tuple[int, int, int, int, int, int]] = []
    for level in range(config.layer_count):
        old, new = mappings[level], mappings[level + 1]
        for triangle in surface.triangles:
            a, b, c = (old[vertex] for vertex in triangle.vertices)
            A, B, C = (new[vertex] for vertex in triangle.vertices)
            prisms.append((a, c, b, A, C, B))
    return added_nodes, prisms, mappings[-1]


def build_coarsened_core_interface(
    surface: FrozenSurface,
    config: LayeredVolumeConfig,
    *,
    first_node_tag: int,
    directory: Path,
) -> FrozenSurface:
    """Build a deterministic, nonphysical inner cylinder for the Gmsh core.

    This intentionally does *not* touch ``surface`` or attempt to coarsen any
    of its triangles.  Its only purpose is to give the core a small, controlled
    boundary independent of the frozen exterior resolution.  A v2-style ring
    topology keeps the internal caps away from the old arbitrary centre-fan
    surface master; its physical ids are discarded when the final fluid MSH2
    is written.
    """

    if not config.uses_coarsened_core_interface:
        raise ValueError("coarsened core interface was not requested")
    assert config.transition_thickness_m is not None
    assert config.core_interface_chords is not None
    radius, length = _cylinder_dimensions(surface)
    offset = config.shell_thickness_m + config.transition_thickness_m
    if offset >= min(radius, length / 2.0):
        raise ValueError("shell plus transition thickness closes the inner cavity")
    # Keep the campaign's conservative half-radius limit for every internal
    # interface, not just the swept prism shell.
    if offset >= radius * 0.5:
        raise ValueError("shell plus transition thickness exceeds the bounded half-radius screen")
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "coarsened-core-interface.msh"
    write_cylinder_surface_master_v2(
        raw,
        length_m=length - 2.0 * offset,
        radius_m=radius - offset,
        circumferential_chords=config.core_interface_chords,
        # The interface is intentionally much coarser than the frozen outer
        # surface.  It retains the v2 ring topology and edge-shape limits, but
        # an outer-cylinder area tolerance is not meaningful for this
        # nonphysical polygonal cavity boundary.
        quality_targets=SurfaceQualityTargets(max_relative_cylinder_area_error=1.0e-2),
    )
    generated = read_frozen_surface(raw)
    z_min = min(point[2] for point in surface.nodes.values())
    remap = {tag: first_node_tag + index for index, tag in enumerate(sorted(generated.nodes))}
    nodes = {
        remap[tag]: (point[0], point[1], point[2] + z_min + offset)
        for tag, point in generated.nodes.items()
    }
    triangles = tuple(
        SurfaceTriangle(triangle.physical_id, tuple(remap[node] for node in triangle.vertices))
        for triangle in generated.triangles
    )
    interface = FrozenSurface(nodes=nodes, triangles=triangles, physical_names=dict(generated.physical_names))
    _validate_surface_contract(interface)
    return interface


def _write_transition_region_msh(
    path: Path,
    *,
    outer_nodes: dict[int, tuple[float, float, float]],
    outer_triangles: Iterable[SurfaceTriangle],
    inner: FrozenSurface,
) -> None:
    """Write two discrete closed shells for a Gmsh annular transition volume.

    The outer and inner entity ids are separated so ``addVolume`` can use the
    second shell as a hole.  The inner triangles are reversed: their normals
    must point into the cavity for the annular volume, while the same original
    orientation is used when meshing the cavity itself.
    """

    outer = list(outer_triangles)
    inner_triangles = list(inner.triangles)
    nodes = dict(outer_nodes)
    if set(nodes).intersection(inner.nodes):
        raise ValueError("coarsened core interface node tags overlap the prism shell")
    nodes.update(inner.nodes)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes))]
    lines.extend(f"{tag} {x:.17g} {y:.17g} {z:.17g}" for tag, (x, y, z) in sorted(nodes.items()))
    lines += ["$EndNodes", "$Elements", str(len(outer) + len(inner_triangles))]
    element_id = 1
    for triangle in outer:
        entity = 100 + triangle.physical_id
        lines.append(f"{element_id} 2 2 {entity} {entity} {' '.join(map(str, triangle.vertices))}")
        element_id += 1
    for triangle in inner_triangles:
        entity = 200 + triangle.physical_id
        a, b, c = triangle.vertices
        lines.append(f"{element_id} 2 2 {entity} {entity} {a} {c} {b}")
        element_id += 1
    lines.append("$EndElements")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_surface_msh(path: Path, nodes: dict[int, tuple[float, float, float]], triangles: Iterable[SurfaceTriangle], *, physical: bool) -> None:
    elements = list(triangles)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat"]
    if physical:
        lines += ["$PhysicalNames", "4", '2 11 "inlet"', '2 12 "outlet"', '2 13 "wall"', '3 1 "fluid"', "$EndPhysicalNames"]
    lines += ["$Nodes", str(len(nodes))]
    lines.extend(f"{tag} {x:.17g} {y:.17g} {z:.17g}" for tag, (x, y, z) in sorted(nodes.items()))
    lines += ["$EndNodes", "$Elements", str(len(elements))]
    for index, triangle in enumerate(elements, 1):
        physical_id = triangle.physical_id if physical else 1
        lines.append(f"{index} 2 2 {physical_id} {physical_id} {' '.join(map(str, triangle.vertices))}")
    lines.append("$EndElements")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mesh_core_with_gmsh(inner_surface: Path, config: LayeredVolumeConfig, debug_msh: Path) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, int, int, int]], list[str]]:
    import gmsh

    gmsh.initialize()
    try:
        gmsh.logger.start()
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.option.setNumber("Mesh.SaveAll", 1)
        gmsh.option.setNumber("Mesh.Algorithm3D", config.algorithm_3d)
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if config.optimize_netgen else 0)
        gmsh.option.setNumber("Mesh.OptimizeThreshold", 0.2)
        gmsh.option.setNumber("Mesh.Smoothing", config.smoothing_steps)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", config.core_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", config.core_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 0)
        gmsh.model.add("flowlab_layered_immutable_core")
        gmsh.merge(str(inner_surface))
        surface_tags = [tag for dimension, tag in gmsh.model.getEntities(2) if dimension == 2]
        if not surface_tags:
            raise RuntimeError("inner surface import has no surface entities")
        loop = gmsh.model.geo.addSurfaceLoop(surface_tags)
        volume = gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()
        for dimension, tag in gmsh.model.getPhysicalGroups(3):
            gmsh.model.removePhysicalGroups([(dimension, tag)])
        gmsh.model.addPhysicalGroup(3, [volume], 1, "fluid")
        gmsh.model.mesh.generate(3)
        gmsh.write(str(debug_msh))
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        nodes = {
            int(node_tags[index]): tuple(float(value) for value in coordinates[3 * index : 3 * index + 3])
            for index in range(len(node_tags))
        }
        types, _, node_sets = gmsh.model.mesh.getElements(3, volume)
        tetrahedra: list[tuple[int, int, int, int]] = []
        for element_type, node_set in zip(types, node_sets):
            if element_type != 4:
                continue
            tetrahedra.extend(tuple(int(value) for value in node_set[index : index + 4]) for index in range(0, len(node_set), 4))
        logs = list(gmsh.logger.get())
        if not tetrahedra:
            raise GmshCoreMeshingError("Gmsh core produced no tetrahedra", logs)
        return nodes, tetrahedra, logs
    finally:
        gmsh.finalize()


def _mesh_transition_with_gmsh(
    transition_surface: Path,
    config: LayeredVolumeConfig,
    debug_msh: Path,
) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, int, int, int]], list[str]]:
    """Mesh the annulus from dense shell interface to coarsened core interface.

    Both discrete surfaces are imported verbatim.  No ``createGeometry`` or
    Gmsh surface-remeshing field is used, and subsequent coordinate remapping
    rejects a run if Gmsh fails to retain every interface node exactly.
    """

    import gmsh

    gmsh.initialize()
    try:
        gmsh.logger.start()
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.option.setNumber("Mesh.SaveAll", 1)
        gmsh.option.setNumber("Mesh.Algorithm3D", config.algorithm_3d)
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if config.optimize_netgen else 0)
        gmsh.option.setNumber("Mesh.OptimizeThreshold", 0.2)
        gmsh.option.setNumber("Mesh.Smoothing", config.smoothing_steps)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", config.core_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", config.core_size_m)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromPoints", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFromCurvature", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthExtendFromBoundary", 0)
        gmsh.model.add("flowlab_layered_immutable_transition")
        gmsh.merge(str(transition_surface))
        entities = {tag for dimension, tag in gmsh.model.getEntities(2) if dimension == 2}
        outer = sorted(tag for tag in entities if 100 < tag < 200)
        inner = sorted(tag for tag in entities if 200 < tag < 300)
        if not outer or not inner:
            raise RuntimeError("transition import did not retain separate outer and inner discrete surfaces")
        outer_loop = gmsh.model.geo.addSurfaceLoop(outer)
        inner_loop = gmsh.model.geo.addSurfaceLoop(inner)
        volume = gmsh.model.geo.addVolume([outer_loop, inner_loop])
        gmsh.model.geo.synchronize()
        gmsh.model.addPhysicalGroup(3, [volume], 1, "fluid")
        gmsh.model.mesh.generate(3)
        gmsh.write(str(debug_msh))
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        nodes = {
            int(node_tags[index]): tuple(float(value) for value in coordinates[3 * index : 3 * index + 3])
            for index in range(len(node_tags))
        }
        types, _, node_sets = gmsh.model.mesh.getElements(3, volume)
        tetrahedra: list[tuple[int, int, int, int]] = []
        for element_type, node_set in zip(types, node_sets):
            if element_type == 4:
                tetrahedra.extend(tuple(int(value) for value in node_set[index : index + 4]) for index in range(0, len(node_set), 4))
        logs = list(gmsh.logger.get())
        if not tetrahedra:
            raise GmshCoreMeshingError("Gmsh transition produced no tetrahedra", logs)
        return nodes, tetrahedra, logs
    finally:
        gmsh.finalize()


def _write_volume_msh(path: Path, nodes: dict[int, tuple[float, float, float]], surface: FrozenSurface, prisms: Iterable[tuple[int, int, int, int, int, int]], tetrahedra: Iterable[tuple[int, int, int, int]]) -> None:
    shell_prisms = list(prisms)
    tets = list(tetrahedra)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", "4", '2 11 "inlet"', '2 12 "outlet"', '2 13 "wall"', '3 1 "fluid"', "$EndPhysicalNames", "$Nodes", str(len(nodes))]
    lines.extend(f"{tag} {x:.17g} {y:.17g} {z:.17g}" for tag, (x, y, z) in sorted(nodes.items()))
    lines += ["$EndNodes", "$Elements", str(len(surface.triangles) + len(shell_prisms) + len(tets))]
    element_id = 1
    for triangle in surface.triangles:
        lines.append(f"{element_id} 2 2 {triangle.physical_id} {triangle.physical_id} {' '.join(map(str, triangle.vertices))}")
        element_id += 1
    for prism in shell_prisms:
        lines.append(f"{element_id} 6 2 1 1 {' '.join(map(str, prism))}")
        element_id += 1
    for tetrahedron in tets:
        lines.append(f"{element_id} 4 2 1 1 {' '.join(map(str, tetrahedron))}")
        element_id += 1
    lines.append("$EndElements")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _remap_core_nodes(
    core_nodes: dict[int, tuple[float, float, float]],
    interface_nodes: dict[int, tuple[float, float, float]],
    occupied_tags: set[int],
) -> tuple[dict[int, int], dict[int, tuple[float, float, float]]]:
    """Map Gmsh's re-numbered nodes back to declared immutable interfaces."""

    precision = 11
    interface_by_coordinate = {
        tuple(round(value, precision) for value in point): tag
        for tag, point in interface_nodes.items()
    }
    if len(interface_by_coordinate) != len(interface_nodes):
        raise ValueError("declared mesh interfaces contain coincident but differently tagged nodes")
    mapping: dict[int, int] = {}
    new_nodes: dict[int, tuple[float, float, float]] = {}
    next_tag = max(occupied_tags) + 1
    for old_tag, point in core_nodes.items():
        key = tuple(round(value, precision) for value in point)
        interface_tag = interface_by_coordinate.get(key)
        if interface_tag is not None:
            reference = interface_nodes[interface_tag]
            if max(abs(point[axis] - reference[axis]) for axis in range(3)) > 1.0e-10:
                raise RuntimeError(f"Gmsh changed declared interface node {interface_tag}")
            mapping[old_tag] = interface_tag
            continue
        mapping[old_tag] = next_tag
        new_nodes[next_tag] = point
        next_tag += 1
    if set(mapping.values()).intersection(interface_nodes) != set(interface_nodes):
        raise RuntimeError("Gmsh did not retain every declared interface node")
    return mapping, new_nodes


def build_layered_volume(
    master: Path,
    output: Path,
    config: LayeredVolumeConfig,
    *,
    core_debug_msh: Path,
    expected_surface_sha256: str | None = None,
) -> dict[str, Any]:
    """Build one shell/core candidate and return the immutable-surface report."""

    surface = read_frozen_surface(master)
    before = msh2_surface_fingerprint(master)
    if expected_surface_sha256 and before["surfaceSha256"] != expected_surface_sha256:
        raise ValueError(
            "declared frozen-surface SHA-256 does not match master "
            f"(expected {expected_surface_sha256}, got {before['surfaceSha256']})"
        )
    shell_nodes, shell_prisms, final_mapping = build_transition_shell(surface, config)
    inner_nodes = {final_mapping[outer]: shell_nodes[final_mapping[outer]] for outer in surface.nodes}
    inner_triangles = tuple(SurfaceTriangle(triangle.physical_id, tuple(final_mapping[node] for node in triangle.vertices)) for triangle in surface.triangles)
    with tempfile.TemporaryDirectory(prefix="flowlab-inner-surface-") as temporary:
        temporary_path = Path(temporary)
        if config.uses_coarsened_core_interface:
            interface = build_coarsened_core_interface(
                surface,
                config,
                first_node_tag=max(shell_nodes) + 1,
                directory=temporary_path,
            )
            transition_surface = temporary_path / "transition-surface.msh"
            _write_transition_region_msh(
                transition_surface,
                outer_nodes=inner_nodes,
                outer_triangles=inner_triangles,
                inner=interface,
            )
            transition_debug = core_debug_msh.with_name(core_debug_msh.stem + "-transition.msh")
            transition_nodes, transition_tets, transition_log = _mesh_transition_with_gmsh(
                transition_surface, config, transition_debug
            )
            all_nodes = dict(surface.nodes)
            all_nodes.update(shell_nodes)
            all_nodes.update(interface.nodes)
            declared_transition_interfaces = dict(inner_nodes)
            declared_transition_interfaces.update(interface.nodes)
            transition_mapping, added_transition_nodes = _remap_core_nodes(
                transition_nodes, declared_transition_interfaces, set(all_nodes)
            )
            all_nodes.update(added_transition_nodes)
            remapped_transition_tets = [
                tuple(transition_mapping[node] for node in tetrahedron)
                for tetrahedron in transition_tets
            ]
            core_surface = temporary_path / "coarsened-core-surface.msh"
            _write_surface_msh(core_surface, interface.nodes, interface.triangles, physical=True)
            core_nodes, core_tets, core_log = _mesh_core_with_gmsh(core_surface, config, core_debug_msh)
            core_mapping, added_core_nodes = _remap_core_nodes(core_nodes, interface.nodes, set(all_nodes))
            all_nodes.update(added_core_nodes)
            remapped_core_tets = [tuple(core_mapping[node] for node in tetrahedron) for tetrahedron in core_tets]
            gmsh_log = ["[transition]"] + transition_log + ["[core]"] + core_log
            transition_tetrahedra = len(remapped_transition_tets)
            core_interface = {
                "strategy": "coarsened-inner-interface-annular-transition.v1",
                "chords": config.core_interface_chords,
                "transitionThicknessM": config.transition_thickness_m,
                "nodes": len(interface.nodes),
                "triangles": len(interface.triangles),
                "transitionDebugMsh": str(transition_debug),
            }
        else:
            inner_surface = temporary_path / "inner-surface.msh"
            _write_surface_msh(inner_surface, inner_nodes, inner_triangles, physical=True)
            core_nodes, core_tets, core_log = _mesh_core_with_gmsh(inner_surface, config, core_debug_msh)
            all_nodes = dict(surface.nodes)
            all_nodes.update(shell_nodes)
            core_mapping, added_core_nodes = _remap_core_nodes(core_nodes, inner_nodes, set(all_nodes))
            all_nodes.update(added_core_nodes)
            remapped_core_tets = [tuple(core_mapping[node] for node in tetrahedron) for tetrahedron in core_tets]
            remapped_transition_tets = []
            gmsh_log = core_log
            transition_tetrahedra = 0
            core_interface = {"strategy": "full-density-shell-interface.v1", "nodes": len(inner_nodes), "triangles": len(inner_triangles)}
    _write_volume_msh(output, all_nodes, surface, shell_prisms, remapped_transition_tets + remapped_core_tets)
    after = msh2_surface_fingerprint(output)
    return {
        "schema": "flowlab.layered-immutable-volume.v1",
        "master": str(master),
        "output": str(output),
        "expectedSurfaceSha256": expected_surface_sha256,
        "config": dict(asdict(config), shellThicknessM=config.shell_thickness_m),
        "volumeStrategy": volume_strategy(config),
        "before": before,
        "after": after,
        "outerSurfaceHashEqual": before["surfaceSha256"] == after["surfaceSha256"],
        "outerSurfaceOrientedHashEqual": before["orientedSurfaceSha256"] == after["orientedSurfaceSha256"],
        "outerSurfaceTriangleCountEqual": before["surfaceTriangles"] == after["surfaceTriangles"],
        "shellPrisms": len(shell_prisms),
        "transitionTetrahedra": transition_tetrahedra,
        "coreTetrahedra": len(remapped_core_tets),
        "coreInterface": core_interface,
        "tetrahedra": tetrahedron_count(output),
        "gmshLog": gmsh_log,
        "accepted": bool(
            before["surfaceSha256"] == after["surfaceSha256"]
            and before["orientedSurfaceSha256"] == after["orientedSurfaceSha256"]
            and tetrahedron_count(output) > 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--core-debug-msh", type=Path, required=True)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--gmsh-log", type=Path)
    parser.add_argument("--first-layer-m", type=float, required=True)
    parser.add_argument("--layer-count", type=int, required=True)
    parser.add_argument("--growth-ratio", type=float, default=1.2)
    parser.add_argument("--core-size-m", type=float, required=True)
    parser.add_argument(
        "--algorithm-3d",
        type=int,
        default=4,
        help="Gmsh 3D algorithm: 1=Delaunay, 4=Frontal (the historical v4 default).",
    )
    parser.add_argument(
        "--smoothing-steps",
        type=int,
        default=20,
        help="Explicit Gmsh smoothing count retained in configuration evidence.",
    )
    parser.add_argument(
        "--no-optimize-netgen",
        action="store_true",
        help="Disable Netgen optimization; enabled by default and always recorded.",
    )
    parser.add_argument(
        "--core-interface-chords",
        type=int,
        help="Opt into the coarsened nonphysical core interface; must be a multiple of eight.",
    )
    parser.add_argument(
        "--transition-thickness-m",
        type=float,
        help="Radial/axial offset from the dense prism shell to the coarsened core interface.",
    )
    parser.add_argument(
        "--volume-strategy-version",
        default="v1",
        help="Explicit evidence version for the selected non-legacy internal-volume strategy.",
    )
    parser.add_argument(
        "--volume-strategy-id",
        help="Explicit internal-volume topology identity for evidence comparison.",
    )
    parser.add_argument(
        "--expected-surface-sha256",
        help="Declared canonical hash of the immutable master; reject before Gmsh if it differs.",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.core_debug_msh.parent.mkdir(parents=True, exist_ok=True)
    if args.configuration:
        args.configuration.parent.mkdir(parents=True, exist_ok=True)
    if args.gmsh_log:
        args.gmsh_log.parent.mkdir(parents=True, exist_ok=True)
    config = LayeredVolumeConfig(
        args.first_layer_m,
        args.layer_count,
        args.growth_ratio,
        args.core_size_m,
        algorithm_3d=args.algorithm_3d,
        optimize_netgen=not args.no_optimize_netgen,
        smoothing_steps=args.smoothing_steps,
        core_interface_chords=args.core_interface_chords,
        transition_thickness_m=args.transition_thickness_m,
        volume_strategy_id=args.volume_strategy_id,
        volume_strategy_version=args.volume_strategy_version,
    )
    if args.configuration:
        args.configuration.write_text(
            json.dumps(
                {
                    "schema": "flowlab.layered-immutable-volume-config.v1",
                    "master": str(args.master),
                    "expectedSurfaceSha256": args.expected_surface_sha256,
                    "config": dict(asdict(config), shellThicknessM=config.shell_thickness_m),
                    "volumeStrategy": volume_strategy(config),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    try:
        report = build_layered_volume(
            args.master,
            args.output,
            config,
            core_debug_msh=args.core_debug_msh,
            expected_surface_sha256=args.expected_surface_sha256,
        )
    except (RuntimeError, ValueError) as error:
        report = {
            "schema": "flowlab.layered-immutable-volume.v1",
            "master": str(args.master),
            "output": str(args.output),
            "expectedSurfaceSha256": args.expected_surface_sha256,
            "config": dict(asdict(config), shellThicknessM=config.shell_thickness_m),
            "volumeStrategy": volume_strategy(config),
            "accepted": False,
            "stage": "builder-preflight",
            "failure": str(error),
        }
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.gmsh_log:
            gmsh_log = getattr(error, "gmsh_log", None)
            args.gmsh_log.write_text(
                "\n".join(gmsh_log) + "\n" if gmsh_log else f"Gmsh not invoked: {error}\n",
                encoding="utf-8",
            )
        return 2
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.gmsh_log:
        args.gmsh_log.write_text("\n".join(report["gmshLog"]) + "\n", encoding="utf-8")
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
