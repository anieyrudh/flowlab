from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from server.flowlab.benchmark_fixtures import (
    PENDING_STATUS,
    load_benchmark_cases,
    load_registry,
    trusted_validated_benchmark_ids,
    validate_all_benchmarks,
    validate_benchmark_case,
    validate_registry,
)


BENCHMARK_ROOT = Path(__file__).resolve().parents[2] / "benchmarks"


def _case(case_id: str) -> dict:
    return next(case for case in load_benchmark_cases() if case["id"] == case_id)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _replace_artifact_digest(case: dict, kind: str, digest: str) -> None:
    for record in case["realOutputs"]["files"]:
        if record["kind"] == kind:
            record["sha256"] = digest
            return
    raise AssertionError(f"missing artifact record: {kind}")


def _refresh_evidence_package_digest(case: dict, evidence_path: Path) -> None:
    digest = _sha256_file(evidence_path)
    _replace_artifact_digest(case, "evidence-package", digest)
    case["realOutputs"]["independentReview"]["evidencePackage"]["sha256"] = digest


def _captured_straight_pipe_case(tmp_path: Path) -> tuple[dict, Path, Path]:
    """Build an isolated, internally consistent captured package for validator tests.

    These are synthetic test artifacts only. The repository's benchmark fixture
    remains pending and contains no claimed CFD output.
    """

    case = deepcopy(_case("straight-pipe"))
    case["status"] = "validated"
    for section in case["quantitativeVerification"].values():
        if isinstance(section, dict) and "status" in section:
            section["status"] = "captured"
    for criterion in case["acceptanceCriteria"]:
        criterion["status"] = "passed"

    case_path = tmp_path / "benchmark.json"
    case_path.write_text("{}\n", encoding="utf-8")
    case["_casePath"] = case_path
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    basic_artifacts = {
        "case-manifest": ("case-manifest.json", "{\"schema\":\"flowlab-case-manifest\"}\n"),
        "mesh-artifact": ("meshes.tar", "three captured mesh levels\n"),
        "solver-log": ("solver.log", "OpenFOAM v11 solver output\n"),
        "raw-result-fields": ("raw-fields.tar", "mesh-1.vtu\nmesh-2.vtu\nmesh-3.vtu\n"),
        "residual-history": ("residuals.csv", "iteration,residual\n1,0.1\n"),
        "mesh-quality-report": ("mesh-quality.json", "{\"checked\":true}\n"),
    }
    artifact_paths: dict[str, Path] = {}
    for kind, (name, contents) in basic_artifacts.items():
        path = artifacts_dir / name
        path.write_text(contents, encoding="utf-8")
        artifact_paths[kind] = path

    reference_inputs = {
        "lengthM": 1.0,
        "diameterM": 0.01,
        "densityKgPerM3": 1000.0,
        "dynamicViscosityPaS": 0.001,
        "volumetricFlowRateM3PerS": 1.0e-5,
    }
    reference_pressure_drop = (
        128.0
        * reference_inputs["dynamicViscosityPaS"]
        * reference_inputs["lengthM"]
        * reference_inputs["volumetricFlowRateM3PerS"]
        / (math.pi * reference_inputs["diameterM"] ** 4)
    )
    raw_result_hash = _sha256_file(artifact_paths["raw-result-fields"])
    mesh_levels = [
        {
            "id": "coarse",
            "source": "solver-produced",
            "sourceArtifactSha256": raw_result_hash,
            "characteristicCellSizeM": 0.004,
            "pressureDropPa": reference_pressure_drop - 0.16,
        },
        {
            "id": "medium",
            "source": "solver-produced",
            "sourceArtifactSha256": raw_result_hash,
            "characteristicCellSizeM": 0.002,
            "pressureDropPa": reference_pressure_drop - 0.04,
        },
        {
            "id": "fine",
            "source": "solver-produced",
            "sourceArtifactSha256": raw_result_hash,
            "characteristicCellSizeM": 0.001,
            "pressureDropPa": reference_pressure_drop - 0.01,
        },
    ]
    inlet_mass_flow = 0.01
    outlet_mass_flow = -0.009999
    qoi_extraction = {
        "schema": "flowlab.straight-pipe-qoi-extraction.v1",
        "caseId": "straight-pipe",
        "referenceInputs": reference_inputs,
        "meshLevels": mesh_levels,
        "conservation": {
            "inletMassFlowRateKgPerS": inlet_mass_flow,
            "outletMassFlowRateKgPerS": outlet_mass_flow,
        },
    }
    qoi_path = artifacts_dir / "qoi-extraction.json"
    _write_json(qoi_path, qoi_extraction)
    artifact_paths["qoi-extraction-table"] = qoi_path
    fine_pressure_drop = mesh_levels[-1]["pressureDropPa"]
    pressure_error = abs(fine_pressure_drop - reference_pressure_drop) / reference_pressure_drop
    observed_order = math.log(abs((mesh_levels[0]["pressureDropPa"] - mesh_levels[1]["pressureDropPa"]) / (mesh_levels[1]["pressureDropPa"] - mesh_levels[2]["pressureDropPa"]))) / math.log(2.0)
    fine_gci = (
        1.25
        * abs(mesh_levels[2]["pressureDropPa"] - mesh_levels[1]["pressureDropPa"])
        / mesh_levels[2]["pressureDropPa"]
        / (2.0**observed_order - 1.0)
        * 100.0
    )
    relative_imbalance = abs(inlet_mass_flow + outlet_mass_flow) / max(
        abs(inlet_mass_flow), abs(outlet_mass_flow)
    )
    reynolds = (
        reference_inputs["densityKgPerM3"]
        * (reference_inputs["volumetricFlowRateM3PerS"] / (math.pi * (reference_inputs["diameterM"] / 2.0) ** 2))
        * reference_inputs["diameterM"]
        / reference_inputs["dynamicViscosityPaS"]
    )
    evidence = {
        "schema": "flowlab.straight-pipe-evidence.v1",
        "caseId": "straight-pipe",
        "applicability": {
            "reynoldsNumber": reynolds,
            "boundaryCondition": "fully-developed-profile",
        },
        "meshRefinement": {
            "levels": [
                {
                    "id": level["id"],
                    "characteristicCellSizeM": level["characteristicCellSizeM"],
                    "pressureDropPa": level["pressureDropPa"],
                }
                for level in mesh_levels
            ],
            "observedOrder": observed_order,
            "fineGridGciPercent": fine_gci,
        },
        "timeRefinement": {
            "method": "direct-steady",
            "solverMethod": "steady SIMPLE",
            "temporalDiscretization": "none",
            "rationale": "The captured run uses a direct steady discretization.",
        },
        "errorAssessment": {
            "analyticalReferencePressureDropPa": reference_pressure_drop,
            "fineMeshPressureDropPa": fine_pressure_drop,
            "pressureDropRelativeError": pressure_error,
        },
        "conservation": {
            "inletMassFlowRateKgPerS": inlet_mass_flow,
            "outletMassFlowRateKgPerS": outlet_mass_flow,
            "relativeImbalance": relative_imbalance,
        },
        "provenance": {
            "caseManifestSha256": _sha256_file(artifact_paths["case-manifest"]),
            "meshArtifactSha256": _sha256_file(artifact_paths["mesh-artifact"]),
            "solverLogSha256": _sha256_file(artifact_paths["solver-log"]),
            "rawResultSha256": raw_result_hash,
            "qoiExtractionSha256": _sha256_file(qoi_path),
            "solverVersion": "OpenFOAM v11",
            "solverCommand": "foamRun -solver incompressibleFluid",
        },
    }
    evidence_path = artifacts_dir / "evidence.json"
    _write_json(evidence_path, evidence)
    artifact_paths["evidence-package"] = evidence_path
    case["realOutputs"] = {
        "status": "captured",
        "files": [
            {
                "kind": kind,
                "path": str(path.relative_to(tmp_path)),
                "sha256": _sha256_file(path),
            }
            for kind, path in artifact_paths.items()
        ],
        "independentReview": {
            "status": "approved",
            "reviewer": "test-reviewer",
            "reviewedAt": "2026-07-13T00:00:00Z",
            "evidencePackage": {
                "path": str(evidence_path.relative_to(tmp_path)),
                "sha256": _sha256_file(evidence_path),
            },
        },
    }
    return case, qoi_path, evidence_path


