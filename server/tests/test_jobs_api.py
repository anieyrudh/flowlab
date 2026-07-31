from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server.flowlab import adapters
from server.flowlab import execution
from server.flowlab.execution import JobManager
from server.flowlab.schemas import JobRecord


def test_job_api_generates_queues_and_returns_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path)
    monkeypatch.setattr(app_module, "JOB_MANAGER", manager)
    app_module.CASES.clear()
    client = TestClient(app_module.app)

    case_response = client.post(
        "/api/cases/generate",
            json={
                "project": {
                    "name": "API test",
                    "solver": {"meshResolution": "coarse"},
                    "nodes": {
                        "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
                        "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
                    },
                    "edges": {
                        "pipe": {
                            "id": "pipe",
                            "type": "pipe",
                            "from": "source",
                            "to": "sink",
                            "fromPort": "outlet",
                            "toPort": "inlet",
                            "length": 10,
                            "shape": {"kind": "circular", "diameter": 0.1},
                        }
                    },
                },
                "solver": "openfoam",
                "advancedMode": "incompressible-navier-stokes",
            },
    )
    assert case_response.status_code == 200

    job_response = client.post("/api/jobs", json=case_response.json())
    assert job_response.status_code == 200
    job = job_response.json()

    assert job["status"] == "blocked"
    assert "Docker is unavailable" in job["error"]
    assert Path(job["caseDir"]).exists()

    logs_response = client.get(f"/api/jobs/{job['id']}/logs")
    assert logs_response.status_code == 200
    payload = logs_response.json()
    assert payload["jobId"] == job["id"]
    assert payload["status"] == "blocked"
    assert any("Materialized" in line for line in payload["logs"])

    recent_response = client.get("/api/jobs", params={"limit": 5})
    assert recent_response.status_code == 200
    recent = recent_response.json()["jobs"]
    assert recent[0]["job"]["id"] == job["id"]
    assert recent[0]["case"]["id"] == case_response.json()["id"]

    restored_manager = JobManager(runtime_root=tmp_path)
    restored_job = restored_manager.get_job(job["id"])
    restored_case = restored_manager.get_case_for_job(job["id"])
    assert restored_job is not None
    assert restored_job.status == "blocked"
    assert restored_case is not None
    assert restored_case.id == case_response.json()["id"]
    assert (tmp_path / "jobs" / job["id"] / "flowlab_job_record.json").is_file()
    assert (tmp_path / "jobs" / job["id"] / "flowlab_case_record.json").is_file()


def test_case_api_generates_full_ogrid_preview_and_fails_closed_for_unsupported_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(app_module, "JOB_MANAGER", JobManager(runtime_root=tmp_path))
    app_module.CASES.clear()
    client = TestClient(app_module.app)
    project = {
        "name": "Full O-grid API",
        "solver": {
            "meshMode": "full-ogrid",
            "meshResolution": "coarse",
            "runMode": "steady",
            "turbulence": "laminar",
            "meshControls": {
                "fullOGridAxialCells": 16,
                "fullOGridAnnularRadialCells": 4,
                "fullOGridCircumferentialCells": 32,
                "fullOGridCoreCellsPerSide": 8,
            },
        },
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "length": 0.024,
                "shape": {"kind": "circular", "diameter": 0.006},
            }
        },
    }

    response = client.post(
        "/api/cases/generate",
        json={
            "project": project,
            "solver": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
        },
    )

    assert response.status_code == 200
    files = response.json()["files"]
    profile = json.loads(files["constant/flowlab_full_ogrid_profile.json"])
    preview = json.loads(files["mesh/flowlab_mesh.json"])
    assert profile["topology"]["resolution"]["cellCount"] == 3072
    assert preview["boundsSpanM"] == [0.024, 0.006, 0.006]
    assert preview["volumeQuality"]["positiveVolume"] is True
    boundary_manifest = json.loads(files["mesh/flowlab_boundary_faces.json"])
    assert boundary_manifest["authorship"] == "generator"
    assert {patch["role"] for patch in boundary_manifest["patches"]} == {"inlet", "outlet"}
    assert all(
        "sourceCellId" in face and len(face["center"]) == 3
        for patch in boundary_manifest["patches"]
        for face in patch["faces"]
    )

    project["edges"]["pipe"]["type"] = "elbow"
    rejected = client.post(
        "/api/cases/generate",
        json={
            "project": project,
            "solver": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
        },
    )
    assert rejected.status_code == 400
    assert "only a straight pipe edge" in rejected.json()["detail"]


