"""Prospective FDA nozzle Re=500 v2 preflight sensitivity matrix.

The preflight is diagnostic.  It freezes and runs a 2^3 medium-grid matrix for
outlet length, spatial schemes, and targeted local resolution.  Its only
allowed outcome is a recommended numerical contract for a later, separately
frozen three-grid validation campaign; it cannot authorize a desktop claim.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
from typing import Any, Sequence

from .fda_nozzle_re500 import (
    CASE_SCHEMA,
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_DIGEST,
    FdaNozzleDefinition,
    OFFICIAL_ARCHIVE_SHA256,
    OFFICIAL_COMMIT,
    _BlockMeshBuilder,
    _case_files,
    _header,
    _now,
    _plane,
    _quads,
    _sha256,
    _vertex_text,
    _validation_row,
    _write,
    _write_json,
    execute_case,
    experimental_summary,
    postprocess_case,
)
from .fda_nozzle_re500_v2_observation import (
    observation_contract,
    pressure_diagnostic,
    run_piv_observation,
    v1_spatial_validation,
    velocity_diagnostic,
)


SCHEMA = "flowlab.fda-nozzle-re500-v2-preflight.v1"
CAMPAIGN_ID = "fda-nozzle-re500-v2-preflight-2x2x2"
V1_CAMPAIGN_ID = "2026-07-17-re500-v1"
OUTLETS_M = {"short": 0.120, "extended": 0.720}
SCHEMES = ("bounded", "second-order")
RESOLUTIONS = ("base", "enhanced")

HARIHARAN_PDF_SHA256 = (
    "ac31f5db07e72c09731b98b4250054e9e9bc7f62e71b1150e3d5943e2747db47"
)
RABEN_PDF_SHA256 = (
    "2adc7ff504ae46e5880c00d55ca8608649c76b777b32191a4830eba524c783fc"
)


def matrix_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outlet_name, outlet_m in OUTLETS_M.items():
        for scheme in SCHEMES:
            for resolution in RESOLUTIONS:
                rows.append(
                    {
                        "label": f"{outlet_name}__{scheme}__{resolution}",
                        "outlet": outlet_name,
                        "outletM": outlet_m,
                        "scheme": scheme,
                        "resolution": resolution,
                        "enhanced": resolution == "enhanced",
                    }
                )
    return rows


def preflight_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "frozenAt": "2026-07-17T00:00:00+00:00",
        "status": "prospective-diagnostic-preflight-not-validation",
        "purpose": (
            "select the outlet, spatial-scheme, and local-resolution contract "
            "before a separately frozen three-grid FDA Re=500 validation"
        ),
        "invariants": {
            "solver": "OpenFOAM Foundation 11 incompressibleFluid, steady laminar SIMPLE",
            "image": DEFAULT_IMAGE,
            "imageDigest": DEFAULT_IMAGE_DIGEST,
            "iterations": 800,
            "comparisonTimes": [750, 800],
            "reynoldsNumber": 500,
            "mesh": "strict all-hex multi-block O-grid",
            "inletVelocity": (
                "fixedValue exact discrete face-flux-normalized parabolic profile"
            ),
            "inletPressure": "fixedFluxPressure initialized at exact zero pressure",
            "outletVelocity": "zeroGradient",
            "outletPressure": "fixedValue zero gauge pressure",
            "flowScale": 1.0,
            "commonInletAxialCells": 116,
        },
        "factors": {
            "outlet": {
                "low": {"name": "short", "outletM": 0.120},
                "high": {
                    "name": "extended",
                    "outletM": 0.720,
                    "sourceBasis": "60 inlet diameters downstream in Raben et al. 2016",
                    "nearFieldUnchangedThroughM": 0.120,
                    "farExtensionCells": 300,
                },
            },
            "scheme": {
                "low": {
                    "name": "bounded",
                    "grad": "cellLimited Gauss linear 1",
                    "laplacian": "Gauss linear limited 0.5",
                    "snGrad": "limited 0.5",
                },
                "high": {
                    "name": "second-order",
                    "grad": "Gauss linear",
                    "laplacian": "Gauss linear corrected",
                    "snGrad": "corrected",
                },
                "commonConvection": "bounded Gauss linearUpwind grad(U)",
            },
            "localResolution": {
                "low": {
                    "name": "base",
                    "contractionAxialCells": 45,
                    "throatAxialCells": 80,
                    "nearDownstreamAxialCells": 240,
                    "downstreamOuterRadialCells": 8,
                },
                "high": {
                    "name": "enhanced",
                    "contractionAxialCells": 90,
                    "throatAxialCells": 160,
                    "nearDownstreamAxialCells": 480,
                    "downstreamOuterRadialCells": 16,
                },
                "unchanged": (
                    "inlet segment, core/tangential topology, geometry, and all physics"
                ),
            },
        },
        "design": {
            "type": "full factorial 2^3",
            "cases": matrix_cases(),
            "parallelExecutionAllowed": True,
            "oneFactorAtATimeInterpretationForbidden": True,
            "effects": ["A", "B", "C", "AB", "AC", "BC", "ABC"],
        },
        "observation": observation_contract(),
        "sources": {
            "officialRepositoryCommit": OFFICIAL_COMMIT,
            "experimentalArchiveSha256": OFFICIAL_ARCHIVE_SHA256,
            "hariharan2011PdfSha256": HARIHARAN_PDF_SHA256,
            "raben2016PdfSha256": RABEN_PDF_SHA256,
        },
        "responses": {
            "velocity": [
                "PIV-window profile normalized RMSE",
                "PIV-window centreline normalized RMSE",
                "eligible-point diagnostic pass fractions",
            ],
            "pressure": [
                "adjacent offset-free wall-pressure-difference RMSE Pa",
                "named offset-free wall-pressure-difference RMSE Pa",
            ],
            "numerical": [
                "strict-all-hex and checkMesh",
                "flow conservation <= 1e-6 relative",
                "OpenFOAM forces versus direct integration <= 1e-10 relative",
                "solver and postprocessing completion",
            ],
            "compositeDiagnosticScore": (
                "profile NRMSE + centreline NRMSE + adjacent-pressure RMSE/500 Pa"
            ),
        },
        "selectionRules": {
            "failClosed": (
                "no full-campaign recommendation if any of the eight cases fails a "
                "numerical response gate"
            ),
            "outlet": (
                "select short only if every matched pair has maximum pooled axial-velocity "
                "change <=0.5% of throat mean velocity and maximum named wall-pressure-"
                "difference change <=1 Pa; otherwise select extended"
            ),
            "resolution": (
                "select enhanced unless it fails a numerical gate or its composite score is "
                "more than 10% worse than base at the selected outlet and scheme; either "
                "condition blocks automatic recommendation"
            ),
            "scheme": (
                "select second-order when numerically admissible and its enhanced-grid "
                "composite score is no more than 2% above bounded; otherwise select bounded"
            ),
            "effectConvention": (
                "coded high-minus-low effect = sum(coded sign times response)/4"
            ),
        },
        "v1Reanalysis": {
            "cases": [
                "coarse",
                "medium",
                "fine",
                "input-minus-5pct",
                "input-plus-5pct",
            ],
            "purpose": (
                "quantify how the prospective PIV observation operator and offset-free "
                "pressure diagnostics move v1 failures; the v1 verdict remains immutable"
            ),
        },
        "authorization": {
            "desktopPromotion": False,
            "scientificClaim": False,
            "allowedOutcome": "recommended contract for a future full campaign only",
        },
        "promotionAuthorized": False,
    }


def _render_block_mesh(builder: _BlockMeshBuilder) -> str:
    vertex_text = "\n".join(
        f"    {_vertex_text(point)} // {index}"
        for index, point in enumerate(builder.vertices)
    )
    block_text = "\n".join(f"    {block}" for block in builder.blocks)
    edge_text = "\n".join(
        f"    arc {start} {end} {_vertex_text(midpoint)}"
        for start, end, midpoint in sorted(builder.edges)
    )

    def patch(name: str, kind: str) -> str:
        faces = "\n".join(
            "            (" + " ".join(str(value) for value in face) + ")"
            for face in builder.boundary[name]
        )
        return f"""    {name}
    {{
        type {kind};
        faces
        (
{faces}
        );
    }}"""

    return _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices
(
{vertex_text}
);
blocks
(
{block_text}
);
edges
(
{edge_text}
);
boundary
(
{patch('inlet', 'patch')}
{patch('outlet', 'patch')}
{patch('wall', 'wall')}
);
mergePatchPairs ();
"""


