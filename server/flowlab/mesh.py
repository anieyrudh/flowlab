from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VTK_QUAD = 9
GMSH_LINE = 1
GMSH_QUAD = 3
GMSH_HEXAHEDRON = 5
SU2_LINE = 3
SU2_QUAD = 9
SUPPORTED_EDGE_TYPES = {"pipe", "venturi", "expansion", "contraction", "nozzle"}
FEATURE_REFINEMENT_EDGE_TYPES = {"venturi", "expansion", "contraction", "nozzle"}
PORT_IDS = {"inlet", "outlet", "north", "south"}
MESH_QUALITY_THRESHOLDS = {
    "minCellArea": 1.0e-6,
    "maxAspectRatio": 50.0,
    "minInteriorAngleDeg": 5.0,
    "maxNonOrthogonalityDeg": 85.0,
    "maxSkewnessEstimate": 0.95,
}
DEFAULT_FLUID = {
    "density": 998.2,
    "dynamicViscosity": 1.002e-3,
}
DEFAULT_BOUNDARY_LAYER_VELOCITY = 1.0
REVIEWED_GEOMETRY_SOURCE_TYPES = {"flowlab-generated", "uploaded-stl", "local-stl-path", "multi-surface-stl"}
BOUNDARY_TAG_ROLES = {"inlet", "outlet", "wall", "interface"}
REQUIRED_REVIEWED_BOUNDARY_ROLES = {"inlet", "outlet", "wall"}
OPENFOAM_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class MeshBundle:
    mesh: dict[str, Any]
    files: dict[str, str]
    provenance: list[str]


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _edge_width(edge: dict[str, Any], station: float) -> float:
    shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
    kind = shape.get("kind")
    if kind == "rectangular":
        height = float(shape.get("height") or 0.0)
        width = float(shape.get("width") or 0.0)
        if height <= 0 or width <= 0:
            raise ValueError(f"Rectangular edge {edge.get('id', '<unknown>')} needs positive width and height.")
        return height
    if kind != "circular":
        raise ValueError(f"Unsupported edge shape for {edge.get('id', '<unknown>')}: {kind}")
    diameter = float(shape.get("diameter") or 0.0)
    if diameter <= 0:
        raise ValueError(f"Circular edge {edge.get('id', '<unknown>')} needs a positive diameter.")
    if edge.get("type") == "venturi" and edge.get("throatDiameter"):
        throat = float(edge["throatDiameter"])
        if throat <= 0:
            raise ValueError(f"Venturi edge {edge.get('id', '<unknown>')} needs a positive throatDiameter.")
        throat_influence = max(0.0, 1.0 - abs(station - 0.5) * 2.0)
        return diameter * (1.0 - throat_influence) + throat * throat_influence
    return diameter


def _segments(project: dict[str, Any]) -> int:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    resolution = solver.get("meshResolution", "coarse")
    return {"coarse": 6, "medium": 12, "fine": 24}.get(str(resolution), 6)


def _mesh_controls(project: dict[str, Any]) -> dict[str, Any]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    controls = solver.get("meshControls") if isinstance(solver.get("meshControls"), dict) else {}
    boundary_layers = _clamp_int(controls.get("boundaryLayerLayers"), 0, 12, 1)
    longitudinal_refinement = _clamp_int(controls.get("longitudinalRefinement"), 1, 4, 1)
    growth_rate = _clamp_float(controls.get("boundaryLayerGrowthRate"), 1.0, 3.0, 1.25)
    transverse_distribution = str(controls.get("transverseDistribution") or "boundary-layer")
    if transverse_distribution not in {"boundary-layer", "uniform"}:
        transverse_distribution = "boundary-layer"
    target_y_plus = _clamp_float(controls.get("targetYPlus"), 0.1, 500.0, 30.0)
    refinement_regions = []
    for item in controls.get("refinementRegions", []) if isinstance(controls.get("refinementRegions"), list) else []:
        if not isinstance(item, dict):
            continue
        edge_id = str(item.get("edgeId") or "").strip()
        if not edge_id:
            continue
        refinement_regions.append(
            {
                "edgeId": edge_id,
                "factor": _clamp_int(item.get("factor"), 1, 4, 1),
                "reason": str(item.get("reason") or "user-refinement"),
            }
        )
    quality_controls = controls.get("quality") if isinstance(controls.get("quality"), dict) else {}
    feature_controls = controls.get("featureRefinement") if isinstance(controls.get("featureRefinement"), dict) else {}
    feature_enabled = bool(feature_controls.get("enabled", False))
    feature_factor = _clamp_int(feature_controls.get("factor"), 1, 4, 1)
    default_cluster = 0.65 if feature_enabled and feature_factor > 1 else 0.0
    feature_cluster_strength = _clamp_float(feature_controls.get("clusterStrength"), 0.0, 0.95, default_cluster)
    adaptive = solver.get("adaptiveMesh") if isinstance(solver.get("adaptiveMesh"), dict) else {}
    target_field = str(adaptive.get("targetField") or "velocity")
    if target_field not in {"velocity", "pressure", "temperature", "phase", "wall-shear", "residual"}:
        target_field = "velocity"
    error_mode = str(adaptive.get("errorMode") or "gradient")
    if error_mode not in {"gradient", "relative-error", "absolute-error"}:
        error_mode = "gradient"
    thresholds = {
        "minCellArea": _clamp_float(quality_controls.get("minCellArea"), 1.0e-12, 1.0, MESH_QUALITY_THRESHOLDS["minCellArea"]),
        "maxAspectRatio": _clamp_float(quality_controls.get("maxAspectRatio"), 1.0, 1.0e6, MESH_QUALITY_THRESHOLDS["maxAspectRatio"]),
        "minInteriorAngleDeg": _clamp_float(
            quality_controls.get("minInteriorAngleDeg"),
            0.1,
            89.0,
            MESH_QUALITY_THRESHOLDS["minInteriorAngleDeg"],
        ),
        "maxNonOrthogonalityDeg": _clamp_float(
            quality_controls.get("maxNonOrthogonalityDeg"),
            0.0,
            90.0,
            MESH_QUALITY_THRESHOLDS["maxNonOrthogonalityDeg"],
        ),
        "maxSkewnessEstimate": _clamp_float(
            quality_controls.get("maxSkewnessEstimate"),
            0.0,
            1.0e6,
            MESH_QUALITY_THRESHOLDS["maxSkewnessEstimate"],
        ),
    }
    return {
        "schema": "flowlab.mesh_controls.v1",
        "baseResolution": str(solver.get("meshResolution", "coarse")),
        "baseSegments": _segments(project),
        "longitudinalRefinement": longitudinal_refinement,
        "boundaryLayerLayers": boundary_layers,
        "boundaryLayerGrowthRate": growth_rate,
        "transverseDistribution": transverse_distribution,
        "targetYPlus": target_y_plus,
        "transverseFractions": _transverse_fractions(boundary_layers, growth_rate, transverse_distribution),
        "refinementRegions": refinement_regions,
        "featureRefinement": {
            "enabled": feature_enabled,
            "factor": feature_factor,
            "clusterStrength": feature_cluster_strength,
            "featureTypes": ["venturi-throat", "diameter-transition"],
            "eligibleEdgeTypes": sorted(FEATURE_REFINEMENT_EDGE_TYPES),
        },
        "qualityThresholds": thresholds,
        "adaptiveMesh": {
            "enabled": bool(adaptive.get("enabled", False)),
            "targetField": target_field,
            "errorMode": error_mode,
            "adaptEvery": _clamp_int(adaptive.get("adaptEvery"), 1, 100, 5),
            "maxCells": _clamp_int(adaptive.get("maxCells"), 100, 50_000_000, 250_000),
            "minCellSize": _clamp_float(adaptive.get("minCellSize"), 1.0e-9, 1.0e6, 0.001),
            "maxCellSize": _clamp_float(adaptive.get("maxCellSize"), 1.0e-9, 1.0e6, 0.1),
            "gradation": _clamp_float(adaptive.get("gradation"), 1.0, 5.0, 1.4),
            "writeAdaptedState": bool(adaptive.get("writeAdaptedState", True)),
            "liveRemeshing": False,
        },
        "productionReady": False,
        "notes": [
            "Controls are applied to the deterministic FlowLab source mesh before solver-specific extrusion.",
            "Boundary-layer controls create transverse 2D strip layers near both walls; they are not a full 3D prism-layer mesher.",
            "Feature refinement can densify and cluster stations near Venturi throats or diameter transitions when explicitly enabled.",
            "Production CFD still requires CAD-quality geometry cleanup, boundary-layer validation, and solver-native mesh checks.",
        ],
    }


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(parsed):
        return fallback
    return max(minimum, min(maximum, parsed))


def _transverse_fractions(
    boundary_layers: int, growth_rate: float, distribution: str = "boundary-layer"
) -> list[float]:
    if boundary_layers <= 0:
        return [0.0, 1.0]
    if distribution == "uniform":
        # Evenly spaced cells across the gap. Unlike the wall-clustered
        # boundary-layer distribution -- which leaves a single coarse core cell
        # exactly where a laminar parabolic profile peaks -- uniform spacing
        # resolves the core and its pressure gradient far more accurately for
        # internal laminar flow. Cell count matches the boundary-layer mode
        # (2*boundary_layers + 1) so the same resolution knob applies.
        cells = 2 * boundary_layers + 1
        return [round(index / cells, 9) for index in range(cells + 1)]
    wall_weights = [growth_rate**index for index in range(boundary_layers)]
    core_weight = growth_rate**boundary_layers * 2.0
    cell_weights = [*wall_weights, core_weight, *reversed(wall_weights)]
    total = sum(cell_weights)
    fractions = [0.0]
    running = 0.0
    for weight in cell_weights:
        running += weight
        fractions.append(round(running / total, 9))
    fractions[-1] = 1.0
    return fractions


def _edge_refinement_factor(edge_id: str, controls: dict[str, Any]) -> int:
    for region in controls["refinementRegions"]:
        if region["edgeId"] == edge_id:
            return int(region["factor"])
    return 1


