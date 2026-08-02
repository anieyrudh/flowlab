from __future__ import annotations

import shutil
import subprocess
import json
import importlib.util
import hashlib
import math
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceleration import build_openfoam_parallel_plan
from .full_ogrid import (
    FULL_OGRID_PATH_PROFILE_SCHEMA,
    FULL_OGRID_PROFILE_SCHEMA,
    FULL_OGRID_VERIFICATION_SCHEMA,
    FullOGridPathSegment,
    FullOGridPathSpec,
    FullOGridSpec,
    block_mesh_dict as full_ogrid_block_mesh_dict,
    path_block_mesh_dict as full_ogrid_path_block_mesh_dict,
    path_preview_mesh as full_ogrid_path_preview_mesh,
    preview_mesh as full_ogrid_preview_mesh,
)
from .curved_elbow import (
    CURVED_ELBOW_PROFILE_SCHEMA,
    CURVED_ELBOW_REPRESENTATION,
    CURVED_ELBOW_VERIFICATION_SCHEMA,
    CurvedElbowSpec,
    block_mesh_dict as curved_elbow_block_mesh_dict,
    preview_mesh as curved_elbow_preview_mesh,
)
from .mesh import (
    VTK_HEXAHEDRON,
    generate_mesh_bundle,
    mesh_to_legacy_vtk,
    mesh_to_openfoam_cht_region_polymesh,
    mesh_to_openfoam_polymesh,
    mesh_to_vtu,
    su2_marker_tags,
)
from .schemas import CaseRequest, ResultComponentMap, SolverCapability, SolverCase
from .result_identity import (
    SOURCE_CELL_ID_FIELD,
    SOURCE_IDENTITY_CONTRACT_PATH,
    SOURCE_IDENTITY_REPORT_SCHEMA,
    ResultIdentityError,
    source_cell_identity_contract,
)
from .validated_benchmark import experimental_capability
from .y_junction import (
    JUNCTION_ARTIFACT_ID,
    Y_JUNCTION_PROFILE_SCHEMA,
    Y_JUNCTION_REPRESENTATION,
    YJunctionSpec,
    generate_fixed_master_mesh as generate_fixed_master_y_junction_mesh,
    generate_mesh as generate_y_junction_mesh,
    mesh_to_openfoam_polymesh as y_junction_to_openfoam_polymesh,
    public_mesh as public_y_junction_mesh,
)

CASE_MANIFEST_PATH = "flowlab_case_manifest.json"
EVIDENCE_CAPABILITY_PATH = "evidence/capability.json"
DEFAULT_OPENFOAM_IMAGE = "flowlab/openfoam11-gmsh:2026-07-13"
OPENFOAM_IMAGE_ENV = "FLOWLAB_OPENFOAM_IMAGE"


def _openfoam_image() -> str:
    """Return the shared product and scientific OpenFOAM runtime image."""
    return os.environ.get(OPENFOAM_IMAGE_ENV, "").strip() or DEFAULT_OPENFOAM_IMAGE


@dataclass(frozen=True)
class CaseConditions:
    density: float = 998.2
    dynamic_viscosity: float = 0.001002
    temperature: float = 293.15
    vapor_pressure: float = 2340.0
    bulk_modulus: float = 2.2e9
    inlet_velocity: float = 1.0
    inlet_pressure: float = 250000.0
    outlet_pressure: float = 101325.0
    hydraulic_diameter: float = 0.1
    reference_area: float = 0.007853981633974483

    @property
    def kinematic_viscosity(self) -> float:
        return self.dynamic_viscosity / self.density

    @property
    def outlet_gauge_pressure(self) -> float:
        return self.outlet_pressure - 101325.0

    @property
    def outlet_kinematic_pressure(self) -> float:
        return self.outlet_gauge_pressure / self.density


def _safe_positive(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _openfoam_ideal_gas_density(conditions: CaseConditions) -> float:
    gas_constant = 287.05
    temperature = max(conditions.temperature, 1.0)
    pressure = max(conditions.outlet_pressure, 1.0)
    return pressure / (gas_constant * temperature)


def _shape_area(shape: dict[str, Any] | None) -> float:
    if not isinstance(shape, dict):
        return 0.007853981633974483
    if shape.get("kind") == "rectangular":
        return _safe_positive(shape.get("width"), 0.1) * _safe_positive(shape.get("height"), 0.1)
    diameter = _safe_positive(shape.get("diameter"), 0.1)
    return 3.141592653589793 * diameter * diameter / 4.0


def _shape_hydraulic_diameter(shape: dict[str, Any] | None) -> float:
    if not isinstance(shape, dict):
        return 0.1
    if shape.get("kind") == "rectangular":
        width = _safe_positive(shape.get("width"), 0.1)
        height = _safe_positive(shape.get("height"), 0.1)
        return 2.0 * width * height / (width + height)
    return _safe_positive(shape.get("diameter"), 0.1)


def _shape_width_for_station(shape: dict[str, Any] | None, edge: dict[str, Any], station: float = 0.0) -> float:
    if not isinstance(shape, dict):
        return 0.1
    if shape.get("kind") == "rectangular":
        return _safe_positive(shape.get("height"), 0.1)
    diameter = _safe_positive(shape.get("diameter"), 0.1)
    outlet_diameter = _safe_positive(edge.get("outletDiameter"), diameter)
    if edge.get("type") == "venturi" and edge.get("throatDiameter"):
        throat = _safe_positive(edge.get("throatDiameter"), diameter)
        throat_position = float(edge.get("throatPosition", 0.5))
        edge_length = _safe_positive(edge.get("length"), 1.0)
        throat_length = max(0.0, float(edge.get("throatLength", 0.0)))
        half_fraction = throat_length / (2.0 * edge_length)
        throat_start = max(1.0e-9, throat_position - half_fraction)
        throat_end = min(1.0 - 1.0e-9, throat_position + half_fraction)
        if station <= throat_start:
            fraction = station / throat_start
            return diameter + (throat - diameter) * fraction
        if station <= throat_end:
            return throat
        fraction = (station - throat_end) / (1.0 - throat_end)
        return throat + (outlet_diameter - throat) * fraction
    return diameter + (outlet_diameter - diameter) * station


def _project_nodes(project: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = project.get("nodes")
    if isinstance(nodes, dict):
        return [node for node in nodes.values() if isinstance(node, dict)]
    if isinstance(nodes, list):
        return [node for node in nodes if isinstance(node, dict)]
    return []


def _project_edges(project: dict[str, Any]) -> list[dict[str, Any]]:
    edges = project.get("edges")
    if isinstance(edges, dict):
        return [edge for edge in edges.values() if isinstance(edge, dict)]
    if isinstance(edges, list):
        return [edge for edge in edges if isinstance(edge, dict)]
    return []


def _node_radius(node: dict[str, Any]) -> float:
    node_type = str(node.get("type", "junction"))
    if node_type in {"source", "sink"}:
        return 17.0
    if node_type == "pump":
        return 19.0
    return 14.0


def _node_position(node: dict[str, Any]) -> tuple[float, float]:
    position = node.get("position") if isinstance(node.get("position"), dict) else {}
    return float(position.get("x", 0.0)), float(position.get("y", 0.0))


def _node_port_position(node: dict[str, Any], port: str) -> tuple[float, float]:
    base = float(node.get("rotation") or 0.0)
    angle_by_port = {"outlet": base, "inlet": base + 180.0, "north": base - 90.0, "south": base + 90.0}
    angle = math.radians(angle_by_port.get(port, base))
    x, y = _node_position(node)
    radius = _node_radius(node) + 10.0
    return x + math.cos(angle) * radius, y + math.sin(angle) * radius


def _edge_effective_length(edge: dict[str, Any], nodes_by_id: dict[str, dict[str, Any]]) -> float:
    nominal = _safe_positive(edge.get("length"), 1.0)
    from_node = nodes_by_id.get(str(edge.get("from")))
    to_node = nodes_by_id.get(str(edge.get("to")))
    if not from_node or not to_node:
        return nominal
    x0, y0 = _node_port_position(from_node, str(edge.get("fromPort") or "outlet"))
    x1, y1 = _node_port_position(to_node, str(edge.get("toPort") or "inlet"))
    cx0, cy0 = _node_position(from_node)
    cx1, cy1 = _node_position(to_node)
    port_length = math.hypot(x1 - x0, y1 - y0)
    center_length = math.hypot(cx1 - cx0, cy1 - cy0) or port_length or 1.0
    return max(0.05, nominal * (port_length or center_length) / center_length)


def _first_node(nodes: list[dict[str, Any]], node_type: str) -> dict[str, Any] | None:
    return next((node for node in nodes if node.get("type") == node_type), None)


def _case_conditions(project: dict[str, Any]) -> CaseConditions:
    fluid = project.get("fluid") if isinstance(project.get("fluid"), dict) else {}
    density = _safe_positive(fluid.get("density") if fluid else None, 998.2)
    dynamic_viscosity = _safe_positive(fluid.get("dynamicViscosity") if fluid else None, 0.001002)
    nodes = _project_nodes(project)
    edges = _project_edges(project)
    source = _first_node(nodes, "source") or {}
    sink = _first_node(nodes, "sink") or {}
    first_edge = edges[0] if edges else {}
    shape = first_edge.get("shape") if isinstance(first_edge.get("shape"), dict) else None
    area = _shape_area(shape)
    flow_demand = sink.get("flowDemand") if isinstance(sink, dict) else None
    inlet_velocity = abs(float(flow_demand) / area) if isinstance(flow_demand, (int, float)) and area > 0 else 1.0
    return CaseConditions(
        density=density,
        dynamic_viscosity=dynamic_viscosity,
        temperature=_safe_positive(fluid.get("temperature") if fluid else None, 293.15),
        vapor_pressure=_safe_positive(fluid.get("vaporPressure") if fluid else None, 2340.0),
        bulk_modulus=_safe_positive(fluid.get("bulkModulus") if fluid else None, 2.2e9),
        inlet_velocity=inlet_velocity,
        inlet_pressure=_safe_positive(source.get("pressure") if isinstance(source, dict) else None, 250000.0),
        outlet_pressure=_safe_positive(sink.get("pressure") if isinstance(sink, dict) else None, 101325.0),
        hydraulic_diameter=_shape_hydraulic_diameter(shape),
        reference_area=area,
    )


def _foam_header(class_name: str, object_name: str) -> str:
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
  FlowLab generated OpenFOAM dictionary
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    object      {object_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

"""


def _case_file_digest(content: str) -> dict[str, str | int]:
    encoded = content.encode("utf-8")
    return {"size": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _mesh_edge_cell_ranges(
    project: dict[str, Any],
    mesh_snapshot: str | None,
) -> tuple[int, list[dict[str, int | str]]] | None:
    """Read deterministic edge ranges declared by the generated mesh metadata."""
    if not isinstance(mesh_snapshot, str):
        return None
    try:
        mesh = json.loads(mesh_snapshot)
    except (TypeError, json.JSONDecodeError):
        return None
    cells = mesh.get("cells")
    regions = mesh.get("regions")
    if not isinstance(cells, list) or not cells or not isinstance(regions, list):
        return None

    project_edge_ids = {
        str(edge.get("id"))
        for edge in _project_edges(project)
        if isinstance(edge.get("id"), str) and edge.get("id")
    }
    ranges: list[dict[str, int | str]] = []
    for region in regions:
        if not isinstance(region, dict) or region.get("edgeId") not in project_edge_ids:
            continue
        edge_id = str(region["edgeId"])
        cell_start = region.get("cellStart")
        cell_count = region.get("cellCount")
        if not isinstance(cell_start, int) or isinstance(cell_start, bool):
            return None
        if not isinstance(cell_count, int) or isinstance(cell_count, bool) or cell_count <= 0:
            return None
        if cell_start < 0 or cell_start + cell_count > len(cells):
            return None
        cell_range: dict[str, int | str] = {
            "edgeId": edge_id,
            "cellStart": cell_start,
            "cellCount": cell_count,
        }
        component_id = region.get("componentId")
        if isinstance(component_id, str) and component_id:
            cell_range["componentId"] = component_id
        ranges.append(cell_range)

    if not ranges or {str(item["edgeId"]) for item in ranges} != project_edge_ids:
        return None
    ranges.sort(key=lambda item: (int(item["cellStart"]), str(item["edgeId"])))
    previous_stop = 0
    for item in ranges:
        start = int(item["cellStart"])
        stop = start + int(item["cellCount"])
        if start < previous_stop:
            return None
        previous_stop = stop
    return len(cells), ranges


def _mesh_unowned_cell_ranges(mesh_snapshot: str | None) -> list[dict[str, Any]]:
    """Read dedicated generated ranges that intentionally have no edge owner."""

    if not isinstance(mesh_snapshot, str):
        return []
    try:
        mesh = json.loads(mesh_snapshot)
    except (TypeError, json.JSONDecodeError):
        return []
    cells = mesh.get("cells")
    regions = mesh.get("regions")
    if not isinstance(cells, list) or not isinstance(regions, list):
        return []
    ranges: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or region.get("role") != "junction":
            continue
        identity = (
            region.get("artifactIdentity")
            if isinstance(region.get("artifactIdentity"), dict)
            else {}
        )
        artifact_id = identity.get("artifactId")
        cell_start = region.get("cellStart")
        cell_count = region.get("cellCount")
        if (
            identity.get("generated") is not True
            or identity.get("schematicOwner") is not None
            or not isinstance(artifact_id, str)
            or not artifact_id
            or not isinstance(cell_start, int)
            or isinstance(cell_start, bool)
            or not isinstance(cell_count, int)
            or isinstance(cell_count, bool)
            or cell_start < 0
            or cell_count <= 0
            or cell_start + cell_count > len(cells)
        ):
            return []
        ranges.append(
            {
                "artifactId": artifact_id,
                "cellStart": cell_start,
                "cellCount": cell_count,
                "schematicOwner": None,
            }
        )
    ranges.sort(key=lambda item: (int(item["cellStart"]), str(item["artifactId"])))
    return ranges


def _result_component_map(
    project: dict[str, Any],
    project_snapshot: str | None = None,
    *,
    solver: str | None = None,
    mesh_snapshot: str | None = None,
    identity_contract_snapshot: str | None = None,
) -> ResultComponentMap | None:
    """Declare whole-artifact or source-cell result ownership.

    Generic VTK/VTU data does not contain a dependable component identifier.  A
    FlowLab-generated case with exactly one edge is the one safe v1 exception:
    every generated result cell belongs to that edge.  Supported multi-edge
    OpenFOAM cases use the generated mesh's explicit cell ranges and only admit
    known whole-volume result artifact families with the exact source cell
    count. Imported and unsupported artifacts intentionally receive no map.
    """
    edges = _project_edges(project)
    if not edges:
        return None
    # Bind the map to the exact queued snapshot file, not a re-serialized
    # in-memory object.  The client verifies this digest against the case
    # manifest before it permits a selection link.
    canonical_project = project_snapshot if isinstance(project_snapshot, str) else json.dumps(project, separators=(",", ":"), sort_keys=True)
    project_sha256 = hashlib.sha256(canonical_project.encode("utf-8")).hexdigest()
    explicit_range_map = _mesh_edge_cell_ranges(project, mesh_snapshot)
    requires_explicit_ranges = False
    if isinstance(mesh_snapshot, str):
        try:
            mesh_manifest = json.loads(mesh_snapshot)
        except json.JSONDecodeError:
            mesh_manifest = {}
        requires_explicit_ranges = (
            isinstance(mesh_manifest, dict)
            and mesh_manifest.get("requiresExplicitSourceCellProvenance") is True
        )
    if len(edges) == 1 and not requires_explicit_ranges:
        edge_id = edges[0].get("id")
        if not isinstance(edge_id, str) or not edge_id:
            return None
        return ResultComponentMap(
            version=1,
            projectSha256=project_sha256,
            artifactBindings=[{"artifactName": "*", "edgeId": edge_id, "scope": "all-cells"}],
        )

    if solver != "openfoam":
        return None
    range_map = explicit_range_map
    if range_map is None:
        return None
    source_cell_count, cell_ranges = range_map
    if not isinstance(identity_contract_snapshot, str):
        return None
    try:
        identity_contract = json.loads(identity_contract_snapshot)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(identity_contract, dict)
        or identity_contract.get("sourceCellCount") != source_cell_count
        or identity_contract.get("identityField") != SOURCE_CELL_ID_FIELD
        or identity_contract.get("orderingAssumptionAllowed") is not False
    ):
        return None
    identity_contract_sha256 = hashlib.sha256(
        identity_contract_snapshot.encode("utf-8")
    ).hexdigest()
    unowned_cell_ranges = _mesh_unowned_cell_ranges(mesh_snapshot)
    return ResultComponentMap(
        version=2,
        projectSha256=project_sha256,
        artifactBindings=[
            {
                "artifactName": "postProcessing/flowlabNative/*.vtk",
                "scope": "cell-ranges",
                "sourceCellCount": source_cell_count,
                "identitySchema": SOURCE_IDENTITY_REPORT_SCHEMA,
                "identityField": SOURCE_CELL_ID_FIELD,
                "identityContractSha256": identity_contract_sha256,
                "cellRanges": cell_ranges,
                "unownedCellRanges": unowned_cell_ranges,
            }
        ],
    )


def add_case_manifest(case: SolverCase) -> SolverCase:
    files = {
        path: _case_file_digest(content)
        for path, content in sorted(case.files.items())
        if path != CASE_MANIFEST_PATH
    }
    manifest = {
        "schema": "flowlab.case_manifest.v1",
        "projectName": case.projectName,
        "solver": case.solver,
        "advancedMode": case.advancedMode,
        "status": case.status,
        "runCommand": case.runCommand,
        "fileCount": len(files),
        "files": files,
        "provenance": case.provenance,
        "resultComponentMap": case.resultComponentMap.model_dump(mode="json") if case.resultComponentMap else None,
    }
    case.files[CASE_MANIFEST_PATH] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if not any("case manifest" in entry.lower() for entry in case.provenance):
        case.provenance.append("Case manifest records generated file sizes and SHA-256 hashes for solve-through evidence.")
    return case


def _mesh_bounds(mesh: dict[str, Any] | None) -> tuple[float, float, float, float]:
    if not mesh or not mesh.get("points"):
        return -0.5, 0.5, -0.25, 0.25
    xs = [float(point[0]) for point in mesh["points"]]
    ys = [float(point[1]) for point in mesh["points"]]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = max((max_x - min_x) * 0.08, 10.0)
    pad_y = max((max_y - min_y) * 0.18, 10.0)
    return min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y


def _openfoam_block_mesh_dict(mesh: dict[str, Any] | None) -> str:
    min_x, max_x, min_y, max_y = _mesh_bounds(mesh)
    cells_x = 48
    cells_y = 16
    z = 1.0
    return (
        _foam_header("dictionary", "blockMeshDict")
        + f"""scale 0.01;

vertices
(
    ({min_x:.6f} {min_y:.6f} 0)
    ({max_x:.6f} {min_y:.6f} 0)
    ({max_x:.6f} {max_y:.6f} 0)
    ({min_x:.6f} {max_y:.6f} 0)
    ({min_x:.6f} {min_y:.6f} {z:.6f})
    ({max_x:.6f} {min_y:.6f} {z:.6f})
    ({max_x:.6f} {max_y:.6f} {z:.6f})
    ({min_x:.6f} {max_y:.6f} {z:.6f})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({cells_x} {cells_y} 1) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
    inlet
    {{
        type patch;
        faces
        (
            (0 4 7 3)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (1 2 6 5)
        );
    }}
    walls
    {{
        type wall;
        faces
        (
            (0 1 5 4)
            (3 7 6 2)
        );
    }}
    frontAndBack
    {{
        type empty;
        faces
        (
            (0 3 2 1)
            (4 5 6 7)
        );
    }}
);

mergePatchPairs
(
);
"""
    )


# The "far" patches close the transverse extent of the domain. Planar-2D uses a
# single empty `frontAndBack`; the axisymmetric wedge uses `wedge` front/back
# faces plus an empty collapsed `axis`. Field boundaryField patch names must match
# the mesh exactly or foamRun aborts, so these move in lockstep with the mesh mode.
_PLANAR_FAR_FIELD_PATCHES = """    frontAndBack
    {
        type            empty;
    }"""

_WEDGE_FAR_FIELD_PATCHES = """    front
    {
        type            wedge;
    }
    back
    {
        type            wedge;
    }
    axis
    {
        type            empty;
    }"""


def _openfoam_vector_field(object_name: str, internal: str = "(1 0 0)", far_patches: str | None = None) -> str:
    far = _PLANAR_FAR_FIELD_PATCHES if far_patches is None else far_patches
    return (
        _foam_header("volVectorField", object_name)
        + f"""dimensions      [0 1 -1 0 0 0 0];

internalField   uniform {internal};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {internal};
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    walls
    {{
        type            noSlip;
    }}
{far}
}}
"""
    )


def _openfoam_pressure_field(
    object_name: str = "p",
    dimensions: str = "[0 2 -2 0 0 0 0]",
    internal: str = "0",
    outlet: str = "0",
    far_patches: str | None = None,
) -> str:
    far = _PLANAR_FAR_FIELD_PATCHES if far_patches is None else far_patches
    return (
        _foam_header("volScalarField", object_name)
        + f"""dimensions      {dimensions};

internalField   uniform {internal};

boundaryField
{{
    inlet
    {{
        type            zeroGradient;
    }}
    outlet
    {{
        type            fixedValue;
        value           uniform {outlet};
    }}
    walls
    {{
        type            zeroGradient;
    }}
{far}
}}
"""
    )


def _openfoam_y_junction_vector_field(profile: dict[str, Any]) -> str:
    velocity = float(profile["flow"]["inletMeanVelocityMPerS"])
    return (
        _foam_header("volVectorField", "U")
        + f"""dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ({velocity:.17g} 0 0);

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform ({velocity:.17g} 0 0);
    }}
    outletUpper
    {{
        type            pressureInletOutletVelocity;
        value           uniform (0 0 0);
    }}
    outletLower
    {{
        type            pressureInletOutletVelocity;
        value           uniform (0 0 0);
    }}
    walls
    {{
        type            noSlip;
    }}
}}
"""
    )


def _openfoam_y_junction_scalar_field(
    object_name: str,
    *,
    dimensions: str,
    internal: float,
    upper_outlet: float,
    lower_outlet: float,
) -> str:
    return (
        _foam_header("volScalarField", object_name)
        + f"""dimensions      {dimensions};

internalField   uniform {internal:.17g};

boundaryField
{{
    inlet
    {{
        type            zeroGradient;
    }}
    outletUpper
    {{
        type            fixedValue;
        value           uniform {upper_outlet:.17g};
    }}
    outletLower
    {{
        type            fixedValue;
        value           uniform {lower_outlet:.17g};
    }}
    walls
    {{
        type            zeroGradient;
    }}
}}
"""
    )


def _openfoam_y_junction_temperature_field(temperature: float) -> str:
    return (
        _foam_header("volScalarField", "T")
        + f"""dimensions      [0 0 0 1 0 0 0];

internalField   uniform {temperature:.17g};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {temperature:.17g};
    }}
    outletUpper
    {{
        type            zeroGradient;
    }}
    outletLower
    {{
        type            zeroGradient;
    }}
    walls
    {{
        type            zeroGradient;
    }}
}}
"""
    )


def _openfoam_axisymmetric_periodic_vector_field(
    internal: str,
    far_patches: str = _WEDGE_FAR_FIELD_PATCHES,
) -> str:
    return (
        _foam_header("volVectorField", "U")
        + f"""dimensions      [0 1 -1 0 0 0 0];

internalField   uniform {internal};

boundaryField
{{
    inlet
    {{
        type            cyclic;
    }}
    outlet
    {{
        type            cyclic;
    }}
    walls
    {{
        type            noSlip;
    }}
{far_patches}
}}
"""
    )


def _openfoam_axisymmetric_periodic_scalar_field(
    object_name: str,
    dimensions: str,
    internal: str,
    wall_type: str = "zeroGradient",
    far_patches: str = _WEDGE_FAR_FIELD_PATCHES,
) -> str:
    return (
        _foam_header("volScalarField", object_name)
        + f"""dimensions      {dimensions};

internalField   uniform {internal};

boundaryField
{{
    inlet
    {{
        type            cyclic;
    }}
    outlet
    {{
        type            cyclic;
    }}
    walls
    {{
        type            {wall_type};
    }}
{far_patches}
}}
"""
    )


AXISYMMETRIC_WEDGE_HALF_ANGLE_DEG = 2.5
AXISYMMETRIC_PROFILE_SCHEMA = "flowlab.axisymmetric-profile.v1"
AXISYMMETRIC_BENCHMARK_SCHEMA = "flowlab.axisymmetric-straight-pipe-contract.v1"
AXISYMMETRIC_QUALIFICATION_SCHEMA = (
    "flowlab.axisymmetric-geometry-experimental-qualification-request.v1"
)
AXISYMMETRIC_QUALIFICATION_CONTRACT_ID = (
    "axisymmetric-generated-geometry-experimental-qualification-v1"
)
AXISYMMETRIC_ALLOWED_EDGE_TYPES = {"pipe", "venturi", "expansion", "contraction", "nozzle"}


def _openfoam_axisymmetric_mode_requested(project: dict[str, Any] | None) -> bool:
    solver = project.get("solver") if isinstance(project, dict) and isinstance(project.get("solver"), dict) else {}
    return str(solver.get("meshMode", "planar-2d")).strip().lower() == "axisymmetric"


def _axisymmetric_positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Axisymmetric geometry requires a numeric {label}.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Axisymmetric geometry requires a positive {label}.")
    return number


def _axisymmetric_nonnegative_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Axisymmetric geometry requires a numeric {label}.") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Axisymmetric geometry requires a non-negative {label}.")
    return number


def _axisymmetric_exact_cell_controls(
    project: dict[str, Any],
    edge_count: int,
) -> tuple[int, int] | None:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    axial_raw = controls.get("axisymmetricAxialCells")
    radial_raw = controls.get("axisymmetricRadialCells")
    axial_by_edge_raw = controls.get("axisymmetricAxialCellsByEdge")
    if axial_by_edge_raw is not None:
        if axial_raw is not None:
            raise ValueError(
                "Use either axisymmetricAxialCells or axisymmetricAxialCellsByEdge, not both."
            )
        return None
    if axial_raw is None and radial_raw is None:
        return None
    if axial_raw is None or radial_raw is None:
        raise ValueError("Exact axisymmetric axial and radial cell counts must be supplied together.")
    if (
        not isinstance(axial_raw, int)
        or isinstance(axial_raw, bool)
        or axial_raw < 4
        or not isinstance(radial_raw, int)
        or isinstance(radial_raw, bool)
        or radial_raw < 2
    ):
        raise ValueError("Exact axisymmetric mesh controls require at least 4 axial and 2 radial cells.")
    if edge_count != 1:
        raise ValueError(
            "Exact global axisymmetric cell counts currently require a single edge; "
            "multi-edge paths use their per-edge mesh controls."
        )
    return axial_raw, radial_raw


def _axisymmetric_exact_edge_cell_controls(
    project: dict[str, Any],
    ordered_edges: list[dict[str, Any]],
) -> tuple[dict[str, int], int] | None:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    raw = controls.get("axisymmetricAxialCellsByEdge")
    if raw is None:
        return None
    radial = controls.get("axisymmetricRadialCells")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("axisymmetricAxialCellsByEdge must be a non-empty edge-to-cell-count object.")
    if not isinstance(radial, int) or isinstance(radial, bool) or radial < 2:
        raise ValueError(
            "Multi-edge exact axisymmetric controls require axisymmetricRadialCells >= 2."
        )
    edge_ids = [str(edge.get("id") or "") for edge in ordered_edges]
    if set(raw) != set(edge_ids):
        raise ValueError(
            "axisymmetricAxialCellsByEdge must contain exactly every ordered path edge."
        )
    parsed: dict[str, int] = {}
    for edge_id in edge_ids:
        value = raw.get(edge_id)
        if not isinstance(value, int) or isinstance(value, bool) or value < 4:
            raise ValueError(
                f"Exact axisymmetric axial cells for `{edge_id}` must be an integer >= 4."
            )
        parsed[edge_id] = value
    return parsed, radial


def _axisymmetric_qualification_request(
    project: dict[str, Any],
    ordered_edges: list[dict[str, Any]],
    exact_edge_cells: tuple[dict[str, int], int] | None,
) -> dict[str, Any] | None:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    raw = solver.get("axisymmetricQualification")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("axisymmetricQualification must be an object.")
    if raw.get("contractId") != AXISYMMETRIC_QUALIFICATION_CONTRACT_ID:
        raise ValueError("Axisymmetric qualification request has an unsupported contractId.")
    contract_sha = raw.get("contractSha256")
    if not isinstance(contract_sha, str) or re.fullmatch(r"[a-f0-9]{64}", contract_sha) is None:
        raise ValueError("Axisymmetric qualification requires the frozen contract SHA-256.")
    if raw.get("qoiHistoryWriteIntervalIterations") != 1:
        raise ValueError("Axisymmetric qualification requires QoI history every SIMPLE iteration.")
    if exact_edge_cells is None:
        raise ValueError("Axisymmetric qualification requires exact per-edge and radial cell controls.")
    if len(ordered_edges) < 3:
        raise ValueError("Axisymmetric qualification runtime requests require at least three path edges.")
    return {
        "schema": AXISYMMETRIC_QUALIFICATION_SCHEMA,
        "contractId": AXISYMMETRIC_QUALIFICATION_CONTRACT_ID,
        "contractSha256": contract_sha,
        "caseId": str(raw.get("caseId") or ""),
        "status": "prospective-experimental-software-qualification",
        "qoiHistoryWriteIntervalIterations": 1,
        "validated": False,
        "promotionAuthorized": False,
    }


def _axisymmetric_benchmark_request(
    project: dict[str, Any],
    ordered_edges: list[dict[str, Any]],
    exact_cells: tuple[int, int] | None,
) -> dict[str, Any] | None:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    raw = solver.get("axisymmetricBenchmark")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("axisymmetricBenchmark must be an object.")
    if raw.get("fixtureId") != "straight-pipe":
        raise ValueError("Axisymmetric benchmark mode currently supports only the straight-pipe fixture.")
    if raw.get("boundaryCondition") != "periodic-pressure-gradient":
        raise ValueError("The straight-pipe axisymmetric benchmark requires periodic-pressure-gradient flow control.")
    if str(solver.get("runMode") or "") != "steady" or str(solver.get("turbulence") or "") != "laminar":
        raise ValueError("The straight-pipe axisymmetric benchmark requires a steady laminar solver.")
    if exact_cells is None:
        raise ValueError("The straight-pipe axisymmetric benchmark requires exact axial and radial cell counts.")
    if len(ordered_edges) != 1:
        raise ValueError("The straight-pipe axisymmetric benchmark requires exactly one edge.")
    edge = ordered_edges[0]
    shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
    inlet_diameter = _axisymmetric_positive_number(shape.get("diameter"), "straight-pipe diameter")
    outlet_diameter = _axisymmetric_positive_number(edge.get("outletDiameter", inlet_diameter), "straight-pipe outlet diameter")
    if edge.get("type") != "pipe" or shape.get("kind") != "circular" or not math.isclose(
        inlet_diameter,
        outlet_diameter,
        rel_tol=1.0e-12,
        abs_tol=1.0e-15,
    ):
        raise ValueError("The straight-pipe axisymmetric benchmark requires one constant-diameter circular pipe edge.")
    return {
        "fixtureId": "straight-pipe",
        "boundaryCondition": "periodic-pressure-gradient",
        "lengthM": _axisymmetric_positive_number(raw.get("lengthM"), "straight-pipe benchmark lengthM"),
        "volumetricFlowRateM3PerS": _axisymmetric_positive_number(
            raw.get("volumetricFlowRateM3PerS"),
            "straight-pipe benchmark volumetricFlowRateM3PerS",
        ),
    }


def _axisymmetric_cell_distribution(total: int, lengths: list[float]) -> list[int]:
    if total < len(lengths):
        raise ValueError(
            f"Axisymmetric mesh needs at least one axial cell per profile segment; got {total} cells for {len(lengths)} segments."
        )
    length_total = sum(lengths)
    if length_total <= 0:
        raise ValueError("Axisymmetric profile segments must have positive physical lengths.")
    remaining = total - len(lengths)
    exact = [remaining * length / length_total for length in lengths]
    extra = [int(math.floor(value)) for value in exact]
    for index in sorted(range(len(lengths)), key=lambda item: (exact[item] - extra[item], -item), reverse=True)[
        : remaining - sum(extra)
    ]:
        extra[index] += 1
    return [1 + value for value in extra]


def _axisymmetric_ordered_path(project: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    nodes = {str(node.get("id")): node for node in _project_nodes(project) if str(node.get("id") or "").strip()}
    edges = _project_edges(project)
    if not nodes or not edges:
        raise ValueError("Axisymmetric mesh mode requires one connected source-to-sink circular path.")

    incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    edge_ids: set[str] = set()
    for edge in edges:
        edge_id = str(edge.get("id") or "").strip()
        from_id = str(edge.get("from") or "")
        to_id = str(edge.get("to") or "")
        if not edge_id or edge_id in edge_ids:
            raise ValueError("Axisymmetric mesh mode requires unique non-empty edge IDs.")
        if from_id not in nodes or to_id not in nodes:
            raise ValueError(f"Axisymmetric edge `{edge_id}` is missing a valid endpoint node.")
        edge_ids.add(edge_id)
        outgoing[from_id].append(edge)
        incoming[to_id].append(edge)

    involved = {node_id for node_id in nodes if incoming[node_id] or outgoing[node_id]}
    starts = [node_id for node_id in involved if not incoming[node_id] and len(outgoing[node_id]) == 1]
    ends = [node_id for node_id in involved if len(incoming[node_id]) == 1 and not outgoing[node_id]]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Axisymmetric mesh mode requires exactly one non-branching source-to-sink path.")
    if nodes[starts[0]].get("type") != "source" or nodes[ends[0]].get("type") != "sink":
        raise ValueError("Axisymmetric path must begin at a source node and end at a sink node.")
    for node_id in involved - {starts[0], ends[0]}:
        if len(incoming[node_id]) != 1 or len(outgoing[node_id]) != 1:
            raise ValueError("Axisymmetric mesh mode does not support branches, merges, or disconnected components.")

    ordered: list[dict[str, Any]] = []
    current = starts[0]
    visited: set[str] = set()
    while current != ends[0]:
        candidates = outgoing[current]
        if len(candidates) != 1:
            raise ValueError("Axisymmetric mesh mode requires a single ordered edge path.")
        edge = candidates[0]
        edge_id = str(edge.get("id"))
        if edge_id in visited:
            raise ValueError("Axisymmetric mesh mode does not support cycles.")
        visited.add(edge_id)
        ordered.append(edge)
        current = str(edge.get("to"))
    if len(ordered) != len(edges):
        raise ValueError("Axisymmetric mesh mode does not support disconnected edge components.")

    path_node_ids = [str(ordered[0].get("from")), *(str(edge.get("to")) for edge in ordered)]
    x0, y0 = _node_position(nodes[path_node_ids[0]])
    x1, y1 = _node_position(nodes[path_node_ids[-1]])
    dx, dy = x1 - x0, y1 - y0
    span = math.hypot(dx, dy)
    if span <= 0:
        raise ValueError("Axisymmetric path endpoints must have distinct editor positions.")
    previous_projection = -math.inf
    for node_id in path_node_ids:
        x, y = _node_position(nodes[node_id])
        cross_distance = abs((x - x0) * dy - (y - y0) * dx) / span
        projection = ((x - x0) * dx + (y - y0) * dy) / span
        if cross_distance > max(1.0e-6, span * 1.0e-6) or projection + 1.0e-9 < previous_projection:
            raise ValueError("Axisymmetric mesh mode requires a straight, collinear, consistently ordered path.")
        previous_projection = projection
    return ordered, nodes


def _openfoam_axisymmetric_profile(
    project: dict[str, Any],
    advanced_mode: str,
    mesh: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compile the requested graph into one fail-closed SI axisymmetric profile."""
    if not _openfoam_axisymmetric_mode_requested(project):
        return None
    if advanced_mode != "incompressible-navier-stokes":
        raise ValueError("Axisymmetric mesh mode currently supports only incompressible Navier-Stokes.")
    if not isinstance(mesh, dict):
        raise ValueError("Axisymmetric mesh mode requires a successfully generated source mesh.")

    ordered_edges, nodes_by_id = _axisymmetric_ordered_path(project)
    exact_cells = _axisymmetric_exact_cell_controls(project, len(ordered_edges))
    exact_edge_cells = _axisymmetric_exact_edge_cell_controls(project, ordered_edges)
    benchmark_request = _axisymmetric_benchmark_request(project, ordered_edges, exact_cells)
    qualification_request = _axisymmetric_qualification_request(
        project,
        ordered_edges,
        exact_edge_cells,
    )
    regions = mesh.get("regions") if isinstance(mesh.get("regions"), list) else []
    region_by_edge = {
        str(region.get("edgeId")): region
        for region in regions
        if isinstance(region, dict) and region.get("edgeType") != "connector"
    }

    stations: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    sampling_surfaces: list[dict[str, Any]] = []
    radial_counts: set[int] = set()
    cumulative_x = 0.0
    previous_outlet_diameter: float | None = None

    def add_station(x_m: float, diameter_m: float, feature: str, edge_id: str) -> int:
        radius_m = diameter_m / 2.0
        if stations and math.isclose(float(stations[-1]["xM"]), x_m, rel_tol=0.0, abs_tol=1.0e-12):
            if not math.isclose(float(stations[-1]["radiusM"]), radius_m, rel_tol=1.0e-6, abs_tol=1.0e-9):
                raise ValueError(f"Axisymmetric path has a diameter discontinuity at edge `{edge_id}`.")
            stations[-1]["features"] = sorted({*stations[-1]["features"], feature})
            stations[-1]["edgeIds"] = [*stations[-1]["edgeIds"], edge_id]
            return len(stations) - 1
        stations.append(
            {
                "index": len(stations),
                "xM": round(x_m, 12),
                "radiusM": round(radius_m, 12),
                "features": [feature],
                "edgeIds": [edge_id],
            }
        )
        return len(stations) - 1

    for edge_index, edge in enumerate(ordered_edges):
        edge_id = str(edge.get("id"))
        edge_type = str(edge.get("type") or "")
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        if edge_type not in AXISYMMETRIC_ALLOWED_EDGE_TYPES:
            raise ValueError(
                "Axisymmetric mesh mode supports only straight circular pipe, venturi, expansion, contraction, and nozzle edges; "
                f"`{edge_id}` is `{edge_type or 'unknown'}`."
            )
        if shape.get("kind") != "circular":
            raise ValueError(f"Axisymmetric edge `{edge_id}` must have a circular section.")
        inlet_diameter = _axisymmetric_positive_number(shape.get("diameter"), f"diameter for edge `{edge_id}`")
        outlet_diameter = _axisymmetric_positive_number(
            edge.get("outletDiameter", inlet_diameter),
            f"outletDiameter for edge `{edge_id}`",
        )
        if edge_type == "expansion" and outlet_diameter <= inlet_diameter:
            raise ValueError(f"Axisymmetric expansion edge `{edge_id}` requires outletDiameter greater than its inlet diameter.")
        if edge_type in {"contraction", "nozzle"} and outlet_diameter >= inlet_diameter:
            raise ValueError(f"Axisymmetric {edge_type} edge `{edge_id}` requires outletDiameter smaller than its inlet diameter.")
        if previous_outlet_diameter is not None and not math.isclose(
            previous_outlet_diameter,
            inlet_diameter,
            rel_tol=1.0e-6,
            abs_tol=1.0e-9,
        ):
            raise ValueError(f"Axisymmetric path has a diameter discontinuity before edge `{edge_id}`.")

        length_m = (
            float(benchmark_request["lengthM"])
            if benchmark_request is not None
            else _edge_effective_length(edge, nodes_by_id)
        )
        region = region_by_edge.get(edge_id)
        if not isinstance(region, dict):
            raise ValueError(f"Axisymmetric mesh source is missing region metadata for edge `{edge_id}`.")
        try:
            edge_axial_cells = (
                exact_cells[0]
                if exact_cells is not None
                else exact_edge_cells[0][edge_id]
                if exact_edge_cells is not None
                else int(region.get("segmentCount"))
            )
            edge_radial_cells = (
                exact_cells[1]
                if exact_cells is not None
                else exact_edge_cells[1]
                if exact_edge_cells is not None
                else int(region.get("transverseDivisions"))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Axisymmetric mesh source has invalid cell controls for edge `{edge_id}`.") from exc
        if edge_axial_cells < 1 or edge_radial_cells < 1:
            raise ValueError(f"Axisymmetric mesh controls must be positive for edge `{edge_id}`.")
        radial_counts.add(edge_radial_cells)

        local_profile: list[tuple[float, float, str]] = [(0.0, inlet_diameter, "inlet" if edge_index == 0 else "junction")]
        if edge_type == "venturi":
            throat_diameter = _axisymmetric_positive_number(
                edge.get("throatDiameter"),
                f"throatDiameter for Venturi edge `{edge_id}`",
            )
            if throat_diameter >= min(inlet_diameter, outlet_diameter):
                raise ValueError(f"Venturi edge `{edge_id}` throatDiameter must be smaller than its inlet and outlet diameters.")
            throat_position = float(edge.get("throatPosition", 0.5))
            if not math.isfinite(throat_position) or not 0.0 < throat_position < 1.0:
                raise ValueError(f"Venturi edge `{edge_id}` throatPosition must be between 0 and 1.")
            throat_length = _axisymmetric_nonnegative_number(
                edge.get("throatLength", 0.0),
                f"throatLength for Venturi edge `{edge_id}`",
            )
            throat_center = throat_position * length_m
            throat_start = throat_center - throat_length / 2.0
            throat_end = throat_center + throat_length / 2.0
            if throat_start <= 0 or throat_end >= length_m:
                raise ValueError(f"Venturi edge `{edge_id}` throat must remain inside its physical edge length.")
            local_profile.append((throat_start, throat_diameter, "throat"))
            if throat_length > 1.0e-12:
                local_profile.append((throat_end, throat_diameter, "throat"))
            sampling_surfaces.append(
                {
                    "name": f"throat_{edge_id}",
                    "role": "internal-sampling-plane",
                    "xM": round(cumulative_x + throat_center, 12),
                    "edgeId": edge_id,
                }
            )
        local_profile.append((length_m, outlet_diameter, "outlet" if edge_index == len(ordered_edges) - 1 else "junction"))

        local_lengths = [local_profile[index + 1][0] - local_profile[index][0] for index in range(len(local_profile) - 1)]
        cell_counts = _axisymmetric_cell_distribution(edge_axial_cells, local_lengths)
        station_indices = [
            add_station(cumulative_x + local_x, diameter, feature, edge_id)
            for local_x, diameter, feature in local_profile
        ]
        for segment_index, cells in enumerate(cell_counts):
            start_index = station_indices[segment_index]
            end_index = station_indices[segment_index + 1]
            segments.append(
                {
                    "index": len(segments),
                    "edgeId": edge_id,
                    "fromStation": start_index,
                    "toStation": end_index,
                    "nAxial": cells,
                }
            )
        cumulative_x += length_m
        previous_outlet_diameter = outlet_diameter

    if len(radial_counts) != 1:
        raise ValueError("Axisymmetric multi-edge paths require one consistent radial cell count.")
    n_radial = next(iter(radial_counts))
    profile = {
        "schema": AXISYMMETRIC_PROFILE_SCHEMA,
        "requestedMeshMode": "axisymmetric",
        "effectiveMeshMode": "axisymmetric-wedge",
        "coordinateSystem": "axisymmetric-x-r",
        "units": {"length": "m", "angle": "deg"},
        "wedge": {"halfAngleDeg": AXISYMMETRIC_WEDGE_HALF_ANGLE_DEG, "totalAngleDeg": 2.0 * AXISYMMETRIC_WEDGE_HALF_ANGLE_DEG},
        "pathEdgeIds": [str(edge.get("id")) for edge in ordered_edges],
        "totalLengthM": round(cumulative_x, 12),
        "nRadial": n_radial,
        "stations": stations,
        "segments": segments,
        "samplingSurfaces": sampling_surfaces,
        "boundaryRoles": {
            "inlet": "cyclic" if benchmark_request is not None else "patch",
            "outlet": "cyclic" if benchmark_request is not None else "patch",
            "walls": "wall",
            "front": "wedge",
            "back": "wedge",
            "axis": "empty",
        },
    }
    if benchmark_request is not None:
        conditions = _case_conditions(project)
        radius_m = float(stations[0]["radiusM"])
        angle_rad = math.radians(float(profile["wedge"]["totalAngleDeg"]))
        # The blockMesh wedge cross-section is the triangle bounded by the
        # collapsed axis and the chord joining the two wall vertices. Its exact
        # area is R^2*tan(halfAngle), not the circular-sector area.
        wedge_area_m2 = radius_m * radius_m * math.tan(angle_rad / 2.0)
        full_circle_scale = 2.0 * math.pi / angle_rad
        full_equivalent_area_m2 = wedge_area_m2 * full_circle_scale
        circular_area_m2 = math.pi * radius_m * radius_m
        flow_rate = float(benchmark_request["volumetricFlowRateM3PerS"])
        mean_velocity = flow_rate / full_equivalent_area_m2
        reynolds_number = (
            conditions.density
            * (flow_rate / circular_area_m2)
            * (2.0 * radius_m)
            / conditions.dynamic_viscosity
        )
        profile["benchmarkContract"] = {
            "schema": AXISYMMETRIC_BENCHMARK_SCHEMA,
            "fixtureId": "straight-pipe",
            "fixtureStatus": "pending-real-run",
            "scientificStatus": "experimental-candidate",
            "boundaryCondition": "periodic-pressure-gradient",
            "physicalLengthM": float(benchmark_request["lengthM"]),
            "targetFullCircleVolumetricFlowRateM3PerS": flow_rate,
            "fullCircleScale": full_circle_scale,
            "wedgeCrossSectionAreaM2": wedge_area_m2,
            "wedgeCrossSectionAreaMethod": "blockMesh triangle R^2*tan(halfAngle)",
            "fullEquivalentMeshAreaM2": full_equivalent_area_m2,
            "analyticCircularAreaM2": circular_area_m2,
            "meanVelocityTargetMPerS": mean_velocity,
            "densityKgPerM3": conditions.density,
            "dynamicViscosityPaS": conditions.dynamic_viscosity,
            "reynoldsNumber": reynolds_number,
            "pressureFieldUnits": "m^2/s^2",
            "pressureToPascalMultiplier": conditions.density,
            "qoiExtraction": {
                "pressureDrop": "final meanVelocityForce kinematic pressure gradient times physicalLengthM and densityKgPerM3",
                "volumetricFlowRate": "signed wedge patch phi times fullCircleScale",
                "massFlowRate": "signed full-circle volumetric flow rate times densityKgPerM3",
            },
        }
    if qualification_request is not None:
        profile["experimentalQualificationContract"] = qualification_request
    return profile


FULL_OGRID_LEVELS: dict[str, tuple[int, int, int, int]] = {
    "coarse": (16, 4, 32, 8),
    "medium": (32, 8, 64, 16),
    "fine": (64, 16, 128, 32),
}
FULL_OGRID_QUALIFICATION_SCHEMA = (
    "flowlab.full-ogrid-geometry-experimental-qualification-request.v1"
)
FULL_OGRID_QUALIFICATION_CONTRACT_ID = (
    "full-ogrid-generated-geometry-experimental-qualification-v3"
)

CURVED_ELBOW_LEVELS: dict[str, tuple[int, int, int, int, int, int]] = {
    "coarse": (28, 16, 28, 2, 16, 4),
    "medium": (56, 32, 56, 4, 32, 8),
    "fine": (112, 64, 112, 8, 64, 16),
}


def _openfoam_full_ogrid_mode_requested(project: dict[str, Any] | None) -> bool:
    solver = project.get("solver") if isinstance(project, dict) and isinstance(project.get("solver"), dict) else {}
    return str(solver.get("meshMode", "planar-2d")).strip().lower() == "full-ogrid"


def _openfoam_curved_elbow_mode_requested(project: dict[str, Any] | None) -> bool:
    solver = project.get("solver") if isinstance(project, dict) and isinstance(project.get("solver"), dict) else {}
    return str(solver.get("meshMode", "planar-2d")).strip().lower() == "curved-elbow-ogrid"


def _full_ogrid_positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Full O-grid geometry requires a numeric {label}.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Full O-grid geometry requires a positive {label}.")
    return number


def _full_ogrid_cell_controls(project: dict[str, Any]) -> tuple[int, int, int, int]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    keys = (
        "fullOGridAxialCells",
        "fullOGridAnnularRadialCells",
        "fullOGridCircumferentialCells",
        "fullOGridCoreCellsPerSide",
    )
    supplied = [controls.get(key) is not None for key in keys]
    if any(supplied) and not all(supplied):
        raise ValueError(
            "Exact full O-grid axial, annular-radial, circumferential, and core cell counts "
            "must be supplied together."
        )
    if all(supplied):
        values = tuple(controls[key] for key in keys)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise ValueError("Exact full O-grid cell controls must be integers.")
        return values  # type: ignore[return-value]
    resolution = str(solver.get("meshResolution", "coarse")).strip().lower()
    try:
        return FULL_OGRID_LEVELS[resolution]
    except KeyError as exc:
        raise ValueError("Full O-grid mesh resolution must be coarse, medium, or fine.") from exc


def _curved_elbow_cell_controls(
    project: dict[str, Any],
) -> tuple[int, int, int, int, int, int]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    keys = (
        "curvedElbowInletAxialCells",
        "curvedElbowBendAxialCells",
        "curvedElbowOutletAxialCells",
        "curvedElbowAnnularRadialCells",
        "curvedElbowCircumferentialCells",
        "curvedElbowCoreCellsPerSide",
    )
    supplied = [controls.get(key) is not None for key in keys]
    if any(supplied) and not all(supplied):
        raise ValueError(
            "Exact curved-elbow inlet, bend, outlet, annular-radial, "
            "circumferential, and core cell counts must be supplied together."
        )
    if all(supplied):
        values = tuple(controls[key] for key in keys)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise ValueError("Exact curved-elbow cell controls must be integers.")
        return values  # type: ignore[return-value]
    resolution = str(solver.get("meshResolution", "coarse")).strip().lower()
    try:
        return CURVED_ELBOW_LEVELS[resolution]
    except KeyError as exc:
        raise ValueError("Curved-elbow mesh resolution must be coarse, medium, or fine.") from exc


def _full_ogrid_verification_request(
    project: dict[str, Any],
    *,
    length_m: float,
    radius_m: float,
    spec: FullOGridSpec,
) -> dict[str, Any] | None:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    raw = solver.get("fullOGridVerification")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("fullOGridVerification must be an object.")
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    exact_keys = (
        "fullOGridAxialCells",
        "fullOGridAnnularRadialCells",
        "fullOGridCircumferentialCells",
        "fullOGridCoreCellsPerSide",
    )
    if any(controls.get(key) is None for key in exact_keys):
        raise ValueError(
            "The full O-grid verification contract requires exact axial, annular-radial, "
            "circumferential, and core cell counts."
        )
    contract_id = raw.get("contractId")
    supported_contract_ids = {
        "straight-circular-pipe-hagen-poiseuille-v1",
        "straight-circular-pipe-hagen-poiseuille-v2",
    }
    if contract_id not in supported_contract_ids:
        raise ValueError("The full O-grid verification request has an unsupported contractId.")
    boundary = "fully-developed-parabolic-inlet-pressure-outlet"
    if raw.get("boundaryCondition") != boundary:
        raise ValueError(
            "The full O-grid verification contract requires a fully-developed parabolic inlet "
            "and pressure outlet."
        )
    requested_length = _full_ogrid_positive_number(
        raw.get("lengthM"),
        "full O-grid verification lengthM",
    )
    if not math.isclose(requested_length, length_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise ValueError(
            "The full O-grid verification lengthM must equal the editor pipe's physical SI length."
        )
    flow_rate = _full_ogrid_positive_number(
        raw.get("volumetricFlowRateM3PerS"),
        "full O-grid verification volumetricFlowRateM3PerS",
    )
    conditions = _case_conditions(project)
    analytic_area = math.pi * radius_m**2
    mean_velocity = flow_rate / analytic_area
    verification = {
        "schema": FULL_OGRID_VERIFICATION_SCHEMA,
        "contractId": contract_id,
        "status": "prospective-request-not-validation",
        "boundaryCondition": boundary,
        "physicalLengthM": length_m,
        "radiusM": radius_m,
        "targetVolumetricFlowRateM3PerS": flow_rate,
        "analyticCircularAreaM2": analytic_area,
        "meanVelocityTargetMPerS": mean_velocity,
        "centerlineVelocityTargetMPerS": 2.0 * mean_velocity,
        "densityKgPerM3": conditions.density,
        "dynamicViscosityPaS": conditions.dynamic_viscosity,
        "reynoldsNumber": conditions.density * mean_velocity * (2.0 * radius_m) / conditions.dynamic_viscosity,
        "pressureFieldUnits": "m^2/s^2",
        "pressureToPascalMultiplier": conditions.density,
        "resolution": spec.topology_manifest()["resolution"],
        "qoiExtraction": {
            "pressureDrop": "patchAverage(p,inlet) minus patchAverage(p,outlet), multiplied by densityKgPerM3",
            "volumetricFlowRate": "signed surface sum(phi) on inlet and outlet",
            "velocityProfile": "solver-space XYZ samples compared with 2*meanVelocity*(1-r^2/R^2)",
        },
    }
    if contract_id == "straight-circular-pipe-hagen-poiseuille-v2":
        history_interval = raw.get("qoiHistoryWriteIntervalIterations")
        if history_interval != 1:
            raise ValueError(
                "The v2 full O-grid verification contract requires "
                "qoiHistoryWriteIntervalIterations=1."
            )
        verification["qoiHistoryWriteIntervalIterations"] = history_interval
    return verification


def _full_ogrid_path_cell_controls(
    project: dict[str, Any],
    ordered_edges: list[dict[str, Any]],
) -> tuple[dict[str, int], int, int, int]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = (
        solver.get("meshControls")
        if isinstance(solver.get("meshControls"), dict)
        else {}
    )
    axial_raw = controls.get("fullOGridAxialCellsByEdge")
    if not isinstance(axial_raw, dict) or not axial_raw:
        raise ValueError(
            "Full O-grid geometry qualification requires "
            "fullOGridAxialCellsByEdge."
        )
    edge_ids = [str(edge.get("id") or "") for edge in ordered_edges]
    if set(axial_raw) != set(edge_ids):
        raise ValueError(
            "fullOGridAxialCellsByEdge must contain exactly every ordered path edge."
        )
    axial_by_edge: dict[str, int] = {}
    for edge_id in edge_ids:
        value = axial_raw.get(edge_id)
        if not isinstance(value, int) or isinstance(value, bool) or value < 4:
            raise ValueError(
                f"Exact full O-grid axial cells for `{edge_id}` must be an integer >= 4."
            )
        axial_by_edge[edge_id] = value
    cross_keys = (
        "fullOGridAnnularRadialCells",
        "fullOGridCircumferentialCells",
        "fullOGridCoreCellsPerSide",
    )
    values = [controls.get(key) for key in cross_keys]
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in values
    ):
        raise ValueError(
            "Full O-grid geometry qualification requires exact annular-radial, "
            "circumferential, and core-side integer controls."
        )
    return (
        axial_by_edge,
        int(values[0]),
        int(values[1]),
        int(values[2]),
    )


def _full_ogrid_qualification_request(
    project: dict[str, Any],
    *,
    ordered_edges: list[dict[str, Any]],
    controls: tuple[dict[str, int], int, int, int],
    inlet_radius_m: float,
) -> dict[str, Any]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    raw = solver.get("fullOGridQualification")
    if not isinstance(raw, dict):
        raise ValueError("fullOGridQualification must be an object.")
    if raw.get("contractId") != FULL_OGRID_QUALIFICATION_CONTRACT_ID:
        raise ValueError(
            "Full O-grid geometry qualification has an unsupported contractId."
        )
    contract_sha = raw.get("contractSha256")
    if (
        not isinstance(contract_sha, str)
        or re.fullmatch(r"[a-f0-9]{64}", contract_sha) is None
    ):
        raise ValueError(
            "Full O-grid geometry qualification requires the frozen contract SHA-256."
        )
    if raw.get("qoiHistoryWriteIntervalIterations") != 1:
        raise ValueError(
            "Full O-grid geometry qualification requires QoI history every SIMPLE iteration."
        )
    flow_rate = _full_ogrid_positive_number(
        raw.get("volumetricFlowRateM3PerS"),
        "full O-grid qualification volumetricFlowRateM3PerS",
    )
    conditions = _case_conditions(project)
    area = math.pi * inlet_radius_m**2
    mean_velocity = flow_rate / area
    axial_by_edge, annular, circumference, core = controls
    return {
        "schema": FULL_OGRID_QUALIFICATION_SCHEMA,
        "contractId": FULL_OGRID_QUALIFICATION_CONTRACT_ID,
        "contractSha256": contract_sha,
        "caseId": str(raw.get("caseId") or ""),
        "status": "prospective-experimental-software-geometry-qualification",
        "qoiHistoryWriteIntervalIterations": 1,
        "targetVolumetricFlowRateM3PerS": flow_rate,
        "analyticCircularAreaM2": area,
        "meanVelocityTargetMPerS": mean_velocity,
        "centerlineVelocityTargetMPerS": 2.0 * mean_velocity,
        "inletRadiusM": inlet_radius_m,
        "densityKgPerM3": conditions.density,
        "dynamicViscosityPaS": conditions.dynamic_viscosity,
        "resolution": {
            "axialCellsByEdge": axial_by_edge,
            "annularRadialCells": annular,
            "circumferentialCells": circumference,
            "coreCellsPerSide": core,
        },
        "validated": False,
        "promotionAuthorized": False,
    }


def _openfoam_full_ogrid_path_profile(
    project: dict[str, Any],
) -> dict[str, Any]:
    ordered_edges, nodes_by_id = _axisymmetric_ordered_path(project)
    controls = _full_ogrid_path_cell_controls(project, ordered_edges)
    axial_by_edge, annular, circumference, core = controls
    stations: list[dict[str, Any]] = []
    compiled_segments: list[FullOGridPathSegment] = []
    profile_segments: list[dict[str, Any]] = []
    cumulative_x = 0.0
    previous_outlet_diameter: float | None = None

    def add_station(
        x_m: float,
        radius_m: float,
        feature: str,
        edge_id: str,
    ) -> int:
        if stations and math.isclose(
            float(stations[-1]["xM"]),
            x_m,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            if not math.isclose(
                float(stations[-1]["radiusM"]),
                radius_m,
                rel_tol=1.0e-9,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    f"Full O-grid path has a diameter discontinuity at edge `{edge_id}`."
                )
            stations[-1]["features"] = sorted(
                {*stations[-1]["features"], feature}
            )
            stations[-1]["edgeIds"] = [*stations[-1]["edgeIds"], edge_id]
            return len(stations) - 1
        stations.append(
            {
                "index": len(stations),
                "xM": round(x_m, 12),
                "radiusM": round(radius_m, 12),
                "features": [feature],
                "edgeIds": [edge_id],
            }
        )
        return len(stations) - 1

    for edge_index, edge in enumerate(ordered_edges):
        edge_id = str(edge.get("id") or "")
        edge_type = str(edge.get("type") or "")
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        if edge_type not in AXISYMMETRIC_ALLOWED_EDGE_TYPES:
            raise ValueError(
                "Full O-grid geometry qualification supports only straight circular "
                "pipe, venturi, expansion, contraction, and nozzle edges."
            )
        if shape.get("kind") != "circular":
            raise ValueError(f"Full O-grid edge `{edge_id}` must be circular.")
        inlet_diameter = _full_ogrid_positive_number(
            shape.get("diameter"),
            f"diameter for edge `{edge_id}`",
        )
        outlet_diameter = _full_ogrid_positive_number(
            edge.get("outletDiameter", inlet_diameter),
            f"outletDiameter for edge `{edge_id}`",
        )
        if edge_type == "expansion" and outlet_diameter <= inlet_diameter:
            raise ValueError(
                f"Full O-grid expansion `{edge_id}` requires a larger outlet."
            )
        if (
            edge_type in {"contraction", "nozzle"}
            and outlet_diameter >= inlet_diameter
        ):
            raise ValueError(
                f"Full O-grid {edge_type} `{edge_id}` requires a smaller outlet."
            )
        if previous_outlet_diameter is not None and not math.isclose(
            previous_outlet_diameter,
            inlet_diameter,
            rel_tol=1.0e-9,
            abs_tol=1.0e-12,
        ):
            raise ValueError(
                f"Full O-grid path has a diameter discontinuity before `{edge_id}`."
            )
        length_m = _full_ogrid_positive_number(
            edge.get("length"),
            f"physical length for edge `{edge_id}`",
        )
        local_profile: list[tuple[float, float, str]] = [
            (
                0.0,
                inlet_diameter,
                "inlet" if edge_index == 0 else "junction",
            )
        ]
        if edge_type == "venturi":
            throat_diameter = _full_ogrid_positive_number(
                edge.get("throatDiameter"),
                f"throatDiameter for Venturi `{edge_id}`",
            )
            if throat_diameter >= min(inlet_diameter, outlet_diameter):
                raise ValueError(
                    f"Full O-grid Venturi `{edge_id}` requires a smaller throat."
                )
            throat_position = float(edge.get("throatPosition", 0.5))
            if not math.isfinite(throat_position) or not 0.0 < throat_position < 1.0:
                raise ValueError(
                    f"Full O-grid Venturi `{edge_id}` throatPosition must be inside the edge."
                )
            throat_length = _axisymmetric_nonnegative_number(
                edge.get("throatLength", 0.0),
                f"throatLength for Venturi `{edge_id}`",
            )
            throat_center = throat_position * length_m
            throat_start = throat_center - throat_length / 2.0
            throat_end = throat_center + throat_length / 2.0
            if throat_start <= 0.0 or throat_end >= length_m:
                raise ValueError(
                    f"Full O-grid Venturi `{edge_id}` throat must remain inside its edge."
                )
            local_profile.append((throat_start, throat_diameter, "throat"))
            if throat_length > 1.0e-12:
                local_profile.append((throat_end, throat_diameter, "throat"))
        local_profile.append(
            (
                length_m,
                outlet_diameter,
                "outlet"
                if edge_index == len(ordered_edges) - 1
                else "junction",
            )
        )
        local_lengths = [
            local_profile[index + 1][0] - local_profile[index][0]
            for index in range(len(local_profile) - 1)
        ]
        cell_counts = _axisymmetric_cell_distribution(
            axial_by_edge[edge_id],
            local_lengths,
        )
        station_indices = [
            add_station(
                cumulative_x + local_x,
                diameter / 2.0,
                feature,
                edge_id,
            )
            for local_x, diameter, feature in local_profile
        ]
        for segment_index, axial_cells in enumerate(cell_counts):
            from_station = station_indices[segment_index]
            to_station = station_indices[segment_index + 1]
            start = stations[from_station]
            end = stations[to_station]
            segment = FullOGridPathSegment(
                edge_id=edge_id,
                edge_type=edge_type,
                length_m=float(end["xM"]) - float(start["xM"]),
                inlet_radius_m=float(start["radiusM"]),
                outlet_radius_m=float(end["radiusM"]),
                axial_cells=axial_cells,
            )
            compiled_segments.append(segment)
            profile_segments.append(
                {
                    "index": len(profile_segments),
                    "edgeId": edge_id,
                    "edgeType": edge_type,
                    "fromStation": from_station,
                    "toStation": to_station,
                    "nAxial": axial_cells,
                }
            )
        cumulative_x += length_m
        previous_outlet_diameter = outlet_diameter

    spec = FullOGridPathSpec(
        segments=tuple(compiled_segments),
        annular_radial_cells=annular,
        circumferential_cells=circumference,
        core_cells_per_side=core,
    )
    qualification = _full_ogrid_qualification_request(
        project,
        ordered_edges=ordered_edges,
        controls=controls,
        inlet_radius_m=compiled_segments[0].inlet_radius_m,
    )
    return {
        "schema": FULL_OGRID_PATH_PROFILE_SCHEMA,
        "requestedMeshMode": "full-ogrid",
        "effectiveMeshMode": "full-revolution-multi-segment-five-block-ogrid",
        "coordinateSystem": "physical-x-y-z-si",
        "units": {"length": "m", "angle": "deg"},
        "pathEdgeIds": [str(edge.get("id") or "") for edge in ordered_edges],
        "sourceNodeId": str(ordered_edges[0].get("from") or ""),
        "sinkNodeId": str(ordered_edges[-1].get("to") or ""),
        "totalLengthM": round(spec.total_length_m, 12),
        "inletRadiusM": compiled_segments[0].inlet_radius_m,
        "outletRadiusM": compiled_segments[-1].outlet_radius_m,
        "stations": stations,
        "segments": profile_segments,
        "topology": spec.topology_manifest(),
        "boundaryRoles": {
            "inlet": "patch",
            "outlet": "patch",
            "walls": "wall",
        },
        "qualificationContract": qualification,
        "scope": {
            "flow": "steady-incompressible-laminar",
            "geometry": "straight-axis-circular-multi-segment-path",
            "unsupported": [
                "elbows",
                "branches",
                "arbitrary-cad",
                "turbulence",
                "transient",
                "multiphase",
                "compressible",
            ],
        },
    }


def _openfoam_full_ogrid_profile(
    project: dict[str, Any],
    advanced_mode: str,
) -> dict[str, Any] | None:
    """Compile exactly one bounded editor pipe into a full-revolution O-grid."""

    if not _openfoam_full_ogrid_mode_requested(project):
        return None
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    if advanced_mode != "incompressible-navier-stokes":
        raise ValueError("Full O-grid mesh mode supports only incompressible Navier-Stokes.")
    if str(solver.get("runMode", "")).strip().lower() != "steady":
        raise ValueError("Full O-grid mesh mode requires a steady solver.")
    if str(solver.get("turbulence", "")).strip().lower() != "laminar":
        raise ValueError("Full O-grid mesh mode requires laminar flow.")
    if solver.get("fullOGridQualification") is not None:
        return _openfoam_full_ogrid_path_profile(project)

    nodes = _project_nodes(project)
    edges = _project_edges(project)
    if len(nodes) != 2 or len(edges) != 1:
        raise ValueError("Full O-grid mesh mode requires exactly one source, one sink, and one connecting pipe.")
    edge = edges[0]
    nodes_by_id = {str(node.get("id") or ""): node for node in nodes}
    if len(nodes_by_id) != 2 or "" in nodes_by_id:
        raise ValueError("Full O-grid editor nodes require unique non-empty IDs.")
    source_id = str(edge.get("from") or "")
    sink_id = str(edge.get("to") or "")
    source = nodes_by_id.get(source_id)
    sink = nodes_by_id.get(sink_id)
    if source is None or sink is None or source.get("type") != "source" or sink.get("type") != "sink":
        raise ValueError("Full O-grid pipe must connect one source directly to one sink.")
    if edge.get("type") != "pipe":
        raise ValueError("Full O-grid mesh mode currently supports only a straight pipe edge.")
    shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
    if shape.get("kind") != "circular":
        raise ValueError("Full O-grid pipe requires a circular section.")
    diameter_m = _full_ogrid_positive_number(shape.get("diameter"), "pipe diameter")
    outlet_diameter_m = _full_ogrid_positive_number(
        edge.get("outletDiameter", diameter_m),
        "pipe outlet diameter",
    )
    if not math.isclose(diameter_m, outlet_diameter_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise ValueError("Full O-grid pipe must have one constant diameter.")
    length_m = _full_ogrid_positive_number(edge.get("length"), "pipe length")
    start = _node_position(source)
    end = _node_position(sink)
    if math.isclose(start[0], end[0], abs_tol=1.0e-12) and math.isclose(start[1], end[1], abs_tol=1.0e-12):
        raise ValueError("Full O-grid source and sink require distinct editor positions.")

    axial, annular, circumference, core = _full_ogrid_cell_controls(project)
    spec = FullOGridSpec(
        length_m=length_m,
        radius_m=diameter_m / 2.0,
        axial_cells=axial,
        annular_radial_cells=annular,
        circumferential_cells=circumference,
        core_cells_per_side=core,
    )
    verification = _full_ogrid_verification_request(
        project,
        length_m=length_m,
        radius_m=diameter_m / 2.0,
        spec=spec,
    )
    profile: dict[str, Any] = {
        "schema": FULL_OGRID_PROFILE_SCHEMA,
        "requestedMeshMode": "full-ogrid",
        "effectiveMeshMode": "full-revolution-five-block-ogrid",
        "coordinateSystem": "physical-x-y-z-si",
        "units": {"length": "m", "angle": "deg"},
        "pathEdgeIds": [str(edge.get("id") or "")],
        "sourceNodeId": source_id,
        "sinkNodeId": sink_id,
        "editorLayout": {
            "source": [float(start[0]), float(start[1])],
            "sink": [float(end[0]), float(end[1])],
            "usedForPhysicalDimensions": False,
        },
        "totalLengthM": length_m,
        "radiusM": diameter_m / 2.0,
        "diameterM": diameter_m,
        "topology": spec.topology_manifest(),
        "boundaryRoles": {"inlet": "patch", "outlet": "patch", "walls": "wall"},
        "scope": {
            "flow": "steady-incompressible-laminar",
            "geometry": "one-straight-constant-diameter-circular-pipe",
            "unsupported": [
                "elbows",
                "branches",
                "arbitrary-cad",
                "venturi",
                "turbulence",
                "transient",
                "multiphase",
                "compressible",
            ],
        },
    }
    if verification is not None:
        profile["verificationContract"] = verification
    return profile


def _curved_elbow_verification_request(
    project: dict[str, Any],
    *,
    spec: CurvedElbowSpec,
) -> dict[str, Any]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    raw = solver.get("curvedElbowVerification")
    if not isinstance(raw, dict):
        raise ValueError(
            "Curved-elbow O-grid mode requires an explicit curvedElbowVerification object."
        )
    if raw.get("contractId") != "canonical-circular-elbow-re100-v2":
        raise ValueError("The curved-elbow verification request has an unsupported contractId.")
    boundary = "fully-developed-parabolic-inlet-pressure-outlet"
    if raw.get("boundaryCondition") != boundary:
        raise ValueError(
            "The curved-elbow verification request requires a fully-developed "
            "parabolic inlet and pressure outlet."
        )
    exact_geometry = {
        "diameterM": spec.diameter_m,
        "centrelineRadiusM": spec.centreline_radius_m,
        "inletLegLengthM": spec.inlet_leg_m,
        "outletLegLengthM": spec.outlet_leg_m,
        "bendAngleDegrees": spec.bend_angle_degrees,
    }
    for key, expected in exact_geometry.items():
        supplied = _full_ogrid_positive_number(raw.get(key), f"curved-elbow {key}")
        if not math.isclose(supplied, expected, rel_tol=1.0e-12, abs_tol=1.0e-15):
            raise ValueError(f"The curved-elbow verification {key} must match the bounded geometry.")
    flow_rate = _full_ogrid_positive_number(
        raw.get("volumetricFlowRateM3PerS"),
        "curved-elbow volumetricFlowRateM3PerS",
    )
    if raw.get("qoiHistoryWriteIntervalIterations") != 1:
        raise ValueError(
            "The curved-elbow verification request requires "
            "qoiHistoryWriteIntervalIterations=1."
        )
    conditions = _case_conditions(project)
    analytic_area = math.pi * spec.radius_m**2
    mean_velocity = flow_rate / analytic_area
    reynolds = (
        conditions.density
        * mean_velocity
        * spec.diameter_m
        / conditions.dynamic_viscosity
    )
    if not math.isclose(reynolds, 100.0, rel_tol=0.01, abs_tol=0.0):
        raise ValueError("The bounded curved-elbow verification request requires Reynolds number approximately 100.")
    return {
        "schema": CURVED_ELBOW_VERIFICATION_SCHEMA,
        "contractId": "canonical-circular-elbow-re100-v2",
        "status": "prospective-request-not-validation",
        "boundaryCondition": boundary,
        "diameterM": spec.diameter_m,
        "radiusM": spec.radius_m,
        "centrelineRadiusM": spec.centreline_radius_m,
        "centrelineRadiusOverDiameter": spec.centreline_radius_over_diameter,
        "inletLegLengthM": spec.inlet_leg_m,
        "outletLegLengthM": spec.outlet_leg_m,
        "bendAngleDegrees": spec.bend_angle_degrees,
        "targetVolumetricFlowRateM3PerS": flow_rate,
        "analyticCircularAreaM2": analytic_area,
        "meanVelocityTargetMPerS": mean_velocity,
        "centerlineVelocityTargetMPerS": 2.0 * mean_velocity,
        "densityKgPerM3": conditions.density,
        "dynamicViscosityPaS": conditions.dynamic_viscosity,
        "reynoldsNumber": reynolds,
        "pressureFieldUnits": "m^2/s^2",
        "pressureToPascalMultiplier": conditions.density,
        "qoiHistoryWriteIntervalIterations": 1,
        "resolution": spec.topology_manifest()["resolution"],
        "qoiExtraction": {
            "pressureLoss": "patchAverage(p,inlet) minus patchAverage(p,outlet), multiplied by densityKgPerM3",
            "volumetricFlowRate": "signed surface sum(phi) on inlet and outlet",
            "totalPressure": "volume-weighted cell-centred p+0.5|U|^2 at the first and last straight-leg cell planes",
            "symmetry": "paired cell-centred p and U samples mirrored across z=0 using explicit source-cell identity",
        },
    }


def _openfoam_curved_elbow_profile(
    project: dict[str, Any],
    advanced_mode: str,
) -> dict[str, Any] | None:
    """Compile exactly one bounded bend edge into the canonical elbow O-grid."""

    if not _openfoam_curved_elbow_mode_requested(project):
        return None
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    if advanced_mode != "incompressible-navier-stokes":
        raise ValueError("Curved-elbow O-grid mode supports only incompressible Navier-Stokes.")
    if str(solver.get("runMode", "")).strip().lower() != "steady":
        raise ValueError("Curved-elbow O-grid mode requires a steady solver.")
    if str(solver.get("turbulence", "")).strip().lower() != "laminar":
        raise ValueError("Curved-elbow O-grid mode requires laminar flow.")

    nodes = _project_nodes(project)
    edges = _project_edges(project)
    if len(nodes) != 2 or len(edges) != 1:
        raise ValueError(
            "Curved-elbow O-grid mode requires exactly one source, one sink, "
            "and one bounded bend edge."
        )
    edge = edges[0]
    nodes_by_id = {str(node.get("id") or ""): node for node in nodes}
    if len(nodes_by_id) != 2 or "" in nodes_by_id:
        raise ValueError("Curved-elbow editor nodes require unique non-empty IDs.")
    source_id = str(edge.get("from") or "")
    sink_id = str(edge.get("to") or "")
    source = nodes_by_id.get(source_id)
    sink = nodes_by_id.get(sink_id)
    if source is None or sink is None or source.get("type") != "source" or sink.get("type") != "sink":
        raise ValueError("Curved-elbow path must connect one source directly to one sink.")
    if edge.get("type") != "bend":
        raise ValueError("Curved-elbow O-grid mode supports only one bend edge.")
    shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
    if shape.get("kind") != "circular":
        raise ValueError("Curved-elbow O-grid mode requires a circular section.")
    diameter_m = _full_ogrid_positive_number(shape.get("diameter"), "curved-elbow diameter")
    outlet_diameter_m = _full_ogrid_positive_number(
        edge.get("outletDiameter", diameter_m),
        "curved-elbow outlet diameter",
    )
    if not math.isclose(diameter_m, outlet_diameter_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise ValueError("Curved-elbow O-grid mode requires one constant diameter.")
    raw = solver.get("curvedElbowVerification")
    if not isinstance(raw, dict):
        raise ValueError(
            "Curved-elbow O-grid mode requires an explicit curvedElbowVerification object."
        )
    centreline_radius = _full_ogrid_positive_number(
        raw.get("centrelineRadiusM"),
        "curved-elbow centrelineRadiusM",
    )
    inlet_leg = _full_ogrid_positive_number(
        raw.get("inletLegLengthM"),
        "curved-elbow inletLegLengthM",
    )
    outlet_leg = _full_ogrid_positive_number(
        raw.get("outletLegLengthM"),
        "curved-elbow outletLegLengthM",
    )
    bend_angle = _full_ogrid_positive_number(
        raw.get("bendAngleDegrees"),
        "curved-elbow bendAngleDegrees",
    )
    counts = _curved_elbow_cell_controls(project)
    spec = CurvedElbowSpec(
        diameter_m=diameter_m,
        centreline_radius_m=centreline_radius,
        inlet_leg_m=inlet_leg,
        outlet_leg_m=outlet_leg,
        inlet_axial_cells=counts[0],
        bend_axial_cells=counts[1],
        outlet_axial_cells=counts[2],
        annular_radial_cells=counts[3],
        circumferential_cells=counts[4],
        core_cells_per_side=counts[5],
        bend_angle_degrees=bend_angle,
    )
    if not math.isclose(spec.centreline_radius_over_diameter, 3.0, rel_tol=1.0e-12):
        raise ValueError("The bounded curved-elbow O-grid requires centreline radius Rc/D=3.")
    if not math.isclose(spec.inlet_leg_over_diameter, 10.0, rel_tol=1.0e-12):
        raise ValueError("The bounded curved-elbow O-grid requires an inlet leg of exactly 10D.")
    if not math.isclose(spec.outlet_leg_over_diameter, 10.0, rel_tol=1.0e-12):
        raise ValueError("The bounded curved-elbow O-grid requires an outlet leg of exactly 10D.")
    edge_length = _full_ogrid_positive_number(edge.get("length"), "curved-elbow edge length")
    if not math.isclose(
        edge_length,
        spec.total_centreline_length_m,
        rel_tol=1.0e-12,
        abs_tol=1.0e-15,
    ):
        raise ValueError(
            "The bounded curved-elbow edge length must equal inlet leg plus "
            "90-degree centreline arc plus outlet leg."
        )
    start = _node_position(source)
    end = _node_position(sink)
    if math.isclose(start[0], end[0], abs_tol=1.0e-12) and math.isclose(start[1], end[1], abs_tol=1.0e-12):
        raise ValueError("Curved-elbow source and sink require distinct editor positions.")
    verification = _curved_elbow_verification_request(project, spec=spec)
    return {
        "schema": CURVED_ELBOW_PROFILE_SCHEMA,
        "requestedMeshMode": "curved-elbow-ogrid",
        "effectiveMeshMode": CURVED_ELBOW_REPRESENTATION,
        "coordinateSystem": "physical-x-y-z-si",
        "units": {"length": "m", "angle": "deg"},
        "pathEdgeIds": [str(edge.get("id") or "")],
        "sourceNodeId": source_id,
        "sinkNodeId": sink_id,
        "editorLayout": {
            "source": [float(start[0]), float(start[1])],
            "sink": [float(end[0]), float(end[1])],
            "usedForPhysicalDimensions": False,
        },
        "diameterM": diameter_m,
        "radiusM": spec.radius_m,
        "centrelineRadiusM": centreline_radius,
        "inletLegLengthM": inlet_leg,
        "outletLegLengthM": outlet_leg,
        "bendAngleDegrees": bend_angle,
        "totalLengthM": spec.total_centreline_length_m,
        "topology": spec.topology_manifest(),
        "components": spec.component_regions(str(edge.get("id") or "")),
        "boundaryRoles": {"inlet": "patch", "outlet": "patch", "walls": "wall"},
        "verificationContract": verification,
        "scope": {
            "flow": "steady-incompressible-laminar",
            "geometry": "one-canonical-90deg-constant-diameter-circular-elbow",
            "unsupported": [
                "arbitrary-cad",
                "other-bend-angles",
                "other-rc-over-d-ratios",
                "branches",
                "diameter-changes",
                "turbulence",
                "transient",
                "multiphase",
                "compressible",
                "su2-without-supported-3d-result-identity",
            ],
        },
    }


def _curved_elbow_spec_from_profile(profile: dict[str, Any]) -> CurvedElbowSpec:
    topology = profile.get("topology") if isinstance(profile.get("topology"), dict) else {}
    resolution = topology.get("resolution") if isinstance(topology.get("resolution"), dict) else {}
    return CurvedElbowSpec(
        diameter_m=float(profile["diameterM"]),
        centreline_radius_m=float(profile["centrelineRadiusM"]),
        inlet_leg_m=float(profile["inletLegLengthM"]),
        outlet_leg_m=float(profile["outletLegLengthM"]),
        inlet_axial_cells=int(resolution["inletAxialCells"]),
        bend_axial_cells=int(resolution["bendAxialCells"]),
        outlet_axial_cells=int(resolution["outletAxialCells"]),
        annular_radial_cells=int(resolution["annularRadialCells"]),
        circumferential_cells=int(resolution["circumferentialCells"]),
        core_cells_per_side=int(resolution["coreCellsPerSide"]),
        bend_angle_degrees=float(profile["bendAngleDegrees"]),
    )


def _openfoam_y_junction_mode_requested(project: dict[str, Any] | None) -> bool:
    solver = project.get("solver") if isinstance(project, dict) and isinstance(project.get("solver"), dict) else {}
    return str(solver.get("meshMode", "planar-2d")).strip().lower() == "y-junction"


def _openfoam_y_junction_profile(
    project: dict[str, Any],
    advanced_mode: str,
) -> dict[str, Any] | None:
    """Compile exactly one source-junction-two-sink graph into the bounded path."""

    if not _openfoam_y_junction_mode_requested(project):
        return None
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    if advanced_mode != "incompressible-navier-stokes":
        raise ValueError("Y-junction mesh mode supports only incompressible Navier-Stokes.")
    if str(solver.get("runMode", "")).strip().lower() != "steady":
        raise ValueError("Y-junction mesh mode requires a steady solver.")
    if str(solver.get("turbulence", "")).strip().lower() != "laminar":
        raise ValueError("Y-junction mesh mode requires laminar flow.")

    nodes = _project_nodes(project)
    edges = _project_edges(project)
    if len(nodes) != 4 or len(edges) != 3:
        raise ValueError(
            "Y-junction mesh mode requires exactly one source, one junction, two sinks, and three pipes."
        )
    nodes_by_id = {str(node.get("id") or ""): node for node in nodes}
    if "" in nodes_by_id or len(nodes_by_id) != 4:
        raise ValueError("Y-junction nodes require unique non-empty IDs.")
    sources = [node for node in nodes if node.get("type") == "source"]
    junctions = [node for node in nodes if node.get("type") == "junction"]
    sinks = [node for node in nodes if node.get("type") == "sink"]
    if len(sources) != 1 or len(junctions) != 1 or len(sinks) != 2:
        raise ValueError("Y-junction topology must contain one source, one junction, and two sinks.")
    source = sources[0]
    junction = junctions[0]
    source_id = str(source["id"])
    junction_id = str(junction["id"])

    inlet_edges = [
        edge
        for edge in edges
        if edge.get("from") == source_id and edge.get("to") == junction_id
    ]
    branch_edges = [
        edge
        for edge in edges
        if edge.get("from") == junction_id and edge.get("to") in {sink.get("id") for sink in sinks}
    ]
    if len(inlet_edges) != 1 or len(branch_edges) != 2:
        raise ValueError("Y-junction edges must be directed source-to-junction and junction-to-each-sink.")
    if any(edge.get("type") != "pipe" for edge in edges):
        raise ValueError("Y-junction mesh mode supports only three pipe edges.")
    shapes = [
        edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        for edge in edges
    ]
    if any(shape.get("kind") != "circular" for shape in shapes):
        raise ValueError("Y-junction mesh mode requires circular inlet and branch sections.")
    diameters = [
        _full_ogrid_positive_number(shape.get("diameter"), "Y-junction diameter")
        for shape in shapes
    ]
    diameter = diameters[0]
    if any(
        not math.isclose(value, diameter, rel_tol=1.0e-12, abs_tol=1.0e-15)
        for value in diameters[1:]
    ):
        raise ValueError("Y-junction inlet and both branches must have one identical diameter.")
    for edge in edges:
        outlet_diameter = _full_ogrid_positive_number(
            edge.get("outletDiameter", diameter),
            "Y-junction outlet diameter",
        )
        if not math.isclose(outlet_diameter, diameter, rel_tol=1.0e-12, abs_tol=1.0e-15):
            raise ValueError("Y-junction pipes must have constant diameter.")

    inlet_length = _full_ogrid_positive_number(inlet_edges[0].get("length"), "Y-junction inlet length")
    branch_lengths = [
        _full_ogrid_positive_number(edge.get("length"), "Y-junction branch length")
        for edge in branch_edges
    ]
    if not math.isclose(branch_lengths[0], branch_lengths[1], rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise ValueError("Y-junction branches must have identical physical lengths.")

    junction_position = _node_position(junction)
    upper_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    lower_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for edge in branch_edges:
        sink = nodes_by_id[str(edge["to"])]
        sink_position = _node_position(sink)
        if sink_position[1] > junction_position[1]:
            upper_candidates.append((edge, sink))
        elif sink_position[1] < junction_position[1]:
            lower_candidates.append((edge, sink))
    if len(upper_candidates) != 1 or len(lower_candidates) != 1:
        raise ValueError("Y-junction editor layout must identify one upper and one lower branch.")
    upper_edge, upper_sink = upper_candidates[0]
    lower_edge, lower_sink = lower_candidates[0]

    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    raw_cell_size = controls.get("yJunctionCellSizeM")
    raw_master_cell_size = controls.get("yJunctionMasterCellSizeM")
    raw_refinement_factor = controls.get("yJunctionRefinementFactor")
    fixed_master_requested = raw_master_cell_size is not None or raw_refinement_factor is not None
    if fixed_master_requested and (
        raw_master_cell_size is None or raw_refinement_factor is None
    ):
        raise ValueError(
            "Fixed-master Y-junction mode requires both master cell size and refinement factor."
        )
    if fixed_master_requested:
        master_cell_size = _full_ogrid_positive_number(
            raw_master_cell_size,
            "Y-junction master cell size",
        )
        if (
            isinstance(raw_refinement_factor, bool)
            or not isinstance(raw_refinement_factor, int)
            or raw_refinement_factor not in {1, 2, 4}
        ):
            raise ValueError("Y-junction refinement factor must be one of 1, 2, or 4.")
        refinement_factor = int(raw_refinement_factor)
        cell_size = master_cell_size / refinement_factor
        if raw_cell_size is not None and not math.isclose(
            _full_ogrid_positive_number(raw_cell_size, "Y-junction cell size"),
            cell_size,
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        ):
            raise ValueError(
                "Y-junction cell size must equal master cell size divided by refinement factor."
            )
    elif raw_cell_size is None:
        cells_across = {"coarse": 6.0, "medium": 8.0, "fine": 12.0}.get(
            str(solver.get("meshResolution", "coarse")).strip().lower()
        )
        if cells_across is None:
            raise ValueError("Y-junction mesh resolution must be coarse, medium, or fine.")
        cell_size = diameter / cells_across
    else:
        cell_size = _full_ogrid_positive_number(raw_cell_size, "Y-junction cell size")
    spec = YJunctionSpec(
        inlet_length_m=inlet_length,
        branch_length_m=branch_lengths[0],
        diameter_m=diameter,
        cell_size_m=cell_size,
    )
    if fixed_master_requested:
        master_spec = YJunctionSpec(
            inlet_length_m=inlet_length,
            branch_length_m=branch_lengths[0],
            diameter_m=diameter,
            cell_size_m=master_cell_size,
        )
        mesh = generate_fixed_master_y_junction_mesh(
            master_spec,
            refinement_factor=refinement_factor,
            inlet_edge_id=str(inlet_edges[0]["id"]),
            upper_edge_id=str(upper_edge["id"]),
            lower_edge_id=str(lower_edge["id"]),
        )
    else:
        mesh = generate_y_junction_mesh(
            spec,
            inlet_edge_id=str(inlet_edges[0]["id"]),
            upper_edge_id=str(upper_edge["id"]),
            lower_edge_id=str(lower_edge["id"]),
        )
    fluid = project.get("fluid") if isinstance(project.get("fluid"), dict) else {}
    density = _safe_positive(fluid.get("density"), 998.2)
    viscosity = _safe_positive(fluid.get("dynamicViscosity"), 0.001002)
    area = math.pi * (diameter / 2.0) ** 2
    demanded_flow = sum(
        max(0.0, float(sink.get("flowDemand", 0.0)))
        for sink in sinks
        if isinstance(sink.get("flowDemand", 0.0), int | float)
    )
    inlet_velocity = demanded_flow / area if demanded_flow > 0.0 else 100.0 * viscosity / (density * diameter)

    def outlet_kinematic_pressure(sink: dict[str, Any]) -> float:
        pressure_pa = float(sink.get("pressure", 101325.0))
        if not math.isfinite(pressure_pa):
            raise ValueError("Y-junction outlet pressures must be finite.")
        return (pressure_pa - 101325.0) / density

    raw_probe_sampling = solver.get("yJunctionProbeSampling")
    if raw_probe_sampling is not None and not isinstance(raw_probe_sampling, dict):
        raise ValueError("Y-junction probe sampling must be an object.")
    probe_sampling = raw_probe_sampling or {}
    raw_pair_stations = probe_sampling.get("stationsM", [0.010, 0.016, 0.022])
    if (
        not isinstance(raw_pair_stations, list)
        or len(raw_pair_stations) != 3
        or any(
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0.0
            or float(value) >= spec.branch_length_m
            for value in raw_pair_stations
        )
    ):
        raise ValueError("Y-junction probe stations must be three finite positions inside each branch.")
    pair_stations = tuple(float(value) for value in raw_pair_stations)
    if any(left >= right for left, right in zip(pair_stations, pair_stations[1:])):
        raise ValueError("Y-junction probe stations must be strictly ordered.")
    interpolation_scheme = str(probe_sampling.get("interpolationScheme", "cell")).strip()
    if interpolation_scheme not in {"cell", "cellPoint"}:
        raise ValueError("Y-junction probe interpolation must be `cell` or `cellPoint`.")
    z_offset_fraction = float(probe_sampling.get("zOffsetCellFraction", 0.25))
    if not math.isfinite(z_offset_fraction) or not 0.0 < z_offset_fraction < 0.5:
        raise ValueError("Y-junction probe z offset must be between zero and half a cell.")
    probe_pairs = [
        {
            "stationM": station,
            "upper": [
                station * spec.upper_direction[0],
                station * spec.upper_direction[1],
                z_offset_fraction * cell_size,
            ],
            "lower": [
                station * spec.lower_direction[0],
                station * spec.lower_direction[1],
                z_offset_fraction * cell_size,
            ],
        }
        for station in pair_stations
        if station < spec.branch_length_m
    ]
    return {
        "schema": Y_JUNCTION_PROFILE_SCHEMA,
        "requestedMeshMode": "y-junction",
        "effectiveMeshMode": Y_JUNCTION_REPRESENTATION,
        "coordinateSystem": "physical-x-y-z-si",
        "units": {"length": "m", "angle": "deg", "pressure": "m^2/s^2"},
        "sourceNodeId": source_id,
        "junctionNodeId": junction_id,
        "upperSinkNodeId": str(upper_sink["id"]),
        "lowerSinkNodeId": str(lower_sink["id"]),
        "pathEdgeIds": [
            str(inlet_edges[0]["id"]),
            str(upper_edge["id"]),
            str(lower_edge["id"]),
        ],
        "edgeRoles": {
            "inlet": str(inlet_edges[0]["id"]),
            "upperBranch": str(upper_edge["id"]),
            "lowerBranch": str(lower_edge["id"]),
        },
        "geometry": mesh["geometry"],
        "mesh": {
            "generationSha256": mesh["generationSha256"],
            "cellCount": len(mesh["cells"]),
            "patches": mesh["patches"],
            "regions": mesh["regions"],
            **(
                {
                    "geometryInvariants": mesh["geometryInvariants"],
                    "refinement": mesh["refinement"],
                }
                if "geometryInvariants" in mesh
                else {}
            ),
        },
        "flow": {
            "densityKgPerM3": density,
            "dynamicViscosityPaS": viscosity,
            "inletMeanVelocityMPerS": inlet_velocity,
            "nominalReynoldsNumber": density * inlet_velocity * diameter / viscosity,
            "outletUpperKinematicPressureM2PerS2": outlet_kinematic_pressure(upper_sink),
            "outletLowerKinematicPressureM2PerS2": outlet_kinematic_pressure(lower_sink),
        },
        "probePairs": probe_pairs,
        "probeSampling": {
            "stationsM": list(pair_stations),
            "interpolationScheme": interpolation_scheme,
            "fixedLocations": True,
            "zOffsetCellFraction": z_offset_fraction,
        },
        "junctionArtifactIdentity": JUNCTION_ARTIFACT_ID,
        "ownership": {
            "source": "generated-region-artifact",
            "geometryInferenceAllowed": False,
            "junctionSchematicOwner": None,
        },
        "scope": {
            "flow": "steady-incompressible-laminar",
            "geometry": "one-inlet-two-identical-plus-minus-30-degree-circular-branches",
            "unsupported": [
                "arbitrary-networks",
                "arbitrary-branch-angles",
                "unequal-branch-diameters",
                "turbulence",
                "transient",
                "multiphase",
                "compressible",
                "promotion",
            ],
        },
        "_mesh": mesh,
    }


def _full_ogrid_spec_from_profile(profile: dict[str, Any]) -> FullOGridSpec:
    topology = profile.get("topology") if isinstance(profile.get("topology"), dict) else {}
    resolution = topology.get("resolution") if isinstance(topology.get("resolution"), dict) else {}
    return FullOGridSpec(
        length_m=float(profile["totalLengthM"]),
        radius_m=float(profile["radiusM"]),
        axial_cells=int(resolution["axialCells"]),
        annular_radial_cells=int(resolution["annularRadialCells"]),
        circumferential_cells=int(resolution["circumferentialCells"]),
        core_cells_per_side=int(resolution["coreCellsPerSide"]),
    )


def _full_ogrid_path_spec_from_profile(
    profile: dict[str, Any],
) -> FullOGridPathSpec:
    topology = (
        profile.get("topology")
        if isinstance(profile.get("topology"), dict)
        else {}
    )
    resolution = (
        topology.get("resolution")
        if isinstance(topology.get("resolution"), dict)
        else {}
    )
    stations = (
        profile.get("stations")
        if isinstance(profile.get("stations"), list)
        else []
    )
    segments = (
        profile.get("segments")
        if isinstance(profile.get("segments"), list)
        else []
    )
    compiled: list[FullOGridPathSegment] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Full O-grid path profile contains an invalid segment.")
        start = stations[int(segment["fromStation"])]
        end = stations[int(segment["toStation"])]
        compiled.append(
            FullOGridPathSegment(
                edge_id=str(segment["edgeId"]),
                edge_type=str(segment["edgeType"]),
                length_m=float(end["xM"]) - float(start["xM"]),
                inlet_radius_m=float(start["radiusM"]),
                outlet_radius_m=float(end["radiusM"]),
                axial_cells=int(segment["nAxial"]),
            )
        )
    return FullOGridPathSpec(
        segments=tuple(compiled),
        annular_radial_cells=int(resolution["annularRadialCells"]),
        circumferential_cells=int(resolution["circumferentialCells"]),
        core_cells_per_side=int(resolution["coreCellsPerSide"]),
    )


def _openfoam_full_ogrid_parabolic_vector_field(
    profile: dict[str, Any],
    verification: dict[str, Any],
) -> str:
    radius = float(profile.get("inletRadiusM", profile.get("radiusM")))
    flow_rate = float(verification["targetVolumetricFlowRateM3PerS"])
    mean_velocity = float(verification["meanVelocityTargetMPerS"])
    return (
        _foam_header("volVectorField", "U")
        + f"""dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ({mean_velocity:.17g} 0 0);

boundaryField
{{
    inlet
    {{
        type codedFixedValue;
        value uniform (0 0 0);
        name fullOGridParabolicInlet;
        code
        #{{
            const scalar radius = {radius:.17g};
            const scalar targetFlow = {flow_rate:.17g};
            const vectorField& centres = patch().Cf();
            const vectorField& areas = patch().Sf();
            scalarField shape(patch().size(), 0.0);
            scalar weightedArea = 0.0;
            forAll(shape, facei)
            {{
                const scalar r2 = sqr(centres[facei].y()) + sqr(centres[facei].z());
                shape[facei] = max(scalar(0), scalar(1) - r2/sqr(radius));
                weightedArea += mag(areas[facei])*shape[facei];
            }}
            vectorField values(patch().size(), vector::zero);
            const scalar scale = targetFlow/weightedArea;
            forAll(values, facei) {{ values[facei].x() = scale*shape[facei]; }}
            operator==(values);
        #}};
    }}
    outlet
    {{
        type pressureInletOutletVelocity;
        value uniform (0 0 0);
    }}
    walls
    {{
        type noSlip;
    }}
}}
"""
    )


def _openfoam_curved_elbow_parabolic_vector_field(
    profile: dict[str, Any],
    verification: dict[str, Any],
) -> str:
    return _openfoam_full_ogrid_parabolic_vector_field(
        profile,
        verification,
    ).replace("fullOGridParabolicInlet", "curvedElbowParabolicInlet")


def _openfoam_axisymmetric_block_mesh_dict(profile: dict[str, Any]) -> str:
    """Generate a conformal multi-block wedge from a compiled SI radius profile."""
    stations = profile.get("stations") if isinstance(profile.get("stations"), list) else []
    segments = profile.get("segments") if isinstance(profile.get("segments"), list) else []
    n_radial = int(profile.get("nRadial") or 0)
    if len(stations) < 2 or not segments or n_radial < 1:
        raise ValueError("Axisymmetric profile is missing stations, segments, or radial mesh controls.")

    vertices: list[str] = []
    for station in stations:
        x_m = float(station["xM"])
        radius_m = float(station["radiusM"])
        radius_tangent = radius_m * math.tan(math.radians(AXISYMMETRIC_WEDGE_HALF_ANGLE_DEG))
        vertices.extend(
            [
                f"    ({x_m:.12g} 0 0)",
                f"    ({x_m:.12g} {radius_m:.12g} {-radius_tangent:.12g})",
                f"    ({x_m:.12g} 0 0)",
                f"    ({x_m:.12g} {radius_m:.12g} {radius_tangent:.12g})",
            ]
        )

    blocks: list[str] = []
    wall_faces: list[str] = []
    front_faces: list[str] = []
    back_faces: list[str] = []
    for segment in segments:
        start = int(segment["fromStation"])
        end = int(segment["toStation"])
        n_axial = int(segment["nAxial"])
        start_base = start * 4
        end_base = end * 4
        vertices_for_block = (
            start_base,
            end_base,
            end_base + 1,
            start_base + 1,
            start_base + 2,
            end_base + 2,
            end_base + 3,
            start_base + 3,
        )
        blocks.append(
            "    hex "
            + "("
            + " ".join(str(value) for value in vertices_for_block)
            + f") ({n_axial} {n_radial} 1) simpleGrading (1 1 1)"
        )
        wall_faces.append(f"        ({end_base + 1} {start_base + 1} {start_base + 3} {end_base + 3})")
        front_faces.append(f"        ({start_base + 2} {end_base + 2} {end_base + 3} {start_base + 3})")
        back_faces.append(f"        ({start_base} {start_base + 1} {end_base + 1} {end_base})")

    first_base = int(segments[0]["fromStation"]) * 4
    last_base = int(segments[-1]["toStation"]) * 4
    periodic = isinstance(profile.get("benchmarkContract"), dict)
    inlet_type = "cyclic" if periodic else "patch"
    outlet_type = "cyclic" if periodic else "patch"
    inlet_neighbour = "\n        neighbourPatch outlet;" if periodic else ""
    outlet_neighbour = "\n        neighbourPatch inlet;" if periodic else ""
    return (
        _foam_header("dictionary", "blockMeshDict")
        + """convertToMeters 1;

vertices
(
"""
        + "\n".join(vertices)
        + """
);

blocks
(
"""
        + "\n".join(blocks)
        + """
);

edges
(
);

defaultPatch
{
    name axis;
    type empty;
}

boundary
(
    inlet
    {
        type """
        + inlet_type
        + ";"
        + inlet_neighbour
        + """
        faces
        (
"""
        + f"            ({first_base} {first_base + 2} {first_base + 3} {first_base + 1})"
        + """
        );
    }
    outlet
    {
        type """
        + outlet_type
        + ";"
        + outlet_neighbour
        + """
        faces
        (
"""
        + f"            ({last_base} {last_base + 1} {last_base + 3} {last_base + 2})"
        + """
        );
    }
    walls
    {
        type wall;
        faces
        (
"""
        + "\n".join(wall_faces)
        + """
        );
    }
    front
    {
        type wedge;
        faces
        (
"""
        + "\n".join(front_faces)
        + """
        );
    }
    back
    {
        type wedge;
        faces
        (
"""
        + "\n".join(back_faces)
        + """
        );
    }
);

mergePatchPairs
(
);
"""
    )


def _openfoam_axisymmetric_preview_mesh(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic 3D inspection mesh derived from the blockMesh profile.

    This is the intended pre-solve wedge geometry. Runtime evidence must still use
    the actual constant/polyMesh and foamToVTK artifacts produced by OpenFOAM.
    """
    stations = profile.get("stations") if isinstance(profile.get("stations"), list) else []
    segments = profile.get("segments") if isinstance(profile.get("segments"), list) else []
    n_radial = int(profile.get("nRadial") or 0)
    if len(stations) < 2 or not segments or n_radial < 1:
        raise ValueError("Axisymmetric preview requires a complete compiled profile.")

    axial_planes: list[tuple[float, float]] = []
    preview_regions: list[dict[str, Any]] = []
    source_cell_start = 0
    for segment in segments:
        start = stations[int(segment["fromStation"])]
        end = stations[int(segment["toStation"])]
        start_x = float(start["xM"])
        end_x = float(end["xM"])
        start_radius = float(start["radiusM"])
        end_radius = float(end["radiusM"])
        n_axial = int(segment["nAxial"])
        if not axial_planes:
            axial_planes.append((start_x, start_radius))
        elif not (
            math.isclose(axial_planes[-1][0], start_x, rel_tol=0.0, abs_tol=1.0e-12)
            and math.isclose(axial_planes[-1][1], start_radius, rel_tol=1.0e-9, abs_tol=1.0e-12)
        ):
            raise ValueError("Axisymmetric preview segments are not conformally ordered.")
        for cell_index in range(1, n_axial + 1):
            fraction = cell_index / n_axial
            axial_planes.append(
                (
                    start_x + (end_x - start_x) * fraction,
                    start_radius + (end_radius - start_radius) * fraction,
                )
            )
        cell_count = n_axial * n_radial
        preview_regions.append(
            {
                **segment,
                "cellStart": source_cell_start,
                "cellCount": cell_count,
            }
        )
        source_cell_start += cell_count

    tangent = math.tan(math.radians(AXISYMMETRIC_WEDGE_HALF_ANGLE_DEG))
    points: list[list[float]] = []
    for x_m, radius_m in axial_planes:
        for radial_index in range(n_radial + 1):
            radial_m = radius_m * radial_index / n_radial
            tangent_m = radial_m * tangent
            points.append([round(x_m, 12), round(radial_m, 12), round(-tangent_m, 12)])
            points.append([round(x_m, 12), round(radial_m, 12), round(tangent_m, 12)])

    points_per_plane = (n_radial + 1) * 2

    def point_index(plane: int, radial: int, side: int) -> int:
        return plane * points_per_plane + radial * 2 + side

    cells: list[list[int]] = []
    for plane_index in range(len(axial_planes) - 1):
        for radial_index in range(n_radial):
            cells.append(
                [
                    point_index(plane_index, radial_index, 0),
                    point_index(plane_index + 1, radial_index, 0),
                    point_index(plane_index + 1, radial_index + 1, 0),
                    point_index(plane_index, radial_index + 1, 0),
                    point_index(plane_index, radial_index, 1),
                    point_index(plane_index + 1, radial_index, 1),
                    point_index(plane_index + 1, radial_index + 1, 1),
                    point_index(plane_index, radial_index + 1, 1),
                ]
            )

    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    return {
        "format": "flowlab-axisymmetric-wedge-preview-v1",
        "coordinateSystem": "axisymmetric-x-r-theta",
        "spatialDimension": 3,
        "representation": "pre-solve-blockMesh-equivalent-wedge",
        "runtimeSolverMesh": False,
        "proxyGeometry": False,
        "profileSchema": profile.get("schema"),
        "wedge": profile.get("wedge"),
        "boundsSpanM": [round(value, 12) for value in spans],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_HEXAHEDRON for _ in cells],
        "regions": preview_regions,
    }


def _openfoam_water_hammer_pressure_field(conditions: CaseConditions, handoff: dict[str, Any]) -> str:
    waveform = handoff["waveform"]
    table_rows = "\n".join(f"            ({row['time']:.9g} {row['kinematicPressure']:.9g})" for row in waveform)
    internal = conditions.outlet_kinematic_pressure
    outlet = conditions.outlet_kinematic_pressure
    return (
        _foam_header("volScalarField", "p")
        + f"""dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {internal:.9g};

boundaryField
{{
    inlet
    {{
        type            uniformFixedValue;
        uniformValue    table
        (
{table_rows}
        );
        value           uniform {waveform[0]["kinematicPressure"]:.9g};
    }}
    outlet
    {{
        type            fixedValue;
        value           uniform {outlet:.9g};
    }}
    walls
    {{
        type            zeroGradient;
    }}
    frontAndBack
    {{
        type            empty;
    }}
}}
"""
    )


def _openfoam_scalar_field(
    object_name: str,
    dimensions: str,
    internal: str,
    inlet: str,
    outlet_type: str = "zeroGradient",
    wall_type: str = "zeroGradient",
) -> str:
    outlet_value = f"\n        value           uniform {internal};" if outlet_type == "fixedValue" else ""
    wall_value = f"\n        value           uniform {internal};" if wall_type == "fixedValue" else ""
    return (
        _foam_header("volScalarField", object_name)
        + f"""dimensions      {dimensions};

internalField   uniform {internal};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {inlet};
    }}
    outlet
    {{
        type            {outlet_type};{outlet_value}
    }}
    walls
    {{
        type            {wall_type};{wall_value}
    }}
    frontAndBack
    {{
        type            empty;
    }}
}}
"""
    )


def _openfoam_calculated_scalar_field(object_name: str, dimensions: str, value: str = "0") -> str:
    return (
        _foam_header("volScalarField", object_name)
        + f"""dimensions      {dimensions};

internalField   uniform {value};

boundaryField
{{
    inlet
    {{
        type            calculated;
        value           uniform {value};
    }}
    outlet
    {{
        type            calculated;
        value           uniform {value};
    }}
    walls
    {{
        type            calculated;
        value           uniform {value};
    }}
    frontAndBack
    {{
        type            empty;
    }}
}}
"""
    )


def _openfoam_temperature_field(temperature: float = 293.15, far_patches: str | None = None) -> str:
    far = _PLANAR_FAR_FIELD_PATCHES if far_patches is None else far_patches
    return (
        _foam_header("volScalarField", "T")
        + f"""dimensions      [0 0 0 1 0 0 0];

internalField   uniform {temperature:.6g};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {temperature:.6g};
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    walls
    {{
        type            zeroGradient;
    }}
{far}
}}
"""
    )


def _reviewed_surface_boundary_condition(surface: dict[str, Any]) -> dict[str, Any] | None:
    value = surface.get("boundaryCondition")
    if not isinstance(value, dict):
        return None
    raw_kind = str(value.get("kind") or value.get("mode") or value.get("type") or "").strip()
    aliases = {
        "velocity-inlet": "velocity",
        "mass-flow-inlet": "mass-flow",
        "pressure-inlet": "pressure-inlet",
        "pressure-outlet": "pressure-outlet",
        "outflow": "outflow",
        "outflow-outlet": "outflow",
        "no-slip-wall": "no-slip",
        "no-slip": "no-slip",
        "slip-wall": "slip",
        "slip": "slip",
        "rough-wall": "rough-wall",
        "heat-flux-wall": "heat-flux",
        "temperature-wall": "temperature",
        "coupled-interface": "coupled-interface",
        "mapped-interface": "mapped-interface",
        "interface": "interface",
    }
    kind = aliases.get(raw_kind, raw_kind)
    if not kind:
        return None
    return {**value, "kind": kind}


def _reviewed_surface_bc_patches(project: dict[str, Any]) -> list[dict[str, Any]]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    reviewed = solver.get("reviewedGeometry") if isinstance(solver.get("reviewedGeometry"), dict) else {}
    surfaces = reviewed.get("surfaces") if isinstance(reviewed.get("surfaces"), list) else []
    patches: list[dict[str, Any]] = []
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        patch_name = str(surface.get("patchName") or "").strip()
        role = str(surface.get("role") or "").strip()
        if not patch_name or role not in {"inlet", "outlet", "wall", "interface"}:
            continue
        patches.append(
            {
                "surfaceName": str(surface.get("surfaceName") or patch_name),
                "patchName": patch_name,
                "role": role,
                "boundaryCondition": _reviewed_surface_boundary_condition(surface),
            }
        )
    return patches


def _number(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _vector(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (_number(value[0], default[0]), _number(value[1], default[1]), _number(value[2], default[2]))
    if isinstance(value, dict):
        return (_number(value.get("x"), default[0]), _number(value.get("y"), default[1]), _number(value.get("z"), default[2]))
    return default


def _foam_vector(value: tuple[float, float, float]) -> str:
    return f"({_number(value[0], 0):.9g} {_number(value[1], 0):.9g} {_number(value[2], 0):.9g})"


def _pressure_value_for_dimensions(value: float, dimensions: str, conditions: CaseConditions) -> float:
    return value if dimensions == "[1 -1 -2 0 0 0 0]" else value / max(conditions.density, 1e-12)


def _patch_block(name: str, body: list[str]) -> list[str]:
    return [f"    {name}", "    {", *[f"        {line}" for line in body], "    }"]


def _surface_velocity_bc(patch: dict[str, Any], conditions: CaseConditions) -> list[str]:
    role = patch["role"]
    bc = patch.get("boundaryCondition") or {}
    kind = str(bc.get("kind") or "")
    default_velocity = (conditions.inlet_velocity, 0.0, 0.0)
    if role == "inlet":
        if kind == "mass-flow":
            mass_flow = _number(bc.get("massFlowRate") or bc.get("value"), conditions.density * conditions.reference_area * conditions.inlet_velocity)
            return [
                "type            flowRateInletVelocity;",
                f"massFlowRate    constant {mass_flow:.9g};",
                "value           uniform (0 0 0);",
            ]
        if kind == "pressure-inlet":
            return ["type            pressureInletOutletVelocity;", "value           uniform (0 0 0);"]
        velocity = _vector(bc.get("velocity"), default_velocity)
        if "speed" in bc:
            velocity = (_number(bc.get("speed"), conditions.inlet_velocity), 0.0, 0.0)
        return ["type            fixedValue;", f"value           uniform {_foam_vector(velocity)};"]
    if role == "outlet":
        if kind == "outflow":
            return ["type            inletOutlet;", "inletValue      uniform (0 0 0);", "value           uniform (0 0 0);"]
        return ["type            zeroGradient;"]
    if role == "wall":
        if kind == "slip":
            return ["type            slip;"]
        return ["type            noSlip;"]
    return ["type            patch;"]


def _surface_pressure_bc(patch: dict[str, Any], conditions: CaseConditions, dimensions: str, default_outlet: float) -> list[str]:
    role = patch["role"]
    bc = patch.get("boundaryCondition") or {}
    kind = str(bc.get("kind") or "")
    if role == "inlet":
        if kind == "pressure-inlet":
            pressure = _pressure_value_for_dimensions(_number(bc.get("pressure"), conditions.inlet_pressure), dimensions, conditions)
            return ["type            fixedValue;", f"value           uniform {pressure:.9g};"]
        return ["type            zeroGradient;"]
    if role == "outlet":
        if kind == "outflow":
            return ["type            zeroGradient;"]
        pressure = _pressure_value_for_dimensions(_number(bc.get("pressure"), default_outlet), dimensions, conditions)
        return ["type            fixedValue;", f"value           uniform {pressure:.9g};"]
    return ["type            zeroGradient;"]


def _surface_temperature_bc(patch: dict[str, Any], conditions: CaseConditions) -> list[str]:
    role = patch["role"]
    bc = patch.get("boundaryCondition") or {}
    kind = str(bc.get("kind") or "")
    if role == "inlet":
        temperature = _number(bc.get("temperature"), conditions.temperature)
        return ["type            fixedValue;", f"value           uniform {temperature:.6g};"]
    if role == "wall":
        if kind == "temperature":
            temperature = _number(bc.get("temperature"), conditions.temperature)
            return ["type            fixedValue;", f"value           uniform {temperature:.6g};"]
        if kind == "heat-flux":
            heat_flux = _number(bc.get("heatFlux"), 0.0)
            return [
                "type            fixedGradient;",
                f"gradient        uniform {heat_flux:.9g};",
                f"value           uniform {conditions.temperature:.6g};",
            ]
    if role == "interface":
        return ["type            zeroGradient;"]
    return ["type            zeroGradient;"]


def _surface_alpha_bc(patch: dict[str, Any], object_name: str) -> list[str]:
    role = patch["role"]
    phase_value = "1" if object_name == "alpha.water" else "0"
    if role == "inlet":
        return ["type            fixedValue;", f"value           uniform {phase_value};"]
    if role == "outlet":
        return ["type            inletOutlet;", f"inletValue      uniform {phase_value};", f"value           uniform {phase_value};"]
    return ["type            zeroGradient;"]


def _surface_generic_scalar_bc(patch: dict[str, Any], value: str = "0") -> list[str]:
    role = patch["role"]
    if role == "wall":
        return ["type            zeroGradient;"]
    if role == "interface":
        return ["type            zeroGradient;"]
    return ["type            zeroGradient;"]


def _surface_calculated_scalar_bc(patch: dict[str, Any], value: str = "0") -> list[str]:
    if patch["role"] == "interface":
        return ["type            zeroGradient;"]
    return ["type            calculated;", f"value           uniform {value};"]


def _openfoam_surface_vector_field(object_name: str, internal: str, patches: list[dict[str, Any]], conditions: CaseConditions) -> str:
    lines = [
        _foam_header("volVectorField", object_name),
        "dimensions      [0 1 -1 0 0 0 0];",
        "",
        f"internalField   uniform {internal};",
        "",
        "boundaryField",
        "{",
    ]
    for patch in patches:
        lines.extend(_patch_block(patch["patchName"], _surface_velocity_bc(patch, conditions)))
    lines.extend(_patch_block("frontAndBack", ["type            empty;"]))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _openfoam_surface_pressure_field(
    object_name: str,
    dimensions: str,
    internal: str,
    patches: list[dict[str, Any]],
    conditions: CaseConditions,
    default_outlet: float,
) -> str:
    lines = [
        _foam_header("volScalarField", object_name),
        f"dimensions      {dimensions};",
        "",
        f"internalField   uniform {internal};",
        "",
        "boundaryField",
        "{",
    ]
    for patch in patches:
        lines.extend(_patch_block(patch["patchName"], _surface_pressure_bc(patch, conditions, dimensions, default_outlet)))
    lines.extend(_patch_block("frontAndBack", ["type            empty;"]))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _openfoam_surface_scalar_field(
    object_name: str,
    dimensions: str,
    internal: str,
    patches: list[dict[str, Any]],
    patch_body_factory,
) -> str:
    lines = [
        _foam_header("volScalarField", object_name),
        f"dimensions      {dimensions};",
        "",
        f"internalField   uniform {internal};",
        "",
        "boundaryField",
        "{",
    ]
    for patch in patches:
        lines.extend(_patch_block(patch["patchName"], patch_body_factory(patch)))
    lines.extend(_patch_block("frontAndBack", ["type            empty;"]))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _surface_bc_allowed_kinds(role: str) -> set[str]:
    return {
        "inlet": {"velocity", "mass-flow", "pressure-inlet"},
        "outlet": {"pressure-outlet", "outflow"},
        "wall": {"no-slip", "slip", "rough-wall", "heat-flux", "temperature"},
        "interface": {"mapped-interface", "coupled-interface", "interface"},
    }.get(role, set())


def _surface_bc_status(patches: list[dict[str, Any]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    for patch in patches:
        bc = patch.get("boundaryCondition")
        kind = str(bc.get("kind") or "") if isinstance(bc, dict) else ""
        allowed = _surface_bc_allowed_kinds(str(patch.get("role")))
        status = "complete" if kind in allowed else "missing" if not kind else "invalid"
        if status == "missing":
            missing.append(str(patch["patchName"]))
        elif status == "invalid":
            invalid.append(str(patch["patchName"]))
        entries.append(
            {
                "surfaceName": patch["surfaceName"],
                "patchName": patch["patchName"],
                "role": patch["role"],
                "kind": kind or None,
                "status": status,
                "allowedKinds": sorted(allowed),
            }
        )
    return {
        "schema": "flowlab.openfoam_surface_boundary_conditions.v1",
        "status": "complete" if not missing and not invalid else "blocked",
        "missingPatchNames": missing,
        "invalidPatchNames": invalid,
        "patches": entries,
        "requiredFields": ["0/U", "0/p", "0/T", "0/alpha.* when present"],
    }


def _apply_reviewed_surface_boundary_conditions(
    files: dict[str, str],
    project: dict[str, Any],
    advanced_mode: str,
    conditions: CaseConditions,
    pressure_dimensions: str,
    default_outlet_pressure: float,
) -> list[str]:
    patches = _reviewed_surface_bc_patches(project)
    if not patches:
        return []

    status = _surface_bc_status(patches)
    files["constant/flowlab_boundary_conditions.json"] = json.dumps(status, indent=2, sort_keys=True) + "\n"
    notes = ["Reviewed STL surface boundary-condition manifest written to constant/flowlab_boundary_conditions.json."]
    if status["status"] != "complete":
        notes.append("Reviewed STL surface boundary conditions are incomplete; execution validation will block solver launch.")
        return notes

    inlet_patch = next((patch for patch in patches if patch["role"] == "inlet"), None)
    inlet_velocity = _foam_vector(_vector((inlet_patch or {}).get("boundaryCondition", {}).get("velocity"), (conditions.inlet_velocity, 0.0, 0.0)))
    files["0/U"] = _openfoam_surface_vector_field("U", inlet_velocity, patches, conditions)
    files["0/p"] = _openfoam_surface_pressure_field(
        "p",
        pressure_dimensions,
        f"{default_outlet_pressure:.9g}",
        patches,
        conditions,
        conditions.outlet_pressure,
    )
    if "0/p_rgh" in files:
        files["0/p_rgh"] = _openfoam_surface_pressure_field(
            "p_rgh",
            pressure_dimensions,
            f"{default_outlet_pressure:.9g}",
            patches,
            conditions,
            conditions.outlet_pressure,
        )
    if "0/T" in files:
        files["0/T"] = _openfoam_surface_scalar_field(
            "T",
            "[0 0 0 1 0 0 0]",
            f"{conditions.temperature:.6g}",
            patches,
            lambda patch: _surface_temperature_bc(patch, conditions),
        )
    for path, text in list(files.items()):
        if not path.startswith("0/") or path in {"0/U", "0/p", "0/p_rgh", "0/T"}:
            continue
        field_name = path.removeprefix("0/")
        if field_name.startswith("alpha."):
            files[path] = _openfoam_surface_scalar_field(
                field_name,
                "[0 0 0 0 0 0 0]",
                "1" if field_name == "alpha.water" else "0",
                patches,
                lambda patch, name=field_name: _surface_alpha_bc(patch, name),
            )
        elif field_name in {"nut", "alphat"}:
            files[path] = _openfoam_surface_scalar_field(
                field_name,
                "[0 2 -1 0 0 0 0]" if field_name == "nut" else "[1 -1 -1 0 0 0 0]",
                "0",
                patches,
                lambda patch: _surface_calculated_scalar_bc(patch, "0"),
            )
        elif "boundaryField" in text:
            internal_match = text.split("internalField   uniform ", 1)
            internal_value = internal_match[1].split(";", 1)[0].strip() if len(internal_match) == 2 else "0"
            dimensions_match = text.split("dimensions      ", 1)
            dimensions = dimensions_match[1].split(";", 1)[0].strip() if len(dimensions_match) == 2 else "[0 0 0 0 0 0 0]"
            files[path] = _openfoam_surface_scalar_field(
                field_name,
                dimensions,
                internal_value,
                patches,
                lambda patch, value=internal_value: _surface_generic_scalar_bc(patch, value),
            )
    return notes


def _openfoam_field_list(advanced_mode: str) -> list[str]:
    fields = ["U", "p", "p_rgh", "T"]
    if advanced_mode in {"compressible-flow", "heat-transfer", "conjugate-heat-transfer", "multiphase-vof", "cavitation"}:
        fields.append("rho")
    if advanced_mode in {"conjugate-heat-transfer", "multiphase-vof", "cavitation"}:
        fields.extend(["k", "omega"])
    if advanced_mode == "multiphase-vof":
        fields.extend(["alpha.water", "alpha.air"])
    if advanced_mode == "cavitation":
        fields.append("alpha.vapour")
    return fields


def _openfoam_probe_locations(mesh: dict[str, Any] | None) -> list[tuple[float, float, float]]:
    min_x, max_x, min_y, max_y = _mesh_bounds(mesh)
    y_mid = (min_y + max_y) * 0.5
    z_mid = 0.005
    scale = 0.01
    return [
        (min_x * scale, y_mid * scale, z_mid),
        (((min_x + max_x) * 0.5) * scale, y_mid * scale, z_mid),
        (max_x * scale, y_mid * scale, z_mid),
    ]


def _openfoam_project_probe_locations(project: dict[str, Any]) -> list[tuple[float, float, float]]:
    locations: list[tuple[float, float, float]] = []
    for node in _project_nodes(project):
        if node.get("type") != "probe":
            continue
        x, y = _node_position(node)
        z = _number(node.get("z"), 0.005)
        locations.append((x * 0.01, y * 0.01, z))
    return locations


def _openfoam_metric_patch_plan(project: dict[str, Any]) -> dict[str, list[str]]:
    if _openfoam_y_junction_mode_requested(project):
        return {
            "inlet": ["inlet"],
            "outlet": ["outletUpper", "outletLower"],
            "wall": ["walls"],
            "flow": ["inlet", "outletUpper", "outletLower"],
        }
    reviewed_patches = _reviewed_surface_bc_patches(project)
    if reviewed_patches:
        inlet = [patch["patchName"] for patch in reviewed_patches if patch["role"] == "inlet"]
        outlet = [patch["patchName"] for patch in reviewed_patches if patch["role"] == "outlet"]
        wall = [patch["patchName"] for patch in reviewed_patches if patch["role"] == "wall"]
    else:
        inlet = ["inlet"]
        outlet = ["outlet"]
        wall = ["walls"]
    return {
        "inlet": inlet or ["inlet"],
        "outlet": outlet or ["outlet"],
        "wall": wall or ["walls"],
        "flow": [*(inlet or ["inlet"]), *(outlet or ["outlet"])],
    }


def _foam_word_list(values: list[str]) -> str:
    return "(" + " ".join(values) + ")"


def _openfoam_patch_metric_manifest(project: dict[str, Any]) -> dict[str, Any]:
    patch_plan = _openfoam_metric_patch_plan(project)
    pressure_probe_locations = _openfoam_project_probe_locations(project)
    return {
        "schema": "flowlab.openfoam_patch_metric_function_objects.v1",
        "patches": patch_plan,
        "pressureProbeLocations": pressure_probe_locations,
        "functionObjects": [
            "patchFlowRate",
            "patchAverage",
            "wallShearStress",
            "wallForces",
            *(("pressureProbes",) if pressure_probe_locations else ()),
        ],
    }


def _openfoam_function_object_runtime_manifest(project: dict[str, Any]) -> dict[str, Any]:
    patch_manifest = _openfoam_patch_metric_manifest(project)
    return {
        "schema": "flowlab.openfoam_function_object_runtime.v1",
        "contract": "constant/flowlab_patch_metrics.json",
        "defaultStyle": "controlDict-functions",
        "runtimeStyles": {
            "opencfd": {
                "placement": "system/controlDict:functions",
                "patchFlowRate": "patchFlowRate function object with patches list",
                "patchAverage": "surfaceFieldValue areaAverage over patch list",
            },
            "foundation": {
                "placement": "system/functions included from system/controlDict when runtime detection selects it",
                "patchFlowRate": "FlowLab keeps the normalized patchFlowRate contract and can fall back to flowRatePatch/surfaceFieldValue-style outputs during native validation.",
                "patchAverage": "surfaceFieldValue areaAverage entries are kept in system/functions for Foundation-style function-object placement.",
            },
        },
        "functionObjects": patch_manifest["functionObjects"],
        "patches": patch_manifest["patches"],
        "notes": [
            "FlowLab normalizes outputs into patchMetrics regardless of the OpenFOAM function-object style.",
            "Runtime detection may switch controlDict to include system/functions while preserving this contract.",
        ],
    }


def _openfoam_axisymmetric_probe_locations(profile: dict[str, Any]) -> list[tuple[float, float, float]]:
    stations = profile.get("stations") if isinstance(profile.get("stations"), list) else []
    segments = profile.get("segments") if isinstance(profile.get("segments"), list) else []
    locations: list[tuple[float, float, float]] = []
    for segment in segments:
        start = stations[int(segment["fromStation"])]
        end = stations[int(segment["toStation"])]
        x_m = (float(start["xM"]) + float(end["xM"])) / 2.0
        radius_m = (float(start["radiusM"]) + float(end["radiusM"])) / 2.0
        locations.append((x_m, radius_m * 0.5, 0.0))
    return locations


def _openfoam_curved_elbow_probe_records(
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    components = (
        profile.get("components")
        if isinstance(profile.get("components"), list)
        else []
    )
    component_by_id = {
        str(row.get("componentId")): row
        for row in components
        if isinstance(row, dict)
    }
    if set(component_by_id) != {"inlet-leg", "elbow", "outlet-leg"}:
        raise ValueError(
            "Curved-elbow probes require explicit inlet-leg, elbow, and "
            "outlet-leg source-cell component ranges."
        )
    inlet = float(profile["inletLegLengthM"])
    centreline = float(profile["centrelineRadiusM"])
    outlet = float(profile["outletLegLengthM"])
    radius = float(profile["radiusM"])
    bend_x = inlet + centreline / math.sqrt(2.0)
    bend_y = centreline - centreline / math.sqrt(2.0)
    definitions = (
        ("inlet-centre", "inlet-leg", (0.5 * inlet, 0.0, 0.0)),
        ("elbow-centre", "elbow", (bend_x, bend_y, 0.0)),
        (
            "outlet-centre",
            "outlet-leg",
            (inlet + centreline, centreline + 0.5 * outlet, 0.0),
        ),
        (
            "elbow-inner-half-radius",
            "elbow",
            (
                bend_x - 0.5 * radius / math.sqrt(2.0),
                bend_y + 0.5 * radius / math.sqrt(2.0),
                0.0,
            ),
        ),
        (
            "elbow-outer-half-radius",
            "elbow",
            (
                bend_x + 0.5 * radius / math.sqrt(2.0),
                bend_y - 0.5 * radius / math.sqrt(2.0),
                0.0,
            ),
        ),
        ("elbow-positive-z-half-radius", "elbow", (bend_x, bend_y, 0.5 * radius)),
        ("elbow-negative-z-half-radius", "elbow", (bend_x, bend_y, -0.5 * radius)),
    )
    return [
        {
            "probeId": probe_id,
            "locationM": list(location),
            "edgeId": str(component_by_id[component_id]["edgeId"]),
            "componentId": component_id,
            "sourceCellRange": {
                "cellStart": int(component_by_id[component_id]["cellStart"]),
                "cellCount": int(component_by_id[component_id]["cellCount"]),
            },
            "ownershipMethod": "explicit-result-component-map-v2-cell-range",
            "geometryInferredOwnership": False,
        }
        for probe_id, component_id, location in definitions
    ]


def _openfoam_curved_elbow_probe_manifest(
    profile: dict[str, Any],
) -> dict[str, Any]:
    records = _openfoam_curved_elbow_probe_records(profile)
    return {
        "schema": "flowlab.curved-elbow-probe-provenance.v1",
        "profileSchema": CURVED_ELBOW_PROFILE_SCHEMA,
        "probeFunctionObject": "curvedElbowXYZProbes",
        "sourceCellIdentity": "result-component-map-v2-cell-ranges",
        "geometryInferredOwnershipAllowed": False,
        "probeCount": len(records),
        "probes": records,
    }


def _openfoam_full_ogrid_probe_locations(profile: dict[str, Any]) -> list[tuple[float, float, float]]:
    if profile.get("schema") == CURVED_ELBOW_PROFILE_SCHEMA:
        return [
            tuple(float(value) for value in record["locationM"])
            for record in _openfoam_curved_elbow_probe_records(profile)
        ]
    stations = (
        profile.get("stations")
        if isinstance(profile.get("stations"), list)
        else []
    )
    segments = (
        profile.get("segments")
        if isinstance(profile.get("segments"), list)
        else []
    )
    if stations and segments:
        locations: list[tuple[float, float, float]] = []
        for segment in segments:
            start = stations[int(segment["fromStation"])]
            end = stations[int(segment["toStation"])]
            x_m = (float(start["xM"]) + float(end["xM"])) / 2.0
            radius_m = (
                float(start["radiusM"]) + float(end["radiusM"])
            ) / 2.0
            locations.extend(
                [
                    (x_m, 0.0, 0.0),
                    (x_m, 0.5 * radius_m, 0.0),
                ]
            )
        return locations
    length_m = float(profile["totalLengthM"])
    radius_m = float(profile["radiusM"])
    return [
        (0.25 * length_m, 0.0, 0.0),
        (0.50 * length_m, 0.0, 0.0),
        (0.75 * length_m, 0.0, 0.0),
        (0.50 * length_m, 0.50 * radius_m, 0.0),
        (0.50 * length_m, 0.0, 0.50 * radius_m),
        (0.50 * length_m, -0.50 * radius_m, 0.0),
        (0.50 * length_m, 0.0, -0.50 * radius_m),
    ]


def _openfoam_function_object_entries(
    mesh: dict[str, Any] | None,
    advanced_mode: str,
    project: dict[str, Any] | None = None,
    axisymmetric_profile: dict[str, Any] | None = None,
    full_ogrid_profile: dict[str, Any] | None = None,
) -> str:
    project = project or {}
    fields = " ".join(_openfoam_field_list(advanced_mode))
    compiled_probe_locations = (
        _openfoam_full_ogrid_probe_locations(full_ogrid_profile)
        if full_ogrid_profile is not None
        else _openfoam_axisymmetric_probe_locations(axisymmetric_profile)
        if axisymmetric_profile is not None
        else _openfoam_probe_locations(mesh)
    )
    probe_locations = "\n".join(f"            ({x:.9g} {y:.9g} {z:.9g})" for x, y, z in compiled_probe_locations)
    probe_object_name = (
        "curvedElbowXYZProbes"
        if isinstance(full_ogrid_profile, dict)
        and full_ogrid_profile.get("schema") == CURVED_ELBOW_PROFILE_SCHEMA
        else "fullOGridXYZProbes"
        if full_ogrid_profile is not None
        else "axisymmetricProfileProbes"
        if axisymmetric_profile is not None
        else "centerlineProbes"
    )
    patch_plan = _openfoam_metric_patch_plan(project)
    metric_patches = _foam_word_list(patch_plan["flow"])
    wall_patches = _foam_word_list(patch_plan["wall"])
    verification = (
        full_ogrid_profile.get("verificationContract")
        if isinstance(full_ogrid_profile, dict)
        and isinstance(full_ogrid_profile.get("verificationContract"), dict)
        else full_ogrid_profile.get("qualificationContract")
        if isinstance(full_ogrid_profile, dict)
        and isinstance(full_ogrid_profile.get("qualificationContract"), dict)
        else axisymmetric_profile.get("experimentalQualificationContract")
        if isinstance(axisymmetric_profile, dict)
        and isinstance(
            axisymmetric_profile.get("experimentalQualificationContract"),
            dict,
        )
        else {}
    )
    qoi_history_interval = verification.get("qoiHistoryWriteIntervalIterations")
    qoi_write_control = "timeStep" if qoi_history_interval == 1 else "writeTime"
    qoi_write_interval = (
        "\n        writeInterval   1;" if qoi_history_interval == 1 else ""
    )
    pressure_probe_locations = _openfoam_project_probe_locations(project)
    pressure_probe_block = ""
    if pressure_probe_locations:
        pressure_locations = "\n".join(f"            ({x:.6f} {y:.6f} {z:.6f})" for x, y, z in pressure_probe_locations)
        pressure_probe_block = f"""

    pressureProbes
    {{
        type            probes;
        libs            ("libsampling.so");
        writeControl    timeStep;
        writeInterval   1;
        fields          (p p_rgh);
        probeLocations
        (
{pressure_locations}
        );
    }}
"""
    return f"""
    residuals
    {{
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        fields          ({fields});
    }}

    {probe_object_name}
    {{
        type            probes;
        libs            ("libsampling.so");
        writeControl    timeStep;
        writeInterval   5;
        fields          ({fields});
        probeLocations
        (
{probe_locations}
        );
    }}

    wallForces
    {{
        type            forces;
        libs            ("libforces.so");
        writeControl    writeTime;
        patches         {wall_patches};
        rho             rhoInf;
        rhoInf          998.2;
        CofR            (0 0 0);
        pName           p;
        UName           U;
    }}

    patchFlowRate
    {{
        type            patchFlowRate;
        libs            ("libfieldFunctionObjects.so");
        writeControl    {qoi_write_control};{qoi_write_interval}
        patches         {metric_patches};
        phi             phi;
    }}

    patchAverage
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    {qoi_write_control};{qoi_write_interval}
        log             true;
        writeFields     false;
        regionType      patch;
        name            {metric_patches};
        operation       areaAverage;
        fields          (p);
    }}

    wallShearStress
    {{
        type            wallShearStress;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
        patches         {wall_patches};
    }}{pressure_probe_block}
"""


def _openfoam_function_objects(
    mesh: dict[str, Any] | None,
    advanced_mode: str,
    project: dict[str, Any] | None = None,
    axisymmetric_profile: dict[str, Any] | None = None,
    full_ogrid_profile: dict[str, Any] | None = None,
) -> str:
    return f"""
functions
{{
{_openfoam_function_object_entries(mesh, advanced_mode, project, axisymmetric_profile, full_ogrid_profile)}
}}
"""


def _openfoam_cht_region_function_objects() -> str:
    return """
functions
{
    residuals
    {
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        fields          (U p p_rgh T rho);
    }

    centerlineProbes
    {
        type            probes;
        libs            ("libsampling.so");
        writeControl    timeStep;
        writeInterval   5;
        fields          (U p p_rgh T rho);
        probeLocations
        (
            (0 0 0.005)
        );
    }

    wallForces
    {
        type            forces;
        libs            ("libforces.so");
        writeControl    writeTime;
        patches         (walls);
        rho             rhoInf;
        rhoInf          998.2;
        CofR            (0 0 0);
        pName           p;
        UName           U;
    }
}
"""


def _openfoam_y_junction_function_object_entries(profile: dict[str, Any]) -> str:
    locations: list[list[float]] = [[-0.002, 0.0, 0.25 * float(profile["geometry"]["cellSizeM"])]]
    for pair in profile["probePairs"]:
        locations.extend([pair["upper"], pair["lower"]])
    probe_locations = "\n".join(
        f"            ({point[0]:.17g} {point[1]:.17g} {point[2]:.17g})"
        for point in locations
    )
    surface_objects: list[str] = []
    for object_name, patch, operation, fields in (
        ("inletFlow", "inlet", "sum", "(phi)"),
        ("upperFlow", "outletUpper", "sum", "(phi)"),
        ("lowerFlow", "outletLower", "sum", "(phi)"),
        ("inletPressure", "inlet", "areaAverage", "(p)"),
        ("upperPressure", "outletUpper", "areaAverage", "(p)"),
        ("lowerPressure", "outletLower", "areaAverage", "(p)"),
    ):
        surface_objects.append(
            f"""
    {object_name}
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   25;
        log             false;
        writeFields     false;
        regionType      patch;
        name            {patch};
        operation       {operation};
        fields          {fields};
    }}
"""
        )
    return f"""
    residuals
    {{
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   25;
        fields          (U p);
    }}

    yJunctionMirroredProbes
    {{
        type            probes;
        libs            ("libsampling.so");
        fixedLocations  true;
        interpolationScheme {profile["probeSampling"]["interpolationScheme"]};
        writeControl    timeStep;
        writeInterval   25;
        fields          (p U);
        probeLocations
        (
{probe_locations}
        );
    }}

    wallForces
    {{
        type            forces;
        libs            ("libforces.so");
        writeControl    writeTime;
        patches         (walls);
        rho             rhoInf;
        rhoInf          {float(profile["flow"]["densityKgPerM3"]):.17g};
        CofR            (0 0 0);
        pName           p;
        UName           U;
    }}

    patchFlowRate
    {{
        type            patchFlowRate;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
        patches         (inlet outletUpper outletLower);
        phi             phi;
    }}

    patchAverage
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
        log             false;
        writeFields     false;
        regionType      patch;
        name            (inlet outletUpper outletLower);
        operation       areaAverage;
        fields          (p);
    }}

    wallShearStress
    {{
        type            wallShearStress;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
        patches         (walls);
    }}
{''.join(surface_objects)}
"""


def _openfoam_y_junction_function_objects(profile: dict[str, Any]) -> str:
    return (
        "\nfunctions\n{\n"
        + _openfoam_y_junction_function_object_entries(profile)
        + "}\n"
    )


def _openfoam_y_junction_control_dict(
    solver: str,
    project: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    solver_settings = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    max_iterations = solver_settings.get("maxIterations", 2500)
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        raise ValueError("Y-junction maxIterations must be a positive integer.")
    return (
        _foam_header("dictionary", "controlDict")
        + f"""application     foamRun;
solver          {solver};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {max_iterations};
deltaT          1;
writeControl    timeStep;
writeInterval   {max_iterations};
purgeWrite      0;
writeFormat     ascii;
writePrecision  12;
writeCompression off;
timeFormat      general;
timePrecision   8;
runTimeModifiable false;
"""
        + _openfoam_y_junction_function_objects(profile)
    )


STEADY_RUN_CAPABLE_MODES = frozenset({"incompressible-navier-stokes"})


def _openfoam_steady_requested(advanced_mode: str, project: dict[str, Any] | None) -> bool:
    """Return True when a converged steady-state SIMPLE run was explicitly requested.

    Opt-in via ``project["solver"]["runMode"] == "steady"``. Honoured only for
    steady-capable incompressible modes; inherently transient modes (water-hammer,
    multiphase VOF, cavitation, compressible flow) always keep transient controls.
    The default (no ``runMode`` set) remains the short transient starter run.
    """
    if advanced_mode not in STEADY_RUN_CAPABLE_MODES:
        return False
    solver = project.get("solver") if isinstance(project, dict) and isinstance(project.get("solver"), dict) else {}
    return str(solver.get("runMode", "transient")).strip().lower() == "steady"


def _openfoam_control_dict(
    solver: str,
    mesh: dict[str, Any] | None,
    advanced_mode: str,
    project: dict[str, Any] | None = None,
    axisymmetric_profile: dict[str, Any] | None = None,
    full_ogrid_profile: dict[str, Any] | None = None,
) -> str:
    if _openfoam_steady_requested(advanced_mode, project):
        # Steady SIMPLE: deltaT is an iteration counter and residualControl in
        # fvSolution stops the solve early once residuals fall below tolerance.
        curved_contract = (
            isinstance(full_ogrid_profile, dict)
            and full_ogrid_profile.get("schema")
            == CURVED_ELBOW_PROFILE_SCHEMA
        )
        declared_iterations = (
            project.get("solver", {}).get("maxIterations", 2000)
            if curved_contract and isinstance(project, dict)
            else 2000
        )
        if (
            not isinstance(declared_iterations, int)
            or isinstance(declared_iterations, bool)
            or declared_iterations <= 0
        ):
            raise ValueError(
                "Curved-elbow steady execution requires a positive integer maxIterations."
            )
        end_time = str(declared_iterations)
        delta_t, write_interval = "1", end_time
    elif advanced_mode == "compressible-flow":
        end_time, delta_t, write_interval = "0.001", "0.00001", "100"
    else:
        end_time, delta_t, write_interval = "0.05", "0.001", "25"
    solver_header = (
        """application     foamMultiRun;

regionSolvers
{
    fluid           fluid;
    solid           solid;
}
"""
        if solver == "foamMultiRun"
        else f"""application     foamRun;
solver          {solver};
"""
    )
    return (
        _foam_header("dictionary", "controlDict")
        + f"""{solver_header}
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {end_time};
deltaT          {delta_t};
writeControl    timeStep;
writeInterval   {write_interval};
purgeWrite      0;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
"""
        + _openfoam_function_objects(
            mesh,
            advanced_mode,
            project,
            axisymmetric_profile,
            full_ogrid_profile,
        )
    )


def _openfoam_fv_schemes(steady: bool = False) -> str:
    ddt_default = "steadyState" if steady else "Euler"
    return (
        _foam_header("dictionary", "fvSchemes")
        + "ddtSchemes\n{\n    default         " + ddt_default + ";\n}\n"
        + """gradSchemes
{
    default         Gauss linear;
}
divSchemes
{
    default         none;
    div(phi,U)      Gauss linearUpwind grad(U);
    div(rhoPhi,U)   Gauss linearUpwind grad(U);
    div(phi,T)      Gauss upwind;
    div(phi,k)      Gauss upwind;
    div(rhoPhi,k)   Gauss upwind;
    div(phi,omega)  Gauss upwind;
    div(rhoPhi,omega) Gauss upwind;
    div(phi,epsilon) Gauss upwind;
    div(phi,(p|rho)) Gauss upwind;
    div(rhoPhi,(p|rho)) Gauss upwind;
    div(phi,p)      Gauss upwind;
    div(phi,K)      Gauss upwind;
    div(rhoPhi,K)   Gauss upwind;
    div(phi,e)      Gauss upwind;
    div(rhoPhi,e)   Gauss upwind;
    div(alphaRhoPhi,e) Gauss upwind;
    div(phi,h)      Gauss upwind;
    div(rhoPhi,h)   Gauss upwind;
    div(alphaRhoPhi,T) Gauss upwind;
    div(phi,alpha)  Gauss vanLeer;
    div(rhoPhi,alpha) Gauss vanLeer;
    div(phid1,p_rgh) Gauss upwind;
    div(phid2,p_rgh) Gauss upwind;
    div(phirb,alpha) Gauss interfaceCompression;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes
{
    default         Gauss linear corrected;
}
interpolationSchemes
{
    default         linear;
}
snGradSchemes
{
    default         corrected;
}
wallDist
{
    method          meshWave;
}
"""
    )


def _openfoam_axisymmetric_benchmark_fv_constraints(profile: dict[str, Any]) -> str:
    contract = profile.get("benchmarkContract")
    if not isinstance(contract, dict):
        raise ValueError("Axisymmetric benchmark fvConstraints require a compiled benchmark contract.")
    mean_velocity = float(contract["meanVelocityTargetMPerS"])
    return (
        _foam_header("dictionary", "fvConstraints")
        + f"""momentumForce
{{
    type            meanVelocityForce;
    select          all;
    Ubar            ({mean_velocity:.17g} 0 0);
    relaxation      1;
}}
"""
    )


def _openfoam_axisymmetric_benchmark_fv_schemes() -> str:
    return (
        _foam_header("dictionary", "fvSchemes")
        + """ddtSchemes
{
    default         steadyState;
}
gradSchemes
{
    default         cellLimited Gauss linear 1;
}
divSchemes
{
    default         none;
    div(phi,U)      Gauss linearUpwind grad(U);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes
{
    default         Gauss linear corrected;
}
interpolationSchemes
{
    default         linear;
}
snGradSchemes
{
    default         corrected;
}
"""
    )


def _openfoam_fv_solution(
    steady: bool = False,
    periodic_pressure_reference: bool = False,
    strict_verification: bool = False,
) -> str:
    if periodic_pressure_reference:
        return (
            _foam_header("dictionary", "fvSolution")
            + """solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-10;
        relTol          0;
    }
    U
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-10;
        relTol          0;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
    consistent      yes;
    pRefCell        0;
    pRefValue       0;
    residualControl
    {
        p               1e-8;
        U               1e-8;
    }
}

relaxationFactors
{
    equations
    {
        U               0.9;
    }
}
"""
        )
    reference_controls = (
        """    consistent      yes;
    pRefCell        0;
    pRefValue       0;
"""
        if periodic_pressure_reference
        else ""
    )
    residual_tolerance = "1e-8" if periodic_pressure_reference or strict_verification else "1e-5"
    coupling = (
        f"""SIMPLE
{{
    nNonOrthogonalCorrectors 1;
{reference_controls}    residualControl
    {{
        p               {residual_tolerance};
        U               {residual_tolerance};
    }}
}}

relaxationFactors
{{
    fields
    {{
        p               0.3;
    }}
    equations
    {{
        U               0.7;
    }}
}}
"""
        if steady
        else """PIMPLE
{
    nOuterCorrectors 1;
    nCorrectors      2;
    nNonOrthogonalCorrectors 0;
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
}
"""
    )
    return (
        _foam_header("dictionary", "fvSolution")
        + """solvers
{
    alpha.water
    {
        nAlphaCorr      1;
        nAlphaSubCycles 2;
        cAlpha          1;
    }
    alpha.vapour
    {
        nAlphaCorr      3;
        nAlphaSubCycles 1;
        cAlpha          1;
    }
    "pcorr.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-10;
        relTol          0;
    }
    p
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0.01;
    }
    p_rgh
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0.01;
    }
    "(p|p_rgh)Final"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0;
    }
    "(U|T|e|h|k|omega|epsilon)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
    }
    "(U|T|e|h|k|omega|epsilon)Final"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }
    rho
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }
    rhoFinal
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }
}

"""
        + coupling
    )


def _openfoam_transport_properties(
    advanced_mode: str = "incompressible-navier-stokes",
    conditions: CaseConditions | None = None,
) -> str:
    conditions = conditions or CaseConditions()
    if advanced_mode == "compressible-flow":
        gas_density = _openfoam_ideal_gas_density(conditions)
        return (
            _foam_header("dictionary", "transportProperties")
            + f"""transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] {1.8e-5 / max(gas_density, 1e-12):.9g};
rho             [1 -3 0 0 0 0 0] {gas_density:.9g};
"""
        )
    if advanced_mode in {"multiphase-vof", "cavitation"}:
        return (
            _foam_header("dictionary", "transportProperties")
            + f"""water
{{
    transportModel  Newtonian;
    nu              [0 2 -1 0 0 0 0] {conditions.kinematic_viscosity:.9g};
    rho             [1 -3 0 0 0 0 0] {conditions.density:.9g};
}}

air
{{
    transportModel  Newtonian;
    nu              [0 2 -1 0 0 0 0] 1.5e-05;
    rho             [1 -3 0 0 0 0 0] 1.2;
}}

sigma           [1 0 -2 0 0 0 0] 0.072;
"""
        )
    return (
        _foam_header("dictionary", "transportProperties")
        + f"""transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] {conditions.kinematic_viscosity:.9g};
rho             [1 -3 0 0 0 0 0] {conditions.density:.9g};
"""
    )


def _openfoam_thermophysical_properties(advanced_mode: str = "compressible-flow") -> str:
    if advanced_mode in {"heat-transfer", "conjugate-heat-transfer"}:
        return (
            _foam_header("dictionary", "thermophysicalProperties")
            + """thermoType
{
    type            heRhoThermo;
    mixture         pureMixture;
    properties      liquid;
    energy          sensibleInternalEnergy;
}

mixture
{
    H2O;
}
"""
        )

    return (
        _foam_header("dictionary", "thermophysicalProperties")
        + """thermoType
{
    type            hePsiThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState perfectGas;
    specie          specie;
    energy          sensibleInternalEnergy;
}

mixture
{
    specie
    {
        nMoles      1;
        molWeight   28.9;
    }
    thermodynamics
    {
        Cp          1005;
        Hf          0;
    }
    transport
    {
        mu          1.8e-05;
        Pr          0.71;
    }
}
"""
    )


def _openfoam_cht_fluid_physical_properties(conditions: CaseConditions) -> str:
    return (
        _foam_header("dictionary", "physicalProperties")
        + f"""thermoType
{{
    type            heRhoThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState rhoConst;
    specie          specie;
    energy          sensibleEnthalpy;
}}

mixture
{{
    specie
    {{
        molWeight       18;
    }}
    equationOfState
    {{
        rho             {conditions.density:.9g};
    }}
    thermodynamics
    {{
        Cp              4181;
        Hf              0;
    }}
    transport
    {{
        mu              {conditions.dynamic_viscosity:.9g};
        Pr              6.62;
    }}
}}
"""
    )


def _openfoam_cht_solid_physical_properties() -> str:
    return (
        _foam_header("dictionary", "physicalProperties")
        + """thermoType
{
    type            heSolidThermo;
    mixture         pureMixture;
    transport       constIsoSolid;
    thermo          eConst;
    equationOfState rhoConst;
    specie          specie;
    energy          sensibleInternalEnergy;
}

mixture
{
    specie
    {
        molWeight       27;
    }
    equationOfState
    {
        rho             2700;
    }
    transport
    {
        kappa           200;
    }
    thermodynamics
    {
        Hf              0;
        Cv              900;
    }
}
"""
    )


def _openfoam_cht_fluid_fv_schemes() -> str:
    return (
        _foam_header("dictionary", "fvSchemes")
        + """ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      Gauss upwind;
    div(phi,K)      Gauss linear;
    div(phi,h)      Gauss upwind;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}
"""
    )


def _openfoam_cht_fluid_fv_solution() -> str:
    return (
        _foam_header("dictionary", "fvSolution")
        + """solvers
{
    rho
    {
        solver          diagonal;
    }
    rhoFinal
    {
        $rho;
    }
    p_rgh
    {
        solver          GAMG;
        smoother        symGaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
    }
    p_rghFinal
    {
        $p_rgh;
        relTol          0;
    }
    "(U|h)"
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-7;
        relTol          0.1;
    }
    "(U|h)Final"
    {
        $U;
        relTol          0;
    }
}

PIMPLE
{
    momentumPredictor yes;
}

relaxationFactors
{
    equations
    {
        h               1;
        U               1;
    }
}
"""
    )


def _openfoam_cht_solid_fv_schemes() -> str:
    return (
        _foam_header("dictionary", "fvSchemes")
        + """ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}
"""
    )


def _openfoam_cht_solid_fv_solution() -> str:
    return (
        _foam_header("dictionary", "fvSolution")
        + """solvers
{
    e
    {
        solver          GAMG;
        smoother        symGaussSeidel;
        tolerance       1e-6;
        relTol          0.1;
        maxIter         10;
    }
    eFinal
    {
        $e;
        relTol          0;
    }
}

PIMPLE
{
    nNonOrthogonalCorrectors 0;
}
"""
    )


def _openfoam_cht_fluid_temperature_field(temperature: float) -> str:
    return (
        _foam_header("volScalarField", "T")
        + f"""dimensions      [0 0 0 1 0 0 0];
internalField   uniform {temperature:.9g};
boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    inlet
    {{
        type            fixedValue;
        value           $internalField;
    }}
    outlet
    {{
        type            inletOutlet;
        value           $internalField;
        inletValue      $internalField;
    }}
    fluid_to_solid
    {{
        type            coupledTemperature;
        value           $internalField;
    }}
    frontAndBack
    {{
        type            empty;
    }}
}}
"""
    )


def _openfoam_cht_solid_temperature_field(temperature: float) -> str:
    solid_temperature = temperature + 20.0
    return (
        _foam_header("volScalarField", "T")
        + f"""dimensions      [0 0 0 1 0 0 0];
internalField   uniform {solid_temperature:.9g};
boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    inlet
    {{
        type            zeroGradient;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    solid_to_fluid
    {{
        type            coupledTemperature;
        value           $internalField;
    }}
    frontAndBack
    {{
        type            empty;
    }}
}}
"""
    )


def _openfoam_patch_renamed(field: str, old: str, new: str) -> str:
    return field.replace(f"    {old}\n    {{", f"    {new}\n    {{")


def _openfoam_cht_region_files(conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    density = f"{conditions.density:.9g}"
    fluid_wall_patch = "fluid_to_solid"
    files = {
        "0/fluid/U": _openfoam_patch_renamed(_openfoam_vector_field("U", f"({conditions.inlet_velocity:.9g} 0 0)"), "walls", fluid_wall_patch),
        "0/fluid/p": _openfoam_patch_renamed(
            _openfoam_pressure_field("p", "[1 -1 -2 0 0 0 0]", f"{conditions.outlet_pressure:.9g}", f"{conditions.outlet_pressure:.9g}"),
            "walls",
            fluid_wall_patch,
        ),
        "0/fluid/p_rgh": _openfoam_patch_renamed(
            _openfoam_pressure_field("p_rgh", "[1 -1 -2 0 0 0 0]", f"{conditions.outlet_pressure:.9g}", f"{conditions.outlet_pressure:.9g}"),
            "walls",
            fluid_wall_patch,
        ),
        "0/fluid/T": _openfoam_cht_fluid_temperature_field(conditions.temperature),
        "0/fluid/rho": _openfoam_patch_renamed(_openfoam_scalar_field("rho", "[1 -3 0 0 0 0 0]", density, density), "walls", fluid_wall_patch),
        "0/fluid/k": _openfoam_patch_renamed(_openfoam_scalar_field("k", "[0 2 -2 0 0 0 0]", "0.001", "0.001"), "walls", fluid_wall_patch),
        "0/fluid/omega": _openfoam_patch_renamed(_openfoam_scalar_field("omega", "[0 0 -1 0 0 0 0]", "10", "10"), "walls", fluid_wall_patch),
        "0/fluid/nut": _openfoam_patch_renamed(_openfoam_calculated_scalar_field("nut", "[0 2 -1 0 0 0 0]", "0"), "walls", fluid_wall_patch),
        "0/fluid/alphat": _openfoam_patch_renamed(_openfoam_calculated_scalar_field("alphat", "[1 -1 -1 0 0 0 0]", "0"), "walls", fluid_wall_patch),
        "0/solid/T": _openfoam_cht_solid_temperature_field(conditions.temperature),
        "constant/fluid/g": _openfoam_gravity(),
        "constant/fluid/momentumTransport": _openfoam_momentum_transport(turbulent=True),
        "constant/fluid/physicalProperties": _openfoam_cht_fluid_physical_properties(conditions),
        "constant/solid/physicalProperties": _openfoam_cht_solid_physical_properties(),
        "system/fluid/fvSchemes": _openfoam_cht_fluid_fv_schemes(),
        "system/fluid/fvSolution": _openfoam_cht_fluid_fv_solution(),
        "system/solid/fvSchemes": _openfoam_cht_solid_fv_schemes(),
        "system/solid/fvSolution": _openfoam_cht_solid_fv_solution(),
    }
    return files, [
        "Conjugate heat-transfer emits OpenFOAM v11 foamMultiRun region files for a fluid solver region and a solid heat-conduction region.",
        "CHT region files mirror the OpenFOAM multiRegion/CHT tutorial pattern with region-scoped physicalProperties, fvSchemes, fvSolution, and temperature fields.",
    ]


def _openfoam_phase_properties() -> str:
    return (
        _foam_header("dictionary", "phaseProperties")
        + """phases          (water air);
sigma           0.072;

flowlabCavitationPreset no;
"""
    )


def _openfoam_cavitation_phase_properties() -> str:
    return (
        _foam_header("dictionary", "phaseProperties")
        + """phases          (vapour water);

sigma
{
    type            constant;
    sigma           0.07;
}

flowlabCavitationPreset yes;
"""
    )


def _openfoam_phase_physical_properties(phase: str, conditions: CaseConditions) -> str:
    if phase == "water":
        return (
            _foam_header("dictionary", "physicalProperties.water")
            + f"""viscosityModel  constant;

nu              {conditions.kinematic_viscosity:.9g};

rho             {conditions.density:.9g};
"""
        )
    if phase == "air":
        return (
            _foam_header("dictionary", "physicalProperties.air")
            + """viscosityModel  constant;

nu              1.48e-05;

rho             1.2;
"""
        )
    raise ValueError(f"Unsupported OpenFOAM phase `{phase}`.")


def _openfoam_cavitation_physical_properties(phase: str, conditions: CaseConditions) -> str:
    if phase == "water":
        return (
            _foam_header("dictionary", "physicalProperties.water")
            + """thermoType
{
    type            heRhoThermo;
    mixture         pureMixture;
    transport       const;
    thermo          eConst;
    equationOfState rPolynomial;
    specie          specie;
    energy          sensibleInternalEnergy;
}

mixture
{
    specie
    {
        molWeight   18.0;
    }
    equationOfState
    {
        C           (0.001278 -2.1055e-06 3.9689e-09 4.3772e-13 -2.0225e-16);
    }
    thermodynamics
    {
        Cv          4195;
        Hf          0;
    }
    transport
    {
        mu          3.645e-4;
        Pr          2.289;
    }
}
"""
        )
    if phase == "vapour":
        return (
            _foam_header("dictionary", "physicalProperties.vapour")
            + """thermoType
{
    type            heRhoThermo;
    mixture         pureMixture;
    transport       sutherland;
    thermo          janaf;
    energy          sensibleInternalEnergy;
    equationOfState perfectGas;
    specie          specie;
}

mixture
{
    specie
    {
        molWeight       18.0153;
    }
    thermodynamics
    {
        Tlow            200;
        Thigh           5000;
        Tcommon         1000;
        highCpCoeffs    (2.67215 0.00305629 -8.73026e-07 1.201e-10 -6.39162e-15 -29899.2 6.86282);
        lowCpCoeffs     (3.38684 0.00347498 -6.3547e-06 6.96858e-09 -2.50659e-12 -30208.1 2.59023);
    }
    transport
    {
        As              1.67212e-06;
        Ts              170.672;
    }
}
"""
        )
    raise ValueError(f"Unsupported OpenFOAM cavitation phase `{phase}`.")


def _openfoam_cavitation_thermodynamic_properties(conditions: CaseConditions) -> str:
    return (
        _foam_header("dictionary", "thermodynamicProperties")
        + f"""barotropicCompressibilityModel linear;

psiv            [0 -2 2 0 0] 2.5e-06;

rholSat         [1 -3 0 0 0] {conditions.density:.9g};

psil            [0 -2 2 0 0] 5e-07;

pSat            [1 -1 -2 0 0 0 0] {conditions.vapor_pressure:.9g};

rhoMin          [1 -3 0 0 0] 0.001;
"""
    )


def _openfoam_cavitation_fv_models(conditions: CaseConditions) -> str:
    return (
        _foam_header("dictionary", "fvModels")
        + f"""VoFCavitation
{{
    type            compressible::VoFCavitation;

    model           SchnerrSauer;

    SchnerrSauerCoeffs
    {{
        pSat        {conditions.vapor_pressure:.9g};
        liquid      water;
        n           1.6e+13;
        dNuc        2.0e-06;
        Cc          1;
        Cv          1;
    }}
}}
"""
    )


def _openfoam_momentum_transport(turbulent: bool) -> str:
    if not turbulent:
        return _foam_header("dictionary", "momentumTransport") + "simulationType laminar;\n"
    return (
        _foam_header("dictionary", "momentumTransport")
        + """simulationType RAS;

RAS
{
    model           kOmegaSST;
    turbulence      on;
    printCoeffs     on;
}
"""
    )


def _openfoam_turbulence_properties(turbulent: bool) -> str:
    if not turbulent:
        return _foam_header("dictionary", "turbulenceProperties") + "simulationType laminar;\n"
    return (
        _foam_header("dictionary", "turbulenceProperties")
        + """simulationType RAS;

RAS
{
    RASModel        kOmegaSST;
    turbulence      on;
    printCoeffs     on;
}
"""
    )


def _openfoam_gravity() -> str:
    return (
        _foam_header("uniformDimensionedVectorField", "g")
        + """dimensions      [0 1 -2 0 0 0 0];
value           (0 -9.81 0);
"""
    )


def _water_hammer_handoff(project: dict[str, Any], conditions: CaseConditions) -> dict[str, Any]:
    nodes = _project_nodes(project)
    edges = _project_edges(project)
    nodes_by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    wave_speed = math.sqrt(max(conditions.bulk_modulus, 1.0) / max(conditions.density, 1.0))
    edge_previews = []
    for edge in edges:
        edge_id = str(edge.get("id") or f"edge-{len(edge_previews) + 1}")
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else None
        area = _shape_area(shape)
        from_node = nodes_by_id.get(str(edge.get("from")), {})
        to_node = nodes_by_id.get(str(edge.get("to")), {})
        demand = to_node.get("flowDemand") if isinstance(to_node, dict) else None
        flow_rate = abs(float(demand)) if isinstance(demand, (int, float)) else abs(conditions.inlet_velocity * area)
        velocity = flow_rate / max(area, 1.0e-12)
        length = _edge_effective_length(edge, nodes_by_id)
        pressure_rise = conditions.density * wave_speed * velocity
        edge_previews.append(
            {
                "edgeId": edge_id,
                "edgeType": str(edge.get("type", "pipe")),
                "effectiveLength": round(length, 9),
                "hydraulicDiameter": round(_shape_hydraulic_diameter(shape), 9),
                "flowRate": round(flow_rate, 12),
                "velocity": round(velocity, 9),
                "waveSpeed": round(wave_speed, 9),
                "pressureRise": round(pressure_rise, 6),
                "kinematicPressureRise": round(pressure_rise / conditions.density, 9),
                "criticalClosureTime": round((2.0 * length) / wave_speed, 9),
            }
        )
    if not edge_previews:
        velocity = conditions.inlet_velocity
        pressure_rise = conditions.density * wave_speed * abs(velocity)
        edge_previews.append(
            {
                "edgeId": "reference",
                "edgeType": "pipe",
                "effectiveLength": 1.0,
                "hydraulicDiameter": round(conditions.hydraulic_diameter, 9),
                "flowRate": round(abs(velocity) * conditions.reference_area, 12),
                "velocity": round(abs(velocity), 9),
                "waveSpeed": round(wave_speed, 9),
                "pressureRise": round(pressure_rise, 6),
                "kinematicPressureRise": round(pressure_rise / conditions.density, 9),
                "criticalClosureTime": round(2.0 / wave_speed, 9),
            }
        )
    dominant = max(edge_previews, key=lambda item: float(item["pressureRise"]))
    closure_time = max(float(dominant["criticalClosureTime"]) * 0.35, 1.0e-5)
    hold_time = max(float(dominant["criticalClosureTime"]), closure_time * 2.0)
    settle_time = hold_time + closure_time
    base = conditions.outlet_kinematic_pressure
    rise = float(dominant["kinematicPressureRise"])
    waveform = [
        {"time": 0.0, "kinematicPressure": round(base, 9), "absolutePressure": round(conditions.outlet_pressure, 6)},
        {
            "time": round(closure_time, 9),
            "kinematicPressure": round(base + rise, 9),
            "absolutePressure": round(conditions.outlet_pressure + float(dominant["pressureRise"]), 6),
        },
        {
            "time": round(hold_time, 9),
            "kinematicPressure": round(base + rise, 9),
            "absolutePressure": round(conditions.outlet_pressure + float(dominant["pressureRise"]), 6),
        },
        {"time": round(settle_time, 9), "kinematicPressure": round(base, 9), "absolutePressure": round(conditions.outlet_pressure, 6)},
    ]
    return {
        "schema": "flowlab.water_hammer_handoff.v1",
        "model": "method-of-characteristics-preview",
        "cfdCoupling": "pressure-wave-boundary-preview",
        "productionReady": False,
        "fluid": {
            "density": conditions.density,
            "bulkModulus": conditions.bulk_modulus,
            "waveSpeed": round(wave_speed, 9),
        },
        "dominantEdgeId": dominant["edgeId"],
        "edges": edge_previews,
        "waveform": waveform,
        "openfoam": {
            "pressureField": "0/p",
            "boundary": "inlet",
            "boundaryType": "uniformFixedValue table",
            "pressureUnits": "kinematic m2/s2",
            "csv": "constant/waterHammerWaveform.csv",
        },
        "codeSaturne": {
            "handoff": "DATA/flowlab_water_hammer_handoff.json",
            "csv": "DATA/flowlab_water_hammer_waveform.csv",
            "boundary": "inlet",
            "boundaryConditionHook": "manual transient pressure boundary review required",
            "pressureUnits": "absolute Pa",
            "nativeStatus": "exported waveform only; not mapped into a Code_Saturne transient boundary setup",
        },
        "su2": {
            "handoff": "flowlab_su2_water_hammer_handoff.json",
            "csv": "flowlab_su2_water_hammer_waveform.csv",
            "boundary": "MARKER_INLET",
            "boundaryConditionHook": "manual transient inlet pressure/characteristic boundary review required",
            "pressureUnits": "absolute Pa",
            "nativeStatus": "exported waveform only; not mapped into a SU2 transient compressible-liquid setup",
        },
        "notes": [
            "This handoff maps the Tier 1 water-hammer preview to solver-reviewable pressure-wave boundary data.",
            "OpenFOAM receives a starter inlet pressure table; Code_Saturne and SU2 receive explicit JSON/CSV handoff artifacts for manual native setup review.",
            "It is a transient CFD preview input, not a fully coupled compressible liquid water-hammer solver.",
        ],
    }


def _water_hammer_waveform_csv(handoff: dict[str, Any]) -> str:
    lines = ["time,kinematicPressure,absolutePressure"]
    lines.extend(
        f"{row['time']},{row['kinematicPressure']},{row['absolutePressure']}"
        for row in handoff["waveform"]
    )
    return "\n".join(lines) + "\n"


def _code_saturne_water_hammer_files(project: dict[str, Any], conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    handoff = _water_hammer_handoff(project, conditions)
    handoff["targetSolver"] = "code-saturne"
    handoff["nativeCodeSaturneReady"] = False
    handoff["manualNativeSetupRequired"] = [
        "Map DATA/flowlab_water_hammer_waveform.csv into a reviewed transient inlet pressure boundary condition.",
        "Choose compressible-liquid assumptions, timestep controls, and pressure-wave damping appropriate for the Code_Saturne setup.",
        "Add pipe-wall elasticity or a validated coupling model before claiming full water-hammer CFD.",
    ]
    return (
        {
            "DATA/flowlab_water_hammer_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n",
            "DATA/flowlab_water_hammer_waveform.csv": _water_hammer_waveform_csv(handoff),
        },
        [
            "Code_Saturne water-hammer mode exports a Tier 1 MOC pressure-wave handoff JSON/CSV for native setup review.",
            "The generated handoff is not automatically wired into a Code_Saturne transient boundary condition; coupled water-hammer CFD remains unresolved.",
        ],
    )


def _code_saturne_cht_handoff_files(project: dict[str, Any], conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    handoff = {
        "schema": "flowlab.code_saturne_cht_handoff.v1",
        "targetSolver": "code-saturne",
        "advancedMode": "conjugate-heat-transfer",
        "productionReady": False,
        "nativeCodeSaturneReady": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "starterFiles": {
            "setupXml": "DATA/setup.xml",
            "mesh": "MESH/flowlab_mesh.msh",
            "physicsHook": "DATA/cs_user_physics.py",
            "boundaryHook": "SRC/cs_user_boundary_conditions.f90",
        },
        "fluidDomain": {
            "status": "starter-generated",
            "zone": "all_cells",
            "thermalScalar": "temperature_celsius",
            "expectedFields": ["pressure", "velocity", "fluid_temperature"],
        },
        "solidDomain": {
            "status": "manual",
            "meshStatus": "not generated",
            "requiredInputs": ["solid mesh", "solid zones", "solid conductivity", "solid density", "solid heat capacity"],
            "expectedFields": ["solid_temperature", "heat_flux"],
        },
        "interfaceCoupling": {
            "status": "manual",
            "requiredCoupling": "Code_Saturne fluid-solid thermal interface continuity",
            "requiredEvidence": ["matching interface groups", "heat-flux continuity check", "per-domain convergence monitors"],
        },
        "thermalInputs": {
            "fluidTemperatureK": conditions.temperature,
            "density": conditions.density,
            "dynamicViscosity": conditions.dynamic_viscosity,
        },
        "manualNativeSetupRequired": [
            "Create native Code_Saturne fluid and solid domains or a reviewed coupled-domain setup.",
            "Generate or import solid-domain mesh groups matching the fluid interface groups.",
            "Define solid material properties and thermal coupling hooks before claiming CHT CFD.",
            "Add per-domain convergence and heat-flux continuity evidence to the run report.",
        ],
        "expectedPrimaryFields": ["pressure", "velocity", "fluid_temperature", "solid_temperature", "heat_flux"],
        "blockingReasons": [
            "FlowLab generates only the starter fluid mesh and scalar metadata; no solid-domain mesh is generated.",
            "Native Code_Saturne fluid-solid thermal coupling hooks are not generated.",
            "Solid material properties and interface heat-flux continuity evidence require native review.",
        ],
    }
    return (
        {"DATA/flowlab_cht_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            "Code_Saturne CHT mode exports a fluid/solid CHT handoff manifest for native setup review.",
            "The generated handoff is not automatically wired into a Code_Saturne coupled fluid-solid setup; Code_Saturne CHT remains blocked/export-only.",
        ],
    )


def _code_saturne_phase_handoff_files(
    project: dict[str, Any],
    conditions: CaseConditions,
    advanced_mode: str,
) -> tuple[dict[str, str], list[str]]:
    is_cavitation = advanced_mode == "cavitation"
    handoff = {
        "schema": "flowlab.code_saturne_phase_handoff.v1",
        "targetSolver": "code-saturne",
        "advancedMode": advanced_mode,
        "productionReady": False,
        "nativeCodeSaturneReady": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "phaseModel": "cavitation-phase-change" if is_cavitation else "multiphase-vof-free-surface",
        "starterFiles": {
            "setupXml": "DATA/setup.xml",
            "mesh": "MESH/flowlab_mesh.msh",
            "physicsHook": "DATA/cs_user_physics.py",
        },
        "phases": [
            {
                "name": "liquid",
                "material": "water-like liquid",
                "density": conditions.density,
                "dynamicViscosity": conditions.dynamic_viscosity,
            },
            {
                "name": "vapour" if is_cavitation else "gas",
                "material": "water vapour" if is_cavitation else "air-like gas",
                "densityStatus": "manual",
                "dynamicViscosityStatus": "manual",
            },
        ],
        "interfaceSetup": {
            "status": "manual",
            "requiredModel": "phase-change cavitation law" if is_cavitation else "VOF/free-surface interface capture",
            "initialization": "manual phase fraction and interface regions",
            "boundednessReviewRequired": True,
        },
        "cavitationInputs": (
            {
                "saturationPressure": conditions.vapor_pressure,
                "phaseChangeLaw": "manual",
                "pressureTreatment": "manual bounded pressure review",
            }
            if is_cavitation
            else None
        ),
        "manualNativeSetupRequired": [
            "Define native Code_Saturne phase material properties and initialization regions.",
            (
                "Add cavitation phase-change source terms, saturation pressure treatment, and bounded vapour-fraction output."
                if is_cavitation
                else "Add VOF/free-surface controls, interface compression/tracking choices, and bounded phase-fraction output."
            ),
            "Review transient timestep, interface boundedness, and post-processing fields before claiming native phase-resolved CFD.",
        ],
        "expectedPrimaryFields": (
            ["pressure", "velocity", "vapour_fraction", "cavitation_source"]
            if is_cavitation
            else ["pressure", "velocity", "phase_fraction", "interface_height"]
        ),
        "blockingReasons": [
            "FlowLab does not generate native Code_Saturne phase-model setup.xml controls.",
            "Phase material properties, initialization regions, and boundedness evidence require native review.",
            (
                "Cavitation phase-change law and saturation-pressure source terms are not generated."
                if is_cavitation
                else "VOF/free-surface interface-capturing controls are not generated."
            ),
        ],
    }
    if handoff["cavitationInputs"] is None:
        del handoff["cavitationInputs"]
    filename = "DATA/flowlab_cavitation_handoff.json" if is_cavitation else "DATA/flowlab_multiphase_handoff.json"
    label = "cavitation" if is_cavitation else "multiphase VOF"
    return (
        {filename: json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            f"Code_Saturne {label} mode exports a phase-physics handoff manifest for native setup review.",
            f"The generated handoff is not automatically wired into a Code_Saturne native {label} setup; requested phase physics remains blocked/export-only.",
        ],
    )


def _code_saturne_rigid_body_handoff_files(
    project: dict[str, Any],
    conditions: CaseConditions,
) -> tuple[dict[str, str], list[str]]:
    handoff = {
        "schema": "flowlab.code_saturne_rigid_body_handoff.v1",
        "targetSolver": "code-saturne",
        "advancedMode": "rigid-body-fluid-forces",
        "productionReady": False,
        "nativeCodeSaturneReady": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "starterFiles": {
            "setupXml": "DATA/setup.xml",
            "mesh": "MESH/flowlab_mesh.msh",
            "physicsHook": "DATA/cs_user_physics.py",
            "boundaryHook": "SRC/cs_user_boundary_conditions.f90",
        },
        "couplingIntent": {
            "status": "manual",
            "preferredCurrentSandbox": "mujoco",
            "requiredNativeSetup": "Code_Saturne moving mesh or external fluid-structure co-simulation bridge",
            "forceExchange": "manual pressure/shear integration and rigid-body state feedback",
            "referenceVelocity": conditions.inlet_velocity,
            "referenceDensity": conditions.density,
            "hydraulicDiameter": conditions.hydraulic_diameter,
        },
        "motionSetup": {
            "status": "manual",
            "meshMotion": "not generated",
            "bodyKinematics": "not generated",
            "requiredEvidence": ["moving-mesh quality history", "force/moment conservation check", "coupled timestep stability"],
        },
        "manualNativeSetupRequired": [
            "Use MuJoCo for the current phenomenological rigid-body force sandbox.",
            "Create native Code_Saturne moving-mesh or immersed/coupled-body setup before claiming fluid-structure CFD.",
            "Define pressure/shear force integration surfaces, rigid-body state exchange, and coupled timestep controls.",
            "Verify force/moment fields, mesh-motion quality, and conservation across the coupling interface.",
        ],
        "expectedPrimaryFields": ["pressure", "velocity", "body_force", "moment", "mesh_displacement"],
        "blockingReasons": [
            "FlowLab does not generate Code_Saturne moving-mesh or FSI coupling setup.",
            "Rigid-body state exchange with MuJoCo or another dynamics solver is not implemented.",
            "Force/moment integration surfaces and coupled timestep stability evidence require native review.",
        ],
    }
    return (
        {"DATA/flowlab_rigid_body_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            "Code_Saturne rigid-body-fluid-forces mode exports a moving-body/FSI handoff manifest for native setup review.",
            "The generated handoff is not wired into a Code_Saturne moving-mesh or co-simulation setup; coupled rigid-body CFD remains unresolved.",
        ],
    )


def _code_saturne_compressible_handoff_files(
    project: dict[str, Any],
    conditions: CaseConditions,
) -> tuple[dict[str, str], list[str]]:
    mach_estimate = conditions.inlet_velocity / max(math.sqrt(1.4 * 287.05 * conditions.temperature), 1.0e-9)
    handoff = {
        "schema": "flowlab.code_saturne_compressible_handoff.v1",
        "targetSolver": "code-saturne",
        "advancedMode": "compressible-flow",
        "productionReady": False,
        "nativeCodeSaturneReady": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "starterFiles": {
            "setupXml": "DATA/setup.xml",
            "mesh": "MESH/flowlab_mesh.msh",
            "physicsHook": "DATA/cs_user_physics.py",
            "boundaryHook": "SRC/cs_user_boundary_conditions.f90",
        },
        "starterSurrogate": {
            "status": "pressure-based-incompressible-surrogate",
            "setupXmlTurbulence": "k-epsilon",
            "density": conditions.density,
            "dynamicViscosity": conditions.dynamic_viscosity,
            "inletVelocity": conditions.inlet_velocity,
            "estimatedMach": round(mach_estimate, 9),
            "nativeStatus": "geometry, mesh, and boundary-condition review only; no native compressible module is generated",
        },
        "requiredNativeModules": [
            "compressible flow module",
            "equation of state",
            "total/static inlet-outlet boundary conditions",
            "density-pressure-energy coupling",
            "acoustic timestep or CFL controls",
        ],
        "thermodynamicSetup": {
            "status": "manual",
            "temperatureK": conditions.temperature,
            "referencePressurePa": conditions.outlet_pressure,
            "equationOfState": "manual",
            "energyEquation": "manual",
            "shockCapturingOrStabilization": "manual native review required",
        },
        "boundaryConditionReview": {
            "status": "manual",
            "inlet": "review total/static pressure, temperature, Mach, and flow direction in native Code_Saturne",
            "outlet": "review static pressure or characteristic outlet treatment",
            "walls": "review no-slip/thermal wall assumptions and compressible wall functions",
        },
        "expectedPrimaryFields": ["pressure", "velocity", "density", "temperature", "mach_number", "residual_history"],
        "manualNativeSetupRequired": [
            "Enable and review Code_Saturne compressible-flow module settings outside the starter setup.xml.",
            "Provide thermodynamic material laws, equation of state, and energy-equation settings.",
            "Replace the pressure-based surrogate boundary setup with reviewed compressible total/static boundary conditions.",
            "Add acoustic timestep/CFL controls, residual monitors, and density/temperature/Mach output verification.",
        ],
        "blockingReasons": [
            "FlowLab does not generate native Code_Saturne compressible-module controls.",
            "Equation-of-state, energy equation, and total/static boundary conditions require native review.",
            "Shock/acoustic timestep controls and Mach/density result verification are not generated.",
        ],
    }
    return (
        {"DATA/flowlab_compressible_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            "Code_Saturne compressible mode exports a compressible-flow handoff manifest for native setup review.",
            "The generated handoff is not wired into a native Code_Saturne compressible module; compressible CFD remains unresolved.",
        ],
    )


def _su2_water_hammer_files(project: dict[str, Any], conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    handoff = _water_hammer_handoff(project, conditions)
    handoff["targetSolver"] = "su2"
    handoff["nativeSu2Ready"] = False
    handoff["manualNativeSetupRequired"] = [
        "Map flowlab_su2_water_hammer_waveform.csv into a reviewed SU2 transient inlet pressure or characteristic boundary setup.",
        "Select a native transient compressible-liquid formulation and timestep controls before claiming water-hammer CFD.",
        "Add pipe-wall elasticity or a validated coupling model before relying on pressure-wave results.",
    ]
    return (
        {
            "flowlab_su2_water_hammer_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n",
            "flowlab_su2_water_hammer_waveform.csv": _water_hammer_waveform_csv(handoff),
        },
        [
            "SU2 water-hammer mode exports a Tier 1 MOC pressure-wave handoff JSON/CSV for native setup review.",
            "The generated handoff is not automatically wired into a SU2 transient compressible-liquid boundary setup; coupled water-hammer CFD remains unresolved.",
        ],
    )


def _su2_cht_handoff_files(project: dict[str, Any], conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    handoff = {
        "schema": "flowlab.su2_cht_handoff.v1",
        "targetSolver": "su2",
        "advancedMode": "conjugate-heat-transfer",
        "productionReady": False,
        "nativeSu2Ready": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "fluidZone": {
            "name": "fluid",
            "solver": "INC_NAVIER_STOKES",
            "mesh": "mesh/flowlab_mesh.su2",
            "requiredMarkers": ["inlet_*", "outlet_*", "wall_or_interface_*"],
            "expectedFields": ["pressure", "velocity", "fluid_temperature"],
        },
        "solidZone": {
            "name": "solid",
            "solver": "HEAT_EQUATION",
            "meshStatus": "not generated",
            "requiredInputs": ["solid mesh", "solid conductivity", "solid density", "solid heat capacity"],
            "expectedFields": ["solid_temperature", "heat_flux"],
        },
        "interface": {
            "status": "manual",
            "requiredCoupling": "SU2 multi-zone CHT interface coupling",
            "markers": ["fluid_solid_interface", "solid_fluid_interface"],
        },
        "thermalInputs": {
            "fluidTemperatureK": conditions.temperature,
            "density": conditions.density,
            "dynamicViscosity": conditions.dynamic_viscosity,
        },
        "manualNativeSetupRequired": [
            "Create a native SU2 MULTIZONE or coupled CHT configuration with separate fluid and solid zones.",
            "Generate or import a solid mesh with reviewed interface markers matching the fluid-side mesh.",
            "Define solid material properties and thermal interface coupling before claiming CHT CFD.",
            "Add per-zone convergence monitors and verify heat-flux continuity across the interface.",
        ],
        "expectedPrimaryFields": ["pressure", "velocity", "fluid_temperature", "solid_temperature", "heat_flux"],
        "blockingReasons": [
            "FlowLab exports only a single fluid starter mesh; no solid-zone SU2 mesh is generated.",
            "SU2 multi-zone CHT driver and interface coupling are not generated.",
            "Solid material properties and heat-flux continuity evidence require native review.",
        ],
    }
    return (
        {"flowlab_su2_cht_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            "SU2 CHT mode exports a multi-zone CHT handoff manifest for native setup review.",
            "The generated handoff is not automatically wired into a SU2 MULTIZONE CHT setup; SU2 CHT remains blocked/export-only.",
        ],
    )


def _su2_phase_handoff_files(
    project: dict[str, Any],
    conditions: CaseConditions,
    advanced_mode: str,
) -> tuple[dict[str, str], list[str]]:
    is_cavitation = advanced_mode == "cavitation"
    filename = "flowlab_su2_cavitation_handoff.json" if is_cavitation else "flowlab_su2_multiphase_handoff.json"
    handoff = {
        "schema": "flowlab.su2_phase_handoff.v1",
        "targetSolver": "su2",
        "advancedMode": advanced_mode,
        "productionReady": False,
        "nativeSu2Ready": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "phaseModel": "cavitation-phase-change" if is_cavitation else "multiphase-vof-free-surface",
        "starterFiles": {
            "caseCfg": "case.cfg",
            "mesh": "mesh/flowlab_mesh.su2",
            "nativeReviewTemplate": "flowlab_su2_native_config_template.cfg",
        },
        "phases": [
            {
                "name": "liquid",
                "material": "water-like liquid",
                "density": conditions.density,
                "dynamicViscosity": conditions.dynamic_viscosity,
            },
            {
                "name": "vapour" if is_cavitation else "gas",
                "material": "water vapour" if is_cavitation else "air-like gas",
                "densityStatus": "manual",
                "dynamicViscosityStatus": "manual",
            },
        ],
        "interfaceSetup": {
            "status": "manual",
            "requiredModel": "phase-change cavitation law" if is_cavitation else "VOF/free-surface interface capture",
            "initialization": "manual phase fraction and interface regions",
            "boundednessReviewRequired": True,
        },
        "cavitationInputs": (
            {
                "saturationPressure": conditions.vapor_pressure,
                "phaseChangeLaw": "manual",
                "pressureTreatment": "manual bounded pressure and vapour-fraction review",
            }
            if is_cavitation
            else None
        ),
        "manualNativeSetupRequired": [
            "Replace the guarded FLOWLAB_UNSUPPORTED_MODE case.cfg with a reviewed native SU2 phase setup.",
            "Define phase-specific material laws, initialization regions, and transient controls.",
            (
                "Add saturation pressure, phase-change source terms, and bounded vapour-fraction output."
                if is_cavitation
                else "Add VOF/free-surface interface tracking, interface compression choices, and bounded phase-fraction output."
            ),
            "Verify expected result fields before treating the SU2 run as phase-resolved CFD.",
        ],
        "expectedPrimaryFields": (
            ["pressure", "velocity", "vapour_fraction", "cavitation_source"]
            if is_cavitation
            else ["pressure", "velocity", "phase_fraction", "interface_height"]
        ),
        "blockingReasons": [
            "FlowLab does not generate a native SU2 phase solver configuration.",
            "Phase material properties, initialization regions, and boundedness evidence require native review.",
            (
                "Cavitation phase-change law and saturation-pressure source terms are not generated."
                if is_cavitation
                else "VOF/free-surface interface-capturing controls are not generated."
            ),
        ],
    }
    if handoff["cavitationInputs"] is None:
        del handoff["cavitationInputs"]
    label = "cavitation" if is_cavitation else "multiphase VOF"
    return (
        {filename: json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            f"SU2 {label} mode exports a phase-physics handoff manifest for native setup review.",
            f"The generated handoff is not wired into a native SU2 {label} setup; requested phase physics remains blocked/export-only.",
        ],
    )


def _su2_rigid_body_handoff_files(project: dict[str, Any], conditions: CaseConditions) -> tuple[dict[str, str], list[str]]:
    handoff = {
        "schema": "flowlab.su2_rigid_body_handoff.v1",
        "targetSolver": "su2",
        "advancedMode": "rigid-body-fluid-forces",
        "productionReady": False,
        "nativeSu2Ready": False,
        "projectName": str(project.get("name") or "FlowLab project"),
        "starterFiles": {
            "caseCfg": "case.cfg",
            "mesh": "mesh/flowlab_mesh.su2",
            "nativeReviewTemplate": "flowlab_su2_native_config_template.cfg",
        },
        "couplingIntent": {
            "status": "manual",
            "preferredCurrentSandbox": "mujoco",
            "su2RequiredSetup": ["moving mesh or FSI", "body-motion law", "force/moment output", "co-simulation exchange"],
            "referenceVelocity": conditions.inlet_velocity,
            "referenceArea": conditions.reference_area,
        },
        "motionSetup": {
            "status": "manual",
            "movingMesh": "manual",
            "rigidBodyDegreesOfFreedom": "manual",
            "meshDeformationQualityEvidence": "manual",
        },
        "manualNativeSetupRequired": [
            "Use MuJoCo for FlowLab's current phenomenological rigid-body fluid-force sandbox.",
            "Add a native SU2 moving-mesh/FSI setup or a reviewed MuJoCo/SU2 co-simulation bridge before claiming coupled CFD.",
            "Define rigid-body motion constraints, force/moment exchange, timestep coupling, and mesh deformation quality checks.",
        ],
        "expectedPrimaryFields": ["pressure", "velocity", "body_force", "moment"],
        "blockingReasons": [
            "FlowLab does not generate SU2 moving-mesh or FSI controls.",
            "MuJoCo/SU2 co-simulation exchange is not implemented.",
            "Rigid-body motion constraints and force/moment verification require native review.",
        ],
    }
    return (
        {"flowlab_su2_rigid_body_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n"},
        [
            "SU2 rigid-body-fluid-forces mode exports a moving-body/FSI handoff manifest for native setup review.",
            "The generated handoff is not wired into SU2 moving-mesh, FSI, or MuJoCo co-simulation; coupled rigid-body CFD remains unresolved.",
        ],
    )


def _openfoam_cht_region_mesh_check_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

echo "FlowLab OpenFOAM CHT region mesh check: fluid"
checkMesh -region fluid -allGeometry -allTopology

echo "FlowLab OpenFOAM CHT region mesh check: solid"
checkMesh -region solid -allGeometry -allTopology
"""


def _openfoam_mode_files(
    advanced_mode: str,
    conditions: CaseConditions | None = None,
    project: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[str]]:
    conditions = conditions or CaseConditions()
    turbulent = advanced_mode in {
        "conjugate-heat-transfer",
        "cavitation",
    }
    files: dict[str, str] = {
        "constant/momentumTransport": _openfoam_momentum_transport(turbulent),
        "constant/g": _openfoam_gravity(),
    }
    notes = ["OpenFOAM template includes mode-specific transport, turbulence, and gravity dictionaries."]

    if turbulent:
        files["0/k"] = _openfoam_scalar_field("k", "[0 2 -2 0 0 0 0]", "0.001", "0.001")
        files["0/omega"] = _openfoam_scalar_field("omega", "[0 0 -1 0 0 0 0]", "10", "10")
        files["0/nut"] = _openfoam_calculated_scalar_field("nut", "[0 2 -1 0 0 0 0]", "0")
        notes.append("RANS preset uses kOmegaSST starter fields for turbulent advanced modes.")

    if advanced_mode in {"compressible-flow", "heat-transfer", "conjugate-heat-transfer"}:
        density = _openfoam_ideal_gas_density(conditions) if advanced_mode == "compressible-flow" else conditions.density
        files["0/rho"] = _openfoam_scalar_field("rho", "[1 -3 0 0 0 0 0]", f"{density:.9g}", f"{density:.9g}")
        if turbulent:
            files["0/alphat"] = _openfoam_calculated_scalar_field("alphat", "[1 -1 -1 0 0 0 0]", "0")
        files["constant/thermophysicalProperties"] = _openfoam_thermophysical_properties(advanced_mode)
        notes.append(
            "Compressible/thermal modes include rho and thermophysicalProperties starter files; turbulent variants also include alphat."
        )

    if advanced_mode == "conjugate-heat-transfer":
        region_files, region_notes = _openfoam_cht_region_files(conditions)
        files.update(region_files)
        files["AllmeshCheck"] = _openfoam_cht_region_mesh_check_script()
        notes.extend(region_notes)

    if advanced_mode == "multiphase-vof":
        files["0/alpha.water"] = _openfoam_scalar_field("alpha.water", "[0 0 0 0 0 0 0]", "1", "1")
        files["0/alpha.air"] = _openfoam_scalar_field("alpha.air", "[0 0 0 0 0 0 0]", "0", "0")
        files["0/rho"] = _openfoam_scalar_field("rho", "[1 -3 0 0 0 0 0]", f"{conditions.density:.9g}", f"{conditions.density:.9g}")
        files["constant/phaseProperties"] = _openfoam_phase_properties()
        files["constant/physicalProperties.water"] = _openfoam_phase_physical_properties("water", conditions)
        files["constant/physicalProperties.air"] = _openfoam_phase_physical_properties("air", conditions)
        notes.append("VOF mode includes phase fractions, density, phaseProperties, and surface tension.")

    if advanced_mode == "cavitation":
        files["0/alpha.vapour"] = _openfoam_scalar_field("alpha.vapour", "[0 0 0 0 0 0 0]", "0", "0")
        files["0/rho"] = _openfoam_scalar_field("rho", "[1 -3 0 0 0 0 0]", f"{conditions.density:.9g}", f"{conditions.density:.9g}")
        files["constant/phaseProperties"] = _openfoam_cavitation_phase_properties()
        files["constant/physicalProperties.water"] = _openfoam_cavitation_physical_properties("water", conditions)
        files["constant/physicalProperties.vapour"] = _openfoam_cavitation_physical_properties("vapour", conditions)
        files["constant/thermodynamicProperties"] = _openfoam_cavitation_thermodynamic_properties(conditions)
        files["constant/fvModels"] = _openfoam_cavitation_fv_models(conditions)
        files["constant/cavitationProperties"] = (
            _foam_header("dictionary", "cavitationProperties")
            + f"""model           SchnerrSauer;
pSat            [1 -1 -2 0 0 0 0] {conditions.vapor_pressure:.9g};
n               1.6e+13;
dNuc            2.0e-06;
Cc              1;
Cv              1;
"""
        )
        notes.append(
            "Cavitation mode includes vapour/water phase fractions, thermo phase dictionaries, "
            "thermodynamicProperties, and a compressible::VoFCavitation fvModel."
        )

    if advanced_mode == "water-hammer":
        handoff = _water_hammer_handoff(project or {}, conditions)
        files["constant/waterHammerPreview.json"] = json.dumps(handoff, indent=2, sort_keys=True) + "\n"
        files["constant/waterHammerWaveform.csv"] = _water_hammer_waveform_csv(handoff)
        files["0/p"] = _openfoam_water_hammer_pressure_field(conditions, handoff)
        notes.append(
            "Water-hammer mode records a computed Tier 1 MOC preview and maps its dominant Joukowsky pressure rise "
            "to a transient OpenFOAM inlet pressure-wave boundary table; full CFD coupling remains pending."
        )

    return files, notes


def _openfoam_parallel_settings(project: dict[str, Any], advanced_mode: str) -> dict[str, Any]:
    """Validate the deliberately narrow OpenFOAM parallel execution opt-in.

    A serial run remains the default and the only configuration available for
    unreviewed advanced modes.  This keeps the performance feature from
    silently widening the current production envelope.
    """

    solver_settings = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    performance = solver_settings.get("performance") if isinstance(solver_settings.get("performance"), dict) else {}
    parallel = performance.get("openfoamParallel")
    if parallel is None:
        return {"enabled": False, "ranks": 1, "decomposition": None}
    if not isinstance(parallel, dict):
        raise ValueError("solver.performance.openfoamParallel must be an object")
    enabled = parallel.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("solver.performance.openfoamParallel.enabled must be a boolean")
    if not enabled:
        return {"enabled": False, "ranks": 1, "decomposition": None}
    if advanced_mode != "incompressible-navier-stokes":
        raise ValueError(
            "OpenFOAM parallel execution is currently limited to incompressible-navier-stokes; "
            "other modes must remain serial until separately verified."
        )
    ranks = parallel.get("ranks")
    if not isinstance(ranks, int) or isinstance(ranks, bool) or not 2 <= ranks <= 256:
        raise ValueError("solver.performance.openfoamParallel.ranks must be an integer from 2 through 256")
    decomposition = parallel.get("decomposition", "scotch")
    if decomposition != "scotch":
        raise ValueError(
            "solver.performance.openfoamParallel.decomposition currently supports only scotch"
        )
    return {"enabled": True, "ranks": ranks, "decomposition": decomposition}


def _openfoam_parallel_plan(parallel_settings: dict[str, Any]) -> dict[str, Any]:
    if parallel_settings.get("enabled"):
        return build_openfoam_parallel_plan(
            ranks=int(parallel_settings["ranks"]),
            decomposition=str(parallel_settings["decomposition"]),
        )
    return {
        "schema": "flowlab.openfoam-parallel-plan.v1",
        "execution": "serial-baseline",
        "ranks": 1,
        "decomposition": None,
        "scientificStatus": "does-not-change-physics-evidence",
        "performanceStatus": "serial-baseline-required",
        "speedupClaim": None,
        "requiredMeasurements": [
            "wallClockSeconds",
            "cpuSeconds",
            "peakResidentMemoryBytes",
            "iterationCount",
            "solverVersionAndContainerDigest",
        ],
        "guardrails": [
            "use this case as the same-mesh, same-numerics serial baseline for a later parallel comparison",
            "do not infer scientific equivalence from elapsed time alone",
        ],
    }


def _openfoam_decompose_par_dict(parallel_settings: dict[str, Any]) -> str:
    ranks = int(parallel_settings["ranks"])
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
| FlowLab narrow-envelope parallel decomposition                              |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      decomposeParDict;
}}

numberOfSubdomains {ranks};
method          scotch;
distributed     no;
roots           ();
"""


def _openfoam_allrun(
    solver: str,
    fitted_poly_mesh: bool = False,
    guarded_preflight: bool = False,
    parallel_settings: dict[str, Any] | None = None,
) -> str:
    parallel_settings = parallel_settings or {"enabled": False}
    mesh_step = (
        """if [ -f constant/polyMesh/points ] && [ -f constant/polyMesh/faces ] && [ -f constant/polyMesh/owner ] && [ -f constant/polyMesh/boundary ]; then
  echo "FlowLab OpenFOAM run: using fitted constant/polyMesh"
else
  echo "FlowLab OpenFOAM run: blockMesh fallback"
  blockMesh
fi"""
        if fitted_poly_mesh
        else """if [ -f constant/polyMesh/points ] && [ -f constant/polyMesh/faces ] && [ -f constant/polyMesh/owner ] && [ -f constant/polyMesh/boundary ]; then
  echo "FlowLab OpenFOAM run: using existing blockMesh polyMesh"
else
  echo "FlowLab OpenFOAM run: blockMesh"
  blockMesh
fi"""
    )
    if solver == "foamMultiRun" and guarded_preflight:
        run_step = """echo "FlowLab OpenFOAM CHT preflight complete; full foamMultiRun remains blocked."
echo "Run bash AllmeshCheck for per-region mesh checks. Promote the interface manifest to productionReady=true before enabling full CHT execution."
exit 64"""
    elif parallel_settings.get("enabled"):
        ranks = int(parallel_settings["ranks"])
        run_step = f"""echo "FlowLab OpenFOAM parallel candidate: decomposePar ({ranks} ranks, scotch)"
decomposePar -force
echo "FlowLab OpenFOAM parallel mesh check: mpirun -np {ranks} checkMesh -parallel -allGeometry -allTopology"
mpirun -np {ranks} checkMesh -parallel -allGeometry -allTopology | tee log.checkMesh.parallel
echo "FlowLab OpenFOAM parallel run: mpirun -np {ranks} foamRun -solver {solver} -parallel"
mpirun -np {ranks} foamRun -solver {solver} -parallel | tee log.foamRun.parallel
echo "FlowLab OpenFOAM parallel post-process: reconstructPar -latestTime"
reconstructPar -latestTime"""
    else:
        run_step = (
            """echo "FlowLab OpenFOAM run: foamMultiRun"
foamMultiRun"""
            if solver == "foamMultiRun"
            else f"""echo "FlowLab OpenFOAM run: foamRun -solver {solver}"
foamRun -solver {solver}"""
        )
    return f"""#!/usr/bin/env bash
set -euo pipefail

{mesh_step}

echo "FlowLab OpenFOAM mesh check: checkMesh -allGeometry -allTopology"
checkMesh -allGeometry -allTopology

{run_step}

if command -v foamToVTK >/dev/null 2>&1; then
  echo "FlowLab OpenFOAM post-process: foamToVTK -ascii -latestTime"
  foamToVTK -ascii -latestTime || true
fi
"""


def _code_saturne_setup_xml(advanced_mode: str, conditions: CaseConditions | None = None) -> str:
    conditions = conditions or CaseConditions()
    thermal_model = "temperature_celsius" if advanced_mode in {"heat-transfer", "conjugate-heat-transfer"} else "off"
    turbulence_model = "k-epsilon" if advanced_mode not in {"incompressible-navier-stokes", "water-hammer"} else "off"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Code_Saturne_GUI study="FlowLab" case="FlowLabCase" version="2.0">
  <solution_domain>
    <mesh_input path="MESH/flowlab_mesh.msh"/>
    <mesh_origin>FlowLab Gmsh 2.2 ASCII export</mesh_origin>
    <volumic_conditions>
      <zone label="all_cells" id="1" initialization="on" head_losses="off" porosity="off" momentum_source_term="off">all[]</zone>
    </volumic_conditions>
  </solution_domain>
  <analysis_control>
    <time_parameters>
      <iterations>25</iterations>
      <time_step>0.001</time_step>
    </time_parameters>
    <output_control>
      <writer id="default" type="ensight" frequency="5"/>
    </output_control>
  </analysis_control>
  <thermophysical_models>
    <turbulence model="{turbulence_model}"/>
    <thermal_scalar model="{thermal_model}"/>
  </thermophysical_models>
  <physical_properties>
    <fluid density="{conditions.density:.9g}" dynamic_viscosity="{conditions.dynamic_viscosity:.9g}"/>
    <reference pressure="{conditions.outlet_pressure:.9g}"/>
  </physical_properties>
  <boundary_conditions>
    <boundary label="flowlab_inlet" name="1" nature="inlet">2</boundary>
    <inlet label="flowlab_inlet">
      <velocity_pressure choice="norm" direction="coordinates">
        <norm>{conditions.inlet_velocity:.9g}</norm>
        <direction_x>1</direction_x>
        <direction_y>0</direction_y>
        <direction_z>0</direction_z>
      </velocity_pressure>
      <turbulence choice="hydraulic_diameter">
        <hydraulic_diameter>{conditions.hydraulic_diameter:.9g}</hydraulic_diameter>
      </turbulence>
    </inlet>
    <boundary label="flowlab_outlet" name="2" nature="outlet">3</boundary>
    <outlet label="flowlab_outlet">
      <dirichlet name="pressure">{conditions.outlet_gauge_pressure:.9g}</dirichlet>
    </outlet>
    <boundary label="flowlab_wall_left" name="3" nature="wall">4</boundary>
    <wall label="flowlab_wall_left">
      <velocity_pressure choice="off"/>
    </wall>
    <boundary label="flowlab_wall_right" name="4" nature="wall">5</boundary>
    <wall label="flowlab_wall_right">
      <velocity_pressure choice="off"/>
    </wall>
    <boundary label="flowlab_wall_front_back" name="5" nature="wall">6</boundary>
    <wall label="flowlab_wall_front_back">
      <velocity_pressure choice="off"/>
    </wall>
  </boundary_conditions>
</Code_Saturne_GUI>
"""


def _code_saturne_turbulence_plan(advanced_mode: str) -> dict[str, Any]:
    model = "off" if advanced_mode in {"incompressible-navier-stokes", "water-hammer"} else "k-epsilon"
    if model == "off":
        return {
            "schema": "flowlab.code_saturne_turbulence_plan.v1",
            "model": "off",
            "starterStatus": "laminar-starter",
            "productionReady": False,
            "wallTreatment": "not applicable for the laminar starter",
            "requiredEvidence": ["laminar-regime justification", "mesh-quality review", "result-field verification"],
            "unresolvedModels": ["RANS turbulence closure", "LES", "DNS", "transition model"],
            "notes": [
                "FlowLab keeps this Code_Saturne starter case laminar unless the selected advanced preset explicitly requests a turbulence starter.",
                "Laminar starter support does not prove transition, RANS, LES, or DNS validity.",
            ],
        }
    return {
        "schema": "flowlab.code_saturne_turbulence_plan.v1",
        "model": "k-epsilon",
        "starterStatus": "rans-starter",
        "productionReady": False,
        "wallTreatment": "hydraulic-diameter inlet turbulence estimate; wall-function/y-plus evidence not generated",
        "requiredEvidence": ["native turbulence setup review", "near-wall y-plus evidence", "mesh-independence study", "residual and field convergence"],
        "unresolvedModels": ["validated wall functions", "LES", "DNS", "transition model", "Reynolds-stress model"],
        "notes": [
            "The generated Code_Saturne setup.xml selects k-epsilon as a starter RANS closure for reviewable advanced cases.",
            "FlowLab does not yet generate production wall-layer/y-plus evidence or higher-fidelity turbulence setups.",
        ],
    }


def _code_saturne_physics_preset(advanced_mode: str, conditions: CaseConditions | None = None) -> dict[str, Any]:
    conditions = conditions or CaseConditions()
    thermal_model = "temperature_celsius" if advanced_mode in {"heat-transfer", "conjugate-heat-transfer"} else "off"
    turbulence_model = "k-epsilon" if advanced_mode not in {"incompressible-navier-stokes", "water-hammer"} else "off"
    base = {
        "schema": "flowlab.code_saturne_physics_preset.v1",
        "advancedMode": advanced_mode,
        "productionReady": False,
        "fluid": {
            "density": conditions.density,
            "dynamicViscosity": conditions.dynamic_viscosity,
            "temperature": conditions.temperature,
            "vaporPressure": conditions.vapor_pressure,
            "bulkModulus": conditions.bulk_modulus,
            "inletVelocity": conditions.inlet_velocity,
            "hydraulicDiameter": conditions.hydraulic_diameter,
            "outletPressure": conditions.outlet_pressure,
        },
        "starterXml": {
            "setup": "DATA/setup.xml",
            "mesh": "MESH/flowlab_mesh.msh",
            "boundaryHook": "SRC/cs_user_boundary_conditions.f90",
        },
        "setupXmlModels": {
            "turbulence": turbulence_model,
            "thermalScalar": thermal_model,
            "volumeZones": ["all_cells"],
            "boundaryZones": ["flowlab_inlet", "flowlab_outlet", "flowlab_wall_left", "flowlab_wall_right", "flowlab_wall_front_back"],
            "outputWriter": "ensight",
        },
        "turbulencePlan": _code_saturne_turbulence_plan(advanced_mode),
        "resultExpectations": {
            "nativeOutputRoot": "RESU/",
            "fieldOutput": "EnSight Gold postprocessing when Code_Saturne writes RESULTS_FLUID_DOMAIN.case",
            "flowlabConversion": "starter hexa8 fluid-domain EnSight fields are converted to bounded legacy VTK for browser visualization",
            "expectedPrimaryFields": ["pressure", "velocity"],
        },
        "nativeSetupPlan": {
            "status": "starter-generated",
            "setupXmlGenerated": True,
            "meshImport": "Gmsh 2.2 physical groups",
            "userHooksGenerated": ["DATA/cs_user_scripts.py", "DATA/cs_user_physics.py", "SRC/cs_user_boundary_conditions.f90"],
            "manualNativeModules": [],
            "transientControlsRequired": False,
            "materialReviewRequired": True,
        },
        "requestedPhysics": [],
        "enabledStarterModels": ["3D finite-volume Navier-Stokes", "constant-property fluid", "velocity inlet", "pressure outlet"],
        "blockedOrManualModels": [],
        "manualSetupRequirements": [],
        "readinessChecks": [
            {"id": "gmsh-physical-groups", "status": "pass", "detail": "Generated Gmsh mesh includes deterministic inlet, outlet, wall, and fluid physical groups."},
            {"id": "single-fluid-volume-zone", "status": "pass", "detail": "setup.xml initializes one all_cells fluid zone."},
            {"id": "production-mesh-review", "status": "fail", "detail": "FlowLab mesh is a deterministic starter mesh, not CAD-grade production meshing."},
        ],
        "notes": [
            "This preset is an explicit FlowLab-to-Code_Saturne physics map for generated-case review.",
            "The runnable starter case remains intentionally conservative; industrial Code_Saturne studies should review setup.xml, user hooks, and mesh quality before relying on results.",
        ],
    }
    mode_updates: dict[str, dict[str, Any]] = {
        "incompressible-navier-stokes": {
            "supportLevel": "starter-supported",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
            "requestedPhysics": ["incompressible Navier-Stokes"],
            "enabledStarterModels": ["laminar constant-density flow"],
            "readinessChecks": [
                {"id": "incompressible-starter-model", "status": "pass", "detail": "setup.xml represents a constant-property incompressible starter case."},
            ],
        },
        "heat-transfer": {
            "supportLevel": "starter-supported",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
            "requestedPhysics": ["incompressible Navier-Stokes", "passive thermal scalar"],
            "enabledStarterModels": ["temperature_celsius thermal scalar", "constant-property fluid", "wall thermal placeholder"],
            "setupXmlModels": {"energyModel": "temperature_celsius scalar"},
            "thermalStarter": {
                "scalarName": "temperature_celsius",
                "model": "passive scalar in starter setup.xml",
                "inletTemperatureK": conditions.temperature,
                "inletTemperatureC": conditions.temperature - 273.15,
                "initialTemperatureK": conditions.temperature,
                "initialTemperatureC": conditions.temperature - 273.15,
                "materialModel": "constant-property fluid; thermal properties require native review",
                "nativeStatus": "starter scalar only, not production thermal validation",
            },
            "thermalBoundaryPlan": {
                "inlet": "initialize temperature_celsius from FlowLab fluid temperature",
                "outlet": "advective/outlet behavior left to native Code_Saturne review",
                "walls": "adiabatic placeholder until wall heat flux or temperature is specified",
                "excludedPhysics": [
                    "fluid-solid conjugate heat transfer",
                    "radiation",
                    "buoyancy validation",
                    "phase change",
                ],
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "temperature"]},
            "readinessChecks": [
                {"id": "thermal-scalar-enabled", "status": "pass", "detail": "setup.xml enables the temperature_celsius thermal scalar starter model."},
                {"id": "thermal-boundary-plan", "status": "warning", "detail": "Thermal boundaries are documented as starter inlet/outlet/wall placeholders requiring native review."},
                {"id": "thermal-material-review", "status": "warning", "detail": "Generated thermal scalar is a starter scalar, not a validated conjugate or radiation heat-transfer setup."},
            ],
        },
        "compressible-flow": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["compressible Navier-Stokes"],
            "enabledStarterModels": ["k-epsilon turbulence starter", "constant-property pressure-based flow surrogate"],
            "blockedOrManualModels": ["native Code_Saturne compressible module setup", "thermodynamic equation-of-state configuration"],
            "setupXmlModels": {
                "compressibility": "manual-native-module",
                "equationOfState": "manual",
                "handoffArtifacts": ["DATA/flowlab_compressible_handoff.json"],
            },
            "nativeSetupPlan": {
                "manualNativeModules": ["compressible flow module", "equation of state", "compressible boundary-condition review"],
                "handoffArtifacts": ["DATA/flowlab_compressible_handoff.json"],
                "materialReviewRequired": True,
                "transientControlsRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "density", "temperature", "mach_number"]},
            "manualSetupRequirements": ["Enable and review Code_Saturne compressible-flow module settings outside the starter setup.xml.", "Provide thermodynamic material laws, total/static boundary conditions, and compressibility convergence criteria."],
            "readinessChecks": [
                {"id": "compressible-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_compressible_handoff.json for Code_Saturne compressible-flow review."},
                {"id": "pressure-surrogate", "status": "warning", "detail": "Starter setup remains pressure-based and can only serve as a geometry/boundary-condition surrogate."},
                {"id": "compressible-module", "status": "fail", "detail": "Native compressible module configuration is not generated yet."},
                {"id": "equation-of-state", "status": "fail", "detail": "Equation-of-state and compressible material tables require manual setup."},
            ],
        },
        "multiphase-vof": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["multiphase free-surface flow"],
            "enabledStarterModels": ["k-epsilon turbulence starter"],
            "blockedOrManualModels": ["VOF/free-surface model setup", "phase property tables", "interface compression controls"],
            "setupXmlModels": {
                "multiphaseModel": "manual-VOF-or-free-surface",
                "phaseCount": "manual",
                "handoffArtifacts": ["DATA/flowlab_multiphase_handoff.json"],
            },
            "nativeSetupPlan": {
                "manualNativeModules": ["VOF/free-surface model", "phase material tables"],
                "handoffArtifacts": ["DATA/flowlab_multiphase_handoff.json"],
                "transientControlsRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "phase_fraction", "interface_height"]},
            "manualSetupRequirements": ["Create phase-specific material properties and free-surface/VOF controls in native Code_Saturne setup.", "Review interface tracking, initialization regions, and post-processing fields."],
            "readinessChecks": [
                {"id": "phase-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_multiphase_handoff.json for Code_Saturne multiphase review."},
                {"id": "vof-model", "status": "fail", "detail": "VOF/free-surface model setup is not generated."},
                {"id": "phase-properties", "status": "fail", "detail": "Phase property tables and initialization regions require manual setup."},
            ],
        },
        "cavitation": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["cavitation", "liquid-vapour phase change"],
            "enabledStarterModels": ["k-epsilon turbulence starter"],
            "blockedOrManualModels": ["phase-change cavitation law", "vapour/liquid material setup", "saturation pressure source terms"],
            "setupXmlModels": {
                "cavitationModel": "manual-phase-change",
                "saturationPressure": conditions.vapor_pressure,
                "handoffArtifacts": ["DATA/flowlab_cavitation_handoff.json"],
            },
            "nativeSetupPlan": {
                "manualNativeModules": ["liquid-vapour phase change", "bounded pressure treatment"],
                "handoffArtifacts": ["DATA/flowlab_cavitation_handoff.json"],
                "transientControlsRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "vapour_fraction", "cavitation_source"]},
            "manualSetupRequirements": ["Add liquid/vapour material definitions, saturation pressure, and a validated phase-change law in Code_Saturne.", "Confirm cavitation source terms, vapor fraction fields, and bounded pressure treatment before relying on results."],
            "readinessChecks": [
                {"id": "phase-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_cavitation_handoff.json for Code_Saturne cavitation review."},
                {"id": "phase-change-law", "status": "fail", "detail": "Phase-change cavitation law is not generated."},
                {"id": "saturation-pressure-source", "status": "fail", "detail": "Saturation pressure source terms require manual native setup."},
            ],
        },
        "conjugate-heat-transfer": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["fluid-solid conjugate heat transfer"],
            "enabledStarterModels": ["temperature_celsius thermal scalar", "k-epsilon turbulence starter"],
            "blockedOrManualModels": ["multi-domain solid/fluid coupling", "interface thermal continuity", "solid material zones"],
            "setupXmlModels": {
                "domainCoupling": "manual-fluid-solid",
                "solidZones": "manual",
                "thermalInterface": "manual",
                "handoffArtifacts": ["DATA/flowlab_cht_handoff.json"],
            },
            "nativeSetupPlan": {
                "manualNativeModules": ["multi-domain CHT", "solid material zones", "thermal interface coupling"],
                "handoffArtifacts": ["DATA/flowlab_cht_handoff.json"],
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["fluid_temperature", "solid_temperature", "heat_flux", "pressure", "velocity"]},
            "manualSetupRequirements": ["Create separate fluid and solid domains or coupled zones in native Code_Saturne.", "Define solid material properties and mapped thermal interface continuity before execution is considered true CHT."],
            "readinessChecks": [
                {"id": "cht-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_cht_handoff.json for Code_Saturne CHT review."},
                {"id": "solid-domain", "status": "fail", "detail": "Solid domain/zone generation is not implemented for Code_Saturne."},
                {"id": "thermal-interface", "status": "fail", "detail": "Mapped fluid-solid thermal continuity is not generated."},
            ],
        },
        "water-hammer": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["transient water-hammer pressure waves"],
            "enabledStarterModels": ["laminar constant-density flow", "Tier 1 MOC pressure waveform export"],
            "blockedOrManualModels": ["native transient pressure-wave boundary condition", "compressible-liquid transient coupling", "elastic pipe-wall model"],
            "setupXmlModels": {
                "transientPressureWave": "manual-native-boundary-from-export",
                "compressibleLiquid": "manual",
                "pipeElasticity": "manual",
                "handoffArtifacts": ["DATA/flowlab_water_hammer_handoff.json", "DATA/flowlab_water_hammer_waveform.csv"],
            },
            "nativeSetupPlan": {"manualNativeModules": ["transient pressure-wave boundary", "compressible liquid model", "pipe-wall elasticity"], "transientControlsRequired": True},
            "resultExpectations": {"expectedPrimaryFields": ["pressure_wave", "velocity", "wall_reaction", "wave_speed"]},
            "manualSetupRequirements": ["Map the Tier 1 MOC pressure waveform into a reviewed Code_Saturne transient boundary setup.", "Add compressible-liquid and pipe-wall elasticity assumptions before claiming coupled water-hammer CFD."],
            "readinessChecks": [
                {"id": "moc-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_water_hammer_handoff.json and DATA/flowlab_water_hammer_waveform.csv for Code_Saturne review."},
                {"id": "native-transient-boundary", "status": "fail", "detail": "The exported waveform is not automatically mapped into a native Code_Saturne transient pressure boundary condition."},
                {"id": "elastic-pipe-wall", "status": "fail", "detail": "Elastic pipe-wall coupling remains manual."},
            ],
        },
        "rigid-body-fluid-forces": {
            "supportLevel": "metadata-plus-handoff",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "requestedPhysics": ["rigid-body fluid-force coupling"],
            "enabledStarterModels": ["k-epsilon turbulence starter"],
            "blockedOrManualModels": ["moving mesh", "fluid-structure coupling", "MuJoCo co-simulation bridge"],
            "setupXmlModels": {
                "movingMesh": "manual",
                "fluidStructureCoupling": "manual",
                "handoffArtifacts": ["DATA/flowlab_rigid_body_handoff.json"],
            },
            "nativeSetupPlan": {
                "manualNativeModules": ["moving mesh", "fluid-structure coupling", "co-simulation bridge"],
                "handoffArtifacts": ["DATA/flowlab_rigid_body_handoff.json"],
                "transientControlsRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "body_force", "moment", "mesh_displacement"]},
            "manualSetupRequirements": ["Use MuJoCo for the current phenomenological rigid-body force sandbox.", "Add a native moving-mesh or co-simulation bridge before treating Code_Saturne as coupled rigid-body CFD."],
            "readinessChecks": [
                {"id": "rigid-body-handoff-export", "status": "pass", "detail": "FlowLab exports DATA/flowlab_rigid_body_handoff.json for Code_Saturne moving-body/FSI review."},
                {"id": "moving-mesh", "status": "fail", "detail": "Moving mesh setup is not generated."},
                {"id": "cosimulation-bridge", "status": "fail", "detail": "MuJoCo/Code_Saturne co-simulation bridge is not implemented."},
            ],
        },
    }
    update = mode_updates.get(advanced_mode, mode_updates["incompressible-navier-stokes"])
    for key, value in update.items():
        if key in {"enabledStarterModels", "blockedOrManualModels", "manualSetupRequirements", "readinessChecks"}:
            base[key] = [*base.get(key, []), *value]
        elif key in {"setupXmlModels", "resultExpectations", "nativeSetupPlan"} and isinstance(value, dict):
            base[key] = {**base.get(key, {}), **value}
        else:
            base[key] = value
    if base.get("requestedPhysicsResolved") is False:
        base["nativeSetupPlan"]["reviewTemplate"] = "DATA/flowlab_native_physics_review.py"
        base["readinessChecks"].append(
            {
                "id": "native-physics-review-template",
                "status": "pass",
                "detail": "FlowLab emits DATA/flowlab_native_physics_review.py as a guarded native Code_Saturne review template.",
            }
        )
    base["blockingReasons"] = [check["detail"] for check in base["readinessChecks"] if check.get("status") == "fail"]
    return base


def _code_saturne_native_setup_checklist(advanced_mode: str, conditions: CaseConditions | None = None) -> dict[str, Any]:
    preset = _code_saturne_physics_preset(advanced_mode, conditions)
    native_plan = preset.get("nativeSetupPlan") if isinstance(preset.get("nativeSetupPlan"), dict) else {}
    result_expectations = preset.get("resultExpectations") if isinstance(preset.get("resultExpectations"), dict) else {}
    thermal_starter = preset.get("thermalStarter") if isinstance(preset.get("thermalStarter"), dict) else None
    thermal_boundary_plan = preset.get("thermalBoundaryPlan") if isinstance(preset.get("thermalBoundaryPlan"), dict) else None
    turbulence_plan = preset.get("turbulencePlan") if isinstance(preset.get("turbulencePlan"), dict) else None
    manual_modules = [str(module) for module in native_plan.get("manualNativeModules", []) if str(module).strip()]
    manual_requirements = [str(item) for item in preset.get("manualSetupRequirements", []) if str(item).strip()]
    readiness_items = [
        check
        for check in preset.get("readinessChecks", [])
        if isinstance(check, dict) and check.get("status") in {"fail", "warning"}
    ]
    action_items = [
        {"kind": "native-module", "item": module}
        for module in manual_modules
    ] + [
        {"kind": "manual-setup", "item": requirement}
        for requirement in manual_requirements
    ]
    generated_files = [
        "DATA/setup.xml",
        "DATA/flowlab_physics_preset.json",
        "DATA/flowlab_native_setup_checklist.json",
        "DATA/cs_user_scripts.py",
        "DATA/cs_user_physics.py",
        "SRC/cs_user_boundary_conditions.f90",
        "MESH/flowlab_mesh.msh",
    ]
    if advanced_mode == "water-hammer":
        generated_files.extend(["DATA/flowlab_water_hammer_handoff.json", "DATA/flowlab_water_hammer_waveform.csv"])
    handoff_artifacts = native_plan.get("handoffArtifacts") if isinstance(native_plan.get("handoffArtifacts"), list) else []
    generated_files.extend(str(item) for item in handoff_artifacts if str(item).strip() and str(item) not in generated_files)
    review_template = native_plan.get("reviewTemplate")
    if isinstance(review_template, str) and review_template.strip() and review_template not in generated_files:
        generated_files.append(review_template)
    return {
        "schema": "flowlab.code_saturne_native_setup_checklist.v1",
        "advancedMode": advanced_mode,
        "supportLevel": preset.get("supportLevel"),
        "supportedByAdapter": preset.get("supportedByAdapter"),
        "requestedPhysicsResolved": preset.get("requestedPhysicsResolved"),
        "productionReady": False,
        "generatedFiles": generated_files,
        "nativeSetupPlan": native_plan,
        **({"turbulencePlan": turbulence_plan} if turbulence_plan else {}),
        **({"thermalStarter": thermal_starter} if thermal_starter else {}),
        **({"thermalBoundaryPlan": thermal_boundary_plan} if thermal_boundary_plan else {}),
        "actionItems": action_items,
        "readinessItems": readiness_items,
        "expectedPrimaryFields": result_expectations.get("expectedPrimaryFields", []),
        "notes": [
            "This checklist is generated from the FlowLab Code_Saturne physics preset.",
            "It separates starter files that FlowLab generated from native Code_Saturne modules that still require manual setup.",
            "Production readiness requires native solver configuration review, mesh-quality review, and result-field verification.",
        ],
    }


def _code_saturne_capability_matrix(active_mode: str, conditions: CaseConditions | None = None) -> dict[str, Any]:
    modes = [
        "incompressible-navier-stokes",
        "heat-transfer",
        "compressible-flow",
        "multiphase-vof",
        "cavitation",
        "conjugate-heat-transfer",
        "water-hammer",
        "rigid-body-fluid-forces",
    ]
    entries: list[dict[str, Any]] = []
    for mode in modes:
        preset = _code_saturne_physics_preset(mode, conditions)
        native_plan = preset.get("nativeSetupPlan") if isinstance(preset.get("nativeSetupPlan"), dict) else {}
        result_expectations = preset.get("resultExpectations") if isinstance(preset.get("resultExpectations"), dict) else {}
        readiness_checks = preset.get("readinessChecks") if isinstance(preset.get("readinessChecks"), list) else []
        entries.append(
            {
                "advancedMode": mode,
                "active": mode == active_mode,
                "supportLevel": preset.get("supportLevel"),
                "supportedByAdapter": preset.get("supportedByAdapter"),
                "requestedPhysicsResolved": preset.get("requestedPhysicsResolved"),
                "productionReady": False,
                "requestedPhysics": preset.get("requestedPhysics", []),
                "enabledStarterModels": preset.get("enabledStarterModels", []),
                "turbulenceModel": preset.get("turbulencePlan", {}).get("model") if isinstance(preset.get("turbulencePlan"), dict) else None,
                "turbulenceStarterStatus": preset.get("turbulencePlan", {}).get("starterStatus") if isinstance(preset.get("turbulencePlan"), dict) else None,
                "manualNativeModules": native_plan.get("manualNativeModules", []),
                "handoffArtifacts": native_plan.get("handoffArtifacts", []),
                "expectedPrimaryFields": result_expectations.get("expectedPrimaryFields", []),
                "readinessSummary": {
                    "pass": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "pass"),
                    "warning": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "warning"),
                    "fail": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "fail"),
                },
                "blockingReasons": preset.get("blockingReasons", []),
            }
        )
    return {
        "schema": "flowlab.code_saturne_capability_matrix.v1",
        "activeMode": active_mode,
        "productionReady": False,
        "entries": entries,
        "summary": {
            "modeCount": len(entries),
            "starterSupportedModes": [
                entry["advancedMode"]
                for entry in entries
                if entry.get("supportedByAdapter") is True and entry.get("requestedPhysicsResolved") is True
            ],
            "unresolvedModes": [
                entry["advancedMode"]
                for entry in entries
                if entry.get("requestedPhysicsResolved") is False
            ],
            "handoffModes": [
                entry["advancedMode"]
                for entry in entries
                if entry.get("supportLevel") == "metadata-plus-handoff"
            ],
        },
        "notes": [
            "This matrix summarizes FlowLab's Code_Saturne adapter coverage for generated-case review.",
            "Starter-supported means FlowLab can generate a runnable starter case, not production-grade Code_Saturne validation.",
            "Unresolved modes require the listed native modules, handoff review, mesh-quality evidence, and result-field verification before physics can be claimed resolved.",
        ],
    }


def _code_saturne_native_physics_review_script(preset: dict[str, Any], checklist: dict[str, Any]) -> str:
    payload = {
        "schema": "flowlab.code_saturne_native_physics_review.v1",
        "advancedMode": preset.get("advancedMode"),
        "supportLevel": preset.get("supportLevel"),
        "requestedPhysicsResolved": preset.get("requestedPhysicsResolved"),
        "productionReady": False,
        "requestedPhysics": preset.get("requestedPhysics", []),
        "manualNativeModules": (
            preset.get("nativeSetupPlan", {}).get("manualNativeModules", [])
            if isinstance(preset.get("nativeSetupPlan"), dict)
            else []
        ),
        "blockedOrManualModels": preset.get("blockedOrManualModels", []),
        "manualSetupRequirements": preset.get("manualSetupRequirements", []),
        "expectedPrimaryFields": checklist.get("expectedPrimaryFields", []),
        "handoffArtifacts": (
            preset.get("nativeSetupPlan", {}).get("handoffArtifacts", [])
            if isinstance(preset.get("nativeSetupPlan"), dict)
            else []
        ),
    }
    return f'''"""FlowLab native Code_Saturne physics review template.

This file is generated only for Code_Saturne modes where FlowLab has not
resolved the requested native physics. It is not imported by the starter
`code_saturne run` path. Use it as a compact review payload before writing
real Code_Saturne XML/user-hook changes.
"""

FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True
FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False
FLOWLAB_PRODUCTION_READY = False
FLOWLAB_NATIVE_REVIEW = {json.dumps(payload, indent=2, sort_keys=True)}


def describe_native_review():
    """Return unresolved native Code_Saturne setup requirements."""
    return FLOWLAB_NATIVE_REVIEW
'''


def _code_saturne_user_physics_script(advanced_mode: str, conditions: CaseConditions | None = None) -> str:
    preset = _code_saturne_physics_preset(advanced_mode, conditions)
    checklist = _code_saturne_native_setup_checklist(advanced_mode, conditions)
    return f'''"""FlowLab Code_Saturne physics preset hook.

The runnable starter case is configured through DATA/setup.xml. This file keeps
the requested FlowLab advanced-mode mapping visible for users who want to add
native Code_Saturne user hooks.
"""

FLOWLAB_CODE_SATURNE_PHYSICS_PRESET = {json.dumps(preset, indent=4, sort_keys=True)}
FLOWLAB_CODE_SATURNE_NATIVE_SETUP_CHECKLIST = {json.dumps(checklist, indent=4, sort_keys=True)}


def describe_flowlab_physics_preset():
    """Return the generated FlowLab physics preset metadata."""

    return FLOWLAB_CODE_SATURNE_PHYSICS_PRESET


def flowlab_readiness_summary():
    """Return failing or warning readiness checks for manual native setup review."""

    return [
        check for check in FLOWLAB_CODE_SATURNE_PHYSICS_PRESET.get("readinessChecks", [])
        if check.get("status") in {"fail", "warning"}
    ]


def flowlab_native_setup_checklist():
    """Return generated files, expected fields, and manual native setup actions."""

    return FLOWLAB_CODE_SATURNE_NATIVE_SETUP_CHECKLIST
'''


def _code_saturne_run_cfg() -> str:
    return """[setup]
parameters = setup.xml

[run]
id = flowlab
force = true
n_procs = 1
n_threads = 1
"""


def _code_saturne_boundary_conditions(conditions: CaseConditions | None = None) -> str:
    conditions = conditions or CaseConditions()
    return f"""! FlowLab starter boundary conditions for Code_Saturne 6.x.
!
! Code_Saturne 6.x's legacy incompressible path reliably calls this Fortran
! hook after GUI/XML boundary setup. Do not copy the packaged template guard
! which returns immediately when DATA/setup.xml is loaded.

subroutine cs_f_user_boundary_conditions &
 ( nvar, nscal, icodcl, itrifb, itypfb, izfppp, dt, rcodcl )

use paramx
use numvar
use mesh

implicit none

integer nvar, nscal
integer icodcl(nfabor,nvar)
integer itrifb(nfabor), itypfb(nfabor)
integer izfppp(nfabor)
double precision dt(ncelet)
double precision rcodcl(nfabor,nvar,3)

integer ifac
double precision xmin, xmax, span, tol, x

if (nfabor .le. 0) return

xmin = cdgfbo(1,1)
xmax = cdgfbo(1,1)

do ifac = 2, nfabor
  xmin = min(xmin, cdgfbo(1,ifac))
  xmax = max(xmax, cdgfbo(1,ifac))
enddo

span = max(xmax - xmin, 1.d0)
tol = max(1.d-6 * span, 1.d-9)

do ifac = 1, nfabor
  itypfb(ifac) = iparoi
enddo

do ifac = 1, nfabor
  x = cdgfbo(1,ifac)

  if (abs(x - xmin) .le. tol) then
    itypfb(ifac) = ientre
    rcodcl(ifac,iu,1) = {conditions.inlet_velocity:.9g}d0
    rcodcl(ifac,iv,1) = 0.d0
    rcodcl(ifac,iw,1) = 0.d0
  else if (abs(x - xmax) .le. tol) then
    itypfb(ifac) = isolib
  endif
enddo

return
end subroutine cs_f_user_boundary_conditions
"""


def _code_saturne_user_scripts() -> str:
    return '''"""FlowLab Code_Saturne runtime overrides.

This file is intentionally small. The generated setup.xml carries the starter
physics, while this script pins the mesh imported from the FlowLab case bundle.
"""

def define_domain_parameters(domain):
    """Hook called by Code_Saturne while preparing the calculation domain."""

    domain.mesh_input = None
    domain.mesh_dir = "MESH"
    domain.meshes = ["flowlab_mesh.msh"]
    domain.preprocess_on_restart = False
    domain.partition_input = None
    domain.restart_input = None
'''


def _code_saturne_readme(project_name: str, advanced_mode: str) -> str:
    return f"""# {project_name}

Generated Code_Saturne starter case for `{advanced_mode}`.

Run from this directory with:

```bash
code_saturne run
```

The case follows the standard Code_Saturne layout with `DATA`, `SRC`, `RESU`,
and `MESH` directories. `MESH/flowlab_mesh.msh` is a Gmsh 2.2 ASCII export of
the FlowLab port-aware mesh extruded to a thin hexahedral volume.
`SRC/cs_user_boundary_conditions.f90` assigns a starter inlet, outlet, and wall
set from deterministic mesh extents. `DATA/flowlab_physics_preset.json` and
`DATA/cs_user_physics.py` record the requested advanced-mode mapping and the
models that remain manual/native Code_Saturne work. This is a starter case for
local dependency validation and template inspection, not a production-quality
industrial mesh.
"""


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _python_module_exists(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _configured_mujoco_python() -> str | None:
    configured = os.environ.get("FLOWLAB_MUJOCO_PYTHON", "").strip()
    if not configured:
        return None
    return str(Path(configured).expanduser())


def _python_command() -> str | None:
    configured = _configured_mujoco_python()
    if configured and _command_exists(configured):
        return configured
    if _command_exists("python3"):
        return "python3"
    if _command_exists("python"):
        return "python"
    return None


def _python_module_exists_for_command(command: str | None, module: str) -> bool:
    if not command:
        return False
    configured = _configured_mujoco_python()
    if not configured or command in {"python3", "python", sys.executable}:
        return _python_module_exists(module)
    try:
        completed = subprocess.run(
            [command, "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _docker_available() -> bool:
    if not _command_exists("docker"):
        return False
    try:
        completed = subprocess.run(["docker", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except Exception:
        return False
    return completed.returncode == 0


def _su2_home() -> Path | None:
    home = os.environ.get("FLOWLAB_SU2_HOME")
    if not home:
        return None
    path = Path(home).expanduser().resolve()
    return path if (path / "bin" / "SU2_CFD").is_file() else None


def _code_saturne_image() -> str | None:
    image = os.environ.get("FLOWLAB_CODE_SATURNE_IMAGE", "").strip()
    return image or None


def _mujoco_xml(conditions: CaseConditions | None = None) -> str:
    conditions = conditions or CaseConditions()
    half_length = max(conditions.hydraulic_diameter, 0.02)
    half_width = max(conditions.hydraulic_diameter * 0.25, 0.005)
    half_depth = max(conditions.hydraulic_diameter * 0.25, 0.005)
    return f"""<mujoco model="flowlab-fluid-forces">
  <compiler angle="degree"/>
  <option timestep="0.002" integrator="implicitfast" density="{conditions.density:.9g}" viscosity="{conditions.dynamic_viscosity:.9g}"/>
  <default>
    <geom rgba="0.1 0.55 0.85 1" fluidshape="ellipsoid" fluidcoef="0.5 0.25 1.5 1.0 1.0"/>
  </default>
  <worldbody>
    <light name="key" pos="0 0 2"/>
    <body name="valve_body" pos="0 0 0">
      <freejoint name="valve_free"/>
      <geom name="valve_plate" type="box" size="{half_length:.9g} {half_width:.9g} {half_depth:.9g}" mass="0.25"/>
    </body>
  </worldbody>
</mujoco>
"""


def _mujoco_runner(conditions: CaseConditions | None = None) -> str:
    conditions = conditions or CaseConditions()
    template = '''#!/usr/bin/env python3
"""Run the FlowLab MuJoCo fluid-force sandbox and export a small VTK snapshot."""

from __future__ import annotations

import json
import math
from pathlib import Path

import mujoco


REFERENCE_VELOCITY = __FLOWLAB_REFERENCE_VELOCITY__
REFERENCE_AREA = __FLOWLAB_REFERENCE_AREA__


def _write_vtk(path: Path, velocity: float, passive_force: float) -> None:
    pressure_like = passive_force / max(REFERENCE_AREA, 1e-9)
    path.write_text(
        """# vtk DataFile Version 3.0
FlowLab MuJoCo fluid-force sandbox
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
-0.10 -0.025 0
0.10 -0.025 0
0.10 0.025 0
-0.10 0.025 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
{pressure:.8f}
{pressure:.8f}
{pressure:.8f}
{pressure:.8f}
VECTORS velocity float
{velocity:.8f} 0 0
{velocity:.8f} 0 0
{velocity:.8f} 0 0
{velocity:.8f} 0 0
""".format(pressure=pressure_like, velocity=velocity),
        encoding="utf-8",
    )


def main() -> None:
    case_dir = Path(__file__).resolve().parent
    outputs = case_dir / "outputs"
    outputs.mkdir(exist_ok=True)

    model = mujoco.MjModel.from_xml_path(str(case_dir / "model.xml"))
    data = mujoco.MjData(model)

    if model.nv >= 3:
        data.qvel[0] = REFERENCE_VELOCITY
        data.qvel[1] = 0.0
        data.qvel[2] = 0.0

    samples = []
    for step in range(120):
        mujoco.mj_step(model, data)
        passive_force = 0.0
        if model.nv:
            passive_force = math.sqrt(sum(float(value) ** 2 for value in data.qfrc_passive[: model.nv]))
        samples.append(
            {
                "step": step,
                "time": float(data.time),
                "position": [float(value) for value in data.qpos[: min(3, model.nq)]],
                "velocity": [float(value) for value in data.qvel[: min(3, model.nv)]],
                "passiveForceNorm": passive_force,
            }
        )

    final_velocity = samples[-1]["velocity"][0] if samples[-1]["velocity"] else 0.0
    final_force = samples[-1]["passiveForceNorm"]
    _write_vtk(outputs / "mujoco_fluid_force_0001.vtk", final_velocity, final_force)
    (outputs / "summary.json").write_text(
        json.dumps(
            {
                "solver": "mujoco",
                "model": "flowlab-fluid-forces",
                "steps": len(samples),
                "final": samples[-1],
                "note": "MuJoCo fluid forces are phenomenological rigid-body forces, not CFD field solves.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"FlowLab MuJoCo sandbox completed {len(samples)} steps")
    print(f"Wrote {outputs / 'mujoco_fluid_force_0001.vtk'}")


if __name__ == "__main__":
    main()
'''
    return (
        template.replace("__FLOWLAB_REFERENCE_VELOCITY__", f"{conditions.inlet_velocity:.9g}")
        .replace("__FLOWLAB_REFERENCE_AREA__", f"{conditions.reference_area:.9g}")
    )


def _su2_csv(values: list[str]) -> str:
    return ", ".join(values) if values else "NONE"


def _su2_number(value: float) -> str:
    formatted = f"{value:.9g}"
    if "e" not in formatted.lower() and "." not in formatted:
        formatted = f"{formatted}.0"
    return formatted


def _su2_marker_values(marker_tags: dict[str, list[str]]) -> tuple[str, str, str]:
    return _su2_csv(marker_tags["inlet"]), _su2_csv(marker_tags["outlet"]), _su2_csv(marker_tags["wall"])


def _su2_repeated_marker_values(markers: list[str], *values: str) -> str:
    if not markers:
        return "NONE"
    parts: list[str] = []
    for marker in markers:
        parts.extend([marker, *values])
    return ", ".join(parts)


def _su2_base_config(advanced_mode: str) -> list[str]:
    return [
        "% FlowLab native SU2 case generated from port-aware mesh v1.",
        f"% Advanced mode: {advanced_mode}",
        "MATH_PROBLEM= DIRECT",
        "MESH_FILENAME= mesh/flowlab_mesh.su2",
        "MESH_FORMAT= SU2",
        "RESTART_SOL= NO",
        "REF_DIMENSIONALIZATION= DIMENSIONAL",
        "TIME_DOMAIN= NO",
        "ITER= 50",
        "CONV_RESIDUAL_MINVAL= -8",
        "OUTPUT_FILES= (RESTART, PARAVIEW_ASCII)",
        "VOLUME_FILENAME= flowlab_su2",
        "CONV_FILENAME= history",
    ]


def _su2_unsupported_config(advanced_mode: str) -> tuple[str, list[str]]:
    config = "\n".join(
        [
            *(_su2_base_config(advanced_mode)),
            "SOLVER= NONE",
            "FLOWLAB_UNSUPPORTED_MODE= YES",
            (
                "% This FlowLab SU2 adapter currently supports incompressible, compressible, "
                "and single-zone heat-transfer starter cases only."
            ),
        ]
    )
    return config, [f"SU2 mode `{advanced_mode}` is blocked until FlowLab can generate the required multi-zone or multiphase setup."]


def _su2_blocked_mode_details(advanced_mode: str) -> dict[str, Any]:
    blocked_modes: dict[str, dict[str, Any]] = {
        "multiphase-vof": {
            "requiredCapabilities": ["multiphase free-surface flow", "interface capturing", "phase material properties"],
            "blockedOrManualModels": ["VOF/free-surface model generation", "phase property tables", "interface-capturing controls"],
            "manualSetupRequirements": [
                "Build a native SU2 multiphase/free-surface setup outside FlowLab's single-zone starter adapter.",
                "Define phase properties, initialization regions, interface-capturing controls, and bounded volume-fraction outputs.",
            ],
            "readinessChecks": [
                {"id": "phase-handoff-export", "status": "pass", "detail": "FlowLab exports flowlab_su2_multiphase_handoff.json for SU2 multiphase review."},
                {"id": "multiphase-solver", "status": "fail", "detail": "FlowLab does not generate a native SU2 multiphase solver configuration."},
                {"id": "phase-properties", "status": "fail", "detail": "Phase material tables and initialization regions require manual setup."},
            ],
            "nativeSetupPlan": {
                "manualNativeModules": ["multiphase solver", "VOF/free-surface interface capture", "phase material tables"],
                "handoffArtifacts": ["flowlab_su2_multiphase_handoff.json"],
                "multiZoneRequired": False,
                "transientControlsRequired": True,
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "phase_fraction", "interface_height"]},
        },
        "cavitation": {
            "requiredCapabilities": ["liquid-vapour cavitation", "phase-change law", "saturation pressure source terms"],
            "blockedOrManualModels": ["phase-change cavitation law", "vapour/liquid material setup", "saturation pressure source terms"],
            "manualSetupRequirements": [
                "Provide a reviewed native SU2 cavitation model setup with liquid/vapour material data.",
                "Define saturation pressure, phase-change source terms, bounded vapor fraction fields, and pressure treatment.",
            ],
            "readinessChecks": [
                {"id": "phase-handoff-export", "status": "pass", "detail": "FlowLab exports flowlab_su2_cavitation_handoff.json for SU2 cavitation review."},
                {"id": "cavitation-model", "status": "fail", "detail": "FlowLab does not generate a SU2 cavitation model or phase-change law."},
                {"id": "vapour-liquid-materials", "status": "fail", "detail": "Liquid/vapour material definitions and saturation pressure are manual."},
            ],
            "nativeSetupPlan": {
                "manualNativeModules": ["cavitation or phase-change model", "liquid/vapour material law", "saturation pressure source terms"],
                "handoffArtifacts": ["flowlab_su2_cavitation_handoff.json"],
                "multiZoneRequired": False,
                "transientControlsRequired": True,
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "vapour_fraction", "cavitation_source"]},
        },
        "water-hammer": {
            "requiredCapabilities": ["transient compressible-liquid pressure waves", "MOC/CFD boundary handoff", "pipe-wall elasticity"],
            "blockedOrManualModels": ["transient compressible-liquid pressure-wave setup", "pipe-wall elasticity coupling", "native SU2 MOC/CFD coupling"],
            "manualSetupRequirements": [
                "Map the Tier 1 MOC pressure waveform into a reviewed SU2 transient compressible-liquid boundary setup.",
                "Add pipe-wall elasticity assumptions and solver timestep controls before claiming coupled water-hammer CFD.",
            ],
            "readinessChecks": [
                {"id": "transient-liquid-model", "status": "fail", "detail": "FlowLab does not generate a SU2 transient compressible-liquid water-hammer setup."},
                {"id": "moc-boundary-handoff-export", "status": "pass", "detail": "FlowLab exports flowlab_su2_water_hammer_handoff.json and flowlab_su2_water_hammer_waveform.csv for SU2 review."},
                {"id": "native-moc-boundary", "status": "fail", "detail": "The exported waveform is not automatically mapped into a native SU2 transient boundary condition."},
                {"id": "elastic-pipe-wall", "status": "fail", "detail": "Pipe-wall elasticity coupling remains manual."},
            ],
            "nativeSetupPlan": {
                "manualNativeModules": ["transient compressible-liquid model", "MOC pressure-wave boundary", "pipe-wall elasticity coupling"],
                "handoffArtifacts": ["flowlab_su2_water_hammer_handoff.json", "flowlab_su2_water_hammer_waveform.csv"],
                "multiZoneRequired": False,
                "transientControlsRequired": True,
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure_wave", "velocity", "wave_speed", "wall_reaction"]},
        },
        "conjugate-heat-transfer": {
            "requiredCapabilities": ["multi-zone fluid/solid CHT", "fluid-solid thermal interface", "solid material zones"],
            "blockedOrManualModels": ["multi-zone fluid/solid setup", "interface thermal continuity", "solid material zones"],
            "manualSetupRequirements": [
                "Create a native SU2 multi-zone fluid/solid case with separate solid material zones.",
                "Define thermal interface continuity, solid conductivity/heat capacity, and per-zone convergence monitors.",
            ],
            "readinessChecks": [
                {"id": "cht-handoff-export", "status": "pass", "detail": "FlowLab exports flowlab_su2_cht_handoff.json for SU2 multi-zone CHT review."},
                {"id": "multi-zone-cht", "status": "fail", "detail": "FlowLab does not generate SU2 multi-zone fluid/solid CHT cases."},
                {"id": "thermal-interface", "status": "fail", "detail": "Fluid-solid thermal interface coupling is manual."},
                {"id": "solid-zone-mesh", "status": "fail", "detail": "FlowLab does not generate a native SU2 solid-zone mesh."},
            ],
            "nativeSetupPlan": {
                "manualNativeModules": ["multi-zone CHT driver", "fluid-solid interface coupling", "solid material zones"],
                "handoffArtifacts": ["flowlab_su2_cht_handoff.json"],
                "multiZoneRequired": True,
                "transientControlsRequired": False,
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "fluid_temperature", "solid_temperature", "heat_flux"]},
        },
        "rigid-body-fluid-forces": {
            "requiredCapabilities": ["moving mesh or FSI", "rigid-body co-simulation", "force/motion exchange"],
            "blockedOrManualModels": ["moving mesh or FSI coupling", "rigid-body co-simulation bridge", "phenomenological MuJoCo force mapping"],
            "manualSetupRequirements": [
                "Use the MuJoCo adapter for the current phenomenological rigid-body force sandbox.",
                "Add a native SU2 moving-mesh/FSI or co-simulation bridge before treating this as coupled rigid-body CFD.",
            ],
            "readinessChecks": [
                {"id": "rigid-body-handoff-export", "status": "pass", "detail": "FlowLab exports flowlab_su2_rigid_body_handoff.json for SU2 moving-body/FSI review."},
                {"id": "moving-mesh", "status": "fail", "detail": "SU2 moving-mesh/FSI setup is not generated."},
                {"id": "cosimulation-bridge", "status": "fail", "detail": "MuJoCo/SU2 co-simulation bridge is not implemented."},
            ],
            "nativeSetupPlan": {
                "manualNativeModules": ["moving mesh or FSI", "rigid-body motion exchange", "MuJoCo/SU2 co-simulation bridge"],
                "handoffArtifacts": ["flowlab_su2_rigid_body_handoff.json"],
                "multiZoneRequired": False,
                "transientControlsRequired": True,
                "materialReviewRequired": False,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "body_force", "moment"]},
        },
    }
    return blocked_modes.get(
        advanced_mode,
        {
            "requiredCapabilities": ["required SU2 setup is not mapped by FlowLab"],
            "blockedOrManualModels": ["required SU2 setup is not mapped by the FlowLab adapter yet"],
            "manualSetupRequirements": ["Review the requested SU2 physics manually and add native configuration outside FlowLab."],
            "readinessChecks": [{"id": "adapter-mapping", "status": "fail", "detail": "FlowLab has no SU2 mapping for this advanced mode."}],
            "nativeSetupPlan": {
                "manualNativeModules": ["unmapped SU2 physics setup"],
                "multiZoneRequired": False,
                "transientControlsRequired": False,
                "materialReviewRequired": True,
            },
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity"]},
        },
    )


def _su2_mode_preset(
    advanced_mode: str,
    conditions: CaseConditions | None = None,
    supported_mode: bool = False,
) -> dict[str, Any]:
    conditions = conditions or CaseConditions()
    preset: dict[str, Any] = {
        "schema": "flowlab.su2_mode_preset.v1",
        "advancedMode": advanced_mode,
        "productionReady": False,
        "supportedByAdapter": supported_mode,
        "requestedPhysicsResolved": supported_mode,
        "supportLevel": "blocked-export-only",
        "solver": None,
        "mesh": {
            "format": "SU2 ASCII",
            "file": "mesh/flowlab_mesh.su2",
            "dimension": 2,
            "quality": "FlowLab starter quad-strip mesh; not CAD-quality production meshing.",
        },
        "fluid": {
            "density": conditions.density,
            "dynamicViscosity": conditions.dynamic_viscosity,
            "temperature": conditions.temperature,
            "inletVelocity": conditions.inlet_velocity,
            "inletPressure": conditions.inlet_pressure,
            "outletPressure": conditions.outlet_pressure,
            "hydraulicDiameter": conditions.hydraulic_diameter,
        },
        "requiredCapabilities": [],
        "enabledStarterModels": [],
        "blockedOrManualModels": [],
        "manualSetupRequirements": [],
        "readinessChecks": [
            {"id": "native-su2-mesh", "status": "pass", "detail": "FlowLab exports a native ASCII SU2 mesh with inlet, outlet, and wall markers."},
            {"id": "production-mesh-review", "status": "fail", "detail": "FlowLab mesh is a deterministic starter quad-strip mesh, not CAD-quality production meshing."},
        ],
        "resultExpectations": {
            "volumeFilename": "flowlab_su2",
            "historyFilename": "history",
            "collectedResult": "flowlab_su2.vtk when SU2_CFD writes ASCII ParaView output",
            "expectedPrimaryFields": ["pressure", "velocity"],
        },
        "nativeSetupPlan": {
            "status": "starter-generated" if supported_mode else "export-only-review",
            "caseCfgGenerated": True,
            "meshInput": "native SU2 ASCII quad mesh",
            "manualNativeModules": [],
            "multiZoneRequired": False,
            "transientControlsRequired": False,
            "materialReviewRequired": True,
        },
        "notes": [
            "This preset is a FlowLab support manifest for generated SU2 cases.",
            "It does not certify the starter mesh or boundary conditions for production CFD.",
        ],
    }
    supported_modes: dict[str, dict[str, Any]] = {
        "incompressible-navier-stokes": {
            "supportLevel": "starter-supported-single-zone",
            "solver": "INC_NAVIER_STOKES",
            "requiredCapabilities": ["single-zone incompressible Navier-Stokes", "constant-density fluid", "velocity inlet", "pressure outlet"],
            "enabledStarterModels": ["constant density", "constant viscosity", "laminar flow", "zero wall heat flux"],
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "residual_history"]},
            "readinessChecks": [
                {"id": "single-zone-supported", "status": "pass", "detail": "FlowLab generates a runnable single-zone incompressible SU2 starter config when SU2 is available."},
            ],
        },
        "compressible-flow": {
            "supportLevel": "starter-supported-single-zone",
            "solver": "NAVIER_STOKES",
            "requiredCapabilities": ["single-zone compressible Navier-Stokes", "standard-air equation of state", "total-condition inlet", "pressure outlet"],
            "enabledStarterModels": ["STANDARD_AIR", "Sutherland viscosity", "Roe flux", "laminar flow"],
            "nativeSetupPlan": {"materialReviewRequired": True},
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "density", "mach_number", "residual_history"]},
            "readinessChecks": [
                {"id": "single-zone-compressible-supported", "status": "pass", "detail": "FlowLab generates a runnable single-zone compressible SU2 starter config when SU2 is available."},
            ],
        },
        "heat-transfer": {
            "supportLevel": "starter-supported-single-zone",
            "solver": "INC_NAVIER_STOKES",
            "requiredCapabilities": ["single-zone incompressible Navier-Stokes", "incompressible energy equation", "isothermal wall markers"],
            "enabledStarterModels": ["INC_ENERGY_EQUATION", "constant water-like heat capacity", "constant thermal conductivity", "isothermal walls"],
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity", "temperature", "residual_history"]},
            "readinessChecks": [
                {"id": "single-zone-energy-supported", "status": "pass", "detail": "FlowLab generates a runnable single-zone incompressible energy-equation SU2 starter config when SU2 is available."},
            ],
        },
    }
    if advanced_mode in supported_modes:
        supported_update = supported_modes[advanced_mode]
        supported_checks = supported_update["readinessChecks"]
        for key, value in supported_update.items():
            if key == "readinessChecks":
                continue
            if key in {"resultExpectations", "nativeSetupPlan"} and isinstance(value, dict):
                preset[key] = {**preset.get(key, {}), **value}
            else:
                preset[key] = value
        preset["supportedByAdapter"] = supported_mode
        preset["requestedPhysicsResolved"] = supported_mode
        preset["readinessChecks"] = [*preset["readinessChecks"], *supported_checks]
        preset["notes"].append("FlowLab can generate a runnable starter SU2 config for this single-zone mode when SU2 is installed.")
    else:
        blocked_details = _su2_blocked_mode_details(advanced_mode)
        preset["requiredCapabilities"] = blocked_details["requiredCapabilities"]
        preset["blockedOrManualModels"] = blocked_details["blockedOrManualModels"]
        preset["manualSetupRequirements"] = blocked_details["manualSetupRequirements"]
        preset["nativeSetupPlan"] = {**preset["nativeSetupPlan"], **blocked_details["nativeSetupPlan"]}
        preset["nativeSetupPlan"]["reviewTemplate"] = "flowlab_su2_native_config_template.cfg"
        preset["resultExpectations"] = {**preset["resultExpectations"], **blocked_details["resultExpectations"]}
        preset["readinessChecks"] = [
            *preset["readinessChecks"],
            {
                "id": "native-config-review-template",
                "status": "pass",
                "detail": "FlowLab emits flowlab_su2_native_config_template.cfg as a guarded review template for manual SU2 setup.",
            },
            *blocked_details["readinessChecks"],
        ]
        preset["notes"].append("FlowLab exports this SU2 case for inspection only and does not attach a solver run command.")
    preset["blockingReasons"] = [check["detail"] for check in preset["readinessChecks"] if check.get("status") == "fail"]
    return preset


def _su2_native_config_template(mode_preset: dict[str, Any], case_cfg: str) -> str:
    advanced_mode = str(mode_preset.get("advancedMode") or "unknown")
    native_setup_plan = mode_preset.get("nativeSetupPlan") if isinstance(mode_preset.get("nativeSetupPlan"), dict) else {}
    result_expectations = mode_preset.get("resultExpectations") if isinstance(mode_preset.get("resultExpectations"), dict) else {}
    required_capabilities = [str(item) for item in mode_preset.get("requiredCapabilities", []) if str(item).strip()]
    manual_models = [str(item) for item in mode_preset.get("blockedOrManualModels", []) if str(item).strip()]
    manual_requirements = [str(item) for item in mode_preset.get("manualSetupRequirements", []) if str(item).strip()]
    manual_modules = [
        str(item)
        for item in native_setup_plan.get("manualNativeModules", [])
        if str(item).strip()
    ]
    expected_fields = [
        str(item)
        for item in result_expectations.get("expectedPrimaryFields", [])
        if str(item).strip()
    ]

    def comment_list(title: str, values: list[str]) -> list[str]:
        if not values:
            return [f"% {title}: none recorded"]
        return [f"% {title}:"] + [f"% - {value}" for value in values]

    lines = [
        "% FlowLab SU2 native configuration review template.",
        "% This file is intentionally not runnable as generated.",
        "% Replace FLOWLAB_TEMPLATE_ONLY/FLOWLAB_UNSUPPORTED_MODE and the placeholder solver setup only after native SU2 review.",
        f"% Advanced mode: {advanced_mode}",
        "FLOWLAB_TEMPLATE_ONLY= YES",
        "FLOWLAB_UNSUPPORTED_MODE= YES",
        f"FLOWLAB_REQUESTED_MODE= {advanced_mode}",
        "MESH_FILENAME= mesh/flowlab_mesh.su2",
        "MESH_FORMAT= SU2",
        "OUTPUT_FILES= (RESTART, PARAVIEW_ASCII)",
        "VOLUME_FILENAME= flowlab_su2",
        "CONV_FILENAME= history",
        "",
        *comment_list("Required native SU2 capabilities", required_capabilities),
        *comment_list("Native modules or models still manual", manual_modules),
        *comment_list("Blocked/manual FlowLab models", manual_models),
        *comment_list("Manual setup actions before running", manual_requirements),
        *comment_list("Expected primary fields after native setup", expected_fields),
        "",
        f"% Multi-zone required: {native_setup_plan.get('multiZoneRequired')}",
        f"% Transient controls required: {native_setup_plan.get('transientControlsRequired')}",
        f"% Material review required: {native_setup_plan.get('materialReviewRequired')}",
        "",
        "% Starter blocked case.cfg follows for marker names and mesh provenance only.",
        "% Do not run this SOLVER= NONE block as CFD.",
        case_cfg.strip(),
        "",
    ]
    return "\n".join(lines) + "\n"


def _su2_native_setup_checklist(mode_preset: dict[str, Any]) -> dict[str, Any]:
    native_setup_plan = mode_preset.get("nativeSetupPlan") if isinstance(mode_preset.get("nativeSetupPlan"), dict) else {}
    result_expectations = mode_preset.get("resultExpectations") if isinstance(mode_preset.get("resultExpectations"), dict) else {}
    manual_modules = native_setup_plan.get("manualNativeModules") if isinstance(native_setup_plan.get("manualNativeModules"), list) else []
    manual_requirements = mode_preset.get("manualSetupRequirements") if isinstance(mode_preset.get("manualSetupRequirements"), list) else []
    action_items = [
        {"kind": "native-module", "item": str(item)}
        for item in manual_modules
    ]
    action_items.extend(
        {"kind": "manual-setup", "item": str(item)}
        for item in manual_requirements
    )
    generated_files = [
        "case.cfg",
        "flowlab_su2_mode_preset.json",
        "mesh/flowlab_mesh.su2",
        "flowlab_su2_capability_matrix.json",
    ]
    handoff_artifacts = native_setup_plan.get("handoffArtifacts") if isinstance(native_setup_plan.get("handoffArtifacts"), list) else []
    review_template = native_setup_plan.get("reviewTemplate")
    if isinstance(review_template, str) and review_template.strip():
        generated_files.append(review_template)
    generated_files.extend(str(item) for item in handoff_artifacts if str(item).strip())
    if mode_preset.get("requestedPhysicsResolved") is False:
        generated_files.append("flowlab_su2_advanced_preflight.json")
    return {
        "schema": "flowlab.su2_native_setup_checklist.v1",
        "advancedMode": mode_preset.get("advancedMode"),
        "supportLevel": mode_preset.get("supportLevel"),
        "supportedByAdapter": mode_preset.get("supportedByAdapter"),
        "requestedPhysicsResolved": mode_preset.get("requestedPhysicsResolved"),
        "productionReady": False,
        "generatedFiles": generated_files,
        "nativeSetupPlan": native_setup_plan,
        "actionItems": action_items,
        "readinessItems": mode_preset.get("readinessChecks", []),
        "blockingReasons": mode_preset.get("blockingReasons", []),
        "blockedOrManualModels": mode_preset.get("blockedOrManualModels", []),
        "manualSetupRequirements": manual_requirements,
        "expectedPrimaryFields": result_expectations.get("expectedPrimaryFields", []),
        "notes": [
            "This checklist is derived from flowlab_su2_mode_preset.json so users can review native SU2 setup gaps directly.",
            "Supported single-zone starter modes may still be productionReady=false because mesh and boundary-condition review remain required.",
            "Blocked export-only modes require the listed native modules and manual setup actions before FlowLab can claim the requested physics is resolved.",
        ],
    }


def _su2_advanced_preflight(mode_preset: dict[str, Any], setup_checklist: dict[str, Any], mode_files: dict[str, str]) -> dict[str, Any]:
    native_setup_plan = mode_preset.get("nativeSetupPlan") if isinstance(mode_preset.get("nativeSetupPlan"), dict) else {}
    result_expectations = mode_preset.get("resultExpectations") if isinstance(mode_preset.get("resultExpectations"), dict) else {}
    handoff_artifacts = [str(item) for item in native_setup_plan.get("handoffArtifacts", []) if str(item).strip()]
    review_template = str(native_setup_plan.get("reviewTemplate") or "")
    generated_files = setup_checklist.get("generatedFiles") if isinstance(setup_checklist.get("generatedFiles"), list) else []
    artifact_checks = [
        {
            "id": f"artifact:{artifact}",
            "artifact": artifact,
            "status": "pass" if artifact in mode_files else "fail",
            "detail": "Generated review artifact is present." if artifact in mode_files else "Generated review artifact is missing.",
        }
        for artifact in handoff_artifacts
    ]
    if review_template:
        artifact_checks.append(
            {
                "id": f"artifact:{review_template}",
                "artifact": review_template,
                "status": "pass" if review_template in generated_files else "fail",
                "detail": "Guarded native config review template is listed in the setup checklist."
                if review_template in generated_files
                else "Guarded native config review template is not listed in the setup checklist.",
            }
        )
    unresolved_actions = [
        str(item.get("item"))
        for item in setup_checklist.get("actionItems", [])
        if isinstance(item, dict) and str(item.get("item") or "").strip()
    ]
    readiness_checks = mode_preset.get("readinessChecks") if isinstance(mode_preset.get("readinessChecks"), list) else []
    blocking_reasons = [
        *(str(reason) for reason in mode_preset.get("blockingReasons", []) if str(reason).strip()),
        *(check["detail"] for check in artifact_checks if check.get("status") == "fail"),
    ]
    return {
        "schema": "flowlab.su2_advanced_preflight.v1",
        "advancedMode": mode_preset.get("advancedMode"),
        "targetSolver": "su2",
        "productionReady": False,
        "nativeSu2Ready": False,
        "status": "blocked-export-only",
        "supportLevel": mode_preset.get("supportLevel"),
        "requestedPhysicsResolved": mode_preset.get("requestedPhysicsResolved"),
        "reviewTemplate": review_template,
        "handoffArtifacts": handoff_artifacts,
        "artifactChecks": artifact_checks,
        "manualNativeModules": native_setup_plan.get("manualNativeModules", []),
        "manualSetupRequirements": mode_preset.get("manualSetupRequirements", []),
        "unresolvedActions": unresolved_actions,
        "expectedPrimaryFields": result_expectations.get("expectedPrimaryFields", []),
        "readinessChecks": readiness_checks,
        "blockingReasons": blocking_reasons,
        "notes": [
            "This preflight is a machine-readable review artifact for blocked SU2 advanced modes.",
            "It does not make the SU2 case runnable; FLOWLAB_UNSUPPORTED_MODE remains active until native setup is supplied.",
        ],
    }


def _su2_capability_matrix(active_mode: str, conditions: CaseConditions | None = None) -> dict[str, Any]:
    modes = [
        "incompressible-navier-stokes",
        "heat-transfer",
        "compressible-flow",
        "multiphase-vof",
        "cavitation",
        "conjugate-heat-transfer",
        "water-hammer",
        "rigid-body-fluid-forces",
    ]
    starter_supported_modes = {
        "incompressible-navier-stokes",
        "heat-transfer",
        "compressible-flow",
    }
    entries: list[dict[str, Any]] = []
    for mode in modes:
        preset = _su2_mode_preset(mode, conditions, mode in starter_supported_modes)
        native_plan = preset.get("nativeSetupPlan") if isinstance(preset.get("nativeSetupPlan"), dict) else {}
        result_expectations = preset.get("resultExpectations") if isinstance(preset.get("resultExpectations"), dict) else {}
        readiness_checks = preset.get("readinessChecks") if isinstance(preset.get("readinessChecks"), list) else []
        entries.append(
            {
                "advancedMode": mode,
                "active": mode == active_mode,
                "supportLevel": preset.get("supportLevel"),
                "supportedByAdapter": preset.get("supportedByAdapter"),
                "requestedPhysicsResolved": preset.get("requestedPhysicsResolved"),
                "productionReady": False,
                "requestedPhysics": preset.get("requiredCapabilities", []),
                "enabledStarterModels": preset.get("enabledStarterModels", []),
                "generatedSolverKind": preset.get("solver") or "NONE",
                "configKind": "native-case.cfg" if preset.get("supportedByAdapter") is True else "guarded-export-template",
                "manualNativeModules": native_plan.get("manualNativeModules", []),
                "handoffArtifacts": native_plan.get("handoffArtifacts", []),
                "expectedPrimaryFields": result_expectations.get("expectedPrimaryFields", []),
                "readinessSummary": {
                    "pass": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "pass"),
                    "warning": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "warning"),
                    "fail": sum(1 for check in readiness_checks if isinstance(check, dict) and check.get("status") == "fail"),
                },
                "blockingReasons": preset.get("blockingReasons", []),
            }
        )
    unresolved_modes = [
        entry["advancedMode"]
        for entry in entries
        if entry.get("requestedPhysicsResolved") is False
    ]
    return {
        "schema": "flowlab.su2_capability_matrix.v1",
        "activeMode": active_mode,
        "productionReady": False,
        "entries": entries,
        "summary": {
            "modeCount": len(entries),
            "starterSupportedModes": [
                entry["advancedMode"]
                for entry in entries
                if entry.get("supportedByAdapter") is True and entry.get("requestedPhysicsResolved") is True
            ],
            "blockedExportOnlyModes": unresolved_modes,
            "unresolvedModes": unresolved_modes,
            "handoffModes": [
                entry["advancedMode"]
                for entry in entries
                if entry.get("handoffArtifacts")
            ],
        },
        "notes": [
            "This matrix summarizes FlowLab's SU2 adapter coverage for generated-case review.",
            "Starter-supported means FlowLab can generate a runnable single-zone starter case when SU2 is available, not production CFD validation.",
            "Blocked export-only modes require the listed native modules, handoff review, mesh-quality evidence, and result-field verification before physics can be claimed resolved.",
        ],
    }


def _su2_config(
    advanced_mode: str,
    marker_tags: dict[str, list[str]],
    conditions: CaseConditions | None = None,
) -> tuple[str, list[str], bool]:
    conditions = conditions or CaseConditions()
    inlet_markers, outlet_markers, wall_markers = _su2_marker_values(marker_tags)
    notes = ["SU2 config uses boundary markers exported in the native port-aware .su2 mesh."]
    lines = _su2_base_config(advanced_mode)

    if advanced_mode == "compressible-flow":
        lines.extend(
            [
                "SOLVER= NAVIER_STOKES",
                "KIND_TURB_MODEL= NONE",
                "FLUID_MODEL= STANDARD_AIR",
                "MACH_NUMBER= 0.30",
                "AOA= 0.0",
                "FREESTREAM_OPTION= TEMPERATURE_FS",
                f"FREESTREAM_PRESSURE= {_su2_number(conditions.outlet_pressure)}",
                f"FREESTREAM_TEMPERATURE= {_su2_number(conditions.temperature)}",
                "REYNOLDS_NUMBER= 100000.0",
                "REYNOLDS_LENGTH= 1.0",
                "VISCOSITY_MODEL= SUTHERLAND",
                "INLET_TYPE= TOTAL_CONDITIONS",
                f"MARKER_INLET= ( {_su2_repeated_marker_values(marker_tags['inlet'], _su2_number(conditions.temperature + 6.85), _su2_number(max(conditions.inlet_pressure, conditions.outlet_pressure)), _su2_number(conditions.inlet_velocity), '0.0', '0.0')} )",
                f"MARKER_OUTLET= ( {_su2_repeated_marker_values(marker_tags['outlet'], _su2_number(conditions.outlet_pressure))} )",
                f"MARKER_HEATFLUX= ( {_su2_repeated_marker_values(marker_tags['wall'], '0.0')} )",
                f"MARKER_PLOTTING= ( {wall_markers} )",
                f"MARKER_MONITORING= ( {outlet_markers} )",
                "NUM_METHOD_GRAD= GREEN_GAUSS",
                "CONV_NUM_METHOD_FLOW= ROE",
                "MUSCL_FLOW= YES",
                "SLOPE_LIMITER_FLOW= VENKATAKRISHNAN",
            ]
        )
        notes.append("Compressible mode uses NAVIER_STOKES with total-condition inlet and pressure outlet markers.")
        return "\n".join(lines), notes, True

    if advanced_mode in {"heat-transfer", "incompressible-navier-stokes"}:
        energy = "YES" if advanced_mode == "heat-transfer" else "NO"
        wall_bc = (
            f"MARKER_ISOTHERMAL= ( {_su2_repeated_marker_values(marker_tags['wall'], _su2_number(conditions.temperature + 26.85))} )"
            if advanced_mode == "heat-transfer"
            else f"MARKER_HEATFLUX= ( {_su2_repeated_marker_values(marker_tags['wall'], '0.0')} )"
        )
        lines.extend(
            [
                "SOLVER= INC_NAVIER_STOKES",
                "KIND_TURB_MODEL= NONE",
                "FLUID_MODEL= CONSTANT_DENSITY",
                "INC_DENSITY_MODEL= CONSTANT",
                f"INC_DENSITY_INIT= {_su2_number(conditions.density)}",
                "VISCOSITY_MODEL= CONSTANT_VISCOSITY",
                f"MU_CONSTANT= {_su2_number(conditions.dynamic_viscosity)}",
                f"INC_ENERGY_EQUATION= {energy}",
                "INC_INLET_TYPE= VELOCITY_INLET",
                "INC_OUTLET_TYPE= PRESSURE_OUTLET",
                f"MARKER_INLET= ( {_su2_repeated_marker_values(marker_tags['inlet'], _su2_number(conditions.temperature), _su2_number(conditions.inlet_velocity), '1.0', '0.0', '0.0')} )",
                f"MARKER_OUTLET= ( {_su2_repeated_marker_values(marker_tags['outlet'], _su2_number(conditions.outlet_gauge_pressure))} )",
                wall_bc,
                f"MARKER_PLOTTING= ( {wall_markers} )",
                f"MARKER_MONITORING= ( {outlet_markers} )",
                "NUM_METHOD_GRAD= GREEN_GAUSS",
                "CONV_NUM_METHOD_FLOW= FDS",
                "LINEAR_SOLVER= FGMRES",
            ]
        )
        if advanced_mode == "heat-transfer":
            lines.extend(
                [
                    "SPECIFIC_HEAT_CP= 4182.0",
                    "THERMAL_CONDUCTIVITY_CONSTANT= 0.598",
                    "MARKER_HEATTRANSFER= ( NONE )",
                ]
            )
            notes.append("Heat-transfer mode uses INC_NAVIER_STOKES with the incompressible energy equation and isothermal walls.")
        else:
            notes.append("Incompressible mode uses INC_NAVIER_STOKES with velocity inlet and gauge-pressure outlet markers.")
        return "\n".join(lines), notes, True

    return (*_su2_unsupported_config(advanced_mode), False)


class SolverAdapter(ABC):
    id: str
    label: str
    docker_image: str | None = None
    native_commands: tuple[str, ...] = ()

    def capability(self) -> SolverCapability:
        native = any(_command_exists(command) for command in self.native_commands)
        docker = _docker_available()
        installed = native or bool(self.docker_image and docker)
        notes: list[str] = []
        if native:
            notes.append("Native solver command detected.")
        if self.docker_image and docker:
            notes.append(f"Docker available; can run image {self.docker_image}.")
        if not installed:
            notes.append("Solver is not available yet. Install native binaries or start Docker.")
        return SolverCapability(
            id=self.id,  # type: ignore[arg-type]
            label=self.label,
            installed=installed,
            execution="native" if native else "docker",
            notes=notes,
        )

    @abstractmethod
    def generate_case(self, request: CaseRequest) -> SolverCase:
        raise NotImplementedError

    def _project_name(self, request: CaseRequest) -> str:
        return str(request.project.get("name") or "FlowLab case")

    def _mesh_files(self, request: CaseRequest) -> tuple[dict[str, str], list[str]]:
        try:
            bundle = generate_mesh_bundle(request.project)
        except ValueError as exc:
            return {
                "mesh/README.md": (
                    "# FlowLab mesh\n\n"
                    f"Mesh was not generated: {exc}\n\n"
                    "FlowLab mesh v1 needs graph geometry with connected edges. "
                    "It supports circular pipes, circular Venturi edges, and rectangular channels.\n"
                )
            }, [f"Mesh export skipped: {exc}"]
        return bundle.files, bundle.provenance


def _openfoam_mesh_review_manifest(mesh: dict[str, Any] | None, openfoam_mesh_files: dict[str, str]) -> dict[str, Any]:
    mesh_quality = mesh.get("quality") if isinstance(mesh, dict) and isinstance(mesh.get("quality"), dict) else {}
    controls = mesh.get("controls") if isinstance(mesh, dict) and isinstance(mesh.get("controls"), dict) else {}
    boundary_layer_plan = mesh.get("boundaryLayerPlan") if isinstance(mesh, dict) and isinstance(mesh.get("boundaryLayerPlan"), dict) else {}
    production_mesh_plan = mesh.get("productionMeshPlan") if isinstance(mesh, dict) and isinstance(mesh.get("productionMeshPlan"), dict) else {}
    quality_summary = mesh_quality.get("summary") if isinstance(mesh_quality.get("summary"), dict) else {}
    has_fitted_polymesh = all(
        path in openfoam_mesh_files
        for path in (
            "constant/polyMesh/points",
            "constant/polyMesh/faces",
            "constant/polyMesh/owner",
            "constant/polyMesh/neighbour",
            "constant/polyMesh/boundary",
        )
    )
    readiness_checks = [
        {
            "id": "fitted-polymesh-export",
            "status": "pass" if has_fitted_polymesh else "fail",
            "detail": "OpenFOAM constant/polyMesh files are generated from FlowLab graph geometry."
            if has_fitted_polymesh
            else "OpenFOAM falls back to blockMesh because fitted graph geometry was not exported.",
        },
        {
            "id": "source-mesh-quality",
            "status": "pass" if mesh_quality.get("status") in {"ok", "warning"} else "fail",
            "detail": f"FlowLab source mesh quality status is {mesh_quality.get('status', 'missing')}.",
        },
        {
            "id": "checkmesh-scripted",
            "status": "pass",
            "detail": "Allrun executes checkMesh -allGeometry -allTopology before launching the solver.",
        },
        {
            "id": "cad-quality-3d-topology",
            "status": "fail",
            "detail": "Generated mesh is a deterministic one-layer extrusion of a FlowLab graph strip, not a CAD-quality 3D volume mesh; see mesh/production_mesh_plan.json.",
        },
        {
            "id": "production-boundary-layer-evidence",
            "status": "fail",
            "detail": "A y-plus first-cell sizing plan is generated, but no production prism-layer mesh or solver-native y-plus field evidence is available; see mesh/production_mesh_plan.json.",
        },
        {
            "id": "solver-native-checkmesh-evidence",
            "status": "fail",
            "detail": "OpenFOAM checkMesh output is collected only after execution; generated cases do not contain pre-run native mesh-quality evidence.",
        },
    ]
    return {
        "schema": "flowlab.openfoam_mesh_review.v1",
        "productionReady": False,
        "meshGenerated": has_fitted_polymesh,
        "meshType": "flowlab-quad-strip-one-layer-extrusion" if has_fitted_polymesh else "blockMesh-fallback",
        "readinessChecks": readiness_checks,
        "blockingReasons": [check["detail"] for check in readiness_checks if check["status"] == "fail"],
        "sourceMesh": {
            "qualityStatus": mesh_quality.get("status"),
            "qualitySummary": quality_summary,
            "boundaryLayerLayers": controls.get("boundaryLayerLayers"),
            "targetYPlus": controls.get("targetYPlus"),
            "transverseFractions": controls.get("transverseFractions"),
            "boundaryLayerPlanSchema": boundary_layer_plan.get("schema"),
            "boundaryLayerEdgeCount": len(boundary_layer_plan.get("edges", [])) if isinstance(boundary_layer_plan.get("edges"), list) else 0,
            "productionMeshPlanSchema": production_mesh_plan.get("schema"),
            "productionMeshReady": production_mesh_plan.get("productionReady"),
        },
        "nativeEvidence": {
            "requiredCommand": "checkMesh -allGeometry -allTopology",
            "availableAtGeneration": False,
            "expectedRuntimeEvidence": "solver log summary and job quality gate after OpenFOAM execution",
        },
        "notes": [
            "This manifest is generated before solver execution and records whether the mesh is ready for production CFD.",
            "Starter OpenFOAM runs may pass checkMesh, but production readiness requires CAD-quality topology and boundary-layer evidence.",
        ],
    }


class OpenFOAMAdapter(SolverAdapter):
    id = "openfoam"
    label = "OpenFOAM"
    native_commands = ("foamRun",)

    @property
    def docker_image(self) -> str:
        return _openfoam_image()

    def generate_case(self, request: CaseRequest) -> SolverCase:
        conditions = _case_conditions(request.project)
        axisymmetric_requested = _openfoam_axisymmetric_mode_requested(request.project)
        full_ogrid_requested = _openfoam_full_ogrid_mode_requested(request.project)
        curved_elbow_requested = _openfoam_curved_elbow_mode_requested(request.project)
        y_junction_requested = _openfoam_y_junction_mode_requested(request.project)
        # Full O-grid scope is independent of the planar source-strip mesh and
        # must fail closed on its own geometry contract first.
        full_ogrid_profile = _openfoam_full_ogrid_profile(request.project, request.advancedMode)
        curved_elbow_profile = _openfoam_curved_elbow_profile(
            request.project,
            request.advancedMode,
        )
        y_junction_profile = _openfoam_y_junction_profile(request.project, request.advancedMode)
        parallel_settings = _openfoam_parallel_settings(request.project, request.advancedMode)
        parallel_plan = _openfoam_parallel_plan(parallel_settings)
        parallel_readme = (
            "This case is an opt-in parallel candidate using scotch decomposition; compare it against its serial baseline before making any performance or equivalence claim.\n"
            if parallel_settings["enabled"]
            else "This case is the serial baseline for any later OpenFOAM parallel comparison.\n"
        )
        solver_by_mode = {
            "incompressible-navier-stokes": "incompressibleFluid",
            "compressible-flow": "shockFluid",
            "heat-transfer": "fluid",
            "conjugate-heat-transfer": "foamMultiRun",
            "multiphase-vof": "incompressibleVoF",
            "cavitation": "compressibleVoF",
            "water-hammer": "incompressibleFluid",
        }
        solver = solver_by_mode.get(request.advancedMode, "incompressibleFluid")
        if curved_elbow_profile is not None:
            source_strip_project = json.loads(json.dumps(request.project))
            source_strip_edges = _project_edges(source_strip_project)
            source_strip_edges[0]["type"] = "pipe"
            mesh_bundle = generate_mesh_bundle(source_strip_project)
            mesh_files = mesh_bundle.files
            mesh_provenance = [
                *mesh_bundle.provenance,
                "The retained planar source strip substitutes a straight inspection chord only; it is not the curved solver geometry.",
            ]
            mesh = mesh_bundle.mesh
            openfoam_mesh_files = {}
            cht_region_mesh_files = {}
        else:
            try:
                mesh_bundle = generate_mesh_bundle(request.project)
                mesh_files = mesh_bundle.files
                mesh_provenance = mesh_bundle.provenance
                mesh = mesh_bundle.mesh
                openfoam_mesh_files = mesh_to_openfoam_polymesh(mesh)
                cht_region_mesh_files = (
                    mesh_to_openfoam_cht_region_polymesh(mesh)
                    if request.advancedMode == "conjugate-heat-transfer"
                    else {}
                )
            except ValueError as exc:
                if axisymmetric_requested or full_ogrid_requested or y_junction_requested:
                    mode = (
                        "Axisymmetric"
                        if axisymmetric_requested
                        else "Full O-grid"
                        if full_ogrid_requested
                        else "Y-junction"
                    )
                    raise ValueError(f"{mode} case generation failed closed: {exc}") from exc
                mesh_files = {
                    "mesh/README.md": (
                        "# FlowLab mesh\n\n"
                        f"Mesh was not generated: {exc}\n\n"
                        "The OpenFOAM starter case still includes a simple blockMesh domain so dependency checks and template inspection can proceed.\n"
                    )
                }
                mesh_provenance = [f"Mesh export skipped: {exc}"]
                mesh = None
                openfoam_mesh_files = {}
                cht_region_mesh_files = {}
        axisymmetric_profile = _openfoam_axisymmetric_profile(request.project, request.advancedMode, mesh)
        axisymmetric = axisymmetric_profile is not None
        full_ogrid = full_ogrid_profile is not None
        curved_elbow = curved_elbow_profile is not None
        y_junction = y_junction_profile is not None
        full_ogrid_verification = (
            full_ogrid_profile.get("verificationContract")
            if isinstance(full_ogrid_profile, dict)
            and isinstance(full_ogrid_profile.get("verificationContract"), dict)
            else None
        )
        full_ogrid_qualification = (
            full_ogrid_profile.get("qualificationContract")
            if isinstance(full_ogrid_profile, dict)
            and isinstance(full_ogrid_profile.get("qualificationContract"), dict)
            else None
        )
        full_ogrid_flow_contract = (
            full_ogrid_verification or full_ogrid_qualification
        )
        curved_elbow_verification = (
            curved_elbow_profile.get("verificationContract")
            if isinstance(curved_elbow_profile, dict)
            and isinstance(curved_elbow_profile.get("verificationContract"), dict)
            else None
        )
        axisymmetric_benchmark = (
            axisymmetric_profile.get("benchmarkContract")
            if isinstance(axisymmetric_profile, dict)
            and isinstance(axisymmetric_profile.get("benchmarkContract"), dict)
            else None
        )
        if axisymmetric_profile is not None:
            # Skip the fitted planar polyMesh so Allrun runs blockMesh on the wedge dict.
            openfoam_mesh_files = {}
            axisymmetric_preview = _openfoam_axisymmetric_preview_mesh(axisymmetric_profile)
            source_strip_paths = {
                "mesh/flowlab_mesh.json": "mesh/flowlab_source_strip.json",
                "mesh/flowlab_mesh.vtk": "mesh/flowlab_source_strip.vtk",
                "mesh/flowlab_mesh.vtu": "mesh/flowlab_source_strip.vtu",
            }
            for source_path, retained_path in source_strip_paths.items():
                if source_path in mesh_files:
                    mesh_files[retained_path] = mesh_files[source_path]
            mesh_files["mesh/flowlab_mesh.json"] = json.dumps(axisymmetric_preview, indent=2, sort_keys=True) + "\n"
            mesh_files["mesh/flowlab_mesh.vtk"] = mesh_to_legacy_vtk(
                axisymmetric_preview,
                "FlowLab axisymmetric blockMesh-equivalent wedge preview",
            )
            mesh_files["mesh/flowlab_mesh.vtu"] = mesh_to_vtu(axisymmetric_preview)
            mesh_provenance = [
                *mesh_provenance,
                "Axisymmetric case inspection VTK/VTU is a non-planar blockMesh-equivalent wedge derived from the canonical SI profile.",
                "The original canvas quad strip is retained separately as flowlab_source_strip and is not solver-result geometry.",
            ]
        if full_ogrid_profile is not None:
            # The canonical full-volume blockMesh is the solver geometry.
            openfoam_mesh_files = {}
            if (
                full_ogrid_profile.get("schema")
                == FULL_OGRID_PATH_PROFILE_SCHEMA
            ):
                path_spec = _full_ogrid_path_spec_from_profile(
                    full_ogrid_profile
                )
                full_preview = full_ogrid_path_preview_mesh(
                    path_spec,
                    full_ogrid_profile,
                )
            else:
                spec = _full_ogrid_spec_from_profile(full_ogrid_profile)
                full_preview = full_ogrid_preview_mesh(
                    spec,
                    full_ogrid_profile,
                )
            source_strip_paths = {
                "mesh/flowlab_mesh.json": "mesh/flowlab_source_strip.json",
                "mesh/flowlab_mesh.vtk": "mesh/flowlab_source_strip.vtk",
                "mesh/flowlab_mesh.vtu": "mesh/flowlab_source_strip.vtu",
            }
            for source_path, retained_path in source_strip_paths.items():
                if source_path in mesh_files:
                    mesh_files[retained_path] = mesh_files[source_path]
            mesh_files["mesh/flowlab_mesh.json"] = json.dumps(full_preview, indent=2, sort_keys=True) + "\n"
            mesh_files["mesh/flowlab_boundary_faces.json"] = (
                json.dumps(full_preview["boundaryFaceManifest"], indent=2, sort_keys=True) + "\n"
            )
            mesh_files["mesh/flowlab_mesh.vtk"] = mesh_to_legacy_vtk(
                full_preview,
                "FlowLab full-revolution five-block O-grid preview",
            )
            mesh_files["mesh/flowlab_mesh.vtu"] = mesh_to_vtu(full_preview)
            mesh_provenance = [
                *mesh_provenance,
                "Full O-grid inspection VTK/VTU is a true three-dimensional, full-volume, blockMesh-equivalent all-hex mesh.",
                "The original editor strip is retained separately as flowlab_source_strip and is not solver-result geometry.",
            ]
        if curved_elbow_profile is not None:
            openfoam_mesh_files = {}
            spec = _curved_elbow_spec_from_profile(curved_elbow_profile)
            elbow_preview = curved_elbow_preview_mesh(spec, curved_elbow_profile)
            source_strip_paths = {
                "mesh/flowlab_mesh.json": "mesh/flowlab_source_strip.json",
                "mesh/flowlab_mesh.vtk": "mesh/flowlab_source_strip.vtk",
                "mesh/flowlab_mesh.vtu": "mesh/flowlab_source_strip.vtu",
            }
            for source_path, retained_path in source_strip_paths.items():
                if source_path in mesh_files:
                    mesh_files[retained_path] = mesh_files[source_path]
            mesh_files["mesh/flowlab_mesh.json"] = json.dumps(
                elbow_preview,
                indent=2,
                sort_keys=True,
            ) + "\n"
            mesh_files["mesh/flowlab_mesh.vtk"] = mesh_to_legacy_vtk(
                elbow_preview,
                "FlowLab canonical 90-degree curved-elbow O-grid preview",
            )
            mesh_files["mesh/flowlab_mesh.vtu"] = mesh_to_vtu(elbow_preview)
            mesh_provenance = [
                *mesh_provenance,
                "Curved-elbow inspection VTK/VTU is the true three-dimensional, full-volume, 15-block all-hex geometry.",
                "Inlet-leg, elbow, and outlet-leg source-cell ranges are explicit; the original editor strip is retained separately and is not solver-result geometry.",
            ]
        if y_junction_profile is not None:
            generated_mesh = y_junction_profile.pop("_mesh")
            source_strip_paths = {
                "mesh/flowlab_mesh.json": "mesh/flowlab_source_strip.json",
                "mesh/flowlab_mesh.vtk": "mesh/flowlab_source_strip.vtk",
                "mesh/flowlab_mesh.vtu": "mesh/flowlab_source_strip.vtu",
            }
            for source_path, retained_path in source_strip_paths.items():
                if source_path in mesh_files:
                    mesh_files[retained_path] = mesh_files[source_path]
            public_mesh = public_y_junction_mesh(generated_mesh)
            mesh = public_mesh
            openfoam_mesh_files = y_junction_to_openfoam_polymesh(generated_mesh)
            mesh_files["mesh/flowlab_mesh.json"] = json.dumps(public_mesh, indent=2, sort_keys=True) + "\n"
            mesh_files["mesh/flowlab_mesh.vtk"] = mesh_to_legacy_vtk(
                public_mesh,
                "FlowLab bounded symmetric true-3D Y-junction",
            )
            mesh_files["mesh/flowlab_mesh.vtu"] = mesh_to_vtu(public_mesh)
            mesh_files["mesh/y_junction_region_artifacts.json"] = json.dumps(
                {
                    "schema": "flowlab.y-junction-region-artifacts.v1",
                    "generationSha256": public_mesh["generationSha256"],
                    "regions": public_mesh["regions"],
                    "ownershipInference": "forbidden",
                },
                indent=2,
                sort_keys=True,
            ) + "\n"
            mesh_provenance = [
                *mesh_provenance,
                "The Y-junction solver polyMesh and 3D inspection VTK/VTU share the exact generated source-cell ordering.",
                "Three edge ranges are construction-time generated artifacts; junction cells carry a dedicated generated identity and no schematic owner.",
                "Result ownership must not be reconstructed from displayed geometry or cell coordinates.",
            ]
        identity_contract_files: dict[str, str] = {}
        try:
            generated_mesh_manifest = json.loads(
                mesh_files["mesh/flowlab_mesh.json"]
            )
        except (KeyError, json.JSONDecodeError):
            generated_mesh_manifest = {}
        requires_explicit_source_identity = (
            isinstance(generated_mesh_manifest, dict)
            and generated_mesh_manifest.get(
                "requiresExplicitSourceCellProvenance"
            )
            is True
        )
        if (
            len(_project_edges(request.project)) > 1
            or requires_explicit_source_identity
        ):
            try:
                identity_contract = source_cell_identity_contract(
                    mesh_files["mesh/flowlab_mesh.json"]
                )
            except (KeyError, ResultIdentityError) as exc:
                raise ValueError(
                    "Multi-edge OpenFOAM generation requires a deterministic source-cell identity contract."
                ) from exc
            identity_contract_files[SOURCE_IDENTITY_CONTRACT_PATH] = (
                json.dumps(identity_contract, indent=2, sort_keys=True) + "\n"
            )
        far_patches = (
            _WEDGE_FAR_FIELD_PATCHES
            if axisymmetric
            else ""
            if full_ogrid or curved_elbow or y_junction
            else None
        )
        mode_files, mode_provenance = _openfoam_mode_files(request.advancedMode, conditions, request.project)
        pressure_dimensions = (
            "[1 -1 -2 0 0 0 0]"
            if request.advancedMode
            in {"compressible-flow", "heat-transfer", "conjugate-heat-transfer", "multiphase-vof", "cavitation"}
            else "[0 2 -2 0 0 0 0]"
        )
        outlet_pressure_value = (
            conditions.outlet_pressure
            if pressure_dimensions == "[1 -1 -2 0 0 0 0]"
            else conditions.outlet_kinematic_pressure
        )
        turbulent = request.advancedMode in {
            "conjugate-heat-transfer",
            "cavitation",
        }
        physical_properties = (
            _openfoam_thermophysical_properties(request.advancedMode)
            if request.advancedMode in {"compressible-flow", "heat-transfer", "conjugate-heat-transfer"}
            else _openfoam_transport_properties(request.advancedMode, conditions)
        )
        volume_ogrid_profile = full_ogrid_profile or curved_elbow_profile
        files = {
            "README.md": (
                f"# {self._project_name(request)}\n\n"
                f"Generated OpenFOAM starter case for `{request.advancedMode}` using `{solver}`.\n\n"
                "Run with `bash Allrun` from this directory after OpenFOAM is installed or through the FlowLab job runner.\n"
                "The case includes a fitted constant/polyMesh when graph geometry is available, a blockMesh fallback, inlet/outlet/wall/empty patches, mode-specific field files, and post-process export.\n"
                + parallel_readme
            ),
            "Allrun": _openfoam_allrun(
                solver,
                bool(openfoam_mesh_files),
                guarded_preflight=request.advancedMode == "conjugate-heat-transfer",
                parallel_settings=parallel_settings,
            ),
            "system/blockMeshDict": (
                _openfoam_axisymmetric_block_mesh_dict(axisymmetric_profile)
                if axisymmetric_profile is not None
                else full_ogrid_path_block_mesh_dict(
                    _full_ogrid_path_spec_from_profile(full_ogrid_profile)
                )
                if full_ogrid_profile is not None
                and full_ogrid_profile.get("schema")
                == FULL_OGRID_PATH_PROFILE_SCHEMA
                else full_ogrid_block_mesh_dict(_full_ogrid_spec_from_profile(full_ogrid_profile))
                if full_ogrid_profile is not None
                else curved_elbow_block_mesh_dict(
                    _curved_elbow_spec_from_profile(curved_elbow_profile)
                )
                if curved_elbow_profile is not None
                else (
                    _foam_header("dictionary", "blockMeshDict")
                    + "// The bounded Y-junction owns a generated constant/polyMesh.\n"
                    + "// Required patches: inlet outletUpper outletLower walls.\n"
                )
                if y_junction_profile is not None
                else _openfoam_block_mesh_dict(mesh)
            ),
            "system/controlDict": (
                _openfoam_y_junction_control_dict(solver, request.project, y_junction_profile)
                if y_junction_profile is not None
                else _openfoam_control_dict(
                    solver,
                    mesh,
                    request.advancedMode,
                    request.project,
                    axisymmetric_profile,
                    volume_ogrid_profile,
                )
            ),
            "system/functions": (
                _openfoam_y_junction_function_object_entries(y_junction_profile)
                if y_junction_profile is not None
                else _openfoam_function_object_entries(
                    mesh,
                    request.advancedMode,
                    request.project,
                    axisymmetric_profile,
                    volume_ogrid_profile,
                )
            ),
            "system/fvSchemes": (
                _openfoam_axisymmetric_benchmark_fv_schemes()
                if axisymmetric_benchmark is not None
                else _openfoam_fv_schemes(steady=_openfoam_steady_requested(request.advancedMode, request.project))
            ),
            "system/fvSolution": _openfoam_fv_solution(
                steady=_openfoam_steady_requested(request.advancedMode, request.project),
                periodic_pressure_reference=axisymmetric_benchmark is not None,
                strict_verification=(
                    full_ogrid_flow_contract is not None
                    or curved_elbow_verification is not None
                    or y_junction_profile is not None
                ),
            ),
            "0/U": (
                _openfoam_axisymmetric_periodic_vector_field(
                    f"({float(axisymmetric_benchmark['meanVelocityTargetMPerS']):.17g} 0 0)"
                )
                if axisymmetric_benchmark is not None
                else _openfoam_y_junction_vector_field(y_junction_profile)
                if y_junction_profile is not None
                else _openfoam_full_ogrid_parabolic_vector_field(
                    full_ogrid_profile,
                    full_ogrid_flow_contract,
                )
                if full_ogrid_profile is not None
                and full_ogrid_flow_contract is not None
                else _openfoam_curved_elbow_parabolic_vector_field(
                    curved_elbow_profile,
                    curved_elbow_verification,
                )
                if curved_elbow_profile is not None
                and curved_elbow_verification is not None
                else _openfoam_vector_field(
                    "U",
                    f"({conditions.inlet_velocity:.9g} 0 0)",
                    far_patches=far_patches,
                )
            ),
            "0/p": (
                _openfoam_axisymmetric_periodic_scalar_field("p", pressure_dimensions, "0")
                if axisymmetric_benchmark is not None
                else _openfoam_y_junction_scalar_field(
                    "p",
                    dimensions=pressure_dimensions,
                    internal=0.5
                    * (
                        float(y_junction_profile["flow"]["outletUpperKinematicPressureM2PerS2"])
                        + float(y_junction_profile["flow"]["outletLowerKinematicPressureM2PerS2"])
                    ),
                    upper_outlet=float(y_junction_profile["flow"]["outletUpperKinematicPressureM2PerS2"]),
                    lower_outlet=float(y_junction_profile["flow"]["outletLowerKinematicPressureM2PerS2"]),
                )
                if y_junction_profile is not None
                else _openfoam_pressure_field(
                    "p",
                    pressure_dimensions,
                    internal=f"{outlet_pressure_value:.9g}",
                    outlet=f"{outlet_pressure_value:.9g}",
                    far_patches=far_patches,
                )
            ),
            "0/p_rgh": (
                _openfoam_axisymmetric_periodic_scalar_field("p_rgh", pressure_dimensions, "0")
                if axisymmetric_benchmark is not None
                else _openfoam_y_junction_scalar_field(
                    "p_rgh",
                    dimensions=pressure_dimensions,
                    internal=0.5
                    * (
                        float(y_junction_profile["flow"]["outletUpperKinematicPressureM2PerS2"])
                        + float(y_junction_profile["flow"]["outletLowerKinematicPressureM2PerS2"])
                    ),
                    upper_outlet=float(y_junction_profile["flow"]["outletUpperKinematicPressureM2PerS2"]),
                    lower_outlet=float(y_junction_profile["flow"]["outletLowerKinematicPressureM2PerS2"]),
                )
                if y_junction_profile is not None
                else _openfoam_pressure_field(
                    "p_rgh",
                    pressure_dimensions,
                    internal=f"{outlet_pressure_value:.9g}",
                    outlet=f"{outlet_pressure_value:.9g}",
                    far_patches=far_patches,
                )
            ),
            "0/T": (
                _openfoam_axisymmetric_periodic_scalar_field(
                    "T",
                    "[0 0 0 1 0 0 0]",
                    f"{conditions.temperature:.6g}",
                )
                if axisymmetric_benchmark is not None
                else _openfoam_y_junction_temperature_field(conditions.temperature)
                if y_junction_profile is not None
                else _openfoam_temperature_field(conditions.temperature, far_patches=far_patches)
            ),
            "constant/transportProperties": _openfoam_transport_properties(request.advancedMode, conditions),
            "constant/physicalProperties": physical_properties,
            "constant/turbulenceProperties": _openfoam_turbulence_properties(turbulent),
            "flowlab_project.json": json.dumps(request.project, indent=2),
            "constant/flowlab.json": json.dumps(request.project, indent=2),
            "constant/flowlab_patch_metrics.json": json.dumps(_openfoam_patch_metric_manifest(request.project), indent=2, sort_keys=True) + "\n",
            "constant/flowlab_openfoam_function_objects.json": json.dumps(
                _openfoam_function_object_runtime_manifest(request.project),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            "constant/flowlab_openfoam_parallel_plan.json": json.dumps(
                parallel_plan,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            **(
                {
                    "constant/flowlab_axisymmetric_profile.json": json.dumps(
                        axisymmetric_profile,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                }
                if axisymmetric_profile is not None
                else {}
            ),
            **(
                {
                    "constant/flowlab_full_ogrid_profile.json": json.dumps(
                        full_ogrid_profile,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                }
                if full_ogrid_profile is not None
                else {}
            ),
            **(
                {
                    "constant/flowlab_curved_elbow_profile.json": json.dumps(
                        curved_elbow_profile,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                }
                if curved_elbow_profile is not None
                else {}
            ),
            **(
                {
                    "constant/flowlab_curved_elbow_probe_provenance.json": json.dumps(
                        _openfoam_curved_elbow_probe_manifest(
                            curved_elbow_profile
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                }
                if curved_elbow_profile is not None
                else {}
            ),
            **(
                {
                    "constant/flowlab_y_junction_profile.json": json.dumps(
                        y_junction_profile,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                }
                if y_junction_profile is not None
                else {}
            ),
            **(
                {
                    "system/fvConstraints": _openfoam_axisymmetric_benchmark_fv_constraints(
                        axisymmetric_profile
                    )
                }
                if axisymmetric_benchmark is not None and axisymmetric_profile is not None
                else {}
            ),
            "mesh/openfoam_review.json": json.dumps(_openfoam_mesh_review_manifest(mesh, openfoam_mesh_files), indent=2, sort_keys=True) + "\n",
            **(
                {"system/decomposeParDict": _openfoam_decompose_par_dict(parallel_settings)}
                if parallel_settings["enabled"]
                else {}
            ),
            **mode_files,
            **openfoam_mesh_files,
            **cht_region_mesh_files,
            **mesh_files,
            **identity_contract_files,
        }
        boundary_condition_provenance = _apply_reviewed_surface_boundary_conditions(
            files,
            request.project,
            request.advancedMode,
            conditions,
            pressure_dimensions,
            outlet_pressure_value,
        )
        command = ["bash", "Allrun"]
        status = "generated" if self.capability().installed else "blocked"
        geometry_provenance = (
            [
                "FlowLab compiled one straight, non-branching circular source-to-sink path into an SI axisymmetric radius profile.",
                "OpenFOAM blockMesh realizes that profile as a conformal five-degree wedge; the throat is an internal sampling plane, not a boundary patch.",
                "The retained FlowLab canvas mesh remains an inspection proxy until the actual wedge polyMesh/foamToVTK artifact is loaded for 3D visualization.",
            ]
            if axisymmetric
            else [
                "FlowLab compiled one straight, non-branching circular multi-edge path into a conformal full-revolution O-grid profile.",
                "OpenFOAM blockMesh realizes every profile segment with five all-hex blocks; shared cross-sections are internal faces, not connector patches.",
                "Generated source-cell ranges retain explicit edge ownership; graph connectors own no cells.",
            ]
            if full_ogrid
            and full_ogrid_profile.get("schema")
            == FULL_OGRID_PATH_PROFILE_SCHEMA
            else [
                "FlowLab compiled exactly one editor-authored constant-diameter circular source-to-sink pipe into an SI full-revolution O-grid profile.",
                "OpenFOAM blockMesh realizes the profile as a conformal five-block all-hex volume with a center block, four circumferential wall blocks, and internal shared interfaces.",
                "The pre-solve VTK/VTU is the full-volume blockMesh-equivalent geometry; solver-produced VTK remains the authority after execution.",
            ]
            if full_ogrid
            else [
                "FlowLab compiled exactly one canonical 90-degree constant-diameter bend edge into an SI curved-elbow O-grid profile.",
                "OpenFOAM blockMesh realizes inlet leg, circular bend, and outlet leg as 15 conformal all-hex blocks with shared internal interfaces.",
                "The pre-solve VTK/VTU is the full-volume curved geometry and carries explicit source-cell component ranges; solver-produced VTK remains authoritative after execution.",
            ]
            if curved_elbow
            else [
                "FlowLab compiled exactly one source-junction-two-sink graph into the bounded symmetric true-3D Y-junction contract.",
                "The generated Cartesian all-hex polyMesh realizes one circular inlet and two identical +/-30-degree circular branches.",
                "Explicit generated source-cell ranges own the three pipe edges; the dedicated generated junction artifact has no schematic owner.",
            ]
            if y_junction
            else [
                "FlowLab v1 OpenFOAM volume mesh extrudes the port-aware FlowLab quad strip into one hexahedral layer with inlet, outlet, walls, and empty front/back patches."
            ]
        )
        provenance = [
            "OpenFOAM selected as broad advanced CFD backend.",
            "Case includes field files, transport/turbulence dictionaries, an Allrun workflow, and a fitted OpenFOAM polyMesh when FlowLab graph geometry is available.",
            "Boundary and fluid dictionaries are initialized from FlowLab project fluid, inlet demand, and outlet pressure where available.",
            *geometry_provenance,
            *mode_provenance,
            *boundary_condition_provenance,
            *mesh_provenance,
        ]
        if request.advancedMode == "cavitation":
            provenance.insert(1, "Cavitation mode uses compressibleVoF with a VoFCavitation fvModel case extension.")
        if parallel_settings["enabled"]:
            provenance.insert(
                1,
                "OpenFOAM parallel execution is an opt-in, scotch-decomposed candidate for the narrow incompressible mode; it requires a same-mesh serial baseline and reviewed QoI/conservation equivalence before any speedup claim.",
            )
        if request.advancedMode == "conjugate-heat-transfer":
            provenance.insert(
                1,
                "Conjugate heat-transfer case generation now emits a foamMultiRun fluid/solid multi-region starter bundle with mapped-wall starter region meshes; execution remains guarded until the interface mesh is production-ready.",
            )

        return SolverCase(
            projectName=self._project_name(request),
            solver="openfoam",
            advancedMode=request.advancedMode,
            status=status,
            files=files,
            runCommand=command,
            provenance=provenance,
        )


class SU2Adapter(SolverAdapter):
    id = "su2"
    label = "SU2"
    docker_image = None
    native_commands = ("SU2_CFD",)

    def capability(self) -> SolverCapability:
        native = any(_command_exists(command) for command in self.native_commands)
        su2_home = _su2_home()
        docker = _docker_available()
        installed = native or bool(su2_home and docker)
        notes: list[str] = []
        if native:
            notes.append("Native SU2_CFD command detected.")
        if su2_home and docker:
            notes.append(f"Docker available; can mount FLOWLAB_SU2_HOME at {su2_home}.")
        elif su2_home and not docker:
            notes.append(f"FLOWLAB_SU2_HOME points to {su2_home}, but Docker is not available for the Linux binary bundle path.")
        if not installed:
            notes.append("Install SU2_CFD on PATH, or set FLOWLAB_SU2_HOME to an official SU2 binary release and start Docker.")
        return SolverCapability(
            id=self.id,  # type: ignore[arg-type]
            label=self.label,
            installed=installed,
            execution="native" if native else "docker",
            notes=notes,
        )

    def generate_case(self, request: CaseRequest) -> SolverCase:
        solver_settings = (
            request.project.get("solver")
            if isinstance(request.project.get("solver"), dict)
            else {}
        )
        mesh_mode = str(solver_settings.get("meshMode", "planar-2d")).strip().lower()
        if mesh_mode != "planar-2d":
            raise ValueError(
                f"SU2 does not support FlowLab `{mesh_mode}` mesh mode; "
                "axisymmetric wedge and full O-grid requests fail closed."
            )
        conditions = _case_conditions(request.project)
        try:
            mesh_bundle = generate_mesh_bundle(request.project)
            mesh_files = mesh_bundle.files
            mesh_provenance = mesh_bundle.provenance
            marker_tags = su2_marker_tags(mesh_bundle.mesh)
        except ValueError as exc:
            mesh_files = {"mesh/README.md": f"Mesh was not generated: {exc}\n"}
            mesh_provenance = [f"Mesh export skipped: {exc}"]
            marker_tags = {"inlet": [], "outlet": [], "wall": []}
        config, config_provenance, supported_mode = _su2_config(request.advancedMode, marker_tags, conditions)
        mode_preset = _su2_mode_preset(request.advancedMode, conditions, supported_mode)
        setup_checklist = _su2_native_setup_checklist(mode_preset)
        capability_matrix = _su2_capability_matrix(request.advancedMode, conditions)
        mode_files: dict[str, str] = {}
        mode_provenance: list[str] = []
        if not supported_mode:
            mode_files["flowlab_su2_native_config_template.cfg"] = _su2_native_config_template(mode_preset, config)
            mode_provenance.append(
                "SU2 blocked export-only mode includes a guarded native config review template; it is not a runnable solver setup."
            )
        if request.advancedMode == "water-hammer":
            extra_files, extra_provenance = _su2_water_hammer_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode == "conjugate-heat-transfer":
            extra_files, extra_provenance = _su2_cht_handoff_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode in {"multiphase-vof", "cavitation"}:
            extra_files, extra_provenance = _su2_phase_handoff_files(request.project, conditions, request.advancedMode)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode == "rigid-body-fluid-forces":
            extra_files, extra_provenance = _su2_rigid_body_handoff_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        if not supported_mode:
            mode_files["flowlab_su2_advanced_preflight.json"] = (
                json.dumps(_su2_advanced_preflight(mode_preset, setup_checklist, mode_files), indent=2, sort_keys=True)
                + "\n"
            )
            mode_provenance.append(
                "SU2 blocked advanced mode includes flowlab_su2_advanced_preflight.json to summarize unresolved native setup, review artifacts, and expected fields."
            )
        has_native_mesh = "mesh/flowlab_mesh.su2" in mesh_files
        status = "generated" if self.capability().installed and has_native_mesh and supported_mode else "blocked"
        run_command = ["SU2_CFD", "case.cfg"] if supported_mode else []
        return SolverCase(
            projectName=self._project_name(request),
            solver="su2",
            advancedMode=request.advancedMode,
            status=status,
            files={
                "case.cfg": config,
                "flowlab_su2_mode_preset.json": json.dumps(mode_preset, indent=2, sort_keys=True) + "\n",
                "flowlab_su2_native_setup_checklist.json": json.dumps(setup_checklist, indent=2, sort_keys=True) + "\n",
                "flowlab_su2_capability_matrix.json": json.dumps(capability_matrix, indent=2, sort_keys=True) + "\n",
                "flowlab_project.json": json.dumps(request.project, indent=2),
                "mesh/README.su2.md": (
                    "# SU2 mesh export\n\n"
                    "FlowLab v1 generates `mesh/flowlab_mesh.su2` as native ASCII SU2 with quadrilateral elements "
                    "and line boundary markers derived from rotated port-to-port geometry. The mesh is still a simple "
                    "2D quad-strip inspection mesh, not a CAD-quality production volume mesh.\n\n"
                    "Supported SU2 starter modes are incompressible Navier-Stokes, compressible Navier-Stokes, "
                    "and single-zone heat transfer. Multiphase, cavitation, water-hammer, conjugate heat, and rigid-body "
                    "modes are exported as blocked cases until FlowLab can generate the required setup. "
                    "`flowlab_su2_mode_preset.json` records this support level, and "
                    "`flowlab_su2_native_setup_checklist.json` lists native/manual setup gaps in machine-readable form. "
                    "`flowlab_su2_capability_matrix.json` summarizes supported starter modes, blocked export-only modes, "
                    "handoff artifacts, expected fields, and readiness counts across all FlowLab advanced modes.\n"
                ),
                **mode_files,
                **mesh_files,
            },
            runCommand=run_command,
            provenance=[
                "SU2 selected for compressible/incompressible CFD and design workflows.",
                "FlowLab v1 exports native SU2 ASCII mesh from the same port-aware mesh bundle used for VTK/VTU.",
                "SU2 boundary and fluid values are initialized from FlowLab project fluid, inlet demand, and pressure boundaries where available.",
                f"SU2 mode preset records `{request.advancedMode}` as `{mode_preset['supportLevel']}`.",
                "SU2 capability matrix records starter-supported versus blocked export-only advanced modes without claiming unresolved physics is runnable.",
                *config_provenance,
                *mode_provenance,
                *mesh_provenance,
            ],
        )


class CodeSaturneAdapter(SolverAdapter):
    id = "code-saturne"
    label = "Code_Saturne"
    docker_image = None
    native_commands = ("code_saturne",)

    def capability(self) -> SolverCapability:
        native_installed = _command_exists("code_saturne")
        docker_image = _code_saturne_image()
        docker_installed = bool(docker_image and _docker_available())
        installed = native_installed or docker_installed
        notes = []
        if native_installed:
            notes.append("Native code_saturne command detected.")
        if docker_installed:
            notes.append(f"Docker daemon is available for configured Code_Saturne image `{docker_image}`.")
        if not installed:
            if docker_image:
                notes.append(f"FLOWLAB_CODE_SATURNE_IMAGE is set to `{docker_image}`, but Docker is unavailable.")
            else:
                notes.append(
                    "Install Code_Saturne and ensure `code_saturne` is on PATH, or set "
                    "FLOWLAB_CODE_SATURNE_IMAGE to a Docker image containing `code_saturne`."
                )
        return SolverCapability(
            id="code-saturne",
            label=self.label,
            installed=installed,
            execution="docker" if docker_installed and not native_installed else "native",
            notes=notes,
        )

    def generate_case(self, request: CaseRequest) -> SolverCase:
        conditions = _case_conditions(request.project)
        mesh_files, mesh_provenance = self._mesh_files(request)
        case_mesh_files = {}
        if "mesh/flowlab_mesh.msh" in mesh_files:
            case_mesh_files["MESH/flowlab_mesh.msh"] = mesh_files["mesh/flowlab_mesh.msh"]
        physics_preset = _code_saturne_physics_preset(request.advancedMode, conditions)
        native_setup_checklist = _code_saturne_native_setup_checklist(request.advancedMode, conditions)
        capability_matrix = _code_saturne_capability_matrix(request.advancedMode, conditions)
        requested_physics_resolved = physics_preset.get("requestedPhysicsResolved") is True
        status = "generated" if self.capability().installed and case_mesh_files and requested_physics_resolved else "blocked"
        run_command = ["code_saturne", "run"] if requested_physics_resolved else []
        mode_files: dict[str, str] = {}
        mode_provenance: list[str] = []
        if physics_preset.get("requestedPhysicsResolved") is False:
            mode_files["DATA/flowlab_native_physics_review.py"] = _code_saturne_native_physics_review_script(
                physics_preset,
                native_setup_checklist,
            )
            mode_provenance.append(
                "Code_Saturne unresolved physics mode includes DATA/flowlab_native_physics_review.py as a guarded native setup review template."
            )
        if request.advancedMode == "water-hammer":
            extra_files, extra_provenance = _code_saturne_water_hammer_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode == "conjugate-heat-transfer":
            extra_files, extra_provenance = _code_saturne_cht_handoff_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode == "compressible-flow":
            extra_files, extra_provenance = _code_saturne_compressible_handoff_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode in {"multiphase-vof", "cavitation"}:
            extra_files, extra_provenance = _code_saturne_phase_handoff_files(request.project, conditions, request.advancedMode)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        elif request.advancedMode == "rigid-body-fluid-forces":
            extra_files, extra_provenance = _code_saturne_rigid_body_handoff_files(request.project, conditions)
            mode_files.update(extra_files)
            mode_provenance.extend(extra_provenance)
        return SolverCase(
            projectName=self._project_name(request),
            solver="code-saturne",
            advancedMode=request.advancedMode,
            status=status,
            files={
                "README.md": _code_saturne_readme(self._project_name(request), request.advancedMode),
                "DATA/setup.xml": _code_saturne_setup_xml(request.advancedMode, conditions),
                "DATA/flowlab_physics_preset.json": json.dumps(physics_preset, indent=2, sort_keys=True) + "\n",
                "DATA/flowlab_native_setup_checklist.json": json.dumps(native_setup_checklist, indent=2, sort_keys=True) + "\n",
                "DATA/flowlab_code_saturne_capability_matrix.json": json.dumps(capability_matrix, indent=2, sort_keys=True) + "\n",
                "DATA/run.cfg": _code_saturne_run_cfg(),
                "DATA/cs_user_scripts.py": _code_saturne_user_scripts(),
                "DATA/cs_user_physics.py": _code_saturne_user_physics_script(request.advancedMode, conditions),
                "SRC/cs_user_boundary_conditions.f90": _code_saturne_boundary_conditions(conditions),
                "MESH/README.md": (
                    "# Code_Saturne mesh input\n\n"
                    "`flowlab_mesh.msh` is a Gmsh 2.2 ASCII mesh exported from FlowLab rotated port geometry. "
                    "Physical groups define inlet, outlet, wall, and fluid regions for Code_Saturne import.\n"
                ),
                "SRC/README.md": "Place optional Code_Saturne user source files here.\n",
                "RESU/README.md": "Code_Saturne writes run outputs under this directory.\n",
                "flowlab_project.json": json.dumps(request.project, indent=2),
                **mode_files,
                **case_mesh_files,
                **mesh_files,
            },
            runCommand=run_command,
            provenance=[
                "Code_Saturne selected as optional finite-volume industrial CFD backend.",
                "Case includes DATA/SRC/RESU/MESH layout, setup.xml, run.cfg, cs_user_scripts.py, and a native Gmsh mesh input.",
                f"Code_Saturne physics preset records `{request.advancedMode}` as `{physics_preset.get('supportLevel')}`.",
                "Code_Saturne capability matrix records starter-supported, surrogate, handoff, and blocked modes with expected fields and unresolved native modules.",
                "Code_Saturne setup.xml uses FlowLab project fluid, inlet demand, hydraulic diameter, and outlet pressure where available.",
                "FlowLab v1 Code_Saturne case is a starter workflow; production runs still require mesh-quality and boundary-condition review.",
                *(
                    [
                        "Code_Saturne requested physics is unresolved, so FlowLab blocks execution and exports review artifacts without a runnable command."
                    ]
                    if not requested_physics_resolved
                    else []
                ),
                *mode_provenance,
                *mesh_provenance,
            ],
        )


class MuJoCoAdapter(SolverAdapter):
    id = "mujoco"
    label = "MuJoCo fluid forces"
    docker_image = None
    native_commands = ("python3", "python")

    def capability(self) -> SolverCapability:
        python = _python_command()
        has_module = _python_module_exists_for_command(python, "mujoco")
        installed = bool(python and has_module)
        notes = ["MuJoCo is a rigid-body fluid-force sandbox, not a Navier-Stokes CFD executor."]
        configured = _configured_mujoco_python()
        if configured:
            if python == configured:
                notes.append(f"FLOWLAB_MUJOCO_PYTHON selected: {configured}.")
            else:
                notes.append(f"FLOWLAB_MUJOCO_PYTHON points to `{configured}`, but that executable was not found.")
        if python:
            notes.append(f"Native Python command detected: {python}.")
        else:
            notes.append("Install Python before running MuJoCo sandbox cases.")
        if has_module:
            notes.append("Python module `mujoco` detected.")
        else:
            notes.append("Install the Python package `mujoco` before running MuJoCo sandbox cases.")
        return SolverCapability(
            id="mujoco",
            label=self.label,
            installed=installed,
            execution="native",
            notes=notes,
        )

    def generate_case(self, request: CaseRequest) -> SolverCase:
        conditions = _case_conditions(request.project)
        mesh_files, mesh_provenance = self._mesh_files(request)
        python = _python_command() or "python3"
        status = "generated" if self.capability().installed else "blocked"
        return SolverCase(
            projectName=self._project_name(request),
            solver="mujoco",
            advancedMode=request.advancedMode,
            status=status,
            files={
                "model.xml": _mujoco_xml(conditions),
                "run_mujoco.py": _mujoco_runner(conditions),
                "README.md": (
                    "# FlowLab MuJoCo fluid-force sandbox\n\n"
                    "MuJoCo is used here for rigid-body experiments with phenomenological fluid forces. "
                    "It does not solve Navier-Stokes CFD fields.\n\n"
                    "The generated MJCF and runner initialize density, viscosity, reference body scale, "
                    "reference velocity, and projected area from the FlowLab project when those values are available.\n\n"
                    f"Run with `{python} run_mujoco.py` when the Python `mujoco` package is installed. "
                    "The runner writes `outputs/mujoco_fluid_force_0001.vtk` and `outputs/summary.json`.\n"
                ),
                "flowlab_project.json": json.dumps(request.project, indent=2),
                **mesh_files,
            },
            runCommand=[python, "run_mujoco.py"],
            provenance=[
                "MuJoCo fluid forces are phenomenological and stateless.",
                "Case includes MJCF, a Python runner, and VTK output for FlowLab visualization.",
                "MuJoCo density, viscosity, body scale, reference velocity, and pressure-area normalization are initialized from the FlowLab project where available.",
                "Use for moving bodies in fluid, not for Navier-Stokes field solves.",
                *mesh_provenance,
            ],
        )


ADAPTERS: dict[str, SolverAdapter] = {
    "openfoam": OpenFOAMAdapter(),
    "su2": SU2Adapter(),
    "code-saturne": CodeSaturneAdapter(),
    "mujoco": MuJoCoAdapter(),
}


def capabilities() -> list[SolverCapability]:
    instant = SolverCapability(
        id="instant-1d",
        label="Instant 1D hydraulics",
        installed=True,
        execution="browser",
        notes=["Runs in the browser with deterministic educational hydraulic models."],
    )
    return [instant, *(adapter.capability() for adapter in ADAPTERS.values())]


def generate_case(request: CaseRequest) -> SolverCase:
    adapter = ADAPTERS.get(request.solver)
    if request.solver == "instant-1d":
        try:
            mesh_files = generate_mesh_bundle(request.project).files
            mesh_provenance = ["Instant export includes the same FlowLab mesh bundle for inspection."]
        except ValueError as exc:
            mesh_files = {"mesh/README.md": f"Mesh was not generated: {exc}\n"}
            mesh_provenance = [f"Mesh export skipped: {exc}"]
        case = add_case_manifest(SolverCase(
            projectName=str(request.project.get("name") or "FlowLab quick case"),
            solver="instant-1d",
            advancedMode=request.advancedMode,
            status="generated",
            files={"flowlab_project.json": json.dumps(request.project, indent=2), **mesh_files},
            runCommand=["browser", "solveHydraulicNetwork"],
            provenance=["Instant case runs entirely in the frontend.", *mesh_provenance],
        ))
        case.evidenceCapability = experimental_capability()
        case.files[EVIDENCE_CAPABILITY_PATH] = json.dumps(case.evidenceCapability.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        return add_case_manifest(case)
    if not adapter:
        raise ValueError(f"Unsupported solver: {request.solver}")
    case = add_case_manifest(adapter.generate_case(request))
    case.evidenceCapability = experimental_capability()
    case.resultComponentMap = _result_component_map(
        request.project,
        case.files.get("flowlab_project.json"),
        solver=case.solver,
        mesh_snapshot=case.files.get("mesh/flowlab_mesh.json"),
        identity_contract_snapshot=case.files.get(SOURCE_IDENTITY_CONTRACT_PATH),
    )
    if case.resultComponentMap:
        if case.resultComponentMap.version == 1:
            case.provenance.append(
                "Result-to-schematic linkage uses a whole-artifact declaration for this single-edge generated case; imported results remain unlinked."
            )
        else:
            case.provenance.append(
                "Result-to-schematic linkage uses generated source-cell ranges for supported OpenFOAM volume artifacts; imported, unmatched, unsupported, and unowned cells remain probe-only."
            )
    case.files[EVIDENCE_CAPABILITY_PATH] = json.dumps(case.evidenceCapability.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    return add_case_manifest(case)
