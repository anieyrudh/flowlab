from __future__ import annotations

import json
import importlib.util
import math
import struct
import threading
import time
from pathlib import Path

import pytest

from server.flowlab import adapters, execution
from server.flowlab.adapters import add_case_manifest
from server.flowlab.execution import (
    JobManager,
    collect_patch_metrics,
    collect_result_files,
    collect_diagnostic_files,
    finalize_run_artifacts,
    list_case_artifacts,
    openfoam_diagnostics_smoke_case,
    parse_diagnostic_files,
    parse_solver_logs,
    read_openfoam_diagnostics_acceptance,
    solver_output_quality_error,
    validate_case_manifest,
    validate_solver_case,
    write_meshio_roundtrip_validation,
    write_openfoam_diagnostics_acceptance,
    write_result_collection_pvd,
)
from server.flowlab.results import parse_vtk_result
from server.flowlab.schemas import CaseRequest, JobRecord, SolverCase


def _openfoam_metric_control_dict(
    *,
    inlet_patches: str = "inlet outlet",
    wall_patches: str = "walls",
    pressure_probes: bool = False,
    application: str = "foamRun",
) -> str:
    pressure_probe_block = (
        """
    pressureProbes
    {
        type            probes;
        fields          (p p_rgh);
    }
"""
        if pressure_probes
        else ""
    )
    return f"""application {application};
functions
{{
    residuals
    {{
    }}

    centerlineProbes
    {{
    }}

    wallForces
    {{
        patches         ({wall_patches});
    }}

    patchFlowRate
    {{
        patches         ({inlet_patches});
    }}

    patchAverage
    {{
        name            ({inlet_patches});
    }}

    wallShearStress
    {{
        patches         ({wall_patches});
    }}{pressure_probe_block}
}}
"""


def _openfoam_patch_metrics_manifest(
    *,
    inlet: list[str] | None = None,
    outlet: list[str] | None = None,
    wall: list[str] | None = None,
    pressure_probes: bool = False,
) -> str:
    inlet = inlet or ["inlet"]
    outlet = outlet or ["outlet"]
    wall = wall or ["walls"]
    function_objects = ["patchFlowRate", "patchAverage", "wallShearStress", "wallForces"]
    if pressure_probes:
        function_objects.append("pressureProbes")
    return json.dumps(
        {
            "schema": "flowlab.openfoam_patch_metric_function_objects.v1",
            "patches": {
                "inlet": inlet,
                "outlet": outlet,
                "wall": wall,
                "flow": [*inlet, *outlet],
            },
            "pressureProbeLocations": [(0.0, 0.0, 0.005)] if pressure_probes else [],
            "functionObjects": function_objects,
        }
    ) + "\n"


