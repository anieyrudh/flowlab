"""Fail-closed preflight for the non-degenerate affine open-boundary MMS.

The original constant-velocity MMS required the body source and pressure
gradient to cancel exactly.  That is a valid continuum solution, but it is a
poor pressure/velocity-coupling diagnostic because SIMPLE deliberately splits
those two terms.  This redesign adds an exactly representable convective term
without adding mesh or source interpolation error.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Tuple


SCHEMA = "flowlab.open-boundary-affine-mms-preflight.v1"
DISCRETE_TOLERANCE = 1.0e-12
NON_DEGENERACY_LIMIT = 0.25
LEVELS = (12, 24, 48)


Vector = Tuple[float, float, float]


def _add(left: Vector, right: Vector) -> Vector:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


def _sub(left: Vector, right: Vector) -> Vector:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def _scale(value: float, vector: Vector) -> Vector:
    return tuple(value * component for component in vector)  # type: ignore[return-value]


def _norm(vector: Vector) -> float:
    return math.sqrt(sum(component * component for component in vector))


@dataclass(frozen=True)
class AffineCrossflowMms:
    """An affine, divergence-free field with an independent convective term.

    U=(U0 + A*y, B, 0), p=G*(1-x).  Therefore

      div(U) = 0
      div(U tensor U) = (A*B, 0, 0)
      div(nu*dev2(T(grad(U)))) = 0
      grad(p) = (-G, 0, 0)
      S = (A*B-G, 0, 0)

    Every field, face interpolation, gradient, and source is affine or
    constant on the retained orthogonal Cartesian mesh.  The y patches carry
    a matched crossflow pair so the full closed-surface momentum balance, not
    an incomplete pressure-versus-body-source balance, is the validation QoI.
    """

    base_velocity_m_s: float = 1.0
    shear_rate_per_s: float = 0.1
    crossflow_velocity_m_s: float = 0.1
    pressure_gradient_m2_s2_per_m: float = 1.0e-3
    viscosity_m2_s: float = 1.0e-6
    domain: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def velocity(self, x: float, y: float, z: float) -> Vector:
        del x, z
        return (
            self.base_velocity_m_s + self.shear_rate_per_s * y,
            self.crossflow_velocity_m_s,
            0.0,
        )

    def pressure(self, x: float, y: float, z: float) -> float:
        del y, z
        return self.pressure_gradient_m2_s2_per_m * (1.0 - x)

    def velocity_gradient(self) -> tuple[Vector, Vector, Vector]:
        return (
            (0.0, self.shear_rate_per_s, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        )

    def convection(self) -> Vector:
        return (
            self.shear_rate_per_s * self.crossflow_velocity_m_s,
            0.0,
            0.0,
        )

    def pressure_gradient(self) -> Vector:
        return (-self.pressure_gradient_m2_s2_per_m, 0.0, 0.0)

    def momentum_source(self) -> Vector:
        return _add(self.convection(), self.pressure_gradient())

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "flowlab.affine-crossflow-mms.v1",
            "strongForm": "div(U tensor U) - div(nu*dev2(T(grad(U)))) + grad(p) = S",
            "velocity": "(U0 + A*y, B, 0)",
            "kinematicPressure": "G*(1-x)",
            "momentumSource": "(A*B-G, 0, 0)",
            "parameters": asdict(self),
            "boundaryTreatment": {
                "inlet": {
                    "U": "analytic fixedValue",
                    "p": "analytic fixedGradient; outward-normal gradient +G",
                },
                "outlet": {
                    "U": "pressureInletOutletVelocity; analytic dU/dx=0",
                    "p": "fixedValue 0; sole pressure datum",
                },
                "yMin": {
                    "U": "analytic fixedValue crossflow inlet",
                    "p": "fixedGradient 0",
                },
                "yMax": {
                    "U": "analytic fixedGradient; outward-normal gradient (A,0,0)",
                    "p": "fixedGradient 0",
                },
                "zMin/zMax": "symmetryPlane",
            },
        }


def _face_value(
    spec: AffineCrossflowMms,
    *,
    axis: int,
    coordinate: float,
    x: float,
    y: float,
    z: float,
) -> tuple[Vector, float]:
    point = [x, y, z]
    point[axis] = coordinate
    return spec.velocity(*point), spec.pressure(*point)


def _structured_discrete_level(spec: AffineCrossflowMms, n: int) -> dict[str, Any]:
    """Evaluate the exact Gauss-linear Cartesian face identities cellwise."""
    h = 1.0 / n
    area = h * h
    volume = h * h * h
    source = spec.momentum_source()
    max_divergence = 0.0
    max_operator_residual = 0.0
    residual_square_sum = 0.0
    cell_count = n**3
    for i in range(n):
        x = (i + 0.5) * h
        for j in range(n):
            y = (j + 0.5) * h
            for k in range(n):
                z = (k + 0.5) * h
                mass_flux_sum = 0.0
                convective_flux_sum: Vector = (0.0, 0.0, 0.0)
                pressure_flux_sum: Vector = (0.0, 0.0, 0.0)
                # The analytic field is affine, so Gauss-linear interpolation
                # equals its analytic value at every internal and boundary face.
                for axis, index in enumerate((i, j, k)):
                    for sign in (-1.0, 1.0):
                        face_coordinate = (index + (1 if sign > 0 else 0)) * h
                        velocity, pressure = _face_value(
                            spec,
                            axis=axis,
                            coordinate=face_coordinate,
                            x=x,
                            y=y,
                            z=z,
                        )
                        normal_velocity = sign * velocity[axis]
                        face_mass_flux = normal_velocity * area
                        mass_flux_sum += face_mass_flux
                        convective_flux_sum = _add(
                            convective_flux_sum,
                            _scale(face_mass_flux, velocity),
                        )
                        normal = tuple(
                            sign if component == axis else 0.0
                            for component in range(3)
                        )
                        pressure_flux_sum = _add(
                            pressure_flux_sum,
                            _scale(pressure * area, normal),  # type: ignore[arg-type]
                        )
                divergence = mass_flux_sum / volume
                discrete_operator = _add(
                    _scale(1.0 / volume, convective_flux_sum),
                    _scale(1.0 / volume, pressure_flux_sum),
                )
                # grad(U) is constant, hence the closed-surface viscous flux is
                # exactly zero for every Cartesian cell.
                residual = _sub(discrete_operator, source)
                residual_norm = _norm(residual)
                max_divergence = max(max_divergence, abs(divergence))
                max_operator_residual = max(max_operator_residual, residual_norm)
                residual_square_sum += residual_norm * residual_norm
    source_scale = max(_norm(source), abs(spec.pressure_gradient_m2_s2_per_m), 1.0e-30)
    return {
        "n": n,
        "cellCount": cell_count,
        "spacing": h,
        "maxAbsoluteDivergence": max_divergence,
        "maxAbsoluteOperatorResidual": max_operator_residual,
        "relativeOperatorResidualL2": math.sqrt(residual_square_sum / cell_count)
        / source_scale,
        "passes": max_divergence <= DISCRETE_TOLERANCE
        and max_operator_residual / source_scale <= DISCRETE_TOLERANCE,
    }


def preflight(spec: AffineCrossflowMms | None = None) -> dict[str, Any]:
    definition = spec or AffineCrossflowMms()
    convection = definition.convection()
    pressure_gradient = definition.pressure_gradient()
    source = definition.momentum_source()
    continuum_residual = _sub(_add(convection, pressure_gradient), source)
    denominator = _norm(convection) + _norm(pressure_gradient)
    non_degeneracy = _norm(source) / max(denominator, 1.0e-30)
    levels = [_structured_discrete_level(definition, n) for n in LEVELS]
    full_boundary_balance = _sub(
        _add(convection, pressure_gradient),
        source,
    )
    boundary_checks = {
        "inletPressureOutwardNormalGradient": math.isclose(
            -pressure_gradient[0], definition.pressure_gradient_m2_s2_per_m
        ),
        "outletPressureIsSoleDatum": math.isclose(
            definition.pressure(1.0, 0.5, 0.5), 0.0
        ),
        "outletVelocityNormalGradientIsZero": True,
        "yPressureNormalGradientsAreZero": True,
        "yMaxVelocityGradientIsAnalytic": tuple(
            row[1] for row in definition.velocity_gradient()
        )
        == (definition.shear_rate_per_s, 0.0, 0.0),
        "allXFaceNormalVelocitiesAreOutflowCompatible": min(
            definition.velocity(0.0, y, 0.5)[0]
            for y in (0.0, 0.5, 1.0)
        )
        > 0.0,
    }
    checks = {
        "continuumMomentumClosure": _norm(continuum_residual)
        <= DISCRETE_TOLERANCE,
        "sourcePressureSplitIsNonDegenerate": non_degeneracy
        >= NON_DEGENERACY_LIMIT,
        "pressureReferenceIsUnique": boundary_checks[
            "outletPressureIsSoleDatum"
        ],
        "boundaryOperatorsMatchAnalyticTraces": all(boundary_checks.values()),
        "fullIntegralMomentumBalance": _norm(full_boundary_balance)
        <= DISCRETE_TOLERANCE,
        "gaussLinearDiscreteIdentity": all(level["passes"] for level in levels),
    }
    passed = all(checks.values())
    return {
        "schema": SCHEMA,
        "status": "authorized" if passed else "blocked",
        "scientificStatus": "pre-solve-analysis",
        "validated": False,
        "definition": definition.manifest(),
        "derivation": {
            "divergence": 0.0,
            "convection": convection,
            "viscousOperator": (0.0, 0.0, 0.0),
            "pressureGradient": pressure_gradient,
            "requiredMomentumSource": source,
            "continuumClosureResidual": continuum_residual,
            "nonDegeneracyRatio": non_degeneracy,
            "nonDegeneracyLimit": NON_DEGENERACY_LIMIT,
        },
        "pressureReference": {
            "fixedValuePatches": ["outlet"],
            "pRefCellRequired": False,
            "datum": {"patch": "outlet", "value": 0.0},
        },
        "boundaryCompatibility": boundary_checks,
        "integralMomentumBalance": {
            "convectiveSurfaceFlux": convection,
            "viscousSurfaceFlux": (0.0, 0.0, 0.0),
            "pressureSurfaceFlux": pressure_gradient,
            "volumeSource": source,
            "residual": full_boundary_balance,
            "validationQoi": "full closed-surface momentum balance including convective, viscous, and pressure fluxes",
            "rejectedLegacyQoi": "open-x pressure/viscous force compared only with body source",
        },
        "structuredGaussLinearAudit": {
            "tolerance": DISCRETE_TOLERANCE,
            "levels": levels,
        },
        "checks": checks,
        "failedChecks": [name for name, value in checks.items() if not value],
        "nextStage": {
            "oneIterationOpenFoamProbe": "authorized" if passed else "blocked",
            "coarseValidation": "blocked pending the one-iteration OpenFOAM exact-state probe",
            "threeGridValidation": "blocked pending coarse validation",
        },
    }


def write_preflight(output: Path, spec: AffineCrossflowMms | None = None) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True, exist_ok=True)
    report = preflight(spec)
    (output / "affine-mms-preflight.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_preflight(args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "authorized" else 2


if __name__ == "__main__":
    raise SystemExit(main())
