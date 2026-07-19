"""Run and analyze the bounded 2^4 coarse forced-MMS diagnostic matrix."""
from __future__ import annotations

import argparse
from itertools import combinations, product
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .open_boundary_campaign import LINEAR_RESIDUAL_LIMIT, MASS_LIMIT, MmsDefinition
from .open_boundary_mms_runner import _observe, _write
from .open_boundary_traction_history import _force_history, _residual_history, _sha, _trend


SCHEMA = "flowlab.open-boundary-factorial-matrix.v1"
FACTOR_ORDER = (
    "outletVelocity",
    "simpleConsistent",
    "uSolverType",
    "uEquationRelaxation",
)
FACTOR_LEVELS: dict[str, tuple[Any, Any]] = {
    "outletVelocity": ("pressureInletOutletVelocity", "zeroGradient"),
    "simpleConsistent": (True, False),
    "uSolverType": ("smoothSolver", "PBiCGStab"),
    "uEquationRelaxation": (1.0, 0.9),
}
RESPONSE_KEYS = (
    "boundaryTractionRelativeImbalance",
    "velocityL2Error",
    "pressureL2Error",
    "log10FirstUxFinalResidual",
    "firstUxLinearIterations",
)


def matrix_configurations() -> list[dict[str, Any]]:
    return [
        dict(zip(FACTOR_ORDER, levels))
        for levels in product(*(FACTOR_LEVELS[factor] for factor in FACTOR_ORDER))
    ]


def _slug(config: dict[str, Any]) -> str:
    outlet = "piov" if config["outletVelocity"] == "pressureInletOutletVelocity" else "zg"
    consistent = "yes" if config["simpleConsistent"] else "no"
    solver = "smooth" if config["uSolverType"] == "smoothSolver" else "pbicgstab"
    relaxation = "10" if math.isclose(config["uEquationRelaxation"], 1.0) else "09"
    return f"outlet-{outlet}__consistent-{consistent}__solver-{solver}__relax-{relaxation}"


def _coded(config: dict[str, Any]) -> dict[str, int]:
    return {
        factor: -1 if config[factor] == FACTOR_LEVELS[factor][0] else 1
        for factor in FACTOR_ORDER
    }


def _change_count(config: dict[str, Any]) -> int:
    return sum(config[factor] != FACTOR_LEVELS[factor][0] for factor in FACTOR_ORDER)


def _history_metrics(case: Path, artifacts: Path) -> dict[str, Any]:
    force_paths = sorted((case / "postProcessing/forces").glob("**/force*.dat"))
    solver_log = artifacts / "foamRun.log"
    try:
        forces = _force_history(force_paths[-1], source_x=-1.0e-3)
        residuals = _residual_history(solver_log)
    except (FileNotFoundError, IndexError, ValueError):
        return {
            "complete": False,
            "firstUx": None,
            "tractionTrend": None,
            "equationInitialResidualTrend": None,
            "forceSamples": 0,
            "residualSamples": 0,
        }
    traction_rows = [row for row in forces if row["iteration"] > 0.0]
    first_ux = residuals[0]["components"]["Ux"]
    traction_trend = _trend(traction_rows, "relativeTractionImbalance") if len(traction_rows) >= 20 else None
    residual_trend = _trend(residuals, "maxEquationInitialResidual") if len(residuals) >= 20 else None
    return {
        "complete": len(traction_rows) == 100 and len(residuals) == 100,
        "firstUx": first_ux,
        "firstUxHealthy": first_ux["finalResidual"] <= LINEAR_RESIDUAL_LIMIT and first_ux["linearIterations"] < 1000,
        "tractionTrend": traction_trend,
        "equationInitialResidualTrend": residual_trend,
        "forceSamples": len(forces),
        "residualSamples": len(residuals),
        "rawEvidence": {
            "forces": {"path": str(force_paths[-1]), "sha256": _sha(force_paths[-1])},
            "solverLog": {"path": str(solver_log), "sha256": _sha(solver_log)},
        },
    }


def _cell_result(
    config: dict[str, Any],
    observation: dict[str, Any],
    *,
    case: Path,
    artifacts: Path,
    baseline: dict[str, Any],
    reused: bool,
    source_run: Path | None,
) -> dict[str, Any]:
    history = _history_metrics(case, artifacts)
    checks = {
        "historyComplete": history["complete"],
        "checkMesh": bool(observation["check_mesh_passed"]),
        "massRelativeImbalance": observation["mass_relative_imbalance"] <= MASS_LIMIT,
        "boundaryTractionRelativeImbalance": observation["boundary_traction_relative_imbalance"] <= MASS_LIMIT,
        "finalLinearResidual": observation["final_linear_residual"] <= LINEAR_RESIDUAL_LIMIT,
        "velocityErrorImprovedVsV10": observation["velocity_l2_error"] < baseline["velocity_l2_error"],
        "pressureErrorImprovedVsV10": observation["pressure_l2_error"] < baseline["pressure_l2_error"],
    }
    first_ux = history["firstUx"]
    responses = {
        "boundaryTractionRelativeImbalance": observation["boundary_traction_relative_imbalance"],
        "velocityL2Error": observation["velocity_l2_error"],
        "pressureL2Error": observation["pressure_l2_error"],
        "log10FirstUxFinalResidual": math.log10(max(first_ux["finalResidual"], 1.0e-300)) if first_ux else math.inf,
        "firstUxLinearIterations": first_ux["linearIterations"] if first_ux else math.inf,
    }
    return {
        "cellId": _slug(config),
        "configuration": config,
        "codedFactors": _coded(config),
        "changeCountVsCurrent": _change_count(config),
        "reused": reused,
        "sourceRun": str(source_run) if source_run else None,
        "case": str(case),
        "artifacts": str(artifacts),
        "observation": observation,
        "history": history,
        "responses": responses,
        "hardGateChecks": checks,
        "failedHardGates": [name for name, passed in checks.items() if not passed],
        "passesCoarseGate": all(checks.values()),
    }


