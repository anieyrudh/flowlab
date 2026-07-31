from __future__ import annotations

from pathlib import Path

from server.flowlab import adapters
from server.flowlab.execution import JobManager
from server.flowlab.smoke import run_code_saturne_smoke, run_mujoco_smoke, run_openfoam_smoke, run_su2_smoke


def _openfoam_command_available(command: str) -> bool:
    return command in {"foamRun", "surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh", "postProcess"}


class SmokeSuccessProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        joined = " ".join(command)
        if "surfaceFeatureExtract" in joined:
            self.stdout = iter(["Extracting surface features\n", "End\n"])
            return
        if "blockMesh" in joined:
            self.stdout = iter(["Creating block mesh\n", "Mesh OK.\n"])
            return
        if "snappyHexMesh" in joined:
            self.stdout = iter(["snappyHexMesh: castellatedMesh true\n", "Layer addition phase\n", "Added 12 layers\n"])
            return
        if "checkMesh" in joined:
            self.stdout = iter(
                [
                    "Mesh stats\n",
                    "    cells:            39\n",
                    "    Max aspect ratio = 5 OK.\n",
                    "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                    "    Max skewness = 0.42 OK.\n",
                    "    Min volume = 1e-09.\n",
                    "Failed 0 mesh checks.\n",
                    "Mesh OK.\n",
                ]
            )
            return
        if "postProcess" in joined:
            yplus_dir = case_dir / "postProcessing" / "yPlus" / "0"
            yplus_dir.mkdir(parents=True, exist_ok=True)
            (yplus_dir / "yPlus.dat").write_text("Time min mean max\n0 0.8 18.5 42.0\n", encoding="utf-8")
            self.stdout = iter(["Executing functionObject yPlus\n", "End\n"])
            return
        (case_dir / "postProcessing" / "VTK").mkdir(parents=True, exist_ok=True)
        (case_dir / "postProcessing" / "VTK" / "field_0001.vtk").write_text(
            """# vtk DataFile Version 3.0
smoke result
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
1
2
3
4
""",
            encoding="utf-8",
        )
        time_dir = case_dir / "0.001"
        time_dir.mkdir(parents=True, exist_ok=True)
        (time_dir / "U").write_text(
            """FoamFile { class volVectorField; object U; }
internalField   uniform (1 0 0);
boundaryField {}
""",
            encoding="utf-8",
        )
        (time_dir / "p").write_text(
            """FoamFile { class volScalarField; object p; }
internalField   uniform 1;
boundaryField {}
""",
            encoding="utf-8",
        )
        (case_dir / "postProcessing" / "residuals" / "0").mkdir(parents=True, exist_ok=True)
        (case_dir / "postProcessing" / "residuals" / "0" / "residuals.dat").write_text(
            "Time Ux p\n0.001 7.5e-06 9e-05\n",
            encoding="utf-8",
        )
        flow_dir = case_dir / "postProcessing" / "patchFlowRate" / "0"
        flow_dir.mkdir(parents=True, exist_ok=True)
        (flow_dir / "patchFlowRate.dat").write_text("# Time inlet outlet\n0.1 -0.012 0.0118\n", encoding="utf-8")
        pressure_dir = case_dir / "postProcessing" / "patchAverage" / "0"
        pressure_dir.mkdir(parents=True, exist_ok=True)
        (pressure_dir / "p.dat").write_text("# Time inlet outlet\n0.1 101325 99000\n", encoding="utf-8")
        shear_dir = case_dir / "postProcessing" / "wallShearStress" / "0"
        shear_dir.mkdir(parents=True, exist_ok=True)
        (shear_dir / "wallShearStress.dat").write_text("# Time walls_min walls_mean walls_max\n0.1 0.4 1.1 2.8\n", encoding="utf-8")
        forces_dir = case_dir / "postProcessing" / "wallForces" / "0"
        forces_dir.mkdir(parents=True, exist_ok=True)
        (forces_dir / "forces.dat").write_text(
            "# Time forces(pressure viscous) moments(pressure viscous)\n"
            "0.1 ((1 2 3) (0.1 0.2 0.3)) ((4 5 6) (0.4 0.5 0.6))\n",
            encoding="utf-8",
        )
        self.stdout = iter(
            [
                "Time = 0.001\n",
                "smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 7.5e-06, No Iterations 2\n",
            ]
        )

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class ChtMeshPreflightProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.stdout = iter(
            [
                "FlowLab OpenFOAM CHT region mesh check: fluid\n",
                "    cells:            39\n",
                "    Max aspect ratio = 5 OK.\n",
                "Failed 0 mesh checks.\n",
                "Mesh OK.\n",
                "checkMesh -region solid -allGeometry -allTopology\n",
                "    cells:            78\n",
                "    Max aspect ratio = 8.5 OK.\n",
                "Failed 0 mesh checks.\n",
                "Mesh OK.\n",
            ]
        )

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class MuJoCoSmokeSuccessProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        (case_dir / "outputs").mkdir(parents=True, exist_ok=True)
        (case_dir / "outputs" / "mujoco_fluid_force_0001.vtk").write_text(
            """# vtk DataFile Version 3.0
mujoco result
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
1
1
1
1
""",
            encoding="utf-8",
        )
        (case_dir / "outputs" / "summary.json").write_text(
            """{
  "solver": "mujoco",
  "model": "flowlab-fluid-forces",
  "steps": 120,
  "final": {
    "step": 119,
    "time": 0.24,
    "position": [0.01, 0.0, 0.0],
    "velocity": [1.25, 0.0, 0.0],
    "passiveForceNorm": 0.42
  },
  "note": "MuJoCo fluid forces are phenomenological rigid-body forces, not CFD field solves."
}
""",
            encoding="utf-8",
        )
        self.stdout = iter(["steps: 120\n", "Wrote outputs/mujoco_fluid_force_0001.vtk\n"])

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class SU2SmokeSuccessProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        (case_dir / "flowlab_su2.vtk").write_text(
            """# vtk DataFile Version 3.0
su2 result
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
SCALARS Pressure float 1
LOOKUP_TABLE default
1
2
3
4
""",
            encoding="utf-8",
        )
        (case_dir / "history.csv").write_text(
            "Inner_Iter,rms[Rho],rms[Momentum]\n1,-3.0,-2.0\n5,-4.0,-3.1\n",
            encoding="utf-8",
        )
        self.stdout = iter(["| 1 | -3.0 | -2.0 | 0.01 |\n", "| 5 | -4.0 | -3.1 | 0.005 |\n"])

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class CodeSaturneSmokeFailingProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        run_dir = case_dir / "RESU" / "flowlab"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "listing").write_text(
            "First face with boundary condition definition error\n"
            "Fatal error: boundary condition type 0\n",
            encoding="utf-8",
        )
        self.stdout = iter(["iteration 1 residual velocity 1.0e-3\n", "boundary setup failed\n"])

    def wait(self) -> int:
        return 1

    def terminate(self) -> None:
        pass


