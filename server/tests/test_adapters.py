from __future__ import annotations

import json

import pytest

from server.flowlab import adapters
from server.flowlab.execution import validate_solver_case
from server.flowlab.schemas import CaseRequest


def _request(solver: str, advanced_mode: str = "incompressible-navier-stokes") -> CaseRequest:
    return CaseRequest.model_construct(
        project={"name": "Adapter smoke case", "nodes": [], "edges": []},
        solver=solver,
        advancedMode=advanced_mode,
    )


def _parameterized_project() -> dict:
    return {
        "name": "Parameterized case",
        "fluid": {
            "density": 1000.0,
            "dynamicViscosity": 0.002,
            "temperature": 300.0,
            "vaporPressure": 2500.0,
            "bulkModulus": 2.1e9,
        },
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "pressure": 260000.0, "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "pressure": 111325.0, "flowDemand": 0.02, "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 10,
                "shape": {"kind": "rectangular", "width": 0.2, "height": 0.1},
            }
        },
    }


def test_capabilities_include_instant_1d(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    caps = {cap.id: cap for cap in adapters.capabilities()}

    assert "instant-1d" in caps
    assert caps["instant-1d"].installed is True
    assert caps["instant-1d"].execution == "browser"
    assert any("browser" in note for note in caps["instant-1d"].notes)


def test_openfoam_case_generation_returns_files_provenance_and_run_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(_request("openfoam", "heat-transfer"))

    assert case.solver == "openfoam"
    assert case.advancedMode == "heat-transfer"
    assert case.projectName == "Adapter smoke case"
    assert case.status in {"generated", "blocked"}
    assert {
        "README.md",
        "Allrun",
        "0/U",
        "0/p",
        "0/T",
        "system/blockMeshDict",
        "system/controlDict",
        "system/functions",
        "system/fvSchemes",
        "system/fvSolution",
        "constant/transportProperties",
        "constant/turbulenceProperties",
        "constant/flowlab.json",
    }.issubset(case.files)
    assert case.files["README.md"].startswith("# Adapter smoke case")
    assert "wallDist" in case.files["system/fvSchemes"]
    assert "meshWave" in case.files["system/fvSchemes"]
    assert "div(phi,(p|rho))" in case.files["system/fvSchemes"]
    assert "div(phi,K)" in case.files["system/fvSchemes"]
    assert "div(phi,e)" in case.files["system/fvSchemes"]
    assert "div(phi,h)" in case.files["system/fvSchemes"]
    assert "(U|T|e|h|k|omega|epsilon)" in case.files["system/fvSolution"]
    assert "(U|T|e|h|k|omega|epsilon)Final" in case.files["system/fvSolution"]
    assert "rhoFinal" in case.files["system/fvSolution"]
    manifest = json.loads(case.files["flowlab_case_manifest.json"])
    assert manifest["schema"] == "flowlab.case_manifest.v1"
    assert manifest["solver"] == "openfoam"
    assert manifest["files"]["Allrun"]["sha256"]
    assert "blockMesh" in case.files["Allrun"]
    assert "foamRun -solver fluid" in case.files["Allrun"]
    assert "foamToVTK -ascii -latestTime" in case.files["Allrun"]
    assert "boundary" in case.files["system/blockMeshDict"]
    assert "frontAndBack" in case.files["0/U"]
    assert "functions" in case.files["system/controlDict"]
    assert "patchFlowRate" in case.files["system/functions"]
    assert "type            residuals;" in case.files["system/controlDict"]
    assert "type            probes;" in case.files["system/controlDict"]
    assert "type            forces;" in case.files["system/controlDict"]
    assert "patchFlowRate" in case.files["system/controlDict"]
    assert "type            patchFlowRate;" in case.files["system/controlDict"]
    assert "patchAverage" in case.files["system/controlDict"]
    assert "type            surfaceFieldValue;" in case.files["system/controlDict"]
    assert "operation       areaAverage;" in case.files["system/controlDict"]
    assert "wallShearStress" in case.files["system/controlDict"]
    assert "patches         (inlet outlet);" in case.files["system/controlDict"]
    assert "name            (inlet outlet);" in case.files["system/controlDict"]
    assert "patches         (walls);" in case.files["system/controlDict"]
    assert "fields          (U p p_rgh T rho);" in case.files["system/controlDict"]
    assert "k omega" not in case.files["system/controlDict"]
    assert "probeLocations" in case.files["system/controlDict"]
    patch_metrics = json.loads(case.files["constant/flowlab_patch_metrics.json"])
    assert patch_metrics["schema"] == "flowlab.openfoam_patch_metric_function_objects.v1"
    assert patch_metrics["patches"]["inlet"] == ["inlet"]
    assert patch_metrics["patches"]["outlet"] == ["outlet"]
    assert patch_metrics["patches"]["wall"] == ["walls"]
    assert patch_metrics["patches"]["flow"] == ["inlet", "outlet"]
    assert patch_metrics["functionObjects"] == ["patchFlowRate", "patchAverage", "wallShearStress", "wallForces"]
    runtime_functions = json.loads(case.files["constant/flowlab_openfoam_function_objects.json"])
    assert runtime_functions["schema"] == "flowlab.openfoam_function_object_runtime.v1"
    assert runtime_functions["contract"] == "constant/flowlab_patch_metrics.json"
    assert "foundation" in runtime_functions["runtimeStyles"]
    assert "opencfd" in runtime_functions["runtimeStyles"]
    assert "constant/thermophysicalProperties" in case.files
    assert "thermoType" in case.files["constant/physicalProperties"]
    assert "properties      liquid" in case.files["constant/physicalProperties"]
    assert "H2O" in case.files["constant/physicalProperties"]
    assert "internalField   uniform 101325" in case.files["0/p"]
    assert "internalField   uniform 101325" in case.files["0/p_rgh"]
    assert "0/rho" in case.files
    assert "0/k" not in case.files
    assert "0/omega" not in case.files
    assert "0/nut" not in case.files
    assert "0/alphat" not in case.files
    assert "simulationType laminar" in case.files["constant/turbulenceProperties"]
    assert case.runCommand == ["bash", "Allrun"]
    assert any("OpenFOAM" in entry and "CFD" in entry for entry in case.provenance)


def test_openfoam_parallel_opt_in_emits_a_reviewable_parallel_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["solver"]["performance"] = {
        "openfoamParallel": {
            "enabled": True,
            "ranks": 4,
            "decomposition": "scotch",
        }
    }

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    plan = json.loads(case.files["constant/flowlab_openfoam_parallel_plan.json"])
    assert plan["execution"] == "parallel-candidate"
    assert plan["ranks"] == 4
    assert plan["decomposition"] == "scotch"
    assert plan["performanceStatus"] == "benchmark-required"
    assert plan["speedupClaim"] is None
    assert "system/decomposeParDict" in case.files
    assert "numberOfSubdomains 4;" in case.files["system/decomposeParDict"]
    assert "method          scotch;" in case.files["system/decomposeParDict"]
    assert "decomposePar -force" in case.files["Allrun"]
    assert "mpirun -np 4 checkMesh -parallel -allGeometry -allTopology" in case.files["Allrun"]
    assert "mpirun -np 4 foamRun -solver incompressibleFluid -parallel" in case.files["Allrun"]
    assert "reconstructPar -latestTime" in case.files["Allrun"]
    assert any("parallel execution is an opt-in" in entry for entry in case.provenance)
    assert validate_solver_case(case) == []


def test_openfoam_parallel_opt_in_rejects_unverified_advanced_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["solver"]["performance"] = {
        "openfoamParallel": {
            "enabled": True,
            "ranks": 4,
            "decomposition": "scotch",
        }
    }

    with pytest.raises(ValueError, match="limited to incompressible-navier-stokes"):
        adapters.generate_case(
            CaseRequest.model_construct(
                project=project,
                solver="openfoam",
                advancedMode="heat-transfer",
            )
        )


def test_parallel_case_validator_rejects_missing_parallel_launch_or_wrong_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["solver"]["performance"] = {
        "openfoamParallel": {"enabled": True, "ranks": 4, "decomposition": "scotch"}
    }
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    missing_parallel = case.model_copy(deep=True)
    missing_parallel.files["Allrun"] = missing_parallel.files["Allrun"].replace(
        "foamRun -solver incompressibleFluid -parallel",
        "foamRun -solver incompressibleFluid",
    )
    missing_parallel = adapters.add_case_manifest(missing_parallel)
    missing_parallel_issues = validate_solver_case(missing_parallel)

    wrong_mode = case.model_copy(update={"advancedMode": "heat-transfer"}, deep=True)
    wrong_mode = adapters.add_case_manifest(wrong_mode)
    wrong_mode_issues = validate_solver_case(wrong_mode)

    assert any("foamRun through mpirun with -parallel" in issue for issue in missing_parallel_issues)
    assert any("limited to incompressible-navier-stokes" in issue for issue in wrong_mode_issues)


def test_parallel_case_validator_rejects_commented_or_out_of_order_parallel_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["solver"]["performance"] = {
        "openfoamParallel": {"enabled": True, "ranks": 4, "decomposition": "scotch"}
    }
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    commented = case.model_copy(deep=True)
    commented.files["Allrun"] = commented.files["Allrun"].replace(
        "\ndecomposePar -force\n",
        "\n# decomposePar -force\n",
    )
    commented = adapters.add_case_manifest(commented)

    out_of_order = case.model_copy(deep=True)
    out_of_order.files["Allrun"] = out_of_order.files["Allrun"].replace(
        "\ndecomposePar -force\n",
        "\n",
    ) + "\ndecomposePar -force\n"
    out_of_order = adapters.add_case_manifest(out_of_order)

    commented_issues = validate_solver_case(commented)
    out_of_order_issues = validate_solver_case(out_of_order)

    assert any("must execute decomposePar -force" in issue for issue in commented_issues)
    assert any("must order serial checkMesh" in issue for issue in out_of_order_issues)


def test_openfoam_reviewed_multi_surface_patches_drive_metric_function_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["solver"]["reviewedGeometry"] = {
        "sourceType": "multi-surface-stl",
        "cadReviewed": True,
        "surfaces": [
            {
                "id": "surf-inlet",
                "surfaceName": "inletSurface",
                "role": "inlet",
                "patchName": "reviewedInlet",
                "cadReviewed": True,
                "boundaryCondition": {"type": "velocity-inlet"},
            },
            {
                "id": "surf-outlet",
                "surfaceName": "outletSurface",
                "role": "outlet",
                "patchName": "reviewedOutlet",
                "cadReviewed": True,
                "boundaryCondition": {"type": "pressure-outlet"},
            },
            {
                "id": "surf-wall",
                "surfaceName": "wallSurface",
                "role": "wall",
                "patchName": "reviewedWalls",
                "cadReviewed": True,
                "boundaryCondition": {"type": "no-slip-wall"},
            },
        ],
    }

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    control_dict = case.files["system/controlDict"]
    assert "patches         (reviewedInlet reviewedOutlet);" in control_dict
    assert "name            (reviewedInlet reviewedOutlet);" in control_dict
    assert "patches         (reviewedWalls);" in control_dict
    patch_metrics = json.loads(case.files["constant/flowlab_patch_metrics.json"])
    assert patch_metrics["patches"]["inlet"] == ["reviewedInlet"]
    assert patch_metrics["patches"]["outlet"] == ["reviewedOutlet"]
    assert patch_metrics["patches"]["wall"] == ["reviewedWalls"]
    assert patch_metrics["patches"]["flow"] == ["reviewedInlet", "reviewedOutlet"]


def test_openfoam_probe_nodes_emit_pressure_probe_function_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()
    project["nodes"]["pressure-probe"] = {
        "id": "pressure-probe",
        "type": "probe",
        "position": {"x": 50, "y": 12},
        "z": 0.02,
    }

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    control_dict = case.files["system/controlDict"]
    assert "pressureProbes" in control_dict
    assert "fields          (p p_rgh);" in control_dict
    assert "(0.500000 0.120000 0.020000)" in control_dict
    patch_metrics = json.loads(case.files["constant/flowlab_patch_metrics.json"])
    assert "pressureProbes" in patch_metrics["functionObjects"]
    assert patch_metrics["pressureProbeLocations"] == [[0.5, 0.12, 0.02]]


def test_openfoam_compressible_mode_uses_laminar_gas_starter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(_request("openfoam", "compressible-flow"))

    assert "foamRun -solver shockFluid" in case.files["Allrun"]
    assert "type            hePsiThermo" in case.files["constant/physicalProperties"]
    assert "equationOfState perfectGas" in case.files["constant/physicalProperties"]
    assert "fields          (U p p_rgh T rho);" in case.files["system/controlDict"]
    assert "endTime         0.001;" in case.files["system/controlDict"]
    assert "deltaT          0.00001;" in case.files["system/controlDict"]
    assert "internalField   uniform 1.20411832" in case.files["0/rho"]
    assert "rho             [1 -3 0 0 0 0 0] 1.20411832" in case.files["constant/transportProperties"]
    assert "0/k" not in case.files
    assert "0/omega" not in case.files
    assert "0/nut" not in case.files
    assert "0/alphat" not in case.files
    assert "simulationType laminar" in case.files["constant/turbulenceProperties"]


def test_openfoam_conjugate_heat_transfer_emits_multiregion_case_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_parameterized_project(),
            solver="openfoam",
            advancedMode="conjugate-heat-transfer",
        )
    )

    assert case.status == "generated"
    assert "full foamMultiRun remains blocked" in case.files["Allrun"]
    assert case.files["Allrun"].index("checkMesh -allGeometry -allTopology") < case.files["Allrun"].index("full foamMultiRun remains blocked")
    executable_lines = [
        line.strip()
        for line in case.files["Allrun"].splitlines()
        if line.strip() and not line.strip().startswith("echo") and not line.strip().startswith("#")
    ]
    assert "foamMultiRun" not in executable_lines
    assert "chtMultiRegionFoam" not in executable_lines
    assert "AllmeshCheck" in case.files
    assert "checkMesh -region fluid -allGeometry -allTopology" in case.files["AllmeshCheck"]
    assert "checkMesh -region solid -allGeometry -allTopology" in case.files["AllmeshCheck"]
    assert "foamRun -solver foamMultiRun" not in case.files["Allrun"]
    assert "application     foamMultiRun;" in case.files["system/controlDict"]
    assert "regionSolvers" in case.files["system/controlDict"]
    assert case.files["system/controlDict"].count("fluid") >= 2
    assert case.files["system/controlDict"].count("solid") >= 2
    assert "0/fluid/U" in case.files
    assert "0/fluid/T" in case.files
    assert "0/solid/T" in case.files
    assert "fluid_to_solid" in case.files["0/fluid/T"]
    assert "solid_to_fluid" in case.files["0/solid/T"]
    assert "coupledTemperature" in case.files["0/fluid/T"]
    assert "coupledTemperature" in case.files["0/solid/T"]
    assert "constant/fluid/physicalProperties" in case.files
    assert "type            heRhoThermo" in case.files["constant/fluid/physicalProperties"]
    assert "constant/fluid/momentumTransport" in case.files
    assert "constant/solid/physicalProperties" in case.files
    assert "type            heSolidThermo" in case.files["constant/solid/physicalProperties"]
    assert "constant/flowlab_cht_interface.json" in case.files
    interface = json.loads(case.files["constant/flowlab_cht_interface.json"])
    assert interface["schema"] == "flowlab.openfoam_cht_interface.v1"
    assert interface["productionReady"] is False
    assert interface["patches"]["fluid"]["neighbourPatch"] == "solid_to_fluid"
    assert interface["patches"]["solid"]["neighbourRegion"] == "fluid"
    assert interface["readinessChecks"][0]["id"] == "multi-region-dictionaries"
    assert interface["interfaceApproximation"] == "outer-wall-offset-starter-sleeve"
    assert interface["solidJacket"]["nonOverlapping"] is True
    assert interface["solidJacket"]["innerInterfaceFaceCount"] == interface["patches"]["fluid"]["faceCount"]
    assert any(check["id"] == "non-overlapping-solid-jacket" and check["status"] == "pass" for check in interface["readinessChecks"])
    assert any(check["id"] == "region-checkmesh-plan" and check["status"] == "pass" for check in interface["readinessChecks"])
    assert any(check["id"] == "region-checkmesh-evidence" and check["status"] == "fail" for check in interface["readinessChecks"])
    assert interface["regionMeshChecks"]["script"] == "AllmeshCheck"
    assert interface["regionMeshChecks"]["commands"] == [
        "checkMesh -region fluid -allGeometry -allTopology",
        "checkMesh -region solid -allGeometry -allTopology",
    ]
    assert interface["regionMeshChecks"]["evidenceStatus"] == "planned-not-executed"
    assert any("Per-region OpenFOAM checkMesh evidence" in reason for reason in interface["blockingReasons"])
    assert "constant/fluid/polyMesh/boundary" in case.files
    assert "fluid_to_solid" in case.files["constant/fluid/polyMesh/boundary"]
    assert "constant/solid/polyMesh/boundary" in case.files
    assert "solid_to_fluid" in case.files["constant/solid/polyMesh/boundary"]
    assert "solid_outer_wall" in case.files["constant/solid/polyMesh/boundary"]
    assert "system/fluid/fvSchemes" in case.files
    assert "system/fluid/fvSolution" in case.files
    assert "system/solid/fvSchemes" in case.files
    assert "system/solid/fvSolution" in case.files
    assert any("foamMultiRun fluid/solid multi-region starter bundle" in entry for entry in case.provenance)