def test_benchmark_registry_lists_expected_pending_cases() -> None:
    registry = load_registry()
    issues = validate_registry(registry)
    ids = {entry["id"] for entry in registry["cases"]}

    assert issues == []
    assert {
        "straight-pipe",
        "venturi",
        "heated-channel",
        "lid-driven-cavity-placeholder",
        "nozzle-placeholder",
    }.issubset(ids)
    assert {entry["status"] for entry in registry["cases"]} == {PENDING_STATUS}


def test_all_benchmark_metadata_is_valid_and_pending_real_run() -> None:
    issues = validate_all_benchmarks()
    cases = load_benchmark_cases()

    assert issues == []
    assert cases
    for case in cases:
        assert case["status"] == PENDING_STATUS
        assert case["realOutputs"]["status"] == PENDING_STATUS
        assert case["realOutputs"]["files"] == []
        assert case["requiredPatches"]
        assert case["requiredFields"]
        assert case["requiredDiagnostics"]
        assert case["acceptanceCriteria"]
        assert all(criterion["status"] == PENDING_STATUS for criterion in case["acceptanceCriteria"])


def test_straight_pipe_has_a_complete_pending_quantitative_vv_contract() -> None:
    case = _case("straight-pipe")
    contract = case["quantitativeVerification"]

    assert contract["reference"]["id"] == "hagen-poiseuille-pressure-drop"
    assert contract["reference"]["kind"] == "analytic"
    assert {qoi["id"] for qoi in contract["quantitiesOfInterest"]} == {"pressure-drop", "mass-flow-rate"}
    assert contract["units"]["pressureDrop"] == "Pa"
    assert contract["units"]["massFlowRate"] == "kg/s"
    assert contract["meshRefinement"]["minimumLevels"] >= 3
    assert contract["meshRefinement"]["maximumFineGridGciPercent"] == 1.0
    assert contract["timeRefinement"]["minimumLevelsWhenRequired"] >= 3
    assert contract["errorAssessment"]["acceptanceThresholds"][0]["maximum"] == 0.05
    assert contract["conservation"]["maximum"] == 0.001
    assert all(section["status"] == PENDING_STATUS for section in (
        contract["applicability"],
        contract["meshRefinement"],
        contract["timeRefinement"],
        contract["errorAssessment"],
        contract["conservation"],
        contract["provenance"],
    ))


