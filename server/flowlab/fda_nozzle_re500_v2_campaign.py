"""Frozen full FDA nozzle Re=500 v2 validation campaign.

This module consumes, rather than regenerates, the preflight-selected contract.
It keeps preparation, mesh qualification, solver execution, observation, and
scientific assessment as separate fail-closed stages.  Desktop UI state is
never mutated here.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import time
from typing import Any, Callable, Iterable, Sequence

from .fda_nozzle_re500 import (
    ASSESSMENT_SCHEMA,
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_DIGEST,
    FdaNozzleDefinition,
    OFFICIAL_ARCHIVE_SHA256,
    OFFICIAL_COMMIT,
    OFFICIAL_DATASET_DOI,
    OFFICIAL_REPOSITORY,
    PRIMARY_PAPER_DOI,
    PRIMARY_PROFILE_STATIONS_M,
    WSS_UQ_PAPER_DOI,
    _BlockMeshBuilder,
    _case_files,
    _container_command,
    _latest_time,
    _now,
    _parse_check_mesh,
    _plane,
    _quads,
    _sha256,
    _summary_counts,
    _validation_row,
    _write,
    _write_json,
    experimental_summary,
    initialize_case,
    postprocess_case,
    run_command,
)
from .fda_nozzle_re500_v2_observation import (
    pressure_diagnostic,
    run_piv_observation,
    velocity_diagnostic,
)
from .fda_nozzle_re500_v2_preflight import (
    _formal_second_order,
    _render_block_mesh,
    _solver_diagnostic,
)


SCHEMA = "flowlab.fda-nozzle-re500-v2-full-campaign.v1"
CASE_SCHEMA = "flowlab.fda-nozzle-re500-v2-full-case.v1"
CAMPAIGN_ID = "2026-07-19-re500-v2-full"
RECOVERY_SCHEMA = "flowlab.fda-nozzle-re500-v2-fine-recovery.v1"
RECOVERY_CONTRACT_SCHEMA = "flowlab.fda-nozzle-re500-v2-fine-recovery-contract.v1"
RECOVERY_START_TIME = "750"
RECOVERY_END_TIME = "800"
RECOVERY_CONTRACT_SHA256 = (
    "9313f3a05202a1385980d218530331ca05272be29f2730083164b010d11642e3"
)
FROZEN_CONTRACT_SHA256 = (
    "99e2e481fbfad65836b4ae311b72a2db4f7d575c40beb9689ae575aa824bb904"
)
FROZEN_PREFLIGHT_ASSESSMENT_SHA256 = (
    "237759bad17be14908c25cbe6e08a9ce9edd2642f058ceeb82ccd491e405e50e"
)

LEVEL_CELLS: dict[str, dict[str, int]] = {
    "coarse": {
        "coreTangential": 2,
        "annularTangential": 2,
        "upstreamAnnularRadial": 1,
        "inletAxial": 58,
        "contractionAxial": 45,
        "throatAxial": 80,
        "nearDownstreamAxial": 240,
        "downstreamOuterRadial": 8,
        "farExtensionAxial": 0,
    },
    "medium": {
        "coreTangential": 4,
        "annularTangential": 4,
        "upstreamAnnularRadial": 2,
        "inletAxial": 116,
        "contractionAxial": 90,
        "throatAxial": 160,
        "nearDownstreamAxial": 480,
        "downstreamOuterRadial": 16,
        "farExtensionAxial": 0,
    },
    "fine": {
        "coreTangential": 8,
        "annularTangential": 8,
        "upstreamAnnularRadial": 4,
        "inletAxial": 232,
        "contractionAxial": 180,
        "throatAxial": 320,
        "nearDownstreamAxial": 960,
        "downstreamOuterRadial": 32,
        "farExtensionAxial": 0,
    },
}

EXPECTED_CELLS = {"coarse": 20_436, "medium": 163_488, "fine": 1_307_904}
CASES = (
    ("coarse", "coarse", 1.0),
    ("medium", "medium", 1.0),
    ("fine", "fine", 1.0),
    ("input-minus-5pct", "medium", 0.95),
    ("input-plus-5pct", "medium", 1.05),
)
LABELS = tuple(row[0] for row in CASES)
NOMINAL_LABELS = ("coarse", "medium", "fine")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_manifest(case: Path) -> dict[str, Any]:
    """Hash the exact immutable state admitted to a fine-grid recovery."""
    roots = (
        case / "0",
        case / RECOVERY_START_TIME,
        case / "constant",
        case / "system",
        case / "case-definition.json",
        case / "initialization.json",
    )
    files: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            raise ValueError(f"missing recovery snapshot input: {root}")
        paths = [root] if root.is_file() else sorted(
            path for path in root.rglob("*") if path.is_file()
        )
        for path in paths:
            files.append(
                {
                    "path": path.relative_to(case).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    payload = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-fine-snapshot.v1",
        "files": files,
        "snapshotSha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _patch_recovery_control_dict(path: Path) -> dict[str, str]:
    """Apply the sole prospectively authorized restart mutation."""
    original = path.read_text(encoding="utf-8")
    before = "startFrom startTime;"
    after = "startFrom latestTime;"
    if original.count(before) != 1 or after in original:
        raise ValueError("controlDict does not have the exact frozen startFrom state")
    if "endTime 800;" not in original or "runTimeModifiable false;" not in original:
        raise ValueError("controlDict no longer matches the frozen completion controls")
    original_sha256 = _sha256(path)
    _write(path, original.replace(before, after, 1))
    return {
        "path": "system/controlDict",
        "before": before,
        "after": after,
        "sourceSha256": original_sha256,
        "recoverySha256": _sha256(path),
    }


def _validate_recovery_contract(path: Path) -> dict[str, Any]:
    if _sha256(path) != RECOVERY_CONTRACT_SHA256:
        raise ValueError("fine recovery contract hash mismatch")
    contract = _read_json(path)
    if contract.get("schema") != RECOVERY_CONTRACT_SCHEMA:
        raise ValueError("unsupported fine recovery contract schema")
    authorization = contract.get("authorization", {})
    if not authorization.get("mayPrepareAndRunOneRecovery"):
        raise ValueError("fine recovery contract is not authorized")
    if authorization.get("scientificPromotion") or authorization.get("desktopPromotion"):
        raise ValueError("fine recovery contract cannot authorize promotion")
    return contract


def _verify_recovery_source(
    source: Path, contract: dict[str, Any]
) -> dict[str, Any]:
    expected = contract["sourceEvidence"]
    if source.name != expected["campaignId"]:
        raise ValueError("recovery source campaign id mismatch")
    paths = {
        "campaignManifestSha256": source / "campaign-manifest.json",
        "campaignContractSha256": source / "campaign-contract.json",
        "meshPreflightSha256": source / "mesh-preflight.json",
        "finishStatusSha256": source / "finish-status.json",
        "fineExecutionSha256": source / "results" / "fine" / "execution.json",
        "rescueLogSha256": source / expected["rescueLog"],
    }
    for key, path in paths.items():
        if _sha256(path) != expected[key]:
            raise ValueError(f"recovery source evidence drifted: {key}")
    execution = _read_json(paths["fineExecutionSha256"])
    finish = _read_json(paths["finishStatusSha256"])
    if execution.get("status") != "solver-failed":
        raise ValueError("source fine execution is not the frozen failed run")
    if str(execution.get("latestTime")) != RECOVERY_START_TIME:
        raise ValueError("source fine checkpoint is not exactly time 750")
    if finish.get("status") != "solver-gate-blocked":
        raise ValueError("source campaign is not fail-closed")
    if (source / "assessment.json").exists():
        raise ValueError("source campaign unexpectedly has a scientific assessment")
    snapshot = _snapshot_manifest(source / "cases" / "fine")
    if snapshot["snapshotSha256"] != expected["fineSnapshotSha256"]:
        raise ValueError("source fine checkpoint snapshot drifted")
    rescue_text = paths["rescueLogSha256"].read_text(
        encoding="utf-8", errors="replace"
    )
    if "Time = 800s" in rescue_text or "\nEnd\n" in rescue_text:
        raise ValueError("source interruption evidence unexpectedly indicates completion")
    return snapshot


def _copy_recovery_case(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for relative in (
        Path("0"),
        Path(RECOVERY_START_TIME),
        Path("constant"),
        Path("system"),
    ):
        shutil.copytree(source / relative, destination / relative)
    for relative in (Path("case-definition.json"), Path("initialization.json")):
        shutil.copy2(source / relative, destination / relative)


def prepare_fine_recovery(
    source: Path, output: Path, contract_path: Path
) -> dict[str, Any]:
    """Materialize a separate, provenance-bound continuation from time 750."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite recovery campaign: {output}")
    contract = _validate_recovery_contract(contract_path)
    source_snapshot = _verify_recovery_source(source, contract)
    output.mkdir(parents=True, exist_ok=True)

    for relative in (
        Path("campaign-contract.json"),
        Path("preflight-assessment.json"),
        Path("mesh-preflight.json"),
    ):
        shutil.copy2(source / relative, output / relative)
    for relative in (Path("experiment"), Path("bin")):
        shutil.copytree(source / relative, output / relative)

    for label in LABELS:
        if label == "fine":
            (output / "results" / label).mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                source / "results" / label / "mesh-preflight.json",
                output / "results" / label / "mesh-preflight.json",
            )
            continue
        shutil.copytree(
            source / "results" / label,
            output / "results" / label,
        )
        shutil.copytree(
            source / "logs" / label,
            output / "logs" / label,
        )

    _copy_recovery_case(source / "cases" / "fine", output / "cases" / "fine")
    cloned_snapshot = _snapshot_manifest(output / "cases" / "fine")
    if cloned_snapshot["snapshotSha256"] != source_snapshot["snapshotSha256"]:
        raise ValueError("cloned recovery snapshot does not match its source")
    mutation = _patch_recovery_control_dict(
        output / "cases" / "fine" / "system" / "controlDict"
    )
    recovery_snapshot = _snapshot_manifest(output / "cases" / "fine")

    provenance = output / "recovery-provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    shutil.copy2(contract_path, output / "recovery-contract.json")
    shutil.copy2(source / "campaign-manifest.json", provenance / "source-campaign-manifest.json")
    shutil.copy2(source / "finish-status.json", provenance / "source-finish-status.json")
    shutil.copy2(
        source / "results" / "fine" / "execution.json",
        provenance / "source-fine-execution.json",
    )
    shutil.copy2(
        source / contract["sourceEvidence"]["rescueLog"],
        provenance / "source-fine-rescue.log",
    )
    _write_json(provenance / "source-snapshot.json", source_snapshot)

    source_manifest = _read_json(source / "campaign-manifest.json")
    campaign_manifest = {
        **source_manifest,
        "campaignId": output.name,
        "createdAt": _now(),
        "status": "fine-recovery-prepared-awaiting-execution",
        "recovery": {
            "schema": RECOVERY_SCHEMA,
            "sourceCampaignId": source.name,
            "sourceCampaignPath": str(source),
            "sourceCheckpoint": RECOVERY_START_TIME,
            "targetEndTime": RECOVERY_END_TIME,
            "sourceSnapshotSha256": source_snapshot["snapshotSha256"],
            "recoverySnapshotSha256": recovery_snapshot["snapshotSha256"],
            "allowedMutation": mutation,
            "contractSha256": _sha256(output / "recovery-contract.json"),
            "runnerSha256": _sha256(Path(__file__).resolve()),
        },
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output / "campaign-manifest.json", campaign_manifest)
    report = {
        "schema": RECOVERY_SCHEMA,
        "preparedAt": campaign_manifest["createdAt"],
        "status": "fine-recovery-prepared-awaiting-execution",
        "checks": {
            "sourceEvidenceHashesMatch": True,
            "sourceCampaignFailClosed": True,
            "sourceAssessmentAbsent": True,
            "sourceCheckpointExactly750": True,
            "sourceSnapshotMatchesContract": True,
            "cloneMatchesSourceBeforeMutation": True,
            "onlyAuthorizedControlMutationApplied": True,
            "targetRemains800": True,
            "rawSourceEvidencePreserved": True,
        },
        "sourceSnapshotSha256": source_snapshot["snapshotSha256"],
        "recoverySnapshotSha256": recovery_snapshot["snapshotSha256"],
        "allowedMutation": mutation,
        "image": contract["runtime"]["image"],
        "imageDigest": contract["runtime"]["imageDigest"],
        "containerAutoRemove": False,
        "promotionAuthorized": False,
    }
    _write_json(output / "recovery-preflight.json", report)
    return report