def test_openfoam_multiphase_and_cavitation_modes_include_phase_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    vof_case = adapters.generate_case(_request("openfoam", "multiphase-vof"))
    cavitation_case = adapters.generate_case(_request("openfoam", "cavitation"))

    assert "foamRun -solver incompressibleVoF" in vof_case.files["Allrun"]
    assert "0/alpha.water" in vof_case.files
    assert "0/alpha.air" in vof_case.files
    assert "constant/phaseProperties" in vof_case.files
    assert "constant/physicalProperties.water" in vof_case.files
    assert "constant/physicalProperties.air" in vof_case.files
    assert "viscosityModel  constant" in vof_case.files["constant/physicalProperties.water"]
    assert "sigma           0.072;" in vof_case.files["constant/phaseProperties"]
    assert "alpha.water" in vof_case.files["system/fvSolution"]
    assert "cAlpha          1;" in vof_case.files["system/fvSolution"]
    assert '"pcorr.*"' in vof_case.files["system/fvSolution"]
    assert "div(rhoPhi,U)   Gauss linearUpwind grad(U);" in vof_case.files["system/fvSchemes"]
    assert "div(rhoPhi,alpha) Gauss vanLeer;" in vof_case.files["system/fvSchemes"]
    assert "flowlabCavitationPreset no" in vof_case.files["constant/phaseProperties"]
    assert "0/k" not in vof_case.files
    assert "0/omega" not in vof_case.files
    assert "simulationType laminar" in vof_case.files["constant/turbulenceProperties"]

    assert "foamRun -solver compressibleVoF" in cavitation_case.files["Allrun"]
    assert "0/alpha.vapour" in cavitation_case.files
    assert "0/alpha.water" not in cavitation_case.files
    assert "0/alpha.air" not in cavitation_case.files
    assert "constant/physicalProperties.water" in cavitation_case.files
    assert "constant/physicalProperties.vapour" in cavitation_case.files
    assert "thermoType" in cavitation_case.files["constant/physicalProperties.water"]
    assert "perfectGas" in cavitation_case.files["constant/physicalProperties.vapour"]
    assert "constant/thermodynamicProperties" in cavitation_case.files
    assert "constant/fvModels" in cavitation_case.files
    assert "compressible::VoFCavitation" in cavitation_case.files["constant/fvModels"]
    assert "alpha.vapour" in cavitation_case.files["system/fvSolution"]
    assert "cAlpha          1;" in cavitation_case.files["system/fvSolution"]
    assert '"(rho|alpha.*)"' not in cavitation_case.files["system/fvSolution"]
    assert "constant/cavitationProperties" in cavitation_case.files
    assert "SchnerrSauer" in cavitation_case.files["constant/cavitationProperties"]
    assert "SchnerrSauerCoeffs" in cavitation_case.files["constant/fvModels"]
    assert "phases          (vapour water);" in cavitation_case.files["constant/phaseProperties"]
    assert "flowlabCavitationPreset yes" in cavitation_case.files["constant/phaseProperties"]
    assert any("Cavitation mode" in entry for entry in cavitation_case.provenance)