def test_openfoam_smoke_reports_blocked_when_runtime_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)

    report = run_openfoam_smoke(tmp_path, timeout_seconds=1)

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert "Docker is unavailable" in report["blockedReason"]
    assert report["runtime"]["runnable"] is False
    assert "flowlab_case_manifest.json" in report["caseValidation"]["requiredFilesPresent"]
    assert report["caseValidation"]["manifest"]["schema"] == "flowlab.case_manifest.v1"
    assert "system/controlDict" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/openfoam_review.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/boundary_layer_plan.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/adaptation_plan.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/production_mesh_plan.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/production_mesh_acceptance.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/openfoam_native_mesh_preflight.py" in report["caseValidation"]["requiredFilesPresent"]
    review = report["caseValidation"]["openfoamMeshReview"]
    assert review["schema"] == "flowlab.openfoam_mesh_review.v1"
    assert review["productionReady"] is False
    assert review["readiness"]["checkmesh-scripted"] == "pass"
    assert review["readiness"]["cad-quality-3d-topology"] == "fail"
    assert Path(report["caseDir"]).exists()


def test_openfoam_smoke_reports_success_with_mocked_solver(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: SmokeSuccessProcess(command, **kwargs))

    report = run_openfoam_smoke(tmp_path, timeout_seconds=2, manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["status"] == "complete"
    assert report["execution"] == "native"
    assert report["exitCode"] == 0
    assert (
        report["resultFiles"][0]["path"]
        == "postProcessing/flowlabNative/time_0_001.vtk"
    )
    assert report["resultFiles"][0]["sourceCellIdentity"]["verified"] is True
    assert report["caseValidation"]["manifest"]["files"]["Allrun"]["sha256"]
    assert report["caseValidation"]["openfoamMeshReview"]["meshGenerated"] is True
    residual_summary = next(item for item in report["diagnosticSummary"] if item["path"] == "postProcessing/residuals/0/residuals.dat")
    assert residual_summary["latest"]["p"] == 9e-05
    assert report["patchMetrics"]["status"] == "complete"
    assert report["diagnosticsAcceptance"]["status"] == "complete"
    assert report["logSummary"]["latestTime"] == 0.001


def test_openfoam_smoke_accepts_advanced_mode_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: SmokeSuccessProcess(command, **kwargs))

    report = run_openfoam_smoke(tmp_path, timeout_seconds=2, advanced_mode="heat-transfer", manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["advancedMode"] == "heat-transfer"
    assert report["smoke"] == "openfoam-heat-transfer-solve-through"
    assert report["caseValidation"]["manifest"]["advancedMode"] == "heat-transfer"
    assert "constant/thermophysicalProperties" in report["caseValidation"]["manifest"]["files"]


def test_openfoam_smoke_reports_water_hammer_handoff_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: SmokeSuccessProcess(command, **kwargs))

    report = run_openfoam_smoke(tmp_path, timeout_seconds=2, advanced_mode="water-hammer", manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["advancedMode"] == "water-hammer"
    assert "constant/waterHammerPreview.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "constant/waterHammerWaveform.csv" in report["caseValidation"]["requiredFilesPresent"]
    assert "constant/waterHammerPreview.json" in report["caseValidation"]["manifest"]["files"]
    assert "constant/waterHammerWaveform.csv" in report["caseValidation"]["manifest"]["files"]
    handoff = report["caseValidation"]["waterHammerHandoff"]
    assert handoff["schema"] == "flowlab.water_hammer_handoff.v1"
    assert handoff["cfdCoupling"] == "pressure-wave-boundary-preview"
    assert handoff["productionReady"] is False
    assert handoff["waveformRows"] == 4
    assert handoff["pressureRise"] > 0
    assert handoff["kinematicPressureRise"] > 0
    assert handoff["criticalClosureTime"] > handoff["closureTime"] > 0
    assert handoff["settleTime"] > handoff["criticalClosureTime"]
    assert handoff["waveformStart"]["absolutePressure"] < handoff["waveformPeak"]["absolutePressure"]
    assert handoff["waveformEnd"]["absolutePressure"] == handoff["waveformStart"]["absolutePressure"]
    assert handoff["pressureField"] == "0/p"
    assert handoff["csv"] == "constant/waterHammerWaveform.csv"


def test_openfoam_smoke_blocks_conjugate_heat_transfer_until_region_mesh_is_production_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "foamRun")

    report = run_openfoam_smoke(tmp_path, timeout_seconds=1, advanced_mode="conjugate-heat-transfer")

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert "not production-ready" in report["blockedReason"]
    assert report["caseValidation"]["manifest"]["status"] == "generated"
    assert "Allrun" in report["caseValidation"]["manifest"]["files"]
    assert "AllmeshCheck" in report["caseValidation"]["manifest"]["files"]
    assert "AllmeshCheck" in report["caseValidation"]["requiredFilesPresent"]
    assert "constant/fluid/physicalProperties" in report["caseValidation"]["manifest"]["files"]
    assert "constant/flowlab_cht_interface.json" in report["caseValidation"]["manifest"]["files"]
    assert "constant/fluid/polyMesh/boundary" in report["caseValidation"]["manifest"]["files"]
    assert "constant/solid/polyMesh/boundary" in report["caseValidation"]["manifest"]["files"]
    assert "system/solid/fvSolution" in report["caseValidation"]["manifest"]["files"]
    interface = report["caseValidation"]["chtInterface"]
    assert interface["schema"] == "flowlab.openfoam_cht_interface.v1"
    assert interface["productionReady"] is False
    assert interface["readiness"]["paired-mapped-wall-patches"] == "pass"
    assert interface["readiness"]["non-overlapping-solid-jacket"] == "pass"
    assert interface["readiness"]["region-checkmesh-plan"] == "pass"
    assert interface["readiness"]["cht-boundary-layer-evidence"] == "fail"
    assert interface["regionMeshChecks"]["script"] == "AllmeshCheck"
    assert interface["regionMeshChecks"]["commands"] == [
        "checkMesh -region fluid -allGeometry -allTopology",
        "checkMesh -region solid -allGeometry -allTopology",
    ]
    assert interface["solidJacket"]["nonOverlapping"] is True
    assert interface["patches"]["fluid"]["neighbourRegion"] == "solid"
    assert any("Per-region OpenFOAM checkMesh evidence" in reason for reason in interface["blockingReasons"])
    assert any("production-ready" in line for line in report["logsTail"])
    assert report["command"] == []