def _docker_image_id(image: str) -> str:
    inspection = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    if inspection.returncode != 0:
        raise ValueError(f"unable to inspect recovery image: {inspection.stdout.strip()}")
    return inspection.stdout.strip()


def launch_fine_recovery(
    output: Path,
    container_name: str,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    """Create and start one retained, named recovery container."""
    if not container_name or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        for character in container_name
    ):
        raise ValueError("unsafe recovery container name")
    preflight = _read_json(output / "recovery-preflight.json")
    if preflight.get("status") != "fine-recovery-prepared-awaiting-execution":
        raise ValueError("recovery preflight is not launchable")
    contract = _validate_recovery_contract(output / "recovery-contract.json")
    if image != contract["runtime"]["image"]:
        raise ValueError("recovery image tag differs from the frozen contract")
    image_id = _docker_image_id(image)
    if image_id != contract["runtime"]["imageDigest"]:
        raise ValueError("recovery image digest differs from the frozen contract")
    current_snapshot = _snapshot_manifest(output / "cases" / "fine")
    if current_snapshot["snapshotSha256"] != preflight["recoverySnapshotSha256"]:
        raise ValueError("recovery case drifted after preflight")
    if (output / "results" / "fine" / "execution.json").exists():
        raise ValueError("recovery fine execution record already exists")

    existing = subprocess.run(
        ["docker", "inspect", container_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if existing.returncode == 0:
        raise ValueError("recovery container name already exists")
    workspace = Path(__file__).resolve().parents[2]
    case = output / "cases" / "fine"
    container_workspace = Path("/flowlab-workspace")
    container_case = container_workspace / case.relative_to(workspace)
    command = [
        "docker",
        "create",
        "--name",
        container_name,
        "--label",
        f"flowlab.campaign={output.name}",
        "--label",
        "flowlab.recovery=fine-from-750",
        "-v",
        f"{workspace}:{container_workspace}",
        "-w",
        str(container_case),
        image,
        "bash",
        "-lc",
        "if [ -f /opt/openfoam11/etc/bashrc ]; then "
        "source /opt/openfoam11/etc/bashrc; else "
        "source /opt/OpenFOAM/OpenFOAM-11/etc/bashrc; fi; "
        "exec foamRun -solver incompressibleFluid",
    ]
    created = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    if created.returncode != 0:
        raise RuntimeError(f"recovery docker create failed: {created.stdout.strip()}")
    container_id = created.stdout.strip()
    started = subprocess.run(
        ["docker", "start", container_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    report = {
        "schema": RECOVERY_SCHEMA,
        "launchedAt": _now(),
        "status": "fine-recovery-running" if started.returncode == 0 else "fine-recovery-launch-failed",
        "containerName": container_name,
        "containerId": container_id,
        "containerAutoRemove": False,
        "image": image,
        "imageDigest": image_id,
        "sourceCheckpoint": RECOVERY_START_TIME,
        "targetEndTime": RECOVERY_END_TIME,
        "exitCodes": {"dockerCreate": created.returncode, "dockerStart": started.returncode},
        "promotionAuthorized": False,
    }
    _write_json(output / "recovery-launch.json", report)
    manifest = _read_json(output / "campaign-manifest.json")
    manifest["status"] = report["status"]
    manifest["recovery"]["containerName"] = container_name
    manifest["recovery"]["containerId"] = container_id
    manifest["recovery"]["launchedAt"] = report["launchedAt"]
    _write_json(output / "campaign-manifest.json", manifest)
    if started.returncode != 0:
        raise RuntimeError(f"recovery docker start failed: {started.stdout.strip()}")
    return report


def _recovery_completed(exit_code: int, latest: str, log_text: str) -> bool:
    return (
        exit_code == 0
        and float(latest) >= float(RECOVERY_END_TIME)
        and "Time = 800s" in log_text
        and "\nEnd\n" in log_text
    )


def finalize_fine_recovery(
    output: Path,
    container_name: str,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    """Wait for the retained container, capture complete logs, and finish fail-closed."""
    existing_execution = output / "results" / "fine" / "execution.json"
    if existing_execution.is_file():
        execution = _read_json(existing_execution)
        if execution.get("status") == "solver-complete":
            return finish_when_ready(output, image=image, poll_seconds=1.0)
        return {
            "status": "solver-gate-blocked",
            "failed": {"fine": execution},
            "promotionAuthorized": False,
        }
    launch = _read_json(output / "recovery-launch.json")
    if launch.get("containerName") != container_name:
        raise ValueError("recovery container does not match its launch record")
    contract = _validate_recovery_contract(output / "recovery-contract.json")
    if image != contract["runtime"]["image"]:
        raise ValueError("recovery finalize image differs from the frozen contract")
    wait = subprocess.run(
        ["docker", "wait", container_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    try:
        container_exit = int(wait.stdout.strip()) if wait.returncode == 0 else -1
    except ValueError:
        container_exit = -1
    inspection = subprocess.run(
        ["docker", "inspect", container_name, "--format", "{{json .State}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    state = json.loads(inspection.stdout) if inspection.returncode == 0 else {}
    log = output / "logs" / "fine" / "foamRun.log"
    if log.exists():
        raise ValueError("refusing to overwrite an existing recovery solver log")
    log_code = run_command(["docker", "logs", container_name], output, log)
    log_text = log.read_text(encoding="utf-8", errors="replace")
    latest = _latest_time(output / "cases" / "fine")
    complete = _recovery_completed(container_exit, latest, log_text)
    mesh_report = _read_json(output / "results" / "fine" / "mesh-preflight.json")
    definition = _read_json(output / "cases" / "fine" / "case-definition.json")
    execution = {
        "schema": CASE_SCHEMA,
        "recoverySchema": RECOVERY_SCHEMA,
        "label": "fine",
        "executedAt": _now(),
        "definition": definition,
        "exitCodes": {
            "foamRun": container_exit,
            "dockerWait": wait.returncode,
            "dockerInspect": inspection.returncode,
            "dockerLogs": log_code,
        },
        "containerName": container_name,
        "containerId": launch["containerId"],
        "containerAutoRemove": False,
        "containerState": state,
        "image": image,
        "imageDigest": launch["imageDigest"],
        "sourceCheckpoint": RECOVERY_START_TIME,
        "latestTime": latest,
        "solverLogSha256": _sha256(log),
        "mesh": mesh_report["mesh"],
        "status": "solver-complete" if complete else "solver-failed",
        "promotionAuthorized": False,
    }
    _write_json(existing_execution, execution)
    manifest = _read_json(output / "campaign-manifest.json")
    manifest["status"] = (
        "fine-recovery-solver-complete" if complete else "fine-recovery-solver-blocked"
    )
    manifest["recovery"].update(
        {
            "finishedAt": execution["executedAt"],
            "latestTime": latest,
            "solverLogSha256": execution["solverLogSha256"],
            "containerState": state,
        }
    )
    _write_json(output / "campaign-manifest.json", manifest)
    if not complete:
        result = {
            "status": "solver-gate-blocked",
            "failed": {"fine": execution},
            "promotionAuthorized": False,
        }
        _write_json(output / "finish-status.json", result)
        return result
    return finish_when_ready(output, image=image, poll_seconds=1.0)


def run_fine_recovery(
    output: Path,
    container_name: str,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    launch = launch_fine_recovery(output, container_name, image)
    if launch.get("status") != "fine-recovery-running":
        return launch
    return finalize_fine_recovery(output, container_name, image)


def v2_block_mesh(level: str) -> str:
    """Render a strict all-hex member of the frozen 1/2/4 nested family."""
    if level not in LEVEL_CELLS:
        raise ValueError(f"unsupported v2 mesh level: {level}")
    cells = LEVEL_CELLS[level]
    spec = FdaNozzleDefinition()
    builder = _BlockMeshBuilder()

    upstream_x = (
        spec.inlet_x_m,
        spec.contraction_start_x_m,
        spec.throat_start_x_m,
        0.0,
    )
    upstream_planes: list[dict[str, list[int]]] = []
    for index, x in enumerate(upstream_x):
        radius = spec.radius(x - (1.0e-12 if x == 0.0 else 0.0))
        upstream_planes.append(
            _plane(
                builder,
                x,
                radius,
                core_half_width=radius / 2.0,
                prefix=f"u{index}",
            )
        )

    upstream_axial = (
        cells["inletAxial"],
        cells["contractionAxial"],
        cells["throatAxial"],
    )
    for left, right, axial in zip(
        upstream_planes, upstream_planes[1:], upstream_axial
    ):
        left_quads = _quads(left)
        right_quads = _quads(right)
        builder.block(
            left_quads[0],
            right_quads[0],
            (cells["coreTangential"], cells["coreTangential"], axial),
        )
        for left_quad, right_quad in zip(left_quads[1:], right_quads[1:]):
            builder.block(
                left_quad,
                right_quad,
                (
                    cells["upstreamAnnularRadial"],
                    cells["annularTangential"],
                    axial,
                ),
            )
            builder.boundary["wall"].append(
                (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
            )
    builder.boundary["inlet"].extend(
        tuple(quad) for quad in _quads(upstream_planes[0])
    )

    inner_start = upstream_planes[-1]
    outer_start = _plane(
        builder,
        0.0,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do0",
    )
    outer_start["core"] = inner_start["ring"]
    inner_end = _plane(
        builder,
        spec.outlet_x_m,
        spec.throat_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="di1",
    )
    outer_end = _plane(
        builder,
        spec.outlet_x_m,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do1",
    )
    outer_end["core"] = inner_end["ring"]
    axial = cells["nearDownstreamAxial"]
    inner_left = _quads(inner_start)
    inner_right = _quads(inner_end)
    builder.block(
        inner_left[0],
        inner_right[0],
        (cells["coreTangential"], cells["coreTangential"], axial),
    )
    for left_quad, right_quad in zip(inner_left[1:], inner_right[1:]):
        builder.block(
            left_quad,
            right_quad,
            (
                cells["upstreamAnnularRadial"],
                cells["annularTangential"],
                axial,
            ),
        )
    outer_left = _quads(outer_start)[1:]
    outer_right = _quads(outer_end)[1:]
    for left_quad, right_quad in zip(outer_left, outer_right):
        builder.block(
            left_quad,
            right_quad,
            (
                cells["downstreamOuterRadial"],
                cells["annularTangential"],
                axial,
            ),
        )
        builder.boundary["wall"].append(tuple(left_quad))
        builder.boundary["wall"].append(
            (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
        )
    outlet_quads = _quads(inner_end) + _quads(outer_end)[1:]
    builder.boundary["outlet"].extend(
        tuple(reversed(quad)) for quad in outlet_quads
    )
    return _render_block_mesh(builder)


def prepare_v2_case(case: Path, label: str, level: str, flow_scale: float) -> None:
    if case.exists() and any(case.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty case: {case}")
    scale = {"coarse": 1, "medium": 2, "fine": 4}[level]
    files = _case_files(label, scale, flow_scale, FdaNozzleDefinition())
    files["system/blockMeshDict"] = v2_block_mesh(level)
    files["system/fvSchemes"] = _formal_second_order(files["system/fvSchemes"])
    definition = json.loads(files["case-definition.json"])
    definition.update(
        {
            "schema": CASE_SCHEMA,
            "meshLevel": level,
            "expectedCells": EXPECTED_CELLS[level],
            "frozenContractSha256": FROZEN_CONTRACT_SHA256,
            "promotionAuthorized": False,
        }
    )
    files["case-definition.json"] = json.dumps(
        definition, indent=2, sort_keys=True
    ) + "\n"
    for relative, content in files.items():
        _write(case / relative, content)


def prepare_campaign(output: Path, preflight: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite campaign: {output}")
    contract_source = preflight / "v2-full-campaign-contract.json"
    assessment_source = preflight / "preflight-assessment.json"
    if _sha256(contract_source) != FROZEN_CONTRACT_SHA256:
        raise ValueError("frozen v2 full-campaign contract hash mismatch")
    if _sha256(assessment_source) != FROZEN_PREFLIGHT_ASSESSMENT_SHA256:
        raise ValueError("frozen v2 preflight assessment hash mismatch")
    contract = _read_json(contract_source)
    if not contract["authorization"]["mayPrepareAndRunFullCampaign"]:
        raise ValueError("frozen contract does not authorize campaign execution")

    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(contract_source, output / "campaign-contract.json")
    shutil.copy2(assessment_source, output / "preflight-assessment.json")
    shutil.copytree(preflight / "experiment", output / "experiment")
    (output / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        preflight / "bin" / "flowlabFdaPatchAudit",
        output / "bin" / "flowlabFdaPatchAudit",
    )

    case_rows: list[dict[str, Any]] = []
    for label, level, flow_scale in CASES:
        case = output / "cases" / label
        prepare_v2_case(case, label, level, flow_scale)
        case_rows.append(
            {
                "label": label,
                "meshLevel": level,
                "flowScale": flow_scale,
                "expectedCells": EXPECTED_CELLS[level],
                "caseDefinitionSha256": _sha256(case / "case-definition.json"),
                "blockMeshDictSha256": _sha256(case / "system" / "blockMeshDict"),
            }
        )
    manifest = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "createdAt": _now(),
        "status": "prepared-awaiting-mesh-preflight",
        "frozenContractSha256": _sha256(output / "campaign-contract.json"),
        "preflightAssessmentSha256": _sha256(output / "preflight-assessment.json"),
        "experimentSha256": _sha256(output / "experiment" / "experimental-data.json"),
        "auditBinarySha256": _sha256(output / "bin" / "flowlabFdaPatchAudit"),
        "image": DEFAULT_IMAGE,
        "imageDigest": DEFAULT_IMAGE_DIGEST,
        "cases": case_rows,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output / "campaign-manifest.json", manifest)
    return manifest


def mesh_preflight_case(
    output: Path, label: str, image: str = DEFAULT_IMAGE
) -> dict[str, Any]:
    if label not in LABELS:
        raise ValueError(f"unsupported v2 case: {label}")
    case = output / "cases" / label
    definition = _read_json(case / "case-definition.json")
    logs = output / "logs" / label
    workspace = Path(__file__).resolve().parents[2]
    codes: dict[str, int] = {}
    for name, command in (
        ("blockMesh", "blockMesh"),
        ("checkMesh", "checkMesh -allTopology -allGeometry"),
        ("writeCellCentres", "foamPostProcess -func writeCellCentres -time 0"),
    ):
        codes[name] = run_command(
            _container_command(image, workspace, case, command),
            case,
            logs / f"{name}.log",
        )
        if codes[name] != 0:
            break
    initialization: dict[str, Any] | None = None
    if all(codes.get(name) == 0 for name in ("blockMesh", "checkMesh", "writeCellCentres")):
        initialize_case(case, float(definition["flowScale"]))
        initialization = _read_json(case / "initialization.json")
    mesh = (
        _parse_check_mesh(logs / "checkMesh.log")
        if (logs / "checkMesh.log").exists()
        else {}
    )
    target = float(initialization["continuumTargetFlowM3PerS"]) if initialization else None
    normalized = float(initialization["normalizedDiscreteFlowM3PerS"]) if initialization else None
    exact_flux = bool(
        target is not None
        and normalized is not None
        and abs(normalized - target) <= 1.0e-14 * max(abs(target), 1.0e-300)
    )
    checks = {
        "commandsComplete": all(value == 0 for value in codes.values())
        and len(codes) == 3,
        "meshOk": bool(mesh.get("meshOk")),
        "strictAllHex": bool(mesh.get("strictAllHex")),
        "expectedCellCount": mesh.get("cells") == definition["expectedCells"],
        "exactDiscreteInletFlux": exact_flux,
        "fixedInletVelocity": "type fixedValue" in (case / "0" / "U").read_text(),
        "fixedFluxInletPressure": "inlet { type fixedFluxPressure" in (case / "0" / "p").read_text(),
        "fixedOutletPressure": "outlet { type fixedValue; value uniform 0; }" in (case / "0" / "p").read_text(),
    }
    report = {
        "schema": SCHEMA,
        "label": label,
        "checkedAt": _now(),
        "exitCodes": codes,
        "mesh": mesh,
        "initialization": initialization,
        "checks": checks,
        "passes": all(checks.values()),
        "promotionAuthorized": False,
    }
    _write_json(output / "results" / label / "mesh-preflight.json", report)
    return report


def mesh_preflight_all(
    output: Path, image: str = DEFAULT_IMAGE, max_workers: int = 3
) -> dict[str, Any]:
    reports = _parallel_map(
        LABELS,
        lambda label: mesh_preflight_case(output, label, image),
        max_workers=max_workers,
    )
    result = {
        "schema": SCHEMA,
        "checkedAt": _now(),
        "cases": reports,
        "passes": all(report["passes"] for report in reports.values()),
        "promotionAuthorized": False,
    }
    _write_json(output / "mesh-preflight.json", result)
    return result


def execute_solver_case(
    output: Path, label: str, image: str = DEFAULT_IMAGE
) -> dict[str, Any]:
    campaign_preflight = _read_json(output / "mesh-preflight.json")
    if not campaign_preflight["passes"]:
        raise ValueError("refusing solver execution because mesh preflight failed")
    case = output / "cases" / label
    definition = _read_json(case / "case-definition.json")
    workspace = Path(__file__).resolve().parents[2]
    code = run_command(
        _container_command(
            image, workspace, case, "foamRun -solver incompressibleFluid"
        ),
        case,
        output / "logs" / label / "foamRun.log",
    )
    latest = _latest_time(case) if code == 0 else None
    complete = code == 0 and latest is not None and float(latest) >= 800.0
    mesh_report = _read_json(output / "results" / label / "mesh-preflight.json")
    report = {
        "schema": CASE_SCHEMA,
        "label": label,
        "executedAt": _now(),
        "definition": definition,
        "exitCodes": {"foamRun": code},
        "mesh": mesh_report["mesh"],
        "latestTime": latest,
        "status": "solver-complete" if complete else "solver-failed",
        "promotionAuthorized": False,
    }
    _write_json(output / "results" / label / "execution.json", report)
    return report


def execute_all(
    output: Path, image: str = DEFAULT_IMAGE, max_workers: int = 2
) -> dict[str, dict[str, Any]]:
    executions = _parallel_map(
        LABELS,
        lambda label: execute_solver_case(output, label, image),
        max_workers=max_workers,
    )
    _write_json(output / "solver-execution.json", executions)
    return executions


def adopt_running_solver(
    output: Path, label: str, container_id: str
) -> dict[str, Any]:
    """Reattach evidence capture to an already-running immutable solver.

    This is used only when the launcher disappeared while Docker kept the
    original container alive.  It neither restarts nor modifies the solver.
    """
    if label not in LABELS:
        raise ValueError(f"unsupported v2 case: {label}")
    case = output / "cases" / label
    logs = output / "logs" / label
    logs.mkdir(parents=True, exist_ok=True)
    temporary_log = logs / "foamRun.reattached.log.tmp"
    with temporary_log.open("wb") as stream:
        capture = subprocess.run(
            ["docker", "logs", "--follow", container_id],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    temporary_log.replace(logs / "foamRun.log")
    text = (logs / "foamRun.log").read_text(encoding="utf-8", errors="replace")
    latest = _latest_time(case)
    complete = (
        capture.returncode == 0
        and float(latest) >= 800.0
        and "Time = 800s" in text
        and "\nEnd\n" in text
    )
    definition = _read_json(case / "case-definition.json")
    mesh_report = _read_json(output / "results" / label / "mesh-preflight.json")
    report = {
        "schema": CASE_SCHEMA,
        "label": label,
        "executedAt": _now(),
        "definition": definition,
        "exitCodes": {
            "foamRun": 0 if complete else 1,
            "dockerLogFollower": capture.returncode,
        },
        "adoptedExistingContainer": container_id,
        "mesh": mesh_report["mesh"],
        "latestTime": latest,
        "status": "solver-complete" if complete else "solver-failed",
        "promotionAuthorized": False,
    }
    _write_json(output / "results" / label / "execution.json", report)
    return report


def adopt_and_finish(
    output: Path,
    label: str,
    container_id: str,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    execution = adopt_running_solver(output, label, container_id)
    if execution["status"] != "solver-complete":
        result = {
            "status": "solver-gate-blocked",
            "failed": {label: execution},
            "promotionAuthorized": False,
        }
        _write_json(output / "finish-status.json", result)
        return result
    return finish_when_ready(output, image=image, poll_seconds=1.0)


def finalize_adopted_capture(
    output: Path,
    label: str,
    container_id: str,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    """Finish an adoption whose detached ``docker logs --follow`` is active."""
    wait = subprocess.run(
        ["docker", "wait", container_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    # Let the independent log follower flush and close the complete stream.
    time.sleep(2.0)
    logs = output / "logs" / label
    temporary_log = logs / "foamRun.reattached.log.tmp"
    if not temporary_log.is_file():
        raise ValueError("missing detached reattached solver log")
    temporary_log.replace(logs / "foamRun.log")
    text = (logs / "foamRun.log").read_text(encoding="utf-8", errors="replace")
    case = output / "cases" / label
    latest = _latest_time(case)
    complete = (
        wait.returncode == 0
        and wait.stdout.strip() == "0"
        and float(latest) >= 800.0
        and "Time = 800s" in text
        and "\nEnd\n" in text
    )
    definition = _read_json(case / "case-definition.json")
    mesh_report = _read_json(output / "results" / label / "mesh-preflight.json")
    execution = {
        "schema": CASE_SCHEMA,
        "label": label,
        "executedAt": _now(),
        "definition": definition,
        "exitCodes": {
            "foamRun": 0 if complete else 1,
            "dockerWait": wait.returncode,
        },
        "adoptedExistingContainer": container_id,
        "mesh": mesh_report["mesh"],
        "latestTime": latest,
        "status": "solver-complete" if complete else "solver-failed",
        "promotionAuthorized": False,
    }
    _write_json(output / "results" / label / "execution.json", execution)
    if not complete:
        result = {
            "status": "solver-gate-blocked",
            "failed": {label: execution},
            "promotionAuthorized": False,
        }
        _write_json(output / "finish-status.json", result)
        return result
    return finish_when_ready(output, image=image, poll_seconds=1.0)


def postprocess_v2_case(
    output: Path, label: str, image: str = DEFAULT_IMAGE
) -> dict[str, Any]:
    observation = postprocess_case(output, label, image)
    if observation.get("status") != "observed":
        return {"status": "postprocessing-failed", "observation": observation}
    experiment = _read_json(output / "experiment" / "experimental-data.json")
    summary = experimental_summary(experiment)
    result = output / "results" / label
    piv = run_piv_observation(
        case=output / "cases" / label,
        result=result,
        summary=summary,
        workspace=Path(__file__).resolve().parents[2],
        log=output / "logs" / label / "fdaPivProbes.log",
        image=image,
    )
    pressure = pressure_diagnostic(experiment, observation)
    velocity = velocity_diagnostic(piv) if piv.get("status") == "observed" else {}
    _write_json(result / "pressure-diagnostic.json", pressure)
    _write_json(result / "velocity-diagnostic.json", velocity)
    report = {
        "schema": SCHEMA,
        "label": label,
        "status": "observed" if piv.get("status") == "observed" else "postprocessing-failed",
        "observationComplete": observation.get("status") == "observed",
        "pivObservationComplete": piv.get("status") == "observed",
        "promotionAuthorized": False,
    }
    _write_json(result / "v2-postprocessing.json", report)
    return report


def postprocess_all(
    output: Path, image: str = DEFAULT_IMAGE, max_workers: int = 2
) -> dict[str, dict[str, Any]]:
    executions = _read_json(output / "solver-execution.json")
    completed = [
        label
        for label in LABELS
        if executions.get(label, {}).get("status") == "solver-complete"
    ]
    reports = _parallel_map(
        completed,
        lambda label: postprocess_v2_case(output, label, image),
        max_workers=max_workers,
    )
    _write_json(output / "postprocessing.json", reports)
    return reports


def finish_when_ready(
    output: Path,
    image: str = DEFAULT_IMAGE,
    poll_seconds: float = 30.0,
) -> dict[str, Any]:
    """Wait for solver lanes, then postprocess and assess without weakening gates."""
    while True:
        executions: dict[str, dict[str, Any]] = {}
        pending = False
        for label in LABELS:
            path = output / "results" / label / "execution.json"
            if not path.is_file():
                pending = True
                continue
            executions[label] = _read_json(path)
        failed = {
            label: report
            for label, report in executions.items()
            if report.get("status") != "solver-complete"
        }
        if failed:
            result = {
                "status": "solver-gate-blocked",
                "failed": failed,
                "promotionAuthorized": False,
            }
            _write_json(output / "finish-status.json", result)
            return result
        if not pending and len(executions) == len(LABELS):
            break
        time.sleep(max(1.0, poll_seconds))

    _write_json(output / "solver-execution.json", executions)
    postprocessing: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        existing = output / "results" / label / "v2-postprocessing.json"
        if existing.is_file():
            report = _read_json(existing)
            if report.get("status") == "observed":
                postprocessing[label] = report
                continue
        postprocessing[label] = postprocess_v2_case(output, label, image)
        if postprocessing[label].get("status") != "observed":
            result = {
                "status": "postprocessing-gate-blocked",
                "case": label,
                "report": postprocessing[label],
                "promotionAuthorized": False,
            }
            _write_json(output / "finish-status.json", result)
            return result
    _write_json(output / "postprocessing.json", postprocessing)
    result = assess_campaign(output)
    _write_json(output / "finish-status.json", result)
    return result


def _parallel_map(
    labels: Iterable[str], function: Callable[[str], dict[str, Any]], *, max_workers: int
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(function, label): label for label in labels}
        for future in as_completed(futures):
            label = futures[future]
            try:
                results[label] = future.result()
            except Exception as error:  # evidence must survive one failed lane
                results[label] = {
                    "status": "exception",
                    "error": f"{type(error).__name__}: {error}",
                    "promotionAuthorized": False,
                }
    return {label: results[label] for label in labels}


def _with_operator_uncertainty(
    validation: dict[str, Any], operator: float
) -> dict[str, Any]:
    validation["uncertainty"]["observationOperator"] = operator
    if validation["qualified"]:
        total = math.sqrt(float(validation["validationUncertainty"]) ** 2 + operator**2)
        error = float(validation["comparisonError"])
        validation["validationUncertainty"] = total
        validation["errorToValidationUncertaintyRatio"] = abs(error) / max(total, 1.0e-300)
        validation["passesVv20"] = abs(error) <= total
    return validation


def _spatial_validation(
    observations: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    indices = {
        label: {
            time: {row["recordId"]: row for row in observation["times"][time]}
            for time in ("750", "800")
        }
        for label, observation in observations.items()
    }
    rows: list[dict[str, Any]] = []
    for record_id, fine in indices["fine"]["800"].items():
        experimental = fine["experimental"]
        eligible = (
            fine["supportValid"]
            and int(experimental.get("n", 0)) >= 3
            and experimental.get("u95") is not None
        )
        base = {key: fine[key] for key in ("recordId", "qoi", "supportValid")}
        for key in ("stationM", "radialCoordinateM", "coordinateM"):
            if key in fine:
                base[key] = fine[key]
        if not eligible:
            rows.append(
                {
                    **base,
                    "experiment": experimental,
                    "experimentalEligible": False,
                    "qualified": False,
                    "passesVv20": False,
                }
            )
            continue
        component = 1 if fine["qoi"] == "radialVelocityProfile" else 0

        def value(label: str, time: str) -> float:
            return float(indices[label][time][record_id]["pooledVelocityMPerS"][component])

        validation = _validation_row(
            experimental=experimental,
            coarse=value("coarse", "800"),
            medium=value("medium", "800"),
            fine=value("fine", "800"),
            fine_previous=value("fine", "750"),
            input_minus=value("input-minus-5pct", "800"),
            input_plus=value("input-plus-5pct", "800"),
        )
        operator = float(fine["operatorHalfRangeMPerS"][component])
        rows.append({**base, **_with_operator_uncertainty(validation, operator)})

    axial = [row for row in rows if row["qoi"] == "axialVelocityProfile"]
    radial = [row for row in rows if row["qoi"] == "radialVelocityProfile"]
    centreline = [row for row in rows if row["qoi"] == "centrelineAxialVelocity"]
    stations: dict[str, Any] = {}
    for station in PRIMARY_PROFILE_STATIONS_M:
        selected = [row for row in axial if float(row["stationM"]) == station]
        eligible = [row for row in selected if row["experimentalEligible"]]
        peak = max((abs(float(row["experiment"]["mean"])) for row in eligible), default=0.0)
        nrmse = (
            math.sqrt(
                statistics.fmean(
                    (float(row["simulation"]["fine"]) - float(row["experiment"]["mean"])) ** 2
                    for row in eligible
                )
            )
            / peak
            if eligible and peak > 0.0
            else None
        )
        stations[f"{station:.6f}"] = {
            "counts": _summary_counts(selected),
            "normalizedRmseByExperimentalPeak": nrmse,
        }
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-spatial-validation.v1",
        "axialProfiles": {"counts": _summary_counts(axial), "stations": stations},
        "radialProfiles": {"counts": _summary_counts(radial)},
        "centreline": {"counts": _summary_counts(centreline)},
        "rows": rows,
    }


def _pressure_validation(
    diagnostics: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "flowlab.fda-nozzle-re500-v2-pressure-validation.v1"
    }
    for kind in ("wall", "centreline"):
        result[kind] = {}
        for group in ("adjacent", "named"):
            indices = {
                label: {row["name"]: row for row in diagnostic[kind][group]["rows"]}
                for label, diagnostic in diagnostics.items()
            }
            rows: list[dict[str, Any]] = []
            for name, fine in indices["fine"].items():
                validation = _validation_row(
                    experimental=fine["experiment"],
                    coarse=float(indices["coarse"][name]["simulationPa"]),
                    medium=float(indices["medium"][name]["simulationPa"]),
                    fine=float(fine["simulationPa"]),
                    fine_previous=float(fine["simulationPreviousPa"]),
                    input_minus=float(indices["input-minus-5pct"][name]["simulationPa"]),
                    input_plus=float(indices["input-plus-5pct"][name]["simulationPa"]),
                )
                rows.append(
                    {
                        "name": name,
                        "leftM": fine["leftM"],
                        "rightM": fine["rightM"],
                        **validation,
                    }
                )
            result[kind][group] = {"rows": rows, "counts": _summary_counts(rows)}
    return result


def _traction_validation(
    observations: dict[str, dict[str, Any]], summary: dict[str, Any]
) -> dict[str, Any]:
    indices = {
        label: {
            time: {
                round(float(row["requestedCoordinateM"]), 12): row
                for row in observation["times"][time]["wallSamples"]
            }
            for time in ("750", "800")
        }
        for label, observation in observations.items()
    }
    rows: list[dict[str, Any]] = []
    for experimental in summary["wallShearLegacy"]:
        coordinate = round(float(experimental["coordinateM"]), 12)

        def value(label: str, time: str) -> float:
            return float(indices[label][time][coordinate]["tangentialTractionPa"])

        rows.append(
            {
                "coordinateM": coordinate,
                **_validation_row(
                    experimental=experimental,
                    coarse=value("coarse", "800"),
                    medium=value("medium", "800"),
                    fine=value("fine", "800"),
                    fine_previous=value("fine", "750"),
                    input_minus=value("input-minus-5pct", "800"),
                    input_plus=value("input-plus-5pct", "800"),
                ),
            }
        )
    return {
        "role": "mandatory-reporting-nonpromotional",
        "counts": _summary_counts(rows),
        "points": rows,
    }


def _outlet_traction_audit(output: Path) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for label in NOMINAL_LABELS:
        cases[label] = {}
        for time in ("750", "800"):
            path = output / "results" / label / f"face-integration-{time}.csv"
            with path.open(newline="", encoding="utf-8") as stream:
                rows = [row for row in csv.DictReader(stream) if row["patch"] == "outlet"]
            area = sum(float(row["area"]) for row in rows)
            traction_magnitudes = [
                math.sqrt(sum(float(row[f"traction_{axis}_pa"]) ** 2 for axis in "xyz"))
                for row in rows
            ]
            viscous_force = [
                sum(float(row[f"viscous_force_{axis}_n"]) for row in rows)
                for axis in "xyz"
            ]
            cases[label][time] = {
                "faceCount": len(rows),
                "areaM2": area,
                "maxAbsNormalVelocityGradientPerS": max(
                    abs(float(row["sn_grad_normal_velocity"])) for row in rows
                ),
                "areaMeanAbsNormalVelocityGradientPerS": sum(
                    float(row["area"]) * abs(float(row["sn_grad_normal_velocity"]))
                    for row in rows
                ) / area,
                "maxViscousTractionPa": max(traction_magnitudes),
                "areaMeanViscousTractionPa": sum(
                    float(row["area"]) * magnitude
                    for row, magnitude in zip(rows, traction_magnitudes)
                ) / area,
                "directViscousForceN": viscous_force,
                "analyticZeroViscousTractionPa": 0.0,
                "analyticZeroNormalVelocityGradientPerS": 0.0,
            }
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-outlet-traction-audit.v1",
        "status": "complete",
        "interpretation": "Numerical outlet fields are reported face-by-face against the analytic zero-gradient/zero-viscous-traction boundary state; no tolerance is fitted from these results.",
        "cases": cases,
    }


def _all_finite_uncertainties(rows: Sequence[dict[str, Any]]) -> bool:
    for row in rows:
        if not row.get("experimentalEligible"):
            continue
        for value in row.get("uncertainty", {}).values():
            if value is not None and not math.isfinite(float(value)):
                return False
    return True


def assess_campaign(output: Path) -> dict[str, Any]:
    if _sha256(output / "campaign-contract.json") != FROZEN_CONTRACT_SHA256:
        raise ValueError("campaign contract changed after preparation")
    campaign_manifest = _read_json(output / "campaign-manifest.json")
    observations = {
        label: _read_json(output / "results" / label / "observation.json")
        for label in LABELS
    }
    piv = {
        label: _read_json(output / "results" / label / "piv-observation.json")
        for label in LABELS
    }
    experiment = _read_json(output / "experiment" / "experimental-data.json")
    summary = experimental_summary(experiment)
    spatial = _spatial_validation(piv)
    pressure_diagnostics = {
        label: pressure_diagnostic(experiment, observation)
        for label, observation in observations.items()
    }
    pressure = _pressure_validation(pressure_diagnostics)
    traction = _traction_validation(observations, summary)
    outlet_audit = _outlet_traction_audit(output)
    _write_json(output / "spatial-validation.json", spatial)
    _write_json(output / "pressure-validation.json", pressure)
    _write_json(output / "outlet-traction-audit.json", outlet_audit)

    axial = spatial["axialProfiles"]
    centreline = spatial["centreline"]
    adjacent = pressure["wall"]["adjacent"]
    named = {row["name"]: row for row in pressure["wall"]["named"]["rows"]}
    overall_drop = named["overall-pressure-drop"]
    mandatory_rows = (
        [row for row in spatial["rows"] if row["qoi"] in {"axialVelocityProfile", "centrelineAxialVelocity"}]
        + adjacent["rows"]
        + [overall_drop]
    )
    mandatory_eligible = [row for row in mandatory_rows if row["experimentalEligible"]]
    qualified_with_total = [
        row
        for row in mandatory_eligible
        if row.get("qualified") and row.get("validationUncertainty") is not None
    ]
    iterative_health = {
        "hardThresholdProspectivelyDeclared": False,
        "gateBehavior": "reported separately and included in U_val; it cannot be hidden or omitted, but no post-hoc stationarity threshold is introduced",
        "maximumFractionOfValidationUncertainty": max(
            (
                float(row["uncertainty"]["iterative"])
                / max(float(row["validationUncertainty"]), 1.0e-300)
                for row in qualified_with_total
            ),
            default=None,
        ),
        "maximumAbsoluteIterativeUncertainty": max(
            (float(row["uncertainty"]["iterative"]) for row in mandatory_eligible),
            default=None,
        ),
    }
    solver_diagnostics = {
        label: _solver_diagnostic(output / "logs" / label / "foamRun.log")
        for label in LABELS
    }
    station_nrmse = [
        row["normalizedRmseByExperimentalPeak"]
        for row in axial["stations"].values()
    ]
    all_observed = all(observation.get("status") == "observed" for observation in observations.values())
    mesh_preflight = _read_json(output / "mesh-preflight.json")
    gates = {
        "frozenContractIntegrity": True,
        "sourcePinned": _sha256(output / "experiment" / "raw" / "SE_exp_0500.zip")
        == OFFICIAL_ARCHIVE_SHA256,
        "allMeshesStrictHexAndCheckMesh": all(
            mesh_preflight["cases"][label]["passes"] for label in NOMINAL_LABELS
        ),
        "allNominalSolversComplete": all(
            observations[label].get("status") == "observed" for label in NOMINAL_LABELS
        ),
        "inputUncertaintyResolved": all_observed,
        "iterativeUncertaintyResolved": all(
            all(time in observations[label].get("times", {}) for time in ("750", "800"))
            and all(time in piv[label].get("times", {}) for time in ("750", "800"))
            for label in LABELS
        ),
        "threeGridGciResolved": bool(mandatory_eligible)
        and all(row["grid"]["qualified"] for row in mandatory_eligible),
        "pivObservationOperatorComplete": all(
            piv[label].get("status") == "observed" for label in LABELS
        ),
        "offsetFreePressureComplete": len(adjacent["rows"]) == 16
        and len(pressure["wall"]["named"]["rows"]) == 4,
        "uncertaintyComponentsFinite": _all_finite_uncertainties(mandatory_rows),
        "axialVelocityValidation": axial["counts"]["vv20PassFraction"] >= 0.90
        and centreline["counts"]["vv20PassFraction"] >= 0.90
        and all(value is not None and value <= 0.10 for value in station_nrmse),
        "pressureValidation": adjacent["counts"]["vv20PassFraction"] >= 0.90
        and overall_drop["passesVv20"],
        "flowConservation": all(
            observations[label]["checks"]["flowConservation"] for label in NOMINAL_LABELS
        ),
        "forceReconciliation": all(
            observations[label]["checks"]["forceObjectMatchesDirect"] for label in NOMINAL_LABELS
        ),
        "tractionAuditComplete": outlet_audit["status"] == "complete",
    }
    promotion = all(gates.values())
    assessment = {
        "schema": "flowlab.fda-nozzle-re500-v2-full-assessment.v1",
        "campaignId": campaign_manifest.get("campaignId", CAMPAIGN_ID),
        "assessedAt": _now(),
        "status": "validated-passed" if promotion else "validated-blocked",
        "claim": "FlowLab's laminar OpenFOAM execution has passed an independent experimental CFD benchmark.",
        "scientificPromotionAuthorized": promotion,
        "desktopPromotionEligible": promotion,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": promotion,
        "gates": gates,
        "meshCellCounts": {
            label: mesh_preflight["cases"][label]["mesh"]["cells"]
            for label in NOMINAL_LABELS
        },
        "comparisons": {
            "axialVelocityProfiles": axial,
            "radialVelocityProfiles": {
                "role": "mandatory-reporting-nonpromotional",
                **spatial["radialProfiles"],
            },
            "centrelineAxialVelocity": centreline,
            "wallOffsetFreePressureAdjacent": adjacent,
            "wallOffsetFreePressureNamed": pressure["wall"]["named"],
            "centrelineOffsetFreePressure": {
                "role": "mandatory-reporting-supporting",
                "adjacent": pressure["centreline"]["adjacent"],
                "named": pressure["centreline"]["named"],
            },
            "wallShearViscousTraction": traction,
            "outletNormalGradientAndTraction": outlet_audit,
            "flowConservation": {
                label: observations[label]["times"]["800"]["flow"]
                for label in NOMINAL_LABELS
            },
            "forceObjectVsDirectFaceIntegration": {
                label: {
                    "absoluteN": observations[label]["times"]["800"]["forceObjectVsDirectAbsoluteN"],
                    "relative": observations[label]["times"]["800"]["forceObjectVsDirectRelative"],
                    "openFoam": observations[label]["times"]["800"]["openFoamForces"],
                    "direct": observations[label]["times"]["800"]["directFaceIntegration"]["all"],
                }
                for label in NOMINAL_LABELS
            },
        },
        "mandatoryGci": {
            "eligible": len(mandatory_eligible),
            "qualified": sum(row["grid"]["qualified"] for row in mandatory_eligible),
        },
        "iterativeHealth": iterative_health,
        "solverDiagnostics": solver_diagnostics,
        "uncertaintyMethod": {
            "experimental": "pointwise paired/trial 95% Student-t",
            "input": "half range of medium-grid Q +/-5%",
            "iterative": "absolute fine-grid change from iteration 750 to 800",
            "grid": "three-grid observed order and fine-grid GCI with Fs=1.25",
            "observationOperator": "half range of three source-backed PIV windows",
            "validation": "root-sum-square; ASME V&V 20 passes when |E| <= U_val",
        },
        "provenance": {
            "officialRepository": OFFICIAL_REPOSITORY,
            "officialCommit": OFFICIAL_COMMIT,
            "officialDatasetDoi": OFFICIAL_DATASET_DOI,
            "primaryPaperDoi": PRIMARY_PAPER_DOI,
            "wallShearUqPaperDoi": WSS_UQ_PAPER_DOI,
            "openFoamImage": DEFAULT_IMAGE,
            "openFoamImageDigest": DEFAULT_IMAGE_DIGEST,
        },
        "evidenceHashesSha256": {
            "campaignContract": _sha256(output / "campaign-contract.json"),
            "assessmentImplementation": _sha256(Path(__file__).resolve()),
            "directIntegrationSource": _sha256(
                Path(__file__).resolve().parents[2]
                / "benchmarks/tools/flowlabFdaPatchAudit/flowlabFdaPatchAudit.C"
            ),
            "observations": {
                label: _sha256(output / "results" / label / "observation.json")
                for label in LABELS
            },
            "pivObservations": {
                label: _sha256(output / "results" / label / "piv-observation.json")
                for label in LABELS
            },
        },
    }
    recovery_manifest_path = output / "recovery-preflight.json"
    if recovery_manifest_path.is_file():
        recovery_preflight = _read_json(recovery_manifest_path)
        recovery_launch = _read_json(output / "recovery-launch.json")
        fine_execution = _read_json(output / "results" / "fine" / "execution.json")
        assessment["provenance"]["recovery"] = {
            "schema": RECOVERY_SCHEMA,
            "sourceCampaignId": campaign_manifest["recovery"]["sourceCampaignId"],
            "sourceCheckpoint": RECOVERY_START_TIME,
            "targetEndTime": RECOVERY_END_TIME,
            "allowedMutation": recovery_preflight["allowedMutation"],
            "containerName": recovery_launch["containerName"],
            "containerId": recovery_launch["containerId"],
            "containerAutoRemove": False,
            "containerState": fine_execution["containerState"],
        }
        assessment["evidenceHashesSha256"]["recovery"] = {
            "contract": _sha256(output / "recovery-contract.json"),
            "preflight": _sha256(recovery_manifest_path),
            "launch": _sha256(output / "recovery-launch.json"),
            "sourceSnapshot": recovery_preflight["sourceSnapshotSha256"],
            "recoverySnapshot": recovery_preflight["recoverySnapshotSha256"],
            "solverLog": fine_execution["solverLogSha256"],
            "execution": _sha256(output / "results" / "fine" / "execution.json"),
        }
    _write_json(output / "assessment.json", assessment)
    issues: list[dict[str, Any]] = []
    for name, passed in gates.items():
        if not passed:
            issues.append(
                {
                    "id": f"gate-{name}",
                    "severity": "scientific-promotion-blocker",
                    "source": "frozen full-v2 assessment",
                    "issue": f"mandatory gate {name} failed",
                    "interference": "blocks the independent experimental benchmark claim and desktop eligibility",
                }
            )
    for label, diagnostic in solver_diagnostics.items():
        if diagnostic["cappedPressureSolveCount"]:
            issues.append(
                {
                    "id": f"pressure-conditioning-{label}",
                    "severity": (
                        "reported-startup-conditioning"
                        if diagnostic["finalIterationMeetsConfiguredLinearTolerance"]
                        else "iterative-risk"
                    ),
                    "source": "retained OpenFOAM solver log",
                    "issue": "one or more pressure solves reached the configured 1000-iteration cap",
                    "evidence": diagnostic,
                    "interference": "can contaminate iterative uncertainty if it persists to the final retained state",
                }
            )
    _write(
        output / "issues.jsonl",
        "\n".join(json.dumps(issue, sort_keys=True) for issue in issues)
        + ("\n" if issues else ""),
    )
    report = [
        "# FDA nozzle Re=500 v2 full validation",
        "",
        f"Status: **{assessment['status']}**",
        "",
        f"Scientific promotion: **{'AUTHORIZED' if promotion else 'BLOCKED'}**",
        "Desktop UI promotion remains a separate desktop-only QA action.",
        "",
        "## Gate summary",
        "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in gates.items()],
        "",
        "## Primary results",
        "",
        f"- Axial profile V&V 20: {axial['counts']['vv20Passed']}/{axial['counts']['experimentalEligible']}",
        f"- Centreline V&V 20: {centreline['counts']['vv20Passed']}/{centreline['counts']['experimentalEligible']}",
        f"- Adjacent pressure differences: {adjacent['counts']['vv20Passed']}/{adjacent['counts']['experimentalEligible']}",
        f"- Overall pressure drop: {'PASS' if overall_drop['passesVv20'] else 'FAIL'}",
        f"- Mandatory GCI: {assessment['mandatoryGci']['qualified']}/{assessment['mandatoryGci']['eligible']}",
        "",
        "Machine-readable comparison errors and every uncertainty component are in `assessment.json`.",
        "",
    ]
    _write(output / "REPORT.md", "\n".join(report))
    campaign_manifest.update(
        {
            "status": assessment["status"],
            "assessedAt": assessment["assessedAt"],
            "assessmentSha256": _sha256(output / "assessment.json"),
            "scientificPromotionAuthorized": promotion,
            "desktopPromotionAuthorized": False,
            "promotionAuthorized": promotion,
        }
    )
    _write_json(output / "campaign-manifest.json", campaign_manifest)
    return assessment


def run_campaign(
    output: Path,
    preflight: Path,
    image: str = DEFAULT_IMAGE,
    max_workers: int = 2,
) -> dict[str, Any]:
    prepare_campaign(output, preflight)
    mesh = mesh_preflight_all(output, image, max_workers=min(3, max_workers + 1))
    if not mesh["passes"]:
        return {"status": "mesh-preflight-blocked", "meshPreflight": mesh}
    executions = execute_all(output, image, max_workers)
    completed = [
        label for label, report in executions.items() if report.get("status") == "solver-complete"
    ]
    postprocessing = postprocess_all(output, image, max_workers)
    if len(completed) != len(LABELS) or not all(
        postprocessing[label].get("status") == "observed" for label in completed
    ):
        return {
            "status": "execution-blocked",
            "executions": executions,
            "postprocessing": postprocessing,
            "promotionAuthorized": False,
        }
    return assess_campaign(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--preflight", type=Path, required=True)
    mesh = sub.add_parser("mesh-preflight")
    mesh.add_argument("--output", type=Path, required=True)
    mesh.add_argument("--image", default=DEFAULT_IMAGE)
    mesh.add_argument("--max-workers", type=int, default=3)
    execute = sub.add_parser("execute-case")
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--label", required=True)
    execute.add_argument("--image", default=DEFAULT_IMAGE)
    execute_all_parser = sub.add_parser("execute-all")
    execute_all_parser.add_argument("--output", type=Path, required=True)
    execute_all_parser.add_argument("--image", default=DEFAULT_IMAGE)
    execute_all_parser.add_argument("--max-workers", type=int, default=2)
    prepare_recovery = sub.add_parser("prepare-fine-recovery")
    prepare_recovery.add_argument("--source", type=Path, required=True)
    prepare_recovery.add_argument("--output", type=Path, required=True)
    prepare_recovery.add_argument("--contract", type=Path, required=True)
    launch_recovery = sub.add_parser("launch-fine-recovery")
    launch_recovery.add_argument("--output", type=Path, required=True)
    launch_recovery.add_argument("--container", required=True)
    launch_recovery.add_argument("--image", default=DEFAULT_IMAGE)
    finalize_recovery = sub.add_parser("finalize-fine-recovery")
    finalize_recovery.add_argument("--output", type=Path, required=True)
    finalize_recovery.add_argument("--container", required=True)
    finalize_recovery.add_argument("--image", default=DEFAULT_IMAGE)
    run_recovery = sub.add_parser("run-fine-recovery")
    run_recovery.add_argument("--output", type=Path, required=True)
    run_recovery.add_argument("--container", required=True)
    run_recovery.add_argument("--image", default=DEFAULT_IMAGE)
    adopt = sub.add_parser("adopt-and-finish")
    adopt.add_argument("--output", type=Path, required=True)
    adopt.add_argument("--label", required=True)
    adopt.add_argument("--container", required=True)
    adopt.add_argument("--image", default=DEFAULT_IMAGE)
    finalize_adopt = sub.add_parser("finalize-adopted-capture")
    finalize_adopt.add_argument("--output", type=Path, required=True)
    finalize_adopt.add_argument("--label", required=True)
    finalize_adopt.add_argument("--container", required=True)
    finalize_adopt.add_argument("--image", default=DEFAULT_IMAGE)
    post = sub.add_parser("postprocess-case")
    post.add_argument("--output", type=Path, required=True)
    post.add_argument("--label", required=True)
    post.add_argument("--image", default=DEFAULT_IMAGE)
    post_all_parser = sub.add_parser("postprocess-all")
    post_all_parser.add_argument("--output", type=Path, required=True)
    post_all_parser.add_argument("--image", default=DEFAULT_IMAGE)
    post_all_parser.add_argument("--max-workers", type=int, default=2)
    assess = sub.add_parser("assess")
    assess.add_argument("--output", type=Path, required=True)
    finish = sub.add_parser("finish-when-ready")
    finish.add_argument("--output", type=Path, required=True)
    finish.add_argument("--image", default=DEFAULT_IMAGE)
    finish.add_argument("--poll-seconds", type=float, default=30.0)
    run = sub.add_parser("run")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--preflight", type=Path, required=True)
    run.add_argument("--image", default=DEFAULT_IMAGE)
    run.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_campaign(args.output.resolve(), args.preflight.resolve())
    elif args.command == "mesh-preflight":
        result = mesh_preflight_all(args.output.resolve(), args.image, args.max_workers)
    elif args.command == "execute-case":
        result = execute_solver_case(args.output.resolve(), args.label, args.image)
    elif args.command == "execute-all":
        result = execute_all(args.output.resolve(), args.image, args.max_workers)
    elif args.command == "prepare-fine-recovery":
        result = prepare_fine_recovery(
            args.source.resolve(), args.output.resolve(), args.contract.resolve()
        )
    elif args.command == "launch-fine-recovery":
        result = launch_fine_recovery(
            args.output.resolve(), args.container, args.image
        )
    elif args.command == "finalize-fine-recovery":
        result = finalize_fine_recovery(
            args.output.resolve(), args.container, args.image
        )
    elif args.command == "run-fine-recovery":
        result = run_fine_recovery(
            args.output.resolve(), args.container, args.image
        )
    elif args.command == "adopt-and-finish":
        result = adopt_and_finish(
            args.output.resolve(), args.label, args.container, args.image
        )
    elif args.command == "finalize-adopted-capture":
        result = finalize_adopted_capture(
            args.output.resolve(), args.label, args.container, args.image
        )
    elif args.command == "postprocess-case":
        result = postprocess_v2_case(args.output.resolve(), args.label, args.image)
    elif args.command == "postprocess-all":
        result = postprocess_all(args.output.resolve(), args.image, args.max_workers)
    elif args.command == "assess":
        result = assess_campaign(args.output.resolve())
    elif args.command == "finish-when-ready":
        result = finish_when_ready(
            args.output.resolve(), args.image, args.poll_seconds
        )
    else:
        result = run_campaign(
            args.output.resolve(),
            args.preflight.resolve(),
            args.image,
            args.max_workers,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    success = result.get("status") in {
        "prepared-awaiting-mesh-preflight",
        "fine-recovery-prepared-awaiting-execution",
        "fine-recovery-running",
        "solver-complete",
        "observed",
        "validated-passed",
    } or bool(result.get("passes"))
    if args.command == "execute-all":
        success = all(row.get("status") == "solver-complete" for row in result.values())
    if args.command == "postprocess-all":
        success = len(result) == len(LABELS) and all(
            row.get("status") == "observed" for row in result.values()
        )
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
