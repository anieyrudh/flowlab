import hashlib
import json
import math

import pytest

from server.flowlab.full_ogrid import (
    FULL_OGRID_PATH_PREVIEW_FORMAT,
    FULL_OGRID_PATH_REPRESENTATION,
    FULL_OGRID_PREVIEW_FORMAT,
    FULL_OGRID_REPRESENTATION,
    FullOGridPathSegment,
    FullOGridPathSpec,
    FullOGridSpec,
    block_mesh_dict,
    path_block_mesh_dict,
    path_preview_mesh,
    preview_mesh,
)


def _spec() -> FullOGridSpec:
    return FullOGridSpec(
        length_m=0.024,
        radius_m=0.006,
        axial_cells=16,
        annular_radial_cells=4,
        circumferential_cells=32,
        core_cells_per_side=8,
    )


def test_full_ogrid_spec_exposes_conformal_five_block_topology() -> None:
    spec = _spec()
    topology = spec.topology_manifest()

    assert topology["representation"] == FULL_OGRID_REPRESENTATION
    assert topology["spatialDimension"] == 3
    assert topology["cellTypes"] == ["hex"]
    assert topology["blockCount"] == 5
    assert [block["role"] for block in topology["blocks"]] == [
        "core",
        "circumferential-wall",
        "circumferential-wall",
        "circumferential-wall",
        "circumferential-wall",
    ]
    assert topology["interfaces"] == {
        "count": 4,
        "treatment": "conformal-internal-faces",
        "boundaryPatchCount": 0,
        "faceCount": 512,
    }
    assert set(topology["patches"]) == {"inlet", "outlet", "walls"}
    assert topology["patches"]["walls"]["type"] == "wall"
    assert topology["collapsedAxisCells"] == 0
    assert topology["resolution"]["cellCount"] == 3072


def test_full_ogrid_resolution_fails_closed_on_nonconformal_interface_counts() -> None:
    with pytest.raises(ValueError, match="circumferentialCells/4"):
        FullOGridSpec(
            length_m=0.024,
            radius_m=0.006,
            axial_cells=16,
            annular_radial_cells=4,
            circumferential_cells=32,
            core_cells_per_side=7,
        )

    with pytest.raises(ValueError, match="divisible by four"):
        FullOGridSpec(
            length_m=0.024,
            radius_m=0.006,
            axial_cells=16,
            annular_radial_cells=4,
            circumferential_cells=30,
            core_cells_per_side=8,
        )


def test_full_ogrid_block_mesh_has_only_external_patches_and_is_deterministic() -> None:
    text = block_mesh_dict(_spec())

    assert text == block_mesh_dict(_spec())
    assert text.count("    hex (") == 5
    assert "type cyclic" not in text
    assert "type patch;" in text
    assert "type wall;" in text
    assert "inlet" in text and "outlet" in text and "walls" in text
    assert "interface" not in text.lower()
    assert len(hashlib.sha256(text.encode("utf-8")).hexdigest()) == 64