def test_job_api_fetches_bounded_job_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "VTK").mkdir(parents=True)
    (case_dir / "VTK" / "case_0001.vtk").write_text(
        """# vtk DataFile Version 3.0
result
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
SCALARS p float 1
LOOKUP_TABLE default
1 2 3 4
""",
        encoding="utf-8",
    )
    (case_dir / "VTK" / "collection.pvd").write_text(
        """<?xml version="1.0"?>
<VTKFile type="Collection" version="0.1">
  <Collection>
    <DataSet timestep="0.25" part="0" file="case_0001.vtk"/>
  </Collection>
</VTKFile>""",
        encoding="utf-8",
    )
    (case_dir / "mesh").mkdir(parents=True)
    (case_dir / "mesh" / "flowlab_mesh.vtk").write_text("# vtk DataFile Version 3.0\nmesh\n", encoding="utf-8")
    (case_dir / "mesh" / "production_mesh_acceptance.json").write_text(
        """{
  "schema": "flowlab.production_mesh_acceptance.v1",
  "productionReady": false,
  "approvalStatus": "blocked",
  "nativeQualityEvidence": {
    "schema": "flowlab.native_mesh_quality_evidence.v1",
    "productionReady": false,
    "status": "openfoam-native-quality-blocked",
    "solverReports": {
      "openfoam": {
        "status": "blocked",
        "commandRuns": [
          {"command": "checkMesh -allGeometry -allTopology", "status": "failed", "exitCode": 0, "logPath": "log.checkMesh"}
        ],
        "qualityMetrics": {
          "failedChecks": 2,
          "maxNonOrthogonality": 72.5,
          "maxSkewness": 4.2,
          "maxAspectRatio": 90.0,
          "minVolume": -1e-12,
          "passed": false
        },
        "yPlusEvidence": {"status": "missing", "blockingReason": "Missing y-plus or wall-distance evidence."},
        "layerSummary": {"status": "present", "excerpts": ["Layer addition phase"]},
        "blockingReasons": ["OpenFOAM checkMesh failed 2 check(s)."]
      }
    }
  },
  "solverAcceptance": {
    "openfoam": {"status": "blocked", "blockingReasons": ["OpenFOAM checkMesh failed 2 check(s)."]}
  },
  "blockingReasons": ["OpenFOAM checkMesh failed 2 check(s)."]
}
""",
        encoding="utf-8",
    )
    (case_dir / "log.checkMesh").write_text("Failed 2 mesh checks.\n", encoding="utf-8")
    (case_dir / "log.snappyHexMesh").write_text("Layer addition phase\n", encoding="utf-8")
    (case_dir / "postProcessing" / "residuals" / "0").mkdir(parents=True)
    (case_dir / "postProcessing" / "residuals" / "0" / "residuals.dat").write_text("Time Ux p\n", encoding="utf-8")
    (case_dir / "VTK" / "too_large.vtk").write_text("x" * 1200, encoding="utf-8")

    monkeypatch.setattr(execution, "MAX_RESULT_FILE_BYTES", 1024)
    manager = JobManager(runtime_root=tmp_path / "runtime")
    job = JobRecord(caseId="case-api-artifact", solver="openfoam", status="complete", caseDir=str(case_dir))
    manager._store(job)
    monkeypatch.setattr(app_module, "JOB_MANAGER", manager)
    client = TestClient(app_module.app)

    result_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "VTK/case_0001.vtk"})
    assert result_response.status_code == 200
    assert result_response.json()["path"] == "VTK/case_0001.vtk"
    assert "result" in result_response.json()["text"]
    assert result_response.json()["fieldSummary"]["fields"][0]["name"] == "p"
    assert result_response.json()["fieldSummary"]["fields"][0]["stdDev"] == pytest.approx(1.11803398875)
    assert result_response.json()["fieldSummary"]["fields"][0]["p50"] == pytest.approx(2.5)
    assert result_response.json()["fieldSummary"]["fields"][0]["p95"] == pytest.approx(3.85)

    collection_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "VTK/collection.pvd"})
    assert collection_response.status_code == 200
    assert collection_response.json()["collectionSummary"]["schema"] == "flowlab.pvd_collection.v1"
    assert collection_response.json()["collectionSummary"]["referencedResultCount"] == 1

    diagnostic_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "postProcessing/residuals/0/residuals.dat"})
    assert diagnostic_response.status_code == 200
    assert diagnostic_response.json()["text"] == "Time Ux p\n"

    oversized_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "VTK/too_large.vtk"})
    assert oversized_response.status_code == 200
    assert oversized_response.json()["skipped"] == "file too large"
    assert "text" not in oversized_response.json()

    chunk_response = client.get(f"/api/jobs/{job.id}/artifact/chunk", params={"path": "VTK/too_large.vtk", "offset": 0, "limit": 12})
    assert chunk_response.status_code == 200
    chunk = chunk_response.json()
    assert chunk["path"] == "VTK/too_large.vtk"
    assert chunk["size"] == 1200
    assert chunk["offset"] == 0
    assert chunk["limit"] == 12
    assert chunk["text"] == "x" * 12
    assert chunk["nextOffset"] == 12
    assert chunk["complete"] is False

    final_chunk_response = client.get(f"/api/jobs/{job.id}/artifact/chunk", params={"path": "VTK/too_large.vtk", "offset": 1192, "limit": 12})
    assert final_chunk_response.status_code == 200
    assert final_chunk_response.json()["text"] == "x" * 8
    assert final_chunk_response.json()["complete"] is True

    preview_response = client.get(
        f"/api/jobs/{job.id}/artifact/preview",
        params={"path": "VTK/case_0001.vtk", "pointLimit": 4, "cellLimit": 1},
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["schema"] == "flowlab.result_preview.v1"
    assert preview["path"] == "VTK/case_0001.vtk"
    assert preview["sourcePointCount"] == 4
    assert preview["pointCount"] == 4
    assert preview["fieldSummary"]["fields"][0]["name"] == "p"
    assert preview["fieldSummary"]["fields"][0]["p95"] == pytest.approx(3.85)
    assert preview["fieldSamples"]["point"][0]["values"] == [1.0, 2.0, 3.0, 4.0]

    collection_preview_response = client.get(
        f"/api/jobs/{job.id}/artifact/preview",
        params={"path": "VTK/collection.pvd", "pointLimit": 4, "cellLimit": 1},
    )
    assert collection_preview_response.status_code == 400
    assert "Only VTK/VTU result artifacts" in collection_preview_response.json()["detail"]

    index_response = client.get(f"/api/jobs/{job.id}/artifacts", params={"kind": "result", "limit": 1})
    assert index_response.status_code == 200
    index = index_response.json()
    assert index["count"] == 3
    assert index["truncated"] is True
    assert index["artifacts"][0]["path"] == "VTK/case_0001.vtk"
    assert index["artifacts"][0]["kind"] == "result"
    assert index["artifacts"][0]["fieldSummary"]["schema"] == "flowlab.result_field_summary.v1"
    assert index["artifacts"][0]["fieldSummary"]["fields"][0]["name"] == "p"
    assert index["artifacts"][0]["time"] == 0.25
    assert index["artifacts"][0]["timeSource"] == "pvd"

    all_index_response = client.get(f"/api/jobs/{job.id}/artifacts", params={"kind": "all", "limit": 10})
    assert all_index_response.status_code == 200
    all_index = all_index_response.json()
    indexed_paths = {artifact["path"] for artifact in all_index["artifacts"]}
    assert "VTK/case_0001.vtk" in indexed_paths
    assert "VTK/collection.pvd" in indexed_paths
    assert "VTK/too_large.vtk" in indexed_paths
    assert "postProcessing/residuals/0/residuals.dat" in indexed_paths
    assert "mesh/flowlab_mesh.vtk" not in indexed_paths

    invalid_chunk_response = client.get(f"/api/jobs/{job.id}/artifact/chunk", params={"path": "VTK/too_large.vtk", "offset": 1201, "limit": 12})
    assert invalid_chunk_response.status_code == 400
    assert "beyond the end" in invalid_chunk_response.json()["detail"]

    traversal_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "../secret.vtk"})
    assert traversal_response.status_code == 400
    assert "escapes" in traversal_response.json()["detail"]

    mesh_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "mesh/flowlab_mesh.vtk"})
    assert mesh_response.status_code == 400
    assert "Mesh inspection exports" in mesh_response.json()["detail"]

    mesh_acceptance_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "mesh/production_mesh_acceptance.json"})
    assert mesh_acceptance_response.status_code == 400
    assert "Mesh inspection exports" in mesh_acceptance_response.json()["detail"]

    mesh_quality_response = client.get(f"/api/jobs/{job.id}/mesh-quality")
    assert mesh_quality_response.status_code == 200
    mesh_quality = mesh_quality_response.json()
    assert mesh_quality["schema"] == "flowlab.mesh_quality_summary.v1"
    assert mesh_quality["approvalStatus"] == "blocked"
    assert mesh_quality["productionReady"] is False
    assert mesh_quality["openfoam"]["status"] == "blocked"
    assert mesh_quality["openfoam"]["qualityMetrics"]["failedChecks"] == 2
    assert mesh_quality["openfoam"]["qualityMetrics"]["minVolume"] == -1e-12
    assert mesh_quality["openfoam"]["commandRuns"][0]["logPath"] == "log.checkMesh"
    assert "OpenFOAM checkMesh failed 2 check(s)." in mesh_quality["openfoam"]["blockingReasons"]
    artifact_payloads = {artifact["path"]: artifact for artifact in mesh_quality["artifacts"]}
    assert artifact_payloads["mesh/production_mesh_acceptance.json"]["exists"] is True
    assert artifact_payloads["log.checkMesh"]["text"] == "Failed 2 mesh checks.\n"
    assert artifact_payloads["log.surfaceFeatureExtract"]["exists"] is False

    missing_response = client.get(f"/api/jobs/{job.id}/artifact", params={"path": "VTK/missing.vtk"})
    assert missing_response.status_code == 404


