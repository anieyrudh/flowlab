from __future__ import annotations

import fnmatch
import itertools
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .results import parse_vtk_result
from .schemas import SolverCase, StreamlineDerivationRequest

DEFAULT_SEEDS = 64
MAX_SEEDS = 256
MAX_VERTICES_PER_LINE = 1_024
MAX_TOTAL_VERTICES = 65_536
MAX_SPRITES = 256
EPSILON = 1.0e-9
ZERO_SPEED = 1.0e-12
CELL_POINT_COUNTS = {5: 3, 9: 4, 10: 4, 12: 8, 13: 6, 14: 5}
TRIANGLES = {5: ((0, 1, 2),), 9: ((0, 1, 2), (0, 2, 3))}
TETRAHEDRA = {
    10: ((0, 1, 2, 3),),
    12: (
        (0, 1, 2, 6),
        (0, 2, 3, 6),
        (0, 3, 7, 6),
        (0, 7, 4, 6),
        (0, 4, 5, 6),
        (0, 5, 1, 6),
    ),
    13: ((0, 1, 2, 3), (1, 2, 4, 3), (2, 4, 5, 3)),
    14: ((0, 1, 2, 4), (0, 2, 3, 4)),
}
COLOR_FIELDS = {
    "pressure": ("p", "p_rgh", "pressure", "static_pressure"),
    "temperature": ("t", "temperature", "temp"),
    "phase": ("alpha", "alpha.water", "phase", "phase_fraction"),
    "vorticity": ("vorticity", "omega"),
}
BOUNDARY_MANIFEST_PATH = "mesh/flowlab_boundary_faces.json"


def _add(left: list[float], right: list[float]) -> list[float]:
    return [left[index] + right[index] for index in range(3)]


def _subtract(left: list[float], right: list[float]) -> list[float]:
    return [left[index] - right[index] for index in range(3)]


