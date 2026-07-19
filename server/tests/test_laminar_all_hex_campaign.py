from __future__ import annotations

import json
import math
import threading
import time

from server.flowlab.laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    ResourceBudget,
    WeightedScheduler,
    build_manifest,
    campaign_cells,
    gate_catalog,
    issue_json_schema,
    make_issue,
    physical_cells,
    run_dry_run,
    validate_manifest,
    _json_safe,
)


def test_physical_matrix_is_complete_and_balanced() -> None:
    cells = physical_cells()

    assert len(cells) == 72
    assert len({cell["cellId"] for cell in cells}) == 72
    for reynolds in (4.17, 16.67, 66.7):
        assert sum(
            cell["parameters"]["reynoldsNumberHeightBased"] == reynolds
            for cell in cells
        ) == 24
    assert sum(cell["resourceClass"] == "fine" for cell in cells) == 24


def test_manifest_freezes_sources_gates_and_all_planned_lanes() -> None:
    manifest = build_manifest()
    checks = validate_manifest(manifest)

    assert all(checks.values())
    assert manifest["campaignId"] == CAMPAIGN_ID
    assert manifest["primaryScientificCellCount"] == 78
    assert manifest["laneCounts"] == {
        "affine": 3,
        "negative-controls": 6,
        "non-affine-mms": 3,
        "physical-envelope": 72,
        "product-contract": 4,
        "reproducibility": 4,
    }
    assert manifest["gateCatalog"] == gate_catalog()
    assert manifest["externalInputs"]["experimentalDataset"]["status"] == "missing"
    assert manifest["solver"]["instrumentedLibrary"]["sha256"]
    assert manifest["solver"]["instrumentedLibrary"]["scope"] == "affine lane only"


def test_json_safe_retains_failed_observation_as_strict_json() -> None:
    value = _json_safe({"finite": 1.0, "infinite": math.inf, "nested": [-math.inf]})

    assert value == {"finite": 1.0, "infinite": None, "nested": [None]}
    json.dumps(value, allow_nan=False)


def test_manifest_validation_detects_source_fingerprint_drift() -> None:
    manifest = build_manifest()
    manifest["sourceRegister"]["records"][0]["sha256"] = "0" * 64

    checks = validate_manifest(manifest)

    assert checks["sourceHashesMatch"] is False


def test_issue_schema_and_records_distinguish_conflicts_and_interference() -> None:
    schema = issue_json_schema()
    issue = make_issue(
        "TEST-1",
        kind="interference",
        severity="P1",
        summary="Iterative error changes apparent order.",
        interacting_factors=("iterativeError", "observedSpatialOrder"),
        conflicts_with=("TEST-2",),
        next_diagnostic="Repeat the smallest failing cell with a tighter fixed solve budget.",
    )

    assert "interference" in schema["properties"]["kind"]["enum"]
    assert issue["kind"] == "interference"
    assert issue["conflictsWith"] == ["TEST-2"]


def test_weighted_scheduler_honors_capacity_and_single_fine_worker(tmp_path) -> None:
    cells = campaign_cells()[:2] + [
        cell for cell in campaign_cells() if cell["resourceClass"] == "fine"
    ][:2]
    lock = threading.Lock()
    active_weight = 0
    fine_workers = 0
    maximum_weight = 0
    maximum_fine = 0

    def worker(cell):
        nonlocal active_weight, fine_workers, maximum_weight, maximum_fine
        with lock:
            active_weight += cell["resourceWeight"]
            fine_workers += int(cell["resourceClass"] == "fine")
            maximum_weight = max(maximum_weight, active_weight)
            maximum_fine = max(maximum_fine, fine_workers)
        time.sleep(0.01)
        with lock:
            active_weight -= cell["resourceWeight"]
            fine_workers -= int(cell["resourceClass"] == "fine")
        return {"cellId": cell["cellId"], "status": "accepted"}

    scheduler = WeightedScheduler(
        ResourceBudget(capacity=6, max_workers=4, max_fine_workers=1),
        tmp_path / "events.jsonl",
    )
    results = scheduler.run(cells, worker)

    assert len(results) == len(cells)
    assert maximum_weight <= 6
    assert maximum_fine == 1


def test_dry_run_materializes_complete_fail_closed_control_plane(tmp_path) -> None:
    report = run_dry_run(tmp_path / "campaign", capacity=4)
    root = tmp_path / "campaign"

    assert report["status"] == "accepted"
    assert report["readyForNumericalLaunch"] is True
    assert report["readyForEmpiricalAcceptance"] is False
    assert report["scientificSolvesExecuted"] is False
    assert report["plannedCellCount"] == 92
    assert report["dryRunPassedCellCount"] == 92
    manifest = json.loads((root / "campaign-manifest.json").read_text())
    assert manifest["primaryScientificCellCount"] == 78
    issues = [json.loads(line) for line in (root / "issues.jsonl").read_text().splitlines()]
    assert {issue["issueId"] for issue in issues} == {"CAM-0001", "CAM-0002"}
    assert (root / "interference-register.json").is_file()
    assert (root / "campaign-report.json").is_file()
