import hashlib
import json
import math

import pytest

from server.flowlab.full_ogrid import (
    FULL_OGRID_PREVIEW_FORMAT,
    FULL_OGRID_REPRESENTATION,
    FullOGridSpec,
    block_mesh_dict,
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
