from __future__ import annotations

from pathlib import Path

from server.flowlab.open_boundary_operator_audit import boundary_coupling_audit, exact_boundary_matrix_audit, exact_ueqn_audit, outlet_constrain_pressure_audit, pressure_presolve_audit, trace_flux_audit, ux_linear_path_audit


def _surface(values: list[float]) -> str:
    return "internalField nonuniform List<scalar>\n" + str(len(values)) + "\n(\n" + "\n".join(str(value) for value in values) + "\n)\n"


def _surface_with_patch(values: list[float], patch_values: list[float]) -> str:
    return _surface(values) + "boundaryField\n{\n inlet\n {\n value nonuniform List<scalar>\n" + str(len(patch_values)) + "\n(\n" + "\n".join(str(value) for value in patch_values) + "\n)\n;\n }\n}\n"


def _surface_with_inlet_outlet(values: list[float], inlet_values: list[float], outlet_values: list[float]) -> str:
    def patch(name: str, patch_values: list[float]) -> str:
        return " " + name + "\n {\n value nonuniform List<scalar>\n" + str(len(patch_values)) + "\n(\n" + "\n".join(str(value) for value in patch_values) + "\n)\n;\n }\n"
    return _surface(values) + "boundaryField\n{\n" + patch("inlet", inlet_values) + patch("outlet", outlet_values) + "}\n"


def test_trace_flux_audit_checks_pressure_correction_identity(tmp_path: Path) -> None:
    fields = tmp_path / "100"
    fields.mkdir()
    (fields / "phiHbyA").write_text(_surface([3.0, -2.0]), encoding="utf-8")
    (fields / "pCorrectionFlux").write_text(_surface([1.0, -0.5]), encoding="utf-8")
    (fields / "phi").write_text(_surface([2.0, -1.5]), encoding="utf-8")
    (fields / "faceContinuityResidual").write_text(_surface([1e-12, -2e-12]), encoding="utf-8")
    report = trace_flux_audit(tmp_path)
    assert report["fluxIdentity"]["passed"] is True
    assert report["faceByFaceContinuity"]["maxAbsoluteDivergence"] == 2e-12


def test_trace_flux_audit_checks_boundary_and_cell_centred_fluxes(tmp_path: Path) -> None:
    fields = tmp_path / "100"
    fields.mkdir()
    (fields / "phiHbyA").write_text(_surface_with_patch([3.0], [4.0]), encoding="utf-8")
    (fields / "pCorrectionFlux").write_text(_surface_with_patch([1.0], [1.0]), encoding="utf-8")
    (fields / "phi").write_text(_surface_with_patch([2.0], [3.0]), encoding="utf-8")
    (fields / "cellCenteredUFlux").write_text(_surface_with_patch([2.1], [2.9]), encoding="utf-8")
    (fields / "faceContinuityResidual").write_text(_surface([0.0]), encoding="utf-8")
    report = trace_flux_audit(tmp_path)
    assert report["boundaryPatches"]["inlet"]["pressureCorrectionIdentity"]["passed"] is True
    assert abs(report["cellCenteredVelocityFlux"]["maxAbsoluteMismatch"] - 0.1) < 1e-15


