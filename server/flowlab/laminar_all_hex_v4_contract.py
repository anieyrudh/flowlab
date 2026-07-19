"""Frozen common-floor campaign contract for ``laminar-all-hex-v4``.

V4 preserves every scientific tolerance from v3.  Its only numerical-method
change is a predeclared common 1300-iteration minimum for every physical cell,
followed by the same sustained residual rule and 2000-iteration hard cap.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .laminar_all_hex_campaign import (
    AXIAL_CELL_ASPECT_LEVELS,
    DIRECTION_LEVELS,
    IMAGE,
    IMAGE_DIGEST,
    INSTRUMENTED_LIBRARY_RELATIVE,
    INSTRUMENTED_LIBRARY_SHA256,
    LEVELS,
    LENGTH_RATIO_LEVELS,
    REYNOLDS_LEVELS,
    SCIENTIFIC_LANES,
    _sha256,
    build_manifest as build_v2_manifest,
    gate_catalog,
    physical_cells,
)
from .laminar_all_hex_v3_contract import mpi_equivalence_contract


CAMPAIGN_ID = "laminar-all-hex-v4"
SCHEMA = "flowlab.laminar-all-hex-campaign.v3"
HARD_CAP_ITERATIONS = 2000
CHECK_INTERVAL_ITERATIONS = 100
MINIMUM_ITERATIONS = 1300
SUSTAINED_WINDOW_ITERATIONS = 25
_ROOT = Path(__file__).resolve().parents[2]
_REMEDY_EVIDENCE = Path(
    "benchmarks/cases/open-boundary/campaigns/"
    "2026-07-16-laminar-all-hex-v3-followups/"
    "common-floor-1300-repair-r1/repair-report.json"
)
_V4_SOURCE_PATHS = (
    "server/flowlab/laminar_all_hex_v3_observer.py",
    "server/flowlab/laminar_all_hex_v4_contract.py",
    "server/flowlab/laminar_all_hex_v4_observer.py",
    "server/flowlab/laminar_all_hex_v4_worker.py",
    "server/flowlab/laminar_all_hex_v4_campaign_runner.py",
)


def termination_contract() -> dict[str, Any]:
    return {
        "method": "host-supervised-common-floor-staged-steady-solve",
        "minimumIterations": MINIMUM_ITERATIONS,
        "commonMinimumAcrossPhysicalMatrix": True,
        "minimumAppliesTo": "stop-iteration",
        "checkIntervalIterations": CHECK_INTERVAL_ITERATIONS,
        "sustainedWindowIterations": SUSTAINED_WINDOW_ITERATIONS,
        "hardCapIterations": HARD_CAP_ITERATIONS,
        "axialResidualLimit": gate_catalog()["physical"]["axialInitialResidual"],
        "pressureResidualLimit": gate_catalog()["physical"]["pressureInitialResidual"],
        "rule": (
            "Do not stop any physical cell before iteration 1300. At each "
            "checkpoint from 1300 onward, stop at the first checkpoint whose "
            "last 25 iterations pass both unchanged residual limits; reject "
            "if no such window exists by iteration 2000."
        ),
    }


def remedy_evidence(root: Path = _ROOT) -> dict[str, Any]:
    path = root / _REMEDY_EVIDENCE
    report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {
        "path": _REMEDY_EVIDENCE.as_posix(),
        "exists": path.is_file(),
        "sha256": _sha256(path) if path.is_file() else None,
        "status": report.get("status"),
        "checks": report.get("checks"),
        "interpretationBoundary": report.get("interpretationBoundary"),
    }


def _source_register(root: Path) -> dict[str, Any]:
    register = build_v2_manifest(root)["sourceRegister"]
    records = {row["path"]: row for row in register["records"]}
    for relative in _V4_SOURCE_PATHS:
        path = root / relative
        records[relative] = {
            "path": relative,
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return {**register, "records": [records[name] for name in sorted(records)]}


def build_manifest(root: Path = _ROOT) -> dict[str, Any]:
    manifest = json.loads(json.dumps(build_v2_manifest(root)))
    manifest["schema"] = SCHEMA
    manifest["campaignId"] = CAMPAIGN_ID
    manifest["scientificStatus"] = "v4-planned-fail-closed"
    manifest["campaignProfile"] = {
        "predecessor": "laminar-all-hex-v3",
        "v2VerdictPreserved": True,
        "v3VerdictPreserved": True,
        "scientificThresholdChanges": [],
        "termination": termination_contract(),
        "mpiEquivalence": mpi_equivalence_contract(),
        "remedyEvidence": remedy_evidence(root),
    }
    for cell in manifest["cells"]:
        cell["campaignId"] = CAMPAIGN_ID
        if cell["lane"] == "physical-envelope":
            cell["parameters"]["convergenceControl"] = termination_contract()
    manifest["sourceRegister"] = _source_register(root)
    return manifest


def validate_manifest(
    manifest: dict[str, Any], root: Path = _ROOT
) -> dict[str, bool]:
    cells = manifest.get("cells", [])
    physical = [cell for cell in cells if cell.get("lane") == "physical-envelope"]
    expected_physical_ids = {cell["cellId"] for cell in physical_cells()}
    actual_register = _source_register(root)
    recorded = {
        row["path"]: row
        for row in manifest.get("sourceRegister", {}).get("records", [])
    }
    source_hashes_match = all(
        recorded.get(row["path"], {}).get("exists") is True
        and recorded[row["path"]].get("sha256") == row["sha256"]
        for row in actual_register["records"]
    )
    lane_counts: dict[str, int] = {}
    for cell in cells:
        lane = cell.get("lane", "missing")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    cell_ids = [cell.get("cellId") for cell in cells]
    evidence = manifest.get("campaignProfile", {}).get("remedyEvidence", {})
    evidence_path = root / str(evidence.get("path", "missing"))
    return {
        "schema": manifest.get("schema") == SCHEMA,
        "campaignId": manifest.get("campaignId") == CAMPAIGN_ID,
        "desktopOnly": manifest.get("mobile")
        == {"inScope": False, "changes": "none"},
        "priorVerdictsPreserved": manifest.get("campaignProfile", {}).get(
            "v2VerdictPreserved"
        )
        is True
        and manifest.get("campaignProfile", {}).get("v3VerdictPreserved") is True,
        "scientificThresholdsUnchanged": manifest.get("gateCatalog")
        == gate_catalog()
        and manifest.get("campaignProfile", {}).get(
            "scientificThresholdChanges"
        )
        == [],
        "terminationContractFrozen": manifest.get("campaignProfile", {}).get(
            "termination"
        )
        == termination_contract(),
        "common1300FloorFrozen": termination_contract()["minimumIterations"]
        == 1300
        and termination_contract()["commonMinimumAcrossPhysicalMatrix"] is True,
        "mpiContractFrozen": manifest.get("campaignProfile", {}).get(
            "mpiEquivalence"
        )
        == mpi_equivalence_contract(),
        "remedyEvidenceAccepted": evidence.get("exists") is True
        and evidence.get("status") == "supports-common-floor"
        and evidence.get("checks", {}).get(
            "allFourGroupsPassExistingGciGates"
        )
        is True
        and evidence_path.is_file()
        and evidence.get("sha256") == _sha256(evidence_path),
        "pinnedImage": manifest.get("solver", {}).get("image") == IMAGE,
        "pinnedImageDigest": manifest.get("solver", {}).get("digest")
        == IMAGE_DIGEST,
        "pinnedInstrumentedLibrary": manifest.get("solver", {}).get(
            "instrumentedLibrary"
        )
        == {
            "path": INSTRUMENTED_LIBRARY_RELATIVE,
            "sha256": INSTRUMENTED_LIBRARY_SHA256,
            "scope": "affine lane only",
        },
        "uniqueCellIds": len(cell_ids) == len(set(cell_ids))
        and None not in cell_ids,
        "affineThreeGrid": lane_counts.get("affine") == 3,
        "nonAffineThreeGrid": lane_counts.get("non-affine-mms") == 3,
        "physicalFullFactorial72": len(physical) == 72
        and {cell["cellId"] for cell in physical} == expected_physical_ids,
        "primaryScientificCount78": sum(
            cell.get("lane") in SCIENTIFIC_LANES for cell in cells
        )
        == 78,
        "physicalEnvelopeUnchanged": manifest.get("physicalEnvelope")
        == {
            "reynoldsNumberHeightBased": list(REYNOLDS_LEVELS),
            "flowDirection": [item[0] for item in DIRECTION_LEVELS],
            "lengthToHeightRatio": list(LENGTH_RATIO_LEVELS),
            "axialCellAspectRatio": list(AXIAL_CELL_ASPECT_LEVELS),
            "cellsPerHeight": [item[1] for item in LEVELS],
        },
        "everyPhysicalCellUsesTerminationContract": len(physical) == 72
        and all(
            cell.get("campaignId") == CAMPAIGN_ID
            and cell.get("parameters", {}).get("convergenceControl")
            == termination_contract()
            for cell in physical
        ),
        "sourceHashesMatch": source_hashes_match,
    }
