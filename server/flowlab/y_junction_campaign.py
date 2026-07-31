"""Prospective qualification runner for the bounded symmetric Y-junction."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from . import adapters
from .execution import TERMINAL_STATUSES, JobManager, materialize_case_files, validate_solver_case
from .schemas import CaseRequest, SolverCase


CONTRACT_SCHEMA = "flowlab.y-junction-qualification-contract.v1"
CAMPAIGN_SCHEMA = "flowlab.y-junction-campaign.v1"
EVALUATION_SCHEMA = "flowlab.y-junction-case-evaluation.v1"
ASSESSMENT_SCHEMA = "flowlab.y-junction-qualification-assessment.v1"
PACKAGE_SCHEMA = "flowlab.y-junction-package-manifest.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "validation"
    / "y-junction"
    / "QUALIFICATION_CONTRACT_V5.json"
)
RUNBOOK_PATH = CONTRACT_PATH.with_name("RUNBOOK_V5.md")
FROZEN_SOURCE_PATHS = (
    "server/flowlab/adapters.py",
    "server/flowlab/execution.py",
    "server/flowlab/schemas.py",
    "server/flowlab/y_junction.py",
    "server/flowlab/y_junction_campaign.py",
    "src/App.tsx",
    "src/App.resultLink.test.ts",
    "src/App.test.tsx",
    "src/projectSchema.ts",
    "src/projectSchema.test.ts",
    "src/types.ts",
    "tests/e2e/editor.spec.ts",
    "server/tests/test_y_junction.py",
    "server/tests/test_y_junction_campaign.py",
    "server/tests/test_execution.py",
    "docs/validation/y-junction/QUALIFICATION_CONTRACT_V1.json",
    "docs/validation/y-junction/RUNBOOK.md",
    "docs/validation/y-junction/QUALIFICATION_CONTRACT_V2.json",
    "docs/validation/y-junction/RUNBOOK_V2.md",
    "docs/validation/y-junction/QUALIFICATION_CONTRACT_V3.json",
    "docs/validation/y-junction/RUNBOOK_V3.md",
    "docs/validation/y-junction/QUALIFICATION_CONTRACT_V4.json",
    "docs/validation/y-junction/RUNBOOK_V4.md",
    "docs/validation/y-junction/QUALIFICATION_CONTRACT_V5.json",
    "docs/validation/y-junction/RUNBOOK_V5.md",
)


class YJunctionCampaignError(RuntimeError):
    """Raised when the frozen campaign cannot proceed honestly."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise YJunctionCampaignError(f"could not read required JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise YJunctionCampaignError(f"required JSON artifact is not an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_terminal_state(output_dir: Path, state: dict[str, Any]) -> None:
    _write_json(output_dir / "campaign-run-state.json", state)
    _write_json(output_dir / "campaign-assessment.json", state)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_contract() -> dict[str, Any]:
    contract = _read_json(CONTRACT_PATH)
    if (
        contract.get("schema") != CONTRACT_SCHEMA
        or contract.get("status")
        != "prospective-frozen-before-first-openfoam-execution"
        or contract.get("promotionAuthorized") is not False
    ):
        raise YJunctionCampaignError("unsupported or unfrozen Y-junction contract")
    levels = contract.get("levels")
    if (
        not isinstance(levels, list)
        or [row.get("id") for row in levels if isinstance(row, dict)]
        != ["coarse", "medium", "fine"]
    ):
        raise YJunctionCampaignError("Y-junction contract must freeze coarse, medium, and fine")
    ratio = float(contract["refinementInterpretation"]["uniformCharacteristicCellSizeRatio"])
    sizes = [float(row["cellSizeM"]) for row in levels]
    if not (
        math.isclose(sizes[0] / sizes[1], ratio, rel_tol=1.0e-12)
        and math.isclose(sizes[1] / sizes[2], ratio, rel_tol=1.0e-12)
    ):
        raise YJunctionCampaignError(
            f"Y-junction frozen cell sizes are not uniform r={ratio:g}"
        )
    master_size = float(contract["fixedMaster"]["cellSizeM"])
    factors = [int(row["refinementFactor"]) for row in levels]
    if factors != [1, 2, 4] or any(
        not math.isclose(
            float(row["masterCellSizeM"]),
            master_size,
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        )
        or not math.isclose(
            float(row["cellSizeM"]),
            master_size / int(row["refinementFactor"]),
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        )
        for row in levels
    ):
        raise YJunctionCampaignError(
            "Y-junction fixed-master levels must use factors 1, 2, and 4"
        )
    return contract


def _level_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in contract["levels"] if isinstance(row, dict)]