def test_openfoam_water_hammer_mode_records_tier1_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(
        CaseRequest.model_construct(project=_parameterized_project(), solver="openfoam", advancedMode="water-hammer")
    )
    handoff = json.loads(case.files["constant/waterHammerPreview.json"])

    assert "foamRun -solver incompressibleFluid" in case.files["Allrun"]
    assert case.files["Allrun"].index("checkMesh -allGeometry -allTopology") < case.files["Allrun"].index("foamRun -solver")
    assert "constant/waterHammerPreview.json" in case.files
    assert "constant/waterHammerWaveform.csv" in case.files
    assert "method-of-characteristics-preview" in case.files["constant/waterHammerPreview.json"]
    assert handoff["schema"] == "flowlab.water_hammer_handoff.v1"
    assert handoff["cfdCoupling"] == "pressure-wave-boundary-preview"
    assert handoff["productionReady"] is False
    assert handoff["fluid"]["waveSpeed"] == pytest.approx((2.1e9 / 1000.0) ** 0.5)
    assert handoff["dominantEdgeId"] == "pipe"
    assert handoff["edges"][0]["velocity"] == pytest.approx(1.0)
    assert handoff["edges"][0]["effectiveLength"] == pytest.approx(4.6)
    assert handoff["edges"][0]["pressureRise"] == pytest.approx(1000.0 * (2.1e9 / 1000.0) ** 0.5)
    assert handoff["edges"][0]["criticalClosureTime"] == pytest.approx(2 * 4.6 / ((2.1e9 / 1000.0) ** 0.5))
    assert case.files["constant/waterHammerWaveform.csv"].startswith("time,kinematicPressure,absolutePressure")
    assert "uniformValue    table" in case.files["0/p"]
    assert "type            uniformFixedValue;" in case.files["0/p"]
    assert any("Water-hammer mode" in entry for entry in case.provenance)


def test_openfoam_graph_case_includes_fitted_polymesh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "foamRun")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(
        CaseRequest.model_construct(project=_parameterized_project(), solver="openfoam", advancedMode="incompressible-navier-stokes")
    )

    assert case.status == "generated"
    assert "using fitted constant/polyMesh" in case.files["Allrun"]
    assert case.files["Allrun"].index("checkMesh -allGeometry -allTopology") < case.files["Allrun"].index("foamRun -solver")
    assert "blockMesh fallback" in case.files["Allrun"]
    assert "constant/polyMesh/points" in case.files
    assert "constant/polyMesh/faces" in case.files
    assert "constant/polyMesh/owner" in case.files
    assert "constant/polyMesh/neighbour" in case.files
    assert "constant/polyMesh/boundary" in case.files
    assert "mesh/boundary_layer_plan.json" in case.files
    assert "mesh/production_mesh_plan.json" in case.files
    assert "mesh/production_mesh_acceptance.json" in case.files
    assert "polyBoundaryMesh" in case.files["constant/polyMesh/boundary"]
    assert "inlet" in case.files["constant/polyMesh/boundary"]
    assert "outlet" in case.files["constant/polyMesh/boundary"]
    assert "frontAndBack" in case.files["constant/polyMesh/boundary"]
    review = json.loads(case.files["mesh/openfoam_review.json"])
    assert review["schema"] == "flowlab.openfoam_mesh_review.v1"
    assert review["productionReady"] is False
    assert review["meshGenerated"] is True
    assert review["sourceMesh"]["boundaryLayerPlanSchema"] == "flowlab.boundary_layer_plan.v1"
    assert review["sourceMesh"]["boundaryLayerEdgeCount"] >= 1
    assert review["sourceMesh"]["productionMeshPlanSchema"] == "flowlab.production_mesh_plan.v1"
    assert review["sourceMesh"]["productionMeshReady"] is False
    assert any(check["id"] == "fitted-polymesh-export" and check["status"] == "pass" for check in review["readinessChecks"])
    assert any(check["id"] == "cad-quality-3d-topology" and check["status"] == "fail" for check in review["readinessChecks"])
    assert any("y-plus first-cell sizing plan" in check["detail"] for check in review["readinessChecks"])
    production_plan = json.loads(case.files["mesh/production_mesh_plan.json"])
    assert production_plan["schema"] == "flowlab.production_mesh_plan.v1"
    assert production_plan["productionReady"] is False
    assert any(check["id"] == "production-3d-volume-mesh" for check in production_plan["readinessChecks"])
    production_acceptance = json.loads(case.files["mesh/production_mesh_acceptance.json"])
    assert production_acceptance["schema"] == "flowlab.production_mesh_acceptance.v1"
    assert production_acceptance["productionReady"] is False
    assert production_acceptance["solverAcceptance"]["openfoam"]["status"] == "blocked"
    assert any("hexahedral layer" in entry for entry in case.provenance)


def test_solver_templates_use_project_fluid_boundary_and_flow_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command in {"foamRun", "SU2_CFD", "code_saturne"})
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()

    openfoam = adapters.generate_case(
        CaseRequest.model_construct(project=project, solver="openfoam", advancedMode="incompressible-navier-stokes")
    )
    su2 = adapters.generate_case(CaseRequest.model_construct(project=project, solver="su2", advancedMode="incompressible-navier-stokes"))
    code_saturne = adapters.generate_case(
        CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode="incompressible-navier-stokes")
    )

    assert "nu              [0 2 -1 0 0 0 0] 2e-06;" in openfoam.files["constant/transportProperties"]
    assert "rho             [1 -3 0 0 0 0 0] 1000;" in openfoam.files["constant/transportProperties"]
    assert "internalField   uniform 10;" in openfoam.files["0/p"]
    assert "value           uniform (1 0 0);" in openfoam.files["0/U"]
    assert "internalField   uniform 300;" in openfoam.files["0/T"]

    assert "INC_DENSITY_INIT= 1000.0" in su2.files["case.cfg"]
    assert "MU_CONSTANT= 0.002" in su2.files["case.cfg"]
    assert "MARKER_INLET= ( inlet_pipe, 300.0, 1.0, 1.0, 0.0, 0.0 )" in su2.files["case.cfg"]
    assert "MARKER_OUTLET= ( outlet_pipe, 10000.0 )" in su2.files["case.cfg"]

    assert '<fluid density="1000" dynamic_viscosity="0.002"/>' in code_saturne.files["DATA/setup.xml"]
    code_saturne_preset = json.loads(code_saturne.files["DATA/flowlab_physics_preset.json"])
    assert code_saturne_preset["fluid"]["density"] == 1000.0
    assert code_saturne_preset["fluid"]["dynamicViscosity"] == 0.002
    assert code_saturne_preset["fluid"]["inletVelocity"] == pytest.approx(1.0)
    assert '<reference pressure="111325"/>' in code_saturne.files["DATA/setup.xml"]
    assert "<norm>1</norm>" in code_saturne.files["DATA/setup.xml"]
    assert "<hydraulic_diameter>0.133333333</hydraulic_diameter>" in code_saturne.files["DATA/setup.xml"]
    assert "<dirichlet name=\"pressure\">10000</dirichlet>" in code_saturne.files["DATA/setup.xml"]
    assert "rcodcl(ifac,iu,1) = 1d0" in code_saturne.files["SRC/cs_user_boundary_conditions.f90"]


def test_openfoam_capability_requires_the_generated_native_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "simpleFoam")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    cap = adapters.OpenFOAMAdapter().capability()

    assert cap.installed is False
    assert any("Install native binaries" in note for note in cap.notes)


def test_openfoam_capability_reports_configured_shared_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(adapters.OPENFOAM_IMAGE_ENV, "flowlab/openfoam:test")
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)

    cap = adapters.OpenFOAMAdapter().capability()

    assert cap.installed is True
    assert cap.execution == "docker"
    assert any("flowlab/openfoam:test" in note for note in cap.notes)


def test_code_saturne_capability_tracks_native_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    cap = adapters.CodeSaturneAdapter().capability()

    assert cap.installed is True
    assert cap.execution == "native"
    assert any("Native code_saturne" in note for note in cap.notes)


def test_code_saturne_capability_tracks_configured_docker_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWLAB_CODE_SATURNE_IMAGE", "flowlab-code-saturne:test")
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)

    cap = adapters.CodeSaturneAdapter().capability()

    assert cap.installed is True
    assert cap.execution == "docker"
    assert any("flowlab-code-saturne:test" in note for note in cap.notes)


def test_generate_case_rejects_unsupported_solver_via_adapter_function() -> None:
    with pytest.raises(ValueError, match="Unsupported solver: made-up-solver"):
        adapters.generate_case(_request("made-up-solver"))


def test_mujoco_case_provenance_clarifies_it_is_not_cfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists", lambda module: False)

    case = adapters.generate_case(_request("mujoco", "rigid-body-fluid-forces"))

    assert case.solver == "mujoco"
    assert case.status == "blocked"
    assert "model.xml" in case.files
    assert "run_mujoco.py" in case.files
    assert "does not solve Navier-Stokes CFD fields" in case.files["README.md"]
    assert any("not for Navier-Stokes field solves" in entry for entry in case.provenance)
    assert case.runCommand == ["python3", "run_mujoco.py"]


def test_mujoco_case_is_generated_when_python_module_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists", lambda module: module == "mujoco")

    case = adapters.generate_case(_request("mujoco", "rigid-body-fluid-forces"))
    cap = adapters.MuJoCoAdapter().capability()

    assert cap.installed is True
    assert case.status == "generated"
    assert case.runCommand == ["python3", "run_mujoco.py"]
    assert "mujoco.MjModel.from_xml_path" in case.files["run_mujoco.py"]
    assert "outputs/mujoco_fluid_force_0001.vtk" in case.files["README.md"]