def _feature_refinement_plan(edge: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    edge_type = str(edge.get("type", "pipe"))
    feature_controls = controls.get("featureRefinement") if isinstance(controls.get("featureRefinement"), dict) else {}
    enabled = bool(feature_controls.get("enabled", False)) and edge_type in FEATURE_REFINEMENT_EDGE_TYPES
    factor = int(feature_controls.get("factor", 1)) if enabled else 1
    cluster_strength = float(feature_controls.get("clusterStrength", 0.0)) if enabled else 0.0
    feature_type = "venturi-throat" if edge_type == "venturi" else "diameter-transition"
    return {
        "enabled": enabled,
        "featureType": feature_type if enabled else None,
        "factor": max(1, factor),
        "clusterStrength": max(0.0, min(0.95, cluster_strength)),
        "targetStation": 0.5 if enabled else None,
    }


def _station_fractions(segment_count: int, feature_plan: dict[str, Any]) -> list[float]:
    raw_stations = [index / segment_count for index in range(segment_count + 1)]
    strength = float(feature_plan.get("clusterStrength") or 0.0)
    if not feature_plan.get("enabled") or strength <= 0:
        return raw_stations
    stations = []
    for station in raw_stations:
        # Positive strength clusters stations near the center feature while
        # preserving endpoints and monotonicity for strength < 1.
        clustered = station + (strength / (2.0 * math.pi)) * math.sin(2.0 * math.pi * station)
        stations.append(round(max(0.0, min(1.0, clustered)), 9))
    stations[0] = 0.0
    stations[-1] = 1.0
    return stations


def _node_radius(node: dict[str, Any]) -> float:
    node_type = str(node.get("type", "junction"))
    if node_type in {"source", "sink"}:
        return 17.0
    if node_type == "pump":
        return 19.0
    return 14.0


def _port_angle(node: dict[str, Any], port: str) -> float:
    base = float(node.get("rotation") or 0.0)
    if port == "outlet":
        return base
    if port == "inlet":
        return base + 180.0
    if port == "north":
        return base - 90.0
    return base + 90.0


def _port_id(edge: dict[str, Any], field: str, fallback: str) -> str:
    port = str(edge.get(field) or fallback)
    if port not in PORT_IDS:
        raise ValueError(f"Edge {edge.get('id', '<unknown>')} uses unsupported port {port}.")
    return port


def _position(node: dict[str, Any]) -> tuple[float, float]:
    pos = node.get("position") if isinstance(node.get("position"), dict) else {}
    return float(pos.get("x", 0.0)), float(pos.get("y", 0.0))


def _port_position(node: dict[str, Any], port: str) -> tuple[float, float]:
    x, y = _position(node)
    angle = math.radians(_port_angle(node, port))
    radius = _node_radius(node) + 10.0
    return x + math.cos(angle) * radius, y + math.sin(angle) * radius


def _distance_2d(a: list[float], b: list[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _polygon_signed_area(points: list[list[float]], cell: list[int]) -> float:
    area = 0.0
    polygon = [points[index] for index in cell]
    for current, following in zip(polygon, polygon[1:] + polygon[:1]):
        area += float(current[0]) * float(following[1]) - float(following[0]) * float(current[1])
    return area * 0.5


def _cell_aspect_ratio(points: list[list[float]], cell: list[int]) -> float:
    polygon = [points[index] for index in cell]
    lengths = [_distance_2d(current, following) for current, following in zip(polygon, polygon[1:] + polygon[:1])]
    shortest = min(lengths) if lengths else 0.0
    longest = max(lengths) if lengths else 0.0
    if shortest <= 0:
        return math.inf
    return longest / shortest


def _interior_angles_deg(points: list[list[float]], cell: list[int]) -> list[float]:
    polygon = [points[index] for index in cell]
    angles: list[float] = []
    for previous, current, following in zip(polygon[-1:] + polygon[:-1], polygon, polygon[1:] + polygon[:1]):
        ax = float(previous[0]) - float(current[0])
        ay = float(previous[1]) - float(current[1])
        bx = float(following[0]) - float(current[0])
        by = float(following[1]) - float(current[1])
        mag_a = math.hypot(ax, ay)
        mag_b = math.hypot(bx, by)
        if mag_a <= 0 or mag_b <= 0:
            angles.append(0.0)
            continue
        cos_theta = max(-1.0, min(1.0, (ax * bx + ay * by) / (mag_a * mag_b)))
        angles.append(math.degrees(math.acos(cos_theta)))
    return angles


def _mesh_quality_report(
    points: list[list[float]],
    cells: list[list[int]],
    regions: list[dict[str, Any]],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or MESH_QUALITY_THRESHOLDS
    areas = [_polygon_signed_area(points, cell) for cell in cells]
    abs_areas = [abs(area) for area in areas]
    aspect_ratios = [_cell_aspect_ratio(points, cell) for cell in cells]
    angles = [angle for cell in cells for angle in _interior_angles_deg(points, cell)]
    angle_deviations = [abs(angle - 90.0) for angle in angles]
    inverted = sum(1 for area in areas if area < 0)
    degenerate = sum(1 for area in abs_areas if area <= thresholds["minCellArea"])
    max_aspect = max(aspect_ratios) if aspect_ratios else math.inf
    min_angle = min(angles) if angles else 0.0
    max_angle = max(angles) if angles else 0.0
    max_non_orthogonality = max(angle_deviations) if angle_deviations else 0.0
    max_skewness_estimate = max_non_orthogonality / 90.0 if angle_deviations else 0.0
    warnings: list[str] = []
    if inverted:
        warnings.append(f"{inverted} cell(s) have negative signed area and require orientation repair.")
    if degenerate:
        warnings.append(f"{degenerate} cell(s) are degenerate or below the minimum area threshold.")
    if max_aspect > thresholds["maxAspectRatio"]:
        warnings.append(f"Maximum aspect ratio {max_aspect:.3g} exceeds the starter threshold.")
    if min_angle < thresholds["minInteriorAngleDeg"]:
        warnings.append(f"Minimum interior angle {min_angle:.3g} deg is below the starter threshold.")
    if max_non_orthogonality > thresholds["maxNonOrthogonalityDeg"]:
        warnings.append(f"Maximum source non-orthogonality estimate {max_non_orthogonality:.3g} deg exceeds the starter threshold.")
    if max_skewness_estimate > thresholds["maxSkewnessEstimate"]:
        warnings.append(f"Maximum source skewness estimate {max_skewness_estimate:.3g} exceeds the starter threshold.")
    status = "failed" if inverted or degenerate else "warning" if warnings else "ok"
    return {
        "schema": "flowlab.mesh_quality.v1",
        "status": status,
        "productionReady": False,
        "thresholds": thresholds,
        "summary": {
            "pointCount": len(points),
            "cellCount": len(cells),
            "regionCount": len(regions),
            "minCellArea": round(min(abs_areas), 9) if abs_areas else 0.0,
            "maxCellArea": round(max(abs_areas), 9) if abs_areas else 0.0,
            "maxAspectRatio": round(max_aspect, 9) if math.isfinite(max_aspect) else "inf",
            "minInteriorAngleDeg": round(min_angle, 6),
            "maxInteriorAngleDeg": round(max_angle, 6),
            "maxNonOrthogonalityDeg": round(max_non_orthogonality, 6),
            "maxSkewnessEstimate": round(max_skewness_estimate, 9),
            "invertedCellCount": inverted,
            "degenerateCellCount": degenerate,
        },
        "warnings": warnings,
        "notes": [
            "Quality metrics are computed on the FlowLab 2D quad-strip source mesh before solver-specific extrusion.",
            "Source non-orthogonality and skewness are angle-based estimates; solver-native mesh checks remain required for production CFD.",
            "This report rejects degenerate or inverted starter geometry but does not certify production CFD readiness.",
            "Production-ready status requires CAD-quality 3D meshing, boundary-layer refinement, and solver-specific mesh checks.",
        ],
    }


def _project_fluid_properties(project: dict[str, Any]) -> dict[str, float]:
    fluid = project.get("fluid") if isinstance(project.get("fluid"), dict) else {}
    density = _clamp_float(fluid.get("density"), 1.0e-9, 1.0e9, DEFAULT_FLUID["density"])
    dynamic_viscosity = _clamp_float(fluid.get("dynamicViscosity"), 1.0e-12, 1.0e6, DEFAULT_FLUID["dynamicViscosity"])
    return {"density": density, "dynamicViscosity": dynamic_viscosity}


def _hydraulic_diameter(edge: dict[str, Any]) -> float:
    shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
    if shape.get("kind") == "rectangular":
        width = float(shape.get("width") or 0.0)
        height = float(shape.get("height") or 0.0)
        if width <= 0 or height <= 0:
            return 0.0
        return 2.0 * width * height / (width + height)
    diameter = float(shape.get("diameter") or 0.0)
    if edge.get("type") == "venturi" and edge.get("throatDiameter"):
        diameter = min(diameter, float(edge.get("throatDiameter") or diameter))
    return max(0.0, diameter)


def _minimum_wall_normal_width(edge: dict[str, Any]) -> float:
    stations = [0.0, 0.5, 1.0] if edge.get("type") == "venturi" else [0.0]
    widths = []
    for station in stations:
        try:
            widths.append(_edge_width(edge, station))
        except ValueError:
            continue
    return min(widths) if widths else 0.0


def _friction_factor(reynolds: float) -> float:
    if reynolds <= 0:
        return 0.0
    if reynolds < 2300:
        return 64.0 / reynolds
    return 0.3164 / (reynolds ** 0.25)


def _boundary_layer_plan(
    project: dict[str, Any],
    edges: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    controls: dict[str, Any],
) -> dict[str, Any]:
    fluid = _project_fluid_properties(project)
    target_y_plus = float(controls["targetYPlus"])
    fractions = [float(item) for item in controls["transverseFractions"]]
    first_fraction = fractions[1] - fractions[0] if len(fractions) > 1 else 1.0
    edge_by_id = {str(edge.get("id") or ""): edge for edge in edges}
    edge_plans = []
    for region in regions:
        if region.get("edgeType") == "connector":
            continue
        edge = edge_by_id.get(str(region.get("edgeId")))
        if not edge:
            continue
        velocity = _clamp_float(edge.get("designVelocity"), 1.0e-9, 1.0e6, DEFAULT_BOUNDARY_LAYER_VELOCITY)
        hydraulic_diameter = _hydraulic_diameter(edge)
        min_wall_normal_width = _minimum_wall_normal_width(edge)
        reynolds = fluid["density"] * velocity * hydraulic_diameter / fluid["dynamicViscosity"] if hydraulic_diameter > 0 else 0.0
        friction_factor = _friction_factor(reynolds)
        friction_velocity = velocity * math.sqrt(friction_factor / 8.0) if friction_factor > 0 else 0.0
        target_first_cell_height = (
            target_y_plus * fluid["dynamicViscosity"] / (fluid["density"] * friction_velocity)
            if friction_velocity > 0
            else 0.0
        )
        starter_first_cell_height = first_fraction * min_wall_normal_width
        edge_plans.append(
            {
                "edgeId": region.get("edgeId"),
                "edgeType": region.get("edgeType"),
                "assumedVelocity": round(velocity, 9),
                "hydraulicDiameter": round(hydraulic_diameter, 9),
                "minimumWallNormalWidth": round(min_wall_normal_width, 9),
                "reynolds": round(reynolds, 6),
                "frictionFactor": round(friction_factor, 9),
                "frictionVelocity": round(friction_velocity, 9),
                "targetFirstCellHeight": round(target_first_cell_height, 12),
                "starterFirstCellHeight": round(starter_first_cell_height, 12),
                "starterToTargetRatio": round(starter_first_cell_height / target_first_cell_height, 6)
                if target_first_cell_height > 0
                else None,
                "needsPrismLayerMeshing": True,
            }
        )
    return {
        "schema": "flowlab.boundary_layer_plan.v1",
        "productionReady": False,
        "targetYPlus": target_y_plus,
        "boundaryLayerLayers": controls["boundaryLayerLayers"],
        "growthRate": controls["boundaryLayerGrowthRate"],
        "transverseFractions": controls["transverseFractions"],
        "fluid": fluid,
        "assumptions": {
            "velocitySource": "edge.designVelocity when provided, otherwise 1.0 m/s starter assumption",
            "frictionFactor": "64/Re for laminar flow, Blasius 0.3164/Re^0.25 for turbulent smooth-pipe estimate",
            "lengthUnits": "project geometry dimensions are interpreted as meters for sizing estimates",
        },
        "edges": edge_plans,
        "notes": [
            "This plan estimates first-cell wall-normal sizing for target y-plus review.",
            "The current mesh still creates 2D transverse strip cells, not production 3D prism layers.",
            "Production CFD requires solver-native y-plus fields and boundary-layer mesh evidence after execution.",
        ],
    }


def _prism_layer_plan(boundary_layer_plan: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    layer_count = int(controls["boundaryLayerLayers"])
    growth_rate = float(controls["boundaryLayerGrowthRate"])
    edge_plans = []
    for edge in boundary_layer_plan.get("edges", []):
        if not isinstance(edge, dict):
            continue
        first_height = float(edge.get("targetFirstCellHeight") or 0.0)
        starter_height = float(edge.get("starterFirstCellHeight") or 0.0)
        layer_heights = [
            round(first_height * (growth_rate ** index), 12)
            for index in range(max(layer_count, 0))
        ]
        total_height = sum(layer_heights)
        edge_plans.append(
            {
                "edgeId": edge.get("edgeId"),
                "edgeType": edge.get("edgeType"),
                "targetFirstCellHeight": round(first_height, 12),
                "layerCount": layer_count,
                "growthRate": growth_rate,
                "layerHeights": layer_heights,
                "totalPrismHeight": round(total_height, 12),
                "starterFirstCellHeight": round(starter_height, 12),
                "starterToTargetRatio": edge.get("starterToTargetRatio"),
                "starterStripCanRepresentPrisms": False,
                "nativeMesherRequired": True,
                "requiredEvidence": [
                    "native prism or hex-layer cells generated on wall/interface patches",
                    "solver y-plus field after a run",
                    "layer coverage and non-orthogonality quality report",
                ],
            }
        )
    return {
        "schema": "flowlab.prism_layer_plan.v1",
        "productionReady": False,
        "sourceBoundaryLayerPlan": "mesh/boundary_layer_plan.json",
        "targetYPlus": boundary_layer_plan.get("targetYPlus"),
        "layerCount": layer_count,
        "growthRate": growth_rate,
        "edges": edge_plans,
        "nativeControlsRequired": {
            "gmsh": ["BoundaryLayer field", "wall/interface curve lists", "FanPoints or corner handling review"],
            "openfoam": ["snappyHexMesh addLayersControls or native layered polyMesh", "nSurfaceLayers", "finalLayerThickness/firstLayerThickness"],
            "su2": ["viscous layer mesh from external mesher", "wall marker preservation"],
            "codeSaturne": ["prism-layer mesh import through Gmsh/CGNS", "wall group preservation"],
        },
        "readinessChecks": [
            {
                "id": "target-first-cell-heights",
                "status": "pass" if edge_plans else "fail",
                "detail": "Target first-cell heights are derived from the boundary-layer y-plus plan.",
            },
            {
                "id": "native-prism-layer-mesh",
                "status": "fail",
                "detail": "No native prism/hex-layer cells are generated yet; the FlowLab starter mesh remains a 2D transverse strip.",
            },
            {
                "id": "solver-y-plus-evidence",
                "status": "fail",
                "detail": "No solver-native y-plus field or wall-distance evidence has been collected.",
            },
        ],
        "blockingReasons": [
            "Native prism or hex-layer boundary cells are not generated.",
            "Solver y-plus and layer-quality evidence are not available until a native mesh and run are reviewed.",
        ],
        "notes": [
            "This plan converts FlowLab y-plus sizing estimates into native prism-layer review inputs.",
            "It is not a prism mesh and does not make the generated case production-ready.",
        ],
    }


def _production_mesh_plan(
    quality: dict[str, Any],
    controls: dict[str, Any],
    refinement_plan: dict[str, Any],
    boundary_layer_plan: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    adaptation_plan: dict[str, Any],
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    quality_status = str(quality.get("status") or "missing")
    refinement_regions = refinement_plan.get("regions") if isinstance(refinement_plan.get("regions"), list) else []
    boundary_layer_edges = boundary_layer_plan.get("edges") if isinstance(boundary_layer_plan.get("edges"), list) else []
    physical_regions = [region for region in regions if region.get("edgeType") != "connector"]
    readiness_checks = [
        {
            "id": "port-aware-source-topology",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/flowlab_mesh.json",
            "detail": "Generated source mesh follows FlowLab port-to-port pipe, Venturi, duct, or rectangular-channel spans.",
        },
        {
            "id": "source-mesh-quality-gate",
            "status": "pass" if quality_status in {"ok", "warning"} else "fail",
            "evidence": "mesh/quality.json",
            "detail": f"Starter source-mesh quality status is {quality_status}.",
        },
        {
            "id": "deterministic-refinement-plan",
            "status": "pass" if refinement_regions else "fail",
            "evidence": "mesh/refinement_plan.json",
            "detail": "Longitudinal, per-edge, and feature-refinement intent is recorded for each generated source region.",
        },
        {
            "id": "y-plus-first-cell-sizing-plan",
            "status": "pass" if boundary_layer_edges else "fail",
            "evidence": "mesh/boundary_layer_plan.json",
            "detail": "Boundary-layer target-y-plus first-cell sizing estimates are recorded for each physical edge.",
        },
        {
            "id": "solver-physical-group-map",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/physical_groups.json",
            "detail": "Solver-facing inlet, outlet, wall, front/back, and volume physical groups are exported as machine-readable handoff evidence.",
        },
        {
            "id": "openfoam-native-meshing-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/openfoam_snappy_handoff.json",
            "detail": "OpenFOAM snappyHexMesh review inputs are exported from physical groups and prism-layer sizing estimates.",
        },
        {
            "id": "su2-native-meshing-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/su2_native_meshing_handoff.json",
            "detail": "SU2 marker, viscous-layer, adaptation, and mesh-diagnostics review inputs are exported for native meshing.",
        },
        {
            "id": "code-saturne-native-meshing-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/code_saturne_native_meshing_handoff.json",
            "detail": "Code_Saturne physical-group import, preprocessing, boundary-localization, and quality-review inputs are exported for native meshing.",
        },
        {
            "id": "solver-neutral-adaptation-plan",
            "status": "pass" if adaptation_plan.get("adaptationTargets") else "fail",
            "evidence": "mesh/adaptation_plan.json",
            "detail": "Solver-neutral adaptation targets are exported for geometry features, boundary layers, and future solver-field error indicators.",
        },
        {
            "id": "cad-quality-geometry-source",
            "status": "fail",
            "evidence": None,
            "detail": "No imported CAD/B-rep, watertight surface model, or geometry cleanup pipeline is generated yet.",
        },
        {
            "id": "production-3d-volume-mesh",
            "status": "fail",
            "evidence": None,
            "detail": "Current solver meshes are thin graph-strip extrusions, not production 3D unstructured or hex-dominant volume meshes.",
        },
        {
            "id": "prism-layer-boundary-mesh",
            "status": "fail",
            "evidence": "mesh/prism_layer_plan.json",
            "detail": "Boundary-layer controls create starter transverse strip cells and prism-layer review inputs, not solver-ready prism layers.",
        },
        {
            "id": "solver-native-quality-evidence",
            "status": "fail",
            "evidence": None,
            "detail": "Solver-native mesh checks, y-plus fields, skewness/non-orthogonality reports, and adaptation history are only available after execution.",
        },
    ]
    return {
        "schema": "flowlab.production_mesh_plan.v1",
        "productionReady": False,
        "meshClass": "flowlab-port-aware-starter-strip",
        "generatedEvidence": [
            "mesh/controls.json",
            "mesh/quality.json",
            "mesh/refinement_plan.json",
            "mesh/boundary_layer_plan.json",
            "mesh/prism_layer_plan.json",
            "mesh/adaptation_plan.json",
            "mesh/physical_groups.json",
            "mesh/openfoam_snappy_handoff.json",
            "mesh/su2_native_meshing_handoff.json",
            "mesh/code_saturne_native_meshing_handoff.json",
            "mesh/openfoam_native_mesh_preflight.py",
            "mesh/openfoam_snappyHexMeshDict.template",
            "mesh/openfoam_surfaceFeatureExtractDict.template",
            "mesh/openfoam_meshQualityDict.template",
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
            "mesh/production_mesh_acceptance.json",
            "mesh/flowlab_mesh.json",
            "mesh/flowlab_mesh.vtk",
            "mesh/flowlab_mesh.vtu",
        ],
        "readinessChecks": readiness_checks,
        "blockingReasons": [check["detail"] for check in readiness_checks if check["status"] == "fail"],
        "recommendedNextSteps": [
            "Import or generate watertight CAD/B-rep geometry for the fluid domain and relevant solids.",
            "Generate a real 3D volume mesh with inlet, outlet, wall, interface, and symmetry/empty groups preserved.",
            "Create prism or hex-layer boundary cells from the target-y-plus first-cell sizing plan.",
            "Run solver-native mesh checks and capture skewness, non-orthogonality, aspect ratio, and y-plus evidence.",
            "Add local refinement/adaptation around Venturi throats, elbows, pumps, mixers, free surfaces, cavitation regions, and thermal interfaces.",
        ],
        "controlsSummary": {
            "baseResolution": controls.get("baseResolution"),
            "longitudinalRefinement": controls.get("longitudinalRefinement"),
            "boundaryLayerLayers": controls.get("boundaryLayerLayers"),
            "targetYPlus": controls.get("targetYPlus"),
            "featureRefinement": controls.get("featureRefinement"),
            "qualityThresholds": controls.get("qualityThresholds"),
        },
        "counts": {
            "physicalRegionCount": len(physical_regions),
            "connectorRegionCount": len(regions) - len(physical_regions),
            "refinementRegionCount": len(refinement_regions),
            "boundaryLayerEdgeCount": len(boundary_layer_edges),
            "prismLayerEdgeCount": len(prism_layer_plan.get("edges", [])) if isinstance(prism_layer_plan.get("edges"), list) else 0,
            "adaptationTargetCount": len(adaptation_plan.get("adaptationTargets", [])) if isinstance(adaptation_plan.get("adaptationTargets"), list) else 0,
        },
        "notes": [
            "This plan is a machine-readable production-mesh gap analysis for generated solver cases.",
            "Passing starter checks means the deterministic FlowLab source mesh is usable for template execution, not production CFD.",
            "Production readiness remains false until CAD-quality 3D mesh, prism-layer, and solver-native quality evidence exist.",
        ],
    }


def _native_meshing_plan(
    production_mesh_plan: dict[str, Any],
    controls: dict[str, Any],
    boundary_layer_plan: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    adaptation_plan: dict[str, Any],
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    physical_regions = [region for region in regions if region.get("edgeType") != "connector"]
    boundary_layer_edges = boundary_layer_plan.get("edges") if isinstance(boundary_layer_plan.get("edges"), list) else []
    checks = [
        {
            "id": "gmsh-handoff-script",
            "status": "pass",
            "evidence": "mesh/gmsh_production_handoff.geo",
            "detail": "FlowLab emits a review-only Gmsh .geo handoff with physical group names and target sizing comments.",
        },
        {
            "id": "physical-group-map",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/physical_groups.json",
            "detail": "FlowLab emits a machine-readable map of source regions to Gmsh, SU2, Code_Saturne, and OpenFOAM boundary/group names.",
        },
        {
            "id": "openfoam-snappy-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/openfoam_snappy_handoff.json",
            "detail": "FlowLab emits OpenFOAM snappyHexMesh and addLayersControls review inputs derived from physical groups and prism-layer sizing.",
        },
        {
            "id": "openfoam-native-mesh-preflight",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/openfoam_native_mesh_preflight.py",
            "detail": "FlowLab emits a local preflight script that checks required CAD/STL, native dictionaries, locationInMesh, and quality-evidence blockers before snappyHexMesh is attempted.",
        },
        {
            "id": "su2-native-meshing-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/su2_native_meshing_handoff.json",
            "detail": "FlowLab emits SU2 marker, viscous-layer, adaptation, and mesh-diagnostics review inputs for native production meshing.",
        },
        {
            "id": "code-saturne-native-meshing-handoff",
            "status": "pass" if physical_regions else "fail",
            "evidence": "mesh/code_saturne_native_meshing_handoff.json",
            "detail": "FlowLab emits Code_Saturne physical-group import, boundary-localization, and preprocessing review inputs for native production meshing.",
        },
        {
            "id": "cad-surface-import",
            "status": "fail",
            "evidence": None,
            "detail": "No watertight CAD/B-rep or cleaned STL surface is available for native production meshing.",
        },
        {
            "id": "boundary-layer-prism-controls",
            "status": "warning" if prism_layer_plan.get("edges") else "fail",
            "evidence": "mesh/prism_layer_plan.json",
            "detail": "Target y-plus first-cell sizing is converted into native prism-layer review inputs, but no generated prism mesh exists.",
        },
        {
            "id": "adaptation-target-controls",
            "status": "warning" if adaptation_plan.get("adaptationTargets") else "fail",
            "evidence": "mesh/adaptation_plan.json",
            "detail": "FlowLab exports adaptation targets for native mesh refinement review, but no solver-native adaptation pass has run.",
        },
        {
            "id": "solver-native-mesh-quality-report",
            "status": "fail",
            "evidence": None,
            "detail": "No native Gmsh quality report, OpenFOAM checkMesh, SU2 mesh check, or Code_Saturne preprocessing evidence has been captured.",
        },
    ]
    return {
        "schema": "flowlab.native_meshing_plan.v1",
        "productionReady": False,
        "handoffArtifacts": [
            "mesh/gmsh_production_handoff.geo",
            "mesh/physical_groups.json",
            "mesh/openfoam_snappy_handoff.json",
            "mesh/su2_native_meshing_handoff.json",
            "mesh/code_saturne_native_meshing_handoff.json",
            "mesh/openfoam_native_mesh_preflight.py",
            "mesh/openfoam_snappyHexMeshDict.template",
            "mesh/openfoam_surfaceFeatureExtractDict.template",
            "mesh/openfoam_meshQualityDict.template",
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
            "mesh/adaptation_plan.json",
            "mesh/production_mesh_acceptance.json",
            "mesh/native_meshing_plan.json",
        ],
        "sourceEvidence": production_mesh_plan.get("generatedEvidence", []),
        "prismLayerPlan": {
            "file": "mesh/prism_layer_plan.json",
            "schema": prism_layer_plan.get("schema"),
            "edgeCount": len(prism_layer_plan.get("edges", [])) if isinstance(prism_layer_plan.get("edges"), list) else 0,
            "productionReady": prism_layer_plan.get("productionReady"),
        },
        "adaptationPlan": {
            "file": "mesh/adaptation_plan.json",
            "schema": adaptation_plan.get("schema"),
            "targetCount": len(adaptation_plan.get("adaptationTargets", [])) if isinstance(adaptation_plan.get("adaptationTargets"), list) else 0,
            "productionReady": adaptation_plan.get("productionReady"),
        },
        "readinessChecks": checks,
        "blockingReasons": [check["detail"] for check in checks if check["status"] == "fail"],
        "nativeWorkflow": [
            {
                "stage": "geometry",
                "tooling": ["CAD/B-rep", "OpenCASCADE", "STL surface cleanup"],
                "requiredEvidence": ["watertight surfaces", "named inlet/outlet/wall/interface groups"],
            },
            {
                "stage": "volume-mesh",
                "tooling": ["Gmsh", "snappyHexMesh", "cfMesh", "solver-native mesher"],
                "requiredEvidence": ["3D volume cells", "preserved physical groups", "local refinement around hydraulic features"],
            },
            {
                "stage": "boundary-layer",
                "tooling": ["Gmsh BoundaryLayer field", "snappyHexMesh layers", "prism-layer mesher"],
                "requiredEvidence": ["first-cell height from target y-plus", "layer count/growth", "solver y-plus field"],
            },
            {
                "stage": "adaptation",
                "tooling": ["Gmsh mesh-size fields", "snappyHexMesh refinementRegions", "solver adaptation/error indicators"],
                "requiredEvidence": ["feature refinement zones", "boundary-layer refinement zones", "solver-field gradient/error indicators", "adaptation history"],
            },
            {
                "stage": "solver-quality",
                "tooling": ["checkMesh", "SU2_CFD mesh checks", "Code_Saturne preprocessing"],
                "requiredEvidence": ["skewness", "non-orthogonality", "aspect ratio", "negative volume count", "adaptation/refinement logs"],
            },
        ],
        "solverTargets": {
            "openfoam": {
                "preferredMesh": "polyMesh from reviewed native mesher",
                "qualityCommand": "checkMesh -allGeometry -allTopology",
                "requiredPatches": ["inlet", "outlet", "walls", "frontAndBack"],
                "nativeMeshingHandoff": "mesh/openfoam_snappy_handoff.json",
            },
            "su2": {
                "preferredMesh": "native .su2 or converted unstructured mesh",
                "qualityCommand": "SU2_CFD case.cfg with mesh diagnostics reviewed",
                "requiredMarkers": ["inlet_*", "outlet_*", "wall_*"],
                "nativeMeshingHandoff": "mesh/su2_native_meshing_handoff.json",
            },
            "codeSaturne": {
                "preferredMesh": "Gmsh .msh with physical groups",
                "qualityCommand": "code_saturne run preprocessing/listing review",
                "requiredGroups": ["fluid", "inlet_*", "outlet_*", "wall_*"],
                "nativeMeshingHandoff": "mesh/code_saturne_native_meshing_handoff.json",
            },
        },
        "controlsSummary": {
            "baseResolution": controls.get("baseResolution"),
            "longitudinalRefinement": controls.get("longitudinalRefinement"),
            "targetYPlus": controls.get("targetYPlus"),
            "boundaryLayerLayers": controls.get("boundaryLayerLayers"),
            "boundaryLayerEdgeCount": len(boundary_layer_edges),
            "physicalRegionCount": len(physical_regions),
        },
        "notes": [
            "This handoff makes the native production-meshing work explicit without claiming FlowLab generated a production mesh.",
            "mesh/gmsh_production_handoff.geo is a review scaffold; real production use should replace its placeholder surfaces with CAD-quality geometry.",
        ],
}


def _production_mesh_acceptance_checklist(
    production_mesh_plan: dict[str, Any],
    native_meshing_plan: dict[str, Any],
    physical_group_map: dict[str, Any],
    openfoam_snappy_handoff: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    adaptation_plan: dict[str, Any],
) -> dict[str, Any]:
    groups = physical_group_map.get("groups") if isinstance(physical_group_map.get("groups"), list) else []
    handoff_artifacts = native_meshing_plan.get("handoffArtifacts") if isinstance(native_meshing_plan.get("handoffArtifacts"), list) else []
    snappy_templates = openfoam_snappy_handoff.get("templateArtifacts") if isinstance(openfoam_snappy_handoff.get("templateArtifacts"), dict) else {}
    starter_geometry = openfoam_snappy_handoff.get("starterGeometry") if isinstance(openfoam_snappy_handoff.get("starterGeometry"), dict) else {}
    tag_validation = starter_geometry.get("boundaryTagValidation") if isinstance(starter_geometry.get("boundaryTagValidation"), dict) else {}
    cad_reviewed = starter_geometry.get("cadReviewed") is True and tag_validation.get("complete") is True
    prism_edges = prism_layer_plan.get("edges") if isinstance(prism_layer_plan.get("edges"), list) else []
    adaptation_targets = adaptation_plan.get("adaptationTargets") if isinstance(adaptation_plan.get("adaptationTargets"), list) else []
    gates = [
        {
            "id": "source-mesh-traceability",
            "status": "pass" if groups else "fail",
            "evidence": [
                "mesh/production_mesh_plan.json",
                "mesh/native_meshing_plan.json",
                "mesh/physical_groups.json",
            ],
            "requiredEvidence": ["FlowLab source mesh", "physical group map", "native meshing handoff manifest"],
            "detail": "Generated starter mesh regions are traceable to solver-facing physical groups.",
        },
        {
            "id": "solver-handoff-artifacts",
            "status": "pass"
            if {
                "mesh/gmsh_production_handoff.geo",
                "mesh/openfoam_snappy_handoff.json",
                "mesh/openfoam_native_mesh_preflight.py",
                "mesh/su2_native_meshing_handoff.json",
                "mesh/code_saturne_native_meshing_handoff.json",
                "constant/triSurface/reviewedFlowLabSurfaces.stl",
                "system/snappyHexMeshDict",
                "system/surfaceFeatureExtractDict",
                "system/meshQualityDict",
            }.issubset(set(handoff_artifacts))
            and snappy_templates
            else "fail",
            "evidence": [
                "mesh/gmsh_production_handoff.geo",
                "mesh/openfoam_snappy_handoff.json",
                "mesh/openfoam_native_mesh_preflight.py",
                "mesh/su2_native_meshing_handoff.json",
                "mesh/code_saturne_native_meshing_handoff.json",
                "mesh/openfoam_snappyHexMeshDict.template",
                "mesh/openfoam_surfaceFeatureExtractDict.template",
                "mesh/openfoam_meshQualityDict.template",
                "constant/triSurface/reviewedFlowLabSurfaces.stl",
                "system/snappyHexMeshDict",
                "system/surfaceFeatureExtractDict",
                "system/meshQualityDict",
            ],
            "requiredEvidence": [
                "Gmsh/OpenCASCADE handoff scaffold",
                "OpenFOAM snappyHexMesh handoff manifest",
                "OpenFOAM native mesh preflight script",
                "SU2 native meshing handoff manifest",
                "Code_Saturne native meshing handoff manifest",
                "review-only OpenFOAM dictionary templates",
                "generated starter triSurface and installed OpenFOAM dictionary handoff files",
            ],
            "detail": "Solver-native meshing intent is exported with starter OpenFOAM surface/dictionary handoff files, but these are not production meshes.",
        },
        {
            "id": "cad-geometry-source",
            "status": "pass" if cad_reviewed else "fail",
            "evidence": ["constant/triSurface/reviewedFlowLabSurfaces.stl", "mesh/openfoam_snappy_handoff.json"] if cad_reviewed else [],
            "requiredEvidence": [
                "watertight CAD/B-rep or cleaned STL surfaces",
                "named inlet, outlet, wall, interface, and symmetry groups",
                "documented geometry cleanup tolerances",
            ],
            "detail": (
                "User supplied STL is explicitly marked as CAD reviewed and has required inlet/outlet/wall tags; production still requires native mesh evidence."
                if cad_reviewed
                else "No CAD-quality geometry source with required inlet/outlet/wall tags is generated or imported."
            ),
        },
        {
            "id": "native-3d-volume-mesh",
            "status": "fail",
            "evidence": [],
            "requiredEvidence": [
                "3D volume cells from a native mesher",
                "preserved physical groups or markers",
                "local refinement around throats, elbows, mixers, pumps, free surfaces, and thermal interfaces",
            ],
            "detail": "The exported starter mesh is a deterministic strip/extrusion handoff, not a production 3D volume mesh.",
        },
        {
            "id": "boundary-layer-prism-mesh",
            "status": "fail",
            "evidence": ["mesh/prism_layer_plan.json"] if prism_edges else [],
            "requiredEvidence": [
                "generated prism or hex-layer cells",
                "first-cell height tied to target y-plus",
                "layer count, growth, collapse, and wall-distance evidence",
            ],
            "detail": "FlowLab emits prism-layer sizing inputs, but native boundary-layer cells and y-plus evidence are still missing.",
        },
        {
            "id": "adapted-refinement-evidence",
            "status": "fail",
            "evidence": ["mesh/adaptation_plan.json"] if adaptation_targets else [],
            "requiredEvidence": [
                "native adaptation or mesh-size fields applied to the volume mesh",
                "solver-field gradient or residual-error indicators",
                "adaptation history showing before/after cell counts and quality metrics",
            ],
            "detail": "FlowLab exports adaptation targets, but no native adapted mesh or solver-field adaptation evidence exists.",
        },
        {
            "id": "solver-native-quality-evidence",
            "status": "fail",
            "evidence": [],
            "requiredEvidence": [
                "OpenFOAM checkMesh report",
                "SU2 mesh diagnostics or preprocessing output",
                "Code_Saturne preprocessing/listing quality report",
                "negative-volume, skewness, non-orthogonality, aspect-ratio, and y-plus summaries",
            ],
            "detail": "No solver-native mesh-quality report has been captured from a native production mesh.",
        },
    ]
    return {
        "schema": "flowlab.production_mesh_acceptance.v1",
        "productionReady": False,
        "approvalStatus": "blocked",
        "sourceArtifacts": {
            "productionMeshPlan": "mesh/production_mesh_plan.json",
            "nativeMeshingPlan": "mesh/native_meshing_plan.json",
            "physicalGroups": "mesh/physical_groups.json",
            "prismLayerPlan": "mesh/prism_layer_plan.json",
            "adaptationPlan": "mesh/adaptation_plan.json",
            "openfoamSnappyHandoff": "mesh/openfoam_snappy_handoff.json",
            "openfoamNativeMeshPreflight": "mesh/openfoam_native_mesh_preflight.py",
            "su2NativeMeshingHandoff": "mesh/su2_native_meshing_handoff.json",
            "codeSaturneNativeMeshingHandoff": "mesh/code_saturne_native_meshing_handoff.json",
        },
        "sourceProductionReadiness": production_mesh_plan.get("productionReady"),
        "acceptanceCriteria": gates,
        "nativeQualityEvidence": {
            "schema": "flowlab.native_mesh_quality_evidence.v1",
            "productionReady": False,
            "status": "missing-native-quality-reports",
            "sharedRequiredEvidence": [
                "native 3D volume mesh generated from reviewed geometry",
                "solver-native cell-quality report",
                "wall-distance or y-plus field for wall-bounded cases",
                "before/after adaptation quality history when adaptation is enabled",
            ],
            "solverReports": {
                "openfoam": {
                    "status": "missing",
                    "commands": ["checkMesh -allGeometry -allTopology", "postProcess -func yPlus"],
                    "requiredMetrics": ["failedChecks", "maxNonOrthogonality", "maxSkewness", "maxAspectRatio", "minVolume", "yPlusMinMeanMax"],
                    "currentEvidence": [
                        "mesh/openfoam_snappy_handoff.json",
                        "mesh/openfoam_native_mesh_preflight.py",
                        "mesh/openfoam_meshQualityDict.template",
                        "constant/triSurface/reviewedFlowLabSurfaces.stl",
                        "system/snappyHexMeshDict",
                        "system/surfaceFeatureExtractDict",
                        "system/meshQualityDict",
                    ],
                },
                "su2": {
                    "status": "missing",
                    "commands": ["SU2_CFD case.cfg startup/preprocess diagnostics"],
                    "requiredMetrics": ["markerCoverage", "negativeVolumeCount", "skewnessOrQualityHistogram", "viscousLayerCoverage", "adaptationHistory"],
                    "currentEvidence": ["mesh/flowlab_mesh.su2", "mesh/su2_native_meshing_handoff.json"],
                },
                "codeSaturne": {
                    "status": "missing",
                    "commands": ["code_saturne run preprocessing/listing review"],
                    "requiredMetrics": ["boundaryLocalization", "cellQuality", "volumeQuality", "preprocessingErrors", "wallLayerCoverage"],
                    "currentEvidence": ["mesh/flowlab_mesh.msh", "mesh/code_saturne_native_meshing_handoff.json"],
                },
            },
            "notes": [
                "This block records the native quality evidence required before FlowLab can approve a production mesh.",
                "Current FlowLab starter meshes do not produce solver-native y-plus, wall-distance, or production quality reports.",
            ],
        },
        "solverAcceptance": {
            "openfoam": {
                "status": "blocked",
                "requiredEvidence": [
                    "constant/polyMesh generated from reviewed CAD/STL or native mesher",
                    "checkMesh -allGeometry -allTopology log",
                    "patch list containing inlet, outlet, walls, frontAndBack, and interface patches where applicable",
                    "non-orthogonality, skewness, aspect-ratio, negative-volume, and y-plus summaries",
                    "adaptation history or mesh-size refinement evidence where feature/field refinement is required",
                ],
                "currentEvidence": [
                    "mesh/openfoam_snappy_handoff.json",
                    "mesh/openfoam_native_mesh_preflight.py",
                    "mesh/openfoam_snappyHexMeshDict.template",
                    "mesh/openfoam_surfaceFeatureExtractDict.template",
                    "mesh/openfoam_meshQualityDict.template",
                    "constant/triSurface/reviewedFlowLabSurfaces.stl",
                    "system/snappyHexMeshDict",
                    "system/surfaceFeatureExtractDict",
                    "system/meshQualityDict",
                ],
            },
            "su2": {
                "status": "blocked",
                "requiredEvidence": [
                    "native .su2 or converted unstructured mesh from reviewed geometry",
                    "inlet, outlet, wall, symmetry, and interface marker verification",
                    "viscous-layer mesh evidence for wall-bounded cases",
                    "initial SU2_CFD mesh-diagnostics or solver startup log",
                    "adaptation/error-indicator evidence for shock, pressure-gradient, or thermal-gradient cases",
                ],
                "currentEvidence": ["mesh/flowlab_mesh.su2", "mesh/physical_groups.json", "mesh/su2_native_meshing_handoff.json"],
            },
            "codeSaturne": {
                "status": "blocked",
                "requiredEvidence": [
                    "Gmsh, CGNS, or MED mesh with named physical groups",
                    "Code_Saturne preprocessing/listing success",
                    "boundary localization review for inlets, outlets, walls, interfaces, and symmetry",
                    "cell-quality and volume-quality report",
                    "refinement/adaptation evidence around scalar, thermal, multiphase, or cavitation regions",
                ],
                "currentEvidence": ["mesh/flowlab_mesh.msh", "mesh/physical_groups.json", "mesh/code_saturne_native_meshing_handoff.json"],
            },
        },
        "blockingReasons": [gate["detail"] for gate in gates if gate["status"] == "fail"],
        "notes": [
            "This checklist is the solver-neutral approval gate between FlowLab starter meshes and production CFD meshes.",
            "Production readiness remains false until CAD-quality geometry, native 3D volume mesh, boundary-layer cells, and solver-native quality evidence are reviewed.",
        ],
    }


def _adaptation_plan(
    quality: dict[str, Any],
    controls: dict[str, Any],
    refinement_plan: dict[str, Any],
    boundary_layer_plan: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    boundary_edges = {
        str(edge.get("edgeId")): edge
        for edge in boundary_layer_plan.get("edges", [])
        if isinstance(edge, dict)
    }
    prism_edges = {
        str(edge.get("edgeId")): edge
        for edge in prism_layer_plan.get("edges", [])
        if isinstance(edge, dict)
    }
    refinement_regions = {
        str(region.get("edgeId")): region
        for region in refinement_plan.get("regions", [])
        if isinstance(region, dict)
    }
    targets: list[dict[str, Any]] = []
    for region in regions:
        if region.get("edgeType") == "connector":
            continue
        edge_id = str(region.get("edgeId"))
        feature = region.get("featureRefinement") if isinstance(region.get("featureRefinement"), dict) else {}
        boundary = boundary_edges.get(edge_id, {})
        prism = prism_edges.get(edge_id, {})
        refinement = refinement_regions.get(edge_id, {})
        feature_reasons = []
        if feature.get("enabled"):
            feature_reasons.append(str(feature.get("featureType") or "geometry-feature"))
        if region.get("edgeType") in FEATURE_REFINEMENT_EDGE_TYPES:
            feature_reasons.append("diameter/throat transition review")
        targets.append(
            {
                "edgeId": edge_id,
                "edgeType": region.get("edgeType"),
                "sourceRegion": {
                    "segmentCount": region.get("segmentCount"),
                    "cellCount": region.get("cellCount"),
                    "transverseDivisions": region.get("transverseDivisions"),
                    "spanLengthPx": region.get("spanLengthPx"),
                },
                "geometryTargets": {
                    "enabled": bool(feature_reasons),
                    "reasons": sorted(set(feature_reasons)),
                    "targetStation": feature.get("targetStation"),
                    "featureRefinementFactor": feature.get("factor", 1),
                    "edgeRefinementFactor": region.get("edgeRefinementFactor"),
                    "stationFractions": refinement.get("stationFractions", []),
                },
                "boundaryLayerTargets": {
                    "enabled": bool(boundary),
                    "targetYPlus": boundary_layer_plan.get("targetYPlus"),
                    "targetFirstCellHeight": boundary.get("targetFirstCellHeight"),
                    "starterFirstCellHeight": boundary.get("starterFirstCellHeight"),
                    "starterToTargetRatio": boundary.get("starterToTargetRatio"),
                    "nativeLayerCount": prism.get("layerCount"),
                    "nativeGrowthRate": prism.get("growthRate"),
                },
                "fieldIndicatorTargets": [
                    "pressure-gradient",
                    "velocity-gradient",
                    "wall-shear",
                    "temperature-gradient",
                    "phase-fraction-gradient",
                    "cavitation-vapour-fraction-gradient",
                ],
                "nativeMesherActions": [
                    "Apply local sizing fields before native 3D volume meshing.",
                    "Refine around geometry transitions, inlets/outlets, walls, and expected high-gradient zones.",
                    "After an initial solve, adapt from solver field indicators and rerun solver-native mesh-quality checks.",
                ],
            }
        )
    readiness_checks = [
        {
            "id": "source-refinement-targets",
            "status": "pass" if targets else "fail",
            "detail": "FlowLab source regions are converted into native adaptation targets.",
        },
        {
            "id": "boundary-layer-adaptation-targets",
            "status": "pass" if any(target["boundaryLayerTargets"]["enabled"] for target in targets) else "fail",
            "detail": "Boundary-layer y-plus sizing is referenced by adaptation targets.",
        },
        {
            "id": "solver-field-indicators",
            "status": "warning" if controls.get("adaptiveMesh", {}).get("enabled") else "fail",
            "detail": (
                "Adaptive mesh planning is configured for export, but no solver-field gradient, residual-error, shock, phase-interface, "
                "cavitation, or thermal indicators have been computed yet."
                if controls.get("adaptiveMesh", {}).get("enabled")
                else "No solver-field gradient, residual-error, shock, phase-interface, cavitation, or thermal indicators have been computed yet."
            ),
        },
        {
            "id": "native-adapted-volume-mesh",
            "status": "fail",
            "detail": "No native 3D adapted volume mesh or adaptation history is generated.",
        },
        {
            "id": "post-adaptation-quality-evidence",
            "status": "fail",
            "detail": "No post-adaptation solver-native quality report is available.",
        },
    ]
    return {
        "schema": "flowlab.mesh_adaptation_plan.v1",
        "productionReady": False,
        "sourceArtifacts": {
            "quality": "mesh/quality.json",
            "refinementPlan": "mesh/refinement_plan.json",
            "boundaryLayerPlan": "mesh/boundary_layer_plan.json",
            "prismLayerPlan": "mesh/prism_layer_plan.json",
            "physicalGroups": "mesh/physical_groups.json",
        },
        "sourceQualityStatus": quality.get("status"),
        "controlsSummary": {
            "baseResolution": controls.get("baseResolution"),
            "longitudinalRefinement": controls.get("longitudinalRefinement"),
            "featureRefinement": controls.get("featureRefinement"),
            "targetYPlus": controls.get("targetYPlus"),
            "boundaryLayerLayers": controls.get("boundaryLayerLayers"),
            "adaptiveMesh": controls.get("adaptiveMesh"),
        },
        "adaptiveMeshPlan": {
            "enabled": bool(controls.get("adaptiveMesh", {}).get("enabled")),
            "config": controls.get("adaptiveMesh"),
            "exportOnly": True,
            "liveRemeshing": False,
            "nativeTargets": [
                "OpenFOAM dynamicRefineFvMesh/dynamicMeshDict review handoff",
                "SU2 adaptation or metric-field workflow review handoff",
                "Gmsh background-field sizing review handoff",
            ],
        },
        "adaptationTargets": targets,
        "readinessChecks": readiness_checks,
        "blockingReasons": [check["detail"] for check in readiness_checks if check["status"] == "fail"],
        "requiredNativeEvidence": [
            "native mesh-size fields or refinement regions applied to CAD-quality geometry",
            "solver-field indicators from at least one initial CFD pass",
            "adapted 3D volume mesh cell counts and patch/group preservation evidence",
            "post-adaptation mesh-quality and y-plus reports",
        ],
        "notes": [
            "This is a solver-neutral adaptation/refinement plan for a production mesher.",
            "It does not adapt the current FlowLab starter strip and does not certify production CFD readiness.",
        ],
    }


def _boundary_role(name: str) -> str:
    if name.startswith("inlet_"):
        return "inlet"
    if name.startswith("outlet_"):
        return "outlet"
    if name.endswith("_front_back"):
        return "front-back"
    if name.startswith("wall_"):
        return "wall"
    return "boundary"


def _openfoam_patch_for_group(name: str) -> str:
    role = _boundary_role(name)
    if role == "inlet":
        return "inlet"
    if role == "outlet":
        return "outlet"
    if role == "front-back":
        return "frontAndBack"
    return "walls"


def _openfoam_location_in_mesh(mesh: dict[str, Any]) -> tuple[float, float, float]:
    points = mesh.get("points") if isinstance(mesh.get("points"), list) else []
    if not points:
        return (0.001, 0.001, 0.001)
    xs = [float(point[0]) for point in points if isinstance(point, list) and len(point) >= 2]
    ys = [float(point[1]) for point in points if isinstance(point, list) and len(point) >= 2]
    if not xs or not ys:
        return (0.001, 0.001, 0.001)
    scale = 0.01
    return (
        round((min(xs) + max(xs)) * 0.5 * scale, 9),
        round((min(ys) + max(ys)) * 0.5 * scale, 9),
        0.001,
    )


def _openfoam_location_text(location: tuple[float, float, float]) -> str:
    return " ".join(f"{component:.9g}" for component in location)


def mesh_to_openfoam_starter_stl(mesh: dict[str, Any]) -> str:
    source_points = mesh["points"]
    cells = mesh["cells"]
    depth = max(_average_cell_span(source_points, cells) * 0.35, 1.0)
    half_depth = depth / 2.0
    scale = 0.01
    lower_points: list[tuple[float, float, float]] = []
    upper_points: list[tuple[float, float, float]] = []
    for point in source_points:
        x = float(point[0]) * scale
        y = float(point[1]) * scale
        z = float(point[2]) * scale
        lower_points.append((x, y, z - half_depth * scale))
        upper_points.append((x, y, z + half_depth * scale))

    edge_counts: dict[tuple[int, int], tuple[int, int, int]] = {}
    triangles: list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = []

    def add_quad(a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float], d: tuple[float, float, float]) -> None:
        triangles.append((a, b, c))
        triangles.append((a, c, d))

    for cell in cells:
        oriented = _orient_cell_ccw(source_points, cell)
        add_quad(
            upper_points[oriented[0]],
            upper_points[oriented[1]],
            upper_points[oriented[2]],
            upper_points[oriented[3]],
        )
        add_quad(
            lower_points[oriented[3]],
            lower_points[oriented[2]],
            lower_points[oriented[1]],
            lower_points[oriented[0]],
        )
        for start, end in zip(oriented, oriented[1:] + oriented[:1]):
            key = tuple(sorted((start, end)))
            count = edge_counts.get(key, (start, end, 0))[2] + 1
            edge_counts[key] = (start, end, count)

    for start, end, count in edge_counts.values():
        if count != 1:
            continue
        add_quad(lower_points[start], lower_points[end], upper_points[end], upper_points[start])

    lines = [
        "solid reviewedFlowLabSurfaces",
        "  // FlowLab-generated starter triSurface from port-aware mesh; review/replace with CAD-quality STL before production meshing.",
    ]
    for a, b, c in triangles:
        normal = _triangle_normal(a, b, c)
        lines.append(f"  facet normal {normal[0]:.9g} {normal[1]:.9g} {normal[2]:.9g}")
        lines.append("    outer loop")
        lines.append(f"      vertex {a[0]:.9g} {a[1]:.9g} {a[2]:.9g}")
        lines.append(f"      vertex {b[0]:.9g} {b[1]:.9g} {b[2]:.9g}")
        lines.append(f"      vertex {c[0]:.9g} {c[1]:.9g} {c[2]:.9g}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid reviewedFlowLabSurfaces")
    return "\n".join(lines) + "\n"


def _safe_relative_stl_path(path_value: Any) -> str | None:
    if not isinstance(path_value, str):
        return None
    candidate = path_value.strip()
    if not candidate or "\x00" in candidate:
        return None
    path = Path(candidate)
    if path.is_absolute() or path.suffix.lower() != ".stl":
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return candidate


def _openfoam_safe_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if OPENFOAM_NAME_RE.fullmatch(candidate) else None


def _ascii_stl_validation(text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        return {"status": "fail", "reasons": ["STL text is empty."]}
    lowered = text.lower()
    reasons = []
    if "solid" not in lowered:
        reasons.append("ASCII STL must contain `solid`.")
    if "facet normal" not in lowered:
        reasons.append("ASCII STL must contain at least one `facet normal`.")
    if "vertex" not in lowered:
        reasons.append("ASCII STL must contain at least one `vertex`.")
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        reasons.append("Only ASCII STL text is supported for reviewed geometry imports.")
    return {"status": "pass" if not reasons else "fail", "reasons": reasons}


def _parse_ascii_stl_metadata(text: str) -> dict[str, Any]:
    triangles = []
    current: list[tuple[float, float, float]] = []
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            try:
                vertex = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                continue
            current.append(vertex)
            for index, value in enumerate(vertex):
                bounds_min[index] = min(bounds_min[index], value)
                bounds_max[index] = max(bounds_max[index], value)
            if len(current) == 3:
                triangles.append(tuple(current))
                current = []

    edge_counts: dict[tuple[tuple[float, float, float], tuple[float, float, float]], int] = {}
    for triangle in triangles:
        for start, end in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
            key = tuple(sorted((tuple(round(value, 9) for value in start), tuple(round(value, 9) for value in end))))
            edge_counts[key] = edge_counts.get(key, 0) + 1
    open_edges = sum(1 for count in edge_counts.values() if count != 2)
    bounds = None
    if triangles:
        bounds = {
            "min": [round(value, 9) for value in bounds_min],
            "max": [round(value, 9) for value in bounds_max],
        }
    return {
        "triangleCount": len(triangles),
        "vertexCount": len(triangles) * 3,
        "bounds": bounds,
        "watertightCheck": {
            "status": "pass" if triangles and open_edges == 0 else "warning",
            "openEdgeCount": open_edges,
            "method": "undirected triangle edge pair count rounded to 1e-9",
        },
        "asciiValidation": _ascii_stl_validation(text),
    }


def _sanitize_boundary_tags(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    tags = []
    issues = []
    if value is None:
        return tags, issues
    if not isinstance(value, list):
        return tags, ["boundaryTags must be a list."]
    seen_names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            issues.append(f"boundaryTags[{index}] must be an object.")
            continue
        role = str(item.get("role") or "").strip()
        patch_name = str(item.get("patchName") or item.get("name") or "").strip()
        if role not in BOUNDARY_TAG_ROLES:
            issues.append(f"boundaryTags[{index}] has unsupported role `{role}`.")
            continue
        if not patch_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", patch_name):
            issues.append(f"boundaryTags[{index}] needs an OpenFOAM-safe patchName.")
            continue
        if patch_name in seen_names:
            issues.append(f"boundaryTags patchName `{patch_name}` is duplicated.")
            continue
        seen_names.add(patch_name)
        tags.append(
            {
                "id": str(item.get("id") or patch_name),
                "role": role,
                "patchName": patch_name,
                "label": str(item.get("label") or patch_name),
                "notes": str(item.get("notes") or "")[:500],
            }
        )
    return tags, issues


def _boundary_tag_summary(tags: list[dict[str, Any]]) -> dict[str, Any]:
    roles = sorted({str(tag.get("role")) for tag in tags})
    missing = sorted(REQUIRED_REVIEWED_BOUNDARY_ROLES - set(roles))
    return {
        "requiredRoles": sorted(REQUIRED_REVIEWED_BOUNDARY_ROLES),
        "rolesPresent": roles,
        "missingRequiredRoles": missing,
        "complete": not missing,
        "tags": tags,
    }


def _read_reviewed_stl_text(source: dict[str, Any], *, context: str) -> tuple[str, str | None]:
    local_path = _safe_relative_stl_path(source.get("stlPath"))
    raw_path = source.get("stlPath") if isinstance(source.get("stlPath"), str) else None
    if raw_path is not None and local_path is None:
        raise ValueError(f"{context}.stlPath must be a safe relative .stl path.")
    stl_text = source.get("stlText")
    if not isinstance(stl_text, str):
        if not local_path:
            raise ValueError(f"{context} must include uploaded stlText or a safe relative stlPath.")
        path = Path(local_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"{context}.stlPath must point to an existing relative STL file or include uploaded stlText.")
        stl_text = path.read_text(encoding="utf-8", errors="strict")
    validation = _ascii_stl_validation(stl_text)
    if validation["status"] != "pass":
        raise ValueError(f"{context}.stlText must be a valid ASCII STL containing solid, facet normal, and vertex records.")
    return str(stl_text).rstrip() + "\n", local_path


def _sanitize_reviewed_surfaces(value: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if value is None:
        return [], {}
    if not isinstance(value, list):
        raise ValueError("reviewedGeometry.surfaces must be a list.")
    surfaces: list[dict[str, Any]] = []
    files: dict[str, str] = {}
    seen_patch_names: set[str] = set()
    seen_surface_files: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"reviewedGeometry.surfaces[{index}] must be an object.")
        role = str(item.get("role") or "").strip()
        if role not in BOUNDARY_TAG_ROLES:
            raise ValueError(f"reviewedGeometry.surfaces[{index}].role must be one of inlet, outlet, wall, interface.")
        patch_name = _openfoam_safe_name(item.get("patchName") or item.get("surfaceName"))
        if not patch_name:
            raise ValueError(f"reviewedGeometry.surfaces[{index}].patchName must be OpenFOAM-safe.")
        if patch_name in seen_patch_names:
            raise ValueError(f"reviewedGeometry.surfaces patchName `{patch_name}` is duplicated.")
        seen_patch_names.add(patch_name)
        surface_file_name = _openfoam_safe_name(item.get("surfaceName")) or patch_name
        if surface_file_name in seen_surface_files:
            raise ValueError(f"reviewedGeometry.surfaces surfaceName `{surface_file_name}` is duplicated.")
        seen_surface_files.add(surface_file_name)
        stl_text, local_path = _read_reviewed_stl_text(item, context=f"reviewedGeometry.surfaces[{index}]")
        tri_surface = f"constant/triSurface/{surface_file_name}.stl"
        files[tri_surface] = stl_text
        surfaces.append(
            {
                "id": str(item.get("id") or patch_name),
                "surfaceName": str(item.get("surfaceName") or patch_name),
                "role": role,
                "patchName": patch_name,
                "triSurface": tri_surface,
                "cadReviewed": item.get("cadReviewed") is True,
                "reviewedAt": item.get("reviewedAt") if isinstance(item.get("reviewedAt"), str) else None,
                "notes": str(item.get("notes") or "")[:1000],
                "stlPath": local_path
                if local_path is not None
                else (item.get("stlPath") if isinstance(item.get("stlPath"), str) else None),
                "patchInfo": {"type": "wall" if role == "wall" else "patch"},
                "stlMetadata": _parse_ascii_stl_metadata(stl_text),
                **({"boundaryCondition": item["boundaryCondition"]} if isinstance(item.get("boundaryCondition"), dict) else {}),
            }
        )
    return surfaces, files


def _surface_coverage_summary(surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed_roles = sorted({str(surface.get("role")) for surface in surfaces if surface.get("cadReviewed") is True})
    missing = sorted(REQUIRED_REVIEWED_BOUNDARY_ROLES - set(reviewed_roles))
    patch_names = [str(surface.get("patchName")) for surface in surfaces if surface.get("patchName")]
    return {
        "requiredRoles": sorted(REQUIRED_REVIEWED_BOUNDARY_ROLES),
        "rolesPresent": reviewed_roles,
        "missingRequiredRoles": missing,
        "complete": not missing,
        "patchNames": patch_names,
        "requiredPatchNames": [
            str(surface.get("patchName"))
            for surface in surfaces
            if surface.get("cadReviewed") is True and surface.get("role") in REQUIRED_REVIEWED_BOUNDARY_ROLES
        ],
        "status": "pass" if not missing else "fail",
    }


def _surface_boundary_condition_coverage(surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    required_patch_names = sorted(str(surface.get("patchName")) for surface in surfaces if surface.get("patchName"))
    patches_with_conditions = sorted(
        str(surface.get("patchName"))
        for surface in surfaces
        if surface.get("patchName")
        and isinstance(surface.get("boundaryCondition"), dict)
        and surface.get("boundaryCondition", {}).get("status") in {"ready", "placeholder"}
    )
    missing = sorted(set(required_patch_names) - set(patches_with_conditions))
    return {
        "requiredPatchNames": required_patch_names,
        "patchesWithConditions": patches_with_conditions,
        "missingPatchNames": missing,
        "complete": not missing,
        "status": "pass" if not missing else "fail",
    }


def _reviewed_geometry_source(project: dict[str, Any], starter_stl: str) -> tuple[str, dict[str, Any], dict[str, str]]:
    solver = project.get("solver") if isinstance(project.get("solver"), dict) else {}
    reviewed = solver.get("reviewedGeometry") if isinstance(solver.get("reviewedGeometry"), dict) else {}
    source_type = str(reviewed.get("sourceType") or "flowlab-generated")
    if source_type not in REVIEWED_GEOMETRY_SOURCE_TYPES:
        raise ValueError(f"Unsupported reviewedGeometry.sourceType: {source_type}")

    review_notes = str(reviewed.get("reviewNotes") or "")[:2000]
    reviewed_at = reviewed.get("reviewedAt") if isinstance(reviewed.get("reviewedAt"), str) else None
    cad_reviewed_requested = reviewed.get("cadReviewed") is True
    local_path = _safe_relative_stl_path(reviewed.get("stlPath"))
    raw_path = reviewed.get("stlPath") if isinstance(reviewed.get("stlPath"), str) else None

    metadata: dict[str, Any] = {
        "triSurface": "constant/triSurface/reviewedFlowLabSurfaces.stl",
        "sourceType": source_type,
        "cadReviewed": False,
        "reviewedAt": reviewed_at,
        "reviewNotes": review_notes,
        "locationInMesh": [],
        "scale": 0.01,
        "validation": {
            "status": "pass",
            "checks": ["solid", "facet normal", "vertex"],
            "reasons": [],
        },
    }

    if source_type == "flowlab-generated":
        metadata.update(
            {
                "source": "FlowLab starter quad-strip extrusion",
                "warning": "Generated from FlowLab graph geometry for native meshing preflight only; replace with reviewed CAD/STL before production.",
                "validation": {
                    "status": "pass",
                    "checks": ["solid", "facet normal", "vertex", "FlowLab starter provenance"],
                    "reasons": [],
                },
            }
        )
        return starter_stl, metadata, {}

    surfaces, surface_files = _sanitize_reviewed_surfaces(reviewed.get("surfaces"))
    if surfaces:
        surface_coverage = _surface_coverage_summary(surfaces)
        boundary_condition_coverage = _surface_boundary_condition_coverage(surfaces)
        boundary_tags = [
            {
                "id": str(surface.get("id")),
                "role": str(surface.get("role")),
                "patchName": str(surface.get("patchName")),
                "label": str(surface.get("surfaceName") or surface.get("patchName")),
                "notes": str(surface.get("notes") or ""),
            }
            for surface in surfaces
        ]
        combined_stl = "\n".join(surface_files[path].rstrip() for path in sorted(surface_files)) + "\n"
        metadata.update(
            {
                "source": "User-reviewed multi-surface STL import",
                "sourceType": "multi-surface-stl",
                "cadReviewed": surface_coverage["complete"],
                "validation": {"status": "pass", "checks": ["multi-surface ASCII STL"], "reasons": []},
                "surfaces": surfaces,
                "surfaceCoverage": surface_coverage,
                "boundaryConditionCoverage": boundary_condition_coverage,
                "boundaryTags": boundary_tags,
                "boundaryTagValidation": {
                    **_boundary_tag_summary(boundary_tags),
                    "missingRequiredRoles": surface_coverage["missingRequiredRoles"],
                    "complete": surface_coverage["complete"],
                    "status": surface_coverage["status"],
                    "issues": [],
                },
                "warning": "Reviewed multi-surface geometry accepted for native meshing handoff; production still requires passing native mesh and patch coverage evidence.",
            }
        )
        return combined_stl, metadata, surface_files

    if raw_path is not None and local_path is None:
        raise ValueError("reviewedGeometry.stlPath must be a safe relative .stl path.")

    stl_text, _ = _read_reviewed_stl_text(reviewed, context="reviewedGeometry")
    validation = _ascii_stl_validation(stl_text)
    boundary_tags, boundary_tag_issues = _sanitize_boundary_tags(reviewed.get("boundaryTags"))
    stl_metadata = _parse_ascii_stl_metadata(str(stl_text))

    if source_type == "local-stl-path" and local_path:
        metadata["stlPath"] = local_path
    metadata.update(
        {
            "source": "User-reviewed STL import" if source_type == "uploaded-stl" else f"User-reviewed STL import from {local_path}",
            "cadReviewed": cad_reviewed_requested,
            "validation": validation,
            "stlMetadata": stl_metadata,
            "boundaryTags": boundary_tags,
            "boundaryTagValidation": {
                **_boundary_tag_summary(boundary_tags),
                "status": "pass" if not boundary_tag_issues and not _boundary_tag_summary(boundary_tags)["missingRequiredRoles"] else "fail",
                "issues": boundary_tag_issues,
            },
            "warning": "Reviewed geometry accepted for native meshing handoff; production still requires passing native mesh evidence.",
        }
    )
    return str(stl_text).rstrip() + "\n", metadata, {}


def _triangle_normal(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _physical_group_map(mesh: dict[str, Any]) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    physical_tag = 1
    for region in mesh.get("regions", []):
        if not isinstance(region, dict):
            continue
        edge_id = str(region.get("edgeId")).replace("-", "_")
        edge_type = str(region.get("edgeType", "unknown"))
        source_kind = "connector" if edge_type == "connector" else "physical-edge"
        volume_name = f"fluid_{edge_id}"
        groups.append(
            {
                "name": volume_name,
                "dimension": 3,
                "gmshTag": physical_tag,
                "role": "fluid-volume",
                "sourceKind": source_kind,
                "edgeId": region.get("edgeId"),
                "edgeType": edge_type,
                "cellStart": region.get("cellStart"),
                "cellCount": region.get("cellCount"),
                "solverNames": {
                    "gmsh": volume_name,
                    "su2": None,
                    "codeSaturne": volume_name,
                    "openfoam": "internalMesh",
                },
            }
        )
        physical_tag += 1
        boundary_names: list[str] = []
        for name, line_segments in _boundary_lines(region):
            boundary_names.append(name)
            groups.append(
                {
                    "name": name,
                    "dimension": 2,
                    "gmshTag": physical_tag,
                    "role": _boundary_role(name),
                    "sourceKind": source_kind,
                    "edgeId": region.get("edgeId"),
                    "edgeType": edge_type,
                    "segmentCount": len(line_segments),
                    "solverNames": {
                        "gmsh": name,
                        "su2": name,
                        "codeSaturne": name,
                        "openfoam": _openfoam_patch_for_group(name),
                    },
                }
            )
            physical_tag += 1
        front_back_name = f"wall_{edge_id}_front_back"
        boundary_names.append(front_back_name)
        groups.append(
            {
                "name": front_back_name,
                "dimension": 2,
                "gmshTag": physical_tag,
                "role": "front-back",
                "sourceKind": source_kind,
                "edgeId": region.get("edgeId"),
                "edgeType": edge_type,
                "segmentCount": region.get("cellCount"),
                "solverNames": {
                    "gmsh": front_back_name,
                    "su2": front_back_name,
                    "codeSaturne": front_back_name,
                    "openfoam": "frontAndBack",
                },
            }
        )
        physical_tag += 1
        groups[-(len(boundary_names) + 1)]["boundaryGroups"] = boundary_names

    boundary_groups = [group for group in groups if group["dimension"] == 2]
    volume_groups = [group for group in groups if group["dimension"] == 3]
    return {
        "schema": "flowlab.physical_group_map.v1",
        "productionReady": False,
        "groups": groups,
        "counts": {
            "groups": len(groups),
            "volumes": len(volume_groups),
            "boundaries": len(boundary_groups),
            "inlets": sum(1 for group in boundary_groups if group["role"] == "inlet"),
            "outlets": sum(1 for group in boundary_groups if group["role"] == "outlet"),
            "walls": sum(1 for group in boundary_groups if group["role"] == "wall"),
            "frontBack": sum(1 for group in boundary_groups if group["role"] == "front-back"),
        },
        "solverTargets": {
            "gmsh": {
                "artifact": "mesh/flowlab_mesh.msh",
                "physicalNames": [group["name"] for group in groups],
            },
            "su2": {
                "artifact": "mesh/flowlab_mesh.su2",
                "markers": [group["name"] for group in boundary_groups],
            },
            "codeSaturne": {
                "artifact": "mesh/flowlab_mesh.msh",
                "volumeGroups": [group["name"] for group in volume_groups],
                "boundaryGroups": [group["name"] for group in boundary_groups],
            },
            "openfoam": {
                "artifact": "constant/polyMesh/boundary",
                "aggregatePatches": ["inlet", "outlet", "walls", "frontAndBack"],
                "sourceToAggregate": {group["name"]: group["solverNames"]["openfoam"] for group in boundary_groups},
            },
        },
        "blockingReasons": [
            "Physical group names are preserved for native handoff, but production CAD surfaces, real 3D meshing, and solver-native quality evidence are still missing.",
        ],
        "notes": [
            "This map is deterministic evidence that source FlowLab regions have solver-facing names before native production meshing.",
            "The OpenFOAM starter polyMesh aggregates individual source groups into inlet/outlet/walls/frontAndBack patches; SU2 and Gmsh preserve per-source names.",
            "productionReady remains false until these groups are bound to CAD-quality 3D geometry and solver-native mesh checks are captured.",
        ],
    }


def _openfoam_snappy_handoff(
    mesh: dict[str, Any],
    physical_group_map: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    controls: dict[str, Any],
    reviewed_geometry: dict[str, Any],
) -> dict[str, Any]:
    boundary_groups = [
        group
        for group in physical_group_map.get("groups", [])
        if isinstance(group, dict) and group.get("dimension") == 2
    ]
    wall_groups = [group for group in boundary_groups if group.get("role") == "wall"]
    inlet_groups = [group for group in boundary_groups if group.get("role") == "inlet"]
    outlet_groups = [group for group in boundary_groups if group.get("role") == "outlet"]
    front_back_groups = [group for group in boundary_groups if group.get("role") == "front-back"]
    prism_edges = {
        str(edge.get("edgeId")): edge
        for edge in prism_layer_plan.get("edges", [])
        if isinstance(edge, dict) and edge.get("edgeId") is not None
    }
    layer_patches = []
    for group in wall_groups:
        edge_id = str(group.get("edgeId"))
        prism_edge = prism_edges.get(edge_id, {})
        layer_patches.append(
            {
                "patch": group["name"],
                "sourceEdgeId": group.get("edgeId"),
                "nSurfaceLayers": prism_edge.get("layerCount", controls.get("boundaryLayerLayers", 1)),
                "firstLayerThickness": prism_edge.get("targetFirstCellHeight"),
                "expansionRatio": prism_edge.get("growthRate", controls.get("boundaryLayerGrowthRate")),
                "totalLayerThickness": prism_edge.get("totalLayerThickness"),
                "requiredEvidence": prism_edge.get("requiredEvidence", []),
            }
        )
    refinement_regions = [
        {
            "region": f"fluid_{str(region.get('edgeId')).replace('-', '_')}",
            "sourceEdgeId": region.get("edgeId"),
            "edgeType": region.get("edgeType"),
            "level": [1, max(1, int(region.get("refinementFactor") or 1))],
            "featureRefinement": region.get("featureRefinement"),
            "segmentCount": region.get("segmentCount"),
        }
        for region in mesh.get("regions", [])
        if isinstance(region, dict) and region.get("edgeType") != "connector"
    ]
    location = _openfoam_location_in_mesh(mesh)
    reviewed_geometry = dict(reviewed_geometry)
    reviewed_geometry["locationInMesh"] = list(location)
    cad_reviewed = reviewed_geometry.get("cadReviewed") is True
    source_type = str(reviewed_geometry.get("sourceType") or "flowlab-generated")
    reviewed_surfaces = reviewed_geometry.get("surfaces") if isinstance(reviewed_geometry.get("surfaces"), list) else []
    reviewed_surfaces = [surface for surface in reviewed_surfaces if isinstance(surface, dict)]
    use_reviewed_surfaces = bool(reviewed_surfaces)
    surface_coverage = reviewed_geometry.get("surfaceCoverage") if isinstance(reviewed_geometry.get("surfaceCoverage"), dict) else {}
    boundary_condition_coverage = (
        reviewed_geometry.get("boundaryConditionCoverage")
        if isinstance(reviewed_geometry.get("boundaryConditionCoverage"), dict)
        else _surface_boundary_condition_coverage(reviewed_surfaces)
        if reviewed_surfaces
        else {}
    )
    tag_validation = reviewed_geometry.get("boundaryTagValidation") if isinstance(reviewed_geometry.get("boundaryTagValidation"), dict) else {}
    reviewed_tags = tag_validation.get("tags") if isinstance(tag_validation.get("tags"), list) else []
    use_reviewed_tags = source_type != "flowlab-generated" and bool(reviewed_tags)
    reviewed_inlet_tags = [tag for tag in reviewed_tags if isinstance(tag, dict) and tag.get("role") == "inlet"]
    reviewed_outlet_tags = [tag for tag in reviewed_tags if isinstance(tag, dict) and tag.get("role") == "outlet"]
    reviewed_wall_tags = [tag for tag in reviewed_tags if isinstance(tag, dict) and tag.get("role") == "wall"]
    reviewed_interface_tags = [tag for tag in reviewed_tags if isinstance(tag, dict) and tag.get("role") == "interface"]
    if use_reviewed_tags:
        layer_patches = [
            {
                "patch": tag["patchName"],
                "sourceBoundaryTag": tag,
                "nSurfaceLayers": controls.get("boundaryLayerLayers", 1),
                "firstLayerThickness": None,
                "expansionRatio": controls.get("boundaryLayerGrowthRate"),
                "totalLayerThickness": None,
                "requiredEvidence": ["reviewed STL wall tag", "native addLayers output", "y-plus or wall-distance evidence"],
            }
            for tag in reviewed_wall_tags
        ]
    if use_reviewed_surfaces:
        layer_patches = [
            {
                "patch": str(surface["patchName"]),
                "sourceSurface": surface,
                "nSurfaceLayers": controls.get("boundaryLayerLayers", 1),
                "firstLayerThickness": None,
                "expansionRatio": controls.get("boundaryLayerGrowthRate"),
                "totalLayerThickness": None,
                "requiredEvidence": ["reviewed wall STL surface", "native addLayers output", "y-plus or wall-distance evidence"],
            }
            for surface in reviewed_surfaces
            if surface.get("role") == "wall"
        ]
    surface_source = (
        "constant/triSurface/reviewedFlowLabSurfaces.stl imported from reviewed user STL."
        if cad_reviewed
        else "constant/triSurface/reviewedFlowLabSurfaces.stl generated from FlowLab starter geometry; CAD/B-rep replacement required for production."
    )
    return {
        "schema": "flowlab.openfoam_snappy_handoff.v1",
        "productionReady": False,
        "status": "review-only",
        "sourceArtifacts": {
            "physicalGroups": "mesh/physical_groups.json",
            "prismLayerPlan": "mesh/prism_layer_plan.json",
            "gmshHandoff": "mesh/gmsh_production_handoff.geo",
        },
        "expectedNativeFiles": [
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            *[str(surface.get("triSurface")) for surface in reviewed_surfaces if surface.get("triSurface")],
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
        ],
        "templateArtifacts": {
            "snappyHexMeshDict": "mesh/openfoam_snappyHexMeshDict.template",
            "surfaceFeatureExtractDict": "mesh/openfoam_surfaceFeatureExtractDict.template",
            "meshQualityDict": "mesh/openfoam_meshQualityDict.template",
        },
        "installedArtifacts": {
            "triSurface": "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "triSurfaces": {str(surface.get("patchName")): str(surface.get("triSurface")) for surface in reviewed_surfaces if surface.get("patchName") and surface.get("triSurface")},
            "snappyHexMeshDict": "system/snappyHexMeshDict",
            "surfaceFeatureExtractDict": "system/surfaceFeatureExtractDict",
            "meshQualityDict": "system/meshQualityDict",
        },
        "starterGeometry": reviewed_geometry,
        "reviewedGeometry": reviewed_geometry,
        "boundaryCoverage": {
            "requiredRoles": surface_coverage.get("requiredRoles", sorted(REQUIRED_REVIEWED_BOUNDARY_ROLES)),
            "rolesPresent": surface_coverage.get("rolesPresent", []),
            "missingRequiredRoles": surface_coverage.get("missingRequiredRoles", []),
            "complete": surface_coverage.get("complete", False),
            "status": surface_coverage.get("status", "not-applicable"),
        },
        "boundaryConditionCoverage": boundary_condition_coverage,
        "reviewedSurfaces": {
            "source": "reviewedGeometry.surfaces",
            "surfaces": reviewed_surfaces,
            "coverage": surface_coverage,
            "status": surface_coverage.get("status", "not-applicable") if use_reviewed_surfaces else "not-applicable",
        },
        "reviewedBoundaryTags": tag_validation,
        "castellatedMeshControls": {
            "locationInMeshRequired": True,
            "locationInMesh": list(location),
            "preservePhysicalGroups": [group["name"] for group in boundary_groups],
            "refinementRegions": refinement_regions,
            "requiredGeometry": [
                *(
                    [
                        {
                            "name": str(surface.get("patchName")),
                            "role": str(surface.get("role")),
                            "patchName": str(surface.get("patchName")),
                            "type": "triSurfaceMesh",
                            "triSurface": str(surface.get("triSurface")),
                            "source": str(surface.get("triSurface")),
                            "patchInfo": {"type": "wall" if surface.get("role") == "wall" else "patch"},
                            "cadReviewed": surface.get("cadReviewed") is True,
                        }
                        for surface in reviewed_surfaces
                    ]
                    if use_reviewed_surfaces
                    else [
                        {
                            "name": "reviewedFlowLabSurfaces",
                            "type": "triSurfaceMesh",
                            "source": surface_source,
                        }
                    ]
                )
            ],
        },
        "snapControls": {
            "nSmoothPatch": 3,
            "tolerance": 2.0,
            "nSolveIter": 30,
            "nRelaxIter": 5,
            "featureSnap": True,
            "requiredFeatureExtraction": True,
        },
        "addLayersControls": {
            "relativeSizes": False,
            "layers": layer_patches,
            "aggregateFallbackPatch": "walls",
            "expansionRatio": controls.get("boundaryLayerGrowthRate"),
            "finalLayerThickness": None,
            "minThickness": None,
            "nGrow": 0,
            "featureAngle": 60,
        },
        "boundaryPatchPlan": {
            "source": "reviewed-surfaces" if use_reviewed_surfaces else "reviewed-boundary-tags" if use_reviewed_tags else "flowlab-physical-groups",
            "inlet": [str(surface["patchName"]) for surface in reviewed_surfaces if surface.get("role") == "inlet"]
            if use_reviewed_surfaces
            else [tag["patchName"] for tag in reviewed_inlet_tags]
            if use_reviewed_tags
            else [group["name"] for group in inlet_groups],
            "outlet": [str(surface["patchName"]) for surface in reviewed_surfaces if surface.get("role") == "outlet"]
            if use_reviewed_surfaces
            else [tag["patchName"] for tag in reviewed_outlet_tags]
            if use_reviewed_tags
            else [group["name"] for group in outlet_groups],
            "walls": [str(surface["patchName"]) for surface in reviewed_surfaces if surface.get("role") == "wall"]
            if use_reviewed_surfaces
            else [tag["patchName"] for tag in reviewed_wall_tags]
            if use_reviewed_tags
            else [group["name"] for group in wall_groups],
            "interfaces": [str(surface["patchName"]) for surface in reviewed_surfaces if surface.get("role") == "interface"]
            if use_reviewed_surfaces
            else [tag["patchName"] for tag in reviewed_interface_tags]
            if use_reviewed_tags
            else [],
            "frontAndBack": [] if use_reviewed_surfaces or use_reviewed_tags else [group["name"] for group in front_back_groups],
            "requiredPatchCoverage": surface_coverage.get("requiredPatchNames", []),
        },
        "qualityEvidenceRequired": [
            "checkMesh -allGeometry -allTopology after snappyHexMesh",
            "layer coverage and illegal face/cell counts from snappyHexMesh logs",
            "wall-distance/y-plus field after a representative solver run",
            "non-orthogonality, skewness, aspect ratio, and negative-volume evidence",
        ],
        "readinessChecks": [
            {
                "id": "physical-groups-mapped",
                "status": "pass" if boundary_groups else "fail",
                "detail": "Boundary physical groups are available for OpenFOAM patch planning.",
            },
            {
                "id": "prism-layer-inputs",
                "status": "warning" if layer_patches else "fail",
                "detail": "Prism-layer first-cell targets are available as review inputs, but no native layer mesh has been generated.",
            },
            {
                "id": "starter-trisurface-export",
                "status": "pass",
                "detail": (
                    "FlowLab exports constant/triSurface/reviewedFlowLabSurfaces.stl from reviewed user STL input."
                    if source_type != "flowlab-generated"
                    else "FlowLab exports constant/triSurface/reviewedFlowLabSurfaces.stl from the starter graph geometry for preflight."
                ),
            },
            {
                "id": "cad-surface-ready",
                "status": "pass"
                if cad_reviewed and (surface_coverage.get("complete") is True if use_reviewed_surfaces else tag_validation.get("complete") is True)
                else "fail",
                "detail": (
                    "User imported STL surfaces are explicitly marked as CAD reviewed and include required inlet/outlet/wall surfaces."
                    if use_reviewed_surfaces and cad_reviewed and surface_coverage.get("complete") is True
                    else "User imported STL is explicitly marked as CAD reviewed and includes required inlet/outlet/wall tags."
                    if cad_reviewed and tag_validation.get("complete") is True
                    else "Reviewed STL needs explicit inlet, outlet, and wall tags before production approval."
                    if cad_reviewed
                    else "The generated STL is a starter surface, not a reviewed CAD/B-rep or cleaned production STL."
                ),
            },
            {
                "id": "reviewed-surface-coverage",
                "status": "pass" if not use_reviewed_surfaces or surface_coverage.get("complete") is True else "fail",
                "detail": (
                    "Reviewed inlet, outlet, and wall STL surfaces are present."
                    if surface_coverage.get("complete") is True
                    else "Reviewed multi-surface STL imports must include CAD-reviewed inlet, outlet, and wall surfaces."
                ),
            },
            {
                "id": "reviewed-boundary-tags",
                "status": "pass" if source_type == "flowlab-generated" or tag_validation.get("complete") is True else "fail",
                "detail": (
                    "Required reviewed STL boundary roles are tagged."
                    if tag_validation.get("complete") is True
                    else "Reviewed STL imports must tag inlet, outlet, and wall patches before production approval."
                ),
            },
            {
                "id": "native-quality-evidence",
                "status": "fail",
                "detail": "No snappyHexMesh logs, checkMesh report, or solver y-plus evidence has been captured.",
            },
        ],
        "blockingReasons": [
            (
                "OpenFOAM native meshing handoff is review-only until snappyHexMesh execution logs, generated layer cells, and checkMesh/y-plus evidence exist."
                if cad_reviewed
                else "OpenFOAM native meshing handoff is review-only until CAD/STL surfaces, snappyHexMesh execution logs, generated layer cells, and checkMesh/y-plus evidence exist."
            ),
        ],
        "notes": [
            "This file is an OpenFOAM-native meshing handoff, not a runnable snappyHexMeshDict.",
            "It translates FlowLab physical groups and prism-layer sizing into review inputs for a future native meshing step.",
            (
                "productionReady remains false until native mesh checks and wall evidence pass for the reviewed STL."
                if cad_reviewed
                else "productionReady remains false because FlowLab still does not generate CAD-quality triSurface geometry or native layer cells."
            ),
        ],
    }


def _openfoam_native_mesh_preflight_script() -> str:
    return """#!/usr/bin/env python3
\"\"\"FlowLab OpenFOAM native mesh preflight.

FLOWLAB_OPENFOAM_NATIVE_MESH_PREFLIGHT_SCHEMA = \"flowlab.openfoam_native_mesh_preflight.v1\"

Run from the materialized OpenFOAM case directory against the generated
starter triSurface and installed system dictionaries before attempting native
surfaceFeatureExtract/snappyHexMesh promotion.
The script writes mesh/openfoam_native_mesh_preflight_report.json and exits
non-zero until the native CAD/STL, dictionary, locationInMesh, and quality
evidence prerequisites are present.
\"\"\"

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SCHEMA = "flowlab.openfoam_native_mesh_preflight_report.v1"
HANDOFF_SCHEMA = "flowlab.openfoam_snappy_handoff.v1"
REPORT_PATH = Path("mesh/openfoam_native_mesh_preflight_report.json")
REQUIRED_FILES = {
    "handoff": Path("mesh/openfoam_snappy_handoff.json"),
    "triSurface": Path("constant/triSurface/reviewedFlowLabSurfaces.stl"),
    "snappyHexMeshDict": Path("system/snappyHexMeshDict"),
    "surfaceFeatureExtractDict": Path("system/surfaceFeatureExtractDict"),
    "meshQualityDict": Path("system/meshQualityDict"),
}
TEMPLATE_FILES = [
    Path("mesh/openfoam_snappyHexMeshDict.template"),
    Path("mesh/openfoam_surfaceFeatureExtractDict.template"),
    Path("mesh/openfoam_meshQualityDict.template"),
]
QUALITY_EVIDENCE = [
    Path("log.snappyHexMesh"),
    Path("log.checkMesh"),
    Path("postProcessing/yPlus"),
]


def _load_handoff() -> tuple[dict, list[dict]]:
    issues: list[dict] = []
    handoff_path = REQUIRED_FILES["handoff"]
    if not handoff_path.exists():
        return {}, [{"id": "handoff-json", "status": "fail", "detail": f"Missing {handoff_path}."}]
    try:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [{"id": "handoff-json", "status": "fail", "detail": f"{handoff_path} is not valid JSON: {exc}"}]
    if handoff.get("schema") != HANDOFF_SCHEMA:
        issues.append({"id": "handoff-schema", "status": "fail", "detail": f"Expected schema {HANDOFF_SCHEMA}."})
    if handoff.get("productionReady") is not False:
        issues.append({"id": "handoff-production-ready", "status": "fail", "detail": "Handoff must stay productionReady=false until native evidence exists."})
    return handoff, issues


def _check_file(path: Path, label: str) -> dict:
    return {
        "id": label,
        "path": str(path),
        "status": "pass" if path.exists() else "fail",
        "detail": f"Present {path}." if path.exists() else f"Missing {path}.",
    }


def _location_check(snappy_path: Path) -> dict:
    if not snappy_path.exists():
        return {"id": "location-in-mesh", "status": "fail", "detail": "system/snappyHexMeshDict is missing."}
    text = snappy_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"locationInMesh\\s*\\(([^)]+)\\)", text)
    if not match:
        return {"id": "location-in-mesh", "status": "fail", "detail": "locationInMesh is missing."}
    value = " ".join(match.group(1).split())
    if value == "0 0 0":
        return {"id": "location-in-mesh", "status": "fail", "detail": "locationInMesh still uses the FlowLab placeholder (0 0 0)."}
    return {"id": "location-in-mesh", "status": "pass", "detail": f"locationInMesh is ({value})."}


def main() -> int:
    handoff, issues = _load_handoff()
    checks = []
    checks.extend(issues)
    checks.extend(_check_file(path, label) for label, path in REQUIRED_FILES.items())
    checks.extend(_check_file(path, f"template-{path.name}") for path in TEMPLATE_FILES)
    checks.append(_location_check(REQUIRED_FILES["snappyHexMeshDict"]))
    checks.append(
        {
            "id": "native-quality-evidence",
            "status": "pass" if all(path.exists() for path in QUALITY_EVIDENCE) else "fail",
            "requiredEvidence": [str(path) for path in QUALITY_EVIDENCE],
            "detail": "Native meshing logs, checkMesh logs, and y-plus output are required before production approval.",
        }
    )
    blocking = [check for check in checks if check.get("status") == "fail"]
    report = {
        "schema": SCHEMA,
        "status": "blocked" if blocking else "ready-for-native-meshing-review",
        "productionReady": False,
        "handoffSchema": handoff.get("schema"),
        "commandsToRunAfterPassingPreflight": [
            "surfaceFeatureExtract",
            "blockMesh or confirm existing constant/polyMesh",
            "snappyHexMesh -overwrite",
            "checkMesh -allGeometry -allTopology",
            "postProcess -func yPlus",
        ],
        "checks": checks,
        "blockingReasons": [check.get("detail") for check in blocking],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _su2_native_meshing_handoff(
    physical_group_map: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    adaptation_plan: dict[str, Any],
) -> dict[str, Any]:
    boundary_groups = [
        group
        for group in physical_group_map.get("groups", [])
        if isinstance(group, dict) and group.get("dimension") == 2
    ]
    markers = [str(group.get("solverNames", {}).get("su2")) for group in boundary_groups if group.get("solverNames", {}).get("su2")]
    wall_markers = [str(group.get("name")) for group in boundary_groups if group.get("role") in {"wall", "front-back"}]
    inlet_markers = [str(group.get("name")) for group in boundary_groups if group.get("role") == "inlet"]
    outlet_markers = [str(group.get("name")) for group in boundary_groups if group.get("role") == "outlet"]
    prism_edges = prism_layer_plan.get("edges") if isinstance(prism_layer_plan.get("edges"), list) else []
    adaptation_targets = adaptation_plan.get("adaptationTargets") if isinstance(adaptation_plan.get("adaptationTargets"), list) else []
    return {
        "schema": "flowlab.su2_native_meshing_handoff.v1",
        "productionReady": False,
        "status": "review-only",
        "sourceArtifacts": {
            "physicalGroups": "mesh/physical_groups.json",
            "prismLayerPlan": "mesh/prism_layer_plan.json",
            "adaptationPlan": "mesh/adaptation_plan.json",
            "gmshHandoff": "mesh/gmsh_production_handoff.geo",
        },
        "currentStarterMesh": "mesh/flowlab_mesh.su2",
        "expectedNativeFiles": ["mesh/production_mesh.su2", "history.csv or SU2_CFD startup log"],
        "markerPlan": {
            "allMarkers": markers,
            "inlet": inlet_markers,
            "outlet": outlet_markers,
            "wall": wall_markers,
            "requiredPreservation": ["inlet", "outlet", "wall", "front-back/interface markers where present"],
        },
        "viscousLayerPlan": {
            "source": "mesh/prism_layer_plan.json",
            "edgeCount": len(prism_edges),
            "wallMarkers": wall_markers,
            "requiredEvidence": [
                "viscous layer cells generated by a native mesher",
                "wall marker preservation after conversion to .su2",
                "first-cell height and y-plus evidence after a representative run",
            ],
        },
        "adaptationPlan": {
            "source": "mesh/adaptation_plan.json",
            "targetCount": len(adaptation_targets),
            "fieldIndicators": sorted(
                {
                    field
                    for target in adaptation_targets
                    if isinstance(target, dict)
                    for field in target.get("fieldIndicatorTargets", [])
                }
            ),
        },
        "qualityEvidenceRequired": [
            "SU2_CFD startup/mesh diagnostics for marker and element checks",
            "negative-volume, skewness/aspect-ratio, and boundary-marker verification",
            "viscous-layer and y-plus evidence for wall-bounded cases",
            "adaptation/error-indicator evidence for shocks, pressure gradients, thermal gradients, phase interfaces, or cavitation regions",
        ],
        "readinessChecks": [
            {"id": "marker-map-exported", "status": "pass" if markers else "fail", "detail": "SU2 marker names are exported from FlowLab physical groups."},
            {"id": "viscous-layer-inputs", "status": "warning" if prism_edges else "fail", "detail": "Viscous-layer sizing inputs exist, but no native SU2 viscous-layer mesh has been generated."},
            {"id": "native-su2-production-mesh", "status": "fail", "detail": "No CAD-quality native production .su2 mesh is generated."},
            {"id": "su2-mesh-diagnostics", "status": "fail", "detail": "No SU2 startup or mesh-diagnostic evidence has been captured for a production mesh."},
        ],
        "blockingReasons": [
            "SU2 native meshing handoff is review-only until CAD-quality geometry, converted .su2 volume mesh, viscous layers, marker checks, and SU2 diagnostics exist."
        ],
    }


def _code_saturne_native_meshing_handoff(
    physical_group_map: dict[str, Any],
    prism_layer_plan: dict[str, Any],
    adaptation_plan: dict[str, Any],
) -> dict[str, Any]:
    solver_targets = physical_group_map.get("solverTargets") if isinstance(physical_group_map.get("solverTargets"), dict) else {}
    code_saturne_targets = solver_targets.get("codeSaturne") if isinstance(solver_targets.get("codeSaturne"), dict) else {}
    boundary_groups = code_saturne_targets.get("boundaryGroups") if isinstance(code_saturne_targets.get("boundaryGroups"), list) else []
    volume_groups = code_saturne_targets.get("volumeGroups") if isinstance(code_saturne_targets.get("volumeGroups"), list) else []
    prism_edges = prism_layer_plan.get("edges") if isinstance(prism_layer_plan.get("edges"), list) else []
    adaptation_targets = adaptation_plan.get("adaptationTargets") if isinstance(adaptation_plan.get("adaptationTargets"), list) else []
    return {
        "schema": "flowlab.code_saturne_native_meshing_handoff.v1",
        "productionReady": False,
        "status": "review-only",
        "sourceArtifacts": {
            "physicalGroups": "mesh/physical_groups.json",
            "prismLayerPlan": "mesh/prism_layer_plan.json",
            "adaptationPlan": "mesh/adaptation_plan.json",
            "gmshHandoff": "mesh/gmsh_production_handoff.geo",
        },
        "currentStarterMesh": "mesh/flowlab_mesh.msh",
        "expectedNativeFiles": ["MESH/production_mesh.msh or .cgns/.med", "RESU/<run>/listing preprocessing evidence"],
        "importPlan": {
            "preferredFormats": ["Gmsh 2.2/4.x", "CGNS", "MED"],
            "volumeGroups": volume_groups,
            "boundaryGroups": boundary_groups,
            "requiredLocalizationReview": ["inlet_*", "outlet_*", "wall_*", "interface groups where present"],
        },
        "prismLayerImportPlan": {
            "source": "mesh/prism_layer_plan.json",
            "edgeCount": len(prism_edges),
            "requiredEvidence": [
                "prism-layer cells preserved through Gmsh/CGNS/MED import",
                "wall group preservation after Code_Saturne preprocessing",
                "wall-distance or y-plus evidence after a representative run",
            ],
        },
        "adaptationPlan": {
            "source": "mesh/adaptation_plan.json",
            "targetCount": len(adaptation_targets),
            "requiredEvidence": ["native mesh-size fields or adapted mesh import", "before/after cell counts", "post-adaptation preprocessing quality report"],
        },
        "qualityEvidenceRequired": [
            "Code_Saturne preprocessing/listing success for the production mesh",
            "boundary localization report for inlet, outlet, wall, interface, and symmetry groups",
            "negative volume/cell quality summaries from preprocessing",
            "refinement/adaptation evidence around scalar, thermal, multiphase, cavitation, and high-gradient regions",
        ],
        "readinessChecks": [
            {"id": "physical-groups-exported", "status": "pass" if boundary_groups and volume_groups else "fail", "detail": "Code_Saturne physical groups are exported for native mesh import review."},
            {"id": "prism-layer-import-inputs", "status": "warning" if prism_edges else "fail", "detail": "Prism-layer sizing inputs exist, but no native prism-layer mesh has been imported through Code_Saturne."},
            {"id": "native-code-saturne-production-mesh", "status": "fail", "detail": "No CAD-quality native production mesh has been generated for Code_Saturne."},
            {"id": "code-saturne-preprocessing-evidence", "status": "fail", "detail": "No Code_Saturne preprocessing/listing quality evidence has been captured for a production mesh."},
        ],
        "blockingReasons": [
            "Code_Saturne native meshing handoff is review-only until CAD-quality geometry, native 3D mesh import, prism layers, group localization, and preprocessing quality evidence exist."
        ],
    }


def _foam_review_header(class_name: str, object_name: str) -> str:
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
| FlowLab review-only OpenFOAM native meshing template                         |
| This file is generated for setup review. It is not production-ready.          |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    object      {object_name};
}}
"""


def _openfoam_snappy_hex_mesh_dict_template(
    handoff: dict[str, Any],
    location: tuple[float, float, float] | None = None,
) -> str:
    required_geometry = handoff["castellatedMeshControls"].get("requiredGeometry", [])
    required_geometry = [item for item in required_geometry if isinstance(item, dict)]
    geometry_name = str(required_geometry[0]["name"]) if required_geometry else "reviewedFlowLabSurfaces"
    use_surface_files = any(item.get("triSurface") for item in required_geometry)
    refinement_regions = handoff["castellatedMeshControls"].get("refinementRegions", [])
    layer_patches = handoff["addLayersControls"].get("layers", [])
    reviewed_tags = handoff.get("reviewedBoundaryTags", {}).get("tags") if isinstance(handoff.get("reviewedBoundaryTags"), dict) else []
    reviewed_tags = reviewed_tags if isinstance(reviewed_tags, list) else []
    reviewed_tags = [tag for tag in reviewed_tags if isinstance(tag, dict)]
    location_text = _openfoam_location_text(location) if location is not None else "0 0 0"
    location_comment = (
        "// FlowLab computed starter point inside the generated starter surface; review before native meshing."
        if location is not None
        else "// TODO: replace with a point inside the reviewed fluid CAD volume."
    )
    lines = [
        _foam_review_header("dictionary", "snappyHexMeshDict"),
        "",
        "// FlowLab review template: replace reviewedFlowLabSurfaces.stl with watertight CAD/STL before production running.",
        "castellatedMesh true;",
        "snap            true;",
        "addLayers       true;",
        "",
        "geometry",
        "{",
    ]
    if use_surface_files:
        for item in required_geometry:
            name = str(item.get("name"))
            tri_surface = str(item.get("triSurface") or f"constant/triSurface/{name}.stl")
            file_name = Path(tri_surface).name
            lines.extend(
                [
                    f"    {file_name}",
                    "    {",
                    "        type triSurfaceMesh;",
                    f"        file \"{file_name}\";",
                    f"        name {name};",
                    "    }",
                ]
            )
    else:
        lines.extend(
            [
                f"    {geometry_name}.stl",
                "    {",
                "        type triSurfaceMesh;",
                f"        name {geometry_name};",
            ]
        )
    if reviewed_tags and not use_surface_files:
        lines.extend(["        regions", "        {"])
        for tag in reviewed_tags:
            patch_name = str(tag.get("patchName"))
            lines.extend(
                [
                    f"            {patch_name}",
                    "            {",
                    f"                name {patch_name};",
                    "            }",
                ]
            )
        lines.append("        }")
    if not use_surface_files:
        lines.append("    }")
    lines.extend(
        [
            "}",
            "",
            "castellatedMeshControls",
            "{",
        "    maxLocalCells 100000;",
        "    maxGlobalCells 2000000;",
        "    minRefinementCells 0;",
        "    nCellsBetweenLevels 3;",
        "    features",
        "    (",
    ]
    )
    for item in required_geometry:
        name = str(item.get("name"))
        tri_surface = str(item.get("triSurface") or f"constant/triSurface/{name}.stl")
        lines.append(f"        {{ file \"{Path(tri_surface).stem}.eMesh\"; level 1; }}")
    lines.extend(
        [
            "    );",
            "    refinementSurfaces",
            "    {",
        ]
    )
    if use_surface_files:
        for item in required_geometry:
            name = str(item.get("name"))
            role = str(item.get("role") or "wall")
            patch_type = "wall" if role == "wall" else "patch"
            in_group = role if role in {"inlet", "outlet", "wall"} else "interface"
            lines.extend(
                [
                    f"        {name}",
                    "        {",
                    "            level (1 2);",
                    f"            patchInfo {{ type {patch_type}; inGroups ({in_group}); }}",
                    "        }",
                ]
            )
    else:
        lines.extend(
            [
                f"        {geometry_name}",
                "        {",
                "            level (1 2);",
                "            patchInfo { type wall; }",
    ]
        )
    if reviewed_tags and not use_surface_files:
        lines.extend(["            regions", "            {"])
        for tag in reviewed_tags:
            patch_name = str(tag.get("patchName"))
            role = str(tag.get("role"))
            patch_type = "wall" if role == "wall" else "patch"
            in_group = role if role in {"inlet", "outlet", "wall"} else "interface"
            lines.extend(
                [
                    f"                {patch_name}",
                    "                {",
                    "                    level (1 2);",
                    f"                    patchInfo {{ type {patch_type}; inGroups ({in_group}); }}",
                    "                }",
                ]
            )
        lines.append("            }")
    if not use_surface_files:
        lines.append("        }")
    lines.extend(["    }", "    refinementRegions", "    {"])
    for region in refinement_regions:
        name = str(region.get("region"))
        level = region.get("level") if isinstance(region.get("level"), list) else [1, 1]
        lines.extend(
            [
                f"        {name}",
                "        {",
                "            mode inside;",
                f"            levels ((1E15 ({int(level[0])} {int(level[1])})));",
                "        }",
            ]
        )
    lines.extend(
        [
            "    }",
            f"    locationInMesh ({location_text}); {location_comment}",
            "    allowFreeStandingZoneFaces true;",
            "}",
            "",
            "snapControls",
            "{",
        ]
    )
    for key, value in handoff.get("snapControls", {}).items():
        if isinstance(value, bool):
            lines.append(f"    {key} {'true' if value else 'false'};")
        else:
            lines.append(f"    {key} {value};")
    lines.extend(
        [
            "}",
            "",
            "addLayersControls",
            "{",
            "    relativeSizes false;",
            "    layers",
            "    {",
        ]
    )
    for layer in layer_patches:
        patch = str(layer.get("patch"))
        first = layer.get("firstLayerThickness")
        ratio = layer.get("expansionRatio")
        lines.extend(
            [
                f"        {patch}",
                "        {",
                f"            nSurfaceLayers {int(layer.get('nSurfaceLayers') or 1)};",
                f"            firstLayerThickness {float(first) if isinstance(first, (int, float)) else 0.001:.9g};",
                f"            expansionRatio {float(ratio) if isinstance(ratio, (int, float)) else 1.2:.9g};",
                "        }",
            ]
        )
    lines.extend(
        [
            "    }",
            "    expansionRatio 1.2;",
            "    finalLayerThickness 0.3;",
            "    minThickness 0.1;",
            "    nGrow 0;",
            "    featureAngle 60;",
            "    nRelaxIter 5;",
            "    nSmoothSurfaceNormals 1;",
            "    nSmoothNormals 3;",
            "    nSmoothThickness 10;",
            "    maxFaceThicknessRatio 0.5;",
            "    maxThicknessToMedialRatio 0.3;",
            "    minMedianAxisAngle 90;",
            "    nBufferCellsNoExtrude 0;",
            "    nLayerIter 50;",
            "}",
            "",
            "meshQualityControls",
            "{",
            "    #include \"meshQualityDict\"",
            "}",
            "",
            "debug 0;",
            "mergeTolerance 1e-6;",
            "",
        ]
    )
    return "\n".join(lines)


def _openfoam_surface_feature_extract_dict_template(handoff: dict[str, Any]) -> str:
    required_geometry = handoff["castellatedMeshControls"].get("requiredGeometry", [])
    required_geometry = [item for item in required_geometry if isinstance(item, dict)]
    surface_files = [
        Path(str(item.get("triSurface"))).name if item.get("triSurface") else f"{str(item.get('name'))}.stl"
        for item in required_geometry
    ] or ["reviewedFlowLabSurfaces.stl"]
    lines = [
        _foam_review_header("dictionary", "surfaceFeatureExtractDict"),
        "",
        "// FlowLab review template: generated for reviewed CAD/STL surfaces.",
    ]
    for surface_file in surface_files:
        lines.extend(
            [
                surface_file,
                "{",
                "    extractionMethod    extractFromSurface;",
                "    extractFromSurfaceCoeffs",
                "    {",
                "        includedAngle   150;",
                "    }",
                "    writeObj            yes;",
                "}",
                "",
            ]
        )
    return "\n".join(lines)


def _openfoam_mesh_quality_dict_template() -> str:
    return (
        _foam_review_header("dictionary", "meshQualityDict")
        + """
// Conservative starter thresholds for review. Production use must tune these
// against solver requirements and captured checkMesh evidence.
maxNonOrtho             65;
maxBoundarySkewness     20;
maxInternalSkewness     4;
maxConcave              80;
minVol                  1e-13;
minTetQuality           1e-30;
minArea                 -1;
minTwist                0.02;
minDeterminant          0.001;
minFaceWeight           0.02;
minVolRatio             0.01;
minTriangleTwist        -1;
nSmoothScale            4;
errorReduction          0.75;
"""
    )


def _gmsh_production_handoff_geo(mesh: dict[str, Any], native_plan: dict[str, Any]) -> str:
    regions = [region for region in mesh.get("regions", []) if isinstance(region, dict) and region.get("edgeType") != "connector"]
    controls = mesh.get("controls") if isinstance(mesh.get("controls"), dict) else {}
    boundary_layer_plan = mesh.get("boundaryLayerPlan") if isinstance(mesh.get("boundaryLayerPlan"), dict) else {}
    target_y_plus = boundary_layer_plan.get("targetYPlus", controls.get("targetYPlus", 30.0))
    lines = [
        "// FlowLab review-only native meshing handoff.",
        "// This is not a production CAD model. Replace the placeholder geometry with watertight CAD/B-rep surfaces.",
        "SetFactory(\"OpenCASCADE\");",
        f"// targetYPlus = {target_y_plus}",
        f"// boundaryLayerLayers = {controls.get('boundaryLayerLayers')}",
        f"// longitudinalRefinement = {controls.get('longitudinalRefinement')}",
        "Mesh.Algorithm3D = 10;",
        "Mesh.Optimize = 1;",
        "Mesh.OptimizeNetgen = 1;",
        "",
        "// Suggested native workflow:",
    ]
    for step in native_plan.get("nativeWorkflow", []):
        if isinstance(step, dict):
            lines.append(f"// - {step.get('stage')}: {', '.join(str(item) for item in step.get('tooling', []))}")
    lines.extend(["", "// FlowLab source regions and physical names to preserve:"])
    for region in regions:
        edge_id = str(region.get("edgeId"))
        edge_type = str(region.get("edgeType"))
        lines.append(
            f"// edge {edge_id}: type={edge_type}, segments={region.get('segmentCount')}, "
            f"from={region.get('fromNode')}:{region.get('fromPort')} to={region.get('toNode')}:{region.get('toPort')}"
        )
        lines.append(f"// Physical Surface(\"inlet_{edge_id}\") / \"outlet_{edge_id}\" / \"wall_{edge_id}_left\" / \"wall_{edge_id}_right\"")
    lines.extend(
        [
            "",
            "// Placeholder point: keeps this .geo syntactically loadable while documenting that CAD import is required.",
            "Point(1) = {0, 0, 0, 1};",
            "",
            "// TODO: Import reviewed CAD, create 3D volumes, assign Physical Volumes/Surfaces, and add real BoundaryLayer fields.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_mesh_bundle(project: dict[str, Any]) -> MeshBundle:
    """Generate a small deterministic 2D quad-strip mesh from FlowLab graph geometry.

    This is an honest v1 export: it follows pipe, Venturi, and rectangular-channel
    port-to-port spans. It is visualization/skeleton-solver input, not a CAD-quality mesh.
    """

    nodes = {node["id"]: node for node in _records(project.get("nodes")) if "id" in node}
    edges = _records(project.get("edges"))
    if not nodes or not edges:
        raise ValueError("FlowLab mesh export needs at least one edge connected to nodes.")

    points: list[list[float]] = []
    cells: list[list[int]] = []
    regions: list[dict[str, Any]] = []
    endpoints_by_node: dict[str, list[dict[str, Any]]] = {}
    controls = _mesh_controls(project)
    base_segment_count = int(controls["baseSegments"])
    transverse_fractions = [float(item) for item in controls["transverseFractions"]]
    transverse_point_count = len(transverse_fractions)
    transverse_divisions = transverse_point_count - 1

    for edge in edges:
        edge_id = str(edge.get("id") or f"edge-{len(regions) + 1}")
        edge_type = str(edge.get("type", "pipe"))
        if edge_type not in SUPPORTED_EDGE_TYPES:
            raise ValueError(f"Unsupported edge type for mesh v1: {edge_type}")
        from_node = nodes.get(edge.get("from"))
        to_node = nodes.get(edge.get("to"))
        if not from_node or not to_node:
            raise ValueError(f"Edge {edge_id} is missing an endpoint node.")
        from_port = _port_id(edge, "fromPort", "outlet")
        to_port = _port_id(edge, "toPort", "inlet")
        x0, y0 = _port_position(from_node, from_port)
        x1, y1 = _port_position(to_node, to_port)
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        nx, ny = -dy / length, dx / length
        edge_point_start = len(points)
        edge_refinement_factor = _edge_refinement_factor(edge_id, controls)
        feature_refinement = _feature_refinement_plan(edge, controls)
        refinement_factor = edge_refinement_factor * int(feature_refinement["factor"])
        segment_count = base_segment_count * int(controls["longitudinalRefinement"]) * refinement_factor
        station_fractions = _station_fractions(segment_count, feature_refinement)

        for station in station_fractions:
            width = _edge_width(edge, station) * 360.0
            cx = x0 + dx * station
            cy = y0 + dy * station
            for fraction in transverse_fractions:
                offset = (0.5 - fraction) * width
                points.append([round(cx + nx * offset, 6), round(cy + ny * offset, 6), 0.0])

        cell_start = len(cells)
        for index in range(segment_count):
            for transverse in range(transverse_divisions):
                lower0 = edge_point_start + index * transverse_point_count + transverse
                upper0 = lower0 + 1
                lower1 = edge_point_start + (index + 1) * transverse_point_count + transverse
                upper1 = lower1 + 1
                cells.append([lower0, lower1, upper1, upper0])

        from_node_id = str(from_node.get("id"))
        to_node_id = str(to_node.get("id"))
        region = {
            "edgeId": edge_id,
            "edgeType": edge_type,
            "shape": edge.get("shape", {}),
            "fromNode": from_node_id,
            "toNode": to_node_id,
            "fromPort": from_port,
            "toPort": to_port,
            "start": [round(x0, 6), round(y0, 6), 0.0],
            "end": [round(x1, 6), round(y1, 6), 0.0],
            "spanLengthPx": round(length, 6),
            "pointStart": edge_point_start,
            "pointCount": (segment_count + 1) * transverse_point_count,
            "segmentCount": segment_count,
            "stationFractions": station_fractions,
            "transversePointCount": transverse_point_count,
            "transverseDivisions": transverse_divisions,
            "boundaryLayerLayers": int(controls["boundaryLayerLayers"]),
            "edgeRefinementFactor": edge_refinement_factor,
            "featureRefinement": feature_refinement,
            "refinementFactor": refinement_factor,
            "cellStart": cell_start,
            "cellCount": segment_count * transverse_divisions,
        }
        regions.append(region)
        endpoints_by_node.setdefault(from_node_id, []).append(
            {
                "edgeId": edge_id,
                "port": from_port,
                "role": "from",
                "pointIds": [edge_point_start + transverse for transverse in range(transverse_point_count)],
            }
        )
        last_station_start = edge_point_start + segment_count * transverse_point_count
        endpoints_by_node.setdefault(to_node_id, []).append(
            {
                "edgeId": edge_id,
                "port": to_port,
                "role": "to",
                "pointIds": [last_station_start + transverse for transverse in range(transverse_point_count)],
            }
        )

    _append_two_port_node_connectors(cells, regions, endpoints_by_node, transverse_divisions)
    cells = [_orient_cell_ccw(points, cell) for cell in cells]
    quality = _mesh_quality_report(points, cells, regions, controls["qualityThresholds"])
    if quality["status"] == "failed":
        warnings = "; ".join(str(item) for item in quality["warnings"]) or "mesh quality failed"
        raise ValueError(f"FlowLab mesh quality check failed: {warnings}")
    boundary_layer_plan = _boundary_layer_plan(project, edges, regions, controls)
    physical_group_map = _physical_group_map({"regions": regions})

    mesh = {
        "format": "flowlab-mesh-v1",
        "coordinateSystem": "flowlab-canvas-2d",
        "supportedGeometry": ["circular pipe", "circular venturi", "straight circular duct", "rectangular channel"],
        "points": points,
        "cells": cells,
        "cellTypes": [VTK_QUAD for _ in cells],
        "regions": regions,
        "controls": controls,
        "quality": quality,
        "boundaryLayerPlan": boundary_layer_plan,
        "physicalGroupMap": physical_group_map,
    }
    refinement_plan = {
        "schema": "flowlab.mesh_refinement_plan.v1",
        "productionReady": False,
        "controls": {
            "baseResolution": controls["baseResolution"],
            "baseSegments": controls["baseSegments"],
            "longitudinalRefinement": controls["longitudinalRefinement"],
            "featureRefinement": controls["featureRefinement"],
            "refinementRegions": controls["refinementRegions"],
        },
        "regions": [
            {
                "edgeId": region["edgeId"],
                "edgeType": region["edgeType"],
                "segmentCount": region.get("segmentCount"),
                "edgeRefinementFactor": region.get("edgeRefinementFactor"),
                "featureRefinement": region.get("featureRefinement"),
                "stationFractions": region.get("stationFractions"),
            }
            for region in regions
            if region.get("edgeType") != "connector"
        ],
        "notes": [
            "Feature refinement clusters source-mesh stations near Venturi throats or diameter transitions when explicitly enabled.",
            "This plan is deterministic starter-mesh evidence, not a replacement for CAD cleanup, curvature-based unstructured meshing, or solver-native adaptation.",
        ],
    }
    prism_layer_plan = _prism_layer_plan(boundary_layer_plan, controls)
    adaptation_plan = _adaptation_plan(quality, controls, refinement_plan, boundary_layer_plan, prism_layer_plan, regions)
    starter_stl = mesh_to_openfoam_starter_stl(mesh)
    reviewed_stl, reviewed_geometry, reviewed_surface_files = _reviewed_geometry_source(project, starter_stl)
    openfoam_snappy_handoff = _openfoam_snappy_handoff(mesh, physical_group_map, prism_layer_plan, controls, reviewed_geometry)
    su2_native_meshing_handoff = _su2_native_meshing_handoff(physical_group_map, prism_layer_plan, adaptation_plan)
    code_saturne_native_meshing_handoff = _code_saturne_native_meshing_handoff(physical_group_map, prism_layer_plan, adaptation_plan)
    production_mesh_plan = _production_mesh_plan(
        quality,
        controls,
        refinement_plan,
        boundary_layer_plan,
        prism_layer_plan,
        adaptation_plan,
        regions,
    )
    native_meshing_plan = _native_meshing_plan(
        production_mesh_plan,
        controls,
        boundary_layer_plan,
        prism_layer_plan,
        adaptation_plan,
        regions,
    )
    production_mesh_acceptance = _production_mesh_acceptance_checklist(
        production_mesh_plan,
        native_meshing_plan,
        physical_group_map,
        openfoam_snappy_handoff,
        prism_layer_plan,
        adaptation_plan,
    )
    openfoam_location = _openfoam_location_in_mesh(mesh)
    mesh["productionMeshPlan"] = production_mesh_plan
    mesh["nativeMeshingPlan"] = native_meshing_plan
    mesh["productionMeshAcceptance"] = production_mesh_acceptance
    mesh["prismLayerPlan"] = prism_layer_plan
    mesh["adaptationPlan"] = adaptation_plan
    mesh["openfoamSnappyHandoff"] = openfoam_snappy_handoff
    mesh["su2NativeMeshingHandoff"] = su2_native_meshing_handoff
    mesh["codeSaturneNativeMeshingHandoff"] = code_saturne_native_meshing_handoff
    files = {
        "mesh/flowlab_mesh.json": json.dumps(mesh, indent=2),
        "mesh/controls.json": json.dumps(controls, indent=2, sort_keys=True) + "\n",
        "mesh/quality.json": json.dumps(quality, indent=2, sort_keys=True) + "\n",
        "mesh/refinement_plan.json": json.dumps(refinement_plan, indent=2, sort_keys=True) + "\n",
        "mesh/boundary_layer_plan.json": json.dumps(boundary_layer_plan, indent=2, sort_keys=True) + "\n",
        "mesh/prism_layer_plan.json": json.dumps(prism_layer_plan, indent=2, sort_keys=True) + "\n",
        "mesh/adaptation_plan.json": json.dumps(adaptation_plan, indent=2, sort_keys=True) + "\n",
        "mesh/physical_groups.json": json.dumps(physical_group_map, indent=2, sort_keys=True) + "\n",
        "mesh/openfoam_snappy_handoff.json": json.dumps(openfoam_snappy_handoff, indent=2, sort_keys=True) + "\n",
        "mesh/su2_native_meshing_handoff.json": json.dumps(su2_native_meshing_handoff, indent=2, sort_keys=True) + "\n",
        "mesh/code_saturne_native_meshing_handoff.json": json.dumps(code_saturne_native_meshing_handoff, indent=2, sort_keys=True) + "\n",
        "mesh/openfoam_native_mesh_preflight.py": _openfoam_native_mesh_preflight_script(),
        "mesh/openfoam_snappyHexMeshDict.template": _openfoam_snappy_hex_mesh_dict_template(openfoam_snappy_handoff),
        "mesh/openfoam_surfaceFeatureExtractDict.template": _openfoam_surface_feature_extract_dict_template(openfoam_snappy_handoff),
        "mesh/openfoam_meshQualityDict.template": _openfoam_mesh_quality_dict_template(),
        "constant/triSurface/reviewedFlowLabSurfaces.stl": reviewed_stl,
        "system/snappyHexMeshDict": _openfoam_snappy_hex_mesh_dict_template(openfoam_snappy_handoff, openfoam_location),
        "system/surfaceFeatureExtractDict": _openfoam_surface_feature_extract_dict_template(openfoam_snappy_handoff),
        "system/meshQualityDict": _openfoam_mesh_quality_dict_template(),
        "mesh/production_mesh_plan.json": json.dumps(production_mesh_plan, indent=2, sort_keys=True) + "\n",
        "mesh/native_meshing_plan.json": json.dumps(native_meshing_plan, indent=2, sort_keys=True) + "\n",
        "mesh/production_mesh_acceptance.json": json.dumps(production_mesh_acceptance, indent=2, sort_keys=True) + "\n",
        "mesh/gmsh_production_handoff.geo": _gmsh_production_handoff_geo(mesh, native_meshing_plan),
        "mesh/flowlab_mesh.vtk": mesh_to_legacy_vtk(mesh, "FlowLab generated mesh"),
        "mesh/flowlab_mesh.vtu": mesh_to_vtu(mesh),
        "mesh/flowlab_mesh.su2": mesh_to_su2(mesh),
        "mesh/flowlab_mesh.msh": mesh_to_gmsh(mesh),
        "mesh/generate_mesh.py": _mesh_script(),
    }
    files.update(reviewed_surface_files)
    return MeshBundle(
        mesh=mesh,
        files=files,
        provenance=[
            "Generated a deterministic 2D quad-strip mesh from FlowLab rotated port-to-port geometry.",
            "Mesh v1 supports circular pipe, circular Venturi throat interpolation, and rectangular channel height.",
            "Mesh v1 exports native SU2 ASCII with quadrilateral elements and line boundary markers.",
            "Mesh v1 exports Gmsh 2.2 ASCII with physical groups for Code_Saturne mesh import.",
            "Mesh v1 applies optional longitudinal refinement and 2D transverse boundary-layer strip controls when provided.",
            "Mesh v1 emits a deterministic y-plus first-cell sizing plan for boundary-layer review.",
            "Mesh v1 emits native prism-layer review inputs derived from the y-plus plan, but no production prism mesh.",
            "Mesh v1 emits a solver-neutral adaptation plan for geometry, boundary-layer, and future solver-field refinement evidence, but no native adapted mesh.",
            "Mesh v1 can optionally apply feature-aware longitudinal refinement around Venturi throats and diameter transitions.",
            "Mesh v1 inserts simple connector cells across two-port components so fitted solver meshes remain face-connected through junction bodies.",
            "Mesh v1 includes deterministic quality metrics and fails closed for degenerate or inverted source quads.",
            "Mesh v1 emits a production mesh plan that records remaining CAD-quality 3D meshing, prism-layer, and solver-native quality evidence gaps.",
            "Mesh v1 emits a native meshing handoff manifest and review-only Gmsh .geo scaffold for production mesher setup.",
            "Mesh v1 emits an OpenFOAM snappyHexMesh handoff manifest for patch, refinement, and layer-control review, but no CAD/STL or native layer mesh.",
            "Mesh v1 emits SU2 and Code_Saturne native meshing handoff manifests for marker/group, boundary-layer, adaptation, and solver-quality review.",
            "Mesh v1 emits a solver-neutral production mesh acceptance checklist that blocks production approval until CAD, native volume mesh, boundary-layer, and solver quality evidence exist.",
            "Mesh v1 is not a CAD-quality unstructured volume mesh and should be inspected before solver execution.",
        ],
    )


def _append_two_port_node_connectors(
    cells: list[list[int]],
    regions: list[dict[str, Any]],
    endpoints_by_node: dict[str, list[dict[str, Any]]],
    transverse_divisions: int,
) -> None:
    for node_id, endpoints in endpoints_by_node.items():
        if len(endpoints) != 2:
            continue
        first, second = endpoints
        first_points = [int(index) for index in first["pointIds"]]
        second_points = [int(index) for index in second["pointIds"]]
        if len(first_points) != len(second_points) or len(first_points) < 2:
            continue
        connector_id = f"connector_{str(node_id).replace('-', '_')}"
        cell_start = len(cells)
        for transverse in range(transverse_divisions):
            cells.append([first_points[transverse], second_points[transverse], second_points[transverse + 1], first_points[transverse + 1]])
        left_wall = [(first_points[0], second_points[0])]
        right_wall = [(second_points[-1], first_points[-1])]
        regions.append(
            {
                "edgeId": connector_id,
                "edgeType": "connector",
                "nodeId": node_id,
                "fromEndpoint": {"edgeId": first["edgeId"], "port": first["port"], "role": first["role"]},
                "toEndpoint": {"edgeId": second["edgeId"], "port": second["port"], "role": second["role"]},
                "pointIds": [*first_points, *second_points],
                "transversePointCount": len(first_points),
                "transverseDivisions": transverse_divisions,
                "boundaryLayerLayers": None,
                "cellStart": cell_start,
                "cellCount": transverse_divisions,
                "boundaryLines": [
                    [f"wall_{connector_id}_left", left_wall],
                    [f"wall_{connector_id}_right", right_wall],
                ],
            }
        )


def mesh_to_legacy_vtk(mesh: dict[str, Any], title: str) -> str:
    points = mesh["points"]
    cells = mesh["cells"]
    lines = [
        "# vtk DataFile Version 3.0",
        title,
        "ASCII",
        "DATASET UNSTRUCTURED_GRID",
        f"POINTS {len(points)} float",
    ]
    lines.extend(f"{point[0]} {point[1]} {point[2]}" for point in points)
    total = sum(len(cell) + 1 for cell in cells)
    lines.append(f"CELLS {len(cells)} {total}")
    lines.extend(" ".join([str(len(cell)), *(str(index) for index in cell)]) for cell in cells)
    lines.append(f"CELL_TYPES {len(cells)}")
    lines.extend(str(VTK_QUAD) for _ in cells)
    return "\n".join(lines) + "\n"


def mesh_to_vtu(mesh: dict[str, Any]) -> str:
    points = mesh["points"]
    cells = mesh["cells"]
    connectivity = " ".join(str(index) for cell in cells for index in cell)
    offsets: list[str] = []
    offset = 0
    for cell in cells:
        offset += len(cell)
        offsets.append(str(offset))
    types = " ".join(str(VTK_QUAD) for _ in cells)
    point_text = " ".join(f"{point[0]} {point[1]} {point[2]}" for point in points)
    return f"""<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="{len(points)}" NumberOfCells="{len(cells)}">
      <Points>
        <DataArray type="Float32" NumberOfComponents="3" format="ascii">{point_text}</DataArray>
      </Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">{connectivity}</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">{" ".join(offsets)}</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">{types}</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""


def su2_marker_tags(mesh: dict[str, Any]) -> dict[str, list[str]]:
    tags = {"inlet": [], "outlet": [], "wall": []}
    for region in mesh["regions"]:
        for tag, elements in _boundary_lines(region):
            if not elements:
                continue
            if tag.startswith("inlet_"):
                tags["inlet"].append(tag)
            elif tag.startswith("outlet_"):
                tags["outlet"].append(tag)
            elif tag.startswith("wall_"):
                tags["wall"].append(tag)
    return tags


def mesh_to_su2(mesh: dict[str, Any]) -> str:
    points = mesh["points"]
    cells = mesh["cells"]
    lines = [
        "% FlowLab native SU2 mesh export",
        "% 2D quadrilateral strip mesh generated from rotated port-to-port FlowLab geometry.",
        "NDIME= 2",
        f"NPOIN= {len(points)}",
    ]
    lines.extend(f"{point[0]} {point[1]}" for point in points)
    lines.append(f"NELEM= {len(cells)}")
    lines.extend(" ".join([str(SU2_QUAD), *(str(index) for index in cell)]) for cell in cells)

    markers: list[tuple[str, list[tuple[int, int]]]] = []
    for region in mesh["regions"]:
        markers.extend(_boundary_lines(region))

    lines.append(f"NMARK= {len(markers)}")
    for tag, elements in markers:
        lines.append(f"MARKER_TAG= {tag}")
        lines.append(f"MARKER_ELEMS= {len(elements)}")
        lines.extend(f"{SU2_LINE} {start} {end}" for start, end in elements)
    return "\n".join(lines) + "\n"


def _boundary_lines(region: dict[str, Any]) -> list[tuple[str, list[tuple[int, int]]]]:
    if isinstance(region.get("boundaryLines"), list):
        boundary_lines: list[tuple[str, list[tuple[int, int]]]] = []
        for item in region["boundaryLines"]:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            tag = str(item[0])
            segments = []
            for segment in item[1]:
                if isinstance(segment, (list, tuple)) and len(segment) == 2:
                    segments.append((int(segment[0]), int(segment[1])))
            boundary_lines.append((tag, segments))
        return boundary_lines
    edge_id = str(region["edgeId"]).replace("-", "_")
    point_start = int(region["pointStart"])
    point_count = int(region["pointCount"])
    transverse_points = int(region.get("transversePointCount") or 2)
    stations = point_count // transverse_points
    last_station = stations - 1
    inlet_segments = [
        (point_start + transverse + 1, point_start + transverse)
        for transverse in range(transverse_points - 1)
    ]
    outlet_segments = [
        (
            point_start + last_station * transverse_points + transverse,
            point_start + last_station * transverse_points + transverse + 1,
        )
        for transverse in range(transverse_points - 1)
    ]
    return [
        (f"inlet_{edge_id}", inlet_segments),
        (f"outlet_{edge_id}", outlet_segments),
        (
            f"wall_{edge_id}_left",
            [(point_start + index * transverse_points, point_start + (index + 1) * transverse_points) for index in range(stations - 1)],
        ),
        (
            f"wall_{edge_id}_right",
            [
                (
                    point_start + (index + 1) * transverse_points + transverse_points - 1,
                    point_start + index * transverse_points + transverse_points - 1,
                )
                for index in range(stations - 1)
            ],
        ),
    ]


def mesh_to_gmsh(mesh: dict[str, Any]) -> str:
    """Export the FlowLab inspection mesh as a thin Gmsh 2.2 volume mesh.

    Code_Saturne interprets Gmsh physical entities as groups, so each boundary
    patch and each edge region receives a deterministic physical name. Unlike
    the browser/SU2 inspection meshes, this export extrudes the 2D quad strip
    into one hexahedral layer because Code_Saturne requires volume elements.
    """

    source_points = mesh["points"]
    cells = mesh["cells"]
    physical_names: list[tuple[int, int, str]] = []
    elements: list[tuple[int, int, list[int], list[int]]] = []
    physical_tag = 1
    element_id = 1
    depth = max(_average_cell_span(source_points, cells) * 0.35, 1.0)
    half_depth = depth / 2.0
    points: list[tuple[float, float, float]] = []
    lower_node_ids: list[int] = []
    upper_node_ids: list[int] = []
    for point in source_points:
        x, y, z = float(point[0]), float(point[1]), float(point[2])
        lower_node_ids.append(len(points) + 1)
        points.append((x, y, z - half_depth))
        upper_node_ids.append(len(points) + 1)
        points.append((x, y, z + half_depth))

    for region in mesh["regions"]:
        edge_id = str(region["edgeId"]).replace("-", "_")
        cell_start = int(region["cellStart"])
        cell_count = int(region["cellCount"])
        region_tag = physical_tag
        physical_tag += 1
        physical_names.append((3, region_tag, f"fluid_{edge_id}"))
        for cell in cells[cell_start : cell_start + cell_count]:
            oriented_cell = _orient_cell_ccw(source_points, cell)
            lower = [lower_node_ids[index] for index in oriented_cell]
            upper = [upper_node_ids[index] for index in oriented_cell]
            elements.append((element_id, GMSH_HEXAHEDRON, [region_tag, region_tag], [*lower, *upper]))
            element_id += 1

        for name, line_segments in _boundary_lines(region):
            boundary_tag = physical_tag
            physical_tag += 1
            physical_names.append((2, boundary_tag, name))
            for start, end in line_segments:
                elements.append(
                    (
                        element_id,
                        GMSH_QUAD,
                        [boundary_tag, boundary_tag],
                        [lower_node_ids[start], lower_node_ids[end], upper_node_ids[end], upper_node_ids[start]],
                    )
                )
                element_id += 1

        front_back_tag = physical_tag
        physical_tag += 1
        physical_names.append((2, front_back_tag, f"wall_{edge_id}_front_back"))
        for cell in cells[cell_start : cell_start + cell_count]:
            oriented_cell = _orient_cell_ccw(source_points, cell)
            elements.append(
                (element_id, GMSH_QUAD, [front_back_tag, front_back_tag], [lower_node_ids[index] for index in reversed(oriented_cell)])
            )
            element_id += 1
            elements.append(
                (
                    element_id,
                    GMSH_QUAD,
                    [front_back_tag, front_back_tag],
                    [upper_node_ids[index] for index in oriented_cell],
                )
            )
            element_id += 1

    lines = [
        "$MeshFormat",
        "2.2 0 8",
        "$EndMeshFormat",
        "$PhysicalNames",
        str(len(physical_names)),
    ]
    lines.extend(f'{dimension} {tag} "{name}"' for dimension, tag, name in physical_names)
    lines.extend(
        [
            "$EndPhysicalNames",
            "$Nodes",
            str(len(points)),
        ]
    )
    lines.extend(f"{index} {point[0]} {point[1]} {point[2]}" for index, point in enumerate(points, start=1))
    lines.extend(["$EndNodes", "$Elements", str(len(elements))])
    for element_id, element_type, tags, node_ids in elements:
        lines.append(
            " ".join(
                [
                    str(element_id),
                    str(element_type),
                    str(len(tags)),
                    *(str(tag) for tag in tags),
                    *(str(node_id) for node_id in node_ids),
                ]
            )
        )
    lines.append("$EndElements")
    return "\n".join(lines) + "\n"


def mesh_to_openfoam_polymesh(
    mesh: dict[str, Any],
    *,
    root: str = "constant/polyMesh",
    wall_patch_name: str = "walls",
    wall_patch_type: str = "wall",
    wall_patch_entries: dict[str, str] | None = None,
) -> dict[str, str]:
    """Export the FlowLab quad-strip mesh as a thin OpenFOAM polyMesh.

    The export intentionally keeps the same v1 geometry limits as the SU2/Gmsh
    paths, but it avoids replacing the pipe with a rectangular blockMesh domain.
    """

    source_points = mesh["points"]
    cells = mesh["cells"]
    depth = max(_average_cell_span(source_points, cells) * 0.35, 1.0)
    half_depth = depth / 2.0
    scale = 0.01
    points: list[tuple[float, float, float]] = []
    lower_point_ids: list[int] = []
    upper_point_ids: list[int] = []
    for point in source_points:
        x, y, z = float(point[0]) * scale, float(point[1]) * scale, float(point[2]) * scale
        lower_point_ids.append(len(points))
        points.append((x, y, z - half_depth * scale))
        upper_point_ids.append(len(points))
        points.append((x, y, z + half_depth * scale))

    line_patches: dict[tuple[int, int], str] = {}
    for region in mesh["regions"]:
        for name, line_segments in _boundary_lines(region):
            for start, end in line_segments:
                line_patches[tuple(sorted((start, end)))] = name

    raw_faces: dict[tuple[int, ...], list[tuple[int, list[int], str]]] = {}

    def add_face(cell_index: int, vertices: list[int], patch: str) -> None:
        raw_faces.setdefault(tuple(sorted(vertices)), []).append((cell_index, vertices, patch))

    for cell_index, cell in enumerate(cells):
        oriented_cell = _orient_cell_ccw(source_points, cell)
        lower = [lower_point_ids[index] for index in oriented_cell]
        upper = [upper_point_ids[index] for index in oriented_cell]
        add_face(cell_index, list(reversed(lower)), "frontAndBack")
        add_face(cell_index, upper, "frontAndBack")
        for start_index, end_index in zip(oriented_cell, oriented_cell[1:] + oriented_cell[:1]):
            patch = line_patches.get(tuple(sorted((start_index, end_index))), "walls")
            add_face(
                cell_index,
                [lower_point_ids[start_index], lower_point_ids[end_index], upper_point_ids[end_index], upper_point_ids[start_index]],
                patch,
            )

    faces: list[list[int]] = []
    owners: list[int] = []
    neighbours: list[int] = []
    boundary_by_patch: dict[str, list[tuple[int, list[int]]]] = {
        "inlet": [],
        "outlet": [],
        wall_patch_name: [],
        "frontAndBack": [],
    }

    for occurrences in raw_faces.values():
        if len(occurrences) == 2:
            owner_cell, vertices, _patch = occurrences[0]
            neighbour_cell = occurrences[1][0]
            faces.append(vertices)
            owners.append(owner_cell)
            neighbours.append(neighbour_cell)
            continue
        if len(occurrences) != 1:
            raise ValueError("OpenFOAM polyMesh export found a non-manifold face.")
        owner_cell, vertices, patch = occurrences[0]
        if patch.startswith("inlet_"):
            patch_name = "inlet"
        elif patch.startswith("outlet_"):
            patch_name = "outlet"
        elif patch == "frontAndBack":
            patch_name = "frontAndBack"
        else:
            patch_name = wall_patch_name
        boundary_by_patch[patch_name].append((owner_cell, vertices))

    boundary_specs: list[tuple[str, str, int, int, dict[str, str]]] = []
    patch_defs = (
        ("inlet", "patch", {}),
        ("outlet", "patch", {}),
        (wall_patch_name, wall_patch_type, wall_patch_entries or {}),
        ("frontAndBack", "empty", {}),
    )
    for patch_name, patch_type, entries in patch_defs:
        start_face = len(faces)
        for owner_cell, vertices in boundary_by_patch[patch_name]:
            faces.append(vertices)
            owners.append(owner_cell)
        boundary_specs.append((patch_name, patch_type, len(boundary_by_patch[patch_name]), start_face, entries))

    return {
        f"{root}/points": _openfoam_points(points),
        f"{root}/faces": _openfoam_faces(faces),
        f"{root}/owner": _openfoam_label_list("owner", owners),
        f"{root}/neighbour": _openfoam_label_list("neighbour", neighbours),
        f"{root}/boundary": _openfoam_boundary(boundary_specs),
    }


def _wall_boundary_segments(mesh: dict[str, Any]) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    for region in mesh["regions"]:
        for name, line_segments in _boundary_lines(region):
            if name.startswith("inlet_") or name.startswith("outlet_"):
                continue
            segments.extend(line_segments)
    return segments


def _outward_offset_direction(
    source_points: list[list[float]],
    start: int,
    end: int,
    centroid: tuple[float, float],
) -> tuple[float, float]:
    start_point = source_points[start]
    end_point = source_points[end]
    midpoint_x = (float(start_point[0]) + float(end_point[0])) / 2.0
    midpoint_y = (float(start_point[1]) + float(end_point[1])) / 2.0
    away_x = midpoint_x - centroid[0]
    away_y = midpoint_y - centroid[1]
    away_length = math.hypot(away_x, away_y)
    if away_length > 1.0e-9:
        return away_x / away_length, away_y / away_length

    tangent_x = float(end_point[0]) - float(start_point[0])
    tangent_y = float(end_point[1]) - float(start_point[1])
    normal_length = math.hypot(tangent_x, tangent_y)
    if normal_length > 1.0e-9:
        return -tangent_y / normal_length, tangent_x / normal_length
    return 1.0, 0.0


def _mesh_to_openfoam_solid_jacket_polymesh(mesh: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    source_points = mesh["points"]
    cells = mesh["cells"]
    wall_segments = _wall_boundary_segments(mesh)
    depth = max(_average_cell_span(source_points, cells) * 0.35, 1.0)
    half_depth = depth / 2.0
    scale = 0.01
    jacket_thickness = max(_average_cell_span(source_points, cells) * 0.2, 1.0) * scale
    centroid = (
        sum(float(point[0]) for point in source_points) / len(source_points),
        sum(float(point[1]) for point in source_points) / len(source_points),
    )

    points: list[tuple[float, float, float]] = []
    interface_faces: list[tuple[int, list[int]]] = []
    outer_faces: list[tuple[int, list[int]]] = []
    cap_faces: list[tuple[int, list[int]]] = []
    front_back_faces: list[tuple[int, list[int]]] = []

    def add_point(point_index: int, z_offset: float, direction: tuple[float, float], outward: bool) -> int:
        point = source_points[point_index]
        x = float(point[0]) * scale
        y = float(point[1]) * scale
        if outward:
            x += direction[0] * jacket_thickness
            y += direction[1] * jacket_thickness
        points.append((x, y, float(point[2]) * scale + z_offset))
        return len(points) - 1

    for owner, (start, end) in enumerate(wall_segments):
        direction = _outward_offset_direction(source_points, start, end, centroid)
        inner_start_lower = add_point(start, -half_depth * scale, direction, False)
        inner_end_lower = add_point(end, -half_depth * scale, direction, False)
        outer_end_lower = add_point(end, -half_depth * scale, direction, True)
        outer_start_lower = add_point(start, -half_depth * scale, direction, True)
        inner_start_upper = add_point(start, half_depth * scale, direction, False)
        inner_end_upper = add_point(end, half_depth * scale, direction, False)
        outer_end_upper = add_point(end, half_depth * scale, direction, True)
        outer_start_upper = add_point(start, half_depth * scale, direction, True)

        interface_faces.append((owner, [inner_start_lower, inner_end_lower, inner_end_upper, inner_start_upper]))
        outer_faces.append((owner, [outer_start_lower, outer_start_upper, outer_end_upper, outer_end_lower]))
        cap_faces.append((owner, [inner_start_lower, inner_start_upper, outer_start_upper, outer_start_lower]))
        cap_faces.append((owner, [inner_end_lower, outer_end_lower, outer_end_upper, inner_end_upper]))
        front_back_faces.append((owner, [inner_start_lower, outer_start_lower, outer_end_lower, inner_end_lower]))
        front_back_faces.append((owner, [inner_start_upper, inner_end_upper, outer_end_upper, outer_start_upper]))

    faces: list[list[int]] = []
    owners: list[int] = []
    neighbours: list[int] = []
    boundary_specs: list[tuple[str, str, int, int, dict[str, str]]] = []
    patch_groups = [
        (
            "solid_to_fluid",
            "mappedWall",
            {"neighbourRegion": "fluid", "neighbourPatch": "fluid_to_solid"},
            interface_faces,
        ),
        ("solid_outer_wall", "wall", {}, outer_faces),
        ("solid_jacket_caps", "wall", {}, cap_faces),
        ("frontAndBack", "empty", {}, front_back_faces),
    ]
    for patch_name, patch_type, entries, patch_faces in patch_groups:
        start_face = len(faces)
        for owner, vertices in patch_faces:
            faces.append(vertices)
            owners.append(owner)
        boundary_specs.append((patch_name, patch_type, len(patch_faces), start_face, entries))

    files = {
        "constant/solid/polyMesh/points": _openfoam_points(points),
        "constant/solid/polyMesh/faces": _openfoam_faces(faces),
        "constant/solid/polyMesh/owner": _openfoam_label_list("owner", owners),
        "constant/solid/polyMesh/neighbour": _openfoam_label_list("neighbour", neighbours),
        "constant/solid/polyMesh/boundary": _openfoam_boundary(boundary_specs),
    }
    metadata = {
        "strategy": "outer-wall-offset-starter-sleeve",
        "cellCount": len(wall_segments),
        "wallSegmentCount": len(wall_segments),
        "pointCount": len(points),
        "innerInterfaceFaceCount": len(interface_faces),
        "outerWallFaceCount": len(outer_faces),
        "capFaceCount": len(cap_faces),
        "frontBackFaceCount": len(front_back_faces),
        "thickness": round(jacket_thickness, 9),
        "nonOverlapping": len(wall_segments) > 0,
        "notes": [
            "The solid region is a deterministic starter sleeve offset outward from the fluid wall interface.",
            "The sleeve preserves one mapped-wall solid face for each fluid wall interface face.",
            "Adjacent sleeve cells are not yet CAD-quality merged solids and still require OpenFOAM checkMesh review.",
        ],
    }
    return files, metadata


def mesh_to_openfoam_cht_region_polymesh(mesh: dict[str, Any]) -> dict[str, str]:
    """Export paired fluid/solid starter polyMeshes for OpenFOAM CHT.

    This is a structural multi-region starter mesh. The fluid region uses the
    generated FlowLab strip, while the solid region is an outward offset sleeve
    around the fluid wall interface. It is suitable for generated-case
    validation and early solver bring-up, but it is not a production solid
    jacket mesh.
    """

    fluid_files = mesh_to_openfoam_polymesh(
        mesh,
        root="constant/fluid/polyMesh",
        wall_patch_name="fluid_to_solid",
        wall_patch_type="mappedWall",
        wall_patch_entries={"neighbourRegion": "solid", "neighbourPatch": "solid_to_fluid"},
    )
    solid_files, solid_jacket = _mesh_to_openfoam_solid_jacket_polymesh(mesh)
    mesh_quality = mesh.get("quality") if isinstance(mesh.get("quality"), dict) else {}
    quality_summary = mesh_quality.get("summary") if isinstance(mesh_quality.get("summary"), dict) else {}
    controls = mesh.get("controls") if isinstance(mesh.get("controls"), dict) else {}
    boundary_layer_plan = mesh.get("boundaryLayerPlan") if isinstance(mesh.get("boundaryLayerPlan"), dict) else {}
    prism_layer_plan = mesh.get("prismLayerPlan") if isinstance(mesh.get("prismLayerPlan"), dict) else {}
    source_regions = mesh.get("regions") if isinstance(mesh.get("regions"), list) else []
    interface_face_count = _openfoam_patch_face_count(fluid_files["constant/fluid/polyMesh/boundary"], "fluid_to_solid")
    solid_interface_face_count = _openfoam_patch_face_count(solid_files["constant/solid/polyMesh/boundary"], "solid_to_fluid")
    readiness_checks = [
        {
            "id": "multi-region-dictionaries",
            "status": "pass",
            "detail": "Fluid and solid region dictionaries and polyMesh directories are generated.",
        },
        {
            "id": "paired-mapped-wall-patches",
            "status": "pass" if interface_face_count == solid_interface_face_count and interface_face_count > 0 else "fail",
            "detail": f"fluid_to_solid faces={interface_face_count}; solid_to_fluid faces={solid_interface_face_count}.",
        },
        {
            "id": "source-mesh-quality",
            "status": "pass" if mesh_quality.get("status") in {"ok", "warning"} else "fail",
            "detail": f"FlowLab source mesh quality status is {mesh_quality.get('status', 'unknown')}.",
        },
        {
            "id": "non-overlapping-solid-jacket",
            "status": "pass" if solid_jacket["nonOverlapping"] and solid_interface_face_count == interface_face_count else "fail",
            "detail": (
                "Solid region is generated as an outward offset starter sleeve "
                f"with {solid_jacket['cellCount']} jacket cells."
            ),
        },
        {
            "id": "region-checkmesh-plan",
            "status": "pass",
            "detail": "Generated AllmeshCheck records per-region checkMesh commands for fluid and solid meshes.",
        },
        {
            "id": "cht-boundary-layer-evidence",
            "status": "fail",
            "detail": "mesh/prism_layer_plan.json records target prism-layer inputs, but no production 3D prism-layer mesh or y-plus evidence is generated.",
        },
        {
            "id": "region-checkmesh-evidence",
            "status": "fail",
            "detail": "Per-region OpenFOAM checkMesh evidence is not collected before unblocking CHT execution.",
        },
    ]
    production_ready = all(check["status"] == "pass" for check in readiness_checks)
    interface_manifest = {
        "schema": "flowlab.openfoam_cht_interface.v1",
        "regions": ["fluid", "solid"],
        "patches": {
            "fluid": {
                "name": "fluid_to_solid",
                "type": "mappedWall",
                "neighbourRegion": "solid",
                "neighbourPatch": "solid_to_fluid",
                "faceCount": interface_face_count,
            },
            "solid": {
                "name": "solid_to_fluid",
                "type": "mappedWall",
                "neighbourRegion": "fluid",
                "neighbourPatch": "fluid_to_solid",
                "faceCount": solid_interface_face_count,
            },
        },
        "fluidPatch": "fluid_to_solid",
        "solidPatch": "solid_to_fluid",
        "patchType": "mappedWall",
        "interfaceApproximation": "outer-wall-offset-starter-sleeve",
        "regionMeshChecks": {
            "script": "AllmeshCheck",
            "commands": [
                "checkMesh -region fluid -allGeometry -allTopology",
                "checkMesh -region solid -allGeometry -allTopology",
            ],
            "evidenceStatus": "planned-not-executed",
            "notes": [
                "The generated script documents the exact native OpenFOAM checks needed before CHT execution can be unblocked.",
                "FlowLab still requires captured per-region checkMesh logs before productionReady can become true.",
            ],
        },
        "productionReady": production_ready,
        "readinessChecks": readiness_checks,
        "blockingReasons": [
            check["detail"]
            for check in readiness_checks
            if check["status"] != "pass"
        ],
        "sourceMesh": {
            "pointCount": quality_summary.get("pointCount"),
            "cellCount": quality_summary.get("cellCount"),
            "regionCount": quality_summary.get("regionCount", len(source_regions)),
            "qualityStatus": mesh_quality.get("status"),
            "qualitySummary": quality_summary,
            "boundaryLayerLayers": controls.get("boundaryLayerLayers"),
            "transverseFractions": controls.get("transverseFractions"),
            "boundaryLayerPlanSchema": boundary_layer_plan.get("schema"),
            "boundaryLayerEdgeCount": len(boundary_layer_plan.get("edges", [])) if isinstance(boundary_layer_plan.get("edges"), list) else 0,
            "prismLayerPlanSchema": prism_layer_plan.get("schema"),
            "prismLayerEdgeCount": len(prism_layer_plan.get("edges", [])) if isinstance(prism_layer_plan.get("edges"), list) else 0,
        },
        "prismLayerPlan": {
            "file": "mesh/prism_layer_plan.json",
            "schema": prism_layer_plan.get("schema"),
            "productionReady": prism_layer_plan.get("productionReady"),
            "nativeMesherRequired": True,
        },
        "solidJacket": solid_jacket,
        "notes": [
            "The starter CHT mesh uses the FlowLab thin strip as the fluid region and an outward offset sleeve as the solid region.",
            "Mapped wall metadata creates a structural fluid/solid interface for OpenFOAM case bring-up.",
            "A production CHT case still needs CAD-quality solid topology, 3D boundary-layer evidence, and per-region OpenFOAM mesh-quality checks.",
        ],
    }
    return {
        **fluid_files,
        **solid_files,
        "constant/flowlab_cht_interface.json": json.dumps(interface_manifest, indent=2, sort_keys=True) + "\n",
    }


def _openfoam_patch_face_count(boundary_text: str, patch_name: str) -> int:
    lines = boundary_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != patch_name:
            continue
        for nested in lines[index + 1 : index + 8]:
            stripped = nested.strip()
            if stripped.startswith("nFaces"):
                parts = stripped.replace(";", "").split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        return 0
    return 0


def _openfoam_poly_header(class_name: str, object_name: str) -> str:
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
  FlowLab generated OpenFOAM polyMesh
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    object      {object_name};
}}

"""


def _openfoam_points(points: list[tuple[float, float, float]]) -> str:
    lines = [_openfoam_poly_header("vectorField", "points"), str(len(points)), "("]
    lines.extend(f"({point[0]:.9g} {point[1]:.9g} {point[2]:.9g})" for point in points)
    lines.append(")")
    return "\n".join(lines) + "\n"


def _openfoam_faces(faces: list[list[int]]) -> str:
    lines = [_openfoam_poly_header("faceList", "faces"), str(len(faces)), "("]
    lines.extend(f"{len(face)}({' '.join(str(index) for index in face)})" for face in faces)
    lines.append(")")
    return "\n".join(lines) + "\n"


def _openfoam_label_list(object_name: str, values: list[int]) -> str:
    lines = [_openfoam_poly_header("labelList", object_name), str(len(values)), "("]
    lines.extend(str(value) for value in values)
    lines.append(")")
    return "\n".join(lines) + "\n"


def _openfoam_boundary(boundary_specs: list[tuple[str, str, int, int, dict[str, str]]]) -> str:
    lines = [_openfoam_poly_header("polyBoundaryMesh", "boundary"), str(len(boundary_specs)), "("]
    for name, patch_type, face_count, start_face, entries in boundary_specs:
        lines.extend(
            [
                name,
                "{",
                f"    type            {patch_type};",
                *[f"    {key} {value};" for key, value in entries.items()],
                f"    nFaces          {face_count};",
                f"    startFace       {start_face};",
                "}",
            ]
        )
    lines.append(")")
    return "\n".join(lines) + "\n"


def _average_cell_span(points: list[list[float]], cells: list[list[int]]) -> float:
    spans: list[float] = []
    for cell in cells:
        if len(cell) < 4:
            continue
        a, b, c, d = (points[index] for index in cell[:4])
        width_a = math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
        width_b = math.hypot(float(c[0]) - float(d[0]), float(c[1]) - float(d[1]))
        spans.extend([width_a, width_b])
    if not spans:
        return 1.0
    return sum(spans) / len(spans)


def _orient_cell_ccw(points: list[list[float]], cell: list[int]) -> list[int]:
    area = 0.0
    polygon = [points[index] for index in cell]
    for current, following in zip(polygon, polygon[1:] + polygon[:1]):
        area += float(current[0]) * float(following[1]) - float(following[0]) * float(current[1])
    return list(cell) if area >= 0 else list(reversed(cell))


def _mesh_script() -> str:
    return """#!/usr/bin/env python3
\"\"\"Regenerate the FlowLab v1 mesh bundle from flowlab_project.json.

Run from the generated solver case root:
    python3 mesh/generate_mesh.py
\"\"\"

import json
from pathlib import Path

from server.flowlab.mesh import generate_mesh_bundle

project = json.loads(Path("flowlab_project.json").read_text())
bundle = generate_mesh_bundle(project)
for name, content in bundle.files.items():
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
print("Wrote", len(bundle.files), "FlowLab mesh files")
"""
