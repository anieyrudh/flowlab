from __future__ import annotations

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server.flowlab import derived as derived_module
from server.flowlab.derived import (
    DERIVED_CACHE,
    DerivedArtifactRef,
    DerivedCache,
    DerivedFieldRequest,
    DerivedGridRequest,
    DerivedImportArtifact,
    DerivedPathlineRequest,
    DerivedVisualizationRequest,
    _safe_artifact_name,
    derive_visualization,
)
from server.flowlab.execution import JobManager
from server.flowlab.schemas import JobRecord
from server.flowlab.schemas import ResultComponentMap, SolverCase


def _legacy_vtk(
    points: list[tuple[float, float, float]],
    cells: list[list[int]],
    cell_types: list[int],
    *,
    point_scalars: dict[str, list[float]] | None = None,
    point_vectors: dict[str, list[tuple[float, float, float]]] | None = None,
    cell_scalars: dict[str, list[float]] | None = None,
) -> str:
    lines = [
        "# vtk DataFile Version 3.0",
        "derived-test",
        "ASCII",
        "DATASET UNSTRUCTURED_GRID",
        f"POINTS {len(points)} float",
        *(" ".join(str(value) for value in point) for point in points),
        f"CELLS {len(cells)} {sum(len(cell) + 1 for cell in cells)}",
        *(" ".join([str(len(cell)), *(str(index) for index in cell)]) for cell in cells),
        f"CELL_TYPES {len(cell_types)}",
        *(str(cell_type) for cell_type in cell_types),
    ]
    if point_scalars or point_vectors:
        lines.append(f"POINT_DATA {len(points)}")
        for name, values in sorted((point_scalars or {}).items()):
            lines.extend([f"SCALARS {name} float 1", "LOOKUP_TABLE default", *(str(value) for value in values)])
        for name, values in sorted((point_vectors or {}).items()):
            lines.append(f"VECTORS {name} float")
            lines.extend(" ".join(str(value) for value in vector) for vector in values)
    if cell_scalars:
        lines.append(f"CELL_DATA {len(cells)}")
        for name, values in sorted(cell_scalars.items()):
            lines.extend([f"SCALARS {name} float 1", "LOOKUP_TABLE default", *(str(value) for value in values)])
    return "\n".join(lines) + "\n"


CELL_FIXTURES = {
    "tet": (
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
        [0, 1, 2, 3],
        10,
    ),
    "hex": (
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
        [0, 1, 2, 3, 4, 5, 6, 7],
        12,
    ),
    "wedge": (
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1)],
        [0, 1, 2, 3, 4, 5],
        13,
    ),
    "pyramid": (
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0.5, 1)],
        [0, 1, 2, 3, 4],
        14,
    ),
}


def _import_volume(
    text: str,
    *,
    fields: list[DerivedFieldRequest],
    gradients: list[str] | None = None,
    scope: str,
) -> dict:
    request = DerivedVisualizationRequest(
        operation="volume",
        artifacts=[DerivedArtifactRef(path="result.vtk")],
        fields=fields,
        grid=DerivedGridRequest(dimensions=(4, 4, 4), gradients=gradients or []),
    )
    return derive_visualization(
        request,
        scope=scope,
        inline_artifacts=[DerivedImportArtifact(path="result.vtk", text=text)],
    )