def _project(
    contract: dict[str, Any],
    level: dict[str, Any],
    *,
    asymmetric: bool = False,
) -> dict[str, Any]:
    physical = contract["physicalCase"]
    total_flow = float(physical["nominalCircularInletFlowM3PerS"])
    lower_pressure = (
        101325.0 + float(physical["asymmetricLowerOutletKinematicPressureM2PerS2"])
        * float(physical["densityKgPerM3"])
        if asymmetric
        else 101325.0
    )
    pipe = {
        "type": "pipe",
        "fromPort": "outlet",
        "toPort": "inlet",
        "shape": {"kind": "circular", "diameter": float(physical["diameterM"])},
    }
    return {
        "version": 1,
        "name": (
            f"Bounded Y-junction {level['id']} asymmetric negative control"
            if asymmetric
            else f"Bounded Y-junction {level['id']} equal pressure"
        ),
        "fluid": {
            "density": float(physical["densityKgPerM3"]),
            "dynamicViscosity": float(physical["dynamicViscosityPaS"]),
            "temperature": 293.15,
            "vaporPressure": 2340.0,
            "bulkModulus": 2.2e9,
        },
        "nodes": {
            "source": {
                "id": "source",
                "type": "source",
                "position": {"x": 0.0, "y": 0.0},
                "pressure": 101325.0,
            },
            "junction": {
                "id": "junction",
                "type": "junction",
                "position": {"x": 200.0, "y": 0.0},
            },
            "upper": {
                "id": "upper",
                "type": "sink",
                "position": {"x": 400.0, "y": 115.47005383792515},
                "pressure": 101325.0,
                "flowDemand": total_flow / 2.0,
            },
            "lower": {
                "id": "lower",
                "type": "sink",
                "position": {"x": 400.0, "y": -115.47005383792515},
                "pressure": lower_pressure,
                "flowDemand": total_flow / 2.0,
            },
        },
        "edges": {
            "inlet-pipe": {
                **pipe,
                "id": "inlet-pipe",
                "from": "source",
                "to": "junction",
                "length": float(physical["inletLengthM"]),
            },
            "upper-branch": {
                **pipe,
                "id": "upper-branch",
                "from": "junction",
                "to": "upper",
                "length": float(physical["branchLengthM"]),
            },
            "lower-branch": {
                **pipe,
                "id": "lower-branch",
                "from": "junction",
                "to": "lower",
                "length": float(physical["branchLengthM"]),
            },
        },
        "solver": {
            "tier": "openfoam",
            "advancedMode": "incompressible-navier-stokes",
            "turbulence": "laminar",
            "meshResolution": str(level["id"]),
            "runMode": "steady",
            "meshMode": "y-junction",
            "meshControls": {
                "yJunctionCellSizeM": float(level["cellSizeM"]),
                "yJunctionMasterCellSizeM": float(level["masterCellSizeM"]),
                "yJunctionRefinementFactor": int(level["refinementFactor"]),
            },
            "maxIterations": int(contract["productRequest"]["maxIterations"]),
            "tolerance": float(contract["productRequest"]["residualControl"]["p"]),
            "yJunctionProbeSampling": dict(contract["probeSampling"]),
        },
        "visualization": {
            "mode": "simulate",
            "overlay": "pressure",
            "particles": False,
            "streamlines": True,
            "grid": True,
        },
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        "sweeps": [],
    }


def build_case(
    level: dict[str, Any],
    contract: dict[str, Any] | None = None,
    *,
    asymmetric: bool = False,
) -> SolverCase:
    selected = contract or load_contract()
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_project(selected, level, asymmetric=asymmetric),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    issues = validate_solver_case(case)
    if issues:
        raise YJunctionCampaignError("generated Y-junction case is invalid: " + "; ".join(issues))
    profile = json.loads(case.files["constant/flowlab_y_junction_profile.json"])
    if (
        profile.get("schema") != "flowlab.y-junction-profile.v1"
        or not math.isclose(
            float(profile["geometry"]["cellSizeM"]),
            float(level["cellSizeM"]),
            rel_tol=1.0e-12,
        )
        or not math.isclose(
            float(profile["geometry"]["masterCellSizeM"]),
            float(level["masterCellSizeM"]),
            rel_tol=1.0e-12,
        )
        or int(profile["mesh"]["refinement"]["factor"])
        != int(level["refinementFactor"])
        or not math.isclose(
            float(profile["flow"]["nominalReynoldsNumber"]),
            float(selected["physicalCase"]["nominalReynoldsNumber"]),
            rel_tol=1.0e-12,
        )
        or profile.get("probeSampling") != selected["probeSampling"]
    ):
        raise YJunctionCampaignError("generated Y-junction profile does not match the frozen contract")
    return case


def _case_file_hashes(case: SolverCase) -> dict[str, str]:
    return {path: _sha256_text(text) for path, text in sorted(case.files.items())}


def _case_rows(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool]]:
    rows = [(str(level["id"]), level, False) for level in _level_rows(contract)]
    fine = _level_rows(contract)[-1]
    rows.append(("fine-asymmetric-control", fine, True))
    return rows


