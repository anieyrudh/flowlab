from __future__ import annotations

import hashlib
import json

import pytest

from server.flowlab import adapters
from server.flowlab.execution import validate_solver_case
from server.flowlab.schemas import CaseRequest
from server.flowlab.y_junction import (
    JUNCTION_ARTIFACT_ID,
    YJunctionSpec,
    generate_mesh,
    mesh_to_openfoam_polymesh,
    public_mesh,
)


def y_junction_project(
    *,
    cell_size_m: float = 0.001125,
    upper_pressure_pa: float = 101325.0,
    lower_pressure_pa: float = 101325.0,
) -> dict:
    half_flow = 2.3561944901923448e-7
    pipe = {
        "type": "pipe",
        "fromPort": "outlet",
        "toPort": "inlet",
        "length": 0.027,
        "shape": {"kind": "circular", "diameter": 0.006},
    }
    return {
        "version": 1,
        "name": "Bounded symmetric Y-junction",
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
                "position": {"x": 0.0, "y": 0.0},
                "pressure": 101325.0,
            },
            "junction": {
                "id": "junction",
                "type": "junction",
                "position": {"x": 200.0, "y": 0.0},
            },
            "upper": {
                "id": "upper",
                "type": "sink",
                "position": {"x": 400.0, "y": 115.47},
                "pressure": upper_pressure_pa,
                "flowDemand": half_flow,
            },
            "lower": {
                "id": "lower",
                "type": "sink",
                "position": {"x": 400.0, "y": -115.47},
                "pressure": lower_pressure_pa,
                "flowDemand": half_flow,
            },
        },
        "edges": {
            "inlet-pipe": {
                **pipe,
                "id": "inlet-pipe",
                "from": "source",
                "to": "junction",
            },
            "upper-branch": {
                **pipe,
                "id": "upper-branch",
                "from": "junction",
                "to": "upper",
            },
            "lower-branch": {
                **pipe,
                "id": "lower-branch",
                "from": "junction",
                "to": "lower",
            },
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": "coarse",
            "runMode": "steady",
            "meshMode": "y-junction",
            "meshControls": {"yJunctionCellSizeM": cell_size_m},
            "maxIterations": 2500,
            "tolerance": 1.0e-8,
        },
        "visualization": {
            "mode": "simulate",
            "overlay": "pressure",
            "particles": False,
            "streamlines": True,
            "grid": True,
        },
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        "sweeps": [],
    }


def _case(project: dict):
    return adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )


def test_y_junction_generation_is_deterministic_connected_and_exactly_three_port() -> None:
    spec = YJunctionSpec(0.027, 0.027, 0.006, 0.001125)
    first = generate_mesh(
        spec,
        inlet_edge_id="inlet-pipe",
        upper_edge_id="upper-branch",
        lower_edge_id="lower-branch",
    )
    second = generate_mesh(
        spec,
        inlet_edge_id="inlet-pipe",
        upper_edge_id="upper-branch",
        lower_edge_id="lower-branch",
    )

    assert first["generationSha256"] == second["generationSha256"]
    assert public_mesh(first) == public_mesh(second)
    assert first["topology"]["connectedFluidRegions"] == 1
    assert first["topology"]["portPatches"] == ["inlet", "outletUpper", "outletLower"]
    assert first["topology"]["cellTypes"] == ["hex"]
    assert first["topology"]["initialCellCount"] == 1616
    assert first["topology"]["prunedUnderconnectedCellCount"] == 12
    assert first["topology"]["minimumFaceNeighbourCount"] == 3
    assert first["volumeQuality"]["minimumCellVolumeM3"] > 0.0
    assert set(first["cellTypes"]) == {12}
    assert first["patches"] == {
        "inlet": {"type": "patch", "faceCount": 72},
        "outletUpper": {"type": "patch", "faceCount": 62},
        "outletLower": {"type": "patch", "faceCount": 62},
        "walls": {"type": "wall", "faceCount": 1612},
    }
    assert hashlib.sha256(
        json.dumps(public_mesh(first), sort_keys=True).encode("utf-8")
    ).hexdigest() == hashlib.sha256(
        json.dumps(public_mesh(second), sort_keys=True).encode("utf-8")
    ).hexdigest()


