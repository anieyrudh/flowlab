from pathlib import Path

import pytest

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint
from server.flowlab.layered_immutable_volume import (
    LayeredVolumeConfig,
    _write_transition_region_msh,
    build_coarsened_core_interface,
    build_transition_shell,
    build_layered_volume,
    read_frozen_surface,
    volume_strategy,
)


def _master(tmp_path: Path) -> Path:
    path = tmp_path / "surface.msh"
    write_cylinder_surface_master(
        path,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=8,
        axial_cells=3,
    )
    return path


def test_transition_shell_is_deterministic_and_preserves_outer_nodes(tmp_path: Path) -> None:
    surface = read_frozen_surface(_master(tmp_path))
    config = LayeredVolumeConfig(0.0001, 2, 1.2, 0.001)

    first = build_transition_shell(surface, config)
    second = build_transition_shell(surface, config)

    assert first == second
    added, prisms, final_mapping = first
    assert len(prisms) == len(surface.triangles) * config.layer_count
    assert all(len(prism) == 6 for prism in prisms)
    assert set(final_mapping) == set(surface.nodes)
    assert not set(surface.nodes).intersection(added)
    assert all(tag >= 10_000_000 or tag > max(surface.nodes) for tag in added)


def test_transition_shell_rejects_closed_cavity(tmp_path: Path) -> None:
    surface = read_frozen_surface(_master(tmp_path))
    with pytest.raises(ValueError, match="closes the inner cavity"):
        build_transition_shell(surface, LayeredVolumeConfig(0.003, 2, 1.0, 0.001))


def test_transition_shell_rejects_unbounded_half_radius_screen(tmp_path: Path) -> None:
    surface = read_frozen_surface(_master(tmp_path))
    with pytest.raises(ValueError, match="half-radius screen"):
        build_transition_shell(surface, LayeredVolumeConfig(0.0015, 2, 1.0, 0.001))


def test_coarsened_core_interface_is_internal_deterministic_and_non_overlapping(tmp_path: Path) -> None:
    surface = read_frozen_surface(_master(tmp_path))
    config = LayeredVolumeConfig(
        0.0001, 2, 1.2, 0.001,
        core_interface_chords=16,
        transition_thickness_m=0.0005,
    )
    shell_nodes, _, _ = build_transition_shell(surface, config)
    first_tag = max(shell_nodes) + 1
    first = build_coarsened_core_interface(surface, config, first_node_tag=first_tag, directory=tmp_path / "first")
    second = build_coarsened_core_interface(surface, config, first_node_tag=first_tag, directory=tmp_path / "second")

    assert first == second
    assert set(first.nodes).isdisjoint(surface.nodes)
    assert set(first.nodes).isdisjoint(shell_nodes)
    assert min(first.nodes) == first_tag
    outer_radius = max((x * x + y * y) ** 0.5 for x, y, _ in surface.nodes.values())
    inner_radius = max((x * x + y * y) ** 0.5 for x, y, _ in first.nodes.values())
    assert inner_radius < outer_radius
    # The test master is intentionally only eight chords; production v2 is
    # 256 chords.  Verify the deterministic selected internal topology here
    # rather than comparing unlike source resolutions.
    assert len(first.triangles) > 0
    assert len(first.triangles) == len(second.triangles)


def test_transition_surface_keeps_interfaces_separate_and_inner_reversed(tmp_path: Path) -> None:
    surface = read_frozen_surface(_master(tmp_path))
    config = LayeredVolumeConfig(
        0.0001, 2, 1.2, 0.001,
        core_interface_chords=16,
        transition_thickness_m=0.0005,
    )
    shell_nodes, _, final_mapping = build_transition_shell(surface, config)
    outer_nodes = {final_mapping[outer]: shell_nodes[final_mapping[outer]] for outer in surface.nodes}
    outer_triangles = tuple(
        type(triangle)(triangle.physical_id, tuple(final_mapping[node] for node in triangle.vertices))
        for triangle in surface.triangles
    )
    inner = build_coarsened_core_interface(
        surface, config, first_node_tag=max(shell_nodes) + 1, directory=tmp_path / "interface"
    )
    path = tmp_path / "transition.msh"
    _write_transition_region_msh(path, outer_nodes=outer_nodes, outer_triangles=outer_triangles, inner=inner)
    rows = path.read_text(encoding="utf-8").split("$Elements\n", 1)[1].splitlines()
    element_rows = rows[1 : 1 + len(outer_triangles) + len(inner.triangles)]
    assert all(row.split()[3] in {"111", "112", "113"} for row in element_rows[: len(outer_triangles)])
    assert all(row.split()[3] in {"211", "212", "213"} for row in element_rows[len(outer_triangles) :])
    inner_row = element_rows[len(outer_triangles)].split()
    assert tuple(map(int, inner_row[-3:])) == (inner.triangles[0].vertices[0], inner.triangles[0].vertices[2], inner.triangles[0].vertices[1])