def preflight_block_mesh(outlet_m: float, enhanced: bool) -> str:
    if outlet_m not in OUTLETS_M.values():
        raise ValueError(f"unsupported preflight outlet: {outlet_m}")
    spec = replace(FdaNozzleDefinition(), outlet_x_m=outlet_m)
    builder = _BlockMeshBuilder()

    upstream_x = (
        spec.inlet_x_m,
        spec.contraction_start_x_m,
        spec.throat_start_x_m,
        0.0,
    )
    upstream_planes: list[dict[str, list[int]]] = []
    for index, x in enumerate(upstream_x):
        radius = spec.radius(x - (1.0e-12 if x == 0.0 else 0.0))
        upstream_planes.append(
            _plane(
                builder,
                x,
                radius,
                core_half_width=radius / 2.0,
                prefix=f"u{index}",
            )
        )

    n_tangent = 4
    n_radial = 2
    n_core = 4
    # 116 is deliberately divisible by two so the selected enhanced topology
    # can become the exact r=2 member of a nested 1/2/4 full-campaign family.
    upstream_axial = [116, 90 if enhanced else 45, 160 if enhanced else 80]
    for segment, (left, right) in enumerate(
        zip(upstream_planes, upstream_planes[1:])
    ):
        left_quads = _quads(left)
        right_quads = _quads(right)
        axial = upstream_axial[segment]
        builder.block(left_quads[0], right_quads[0], (n_core, n_core, axial))
        for left_quad, right_quad in zip(left_quads[1:], right_quads[1:]):
            builder.block(left_quad, right_quad, (n_radial, n_tangent, axial))
            builder.boundary["wall"].append(
                (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
            )
    builder.boundary["inlet"].extend(
        tuple(quad) for quad in _quads(upstream_planes[0])
    )

    downstream_x = [0.0, 0.120]
    downstream_axial = [480 if enhanced else 240]
    if outlet_m > 0.120:
        downstream_x.append(outlet_m)
        downstream_axial.append(300)

    inner_planes = [upstream_planes[-1]]
    outer_planes: list[dict[str, list[int]]] = []
    outer_start = _plane(
        builder,
        0.0,
        spec.inlet_radius_m,
        core_half_width=spec.throat_radius_m / 2.0,
        prefix="do0",
    )
    outer_start["core"] = upstream_planes[-1]["ring"]
    outer_planes.append(outer_start)
    for index, x in enumerate(downstream_x[1:], start=1):
        inner = _plane(
            builder,
            x,
            spec.throat_radius_m,
            core_half_width=spec.throat_radius_m / 2.0,
            prefix=f"di{index}",
        )
        outer = _plane(
            builder,
            x,
            spec.inlet_radius_m,
            core_half_width=spec.throat_radius_m / 2.0,
            prefix=f"do{index}",
        )
        outer["core"] = inner["ring"]
        inner_planes.append(inner)
        outer_planes.append(outer)

    outer_radial = 16 if enhanced else 8
    for segment, axial in enumerate(downstream_axial):
        inner_left = _quads(inner_planes[segment])
        inner_right = _quads(inner_planes[segment + 1])
        builder.block(inner_left[0], inner_right[0], (n_core, n_core, axial))
        for left_quad, right_quad in zip(inner_left[1:], inner_right[1:]):
            builder.block(left_quad, right_quad, (n_radial, n_tangent, axial))

        outer_left = _quads(outer_planes[segment])[1:]
        outer_right = _quads(outer_planes[segment + 1])[1:]
        for left_quad, right_quad in zip(outer_left, outer_right):
            builder.block(left_quad, right_quad, (outer_radial, n_tangent, axial))
            if segment == 0:
                builder.boundary["wall"].append(tuple(left_quad))
            builder.boundary["wall"].append(
                (left_quad[1], right_quad[1], right_quad[2], left_quad[2])
            )

    outlet_quads = _quads(inner_planes[-1]) + _quads(outer_planes[-1])[1:]
    builder.boundary["outlet"].extend(
        tuple(reversed(quad)) for quad in outlet_quads
    )
    return _render_block_mesh(builder)


def _formal_second_order(schemes: str) -> str:
    updated = schemes.replace(
        "gradSchemes { default cellLimited Gauss linear 1; }",
        "gradSchemes { default Gauss linear; }",
    )
    updated = updated.replace(
        "laplacianSchemes { default Gauss linear limited 0.5; }",
        "laplacianSchemes { default Gauss linear corrected; }",
    )
    updated = updated.replace(
        "snGradSchemes { default limited 0.5; }",
        "snGradSchemes { default corrected; }",
    )
    return updated


def prepare_preflight_case(case: Path, factors: dict[str, Any]) -> None:
    if case.exists() and any(case.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty case: {case}")
    spec = replace(FdaNozzleDefinition(), outlet_x_m=float(factors["outletM"]))
    files = _case_files(str(factors["label"]), 2, 1.0, spec)
    files["system/blockMeshDict"] = preflight_block_mesh(
        float(factors["outletM"]), bool(factors["enhanced"])
    )
    if factors["scheme"] == "second-order":
        files["system/fvSchemes"] = _formal_second_order(files["system/fvSchemes"])
    definition = json.loads(files["case-definition.json"])
    definition.update(
        {
            "schema": "flowlab.fda-nozzle-re500-v2-preflight-case.v1",
            "preflightFactors": factors,
            "promotionAuthorized": False,
        }
    )
    files["case-definition.json"] = json.dumps(
        definition, indent=2, sort_keys=True
    ) + "\n"
    for relative, content in files.items():
        _write(case / relative, content)


def _copytree_hardlink(source: Path, target: Path) -> str:
    try:
        shutil.copytree(source, target, copy_function=os.link)
        return "hardlink"
    except OSError:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, copy_function=shutil.copy2)
        return "copy"


def _v1_labels() -> tuple[str, ...]:
    return (
        "coarse",
        "medium",
        "fine",
        "input-minus-5pct",
        "input-plus-5pct",
    )


def prepare_preflight(output: Path, v1: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite campaign: {output}")
    if _sha256(v1 / "assessment.json") != (
        "5fd9c880e01a04c3fe4ff721f050c12e64b696d1d65e69be22b31ae983fdf77b"
    ):
        raise ValueError("v1 assessment hash does not match the frozen baseline")
    output.mkdir(parents=True, exist_ok=True)
    contract = preflight_contract()
    _write_json(output / "preflight-contract.json", contract)
    shutil.copytree(v1 / "experiment", output / "experiment")
    (output / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        v1 / "bin" / "flowlabFdaPatchAudit",
        output / "bin" / "flowlabFdaPatchAudit",
    )

    cases = matrix_cases()
    for factors in cases:
        prepare_preflight_case(output / "cases" / factors["label"], factors)

    v1_cases: list[dict[str, Any]] = []
    for label in _v1_labels():
        mode = _copytree_hardlink(
            v1 / "cases" / label,
            output / "v1-reanalysis" / "cases" / label,
        )
        result_target = output / "v1-reanalysis" / "baseline-results" / label
        result_target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            v1 / "results" / label / "observation.json",
            result_target / "observation.json",
        )
        v1_cases.append({"label": label, "copyMode": mode})

    manifest = {
        "schema": SCHEMA,
        "createdAt": _now(),
        "status": "prepared-not-executed",
        "contractSha256": _sha256(output / "preflight-contract.json"),
        "v1AssessmentSha256": _sha256(v1 / "assessment.json"),
        "cases": cases,
        "v1ReanalysisCases": v1_cases,
        "image": DEFAULT_IMAGE,
        "imageDigest": DEFAULT_IMAGE_DIGEST,
        "promotionAuthorized": False,
    }
    _write_json(output / "preflight-manifest.json", manifest)
    return manifest


def postprocess_preflight_case(
    output: Path, label: str, image: str = DEFAULT_IMAGE
) -> dict[str, Any]:
    report = postprocess_case(output, label, image)
    if report["status"] != "observed":
        return report
    experiment = json.loads(
        (output / "experiment" / "experimental-data.json").read_text(
            encoding="utf-8"
        )
    )
    summary = experimental_summary(experiment)
    piv = run_piv_observation(
        case=output / "cases" / label,
        result=output / "results" / label,
        summary=summary,
        workspace=Path(__file__).resolve().parents[2],
        log=output / "logs" / label / "fdaPivProbes.log",
        image=image,
    )
    return {"observation": report, "pivObservation": piv}


def reanalyze_v1_case(
    output: Path, label: str, image: str = DEFAULT_IMAGE
) -> dict[str, Any]:
    if label not in _v1_labels():
        raise ValueError(f"unsupported v1 case: {label}")
    experiment = json.loads(
        (output / "experiment" / "experimental-data.json").read_text(
            encoding="utf-8"
        )
    )
    report = run_piv_observation(
        case=output / "v1-reanalysis" / "cases" / label,
        result=output / "v1-reanalysis" / "results" / label,
        summary=experimental_summary(experiment),
        workspace=Path(__file__).resolve().parents[2],
        log=output / "v1-reanalysis" / "logs" / label / "fdaPivProbes.log",
        image=image,
    )
    return report


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _v1_pressure_validation(
    diagnostics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "flowlab.fda-nozzle-re500-v2-v1-pressure-reanalysis.v1",
        "status": "diagnostic-only-v1-verdict-unchanged",
        "promotionAuthorized": False,
    }
    for kind in ("wall", "centreline"):
        result[kind] = {}
        for group in ("adjacent", "named"):
            indices = {
                label: {
                    row["name"]: row
                    for row in diagnostic[kind][group]["rows"]
                }
                for label, diagnostic in diagnostics.items()
            }
            rows: list[dict[str, Any]] = []
            for name, fine in indices["fine"].items():
                validation = _validation_row(
                    experimental=fine["experiment"],
                    coarse=float(indices["coarse"][name]["simulationPa"]),
                    medium=float(indices["medium"][name]["simulationPa"]),
                    fine=float(fine["simulationPa"]),
                    fine_previous=float(fine["simulationPreviousPa"]),
                    input_minus=float(
                        indices["input-minus-5pct"][name]["simulationPa"]
                    ),
                    input_plus=float(
                        indices["input-plus-5pct"][name]["simulationPa"]
                    ),
                )
                rows.append(
                    {
                        "name": name,
                        "leftM": fine["leftM"],
                        "rightM": fine["rightM"],
                        **validation,
                    }
                )
            qualified = [row for row in rows if row["qualified"]]
            passed = [row for row in rows if row["passesVv20"]]
            result[kind][group] = {
                "rows": rows,
                "counts": {
                    "reported": len(rows),
                    "gciQualified": len(qualified),
                    "vv20Passed": len(passed),
                    "gciQualifiedFraction": len(qualified) / len(rows),
                    "vv20PassFraction": len(passed) / len(rows),
                },
            }
    return result


def _numerical_gate(
    execution: dict[str, Any], observation: dict[str, Any], piv: dict[str, Any]
) -> dict[str, Any]:
    checks = {
        "solverComplete": execution.get("status") == "solver-complete",
        "checkMesh": bool(execution.get("mesh", {}).get("meshOk")),
        "strictAllHex": bool(execution.get("mesh", {}).get("strictAllHex")),
        "observationComplete": observation.get("status") == "observed",
        "flowConservation": bool(
            observation.get("checks", {}).get("flowConservation")
        ),
        "forceObjectMatchesDirect": bool(
            observation.get("checks", {}).get("forceObjectMatchesDirect")
        ),
        "pivObservationComplete": piv.get("status") == "observed",
    }
    return {"checks": checks, "passes": all(checks.values())}


def _case_result(output: Path, factors: dict[str, Any]) -> dict[str, Any]:
    label = str(factors["label"])
    root = output / "results" / label
    execution = _read_json(root / "execution.json")
    observation = _read_json(root / "observation.json")
    piv = _read_json(root / "piv-observation.json")
    velocity = velocity_diagnostic(piv)
    experiment = _read_json(output / "experiment" / "experimental-data.json")
    pressure = pressure_diagnostic(experiment, observation)
    solver = _solver_diagnostic(output / "logs" / label / "foamRun.log")
    _write_json(root / "velocity-diagnostic.json", velocity)
    _write_json(root / "pressure-diagnostic.json", pressure)
    gate = _numerical_gate(execution, observation, piv)
    profile = velocity["profiles"]["summary"]
    centreline = velocity["centreline"]["summary"]
    response = {
        "profileNrmse": profile["normalizedRmseByExperimentalPeak"],
        "profilePassFraction": profile["passFraction"],
        "centrelineNrmse": centreline["normalizedRmseByExperimentalPeak"],
        "centrelinePassFraction": centreline["passFraction"],
        "wallAdjacentPressureRmsePa": pressure["wall"]["adjacent"]["rmsePa"],
        "wallAdjacentPressurePassFraction": pressure["wall"]["adjacent"][
            "passFraction"
        ],
        "wallNamedPressureRmsePa": pressure["wall"]["named"]["rmsePa"],
        "wallNamedPressurePassFraction": pressure["wall"]["named"][
            "passFraction"
        ],
        "maximumFlowRelativeError": max(
            float(observation["times"]["800"]["flow"][key])
            for key in (
                "boundaryClosureRelative",
                "inletTargetRelative",
                "outletTargetRelative",
            )
        ),
        "forceObjectVsDirectRelative": observation["times"]["800"][
            "forceObjectVsDirectRelative"
        ],
        "forceObjectVsDirectAbsoluteN": observation["times"]["800"][
            "forceObjectVsDirectAbsoluteN"
        ],
        "solverExecutionTimeSeconds": solver["executionTimeSeconds"],
    }
    response["compositeDiagnosticScore"] = (
        float(response["profileNrmse"])
        + float(response["centrelineNrmse"])
        + float(response["wallAdjacentPressureRmsePa"]) / 500.0
    )
    return {
        "label": label,
        "factors": factors,
        "mesh": execution["mesh"],
        "numericalGate": gate,
        "responses": response,
        "piv": piv,
        "pressure": pressure,
        "solverDiagnostic": solver,
    }


def _solver_diagnostic(log: Path) -> dict[str, Any]:
    current_time: int | None = None
    solves: list[dict[str, Any]] = []
    execution_seconds = 0.0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        time_match = re.fullmatch(r"Time = (\d+)s", line.strip())
        if time_match:
            current_time = int(time_match.group(1))
            continue
        execution_match = re.match(r"ExecutionTime = ([-+0-9.eE]+) s", line)
        if execution_match:
            execution_seconds = float(execution_match.group(1))
        pressure_match = re.search(
            r"GAMG:\s+Solving for p, Initial residual = ([-+0-9.eE]+), "
            r"Final residual = ([-+0-9.eE]+), No Iterations (\d+)",
            line,
        )
        if pressure_match:
            solves.append(
                {
                    "time": current_time,
                    "initialResidual": float(pressure_match.group(1)),
                    "finalResidual": float(pressure_match.group(2)),
                    "iterations": int(pressure_match.group(3)),
                }
            )
    capped = [solve for solve in solves if solve["iterations"] >= 1000]
    final = [solve for solve in solves if solve["time"] == 800]
    return {
        "executionTimeSeconds": execution_seconds,
        "pressureSolveCount": len(solves),
        "pressureSolveCap": 1000,
        "cappedPressureSolveCount": len(capped),
        "cappedPressureSolveFraction": len(capped) / len(solves) if solves else 0.0,
        "maximumCappedFinalResidual": max(
            (float(solve["finalResidual"]) for solve in capped), default=None
        ),
        "finalIterationPressureSolves": final,
        "finalIterationMeetsConfiguredLinearTolerance": bool(final)
        and all(float(solve["finalResidual"]) <= 1.0e-12 for solve in final),
    }


def _factorial_effects(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    factor_codes: dict[str, tuple[str, str]] = {
        "A": ("outlet", "extended"),
        "B": ("scheme", "second-order"),
        "C": ("resolution", "enhanced"),
    }
    metrics = [
        "profileNrmse",
        "profilePassFraction",
        "centrelineNrmse",
        "centrelinePassFraction",
        "wallAdjacentPressureRmsePa",
        "wallAdjacentPressurePassFraction",
        "wallNamedPressureRmsePa",
        "wallNamedPressurePassFraction",
        "compositeDiagnosticScore",
        "solverExecutionTimeSeconds",
    ]

    def sign(row: dict[str, Any], term: str) -> int:
        value = 1
        for factor in term:
            key, high = factor_codes[factor]
            value *= 1 if row["factors"][key] == high else -1
        return value

    return {
        term: {
            metric: sum(
                sign(row, term) * float(row["responses"][metric]) for row in rows
            )
            / 4.0
            for metric in metrics
        }
        for term in ("A", "B", "C", "AB", "AC", "BC", "ABC")
    }


def _piv_index(case: dict[str, Any]) -> dict[str, float]:
    return {
        row["recordId"]: float(row["pooledVelocityMPerS"][0])
        for row in case["piv"]["times"]["800"]
        if row["supportValid"]
    }


def _named_pressure_index(case: dict[str, Any]) -> dict[str, float]:
    return {
        row["name"]: float(row["simulationPa"])
        for row in case["pressure"]["wall"]["named"]["rows"]
    }


def _domain_independence(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_label = {row["label"]: row for row in rows}
    throat_mean = FdaNozzleDefinition().throat_mean_velocity_m_s
    pairs: list[dict[str, Any]] = []
    for scheme in SCHEMES:
        for resolution in RESOLUTIONS:
            short = by_label[f"short__{scheme}__{resolution}"]
            extended = by_label[f"extended__{scheme}__{resolution}"]
            short_u = _piv_index(short)
            extended_u = _piv_index(extended)
            max_u = max(
                abs(short_u[key] - extended_u[key]) for key in short_u.keys()
            )
            short_p = _named_pressure_index(short)
            extended_p = _named_pressure_index(extended)
            max_p = max(
                abs(short_p[key] - extended_p[key]) for key in short_p.keys()
            )
            pairs.append(
                {
                    "scheme": scheme,
                    "resolution": resolution,
                    "maximumPooledAxialVelocityChangeMPerS": max_u,
                    "maximumPooledAxialVelocityChangeOfThroatMean": max_u
                    / throat_mean,
                    "maximumNamedPressureDifferenceChangePa": max_p,
                    "velocityPasses": max_u / throat_mean <= 0.005,
                    "pressurePasses": max_p <= 1.0,
                }
            )
    return {
        "pairs": pairs,
        "allPairsPass": all(
            pair["velocityPasses"] and pair["pressurePasses"] for pair in pairs
        ),
        "velocityThresholdOfThroatMean": 0.005,
        "pressureThresholdPa": 1.0,
    }


def _select_contract(
    rows: Sequence[dict[str, Any]], domain: dict[str, Any]
) -> dict[str, Any]:
    all_numerical = all(row["numericalGate"]["passes"] for row in rows)
    outlet = "short" if domain["allPairsPass"] else "extended"
    by_label = {row["label"]: row for row in rows}
    bounded = by_label[f"{outlet}__bounded__enhanced"]
    second = by_label[f"{outlet}__second-order__enhanced"]
    second_admissible = second["numericalGate"]["passes"] and (
        float(second["responses"]["compositeDiagnosticScore"])
        <= 1.02 * float(bounded["responses"]["compositeDiagnosticScore"])
    )
    scheme = "second-order" if second_admissible else "bounded"
    base = by_label[f"{outlet}__{scheme}__base"]
    enhanced = by_label[f"{outlet}__{scheme}__enhanced"]
    enhanced_admissible = enhanced["numericalGate"]["passes"] and (
        float(enhanced["responses"]["compositeDiagnosticScore"])
        <= 1.10 * float(base["responses"]["compositeDiagnosticScore"])
    )
    recommendation_ready = all_numerical and enhanced_admissible
    return {
        "allEightNumericallyAdmissible": all_numerical,
        "outlet": outlet,
        "outletM": OUTLETS_M[outlet],
        "scheme": scheme,
        "resolution": "enhanced" if enhanced_admissible else None,
        "secondOrderAdmissibleByFrozenRule": second_admissible,
        "enhancedAdmissibleByFrozenRule": enhanced_admissible,
        "recommendationReady": recommendation_ready,
        "selectedMatrixCase": (
            f"{outlet}__{scheme}__enhanced" if recommendation_ready else None
        ),
    }


def _full_campaign_contract(
    assessment_sha256: str, selection: dict[str, Any]
) -> dict[str, Any]:
    ready = bool(selection["recommendationReady"])
    levels = [
        {
            "name": name,
            "linearScale": scale,
            "cells": {
                "coreTangential": 2 * scale,
                "annularTangential": 2 * scale,
                "upstreamAnnularRadial": 1 * scale,
                "inletAxial": 58 * scale,
                "contractionAxial": 45 * scale,
                "throatAxial": 80 * scale,
                "nearDownstreamAxial": 240 * scale,
                "downstreamOuterRadial": 8 * scale,
                "farExtensionAxial": (
                    150 * scale if selection["outlet"] == "extended" else 0
                ),
            },
        }
        for name, scale in (("coarse", 1), ("medium", 2), ("fine", 4))
    ]
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-full-campaign-contract.v1",
        "status": "frozen-ready-to-prepare" if ready else "blocked-by-preflight",
        "basis": {
            "preflightAssessmentSha256": assessment_sha256,
            "selection": selection,
        },
        "numericalContract": {
            "outletM": selection["outletM"],
            "scheme": selection["scheme"],
            "localResolution": selection["resolution"],
            "strictAllHex": True,
            "nestedLinearRefinementRatio": 2,
            "levels": levels,
            "mediumReproducesSelectedPreflightTopology": ready,
        },
        "validationContract": {
            "observation": observation_contract(),
            "uncertainties": [
                "experimental",
                "input",
                "iterative",
                "grid/GCI",
                "PIV observation operator",
            ],
            "comparisons": [
                "axial and radial velocity profiles",
                "centreline and wall pressure",
                "offset-free pressure differences and pressure drop",
                "wall shear/viscous traction diagnostic",
                "flow conservation",
                "OpenFOAM force object versus direct face integration",
                "ASME V&V 20 comparison error and validation uncertainty",
            ],
            "existingPromotionGatesRemainUnchanged": True,
        },
        "authorization": {
            "mayPrepareAndRunFullCampaign": ready,
            "desktopPromotion": False,
            "scientificClaim": False,
        },
        "promotionAuthorized": False,
    }


def assess_preflight(output: Path) -> dict[str, Any]:
    contract_path = output / "preflight-contract.json"
    manifest = _read_json(output / "preflight-manifest.json")
    if _sha256(contract_path) != manifest["contractSha256"]:
        raise ValueError("preflight contract changed after preparation")
    rows = [_case_result(output, factors) for factors in matrix_cases()]
    effects = _factorial_effects(rows)
    domain = _domain_independence(rows)
    selection = _select_contract(rows, domain)

    v1_piv = {
        label: _read_json(
            output
            / "v1-reanalysis"
            / "results"
            / label
            / "piv-observation.json"
        )
        for label in _v1_labels()
    }
    v1_spatial = v1_spatial_validation(v1_piv)
    experiment = _read_json(output / "experiment" / "experimental-data.json")
    v1_pressure_diagnostics = {
        label: pressure_diagnostic(
            experiment,
            _read_json(
                output
                / "v1-reanalysis"
                / "baseline-results"
                / label
                / "observation.json"
            ),
        )
        for label in _v1_labels()
    }
    v1_pressure = _v1_pressure_validation(v1_pressure_diagnostics)
    _write_json(output / "v1-reanalysis" / "spatial-validation.json", v1_spatial)
    _write_json(output / "v1-reanalysis" / "pressure-validation.json", v1_pressure)

    assessment = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "assessedAt": _now(),
        "status": (
            "preflight-complete-contract-recommended"
            if selection["recommendationReady"]
            else "preflight-complete-contract-blocked"
        ),
        "contractSha256": _sha256(contract_path),
        "matrix": [
            {
                key: value
                for key, value in row.items()
                if key not in {"piv", "pressure"}
            }
            for row in rows
        ],
        "factorialEffects": effects,
        "domainIndependence": domain,
        "selection": selection,
        "v1Reanalysis": {
            "spatial": {
                "profiles": v1_spatial["profiles"],
                "centreline": v1_spatial["centreline"],
            },
            "pressure": {
                kind: {
                    group: v1_pressure[kind][group]["counts"]
                    for group in ("adjacent", "named")
                }
                for kind in ("wall", "centreline")
            },
            "v1VerdictChanged": False,
        },
        "desktopPromotionAuthorized": False,
        "scientificClaimAuthorized": False,
        "promotionAuthorized": False,
    }
    _write_json(output / "preflight-assessment.json", assessment)
    assessment_sha = _sha256(output / "preflight-assessment.json")
    full_contract = _full_campaign_contract(assessment_sha, selection)
    _write_json(output / "v2-full-campaign-contract.json", full_contract)

    issues: list[dict[str, Any]] = []
    for row in rows:
        if not row["numericalGate"]["passes"]:
            issues.append(
                {
                    "id": f"numerical-{row['label']}",
                    "severity": "blocker",
                    "source": "preflight matrix",
                    "issue": "one or more numerical gates failed",
                    "evidence": row["numericalGate"],
                    "interference": "blocks all automatic factor selection",
                }
            )
        solver = row["solverDiagnostic"]
        if int(solver["cappedPressureSolveCount"]) > 0:
            issues.append(
                {
                    "id": f"pressure-conditioning-{row['label']}",
                    "severity": (
                        "iterative-risk"
                        if not solver["finalIterationMeetsConfiguredLinearTolerance"]
                        else "resolved-startup-cost"
                    ),
                    "source": "retained OpenFOAM foamRun log",
                    "issue": "one or more pressure solves reached the 1000-iteration cap",
                    "evidence": solver,
                    "interference": (
                        "outlet length and resolution can increase iterative cost and "
                        "contaminate factor responses if final fields are not stable"
                    ),
                }
            )
    if not domain["allPairsPass"]:
        issues.append(
            {
                "id": "short-outlet-domain-dependence",
                "severity": "resolved-by-contract-selection",
                "source": "matched short versus extended matrix pairs",
                "issue": "short outlet exceeded at least one frozen near-field threshold",
                "evidence": domain,
                "interference": "outlet error can alias scheme and mesh error",
                "resolution": "extended outlet selected for the full campaign",
            }
        )
    issues.append(
        {
            "id": "v1-scientific-promotion-remains-blocked",
            "severity": "expected",
            "source": "prospective v1 observation-operator reanalysis",
            "issue": "preflight diagnostics cannot retroactively change the v1 verdict",
            "evidence": assessment["v1Reanalysis"],
            "interference": "none; full v2 must independently pass every existing gate",
        }
    )
    issue_text = "\n".join(json.dumps(issue, sort_keys=True) for issue in issues)
    _write(output / "issues.jsonl", issue_text + ("\n" if issue_text else ""))

    profile_counts = v1_spatial["profiles"]["counts"]
    centreline_counts = v1_spatial["centreline"]["counts"]
    report = [
        "# FDA nozzle Re=500 v2 preflight",
        "",
        f"Status: **{assessment['status']}**",
        "",
        "This 2^3 matrix is diagnostic only. It does not authorize the scientific claim or desktop promotion.",
        "",
        "## Frozen selection",
        "",
        f"- Outlet: {selection['outlet']} ({selection['outletM']:.3f} m)",
        f"- Spatial scheme: {selection['scheme']}",
        f"- Local resolution: {selection['resolution']}",
        f"- All eight numerical gates: {'PASS' if selection['allEightNumericallyAdmissible'] else 'FAIL'}",
        f"- Full campaign contract: {'READY' if selection['recommendationReady'] else 'BLOCKED'}",
        f"- Slowest matrix solver execution: {max(row['responses']['solverExecutionTimeSeconds'] for row in rows):.1f} s",
        "",
        "## Prospective v1 reanalysis",
        "",
        f"- PIV-window primary profile V&V 20: {profile_counts['vv20Passed']}/{profile_counts['experimentalEligible']} pass; {profile_counts['gciQualified']}/{profile_counts['experimentalEligible']} GCI-qualified.",
        f"- PIV-window centreline V&V 20: {centreline_counts['vv20Passed']}/{centreline_counts['experimentalEligible']} pass; {centreline_counts['gciQualified']}/{centreline_counts['experimentalEligible']} GCI-qualified.",
        f"- Offset-free wall adjacent pressure V&V 20: {v1_pressure['wall']['adjacent']['counts']['vv20Passed']}/{v1_pressure['wall']['adjacent']['counts']['reported']} pass.",
        f"- Offset-free named pressure V&V 20: {v1_pressure['wall']['named']['counts']['vv20Passed']}/{v1_pressure['wall']['named']['counts']['reported']} pass.",
        "- The immutable v1 promotion verdict remains blocked.",
        "",
        "## Evidence",
        "",
        "Machine-readable responses, main and interaction effects, matched outlet deltas, and gate evidence are in `preflight-assessment.json`. The next campaign contract is in `v2-full-campaign-contract.json`; conflicts and aliasing risks are in `issues.jsonl`.",
        "",
    ]
    _write(output / "REPORT.md", "\n".join(report))
    return assessment


def _apparent_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def compact_preflight(output: Path) -> dict[str, Any]:
    """Remove reproducible solver fields only after all retained evidence exists."""
    required = (
        output / "preflight-assessment.json",
        output / "v2-full-campaign-contract.json",
        output / "REPORT.md",
    )
    if not all(path.is_file() for path in required):
        raise ValueError("refusing to compact before assessment and report completion")
    before = _apparent_bytes(output)
    removed: list[str] = []
    for factors in matrix_cases():
        case = output / "cases" / factors["label"]
        for child in list(case.iterdir()):
            remove = child.name in {"postProcessing"} or (
                child.is_dir()
                and child.name != "0"
                and _is_numeric_directory(child.name)
            )
            if remove:
                shutil.rmtree(child)
                removed.append(str(child.relative_to(output)))
        for generated in (case / "constant" / "polyMesh", case / "0"):
            if generated.exists():
                shutil.rmtree(generated)
                removed.append(str(generated.relative_to(output)))
        spec = replace(FdaNozzleDefinition(), outlet_x_m=float(factors["outletM"]))
        templates = _case_files(str(factors["label"]), 2, 1.0, spec)
        _write(case / "0" / "U", templates["0/U"])
        _write(case / "0" / "p", templates["0/p"])

    v1_cases = output / "v1-reanalysis" / "cases"
    if v1_cases.exists():
        shutil.rmtree(v1_cases)
        removed.append(str(v1_cases.relative_to(output)))
    after = _apparent_bytes(output)
    report = {
        "schema": "flowlab.fda-nozzle-re500-v2-preflight-compaction.v1",
        "compactedAt": _now(),
        "policy": (
            "remove reproducible OpenFOAM meshes, time fields, and raw function-object "
            "outputs only after retained execution, face-integration, observation, "
            "diagnostic, assessment, logs, and contracts are complete"
        ),
        "apparentBytesBefore": before,
        "apparentBytesAfter": after,
        "apparentBytesRemoved": before - after,
        "removed": removed,
        "promotionAuthorized": False,
    }
    _write_json(output / "compaction.json", report)
    return report


def _is_numeric_directory(name: str) -> bool:
    try:
        float(name)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract_parser = subparsers.add_parser("contract")
    contract_parser.add_argument("--output", type=Path, required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--v1", type=Path, required=True)
    execute_parser = subparsers.add_parser("execute-case")
    execute_parser.add_argument("--output", type=Path, required=True)
    execute_parser.add_argument("--label", required=True)
    execute_parser.add_argument("--image", default=DEFAULT_IMAGE)
    post_parser = subparsers.add_parser("postprocess-case")
    post_parser.add_argument("--output", type=Path, required=True)
    post_parser.add_argument("--label", required=True)
    post_parser.add_argument("--image", default=DEFAULT_IMAGE)
    v1_parser = subparsers.add_parser("reanalyze-v1-case")
    v1_parser.add_argument("--output", type=Path, required=True)
    v1_parser.add_argument("--label", required=True)
    v1_parser.add_argument("--image", default=DEFAULT_IMAGE)
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--output", type=Path, required=True)
    compact_parser = subparsers.add_parser("compact")
    compact_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "contract":
        _write_json(args.output.resolve(), preflight_contract())
        return 0
    if args.command == "prepare":
        report = prepare_preflight(args.output.resolve(), args.v1.resolve())
    elif args.command == "execute-case":
        report = execute_case(args.output.resolve(), args.label, args.image)
    elif args.command == "postprocess-case":
        report = postprocess_preflight_case(
            args.output.resolve(), args.label, args.image
        )
    elif args.command == "reanalyze-v1-case":
        report = reanalyze_v1_case(args.output.resolve(), args.label, args.image)
    elif args.command == "compact":
        report = compact_preflight(args.output.resolve())
    else:
        report = assess_preflight(args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.command == "assess":
        return 0 if report["selection"]["recommendationReady"] else 3
    if args.command == "compact":
        return 0
    status = report.get("status")
    if status is None and "pivObservation" in report:
        status = report["pivObservation"].get("status")
    return 0 if status in {"prepared-not-executed", "solver-complete", "observed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
