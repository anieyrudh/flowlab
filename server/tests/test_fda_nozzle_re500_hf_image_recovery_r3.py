from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.flowlab import fda_nozzle_re500_hf_image_recovery_r3 as recovery


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/validation/fda-nozzle-re500/HF_AMD64_IMAGE_RECOVERY_R3_CONTRACT.json"
ASSESSMENT = ROOT / "docs/validation/fda-nozzle-re500/audits/2026-07-20-hf-amd64-image-recovery-r3.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r3_contract_is_source_bound_and_nonpromotional() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for record in contract["sourceEvidence"]:
        assert sha256(ROOT / record["path"]) == record["sha256"]

    hypothesis = contract["imageHypothesis"]
    assert hypothesis["platform"] == "linux/amd64"
    assert hypothesis["runtimeUid"] == 1000
    assert hypothesis["runtimeGid"] == 1000
    assert hypothesis["openFoamPackageVersion"] == "20240612"
    assert hypothesis["gmshVersion"] == "4.15.2"
    assert contract["authorization"]["runHfJobsProbe"] is False
    assert contract["authorization"]["runSolver"] is False
    assert contract["authorization"]["runSixCaseCampaign"] is False
    assert contract["promotionAuthorized"] is False


def test_r3_dockerfile_uses_exact_builder_safe_identity() -> None:
    dockerfile = (
        ROOT / "docker/openfoam11-gmsh415-immutable-amd64-hf-r3/Dockerfile"
    ).read_text(encoding="utf-8")
    assert "ubuntu:20.04@sha256:c664f8f86ed5a386b0a340d981b8f81714e21a8b9c73f658c4bea56aa179d54a" in dockerfile
    assert '"openfoam11=${OPENFOAM_VERSION}"' in dockerfile
    assert "groupadd --gid 1000 openfoam" in dockerfile
    assert "useradd --uid 1000 --gid 1000" in dockerfile
    assert "COPY --chown=1000:1000 remote_runner.py" in dockerfile
    assert "USER 1000:1000" in dockerfile
    assert "foamRun" not in dockerfile


def test_r3_assessment_passes_only_image_recovery() -> None:
    assessment = json.loads(ASSESSMENT.read_text(encoding="utf-8"))
    assert assessment["status"] == "image-recovery-passed-nonpromotional"
    assert all(assessment["gates"].values())
    assert assessment["remoteImage"]["spaceCommit"].startswith("4c8572a")
    assert assessment["remoteImage"]["commitPrefixedRegistryTag"] == "cpu-4c8572a"
    assert assessment["remoteImage"]["mutableLatestUsedAsEvidence"] is False
    assert assessment["hfInfrastructureQualified"] is False
    assert assessment["sixCaseExecutionContractMayBeFrozen"] is False
    assert assessment["sixCaseExecutionAuthorized"] is False
    assert assessment["promotionAuthorized"] is False


def test_r3_validator_rehashes_raw_evidence() -> None:
    raw = ROOT / "benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-hf-amd64-image-recovery-r3"
    if not raw.is_dir():
        pytest.skip("raw R3 evidence is intentionally outside normal Git clones")
    verified = recovery.verify(CONTRACT, ASSESSMENT, raw, ROOT)
    assert verified["status"] == "verified-image-recovery-only"
    assert verified["remoteRegistryDigest"].startswith("sha256:")
    assert verified["promotionAuthorized"] is False


def test_r3_validator_rejects_tampered_raw_record(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-hf-amd64-image-recovery-r3"
    if not source.is_dir():
        pytest.skip("raw R3 evidence is intentionally outside normal Git clones")
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in (recovery.LOCAL_RECORD, recovery.REMOTE_RECORD, recovery.BUILD_LOG):
        (raw / name).write_bytes((source / name).read_bytes())
    local = json.loads((raw / recovery.LOCAL_RECORD).read_text(encoding="utf-8"))
    local["runtimeUid"] = 98765
    (raw / recovery.LOCAL_RECORD).write_text(json.dumps(local), encoding="utf-8")
    with pytest.raises(ValueError, match="local image record hash mismatch"):
        recovery.verify(CONTRACT, ASSESSMENT, raw, ROOT)
