from __future__ import annotations

import math

from server.flowlab.open_boundary_mms_redesign import (
    AffineCrossflowMms,
    NON_DEGENERACY_LIMIT,
    preflight,
)


def test_affine_mms_continuum_terms_close_without_source_pressure_cancellation() -> None:
    spec = AffineCrossflowMms()

    assert spec.velocity(0.3, 0.5, 0.7) == (1.05, 0.1, 0.0)
    assert spec.convection() == (0.010000000000000002, 0.0, 0.0)
    assert spec.pressure_gradient() == (-0.001, 0.0, 0.0)
    assert math.isclose(spec.momentum_source()[0], 0.009)
    ratio = abs(spec.momentum_source()[0]) / (
        abs(spec.convection()[0]) + abs(spec.pressure_gradient()[0])
    )
    assert ratio >= NON_DEGENERACY_LIMIT


def test_affine_mms_boundary_traces_match_openfoam_conditions() -> None:
    spec = AffineCrossflowMms()

    assert spec.pressure(1.0, 0.4, 0.5) == 0.0
    assert spec.velocity(0.0, 0.0, 0.5) == (1.0, 0.1, 0.0)
    assert spec.velocity(1.0, 1.0, 0.5) == (1.1, 0.1, 0.0)
    assert spec.velocity_gradient()[0] == (0.0, 0.1, 0.0)


def test_affine_mms_preflight_authorizes_only_the_one_iteration_probe() -> None:
    report = preflight()

    assert report["status"] == "authorized"
    assert all(report["checks"].values())
    assert report["pressureReference"]["fixedValuePatches"] == ["outlet"]
    assert report["pressureReference"]["pRefCellRequired"] is False
    assert all(
        level["passes"]
        for level in report["structuredGaussLinearAudit"]["levels"]
    )
    assert report["nextStage"]["oneIterationOpenFoamProbe"] == "authorized"
    assert report["nextStage"]["coarseValidation"].startswith("blocked")


def test_affine_mms_preflight_rejects_degenerate_source_pressure_split() -> None:
    report = preflight(
        AffineCrossflowMms(
            shear_rate_per_s=0.01,
            crossflow_velocity_m_s=0.1,
        )
    )

    assert report["status"] == "blocked"
    assert report["checks"]["sourcePressureSplitIsNonDegenerate"] is False
    assert report["nextStage"]["oneIterationOpenFoamProbe"] == "blocked"