def test_boundary_coupling_audit_compares_matrix_gradient_and_fluxes(tmp_path: Path) -> None:
    fields = tmp_path / "100"
    fields.mkdir()
    for name, values in {
        "p": [0.001], "phi": [3.0], "cellCenteredUFlux": [3.1], "phiHbyA": [4.0], "pCorrectionFlux": [1.0],
        "pressureNormalGradient": [0.001], "pressureGradientFlux": [0.25], "requiredPressureCorrectionFlux": [0.9],
    }.items():
        outlet_values = -0.001 if name == "pressureNormalGradient" else (0.0 if name == "p" else values)
        if not isinstance(outlet_values, list):
            outlet_values = [outlet_values]
        (fields / name).write_text(_surface_with_inlet_outlet([0.0], values, outlet_values), encoding="utf-8")
    (tmp_path / "mms-definition.json").write_text('{"parameters":{"pressure_gradient_m2_s2_per_m":0.001}}', encoding="utf-8")
    (tmp_path / "log.boundaryTraceFoamRun").write_text(
        "FLOWLAB_PEQN_BOUNDARY time=100 patch=inlet faceCount=1 internalCoeffSum=2 boundaryCoeffSum=3\n"
        "FLOWLAB_PEQN_BOUNDARY time=100 patch=outlet faceCount=1 internalCoeffSum=2 boundaryCoeffSum=3\n", encoding="utf-8"
    )
    report = boundary_coupling_audit(tmp_path)
    assert report["patches"]["inlet"]["normalPressureGradient"]["maxAbsoluteError"] == 0.0
    assert abs(report["patches"]["inlet"]["correctionCompatibility"]["maxAbsoluteMismatch"] - 0.1) < 1e-15


def test_exact_boundary_matrix_audit_compares_exact_and_actual_terms(tmp_path: Path) -> None:
    (tmp_path / "log.exactMatrixFoamRun").write_text(
        "FLOWLAB_EXACT_PEQN_BOUNDARY time=1 patch=inlet faceCount=1 internalCoeffSum=-2 boundaryCoeffSum=-0.002\n"
        "FLOWLAB_EXACT_PEQN_BOUNDARY time=1 patch=outlet faceCount=1 internalCoeffSum=40 boundaryCoeffSum=0\n"
        "FLOWLAB_PEQN_BOUNDARY time=1 patch=inlet faceCount=1 internalCoeffSum=-2 boundaryCoeffSum=-0.002\n"
        "FLOWLAB_PEQN_BOUNDARY time=1 patch=outlet faceCount=1 internalCoeffSum=-40 boundaryCoeffSum=0\n", encoding="utf-8"
    )
    report = exact_boundary_matrix_audit(tmp_path)
    assert report["patches"]["inlet"]["terms"]["internalCoefficientSum"]["relativeDifference"] == 0.0
    assert report["patches"]["outlet"]["terms"]["internalCoefficientSum"]["relativeDifference"] == 2.0


def test_exact_ueqn_audit_distinguishes_model_and_assembled_source_signs(tmp_path: Path) -> None:
    (tmp_path / "log.exactUeqnFoamRun").write_text(
        "FLOWLAB_EXACT_UEQN time=1 "
        "modelSourceMatrixIntegral=(0.001 0 0) "
        "assembledEquationSourceIntegral=(-0.001 0 0) "
        "pressureGradientRhsIntegral=(0.001 0 0) "
        "sourcePlusPressureRhs=(0 0 0) "
        "matrixResidualIntegral=(-0.001 0 0) "
        "fullResidualIntegral=(0 0 0) fullResidualL2=2e-17 "
        "exactEqualityResidualIntegral=(0 0 0) exactEqualityResidualL2=3e-17 "
        "actualAssembledSourceIntegral=(11 0 0) "
        "actualMatrixResidualIntegral=(-0.001 0 0) "
        "actualEqualityResidualIntegral=(0 0 0) actualEqualityResidualL2=5e-17\n"
        "smoothSolver:  Solving for Ux, Initial residual = 0.99, Final residual = 0.98, No Iterations 1000\n",
        encoding="utf-8",
    )
    report = exact_ueqn_audit(tmp_path)
    assert report["operatorGate"]["passed"] is True
    assert report["exactState"]["sourcePressureBalance"]["passed"] is True
    assert report["exactState"]["assembledEquationSourceIntegral"] == (-0.001, 0.0, 0.0)
    assert report["firstReportedUxSolve"]["iterations"] == 1000


