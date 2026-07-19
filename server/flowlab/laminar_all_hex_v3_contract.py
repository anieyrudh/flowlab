"""Frozen campaign contract for ``laminar-all-hex-v3``.

V3 keeps every v2 scientific tolerance and changes only two predeclared
assessment mechanisms: physical solves stop after a sustained residual window
or at a hard cap, and MPI field equivalence is normalized by the analytic
field rather than by an already-small discretization error.
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


CAMPAIGN_ID = "laminar-all-hex-v3"
SCHEMA = "flowlab.laminar-all-hex-campaign.v2"
HARD_CAP_ITERATIONS = 2000
CHECK_INTERVAL_ITERATIONS = 100
MINIMUM_ITERATIONS = 300
SUSTAINED_WINDOW_ITERATIONS = 25
PRIMARY_QOI_RELATIVE_TOLERANCE = 1.0e-6
FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT = 1.0e-10
_ROOT = Path(__file__).resolve().parents[2]
_V3_SOURCE_PATHS = (
    "server/flowlab/laminar_all_hex_v3_contract.py",
    "server/flowlab/laminar_all_hex_v3_observer.py",
    "server/flowlab/laminar_all_hex_v3_worker.py",
    "server/flowlab/laminar_all_hex_v3_campaign_runner.py",
    "server/flowlab/laminar_all_hex_v3_confirmation.py",
    "server/flowlab/laminar_all_hex_v3_reproducibility.py",
    "server/flowlab/laminar_all_hex_v3_final_assessment.py",
)


def termination_contract() -> dict[str, Any]:
    return {
        "method": "host-supervised-staged-steady-solve",
        "minimumIterations": MINIMUM_ITERATIONS,
        "checkIntervalIterations": CHECK_INTERVAL_ITERATIONS,
        "sustainedWindowIterations": SUSTAINED_WINDOW_ITERATIONS,
        "hardCapIterations": HARD_CAP_ITERATIONS,
        "axialResidualLimit": gate_catalog()["physical"]["axialInitialResidual"],
        "pressureResidualLimit": gate_catalog()["physical"]["pressureInitialResidual"],
        "rule": (
            "Stop at the first checkpoint at or after the minimum for which the "
            "last sustained window passes both unchanged residual limits; reject "
            "if no such window exists by the hard cap."
        ),
    }


def mpi_equivalence_contract() -> dict[str, Any]:
    return {
        "primaryQoiRelativeTolerance": PRIMARY_QOI_RELATIVE_TOLERANCE,
        "fieldDifferenceNormalization": "analytic-field-L2-norm",
        "fieldDifferenceLimit": FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT,
        "derivedErrorNorms": "diagnostic-only-not-promotion-gates",
        "rankCounts": [2, 4],
    }


def _source_register(root: Path) -> dict[str, Any]:
    register = build_v2_manifest(root)["sourceRegister"]
    records = {row["path"]: row for row in register["records"]}
    for relative in _V3_SOURCE_PATHS:
        path = root / relative
        records[relative] = {
            "path": relative,
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return {
        **register,
        "records": [records[name] for name in sorted(records)],
    }


def build_manifest(root: Path = _ROOT) -> dict[str, Any]:
    manifest = json.loads(json.dumps(build_v2_manifest(root)))
    manifest["schema"] = SCHEMA
    manifest["campaignId"] = CAMPAIGN_ID
    manifest["scientificStatus"] = "v3-planned-fail-closed"
    manifest["campaignProfile"] = {
        "predecessor": "laminar-all-hex-v2",
        "v2VerdictPreserved": True,
        "scientificThresholdChanges": [],
        "termination": termination_contract(),
        "mpiEquivalence": mpi_equivalence_contract(),
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
        lane_counts[cell.get("lane", "missing")] = (
            lane_counts.get(cell.get("lane", "missing"), 0) + 1
        )
    cell_ids = [cell.get("cellId") for cell in cells]
    return {
        "schema": manifest.get("schema") == SCHEMA,
        "campaignId": manifest.get("campaignId") == CAMPAIGN_ID,
        "desktopOnly": manifest.get("mobile")
        == {"inScope": False, "changes": "none"},
        "v2VerdictPreserved": manifest.get("campaignProfile", {}).get(
            "v2VerdictPreserved"
        )
        is True,
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
        "mpiContractFrozen": manifest.get("campaignProfile", {}).get(
            "mpiEquivalence"
        )
        == mpi_equivalence_contract(),
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
