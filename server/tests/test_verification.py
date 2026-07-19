from __future__ import annotations

import math

import pytest

from server.flowlab.acceleration import (
    build_bounded_surrogate_policy,
    build_openfoam_parallel_plan,
    evaluate_surrogate_eligibility,
)
from server.flowlab.verification import (
    StraightPipeSpec,
    VerificationInputError,
    build_straight_pipe_3d_reference_bundle,
    evaluate_straight_pipe_pressure_drop,
    hagen_poiseuille_pressure_drop_pa,
    richardson_grid_convergence,
    straight_pipe_reference,
)


def _spec() -> StraightPipeSpec:
    return StraightPipeSpec(
        length_m=1.0,
        radius_m=0.01,
        density_kg_m3=1000.0,
        dynamic_viscosity_pa_s=0.001,
        volumetric_flow_rate_m3_s=1.0e-5,
    )


def _samples() -> list[dict[str, float | str]]:
    reference = hagen_poiseuille_pressure_drop_pa(_spec())
    return [
        {
            "id": "coarse",
            "source": "solver-produced",
            "sourceArtifactSha256": "1" * 64,
            "characteristicCellSizeM": 0.04,
            "value": reference + 0.016,
        },
        {
            "id": "medium",
            "source": "solver-produced",
            "sourceArtifactSha256": "2" * 64,
            "characteristicCellSizeM": 0.02,
            "value": reference + 0.004,
        },
        {
            "id": "fine",
            "source": "solver-produced",
            "sourceArtifactSha256": "3" * 64,
            "characteristicCellSizeM": 0.01,
            "value": reference + 0.001,
        },
    ]


def _analysis_provenance() -> dict[str, str]:
    return {
        "caseManifestSha256": "a" * 64,
        "meshArtifactSha256": "b" * 64,
        "solverLogSha256": "c" * 64,
        "rawResultSha256": "d" * 64,
        "qoiExtractionSha256": "e" * 64,
        "solverVersion": "OpenFOAM v11",
        "solverCommand": "foamRun -solver incompressibleFluid",
    }


def test_hagen_poiseuille_reference_has_expected_units_and_regime() -> None:
    reference = straight_pipe_reference(_spec())

    assert math.isclose(
        reference["pressureDropPa"],
        8.0 * 0.001 * 1.0 * 1.0e-5 / (math.pi * 0.01**4),
    )
    assert reference["meanVelocityMPerS"] > 0.0
    assert reference["reynoldsNumber"] < 2100.0


def test_3d_pipe_bundle_is_pending_and_preserves_real_geometry_contract() -> None:
    bundle = build_straight_pipe_3d_reference_bundle(_spec(), [0.005, 0.0025, 0.00125])

    assert bundle["manifest"]["scientificStatus"] == "unverified"
    assert bundle["manifest"]["geometry"]["dimension"] == 3
    assert "independent-review-before-validation-claim" in bundle["manifest"]["requiredEvidence"]
    assert "fully developed inlet profile" in bundle["manifest"]["boundaryConditionRequirement"]
    mesh = bundle["files"]["meshes/mesh-3/pipe.geo"]
    assert "Cylinder(1)" in mesh
    assert 'Physical Surface("inlet")' in mesh
    assert 'Physical Surface("outlet")' in mesh
    assert 'Physical Surface("wall")' in mesh
    assert "Mesh.Algorithm3D = 4" in mesh
    assert "Mesh.OptimizeThreshold = 0.2" in mesh
    assert "Mesh.Smoothing = 20" in mesh
    assert "Mesh.MshFileVersion = 2.2" in mesh


def test_3d_pipe_reference_rejects_transition_or_turbulent_envelopes() -> None:
    high_reynolds_spec = StraightPipeSpec(
        length_m=1.0,
        radius_m=0.01,
        density_kg_m3=1000.0,
        dynamic_viscosity_pa_s=0.001,
        volumetric_flow_rate_m3_s=1.0e-3,
    )

    with pytest.raises(VerificationInputError, match="Reynolds number below"):
        build_straight_pipe_3d_reference_bundle(high_reynolds_spec, [0.005, 0.0025, 0.00125])


def test_richardson_grid_convergence_reports_known_second_order_sequence() -> None:
    result = richardson_grid_convergence(_samples())

    assert result["refinementRatio"] == 2.0
    assert math.isclose(result["observedOrder"], 2.0)
    assert result["fineGridGciPercent"] > 0.0


def test_verification_rejects_non_solver_produced_samples() -> None:
    samples = _samples()
    samples[-1]["source"] = "typed-by-user"

    with pytest.raises(VerificationInputError, match="solver-produced"):
        richardson_grid_convergence(samples)