def _floats(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def _uints(blob: bytes) -> tuple[int, ...]:
    return struct.unpack(f"<{len(blob) // 4}I", blob)


@pytest.fixture(autouse=True)
def clear_derived_cache() -> None:
    DERIVED_CACHE.clear()


@pytest.mark.parametrize("name", ["tet", "hex", "wedge", "pyramid"])
def test_point_fields_use_analytic_barycentric_interpolation(name: str) -> None:
    points, cell, cell_type = CELL_FIXTURES[name]
    values = [x + 2 * y + 3 * z for x, y, z in points]
    manifest = _import_volume(
        _legacy_vtk(points, [cell], [cell_type], point_scalars={"phi": values}),
        fields=[DerivedFieldRequest(name="phi", location="point", kind="scalar", unit="K")],
        scope=f"analytic-{name}",
    )
    request_hash = manifest["requestSha256"]
    validity = DERIVED_CACHE.blob(f"analytic-{name}", request_hash, "validity.bin")
    sampled = _floats(DERIVED_CACHE.blob(f"analytic-{name}", request_hash, "values-000.bin"))
    source_ids = _uints(DERIVED_CACHE.blob(f"analytic-{name}", request_hash, "source-cell-ids.bin"))
    weights = _floats(DERIVED_CACHE.blob(f"analytic-{name}", request_hash, "spatial-weights.bin"))
    assert any(validity)
    dimensions = manifest["grid"]["dimensions"]
    minimum = manifest["grid"]["bounds"]["min"]
    spacing = manifest["grid"]["spacing"]
    for index, valid in enumerate(validity):
        if not valid:
            continue
        x_index = index % dimensions[0]
        y_index = (index // dimensions[0]) % dimensions[1]
        z_index = index // (dimensions[0] * dimensions[1])
        point = [
            minimum[0] + (x_index + 0.5) * spacing[0],
            minimum[1] + (y_index + 0.5) * spacing[1],
            minimum[2] + (z_index + 0.5) * spacing[2],
        ]
        assert sampled[index] == pytest.approx(point[0] + 2 * point[1] + 3 * point[2], abs=2e-6)
        assert source_ids[index] == 0
        assert sum(weights[index * 4 : index * 4 + 4]) == pytest.approx(1.0, abs=2e-6)


def test_cell_fields_remain_cell_constant() -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    manifest = _import_volume(
        _legacy_vtk(points, [cell], [cell_type], cell_scalars={"pressure": [7.25]}),
        fields=[DerivedFieldRequest(name="pressure", location="cell", kind="scalar", unit="Pa")],
        scope="cell-constant",
    )
    request_hash = manifest["requestSha256"]
    validity = DERIVED_CACHE.blob("cell-constant", request_hash, "validity.bin")
    values = _floats(DERIVED_CACHE.blob("cell-constant", request_hash, "values-000.bin"))
    assert {round(values[index], 6) for index, valid in enumerate(validity) if valid} == {7.25}


def test_blobs_hashes_and_cache_are_deterministic() -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    text = _legacy_vtk(points, [cell], [cell_type], point_scalars={"p": [sum(point) for point in points]})
    first = _import_volume(
        text,
        fields=[DerivedFieldRequest(name="p", location="point", kind="scalar", unit="Pa")],
        scope="deterministic",
    )
    second = _import_volume(
        text,
        fields=[DerivedFieldRequest(name="p", location="point", kind="scalar", unit="Pa")],
        scope="deterministic",
    )
    assert first == second
    for descriptor in first["blobs"]:
        blob = DERIVED_CACHE.blob("deterministic", first["requestSha256"], descriptor["name"])
        assert len(blob) == descriptor["byteLength"]
        assert descriptor["sha256"]


def test_gradient_requires_complete_neighbor_mask_and_matches_linear_field() -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    text = _legacy_vtk(points, [cell], [cell_type], point_scalars={"p": [x + 2 * y + 3 * z for x, y, z in points]})
    manifest = _import_volume(
        text,
        fields=[DerivedFieldRequest(name="p", location="point", kind="scalar", unit="Pa")],
        gradients=["pressure"],
        scope="gradient",
    )
    gradient = manifest["gradients"][0]
    mask = DERIVED_CACHE.blob("gradient", manifest["requestSha256"], gradient["validity"]["name"])
    values = _floats(DERIVED_CACHE.blob("gradient", manifest["requestSha256"], gradient["values"]["name"]))
    assert sum(mask) == 8
    for index, valid in enumerate(mask):
        if valid:
            assert values[index * 3 : index * 3 + 3] == pytest.approx((1, 2, 3), abs=2e-6)


def test_overlapping_source_cells_are_marked_ambiguous_and_imports_are_probe_only() -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    text = _legacy_vtk(
        points,
        [cell, cell],
        [cell_type, cell_type],
        cell_scalars={"pressure": [1, 2]},
    )
    manifest = _import_volume(
        text,
        fields=[DerivedFieldRequest(name="pressure", location="cell", kind="scalar", unit="Pa")],
        scope="ambiguous",
    )
    ambiguity = DERIVED_CACHE.blob("ambiguous", manifest["requestSha256"], "ambiguity.bin")
    assert all(ambiguity)
    assert manifest["componentResolution"]["status"] == "probe-only"
    assert manifest["provenance"]["ambiguousSelections"] == "probe-only"


@pytest.mark.parametrize(
    "path",
    ["/absolute/result.vtk", "../result.vtk", "mesh/result.vtk", "result.txt", "nested/../result.vtk"],
)
def test_derived_artifact_paths_fail_closed(path: str) -> None:
    with pytest.raises(ValueError):
        _safe_artifact_name(path)


def test_request_and_cache_limits_reject_instead_of_clamping(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="between 2 and 96"):
        DerivedGridRequest(dimensions=(97, 2, 2))
    with pytest.raises(ValueError, match="at most 512"):
        DerivedPathlineRequest(
            seeds=[(0, 0, 0)] * 513,
            stepSeconds=0.1,
        )
    with pytest.raises(ValueError, match="less than or equal to 250000"):
        DerivedPathlineRequest(
            seeds=[(0, 0, 0)],
            stepSeconds=0.1,
            maxVertices=250001,
        )

    cache = DerivedCache()
    monkeypatch.setattr(derived_module, "MAX_DERIVED_CACHE_BYTES_PER_SCOPE", 32)
    with pytest.raises(ValueError, match="cache budget exceeded"):
        cache.put("job:test", "a" * 64, {}, {"values.bin": b"x" * 64})
    with pytest.raises(ValueError, match="blob name is unsafe"):
        cache.blob("job:test", "a" * 64, "../values.bin")


def test_planar_su2_style_results_are_never_extruded() -> None:
    text = _legacy_vtk(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [[0, 1, 2, 3]],
        [9],
        point_scalars={"Pressure": [1, 2, 3, 4]},
    )
    with pytest.raises(ValueError, match="no surface/planar extrusion|Planar"):
        _import_volume(
            text,
            fields=[DerivedFieldRequest(name="Pressure", location="point", kind="scalar", unit="Pa")],
            scope="planar",
        )


def test_generated_job_units_and_component_map_are_verified(tmp_path: Path) -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    artifact = tmp_path / "VTK" / "result.vtk"
    artifact.parent.mkdir()
    artifact.write_text(
        _legacy_vtk(points, [cell], [cell_type], point_vectors={"U": [(1, 0, 0)] * len(points)}),
        encoding="utf-8",
    )
    case = SolverCase(
        projectName="derived",
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
        resultComponentMap=ResultComponentMap(
            version=1,
            projectSha256="a" * 64,
            artifactBindings=[{"artifactName": "*", "edgeId": "edge-1", "scope": "all-cells"}],
        ),
    )
    request = DerivedVisualizationRequest(
        operation="volume",
        artifacts=[DerivedArtifactRef(path="VTK/result.vtk")],
        fields=[DerivedFieldRequest(name="U", location="point", kind="vector", unit="m/s")],
        grid=DerivedGridRequest(dimensions=(2, 2, 2)),
    )
    manifest = derive_visualization(request, scope="job:test", case=case, case_dir=tmp_path)
    assert manifest["unitAuthority"] == "case-contract"
    assert manifest["componentResolution"]["status"] == "source-cell-map"
    with pytest.raises(ValueError, match="unit mismatch"):
        derive_visualization(
            request.model_copy(
                update={"fields": [DerivedFieldRequest(name="U", location="point", kind="vector", unit="km/h")]}
            ),
            scope="job:test-bad-unit",
            case=case,
            case_dir=tmp_path,
        )


def test_generated_component_map_must_match_artifact_and_source_cell_count(tmp_path: Path) -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    artifact = tmp_path / "VTK" / "result.vtk"
    artifact.parent.mkdir()
    artifact.write_text(
        _legacy_vtk(points, [cell], [cell_type], point_vectors={"U": [(1, 0, 0)] * len(points)}),
        encoding="utf-8",
    )
    request = DerivedVisualizationRequest(
        operation="volume",
        artifacts=[DerivedArtifactRef(path="VTK/result.vtk")],
        fields=[DerivedFieldRequest(name="U", location="point", kind="vector", unit="m/s")],
        grid=DerivedGridRequest(dimensions=(2, 2, 2)),
    )
    unrelated = SolverCase(
        projectName="unrelated-map",
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
        resultComponentMap=ResultComponentMap(
            version=2,
            projectSha256="a" * 64,
            artifactBindings=[
                {
                    "artifactName": "postProcessing/flowlabNative/*.vtk",
                    "scope": "cell-ranges",
                    "sourceCellCount": 1,
                    "cellRanges": [{"edgeId": "edge-1", "cellStart": 0, "cellCount": 1}],
                }
            ],
        ),
    )
    unrelated_manifest = derive_visualization(
        request,
        scope="job:unrelated-map",
        case=unrelated,
        case_dir=tmp_path,
    )
    assert unrelated_manifest["componentResolution"]["status"] == "probe-only"
    assert unrelated_manifest["componentResolution"]["map"] is None

    wrong_count = unrelated.model_copy(
        update={
            "projectName": "wrong-count-map",
            "resultComponentMap": ResultComponentMap(
                version=2,
                projectSha256="a" * 64,
                artifactBindings=[
                    {
                        "artifactName": "VTK/*.vtk",
                        "scope": "cell-ranges",
                        "sourceCellCount": 2,
                        "cellRanges": [{"edgeId": "edge-1", "cellStart": 0, "cellCount": 1}],
                    }
                ],
            ),
        }
    )
    wrong_count_manifest = derive_visualization(
        request,
        scope="job:wrong-count-map",
        case=wrong_count,
        case_dir=tmp_path,
    )
    assert wrong_count_manifest["componentResolution"]["status"] == "probe-only"
    assert "declares 2" in wrong_count_manifest["componentResolution"]["reason"]


def _pathline_request(paths: list[tuple[str, float]], *, step: float = 0.25) -> DerivedVisualizationRequest:
    return DerivedVisualizationRequest(
        operation="pathlines",
        artifacts=[DerivedArtifactRef(path=path, time=time) for path, time in paths],
        fields=[DerivedFieldRequest(name="U", location="point", kind="vector", unit="m/s")],
        pathlines=DerivedPathlineRequest(seeds=[(0.1, 0.5, 0.5)], stepSeconds=step, maxVertices=100),
    )


def test_constant_and_time_linear_pathlines_are_deterministic() -> None:
    points = [(2 * x, y, z) for x, y, z in CELL_FIXTURES["hex"][0]]
    cell = CELL_FIXTURES["hex"][1]
    constant = _legacy_vtk(points, [cell], [12], point_vectors={"U": [(0.5, 0, 0)] * len(points)})
    request = _pathline_request([("t0.vtk", 0), ("t1.vtk", 1)])
    manifest = derive_visualization(
        request,
        scope="constant-pathline",
        inline_artifacts=[
            DerivedImportArtifact(path="t0.vtk", time=0, text=constant),
            DerivedImportArtifact(path="t1.vtk", time=1, text=constant),
        ],
    )
    positions = _floats(DERIVED_CACHE.blob("constant-pathline", manifest["requestSha256"], "pathline-positions.bin"))
    assert positions[-3:] == pytest.approx((0.6, 0.5, 0.5), abs=2e-6)

    zero = _legacy_vtk(points, [cell], [12], point_vectors={"U": [(0, 0, 0)] * len(points)})
    one = _legacy_vtk(points, [cell], [12], point_vectors={"U": [(1, 0, 0)] * len(points)})
    linear = derive_visualization(
        request,
        scope="linear-pathline",
        inline_artifacts=[
            DerivedImportArtifact(path="t0.vtk", time=0, text=zero),
            DerivedImportArtifact(path="t1.vtk", time=1, text=one),
        ],
    )
    linear_positions = _floats(DERIVED_CACHE.blob("linear-pathline", linear["requestSha256"], "pathline-positions.bin"))
    assert linear_positions[-3:] == pytest.approx((0.6, 0.5, 0.5), abs=3e-6)
    assert linear["pathlines"]["integration"] == "deterministic-rk4-time-linear-v1"


def test_pathlines_reject_time_geometry_units_and_missing_velocity() -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    text = _legacy_vtk(points, [cell], [cell_type], point_vectors={"U": [(0, 0, 0)] * len(points)})
    with pytest.raises(ValueError, match="strictly increasing"):
        derive_visualization(
            _pathline_request([("a.vtk", 1), ("b.vtk", 1)]),
            scope="bad-time",
            inline_artifacts=[
                DerivedImportArtifact(path="a.vtk", time=1, text=text),
                DerivedImportArtifact(path="b.vtk", time=1, text=text),
            ],
        )
    changed = _legacy_vtk(
        [(x * 2, y, z) for x, y, z in points],
        [cell],
        [cell_type],
        point_vectors={"U": [(0, 0, 0)] * len(points)},
    )
    with pytest.raises(ValueError, match="identical geometry"):
        derive_visualization(
            _pathline_request([("a.vtk", 0), ("b.vtk", 1)]),
            scope="bad-geometry",
            inline_artifacts=[
                DerivedImportArtifact(path="a.vtk", time=0, text=text),
                DerivedImportArtifact(path="b.vtk", time=1, text=changed),
            ],
        )
    with pytest.raises(ValueError, match="missing"):
        derive_visualization(
            _pathline_request([("a.vtk", 0), ("b.vtk", 1)]),
            scope="missing-u",
            inline_artifacts=[
                DerivedImportArtifact(path="a.vtk", time=0, text=text),
                DerivedImportArtifact(
                    path="b.vtk",
                    time=1,
                    text=_legacy_vtk(points, [cell], [cell_type], point_scalars={"p": [0] * len(points)}),
                ),
            ],
        )


def test_job_and_import_derived_api_return_hashed_binary_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, cell, cell_type = CELL_FIXTURES["hex"]
    text = _legacy_vtk(points, [cell], [cell_type], point_vectors={"U": [(1, 0, 0)] * len(points)})
    case_dir = tmp_path / "case"
    artifact = case_dir / "VTK" / "result.vtk"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(text, encoding="utf-8")
    case = SolverCase(
        projectName="derived-api",
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
        resultComponentMap=ResultComponentMap(
            version=1,
            projectSha256="a" * 64,
            artifactBindings=[{"artifactName": "*", "edgeId": "edge-1", "scope": "all-cells"}],
        ),
    )
    job = JobRecord(caseId=case.id, solver="openfoam", status="complete", caseDir=str(case_dir))
    manager = JobManager(runtime_root=tmp_path / "runtime")
    manager._store(job)
    monkeypatch.setattr(app_module, "JOB_MANAGER", manager)
    app_module.CASES.clear()
    app_module.CASES[case.id] = case
    client = TestClient(app_module.app)
    request = {
        "schema": "flowlab.derived_visualization_request.v1",
        "operation": "volume",
        "artifacts": [{"path": "VTK/result.vtk", "time": 0}],
        "fields": [{"name": "U", "location": "point", "kind": "vector", "unit": "m/s"}],
        "grid": {"dimensions": [2, 2, 2], "gradients": ["speed"]},
    }

    response = client.post(f"/api/jobs/{job.id}/derived", json=request)
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["schema"] == "flowlab.derived_visualization_manifest.v1"
    assert manifest["componentResolution"]["status"] == "source-cell-map"
    assert manifest["visualizationOnly"] is True
    descriptor = manifest["blobs"][0]
    blob = client.get(
        f"/api/jobs/{job.id}/derived/{manifest['requestSha256']}/blob/{descriptor['name']}"
    )
    assert blob.status_code == 200
    assert len(blob.content) == descriptor["byteLength"]

    app_module.CASES.clear()
    manager.cases[job.id] = case
    DERIVED_CACHE.clear()
    restored = client.post(f"/api/jobs/{job.id}/derived", json=request)
    assert restored.status_code == 200
    assert restored.json()["componentResolution"]["status"] == "source-cell-map"

    imported = client.post(
        "/api/derived/import",
        json={
            "request": request,
            "artifacts": [{"path": "VTK/result.vtk", "time": 0, "text": text}],
        },
    )
    assert imported.status_code == 200
    imported_manifest = imported.json()
    assert imported_manifest["componentResolution"]["status"] == "probe-only"
    imported_blob = client.get(
        f"/api/derived/import/{imported_manifest['requestSha256']}/blob/{imported_manifest['blobs'][0]['name']}"
    )
    assert imported_blob.status_code == 200

    rejected_preview = client.post(
        f"/api/jobs/{job.id}/derived",
        json={
            **request,
            "artifacts": [{"path": "mesh/result.vtk", "time": 0}],
        },
    )
    assert rejected_preview.status_code == 409
    assert "full VTK/VTU result artifact outside mesh" in rejected_preview.json()["detail"]
