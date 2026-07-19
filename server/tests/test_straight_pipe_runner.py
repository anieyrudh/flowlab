import json
from dataclasses import asdict
from pathlib import Path

import pytest

from server.flowlab import straight_pipe_runner as runner
from server.flowlab.straight_pipe_runner import (
    DEFAULT_MESH_SIZES_M,
    FULL_PIPE_OGRID_MESH_RECIPE,
    SECTOR90_MESH_RECIPE,
    StraightPipeRunSpec,
    StraightPipeRunError,
    _artifact_index_integrity,
    _block_mesh_dict,
    _case_runner_source_provenance,
    _cpu_set_logical_ids,
    _docker_parallel_command,
    _docker_serial_command,
    _mesh_flow_control,
    _mesh_recipe_metadata,
    _parallel_qoi_equivalence,
    _parallel_run_script,
    _parallel_cells_per_rank,
    _periodic_numerical_convergence,
    _solver_iteration_count,
    _solver_resource_usage,
    _spec_from_run_manifest,
    _native_timing_preflight_from_facts,
    _uniform_runtime_provenance,
    _validate_timing_cpu_sets,
    default_run_spec,
    main,
    materialize_parallel_case_from_source,
    materialize_serial_cases,
    materialize_serial_trial_case,
    run_replicated_timing,
)


def test_default_straight_pipe_runner_stays_within_laminar_reference_envelope() -> None:
    spec = default_run_spec()

    assert spec.mesh_sizes_m == DEFAULT_MESH_SIZES_M
    assert spec.mesh_recipe == FULL_PIPE_OGRID_MESH_RECIPE
    assert spec.reference()["reynoldsNumber"] < 2100.0
    assert spec.reference()["pressureDropPa"] > 0.0


def test_cli_uses_configured_shared_openfoam_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWLAB_OPENFOAM_IMAGE", "flowlab/openfoam:test")

    args = runner._parser().parse_args(["--output-dir", "/tmp/flowlab-test", "--materialize-only"])

    assert args.image == "flowlab/openfoam:test"


