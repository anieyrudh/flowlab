from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .verification import (
    StraightPipeSpec,
    VerificationInputError,
    relative_mass_flow_imbalance,
    richardson_grid_convergence,
    straight_pipe_reference,
)

BENCHMARK_ROOT = Path(__file__).resolve().parents[2] / "benchmarks"
FIXTURE_SCHEMA = "flowlab.benchmark_fixture.v1"
REGISTRY_SCHEMA = "flowlab.benchmark_registry.v1"
PENDING_STATUS = "pending-real-run"
VALIDATED_STATUS = "validated"
CAPTURED_OUTPUT_STATUS = "captured"
STRAIGHT_PIPE_ID = "straight-pipe"
QUANTITATIVE_PENDING_STATUS = PENDING_STATUS
QUANTITATIVE_CAPTURED_STATUS = CAPTURED_OUTPUT_STATUS
QUANTITATIVE_STATUSES = {QUANTITATIVE_PENDING_STATUS, QUANTITATIVE_CAPTURED_STATUS}
QUANTITATIVE_VERIFICATION_FIELDS = (
    "reference",
    "applicability",
    "quantitiesOfInterest",
    "units",
    "meshRefinement",
    "timeRefinement",
    "errorAssessment",
    "conservation",
    "provenance",
)
STRAIGHT_PIPE_REQUIRED_QOIS = {
    "pressure-drop": "Pa",
    "mass-flow-rate": "kg/s",
}
STRAIGHT_PIPE_REQUIRED_UNITS = {
    "length": "m",
    "diameter": "m",
    "dynamicViscosity": "Pa*s",
    "density": "kg/m^3",
    "volumetricFlowRate": "m^3/s",
    "massFlowRate": "kg/s",
    "pressureDrop": "Pa",
    "velocity": "m/s",
}
STRAIGHT_PIPE_REQUIRED_RUN_METADATA = {
    "solver-name-and-version",
    "solver-command",
    "runtime-environment",
    "case-manifest-sha256",
    "mesh-artifact-sha256",
    "raw-result-sha256",
    "postprocessing-method-and-version",
}
STRAIGHT_PIPE_REQUIRED_ARTIFACTS = {
    "case-manifest",
    "mesh-artifact",
    "solver-log",
    "raw-result-fields",
    "residual-history",
    "mesh-quality-report",
    "qoi-extraction-table",
}
PROHIBITED_PENDING_KEYS = {
    "actualValue",
    "actualValues",
    "observedValue",
    "observedValues",
    "measuredValue",
    "measuredValues",
    "resultValue",
    "passedAt",
    "validatedAt",
    "solverRunId",
}
PROHIBITED_PENDING_QUANTITATIVE_KEYS = PROHIBITED_PENDING_KEYS | {
    "computedValue",
    "computedValues",
    "errorValue",
    "errorValues",
    "massImbalance",
    "relativeError",
    "relativeErrors",
    "runId",
    "capturedAt",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STRAIGHT_PIPE_EVIDENCE_SCHEMA = "flowlab.straight-pipe-evidence.v1"
STRAIGHT_PIPE_QOI_EXTRACTION_SCHEMA = "flowlab.straight-pipe-qoi-extraction.v1"
STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT = 2100.0


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def load_registry(root: Path = BENCHMARK_ROOT) -> dict[str, Any]:
    return _read_json(root / "registry.json")


def load_benchmark_cases(root: Path = BENCHMARK_ROOT) -> list[dict[str, Any]]:
    registry = load_registry(root)
    cases: list[dict[str, Any]] = []
    for entry in registry.get("cases", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        case = _read_json(root / entry["path"])
        case["_registryEntry"] = entry
        case["_casePath"] = root / entry["path"]
        cases.append(case)
    return cases


def validate_registry(registry: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if registry.get("schema") != REGISTRY_SCHEMA:
        issues.append("Benchmark registry has an unsupported schema.")
    cases = registry.get("cases")
    if not isinstance(cases, list) or not cases:
        issues.append("Benchmark registry must list at least one case.")
        return issues

    seen: set[str] = set()
    for index, entry in enumerate(cases):
        if not isinstance(entry, dict):
            issues.append(f"Registry case {index} must be an object.")
            continue
        case_id = entry.get("id")
        if not isinstance(case_id, str) or not case_id:
            issues.append(f"Registry case {index} must have an id.")
            continue
        if case_id in seen:
            issues.append(f"Registry case id `{case_id}` is duplicated.")
        seen.add(case_id)
        if entry.get("status") not in {PENDING_STATUS, VALIDATED_STATUS}:
            issues.append(f"Registry case `{case_id}` must be pending-real-run or validated.")
        path = entry.get("path")
        if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts:
            issues.append(f"Registry case `{case_id}` must use a safe relative path.")
    return issues


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_non_empty_string(value: Any, *, label: str, case_id: str, issues: list[str]) -> None:
    if not _is_non_empty_string(value):
        issues.append(f"{case_id}: `{label}` must be a non-empty string.")


def _require_string_list(value: Any, *, label: str, case_id: str, issues: list[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(_is_non_empty_string(item) for item in value):
        issues.append(f"{case_id}: `{label}` must be a non-empty list of strings.")
        return []
    return [str(item) for item in value]


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _case_directory(case: dict[str, Any]) -> Path | None:
    raw_path = case.get("_casePath")
    if isinstance(raw_path, Path):
        return raw_path.parent
    if isinstance(raw_path, str):
        return Path(raw_path).parent
    return None


def _safe_artifact_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_captured_real_outputs(
    case: dict[str, Any],
    *,
    case_id: str,
    issues: list[str],
) -> tuple[dict[str, dict[str, Any]], Path | None]:
    """Validate immutable, filesystem-backed captured evidence for promotion."""

    real_outputs = case.get("realOutputs") if isinstance(case.get("realOutputs"), dict) else {}
    records = real_outputs.get("files")
    case_dir = _case_directory(case)
    validated_records: dict[str, dict[str, Any]] = {}
    if not isinstance(records, list) or not records:
        issues.append(f"{case_id}: validated benchmark requires non-empty immutable real-output records.")
        records = []
    if case_dir is None:
        issues.append(f"{case_id}: validated benchmark requires a filesystem-backed case path for artifact verification.")
    resolved_case_dir = case_dir.resolve() if case_dir is not None else None
    seen_paths: set[str] = set()
    for index, record in enumerate(records):
        label = f"realOutputs.files[{index}]"
        if not isinstance(record, dict):
            issues.append(f"{case_id}: `{label}` must be an object with kind, path, and sha256.")
            continue
        kind = record.get("kind")
        artifact_path = _safe_artifact_path(record.get("path"))
        expected_hash = record.get("sha256")
        if not _is_non_empty_string(kind):
            issues.append(f"{case_id}: `{label}.kind` must be a non-empty string.")
            continue
        if artifact_path is None:
            issues.append(f"{case_id}: `{label}.path` must be a safe relative path.")
            continue
        if not _is_sha256(expected_hash):
            issues.append(f"{case_id}: `{label}.sha256` must be a lowercase SHA-256 digest.")
            continue
        path_key = artifact_path.as_posix()
        if path_key in seen_paths:
            issues.append(f"{case_id}: real output artifact path `{path_key}` is duplicated.")
            continue
        seen_paths.add(path_key)
        if kind in validated_records:
            issues.append(f"{case_id}: real output artifact kind `{kind}` is duplicated.")
            continue
        if resolved_case_dir is None:
            continue
        resolved_artifact = (resolved_case_dir / artifact_path).resolve()
        try:
            resolved_artifact.relative_to(resolved_case_dir)
        except ValueError:
            issues.append(f"{case_id}: `{label}.path` escapes the benchmark case directory.")
            continue
        if not resolved_artifact.is_file():
            issues.append(f"{case_id}: captured real output `{path_key}` is missing from the benchmark package.")
            continue
        if _sha256_file(resolved_artifact) != expected_hash:
            issues.append(f"{case_id}: captured real output `{path_key}` does not match its declared SHA-256 digest.")
            continue
        validated_records[str(kind)] = record

    review = real_outputs.get("independentReview")
    if not isinstance(review, dict):
        issues.append(f"{case_id}: validated benchmark requires an independentReview object.")
        return validated_records, None
    if review.get("status") != "approved":
        issues.append(f"{case_id}: independentReview.status must be approved for a validated benchmark.")
    if not _is_non_empty_string(review.get("reviewer")):
        issues.append(f"{case_id}: independentReview.reviewer must be a non-empty string.")
    if not _parse_iso_datetime(review.get("reviewedAt")):
        issues.append(f"{case_id}: independentReview.reviewedAt must be an ISO-8601 timestamp.")
    evidence_package = review.get("evidencePackage")
    if not isinstance(evidence_package, dict):
        issues.append(f"{case_id}: independentReview.evidencePackage must be an object.")
        return validated_records, None
    evidence_path = _safe_artifact_path(evidence_package.get("path"))
    evidence_hash = evidence_package.get("sha256")
    if evidence_path is None or not _is_sha256(evidence_hash):
        issues.append(f"{case_id}: independentReview.evidencePackage requires a safe path and SHA-256 digest.")
        return validated_records, None
    package_record = validated_records.get("evidence-package")
    if (
        not package_record
        or package_record.get("path") != evidence_path.as_posix()
        or package_record.get("sha256") != evidence_hash
    ):
        issues.append(f"{case_id}: evidence package must appear as a verified realOutputs.files artifact of kind evidence-package.")
        return validated_records, None
    if resolved_case_dir is None:
        return validated_records, None
    return validated_records, (resolved_case_dir / evidence_path).resolve()


def _require_pending_quantitative_status(
    section: Any,
    *,
    label: str,
    case_status: Any,
    case_id: str,
    issues: list[str],
) -> dict[str, Any]:
    if not isinstance(section, dict):
        issues.append(f"{case_id}: quantitative verification `{label}` must be an object.")
        return {}
    status = section.get("status")
    if status not in QUANTITATIVE_STATUSES:
        issues.append(f"{case_id}: quantitative verification `{label}.status` must be pending-real-run or captured.")
    elif case_status == PENDING_STATUS and status != QUANTITATIVE_PENDING_STATUS:
        issues.append(f"{case_id}: pending benchmark must keep quantitative verification `{label}.status=pending-real-run`.")
    elif case_status == VALIDATED_STATUS and status != QUANTITATIVE_CAPTURED_STATUS:
        issues.append(f"{case_id}: validated benchmark requires quantitative verification `{label}.status=captured`.")
    return section


def _collect_prohibited_pending_quantitative_keys(value: Any, path: str = "quantitativeVerification") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value):
            child_path = f"{path}.{key}"
            if key in PROHIBITED_PENDING_QUANTITATIVE_KEYS:
                violations.append(child_path)
            violations.extend(_collect_prohibited_pending_quantitative_keys(value[key], child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            violations.extend(_collect_prohibited_pending_quantitative_keys(item, f"{path}[{index}]"))
    return violations


def _validate_straight_pipe_quantitative_verification(case: dict[str, Any], issues: list[str]) -> None:
    case_id = STRAIGHT_PIPE_ID
    contract = case.get("quantitativeVerification")
    if not isinstance(contract, dict):
        issues.append(f"{case_id}: missing required quantitativeVerification contract.")
        return

    for field in QUANTITATIVE_VERIFICATION_FIELDS:
        if field not in contract:
            issues.append(f"{case_id}: quantitativeVerification is missing `{field}`.")

    applicability = _require_pending_quantitative_status(
        contract.get("applicability"),
        label="applicability",
        case_status=case.get("status"),
        case_id=case_id,
        issues=issues,
    )
    if applicability:
        if applicability.get("referenceRegime") != "fully-developed-laminar":
            issues.append(f"{case_id}: applicability.referenceRegime must be fully-developed-laminar.")
        maximum_reynolds = applicability.get("maximumReynoldsNumber")
        if (
            not isinstance(maximum_reynolds, (int, float))
            or isinstance(maximum_reynolds, bool)
            or not 0 < maximum_reynolds <= STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT
        ):
            issues.append(
                f"{case_id}: applicability.maximumReynoldsNumber must be positive and no greater than {STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT:g}."
            )
        if applicability.get("boundaryConditionRequirement") not in {
            "fully-developed-profile-or-periodic-pressure-gradient",
            "fully-developed-profile-or-documented-entrance-length",
        }:
            issues.append(
                f"{case_id}: applicability.boundaryConditionRequirement must declare a fully developed profile, periodic pressure gradient, or documented entrance-length basis."
            )
        _require_string_list(
            applicability.get("evidenceRequired"),
            label="applicability.evidenceRequired",
            case_id=case_id,
            issues=issues,
        )

    reference = contract.get("reference")
    if not isinstance(reference, dict):
        issues.append(f"{case_id}: quantitative verification `reference` must be an object.")
    else:
        if reference.get("id") != "hagen-poiseuille-pressure-drop":
            issues.append(f"{case_id}: quantitative verification reference.id must be hagen-poiseuille-pressure-drop.")
        if reference.get("kind") != "analytic":
            issues.append(f"{case_id}: quantitative verification reference.kind must be analytic.")
        for field in ("equation", "source"):
            _require_non_empty_string(reference.get(field), label=f"quantitativeVerification.reference.{field}", case_id=case_id, issues=issues)
        _require_string_list(
            reference.get("applicability"),
            label="quantitativeVerification.reference.applicability",
            case_id=case_id,
            issues=issues,
        )

    qois = contract.get("quantitiesOfInterest")
    qoi_ids: set[str] = set()
    if not isinstance(qois, list) or not qois:
        issues.append(f"{case_id}: quantitative verification `quantitiesOfInterest` must be a non-empty list.")
    else:
        for index, qoi in enumerate(qois):
            label = f"quantitativeVerification.quantitiesOfInterest[{index}]"
            if not isinstance(qoi, dict):
                issues.append(f"{case_id}: `{label}` must be an object.")
                continue
            qoi_id = qoi.get("id")
            if not _is_non_empty_string(qoi_id):
                issues.append(f"{case_id}: `{label}.id` must be a non-empty string.")
            elif qoi_id in qoi_ids:
                issues.append(f"{case_id}: quantitative verification quantity id `{qoi_id}` is duplicated.")
            else:
                qoi_ids.add(qoi_id)
            for field in ("description", "unit", "reference"):
                _require_non_empty_string(qoi.get(field), label=f"{label}.{field}", case_id=case_id, issues=issues)

        for required_qoi, expected_unit in STRAIGHT_PIPE_REQUIRED_QOIS.items():
            if required_qoi not in qoi_ids:
                issues.append(f"{case_id}: quantitative verification must define QoI `{required_qoi}`.")
                continue
            matching_qoi = next(qoi for qoi in qois if isinstance(qoi, dict) and qoi.get("id") == required_qoi)
            if matching_qoi.get("unit") != expected_unit:
                issues.append(f"{case_id}: QoI `{required_qoi}` must use unit `{expected_unit}`.")

    units = contract.get("units")
    if not isinstance(units, dict):
        issues.append(f"{case_id}: quantitative verification `units` must be an object.")
    else:
        for key, expected_unit in STRAIGHT_PIPE_REQUIRED_UNITS.items():
            if units.get(key) != expected_unit:
                issues.append(f"{case_id}: quantitative verification units.{key} must be `{expected_unit}`.")

    case_status = case.get("status")
    mesh = _require_pending_quantitative_status(
        contract.get("meshRefinement"),
        label="meshRefinement",
        case_status=case_status,
        case_id=case_id,
        issues=issues,
    )
    if mesh:
        if mesh.get("method") != "grid-convergence-index":
            issues.append(f"{case_id}: meshRefinement.method must be grid-convergence-index.")
        if not isinstance(mesh.get("minimumLevels"), int) or mesh["minimumLevels"] < 3:
            issues.append(f"{case_id}: meshRefinement.minimumLevels must be an integer of at least 3.")
        elif mesh["minimumLevels"] != 3:
            issues.append(
                f"{case_id}: current straight-pipe Richardson/GCI validation supports exactly three mesh levels."
            )
        ratio = mesh.get("minimumRefinementRatio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or ratio <= 1:
            issues.append(f"{case_id}: meshRefinement.minimumRefinementRatio must be greater than 1.")
        elif float(ratio) < 1.3:
            issues.append(f"{case_id}: meshRefinement.minimumRefinementRatio must be at least 1.3.")
        maximum_gci = mesh.get("maximumFineGridGciPercent")
        if not _finite_number(maximum_gci) or float(maximum_gci) <= 0.0:
            issues.append(f"{case_id}: meshRefinement.maximumFineGridGciPercent must be a positive number.")
        mesh_qois = _require_string_list(
            mesh.get("quantitiesOfInterest"),
            label="meshRefinement.quantitiesOfInterest",
            case_id=case_id,
            issues=issues,
        )
        if "pressure-drop" not in mesh_qois:
            issues.append(f"{case_id}: meshRefinement must include the pressure-drop QoI.")
        _require_string_list(mesh.get("evidenceRequired"), label="meshRefinement.evidenceRequired", case_id=case_id, issues=issues)

    time = _require_pending_quantitative_status(
        contract.get("timeRefinement"),
        label="timeRefinement",
        case_status=case_status,
        case_id=case_id,
        issues=issues,
    )
    if time:
        if time.get("applicability") != "conditional":
            issues.append(f"{case_id}: timeRefinement.applicability must be conditional.")
        required_when = _require_string_list(
            time.get("requiredWhen"),
            label="timeRefinement.requiredWhen",
            case_id=case_id,
            issues=issues,
        )
        if not {"transient", "pseudo-transient"}.issubset(required_when):
            issues.append(f"{case_id}: timeRefinement.requiredWhen must include transient and pseudo-transient.")
        if time.get("method") != "time-step-convergence":
            issues.append(f"{case_id}: timeRefinement.method must be time-step-convergence.")
        if not isinstance(time.get("minimumLevelsWhenRequired"), int) or time["minimumLevelsWhenRequired"] < 3:
            issues.append(f"{case_id}: timeRefinement.minimumLevelsWhenRequired must be an integer of at least 3.")
        time_qois = _require_string_list(
            time.get("quantitiesOfInterest"),
            label="timeRefinement.quantitiesOfInterest",
            case_id=case_id,
            issues=issues,
        )
        if "pressure-drop" not in time_qois:
            issues.append(f"{case_id}: timeRefinement must include the pressure-drop QoI.")
        _require_string_list(time.get("evidenceRequired"), label="timeRefinement.evidenceRequired", case_id=case_id, issues=issues)
        _require_string_list(
            time.get("notApplicableEvidenceRequired"),
            label="timeRefinement.notApplicableEvidenceRequired",
            case_id=case_id,
            issues=issues,
        )

    error = _require_pending_quantitative_status(
        contract.get("errorAssessment"),
        label="errorAssessment",
        case_status=case_status,
        case_id=case_id,
        issues=issues,
    )
    if error:
        if error.get("method") != "relative-error-against-analytic-reference":
            issues.append(f"{case_id}: errorAssessment.method must be relative-error-against-analytic-reference.")
        _require_non_empty_string(error.get("formula"), label="errorAssessment.formula", case_id=case_id, issues=issues)
        error_qois = _require_string_list(
            error.get("quantitiesOfInterest"),
            label="errorAssessment.quantitiesOfInterest",
            case_id=case_id,
            issues=issues,
        )
        if "pressure-drop" not in error_qois:
            issues.append(f"{case_id}: errorAssessment must include the pressure-drop QoI.")
        thresholds = error.get("acceptanceThresholds")
        has_pressure_drop_threshold = False
        if not isinstance(thresholds, list) or not thresholds:
            issues.append(f"{case_id}: errorAssessment.acceptanceThresholds must be a non-empty list.")
        else:
            for index, threshold in enumerate(thresholds):
                label = f"errorAssessment.acceptanceThresholds[{index}]"
                if not isinstance(threshold, dict):
                    issues.append(f"{case_id}: `{label}` must be an object.")
                    continue
                if threshold.get("qoi") == "pressure-drop":
                    has_pressure_drop_threshold = True
                    if threshold.get("metric") != "relative-error":
                        issues.append(f"{case_id}: pressure-drop error threshold must use metric relative-error.")
                    if threshold.get("unit") != "1":
                        issues.append(f"{case_id}: pressure-drop error threshold must use dimensionless unit `1`.")
                maximum = threshold.get("maximum")
                if not isinstance(maximum, (int, float)) or isinstance(maximum, bool) or not 0 < maximum < 1:
                    issues.append(f"{case_id}: `{label}.maximum` must be a number between 0 and 1.")
            if not has_pressure_drop_threshold:
                issues.append(f"{case_id}: errorAssessment must define a pressure-drop acceptance threshold.")
        _require_string_list(error.get("evidenceRequired"), label="errorAssessment.evidenceRequired", case_id=case_id, issues=issues)

    conservation = _require_pending_quantitative_status(
        contract.get("conservation"),
        label="conservation",
        case_status=case_status,
        case_id=case_id,
        issues=issues,
    )
    if conservation:
        if conservation.get("quantity") != "mass-flow-rate":
            issues.append(f"{case_id}: conservation.quantity must be mass-flow-rate.")
        if conservation.get("metric") != "relative-inlet-outlet-mass-flow-imbalance":
            issues.append(f"{case_id}: conservation.metric must be relative-inlet-outlet-mass-flow-imbalance.")
        _require_non_empty_string(conservation.get("formula"), label="conservation.formula", case_id=case_id, issues=issues)
        if conservation.get("unit") != "1":
            issues.append(f"{case_id}: conservation.unit must be dimensionless `1`.")
        maximum = conservation.get("maximum")
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool) or not 0 < maximum < 1:
            issues.append(f"{case_id}: conservation.maximum must be a number between 0 and 1.")
        _require_string_list(
            conservation.get("evidenceRequired"),
            label="conservation.evidenceRequired",
            case_id=case_id,
            issues=issues,
        )

    provenance = _require_pending_quantitative_status(
        contract.get("provenance"),
        label="provenance",
        case_status=case_status,
        case_id=case_id,
        issues=issues,
    )
    if provenance:
        run_metadata = set(
            _require_string_list(
                provenance.get("requiredRunMetadata"),
                label="provenance.requiredRunMetadata",
                case_id=case_id,
                issues=issues,
            )
        )
        artifacts = set(
            _require_string_list(
                provenance.get("requiredArtifacts"),
                label="provenance.requiredArtifacts",
                case_id=case_id,
                issues=issues,
            )
        )
        missing_metadata = sorted(STRAIGHT_PIPE_REQUIRED_RUN_METADATA - run_metadata)
        if missing_metadata:
            issues.append(f"{case_id}: provenance.requiredRunMetadata is missing {', '.join(missing_metadata)}.")
        missing_artifacts = sorted(STRAIGHT_PIPE_REQUIRED_ARTIFACTS - artifacts)
        if missing_artifacts:
            issues.append(f"{case_id}: provenance.requiredArtifacts is missing {', '.join(missing_artifacts)}.")

    if case_status == PENDING_STATUS:
        prohibited = _collect_prohibited_pending_quantitative_keys(contract)
        if prohibited:
            issues.append(
                f"{case_id}: pending quantitative verification must not include observed numeric CFD data: {', '.join(prohibited)}."
            )


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _straight_pipe_error_limit(case: dict[str, Any]) -> float | None:
    contract = case.get("quantitativeVerification")
    if not isinstance(contract, dict):
        return None
    error = contract.get("errorAssessment")
    thresholds = error.get("acceptanceThresholds") if isinstance(error, dict) else None
    if not isinstance(thresholds, list):
        return None
    for threshold in thresholds:
        if isinstance(threshold, dict) and threshold.get("qoi") == "pressure-drop":
            maximum = threshold.get("maximum")
            if _finite_number(maximum):
                return float(maximum)
    return None


def _straight_pipe_mass_balance_limit(case: dict[str, Any]) -> float | None:
    contract = case.get("quantitativeVerification")
    conservation = contract.get("conservation") if isinstance(contract, dict) else None
    maximum = conservation.get("maximum") if isinstance(conservation, dict) else None
    return float(maximum) if _finite_number(maximum) else None


def _straight_pipe_minimum_refinement_ratio(case: dict[str, Any]) -> float | None:
    contract = case.get("quantitativeVerification")
    mesh = contract.get("meshRefinement") if isinstance(contract, dict) else None
    ratio = mesh.get("minimumRefinementRatio") if isinstance(mesh, dict) else None
    return float(ratio) if _finite_number(ratio) else None


def _straight_pipe_fine_grid_gci_limit(case: dict[str, Any]) -> float | None:
    contract = case.get("quantitativeVerification")
    mesh = contract.get("meshRefinement") if isinstance(contract, dict) else None
    maximum = mesh.get("maximumFineGridGciPercent") if isinstance(mesh, dict) else None
    return float(maximum) if _finite_number(maximum) else None


def _numbers_match(left: Any, right: Any) -> bool:
    return (
        _finite_number(left)
        and _finite_number(right)
        and math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)
    )


def _load_verified_straight_pipe_qoi_extraction(
    case: dict[str, Any],
    *,
    artifact_records: dict[str, dict[str, Any]],
    issues: list[str],
) -> dict[str, Any] | None:
    """Load the machine-readable QoI artifact already hash-verified on disk."""

    case_id = STRAIGHT_PIPE_ID
    record = artifact_records.get("qoi-extraction-table")
    case_dir = _case_directory(case)
    artifact_path = _safe_artifact_path(record.get("path")) if isinstance(record, dict) else None
    if case_dir is None or artifact_path is None:
        issues.append(f"{case_id}: validated evidence requires a verified qoi-extraction-table artifact.")
        return None
    resolved_case_dir = case_dir.resolve()
    qoi_path = (resolved_case_dir / artifact_path).resolve()
    try:
        qoi_path.relative_to(resolved_case_dir)
    except ValueError:
        issues.append(f"{case_id}: qoi-extraction-table artifact escapes the benchmark case directory.")
        return None
    try:
        qoi = _read_json(qoi_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"{case_id}: qoi-extraction-table artifact could not be read: {exc}")
        return None
    if qoi.get("schema") != STRAIGHT_PIPE_QOI_EXTRACTION_SCHEMA:
        issues.append(f"{case_id}: qoi-extraction-table artifact has an unsupported schema.")
        return None
    if qoi.get("caseId") != case_id:
        issues.append(f"{case_id}: qoi-extraction-table artifact caseId must be straight-pipe.")
        return None
    return qoi


def _canonical_straight_pipe_qoi_metrics(
    case: dict[str, Any],
    qoi_extraction: dict[str, Any],
    *,
    artifact_records: dict[str, dict[str, Any]],
    issues: list[str],
) -> dict[str, Any] | None:
    """Recompute V&V metrics from a verified raw-QoI extraction artifact.

    The extraction table must identify its raw result artifact. This establishes
    deterministic internal traceability, while independent review remains the
    human/process control that assesses whether those captured artifacts came
    from the claimed solver run.
    """

    case_id = STRAIGHT_PIPE_ID
    reference_inputs = qoi_extraction.get("referenceInputs")
    required_inputs = (
        "lengthM",
        "diameterM",
        "densityKgPerM3",
        "dynamicViscosityPaS",
        "volumetricFlowRateM3PerS",
    )
    if not isinstance(reference_inputs, dict) or any(
        not _finite_number(reference_inputs.get(field)) for field in required_inputs
    ):
        issues.append(
            f"{case_id}: qoi-extraction-table requires finite SI referenceInputs: {', '.join(required_inputs)}."
        )
        return None
    diameter_m = float(reference_inputs["diameterM"])
    if diameter_m <= 0.0:
        issues.append(f"{case_id}: qoi-extraction-table referenceInputs.diameterM must be positive.")
        return None
    try:
        reference = straight_pipe_reference(
            StraightPipeSpec(
                length_m=float(reference_inputs["lengthM"]),
                radius_m=diameter_m / 2.0,
                density_kg_m3=float(reference_inputs["densityKgPerM3"]),
                dynamic_viscosity_pa_s=float(reference_inputs["dynamicViscosityPaS"]),
                volumetric_flow_rate_m3_s=float(reference_inputs["volumetricFlowRateM3PerS"]),
            )
        )
    except VerificationInputError as exc:
        issues.append(f"{case_id}: qoi-extraction-table referenceInputs are invalid: {exc}")
        return None
    if reference["reynoldsNumber"] >= STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT:
        issues.append(
            f"{case_id}: qoi-extraction-table referenceInputs produce Reynolds number at or above {STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT:g}."
        )
        return None

    raw_result_record = artifact_records.get("raw-result-fields")
    raw_result_hash = raw_result_record.get("sha256") if isinstance(raw_result_record, dict) else None
    if not _is_sha256(raw_result_hash):
        issues.append(f"{case_id}: qoi-extraction-table requires a verified raw-result-fields artifact.")
        return None

    levels = qoi_extraction.get("meshLevels")
    if not isinstance(levels, list) or len(levels) != 3:
        issues.append(
            f"{case_id}: qoi-extraction-table requires exactly three coarse-to-fine mesh levels for the current Richardson/GCI gate."
        )
        return None
    samples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, level in enumerate(levels):
        label = f"qoi-extraction-table.meshLevels[{index}]"
        if not isinstance(level, dict):
            issues.append(f"{case_id}: `{label}` must be an object.")
            return None
        level_id = level.get("id")
        if not _is_non_empty_string(level_id) or str(level_id) in seen_ids:
            issues.append(f"{case_id}: `{label}.id` must be a unique non-empty string.")
            return None
        seen_ids.add(str(level_id))
        if level.get("source") != "solver-produced":
            issues.append(f"{case_id}: `{label}.source` must be solver-produced.")
            return None
        if level.get("sourceArtifactSha256") != raw_result_hash:
            issues.append(
                f"{case_id}: `{label}.sourceArtifactSha256` must match the verified raw-result-fields artifact."
            )
            return None
        if not _finite_number(level.get("characteristicCellSizeM")) or not _finite_number(
            level.get("pressureDropPa")
        ):
            issues.append(
                f"{case_id}: `{label}` requires finite characteristicCellSizeM and pressureDropPa values."
            )
            return None
        samples.append(
            {
                "id": str(level_id),
                "source": "solver-produced",
                "sourceArtifactSha256": raw_result_hash,
                "characteristicCellSizeM": float(level["characteristicCellSizeM"]),
                "value": float(level["pressureDropPa"]),
            }
        )
    sizes = [float(sample["characteristicCellSizeM"]) for sample in samples]
    if not sizes[0] > sizes[1] > sizes[2]:
        issues.append(f"{case_id}: qoi-extraction-table meshLevels must be strictly ordered coarse-to-fine.")
        return None
    try:
        grid = richardson_grid_convergence(samples)
    except VerificationInputError as exc:
        issues.append(f"{case_id}: qoi-extraction-table cannot support Richardson/GCI verification: {exc}")
        return None
    minimum_refinement_ratio = _straight_pipe_minimum_refinement_ratio(case)
    if (
        minimum_refinement_ratio is None
        or float(grid["refinementRatio"]) < minimum_refinement_ratio
    ):
        issues.append(
            f"{case_id}: qoi-extraction-table refinement ratio must meet the declared minimum refinement ratio."
        )
        return None

    conservation = qoi_extraction.get("conservation")
    if not isinstance(conservation, dict):
        issues.append(f"{case_id}: qoi-extraction-table requires signed conservation QoIs.")
        return None
    inlet = conservation.get("inletMassFlowRateKgPerS")
    outlet = conservation.get("outletMassFlowRateKgPerS")
    if (
        not _finite_number(inlet)
        or not _finite_number(outlet)
        or max(abs(float(inlet)), abs(float(outlet))) == 0.0
    ):
        issues.append(
            f"{case_id}: qoi-extraction-table requires non-zero finite signed inlet and outlet mass-flow rates."
        )
        return None
    try:
        relative_imbalance = relative_mass_flow_imbalance(float(inlet), float(outlet))
    except VerificationInputError as exc:
        issues.append(f"{case_id}: qoi-extraction-table conservation is invalid: {exc}")
        return None
    return {
        "reference": reference,
        "samples": samples,
        "grid": grid,
        "finePressureDropPa": float(samples[-1]["value"]),
        "inletMassFlowRateKgPerS": float(inlet),
        "outletMassFlowRateKgPerS": float(outlet),
        "relativeImbalance": relative_imbalance,
    }


def _validate_straight_pipe_captured_evidence(
    case: dict[str, Any],
    *,
    evidence_package_path: Path | None,
    artifact_records: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    """Verify the immutable package needed before a straight-pipe promotion."""

    case_id = STRAIGHT_PIPE_ID
    if evidence_package_path is None:
        issues.append(f"{case_id}: validated benchmark requires a verified straight-pipe evidence package.")
        return
    try:
        evidence = _read_json(evidence_package_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"{case_id}: evidence package could not be read: {exc}")
        return
    if evidence.get("schema") != STRAIGHT_PIPE_EVIDENCE_SCHEMA:
        issues.append(f"{case_id}: evidence package has an unsupported schema.")
    if evidence.get("caseId") != case_id:
        issues.append(f"{case_id}: evidence package caseId must be straight-pipe.")

    qoi_extraction = _load_verified_straight_pipe_qoi_extraction(
        case,
        artifact_records=artifact_records,
        issues=issues,
    )
    computed = (
        _canonical_straight_pipe_qoi_metrics(
            case,
            qoi_extraction,
            artifact_records=artifact_records,
            issues=issues,
        )
        if qoi_extraction is not None
        else None
    )

    applicability = evidence.get("applicability")
    if not isinstance(applicability, dict):
        issues.append(f"{case_id}: evidence package requires an applicability object.")
    else:
        reynolds = applicability.get("reynoldsNumber")
        if not _finite_number(reynolds) or not 0 <= float(reynolds) < STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT:
            issues.append(f"{case_id}: evidence applicability Reynolds number must be finite and below {STRAIGHT_PIPE_LAMINAR_REYNOLDS_LIMIT:g}.")
        if applicability.get("boundaryCondition") not in {
            "fully-developed-profile",
            "periodic-pressure-gradient",
            "documented-entrance-length-exclusion",
        }:
            issues.append(f"{case_id}: evidence applicability must record a valid fully-developed-flow boundary basis.")
        elif computed is not None and not _numbers_match(
            reynolds,
            computed["reference"]["reynoldsNumber"],
        ):
            issues.append(
                f"{case_id}: evidence applicability Reynolds number must match the verified QoI extraction reference inputs."
            )

    mesh = evidence.get("meshRefinement")
    if not isinstance(mesh, dict):
        issues.append(f"{case_id}: evidence package requires meshRefinement.")
    else:
        levels = mesh.get("levels")
        if not isinstance(levels, list) or len(levels) != 3:
            issues.append(f"{case_id}: evidence meshRefinement requires exactly three captured levels.")
        elif computed is not None:
            for index, (reported, canonical) in enumerate(zip(levels, computed["samples"])):
                if not isinstance(reported, dict):
                    issues.append(f"{case_id}: evidence meshRefinement level {index} must be an object.")
                    continue
                if (
                    reported.get("id") != canonical["id"]
                    or not _numbers_match(reported.get("characteristicCellSizeM"), canonical["characteristicCellSizeM"])
                    or not _numbers_match(reported.get("pressureDropPa"), canonical["value"])
                ):
                    issues.append(
                        f"{case_id}: evidence meshRefinement levels must exactly match the verified QoI extraction table."
                    )
                    break
        observed_order = mesh.get("observedOrder")
        if not _finite_number(observed_order) or float(observed_order) <= 0.0:
            issues.append(f"{case_id}: evidence meshRefinement.observedOrder must be positive.")
        elif computed is not None and not _numbers_match(observed_order, computed["grid"]["observedOrder"]):
            issues.append(
                f"{case_id}: evidence meshRefinement.observedOrder must match the recomputed Richardson result."
            )
        fine_gci = mesh.get("fineGridGciPercent")
        fine_gci_limit = _straight_pipe_fine_grid_gci_limit(case)
        if not _finite_number(fine_gci) or float(fine_gci) < 0.0:
            issues.append(f"{case_id}: evidence meshRefinement.fineGridGciPercent must be non-negative.")
        elif computed is not None and not _numbers_match(fine_gci, computed["grid"]["fineGridGciPercent"]):
            issues.append(
                f"{case_id}: evidence meshRefinement.fineGridGciPercent must match the recomputed Richardson result."
            )
        if _finite_number(fine_gci) and (
            fine_gci_limit is None or float(fine_gci) > fine_gci_limit
        ):
            issues.append(f"{case_id}: evidence fine-grid GCI exceeds the declared acceptance limit.")

    time_refinement = evidence.get("timeRefinement")
    if not isinstance(time_refinement, dict):
        issues.append(f"{case_id}: evidence package requires timeRefinement.")
    elif time_refinement.get("method") == "direct-steady":
        if not _is_non_empty_string(time_refinement.get("rationale")):
            issues.append(f"{case_id}: direct-steady evidence requires a temporal-inapplicability rationale.")
        if not _is_non_empty_string(time_refinement.get("solverMethod")):
            issues.append(f"{case_id}: direct-steady evidence requires the declared solverMethod.")
        if time_refinement.get("temporalDiscretization") != "none":
            issues.append(f"{case_id}: direct-steady evidence must declare temporalDiscretization=none.")
    elif time_refinement.get("method") == "time-step-convergence":
        levels = time_refinement.get("levels")
        if not isinstance(levels, list) or len(levels) < 3:
            issues.append(f"{case_id}: time-step convergence evidence requires at least three captured levels.")
    else:
        issues.append(f"{case_id}: evidence timeRefinement must use direct-steady or time-step-convergence.")

    error_assessment = evidence.get("errorAssessment")
    error_limit = _straight_pipe_error_limit(case)
    pressure_error = (
        error_assessment.get("pressureDropRelativeError") if isinstance(error_assessment, dict) else None
    )
    if not isinstance(error_assessment, dict):
        issues.append(f"{case_id}: evidence package requires errorAssessment.")
    elif not _finite_number(pressure_error) or float(pressure_error) < 0:
        issues.append(f"{case_id}: evidence errorAssessment.pressureDropRelativeError must be non-negative and finite.")
    elif computed is not None:
        reference_pressure_drop = computed["reference"]["pressureDropPa"]
        fine_pressure_drop = computed["finePressureDropPa"]
        recomputed_pressure_error = abs(fine_pressure_drop - reference_pressure_drop) / reference_pressure_drop
        if not _numbers_match(
            error_assessment.get("analyticalReferencePressureDropPa"), reference_pressure_drop
        ):
            issues.append(
                f"{case_id}: evidence errorAssessment.analyticalReferencePressureDropPa must match the Hagen-Poiseuille calculation."
            )
        if not _numbers_match(error_assessment.get("fineMeshPressureDropPa"), fine_pressure_drop):
            issues.append(
                f"{case_id}: evidence errorAssessment.fineMeshPressureDropPa must match the verified QoI extraction table."
            )
        if not _numbers_match(pressure_error, recomputed_pressure_error):
            issues.append(
                f"{case_id}: evidence pressureDropRelativeError must match the recomputed fine-mesh analytic error."
            )
        if error_limit is None or recomputed_pressure_error > error_limit:
            issues.append(f"{case_id}: evidence pressure-drop relative error exceeds the declared acceptance limit.")

    conservation = evidence.get("conservation")
    mass_limit = _straight_pipe_mass_balance_limit(case)
    if not isinstance(conservation, dict):
        issues.append(f"{case_id}: evidence package requires conservation.")
    else:
        inlet = conservation.get("inletMassFlowRateKgPerS")
        outlet = conservation.get("outletMassFlowRateKgPerS")
        imbalance = conservation.get("relativeImbalance")
        if not _finite_number(inlet) or not _finite_number(outlet) or max(abs(float(inlet)), abs(float(outlet))) == 0:
            issues.append(f"{case_id}: evidence conservation requires non-zero finite signed inlet and outlet mass-flow rates.")
        elif not _finite_number(imbalance) or float(imbalance) < 0:
            issues.append(f"{case_id}: evidence conservation.relativeImbalance must be non-negative and finite.")
        else:
            recomputed = abs(float(inlet) + float(outlet)) / max(abs(float(inlet)), abs(float(outlet)))
            if not math.isclose(float(imbalance), recomputed, rel_tol=1e-6, abs_tol=1e-12):
                issues.append(f"{case_id}: evidence conservation.relativeImbalance must match the signed-flow calculation.")
            if computed is not None:
                if not _numbers_match(inlet, computed["inletMassFlowRateKgPerS"]) or not _numbers_match(
                    outlet,
                    computed["outletMassFlowRateKgPerS"],
                ):
                    issues.append(
                        f"{case_id}: evidence conservation flows must match the verified QoI extraction table."
                    )
                if not _numbers_match(imbalance, computed["relativeImbalance"]):
                    issues.append(
                        f"{case_id}: evidence conservation.relativeImbalance must match the verified QoI extraction table."
                    )
                recomputed = float(computed["relativeImbalance"])
            if mass_limit is None or recomputed > mass_limit:
                issues.append(f"{case_id}: evidence mass-flow imbalance exceeds the declared acceptance limit.")

    provenance = evidence.get("provenance")
    if not isinstance(provenance, dict):
        issues.append(f"{case_id}: evidence package requires provenance.")
    else:
        for field in (
            "caseManifestSha256",
            "meshArtifactSha256",
            "solverLogSha256",
            "rawResultSha256",
            "qoiExtractionSha256",
        ):
            if not _is_sha256(provenance.get(field)):
                issues.append(f"{case_id}: evidence provenance.{field} must be a lowercase SHA-256 digest.")
        for field in ("solverVersion", "solverCommand"):
            if not _is_non_empty_string(provenance.get(field)):
                issues.append(f"{case_id}: evidence provenance.{field} must be non-empty.")
        expected_artifact_hashes = {
            "case-manifest": provenance.get("caseManifestSha256"),
            "mesh-artifact": provenance.get("meshArtifactSha256"),
            "solver-log": provenance.get("solverLogSha256"),
            "raw-result-fields": provenance.get("rawResultSha256"),
            "qoi-extraction-table": provenance.get("qoiExtractionSha256"),
        }
        for artifact_kind, expected_hash in expected_artifact_hashes.items():
            record = artifact_records.get(artifact_kind)
            if record is None or record.get("sha256") != expected_hash:
                issues.append(f"{case_id}: evidence provenance must match the verified `{artifact_kind}` artifact.")

    missing_artifacts = (STRAIGHT_PIPE_REQUIRED_ARTIFACTS | {"evidence-package"}) - set(artifact_records)
    if missing_artifacts:
        issues.append(f"{case_id}: validated evidence package is missing verified artifacts: {', '.join(sorted(missing_artifacts))}.")


def validate_benchmark_case(case: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    case_id = str(case.get("id") or "<missing>")
    artifact_records: dict[str, dict[str, Any]] = {}
    evidence_package_path: Path | None = None
    if case.get("schema") != FIXTURE_SCHEMA:
        issues.append(f"{case_id}: unsupported benchmark fixture schema.")
    if case.get("status") not in {PENDING_STATUS, VALIDATED_STATUS}:
        issues.append(f"{case_id}: status must be pending-real-run or validated.")

    for field in (
        "expectedInputs",
        "requiredPatches",
        "requiredFields",
        "requiredDiagnostics",
        "acceptanceCriteria",
        "realOutputs",
        "promotion",
    ):
        if field not in case:
            issues.append(f"{case_id}: missing required field `{field}`.")

    for field in ("requiredPatches", "requiredFields", "requiredDiagnostics", "acceptanceCriteria"):
        value = case.get(field)
        if not isinstance(value, list) or not value:
            issues.append(f"{case_id}: `{field}` must be a non-empty list.")

    real_outputs = case.get("realOutputs") if isinstance(case.get("realOutputs"), dict) else {}
    real_output_status = real_outputs.get("status")
    real_output_files = real_outputs.get("files")
    if real_output_status not in {PENDING_STATUS, CAPTURED_OUTPUT_STATUS}:
        issues.append(f"{case_id}: realOutputs.status must be pending-real-run or captured.")
    if not isinstance(real_output_files, list):
        issues.append(f"{case_id}: realOutputs.files must be a list.")

    criteria = case.get("acceptanceCriteria") if isinstance(case.get("acceptanceCriteria"), list) else []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            issues.append(f"{case_id}: each acceptance criterion must be an object.")
            continue
        for field in ("id", "description", "evidenceRequired", "status"):
            if field not in criterion:
                issues.append(f"{case_id}: acceptance criterion is missing `{field}`.")
        if criterion.get("status") not in {PENDING_STATUS, "passed", "failed"}:
            issues.append(f"{case_id}: acceptance criterion status must be pending-real-run, passed, or failed.")
        evidence_required = criterion.get("evidenceRequired")
        if not isinstance(evidence_required, list) or not evidence_required:
            issues.append(f"{case_id}: acceptance criterion must list evidenceRequired.")

    if case.get("status") == PENDING_STATUS:
        if real_output_status != PENDING_STATUS:
            issues.append(f"{case_id}: pending benchmark must keep realOutputs.status=pending-real-run.")
        if real_output_files:
            issues.append(f"{case_id}: pending benchmark must not list real output files.")
        for criterion in criteria:
            if isinstance(criterion, dict) and criterion.get("status") != PENDING_STATUS:
                issues.append(f"{case_id}: pending benchmark must not mark acceptance criteria as passed or failed.")
            prohibited = sorted(PROHIBITED_PENDING_KEYS.intersection(criterion))
            if prohibited:
                issues.append(f"{case_id}: pending criterion must not include fabricated observed values: {', '.join(prohibited)}.")

    if case.get("status") == VALIDATED_STATUS:
        if real_output_status != CAPTURED_OUTPUT_STATUS or not real_output_files:
            issues.append(f"{case_id}: validated benchmark requires captured real output files.")
        if not criteria or any(isinstance(criterion, dict) and criterion.get("status") != "passed" for criterion in criteria):
            issues.append(f"{case_id}: validated benchmark requires passed acceptance criteria.")
        artifact_records, evidence_package_path = _validate_captured_real_outputs(
            case,
            case_id=case_id,
            issues=issues,
        )

    if case.get("id") == STRAIGHT_PIPE_ID:
        _validate_straight_pipe_quantitative_verification(case, issues)
        if case.get("status") == VALIDATED_STATUS:
            _validate_straight_pipe_captured_evidence(
                case,
                evidence_package_path=evidence_package_path,
                artifact_records=artifact_records,
                issues=issues,
            )

    return issues


def validate_all_benchmarks(root: Path = BENCHMARK_ROOT) -> list[str]:
    issues = validate_registry(load_registry(root))
    for case in load_benchmark_cases(root):
        entry = case.get("_registryEntry") if isinstance(case.get("_registryEntry"), dict) else {}
        if entry.get("id") != case.get("id"):
            issues.append(f"{case.get('id')}: registry id and case id must match.")
        if entry.get("status") != case.get("status"):
            issues.append(f"{case.get('id')}: registry status and case status must match.")
        issues.extend(validate_benchmark_case(case))
    return issues


def trusted_validated_benchmark_ids(root: Path = BENCHMARK_ROOT) -> set[str]:
    """Return only the current, typed V&V benchmark IDs eligible for acceleration.

    This is intentionally stricter than a status string. Until another benchmark
    has a case-specific quantitative validator, only straight-pipe may ever be
    returned here. The separate surrogate-release registry remains mandatory,
    so this internal traceability check alone cannot authorize a model result.
    """

    if validate_registry(load_registry(root)):
        return set()
    trusted: set[str] = set()
    for case in load_benchmark_cases(root):
        case_id = case.get("id")
        registry_entry = case.get("_registryEntry")
        if (
            case.get("status") != VALIDATED_STATUS
            or not isinstance(case_id, str)
            or case_id != STRAIGHT_PIPE_ID
            or not isinstance(registry_entry, dict)
            or registry_entry.get("status") != VALIDATED_STATUS
        ):
            continue
        if not validate_benchmark_case(case):
            trusted.add(case_id)
    return trusted
