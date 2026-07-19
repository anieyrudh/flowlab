"""FDA benchmark nozzle Re=500, strict all-hex validation campaign.

The module deliberately separates four phases:

* freeze the campaign contract and ingest the official experimental archive;
* generate nested, strict-hexahedral OpenFOAM cases;
* execute the cases in a pinned OpenFOAM 11 container; and
* assess grid, iterative, input, and experimental uncertainty without
  authorizing a product claim unless every mandatory gate passes.

The primary configuration is the FDA ``Sudden Expansion`` orientation.  The
coordinate origin is the sudden expansion and flow is in the positive x
direction.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
from typing import Any, Iterable, Sequence
import zipfile

from .cad_parabolic_smoke import (
    _nonuniform_scalar_field,
    _nonuniform_vector_field,
    _read_cell_centres,
)
from .open_boundary_affine_flux_pressure_probe import _boundary_vectors
from .open_boundary_mms_runner import _header, _values, _write


SCHEMA = "flowlab.fda-nozzle-re500-campaign.v1"
CASE_SCHEMA = "flowlab.fda-nozzle-re500-case.v1"
EXPERIMENT_SCHEMA = "flowlab.fda-nozzle-experiment.v1"
ASSESSMENT_SCHEMA = "flowlab.fda-nozzle-re500-assessment.v1"

OFFICIAL_REPOSITORY = "https://github.com/OSEL-DAM/CFD-and-Blood-Damage-Benchmarks"
OFFICIAL_COMMIT = "76cdd3423845a398fc75f8121be4130caca4de90"
OFFICIAL_ARCHIVE_PATH = "Nozzle/Data/SE_exp_0500.zip"
OFFICIAL_ARCHIVE_SHA256 = "c33d8d604c072edd0298890274e9b8d625bb39281e2b691c51dac639aaf95d58"
OFFICIAL_DATASET_DOI = "10.17917/C78G69"
PRIMARY_PAPER_DOI = "10.1115/1.4003440"
WSS_UQ_PAPER_DOI = "10.1007/s13239-015-0251-9"

DEFAULT_IMAGE = "flowlab/openfoam11-gmsh415-immutable:2026-07-14-arm64-v1"
DEFAULT_IMAGE_DIGEST = "sha256:6a6ac1898cb482ae16ff65cb538f458f9ff86941ac9f7e435088a3c54357ce36"

LEVELS = (("coarse", 1), ("medium", 2), ("fine", 4))
SENSITIVITY_CASES = (("input-minus-5pct", 2, 0.95), ("input-plus-5pct", 2, 1.05))
PROFILE_STATIONS_M = (
    -0.088,
    -0.064,
    -0.048,
    -0.020,
    -0.008,
    0.000,
    0.008,
    0.016,
    0.024,
    0.032,
    0.060,
    0.080,
)
PRIMARY_PROFILE_STATIONS_M = (-0.088, -0.048, -0.008, 0.008, 0.024, 0.080)
PRESSURE_CODES = (243, 468, 763)

END_ITERATION = 800
WRITE_INTERVAL = 50
LINEAR_TOLERANCE = 1.0e-12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    def json_safe(item: Any) -> Any:
        if isinstance(item, float) and not math.isfinite(item):
            return None
        if isinstance(item, dict):
            return {key: json_safe(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [json_safe(child) for child in item]
        return item

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(json_safe(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


@dataclass(frozen=True)
class FdaNozzleDefinition:
    density_kg_m3: float = 1056.0
    dynamic_viscosity_pa_s: float = 0.0035
    volumetric_flow_rate_m3_s: float = 5.20624e-6
    inlet_radius_m: float = 0.006
    throat_radius_m: float = 0.002
    throat_length_m: float = 0.040
    contraction_included_angle_deg: float = 20.0
    inlet_x_m: float = -0.120
    outlet_x_m: float = 0.120
    throat_start_x_m: float = -0.040
    sudden_expansion_x_m: float = 0.0

    @property
    def kinematic_viscosity_m2_s(self) -> float:
        return self.dynamic_viscosity_pa_s / self.density_kg_m3

    @property
    def throat_mean_velocity_m_s(self) -> float:
        return self.volumetric_flow_rate_m3_s / (
            math.pi * self.throat_radius_m**2
        )

    @property
    def inlet_mean_velocity_m_s(self) -> float:
        return self.volumetric_flow_rate_m3_s / (
            math.pi * self.inlet_radius_m**2
        )

    @property
    def throat_reynolds_number(self) -> float:
        return (
            self.density_kg_m3
            * self.throat_mean_velocity_m_s
            * 2.0
            * self.throat_radius_m
            / self.dynamic_viscosity_pa_s
        )

    @property
    def contraction_start_x_m(self) -> float:
        half_angle = math.radians(self.contraction_included_angle_deg / 2.0)
        axial_length = (
            self.inlet_radius_m - self.throat_radius_m
        ) / math.tan(half_angle)
        return self.throat_start_x_m - axial_length

    def radius(self, x: float, *, downstream_inner: bool = False) -> float:
        if x < self.contraction_start_x_m:
            return self.inlet_radius_m
        if x < self.throat_start_x_m:
            return self.throat_radius_m + (
                self.throat_start_x_m - x
            ) * math.tan(math.radians(self.contraction_included_angle_deg / 2.0))
        if x <= self.sudden_expansion_x_m or downstream_inner:
            return self.throat_radius_m
        return self.inlet_radius_m

    def inlet_velocity(self, point: Sequence[float], flow_scale: float = 1.0) -> tuple[float, float, float]:
        _, y, z = point
        radial_squared = y * y + z * z
        value = 2.0 * self.inlet_mean_velocity_m_s * flow_scale * max(
            0.0, 1.0 - radial_squared / self.inlet_radius_m**2
        )
        return (value, 0.0, 0.0)

    def initial_velocity(self, point: Sequence[float], flow_scale: float = 1.0) -> tuple[float, float, float]:
        x, y, z = point
        radius = self.radius(x)
        if x > 0.0 and y * y + z * z > self.throat_radius_m**2:
            return (0.0, 0.0, 0.0)
        mean = (
            self.volumetric_flow_rate_m3_s
            * flow_scale
            / (math.pi * radius**2)
        )
        radial_squared = y * y + z * z
        return (
            2.0 * mean * max(0.0, 1.0 - radial_squared / radius**2),
            0.0,
            0.0,
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "flowlab.fda-nozzle-definition.v1",
            "configuration": "sudden-expansion",
            "coordinateSystem": "x=0 at sudden expansion; positive x is flow direction",
            "parameters": asdict(self),
            "derived": {
                "kinematicViscosityM2PerS": self.kinematic_viscosity_m2_s,
                "inletMeanVelocityMPerS": self.inlet_mean_velocity_m_s,
                "throatMeanVelocityMPerS": self.throat_mean_velocity_m_s,
                "throatReynoldsNumber": self.throat_reynolds_number,
                "contractionStartXM": self.contraction_start_x_m,
            },
        }


def predeclared_contract(spec: FdaNozzleDefinition | None = None) -> dict[str, Any]:
    spec = spec or FdaNozzleDefinition()
    return {
        "schema": SCHEMA,
        "campaignId": "fda-nozzle-re500-v1",
        "frozenAt": "2026-07-17T00:00:00+00:00",
        "claimUnderTest": "FlowLab's laminar OpenFOAM execution has passed an independent experimental CFD benchmark.",
        "definition": spec.manifest(),
        "boundaryConditions": {
            "inletVelocity": "fixedValue, exact fully-developed parabolic profile at the nominal experimental flow rate",
            "inletPressure": "fixedFluxPressure",
            "outletVelocity": "zeroGradient",
            "outletPressure": "fixedValue, zero gauge kinematic pressure",
            "wallVelocity": "no-slip fixedValue",
            "wallPressure": "fixedFluxPressure",
        },
        "mesh": {
            "family": "nested multi-block O-grid",
            "levels": [
                {"name": name, "linearRefinement": refinement}
                for name, refinement in LEVELS
            ],
            "refinementRatio": 2.0,
            "requiredCellType": "hex",
            "disallowedCellTypes": ["tet", "prism", "pyramid", "wedge", "polyhedron"],
            "checkMesh": ["-allTopology", "-allGeometry"],
        },
        "comparisons": {
            "axialVelocityProfiles": {
                "role": "mandatory-validation",
                "stationsM": list(PRIMARY_PROFILE_STATIONS_M),
                "gate": "at least 90% of eligible points satisfy |E| <= U_val and station normalized RMSE <= 0.10",
            },
            "radialVelocityProfiles": {
                "role": "mandatory-reporting-nonpromotional",
                "stationsM": list(PROFILE_STATIONS_M),
                "reason": "the companion interlaboratory paper says the legacy radial measurements were not reliable in low-radial-velocity and jet regions",
            },
            "centrelineVelocity": {
                "role": "mandatory-validation",
                "gate": "at least 90% of eligible points satisfy |E| <= U_val",
            },
            "centrelineAndWallPressure": {
                "role": "mandatory-validation",
                "eligibleExperimentCodes": list(PRESSURE_CODES),
                "reference": "pressure differences relative to x=0",
                "gate": "at least 90% of eligible wall-pressure points satisfy |E| <= U_val and pressure-drop |E| <= U_val",
            },
            "wallShearViscousTraction": {
                "role": "mandatory-reporting-nonpromotional",
                "reason": "legacy wall-shear values were omitted from the primary CFD comparison as unreliable; later pointwise UQ is not machine-readable in the official archive",
                "laterStudyMedian95PctUncertaintyDyneCm2AtRe500": [0.799, 1.948, 1.303, 5.471, 3.253],
            },
            "flowConservation": {
                "role": "mandatory-validation",
                "gate": "maximum relative section or boundary flow error <= 1e-6",
            },
            "forceObjectVsDirectFaceIntegration": {
                "role": "mandatory-verification",
                "gate": "relative difference <= 1e-10 or absolute difference <= 1e-12 N",
            },
        },
        "uncertainty": {
            "experimental": "pointwise 95% Student-t interval across official blinded trials; pressure uses the three published-eligible Re=500 series",
            "input": "central finite-difference sensitivity from medium-grid Q +/-5% cases",
            "iterative": "absolute change between the final two 50-iteration output intervals",
            "grid": "three-grid observed order and fine-grid GCI with Fs=1.25; nonmonotonic points are explicitly unqualified",
            "validation": "U_val=sqrt(U_exp^2+U_input^2+U_iter^2+U_grid^2); ASME V&V 20 comparison passes pointwise when |E|<=U_val",
        },
        "mandatoryGates": {
            "sourcePinned": True,
            "allMeshesStrictHexAndCheckMesh": True,
            "allNominalSolversComplete": True,
            "iterativeUncertaintyResolved": True,
            "threeGridGciResolved": True,
            "inputUncertaintyResolved": True,
            "flowConservation": True,
            "forceReconciliation": True,
            "axialVelocityValidation": True,
            "pressureValidation": True,
        },
        "sourceRegister": [
            {
                "title": "FDA CFD and Blood Damage Benchmarks",
                "url": OFFICIAL_REPOSITORY,
                "commit": OFFICIAL_COMMIT,
                "path": OFFICIAL_ARCHIVE_PATH,
                "sha256": OFFICIAL_ARCHIVE_SHA256,
                "doi": OFFICIAL_DATASET_DOI,
            },
            {"title": "Hariharan et al. interlaboratory PIV", "doi": PRIMARY_PAPER_DOI},
            {"title": "Raben et al. time-resolved PIV and WSS UQ", "doi": WSS_UQ_PAPER_DOI},
        ],
        "promotionRule": "The claim is authorized only when every mandatory gate is true. Diagnostic radial-velocity and wall-shear comparisons must be reported but cannot rescue or defeat the claim without an eligible pointwise uncertainty dataset.",
    }


def _vertex_text(point: tuple[float, float, float]) -> str:
    return f"({point[0]:.17g} {point[1]:.17g} {point[2]:.17g})"


class _BlockMeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[tuple[float, float, float]] = []
        self.vertex_index: dict[tuple[float, float, float], int] = {}
        self.blocks: list[str] = []
        self.edges: set[tuple[int, int, tuple[float, float, float]]] = set()
        self.boundary: dict[str, list[tuple[int, int, int, int]]] = {
            "inlet": [],
            "outlet": [],
            "wall": [],
        }

    def vertex(self, point: tuple[float, float, float]) -> int:
        key = tuple(round(value, 15) for value in point)
        if key not in self.vertex_index:
            self.vertex_index[key] = len(self.vertices)
            self.vertices.append(point)
        return self.vertex_index[key]

    def arc(self, start: int, end: int, midpoint: tuple[float, float, float]) -> None:
        key = (min(start, end), max(start, end), midpoint)
        self.edges.add(key)

    def block(self, left: Sequence[int], right: Sequence[int], cells: tuple[int, int, int]) -> None:
        vertices = tuple(left) + tuple(right)
        self.blocks.append(
            "hex (" + " ".join(str(value) for value in vertices) + ") "
            f"({cells[0]} {cells[1]} {cells[2]}) simpleGrading (1 1 1)"
        )


def _plane(
    builder: _BlockMeshBuilder,
    x: float,
    radius: float,
    *,
    core_half_width: float,
    prefix: str,
) -> dict[str, list[int]]:
    del prefix
    core_coordinates = (
        (-core_half_width, -core_half_width),
        (core_half_width, -core_half_width),
        (core_half_width, core_half_width),
        (-core_half_width, core_half_width),
    )
    diagonal = radius / math.sqrt(2.0)
    ring_coordinates = (
        (-diagonal, -diagonal),
        (diagonal, -diagonal),
        (diagonal, diagonal),
        (-diagonal, diagonal),
    )
    core = [builder.vertex((x, y, z)) for y, z in core_coordinates]
    ring = [builder.vertex((x, y, z)) for y, z in ring_coordinates]
    midpoints = ((0.0, -radius), (radius, 0.0), (0.0, radius), (-radius, 0.0))
    for index in range(4):
        builder.arc(ring[index], ring[(index + 1) % 4], (x, *midpoints[index]))
    return {"core": core, "ring": ring}


def _quads(plane: dict[str, list[int]]) -> list[list[int]]:
    core = plane["core"]
    ring = plane["ring"]
    return [
        list(core),
        [core[0], ring[0], ring[1], core[1]],
        [core[1], ring[1], ring[2], core[2]],
        [core[2], ring[2], ring[3], core[3]],
        [core[3], ring[3], ring[0], core[0]],
    ]


def block_mesh_dict(refinement: int, spec: FdaNozzleDefinition | None = None) -> str:
    if refinement not in {1, 2, 4}:
        raise ValueError("refinement must be one of 1, 2, or 4")
    spec = spec or FdaNozzleDefinition()
    builder = _BlockMeshBuilder()

    upstream_x = (spec.inlet_x_m, spec.contraction_start_x_m, spec.throat_start_x_m, 0.0)
    upstream_planes: list[dict[str, list[int]]] = []
    for index, x in enumerate(upstream_x):
        radius = spec.radius(x - (1.0e-12 if x == 0.0 else 0.0))
        upstream_planes.append(
            _plane(
                builder,
                x,
                radius,
                core_half_width=radius / 2.0,
                prefix=f"u{index}",
            )
        )

    n_tangent = 2 * refinement
    n_radial = 1 * refinement
    n_core = n_tangent
    target_dx = 0.001 / refinement
    for segment, (left, right) in enumerate(zip(upstream_planes, upstream_planes[1:])):
        dx = upstream_x[segment + 1] - upstream_x[segment]
        n_axial = max(1, round(dx / target_dx))
        left_quads = _quads(left)
        right_quads = _quads(right)
        builder.block(left_quads[0], right_quads[0], (n_core, n_core, n_axial))
        for left_quad, right_quad in zip(left_quads[1:], right_quads[1:]):
            builder.block(left_quad, right_quad, (n_radial, n_tangent, n_axial))
            builder.boundary["wall"].append(
                (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
            )

    inlet_quads = _quads(upstream_planes[0])
    builder.boundary["inlet"].extend(tuple(quad) for quad in inlet_quads)

    inner_start = upstream_planes[-1]
    inner_end = _plane(
        builder,
        spec.outlet_x_m,
        spec.throat_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="di",
    )
    outer_start_ring_plane = _plane(
        builder,
        0.0,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do0",
    )
    outer_end_ring_plane = _plane(
        builder,
        spec.outlet_x_m,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do1",
    )
    # Reuse the throat-radius ring as the inner boundary of the outer annulus.
    outer_start_ring_plane["core"] = inner_start["ring"]
    outer_end_ring_plane["core"] = inner_end["ring"]

    downstream_axial = round((spec.outlet_x_m - 0.0) / target_dx)
    inner_left_quads = _quads(inner_start)
    inner_right_quads = _quads(inner_end)
    builder.block(
        inner_left_quads[0], inner_right_quads[0], (n_core, n_core, downstream_axial)
    )
    for left_quad, right_quad in zip(inner_left_quads[1:], inner_right_quads[1:]):
        builder.block(
            left_quad,
            right_quad,
            (n_radial, n_tangent, downstream_axial),
        )

    outer_radial = 4 * refinement
    outer_left_quads = _quads(outer_start_ring_plane)[1:]
    outer_right_quads = _quads(outer_end_ring_plane)[1:]
    for left_quad, right_quad in zip(outer_left_quads, outer_right_quads):
        builder.block(
            left_quad,
            right_quad,
            (outer_radial, n_tangent, downstream_axial),
        )
        builder.boundary["wall"].append(tuple(left_quad))
        builder.boundary["wall"].append(
            (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
        )

    outlet_quads = _quads(inner_end) + outer_right_quads
    builder.boundary["outlet"].extend(tuple(reversed(quad)) for quad in outlet_quads)

    vertex_text = "\n".join(
        f"    {_vertex_text(point)} // {index}" for index, point in enumerate(builder.vertices)
    )
    block_text = "\n".join(f"    {block}" for block in builder.blocks)
    edge_text = "\n".join(
        f"    arc {start} {end} {_vertex_text(midpoint)}"
        for start, end, midpoint in sorted(builder.edges)
    )

    def patch(name: str, kind: str) -> str:
        faces = "\n".join(
            "            (" + " ".join(str(value) for value in face) + ")"
            for face in builder.boundary[name]
        )
        return f"""    {name}
    {{
        type {kind};
        faces
        (
{faces}
        );
    }}"""

    return _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices
(
{vertex_text}
);
blocks
(
{block_text}
);
edges
(
{edge_text}
);
boundary
(
{patch('inlet', 'patch')}
{patch('outlet', 'patch')}
{patch('wall', 'wall')}
);
mergePatchPairs ();
"""


