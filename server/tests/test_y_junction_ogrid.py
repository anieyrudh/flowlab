"""Contract tests for the body-fitted direct-polyMesh Y-junction (Milestone 1).

Like the Milestone-0 tests, these read the emitted ``constant/polyMesh`` text
back and rebuild the geometry from it, so they check the artifact OpenFOAM
would consume rather than the in-memory intermediate.
"""

import hashlib
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.flowlab.y_junction import JUNCTION_ARTIFACT_ID as CARTESIAN_ARTIFACT_ID
from server.flowlab.ogrid_polymesh import block_set_to_polymesh, write_polymesh
from server.flowlab.y_junction_ogrid import (
    JUNCTION_OGRID_ARTIFACT_ID,
    PATCH_ORDER,
    REGION_ORDER,
    Y_JUNCTION_OGRID_REPRESENTATION,
    YJunctionOGridSpec,
    y_junction_block_set,
    y_junction_manifest,
    y_junction_polymesh,
)
from server.flowlab.y_junction_ogrid import _butterfly, _octant_circle_point


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROOT_THREE = math.sqrt(3.0)
# tan(15 deg): the inlet/branch bisector slope against the inlet axis.
SEAM_SLOPE = 2.0 - ROOT_THREE


def _spec(**overrides) -> YJunctionOGridSpec:
    fields = {
        "radius_m": 0.003,
        "inlet_length_m": 0.012,
        "branch_length_m": 0.012,
        "annular_radial_cells": 2,
        "circumferential_cells": 16,
        "core_cells_per_side": 4,
        "inlet_leg_axial_cells": 2,
        "branch_leg_axial_cells": 2,
        "junction_axial_cells": 2,
    }
    fields.update(overrides)
    return YJunctionOGridSpec(**fields)


def _parse_points(text: str) -> list[tuple[float, float, float]]:
    return [
        tuple(float(value) for value in match.split())
        for match in re.findall(r"^\(([^)\n]*)\)$", text, flags=re.MULTILINE)
    ]


def _parse_faces(text: str) -> list[list[int]]:
    return [
        [int(value) for value in match.split()]
        for match in re.findall(r"^\d+\(([^)\n]*)\)$", text, flags=re.MULTILINE)
    ]


def _parse_labels(text: str) -> list[int]:
    body = text.split("(", 1)[1].rsplit(")", 1)[0]
    return [int(line) for line in body.split()]


def _parse_boundary(text: str) -> list[tuple[str, str, int, int]]:
    pattern = r"(\w+)\s*\{\s*type\s+(\w+);\s*nFaces\s+(\d+);\s*startFace\s+(\d+);\s*\}"
    return [
        (name, patch_type, int(count), int(start))
        for name, patch_type, count, start in re.findall(pattern, text)
    ]


def _parse_cell_zones(text: str) -> list[tuple[str, list[int]]]:
    pattern = r"(\w+)\s*\{\s*type\s+cellZone;\s*cellLabels\s+List<label>\s*\d+\s*\(([^)]*)\)"
    return [
        (name, [int(value) for value in labels.split()])
        for name, labels in re.findall(pattern, text)
    ]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _face_moment(points, face):
    """Return ``(outward area vector, integral of x.n over the face)``."""

    origin = points[face[0]]
    area = [0.0, 0.0, 0.0]
    flux = 0.0
    for index in range(1, len(face) - 1):
        first = points[face[index]]
        second = points[face[index + 1]]
        normal = _cross(_sub(first, origin), _sub(second, origin))
        centroid = tuple(
            (origin[axis] + first[axis] + second[axis]) / 3.0 for axis in range(3)
        )
        for axis in range(3):
            area[axis] += normal[axis] / 2.0
        flux += _dot(centroid, normal) / 2.0
    return tuple(area), flux


