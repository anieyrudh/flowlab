"""Consolidate immutable campaign and follow-up evidence into one promotion gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .laminar_all_hex_campaign import CAMPAIGN_ID, _sha256, _write_json


SCHEMA = "flowlab.laminar-all-hex-final-assessment.v1"
_FORCE_AND_FIELD_CHECKS = {
    "checkMesh",
    "solverCompleted",
    "directAuditCompleted",
    "massBalance",
    "finalLinearResidual",
    "transverseVelocity",
    "openFoamVsDirect",
    "analyticPressureForce",
    "wallViscousForceCoarseThroughFine",
    "fineWallViscousForce",
    "fineFaceViscousTraction",
    "fineFields",
    "fineMomentumBalance",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference(root: Path, path: Path) -> dict[str, str]:
    try:
        label = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        label = str(path.resolve())
    return {"path": label, "sha256": _sha256(path)}


def physical_scope_summary(campaign: Path) -> dict[str, Any]:
    reports = [
        _load(path)
        for path in sorted((campaign / "cells").glob("physical*/result.json"))
    ]

    def subset(name: str, predicate: Any) -> dict[str, Any]:
        rows = [row for row in reports if predicate(row["parameters"])]
        accepted = sum(row["status"] == "accepted" for row in rows)
        return {
            "name": name,
            "cellCount": len(rows),
            "acceptedCellCount": accepted,
            "allAccepted": bool(rows) and accepted == len(rows),
        }

    scientific_failures = [
        {
            "cellId": row["cellId"],
            "failedChecks": row["failedChecks"],
            "parameters": row["parameters"],
        }
        for row in reports
        if row["status"] != "accepted"
    ]
    force_field_failures = []
    for row in reports:
        failed = sorted(
            name
            for name in _FORCE_AND_FIELD_CHECKS
            if name in row["checks"] and row["checks"][name] is not True
        )
        if failed:
            force_field_failures.append({"cellId": row["cellId"], "failedChecks": failed})
    return {
        "cellCount": len(reports),
        "acceptedCellCount": sum(row["status"] == "accepted" for row in reports),
        "scientificFailures": scientific_failures,
        "allForceFieldMeshMassAndLinearChecksPass": not force_field_failures,
        "forceFieldFailures": force_field_failures,
        "nestedNumericalScopes": [
            subset("Re=4.17 full factorial", lambda p: p["reynoldsNumberHeightBased"] == 4.17),
            subset("Re=16.67 full factorial", lambda p: p["reynoldsNumberHeightBased"] == 16.67),
            subset("Re<=16.67 full factorial", lambda p: p["reynoldsNumberHeightBased"] <= 16.67),
            subset("Re=66.7 and L/H=4", lambda p: p["reynoldsNumberHeightBased"] == 66.7 and p["lengthToHeightRatio"] == 4.0),
            subset("Re=66.7 and L/H=1", lambda p: p["reynoldsNumberHeightBased"] == 66.7 and p["lengthToHeightRatio"] == 1.0),
        ],
    }


def build_assessment(campaign: Path, followups: Path, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    campaign_report_path = campaign / "campaign-report.json"
    factorial_path = campaign / "factorial-analysis.json"
    confirmation_path = followups / "confirmation-r1/confirmation-report.json"
    diagnostic_path = followups / "iteration-1250-r1/diagnostic-report.json"
    reproducibility_path = followups / "reproducibility-r1/reproducibility-report.json"
    controls_path = followups / "controls-r1/controls-report.json"
    experimental_path = followups / "experimental-data-research-r1/experimental-dataset-assessment.json"
    required = (
        campaign_report_path,
        factorial_path,
        confirmation_path,
        diagnostic_path,
        reproducibility_path,
        controls_path,
        experimental_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("final assessment is missing required evidence: " + ", ".join(missing))
    campaign_report = _load(campaign_report_path)
    confirmation = _load(confirmation_path)
    diagnostic = _load(diagnostic_path)
    reproducibility = _load(reproducibility_path)
    controls = _load(controls_path)
    experimental = _load(experimental_path)
    scope = physical_scope_summary(campaign)
    checks = {
        "everyScientificCellAccountedFor": campaign_report["checks"]["everyScientificCellAccountedFor"] is True,
        "noInfrastructureGaps": campaign_report["checks"]["noInfrastructureGaps"] is True,
        "affineAccepted": campaign_report["checks"]["affineAccepted"] is True,
        "nonAffineMmsAccepted": campaign_report["checks"]["nonAffineMmsAccepted"] is True,
        "physicalEnvelopeAccepted": campaign_report["checks"]["physicalEnvelopeAccepted"] is True,
        "experimentalDatasetPinned": experimental["experimentalDatasetPinned"] is True,
        "reproducibilityAccepted": reproducibility["status"] == "accepted",
        "negativeControlsAccepted": controls["checks"]["sixNegativeControlsReject"] is True,
        "productContractAccepted": controls["checks"]["fourProductContractsPass"] is True,
    }
    promotion = all(checks.values())
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "accepted" if promotion else "rejected-promotion-blocked",
        "checks": checks,
        "promotionAuthorized": promotion,
        "primaryCampaign": {
            "status": campaign_report["status"],
            "scientificCellCount": campaign_report["scientificCellCount"],
            "accountedCellCount": campaign_report["accountedCellCount"],
        },
        "physicalScope": scope,
        "failureConfirmation": {
            "status": confirmation["status"],
            "allFailureSignaturesConfirmed": confirmation["checks"]["allFailureSignaturesConfirmed"],
        },
        "iterationDiagnostic": {
            "status": diagnostic["status"],
            "oneChangedFactor": diagnostic["oneChangedFactor"],
            "allFrozenGatesNowPass": diagnostic["checks"]["allFrozenGatesNowPass"],
            "maximumObservedCrossingIteration": max(
                max(
                    row["residualHistory"]["firstSustainedAxialPassIteration"],
                    row["residualHistory"]["firstSustainedPressurePassIteration"],
                )
                for row in diagnostic["observations"]
            ),
            "interpretation": diagnostic["interpretation"],
        },
        "reproducibility": {
            "status": reproducibility["status"],
            "checks": reproducibility["checks"],
        },
        "controls": {"status": controls["status"], "checks": controls["checks"]},
        "experimentalValidation": {
            "status": experimental["status"],
            "bestCandidate": experimental["bestCandidate"],
            "nextAction": experimental["nextAction"],
        },
        "decision": {
            "desktopRegimeVisible": promotion,
            "runnableValidatedPresetAvailable": promotion,
            "completedV2VerdictChangedByDiagnostics": False,
            "nextCampaign": (
                "Create laminar-all-hex-v3 with a convergence-based stopping rule or a predeclared iteration budget of at least 1250, rerun the full matrix, and keep experimental validation independent."
            ),
        },
        "evidence": [
            _reference(campaign.parent.parent.parent.parent.parent, path)
            for path in required
        ],
    }
    _write_json(output / "final-assessment.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--followups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_assessment(
        args.campaign.resolve(), args.followups.resolve(), args.output.resolve()
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["promotionAuthorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
