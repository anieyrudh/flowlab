from __future__ import annotations

from pathlib import Path

from server.flowlab.frozen_surface_continuation import continuation_decision
from server.flowlab.open_boundary_campaign import (
    MmsDefinition,
    PipeObservation,
    RefinementObservation,
    evaluate_mms_stage,
    evaluate_open_boundary_campaign,
    write_campaign_report,
)


def _mms_levels() -> list[RefinementObservation]:
    return [
        RefinementObservation("coarse", 0.04, True, 8e-3, 6e-3, 8e-3, 1e-10, 1e-9, 1e-10, 1e-9, {"fields": "coarse"}),
        RefinementObservation("medium", 0.02, True, 2e-3, 1.5e-3, 2e-3, 1e-10, 1e-9, 1e-10, 1e-9, {"fields": "medium"}),
        RefinementObservation("fine", 0.01, True, 5e-4, 3.75e-4, 5e-4, 1e-10, 1e-9, 1e-10, 1e-9, {"fields": "fine"}),
    ]


def _pipe_levels() -> list[PipeObservation]:
    return [
        PipeObservation(**level.__dict__, static_pressure_drop_relative_error=8e-3, wall_force_relative_imbalance=1e-9)
        for level in _mms_levels()
    ]


def test_mms_definition_is_divergence_free_and_forcing_is_explicit() -> None:
    definition = MmsDefinition()
    assert definition.velocity(0.3, 0.5, 0.5) == (1.0, 0.0, 0.0)
    manifest = definition.manifest()
    assert manifest["divergence"] == "0"
    assert "momentumSource" in manifest


def test_campaign_accepts_three_grid_evidence_and_unlocks_frozen_surface(tmp_path: Path) -> None:
    report = evaluate_open_boundary_campaign(_mms_levels(), _pipe_levels())
    assert report["status"] == "accepted"
    assert report["frozenSurfaceContinuation"]["status"] == "unlocked"
    path = tmp_path / "open-boundary-report.json"
    write_campaign_report(path, report)
    decision = continuation_decision(path)
    assert decision["status"] == "unlocked"
    assert decision["schedule"] == [64, 96, 128]


def test_mms_rejects_operator_error_before_open_pipe() -> None:
    levels = _mms_levels()
    bad = [*levels]
    bad[-1] = RefinementObservation(**{**bad[-1].__dict__, "pressure_l2_error": 0.002})
    report = evaluate_mms_stage(bad)
    assert report["status"] == "rejected"
    assert report["diagnosis"] == "operator-source-implementation"
