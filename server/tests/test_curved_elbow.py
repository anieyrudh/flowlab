from __future__ import annotations

import hashlib
import json
import math

import pytest

from server.flowlab import adapters
from server.flowlab.curved_elbow import (
    CURVED_ELBOW_PREVIEW_FORMAT,
    CURVED_ELBOW_REPRESENTATION,
    CurvedElbowSpec,
    block_mesh_dict,
    preview_mesh,
)
from server.flowlab.execution import validate_solver_case
from server.flowlab.schemas import CaseRequest


def _spec() -> CurvedElbowSpec:
    return CurvedElbowSpec(
        diameter_m=0.01,
        centreline_radius_m=0.03,
        inlet_leg_m=0.1,
        outlet_leg_m=0.1,
        inlet_axial_cells=20,
        bend_axial_cells=12,
        outlet_axial_cells=20,
        annular_radial_cells=2,
        circumferential_cells=16,
        core_cells_per_side=4,
    )


def _project() -> dict:
    spec = _spec()
    return {
        "version": 1,
        "name": "Canonical Re100 circular elbow",
        "fluid": {
            "density": 1000.0,
            "dynamicViscosity": 0.001,
            "temperature": 293.15,
            "vaporPressure": 2340.0,
            "bulkModulus": 2.2e9,
        },
        "nodes": {
            "source": {
                "id": "source",
                "type": "source",
                "position": {"x": 100.0, "y": 300.0},
                "pressure": 101325.0,
            },
            "sink": {
                "id": "sink",
                "type": "sink",
                "position": {"x": 700.0, "y": 100.0},
                "pressure": 101325.0,
                "flowDemand": math.pi * 0.01**2 / 4.0 * 0.01,
            },
        },
        "edges": {
            "canonical-elbow": {
                "id": "canonical-elbow",
                "type": "bend",
                "from": "source",
                "to": "sink",
                "length": spec.total_centreline_length_m,
                "shape": {"kind": "circular", "diameter": 0.01},
                "roughness": 0.0,
                "minorLossK": 0.0,
            }
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": "coarse",
            "runMode": "steady",
            "meshMode": "curved-elbow-ogrid",
            "meshControls": {
                "curvedElbowInletAxialCells": 20,
                "curvedElbowBendAxialCells": 12,
                "curvedElbowOutletAxialCells": 20,
                "curvedElbowAnnularRadialCells": 2,
                "curvedElbowCircumferentialCells": 16,
                "curvedElbowCoreCellsPerSide": 4,
            },
            "curvedElbowVerification": {
                "contractId": "canonical-circular-elbow-re100-v1",
                "boundaryCondition": "fully-developed-parabolic-inlet-pressure-outlet",
                "diameterM": 0.01,
                "centrelineRadiusM": 0.03,
                "inletLegLengthM": 0.1,
                "outletLegLengthM": 0.1,
                "bendAngleDegrees": 90.0,
                "volumetricFlowRateM3PerS": math.pi * 0.01**2 / 4.0 * 0.01,
                "qoiHistoryWriteIntervalIterations": 1,
            },
            "maxIterations": 2000,
            "tolerance": 1.0e-8,
        },
    }