def _parent_provenance(preview: dict[str, Any]) -> dict[str, Any]:
    refinement = preview.get("refinement")
    invariants = preview.get("geometryInvariants")
    parents = preview.get("parentCellIndices")
    if (
        not isinstance(refinement, dict)
        or not isinstance(invariants, dict)
        or not isinstance(parents, list)
    ):
        raise YJunctionCampaignError("fixed-master mesh provenance is missing")
    factor = int(refinement["factor"])
    master_count = int(invariants["masterCellCount"])
    counts = {index: 0 for index in range(master_count)}
    valid = len(parents) == len(preview["cells"])
    for value in parents:
        if isinstance(value, bool) or not isinstance(value, int) or value not in counts:
            valid = False
            continue
        counts[value] += 1
    child_counts = list(counts.values())
    expected_master_regions = {
        str(region["id"]): int(region["masterCellCount"])
        for region in preview["regions"]
    }
    realized_regions = {
        str(region["id"]): int(region["cellCount"])
        for region in preview["regions"]
    }
    region_scaling = all(
        realized_regions[region_id] == master_count_for_region * factor**3
        for region_id, master_count_for_region in expected_master_regions.items()
    )
    return {
        "parentCellIndexCount": len(parents),
        "uniqueMasterParentCount": sum(count > 0 for count in child_counts),
        "masterCellCount": master_count,
        "allParentIndicesValid": valid,
        "minimumChildrenPerMasterCell": min(child_counts) if child_counts else 0,
        "maximumChildrenPerMasterCell": max(child_counts) if child_counts else 0,
        "expectedChildrenPerMasterCell": factor**3,
        "regionCellCounts": realized_regions,
        "masterRegionCellCounts": expected_master_regions,
        "regionCountsScaleByFactorCubed": region_scaling,
        "regionOwnershipReclassifiedFromGeometry": bool(
            refinement.get("regionOwnershipReclassifiedFromGeometry", True)
        ),
        "boundaryPatchesReclassifiedFromGeometry": bool(
            refinement.get("boundaryPatchesReclassifiedFromGeometry", True)
        ),
    }


def _fixed_master_hierarchy(
    records: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    equal_records = [record for record in records if not record["asymmetric"]]
    expected_levels = _level_rows(contract)
    if [record["level"] for record in equal_records] != [
        level["id"] for level in expected_levels
    ]:
        raise YJunctionCampaignError("fixed-master hierarchy is missing an equal-pressure level")
    master_hashes = {
        str(record["geometryInvariants"]["masterGeometrySha256"])
        for record in records
    }
    volumes = [float(record["geometryInvariants"]["totalCellVolumeM3"]) for record in equal_records]
    patch_areas = [record["geometryInvariants"]["patchAreasM2"] for record in equal_records]
    master_counts = [
        int(record["geometryInvariants"]["masterCellCount"]) for record in equal_records
    ]
    expected_master_count = int(contract["gates"]["fixedMasterHierarchy"]["masterCellCount"])
    per_level: dict[str, dict[str, bool]] = {}
    for record, level in zip(equal_records, expected_levels, strict=True):
        factor = int(level["refinementFactor"])
        provenance = record["parentProvenance"]
        expected_cell_count = int(level["expectedCellCount"])
        expected_patch_faces = {
            name: int(equal_records[0]["patches"][name]["faceCount"]) * factor**2
            for name in equal_records[0]["patches"]
        }
        gates = {
            "expectedCellCount": int(record["cellCount"]) == expected_cell_count,
            "parentProvenanceComplete": (
                provenance["allParentIndicesValid"]
                and provenance["parentCellIndexCount"] == expected_cell_count
                and provenance["uniqueMasterParentCount"] == expected_master_count
            ),
            "childrenPerMasterCell": (
                provenance["minimumChildrenPerMasterCell"] == factor**3
                and provenance["maximumChildrenPerMasterCell"] == factor**3
            ),
            "regionCountsScale": provenance["regionCountsScaleByFactorCubed"],
            "patchFaceCountsScale": {
                name: int(record["patches"][name]["faceCount"])
                for name in record["patches"]
            }
            == expected_patch_faces,
            "noGeometryReclassification": (
                not provenance["regionOwnershipReclassifiedFromGeometry"]
                and not provenance["boundaryPatchesReclassifiedFromGeometry"]
            ),
        }
        per_level[str(level["id"])] = gates
    gates = {
        "masterGeometrySha256Identical": len(master_hashes) == 1,
        "masterCellCount": all(value == expected_master_count for value in master_counts),
        "totalVolumeIdentical": all(value == volumes[0] for value in volumes[1:]),
        "patchAreasIdentical": all(value == patch_areas[0] for value in patch_areas[1:]),
        "allLevelHierarchyGates": all(
            all(level_gates.values()) for level_gates in per_level.values()
        ),
        "negativeControlMatchesFineGeometry": (
            records[-1]["generationSha256"] == equal_records[-1]["generationSha256"]
            and records[-1]["geometryInvariants"] == equal_records[-1]["geometryInvariants"]
            and records[-1]["parentProvenance"] == equal_records[-1]["parentProvenance"]
        ),
    }
    return {
        "masterGeometrySha256": next(iter(master_hashes)) if len(master_hashes) == 1 else None,
        "totalCellVolumeM3": volumes,
        "patchAreasM2": patch_areas,
        "perLevel": per_level,
        "gates": gates,
        "passed": all(gates.values()),
    }


def materialize_campaign(output_dir: Path) -> dict[str, Any]:
    contract = load_contract()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise YJunctionCampaignError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for label, level, asymmetric in _case_rows(contract):
        first = build_case(level, contract, asymmetric=asymmetric)
        second = build_case(level, contract, asymmetric=asymmetric)
        first_hashes = _case_file_hashes(first)
        second_hashes = _case_file_hashes(second)
        first_preview = json.loads(first.files["mesh/flowlab_mesh.json"])
        second_profile = json.loads(
            second.files["constant/flowlab_y_junction_profile.json"]
        )
        if first_hashes != second_hashes:
            raise YJunctionCampaignError(f"{label} duplicate generated-file hashes do not match")
        if first_preview.get("generationSha256") != second_profile["mesh"].get(
            "generationSha256"
        ):
            raise YJunctionCampaignError(f"{label} duplicate mesh generation hashes do not match")
        case_dir = output_dir / "cases" / label
        materialize_case_files(first, case_dir)
        preview = first_preview
        binding = first.resultComponentMap.artifactBindings[0].model_dump() if first.resultComponentMap else {}
        record = {
            "label": label,
            "level": level["id"],
            "asymmetric": asymmetric,
            "cellSizeM": level["cellSizeM"],
            "caseDirectory": str(case_dir.relative_to(output_dir)),
            "cellCount": len(preview["cells"]),
            "patches": preview["patches"],
            "generationSha256": preview["generationSha256"],
            "geometryInvariants": preview["geometryInvariants"],
            "refinement": preview["refinement"],
            "parentProvenance": _parent_provenance(preview),
            "resultBinding": binding,
            "determinism": {
                "duplicateGeneratedFileHashesMatch": True,
                "duplicateGenerationHashesMatch": True,
                "generatedFileCount": len(first_hashes),
                "generatedFileTreeSha256": hashlib.sha256(
                    "".join(
                        f"{path}\0{digest}\n" for path, digest in first_hashes.items()
                    ).encode("utf-8")
                ).hexdigest(),
            },
        }
        records.append(record)
        _write_json(case_dir / "qualification-case.json", record)
    hierarchy = _fixed_master_hierarchy(records, contract)
    if not hierarchy["passed"]:
        raise YJunctionCampaignError("fixed-master hierarchy failed before OpenFOAM execution")
    manifest = {
        "schema": CAMPAIGN_SCHEMA,
        "contractId": contract["contractId"],
        "status": "materialized-pending-openfoam",
        "scientificStatus": "experimental-qualification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "runbookSha256": _sha256_file(RUNBOOK_PATH),
        "cases": records,
        "fixedMasterHierarchy": hierarchy,
        "gates": contract["gates"],
    }
    _write_json(output_dir / "campaign-manifest.json", manifest)
    return manifest


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise YJunctionCampaignError(
            f"could not run provenance command: {' '.join(command)}"
        ) from exc


def _source_identity() -> dict[str, Any]:
    commit = _run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    status = _run(
        ["git", "status", "--porcelain", "--", *FROZEN_SOURCE_PATHS],
        cwd=REPOSITORY_ROOT,
    )
    if commit.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit.stdout.strip()):
        raise YJunctionCampaignError("could not resolve exact campaign source commit")
    if status.returncode != 0 or status.stdout.strip():
        raise YJunctionCampaignError(
            "refusing retained OpenFOAM execution with uncommitted frozen source paths"
        )
    return {
        "commit": commit.stdout.strip(),
        "frozenPaths": list(FROZEN_SOURCE_PATHS),
        "frozenPathsClean": True,
        "sourceSha256": {
            path: _sha256_file(REPOSITORY_ROOT / path) for path in FROZEN_SOURCE_PATHS
        },
    }


