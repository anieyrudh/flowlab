"""Contract tests for the direct-polyMesh straight-pipe O-grid (Milestone 0).

These tests read the emitted ``constant/polyMesh`` text back and rebuild the
geometry from it, so they check the artifact that OpenFOAM would consume rather
than the in-memory intermediate.
"""

import hashlib
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.flowlab.full_ogrid import FullOGridSpec, _cross_section
from server.flowlab.ogrid_polymesh import (
    OGRID_POLYMESH_REPRESENTATION,
    OGridBlockSet,
    OGridFrame,
    OGridPatch,
    OGridRegion,
    block_set_to_polymesh,
    straight_pipe_block_set,
    swept_ogrid_block_set,
    write_polymesh,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _spec(**overrides) -> FullOGridSpec:
    fields = {
        "length_m": 0.024,
        "radius_m": 0.006,
        "axial_cells": 16,
        "annular_radial_cells": 4,
        "circumferential_cells": 32,
        "core_cells_per_side": 8,
    }
    fields.update(overrides)
    return FullOGridSpec(**fields)


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
    pattern = (
        r"(\w+)\s*\{\s*type\s+(\w+);\s*nFaces\s+(\d+);\s*startFace\s+(\d+);\s*\}"
    )
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


def _face_moment(points, face):
    """Return ``(outward area vector, integral of x.n over the face)``.

    The face is decomposed into a triangle fan, so the result is exact for the
    polyhedron actually described by the face list rather than an approximation.
    """

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


def test_frame_from_axis_is_orthonormal_and_right_handed() -> None:
    frame = OGridFrame.from_axis((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

    assert frame.tangent == (1.0, 0.0, 0.0)
    assert frame.normal == (0.0, 1.0, 0.0)
    assert frame.binormal == (0.0, 0.0, 1.0)
    assert frame.point(0.5, 0.25, -0.125) == (0.5, 0.25, -0.125)

    oblique = OGridFrame.from_axis((1.0, 2.0, 3.0), (1.0, 2.0, -3.0))
    for axis in (oblique.tangent, oblique.normal, oblique.binormal):
        assert abs(math.sqrt(_dot(axis, axis)) - 1.0) < 1e-15
    assert abs(_dot(oblique.tangent, oblique.normal)) < 1e-15
    assert abs(_dot(oblique.tangent, oblique.binormal)) < 1e-15
    assert abs(_dot(oblique.normal, oblique.binormal)) < 1e-15


def test_frame_fails_closed_on_left_handed_and_degenerate_input() -> None:
    with pytest.raises(ValueError, match="right-handed"):
        OGridFrame(
            origin=(0.0, 0.0, 0.0),
            tangent=(1.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            binormal=(0.0, 1.0, 0.0),
        )
    with pytest.raises(ValueError, match="unit vector"):
        OGridFrame(
            origin=(0.0, 0.0, 0.0),
            tangent=(2.0, 0.0, 0.0),
            normal=(0.0, 1.0, 0.0),
            binormal=(0.0, 0.0, 1.0),
        )
    with pytest.raises(ValueError, match="non-zero direction"):
        OGridFrame.from_axis((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def test_swept_block_set_counts_match_the_declared_spec() -> None:
    spec = _spec()
    block_set = straight_pipe_block_set(spec)
    cross_points, cross_cells, _areas = _cross_section(spec)

    assert isinstance(block_set, OGridBlockSet)
    assert block_set.cell_count == spec.cell_count == 3072
    assert len(block_set.points) == (spec.axial_cells + 1) * len(cross_points) == 3553
    assert [block.name for block in block_set.blocks] == [
        "center",
        "wall-0",
        "wall-1",
        "wall-2",
        "wall-3",
    ]
    assert sum(len(block.cells) for block in block_set.blocks) == spec.axial_cells * len(
        cross_cells
    )
    manifest = block_set.manifest()
    assert manifest["representation"] == OGRID_POLYMESH_REPRESENTATION
    assert manifest["cellIdentity"] == "flowlab_mesh_order"


def test_swept_block_set_rejects_inconsistent_stations() -> None:
    spec = _spec(axial_cells=4)
    frame = OGridFrame.from_axis((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

    with pytest.raises(ValueError, match="axialCells"):
        swept_ogrid_block_set(frame, [0.0, 1.0, 2.0], spec)
    with pytest.raises(ValueError, match="strictly increasing"):
        swept_ogrid_block_set(frame, [0.0, 1.0, 1.0, 3.0, 4.0], spec)


def test_non_uniform_stations_and_oblique_frames_stay_positive_volume() -> None:
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    frame = OGridFrame.from_axis((0.01, -0.02, 0.03), (1.0, 2.0, -3.0))
    stations = [0.0, 0.002, 0.006, 0.014, 0.024]
    block_set = swept_ogrid_block_set(frame, stations, spec, region_name="leg")
    mesh = _read_mesh(block_set.to_polymesh())

    assert min(mesh["volumes"]) > 0.0
    assert set(mesh["facesPerCell"]) == {6}
    # A rigid frame plus a graded sweep must preserve the total swept volume.
    expected = float(spec.wall_geometry()["polygonAreaM2"]) * (stations[-1] - stations[0])
    assert abs(sum(mesh["volumes"]) - expected) / expected < 1e-12
    assert block_set.region_ranges() == {"leg": (0, spec.cell_count)}


def test_block_and_region_ranges_are_contiguous_and_non_overlapping() -> None:
    block_set = straight_pipe_block_set(_spec())
    ranges = block_set.block_ranges()

    cursor = 0
    for block in block_set.blocks:
        start, count = ranges[block.name]
        assert start == cursor
        assert count == len(block.cells)
        cursor += count
    assert cursor == block_set.cell_count

    split = OGridBlockSet(
        points=block_set.points,
        blocks=block_set.blocks,
        patches=block_set.patches,
        regions=(
            OGridRegion(name="core", block_names=("center",)),
            OGridRegion(name="annulus", block_names=("wall-0", "wall-1", "wall-2", "wall-3")),
        ),
    )
    region_ranges = split.region_ranges()
    assert region_ranges["core"] == (0, 1024)
    assert region_ranges["annulus"] == (1024, 2048)

    with pytest.raises(ValueError, match="consecutive"):
        OGridBlockSet(
            points=block_set.points,
            blocks=block_set.blocks,
            patches=block_set.patches,
            regions=(
                OGridRegion(name="split", block_names=("center", "wall-1")),
                OGridRegion(name="rest", block_names=("wall-0", "wall-2", "wall-3")),
            ),
        ).region_ranges()
    with pytest.raises(ValueError, match="exactly one region"):
        OGridBlockSet(
            points=block_set.points,
            blocks=block_set.blocks,
            patches=block_set.patches,
            regions=(OGridRegion(name="partial", block_names=("center",)),),
        ).region_ranges()


def test_emitted_polymesh_is_all_hex_with_one_connected_cell_zone() -> None:
    spec = _spec()
    block_set = straight_pipe_block_set(spec)
    files = block_set.to_polymesh()
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
    assert len(mesh["faces"]) == len(mesh["owner"])
    # Every cell-face incidence is either shared once or claimed by one patch.
    assert 2 * len(mesh["neighbour"]) + sum(
        count for _name, _type, count, _start in mesh["boundary"]
    ) == 6 * spec.cell_count

    zones = _parse_cell_zones(files["constant/polyMesh/cellZones"])
    assert [name for name, _labels in zones] == ["pipe"]
    assert zones[0][1] == list(range(spec.cell_count))


def test_emitted_polymesh_cells_all_have_positive_volume() -> None:
    spec = _spec()
    files = straight_pipe_block_set(spec).to_polymesh()
    mesh = _read_mesh(files)

    assert min(mesh["volumes"]) > 0.0
    total = sum(mesh["volumes"])
    expected = float(spec.wall_geometry()["polygonAreaM2"]) * spec.length_m
    assert abs(total - expected) / expected < 1e-12


def test_internal_faces_point_from_owner_to_neighbour() -> None:
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    files = straight_pipe_block_set(spec).to_polymesh()
    mesh = _read_mesh(files)
    centroids = _cell_centroid(
        mesh["points"], mesh["faces"], mesh["owner"], mesh["neighbour"], mesh["cellCount"]
    )

    for index, neighbour in enumerate(mesh["neighbour"]):
        owner = mesh["owner"][index]
        assert owner < neighbour
        area, _flux = _face_moment(mesh["points"], mesh["faces"][index])
        offset = _sub(centroids[neighbour], centroids[owner])
        assert _dot(area, offset) > 0.0

    upper_triangular = list(zip(mesh["owner"][: len(mesh["neighbour"])], mesh["neighbour"]))
    assert upper_triangular == sorted(upper_triangular)


def test_boundary_faces_point_out_of_their_owner_cell() -> None:
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    files = straight_pipe_block_set(spec).to_polymesh()
    mesh = _read_mesh(files)
    centroids = _cell_centroid(
        mesh["points"], mesh["faces"], mesh["owner"], mesh["neighbour"], mesh["cellCount"]
    )

    for index in range(len(mesh["neighbour"]), len(mesh["faces"])):
        face = mesh["faces"][index]
        area, _flux = _face_moment(mesh["points"], face)
        face_centre = [
            sum(mesh["points"][label][axis] for label in face) / len(face)
            for axis in range(3)
        ]
        offset = _sub(face_centre, centroids[mesh["owner"][index]])
        assert _dot(area, offset) > 0.0


def test_patch_face_counts_and_types_match_the_analytic_topology() -> None:
    spec = _spec()
    files = straight_pipe_block_set(spec).to_polymesh()
    mesh = _read_mesh(files)
    topology = spec.topology_manifest()["patches"]

    assert [name for name, _type, _count, _start in mesh["boundary"]] == [
        "inlet",
        "outlet",
        "walls",
    ]
    counts = {name: count for name, _type, count, _start in mesh["boundary"]}
    types = {name: patch_type for name, patch_type, _count, _start in mesh["boundary"]}
    assert counts["inlet"] == topology["inlet"]["faceCount"] == spec.cross_section_cell_count
    assert counts["outlet"] == topology["outlet"]["faceCount"]
    assert counts["walls"] == topology["walls"]["faceCount"]
    assert counts["walls"] == spec.circumferential_cells * spec.axial_cells
    assert types == {"inlet": "patch", "outlet": "patch", "walls": "wall"}

    cursor = len(mesh["neighbour"])
    for _name, _type, count, start in mesh["boundary"]:
        assert start == cursor
        cursor += count
    assert cursor == len(mesh["faces"])


def test_wall_points_sit_exactly_on_the_analytic_circle() -> None:
    spec = _spec()
    block_set = straight_pipe_block_set(spec)
    files = block_set.to_polymesh()
    mesh = _read_mesh(files)
    wall = next(entry for entry in mesh["boundary"] if entry[0] == "walls")
    labels = {
        label
        for index in range(wall[3], wall[3] + wall[2])
        for label in mesh["faces"][index]
    }

    radii = [
        math.hypot(mesh["points"][label][1], mesh["points"][label][2])
        for label in labels
    ]
    assert len(radii) == spec.circumferential_cells * (spec.axial_cells + 1)
    assert max(abs(value - spec.radius_m) for value in radii) < spec.radius_m * 1e-12


def test_unclaimed_boundary_face_and_bad_patch_claim_fail_closed() -> None:
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    block_set = straight_pipe_block_set(spec)
    without_walls = tuple(
        patch for patch in block_set.patches if patch.name != "walls"
    )

    with pytest.raises(ValueError, match="no patch owner"):
        block_set_to_polymesh(
            block_set.blocks,
            without_walls,
            block_set.regions,
            points=block_set.points,
        )

    interior_claim = (
        OGridPatch(name="bogus", type="patch", faces=(("center", 0, 1),)),
        *block_set.patches,
    )
    with pytest.raises(ValueError, match="not an unclaimed"):
        block_set_to_polymesh(
            block_set.blocks,
            interior_claim,
            block_set.regions,
            points=block_set.points,
        )


def test_regeneration_is_byte_identical_across_fresh_interpreters() -> None:
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    first = straight_pipe_block_set(spec).to_polymesh()
    second = straight_pipe_block_set(_spec(axial_cells=4, annular_radial_cells=2,
                                           circumferential_cells=16,
                                           core_cells_per_side=4)).to_polymesh()
    assert first == second

    script = (
        "import hashlib\n"
        "from server.flowlab.full_ogrid import FullOGridSpec\n"
        "from server.flowlab.ogrid_polymesh import straight_pipe_block_set\n"
        "spec = FullOGridSpec(length_m=0.024, radius_m=0.006, axial_cells=4,\n"
        "                     annular_radial_cells=2, circumferential_cells=16,\n"
        "                     core_cells_per_side=4)\n"
        "files = straight_pipe_block_set(spec).to_polymesh()\n"
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
    spec = _spec(axial_cells=4, annular_radial_cells=2, circumferential_cells=16,
                 core_cells_per_side=4)
    files = straight_pipe_block_set(spec).to_polymesh()
    written = write_polymesh(str(tmp_path), files)

    assert len(written) == len(files)
    for relative, text in files.items():
        assert (tmp_path / relative).read_text(encoding="utf-8") == text


def test_wall_area_deficit_shrinks_as_circumferential_cells_increase() -> None:
    exact = math.pi * 0.006**2
    deficits: list[float] = []
    for circumferential in (16, 32, 64):
        spec = _spec(
            axial_cells=4,
            annular_radial_cells=2,
            circumferential_cells=circumferential,
            core_cells_per_side=circumferential // 4,
        )
        files = straight_pipe_block_set(spec).to_polymesh()
        mesh = _read_mesh(files)
        mesh_area = sum(mesh["volumes"]) / spec.length_m
        geometry = spec.wall_geometry()

        assert min(mesh["volumes"]) > 0.0
        assert geometry["wallFacetCount"] == circumferential
        assert abs(mesh_area - float(geometry["polygonAreaM2"])) / mesh_area < 1e-12
        deficit = 1.0 - mesh_area / exact
        assert abs(deficit - float(geometry["areaRelativeDeficit"])) < 1e-12
        deficits.append(deficit)

    assert all(value > 0.0 for value in deficits)
    assert deficits[0] > deficits[1] > deficits[2]
    # Chordal area deficit is second order in the facet angle: each doubling of
    # the circumferential count must cut it by roughly four.
    for coarse, fine in zip(deficits, deficits[1:]):
        assert 3.5 < coarse / fine < 4.5