def test_materialized_default_cases_use_a_full_pipe_ogrid_without_collapsed_axis(tmp_path: Path) -> None:
    output_dir = tmp_path / "straight-pipe"
    case_dirs = materialize_serial_cases(output_dir)
    coarse = case_dirs[0]

    block_mesh = (coarse / "system" / "blockMeshDict").read_text(encoding="utf-8")
    velocity = (coarse / "0" / "U").read_text(encoding="utf-8")
    constraints = (coarse / "system" / "fvConstraints").read_text(encoding="utf-8")
    control = (coarse / "system" / "controlDict").read_text(encoding="utf-8")
    run_script = (coarse / "run_level.sh").read_text(encoding="utf-8")

    assert "type cyclic" in block_mesh
    assert "side1" not in block_mesh
    assert "side2" not in block_mesh
    assert "type symmetryPlane" not in block_mesh
    assert block_mesh.count("hex (") == 5
    assert "hex (0 8 9 1 3 11 10 2) (16 16 16)" in block_mesh
    assert "hex (0 8 12 4 1 9 13 5) (16 3 16)" in block_mesh
    assert "type            noSlip" in velocity
    assert "meanVelocityForce" in constraints
    assert "relaxation      1" in constraints
    assert all(name in control for name in ("transverseMomentumResiduals", "wallShearStress", "wallForces"))
    assert not (coarse / "constant" / "fvConstraints").exists()
    assert "blockMesh > log.blockMesh" in run_script
    assert "checkMesh -allGeometry -allTopology" in run_script
    assert "setFields > log.setFields" in run_script
    assert 'grep -q "End" log.foamRun' in run_script
    assert not (coarse / "pipe.geo").exists()
    manifest = json.loads((coarse / "case-manifest.json").read_text(encoding="utf-8"))
    assert manifest["geometry"]["meshRecipe"] == FULL_PIPE_OGRID_MESH_RECIPE
    assert manifest["flowControl"]["fullPipeScale"] == 1.0
    assert manifest["boundaryCondition"]["axis"] is None
    assert len(manifest["provenance"]["runnerSourceSha256"]) == 64
    source_snapshot = output_dir / "runner-source-snapshot.py"
    run_manifest = json.loads((output_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert source_snapshot.is_file()
    assert runner._sha256_file(source_snapshot) == manifest["provenance"]["runnerSourceSha256"]
    assert run_manifest["provenance"]["runnerSourceSnapshot"] == "runner-source-snapshot.py"
    assert {
        json.loads((case_dir / "case-manifest.json").read_text(encoding="utf-8"))["provenance"][
            "runnerSourceSha256"
        ]
        for case_dir in case_dirs
    } == {runner._sha256_file(source_snapshot)}


def test_ogrid_refinement_doubles_axial_and_annular_radial_directions() -> None:
    spec = default_run_spec()

    coarse = _block_mesh_dict(spec, spec.mesh_sizes_m[0])
    medium = _block_mesh_dict(spec, spec.mesh_sizes_m[1])
    fine = _block_mesh_dict(spec, spec.mesh_sizes_m[2])

    assert "(16 16 16)" in coarse
    assert "(16 3 16)" in coarse
    assert "(32 16 16)" in medium
    assert "(32 6 16)" in medium
    assert "(64 16 16)" in fine
    assert "(64 12 16)" in fine


def test_legacy_sector_recipe_remains_materializable_without_reinterpreting_its_topology() -> None:
    spec = StraightPipeRunSpec(mesh_recipe=SECTOR90_MESH_RECIPE)
    coarse = _block_mesh_dict(spec, spec.mesh_sizes_m[0])

    assert "side1" in coarse
    assert "side2" in coarse
    assert "type symmetryPlane" in coarse
    assert "hex (0 1 2 3 4 5 6 7) (16 4 4)" in coarse


def test_ogrid_requires_conforming_core_and_annular_interface_counts() -> None:
    with pytest.raises(StraightPipeRunError, match="conforming block interfaces"):
        StraightPipeRunSpec(
            mesh_recipe=FULL_PIPE_OGRID_MESH_RECIPE,
            ogrid_azimuthal_cells_per_quadrant=32,
            ogrid_core_cells_per_side=16,
        )


def test_cli_materializes_a_named_higher_fidelity_ogrid(tmp_path: Path) -> None:
    output_dir = tmp_path / "ogrid-128"

    assert (
        main(
            [
                "--output-dir",
                str(output_dir),
                "--materialize-only",
                "--mesh-recipe",
                FULL_PIPE_OGRID_MESH_RECIPE,
                "--ogrid-azimuthal-cells-per-quadrant",
                "32",
                "--ogrid-core-cells-per-side",
                "32",
            ]
        )
        == 0
    )

    spec = json.loads((output_dir / "run-manifest.json").read_text(encoding="utf-8"))["spec"]
    assert spec["ogrid_azimuthal_cells_per_quadrant"] == 32
    assert spec["ogrid_core_cells_per_side"] == 32


def test_cli_rejects_ogrid_resolution_options_for_the_legacy_sector_recipe(tmp_path: Path) -> None:
    with pytest.raises(StraightPipeRunError, match="O-grid resolution options"):
        main(
            [
                "--output-dir",
                str(tmp_path / "sector"),
                "--materialize-only",
                "--mesh-recipe",
                SECTOR90_MESH_RECIPE,
                "--ogrid-azimuthal-cells-per-quadrant",
                "32",
            ]
        )


def test_mesh_specific_flow_control_preserves_the_declared_volume_flow_rate() -> None:
    spec = default_run_spec()

    for mesh_size_m in spec.mesh_sizes_m:
        flow_control = _mesh_flow_control(spec, mesh_size_m)
        assert (
            flow_control["meanVelocityTargetMPerS"] * flow_control["sectorAreaM2"]
            == flow_control["targetSectorVolumetricFlowRateM3PerS"]
        )
        assert flow_control["targetFullPipeVolumetricFlowRateM3PerS"] == spec.volumetric_flow_rate_m3_s
        assert flow_control["fullPipeScale"] == 1.0
        assert flow_control["geometryAreaRelativeDeficit"] < 0.002


def test_ogrid_wall_geometry_stays_fixed_across_the_three_grid_study() -> None:
    spec = default_run_spec()
    metadata = [_mesh_recipe_metadata(spec, mesh_size) for mesh_size in spec.mesh_sizes_m]

    assert [entry["totalAzimuthalCells"] for entry in metadata] == [64, 64, 64]
    assert [entry["coreCellsPerSide"] for entry in metadata] == [16, 16, 16]
    assert [entry["axialCells"] for entry in metadata] == [16, 32, 64]
    assert [entry["annularRadialCells"] for entry in metadata] == [3, 6, 12]
    assert len({entry["crossSectionAreaRelativeDeficit"] for entry in metadata}) == 1


def test_geometry_change_classification_separates_fixed_ogrid_from_legacy_level_geometry() -> None:
    def observation(recipe: str, area: float, deficit: float, facets: int) -> dict[str, object]:
        return {
            "meshRecipe": recipe,
            "sectorAngleDegrees": 360.0 if recipe == FULL_PIPE_OGRID_MESH_RECIPE else 90.0,
            "sectorScaleToFullPipe": 1.0 if recipe == FULL_PIPE_OGRID_MESH_RECIPE else 4.0,
            "meshCrossSectionAreaM2": area,
            "geometryAreaRelativeDeficit": deficit,
            "meshRepresentation": {
                "totalAzimuthalCells": facets,
                "facetAngleDegrees": 360.0 / facets,
                "azimuthalCells": facets,
                "azimuthalCellsPerQuadrant": facets // 4,
                "coreRadiusM": 0.0025,
                "coreCellsPerSide": 16,
            },
        }

    ogrid = [observation(FULL_PIPE_OGRID_MESH_RECIPE, 1.0, 0.0016, 64) for _ in range(3)]
    legacy = [
        observation(SECTOR90_MESH_RECIPE, 1.0, 0.0255, 16),
        observation(SECTOR90_MESH_RECIPE, 1.02, 0.0064, 32),
        observation(SECTOR90_MESH_RECIPE, 1.025, 0.0016, 64),
    ]

    assert runner._geometry_changes_across_levels(ogrid) is False
    assert runner._geometry_changes_across_levels(legacy) is True


def test_old_run_manifest_defaults_to_legacy_sector_recipe(tmp_path: Path) -> None:
    legacy = StraightPipeRunSpec(mesh_recipe=SECTOR90_MESH_RECIPE)
    values = asdict(legacy)
    values.pop("mesh_recipe")
    values.pop("ogrid_azimuthal_cells_per_quadrant")
    values.pop("ogrid_core_cells_per_side")
    (tmp_path / "run-manifest.json").write_text(json.dumps({"spec": values}), encoding="utf-8")

    restored = _spec_from_run_manifest(tmp_path)

    assert restored.mesh_recipe == SECTOR90_MESH_RECIPE


def test_serial_evidence_rejects_a_mixed_immutable_image_identity() -> None:
    baseline = {
        "containerImage": "example/openfoam:tag",
        "containerPlatform": "linux/amd64",
        "runnerSourceSha256": "a" * 64,
        "containerImageProvenance": {
            "captureStatus": "captured",
            "imageId": "sha256:" + "1" * 64,
            "repoDigests": ["example/openfoam@sha256:" + "2" * 64],
            "os": "linux",
            "architecture": "amd64",
        },
    }
    changed = {
        **baseline,
        "containerImageProvenance": {
            **baseline["containerImageProvenance"],
            "imageId": "sha256:" + "3" * 64,
        },
    }

    with pytest.raises(StraightPipeRunError, match="immutable container images"):
        _uniform_runtime_provenance([baseline, changed])


def test_case_runner_provenance_uses_the_materialized_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_dir = materialize_serial_cases(tmp_path / "straight-pipe")[0]
    manifest = json.loads((case_dir / "case-manifest.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "_runner_source_sha256", lambda: "b" * 64)

    provenance = _case_runner_source_provenance(case_dir)

    assert provenance["runnerSourceSha256"] == manifest["provenance"]["runnerSourceSha256"]
    assert "case-manifest" in provenance["runnerSourceHashScope"]


def test_periodic_convergence_uses_physical_tail_stability_not_pressure_gauge_residual(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case-manifest.json").write_text(
        json.dumps({"flowControl": {"meanVelocityTargetMPerS": 2.0}}), encoding="utf-8"
    )
    steps = []
    for step in range(1, 51):
        steps.extend(
            [
                f"Time = {step}s",
                "smoothSolver:  Solving for Ux, Initial residual = 0.9, Final residual = 1e-10, No Iterations 1",
                "smoothSolver:  Solving for Uy, Initial residual = 0.8, Final residual = 1e-10, No Iterations 1",
                "smoothSolver:  Solving for Uz, Initial residual = 0.7, Final residual = 1e-10, No Iterations 1",
                "Pressure gradient source: uncorrected Ubar = 2, pressure gradient = 0.5",
                "GAMG:  Solving for p, Initial residual = 0.95, Final residual = 1e-10, No Iterations 1",
                "time step continuity errors : sum local = 1e-14, global = -1e-14, cumulative = 1e-13",
                "Pressure gradient source: uncorrected Ubar = 2, pressure gradient = 0.5",
            ]
        )
    steps.append("End")
    (case_dir / "log.foamRun").write_text("\n".join(steps), encoding="utf-8")

    result = _periodic_numerical_convergence(case_dir)

    assert result["passed"] is True
    assert result["genericSimpleConvergenceReported"] is False
    assert result["pressureGradientTailRelativeSpan"] == 0.0


def test_parallel_script_has_the_required_openfoam_decomposition_order() -> None:
    script = _parallel_run_script(2)

    commands = [
        "checkMesh -allGeometry -allTopology",
        "decomposePar -force",
        "mpirun -np 2 checkMesh -parallel -allGeometry -allTopology",
        "mpirun -np 2 foamRun -solver incompressibleFluid -parallel",
        "reconstructPar -latestTime",
        "foamToVTK -ascii -latestTime",
    ]
    positions = [script.index(command) for command in commands]
    assert positions == sorted(positions)


def test_runtime_helpers_capture_iterations_memory_and_decomposition_cell_counts(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "log.foamRun").write_text("Time = 1s\nTime = 2s\nEnd\n", encoding="utf-8")
    (case_dir / "runtime").mkdir()
    (case_dir / "runtime" / "solver-resources.txt").write_text(
        "Maximum resident set size (kbytes): 123\n", encoding="utf-8"
    )
    (case_dir / "log.decomposePar").write_text(
        "Processor 0\n    Number of cells = 11\n\nProcessor 1\n    Number of cells = 13\n",
        encoding="utf-8",
    )

    assert _solver_iteration_count(case_dir) == 2
    assert _solver_resource_usage(case_dir, ranks=2)["peakResidentMemoryBytes"] == 123 * 1024
    assert _parallel_cells_per_rank(case_dir, ranks=2) == [11, 13]


def test_parallel_qoi_equivalence_requires_the_predeclared_tolerance() -> None:
    serial = {
        "pressureDropPa": 2.0,
        "computedKinematicPressureGradientMPerS2": 0.1,
        "fullPipeVolumetricFlowRateM3PerS": 1.0e-5,
        "inletMassFlowRateKgPerS": -0.01,
        "outletMassFlowRateKgPerS": 0.01,
        "solverClockTimeSeconds": 10.0,
    }
    parallel = dict(serial)

    accepted = _parallel_qoi_equivalence(serial=serial, parallel=parallel)
    assert accepted["passed"] is True

    parallel["pressureDropPa"] = 2.000004
    rejected = _parallel_qoi_equivalence(serial=serial, parallel=parallel)
    assert rejected["passed"] is False


def test_docker_commands_accept_explicit_logical_cpu_sets(tmp_path: Path) -> None:
    serial = _docker_serial_command(
        tmp_path,
        image="example/openfoam:fixed",
        platform="linux/amd64",
        cpu_set="0",
    )
    parallel = _docker_parallel_command(
        tmp_path,
        image="example/openfoam:fixed",
        platform="linux/amd64",
        cpu_set="0-3",
    )

    assert serial[serial.index("--cpuset-cpus") + 1] == "0"
    assert parallel[parallel.index("--cpuset-cpus") + 1] == "0-3"
    assert "--cpuset-cpus" not in _docker_serial_command(
        tmp_path, image="example/openfoam:fixed", platform="linux/amd64"
    )
    with pytest.raises(StraightPipeRunError, match="cpu_set"):
        _docker_parallel_command(
            tmp_path,
            image="example/openfoam:fixed",
            platform="linux/amd64",
            cpu_set="0; rm -rf /",
        )


def test_replicated_timing_cpu_allocations_require_distinct_sufficient_logical_cpus() -> None:
    assert _cpu_set_logical_ids("0,2-3") == [0, 2, 3]
    allocations = _validate_timing_cpu_sets({1: "0", 2: "0-1", 4: "0-3"})
    assert allocations[4]["logicalCpuIds"] == [0, 1, 2, 3]
    with pytest.raises(StraightPipeRunError, match="ascending"):
        _cpu_set_logical_ids("3-1")
    with pytest.raises(StraightPipeRunError, match="at least 4"):
        _validate_timing_cpu_sets({1: "0", 2: "0-1", 4: "0-2"})


def test_native_timing_preflight_rejects_emulation_without_claiming_physical_core_pinning() -> None:
    preflight = _native_timing_preflight_from_facts(
        image="example/openfoam:fixed",
        platform="linux/amd64",
        image_provenance={
            "captureStatus": "captured",
            "os": "linux",
            "architecture": "amd64",
        },
        engine_facts={
            "engineOs": "linux",
            "engineArchitecture": "arm64",
            "engineOperatingSystem": "Docker Desktop",
            "engineName": "docker-desktop",
        },
        cpu_allocations=_validate_timing_cpu_sets({1: "0", 2: "0-1", 4: "0-3"}),
    )

    assert preflight["nativeTimingPermitted"] is False
    assert preflight["nativeCompatibility"]["emulationRisk"] is True
    assert preflight["cpuSetScope"]["physicalCorePinningClaimed"] is False


def test_serial_and_parallel_trial_materialization_preserve_only_frozen_inputs(tmp_path: Path) -> None:
    source = materialize_serial_cases(tmp_path / "verification")[2]
    serial_trial = tmp_path / "trials" / "measurement-001" / "serial" / "fine"
    manifest = materialize_serial_trial_case(source, serial_trial, evidence_root=tmp_path)

    assert manifest["sourceInputTreeSha256"] == manifest["copiedInputTreeSha256"]
    assert (serial_trial / "run_level.sh").is_file()
    parallel_trial = tmp_path / "trials" / "measurement-001" / "parallel" / "mpi-2"
    materialize_parallel_case_from_source(
        serial_trial, parallel_trial, ranks=2, evidence_root=tmp_path
    )
    parallel_manifest = json.loads((parallel_trial / "parallel-manifest.json").read_text(encoding="utf-8"))
    assert parallel_manifest["sourceInputTreeSha256"] == parallel_manifest["copiedInputTreeSha256"]
    assert not (parallel_trial / "run_level.sh").exists()


def test_replicated_timing_writes_a_non_execution_record_when_native_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner,
        "_capture_native_timing_preflight",
        lambda **_kwargs: {
            "nativeTimingPermitted": False,
            "failureReasons": ["architecture mismatch"],
            "executionQualification": {
                "architectureNative": False,
                "physicalCorePinningClaimed": False,
            },
        },
    )

    result = run_replicated_timing(
        tmp_path / "timing",
        cpu_sets={1: "0", 2: "0-1", 4: "0-3"},
    )

    assert result["status"] == "native-preflight-failed"
    assert not (tmp_path / "timing" / "verification").exists()
    assert json.loads((tmp_path / "timing" / "artifacts" / "timing-summary.json").read_text())["status"] == (
        "native-preflight-failed"
    )


def test_artifact_index_integrity_recomputes_hashes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "evidence.json"
    artifact.parent.mkdir()
    artifact.write_text("first", encoding="utf-8")
    index = tmp_path / "artifacts" / "artifact-index.json"
    index.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "kind": "evidence",
                        "path": "artifacts/evidence.json",
                        "sha256": runner._sha256_file(artifact),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _artifact_index_integrity(tmp_path, index)["allValid"] is True
    artifact.write_text("changed", encoding="utf-8")
    assert _artifact_index_integrity(tmp_path, index)["allValid"] is False


def test_parallel_cli_uses_parallel_result_status_for_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner,
        "run_parallel_benchmarks",
        lambda *_args, **_kwargs: {"status": "parallel-qoi-equivalence-passed"},
    )

    assert main(["--output-dir", str(tmp_path), "--run-parallel"]) == 0