def test_schema_requires_the_quantitative_contract_for_straight_pipe() -> None:
    schema = json.loads((BENCHMARK_ROOT / "flowlab_benchmark.schema.json").read_text(encoding="utf-8"))

    assert "quantitativeVerification" in schema["properties"]
    assert any(
        condition.get("if", {}).get("properties", {}).get("id", {}).get("const") == "straight-pipe"
        and "quantitativeVerification" in condition.get("then", {}).get("required", [])
        for condition in schema["allOf"]
    )


def test_pending_benchmark_rejects_fake_passed_acceptance_claim() -> None:
    case = deepcopy(load_benchmark_cases()[0])
    case["acceptanceCriteria"][0]["status"] = "passed"
    case["acceptanceCriteria"][0]["observedValue"] = 0.0

    issues = validate_benchmark_case(case)

    assert any("pending benchmark must not mark acceptance criteria" in issue for issue in issues)
    assert any("fabricated observed values" in issue for issue in issues)


def test_validated_benchmark_requires_captured_real_outputs() -> None:
    case = deepcopy(load_benchmark_cases()[1])
    case["status"] = "validated"
    for criterion in case["acceptanceCriteria"]:
        criterion["status"] = "passed"

    issues = validate_benchmark_case(case)

    assert any("validated benchmark requires captured real output files" in issue for issue in issues)


def test_straight_pipe_quantitative_contract_rejects_missing_or_invalid_vv_evidence() -> None:
    case = deepcopy(_case("straight-pipe"))
    contract = case["quantitativeVerification"]
    del contract["reference"]["equation"]
    contract["units"]["pressureDrop"] = "kPa"
    contract["meshRefinement"]["minimumLevels"] = 2
    contract["meshRefinement"]["minimumRefinementRatio"] = 1.2
    del contract["meshRefinement"]["maximumFineGridGciPercent"]
    contract["timeRefinement"]["requiredWhen"] = ["transient"]
    contract["errorAssessment"]["acceptanceThresholds"][0]["unit"] = "Pa"
    contract["conservation"]["maximum"] = 1
    contract["provenance"]["requiredArtifacts"].remove("solver-log")

    issues = validate_benchmark_case(case)

    assert issues == validate_benchmark_case(case)
    assert any("reference.equation" in issue for issue in issues)
    assert any("units.pressureDrop" in issue for issue in issues)
    assert any("meshRefinement.minimumLevels" in issue for issue in issues)
    assert any("minimumRefinementRatio must be at least 1.3" in issue for issue in issues)
    assert any("maximumFineGridGciPercent must be a positive number" in issue for issue in issues)
    assert any("requiredWhen must include transient and pseudo-transient" in issue for issue in issues)
    assert any("pressure-drop error threshold must use dimensionless" in issue for issue in issues)
    assert any("conservation.maximum" in issue for issue in issues)
    assert any("provenance.requiredArtifacts is missing solver-log" in issue for issue in issues)