def _runtime_identity() -> dict[str, Any]:
    image = adapters._openfoam_image()
    inspect = _run(["docker", "image", "inspect", image], cwd=REPOSITORY_ROOT)
    if inspect.returncode != 0:
        raise YJunctionCampaignError(
            "the pinned OpenFOAM image is not locally inspectable: "
            + (inspect.stderr.strip() or inspect.stdout.strip())
        )
    try:
        records = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise YJunctionCampaignError("docker image inspect returned invalid JSON") from exc
    if not isinstance(records, list) or len(records) != 1:
        raise YJunctionCampaignError("docker image inspect did not resolve one image")
    record = records[0]
    return {
        "imageTag": image,
        "imageId": record.get("Id"),
        "repoDigests": sorted(record.get("RepoDigests") or []),
        "architecture": record.get("Architecture"),
        "os": record.get("Os"),
    }


def _latest_data_file(case_dir: Path, object_name: str, field_hint: str) -> Path:
    candidates = [
        path
        for path in (case_dir / "postProcessing" / object_name).rglob("*")
        if path.is_file()
        and (
            field_hint.lower() in path.name.lower()
            or path.name in {"surfaceFieldValue.dat", "patchFlowRate.dat"}
        )
    ]
    if not candidates:
        raise YJunctionCampaignError(
            f"missing retained {object_name} {field_hint} output in {case_dir}"
        )
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))[-1]


def _numeric_rows(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    pattern = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(token) for token in pattern.findall(line)]
        if len(values) >= 2 and all(math.isfinite(value) for value in values):
            rows.append(values)
    if not rows:
        raise YJunctionCampaignError(f"no finite numeric row in retained output: {path}")
    return rows


def _last_numeric_row(path: Path) -> list[float]:
    return _numeric_rows(path)[-1]


def _surface_value(case_dir: Path, object_name: str, field_hint: str) -> tuple[float, Path]:
    path = _latest_data_file(case_dir, object_name, field_hint)
    return _last_numeric_row(path)[-1], path


def _probe_values(case_dir: Path, field: str) -> tuple[list[float], Path]:
    path = _latest_data_file(case_dir, "yJunctionMirroredProbes", field)
    row = _last_numeric_row(path)
    return row[1:], path


def _surface_series(
    case_dir: Path,
    object_name: str,
    field_hint: str,
) -> tuple[dict[float, float], Path]:
    path = _latest_data_file(case_dir, object_name, field_hint)
    series = {row[0]: row[-1] for row in _numeric_rows(path)}
    return series, path


