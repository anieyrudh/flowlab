from __future__ import annotations

from pathlib import Path

from server.flowlab.laminar_all_hex_final_assessment import physical_scope_summary


def test_physical_scope_summary_retains_failed_outer_envelope() -> None:
    campaign = Path(
        "benchmarks/cases/open-boundary/campaigns/"
        "2026-07-16-laminar-all-hex-v2-campaign-r3"
    ).resolve()
    summary = physical_scope_summary(campaign)
    assert summary["cellCount"] == 72
    assert summary["acceptedCellCount"] == 66
    assert len(summary["scientificFailures"]) == 6
    assert summary["allForceFieldMeshMassAndLinearChecksPass"] is True
    nested = {row["name"]: row for row in summary["nestedNumericalScopes"]}
    assert nested["Re<=16.67 full factorial"]["allAccepted"] is True
    assert nested["Re=66.7 and L/H=1"]["allAccepted"] is False