def test_pending_straight_pipe_quantitative_contract_rejects_observed_data() -> None:
    case = deepcopy(_case("straight-pipe"))
    case["quantitativeVerification"]["errorAssessment"]["observedValue"] = 0.02

    issues = validate_benchmark_case(case)

    assert any("pending quantitative verification must not include observed numeric CFD data" in issue for issue in issues)


def test_validated_straight_pipe_requires_captured_quantitative_sections() -> None:
    case = deepcopy(_case("straight-pipe"))
    case["status"] = "validated"
    case["realOutputs"] = {"status": "captured", "files": ["outputs/solver.log"]}
    for criterion in case["acceptanceCriteria"]:
        criterion["status"] = "passed"

    issues = validate_benchmark_case(case)

    assert any("validated benchmark requires quantitative verification `meshRefinement.status=captured`" in issue for issue in issues)


def test_validated_benchmark_rejects_fake_captured_output_names_and_review_claims() -> None:
    case = deepcopy(_case("straight-pipe"))
    case["status"] = "validated"
    for criterion in case["acceptanceCriteria"]:
        criterion["status"] = "passed"
    for section in case["quantitativeVerification"].values():
        if isinstance(section, dict) and "status" in section:
            section["status"] = "captured"
    case["realOutputs"] = {
        "status": "captured",
        "files": ["invented.log"],
        "independentReview": {
            "status": "approved",
            "reviewer": "invented",
            "reviewedAt": "2026-07-13T00:00:00Z",
            "evidencePackage": {"path": "invented.json", "sha256": "0" * 64},
        },
    }

    issues = validate_benchmark_case(case)

    assert any("immutable real-output records" in issue or "must be an object with kind, path, and sha256" in issue for issue in issues)
    assert any("evidence package" in issue for issue in issues)


def test_validated_straight_pipe_recomputes_metrics_from_hashed_qoi_artifact(tmp_path: Path) -> None:
    case, _qoi_path, _evidence_path = _captured_straight_pipe_case(tmp_path)

    assert validate_benchmark_case(case) == []


def test_straight_pipe_rejects_self_attested_grid_metrics_after_hash_refresh(tmp_path: Path) -> None:
    case, _qoi_path, evidence_path = _captured_straight_pipe_case(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["meshRefinement"]["observedOrder"] = 1.0
    _write_json(evidence_path, evidence)
    _refresh_evidence_package_digest(case, evidence_path)

    issues = validate_benchmark_case(case)

    assert any("observedOrder must match the recomputed Richardson result" in issue for issue in issues)


def test_straight_pipe_binds_provenance_digests_to_verified_artifacts(tmp_path: Path) -> None:
    case, _qoi_path, evidence_path = _captured_straight_pipe_case(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["provenance"]["meshArtifactSha256"] = "f" * 64
    _write_json(evidence_path, evidence)
    _refresh_evidence_package_digest(case, evidence_path)

    issues = validate_benchmark_case(case)

    assert any("must match the verified `mesh-artifact` artifact" in issue for issue in issues)


def test_straight_pipe_rejects_out_of_order_qoi_artifact_even_if_hashes_are_refreshed(tmp_path: Path) -> None:
    case, qoi_path, evidence_path = _captured_straight_pipe_case(tmp_path)
    qoi = json.loads(qoi_path.read_text(encoding="utf-8"))
    qoi["meshLevels"][0], qoi["meshLevels"][1] = qoi["meshLevels"][1], qoi["meshLevels"][0]
    _write_json(qoi_path, qoi)
    qoi_digest = _sha256_file(qoi_path)
    _replace_artifact_digest(case, "qoi-extraction-table", qoi_digest)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["provenance"]["qoiExtractionSha256"] = qoi_digest
    _write_json(evidence_path, evidence)
    _refresh_evidence_package_digest(case, evidence_path)

    issues = validate_benchmark_case(case)

    assert any("meshLevels must be strictly ordered coarse-to-fine" in issue for issue in issues)


def test_no_pending_fixture_is_a_trusted_validated_benchmark() -> None:
    assert trusted_validated_benchmark_ids() == set()
