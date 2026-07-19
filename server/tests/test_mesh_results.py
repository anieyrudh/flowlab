from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.flowlab.mesh import generate_mesh_bundle, mesh_to_openfoam_cht_region_polymesh, mesh_to_openfoam_polymesh
from server.flowlab.results import parse_vtk_result, preview_vtk_result_text, summarize_vtk_result_text
from server.flowlab.schemas import CaseRequest
from server.flowlab import adapters


def _venturi_project() -> dict:
    return {
        "name": "Backend Venturi Fixture",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "position": {"x": 120, "y": 260}},
            "throat": {"id": "throat", "position": {"x": 420, "y": 260}},
            "sink": {"id": "sink", "position": {"x": 720, "y": 260}},
        },
        "edges": {
            "inlet": {
                "id": "inlet",
                "type": "venturi",
                "from": "source",
                "to": "throat",
                "length": 6,
                "shape": {"kind": "circular", "diameter": 0.18},
                "throatDiameter": 0.075,
            },
            "channel": {
                "id": "channel",
                "type": "pipe",
                "from": "throat",
                "to": "sink",
                "length": 7,
                "shape": {"kind": "rectangular", "width": 0.22, "height": 0.08},
            },
        },
    }


def _valid_reviewed_stl() -> str:
    return """solid reviewedFlowLabSurfaces
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
endsolid reviewedFlowLabSurfaces
"""


def _reviewed_surface_stl(name: str, *, x_offset: float = 0.0) -> str:
    return f"""solid {name}
  facet normal 0 0 1
    outer loop
      vertex {x_offset} 0 0
      vertex {x_offset + 1} 0 0
      vertex {x_offset} 1 0
    endloop
  endfacet
endsolid {name}
"""


def _multi_surface_reviewed_geometry(*, duplicate_patch: bool = False, unsafe_patch: bool = False) -> dict:
    wall_patch = "reviewed_inlet" if duplicate_patch else "reviewed_walls"
    if unsafe_patch:
        wall_patch = "../walls"
    return {
        "sourceType": "multi-surface-stl",
        "cadReviewed": True,
        "reviewedAt": "2026-06-15T00:00:00Z",
        "reviewNotes": "Reviewed multi-surface CAD export from a patch-cleanup workflow.",
        "surfaces": [
            {
                "id": "surf-inlet",
                "surfaceName": "inlet",
                "role": "inlet",
                "patchName": "reviewed_inlet",
                "cadReviewed": True,
                "reviewNotes": "Planar inlet cap.",
                "stlText": _reviewed_surface_stl("inlet"),
            },
            {
                "id": "surf-outlet",
                "surfaceName": "outlet",
                "role": "outlet",
                "patchName": "reviewed_outlet",
                "cadReviewed": True,
                "reviewNotes": "Planar outlet cap.",
                "stlText": _reviewed_surface_stl("outlet", x_offset=2.0),
            },
            {
                "id": "surf-walls",
                "surfaceName": "walls",
                "role": "wall",
                "patchName": wall_patch,
                "cadReviewed": True,
                "reviewNotes": "Fluid wetted wall surface.",
                "stlText": _reviewed_surface_stl("walls", x_offset=4.0),
            },
            {
                "id": "surf-interface",
                "surfaceName": "interface_probe",
                "role": "interface",
                "patchName": "reviewed_interface",
                "cadReviewed": True,
                "reviewNotes": "Optional interface surface for later coupled workflows.",
                "stlText": _reviewed_surface_stl("interface_probe", x_offset=6.0),
            },
        ],
    }


def _multi_surface_reviewed_geometry_with_boundary_conditions() -> dict:
    reviewed_geometry = _multi_surface_reviewed_geometry()
    boundary_conditions = {
        "reviewed_inlet": {
            "type": "velocity-inlet",
            "status": "ready",
            "velocity": {"x": 1.25, "y": 0.0, "z": 0.0},
        },
        "reviewed_outlet": {
            "type": "pressure-outlet",
            "status": "ready",
            "pressure": 101325.0,
        },
        "reviewed_walls": {
            "type": "temperature-wall",
            "status": "ready",
            "temperature": 315.15,
        },
        "reviewed_interface": {
            "type": "coupled-interface",
            "status": "placeholder",
            "notes": "Mapped/coupled interface setup is a guarded placeholder.",
        },
    }
    for surface in reviewed_geometry["surfaces"]:
        surface["boundaryCondition"] = boundary_conditions[surface["patchName"]]
    return reviewed_geometry


def _multi_surface_project(**geometry_overrides: object) -> dict:
    project = _venturi_project()
    reviewed_geometry = _multi_surface_reviewed_geometry()
    reviewed_geometry.update(geometry_overrides)
    project["solver"] = {
        "meshResolution": "coarse",
        "reviewedGeometry": reviewed_geometry,
    }
    return project


def _patch_block(field_text: str, patch_name: str) -> str:
    match = re.search(rf"\b{re.escape(patch_name)}\b\s*\{{(?P<body>.*?)\n\s*\}}", field_text, re.DOTALL)
    assert match is not None, f"{patch_name} boundaryField entry missing from field file:\n{field_text}"
    return match.group("body")


def _assert_patch_block_contains(field_text: str, patch_name: str, *needles: str) -> None:
    block = _patch_block(field_text, patch_name)
    for needle in needles:
        assert needle in block


