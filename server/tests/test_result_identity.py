from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from server.flowlab import adapters, result_identity
from server.flowlab.execution import materialize_case_files
from server.flowlab.result_identity import (
    SOURCE_CELL_ID_FIELD,
    SOURCE_IDENTITY_ALGORITHM,
    SOURCE_IDENTITY_ALGORITHM_V1,
    SOURCE_IDENTITY_CONTRACT_PATH,
    ResultIdentityError,
    reorder_solver_values_to_source,
    resolve_openfoam_source_cell_identity,
)
from server.flowlab.schemas import CaseRequest


def _multi_edge_project() -> dict:
    return {
        "version": 1,
        "name": "Explicit source identity",
        "fluid": {
            "density": 1000.0,
            "dynamicViscosity": 0.001,
            "temperature": 293.15,
        },
        "nodes": {
            "source": {
                "id": "source",
                "type": "source",
                "position": {"x": 0.0, "y": 0.0},
                "pressure": 120000.0,
            },
            "junction": {
                "id": "junction",
                "type": "junction",
                "position": {"x": 500.0, "y": 0.0},
            },
            "sink": {
                "id": "sink",
                "type": "sink",
                "position": {"x": 1000.0, "y": 0.0},
                "pressure": 101325.0,
                "flowDemand": 1.0e-5,
            },
        },
        "edges": {
            "left": {
                "id": "left",
                "type": "pipe",
                "from": "source",
                "to": "junction",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 0.05,
                "shape": {"kind": "rectangular", "width": 0.01, "height": 0.01},
            },
            "right": {
                "id": "right",
                "type": "pipe",
                "from": "junction",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 0.05,
                "shape": {"kind": "rectangular", "width": 0.01, "height": 0.01},
            },
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": "coarse",
        },
    }


def _axisymmetric_multi_edge_project() -> dict:
    project = _multi_edge_project()
    project["edges"]["left"]["shape"] = {"kind": "circular", "diameter": 0.01}
    project["edges"]["right"]["shape"] = {"kind": "circular", "diameter": 0.01}
    project["solver"]["meshMode"] = "axisymmetric"
    return project


def test_generated_contract_resolves_actual_polymesh_without_order_assumption(
    tmp_path: Path,
) -> None:
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_multi_edge_project(),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    case_dir = tmp_path / "case"
    materialize_case_files(case, case_dir)
    mesh = json.loads(case.files["mesh/flowlab_mesh.json"])

    report = resolve_openfoam_source_cell_identity(case_dir, mesh)

    assert report is not None
    assert report["verified"] is True
    assert report["orderingAssumptionUsed"] is False
    assert sorted(report["solverToSourceCell"]) == list(
        range(report["sourceCellCount"])
    )
    assert (case_dir / "postProcessing" / "flowlab_result_identity.json").is_file()
    contract = json.loads(case.files[SOURCE_IDENTITY_CONTRACT_PATH])
    assert contract["identityField"] == SOURCE_CELL_ID_FIELD
    assert contract["algorithm"] == SOURCE_IDENTITY_ALGORITHM_V1
    assert contract["orderingAssumptionAllowed"] is False
    assert contract["unownedRanges"]


def test_axisymmetric_logical_identity_is_invariant_to_prospective_grading() -> None:
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_axisymmetric_multi_edge_project(),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    mesh = json.loads(case.files["mesh/flowlab_mesh.json"])
    graded_points = [
        [
            float(point[0]) ** 2,
            float(point[1]) * (1.0 + 0.1 * float(point[0])),
            float(point[2]) * (1.0 + 0.1 * float(point[0])),
        ]
        for point in mesh["points"]
    ]

    assert result_identity._axisymmetric_logical_signatures(
        mesh["points"],
        mesh["cells"],
    ) == result_identity._axisymmetric_logical_signatures(
        graded_points,
        mesh["cells"],
    )


def test_solver_values_are_reordered_only_through_verified_mapping() -> None:
    identity = {
        "verified": True,
        "sourceCellCount": 3,
        "solverToSourceCell": [2, 0, 1],
    }

    assert reorder_solver_values_to_source([20.0, 0.0, 10.0], identity) == [
        0.0,
        10.0,
        20.0,
    ]

    with pytest.raises(ResultIdentityError, match="verified identity"):
        reorder_solver_values_to_source(
            [20.0, 0.0, 10.0],
            {**identity, "verified": False},
        )


def test_polymesh_mismatch_fails_closed(tmp_path: Path) -> None:
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_multi_edge_project(),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    case_dir = tmp_path / "case"
    materialize_case_files(case, case_dir)
    mesh = json.loads(case.files["mesh/flowlab_mesh.json"])
    points_path = case_dir / "constant" / "polyMesh" / "points"
    points_text = points_path.read_text(encoding="utf-8")
    points_path.write_text(
        re.sub(
            r"\([-+0-9.eE]+\s+[-+0-9.eE]+\s+[-+0-9.eE]+\)",
            "(9 9 9)",
            points_text,
            count=1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ResultIdentityError, match="no matching generated"):
        resolve_openfoam_source_cell_identity(case_dir, mesh)
