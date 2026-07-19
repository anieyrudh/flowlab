"""Read-only diagnosis of the affine MMS one-iteration OpenFOAM probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .cad_parabolic_smoke import _read_cell_centres
from .open_boundary_mms_redesign import AffineCrossflowMms
from .open_boundary_mms_runner import _values


SCHEMA = "flowlab.open-boundary-affine-mms-one-iteration-audit.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _solver_residuals(text: str) -> dict[str, dict[str, float | int]]:
    pattern = re.compile(
        r"Solving for (?P<field>Ux|Uy|Uz|p), Initial residual = "
        r"(?P<initial>[-+0-9.eE]+), Final residual = "
        r"(?P<final>[-+0-9.eE]+), No Iterations (?P<iterations>\d+)"
    )
    return {
        match.group("field"): {
            "initial": float(match.group("initial")),
            "final": float(match.group("final")),
            "iterations": int(match.group("iterations")),
        }
        for match in pattern.finditer(text)
    }


def _component_error(
    actual: list[tuple[float, ...]],
    exact: list[tuple[float, ...]],
    component: int,
) -> dict[str, float | None]:
    errors = [left[component] - right[component] for left, right in zip(actual, exact)]
    absolute = math.sqrt(sum(value * value for value in errors) / len(errors))
    denominator = math.sqrt(
        sum(value[component] * value[component] for value in exact) / len(exact)
    )
    return {
        "absoluteL2": absolute,
        "relativeL2": absolute / denominator if denominator > 0.0 else None,
        "minDifference": min(errors),
        "maxDifference": max(errors),
    }


def _pressure_fit(
    centres: list[tuple[float, float, float]], values: list[float]
) -> dict[str, float]:
    mean_x = sum(point[0] for point in centres) / len(centres)
    mean_p = sum(values) / len(values)
    denominator = sum((point[0] - mean_x) ** 2 for point in centres)
    slope = sum(
        (point[0] - mean_x) * (value - mean_p)
        for point, value in zip(centres, values)
    ) / denominator
    intercept = mean_p - slope * mean_x
    residual = math.sqrt(
        sum(
            (value - (intercept + slope * point[0])) ** 2
            for point, value in zip(centres, values)
        )
        / len(values)
    )
    return {"slope": slope, "intercept": intercept, "fitResidualL2": residual}


def audit(run: Path) -> dict[str, Any]:
    case = run / "case"
    artifacts = run / "artifacts"
    probe_path = artifacts / "affine-mms-one-iteration-probe.json"
    log_path = artifacts / "foamRun.log"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    residuals = _solver_residuals(log_text)
    if set(residuals) != {"Ux", "Uy", "Uz", "p"}:
        raise ValueError("probe log does not contain one complete U/p solve")
    spec = AffineCrossflowMms()
    centres = _read_cell_centres(case / "0/C")
    actual_u = _values(case / "1/U", True)
    actual_p_tuples = _values(case / "1/p", False)
    actual_p = [value[0] for value in actual_p_tuples]
    exact_u = [spec.velocity(*point) for point in centres]
    exact_p = [spec.pressure(*point) for point in centres]
    pressure_fit = _pressure_fit(centres, actual_p)
    source_direction_predictor_exact = float(residuals["Ux"]["initial"]) <= 1.0e-12
    pressure_equation_rejects_exact_state = float(residuals["p"]["initial"]) > 1.0e-8
    classification = (
        "pressure-correction-exact-state-not-fixed-point"
        if source_direction_predictor_exact and pressure_equation_rejects_exact_state
        else "mixed-assembly-failure"
    )
    return {
        "schema": SCHEMA,
        "status": "audited",
        "scientificStatus": "analysis-only",
        "validated": False,
        "classification": classification,
        "solverResiduals": residuals,
        "fieldDepartureAfterPressureCorrection": {
            "velocity": {
                name: _component_error(actual_u, exact_u, index)
                for index, name in enumerate(("Ux", "Uy", "Uz"))
            },
            "pressure": {
                "relativeL2": probe["observation"]["pressureRelativeL2Error"],
                "actualLinearFit": pressure_fit,
                "expectedSlope": -spec.pressure_gradient_m2_s2_per_m,
                "slopeRatio": pressure_fit["slope"]
                / -spec.pressure_gradient_m2_s2_per_m,
            },
        },
        "causalChecks": {
            "sourceDirectionPredictorIsExactBeforePressureSolve": source_direction_predictor_exact,
            "pressureEquationRejectsExactInitializedState": pressure_equation_rejects_exact_state,
            "massBalanceStillPasses": probe["checks"]["massBalance"],
            "coarseValidationRemainsBlocked": probe["nextStage"][
                "coarseValidation"
            ]
            == "blocked",
        },
        "conclusion": {
            "finding": "The affine source sign and x-momentum convection close at the live predictor (initial Ux residual is round-off), but the segregated SIMPLE pressure equation does not preserve the exact initialized pressure/velocity state.",
            "notAuthorized": "Do not run the 100-iteration coarse or three-grid campaigns, and do not tune another boundary value or MMS amplitude.",
            "nextAction": "Instrument the affine first-step pEqn predictor flux, pressure-correction flux, and boundary coefficients, or move the manufactured validation to a coupling formulation that proves exact-state preservation before promotion.",
        },
        "rawEvidence": {
            "probe": {"path": str(probe_path), "sha256": _sha(probe_path)},
            "solverLog": {"path": str(log_path), "sha256": _sha(log_path)},
            "initialU": {"path": str(case / "0/U"), "sha256": _sha(case / "0/U")},
            "initialP": {"path": str(case / "0/p"), "sha256": _sha(case / "0/p")},
            "finalU": {"path": str(case / "1/U"), "sha256": _sha(case / "1/U")},
            "finalP": {"path": str(case / "1/p"), "sha256": _sha(case / "1/p")},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.run.resolve())
    output = args.run.resolve() / "artifacts/affine-mms-one-iteration-audit.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