def test_ux_linear_path_audit_reports_roundoff_scale_normalization(tmp_path: Path) -> None:
    system = tmp_path / "system"
    system.mkdir()
    (system / "fvSolution").write_text(
        "solvers { U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0; } }",
        encoding="utf-8",
    )
    (tmp_path / "log.uxNormalizationFoamRun").write_text(
        "   Normalisation factor = 1.75e-15\n"
        "smoothSolver:  Solving for Ux, Initial residual = 0.999, Final residual = 0.996, No Iterations 1000\n"
        "FLOWLAB_UX_SOLVER_PATH time=1 solver=smoothSolver initialResidual=(0.999 0.9 0.8) "
        "finalResidual=(0.996 1e-11 1e-11) iterations=(1000 27 27) lduMatrixDebugDuringSolve=2\n",
        encoding="utf-8",
    )
    report = ux_linear_path_audit(tmp_path)
    assert report["firstUx"]["hitConfiguredOrDefaultMaxIterations"] is True
    assert abs(report["firstUx"]["rawInitialResidualL1"] - 1.74825e-15) < 1e-24
    assert report["returnedPath"]["iterations"] == (1000, 27, 27)
    assert report["interpretation"]["normalizationArtifact"] is True


def test_pressure_presolve_audit_localizes_required_correction_without_reference_cell(tmp_path: Path) -> None:
    (tmp_path / "log.pEqnPreSolveFoamRun").write_text(
        "FLOWLAB_PEQN_PRE_SOLVE time=1 referenceCell=-1 referenceApplied=false referenceValue=0 "
        "referencePressureBefore=0 referencePressureAfter=0 noReferenceDiagonal=0 noReferenceSource=0 "
        "referenceDiagonal=0 referenceSource=0 predictorDivergenceIntegral=-0.0015 predictorDivergenceL2=0.1 "
        "predictorDivergenceMax=0.01 exactPressureResidualIntegral=1e-8 exactPressureResidualL1=0.0003 "
        "exactPressureResidualL2=2e-5 exactPressureResidualMax=1e-6 directSourceSum=-0.0015 "
        "requiredCorrectionInternalFluxSum=-0.0017\n"
        "FLOWLAB_PEQN_PRE_SOLVE_BOUNDARY time=1 patch=inlet faceCount=1 predictorFluxSum=-1 "
        "requiredCorrectionFluxSum=-0.0001 internalCoeffSum=-2 boundaryRhsSum=-0.002\n"
        "FLOWLAB_PEQN_PRE_SOLVE_BOUNDARY time=1 patch=outlet faceCount=1 predictorFluxSum=0.9985 "
        "requiredCorrectionFluxSum=-0.0015 internalCoeffSum=-40 boundaryRhsSum=0\n",
        encoding="utf-8",
    )
    report = pressure_presolve_audit(tmp_path)
    assert report["reference"]["applied"] is False
    assert report["requiredCorrection"]["dominantPatch"] == "outlet"
    assert abs(report["requiredCorrection"]["dominantPatchAbsoluteShare"] - 0.9375) < 1e-15
    assert report["conclusion"]["referenceCellCauseExcluded"] is True


def test_outlet_constrain_pressure_audit_identifies_fixed_value_noop(tmp_path: Path) -> None:
    before = (
        "FLOWLAB_CONSTRAIN_PRESSURE time=1 phase=before patch=outlet "
        "pressureType=fixedValue velocityType=fixedValue pressureFixesValue=1 velocityFixesValue=1 "
        "fixedFluxPressure=0 pressureUpdated=0 predictorFluxSum=0.9985 velocityFluxSum=1 "
        "requiredGradientMean=-0.00068 actualGradientMean=-0.001\n"
    )
    after = before.replace("phase=before", "phase=after")
    (tmp_path / "log.outletConstrainPressureFoamRun").write_text(before + after, encoding="utf-8")

    report = outlet_constrain_pressure_audit(tmp_path)

    assert report["comparison"]["simultaneousFixedPressureAndVelocity"] is True
    assert report["comparison"]["constrainPressureWasNoOp"] is True
    assert report["comparison"]["incompatibleContributionIdentified"] is True
    assert report["conclusion"]["singleChangeJustified"] is True