def test_y_junction_polymesh_declares_edge_zones_and_generated_unowned_junction() -> None:
    mesh = generate_mesh(
        YJunctionSpec(0.027, 0.027, 0.006, 0.001125),
        inlet_edge_id="inlet-pipe",
        upper_edge_id="upper-branch",
        lower_edge_id="lower-branch",
    )
    files = mesh_to_openfoam_polymesh(mesh)

    boundary = files["constant/polyMesh/boundary"]
    assert set(("inlet", "outletUpper", "outletLower", "walls")) <= set(boundary.split())
    owner_values = [
        int(line)
        for line in files["constant/polyMesh/owner"].splitlines()
        if line.strip().isdigit()
    ][1:]
    neighbour_values = [
        int(line)
        for line in files["constant/polyMesh/neighbour"].splitlines()
        if line.strip().isdigit()
    ][1:]
    internal_pairs = list(
        zip(owner_values[: len(neighbour_values)], neighbour_values, strict=True)
    )
    assert all(owner < neighbour for owner, neighbour in internal_pairs)
    assert internal_pairs == sorted(internal_pairs)
    zones = files["constant/polyMesh/cellZones"]
    for zone in ("edge_inlet_pipe", "edge_upper_branch", "edge_lower_branch", "junction_generated"):
        assert zone in zones
    junction = mesh["regions"][-1]
    assert junction["artifactIdentity"] == {
        "schema": "flowlab.generated-region-artifact.v1",
        "artifactId": JUNCTION_ARTIFACT_ID,
        "generated": True,
        "schematicOwner": None,
    }
    edge_stop = max(
        region["cellStart"] + region["cellCount"]
        for region in mesh["regions"]
        if region["role"] == "edge"
    )
    assert edge_stop <= junction["cellStart"]


def test_openfoam_y_junction_case_binds_only_explicit_edge_ranges(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = _case(y_junction_project())

    profile = json.loads(case.files["constant/flowlab_y_junction_profile.json"])
    preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    assert profile["effectiveMeshMode"] == "generated-cartesian-all-hex-y-junction"
    assert profile["flow"]["nominalReynoldsNumber"] == pytest.approx(100.0)
    assert preview["spatialDimension"] == 3
    assert preview["proxyGeometry"] is False
    assert case.resultComponentMap is not None
    assert case.resultComponentMap.version == 2
    binding = case.resultComponentMap.artifactBindings[0].model_dump()
    assert {item["edgeId"] for item in binding["cellRanges"]} == {
        "inlet-pipe",
        "upper-branch",
        "lower-branch",
    }
    assert binding["unownedCellRanges"] == [
        {
            "artifactId": JUNCTION_ARTIFACT_ID,
            "cellStart": preview["regions"][-1]["cellStart"],
            "cellCount": preview["regions"][-1]["cellCount"],
            "schematicOwner": None,
        }
    ]
    assert "outletUpper" in case.files["0/U"]
    assert "outletLower" in case.files["0/p"]
    assert "yJunctionMirroredProbes" in case.files["system/controlDict"]
    assert not case.files["system/functions"].lstrip().startswith("functions")
    assert "yJunctionMirroredProbes" in case.files["system/functions"]
    assert validate_solver_case(case) == []


def test_y_junction_asymmetric_control_is_encoded_without_changing_geometry(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    equal = _case(y_junction_project())
    asymmetric = _case(y_junction_project(lower_pressure_pa=101324.8))
    equal_profile = json.loads(equal.files["constant/flowlab_y_junction_profile.json"])
    asymmetric_profile = json.loads(asymmetric.files["constant/flowlab_y_junction_profile.json"])

    assert equal_profile["mesh"]["generationSha256"] == asymmetric_profile["mesh"]["generationSha256"]
    assert asymmetric_profile["flow"]["outletUpperKinematicPressureM2PerS2"] == pytest.approx(0.0)
    assert asymmetric_profile["flow"]["outletLowerKinematicPressureM2PerS2"] == pytest.approx(-0.0002)
    assert "value           uniform -0.0001999999999970896;" in asymmetric.files["0/p"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda project: project["edges"]["upper-branch"].update(
                {"shape": {"kind": "circular", "diameter": 0.005}}
            ),
            "identical diameter",
        ),
        (
            lambda project: project["nodes"]["lower"]["position"].update({"y": 115.47}),
            "one upper and one lower",
        ),
        (
            lambda project: project["solver"].update({"turbulence": "rans-sst"}),
            "requires laminar",
        ),
    ],
)
def test_y_junction_unsupported_topology_and_physics_fail_closed(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = y_junction_project()
    mutate(project)

    with pytest.raises(ValueError, match=message):
        _case(project)
