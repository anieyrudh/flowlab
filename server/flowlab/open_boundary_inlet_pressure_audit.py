"""Audit a one-change coarse inlet-pressure boundary experiment."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

from .open_boundary_campaign import MASS_LIMIT
from .open_boundary_traction_history import (
    MINIMUM_DECAY_FRACTION,
    TREND_WINDOW,
    _force_history,
    _residual_history,
    _sha,
    _trend,
)


SCHEMAS = {
    "fixedFluxPressure": "flowlab.open-boundary-inlet-fixed-flux-pressure-audit.v1",
    "fixedGradient": "flowlab.open-boundary-inlet-fixed-gradient-pressure-audit.v1",
}
OUTPUT_NAMES = {
    "fixedFluxPressure": "inlet-pressure-fixed-flux-audit.json",
    "fixedGradient": "inlet-pressure-fixed-gradient-audit.json",
}
UNCHANGED_SOLVE_FILES = (
    "0/U",
    "constant/fvModels",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "system/blockMeshDict",
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
)
NUMBER = r"[-+0-9.eE]+"
INLET_PATCH_PATTERN = re.compile(
    r"(boundaryField\s*\{.*?\binlet\s*\{)(.*?)(\}\s*outlet\s*\{)",
    re.DOTALL,
)
TYPE_PATTERN = re.compile(r"\btype\s+(fixedValue|fixedFluxPressure|fixedGradient)\s*;")
DATUM_PATTERN = re.compile(rf"\b(value|gradient)\s+uniform\s+({NUMBER})\s*;")


def _inlet_pressure_spec(text: str) -> dict[str, Any]:
    matches = INLET_PATCH_PATTERN.findall(text)
    if len(matches) != 1:
        raise ValueError("p field must contain exactly one supported inlet pressure patch")
    body = matches[0][1]
    type_match = TYPE_PATTERN.search(body)
    datum_match = DATUM_PATTERN.search(body)
    if type_match is None or datum_match is None:
        raise ValueError("inlet pressure patch is missing its type or analytic datum")
    return {
        "type": type_match.group(1),
        "datumKind": datum_match.group(1),
        "datum": float(datum_match.group(2)),
    }


def _normalize_inlet_pressure(text: str) -> str:
    normalized, count = INLET_PATCH_PATTERN.subn(
        r"\g<1> <inlet-pressure-condition>; \g<3>", text
    )
    if count != 1:
        raise ValueError("could not isolate the inlet pressure patch type")
    return normalized


def _implementation_equivalent(control_path: Path, changed_path: Path) -> bool:
    control = json.loads(control_path.read_text(encoding="utf-8"))
    changed = json.loads(changed_path.read_text(encoding="utf-8"))
    control["inletPressure"] = "<inlet-pressure-type>"
    changed["inletPressure"] = "<inlet-pressure-type>"
    # This descriptive key was added with fixedFluxPressure support; it is not
    # an OpenFOAM solve input and the value is proven directly from 0/p below.
    control.pop("inletPressureAnalyticInitialValue", None)
    changed.pop("inletPressureAnalyticInitialValue", None)
    control.pop("inletPressureAnalyticNormalGradient", None)
    changed.pop("inletPressureAnalyticNormalGradient", None)
    return control == changed


def _inlet_pressure_equivalence(
    control_case: Path,
    changed_case: Path,
    *,
    expected_type: str = "fixedFluxPressure",
    expected_value: float = 0.001,
) -> dict[str, Any]:
    file_checks = {
        name: {
            "same": _sha(control_case / name) == _sha(changed_case / name),
            "controlSha256": _sha(control_case / name),
            "changedSha256": _sha(changed_case / name),
        }
        for name in UNCHANGED_SOLVE_FILES
    }
    control_p_path = control_case / "0/p"
    changed_p_path = changed_case / "0/p"
    control_p = control_p_path.read_text(encoding="utf-8")
    changed_p = changed_p_path.read_text(encoding="utf-8")
    before = _inlet_pressure_spec(control_p)
    after = _inlet_pressure_spec(changed_p)
    normalized_same = _normalize_inlet_pressure(control_p) == _normalize_inlet_pressure(changed_p)
    expected_change = (
        before["type"] == "fixedValue"
        and before["datumKind"] == "value"
        and after["type"] == expected_type
        and after["datumKind"] == (
            "gradient" if expected_type == "fixedGradient" else "value"
        )
        and math.isclose(before["datum"], expected_value)
        and math.isclose(after["datum"], expected_value)
    )
    all_unchanged = all(check["same"] for check in file_checks.values())
    implementation_same = _implementation_equivalent(
        control_case / "boundary-implementation.json",
        changed_case / "boundary-implementation.json",
    )
    return {
        "unchangedSolveFiles": file_checks,
        "allOtherSolveFilesIdentical": all_unchanged,
        "pIdenticalAfterInletTypeNormalization": normalized_same,
        "boundaryMetadataIdenticalAfterInletTypeNormalization": implementation_same,
        "inletPressure": {
            "before": before,
            "after": after,
            "expectedAnalyticDatum": {
                "kind": "gradient" if expected_type == "fixedGradient" else "value",
                "value": expected_value,
            },
        },
        "onlyInletPressureBoundaryChanged": (
            all_unchanged and normalized_same and implementation_same and expected_change
        ),
        "onlyInletPressureTypeChanged": (
            all_unchanged and normalized_same and implementation_same and expected_change
        ),
        "p": {
            "controlSha256": _sha(control_p_path),
            "changedSha256": _sha(changed_p_path),
        },
    }


def inlet_pressure_audit(
    run: Path,
    *,
    control_run: Path,
    expected_type: str = "fixedFluxPressure",
    expected_value: float = 0.001,
) -> dict[str, Any]:
    if expected_type not in SCHEMAS:
        raise ValueError(f"unsupported audited inlet pressure type: {expected_type}")
    case = run / "coarse/case"
    control_case = control_run / "coarse/case"
    equivalence = _inlet_pressure_equivalence(
        control_case,
        case,
        expected_type=expected_type,
        expected_value=expected_value,
    )
    force_paths = sorted((case / "postProcessing/forces").glob("**/force*.dat"))
    if not force_paths:
        raise FileNotFoundError(f"missing forces history below {case}")
    force_path = force_paths[-1]
    solver_log = run / "coarse/artifacts/foamRun.log"
    force_history = _force_history(force_path, source_x=-expected_value)
    residual_history = _residual_history(solver_log)
    traction_rows = [row for row in force_history if row["iteration"] > 0.0]
    traction_trend = _trend(traction_rows, "relativeTractionImbalance")
    residual_trend = _trend(residual_history, "maxEquationInitialResidual")
    final_traction = traction_rows[-1]["relativeTractionImbalance"]
    final_force = traction_rows[-1]
    pressure_departure = abs(final_force["pressureForceX"] + expected_value) / expected_value
    viscous_departure = abs(final_force["viscousForceX"]) / expected_value
    traction_slope = traction_trend["logSlopePerIteration"]
    residual_slope = residual_trend["logSlopePerIteration"]
    still_decaying = (
        traction_trend["relativeDrop"] >= MINIMUM_DECAY_FRACTION
        and residual_trend["relativeDrop"] >= MINIMUM_DECAY_FRACTION
        and traction_slope is not None
        and traction_slope < 0.0
        and residual_slope is not None
        and residual_slope < 0.0
    )
    if final_traction <= MASS_LIMIT:
        classification = "gate-passed"
    elif still_decaying:
        classification = "still-decaying-at-frozen-stop"
    else:
        classification = "plateaued-or-oscillatory"

    advancement_path = run / "artifacts/coarse-advancement-report.json"
    advancement = json.loads(advancement_path.read_text(encoding="utf-8"))
    control_observation_path = control_run / "coarse/artifacts/observation.json"
    control_observation = json.loads(control_observation_path.read_text(encoding="utf-8"))
    changed_observation_path = run / "coarse/artifacts/observation.json"
    changed_observation = json.loads(changed_observation_path.read_text(encoding="utf-8"))
    provenance_path = run / "execution-provenance.json"
    report = {
        "schema": SCHEMAS[expected_type],
        "status": "audited",
        "scientificStatus": "analysis-only",
        "validated": False,
        "singleChange": {
            "field": "p",
            "patch": "inlet",
            "before": "fixedValue",
            "after": expected_type,
            (
                "analyticNormalGradient"
                if expected_type == "fixedGradient"
                else "analyticInitialValue"
            ): expected_value,
            "rationale": (
                "Impose the manufactured outward-normal pressure gradient while retaining the exact inlet velocity and fixed outlet pressure."
                if expected_type == "fixedGradient"
                else "Remove simultaneous fixed inlet pressure and velocity while retaining the exact inlet velocity and fixed outlet pressure."
            ),
        },
        "controlEquivalence": equivalence,
        "method": {
            "trendWindow": TREND_WINDOW,
            "minimumRelativeDecay": MINIMUM_DECAY_FRACTION,
            "tractionLimit": MASS_LIMIT,
            "iterationLimit": 100,
            "classificationRule": "Still decaying requires at least 10% reduction and a negative log slope for both traction imbalance and maximum equation-initial residual over the last 20 frozen iterations.",
        },
        "samples": {
            "force": len(force_history),
            "forceExcludingInitial": len(traction_rows),
            "equationResidual": len(residual_history),
        },
        "traction": {
            "final": final_traction,
            "limit": MASS_LIMIT,
            "passes": final_traction <= MASS_LIMIT,
            "classification": classification,
            "trend": traction_trend,
            "lastWindow": traction_rows[-TREND_WINDOW:],
        },
        "finalForceDecomposition": {
            "analyticPressureForceX": -expected_value,
            "pressureForceX": final_force["pressureForceX"],
            "viscousForceX": final_force["viscousForceX"],
            "totalForceX": final_force["totalForceX"],
            "pressureDepartureRelativeToSource": pressure_departure,
            "viscousDepartureRelativeToSource": viscous_departure,
            "dominantGateFailureContribution": (
                "pressure" if pressure_departure > viscous_departure else "viscous"
            ),
        },
        "equationInitialResidual": {
            "trend": residual_trend,
            "lastWindow": residual_history[-TREND_WINDOW:],
            "firstIterationUxLinearSolve": residual_history[0]["components"]["Ux"],
        },
        "relativeToV12Control": {
            "velocityL2ErrorRatio": changed_observation["velocity_l2_error"]
            / control_observation["velocity_l2_error"],
            "pressureL2ErrorRatio": changed_observation["pressure_l2_error"]
            / control_observation["pressure_l2_error"],
            "boundaryTractionRelativeImbalanceRatio": changed_observation[
                "boundary_traction_relative_imbalance"
            ]
            / control_observation["boundary_traction_relative_imbalance"],
        },
        "conclusion": {
            "classification": classification,
            "coarseAdvancementStatus": advancement["status"],
            "failedChecks": advancement["failedChecks"],
            "threeGridForcedMms": advancement["nextStage"]["forcedMmsThreeGrid"],
            "threeGridExecuted": advancement["nextStage"]["threeGridExecuted"],
            "allExistingGatesPass": all(advancement["checks"].values()),
            "gateUnchanged": True,
            "iterationLimitUnchanged": True,
        },
        "rawEvidence": {
            "forces": {"path": str(force_path), "sha256": _sha(force_path)},
            "solverLog": {"path": str(solver_log), "sha256": _sha(solver_log)},
            "controlObservation": {
                "path": str(control_observation_path),
                "sha256": _sha(control_observation_path),
            },
            "changedObservation": {
                "path": str(changed_observation_path),
                "sha256": _sha(changed_observation_path),
            },
            "advancementReport": {
                "path": str(advancement_path),
                "sha256": _sha(advancement_path),
            },
            "executionProvenance": {
                "path": str(provenance_path),
                "sha256": _sha(provenance_path),
            },
        },
    }
    output = run / "artifacts" / OUTPUT_NAMES[expected_type]
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--control-run", type=Path, required=True)
    parser.add_argument("--expected-type", choices=tuple(SCHEMAS), default="fixedFluxPressure")
    parser.add_argument("--expected-value", type=float, default=0.001)
    args = parser.parse_args()
    report = inlet_pressure_audit(
        args.run.resolve(),
        control_run=args.control_run.resolve(),
        expected_type=args.expected_type,
        expected_value=args.expected_value,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
