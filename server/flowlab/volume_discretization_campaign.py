#!/usr/bin/env python3
"""Compare two immutable-surface internal-volume discretizations.

This is deliberately a *one-level* campaign.  Each candidate must preserve
the frozen master, clear the full OpenFOAM mesh gate, and then clear an
exactly initialized Poiseuille gate before it can be compared for cell
efficiency.  It never starts GCI or performance work.

Run inside the pinned OpenFOAM/Gmsh image: it needs ``gmshToFoam``,
``checkMesh``, ``foamPostProcess``, and ``foamRun``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .cad_parabolic_smoke import CadParabolicSmokeSpec, materialize_cad_parabolic_smoke_case
from .gmsh_immutable_surface_probe import msh2_surface_fingerprint
from .immutable_surface_gates import audit_immutable_surface_candidate
from .layered_immutable_screen import _write_minimal_case


SCHEMA = "flowlab.volume-discretization-campaign.v6"
_EXACT_SMOKE_SPEC = CadParabolicSmokeSpec()
EXPECTED_DP = (
    8.0
    * (_EXACT_SMOKE_SPEC.dynamic_viscosity_pa_s / _EXACT_SMOKE_SPEC.density_kg_m3)
    * _EXACT_SMOKE_SPEC.length_m
    * _EXACT_SMOKE_SPEC.volumetric_flow_rate_m3_s
    / (math.pi * _EXACT_SMOKE_SPEC.radius_m**4)
)
MAX_EXACT_DP_ERROR = 0.01
MAX_MASS_RELATIVE_IMBALANCE = 1.0e-6
MAX_FINAL_U_RESIDUAL = 1.0e-8


@dataclass(frozen=True)
class Candidate:
    identifier: str
    description: str
    first_layer_m: float
    layer_count: int
    growth_ratio: float
    core_size_m: float
    core_interface_chords: int | None
    transition_thickness_m: float | None
    algorithm_3d: int
    smoothing_steps: int
    volume_strategy_version: str


def candidates(wall_chord_m: float) -> tuple[Candidate, Candidate]:
    """Return the two bounded, materially different v6 volume candidates."""

    return (
        Candidate(
            identifier="prismatic-direct-core-v6",
            description=(
                "Ten smooth swept triangular-prism layers feed the full-density inner "
                "surface directly to an Algorithm-4 core. This removes the unstructured "
                "coarsening annulus between the prism band and core."
            ),
            first_layer_m=0.50 * wall_chord_m,
            layer_count=10,
            growth_ratio=1.05,
            core_size_m=0.003,
            core_interface_chords=None,
            transition_thickness_m=None,
            algorithm_3d=4,
            smoothing_steps=30,
            volume_strategy_version="v6",
        ),
        Candidate(
            identifier="frontal-dense-core-v6",
            description=(
                "Four prism layers feed a 192-chord inner interface, then a Netgen-smoothed "
                "Gmsh Frontal transition and core. Algorithm 1 is permanently rejected for "
                "the discrete annular-transition path."
            ),
            first_layer_m=0.5 * wall_chord_m,
            layer_count=4,
            growth_ratio=1.15,
            core_size_m=0.003,
            core_interface_chords=192,
            transition_thickness_m=0.00060,
            algorithm_3d=4,
            smoothing_steps=30,
            volume_strategy_version="v6",
        ),
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(command: Sequence[str], log: Path, *, cwd: Path | None = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    except OSError as error:
        log.write_text(f"command could not be executed: {error}\n", encoding="utf-8")
        return 127
    log.write_text(completed.stdout, encoding="utf-8")
    return completed.returncode


def _paths(root: Path, identifier: str) -> dict[str, Path]:
    candidate = root / "candidates" / identifier
    artifacts = candidate / "artifacts"
    foam = candidate / "foam"
    smoke = candidate / "exact-init-smoke"
    return {
        "candidate": candidate, "artifacts": artifacts, "foam": foam, "smoke": smoke,
        "volume": candidate / "volume.msh",
        "configuration": artifacts / "configuration.json",
        "layeredReport": artifacts / "layered-report.json",
        "gmshLog": artifacts / "gmsh.log",
        "coreDebug": artifacts / "core-debug.msh",
        "gmshToFoamLog": foam / "gmshToFoam.log",
        "checkMeshLog": foam / "checkMesh.log",
        "meshGate": artifacts / "mesh-gate-report.json",
        "exactInit": smoke / "exact-init.json",
        "cellCentresLog": smoke / "log.writeCellCentres",
        "solverLog": smoke / "log.foamRun",
        "exactGate": artifacts / "exact-init-gate-report.json",
    }


def _last_table_value(path: Path) -> float | None:
    if not path.is_file():
        return None
    result: float | None = None
    for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = row.split()
        if not row.lstrip().startswith("#") and len(fields) >= 2:
            try:
                result = float(fields[-1])
            except ValueError:
                pass
    return result


def _function_value(case: Path, name: str) -> float | None:
    paths = sorted((case / "postProcessing" / name).glob("*/surfaceFieldValue.dat"))
    return _last_table_value(paths[-1]) if paths else None


def _last_u_residual(log: Path) -> float | None:
    # The exact-init contract is a *final linear-solver residual* gate.  The
    # initial residual is an outer-SIMPLE defect and cannot be substituted for
    # the residual left by the U linear solve; doing so labels a fully solved
    # component as failed solely because its next nonlinear iteration starts
    # with a non-zero correction.
    values = [float(value) for value in re.findall(r"Solving for U[xyz], Initial residual = [0-9.eE+-]+, Final residual = ([0-9.eE+-]+)", log.read_text(encoding="utf-8", errors="replace"))]
    return max(values[-3:]) if values else None


def _assert_exact_iteration_limit(control_dict: Path, iteration_limit: int) -> None:
    """Fail before solver launch if the retained case cannot be the declared gate."""
    text = control_dict.read_text(encoding="utf-8")
    match = re.search(r"\bendTime\s+(\d+)\s*;", text)
    if match is None or int(match.group(1)) != iteration_limit:
        observed = None if match is None else match.group(1)
        raise RuntimeError(
            f"exact-init controlDict endTime {observed!r} does not equal iteration limit {iteration_limit}"
        )


def _exact_gate(paths: dict[str, Path], *, solver_status: int, iteration_limit: int) -> dict[str, Any]:
    smoke = paths["smoke"]
    inlet_p = _function_value(smoke, "inletPressure")
    outlet_p = _function_value(smoke, "outletPressure")
    inlet_q = _function_value(smoke, "inletFlux")
    outlet_q = _function_value(smoke, "outletFlux")
    pressure_drop = inlet_p - outlet_p if inlet_p is not None and outlet_p is not None else None
    dp_error = abs(pressure_drop - EXPECTED_DP) / EXPECTED_DP if pressure_drop is not None else None
    flux_imbalance = abs(inlet_q + outlet_q) / 1.0e-5 if inlet_q is not None and outlet_q is not None else None
    u_residual = _last_u_residual(paths["solverLog"])
    reasons: list[str] = []
    if solver_status != 0:
        reasons.append("foamRun did not complete successfully")
    if pressure_drop is None or dp_error is None:
        reasons.append("pressure-drop QoI was not retained")
    elif dp_error > MAX_EXACT_DP_ERROR:
        reasons.append(f"analytical pressure-drop relative error {dp_error:.8g} exceeds {MAX_EXACT_DP_ERROR:.8g}")
    if flux_imbalance is None:
        reasons.append("inlet/outlet flux QoIs were not retained")
    elif flux_imbalance > MAX_MASS_RELATIVE_IMBALANCE:
        reasons.append(f"mass relative imbalance {flux_imbalance:.8g} exceeds {MAX_MASS_RELATIVE_IMBALANCE:.8g}")
    if u_residual is None:
        reasons.append("final U residual was not retained")
    elif u_residual > MAX_FINAL_U_RESIDUAL:
        reasons.append(f"final U residual {u_residual:.8g} exceeds {MAX_FINAL_U_RESIDUAL:.8g}")
    return {
        "schema": "flowlab.exact-initialized-poiseuille-gate.v1",
        "accepted": not reasons,
        "rejectionReasons": reasons,
        "iterationLimit": iteration_limit,
        "expectedKinematicPressureDropM2PerS2": EXPECTED_DP,
        "qoi": {
            "inletPressure": inlet_p, "outletPressure": outlet_p,
            "pressureDrop": pressure_drop, "pressureDropRelativeError": dp_error,
            "inletFlux": inlet_q, "outletFlux": outlet_q,
            "massRelativeImbalance": flux_imbalance, "finalUResidual": u_residual,
        },
        "artifacts": {"solverLog": str(paths["solverLog"]), "cellCentresLog": str(paths["cellCentresLog"]), "exactInitialization": str(paths["exactInit"])},
    }


def _run_candidate(
    root: Path,
    master: Path,
    expected_sha: str,
    candidate: Candidate,
    *,
    iteration_limit: int,
    run_exact_init: bool = True,
) -> dict[str, Any]:
    paths = _paths(root, candidate.identifier)
    paths["artifacts"].mkdir(parents=True, exist_ok=True)
    build = [
        sys.executable, "-m", "server.flowlab.layered_immutable_volume",
        "--master", str(master), "--output", str(paths["volume"]), "--report", str(paths["layeredReport"]),
        "--core-debug-msh", str(paths["coreDebug"]), "--configuration", str(paths["configuration"]),
        "--gmsh-log", str(paths["gmshLog"]), "--expected-surface-sha256", expected_sha,
        "--first-layer-m", str(candidate.first_layer_m), "--layer-count", str(candidate.layer_count),
        "--growth-ratio", str(candidate.growth_ratio), "--core-size-m", str(candidate.core_size_m),
        "--algorithm-3d", str(candidate.algorithm_3d), "--smoothing-steps", str(candidate.smoothing_steps),
        "--volume-strategy-id", candidate.identifier, "--volume-strategy-version", candidate.volume_strategy_version,
    ]
    if candidate.core_interface_chords is not None:
        build.extend(["--core-interface-chords", str(candidate.core_interface_chords)])
    if candidate.transition_thickness_m is not None:
        build.extend(["--transition-thickness-m", str(candidate.transition_thickness_m)])
    statuses: dict[str, int | None] = {"builder": _run(build, paths["artifacts"] / "builder-command.log"), "gmshToFoam": None, "checkMesh": None, "writeCellCentres": None, "foamRun": None}
    if statuses["builder"] == 0:
        _write_minimal_case(paths["foam"])
        statuses["gmshToFoam"] = _run(["gmshToFoam", "-case", str(paths["foam"]), str(paths["volume"])], paths["gmshToFoamLog"])
        if statuses["gmshToFoam"] == 0:
            statuses["checkMesh"] = _run(["checkMesh", "-case", str(paths["foam"]), "-allGeometry", "-allTopology"], paths["checkMeshLog"])
    mesh_gate = audit_immutable_surface_candidate(
        surface_msh=master, volume_msh=paths["volume"], surface_report=paths["layeredReport"], configuration=paths["configuration"],
        gmsh_log=paths["gmshLog"], gmsh_to_foam_log=paths["gmshToFoamLog"], checkmesh_log=paths["checkMeshLog"], poly_mesh_dir=paths["foam"] / "constant" / "polyMesh",
    )
    mesh_gate["commandStatus"] = statuses.copy()
    _write_json(paths["meshGate"], mesh_gate)
    exact_gate: dict[str, Any] | None = None
    if mesh_gate["accepted"] and run_exact_init:
        try:
            materialize_cad_parabolic_smoke_case(
                paths["smoke"],
                source_poly_mesh=paths["foam"] / "constant" / "polyMesh",
                immutable_gate_report=paths["meshGate"],
                iteration_limit=iteration_limit,
            )
            control = paths["smoke"] / "system" / "controlDict"
            _assert_exact_iteration_limit(control, iteration_limit)
            statuses["writeCellCentres"] = _run(["foamPostProcess", "-case", str(paths["smoke"]), "-func", "writeCellCentres", "-time", "0"], paths["cellCentresLog"])
            if statuses["writeCellCentres"] == 0:
                exact = subprocess.run([sys.executable, "-m", "server.flowlab.cad_parabolic_smoke", "--initialize-exact", str(paths["smoke"])], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
                paths["exactInit"].write_text(exact.stdout, encoding="utf-8")
                if exact.returncode == 0:
                    statuses["foamRun"] = _run(["foamRun", "-case", str(paths["smoke"]), "-solver", "incompressibleFluid"], paths["solverLog"])
            exact_gate = _exact_gate(paths, solver_status=int(statuses["foamRun"] if statuses["foamRun"] is not None else 127), iteration_limit=iteration_limit)
        except (OSError, RuntimeError, ValueError) as error:
            exact_gate = {"schema": "flowlab.exact-initialized-poiseuille-gate.v1", "accepted": False, "rejectionReasons": [str(error)]}
    if exact_gate is not None:
        _write_json(paths["exactGate"], exact_gate)
    return {
        "candidate": asdict(candidate), "meshGate": {"accepted": mesh_gate["accepted"], "path": str(paths["meshGate"])},
        "exactInitGate": None if exact_gate is None else {"accepted": exact_gate["accepted"], "path": str(paths["exactGate"]), "qoi": exact_gate.get("qoi")},
        "commandStatus": statuses,
    }


def _select(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    accepted = [
        record
        for record in records
        if isinstance(record.get("exactInitGate"), dict)
        and record["exactInitGate"].get("accepted")
    ]
    if not accepted:
        return None
    return min(accepted, key=lambda record: (
        record["meshGate"].get("maxNonOrthogonality", float("inf")),
        record["meshGate"].get("maxSkewness", float("inf")),
        record["meshGate"].get("cells", float("inf")),
        record["candidate"]["identifier"],
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--expected-surface-sha256", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--wall-chord-m", type=float, required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        help="run only the named bounded candidate; repeat to run a deliberate subset",
    )
    parser.add_argument("--iteration-limit", type=int, default=100)
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--runtime-image-id", required=True)
    parser.add_argument("--runtime-architecture", default="unknown")
    parser.add_argument(
        "--runtime-evidence-class",
        default="functional-meshing-and-cfd-only",
        help="provenance classification; never infer performance portability from a functional run",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild the campaign aggregate from retained candidate evidence without rerunning candidates",
    )
    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="run immutable-surface, import, and full checkMesh gates without a CFD solve",
    )
    args = parser.parse_args()
    if args.iteration_limit < 1:
        parser.error("--iteration-limit must be positive")
    observed = msh2_surface_fingerprint(args.master)
    if observed["surfaceSha256"] != args.expected_surface_sha256:
        parser.error("declared frozen-surface SHA-256 does not match master")
    candidate_set = candidates(args.wall_chord_m)
    if args.candidate:
        by_identifier = {candidate.identifier: candidate for candidate in candidate_set}
        unknown = sorted(set(args.candidate).difference(by_identifier))
        if unknown:
            parser.error(f"unknown --candidate value(s): {', '.join(unknown)}")
        candidate_set = tuple(by_identifier[identifier] for identifier in args.candidate)
    if args.report_only:
        records = []
        for candidate in candidate_set:
            paths = _paths(args.run_root, candidate.identifier)
            mesh_gate = json.loads(paths["meshGate"].read_text(encoding="utf-8"))
            exact_gate = (
                json.loads(paths["exactGate"].read_text(encoding="utf-8"))
                if paths["exactGate"].is_file()
                else None
            )
            records.append({
                "candidate": asdict(candidate),
                "meshGate": {"accepted": mesh_gate["accepted"], "path": str(paths["meshGate"])},
                "exactInitGate": None if exact_gate is None else {
                    "accepted": exact_gate["accepted"],
                    "path": str(paths["exactGate"]),
                    "qoi": exact_gate.get("qoi"),
                },
                "commandStatus": mesh_gate.get("commandStatus", {}),
            })
    else:
        records = [
            _run_candidate(
                args.run_root,
                args.master,
                args.expected_surface_sha256,
                candidate,
                iteration_limit=args.iteration_limit,
                run_exact_init=not args.mesh_only,
            )
            for candidate in candidate_set
        ]
    for record in records:
        gate = record.get("meshGate", {})
        path = Path(gate.get("path", ""))
        if path.is_file():
            quality = json.loads(path.read_text(encoding="utf-8")).get("checkMesh", {})
            record["meshGate"].update({
                "cells": quality.get("counts", {}).get("cells"),
                "maxNonOrthogonality": quality.get("metrics", {}).get("maxNonOrthogonality"),
                "maxSkewness": quality.get("metrics", {}).get("maxSkewness"),
            })
    selected = _select(records)
    mesh_screen_accepted = all(record["meshGate"]["accepted"] for record in records)
    status = (
        "accepted_mesh_screen_only"
        if args.mesh_only and mesh_screen_accepted
        else "rejected_mesh_screen_only"
        if args.mesh_only
        else "accepted_one_level_candidate"
        if selected
        else "rejected_no_candidate_cleared_exact_init_gate"
    )
    report = {
        "schema": SCHEMA,
        "status": status,
        "scientificStatus": "analysis-only",
        "master": {"path": str(args.master), "declaredSurfaceSha256": args.expected_surface_sha256, "observed": observed},
        "runtime": {
            "image": args.runtime_image,
            "imageId": args.runtime_image_id,
            "architecture": args.runtime_architecture,
            "evidenceClass": args.runtime_evidence_class,
        },
        "exactInitGate": {"maxPressureDropRelativeError": MAX_EXACT_DP_ERROR, "maxMassRelativeImbalance": MAX_MASS_RELATIVE_IMBALANCE, "maxFinalUResidual": MAX_FINAL_U_RESIDUAL, "iterationLimit": args.iteration_limit},
        "meshScreenOnly": args.mesh_only,
        "candidates": records,
        "selection": None if selected is None else {"identifier": selected["candidate"]["identifier"], "rule": "lowest non-orthogonality, then skewness, then cell count among candidates that clear both mesh and exact-init gates"},
    }
    _write_json(args.run_root / "artifacts" / "campaign-report.json", report)
    return 0 if (mesh_screen_accepted if args.mesh_only else selected) else 2


if __name__ == "__main__":
    raise SystemExit(main())