def _effect(cells: Iterable[dict[str, Any]], response: str, factors: tuple[str, ...]) -> dict[str, Any]:
    positive: list[float] = []
    negative: list[float] = []
    for cell in cells:
        value = float(cell["responses"][response])
        if not math.isfinite(value):
            continue
        sign = math.prod(cell["codedFactors"][factor] for factor in factors)
        (positive if sign > 0 else negative).append(value)
    if not positive or not negative:
        effect = None
    else:
        effect = sum(positive) / len(positive) - sum(negative) / len(negative)
    return {
        "factors": list(factors),
        "response": response,
        "contrast": "mean(coded product +1) - mean(coded product -1)",
        "effect": effect,
        "positiveCount": len(positive),
        "negativeCount": len(negative),
    }


def calculate_effects(cells: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    main = {
        factor: {
            "currentLevel": FACTOR_LEVELS[factor][0],
            "alternativeLevel": FACTOR_LEVELS[factor][1],
            "responses": {response: _effect(cells, response, (factor,)) for response in RESPONSE_KEYS},
        }
        for factor in FACTOR_ORDER
    }
    interactions = {
        " x ".join(pair): {
            "responses": {response: _effect(cells, response, pair) for response in RESPONSE_KEYS}
        }
        for pair in combinations(FACTOR_ORDER, 2)
    }
    return (
        {
            "schema": "flowlab.open-boundary-factorial-main-effects.v1",
            "method": "balanced two-level full-factorial contrast; deterministic diagnostic, no p-values",
            "effects": main,
        },
        {
            "schema": "flowlab.open-boundary-factorial-two-factor-interactions.v1",
            "method": "balanced two-level full-factorial interaction contrast; deterministic diagnostic, no p-values",
            "effects": interactions,
        },
    )


def select_candidate(cells: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    eligible = [cell for cell in cells if cell["passesCoarseGate"]]
    ranked = sorted(
        eligible,
        key=lambda cell: (
            not bool(cell["history"].get("firstUxHealthy")),
            cell["changeCountVsCurrent"],
            max(
                cell["observation"]["velocity_l2_error"] / baseline["velocity_l2_error"],
                cell["observation"]["pressure_l2_error"] / baseline["pressure_l2_error"],
            ),
            cell["observation"]["velocity_l2_error"] + cell["observation"]["pressure_l2_error"],
            cell["cellId"],
        ),
    )
    selected = ranked[0] if ranked else None
    return {
        "schema": "flowlab.open-boundary-factorial-candidate-selection.v1",
        "status": "selected-for-independent-confirmation" if selected else "no-coarse-passing-candidate",
        "policy": [
            "all immutable coarse gates must pass",
            "prefer a healthy first Ux solve",
            "prefer the fewest changes from the current pressureInletOutletVelocity configuration",
            "then minimize the worst normalized velocity/pressure field error",
        ],
        "eligibleCellIds": [cell["cellId"] for cell in ranked],
        "selectedCellId": selected["cellId"] if selected else None,
        "selectedConfiguration": selected["configuration"] if selected else None,
        "selectedChangeCount": selected["changeCountVsCurrent"] if selected else None,
        "requiresIndependentConfirmation": selected is not None,
        "threeGridForcedMms": "blocked pending independent confirmation" if selected else "blocked",
    }


def run_matrix(
    output: Path,
    *,
    baseline_observation: Path,
    reuse_unrelaxed_run: Path,
    reuse_relaxed_run: Path,
    solver_image_digest: str,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    baseline = json.loads(baseline_observation.read_text(encoding="utf-8"))
    configurations = matrix_configurations()
    reuse = {
        _slug(matrix_configurations()[0]): reuse_unrelaxed_run,
        _slug(matrix_configurations()[1]): reuse_relaxed_run,
    }
    spec = {
        "schema": SCHEMA,
        "scientificStatus": "diagnostic-only",
        "validated": False,
        "design": "2^4 full factorial, coarse grid, serial execution",
        "factorOrder": list(FACTOR_ORDER),
        "factorLevels": {factor: list(levels) for factor, levels in FACTOR_LEVELS.items()},
        "cellCount": len(configurations),
        "reusedCellCount": len(reuse),
        "newCellCount": len(configurations) - len(reuse),
        "frozenControls": [
            "12x12x12 structured all-hex mesh",
            "100 outer iterations",
            "1e-6 traction and mass gates",
            "1e-8 final linear residual gate",
            "fixed outlet pressure",
            "momentum source",
            "numerical schemes",
            "linear tolerances",
            "exact initialization",
        ],
        "solverImageDigest": solver_image_digest,
        "baselineObservation": {"path": str(baseline_observation), "sha256": _sha(baseline_observation)},
        "mobile": {"inScope": False, "changes": "none"},
    }
    _write(output / "matrix-spec.json", json.dumps(spec, indent=2, sort_keys=True) + "\n")
    cells: list[dict[str, Any]] = []
    mms = MmsDefinition()
    for index, config in enumerate(configurations, start=1):
        cell_id = _slug(config)
        print(f"MATRIX_CELL {index}/16 {cell_id} start", flush=True)
        if cell_id in reuse:
            source = reuse[cell_id]
            case = source / "coarse/case"
            artifacts = source / "coarse/artifacts"
            observation = json.loads((artifacts / "observation.json").read_text(encoding="utf-8"))
            result = _cell_result(
                config,
                observation,
                case=case,
                artifacts=artifacts,
                baseline=baseline,
                reused=True,
                source_run=source,
            )
        else:
            observation_object = _observe(
                output / "cells",
                cell_id,
                12,
                mms,
                outlet_velocity_type=config["outletVelocity"],
                force_write_interval=1,
                u_equation_relaxation=config["uEquationRelaxation"],
                simple_consistent=config["simpleConsistent"],
                u_solver_type=config["uSolverType"],
            )
            case = output / "cells" / cell_id / "case"
            artifacts = output / "cells" / cell_id / "artifacts"
            result = _cell_result(
                config,
                observation_object.__dict__,
                case=case,
                artifacts=artifacts,
                baseline=baseline,
                reused=False,
                source_run=None,
            )
            _write(artifacts / "matrix-cell-report.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
        cells.append(result)
        print(
            f"MATRIX_CELL {index}/16 {cell_id} complete pass={str(result['passesCoarseGate']).lower()} "
            f"traction={result['observation']['boundary_traction_relative_imbalance']:.17g}",
            flush=True,
        )
    main_effects, interactions = calculate_effects(cells)
    selection = select_candidate(cells, baseline)
    results = {
        "schema": "flowlab.open-boundary-factorial-results.v1",
        "status": "complete",
        "scientificStatus": "diagnostic-only",
        "validated": False,
        "cellCount": len(cells),
        "reusedCellCount": sum(cell["reused"] for cell in cells),
        "newCellCount": sum(not cell["reused"] for cell in cells),
        "passingCellIds": [cell["cellId"] for cell in cells if cell["passesCoarseGate"]],
        "cells": cells,
    }
    _write(output / "matrix-results.json", json.dumps(results, indent=2, sort_keys=True) + "\n")
    _write(output / "main-effects.json", json.dumps(main_effects, indent=2, sort_keys=True) + "\n")
    _write(output / "interaction-effects.json", json.dumps(interactions, indent=2, sort_keys=True) + "\n")
    _write(output / "candidate-selection.json", json.dumps(selection, indent=2, sort_keys=True) + "\n")
    report = {
        "schema": "flowlab.open-boundary-factorial-report.v1",
        "status": "complete",
        "scientificStatus": "diagnostic-only",
        "validated": False,
        "matrix": {
            "cellCount": len(cells),
            "passingCellCount": len(results["passingCellIds"]),
            "reusedCellCount": results["reusedCellCount"],
            "newCellCount": results["newCellCount"],
        },
        "selection": selection,
        "artifacts": {
            "spec": {"path": str(output / "matrix-spec.json"), "sha256": _sha(output / "matrix-spec.json")},
            "results": {"path": str(output / "matrix-results.json"), "sha256": _sha(output / "matrix-results.json")},
            "mainEffects": {"path": str(output / "main-effects.json"), "sha256": _sha(output / "main-effects.json")},
            "interactions": {"path": str(output / "interaction-effects.json"), "sha256": _sha(output / "interaction-effects.json")},
            "selection": {"path": str(output / "candidate-selection.json"), "sha256": _sha(output / "candidate-selection.json")},
        },
        "nextStage": "independent coarse confirmation" if selection["selectedCellId"] else "first-iteration Ux matrix audit; no configuration passed",
    }
    _write(output / "matrix-report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-observation", type=Path, required=True)
    parser.add_argument("--reuse-unrelaxed-run", type=Path, required=True)
    parser.add_argument("--reuse-relaxed-run", type=Path, required=True)
    parser.add_argument("--solver-image-digest", required=True)
    args = parser.parse_args()
    report = run_matrix(
        args.output.resolve(),
        baseline_observation=args.baseline_observation.resolve(),
        reuse_unrelaxed_run=args.reuse_unrelaxed_run.resolve(),
        reuse_relaxed_run=args.reuse_relaxed_run.resolve(),
        solver_image_digest=args.solver_image_digest,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
