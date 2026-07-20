from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any


CONTRACT_SHA256 = "49ea1c787594b8afd695b77ef749421941de2082df17eb53cee9d753bbcd6a79"
LOCAL_RECORD = "local-image-record.json"
REMOTE_RECORD = "remote-space-record.json"
BUILD_LOG = "remote-space-build.log"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify(
    contract_path: Path,
    assessment_path: Path,
    raw_directory: Path,
    repository_root: Path,
) -> dict[str, Any]:
    if sha256(contract_path) != CONTRACT_SHA256:
        raise ValueError("R3 image-recovery contract hash mismatch")
    contract = load_json(contract_path)
    assessment = load_json(assessment_path)
    local_path = raw_directory / LOCAL_RECORD
    remote_path = raw_directory / REMOTE_RECORD
    log_path = raw_directory / BUILD_LOG
    for path in (local_path, remote_path, log_path):
        if not path.is_file():
            raise ValueError(f"missing raw R3 image evidence: {path}")

    for source in contract["sourceEvidence"]:
        if sha256(repository_root / source["path"]) != source["sha256"]:
            raise ValueError(f"R3 source hash mismatch: {source['path']}")

    local = load_json(local_path)
    remote = load_json(remote_path)
    if sha256(local_path) != assessment["localEvidence"]["sha256"]:
        raise ValueError("local image record hash mismatch")
    if sha256(remote_path) != assessment["remoteEvidence"]["sha256"]:
        raise ValueError("remote Space record hash mismatch")
    if sha256(log_path) != remote["buildLog"]["sha256"]:
        raise ValueError("remote Space build log hash mismatch")
    if log_path.stat().st_size != remote["buildLog"]["bytes"]:
        raise ValueError("remote Space build log size mismatch")

    hypothesis = contract["imageHypothesis"]
    expected_local = {
        "architecture": "amd64",
        "os": "linux",
        "runtimeUid": hypothesis["runtimeUid"],
        "runtimeGid": hypothesis["runtimeGid"],
        "openFoamPackageVersion": hypothesis["openFoamPackageVersion"],
        "foamVersion": hypothesis["expectedFoamVersion"],
        "gmshVersionNormalized": hypothesis["gmshVersion"],
        "runnerSha256": hypothesis["runnerSha256"],
    }
    for key, expected in expected_local.items():
        if local.get(key) != expected:
            raise ValueError(f"local image identity mismatch: {key}")
    if not DIGEST.fullmatch(local.get("ociDigest", "")):
        raise ValueError("invalid local OCI digest")

    commit = remote.get("spaceCommit", "")
    if not COMMIT.fullmatch(commit):
        raise ValueError("invalid remote Space commit")
    if remote.get("runtimeCommit") != commit:
        raise ValueError("remote runtime is not bound to Space commit")
    registry = remote["registry"]
    if registry.get("commitPrefixedTag") != f"cpu-{commit[:7]}":
        raise ValueError("remote registry tag is not commit-prefixed")
    if not DIGEST.fullmatch(registry.get("ociDigest", "")):
        raise ValueError("invalid remote registry digest")
    if registry.get("latestDigestAtObservation") != registry["ociDigest"]:
        raise ValueError("observed latest digest differed from commit-prefixed digest")
    if registry.get("mutableLatestUsedAsEvidence") is not False:
        raise ValueError("mutable latest tag used as evidence")
    if assessment["remoteImage"]["spaceCommit"] != commit:
        raise ValueError("assessment Space commit mismatch")
    if assessment["remoteImage"]["registryDigest"] != registry["ociDigest"]:
        raise ValueError("assessment registry digest mismatch")

    declared_sources = {item["path"].split("/")[-1]: item["sha256"] for item in contract["sourceEvidence"]}
    remote_sources = {item["path"]: item["sha256"] for item in remote["sourceFiles"]}
    expected_remote_sources = {
        "Dockerfile": declared_sources["Dockerfile"],
        "README.md": declared_sources["SPACE_README.md"],
        "remote_runner.py": declared_sources["remote_runner.py"],
    }
    if remote_sources != expected_remote_sources:
        raise ValueError("remote Space source hashes differ from frozen sources")

    if not (
        timestamp(contract["frozenAt"])
        < timestamp(local["recordedAt"])
        < timestamp(remote["recordedAt"])
        < timestamp(assessment["assessedAt"])
    ):
        raise ValueError("R3 freeze/evidence/assessment chronology is invalid")
    if not all(assessment["gates"].values()):
        raise ValueError("R3 compact assessment contains a failed gate")
    for record in (contract, assessment, local, remote):
        if record.get("promotionAuthorized") is not False:
            raise ValueError("R3 image recovery must remain nonpromotional")
    if assessment.get("hfInfrastructureQualified") is not False:
        raise ValueError("image recovery cannot qualify HF infrastructure")
    if assessment.get("sixCaseExecutionContractMayBeFrozen") is not False:
        raise ValueError("image recovery cannot permit six-case contract freeze")

    return {
        "schema": "flowlab.fda-nozzle-re500-hf-amd64-image-recovery-verification.v1",
        "status": "verified-image-recovery-only",
        "contractSha256": CONTRACT_SHA256,
        "localRecordSha256": sha256(local_path),
        "remoteRecordSha256": sha256(remote_path),
        "buildLogSha256": sha256(log_path),
        "spaceCommit": commit,
        "remoteRegistryDigest": registry["ociDigest"],
        "hfInfrastructureQualified": False,
        "sixCaseExecutionContractMayBeFrozen": False,
        "promotionAuthorized": False,
    }
