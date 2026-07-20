from __future__ import annotations

import json
from pathlib import Path

from server.flowlab import fda_nozzle_re500_v3_mesh_preflight as base
from server.flowlab import fda_nozzle_re500_v3_mesh_preflight_recovery as recovery


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT / "docs/validation/fda-nozzle-re500/V3_MESH_PREFLIGHT_R2_CONTRACT.json"
)
PREDECESSOR = (
    ROOT / "docs/validation/fda-nozzle-re500/V3_MESH_PREFLIGHT_CONTRACT.json"
)


def test_recovery_contract_hashes_corrected_runner_and_failed_predecessor() -> None:
    contract = recovery._verify_recovery_contract(CONTRACT)
    assert base._sha256(CONTRACT) == recovery.RECOVERY_CONTRACT_SHA256
    assert contract["correctedRunner"]["sha256"] == base._sha256(
        ROOT / contract["correctedRunner"]["path"]
    )
    assert contract["recoveryOf"]["contractSha256"] == base._sha256(PREDECESSOR)
    assert contract["recoveryOf"]["scientificGateEvaluated"] is False
    assert contract["correctedRunner"]["scientificContractChanged"] is False


def test_recovery_preserves_every_scientific_and_execution_gate() -> None:
    original = json.loads(PREDECESSOR.read_text(encoding="utf-8"))
    recovery_contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for key in (
        "pressureDisposition",
        "contextOfUse",
        "candidateFamily",
        "meshAcceptance",
        "authorization",
        "scientificPromotionAuthorized",
        "desktopPromotionAuthorized",
        "promotionAuthorized",
    ):
        assert recovery_contract[key] == original[key]
    assert (
        recovery_contract["geometryAcceptance"][
            "fineMaximumAbsoluteRelativeError"
        ]
        == original["geometryAcceptance"]["fineMaximumAbsoluteRelativeError"]
        == 0.01
    )
    assert (
        recovery_contract["geometryAcceptance"]["quantities"]
        == original["geometryAcceptance"]["quantities"]
    )
    assert recovery_contract["execution"] == original["execution"]


def test_recovery_uses_a_new_raw_and_compact_output_boundary() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["outputPolicy"] == {
        "rawCampaign": "benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-mesh-preflight-r2",
        "rawCampaignTrackedByGit": False,
        "compactAssessment": "docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-mesh-preflight-r2.json",
        "compactReport": "docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-mesh-preflight-r2.md",
        "newHypothesisRequiresNewOutputDirectory": True,
    }


def test_recovery_identity_is_scoped_and_restored() -> None:
    original = (base.CONTRACT_SCHEMA, base.CONTRACT_SHA256, base.CAMPAIGN_ID)
    with recovery._recovery_identity():
        assert base.CONTRACT_SCHEMA == recovery.RECOVERY_SCHEMA
        assert base.CONTRACT_SHA256 == recovery.RECOVERY_CONTRACT_SHA256
        assert base.CAMPAIGN_ID == recovery.RECOVERY_CAMPAIGN_ID
    assert (base.CONTRACT_SCHEMA, base.CONTRACT_SHA256, base.CAMPAIGN_ID) == original