def _scale(value: list[float], factor: float) -> list[float]:
    return [component * factor for component in value]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _magnitude(value: list[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def _within_weights(weights: list[float]) -> bool:
    return all(-EPSILON <= weight <= 1.0 + EPSILON for weight in weights) and abs(sum(weights) - 1.0) <= 1.0e-7


def _triangle_weights(point: list[float], a: list[float], b: list[float], c: list[float]) -> list[float] | None:
    v0 = _subtract(b, a)
    v1 = _subtract(c, a)
    v2 = _subtract(point, a)
    normal = _cross(v0, v1)
    normal_magnitude = _magnitude(normal)
    if normal_magnitude <= EPSILON or abs(_dot(v2, normal)) / normal_magnitude > EPSILON * 10.0:
        return None
    d00 = _dot(v0, v0)
    d01 = _dot(v0, v1)
    d11 = _dot(v1, v1)
    d20 = _dot(v2, v0)
    d21 = _dot(v2, v1)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= EPSILON:
        return None
    second = (d11 * d20 - d01 * d21) / denominator
    third = (d00 * d21 - d01 * d20) / denominator
    weights = [1.0 - second - third, second, third]
    return weights if _within_weights(weights) else None


def _determinant(a: list[float], b: list[float], c: list[float]) -> float:
    return _dot(a, _cross(b, c))


def _tetra_weights(
    point: list[float],
    a: list[float],
    b: list[float],
    c: list[float],
    d: list[float],
) -> list[float] | None:
    ab = _subtract(b, a)
    ac = _subtract(c, a)
    ad = _subtract(d, a)
    ap = _subtract(point, a)
    denominator = _determinant(ab, ac, ad)
    if abs(denominator) <= EPSILON:
        return None
    second = _determinant(ap, ac, ad) / denominator
    third = _determinant(ab, ap, ad) / denominator
    fourth = _determinant(ab, ac, ap) / denominator
    weights = [1.0 - second - third - fourth, second, third, fourth]
    return weights if _within_weights(weights) else None


def _validate_full_dataset(dataset: dict[str, Any]) -> None:
    points = dataset.get("points")
    cells = dataset.get("cells")
    cell_types = dataset.get("cellTypes")
    if not isinstance(points, list) or not isinstance(cells, list) or not isinstance(cell_types, list) or not points or not cells:
        raise ValueError("Complete supported topology required.")
    if len(cells) != len(cell_types):
        raise ValueError("Complete supported topology required.")
    for cell, cell_type in zip(cells, cell_types, strict=True):
        expected = CELL_POINT_COUNTS.get(cell_type)
        if expected is None or not isinstance(cell, list) or len(cell) != expected:
            raise ValueError("Complete supported topology required.")
        if any(not isinstance(point_id, int) or point_id < 0 or point_id >= len(points) for point_id in cell):
            raise ValueError("Complete supported topology required.")


class _CellLocator:
    """A deterministic broad-phase index; exact barycentric tests establish membership."""

    def __init__(self, dataset: dict[str, Any]) -> None:
        points = dataset["points"]
        self.cell_bounds: list[tuple[list[float], list[float]]] = []
        self.minimum = [min(point[axis] for point in points) for axis in range(3)]
        self.maximum = [max(point[axis] for point in points) for axis in range(3)]
        spans = [self.maximum[axis] - self.minimum[axis] for axis in range(3)]
        active_dimensions = max(1, len([span for span in spans if span > EPSILON]))
        division = min(64, max(1, math.ceil(len(dataset["cells"]) ** (1.0 / active_dimensions))))
        self.divisions = [division if span > EPSILON else 1 for span in spans]
        self.buckets: dict[tuple[int, int, int], list[int]] = {}

        for rendered_cell_id, cell in enumerate(dataset["cells"]):
            cell_points = [points[point_id] for point_id in cell]
            lower = [min(candidate[axis] for candidate in cell_points) for axis in range(3)]
            upper = [max(candidate[axis] for candidate in cell_points) for axis in range(3)]
            self.cell_bounds.append((lower, upper))
            ranges = [
                range(
                    self._axis_bucket(lower[axis] - EPSILON, axis),
                    self._axis_bucket(upper[axis] + EPSILON, axis) + 1,
                )
                for axis in range(3)
            ]
            for key in itertools.product(*ranges):
                self.buckets.setdefault(key, []).append(rendered_cell_id)

    def _axis_bucket(self, value: float, axis: int) -> int:
        span = self.maximum[axis] - self.minimum[axis]
        if span <= EPSILON or self.divisions[axis] == 1:
            return 0
        normalized = (value - self.minimum[axis]) / span
        return min(self.divisions[axis] - 1, max(0, math.floor(normalized * self.divisions[axis])))

    def candidates(self, point: list[float]) -> list[int]:
        if any(
            point[axis] < self.minimum[axis] - EPSILON or point[axis] > self.maximum[axis] + EPSILON
            for axis in range(3)
        ):
            return []
        key = tuple(self._axis_bucket(point[axis], axis) for axis in range(3))
        return self.buckets.get(key, [])


def _locate(dataset: dict[str, Any], locator: _CellLocator, point: list[float]) -> dict[str, Any] | None:
    points = dataset["points"]
    for rendered_cell_id in locator.candidates(point):
        cell = dataset["cells"][rendered_cell_id]
        cell_type = dataset["cellTypes"][rendered_cell_id]
        lower, upper = locator.cell_bounds[rendered_cell_id]
        if any(
            point[axis] < lower[axis] - EPSILON or point[axis] > upper[axis] + EPSILON
            for axis in range(3)
        ):
            continue
        for local_ids in TRIANGLES.get(cell_type, ()):
            point_ids = [cell[local_id] for local_id in local_ids]
            weights = _triangle_weights(point, *(points[point_id] for point_id in point_ids))
            if weights is not None:
                return {
                    "renderedCellId": rendered_cell_id,
                    "sourceCellId": rendered_cell_id,
                    "pointIds": point_ids,
                    "weights": weights,
                    "pointMethod": "point-barycentric-triangle",
                }
        for local_ids in TETRAHEDRA.get(cell_type, ()):
            point_ids = [cell[local_id] for local_id in local_ids]
            weights = _tetra_weights(point, *(points[point_id] for point_id in point_ids))
            if weights is not None:
                return {
                    "renderedCellId": rendered_cell_id,
                    "sourceCellId": rendered_cell_id,
                    "pointIds": point_ids,
                    "weights": weights,
                    "pointMethod": "point-barycentric-tetra-decomposition",
                }
    return None


def _find_field(fields: dict[str, Any], aliases: tuple[str, ...]) -> tuple[str, list[Any]] | None:
    normalized = {str(key).lower(): str(key) for key in fields}
    for alias in aliases:
        key = normalized.get(alias.lower())
        values = fields.get(key) if key is not None else None
        if key is not None and isinstance(values, list):
            return key, values
    return None


def _velocity_field(dataset: dict[str, Any]) -> dict[str, Any] | None:
    point_data = dataset.get("pointData", {}).get("vectors", {})
    cell_data = dataset.get("cellData", {}).get("vectors", {})
    point = _find_field(point_data, ("u", "velocity", "vel"))
    if point is not None:
        return {"name": point[0], "location": "point", "values": point[1]}
    cell = _find_field(cell_data, ("u", "velocity", "vel"))
    return None if cell is None else {"name": cell[0], "location": "cell", "values": cell[1]}


def _color_fields(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for field_name, aliases in COLOR_FIELDS.items():
        for location in ("point", "cell"):
            data = dataset.get(f"{location}Data", {})
            scalar = _find_field(data.get("scalars", {}), aliases)
            if scalar is not None:
                resolved[field_name] = {
                    "location": location,
                    "kind": "scalar",
                    "values": scalar[1],
                }
                break
            vector = _find_field(data.get("vectors", {}), aliases)
            if vector is not None:
                resolved[field_name] = {
                    "location": location,
                    "kind": "vector",
                    "values": vector[1],
                }
                break
    return resolved


def _interpolate_scalar(values: list[float], location: str, located: dict[str, Any]) -> float:
    if location == "cell":
        return float(values[located["renderedCellId"]])
    return sum(float(values[point_id]) * weight for point_id, weight in zip(located["pointIds"], located["weights"], strict=True))


def _interpolate_vector(values: list[list[float]], location: str, located: dict[str, Any]) -> list[float]:
    if location == "cell":
        return [float(component) for component in values[located["renderedCellId"]]]
    return [
        sum(float(values[point_id][axis]) * weight for point_id, weight in zip(located["pointIds"], located["weights"], strict=True))
        for axis in range(3)
    ]


def _explicit_field(field: dict[str, Any], located: dict[str, Any]) -> float:
    if field["kind"] == "scalar":
        return _interpolate_scalar(field["values"], field["location"], located)
    return _magnitude(_interpolate_vector(field["values"], field["location"], located))


def _velocity_sample(
    dataset: dict[str, Any],
    locator: _CellLocator,
    velocity: dict[str, Any],
    point: list[float],
) -> tuple[dict[str, Any], list[float]] | None:
    located = _locate(dataset, locator, point)
    if located is None:
        return None
    return located, _interpolate_vector(velocity["values"], velocity["location"], located)


def _sample(
    dataset: dict[str, Any],
    locator: _CellLocator,
    velocity: dict[str, Any],
    color_fields: dict[str, dict[str, Any]],
    point: list[float],
) -> dict[str, Any] | None:
    sampled_velocity = _velocity_sample(dataset, locator, velocity, point)
    if sampled_velocity is None:
        return None
    located, vector = sampled_velocity
    fields = {"velocity": _magnitude(vector)}
    for field_name, field in color_fields.items():
        value = _explicit_field(field, located)
        if math.isfinite(value):
            fields[field_name] = value
    return {
        "velocity": vector,
        "fields": fields,
        "provenance": {
            "renderedCellId": located["renderedCellId"],
            "sourceCellId": located["sourceCellId"],
            "pointIds": located["pointIds"] if velocity["location"] == "point" else [],
            "weights": located["weights"] if velocity["location"] == "point" else [1.0],
            "method": located["pointMethod"] if velocity["location"] == "point" else "cell-piecewise-constant",
        },
    }


def _direction(velocity: list[float]) -> list[float] | None:
    speed = _magnitude(velocity)
    return None if speed <= ZERO_SPEED else _scale(velocity, 1.0 / speed)


def _rk4(
    dataset: dict[str, Any],
    locator: _CellLocator,
    velocity: dict[str, Any],
    point: list[float],
    step: float,
) -> list[float] | None:
    first = _velocity_sample(dataset, locator, velocity, point)
    k1 = None if first is None else _direction(first[1])
    if k1 is None:
        return None
    second = _velocity_sample(dataset, locator, velocity, _add(point, _scale(k1, step / 2.0)))
    k2 = None if second is None else _direction(second[1])
    if k2 is None:
        return None
    third = _velocity_sample(dataset, locator, velocity, _add(point, _scale(k2, step / 2.0)))
    k3 = None if third is None else _direction(third[1])
    if k3 is None:
        return None
    fourth = _velocity_sample(dataset, locator, velocity, _add(point, _scale(k3, step)))
    k4 = None if fourth is None else _direction(fourth[1])
    if k4 is None:
        return None
    combined = [k1[axis] + 2.0 * k2[axis] + 2.0 * k3[axis] + k4[axis] for axis in range(3)]
    return _add(point, _scale(combined, step / 6.0))


def _default_step(dataset: dict[str, Any]) -> float:
    spans = [
        max(point[axis] for point in dataset["points"]) - min(point[axis] for point in dataset["points"])
        for axis in range(3)
    ]
    return max(max(spans), EPSILON) / 200.0


def _spatial_dimension(dataset: dict[str, Any]) -> int:
    spans = [
        max(point[axis] for point in dataset["points"]) - min(point[axis] for point in dataset["points"])
        for axis in range(3)
    ]
    return 2 if len([span for span in spans if span > EPSILON]) <= 2 else 3


def _generate_plane_seeds(plane: Any) -> list[list[float]]:
    count_u = int(plane.countU)
    count_v = int(plane.countV)
    if count_u * count_v > MAX_SEEDS:
        raise ValueError(f"Seed count exceeds {MAX_SEEDS}.")
    seeds: list[list[float]] = []
    for v_index in range(count_v):
        for u_index in range(count_u):
            fraction_u = 0.5 if count_u == 1 else u_index / (count_u - 1)
            fraction_v = 0.5 if count_v == 1 else v_index / (count_v - 1)
            seeds.append(
                _add(
                    list(plane.origin),
                    _add(_scale(list(plane.axisU), fraction_u), _scale(list(plane.axisV), fraction_v)),
                )
            )
    return seeds


def _dataset_plane_seeds(dataset: dict[str, Any], normal_axis: int, normalized_position: float, seed_count: int) -> list[list[float]]:
    minimum = [min(point[axis] for point in dataset["points"]) for axis in range(3)]
    maximum = [max(point[axis] for point in dataset["points"]) for axis in range(3)]
    spans = [maximum[axis] - minimum[axis] for axis in range(3)]
    tangents = [axis for axis in range(3) if axis != normal_axis and spans[axis] > EPSILON]
    first_axis = tangents[0] if tangents else (normal_axis + 1) % 3
    second_axis = tangents[1] if len(tangents) > 1 else None
    bounded_count = min(max(1, int(seed_count)), MAX_SEEDS)
    count_v = 1 if second_axis is None else max(1, math.floor(math.sqrt(bounded_count)))
    count_u = max(1, math.floor(bounded_count / count_v))
    origin = list(minimum)
    origin[normal_axis] = minimum[normal_axis] + spans[normal_axis] * max(1.0e-6, min(1.0 - 1.0e-6, normalized_position))
    axis_u = [0.0, 0.0, 0.0]
    axis_v = [0.0, 0.0, 0.0]
    inset = 0.02
    origin[first_axis] = minimum[first_axis] + spans[first_axis] * inset
    axis_u[first_axis] = spans[first_axis] * (1.0 - inset * 2.0)
    if second_axis is not None:
        origin[second_axis] = minimum[second_axis] + spans[second_axis] * inset
        axis_v[second_axis] = spans[second_axis] * (1.0 - inset * 2.0)
    plane = SimpleNamespace(
        origin=origin,
        axisU=axis_u,
        axisV=axis_v,
        countU=count_u,
        countV=count_v,
    )
    return _generate_plane_seeds(plane)


def _inlet_manifest_seeds(
    case_dir: Path,
    requested_count: int,
    case: SolverCase | None,
) -> list[list[float]]:
    path = case_dir / BOUNDARY_MANIFEST_PATH
    expected_manifest = None if case is None else case.files.get(BOUNDARY_MANIFEST_PATH)
    if not path.is_file() or expected_manifest is None:
        raise ValueError("Automatic inlet seeds require a generator-authored boundary-face manifest.")
    import json

    manifest_text = path.read_text(encoding="utf-8")
    if manifest_text != expected_manifest:
        raise ValueError("Automatic inlet seeds require a generator-authored boundary-face manifest.")
    manifest = json.loads(manifest_text)
    if manifest.get("schema") != "flowlab.boundary_faces.v1" or manifest.get("authorship") != "generator":
        raise ValueError("Automatic inlet seeds require a generator-authored boundary-face manifest.")
    faces = [
        face
        for patch in manifest.get("patches", [])
        if isinstance(patch, dict) and patch.get("role") == "inlet"
        for face in patch.get("faces", [])
        if isinstance(face, dict)
    ]
    centers = [face.get("center") for face in faces]
    centers = [list(center) for center in centers if isinstance(center, list) and len(center) == 3]
    if not centers:
        raise ValueError("Generator-authored inlet boundary-face manifest contains no seedable faces.")
    limit = min(max(1, requested_count), MAX_SEEDS, len(centers))
    if limit == len(centers):
        return centers
    if limit == 1:
        return [centers[0]]
    indices = sorted({round(index * (len(centers) - 1) / (limit - 1)) for index in range(limit)})
    return [centers[index] for index in indices]


def _verified_source_identity(case: SolverCase | None, artifact_path: str, cell_count: int) -> str:
    if case is None or case.solver == "su2" or case.resultComponentMap is None:
        return "artifact-local-unlinked"
    for binding in case.resultComponentMap.artifactBindings:
        pattern = binding.artifactName
        if not fnmatch.fnmatchcase(artifact_path, pattern):
            continue
        if binding.scope == "cell-ranges" and binding.sourceCellCount == cell_count:
            return "verified-case-cell-order"
    return "artifact-local-unlinked"


def integrate_dataset(
    dataset: dict[str, Any],
    seeds: list[list[float]],
    *,
    source_name: str,
    source_identity: str,
    step_size: float | None = None,
    max_vertices_per_line: int = MAX_VERTICES_PER_LINE,
    max_total_vertices: int = MAX_TOTAL_VERTICES,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    _validate_full_dataset(dataset)
    if len(seeds) > MAX_SEEDS:
        raise ValueError(f"Seed count exceeds {MAX_SEEDS}.")
    velocity = _velocity_field(dataset)
    if velocity is None:
        raise ValueError("A loaded U/velocity vector field is required.")
    locator = _CellLocator(dataset)
    color_fields = _color_fields(dataset)
    step = _default_step(dataset) if step_size is None else float(step_size)
    if not math.isfinite(step) or step <= 0:
        raise ValueError("Streamline step size must be positive.")
    per_line_limit = min(max(1, int(max_vertices_per_line)), MAX_VERTICES_PER_LINE)
    total_limit = min(max(1, int(max_total_vertices)), MAX_TOTAL_VERTICES)
    lines: list[dict[str, Any]] = []
    vertex_count = 0

    for seed_index, seed in enumerate(seeds):
        point = [float(component) for component in seed]
        vertices: list[dict[str, Any]] = []
        termination = "max-vertices"
        while len(vertices) < per_line_limit:
            if cancelled():
                termination = "cancelled"
                break
            if vertex_count >= total_limit:
                termination = "total-vertex-limit"
                break
            sample = _sample(dataset, locator, velocity, color_fields, point)
            if sample is None:
                termination = "seed-outside-domain" if not vertices else "domain-exit"
                break
            speed = _magnitude(sample["velocity"])
            vertices.append(
                {
                    "position": list(point),
                    "velocity": sample["velocity"],
                    "speed": speed,
                    "fields": sample["fields"],
                    "provenance": sample["provenance"],
                    "terminationReason": "active",
                }
            )
            vertex_count += 1
            if speed <= ZERO_SPEED:
                termination = "zero-velocity"
                break
            next_point = _rk4(dataset, locator, velocity, point, step)
            if next_point is None or _velocity_sample(dataset, locator, velocity, next_point) is None:
                termination = "domain-exit"
                break
            point = next_point
        if vertices:
            vertices[-1]["terminationReason"] = termination
        lines.append({"seedIndex": seed_index, "vertices": vertices, "terminationReason": termination})
        if termination in {"cancelled", "total-vertex-limit"}:
            break

    return {
        "schema": "flowlab.steady_streamlines.v1",
        "terminology": "steady-streamline",
        "sourceName": source_name,
        "sourceIdentity": source_identity,
        "spatialDimension": _spatial_dimension(dataset),
        "velocityField": velocity["name"],
        "velocityLocation": velocity["location"],
        "velocityInterpolation": "barycentric point field" if velocity["location"] == "point" else "piecewise constant cell field",
        "fieldInterpolations": {
            "velocity": "barycentric point field" if velocity["location"] == "point" else "piecewise constant cell field",
            **{
                field_name: "barycentric point field" if field["location"] == "point" else "piecewise constant cell field"
                for field_name, field in color_fields.items()
            },
        },
        "lines": lines,
        "seedCount": len(seeds),
        "vertexCount": vertex_count,
        "limits": {
            "defaultSeeds": DEFAULT_SEEDS,
            "maxSeeds": MAX_SEEDS,
            "maxVerticesPerLine": MAX_VERTICES_PER_LINE,
            "maxTotalVertices": MAX_TOTAL_VERTICES,
            "maxSprites": MAX_SPRITES,
        },
    }


def derive_streamlines_from_artifact(
    case_dir: Path,
    case: SolverCase | None,
    request: StreamlineDerivationRequest,
) -> dict[str, Any]:
    if request.sourceRepresentation != "full":
        raise ValueError("Full result required.")
    artifact_path = (case_dir / request.artifactPath).resolve()
    case_root = case_dir.resolve()
    if case_root not in artifact_path.parents or not artifact_path.is_file():
        raise FileNotFoundError("Result artifact not found.")
    if artifact_path.suffix.lower() not in {".vtk", ".vtu"}:
        raise ValueError("Only VTK/VTU result artifacts support derived streamlines.")
    try:
        text = artifact_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Streamline derivation requires an ASCII UTF-8 VTK/VTU artifact.") from exc
    dataset = parse_vtk_result(text)
    _validate_full_dataset(dataset)
    if request.seedMode == "inlet-manifest":
        seeds = _inlet_manifest_seeds(case_dir, request.seedCount, case)
    elif request.seedPlane is not None:
        seeds = _generate_plane_seeds(request.seedPlane)
    elif not request.seeds:
        seeds = _dataset_plane_seeds(dataset, request.seedAxis, request.seedPosition, request.seedCount)
    else:
        seeds = [list(seed) for seed in request.seeds]
    if not seeds:
        raise ValueError("At least one user-plane seed is required.")
    source_identity = _verified_source_identity(case, request.artifactPath, len(dataset["cells"]))
    if source_identity != "verified-case-cell-order":
        raise ValueError(
            "Derived streamlines require an explicit resultComponentMap cell-range binding for the full artifact."
        )
    result = integrate_dataset(
        dataset,
        seeds,
        source_name=request.artifactPath,
        source_identity=source_identity,
        step_size=request.stepSize,
        max_vertices_per_line=request.maxVerticesPerLine,
        max_total_vertices=request.maxTotalVertices,
    )
    return result