def test_job_api_rejects_client_supplied_case_that_was_not_generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "JOB_MANAGER", JobManager(runtime_root=tmp_path))
    app_module.CASES.clear()
    client = TestClient(app_module.app)

    response = client.post(
        "/api/jobs",
        json={
            "id": "case-forged",
            "projectName": "Forged",
            "solver": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "status": "generated",
            "files": {"README.md": "forged"},
            "runCommand": ["python", "-c", "print('not server generated')"],
            "provenance": [],
        },
    )

    assert response.status_code == 400
    assert "Generate the case" in response.json()["detail"]


def test_runtime_api_reports_missing_solver_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_python_module_exists", lambda _module: False)
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["instant-1d"]["runnable"] is True
    assert statuses["instant-1d"]["preferredExecution"] == "browser"
    assert statuses["openfoam"]["runnable"] is False
    assert statuses["openfoam"]["preferredExecution"] == "none"
    assert any("Docker daemon is unavailable" in blocker for blocker in statuses["openfoam"]["blockers"])
    assert any("foamRun" in blocker for blocker in statuses["openfoam"]["blockers"])
    assert statuses["mujoco"]["runnable"] is False
    assert any("python3" in blocker for blocker in statuses["mujoco"]["blockers"])


def test_runtime_api_prefers_docker_for_openfoam_and_su2_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    su2_home = tmp_path / "su2"
    (su2_home / "bin").mkdir(parents=True)
    (su2_home / "bin" / "SU2_CFD").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_python_module_exists", lambda _module: False)
    monkeypatch.setenv("FLOWLAB_SU2_HOME", str(su2_home))
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["openfoam"]["runnable"] is True
    assert statuses["openfoam"]["preferredExecution"] == "docker"
    assert statuses["openfoam"]["dockerImage"] == adapters.DEFAULT_OPENFOAM_IMAGE
    assert statuses["su2"]["runnable"] is True
    assert statuses["su2"]["preferredExecution"] == "docker"
    assert statuses["su2"]["dockerImage"] == "ubuntu:22.04"
    assert statuses["code-saturne"]["runnable"] is False


