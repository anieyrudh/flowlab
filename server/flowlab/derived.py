from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import sys
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import parse_vtk_result
from .schemas import SolverCase

DERIVED_REQUEST_SCHEMA = "flowlab.derived_visualization_request.v1"
DERIVED_MANIFEST_SCHEMA = "flowlab.derived_visualization_manifest.v1"
DERIVED_BLOB_SCHEMA = "flowlab.derived_visualization_blob.v1"

DEFAULT_GRID_DIMENSION = 64
MAX_GRID_DIMENSION = 96
MAX_ARTIFACT_SET_BYTES = 48 * 1024 * 1024
MAX_BROWSER_RESIDENCY_BYTES = 96 * 1024 * 1024
MAX_DERIVED_CACHE_BYTES_PER_SCOPE = 256 * 1024 * 1024
MAX_SEEDS = 512
MAX_PATHLINE_VERTICES = 250_000
MAX_ISO_TRIANGLES = 500_000
INVALID_SOURCE_CELL = 0xFFFFFFFF

VTK_TETRA = 10
VTK_HEXAHEDRON = 12
VTK_WEDGE = 13
VTK_PYRAMID = 14
VOLUME_CELL_ARITY = {
    VTK_TETRA: 4,
    VTK_HEXAHEDRON: 8,
    VTK_WEDGE: 6,
    VTK_PYRAMID: 5,
}
SUBCELL_VERTICES: dict[int, tuple[tuple[int, int, int, int], ...]] = {
    VTK_TETRA: ((0, 1, 2, 3),),
    VTK_HEXAHEDRON: (
        (0, 1, 2, 6),
        (0, 2, 3, 6),
        (0, 3, 7, 6),
        (0, 7, 4, 6),
        (0, 4, 5, 6),
        (0, 5, 1, 6),
    ),
    VTK_WEDGE: (
        (0, 1, 2, 3),
        (1, 2, 4, 3),
        (2, 4, 5, 3),
    ),
    VTK_PYRAMID: (
        (0, 1, 2, 4),
        (0, 2, 3, 4),
    ),
}


class DerivedArtifactRef(BaseModel):
    path: str = Field(min_length=1)
    time: float | None = None


class DerivedFieldRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: Literal["point", "cell"]
    kind: Literal["scalar", "vector"]
    unit: str = Field(min_length=1, max_length=64)


