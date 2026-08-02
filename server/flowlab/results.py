from __future__ import annotations

import xml.etree.ElementTree as ET
import math
from typing import Any

from .result_identity import (
    SOURCE_CELL_ID_FIELD,
    SOURCE_IDENTITY_REPORT_SCHEMA,
)

VTK_POLYGON = 7
SUPPORTED_CELL_TYPES = {5, VTK_POLYGON, 9, 10, 12, 13, 14}
MAX_PREVIEW_FIELDS = 12


def parse_vtk_result(text: str) -> dict[str, Any]:
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return parse_vtu_result(text)
    return parse_legacy_vtk_result(text)


def summarize_vtk_result_text(text: str) -> dict[str, Any]:
    """Return bounded field metadata for a supported VTK/VTU result file."""

    return summarize_vtk_dataset(parse_vtk_result(text))


def preview_vtk_result_text(text: str, point_limit: int = 500, cell_limit: int = 500) -> dict[str, Any]:
    """Return a bounded geometry and field preview for a supported VTK/VTU result file."""

    return preview_vtk_dataset(parse_vtk_result(text), point_limit=point_limit, cell_limit=cell_limit)


def preview_vtk_dataset(dataset: dict[str, Any], point_limit: int = 500, cell_limit: int = 500) -> dict[str, Any]:
    point_limit = max(1, min(int(point_limit), 5_000))
    cell_limit = max(0, min(int(cell_limit), 5_000))
    points = dataset.get("points", []) if isinstance(dataset.get("points"), list) else []
    cells = dataset.get("cells", []) if isinstance(dataset.get("cells"), list) else []
    cell_types = dataset.get("cellTypes", []) if isinstance(dataset.get("cellTypes"), list) else []
    cell_indices = _bounded_cell_indices(cells, point_limit=point_limit, cell_limit=cell_limit)
    point_indices = _point_indices_for_cells(cells, cell_indices, len(points))
    if not point_indices:
        point_indices = _even_indices(len(points), point_limit)
    point_index_lookup = {source_index: preview_index for preview_index, source_index in enumerate(point_indices)}
    preview_cells = [
        [point_index_lookup[index] for index in cells[cell_index]]
        for cell_index in cell_indices
        if all(index in point_index_lookup for index in cells[cell_index])
    ]
    preview_cell_types = [cell_types[index] for index in cell_indices if index < len(cell_types)]
    source_cell_indices = (
        dataset.get("sourceCellIndices")
        if isinstance(dataset.get("sourceCellIndices"), list)
        else None
    )
    selected_source_cell_indices = (
        [source_cell_indices[index] for index in cell_indices]
        if source_cell_indices is not None
        else cell_indices
    )

    return {
        "schema": "flowlab.result_preview.v1",
        "format": dataset.get("format"),
        "sourcePointCount": len(points),
        "sourceCellCount": len(cells),
        "pointCount": len(point_indices),
        "cellCount": len(preview_cells),
        "pointLimit": point_limit,
        "cellLimit": cell_limit,
        "truncated": len(point_indices) < len(points) or len(preview_cells) < len(cells),
        "pointIndices": point_indices,
        "cellIndices": selected_source_cell_indices[: len(preview_cells)],
        "points": [points[index] for index in point_indices],
        "cells": preview_cells,
        "cellTypes": preview_cell_types[: len(preview_cells)],
        "fieldSummary": summarize_vtk_dataset(dataset),
        "fieldSamples": _field_samples(dataset, point_indices, cell_indices[: len(preview_cells)]),
        "sourceCellIdentity": dataset.get("sourceCellIdentity"),
    }


def _even_indices(count: int, limit: int) -> list[int]:
    if count <= 0 or limit <= 0:
        return []
    if count <= limit:
        return list(range(count))
    if limit == 1:
        return [0]
    return sorted({round(index * (count - 1) / (limit - 1)) for index in range(limit)})


def _bounded_cell_indices(cells: list[Any], point_limit: int, cell_limit: int) -> list[int]:
    selected: list[int] = []
    referenced: set[int] = set()
    for cell_index in _even_indices(len(cells), cell_limit):
        cell = cells[cell_index]
        if not isinstance(cell, list):
            continue
        candidate = referenced | {int(point_index) for point_index in cell}
        if len(candidate) > point_limit and selected:
            continue
        if len(candidate) > point_limit:
            break
        selected.append(cell_index)
        referenced = candidate
    return selected


