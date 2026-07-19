"""Post-campaign factorial and conflict analysis for laminar-all-hex-v2."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

from .laminar_all_hex_campaign import CAMPAIGN_ID, _write_json


SCHEMA = "flowlab.laminar-all-hex-factorial-analysis.v1"
FACTORS = (
    "reynoldsNumberHeightBased",
    "flowDirection",
    "lengthToHeightRatio",
    "axialCellAspectRatio",
    "level",
)
RESPONSES = (
    "finalAxialInitialResidual",
    "finalPressureInitialResidual",
    "velocityRelativeL2Error",
    "pressureRelativeL2Error",
    "wallViscousForceRelativeError",
    "faceViscousTractionRelativeError",
)


def _load_reports(campaign: Path) -> list[dict[str, Any]]:
    reports = []
    for path in sorted((campaign / "cells").glob("physical*/result.json")):
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    if len(reports) != 72:
        raise ValueError(f"expected 72 physical reports, found {len(reports)}")
    return reports


def response_row(report: dict[str, Any]) -> dict[str, Any]:
    observation = report["observation"]
    force = observation["forceComparison"]
    face = observation["faceComparison"]
    return {
        **report["parameters"],
        "cellId": report["cellId"],
        "status": report["status"],
        "failedChecks": report["failedChecks"],
        "finalAxialInitialResidual": observation["finalAxialInitialResidual"],
        "finalPressureInitialResidual": observation["finalPressureInitialResidual"],
        "velocityRelativeL2Error": observation["velocityRelativeL2Error"],
        "pressureRelativeL2Error": observation["pressureRelativeL2Error"],
        "wallViscousForceRelativeError": force["wallViscousForceRelativeError"],
        "faceViscousTractionRelativeError": face["maxViscousTractionRelativeError"],
    }


def _log_response(row: dict[str, Any], response: str) -> float:
    value = float(row[response])
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{response} must be finite and positive: {value}")
    return math.log10(value)


def _factor_levels(rows: Iterable[dict[str, Any]], factor: str) -> list[Any]:
    return sorted({row[factor] for row in rows}, key=str)


def main_effects(rows: list[dict[str, Any]], response: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for factor in FACTORS:
        means = {
            str(level): fmean(
                _log_response(row, response)
                for row in rows
                if row[factor] == level
            )
            for level in _factor_levels(rows, factor)
        }
        result[factor] = {
            "log10MeanByLevel": means,
            "effectRangeDecades": max(means.values()) - min(means.values()),
        }
    return result


def two_factor_interactions(
    rows: list[dict[str, Any]], response: str
) -> list[dict[str, Any]]:
    grand = fmean(_log_response(row, response) for row in rows)
    effects = main_effects(rows, response)
    interactions = []
    for left_index, left in enumerate(FACTORS):
        for right in FACTORS[left_index + 1 :]:
            residuals = []
            cells = []
            for left_level in _factor_levels(rows, left):
                for right_level in _factor_levels(rows, right):
                    selected = [
                        row
                        for row in rows
                        if row[left] == left_level and row[right] == right_level
                    ]
                    combo = fmean(_log_response(row, response) for row in selected)
                    additive = (
                        effects[left]["log10MeanByLevel"][str(left_level)]
                        + effects[right]["log10MeanByLevel"][str(right_level)]
                        - grand
                    )
                    residual = combo - additive
                    residuals.append(abs(residual))
                    cells.append(
                        {
                            "leftLevel": left_level,
                            "rightLevel": right_level,
                            "meanLog10Response": combo,
                            "additiveResidualDecades": residual,
                        }
                    )
            interactions.append(
                {
                    "left": left,
                    "right": right,
                    "maximumAbsoluteAdditiveResidualDecades": max(residuals),
                    "cells": cells,
                }
            )
    return sorted(
        interactions,
        key=lambda row: row["maximumAbsoluteAdditiveResidualDecades"],
        reverse=True,
    )


def failure_rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for factor in FACTORS:
        levels = {}
        for level in _factor_levels(rows, factor):
            selected = [row for row in rows if row[factor] == level]
            failed = sum(row["status"] != "accepted" for row in selected)
            levels[str(level)] = {
                "failed": failed,
                "total": len(selected),
                "rate": failed / len(selected),
            }
        result[factor] = levels
    return result


def direction_symmetry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    paired: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (
            row["reynoldsNumberHeightBased"],
            row["lengthToHeightRatio"],
            row["axialCellAspectRatio"],
            row["level"],
        )
        paired[key][row["flowDirection"]] = row
    metrics = {}
    for response in RESPONSES:
        maximum = 0.0
        maximum_pair = None
        for key, pair in paired.items():
            if set(pair) != {"forward", "reverse"}:
                raise ValueError(f"missing direction pair for {key}")
            forward = float(pair["forward"][response])
            reverse = float(pair["reverse"][response])
            relative = abs(forward - reverse) / max(abs(forward), abs(reverse), 1e-300)
            if relative > maximum:
                maximum = relative
                maximum_pair = key
        metrics[response] = {
            "maximumRelativeDifference": maximum,
            "parameterKey": list(maximum_pair) if maximum_pair else None,
        }
    return {"pairCount": len(paired), "metrics": metrics}


def nested_claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = [
        ("re-at-most-4.17-full-factorial", lambda row: row["reynoldsNumberHeightBased"] <= 4.17),
        ("re-at-most-16.67-full-factorial", lambda row: row["reynoldsNumberHeightBased"] <= 16.67),
        ("re-66.7-long-channel", lambda row: row["reynoldsNumberHeightBased"] == 66.7 and row["lengthToHeightRatio"] == 4.0),
        ("full-predeclared-envelope", lambda row: True),
    ]
    result = []
    for name, predicate in definitions:
        selected = [row for row in rows if predicate(row)]
        failed = [row["cellId"] for row in selected if row["status"] != "accepted"]
        result.append(
            {
                "claim": name,
                "status": "accepted" if not failed else "rejected",
                "cellCount": len(selected),
                "failedCellIds": failed,
            }
        )
    return result


def build_analysis(campaign: Path) -> dict[str, Any]:
    reports = _load_reports(campaign)
    campaign_id = reports[0].get("campaignId", CAMPAIGN_ID)
    rows = [response_row(report) for report in reports]
    failed = [row for row in rows if row["status"] != "accepted"]
    return {
        "schema": SCHEMA,
        "campaignId": campaign_id,
        "sourceCampaign": str(campaign),
        "status": "recorded-fail-closed",
        "cellCount": len(rows),
        "acceptedCellCount": len(rows) - len(failed),
        "rejectedCellCount": len(failed),
        "failedCells": failed,
        "failureRates": failure_rates(rows),
        "nestedClaims": nested_claims(rows),
        "directionSymmetry": direction_symmetry(rows),
        "responses": {
            response: {
                "mainEffects": main_effects(rows, response),
                "twoFactorInteractions": two_factor_interactions(rows, response),
            }
            for response in RESPONSES
        },
        "interpretationBoundary": (
            "Balanced marginal means and additive residuals are screening evidence, "
            "not a causal model. Resolution diagnostics must be confirmed independently."
        ),
        "promotionAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_analysis(args.campaign.resolve())
    _write_json(args.output.resolve(), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
