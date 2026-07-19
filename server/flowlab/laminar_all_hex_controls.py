"""Execute the campaign's negative and desktop product-contract controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    IMAGE,
    IMAGE_DIGEST,
    _canonical_sha256,
    _sha256,
    _write_json,
)
from .laminar_all_hex_campaign_runner import _run_command
from .open_boundary_laminar_force_benchmark import PlanePoiseuille, _case_files
from .validated_benchmark import (
    OPEN_BOUNDARY_BENCHMARK_ID,
    all_hex_campaign_promotion_decision,
    experimental_capability,
    validated_benchmark_registry,
)
from .validated_preset import immutable_preset_file_issues


SCHEMA = "flowlab.laminar-all-hex-controls.v1"
_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_CAMPAIGN_ARTIFACTS = (
    "campaign-manifest.json",
    "campaign-manifest.sha256",
    "campaign-report.json",
    "gate-catalog.json",
    "source-register.json",
    "lanes/affine/lane-report.json",
    "lanes/non-affine-mms/lane-report.json",
    "lanes/physical-envelope/lane-report.json",
)


def campaign_contract_issues(
    candidate: dict[str, Any], frozen: dict[str, Any]
) -> list[str]:
    issues = []
    comparisons = (
        ("solverBoundaryContract", candidate.get("solver", {}).get("boundaryContract"), frozen.get("solver", {}).get("boundaryContract")),
        ("gateCatalog", candidate.get("gateCatalog"), frozen.get("gateCatalog")),
        ("sourceRegister", candidate.get("sourceRegister"), frozen.get("sourceRegister")),
        ("solverImageDigest", candidate.get("solver", {}).get("digest"), frozen.get("solver", {}).get("digest")),
    )
    for name, actual, expected in comparisons:
        if _canonical_sha256(actual) != _canonical_sha256(expected):
            issues.append(f"Frozen campaign contract mismatch: {name}.")
    return issues


def physical_envelope_contains(manifest: dict[str, Any], parameters: dict[str, Any]) -> bool:
    envelope = manifest["physicalEnvelope"]
    return all(
        parameters.get(name) in envelope[name]
        for name in (
            "reynoldsNumberHeightBased",
            "flowDirection",
            "lengthToHeightRatio",
            "axialCellAspectRatio",
            "cellsPerHeight",
        )
    )


def _detected(control: str, evidence: Any, detected: bool) -> dict[str, Any]:
    return {
        "control": control,
        "expectedOutcome": "rejected",
        "observedOutcome": "rejected" if detected else "accepted-unexpectedly",
        "passed": detected,
        "evidence": evidence,
    }


def _invalid_mesh_control(output: Path) -> dict[str, Any]:
    control_root = output / "invalid-mesh-topology"
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-v",
        f"{_ROOT.resolve()}:/workspace:ro",
        "-v",
        f"{output.resolve()}:/controls",
        "-w",
        "/workspace",
        "--entrypoint",
        "/bin/bash",
        IMAGE,
        "-lc",
        (
            "source /opt/openfoam11/etc/bashrc && export PYTHONPATH=/workspace && "
            "python3 -m server.flowlab.laminar_all_hex_mesh_control "
            "--output /controls/invalid-mesh-topology"
        ),
    ]
    log = output / "invalid-mesh-topology-container.log"
    exit_code = _run_command(command, log, timeout=600)
    report_path = control_root / "control-report.json"
    if not report_path.is_file():
        return _detected(
            "invalid-mesh-topology",
            {"containerExitCode": exit_code, "log": str(log)},
            False,
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["containerExitCode"] = exit_code
    report["containerLogSha256"] = _sha256(log)
    return _detected("invalid-mesh-topology", report, report.get("passed") is True)


def run_controls(campaign: Path, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((campaign / "campaign-manifest.json").read_text(encoding="utf-8"))
    campaign_report = json.loads((campaign / "campaign-report.json").read_text(encoding="utf-8"))

    mutated_boundary = json.loads(json.dumps(manifest))
    mutated_boundary["solver"]["boundaryContract"] = "zeroGradient outlet pressure"
    boundary_issues = campaign_contract_issues(mutated_boundary, manifest)

    mutated_gate = json.loads(json.dumps(manifest))
    mutated_gate["gateCatalog"]["physical"]["massRelativeImbalance"] = 1.0
    gate_issues = campaign_contract_issues(mutated_gate, manifest)

    corrupt_source = json.loads(json.dumps(manifest))
    corrupt_source["sourceRegister"]["records"][0]["sha256"] = "0" * 64
    source_issues = campaign_contract_issues(corrupt_source, manifest)

    available = {
        relative for relative in _REQUIRED_CAMPAIGN_ARTIFACTS if (campaign / relative).is_file()
    }
    missing_simulation = available - {"lanes/physical-envelope/lane-report.json"}
    missing = sorted(set(_REQUIRED_CAMPAIGN_ARTIFACTS) - missing_simulation)

    wrong_image = json.loads(json.dumps(manifest))
    wrong_image["solver"]["digest"] = "sha256:" + "0" * 64
    image_issues = campaign_contract_issues(wrong_image, manifest)

    negative_controls = [
        _detected("mutated-boundary-condition", boundary_issues, "Frozen campaign contract mismatch: solverBoundaryContract." in boundary_issues),
        _detected("mutated-gate-threshold", gate_issues, "Frozen campaign contract mismatch: gateCatalog." in gate_issues),
        _detected("corrupt-source-hash", source_issues, "Frozen campaign contract mismatch: sourceRegister." in source_issues),
        _detected("missing-artifact", {"missing": missing}, missing == ["lanes/physical-envelope/lane-report.json"]),
        _invalid_mesh_control(output),
        _detected("wrong-image-digest", image_issues, "Frozen campaign contract mismatch: solverImageDigest." in image_issues),
    ]

    incomplete = json.loads(json.dumps(campaign_report))
    incomplete["checks"].pop("experimentalDatasetPinned", None)
    incomplete_decision = all_hex_campaign_promotion_decision(incomplete)

    out_of_envelope = {
        "reynoldsNumberHeightBased": 1000.0,
        "flowDirection": "forward",
        "lengthToHeightRatio": 1.0,
        "axialCellAspectRatio": 1.0,
        "cellsPerHeight": 48,
    }
    out_is_contained = physical_envelope_contains(manifest, out_of_envelope)
    generic_capability = experimental_capability()

    expected_files = _case_files(12, PlanePoiseuille())
    mutated_files = dict(expected_files)
    mutated_files["0/p"] += "\n// negative-control mutation\n"
    preset_issues = immutable_preset_file_issues(mutated_files, expected_files)

    complete = json.loads(json.dumps(campaign_report))
    complete["status"] = "accepted"
    complete["promotionAuthorized"] = True
    complete["checks"] = {name: True for name in complete["checks"]}
    complete_decision = all_hex_campaign_promotion_decision(complete)
    actual_decision = all_hex_campaign_promotion_decision(campaign_report)
    registry_entry = next(
        item
        for item in validated_benchmark_registry()["benchmarks"]
        if item["id"] == OPEN_BOUNDARY_BENCHMARK_ID
    )

    product_contracts = [
        _detected("incomplete-evidence", incomplete_decision, incomplete_decision["promotionAuthorized"] is False),
        _detected(
            "out-of-envelope-parameters",
            {"parameters": out_of_envelope, "contained": out_is_contained, "capability": generic_capability.model_dump(mode="json")},
            not out_is_contained and generic_capability.promotionBlocked is True,
        ),
        _detected(
            "mutated-preset-file",
            preset_issues,
            preset_issues == ["Validated preset file `0/p` does not match the immutable contract."],
        ),
        {
            "control": "accepted-bounded-envelope",
            "expectedOutcome": "positive path accepts only a complete report while the current incomplete campaign remains blocked",
            "observedOutcome": {
                "syntheticCompleteDecision": complete_decision,
                "currentDecision": actual_decision,
                "desktopPromotionBlocked": registry_entry["promotionBlocked"],
            },
            "passed": complete_decision["promotionAuthorized"] is True
            and actual_decision["promotionAuthorized"] is False
            and registry_entry["promotionBlocked"] is True,
        },
    ]
    checks = {
        "sixNegativeControlsReject": len(negative_controls) == 6
        and all(row["passed"] for row in negative_controls),
        "fourProductContractsPass": len(product_contracts) == 4
        and all(row["passed"] for row in product_contracts),
        "desktopPromotionRemainsBlocked": registry_entry["promotionBlocked"] is True,
        "pinnedImageDigestPreserved": manifest["solver"]["digest"] == IMAGE_DIGEST,
    }
    report = {
        "schema": SCHEMA,
        "campaignId": manifest.get("campaignId", CAMPAIGN_ID),
        "status": "accepted" if all(checks.values()) else "rejected",
        "sourceCampaign": str(campaign),
        "sourceManifestSha256": _sha256(campaign / "campaign-manifest.json"),
        "checks": checks,
        "negativeControls": negative_controls,
        "productContracts": product_contracts,
        "promotionAuthorized": False,
    }
    _write_json(output / "controls-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_controls(args.campaign.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