def _point_indices_for_cells(cells: list[Any], cell_indices: list[int], point_count: int) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for cell_index in cell_indices:
        if cell_index >= len(cells) or not isinstance(cells[cell_index], list):
            continue
        for point_index in cells[cell_index]:
            parsed = int(point_index)
            if parsed < 0 or parsed >= point_count or parsed in seen:
                continue
            seen.add(parsed)
            ordered.append(parsed)
    return ordered


def _field_samples(dataset: dict[str, Any], point_indices: list[int], cell_indices: list[int]) -> dict[str, Any]:
    point_data = dataset.get("pointData") if isinstance(dataset.get("pointData"), dict) else {}
    cell_data = dataset.get("cellData") if isinstance(dataset.get("cellData"), dict) else {}
    return {
        "point": _sample_location_fields(point_data, point_indices),
        "cell": _sample_location_fields(cell_data, cell_indices),
    }


def _sample_location_fields(data: dict[str, Any], indices: list[int]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    scalar_fields = data.get("scalars") if isinstance(data.get("scalars"), dict) else {}
    vector_fields = data.get("vectors") if isinstance(data.get("vectors"), dict) else {}
    for name in sorted(scalar_fields):
        values = scalar_fields[name]
        if isinstance(values, list):
            samples.append({"name": str(name), "kind": "scalar", "values": [values[index] for index in indices if index < len(values)]})
        if len(samples) >= MAX_PREVIEW_FIELDS:
            return samples
    for name in sorted(vector_fields):
        values = vector_fields[name]
        if isinstance(values, list):
            vectors = [values[index] for index in indices if index < len(values)]
            samples.append(
                {
                    "name": str(name),
                    "kind": "vector",
                    "values": vectors,
                    "magnitudes": [
                        math.sqrt(float(vector[0]) ** 2 + float(vector[1]) ** 2 + float(vector[2]) ** 2)
                        for vector in vectors
                        if isinstance(vector, list) and len(vector) == 3
                    ],
                }
            )
        if len(samples) >= MAX_PREVIEW_FIELDS:
            return samples
    return samples


def summarize_vtk_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    point_data = dataset.get("pointData") if isinstance(dataset.get("pointData"), dict) else {}
    cell_data = dataset.get("cellData") if isinstance(dataset.get("cellData"), dict) else {}
    fields: list[dict[str, Any]] = []
    fields.extend(_summarize_scalar_fields(point_data.get("scalars"), "point"))
    fields.extend(_summarize_vector_fields(point_data.get("vectors"), "point"))
    fields.extend(_summarize_scalar_fields(cell_data.get("scalars"), "cell"))
    fields.extend(_summarize_vector_fields(cell_data.get("vectors"), "cell"))
    fields.sort(key=lambda item: (item["name"], item["location"], item["kind"]))
    return {
        "schema": "flowlab.result_field_summary.v1",
        "format": dataset.get("format"),
        "pointCount": len(dataset.get("points", [])),
        "cellCount": len(dataset.get("cells", [])),
        "fields": fields,
        "sourceCellIdentity": dataset.get("sourceCellIdentity"),
    }


def _extract_source_cell_identity(
    cell_scalars: dict[str, list[float]],
    cell_count: int,
) -> tuple[list[int] | None, dict[str, Any] | None]:
    raw_ids = cell_scalars.pop(SOURCE_CELL_ID_FIELD, None)
    if raw_ids is None:
        return None, None
    if len(raw_ids) != cell_count:
        raise ValueError(
            f"{SOURCE_CELL_ID_FIELD} count must match the result cell count."
        )
    source_ids: list[int] = []
    for value in raw_ids:
        number = float(value)
        if not math.isfinite(number) or not number.is_integer() or number < 0:
            raise ValueError(
                f"{SOURCE_CELL_ID_FIELD} must contain finite non-negative integers."
            )
        source_ids.append(int(number))
    if len(set(source_ids)) != cell_count or sorted(source_ids) != list(range(cell_count)):
        raise ValueError(
            f"{SOURCE_CELL_ID_FIELD} must be a unique complete source-cell permutation."
        )
    return source_ids, {
        "schema": SOURCE_IDENTITY_REPORT_SCHEMA,
        "field": SOURCE_CELL_ID_FIELD,
        "sourceCellCount": cell_count,
        "unique": True,
        "complete": True,
        "verified": True,
    }


def _summarize_scalar_fields(fields: Any, location: str) -> list[dict[str, Any]]:
    if not isinstance(fields, dict):
        return []
    summaries = []
    for name, values in fields.items():
        if not isinstance(values, list):
            continue
        numeric = [float(value) for value in values]
        stats = _stats(numeric)
        if stats is None:
            continue
        summaries.append({"name": str(name), "location": location, "kind": "scalar", "tupleCount": len(numeric), **stats})
    return summaries


def _summarize_vector_fields(fields: Any, location: str) -> list[dict[str, Any]]:
    if not isinstance(fields, dict):
        return []
    summaries = []
    for name, values in fields.items():
        if not isinstance(values, list):
            continue
        magnitudes = [
            math.sqrt(float(vector[0]) ** 2 + float(vector[1]) ** 2 + float(vector[2]) ** 2)
            for vector in values
            if isinstance(vector, list) and len(vector) == 3
        ]
        stats = _stats(magnitudes)
        if stats is None:
            continue
        summaries.append(
            {
                "name": str(name),
                "location": location,
                "kind": "vector-magnitude",
                "tupleCount": len(magnitudes),
                **stats,
            }
        )
    return summaries


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    sorted_values = sorted(values)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "mean": mean,
        "stdDev": math.sqrt(variance),
        "p50": _percentile(sorted_values, 0.5),
        "p95": _percentile(sorted_values, 0.95),
    }