class DerivedGridRequest(BaseModel):
    dimensions: tuple[int, int, int] = (
        DEFAULT_GRID_DIMENSION,
        DEFAULT_GRID_DIMENSION,
        DEFAULT_GRID_DIMENSION,
    )
    gradients: list[Literal["pressure", "speed"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "DerivedGridRequest":
        if any(value < 2 or value > MAX_GRID_DIMENSION for value in self.dimensions):
            raise ValueError(f"Grid dimensions must be between 2 and {MAX_GRID_DIMENSION}.")
        if len(set(self.gradients)) != len(self.gradients):
            raise ValueError("Gradient requests must be unique.")
        return self


class DerivedPathlineRequest(BaseModel):
    seeds: list[tuple[float, float, float]] = Field(min_length=1, max_length=MAX_SEEDS)
    stepSeconds: float = Field(gt=0)
    maxVertices: int = Field(default=MAX_PATHLINE_VERTICES, ge=2, le=MAX_PATHLINE_VERTICES)


class DerivedVisualizationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: Literal["flowlab.derived_visualization_request.v1"] = Field(
        default=DERIVED_REQUEST_SCHEMA,
        alias="schema",
    )
    operation: Literal["volume", "pathlines"]
    artifacts: list[DerivedArtifactRef] = Field(min_length=1, max_length=500)
    fields: list[DerivedFieldRequest] = Field(min_length=1, max_length=16)
    grid: DerivedGridRequest | None = None
    pathlines: DerivedPathlineRequest | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "DerivedVisualizationRequest":
        keys = [(field.name, field.location, field.kind) for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Derived fields must be unique by name, location, and kind.")
        if self.operation == "volume":
            if self.grid is None:
                self.grid = DerivedGridRequest()
            if self.pathlines is not None:
                raise ValueError("A volume request cannot include pathline controls.")
        else:
            if self.pathlines is None:
                raise ValueError("A pathline request requires pathline controls.")
            if self.grid is not None:
                raise ValueError("A pathline request cannot include grid controls.")
            velocity = [
                field
                for field in self.fields
                if field.name in {"U", "Velocity", "velocity"}
                and field.kind == "vector"
            ]
            if len(velocity) != 1:
                raise ValueError("Pathlines require exactly one vector velocity field named U or Velocity.")
            if len(self.artifacts) < 2:
                raise ValueError("Transient pathlines require at least two full result artifacts.")
        return self


class DerivedImportArtifact(DerivedArtifactRef):
    text: str


class DerivedImportRequest(BaseModel):
    request: DerivedVisualizationRequest
    artifacts: list[DerivedImportArtifact] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_artifacts(self) -> "DerivedImportRequest":
        references = [(artifact.path, artifact.time) for artifact in self.request.artifacts]
        inline = [(artifact.path, artifact.time) for artifact in self.artifacts]
        if references != inline:
            raise ValueError("Inline artifacts must exactly match the request artifact order, paths, and timestamps.")
        return self


@dataclass(frozen=True)
class _SourceArtifact:
    path: str
    time: float | None
    text: str
    size: int
    sha256: str
    dataset: dict[str, Any]
    geometry_digest: str
    cell_order_digest: str


@dataclass(frozen=True)
class _Subcell:
    source_cell: int
    subcell_id: int
    point_indices: tuple[int, int, int, int]
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]


@dataclass(frozen=True)
class _Sample:
    source_cell: int
    subcell_id: int
    point_indices: tuple[int, int, int, int]
    weights: tuple[float, float, float, float]
    ambiguous: bool


class DerivedCache:
    """A deterministic, bounded in-memory cache for visualization-only products."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, tuple[dict[str, Any], dict[str, bytes], int]]] = {}

    def get(self, scope: str, request_sha256: str) -> tuple[dict[str, Any], dict[str, bytes]] | None:
        with self._lock:
            entry = self._entries.get(scope, {}).get(request_sha256)
            return (entry[0], entry[1]) if entry else None

    def put(self, scope: str, request_sha256: str, manifest: dict[str, Any], blobs: dict[str, bytes]) -> None:
        byte_count = sum(len(blob) for blob in blobs.values()) + len(_canonical_json(manifest).encode("utf-8"))
        with self._lock:
            entries = self._entries.setdefault(scope, {})
            if request_sha256 in entries:
                return
            current = sum(entry[2] for entry in entries.values())
            if current + byte_count > MAX_DERIVED_CACHE_BYTES_PER_SCOPE:
                raise ValueError(
                    "Derived cache budget exceeded: "
                    f"{current + byte_count} bytes requested, {MAX_DERIVED_CACHE_BYTES_PER_SCOPE} bytes allowed per job/import."
                )
            entries[request_sha256] = (manifest, blobs, byte_count)

    def blob(self, scope: str, request_sha256: str, name: str) -> bytes:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.bin", name):
            raise ValueError("Derived blob name is unsafe.")
        with self._lock:
            entry = self._entries.get(scope, {}).get(request_sha256)
            if not entry or name not in entry[1]:
                raise FileNotFoundError("Derived blob not found.")
            return entry[1][name]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


DERIVED_CACHE = DerivedCache()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _safe_artifact_name(path_text: str) -> str:
    path = PurePosixPath(path_text)
    if (
        not path_text
        or path.is_absolute()
        or "\x00" in path_text
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() not in {".vtk", ".vtu"}
        or (path.parts and path.parts[0].lower() == "mesh")
    ):
        raise ValueError("Derived source must be a relative full VTK/VTU result artifact outside mesh/.")
    return str(path)


def _load_job_artifacts(case_dir: Path, references: list[DerivedArtifactRef]) -> list[tuple[str, float | None, str]]:
    root = case_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError("Job case directory is unavailable.")
    loaded: list[tuple[str, float | None, str]] = []
    total = 0
    for reference in references:
        relative = _safe_artifact_name(reference.path)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Derived source path escapes the job case directory.")
        if not path.is_file():
            raise FileNotFoundError(f"Derived source artifact not found: {relative}")
        size = path.stat().st_size
        total += size
        if total > MAX_ARTIFACT_SET_BYTES:
            raise ValueError(
                f"Derived artifact set exceeds {MAX_ARTIFACT_SET_BYTES} bytes; sparse previews cannot substitute for full artifacts."
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Derived source artifact is not UTF-8 ASCII VTK/VTU: {relative}") from exc
        loaded.append((relative, reference.time, text))
    return loaded


def _load_inline_artifacts(artifacts: list[DerivedImportArtifact]) -> list[tuple[str, float | None, str]]:
    loaded: list[tuple[str, float | None, str]] = []
    total = 0
    for artifact in artifacts:
        relative = _safe_artifact_name(artifact.path)
        size = len(artifact.text.encode("utf-8"))
        total += size
        if total > MAX_ARTIFACT_SET_BYTES:
            raise ValueError(f"Derived artifact set exceeds {MAX_ARTIFACT_SET_BYTES} bytes.")
        loaded.append((relative, artifact.time, artifact.text))
    return loaded


def _float64_bytes(values: list[float]) -> bytes:
    return b"".join(struct.pack("<d", float(value)) for value in values)


def _geometry_digests(dataset: dict[str, Any]) -> tuple[str, str]:
    points = dataset.get("points")
    cells = dataset.get("cells")
    cell_types = dataset.get("cellTypes")
    if not isinstance(points, list) or not isinstance(cells, list) or not isinstance(cell_types, list):
        raise ValueError("Result artifact is missing complete points, cells, or cell types.")
    geometry = hashlib.sha256()
    geometry.update(struct.pack("<Q", len(points)))
    for point in points:
        if not isinstance(point, list) or len(point) != 3 or not all(math.isfinite(float(value)) for value in point):
            raise ValueError("Result artifact contains a non-finite or malformed point.")
        geometry.update(_float64_bytes([float(value) for value in point]))
    order = hashlib.sha256()
    order.update(geometry.digest())
    order.update(struct.pack("<Q", len(cells)))
    if len(cells) != len(cell_types):
        raise ValueError("Result artifact cellTypes count does not match cells.")
    for cell_type, cell in zip(cell_types, cells, strict=True):
        order.update(struct.pack("<I", int(cell_type)))
        order.update(struct.pack("<I", len(cell)))
        for point_index in cell:
            order.update(struct.pack("<I", int(point_index)))
    return geometry.hexdigest(), order.hexdigest()


def _prepare_sources(raw: list[tuple[str, float | None, str]]) -> list[_SourceArtifact]:
    prepared: list[_SourceArtifact] = []
    for path, time, text in raw:
        encoded = text.encode("utf-8")
        dataset = parse_vtk_result(text)
        geometry_digest, cell_order_digest = _geometry_digests(dataset)
        prepared.append(
            _SourceArtifact(
                path=path,
                time=time,
                text=text,
                size=len(encoded),
                sha256=hashlib.sha256(encoded).hexdigest(),
                dataset=dataset,
                geometry_digest=geometry_digest,
                cell_order_digest=cell_order_digest,
            )
        )
    return prepared


def _authoritative_unit(case: SolverCase, field_name: str) -> str | None:
    normalized = field_name.lower()
    if normalized in {"u", "velocity", "vel"}:
        return "m/s"
    if normalized in {"t", "temperature", "temp"}:
        return "K"
    if normalized.startswith("alpha") or "fraction" in normalized:
        return "1"
    if normalized in {"rho", "density"}:
        return "kg/m3"
    if normalized in {"p", "p_rgh"}:
        if case.solver == "openfoam" and case.advancedMode == "incompressible-navier-stokes":
            return "m2/s2"
        return "Pa"
    if normalized in {"pressure", "static_pressure", "total_pressure"}:
        return "Pa"
    return None


def _validate_units(case: SolverCase | None, fields: list[DerivedFieldRequest]) -> str:
    if case is None:
        if any(not field.unit.strip() or field.unit == "solver units" for field in fields):
            raise ValueError("Imported derivation requires an explicit non-placeholder unit for every field.")
        return "user-declared"
    for field in fields:
        expected = _authoritative_unit(case, field.name)
        if expected is None:
            raise ValueError(f"Field unit cannot be verified from the generated case contract: {field.name}")
        if field.unit != expected:
            raise ValueError(f"Field unit mismatch for {field.name}: request declares {field.unit}, case contract requires {expected}.")
    return "case-contract"


def _validate_field(dataset: dict[str, Any], request: DerivedFieldRequest) -> list[Any]:
    data_key = "pointData" if request.location == "point" else "cellData"
    kind_key = "scalars" if request.kind == "scalar" else "vectors"
    data = dataset.get(data_key)
    values = data.get(kind_key, {}).get(request.name) if isinstance(data, dict) else None
    if not isinstance(values, list):
        raise ValueError(f"Required {request.location} {request.kind} field is missing: {request.name}")
    expected = len(dataset["points"]) if request.location == "point" else len(dataset["cells"])
    if len(values) != expected:
        raise ValueError(f"Field {request.name} tuple count {len(values)} does not match {request.location} count {expected}.")
    components = 1 if request.kind == "scalar" else 3
    for value in values:
        entries = [value] if components == 1 else value
        if not isinstance(entries, list) or len(entries) != components:
            raise ValueError(f"Field {request.name} contains a malformed tuple.")
        if not all(math.isfinite(float(component)) for component in entries):
            raise ValueError(f"Field {request.name} contains non-finite values.")
    return values


def _vector_sub(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return left[0] - right[0], left[1] - right[1], left[2] - right[2]


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _tetra_weights(
    point: tuple[float, float, float],
    vertices: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float, float, float] | None:
    p0, p1, p2, p3 = vertices
    a = _vector_sub(p1, p0)
    b = _vector_sub(p2, p0)
    c = _vector_sub(p3, p0)
    relative = _vector_sub(point, p0)
    determinant = _dot(a, _cross(b, c))
    scale = max(
        math.dist(p0, p1),
        math.dist(p0, p2),
        math.dist(p0, p3),
        1.0,
    )
    if abs(determinant) <= 1e-14 * scale**3:
        return None
    w1 = _dot(relative, _cross(b, c)) / determinant
    w2 = _dot(a, _cross(relative, c)) / determinant
    w3 = _dot(a, _cross(b, relative)) / determinant
    weights = (1.0 - w1 - w2 - w3, w1, w2, w3)
    epsilon = 2e-8
    if any(weight < -epsilon or weight > 1.0 + epsilon for weight in weights):
        return None
    normalized = tuple(0.0 if abs(weight) < epsilon else 1.0 if abs(weight - 1.0) < epsilon else weight for weight in weights)
    total = sum(normalized)
    return tuple(weight / total for weight in normalized)  # type: ignore[return-value]


class SpatialIndex:
    def __init__(self, dataset: dict[str, Any]) -> None:
        points = dataset["points"]
        cells = dataset["cells"]
        cell_types = dataset["cellTypes"]
        if not points or not cells:
            raise ValueError("Derived visualization requires non-empty full source topology.")
        spans = [
            max(float(point[axis]) for point in points) - min(float(point[axis]) for point in points)
            for axis in range(3)
        ]
        if any(span <= max(spans) * 1e-12 for span in spans):
            raise ValueError("Planar or collapsed results cannot be derived into a 3D volume; SU2 planar results are never extruded.")
        subcells: list[_Subcell] = []
        for source_cell, (cell, cell_type) in enumerate(zip(cells, cell_types, strict=True)):
            expected = VOLUME_CELL_ARITY.get(int(cell_type))
            if expected is None:
                raise ValueError(f"Derived volume topology does not support VTK cell type {cell_type}; no surface/planar extrusion is allowed.")
            if len(cell) != expected or len(set(cell)) != expected:
                raise ValueError(f"Cell {source_cell} does not provide complete VTK {cell_type} topology.")
            for subcell_id, local_vertices in enumerate(SUBCELL_VERTICES[int(cell_type)]):
                point_indices = tuple(int(cell[index]) for index in local_vertices)
                vertices = tuple(tuple(float(value) for value in points[index]) for index in point_indices)
                determinant = _dot(
                    _vector_sub(vertices[1], vertices[0]),
                    _cross(_vector_sub(vertices[2], vertices[0]), _vector_sub(vertices[3], vertices[0])),
                )
                if abs(determinant) <= 1e-18:
                    raise ValueError(f"Cell {source_cell} has a degenerate tetrahedral subcell {subcell_id}.")
                minimum = tuple(min(vertex[axis] for vertex in vertices) for axis in range(3))
                maximum = tuple(max(vertex[axis] for vertex in vertices) for axis in range(3))
                subcells.append(_Subcell(source_cell, subcell_id, point_indices, minimum, maximum))
        self.dataset = dataset
        self.subcells = subcells
        self.minimum = tuple(min(float(point[axis]) for point in points) for axis in range(3))
        self.maximum = tuple(max(float(point[axis]) for point in points) for axis in range(3))
        bin_count = max(1, min(32, math.ceil(len(subcells) ** (1 / 3))))
        self.bin_dimensions = (bin_count, bin_count, bin_count)
        self.bins: dict[tuple[int, int, int], list[int]] = {}
        for index, subcell in enumerate(subcells):
            lower = self._bin(subcell.minimum)
            upper = self._bin(subcell.maximum)
            for z in range(lower[2], upper[2] + 1):
                for y in range(lower[1], upper[1] + 1):
                    for x in range(lower[0], upper[0] + 1):
                        self.bins.setdefault((x, y, z), []).append(index)

    def _bin(self, point: tuple[float, float, float]) -> tuple[int, int, int]:
        indices = []
        for axis in range(3):
            span = self.maximum[axis] - self.minimum[axis]
            fraction = (point[axis] - self.minimum[axis]) / span
            indices.append(max(0, min(self.bin_dimensions[axis] - 1, int(fraction * self.bin_dimensions[axis]))))
        return indices[0], indices[1], indices[2]

    def sample(self, point: tuple[float, float, float]) -> _Sample | None:
        if any(point[axis] < self.minimum[axis] or point[axis] > self.maximum[axis] for axis in range(3)):
            return None
        matches: list[tuple[_Subcell, tuple[float, float, float, float]]] = []
        for subcell_index in self.bins.get(self._bin(point), []):
            subcell = self.subcells[subcell_index]
            if any(point[axis] < subcell.minimum[axis] - 1e-12 or point[axis] > subcell.maximum[axis] + 1e-12 for axis in range(3)):
                continue
            vertices = tuple(
                tuple(float(value) for value in self.dataset["points"][point_index])
                for point_index in subcell.point_indices
            )
            weights = _tetra_weights(point, vertices)  # type: ignore[arg-type]
            if weights is not None:
                matches.append((subcell, weights))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0].source_cell, item[0].subcell_id))
        selected, weights = matches[0]
        source_cells = {subcell.source_cell for subcell, _weights in matches}
        return _Sample(
            source_cell=selected.source_cell,
            subcell_id=selected.subcell_id,
            point_indices=selected.point_indices,
            weights=weights,
            ambiguous=len(source_cells) > 1,
        )


def _sample_field(values: list[Any], field: DerivedFieldRequest, sample: _Sample) -> tuple[float, ...]:
    if field.location == "cell":
        value = values[sample.source_cell]
        return (float(value),) if field.kind == "scalar" else tuple(float(component) for component in value)
    tuples = [values[index] for index in sample.point_indices]
    if field.kind == "scalar":
        return (sum(float(value) * weight for value, weight in zip(tuples, sample.weights, strict=True)),)
    return tuple(
        sum(float(value[component]) * weight for value, weight in zip(tuples, sample.weights, strict=True))
        for component in range(3)
    )


def _float_blob(values: list[float]) -> bytes:
    payload = array("f", (float(value) for value in values))
    if sys.byteorder != "little":
        payload.byteswap()
    return payload.tobytes()


def _uint32_blob(values: list[int]) -> bytes:
    payload = array("I", (int(value) for value in values))
    if payload.itemsize != 4:
        return b"".join(struct.pack("<I", int(value)) for value in values)
    if sys.byteorder != "little":
        payload.byteswap()
    return payload.tobytes()


def _blob_descriptor(name: str, payload: bytes, dtype: str, components: int, count: int) -> dict[str, Any]:
    return {
        "schema": DERIVED_BLOB_SCHEMA,
        "name": name,
        "dtype": dtype,
        "components": components,
        "count": count,
        "byteOrder": "little-endian",
        "byteLength": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _add_blob(
    blobs: dict[str, bytes],
    descriptors: list[dict[str, Any]],
    name: str,
    payload: bytes,
    dtype: str,
    components: int,
    count: int,
) -> dict[str, Any]:
    blobs[name] = payload
    descriptor = _blob_descriptor(name, payload, dtype, components, count)
    descriptors.append(descriptor)
    return descriptor


def _gradient(
    scalar_values: list[float],
    validity: list[int],
    dimensions: tuple[int, int, int],
    spacing: tuple[float, float, float],
) -> tuple[list[float], list[int]]:
    nx, ny, nz = dimensions
    gradient = [0.0] * (nx * ny * nz * 3)
    mask = [0] * (nx * ny * nz)

    def index(x: int, y: int, z: int) -> int:
        return (z * ny + y) * nx + x

    for z in range(1, nz - 1):
        for y in range(1, ny - 1):
            for x in range(1, nx - 1):
                center = index(x, y, z)
                neighbors = (
                    index(x - 1, y, z),
                    index(x + 1, y, z),
                    index(x, y - 1, z),
                    index(x, y + 1, z),
                    index(x, y, z - 1),
                    index(x, y, z + 1),
                )
                if not validity[center] or any(not validity[item] for item in neighbors):
                    continue
                gx = (scalar_values[neighbors[1]] - scalar_values[neighbors[0]]) / (2 * spacing[0])
                gy = (scalar_values[neighbors[3]] - scalar_values[neighbors[2]]) / (2 * spacing[1])
                gz = (scalar_values[neighbors[5]] - scalar_values[neighbors[4]]) / (2 * spacing[2])
                if not all(math.isfinite(value) for value in (gx, gy, gz)):
                    continue
                mask[center] = 1
                gradient[center * 3 : center * 3 + 3] = [gx, gy, gz]
    return gradient, mask


def _component_manifest(case: SolverCase | None, sources: list[_SourceArtifact]) -> dict[str, Any]:
    if case is None or case.resultComponentMap is None:
        return {
            "status": "probe-only",
            "reason": "Imported artifacts have no generated-case component authority.",
            "map": None,
        }
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        source_matches = [
            binding
            for binding in case.resultComponentMap.artifactBindings
            if PurePosixPath(source.path).match(binding.artifactName)
        ]
        if len(source_matches) != 1:
            return {
                "status": "probe-only",
                "reason": (
                    f"Source artifact {source.path!r} does not have exactly one explicit component-map binding."
                ),
                "map": None,
            }
        binding = source_matches[0]
        if binding.scope == "cell-ranges":
            source_cell_count = len(source.dataset["cells"])
            if binding.sourceCellCount != source_cell_count:
                return {
                    "status": "probe-only",
                    "reason": (
                        f"Source artifact {source.path!r} has {source_cell_count} cells, but its component map "
                        f"declares {binding.sourceCellCount}."
                    ),
                    "map": None,
                }
            if any(
                cell_range.cellStart + cell_range.cellCount > source_cell_count
                for cell_range in binding.cellRanges
            ):
                return {
                    "status": "probe-only",
                    "reason": f"Source artifact {source.path!r} has an out-of-range component-map interval.",
                    "map": None,
                }
        dumped = binding.model_dump(mode="json")
        key = _canonical_json(dumped)
        if key not in seen:
            seen.add(key)
            matched.append(dumped)
    narrowed_map = case.resultComponentMap.model_dump(mode="json")
    narrowed_map["artifactBindings"] = matched
    return {
        "status": "source-cell-map",
        "reason": (
            "Selections resolve only through source-cell IDs and the artifact-compatible bindings in this "
            "explicit generated-case map."
        ),
        "map": narrowed_map,
    }


def _base_manifest(
    request: DerivedVisualizationRequest,
    request_sha256: str,
    sources: list[_SourceArtifact],
    unit_authority: str,
    case: SolverCase | None,
) -> dict[str, Any]:
    return {
        "schema": DERIVED_MANIFEST_SCHEMA,
        "requestSchema": request.schema_,
        "requestSha256": request_sha256,
        "operation": request.operation,
        "visualizationOnly": True,
        "scientificStateEffect": "none",
        "releaseStateEffect": "none",
        "unitAuthority": unit_authority,
        "sourceArtifacts": [
            {
                "path": source.path,
                "time": source.time,
                "size": source.size,
                "sha256": source.sha256,
                "geometryDigest": source.geometry_digest,
                "cellOrderDigest": source.cell_order_digest,
            }
            for source in sources
        ],
        "componentResolution": _component_manifest(case, sources),
        "limits": {
            "defaultGridDimension": DEFAULT_GRID_DIMENSION,
            "maxGridDimension": MAX_GRID_DIMENSION,
            "artifactSetBytes": MAX_ARTIFACT_SET_BYTES,
            "browserResidencyBytes": MAX_BROWSER_RESIDENCY_BYTES,
            "derivedCacheBytesPerJob": MAX_DERIVED_CACHE_BYTES_PER_SCOPE,
            "maxSeeds": MAX_SEEDS,
            "maxPathlineVertices": MAX_PATHLINE_VERTICES,
            "maxIsoTriangles": MAX_ISO_TRIANGLES,
            "overflowBehavior": "reject",
        },
        "interpolation": {
            "schema": "flowlab.tetrahedral_subcell_interpolation.v1",
            "pointFields": "barycentric",
            "cellFields": "cell-constant",
            "subcellVerticesByVtkCellType": {
                str(cell_type): [list(vertices) for vertices in decomposition]
                for cell_type, decomposition in sorted(SUBCELL_VERTICES.items())
            },
        },
    }


def _request_hash(request: DerivedVisualizationRequest, sources: list[_SourceArtifact], scope: str) -> str:
    payload = {
        "scope": scope,
        "request": request.model_dump(mode="json", by_alias=True),
        "artifacts": [{"path": source.path, "sha256": source.sha256, "time": source.time} for source in sources],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_frames(
    request: DerivedVisualizationRequest,
    sources: list[_SourceArtifact],
    case: SolverCase | None,
) -> str:
    unit_authority = _validate_units(case, request.fields)
    first = sources[0]
    for source in sources:
        for field in request.fields:
            _validate_field(source.dataset, field)
        if request.operation == "pathlines":
            if source.geometry_digest != first.geometry_digest or source.cell_order_digest != first.cell_order_digest:
                raise ValueError("Transient pathlines require identical geometry and cell-order digests at every frame.")
    if request.operation == "pathlines":
        times = [source.time for source in sources]
        if any(time is None or not math.isfinite(float(time)) for time in times):
            raise ValueError("Transient pathlines require an explicit finite timestamp for every frame.")
        numeric_times = [float(time) for time in times if time is not None]
        if any(right <= left for left, right in zip(numeric_times, numeric_times[1:])):
            raise ValueError("Transient pathline timestamps must be strictly increasing in request order.")
    return unit_authority


def _volume_product(
    request: DerivedVisualizationRequest,
    request_sha256: str,
    sources: list[_SourceArtifact],
    unit_authority: str,
    case: SolverCase | None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(sources) != 1:
        raise ValueError("A volume request accepts exactly one full source artifact.")
    source = sources[0]
    grid = request.grid or DerivedGridRequest()
    dimensions = grid.dimensions
    voxel_count = dimensions[0] * dimensions[1] * dimensions[2]
    component_count = sum(1 if field.kind == "scalar" else 3 for field in request.fields)
    gradient_count = len(grid.gradients)
    estimated = voxel_count * (1 + 1 + 4 + 16 + component_count * 4 + gradient_count * (12 + 1))
    if estimated > MAX_BROWSER_RESIDENCY_BYTES:
        raise ValueError(
            f"Derived browser residency would be {estimated} bytes, above {MAX_BROWSER_RESIDENCY_BYTES}; request fewer fields or a smaller grid."
        )
    index = SpatialIndex(source.dataset)
    spacing = tuple((index.maximum[axis] - index.minimum[axis]) / dimensions[axis] for axis in range(3))
    fields = [(field, _validate_field(source.dataset, field)) for field in request.fields]
    validity: list[int] = []
    ambiguity: list[int] = []
    source_cells: list[int] = []
    subcells: list[int] = []
    weights: list[float] = []
    field_values: list[list[float]] = [[] for _field, _values in fields]
    nx, ny, nz = dimensions
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                point = (
                    index.minimum[0] + (x + 0.5) * spacing[0],
                    index.minimum[1] + (y + 0.5) * spacing[1],
                    index.minimum[2] + (z + 0.5) * spacing[2],
                )
                sample = index.sample(point)
                if sample is None:
                    validity.append(0)
                    ambiguity.append(0)
                    source_cells.append(INVALID_SOURCE_CELL)
                    subcells.append(255)
                    weights.extend((0.0, 0.0, 0.0, 0.0))
                    for field_index, (field, _values) in enumerate(fields):
                        field_values[field_index].extend([0.0] * (1 if field.kind == "scalar" else 3))
                    continue
                validity.append(1)
                ambiguity.append(1 if sample.ambiguous else 0)
                source_cells.append(sample.source_cell)
                subcells.append(sample.subcell_id)
                weights.extend(sample.weights)
                for field_index, (field, values) in enumerate(fields):
                    field_values[field_index].extend(_sample_field(values, field, sample))

    blobs: dict[str, bytes] = {}
    descriptors: list[dict[str, Any]] = []
    validity_blob = _add_blob(blobs, descriptors, "validity.bin", bytes(validity), "uint8", 1, voxel_count)
    ambiguity_blob = _add_blob(blobs, descriptors, "ambiguity.bin", bytes(ambiguity), "uint8", 1, voxel_count)
    source_blob = _add_blob(blobs, descriptors, "source-cell-ids.bin", _uint32_blob(source_cells), "uint32", 1, voxel_count)
    subcell_blob = _add_blob(blobs, descriptors, "subcell-ids.bin", bytes(subcells), "uint8", 1, voxel_count)
    weights_blob = _add_blob(blobs, descriptors, "spatial-weights.bin", _float_blob(weights), "float32", 4, voxel_count)
    field_manifests: list[dict[str, Any]] = []
    scalar_lookup: dict[str, tuple[list[float], DerivedFieldRequest]] = {}
    for field_index, ((field, _source_values), values) in enumerate(zip(fields, field_values, strict=True)):
        components = 1 if field.kind == "scalar" else 3
        descriptor = _add_blob(
            blobs,
            descriptors,
            f"values-{field_index:03d}.bin",
            _float_blob(values),
            "float32",
            components,
            voxel_count,
        )
        field_manifests.append(
            {
                **field.model_dump(mode="json"),
                "values": descriptor,
                "validity": validity_blob,
            }
        )
        if field.kind == "scalar":
            scalar_lookup[field.name.lower()] = (values, field)
        elif field.name.lower() in {"u", "velocity"}:
            speed = [
                math.sqrt(values[offset] ** 2 + values[offset + 1] ** 2 + values[offset + 2] ** 2)
                for offset in range(0, len(values), 3)
            ]
            scalar_lookup["speed"] = (
                speed,
                DerivedFieldRequest(name="speed", location=field.location, kind="scalar", unit=field.unit),
            )

    gradients: list[dict[str, Any]] = []
    for gradient_index, gradient_name in enumerate(grid.gradients):
        candidates = (
            ["p", "p_rgh", "pressure", "static_pressure", "total_pressure"]
            if gradient_name == "pressure"
            else ["speed"]
        )
        source_scalar = next((scalar_lookup[name] for name in candidates if name in scalar_lookup), None)
        if source_scalar is None:
            raise ValueError(f"{gradient_name} gradient requested without a compatible source field.")
        scalar_values, scalar_field = source_scalar
        gradient_values, gradient_validity = _gradient(scalar_values, validity, dimensions, spacing)
        values_descriptor = _add_blob(
            blobs,
            descriptors,
            f"gradient-{gradient_index:03d}.bin",
            _float_blob(gradient_values),
            "float32",
            3,
            voxel_count,
        )
        mask_descriptor = _add_blob(
            blobs,
            descriptors,
            f"gradient-validity-{gradient_index:03d}.bin",
            bytes(gradient_validity),
            "uint8",
            1,
            voxel_count,
        )
        gradients.append(
            {
                "name": f"grad({scalar_field.name})",
                "source": scalar_field.name,
                "unit": f"{scalar_field.unit}/m",
                "values": values_descriptor,
                "validity": mask_descriptor,
            }
        )

    residency = sum(len(blob) for blob in blobs.values())
    if residency > MAX_BROWSER_RESIDENCY_BYTES:
        raise ValueError(f"Derived browser residency is {residency} bytes, above {MAX_BROWSER_RESIDENCY_BYTES}.")
    manifest = {
        **_base_manifest(request, request_sha256, sources, unit_authority, case),
        "grid": {
            "dimensions": list(dimensions),
            "voxelCount": voxel_count,
            "bounds": {"min": list(index.minimum), "max": list(index.maximum)},
            "spacing": list(spacing),
            "sampleLocation": "voxel-center",
        },
        "provenance": {
            "validity": validity_blob,
            "ambiguity": ambiguity_blob,
            "sourceCellIds": source_blob,
            "subcellIds": subcell_blob,
            "spatialWeights": weights_blob,
            "invalidSourceCellId": INVALID_SOURCE_CELL,
            "ambiguousSelections": "probe-only",
        },
        "fields": field_manifests,
        "gradients": gradients,
        "blobs": descriptors,
        "browserResidencyBytes": residency,
    }
    return manifest, blobs


def _time_velocity(
    sources: list[_SourceArtifact],
    indexes: list[SpatialIndex],
    field: DerivedFieldRequest,
    values: list[list[Any]],
    point: tuple[float, float, float],
    time_value: float,
) -> tuple[tuple[float, float, float], _Sample] | None:
    times = [float(source.time) for source in sources if source.time is not None]
    if time_value < times[0] - 1e-12 or time_value > times[-1] + 1e-12:
        return None
    upper = next((index for index, time in enumerate(times) if time >= time_value), len(times) - 1)
    lower = max(0, upper - 1)
    sample = indexes[lower].sample(point)
    upper_sample = indexes[upper].sample(point)
    if sample is None or upper_sample is None:
        return None
    left = _sample_field(values[lower], field, sample)
    right = _sample_field(values[upper], field, upper_sample)
    if upper == lower:
        return (left[0], left[1], left[2]), sample
    fraction = (time_value - times[lower]) / (times[upper] - times[lower])
    velocity = tuple(left[axis] * (1 - fraction) + right[axis] * fraction for axis in range(3))
    combined = _Sample(
        sample.source_cell,
        sample.subcell_id,
        sample.point_indices,
        sample.weights,
        sample.ambiguous or upper_sample.ambiguous or sample.source_cell != upper_sample.source_cell,
    )
    return (velocity[0], velocity[1], velocity[2]), combined


def _pathline_product(
    request: DerivedVisualizationRequest,
    request_sha256: str,
    sources: list[_SourceArtifact],
    unit_authority: str,
    case: SolverCase | None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    controls = request.pathlines
    if controls is None:
        raise ValueError("Pathline controls are missing.")
    velocity_field = next(field for field in request.fields if field.kind == "vector")
    indexes = [SpatialIndex(source.dataset) for source in sources]
    values = [_validate_field(source.dataset, velocity_field) for source in sources]
    start_time = float(sources[0].time)  # validated before dispatch
    end_time = float(sources[-1].time)
    positions: list[float] = []
    times: list[float] = []
    source_cells: list[int] = []
    subcells: list[int] = []
    ambiguity: list[int] = []
    weights: list[float] = []
    offsets = [0]
    terminations: list[str] = []

    def velocity(point: tuple[float, float, float], time_value: float) -> tuple[tuple[float, float, float], _Sample] | None:
        return _time_velocity(sources, indexes, velocity_field, values, point, time_value)

    for seed in controls.seeds:
        point = tuple(float(value) for value in seed)
        initial = velocity(point, start_time)
        if initial is None:
            raise ValueError(f"Pathline seed is outside the verified volume at t={start_time:g}: {list(seed)}")
        current_time = start_time
        termination = "end-time"
        while True:
            sampled = velocity(point, current_time)
            if sampled is None:
                termination = "left-domain"
                break
            _current_velocity, provenance = sampled
            positions.extend(point)
            times.append(current_time)
            source_cells.append(provenance.source_cell)
            subcells.append(provenance.subcell_id)
            ambiguity.append(1 if provenance.ambiguous else 0)
            weights.extend(provenance.weights)
            if len(times) > controls.maxVertices or len(times) > MAX_PATHLINE_VERTICES:
                raise ValueError(
                    f"Pathline vertex budget exceeded; requested result is above {min(controls.maxVertices, MAX_PATHLINE_VERTICES)} vertices."
                )
            if current_time >= end_time - 1e-12:
                break
            step = min(controls.stepSeconds, end_time - current_time)
            k1 = velocity(point, current_time)
            if k1 is None:
                termination = "left-domain"
                break
            p2 = tuple(point[axis] + 0.5 * step * k1[0][axis] for axis in range(3))
            k2 = velocity(p2, current_time + 0.5 * step)
            if k2 is None:
                termination = "left-domain"
                break
            p3 = tuple(point[axis] + 0.5 * step * k2[0][axis] for axis in range(3))
            k3 = velocity(p3, current_time + 0.5 * step)
            if k3 is None:
                termination = "left-domain"
                break
            p4 = tuple(point[axis] + step * k3[0][axis] for axis in range(3))
            k4 = velocity(p4, current_time + step)
            if k4 is None:
                termination = "left-domain"
                break
            point = tuple(
                point[axis]
                + step
                * (k1[0][axis] + 2 * k2[0][axis] + 2 * k3[0][axis] + k4[0][axis])
                / 6
                for axis in range(3)
            )
            current_time += step
        offsets.append(len(times))
        terminations.append(termination)

    vertex_count = len(times)
    blobs: dict[str, bytes] = {}
    descriptors: list[dict[str, Any]] = []
    positions_blob = _add_blob(blobs, descriptors, "pathline-positions.bin", _float_blob(positions), "float32", 3, vertex_count)
    times_blob = _add_blob(blobs, descriptors, "pathline-times.bin", _float_blob(times), "float32", 1, vertex_count)
    offsets_blob = _add_blob(blobs, descriptors, "pathline-offsets.bin", _uint32_blob(offsets), "uint32", 1, len(offsets))
    source_blob = _add_blob(blobs, descriptors, "source-cell-ids.bin", _uint32_blob(source_cells), "uint32", 1, vertex_count)
    subcell_blob = _add_blob(blobs, descriptors, "subcell-ids.bin", bytes(subcells), "uint8", 1, vertex_count)
    ambiguity_blob = _add_blob(blobs, descriptors, "ambiguity.bin", bytes(ambiguity), "uint8", 1, vertex_count)
    weights_blob = _add_blob(blobs, descriptors, "spatial-weights.bin", _float_blob(weights), "float32", 4, vertex_count)
    residency = sum(len(blob) for blob in blobs.values())
    if residency > MAX_BROWSER_RESIDENCY_BYTES:
        raise ValueError(f"Pathline browser residency is {residency} bytes, above {MAX_BROWSER_RESIDENCY_BYTES}.")
    manifest = {
        **_base_manifest(request, request_sha256, sources, unit_authority, case),
        "pathlines": {
            "integration": "deterministic-rk4-time-linear-v1",
            "seedCount": len(controls.seeds),
            "vertexCount": vertex_count,
            "stepSeconds": controls.stepSeconds,
            "startTime": start_time,
            "endTime": end_time,
            "terminations": terminations,
            "positions": positions_blob,
            "times": times_blob,
            "offsets": offsets_blob,
        },
        "provenance": {
            "sourceCellIds": source_blob,
            "subcellIds": subcell_blob,
            "ambiguity": ambiguity_blob,
            "spatialWeights": weights_blob,
            "ambiguousSelections": "probe-only",
        },
        "blobs": descriptors,
        "browserResidencyBytes": residency,
    }
    return manifest, blobs


def derive_visualization(
    request: DerivedVisualizationRequest,
    *,
    scope: str,
    case: SolverCase | None = None,
    case_dir: Path | None = None,
    inline_artifacts: list[DerivedImportArtifact] | None = None,
    require_component_authority: bool = False,
) -> dict[str, Any]:
    if case_dir is not None and inline_artifacts is not None:
        raise ValueError("Derived visualization must use either job artifacts or inline imported artifacts, never both.")
    if case_dir is None and inline_artifacts is None:
        raise ValueError("Derived visualization has no full source artifacts.")
    raw = _load_job_artifacts(case_dir, request.artifacts) if case_dir is not None else _load_inline_artifacts(inline_artifacts or [])
    sources = _prepare_sources(raw)
    component_resolution = _component_manifest(case, sources)
    if require_component_authority and component_resolution["status"] != "source-cell-map":
        raise ValueError(
            "Derived visualization requires explicit resultComponentMap authority: "
            f"{component_resolution['reason']}"
        )
    unit_authority = _validate_frames(request, sources, case)
    request_sha256 = _request_hash(request, sources, scope)
    cached = DERIVED_CACHE.get(scope, request_sha256)
    if cached:
        return cached[0]
    if request.operation == "volume":
        manifest, blobs = _volume_product(request, request_sha256, sources, unit_authority, case)
    else:
        manifest, blobs = _pathline_product(request, request_sha256, sources, unit_authority, case)
    manifest["manifestSha256"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    DERIVED_CACHE.put(scope, request_sha256, manifest, blobs)
    return manifest
