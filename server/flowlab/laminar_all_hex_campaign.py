"""Fail-closed control plane for the parallel all-hex laminar V&V campaign.

The module freezes the scientific contract before any new solve, materializes
the full validation matrix, records source fingerprints and known blockers,
and provides a resource-weighted scheduler.  A dry run validates this control
plane without invoking OpenFOAM.  Scientific execution is handled by the
companion ``laminar_all_hex_campaign_worker`` module.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Callable, Iterable

from .open_boundary_affine_grid_invariance import (
    LINEAR_SOLVER_TOLERANCE as AFFINE_LINEAR_SOLVER_TOLERANCE,
)
from .open_boundary_laminar_force_benchmark import (
    AXIAL_NONLINEAR_RESIDUAL_LIMIT,
    COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
    FINE_FACE_TRACTION_RELATIVE_LIMIT,
    FINE_FIELD_RELATIVE_LIMIT,
    FINE_WALL_VISCOUS_RELATIVE_LIMIT,
    FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
    LINEAR_RESIDUAL_LIMIT as PHYSICAL_LINEAR_RESIDUAL_LIMIT,
    MASS_LIMIT as PHYSICAL_MASS_LIMIT,
    PRESSURE_FORCE_RELATIVE_LIMIT,
    PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
    TRANSVERSE_VELOCITY_RELATIVE_LIMIT,
)
from .open_boundary_non_affine_mms import (
    FINE_GCI_LIMIT,
    LINEAR_RESIDUAL_LIMIT as MMS_LINEAR_RESIDUAL_LIMIT,
    MASS_LIMIT as MMS_MASS_LIMIT,
    MAXIMUM_ORDER_SPREAD,
    MINIMUM_OBSERVED_ORDER,
    NONLINEAR_RESIDUAL_LIMIT,
    SAFETY_FACTOR,
)


SCHEMA = "flowlab.laminar-all-hex-campaign.v1"
CELL_SCHEMA = "flowlab.laminar-all-hex-campaign-cell.v1"
ISSUE_SCHEMA = "flowlab.laminar-all-hex-issue.v1"
DRY_RUN_SCHEMA = "flowlab.laminar-all-hex-dry-run.v1"
CAMPAIGN_ID = "laminar-all-hex-v2"
IMAGE = "flowlab/openfoam11-gmsh:2026-07-13"
IMAGE_DIGEST = "sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe"
INSTRUMENTED_LIBRARY_RELATIVE = (
    "benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v10/"
    "trace-outlet-constrain/lib/libincompressibleFluid.so"
)
INSTRUMENTED_LIBRARY_SHA256 = (
    "157ac7086c82dcf5bc7fd1fde1a3ff83e8cc3710ae9b83d9d085711a97f076de"
)
LEVELS = (("coarse", 12, 1), ("medium", 24, 2), ("fine", 48, 4))
REYNOLDS_LEVELS = (4.17, 16.67, 66.7)
DIRECTION_LEVELS = (("forward", 1), ("reverse", -1))
LENGTH_RATIO_LEVELS = (1.0, 4.0)
AXIAL_CELL_ASPECT_LEVELS = (1.0, 2.0)
ISSUE_KINDS = (
    "error",
    "conflict",
    "interference",
    "infrastructure",
    "provenance",
    "known-limitation",
)
ISSUE_STATUSES = ("open", "investigating", "resolved", "accepted-limitation")
SCIENTIFIC_LANES = ("affine", "non-affine-mms", "physical-envelope")


_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_PATHS = (
    "server/flowlab/laminar_all_hex_campaign.py",
    "server/flowlab/laminar_all_hex_campaign_worker.py",
    "server/flowlab/laminar_all_hex_campaign_runner.py",
    "server/flowlab/open_boundary_affine_grid_invariance.py",
    "server/flowlab/open_boundary_non_affine_mms.py",
    "server/flowlab/open_boundary_laminar_force_benchmark.py",
    "server/flowlab/open_boundary_factorial_matrix.py",
    "server/flowlab/validated_benchmark.py",
    "benchmarks/tools/flowlabPatchTractionAudit/flowlabPatchTractionAudit.C",
    "benchmarks/laminar_all_hex_campaign.schema.json",
    "benchmarks/laminar_all_hex_issue.schema.json",
    INSTRUMENTED_LIBRARY_RELATIVE,
)
_PINNED_UPSTREAM = (
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v29-affine-grid-invariance/artifacts/affine-grid-invariance.json",
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v36-non-affine-mms/artifacts/non-affine-mms-report.json",
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v40-laminar-force-benchmark/artifacts/laminar-force-benchmark.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats with JSON null at the persistence boundary.

    Failed scientific observations may legitimately contain infinity as an
    internal sentinel.  Reports must remain strict JSON so those failures can
    be retained and classified instead of being misreported as infrastructure
    gaps.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, value: Any, lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
    if lock is None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        return
    with lock:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)


def _slug_number(value: float) -> str:
    return f"{value:g}".replace("-", "m").replace(".", "p")


def _physical_cell_id(
    reynolds: float,
    direction: str,
    length_ratio: float,
    axial_cell_aspect: float,
    level: str,
) -> str:
    return (
        f"physical__re-{_slug_number(reynolds)}__dir-{direction}"
        f"__lh-{_slug_number(length_ratio)}"
        f"__ax-{_slug_number(axial_cell_aspect)}__{level}"
    )


def _cell(
    cell_id: str,
    lane: str,
    parameters: dict[str, Any],
    *,
    weight: int,
    resource_class: str,
    dependencies: Iterable[str] = (),
    execution_kind: str = "scientific",
) -> dict[str, Any]:
    return {
        "schema": CELL_SCHEMA,
        "cellId": cell_id,
        "lane": lane,
        "executionKind": execution_kind,
        "parameters": parameters,
        "resourceWeight": weight,
        "resourceClass": resource_class,
        "acceptanceDependencies": list(dependencies),
        "plannedStatus": "provisional-until-dependencies-pass",
    }


def physical_cells() -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for reynolds, (direction, sign), length_ratio, axial_aspect, (
        level,
        n,
        weight,
    ) in product(
        REYNOLDS_LEVELS,
        DIRECTION_LEVELS,
        LENGTH_RATIO_LEVELS,
        AXIAL_CELL_ASPECT_LEVELS,
        LEVELS,
    ):
        cells.append(
            _cell(
                _physical_cell_id(
                    reynolds, direction, length_ratio, axial_aspect, level
                ),
                "physical-envelope",
                {
                    "reynoldsNumberHeightBased": reynolds,
                    "flowDirection": direction,
                    "flowDirectionSign": sign,
                    "lengthToHeightRatio": length_ratio,
                    "axialCellAspectRatio": axial_aspect,
                    "level": level,
                    "cellsPerHeight": n,
                },
                weight=weight,
                resource_class=level,
                dependencies=("affine", "non-affine-mms"),
            )
        )
    return cells


def campaign_cells() -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for lane in ("affine", "non-affine-mms"):
        for level, n, weight in LEVELS:
            dependencies: tuple[str, ...] = () if lane == "affine" else ("affine",)
            cells.append(
                _cell(
                    f"{lane}__{level}",
                    lane,
                    {"level": level, "cellsPerAxis": n},
                    weight=weight,
                    resource_class=level,
                    dependencies=dependencies,
                )
            )
    cells.extend(physical_cells())
    for repeat in ("a", "b"):
        cells.append(
            _cell(
                f"reproducibility__cold-repeat-{repeat}",
                "reproducibility",
                {
                    "sourceCellId": "physical__re-16p67__dir-forward__lh-1__ax-1__fine",
                    "mode": "cold-repeat",
                },
                weight=4,
                resource_class="fine",
                dependencies=("physical-envelope",),
            )
        )
    for ranks in (2, 4):
        cells.append(
            _cell(
                f"reproducibility__mpi-{ranks}",
                "reproducibility",
                {
                    "sourceCellId": "physical__re-16p67__dir-forward__lh-1__ax-1__fine",
                    "mode": "mpi-decomposition",
                    "ranks": ranks,
                },
                weight=4,
                resource_class="fine",
                dependencies=("physical-envelope",),
            )
        )
    for control in (
        "mutated-boundary-condition",
        "mutated-gate-threshold",
        "corrupt-source-hash",
        "missing-artifact",
        "invalid-mesh-topology",
        "wrong-image-digest",
    ):
        cells.append(
            _cell(
                f"negative-control__{control}",
                "negative-controls",
                {"control": control, "expectedOutcome": "rejected"},
                weight=1,
                resource_class="control",
                execution_kind="control",
            )
        )
    for contract in (
        "incomplete-evidence",
        "out-of-envelope-parameters",
        "mutated-preset-file",
        "accepted-bounded-envelope",
    ):
        cells.append(
            _cell(
                f"product-contract__{contract}",
                "product-contract",
                {"contract": contract},
                weight=1,
                resource_class="control",
                dependencies=("campaign-aggregation",),
                execution_kind="control",
            )
        )
    return cells


def gate_catalog() -> dict[str, Any]:
    return {
        "schema": "flowlab.laminar-all-hex-gate-catalog.v1",
        "policy": {
            "thresholdMutationAllowedDuringCampaign": False,
            "missingEvidenceOutcome": "incomplete-infrastructure",
            "scientificFailureRetry": "independent-confirmation-only",
            "promotion": "fail-closed",
        },
        "affine": {
            "linearSolverTolerance": AFFINE_LINEAR_SOLVER_TOLERANCE,
            "required": [
                "authorizedCoarseStabilityGate",
                "resolutionSequence12_24_48",
                "everyLevelPassesEveryExistingGate",
                "allGridMetricsFinite",
            ],
        },
        "nonAffineMms": {
            "massRelativeImbalance": MMS_MASS_LIMIT,
            "finalLinearResidual": MMS_LINEAR_RESIDUAL_LIMIT,
            "finalNonlinearResidual": NONLINEAR_RESIDUAL_LIMIT,
            "minimumObservedOrder": MINIMUM_OBSERVED_ORDER,
            "maximumOrderSpread": MAXIMUM_ORDER_SPREAD,
            "fineGciRelativeToAnalyticFieldNorm": FINE_GCI_LIMIT,
            "gciSafetyFactor": SAFETY_FACTOR,
        },
        "physical": {
            "massRelativeImbalance": PHYSICAL_MASS_LIMIT,
            "finalLinearResidual": PHYSICAL_LINEAR_RESIDUAL_LIMIT,
            "axialInitialResidual": AXIAL_NONLINEAR_RESIDUAL_LIMIT,
            "pressureInitialResidual": PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
            "transverseVelocityRelativeL2": TRANSVERSE_VELOCITY_RELATIVE_LIMIT,
            "forceObjectVsDirectAbsolute": FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
            "analyticPressureForceRelative": PRESSURE_FORCE_RELATIVE_LIMIT,
            "coarseWallViscousForceRelative": COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
            "fineWallViscousForceRelative": FINE_WALL_VISCOUS_RELATIVE_LIMIT,
            "fineFaceViscousTractionRelative": FINE_FACE_TRACTION_RELATIVE_LIMIT,
            "fineFieldRelative": FINE_FIELD_RELATIVE_LIMIT,
        },
        "crossLane": {
            "everyPlannedCellAccountedFor": True,
            "sourceHashesMatch": True,
            "noUnresolvedClaimBlockingConflicts": True,
            "independentConfirmationRequiredForUnexpectedFailure": True,
            "experimentalValidationRequiredForEmpiricalClaim": True,
        },
    }


def source_register(root: Path = _ROOT) -> dict[str, Any]:
    records = []
    for relative in _SOURCE_PATHS + _PINNED_UPSTREAM:
        path = root / relative
        records.append(
            {
                "path": relative,
                "exists": path.is_file(),
                "sha256": _sha256(path) if path.is_file() else None,
            }
        )
    return {
        "schema": "flowlab.laminar-all-hex-source-register.v1",
        "records": records,
        "methodology": [
            {
                "title": "ASME V&V 20",
                "url": "https://www.asme.org/codes-standards/find-codes-standards/standard-for-verification-and-validation-in-computational-fluid-dynamics-and-heat-transfer",
                "role": "validation comparison and uncertainty framing",
            },
            {
                "title": "NASA CFD Verification Assessment",
                "url": "https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html",
                "role": "observed-order and GCI reporting",
            },
        ],
    }


def issue_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://flowlab.local/schemas/laminar-all-hex-issue.v1.json",
        "title": "FlowLab laminar all-hex campaign issue",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "issueId",
            "kind",
            "severity",
            "status",
            "summary",
            "affectedClaims",
            "affectedGates",
            "evidence",
            "suspectedCauses",
            "falsifiedCauses",
            "conflictsWith",
            "interactingFactors",
            "nextDiagnostic",
        ],
        "properties": {
            "schema": {"const": ISSUE_SCHEMA},
            "issueId": {"type": "string", "minLength": 1},
            "recordedAt": {"type": "string"},
            "campaignId": {"const": CAMPAIGN_ID},
            "lane": {"type": ["string", "null"]},
            "cellId": {"type": ["string", "null"]},
            "kind": {"enum": list(ISSUE_KINDS)},
            "severity": {"enum": ["P0", "P1", "P2", "P3"]},
            "status": {"enum": list(ISSUE_STATUSES)},
            "summary": {"type": "string", "minLength": 1},
            "observed": {},
            "limit": {},
            "affectedClaims": {"type": "array", "items": {"type": "string"}},
            "affectedGates": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "suspectedCauses": {"type": "array", "items": {"type": "object"}},
            "falsifiedCauses": {"type": "array", "items": {"type": "string"}},
            "conflictsWith": {"type": "array", "items": {"type": "string"}},
            "interactingFactors": {"type": "array", "items": {"type": "string"}},
            "minimalReproducer": {"type": ["string", "null"]},
            "blockedWork": {"type": "array", "items": {"type": "string"}},
            "nextDiagnostic": {"type": ["string", "null"]},
            "resolutionEvidence": {"type": "array", "items": {"type": "object"}},
        },
    }


def make_issue(
    issue_id: str,
    *,
    kind: str,
    severity: str,
    summary: str,
    status: str = "open",
    lane: str | None = None,
    cell_id: str | None = None,
    affected_claims: Iterable[str] = (),
    affected_gates: Iterable[str] = (),
    evidence: Iterable[dict[str, Any]] = (),
    suspected_causes: Iterable[dict[str, Any]] = (),
    falsified_causes: Iterable[str] = (),
    conflicts_with: Iterable[str] = (),
    interacting_factors: Iterable[str] = (),
    minimal_reproducer: str | None = None,
    blocked_work: Iterable[str] = (),
    next_diagnostic: str | None = None,
    observed: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    if kind not in ISSUE_KINDS:
        raise ValueError(f"unsupported issue kind: {kind}")
    if status not in ISSUE_STATUSES:
        raise ValueError(f"unsupported issue status: {status}")
    return {
        "schema": ISSUE_SCHEMA,
        "issueId": issue_id,
        "recordedAt": _now(),
        "campaignId": CAMPAIGN_ID,
        "lane": lane,
        "cellId": cell_id,
        "kind": kind,
        "severity": severity,
        "status": status,
        "summary": summary,
        "observed": observed,
        "limit": limit,
        "affectedClaims": list(affected_claims),
        "affectedGates": list(affected_gates),
        "evidence": list(evidence),
        "suspectedCauses": list(suspected_causes),
        "falsifiedCauses": list(falsified_causes),
        "conflictsWith": list(conflicts_with),
        "interactingFactors": list(interacting_factors),
        "minimalReproducer": minimal_reproducer,
        "blockedWork": list(blocked_work),
        "nextDiagnostic": next_diagnostic,
        "resolutionEvidence": [],
    }


def initial_issues() -> list[dict[str, Any]]:
    return [
        make_issue(
            "CAM-0001",
            kind="known-limitation",
            severity="P1",
            summary="No experimental dataset with declared measurement uncertainty is pinned yet.",
            affected_claims=("empirically validated laminar envelope",),
            affected_gates=("crossLane.experimentalValidationRequiredForEmpiricalClaim",),
            blocked_work=("empirical validation acceptance",),
            next_diagnostic="Select and provenance-pin an experimental rectangular-channel dataset before calculating validation uncertainty.",
        ),
        make_issue(
            "CAM-0002",
            kind="known-limitation",
            severity="P2",
            status="accepted-limitation",
            summary="The pinned AMD64 OpenFOAM image runs under emulation on this host; concurrent runs cannot support performance claims.",
            affected_claims=("native solver performance",),
            evidence=({"image": IMAGE, "digest": IMAGE_DIGEST},),
            blocked_work=("performance promotion",),
            next_diagnostic="Repeat timings exclusively on native AMD64 hardware if a performance claim is later requested.",
        ),
    ]


def interference_register() -> dict[str, Any]:
    pairs = [
        ("reynoldsNumber", "axialCellAspectRatio"),
        ("reynoldsNumber", "gridResolution"),
        ("lengthToHeightRatio", "inletDevelopmentError"),
        ("boundaryCondition", "pressureAndForceAccuracy"),
        ("iterativeError", "observedSpatialOrder"),
        ("meshQuality", "faceTractionAccuracy"),
        ("parallelDecomposition", "smallForceReduction"),
        ("integratedForceAgreement", "fieldError"),
        ("experimentalUncertainty", "numericalGci"),
        ("resourceContention", "runtimeAndIncompleteOutput"),
    ]
    return {
        "schema": "flowlab.laminar-all-hex-interference-register.v1",
        "status": "predeclared",
        "pairs": [
            {
                "left": left,
                "right": right,
                "assessment": "pending-campaign-evidence",
                "issueIds": [],
            }
            for left, right in pairs
        ],
    }


def build_manifest(root: Path = _ROOT) -> dict[str, Any]:
    cells = campaign_cells()
    sources = source_register(root)
    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell["lane"]] = counts.get(cell["lane"], 0) + 1
    return {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "scientificStatus": "planned-fail-closed",
        "mobile": {"inScope": False, "changes": "none"},
        "execution": {
            "strategy": "parallel-resource-weighted",
            "acceptance": "dependency-ordered",
            "performanceClaimsDuringConcurrentRuns": False,
            "maxFineWorkersPerHost": 1,
            "retryPolicy": {
                "infrastructure": 1,
                "scientific": 0,
                "confirmationRequiresNewCell": True,
            },
        },
        "solver": {
            "image": IMAGE,
            "digest": IMAGE_DIGEST,
            "application": "OpenFOAM 11 incompressibleFluid",
            "instrumentedLibrary": {
                "path": INSTRUMENTED_LIBRARY_RELATIVE,
                "sha256": INSTRUMENTED_LIBRARY_SHA256,
                "scope": "affine lane only",
            },
            "velocitySolver": "PBiCGStab/DILU",
            "boundaryContract": "fixed inlet/outlet kinematic pressure traces plus pressureInletOutletVelocity and exact tangential velocity",
        },
        "physicalEnvelope": {
            "reynoldsNumberHeightBased": list(REYNOLDS_LEVELS),
            "flowDirection": [item[0] for item in DIRECTION_LEVELS],
            "lengthToHeightRatio": list(LENGTH_RATIO_LEVELS),
            "axialCellAspectRatio": list(AXIAL_CELL_ASPECT_LEVELS),
            "cellsPerHeight": [item[1] for item in LEVELS],
        },
        "nestedClaims": [
            "baseline-point",
            "reynolds-extension-at-baseline-geometry",
            "geometry-and-cell-aspect-extension",
            "direction-reversal-extension",
            "experimentally-validated-envelope",
        ],
        "laneCounts": counts,
        "primaryScientificCellCount": sum(
            1 for cell in cells if cell["lane"] in SCIENTIFIC_LANES
        ),
        "cells": cells,
        "gateCatalog": gate_catalog(),
        "sourceRegister": sources,
        "externalInputs": {
            "experimentalDataset": {
                "status": "missing",
                "required": True,
                "requiredQuantities": [
                    "volume flow",
                    "pressure drop",
                    "velocity profiles",
                    "wall shear or friction factor",
                    "measurement uncertainty",
                ],
            }
        },
    }


def validate_manifest(manifest: dict[str, Any], root: Path = _ROOT) -> dict[str, bool]:
    cells = manifest.get("cells", [])
    cell_ids = [cell.get("cellId") for cell in cells]
    physical = [cell for cell in cells if cell.get("lane") == "physical-envelope"]
    expected_physical_ids = {cell["cellId"] for cell in physical_cells()}
    actual_source = source_register(root)
    recorded_sources = {
        row["path"]: row for row in manifest.get("sourceRegister", {}).get("records", [])
    }
    source_hashes_match = all(
        recorded_sources.get(row["path"], {}).get("exists") is True
        and recorded_sources[row["path"]].get("sha256") == row["sha256"]
        for row in actual_source["records"]
    )
    checks = {
        "schema": manifest.get("schema") == SCHEMA,
        "campaignId": manifest.get("campaignId") == CAMPAIGN_ID,
        "desktopOnly": manifest.get("mobile") == {"inScope": False, "changes": "none"},
        "pinnedImage": manifest.get("solver", {}).get("image") == IMAGE,
        "pinnedImageDigest": manifest.get("solver", {}).get("digest") == IMAGE_DIGEST,
        "pinnedInstrumentedLibrary": manifest.get("solver", {}).get(
            "instrumentedLibrary"
        )
        == {
            "path": INSTRUMENTED_LIBRARY_RELATIVE,
            "sha256": INSTRUMENTED_LIBRARY_SHA256,
            "scope": "affine lane only",
        },
        "uniqueCellIds": len(cell_ids) == len(set(cell_ids)) and None not in cell_ids,
        "affineThreeGrid": manifest.get("laneCounts", {}).get("affine") == 3,
        "nonAffineThreeGrid": manifest.get("laneCounts", {}).get("non-affine-mms") == 3,
        "physicalFullFactorial72": len(physical) == 72
        and {cell["cellId"] for cell in physical} == expected_physical_ids,
        "primaryScientificCount78": manifest.get("primaryScientificCellCount") == 78,
        "gateCatalogFrozen": manifest.get("gateCatalog") == gate_catalog(),
        "sourceHashesMatch": source_hashes_match,
        "fineConcurrencyBounded": manifest.get("execution", {}).get("maxFineWorkersPerHost") == 1,
        "scientificRetryDisabled": manifest.get("execution", {}).get("retryPolicy", {}).get("scientific") == 0,
    }
    return checks


@dataclass(frozen=True)
class ResourceBudget:
    capacity: int
    max_workers: int
    max_fine_workers: int = 1

    @classmethod
    def discover(cls, capacity: int | None = None) -> "ResourceBudget":
        logical = max(1, os.cpu_count() or 1)
        resolved = capacity if capacity is not None else max(2, min(8, logical // 2))
        if resolved <= 0:
            raise ValueError("scheduler capacity must be positive")
        return cls(capacity=resolved, max_workers=max(1, min(logical, resolved)))


class WeightedScheduler:
    def __init__(
        self,
        budget: ResourceBudget,
        event_path: Path,
    ) -> None:
        self.budget = budget
        self.event_path = event_path
        self._event_lock = threading.Lock()

    def _event(self, event: str, cell: dict[str, Any], **extra: Any) -> None:
        _append_jsonl(
            self.event_path,
            {
                "schema": "flowlab.laminar-all-hex-execution-event.v1",
                "recordedAt": _now(),
                "event": event,
                "cellId": cell["cellId"],
                "lane": cell["lane"],
                "resourceWeight": cell["resourceWeight"],
                **extra,
            },
            self._event_lock,
        )

    def run(
        self,
        cells: Iterable[dict[str, Any]],
        worker: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        pending = list(cells)
        running: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        available = self.budget.capacity
        fine_running = 0
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.budget.max_workers) as executor:
            while pending or running:
                launched = False
                for cell in list(pending):
                    weight = int(cell["resourceWeight"])
                    is_fine = cell.get("resourceClass") == "fine"
                    if weight > self.budget.capacity:
                        raise ValueError(
                            f"cell {cell['cellId']} weight {weight} exceeds capacity {self.budget.capacity}"
                        )
                    if weight > available:
                        continue
                    if is_fine and fine_running >= self.budget.max_fine_workers:
                        continue
                    pending.remove(cell)
                    available -= weight
                    fine_running += int(is_fine)
                    self._event(
                        "started",
                        cell,
                        availableCapacity=available,
                        fineWorkers=fine_running,
                    )
                    running[executor.submit(worker, cell)] = cell
                    launched = True
                if not running:
                    if pending and not launched:
                        raise RuntimeError("scheduler deadlock")
                    continue
                if launched and pending:
                    continue
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    cell = running.pop(future)
                    weight = int(cell["resourceWeight"])
                    is_fine = cell.get("resourceClass") == "fine"
                    available += weight
                    fine_running -= int(is_fine)
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive boundary
                        result = {
                            "cellId": cell["cellId"],
                            "status": "incomplete-infrastructure",
                            "error": str(exc),
                        }
                    results.append(result)
                    self._event(
                        "completed",
                        cell,
                        status=result.get("status"),
                        availableCapacity=available,
                        fineWorkers=fine_running,
                    )
        return sorted(results, key=lambda item: item["cellId"])


def _dry_run_cell(cell: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "schema": cell.get("schema") == CELL_SCHEMA,
        "cellId": bool(cell.get("cellId")),
        "lane": bool(cell.get("lane")),
        "resourceWeight": isinstance(cell.get("resourceWeight"), int)
        and cell["resourceWeight"] > 0,
        "parameters": isinstance(cell.get("parameters"), dict),
        "scientificRetryIsNotEncodedInCell": "retry" not in cell,
    }
    if cell.get("lane") == "physical-envelope":
        parameters = cell["parameters"]
        checks.update(
            {
                "reynoldsDeclared": parameters.get("reynoldsNumberHeightBased")
                in REYNOLDS_LEVELS,
                "directionDeclared": parameters.get("flowDirection")
                in {item[0] for item in DIRECTION_LEVELS},
                "lengthRatioDeclared": parameters.get("lengthToHeightRatio")
                in LENGTH_RATIO_LEVELS,
                "cellAspectDeclared": parameters.get("axialCellAspectRatio")
                in AXIAL_CELL_ASPECT_LEVELS,
                "resolutionDeclared": parameters.get("cellsPerHeight")
                in {item[1] for item in LEVELS},
            }
        )
    return {
        "schema": "flowlab.laminar-all-hex-cell-dry-run.v1",
        "cellId": cell["cellId"],
        "lane": cell["lane"],
        "status": "dry-run-passed" if all(checks.values()) else "dry-run-failed",
        "checks": checks,
        "failedChecks": [name for name, passed in checks.items() if not passed],
    }


def run_dry_run(output: Path, *, capacity: int | None = None) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty campaign output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    manifest_checks = validate_manifest(manifest)
    manifest_hash = _canonical_sha256(manifest)
    _write_json(output / "campaign-manifest.json", manifest)
    (output / "campaign-manifest.sha256").write_text(
        manifest_hash + "  campaign-manifest.json\n", encoding="utf-8"
    )
    _write_json(output / "gate-catalog.json", manifest["gateCatalog"])
    _write_json(output / "source-register.json", manifest["sourceRegister"])
    _write_json(output / "issue.schema.json", issue_json_schema())
    _write_json(output / "interference-register.json", interference_register())
    for issue in initial_issues():
        _append_jsonl(output / "issues.jsonl", issue)
    budget = ResourceBudget.discover(capacity)
    scheduler = WeightedScheduler(budget, output / "execution-events.jsonl")
    results = scheduler.run(manifest["cells"], _dry_run_cell)
    for result in results:
        _write_json(output / "cells" / result["cellId"] / "dry-run.json", result)
    cell_checks_pass = all(result["status"] == "dry-run-passed" for result in results)
    control_pass = all(manifest_checks.values()) and cell_checks_pass
    report = {
        "schema": DRY_RUN_SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "status": "accepted" if control_pass else "rejected",
        "manifestSha256": manifest_hash,
        "manifestChecks": manifest_checks,
        "failedManifestChecks": [
            name for name, passed in manifest_checks.items() if not passed
        ],
        "scheduler": {
            "capacity": budget.capacity,
            "maxWorkers": budget.max_workers,
            "maxFineWorkers": budget.max_fine_workers,
        },
        "plannedCellCount": len(manifest["cells"]),
        "dryRunPassedCellCount": sum(
            result["status"] == "dry-run-passed" for result in results
        ),
        "readyForNumericalLaunch": control_pass,
        "readyForEmpiricalAcceptance": False,
        "empiricalBlocker": "CAM-0001",
        "scientificSolvesExecuted": False,
        "mobile": {"inScope": False, "changes": "none"},
    }
    _write_json(output / "dry-run-report.json", report)
    _write_json(
        output / "campaign-report.json",
        {
            "schema": "flowlab.laminar-all-hex-campaign-report.v1",
            "campaignId": CAMPAIGN_ID,
            "status": "control-plane-accepted" if control_pass else "control-plane-rejected",
            "dryRun": {
                "path": "dry-run-report.json",
                "sha256": _sha256(output / "dry-run-report.json"),
            },
            "nextStage": "scientific-lane-launch" if control_pass else "control-plane-repair",
            "promotionAuthorized": False,
        },
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry-run",), default="dry-run")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int)
    args = parser.parse_args()
    report = run_dry_run(args.output.resolve(), capacity=args.capacity)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
