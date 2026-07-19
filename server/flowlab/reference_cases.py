from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parents[2] / "reference_cases" / "registry.json"
REQUIRED_CASE_KEYS = {"id", "label", "solver", "source", "physics", "importMode", "notes"}


def load_reference_case_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    validate_reference_case_registry(registry)
    return registry


def validate_reference_case_registry(registry: dict[str, Any]) -> None:
    if registry.get("schema") != "flowlab.reference_cases.registry.v1":
        raise ValueError("Reference case registry schema must be flowlab.reference_cases.registry.v1.")
    cases = registry.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Reference case registry must include at least one case.")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Reference case {index} must be an object.")
        missing = REQUIRED_CASE_KEYS - set(case)
        if missing:
            raise ValueError(f"Reference case {index} is missing: {', '.join(sorted(missing))}.")
        case_id = str(case["id"])
        if case_id in seen:
            raise ValueError(f"Duplicate reference case id: {case_id}.")
        seen.add(case_id)
        source = case.get("source")
        if not isinstance(source, dict) or not source.get("url") or not source.get("casePath"):
            raise ValueError(f"Reference case {case_id} needs a source url and casePath.")
        expected_files = source.get("expectedFiles")
        if not isinstance(expected_files, list) or not expected_files:
            raise ValueError(f"Reference case {case_id} needs expected source files.")


def list_reference_cases() -> dict[str, Any]:
    return load_reference_case_registry()


def build_reference_case_import_plan(case_id: str) -> dict[str, Any]:
    registry = load_reference_case_registry()
    selected = next((case for case in registry["cases"] if case["id"] == case_id), None)
    if selected is None:
        raise KeyError(case_id)
    source = selected["source"]
    return {
        "schema": "flowlab.reference_case_import_plan.v1",
        "caseId": selected["id"],
        "label": selected["label"],
        "solver": selected["solver"],
        "physics": selected["physics"],
        "source": source,
        "importMode": selected["importMode"],
        "requiredUserActions": [
            "Review the upstream case license and solver-version compatibility.",
            "Provide a local copy of the tutorial/TestCases directory or paste/copy the specific config and mesh artifacts into a FlowLab-managed case folder.",
            "Run FlowLab generation/validation so meshio round-trip, solver runtime checks, diagnostics contracts, and result parsing evidence are recorded.",
        ],
        "generatedArtifacts": {
            "handoffPlan": f"reference_cases/import_plans/{selected['id']}.json",
            "expectedSourceFiles": source["expectedFiles"],
        },
        "limitations": [
            "FlowLab does not auto-download third-party repositories in the local solver service.",
            "Imported tutorial files are not treated as CAD-reviewed production geometry.",
            "Reference results must be promoted only after a real local run captures logs, fields, diagnostics, and mesh evidence.",
        ],
        "notes": selected.get("notes", []),
    }