def _case_files(
    label: str,
    refinement: int,
    flow_scale: float,
    spec: FdaNozzleDefinition,
) -> dict[str, str]:
    functions = f"""
    residuals
    {{
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
        writeControl timeStep;
        writeInterval 1;
    }}
    forcesAll
    {{
        type forces;
        libs ("libforces.so");
        patches (inlet outlet wall);
        CofR (0 0 0);
        rho rhoInf;
        rhoInf {spec.density_kg_m3:.17g};
        writeControl timeStep;
        writeInterval {WRITE_INTERVAL};
    }}
    inletFlux
    {{
        type surfaceFieldValue;
        libs ("libfieldFunctionObjects.so");
        regionType patch;
        name inlet;
        operation sum;
        fields (phi);
        writeFields false;
        writeControl timeStep;
        writeInterval {WRITE_INTERVAL};
    }}
    outletFlux
    {{
        type surfaceFieldValue;
        libs ("libfieldFunctionObjects.so");
        regionType patch;
        name outlet;
        operation sum;
        fields (phi);
        writeFields false;
        writeControl timeStep;
        writeInterval {WRITE_INTERVAL};
    }}
"""
    return {
        "system/blockMeshDict": block_mesh_dict(refinement, spec),
        "0/U": _header("0", "U", "volVectorField")
        + """dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    inlet { type fixedValue; value uniform (0 0 0); }
    outlet { type zeroGradient; }
    wall { type fixedValue; value uniform (0 0 0); }
}
""",
        "0/p": _header("0", "p", "volScalarField")
        + """dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    inlet { type fixedFluxPressure; value uniform 0; gradient uniform 0; }
    outlet { type fixedValue; value uniform 0; }
    wall { type fixedFluxPressure; value uniform 0; gradient uniform 0; }
}
""",
        "constant/physicalProperties": _header("constant", "physicalProperties")
        + "viscosityModel constant;\n"
        + f"nu [0 2 -1 0 0 0 0] {spec.kinematic_viscosity_m2_s:.17g};\n",
        "constant/momentumTransport": _header("constant", "momentumTransport")
        + "simulationType laminar;\n",
        "system/controlDict": _header("system", "controlDict")
        + f"""application foamRun;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime {END_ITERATION};
deltaT 1;
writeControl timeStep;
writeInterval {WRITE_INTERVAL};
purgeWrite 3;
writeFormat ascii;
writePrecision 16;
runTimeModifiable false;
functions
{{
{functions}
}}
""",
        "system/fvSchemes": _header("system", "fvSchemes")
        + """ddtSchemes { default steadyState; }
gradSchemes { default cellLimited Gauss linear 1; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss linearUpwind grad(U);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear limited 0.5; }
interpolationSchemes { default linear; }
snGradSchemes { default limited 0.5; }
wallDist { method meshWave; }
""",
        "system/fvSolution": _header("system", "fvSolution")
        + f"""solvers
{{
    p
    {{
        solver GAMG;
        smoother GaussSeidel;
        tolerance {LINEAR_TOLERANCE:.17g};
        relTol 0;
    }}
    pFinal {{ $p; relTol 0; }}
    U
    {{
        solver PBiCGStab;
        preconditioner DILU;
        tolerance {LINEAR_TOLERANCE:.17g};
        relTol 0;
    }}
    UFinal {{ $U; relTol 0; }}
}}
SIMPLE
{{
    nNonOrthogonalCorrectors 1;
    consistent yes;
}}
relaxationFactors
{{
    fields {{ p 0.3; }}
    equations {{ U 0.7; }}
}}
""",
        "case-definition.json": json.dumps(
            {
                "schema": CASE_SCHEMA,
                "label": label,
                "refinement": refinement,
                "flowScale": flow_scale,
                "endIteration": END_ITERATION,
                "writeInterval": WRITE_INTERVAL,
                "definition": spec.manifest(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }


def prepare_case(
    case: Path,
    label: str,
    refinement: int,
    flow_scale: float = 1.0,
    spec: FdaNozzleDefinition | None = None,
) -> None:
    spec = spec or FdaNozzleDefinition()
    if case.exists() and any(case.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty case: {case}")
    for relative, content in _case_files(label, refinement, flow_scale, spec).items():
        _write(case / relative, content)


def _foam_points(path: Path) -> list[tuple[float, float, float]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)",
            text,
        )
    ]


def _foam_faces(path: Path) -> list[list[int]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [
        [int(value) for value in body.split()]
        for _, body in re.findall(r"(\d+)\s*\(([^()]*)\)", text)
    ]


def _patch_range(path: Path, patch: str) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf"\b{re.escape(patch)}\s*\{{(.*?)\}}",
        text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"patch {patch!r} not found in {path}")
    body = match.group(1)
    n_faces = re.search(r"\bnFaces\s+(\d+)\s*;", body)
    start_face = re.search(r"\bstartFace\s+(\d+)\s*;", body)
    if not n_faces or not start_face:
        raise ValueError(f"patch {patch!r} has no face range in {path}")
    return int(start_face.group(1)), int(n_faces.group(1))


def _polygon_area(points: Sequence[tuple[float, float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    origin = points[0]
    area_vector = [0.0, 0.0, 0.0]
    for left, right in zip(points[1:-1], points[2:]):
        a = tuple(left[index] - origin[index] for index in range(3))
        b = tuple(right[index] - origin[index] for index in range(3))
        cross = (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )
        for index in range(3):
            area_vector[index] += 0.5 * cross[index]
    return math.sqrt(sum(value * value for value in area_vector))


def _patch_face_areas(case: Path, patch: str) -> list[float]:
    poly_mesh = case / "constant" / "polyMesh"
    points = _foam_points(poly_mesh / "points")
    faces = _foam_faces(poly_mesh / "faces")
    start, count = _patch_range(poly_mesh / "boundary", patch)
    return [
        _polygon_area([points[index] for index in face])
        for face in faces[start : start + count]
    ]


def initialize_case(case: Path, flow_scale: float = 1.0, spec: FdaNozzleDefinition | None = None) -> None:
    spec = spec or FdaNozzleDefinition()
    centres = _read_cell_centres(case / "0/C")
    patches = _boundary_vectors(case / "0/C", ("inlet", "outlet", "wall"))
    internal_u = [spec.initial_velocity(point, flow_scale) for point in centres]
    raw_inlet_u = [spec.inlet_velocity(point, flow_scale) for point in patches["inlet"]]
    inlet_areas = _patch_face_areas(case, "inlet")
    if len(inlet_areas) != len(raw_inlet_u):
        raise ValueError("inlet face geometry does not match inlet boundary values")
    raw_flow = sum(value[0] * area for value, area in zip(raw_inlet_u, inlet_areas))
    target_flow = spec.volumetric_flow_rate_m3_s * flow_scale
    discrete_scale = target_flow / raw_flow
    inlet_u = [
        (value[0] * discrete_scale, value[1], value[2])
        for value in raw_inlet_u
    ]
    _write(
        case / "0/U",
        _header("0", "U", "volVectorField")
        + "dimensions [0 1 -1 0 0 0 0];\n"
        + f"internalField {_nonuniform_vector_field(internal_u)};\n"
        + "boundaryField\n{\n"
        + f" inlet {{ type fixedValue; value {_nonuniform_vector_field(inlet_u)}; }}\n"
        + " outlet { type zeroGradient; }\n"
        + " wall { type fixedValue; value uniform (0 0 0); }\n"
        + "}\n",
    )
    _write_json(
        case / "initialization.json",
        {
            "schema": "flowlab.fda-nozzle-initialization.v1",
            "flowScale": flow_scale,
            "continuumTargetFlowM3PerS": target_flow,
            "rawFaceCentreQuadratureFlowM3PerS": raw_flow,
            "discreteProfileScale": discrete_scale,
            "normalizedDiscreteFlowM3PerS": sum(
                value[0] * area for value, area in zip(inlet_u, inlet_areas)
            ),
            "boundaryCondition": "fixedValue nonuniform parabolic shape normalized to exact discrete flow",
        },
    )
    internal_p = [0.0 for _ in centres]
    inlet_p = [0.0 for _ in patches["inlet"]]
    wall_p = [0.0 for _ in patches["wall"]]
    _write(
        case / "0/p",
        _header("0", "p", "volScalarField")
        + "dimensions [0 2 -2 0 0 0 0];\n"
        + f"internalField {_nonuniform_scalar_field(internal_p)};\n"
        + "boundaryField\n{\n"
        + f" inlet {{ type fixedFluxPressure; value {_nonuniform_scalar_field(inlet_p)}; gradient uniform 0; }}\n"
        + " outlet { type fixedValue; value uniform 0; }\n"
        + f" wall {{ type fixedFluxPressure; value {_nonuniform_scalar_field(wall_p)}; gradient uniform 0; }}\n"
        + "}\n",
    )


def _parse_experimental_file(path: Path) -> dict[str, Any]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    result: dict[str, Any] = {"path": str(path), "plots": {}}
    index = 0
    while index < len(lines):
        line = lines[index]
        scalar = re.fullmatch(r"(dataset-code|dataset-reynolds|fluid-density|fluid-viscosity|fluid-volumetric-flow-rate)\s+(.+)", line)
        if scalar:
            key, raw = scalar.groups()
            value = float(raw)
            result[key] = int(value) if key in {"dataset-code", "dataset-reynolds"} else value
            index += 1
            continue
        if line.startswith("plot-") or line.startswith("deleted-plot-"):
            deleted = line.startswith("deleted-")
            name = line.removeprefix("deleted-")
            station = None
            match = re.fullmatch(r"(plot-profile-(?:axial|radial)-velocity-at-z)\s+([-+0-9.eE]+)\s+\d+", name)
            if match:
                name = match.group(1)
                station = float(match.group(2))
            index += 1
            count = int(lines[index])
            rows: list[list[float]] = []
            for row in lines[index + 1 : index + 1 + count]:
                fields = row.split()
                if len(fields) >= 2:
                    rows.append([float(fields[0]), float(fields[1])])
            key = name if station is None else f"{name}@{station:.6f}"
            result["plots"][key] = {"deleted": deleted, "rows": rows, "stationM": station}
            index += count + 1
            continue
        index += 1
    return result


def ingest_experiment(archive: Path, output: Path) -> dict[str, Any]:
    if _sha256(archive) != OFFICIAL_ARCHIVE_SHA256:
        raise ValueError("official FDA Re=500 archive hash mismatch")
    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pinned_archive = raw / archive.name
    shutil.copy2(archive, pinned_archive)
    extracted = raw / "extracted"
    with zipfile.ZipFile(pinned_archive) as bundle:
        bundle.extractall(extracted)
    files = sorted(extracted.glob("experiment/*.txt"))
    parsed = [_parse_experimental_file(path) for path in files]
    report = {
        "schema": EXPERIMENT_SCHEMA,
        "source": {
            "repository": OFFICIAL_REPOSITORY,
            "commit": OFFICIAL_COMMIT,
            "path": OFFICIAL_ARCHIVE_PATH,
            "doi": OFFICIAL_DATASET_DOI,
            "sha256": OFFICIAL_ARCHIVE_SHA256,
            "pinnedArchive": str(pinned_archive),
        },
        "files": parsed,
        "pressureEligibility": {
            "codes": list(PRESSURE_CODES),
            "rationale": "the primary paper reports n=3 eligible Re=500 pressure series; repository code 297 is explicitly deleted and code 999 is the additional published-excluded outlying pressure series",
        },
        "limitations": {
            "radialVelocity": "reported but nonpromotional because the primary paper identifies unreliable low-radial-velocity and jet-region measurements",
            "wallShear": "reported but nonpromotional because the primary paper omitted legacy WSS as unreliable and the later pointwise UQ data are not machine-readable in this archive",
        },
    }
    _write_json(output / "experimental-data.json", report)
    return report


_T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def _expanded_mean_uncertainty(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        raise ValueError("cannot summarize an empty experimental sample")
    mean = statistics.fmean(values)
    if len(values) == 1:
        return {"mean": mean, "sampleStd": None, "n": 1, "u95": None}
    sample_std = statistics.stdev(values)
    t95 = _T95.get(len(values), 1.96)
    return {
        "mean": mean,
        "sampleStd": sample_std,
        "n": len(values),
        "u95": t95 * sample_std / math.sqrt(len(values)),
    }


def _aggregate_plot(
    experiment: dict[str, Any],
    key: str,
    *,
    eligible_codes: set[int] | None = None,
    reference_x: float | None = None,
) -> list[dict[str, Any]]:
    series: list[list[tuple[float, float]]] = []
    for item in experiment["files"]:
        code = int(item["dataset-code"])
        if eligible_codes is not None and code not in eligible_codes:
            continue
        plot = item["plots"].get(key)
        if not plot or plot.get("deleted"):
            continue
        rows = plot["rows"]
        reference = 0.0
        if reference_x is not None:
            candidates = [row for row in rows if abs(row[0] - reference_x) <= 1.0e-9]
            if not candidates:
                raise ValueError(f"missing reference x={reference_x} for {key}, code {code}")
            reference = candidates[0][1]
        within_file: dict[float, list[float]] = {}
        for coordinate, value in rows:
            within_file.setdefault(round(coordinate, 12), []).append(value - reference)
        series.append(
            sorted(
                (coordinate, statistics.fmean(values))
                for coordinate, values in within_file.items()
            )
        )
    if not series:
        return []

    def interpolate(rows: Sequence[tuple[float, float]], coordinate: float) -> float | None:
        if coordinate < rows[0][0] or coordinate > rows[-1][0]:
            return None
        for left, right in zip(rows, rows[1:]):
            if abs(coordinate - left[0]) <= 1.0e-12:
                return left[1]
            if left[0] <= coordinate <= right[0]:
                if abs(right[0] - left[0]) <= 1.0e-15:
                    return statistics.fmean((left[1], right[1]))
                weight = (coordinate - left[0]) / (right[0] - left[0])
                return left[1] + weight * (right[1] - left[1])
        if abs(coordinate - rows[-1][0]) <= 1.0e-12:
            return rows[-1][1]
        return None

    grouped: dict[float, list[float]] = {}
    for coordinate, _ in series[0]:
        values = [value for rows in series if (value := interpolate(rows, coordinate)) is not None]
        grouped[coordinate] = values
    return [
        {"coordinateM": coordinate, **_expanded_mean_uncertainty(values)}
        for coordinate, values in sorted(grouped.items())
    ]


def experimental_summary(experiment: dict[str, Any]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for station in PROFILE_STATIONS_M:
        suffix = f"@{station:.6f}"
        profiles[f"{station:.6f}"] = {
            "axial": _aggregate_plot(
                experiment,
                "plot-profile-axial-velocity-at-z" + suffix,
            ),
            "radial": _aggregate_plot(
                experiment,
                "plot-profile-radial-velocity-at-z" + suffix,
            ),
        }
    return {
        "schema": "flowlab.fda-nozzle-experimental-summary.v1",
        "axialVelocityProfiles": profiles,
        "centrelineAxialVelocity": _aggregate_plot(
            experiment, "plot-z-distribution-axial-velocity"
        ),
        "wallPressureRelativeToExpansion": _aggregate_plot(
            experiment,
            "plot-wall-distribution-pressure",
            eligible_codes=set(PRESSURE_CODES),
            reference_x=0.0,
        ),
        "wallShearLegacy": _aggregate_plot(
            experiment, "plot-wall-distribution-wall-shear-stress"
        ),
        "roles": {
            "axialVelocityProfiles": "mandatory-validation",
            "centrelineAxialVelocity": "mandatory-validation",
            "wallPressureRelativeToExpansion": "mandatory-validation",
            "radialVelocityProfiles": "mandatory-reporting-nonpromotional",
            "wallShearLegacy": "mandatory-reporting-nonpromotional",
        },
    }


def _probe_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    nozzle = FdaNozzleDefinition()
    records: list[dict[str, Any]] = []
    for row in summary["centrelineAxialVelocity"]:
        x = float(row["coordinateM"])
        records.append(
            {
                "qoi": "centrelineVelocity",
                "coordinateM": x,
                "point": [x, 0.0, 0.0],
            }
        )
    for row in summary["wallPressureRelativeToExpansion"]:
        x = float(row["coordinateM"])
        records.append(
            {
                "qoi": "centrelinePressure",
                "coordinateM": x,
                "point": [x if x != 0.0 else 1.0e-10, 0.0, 0.0],
            }
        )
    for station_text, components in summary["axialVelocityProfiles"].items():
        station = float(station_text)
        # The experimental x=0 profile is the downstream expansion plane.
        sample_x = station if station != 0.0 else 1.0e-10
        coordinates = sorted(
            {
                float(row["coordinateM"])
                for component in ("axial", "radial")
                for row in components[component]
                if abs(float(row["coordinateM"]))
                <= nozzle.radius(sample_x) + 1.0e-12
            }
        )
        for radial in coordinates:
            records.append(
                {
                    "qoi": "velocityProfile",
                    "stationM": station,
                    "radialCoordinateM": radial,
                    "point": [sample_x, radial, 0.0],
                }
            )
    for index, record in enumerate(records):
        record["probeIndex"] = index
    return records


def write_probe_dictionary(case: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    records = _probe_records(summary)
    locations = "\n".join(
        "    (" + " ".join(f"{value:.17g}" for value in record["point"]) + ")"
        for record in records
    )
    content = _header("system", "fdaProbes") + f"""type probes;
libs ("libsampling.so");
writeControl timeStep;
writeInterval 1;
fields (U p);
fixedLocations true;
interpolationScheme cellPoint;
probeLocations
(
{locations}
);
"""
    _write(case / "system" / "fdaProbes", content)
    _write_json(case / "probe-map.json", {"records": records})
    return records


def _probe_output(case: Path, field: str) -> Path:
    candidates = sorted((case / "postProcessing" / "fdaProbes").glob(f"**/{field}"))
    if not candidates:
        raise ValueError(f"missing fdaProbes output for {field}")
    return candidates[-1]


def _parse_probe_output(path: Path, vector: bool) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        time_match = re.match(r"\s*([-+0-9.eE]+)\s+", line)
        if not time_match:
            continue
        time = f"{float(time_match.group(1)):g}"
        body = line[time_match.end() :]
        if vector:
            result[time] = [
                [float(component) for component in match]
                for match in re.findall(
                    r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)",
                    body,
                )
            ]
        else:
            result[time] = [float(value) for value in body.split()]
    return result


def _direct_integrals(path: Path) -> dict[str, Any]:
    groups = {name: {"pressure": [0.0, 0.0, 0.0], "viscous": [0.0, 0.0, 0.0]} for name in ("inlet", "outlet", "wall", "all")}
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        patch = row["patch"]
        for kind, prefix in (("pressure", "pressure_force"), ("viscous", "viscous_force")):
            vector = [float(row[f"{prefix}_{axis}_n"]) for axis in "xyz"]
            for index, value in enumerate(vector):
                groups[patch][kind][index] += value
                groups["all"][kind][index] += value
    groups["faceCount"] = len(rows)  # type: ignore[assignment]
    return groups


def _wall_samples(path: Path, coordinates: Sequence[float]) -> list[dict[str, Any]]:
    """Area-average rings and interpolate wall QoIs to exact tap coordinates.

    Expansion-plane faces are deliberately excluded using their axial normal.  This
    leaves the cylindrical/conical wall surface on which the experimental pressure
    taps and legacy wall-shear measurements were located.
    """
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [
            row
            for row in csv.DictReader(stream)
            if row["patch"] == "wall" and abs(float(row["n_x"])) < 0.5
        ]
    if not rows:
        raise ValueError(f"no axial wall rings found in {path}")
    axial_locations = sorted({float(row["cf_x"]) for row in rows})
    rings: dict[float, dict[str, Any]] = {}
    for axial in axial_locations:
        ring = [row for row in rows if abs(float(row["cf_x"]) - axial) <= 1.0e-12]
        area = sum(float(row["area"]) for row in ring)
        if area <= 0.0:
            raise ValueError(f"nonpositive wall-ring area at x={axial}")
        pressure = sum(
            float(row["area"]) * float(row["pressure_pa"]) for row in ring
        ) / area
        traction_magnitude = 0.0
        for row in ring:
            normal = [float(row[f"n_{axis}"]) for axis in "xyz"]
            traction = [float(row[f"traction_{axis}_pa"]) for axis in "xyz"]
            normal_component = sum(a * b for a, b in zip(traction, normal))
            tangential = [
                component - normal_component * direction
                for component, direction in zip(traction, normal)
            ]
            traction_magnitude += float(row["area"]) * math.sqrt(
                sum(component * component for component in tangential)
            )
        rings[axial] = {
            "faceCount": len(ring),
            "areaM2": area,
            "pressurePa": pressure,
            "tangentialTractionPa": traction_magnitude / area,
        }

    samples: list[dict[str, Any]] = []
    for requested in coordinates:
        if abs(requested) <= 1.0e-15:
            # The experimental expansion reference is on the downstream outer
            # wall; interpolation across the step would be nonphysical.
            bracket = [min(value for value in axial_locations if value > 0.0)]
        else:
            left = [value for value in axial_locations if value <= requested]
            right = [value for value in axial_locations if value >= requested]
            if left and right:
                bracket = [max(left), min(right)]
            else:
                bracket = [
                    min(axial_locations, key=lambda value: abs(value - requested))
                ]
        bracket = list(dict.fromkeys(bracket))
        if len(bracket) == 1:
            axial = bracket[0]
            weight = 0.0
            lower = upper = rings[axial]
            sampled_coordinate = axial
        else:
            lower_x, upper_x = bracket
            lower, upper = rings[lower_x], rings[upper_x]
            weight = (requested - lower_x) / (upper_x - lower_x)
            sampled_coordinate = requested

        def interpolate(name: str) -> float:
            return float(lower[name]) + weight * (
                float(upper[name]) - float(lower[name])
            )

        samples.append(
            {
                "requestedCoordinateM": requested,
                "sampledCoordinateM": sampled_coordinate,
                "bracketingCoordinatesM": bracket,
                "faceCount": int(lower["faceCount"]) + (
                    int(upper["faceCount"]) if len(bracket) == 2 else 0
                ),
                "areaM2": interpolate("areaM2"),
                "pressurePa": interpolate("pressurePa"),
                "tangentialTractionPa": interpolate("tangentialTractionPa"),
            }
        )
    return samples


def _force_object(case: Path, time: str) -> dict[str, list[float]]:
    paths = sorted((case / "postProcessing" / "forcesAll").glob("**/forces.dat"))
    if not paths:
        raise ValueError("missing OpenFOAM forcesAll output")
    rows = [
        line
        for line in paths[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    target = next(
        (line for line in reversed(rows) if f"{float(line.split()[0]):g}" == time),
        None,
    )
    if target is None:
        raise ValueError(f"missing forces at time {time}")
    vectors = re.findall(
        r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)",
        target,
    )
    return {
        "pressure": [float(value) for value in vectors[0]],
        "viscous": [float(value) for value in vectors[1]],
    }


def _relative_flux_error(case: Path, time: str, target: float) -> dict[str, float]:
    values: dict[str, float] = {}
    for name in ("inletFlux", "outletFlux"):
        path = next((case / "postProcessing" / name).glob("**/surfaceFieldValue.dat"))
        rows = [
            line.split()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        row = next(item for item in reversed(rows) if f"{float(item[0]):g}" == time)
        values[name] = float(row[1])
    values["boundaryClosureRelative"] = abs(values["inletFlux"] + values["outletFlux"]) / target
    values["inletTargetRelative"] = abs(abs(values["inletFlux"]) - target) / target
    values["outletTargetRelative"] = abs(abs(values["outletFlux"]) - target) / target
    return values


def postprocess_case(output: Path, label: str, image: str = DEFAULT_IMAGE) -> dict[str, Any]:
    case = output / "cases" / label
    result = output / "results" / label
    result.mkdir(parents=True, exist_ok=True)
    definition = json.loads((case / "case-definition.json").read_text(encoding="utf-8"))
    experiment = json.loads((output / "experiment" / "experimental-data.json").read_text(encoding="utf-8"))
    summary = experimental_summary(experiment)
    records = write_probe_dictionary(case, summary)
    workspace = Path(__file__).resolve().parents[2]
    probe_code = run_command(
        _container_command(
            image,
            workspace,
            case,
            "foamPostProcess -func fdaProbes -time '750,800'",
        ),
        case,
        output / "logs" / label / "fdaProbes.log",
    )
    audit_codes: dict[str, int] = {}
    for time in ("750", "800"):
        csv_path = result / f"face-integration-{time}.csv"
        command = (
            f"{output}/bin/flowlabFdaPatchAudit -time {time} "
            f"-rho {FdaNozzleDefinition().density_kg_m3:.17g} -output {csv_path}"
        )
        audit_codes[time] = run_command(
            _container_command(image, workspace, case, command),
            case,
            output / "logs" / label / f"face-integration-{time}.log",
        )
    if probe_code != 0 or any(code != 0 for code in audit_codes.values()):
        report = {
            "schema": CASE_SCHEMA,
            "label": label,
            "status": "postprocessing-failed",
            "probeExitCode": probe_code,
            "auditExitCodes": audit_codes,
            "promotionAuthorized": False,
        }
        _write_json(result / "observation.json", report)
        return report
    u_by_time = _parse_probe_output(_probe_output(case, "U"), True)
    p_by_time = _parse_probe_output(_probe_output(case, "p"), False)
    observations_by_time: dict[str, Any] = {}
    target_flow = FdaNozzleDefinition().volumetric_flow_rate_m3_s * float(definition["flowScale"])
    wall_coordinates = sorted(
        {
            float(row["coordinateM"])
            for row in summary["wallPressureRelativeToExpansion"]
        }
        | {
            float(row["coordinateM"])
            for row in summary["wallShearLegacy"]
        }
    )
    for time in ("750", "800"):
        if len(u_by_time.get(time, [])) != len(records) or len(p_by_time.get(time, [])) != len(records):
            raise ValueError(f"probe output count mismatch at time {time}")
        probes = []
        for record, velocity, pressure in zip(records, u_by_time[time], p_by_time[time]):
            probes.append({**record, "velocityMPerS": velocity, "pressurePa": pressure * FdaNozzleDefinition().density_kg_m3})
        direct = _direct_integrals(result / f"face-integration-{time}.csv")
        wall_samples = _wall_samples(
            result / f"face-integration-{time}.csv", wall_coordinates
        )
        force = _force_object(case, time)
        delta = max(
            abs(force[kind][index] - direct["all"][kind][index])
            for kind in ("pressure", "viscous")
            for index in range(3)
        )
        force_scale = max(
            1.0e-300,
            *(abs(value) for kind in ("pressure", "viscous") for value in force[kind]),
        )
        observations_by_time[time] = {
            "probes": probes,
            "directFaceIntegration": direct,
            "wallSamples": wall_samples,
            "openFoamForces": force,
            "forceObjectVsDirectAbsoluteN": delta,
            "forceObjectVsDirectRelative": delta / force_scale,
            "flow": _relative_flux_error(case, time, target_flow),
        }
    report = {
        "schema": CASE_SCHEMA,
        "label": label,
        "status": "observed",
        "definition": definition,
        "mesh": json.loads((result / "execution.json").read_text(encoding="utf-8"))["mesh"],
        "probeCount": len(records),
        "times": observations_by_time,
        "checks": {
            "probePostprocessingComplete": True,
            "directIntegrationComplete": True,
            "flowConservation": max(
                observations_by_time["800"]["flow"][key]
                for key in ("boundaryClosureRelative", "inletTargetRelative", "outletTargetRelative")
            ) <= 1.0e-6,
            "forceObjectMatchesDirect": observations_by_time["800"]["forceObjectVsDirectRelative"] <= 1.0e-10
            or observations_by_time["800"]["forceObjectVsDirectAbsoluteN"] <= 1.0e-12,
        },
        "promotionAuthorized": False,
    }
    _write_json(result / "observation.json", report)
    return report


def _probe_key(record: dict[str, Any]) -> tuple[Any, ...]:
    if record["qoi"] == "velocityProfile":
        return (
            record["qoi"],
            round(float(record["stationM"]), 12),
            round(float(record["radialCoordinateM"]), 12),
        )
    return (record["qoi"], round(float(record["coordinateM"]), 12))


def _probe_index(observation: dict[str, Any], time: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    return {
        _probe_key(record): record
        for record in observation["times"][time]["probes"]
    }


def _wall_index(observation: dict[str, Any], time: str) -> dict[float, dict[str, Any]]:
    return {
        round(float(record["requestedCoordinateM"]), 12): record
        for record in observation["times"][time]["wallSamples"]
    }


def _gci(coarse: float, medium: float, fine: float, refinement_ratio: float = 2.0) -> dict[str, Any]:
    values = (coarse, medium, fine)
    if not all(math.isfinite(value) for value in values):
        return {"qualified": False, "reason": "nonfinite-value"}
    delta_coarse_medium = medium - coarse
    delta_medium_fine = fine - medium
    tolerance = 1.0e-12 * max(1.0, *(abs(value) for value in values))
    if abs(delta_coarse_medium) <= tolerance and abs(delta_medium_fine) <= tolerance:
        return {
            "qualified": True,
            "convergence": "grid-invariant-to-tolerance",
            "observedOrder": None,
            "absoluteFineGridGci": 0.0,
            "relativeFineGridGci": 0.0,
        }
    if abs(delta_medium_fine) <= tolerance and abs(delta_coarse_medium) > tolerance:
        return {
            "qualified": True,
            "convergence": "fine-grid-plateau",
            "observedOrder": None,
            "absoluteFineGridGci": 0.0,
            "relativeFineGridGci": 0.0,
        }
    if (
        delta_coarse_medium * delta_medium_fine <= 0.0
        or abs(delta_coarse_medium) <= abs(delta_medium_fine)
    ):
        return {
            "qualified": False,
            "reason": "nonmonotonic-or-not-convergent",
            "deltaCoarseMedium": delta_coarse_medium,
            "deltaMediumFine": delta_medium_fine,
        }
    observed_order = math.log(
        abs(delta_coarse_medium / delta_medium_fine)
    ) / math.log(refinement_ratio)
    if not math.isfinite(observed_order) or observed_order <= 0.0:
        return {"qualified": False, "reason": "invalid-observed-order"}
    absolute_gci = (
        1.25
        * abs(delta_medium_fine)
        / (refinement_ratio**observed_order - 1.0)
    )
    return {
        "qualified": True,
        "convergence": "monotonic",
        "observedOrder": observed_order,
        "absoluteFineGridGci": absolute_gci,
        "relativeFineGridGci": absolute_gci / max(abs(fine), 1.0e-300),
    }


def _validation_row(
    *,
    experimental: dict[str, Any],
    coarse: float,
    medium: float,
    fine: float,
    fine_previous: float,
    input_minus: float,
    input_plus: float,
) -> dict[str, Any]:
    grid = _gci(coarse, medium, fine)
    experimental_eligible = (
        int(experimental.get("n", 0)) >= 3
        and experimental.get("u95") is not None
        and math.isfinite(float(experimental["mean"]))
        and math.isfinite(float(experimental["u95"]))
    )
    input_uncertainty = 0.5 * abs(input_plus - input_minus)
    iterative_uncertainty = abs(fine - fine_previous)
    result: dict[str, Any] = {
        "experiment": experimental,
        "simulation": {
            "coarse": coarse,
            "medium": medium,
            "fine": fine,
            "finePrevious": fine_previous,
            "inputMinus5PctMedium": input_minus,
            "inputPlus5PctMedium": input_plus,
        },
        "grid": grid,
        "experimentalEligible": experimental_eligible,
        "uncertainty": {
            "experimental95": experimental.get("u95"),
            "input": input_uncertainty,
            "iterative": iterative_uncertainty,
            "grid": grid.get("absoluteFineGridGci"),
        },
        "qualified": False,
        "passesVv20": False,
    }
    if not experimental_eligible or not grid["qualified"]:
        return result
    error = fine - float(experimental["mean"])
    validation_uncertainty = math.sqrt(
        float(experimental["u95"]) ** 2
        + input_uncertainty**2
        + iterative_uncertainty**2
        + float(grid["absoluteFineGridGci"]) ** 2
    )
    result.update(
        {
            "qualified": True,
            "comparisonError": error,
            "validationUncertainty": validation_uncertainty,
            "errorToValidationUncertaintyRatio": abs(error)
            / max(validation_uncertainty, 1.0e-300),
            "passesVv20": abs(error) <= validation_uncertainty,
        }
    )
    return result


def _summary_counts(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["experimentalEligible"]]
    qualified = [row for row in eligible if row["qualified"]]
    passed = [row for row in eligible if row["passesVv20"]]
    return {
        "reported": len(rows),
        "experimentalEligible": len(eligible),
        "gciQualified": len(qualified),
        "vv20Passed": len(passed),
        "gciQualifiedFraction": len(qualified) / len(eligible) if eligible else 0.0,
        "vv20PassFraction": len(passed) / len(eligible) if eligible else 0.0,
    }


def _pressure_drop_experiment(experiment: dict[str, Any], first_x: float, last_x: float) -> dict[str, Any]:
    values: list[float] = []
    for item in experiment["files"]:
        if int(item["dataset-code"]) not in PRESSURE_CODES:
            continue
        plot = item["plots"].get("plot-wall-distribution-pressure")
        if not plot or plot.get("deleted"):
            continue
        rows = {round(float(x), 12): float(value) for x, value in plot["rows"]}
        values.append(rows[round(first_x, 12)] - rows[round(last_x, 12)])
    return {
        "firstCoordinateM": first_x,
        "lastCoordinateM": last_x,
        **_expanded_mean_uncertainty(values),
    }


def assess_campaign(output: Path) -> dict[str, Any]:
    labels = [name for name, _ in LEVELS] + [name for name, _, _ in SENSITIVITY_CASES]
    observations = {
        label: json.loads(
            (output / "results" / label / "observation.json").read_text(
                encoding="utf-8"
            )
        )
        for label in labels
    }
    experiment = json.loads(
        (output / "experiment" / "experimental-data.json").read_text(
            encoding="utf-8"
        )
    )
    summary = experimental_summary(experiment)
    probes = {
        label: {
            time: _probe_index(observation, time)
            for time in ("750", "800")
        }
        for label, observation in observations.items()
    }
    walls = {
        label: {
            time: _wall_index(observation, time)
            for time in ("750", "800")
        }
        for label, observation in observations.items()
    }

    def probe_value(
        label: str, time: str, key: tuple[Any, ...], component: int
    ) -> float:
        return float(probes[label][time][key]["velocityMPerS"][component])

    def comparison_from_probe(
        experimental: dict[str, Any], key: tuple[Any, ...], component: int
    ) -> dict[str, Any]:
        return _validation_row(
            experimental=experimental,
            coarse=probe_value("coarse", "800", key, component),
            medium=probe_value("medium", "800", key, component),
            fine=probe_value("fine", "800", key, component),
            fine_previous=probe_value("fine", "750", key, component),
            input_minus=probe_value("input-minus-5pct", "800", key, component),
            input_plus=probe_value("input-plus-5pct", "800", key, component),
        )

    axial_profiles: dict[str, Any] = {}
    radial_profiles: dict[str, Any] = {}
    axial_rows_all: list[dict[str, Any]] = []
    radial_rows_all: list[dict[str, Any]] = []
    for station_text, components in summary["axialVelocityProfiles"].items():
        station = float(station_text)
        physical_radius = FdaNozzleDefinition().radius(
            station if station != 0.0 else 1.0e-10
        )
        station_axial: list[dict[str, Any]] = []
        station_radial: list[dict[str, Any]] = []
        for name, component_index, destination in (
            ("axial", 0, station_axial),
            ("radial", 1, station_radial),
        ):
            for experimental_row in components[name]:
                radial = float(experimental_row["coordinateM"])
                if abs(radial) > physical_radius + 1.0e-12:
                    continue
                key = ("velocityProfile", round(station, 12), round(radial, 12))
                row = {
                    "stationM": station,
                    "radialCoordinateM": radial,
                    **comparison_from_probe(experimental_row, key, component_index),
                }
                destination.append(row)
        eligible_axial = [row for row in station_axial if row["experimentalEligible"]]
        peak = max(
            (abs(float(row["experiment"]["mean"])) for row in eligible_axial),
            default=0.0,
        )
        nrmse = (
            math.sqrt(
                statistics.fmean(
                    (float(row["simulation"]["fine"]) - float(row["experiment"]["mean"])) ** 2
                    for row in eligible_axial
                )
            )
            / peak
            if eligible_axial and peak > 0.0
            else None
        )
        axial_profiles[station_text] = {
            "role": "mandatory-validation" if station in PRIMARY_PROFILE_STATIONS_M else "additional-reporting",
            "normalizedRmseByExperimentalPeak": nrmse,
            "counts": _summary_counts(station_axial),
            "points": station_axial,
        }
        radial_profiles[station_text] = {
            "role": "mandatory-reporting-nonpromotional",
            "counts": _summary_counts(station_radial),
            "points": station_radial,
        }
        if station in PRIMARY_PROFILE_STATIONS_M:
            axial_rows_all.extend(station_axial)
        radial_rows_all.extend(station_radial)

    centreline_rows: list[dict[str, Any]] = []
    for experimental_row in summary["centrelineAxialVelocity"]:
        coordinate = float(experimental_row["coordinateM"])
        key = ("centrelineVelocity", round(coordinate, 12))
        centreline_rows.append(
            {
                "coordinateM": coordinate,
                **comparison_from_probe(experimental_row, key, 0),
            }
        )

    pressure_coordinates = [
        float(row["coordinateM"])
        for row in summary["wallPressureRelativeToExpansion"]
    ]
    reference_key = round(0.0, 12)

    def wall_pressure(label: str, time: str, coordinate: float) -> float:
        index = walls[label][time]
        return float(index[round(coordinate, 12)]["pressurePa"]) - float(
            index[reference_key]["pressurePa"]
        )

    def centreline_pressure(label: str, time: str, coordinate: float) -> float:
        index = probes[label][time]
        return float(
            index[("centrelinePressure", round(coordinate, 12))]["pressurePa"]
        ) - float(index[("centrelinePressure", reference_key)]["pressurePa"])

    pressure_rows: list[dict[str, Any]] = []
    centreline_pressure_rows: list[dict[str, Any]] = []
    centreline_wall_differences: list[dict[str, Any]] = []
    for experimental_row in summary["wallPressureRelativeToExpansion"]:
        coordinate = float(experimental_row["coordinateM"])
        validation = _validation_row(
            experimental=experimental_row,
            coarse=wall_pressure("coarse", "800", coordinate),
            medium=wall_pressure("medium", "800", coordinate),
            fine=wall_pressure("fine", "800", coordinate),
            fine_previous=wall_pressure("fine", "750", coordinate),
            input_minus=wall_pressure("input-minus-5pct", "800", coordinate),
            input_plus=wall_pressure("input-plus-5pct", "800", coordinate),
        )
        pressure_rows.append({"coordinateM": coordinate, **validation})
        centreline_validation = _validation_row(
            experimental=experimental_row,
            coarse=centreline_pressure("coarse", "800", coordinate),
            medium=centreline_pressure("medium", "800", coordinate),
            fine=centreline_pressure("fine", "800", coordinate),
            fine_previous=centreline_pressure("fine", "750", coordinate),
            input_minus=centreline_pressure(
                "input-minus-5pct", "800", coordinate
            ),
            input_plus=centreline_pressure(
                "input-plus-5pct", "800", coordinate
            ),
        )
        centreline_pressure_rows.append(
            {"coordinateM": coordinate, **centreline_validation}
        )
        centreline = centreline_pressure("fine", "800", coordinate)
        wall = wall_pressure("fine", "800", coordinate)
        centreline_wall_differences.append(
            {
                "coordinateM": coordinate,
                "fineCentrelinePressureRelativePa": centreline,
                "fineWallPressureRelativePa": wall,
                "centrelineMinusWallPa": centreline - wall,
            }
        )

    first_pressure_x = min(pressure_coordinates)
    last_pressure_x = max(pressure_coordinates)
    pressure_drop_exp = _pressure_drop_experiment(
        experiment, first_pressure_x, last_pressure_x
    )

    def pressure_drop(label: str, time: str) -> float:
        return wall_pressure(label, time, first_pressure_x) - wall_pressure(
            label, time, last_pressure_x
        )

    pressure_drop_row = _validation_row(
        experimental=pressure_drop_exp,
        coarse=pressure_drop("coarse", "800"),
        medium=pressure_drop("medium", "800"),
        fine=pressure_drop("fine", "800"),
        fine_previous=pressure_drop("fine", "750"),
        input_minus=pressure_drop("input-minus-5pct", "800"),
        input_plus=pressure_drop("input-plus-5pct", "800"),
    )

    wall_shear_rows: list[dict[str, Any]] = []
    for experimental_row in summary["wallShearLegacy"]:
        coordinate = round(float(experimental_row["coordinateM"]), 12)

        def traction(label: str, time: str) -> float:
            return float(walls[label][time][coordinate]["tangentialTractionPa"])

        wall_shear_rows.append(
            {
                "coordinateM": float(experimental_row["coordinateM"]),
                **_validation_row(
                    experimental=experimental_row,
                    coarse=traction("coarse", "800"),
                    medium=traction("medium", "800"),
                    fine=traction("fine", "800"),
                    fine_previous=traction("fine", "750"),
                    input_minus=traction("input-minus-5pct", "800"),
                    input_plus=traction("input-plus-5pct", "800"),
                ),
            }
        )

    axial_counts = _summary_counts(axial_rows_all)
    centreline_counts = _summary_counts(centreline_rows)
    pressure_counts = _summary_counts(pressure_rows)
    centreline_pressure_counts = _summary_counts(centreline_pressure_rows)
    radial_counts = _summary_counts(radial_rows_all)
    wall_shear_counts = _summary_counts(wall_shear_rows)
    primary_station_nrmse_ok = all(
        profile["normalizedRmseByExperimentalPeak"] is not None
        and profile["normalizedRmseByExperimentalPeak"] <= 0.10
        for profile in axial_profiles.values()
        if profile["role"] == "mandatory-validation"
    )
    mandatory_rows = axial_rows_all + centreline_rows + pressure_rows + [pressure_drop_row]
    mandatory_experimental_rows = [
        row for row in mandatory_rows if row["experimentalEligible"]
    ]
    nominal_labels = [name for name, _ in LEVELS]
    all_labels_observed = all(
        observation["status"] == "observed"
        for observation in observations.values()
    )
    gates = {
        "sourcePinned": _sha256(output / "experiment" / "raw" / "SE_exp_0500.zip")
        == OFFICIAL_ARCHIVE_SHA256,
        "allMeshesStrictHexAndCheckMesh": all(
            observations[label]["mesh"].get("meshOk")
            and observations[label]["mesh"].get("strictAllHex")
            for label in nominal_labels
        ),
        "allNominalSolversComplete": all(
            observations[label]["status"] == "observed"
            for label in nominal_labels
        ),
        "inputUncertaintyResolved": all_labels_observed,
        "iterativeUncertaintyResolved": all(
            "750" in observations[label]["times"]
            and "800" in observations[label]["times"]
            for label in labels
        ),
        "threeGridGciResolved": bool(mandatory_experimental_rows)
        and all(row["grid"]["qualified"] for row in mandatory_experimental_rows),
        "axialVelocityValidation": axial_counts["vv20PassFraction"] >= 0.90
        and centreline_counts["vv20PassFraction"] >= 0.90
        and primary_station_nrmse_ok,
        "pressureValidation": pressure_counts["vv20PassFraction"] >= 0.90
        and pressure_drop_row["passesVv20"],
        "flowConservation": all(
            observations[label]["checks"]["flowConservation"]
            for label in nominal_labels
        ),
        "forceReconciliation": all(
            observations[label]["checks"]["forceObjectMatchesDirect"]
            for label in nominal_labels
        ),
    }
    promotion_authorized = all(gates.values())
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "assessedAt": _now(),
        "campaignId": "fda-nozzle-re500-v1",
        "status": "passed" if promotion_authorized else "blocked",
        "claim": predeclared_contract()["claimUnderTest"],
        "promotionAuthorized": promotion_authorized,
        "provenance": {
            "officialRepository": OFFICIAL_REPOSITORY,
            "officialCommit": OFFICIAL_COMMIT,
            "officialDatasetDoi": OFFICIAL_DATASET_DOI,
            "primaryPaperDoi": PRIMARY_PAPER_DOI,
            "wallShearUqPaperDoi": WSS_UQ_PAPER_DOI,
            "openFoamImage": DEFAULT_IMAGE,
            "openFoamImageDigest": DEFAULT_IMAGE_DIGEST,
        },
        "evidenceHashesSha256": {
            "campaignContract": _sha256(output / "campaign-contract.json"),
            "officialArchive": _sha256(
                output / "experiment" / "raw" / "SE_exp_0500.zip"
            ),
            "assessmentImplementation": _sha256(Path(__file__).resolve()),
            "directIntegrationSource": _sha256(
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "tools"
                / "flowlabFdaPatchAudit"
                / "flowlabFdaPatchAudit.C"
            ),
            "directIntegrationBinary": _sha256(
                output / "bin" / "flowlabFdaPatchAudit"
            ),
            "observations": {
                label: _sha256(output / "results" / label / "observation.json")
                for label in labels
            },
            "solverLogs": {
                label: _sha256(output / "logs" / label / "foamRun.log")
                for label in labels
            },
            "directIntegration800": {
                label: _sha256(
                    output / "results" / label / "face-integration-800.csv"
                )
                for label in labels
            },
        },
        "gates": gates,
        "failureAnalysis": {
            "mandatoryGci": {
                "experimentalEligible": len(mandatory_experimental_rows),
                "qualified": sum(
                    bool(row["grid"]["qualified"])
                    for row in mandatory_experimental_rows
                ),
                "unqualified": sum(
                    not bool(row["grid"]["qualified"])
                    for row in mandatory_experimental_rows
                ),
                "byQoi": {
                    "primaryAxialProfiles": axial_counts,
                    "centrelineAxialVelocity": centreline_counts,
                    "wallPressure": pressure_counts,
                    "pressureDropQualified": pressure_drop_row["grid"]["qualified"],
                },
            },
            "axialVelocity": {
                "allPrimaryStationNormalizedRmseBelow10Pct": primary_station_nrmse_ok,
                "pointwisePassFraction": axial_counts["vv20PassFraction"],
                "centrelinePassFraction": centreline_counts["vv20PassFraction"],
                "interpretation": "profile-shape NRMSE passes at every primary station, but the frozen pointwise 90% V&V 20 rule and complete pointwise GCI rule do not",
            },
            "pressure": {
                "wallPointwisePassFraction": pressure_counts["vv20PassFraction"],
                "pressureDropPasses": pressure_drop_row["passesVv20"],
                "publishedSystematicNotAddedPostHoc": "100-250 Pa Re=500 normalization-point offset attributed partly to non-differential pressure transducers",
                "interpretation": "the offset-invariant pressure drop passes, while absolute profile differences relative to the uncertain x=0 reference do not",
            },
        },
        "nextCampaignRequirements": [
            "freeze a v2 pressure metric that treats the published x=0 offset prospectively using offset-invariant pressure differences and an explicit covariance/systematic model; do not fit an offset after seeing CFD",
            "replace the under-resolved coarse member with a refined nested sequence and require monotonic GCI at the currently unqualified contraction and downstream profile points",
            "predeclare a CFD-to-PIV observation operator that reproduces the experimental spatial averaging rather than comparing unresolved shear layers as ideal points",
            "run downstream-domain-length and spatial-scheme sensitivity before repeating the experimental validation",
        ],
        "meshCellCounts": {
            label: observations[label]["mesh"]["cells"] for label in nominal_labels
        },
        "comparisons": {
            "axialVelocityProfiles": {
                "role": "mandatory-validation",
                "counts": axial_counts,
                "primaryStationNrmseGate": primary_station_nrmse_ok,
                "stations": axial_profiles,
            },
            "radialVelocityProfiles": {
                "role": "mandatory-reporting-nonpromotional",
                "counts": radial_counts,
                "stations": radial_profiles,
            },
            "centrelineAxialVelocity": {
                "role": "mandatory-validation",
                "counts": centreline_counts,
                "points": centreline_rows,
            },
            "wallPressureRelativeToExpansion": {
                "role": "mandatory-validation",
                "counts": pressure_counts,
                "points": pressure_rows,
                "fineCentrelineVsWall": centreline_wall_differences,
            },
            "centrelinePressureAgainstWallExperiment": {
                "role": "mandatory-reporting-supporting",
                "counts": centreline_pressure_counts,
                "points": centreline_pressure_rows,
            },
            "pressureDrop": {
                "role": "mandatory-validation",
                "firstCoordinateM": first_pressure_x,
                "lastCoordinateM": last_pressure_x,
                **pressure_drop_row,
            },
            "wallShearViscousTraction": {
                "role": "mandatory-reporting-nonpromotional",
                "counts": wall_shear_counts,
                "points": wall_shear_rows,
            },
            "flowConservation": {
                label: observations[label]["times"]["800"]["flow"]
                for label in nominal_labels
            },
            "forceObjectVsDirectFaceIntegration": {
                label: {
                    "absoluteN": observations[label]["times"]["800"]["forceObjectVsDirectAbsoluteN"],
                    "relative": observations[label]["times"]["800"]["forceObjectVsDirectRelative"],
                    "openFoam": observations[label]["times"]["800"]["openFoamForces"],
                    "direct": observations[label]["times"]["800"]["directFaceIntegration"]["all"],
                }
                for label in nominal_labels
            },
        },
        "uncertaintyMethod": predeclared_contract()["uncertainty"],
        "experimentalLimitations": experiment.get("limitations", {}),
        "pressureEligibility": experiment.get("pressureEligibility", {}),
    }
    _write_json(output / "assessment.json", assessment)
    manifest_path = output / "campaign-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "status": "validated-passed" if promotion_authorized else "validated-blocked",
            "assessedAt": assessment["assessedAt"],
            "assessmentSha256": _sha256(output / "assessment.json"),
            "promotionAuthorized": promotion_authorized,
        }
    )
    _write_json(manifest_path, manifest)
    failed_gates = [name for name, passed in gates.items() if not passed]
    report_lines = [
        "# FDA nozzle Re=500 validation report",
        "",
        f"**Verdict:** {'PASS' if promotion_authorized else 'BLOCKED'}",
        "",
        f"Claim under test: {assessment['claim']}",
        "",
        "## Gate results",
        "",
    ]
    report_lines.extend(
        f"- {'PASS' if passed else 'FAIL'} — `{name}`"
        for name, passed in gates.items()
    )
    report_lines.extend(
        [
            "",
            "## Headline evidence",
            "",
            f"- Strict all-hex cells: {assessment['meshCellCounts']}",
            f"- Primary axial profile V&V 20 pass fraction: {axial_counts['vv20PassFraction']:.3f}",
            f"- Centreline axial V&V 20 pass fraction: {centreline_counts['vv20PassFraction']:.3f}",
            f"- Wall-pressure V&V 20 pass fraction: {pressure_counts['vv20PassFraction']:.3f}",
            f"- Centreline-pressure versus wall-experiment V&V 20 pass fraction: {centreline_pressure_counts['vv20PassFraction']:.3f}",
            f"- Pressure-drop V&V 20: {'PASS' if pressure_drop_row['passesVv20'] else 'FAIL'}",
            f"- Mandatory pointwise GCI: {sum(bool(row['grid']['qualified']) for row in mandatory_experimental_rows)}/{len(mandatory_experimental_rows)} qualified",
            f"- Failed gates: {failed_gates if failed_gates else 'none'}",
            "",
            "## Scientific diagnosis",
            "",
            f"All six primary axial-profile normalized RMSE values are below 10%, but the stricter pointwise V&V 20 pass fraction is {axial_counts['vv20PassFraction']:.1%} and {axial_counts['experimentalEligible'] - axial_counts['gciQualified']} primary profile points have nonqualified grid sequences. Centreline axial velocity passes {centreline_counts['vv20Passed']}/{centreline_counts['experimentalEligible']} points; its remaining failures are nonqualified grid sequences.",
            "",
            f"The offset-invariant pressure-drop comparison {'passes' if pressure_drop_row['passesVv20'] else 'fails'} (comparison error {pressure_drop_row.get('comparisonError', float('nan')):.2f} Pa, validation uncertainty {pressure_drop_row.get('validationUncertainty', float('nan')):.2f} Pa), but only {pressure_counts['vv20Passed']}/{pressure_counts['experimentalEligible']} wall-pressure points pass. The official CFD interlaboratory paper reports a 100-250 Pa Re=500 normalization-point offset. The v1 contract deliberately does not add that systematic after seeing the result.",
            "",
            "## Required v2 work",
            "",
            "1. Predeclare offset-invariant pressure differences with a sourced covariance/systematic model.",
            "2. Replace the under-resolved coarse member with a refined nested sequence at the contraction and downstream shear layer.",
            "3. Apply a predeclared CFD-to-PIV spatial-averaging observation operator.",
            "4. Run downstream-domain-length and spatial-scheme sensitivity before repeating validation.",
            "",
            "Radial velocity and legacy wall-shear comparisons are retained as nonpromotional diagnostics because the official source does not supply a reliable promotion-grade pointwise uncertainty basis for them.",
            "",
            "Machine-readable details, including every comparison error and uncertainty component, are in `assessment.json`.",
            "",
        ]
    )
    _write(output / "REPORT.md", "\n".join(report_lines))
    return assessment


def _container_command(
    image: str,
    mount_root: Path,
    workdir: Path,
    command: str,
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{mount_root}:{mount_root}",
        "-w",
        str(workdir),
        image,
        "bash",
        "-lc",
        "if [ -f /opt/openfoam11/etc/bashrc ]; then "
        "source /opt/openfoam11/etc/bashrc; else "
        "source /opt/OpenFOAM/OpenFOAM-11/etc/bashrc; fi; "
        + command,
    ]


def run_command(command: Sequence[str], cwd: Path, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        process = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return process.returncode


def prepare_campaign(output: Path, experiment_archive: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite campaign: {output}")
    output.mkdir(parents=True, exist_ok=True)
    contract = predeclared_contract()
    _write_json(output / "campaign-contract.json", contract)
    experiment = ingest_experiment(experiment_archive, output / "experiment")
    cases: list[dict[str, Any]] = []
    for label, refinement in LEVELS:
        case = output / "cases" / label
        prepare_case(case, label, refinement)
        cases.append({"label": label, "refinement": refinement, "flowScale": 1.0, "path": str(case)})
    for label, refinement, flow_scale in SENSITIVITY_CASES:
        case = output / "cases" / label
        prepare_case(case, label, refinement, flow_scale)
        cases.append({"label": label, "refinement": refinement, "flowScale": flow_scale, "path": str(case)})
    manifest = {
        "schema": SCHEMA,
        "createdAt": _now(),
        "status": "prepared-not-executed",
        "image": DEFAULT_IMAGE,
        "imageDigest": DEFAULT_IMAGE_DIGEST,
        "contractSha256": _sha256(output / "campaign-contract.json"),
        "experimentSha256": _sha256(output / "experiment" / "experimental-data.json"),
        "cases": cases,
        "promotionAuthorized": False,
    }
    _write_json(output / "campaign-manifest.json", manifest)
    return {"manifest": manifest, "contract": contract, "experiment": experiment}


def _latest_time(case: Path) -> str:
    values = []
    for child in case.iterdir():
        if child.is_dir():
            try:
                values.append((float(child.name), child.name))
            except ValueError:
                continue
    if not values:
        raise ValueError(f"no OpenFOAM time directories in {case}")
    return max(values)[1]


def _parse_check_mesh(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    cells = re.search(r"^\s*cells:\s+(\d+)", text, re.MULTILINE)
    hexahedra = re.search(r"^\s*hexahedra:\s+(\d+)", text, re.MULTILINE)
    return {
        "meshOk": "Mesh OK." in text or "Mesh OK" in text,
        "cells": int(cells.group(1)) if cells else None,
        "hexahedra": int(hexahedra.group(1)) if hexahedra else None,
        "strictAllHex": bool(cells and hexahedra and cells.group(1) == hexahedra.group(1)),
    }


def execute_case(output: Path, label: str, image: str = DEFAULT_IMAGE) -> dict[str, Any]:
    case = output / "cases" / label
    definition = json.loads((case / "case-definition.json").read_text(encoding="utf-8"))
    logs = output / "logs" / label
    # Mount the workspace root so both the case and the retained audit utility are visible.
    workspace = Path(__file__).resolve().parents[2]
    commands = [
        ("blockMesh", "blockMesh"),
        ("checkMesh", "checkMesh -allTopology -allGeometry"),
        ("writeCellCentres", "foamPostProcess -func writeCellCentres -time 0"),
    ]
    codes: dict[str, int] = {}
    for name, command in commands:
        codes[name] = run_command(
            _container_command(image, workspace, case, command),
            case,
            logs / f"{name}.log",
        )
        if codes[name] != 0:
            break
    if all(codes.get(name) == 0 for name, _ in commands):
        initialize_case(case, float(definition["flowScale"]))
        codes["foamRun"] = run_command(
            _container_command(
                image,
                workspace,
                case,
                "foamRun -solver incompressibleFluid",
            ),
            case,
            logs / "foamRun.log",
        )
    mesh = _parse_check_mesh(logs / "checkMesh.log") if (logs / "checkMesh.log").exists() else {}
    report = {
        "schema": CASE_SCHEMA,
        "label": label,
        "executedAt": _now(),
        "definition": definition,
        "exitCodes": codes,
        "mesh": mesh,
        "latestTime": _latest_time(case) if codes.get("foamRun") == 0 else None,
        "status": "solver-complete" if codes.get("foamRun") == 0 else "infrastructure-failed",
        "promotionAuthorized": False,
    }
    _write_json(output / "results" / label / "execution.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract_parser = subparsers.add_parser("contract")
    contract_parser.add_argument("--output", type=Path, required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--experiment-archive", type=Path, required=True)
    execute_parser = subparsers.add_parser("execute-case")
    execute_parser.add_argument("--output", type=Path, required=True)
    execute_parser.add_argument("--label", required=True)
    execute_parser.add_argument("--image", default=DEFAULT_IMAGE)
    post_parser = subparsers.add_parser("postprocess-case")
    post_parser.add_argument("--output", type=Path, required=True)
    post_parser.add_argument("--label", required=True)
    post_parser.add_argument("--image", default=DEFAULT_IMAGE)
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "contract":
        _write_json(args.output.resolve(), predeclared_contract())
        return 0
    if args.command == "prepare":
        result = prepare_campaign(args.output.resolve(), args.experiment_archive.resolve())
        print(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return 0
    if args.command == "assess":
        assessment = assess_campaign(args.output.resolve())
        print(json.dumps(assessment, indent=2, sort_keys=True))
        return 0 if assessment["promotionAuthorized"] else 3
    if args.command == "postprocess-case":
        report = postprocess_case(args.output.resolve(), args.label, args.image)
    else:
        report = execute_case(args.output.resolve(), args.label, args.image)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"solver-complete", "observed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