def test_full_ogrid_preview_is_true_3d_positive_volume_and_deterministic() -> None:
    spec = _spec()
    preview = preview_mesh(spec, {"schema": "flowlab.full-ogrid-profile.v1"})
    repeated = preview_mesh(spec, {"schema": "flowlab.full-ogrid-profile.v1"})

    assert preview == repeated
    assert preview["format"] == FULL_OGRID_PREVIEW_FORMAT
    assert preview["spatialDimension"] == 3
    assert preview["proxyGeometry"] is False
    assert preview["boundsSpanM"] == [0.024, 0.012, 0.012]
    assert len(preview["cells"]) == spec.cell_count
    assert all(len(cell) == 8 and len(set(cell)) == 8 for cell in preview["cells"])
    assert set(preview["cellTypes"]) == {12}
    assert preview["volumeQuality"]["positiveVolume"] is True
    assert preview["volumeQuality"]["zeroVolumeCellCount"] == 0
    assert preview["volumeQuality"]["minimumCellVolumeM3"] > 0.0
    assert preview["volumeQuality"]["totalCellVolumeM3"] > 0.0
    serialized = json.dumps(preview, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == hashlib.sha256(
        json.dumps(repeated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_full_ogrid_wall_geometry_error_decreases_with_circumferential_refinement() -> None:
    deficits = []
    for circumference, core in ((32, 8), (64, 16), (128, 32)):
        spec = FullOGridSpec(
            length_m=0.024,
            radius_m=0.006,
            axial_cells=circumference // 2,
            annular_radial_cells=circumference // 8,
            circumferential_cells=circumference,
            core_cells_per_side=core,
        )
        deficits.append(float(spec.wall_geometry()["areaRelativeDeficit"]))

    assert deficits[0] > deficits[1] > deficits[2] > 0.0
    assert math.isclose(deficits[0] / deficits[1], 4.0, rel_tol=0.02)


def _path_spec() -> FullOGridPathSpec:
    return FullOGridPathSpec(
        segments=(
            FullOGridPathSegment(
                edge_id="inlet-pipe",
                edge_type="pipe",
                length_m=0.06,
                inlet_radius_m=0.006,
                outlet_radius_m=0.006,
                axial_cells=8,
            ),
            FullOGridPathSegment(
                edge_id="contraction",
                edge_type="contraction",
                length_m=0.03,
                inlet_radius_m=0.006,
                outlet_radius_m=0.003,
                axial_cells=8,
            ),
            FullOGridPathSegment(
                edge_id="throat",
                edge_type="pipe",
                length_m=0.03,
                inlet_radius_m=0.003,
                outlet_radius_m=0.003,
                axial_cells=8,
            ),
            FullOGridPathSegment(
                edge_id="expansion",
                edge_type="expansion",
                length_m=0.06,
                inlet_radius_m=0.003,
                outlet_radius_m=0.006,
                axial_cells=12,
            ),
            FullOGridPathSegment(
                edge_id="recovery",
                edge_type="pipe",
                length_m=0.12,
                inlet_radius_m=0.006,
                outlet_radius_m=0.006,
                axial_cells=16,
            ),
        ),
        annular_radial_cells=2,
        circumferential_cells=16,
        core_cells_per_side=4,
    )


def test_full_ogrid_path_has_conformal_multi_segment_topology() -> None:
    spec = _path_spec()
    topology = spec.topology_manifest()

    assert topology["representation"] == FULL_OGRID_PATH_REPRESENTATION
    assert topology["geometrySegmentCount"] == 5
    assert topology["blockCount"] == 25
    assert topology["resolution"]["totalAxialCells"] == 52
    assert topology["resolution"]["crossSectionCellCount"] == 48
    assert topology["resolution"]["cellCount"] == 2496
    assert topology["interfaces"]["axialSegmentInterfaces"] == 4
    assert topology["interfaces"]["boundaryPatchCount"] == 0
    assert topology["connectorCellCount"] == 0
    assert set(topology["patches"]) == {"inlet", "outlet", "walls"}


def test_full_ogrid_path_block_mesh_is_deterministic_and_exposes_only_external_patches() -> None:
    text = path_block_mesh_dict(_path_spec())

    assert text == path_block_mesh_dict(_path_spec())
    assert text.count("    hex (") == 25
    assert text.count("    arc ") == 24
    assert text.count("type patch;") == 2
    assert text.count("type wall;") == 1
    assert "type wedge" not in text
    assert "type empty" not in text
    assert "connector" not in text


def test_full_ogrid_path_preview_is_positive_volume_with_unique_edge_ranges() -> None:
    spec = _path_spec()
    profile = {"schema": "flowlab.full-ogrid-path-profile.v1"}
    preview = path_preview_mesh(spec, profile)

    assert preview == path_preview_mesh(spec, profile)
    assert preview["format"] == FULL_OGRID_PATH_PREVIEW_FORMAT
    assert preview["spatialDimension"] == 3
    assert preview["boundsSpanM"] == [0.3, 0.012, 0.012]
    assert len(preview["cells"]) == 2496
    assert set(preview["cellTypes"]) == {12}
    assert preview["volumeQuality"]["positiveVolume"] is True
    assert preview["volumeQuality"]["minimumCellVolumeM3"] > 0.0
    assert [region["edgeId"] for region in preview["regions"]] == [
        "inlet-pipe",
        "contraction",
        "throat",
        "expansion",
        "recovery",
    ]
    expected_start = 0
    for region in preview["regions"]:
        assert region["cellStart"] == expected_start
        expected_start += region["cellCount"]
    assert expected_start == len(preview["cells"])


def test_full_ogrid_path_fails_closed_on_radius_discontinuity() -> None:
    segments = list(_path_spec().segments)
    segments[1] = FullOGridPathSegment(
        edge_id="contraction",
        edge_type="contraction",
        length_m=0.03,
        inlet_radius_m=0.005,
        outlet_radius_m=0.003,
        axial_cells=8,
    )

    with pytest.raises(ValueError, match="continuous"):
        FullOGridPathSpec(
            segments=tuple(segments),
            annular_radial_cells=2,
            circumferential_cells=16,
            core_cells_per_side=4,
        )
