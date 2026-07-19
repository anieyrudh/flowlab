"""Run and retain evidence for the narrow-envelope straight-pipe benchmark.

This module deliberately materializes a dedicated 3D OpenFOAM case instead of
reusing FlowLab's generic 2D-style smoke adapter.  It produces analysis-only
evidence: a successful local run is never treated as independent validation.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from typing import Any, Iterable, Sequence

from .adapters import DEFAULT_OPENFOAM_IMAGE, _openfoam_image
from .performance_protocol import (
    PerformanceProtocolError,
    classify_cpu_set_scope,
    native_compatibility_decision,
    summarize_numeric_trials,
)
from .verification import (
    LAMINAR_REFERENCE_MAX_REYNOLDS,
    StraightPipeSpec,
    VerificationInputError,
    relative_mass_flow_imbalance,
    richardson_grid_convergence,
    straight_pipe_reference,
)


RUNNER_SCHEMA = "flowlab.straight-pipe-runtime-run.v1"
QOI_SCHEMA = "flowlab.straight-pipe-qoi-extraction.v1"
EVIDENCE_SCHEMA = "flowlab.straight-pipe-evidence.v1"
PARALLEL_EVIDENCE_SCHEMA = "flowlab.straight-pipe-parallel-evidence.v1"
REPLICATED_TIMING_SCHEMA = "flowlab.straight-pipe-replicated-timing.v1"
DEFAULT_IMAGE = DEFAULT_OPENFOAM_IMAGE
DEFAULT_PLATFORM = "linux/amd64"
DEFAULT_FINE_GRID_GCI_PERCENT_LIMIT = 1.0
DEFAULT_MESH_SIZES_M = (0.0015, 0.00075, 0.000375)
DEFAULT_PARALLEL_RANKS = (2, 4)
DEFAULT_PARALLEL_QOI_RELATIVE_TOLERANCE = 1.0e-6
DEFAULT_TIMING_WARMUP_TRIALS = 1
DEFAULT_TIMING_MEASUREMENT_TRIALS = 5
SECTOR90_MESH_RECIPE = "sector90-v3"
FULL_PIPE_OGRID_MESH_RECIPE = "full-pipe-ogrid-v1"
DEFAULT_MESH_RECIPE = FULL_PIPE_OGRID_MESH_RECIPE
DEFAULT_OGRID_AZIMUTHAL_CELLS_PER_QUADRANT = 16
DEFAULT_OGRID_CORE_CELLS_PER_SIDE = 16
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PERIODIC_CONVERGENCE_TAIL_SAMPLES = 50
_PERIODIC_MEAN_VELOCITY_RELATIVE_TOLERANCE = 1.0e-8
_PERIODIC_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE = 1.0e-6
_PERIODIC_GLOBAL_CONTINUITY_TOLERANCE = 1.0e-10
_PERIODIC_LINEAR_FINAL_RESIDUAL_TOLERANCE = 1.0e-8


class StraightPipeRunError(RuntimeError):
    """Raised when a real solver result cannot support the declared evidence."""


@dataclass(frozen=True)
class StraightPipeRunSpec:
    """Frozen SI inputs for the executable Hagen–Poiseuille reference case."""

    length_m: float = 0.024
    radius_m: float = 0.006
    density_kg_m3: float = 1000.0
    dynamic_viscosity_pa_s: float = 0.001
    volumetric_flow_rate_m3_s: float = 1.0e-5
    mesh_sizes_m: tuple[float, float, float] = DEFAULT_MESH_SIZES_M
    mesh_recipe: str = DEFAULT_MESH_RECIPE
    ogrid_azimuthal_cells_per_quadrant: int = DEFAULT_OGRID_AZIMUTHAL_CELLS_PER_QUADRANT
    ogrid_core_cells_per_side: int = DEFAULT_OGRID_CORE_CELLS_PER_SIDE

    def __post_init__(self) -> None:
        spec = self.reference_spec()
        reference = straight_pipe_reference(spec)
        if reference["reynoldsNumber"] >= LAMINAR_REFERENCE_MAX_REYNOLDS:
            raise StraightPipeRunError(
                f"reference Reynolds number must be below {LAMINAR_REFERENCE_MAX_REYNOLDS:g}"
            )
        if len(self.mesh_sizes_m) != 3:
            raise StraightPipeRunError("exactly three serial mesh levels are required")
        mesh_sizes = tuple(float(value) for value in self.mesh_sizes_m)
        if any(not math.isfinite(value) or value <= 0.0 for value in mesh_sizes):
            raise StraightPipeRunError("mesh sizes must be finite positive SI values")
        if any(value >= self.radius_m for value in mesh_sizes):
            raise StraightPipeRunError("each mesh size must be smaller than the pipe radius")
        if tuple(sorted(mesh_sizes, reverse=True)) != mesh_sizes:
            raise StraightPipeRunError("mesh sizes must be ordered coarse-to-fine")
        ratios = (mesh_sizes[0] / mesh_sizes[1], mesh_sizes[1] / mesh_sizes[2])
        if not math.isclose(ratios[0], ratios[1], rel_tol=1.0e-12, abs_tol=1.0e-15):
            raise StraightPipeRunError("the three mesh sizes must use one uniform refinement ratio")
        if ratios[0] < 1.3:
            raise StraightPipeRunError("the mesh refinement ratio must be at least 1.3")
        if self.mesh_recipe not in (SECTOR90_MESH_RECIPE, FULL_PIPE_OGRID_MESH_RECIPE):
            raise StraightPipeRunError(
                f"mesh_recipe must be {SECTOR90_MESH_RECIPE!r} or {FULL_PIPE_OGRID_MESH_RECIPE!r}"
            )
        for field_name, value in (
            ("ogrid_azimuthal_cells_per_quadrant", self.ogrid_azimuthal_cells_per_quadrant),
            ("ogrid_core_cells_per_side", self.ogrid_core_cells_per_side),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 2:
                raise StraightPipeRunError(f"{field_name} must be an integer of at least 2")
        if self.mesh_recipe == FULL_PIPE_OGRID_MESH_RECIPE and (
            self.ogrid_core_cells_per_side != self.ogrid_azimuthal_cells_per_quadrant
        ):
            raise StraightPipeRunError(
                "the full-pipe O-grid requires equal core-side and outer-azimuthal counts "
                "for conforming block interfaces"
            )

    def reference_spec(self) -> StraightPipeSpec:
        return StraightPipeSpec(
            length_m=self.length_m,
            radius_m=self.radius_m,
            density_kg_m3=self.density_kg_m3,
            dynamic_viscosity_pa_s=self.dynamic_viscosity_pa_s,
            volumetric_flow_rate_m3_s=self.volumetric_flow_rate_m3_s,
        )

    def reference(self) -> dict[str, float]:
        return straight_pipe_reference(self.reference_spec())


def default_run_spec() -> StraightPipeRunSpec:
    return StraightPipeRunSpec()


def _spec_from_run_manifest(output_dir: Path) -> StraightPipeRunSpec:
    """Recover the frozen mesh recipe used by an already materialized run.

    The first captured v3 suite predates ``mesh_recipe``.  Its lack is
    intentionally interpreted as the legacy 90-degree sector rather than the
    current default O-grid so later packaging or MPI work cannot silently
    reinterpret already-captured results.
    """

    manifest_path = output_dir / "run-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        values = dict(manifest["spec"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StraightPipeRunError(f"could not read frozen run specification: {manifest_path}") from exc
    values.setdefault("mesh_recipe", SECTOR90_MESH_RECIPE)
    if isinstance(values.get("mesh_sizes_m"), list):
        values["mesh_sizes_m"] = tuple(values["mesh_sizes_m"])
    try:
        return StraightPipeRunSpec(**values)
    except (TypeError, StraightPipeRunError) as exc:
        raise StraightPipeRunError(f"frozen run specification is invalid: {manifest_path}") from exc


def _existing_run_spec(output_dir: Path, requested_spec: StraightPipeRunSpec | None) -> StraightPipeRunSpec:
    """Use a run's frozen spec and reject a caller that would reinterpret it."""

    recorded_spec = _spec_from_run_manifest(output_dir)
    if requested_spec is not None:
        recorded_json = json.dumps(asdict(recorded_spec), sort_keys=True)
        requested_json = json.dumps(asdict(requested_spec), sort_keys=True)
        if requested_json != recorded_json:
            raise StraightPipeRunError(
                "requested mesh/physics specification differs from the existing frozen run manifest"
            )
    return recorded_spec


def _ogrid_core_radius_m(spec: StraightPipeRunSpec) -> float:
    """Return the fixed diamond-core radius for the full-pipe O-grid."""

    return spec.radius_m / 4.0


