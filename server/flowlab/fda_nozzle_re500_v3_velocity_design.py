"""Offline validator for the FDA Re=500 V3 velocity-campaign design.

This module validates a declarative design only. It has no solver, Docker,
case-generation, postprocessing, or campaign-assessment command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "flowlab.fda-nozzle-re500-v3-velocity-verification-design.v1"
ASSESSMENT_SCHEMA = (
    "flowlab.fda-nozzle-re500-v3-velocity-verification-design-assessment.v1"
)
EXPECTED_LEVELS = {
    "coarse": 44_256,
    "medium": 354_048,
    "fine": 2_832_384,
}
EXPECTED_STATIONS_M = [-0.088, -0.048, -0.008, 0.008, 0.024, 0.080]
EXPECTED_FUNCTIONALS = [
    "chordMeanAxialVelocity",
    "chordRmsAxialVelocity",
    "peakAxialVelocity",
]
EXPECTED_CASES = [
    ("fine-stationarity-a", "fine", 1.0, "phase-1"),
    ("fine-stationarity-b", "fine", 1.0, "phase-1"),
    ("coarse-nominal", "coarse", 1.0, "phase-2"),
    ("medium-nominal", "medium", 1.0, "phase-2"),
    ("input-minus-5pct-medium", "medium", 0.95, "phase-2"),
    ("input-plus-5pct-medium", "medium", 1.05, "phase-2"),
]
RETAINED_TIMES_SECONDS = {
    "coarse-nominal": 43.875743,
    "medium-nominal": 515.044606,
    "fine-stationarity-a": 5151.812454,
    "fine-stationarity-b": 5151.812454,
    "input-minus-5pct-medium": 503.364664,
    "input-plus-5pct-medium": 521.957564,
}
SOURCE_FINE_CELLS = 1_307_904


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(_root()).as_posix()
    except ValueError:
        return "external-contract-not-retained"


def _require_new(path: Path, description: str) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite existing {description}: {path}")


def expected_compute_estimate() -> dict[str, Any]:
    cell_scale = EXPECTED_LEVELS["fine"] / SOURCE_FINE_CELLS
    cases = {
        label: retained * cell_scale
        for label, retained in RETAINED_TIMES_SECONDS.items()
    }
    baseline_seconds = sum(cases.values())
    planning_multiplier = 1.8
    return {
        "basis": "retained-v2-serial-wall-time-scaled-linearly-by-cell-count",
        "cellCountScale": cell_scale,
        "retainedV2Seconds": RETAINED_TIMES_SECONDS,
        "estimatedCaseSeconds": cases,
        "baselineSolverHours": baseline_seconds / 3600.0,
        "planningMultiplier": planning_multiplier,
        "plannedWallClockHours": baseline_seconds * planning_multiplier / 3600.0,
        "reservedRunWindowHours": 16.0,
        "minimumDockerMemoryGiB": 16.0,
        "minimumDockerSwapGiB": 4.0,
        "minimumFreeDiskGiB": 20.0,
        "maximumConcurrentSolverCases": 1,
        "estimateIsAuthorization": False,
        "uncertainty": "Planning estimate only; pressure-solver cost and I/O need not scale linearly with cells.",
    }


def _source_integrity(contract: dict[str, Any]) -> bool:
    for item in contract.get("sourceEvidence", []):
        path = _root() / item["path"]
        if not path.is_file() or _sha256(path) != item["sha256"]:
            return False
    estimate = contract.get("computeEstimateDocument", {})
    estimate_path = _root() / estimate.get("path", "")
    return estimate_path.is_file() and _sha256(estimate_path) == estimate.get("sha256")


def _case_matrix(contract: dict[str, Any]) -> list[tuple[str, str, float, str]]:
    return [
        (
            str(case["label"]),
            str(case["meshLevel"]),
            float(case["flowScale"]),
            str(case["phase"]),
        )
        for case in contract.get("intendedCases", [])
    ]


def validate_design(contract_path: Path) -> dict[str, Any]:
    contract = _read_json(contract_path)
    acceptance = contract.get("acceptance", {})
    uncertainty = contract.get("uncertainty", {})
    convergence = contract.get("iterativeConvergence", {})
    authorization = contract.get("authorization", {})
    mesh = contract.get("acceptedMeshFamily", {})
    observation = contract.get("observationDesign", {})
    estimate = contract.get("computeEstimate", {})
    expected_estimate = expected_compute_estimate()

    checks = {
        "schema": contract.get("schema") == SCHEMA,
        "statusDesignOnly": contract.get("status")
        == "frozen-design-complete-execution-not-authorized",
        "sourceIntegrity": _source_integrity(contract),
        "pressureNonpromotional": contract.get("pressurePolicy", {}).get("status")
        == "mandatory-diagnostic-nonpromotional",
        "velocityOnlyContext": contract.get("contextOfUse", {}).get(
            "promotionEligibleQuantities"
        )
        == ["axialVelocityProfiles", "centrelineAxialVelocity"],
        "acceptedMeshFamilyExact": mesh.get("cells") == EXPECTED_LEVELS
        and mesh.get("volumetricCellRatios") == [8.0, 8.0]
        and mesh.get("geometryPreflightPassed") is True,
        "caseMatrixExact": _case_matrix(contract) == EXPECTED_CASES,
        "profileStationsExact": observation.get("axialProfileStationsM")
        == EXPECTED_STATIONS_M,
        "criticalFunctionalsExact": observation.get("criticalStationFunctionals")
        == EXPECTED_FUNCTIONALS,
        "trialLevelScalarizationRequired": observation.get(
            "trialLevelScalarizationBeforeAggregation"
        )
        is True,
        "denseProfilesDiagnosticOnly": observation.get("densePointwiseProfiles", {}).get(
            "promotionRole"
        )
        == "mandatory-diagnostic-nonpromotional",
        "everyCriticalQoiMustQualify": acceptance.get(
            "everyCriticalFunctionalMustQualify"
        )
        is True
        and acceptance.get("everyCriticalFunctionalMustPass") is True,
        "vv20ScalarRuleExact": acceptance.get("scalarFunctionalRule")
        == "abs(comparisonError) <= validationUncertainty",
        "historicalNinetyPercentNotReused": acceptance.get(
            "historicalAggregatePassFractionGate"
        )
        is None
        and acceptance.get("pointwisePassFractionMayAuthorize") is False,
        "uncertaintyComplete": uncertainty.get("requiredComponents")
        == [
            "experimental95",
            "input",
            "iterative",
            "combinedGeometryAndSolutionDiscretization",
            "observationOperator",
        ]
        and uncertainty.get("gciSafetyFactor") == 1.25
        and uncertainty.get("linearRefinementRatio") == 2.0,
        "unqualifiedGridSequenceBlocks": uncertainty.get(
            "unqualifiedThreeGridAction"
        )
        == "block-and-design-fourth-grid-under-new-contract",
        "iterativeThresholdsProspective": convergence.get("snapshots")
        == [700, 750, 800]
        and convergence.get("maximumCriticalQoiRelativeDrift750To800") == 0.0025
        and convergence.get("maximumFinalLinearResidual") == {
            "velocity": 1.0e-10,
            "pressure": 1.0e-10,
        },
        "coldFineRepeatRequired": convergence.get("coldFineRepeatRequired") is True
        and convergence.get("maximumCriticalQoiRepeatRelativeDifference")
        == 1.0e-10,
        "computeEstimateExact": _numbers_close(estimate, expected_estimate),
        "singleWorkerResourceBoundary": estimate.get(
            "maximumConcurrentSolverCases"
        )
        == 1
        and estimate.get("minimumDockerMemoryGiB") == 16.0,
        "executionFailsClosed": authorization
        == {
            "designComplete": True,
            "prepareCampaign": False,
            "runSolver": False,
            "runStationarityDiagnostic": False,
            "runIndependentSolver": False,
            "runFullSuccessorCampaign": False,
            "scientificPromotion": False,
            "desktopPromotion": False,
        },
        "promotionFalse": contract.get("scientificPromotionAuthorized") is False
        and contract.get("desktopPromotionAuthorized") is False
        and contract.get("promotionAuthorized") is False,
    }
    passed = all(checks.values())
    return {
        "schema": ASSESSMENT_SCHEMA,
        "contract": _relative_to_root(contract_path),
        "contractSha256": _sha256(contract_path),
        "validatorSha256": _sha256(Path(__file__).resolve()),
        "status": (
            "design-valid-execution-blocked"
            if passed
            else "design-invalid-fail-closed"
        ),
        "checks": checks,
        "computeEstimate": expected_estimate,
        "nextAuthorizedWork": (
            "independent-review-of-design-and-separate-execution-authorization"
            if passed
            else "repair-design-under-new-contract-revision"
        ),
        "solverExecutionAuthorized": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }


def _numbers_close(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(
            _numbers_close(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and math.isclose(
            float(actual), expected, rel_tol=1.0e-12, abs_tol=1.0e-12
        )
    return actual == expected


def render_report(assessment: dict[str, Any]) -> str:
    estimate = assessment["computeEstimate"]
    lines = [
        "# FDA Re=500 V3 velocity-verification design validation",
        "",
        f"Status: **{assessment['status']}**",
        "",
        "This is an offline design assessment. It does not prepare or execute a CFD campaign and cannot authorize promotion.",
        "",
        "## Checks",
        "",
        *[
            f"- `{name}`: {'pass' if passed else 'fail'}"
            for name, passed in assessment["checks"].items()
        ],
        "",
        "## Planning estimate",
        "",
        f"- Baseline serial solver estimate: {estimate['baselineSolverHours']:.2f} hours",
        f"- Planned wall-clock estimate with contingency: {estimate['plannedWallClockHours']:.2f} hours",
        f"- Reserved run window: {estimate['reservedRunWindowHours']:.1f} hours",
        f"- Minimum Docker memory/swap: {estimate['minimumDockerMemoryGiB']:.0f}/{estimate['minimumDockerSwapGiB']:.0f} GiB",
        f"- Minimum free disk: {estimate['minimumFreeDiskGiB']:.0f} GiB",
        "",
        "Only independent review and a separate execution authorization may follow this design validation. Solver and promotion authorization remain false.",
        "",
    ]
    return "\n".join(lines)


def write_assessment(
    contract_path: Path, output_path: Path, report_path: Path
) -> dict[str, Any]:
    _require_new(output_path, "design assessment")
    _require_new(report_path, "design report")
    assessment = validate_design(contract_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(assessment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_report(assessment), encoding="utf-8")
    return assessment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    assessment = write_assessment(
        args.contract.resolve(), args.output.resolve(), args.report.resolve()
    )
    print(json.dumps(assessment, indent=2, sort_keys=True))
    return 0 if assessment["status"] == "design-valid-execution-blocked" else 3


if __name__ == "__main__":
    raise SystemExit(main())