def _case(solver: str = "openfoam") -> SolverCase:
    run_command = ["bash", "Allrun"]
    files = {
        "README.md": "# Execution smoke\n",
        "Allrun": "#!/usr/bin/env bash\nblockMesh\ncheckMesh -allGeometry -allTopology\nfoamRun -solver incompressibleFluid\n",
        "system/blockMeshDict": "boundary\n(\n inlet {}\n outlet {}\n walls {}\n frontAndBack {}\n);\n",
        "system/controlDict": _openfoam_metric_control_dict(),
        "system/functions": _openfoam_metric_control_dict()
        .split("functions\n{", 1)[1]
        .rsplit("\n}", 1)[0],
        "system/fvSchemes": "ddtSchemes {}\n",
        "system/fvSolution": "solvers {}\n",
        "0/U": "boundaryField {}\n",
        "0/p": "boundaryField {}\n",
        "0/T": "boundaryField {}\n",
        "constant/transportProperties": "transportModel Newtonian;\n",
        "constant/turbulenceProperties": "simulationType laminar;\n",
        "constant/flowlab.json": "{}\n",
        "constant/flowlab_patch_metrics.json": _openfoam_patch_metrics_manifest(),
        "constant/flowlab_openfoam_function_objects.json": json.dumps(
            {
                "schema": "flowlab.openfoam_function_object_runtime.v1",
                "contract": "constant/flowlab_patch_metrics.json",
                "defaultStyle": "controlDict-functions",
                "runtimeStyles": {"opencfd": {}, "foundation": {}},
                "functionObjects": ["patchFlowRate", "patchAverage", "wallShearStress", "wallForces"],
                "patches": {"inlet": ["inlet"], "outlet": ["outlet"], "wall": ["walls"], "flow": ["inlet", "outlet"]},
            }
        )
        + "\n",
        "mesh/flowlab_mesh.json": "{}\n",
        "mesh/flowlab_mesh.vtk": """# vtk DataFile Version 3.0
FlowLab test mesh
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
""",
        "mesh/flowlab_mesh.vtu": "<VTKFile></VTKFile>\n",
        "mesh/quality.json": json.dumps(
            {
                "schema": "flowlab.mesh_quality.v1",
                "status": "ok",
                "summary": {
                    "minCellArea": 0.1,
                    "maxAspectRatio": 1.4,
                    "maxNonOrthogonalityDeg": 12.5,
                    "maxSkewnessEstimate": 0.2,
                },
                "thresholds": {
                    "minCellArea": 1e-9,
                    "maxAspectRatio": 200,
                    "maxNonOrthogonalityDeg": 85,
                    "maxSkewnessEstimate": 0.95,
                },
                "warnings": [],
            }
        )
        + "\n",
        "mesh/boundary_layer_plan.json": (
            '{"schema":"flowlab.boundary_layer_plan.v1","productionReady":false,"edges":[]}\n'
        ),
        "mesh/prism_layer_plan.json": (
            '{"schema":"flowlab.prism_layer_plan.v1","productionReady":false,"edges":[],'
            '"readinessChecks":[{"id":"native-prism-layer-mesh","status":"fail","detail":"starter"}],'
            '"blockingReasons":["starter"]}\n'
        ),
        "mesh/adaptation_plan.json": (
            '{"schema":"flowlab.mesh_adaptation_plan.v1","productionReady":false,'
            '"sourceArtifacts":{"quality":"mesh/quality.json","refinementPlan":"mesh/refinement_plan.json","boundaryLayerPlan":"mesh/boundary_layer_plan.json","prismLayerPlan":"mesh/prism_layer_plan.json","physicalGroups":"mesh/physical_groups.json"},'
            '"adaptationTargets":[{"edgeId":"pipe","geometryTargets":{"enabled":false},"boundaryLayerTargets":{"enabled":true},"fieldIndicatorTargets":["pressure-gradient"]}],'
            '"readinessChecks":[{"id":"native-adapted-volume-mesh","status":"fail","detail":"starter"}],'
            '"blockingReasons":["starter"]}\n'
        ),
        "mesh/production_mesh_plan.json": (
            '{"schema":"flowlab.production_mesh_plan.v1","productionReady":false,'
            '"readinessChecks":[{"id":"cad-quality-3d-topology","status":"fail","detail":"starter"}],'
            '"blockingReasons":["starter"]}\n'
        ),
        "mesh/production_mesh_acceptance.json": (
            '{"schema":"flowlab.production_mesh_acceptance.v1","productionReady":false,'
            '"sourceArtifacts":{"productionMeshPlan":"mesh/production_mesh_plan.json","nativeMeshingPlan":"mesh/native_meshing_plan.json","physicalGroups":"mesh/physical_groups.json","prismLayerPlan":"mesh/prism_layer_plan.json","adaptationPlan":"mesh/adaptation_plan.json","openfoamSnappyHandoff":"mesh/openfoam_snappy_handoff.json","openfoamNativeMeshPreflight":"mesh/openfoam_native_mesh_preflight.py","su2NativeMeshingHandoff":"mesh/su2_native_meshing_handoff.json","codeSaturneNativeMeshingHandoff":"mesh/code_saturne_native_meshing_handoff.json"},'
            '"acceptanceCriteria":[{"id":"cad-geometry-source","status":"fail","detail":"starter"}],'
            '"nativeQualityEvidence":{"schema":"flowlab.native_mesh_quality_evidence.v1","productionReady":false,"status":"missing-native-quality-reports","sharedRequiredEvidence":["solver-native cell-quality report","wall-distance or y-plus field for wall-bounded cases"],"solverReports":{"openfoam":{"status":"missing","commands":["checkMesh -allGeometry -allTopology"],"requiredMetrics":["failedChecks","yPlusMinMeanMax"],"currentEvidence":["mesh/openfoam_native_mesh_preflight.py"]},"su2":{"status":"missing","commands":["SU2_CFD case.cfg startup/preprocess diagnostics"],"requiredMetrics":["markerCoverage"],"currentEvidence":[]},"codeSaturne":{"status":"missing","commands":["code_saturne run preprocessing/listing review"],"requiredMetrics":["cellQuality"],"currentEvidence":[]}}},'
            '"solverAcceptance":{"openfoam":{"status":"blocked","requiredEvidence":["checkMesh"],"currentEvidence":[]},"su2":{"status":"blocked","requiredEvidence":["mesh diagnostics"],"currentEvidence":[]},"codeSaturne":{"status":"blocked","requiredEvidence":["listing"],"currentEvidence":[]}},'
            '"blockingReasons":["starter"]}\n'
        ),
        "mesh/physical_groups.json": json.dumps(
            {
                "schema": "flowlab.physical_group_map.v1",
                "productionReady": False,
                "groups": [
                    {
                        "name": "fluid_pipe",
                        "dimension": 3,
                        "role": "fluid-volume",
                        "solverNames": {"gmsh": "fluid_pipe", "su2": None, "codeSaturne": "fluid_pipe", "openfoam": "internalMesh"},
                    },
                    {
                        "name": "inlet_pipe",
                        "dimension": 2,
                        "role": "inlet",
                        "solverNames": {"gmsh": "inlet_pipe", "su2": "inlet_pipe", "codeSaturne": "inlet_pipe", "openfoam": "inlet"},
                    },
                    {
                        "name": "outlet_pipe",
                        "dimension": 2,
                        "role": "outlet",
                        "solverNames": {"gmsh": "outlet_pipe", "su2": "outlet_pipe", "codeSaturne": "outlet_pipe", "openfoam": "outlet"},
                    },
                    {
                        "name": "wall_pipe_left",
                        "dimension": 2,
                        "role": "wall",
                        "solverNames": {"gmsh": "wall_pipe_left", "su2": "wall_pipe_left", "codeSaturne": "wall_pipe_left", "openfoam": "walls"},
                    },
                    {
                        "name": "wall_pipe_front_back",
                        "dimension": 2,
                        "role": "front-back",
                        "solverNames": {"gmsh": "wall_pipe_front_back", "su2": "wall_pipe_front_back", "codeSaturne": "wall_pipe_front_back", "openfoam": "frontAndBack"},
                    },
                ],
                "solverTargets": {"gmsh": {}, "su2": {}, "codeSaturne": {}, "openfoam": {}},
            }
        )
        + "\n",
        "mesh/openfoam_snappy_handoff.json": json.dumps(
            {
                "schema": "flowlab.openfoam_snappy_handoff.v1",
                "productionReady": False,
                "templateArtifacts": {
                    "snappyHexMeshDict": "mesh/openfoam_snappyHexMeshDict.template",
                    "surfaceFeatureExtractDict": "mesh/openfoam_surfaceFeatureExtractDict.template",
                    "meshQualityDict": "mesh/openfoam_meshQualityDict.template",
                },
                "addLayersControls": {"layers": []},
                "boundaryPatchPlan": {"inlet": ["inlet_pipe"], "outlet": ["outlet_pipe"], "walls": ["wall_pipe_left"], "frontAndBack": ["wall_pipe_front_back"]},
                "readinessChecks": [{"id": "cad-surface-ready", "status": "fail", "detail": "starter"}],
                "blockingReasons": ["starter"],
            }
        )
        + "\n",
        "mesh/openfoam_snappyHexMeshDict.template": "review-only OpenFOAM native meshing template\ncastellatedMeshControls\naddLayersControls\nreviewedFlowLabSurfaces.stl\n",
        "mesh/openfoam_surfaceFeatureExtractDict.template": "surfaceFeatureExtractDict\n",
        "mesh/openfoam_meshQualityDict.template": "meshQualityDict\n",
        "mesh/su2_native_meshing_handoff.json": json.dumps(
            {
                "schema": "flowlab.su2_native_meshing_handoff.v1",
                "productionReady": False,
                "markerPlan": {"allMarkers": ["inlet_pipe", "outlet_pipe", "wall_pipe_left"]},
                "viscousLayerPlan": {"source": "mesh/prism_layer_plan.json"},
                "readinessChecks": [{"id": "native-su2-production-mesh", "status": "fail", "detail": "starter"}],
                "blockingReasons": ["starter"],
            }
        )
        + "\n",
        "mesh/code_saturne_native_meshing_handoff.json": json.dumps(
            {
                "schema": "flowlab.code_saturne_native_meshing_handoff.v1",
                "productionReady": False,
                "importPlan": {"boundaryGroups": ["inlet_pipe", "outlet_pipe", "wall_pipe_left"], "volumeGroups": ["fluid_pipe"]},
                "prismLayerImportPlan": {"source": "mesh/prism_layer_plan.json"},
                "readinessChecks": [{"id": "native-code-saturne-production-mesh", "status": "fail", "detail": "starter"}],
                "blockingReasons": ["starter"],
            }
        )
        + "\n",
        "mesh/native_meshing_plan.json": (
            '{"schema":"flowlab.native_meshing_plan.v1","productionReady":false,'
            '"handoffArtifacts":["mesh/gmsh_production_handoff.geo","mesh/physical_groups.json","mesh/openfoam_snappy_handoff.json","mesh/openfoam_native_mesh_preflight.py","mesh/su2_native_meshing_handoff.json","mesh/code_saturne_native_meshing_handoff.json","mesh/openfoam_snappyHexMeshDict.template","mesh/openfoam_surfaceFeatureExtractDict.template","mesh/openfoam_meshQualityDict.template","mesh/adaptation_plan.json","mesh/production_mesh_acceptance.json","mesh/native_meshing_plan.json"],'
            '"prismLayerPlan":{"file":"mesh/prism_layer_plan.json"},'
            '"adaptationPlan":{"file":"mesh/adaptation_plan.json"},'
            '"readinessChecks":[{"id":"cad-surface-import","status":"fail","detail":"starter"}],'
            '"blockingReasons":["starter"]}\n'
        ),
        "mesh/openfoam_native_mesh_preflight.py": (
            'FLOWLAB_OPENFOAM_NATIVE_MESH_PREFLIGHT_SCHEMA = "flowlab.openfoam_native_mesh_preflight.v1"\n'
            'SCHEMA = "flowlab.openfoam_native_mesh_preflight_report.v1"\n'
            '"constant/triSurface/reviewedFlowLabSurfaces.stl"\n'
            '"locationInMesh"\n'
            '"snappyHexMesh -overwrite"\n'
            '"postProcess -func yPlus"\n'
        ),
        "mesh/gmsh_production_handoff.geo": "// FlowLab review-only native meshing handoff\n",
        "mesh/openfoam_review.json": (
            '{"schema":"flowlab.openfoam_mesh_review.v1","productionReady":false,'
            '"readinessChecks":[{"id":"starter","status":"fail","detail":"starter"}],'
            '"blockingReasons":["starter"]}\n'
        ),
    }
    openfoam_snappy = json.loads(files["mesh/openfoam_snappy_handoff.json"])
    openfoam_snappy["installedArtifacts"] = {
        "triSurface": "constant/triSurface/reviewedFlowLabSurfaces.stl",
        "snappyHexMeshDict": "system/snappyHexMeshDict",
        "surfaceFeatureExtractDict": "system/surfaceFeatureExtractDict",
        "meshQualityDict": "system/meshQualityDict",
    }
    openfoam_snappy["starterGeometry"] = {
        "triSurface": "constant/triSurface/reviewedFlowLabSurfaces.stl",
        "source": "FlowLab starter quad-strip extrusion",
        "cadReviewed": False,
        "locationInMesh": [0.7, 0.0, 0.001],
    }
    openfoam_snappy["expectedNativeFiles"] = [
        "constant/triSurface/reviewedFlowLabSurfaces.stl",
        "system/snappyHexMeshDict",
        "system/surfaceFeatureExtractDict",
        "system/meshQualityDict",
    ]
    files["mesh/openfoam_snappy_handoff.json"] = json.dumps(openfoam_snappy) + "\n"
    production_acceptance = json.loads(files["mesh/production_mesh_acceptance.json"])
    production_acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]["currentEvidence"].extend(
        [
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
        ]
    )
    production_acceptance["solverAcceptance"]["openfoam"]["currentEvidence"].extend(
        [
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
        ]
    )
    files["mesh/production_mesh_acceptance.json"] = json.dumps(production_acceptance) + "\n"
    native_meshing_plan = json.loads(files["mesh/native_meshing_plan.json"])
    native_meshing_plan["handoffArtifacts"].extend(
        [
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "system/snappyHexMeshDict",
            "system/surfaceFeatureExtractDict",
            "system/meshQualityDict",
        ]
    )
    files["mesh/native_meshing_plan.json"] = json.dumps(native_meshing_plan) + "\n"
    files["constant/triSurface/reviewedFlowLabSurfaces.stl"] = (
        "solid reviewedFlowLabSurfaces\n"
        "  // FlowLab-generated starter triSurface\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid reviewedFlowLabSurfaces\n"
    )
    files["system/snappyHexMeshDict"] = "castellatedMeshControls\ngeometry { reviewedFlowLabSurfaces.stl {} }\nlocationInMesh (0.7 0 0.001);\n"
    files["system/surfaceFeatureExtractDict"] = "surfaceFeatureExtractDict\nreviewedFlowLabSurfaces.stl {}\n"
    files["system/meshQualityDict"] = "meshQualityDict\nmaxNonOrtho 65;\n"
    if solver == "su2":
        run_command = ["SU2_CFD", "case.cfg"]
        su2_preset = {
            "schema": "flowlab.su2_mode_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
            "supportLevel": "starter-supported-single-zone",
            "readinessChecks": [{"id": "single-zone-supported", "status": "pass", "detail": "starter"}],
            "blockingReasons": [],
            "nativeSetupPlan": {"manualNativeModules": []},
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity"]},
        }
        su2_checklist = {
            "schema": "flowlab.su2_native_setup_checklist.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportLevel": "starter-supported-single-zone",
            "requestedPhysicsResolved": True,
            "productionReady": False,
            "generatedFiles": ["case.cfg", "flowlab_su2_capability_matrix.json"],
            "readinessItems": [{"id": "single-zone-supported", "status": "pass", "detail": "starter"}],
            "expectedPrimaryFields": ["pressure", "velocity"],
            "actionItems": [],
        }
        su2_matrix = {
            "schema": "flowlab.su2_capability_matrix.v1",
            "activeMode": "incompressible-navier-stokes",
            "productionReady": False,
            "entries": [
                {
                    "advancedMode": "incompressible-navier-stokes",
                    "active": True,
                    "supportLevel": "starter-supported-single-zone",
                    "supportedByAdapter": True,
                    "requestedPhysicsResolved": True,
                    "productionReady": False,
                    "manualNativeModules": [],
                    "expectedPrimaryFields": ["pressure", "velocity"],
                    "blockingReasons": [],
                },
                {
                    "advancedMode": "heat-transfer",
                    "active": False,
                    "supportLevel": "starter-supported-single-zone",
                    "supportedByAdapter": True,
                    "requestedPhysicsResolved": True,
                    "productionReady": False,
                    "manualNativeModules": [],
                    "expectedPrimaryFields": ["pressure", "velocity", "temperature"],
                    "blockingReasons": [],
                },
                {
                    "advancedMode": "compressible-flow",
                    "active": False,
                    "supportLevel": "starter-supported-single-zone",
                    "supportedByAdapter": True,
                    "requestedPhysicsResolved": True,
                    "productionReady": False,
                    "manualNativeModules": [],
                    "expectedPrimaryFields": ["pressure", "velocity", "density"],
                    "blockingReasons": [],
                },
                {
                    "advancedMode": "multiphase-vof",
                    "active": False,
                    "supportLevel": "blocked-export-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "productionReady": False,
                    "manualNativeModules": ["multiphase solver"],
                    "handoffArtifacts": ["flowlab_su2_multiphase_handoff.json"],
                    "expectedPrimaryFields": ["pressure", "velocity", "phase_fraction"],
                    "blockingReasons": ["manual multiphase setup required"],
                },
                {
                    "advancedMode": "cavitation",
                    "active": False,
                    "supportLevel": "blocked-export-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "productionReady": False,
                    "manualNativeModules": ["cavitation model"],
                    "handoffArtifacts": ["flowlab_su2_cavitation_handoff.json"],
                    "expectedPrimaryFields": ["pressure", "velocity", "vapour_fraction"],
                    "blockingReasons": ["manual cavitation setup required"],
                },
                {
                    "advancedMode": "conjugate-heat-transfer",
                    "active": False,
                    "supportLevel": "blocked-export-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "productionReady": False,
                    "manualNativeModules": ["multi-zone CHT driver"],
                    "handoffArtifacts": ["flowlab_su2_cht_handoff.json"],
                    "expectedPrimaryFields": ["pressure", "velocity", "solid_temperature"],
                    "blockingReasons": ["manual CHT setup required"],
                },
                {
                    "advancedMode": "water-hammer",
                    "active": False,
                    "supportLevel": "blocked-export-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "productionReady": False,
                    "manualNativeModules": ["MOC pressure-wave boundary"],
                    "handoffArtifacts": ["flowlab_su2_water_hammer_handoff.json", "flowlab_su2_water_hammer_waveform.csv"],
                    "expectedPrimaryFields": ["pressure_wave", "velocity"],
                    "blockingReasons": ["manual water-hammer setup required"],
                },
                {
                    "advancedMode": "rigid-body-fluid-forces",
                    "active": False,
                    "supportLevel": "blocked-export-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "productionReady": False,
                    "manualNativeModules": ["moving mesh or FSI"],
                    "handoffArtifacts": ["flowlab_su2_rigid_body_handoff.json"],
                    "expectedPrimaryFields": ["pressure", "velocity", "body_force"],
                    "blockingReasons": ["manual rigid-body coupling required"],
                },
            ],
            "summary": {
                "modeCount": 8,
                "starterSupportedModes": ["incompressible-navier-stokes", "heat-transfer", "compressible-flow"],
                "blockedExportOnlyModes": [
                    "multiphase-vof",
                    "cavitation",
                    "conjugate-heat-transfer",
                    "water-hammer",
                    "rigid-body-fluid-forces",
                ],
                "handoffModes": [
                    "multiphase-vof",
                    "cavitation",
                    "conjugate-heat-transfer",
                    "water-hammer",
                    "rigid-body-fluid-forces",
                ],
            },
        }
        files = {
            "README.md": "# SU2 smoke\n",
            "case.cfg": "MESH_FILENAME= mesh/flowlab_mesh.su2\nMARKER_WALL= ( wall_pipe_left, wall_pipe_right )\n",
            "flowlab_su2_mode_preset.json": json.dumps(su2_preset) + "\n",
            "flowlab_su2_native_setup_checklist.json": json.dumps(su2_checklist) + "\n",
            "flowlab_su2_capability_matrix.json": json.dumps(su2_matrix) + "\n",
            "mesh/flowlab_mesh.su2": "NDIME= 2\n",
            "mesh/flowlab_mesh.json": "{}\n",
            "mesh/flowlab_mesh.vtk": "# vtk DataFile Version 3.0\n",
            "mesh/flowlab_mesh.vtu": "<VTKFile></VTKFile>\n",
            "mesh/quality.json": json.dumps(
                {
                    "schema": "flowlab.mesh_quality.v1",
                    "status": "ok",
                    "summary": {
                        "maxNonOrthogonalityDeg": 12.5,
                        "maxSkewnessEstimate": 0.2,
                    },
                    "thresholds": {
                        "maxNonOrthogonalityDeg": 85,
                        "maxSkewnessEstimate": 0.95,
                    },
                    "warnings": [],
                }
            )
            + "\n",
        }
    if solver == "code-saturne":
        run_command = ["code_saturne", "run"]
        code_saturne_preset = {
            "schema": "flowlab.code_saturne_physics_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "productionReady": False,
            "supportLevel": "starter-supported",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
            "readinessChecks": [{"id": "incompressible-starter-model", "status": "pass", "detail": "starter"}],
            "blockingReasons": [],
            "setupXmlModels": {"turbulence": "off"},
            "turbulencePlan": {
                "schema": "flowlab.code_saturne_turbulence_plan.v1",
                "model": "off",
                "starterStatus": "laminar-starter",
                "productionReady": False,
                "requiredEvidence": ["laminar-regime justification"],
                "unresolvedModels": ["RANS turbulence closure", "LES", "DNS"],
            },
            "nativeSetupPlan": {"manualNativeModules": []},
            "resultExpectations": {"expectedPrimaryFields": ["pressure", "velocity"]},
        }
        code_saturne_checklist = {
            "schema": "flowlab.code_saturne_native_setup_checklist.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportLevel": "starter-supported",
            "requestedPhysicsResolved": True,
            "productionReady": False,
            "generatedFiles": ["DATA/setup.xml"],
            "turbulencePlan": {
                "schema": "flowlab.code_saturne_turbulence_plan.v1",
                "model": "off",
                "starterStatus": "laminar-starter",
                "productionReady": False,
                "requiredEvidence": ["laminar-regime justification"],
                "unresolvedModels": ["RANS turbulence closure", "LES", "DNS"],
            },
            "expectedPrimaryFields": ["pressure", "velocity"],
            "actionItems": [],
        }
        code_saturne_matrix = {
            "schema": "flowlab.code_saturne_capability_matrix.v1",
            "activeMode": "incompressible-navier-stokes",
            "productionReady": False,
            "entries": [
                {
                    "advancedMode": "incompressible-navier-stokes",
                    "active": True,
                    "supportLevel": "starter-supported",
                    "supportedByAdapter": True,
                    "requestedPhysicsResolved": True,
                    "expectedPrimaryFields": ["pressure", "velocity"],
                    "turbulenceModel": "off",
                    "turbulenceStarterStatus": "laminar-starter",
                    "manualNativeModules": [],
                    "blockingReasons": [],
                },
                {
                    "advancedMode": "heat-transfer",
                    "active": False,
                    "supportLevel": "starter-supported",
                    "supportedByAdapter": True,
                    "requestedPhysicsResolved": True,
                    "expectedPrimaryFields": ["pressure", "velocity", "temperature"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": [],
                    "blockingReasons": [],
                },
                {
                    "advancedMode": "compressible-flow",
                    "active": False,
                    "supportLevel": "metadata-plus-handoff",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["pressure", "velocity", "density", "temperature", "mach_number"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": ["compressible flow module", "equation of state"],
                    "handoffArtifacts": ["DATA/flowlab_compressible_handoff.json"],
                    "blockingReasons": ["compressible module not generated"],
                },
                {
                    "advancedMode": "multiphase-vof",
                    "active": False,
                    "supportLevel": "metadata-plus-handoff",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["pressure", "velocity", "phase_fraction"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": ["VOF/free-surface model"],
                    "blockingReasons": ["VOF model not generated"],
                },
                {
                    "advancedMode": "cavitation",
                    "active": False,
                    "supportLevel": "metadata-plus-handoff",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["pressure", "velocity", "vapour_fraction"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": ["liquid-vapour phase change"],
                    "blockingReasons": ["cavitation model not generated"],
                },
                {
                    "advancedMode": "conjugate-heat-transfer",
                    "active": False,
                    "supportLevel": "metadata-plus-handoff",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["fluid_temperature", "solid_temperature", "heat_flux"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": ["multi-domain CHT"],
                    "blockingReasons": ["solid domain not generated"],
                },
                {
                    "advancedMode": "water-hammer",
                    "active": False,
                    "supportLevel": "metadata-plus-handoff",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["pressure_wave", "velocity"],
                    "turbulenceModel": "off",
                    "turbulenceStarterStatus": "laminar-starter",
                    "manualNativeModules": ["transient pressure-wave boundary"],
                    "blockingReasons": ["transient boundary not generated"],
                },
                {
                    "advancedMode": "rigid-body-fluid-forces",
                    "active": False,
                    "supportLevel": "metadata-only",
                    "supportedByAdapter": False,
                    "requestedPhysicsResolved": False,
                    "expectedPrimaryFields": ["pressure", "velocity", "body_force"],
                    "turbulenceModel": "k-epsilon",
                    "turbulenceStarterStatus": "rans-starter",
                    "manualNativeModules": ["moving mesh"],
                    "blockingReasons": ["moving mesh not generated"],
                },
            ],
            "summary": {
                "starterSupportedModes": ["incompressible-navier-stokes", "heat-transfer"],
                "unresolvedModes": [
                    "compressible-flow",
                    "multiphase-vof",
                    "cavitation",
                    "conjugate-heat-transfer",
                    "water-hammer",
                    "rigid-body-fluid-forces",
                ],
                "handoffModes": ["compressible-flow", "multiphase-vof", "cavitation", "conjugate-heat-transfer", "water-hammer"],
            },
        }
        files = {
            "README.md": "# Code_Saturne smoke\n",
            "DATA/setup.xml": '<mesh_input path="MESH/flowlab_mesh.msh"/>\n',
            "DATA/flowlab_physics_preset.json": json.dumps(code_saturne_preset) + "\n",
            "DATA/flowlab_native_setup_checklist.json": json.dumps(code_saturne_checklist) + "\n",
            "DATA/flowlab_code_saturne_capability_matrix.json": json.dumps(code_saturne_matrix) + "\n",
            "DATA/run.cfg": "parameters = setup.xml\n",
            "DATA/cs_user_scripts.py": 'domain.mesh_dir = "MESH"\ndomain.meshes = ["flowlab_mesh.msh"]\n',
            "DATA/cs_user_physics.py": "FLOWLAB_CODE_SATURNE_PHYSICS_PRESET = {}\n",
            "SRC/cs_user_boundary_conditions.f90": "subroutine cs_f_user_boundary_conditions\nend subroutine cs_f_user_boundary_conditions\n",
            "MESH/flowlab_mesh.msh": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "mesh/flowlab_mesh.json": "{}\n",
            "mesh/quality.json": json.dumps(
                {
                    "schema": "flowlab.mesh_quality.v1",
                    "status": "ok",
                    "summary": {
                        "maxNonOrthogonalityDeg": 12.5,
                        "maxSkewnessEstimate": 0.2,
                    },
                    "thresholds": {
                        "maxNonOrthogonalityDeg": 85,
                        "maxSkewnessEstimate": 0.95,
                    },
                    "warnings": [],
                }
            )
            + "\n",
        }
    if solver == "mujoco":
        run_command = ["python3", "run_mujoco.py"]
        files = {
            "README.md": "# MuJoCo smoke\n",
            "model.xml": "<mujoco model=\"smoke\"></mujoco>\n",
            "run_mujoco.py": "import mujoco\nmodel = mujoco.MjModel.from_xml_path('model.xml')\n",
            "mesh/flowlab_mesh.json": "{}\n",
        }
    return add_case_manifest(SolverCase.model_construct(
        id="case-test",
        projectName="Execution smoke",
        solver=solver,
        advancedMode="incompressible-navier-stokes",
        status="generated",
        files=files,
        runCommand=run_command,
        provenance=[],
    ))


def _reviewed_stl() -> str:
    return (
        "solid reviewedFlowLabSurfaces\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid reviewedFlowLabSurfaces\n"
    )


def _reviewed_boundary_tags(*, complete: bool = True) -> list[dict[str, str]]:
    tags = [
        {"id": "bt-inlet", "role": "inlet", "patchName": "reviewed_inlet", "label": "Reviewed inlet", "notes": ""},
    ]
    if complete:
        tags.extend(
            [
                {
                    "id": "bt-outlet",
                    "role": "outlet",
                    "patchName": "reviewed_outlet",
                    "label": "Reviewed outlet",
                    "notes": "",
                },
                {"id": "bt-wall", "role": "wall", "patchName": "reviewed_wall", "label": "Reviewed wall", "notes": ""},
                {
                    "id": "bt-interface",
                    "role": "interface",
                    "patchName": "reviewed_interface",
                    "label": "Reviewed interface",
                    "notes": "",
                },
            ]
        )
    return tags


def _reviewed_boundary_tag_validation(*, complete: bool = True) -> dict:
    tags = _reviewed_boundary_tags(complete=complete)
    roles_present = sorted({tag["role"] for tag in tags})
    missing = [] if complete else ["outlet", "wall"]
    return {
        "requiredRoles": ["inlet", "outlet", "wall"],
        "rolesPresent": roles_present,
        "missingRequiredRoles": missing,
        "complete": complete,
        "tags": tags,
        "status": "pass" if complete else "fail",
        "issues": [],
    }


def _mark_case_as_reviewed_stl(case: SolverCase, *, complete_boundary_tags: bool = True) -> SolverCase:
    boundary_tags = _reviewed_boundary_tags(complete=complete_boundary_tags)
    boundary_tag_validation = _reviewed_boundary_tag_validation(complete=complete_boundary_tags)
    handoff = json.loads(case.files["mesh/openfoam_snappy_handoff.json"])
    handoff["starterGeometry"].update(
        {
            "source": "User-reviewed STL import",
            "sourceType": "uploaded-stl",
            "cadReviewed": True,
            "reviewedAt": "2026-06-15T00:00:00Z",
            "reviewNotes": "CAD-reviewed test STL.",
            "validation": {"status": "pass", "checks": ["solid", "facet normal", "vertex"], "reasons": []},
            "stlMetadata": {
                "triangleCount": 1,
                "vertexCount": 3,
                "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 0.0]},
                "watertightCheck": {
                    "status": "warning",
                    "openEdgeCount": 3,
                    "method": "undirected triangle edge pair count rounded to 1e-9",
                },
                "asciiValidation": {"status": "pass", "reasons": []},
            },
            "boundaryTags": boundary_tags,
            "boundaryTagValidation": boundary_tag_validation,
        }
    )
    handoff["reviewedGeometry"] = handoff["starterGeometry"]
    handoff["reviewedBoundaryTags"] = boundary_tag_validation
    handoff["boundaryPatchPlan"] = {
        "source": "reviewed-boundary-tags",
        "inlet": ["reviewed_inlet"],
        "outlet": ["reviewed_outlet"] if complete_boundary_tags else [],
        "walls": ["reviewed_wall"] if complete_boundary_tags else [],
        "interfaces": ["reviewed_interface"] if complete_boundary_tags else [],
        "frontAndBack": [],
    }
    handoff["addLayersControls"] = {
        **handoff.get("addLayersControls", {}),
        "layers": [
            {
                "patch": "reviewed_wall",
                "sourceBoundaryTag": boundary_tags[2],
                "nSurfaceLayers": 1,
                "firstLayerThickness": None,
                "expansionRatio": 1.2,
                "totalLayerThickness": None,
                "requiredEvidence": ["reviewed STL wall tag", "native addLayers output", "y-plus or wall-distance evidence"],
            }
        ]
        if complete_boundary_tags
        else [],
    }
    case.files["mesh/openfoam_snappy_handoff.json"] = json.dumps(handoff) + "\n"
    case.files["constant/triSurface/reviewedFlowLabSurfaces.stl"] = _reviewed_stl()
    case.files["system/snappyHexMeshDict"] = (
        "castellatedMeshControls\n"
        "geometry { reviewedFlowLabSurfaces.stl {} }\n"
        "reviewed_inlet { inGroups (inlet); }\n"
        + ("reviewed_outlet { inGroups (outlet); }\nreviewed_wall { inGroups (wall); }\nreviewed_interface { inGroups (interface); }\n" if complete_boundary_tags else "")
        + "locationInMesh (0.7 0 0.001);\n"
    )
    acceptance = json.loads(case.files["mesh/production_mesh_acceptance.json"])
    criteria = acceptance.setdefault("acceptanceCriteria", [])
    existing_ids = {criterion.get("id") for criterion in criteria if isinstance(criterion, dict)}
    for criterion_id in [
        "native-3d-volume-mesh",
        "boundary-layer-prism-mesh",
        "adapted-refinement-evidence",
        "solver-native-quality-evidence",
    ]:
        if criterion_id not in existing_ids:
            criteria.append({"id": criterion_id, "status": "fail", "evidence": [], "detail": "starter"})
    case.files["mesh/production_mesh_acceptance.json"] = json.dumps(acceptance) + "\n"
    return add_case_manifest(case)


def _add_flowlab_generated_base_mesh(case: SolverCase) -> SolverCase:
    handoff = json.loads(case.files["mesh/openfoam_snappy_handoff.json"])
    handoff["starterGeometry"]["sourceType"] = "flowlab-generated"
    handoff["starterGeometry"]["cadReviewed"] = False
    handoff["reviewedGeometry"] = handoff["starterGeometry"]
    case.files["mesh/openfoam_snappy_handoff.json"] = json.dumps(handoff) + "\n"
    case.files.update(
        {
            "constant/polyMesh/points": "4\n((0 0 0)(1 0 0)(1 1 0)(0 1 0))\n",
            "constant/polyMesh/faces": "1\n(4(0 1 2 3))\n",
            "constant/polyMesh/owner": "1\n(0)\n",
            "constant/polyMesh/neighbour": "0\n()\n",
            "constant/polyMesh/boundary": """4
(
inlet
{
    type patch;
    nFaces 1;
    startFace 0;
}
outlet
{
    type patch;
    nFaces 1;
    startFace 1;
}
walls
{
    type wall;
    nFaces 1;
    startFace 2;
}
frontAndBack
{
    type empty;
    nFaces 1;
    startFace 3;
}
)
""",
        }
    )
    return add_case_manifest(case)


def _reviewed_surface(surface_name: str, role: str, patch_name: str, *, x_offset: float = 0.0) -> dict:
    return {
        "id": f"surf-{surface_name}",
        "surfaceName": surface_name,
        "role": role,
        "patchName": patch_name,
        "triSurface": f"constant/triSurface/{surface_name}.stl",
        "cadReviewed": True,
        "reviewNotes": f"Reviewed {role} surface.",
        "sourceType": "uploaded-stl",
        "patchInfo": {"type": "wall" if role == "wall" else "patch"},
        "validation": {"status": "pass", "checks": ["solid", "facet normal", "vertex"], "reasons": []},
        "stlMetadata": {
            "triangleCount": 1,
            "vertexCount": 3,
            "bounds": {"min": [x_offset, 0.0, 0.0], "max": [x_offset + 1.0, 1.0, 0.0]},
            "watertightCheck": {
                "status": "warning",
                "openEdgeCount": 3,
                "method": "undirected triangle edge pair count rounded to 1e-9",
            },
            "asciiValidation": {"status": "pass", "reasons": []},
        },
    }


def _boundary_condition_for_reviewed_surface(role: str, patch_name: str) -> dict:
    if role == "inlet":
        return {
            "type": "velocity-inlet",
            "status": "ready",
            "velocity": {"x": 1.25, "y": 0.0, "z": 0.0},
        }
    if role == "outlet":
        return {
            "type": "pressure-outlet",
            "status": "ready",
            "pressure": 101325.0,
        }
    if role == "wall":
        return {
            "type": "no-slip-wall",
            "status": "ready",
        }
    return {
        "type": "coupled-interface",
        "status": "placeholder",
        "notes": f"{patch_name} requires native mapped/coupled interface review.",
    }


def _reviewed_surface_boundary_fields(patches: dict[str, str]) -> dict[str, str]:
    inlet = patches["inlet"]
    outlet = patches["outlet"]
    wall = patches.get("wall")
    wall_u = f"""    {wall}
    {{
        type            noSlip;
    }}
""" if wall else ""
    wall_p = f"""    {wall}
    {{
        type            zeroGradient;
    }}
""" if wall else ""
    wall_t = f"""    {wall}
    {{
        type            zeroGradient;
    }}
""" if wall else ""
    return {
        "0/U": f"""FoamFile {{ class volVectorField; object U; }}
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (1.25 0 0);
boundaryField
{{
    {inlet}
    {{
        type            fixedValue;
        value           uniform (1.25 0 0);
    }}
    {outlet}
    {{
        type            zeroGradient;
    }}
{wall_u.rstrip()}
}}
""",
        "0/p": f"""FoamFile {{ class volScalarField; object p; }}
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 101325;
boundaryField
{{
    {inlet}
    {{
        type            zeroGradient;
    }}
    {outlet}
    {{
        type            fixedValue;
        value           uniform 101325;
    }}
{wall_p.rstrip()}
}}
""",
        "0/T": f"""FoamFile {{ class volScalarField; object T; }}
dimensions      [0 0 0 1 0 0 0];
internalField   uniform 293.15;
boundaryField
{{
    {inlet}
    {{
        type            fixedValue;
        value           uniform 293.15;
    }}
    {outlet}
    {{
        type            zeroGradient;
    }}
{wall_t.rstrip()}
}}
""",
    }


def _multi_surface_stl(surface_name: str, *, x_offset: float = 0.0) -> str:
    return (
        f"solid {surface_name}\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        f"      vertex {x_offset} 0 0\n"
        f"      vertex {x_offset + 1.0} 0 0\n"
        f"      vertex {x_offset} 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        f"endsolid {surface_name}\n"
    )


def _mark_case_as_multi_surface_reviewed_stl(
    case: SolverCase,
    *,
    include_wall: bool = True,
    include_boundary_conditions: bool = False,
) -> SolverCase:
    surfaces = [
        _reviewed_surface("inlet", "inlet", "reviewed_inlet", x_offset=0.0),
        _reviewed_surface("outlet", "outlet", "reviewed_outlet", x_offset=2.0),
    ]
    if include_wall:
        surfaces.append(_reviewed_surface("walls", "wall", "reviewed_walls", x_offset=4.0))
    if include_boundary_conditions:
        for surface in surfaces:
            surface["boundaryCondition"] = _boundary_condition_for_reviewed_surface(
                surface["role"], surface["patchName"]
            )

    roles_present = sorted({surface["role"] for surface in surfaces})
    missing_roles = [role for role in ["inlet", "outlet", "wall"] if role not in roles_present]
    patches_with_conditions = sorted(
        surface["patchName"]
        for surface in surfaces
        if isinstance(surface.get("boundaryCondition"), dict)
        and surface["boundaryCondition"].get("status") in {"ready", "placeholder"}
    )
    required_patch_names = [surface["patchName"] for surface in surfaces]
    missing_condition_patches = sorted(set(required_patch_names) - set(patches_with_conditions))
    boundary_condition_coverage = {
        "requiredPatchNames": required_patch_names,
        "patchesWithConditions": patches_with_conditions,
        "missingPatchNames": missing_condition_patches,
        "complete": not missing_condition_patches,
        "status": "pass" if not missing_condition_patches else "fail",
    }
    coverage = {
        "requiredRoles": ["inlet", "outlet", "wall"],
        "rolesPresent": roles_present,
        "missingRequiredRoles": missing_roles,
        "complete": not missing_roles,
        "status": "pass" if not missing_roles else "fail",
    }

    handoff = json.loads(case.files["mesh/openfoam_snappy_handoff.json"])
    handoff["starterGeometry"].update(
        {
            "source": "User-reviewed multi-surface STL import",
            "sourceType": "multi-surface-stl",
            "cadReviewed": True,
            "reviewedAt": "2026-06-15T00:00:00Z",
            "reviewNotes": "CAD-reviewed multi-surface test STL.",
            "validation": {"status": "pass", "checks": ["solid", "facet normal", "vertex"], "reasons": []},
            "boundaryCoverage": coverage,
        }
    )
    handoff["reviewedGeometry"] = {
        "sourceType": "multi-surface-stl",
        "cadReviewed": True,
        "reviewedAt": "2026-06-15T00:00:00Z",
        "reviewNotes": "CAD-reviewed multi-surface test STL.",
        "surfaces": surfaces,
    }
    handoff["reviewedSurfaces"] = surfaces
    handoff["boundaryCoverage"] = coverage
    handoff["boundaryConditionCoverage"] = boundary_condition_coverage
    handoff["expectedPatchNames"] = [surface["patchName"] for surface in surfaces]
    handoff["boundaryPatchPlan"] = {
        "source": "reviewed-surfaces",
        "inlet": ["reviewed_inlet"],
        "outlet": ["reviewed_outlet"],
        "walls": ["reviewed_walls"] if include_wall else [],
        "interfaces": [],
        "frontAndBack": [],
    }
    handoff["addLayersControls"] = {
        **handoff.get("addLayersControls", {}),
        "layers": [
            {
                "patch": "reviewed_walls",
                "sourceSurface": "walls",
                "nSurfaceLayers": 1,
                "firstLayerThickness": None,
                "expansionRatio": 1.2,
                "totalLayerThickness": None,
                "requiredEvidence": ["reviewed wall STL surface", "native addLayers output", "y-plus or wall-distance evidence"],
            }
        ]
        if include_wall
        else [],
    }
    handoff["installedArtifacts"] = {
        **handoff.get("installedArtifacts", {}),
        "triSurfaces": {surface["surfaceName"]: surface["triSurface"] for surface in surfaces},
    }
    handoff["expectedNativeFiles"] = sorted(
        set(handoff.get("expectedNativeFiles", [])) | {surface["triSurface"] for surface in surfaces}
    )
    case.files["mesh/openfoam_snappy_handoff.json"] = json.dumps(handoff) + "\n"

    case.files["constant/triSurface/reviewedFlowLabSurfaces.stl"] = _reviewed_stl()
    case.files["constant/triSurface/inlet.stl"] = _multi_surface_stl("inlet", x_offset=0.0)
    case.files["constant/triSurface/outlet.stl"] = _multi_surface_stl("outlet", x_offset=2.0)
    if include_wall:
        case.files["constant/triSurface/walls.stl"] = _multi_surface_stl("walls", x_offset=4.0)
    if include_boundary_conditions:
        patches = {"inlet": "reviewed_inlet", "outlet": "reviewed_outlet"}
        if include_wall:
            patches["wall"] = "reviewed_walls"
        case.files.update(
            _reviewed_surface_boundary_fields(patches)
        )
    case.files["system/controlDict"] = _openfoam_metric_control_dict(
        inlet_patches="reviewed_inlet reviewed_outlet",
        wall_patches="reviewed_walls" if include_wall else "walls",
    )
    case.files["system/functions"] = case.files["system/controlDict"].split("functions\n{", 1)[1].rsplit("\n}", 1)[0]
    case.files["constant/flowlab_patch_metrics.json"] = _openfoam_patch_metrics_manifest(
        inlet=["reviewed_inlet"],
        outlet=["reviewed_outlet"],
        wall=["reviewed_walls"] if include_wall else ["walls"],
    )
    case.files["system/snappyHexMeshDict"] = (
        "castellatedMeshControls\n"
        "geometry\n"
        "{\n"
        "    inlet.stl { type triSurfaceMesh; name reviewed_inlet; }\n"
        "    outlet.stl { type triSurfaceMesh; name reviewed_outlet; }\n"
        + ("    walls.stl { type triSurfaceMesh; name reviewed_walls; }\n" if include_wall else "")
        + "    reviewedFlowLabSurfaces.stl { type triSurfaceMesh; name reviewedFlowLabSurfaces; }\n"
        "}\n"
        "refinementSurfaces\n"
        "{\n"
        "    reviewed_inlet { level (2 2); patchInfo { type patch; } }\n"
        "    reviewed_outlet { level (2 2); patchInfo { type patch; } }\n"
        + ("    reviewed_walls { level (2 2); patchInfo { type wall; } }\n" if include_wall else "")
        + "}\n"
        "locationInMesh (0.7 0 0.001);\n"
    )
    acceptance = json.loads(case.files["mesh/production_mesh_acceptance.json"])
    criteria = acceptance.setdefault("acceptanceCriteria", [])
    existing_ids = {criterion.get("id") for criterion in criteria if isinstance(criterion, dict)}
    for criterion_id in [
        "native-3d-volume-mesh",
        "boundary-layer-prism-mesh",
        "adapted-refinement-evidence",
        "solver-native-quality-evidence",
    ]:
        if criterion_id not in existing_ids:
            criteria.append({"id": criterion_id, "status": "fail", "evidence": [], "detail": "starter"})
    case.files["mesh/production_mesh_acceptance.json"] = json.dumps(acceptance) + "\n"
    return add_case_manifest(case)


def _wait_for(manager: JobManager, job_id: str, statuses: set[str], timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job.status in statuses:
            return job
        time.sleep(0.01)
    return manager.get_job(job_id)


def _wait_until(manager: JobManager, job_id: str, predicate, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and predicate(job):
            return job
        time.sleep(0.01)
    return manager.get_job(job_id)


def _openfoam_command_available(command: str) -> bool:
    return command in {"foamRun", "surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh", "postProcess"}


def _connected_code_saturne_project() -> dict:
    return {
        "name": "Code Saturne connected case",
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


def _write_openfoam_native_time_fields(case_dir: Path, time_name: str = "0.1", *, malformed: bool = False) -> None:
    time_dir = case_dir / time_name
    time_dir.mkdir(parents=True, exist_ok=True)
    (time_dir / "U").write_text(
        """FoamFile { class volVectorField; object U; }
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (1 0 0);
boundaryField {}
""",
        encoding="utf-8",
    )
    (time_dir / "p").write_text(
        """FoamFile { class volScalarField; object p; }
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 12.5;
boundaryField {}
"""
        if not malformed
        else "FoamFile { class volScalarField; object p; }\ninternalField nonuniform List<scalar> 2 (1 2);\n",
        encoding="utf-8",
    )
    (time_dir / "T").write_text(
        """FoamFile { class volScalarField; object T; }
dimensions      [0 0 0 1 0 0 0];
internalField   uniform 293.15;
boundaryField {}
""",
        encoding="utf-8",
    )


def _write_openfoam_patch_metric_outputs(case_dir: Path) -> None:
    flow_dir = case_dir / "postProcessing" / "patchFlowRate" / "0"
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "patchFlowRate.dat").write_text(
        "# Time inlet outlet\n0.1 -0.012 0.0118\n",
        encoding="utf-8",
    )
    pressure_dir = case_dir / "postProcessing" / "patchAverage" / "0"
    pressure_dir.mkdir(parents=True, exist_ok=True)
    (pressure_dir / "p.dat").write_text(
        "# Time inlet outlet\n0.1 101325 99000\n",
        encoding="utf-8",
    )
    shear_dir = case_dir / "postProcessing" / "wallShearStress" / "0"
    shear_dir.mkdir(parents=True, exist_ok=True)
    (shear_dir / "wallShearStress.dat").write_text(
        "# Time walls_min walls_mean walls_max\n0.1 0.4 1.1 2.8\n",
        encoding="utf-8",
    )
    forces_dir = case_dir / "postProcessing" / "wallForces" / "0"
    forces_dir.mkdir(parents=True, exist_ok=True)
    (forces_dir / "forces.dat").write_text(
        "# Time forces(pressure viscous) moments(pressure viscous)\n"
        "0.1 ((1 2 3) (0.1 0.2 0.3)) ((4 5 6) (0.4 0.5 0.6))\n",
        encoding="utf-8",
    )
    probes_dir = case_dir / "postProcessing" / "probes" / "0"
    probes_dir.mkdir(parents=True, exist_ok=True)
    (probes_dir / "p").write_text(
        "# Probe 0 (0 0 0)\n# Probe 1 (1 0 0)\n# Time p0 p1\n0.1 101325 99000\n",
        encoding="utf-8",
    )


class SuccessfulProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        joined = " ".join(str(part) for part in command)
        if "surfaceFeatureExtract" in joined:
            self.stdout = iter(["Extracting surface features\n", "End\n"])
            return
        if "blockMesh" in joined:
            self.stdout = iter(["Creating block mesh\n", "Mesh OK.\n"])
            return
        if "snappyHexMesh" in joined:
            self.stdout = iter(["snappyHexMesh: castellatedMesh true\n", "Added 24 layers over 24 faces\n", "End\n"])
            return
        if "checkMesh" in joined:
            self.stdout = iter(
                [
                    "Mesh stats\n",
                    "    points:           112\n",
                    "    faces:            276\n",
                    "    internal faces:   82\n",
                    "    cells:            39\n",
                    "    Max aspect ratio = 5 OK.\n",
                    "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                    "    Max skewness = 0.42 OK.\n",
                    "    Min volume = 1e-09. Max volume = 1e-06.\n",
                    "Failed 0 mesh checks.\n",
                    "Mesh OK.\n",
                ]
            )
            return
        if "postProcess" in joined and "yPlus" in joined and "cwd" in kwargs:
            yplus_dir = Path(kwargs["cwd"]) / "postProcessing" / "yPlus" / "0"
            yplus_dir.mkdir(parents=True, exist_ok=True)
            (yplus_dir / "yPlus.dat").write_text("Time min mean max\n0 0.8 18.5 42.0\n", encoding="utf-8")
            self.stdout = iter(["Executing functionObject yPlus\n", "End\n"])
            return
        if "Allrun" in joined and "cwd" in kwargs:
            case_dir = Path(kwargs["cwd"])
            _write_openfoam_native_time_fields(case_dir)
            _write_openfoam_patch_metric_outputs(case_dir)
        self.stdout = iter(["Time = 0.001\n", "solver ok\n"])

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class ChtMeshPreflightProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.stdout = iter(
            [
                "FlowLab OpenFOAM CHT region mesh check: fluid\n",
                "    points:           112\n",
                "    faces:            276\n",
                "    cells:            39\n",
                "    Max aspect ratio = 5 OK.\n",
                "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                "    Max skewness = 0.42 OK.\n",
                "Failed 0 mesh checks.\n",
                "Mesh OK.\n",
                "checkMesh -region solid -allGeometry -allTopology\n",
                "    points:           224\n",
                "    faces:            552\n",
                "    cells:            78\n",
                "    Max aspect ratio = 8.5 OK.\n",
                "    Mesh non-orthogonality Max: 18 average: 4.5\n",
                "    Max skewness = 0.61 OK.\n",
                "Failed 0 mesh checks.\n",
                "Mesh OK.\n",
            ]
        )


class ResultWritingProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        case_dir = Path(kwargs["cwd"])
        (case_dir / "postProcessing").mkdir(exist_ok=True)
        (case_dir / "postProcessing" / "field_0001.vtk").write_text(
            """# vtk DataFile Version 3.0
mock solver result
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
        (case_dir / "postProcessing" / "residuals" / "0").mkdir(parents=True, exist_ok=True)
        (case_dir / "postProcessing" / "residuals" / "0" / "residuals.dat").write_text(
            "Time Ux p\n0.001 7.5e-06 9e-05\n",
            encoding="utf-8",
        )


class PatchMetricWritingProcess(ResultWritingProcess):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        _write_openfoam_patch_metric_outputs(Path(kwargs["cwd"]))


class FieldOnlyResultWritingProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        _write_openfoam_native_time_fields(case_dir)
        (case_dir / "postProcessing").mkdir(exist_ok=True)
        (case_dir / "postProcessing" / "field_0001.vtk").write_text(
            """# vtk DataFile Version 3.0
mock solver result
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
        self.stdout = iter(["Time = 0.001\n", "solver ok\n"])

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


class OpenFOAMLogProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        if "Allrun" in " ".join(str(part) for part in command) and "cwd" in kwargs:
            case_dir = Path(kwargs["cwd"])
            _write_openfoam_native_time_fields(case_dir)
            _write_openfoam_patch_metric_outputs(case_dir)
        self.stdout = iter(
            [
                "Mesh stats\n",
                "    points:           112\n",
                "    faces:            276\n",
                "    internal faces:   82\n",
                "    cells:            39\n",
                "    Max aspect ratio = 5 OK.\n",
                "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                "    Max skewness = 0.42 OK.\n",
                "Failed 0 mesh checks.\n",
                "Mesh OK.\n",
                "Time = 0.001\n",
                "smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 7.5e-06, No Iterations 2\n",
                "GAMG:  Solving for p, Initial residual = 0.4, Final residual = 9e-05, No Iterations 3\n",
                "Time = 0.002\n",
                "WARNING: Courant number adjusted\n",
            ]
        )


class OpenFOAMNativeMeshSuccessProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        joined = " ".join(command)
        if "surfaceFeatureExtract" in joined:
            self.stdout = iter(["Extracting surface features\n", "End\n"])
        elif "blockMesh" in joined:
            self.stdout = iter(["Creating block mesh\n", "Mesh OK.\n"])
        elif "snappyHexMesh" in joined:
            self.stdout = iter(
                [
                    "snappyHexMesh: castellatedMesh true\n",
                    "Layer addition phase\n",
                    "Added 24 layers over 24 faces\n",
                    "End\n",
                ]
            )
        elif "checkMesh" in joined:
            self.stdout = iter(
                [
                    "Mesh stats\n",
                    "    points:           112\n",
                    "    faces:            276\n",
                    "    internal faces:   82\n",
                    "    cells:            39\n",
                    "    Max aspect ratio = 5 OK.\n",
                    "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                    "    Max skewness = 0.42 OK.\n",
                    "    Min volume = 1e-09. Max volume = 1e-06.\n",
                    "Failed 0 mesh checks.\n",
                    "Mesh OK.\n",
                ]
            )
        elif "postProcess" in joined:
            yplus_dir = case_dir / "postProcessing" / "yPlus" / "0"
            yplus_dir.mkdir(parents=True, exist_ok=True)
            (yplus_dir / "yPlus.dat").write_text("Time min mean max\n0 0.8 18.5 42.0\n", encoding="utf-8")
            self.stdout = iter(["Executing functionObject yPlus\n", "End\n"])
        else:
            _write_openfoam_native_time_fields(case_dir)
            _write_openfoam_patch_metric_outputs(case_dir)
            self.stdout = iter(["Time = 0.001\n", "solver ok\n"])


class OpenFOAMOptionalYPlusFatalProcess(OpenFOAMNativeMeshSuccessProcess):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        self.exit_code = 0
        if "postProcess" in " ".join(command):
            self.exit_code = 1
            self.stdout = iter(
                [
                    "Executing functionObjects\n",
                    "--> FOAM FATAL ERROR:\n",
                    "wall distance field unavailable\n",
                ]
            )

    def wait(self) -> int:
        return self.exit_code


class OpenFOAMFoundationRuntimeProcess(OpenFOAMNativeMeshSuccessProcess):
    def __init__(self, command, **kwargs) -> None:
        if command == ["foamVersion"]:
            self.command = command
            self.kwargs = kwargs
            self.stdout = iter(["OpenFOAM-13\n"])
        else:
            super().__init__(command, **kwargs)


class OpenFOAMOpenCFDRuntimeProcess(OpenFOAMNativeMeshSuccessProcess):
    def __init__(self, command, **kwargs) -> None:
        if command == ["foamVersion"]:
            self.command = command
            self.kwargs = kwargs
            self.stdout = iter(["OpenFOAM-v2312 (OpenCFD Ltd.)\n"])
        else:
            super().__init__(command, **kwargs)


class OpenFOAMNativeMeshFailedCheckProcess(OpenFOAMNativeMeshSuccessProcess):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        joined = " ".join(command)
        if "checkMesh" in joined:
            self.stdout = iter(
                [
                    "Mesh stats\n",
                    "    cells:            39\n",
                    "    Max aspect ratio = 90.0\n",
                    "    Mesh non-orthogonality Max: 72.5 average: 18.0\n",
                    "    Max skewness = 4.2\n",
                    "    Min volume = -1e-12.\n",
                    "Failed 2 mesh checks.\n",
                ]
            )


class OpenFOAMNativeMeshMissingExpectedPatchProcess(OpenFOAMNativeMeshSuccessProcess):
    def __init__(self, command, **kwargs) -> None:
        super().__init__(command, **kwargs)
        joined = " ".join(command)
        if "snappyHexMesh" in joined:
            self.stdout = iter(
                [
                    "snappyHexMesh: castellatedMesh true\n",
                    "Patch reviewed_inlet generated from inlet.stl\n",
                    "Patch reviewed_outlet generated from outlet.stl\n",
                    "End\n",
                ]
            )
        elif "checkMesh" in joined:
            self.stdout = iter(
                [
                    "Mesh stats\n",
                    "    cells:            39\n",
                    "Boundary patches\n",
                    "    reviewed_inlet\n",
                    "    reviewed_outlet\n",
                    "    defaultFaces\n",
                    "    Max aspect ratio = 5 OK.\n",
                    "    Mesh non-orthogonality Max: 12.5 average: 3.25\n",
                    "    Max skewness = 0.42 OK.\n",
                    "    Min volume = 1e-09. Max volume = 1e-06.\n",
                    "Failed 0 mesh checks.\n",
                    "Mesh OK.\n",
                ]
            )


class OpenFOAMInvalidNumericProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.stdout = iter(
            [
                "Time = 0.001\n",
                "smoothSolver:  Solving for Ux, Initial residual = nan, Final residual = nan, No Iterations 1000\n",
                "--> FOAM FATAL IO ERROR:\n",
                "wrong token type - expected Scalar, found punctuation token '-'\n",
            ]
        )

    def wait(self) -> int:
        return 0


class OpenFOAMCheckMeshFailedProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.stdout = iter(
            [
                "FlowLab OpenFOAM mesh check: checkMesh -allGeometry -allTopology\n",
                "    points:           8\n",
                "    faces:            6\n",
                "    cells:            1\n",
                "    Max aspect ratio = 90.0\n",
                "    Mesh non-orthogonality Max: 72.5 average: 18.0\n",
                "Failed 1 mesh checks.\n",
                "End\n",
            ]
        )

    def wait(self) -> int:
        return 0


class FailingSU2Process(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.stdout = iter(["| 1 | -3.0 | -2.5 | 0.01 |\n", "| 2 | -3.8 | -3.2 | 0.005 |\n", "ERROR: linear solver failed\n"])

    def wait(self) -> int:
        return 9


class FailingCodeSaturneProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        run_dir = case_dir / "RESU" / "run-001"
        (run_dir / "monitoring").mkdir(parents=True, exist_ok=True)
        (run_dir / "listing").write_text(
            "First face with boundary condition definition error\n"
            "Fatal error: boundary condition type 0\n",
            encoding="utf-8",
        )
        (run_dir / "monitoring" / "residuals.dat").write_text(
            "Time residual\n0.1 4.2e-3\n",
            encoding="utf-8",
        )
        self.stdout = iter(["iteration 1 residual velocity 4.2e-3\n", "boundary setup failed\n"])

    def wait(self) -> int:
        return 1


class MuJoCoDiagnosticProcess(SuccessfulProcess):
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        case_dir = Path(kwargs["cwd"])
        (case_dir / "outputs").mkdir(parents=True, exist_ok=True)
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
        self.stdout = iter(["FlowLab MuJoCo sandbox completed 120 steps\n"])


class ProgressiveResultProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.release = threading.Event()
        self.stdout = self._stdout()
        self.terminated = False

    def _stdout(self):
        case_dir = Path(self.kwargs["cwd"])
        (case_dir / "postProcessing").mkdir(exist_ok=True)
        (case_dir / "postProcessing" / "field_running.vtk").write_text(
            """# vtk DataFile Version 3.0
progressive solver result
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
10
20
30
40
""",
            encoding="utf-8",
        )
        (case_dir / "postProcessing" / "probes" / "0").mkdir(parents=True, exist_ok=True)
        (case_dir / "postProcessing" / "probes" / "0" / "p").write_text("# Probe output without diagnostic extension\n", encoding="utf-8")
        (case_dir / "postProcessing" / "fieldExtents" / "0").mkdir(parents=True, exist_ok=True)
        (case_dir / "postProcessing" / "fieldExtents" / "0" / "fieldMinMax.dat").write_text(
            "Time field min max\n0.005 p 10 40\n",
            encoding="utf-8",
        )
        yield "Time = 0.005\n"
        yield "smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 7.5e-06, No Iterations 2\n"
        self.release.wait(2)

    def wait(self) -> int:
        self.release.wait(2)
        if not self.terminated:
            _write_openfoam_patch_metric_outputs(Path(self.kwargs["cwd"]))
        return -15 if self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True
        self.release.set()


def test_job_creates_runtime_case_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: SuccessfulProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    assert case_dir.is_dir()
    assert (case_dir / "README.md").read_text(encoding="utf-8") == "# Execution smoke\n"
    assert (case_dir / "system" / "controlDict").exists()


def test_validate_case_manifest_accepts_generated_manifest_and_rejects_tampering() -> None:
    case = _case()

    assert validate_case_manifest(case) == []

    case.files["0/U"] = "tampered\n"
    issues = validate_case_manifest(case)

    assert any("SHA-256 mismatch" in issue and "0/U" in issue for issue in issues)


def test_parse_solver_logs_extracts_openfoam_residuals_and_warnings() -> None:
    summary = parse_solver_logs(
        "openfoam",
        [
            "Mesh stats",
            "    points:           112",
            "    faces:            276",
            "    internal faces:   82",
            "    cells:            39",
            "    Max aspect ratio = 5 OK.",
            "    Mesh non-orthogonality Max: 12.5 average: 3.25",
            "    Max skewness = 0.42 OK.",
            "Failed 0 mesh checks.",
            "Mesh OK.",
            "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).",
            "Time = 0.001",
            "time step continuity errors : sum local = 1e-09, global = -2e-12, cumulative = -2e-12",
            "smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 7.5e-06, No Iterations 2",
            "cumulative continuity errors : 2e-12",
            "WARNING: Courant number adjusted",
        ],
    )

    assert summary["latestTime"] == 0.001
    assert summary["residuals"]["Ux"]["final"] == 7.5e-06
    assert summary["residuals"]["Ux"]["iterations"] == 2
    assert summary["checkMesh"] == {
        "maxAspectRatio": 5.0,
        "maxNonOrthogonality": 12.5,
        "averageNonOrthogonality": 3.25,
        "maxSkewness": 0.42,
        "failedChecks": 0,
        "completed": True,
        "passed": True,
        "counts": {"points": 112, "faces": 276, "internal_faces": 82, "cells": 39},
    }
    assert summary["warnings"] == ["WARNING: Courant number adjusted"]
    assert "errors" not in summary


def test_parse_solver_logs_extracts_openfoam_region_checkmesh_metrics() -> None:
    summary = parse_solver_logs(
        "openfoam",
        [
            "FlowLab OpenFOAM CHT region mesh check: fluid",
            "    points:           112",
            "    faces:            276",
            "    cells:            39",
            "    Max aspect ratio = 5 OK.",
            "    Mesh non-orthogonality Max: 12.5 average: 3.25",
            "    Max skewness = 0.42 OK.",
            "Failed 0 mesh checks.",
            "Mesh OK.",
            "checkMesh -region solid -allGeometry -allTopology",
            "    points:           224",
            "    faces:            552",
            "    cells:            78",
            "    Max aspect ratio = 8.5 OK.",
            "    Mesh non-orthogonality Max: 18 average: 4.5",
            "    Max skewness = 0.61 OK.",
            "Failed 0 mesh checks.",
            "Mesh OK.",
        ],
    )

    regions = summary["checkMeshRegions"]
    assert regions["fluid"]["passed"] is True
    assert regions["fluid"]["failedChecks"] == 0
    assert regions["fluid"]["counts"]["cells"] == 39
    assert regions["fluid"]["maxNonOrthogonality"] == 12.5
    assert regions["solid"]["passed"] is True
    assert regions["solid"]["counts"]["points"] == 224
    assert regions["solid"]["maxAspectRatio"] == 8.5
    assert regions["solid"]["averageNonOrthogonality"] == 4.5
    assert "checkMesh" not in summary


def test_parse_solver_logs_extracts_su2_code_saturne_and_mujoco_progress() -> None:
    su2 = parse_solver_logs("su2", ["| 1 | -3.0 | -2.0 | 0.01 |", "| 5 | -4.0 | -3.1 | 0.005 |"])
    code_saturne = parse_solver_logs("code-saturne", ["iteration 4 residual velocity 1.0e-3", "time step 7 residual pressure 5.0e-4"])
    mujoco = parse_solver_logs("mujoco", ["step=100", "steps: 250", "FlowLab MuJoCo sandbox completed 120 steps"])

    assert su2["latestIteration"] == 5
    assert su2["residuals"]["su2_primary"]["iterations"] == 5
    assert code_saturne["latestIteration"] == 7
    assert code_saturne["residuals"]["code_saturne_residual"]["final"] == 5.0e-4
    assert mujoco["iterations"] == [100, 250, 120]
    assert mujoco["latestIteration"] == 120


def test_job_result_includes_parsed_log_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMLogProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.result is not None
    assert finished.result["logSummary"]["latestTime"] == 0.002
    assert finished.result["logSummary"]["residuals"]["p"]["final"] == 9e-05
    assert finished.result["logSummary"]["checkMesh"]["passed"] is True
    assert finished.result["logSummary"]["checkMesh"]["counts"]["cells"] == 39
    assert "Courant" in finished.result["logSummary"]["warnings"][0]
    assert finished.result["solverLogPath"] == "postProcessing/solverLogs/solve.log"
    solve_log = next(
        diagnostic for diagnostic in finished.result["diagnosticFiles"] if diagnostic["path"] == "postProcessing/solverLogs/solve.log"
    )
    assert "GAMG:  Solving for p" in solve_log["text"]


def test_openfoam_exit_zero_with_nan_or_fatal_logs_fails_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMInvalidNumericProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.exitCode is None
    assert finished.error is not None
    assert "OpenFOAM" in finished.error
    assert finished.result is not None
    assert "errors" in finished.result["logSummary"]


def test_openfoam_exit_zero_with_failed_checkmesh_logs_fails_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMCheckMeshFailedProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.exitCode is None
    assert finished.error == "OpenFOAM checkMesh failed 1 check(s) before solver launch."
    assert finished.result is not None
    assert finished.result["logSummary"]["checkMesh"]["passed"] is False
    assert finished.result["logSummary"]["checkMesh"]["failedChecks"] == 1
    assert finished.result["logSummary"]["checkMesh"]["maxNonOrthogonality"] == 72.5
    assert "Failed 1 mesh checks" in "\n".join(finished.logs)


def test_openfoam_native_mesh_runner_captures_acceptance_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert commands[:5] == [
        ["surfaceFeatureExtract"],
        ["blockMesh"],
        ["snappyHexMesh", "-overwrite"],
        ["checkMesh", "-allGeometry", "-allTopology"],
        ["postProcess", "-func", "yPlus", "-latestTime"],
    ]
    assert commands[-1] == ["bash", "Allrun"]
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    acceptance = json.loads((case_dir / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    native_quality = acceptance["nativeQualityEvidence"]
    openfoam_report = native_quality["solverReports"]["openfoam"]
    assert native_quality["status"] == "openfoam-native-quality-passed"
    assert openfoam_report["status"] == "passed"
    assert openfoam_report["qualityMetrics"]["failedChecks"] == 0
    assert openfoam_report["qualityMetrics"]["maxNonOrthogonality"] == 12.5
    assert openfoam_report["qualityMetrics"]["maxSkewness"] == 0.42
    assert openfoam_report["qualityMetrics"]["maxAspectRatio"] == 5.0
    assert openfoam_report["qualityMetrics"]["minVolume"] == 1e-09
    assert openfoam_report["layerSummary"]["status"] == "present"
    assert openfoam_report["yPlusEvidence"]["status"] == "present"
    assert openfoam_report["yPlusEvidence"]["max"] == 42.0
    assert "log.checkMesh" in openfoam_report["currentEvidence"]
    assert "postProcessing/yPlus/0/yPlus.dat" in openfoam_report["currentEvidence"]
    assert acceptance["solverAcceptance"]["openfoam"]["status"] == "native-evidence-passed"
    assert acceptance["approvalStatus"] == "blocked"
    assert acceptance["productionReady"] is False
    handoff = json.loads((case_dir / "mesh/openfoam_snappy_handoff.json").read_text(encoding="utf-8"))
    assert handoff["starterGeometry"]["cadReviewed"] is False
    assert native_quality["productionReady"] is False
    assert any("CAD/B-rep reviewed" in reason for reason in acceptance["blockingReasons"])


def test_full_ogrid_native_mesh_path_uses_only_blockmesh_and_checkmesh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    project = {
        "name": "Full O-grid execution path",
        "fluid": {"density": 998.2, "dynamicViscosity": 0.001002},
        "solver": {
            "meshMode": "full-ogrid",
            "meshResolution": "coarse",
            "runMode": "steady",
            "turbulence": "laminar",
            "meshControls": {
                "fullOGridAxialCells": 16,
                "fullOGridAnnularRadialCells": 4,
                "fullOGridCircumferentialCells": 32,
                "fullOGridCoreCellsPerSide": 8,
            },
        },
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
                "length": 0.024,
                "shape": {"kind": "circular", "diameter": 0.006},
            }
        },
    }
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )
    case_dir = tmp_path / "case"
    execution.materialize_case_files(case, case_dir)

    assert execution._openfoam_case_is_full_ogrid(case_dir) is True
    assert execution._openfoam_case_is_axisymmetric_wedge(case_dir) is False
    assert execution._openfoam_required_mesh_commands(case_dir) == ["blockMesh", "checkMesh"]


def test_openfoam_generated_fitted_mesh_skips_snappy_and_solves_starter_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_add_flowlab_generated_base_mesh(_case()))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert ["snappyHexMesh", "-overwrite"] not in commands
    assert ["blockMesh"] not in commands
    assert commands[:3] == [
        ["surfaceFeatureExtract"],
        ["checkMesh", "-allGeometry", "-allTopology"],
        ["postProcess", "-func", "yPlus", "-latestTime"],
    ]
    assert commands[-1] == ["bash", "Allrun"]
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    acceptance = json.loads((case_dir / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    snappy_runs = [run for run in report["commandRuns"] if run["command"] == "snappyHexMesh -overwrite"]
    assert snappy_runs == [
        {
            "command": "snappyHexMesh -overwrite",
            "execution": "native",
            "required": False,
            "status": "skipped",
            "exitCode": None,
            "logPath": None,
            "reason": "Skipped for FlowLab-generated fitted starter polyMesh with empty front/back patches; production reviewed STL meshing still requires snappyHexMesh evidence.",
        }
    ]
    assert report["qualityMetrics"]["passed"] is True
    assert report["qualityMetrics"]["failedChecks"] == 0
    assert acceptance["approvalStatus"] == "blocked"
    assert acceptance["productionReady"] is False
    assert any("CAD/B-rep reviewed" in reason for reason in acceptance["blockingReasons"])
    assert finished.result is not None
    assert finished.result["resultFiles"]
    assert finished.result["diagnosticsAcceptance"]["status"] == "complete"


def test_openfoam_optional_yplus_fatal_log_does_not_fail_solver_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMOptionalYPlusFatalProcess(command, **kwargs))

    job = manager.queue_job(_add_flowlab_generated_base_mesh(_case()))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.error is None
    assert finished.caseDir is not None
    assert "--> FOAM FATAL ERROR:" not in finished.logs
    assert "OpenFOAM optional mesh command `postProcess -func yPlus -latestTime` exited with code 1" in "\n".join(finished.logs)
    assert "errors" not in finished.result["logSummary"]
    yplus_log = Path(finished.caseDir) / "log.yPlus"
    assert "--> FOAM FATAL ERROR:" in yplus_log.read_text(encoding="utf-8")


def test_openfoam_foundation_runtime_detection_uses_system_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(
        adapters,
        "_command_exists",
        lambda command: command in {"foamRun", "foamVersion", "surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh", "postProcess"},
    )
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMFoundationRuntimeProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    runtime = json.loads((case_dir / "constant/flowlab_openfoam_runtime.json").read_text(encoding="utf-8"))
    assert runtime["detectedStyle"] == "foundation"
    assert runtime["detectedVersion"] == "13"
    assert runtime["controlDictAdaptation"] == 'functions block replaced with #include "functions"'
    assert runtime["functionObjectAdaptation"] == "patchFlowRate and patchAverage replaced with per-patch surfaceFieldValue objects"
    assert '#include "functions"' in (case_dir / "system/controlDict").read_text(encoding="utf-8")
    functions_text = (case_dir / "system/functions").read_text(encoding="utf-8")
    assert "patchFlowRate_inlet" in functions_text
    assert "patchFlowRate_outlet" in functions_text
    assert "patchAverage_inlet" in functions_text
    assert "patchAverage_outlet" in functions_text
    assert "type            surfaceFieldValue;" in functions_text
    assert "type            patchFlowRate;" not in functions_text


def test_openfoam_opencfd_runtime_detection_keeps_inline_control_dict_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(
        adapters,
        "_command_exists",
        lambda command: command in {"foamRun", "foamVersion", "surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh", "postProcess"},
    )
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: OpenFOAMOpenCFDRuntimeProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    runtime = json.loads((case_dir / "constant/flowlab_openfoam_runtime.json").read_text(encoding="utf-8"))
    control_dict = (case_dir / "system/controlDict").read_text(encoding="utf-8")
    assert runtime["detectedStyle"] == "opencfd"
    assert runtime["detectedVersion"] == "v2312"
    assert runtime["controlDictAdaptation"] == "none"
    assert "functions\n{" in control_dict
    assert '#include "functions"' not in control_dict
    assert "patchFlowRate" in control_dict


def test_reviewed_stl_with_passing_native_evidence_becomes_production_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_mark_case_as_reviewed_stl(_case()))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert commands[-1] == ["bash", "Allrun"]
    assert finished.caseDir is not None
    acceptance = json.loads((Path(finished.caseDir) / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    handoff = json.loads((Path(finished.caseDir) / "mesh/openfoam_snappy_handoff.json").read_text(encoding="utf-8"))
    criteria = {criterion["id"]: criterion["status"] for criterion in acceptance["acceptanceCriteria"]}
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    assert handoff["starterGeometry"]["stlMetadata"]["triangleCount"] == 1
    assert handoff["reviewedBoundaryTags"]["complete"] is True
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-boundary-tags"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == ["reviewed_outlet"]
    assert handoff["boundaryPatchPlan"]["walls"] == ["reviewed_wall"]
    assert acceptance["productionReady"] is True
    assert acceptance["approvalStatus"] == "approved"
    assert acceptance["blockingReasons"] == []
    assert acceptance["nativeQualityEvidence"]["productionReady"] is True
    assert report["status"] == "passed"
    assert criteria["cad-geometry-source"] == "pass"
    assert criteria["native-3d-volume-mesh"] == "pass"
    assert criteria["boundary-layer-prism-mesh"] == "pass"
    assert criteria["adapted-refinement-evidence"] == "pass"
    assert criteria["solver-native-quality-evidence"] == "pass"


def test_reviewed_stl_missing_boundary_tags_blocks_production_ready_even_with_native_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_mark_case_as_reviewed_stl(_case(), complete_boundary_tags=False))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert commands[-1] == ["bash", "Allrun"]
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    handoff = json.loads((case_dir / "mesh/openfoam_snappy_handoff.json").read_text(encoding="utf-8"))
    acceptance = json.loads((case_dir / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    assert handoff["reviewedBoundaryTags"]["status"] == "fail"
    assert handoff["reviewedBoundaryTags"]["complete"] is False
    assert handoff["reviewedBoundaryTags"]["missingRequiredRoles"] == ["outlet", "wall"]
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-boundary-tags"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == []
    assert handoff["boundaryPatchPlan"]["walls"] == []
    assert report["status"] == "passed"
    assert acceptance["nativeQualityEvidence"]["status"] == "openfoam-native-quality-passed"
    assert acceptance["approvalStatus"] == "blocked"
    assert acceptance["productionReady"] is False
    assert any("boundary" in reason.lower() and "tag" in reason.lower() for reason in acceptance["blockingReasons"])


def test_multi_surface_missing_required_reviewed_surface_blocks_production_ready_even_with_native_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(
        _mark_case_as_multi_surface_reviewed_stl(
            _case(), include_wall=False, include_boundary_conditions=True
        )
    )
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status in {"complete", "blocked"}
    if finished.status == "complete":
        assert commands[-1] == ["bash", "Allrun"]
    else:
        assert ["bash", "Allrun"] not in commands
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    handoff = json.loads((case_dir / "mesh/openfoam_snappy_handoff.json").read_text(encoding="utf-8"))
    assert handoff["reviewedGeometry"]["sourceType"] == "multi-surface-stl"
    assert handoff["boundaryCoverage"]["status"] == "fail"
    assert handoff["boundaryCoverage"]["missingRequiredRoles"] == ["wall"]
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-surfaces"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == ["reviewed_outlet"]
    assert handoff["boundaryPatchPlan"]["walls"] == []
    if finished.status == "complete":
        acceptance = json.loads((case_dir / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
        assert acceptance["nativeQualityEvidence"]["status"] == "openfoam-native-quality-passed"
        assert acceptance["approvalStatus"] == "blocked"
        assert acceptance["productionReady"] is False
        assert any("wall" in reason.lower() and "surface" in reason.lower() for reason in acceptance["blockingReasons"])
    else:
        assert any("wall" in issue.lower() and "surface" in issue.lower() for issue in finished.logs)


def test_multi_surface_missing_boundary_conditions_blocks_before_solve_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshSuccessProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_mark_case_as_multi_surface_reviewed_stl(_case()))
    finished = _wait_for(manager, job.id, {"failed", "blocked"})

    assert finished is not None
    assert finished.status in {"failed", "blocked"}
    assert ["bash", "Allrun"] not in commands
    validation_text = "\n".join([finished.error or "", *finished.logs])
    assert "openfoam field" in validation_text.lower()
    assert "missing reviewed surface patch" in validation_text.lower()
    assert "reviewed_inlet" in validation_text
    assert "reviewed_outlet" in validation_text
    assert "reviewed_walls" in validation_text
    assert finished.caseDir is not None
    handoff = json.loads(
        (Path(finished.caseDir) / "mesh/openfoam_snappy_handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["boundaryConditionCoverage"]["status"] == "fail"
    assert handoff["boundaryConditionCoverage"]["missingPatchNames"] == [
        "reviewed_inlet",
        "reviewed_outlet",
        "reviewed_walls",
    ]


def test_multi_surface_missing_expected_patch_in_native_logs_blocks_before_solve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshMissingExpectedPatchProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_mark_case_as_multi_surface_reviewed_stl(_case(), include_boundary_conditions=True))
    finished = _wait_for(manager, job.id, {"failed", "blocked"})

    assert finished is not None
    assert finished.status in {"failed", "blocked"}
    assert ["bash", "Allrun"] not in commands
    assert finished.error is not None
    assert "reviewed_walls" in finished.error
    assert finished.caseDir is not None
    case_dir = Path(finished.caseDir)
    acceptance = json.loads((case_dir / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    assert report["status"] == "blocked"
    assert "reviewed_walls" in " ".join(report["blockingReasons"])
    assert acceptance["approvalStatus"] == "blocked"
    assert acceptance["productionReady"] is False


def test_openfoam_failed_native_checkmesh_blocks_before_solver_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return OpenFOAMNativeMeshFailedCheckProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.error == "OpenFOAM checkMesh failed 2 check(s) before solver launch."
    assert ["bash", "Allrun"] not in commands
    assert finished.caseDir is not None
    acceptance = json.loads((Path(finished.caseDir) / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    assert report["status"] == "blocked"
    assert report["qualityMetrics"]["failedChecks"] == 2
    assert report["qualityMetrics"]["minVolume"] == -1e-12
    assert "OpenFOAM checkMesh failed 2 check(s)." in report["blockingReasons"]
    assert acceptance["approvalStatus"] == "blocked"
    assert acceptance["productionReady"] is False


def test_openfoam_missing_native_mesh_commands_block_before_process_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "foamRun")
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"blocked"})

    assert finished is not None
    assert finished.status == "blocked"
    assert commands == []
    assert finished.error is not None
    assert "Missing OpenFOAM native mesh command `surfaceFeatureExtract`" in finished.error
    assert "Missing OpenFOAM native mesh command `snappyHexMesh`" in finished.error
    assert "Missing OpenFOAM native mesh command `checkMesh`" in finished.error
    assert finished.caseDir is not None
    acceptance = json.loads((Path(finished.caseDir) / "mesh/production_mesh_acceptance.json").read_text(encoding="utf-8"))
    report = acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]
    assert report["status"] == "blocked"
    assert {run["command"] for run in report["commandRuns"]} >= {"surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh"}
    assert all(run["status"] == "missing-command" for run in report["commandRuns"])
    assert acceptance["approvalStatus"] == "blocked"


@pytest.mark.parametrize(
    ("source_type", "stl_path", "stl_text", "expected_issue"),
    [
        ("local-stl-path", "../unsafe.stl", _reviewed_stl(), "safe relative .stl path"),
        ("local-stl-path", "geometry/not-stl.txt", _reviewed_stl(), "safe relative .stl path"),
        ("uploaded-stl", None, "solid bad\nendsolid bad\n", "sane ASCII STL"),
    ],
)
def test_reviewed_stl_validation_blocks_unsafe_or_malformed_case(
    source_type: str,
    stl_path: str | None,
    stl_text: str,
    expected_issue: str,
) -> None:
    case = _mark_case_as_reviewed_stl(_case())
    handoff = json.loads(case.files["mesh/openfoam_snappy_handoff.json"])
    handoff["starterGeometry"]["sourceType"] = source_type
    if stl_path is None:
        handoff["starterGeometry"].pop("stlPath", None)
    else:
        handoff["starterGeometry"]["stlPath"] = stl_path
    case.files["mesh/openfoam_snappy_handoff.json"] = json.dumps(handoff) + "\n"
    case.files["constant/triSurface/reviewedFlowLabSurfaces.stl"] = stl_text
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any(expected_issue in issue for issue in issues)


def test_failed_job_result_includes_parsed_log_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: FailingSU2Process(command, **kwargs))

    job = manager.queue_job(_case("su2"))
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.result is not None
    assert finished.result["logSummary"]["latestIteration"] == 2
    assert finished.result["logSummary"]["errors"] == ["ERROR: linear solver failed"]


def test_failed_code_saturne_job_collects_resu_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: FailingCodeSaturneProcess(command, **kwargs))

    job = manager.queue_job(_case("code-saturne"))
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.result is not None
    assert [diagnostic["path"] for diagnostic in finished.result["diagnosticFiles"]] == [
        "RESU/run-001/listing",
        "RESU/run-001/monitoring/residuals.dat",
    ]
    assert finished.result["diagnosticSummary"][0]["kind"] == "code-saturne-error"
    assert "boundary condition type 0" in finished.result["diagnosticSummary"][0]["excerpts"][1]
    assert finished.result["diagnosticSummary"][1]["kind"] == "residuals"
    assert finished.result["diagnosticSummary"][1]["latest"] == {"Time": 0.1, "residual": 4.2e-3}


def test_collect_diagnostic_files_reads_small_postprocessing_text(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "postProcessing" / "forces" / "0").mkdir(parents=True)
    (case_dir / "postProcessing" / "forces" / "0" / "forces.dat").write_text("Time Fx Fy Fz\n0 1 2 3\n", encoding="utf-8")
    (case_dir / "mesh").mkdir()
    (case_dir / "mesh" / "ignored.dat").write_text("mesh metadata", encoding="utf-8")

    diagnostics = collect_diagnostic_files(case_dir)

    assert diagnostics == [
        {
            "path": "postProcessing/forces/0/forces.dat",
            "size": len("Time Fx Fy Fz\n0 1 2 3\n"),
            "text": "Time Fx Fy Fz\n0 1 2 3\n",
        }
    ]


def test_collect_diagnostic_files_reads_code_saturne_resu_text(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    run_dir = case_dir / "RESU" / "20260612-121212"
    run_dir.mkdir(parents=True)
    (run_dir / "compile.log").write_text("Compiling cs_user_boundary_conditions.f90\n", encoding="utf-8")
    (run_dir / "preprocessor.log").write_text("Mesh checked: 12 cells\n", encoding="utf-8")
    (run_dir / "listing").write_text(
        "First face with boundary condition definition error\n"
        "Fatal error: boundary condition type 0\n",
        encoding="utf-8",
    )
    (run_dir / "error").write_text("boundary condition type 0\n", encoding="utf-8")
    (run_dir / "mesh_input").write_bytes(b"\x80\x81binary mesh")

    diagnostics = collect_diagnostic_files(case_dir)

    assert [diagnostic["path"] for diagnostic in diagnostics] == [
        "RESU/20260612-121212/compile.log",
        "RESU/20260612-121212/error",
        "RESU/20260612-121212/listing",
        "RESU/20260612-121212/preprocessor.log",
    ]
    assert "boundary condition type 0" in diagnostics[1]["text"]


def test_collect_diagnostic_files_reads_mujoco_output_summary(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    outputs = case_dir / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "summary.json").write_text('{"solver":"mujoco","steps":120,"final":{"step":119,"time":0.24}}\n', encoding="utf-8")
    (outputs / "raw.bin").write_bytes(b"\x80\x81binary")

    diagnostics = collect_diagnostic_files(case_dir)

    assert diagnostics == [
        {
            "path": "outputs/summary.json",
            "size": len('{"solver":"mujoco","steps":120,"final":{"step":119,"time":0.24}}\n'),
            "text": '{"solver":"mujoco","steps":120,"final":{"step":119,"time":0.24}}\n',
        }
    ]


def test_parse_diagnostic_files_extracts_mujoco_summary_json() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "outputs/summary.json",
                "size": 1,
                "text": """{
  "solver": "mujoco",
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
            }
        ]
    )

    assert summaries == [
        {
            "path": "outputs/summary.json",
            "kind": "mujoco-summary",
            "lineCount": 12,
            "columns": ["steps", "step", "time", "passiveForceNorm", "position0", "position1", "position2", "velocity0", "velocity1", "velocity2"],
            "rowCount": 1,
            "latest": {
                "steps": 120.0,
                "step": 119.0,
                "time": 0.24,
                "passiveForceNorm": 0.42,
                "position0": 0.01,
                "position1": 0.0,
                "position2": 0.0,
                "velocity0": 1.25,
                "velocity1": 0.0,
                "velocity2": 0.0,
            },
            "excerpts": ["MuJoCo fluid forces are phenomenological rigid-body forces, not CFD field solves."],
        }
    ]


def test_parse_diagnostic_files_extracts_latest_numeric_table_rows() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "postProcessing/residuals/0/residuals.dat",
                "size": 48,
                "text": "# Time Ux p\n0.001 7.5e-06 9e-05\n0.002 4e-06 3e-05\n",
            },
            {
                "path": "postProcessing/forces/0/forces.dat",
                "size": 32,
                "text": "Time Fx Fy Fz\n0 1 2 3\n",
            },
        ]
    )

    assert summaries[0]["kind"] == "residuals"
    assert summaries[0]["rowCount"] == 2
    assert summaries[0]["latest"] == {"Time": 0.002, "Ux": 4e-06, "p": 3e-05}
    assert summaries[1]["kind"] == "forces"
    assert summaries[1]["latest"]["Fz"] == 3.0


def test_parse_diagnostic_files_skips_openfoam_comment_titles_before_table_header() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "postProcessing/residuals/0/residuals.dat",
                "size": 180,
                "text": (
                    "# Residuals     \n"
                    "# Time          \tUx              \tUy              \tp               \n"
                    "0               \tN/A\tN/A\n"
                    "0.049           \t1.02061011e-02\t1.05371157e-02\t6.18736364e-04\n"
                    "0.05            \t9.99884917e-03\t1.03236906e-02\t6.19983648e-04\n"
                ),
            }
        ]
    )

    assert summaries == [
        {
            "path": "postProcessing/residuals/0/residuals.dat",
            "kind": "residuals",
            "columns": ["Time", "Ux", "Uy", "p"],
            "rowCount": 2,
            "latest": {
                "Time": 0.05,
                "Ux": 9.99884917e-03,
                "Uy": 1.03236906e-02,
                "p": 6.19983648e-04,
            },
        }
    ]


def test_parse_diagnostic_files_summarizes_code_saturne_boundary_errors() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "RESU/20260612-121212/listing",
                "size": 1024,
                "text": (
                    "Solver log\n"
                    "First face with boundary condition definition error\n"
                    "Fatal error: boundary condition type 0\n"
                ),
            }
        ]
    )

    assert summaries == [
        {
            "path": "RESU/20260612-121212/listing",
            "kind": "code-saturne-error",
            "lineCount": 3,
            "excerpts": [
                "First face with boundary condition definition error",
                "Fatal error: boundary condition type 0",
            ],
        }
    ]


def test_parse_diagnostic_files_summarizes_openfoam_nested_forces() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "postProcessing/wallForces/0/forces.dat",
                "size": 512,
                "text": (
                    "# Forces\n"
                    "# Time forces(pressure viscous) moments(pressure viscous)\n"
                    "0.05 ((0.00000000e+00 -1.26156687e-10 0.00000000e+00) "
                    "(2.18085968e-03 -1.44492408e-16 0.00000000e+00)) "
                    "((6.30783435e-13 0.00000000e+00 -4.92730878e-11) "
                    "(7.22462036e-19 1.09042984e-05 -2.26122222e-16))\n"
                ),
            }
        ]
    )

    assert summaries == [
        {
            "path": "postProcessing/wallForces/0/forces.dat",
            "kind": "forces",
            "columns": [
                "Time",
                "pressureFx",
                "pressureFy",
                "pressureFz",
                "viscousFx",
                "viscousFy",
                "viscousFz",
                "pressureMx",
                "pressureMy",
                "pressureMz",
                "viscousMx",
                "viscousMy",
                "viscousMz",
            ],
            "rowCount": 1,
            "latest": {
                "Time": 0.05,
                "pressureFx": 0.0,
                "pressureFy": -1.26156687e-10,
                "pressureFz": 0.0,
                "viscousFx": 2.18085968e-03,
                "viscousFy": -1.44492408e-16,
                "viscousFz": 0.0,
                "pressureMx": 6.30783435e-13,
                "pressureMy": 0.0,
                "pressureMz": -4.92730878e-11,
                "viscousMx": 7.22462036e-19,
                "viscousMy": 1.09042984e-05,
                "viscousMz": -2.26122222e-16,
            },
        }
    ]


def test_collect_patch_metrics_summarizes_openfoam_postprocessing(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    _write_openfoam_patch_metric_outputs(case_dir)

    metrics = collect_patch_metrics(case_dir)

    assert metrics["schema"] == "flowlab.patch_metrics.v1"
    assert metrics["status"] == "complete"
    assert metrics["warnings"] == []
    assert metrics["flowBalance"] == {
        "inletFlow": 0.012,
        "outletFlow": 0.0118,
        "imbalance": pytest.approx(-0.0002),
        "relativeImbalance": pytest.approx(0.0166666667),
        "unit": "m3/s",
        "inletPatches": ["inlet"],
        "outletPatches": ["outlet"],
    }
    assert metrics["pressureDrops"] == [
        {
            "fromPatch": "inlet",
            "toPatch": "outlet",
            "inletPressure": 101325.0,
            "outletPressure": 99000.0,
            "deltaP": 2325.0,
            "unit": "Pa",
        }
    ]
    patches = metrics["patches"]
    assert patches["walls"]["wallShear"] == {
        "min": 0.4,
        "mean": 1.1,
        "max": 2.8,
        "unit": "Pa",
        "time": 0.1,
        "path": "postProcessing/wallShearStress/0/wallShearStress.dat",
    }
    force = metrics["forces"][0]
    assert force["patchName"] == "walls"
    assert force["force"] == {"x": 1.1, "y": 2.2, "z": 3.3}
    assert force["moment"] == {"x": 4.4, "y": 5.5, "z": 6.6}
    assert force["forceMagnitude"] == pytest.approx(math.sqrt(1.1**2 + 2.2**2 + 3.3**2))
    assert metrics["pressureProbes"][0]["pressureSpan"] == 2325.0


def test_collect_patch_metrics_parses_foundation_vector_wall_shear_extrema(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "case"
    shear_dir = case_dir / "postProcessing" / "wallShearStress" / "0"
    shear_dir.mkdir(parents=True)
    (shear_dir / "wallShearStress.dat").write_text(
        "# Wall shear stress\n"
        "# Time patch min max\n"
        "0 walls (-5.75571294e-05 -1.11865096e-05 -7.45767274e-06) "
        "(-3.95989057e-05 1.11865096e-05 7.45767274e-06)\n"
        "3000 walls (-1.11153591e-05 -1.52194535e-05 -3.66447540e-06) "
        "(3.27772359e-06 2.22108282e-08 3.66447538e-06)\n",
        encoding="utf-8",
    )

    metrics = collect_patch_metrics(case_dir)

    source = next(
        row
        for row in metrics["sources"]
        if row["kind"] == "wall-shear"
    )
    assert source["status"] == "parsed"
    wall_shear = metrics["patches"]["walls"]["wallShear"]
    assert wall_shear["time"] == 3000.0
    assert wall_shear["componentMinimumVector"] == pytest.approx(
        [-1.11153591e-05, -1.52194535e-05, -3.66447540e-06]
    )
    assert wall_shear["componentMaximumVector"] == pytest.approx(
        [3.27772359e-06, 2.22108282e-08, 3.66447538e-06]
    )
    assert wall_shear["aggregation"] == (
        "componentwise-extrema-not-pointwise-magnitude"
    )
    assert not any(
        "wallShearStress output is missing" in warning
        for warning in metrics["warnings"]
    )


def test_collect_patch_metrics_builds_pressure_drop_from_per_patch_surface_field_value(tmp_path: Path) -> None:
    # Regression: FlowLab writes per-patch `patchAverage_<patch>/surfaceFieldValue.dat`
    # (not a single `patchAverage/p.dat`). The numeric-table parser drops the real
    # `areaAverage(p)` column header (returning a generic `c1`), so the collector must
    # recover the pressure field from the header line to populate averagePressure and
    # build pressureDrops -- otherwise a fully converged run reports no pressure drop.
    case_dir = tmp_path / "case"
    pp = case_dir / "postProcessing"
    for patch, flow, pressure in (("inlet", -0.018165, 6.06438719), ("outlet", 0.018165, 0.0)):
        flow_dir = pp / f"patchFlowRate_{patch}" / "0"
        flow_dir.mkdir(parents=True)
        (flow_dir / "surfaceFieldValue.dat").write_text(
            f"# Selection type : patch {patch}\n# Faces  : 17\n"
            f"# Time          \tsum(phi)\n0               \t0.0\n2000            \t{flow}\n",
            encoding="utf-8",
        )
        avg_dir = pp / f"patchAverage_{patch}" / "0"
        avg_dir.mkdir(parents=True)
        (avg_dir / "surfaceFieldValue.dat").write_text(
            f"# Selection type : patch {patch}\n# Faces  : 17\n# Area   : 1.8165e-02\n"
            f"# Time          \tareaAverage(p)\n0               \t0.0\n2000            \t{pressure}\n",
            encoding="utf-8",
        )

    metrics = collect_patch_metrics(case_dir)

    patches = metrics["patches"]
    assert patches["inlet"]["averagePressure"]["value"] == pytest.approx(6.06438719)
    assert patches["inlet"]["averagePressure"]["field"] == "p"
    assert patches["outlet"]["averagePressure"]["value"] == pytest.approx(0.0)
    assert metrics["pressureDrops"] == [
        {
            "fromPatch": "inlet",
            "toPatch": "outlet",
            "inletPressure": pytest.approx(6.06438719),
            "outletPressure": pytest.approx(0.0),
            "deltaP": pytest.approx(6.06438719),
            "unit": "Pa",
        }
    ]


def test_collect_patch_metrics_warns_for_malformed_and_missing_outputs(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    bad_dir = case_dir / "postProcessing" / "patchFlowRate" / "0"
    bad_dir.mkdir(parents=True)
    (bad_dir / "patchFlowRate.dat").write_text("not a numeric table\n", encoding="utf-8")

    metrics = collect_patch_metrics(case_dir)

    assert metrics["status"] == "unparsed"
    assert metrics["flowBalance"] is None
    assert any("no parseable numeric table rows" in warning for warning in metrics["warnings"])
    assert any("patchAverage pressure output is missing" in warning for warning in metrics["warnings"])
    assert any(source["status"] == "unparsed" for source in metrics["sources"])


def test_parse_diagnostic_files_ignores_benign_code_saturne_listing() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "RESU/20260612-121212/listing",
                "size": 1024,
                "text": (
                    "No error detected during the data verification\n"
                    "** BOUNDARY CONDITIONS FOR SMOOTH WALLS\n"
                    "Calculation completed normally\n"
                ),
            },
            {
                "path": "RESU/20260612-121212/run_solver.log",
                "size": 1024,
                "text": (
                    "The Code_Saturne CFD tool is free software;\n"
                    "101320 101330 101320 101320\n"
                ),
            },
        ]
    )

    assert summaries == []


def test_parse_diagnostic_files_summarizes_su2_history_csv() -> None:
    summaries = parse_diagnostic_files(
        [
            {
                "path": "history.csv",
                "size": 128,
                "text": (
                    '"Time_Iter","Outer_Iter","Inner_Iter","rms[P]","rms[U]"\n'
                    "0,0,0,-32,-32\n"
                    "0,0,5,-31.5,-30.25\n"
                ),
            }
        ]
    )

    assert summaries == [
        {
            "path": "history.csv",
            "kind": "residuals",
            "columns": ["Time_Iter", "Outer_Iter", "Inner_Iter", "rms[P]", "rms[U]"],
            "rowCount": 2,
            "latest": {
                "Time_Iter": 0.0,
                "Outer_Iter": 0.0,
                "Inner_Iter": 5.0,
                "rms[P]": -31.5,
                "rms[U]": -30.25,
            },
        }
    ]


def test_running_job_exposes_progressive_result_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = ProgressiveResultProcess
    process_instance: ProgressiveResultProcess | None = None

    def fake_popen(command, **kwargs):
        nonlocal process_instance
        if command == ["bash", "Allrun"]:
            process_instance = process(command, **kwargs)
            return process_instance
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    running = _wait_until(
        manager,
        job.id,
        lambda current: current.status == "running"
        and current.result is not None
        and current.result.get("progressive") is True
        and current.result.get("resultFiles")
        and current.result.get("logSummary", {}).get("residuals", {}).get("Ux", {}).get("final") == 7.5e-06,
    )

    assert running is not None
    assert running.status == "running"
    assert running.result is not None
    assert running.result["progressive"] is True
    assert running.result["resultFiles"][0]["path"] == "postProcessing/field_running.vtk"
    assert running.result["diagnosticFiles"][0]["path"] == "postProcessing/fieldExtents/0/fieldMinMax.dat"
    assert running.result["diagnosticSummary"][0]["kind"] == "field-min-max"
    assert running.result["diagnosticSummary"][0]["latest"]["max"] == 40.0
    assert running.result["logSummary"]["latestTime"] == 0.005
    assert running.result["logSummary"]["residuals"]["Ux"]["final"] == 7.5e-06

    assert process_instance is not None
    process_instance.release.set()
    finished = _wait_for(manager, job.id, {"complete"})
    assert finished is not None
    assert finished.result is not None
    assert finished.result["progressive"] is False


def test_job_collects_vtk_result_files_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: ResultWritingProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.result is not None
    assert finished.result["resultFiles"][0]["path"] == "postProcessing/field_0001.vtk"
    assert "POINT_DATA 4" in finished.result["resultFiles"][0]["text"]
    field_summary = finished.result["resultFiles"][0]["fieldSummary"]
    assert field_summary["schema"] == "flowlab.result_field_summary.v1"
    assert field_summary["pointCount"] == 4
    assert field_summary["cellCount"] == 1
    assert field_summary["fields"][0]["name"] == "pressure"
    assert field_summary["fields"][0]["location"] == "point"
    assert field_summary["fields"][0]["kind"] == "scalar"
    assert field_summary["fields"][0]["mean"] == 2.5
    assert field_summary["fields"][0]["stdDev"] == pytest.approx(1.11803398875)
    assert field_summary["fields"][0]["p50"] == pytest.approx(2.5)
    assert field_summary["fields"][0]["p95"] == pytest.approx(3.85)
    residual_file = next(item for item in finished.result["diagnosticFiles"] if item["path"] == "postProcessing/residuals/0/residuals.dat")
    assert "Ux p" in residual_file["text"]
    residual_summary = next(item for item in finished.result["diagnosticSummary"] if item["path"] == "postProcessing/residuals/0/residuals.dat")
    assert residual_summary["latest"]["p"] == 9e-05
    assert any("Collected 1 VTK/VTU result" in line for line in finished.logs)
    assert any("solver diagnostic file" in line for line in finished.logs)


def test_openfoam_job_result_includes_patch_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: PatchMetricWritingProcess(command, **kwargs))

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.result is not None
    metrics = finished.result["patchMetrics"]
    assert metrics["status"] == "complete"
    assert metrics["flowBalance"]["inletFlow"] == 0.012
    assert metrics["pressureDrops"][0]["deltaP"] == 2325.0
    assert metrics["patches"]["walls"]["wallShear"]["mean"] == 1.1
    assert metrics["forces"][0]["forceMagnitude"] == pytest.approx(math.sqrt(1.1**2 + 2.2**2 + 3.3**2))


def test_openfoam_diagnostics_acceptance_artifact_records_required_outputs(tmp_path: Path) -> None:
    case = _case()
    case_dir = tmp_path / "case"
    for relative_path, text in case.files.items():
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _write_openfoam_patch_metric_outputs(case_dir)

    acceptance = write_openfoam_diagnostics_acceptance(case_dir, exit_code=0, mode="incompressible-navier-stokes")

    assert acceptance["schema"] == "flowlab.openfoam_diagnostics_acceptance.v1"
    assert acceptance["status"] == "complete"
    assert acceptance["completionGate"]["status"] == "pass"
    assert acceptance["observedOutputs"]["patch-flow-rate"] == ["postProcessing/patchFlowRate/0/patchFlowRate.dat"]
    assert acceptance["observedOutputs"]["patch-average"] == ["postProcessing/patchAverage/0/p.dat"]
    assert acceptance["observedOutputs"]["wall-shear"] == ["postProcessing/wallShearStress/0/wallShearStress.dat"]
    assert acceptance["observedOutputs"]["forces"] == ["postProcessing/wallForces/0/forces.dat"]
    assert read_openfoam_diagnostics_acceptance(case_dir)["status"] == "complete"


def test_openfoam_diagnostics_acceptance_accepts_foundation_flow_rate_patch_output(tmp_path: Path) -> None:
    case = _case()
    case_dir = tmp_path / "case"
    for relative_path, text in case.files.items():
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _write_openfoam_patch_metric_outputs(case_dir)
    (case_dir / "postProcessing" / "patchFlowRate").rename(case_dir / "postProcessing" / "flowRatePatch")

    acceptance = write_openfoam_diagnostics_acceptance(case_dir, exit_code=0, mode="incompressible-navier-stokes")

    assert acceptance["status"] == "complete"
    assert acceptance["completionGate"]["status"] == "pass"
    assert acceptance["observedOutputs"]["patch-flow-rate"] == ["postProcessing/flowRatePatch/0/patchFlowRate.dat"]
    assert acceptance["patchMetrics"]["flowBalance"]["inletPatches"] == ["inlet"]
    assert acceptance["patchMetrics"]["flowBalance"]["outletPatches"] == ["outlet"]


def test_openfoam_diagnostics_acceptance_accepts_wall_shear_patch_vtk_output(tmp_path: Path) -> None:
    case = _case()
    case_dir = tmp_path / "case"
    for relative_path, text in case.files.items():
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _write_openfoam_patch_metric_outputs(case_dir)
    shear_root = case_dir / "postProcessing" / "wallShearStress"
    for path in sorted(shear_root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    shear_root.rmdir()
    vtk_path = case_dir / "VTK" / "walls" / "walls_50.vtk"
    vtk_path.parent.mkdir(parents=True, exist_ok=True)
    vtk_path.write_text(
        """# vtk DataFile Version 3.0
wall shear patch
ASCII
DATASET POLYDATA
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
POLYGONS 1 5
4 0 1 2 3
CELL_DATA 1
FIELD attributes 1
wallShearStress 3 1 float
0 3 4
""",
        encoding="utf-8",
    )

    acceptance = write_openfoam_diagnostics_acceptance(case_dir, exit_code=0, mode="incompressible-navier-stokes")

    assert acceptance["status"] == "complete"
    assert acceptance["completionGate"]["status"] == "pass"
    assert acceptance["observedOutputs"]["wall-shear"] == ["VTK/walls/walls_50.vtk"]
    wall_shear = acceptance["patchMetrics"]["patches"]["walls"]["wallShear"]
    assert wall_shear["min"] == 5.0
    assert wall_shear["mean"] == 5.0
    assert wall_shear["max"] == 5.0


def test_openfoam_exit_zero_missing_patch_diagnostics_fails_completion_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)

    def fake_popen(command, **kwargs):
        if command == ["bash", "Allrun"]:
            return FieldOnlyResultWritingProcess(command, **kwargs)
        return SuccessfulProcess(command, **kwargs)

    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"failed"})

    assert finished is not None
    assert finished.status == "failed"
    assert finished.error is not None
    assert "OpenFOAM diagnostics incomplete" in finished.error
    assert finished.result is not None
    acceptance = finished.result["diagnosticsAcceptance"]
    assert acceptance["status"] == "missing"
    assert acceptance["completionGate"]["status"] == "fail"
    assert {item["functionObject"] for item in acceptance["missingDiagnostics"]} >= {
        "patchFlowRate",
        "patchAverage",
        "wallShearStress",
        "wallForces",
    }


def test_openfoam_diagnostics_smoke_case_includes_probe_and_metric_contract() -> None:
    case = openfoam_diagnostics_smoke_case()

    assert case.solver == "openfoam"
    assert case.advancedMode == "incompressible-navier-stokes"
    assert "pressureProbes" in case.files["system/controlDict"]
    manifest = json.loads(case.files["constant/flowlab_patch_metrics.json"])
    assert "pressureProbes" in manifest["functionObjects"]
    assert manifest["patches"]["flow"] == ["inlet", "outlet"]
    runtime_manifest = json.loads(case.files["constant/flowlab_openfoam_function_objects.json"])
    assert runtime_manifest["schema"] == "flowlab.openfoam_function_object_runtime.v1"
    assert "foundation" in runtime_manifest["runtimeStyles"]
    assert "system/functions" in case.files


def test_collect_result_files_ignores_mesh_artifacts_case_insensitively(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "MESH").mkdir(parents=True)
    (case_dir / "MESH" / "flowlab_mesh.vtk").write_text("# vtk DataFile Version 3.0\nmesh\n", encoding="utf-8")
    (case_dir / "RESU" / "run-001").mkdir(parents=True)
    (case_dir / "RESU" / "run-001" / "field.vtk").write_text("# vtk DataFile Version 3.0\nresult\n", encoding="utf-8")

    results = collect_result_files(case_dir)

    assert [result["path"] for result in results] == ["RESU/run-001/field.vtk"]


def test_collect_result_files_identity_contract_requires_controlled_native_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "constant").mkdir(parents=True)
    (case_dir / "constant" / "flowlab_result_identity_contract.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (case_dir / "VTK").mkdir()
    (case_dir / "VTK" / "case_0001.vtk").write_text(
        "# vtk DataFile Version 3.0\nlegacy ordering\n",
        encoding="utf-8",
    )
    controlled = [
        {
            "path": "postProcessing/flowlabNative/time_1.vtk",
            "size": 12,
            "sourceCellIdentity": {"verified": True},
        }
    ]
    monkeypatch.setattr(
        execution,
        "_collect_openfoam_native_time_results",
        lambda received_case_dir, limit: controlled,
    )

    results = collect_result_files(case_dir)

    assert results == controlled


def test_collect_result_files_reports_overflow_after_collection_limit(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    result_dir = case_dir / "VTK"
    result_dir.mkdir(parents=True)
    for index in range(10):
        (result_dir / f"case_{index:04d}.vtk").write_text(f"# vtk DataFile Version 3.0\nresult {index}\n", encoding="utf-8")

    results = collect_result_files(case_dir)

    embedded = [result for result in results if "text" in result]
    skipped = [result for result in results if result.get("skipped")]
    assert len(embedded) == 8
    assert embedded[0]["path"] == "VTK/case_0000.vtk"
    assert embedded[-1]["path"] == "VTK/case_0007.vtk"
    assert skipped == [
        {
            "path": "<additional-result-files>",
            "size": len("# vtk DataFile Version 3.0\nresult 8\n") + len("# vtk DataFile Version 3.0\nresult 9\n"),
            "skipped": "2 additional VTK/VTU result file(s) omitted after collection limit 8",
        }
    ]


def test_collect_result_files_converts_openfoam_native_time_directories(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    mesh_path = case_dir / "mesh" / "flowlab_mesh.vtk"
    mesh_path.parent.mkdir(parents=True)
    mesh_path.write_text(_case().files["mesh/flowlab_mesh.vtk"], encoding="utf-8")
    _write_openfoam_native_time_fields(case_dir, "0.1")
    time_dir = case_dir / "0.2"
    time_dir.mkdir()
    (time_dir / "U").write_text(
        """FoamFile { class volVectorField; object U; }
internalField   uniform (2 0 0);
boundaryField {}
""",
        encoding="utf-8",
    )
    (time_dir / "p").write_text(
        """FoamFile { class volScalarField; object p; }
internalField   uniform 10;
boundaryField {}
""",
        encoding="utf-8",
    )
    (time_dir / "alpha.water").write_text(
        """FoamFile { class volScalarField; object alpha.water; }
internalField   uniform 0.75;
boundaryField {}
""",
        encoding="utf-8",
    )
    (time_dir / "rho").write_text(
        """FoamFile { class volScalarField; object rho; }
internalField   uniform 997;
boundaryField {}
""",
        encoding="utf-8",
    )

    results = collect_result_files(case_dir)

    assert [result["path"] for result in results] == [
        "postProcessing/flowlabNative/time_0_1.vtk",
        "postProcessing/flowlabNative/time_0_2.vtk",
    ]
    assert (case_dir / "postProcessing" / "flowlabNative" / "time_0_1.vtk").is_file()
    assert results[0]["time"] == 0.1
    assert results[0]["timeText"] == "0.1"
    assert results[0]["timeSource"] == "openfoam-time-directory"
    assert results[0]["sourceFields"] == ["T", "U", "p"]
    parsed_first = parse_vtk_result(results[0]["text"])
    assert parsed_first["cellData"]["vectors"]["U"] == [[1.0, 0.0, 0.0]]
    assert parsed_first["cellData"]["scalars"]["p"] == [12.5]
    assert parsed_first["cellData"]["scalars"]["T"] == [293.15]
    parsed_second = parse_vtk_result(results[1]["text"])
    assert parsed_second["fields"] == ["U", "alpha.water", "p", "rho"]
    assert parsed_second["cellData"]["scalars"]["alpha.water"] == [0.75]
    assert parsed_second["cellData"]["scalars"]["rho"] == [997.0]
    assert {field["name"] for field in results[1]["fieldSummary"]["fields"]} == {"U", "alpha.water", "p", "rho"}


def test_collect_result_files_reports_unparseable_openfoam_native_fields(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    mesh_path = case_dir / "mesh" / "flowlab_mesh.vtk"
    mesh_path.parent.mkdir(parents=True)
    mesh_path.write_text(_case().files["mesh/flowlab_mesh.vtk"], encoding="utf-8")
    _write_openfoam_native_time_fields(case_dir, "0.1", malformed=True)
    for field_name in ("U", "T"):
        (case_dir / "0.1" / field_name).unlink()

    results = collect_result_files(case_dir)

    assert results == [
        {
            "path": "openfoam-native-results",
            "size": 0,
            "skipped": "No parseable OpenFOAM native time-directory fields: 0.1: p: tuple count 2 does not match 1 cells or 4 points",
        }
    ]


def test_openfoam_exit_zero_without_parseable_results_fails_quality_gate() -> None:
    error = solver_output_quality_error("openfoam", ["Time = 0.1", "End"], [])

    assert error == "OpenFOAM completed but no parseable VTK/VTU or native time-directory field results were surfaced."


def test_list_case_artifacts_indexes_pvd_collection_times(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    result_dir = case_dir / "VTK"
    result_dir.mkdir(parents=True)
    for index in (0, 1):
        (result_dir / f"case_{index:04d}.vtu").write_text(
            """<VTKFile type="UnstructuredGrid" version="0.1">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <PointData><DataArray type="Float32" Name="pressure" format="ascii">1 2 3 4</DataArray></PointData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>""",
            encoding="utf-8",
        )
    (result_dir / "collection.pvd").write_text(
        """<?xml version="1.0"?>
<VTKFile type="Collection" version="0.1">
  <Collection>
    <DataSet timestep="0.00" part="0" file="case_0000.vtu"/>
    <DataSet timestep="0.05" part="0" file="case_0001.vtu"/>
    <DataSet timestep="0.10" part="0" file="../missing.vtu"/>
    <DataSet timestep="0.15" part="0" file="../../escape.vtu"/>
  </Collection>
</VTKFile>""",
        encoding="utf-8",
    )

    index = list_case_artifacts(case_dir, kind="result", limit=10)

    by_path = {artifact["path"]: artifact for artifact in index["artifacts"]}  # type: ignore[index]
    assert index["count"] == 3
    assert by_path["VTK/case_0000.vtu"]["time"] == 0.0
    assert by_path["VTK/case_0000.vtu"]["timeSource"] == "pvd"
    assert by_path["VTK/case_0000.vtu"]["collectionPath"] == "VTK/collection.pvd"
    assert by_path["VTK/case_0001.vtu"]["time"] == 0.05
    collection = by_path["VTK/collection.pvd"]["collectionSummary"]
    assert collection["schema"] == "flowlab.pvd_collection.v1"
    assert collection["datasetCount"] == 4
    assert collection["referencedResultCount"] == 2
    assert collection["missingResultCount"] == 1
    assert collection["unsafeReferenceCount"] == 1
    assert collection["datasets"][0]["file"] == "VTK/case_0000.vtu"
    assert collection["datasets"][0]["exists"] is True


def test_write_result_collection_pvd_and_run_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = tmp_path / "case"
    result_dir = case_dir / "postProcessing" / "flowlabNative"
    result_dir.mkdir(parents=True)
    for index, time_value in enumerate((0.1, 0.2)):
        (result_dir / f"time_{index}.vtk").write_text(
            """# vtk DataFile Version 3.0
result
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
SCALARS p float 1
LOOKUP_TABLE default
1 2 3 4
""",
            encoding="utf-8",
        )
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name in {"pyvista", "meshio"} else original_find_spec(name))
    results = [
        {"path": "postProcessing/flowlabNative/time_0.vtk", "size": 10, "time": 0.1},
        {"path": "postProcessing/flowlabNative/time_1.vtk", "size": 10, "time": 0.2},
    ]
    collection = write_result_collection_pvd(case_dir, results)
    assert collection is not None
    assert collection["path"] == "postProcessing/flowlab_results.pvd"

    index = list_case_artifacts(case_dir, kind="result", limit=10)
    by_path = {artifact["path"]: artifact for artifact in index["artifacts"]}  # type: ignore[index]
    assert by_path["postProcessing/flowlabNative/time_0.vtk"]["time"] == 0.1
    assert by_path["postProcessing/flowlabNative/time_1.vtk"]["time"] == 0.2
    assert by_path["postProcessing/flowlab_results.pvd"]["collectionSummary"]["referencedResultCount"] == 2

    job = JobRecord(caseId="case-manifest", solver="openfoam", status="complete", caseDir=str(case_dir), execution="docker", exitCode=0)
    diagnostic_files: list[dict[str, object]] = []
    artifact_payload = finalize_run_artifacts(
        case_dir,
        job=job,
        result_files=results,
        diagnostic_files=diagnostic_files,
        diagnostic_summary=[],
        mesh_quality={"status": "passed", "productionReady": False, "approvalStatus": "blocked"},
        patch_metrics={"status": "complete", "patches": {"inlet": {}, "outlet": {}}},
    )
    assert artifact_payload["artifactManifest"]["path"] == "postProcessing/flowlab_run_artifacts.json"
    manifest = json.loads((case_dir / "postProcessing" / "flowlab_run_artifacts.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "flowlab.run_artifact_manifest.v1"
    assert manifest["status"] == "complete"
    assert manifest["collections"][0]["datasetCount"] == 2
    visual = json.loads((case_dir / "postProcessing" / "flowlab_visual_postprocessing.json").read_text(encoding="utf-8"))
    assert visual["status"] == "skipped"


def test_meshio_roundtrip_validation_skips_when_optional_dependency_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "mesh").mkdir(parents=True)
    (case_dir / "mesh" / "flowlab_mesh.vtk").write_text(_case().files["mesh/flowlab_mesh.vtk"], encoding="utf-8")

    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "meshio" else original_find_spec(name))

    report = write_meshio_roundtrip_validation(case_dir)

    assert report["schema"] == "flowlab.meshio_roundtrip_validation.v1"
    assert report["status"] == "skipped"
    assert report["dependency"] == {"name": "meshio", "available": False}
    assert report["artifacts"] == [{"path": "mesh/flowlab_mesh.vtk", "status": "not-run"}]
    assert (case_dir / "mesh" / "meshio_roundtrip_validation.json").is_file()


def test_collect_result_files_converts_code_saturne_ensight_fluid_domain(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    post = case_dir / "RESU" / "run-001" / "postprocessing"
    post.mkdir(parents=True)
    (post / "RESULTS_FLUID_DOMAIN.case").write_text(
        """FORMAT
type: ensight gold

GEOMETRY
model: results_fluid_domain.geo

VARIABLE
vector per element: 1                         Velocity       results_fluid_domain.velocity.*****
scalar per element: 1                         Pressure       results_fluid_domain.pressure.*****
scalar per element: 1                   Total_Pressure       results_fluid_domain.total_pressure.*****
""",
        encoding="utf-8",
    )
    _write_ensight_geometry(post / "results_fluid_domain.geo")
    _write_ensight_scalar(post / "results_fluid_domain.pressure.00001", "Pressure", [42.0])
    _write_ensight_scalar(post / "results_fluid_domain.total_pressure.00001", "Total Pressure", [45.0])
    _write_ensight_vector(post / "results_fluid_domain.velocity.00001", "Velocity", [(1.0, 2.0, 3.0)])

    results = collect_result_files(case_dir)

    assert [result["path"] for result in results] == ["RESU/run-001/postprocessing/flowlab_code_saturne_fluid.vtk"]
    assert "CELL_TYPES 1\n12" in results[0]["text"]
    parsed = parse_vtk_result(results[0]["text"])
    assert parsed["cellTypes"] == [12]
    assert parsed["fields"] == ["pressure", "total_pressure", "velocity"]
    assert parsed["pointData"]["scalars"]["pressure"] == [42.0] * 8
    assert parsed["pointData"]["scalars"]["total_pressure"] == [45.0] * 8
    assert parsed["pointData"]["vectors"]["velocity"] == [[1.0, 2.0, 3.0]] * 8
    assert results[0]["fieldSummary"]["fields"][0]["name"] == "pressure"
    assert results[0]["fieldSummary"]["fields"][0]["tupleCount"] == 8


def _ensight_record(text: str) -> bytes:
    return text.encode("ascii").ljust(80, b" ")[:80]


def _write_ensight_geometry(path: Path) -> None:
    points = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
    ]
    data = bytearray()
    for record in ("C Binary", "FlowLab test", "Output by Code_Saturne version", "node id assign", "element id assign", "part"):
        data.extend(_ensight_record(record))
    data.extend(struct.pack("<i", 1))
    data.extend(_ensight_record("Fluid domain"))
    data.extend(_ensight_record("coordinates"))
    data.extend(struct.pack("<i", len(points)))
    for axis in range(3):
        data.extend(struct.pack("<" + "f" * len(points), *(point[axis] for point in points)))
    data.extend(_ensight_record("hexa8"))
    data.extend(struct.pack("<i", 1))
    data.extend(struct.pack("<8i", 1, 2, 3, 4, 5, 6, 7, 8))
    path.write_bytes(bytes(data))


def _write_ensight_scalar(path: Path, label: str, values: list[float]) -> None:
    data = bytearray()
    data.extend(_ensight_record(label))
    data.extend(_ensight_record("part"))
    data.extend(struct.pack("<i", 1))
    data.extend(_ensight_record("hexa8"))
    data.extend(struct.pack("<" + "f" * len(values), *values))
    path.write_bytes(bytes(data))


def _write_ensight_vector(path: Path, label: str, values: list[tuple[float, float, float]]) -> None:
    data = bytearray()
    data.extend(_ensight_record(label))
    data.extend(_ensight_record("part"))
    data.extend(struct.pack("<i", 1))
    data.extend(_ensight_record("hexa8"))
    components = [component for axis in range(3) for vector in values for component in (vector[axis],)]
    data.extend(struct.pack("<" + "f" * len(components), *components))
    path.write_bytes(bytes(data))


def test_job_blocks_when_docker_and_native_solver_are_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path)

    job = manager.queue_job(_case())

    assert job.status == "blocked"
    assert job.error is not None
    assert "Docker is unavailable" in job.error
    assert "`foamRun` was not found" in job.error
    assert job.caseDir is not None
    assert (Path(job.caseDir) / "README.md").exists()


def test_validate_solver_case_catches_missing_openfoam_assets() -> None:
    case = _case()
    case.files.pop("system/blockMeshDict")
    case.files["Allrun"] = "foamRun -solver incompressibleFluid\n"

    issues = validate_solver_case(case)

    assert any("system/blockMeshDict" in issue for issue in issues)
    assert any("blockMesh" in issue for issue in issues)
    assert any("checkMesh" in issue for issue in issues)


def test_validate_solver_case_requires_openfoam_checkmesh_step() -> None:
    case = _case()
    case.files["Allrun"] = "#!/usr/bin/env bash\nblockMesh\nfoamRun -solver incompressibleFluid\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("checkMesh -allGeometry -allTopology" in issue for issue in issues)


def test_job_blocks_invalid_generated_case_before_execution_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = _case()
    case.files.pop("0/U")

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("0/U" in line for line in job.logs)
    assert job.command == []


def test_job_blocks_openfoam_case_missing_patch_metric_function_object_before_process_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = _case()
    case.files["system/controlDict"] = case.files["system/controlDict"].replace(
        """
    wallShearStress
    {
        patches         (walls);
    }
""",
        "",
    )
    case.files["system/functions"] = case.files["system/functions"].replace(
        "\n    wallShearStress\n"
        """    {
        patches         (walls);
    }""",
        "",
    )
    case.files["system/functions"] = case.files["system/functions"].replace(
        """
    wallShearStress
    {
        patches         (walls);
    }
""",
        "",
    )
    case = add_case_manifest(case)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("missing `wallShearStress` function object" in line for line in job.logs)
    assert job.command == []


def test_job_blocks_su2_case_missing_mode_preset_before_execution_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = _case("su2")
    case.files.pop("flowlab_su2_mode_preset.json")
    case = add_case_manifest(case)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("flowlab_su2_mode_preset.json" in line for line in job.logs)
    assert job.command == []


def test_job_blocks_su2_case_missing_capability_matrix_before_execution_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = _case("su2")
    case.files.pop("flowlab_su2_capability_matrix.json")
    case = add_case_manifest(case)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("flowlab_su2_capability_matrix.json" in line for line in job.logs)
    assert job.command == []


def test_job_blocks_su2_blocked_mode_missing_preflight_before_execution_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    case.files.pop("flowlab_su2_advanced_preflight.json")
    case = add_case_manifest(case)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("flowlab_su2_advanced_preflight.json" in line for line in job.logs)
    assert job.command == []


def test_job_blocks_su2_blocked_mode_malformed_preflight_before_execution_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: True)
    manager = JobManager(runtime_root=tmp_path)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    case.files["flowlab_su2_advanced_preflight.json"] = json.dumps(
        {
            "schema": "flowlab.su2_advanced_preflight.v1",
            "advancedMode": "cavitation",
            "targetSolver": "su2",
            "productionReady": False,
            "nativeSu2Ready": False,
            "requestedPhysicsResolved": False,
            "status": "generated",
            "supportLevel": "starter-supported-single-zone",
            "reviewTemplate": "flowlab_su2_native_config_template.cfg",
            "handoffArtifacts": ["flowlab_su2_cavitation_handoff.json"],
            "artifactChecks": [{"artifact": "flowlab_su2_cavitation_handoff.json", "status": "pass"}],
            "unresolvedActions": ["manual native cavitation setup"],
            "expectedPrimaryFields": ["pressure", "velocity", "vapour_fraction", "cavitation_source"],
            "readinessChecks": [],
            "blockingReasons": ["manual native cavitation setup"],
        }
    ) + "\n"
    case = add_case_manifest(case)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert any("advanced preflight status must be blocked-export-only" in line for line in job.logs)
    assert any("advanced preflight supportLevel must match" in line for line in job.logs)
    assert any("advanced preflight artifactChecks must cover" in line for line in job.logs)
    assert any("advanced preflight must include readinessChecks" in line for line in job.logs)
    assert job.command == []


def test_validate_solver_case_requires_mode_specific_openfoam_files() -> None:
    case = _case()
    case.advancedMode = "cavitation"

    issues = validate_solver_case(case)

    assert any("0/alpha.vapour" in issue for issue in issues)
    assert any("constant/fvModels" in issue for issue in issues)
    assert any("constant/cavitationProperties" in issue for issue in issues)


def test_validate_solver_case_blocks_failed_mesh_quality_report() -> None:
    case = _case()
    case.files["mesh/quality.json"] = (
        '{"schema":"flowlab.mesh_quality.v1","status":"failed",'
        '"warnings":["1 cell(s) are degenerate or below the minimum area threshold."]}\n'
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("Mesh quality check failed" in issue for issue in issues)
    assert any("degenerate" in issue for issue in issues)


@pytest.mark.parametrize("solver", ["openfoam", "su2", "code-saturne"])
def test_validate_solver_case_requires_mesh_quality_report_for_mesh_bundle_solvers(solver: str) -> None:
    case = _case(solver)
    case.files.pop("mesh/quality.json")
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any(f"Missing required {solver} case file `mesh/quality.json`" in issue for issue in issues)


def test_validate_solver_case_requires_source_angle_quality_evidence() -> None:
    case = _case()
    case.files["mesh/quality.json"] = (
        '{"schema":"flowlab.mesh_quality.v1","status":"ok",'
        '"summary":{"maxAspectRatio":1.4},'
        '"thresholds":{"maxAspectRatio":200},'
        '"warnings":[]}\n'
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("summary must include numeric `maxNonOrthogonalityDeg`" in issue for issue in issues)
    assert any("summary must include numeric `maxSkewnessEstimate`" in issue for issue in issues)
    assert any("thresholds must include numeric `maxNonOrthogonalityDeg`" in issue for issue in issues)
    assert any("thresholds must include numeric `maxSkewnessEstimate`" in issue for issue in issues)


def test_validate_solver_case_rejects_ok_status_for_exceeded_source_quality_threshold() -> None:
    case = _case()
    case.files["mesh/quality.json"] = (
        '{"schema":"flowlab.mesh_quality.v1","status":"ok",'
        '"summary":{"maxNonOrthogonalityDeg":90,"maxSkewnessEstimate":1.2},'
        '"thresholds":{"maxNonOrthogonalityDeg":85,"maxSkewnessEstimate":0.95},'
        '"warnings":[]}\n'
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("status cannot be ok when `maxNonOrthogonalityDeg` exceeds" in issue for issue in issues)
    assert any("status cannot be ok when `maxSkewnessEstimate` exceeds" in issue for issue in issues)
    assert any("must include warnings when `maxNonOrthogonalityDeg` exceeds" in issue for issue in issues)
    assert any("must include warnings when `maxSkewnessEstimate` exceeds" in issue for issue in issues)


def test_validate_solver_case_requires_boundary_layer_plan_schema() -> None:
    case = _case()
    case.files["mesh/boundary_layer_plan.json"] = '{"schema":"wrong","productionReady":true}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("boundary_layer_plan.json` has an unsupported schema" in issue for issue in issues)
    assert any("productionReady=false" in issue for issue in issues)


def test_validate_solver_case_requires_prism_layer_plan_schema() -> None:
    case = _case()
    case.files["mesh/prism_layer_plan.json"] = '{"schema":"wrong","productionReady":true}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("prism_layer_plan.json` has an unsupported schema" in issue for issue in issues)
    assert any("native prism-layer mesh evidence" in issue for issue in issues)
    assert any("prism_layer_plan.json` must include readinessChecks" in issue for issue in issues)


def test_validate_solver_case_requires_production_mesh_plan_schema() -> None:
    case = _case()
    case.files["mesh/production_mesh_plan.json"] = '{"schema":"wrong","productionReady":true}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("production_mesh_plan.json` has an unsupported schema" in issue for issue in issues)
    assert any("CAD-quality 3D mesh evidence" in issue for issue in issues)
    assert any("readinessChecks" in issue for issue in issues)


def test_validate_solver_case_requires_production_mesh_acceptance_schema() -> None:
    case = _case()
    case.files["mesh/production_mesh_acceptance.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "sourceArtifacts": {},
            "acceptanceCriteria": [],
            "solverAcceptance": {"openfoam": {}, "su2": {}},
            "nativeQualityEvidence": {},
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("production_mesh_acceptance.json` has an unsupported schema" in issue for issue in issues)
    assert any("CAD/native mesh acceptance evidence" in issue for issue in issues)
    assert any("production_mesh_acceptance.json` must include acceptanceCriteria" in issue for issue in issues)
    assert any("must include OpenFOAM, SU2, and Code_Saturne solver acceptance entries" in issue for issue in issues)
    assert any("must include nativeQualityEvidence" in issue for issue in issues)
    assert any("must reference generated production mesh source artifacts" in issue for issue in issues)

    case = _case()
    acceptance = json.loads(case.files["mesh/production_mesh_acceptance.json"])
    acceptance["solverAcceptance"]["openfoam"] = {"status": "passed", "requiredEvidence": [], "currentEvidence": []}
    acceptance["nativeQualityEvidence"]["schema"] = "wrong"
    acceptance["nativeQualityEvidence"]["productionReady"] = True
    acceptance["nativeQualityEvidence"]["status"] = "complete"
    acceptance["nativeQualityEvidence"]["sharedRequiredEvidence"] = []
    acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"] = {
        "status": "complete",
        "commands": [],
        "requiredMetrics": ["failedChecks"],
    }
    case.files["mesh/production_mesh_acceptance.json"] = json.dumps(acceptance) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("solver acceptance entries must remain blocked" in issue for issue in issues)
    assert any("nativeQualityEvidence has an unsupported schema" in issue for issue in issues)
    assert any("nativeQualityEvidence must remain productionReady=false" in issue for issue in issues)
    assert any("nativeQualityEvidence must report missing native quality reports" in issue for issue in issues)
    assert any("nativeQualityEvidence must list solver-native cell-quality and y-plus evidence" in issue for issue in issues)
    assert any("nativeQualityEvidence solver reports must remain missing" in issue for issue in issues)
    assert any("OpenFOAM native quality evidence must require y-plus metrics" in issue for issue in issues)


def test_validate_solver_case_requires_adaptation_plan_schema() -> None:
    case = _case()
    case.files["mesh/adaptation_plan.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "sourceArtifacts": {},
            "adaptationTargets": [],
            "readinessChecks": [],
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("adaptation_plan.json` has an unsupported schema" in issue for issue in issues)
    assert any("adaptation_plan.json` must remain productionReady=false" in issue for issue in issues)
    assert any("adaptation_plan.json` must include adaptationTargets" in issue for issue in issues)
    assert any("adaptation_plan.json` must include readinessChecks" in issue for issue in issues)
    assert any("adaptation_plan.json` must reference generated mesh source artifacts" in issue for issue in issues)

    case = _case()
    case.files["mesh/adaptation_plan.json"] = json.dumps(
        {
            "schema": "flowlab.mesh_adaptation_plan.v1",
            "productionReady": False,
            "sourceArtifacts": {
                "quality": "mesh/quality.json",
                "refinementPlan": "mesh/refinement_plan.json",
                "boundaryLayerPlan": "mesh/boundary_layer_plan.json",
                "prismLayerPlan": "mesh/prism_layer_plan.json",
                "physicalGroups": "mesh/physical_groups.json",
            },
            "adaptationTargets": [
                    {
                        "edgeId": "pipe",
                        "geometryTargets": {},
                        "boundaryLayerTargets": {},
                        "fieldIndicatorTargets": ["pressure-gradient"],
                    }
            ],
            "readinessChecks": [{"id": "source-refinement-targets", "status": "pass", "detail": "starter"}],
            "blockingReasons": [],
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("targets must include geometryTargets and boundaryLayerTargets enabled flags" in issue for issue in issues)
    assert any("must include failing native-adaptation readiness checks" in issue for issue in issues)
    assert any("must list blockingReasons" in issue for issue in issues)


def test_validate_solver_case_requires_native_meshing_handoff_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    request = CaseRequest.model_construct(
        project={
            "name": "Native meshing validation",
            "solver": {"meshResolution": "coarse"},
            "nodes": {
                "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
                "sink": {"id": "sink", "type": "sink", "position": {"x": 140, "y": 0}},
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
        },
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
    )
    case = adapters.generate_case(request)

    assert not [
        issue
        for issue in validate_solver_case(case)
        if "native_meshing_plan" in issue
        or "gmsh_production_handoff" in issue
        or "physical_groups" in issue
        or "openfoam_snappy" in issue
        or "production_mesh_acceptance" in issue
    ]

    missing = adapters.generate_case(request)
    missing.files.pop("mesh/native_meshing_plan.json")
    missing = add_case_manifest(missing)

    issues = validate_solver_case(missing)

    assert any("OpenFOAM/SU2/Code_Saturne native meshing handoff artifacts" in issue for issue in issues)

    missing_acceptance = adapters.generate_case(request)
    missing_acceptance.files.pop("mesh/production_mesh_acceptance.json")
    missing_acceptance = add_case_manifest(missing_acceptance)

    issues = validate_solver_case(missing_acceptance)

    assert any("production mesh acceptance checklist JSON" in issue for issue in issues)

    missing_adaptation = adapters.generate_case(request)
    missing_adaptation.files.pop("mesh/adaptation_plan.json")
    missing_adaptation = add_case_manifest(missing_adaptation)

    issues = validate_solver_case(missing_adaptation)

    assert any("adaptation plan JSON" in issue for issue in issues)

    missing_groups = adapters.generate_case(request)
    missing_groups.files.pop("mesh/physical_groups.json")
    missing_groups = add_case_manifest(missing_groups)

    issues = validate_solver_case(missing_groups)

    assert any("physical group map JSON" in issue for issue in issues)

    missing_snappy = adapters.generate_case(request)
    missing_snappy.files.pop("mesh/openfoam_snappy_handoff.json")
    missing_snappy = add_case_manifest(missing_snappy)

    issues = validate_solver_case(missing_snappy)

    assert any("OpenFOAM snappy handoff JSON" in issue for issue in issues)

    missing_snappy_template = adapters.generate_case(request)
    missing_snappy_template.files.pop("mesh/openfoam_snappyHexMeshDict.template")
    missing_snappy_template = add_case_manifest(missing_snappy_template)

    issues = validate_solver_case(missing_snappy_template)

    assert any("OpenFOAM snappy handoff JSON/templates" in issue for issue in issues)

    missing_openfoam_preflight = adapters.generate_case(request)
    missing_openfoam_preflight.files.pop("mesh/openfoam_native_mesh_preflight.py")
    missing_openfoam_preflight = add_case_manifest(missing_openfoam_preflight)

    issues = validate_solver_case(missing_openfoam_preflight)

    assert any("OpenFOAM native mesh preflight script" in issue or "OpenFOAM native mesh preflight artifact" in issue for issue in issues)

    missing_su2_handoff = adapters.generate_case(request)
    missing_su2_handoff.files.pop("mesh/su2_native_meshing_handoff.json")
    missing_su2_handoff = add_case_manifest(missing_su2_handoff)

    issues = validate_solver_case(missing_su2_handoff)

    assert any("SU2 native meshing handoff artifacts" in issue or "OpenFOAM/SU2/Code_Saturne native meshing handoff artifacts" in issue for issue in issues)

    missing_code_saturne_handoff = adapters.generate_case(request)
    missing_code_saturne_handoff.files.pop("mesh/code_saturne_native_meshing_handoff.json")
    missing_code_saturne_handoff = add_case_manifest(missing_code_saturne_handoff)

    issues = validate_solver_case(missing_code_saturne_handoff)

    assert any("Code_Saturne native meshing handoff artifacts" in issue or "OpenFOAM/SU2/Code_Saturne native meshing handoff artifacts" in issue for issue in issues)

    broken_snappy = adapters.generate_case(request)
    broken_snappy.files["mesh/openfoam_snappy_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "addLayersControls": {},
            "boundaryPatchPlan": {"inlet": []},
            "readinessChecks": [],
        }
    ) + "\n"
    broken_snappy = add_case_manifest(broken_snappy)

    issues = validate_solver_case(broken_snappy)

    assert any("openfoam_snappy_handoff.json` has an unsupported schema" in issue for issue in issues)
    assert any("openfoam_snappy_handoff.json` must remain productionReady=false" in issue for issue in issues)
    assert any("openfoam_snappy_handoff.json` must include addLayersControls.layers" in issue for issue in issues)
    assert any("openfoam_snappy_handoff.json` must include inlet, outlet, walls, and frontAndBack patch plans" in issue for issue in issues)
    assert any("openfoam_snappy_handoff.json` must include readinessChecks" in issue for issue in issues)
    assert any("openfoam_snappy_handoff.json` must reference generated OpenFOAM dictionary templates" in issue for issue in issues)

    broken_preflight = adapters.generate_case(request)
    broken_preflight.files["mesh/openfoam_native_mesh_preflight.py"] = "#!/usr/bin/env python3\nprint('missing markers')\n"
    broken_preflight = add_case_manifest(broken_preflight)

    issues = validate_solver_case(broken_preflight)

    assert any("openfoam_native_mesh_preflight.py` must identify" in issue for issue in issues)

    broken_su2_handoff = adapters.generate_case(request)
    broken_su2_handoff.files["mesh/su2_native_meshing_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "markerPlan": {"allMarkers": []},
            "viscousLayerPlan": {"source": "wrong.json"},
            "readinessChecks": [],
        }
    ) + "\n"
    broken_su2_handoff = add_case_manifest(broken_su2_handoff)

    issues = validate_solver_case(broken_su2_handoff)

    assert any("su2_native_meshing_handoff.json` has an unsupported schema" in issue for issue in issues)
    assert any("su2_native_meshing_handoff.json` must remain productionReady=false" in issue for issue in issues)
    assert any("su2_native_meshing_handoff.json` must include a non-empty markerPlan.allMarkers" in issue for issue in issues)
    assert any("su2_native_meshing_handoff.json` must reference `mesh/prism_layer_plan.json`" in issue for issue in issues)
    assert any("su2_native_meshing_handoff.json` must include readinessChecks" in issue for issue in issues)

    broken_code_saturne_handoff = adapters.generate_case(request)
    broken_code_saturne_handoff.files["mesh/code_saturne_native_meshing_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "importPlan": {"boundaryGroups": [], "volumeGroups": []},
            "prismLayerImportPlan": {"source": "wrong.json"},
            "readinessChecks": [],
        }
    ) + "\n"
    broken_code_saturne_handoff = add_case_manifest(broken_code_saturne_handoff)

    issues = validate_solver_case(broken_code_saturne_handoff)

    assert any("code_saturne_native_meshing_handoff.json` has an unsupported schema" in issue for issue in issues)
    assert any("code_saturne_native_meshing_handoff.json` must remain productionReady=false" in issue for issue in issues)
    assert any("code_saturne_native_meshing_handoff.json` must include importPlan.boundaryGroups" in issue for issue in issues)
    assert any("code_saturne_native_meshing_handoff.json` must include importPlan.volumeGroups" in issue for issue in issues)
    assert any("code_saturne_native_meshing_handoff.json` must reference `mesh/prism_layer_plan.json`" in issue for issue in issues)
    assert any("code_saturne_native_meshing_handoff.json` must include readinessChecks" in issue for issue in issues)

    broken = adapters.generate_case(request)
    broken.files["mesh/native_meshing_plan.json"] = json.dumps(
        {
            "schema": "wrong",
            "productionReady": True,
            "handoffArtifacts": [],
            "prismLayerPlan": {},
            "readinessChecks": [],
        }
    ) + "\n"
    broken.files["mesh/gmsh_production_handoff.geo"] = "// missing marker\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("native_meshing_plan.json` has an unsupported schema" in issue for issue in issues)
    assert any("native_meshing_plan.json` must remain productionReady=false" in issue for issue in issues)
    assert any("must list the generated Gmsh handoff artifact" in issue for issue in issues)
    assert any("must list the generated physical group map artifact" in issue for issue in issues)
    assert any("must list the generated OpenFOAM snappy handoff artifact" in issue for issue in issues)
    assert any("must list the generated SU2 native meshing handoff artifact" in issue for issue in issues)
    assert any("must list the generated Code_Saturne native meshing handoff artifact" in issue for issue in issues)
    assert any("must list the generated production mesh acceptance checklist artifact" in issue for issue in issues)
    assert any("must list the generated adaptation plan artifact" in issue for issue in issues)
    assert any("native_meshing_plan.json` must include readinessChecks" in issue for issue in issues)
    assert any("native_meshing_plan.json` must reference `mesh/prism_layer_plan.json`" in issue for issue in issues)
    assert any("native_meshing_plan.json` must reference `mesh/adaptation_plan.json`" in issue for issue in issues)
    assert any("gmsh_production_handoff.geo` must identify itself" in issue for issue in issues)


def test_validate_solver_case_requires_openfoam_mesh_review_manifest() -> None:
    case = _case()
    case.files["mesh/openfoam_review.json"] = '{"schema":"wrong","productionReady":false}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("mesh review manifest has an unsupported schema" in issue for issue in issues)
    assert any("mesh review manifest must include readinessChecks" in issue for issue in issues)
    assert any("non-production mesh review manifest must list blockingReasons" in issue for issue in issues)


def test_validate_solver_case_blocks_missing_openfoam_patch_metric_function_objects() -> None:
    case = _case()
    case.files["system/controlDict"] = case.files["system/controlDict"].replace(
        """
    patchFlowRate
    {
        patches         (inlet outlet);
    }
""",
        "",
    )
    case.files["system/functions"] = case.files["system/functions"].replace(
        "\n    patchFlowRate\n"
        """    {
        patches         (inlet outlet);
    }""",
        "",
    )
    case.files["system/functions"] = case.files["system/functions"].replace(
        """
    patchFlowRate
    {
        patches         (inlet outlet);
    }
""",
        "",
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("missing `patchFlowRate` function object" in issue for issue in issues)


def test_validate_solver_case_blocks_incomplete_openfoam_conjugate_heat_transfer() -> None:
    case = _case()
    case.advancedMode = "conjugate-heat-transfer"
    case.files["Allrun"] = "#!/usr/bin/env bash\nblockMesh\nfoamMultiRun\n"
    case.files["system/controlDict"] = (
        "application     foamMultiRun;\n"
        "regionSolvers\n"
        "{\n"
        "    fluid fluid;\n"
        "    solid solid;\n"
        "}\n"
        + _openfoam_metric_control_dict(application="foamMultiRun")
    )
    case.files["0/rho"] = "boundaryField {}\n"
    case.files["0/k"] = "boundaryField {}\n"
    case.files["0/omega"] = "boundaryField {}\n"
    case.files["0/nut"] = "boundaryField {}\n"
    case.files["0/alphat"] = "boundaryField {}\n"
    case.files["constant/thermophysicalProperties"] = "thermoType {}\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("constant/fluid/physicalProperties" in issue for issue in issues)
    assert any("system/fluid/fvSchemes" in issue for issue in issues)
    assert any("system/solid/fvSolution" in issue for issue in issues)
    assert any("must not execute `foamMultiRun`" in issue for issue in issues)


def test_validate_solver_case_requires_valid_openfoam_cht_interface_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    case = adapters.generate_case(
        adapters.CaseRequest.model_construct(
            project={
                "name": "CHT validation",
                "solver": {"meshResolution": "coarse"},
                "nodes": {
                    "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
                    "sink": {"id": "sink", "type": "sink", "position": {"x": 160, "y": 0}},
                },
                "edges": {
                    "pipe": {
                        "id": "pipe",
                        "type": "pipe",
                        "from": "source",
                        "to": "sink",
                        "shape": {"kind": "circular", "diameter": 0.1},
                    }
                },
            },
            solver="openfoam",
            advancedMode="conjugate-heat-transfer",
        )
    )

    assert validate_solver_case(case) == []

    case.files["constant/flowlab_cht_interface.json"] = "not json\n"
    case = add_case_manifest(case)
    issues = validate_solver_case(case)
    assert any("must be valid JSON" in issue for issue in issues)

    case = adapters.generate_case(
        adapters.CaseRequest.model_construct(
            project={
                "name": "CHT validation",
                "solver": {"meshResolution": "coarse"},
                "nodes": {
                    "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
                    "sink": {"id": "sink", "type": "sink", "position": {"x": 160, "y": 0}},
                },
                "edges": {
                    "pipe": {
                        "id": "pipe",
                        "type": "pipe",
                        "from": "source",
                        "to": "sink",
                        "shape": {"kind": "circular", "diameter": 0.1},
                    }
                },
            },
            solver="openfoam",
            advancedMode="conjugate-heat-transfer",
        )
    )
    interface = json.loads(case.files["constant/flowlab_cht_interface.json"])
    interface["schema"] = "wrong"
    interface["patches"]["fluid"]["neighbourRegion"] = "fluid"
    interface["readinessChecks"] = []
    interface["blockingReasons"] = []
    interface["regionMeshChecks"] = {"script": "missing", "commands": []}
    case.files["AllmeshCheck"] = "#!/usr/bin/env bash\n"
    case.files["constant/flowlab_cht_interface.json"] = json.dumps(interface)
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("fluid interface patch" in issue for issue in issues)
    assert any("readinessChecks" in issue for issue in issues)
    assert any("fluid region mesh" in issue for issue in issues)
    assert any("solid region mesh" in issue for issue in issues)
    assert any("generated AllmeshCheck" in issue for issue in issues)
    assert any("fluid region checkMesh command" in issue for issue in issues)
    assert any("solid region checkMesh command" in issue for issue in issues)
    assert any("blockingReasons" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_physics_preset() -> None:
    case = _case("code-saturne")
    case.files["DATA/flowlab_physics_preset.json"] = "not json\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("valid JSON" in issue for issue in issues)

    case = _case("code-saturne")
    case.files["DATA/flowlab_physics_preset.json"] = '{"schema":"wrong","advancedMode":"incompressible-navier-stokes"}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("unsupported schema" in issue for issue in issues)

    case = _case("code-saturne")
    case.files["DATA/flowlab_physics_preset.json"] = '{"schema":"flowlab.code_saturne_physics_preset.v1","advancedMode":"cavitation"}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("advancedMode must match" in issue for issue in issues)

    case = _case("code-saturne")
    case.files["DATA/flowlab_physics_preset.json"] = json.dumps(
        {
            "schema": "flowlab.code_saturne_physics_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportLevel": "starter-supported",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("must include readinessChecks" in issue for issue in issues)
    assert any("nativeSetupPlan.manualNativeModules" in issue for issue in issues)
    assert any("resultExpectations.expectedPrimaryFields" in issue for issue in issues)

    case = _case("code-saturne")
    case.files["DATA/flowlab_native_setup_checklist.json"] = json.dumps(
        {
            "schema": "wrong",
            "advancedMode": "cavitation",
            "requestedPhysicsResolved": False,
            "generatedFiles": [],
            "expectedPrimaryFields": [],
            "actionItems": [],
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("native setup checklist has an unsupported schema" in issue for issue in issues)
    assert any("native setup checklist advancedMode must match" in issue for issue in issues)
    assert any("must list generated native case files" in issue for issue in issues)
    assert any("must include expectedPrimaryFields" in issue for issue in issues)
    assert any("unresolved native setup checklist must include actionItems" in issue for issue in issues)

    case = _case("code-saturne")
    case.advancedMode = "cavitation"
    case.files["DATA/flowlab_physics_preset.json"] = json.dumps(
        {
            "schema": "flowlab.code_saturne_physics_preset.v1",
            "advancedMode": "cavitation",
            "supportLevel": "metadata-only",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "readinessChecks": [{"id": "phase-change-law", "status": "fail", "detail": "missing"}],
            "blockedOrManualModels": ["phase-change cavitation law"],
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("blockingReasons" in issue for issue in issues)
    assert any("blockedOrManualModels and manualSetupRequirements" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_capability_matrix() -> None:
    case = _case("code-saturne")
    case.files["DATA/flowlab_code_saturne_capability_matrix.json"] = "not json\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("capability matrix must be valid JSON" in issue for issue in issues)

    case = _case("code-saturne")
    case.files["DATA/flowlab_code_saturne_capability_matrix.json"] = json.dumps(
        {
            "schema": "wrong",
            "activeMode": "cavitation",
            "productionReady": True,
            "entries": [],
            "summary": {},
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("capability matrix has an unsupported schema" in issue for issue in issues)
    assert any("capability matrix activeMode must match" in issue for issue in issues)
    assert any("capability matrix must remain productionReady=false" in issue for issue in issues)
    assert any("capability matrix must include entries" in issue for issue in issues)

    case = _case("code-saturne")
    matrix = json.loads(case.files["DATA/flowlab_code_saturne_capability_matrix.json"])
    matrix["entries"] = [
        {
            "advancedMode": "incompressible-navier-stokes",
            "active": True,
            "requestedPhysicsResolved": True,
            "expectedPrimaryFields": ["pressure", "velocity"],
            "manualNativeModules": [],
            "blockingReasons": [],
        }
    ]
    matrix["summary"] = {"starterSupportedModes": [], "unresolvedModes": []}
    case.files["DATA/flowlab_code_saturne_capability_matrix.json"] = json.dumps(matrix) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("capability matrix must cover all FlowLab advanced modes" in issue for issue in issues)
    assert any("summary must include starter-supported incompressible mode" in issue for issue in issues)
    assert any("summary must include unresolved advanced modes" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_heat_transfer_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Heat CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="heat-transfer")
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "heat-transfer" in issue or "temperature_celsius" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Heat CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="heat-transfer")
    )
    preset = json.loads(broken.files["DATA/flowlab_physics_preset.json"])
    preset["productionReady"] = True
    preset["setupXmlModels"]["thermalScalar"] = "off"
    preset["resultExpectations"]["expectedPrimaryFields"] = ["pressure", "velocity"]
    preset["thermalStarter"]["scalarName"] = "temperature"
    preset["thermalBoundaryPlan"]["excludedPhysics"] = ["radiation"]
    broken.files["DATA/flowlab_physics_preset.json"] = json.dumps(preset) + "\n"
    checklist = json.loads(broken.files["DATA/flowlab_native_setup_checklist.json"])
    checklist["productionReady"] = True
    checklist["thermalStarter"]["scalarName"] = "temperature"
    broken.files["DATA/flowlab_native_setup_checklist.json"] = json.dumps(checklist) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("productionReady=false" in issue for issue in issues)
    assert any("setupXmlModels.thermalScalar=`temperature_celsius`" in issue for issue in issues)
    assert any("include temperature in expectedPrimaryFields" in issue for issue in issues)
    assert any("thermalStarter scalarName temperature_celsius" in issue for issue in issues)
    assert any("exclude fluid-solid conjugate heat transfer" in issue for issue in issues)
    assert any("exclude phase change" in issue for issue in issues)
    assert any("native setup checklist must remain productionReady=false" in issue for issue in issues)
    assert any("heat-transfer checklist must include thermalStarter scalarName temperature_celsius" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_turbulence_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Turbulence CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="compressible-flow")
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "turbulencePlan" in issue or "setupXmlModels.turbulence" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Turbulence CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="compressible-flow")
    )
    preset = json.loads(broken.files["DATA/flowlab_physics_preset.json"])
    preset["setupXmlModels"]["turbulence"] = "off"
    preset["turbulencePlan"]["schema"] = "wrong"
    preset["turbulencePlan"]["model"] = "off"
    preset["turbulencePlan"]["starterStatus"] = "laminar-starter"
    preset["turbulencePlan"]["productionReady"] = True
    preset["turbulencePlan"]["requiredEvidence"] = []
    preset["turbulencePlan"]["unresolvedModels"] = ["RANS"]
    broken.files["DATA/flowlab_physics_preset.json"] = json.dumps(preset) + "\n"
    checklist = json.loads(broken.files["DATA/flowlab_native_setup_checklist.json"])
    checklist["turbulencePlan"]["model"] = "off"
    broken.files["DATA/flowlab_native_setup_checklist.json"] = json.dumps(checklist) + "\n"
    matrix = json.loads(broken.files["DATA/flowlab_code_saturne_capability_matrix.json"])
    active_entry = next(entry for entry in matrix["entries"] if entry["advancedMode"] == "compressible-flow")
    active_entry["turbulenceModel"] = "off"
    broken.files["DATA/flowlab_code_saturne_capability_matrix.json"] = json.dumps(matrix) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("setupXmlModels.turbulence=`k-epsilon`" in issue for issue in issues)
    assert any("turbulencePlan has an unsupported schema" in issue for issue in issues)
    assert any("turbulencePlan model must match" in issue for issue in issues)
    assert any("starterStatus must be rans-starter" in issue for issue in issues)
    assert any("turbulencePlan must remain productionReady=false" in issue for issue in issues)
    assert any("turbulencePlan must list requiredEvidence" in issue for issue in issues)
    assert any("LES and DNS listed as unresolved" in issue for issue in issues)
    assert any("capability matrix entries must include turbulenceModel" in issue for issue in issues)
    assert any("checklist turbulencePlan must match" in issue for issue in issues)


def test_validate_solver_case_requires_code_saturne_native_physics_review_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_connected_code_saturne_project(),
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "native physics review template" in issue]
    assert not [issue for issue in issues if "unresolved physics cases" in issue]
    assert case.status == "blocked"
    assert case.runCommand == []

    broken = case.model_copy(deep=True)
    broken.status = "generated"
    broken.runCommand = ["code_saturne", "run"]
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unresolved physics cases must not include a runnable solver command" in issue for issue in issues)
    assert any("unresolved physics cases must be marked blocked" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Cavitation", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )
    broken.files.pop("DATA/flowlab_native_physics_review.py")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include `DATA/flowlab_native_physics_review.py`" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Cavitation", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )
    broken.files["DATA/flowlab_native_physics_review.py"] = "FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = False\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must keep unresolved-physics guardrails" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Cavitation", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )
    preset = json.loads(broken.files["DATA/flowlab_physics_preset.json"])
    preset["nativeSetupPlan"]["reviewTemplate"] = "DATA/wrong.py"
    broken.files["DATA/flowlab_physics_preset.json"] = json.dumps(preset) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unresolved physics preset must reference the native physics review template" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Cavitation", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )
    checklist = json.loads(broken.files["DATA/flowlab_native_setup_checklist.json"])
    checklist["generatedFiles"].remove("DATA/flowlab_native_physics_review.py")
    broken.files["DATA/flowlab_native_setup_checklist.json"] = json.dumps(checklist) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unresolved native setup checklist must list the native physics review template" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_compressible_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Compressible", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="compressible-flow",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "Code_Saturne compressible handoff" in issue or "compressible-flow case" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Compressible", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="compressible-flow",
        )
    )
    broken.files.pop("DATA/flowlab_compressible_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("compressible-flow case must include generated compressible handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Compressible", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="compressible-flow",
        )
    )
    broken.files["DATA/flowlab_compressible_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "advancedMode": "heat-transfer",
            "productionReady": True,
            "nativeCodeSaturneReady": True,
            "starterSurrogate": {"status": "native-compressible"},
            "requiredNativeModules": [],
            "thermodynamicSetup": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("compressible handoff has an unsupported schema" in issue for issue in issues)
    assert any("compressible handoff must target code-saturne" in issue for issue in issues)
    assert any("compressible handoff advancedMode must be compressible-flow" in issue for issue in issues)
    assert any("compressible handoff must remain non-production" in issue for issue in issues)
    assert any("identify the pressure-based surrogate" in issue for issue in issues)
    assert any("list required native compressible modules" in issue for issue in issues)
    assert any("thermodynamic setup manual" in issue for issue in issues)
    assert any("density, temperature, and mach_number" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_water_hammer_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="water-hammer")
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "water-hammer handoff" in issue or "water-hammer waveform" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="water-hammer")
    )
    broken.files.pop("DATA/flowlab_water_hammer_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("handoff JSON and waveform CSV" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer CS", "nodes": {}, "edges": {}}, solver="code-saturne", advancedMode="water-hammer")
    )
    broken.files["DATA/flowlab_water_hammer_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeCodeSaturneReady": True,
            "codeSaturne": {"csv": "wrong.csv"},
        }
    ) + "\n"
    broken.files["DATA/flowlab_water_hammer_waveform.csv"] = "bad\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target code-saturne" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("must reference the generated waveform CSV" in issue for issue in issues)
    assert any("waveform CSV has an unsupported header" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_cht_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne CHT", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="conjugate-heat-transfer",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "Code_Saturne CHT handoff" in issue or "conjugate heat-transfer" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne CHT", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="conjugate-heat-transfer",
        )
    )
    broken.files.pop("DATA/flowlab_cht_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include generated CHT handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne CHT", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="conjugate-heat-transfer",
        )
    )
    broken.files["DATA/flowlab_cht_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeCodeSaturneReady": True,
            "fluidDomain": {"thermalScalar": "temperature"},
            "solidDomain": {"meshStatus": "generated"},
            "interfaceCoupling": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target code-saturne" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("starter fluid thermal scalar" in issue for issue in issues)
    assert any("solid mesh is not generated" in issue for issue in issues)
    assert any("interface coupling manual" in issue for issue in issues)
    assert any("solid_temperature and heat_flux" in issue for issue in issues)


@pytest.mark.parametrize(
    ("advanced_mode", "handoff_path", "missing_field"),
    [
        ("multiphase-vof", "DATA/flowlab_multiphase_handoff.json", "phase_fraction"),
        ("cavitation", "DATA/flowlab_cavitation_handoff.json", "vapour_fraction"),
    ],
)
def test_validate_solver_case_requires_valid_code_saturne_phase_handoff(
    monkeypatch: pytest.MonkeyPatch,
    advanced_mode: str,
    handoff_path: str,
    missing_field: str,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Phase", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode=advanced_mode,
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "Code_Saturne phase handoff" in issue or "phase-physics" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Phase", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode=advanced_mode,
        )
    )
    broken.files.pop(handoff_path)
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include generated phase handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Phase", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode=advanced_mode,
        )
    )
    broken.files[handoff_path] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "advancedMode": "wrong",
            "productionReady": True,
            "nativeCodeSaturneReady": True,
            "phases": [{"name": "liquid"}],
            "interfaceSetup": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
            "cavitationInputs": {} if advanced_mode == "cavitation" else None,
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target code-saturne" in issue for issue in issues)
    assert any("advancedMode must match" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("at least two phases" in issue for issue in issues)
    assert any("interface setup manual" in issue for issue in issues)
    assert any("expected phase fields" in issue for issue in issues)
    assert missing_field in json.loads(case.files[handoff_path])["expectedPrimaryFields"]
    if advanced_mode == "cavitation":
        assert any("saturationPressure" in issue for issue in issues)


def test_validate_solver_case_requires_valid_code_saturne_rigid_body_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Rigid Body", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="rigid-body-fluid-forces",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "Code_Saturne rigid-body handoff" in issue or "Code_Saturne rigid-body case" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Rigid Body", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="rigid-body-fluid-forces",
        )
    )
    broken.files.pop("DATA/flowlab_rigid_body_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("rigid-body case must include generated rigid-body handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "Code Saturne Rigid Body", "nodes": {}, "edges": {}},
            solver="code-saturne",
            advancedMode="rigid-body-fluid-forces",
        )
    )
    broken.files["DATA/flowlab_rigid_body_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "advancedMode": "wrong",
            "productionReady": True,
            "nativeCodeSaturneReady": True,
            "couplingIntent": {"status": "automatic", "preferredCurrentSandbox": "openfoam"},
            "motionSetup": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target code-saturne" in issue for issue in issues)
    assert any("advancedMode must be rigid-body-fluid-forces" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("coupling setup manual" in issue for issue in issues)
    assert any("MuJoCo as the current sandbox" in issue for issue in issues)
    assert any("motion setup manual" in issue for issue in issues)
    assert any("body_force and moment" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_water_hammer_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer SU2", "nodes": {}, "edges": {}}, solver="su2", advancedMode="water-hammer")
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "water-hammer handoff" in issue or "water-hammer waveform" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer SU2", "nodes": {}, "edges": {}}, solver="su2", advancedMode="water-hammer")
    )
    broken.files.pop("flowlab_su2_water_hammer_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("handoff JSON and waveform CSV" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(project={"name": "Water Hammer SU2", "nodes": {}, "edges": {}}, solver="su2", advancedMode="water-hammer")
    )
    broken.files["flowlab_su2_water_hammer_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeSu2Ready": True,
            "su2": {"csv": "wrong.csv"},
        }
    ) + "\n"
    broken.files["flowlab_su2_water_hammer_waveform.csv"] = "bad\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target su2" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("must reference the generated waveform CSV" in issue for issue in issues)
    assert any("waveform CSV has an unsupported header" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_cht_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 CHT", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="conjugate-heat-transfer",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "SU2 CHT handoff" in issue or "conjugate heat-transfer" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 CHT", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="conjugate-heat-transfer",
        )
    )
    broken.files.pop("flowlab_su2_cht_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include generated CHT handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 CHT", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="conjugate-heat-transfer",
        )
    )
    broken.files["flowlab_su2_cht_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeSu2Ready": True,
            "fluidZone": {"solver": "NAVIER_STOKES"},
            "solidZone": {"meshStatus": "generated"},
            "interface": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target su2" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("must define the fluid zone starter solver" in issue for issue in issues)
    assert any("solid mesh is not generated" in issue for issue in issues)
    assert any("interface coupling manual" in issue for issue in issues)
    assert any("solid_temperature and heat_flux" in issue for issue in issues)


@pytest.mark.parametrize(
    ("advanced_mode", "handoff_path", "expected_fields"),
    [
        ("multiphase-vof", "flowlab_su2_multiphase_handoff.json", {"phase_fraction", "interface_height"}),
        ("cavitation", "flowlab_su2_cavitation_handoff.json", {"vapour_fraction", "cavitation_source"}),
    ],
)
def test_validate_solver_case_requires_valid_su2_phase_handoff(
    monkeypatch: pytest.MonkeyPatch,
    advanced_mode: str,
    handoff_path: str,
    expected_fields: set[str],
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Phase", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode=advanced_mode,
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "SU2 phase" in issue or "SU2 cavitation handoff" in issue]
    assert expected_fields.issubset(set(json.loads(case.files[handoff_path])["expectedPrimaryFields"]))

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Phase", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode=advanced_mode,
        )
    )
    broken.files.pop(handoff_path)
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("phase-mode case must include generated phase handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Phase", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode=advanced_mode,
        )
    )
    broken.files[handoff_path] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "advancedMode": "wrong",
            "productionReady": True,
            "nativeSu2Ready": True,
            "phases": [{"name": "liquid"}],
            "interfaceSetup": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
            "cavitationInputs": {} if advanced_mode == "cavitation" else None,
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target su2" in issue for issue in issues)
    assert any("advancedMode must match" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("at least two phases" in issue for issue in issues)
    assert any("interface setup manual" in issue for issue in issues)
    assert any("expected phase fields" in issue for issue in issues)
    if advanced_mode == "cavitation":
        assert any("saturationPressure" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_rigid_body_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Rigid Body", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="rigid-body-fluid-forces",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "SU2 rigid-body handoff" in issue or "SU2 rigid-body case" in issue]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Rigid Body", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="rigid-body-fluid-forces",
        )
    )
    broken.files.pop("flowlab_su2_rigid_body_handoff.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("rigid-body case must include generated rigid-body handoff JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Rigid Body", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="rigid-body-fluid-forces",
        )
    )
    broken.files["flowlab_su2_rigid_body_handoff.json"] = json.dumps(
        {
            "schema": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeSu2Ready": True,
            "couplingIntent": {"status": "automatic", "preferredCurrentSandbox": "openfoam"},
            "motionSetup": {"status": "automatic"},
            "expectedPrimaryFields": ["pressure"],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("must target su2" in issue for issue in issues)
    assert any("must remain non-production" in issue for issue in issues)
    assert any("coupling setup manual" in issue for issue in issues)
    assert any("MuJoCo as the current sandbox" in issue for issue in issues)
    assert any("motion setup manual" in issue for issue in issues)
    assert any("body_force and moment" in issue for issue in issues)


@pytest.mark.parametrize(
    ("advanced_mode", "expected_handoffs", "expected_field"),
    [
        ("multiphase-vof", ["flowlab_su2_multiphase_handoff.json"], "phase_fraction"),
        ("cavitation", ["flowlab_su2_cavitation_handoff.json"], "vapour_fraction"),
        (
            "water-hammer",
            ["flowlab_su2_water_hammer_handoff.json", "flowlab_su2_water_hammer_waveform.csv"],
            "pressure_wave",
        ),
        ("conjugate-heat-transfer", ["flowlab_su2_cht_handoff.json"], "solid_temperature"),
        ("rigid-body-fluid-forces", ["flowlab_su2_rigid_body_handoff.json"], "body_force"),
    ],
)
def test_validate_solver_case_accepts_su2_blocked_mode_preflight_for_all_handoff_modes(
    monkeypatch: pytest.MonkeyPatch,
    advanced_mode: str,
    expected_handoffs: list[str],
    expected_field: str,
) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 blocked preflight", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode=advanced_mode,
        )
    )

    issues = validate_solver_case(case)

    unexpected_preflight_issues = [
        issue
        for issue in issues
        if "SU2 advanced preflight" in issue
        or "advanced preflight" in issue
        or "native config review template" in issue
        or "blocked export preset" in issue
    ]
    assert unexpected_preflight_issues == []
    assert case.status == "blocked"
    assert case.runCommand == []
    assert "FLOWLAB_UNSUPPORTED_MODE= YES" in case.files["case.cfg"]

    preset = json.loads(case.files["flowlab_su2_mode_preset.json"])
    checklist = json.loads(case.files["flowlab_su2_native_setup_checklist.json"])
    preflight = json.loads(case.files["flowlab_su2_advanced_preflight.json"])

    assert preset["supportLevel"] == "blocked-export-only"
    assert preset["supportedByAdapter"] is False
    assert preset["requestedPhysicsResolved"] is False
    assert preset["nativeSetupPlan"]["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
    assert preset["nativeSetupPlan"]["handoffArtifacts"] == expected_handoffs
    assert expected_field in preset["resultExpectations"]["expectedPrimaryFields"]

    assert checklist["advancedMode"] == advanced_mode
    assert checklist["supportLevel"] == "blocked-export-only"
    assert checklist["requestedPhysicsResolved"] is False
    assert "flowlab_su2_native_config_template.cfg" in checklist["generatedFiles"]
    assert "flowlab_su2_advanced_preflight.json" in checklist["generatedFiles"]
    for artifact in expected_handoffs:
        assert artifact in case.files
        assert artifact in checklist["generatedFiles"]
    assert checklist["expectedPrimaryFields"] == preset["resultExpectations"]["expectedPrimaryFields"]
    assert expected_field in checklist["expectedPrimaryFields"]
    assert checklist["actionItems"]

    assert preflight["schema"] == "flowlab.su2_advanced_preflight.v1"
    assert preflight["targetSolver"] == "su2"
    assert preflight["advancedMode"] == advanced_mode
    assert preflight["status"] == "blocked-export-only"
    assert preflight["supportLevel"] == "blocked-export-only"
    assert preflight["productionReady"] is False
    assert preflight["nativeSu2Ready"] is False
    assert preflight["requestedPhysicsResolved"] is False
    assert preflight["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
    assert preflight["handoffArtifacts"] == expected_handoffs
    assert preflight["manualNativeModules"] == preset["nativeSetupPlan"]["manualNativeModules"]
    assert preflight["expectedPrimaryFields"] == preset["resultExpectations"]["expectedPrimaryFields"]
    assert expected_field in preflight["expectedPrimaryFields"]
    assert preflight["unresolvedActions"]
    assert preflight["blockingReasons"]
    assert all(check["status"] == "pass" for check in preflight["artifactChecks"])
    checked_artifacts = {check["artifact"] for check in preflight["artifactChecks"]}
    assert set(expected_handoffs).issubset(checked_artifacts)
    assert "flowlab_su2_native_config_template.cfg" in checked_artifacts


def test_validate_solver_case_requires_su2_blocked_mode_review_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "SU2_CFD")
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )

    issues = validate_solver_case(case)

    assert not [issue for issue in issues if "native config review template" in issue]
    preflight = json.loads(case.files["flowlab_su2_advanced_preflight.json"])
    assert preflight["schema"] == "flowlab.su2_advanced_preflight.v1"
    assert preflight["advancedMode"] == "cavitation"
    assert preflight["status"] == "blocked-export-only"
    assert preflight["productionReady"] is False
    assert preflight["nativeSu2Ready"] is False
    assert preflight["reviewTemplate"] == "flowlab_su2_native_config_template.cfg"
    assert "flowlab_su2_cavitation_handoff.json" in preflight["handoffArtifacts"]
    assert preflight["unresolvedActions"]
    assert "vapour_fraction" in preflight["expectedPrimaryFields"]
    assert all(check["status"] == "pass" for check in preflight["artifactChecks"])
    assert "flowlab_su2_advanced_preflight.json" in json.loads(case.files["flowlab_su2_native_setup_checklist.json"])["generatedFiles"]

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    broken.files.pop("flowlab_su2_native_config_template.cfg")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include `flowlab_su2_native_config_template.cfg`" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    broken.files["flowlab_su2_native_config_template.cfg"] = "SOLVER= NAVIER_STOKES\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must keep FLOWLAB_TEMPLATE_ONLY and FLOWLAB_UNSUPPORTED_MODE guardrails" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    checklist = json.loads(broken.files["flowlab_su2_native_setup_checklist.json"])
    checklist["generatedFiles"].remove("flowlab_su2_native_config_template.cfg")
    broken.files["flowlab_su2_native_setup_checklist.json"] = json.dumps(checklist) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("checklist must list the native config review template" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    broken.files.pop("flowlab_su2_advanced_preflight.json")
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("must include `flowlab_su2_advanced_preflight.json`" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    broken.files["flowlab_su2_advanced_preflight.json"] = json.dumps(
        {
            "schema": "wrong",
            "advancedMode": "wrong",
            "targetSolver": "openfoam",
            "productionReady": True,
            "nativeSu2Ready": True,
            "requestedPhysicsResolved": True,
            "reviewTemplate": "wrong.cfg",
            "handoffArtifacts": [],
            "artifactChecks": [{"status": "fail"}],
            "unresolvedActions": [],
            "expectedPrimaryFields": ["pressure"],
            "blockingReasons": [],
        }
    ) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("advanced preflight has an unsupported schema" in issue for issue in issues)
    assert any("advanced preflight advancedMode must match" in issue for issue in issues)
    assert any("advanced preflight must remain non-production" in issue for issue in issues)
    assert any("advanced preflight must reference" in issue for issue in issues)
    assert any("advanced preflight handoffArtifacts must match" in issue for issue in issues)
    assert any("advanced preflight artifactChecks must pass" in issue for issue in issues)
    assert any("advanced preflight must list unresolvedActions" in issue for issue in issues)
    assert any("advanced preflight expectedPrimaryFields must match" in issue for issue in issues)
    assert any("advanced preflight must include blockingReasons" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    broken.files["flowlab_su2_advanced_preflight.json"] = "{not-json\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("SU2 advanced preflight must be valid JSON" in issue for issue in issues)

    broken = adapters.generate_case(
        CaseRequest.model_construct(
            project={"name": "SU2 Cavitation", "nodes": {}, "edges": {}},
            solver="su2",
            advancedMode="cavitation",
        )
    )
    preset = json.loads(broken.files["flowlab_su2_mode_preset.json"])
    preset["nativeSetupPlan"]["reviewTemplate"] = "wrong.cfg"
    broken.files["flowlab_su2_mode_preset.json"] = json.dumps(preset) + "\n"
    broken = add_case_manifest(broken)

    issues = validate_solver_case(broken)

    assert any("blocked export preset must reference the native config review template" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_mode_preset() -> None:
    case = _case("su2")
    case.files["flowlab_su2_mode_preset.json"] = "not json\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("valid JSON" in issue for issue in issues)

    case = _case("su2")
    case.files["flowlab_su2_mode_preset.json"] = '{"schema":"wrong","advancedMode":"incompressible-navier-stokes"}\n'
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("unsupported schema" in issue for issue in issues)

    case = _case("su2")
    case.files["flowlab_su2_mode_preset.json"] = json.dumps(
        {
            "schema": "flowlab.su2_mode_preset.v1",
            "advancedMode": "cavitation",
            "supportedByAdapter": True,
            "supportLevel": "starter-supported-single-zone",
        }
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("advancedMode must match" in issue for issue in issues)

    case = _case("su2")
    case.status = "generated"
    case.files["flowlab_su2_mode_preset.json"] = json.dumps(
        {
            "schema": "flowlab.su2_mode_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportedByAdapter": False,
            "supportLevel": "blocked-export-only",
        }
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("supportedByAdapter=true" in issue for issue in issues)
    assert any("readinessChecks" in issue for issue in issues)
    assert any("nativeSetupPlan.manualNativeModules" in issue for issue in issues)
    assert any("resultExpectations.expectedPrimaryFields" in issue for issue in issues)

    case = _case("su2")
    case.status = "blocked"
    case.runCommand = []
    case.files["case.cfg"] = (
        "MESH_FILENAME= mesh/flowlab_mesh.su2\n"
        "FLOWLAB_UNSUPPORTED_MODE= YES\n"
    )
    case.files["flowlab_su2_mode_preset.json"] = json.dumps(
        {
            "schema": "flowlab.su2_mode_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportedByAdapter": False,
            "requestedPhysicsResolved": False,
            "supportLevel": "blocked-export-only",
            "readinessChecks": [{"id": "adapter-mapping", "status": "fail", "detail": "missing"}],
            "blockedOrManualModels": ["manual model"],
        }
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("blockingReasons" in issue for issue in issues)
    assert any("blockedOrManualModels and manualSetupRequirements" in issue for issue in issues)
    assert any("nativeSetupPlan.manualNativeModules" in issue for issue in issues)
    assert any("resultExpectations.expectedPrimaryFields" in issue for issue in issues)

    case = _case("su2")
    case.status = "blocked"
    case.runCommand = ["SU2_CFD", "case.cfg"]
    case.files["case.cfg"] = (
        "MESH_FILENAME= mesh/flowlab_mesh.su2\n"
        "FLOWLAB_UNSUPPORTED_MODE= YES\n"
    )
    case.files["flowlab_su2_mode_preset.json"] = json.dumps(
        {
            "schema": "flowlab.su2_mode_preset.v1",
            "advancedMode": "incompressible-navier-stokes",
            "supportedByAdapter": False,
            "supportLevel": "starter-supported-single-zone",
        }
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("must not include a solver run command" in issue for issue in issues)
    assert any("blocked-export-only" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_capability_matrix() -> None:
    case = _case("su2")
    case.files["flowlab_su2_capability_matrix.json"] = "not json\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("SU2 capability matrix must be valid JSON" in issue for issue in issues)

    case = _case("su2")
    case.files["flowlab_su2_capability_matrix.json"] = json.dumps(
        {
            "schema": "wrong",
            "activeMode": "cavitation",
            "productionReady": True,
            "entries": [],
            "summary": {},
        }
    ) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("SU2 capability matrix has an unsupported schema" in issue for issue in issues)
    assert any("SU2 capability matrix activeMode must match" in issue for issue in issues)
    assert any("SU2 capability matrix must remain productionReady=false" in issue for issue in issues)
    assert any("SU2 capability matrix must include entries" in issue for issue in issues)

    case = _case("su2")
    matrix = json.loads(case.files["flowlab_su2_capability_matrix.json"])
    matrix["entries"] = [
        {
            "advancedMode": "incompressible-navier-stokes",
            "active": True,
            "supportLevel": "starter-supported-single-zone",
            "supportedByAdapter": True,
            "requestedPhysicsResolved": True,
            "productionReady": True,
            "expectedPrimaryFields": ["pressure", "velocity"],
            "manualNativeModules": [],
            "blockingReasons": [],
        }
    ]
    matrix["summary"] = {"modeCount": 2, "starterSupportedModes": [], "blockedExportOnlyModes": [], "handoffModes": []}
    case.files["flowlab_su2_capability_matrix.json"] = json.dumps(matrix) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("SU2 capability matrix must cover all FlowLab advanced modes" in issue for issue in issues)
    assert any("SU2 capability matrix entries must remain productionReady=false" in issue for issue in issues)
    assert any("SU2 capability matrix summary modeCount must match entries" in issue for issue in issues)
    assert any("SU2 capability matrix summary must include all starter-supported single-zone modes" in issue for issue in issues)
    assert any("SU2 capability matrix summary must include blocked export-only advanced modes" in issue for issue in issues)
    assert any("SU2 capability matrix summary must include handoff modes" in issue for issue in issues)

    case = _case("su2")
    matrix = json.loads(case.files["flowlab_su2_capability_matrix.json"])
    active_entry = next(entry for entry in matrix["entries"] if entry["advancedMode"] == "incompressible-navier-stokes")
    active_entry["supportLevel"] = "blocked-export-only"
    active_entry["supportedByAdapter"] = False
    active_entry["requestedPhysicsResolved"] = False
    case.files["flowlab_su2_capability_matrix.json"] = json.dumps(matrix) + "\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("active entry supportLevel must match" in issue for issue in issues)
    assert any("active entry supportedByAdapter must match" in issue for issue in issues)
    assert any("active entry requestedPhysicsResolved must match" in issue for issue in issues)


def test_validate_solver_case_requires_valid_su2_native_setup_checklist() -> None:
    case = _case("su2")
    case.files["flowlab_su2_native_setup_checklist.json"] = "not json\n"
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("flowlab_su2_native_setup_checklist.json" in issue and "valid JSON" in issue for issue in issues)

    case = _case("su2")
    case.files["flowlab_su2_native_setup_checklist.json"] = json.dumps(
        {
            "schema": "wrong",
            "advancedMode": "cavitation",
            "supportLevel": "blocked-export-only",
            "requestedPhysicsResolved": False,
            "productionReady": True,
            "generatedFiles": [],
            "readinessItems": [],
            "expectedPrimaryFields": [],
        }
    )
    case = add_case_manifest(case)

    issues = validate_solver_case(case)

    assert any("unsupported schema" in issue for issue in issues)
    assert any("advancedMode must match" in issue for issue in issues)
    assert any("generated case files" in issue for issue in issues)
    assert any("expectedPrimaryFields" in issue for issue in issues)
    assert any("productionReady=false" in issue for issue in issues)
    assert any("readinessItems" in issue for issue in issues)
    assert any("supportLevel must match" in issue for issue in issues)
    assert any("requestedPhysicsResolved must match" in issue for issue in issues)
    assert any("unresolved native setup checklist must include actionItems" in issue for issue in issues)


def test_code_saturne_job_blocks_when_native_solver_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path)

    job = manager.queue_job(_case("code-saturne"))

    assert job.status == "blocked"
    assert job.error is not None
    assert "FLOWLAB_CODE_SATURNE_IMAGE" in job.error
    assert "native `code_saturne`" in job.error
    assert job.caseDir is not None
    assert (Path(job.caseDir) / "README.md").exists()


def test_code_saturne_unresolved_physics_blocks_before_process_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    case = adapters.generate_case(
        CaseRequest.model_construct(
            project=_connected_code_saturne_project(),
            solver="code-saturne",
            advancedMode="cavitation",
        )
    )
    case.status = "generated"
    case.runCommand = ["code_saturne", "run"]
    case = add_case_manifest(case)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(case)

    assert job.status == "blocked"
    assert job.error == "Generated case validation failed."
    assert commands == []
    assert any("unresolved physics cases must not include a runnable solver command" in line for line in job.logs)
    assert any("unresolved physics cases must be marked blocked" in line for line in job.logs)
    assert job.caseDir is not None
    assert (Path(job.caseDir) / "DATA/flowlab_native_physics_review.py").exists()


def test_job_captures_logs_and_successful_mocked_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "docker"
    assert commands[0][:2] == ["docker", "run"]
    assert "--entrypoint" in commands[0]
    assert "--platform" in commands[0]
    assert "linux/amd64" in commands[0]
    assert adapters.DEFAULT_OPENFOAM_IMAGE in commands[0]
    assert any("source /opt/openfoam11/etc/bashrc" in arg for arg in commands[0])
    assert any("solver ok" in line for line in finished.logs)
    assert any("solver ok" in line for line in finished.logs)
    assert finished.exitCode == 0


def test_openfoam_job_uses_configured_shared_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setenv(adapters.OPENFOAM_IMAGE_ENV, "flowlab/openfoam:test")
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case())
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert "flowlab/openfoam:test" in commands[0]


def test_openfoam_cht_runs_mesh_preflight_but_keeps_full_solve_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    request = CaseRequest.model_construct(
        project={
            "name": "CHT preflight",
            "solver": {"meshResolution": "coarse"},
            "nodes": {
                "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
                "sink": {"id": "sink", "type": "sink", "position": {"x": 140, "y": 0}},
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
        },
        solver="openfoam",
        advancedMode="conjugate-heat-transfer",
    )
    case = adapters.generate_case(request)

    def fake_popen(command, **kwargs):
        commands.append(command)
        return ChtMeshPreflightProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "checkMesh")
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(case)
    finished = _wait_for(manager, job.id, {"blocked"})

    assert finished is not None
    assert finished.status == "blocked"
    assert finished.execution == "preflight"
    assert finished.command == ["bash", "AllmeshCheck"]
    assert commands == [["bash", "AllmeshCheck"]]
    assert finished.exitCode == 0
    assert finished.error is not None
    assert "mesh preflight completed" in finished.error
    assert "foamMultiRun remains blocked" in finished.error
    assert finished.result is not None
    regions = finished.result["logSummary"]["checkMeshRegions"]
    assert regions["fluid"]["passed"] is True
    assert regions["fluid"]["counts"]["cells"] == 39
    assert regions["solid"]["passed"] is True
    assert regions["solid"]["maxAspectRatio"] == 8.5


def test_su2_job_runs_with_mounted_binary_bundle_in_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    su2_home = tmp_path / "su2"
    (su2_home / "bin").mkdir(parents=True)
    (su2_home / "bin" / "SU2_CFD").write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setenv("FLOWLAB_SU2_HOME", str(su2_home))
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case("su2"))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "docker"
    assert commands[0][:2] == ["docker", "run"]
    assert "--platform" in commands[0]
    assert "linux/amd64" in commands[0]
    assert "ubuntu:22.04" in commands[0]
    assert f"{su2_home}:/opt/su2:ro" in commands[0]
    assert any("PYTHONPATH=/opt/su2/bin" in arg and "SU2_CFD case.cfg" in arg for arg in commands[0])


def test_code_saturne_job_runs_with_mocked_native_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "code_saturne")
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case("code-saturne"))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "native"
    assert commands[0] == ["code_saturne", "run"]
    assert any("solver ok" in line for line in finished.logs)
    assert finished.exitCode == 0


def test_code_saturne_job_runs_with_configured_docker_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setenv("FLOWLAB_CODE_SATURNE_IMAGE", "flowlab-code-saturne:test")
    monkeypatch.setenv("FLOWLAB_CODE_SATURNE_PLATFORM", "linux/amd64")
    monkeypatch.setattr(adapters, "_docker_available", lambda: True)
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case("code-saturne"))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "docker"
    assert commands[0][:2] == ["docker", "run"]
    assert "--platform" in commands[0]
    assert "linux/amd64" in commands[0]
    assert "flowlab-code-saturne:test" in commands[0]
    image_index = commands[0].index("flowlab-code-saturne:test")
    assert commands[0][image_index: image_index + 3] == ["flowlab-code-saturne:test", "/bin/bash", "-lc"]
    assert "USER=${USER:-flowlab}" in commands[0][-1]
    assert "code_saturne run" in commands[0][-1]
    assert any("solver ok" in line for line in finished.logs)
    assert finished.exitCode == 0


def test_mujoco_job_blocks_when_python_module_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists", lambda _module: False)
    manager = JobManager(runtime_root=tmp_path)

    job = manager.queue_job(_case("mujoco"))

    assert job.status == "blocked"
    assert job.error is not None
    assert "Python module `mujoco` is not installed" in job.error
    assert job.caseDir is not None
    assert (Path(job.caseDir) / "README.md").exists()


def test_mujoco_job_runs_with_mocked_native_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists", lambda module: module == "mujoco")
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    job = manager.queue_job(_case("mujoco"))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "native"
    assert commands[0] == ["python3", "run_mujoco.py"]
    assert any("solver ok" in line for line in finished.logs)
    assert finished.exitCode == 0


def test_mujoco_job_collects_output_summary_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", lambda command: command == "python3")
    monkeypatch.setattr(adapters, "_python_module_exists", lambda module: module == "mujoco")
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: MuJoCoDiagnosticProcess(command, **kwargs))

    job = manager.queue_job(_case("mujoco"))
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.result is not None
    assert finished.result["diagnosticFiles"][0]["path"] == "outputs/summary.json"
    assert finished.result["diagnosticSummary"][0]["kind"] == "mujoco-summary"
    assert finished.result["diagnosticSummary"][0]["latest"]["steps"] == 120.0
    assert finished.result["diagnosticSummary"][0]["latest"]["velocity0"] == 1.25


def test_mujoco_job_runs_with_configured_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    def fake_popen(command, **kwargs):
        commands.append(command)
        return SuccessfulProcess(command, **kwargs)

    monkeypatch.setenv("FLOWLAB_MUJOCO_PYTHON", str(python))
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_python_module_exists_for_command", lambda command, module: command == str(python) and module == "mujoco")
    manager = JobManager(runtime_root=tmp_path, popen_factory=fake_popen)

    case = _case("mujoco")
    case.runCommand = [str(python), "run_mujoco.py"]
    case = add_case_manifest(case)
    job = manager.queue_job(case)
    finished = _wait_for(manager, job.id, {"complete"})

    assert finished is not None
    assert finished.status == "complete"
    assert finished.execution == "native"
    assert commands[0] == [str(python), "run_mujoco.py"]
    assert finished.exitCode == 0


class BlockingStdout:
    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def __iter__(self):
        yield "started\n"
        self.release.wait(2)


class BlockingProcess:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.stdout = BlockingStdout(self.release)
        self.terminated = False

    def wait(self) -> int:
        self.release.wait(2)
        return -15 if self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True
        self.release.set()


def test_job_cancellation_terminates_running_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = BlockingProcess()
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    monkeypatch.setattr(adapters, "_command_exists", _openfoam_command_available)
    manager = JobManager(runtime_root=tmp_path, popen_factory=lambda command, **kwargs: process)

    job = manager.queue_job(_case())
    running = _wait_until(manager, job.id, lambda current: current.status == "running" and any("started" in line for line in current.logs))
    assert running is not None

    cancelled = manager.cancel_job(job.id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert process.terminated is True
    assert any("Cancellation requested" in line for line in cancelled.logs)