def _ogrid_mesh_counts(spec: StraightPipeRunSpec, mesh_size_m: float) -> dict[str, int]:
    """Return conforming counts for the fixed-wall full-pipe O-grid.

    The wall-facet count and central diamond mesh are deliberately fixed
    across the three V&V levels.  Axial and annular-radial spacings refine by
    the declared ratio; the fixed pieces are already finer than the coarse
    annular spacing, so this family separates geometry error from the
    axial/radial finite-volume sequence without collapsed-axis cells.
    """

    axial_cells = int(round(spec.length_m / mesh_size_m))
    annular_span = spec.radius_m - _ogrid_core_radius_m(spec)
    annular_radial_cells = int(round(annular_span / mesh_size_m))
    if axial_cells < 4 or annular_radial_cells < 2:
        raise StraightPipeRunError("the full-pipe O-grid needs at least 4 axial and 2 annular-radial cells")
    if not math.isclose(spec.length_m / axial_cells, mesh_size_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise StraightPipeRunError("mesh size must divide the pipe length for the full-pipe O-grid")
    if not math.isclose(
        annular_span / annular_radial_cells,
        mesh_size_m,
        rel_tol=1.0e-12,
        abs_tol=1.0e-15,
    ):
        raise StraightPipeRunError(
            "mesh size must divide the full-pipe O-grid annular span for uniform radial refinement"
        )
    return {
        "axialCells": axial_cells,
        "annularRadialCells": annular_radial_cells,
        "azimuthalCellsPerQuadrant": spec.ogrid_azimuthal_cells_per_quadrant,
        "coreCellsPerSide": spec.ogrid_core_cells_per_side,
    }


def _ogrid_wall_geometry(spec: StraightPipeRunSpec) -> dict[str, float | int]:
    total_azimuthal_cells = 4 * spec.ogrid_azimuthal_cells_per_quadrant
    facet_angle = 2.0 * math.pi / total_azimuthal_cells
    area = 0.5 * total_azimuthal_cells * spec.radius_m**2 * math.sin(facet_angle)
    exact_area = math.pi * spec.radius_m**2
    return {
        "totalAzimuthalCells": total_azimuthal_cells,
        "facetAngleDegrees": math.degrees(facet_angle),
        "crossSectionAreaM2": area,
        "crossSectionAreaRelativeDeficit": 1.0 - area / exact_area,
    }


def _mesh_recipe_metadata(spec: StraightPipeRunSpec, mesh_size_m: float) -> dict[str, Any]:
    if spec.mesh_recipe == SECTOR90_MESH_RECIPE:
        axial_cells, azimuthal_cells, radial_cells = _structured_mesh_counts(spec, mesh_size_m)
        return {
            "meshRecipe": SECTOR90_MESH_RECIPE,
            "meshGenerator": "blockMesh structured 90-degree symmetry pipe sector",
            "fullPipeScale": 4.0,
            "sectorAngleDegrees": 90.0,
            "radialPlaneBoundaryType": "symmetryPlane",
            "radialPlanes": ["side1", "side2"],
            "crossSectionAreaMethod": "faceted inscribed-polygon area from azimuthal cell count",
            "effectiveDiscretizationDimension": 3,
            "characteristicLengthDefinition": "uniform axial, azimuthal, and radial block spacing",
            "azimuthalCells": azimuthal_cells,
            "axialCells": axial_cells,
            "radialCells": radial_cells,
        }
    if spec.mesh_recipe == FULL_PIPE_OGRID_MESH_RECIPE:
        counts = _ogrid_mesh_counts(spec, mesh_size_m)
        wall_geometry = _ogrid_wall_geometry(spec)
        return {
            "meshRecipe": FULL_PIPE_OGRID_MESH_RECIPE,
            "meshGenerator": "blockMesh five-block full-pipe O-grid with fixed wall facets",
            "fullPipeScale": 1.0,
            "sectorAngleDegrees": 360.0,
            "radialPlaneBoundaryType": None,
            "radialPlanes": [],
            "crossSectionAreaMethod": "fixed full-pipe inscribed polygon from O-grid wall facets",
            "effectiveDiscretizationDimension": 2,
            "characteristicLengthDefinition": "uniform axial and annular-radial spacing; fixed resolved core and wall facets",
            "coreRadiusM": _ogrid_core_radius_m(spec),
            **counts,
            **wall_geometry,
        }
    raise StraightPipeRunError(f"unsupported mesh recipe: {spec.mesh_recipe}")


def _mesh_geometry_description(metadata: dict[str, Any]) -> str:
    """Return an explicit, non-promotional description of the mesh geometry."""

    recipe = metadata["meshRecipe"]
    if recipe == FULL_PIPE_OGRID_MESH_RECIPE:
        return (
            "3D five-block full-pipe O-grid with a fixed inscribed polygonal wall "
            f"({metadata['totalAzimuthalCells']} wall facets); no collapsed axis; not CAD-exact geometry"
        )
    if recipe == SECTOR90_MESH_RECIPE:
        return (
            "3D structured 90-degree symmetry sector representing a full circular pipe; "
            "its inscribed-polygon wall resolution changes with each grid level"
        )
    raise StraightPipeRunError(f"unsupported mesh recipe: {recipe}")


def _boundary_condition_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Describe only the boundary topology actually present in a mesh recipe."""

    if metadata["meshRecipe"] == FULL_PIPE_OGRID_MESH_RECIPE:
        return {
            "axial": "cyclic periodic pressure-gradient representation",
            "radialPlanes": None,
            "flowControl": "meanVelocityForce targets the declared Q over the actual fixed faceted mesh area",
            "initialInternalField": "16 annular averages of the fully-developed profile",
            "wall": "no-slip",
            "axis": None,
            "topology": "full pipe; five conforming hexahedral blocks; no collapsed axis",
        }
    return {
        "axial": "cyclic periodic pressure-gradient representation",
        "radialPlanes": "symmetry planes exact for the fully developed axisymmetric solution",
        "flowControl": "meanVelocityForce targets the declared Q over the actual faceted mesh area",
        "initialInternalField": "16 annular averages of the fully-developed profile",
        "wall": "no-slip",
        "axis": "collapsed structured-mesh axis",
        "topology": "90-degree symmetry sector with a collapsed axis",
    }


def _foam_header(*, class_name: str, location: str, object_name: str) -> str:
    return f'''/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Version:  11
     \\/     M anipulation  |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    format      ascii;
    class       {class_name};
    location    "{location}";
    object      {object_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
'''


def _format_scalar(value: float) -> str:
    return f"{value:.17g}"


def _velocity_field(spec: StraightPipeRunSpec, mesh_size_m: float) -> str:
    mean_velocity = _mesh_flow_control(spec, mesh_size_m)["meanVelocityTargetMPerS"]
    return _foam_header(class_name="volVectorField", location="0", object_name="U") + f'''
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ({_format_scalar(mean_velocity)} 0 0);

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    wall
    {{
        type            noSlip;
    }}
}}
'''


def _pressure_field() -> str:
    return _foam_header(class_name="volScalarField", location="0", object_name="p") + '''
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    #includeEtc "caseDicts/setConstraintTypes"

    wall
    {
        type            zeroGradient;
    }
}
'''


def _physical_properties(spec: StraightPipeRunSpec) -> str:
    nu = spec.dynamic_viscosity_pa_s / spec.density_kg_m3
    return _foam_header(
        class_name="dictionary", location="constant", object_name="physicalProperties"
    ) + f'''
viscosityModel  constant;

nu              [0 2 -1 0 0 0 0] {_format_scalar(nu)};
'''


def _momentum_transport() -> str:
    return _foam_header(
        class_name="dictionary", location="constant", object_name="momentumTransport"
    ) + '''
simulationType  laminar;
'''


def _fv_constraints(spec: StraightPipeRunSpec, mesh_size_m: float) -> str:
    mean_velocity = _mesh_flow_control(spec, mesh_size_m)["meanVelocityTargetMPerS"]
    # ``fvConstraints`` is a run-time system dictionary in OpenFOAM v11.
    # Placing it in ``constant`` is silently ignored by ``foamRun``, which
    # would let a periodic pipe decay to rest instead of applying the
    # flow-rate controller.
    return _foam_header(class_name="dictionary", location="system", object_name="fvConstraints") + f'''
momentumForce
{{
    type            meanVelocityForce;

    select          all;

    Ubar            ({_format_scalar(mean_velocity)} 0 0);
    relaxation      1;
}}
'''


def _structured_mesh_counts(spec: StraightPipeRunSpec, mesh_size_m: float) -> tuple[int, int, int]:
    """Return reproducible axial, azimuthal, and radial cells for a 90-degree wedge.

    The two symmetry planes represent an axisymmetric full circular pipe while
    allowing a structured hexahedral mesh.  Each refinement doubles every
    logical mesh direction, preserving the uniform factor used by the GCI
    calculation.
    """

    radial_cells = int(round(spec.radius_m / mesh_size_m))
    axial_cells = int(round(spec.length_m / mesh_size_m))
    if radial_cells < 2 or axial_cells < 4:
        raise StraightPipeRunError("the structured reference mesh needs at least 2 radial and 4 axial cells")
    if not math.isclose(spec.radius_m / radial_cells, mesh_size_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise StraightPipeRunError("mesh size must divide the pipe radius for the structured reference mesh")
    if not math.isclose(spec.length_m / axial_cells, mesh_size_m, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise StraightPipeRunError("mesh size must divide the pipe length for the structured reference mesh")
    return axial_cells, radial_cells, radial_cells


def _mesh_flow_control(spec: StraightPipeRunSpec, mesh_size_m: float) -> dict[str, Any]:
    """Return the imposed flow data for the faceted 90-degree mesh geometry.

    ``blockMesh`` represents each circular arc by azimuthal face segments.  Its
    actual sector area is therefore the inscribed-polygon area, not exactly a
    quarter of ``pi*R**2``.  Targeting the analytical circular-pipe mean
    velocity would silently change the specified volumetric flow rate on each
    grid.  We instead impose one quarter of the declared Q over the actual
    mesh area and retain this geometry approximation as part of the spatial
    refinement error.
    """

    metadata = _mesh_recipe_metadata(spec, mesh_size_m)
    if spec.mesh_recipe == FULL_PIPE_OGRID_MESH_RECIPE:
        sector_area = float(metadata["crossSectionAreaM2"])
        return {
            **metadata,
            "sectorAreaM2": sector_area,
            "targetSectorVolumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
            "targetFullPipeVolumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
            "meanVelocityTargetMPerS": spec.volumetric_flow_rate_m3_s / sector_area,
            "geometryAreaRelativeDeficit": metadata["crossSectionAreaRelativeDeficit"],
        }

    _, azimuthal_cells, _ = _structured_mesh_counts(spec, mesh_size_m)
    sector_angle_rad = math.pi / 2.0
    sector_area = 0.5 * azimuthal_cells * spec.radius_m**2 * math.sin(
        sector_angle_rad / azimuthal_cells
    )
    sector_flow = spec.volumetric_flow_rate_m3_s / 4.0
    mean_velocity = sector_flow / sector_area
    return {
        **metadata,
        "sectorAreaM2": sector_area,
        "targetSectorVolumetricFlowRateM3PerS": sector_flow,
        "targetFullPipeVolumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
        "meanVelocityTargetMPerS": mean_velocity,
        "geometryAreaRelativeDeficit": 1.0
        - (4.0 * sector_area) / (math.pi * spec.radius_m**2),
    }


def _block_mesh_dict(spec: StraightPipeRunSpec, mesh_size_m: float) -> str:
    if spec.mesh_recipe == FULL_PIPE_OGRID_MESH_RECIPE:
        return _full_pipe_ogrid_block_mesh_dict(spec, mesh_size_m)
    if spec.mesh_recipe != SECTOR90_MESH_RECIPE:
        raise StraightPipeRunError(f"unsupported mesh recipe: {spec.mesh_recipe}")
    axial_cells, azimuthal_cells, radial_cells = _structured_mesh_counts(spec, mesh_size_m)
    edge_coordinate = spec.radius_m / math.sqrt(2.0)
    return _foam_header(class_name="dictionary", location="system", object_name="blockMeshDict") + f'''
convertToMeters 1;

vertices
(
    (0 0 0)
    ({_format_scalar(spec.length_m)} 0 0)
    ({_format_scalar(spec.length_m)} 0 0)
    (0 0 0)
    (0 -{_format_scalar(edge_coordinate)} {_format_scalar(edge_coordinate)})
    ({_format_scalar(spec.length_m)} -{_format_scalar(edge_coordinate)} {_format_scalar(edge_coordinate)})
    ({_format_scalar(spec.length_m)} {_format_scalar(edge_coordinate)} {_format_scalar(edge_coordinate)})
    (0 {_format_scalar(edge_coordinate)} {_format_scalar(edge_coordinate)})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({axial_cells} {azimuthal_cells} {radial_cells}) simpleGrading (1 1 1)
);

edges
(
    arc 4 7 (0 0 {_format_scalar(spec.radius_m)})
    arc 5 6 ({_format_scalar(spec.length_m)} 0 {_format_scalar(spec.radius_m)})
);

boundary
(
    inlet
    {{
        type cyclic;
        neighbourPatch outlet;
        faces
        (
            (0 4 7 3)
        );
    }}
    outlet
    {{
        type cyclic;
        neighbourPatch inlet;
        faces
        (
            (1 2 6 5)
        );
    }}
    side1
    {{
        type symmetryPlane;
        faces
        (
            (0 1 5 4)
        );
    }}
    side2
    {{
        type symmetryPlane;
        faces
        (
            (7 6 2 3)
        );
    }}
    wall
    {{
        type wall;
        faces
        (
            (4 5 6 7)
        );
    }}
    axis
    {{
        type symmetryPlane;
        faces
        (
            (3 2 1 0)
        );
    }}
);

mergePatchPairs
(
);
'''


def _full_pipe_ogrid_block_mesh_dict(spec: StraightPipeRunSpec, mesh_size_m: float) -> str:
    """Create a conforming five-block full-pipe O-grid without an axis collapse.

    The central diamond and the outer wall-facet count are fixed; only the
    axial and annular-radial directions refine.  This gives a stable polygonal
    wall representation across the three-grid GCI study and avoids the
    under-determined cells produced by a very narrow collapsed wedge.
    """

    counts = _ogrid_mesh_counts(spec, mesh_size_m)
    axial_cells = counts["axialCells"]
    annular_radial_cells = counts["annularRadialCells"]
    azimuthal_cells = counts["azimuthalCellsPerQuadrant"]
    core_cells = counts["coreCellsPerSide"]
    core_radius = _ogrid_core_radius_m(spec)
    diagonal_coordinate = spec.radius_m / math.sqrt(2.0)
    length = _format_scalar(spec.length_m)
    core = _format_scalar(core_radius)
    radius = _format_scalar(spec.radius_m)
    diagonal = _format_scalar(diagonal_coordinate)
    return _foam_header(class_name="dictionary", location="system", object_name="blockMeshDict") + f'''
convertToMeters 1;

vertices
(
    (0 {core} 0)
    (0 0 {core})
    (0 -{core} 0)
    (0 0 -{core})
    (0 {radius} 0)
    (0 0 {radius})
    (0 -{radius} 0)
    (0 0 -{radius})
    ({length} {core} 0)
    ({length} 0 {core})
    ({length} -{core} 0)
    ({length} 0 -{core})
    ({length} {radius} 0)
    ({length} 0 {radius})
    ({length} -{radius} 0)
    ({length} 0 -{radius})
);

blocks
(
    hex (0 8 9 1 3 11 10 2) ({axial_cells} {core_cells} {core_cells}) simpleGrading (1 1 1)
    hex (0 8 12 4 1 9 13 5) ({axial_cells} {annular_radial_cells} {azimuthal_cells}) simpleGrading (1 1 1)
    hex (1 9 13 5 2 10 14 6) ({axial_cells} {annular_radial_cells} {azimuthal_cells}) simpleGrading (1 1 1)
    hex (2 10 14 6 3 11 15 7) ({axial_cells} {annular_radial_cells} {azimuthal_cells}) simpleGrading (1 1 1)
    hex (3 11 15 7 0 8 12 4) ({axial_cells} {annular_radial_cells} {azimuthal_cells}) simpleGrading (1 1 1)
);

edges
(
    arc 4 5 (0 {diagonal} {diagonal})
    arc 5 6 (0 -{diagonal} {diagonal})
    arc 6 7 (0 -{diagonal} -{diagonal})
    arc 7 4 (0 {diagonal} -{diagonal})
    arc 12 13 ({length} {diagonal} {diagonal})
    arc 13 14 ({length} -{diagonal} {diagonal})
    arc 14 15 ({length} -{diagonal} -{diagonal})
    arc 15 12 ({length} {diagonal} -{diagonal})
);

boundary
(
    inlet
    {{
        type cyclic;
        neighbourPatch outlet;
        faces
        (
            (0 3 2 1)
            (0 1 5 4)
            (1 2 6 5)
            (2 3 7 6)
            (3 0 4 7)
        );
    }}
    outlet
    {{
        type cyclic;
        neighbourPatch inlet;
        faces
        (
            (8 9 10 11)
            (8 12 13 9)
            (9 13 14 10)
            (10 14 15 11)
            (11 15 12 8)
        );
    }}
    wall
    {{
        type wall;
        faces
        (
            (4 12 13 5)
            (5 13 14 6)
            (6 14 15 7)
            (7 15 12 4)
        );
    }}
);

mergePatchPairs
(
);
'''


def _control_dict() -> str:
    return _foam_header(class_name="dictionary", location="system", object_name="controlDict") + '''
application     foamRun;

solver          incompressibleFluid;

startFrom       startTime;

startTime       0;

stopAt          endTime;

endTime         1500;

deltaT          1;

writeControl    timeStep;

writeInterval   100;

purgeWrite      0;

writeFormat     ascii;

writePrecision  12;

writeCompression off;

timeFormat      general;

timePrecision   12;

runTimeModifiable false;

functions
{
    inletFlux
    {
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        writeFields     false;
        select          patch;
        patch           inlet;
        operation       sum;
        fields          (phi);
    }
    outletFlux
    {
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        writeFields     false;
        select          patch;
        patch           outlet;
        operation       sum;
        fields          (phi);
    }
    transverseMomentumResiduals
    {
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        fields          (U);
    }
    wallShearStress
    {
        type            wallShearStress;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   100;
        patches         (wall);
    }
    wallForces
    {
        type            forces;
        libs            ("libforces.so");
        writeControl    timeStep;
        writeInterval   100;
        patches         (wall);
        rho             rhoInf;
        rhoInf          1000;
        CofR            (0 0 0);
        p               p;
        U               U;
    }
}
'''


def _fv_schemes() -> str:
    return _foam_header(class_name="dictionary", location="system", object_name="fvSchemes") + '''
ddtSchemes
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
'''


def _fv_solution() -> str:
    return _foam_header(class_name="dictionary", location="system", object_name="fvSolution") + '''
solvers
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
    consistent          yes;
    pRefCell            0;
    pRefValue           0;
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
'''


def _set_fields_dict(
    spec: StraightPipeRunSpec,
    mesh_size_m: float,
    *,
    radial_bands: int = 16,
) -> str:
    """Approximate the analytical profile in every cell before steady iteration.

    Nested ``cylinderToCell`` regions initialize annular averages of the exact
    profile.  This improves nonlinear convergence without prescribing the
    pressure drop that remains the measured quantity of interest.
    """

    if radial_bands < 2:
        raise StraightPipeRunError("at least two radial initialization bands are required")
    mean_velocity = _mesh_flow_control(spec, mesh_size_m)["meanVelocityTargetMPerS"]
    regions: list[str] = []
    for band in range(radial_bands, 0, -1):
        lower = spec.radius_m * (band - 1) / radial_bands
        upper = spec.radius_m * band / radial_bands
        annular_average = 2.0 * mean_velocity * (
            1.0 - (lower * lower + upper * upper) / (2.0 * spec.radius_m * spec.radius_m)
        )
        regions.append(
            f'''    cylinderToCell
    {{
        p1              (0 0 0);
        p2              ({_format_scalar(spec.length_m)} 0 0);
        radius          {_format_scalar(upper)};
        fieldValues
        (
            volVectorFieldValue U ({_format_scalar(annular_average)} 0 0)
        );
    }}'''
        )
    return _foam_header(
        class_name="dictionary", location="system", object_name="setFieldsDict"
    ) + """
defaultFieldValues
(
    volVectorFieldValue U (0 0 0)
);

regions
(
""" + "\n".join(regions) + "\n);\n"


def _run_level_script() -> str:
    return '''#!/usr/bin/env bash
source /opt/openfoam11/etc/bashrc
set -euo pipefail

blockMesh > log.blockMesh 2>&1
checkMesh -allGeometry -allTopology > log.checkMesh 2>&1
grep -q "Mesh OK" log.checkMesh
setFields > log.setFields 2>&1
mkdir -p runtime
if [ -x /usr/bin/time ]; then
    /usr/bin/time -v -o runtime/solver-resources.txt \
        foamRun -solver incompressibleFluid > log.foamRun 2>&1
else
    foamRun -solver incompressibleFluid > log.foamRun 2>&1
fi
grep -q "End" log.foamRun
foamToVTK -ascii -latestTime > log.foamToVTK 2>&1
'''


def _serial_manifest(
    spec: StraightPipeRunSpec,
    mesh_size_m: float,
    level_id: str,
    *,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    runner_source_sha256: str | None = None,
) -> dict[str, Any]:
    reference = spec.reference()
    flow_control = _mesh_flow_control(spec, mesh_size_m)
    mesh_metadata = _mesh_recipe_metadata(spec, mesh_size_m)
    return {
        "schema": RUNNER_SCHEMA,
        "caseId": "straight-pipe",
        "levelId": level_id,
        "runMode": "serial",
        "scientificStatus": "analysis-only",
        "validated": False,
        "geometry": {
            "kind": "straight-circular-pipe",
            "dimension": 3,
            "lengthM": spec.length_m,
            "radiusM": spec.radius_m,
            "meshCharacteristicLengthM": mesh_size_m,
            "meshRecipe": mesh_metadata["meshRecipe"],
            "meshGenerator": mesh_metadata["meshGenerator"],
            "meshGeometry": _mesh_geometry_description(mesh_metadata),
            "meshRepresentation": mesh_metadata,
        },
        "fluid": {
            "densityKgPerM3": spec.density_kg_m3,
            "dynamicViscosityPaS": spec.dynamic_viscosity_pa_s,
            "volumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
        },
        "reference": {
            "model": "Hagen-Poiseuille",
            "reynoldsNumber": reference["reynoldsNumber"],
            "pressureDropPa": reference["pressureDropPa"],
            "meanVelocityMPerS": reference["meanVelocityMPerS"],
        },
        "flowControl": flow_control,
        "boundaryCondition": _boundary_condition_metadata(mesh_metadata),
        "numerics": {
            "solver": "foamRun -solver incompressibleFluid",
            "timeTreatment": "direct steady SIMPLE",
            "convergence": {
                "method": "periodic-controller-tail-stability",
                "tailSamples": _PERIODIC_CONVERGENCE_TAIL_SAMPLES,
                "meanVelocityRelativeTolerance": _PERIODIC_MEAN_VELOCITY_RELATIVE_TOLERANCE,
                "pressureGradientTailRelativeSpanTolerance": _PERIODIC_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE,
                "globalContinuityTolerance": _PERIODIC_GLOBAL_CONTINUITY_TOLERANCE,
                "linearFinalResidualTolerance": _PERIODIC_LINEAR_FINAL_RESIDUAL_TOLERANCE,
                "rationale": (
                    "Periodic kinematic pressure has a gauge null space, so generic SIMPLE "
                    "normalized initial residual control is retained in the log but is not "
                    "the numerical acceptance metric."
                ),
            },
        },
        "runtime": {
            "containerImage": image,
            "containerPlatform": platform,
            "hostTimingCaveat": "linux/amd64 image may be emulated on non-amd64 hosts",
        },
        "provenance": {
            "runnerSourceSha256": runner_source_sha256 or _runner_source_sha256(),
            "runnerSourceHashScope": "runner source snapshot at case materialization",
        },
    }


def _write_text(path: Path, value: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def materialize_serial_case(
    case_dir: Path,
    *,
    spec: StraightPipeRunSpec,
    mesh_size_m: float,
    level_id: str,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    runner_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Write one reproducible serial case without running a solver."""

    case_dir = case_dir.resolve()
    if case_dir.exists() and any(case_dir.iterdir()):
        raise StraightPipeRunError(f"refusing to overwrite non-empty case directory: {case_dir}")
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_text(case_dir / "0" / "U", _velocity_field(spec, mesh_size_m))
    _write_text(case_dir / "0" / "p", _pressure_field())
    _write_text(case_dir / "constant" / "physicalProperties", _physical_properties(spec))
    _write_text(case_dir / "constant" / "momentumTransport", _momentum_transport())
    _write_text(case_dir / "system" / "fvConstraints", _fv_constraints(spec, mesh_size_m))
    _write_text(case_dir / "system" / "controlDict", _control_dict())
    _write_text(case_dir / "system" / "blockMeshDict", _block_mesh_dict(spec, mesh_size_m))
    _write_text(case_dir / "system" / "fvSchemes", _fv_schemes())
    _write_text(case_dir / "system" / "fvSolution", _fv_solution())
    _write_text(case_dir / "system" / "setFieldsDict", _set_fields_dict(spec, mesh_size_m))
    _write_text(case_dir / "run_level.sh", _run_level_script(), executable=True)
    manifest = _serial_manifest(
        spec,
        mesh_size_m,
        level_id,
        image=image,
        platform=platform,
        runner_source_sha256=runner_source_sha256,
    )
    _write_json(case_dir / "case-manifest.json", manifest)
    return manifest


def materialize_serial_cases(
    output_dir: Path,
    spec: StraightPipeRunSpec | None = None,
    *,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
) -> list[Path]:
    """Create the frozen three-level serial suite and its top-level manifest."""

    selected_spec = spec or default_run_spec()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise StraightPipeRunError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    runner_source_path = Path(__file__).resolve()
    runner_source_sha256 = _sha256_file(runner_source_path)
    runner_source_snapshot = output_dir / "runner-source-snapshot.py"
    _write_text(runner_source_snapshot, runner_source_path.read_text(encoding="utf-8"))
    if _sha256_file(runner_source_snapshot) != runner_source_sha256:
        raise StraightPipeRunError("runner source snapshot hash does not match the materialized source")
    case_dirs: list[Path] = []
    for index, mesh_size_m in enumerate(selected_spec.mesh_sizes_m, start=1):
        level_id = ("coarse", "medium", "fine")[index - 1]
        case_dir = output_dir / "serial" / level_id
        materialize_serial_case(
            case_dir,
            spec=selected_spec,
            mesh_size_m=mesh_size_m,
            level_id=level_id,
            image=image,
            platform=platform,
            runner_source_sha256=runner_source_sha256,
        )
        case_dirs.append(case_dir)
    _write_json(
        output_dir / "run-manifest.json",
        {
            "schema": RUNNER_SCHEMA,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "caseId": "straight-pipe",
            "status": "materialized-not-run",
            "scientificStatus": "analysis-only",
            "validated": False,
            "runtime": {
                "containerImage": image,
                "containerPlatform": platform,
            },
            "provenance": {
                "runnerSourceSha256": runner_source_sha256,
                "runnerSourceSnapshot": runner_source_snapshot.relative_to(output_dir).as_posix(),
            },
            "spec": asdict(selected_spec),
            "serialLevels": [path.relative_to(output_dir).as_posix() for path in case_dirs],
        },
    )
    return case_dirs


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runner_source_sha256() -> str:
    """Return the source fingerprint that produced a captured runtime record."""

    return _sha256_file(Path(__file__).resolve())


def _case_runner_source_provenance(case_dir: Path) -> dict[str, str]:
    """Bind execution records to the source snapshot frozen in the case input.

    Reading ``__file__`` at the end of a long solve is not a reliable measure
    of the code actually loaded by its parent Python process: a developer may
    edit the file while the solver is running. New materialized cases therefore
    carry the source hash, and legacy cases transparently fall back to the
    current file with an explicit scope label.
    """

    manifest_path = case_dir / "case-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provenance = manifest.get("provenance", {})
        source_hash = provenance.get("runnerSourceSha256")
    except (OSError, TypeError, json.JSONDecodeError):
        source_hash = None
    if isinstance(source_hash, str) and _SHA256_RE.fullmatch(source_hash):
        return {
            "runnerSourceSha256": source_hash,
            "runnerSourceHashScope": "case-manifest source snapshot at materialization",
        }
    return {
        "runnerSourceSha256": _runner_source_sha256(),
        "runnerSourceHashScope": "live source fallback for legacy case without source snapshot",
    }


def _container_image_provenance(image: str) -> dict[str, Any]:
    """Capture Docker's immutable local image identifiers after a successful run.

    A tag alone is mutable.  We retain it for usability, but bind future
    evidence to the Docker image ID and any repository digests Docker exposes.
    Inspection failure is represented explicitly rather than silently omitted.
    """

    command = ["docker", "image", "inspect", image]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {
            "requestedImage": image,
            "captureStatus": "unavailable",
            "command": command,
            "error": completed.stderr.strip() or completed.stdout.strip() or "docker image inspect failed",
        }
    try:
        payload = json.loads(completed.stdout)
        record = payload[0]
        image_id = record["Id"]
        if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
            raise ValueError("Docker image inspect returned no SHA-256 image ID")
        repo_digests = record.get("RepoDigests") or []
        if not isinstance(repo_digests, list) or not all(isinstance(value, str) for value in repo_digests):
            raise ValueError("Docker image inspect returned malformed repository digests")
        return {
            "requestedImage": image,
            "captureStatus": "captured",
            "imageId": image_id,
            "repoDigests": repo_digests,
            "os": record.get("Os"),
            "architecture": record.get("Architecture"),
        }
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "requestedImage": image,
            "captureStatus": "unavailable",
            "command": command,
            "error": f"could not parse docker image inspect output: {exc}",
        }


def _docker_json(command: Sequence[str], *, label: str) -> dict[str, Any]:
    """Run one fixed Docker introspection command and parse its JSON output."""

    completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise StraightPipeRunError(f"could not collect {label}: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StraightPipeRunError(f"could not parse {label} as JSON") from exc
    if not isinstance(value, dict):
        raise StraightPipeRunError(f"could not parse {label}: expected JSON object")
    return value


def _docker_timing_engine_facts() -> dict[str, Any]:
    """Capture the architecture and CPU-allocation context needed for timing."""

    version = _docker_json(
        ["docker", "version", "--format", "{{json .Server}}"], label="Docker server version"
    )
    info = _docker_json(["docker", "info", "--format", "{{json .}}"], label="Docker server info")
    context = subprocess.run(
        ["docker", "context", "show"], capture_output=True, text=True, check=False
    )
    return {
        "dockerContext": context.stdout.strip() if context.returncode == 0 else None,
        "dockerServerVersion": version.get("Version"),
        "engineOs": version.get("Os"),
        "engineArchitecture": version.get("Arch"),
        "engineOperatingSystem": info.get("OperatingSystem"),
        "engineName": info.get("Name"),
        "engineLogicalCpuCount": info.get("NCPU"),
        "engineMemoryBytes": info.get("MemTotal"),
    }


def _native_timing_preflight_from_facts(
    *,
    image: str,
    platform: str,
    image_provenance: dict[str, Any],
    engine_facts: dict[str, Any],
    cpu_allocations: dict[int, dict[str, Any]],
    cpu_set_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a conservative native-timing decision from captured Docker facts."""

    try:
        compatibility = native_compatibility_decision(
            requested_platform=platform,
            engine_os=engine_facts.get("engineOs"),
            engine_architecture=engine_facts.get("engineArchitecture"),
            image_os=image_provenance.get("os"),
            image_architecture=image_provenance.get("architecture"),
        )
    except PerformanceProtocolError as exc:
        raise StraightPipeRunError(f"native timing compatibility check failed: {exc}") from exc
    probe_passed = cpu_set_probe is not None and cpu_set_probe.get("passed") is True
    try:
        cpu_scope = classify_cpu_set_scope(
            engine_platform_name=engine_facts.get("engineName"),
            engine_operating_system=engine_facts.get("engineOperatingSystem"),
            engine_cpu_set_supported=probe_passed if cpu_set_probe is not None else None,
        )
    except PerformanceProtocolError as exc:
        raise StraightPipeRunError(f"CPU-set scope classification failed: {exc}") from exc

    failure_reasons: list[str] = []
    if image_provenance.get("captureStatus") != "captured":
        failure_reasons.append("Docker image provenance could not be captured")
    if compatibility["nativeCompatible"] is not True:
        failure_reasons.extend(compatibility["reasons"])
    if cpu_set_probe is not None and not probe_passed:
        failure_reasons.append(str(cpu_set_probe.get("error") or "Docker CPU-set probe failed"))
    return {
        "schema": REPLICATED_TIMING_SCHEMA,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "requestedImage": image,
        "requestedPlatform": platform,
        "engine": engine_facts,
        "image": image_provenance,
        "nativeCompatibility": compatibility,
        "cpuAllocations": {str(ranks): allocation for ranks, allocation in cpu_allocations.items()},
        "cpuSetScope": cpu_scope,
        "cpuSetProbe": cpu_set_probe,
        "nativeTimingPermitted": not failure_reasons,
        "failureReasons": failure_reasons,
        "executionQualification": {
            "architectureNative": compatibility["nativeCompatible"] is True,
            "executionLayer": cpu_scope["executionLayer"],
            "physicalCorePinningClaimed": False,
            "performanceClaim": (
                "architecture-native container timing only; not a bare-metal or physical-core claim"
                if not failure_reasons
                else "no performance timing is permitted by this environment preflight"
            ),
        },
    }


def _docker_cpu_set_probe(
    *, image: str, platform: str, cpu_set: str
) -> dict[str, Any]:
    """Verify Docker accepts an explicit CPU mask before expensive timing work."""

    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        platform,
        "--cpuset-cpus",
        cpu_set,
        "--entrypoint",
        "/bin/bash",
        image,
        "-lc",
        "grep '^Cpus_allowed_list:' /proc/self/status",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    allowed = None
    match = re.search(r"^Cpus_allowed_list:\s*(\S+)\s*$", completed.stdout, flags=re.MULTILINE)
    if match is not None:
        allowed = match.group(1)
    return {
        "command": command,
        "requestedCpuSet": cpu_set,
        "reportedCpuSet": allowed,
        "returnCode": completed.returncode,
        "passed": completed.returncode == 0 and allowed is not None,
        "error": None
        if completed.returncode == 0 and allowed is not None
        else completed.stderr.strip() or completed.stdout.strip() or "Docker CPU-set probe failed",
    }


def _capture_native_timing_preflight(
    *, image: str, platform: str, cpu_allocations: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Collect local Docker facts; do not start a solver unless this passes."""

    engine_facts = _docker_timing_engine_facts()
    image_provenance = _container_image_provenance(image)
    preliminary = _native_timing_preflight_from_facts(
        image=image,
        platform=platform,
        image_provenance=image_provenance,
        engine_facts=engine_facts,
        cpu_allocations=cpu_allocations,
    )
    probe = None
    if preliminary["nativeTimingPermitted"]:
        probe = _docker_cpu_set_probe(
            image=image,
            platform=platform,
            cpu_set=cpu_allocations[1]["requestedCpuSet"],
        )
    return _native_timing_preflight_from_facts(
        image=image,
        platform=platform,
        image_provenance=image_provenance,
        engine_facts=engine_facts,
        cpu_allocations=cpu_allocations,
        cpu_set_probe=probe,
    )


def _artifact_index_integrity(root: Path, index_path: Path) -> dict[str, Any]:
    """Recompute a serial artifact index without trusting its stored hashes."""

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        records = index["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StraightPipeRunError(f"could not read artifact index {index_path}: {exc}") from exc
    if not isinstance(records, list) or not records:
        raise StraightPipeRunError(f"artifact index has no records: {index_path}")

    checked: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise StraightPipeRunError(f"artifact index contains malformed record: {index_path}")
        relative = record.get("path")
        expected = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str) or _SHA256_RE.fullmatch(expected) is None:
            raise StraightPipeRunError(f"artifact index contains invalid path or SHA-256: {index_path}")
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise StraightPipeRunError(f"artifact index path escapes run root: {relative}") from exc
        actual = _sha256_file(path) if path.is_file() else None
        checked.append(
            {
                "kind": record.get("kind"),
                "path": relative,
                "expectedSha256": expected,
                "actualSha256": actual,
                "passed": actual == expected,
            }
        )
    return {
        "artifactIndex": index_path.relative_to(root).as_posix(),
        "allValid": all(item["passed"] for item in checked),
        "files": checked,
    }


def _require_serial_artifact_integrity(output_dir: Path) -> dict[str, Any]:
    index_path = output_dir / "artifacts" / "artifact-index.json"
    if not index_path.is_file():
        raise StraightPipeRunError(f"serial artifact index is missing: {index_path}")
    integrity = _artifact_index_integrity(output_dir, index_path)
    if not integrity["allValid"]:
        failed = [item["path"] for item in integrity["files"] if not item["passed"]]
        raise StraightPipeRunError(
            "serial artifact hashes changed after gating; refuse parallel execution: " + ", ".join(failed)
        )
    return integrity


def _last_function_value(case_dir: Path, function_name: str) -> float:
    candidates = sorted((case_dir / "postProcessing" / function_name).glob("**/surfaceFieldValue.dat"))
    if not candidates:
        raise StraightPipeRunError(f"missing surfaceFieldValue output for {function_name} in {case_dir}")
    for line in reversed(candidates[-1].read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        try:
            return float(tokens[-1])
        except (IndexError, ValueError):
            continue
    raise StraightPipeRunError(f"no numeric QoI value found for {function_name} in {case_dir}")


def _cell_count_from_mesh_report(case_dir: Path) -> int:
    report = (case_dir / "log.checkMesh").read_text(encoding="utf-8")
    matched = re.search(r"^\s*cells:\s+(\d+)\s*$", report, flags=re.MULTILINE)
    if matched is None:
        raise StraightPipeRunError(f"could not read cell count from {case_dir / 'log.checkMesh'}")
    return int(matched.group(1))


def _solver_execution_times(case_dir: Path) -> dict[str, float]:
    log = (case_dir / "log.foamRun").read_text(encoding="utf-8")
    matches = re.findall(
        r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s+s\s+ClockTime\s*=\s*([0-9.eE+-]+)\s+s",
        log,
    )
    if not matches:
        raise StraightPipeRunError(f"could not read solver execution time from {case_dir / 'log.foamRun'}")
    execution_time, clock_time = matches[-1]
    return {
        "solverExecutionTimeSeconds": float(execution_time),
        "solverClockTimeSeconds": float(clock_time),
    }


def _solver_iteration_count(case_dir: Path) -> int:
    """Return the number of completed outer SIMPLE time steps in the solver log."""

    log = (case_dir / "log.foamRun").read_text(encoding="utf-8")
    count = len(re.findall(r"^Time = [0-9.eE+-]+s$", log, flags=re.MULTILINE))
    if count <= 0:
        raise StraightPipeRunError(f"could not read solver iteration count from {case_dir / 'log.foamRun'}")
    return count


def _solver_resource_usage(case_dir: Path, *, ranks: int) -> dict[str, Any]:
    """Read GNU-time peak RSS when available without overstating MPI memory."""

    resource_path = case_dir / "runtime" / "solver-resources.txt"
    if not resource_path.is_file():
        return {
            "captureStatus": "unavailable",
            "peakResidentMemoryBytes": None,
            "scope": "not captured; no memory claim is available",
        }
    content = resource_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", content)
    if match is None:
        return {
            "captureStatus": "unavailable",
            "peakResidentMemoryBytes": None,
            "scope": "resource wrapper ran but did not expose GNU time peak RSS",
        }
    return {
        "captureStatus": "captured",
        "peakResidentMemoryBytes": int(match.group(1)) * 1024,
        "scope": (
            "serial solver process peak RSS"
            if ranks == 1
            else "MPI launcher process peak RSS; not an aggregate across ranks"
        ),
    }


def _parallel_cells_per_rank(case_dir: Path, *, ranks: int) -> list[int]:
    """Read the actual decomposePar cell counts, retaining decomposition imbalance."""

    if ranks == 1:
        return [_cell_count_from_mesh_report(case_dir)]
    log_path = case_dir / "log.decomposePar"
    content = log_path.read_text(encoding="utf-8")
    matches = re.findall(
        r"^Processor\s+(\d+)\s*\n\s*Number of cells =\s*(\d+)\s*$",
        content,
        flags=re.MULTILINE,
    )
    values = {int(processor): int(cells) for processor, cells in matches}
    if sorted(values) != list(range(ranks)):
        raise StraightPipeRunError(f"could not read every rank's cell count from {log_path}")
    return [values[rank] for rank in range(ranks)]


def _runtime_workload_metrics(case_dir: Path, *, ranks: int) -> dict[str, Any]:
    cells_per_rank = _parallel_cells_per_rank(case_dir, ranks=ranks)
    return {
        "meshCellCount": _cell_count_from_mesh_report(case_dir),
        "rankCount": ranks,
        "cellsPerRank": cells_per_rank,
        "solverOuterIterationCount": _solver_iteration_count(case_dir),
    }


def _mean_velocity_force_history(case_dir: Path) -> list[tuple[float, float]]:
    log = (case_dir / "log.foamRun").read_text(encoding="utf-8")
    matches = re.findall(
        r"Pressure gradient source:\s+uncorrected Ubar\s*=\s*([0-9.eE+-]+),\s+"
        r"pressure gradient\s*=\s*([0-9.eE+-]+)",
        log,
    )
    if not matches:
        raise StraightPipeRunError(
            f"could not read meanVelocityForce history from {case_dir / 'log.foamRun'}"
        )
    return [(float(mean_velocity), float(gradient)) for mean_velocity, gradient in matches]


def _periodic_numerical_convergence(case_dir: Path) -> dict[str, Any]:
    """Evaluate predeclared physical steady-state criteria for a periodic pipe.

    The periodic pressure field has a gauge null space.  In this case OpenFOAM
    can retain a large *normalized initial* pressure residual after the
    physical p and transverse velocity fields are already O(1e-13).  We retain
    the full solver log, but require controller, flux/continuity, and inner
    linear-solver stability instead of treating that normalized null-space
    diagnostic as an actual physical non-convergence.
    """

    log_path = case_dir / "log.foamRun"
    log = log_path.read_text(encoding="utf-8")
    manifest = json.loads((case_dir / "case-manifest.json").read_text(encoding="utf-8"))
    try:
        target_mean_velocity = float(manifest["flowControl"]["meanVelocityTargetMPerS"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StraightPipeRunError(
            f"missing mesh-specific mean-velocity target in {case_dir / 'case-manifest.json'}"
        ) from exc

    force_history = _mean_velocity_force_history(case_dir)
    if len(force_history) < _PERIODIC_CONVERGENCE_TAIL_SAMPLES:
        raise StraightPipeRunError(
            f"periodic force history has fewer than {_PERIODIC_CONVERGENCE_TAIL_SAMPLES} samples: {log_path}"
        )
    continuity = [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"time step continuity errors : sum local = ([0-9.eE+-]+), global = "
            r"([0-9.eE+-]+), cumulative = ([0-9.eE+-]+)",
            log,
        )
    ]
    if len(continuity) < _PERIODIC_CONVERGENCE_TAIL_SAMPLES:
        raise StraightPipeRunError(
            f"periodic continuity history has fewer than {_PERIODIC_CONVERGENCE_TAIL_SAMPLES} samples: {log_path}"
        )
    linear_residuals = [
        float(value)
        for value in re.findall(
            r"(?:smoothSolver|GAMG):\s+Solving for (?:Ux|Uy|Uz|p),\s+"
            r"Initial residual = [0-9.eE+-]+,\s+Final residual = ([0-9.eE+-]+)",
            log,
        )
    ]
    if len(linear_residuals) < _PERIODIC_CONVERGENCE_TAIL_SAMPLES:
        raise StraightPipeRunError(
            f"periodic linear-solver history has fewer than {_PERIODIC_CONVERGENCE_TAIL_SAMPLES} samples: {log_path}"
        )

    tail_forces = force_history[-_PERIODIC_CONVERGENCE_TAIL_SAMPLES:]
    tail_gradients = [gradient for _, gradient in tail_forces]
    final_mean_velocity, final_gradient = tail_forces[-1]
    if not all(math.isfinite(value) for value in (final_mean_velocity, final_gradient, *tail_gradients)):
        raise StraightPipeRunError(f"non-finite periodic controller history: {log_path}")
    mean_velocity_relative_error = abs(final_mean_velocity - target_mean_velocity) / abs(
        target_mean_velocity
    )
    gradient_tail_relative_span = (max(tail_gradients) - min(tail_gradients)) / max(
        abs(final_gradient), 1.0e-30
    )
    max_abs_global_continuity = max(abs(global_error) for _, global_error, _ in continuity[-50:])
    max_linear_final_residual = max(linear_residuals[-200:])
    criteria = {
        "normalTermination": "FOAM FATAL ERROR" not in log and "End" in log,
        "meanVelocityTarget": mean_velocity_relative_error <= _PERIODIC_MEAN_VELOCITY_RELATIVE_TOLERANCE,
        "pressureGradientTailStability": (
            gradient_tail_relative_span <= _PERIODIC_GRADIENT_TAIL_RELATIVE_SPAN_TOLERANCE
        ),
        "globalContinuity": max_abs_global_continuity <= _PERIODIC_GLOBAL_CONTINUITY_TOLERANCE,
        "linearSolverFinalResidual": (
            max_linear_final_residual <= _PERIODIC_LINEAR_FINAL_RESIDUAL_TOLERANCE
        ),
    }
    metrics = {
        "method": "periodic-controller-tail-stability",
        "genericSimpleConvergenceReported": "SIMPLE solution converged" in log,
        "targetMeanVelocityMPerS": target_mean_velocity,
        "finalMeanVelocityMPerS": final_mean_velocity,
        "meanVelocityRelativeError": mean_velocity_relative_error,
        "finalKinematicPressureGradientMPerS2": final_gradient,
        "pressureGradientTailRelativeSpan": gradient_tail_relative_span,
        "tailSampleCount": _PERIODIC_CONVERGENCE_TAIL_SAMPLES,
        "maxAbsGlobalContinuityError": max_abs_global_continuity,
        "maxLinearSolverFinalResidual": max_linear_final_residual,
        "criteria": criteria,
        "passed": all(criteria.values()),
    }
    if not metrics["passed"]:
        raise StraightPipeRunError(
            f"periodic physical convergence criteria failed for {case_dir}: "
            f"{json.dumps(metrics, sort_keys=True)}"
        )
    return metrics


def _validate_cpu_set(cpu_set: str | None) -> str | None:
    """Accept Docker's compact logical-CPU list syntax without shell parsing."""

    if cpu_set is None:
        return None
    if not isinstance(cpu_set, str) or not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", cpu_set):
        raise StraightPipeRunError(
            "cpu_set must use Docker logical-CPU syntax such as '0', '0-3', or '0,2-3'"
        )
    return cpu_set


def _cpu_set_logical_ids(cpu_set: str) -> list[int]:
    """Expand a validated Docker CPU set and reject ambiguous allocations."""

    selected = _validate_cpu_set(cpu_set)
    if selected is None:
        raise StraightPipeRunError("a CPU set is required for replicated timing")
    identifiers: set[int] = set()
    for component in selected.split(","):
        if "-" in component:
            start_text, end_text = component.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise StraightPipeRunError("cpu_set ranges must be ascending")
            expanded = range(start, end + 1)
        else:
            expanded = (int(component),)
        for identifier in expanded:
            if identifier in identifiers:
                raise StraightPipeRunError("cpu_set must not contain duplicate logical CPUs")
            identifiers.add(identifier)
    return sorted(identifiers)


def _validate_timing_cpu_sets(cpu_sets: dict[int, str]) -> dict[int, dict[str, Any]]:
    """Validate the exact 1/2/4-rank CPU allocations for timing evidence."""

    if set(cpu_sets) != {1, 2, 4}:
        raise StraightPipeRunError("replicated timing requires CPU sets for exactly 1, 2, and 4 ranks")
    normalized: dict[int, dict[str, Any]] = {}
    for ranks in (1, 2, 4):
        value = _validate_cpu_set(cpu_sets[ranks])
        assert value is not None
        logical_ids = _cpu_set_logical_ids(value)
        if len(logical_ids) < ranks:
            raise StraightPipeRunError(
                f"the {ranks}-rank timing allocation needs at least {ranks} logical CPUs"
            )
        normalized[ranks] = {"requestedCpuSet": value, "logicalCpuIds": logical_ids}
    return normalized


def _docker_serial_command(
    case_dir: Path,
    *,
    image: str,
    platform: str,
    cpu_set: str | None = None,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        platform,
    ]
    selected_cpu_set = _validate_cpu_set(cpu_set)
    if selected_cpu_set is not None:
        command.extend(["--cpuset-cpus", selected_cpu_set])
    command.extend(
        [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{case_dir.resolve()}:/case",
        "-w",
        "/case",
        "--entrypoint",
        "/bin/bash",
        image,
        "-lc",
        "bash run_level.sh",
        ]
    )
    return command


def run_serial_case(
    case_dir: Path,
    *,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    cpu_set: str | None = None,
) -> dict[str, Any]:
    """Run one materialized case and retain host/runtime timing provenance."""

    case_dir = case_dir.resolve()
    runtime_dir = case_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    command = _docker_serial_command(case_dir, image=image, platform=platform, cpu_set=cpu_set)
    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    docker_log = runtime_dir / "docker-serial.log"
    with docker_log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, text=True, check=False)
    host_wall_seconds = time.perf_counter() - start
    if completed.returncode != 0:
        raise StraightPipeRunError(
            f"serial OpenFOAM case failed with exit code {completed.returncode}; see {docker_log}"
        )
    numerical_convergence = _periodic_numerical_convergence(case_dir)
    diagnostics = _periodic_diagnostics(case_dir)
    _write_json(runtime_dir / "periodic-diagnostics.json", diagnostics)
    runtime = {
        "schema": RUNNER_SCHEMA,
        "runMode": "serial",
        "startedAt": started_at.isoformat(),
        "hostWallTimeSeconds": host_wall_seconds,
        "containerImage": image,
        "containerPlatform": platform,
        "containerImageProvenance": _container_image_provenance(image),
        **_case_runner_source_provenance(case_dir),
        "requestedCpuSet": _validate_cpu_set(cpu_set),
        "hostTimingCaveat": "linux/amd64 execution may be emulated on non-amd64 hosts",
        "dockerCommand": command,
        "numericalConvergence": numerical_convergence,
        "periodicDiagnostics": {
            "path": "runtime/periodic-diagnostics.json",
            "transverseResidualSource": diagnostics["transverseMomentumResiduals"]["source"],
            "wallForceSource": diagnostics["wallShearBalance"]["wallForceSource"],
        },
        "workload": _runtime_workload_metrics(case_dir, ranks=1),
        "resourceUsage": _solver_resource_usage(case_dir, ranks=1),
        **_solver_execution_times(case_dir),
    }
    _write_json(runtime_dir / "runtime.json", runtime)
    return runtime


def _periodic_diagnostics(case_dir: Path) -> dict[str, Any]:
    """Summarize retained periodic transverse-residual and wall-force evidence."""
    manifest = json.loads((case_dir / "case-manifest.json").read_text(encoding="utf-8"))
    residual_files = sorted((case_dir / "postProcessing" / "transverseMomentumResiduals").glob("*/residuals.dat"))
    force_files = sorted((case_dir / "postProcessing" / "wallForces").glob("**/force*.dat"))
    shear_files = sorted((case_dir / "postProcessing" / "wallShearStress").glob("*/wallShearStress.dat"))
    residuals: dict[str, float | None] = {"Ux": None, "Uy": None, "Uz": None}
    if residual_files:
        rows = [line.split() for line in residual_files[-1].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if rows and len(rows[-1]) >= 4:
            for component, value in zip(("Ux", "Uy", "Uz"), rows[-1][1:4]):
                residuals[component] = None if value == "N/A" else float(value)
    gradient = _last_mean_velocity_force_gradient(case_dir)
    fluid = manifest["fluid"]
    geometry = manifest["geometry"]
    flow_control = manifest["flowControl"]
    expected_force = abs(gradient) * float(fluid["densityKgPerM3"]) * float(flow_control["sectorAreaM2"]) * float(geometry["lengthM"])
    viscous_force: tuple[float, float, float] | None = None
    if force_files:
        rows = [line for line in force_files[-1].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
        vectors = re.findall(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", rows[-1]) if rows else []
        if len(vectors) >= 2:
            viscous_force = tuple(float(value) for value in vectors[1])
    return {
        "schema": "flowlab.periodic-structured-diagnostics.v1",
        "boundaryCondition": "cyclic periodic pressure-gradient; meanVelocityForce",
        "transverseMomentumResiduals": {
            "source": None if not residual_files else str(residual_files[-1].relative_to(case_dir)),
            "lastInitialResiduals": residuals,
            "transverseComponents": {"Uy": residuals["Uy"], "Uz": residuals["Uz"]},
            "interpretation": "Periodic pressure has a gauge null space; component initial residuals are diagnostic only. Acceptance remains controller-tail, continuity, and final linear-residual gates.",
        },
        "wallShearBalance": {
            "wallShearSource": None if not shear_files else str(shear_files[-1].relative_to(case_dir)),
            "wallForceSource": None if not force_files else str(force_files[-1].relative_to(case_dir)),
            "kinematicPressureGradientMPerS2": gradient,
            "expectedSectorAxialWallForceN": expected_force,
            "observedSectorViscousForceN": viscous_force,
            "relativeAxialForceImbalance": None if viscous_force is None or expected_force == 0 else abs(abs(viscous_force[0]) - expected_force) / expected_force,
        },
    }


def _last_mean_velocity_force_gradient(case_dir: Path) -> float:
    return _mean_velocity_force_history(case_dir)[-1][1]


def _serial_level_observation(
    case_dir: Path,
    *,
    spec: StraightPipeRunSpec,
    mesh_size_m: float,
) -> dict[str, Any]:
    inlet_flux_sector = _last_function_value(case_dir, "inletFlux")
    outlet_flux_sector = _last_function_value(case_dir, "outletFlux")
    pressure_gradient_kinematic = _last_mean_velocity_force_gradient(case_dir)
    pressure_drop_pa = pressure_gradient_kinematic * spec.length_m * spec.density_kg_m3
    flow_control = _mesh_flow_control(spec, mesh_size_m)
    mesh_metadata = _mesh_recipe_metadata(spec, mesh_size_m)
    sector_scale = float(flow_control["fullPipeScale"])
    full_pipe_volumetric_flow_rate = outlet_flux_sector * sector_scale
    flow_rate_relative_error = abs(
        full_pipe_volumetric_flow_rate - flow_control["targetFullPipeVolumetricFlowRateM3PerS"]
    ) / flow_control["targetFullPipeVolumetricFlowRateM3PerS"]
    observation = {
        "id": case_dir.name,
        "characteristicCellSizeM": mesh_size_m,
        "cellCount": _cell_count_from_mesh_report(case_dir),
        "meshRecipe": mesh_metadata["meshRecipe"],
        "meshGenerator": mesh_metadata["meshGenerator"],
        "meshRepresentation": mesh_metadata,
        "sectorAngleDegrees": float(flow_control["sectorAngleDegrees"]),
        "sectorScaleToFullPipe": sector_scale,
        "computedKinematicPressureGradientMPerS2": pressure_gradient_kinematic,
        "meanVelocityTargetMPerS": flow_control["meanVelocityTargetMPerS"],
        "sectorCrossSectionAreaM2": flow_control["sectorAreaM2"],
        "meshCrossSectionAreaM2": flow_control["sectorAreaM2"],
        "geometryAreaRelativeDeficit": flow_control["geometryAreaRelativeDeficit"],
        "targetFullPipeVolumetricFlowRateM3PerS": flow_control[
            "targetFullPipeVolumetricFlowRateM3PerS"
        ],
        "fullPipeVolumetricFlowRateM3PerS": full_pipe_volumetric_flow_rate,
        "flowRateRelativeError": flow_rate_relative_error,
        "pressureDropPa": pressure_drop_pa,
        "inletMassFlowRateKgPerS": inlet_flux_sector * spec.density_kg_m3 * sector_scale,
        "outletMassFlowRateKgPerS": outlet_flux_sector * spec.density_kg_m3 * sector_scale,
        "rawSectorInletVolumetricFlowRateM3PerS": inlet_flux_sector,
        "rawSectorOutletVolumetricFlowRateM3PerS": outlet_flux_sector,
    }
    _write_json(
        case_dir / "mesh-report.json",
        {
            "schema": RUNNER_SCHEMA,
            "levelId": case_dir.name,
            "meshRecipe": mesh_metadata["meshRecipe"],
            "meshGenerator": mesh_metadata["meshGenerator"],
            "meshGeometry": _mesh_geometry_description(mesh_metadata),
            "meshRepresentation": mesh_metadata,
            "checkMeshPassed": "Mesh OK" in (case_dir / "log.checkMesh").read_text(encoding="utf-8"),
            "cellCount": observation["cellCount"],
            "characteristicCellSizeM": mesh_size_m,
            "sectorAngleDegrees": float(flow_control["sectorAngleDegrees"]),
            "sectorScaleToFullPipe": sector_scale,
            "computedKinematicPressureGradientMPerS2": pressure_gradient_kinematic,
            "meanVelocityTargetMPerS": flow_control["meanVelocityTargetMPerS"],
            "sectorCrossSectionAreaM2": flow_control["sectorAreaM2"],
            "meshCrossSectionAreaM2": flow_control["sectorAreaM2"],
            "geometryAreaRelativeDeficit": flow_control["geometryAreaRelativeDeficit"],
            "fullPipeVolumetricFlowRateM3PerS": full_pipe_volumetric_flow_rate,
            "flowRateRelativeError": flow_rate_relative_error,
        },
    )
    return observation


def _create_archive(root: Path, archive_path: Path, sources: Iterable[Path]) -> Path:
    root = root.resolve()
    seen: set[Path] = set()
    with tarfile.open(archive_path, "w:gz") as archive:
        for source in sorted((path.resolve() for path in sources), key=lambda path: str(path)):
            if source in seen:
                continue
            seen.add(source)
            if not source.exists():
                raise StraightPipeRunError(f"cannot archive missing evidence source: {source}")
            try:
                arcname = source.relative_to(root).as_posix()
            except ValueError as exc:
                raise StraightPipeRunError(f"evidence source is outside run root: {source}") from exc
            archive.add(source, arcname=arcname, recursive=True)
    return archive_path


def _artifact_record(root: Path, kind: str, path: Path) -> dict[str, str]:
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
    }


def _uniform_runtime_provenance(runtimes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Require every serial mesh level to share one reproducible runtime identity."""

    if not runtimes:
        raise StraightPipeRunError("cannot package evidence without runtime records")
    fields = ("containerImage", "containerPlatform", "runnerSourceSha256")
    first = runtimes[0]
    try:
        reference = {field: first[field] for field in fields}
    except KeyError as exc:
        raise StraightPipeRunError(f"runtime record is missing provenance field: {exc}") from exc
    for runtime in runtimes[1:]:
        if any(runtime.get(field) != value for field, value in reference.items()):
            raise StraightPipeRunError("serial mesh levels used different runtime or runner provenance")
    image_provenance = first.get("containerImageProvenance")
    if not isinstance(image_provenance, dict):
        image_provenance = {
            "requestedImage": reference["containerImage"],
            "captureStatus": "not-recorded",
        }
    identity_keys = ("captureStatus", "imageId", "repoDigests", "os", "architecture")
    expected_image_identity = {key: image_provenance.get(key) for key in identity_keys}
    for runtime in runtimes[1:]:
        candidate = runtime.get("containerImageProvenance")
        if not isinstance(candidate, dict):
            candidate = {
                "requestedImage": reference["containerImage"],
                "captureStatus": "not-recorded",
            }
        candidate_identity = {key: candidate.get(key) for key in identity_keys}
        if candidate_identity != expected_image_identity:
            raise StraightPipeRunError("serial mesh levels used different immutable container images")
    return {**reference, "containerImageProvenance": image_provenance}


def _numeric_time_directories(case_dir: Path) -> list[Path]:
    return [
        path
        for path in case_dir.iterdir()
        if path.is_dir() and re.fullmatch(r"\d+(?:\.\d+)?", path.name) is not None
    ]


def _geometry_changes_across_levels(observations: Sequence[dict[str, Any]]) -> bool:
    """Return whether the physical cross-section representation changes with h."""

    signatures = {
        (
            observation["meshRecipe"],
            observation["sectorAngleDegrees"],
            observation["sectorScaleToFullPipe"],
            observation["meshCrossSectionAreaM2"],
            observation["geometryAreaRelativeDeficit"],
            observation["meshRepresentation"].get("totalAzimuthalCells"),
            observation["meshRepresentation"].get("facetAngleDegrees"),
            observation["meshRepresentation"].get("azimuthalCells"),
            observation["meshRepresentation"].get("azimuthalCellsPerQuadrant"),
            observation["meshRepresentation"].get("coreRadiusM"),
            observation["meshRepresentation"].get("coreCellsPerSide"),
        )
        for observation in observations
    }
    return len(signatures) > 1


def _package_serial_evidence(
    output_dir: Path,
    *,
    spec: StraightPipeRunSpec,
    observations: list[dict[str, Any]],
    runtimes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create immutable-ish analysis artifacts and evaluate the three local gates."""

    output_dir = output_dir.resolve()
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    reference = spec.reference()
    levels = ["coarse", "medium", "fine"]
    if [observation["id"] for observation in observations] != levels:
        raise StraightPipeRunError("serial observations must be ordered coarse, medium, fine")
    runtime_provenance = _uniform_runtime_provenance(runtimes)
    runner_snapshot_path = output_dir / "runner-source-snapshot.py"
    runner_snapshot_record: dict[str, str] | None = None
    if runner_snapshot_path.is_file():
        runner_snapshot_hash = _sha256_file(runner_snapshot_path)
        if runner_snapshot_hash != runtime_provenance["runnerSourceSha256"]:
            raise StraightPipeRunError(
                "materialized runner source snapshot does not match the serial runtime source hash"
            )
        runner_snapshot_record = _artifact_record(
            output_dir, "runner-source-snapshot", runner_snapshot_path
        )

    run_manifest_path = output_dir / "run-manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    run_manifest.update(
        {
            "status": "serial-runs-complete",
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "serialRuntime": runtimes,
            "serialMeshLevels": observations,
            "scientificStatus": "analysis-only",
            "validated": False,
        }
    )
    _write_json(run_manifest_path, run_manifest)

    serial_cases = [output_dir / "serial" / level for level in levels]
    mesh_sources: list[Path] = []
    log_sources: list[Path] = []
    raw_sources: list[Path] = []
    residual_sources: list[Path] = []
    quality_sources: list[Path] = []
    for case_dir in serial_cases:
        mesh_sources.extend(
            [
                case_dir / "case-manifest.json",
                case_dir / "system",
                case_dir / "constant",
                case_dir / "log.blockMesh",
                case_dir / "log.checkMesh",
            ]
        )
        log_sources.extend(sorted(case_dir.glob("log.*")))
        raw_sources.extend([case_dir / "0", *sorted(_numeric_time_directories(case_dir)), case_dir / "VTK"])
        residual_sources.append(case_dir / "log.foamRun")
        quality_sources.extend([case_dir / "log.checkMesh", case_dir / "mesh-report.json"])
    if runner_snapshot_path.is_file():
        mesh_sources.append(runner_snapshot_path)

    mesh_archive = _create_archive(output_dir, artifacts_dir / "mesh-artifact.tar.gz", mesh_sources)
    solver_archive = _create_archive(output_dir, artifacts_dir / "solver-logs.tar.gz", log_sources)
    raw_archive = _create_archive(output_dir, artifacts_dir / "raw-result-fields.tar.gz", raw_sources)
    residual_archive = _create_archive(output_dir, artifacts_dir / "residual-history.tar.gz", residual_sources)
    quality_archive = _create_archive(output_dir, artifacts_dir / "mesh-quality.tar.gz", quality_sources)
    raw_digest = _sha256_file(raw_archive)

    qoi_levels = [
        {
            "id": observation["id"],
            "source": "solver-produced",
            "sourceArtifactSha256": raw_digest,
            "characteristicCellSizeM": observation["characteristicCellSizeM"],
            "pressureDropPa": observation["pressureDropPa"],
            "cellCount": observation["cellCount"],
            "meshRecipe": observation["meshRecipe"],
            "meshGenerator": observation["meshGenerator"],
            "meshRepresentation": observation["meshRepresentation"],
            "sectorAngleDegrees": observation["sectorAngleDegrees"],
            "sectorScaleToFullPipe": observation["sectorScaleToFullPipe"],
            "computedKinematicPressureGradientMPerS2": observation[
                "computedKinematicPressureGradientMPerS2"
            ],
            "meanVelocityTargetMPerS": observation["meanVelocityTargetMPerS"],
            "sectorCrossSectionAreaM2": observation["sectorCrossSectionAreaM2"],
            "meshCrossSectionAreaM2": observation["meshCrossSectionAreaM2"],
            "geometryAreaRelativeDeficit": observation["geometryAreaRelativeDeficit"],
            "targetFullPipeVolumetricFlowRateM3PerS": observation[
                "targetFullPipeVolumetricFlowRateM3PerS"
            ],
            "fullPipeVolumetricFlowRateM3PerS": observation["fullPipeVolumetricFlowRateM3PerS"],
            "flowRateRelativeError": observation["flowRateRelativeError"],
        }
        for observation in observations
    ]
    fine = observations[-1]
    geometry_changes_across_levels = _geometry_changes_across_levels(observations)
    qoi = {
        "schema": QOI_SCHEMA,
        "caseId": "straight-pipe",
        "source": "solver-produced",
        "referenceInputs": {
            "lengthM": spec.length_m,
            "diameterM": 2.0 * spec.radius_m,
            "densityKgPerM3": spec.density_kg_m3,
            "dynamicViscosityPaS": spec.dynamic_viscosity_pa_s,
            "volumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
        },
        "meshLevels": qoi_levels,
        "conservation": {
            "inletMassFlowRateKgPerS": fine["inletMassFlowRateKgPerS"],
            "outletMassFlowRateKgPerS": fine["outletMassFlowRateKgPerS"],
            "relativeImbalance": relative_mass_flow_imbalance(
                fine["inletMassFlowRateKgPerS"], fine["outletMassFlowRateKgPerS"]
            ),
        },
        "flowControl": {
            "targetFullPipeVolumetricFlowRateM3PerS": fine[
                "targetFullPipeVolumetricFlowRateM3PerS"
            ],
            "measuredFullPipeVolumetricFlowRateM3PerS": fine["fullPipeVolumetricFlowRateM3PerS"],
            "relativeError": fine["flowRateRelativeError"],
        },
        "meshRepresentation": {
            "meshRecipe": fine["meshRecipe"],
            "meshGenerator": fine["meshGenerator"],
            "geometry": _mesh_geometry_description(fine["meshRepresentation"]),
            "angleDegrees": fine["sectorAngleDegrees"],
            "scaleToFullCircularPipe": fine["sectorScaleToFullPipe"],
            "radialPlanes": fine["meshRepresentation"]["radialPlanes"],
            "radialPlaneBoundaryType": fine["meshRepresentation"]["radialPlaneBoundaryType"],
            "crossSectionAreaMethod": fine["meshRepresentation"]["crossSectionAreaMethod"],
            "crossSectionAreaRelativeDeficit": fine["geometryAreaRelativeDeficit"],
            "geometryChangesAcrossLevels": geometry_changes_across_levels,
            "levelSpecificGeometry": geometry_changes_across_levels,
        },
        "pressureDropMethod": {
            "kind": "periodic-mean-velocity-control",
            "quantity": "final meanVelocityForce kinematic pressure gradient times pipe length and density",
        },
    }
    qoi_path = artifacts_dir / "qoi-extraction.json"
    _write_json(qoi_path, qoi)
    _write_text(
        artifacts_dir / "serial-qoi-table.csv",
        "level,characteristic_cell_size_m,cell_count,pressure_drop_pa,inlet_mass_flow_kg_s,outlet_mass_flow_kg_s,mean_velocity_target_m_s,sector_cross_section_area_m2,target_full_pipe_flow_m3_s,measured_full_pipe_flow_m3_s,flow_rate_relative_error\n"
        + "".join(
            f"{observation['id']},{observation['characteristicCellSizeM']:.17g},{observation['cellCount']},"
            f"{observation['pressureDropPa']:.17g},{observation['inletMassFlowRateKgPerS']:.17g},"
            f"{observation['outletMassFlowRateKgPerS']:.17g},{observation['meanVelocityTargetMPerS']:.17g},"
            f"{observation['sectorCrossSectionAreaM2']:.17g},"
            f"{observation['targetFullPipeVolumetricFlowRateM3PerS']:.17g},"
            f"{observation['fullPipeVolumetricFlowRateM3PerS']:.17g},"
            f"{observation['flowRateRelativeError']:.17g}\n"
            for observation in observations
        ),
    )

    grid_samples = [
        {
            "id": level["id"],
            "source": "solver-produced",
            "sourceArtifactSha256": raw_digest,
            "characteristicCellSizeM": level["characteristicCellSizeM"],
            "value": level["pressureDropPa"],
        }
        for level in qoi_levels
    ]
    grid = richardson_grid_convergence(grid_samples)
    pressure_error = abs(fine["pressureDropPa"] - reference["pressureDropPa"]) / reference["pressureDropPa"]
    mass_imbalance = qoi["conservation"]["relativeImbalance"]
    gates = {
        "analyticalPressureDropError": {
            "value": pressure_error,
            "limit": 0.05,
            "passed": pressure_error <= 0.05,
        },
        "massBalance": {
            "value": mass_imbalance,
            "limit": 0.001,
            "passed": mass_imbalance <= 0.001,
        },
        "flowRateTarget": {
            "value": fine["flowRateRelativeError"],
            "limit": 1.0e-6,
            "passed": fine["flowRateRelativeError"] <= 1.0e-6,
        },
        "fineGridGciPercent": {
            "value": grid["fineGridGciPercent"],
            "limit": DEFAULT_FINE_GRID_GCI_PERCENT_LIMIT,
            "passed": grid["fineGridGciPercent"] <= DEFAULT_FINE_GRID_GCI_PERCENT_LIMIT,
        },
    }
    serial_gates_passed = all(bool(gate["passed"]) for gate in gates.values())
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "caseId": "straight-pipe",
        "scientificStatus": "analysis-only",
        "validated": False,
        "applicability": {
            "reynoldsNumber": reference["reynoldsNumber"],
            "boundaryCondition": "periodic-pressure-gradient",
            "pressureDropExtraction": "final meanVelocityForce gradient from the converged periodic solve",
            "geometry": _mesh_geometry_description(fine["meshRepresentation"]),
            "meshRecipe": fine["meshRecipe"],
            "geometryAreaRelativeDeficit": fine["geometryAreaRelativeDeficit"],
        },
        "meshRefinement": {
            "levels": [
                {
                    "id": level["id"],
                    "characteristicCellSizeM": level["characteristicCellSizeM"],
                    "pressureDropPa": level["pressureDropPa"],
                }
                for level in qoi_levels
            ],
            "observedOrder": grid["observedOrder"],
            "fineGridGciPercent": grid["fineGridGciPercent"],
            "refinementRatio": grid["refinementRatio"],
            "richardsonExtrapolatedPressureDropPa": grid["richardsonExtrapolatedValue"],
            "effectiveDiscretizationDimension": fine["meshRepresentation"][
                "effectiveDiscretizationDimension"
            ],
            "characteristicLengthDefinition": fine["meshRepresentation"][
                "characteristicLengthDefinition"
            ],
        },
        "timeRefinement": {
            "method": "direct-steady",
            "solverMethod": "OpenFOAM incompressibleFluid SIMPLE",
            "temporalDiscretization": "none",
            "rationale": (
                "All three cases passed the predeclared periodic-controller tail-stability, "
                "continuity, and inner linear-solver criteria. Generic normalized SIMPLE "
                "initial residuals are retained as diagnostics but are not acceptance criteria "
                "for this periodic pressure-gauge formulation."
            ),
        },
        "errorAssessment": {
            "analyticalReferencePressureDropPa": reference["pressureDropPa"],
            "fineMeshPressureDropPa": fine["pressureDropPa"],
            "pressureDropRelativeError": pressure_error,
        },
        "conservation": qoi["conservation"],
        "provenance": {
            "caseManifestSha256": _sha256_file(run_manifest_path),
            "meshArtifactSha256": _sha256_file(mesh_archive),
            "solverLogSha256": _sha256_file(solver_archive),
            "rawResultSha256": raw_digest,
            "qoiExtractionSha256": _sha256_file(qoi_path),
            "solverVersion": "OpenFOAM-11",
            "solverCommand": "foamRun -solver incompressibleFluid",
            "evidencePackagingRunnerSourceSha256": _runner_source_sha256(),
            "runnerSourceSnapshotSha256": (
                runner_snapshot_record["sha256"] if runner_snapshot_record is not None else None
            ),
            **runtime_provenance,
        },
    }
    evidence_path = artifacts_dir / "evidence-package.json"
    _write_json(evidence_path, evidence)
    records = [
        _artifact_record(output_dir, "case-manifest", run_manifest_path),
        _artifact_record(output_dir, "mesh-artifact", mesh_archive),
        _artifact_record(output_dir, "solver-log", solver_archive),
        _artifact_record(output_dir, "raw-result-fields", raw_archive),
        _artifact_record(output_dir, "residual-history", residual_archive),
        _artifact_record(output_dir, "mesh-quality-report", quality_archive),
        _artifact_record(output_dir, "qoi-extraction-table", qoi_path),
        _artifact_record(output_dir, "evidence-package", evidence_path),
    ]
    if runner_snapshot_record is not None:
        records.append(runner_snapshot_record)
    artifact_index_path = artifacts_dir / "artifact-index.json"
    _write_json(
        artifact_index_path,
        {
            "schema": RUNNER_SCHEMA,
            "caseId": "straight-pipe",
            "status": "captured-awaiting-independent-review",
            "files": records,
        },
    )
    candidate = {
        "schema": RUNNER_SCHEMA,
        "caseId": "straight-pipe",
        "status": "serial-gates-passed" if serial_gates_passed else "serial-gates-failed",
        "scientificStatus": "analysis-only",
        "validated": False,
        "independentReviewRequired": True,
        "meshRecipe": fine["meshRecipe"],
        "meshGeometry": _mesh_geometry_description(fine["meshRepresentation"]),
        "serialGates": gates,
        "selectedMesh": "fine" if serial_gates_passed else None,
        "parallelRunPermitted": serial_gates_passed,
        "parallelEquivalenceRelativeTolerance": 1.0e-6,
        "nextRequiredAction": (
            "Submit this mesh family for independent review.  Exact-input MPI QoI evidence may then be "
            "captured at 2 and 4 ranks; portable performance claims require a separate native repeated-trial protocol."
            if serial_gates_passed
            else "Do not run MPI; investigate and rerun the rejected serial evidence."
        ),
    }
    candidate_path = artifacts_dir / "candidate-report.json"
    _write_json(candidate_path, candidate)
    return {
        "qoi": qoi,
        "evidence": evidence,
        "gates": gates,
        "serialGatesPassed": serial_gates_passed,
        "candidateReport": candidate_path,
        "artifactIndex": artifact_index_path,
    }


def run_serial_suite(
    output_dir: Path,
    *,
    spec: StraightPipeRunSpec | None = None,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    cpu_set: str | None = None,
) -> dict[str, Any]:
    """Materialize, run, package, and assess exactly three serial refinements."""

    selected_spec = spec or default_run_spec()
    case_dirs = materialize_serial_cases(
        output_dir,
        selected_spec,
        image=image,
        platform=platform,
    )
    runtimes = [
        run_serial_case(case_dir, image=image, platform=platform, cpu_set=cpu_set)
        for case_dir in case_dirs
    ]
    observations = [
        _serial_level_observation(case_dir, spec=selected_spec, mesh_size_m=mesh_size_m)
        for case_dir, mesh_size_m in zip(case_dirs, selected_spec.mesh_sizes_m)
    ]
    return _package_serial_evidence(
        output_dir,
        spec=selected_spec,
        observations=observations,
        runtimes=runtimes,
    )


def package_existing_serial_suite(
    output_dir: Path,
    *,
    spec: StraightPipeRunSpec | None = None,
) -> dict[str, Any]:
    """Package a complete three-level run that was executed in isolated jobs.

    This recovery path is intentionally strict: it only packages existing logs
    after every serial case recorded periodic physical-convergence evidence and
    runtime provenance.  It never reruns a level or fills in missing evidence.
    """

    output_dir = output_dir.resolve()
    selected_spec = _existing_run_spec(output_dir, spec)
    levels = ("coarse", "medium", "fine")
    case_dirs = [output_dir / "serial" / level for level in levels]
    for case_dir in case_dirs:
        runtime_path = case_dir / "runtime" / "runtime.json"
        solver_log = case_dir / "log.foamRun"
        if not runtime_path.is_file() or not solver_log.is_file():
            raise StraightPipeRunError(f"cannot package incomplete serial case: {case_dir}")
        _periodic_numerical_convergence(case_dir)
    runtimes = [
        json.loads((case_dir / "runtime" / "runtime.json").read_text(encoding="utf-8"))
        for case_dir in case_dirs
    ]
    observations = [
        _serial_level_observation(case_dir, spec=selected_spec, mesh_size_m=mesh_size_m)
        for case_dir, mesh_size_m in zip(case_dirs, selected_spec.mesh_sizes_m)
    ]
    return _package_serial_evidence(
        output_dir,
        spec=selected_spec,
        observations=observations,
        runtimes=runtimes,
    )


_PARALLEL_INPUT_PATHS = ("0", "constant", "system", "case-manifest.json")
_SERIAL_TRIAL_INPUT_PATHS = (*_PARALLEL_INPUT_PATHS, "run_level.sh")


def _input_file_digests(case_dir: Path) -> dict[str, str]:
    """Hash exactly the files that define a reproducible parallel input case."""

    records: dict[str, str] = {}
    for relative in _PARALLEL_INPUT_PATHS:
        source = case_dir / relative
        if source.is_file():
            records[relative] = _sha256_file(source)
        elif source.is_dir():
            for child in sorted(source.rglob("*")):
                if child.is_file():
                    records[child.relative_to(case_dir).as_posix()] = _sha256_file(child)
        else:
            raise StraightPipeRunError(f"parallel input is missing required path: {source}")
    return records


def _digest_file_records(records: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_digest in sorted(records.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _decompose_par_dict(ranks: int) -> str:
    if ranks < 2:
        raise StraightPipeRunError("parallel execution requires at least two MPI ranks")
    return _foam_header(
        class_name="dictionary", location="system", object_name="decomposeParDict"
    ) + f'''
numberOfSubdomains {ranks};

method          scotch;
'''


def _parallel_run_script(ranks: int) -> str:
    return f'''#!/usr/bin/env bash
source /opt/openfoam11/etc/bashrc
set -euo pipefail

checkMesh -allGeometry -allTopology > log.checkMesh 2>&1
grep -q "Mesh OK" log.checkMesh
decomposePar -force > log.decomposePar 2>&1
mpirun -np {ranks} checkMesh -parallel -allGeometry -allTopology > log.checkMesh.parallel 2>&1
grep -q "Mesh OK" log.checkMesh.parallel
mkdir -p runtime
if [ -x /usr/bin/time ]; then
    /usr/bin/time -v -o runtime/solver-resources.txt \
        mpirun -np {ranks} foamRun -solver incompressibleFluid -parallel > log.foamRun 2>&1
else
    mpirun -np {ranks} foamRun -solver incompressibleFluid -parallel > log.foamRun 2>&1
fi
grep -q "End" log.foamRun
reconstructPar -latestTime > log.reconstructPar 2>&1
foamToVTK -ascii -latestTime > log.foamToVTK 2>&1
'''


def _copy_frozen_case_inputs(
    source_case: Path,
    target_case: Path,
    *,
    include_run_level_script: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    """Copy only solver-defining inputs into a new isolated case directory."""

    source_case = source_case.resolve()
    target_case = target_case.resolve()
    if target_case.exists() and any(target_case.iterdir()):
        raise StraightPipeRunError(f"refusing to overwrite non-empty target case directory: {target_case}")
    source_records = _input_file_digests(source_case)
    target_case.mkdir(parents=True, exist_ok=True)
    for relative in _PARALLEL_INPUT_PATHS:
        source = source_case / relative
        target = target_case / relative
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    if include_run_level_script:
        run_script = source_case / "run_level.sh"
        if not run_script.is_file():
            raise StraightPipeRunError(f"serial trial source is missing run_level.sh: {run_script}")
        shutil.copy2(run_script, target_case / "run_level.sh")
    copied_records = _input_file_digests(target_case)
    if copied_records != source_records:
        raise StraightPipeRunError("case input copy does not exactly match its frozen serial source")
    return source_records, copied_records


def _source_case_label(source_case: Path, evidence_root: Path) -> str:
    try:
        return source_case.resolve().relative_to(evidence_root.resolve()).as_posix()
    except ValueError:
        return source_case.resolve().as_posix()


def materialize_parallel_case_from_source(
    source_case: Path,
    target_case: Path,
    *,
    ranks: int,
    evidence_root: Path,
) -> Path:
    """Clone one frozen serial source into an isolated parallel case.

    Numeric time directories, post-processing, logs, VTK, and runtime metadata
    are deliberately excluded so an MPI QoI cannot accidentally consume a
    serial result.
    """

    source_case = source_case.resolve()
    target_case = target_case.resolve()
    source_records, copied_records = _copy_frozen_case_inputs(
        source_case, target_case, include_run_level_script=False
    )
    input_digest = _digest_file_records(source_records)
    _write_text(target_case / "system" / "decomposeParDict", _decompose_par_dict(ranks))
    _write_text(target_case / "run_parallel.sh", _parallel_run_script(ranks), executable=True)
    _write_json(
        target_case / "parallel-manifest.json",
        {
            "schema": RUNNER_SCHEMA,
            "runMode": "parallel",
            "rankCount": ranks,
            "sourceSerialCase": _source_case_label(source_case, evidence_root),
            "sourceInputPaths": list(_PARALLEL_INPUT_PATHS),
            "sourceInputTreeSha256": input_digest,
            "copiedInputTreeSha256": _digest_file_records(copied_records),
            "numericsPreserved": True,
            "qoiExtractionPreserved": True,
            "excludedSourceOutputs": ["numeric time directories", "postProcessing", "VTK", "log.*", "runtime"],
        },
    )
    return target_case


def materialize_parallel_case(
    output_dir: Path,
    *,
    ranks: int,
) -> Path:
    """Compatibility wrapper for a normal accepted-fine MPI evidence run."""

    output_dir = output_dir.resolve()
    return materialize_parallel_case_from_source(
        output_dir / "serial" / "fine",
        output_dir / "parallel" / f"mpi-{ranks}",
        ranks=ranks,
        evidence_root=output_dir,
    )


def materialize_serial_trial_case(
    source_case: Path,
    target_case: Path,
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    """Make a fine-grid serial timing trial from immutable accepted inputs."""

    source_records, copied_records = _copy_frozen_case_inputs(
        source_case, target_case, include_run_level_script=True
    )
    source_trial_records = {
        **source_records,
        "run_level.sh": _sha256_file(source_case.resolve() / "run_level.sh"),
    }
    copied_trial_records = {
        **copied_records,
        "run_level.sh": _sha256_file(target_case.resolve() / "run_level.sh"),
    }
    if source_trial_records != copied_trial_records:
        raise StraightPipeRunError("serial trial run script does not match its frozen source")
    digest = _digest_file_records(source_trial_records)
    manifest = {
        "schema": REPLICATED_TIMING_SCHEMA,
        "runMode": "serial-timing-trial",
        "sourceSerialCase": _source_case_label(source_case, evidence_root),
        "sourceInputPaths": list(_SERIAL_TRIAL_INPUT_PATHS),
        "sourceInputTreeSha256": digest,
        "copiedInputTreeSha256": _digest_file_records(copied_trial_records),
        "numericsPreserved": True,
        "qoiExtractionPreserved": True,
        "excludedSourceOutputs": ["numeric time directories", "postProcessing", "VTK", "log.*", "runtime"],
    }
    _write_json(target_case / "trial-input-manifest.json", manifest)
    return manifest


def _docker_parallel_command(
    case_dir: Path,
    *,
    image: str,
    platform: str,
    cpu_set: str | None = None,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        platform,
    ]
    selected_cpu_set = _validate_cpu_set(cpu_set)
    if selected_cpu_set is not None:
        command.extend(["--cpuset-cpus", selected_cpu_set])
    command.extend(
        [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{case_dir.resolve()}:/case",
        "-w",
        "/case",
        "--entrypoint",
        "/bin/bash",
        image,
        "-lc",
        "bash run_parallel.sh",
        ]
    )
    return command


def run_parallel_case(
    case_dir: Path,
    *,
    ranks: int,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    cpu_set: str | None = None,
) -> dict[str, Any]:
    """Run a decomposed case, reconstruct fields, and retain comparable timing."""

    case_dir = case_dir.resolve()
    runtime_dir = case_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    command = _docker_parallel_command(case_dir, image=image, platform=platform, cpu_set=cpu_set)
    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    docker_log = runtime_dir / "docker-parallel.log"
    with docker_log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, text=True, check=False)
    host_wall_seconds = time.perf_counter() - start
    if completed.returncode != 0:
        raise StraightPipeRunError(
            f"parallel OpenFOAM case failed with exit code {completed.returncode}; see {docker_log}"
        )
    numerical_convergence = _periodic_numerical_convergence(case_dir)
    runtime = {
        "schema": RUNNER_SCHEMA,
        "runMode": "parallel",
        "rankCount": ranks,
        "startedAt": started_at.isoformat(),
        "hostWallTimeSeconds": host_wall_seconds,
        "containerImage": image,
        "containerPlatform": platform,
        "containerImageProvenance": _container_image_provenance(image),
        **_case_runner_source_provenance(case_dir),
        "requestedCpuSet": _validate_cpu_set(cpu_set),
        "hostTimingCaveat": "linux/amd64 execution may be emulated on non-amd64 hosts",
        "dockerCommand": command,
        "numericalConvergence": numerical_convergence,
        "workload": _runtime_workload_metrics(case_dir, ranks=ranks),
        "resourceUsage": _solver_resource_usage(case_dir, ranks=ranks),
        **_solver_execution_times(case_dir),
    }
    _write_json(runtime_dir / "runtime.json", runtime)
    return runtime


def _accepted_parallel_candidate(output_dir: Path) -> dict[str, Any]:
    candidate_path = output_dir / "artifacts" / "candidate-report.json"
    if not candidate_path.is_file():
        raise StraightPipeRunError(f"serial candidate report is missing: {candidate_path}")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate.get("parallelRunPermitted") is not True or candidate.get("selectedMesh") != "fine":
        raise StraightPipeRunError("parallel execution is blocked until all serial gates pass on the fine mesh")
    return candidate


def _serial_fine_parallel_baseline(output_dir: Path) -> dict[str, float]:
    qoi_path = output_dir / "artifacts" / "qoi-extraction.json"
    runtime_path = output_dir / "serial" / "fine" / "runtime" / "runtime.json"
    if not qoi_path.is_file() or not runtime_path.is_file():
        raise StraightPipeRunError("accepted serial QoI or runtime evidence is missing")
    qoi = json.loads(qoi_path.read_text(encoding="utf-8"))
    levels = qoi.get("meshLevels")
    if not isinstance(levels, list):
        raise StraightPipeRunError("accepted serial QoI table is missing mesh levels")
    fine = next((level for level in levels if level.get("id") == "fine"), None)
    if not isinstance(fine, dict):
        raise StraightPipeRunError("accepted serial QoI table has no fine level")
    conservation = qoi.get("conservation")
    flow_control = qoi.get("flowControl")
    if not isinstance(conservation, dict) or not isinstance(flow_control, dict):
        raise StraightPipeRunError("accepted serial QoI table lacks conservation or flow-control evidence")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    try:
        return {
            "pressureDropPa": float(fine["pressureDropPa"]),
            "computedKinematicPressureGradientMPerS2": float(
                fine["computedKinematicPressureGradientMPerS2"]
            ),
            "fullPipeVolumetricFlowRateM3PerS": float(
                flow_control["measuredFullPipeVolumetricFlowRateM3PerS"]
            ),
            "inletMassFlowRateKgPerS": float(conservation["inletMassFlowRateKgPerS"]),
            "outletMassFlowRateKgPerS": float(conservation["outletMassFlowRateKgPerS"]),
            "solverClockTimeSeconds": float(runtime["solverClockTimeSeconds"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise StraightPipeRunError("accepted serial QoI baseline is malformed") from exc


def _relative_difference(value: float, baseline: float) -> float:
    return abs(value - baseline) / max(abs(baseline), 1.0e-30)


def _parallel_qoi_equivalence(
    *,
    serial: dict[str, float],
    parallel: dict[str, Any],
) -> dict[str, Any]:
    comparisons = {}
    for key in (
        "pressureDropPa",
        "computedKinematicPressureGradientMPerS2",
        "fullPipeVolumetricFlowRateM3PerS",
        "inletMassFlowRateKgPerS",
        "outletMassFlowRateKgPerS",
    ):
        serial_value = serial[key]
        parallel_value = float(parallel[key])
        difference = _relative_difference(parallel_value, serial_value)
        comparisons[key] = {
            "serial": serial_value,
            "parallel": parallel_value,
            "relativeDifference": difference,
            "limit": DEFAULT_PARALLEL_QOI_RELATIVE_TOLERANCE,
            "passed": difference <= DEFAULT_PARALLEL_QOI_RELATIVE_TOLERANCE,
        }
    mass_imbalance = relative_mass_flow_imbalance(
        float(parallel["inletMassFlowRateKgPerS"]), float(parallel["outletMassFlowRateKgPerS"])
    )
    mass_balance = {
        "value": mass_imbalance,
        "limit": 0.001,
        "passed": mass_imbalance <= 0.001,
    }
    return {
        "relativeTolerance": DEFAULT_PARALLEL_QOI_RELATIVE_TOLERANCE,
        "comparisons": comparisons,
        "massBalance": mass_balance,
        "passed": all(item["passed"] for item in comparisons.values()) and mass_balance["passed"],
    }


def _require_parallel_runtime_matches_serial(
    output_dir: Path,
    *,
    image: str,
    platform: str,
) -> dict[str, Any]:
    """Reject an MPI timing comparison made with a different solver runtime."""

    runtime_path = output_dir / "serial" / "fine" / "runtime" / "runtime.json"
    try:
        serial_runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StraightPipeRunError(f"could not read accepted serial runtime: {runtime_path}") from exc
    if serial_runtime.get("containerImage") != image or serial_runtime.get("containerPlatform") != platform:
        raise StraightPipeRunError(
            "parallel execution must use the same image and platform as the accepted serial V&V run"
        )
    serial_image = serial_runtime.get("containerImageProvenance")
    current_image = _container_image_provenance(image)
    if isinstance(serial_image, dict) and serial_image.get("captureStatus") == "captured":
        if current_image.get("captureStatus") != "captured":
            raise StraightPipeRunError(
                "parallel execution cannot verify the immutable image identity recorded by serial V&V"
            )
        if current_image.get("imageId") != serial_image.get("imageId"):
            raise StraightPipeRunError(
                "parallel execution must use the identical immutable Docker image recorded by serial V&V"
            )
    return serial_runtime


def _package_parallel_evidence(
    output_dir: Path,
    *,
    records: Sequence[dict[str, Any]],
    result_path: Path,
    qoi_table_path: Path,
    serial_artifact_integrity: dict[str, Any],
) -> dict[str, Path]:
    """Bind MPI logs, reconstructed fields, and QoI gates into a hash index.

    This package is deliberately separate from the immutable serial evidence:
    parallel execution happens only after serial gates pass and must not mutate
    the serial artifact index that authorized it.
    """

    artifacts_dir = output_dir / "artifacts"
    case_dirs: list[Path] = []
    run_records: list[dict[str, Any]] = []
    for record in records:
        rank_count = int(record["rankCount"])
        case_dir = output_dir / "parallel" / f"mpi-{rank_count}"
        manifest_path = case_dir / "parallel-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StraightPipeRunError(f"parallel manifest is missing or malformed: {manifest_path}") from exc
        if manifest.get("sourceInputTreeSha256") != manifest.get("copiedInputTreeSha256"):
            raise StraightPipeRunError(f"parallel input digest mismatch for {case_dir}")
        case_dirs.append(case_dir)
        run_records.append(
            {
                "rankCount": rank_count,
                "parallelManifest": _artifact_record(output_dir, "parallel-manifest", manifest_path),
                "sourceInputTreeSha256": manifest["sourceInputTreeSha256"],
                "copiedInputTreeSha256": manifest["copiedInputTreeSha256"],
                "numericsPreserved": manifest.get("numericsPreserved") is True,
                "qoiExtractionPreserved": manifest.get("qoiExtractionPreserved") is True,
                "qoiEquivalencePassed": record["qoiEquivalence"]["passed"],
                "numericalConvergencePassed": record["runtime"]["numericalConvergence"]["passed"],
            }
        )

    runtime_provenance = _uniform_runtime_provenance(
        [dict(record["runtime"]) for record in records]
    )
    archive_path = _create_archive(
        output_dir,
        artifacts_dir / "parallel-run-artifacts.tar.gz",
        case_dirs,
    )
    serial_index_path = artifacts_dir / "artifact-index.json"
    serial_candidate_path = artifacts_dir / "candidate-report.json"
    serial_evidence_path = artifacts_dir / "evidence-package.json"
    parallel_evidence = {
        "schema": PARALLEL_EVIDENCE_SCHEMA,
        "caseId": "straight-pipe",
        "status": "captured-awaiting-independent-review",
        "scientificStatus": "analysis-only",
        "validated": False,
        "serialEvidence": {
            "candidateReport": _artifact_record(output_dir, "serial-candidate-report", serial_candidate_path),
            "evidencePackage": _artifact_record(output_dir, "serial-evidence-package", serial_evidence_path),
            "artifactIndex": _artifact_record(output_dir, "serial-artifact-index", serial_index_path),
            "artifactIntegrity": serial_artifact_integrity,
        },
        "parallelRuns": run_records,
        "provenance": runtime_provenance,
        "evidencePackagingRunnerSourceSha256": _runner_source_sha256(),
        "requiredReview": {
            "reviewStillRequired": True,
            "rationale": (
                "Hash binding proves local artifact consistency, not independent scientific validation "
                "or portable performance."
            ),
        },
    }
    parallel_evidence_path = artifacts_dir / "parallel-evidence-package.json"
    _write_json(parallel_evidence_path, parallel_evidence)
    parallel_index_path = artifacts_dir / "parallel-artifact-index.json"
    _write_json(
        parallel_index_path,
        {
            "schema": PARALLEL_EVIDENCE_SCHEMA,
            "caseId": "straight-pipe",
            "status": "captured-awaiting-independent-review",
            "files": [
                _artifact_record(output_dir, "parallel-run-artifacts", archive_path),
                _artifact_record(output_dir, "parallel-benchmark", result_path),
                _artifact_record(output_dir, "parallel-qoi-table", qoi_table_path),
                _artifact_record(output_dir, "parallel-evidence-package", parallel_evidence_path),
            ],
        },
    )
    return {
        "evidencePackage": parallel_evidence_path,
        "artifactIndex": parallel_index_path,
        "runArchive": archive_path,
    }


def run_parallel_benchmarks(
    output_dir: Path,
    *,
    spec: StraightPipeRunSpec | None = None,
    ranks: Sequence[int] = DEFAULT_PARALLEL_RANKS,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
) -> dict[str, Any]:
    """Run 2- and 4-rank exact-input benchmarks after serial gate acceptance."""

    output_dir = output_dir.resolve()
    _accepted_parallel_candidate(output_dir)
    serial_artifact_integrity = _require_serial_artifact_integrity(output_dir)
    _require_parallel_runtime_matches_serial(output_dir, image=image, platform=platform)
    selected_spec = _existing_run_spec(output_dir, spec)
    selected_ranks = tuple(int(value) for value in ranks)
    if selected_ranks != DEFAULT_PARALLEL_RANKS:
        raise StraightPipeRunError("the first parallel benchmark must run exactly 2 and 4 MPI ranks")
    serial = _serial_fine_parallel_baseline(output_dir)
    records: list[dict[str, Any]] = []
    for rank_count in selected_ranks:
        case_dir = materialize_parallel_case(output_dir, ranks=rank_count)
        runtime = run_parallel_case(case_dir, ranks=rank_count, image=image, platform=platform)
        observation = _serial_level_observation(
            case_dir,
            spec=selected_spec,
            mesh_size_m=selected_spec.mesh_sizes_m[-1],
        )
        equivalence = _parallel_qoi_equivalence(serial=serial, parallel=observation)
        solver_clock_time = runtime["solverClockTimeSeconds"]
        records.append(
            {
                "rankCount": rank_count,
                "runtime": runtime,
                "qoi": observation,
                "qoiEquivalence": equivalence,
                "solverClockSpeedup": serial["solverClockTimeSeconds"] / solver_clock_time,
                "solverClockParallelEfficiency": (
                    serial["solverClockTimeSeconds"] / solver_clock_time / rank_count
                ),
            }
        )
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    passed = all(record["qoiEquivalence"]["passed"] for record in records)
    result = {
        "schema": RUNNER_SCHEMA,
        "caseId": "straight-pipe",
        "status": "parallel-qoi-equivalence-passed" if passed else "parallel-qoi-equivalence-failed",
        "scientificStatus": "analysis-only",
        "validated": False,
        "serialBaseline": serial,
        "serialArtifactIntegrity": serial_artifact_integrity,
        "runs": records,
        "performanceScope": {
            "speedupMetric": "OpenFOAM solver ClockTime only",
            "excludedFromSpeedup": "different end-to-end setup/decomposition/reconstruction work",
            "hostSpecificCaveat": "linux/amd64 execution may be emulated on non-amd64 hosts",
        },
    }
    result_path = artifacts_dir / "parallel-benchmark.json"
    _write_json(result_path, result)
    qoi_table_path = artifacts_dir / "parallel-qoi-table.csv"
    _write_text(
        qoi_table_path,
        "ranks,solver_clock_time_s,solver_clock_speedup,solver_clock_parallel_efficiency,pressure_drop_pa,full_pipe_flow_m3_s,qoi_equivalence_passed\n"
        + "".join(
            f"{record['rankCount']},{record['runtime']['solverClockTimeSeconds']:.17g},"
            f"{record['solverClockSpeedup']:.17g},{record['solverClockParallelEfficiency']:.17g},"
            f"{record['qoi']['pressureDropPa']:.17g},"
            f"{record['qoi']['fullPipeVolumetricFlowRateM3PerS']:.17g},"
            f"{str(record['qoiEquivalence']['passed']).lower()}\n"
            for record in records
        ),
    )
    parallel_evidence = _package_parallel_evidence(
        output_dir,
        records=records,
        result_path=result_path,
        qoi_table_path=qoi_table_path,
        serial_artifact_integrity=serial_artifact_integrity,
    )
    return {
        **result,
        "parallelEvidencePackage": parallel_evidence["evidencePackage"],
        "parallelArtifactIndex": parallel_evidence["artifactIndex"],
        "parallelRunArchive": parallel_evidence["runArchive"],
    }


def _serial_trial_input_records(case_dir: Path) -> dict[str, str]:
    """Hash every solver-defining input used by an isolated serial timing trial."""

    records = _input_file_digests(case_dir)
    run_script = case_dir / "run_level.sh"
    if not run_script.is_file():
        raise StraightPipeRunError(f"serial timing trial is missing run_level.sh: {run_script}")
    return {**records, "run_level.sh": _sha256_file(run_script)}


def _timing_serial_gates(
    *,
    observation: dict[str, Any],
    runtime: dict[str, Any],
    baseline: dict[str, float],
    inputs_unchanged: bool,
    reference_pressure_drop_pa: float,
) -> dict[str, Any]:
    """Apply the accepted fine-grid gates to one serial timing trial."""

    pressure_error = _relative_difference(observation["pressureDropPa"], reference_pressure_drop_pa)
    mass_imbalance = relative_mass_flow_imbalance(
        observation["inletMassFlowRateKgPerS"], observation["outletMassFlowRateKgPerS"]
    )
    equivalence = _parallel_qoi_equivalence(serial=baseline, parallel=observation)
    gates = {
        "numericalConvergence": {
            "passed": runtime["numericalConvergence"]["passed"],
        },
        "analyticalPressureDropError": {
            "value": pressure_error,
            "limit": 0.05,
            "passed": pressure_error <= 0.05,
        },
        "massBalance": {
            "value": mass_imbalance,
            "limit": 0.001,
            "passed": mass_imbalance <= 0.001,
        },
        "flowRateTarget": {
            "value": observation["flowRateRelativeError"],
            "limit": 1.0e-6,
            "passed": observation["flowRateRelativeError"] <= 1.0e-6,
        },
        "inputTreeUnchanged": {"passed": inputs_unchanged},
        "qoiEquivalenceToVerifiedFine": equivalence,
    }
    return {"gates": gates, "passed": all(gate["passed"] for gate in gates.values())}


def _run_replicated_timing_trial(
    *,
    output_dir: Path,
    source_case: Path,
    trial_dir: Path,
    phase: str,
    trial_number: int,
    spec: StraightPipeRunSpec,
    baseline: dict[str, float],
    image: str,
    platform: str,
    cpu_allocations: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Run one gated fine-grid serial/2-rank/4-rank timing trial."""

    trial_dir = trial_dir.resolve()
    record: dict[str, Any] = {
        "schema": REPLICATED_TIMING_SCHEMA,
        "phase": phase,
        "trialNumber": trial_number,
        "status": "started",
        "sourceFineCase": _source_case_label(source_case, output_dir),
        "cpuAllocations": {str(ranks): allocation for ranks, allocation in cpu_allocations.items()},
    }
    trial_manifest_path = trial_dir / "trial-manifest.json"
    try:
        serial_case = trial_dir / "serial" / "fine"
        serial_input_manifest = materialize_serial_trial_case(
            source_case, serial_case, evidence_root=output_dir
        )
        before_records = _serial_trial_input_records(serial_case)
        serial_runtime = run_serial_case(
            serial_case,
            image=image,
            platform=platform,
            cpu_set=cpu_allocations[1]["requestedCpuSet"],
        )
        after_records = _serial_trial_input_records(serial_case)
        serial_observation = _serial_level_observation(
            serial_case, spec=spec, mesh_size_m=spec.mesh_sizes_m[-1]
        )
        serial_gate_result = _timing_serial_gates(
            observation=serial_observation,
            runtime=serial_runtime,
            baseline=baseline,
            inputs_unchanged=before_records == after_records,
            reference_pressure_drop_pa=spec.reference()["pressureDropPa"],
        )
        record["serial"] = {
            "inputManifest": serial_input_manifest,
            "inputTreeBeforeSha256": _digest_file_records(before_records),
            "inputTreeAfterSha256": _digest_file_records(after_records),
            "runtime": serial_runtime,
            "qoi": serial_observation,
            "gates": serial_gate_result,
        }
        if not serial_gate_result["passed"]:
            record["status"] = "failed"
            record["failureReason"] = "serial timing trial did not preserve the verified fine-grid gates"
            _write_json(trial_manifest_path, record)
            return record

        parallel_records: list[dict[str, Any]] = []
        for ranks in (2, 4):
            parallel_case = trial_dir / "parallel" / f"mpi-{ranks}"
            materialize_parallel_case_from_source(
                serial_case,
                parallel_case,
                ranks=ranks,
                evidence_root=output_dir,
            )
            parallel_runtime = run_parallel_case(
                parallel_case,
                ranks=ranks,
                image=image,
                platform=platform,
                cpu_set=cpu_allocations[ranks]["requestedCpuSet"],
            )
            parallel_observation = _serial_level_observation(
                parallel_case, spec=spec, mesh_size_m=spec.mesh_sizes_m[-1]
            )
            qoi_equivalence = _parallel_qoi_equivalence(
                serial={
                    **baseline,
                    "pressureDropPa": serial_observation["pressureDropPa"],
                    "computedKinematicPressureGradientMPerS2": serial_observation[
                        "computedKinematicPressureGradientMPerS2"
                    ],
                    "fullPipeVolumetricFlowRateM3PerS": serial_observation[
                        "fullPipeVolumetricFlowRateM3PerS"
                    ],
                    "inletMassFlowRateKgPerS": serial_observation["inletMassFlowRateKgPerS"],
                    "outletMassFlowRateKgPerS": serial_observation["outletMassFlowRateKgPerS"],
                },
                parallel=parallel_observation,
            )
            parallel_records.append(
                {
                    "rankCount": ranks,
                    "runtime": parallel_runtime,
                    "qoi": parallel_observation,
                    "qoiEquivalenceToSerialTrial": qoi_equivalence,
                    "solverClockSpeedup": (
                        serial_runtime["solverClockTimeSeconds"]
                        / parallel_runtime["solverClockTimeSeconds"]
                    ),
                    "solverClockParallelEfficiency": (
                        serial_runtime["solverClockTimeSeconds"]
                        / parallel_runtime["solverClockTimeSeconds"]
                        / ranks
                    ),
                }
            )
            if not qoi_equivalence["passed"]:
                break
        record["parallel"] = parallel_records
        parallel_passed = len(parallel_records) == 2 and all(
            item["qoiEquivalenceToSerialTrial"]["passed"] for item in parallel_records
        )
        record["status"] = "passed" if parallel_passed else "failed"
        if not parallel_passed:
            record["failureReason"] = "parallel QoI equivalence failed or did not complete for every rank count"
    except StraightPipeRunError as exc:
        record["status"] = "failed"
        record["failureReason"] = str(exc)
    _write_json(trial_manifest_path, record)
    return record


def _timing_summary_from_trials(trials: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize only passing measured trials; warm-ups are deliberately excluded."""

    measured = [
        trial
        for trial in trials
        if trial.get("phase") == "measurement" and trial.get("status") == "passed"
    ]
    if not measured:
        return None
    if any("serial" not in trial or len(trial.get("parallel", [])) != 2 for trial in measured):
        return None
    result: dict[str, Any] = {
        "serial": {
            "solverClockTimeSeconds": summarize_numeric_trials(
                trial["serial"]["runtime"]["solverClockTimeSeconds"] for trial in measured
            ),
            "hostWallTimeSeconds": summarize_numeric_trials(
                trial["serial"]["runtime"]["hostWallTimeSeconds"] for trial in measured
            ),
        }
    }
    for ranks in (2, 4):
        records = [
            next(item for item in trial["parallel"] if item["rankCount"] == ranks) for trial in measured
        ]
        result[f"mpi-{ranks}"] = {
            "solverClockTimeSeconds": summarize_numeric_trials(
                item["runtime"]["solverClockTimeSeconds"] for item in records
            ),
            "hostWallTimeSeconds": summarize_numeric_trials(
                item["runtime"]["hostWallTimeSeconds"] for item in records
            ),
            "pairedSolverClockSpeedup": summarize_numeric_trials(
                item["solverClockSpeedup"] for item in records
            ),
            "pairedSolverClockParallelEfficiency": summarize_numeric_trials(
                item["solverClockParallelEfficiency"] for item in records
            ),
        }
    return result


def _timing_csv(trials: Sequence[dict[str, Any]]) -> str:
    rows = [
        "phase,trial,status,ranks,solver_clock_time_s,host_wall_time_s,solver_clock_speedup,solver_clock_parallel_efficiency,qoi_equivalence_passed"
    ]
    for trial in trials:
        phase = trial.get("phase", "")
        number = trial.get("trialNumber", "")
        status = trial.get("status", "")
        serial = trial.get("serial")
        if isinstance(serial, dict) and isinstance(serial.get("runtime"), dict):
            runtime = serial["runtime"]
            rows.append(
                f"{phase},{number},{status},1,{runtime['solverClockTimeSeconds']:.17g},"
                f"{runtime['hostWallTimeSeconds']:.17g},,,"
            )
        for parallel in trial.get("parallel", []):
            runtime = parallel["runtime"]
            equivalence = parallel["qoiEquivalenceToSerialTrial"]["passed"]
            rows.append(
                f"{phase},{number},{status},{parallel['rankCount']},"
                f"{runtime['solverClockTimeSeconds']:.17g},{runtime['hostWallTimeSeconds']:.17g},"
                f"{parallel['solverClockSpeedup']:.17g},{parallel['solverClockParallelEfficiency']:.17g},"
                f"{str(equivalence).lower()}"
            )
    return "\n".join(rows) + "\n"


def run_replicated_timing(
    output_dir: Path,
    *,
    spec: StraightPipeRunSpec | None = None,
    image: str = DEFAULT_IMAGE,
    platform: str = DEFAULT_PLATFORM,
    warmup_trials: int = DEFAULT_TIMING_WARMUP_TRIALS,
    measurement_trials: int = DEFAULT_TIMING_MEASUREMENT_TRIALS,
    cpu_sets: dict[int, str],
) -> dict[str, Any]:
    """Run a native-only, repeated exact-input timing protocol.

    A preflight failure writes an explicit environment record and performs no
    solver work.  A passing preflight first runs fresh three-grid V&V and then
    measures only independently copied fine-grid serial/2-rank/4-rank trials.
    """

    if not isinstance(warmup_trials, int) or isinstance(warmup_trials, bool) or warmup_trials < 0:
        raise StraightPipeRunError("warmup_trials must be a non-negative integer")
    if (
        not isinstance(measurement_trials, int)
        or isinstance(measurement_trials, bool)
        or measurement_trials < 3
    ):
        raise StraightPipeRunError("measurement_trials must be an integer of at least 3")
    selected_spec = spec or default_run_spec()
    cpu_allocations = _validate_timing_cpu_sets(cpu_sets)
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise StraightPipeRunError(f"refusing to overwrite non-empty timing output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        environment = _capture_native_timing_preflight(
            image=image, platform=platform, cpu_allocations=cpu_allocations
        )
    except StraightPipeRunError as exc:
        environment = {
            "schema": REPLICATED_TIMING_SCHEMA,
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "requestedImage": image,
            "requestedPlatform": platform,
            "nativeTimingPermitted": False,
            "failureReasons": [str(exc)],
            "executionQualification": {
                "architectureNative": False,
                "physicalCorePinningClaimed": False,
                "performanceClaim": "no performance timing is permitted because environment capture failed",
            },
        }
    environment_path = artifacts_dir / "timing-environment.json"
    _write_json(environment_path, environment)
    protocol = {
        "warmupTrials": warmup_trials,
        "measurementTrials": measurement_trials,
        "qoiRelativeTolerance": DEFAULT_PARALLEL_QOI_RELATIVE_TOLERANCE,
        "cpuAllocations": {str(ranks): allocation for ranks, allocation in cpu_allocations.items()},
    }
    if environment.get("nativeTimingPermitted") is not True:
        result = {
            "schema": REPLICATED_TIMING_SCHEMA,
            "status": "native-preflight-failed",
            "scientificStatus": "analysis-only",
            "validated": False,
            "protocol": protocol,
            "timingEnvironment": "artifacts/timing-environment.json",
            "failureReasons": environment.get("failureReasons", []),
        }
        _write_json(artifacts_dir / "timing-summary.json", result)
        return result

    verification_dir = output_dir / "verification"
    try:
        verification = run_serial_suite(
            verification_dir,
            spec=selected_spec,
            image=image,
            platform=platform,
            cpu_set=cpu_allocations[1]["requestedCpuSet"],
        )
    except StraightPipeRunError as exc:
        result = {
            "schema": REPLICATED_TIMING_SCHEMA,
            "status": "verification-execution-failed",
            "scientificStatus": "analysis-only",
            "validated": False,
            "protocol": protocol,
            "timingEnvironment": "artifacts/timing-environment.json",
            "failureReasons": [str(exc)],
        }
        _write_json(artifacts_dir / "timing-summary.json", result)
        return result
    if verification["serialGatesPassed"] is not True:
        result = {
            "schema": REPLICATED_TIMING_SCHEMA,
            "status": "verification-gates-failed",
            "scientificStatus": "analysis-only",
            "validated": False,
            "protocol": protocol,
            "timingEnvironment": "artifacts/timing-environment.json",
            "verification": {
                "directory": "verification",
                "candidateReport": "verification/artifacts/candidate-report.json",
                "serialGates": verification["gates"],
            },
        }
        _write_json(artifacts_dir / "timing-summary.json", result)
        return result

    source_case = verification_dir / "serial" / "fine"
    baseline = _serial_fine_parallel_baseline(verification_dir)
    trial_records: list[dict[str, Any]] = []
    for phase, count in (("warmup", warmup_trials), ("measurement", measurement_trials)):
        for trial_number in range(1, count + 1):
            trial = _run_replicated_timing_trial(
                output_dir=output_dir,
                source_case=source_case,
                trial_dir=output_dir / "trials" / f"{phase}-{trial_number:03d}",
                phase=phase,
                trial_number=trial_number,
                spec=selected_spec,
                baseline=baseline,
                image=image,
                platform=platform,
                cpu_allocations=cpu_allocations,
            )
            trial_records.append(trial)
            if trial["status"] != "passed":
                break
        if trial_records and trial_records[-1]["status"] != "passed":
            break

    trials_path = artifacts_dir / "timing-trials.json"
    _write_json(trials_path, {"schema": REPLICATED_TIMING_SCHEMA, "trials": trial_records})
    summary_values = _timing_summary_from_trials(trial_records)
    completed_measurements = sum(
        trial.get("phase") == "measurement" and trial.get("status") == "passed"
        for trial in trial_records
    )
    timing_passed = completed_measurements == measurement_trials and all(
        trial.get("status") == "passed" for trial in trial_records
    )
    result = {
        "schema": REPLICATED_TIMING_SCHEMA,
        "status": "replicated-timing-passed" if timing_passed else "replicated-timing-failed",
        "scientificStatus": "analysis-only",
        "validated": False,
        "protocol": protocol,
        "timingEnvironment": "artifacts/timing-environment.json",
        "verification": {
            "directory": "verification",
            "candidateReport": "verification/artifacts/candidate-report.json",
            "serialGates": verification["gates"],
        },
        "trialArtifact": "artifacts/timing-trials.json",
        "measurementSummary": summary_values,
        "completedMeasurementTrials": completed_measurements,
        "executionQualification": environment["executionQualification"],
    }
    _write_json(artifacts_dir / "timing-summary.json", result)
    _write_text(artifacts_dir / "timing-summary.csv", _timing_csv(trial_records))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize or run the FlowLab straight-pipe verification suite."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--image",
        default=_openfoam_image(),
        help=(
            "Pinned OpenFOAM runtime image used for materialized provenance and execution "
            "(default: FLOWLAB_OPENFOAM_IMAGE or the repository-pinned image)."
        ),
    )
    parser.add_argument(
        "--platform",
        default=DEFAULT_PLATFORM,
        help="Docker target platform, which must match serial and MPI evidence.",
    )
    parser.add_argument(
        "--mesh-recipe",
        choices=(FULL_PIPE_OGRID_MESH_RECIPE, SECTOR90_MESH_RECIPE),
        default=None,
        help=(
            "Mesh family for a new materialized/serial suite. Existing runs use their frozen manifest "
            "unless the supplied recipe exactly matches it."
        ),
    )
    parser.add_argument(
        "--ogrid-azimuthal-cells-per-quadrant",
        type=int,
        help=(
            "Resolved outer-wall cells per quadrant for a new full-pipe O-grid suite. "
            "Use with --ogrid-core-cells-per-side; both must match for a conforming O-grid."
        ),
    )
    parser.add_argument(
        "--ogrid-core-cells-per-side",
        type=int,
        help=(
            "Resolved central-diamond cells per side for a new full-pipe O-grid suite. "
            "Use with --ogrid-azimuthal-cells-per-quadrant."
        ),
    )
    parser.add_argument(
        "--materialize-only",
        action="store_true",
        help="Write the three serial cases without starting Docker.",
    )
    parser.add_argument(
        "--run-serial",
        action="store_true",
        help="Materialize and execute the three serial refinements, then evaluate their gates.",
    )
    parser.add_argument(
        "--package-existing-serial",
        action="store_true",
        help="Package an already complete, isolated three-level serial run without rerunning it.",
    )
    parser.add_argument(
        "--run-parallel",
        action="store_true",
        help="After passed serial gates, run the accepted fine mesh at 2 and 4 MPI ranks.",
    )
    parser.add_argument(
        "--run-replicated-timing",
        action="store_true",
        help=(
            "Native-only timing: run fresh V&V, then repeated exact-input fine-grid serial/2-rank/4-rank trials."
        ),
    )
    parser.add_argument(
        "--serial-cpuset",
        help="Docker logical CPU set for serial timing trials, for example 0.",
    )
    parser.add_argument(
        "--mpi2-cpuset",
        help="Docker logical CPU set for 2-rank timing trials, for example 0-1.",
    )
    parser.add_argument(
        "--mpi4-cpuset",
        help="Docker logical CPU set for 4-rank timing trials, for example 0-3.",
    )
    parser.add_argument(
        "--warmup-trials",
        type=int,
        default=DEFAULT_TIMING_WARMUP_TRIALS,
        help="Warm-up timing trials to gate but exclude from summary statistics (default: 1).",
    )
    parser.add_argument(
        "--measurement-trials",
        type=int,
        default=DEFAULT_TIMING_MEASUREMENT_TRIALS,
        help="Measured timing trials to summarize; must be at least 3 (default: 5).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ogrid_counts_requested = (
        args.ogrid_azimuthal_cells_per_quadrant is not None
        or args.ogrid_core_cells_per_side is not None
    )
    if ogrid_counts_requested and args.mesh_recipe == SECTOR90_MESH_RECIPE:
        raise StraightPipeRunError("O-grid resolution options require the full-pipe O-grid mesh recipe")
    requested_spec_values: dict[str, Any] = {}
    if args.mesh_recipe is not None:
        requested_spec_values["mesh_recipe"] = args.mesh_recipe
    if args.ogrid_azimuthal_cells_per_quadrant is not None:
        requested_spec_values["ogrid_azimuthal_cells_per_quadrant"] = (
            args.ogrid_azimuthal_cells_per_quadrant
        )
    if args.ogrid_core_cells_per_side is not None:
        requested_spec_values["ogrid_core_cells_per_side"] = args.ogrid_core_cells_per_side
    requested_spec = StraightPipeRunSpec(**requested_spec_values) if requested_spec_values else None
    if args.materialize_only:
        materialize_serial_cases(
            args.output_dir,
            spec=requested_spec,
            image=args.image,
            platform=args.platform,
        )
        return 0
    if args.run_serial:
        result = run_serial_suite(
            args.output_dir,
            spec=requested_spec,
            image=args.image,
            platform=args.platform,
        )
    elif args.package_existing_serial:
        result = package_existing_serial_suite(args.output_dir, spec=requested_spec)
    elif args.run_parallel:
        result = run_parallel_benchmarks(
            args.output_dir,
            spec=requested_spec,
            image=args.image,
            platform=args.platform,
        )
    elif args.run_replicated_timing:
        if None in (args.serial_cpuset, args.mpi2_cpuset, args.mpi4_cpuset):
            raise StraightPipeRunError(
                "--run-replicated-timing requires --serial-cpuset, --mpi2-cpuset, and --mpi4-cpuset"
            )
        result = run_replicated_timing(
            args.output_dir,
            spec=requested_spec,
            image=args.image,
            platform=args.platform,
            warmup_trials=args.warmup_trials,
            measurement_trials=args.measurement_trials,
            cpu_sets={1: args.serial_cpuset, 2: args.mpi2_cpuset, 4: args.mpi4_cpuset},
        )
    else:
        raise StraightPipeRunError(
            "select --materialize-only, --run-serial, --package-existing-serial, --run-parallel, "
            "or --run-replicated-timing"
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if args.run_parallel:
        return 0 if result["status"] == "parallel-qoi-equivalence-passed" else 2
    if args.run_replicated_timing:
        return 0 if result["status"] == "replicated-timing-passed" else 2
    return 0 if result["serialGatesPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