def _percentile(sorted_values: list[float], percentile_rank: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = max(0.0, min(1.0, percentile_rank)) * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def parse_legacy_vtk_result(text: str) -> dict[str, Any]:
    tokens = text.replace("\r", " ").split()
    if len(tokens) < 8 or tokens[0] != "#" or "vtk" not in tokens[1].lower():
        raise ValueError("Only ASCII legacy VTK files are supported.")
    if "BINARY" in tokens[:20]:
        raise ValueError("Binary VTK files are not supported by the FlowLab v1 parser.")
    try:
        dataset_index = tokens.index("DATASET")
        dataset_type = tokens[dataset_index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("VTK DATASET declaration is missing.") from exc
    if dataset_type not in {"UNSTRUCTURED_GRID", "POLYDATA"}:
        raise ValueError(f"Unsupported VTK dataset type: {dataset_type}")

    points_index = tokens.index("POINTS")
    point_count = int(tokens[points_index + 1])
    cursor = points_index + 3
    points = [
        [float(tokens[cursor + index * 3]), float(tokens[cursor + index * 3 + 1]), float(tokens[cursor + index * 3 + 2])]
        for index in range(point_count)
    ]
    cursor += point_count * 3

    cell_section = "POLYGONS" if dataset_type == "POLYDATA" else "CELLS"
    try:
        cells_index = tokens.index(cell_section, cursor)
    except ValueError as exc:
        raise ValueError(f"VTK {cell_section} section is missing.") from exc
    cell_count = int(tokens[cells_index + 1])
    cursor = cells_index + 3
    cells: list[list[int]] = []
    for _ in range(cell_count):
        size = int(tokens[cursor])
        cursor += 1
        cells.append([int(token) for token in tokens[cursor : cursor + size]])
        cursor += size
    _validate_cells(cells, point_count)

    if dataset_type == "POLYDATA":
        cell_types = [VTK_POLYGON for _ in cells]
    else:
        if "CELL_TYPES" not in tokens[cursor:]:
            raise ValueError("VTK CELL_TYPES section is missing.")
        cell_types_index = tokens.index("CELL_TYPES", cursor)
        type_count = int(tokens[cell_types_index + 1])
        if type_count != cell_count:
            raise ValueError("CELL_TYPES count must match CELLS count.")
        cursor = cell_types_index + 2
        cell_types = [int(token) for token in tokens[cursor : cursor + type_count]]
        unsupported = sorted(set(cell_types) - SUPPORTED_CELL_TYPES)
        if unsupported:
            raise ValueError(f"Unsupported VTK cell types: {unsupported}")
        cursor += type_count

    point_scalars: dict[str, list[float]] = {}
    point_vectors: dict[str, list[list[float]]] = {}
    cell_scalars: dict[str, list[float]] = {}
    cell_vectors: dict[str, list[list[float]]] = {}
    while cursor < len(tokens):
        section = tokens[cursor]
        if section == "POINT_DATA":
            data_count = int(tokens[cursor + 1])
            if data_count != point_count:
                raise ValueError("POINT_DATA count must match POINTS count.")
            cursor = _parse_legacy_data_arrays(tokens, cursor + 2, point_count, "POINT_DATA", point_scalars, point_vectors)
        elif section == "CELL_DATA":
            data_count = int(tokens[cursor + 1])
            if data_count != cell_count:
                raise ValueError("CELL_DATA count must match CELLS count.")
            cursor = _parse_legacy_data_arrays(tokens, cursor + 2, cell_count, "CELL_DATA", cell_scalars, cell_vectors)
        else:
            raise ValueError(f"Unsupported VTK data section: {section}")

    source_cell_indices, source_cell_identity = _extract_source_cell_identity(
        cell_scalars,
        cell_count,
    )

    return {
        "format": "legacy-vtk-polydata-ascii-v1" if dataset_type == "POLYDATA" else "legacy-vtk-ascii-v1",
        "points": points,
        "cells": cells,
        "cellTypes": cell_types,
        "pointData": {"scalars": point_scalars, "vectors": point_vectors},
        "cellData": {"scalars": cell_scalars, "vectors": cell_vectors},
        "fields": sorted({*point_scalars.keys(), *point_vectors.keys(), *cell_scalars.keys(), *cell_vectors.keys()}),
        **(
            {
                "sourceCellIndices": source_cell_indices,
                "sourceCellCount": cell_count,
                "sourceCellIdentity": source_cell_identity,
            }
            if source_cell_indices is not None and source_cell_identity is not None
            else {}
        ),
    }


def parse_vtu_result(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)
    if root.tag != "VTKFile" or root.attrib.get("type") != "UnstructuredGrid":
        raise ValueError("Only VTK XML UnstructuredGrid (.vtu) files are supported.")
    piece = root.find(".//Piece")
    if piece is None:
        raise ValueError("VTU Piece is missing.")
    declared_points = int(piece.attrib.get("NumberOfPoints", "0"))
    declared_cells = int(piece.attrib.get("NumberOfCells", "0"))
    points_array = piece.find("./Points/DataArray")
    if points_array is None or points_array.attrib.get("format", "ascii") != "ascii":
        raise ValueError("VTU parser only supports ASCII point DataArray values.")
    components = int(points_array.attrib.get("NumberOfComponents", "3"))
    if components != 3:
        raise ValueError("VTU points must have three components.")
    point_tokens = _numbers(points_array.text or "")
    if len(point_tokens) != declared_points * 3:
        raise ValueError("VTU point count does not match NumberOfPoints.")
    points = [point_tokens[index : index + 3] for index in range(0, len(point_tokens), 3)]

    cells_node = piece.find("./Cells")
    if cells_node is None:
        raise ValueError("VTU Cells section is missing.")
    connectivity = _named_array(cells_node, "connectivity", int)
    offsets = _named_array(cells_node, "offsets", int)
    cell_types = _named_array(cells_node, "types", int)
    if len(offsets) != declared_cells or len(cell_types) != declared_cells:
        raise ValueError("VTU cell arrays do not match NumberOfCells.")
    unsupported = sorted(set(cell_types) - SUPPORTED_CELL_TYPES)
    if unsupported:
        raise ValueError(f"Unsupported VTU cell types: {unsupported}")
    cells: list[list[int]] = []
    previous = 0
    for offset in offsets:
        if offset <= previous or offset > len(connectivity):
            raise ValueError("VTU cell offsets are invalid.")
        cells.append(connectivity[previous:offset])
        previous = offset
    if previous != len(connectivity):
        raise ValueError("VTU final cell offset does not consume connectivity.")
    _validate_cells(cells, declared_points)

    scalars: dict[str, list[float]] = {}
    vectors: dict[str, list[list[float]]] = {}
    point_data = piece.find("./PointData")
    if point_data is not None:
        for array in point_data.findall("./DataArray"):
            if array.attrib.get("format", "ascii") != "ascii":
                raise ValueError("VTU parser only supports ASCII PointData arrays.")
            name = array.attrib.get("Name")
            if not name:
                raise ValueError("VTU PointData arrays must have a Name.")
            values = _numbers(array.text or "")
            item_components = int(array.attrib.get("NumberOfComponents", "1"))
            if item_components == 1:
                if len(values) != declared_points:
                    raise ValueError(f"VTU scalar {name} count does not match NumberOfPoints.")
                scalars[name] = values
            elif item_components == 3:
                if len(values) != declared_points * 3:
                    raise ValueError(f"VTU vector {name} count does not match NumberOfPoints.")
                vectors[name] = [values[index : index + 3] for index in range(0, len(values), 3)]
            else:
                raise ValueError(f"Unsupported VTU PointData component count for {name}: {item_components}")

    cell_scalars: dict[str, list[float]] = {}
    cell_vectors: dict[str, list[list[float]]] = {}
    cell_data = piece.find("./CellData")
    if cell_data is not None:
        for array in cell_data.findall("./DataArray"):
            if array.attrib.get("format", "ascii") != "ascii":
                raise ValueError("VTU parser only supports ASCII CellData arrays.")
            name = array.attrib.get("Name")
            if not name:
                raise ValueError("VTU CellData arrays must have a Name.")
            values = _numbers(array.text or "")
            item_components = int(array.attrib.get("NumberOfComponents", "1"))
            if item_components == 1:
                if len(values) != declared_cells:
                    raise ValueError(f"VTU cell scalar {name} count does not match NumberOfCells.")
                cell_scalars[name] = values
            elif item_components == 3:
                if len(values) != declared_cells * 3:
                    raise ValueError(f"VTU cell vector {name} count does not match NumberOfCells.")
                cell_vectors[name] = [values[index : index + 3] for index in range(0, len(values), 3)]
            else:
                raise ValueError(f"Unsupported VTU CellData component count for {name}: {item_components}")

    source_cell_indices, source_cell_identity = _extract_source_cell_identity(
        cell_scalars,
        declared_cells,
    )

    return {
        "format": "vtu-ascii-v1",
        "points": points,
        "cells": cells,
        "cellTypes": cell_types,
        "pointData": {"scalars": scalars, "vectors": vectors},
        "cellData": {"scalars": cell_scalars, "vectors": cell_vectors},
        "fields": sorted({*scalars.keys(), *vectors.keys(), *cell_scalars.keys(), *cell_vectors.keys()}),
        **(
            {
                "sourceCellIndices": source_cell_indices,
                "sourceCellCount": declared_cells,
                "sourceCellIdentity": source_cell_identity,
            }
            if source_cell_indices is not None and source_cell_identity is not None
            else {}
        ),
    }


def _parse_legacy_data_arrays(
    tokens: list[str],
    cursor: int,
    tuple_count: int,
    context: str,
    scalars: dict[str, list[float]],
    vectors: dict[str, list[list[float]]],
) -> int:
    while cursor < len(tokens) and tokens[cursor] not in {"POINT_DATA", "CELL_DATA"}:
        section = tokens[cursor]
        if section == "SCALARS":
            name = tokens[cursor + 1]
            components = int(tokens[cursor + 3]) if cursor + 3 < len(tokens) and tokens[cursor + 3].isdigit() else 1
            if components != 1:
                raise ValueError(f"Only single-component SCALARS are supported, got {name} with {components}.")
            cursor += 4 if cursor + 3 < len(tokens) and tokens[cursor + 3].isdigit() else 3
            if cursor + 1 >= len(tokens) or tokens[cursor] != "LOOKUP_TABLE":
                raise ValueError(f"SCALARS {name} is missing LOOKUP_TABLE.")
            cursor += 2
            scalars[name] = [float(token) for token in tokens[cursor : cursor + tuple_count]]
            cursor += tuple_count
        elif section == "VECTORS":
            name = tokens[cursor + 1]
            cursor += 3
            values: list[list[float]] = []
            for _ in range(tuple_count):
                values.append([float(tokens[cursor]), float(tokens[cursor + 1]), float(tokens[cursor + 2])])
                cursor += 3
            vectors[name] = values
        elif section == "FIELD":
            cursor += 1
            if cursor >= len(tokens):
                raise ValueError(f"{context} FIELD section is missing a name.")
            cursor += 1
            array_count = int(tokens[cursor])
            cursor += 1
            for _ in range(array_count):
                name = tokens[cursor]
                components = int(tokens[cursor + 1])
                tuples = int(tokens[cursor + 2])
                cursor += 4
                value_count = components * tuples
                raw_values = [float(token) for token in tokens[cursor : cursor + value_count]]
                if tuples == tuple_count and components == 1:
                    scalars[name] = raw_values
                elif tuples == tuple_count and components == 3:
                    vectors[name] = [raw_values[index : index + 3] for index in range(0, len(raw_values), 3)]
                elif tuples == tuple_count:
                    raise ValueError(f"Unsupported {context} FIELD array component count for {name}: {components}")
                cursor += value_count
        else:
            raise ValueError(f"Unsupported {context} section: {section}")
    return cursor


def _numbers(text: str) -> list[float]:
    return [float(token) for token in text.split()]


def _named_array(node: ET.Element, name: str, caster: type[int] | type[float]) -> list[Any]:
    for array in node.findall("./DataArray"):
        if array.attrib.get("Name") == name:
            if array.attrib.get("format", "ascii") != "ascii":
                raise ValueError(f"VTU {name} array must be ASCII.")
            return [caster(float(token)) for token in (array.text or "").split()]
    raise ValueError(f"VTU Cells array {name} is missing.")


def _validate_cells(cells: list[list[int]], point_count: int) -> None:
    for cell in cells:
        if len(cell) < 3:
            raise ValueError("VTK cells must have at least three points.")
        if any(index < 0 or index >= point_count for index in cell):
            raise ValueError("VTK cell connectivity is out of range.")