def test_runtime_api_reports_configured_shared_openfoam_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_python_module_exists", lambda _module: False)
    monkeypatch.setenv(adapters.OPENFOAM_IMAGE_ENV, "flowlab/openfoam:test")
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["openfoam"]["runnable"] is True
    assert statuses["openfoam"]["dockerImage"] == "flowlab/openfoam:test"


def test_runtime_api_prefers_configured_code_saturne_docker_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setenv("FLOWLAB_CODE_SATURNE_IMAGE", "flowlab-code-saturne:test")
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["code-saturne"]["runnable"] is True
    assert statuses["code-saturne"]["preferredExecution"] == "docker"
    assert statuses["code-saturne"]["dockerImage"] == "flowlab-code-saturne:test"
    assert statuses["code-saturne"]["nativeAvailable"] is False


def test_runtime_api_reports_native_code_saturne_and_mujoco(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    def command_exists(command: str) -> bool:
        return command in {"code_saturne", "python3"}

    monkeypatch.setattr(adapters, "_command_exists", command_exists)
    monkeypatch.setattr(adapters, "_python_module_exists", lambda module: module == "mujoco")
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["code-saturne"]["runnable"] is True
    assert statuses["code-saturne"]["preferredExecution"] == "native"
    assert statuses["mujoco"]["runnable"] is True
    assert statuses["mujoco"]["preferredExecution"] == "native"
    assert statuses["mujoco"]["pythonModuleAvailable"] is True


def test_runtime_api_reports_configured_mujoco_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    monkeypatch.setenv("FLOWLAB_MUJOCO_PYTHON", str(python))
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(
        adapters,
        "_python_module_exists_for_command",
        lambda command, module: command == str(python) and module == "mujoco",
    )
    client = TestClient(app_module.app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    statuses = {item["solver"]: item for item in response.json()}
    assert statuses["mujoco"]["runnable"] is True
    assert statuses["mujoco"]["preferredExecution"] == "native"
    assert statuses["mujoco"]["nativeCommand"] == str(python)
    assert statuses["mujoco"]["pythonModuleAvailable"] is True
