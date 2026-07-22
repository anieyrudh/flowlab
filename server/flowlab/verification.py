"""Quantitative verification helpers for narrow-envelope FlowLab reference cases.

The functions in this module deliberately separate analytical reference values,
numerical-grid diagnostics, and a reviewed validation claim. They can create or
assess evidence, but never label a CFD case scientifically validated on their
own.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping, Sequence


STRAIGHT_PIPE_VERIFICATION_SCHEMA = "flowlab.straight-pipe-verification.v1"
STRAIGHT_PIPE_BUNDLE_SCHEMA = "flowlab.straight-pipe-3d-reference-bundle.v1"
LAMINAR_REFERENCE_MAX_REYNOLDS = 2100.0
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ANALYSIS_PROVENANCE_DIGESTS = (
    "caseManifestSha256",
    "meshArtifactSha256",
    "solverLogSha256",
    "rawResultSha256",
    "qoiExtractionSha256",
)


class VerificationInputError(ValueError):
    """Raised when a verification request cannot support a meaningful metric."""


@dataclass(frozen=True)
class StraightPipeSpec:
    """SI inputs for the fully developed Hagen-Poiseuille reference problem."""

    length_m: float
    radius_m: float
    density_kg_m3: float
    dynamic_viscosity_pa_s: float
    volumetric_flow_rate_m3_s: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("length_m", self.length_m),
            ("radius_m", self.radius_m),
            ("density_kg_m3", self.density_kg_m3),
            ("dynamic_viscosity_pa_s", self.dynamic_viscosity_pa_s),
            ("volumetric_flow_rate_m3_s", self.volumetric_flow_rate_m3_s),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise VerificationInputError(f"{field_name} must be a finite positive SI value")


def hagen_poiseuille_pressure_drop_pa(spec: StraightPipeSpec) -> float:
    """Return the analytical pressure drop for steady laminar pipe flow in Pa."""

    return (
        8.0
        * spec.dynamic_viscosity_pa_s
        * spec.length_m
        * spec.volumetric_flow_rate_m3_s
        / (math.pi * spec.radius_m**4)
    )


def straight_pipe_reference(spec: StraightPipeSpec) -> dict[str, float]:
    """Return analytical QoIs with explicit SI units encoded in key names."""

    area_m2 = math.pi * spec.radius_m**2
    mean_velocity_m_s = spec.volumetric_flow_rate_m3_s / area_m2
    diameter_m = 2.0 * spec.radius_m
    reynolds_number = (
        spec.density_kg_m3 * mean_velocity_m_s * diameter_m / spec.dynamic_viscosity_pa_s
    )
    return {
        "crossSectionAreaM2": area_m2,
        "meanVelocityMPerS": mean_velocity_m_s,
        "reynoldsNumber": reynolds_number,
        "pressureDropPa": hagen_poiseuille_pressure_drop_pa(spec),
    }


@dataclass(frozen=True)
class PlaneChannelSpec:
    """SI inputs for the fully developed 2D plane-Poiseuille reference problem.

    This matches FlowLab's product-pipeline mesh, which materialises a "pipe" as
    a one-cell-thick planar channel of wall-to-wall gap ``gap_m`` (the edge
    diameter scaled into the canvas plane). The applicable analytical solution
    is plane-Poiseuille flow between parallel plates, not the 3D Hagen-Poiseuille
    pipe law used by :class:`StraightPipeSpec`.
    """

    length_m: float
    gap_m: float
    density_kg_m3: float
    dynamic_viscosity_pa_s: float
    mean_velocity_m_s: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("length_m", self.length_m),
            ("gap_m", self.gap_m),
            ("density_kg_m3", self.density_kg_m3),
            ("dynamic_viscosity_pa_s", self.dynamic_viscosity_pa_s),
            ("mean_velocity_m_s", self.mean_velocity_m_s),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise VerificationInputError(f"{field_name} must be a finite positive SI value")


def plane_poiseuille_pressure_drop_pa(spec: PlaneChannelSpec) -> float:
    """Return the analytical pressure drop for steady laminar plane-channel flow in Pa.

    Fully developed flow between parallel plates of gap ``H`` has a parabolic
    profile with mean velocity ``U`` and pressure gradient ``dp/dx = -12 mu U / H**2``.
    """

    return (
        12.0
        * spec.dynamic_viscosity_pa_s
        * spec.mean_velocity_m_s
        * spec.length_m
        / spec.gap_m**2
    )


def plane_channel_reference(spec: PlaneChannelSpec) -> dict[str, float]:
    """Return analytical plane-Poiseuille QoIs with explicit SI units in key names.

    The Reynolds number uses the parallel-plate hydraulic diameter ``D_h = 2*H``
    so it is directly comparable to the pipe reference's diameter-based value.
    """

    hydraulic_diameter_m = 2.0 * spec.gap_m
    reynolds_number = (
        spec.density_kg_m3
        * spec.mean_velocity_m_s
        * hydraulic_diameter_m
        / spec.dynamic_viscosity_pa_s
    )
    return {
        "channelGapM": spec.gap_m,
        "hydraulicDiameterM": hydraulic_diameter_m,
        "meanVelocityMPerS": spec.mean_velocity_m_s,
        "reynoldsNumber": reynolds_number,
        "pressureDropPa": plane_poiseuille_pressure_drop_pa(spec),
    }


def _require_laminar_reference_envelope(spec: StraightPipeSpec) -> dict[str, float]:
    reference = straight_pipe_reference(spec)
    if reference["reynoldsNumber"] >= LAMINAR_REFERENCE_MAX_REYNOLDS:
        raise VerificationInputError(
            "the Hagen-Poiseuille verification bundle requires Reynolds number below "
            f"{LAMINAR_REFERENCE_MAX_REYNOLDS:g}; declare a different reference problem for transitional or turbulent flow"
        )
    return reference


def gmsh_straight_pipe_geo(spec: StraightPipeSpec, characteristic_length_m: float) -> str:
    """Create a genuine 3D Gmsh/OpenCASCADE pipe template in metres.

    This is a reproducible geometry input, not evidence that the mesh or solver
    has already run. The generated physical names form the intended hand-off to
    a reviewed OpenFOAM conversion pipeline. It intentionally remains an
    all-h template: a frozen-surface spatial-GCI campaign needs a separate
    surface-master contract rather than regenerating this geometry at each h.
    """

    if (
        not isinstance(characteristic_length_m, (int, float))
        or not math.isfinite(characteristic_length_m)
        or characteristic_length_m <= 0
    ):
        raise VerificationInputError(
            "characteristic_length_m must be a finite positive SI value"
        )

    epsilon_m = min(spec.radius_m, spec.length_m) * 1.0e-7
    return f'''// FlowLab straight-pipe 3D verification geometry (all values in metres).
SetFactory("OpenCASCADE");
length_m = {spec.length_m:.17g};
radius_m = {spec.radius_m:.17g};
mesh_size_m = {characteristic_length_m:.17g};
epsilon_m = {epsilon_m:.17g};

Cylinder(1) = {{0, 0, 0, length_m, 0, 0, radius_m}};
Physical Volume("fluid") = {{1}};
// OpenCASCADE Cylinder(1) in the pinned Gmsh runtime has stable boundary
// tags: 1 is the cylindrical wall, 2 is x=length_m, and 3 is x=0.
// Explicit, non-overlapping physical groups are required because MSH2 stores
// one physical tag per surface element; overlapping groups collapse patches
// during gmshToFoam conversion.
Physical Surface("wall") = {{1}};
Physical Surface("outlet") = {{2}};
Physical Surface("inlet") = {{3}};
Mesh.CharacteristicLengthMin = mesh_size_m;
Mesh.CharacteristicLengthMax = mesh_size_m;
// These controls are mandatory for the pinned Gmsh 4.4/Foundation-v11
// conversion preflight. Default tetrahedralization produced under-determined
// cells in an isolated cylinder probe; this template must not silently omit
// the optimizer controls that made the imported mesh pass checkMesh.
Mesh.Algorithm3D = 4;
Mesh.Optimize = 1;
Mesh.OptimizeNetgen = 1;
Mesh.OptimizeThreshold = 0.2;
Mesh.Smoothing = 20;
Mesh.MshFileVersion = 2.2;
'''


def build_straight_pipe_3d_reference_bundle(
    spec: StraightPipeSpec,
    mesh_sizes_m: Sequence[float],
) -> dict[str, Any]:
    """Build non-executed mesh inputs and a pending evidence manifest.

    Callers may write the returned files into a clean case directory. Each
    refinement is deliberately marked pending-real-run until solver logs,
    mesh reports, and sampled QoIs are imported from an actual CFD execution.
    """

    if len(mesh_sizes_m) < 3:
        raise VerificationInputError("at least three mesh sizes are required for grid refinement")
    if any(isinstance(value, bool) for value in mesh_sizes_m):
        raise VerificationInputError("mesh_sizes_m must contain numeric SI values, not booleans")
    parsed_mesh_sizes = [float(value) for value in mesh_sizes_m]
    if any(not math.isfinite(value) or value <= 0 for value in parsed_mesh_sizes):
        raise VerificationInputError("mesh_sizes_m must contain finite positive SI values")
    if len(set(parsed_mesh_sizes)) != len(parsed_mesh_sizes):
        raise VerificationInputError("mesh_sizes_m must be distinct")
    if any(value >= spec.radius_m for value in parsed_mesh_sizes):
        raise VerificationInputError("each mesh size must be smaller than the pipe radius")

    ordered_mesh_sizes = sorted(parsed_mesh_sizes, reverse=True)
    reference = _require_laminar_reference_envelope(spec)
    refinements = [
        {
            "id": f"mesh-{index + 1}",
            "characteristicCellSizeM": mesh_size_m,
            "status": "pending-real-run",
            "requiredArtifacts": ["mesh-report", "solver-log", "sampled-qois"],
        }
        for index, mesh_size_m in enumerate(ordered_mesh_sizes)
    ]
    manifest = {
        "schema": STRAIGHT_PIPE_BUNDLE_SCHEMA,
        "status": "pending-real-run",
        "scientificStatus": "unverified",
        "geometry": {
            "kind": "straight-circular-pipe",
            "dimension": 3,
            "coordinateUnit": "m",
            "lengthM": spec.length_m,
            "radiusM": spec.radius_m,
            "physicalGroups": ["fluid", "inlet", "outlet", "wall"],
        },
        "envelope": {
            "flowState": "steady",
            "phase": "single",
            "fluid": "Newtonian-incompressible",
            "referenceRegime": "fully-developed-laminar",
            "reynoldsNumber": reference["reynoldsNumber"],
        },
        "analyticalReference": {
            "model": "Hagen-Poiseuille",
            "assumptions": [
                "straight circular pipe",
                "no-slip wall",
                "steady incompressible Newtonian flow",
                "fully developed laminar profile",
            ],
            "qois": {
                "pressureDropPa": reference["pressureDropPa"],
                "meanVelocityMPerS": reference["meanVelocityMPerS"],
                "volumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s,
            },
        },
        "boundaryConditionRequirement": (
            "Impose a fully developed inlet profile, or retain a documented entrance-length "
            "justification before comparing pressure drop to the fully developed reference."
        ),
        "meshRefinement": refinements,
        "requiredEvidence": [
            "solver-and-container-provenance",
            "mesh-quality-report-for-each-refinement",
            "residual-and-conservation-history-for-each-refinement",
            "pressure-drop-and-mass-balance-qois-for-each-refinement",
            "grid-convergence-analysis",
            "independent-review-before-validation-claim",
        ],
        "promotion": {
            "forbiddenUntil": "all required evidence is present and independently reviewed",
            "neverInferValidatedFrom": ["template-generation", "analytical-reference-only"],
        },
    }
    files = {
        f"meshes/{refinement['id']}/pipe.geo": gmsh_straight_pipe_geo(
            spec, refinement["characteristicCellSizeM"]
        )
        for refinement in refinements
    }
    files["verification_manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + chr(10)
    return {"manifest": manifest, "files": files}


def _required_sample_value(sample: Mapping[str, Any], key: str) -> float:
    value = sample.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise VerificationInputError(f"each refinement sample requires finite {key}")
    return float(value)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _require_analysis_provenance(provenance: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(provenance, Mapping):
        raise VerificationInputError("analysis provenance must be a mapping")
    parsed: dict[str, str] = {}
    for field in REQUIRED_ANALYSIS_PROVENANCE_DIGESTS:
        value = provenance.get(field)
        if not _is_sha256(value):
            raise VerificationInputError(f"analysis provenance requires lowercase SHA-256 {field}")
        parsed[field] = str(value)
    for field in ("solverVersion", "solverCommand"):
        value = provenance.get(field)
        if not isinstance(value, str) or not value.strip():
            raise VerificationInputError(f"analysis provenance requires non-empty {field}")
        parsed[field] = value
    return parsed


def relative_mass_flow_imbalance(
    inlet_mass_flow_rate_kg_s: float,
    outlet_mass_flow_rate_kg_s: float,
) -> float:
    """Compute the signed inlet/outlet mass-balance metric used by the fixture."""

    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in (inlet_mass_flow_rate_kg_s, outlet_mass_flow_rate_kg_s)
    ):
        raise VerificationInputError("signed inlet and outlet mass flow rates must be finite")
    denominator = max(abs(float(inlet_mass_flow_rate_kg_s)), abs(float(outlet_mass_flow_rate_kg_s)))
    if denominator == 0.0:
        raise VerificationInputError("signed inlet and outlet mass flow rates cannot both be zero")
    return abs(float(inlet_mass_flow_rate_kg_s) + float(outlet_mass_flow_rate_kg_s)) / denominator


def richardson_grid_convergence(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate observed order and fine-grid GCI from three uniform refinements.

    Samples must carry a solver-produced QoI named value and a physical
    characteristic size named characteristicCellSizeM. This guards against
    accidentally applying a numerical metric to a manually typed benchmark.
    """

    if len(samples) != 3:
        raise VerificationInputError("Richardson GCI currently requires exactly three refinements")
    parsed: list[dict[str, float | str]] = []
    for sample in samples:
        if sample.get("source") != "solver-produced":
            raise VerificationInputError("each refinement sample must be solver-produced")
        if not _is_sha256(sample.get("sourceArtifactSha256")):
            raise VerificationInputError("each refinement sample requires a sourceArtifactSha256")
        parsed.append(
            {
                "id": str(sample.get("id", "")),
                "characteristicCellSizeM": _required_sample_value(sample, "characteristicCellSizeM"),
                "value": _required_sample_value(sample, "value"),
            }
        )
    parsed.sort(key=lambda sample: float(sample["characteristicCellSizeM"]), reverse=True)
    h0, h1, h2 = (float(sample["characteristicCellSizeM"]) for sample in parsed)
    q0, q1, q2 = (float(sample["value"]) for sample in parsed)
    if not h0 > h1 > h2:
        raise VerificationInputError("refinement sizes must be strictly coarse-to-fine")
    r01, r12 = h0 / h1, h1 / h2
    if not math.isclose(r01, r12, rel_tol=1e-6, abs_tol=1e-12):
        raise VerificationInputError("Richardson GCI requires a uniform refinement ratio")
    coarse_change, fine_change = q0 - q1, q1 - q2
    if coarse_change == 0.0 or fine_change == 0.0 or coarse_change * fine_change <= 0.0:
        raise VerificationInputError("QoI sequence is not monotone enough for observed-order GCI")
    observed_order = math.log(abs(coarse_change / fine_change)) / math.log(r12)
    if not math.isfinite(observed_order) or observed_order <= 0.0:
        raise VerificationInputError("observed order must be positive for a convergent GCI estimate")
    denominator = r12**observed_order - 1.0
    if denominator <= 0.0 or q2 == 0.0:
        raise VerificationInputError("cannot form a stable fine-grid GCI estimate")
    extrapolated_value = q2 + (q2 - q1) / denominator
    fine_grid_gci_percent = 1.25 * abs((q2 - q1) / q2) / denominator * 100.0
    return {
        "method": "three-grid-Richardson-extrapolation-with-GCI",
        "refinementRatio": r12,
        "observedOrder": observed_order,
        "richardsonExtrapolatedValue": extrapolated_value,
        "fineGridGciPercent": fine_grid_gci_percent,
        "samplesCoarseToFine": parsed,
    }