def _iterative_stability(
    case_dir: Path,
    contract: dict[str, Any],
) -> dict[str, Any]:
    inlet, inlet_path = _surface_series(case_dir, "inletPressure", "p")
    upper, upper_path = _surface_series(case_dir, "upperPressure", "p")
    lower, lower_path = _surface_series(case_dir, "lowerPressure", "p")
    common_times = sorted(set(inlet) & set(upper) & set(lower))
    limits = contract["gates"]["solverPerCase"]["iterativeStability"]
    final_window = float(limits["finalWindowIterations"])
    final_time = common_times[-1] if common_times else float("nan")
    window_times = [
        value for value in common_times if value >= final_time - final_window
    ]
    density = float(contract["physicalCase"]["densityKgPerM3"])
    qoi_values = [
        (inlet[value] - 0.5 * (upper[value] + lower[value])) * density
        for value in window_times
    ]
    finite = all(math.isfinite(value) for value in (*window_times, *qoi_values))
    relative_range = (
        (max(qoi_values) - min(qoi_values))
        / max(abs(qoi_values[-1]), 1.0e-12)
        if qoi_values and finite
        else float("inf")
    )
    gates = {
        "minimumCommonPressureSamples": len(qoi_values)
        >= int(limits["minimumCommonPressureSamples"]),
        "finitePrimaryQoiHistory": finite,
        "primaryQoiRelativeRange": relative_range
        <= float(limits["maximumPrimaryQoiRelativeRange"]),
    }
    return {
        "finalWindowIterations": final_window,
        "finalCommonSampleTime": final_time if math.isfinite(final_time) else None,
        "commonPressureSampleCount": len(qoi_values),
        "sampleTimes": window_times,
        "primaryPressureDropPa": qoi_values,
        "primaryQoiRelativeRange": relative_range if math.isfinite(relative_range) else None,
        "sourceArtifacts": [
            {"path": str(path.relative_to(case_dir)), "sha256": _sha256_file(path)}
            for path in (inlet_path, upper_path, lower_path)
        ],
        "gates": gates,
        "passed": all(gates.values()),
    }


