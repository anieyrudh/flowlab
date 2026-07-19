from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server.flowlab.reference_cases import build_reference_case_import_plan, list_reference_cases


def test_reference_case_registry_lists_su2_and_openfoam_sources() -> None:
    registry = list_reference_cases()

    assert registry["schema"] == "flowlab.reference_cases.registry.v1"
    case_ids = {case["id"] for case in registry["cases"]}
    assert "su2-euler-naca0012" in case_ids
    assert "openfoam-icofoam-cavity" in case_ids
    assert all(case["source"]["expectedFiles"] for case in registry["cases"])


def test_reference_case_import_plan_is_handoff_not_execution() -> None:
    plan = build_reference_case_import_plan("su2-euler-naca0012")

    assert plan["schema"] == "flowlab.reference_case_import_plan.v1"
    assert plan["solver"] == "su2"
    assert plan["source"]["repo"] == "su2code/TestCases"
    assert "FlowLab does not auto-download third-party repositories in the local solver service." in plan["limitations"]
    assert any("Run FlowLab generation/validation" in action for action in plan["requiredUserActions"])


def test_reference_case_import_plan_rejects_unknown_case() -> None:
    with pytest.raises(KeyError):
        build_reference_case_import_plan("missing-case")


def test_reference_case_api_returns_registry_and_import_plan() -> None:
    client = TestClient(app_module.app)

    registry_response = client.get("/api/reference-cases")
    assert registry_response.status_code == 200
    assert registry_response.json()["schema"] == "flowlab.reference_cases.registry.v1"

    plan_response = client.post("/api/reference-cases/openfoam-icofoam-cavity/import-plan")
    assert plan_response.status_code == 200
    assert plan_response.json()["solver"] == "openfoam"

    missing_response = client.post("/api/reference-cases/not-real/import-plan")
    assert missing_response.status_code == 404