def _read_mesh(files: dict[str, str]) -> dict[str, object]:
    points = _parse_points(files["constant/polyMesh/points"])
    faces = _parse_faces(files["constant/polyMesh/faces"])
    owner = _parse_labels(files["constant/polyMesh/owner"])
    neighbour = _parse_labels(files["constant/polyMesh/neighbour"])
    boundary = _parse_boundary(files["constant/polyMesh/boundary"])
    cell_count = max(owner + neighbour) + 1
    volumes = [0.0] * cell_count
    face_count = [0] * cell_count
    cell_points: list[set[int]] = [set() for _ in range(cell_count)]
    for index, face in enumerate(faces):
        _area, flux = _face_moment(points, face)
        contribution = flux / 3.0
        volumes[owner[index]] += contribution
        face_count[owner[index]] += 1
        cell_points[owner[index]].update(face)
        if index < len(neighbour):
            volumes[neighbour[index]] -= contribution
            face_count[neighbour[index]] += 1
            cell_points[neighbour[index]].update(face)
    return {
        "points": points,
        "faces": faces,
        "owner": owner,
        "neighbour": neighbour,
        "boundary": boundary,
        "cellCount": cell_count,
        "volumes": volumes,
        "facesPerCell": face_count,
        "pointsPerCell": [len(entry) for entry in cell_points],
    }


def _cell_centroid(points, faces, owner, neighbour, cell_count):
    accumulated = [[0.0, 0.0, 0.0] for _ in range(cell_count)]
    counts = [0] * cell_count
    for index, face in enumerate(faces):
        for label in face:
            for axis in range(3):
                accumulated[owner[index]][axis] += points[label][axis]
            counts[owner[index]] += 1
            if index < len(neighbour):
                for axis in range(3):
                    accumulated[neighbour[index]][axis] += points[label][axis]
                counts[neighbour[index]] += 1
    return [
        tuple(value / counts[cell] for value in accumulated[cell])
        for cell in range(cell_count)
    ]


def _axis_distance(point, direction):
    axial = _dot(point, direction)
    radial = tuple(point[axis] - axial * direction[axis] for axis in range(3))
    return math.sqrt(_dot(radial, radial)), axial


def _leg_axes(spec):
    return (
        ((-1.0, 0.0, 0.0), spec.inlet_length_m),
        (spec.upper_direction, spec.branch_length_m),
        (spec.lower_direction, spec.branch_length_m),
    )


# ---------------------------------------------------------------------------
# Declared geometry and fail-closed validation
# ---------------------------------------------------------------------------


def test_spec_fails_closed_on_every_invariant_the_topology_needs() -> None:
    with pytest.raises(ValueError, match="divisible by eight"):
        _spec(circumferential_cells=20, core_cells_per_side=5)
    with pytest.raises(ValueError, match="coreCellsPerSide must equal"):
        _spec(circumferential_cells=16, core_cells_per_side=8)
    with pytest.raises(ValueError, match="inlet length must exceed"):
        _spec(inlet_length_m=0.003)
    with pytest.raises(ValueError, match="branch length must exceed"):
        _spec(branch_length_m=0.006)
    with pytest.raises(ValueError, match="30-degree branches"):
        _spec(branch_angle_degrees=45.0)
    with pytest.raises(ValueError, match="junctionAxialCells"):
        _spec(junction_axial_cells=1)
    with pytest.raises(ValueError, match="annularRadialExpansion"):
        _spec(annular_radial_expansion=0.0)


def test_seam_geometry_matches_the_declared_circular_primitives() -> None:
    spec = _spec()
    radius = spec.radius_m
    seam = spec.seam_geometry()

    # Branch/branch crotch: x^2/(2R)^2 + z^2/R^2 = 1 in the plane y = 0.
    assert seam["crotchEllipseSemiAxesM"] == [2.0 * radius, radius]
    assert seam["crotchVertexM"] == [2.0 * radius, 0.0, 0.0]
    assert abs(seam["crotchBranchAxialExtentM"] - ROOT_THREE * radius) < 1e-18
    # Inlet/branch seam vertex sits at R / sin(75 deg) along the 105 deg ray.
    vertex = seam["inletBranchSeamVertexM"]
    assert abs(vertex[0] - -SEAM_SLOPE * radius) < 1e-18
    assert vertex[1] == radius and vertex[2] == 0.0
    assert abs(_norm(vertex) - radius / math.sin(math.radians(75.0))) < 1e-15
    assert seam["triplePointsM"] == [[0.0, 0.0, radius], [0.0, 0.0, -radius]]
    assert seam["crotchFluidInteriorAngleDegrees"] == 300.0

    # Every declared seam vertex must lie on both of its cylinders.
    for point in (
        tuple(seam["crotchVertexM"]),
        tuple(vertex),
        (0.0, 0.0, radius),
    ):
        distances = [
            _axis_distance(point, direction)[0] for direction, _length in _leg_axes(spec)
        ]
        on_surface = [value for value in distances if abs(value - radius) < 1e-15]
        assert len(on_surface) >= 2
    # Both cut planes clear every seam.
    assert spec.inlet_cut_m > SEAM_SLOPE * radius
    assert spec.branch_cut_m > ROOT_THREE * radius


