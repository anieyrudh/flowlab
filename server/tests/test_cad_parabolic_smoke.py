from __future__ import annotations

import json
import math
from pathlib import Path
import pytest
from server.flowlab.cad_parabolic_smoke import CadParabolicSmokeError, CadParabolicSmokeSpec, initialize_exact_parabolic_fields, materialize_cad_parabolic_smoke_case, validate_accepted_immutable_mesh

def _mesh(root: Path) -> Path:
    path=root/"polyMesh"; path.mkdir(parents=True)
    for name in ("points","faces","owner","neighbour"): (path/name).write_text("x\n")
    (path/"boundary").write_text("inlet\noutlet\nwall\n")
    return path

def _report(mesh: Path, accepted: bool=True) -> Path:
    path=mesh.parent/"gate.json"; artifacts={f"polyMesh/{n}": {"path":str(mesh/n)} for n in ("points","faces","owner","neighbour","boundary")}
    data={"schema":"flowlab.immutable-surface-layered-gate.v1","accepted":accepted,"artifacts":artifacts,"expected":{"patches":{"inlet":11,"outlet":12,"wall":13}},"conversion":{"patchNames":["inlet","outlet","wall"]},"checkMesh":{"meshOk":True,"failedChecks":0,"issueCounts":{"smallDeterminantCells":0,"lowInterpolationWeightFaces":0,"lowVolumeRatioFaces":0,"concaveCells":0}}}
    path.write_text(json.dumps(data)); return path

def test_rejects_unaccepted_gate(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh,False)
    with pytest.raises(CadParabolicSmokeError, match="not accepted"): materialize_cad_parabolic_smoke_case(tmp_path/"case",source_poly_mesh=mesh,immutable_gate_report=report)

def test_gate_binds_exact_mesh(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path/"a"); report=_report(mesh); other=_mesh(tmp_path/"b")
    with pytest.raises(CadParabolicSmokeError, match="does not bind"): validate_accepted_immutable_mesh(source_poly_mesh=other,immutable_gate_report=report)

def test_materializes_nonexecuted_parabolic_case(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh); result=materialize_cad_parabolic_smoke_case(tmp_path/"case",source_poly_mesh=mesh,immutable_gate_report=report)
    case=tmp_path/"case"; assert result["status"]=="materialized-not-run"; assert result["execution"]["solverStarted"] is False; assert result["parabolicInlet"]["relativeIntegralError"] < 1e-12
    assert "codedFixedValue" in (case/"0"/"U").read_text(); assert "fullyDevelopedPipeInlet" in (case/"0"/"U").read_text()
    assert "targetFlow" in (case/"0"/"U").read_text()
    pressure = (case/"0"/"p").read_text()
    assert "dimensions [0 2 -2 0 0 0 0];" in pressure
    assert "outlet { type fixedValue; value uniform 0; }" in pressure
    assert "pressureInletOutletVelocity" in (case/"0"/"U").read_text()
    assert "smoother symGaussSeidel" in (case/"system"/"fvSolution").read_text()
    assert "endTime 2000;" in (case/"system"/"controlDict").read_text()
    solution = (case/"system"/"fvSolution").read_text()
    assert "SIMPLE { nNonOrthogonalCorrectors 2; consistent yes;" in solution
    assert "fields { p" not in solution
    schemes = (case/"system"/"fvSchemes").read_text()
    assert "gradSchemes { default Gauss linear; }" in schemes
    assert "div(phi,U) Gauss linear;" in schemes
    assert all(x in (case/"system"/"controlDict").read_text() for x in ("inletPressure","outletPressure","inletFlux","outletFlux")); assert (case/"run_smoke.sh").stat().st_mode & 0o111

def test_materialized_case_can_bind_the_exact_iteration_limit(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh)
    materialize_cad_parabolic_smoke_case(tmp_path/"case",source_poly_mesh=mesh,immutable_gate_report=report,iteration_limit=100)
    manifest=json.loads((tmp_path/"case"/"cad-smoke-manifest.json").read_text())
    assert manifest["execution"]["iterationLimit"] == 100
    assert "endTime 100;" in (tmp_path/"case"/"system"/"controlDict").read_text()

def test_parabolic_integral() -> None:
    spec=CadParabolicSmokeSpec(); assert math.isclose(spec.analytic_flow_m3_per_s(),spec.volumetric_flow_rate_m3_s,rel_tol=1e-12)

def test_x_axis_materialization_and_exact_initialization(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh); case=tmp_path/"case"
    materialize_cad_parabolic_smoke_case(case,source_poly_mesh=mesh,immutable_gate_report=report,spec=CadParabolicSmokeSpec(axis="x"))
    assert "profile[facei].x()" in (case/"0"/"U").read_text()
    assert "centres[facei].y())+sqr(centres[facei].z())" in (case/"0"/"U").read_text()
    (case/"0"/"C").write_text("FoamFile {}\ninternalField nonuniform List<vector>\n2\n(\n(0 0 0)\n(0.05 0 0)\n)\n;\nboundaryField {}\n")
    initialize_exact_parabolic_fields(case)
    assert "(0.254647908947" in (case/"0"/"U").read_text()

def test_fully_developed_outlet_is_explicit_and_opt_in(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh); case=tmp_path/"case"
    materialize_cad_parabolic_smoke_case(case,source_poly_mesh=mesh,immutable_gate_report=report,spec=CadParabolicSmokeSpec(axis="x"),fully_developed_outlet=True)
    velocity = (case/"0"/"U").read_text()
    assert "fullyDevelopedPipeOutlet" in velocity
    assert "pressureInletOutletVelocity" not in velocity
    assert json.loads((case/"cad-smoke-manifest.json").read_text())["parabolicInlet"]["outletVelocity"] == "same fully-developed parabolic profile"

def test_exact_initialization_replaces_only_internal_fields(tmp_path: Path) -> None:
    mesh=_mesh(tmp_path); report=_report(mesh); case=tmp_path/"case"
    materialize_cad_parabolic_smoke_case(case,source_poly_mesh=mesh,immutable_gate_report=report)
    (case/"0"/"C").write_text("FoamFile {}\ninternalField nonuniform List<vector>\n2\n(\n(0 0 0)\n(0 0 0.05)\n)\n;\nboundaryField {}\n")
    result=initialize_exact_parabolic_fields(case)
    assert result["cellCount"] == 2
    assert result["expectedKinematicPressureDropM2PerS2"] > 0
    assert "nonuniform List<vector>\n2" in (case/"0"/"U").read_text()
    assert "nonuniform List<scalar>\n2" in (case/"0"/"p").read_text()
    assert "pressureInletOutletVelocity" in (case/"0"/"U").read_text()
