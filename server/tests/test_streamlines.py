from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server.flowlab.execution import JobManager
from server.flowlab.schemas import (
    JobRecord,
    ResultComponentCellBinding,
    ResultComponentMap,
    SolverCase,
    StreamlineDerivationRequest,
)
from server.flowlab.streamlines import derive_streamlines_from_artifact, integrate_dataset


def _dataset(points: list[list[float]], cell: list[int], cell_type: int) -> dict:
    return {
        "format": "legacy-vtk-ascii-v1",
        "points": points,
        "cells": [cell],
        "cellTypes": [cell_type],
        "pointData": {
            "scalars": {
                "p": [sum(point) for point in points],
                "T": [290.0 + index for index in range(len(points))],
                "alpha.water": [index / max(len(points) - 1, 1) for index in range(len(points))],
            },
            "vectors": {
                "U": [[1.0, 0.0, 0.0] for _point in points],
                "vorticity": [[0.0, 0.0, 2.0] for _point in points],
            },
        },
        "cellData": {"scalars": {}, "vectors": {}},
        "fields": ["U", "p", "T", "alpha.water", "vorticity"],
    }


SUPPORTED = [
    ("triangle", [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 1, 2], 5, [0.2, 0.2, 0]),
    ("quad", [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], [0, 1, 2, 3], 9, [0.2, 0.2, 0]),
    ("tetra", [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 1, 2, 3], 10, [0.1, 0.1, 0.1]),
    (
        "hex",
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        [0, 1, 2, 3, 4, 5, 6, 7],
        12,
        [0.2, 0.2, 0.2],
    ),
    (
        "wedge",
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]],
        [0, 1, 2, 3, 4, 5],
        13,
        [0.1, 0.1, 0.2],
    ),
    (
        "pyramid",
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1]],
        [0, 1, 2, 3, 4],
        14,
        [0.4, 0.4, 0.2],
    ),
]


@pytest.mark.parametrize(("_name", "points", "cell", "cell_type", "seed"), SUPPORTED)
def test_integrates_supported_cells_with_explicit_provenance(
    _name: str,
    points: list[list[float]],
    cell: list[int],
    cell_type: int,
    seed: list[float],
) -> None:
    result = integrate_dataset(
        _dataset(points, cell, cell_type),
        [seed],
        source_name="fixture.vtk",
        source_identity="artifact-local-unlinked",
        step_size=0.01,
        max_vertices_per_line=2,
    )

    vertex = result["lines"][0]["vertices"][0]
    assert vertex["provenance"]["renderedCellId"] == 0
    assert vertex["provenance"]["sourceCellId"] == 0
    assert sum(vertex["provenance"]["weights"]) == pytest.approx(1.0)
    assert vertex["fields"]["vorticity"] == pytest.approx(2.0)
    assert result["terminology"] == "steady-streamline"


def _legacy_quad_vtk() -> str:
    return """# vtk DataFile Version 3.0
full result
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
VECTORS U float
1 0 0
1 0 0
1 0 0
1 0 0
SCALARS p float 1
LOOKUP_TABLE default
0 1 2 1
"""


def _case() -> SolverCase:
    return SolverCase(
        id="case-streamlines",
        projectName="Streamlines",
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
        resultComponentMap=ResultComponentMap(
            version=2,
            projectSha256="a" * 64,
            artifactBindings=[
                ResultComponentCellBinding(
                    artifactName="postProcessing/flowlabNative/*.vtk",
                    scope="cell-ranges",
                    sourceCellCount=1,
                    identitySchema="flowlab.openfoam-source-cell-identity.v1",
                    identityField="flowlabSourceCellId",
                    identityContractSha256="b" * 64,
                    cellRanges=[],
                )
            ],
        ),
    )


def test_derives_from_full_artifact_and_rejects_preview_source(tmp_path: Path) -> None:
    artifact = tmp_path / "postProcessing" / "flowlabNative" / "time_1.vtk"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(_legacy_quad_vtk(), encoding="utf-8")
    request = StreamlineDerivationRequest(
        artifactPath="postProcessing/flowlabNative/time_1.vtk",
        seeds=[(0.1, 0.5, 0.0)],
        maxVerticesPerLine=3,
    )

    result = derive_streamlines_from_artifact(tmp_path, _case(), request)
    assert result["sourceIdentity"] == "verified-case-cell-order"
    assert result["velocityInterpolation"] == "barycentric point field"
    assert result["lines"][0]["vertices"][0]["provenance"]["sourceCellId"] == 0

    preview = request.model_copy(update={"sourceRepresentation": "preview"})
    with pytest.raises(ValueError, match="Full result required"):
        derive_streamlines_from_artifact(tmp_path, _case(), preview)


