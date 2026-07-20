"""Prospective, nonpromotional Hugging Face qualification for FDA Re=500.

This module prepares one coarse input bundle and assesses recovered evidence.
It never submits a Hugging Face Job, launches the six-case campaign, changes a
scientific gate, or authorizes product promotion.
"""

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

from .fda_nozzle_re500 import (
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_DIGEST,
    FdaNozzleDefinition,
    _case_files,
    _container_command,
    _sha256,
    _write,
    _write_json,
    initialize_case,
    run_command,
)
from .fda_nozzle_re500_v2_preflight import _formal_second_order
from .fda_nozzle_re500_v3_mesh_preflight import EXPECTED_CELLS, v3_block_mesh


SCHEMA = "flowlab.fda-nozzle-re500-hf-infrastructure-qualification.v1"
ASSESSMENT_SCHEMA = "flowlab.fda-nozzle-re500-hf-infrastructure-assessment.v1"
EXPECTED_PRESSURE_SHA256 = "96c390c95583fcdbb15e8cc41a31cd520c4d86e33cdb463c9a53a23150f09734"
EXPECTED_MESH_SHA256 = "c5693f80e632726f0ea51a959e2b429a65e721ad07671324ea35b5e8dba1228a"


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_new(path: Path, description: str) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"refusing to overwrite existing {description}: {path}")


def verify_contract(path: Path) -> dict[str, Any]:
    contract = read_json(path)
    if contract.get("schema") != SCHEMA:
        raise ValueError("unexpected HF qualification contract schema")
    if contract.get("status") != "frozen-nonpromotional-qualification-authorized":
        raise ValueError("HF qualification contract is not frozen and authorized")
    predecessors = contract.get("predecessors", {})
    pressure = root() / predecessors["pressureDisposition"]["path"]
    mesh = root() / predecessors["meshPreflightAssessment"]["path"]
    if _sha256(pressure) != EXPECTED_PRESSURE_SHA256:
        raise ValueError("pressure disposition hash mismatch")
    if _sha256(mesh) != EXPECTED_MESH_SHA256:
        raise ValueError("V3 mesh assessment hash mismatch")
    for row in contract.get("sourceEvidence", []):
        source = root() / row["path"]
        if not source.is_file() or _sha256(source) != row["sha256"]:
            raise ValueError(f"qualification source hash mismatch: {row['path']}")
    authorization = contract.get("authorization", {})
    required_true = {
        "runArchitectureProbe",
        "runRevisionPinnedVolumeProbe",
        "buildAmd64Image",
        "runCpuUpgradeCoarsePilot",
        "runCpuXlCoarseComparison",
        "assessQualification",
    }
    required_false = {
        "runSixCaseCampaign",
        "automaticPhaseProgression",
        "scientificPromotion",
        "desktopPromotion",
    }
    if not all(authorization.get(name) is True for name in required_true):
        raise ValueError("qualification stages are not fully authorized")
    if not all(authorization.get(name) is False for name in required_false):
        raise ValueError("qualification contract does not fail closed")
    if contract.get("promotionAuthorized") is not False:
        raise ValueError("qualification contract cannot authorize promotion")
    probe = contract.get("probe", {})
    payload = str(probe.get("payload", "")).encode("utf-8")
    if hashlib.sha256(payload).hexdigest() != probe.get("payloadSha256"):
        raise ValueError("qualification probe payload hash mismatch")
    return contract


def prepare_case(case: Path, contract_sha256: str) -> None:
    require_new(case, "coarse qualification case")
    files = _case_files("coarse-hf-qualification", 1, 1.0, FdaNozzleDefinition())
    files["system/blockMeshDict"] = v3_block_mesh("coarse")
    files["system/fvSchemes"] = _formal_second_order(files["system/fvSchemes"])
    definition = json.loads(files["case-definition.json"])
    definition.update(
        {
            "schema": "flowlab.fda-nozzle-re500-hf-coarse-input.v1",
            "meshLevel": "coarse",
            "expectedCells": EXPECTED_CELLS["coarse"],
            "qualificationContractSha256": contract_sha256,
            "nonpromotional": True,
            "promotionAuthorized": False,
        }
    )
    files["case-definition.json"] = json.dumps(definition, indent=2, sort_keys=True) + "\n"
    for relative, content in files.items():
        _write(case / relative, content)


def prepare_input(contract_path: Path, output: Path) -> dict[str, Any]:
    contract = verify_contract(contract_path)
    expected = contract["artifacts"]["localQualificationDirectory"]
    relative = output.resolve().relative_to(root()).as_posix()
    if relative != expected:
        raise ValueError(f"qualification output must match contract: {expected}")
    require_new(output, "HF qualification directory")
    output.mkdir(parents=True, exist_ok=True)
    copied_contract = output / "qualification-contract.json"
    shutil.copy2(contract_path, copied_contract)
    contract_sha = _sha256(copied_contract)
    baseline_case = output / "baseline" / "case"
    prepare_case(baseline_case, contract_sha)
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
            raise RuntimeError(f"local ARM64 input preparation failed at {name}")
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
        for path in sorted((output / "bundle").rglob("*")):
            tar.add(path, arcname=path.relative_to(output / "bundle"))
    record = {
        "schema": "flowlab.fda-nozzle-re500-hf-input-bundle.v1",
        "preparedAt": now(),
        "qualificationContractSha256": contract_sha,
        "arm64Image": DEFAULT_IMAGE,
        "arm64ImageDigest": DEFAULT_IMAGE_DIGEST,
        "localPreparationExitCodes": codes,
        "expectedCells": EXPECTED_CELLS["coarse"],
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


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0e-30)


