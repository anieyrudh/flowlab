from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

from server.flowlab import fda_nozzle_re500_hf_qualification_r2 as hfq


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/validation/fda-nozzle-re500/HF_INFRASTRUCTURE_QUALIFICATION_R2_CONTRACT.json"


def load_runner():
    path = ROOT / "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/remote_runner.py"
    spec = importlib.util.spec_from_file_location("flowlab_hf_remote_runner_r2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_contract(tmp_path: Path, mutate) -> Path:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    mutate(value)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_r2_contract_is_exact_and_nonpromotional() -> None:
    contract = hfq.verify_contract(CONTRACT)
    assert contract["coarsePilot"]["maximumCrossFlavorRelativeDifference"] == 1.0e-10
    assert contract["amd64Image"]["localOciDigest"] == hfq.LOCAL_OCI_DIGEST
    assert contract["authorization"]["runSixCaseCampaign"] is False
    assert contract["promotionAuthorized"] is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["coarsePilot"].__setitem__("maximumCrossFlavorRelativeDifference", 1.0), "frozen"),
        (lambda value: value["amd64Image"].__setitem__("openFoamBaseDigest", "sha256:" + "0" * 64), "frozen"),
        (lambda value: value.__setitem__("sourceEvidence", []), "source evidence"),
        (lambda value: value["futureConcurrencyDesign"].__setitem__("maximumConcurrentPhase2Jobs", 20), "frozen"),
    ],
)
def test_r2_contract_tampering_fails_closed(tmp_path: Path, mutate, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        hfq.verify_contract(write_contract(tmp_path, mutate))


def test_r2_assessment_rejects_boolean_only_records(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification"
    qualification.mkdir()
    (qualification / "qualification-contract.json").write_bytes(CONTRACT.read_bytes())
    archive = qualification / "qualification-input.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    (qualification / "input-bundle.json").write_text(
        json.dumps({
            "schema": "flowlab.fda-nozzle-re500-hf-input-bundle.v2",
            "archiveMembersUnique": True,
            "archiveSha256": hfq._sha256(archive),
            "promotionAuthorized": False,
        }),
        encoding="utf-8",
    )
    records = []
    for name in ("probe", "volume", "image", "upgrade", "xl", "review"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"passesQualificationLane": True, "promotionAuthorized": False}), encoding="utf-8")
        records.append(path)
    with pytest.raises(ValueError, match="architecture probe"):
        hfq.assess(CONTRACT, qualification, *records, tmp_path / "assessment.json", tmp_path / "report.md")


def test_r2_terminal_residuals_require_all_four_fields(tmp_path: Path) -> None:
    runner = load_runner()
    log = tmp_path / "foamRun.log"
    log.write_text(
        "Time = 799\n"
        "Solving for Uy, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 2\n"
        "Solving for Uz, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 2\n"
        "Solving for p, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 2\n"
        "Time = 800\n"
        "Solving for Ux, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 2\n"
        "time step continuity errors : sum local = 1e-12, global = 0, cumulative = 0\n"
        "End\n",
        encoding="utf-8",
    )
    parsed = runner.parse_solver(log)
    assert parsed["terminalEnd"] is True
    assert parsed["completeFinalResidualSet"] is False
    assert parsed["maximumFinalResidual"] is None


def test_r2_archive_extraction_rejects_duplicate_members(tmp_path: Path) -> None:
    runner = load_runner()
    archive = tmp_path / "duplicate.tar.gz"
    info = tarfile.TarInfo("case/value")
    info.size = 1
    with tarfile.open(archive, "w:gz") as tar:
        tar.addfile(info, io.BytesIO(b"a"))
        tar.addfile(info, io.BytesIO(b"b"))
    with pytest.raises(ValueError, match="duplicate"):
        runner.safe_extract(archive, tmp_path / "out")