def test_coarsened_interface_requires_explicit_complete_strategy() -> None:
    with pytest.raises(ValueError, match="requires both chords and transition thickness"):
        LayeredVolumeConfig(0.0001, 2, 1.2, 0.001, core_interface_chords=16)
    with pytest.raises(ValueError, match="multiple of eight"):
        LayeredVolumeConfig(0.0001, 2, 1.2, 0.001, core_interface_chords=10, transition_thickness_m=0.0005)


def test_coarsened_strategy_is_explicit_for_screen_rebinding() -> None:
    assert volume_strategy(LayeredVolumeConfig(0.0001, 2, 1.2, 0.001)) == {
        "id": "full-density-shell-interface",
        "version": "v1",
        "algorithm3D": 4,
        "optimizeNetgen": True,
        "smoothingSteps": 20,
    }
    assert volume_strategy(
        LayeredVolumeConfig(
            0.0001, 2, 1.2, 0.001,
            core_interface_chords=16,
            transition_thickness_m=0.0005,
        )
    ) == {
        "id": "coarsened-inner-interface",
        "version": "v1",
        "algorithm3D": 4,
        "optimizeNetgen": True,
        "smoothingSteps": 20,
        "interfaceChords": 16,
        "transitionThicknessM": 0.0005,
    }


def test_coarsened_annular_transition_rejects_algorithm_one() -> None:
    with pytest.raises(ValueError, match="Algorithm3D=1 is permanently rejected"):
        LayeredVolumeConfig(
            0.0001, 4, 1.15, 0.001,
            algorithm_3d=1,
            core_interface_chords=32,
            transition_thickness_m=0.0005,
        )


def test_internal_strategy_identity_and_gmsh_controls_are_explicit() -> None:
    strategy = volume_strategy(
        LayeredVolumeConfig(
            0.0001, 4, 1.15, 0.001,
            algorithm_3d=4,
            optimize_netgen=True,
            smoothing_steps=30,
            core_interface_chords=32,
            transition_thickness_m=0.0005,
            volume_strategy_id="frontal-dense-core",
            volume_strategy_version="v6",
        )
    )

    assert strategy == {
        "id": "frontal-dense-core",
        "version": "v6",
        "algorithm3D": 4,
        "optimizeNetgen": True,
        "smoothingSteps": 30,
        "interfaceChords": 32,
        "transitionThicknessM": 0.0005,
    }


def test_surface_contract_rejects_missing_patch(tmp_path: Path) -> None:
    path = _master(tmp_path)
    text = path.read_text(encoding="utf-8").replace('2 12 "outlet"', '2 99 "outlet"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="declare inlet/outlet/wall"):
        read_frozen_surface(path)


def test_builder_rejects_mismatched_declared_master_hash_before_gmsh(tmp_path: Path) -> None:
    master = _master(tmp_path)
    config = LayeredVolumeConfig(0.0001, 2, 1.2, 0.001)

    with pytest.raises(ValueError, match="declared frozen-surface SHA-256"):
        build_layered_volume(
            master,
            tmp_path / "volume.msh",
            config,
            core_debug_msh=tmp_path / "core-debug.msh",
            expected_surface_sha256="0" * 64,
        )

    assert msh2_surface_fingerprint(master)["surfaceSha256"] != "0" * 64
    assert not (tmp_path / "volume.msh").exists()