def test_backend_integration_records_cancellation_on_last_vertex() -> None:
    checks = iter((False, True))
    result = integrate_dataset(
        _dataset([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 1, 2], 5),
        [[0.1, 0.1, 0.0]],
        source_name="fixture.vtk",
        source_identity="artifact-local-unlinked",
        step_size=0.01,
        cancelled=lambda: next(checks, True),
    )

    assert result["lines"][0]["terminationReason"] == "cancelled"
    assert result["lines"][0]["vertices"][-1]["terminationReason"] == "cancelled"


def test_automatic_inlet_seeds_require_generator_manifest(tmp_path: Path) -> None:
    artifact = tmp_path / "postProcessing" / "flowlabNative" / "time_1.vtk"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(_legacy_quad_vtk(), encoding="utf-8")
    request = StreamlineDerivationRequest(
        artifactPath="postProcessing/flowlabNative/time_1.vtk",
        seedMode="inlet-manifest",
        seedCount=1,
    )
    with pytest.raises(ValueError, match="generator-authored boundary-face manifest"):
        derive_streamlines_from_artifact(tmp_path, _case(), request)

    manifest = tmp_path / "mesh" / "flowlab_boundary_faces.json"
    manifest.parent.mkdir()
    manifest_text = json.dumps(
        {
            "schema": "flowlab.boundary_faces.v1",
            "authorship": "generator",
            "cellIdentity": "flowlab_mesh_order",
            "patches": [
                {
                    "name": "inlet",
                    "role": "inlet",
                    "faces": [{"sourceCellId": 0, "pointIds": [0, 3], "center": [0.0, 0.5, 0.0]}],
                }
            ],
        }
    )
    manifest.write_text(manifest_text, encoding="utf-8")
    generated_case = _case().model_copy(update={"files": {"mesh/flowlab_boundary_faces.json": manifest_text}})
    result = derive_streamlines_from_artifact(tmp_path, generated_case, request)
    assert result["seedCount"] == 1
    assert result["lines"][0]["vertices"][0]["position"] == [0.0, 0.5, 0.0]

    manifest.write_text(manifest_text + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="generator-authored boundary-face manifest"):
        derive_streamlines_from_artifact(tmp_path, generated_case, request)


def test_su2_streamlines_fail_closed_without_verified_solver_cell_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "result.vtk"
    artifact.write_text(_legacy_quad_vtk(), encoding="utf-8")
    case = _case().model_copy(update={"solver": "su2"})
    request = StreamlineDerivationRequest(artifactPath="result.vtk", seeds=[(0.1, 0.5, 0.0)], maxVerticesPerLine=1)

    with pytest.raises(ValueError, match="explicit resultComponentMap cell-range binding"):
        derive_streamlines_from_artifact(tmp_path, case, request)


def test_full_artifact_streamline_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = tmp_path / "case"
    artifact = case_dir / "postProcessing" / "flowlabNative" / "time_1.vtk"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(_legacy_quad_vtk(), encoding="utf-8")
    manager = JobManager(runtime_root=tmp_path / "runtime")
    job = JobRecord(
        id="job-streamlines",
        caseId="case-streamlines",
        solver="openfoam",
        status="complete",
        caseDir=str(case_dir),
    )
    manager.jobs[job.id] = job
    manager.cases[job.id] = _case()
    monkeypatch.setattr(app_module, "JOB_MANAGER", manager)
    client = TestClient(app_module.app)

    response = client.post(
        "/api/jobs/job-streamlines/artifact/streamlines",
        json={
            "artifactPath": "postProcessing/flowlabNative/time_1.vtk",
            "seeds": [[0.1, 0.5, 0.0]],
            "maxVerticesPerLine": 2,
        },
    )
    assert response.status_code == 200
    assert response.json()["schema"] == "flowlab.steady_streamlines.v1"

    preview = client.post(
        "/api/jobs/job-streamlines/artifact/streamlines",
        json={
            "artifactPath": "postProcessing/flowlabNative/time_1.vtk",
            "sourceRepresentation": "preview",
            "seeds": [[0.1, 0.5, 0.0]],
        },
    )
    assert preview.status_code == 400
    assert preview.json()["detail"] == "Full result required."