def assess(
    contract_path: Path,
    qualification_dir: Path,
    probe: Path,
    volume_probe: Path,
    image_record: Path,
    cpu_upgrade: Path,
    cpu_xl: Path,
    output: Path,
    report: Path,
) -> dict[str, Any]:
    contract = verify_contract(contract_path)
    for path, description in ((output, "assessment"), (report, "assessment report")):
        require_new(path, description)
    records = {
        "probe": read_json(probe),
        "volumeProbe": read_json(volume_probe),
        "image": read_json(image_record),
        "cpu-upgrade": read_json(cpu_upgrade),
        "cpu-xl": read_json(cpu_xl),
    }
    input_record = read_json(qualification_dir / "input-bundle.json")
    lane_checks = {
        lane: bool(records[lane].get("passesQualificationLane"))
        for lane in ("cpu-upgrade", "cpu-xl")
    }
    cross: dict[str, float] = {}
    left = records["cpu-upgrade"]
    right = records["cpu-xl"]
    for field in ("U", "p"):
        for metric in ("sum", "sumSquares", "minimum", "maximum"):
            key = f"{field}.{metric}"
            cross[key] = relative_difference(
                float(left["fields"][field][metric]),
                float(right["fields"][field][metric]),
            )
    cross_limit = float(contract["coarsePilot"]["maximumCrossFlavorRelativeDifference"])
    gates = {
        "architectureProbe": records["probe"].get("machine") in {"x86_64", "amd64"},
        "volumeProbe": records["volumeProbe"].get("readOnlyRevisionPinnedMountPassed") is True,
        "imageQualified": records["image"].get("passesImageQualification") is True,
        "inputRecovered": input_record["archiveSha256"]
        == _sha256(qualification_dir / "qualification-input.tar.gz"),
        "cpuUpgradePassed": lane_checks["cpu-upgrade"],
        "cpuXlPassed": lane_checks["cpu-xl"],
        "crossFlavorEquivalent": max(cross.values(), default=math.inf) <= cross_limit,
        "artifactRecovery": all(
            records[lane].get("artifactRecoveredAndRehashed") is True
            for lane in ("cpu-upgrade", "cpu-xl")
        ),
        "promotionClosed": all(
            records[name].get("promotionAuthorized") is False
            for name in ("cpu-upgrade", "cpu-xl")
        ),
    }
    passed = all(gates.values())
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "assessedAt": now(),
        "status": "hf-infrastructure-qualified-nonpromotional" if passed else "hf-infrastructure-qualification-blocked",
        "contractSha256": _sha256(contract_path),
        "gates": gates,
        "laneChecks": lane_checks,
        "crossFlavorRelativeDifferences": cross,
        "maximumCrossFlavorRelativeDifference": max(cross.values(), default=None),
        "selectedFlavorForFutureContract": "cpu-upgrade" if passed else None,
        "selectionRationale": "Both lanes use an isolated serial solver; cpu-upgrade supplies 32 GiB and allows independent Jobs at substantially lower cost.",
        "sixCaseExecutionAuthorized": False,
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output, assessment)
    lines = [
        "# FDA Hugging Face infrastructure qualification",
        "",
        f"Status: **{assessment['status']}**",
        "",
        "This assessment is infrastructure and reproducibility evidence only. It cannot authorize scientific or desktop promotion.",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- {name}: {'PASS' if value else 'FAIL'}" for name, value in gates.items())
    lines.extend(
        [
            "",
            f"Selected future flavor: `{assessment['selectedFlavorForFutureContract']}`",
            "",
            "The six-case campaign remains unlaunched and requires the separately frozen HF execution contract plus an explicit launch instruction.",
            "",
        ]
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    return assessment


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-contract")
    validate.add_argument("--contract", type=Path, required=True)
    prepare = sub.add_parser("prepare-input")
    prepare.add_argument("--contract", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    assessment = sub.add_parser("assess")
    assessment.add_argument("--contract", type=Path, required=True)
    assessment.add_argument("--qualification-dir", type=Path, required=True)
    assessment.add_argument("--probe", type=Path, required=True)
    assessment.add_argument("--volume-probe", type=Path, required=True)
    assessment.add_argument("--image-record", type=Path, required=True)
    assessment.add_argument("--cpu-upgrade", type=Path, required=True)
    assessment.add_argument("--cpu-xl", type=Path, required=True)
    assessment.add_argument("--output", type=Path, required=True)
    assessment.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate-contract":
        print(json.dumps(verify_contract(args.contract), indent=2, sort_keys=True))
        return 0
    if args.command == "prepare-input":
        print(json.dumps(prepare_input(args.contract, args.output), indent=2, sort_keys=True))
        return 0
    result = assess(
        args.contract,
        args.qualification_dir,
        args.probe,
        args.volume_probe,
        args.image_record,
        args.cpu_upgrade,
        args.cpu_xl,
        args.output,
        args.report,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "hf-infrastructure-qualified-nonpromotional" else 3


if __name__ == "__main__":
    raise SystemExit(main())