def test_mujoco_case_uses_configured_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    monkeypatch.setenv("FLOWLAB_MUJOCO_PYTHON", str(python))
    monkeypatch.setattr(adapters, "_python_module_exists_for_command", lambda command, module: command == str(python) and module == "mujoco")

    case = adapters.generate_case(_request("mujoco", "rigid-body-fluid-forces"))
    cap = adapters.MuJoCoAdapter().capability()

    assert cap.installed is True
    assert any("FLOWLAB_MUJOCO_PYTHON selected" in note for note in cap.notes)
    assert case.status == "generated"
    assert case.runCommand == [str(python), "run_mujoco.py"]
    assert f"Run with `{python} run_mujoco.py`" in case.files["README.md"]


def test_mujoco_case_uses_project_fluid_scale_and_reference_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists_for_command", lambda command, module: command == "python3" and module == "mujoco")

    case = adapters.generate_case(
        CaseRequest.model_construct(project=_parameterized_project(), solver="mujoco", advancedMode="rigid-body-fluid-forces")
    )

    assert case.status == "generated"
    assert 'density="1000" viscosity="0.002"' in case.files["model.xml"]
    assert 'size="0.133333333 0.0333333333 0.0333333333"' in case.files["model.xml"]
    assert "REFERENCE_VELOCITY = 1" in case.files["run_mujoco.py"]
    assert "REFERENCE_AREA = 0.02" in case.files["run_mujoco.py"]
    assert any("density, viscosity, body scale" in entry for entry in case.provenance)


def test_su2_case_is_generated_when_solver_and_native_mesh_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    project = {
        "name": "SU2 mesh case",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 10,
                "shape": {"kind": "circular", "diameter": 0.1},
            }
        },
    }
    case = adapters.generate_case(CaseRequest.model_construct(project=project, solver="su2", advancedMode="incompressible-navier-stokes"))

    assert case.status == "generated"
    assert "mesh/flowlab_mesh.su2" in case.files
    assert "MESH_FILENAME= mesh/flowlab_mesh.su2" in case.files["case.cfg"]
    assert "SOLVER= INC_NAVIER_STOKES" in case.files["case.cfg"]
    assert "INC_ENERGY_EQUATION= NO" in case.files["case.cfg"]
    assert "VISCOSITY_MODEL= CONSTANT_VISCOSITY" in case.files["case.cfg"]
    assert "INC_INLET_TYPE= VELOCITY_INLET" in case.files["case.cfg"]
    assert "MARKER_OUTLET= ( outlet_pipe, 0.0 )" in case.files["case.cfg"]
    preset = json.loads(case.files["flowlab_su2_mode_preset.json"])
    checklist = json.loads(case.files["flowlab_su2_native_setup_checklist.json"])
    matrix = json.loads(case.files["flowlab_su2_capability_matrix.json"])
    assert preset["schema"] == "flowlab.su2_mode_preset.v1"
    assert preset["advancedMode"] == "incompressible-navier-stokes"
    assert preset["supportedByAdapter"] is True
    assert preset["requestedPhysicsResolved"] is True
    assert preset["supportLevel"] == "starter-supported-single-zone"
    assert preset["solver"] == "INC_NAVIER_STOKES"
    assert "constant-density fluid" in preset["requiredCapabilities"]
    assert any(check["id"] == "native-su2-mesh" and check["status"] == "pass" for check in preset["readinessChecks"])
    assert any(check["id"] == "production-mesh-review" and check["status"] == "fail" for check in preset["readinessChecks"])
    assert "FlowLab mesh is a deterministic starter quad-strip mesh" in preset["blockingReasons"][0]
    assert preset["resultExpectations"]["volumeFilename"] == "flowlab_su2"
    assert preset["resultExpectations"]["expectedPrimaryFields"] == ["pressure", "velocity", "residual_history"]
    assert preset["nativeSetupPlan"]["manualNativeModules"] == []
    assert preset["nativeSetupPlan"]["caseCfgGenerated"] is True
    assert checklist["schema"] == "flowlab.su2_native_setup_checklist.v1"
    assert checklist["advancedMode"] == "incompressible-navier-stokes"
    assert checklist["supportLevel"] == preset["supportLevel"]
    assert checklist["requestedPhysicsResolved"] is True
    assert checklist["productionReady"] is False
    assert "case.cfg" in checklist["generatedFiles"]
    assert checklist["expectedPrimaryFields"] == preset["resultExpectations"]["expectedPrimaryFields"]
    assert checklist["actionItems"] == []
    assert "flowlab_su2_capability_matrix.json" in checklist["generatedFiles"]
    assert matrix["schema"] == "flowlab.su2_capability_matrix.v1"
    assert matrix["activeMode"] == "incompressible-navier-stokes"
    assert matrix["productionReady"] is False
    assert matrix["summary"]["modeCount"] == 8
    assert set(matrix["summary"]["starterSupportedModes"]) == {
        "incompressible-navier-stokes",
        "compressible-flow",
        "heat-transfer",
    }
    assert {"multiphase-vof", "cavitation", "water-hammer", "conjugate-heat-transfer", "rigid-body-fluid-forces"}.issubset(
        set(matrix["summary"]["blockedExportOnlyModes"])
    )
    assert {"multiphase-vof", "cavitation", "water-hammer", "conjugate-heat-transfer", "rigid-body-fluid-forces"}.issubset(
        set(matrix["summary"]["handoffModes"])
    )
    active_entry = next(entry for entry in matrix["entries"] if entry["advancedMode"] == "incompressible-navier-stokes")
    assert active_entry["active"] is True
    assert active_entry["supportLevel"] == preset["supportLevel"]
    assert active_entry["supportedByAdapter"] is True
    blocked_entry = next(entry for entry in matrix["entries"] if entry["advancedMode"] == "cavitation")
    assert blocked_entry["supportedByAdapter"] is False
    assert blocked_entry["manualNativeModules"]
    assert "vapour_fraction" in blocked_entry["expectedPrimaryFields"]
    assert "mesh/README.su2.md" in case.files
    assert "flowlab_su2_mode_preset.json" in case.files["mesh/README.su2.md"]
    assert "flowlab_su2_native_setup_checklist.json" in case.files["mesh/README.su2.md"]
    assert "flowlab_su2_capability_matrix.json" in case.files["mesh/README.su2.md"]
    assert any("native SU2 ASCII mesh" in entry for entry in case.provenance)
    assert any("mode preset" in entry for entry in case.provenance)


def test_su2_compressible_and_heat_transfer_modes_emit_mode_specific_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    project = {
        "name": "SU2 advanced modes",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 10,
                "shape": {"kind": "circular", "diameter": 0.1},
            }
        },
    }

    compressible = adapters.generate_case(CaseRequest.model_construct(project=project, solver="su2", advancedMode="compressible-flow"))
    heat = adapters.generate_case(CaseRequest.model_construct(project=project, solver="su2", advancedMode="heat-transfer"))

    assert compressible.status == "generated"
    assert "SOLVER= NAVIER_STOKES" in compressible.files["case.cfg"]
    assert "FLUID_MODEL= STANDARD_AIR" in compressible.files["case.cfg"]
    assert "INLET_TYPE= TOTAL_CONDITIONS" in compressible.files["case.cfg"]
    assert "MARKER_HEATFLUX= ( wall_pipe_left, 0.0, wall_pipe_right, 0.0 )" in compressible.files["case.cfg"]
    compressible_preset = json.loads(compressible.files["flowlab_su2_mode_preset.json"])
    compressible_matrix = json.loads(compressible.files["flowlab_su2_capability_matrix.json"])
    assert compressible_preset["solver"] == "NAVIER_STOKES"
    assert "STANDARD_AIR" in compressible_preset["enabledStarterModels"]
    assert "mach_number" in compressible_preset["resultExpectations"]["expectedPrimaryFields"]
    assert compressible_preset["nativeSetupPlan"]["manualNativeModules"] == []
    assert compressible_matrix["activeMode"] == "compressible-flow"
    assert next(entry for entry in compressible_matrix["entries"] if entry["advancedMode"] == "compressible-flow")["active"] is True

    assert heat.status == "generated"
    assert "SOLVER= INC_NAVIER_STOKES" in heat.files["case.cfg"]
    assert "INC_ENERGY_EQUATION= YES" in heat.files["case.cfg"]
    assert "MARKER_ISOTHERMAL= ( wall_pipe_left, 320.0, wall_pipe_right, 320.0 )" in heat.files["case.cfg"]
    assert "SPECIFIC_HEAT_CP= 4182.0" in heat.files["case.cfg"]
    heat_preset = json.loads(heat.files["flowlab_su2_mode_preset.json"])
    heat_matrix = json.loads(heat.files["flowlab_su2_capability_matrix.json"])
    assert heat_preset["advancedMode"] == "heat-transfer"
    assert "INC_ENERGY_EQUATION" in heat_preset["enabledStarterModels"]
    assert "temperature" in heat_preset["resultExpectations"]["expectedPrimaryFields"]
    assert heat_preset["nativeSetupPlan"]["manualNativeModules"] == []
    assert heat_matrix["activeMode"] == "heat-transfer"
    assert next(entry for entry in heat_matrix["entries"] if entry["advancedMode"] == "heat-transfer")["active"] is True