def evaluate_straight_pipe_pressure_drop(
    spec: StraightPipeSpec,
    samples: Sequence[Mapping[str, Any]],
    *,
    pressure_drop_relative_error_limit: float,
    inlet_mass_flow_rate_kg_s: float,
    outlet_mass_flow_rate_kg_s: float,
    mass_balance_relative_error_limit: float,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute review-only metrics from asserted CFD evidence.

    This function intentionally returns analysis-only status even when thresholds
    are met. A caller-provided value, source label, or digest is not an
    independently verified benchmark package and can never promote a case.
    """

    for name, value in (
        ("pressure_drop_relative_error_limit", pressure_drop_relative_error_limit),
        ("mass_balance_relative_error_limit", mass_balance_relative_error_limit),
    ):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.0:
            raise VerificationInputError(f"{name} must be a finite non-negative value")
    _require_laminar_reference_envelope(spec)
    analysis_provenance = _require_analysis_provenance(provenance)
    grid = richardson_grid_convergence(samples)
    fine_value = float(grid["samplesCoarseToFine"][-1]["value"])
    reference_pressure_drop_pa = hagen_poiseuille_pressure_drop_pa(spec)
    pressure_drop_relative_error = abs(fine_value - reference_pressure_drop_pa) / reference_pressure_drop_pa
    mass_balance_relative_error = relative_mass_flow_imbalance(
        inlet_mass_flow_rate_kg_s,
        outlet_mass_flow_rate_kg_s,
    )
    thresholds_met = (
        pressure_drop_relative_error <= pressure_drop_relative_error_limit
        and mass_balance_relative_error <= mass_balance_relative_error_limit
    )
    return {
        "schema": STRAIGHT_PIPE_VERIFICATION_SCHEMA,
        "scientificStatus": "analysis-only",
        "validated": False,
        "reviewRequired": True,
        "thresholdsMet": thresholds_met,
        "analyticalReferencePressureDropPa": reference_pressure_drop_pa,
        "fineMeshPressureDropPa": fine_value,
        "pressureDropRelativeError": pressure_drop_relative_error,
        "pressureDropRelativeErrorLimit": pressure_drop_relative_error_limit,
        "massBalanceRelativeError": mass_balance_relative_error,
        "massBalanceRelativeErrorLimit": mass_balance_relative_error_limit,
        "gridConvergence": grid,
        "assertedProvenance": analysis_provenance,
        "promotion": {
            "forbiddenUntil": "a filesystem-verified evidence package and independent review are attached",
            "neverTreatAs": "a passed or validated benchmark claim",
        },
    }