def _write_bundle_files(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_mesh_bundle_generates_json_vtk_vtu_and_script() -> None:
    bundle = generate_mesh_bundle(_venturi_project())

    assert bundle.mesh["format"] == "flowlab-mesh-v1"
    assert len(bundle.mesh["points"]) == 56
    assert len(bundle.mesh["cells"]) == 39
    assert {region["edgeType"] for region in bundle.mesh["regions"]} == {"venturi", "pipe", "connector"}
    connector = next(region for region in bundle.mesh["regions"] if region["edgeType"] == "connector")
    assert connector["edgeId"] == "connector_throat"
    assert connector["cellCount"] == 3
    assert {
        "mesh/flowlab_mesh.json",
        "mesh/flowlab_mesh.vtk",
        "mesh/flowlab_mesh.vtu",
        "mesh/flowlab_mesh.su2",
        "mesh/flowlab_mesh.msh",
        "mesh/controls.json",
        "mesh/quality.json",
        "mesh/refinement_plan.json",
        "mesh/boundary_layer_plan.json",
        "mesh/prism_layer_plan.json",
        "mesh/adaptation_plan.json",
        "mesh/physical_groups.json",
        "mesh/openfoam_snappy_handoff.json",
        "mesh/su2_native_meshing_handoff.json",
        "mesh/code_saturne_native_meshing_handoff.json",
        "mesh/openfoam_native_mesh_preflight.py",
        "mesh/openfoam_snappyHexMeshDict.template",
        "mesh/openfoam_surfaceFeatureExtractDict.template",
        "mesh/openfoam_meshQualityDict.template",
        "constant/triSurface/reviewedFlowLabSurfaces.stl",
        "system/snappyHexMeshDict",
        "system/surfaceFeatureExtractDict",
        "system/meshQualityDict",
        "mesh/production_mesh_plan.json",
        "mesh/native_meshing_plan.json",
        "mesh/production_mesh_acceptance.json",
        "mesh/gmsh_production_handoff.geo",
        "mesh/generate_mesh.py",
    }.issubset(bundle.files)
    controls = json.loads(bundle.files["mesh/controls.json"])
    quality = json.loads(bundle.files["mesh/quality.json"])
    refinement_plan = json.loads(bundle.files["mesh/refinement_plan.json"])
    boundary_layer_plan = json.loads(bundle.files["mesh/boundary_layer_plan.json"])
    prism_layer_plan = json.loads(bundle.files["mesh/prism_layer_plan.json"])
    adaptation_plan = json.loads(bundle.files["mesh/adaptation_plan.json"])
    physical_groups = json.loads(bundle.files["mesh/physical_groups.json"])
    openfoam_snappy = json.loads(bundle.files["mesh/openfoam_snappy_handoff.json"])
    su2_handoff = json.loads(bundle.files["mesh/su2_native_meshing_handoff.json"])
    code_saturne_handoff = json.loads(bundle.files["mesh/code_saturne_native_meshing_handoff.json"])
    production_mesh_plan = json.loads(bundle.files["mesh/production_mesh_plan.json"])
    native_meshing_plan = json.loads(bundle.files["mesh/native_meshing_plan.json"])
    production_mesh_acceptance = json.loads(bundle.files["mesh/production_mesh_acceptance.json"])
    assert controls["schema"] == "flowlab.mesh_controls.v1"
    assert controls["baseSegments"] == 6
    assert controls["boundaryLayerLayers"] == 1
    assert controls["featureRefinement"] == {
        "enabled": False,
        "factor": 1,
        "clusterStrength": 0.0,
        "featureTypes": ["venturi-throat", "diameter-transition"],
        "eligibleEdgeTypes": ["contraction", "expansion", "nozzle", "venturi"],
    }
    assert controls["transverseFractions"] == pytest.approx([0.0, 0.222222222, 0.777777778, 1.0])
    assert refinement_plan["schema"] == "flowlab.mesh_refinement_plan.v1"
    assert refinement_plan["productionReady"] is False
    assert refinement_plan["regions"][0]["featureRefinement"]["enabled"] is False
    assert boundary_layer_plan["schema"] == "flowlab.boundary_layer_plan.v1"
    assert boundary_layer_plan["productionReady"] is False
    assert boundary_layer_plan["targetYPlus"] == 30.0
    assert len(boundary_layer_plan["edges"]) == 2
    inlet_plan = next(edge for edge in boundary_layer_plan["edges"] if edge["edgeId"] == "inlet")
    assert inlet_plan["hydraulicDiameter"] == pytest.approx(0.075)
    assert inlet_plan["targetFirstCellHeight"] > 0
    assert inlet_plan["starterFirstCellHeight"] == pytest.approx(0.01666666665)
    assert inlet_plan["needsPrismLayerMeshing"] is True
    assert prism_layer_plan["schema"] == "flowlab.prism_layer_plan.v1"
    assert prism_layer_plan["productionReady"] is False
    assert prism_layer_plan["sourceBoundaryLayerPlan"] == "mesh/boundary_layer_plan.json"
    assert len(prism_layer_plan["edges"]) == 2
    inlet_prism = next(edge for edge in prism_layer_plan["edges"] if edge["edgeId"] == "inlet")
    assert inlet_prism["layerCount"] == 1
    assert inlet_prism["targetFirstCellHeight"] == pytest.approx(inlet_plan["targetFirstCellHeight"])
    assert inlet_prism["starterStripCanRepresentPrisms"] is False
    assert inlet_prism["nativeMesherRequired"] is True
    assert "solver y-plus field after a run" in inlet_prism["requiredEvidence"]
    prism_readiness = {check["id"]: check["status"] for check in prism_layer_plan["readinessChecks"]}
    assert prism_readiness["target-first-cell-heights"] == "pass"
    assert prism_readiness["native-prism-layer-mesh"] == "fail"
    assert prism_readiness["solver-y-plus-evidence"] == "fail"
    assert adaptation_plan["schema"] == "flowlab.mesh_adaptation_plan.v1"
    assert adaptation_plan["productionReady"] is False
    assert adaptation_plan["sourceArtifacts"]["refinementPlan"] == "mesh/refinement_plan.json"
    assert adaptation_plan["sourceArtifacts"]["prismLayerPlan"] == "mesh/prism_layer_plan.json"
    assert len(adaptation_plan["adaptationTargets"]) == 2
    inlet_adaptation = next(target for target in adaptation_plan["adaptationTargets"] if target["edgeId"] == "inlet")
    assert inlet_adaptation["geometryTargets"]["enabled"] is True
    assert "diameter/throat transition review" in inlet_adaptation["geometryTargets"]["reasons"]
    assert inlet_adaptation["boundaryLayerTargets"]["enabled"] is True
    assert inlet_adaptation["boundaryLayerTargets"]["targetYPlus"] == 30.0
    assert "pressure-gradient" in inlet_adaptation["fieldIndicatorTargets"]
    assert "phase-fraction-gradient" in inlet_adaptation["fieldIndicatorTargets"]
    channel_adaptation = next(target for target in adaptation_plan["adaptationTargets"] if target["edgeId"] == "channel")
    assert channel_adaptation["geometryTargets"]["enabled"] is False
    adaptation_readiness = {check["id"]: check["status"] for check in adaptation_plan["readinessChecks"]}
    assert adaptation_readiness["source-refinement-targets"] == "pass"
    assert adaptation_readiness["boundary-layer-adaptation-targets"] == "pass"
    assert adaptation_readiness["solver-field-indicators"] == "fail"
    assert adaptation_readiness["native-adapted-volume-mesh"] == "fail"
    assert adaptation_plan["blockingReasons"]
    assert physical_groups["schema"] == "flowlab.physical_group_map.v1"
    assert physical_groups["productionReady"] is False
    assert physical_groups["counts"]["volumes"] == 3
    assert physical_groups["counts"]["inlets"] == 2
    assert physical_groups["counts"]["outlets"] == 2
    assert "fluid_inlet" in physical_groups["solverTargets"]["gmsh"]["physicalNames"]
    assert "inlet_inlet" in physical_groups["solverTargets"]["su2"]["markers"]
    assert physical_groups["solverTargets"]["openfoam"]["sourceToAggregate"]["inlet_inlet"] == "inlet"
    inlet_group = next(group for group in physical_groups["groups"] if group["name"] == "inlet_inlet")
    assert inlet_group["role"] == "inlet"
    assert inlet_group["dimension"] == 2
    assert inlet_group["solverNames"]["codeSaturne"] == "inlet_inlet"
    fluid_group = next(group for group in physical_groups["groups"] if group["name"] == "fluid_inlet")
    assert fluid_group["dimension"] == 3
    assert fluid_group["boundaryGroups"]
    assert openfoam_snappy["schema"] == "flowlab.openfoam_snappy_handoff.v1"
    assert openfoam_snappy["productionReady"] is False
    assert openfoam_snappy["status"] == "review-only"
    assert openfoam_snappy["sourceArtifacts"]["physicalGroups"] == "mesh/physical_groups.json"
    assert openfoam_snappy["sourceArtifacts"]["prismLayerPlan"] == "mesh/prism_layer_plan.json"
    assert openfoam_snappy["templateArtifacts"]["snappyHexMeshDict"] == "mesh/openfoam_snappyHexMeshDict.template"
    assert openfoam_snappy["templateArtifacts"]["surfaceFeatureExtractDict"] == "mesh/openfoam_surfaceFeatureExtractDict.template"
    assert openfoam_snappy["templateArtifacts"]["meshQualityDict"] == "mesh/openfoam_meshQualityDict.template"
    preflight_script = bundle.files["mesh/openfoam_native_mesh_preflight.py"]
    assert "flowlab.openfoam_native_mesh_preflight.v1" in preflight_script
    assert "flowlab.openfoam_native_mesh_preflight_report.v1" in preflight_script
    assert "constant/triSurface/reviewedFlowLabSurfaces.stl" in preflight_script
    assert "snappyHexMesh -overwrite" in preflight_script
    assert "postProcess -func yPlus" in preflight_script
    assert "constant/triSurface/reviewedFlowLabSurfaces.stl" in openfoam_snappy["expectedNativeFiles"]
    assert openfoam_snappy["installedArtifacts"]["triSurface"] == "constant/triSurface/reviewedFlowLabSurfaces.stl"
    assert openfoam_snappy["installedArtifacts"]["snappyHexMeshDict"] == "system/snappyHexMeshDict"
    assert openfoam_snappy["starterGeometry"]["triSurface"] == "constant/triSurface/reviewedFlowLabSurfaces.stl"
    assert openfoam_snappy["starterGeometry"]["cadReviewed"] is False
    assert openfoam_snappy["starterGeometry"]["locationInMesh"] != [0, 0, 0]
    assert "inlet_inlet" in openfoam_snappy["boundaryPatchPlan"]["inlet"]
    assert "outlet_channel" in openfoam_snappy["boundaryPatchPlan"]["outlet"]
    assert "wall_inlet_left" in openfoam_snappy["boundaryPatchPlan"]["walls"]
    assert "wall_channel_front_back" in openfoam_snappy["boundaryPatchPlan"]["frontAndBack"]
    inlet_layer = next(layer for layer in openfoam_snappy["addLayersControls"]["layers"] if layer["patch"] == "wall_inlet_left")
    assert inlet_layer["sourceEdgeId"] == "inlet"
    assert inlet_layer["firstLayerThickness"] == pytest.approx(inlet_prism["targetFirstCellHeight"])
    snappy_readiness = {check["id"]: check["status"] for check in openfoam_snappy["readinessChecks"]}
    assert snappy_readiness["physical-groups-mapped"] == "pass"
    assert snappy_readiness["prism-layer-inputs"] == "warning"
    assert snappy_readiness["starter-trisurface-export"] == "pass"
    assert snappy_readiness["cad-surface-ready"] == "fail"
    assert su2_handoff["schema"] == "flowlab.su2_native_meshing_handoff.v1"
    assert su2_handoff["productionReady"] is False
    assert "inlet_inlet" in su2_handoff["markerPlan"]["inlet"]
    assert "outlet_channel" in su2_handoff["markerPlan"]["outlet"]
    assert "wall_inlet_left" in su2_handoff["markerPlan"]["wall"]
    assert su2_handoff["viscousLayerPlan"]["source"] == "mesh/prism_layer_plan.json"
    assert su2_handoff["viscousLayerPlan"]["edgeCount"] == 2
    assert su2_handoff["adaptationPlan"]["targetCount"] == 2
    assert "pressure-gradient" in su2_handoff["adaptationPlan"]["fieldIndicators"]
    su2_readiness = {check["id"]: check["status"] for check in su2_handoff["readinessChecks"]}
    assert su2_readiness["marker-map-exported"] == "pass"
    assert su2_readiness["viscous-layer-inputs"] == "warning"
    assert su2_readiness["native-su2-production-mesh"] == "fail"
    assert code_saturne_handoff["schema"] == "flowlab.code_saturne_native_meshing_handoff.v1"
    assert code_saturne_handoff["productionReady"] is False
    assert "fluid_inlet" in code_saturne_handoff["importPlan"]["volumeGroups"]
    assert "inlet_inlet" in code_saturne_handoff["importPlan"]["boundaryGroups"]
    assert code_saturne_handoff["prismLayerImportPlan"]["source"] == "mesh/prism_layer_plan.json"
    assert code_saturne_handoff["prismLayerImportPlan"]["edgeCount"] == 2
    assert code_saturne_handoff["adaptationPlan"]["targetCount"] == 2
    code_saturne_readiness = {check["id"]: check["status"] for check in code_saturne_handoff["readinessChecks"]}
    assert code_saturne_readiness["physical-groups-exported"] == "pass"
    assert code_saturne_readiness["prism-layer-import-inputs"] == "warning"
    assert code_saturne_readiness["native-code-saturne-production-mesh"] == "fail"
    snappy_template = bundle.files["mesh/openfoam_snappyHexMeshDict.template"]
    assert "review-only OpenFOAM native meshing template" in snappy_template
    assert "castellatedMeshControls" in snappy_template
    assert "addLayersControls" in snappy_template
    assert "wall_inlet_left" in snappy_template
    assert "firstLayerThickness" in snappy_template
    assert "reviewedFlowLabSurfaces.stl" in snappy_template
    assert "solid reviewedFlowLabSurfaces" in bundle.files["constant/triSurface/reviewedFlowLabSurfaces.stl"]
    assert "FlowLab-generated starter triSurface" in bundle.files["constant/triSurface/reviewedFlowLabSurfaces.stl"]
    assert "facet normal" in bundle.files["constant/triSurface/reviewedFlowLabSurfaces.stl"]
    assert "locationInMesh (0 0 0)" in snappy_template
    assert "locationInMesh (0 0 0)" not in bundle.files["system/snappyHexMeshDict"]
    assert "reviewedFlowLabSurfaces.stl" in bundle.files["system/snappyHexMeshDict"]
    assert "surfaceFeatureExtractDict" in bundle.files["mesh/openfoam_surfaceFeatureExtractDict.template"]
    assert "meshQualityDict" in bundle.files["mesh/openfoam_meshQualityDict.template"]
    assert "surfaceFeatureExtractDict" in bundle.files["system/surfaceFeatureExtractDict"]
    assert "meshQualityDict" in bundle.files["system/meshQualityDict"]
    assert bundle.mesh["quality"]["schema"] == "flowlab.mesh_quality.v1"
    assert quality["status"] == "ok"
    assert quality["productionReady"] is False
    assert quality["summary"]["cellCount"] == 39
    assert quality["summary"]["regionCount"] == 3
    assert quality["summary"]["invertedCellCount"] == 0
    assert quality["summary"]["degenerateCellCount"] == 0
    assert quality["summary"]["maxAspectRatio"] == pytest.approx(8.010003511)
    assert quality["summary"]["maxNonOrthogonalityDeg"] == pytest.approx(20.556045)
    assert quality["summary"]["maxSkewnessEstimate"] == pytest.approx(0.2284005)
    assert quality["thresholds"]["maxNonOrthogonalityDeg"] == 85.0
    assert quality["thresholds"]["maxSkewnessEstimate"] == 0.95
    assert production_mesh_plan["schema"] == "flowlab.production_mesh_plan.v1"
    assert production_mesh_plan["productionReady"] is False
    assert production_mesh_plan["meshClass"] == "flowlab-port-aware-starter-strip"
    assert "mesh/quality.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/prism_layer_plan.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/adaptation_plan.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/physical_groups.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/openfoam_snappy_handoff.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/openfoam_native_mesh_preflight.py" in production_mesh_plan["generatedEvidence"]
    assert "mesh/su2_native_meshing_handoff.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/code_saturne_native_meshing_handoff.json" in production_mesh_plan["generatedEvidence"]
    assert "mesh/openfoam_snappyHexMeshDict.template" in production_mesh_plan["generatedEvidence"]
    assert "constant/triSurface/reviewedFlowLabSurfaces.stl" in production_mesh_plan["generatedEvidence"]
    assert "system/snappyHexMeshDict" in production_mesh_plan["generatedEvidence"]
    assert "mesh/production_mesh_acceptance.json" in production_mesh_plan["generatedEvidence"]
    assert production_mesh_plan["counts"]["physicalRegionCount"] == 2
    assert production_mesh_plan["counts"]["connectorRegionCount"] == 1
    assert production_mesh_plan["counts"]["prismLayerEdgeCount"] == 2
    assert production_mesh_plan["counts"]["adaptationTargetCount"] == 2
    readiness = {check["id"]: check["status"] for check in production_mesh_plan["readinessChecks"]}
    assert readiness["port-aware-source-topology"] == "pass"
    assert readiness["source-mesh-quality-gate"] == "pass"
    assert readiness["solver-neutral-adaptation-plan"] == "pass"
    assert readiness["cad-quality-geometry-source"] == "fail"
    assert readiness["production-3d-volume-mesh"] == "fail"
    assert readiness["prism-layer-boundary-mesh"] == "fail"
    assert production_mesh_plan["blockingReasons"]
    assert native_meshing_plan["schema"] == "flowlab.native_meshing_plan.v1"
    assert native_meshing_plan["productionReady"] is False
    assert "mesh/gmsh_production_handoff.geo" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/physical_groups.json" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/openfoam_snappy_handoff.json" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/openfoam_native_mesh_preflight.py" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/su2_native_meshing_handoff.json" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/code_saturne_native_meshing_handoff.json" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/openfoam_snappyHexMeshDict.template" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/openfoam_surfaceFeatureExtractDict.template" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/openfoam_meshQualityDict.template" in native_meshing_plan["handoffArtifacts"]
    assert "constant/triSurface/reviewedFlowLabSurfaces.stl" in native_meshing_plan["handoffArtifacts"]
    assert "system/snappyHexMeshDict" in native_meshing_plan["handoffArtifacts"]
    assert "system/surfaceFeatureExtractDict" in native_meshing_plan["handoffArtifacts"]
    assert "system/meshQualityDict" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/adaptation_plan.json" in native_meshing_plan["handoffArtifacts"]
    assert "mesh/production_mesh_acceptance.json" in native_meshing_plan["handoffArtifacts"]
    assert native_meshing_plan["solverTargets"]["openfoam"]["qualityCommand"] == "checkMesh -allGeometry -allTopology"
    assert native_meshing_plan["solverTargets"]["openfoam"]["nativeMeshingHandoff"] == "mesh/openfoam_snappy_handoff.json"
    assert native_meshing_plan["solverTargets"]["su2"]["nativeMeshingHandoff"] == "mesh/su2_native_meshing_handoff.json"
    assert native_meshing_plan["solverTargets"]["codeSaturne"]["nativeMeshingHandoff"] == "mesh/code_saturne_native_meshing_handoff.json"
    native_readiness = {check["id"]: check["status"] for check in native_meshing_plan["readinessChecks"]}
    assert native_readiness["gmsh-handoff-script"] == "pass"
    assert native_readiness["physical-group-map"] == "pass"
    assert native_readiness["openfoam-snappy-handoff"] == "pass"
    assert native_readiness["openfoam-native-mesh-preflight"] == "pass"
    assert native_readiness["su2-native-meshing-handoff"] == "pass"
    assert native_readiness["code-saturne-native-meshing-handoff"] == "pass"
    assert native_readiness["cad-surface-import"] == "fail"
    assert native_readiness["boundary-layer-prism-controls"] == "warning"
    assert native_readiness["adaptation-target-controls"] == "warning"
    assert native_meshing_plan["prismLayerPlan"]["file"] == "mesh/prism_layer_plan.json"
    assert native_meshing_plan["prismLayerPlan"]["edgeCount"] == 2
    assert native_meshing_plan["adaptationPlan"]["file"] == "mesh/adaptation_plan.json"
    assert native_meshing_plan["adaptationPlan"]["targetCount"] == 2
    assert native_meshing_plan["blockingReasons"]
    assert production_mesh_acceptance["schema"] == "flowlab.production_mesh_acceptance.v1"
    assert production_mesh_acceptance["productionReady"] is False
    assert production_mesh_acceptance["approvalStatus"] == "blocked"
    assert production_mesh_acceptance["sourceArtifacts"]["productionMeshPlan"] == "mesh/production_mesh_plan.json"
    assert production_mesh_acceptance["sourceArtifacts"]["nativeMeshingPlan"] == "mesh/native_meshing_plan.json"
    assert production_mesh_acceptance["sourceArtifacts"]["adaptationPlan"] == "mesh/adaptation_plan.json"
    assert production_mesh_acceptance["sourceArtifacts"]["openfoamNativeMeshPreflight"] == "mesh/openfoam_native_mesh_preflight.py"
    assert production_mesh_acceptance["sourceArtifacts"]["su2NativeMeshingHandoff"] == "mesh/su2_native_meshing_handoff.json"
    assert production_mesh_acceptance["sourceArtifacts"]["codeSaturneNativeMeshingHandoff"] == "mesh/code_saturne_native_meshing_handoff.json"
    assert production_mesh_acceptance["solverAcceptance"]["openfoam"]["status"] == "blocked"
    assert production_mesh_acceptance["solverAcceptance"]["su2"]["status"] == "blocked"
    assert production_mesh_acceptance["solverAcceptance"]["codeSaturne"]["status"] == "blocked"
    assert production_mesh_acceptance["nativeQualityEvidence"]["schema"] == "flowlab.native_mesh_quality_evidence.v1"
    assert production_mesh_acceptance["nativeQualityEvidence"]["productionReady"] is False
    assert production_mesh_acceptance["nativeQualityEvidence"]["status"] == "missing-native-quality-reports"
    assert "wall-distance or y-plus field for wall-bounded cases" in production_mesh_acceptance["nativeQualityEvidence"]["sharedRequiredEvidence"]
    native_reports = production_mesh_acceptance["nativeQualityEvidence"]["solverReports"]
    assert native_reports["openfoam"]["status"] == "missing"
    assert "mesh/openfoam_native_mesh_preflight.py" in native_reports["openfoam"]["currentEvidence"]
    assert "constant/triSurface/reviewedFlowLabSurfaces.stl" in native_reports["openfoam"]["currentEvidence"]
    assert "system/snappyHexMeshDict" in native_reports["openfoam"]["currentEvidence"]
    assert "yPlusMinMeanMax" in native_reports["openfoam"]["requiredMetrics"]
    assert native_reports["su2"]["commands"]
    assert native_reports["codeSaturne"]["commands"]
    assert "mesh/su2_native_meshing_handoff.json" in production_mesh_acceptance["solverAcceptance"]["su2"]["currentEvidence"]
    assert "mesh/code_saturne_native_meshing_handoff.json" in production_mesh_acceptance["solverAcceptance"]["codeSaturne"]["currentEvidence"]
    acceptance_readiness = {check["id"]: check["status"] for check in production_mesh_acceptance["acceptanceCriteria"]}
    assert acceptance_readiness["source-mesh-traceability"] == "pass"
    assert acceptance_readiness["solver-handoff-artifacts"] == "pass"
    assert acceptance_readiness["cad-geometry-source"] == "fail"
    assert acceptance_readiness["native-3d-volume-mesh"] == "fail"
    assert acceptance_readiness["boundary-layer-prism-mesh"] == "fail"
    assert acceptance_readiness["adapted-refinement-evidence"] == "fail"
    assert acceptance_readiness["solver-native-quality-evidence"] == "fail"
    assert production_mesh_acceptance["blockingReasons"]
    assert "FlowLab review-only native meshing handoff" in bundle.files["mesh/gmsh_production_handoff.geo"]
    assert "SetFactory(\"OpenCASCADE\")" in bundle.files["mesh/gmsh_production_handoff.geo"]
    assert "Physical Surface(\"inlet_inlet\")" in bundle.files["mesh/gmsh_production_handoff.geo"]
    assert "DATASET UNSTRUCTURED_GRID" in bundle.files["mesh/flowlab_mesh.vtk"]
    assert "<VTKFile type=\"UnstructuredGrid\"" in bundle.files["mesh/flowlab_mesh.vtu"]
    assert "NDIME= 2" in bundle.files["mesh/flowlab_mesh.su2"]
    assert "NELEM= 39" in bundle.files["mesh/flowlab_mesh.su2"]
    assert "MARKER_TAG= inlet_inlet" in bundle.files["mesh/flowlab_mesh.su2"]
    assert "MARKER_TAG= wall_channel_right" in bundle.files["mesh/flowlab_mesh.su2"]
    assert "MARKER_TAG= wall_connector_throat_left" in bundle.files["mesh/flowlab_mesh.su2"]
    assert "$MeshFormat" in bundle.files["mesh/flowlab_mesh.msh"]
    assert "$PhysicalNames" in bundle.files["mesh/flowlab_mesh.msh"]
    assert '"inlet_inlet"' in bundle.files["mesh/flowlab_mesh.msh"]
    assert '"wall_channel_right"' in bundle.files["mesh/flowlab_mesh.msh"]


def test_mesh_bundle_materializes_reviewed_uploaded_stl_and_metadata() -> None:
    project = _venturi_project()
    project["solver"] = {
        "meshResolution": "coarse",
        "reviewedGeometry": {
            "sourceType": "uploaded-stl",
            "cadReviewed": True,
            "reviewedAt": "2026-06-15T00:00:00Z",
            "reviewNotes": "Reviewed watertight STL from CAD cleanup.",
            "stlText": _valid_reviewed_stl(),
            "boundaryTags": [
                {"id": "bt-inlet", "role": "inlet", "patchName": "reviewed_inlet", "label": "Reviewed inlet"},
                {"id": "bt-outlet", "role": "outlet", "patchName": "reviewed_outlet", "label": "Reviewed outlet"},
                {"id": "bt-wall", "role": "wall", "patchName": "reviewed_wall", "label": "Reviewed wall"},
                {
                    "id": "bt-interface",
                    "role": "interface",
                    "patchName": "reviewed_interface",
                    "label": "Reviewed interface",
                },
            ],
        },
    }

    bundle = generate_mesh_bundle(project)
    handoff = json.loads(bundle.files["mesh/openfoam_snappy_handoff.json"])
    acceptance = json.loads(bundle.files["mesh/production_mesh_acceptance.json"])
    readiness = {check["id"]: check["status"] for check in handoff["readinessChecks"]}
    criteria = {check["id"]: check["status"] for check in acceptance["acceptanceCriteria"]}

    assert bundle.files["constant/triSurface/reviewedFlowLabSurfaces.stl"] == _valid_reviewed_stl()
    assert handoff["starterGeometry"]["sourceType"] == "uploaded-stl"
    assert handoff["starterGeometry"]["cadReviewed"] is True
    assert handoff["starterGeometry"]["reviewedAt"] == "2026-06-15T00:00:00Z"
    assert handoff["starterGeometry"]["reviewNotes"] == "Reviewed watertight STL from CAD cleanup."
    assert handoff["starterGeometry"]["validation"]["status"] == "pass"
    assert handoff["starterGeometry"]["stlMetadata"]["triangleCount"] == 1
    assert handoff["starterGeometry"]["stlMetadata"]["bounds"] == {
        "min": [0.0, 0.0, 0.0],
        "max": [1.0, 1.0, 0.0],
    }
    assert handoff["starterGeometry"]["stlMetadata"]["asciiValidation"]["status"] == "pass"
    assert handoff["starterGeometry"]["stlMetadata"]["watertightCheck"]["status"] == "warning"
    assert handoff["starterGeometry"]["stlMetadata"]["watertightCheck"]["openEdgeCount"] == 3
    assert handoff["reviewedBoundaryTags"]["status"] == "pass"
    assert handoff["reviewedBoundaryTags"]["complete"] is True
    assert handoff["reviewedBoundaryTags"]["missingRequiredRoles"] == []
    assert handoff["reviewedBoundaryTags"]["requiredRoles"] == ["inlet", "outlet", "wall"]
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-boundary-tags"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == ["reviewed_outlet"]
    assert handoff["boundaryPatchPlan"]["walls"] == ["reviewed_wall"]
    assert handoff["boundaryPatchPlan"]["interfaces"] == ["reviewed_interface"]
    assert "reviewed_wall" in bundle.files["system/snappyHexMeshDict"]
    assert "wall_inlet_left" not in bundle.files["system/snappyHexMeshDict"]
    assert readiness["cad-surface-ready"] == "pass"
    assert readiness["reviewed-boundary-tags"] == "pass"
    assert criteria["cad-geometry-source"] == "pass"
    assert acceptance["productionReady"] is False


def test_reviewed_stl_missing_required_boundary_tags_marks_handoff_not_ready() -> None:
    project = _venturi_project()
    project["solver"] = {
        "meshResolution": "coarse",
        "reviewedGeometry": {
            "sourceType": "uploaded-stl",
            "cadReviewed": True,
            "reviewedAt": "2026-06-15T00:00:00Z",
            "reviewNotes": "Reviewed STL without enough patch tagging.",
            "stlText": _valid_reviewed_stl(),
            "boundaryTags": [
                {"id": "bt-inlet", "role": "inlet", "patchName": "reviewed_inlet", "label": "Reviewed inlet"},
            ],
        },
    }

    bundle = generate_mesh_bundle(project)
    handoff = json.loads(bundle.files["mesh/openfoam_snappy_handoff.json"])
    readiness = {check["id"]: check["status"] for check in handoff["readinessChecks"]}

    assert handoff["reviewedBoundaryTags"]["status"] == "fail"
    assert handoff["reviewedBoundaryTags"]["complete"] is False
    assert handoff["reviewedBoundaryTags"]["missingRequiredRoles"] == ["outlet", "wall"]
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-boundary-tags"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == []
    assert handoff["boundaryPatchPlan"]["walls"] == []
    assert readiness["cad-surface-ready"] == "fail"
    assert readiness["reviewed-boundary-tags"] == "fail"


@pytest.mark.parametrize(
    ("reviewed_geometry", "message"),
    [
        (
            {"sourceType": "local-stl-path", "cadReviewed": True, "stlPath": "../unsafe.stl"},
            "safe relative .stl",
        ),
        (
            {"sourceType": "local-stl-path", "cadReviewed": True, "stlPath": "geometry/not-stl.txt"},
            "safe relative .stl",
        ),
        (
            {"sourceType": "uploaded-stl", "cadReviewed": True, "stlText": "solid bad\nendsolid bad\n"},
            "valid ASCII STL",
        ),
    ],
)
def test_mesh_bundle_rejects_unsafe_or_malformed_reviewed_stl(reviewed_geometry: dict, message: str) -> None:
    project = _venturi_project()
    project["solver"] = {"meshResolution": "coarse", "reviewedGeometry": reviewed_geometry}

    with pytest.raises(ValueError, match=message):
        generate_mesh_bundle(project)


def test_mesh_bundle_materializes_multi_surface_reviewed_stl_patch_handoff() -> None:
    bundle = generate_mesh_bundle(_multi_surface_project())
    handoff = json.loads(bundle.files["mesh/openfoam_snappy_handoff.json"])
    snappy_dict = bundle.files["system/snappyHexMeshDict"]

    assert bundle.files["constant/triSurface/inlet.stl"] == _reviewed_surface_stl("inlet")
    assert bundle.files["constant/triSurface/outlet.stl"] == _reviewed_surface_stl("outlet", x_offset=2.0)
    assert bundle.files["constant/triSurface/walls.stl"] == _reviewed_surface_stl("walls", x_offset=4.0)
    assert bundle.files["constant/triSurface/interface_probe.stl"] == _reviewed_surface_stl(
        "interface_probe", x_offset=6.0
    )
    assert handoff["starterGeometry"]["triSurface"] == "constant/triSurface/reviewedFlowLabSurfaces.stl"
    assert handoff["starterGeometry"]["sourceType"] == "multi-surface-stl"
    assert handoff["reviewedGeometry"]["sourceType"] == "multi-surface-stl"
    assert handoff["reviewedGeometry"]["cadReviewed"] is True
    surfaces = {surface["surfaceName"]: surface for surface in handoff["reviewedGeometry"]["surfaces"]}
    assert set(surfaces) == {"inlet", "outlet", "walls", "interface_probe"}
    assert surfaces["inlet"]["triSurface"] == "constant/triSurface/inlet.stl"
    assert surfaces["inlet"]["role"] == "inlet"
    assert surfaces["inlet"]["patchName"] == "reviewed_inlet"
    assert surfaces["inlet"]["stlMetadata"]["triangleCount"] == 1
    assert surfaces["inlet"]["stlMetadata"]["asciiValidation"]["status"] == "pass"
    assert surfaces["walls"]["triSurface"] == "constant/triSurface/walls.stl"
    assert surfaces["walls"]["role"] == "wall"
    assert surfaces["walls"]["patchInfo"]["type"] == "wall"
    assert surfaces["outlet"]["patchInfo"]["type"] == "patch"
    assert surfaces["interface_probe"]["patchInfo"]["type"] == "patch"
    assert handoff["boundaryCoverage"] == {
        "requiredRoles": ["inlet", "outlet", "wall"],
        "rolesPresent": ["inlet", "interface", "outlet", "wall"],
        "missingRequiredRoles": [],
        "complete": True,
        "status": "pass",
    }
    assert handoff["boundaryPatchPlan"]["source"] == "reviewed-surfaces"
    assert handoff["boundaryPatchPlan"]["inlet"] == ["reviewed_inlet"]
    assert handoff["boundaryPatchPlan"]["outlet"] == ["reviewed_outlet"]
    assert handoff["boundaryPatchPlan"]["walls"] == ["reviewed_walls"]
    assert handoff["boundaryPatchPlan"]["interfaces"] == ["reviewed_interface"]

    for file_name, patch_name, patch_type in [
        ("inlet.stl", "reviewed_inlet", "patch"),
        ("outlet.stl", "reviewed_outlet", "patch"),
        ("walls.stl", "reviewed_walls", "wall"),
        ("interface_probe.stl", "reviewed_interface", "patch"),
    ]:
        assert file_name in snappy_dict
        assert patch_name in snappy_dict
        assert f"{patch_name}\n" in snappy_dict or f"{patch_name} " in snappy_dict
        assert f"type {patch_type};" in snappy_dict
    assert "geometry" in snappy_dict
    assert "refinementSurfaces" in snappy_dict


def test_openfoam_case_generates_per_surface_boundary_condition_fields() -> None:
    project = _multi_surface_project()
    project["solver"]["reviewedGeometry"] = _multi_surface_reviewed_geometry_with_boundary_conditions()
    request = CaseRequest(project=project, solver="openfoam", advancedMode="multiphase-vof")

    case = adapters.generate_case(request)

    handoff = json.loads(case.files["mesh/openfoam_snappy_handoff.json"])
    boundary_coverage = handoff["boundaryConditionCoverage"]
    assert boundary_coverage["status"] == "pass"
    assert boundary_coverage["complete"] is True
    assert boundary_coverage["missingPatchNames"] == []
    assert boundary_coverage["patchesWithConditions"] == [
        "reviewed_inlet",
        "reviewed_interface",
        "reviewed_outlet",
        "reviewed_walls",
    ]
    surfaces = {surface["patchName"]: surface for surface in handoff["reviewedGeometry"]["surfaces"]}
    assert surfaces["reviewed_inlet"]["boundaryCondition"]["type"] == "velocity-inlet"
    assert surfaces["reviewed_outlet"]["boundaryCondition"]["type"] == "pressure-outlet"
    assert surfaces["reviewed_walls"]["boundaryCondition"]["type"] == "temperature-wall"
    assert surfaces["reviewed_interface"]["boundaryCondition"]["status"] == "placeholder"

    for field_name in ["0/U", "0/p", "0/T", "0/alpha.water"]:
        assert field_name in case.files
        for patch_name in boundary_coverage["requiredPatchNames"]:
            assert patch_name in case.files[field_name]

    _assert_patch_block_contains(case.files["0/U"], "reviewed_inlet", "type            fixedValue", "uniform (1.25 0 0)")
    _assert_patch_block_contains(case.files["0/p"], "reviewed_inlet", "type            zeroGradient")
    _assert_patch_block_contains(case.files["0/U"], "reviewed_outlet", "type            zeroGradient")
    _assert_patch_block_contains(case.files["0/p"], "reviewed_outlet", "type            fixedValue", "uniform 101325")
    _assert_patch_block_contains(case.files["0/U"], "reviewed_walls", "type            noSlip")
    _assert_patch_block_contains(case.files["0/T"], "reviewed_walls", "type            fixedValue", "uniform 315.15")
    _assert_patch_block_contains(case.files["0/alpha.water"], "reviewed_inlet", "type            fixedValue", "uniform 1")
    _assert_patch_block_contains(case.files["0/alpha.water"], "reviewed_outlet", "type            inletOutlet")


@pytest.mark.parametrize(
    ("reviewed_geometry", "message"),
    [
        (_multi_surface_reviewed_geometry(duplicate_patch=True), "duplicate|duplicated"),
        (_multi_surface_reviewed_geometry(unsafe_patch=True), "OpenFOAM-safe|safe patch"),
    ],
)
def test_mesh_bundle_rejects_duplicate_or_unsafe_multi_surface_patch_names(
    reviewed_geometry: dict, message: str
) -> None:
    project = _venturi_project()
    project["solver"] = {"meshResolution": "coarse", "reviewedGeometry": reviewed_geometry}

    with pytest.raises(ValueError, match=message):
        generate_mesh_bundle(project)


def test_openfoam_native_mesh_preflight_runs_against_generated_handoff(tmp_path: Path) -> None:
    bundle = generate_mesh_bundle(_venturi_project())
    _write_bundle_files(tmp_path, bundle.files)

    completed = subprocess.run(
        [sys.executable, "mesh/openfoam_native_mesh_preflight.py"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    report_path = tmp_path / "mesh/openfoam_native_mesh_preflight_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = {check["id"]: check for check in report["checks"]}
    assert report["schema"] == "flowlab.openfoam_native_mesh_preflight_report.v1"
    assert report["productionReady"] is False
    assert checks["triSurface"]["status"] == "pass"
    assert checks["snappyHexMeshDict"]["status"] == "pass"
    assert checks["surfaceFeatureExtractDict"]["status"] == "pass"
    assert checks["meshQualityDict"]["status"] == "pass"
    assert checks["location-in-mesh"]["status"] == "pass"
    assert checks["native-quality-evidence"]["status"] == "fail"
    assert "snappyHexMesh -overwrite" in report["commandsToRunAfterPassingPreflight"]
    assert any("solver-neutral adaptation plan" in entry for entry in bundle.provenance)
    assert any("not a CAD-quality" in entry for entry in bundle.provenance)
    assert any("production mesh plan" in entry for entry in bundle.provenance)
    assert any("native meshing handoff" in entry for entry in bundle.provenance)


def test_result_field_summary_reports_point_and_cell_field_stats() -> None:
    summary = summarize_vtk_result_text(
        """# vtk DataFile Version 3.0
field summary fixture
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
VECTORS U float
1 0 0 2 0 0 3 0 0 4 0 0
CELL_DATA 1
FIELD attributes 2
wallHeatFlux 1 1 float
125
wallShear 3 1 float
0 3 4
"""
    )

    assert summary["schema"] == "flowlab.result_field_summary.v1"
    assert summary["pointCount"] == 4
    assert summary["cellCount"] == 1
    fields = {(field["name"], field["location"], field["kind"]): field for field in summary["fields"]}
    assert fields[("p", "point", "scalar")]["mean"] == pytest.approx(2.5)
    assert fields[("p", "point", "scalar")]["stdDev"] == pytest.approx(1.11803398875)
    assert fields[("p", "point", "scalar")]["p50"] == pytest.approx(2.5)
    assert fields[("p", "point", "scalar")]["p95"] == pytest.approx(3.85)
    assert fields[("U", "point", "vector-magnitude")]["max"] == pytest.approx(4.0)
    assert fields[("U", "point", "vector-magnitude")]["p95"] == pytest.approx(3.85)
    assert fields[("wallHeatFlux", "cell", "scalar")]["min"] == pytest.approx(125.0)
    assert fields[("wallShear", "cell", "vector-magnitude")]["mean"] == pytest.approx(5.0)
    assert fields[("wallShear", "cell", "vector-magnitude")]["stdDev"] == pytest.approx(0.0)


def test_result_preview_returns_bounded_geometry_and_field_samples() -> None:
    preview = preview_vtk_result_text(
        """# vtk DataFile Version 3.0
preview fixture
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 6 float
0 0 0
1 0 0
1 1 0
0 1 0
2 0 0
2 1 0
CELLS 2 10
4 0 1 2 3
4 1 4 5 2
CELL_TYPES 2
9
9
POINT_DATA 6
SCALARS p float 1
LOOKUP_TABLE default
10 20 30 40 50 60
VECTORS U float
1 0 0 2 0 0 3 0 0 4 0 0 5 0 0 6 0 0
CELL_DATA 2
SCALARS wallHeatFlux float 1
LOOKUP_TABLE default
100 200
""",
        point_limit=4,
        cell_limit=1,
    )

    assert preview["schema"] == "flowlab.result_preview.v1"
    assert preview["sourcePointCount"] == 6
    assert preview["sourceCellCount"] == 2
    assert preview["pointCount"] == 4
    assert preview["cellCount"] == 1
    assert preview["truncated"] is True
    assert preview["pointIndices"] == [0, 1, 2, 3]
    assert preview["cells"] == [[0, 1, 2, 3]]
    point_samples = {sample["name"]: sample for sample in preview["fieldSamples"]["point"]}
    cell_samples = {sample["name"]: sample for sample in preview["fieldSamples"]["cell"]}
    assert point_samples["p"]["values"] == [10.0, 20.0, 30.0, 40.0]
    assert point_samples["U"]["magnitudes"] == [1.0, 2.0, 3.0, 4.0]
    assert cell_samples["wallHeatFlux"]["values"] == [100.0]


def test_mesh_controls_apply_boundary_layers_and_edge_refinement() -> None:
    project = _venturi_project()
    project["solver"]["meshControls"] = {
        "longitudinalRefinement": 2,
        "boundaryLayerLayers": 1,
        "boundaryLayerGrowthRate": 1.4,
        "targetYPlus": 20,
        "refinementRegions": [{"edgeId": "channel", "factor": 2, "reason": "downstream-mixer-wake"}],
        "quality": {"maxAspectRatio": 100},
    }

    bundle = generate_mesh_bundle(project)
    controls = json.loads(bundle.files["mesh/controls.json"])
    boundary_layer_plan = json.loads(bundle.files["mesh/boundary_layer_plan.json"])
    inlet = next(region for region in bundle.mesh["regions"] if region["edgeId"] == "inlet")
    channel = next(region for region in bundle.mesh["regions"] if region["edgeId"] == "channel")

    assert controls["boundaryLayerLayers"] == 1
    assert controls["longitudinalRefinement"] == 2
    assert controls["transverseFractions"] == pytest.approx([0.0, 0.208333333, 0.791666667, 1.0])
    assert inlet["segmentCount"] == 12
    assert inlet["transverseDivisions"] == 3
    assert inlet["cellCount"] == 36
    assert channel["segmentCount"] == 24
    assert channel["refinementFactor"] == 2
    assert channel["cellCount"] == 72
    assert len(bundle.mesh["cells"]) == 111
    assert boundary_layer_plan["targetYPlus"] == 20.0
    channel_plan = next(edge for edge in boundary_layer_plan["edges"] if edge["edgeId"] == "channel")
    assert channel_plan["hydraulicDiameter"] == pytest.approx(0.117333333)
    assert channel_plan["starterToTargetRatio"] > 1
    assert "mesh/controls.json" in bundle.files
    assert "MARKER_TAG= inlet_channel\nMARKER_ELEMS= 3" in bundle.files["mesh/flowlab_mesh.su2"]
    assert any("boundary-layer strip controls" in entry for entry in bundle.provenance)


def test_mesh_controls_export_adaptive_mesh_plan() -> None:
    project = _venturi_project()
    project["solver"]["adaptiveMesh"] = {
        "enabled": True,
        "targetField": "pressure",
        "errorMode": "relative-error",
        "adaptEvery": 3,
        "maxCells": 500000,
        "minCellSize": 0.0005,
        "maxCellSize": 0.05,
        "gradation": 1.25,
        "writeAdaptedState": False,
    }

    bundle = generate_mesh_bundle(project)
    controls = json.loads(bundle.files["mesh/controls.json"])
    adaptation_plan = json.loads(bundle.files["mesh/adaptation_plan.json"])

    assert controls["adaptiveMesh"] == {
        "adaptEvery": 3,
        "enabled": True,
        "errorMode": "relative-error",
        "gradation": 1.25,
        "liveRemeshing": False,
        "maxCellSize": 0.05,
        "maxCells": 500000,
        "minCellSize": 0.0005,
        "targetField": "pressure",
        "writeAdaptedState": False,
    }
    assert adaptation_plan["adaptiveMeshPlan"]["enabled"] is True
    assert adaptation_plan["adaptiveMeshPlan"]["exportOnly"] is True
    assert adaptation_plan["controlsSummary"]["adaptiveMesh"]["targetField"] == "pressure"
    solver_field_check = next(check for check in adaptation_plan["readinessChecks"] if check["id"] == "solver-field-indicators")
    assert solver_field_check["status"] == "warning"
    assert adaptation_plan["productionReady"] is False


def test_mesh_quality_warns_for_source_skewness_thresholds() -> None:
    project = _venturi_project()
    project["solver"]["meshControls"] = {
        "quality": {"maxNonOrthogonalityDeg": 10, "maxSkewnessEstimate": 0.1},
    }

    bundle = generate_mesh_bundle(project)
    quality = json.loads(bundle.files["mesh/quality.json"])

    assert quality["status"] == "warning"
    assert quality["summary"]["maxNonOrthogonalityDeg"] == pytest.approx(20.556045)
    assert quality["summary"]["maxSkewnessEstimate"] == pytest.approx(0.2284005)
    assert any("source non-orthogonality" in warning for warning in quality["warnings"])
    assert any("source skewness" in warning for warning in quality["warnings"])


def test_mesh_feature_refinement_clusters_venturi_stations() -> None:
    project = _venturi_project()
    project["solver"]["meshControls"] = {
        "featureRefinement": {"enabled": True, "factor": 2, "clusterStrength": 0.65},
    }

    bundle = generate_mesh_bundle(project)
    controls = json.loads(bundle.files["mesh/controls.json"])
    refinement_plan = json.loads(bundle.files["mesh/refinement_plan.json"])
    inlet = next(region for region in bundle.mesh["regions"] if region["edgeId"] == "inlet")
    channel = next(region for region in bundle.mesh["regions"] if region["edgeId"] == "channel")

    assert controls["featureRefinement"]["enabled"] is True
    assert controls["featureRefinement"]["factor"] == 2
    assert inlet["segmentCount"] == 12
    assert inlet["featureRefinement"] == {
        "enabled": True,
        "featureType": "venturi-throat",
        "factor": 2,
        "clusterStrength": 0.65,
        "targetStation": 0.5,
    }
    assert channel["segmentCount"] == 6
    assert channel["featureRefinement"]["enabled"] is False
    assert len(bundle.mesh["cells"]) == 57
    assert inlet["stationFractions"][6] == pytest.approx(0.5)
    assert inlet["stationFractions"][7] - inlet["stationFractions"][6] < inlet["stationFractions"][1] - inlet["stationFractions"][0]
    assert refinement_plan["regions"][0]["featureRefinement"]["featureType"] == "venturi-throat"
    assert "Feature refinement clusters source-mesh stations" in refinement_plan["notes"][0]
    assert any("feature-aware longitudinal refinement" in entry for entry in bundle.provenance)


def test_mesh_bundle_uses_rotated_port_endpoints() -> None:
    project = _venturi_project()
    project["nodes"]["source"].update({"type": "source", "rotation": 90, "position": {"x": 100, "y": 100}})
    project["nodes"]["throat"].update({"type": "pump", "rotation": 180, "position": {"x": 220, "y": 180}})
    project["edges"]["inlet"].update({"fromPort": "north", "toPort": "south"})

    bundle = generate_mesh_bundle(project)
    region = next(item for item in bundle.mesh["regions"] if item["edgeId"] == "inlet")
    transverse_points = region["transversePointCount"]
    first_station = bundle.mesh["points"][region["pointStart"] : region["pointStart"] + transverse_points]
    last_station = bundle.mesh["points"][region["pointStart"] + region["pointCount"] - transverse_points : region["pointStart"] + region["pointCount"]]
    first_center = [sum(point[axis] for point in first_station) / len(first_station) for axis in range(3)]
    last_center = [sum(point[axis] for point in last_station) / len(last_station) for axis in range(3)]

    assert region["fromPort"] == "north"
    assert region["toPort"] == "south"
    assert region["start"] == pytest.approx([127.0, 100.0, 0.0])
    assert region["end"] == pytest.approx([220.0, 151.0, 0.0])
    assert first_center == pytest.approx(region["start"])
    assert last_center == pytest.approx(region["end"])
    assert any("port-to-port" in entry for entry in bundle.provenance)


def test_mesh_exports_openfoam_polymesh_from_port_geometry() -> None:
    bundle = generate_mesh_bundle(_venturi_project())
    files = mesh_to_openfoam_polymesh(bundle.mesh)

    assert {
        "constant/polyMesh/points",
        "constant/polyMesh/faces",
        "constant/polyMesh/owner",
        "constant/polyMesh/neighbour",
        "constant/polyMesh/boundary",
    }.issubset(files)
    assert "vectorField" in files["constant/polyMesh/points"]
    assert "faceList" in files["constant/polyMesh/faces"]
    assert "polyBoundaryMesh" in files["constant/polyMesh/boundary"]
    assert "inlet" in files["constant/polyMesh/boundary"]
    assert "outlet" in files["constant/polyMesh/boundary"]
    assert "walls" in files["constant/polyMesh/boundary"]
    assert "frontAndBack" in files["constant/polyMesh/boundary"]


def test_mesh_exports_openfoam_cht_region_polymesh_with_mapped_interfaces() -> None:
    bundle = generate_mesh_bundle(_venturi_project())
    files = mesh_to_openfoam_cht_region_polymesh(bundle.mesh)

    assert {
        "constant/flowlab_cht_interface.json",
        "constant/fluid/polyMesh/points",
        "constant/fluid/polyMesh/faces",
        "constant/fluid/polyMesh/owner",
        "constant/fluid/polyMesh/neighbour",
        "constant/fluid/polyMesh/boundary",
        "constant/solid/polyMesh/points",
        "constant/solid/polyMesh/faces",
        "constant/solid/polyMesh/owner",
        "constant/solid/polyMesh/neighbour",
        "constant/solid/polyMesh/boundary",
    }.issubset(files)
    assert "fluid_to_solid" in files["constant/fluid/polyMesh/boundary"]
    assert "type            mappedWall;" in files["constant/fluid/polyMesh/boundary"]
    assert "neighbourRegion solid;" in files["constant/fluid/polyMesh/boundary"]
    assert "neighbourPatch solid_to_fluid;" in files["constant/fluid/polyMesh/boundary"]
    assert "solid_to_fluid" in files["constant/solid/polyMesh/boundary"]
    assert "neighbourRegion fluid;" in files["constant/solid/polyMesh/boundary"]
    manifest = json.loads(files["constant/flowlab_cht_interface.json"])
    assert manifest["schema"] == "flowlab.openfoam_cht_interface.v1"
    assert manifest["productionReady"] is False
    assert manifest["patches"]["fluid"]["faceCount"] == manifest["patches"]["solid"]["faceCount"]
    assert manifest["patches"]["fluid"]["neighbourRegion"] == "solid"
    assert manifest["patches"]["solid"]["neighbourPatch"] == "fluid_to_solid"
    assert manifest["sourceMesh"]["cellCount"] == bundle.mesh["quality"]["summary"]["cellCount"]
    assert manifest["sourceMesh"]["boundaryLayerLayers"] == 1
    assert manifest["sourceMesh"]["boundaryLayerPlanSchema"] == "flowlab.boundary_layer_plan.v1"
    assert manifest["sourceMesh"]["prismLayerPlanSchema"] == "flowlab.prism_layer_plan.v1"
    assert manifest["sourceMesh"]["prismLayerEdgeCount"] == 2
    assert manifest["prismLayerPlan"]["file"] == "mesh/prism_layer_plan.json"
    assert manifest["prismLayerPlan"]["productionReady"] is False
    assert manifest["interfaceApproximation"] == "outer-wall-offset-starter-sleeve"
    assert manifest["solidJacket"]["strategy"] == "outer-wall-offset-starter-sleeve"
    assert manifest["solidJacket"]["nonOverlapping"] is True
    assert manifest["solidJacket"]["innerInterfaceFaceCount"] == manifest["patches"]["fluid"]["faceCount"]
    assert "solid_outer_wall" in files["constant/solid/polyMesh/boundary"]
    assert any(check["id"] == "non-overlapping-solid-jacket" and check["status"] == "pass" for check in manifest["readinessChecks"])
    assert any(check["id"] == "cht-boundary-layer-evidence" and check["status"] == "fail" for check in manifest["readinessChecks"])
    assert any("prism_layer_plan.json" in reason for reason in manifest["blockingReasons"])


def test_mesh_generation_fails_closed_without_connected_geometry() -> None:
    with pytest.raises(ValueError, match="needs at least one edge"):
        generate_mesh_bundle({"name": "empty", "nodes": {}, "edges": {}})


def test_mesh_generation_rejects_unsupported_geometry() -> None:
    project = _venturi_project()
    project["edges"]["channel"]["shape"] = {"kind": "triangle", "height": 0.1}

    with pytest.raises(ValueError, match="Unsupported edge shape"):
        generate_mesh_bundle(project)


def test_mesh_generation_rejects_unsupported_geometry() -> None:
    project = _venturi_project()
    project["edges"]["channel"]["shape"] = {"kind": "triangle", "height": 0.1}

    with pytest.raises(ValueError, match="Unsupported edge shape"):
        generate_mesh_bundle(project)


def test_mesh_quality_fails_closed_for_degenerate_port_span() -> None:
    project = {
        "name": "Degenerate mesh",
        "nodes": {
            "source": {"id": "source", "type": "source", "position": {"x": 0, "y": 0}},
            "sink": {"id": "sink", "type": "sink", "position": {"x": 54, "y": 0}},
        },
        "edges": {
            "pipe": {
                "id": "pipe",
                "type": "pipe",
                "from": "source",
                "to": "sink",
                "fromPort": "outlet",
                "toPort": "inlet",
                "shape": {"kind": "circular", "diameter": 0.1},
            }
        },
    }

    with pytest.raises(ValueError, match="FlowLab mesh quality check failed"):
        generate_mesh_bundle(project)


def test_parse_fixture_legacy_vtk_result() -> None:
    fixture = Path(__file__).parents[2] / "public" / "fixtures" / "venturi-result.vtk"
    parsed = parse_vtk_result(fixture.read_text())

    assert parsed["format"] == "legacy-vtk-ascii-v1"
    assert len(parsed["points"]) == 8
    assert len(parsed["cells"]) == 3
    assert parsed["pointData"]["scalars"]["pressure"][4] == 72000
    assert parsed["pointData"]["scalars"]["phase_fraction"][4] == 0.75
    assert parsed["pointData"]["vectors"]["velocity"][4] == [7.2, 0.0, 0.0]
    assert {"pressure", "velocity", "temperature", "phase_fraction", "residuals"}.issubset(parsed["fields"])


def test_parse_legacy_vtk_accepts_common_linear_cell_types() -> None:
    vtk = """# vtk DataFile Version 3.0
mixed-linear-cells
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0
1 0 0
1 1 0
0 1 0
0 0 1
1 0 1
1 1 1
0 1 1
CELLS 4 22
3 0 1 2
4 0 1 2 4
6 0 1 2 4 5 6
5 0 1 2 3 7
CELL_TYPES 4
5 10 13 14
CELL_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
1 2 3 4
"""
    parsed = parse_vtk_result(vtk)

    assert parsed["cellTypes"] == [5, 10, 13, 14]
    assert parsed["cells"][0] == [0, 1, 2]
    assert parsed["cells"][2] == [0, 1, 2, 4, 5, 6]
    assert parsed["cellData"]["scalars"]["pressure"] == [1.0, 2.0, 3.0, 4.0]


def test_parse_legacy_vtk_accepts_openfoam_polydata_patch() -> None:
    vtk = """# vtk DataFile Version 2.0
inlet
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
FIELD attributes 2
p 1 1 float
101325
U 3 1 float
2 0 0
POINT_DATA 4
FIELD attributes 2
p 1 4 float
101325 101300 101250 101200
U 3 4 float
2 0 0 2.5 0 0 3 0 0 3.5 0 0
"""
    parsed = parse_vtk_result(vtk)

    assert parsed["format"] == "legacy-vtk-polydata-ascii-v1"
    assert parsed["cellTypes"] == [7]
    assert parsed["cells"] == [[0, 1, 2, 3]]
    assert parsed["pointData"]["scalars"]["p"] == [101325.0, 101300.0, 101250.0, 101200.0]
    assert parsed["pointData"]["vectors"]["U"][3] == [3.5, 0.0, 0.0]
    assert parsed["cellData"]["vectors"]["U"] == [[2.0, 0.0, 0.0]]


def test_parse_legacy_vtk_rejects_bad_connectivity_and_cell_types() -> None:
    bad_connectivity = """# vtk DataFile Version 3.0
bad
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 3 float
0 0 0
1 0 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
"""
    with pytest.raises(ValueError, match="connectivity is out of range"):
        parse_vtk_result(bad_connectivity)

    unsupported_cell = bad_connectivity.replace("4 0 1 2 3", "3 0 1 2").replace("9", "99")
    with pytest.raises(ValueError, match="Unsupported VTK cell types"):
        parse_vtk_result(unsupported_cell)


def test_parse_legacy_vtk_rejects_invalid_connectivity_and_cell_type() -> None:
    invalid_connectivity = """# vtk DataFile Version 3.0
invalid
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 3 float
0 0 0
1 0 0
0 1 0
CELLS 1 5
4 0 1 2 8
CELL_TYPES 1
9
"""
    with pytest.raises(ValueError, match="connectivity is out of range"):
        parse_vtk_result(invalid_connectivity)

    invalid_type = invalid_connectivity.replace("4 0 1 2 8", "3 0 1 2").replace("9", "99")
    with pytest.raises(ValueError, match="Unsupported VTK cell types"):
        parse_vtk_result(invalid_type)


def test_parse_ascii_vtu_subset() -> None:
    vtu = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <PointData>
        <DataArray type="Float32" Name="pressure" format="ascii">1 2 3 4</DataArray>
        <DataArray type="Float32" Name="velocity" NumberOfComponents="3" format="ascii">1 0 0 2 0 0 3 0 0 4 0 0</DataArray>
      </PointData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""
    parsed = parse_vtk_result(vtu)

    assert parsed["format"] == "vtu-ascii-v1"
    assert parsed["cells"] == [[0, 1, 2, 3]]
    assert parsed["pointData"]["scalars"]["pressure"] == [1.0, 2.0, 3.0, 4.0]
    assert parsed["pointData"]["vectors"]["velocity"][2] == [3.0, 0.0, 0.0]


def test_parse_ascii_vtu_accepts_common_linear_cell_types() -> None:
    vtu = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="8" NumberOfCells="4">
      <CellData>
        <DataArray type="Float32" Name="pressure" format="ascii">4 3 2 1</DataArray>
      </CellData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0 0 0 1 1 0 1 1 1 1 0 1 1</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 0 1 2 4 0 1 2 4 5 6 0 1 2 3 7</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">3 7 13 18</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">5 10 13 14</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""
    parsed = parse_vtk_result(vtu)

    assert parsed["cellTypes"] == [5, 10, 13, 14]
    assert parsed["cells"][3] == [0, 1, 2, 3, 7]
    assert parsed["cellData"]["scalars"]["pressure"] == [4.0, 3.0, 2.0, 1.0]


def test_parse_legacy_vtk_cell_data_field_arrays() -> None:
    vtk = """# vtk DataFile Version 2.0
cell-data
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0 1 0 0 1 1 0 0 1 0 0 0 1 1 0 1 1 1 1 0 1 1
CELLS 1 9
8 0 1 2 3 4 5 6 7
CELL_TYPES 1
12
CELL_DATA 1
FIELD attributes 2
p 1 1 float
42
U 3 1 float
1 2 3
"""
    parsed = parse_vtk_result(vtk)

    assert parsed["cellData"]["scalars"]["p"] == [42.0]
    assert parsed["cellData"]["vectors"]["U"] == [[1.0, 2.0, 3.0]]
    assert parsed["pointData"]["scalars"] == {}
    assert {"p", "U"}.issubset(parsed["fields"])


def test_parse_ascii_vtu_cell_data_subset() -> None:
    vtu = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <CellData>
        <DataArray type="Float32" Name="pressure" format="ascii">12</DataArray>
        <DataArray type="Float32" Name="velocity" NumberOfComponents="3" format="ascii">3 4 0</DataArray>
      </CellData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""
    parsed = parse_vtk_result(vtu)

    assert parsed["cellData"]["scalars"]["pressure"] == [12.0]
    assert parsed["cellData"]["vectors"]["velocity"] == [[3.0, 4.0, 0.0]]
    assert {"pressure", "velocity"}.issubset(parsed["fields"])


def test_parse_vtu_rejects_declared_count_mismatch() -> None:
    malformed = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="2">
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""
    with pytest.raises(ValueError, match="cell arrays do not match NumberOfCells"):
        parse_vtk_result(malformed)


def test_su2_case_includes_native_mesh_and_only_blocks_without_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    request = CaseRequest(project=_venturi_project(), solver="su2", advancedMode="incompressible-navier-stokes")

    case = adapters.generate_case(request)

    assert case.status == "blocked"
    assert "mesh/flowlab_mesh.su2" in case.files
    assert "mesh/flowlab_mesh.vtu" in case.files
    assert "flowlab_su2_mode_preset.json" in case.files
    assert "mesh/README.su2.md" in case.files
    assert "native ASCII SU2" in case.files["mesh/README.su2.md"]
    assert "MESH_FILENAME= mesh/flowlab_mesh.su2" in case.files["case.cfg"]
    assert "flowlab.su2_mode_preset.v1" in case.files["flowlab_su2_mode_preset.json"]
    assert any("native SU2 ASCII mesh" in item for item in case.provenance)


def test_code_saturne_case_includes_gmsh_mesh_and_blocks_without_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    request = CaseRequest(project=_venturi_project(), solver="code-saturne", advancedMode="incompressible-navier-stokes")

    case = adapters.generate_case(request)

    assert case.status == "blocked"
    assert "MESH/flowlab_mesh.msh" in case.files
    assert "mesh/flowlab_mesh.msh" in case.files
    assert "DATA/setup.xml" in case.files
    assert "DATA/flowlab_physics_preset.json" in case.files
    assert "DATA/run.cfg" in case.files
    assert "DATA/cs_user_scripts.py" in case.files
    assert "DATA/cs_user_physics.py" in case.files
    assert "$MeshFormat" in case.files["MESH/flowlab_mesh.msh"]
    assert "domain.mesh_input" in case.files["DATA/cs_user_scripts.py"]
    assert "flowlab.code_saturne_physics_preset.v1" in case.files["DATA/flowlab_physics_preset.json"]
    assert case.runCommand == ["code_saturne", "run"]
    assert any("native Gmsh mesh input" in item for item in case.provenance)


def test_parse_vtu_rejects_declared_count_mismatch() -> None:
    malformed = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">3</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""
    with pytest.raises(ValueError, match="point count"):
        parse_vtk_result(malformed)
