"""Hardened, nonpromotional Hugging Face qualification for FDA Re=500."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import tarfile
from typing import Any

from . import fda_nozzle_re500_hf_qualification as r1
from .fda_nozzle_re500 import (
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_DIGEST,
    _container_command,
    _sha256,
    _write_json,
    initialize_case,
    run_command,
)
from .fda_nozzle_re500_v3_mesh_preflight import EXPECTED_CELLS


SCHEMA = "flowlab.fda-nozzle-re500-hf-infrastructure-qualification.v2"
ASSESSMENT_SCHEMA = "flowlab.fda-nozzle-re500-hf-infrastructure-assessment.v2"
RECOVERY_SCHEMA = "flowlab.fda-nozzle-re500-hf-lane-recovery.v2"
RECORD_RECOVERY_SCHEMA = "flowlab.fda-nozzle-re500-hf-record-recovery.v2"
CONTRACT_ID = "fda-nozzle-re500-v3-hf-infrastructure-qualification-r2-2026-07-20"
ARTIFACT_REPO = "Anieyrudh/flowlab-fda-hf-qualification-20260720"
SPACE_ID = "Anieyrudh/flowlab-openfoam11-gmsh415-amd64-r2"
OUTPUT_DIR = "benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-hf-infrastructure-qualification-r2"
BASE_DIGEST = "sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3"
GMSH_SHA256 = "2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db"
LOCAL_OCI_DIGEST = "sha256:6b95dec7192e5f843f888ac5c27ea474d4b0a07d8bb569c37686840e5dab45e6"
PROBE_PAYLOAD = "flowlab-hf-volume-probe-v2\n"
PROBE_SHA256 = hashlib.sha256(PROBE_PAYLOAD.encode()).hexdigest()
EXPECTED_POLICY_SHA256 = "9e7110f982d7a477315d000dc3990493639db70b3429414732eedaf276d2370d"
EXPECTED_SOURCE_PATHS = {
    "server/flowlab/fda_nozzle_re500_hf_qualification.py",
    "server/flowlab/fda_nozzle_re500_hf_qualification_r2.py",
    "server/flowlab/hf_jobs/fda_nozzle_re500_probe_r2.py",
    "server/flowlab/hf_jobs/fda_nozzle_re500_volume_probe_r2.py",
    "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/Dockerfile",
    "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/remote_runner.py",
    "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/SPACE_README.md",
}
HEX64 = set("0123456789abcdef")
REQUIRED_LANE_CHECKS = {
    "commandsComplete", "expectedArchitecture", "openFoam11", "gmsh4152",
    "meshOk", "strictAllHex", "expectedCells", "time800", "terminalEnd",
    "completeFinalResidualSet", "finalResidual", "continuity",
    "finalFieldsParsed", "telemetryPresent", "completeMemoryTelemetry",
    "noOomEvents",
}


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_new(path: Path, description: str) -> None:
    r1.require_new(path, description)


def nested(value: dict[str, Any], dotted: str) -> Any:
    current: Any = value
    for key in dotted.split("."):
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing frozen contract field: {dotted}")
        current = current[key]
    return current


def sha256_text(value: str) -> bool:
    return len(value) == 64 and set(value) <= HEX64


def digest_text(value: str) -> bool:
    return value.startswith("sha256:") and sha256_text(value[7:])


def commit_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) in {40, 64} and set(value.lower()) <= HEX64


def source_sha(contract: dict[str, Any], path: str) -> str:
    matches = [row["sha256"] for row in contract["sourceEvidence"] if row["path"] == path]
    if len(matches) != 1:
        raise ValueError(f"HF r2 source identity missing: {path}")
    return matches[0]


def verify_contract(path: Path) -> dict[str, Any]:
    contract = read_json(path)
    policy = {key: value for key, value in contract.items() if key != "sourceEvidence"}
    policy_bytes = json.dumps(
        policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if hashlib.sha256(policy_bytes).hexdigest() != EXPECTED_POLICY_SHA256:
        raise ValueError("frozen HF r2 policy hash mismatch")
    expected = {
        "schema": SCHEMA,
        "contractId": CONTRACT_ID,
        "status": "frozen-nonpromotional-qualification-authorized",
        "predecessors.pressureDisposition.path": "docs/validation/fda-nozzle-re500/PRESSURE_REFERENCE_DISPOSITION.json",
        "predecessors.pressureDisposition.sha256": r1.EXPECTED_PRESSURE_SHA256,
        "predecessors.meshPreflightAssessment.path": "docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-mesh-preflight-r2.json",
        "predecessors.meshPreflightAssessment.sha256": r1.EXPECTED_MESH_SHA256,
        "probe.flavor": "cpu-basic",
        "probe.artifactRepository": ARTIFACT_REPO,
        "probe.payload": PROBE_PAYLOAD,
        "probe.payloadSha256": PROBE_SHA256,
        "amd64Image.spaceId": SPACE_ID,
        "amd64Image.expectedArchitecture": "amd64",
        "amd64Image.openFoamVersion": "11",
        "amd64Image.openFoamBaseDigest": BASE_DIGEST,
        "amd64Image.gmshVersion": "4.15.2",
        "amd64Image.gmshArchiveSha256": GMSH_SHA256,
        "amd64Image.localOciDigest": LOCAL_OCI_DIGEST,
        "coarsePilot.expectedCells": 44256,
        "coarsePilot.requiredFinalTime": 800,
        "coarsePilot.maximumFinalLinearResidual": 1.0e-10,
        "coarsePilot.maximumAbsoluteContinuitySumLocal": 1.0e-9,
        "coarsePilot.maximumCrossFlavorRelativeDifference": 1.0e-10,
        "coarsePilot.oneSerialSolverPerJob": True,
        "artifacts.localQualificationDirectory": OUTPUT_DIR,
        "artifacts.remoteRepository": ARTIFACT_REPO,
        "artifacts.remoteOverwriteAllowed": False,
        "artifacts.localOverwriteAllowed": False,
        "futureConcurrencyDesign.maximumConcurrentPhase1Jobs": 2,
        "futureConcurrencyDesign.maximumConcurrentPhase2Jobs": 4,
        "futureConcurrencyDesign.automaticProgressionBetweenPhases": False,
        "authorization.runArchitectureProbe": True,
        "authorization.runRevisionPinnedVolumeProbe": True,
        "authorization.buildAmd64Image": True,
        "authorization.runCpuUpgradeCoarsePilot": True,
        "authorization.runCpuXlCoarseComparison": True,
        "authorization.assessQualification": True,
        "authorization.runSixCaseCampaign": False,
        "authorization.automaticPhaseProgression": False,
        "authorization.scientificPromotion": False,
        "authorization.desktopPromotion": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    for field, wanted in expected.items():
        if nested(contract, field) != wanted:
            raise ValueError(f"frozen HF r2 contract field mismatch: {field}")
    sources = contract.get("sourceEvidence")
    if not isinstance(sources, list):
        raise ValueError("missing source evidence")
    paths = [row.get("path") for row in sources if isinstance(row, dict)]
    if len(paths) != len(set(paths)) or set(paths) != EXPECTED_SOURCE_PATHS:
        raise ValueError("HF r2 source evidence set mismatch")
    for row in sources:
        source = root() / row["path"]
        if not source.is_file() or _sha256(source) != row.get("sha256"):
            raise ValueError(f"HF r2 source hash mismatch: {row['path']}")
    for predecessor in ("pressureDisposition", "meshPreflightAssessment", "velocityDesign"):
        row = contract["predecessors"][predecessor]
        source = root() / row["path"]
        if not source.is_file() or _sha256(source) != row["sha256"]:
            raise ValueError(f"HF r2 predecessor hash mismatch: {predecessor}")
    if contract["coarsePilot"]["mandatoryFlavors"] != ["cpu-upgrade", "cpu-xl"]:
        raise ValueError("HF r2 mandatory flavor sequence mismatch")
    return contract


def add_unique_files(tar: tarfile.TarFile, source: Path) -> None:
    names: set[str] = set()
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(source).as_posix()
        if name in names:
            raise ValueError(f"duplicate input archive member: {name}")
        names.add(name)
        tar.add(path, arcname=name, recursive=False)


def prepare_input(contract_path: Path, output: Path) -> dict[str, Any]:
    contract = verify_contract(contract_path)
    relative = output.resolve().relative_to(root()).as_posix()
    if relative != contract["artifacts"]["localQualificationDirectory"]:
        raise ValueError("HF r2 qualification output path mismatch")
    require_new(output, "HF r2 qualification directory")
    output.mkdir(parents=True, exist_ok=True)
    copied_contract = output / "qualification-contract.json"
    shutil.copy2(contract_path, copied_contract)
    contract_sha = _sha256(copied_contract)
    baseline_case = output / "baseline" / "case"
    r1.prepare_case(baseline_case, contract_sha)
    logs = output / "baseline" / "logs"
    codes: dict[str, int] = {}
    for name, command in (
        ("blockMesh", "blockMesh"),
        ("checkMesh", "checkMesh -allTopology -allGeometry"),
        ("writeCellCentres", "foamPostProcess -func writeCellCentres -time 0"),
    ):
        codes[name] = run_command(
            _container_command(DEFAULT_IMAGE, root(), baseline_case, command),
            baseline_case,
            logs / f"{name}.log",
        )
        if codes[name] != 0:
            raise RuntimeError(f"local ARM64 r2 input preparation failed at {name}")
    initialize_case(baseline_case)
    remote_case = output / "bundle" / "case"
    shutil.copytree(baseline_case, remote_case)
    shutil.rmtree(remote_case / "constant" / "polyMesh")
    centre = remote_case / "0" / "C"
    if centre.exists():
        centre.unlink()
    shutil.copy2(copied_contract, output / "bundle" / "qualification-contract.json")
    archive = output / "qualification-input.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        add_unique_files(tar, output / "bundle")
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    if len(names) != len(set(names)):
        raise ValueError("HF r2 input archive contains duplicate members")
    record = {
        "schema": "flowlab.fda-nozzle-re500-hf-input-bundle.v2",
        "preparedAt": now(),
        "qualificationContractSha256": contract_sha,
        "arm64Image": DEFAULT_IMAGE,
        "arm64ImageDigest": DEFAULT_IMAGE_DIGEST,
        "localPreparationExitCodes": codes,
        "expectedCells": EXPECTED_CELLS["coarse"],
        "archiveMemberCount": len(names),
        "archiveMembersUnique": True,
        "caseDefinitionSha256": _sha256(remote_case / "case-definition.json"),
        "blockMeshDictSha256": _sha256(remote_case / "system" / "blockMeshDict"),
        "initialUSha256": _sha256(remote_case / "0" / "U"),
        "initialPSha256": _sha256(remote_case / "0" / "p"),
        "archiveSha256": _sha256(archive),
        "nonpromotional": True,
        "promotionAuthorized": False,
    }
    _write_json(output / "input-bundle.json", record)
    return record


def safe_members(archive: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
    names = [member.name for member in members]
    if len(names) != len(set(names)):
        raise ValueError("recovered archive has duplicate members")
    if any(name.startswith("/") or ".." in Path(name).parts for name in names):
        raise ValueError("recovered archive has unsafe members")
    if any(member.issym() or member.islnk() or member.isdev() for member in members):
        raise ValueError("recovered archive has unsupported member types")
    return members


def recover_lane(
    contract_path: Path,
    qualification_dir: Path,
    lane: str,
    job_record: Path,
    remote_result: Path,
    archive: Path,
    checksum: Path,
    hub_commit: str,
    output: Path,
) -> dict[str, Any]:
    contract = verify_contract(contract_path)
    if lane not in contract["coarsePilot"]["mandatoryFlavors"]:
        raise ValueError("unexpected HF r2 lane")
    require_new(output, "HF r2 lane recovery record")
    result = read_json(remote_result)
    job = read_json(job_record)
    archive_sha = _sha256(archive)
    checksum_sha = checksum.read_text(encoding="utf-8").split()[0]
    if not sha256_text(checksum_sha) or checksum_sha != archive_sha:
        raise ValueError("recovered archive checksum mismatch")
    if result.get("artifactArchiveSha256") != archive_sha:
        raise ValueError("remote result does not bind recovered archive")
    if result.get("jobId") != job.get("id") or result.get("flavor") != lane:
        raise ValueError("recovered lane job identity mismatch")
    if job.get("flavor") != lane or nested(job, "status.stage") != "COMPLETED":
        raise ValueError("recovered lane Job did not complete")
    if not commit_text(hub_commit):
        raise ValueError("recovered lane Hub commit is invalid")
    safe_members(archive)
    extraction = qualification_dir / "recovered" / lane / result["jobId"]
    require_new(extraction, "HF r2 recovered lane directory")
    extraction.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extraction)
    evidence = extraction / "qualification"
    manifest = read_json(evidence / "artifact-manifest.json")
    if manifest.get("schema") != "flowlab.fda-nozzle-re500-hf-artifact-manifest.v2" or manifest.get("jobId") != result["jobId"] or manifest.get("promotionAuthorized") is not False:
        raise ValueError("recovered artifact manifest identity mismatch")
    declared_paths = {row.get("path") for row in manifest.get("files", [])}
    actual_paths = {
        path.relative_to(evidence).as_posix()
        for path in evidence.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    if None in declared_paths or declared_paths != actual_paths:
        raise ValueError("recovered artifact manifest file set mismatch")
    for row in manifest.get("files", []):
        path = evidence / row["path"]
        if not path.is_file() or path.stat().st_size != row["bytes"] or _sha256(path) != row["sha256"]:
            raise ValueError(f"recovered artifact manifest mismatch: {row['path']}")
    recovery = {
        "schema": RECOVERY_SCHEMA,
        "recoveredAt": now(),
        "lane": lane,
        "jobId": result["jobId"],
        "hubCommit": hub_commit,
        "jobMetadata": {
            "id": job.get("id"),
            "flavor": job.get("flavor"),
            "arch": job.get("arch"),
            "dockerImage": job.get("dockerImage"),
            "status": job.get("status"),
        },
        "jobRecordSha256": _sha256(job_record),
        "remoteResultSha256": _sha256(remote_result),
        "artifactArchiveSha256": archive_sha,
        "checksumFileSha256": _sha256(checksum),
        "recoveredArtifactArchivePath": archive.resolve().relative_to(root()).as_posix(),
        "result": result,
        "artifactRecoveredAndRehashed": True,
        "promotionAuthorized": False,
    }
    _write_json(output, recovery)
    return recovery


def bind_record(contract_path: Path, kind: str, job_record: Path, remote_record: Path, hub_commit: str, output: Path) -> dict[str, Any]:
    verify_contract(contract_path)
    if kind not in {"probe", "volume-probe"}:
        raise ValueError("unexpected HF r2 record recovery kind")
    require_new(output, "HF r2 recovered record")
    job = read_json(job_record)
    record = read_json(remote_record)
    if record.get("jobId") != job.get("id") or nested(job, "status.stage") != "COMPLETED":
        raise ValueError("HF r2 recovered record Job mismatch")
    if job.get("flavor") != "cpu-basic" or job.get("arch") != "amd64":
        raise ValueError("HF r2 recovered record Job environment mismatch")
    if not commit_text(hub_commit):
        raise ValueError("HF r2 recovered record Hub commit is invalid")
    recovery = {
        "schema": RECORD_RECOVERY_SCHEMA,
        "kind": kind,
        "recoveredAt": now(),
        "jobId": record["jobId"],
        "hubCommit": hub_commit,
        "jobRecordSha256": _sha256(job_record),
        "remoteRecordSha256": _sha256(remote_record),
        "jobMetadata": {
            "id": job.get("id"), "flavor": job.get("flavor"), "arch": job.get("arch"),
            "dockerImage": job.get("dockerImage"), "status": job.get("status"),
        },
        "record": record,
        "promotionAuthorized": False,
    }
    _write_json(output, recovery)
    return recovery


def validate_probe(recovery: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if recovery.get("schema") != RECORD_RECOVERY_SCHEMA or recovery.get("kind") != "probe" or recovery.get("promotionAuthorized") is not False:
        raise ValueError("invalid HF r2 architecture probe recovery")
    record = recovery.get("record", {})
    required = {
        "schema": "flowlab.fda-nozzle-re500-hf-probe.v2",
        "contractId": CONTRACT_ID,
        "artifactRepository": ARTIFACT_REPO,
        "machine": "x86_64",
        "payloadSha256": PROBE_SHA256,
        "solverInvoked": False,
        "promotionAuthorized": False,
    }
    if any(record.get(key) != value for key, value in required.items()):
        raise ValueError("invalid HF r2 architecture probe record")
    if not record.get("jobId") or not commit_text(record.get("hubCommit")):
        if recovery.get("jobId") != record.get("jobId") or not commit_text(recovery.get("hubCommit")):
            raise ValueError("unbound HF r2 architecture probe")
    metadata = recovery.get("jobMetadata", {})
    if metadata.get("id") != record.get("jobId") or metadata.get("flavor") != "cpu-basic" or metadata.get("arch") != "amd64" or nested(metadata, "status.stage") != "COMPLETED":
        raise ValueError("HF r2 architecture probe Job binding mismatch")
    return record


def validate_volume(recovery: dict[str, Any], probe: dict[str, Any], probe_hub_commit: str) -> dict[str, Any]:
    if recovery.get("schema") != RECORD_RECOVERY_SCHEMA or recovery.get("kind") != "volume-probe" or recovery.get("promotionAuthorized") is not False:
        raise ValueError("invalid HF r2 volume probe recovery")
    record = recovery.get("record", {})
    required = {
        "schema": "flowlab.fda-nozzle-re500-hf-volume-probe.v2",
        "contractId": CONTRACT_ID,
        "artifactRepository": ARTIFACT_REPO,
        "sourceJobId": probe["jobId"],
        "expectedSha256": PROBE_SHA256,
        "actualSha256": PROBE_SHA256,
        "readOnlyRevisionPinnedMountPassed": True,
        "solverInvoked": False,
        "promotionAuthorized": False,
    }
    if any(record.get(key) != value for key, value in required.items()):
        raise ValueError("invalid HF r2 volume probe record")
    if not record.get("jobId") or record["jobId"] == probe["jobId"] or record.get("mountedRevision") != probe_hub_commit or not commit_text(recovery.get("hubCommit")):
        raise ValueError("unbound HF r2 volume probe")
    metadata = recovery.get("jobMetadata", {})
    if metadata.get("id") != record.get("jobId") or metadata.get("flavor") != "cpu-basic" or metadata.get("arch") != "amd64" or nested(metadata, "status.stage") != "COMPLETED":
        raise ValueError("HF r2 volume probe Job binding mismatch")
    return record


def validate_image(record: dict[str, Any], contract: dict[str, Any]) -> None:
    required = {
        "schema": "flowlab.fda-nozzle-re500-hf-amd64-image.v2",
        "spaceId": SPACE_ID,
        "localOciDigest": LOCAL_OCI_DIGEST,
        "architecture": "amd64",
        "openFoamVersion": "OpenFOAM-11",
        "gmshVersion": "4.15.2",
        "baseImageDigest": BASE_DIGEST,
        "gmshArchiveSha256": GMSH_SHA256,
        "runnerSha256": source_sha(contract, "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/remote_runner.py"),
        "passesImageQualification": True,
        "promotionAuthorized": False,
    }
    if any(record.get(key) != value for key, value in required.items()):
        raise ValueError("invalid HF r2 image record")
    if not commit_text(record.get("spaceCommit")) or not digest_text(str(record.get("registryDigest", ""))):
        raise ValueError("unbound HF r2 remote image identity")
    if record.get("jobImageReference") != f"hf.co/spaces/{SPACE_ID}":
        raise ValueError("HF r2 Job image reference is not Space-bound")


def validate_lane(recovery: dict[str, Any], lane: str, contract: dict[str, Any], qualification_dir: Path) -> dict[str, Any]:
    if recovery.get("schema") != RECOVERY_SCHEMA or recovery.get("lane") != lane or recovery.get("artifactRecoveredAndRehashed") is not True:
        raise ValueError(f"invalid HF r2 recovery record: {lane}")
    if recovery.get("promotionAuthorized") is not False or not commit_text(recovery.get("hubCommit")):
        raise ValueError(f"unbound HF r2 recovery record: {lane}")
    archive = root() / recovery["recoveredArtifactArchivePath"]
    resolved_archive = archive.resolve()
    resolved_qualification = qualification_dir.resolve()
    if resolved_qualification not in resolved_archive.parents:
        raise ValueError(f"HF r2 recovered archive is outside qualification directory: {lane}")
    if not archive.is_file() or _sha256(archive) != recovery.get("artifactArchiveSha256"):
        raise ValueError(f"HF r2 recovered archive hash mismatch: {lane}")
    result = recovery.get("result", {})
    expected_contract_sha = _sha256(contract_path := qualification_dir / "qualification-contract.json")
    required = {
        "schema": "flowlab.fda-nozzle-re500-hf-coarse-pilot.v2",
        "contractSha256": expected_contract_sha,
        "artifactRepository": ARTIFACT_REPO,
        "flavor": lane,
        "inputArchiveSha256": read_json(qualification_dir / "input-bundle.json")["archiveSha256"],
        "artifactArchiveSha256": recovery["artifactArchiveSha256"],
        "passesQualificationLane": True,
        "promotionAuthorized": False,
    }
    if any(result.get(key) != value for key, value in required.items()):
        raise ValueError(f"invalid HF r2 lane result: {lane}")
    if result.get("jobId") != recovery.get("jobId") or not result.get("jobId"):
        raise ValueError(f"HF r2 lane job mismatch: {lane}")
    checks = result.get("checks", {})
    if set(checks) != REQUIRED_LANE_CHECKS or not all(value is True for value in checks.values()):
        raise ValueError(f"HF r2 lane checks incomplete: {lane}")
    mesh = result.get("mesh", {})
    if mesh.get("cells") != 44256 or mesh.get("hexahedra") != 44256 or mesh.get("strictAllHex") is not True:
        raise ValueError(f"HF r2 lane mesh mismatch: {lane}")
    solver = result.get("solver", {})
    if solver.get("latestLoggedTime") != 800 or solver.get("terminalEnd") is not True or solver.get("completeFinalResidualSet") is not True:
        raise ValueError(f"HF r2 lane terminal evidence incomplete: {lane}")
    if set(solver.get("finalResiduals", {})) != {"Ux", "Uy", "Uz", "p"}:
        raise ValueError(f"HF r2 lane residual fields incomplete: {lane}")
    if float(solver.get("maximumFinalResidual", math.inf)) > 1.0e-10 or float(solver.get("finalAbsoluteContinuitySumLocal", math.inf)) > 1.0e-9:
        raise ValueError(f"HF r2 lane convergence mismatch: {lane}")
    fields = result.get("fields", {})
    if fields.get("U", {}).get("count") != 44256 * 3 or fields.get("p", {}).get("count") != 44256:
        raise ValueError(f"HF r2 lane field count mismatch: {lane}")
    for field in ("U", "p"):
        if not sha256_text(str(fields[field].get("canonical15Sha256", ""))):
            raise ValueError(f"HF r2 lane field hash missing: {lane}/{field}")
        if not all(math.isfinite(float(fields[field][metric])) for metric in ("sum", "sumSquares", "minimum", "maximum")):
            raise ValueError(f"HF r2 lane field invariant invalid: {lane}/{field}")
    memory = result.get("memorySummary", {})
    if memory.get("samples", 0) < 1 or memory.get("noOomEvents") is not True:
        raise ValueError(f"HF r2 lane memory summary incomplete: {lane}")
    if any(memory.get(name) is None for name in (
        "maximumCgroupCurrentBytes", "maximumCgroupPeakBytes", "cgroupLimitBytes",
        "maximumProcessTreeRssBytes", "maximumProcessTreeHighWaterBytes",
    )):
        raise ValueError(f"HF r2 lane memory metrics missing: {lane}")
    return result


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-30)


def assess(contract_path: Path, qualification_dir: Path, probe_path: Path, volume_path: Path, image_path: Path, cpu_upgrade_path: Path, cpu_xl_path: Path, review_path: Path, output: Path, report: Path) -> dict[str, Any]:
    contract = verify_contract(contract_path)
    for path, description in ((output, "HF r2 assessment"), (report, "HF r2 assessment report")):
        require_new(path, description)
    copied = qualification_dir / "qualification-contract.json"
    if not copied.is_file() or _sha256(copied) != _sha256(contract_path):
        raise ValueError("HF r2 qualification contract recovery mismatch")
    input_record = read_json(qualification_dir / "input-bundle.json")
    if input_record.get("schema") != "flowlab.fda-nozzle-re500-hf-input-bundle.v2" or input_record.get("archiveMembersUnique") is not True or input_record.get("promotionAuthorized") is not False:
        raise ValueError("invalid HF r2 input record")
    input_archive = qualification_dir / "qualification-input.tar.gz"
    if not input_archive.is_file() or _sha256(input_archive) != input_record.get("archiveSha256"):
        raise ValueError("HF r2 input archive recovery mismatch")
    probe_recovery = read_json(probe_path)
    volume_recovery = read_json(volume_path)
    image = read_json(image_path)
    probe = validate_probe(probe_recovery, contract)
    validate_volume(volume_recovery, probe, probe_recovery["hubCommit"])
    validate_image(image, contract)
    left_recovery = read_json(cpu_upgrade_path)
    right_recovery = read_json(cpu_xl_path)
    left = validate_lane(left_recovery, "cpu-upgrade", contract, qualification_dir)
    right = validate_lane(right_recovery, "cpu-xl", contract, qualification_dir)
    if left["jobId"] == right["jobId"]:
        raise ValueError("HF r2 coarse lanes reused a Job ID")
    for lane, result, recovery in (
        ("cpu-upgrade", left, left_recovery), ("cpu-xl", right, right_recovery)
    ):
        identity = result.get("image", {})
        if identity.get("runnerSha256") != image.get("runnerSha256") or identity.get("declaredSpaceId") != SPACE_ID or identity.get("declaredSpaceCommit") != image.get("spaceCommit") or identity.get("declaredRegistryDigest") != image.get("registryDigest"):
            raise ValueError(f"HF r2 lane image identity mismatch: {lane}")
        metadata = recovery.get("jobMetadata", {})
        if metadata.get("arch") != "amd64" or metadata.get("dockerImage") != image.get("jobImageReference"):
            raise ValueError(f"HF r2 lane Job image binding mismatch: {lane}")
    review = read_json(review_path)
    if review.get("schema") != "flowlab.fda-nozzle-re500-hf-infrastructure-independent-review.v2" or review.get("qualificationAccepted") is not True or review.get("promotionAuthorized") is not False:
        raise ValueError("HF r2 independent review did not accept qualification")
    bound_hashes = review.get("evidenceSha256", {})
    expected_hashes = {
        "probe": _sha256(probe_path), "volumeProbe": _sha256(volume_path), "image": _sha256(image_path),
        "cpu-upgrade": _sha256(cpu_upgrade_path), "cpu-xl": _sha256(cpu_xl_path),
    }
    if bound_hashes != expected_hashes:
        raise ValueError("HF r2 independent review evidence binding mismatch")
    cross: dict[str, float] = {}
    for field in ("U", "p"):
        for metric in ("sum", "sumSquares", "minimum", "maximum"):
            key = f"{field}.{metric}"
            cross[key] = relative_difference(float(left["fields"][field][metric]), float(right["fields"][field][metric]))
    limit = contract["coarsePilot"]["maximumCrossFlavorRelativeDifference"]
    gates = {
        "architectureProbe": True, "revisionPinnedVolumeProbe": True, "imageQualified": True,
        "inputRecovered": True, "cpuUpgradePassed": True, "cpuXlPassed": True,
        "crossFlavorEquivalent": max(cross.values()) <= limit,
        "artifactRecovery": True, "independentReview": True, "promotionClosed": True,
    }
    passed = all(gates.values())
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "assessedAt": now(),
        "status": "hf-infrastructure-qualified-nonpromotional" if passed else "hf-infrastructure-qualification-blocked",
        "contractSha256": _sha256(contract_path),
        "evidenceSha256": {**expected_hashes, "independentReview": _sha256(review_path)},
        "gates": gates,
        "crossFlavorRelativeDifferences": cross,
        "maximumCrossFlavorRelativeDifference": max(cross.values()),
        "selectedFlavorForFutureContract": "cpu-upgrade" if passed else None,
        "sixCaseExecutionAuthorized": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output, assessment)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# FDA Hugging Face infrastructure qualification r2\n\nStatus: **" + assessment["status"] + "**\n\nThe six-case campaign remains unlaunched and unauthorized.\n", encoding="utf-8")
    return assessment


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-contract")
    validate.add_argument("--contract", type=Path, required=True)
    prepare = sub.add_parser("prepare-input")
    prepare.add_argument("--contract", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    recover = sub.add_parser("recover-lane")
    recover.add_argument("--contract", type=Path, required=True)
    recover.add_argument("--qualification-dir", type=Path, required=True)
    recover.add_argument("--lane", required=True)
    recover.add_argument("--job-record", type=Path, required=True)
    recover.add_argument("--remote-result", type=Path, required=True)
    recover.add_argument("--archive", type=Path, required=True)
    recover.add_argument("--checksum", type=Path, required=True)
    recover.add_argument("--hub-commit", required=True)
    recover.add_argument("--output", type=Path, required=True)
    bind = sub.add_parser("bind-record")
    bind.add_argument("--contract", type=Path, required=True)
    bind.add_argument("--kind", required=True)
    bind.add_argument("--job-record", type=Path, required=True)
    bind.add_argument("--remote-record", type=Path, required=True)
    bind.add_argument("--hub-commit", required=True)
    bind.add_argument("--output", type=Path, required=True)
    assessment = sub.add_parser("assess")
    for name in ("contract", "qualification-dir", "probe", "volume-probe", "image-record", "cpu-upgrade", "cpu-xl", "independent-review", "output", "report"):
        assessment.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate-contract":
        print(json.dumps(verify_contract(args.contract), indent=2, sort_keys=True)); return 0
    if args.command == "prepare-input":
        print(json.dumps(prepare_input(args.contract, args.output), indent=2, sort_keys=True)); return 0
    if args.command == "recover-lane":
        result = recover_lane(args.contract, args.qualification_dir, args.lane, args.job_record, args.remote_result, args.archive, args.checksum, args.hub_commit, args.output)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    if args.command == "bind-record":
        result = bind_record(args.contract, args.kind, args.job_record, args.remote_record, args.hub_commit, args.output)
        print(json.dumps(result, indent=2, sort_keys=True)); return 0
    result = assess(args.contract, args.qualification_dir, args.probe, args.volume_probe, args.image_record, args.cpu_upgrade, args.cpu_xl, args.independent_review, args.output, args.report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "hf-infrastructure-qualified-nonpromotional" else 3


if __name__ == "__main__":
    raise SystemExit(main())
