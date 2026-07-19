from collections import Counter
from pathlib import Path

import pytest

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.cad_cylinder_surface_master_v2 import (
    SurfaceQualityTargets,
    freeze_report,
    recommended_axial_cells,
    write_cylinder_surface_master_v2,
)
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint
from server.flowlab.layered_immutable_volume import read_frozen_surface


def _write_v2(path: Path):
    return write_cylinder_surface_master_v2(
        path,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=256,
    )


def test_v2_is_deterministic_patch_partitioned_and_quality_accepted(tmp_path: Path) -> None:
    first, second = tmp_path / "first.msh", tmp_path / "second.msh"
    first_quality = _write_v2(first)
    second_quality = _write_v2(second)

    assert first.read_bytes() == second.read_bytes()
    assert first_quality == second_quality
    assert first_quality.accepted
    assert first_quality.maximum_wall_edge_ratio <= first_quality.targets.max_wall_edge_ratio
    assert first_quality.maximum_cap_edge_ratio <= first_quality.targets.max_cap_edge_ratio
    fingerprint = msh2_surface_fingerprint(first)
    assert fingerprint["trianglesByPhysicalId"] == {
        "11": 8192,
        "12": 8192,
        "13": 208384,
    }
    assert fingerprint["surfaceTriangles"] == 224768
    assert freeze_report(first, first_quality)["fingerprint"] == fingerprint
    frozen = read_frozen_surface(first)
    assert len(frozen.nodes) == 112386
    assert len(frozen.triangles) == fingerprint["surfaceTriangles"]


def test_v2_default_axial_resolution_matches_outer_chord() -> None:
    assert recommended_axial_cells(length_m=0.05, radius_m=0.005, circumferential_chords=256) == 407


def test_v2_is_consistently_oriented_closed_surface(tmp_path: Path) -> None:
    path = tmp_path / "surface.msh"
    _write_v2(path)
    surface = read_frozen_surface(path)
    directions = Counter(
        (first, second)
        for triangle in surface.triangles
        for first, second in zip(triangle.vertices, triangle.vertices[1:] + triangle.vertices[:1])
    )
    for first, second in {tuple(sorted(edge)) for edge in directions}:
        assert directions[(first, second)] == 1
        assert directions[(second, first)] == 1


def test_v2_rejects_an_explicitly_elongated_wall_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "rejected.msh"
    with pytest.raises(ValueError, match="wall triangle edge ratio"):
        write_cylinder_surface_master_v2(
            output,
            length_m=0.05,
            radius_m=0.005,
            circumferential_chords=256,
            axial_cells=40,
        )
    assert not output.exists()


def test_v2_preserves_v1_api_and_does_not_change_its_master(tmp_path: Path) -> None:
    v1, v2 = tmp_path / "v1.msh", tmp_path / "v2.msh"
    write_cylinder_surface_master(
        v1,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=8,
        axial_cells=3,
    )
    write_cylinder_surface_master_v2(
        v2,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=8,
        axial_cells=3,
        cap_ring_count=1,
        quality_targets=SurfaceQualityTargets(
            max_wall_edge_ratio=20.0,
            max_cap_edge_ratio=2.0,
            max_relative_cylinder_area_error=0.1,
        ),
    )
    assert msh2_surface_fingerprint(v1)["trianglesByPhysicalId"] == {"11": 8, "12": 8, "13": 48}
    assert msh2_surface_fingerprint(v2)["trianglesByPhysicalId"] == {"11": 8, "12": 8, "13": 48}