def test_r2_archive_extraction_rejects_links(tmp_path: Path) -> None:
    runner = load_runner()
    archive = tmp_path / "link.tar.gz"
    info = tarfile.TarInfo("case/link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    with tarfile.open(archive, "w:gz") as tar:
        tar.addfile(info)
    with pytest.raises(ValueError, match="unsupported"):
        runner.safe_extract(archive, tmp_path / "out")


def test_r2_oom_telemetry_fails_closed() -> None:
    runner = load_runner()
    assert runner.no_oom_events([]) is False
    assert runner.no_oom_events([{"cgroupMemory": {}}]) is False
    assert runner.no_oom_events([{"cgroupMemory": {"events": {"oom_kill": 0}}}]) is True
    assert runner.no_oom_events([{"cgroupMemory": {"events": {"oom_kill": 1}}}]) is False


def test_r2_memory_telemetry_requires_cgroup_process_and_oom_fields() -> None:
    runner = load_runner()
    complete = [{
        "cgroupMemory": {
            "currentBytes": 10,
            "peakBytes": 20,
            "limitBytes": 30,
            "events": {"oom": 0, "oom_kill": 0},
        },
        "processTreeMemory": {"processCount": 2, "rssBytes": 5, "highWaterBytes": 6},
    }]
    assert runner.telemetry_complete(complete) is True
    incomplete = json.loads(json.dumps(complete))
    incomplete[0]["cgroupMemory"]["peakBytes"] = None
    assert runner.telemetry_complete(incomplete) is False


def test_r2_volume_mount_must_equal_probe_commit() -> None:
    probe = {"jobId": "probe-job"}
    recovery = {
        "schema": hfq.RECORD_RECOVERY_SCHEMA,
        "kind": "volume-probe",
        "hubCommit": "b" * 40,
        "promotionAuthorized": False,
        "jobMetadata": {
            "id": "volume-job", "flavor": "cpu-basic", "arch": "amd64",
            "status": {"stage": "COMPLETED"},
        },
        "record": {
            "schema": "flowlab.fda-nozzle-re500-hf-volume-probe.v2",
            "contractId": hfq.CONTRACT_ID,
            "artifactRepository": hfq.ARTIFACT_REPO,
            "sourceJobId": "probe-job",
            "jobId": "volume-job",
            "mountedRevision": "c" * 40,
            "expectedSha256": hfq.PROBE_SHA256,
            "actualSha256": hfq.PROBE_SHA256,
            "readOnlyRevisionPinnedMountPassed": True,
            "solverInvoked": False,
            "promotionAuthorized": False,
        },
    }
    with pytest.raises(ValueError, match="unbound"):
        hfq.validate_volume(recovery, probe, "a" * 40)


def test_r2_image_record_requires_space_job_reference() -> None:
    contract = hfq.verify_contract(CONTRACT)
    record = {
        "schema": "flowlab.fda-nozzle-re500-hf-amd64-image.v2",
        "spaceId": hfq.SPACE_ID,
        "localOciDigest": hfq.LOCAL_OCI_DIGEST,
        "architecture": "amd64",
        "openFoamVersion": "OpenFOAM-11",
        "gmshVersion": "4.15.2",
        "baseImageDigest": hfq.BASE_DIGEST,
        "gmshArchiveSha256": hfq.GMSH_SHA256,
        "runnerSha256": hfq.source_sha(contract, "docker/openfoam11-gmsh415-immutable-amd64-hf-r2/remote_runner.py"),
        "spaceCommit": "a" * 40,
        "registryDigest": "sha256:" + "b" * 64,
        "jobImageReference": "mutable/latest",
        "passesImageQualification": True,
        "promotionAuthorized": False,
    }
    with pytest.raises(ValueError, match="Space-bound"):
        hfq.validate_image(record, contract)


def test_unique_input_tar_has_one_entry_per_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "a").mkdir(parents=True)
    (source / "a" / "one").write_text("1", encoding="utf-8")
    (source / "two").write_text("2", encoding="utf-8")
    archive = tmp_path / "input.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        hfq.add_unique_files(tar, source)
    with tarfile.open(archive, "r:gz") as tar:
        assert tar.getnames() == ["a/one", "two"]
