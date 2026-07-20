"""One-shot infrastructure recovery for the FDA Re=500 V3 mesh preflight.

This launcher binds the corrected, already-tested mesh-only runner to a new
frozen contract and output directory. It does not alter any scientific gate.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any, Iterator

from . import fda_nozzle_re500_v3_mesh_preflight as base


RECOVERY_SCHEMA = (
    "flowlab.fda-nozzle-re500-v3-mesh-preflight-recovery-contract.v1"
)
RECOVERY_CONTRACT_SHA256 = (
    "85d5166a38eb0a5e94519259439d812f2238ceab21dfb6464f14a53969b33e5f"
)
RECOVERY_CAMPAIGN_ID = "2026-07-20-re500-v3-mesh-preflight-r2"
CORRECTED_RUNNER_SHA256 = (
    "06be36c7a9a815e3b04265d49b34c5707e976799cf4d97ae5752b238ff571369"
)
FAILED_CONTRACT_SHA256 = (
    "8775ca7ce86ef22d8172bbec346adae21b3301dec20558d8ec6e9e66e20a1d34"
)
FAILED_ASSESSMENT_SHA256 = (
    "388c76aa05c32f04ffaf6dd653339835983433f4f2062ca2991397e5fe4458b9"
)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _verify_recovery_contract(contract_path: Path) -> dict[str, Any]:
    if not contract_path.is_file():
        raise ValueError("missing frozen V3 mesh-preflight recovery contract")
    if base._sha256(contract_path) != RECOVERY_CONTRACT_SHA256:
        raise ValueError("frozen V3 mesh-preflight recovery contract hash mismatch")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != RECOVERY_SCHEMA:
        raise ValueError("unexpected V3 mesh-preflight recovery contract schema")
    runner = _root() / contract["correctedRunner"]["path"]
    if contract["correctedRunner"]["sha256"] != CORRECTED_RUNNER_SHA256:
        raise ValueError("recovery contract corrected-runner hash mismatch")
    if base._sha256(runner) != CORRECTED_RUNNER_SHA256:
        raise ValueError("corrected mesh-preflight runner has drifted")
    prior_contract = _root() / contract["recoveryOf"]["contract"]
    prior_assessment = _root() / contract["recoveryOf"]["assessment"]
    if base._sha256(prior_contract) != FAILED_CONTRACT_SHA256:
        raise ValueError("failed predecessor contract hash mismatch")
    if base._sha256(prior_assessment) != FAILED_ASSESSMENT_SHA256:
        raise ValueError("failed predecessor assessment hash mismatch")
    if contract["recoveryOf"]["scientificGateEvaluated"] is not False:
        raise ValueError("recovery is not limited to an infrastructure failure")
    if contract["correctedRunner"]["scientificContractChanged"] is not False:
        raise ValueError("recovery changes the scientific contract")
    return contract


@contextmanager
def _recovery_identity() -> Iterator[None]:
    names = ("CONTRACT_SCHEMA", "CONTRACT_SHA256", "CAMPAIGN_ID")
    original = {name: getattr(base, name) for name in names}
    base.CONTRACT_SCHEMA = RECOVERY_SCHEMA
    base.CONTRACT_SHA256 = RECOVERY_CONTRACT_SHA256
    base.CAMPAIGN_ID = RECOVERY_CAMPAIGN_ID
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(base, name, value)


def prepare_recovery(
    output: Path, contract_path: Path, pressure_path: Path
) -> dict[str, Any]:
    contract = _verify_recovery_contract(contract_path)
    with _recovery_identity():
        manifest = base.prepare_campaign(output, contract_path, pressure_path)
    manifest["recoveryOf"] = {
        "contractSha256": contract["recoveryOf"]["contractSha256"],
        "assessmentSha256": contract["recoveryOf"]["assessmentSha256"],
        "failureClassification": contract["recoveryOf"][
            "failureClassification"
        ],
    }
    manifest["sourceSha256"]["recoveryLauncherSource"] = base._sha256(
        Path(__file__).resolve()
    )
    base._write_json(output / "campaign-manifest.json", manifest)
    return manifest


def mesh_all_recovery(
    output: Path, image: str = base.DEFAULT_IMAGE, max_workers: int = 1
) -> dict[str, Any]:
    with _recovery_identity():
        return base.mesh_all(output, image=image, max_workers=max_workers)


def assess_recovery(
    output: Path, compact_output: Path, compact_report: Path
) -> dict[str, Any]:
    with _recovery_identity():
        return base.assess(output, compact_output, compact_report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--contract", type=Path, required=True)
    prepare.add_argument("--pressure-disposition", type=Path, required=True)
    mesh = sub.add_parser("mesh-all")
    mesh.add_argument("--output", type=Path, required=True)
    mesh.add_argument("--image", default=base.DEFAULT_IMAGE)
    mesh.add_argument("--max-workers", type=int, default=1)
    assessment = sub.add_parser("assess")
    assessment.add_argument("--output", type=Path, required=True)
    assessment.add_argument("--compact-output", type=Path, required=True)
    assessment.add_argument("--compact-report", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        result = prepare_recovery(
            args.output.resolve(),
            args.contract.resolve(),
            args.pressure_disposition.resolve(),
        )
        success = result["status"] == "prepared-awaiting-mesh-only-execution"
    elif args.command == "mesh-all":
        result = mesh_all_recovery(
            args.output.resolve(), image=args.image, max_workers=args.max_workers
        )
        success = bool(result["passesExecutionStage"])
    else:
        result = assess_recovery(
            args.output.resolve(),
            args.compact_output.resolve(),
            args.compact_report.resolve(),
        )
        success = result["status"] == "mesh-preflight-passed"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