def test_su2_unsupported_modes_block_even_when_solver_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(_request("su2", "multiphase-vof"))

    assert case.status == "blocked"
    assert case.runCommand == []
    assert "FLOWLAB_UNSUPPORTED_MODE= YES" in case.files["case.cfg"]
    assert "flowlab_su2_native_config_template.cfg" in case.files
    assert "FLOWLAB_TEMPLATE_ONLY= YES" in case.files["flowlab_su2_native_config_template.cfg"]
    assert "FLOWLAB_UNSUPPORTED_MODE= YES" in case.files["flowlab_su2_native_config_template.cfg"]
    assert "% Required native SU2 capabilities:" in case.files["flowlab_su2_native_config_template.cfg"]
    preset = json.loads(case.files["flowlab_su2_mode_preset.json"])
    checklist = json.loads(case.files["flowlab_su2_native_setup_checklist.json"])
    matrix = json.loads(case.files["flowlab_su2_capability_matrix.json"])
    assert preset["schema"] == "flowlab.su2_mode_preset.v1"
    assert preset["advancedMode"] == "multiphase-vof"
    assert preset["supportedByAdapter"] is False
    assert preset["requestedPhysicsResolved"] is False
    assert preset["supportLevel"] == "blocked-export-only"
    assert "multiphase free-surface flow" in preset["requiredCapabilities"]
    assert "VOF/free-surface model generation" in preset["blockedOrManualModels"]
    assert "Build a native SU2 multiphase/free-surface setup outside FlowLab's single-zone starter adapter." in preset["manualSetupRequirements"]
    assert preset["nativeSetupPlan"]["manualNativeModules"] == [
        "multiphase solver",
        "VOF/free-surface interface capture",
        "phase material tables",
    ]
    assert preset["nativeSetupPlan"]["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
    assert preset["nativeSetupPlan"]["handoffArtifacts"] == ["flowlab_su2_multiphase_handoff.json"]
    assert preset["nativeSetupPlan"]["transientControlsRequired"] is True
    assert "phase_fraction" in preset["resultExpectations"]["expectedPrimaryFields"]
    assert "flowlab_su2_multiphase_handoff.json" in case.files
    phase_handoff = json.loads(case.files["flowlab_su2_multiphase_handoff.json"])
    preflight = json.loads(case.files["flowlab_su2_advanced_preflight.json"])
    assert phase_handoff["schema"] == "flowlab.su2_phase_handoff.v1"
    assert phase_handoff["targetSolver"] == "su2"
    assert phase_handoff["advancedMode"] == "multiphase-vof"
    assert phase_handoff["productionReady"] is False
    assert phase_handoff["nativeSu2Ready"] is False
    assert phase_handoff["phaseModel"] == "multiphase-vof-free-surface"
    assert {phase["name"] for phase in phase_handoff["phases"]} == {"liquid", "gas"}
    assert phase_handoff["interfaceSetup"]["status"] == "manual"
    assert phase_handoff["interfaceSetup"]["requiredModel"] == "VOF/free-surface interface capture"
    assert "cavitationInputs" not in phase_handoff
    assert "phase_fraction" in phase_handoff["expectedPrimaryFields"]
    assert "interface_height" in phase_handoff["expectedPrimaryFields"]
    assert preflight["schema"] == "flowlab.su2_advanced_preflight.v1"
    assert preflight["advancedMode"] == "multiphase-vof"
    assert preflight["status"] == "blocked-export-only"
    assert preflight["productionReady"] is False
    assert preflight["nativeSu2Ready"] is False
    assert preflight["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
    assert preflight["handoffArtifacts"] == ["flowlab_su2_multiphase_handoff.json"]
    assert preflight["expectedPrimaryFields"] == preset["resultExpectations"]["expectedPrimaryFields"]
    assert preflight["unresolvedActions"]
    assert all(check["status"] == "pass" for check in preflight["artifactChecks"])
    assert checklist["schema"] == "flowlab.su2_native_setup_checklist.v1"
    assert checklist["advancedMode"] == "multiphase-vof"
    assert checklist["requestedPhysicsResolved"] is False
    assert {"kind": "native-module", "item": "multiphase solver"} in checklist["actionItems"]
    assert {"kind": "manual-setup", "item": "Build a native SU2 multiphase/free-surface setup outside FlowLab's single-zone starter adapter."} in checklist["actionItems"]
    assert "flowlab_su2_native_config_template.cfg" in checklist["generatedFiles"]
    assert "flowlab_su2_capability_matrix.json" in checklist["generatedFiles"]
    assert "flowlab_su2_multiphase_handoff.json" in checklist["generatedFiles"]
    assert "flowlab_su2_advanced_preflight.json" in checklist["generatedFiles"]
    assert "phase_fraction" in checklist["expectedPrimaryFields"]
    assert matrix["activeMode"] == "multiphase-vof"
    active_entry = next(entry for entry in matrix["entries"] if entry["advancedMode"] == "multiphase-vof")
    assert active_entry["active"] is True
    assert active_entry["supportedByAdapter"] is False
    assert "multiphase solver" in active_entry["manualNativeModules"]
    assert "flowlab_su2_multiphase_handoff.json" in active_entry["handoffArtifacts"]
    assert "multiphase-vof" in matrix["summary"]["blockedExportOnlyModes"]
    assert "multiphase-vof" in matrix["summary"]["handoffModes"]
    assert any(check["id"] == "native-config-review-template" and check["status"] == "pass" for check in preset["readinessChecks"])
    assert any(check["id"] == "phase-handoff-export" and check["status"] == "pass" for check in preset["readinessChecks"])
    assert any(check["id"] == "multiphase-solver" and check["status"] == "fail" for check in preset["readinessChecks"])
    assert any("FlowLab does not generate a native SU2 multiphase" in reason for reason in preset["blockingReasons"])
    assert any("blocked until FlowLab" in entry for entry in case.provenance)

    for advanced_mode, expected_field in [
        ("cavitation", "vapour_fraction"),
        ("water-hammer", "pressure_wave"),
        ("conjugate-heat-transfer", "solid_temperature"),
        ("rigid-body-fluid-forces", "body_force"),
    ]:
        blocked_case = adapters.generate_case(_request("su2", advanced_mode))
        blocked_preset = json.loads(blocked_case.files["flowlab_su2_mode_preset.json"])
        blocked_checklist = json.loads(blocked_case.files["flowlab_su2_native_setup_checklist.json"])
        blocked_matrix = json.loads(blocked_case.files["flowlab_su2_capability_matrix.json"])
        blocked_preflight = json.loads(blocked_case.files["flowlab_su2_advanced_preflight.json"])
        assert blocked_case.status == "blocked"
        assert "flowlab_su2_native_config_template.cfg" in blocked_case.files
        assert "FLOWLAB_TEMPLATE_ONLY= YES" in blocked_case.files["flowlab_su2_native_config_template.cfg"]
        assert "FLOWLAB_UNSUPPORTED_MODE= YES" in blocked_case.files["flowlab_su2_native_config_template.cfg"]
        assert blocked_preset["supportedByAdapter"] is False
        assert blocked_preset["requestedPhysicsResolved"] is False
        assert blocked_preset["nativeSetupPlan"]["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
        assert blocked_preset["nativeSetupPlan"]["manualNativeModules"]
        assert expected_field in blocked_preset["resultExpectations"]["expectedPrimaryFields"]
        assert blocked_checklist["requestedPhysicsResolved"] is False
        assert "flowlab_su2_native_config_template.cfg" in blocked_checklist["generatedFiles"]
        assert "flowlab_su2_capability_matrix.json" in blocked_checklist["generatedFiles"]
        assert "flowlab_su2_advanced_preflight.json" in blocked_checklist["generatedFiles"]
        assert blocked_checklist["actionItems"]
        assert expected_field in blocked_checklist["expectedPrimaryFields"]
        assert blocked_preflight["schema"] == "flowlab.su2_advanced_preflight.v1"
        assert blocked_preflight["advancedMode"] == advanced_mode
        assert blocked_preflight["status"] == "blocked-export-only"
        assert blocked_preflight["productionReady"] is False
        assert blocked_preflight["nativeSu2Ready"] is False
        assert blocked_preflight["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
        assert blocked_preflight["handoffArtifacts"] == blocked_preset["nativeSetupPlan"]["handoffArtifacts"]
        assert blocked_preflight["expectedPrimaryFields"] == blocked_preset["resultExpectations"]["expectedPrimaryFields"]
        assert expected_field in blocked_preflight["expectedPrimaryFields"]
        assert blocked_preflight["unresolvedActions"]
        assert all(check["status"] == "pass" for check in blocked_preflight["artifactChecks"])
        assert blocked_matrix["activeMode"] == advanced_mode
        assert next(entry for entry in blocked_matrix["entries"] if entry["advancedMode"] == advanced_mode)["active"] is True
        assert advanced_mode in blocked_matrix["summary"]["blockedExportOnlyModes"]
        assert advanced_mode in blocked_matrix["summary"]["handoffModes"]
        if advanced_mode == "cavitation":
            assert "flowlab_su2_cavitation_handoff.json" in blocked_case.files
            handoff = json.loads(blocked_case.files["flowlab_su2_cavitation_handoff.json"])
            assert handoff["schema"] == "flowlab.su2_phase_handoff.v1"
            assert handoff["targetSolver"] == "su2"
            assert handoff["advancedMode"] == "cavitation"
            assert handoff["productionReady"] is False
            assert handoff["nativeSu2Ready"] is False
            assert handoff["phaseModel"] == "cavitation-phase-change"
            assert {phase["name"] for phase in handoff["phases"]} == {"liquid", "vapour"}
            assert handoff["interfaceSetup"]["status"] == "manual"
            assert handoff["interfaceSetup"]["requiredModel"] == "phase-change cavitation law"
            assert handoff["cavitationInputs"]["saturationPressure"] == 2340.0
            assert "vapour_fraction" in handoff["expectedPrimaryFields"]
            assert "cavitation_source" in handoff["expectedPrimaryFields"]
            assert "flowlab_su2_cavitation_handoff.json" in blocked_checklist["generatedFiles"]
            assert blocked_preset["nativeSetupPlan"]["handoffArtifacts"] == ["flowlab_su2_cavitation_handoff.json"]
            assert any(
                check["id"] == "phase-handoff-export" and check["status"] == "pass"
                for check in blocked_preset["readinessChecks"]
            )
        if advanced_mode == "water-hammer":
            assert "flowlab_su2_water_hammer_handoff.json" in blocked_case.files
            assert "flowlab_su2_water_hammer_waveform.csv" in blocked_case.files
            handoff = json.loads(blocked_case.files["flowlab_su2_water_hammer_handoff.json"])
            assert handoff["schema"] == "flowlab.water_hammer_handoff.v1"
            assert handoff["targetSolver"] == "su2"
            assert handoff["nativeSu2Ready"] is False
            assert handoff["su2"]["csv"] == "flowlab_su2_water_hammer_waveform.csv"
            assert blocked_case.files["flowlab_su2_water_hammer_waveform.csv"].startswith("time,kinematicPressure,absolutePressure")
            assert "flowlab_su2_water_hammer_handoff.json" in blocked_checklist["generatedFiles"]
            assert "flowlab_su2_water_hammer_waveform.csv" in blocked_checklist["generatedFiles"]
            assert any(
                check["id"] == "moc-boundary-handoff-export" and check["status"] == "pass"
                for check in blocked_preset["readinessChecks"]
            )
        if advanced_mode == "conjugate-heat-transfer":
            assert "flowlab_su2_cht_handoff.json" in blocked_case.files
            handoff = json.loads(blocked_case.files["flowlab_su2_cht_handoff.json"])
            assert handoff["schema"] == "flowlab.su2_cht_handoff.v1"
            assert handoff["targetSolver"] == "su2"
            assert handoff["nativeSu2Ready"] is False
            assert handoff["solidZone"]["meshStatus"] == "not generated"
            assert handoff["interface"]["status"] == "manual"
            assert "solid_temperature" in handoff["expectedPrimaryFields"]
            assert "heat_flux" in handoff["expectedPrimaryFields"]
            assert "flowlab_su2_cht_handoff.json" in blocked_checklist["generatedFiles"]
            assert blocked_preset["nativeSetupPlan"]["handoffArtifacts"] == ["flowlab_su2_cht_handoff.json"]
            assert any(
                check["id"] == "cht-handoff-export" and check["status"] == "pass"
                for check in blocked_preset["readinessChecks"]
            )
        if advanced_mode == "rigid-body-fluid-forces":
            assert "flowlab_su2_rigid_body_handoff.json" in blocked_case.files
            handoff = json.loads(blocked_case.files["flowlab_su2_rigid_body_handoff.json"])
            assert handoff["schema"] == "flowlab.su2_rigid_body_handoff.v1"
            assert handoff["targetSolver"] == "su2"
            assert handoff["advancedMode"] == "rigid-body-fluid-forces"
            assert handoff["productionReady"] is False
            assert handoff["nativeSu2Ready"] is False
            assert handoff["couplingIntent"]["status"] == "manual"
            assert handoff["couplingIntent"]["preferredCurrentSandbox"] == "mujoco"
            assert handoff["motionSetup"]["status"] == "manual"
            assert "body_force" in handoff["expectedPrimaryFields"]
            assert "moment" in handoff["expectedPrimaryFields"]
            assert "flowlab_su2_rigid_body_handoff.json" in blocked_checklist["generatedFiles"]
            assert blocked_preset["nativeSetupPlan"]["handoffArtifacts"] == ["flowlab_su2_rigid_body_handoff.json"]
            assert any(
                check["id"] == "rigid-body-handoff-export" and check["status"] == "pass"
                for check in blocked_preset["readinessChecks"]
            )


def test_code_saturne_case_is_generated_when_solver_and_gmsh_mesh_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    project = {
        "name": "Code Saturne mesh case",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 100, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 10,
                "shape": {"kind": "circular", "diameter": 0.1},
            }
        },
    }
    case = adapters.generate_case(
        CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode="incompressible-navier-stokes")
    )

    assert case.status == "generated"
    assert case.runCommand == ["code_saturne", "run"]
    assert "MESH/flowlab_mesh.msh" in case.files
    assert "DATA/setup.xml" in case.files
    assert "DATA/flowlab_physics_preset.json" in case.files
    assert "DATA/flowlab_native_setup_checklist.json" in case.files
    assert "DATA/run.cfg" in case.files
    assert "DATA/cs_user_scripts.py" in case.files
    assert "DATA/cs_user_physics.py" in case.files
    assert "SRC/cs_user_boundary_conditions.f90" in case.files
    preset = json.loads(case.files["DATA/flowlab_physics_preset.json"])
    checklist = json.loads(case.files["DATA/flowlab_native_setup_checklist.json"])
    assert preset["schema"] == "flowlab.code_saturne_physics_preset.v1"
    assert preset["advancedMode"] == "incompressible-navier-stokes"
    assert preset["supportLevel"] == "starter-supported"
    assert preset["supportedByAdapter"] is True
    assert preset["requestedPhysicsResolved"] is True
    assert preset["productionReady"] is False
    assert "incompressible Navier-Stokes" in preset["requestedPhysics"]
    assert preset["setupXmlModels"]["turbulence"] == "off"
    assert preset["turbulencePlan"]["model"] == "off"
    assert preset["turbulencePlan"]["starterStatus"] == "laminar-starter"
    assert preset["turbulencePlan"]["productionReady"] is False
    assert {"LES", "DNS"}.issubset(set(preset["turbulencePlan"]["unresolvedModels"]))
    assert preset["setupXmlModels"]["thermalScalar"] == "off"
    assert preset["resultExpectations"]["flowlabConversion"].startswith("starter hexa8")
    assert any(check["id"] == "production-mesh-review" and check["status"] == "fail" for check in preset["readinessChecks"])
    assert "FlowLab mesh is a deterministic starter mesh" in preset["blockingReasons"][0]
    assert "FLOWLAB_CODE_SATURNE_PHYSICS_PRESET" in case.files["DATA/cs_user_physics.py"]
    assert "FLOWLAB_CODE_SATURNE_NATIVE_SETUP_CHECKLIST" in case.files["DATA/cs_user_physics.py"]
    assert "def flowlab_readiness_summary()" in case.files["DATA/cs_user_physics.py"]
    assert "def flowlab_native_setup_checklist()" in case.files["DATA/cs_user_physics.py"]
    assert checklist["schema"] == "flowlab.code_saturne_native_setup_checklist.v1"
    assert checklist["advancedMode"] == "incompressible-navier-stokes"
    assert checklist["supportLevel"] == "starter-supported"
    assert checklist["requestedPhysicsResolved"] is True
    assert checklist["turbulencePlan"]["model"] == "off"
    assert checklist["turbulencePlan"]["starterStatus"] == "laminar-starter"
    assert checklist["actionItems"] == []
    assert checklist["expectedPrimaryFields"] == ["pressure", "velocity"]
    assert "DATA/setup.xml" in checklist["generatedFiles"]
    setup_xml = case.files["DATA/setup.xml"]
    assert "<!--" not in setup_xml
    assert '<zone label="all_cells" id="1" initialization="on"' in setup_xml
    assert "all[]" in setup_xml
    assert '<boundary label="flowlab_inlet" name="1" nature="inlet">2</boundary>' in setup_xml
    assert '<inlet label="flowlab_inlet">' in setup_xml
    assert '<velocity_pressure choice="norm" direction="coordinates">' in setup_xml
    assert "<direction_x>1</direction_x>" in setup_xml
    assert "<direction_y>0</direction_y>" in setup_xml
    assert "<direction_z>0</direction_z>" in setup_xml
    assert '<boundary label="flowlab_outlet" name="2" nature="outlet">3</boundary>' in setup_xml
    assert '<outlet label="flowlab_outlet">' in setup_xml
    assert '<boundary label="flowlab_wall_front_back" name="5" nature="wall">6</boundary>' in setup_xml
    assert '<wall label="flowlab_wall_front_back">' in setup_xml
    assert "def define_domain_parameters(domain)" in case.files["DATA/cs_user_scripts.py"]
    assert 'domain.mesh_dir = "MESH"' in case.files["DATA/cs_user_scripts.py"]
    assert 'domain.meshes = ["flowlab_mesh.msh"]' in case.files["DATA/cs_user_scripts.py"]
    assert "cs_f_user_boundary_conditions" in case.files["SRC/cs_user_boundary_conditions.f90"]
    assert any("physics preset" in entry for entry in case.provenance)
    assert any("native Gmsh mesh input" in entry for entry in case.provenance)


def test_code_saturne_advanced_modes_emit_physics_preset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = _parameterized_project()

    heat = adapters.generate_case(CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode="heat-transfer"))
    compressible = adapters.generate_case(CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode="compressible-flow"))
    cavitation = adapters.generate_case(CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode="cavitation"))

    heat_preset = json.loads(heat.files["DATA/flowlab_physics_preset.json"])
    compressible_preset = json.loads(compressible.files["DATA/flowlab_physics_preset.json"])
    cavitation_preset = json.loads(cavitation.files["DATA/flowlab_physics_preset.json"])
    heat_checklist = json.loads(heat.files["DATA/flowlab_native_setup_checklist.json"])
    compressible_checklist = json.loads(compressible.files["DATA/flowlab_native_setup_checklist.json"])
    cavitation_checklist = json.loads(cavitation.files["DATA/flowlab_native_setup_checklist.json"])

    assert '<thermal_scalar model="temperature_celsius"/>' in heat.files["DATA/setup.xml"]
    assert heat_preset["supportLevel"] == "starter-supported"
    assert heat_preset["supportedByAdapter"] is True
    assert heat_preset["requestedPhysicsResolved"] is True
    assert heat_preset["setupXmlModels"]["thermalScalar"] == "temperature_celsius"
    assert heat_preset["setupXmlModels"]["energyModel"] == "temperature_celsius scalar"
    assert heat_preset["turbulencePlan"]["model"] == "k-epsilon"
    assert heat_preset["turbulencePlan"]["starterStatus"] == "rans-starter"
    assert "near-wall y-plus evidence" in heat_preset["turbulencePlan"]["requiredEvidence"]
    assert heat_preset["thermalStarter"]["scalarName"] == "temperature_celsius"
    assert heat_preset["thermalStarter"]["inletTemperatureK"] == 300.0
    assert heat_preset["thermalStarter"]["inletTemperatureC"] == pytest.approx(26.85)
    assert heat_preset["thermalBoundaryPlan"]["walls"] == "adiabatic placeholder until wall heat flux or temperature is specified"
    assert "fluid-solid conjugate heat transfer" in heat_preset["thermalBoundaryPlan"]["excludedPhysics"]
    assert "radiation" in heat_preset["thermalBoundaryPlan"]["excludedPhysics"]
    assert "phase change" in heat_preset["thermalBoundaryPlan"]["excludedPhysics"]
    assert "temperature" in heat_preset["resultExpectations"]["expectedPrimaryFields"]
    assert "passive thermal scalar" in heat_preset["requestedPhysics"]
    assert "temperature_celsius thermal scalar" in heat_preset["enabledStarterModels"]
    assert any(check["id"] == "thermal-scalar-enabled" and check["status"] == "pass" for check in heat_preset["readinessChecks"])
    assert any(check["id"] == "thermal-boundary-plan" and check["status"] == "warning" for check in heat_preset["readinessChecks"])
    assert heat_checklist["thermalStarter"]["scalarName"] == "temperature_celsius"
    assert heat_checklist["turbulencePlan"]["model"] == heat_preset["turbulencePlan"]["model"]
    assert heat_checklist["thermalBoundaryPlan"]["excludedPhysics"] == heat_preset["thermalBoundaryPlan"]["excludedPhysics"]
    assert heat_checklist["actionItems"] == []
    assert heat.status == "generated"
    assert heat.runCommand == ["code_saturne", "run"]

    assert compressible.status == "blocked"
    assert compressible.runCommand == []
    assert compressible_preset["supportLevel"] == "metadata-plus-handoff"
    assert compressible_preset["supportedByAdapter"] is False
    assert compressible_preset["requestedPhysicsResolved"] is False
    assert "DATA/flowlab_compressible_handoff.json" in compressible.files
    assert "DATA/flowlab_native_physics_review.py" in compressible.files
    assert "FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True" in compressible.files["DATA/flowlab_native_physics_review.py"]
    assert "FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False" in compressible.files["DATA/flowlab_native_physics_review.py"]
    assert "FLOWLAB_PRODUCTION_READY = False" in compressible.files["DATA/flowlab_native_physics_review.py"]
    assert compressible_preset["nativeSetupPlan"]["reviewTemplate"] == "DATA/flowlab_native_physics_review.py"
    assert compressible_preset["nativeSetupPlan"]["handoffArtifacts"] == ["DATA/flowlab_compressible_handoff.json"]
    assert compressible_preset["setupXmlModels"]["turbulence"] == "k-epsilon"
    assert compressible_preset["turbulencePlan"]["starterStatus"] == "rans-starter"
    assert compressible_preset["setupXmlModels"]["compressibility"] == "manual-native-module"
    assert compressible_preset["setupXmlModels"]["handoffArtifacts"] == ["DATA/flowlab_compressible_handoff.json"]
    assert "compressible flow module" in compressible_preset["nativeSetupPlan"]["manualNativeModules"]
    assert "compressible boundary-condition review" in compressible_preset["nativeSetupPlan"]["manualNativeModules"]
    assert "density" in compressible_preset["resultExpectations"]["expectedPrimaryFields"]
    assert "temperature" in compressible_preset["resultExpectations"]["expectedPrimaryFields"]
    assert "compressible Navier-Stokes" in compressible_preset["requestedPhysics"]
    assert "native Code_Saturne compressible module setup" in compressible_preset["blockedOrManualModels"]
    assert "Enable and review Code_Saturne compressible-flow module settings outside the starter setup.xml." in compressible_preset["manualSetupRequirements"]
    assert any(check["id"] == "compressible-handoff-export" and check["status"] == "pass" for check in compressible_preset["readinessChecks"])
    assert any(check["id"] == "compressible-module" and check["status"] == "fail" for check in compressible_preset["readinessChecks"])
    assert any(check["id"] == "native-physics-review-template" and check["status"] == "pass" for check in compressible_preset["readinessChecks"])
    assert {"kind": "native-module", "item": "compressible flow module"} in compressible_checklist["actionItems"]
    assert "DATA/flowlab_native_physics_review.py" in compressible_checklist["generatedFiles"]
    assert "DATA/flowlab_compressible_handoff.json" in compressible_checklist["generatedFiles"]
    assert "density" in compressible_checklist["expectedPrimaryFields"]
    assert compressible_checklist["turbulencePlan"]["model"] == "k-epsilon"
    compressible_handoff = json.loads(compressible.files["DATA/flowlab_compressible_handoff.json"])
    assert compressible_handoff["schema"] == "flowlab.code_saturne_compressible_handoff.v1"
    assert compressible_handoff["targetSolver"] == "code-saturne"
    assert compressible_handoff["advancedMode"] == "compressible-flow"
    assert compressible_handoff["nativeCodeSaturneReady"] is False
    assert compressible_handoff["starterSurrogate"]["status"] == "pressure-based-incompressible-surrogate"
    assert "compressible flow module" in compressible_handoff["requiredNativeModules"]
    assert compressible_handoff["thermodynamicSetup"]["status"] == "manual"
    assert "density" in compressible_handoff["expectedPrimaryFields"]
    assert "temperature" in compressible_handoff["expectedPrimaryFields"]
    assert "mach_number" in compressible_handoff["expectedPrimaryFields"]
    compressible_matrix = json.loads(compressible.files["DATA/flowlab_code_saturne_capability_matrix.json"])
    assert compressible_matrix["schema"] == "flowlab.code_saturne_capability_matrix.v1"
    assert compressible_matrix["activeMode"] == "compressible-flow"
    assert compressible_matrix["productionReady"] is False
    assert "incompressible-navier-stokes" in compressible_matrix["summary"]["starterSupportedModes"]
    assert "heat-transfer" in compressible_matrix["summary"]["starterSupportedModes"]
    assert "compressible-flow" in compressible_matrix["summary"]["unresolvedModes"]
    assert "cavitation" in compressible_matrix["summary"]["unresolvedModes"]
    compressible_matrix_entry = next(entry for entry in compressible_matrix["entries"] if entry["advancedMode"] == "compressible-flow")
    assert compressible_matrix_entry["active"] is True
    assert compressible_matrix_entry["turbulenceModel"] == "k-epsilon"
    assert compressible_matrix_entry["turbulenceStarterStatus"] == "rans-starter"
    assert "compressible flow module" in compressible_matrix_entry["manualNativeModules"]
    assert "DATA/flowlab_compressible_handoff.json" in compressible_matrix_entry["handoffArtifacts"]
    assert "density" in compressible_matrix_entry["expectedPrimaryFields"]
    assert compressible_matrix_entry["readinessSummary"]["fail"] >= 1
    assert "compressible-flow" in compressible_matrix["summary"]["handoffModes"]

    assert cavitation.status == "blocked"
    assert cavitation.runCommand == []
    assert cavitation_preset["supportLevel"] == "metadata-plus-handoff"
    assert cavitation_preset["supportedByAdapter"] is False
    assert cavitation_preset["requestedPhysicsResolved"] is False
    assert "DATA/flowlab_native_physics_review.py" in cavitation.files
    assert cavitation_preset["nativeSetupPlan"]["reviewTemplate"] == "DATA/flowlab_native_physics_review.py"
    assert cavitation_preset["setupXmlModels"]["cavitationModel"] == "manual-phase-change"
    assert cavitation_preset["setupXmlModels"]["handoffArtifacts"] == ["DATA/flowlab_cavitation_handoff.json"]
    assert cavitation_preset["nativeSetupPlan"]["transientControlsRequired"] is True
    assert cavitation_preset["nativeSetupPlan"]["handoffArtifacts"] == ["DATA/flowlab_cavitation_handoff.json"]
    assert "vapour_fraction" in cavitation_preset["resultExpectations"]["expectedPrimaryFields"]
    assert "liquid-vapour phase change" in cavitation_preset["requestedPhysics"]
    assert "phase-change cavitation law" in cavitation_preset["blockedOrManualModels"]
    assert "Add liquid/vapour material definitions, saturation pressure, and a validated phase-change law in Code_Saturne." in cavitation_preset["manualSetupRequirements"]
    assert any(check["id"] == "phase-handoff-export" and check["status"] == "pass" for check in cavitation_preset["readinessChecks"])
    assert any(check["id"] == "phase-change-law" and check["status"] == "fail" for check in cavitation_preset["readinessChecks"])
    assert any(check["id"] == "native-physics-review-template" and check["status"] == "pass" for check in cavitation_preset["readinessChecks"])
    assert any("Phase-change cavitation law" in reason for reason in cavitation_preset["blockingReasons"])
    assert {"kind": "native-module", "item": "liquid-vapour phase change"} in cavitation_checklist["actionItems"]
    assert "DATA/flowlab_native_physics_review.py" in cavitation_checklist["generatedFiles"]
    assert "vapour_fraction" in cavitation_checklist["expectedPrimaryFields"]
    assert "DATA/flowlab_cavitation_handoff.json" in cavitation_checklist["generatedFiles"]
    cavitation_handoff = json.loads(cavitation.files["DATA/flowlab_cavitation_handoff.json"])
    assert cavitation_handoff["schema"] == "flowlab.code_saturne_phase_handoff.v1"
    assert cavitation_handoff["targetSolver"] == "code-saturne"
    assert cavitation_handoff["advancedMode"] == "cavitation"
    assert cavitation_handoff["nativeCodeSaturneReady"] is False
    assert cavitation_handoff["interfaceSetup"]["status"] == "manual"
    assert cavitation_handoff["cavitationInputs"]["saturationPressure"] == 2500.0
    assert "vapour_fraction" in cavitation_handoff["expectedPrimaryFields"]

    for mode, required_module, expected_field in [
        ("multiphase-vof", "VOF/free-surface model", "phase_fraction"),
        ("conjugate-heat-transfer", "multi-domain CHT", "heat_flux"),
        ("water-hammer", "transient pressure-wave boundary", "pressure_wave"),
        ("rigid-body-fluid-forces", "moving mesh", "body_force"),
    ]:
        case = adapters.generate_case(CaseRequest.model_construct(project=project, solver="code-saturne", advancedMode=mode))
        preset = json.loads(case.files["DATA/flowlab_physics_preset.json"])
        checklist = json.loads(case.files["DATA/flowlab_native_setup_checklist.json"])
        matrix = json.loads(case.files["DATA/flowlab_code_saturne_capability_matrix.json"])
        assert case.status == "blocked"
        assert case.runCommand == []
        expected_support = (
            "metadata-plus-handoff"
            if mode in {"water-hammer", "conjugate-heat-transfer", "multiphase-vof", "rigid-body-fluid-forces"}
            else "metadata-only"
        )
        assert preset["supportLevel"] == expected_support
        expected_turbulence = "off" if mode == "water-hammer" else "k-epsilon"
        expected_turbulence_status = "laminar-starter" if expected_turbulence == "off" else "rans-starter"
        assert preset["turbulencePlan"]["model"] == expected_turbulence
        assert preset["turbulencePlan"]["starterStatus"] == expected_turbulence_status
        assert checklist["turbulencePlan"]["model"] == expected_turbulence
        assert preset["supportedByAdapter"] is False
        assert preset["requestedPhysicsResolved"] is False
        assert matrix["activeMode"] == mode
        assert any(entry["advancedMode"] == mode and entry["active"] is True for entry in matrix["entries"])
        assert mode in matrix["summary"]["unresolvedModes"]
        if expected_support == "metadata-plus-handoff":
            assert mode in matrix["summary"]["handoffModes"]
        assert "DATA/flowlab_native_physics_review.py" in case.files
        assert "FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True" in case.files["DATA/flowlab_native_physics_review.py"]
        assert "FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False" in case.files["DATA/flowlab_native_physics_review.py"]
        assert preset["nativeSetupPlan"]["reviewTemplate"] == "DATA/flowlab_native_physics_review.py"
        assert required_module in preset["nativeSetupPlan"]["manualNativeModules"]
        assert expected_field in preset["resultExpectations"]["expectedPrimaryFields"]
        assert {"kind": "native-module", "item": required_module} in checklist["actionItems"]
        assert "DATA/flowlab_native_physics_review.py" in checklist["generatedFiles"]
        assert expected_field in checklist["expectedPrimaryFields"]
        assert preset["manualSetupRequirements"]
        assert any(check["status"] == "fail" for check in preset["readinessChecks"])
        if mode == "multiphase-vof":
            assert "DATA/flowlab_multiphase_handoff.json" in case.files
            handoff = json.loads(case.files["DATA/flowlab_multiphase_handoff.json"])
            assert handoff["schema"] == "flowlab.code_saturne_phase_handoff.v1"
            assert handoff["targetSolver"] == "code-saturne"
            assert handoff["advancedMode"] == "multiphase-vof"
            assert handoff["nativeCodeSaturneReady"] is False
            assert handoff["interfaceSetup"]["status"] == "manual"
            assert "phase_fraction" in handoff["expectedPrimaryFields"]
            assert preset["nativeSetupPlan"]["handoffArtifacts"] == ["DATA/flowlab_multiphase_handoff.json"]
            assert "DATA/flowlab_multiphase_handoff.json" in checklist["generatedFiles"]
            assert any(check["id"] == "phase-handoff-export" and check["status"] == "pass" for check in preset["readinessChecks"])
        if mode == "water-hammer":
            assert "DATA/flowlab_water_hammer_handoff.json" in case.files
            assert "DATA/flowlab_water_hammer_waveform.csv" in case.files
            handoff = json.loads(case.files["DATA/flowlab_water_hammer_handoff.json"])
            assert handoff["schema"] == "flowlab.water_hammer_handoff.v1"
            assert handoff["targetSolver"] == "code-saturne"
            assert handoff["nativeCodeSaturneReady"] is False
            assert handoff["codeSaturne"]["csv"] == "DATA/flowlab_water_hammer_waveform.csv"
            assert case.files["DATA/flowlab_water_hammer_waveform.csv"].startswith("time,kinematicPressure,absolutePressure")
            assert "DATA/flowlab_water_hammer_handoff.json" in checklist["generatedFiles"]
            assert any(check["id"] == "moc-handoff-export" and check["status"] == "pass" for check in preset["readinessChecks"])
        if mode == "conjugate-heat-transfer":
            assert "DATA/flowlab_cht_handoff.json" in case.files
            handoff = json.loads(case.files["DATA/flowlab_cht_handoff.json"])
            assert handoff["schema"] == "flowlab.code_saturne_cht_handoff.v1"
            assert handoff["targetSolver"] == "code-saturne"
            assert handoff["nativeCodeSaturneReady"] is False
            assert handoff["solidDomain"]["meshStatus"] == "not generated"
            assert handoff["interfaceCoupling"]["status"] == "manual"
            assert "solid_temperature" in handoff["expectedPrimaryFields"]
            assert "heat_flux" in handoff["expectedPrimaryFields"]
            assert preset["nativeSetupPlan"]["handoffArtifacts"] == ["DATA/flowlab_cht_handoff.json"]
            assert "DATA/flowlab_cht_handoff.json" in checklist["generatedFiles"]
            assert any(check["id"] == "cht-handoff-export" and check["status"] == "pass" for check in preset["readinessChecks"])
        if mode == "rigid-body-fluid-forces":
            assert "DATA/flowlab_rigid_body_handoff.json" in case.files
            handoff = json.loads(case.files["DATA/flowlab_rigid_body_handoff.json"])
            assert handoff["schema"] == "flowlab.code_saturne_rigid_body_handoff.v1"
            assert handoff["targetSolver"] == "code-saturne"
            assert handoff["advancedMode"] == "rigid-body-fluid-forces"
            assert handoff["productionReady"] is False
            assert handoff["nativeCodeSaturneReady"] is False
            assert handoff["couplingIntent"]["status"] == "manual"
            assert handoff["couplingIntent"]["preferredCurrentSandbox"] == "mujoco"
            assert handoff["motionSetup"]["status"] == "manual"
            assert "body_force" in handoff["expectedPrimaryFields"]
            assert "moment" in handoff["expectedPrimaryFields"]
            assert preset["nativeSetupPlan"]["handoffArtifacts"] == ["DATA/flowlab_rigid_body_handoff.json"]
            assert "DATA/flowlab_rigid_body_handoff.json" in checklist["generatedFiles"]
            assert any(check["id"] == "rigid-body-handoff-export" and check["status"] == "pass" for check in preset["readinessChecks"])