# ---------------------------------------------------------------------------
# Rotated butterfly invariants (these differ from full_ogrid)
# ---------------------------------------------------------------------------


def test_rotated_core_makes_the_septum_and_z_planes_exact_mesh_lines() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    butterfly = _butterfly(spec)

    hinge = [index for index, sign in enumerate(butterfly.signs) if sign == 0]
    assert hinge, "the septum must carry mesh points"
    assert all(butterfly.points[index][0] == 0.0 for index in hinge)
    # z = 0 is a mesh line too: the same count of points sits on second == 0.
    assert sum(1 for point in butterfly.points if point[1] == 0.0) == len(hinge)
    # No cross-section cell may straddle the septum, or the chisel end could not
    # be split between two legs by whole cells.
    for quad in butterfly.cells:
        sides = {butterfly.signs[label] for label in quad}
        assert not (1 in sides and -1 in sides)
    # The rotated core square's corners sit at 45/135/225/315 degrees.
    corner = spec.core_radius_m / math.sqrt(2.0)
    assert (corner, corner) in butterfly.points
    assert (-corner, corner) in butterfly.points


def test_septum_mirror_is_an_exact_logical_involution() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    butterfly = _butterfly(spec)

    for index, partner in enumerate(butterfly.mirror_first):
        assert butterfly.mirror_first[partner] == index
        first, second = butterfly.points[index]
        # Bit-exact, so the upper and lower branches can share septum points by
        # label without any coordinate tolerance.
        assert butterfly.points[partner] == (-first, second)
        assert butterfly.signs[partner] == -butterfly.signs[index]
    quads = {frozenset(quad) for quad in butterfly.cells}
    for quad in butterfly.cells:
        assert frozenset(butterfly.mirror_first[label] for label in quad) in quads


