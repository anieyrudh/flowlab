from __future__ import annotations

from server.flowlab.laminar_all_hex_v3_observer import (
    first_sustained_joint_pass_iteration,
    sustained_window,
)
from server.flowlab.laminar_all_hex_v4_contract import (
    CAMPAIGN_ID,
    MINIMUM_ITERATIONS,
    build_manifest,
    remedy_evidence,
    termination_contract,
    validate_manifest,
)


def _passing_history(end: int) -> list[dict[str, float | int]]:
    return [
        {"iteration": iteration, "Ux": 9.0e-7, "p": 9.0e-9}
        for iteration in range(1, end + 1)
    ]


def test_v4_manifest_freezes_common_floor_without_changing_scientific_limits() -> None:
    manifest = build_manifest()
    checks = validate_manifest(manifest)

    assert manifest["campaignId"] == CAMPAIGN_ID
    assert manifest["mobile"] == {"inScope": False, "changes": "none"}
    assert manifest["primaryScientificCellCount"] == 78
    assert len(
        [cell for cell in manifest["cells"] if cell["lane"] == "physical-envelope"]
    ) == 72
    assert manifest["campaignProfile"]["scientificThresholdChanges"] == []
    assert termination_contract()["minimumIterations"] == 1300
    assert all(checks.values()), checks


def test_v4_remedy_evidence_is_pinned_and_supportive() -> None:
    evidence = remedy_evidence()

    assert evidence["exists"] is True
    assert evidence["status"] == "supports-common-floor"
    assert evidence["checks"]["allFourGroupsPassExistingGciGates"] is True


def test_common_floor_blocks_an_early_sustained_pass() -> None:
    before_floor = _passing_history(1299)
    at_floor = _passing_history(1300)

    assert sustained_window(
        before_floor,
        minimum_iterations=MINIMUM_ITERATIONS,
        minimum_is_window_start=False,
    )["passed"] is False
    assert sustained_window(
        at_floor,
        minimum_iterations=MINIMUM_ITERATIONS,
        minimum_is_window_start=False,
    )["passed"] is True
    assert first_sustained_joint_pass_iteration(
        at_floor,
        minimum_iterations=MINIMUM_ITERATIONS,
        minimum_is_window_start=False,
    ) == 1300
