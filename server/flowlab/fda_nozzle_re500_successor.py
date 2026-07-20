"""Read-only successor preflight for the FDA nozzle Re=500 campaign.

This module does not reassess, edit, or promote a retained campaign.  It reads
the immutable v2 evidence and produces a new diagnostic record that determines
whether a successor CFD campaign is scientifically ready to run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from server.flowlab.fda_nozzle_re500 import FdaNozzleDefinition


SCHEMA = "flowlab.fda-nozzle-re500.successor-preflight.v1"
EXPECTED_PRESSURE_CODES = (243, 468, 763)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matrix_rank(matrix: list[list[float]], tolerance: float = 1.0e-10) -> int:
    """Return numerical rank using deterministic Gaussian elimination."""
    if not matrix:
        return 0
    width = len(matrix[0])
    if any(len(row) != width for row in matrix):
        raise ValueError("matrix rows must have equal length")
    work = [[float(value) for value in row] for row in matrix]
    scale = max((abs(value) for row in work for value in row), default=0.0)
    cutoff = tolerance * max(1.0, scale)
    rank = 0
    for column in range(width):
        pivot = max(range(rank, len(work)), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= cutoff:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][column]
        for col in range(column, width):
            work[rank][col] /= pivot_value
        for row in range(len(work)):
            if row == rank:
                continue
            factor = work[row][column]
            if abs(factor) <= cutoff:
                continue
            for col in range(column, width):
                work[row][col] -= factor * work[rank][col]
        rank += 1
        if rank == len(work):
            break
    return rank


def parse_check_mesh(text: str) -> dict[str, Any]:
    """Extract geometry/refinement invariants from an OpenFOAM checkMesh log."""
    patterns = {
        "cells": r"^\s*cells:\s+(\d+)\s*$",
        "hexahedra": r"^\s*hexahedra:\s+(\d+)\s*$",
        "inletFaces": r"^\s*inlet\s+(\d+)\s+\d+\s+ok\b",
        "totalVolumeM3": r"Total volume = ([0-9.eE+-]+)\.",
        "maximumNonOrthogonalityDegrees": (
            r"Mesh non-orthogonality Max: ([0-9.eE+-]+)"
        ),
    }
    result: dict[str, Any] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match is None:
            raise ValueError(f"checkMesh log is missing {key}")
        value = match.group(1)
        result[key] = int(value) if key in {"cells", "hexahedra", "inletFaces"} else float(value)
    result["strictAllHex"] = result["cells"] == result["hexahedra"]
    result["checkMeshPassed"] = "Mesh OK." in text
    return result


def _counts(block: dict[str, Any]) -> dict[str, Any]:
    counts = block["counts"]
    eligible = int(counts["experimentalEligible"])
    qualified = int(counts["gciQualified"])
    passed = int(counts["vv20Passed"])
    return {
        "eligible": eligible,
        "gciQualified": qualified,
        "gciQualifiedFraction": qualified / eligible if eligible else None,
        "vv20Passed": passed,
        "rawPassFraction": passed / eligible if eligible else None,
        "qualifiedOnlyPassFraction": passed / qualified if qualified else None,
    }


def audit_experimental_reference(
    experiment: dict[str, Any], pressure: dict[str, Any]
) -> dict[str, Any]:
    def plot_records(dataset: dict[str, Any]) -> list[dict[str, Any]]:
        plots = dataset["plots"]
        if isinstance(plots, dict):
            return list(plots.values())
        return list(plots)

    file_codes = sorted(int(row["dataset-code"]) for row in experiment["files"])
    eligible_codes = tuple(int(code) for code in experiment["pressureEligibility"]["codes"])
    deleted_codes = sorted(
        int(row["dataset-code"])
        for row in experiment["files"]
        if any(bool(plot.get("deleted")) for plot in plot_records(row))
    )

    adjacent_rows = pressure["wall"]["adjacent"]["rows"]
    trials = [row["experiment"]["trialValuesPa"] for row in adjacent_rows]
    trial_count = len(trials[0]) if trials else 0
    if any(len(row) != trial_count for row in trials):
        raise ValueError("pressure rows have inconsistent trial counts")
    trial_vectors = [
        [float(trials[point][trial]) for point in range(len(trials))]
        for trial in range(trial_count)
    ]
    means = [
        sum(vector[index] for vector in trial_vectors) / trial_count
        for index in range(len(trials))
    ] if trial_count else []
    centred = [
        [vector[index] - means[index] for index in range(len(means))]
        for vector in trial_vectors
    ]
    rank = matrix_rank(centred)
    exact_zero_values = sum(
        math.isclose(float(value), 0.0, abs_tol=1.0e-12)
        for vector in trial_vectors
        for value in vector
    )
    dimension = len(adjacent_rows)
    rank_limit = max(0, trial_count - 1)

    qualification = experiment.get("referenceQualification", {})
    sample_covariance_full_rank = trial_count > dimension and rank == dimension
    requirements = {
        "trace-to-laboratory mapping": bool(
            qualification.get("traceToLaboratoryMapping")
        ),
        "tap-specific calibration and bias model": bool(
            qualification.get("tapCalibrationAndBiasModel")
        ),
        "full-rank tap covariance or enough independent traces to estimate it": (
            bool(qualification.get("fullRankTapCovariance"))
            or sample_covariance_full_rank
        ),
        "as-built nozzle and pressure-tap metrology": bool(
            qualification.get("asBuiltGeometryAndTapMetrology")
        ),
    }
    missing = [name for name, present in requirements.items() if not present]
    ready = (
        eligible_codes == EXPECTED_PRESSURE_CODES
        and not missing
    )
    return {
        "archiveSha256": experiment["source"]["sha256"],
        "repositoryCommit": experiment["source"]["commit"],
        "availableDatasetCodes": file_codes,
        "eligiblePressureCodes": list(eligible_codes),
        "deletedPressureDatasetCodes": deleted_codes,
        "publishedExcludedPressureDatasetCodes": [
            code for code in file_codes if code not in eligible_codes and code not in deleted_codes
        ],
        "pressureTraceCount": trial_count,
        "adjacentPressureDimension": dimension,
        "centredTraceMatrixRank": rank,
        "maximumPossibleSampleCovarianceRank": rank_limit,
        "exactZeroAdjacentTrialValues": exact_zero_values,
        "requiredReferenceArtifactsNotPresent": missing,
        "pressureReferencePromotionReady": ready,
        "finding": (
            "Three eligible pressure traces cannot identify a full 16-dimensional "
            "adjacent-tap covariance; the reference remains diagnostic, not "
            "promotion-grade."
        ),
    }


def audit_grid_family(meshes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = ("coarse", "medium", "fine")
    if tuple(meshes) != required:
        raise ValueError(f"mesh order must be {required}")
    volumes = [float(meshes[label]["totalVolumeM3"]) for label in required]
    cell_counts = [int(meshes[label]["cells"]) for label in required]
    cell_ratios = [cell_counts[index + 1] / cell_counts[index] for index in range(2)]
    volume_range = max(volumes) - min(volumes)
    relative_range = volume_range / max(volumes)
    exact_cell_refinement = all(math.isclose(ratio, 8.0) for ratio in cell_ratios)
    spec = FdaNozzleDefinition()
    inlet_length = spec.contraction_start_x_m - spec.inlet_x_m
    contraction_length = spec.throat_start_x_m - spec.contraction_start_x_m
    throat_length = spec.sudden_expansion_x_m - spec.throat_start_x_m
    downstream_length = spec.outlet_x_m - spec.sudden_expansion_x_m
    outer = spec.inlet_radius_m
    inner = spec.throat_radius_m
    nominal_volume = (
        math.pi * outer**2 * inlet_length
        + math.pi
        * contraction_length
        * (outer**2 + outer * inner + inner**2)
        / 3.0
        + math.pi * inner**2 * throat_length
        + math.pi * outer**2 * downstream_length
    )
    errors = {
        label: (float(meshes[label]["totalVolumeM3"]) - nominal_volume)
        / nominal_volume
        for label in required
    }
    error_magnitudes = [abs(errors[label]) for label in required]
    geometry_converges = all(
        right < left for left, right in zip(error_magnitudes, error_magnitudes[1:])
    )
    return {
        "meshes": meshes,
        "cellCountRatios": cell_ratios,
        "exactThreeDimensionalCellRefinement": exact_cell_refinement,
        "analyticNominalDomainVolumeM3": nominal_volume,
        "relativeDomainVolumeErrorToNominal": errors,
        "geometryErrorMagnitudeDecreasesWithRefinement": geometry_converges,
        "domainVolumeRangeM3": volume_range,
        "domainVolumeRelativeRange": relative_range,
        "geometryAndSolutionDiscretizationSeparated": False,
        "successorGridFamilyPreflightComplete": False,
        "finding": (
            "Cell count refines by 8x and discrete volume converges monotonically "
            "toward the analytic nominal geometry, but the coarse curved-boundary "
            "approximation has a material geometry error. The retained GCI includes "
            "both geometry and solution discretization; this is a plausible source "
            "of non-asymptotic behavior, not proof that the v2 GCI is invalid."
        ),
    }


def audit_validation(
    assessment: dict[str, Any],
    spatial: dict[str, Any],
    pressure: dict[str, Any],
) -> dict[str, Any]:
    comparisons = assessment["comparisons"]
    station_counts = comparisons["axialVelocityProfiles"]["stations"]
    below_historical_threshold = [
        {
            "stationM": float(station),
            "rawPassFraction": values["counts"]["vv20PassFraction"],
            "normalizedRmseByExperimentalPeak": values[
                "normalizedRmseByExperimentalPeak"
            ],
        }
        for station, values in sorted(station_counts.items(), key=lambda row: float(row[0]))
        if values["counts"]["vv20PassFraction"] < 0.90
    ]
    return {
        "retainedStatus": assessment["status"],
        "retainedPromotionAuthorized": assessment["promotionAuthorized"],
        "failedGates": sorted(
            name for name, passed in assessment["gates"].items() if not passed
        ),
        "axialVelocityProfiles": _counts(spatial["axialProfiles"]),
        "centrelineAxialVelocity": _counts(spatial["centreline"]),
        "wallAdjacentPressure": _counts(pressure["wall"]["adjacent"]),
        "mandatoryGci": assessment["mandatoryGci"],
        "stationsBelowHistoricalNinetyPercent": below_historical_threshold,
        "ninetyPercentInterpretation": (
            "FlowLab campaign policy retained as a historical diagnostic. It is "
            "not an FDA or ASME-prescribed universal acceptance figure and is not "
            "carried forward as a successor promotion gate without calibration."
        ),
        "fineSolver": assessment["solverDiagnostics"]["fine"],
        "iterativeHealth": assessment["iterativeHealth"],
    }


def build_successor_preflight(
    *,
    contract: dict[str, Any],
    experiment: dict[str, Any],
    assessment: dict[str, Any],
    spatial: dict[str, Any],
    pressure: dict[str, Any],
    meshes: dict[str, dict[str, Any]],
    evidence_hashes: dict[str, str],
) -> dict[str, Any]:
    reference = audit_experimental_reference(experiment, pressure)
    grid = audit_grid_family(meshes)
    validation = audit_validation(assessment, spatial, pressure)
    immutable_campaign_complete = (
        assessment.get("status") == "validated-blocked"
        and assessment.get("promotionAuthorized") is False
    )
    full_campaign_authorized = (
        immutable_campaign_complete
        and reference["pressureReferencePromotionReady"]
        and grid["successorGridFamilyPreflightComplete"]
    )
    return {
        "schema": SCHEMA,
        "contractId": contract["contractId"],
        "scope": contract["contextOfUse"],
        "sourceCampaign": contract["sourceCampaign"],
        "sourceCampaignImmutable": True,
        "evidenceSha256": dict(sorted(evidence_hashes.items())),
        "referenceAudit": reference,
        "gridFamilyAudit": grid,
        "retainedValidationAudit": validation,
        "hypotheses": [
            {
                "id": "H1-reference-pressure-limited",
                "likelihood": "high for the pressure gate; low explanatory power for velocity",
                "verifiable": True,
                "evidence": (
                    "Only three eligible pressure traces support sixteen adjacent "
                    "differences, so centred sample covariance rank is at most two; "
                    "tap bias and as-built metrology are absent."
                ),
                "nextDiscriminator": (
                    "Obtain laboratory mapping, calibration/bias records, tap covariance, "
                    "and as-built geometry; otherwise keep pressure nonpromotional."
                ),
            },
            {
                "id": "H2-grid-family-changes-geometry",
                "likelihood": "high as a contributor to unresolved GCI; moderate for validation discrepancies",
                "verifiable": True,
                "evidence": (
                    "checkMesh volume converges toward the analytic nominal domain, but "
                    "the coarse curved-boundary representation is materially low."
                ),
                "nextDiscriminator": (
                    "Predeclare a geometry-discretization tolerance, mesh a new three- "
                    "or four-grid family, and separate geometry from solution convergence "
                    "before experimental comparison."
                ),
            },
            {
                "id": "H3-model-boundary-or-as-built-mismatch",
                "likelihood": "moderate for downstream velocity and pressure shape",
                "verifiable": True,
                "evidence": (
                    "Downstream profile failures cluster while normalized profile RMSE remains "
                    "small; observation-window and outlet-length studies did not remove them."
                ),
                "nextDiscriminator": (
                    "After H1 and H2, predeclare independent-solver and as-built/inlet/pressure-"
                    "tap sensitivity studies in separate output directories."
                ),
            },
            {
                "id": "H4-fine-iterative-stall",
                "likelihood": "low as the primary scientific cause; high as a performance problem",
                "verifiable": True,
                "evidence": (
                    "The fine solve capped 62 of 100 pressure solves, but retained iterative "
                    "uncertainty is small relative to validation uncertainty."
                ),
                "nextDiscriminator": (
                    "Use a stationary cloned-fine diagnostic with predeclared QoI-drift and "
                    "linear-residual thresholds; do not rerun the campaign from time zero."
                ),
            },
        ],
        "gates": {
            "retainedCampaignCompletedFailClosed": immutable_campaign_complete,
            "referencePressurePromotionReady": reference[
                "pressureReferencePromotionReady"
            ],
            "successorGridFamilyPreflightComplete": grid[
                "successorGridFamilyPreflightComplete"
            ],
            "fullSuccessorCampaignAuthorized": full_campaign_authorized,
            "desktopPromotionAuthorized": False,
        },
        "status": (
            "prospective-full-campaign-authorized"
            if full_campaign_authorized
            else "blocked-before-new-cfd"
        ),
        "nextAuthorizedWork": contract["nextAuthorizedWork"],
        "promotionAuthorized": False,
    }


def run_audit(
    campaign: Path,
    fine_check_mesh: Path,
    contract_path: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing audit: {output}")
    paths = {
        "auditSource": Path(__file__).resolve(),
        "assessment": campaign / "assessment.json",
        "experiment": campaign / "experiment" / "experimental-data.json",
        "spatialValidation": campaign / "spatial-validation.json",
        "pressureValidation": campaign / "pressure-validation.json",
        "coarseCheckMesh": campaign / "logs" / "coarse" / "checkMesh.log",
        "mediumCheckMesh": campaign / "logs" / "medium" / "checkMesh.log",
        "fineCheckMesh": fine_check_mesh,
        "diagnosticContract": contract_path,
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required audit inputs are missing: {missing}")

    def load_json(name: str) -> dict[str, Any]:
        return json.loads(paths[name].read_text(encoding="utf-8"))

    meshes = {
        "coarse": parse_check_mesh(paths["coarseCheckMesh"].read_text(encoding="utf-8")),
        "medium": parse_check_mesh(paths["mediumCheckMesh"].read_text(encoding="utf-8")),
        "fine": parse_check_mesh(paths["fineCheckMesh"].read_text(encoding="utf-8")),
    }
    report = build_successor_preflight(
        contract=load_json("diagnosticContract"),
        experiment=load_json("experiment"),
        assessment=load_json("assessment"),
        spatial=load_json("spatialValidation"),
        pressure=load_json("pressureValidation"),
        meshes=meshes,
        evidence_hashes={name: sha256_path(path) for name, path in paths.items()},
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--fine-check-mesh", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(
        args.campaign.resolve(),
        args.fine_check_mesh.resolve(),
        args.contract.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["gates"]["fullSuccessorCampaignAuthorized"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