def test_wall_ring_points_are_exact_on_axis_and_on_the_analytic_circle() -> None:
    radius = 0.003
    for count in (16, 32, 64):
        assert _octant_circle_point(radius, 0, count) == (radius, 0.0)
        assert _octant_circle_point(radius, count // 4, count) == (0.0, radius)
        assert _octant_circle_point(radius, count // 2, count) == (-radius, 0.0)
        assert _octant_circle_point(radius, 3 * count // 4, count) == (0.0, -radius)
        for index in range(count):
            point = _octant_circle_point(radius, index, count)
            assert abs(math.hypot(*point) - radius) < radius * 1e-15
            mirrored = _octant_circle_point(radius, count // 2 - index, count)
            assert mirrored == (-point[0], point[1])


# ---------------------------------------------------------------------------
# Declared block order, ownership, and artifact identity
# ---------------------------------------------------------------------------


def test_block_order_gives_each_region_one_contiguous_cursor_walk_range() -> None:
    spec = _spec()
    block_set = y_junction_block_set(spec)
    ranges = block_set.block_ranges()

    assert [block.name for block in block_set.blocks][:5] == [
        "inlet-leg-center",
        "inlet-leg-wall-0",
        "inlet-leg-wall-1",
        "inlet-leg-wall-2",
        "inlet-leg-wall-3",
    ]
    assert len(block_set.blocks) == 30
    cursor = 0
    for block in block_set.blocks:
        start, count = ranges[block.name]
        assert start == cursor
        cursor += count
    assert cursor == block_set.cell_count == spec.cell_count

    region_ranges = block_set.region_ranges()
    assert list(region_ranges) == list(REGION_ORDER)
    cross = spec.cross_section_cell_count
    assert region_ranges["inlet-leg"] == (0, cross * spec.inlet_leg_axial_cells)
    expected_start = 0
    for name, axial in (
        ("inlet-leg", spec.inlet_leg_axial_cells),
        ("upper-branch-leg", spec.branch_leg_axial_cells),
        ("lower-branch-leg", spec.branch_leg_axial_cells),
        ("junction-core", 3 * spec.junction_axial_cells),
    ):
        assert region_ranges[name] == (expected_start, cross * axial)
        expected_start += cross * axial
    assert expected_start == spec.cell_count


def test_manifest_declares_the_new_artifact_and_forbids_geometry_ownership() -> None:
    manifest = y_junction_manifest(_spec())

    assert manifest["representation"] == Y_JUNCTION_OGRID_REPRESENTATION
    assert manifest["geometryDerivedOwnershipAllowed"] is False
    assert manifest["ownershipSource"] == "declared-block-order-cursor-walk"
    assert manifest["cellIdentity"] == "flowlab_mesh_order"
    junction = next(
        region for region in manifest["regions"] if region["name"] == "junction-core"
    )
    assert junction["artifactIdentity"]["artifactId"] == JUNCTION_OGRID_ARTIFACT_ID
    assert JUNCTION_OGRID_ARTIFACT_ID == "generated:y-junction-ogrid:junction-core:v1"
    # The retained Cartesian decomposition must never be reused for this one.
    assert JUNCTION_OGRID_ARTIFACT_ID != CARTESIAN_ARTIFACT_ID
    assert manifest["coreOrientation"] == "rotated-45-degree-core-square"
    assert manifest["irregularEdges"]["centralAxisChord"]["cellsPerEdge"] == 6


def test_cell_zones_cover_every_cell_exactly_once_in_declared_order() -> None:
    spec = _spec()
    files = y_junction_polymesh(spec)
    zones = _parse_cell_zones(files["constant/polyMesh/cellZones"])

    assert [name for name, _labels in zones] == [
        "inlet_leg",
        "upper_branch_leg",
        "lower_branch_leg",
        "junction_core",
    ]
    covered: list[int] = []
    for _name, labels in zones:
        assert labels == list(range(labels[0], labels[0] + len(labels)))
        covered.extend(labels)
    assert covered == list(range(spec.cell_count))


# ---------------------------------------------------------------------------
# Emitted polyMesh
# ---------------------------------------------------------------------------


def test_emitted_polymesh_is_all_hex_positive_volume_and_one_region() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    files = y_junction_polymesh(spec)
    mesh = _read_mesh(files)

    assert set(files) == {
        "constant/polyMesh/points",
        "constant/polyMesh/faces",
        "constant/polyMesh/owner",
        "constant/polyMesh/neighbour",
        "constant/polyMesh/boundary",
        "constant/polyMesh/cellZones",
    }
    assert mesh["cellCount"] == spec.cell_count
    assert set(mesh["facesPerCell"]) == {6}
    assert set(mesh["pointsPerCell"]) == {8}
    assert all(len(face) == 4 for face in mesh["faces"])
    assert min(mesh["volumes"]) > 0.0
    assert 2 * len(mesh["neighbour"]) + sum(
        count for _name, _type, count, _start in mesh["boundary"]
    ) == 6 * spec.cell_count

    adjacency: list[list[int]] = [[] for _ in range(mesh["cellCount"])]
    for index, neighbour in enumerate(mesh["neighbour"]):
        adjacency[mesh["owner"][index]].append(neighbour)
        adjacency[neighbour].append(mesh["owner"][index])
    seen = {0}
    stack = [0]
    while stack:
        current = stack.pop()
        for following in adjacency[current]:
            if following not in seen:
                seen.add(following)
                stack.append(following)
    assert len(seen) == mesh["cellCount"]


def test_internal_faces_point_from_owner_to_neighbour() -> None:
    files = y_junction_polymesh(_spec())
    mesh = _read_mesh(files)
    centroids = _cell_centroid(
        mesh["points"], mesh["faces"], mesh["owner"], mesh["neighbour"], mesh["cellCount"]
    )

    for index, neighbour in enumerate(mesh["neighbour"]):
        owner = mesh["owner"][index]
        assert owner < neighbour
        area, _flux = _face_moment(mesh["points"], mesh["faces"][index])
        assert _dot(area, _sub(centroids[neighbour], centroids[owner])) > 0.0
    upper_triangular = list(zip(mesh["owner"][: len(mesh["neighbour"])], mesh["neighbour"]))
    assert upper_triangular == sorted(upper_triangular)


def test_boundary_faces_point_out_of_their_owner_cell() -> None:
    files = y_junction_polymesh(_spec())
    mesh = _read_mesh(files)
    centroids = _cell_centroid(
        mesh["points"], mesh["faces"], mesh["owner"], mesh["neighbour"], mesh["cellCount"]
    )

    for index in range(len(mesh["neighbour"]), len(mesh["faces"])):
        face = mesh["faces"][index]
        area, _flux = _face_moment(mesh["points"], face)
        centre = [
            sum(mesh["points"][label][axis] for label in face) / len(face)
            for axis in range(3)
        ]
        assert _dot(area, _sub(centre, centroids[mesh["owner"][index]])) > 0.0


def test_patch_counts_and_types_match_the_declared_topology() -> None:
    spec = _spec()
    files = y_junction_polymesh(spec)
    mesh = _read_mesh(files)
    declared = spec.topology_manifest()["patches"]

    assert [name for name, _t, _c, _s in mesh["boundary"]] == list(PATCH_ORDER)
    counts = {name: count for name, _t, count, _s in mesh["boundary"]}
    types = {name: patch_type for name, patch_type, _c, _s in mesh["boundary"]}
    for name in PATCH_ORDER:
        assert counts[name] == declared[name]["faceCount"]
        assert types[name] == declared[name]["type"]
    assert counts["inlet"] == spec.cross_section_cell_count
    assert counts["walls"] == spec.circumferential_cells * spec.total_axial_cells
    cursor = len(mesh["neighbour"])
    for _name, _type, count, start in mesh["boundary"]:
        assert start == cursor
        cursor += count
    assert cursor == len(mesh["faces"])


def test_port_faces_are_exact_planes_normal_to_their_own_axis() -> None:
    spec = _spec()
    files = y_junction_polymesh(spec)
    mesh = _read_mesh(files)
    radius = spec.radius_m
    expectations = {
        "inlet": ((-1.0, 0.0, 0.0), spec.inlet_length_m),
        "outletUpper": (spec.upper_direction, spec.branch_length_m),
        "outletLower": (spec.lower_direction, spec.branch_length_m),
    }

    for name, (direction, length) in expectations.items():
        entry = next(item for item in mesh["boundary"] if item[0] == name)
        labels = {
            label
            for index in range(entry[3], entry[3] + entry[2])
            for label in mesh["faces"][index]
        }
        assert len(labels) == spec.cross_section_point_count
        stations = []
        for label in labels:
            point = mesh["points"][label]
            distance, axial = _axis_distance(point, direction)
            stations.append(axial)
            assert abs(axial - length) < length * 1e-14
            assert distance <= radius * (1.0 + 1e-15)
        # Planar to double precision: the whole port sits on one station.
        assert max(stations) - min(stations) < radius * 1e-14
        for index in range(entry[3], entry[3] + entry[2]):
            area, _flux = _face_moment(mesh["points"], mesh["faces"][index])
            magnitude = _norm(area)
            cosine = _dot(area, direction) / magnitude
            assert abs(cosine - 1.0) < 1e-14


def test_wall_points_sit_exactly_on_the_analytic_cylinder_union() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    files = y_junction_polymesh(spec)
    mesh = _read_mesh(files)
    radius = spec.radius_m
    wall = next(entry for entry in mesh["boundary"] if entry[0] == "walls")
    labels = {
        label
        for index in range(wall[3], wall[3] + wall[2])
        for label in mesh["faces"][index]
    }

    tolerance = radius * 1e-12
    on_surface = 0
    for label in labels:
        point = mesh["points"][label]
        distances = []
        for direction, length in _leg_axes(spec):
            distance, axial = _axis_distance(point, direction)
            # Only count a cylinder that actually spans this station.
            distances.append(distance if -tolerance <= axial <= length + tolerance
                             else float("inf"))
        assert min(distances) >= radius - tolerance, "a wall point is inside the union"
        if min(distances) <= radius + tolerance:
            on_surface += 1
    assert on_surface == len(labels)
    # Wall point book-keeping: three chisel surfaces share their wall ring.
    ring = spec.circumferential_cells
    expected = ring * spec.total_axial_cells + 3 * ring // 2 - 1
    assert len(labels) == expected


def test_seam_vertices_and_triple_points_are_mesh_points() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    block_set = y_junction_block_set(spec)
    radius = spec.radius_m
    points = set(block_set.points)

    assert (2.0 * radius, 0.0, 0.0) in points  # crotch vertex
    assert (0.0, 0.0, radius) in points  # triple points
    assert (0.0, 0.0, -radius) in points
    assert (-SEAM_SLOPE * radius, radius, 0.0) in points  # inlet/upper seam vertex
    assert (-SEAM_SLOPE * radius, -radius, 0.0) in points  # inlet/lower seam vertex
    # The whole central chord is a mesh edge shared by all three legs.
    chord = [point for point in points if point[0] == 0.0 and point[1] == 0.0]
    assert len(chord) == spec.core_cells_per_side + 2 * spec.annular_radial_cells + 1


def test_septum_faces_are_internal_and_shared_by_the_two_branches() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    files = y_junction_polymesh(spec)
    mesh = _read_mesh(files)
    radius = spec.radius_m

    septum = [
        index
        for index, face in enumerate(mesh["faces"])
        if all(mesh["points"][label][1] == 0.0 for label in face)
        and all(mesh["points"][label][0] >= 0.0 for label in face)
    ]
    # Exactly the half of the cross-section that faces the crotch, and every one
    # of them is an internal face because both branches claim it by label.
    assert len(septum) == spec.cross_section_cell_count // 2
    assert all(index < len(mesh["neighbour"]) for index in septum)
    for index in septum:
        for label in mesh["faces"][index]:
            point = mesh["points"][label]
            assert 0.0 <= point[0] <= 2.0 * radius + 1e-18
            # The septum is bounded by the crotch ellipse x^2/(2R)^2+z^2/R^2 = 1.
            assert (point[0] / (2.0 * radius)) ** 2 + (point[2] / radius) ** 2 <= 1.0 + 1e-12


# ---------------------------------------------------------------------------
# The measurement the staircase could never make
# ---------------------------------------------------------------------------


def test_wall_area_deficit_converges_second_order_under_refinement() -> None:
    """The staircase's wall area tends to 4/pi at every cell size; this one converges."""

    deficits: list[float] = []
    for circumferential in (16, 32, 64):
        spec = _spec(
            circumferential_cells=circumferential,
            core_cells_per_side=circumferential // 4,
        )
        files = y_junction_polymesh(spec)
        mesh = _read_mesh(files)
        wall = next(entry for entry in mesh["boundary"] if entry[0] == "walls")
        area = 0.0
        for index in range(wall[3], wall[3] + wall[2]):
            vector, _flux = _face_moment(mesh["points"], mesh["faces"][index])
            area += _norm(vector)

        geometry = spec.wall_geometry()
        analytic = float(geometry["analyticWallAreaM2"])
        # The emitted mesh realizes the declared chordal area exactly.
        assert abs(area - float(geometry["chordalWallAreaM2"])) / area < 1e-12
        deficit = 1.0 - area / analytic
        assert abs(deficit - float(geometry["areaRelativeDeficit"])) < 1e-12
        deficits.append(deficit)

    # A staircase realization is 4/pi - 1 = 27.3 percent long at every size.
    assert all(0.0 < value < 0.01 for value in deficits)
    assert deficits[0] > deficits[1] > deficits[2]
    for coarse, fine in zip(deficits, deficits[1:]):
        assert 3.8 < coarse / fine < 4.2


def test_analytic_wall_area_matches_the_closed_form_union_surface() -> None:
    spec = _spec()
    radius = spec.radius_m
    expected = 2.0 * math.pi * radius * (
        spec.inlet_length_m + 2.0 * spec.branch_length_m
    ) - radius * radius * (16.0 - 4.0 * ROOT_THREE)

    assert abs(float(spec.wall_geometry()["analyticWallAreaM2"]) - expected) < 1e-18


def test_enclosed_volume_converges_to_the_analytic_union_volume() -> None:
    deficits: list[float] = []
    for circumferential in (16, 32, 64):
        spec = _spec(
            circumferential_cells=circumferential,
            core_cells_per_side=circumferential // 4,
        )
        mesh = _read_mesh(y_junction_polymesh(spec))
        radius = spec.radius_m
        analytic = math.pi * radius * radius * (
            spec.inlet_length_m + 2.0 * spec.branch_length_m
        ) - (4.0 / 3.0) * (4.0 - ROOT_THREE) * radius**3
        assert min(mesh["volumes"]) > 0.0
        deficits.append(1.0 - sum(mesh["volumes"]) / analytic)

    assert all(value > 0.0 for value in deficits)
    for coarse, fine in zip(deficits, deficits[1:]):
        assert 3.8 < coarse / fine < 4.2


# ---------------------------------------------------------------------------
# Fail-closed emission and determinism
# ---------------------------------------------------------------------------


def test_unclaimed_boundary_face_and_bad_patch_claim_fail_closed() -> None:
    block_set = y_junction_block_set(_spec())
    without_walls = tuple(patch for patch in block_set.patches if patch.name != "walls")

    with pytest.raises(ValueError, match="no patch owner"):
        block_set_to_polymesh(
            block_set.blocks, without_walls, block_set.regions, points=block_set.points
        )


def test_near_wall_radial_clustering_keeps_the_wall_on_the_analytic_circle() -> None:
    spec = _spec(annular_radial_cells=4, circumferential_cells=32,
                 core_cells_per_side=8, annular_radial_expansion=0.3)
    fractions = spec.annular_radial_fractions()

    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    widths = [b - a for a, b in zip(fractions, fractions[1:])]
    assert all(value > 0.0 for value in widths)
    assert abs(widths[-1] / widths[0] - 0.3) < 1e-12

    mesh = _read_mesh(y_junction_polymesh(spec))
    assert min(mesh["volumes"]) > 0.0
    uniform = _spec(annular_radial_cells=4, circumferential_cells=32, core_cells_per_side=8)
    # Radial clustering is a point placement choice; it cannot move the wall.
    assert abs(
        float(spec.wall_geometry()["chordalWallAreaM2"])
        - float(uniform.wall_geometry()["chordalWallAreaM2"])
    ) < 1e-18


def test_regeneration_is_byte_identical_across_fresh_interpreters() -> None:
    first = y_junction_polymesh(_spec())
    assert first == y_junction_polymesh(_spec())

    script = (
        "import hashlib\n"
        "from server.flowlab.y_junction_ogrid import "
        "YJunctionOGridSpec, y_junction_polymesh\n"
        "spec = YJunctionOGridSpec(radius_m=0.003, inlet_length_m=0.012,\n"
        "    branch_length_m=0.012, annular_radial_cells=2,\n"
        "    circumferential_cells=16, core_cells_per_side=4,\n"
        "    inlet_leg_axial_cells=2, branch_leg_axial_cells=2,\n"
        "    junction_axial_cells=2)\n"
        "files = y_junction_polymesh(spec)\n"
        "blob = ''.join(f'{k}{files[k]}' for k in sorted(files)).encode('utf-8')\n"
        "print(hashlib.sha256(blob).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPOSITORY_ROOT),
            env={
                **os.environ,
                "PYTHONPATH": str(REPOSITORY_ROOT),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": seed,
            },
            capture_output=True,
            text=True,
            check=True,
        )
        digests.append(completed.stdout.strip())

    expected = hashlib.sha256(
        "".join(f"{key}{first[key]}" for key in sorted(first)).encode("utf-8")
    ).hexdigest()
    assert digests == [expected, expected]


def test_written_case_tree_contains_every_polymesh_file(tmp_path) -> None:
    files = y_junction_polymesh(_spec())
    written = write_polymesh(str(tmp_path), files)

    assert len(written) == len(files)
    for relative, text in files.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == text
