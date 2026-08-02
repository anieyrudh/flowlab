"""Body-fitted straight-pipe O-grid emitted directly as an OpenFOAM polyMesh.

FlowLab's five-block O-grid generator normally emits a ``blockMeshDict`` and
lets ``blockMesh`` create the cells.  FlowLab then has to re-identify its own
cells inside the solver mesh by matching geometric signatures.  This module
removes that problem at the source: it sweeps the same butterfly cross-section
along an explicit frame and writes ``constant/polyMesh`` itself, so the FlowLab
cell index *is* the OpenFOAM cell index by construction.

Milestone 0 proves the approach on a straight circular pipe.  The cross-section
is reused verbatim from :mod:`server.flowlab.full_ogrid`; nothing here
re-derives cross-section geometry.  Cell ownership is assigned by a cursor walk
over a declared block order and is never reconstructed from coordinates.  Face
identity is logical - the labels of the four points bounding the face - and is
never a coordinate tolerance match.

This module is product geometry source, not validation evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import re
from pathlib import Path
from typing import Any

from .full_ogrid import FullOGridSpec, _cross_section


OGRID_POLYMESH_SCHEMA = "flowlab.ogrid-polymesh.v1"
OGRID_POLYMESH_REPRESENTATION = "swept-five-block-ogrid-direct-polymesh"
PATCH_ORDER = ("inlet", "outlet", "walls")
PATCH_TYPES = {"inlet": "patch", "outlet": "patch", "walls": "wall"}

# Faces of the OpenFOAM ``hex`` cell model, each already oriented outward for a
# cell whose points 0-3 are the sweep-start quad traversed counter-clockwise
# about the sweep direction and points 4-7 the matching sweep-end quad.
HEX_FACES = (
    (0, 4, 7, 3),
    (1, 2, 6, 5),
    (0, 1, 5, 4),
    (3, 7, 6, 2),
    (0, 3, 2, 1),
    (4, 5, 6, 7),
)
SWEEP_START_FACE = 4
SWEEP_END_FACE = 5
# Side face carrying cross-section edge ``(i, i + 1)`` of the sweep-start quad.
SIDE_FACE_BY_EDGE = (2, 1, 3, 0)


def _finite(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite SI value.")
    return number


def _vector(value: Sequence[float], label: str) -> tuple[float, float, float]:
    values = tuple(_finite(component, label) for component in value)
    if len(values) != 3:
        raise ValueError(f"{label} must have exactly three components.")
    return values


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(value: Sequence[float], label: str) -> tuple[float, float, float]:
    vector = _vector(value, label)
    norm = math.sqrt(_dot(vector, vector))
    if norm <= 0.0:
        raise ValueError(f"{label} must be a non-zero direction.")
    return (vector[0] / norm, vector[1] / norm, vector[2] / norm)


def _safe_zone_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(value))
    if not name or not re.match(r"[A-Za-z_]", name):
        name = f"zone_{name}"
    return name


@dataclass(frozen=True)
class OGridFrame:
    """Right-handed sweep frame for one straight O-grid run.

    ``tangent`` is the sweep direction; ``normal`` and ``binormal`` span the
    cross-section plane so that ``normal x binormal == tangent``.  A
    cross-section coordinate ``(first, second)`` produced by
    :func:`full_ogrid._cross_section` is placed at
    ``origin + distance * tangent + first * normal + second * binormal``.
    """

    origin: tuple[float, float, float]
    tangent: tuple[float, float, float]
    normal: tuple[float, float, float]
    binormal: tuple[float, float, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _vector(self.origin, "O-grid frame origin"))
        for label in ("tangent", "normal", "binormal"):
            object.__setattr__(
                self, label, _vector(getattr(self, label), f"O-grid frame {label}")
            )
        for label in ("tangent", "normal", "binormal"):
            axis = getattr(self, label)
            if abs(_dot(axis, axis) - 1.0) > 1e-12:
                raise ValueError(f"O-grid frame {label} must be a unit vector.")
        pairs = (
            ("tangent", "normal"),
            ("tangent", "binormal"),
            ("normal", "binormal"),
        )
        for first, second in pairs:
            if abs(_dot(getattr(self, first), getattr(self, second))) > 1e-12:
                raise ValueError(f"O-grid frame {first} and {second} must be orthogonal.")
        handed = _cross(self.normal, self.binormal)
        if any(abs(handed[axis] - self.tangent[axis]) > 1e-12 for axis in range(3)):
            raise ValueError(
                "O-grid frame must be right-handed: normal x binormal must equal tangent."
            )

    @classmethod
    def from_axis(
        cls,
        origin: Sequence[float],
        direction: Sequence[float],
    ) -> "OGridFrame":
        """Return a deterministic right-handed frame around a straight axis."""

        tangent = _unit(direction, "O-grid frame direction")
        seed_axis = min((abs(tangent[axis]), axis) for axis in range(3))[1]
        seed = tuple(1.0 if axis == seed_axis else 0.0 for axis in range(3))
        projection = _dot(seed, tangent)
        normal = _unit(
            tuple(seed[axis] - projection * tangent[axis] for axis in range(3)),
            "O-grid frame normal",
        )
        binormal = _cross(tangent, normal)
        return cls(origin=_vector(origin, "O-grid frame origin"), tangent=tangent,
                   normal=normal, binormal=binormal)

    def point(self, distance_m: float, first: float, second: float) -> tuple[float, float, float]:
        return tuple(  # type: ignore[return-value]
            self.origin[axis]
            + distance_m * self.tangent[axis]
            + first * self.normal[axis]
            + second * self.binormal[axis]
            for axis in range(3)
        )


@dataclass(frozen=True)
class OGridBlock:
    """One logically structured swept hexahedral block."""

    name: str
    cells: tuple[tuple[int, int, int, int, int, int, int, int], ...]


@dataclass(frozen=True)
class OGridPatch:
    """Boundary patch declared as explicit ``(block, cell, face)`` claims."""

    name: str
    type: str
    faces: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class OGridRegion:
    """Named owner of a consecutive run of declared blocks."""

    name: str
    block_names: tuple[str, ...]


@dataclass(frozen=True)
class OGridBlockSet:
    """Points plus the declared block, patch, and region ordering."""

    points: tuple[tuple[float, float, float], ...]
    blocks: tuple[OGridBlock, ...]
    patches: tuple[OGridPatch, ...]
    regions: tuple[OGridRegion, ...]

    @property
    def cell_count(self) -> int:
        return sum(len(block.cells) for block in self.blocks)

    def block_ranges(self) -> dict[str, tuple[int, int]]:
        return _block_ranges(self.blocks)

    def region_ranges(self) -> dict[str, tuple[int, int]]:
        return _region_ranges(self.blocks, self.regions)

    def to_polymesh(self, *, root: str = "constant/polyMesh") -> dict[str, str]:
        return block_set_to_polymesh(
            self.blocks, self.patches, self.regions, points=self.points, root=root
        )

    def manifest(self) -> dict[str, Any]:
        block_ranges = self.block_ranges()
        region_ranges = self.region_ranges()
        return {
            "schema": OGRID_POLYMESH_SCHEMA,
            "representation": OGRID_POLYMESH_REPRESENTATION,
            "spatialDimension": 3,
            "cellTypes": ["hex"],
            "cellIdentity": "flowlab_mesh_order",
            "meshAuthority": "flowlab-direct-polymesh",
            "pointCount": len(self.points),
            "cellCount": self.cell_count,
            "blocks": [
                {
                    "name": block.name,
                    "cellStart": block_ranges[block.name][0],
                    "cellCount": block_ranges[block.name][1],
                }
                for block in self.blocks
            ],
            "patches": {
                patch.name: {"type": patch.type, "faceCount": len(patch.faces)}
                for patch in self.patches
            },
            "regions": [
                {
                    "name": region.name,
                    "cellStart": region_ranges[region.name][0],
                    "cellCount": region_ranges[region.name][1],
                }
                for region in self.regions
            ],
        }


def _block_ranges(blocks: Sequence[OGridBlock]) -> dict[str, tuple[int, int]]:
    """Assign every block a contiguous cell range by one forward cursor walk."""

    ranges: dict[str, tuple[int, int]] = {}
    cursor = 0
    for block in blocks:
        if block.name in ranges:
            raise ValueError(f"O-grid block name {block.name!r} is declared twice.")
        if not block.cells:
            raise ValueError(f"O-grid block {block.name!r} declares no cells.")
        ranges[block.name] = (cursor, len(block.cells))
        cursor += len(block.cells)
    if not ranges:
        raise ValueError("O-grid block set declares no blocks.")
    return ranges


def _region_ranges(
    blocks: Sequence[OGridBlock],
    regions: Sequence[OGridRegion],
) -> dict[str, tuple[int, int]]:
    """Fold consecutive declared blocks into contiguous region cell ranges."""

    ranges = _block_ranges(blocks)
    order = {block.name: index for index, block in enumerate(blocks)}
    result: dict[str, tuple[int, int]] = {}
    claimed: set[str] = set()
    for region in regions:
        if region.name in result:
            raise ValueError(f"O-grid region name {region.name!r} is declared twice.")
        if not region.block_names:
            raise ValueError(f"O-grid region {region.name!r} owns no blocks.")
        indices = []
        for name in region.block_names:
            if name not in ranges:
                raise ValueError(f"O-grid region {region.name!r} names unknown block {name!r}.")
            if name in claimed:
                raise ValueError(f"O-grid block {name!r} is owned by more than one region.")
            claimed.add(name)
            indices.append(order[name])
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise ValueError(
                f"O-grid region {region.name!r} must own consecutive declared blocks."
            )
        start = ranges[region.block_names[0]][0]
        count = sum(ranges[name][1] for name in region.block_names)
        result[region.name] = (start, count)
    if len(claimed) != len(ranges):
        raise ValueError("Every O-grid block must be owned by exactly one region.")
    return result


def _edge_start(first: int, second: int) -> int:
    for start in range(4):
        if {start, (start + 1) % 4} == {first, second}:
            return start
    raise ValueError("Cross-section corners do not bound a quad edge.")


def _signed_area(points: Sequence[tuple[float, float]], quad: Sequence[int]) -> float:
    total = 0.0
    for index in range(4):
        current = points[quad[index]]
        following = points[quad[(index + 1) % 4]]
        total += current[0] * following[1] - current[1] * following[0]
    return total / 2.0


def swept_ogrid_block_set(
    frame: OGridFrame,
    stations: Sequence[float],
    spec: FullOGridSpec,
    *,
    region_name: str = "pipe",
) -> OGridBlockSet:
    """Sweep the canonical butterfly cross-section along ``frame``.

    ``stations`` are strictly increasing arc-length positions along the frame
    tangent; there must be ``spec.axial_cells + 1`` of them.  The cross-section
    itself comes verbatim from :func:`full_ogrid._cross_section`, so the wall
    points sit exactly on the analytic circle and the core/annulus interface is
    conformal by shared point label rather than by tolerance.
    """

    if not isinstance(frame, OGridFrame):
        raise ValueError("swept O-grid requires an OGridFrame.")
    values = [_finite(station, "swept O-grid station") for station in stations]
    if len(values) != spec.axial_cells + 1:
        raise ValueError("swept O-grid needs exactly axialCells+1 stations.")
    if any(values[index] >= values[index + 1] for index in range(len(values) - 1)):
        raise ValueError("swept O-grid stations must be strictly increasing.")

    cross_points, cross_cells, _cross_areas = _cross_section(spec)
    slice_points = len(cross_points)
    points: list[tuple[float, float, float]] = []
    for station in values:
        points.extend(frame.point(station, first, second) for first, second in cross_points)

    # Normalize every cross-section quad to counter-clockwise order about the
    # sweep tangent.  ``_cross_section`` emits core quads counter-clockwise and
    # annular quads clockwise, and OpenFOAM hex ordering requires one handedness.
    quads: list[tuple[int, int, int, int]] = []
    outer_edges: list[tuple[int, int]] = []
    for quad in cross_cells:
        if _signed_area(cross_points, quad) > 0.0:
            permutation = (0, 1, 2, 3)
        else:
            permutation = (3, 2, 1, 0)
        quads.append(tuple(quad[index] for index in permutation))  # type: ignore[arg-type]
        outer_edges.append((permutation.index(2), permutation.index(3)))

    core = spec.core_cells_per_side
    quadrant_cells = spec.circumferential_cells_per_quadrant
    radial_cells = spec.annular_radial_cells
    center_count = core * core
    quadrant_count = radial_cells * quadrant_cells

    block_quads: list[tuple[str, range]] = [("center", range(center_count))]
    for quadrant in range(4):
        start = center_count + quadrant * quadrant_count
        block_quads.append((f"wall-{quadrant}", range(start, start + quadrant_count)))

    intervals = len(values) - 1
    blocks: list[OGridBlock] = []
    inlet_faces: list[tuple[str, int, int]] = []
    outlet_faces: list[tuple[str, int, int]] = []
    wall_faces: list[tuple[str, int, int]] = []
    for name, quad_range in block_quads:
        cells: list[tuple[int, int, int, int, int, int, int, int]] = []
        for interval in range(intervals):
            low = interval * slice_points
            high = (interval + 1) * slice_points
            for quad_index in quad_range:
                quad = quads[quad_index]
                local_index = len(cells)
                cells.append(
                    (
                        low + quad[0], low + quad[1], low + quad[2], low + quad[3],
                        high + quad[0], high + quad[1], high + quad[2], high + quad[3],
                    )
                )
                if interval == 0:
                    inlet_faces.append((name, local_index, SWEEP_START_FACE))
                if interval == intervals - 1:
                    outlet_faces.append((name, local_index, SWEEP_END_FACE))
                if name != "center":
                    offset = quad_index - quad_range.start
                    if offset // quadrant_cells == radial_cells - 1:
                        edge = outer_edges[quad_index]
                        wall_faces.append(
                            (name, local_index, SIDE_FACE_BY_EDGE[_edge_start(*edge)])
                        )
        blocks.append(OGridBlock(name=name, cells=tuple(cells)))

    declared = {"inlet": inlet_faces, "outlet": outlet_faces, "walls": wall_faces}
    patches = tuple(
        OGridPatch(name=name, type=PATCH_TYPES[name], faces=tuple(declared[name]))
        for name in PATCH_ORDER
    )
    regions = (
        OGridRegion(name=region_name, block_names=tuple(name for name, _ in block_quads)),
    )
    block_set = OGridBlockSet(
        points=tuple(points),
        blocks=tuple(blocks),
        patches=patches,
        regions=regions,
    )
    if block_set.cell_count != spec.cell_count:
        raise ValueError("swept O-grid cell count disagrees with the declared spec.")
    if len(points) != (spec.axial_cells + 1) * slice_points:
        raise ValueError("swept O-grid point count disagrees with the swept cross-section.")
    return block_set


def straight_pipe_block_set(
    spec: FullOGridSpec,
    *,
    origin: Sequence[float] = (0.0, 0.0, 0.0),
    axis: Sequence[float] = (1.0, 0.0, 0.0),
    region_name: str = "pipe",
) -> OGridBlockSet:
    """Return the Milestone-0 straight circular pipe swept along ``axis``."""

    frame = OGridFrame.from_axis(origin, axis)
    stations = [
        spec.length_m * index / spec.axial_cells for index in range(spec.axial_cells + 1)
    ]
    return swept_ogrid_block_set(frame, stations, spec, region_name=region_name)


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


def block_set_to_polymesh(
    blocks: Sequence[OGridBlock],
    patches: Sequence[OGridPatch],
    regions: Sequence[OGridRegion],
    *,
    points: Sequence[tuple[float, float, float]],
    root: str = "constant/polyMesh",
) -> dict[str, str]:
    """Emit ``points``, ``faces``, ``owner``, ``neighbour``, ``boundary``, ``cellZones``.

    Cells are appended in the declared block order, so every block and region
    owns a contiguous range by construction.  Two cells share a face when the
    face carries the same four point labels; there is no coordinate tolerance
    anywhere in the dedup.  A boundary face that no patch claims, or a patch
    claim that is not a boundary face of its declared owner, fails closed.
    """

    ranges = _block_ranges(blocks)
    region_ranges = _region_ranges(blocks, regions)
    cells: list[tuple[int, ...]] = []
    for block in blocks:
        cells.extend(block.cells)
    point_count = len(points)
    for index, cell in enumerate(cells):
        if len(cell) != 8 or len(set(cell)) != 8:
            raise ValueError(f"O-grid cell {index} is not an eight-point hexahedron.")
        if any(not 0 <= label < point_count for label in cell):
            raise ValueError(f"O-grid cell {index} references an unknown point label.")

    open_faces: dict[tuple[int, ...], tuple[int, tuple[int, ...]]] = {}
    closed_faces: set[tuple[int, ...]] = set()
    internal_faces: list[tuple[int, int, tuple[int, ...]]] = []
    for index, cell in enumerate(cells):
        for local in HEX_FACES:
            vertices = tuple(cell[position] for position in local)
            key = tuple(sorted(vertices))
            if key in closed_faces:
                raise ValueError("O-grid face is shared by more than two cells.")
            previous = open_faces.get(key)
            if previous is None:
                open_faces[key] = (index, vertices)
                continue
            owner_index, owner_vertices = previous
            internal_faces.append((owner_index, index, owner_vertices))
            del open_faces[key]
            closed_faces.add(key)

    claimed: dict[tuple[int, ...], str] = {}
    patch_faces: dict[str, list[tuple[int, tuple[int, ...]]]] = {}
    patch_specs: list[tuple[str, str]] = []
    for patch in patches:
        if patch.name in patch_faces:
            raise ValueError(f"O-grid patch name {patch.name!r} is declared twice.")
        entries: list[tuple[int, tuple[int, ...]]] = []
        for block_name, local_index, face_index in patch.faces:
            if block_name not in ranges:
                raise ValueError(f"O-grid patch {patch.name!r} names unknown block {block_name!r}.")
            start, count = ranges[block_name]
            if not 0 <= local_index < count:
                raise ValueError(f"O-grid patch {patch.name!r} names a cell outside its block.")
            if not 0 <= face_index < len(HEX_FACES):
                raise ValueError(f"O-grid patch {patch.name!r} names an unknown hex face.")
            global_index = start + local_index
            vertices = tuple(
                cells[global_index][position] for position in HEX_FACES[face_index]
            )
            key = tuple(sorted(vertices))
            record = open_faces.get(key)
            if record is None or record[0] != global_index:
                raise ValueError(
                    f"O-grid patch {patch.name!r} claims a face that is not an unclaimed "
                    "boundary face of its declared owner cell."
                )
            if key in claimed:
                raise ValueError(f"O-grid boundary face is claimed by {claimed[key]!r} and {patch.name!r}.")
            claimed[key] = patch.name
            entries.append((global_index, vertices))
        entries.sort(key=lambda record: (record[0], record[1]))
        patch_faces[patch.name] = entries
        patch_specs.append((patch.name, patch.type))
    if len(claimed) != len(open_faces):
        raise ValueError(
            f"{len(open_faces) - len(claimed)} O-grid boundary faces have no patch owner."
        )

    internal_faces.sort(key=lambda record: (record[0], record[1]))
    face_values: list[tuple[int, ...]] = []
    owner_values: list[int] = []
    neighbour_values: list[int] = []
    for owner, neighbour, vertices in internal_faces:
        face_values.append(vertices)
        owner_values.append(owner)
        neighbour_values.append(neighbour)

    boundary_specs: list[tuple[str, str, int, int]] = []
    for name, patch_type in patch_specs:
        start_face = len(face_values)
        for owner, vertices in patch_faces[name]:
            face_values.append(vertices)
            owner_values.append(owner)
        boundary_specs.append((name, patch_type, len(patch_faces[name]), start_face))

    points_text = (
        _foam_header("vectorField", "points")
        + f"{len(points)}\n(\n"
        + "".join(f"({point[0]:.17g} {point[1]:.17g} {point[2]:.17g})\n" for point in points)
        + ")\n"
    )
    faces_text = (
        _foam_header("faceList", "faces")
        + f"{len(face_values)}\n(\n"
        + "".join(
            f"{len(face)}(" + " ".join(str(value) for value in face) + ")\n"
            for face in face_values
        )
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
    for region in regions:
        start, count = region_ranges[region.name]
        labels = "\n".join(str(value) for value in range(start, start + count))
        zone_entries.append(
            f"{_safe_zone_name(region.name)}\n{{\n    type cellZone;\n    cellLabels List<label>\n"
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


def write_polymesh(case_dir: str, files: dict[str, str]) -> list[str]:
    """Materialize emitted polyMesh text under ``case_dir`` and return the paths."""

    root = Path(case_dir)
    written: list[str] = []
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(str(target))
    return written
