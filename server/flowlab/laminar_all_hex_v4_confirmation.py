"""Independent confirmation of the six historically sensitive cells under v4."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading

from .laminar_all_hex_campaign import (
    ResourceBudget,
    WeightedScheduler,
    _canonical_sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import (
    DockerScientificWorker,
    compile_traction_utility,
)
from .laminar_all_hex_confirmation import (
    archived_sources_match,
    compare_confirmation,
)
from .laminar_all_hex_v3_confirmation import is_v2_sensitive_cell
from .laminar_all_hex_v4_contract import CAMPAIGN_ID


SCHEMA = "flowlab.laminar-all-hex-v4-confirmation.v1"


def run_confirmation(campaign: Path, output: Path, capacity: int) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    if not archived_sources_match(campaign):
        raise ValueError("workspace source hashes no longer match the v4 campaign")
    manifest = json.loads(
        (campaign / "campaign-manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("campaignId") != CAMPAIGN_ID:
        raise ValueError("confirmation source is not laminar-all-hex-v4")
    selected = [cell for cell in manifest["cells"] if is_v2_sensitive_cell(cell)]
    if len(selected) != 6:
        raise ValueError("v4 confirmation must select exactly six sensitive cells")
    originals = {
        cell["cellId"]: json.loads(
            (campaign / "cells" / cell["cellId"] / "result.json").read_text(
                encoding="utf-8"
            )
        )
        for cell in selected
    }
    if not all(row["status"] == "accepted" for row in originals.values()):
        raise ValueError("all six source cells must be accepted before confirmation")
    cells = []
    source_by_confirmation = {}
    for source in selected:
        clone = json.loads(json.dumps(source))
        source_id = source["cellId"]
        clone["cellId"] = f"confirmation__{source_id}"
        clone["sourceCellId"] = source_id
        cells.append(clone)
        source_by_confirmation[clone["cellId"]] = source_id
    output.mkdir(parents=True, exist_ok=True)
    _write_json(
        output / "confirmation-manifest.json",
        {
            "schema": SCHEMA,
            "campaignId": CAMPAIGN_ID,
            "sourceCampaign": str(campaign),
            "sourceManifestSha256": _canonical_sha256(manifest),
            "method": "fresh independent cases with the identical frozen v4 contract",
            "cells": cells,
        },
    )
    utility = compile_traction_utility(output)
    budget = ResourceBudget.discover(capacity)
    results = WeightedScheduler(
        budget, output / "execution-events.jsonl"
    ).run(
        cells,
        DockerScientificWorker(
            output,
            threading.Lock(),
            worker_module="server.flowlab.laminar_all_hex_v4_worker",
        ),
    )
    comparisons = []
    for result in results:
        repeated = json.loads(
            (output / result["result"]).read_text(encoding="utf-8")
        )
        source_id = source_by_confirmation[result["cellId"]]
        comparisons.append(compare_confirmation(originals[source_id], repeated))
    checks = {
        "archivedSourcesMatchedBeforeExecution": True,
        "sixSensitiveCellsRepeated": len(comparisons) == 6,
        "allSourceCellsAccepted": all(
            row["status"] == "accepted" for row in originals.values()
        ),
        "allConfirmationCellsAccepted": len(results) == 6
        and all(result["status"] == "accepted" for result in results),
        "allNumericSignaturesConfirmed": len(comparisons) == 6
        and all(row["status"] == "confirmed" for row in comparisons),
        "noInfrastructureGaps": all(
            result["status"] != "incomplete-infrastructure" for result in results
        ),
    }
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": (
            "accepted-confirmation"
            if all(checks.values())
            else "rejected-confirmation"
        ),
        "checks": checks,
        "utility": utility,
        "comparisons": comparisons,
        "promotionAuthorized": False,
    }
    _write_json(output / "confirmation-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    report = run_confirmation(
        args.campaign.resolve(), args.output.resolve(), args.capacity
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted-confirmation" else 2


if __name__ == "__main__":
    raise SystemExit(main())
