"""Audit a single U-equation relaxation change in the coarse forced-MMS case."""
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


SCHEMA = "flowlab.open-boundary-u-relaxation-stabilization-audit.v1"
UNCHANGED_SOLVE_FILES = (
    "0/U",
    "0/p",
    "constant/fvModels",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "system/blockMeshDict",
    "system/controlDict",
    "system/fvSchemes",
)
RELAXATION_PATTERN = re.compile(
    r"(relaxationFactors\s*\{\s*equations\s*\{\s*U\s+)([-+0-9.eE]+)(;)",
    re.DOTALL,
)


def _relaxation_value(text: str) -> float:
    matches = RELAXATION_PATTERN.findall(text)
    if len(matches) != 1:
        raise ValueError("fvSolution must contain exactly one U equation relaxation value")
    return float(matches[0][1])


def _normalize_relaxation(text: str) -> str:
    normalized, count = RELAXATION_PATTERN.subn(r"\g<1><u-equation-relaxation>\3", text)
    if count != 1:
        raise ValueError("could not isolate U equation relaxation in fvSolution")
    return normalized


def _stabilization_equivalence(
    control_case: Path,
    stabilized_case: Path,
    *,
    expected_after: float,
) -> dict[str, Any]:
    file_checks = {
        name: {
            "same": _sha(control_case / name) == _sha(stabilized_case / name),
            "controlSha256": _sha(control_case / name),
            "stabilizedSha256": _sha(stabilized_case / name),
        }
        for name in UNCHANGED_SOLVE_FILES
    }
    control_solution_path = control_case / "system/fvSolution"
    stabilized_solution_path = stabilized_case / "system/fvSolution"
    control_solution = control_solution_path.read_text(encoding="utf-8")
    stabilized_solution = stabilized_solution_path.read_text(encoding="utf-8")
    before = _relaxation_value(control_solution)
    after = _relaxation_value(stabilized_solution)
    normalized_same = _normalize_relaxation(control_solution) == _normalize_relaxation(stabilized_solution)
    expected_change = math.isclose(before, 1.0) and math.isclose(after, expected_after)
    all_unchanged = all(check["same"] for check in file_checks.values())
    return {
        "unchangedSolveFiles": file_checks,
        "allOtherSolveFilesIdentical": all_unchanged,
        "fvSolutionIdenticalAfterRelaxationNormalization": normalized_same,
        "relaxation": {"before": before, "after": after, "expectedAfter": expected_after},
        "onlyUEquationRelaxationChanged": all_unchanged and normalized_same and expected_change,
        "fvSolution": {
            "controlSha256": _sha(control_solution_path),
            "stabilizedSha256": _sha(stabilized_solution_path),
        },
    }


def stabilization_audit(
    run: Path,
    *,
    control_run: Path,
    expected_relaxation: float = 0.9,
) -> dict[str, Any]:
    case = run / "coarse/case"
    control_case = control_run / "coarse/case"
    equivalence = _stabilization_equivalence(
        control_case,
        case,
        expected_after=expected_relaxation,
    )
    force_paths = sorted((case / "postProcessing/forces").glob("**/force*.dat"))
    if not force_paths:
        raise FileNotFoundError(f"missing forces history below {case}")
    force_path = force_paths[-1]
    solver_log = run / "coarse/artifacts/foamRun.log"
    force_history = _force_history(force_path, source_x=-1.0e-3)
    residual_history = _residual_history(solver_log)
    traction_rows = [row for row in force_history if row["iteration"] > 0.0]
    traction_trend = _trend(traction_rows, "relativeTractionImbalance")
    residual_trend = _trend(residual_history, "maxEquationInitialResidual")
    final_traction = traction_rows[-1]["relativeTractionImbalance"]
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
    stabilized_observation_path = run / "coarse/artifacts/observation.json"
    stabilized_observation = json.loads(stabilized_observation_path.read_text(encoding="utf-8"))
    first_ux = residual_history[0]["components"]["Ux"]
    report = {
        "schema": SCHEMA,
        "status": "audited",
        "scientificStatus": "analysis-only",
        "validated": False,
        "singleChange": {
            "parameter": "U equation relaxation",
            "before": equivalence["relaxation"]["before"],
            "after": equivalence["relaxation"]["after"],
            "rationale": "Damp the retained even/odd SIMPLE traction cycle using the value already established by FlowLab's reproduced straight-pipe runner.",
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
            "trend": traction_trend,
            "lastWindow": traction_rows[-TREND_WINDOW:],
        },
        "equationInitialResidual": {
            "trend": residual_trend,
            "lastWindow": residual_history[-TREND_WINDOW:],
            "firstIterationUxLinearSolve": first_ux,
        },
        "relativeToUnrelaxedControl": {
            "velocityL2ErrorRatio": stabilized_observation["velocity_l2_error"] / control_observation["velocity_l2_error"],
            "pressureL2ErrorRatio": stabilized_observation["pressure_l2_error"] / control_observation["pressure_l2_error"],
            "boundaryTractionRelativeImbalanceRatio": stabilized_observation["boundary_traction_relative_imbalance"] / control_observation["boundary_traction_relative_imbalance"],
        },
        "conclusion": {
            "classification": classification,
            "coarseAdvancementStatus": advancement["status"],
            "threeGridForcedMms": advancement["nextStage"]["forcedMmsThreeGrid"],
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
            "stabilizedObservation": {
                "path": str(stabilized_observation_path),
                "sha256": _sha(stabilized_observation_path),
            },
            "advancementReport": {"path": str(advancement_path), "sha256": _sha(advancement_path)},
        },
    }
    output = run / "artifacts/u-relaxation-stabilization-audit.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--control-run", type=Path, required=True)
    parser.add_argument("--expected-relaxation", type=float, default=0.9)
    args = parser.parse_args()
    report = stabilization_audit(
        args.run.resolve(),
        control_run=args.control_run.resolve(),
        expected_relaxation=args.expected_relaxation,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