def test_metric_analysis_never_self_promotes_to_a_passed_or_validated_result() -> None:
    result = evaluate_straight_pipe_pressure_drop(
        _spec(),
        _samples(),
        pressure_drop_relative_error_limit=0.01,
        inlet_mass_flow_rate_kg_s=0.01,
        outlet_mass_flow_rate_kg_s=-0.009999,
        mass_balance_relative_error_limit=0.001,
        provenance=_analysis_provenance(),
    )

    assert result["scientificStatus"] == "analysis-only"
    assert result["thresholdsMet"] is True
    assert result["validated"] is False
    assert result["reviewRequired"] is True


def test_metric_analysis_requires_provenance_and_artifact_assertions() -> None:
    samples = _samples()
    del samples[0]["sourceArtifactSha256"]

    with pytest.raises(VerificationInputError, match="sourceArtifactSha256"):
        evaluate_straight_pipe_pressure_drop(
            _spec(),
            samples,
            pressure_drop_relative_error_limit=0.01,
            inlet_mass_flow_rate_kg_s=0.01,
            outlet_mass_flow_rate_kg_s=-0.01,
            mass_balance_relative_error_limit=0.001,
            provenance=_analysis_provenance(),
        )


def _policy() -> dict[str, object]:
    return build_bounded_surrogate_policy(
        geometry_families=["straight-circular-pipe"],
        qois=["pressure-drop", "mean-velocity"],
        reynolds_range=[1.0, 2000.0],
        validated_benchmark_ids=["straight-pipe-v1"],
        training_provenance={
            "solver": "OpenFOAM",
            "solverVersion": "v11",
            "containerImageDigest": "sha256:example",
            "meshRecipeHash": "mesh-recipe-example",
            "datasetManifestHash": "dataset-manifest-example",
        },
    )


def test_surrogate_policy_requires_authoritative_cfd_for_out_of_envelope_input() -> None:
    result = evaluate_surrogate_eligibility(
        {
            "geometryFamily": "airfoil",
            "qois": ["pressure-drop"],
            "reynoldsNumber": 3000.0,
            "physics": {
                "flowState": "steady",
                "phase": "single",
                "fluidModel": "incompressible-newtonian",
                "regime": "laminar",
            },
        },
        _policy(),
    )

    assert result["eligible"] is False
    assert result["execution"] == "openfoam-authoritative-cfd"
    assert "geometry-outside-envelope" in result["violations"]


def test_surrogate_policy_fails_closed_until_a_trusted_release_exists() -> None:
    result = evaluate_surrogate_eligibility(
        {
            "geometryFamily": "straight-circular-pipe",
            "qois": ["pressure-drop"],
            "reynoldsNumber": 1200.0,
            "physics": {
                "flowState": "steady",
                "phase": "single",
                "fluidModel": "incompressible-newtonian",
                "regime": "laminar",
            },
        },
        _policy(),
    )

    assert result["eligible"] is False
    assert result["execution"] == "openfoam-authoritative-cfd"
    assert "untrusted-validated-benchmark-provenance" in result["violations"]
    assert "no-registered-surrogate-release" in result["violations"]


def test_parallel_plan_demands_a_baseline_measurement() -> None:
    plan = build_openfoam_parallel_plan(ranks=4, decomposition="scotch")

    assert plan["performanceStatus"] == "benchmark-required"
    assert plan["speedupClaim"] is None


def test_surrogate_policy_fails_closed_when_policy_or_request_is_malformed() -> None:
    policy = _policy()
    policy["envelope"] = {
        "geometryFamilies": ["straight-circular-pipe"],
        "qois": ["pressure-drop"],
        "reynoldsRange": ["not-a-number", "also-not-a-number"],
        "physics": {
            "flowState": "steady",
            "phase": "single",
            "fluidModel": "incompressible-newtonian",
            "regime": "laminar",
        },
    }

    result = evaluate_surrogate_eligibility(
        {
            "geometryFamily": "straight-circular-pipe",
            "qois": [{"not": "a-qoi-id"}],
            "reynoldsNumber": 1200.0,
            "physics": {
                "flowState": "steady",
                "phase": "single",
                "fluidModel": "incompressible-newtonian",
                "regime": "laminar",
            },
        },
        policy,
    )

    assert result["eligible"] is False
    assert result["status"] == "authoritative-cfd-required"


def test_surrogate_policy_rejects_an_empty_qoi_request() -> None:
    result = evaluate_surrogate_eligibility(
        {
            "geometryFamily": "straight-circular-pipe",
            "qois": [],
            "reynoldsNumber": 1200.0,
            "physics": {
                "flowState": "steady",
                "phase": "single",
                "fluidModel": "incompressible-newtonian",
                "regime": "laminar",
            },
        },
        _policy(),
    )

    assert result["eligible"] is False
    assert "missing-request-qois" in result["violations"]
