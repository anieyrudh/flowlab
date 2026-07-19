from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server import app as app_module
from server.flowlab import adapters
from server.flowlab.execution import JobManager
from server.flowlab.validated_benchmark import validated_benchmark_registry
from server.flowlab.validated_preset import (
    build_validated_open_boundary_case,
    validate_validated_open_boundary_case,
)


def test_pinned_benchmark_registry_contains_hashed_analysis_only_evidence() -> None:
    registry = validated_benchmark_registry()
    benchmark = registry["benchmarks"][0]
    assert registry["schema"] == "flowlab.validated_benchmark_registry.v1"
    assert benchmark["capabilityStatus"] == "validated-benchmark"
    assert benchmark["promotionBlocked"] is True
    assert len(benchmark["evidence"]) == 3
    assert all(len(reference["sha256"]) == 64 for reference in benchmark["evidence"])
    assert "Not validated for open boundaries" in benchmark["limits"][0]


def test_open_boundary_regime_is_blocked_by_the_full_campaign_gate() -> None:
    registry = validated_benchmark_registry()
    benchmark = registry["benchmarks"][1]

    assert benchmark["id"] == "laminar-open-boundary-all-hex-v1"
    assert benchmark["scientificStatus"] == "campaign-promotion-blocked"
    assert benchmark["capabilityStatus"] == "experimental"
    assert benchmark["promotionBlocked"] is True
    assert len(benchmark["evidence"]) == 4
    assert all(len(reference["sha256"]) == 64 for reference in benchmark["evidence"])
    assert "not a general open-boundary" in benchmark["limits"][0]
    gate = benchmark["metrics"]["allHexCampaignGate"]
    assert gate["checks"]["physicalEnvelopeAccepted"] is True
    assert gate["checks"]["reproducibilityAccepted"] is True
    assert gate["checks"]["negativeControlsAccepted"] is True
    assert gate["checks"]["productContractAccepted"] is True
    assert gate["checks"]["experimentalDatasetPinned"] is False
    assert gate["blockingReasons"] == [
        "Campaign gate `experimentalDatasetPinned` has not passed."
    ]


def test_generated_jobs_are_experimental_and_promotion_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(app_module, "JOB_MANAGER", JobManager(runtime_root=tmp_path))
    app_module.CASES.clear()
    client = TestClient(app_module.app)
    generated = client.post("/api/cases/generate", json={
        "project": {"name": "experimental", "nodes": {}, "edges": {}, "solver": {}},
        "solver": "openfoam",
        "advancedMode": "incompressible-navier-stokes",
    })
    assert generated.status_code == 200
    assert generated.json()["evidenceCapability"]["status"] == "experimental"
    queued = client.post("/api/jobs", json=generated.json())
    assert queued.status_code == 200
    assert queued.json()["evidenceCapability"]["promotionBlocked"] is True
    refused = client.post(f"/api/jobs/{queued.json()['id']}/promote?claim=production-ready")
    assert refused.status_code == 409


def test_validated_preset_is_unavailable_until_campaign_promotion() -> None:
    try:
        build_validated_open_boundary_case()
    except ValueError as exc:
        assert "does not authorize a runnable preset" in str(exc)
    else:
        raise AssertionError("blocked campaign unexpectedly minted a validated preset")


def test_runnable_validated_endpoint_fails_closed_while_campaign_is_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(app_module, "JOB_MANAGER", JobManager(runtime_root=tmp_path))
    app_module.CASES.clear()
    client = TestClient(app_module.app)

    response = client.post("/api/benchmarks/validated/laminar-open-boundary-all-hex-v1/jobs")

    assert response.status_code == 409
    assert "does not authorize a runnable preset" in response.json()["detail"]


def test_unknown_validated_benchmark_has_no_runnable_preset() -> None:
    client = TestClient(app_module.app)
    response = client.post("/api/benchmarks/validated/periodic-all-hex-straight-pipe-v1/jobs")
    assert response.status_code == 404
