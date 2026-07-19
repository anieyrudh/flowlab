from __future__ import annotations

import json

from server.flowlab.laminar_all_hex_campaign import build_manifest
from server.flowlab.laminar_all_hex_controls import (
    campaign_contract_issues,
    physical_envelope_contains,
)
from server.flowlab.validated_benchmark import all_hex_campaign_promotion_decision


def test_campaign_contract_detects_boundary_mutation() -> None:
    frozen = build_manifest()
    mutated = json.loads(json.dumps(frozen))
    mutated["solver"]["boundaryContract"] = "mutated"
    assert campaign_contract_issues(mutated, frozen) == [
        "Frozen campaign contract mismatch: solverBoundaryContract."
    ]


def test_out_of_envelope_parameters_are_not_contained() -> None:
    manifest = build_manifest()
    assert physical_envelope_contains(
        manifest,
        {
            "reynoldsNumberHeightBased": 1000.0,
            "flowDirection": "forward",
            "lengthToHeightRatio": 1.0,
            "axialCellAspectRatio": 1.0,
            "cellsPerHeight": 48,
        },
    ) is False


def test_promotion_decision_requires_every_gate_and_explicit_authorization() -> None:
    report = {
        "schema": "flowlab.laminar-all-hex-campaign-report.v1",
        "campaignId": "laminar-all-hex-v2",
        "status": "accepted",
        "promotionAuthorized": True,
        "checks": {
            "everyScientificCellAccountedFor": True,
            "noInfrastructureGaps": True,
            "affineAccepted": True,
            "nonAffineMmsAccepted": True,
            "physicalEnvelopeAccepted": True,
            "experimentalDatasetPinned": True,
            "reproducibilityAccepted": True,
            "negativeControlsAccepted": True,
            "productContractAccepted": True,
        },
    }
    assert all_hex_campaign_promotion_decision(report)["promotionAuthorized"] is True
    report["checks"]["experimentalDatasetPinned"] = False
    assert all_hex_campaign_promotion_decision(report)["promotionAuthorized"] is False


def test_promotion_decision_accepts_v4_final_assessment_identity() -> None:
    report = {
        "schema": "flowlab.laminar-all-hex-final-assessment.v2",
        "campaignId": "laminar-all-hex-v4",
        "status": "accepted",
        "promotionAuthorized": True,
        "checks": {
            "everyScientificCellAccountedFor": True,
            "noInfrastructureGaps": True,
            "affineAccepted": True,
            "nonAffineMmsAccepted": True,
            "physicalEnvelopeAccepted": True,
            "experimentalDatasetPinned": True,
            "reproducibilityAccepted": True,
            "negativeControlsAccepted": True,
            "productContractAccepted": True,
        },
    }

    assert all_hex_campaign_promotion_decision(report)["promotionAuthorized"] is True