def _check_mesh_log(case_dir: Path) -> Path:
    candidates = [
        case_dir / "log.checkMesh",
        *sorted(case_dir.rglob("log.checkMesh")),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise YJunctionCampaignError(f"retained checkMesh log is missing: {case_dir}")


def _solver_log(case_dir: Path) -> Path:
    candidates = (
        case_dir / "postProcessing" / "solverLogs" / "solve.log",
        case_dir / "log.foamRun",
        case_dir / "smoke.log",
    )
    for path in candidates:
        if path.is_file():
            return path
    logs = sorted(
        path for path in case_dir.rglob("*") if path.is_file() and "foamrun" in path.name.lower()
    )
    if logs:
        return logs[-1]
    raise YJunctionCampaignError(f"retained solver log is missing: {case_dir}")


def _mesh_metrics(case_dir: Path, preview: dict[str, Any]) -> dict[str, Any]:
    log_path = _check_mesh_log(case_dir)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    region_matches = re.findall(r"Number of regions:\s*(\d+)", text)
    cell_match = re.search(r"\bcells:\s*(\d+)", text)
    hex_match = re.search(r"\bhexahedra:\s*(\d+)", text)
    minimum_volume = re.search(
        r"min volume\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    boundary = (case_dir / "constant" / "polyMesh" / "boundary").read_text(
        encoding="utf-8",
        errors="replace",
    )
    patch_types = {
        name: patch_type
        for name, patch_type in re.findall(
            r"(?m)^\s*([A-Za-z][A-Za-z0-9]*)\s*\n\s*\{\s*\n\s*type\s+([A-Za-z]+);",
            boundary,
        )
    }
    expected = {"inlet": "patch", "outletUpper": "patch", "outletLower": "patch", "walls": "wall"}
    cells = int(cell_match.group(1)) if cell_match else -1
    hexes = int(hex_match.group(1)) if hex_match else -1
    regions = int(region_matches[-1]) if region_matches else -1
    min_volume = float(minimum_volume.group(1)) if minimum_volume else float("nan")
    gates = {
        "meshOk": "Mesh OK." in text,
        "connectedFluidRegions": regions == 1,
        "allCellsHex": cells == hexes == int(preview["topology"]["cellCount"]),
        "positiveMinimumVolume": math.isfinite(min_volume) and min_volume > 0.0,
        "exactPatchContract": patch_types == expected,
    }
    return {
        "checkMeshLog": str(log_path),
        "checkMeshLogSha256": _sha256_file(log_path),
        "cellCount": cells,
        "hexCellCount": hexes,
        "connectedRegions": regions,
        "minimumCellVolumeM3": min_volume,
        "patchTypes": patch_types,
        "gates": gates,
        "passed": all(gates.values()),
    }


def _mirrored_metrics(
    p_values: list[float],
    u_values: list[float],
    *,
    inlet_pressure: float,
    upper_pressure: float,
    lower_pressure: float,
) -> dict[str, float]:
    if len(p_values) < 7 or len(u_values) < 21:
        raise YJunctionCampaignError("retained mirrored probes are incomplete")
    junction_pressure = p_values[0]
    upper_pressures = p_values[1::2]
    lower_pressures = p_values[2::2]
    pressure_difference_rms = math.sqrt(
        sum((upper - lower) ** 2 for upper, lower in zip(upper_pressures, lower_pressures, strict=True))
        / len(upper_pressures)
    )
    pressure_scale = max(
        abs(inlet_pressure - 0.5 * (upper_pressure + lower_pressure)),
        1.0e-12,
    )
    vectors = [tuple(u_values[index : index + 3]) for index in range(0, len(u_values), 3)]
    upper_vectors = vectors[1::2]
    lower_vectors = vectors[2::2]
    squared_differences: list[float] = []
    upper_squared_speeds: list[float] = []
    for upper, lower in zip(upper_vectors, lower_vectors, strict=True):
        mirrored_lower = (lower[0], -lower[1], lower[2])
        squared_differences.append(
            sum((upper[index] - mirrored_lower[index]) ** 2 for index in range(3))
        )
        upper_squared_speeds.append(sum(value * value for value in upper))
    velocity_error = math.sqrt(sum(squared_differences) / len(squared_differences)) / max(
        math.sqrt(sum(upper_squared_speeds) / len(upper_squared_speeds)),
        1.0e-30,
    )
    upper_drop = junction_pressure - upper_pressure
    lower_drop = junction_pressure - lower_pressure
    pressure_drop_difference = abs(upper_drop - lower_drop) / max(
        abs(0.5 * (upper_drop + lower_drop)),
        1.0e-12,
    )
    return {
        "junctionProbeKinematicPressure": junction_pressure,
        "mirroredPressureRelativeError": pressure_difference_rms / pressure_scale,
        "mirroredVelocityRelativeError": velocity_error,
        "upperBranchKinematicPressureDrop": upper_drop,
        "lowerBranchKinematicPressureDrop": lower_drop,
        "branchPressureDropRelativeDifference": pressure_drop_difference,
    }


def evaluate_case(
    case_dir: Path,
    *,
    label: str,
    asymmetric: bool,
    solver_exit_code: int,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = contract or load_contract()
    preview = _read_json(case_dir / "mesh" / "flowlab_mesh.json")
    mesh = _mesh_metrics(case_dir, preview)
    solver_path = _solver_log(case_dir)
    solver_text = solver_path.read_text(encoding="utf-8", errors="replace")
    fatal_solver_output = (
        re.search(
            r"(?im)^\s*(?:floating point exception(?:\s|\(|$)|-->\s*FOAM FATAL(?: IO)? ERROR:)",
            solver_text,
        )
        is not None
        or re.search(
            r"(?i)(?:^|[^A-Za-z])(?:nan|inf)(?:[^A-Za-z]|$)",
            solver_text,
        )
        is not None
    )
    normal = (
        solver_exit_code == 0
        and re.search(r"(?m)^End\s*$", solver_text) is not None
        and not fatal_solver_output
    )

    inlet_flow, inlet_flow_path = _surface_value(case_dir, "inletFlow", "phi")
    upper_flow, upper_flow_path = _surface_value(case_dir, "upperFlow", "phi")
    lower_flow, lower_flow_path = _surface_value(case_dir, "lowerFlow", "phi")
    inlet_pressure, inlet_pressure_path = _surface_value(case_dir, "inletPressure", "p")
    upper_pressure, upper_pressure_path = _surface_value(case_dir, "upperPressure", "p")
    lower_pressure, lower_pressure_path = _surface_value(case_dir, "lowerPressure", "p")
    p_values, p_probe_path = _probe_values(case_dir, "p")
    u_values, u_probe_path = _probe_values(case_dir, "U")
    finite = all(
        math.isfinite(value)
        for value in (
            inlet_flow,
            upper_flow,
            lower_flow,
            inlet_pressure,
            upper_pressure,
            lower_pressure,
            *p_values,
            *u_values,
        )
    )
    q_in = abs(inlet_flow)
    q_upper = abs(upper_flow)
    q_lower = abs(lower_flow)
    mass_imbalance = abs(q_in - q_upper - q_lower) / max(q_in, 1.0e-30)
    upper_fraction = q_upper / max(q_upper + q_lower, 1.0e-30)
    mirrored = _mirrored_metrics(
        p_values,
        u_values,
        inlet_pressure=inlet_pressure,
        upper_pressure=upper_pressure,
        lower_pressure=lower_pressure,
    )
    primary_qoi_pa = (
        inlet_pressure - 0.5 * (upper_pressure + lower_pressure)
    ) * float(selected["physicalCase"]["densityKgPerM3"])
    iterative_stability = _iterative_stability(case_dir, selected)
    equal_limits = selected["gates"]["equalPressurePerLevel"]
    solver_gates = {
        "exitCode": solver_exit_code == int(selected["gates"]["solverPerCase"]["exitCode"]),
        "normalTermination": normal,
        "finitePressureAndVelocity": finite,
        "iterativeStability": iterative_stability["passed"],
    }
    if asymmetric:
        physics_gates = {
            "lowerPressureOutletHasGreaterOutflow": q_lower > q_upper,
            "massImbalance": mass_imbalance
            <= float(equal_limits["maximumRelativeMassImbalance"]),
        }
    else:
        physics_gates = {
            "massImbalance": mass_imbalance
            <= float(equal_limits["maximumRelativeMassImbalance"]),
            "equalOutletSplit": float(equal_limits["outletUpperFlowFractionMinimum"])
            <= upper_fraction
            <= float(equal_limits["outletUpperFlowFractionMaximum"]),
            "mirroredPressure": mirrored["mirroredPressureRelativeError"]
            <= float(equal_limits["maximumMirroredPressureError"]),
            "mirroredVelocity": mirrored["mirroredVelocityRelativeError"]
            <= float(equal_limits["maximumMirroredVelocityError"]),
            "branchPressureDropDifference": mirrored[
                "branchPressureDropRelativeDifference"
            ]
            <= float(equal_limits["maximumBranchPressureDropDifference"]),
        }
    source_paths = (
        inlet_flow_path,
        upper_flow_path,
        lower_flow_path,
        inlet_pressure_path,
        upper_pressure_path,
        lower_pressure_path,
        p_probe_path,
        u_probe_path,
    )
    return {
        "schema": EVALUATION_SCHEMA,
        "label": label,
        "asymmetric": asymmetric,
        "scientificStatus": "experimental-qualification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "mesh": mesh,
        "solver": {
            "exitCode": solver_exit_code,
            "normalTermination": normal,
            "finitePressureAndVelocity": finite,
            "solverLog": str(solver_path),
            "solverLogSha256": _sha256_file(solver_path),
            "iterativeStability": iterative_stability,
            "gates": solver_gates,
            "passed": all(solver_gates.values()),
        },
        "qoi": {
            "inletFlowM3PerS": inlet_flow,
            "upperOutletFlowM3PerS": upper_flow,
            "lowerOutletFlowM3PerS": lower_flow,
            "relativeMassImbalance": mass_imbalance,
            "upperOutletFlowFraction": upper_fraction,
            "inletKinematicPressure": inlet_pressure,
            "upperOutletKinematicPressure": upper_pressure,
            "lowerOutletKinematicPressure": lower_pressure,
            "primaryPressureDropPa": primary_qoi_pa,
            **mirrored,
        },
        "physics": {
            "gates": physics_gates,
            "passed": all(physics_gates.values()),
        },
        "sourceArtifacts": [
            {
                "path": str(path.relative_to(case_dir)),
                "sha256": _sha256_file(path),
            }
            for path in source_paths
        ],
        "allPerCaseGatesPassed": mesh["passed"]
        and all(solver_gates.values())
        and all(physics_gates.values()),
    }


def _sequence(evaluations: dict[str, dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    values = [
        float(evaluations[level]["qoi"]["primaryPressureDropPa"])
        for level in ("coarse", "medium", "fine")
    ]
    ratio = float(contract["refinementInterpretation"]["uniformCharacteristicCellSizeRatio"])
    numerator = values[0] - values[1]
    denominator = values[1] - values[2]
    qualified = (
        all(math.isfinite(value) for value in values)
        and numerator * denominator > 0.0
        and abs(denominator) > 1.0e-30
        and abs(values[2]) > 1.0e-30
    )
    reason = None
    order = float("nan")
    gci = float("nan")
    if qualified:
        order = math.log(abs(numerator / denominator)) / math.log(ratio)
        denominator_gci = ratio**order - 1.0
        if not math.isfinite(order) or denominator_gci <= 0.0:
            qualified = False
            reason = "observed order or GCI denominator is invalid"
        else:
            gci = 1.25 * abs((values[2] - values[1]) / values[2]) / denominator_gci * 100.0
            if not math.isfinite(gci):
                qualified = False
                reason = "fine-grid GCI is non-finite"
    else:
        reason = "primary-QoI sequence is non-monotone, degenerate, or non-finite"
    limits = contract["gates"]["sequence"]
    gates = {
        "mathematicallyQualified": qualified,
        "observedOrder": qualified
        and float(limits["minimumObservedOrder"])
        <= order
        <= float(limits["maximumObservedOrder"]),
        "finePrimaryQoiGci": qualified
        and gci <= float(limits["maximumFinePrimaryQoiGciPercent"]),
    }
    return {
        "primaryQoiPressureDropPa": values,
        "refinementRatio": ratio,
        "qualified": qualified,
        "reason": reason,
        "observedOrder": order if math.isfinite(order) else None,
        "fineGridGciPercent": gci if math.isfinite(gci) else None,
        "gates": gates,
        "passed": all(gates.values()),
    }


def execute_campaign(
    output_dir: Path,
    *,
    poll_interval_seconds: float = 0.25,
    timeout_seconds_per_case: float = 7200.0,
) -> dict[str, Any]:
    manifest = materialize_campaign(output_dir)
    output_dir = output_dir.resolve()
    contract = load_contract()
    source = _source_identity()
    runtime = _runtime_identity()
    manager = JobManager(runtime_root=output_dir / "runtime")
    state: dict[str, Any] = {
        "schema": ASSESSMENT_SCHEMA,
        "contractId": contract["contractId"],
        "status": "running",
        "scientificStatus": "experimental-qualification-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "campaignManifestSha256": _sha256_file(output_dir / "campaign-manifest.json"),
        "fixedMasterHierarchy": manifest["fixedMasterHierarchy"],
        "sourceControl": source,
        "runtimeEnvironment": runtime,
        "cases": [],
    }
    _write_json(output_dir / "campaign-run-state.json", state)
    evaluations: dict[str, dict[str, Any]] = {}
    for label, level, asymmetric in _case_rows(contract):
        case = build_case(level, contract, asymmetric=asymmetric)
        queued = manager.queue_job(case)
        deadline = time.monotonic() + timeout_seconds_per_case
        terminal = queued
        while terminal.status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                manager.cancel_job(terminal.id)
                raise YJunctionCampaignError(
                    f"{label} exceeded the frozen {timeout_seconds_per_case:g}-second timeout"
                )
            time.sleep(poll_interval_seconds)
            refreshed = manager.get_job(terminal.id)
            if refreshed is None:
                raise YJunctionCampaignError(f"JobManager lost {label}")
            terminal = refreshed
        record: dict[str, Any] = {
            "label": label,
            "level": level["id"],
            "asymmetric": asymmetric,
            "jobId": terminal.id,
            "status": terminal.status,
            "exitCode": terminal.exitCode,
            "execution": terminal.execution,
            "command": terminal.command,
            "error": terminal.error,
            "caseDirectory": None,
            "evaluationPath": None,
        }
        state["cases"].append(record)
        if terminal.caseDir:
            case_dir = Path(terminal.caseDir).resolve()
            if not case_dir.is_relative_to(output_dir):
                raise YJunctionCampaignError("JobManager evidence escaped the campaign directory")
            record["caseDirectory"] = str(case_dir.relative_to(output_dir))
        else:
            case_dir = output_dir / "missing"
        _write_json(output_dir / "campaign-run-state.json", state)
        if terminal.status != "complete" or terminal.exitCode != 0 or not terminal.caseDir:
            state.update(
                {
                    "status": "infrastructure-failure-retained",
                    "finishedAt": datetime.now(timezone.utc).isoformat(),
                    "failedCase": label,
                }
            )
            _write_terminal_state(output_dir, state)
            return state
        evaluation = evaluate_case(
            case_dir,
            label=label,
            asymmetric=asymmetric,
            solver_exit_code=int(terminal.exitCode),
            contract=contract,
        )
        evaluation_path = output_dir / "evaluations" / f"{label}.json"
        _write_json(evaluation_path, evaluation)
        record["evaluationPath"] = str(evaluation_path.relative_to(output_dir))
        record["evaluationSha256"] = _sha256_file(evaluation_path)
        record["allPerCaseGatesPassed"] = evaluation["allPerCaseGatesPassed"]
        evaluations[label] = evaluation
        _write_json(output_dir / "campaign-run-state.json", state)
        if not evaluation["allPerCaseGatesPassed"]:
            state.update(
                {
                    "status": "qualification-gate-failed-retained",
                    "finishedAt": datetime.now(timezone.utc).isoformat(),
                    "failedCase": label,
                    "allQualificationGatesPassed": False,
                }
            )
            _write_terminal_state(output_dir, state)
            return state

    sequence = _sequence(evaluations, contract)
    negative = evaluations["fine-asymmetric-control"]["physics"]["gates"][
        "lowerPressureOutletHasGreaterOutflow"
    ]
    passed = sequence["passed"] and negative
    state.update(
        {
            "status": (
                "software-and-numerical-qualification-passed"
                if passed
                else "qualification-gate-failed-retained"
            ),
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "negativeControlPassed": negative,
            "allQualificationGatesPassed": passed,
            "scientificStatus": (
                "software-and-numerical-qualification-candidate"
                if passed
                else "experimental-qualification-gates-failed"
            ),
            "limitations": [
                "No independent empirical validation was performed.",
                "The fixed Cartesian staircase master is not CAD-exact.",
                "The qualification claim is bounded to this symmetric +/-30-degree Re=100 steady laminar Y-junction.",
                "No product promotion, arbitrary-network, turbulence, transient, or release claim is authorized.",
            ],
        }
    )
    _write_terminal_state(output_dir, state)
    return state


def build_evidence_package(campaign_dir: Path) -> dict[str, Any]:
    campaign_dir = campaign_dir.resolve()
    assessment_path = campaign_dir / "campaign-assessment.json"
    assessment = _read_json(assessment_path)
    package_dir = campaign_dir / "immutable-evidence-package"
    if package_dir.exists():
        raise YJunctionCampaignError(f"refusing to overwrite evidence package: {package_dir}")
    package_dir.mkdir(parents=True)
    entries = [
        path
        for path in campaign_dir.rglob("*")
        if path.is_file() and not path.is_relative_to(package_dir)
    ]
    files = [
        {
            "path": str(path.relative_to(campaign_dir)),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(entries)
    ]
    tree_digest = hashlib.sha256(
        "".join(
            f"{item['path']}\0{item['size']}\0{item['sha256']}\n"
            for item in files
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "contractId": assessment["contractId"],
        "assessmentStatus": assessment["status"],
        "allQualificationGatesPassed": assessment.get("allQualificationGatesPassed", False),
        "validated": False,
        "promotionAuthorized": False,
        "fileCount": len(files),
        "files": files,
        "treeSha256": tree_digest,
        "reviewStatus": "awaiting-controlled-independent-review",
    }
    _write_json(package_dir / "package-manifest.json", manifest)
    for path in package_dir.rglob("*"):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(package_dir, 0o555)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--run-and-package", action="store_true")
    parser.add_argument("--materialize-only", action="store_true")
    args = parser.parse_args()
    if args.run_and_package == args.materialize_only:
        parser.error("choose exactly one of --run-and-package or --materialize-only")
    if args.materialize_only:
        result = materialize_campaign(args.output_dir)
    else:
        result = execute_campaign(args.output_dir)
        package = build_evidence_package(args.output_dir)
        result = {**result, "packageTreeSha256": package["treeSha256"]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