def _steady_project(run_mode: str | None) -> dict:
    project = _parameterized_project()
    solver = dict(project.get("solver") or {})
    if run_mode is not None:
        solver["runMode"] = run_mode
    project["solver"] = solver
    return project


def test_openfoam_steady_run_mode_emits_converging_simple_controls(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    request = CaseRequest.model_construct(
        project=_steady_project("steady"),
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
    )
    case = adapters.generate_case(request)

    control = case.files["system/controlDict"]
    assert "endTime         2000;" in control
    assert "deltaT          1;" in control
    assert "default         steadyState;" in case.files["system/fvSchemes"]
    solution = case.files["system/fvSolution"]
    assert "residualControl" in solution
    assert "relaxationFactors" in solution


def test_openfoam_default_run_mode_stays_transient(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    request = CaseRequest.model_construct(
        project=_steady_project(None),
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
    )
    case = adapters.generate_case(request)

    assert "endTime         0.05;" in case.files["system/controlDict"]
    assert "default         Euler;" in case.files["system/fvSchemes"]
    assert "residualControl" not in case.files["system/fvSolution"]


def test_openfoam_steady_run_mode_ignored_for_transient_physics(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    # runMode=steady must NOT override inherently transient compressible flow.
    request = CaseRequest.model_construct(
        project=_steady_project("steady"),
        solver="openfoam",
        advancedMode="compressible-flow",
    )
    case = adapters.generate_case(request)

    assert "endTime         0.001;" in case.files["system/controlDict"]
    assert "default         Euler;" in case.files["system/fvSchemes"]
    assert "residualControl" not in case.files["system/fvSolution"]


def _circular_pipe_project(mesh_mode: str | None) -> dict:
    solver: dict = {"meshResolution": "coarse"}
    if mesh_mode is not None:
        solver["meshMode"] = mesh_mode
    return {
        "name": "Axisymmetric pipe",
        "fluid": {
            "density": 1000.0,
            "dynamicViscosity": 0.018,
            "temperature": 300.0,
            "vaporPressure": 2300.0,
            "bulkModulus": 2.2e9,
        },
        "solver": solver,
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}, "pressure": 101325.0},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 400, "y": 0}, "pressure": 101325.0},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "length": 8.0,
                "shape": {"kind": "circular", "diameter": 0.1},
            }
        },
    }