def test_openfoam_smoke_reports_cht_mesh_preflight_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "checkMesh")

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: ChtMeshPreflightProcess(command, **kwargs))

    report = run_openfoam_smoke(tmp_path, timeout_seconds=1, advanced_mode="conjugate-heat-transfer", manager_factory=manager_factory)

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert report["execution"] == "preflight"
    assert report["command"] == ["bash", "AllmeshCheck"]
    assert "mesh preflight completed" in report["blockedReason"]
    summary = report["logSummary"]
    assert summary["checkMeshRegions"]["fluid"]["passed"] is True
    assert summary["checkMeshRegions"]["solid"]["passed"] is True


def test_mujoco_smoke_reports_blocked_when_module_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists_for_command", lambda _command, _module: False)

    report = run_mujoco_smoke(tmp_path, timeout_seconds=1)

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert "mujoco" in report["blockedReason"]
    assert report["runtime"]["runnable"] is False
    assert "model.xml" in report["caseValidation"]["requiredFilesPresent"]


def test_mujoco_smoke_reports_success_with_mocked_solver(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists_for_command", lambda command, module: module == "mujoco")

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: MuJoCoSmokeSuccessProcess(command, **kwargs))

    report = run_mujoco_smoke(tmp_path, timeout_seconds=2, manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["status"] == "complete"
    assert report["execution"] == "native"
    assert report["exitCode"] == 0
    assert report["resultFiles"][0]["path"] == "outputs/mujoco_fluid_force_0001.vtk"
    assert report["diagnosticFiles"][0]["path"] == "outputs/summary.json"
    assert report["diagnosticSummary"][0]["kind"] == "mujoco-summary"
    assert report["diagnosticSummary"][0]["latest"]["steps"] == 120.0
    assert report["diagnosticSummary"][0]["latest"]["passiveForceNorm"] == 0.42
    assert report["caseValidation"]["manifest"]["files"]["run_mujoco.py"]["sha256"]
    assert report["logSummary"]["latestIteration"] == 120


def test_su2_smoke_reports_blocked_when_runtime_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FLOWLAB_SU2_HOME", raising=False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)

    report = run_su2_smoke(tmp_path, timeout_seconds=1)

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert "SU2_CFD" in report["blockedReason"]
    assert report["runtime"]["runnable"] is False
    assert "case.cfg" in report["caseValidation"]["requiredFilesPresent"]
    assert "flowlab_su2_mode_preset.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "flowlab_su2_native_setup_checklist.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "flowlab_su2_capability_matrix.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/flowlab_mesh.su2" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/quality.json" in report["caseValidation"]["requiredFilesPresent"]


def test_su2_smoke_reports_success_with_mocked_solver(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FLOWLAB_SU2_HOME", raising=False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: SU2SmokeSuccessProcess(command, **kwargs))

    report = run_su2_smoke(tmp_path, timeout_seconds=2, manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["status"] == "complete"
    assert report["execution"] == "native"
    assert report["exitCode"] == 0
    assert report["resultFiles"][0]["path"] == "flowlab_su2.vtk"
    assert report["caseValidation"]["manifest"]["files"]["case.cfg"]["sha256"]
    assert report["caseValidation"]["manifest"]["files"]["flowlab_su2_mode_preset.json"]["sha256"]
    assert report["caseValidation"]["manifest"]["files"]["flowlab_su2_native_setup_checklist.json"]["sha256"]
    assert report["caseValidation"]["manifest"]["files"]["flowlab_su2_capability_matrix.json"]["sha256"]
    assert report["diagnosticSummary"][0]["latest"]["rms[Momentum]"] == -3.1
    assert report["logSummary"]["latestIteration"] == 5


def test_su2_smoke_accepts_advanced_mode_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FLOWLAB_SU2_HOME", raising=False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: SU2SmokeSuccessProcess(command, **kwargs))

    report = run_su2_smoke(tmp_path, timeout_seconds=2, advanced_mode="heat-transfer", manager_factory=manager_factory)

    assert report["completed"] is True
    assert report["advancedMode"] == "heat-transfer"
    assert report["smoke"] == "su2-heat-transfer-solve-through"
    assert report["caseValidation"]["manifest"]["advancedMode"] == "heat-transfer"
    assert report["caseValidation"]["manifest"]["files"]["case.cfg"]["sha256"]
    assert report["caseValidation"]["manifest"]["files"]["flowlab_su2_mode_preset.json"]["sha256"]
    assert report["caseValidation"]["manifest"]["files"]["flowlab_su2_capability_matrix.json"]["sha256"]


def test_code_saturne_smoke_reports_blocked_when_runtime_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FLOWLAB_CODE_SATURNE_IMAGE", raising=False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)

    report = run_code_saturne_smoke(tmp_path, timeout_seconds=1)

    assert report["completed"] is False
    assert report["status"] == "blocked"
    assert "FLOWLAB_CODE_SATURNE_IMAGE" in report["blockedReason"]
    assert report["runtime"]["runnable"] is False
    assert "DATA/flowlab_physics_preset.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "DATA/flowlab_native_setup_checklist.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "DATA/flowlab_code_saturne_capability_matrix.json" in report["caseValidation"]["requiredFilesPresent"]
    assert "DATA/cs_user_physics.py" in report["caseValidation"]["requiredFilesPresent"]
    assert "SRC/cs_user_boundary_conditions.f90" in report["caseValidation"]["requiredFilesPresent"]
    assert "MESH/flowlab_mesh.msh" in report["caseValidation"]["requiredFilesPresent"]
    assert "mesh/quality.json" in report["caseValidation"]["requiredFilesPresent"]


def test_code_saturne_smoke_reports_failed_resu_diagnostics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FLOWLAB_CODE_SATURNE_IMAGE", raising=False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")

    def manager_factory(runtime_root: Path) -> JobManager:
        return JobManager(runtime_root=runtime_root, popen_factory=lambda command, **kwargs: CodeSaturneSmokeFailingProcess(command, **kwargs))

    report = run_code_saturne_smoke(tmp_path, timeout_seconds=2, manager_factory=manager_factory)

    assert report["completed"] is False
    assert report["status"] == "failed"
    assert report["execution"] == "native"
    assert report["exitCode"] == 1
    assert report["diagnosticFiles"][0]["path"] == "RESU/flowlab/listing"
    assert report["diagnosticSummary"][0]["kind"] == "code-saturne-error"
    assert "boundary condition type 0" in report["diagnosticSummary"][0]["excerpts"][1]