def test_curved_elbow_spec_is_exactly_bounded_and_conformal() -> None:
    spec = _spec()
    topology = spec.topology_manifest()

    assert spec.centreline_radius_over_diameter == pytest.approx(3.0)
    assert spec.inlet_leg_over_diameter == pytest.approx(10.0)
    assert spec.outlet_leg_over_diameter == pytest.approx(10.0)
    assert spec.bend_angle_degrees == 90.0
    assert topology["representation"] == CURVED_ELBOW_REPRESENTATION
    assert topology["spatialDimension"] == 3
    assert topology["cellTypes"] == ["hex"]
    assert topology["blockCount"] == 15
    assert topology["collapsedAxisCells"] == 0
    assert topology["resolution"]["crossSectionCellCount"] == 48
    assert topology["resolution"]["cellCount"] == 2496
    assert [row["cellCount"] for row in topology["componentBlocks"]] == [
        960,
        576,
        960,
    ]
    assert topology["patches"]["inlet"]["faceCount"] == 48
    assert topology["patches"]["outlet"]["faceCount"] == 48
    assert topology["patches"]["walls"]["faceCount"] == 832


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"bend_angle_degrees": 45.0}, "exactly 90"),
        ({"centreline_radius_m": 0.005}, "must exceed"),
        ({"circumferential_cells": 18}, "divisible by four"),
        ({"core_cells_per_side": 5}, "circumferentialCells/4"),
    ],
)
def test_curved_elbow_rejects_out_of_scope_or_nonconformal_geometry(
    changes: dict[str, float | int],
    message: str,
) -> None:
    values = {
        "diameter_m": 0.01,
        "centreline_radius_m": 0.03,
        "inlet_leg_m": 0.1,
        "outlet_leg_m": 0.1,
        "inlet_axial_cells": 20,
        "bend_axial_cells": 12,
        "outlet_axial_cells": 20,
        "annular_radial_cells": 2,
        "circumferential_cells": 16,
        "core_cells_per_side": 4,
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        CurvedElbowSpec(**values)


def test_curved_elbow_block_mesh_is_deterministic_and_has_only_external_patches() -> None:
    text = block_mesh_dict(_spec())

    assert text == block_mesh_dict(_spec())
    assert text.count("    hex (") == 15
    assert text.count("    arc ") == 24
    assert "type wedge" not in text
    assert "type cyclic" not in text
    assert "frontAndBack" not in text
    assert "inlet" in text and "outlet" in text and "walls" in text
    assert len(hashlib.sha256(text.encode("utf-8")).hexdigest()) == 64


def test_curved_elbow_preview_is_true_3d_positive_volume_and_source_cell_bound() -> None:
    spec = _spec()
    profile = {
        "schema": "flowlab.curved-elbow-ogrid-profile.v1",
        "pathEdgeIds": ["elbow-path"],
    }
    preview = preview_mesh(spec, profile)
    repeated = preview_mesh(spec, profile)

    assert preview == repeated
    assert preview["format"] == CURVED_ELBOW_PREVIEW_FORMAT
    assert preview["spatialDimension"] == 3
    assert preview["proxyGeometry"] is False
    assert preview["requiresExplicitSourceCellProvenance"] is True
    assert preview["boundsSpanM"] == [0.135, 0.135, 0.01]
    assert len(preview["cells"]) == spec.cell_count
    assert all(len(cell) == 8 and len(set(cell)) == 8 for cell in preview["cells"])
    assert set(preview["cellTypes"]) == {12}
    assert preview["volumeQuality"]["positiveVolume"] is True
    assert preview["volumeQuality"]["zeroVolumeCellCount"] == 0
    assert preview["volumeQuality"]["minimumCellVolumeM3"] > 0.0
    expected_faceted_volume = (
        float(spec.wall_geometry()["polygonAreaM2"])
        * spec.total_centreline_length_m
    )
    assert preview["volumeQuality"]["totalCellVolumeM3"] == pytest.approx(
        expected_faceted_volume,
        rel=1.0e-12,
    )
    assert [region["componentId"] for region in preview["regions"]] == [
        "inlet-leg",
        "elbow",
        "outlet-leg",
    ]
    assert all(region["edgeId"] == "elbow-path" for region in preview["regions"])
    assert sum(region["cellCount"] for region in preview["regions"]) == spec.cell_count
    serialized = json.dumps(preview, sort_keys=True, separators=(",", ":"))
    repeated_serialized = json.dumps(repeated, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(serialized.encode()).hexdigest() == hashlib.sha256(
        repeated_serialized.encode()
    ).hexdigest()


def test_curved_elbow_uniform_three_grid_sequence_has_exact_eightfold_cell_refinement() -> None:
    levels = (
        (20, 12, 20, 2, 16, 4),
        (40, 24, 40, 4, 32, 8),
        (80, 48, 80, 8, 64, 16),
    )
    specs = [
        CurvedElbowSpec(
            diameter_m=0.01,
            centreline_radius_m=0.03,
            inlet_leg_m=0.1,
            outlet_leg_m=0.1,
            inlet_axial_cells=inlet,
            bend_axial_cells=bend,
            outlet_axial_cells=outlet,
            annular_radial_cells=annular,
            circumferential_cells=circumference,
            core_cells_per_side=core,
        )
        for inlet, bend, outlet, annular, circumference, core in levels
    ]

    assert [spec.cell_count for spec in specs] == [2496, 19968, 159744]
    assert specs[1].cell_count == 8 * specs[0].cell_count
    assert specs[2].cell_count == 8 * specs[1].cell_count
    deficits = [float(spec.wall_geometry()["areaRelativeDeficit"]) for spec in specs]
    assert deficits[0] > deficits[1] > deficits[2] > 0.0
    assert math.isclose(deficits[0] / deficits[1], 4.0, rel_tol=0.03)


def test_openfoam_curved_elbow_case_is_full_volume_and_explicitly_component_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_project(),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    profile = json.loads(case.files["constant/flowlab_curved_elbow_profile.json"])
    preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    assert profile["schema"] == "flowlab.curved-elbow-ogrid-profile.v1"
    assert profile["effectiveMeshMode"] == CURVED_ELBOW_REPRESENTATION
    assert profile["verificationContract"]["reynoldsNumber"] == pytest.approx(100.0)
    assert profile["scope"]["geometry"] == "one-canonical-90deg-constant-diameter-circular-elbow"
    assert preview["representation"] == "pre-solve-blockMesh-equivalent-curved-elbow-ogrid"
    assert preview["requiresExplicitSourceCellProvenance"] is True
    assert case.files["system/blockMeshDict"].count("    hex (") == 15
    assert "curvedElbowParabolicInlet" in case.files["0/U"]
    assert "curvedElbowXYZProbes" in case.files["system/functions"]
    assert case.resultComponentMap is not None
    assert case.resultComponentMap.version == 2
    binding = case.resultComponentMap.artifactBindings[0].model_dump()
    assert binding["sourceCellCount"] == 2496
    assert [row["componentId"] for row in binding["cellRanges"]] == [
        "inlet-leg",
        "elbow",
        "outlet-leg",
    ]
    assert sum(row["cellCount"] for row in binding["cellRanges"]) == 2496
    assert validate_solver_case(case) == []


def test_openfoam_curved_elbow_case_fails_closed_without_probe_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_project(),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    case.files.pop("constant/flowlab_curved_elbow_probe_provenance.json")

    assert any(
        "explicit, non-geometric source-cell component provenance" in issue
        for issue in validate_solver_case(case)
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda project: project["solver"]["curvedElbowVerification"].__setitem__(
                "centrelineRadiusM",
                0.04,
            ),
            "Rc/D=3",
        ),
        (
            lambda project: project["solver"].__setitem__("turbulence", "rans-sst"),
            "requires laminar",
        ),
        (
            lambda project: project["edges"]["canonical-elbow"].__setitem__(
                "type",
                "pipe",
            ),
            "only one bend edge",
        ),
        (
            lambda project: project["solver"]["curvedElbowVerification"].__setitem__(
                "bendAngleDegrees",
                45.0,
            ),
            "exactly 90",
        ),
    ],
)
def test_openfoam_curved_elbow_case_fails_closed_on_scope_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _project()
    mutation(project)
    with pytest.raises(ValueError, match=message):
        adapters.generate_case(
            CaseRequest.model_construct(
                project=project,
                solver="openfoam",
                advancedMode="incompressible-navier-stokes",
            )
        )
