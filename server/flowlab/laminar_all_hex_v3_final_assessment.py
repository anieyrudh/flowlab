"""Aggregate all v3 numerical, reproducibility, control, and empirical gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .laminar_all_hex_campaign import _sha256, _write_json
from .laminar_all_hex_final_assessment import physical_scope_summary
from .laminar_all_hex_v3_contract import CAMPAIGN_ID, termination_contract


SCHEMA = "flowlab.laminar-all-hex-final-assessment.v1"
_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference(path: Path) -> dict[str, str]:
    try:
        label = path.resolve().relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        label = str(path.resolve())
    return {"path": label, "sha256": _sha256(path)}


def convergence_summary(campaign: Path) -> dict[str, Any]:
    reports = [
        _load(path)
        for path in sorted((campaign / "cells").glob("physical*/result.json"))
    ]
    rows = [
        {
            "cellId": report["cellId"],
            "status": report["status"],
            "stopIteration": report["observation"]["iterations"],
            "firstSustainedJointPassIteration": report["observation"][
                "convergenceControl"
            ]["firstSustainedJointPassIteration"],
            "hardCapReached": report["observation"]["convergenceControl"][
                "hardCapReached"
            ],
        }
        for report in reports
    ]
    return {
        "contract": termination_contract(),
        "cellCount": len(rows),
        "allAchieved": len(rows) == 72
        and all(row["status"] == "accepted" for row in rows),
        "minimumStopIteration": min(
            (row["stopIteration"] for row in rows), default=None
        ),
        "maximumStopIteration": max(
            (row["stopIteration"] for row in rows), default=None
        ),
        "hardCapCellIds": [
            row["cellId"] for row in rows if row["hardCapReached"]
        ],
        "cells": rows,
    }


def build_assessment(campaign: Path, followups: Path, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    required = {
        "campaign": campaign / "campaign-report.json",
        "factorial": campaign / "factorial-analysis.json",
        "confirmation": followups / "confirmation-r1/confirmation-report.json",
        "reproducibility": followups
        / "reproducibility-r1/reproducibility-report.json",
        "controls": followups / "controls-r1/controls-report.json",
        "experimental": followups
        / "experimental-data-research-r1/experimental-dataset-assessment.json",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise ValueError(
            "v3 assessment is missing required evidence: " + ", ".join(missing)
        )
    reports = {name: _load(path) for name, path in required.items()}
    if reports["campaign"].get("campaignId") != CAMPAIGN_ID:
        raise ValueError("primary campaign is not laminar-all-hex-v3")
    convergence = convergence_summary(campaign)
    confirmation_accepted = (
        reports["confirmation"].get("status") == "accepted-confirmation"
        and reports["confirmation"].get("checks", {}).get(
            "sixSensitiveCellsRepeated"
        )
        is True
    )
    campaign_checks = reports["campaign"]["checks"]
    checks = {
        "everyScientificCellAccountedFor": campaign_checks[
            "everyScientificCellAccountedFor"
        ]
        is True,
        "noInfrastructureGaps": campaign_checks["noInfrastructureGaps"] is True,
        "affineAccepted": campaign_checks["affineAccepted"] is True,
        "nonAffineMmsAccepted": campaign_checks["nonAffineMmsAccepted"] is True,
        "physicalEnvelopeAccepted": campaign_checks["physicalEnvelopeAccepted"]
        is True
        and convergence["allAchieved"] is True
        and confirmation_accepted,
        "experimentalDatasetPinned": reports["experimental"].get(
            "experimentalDatasetPinned"
        )
        is True,
        "reproducibilityAccepted": reports["reproducibility"].get("status")
        == "accepted",
        "negativeControlsAccepted": reports["controls"].get("checks", {}).get(
            "sixNegativeControlsReject"
        )
        is True,
        "productContractAccepted": reports["controls"].get("checks", {}).get(
            "fourProductContractsPass"
        )
        is True,
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
            "status": reports["campaign"]["status"],
            "scientificCellCount": reports["campaign"]["scientificCellCount"],
            "accountedCellCount": reports["campaign"]["accountedCellCount"],
        },
        "physicalScope": physical_scope_summary(campaign),
        "convergenceControl": convergence,
        "sensitiveCellConfirmation": {
            "status": reports["confirmation"]["status"],
            "checks": reports["confirmation"]["checks"],
        },
        "reproducibility": {
            "status": reports["reproducibility"]["status"],
            "checks": reports["reproducibility"]["checks"],
            "equivalenceContract": reports["reproducibility"].get(
                "equivalenceContract"
            ),
        },
        "controls": {
            "status": reports["controls"]["status"],
            "checks": reports["controls"]["checks"],
        },
        "experimentalValidation": {
            "status": reports["experimental"]["status"],
            "bestCandidate": reports["experimental"].get("bestCandidate"),
            "nextAction": reports["experimental"].get("nextAction"),
        },
        "decision": {
            "desktopRegimeVisible": promotion,
            "runnableValidatedPresetAvailable": promotion,
            "completedV2VerdictChanged": False,
            "nextStage": (
                "desktop-promotion-and-platform-QA"
                if promotion
                else "resolve-only-the-listed-failed-final-gates"
            ),
        },
        "evidence": [_reference(path) for path in required.values()],
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