def test_openfoam_axisymmetric_mesh_mode_emits_valid_wedge_pipe(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_circular_pipe_project("axisymmetric"),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    block_mesh = case.files["system/blockMeshDict"]
    assert "type wedge" in block_mesh
    for patch in ("inlet", "outlet", "walls", "front", "back"):
        assert patch in block_mesh
    # The fitted planar polyMesh is skipped so Allrun's blockMesh builds the wedge.
    assert "constant/polyMesh/points" not in case.files
    assert "wedge" in case.files["0/U"] and "axis" in case.files["0/U"]
    assert "wedge" in case.files["0/p"] and "frontAndBack" not in case.files["0/p"]
    # The wedge-aware validator must accept the case with no issues.
    assert validate_solver_case(case) == []


def test_openfoam_default_mesh_mode_stays_planar_2d(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_circular_pipe_project(None),
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )

    assert "type wedge" not in case.files["system/blockMeshDict"]
    assert "frontAndBack" in case.files["0/U"]
    assert "constant/polyMesh/points" in case.files


def test_openfoam_axisymmetric_ignored_for_transient_physics(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)

    # meshMode=axisymmetric must NOT apply to inherently transient modes.
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_circular_pipe_project("axisymmetric"),
            solver="openfoam",
            advancedMode="water-hammer",
        )
    )
    assert "type wedge" not in case.files["system/blockMeshDict"]
