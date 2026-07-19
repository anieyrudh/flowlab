"""Audit coarse forced-MMS traction and equation-residual history.

This module changes no OpenFOAM input.  It compares a diagnostic rerun with a
retained control, verifies that only function-object sampling changed, and
classifies the last 20 frozen iterations using a predeclared 10% decay rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .open_boundary_campaign import MASS_LIMIT, MmsDefinition


SCHEMA = "flowlab.open-boundary-traction-history-audit.v1"
TREND_WINDOW = 20
MINIMUM_DECAY_FRACTION = 0.10
SOLVE_FILES = (
    "0/U",
    "0/p",
    "constant/fvModels",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "system/blockMeshDict",
    "system/fvSchemes",
    "system/fvSolution",
)
VECTOR_PATTERN = re.compile(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)")
TIME_PATTERN = re.compile(r"^Time = ([-+0-9.eE]+)s?$")
SOLVE_PATTERN = re.compile(
    r"Solving for (Ux|Uy|Uz|p), Initial residual = ([-+0-9.eE]+), "
    r"Final residual = ([-+0-9.eE]+), No Iterations (\d+)"
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _force_history(path: Path, *, source_x: float) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        vectors = VECTOR_PATTERN.findall(line)
        if len(vectors) < 2:
            continue
        pressure_x = float(vectors[0][0])
        viscous_x = float(vectors[1][0])
        force_x = pressure_x + viscous_x
        rows.append(
            {
                "iteration": float(line.split()[0]),
                "pressureForceX": pressure_x,
                "viscousForceX": viscous_x,
                "totalForceX": force_x,
                "relativeTractionImbalance": abs(force_x - source_x) / max(abs(source_x), 1.0e-30),
            }
        )
    if not rows:
        raise ValueError(f"no force history found in {path}")
    return rows


def _residual_history(path: Path) -> list[dict[str, Any]]:
    records: dict[float, dict[str, Any]] = {}
    current_time: float | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        time_match = TIME_PATTERN.match(line.strip())
        if time_match:
            current_time = float(time_match.group(1))
            records[current_time] = {"iteration": current_time, "components": {}}
            continue
        solve_match = SOLVE_PATTERN.search(line)
        if current_time is None or solve_match is None:
            continue
        field, initial, final, count = solve_match.groups()
        records[current_time]["components"][field] = {
            "initialResidual": float(initial),
            "finalResidual": float(final),
            "linearIterations": int(count),
        }
    history: list[dict[str, Any]] = []
    for iteration in sorted(records):
        record = records[iteration]
        components = record["components"]
        if not all(field in components for field in ("Ux", "Uy", "Uz", "p")):
            continue
        velocity_initial = max(components[field]["initialResidual"] for field in ("Ux", "Uy", "Uz"))
        history.append(
            {
                **record,
                "maxVelocityEquationInitialResidual": velocity_initial,
                "pressureEquationInitialResidual": components["p"]["initialResidual"],
                "maxEquationInitialResidual": max(
                    component["initialResidual"] for component in components.values()
                ),
                "maxLinearFinalResidual": max(
                    component["finalResidual"] for component in components.values()
                ),
            }
        )
    if not history:
        raise ValueError(f"no complete equation-residual history found in {path}")
    return history


def _log_slope(rows: list[dict[str, Any]], key: str) -> float | None:
    points = [(float(row["iteration"]), float(row[key])) for row in rows if float(row[key]) > 0.0]
    if len(points) < 2:
        return None
    x_mean = sum(point[0] for point in points) / len(points)
    y_values = [math.log(point[1]) for point in points]
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((point[0] - x_mean) ** 2 for point in points)
    if denominator == 0.0:
        return None
    return sum((point[0] - x_mean) * (value - y_mean) for point, value in zip(points, y_values)) / denominator


def _trend(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    if len(rows) < TREND_WINDOW:
        raise ValueError(f"at least {TREND_WINDOW} samples are required")
    window = rows[-TREND_WINDOW:]
    start = float(window[0][key])
    end = float(window[-1][key])
    relative_drop = (start - end) / max(abs(start), 1.0e-30)
    decreasing_steps = sum(
        float(left[key]) > float(right[key]) for left, right in zip(window, window[1:])
    )
    return {
        "window": TREND_WINDOW,
        "startIteration": window[0]["iteration"],
        "endIteration": window[-1]["iteration"],
        "start": start,
        "end": end,
        "relativeDrop": relative_drop,
        "logSlopePerIteration": _log_slope(window, key),
        "decreasingStepFraction": decreasing_steps / (TREND_WINDOW - 1),
    }


def _normalize_control_dict(text: str) -> str:
    pattern = re.compile(r"(forces\s*\{.*?writeInterval\s+)\d+(;)", re.DOTALL)
    normalized, count = pattern.subn(r"\g<1><diagnostic-sampling>\2", text, count=1)
    if count != 1:
        raise ValueError("could not isolate forces writeInterval in controlDict")
    return normalized


def _control_equivalence(control_case: Path, diagnostic_case: Path) -> dict[str, Any]:
    file_checks = {
        name: {
            "same": _sha(control_case / name) == _sha(diagnostic_case / name),
            "controlSha256": _sha(control_case / name),
            "diagnosticSha256": _sha(diagnostic_case / name),
        }
        for name in SOLVE_FILES
    }
    control_dict = (control_case / "system/controlDict").read_text(encoding="utf-8")
    diagnostic_dict = (diagnostic_case / "system/controlDict").read_text(encoding="utf-8")
    normalized_same = _normalize_control_dict(control_dict) == _normalize_control_dict(diagnostic_dict)
    return {
        "solveFiles": file_checks,
        "allSolveFilesIdentical": all(check["same"] for check in file_checks.values()),
        "controlDictIdenticalAfterForceSamplingNormalization": normalized_same,
        "onlyDiagnosticSamplingChanged": normalized_same and all(check["same"] for check in file_checks.values()),
    }


def traction_history_audit(run: Path, *, control_run: Path) -> dict[str, Any]:
    case = run / "coarse/case"
    control_case = control_run / "coarse/case"
    force_paths = sorted((case / "postProcessing/forces").glob("**/force*.dat"))
    if not force_paths:
        raise FileNotFoundError(f"missing forces history below {case}")
    force_path = force_paths[-1]
    solver_log = run / "coarse/artifacts/foamRun.log"
    spec = MmsDefinition()
    force_history = _force_history(force_path, source_x=-spec.pressure_gradient_m2_s2_per_m)
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
    projected_gate_iteration = None
    if final_traction > MASS_LIMIT and traction_slope is not None and traction_slope < 0.0:
        projected_gate_iteration = math.ceil(
            traction_rows[-1]["iteration"] + math.log(MASS_LIMIT / final_traction) / traction_slope
        )
    advancement_report_path = run / "artifacts/coarse-advancement-report.json"
    advancement = json.loads(advancement_report_path.read_text(encoding="utf-8"))
    equivalence = _control_equivalence(control_case, case)
    report = {
        "schema": SCHEMA,
        "status": "audited",
        "scientificStatus": "analysis-only",
        "validated": False,
        "method": {
            "trendWindow": TREND_WINDOW,
            "minimumRelativeDecay": MINIMUM_DECAY_FRACTION,
            "classificationRule": "Still decaying requires at least 10% reduction and a negative log slope for both traction imbalance and maximum equation-initial residual over the last 20 frozen iterations.",
            "projectionIsExploratoryOnly": True,
        },
        "controlEquivalence": equivalence,
        "samples": {
            "force": len(force_history),
            "forceExcludingInitial": len(traction_rows),
            "equationResidual": len(residual_history),
        },
        "traction": {
            "limit": MASS_LIMIT,
            "final": final_traction,
            "passes": final_traction <= MASS_LIMIT,
            "trend": traction_trend,
            "history": force_history,
        },
        "equationInitialResidual": {
            "trend": residual_trend,
            "history": residual_history,
        },
        "conclusion": {
            "classification": classification,
            "projectedGateIterationAtLastWindowLogSlope": projected_gate_iteration,
            "threeGridForcedMms": advancement["nextStage"]["forcedMmsThreeGrid"],
            "gateUnchanged": True,
            "iterationLimitUnchanged": True,
        },
        "rawEvidence": {
            "forces": {"path": str(force_path), "sha256": _sha(force_path)},
            "solverLog": {"path": str(solver_log), "sha256": _sha(solver_log)},
            "advancementReport": {
                "path": str(advancement_report_path),
                "sha256": _sha(advancement_report_path),
            },
        },
    }
    output = run / "artifacts/traction-history-audit.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--control-run", type=Path, required=True)
    args = parser.parse_args()
    report = traction_history_audit(args.run.resolve(), control_run=args.control_run.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
