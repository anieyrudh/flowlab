#!/usr/bin/env python3
"""Continue an accepted layered immutable-surface screen to a three-level mesh family.

This module is deliberately a mesh-evidence runner, not a CFD runner.  It
binds itself to a specific accepted v4 one-level screen and its explicit,
versioned non-legacy internal-volume strategy.  Every core size, including
0.003 m, is rebuilt: a frozen outer surface does not make the old dense inner
interface evidence reusable after a volume-topology redesign.  It admits only
0.003, 0.002, and 0.0015 m core targets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .gmsh_immutable_surface_probe import msh2_surface_fingerprint
from .immutable_surface_gates import audit_immutable_surface_candidate
from .layered_immutable_screen import _write_minimal_case, interface_chords_for_core_size


CORE_SIZES_M = (0.003, 0.002, 0.0015)
SCREEN_SCHEMA = "flowlab.layered-immutable-screen.v4"


class ScreenPreflightError(ValueError):
    """The screen cannot safely authorize a three-core continuation."""


@dataclass(frozen=True)
class SelectedLayerConfig:
    """The layer controls frozen by the accepted one-level selection."""

    identifier: str
    layer_count: int
    chord_multiplier: float
    first_layer_m: float
    growth_ratio: float


@dataclass(frozen=True)
class VolumeStrategyBinding:
    """The explicit, non-legacy inner-volume strategy authorized by a screen."""

    identifier: str
    version: str
    interface_chord_schedule: tuple[tuple[float, int], ...]
    transition_thickness_m: float

    def chords_for(self, core_size_m: float) -> int:
        return interface_chords_for_core_size(
            [
                {"coreSizeM": size, "interfaceChords": chords}
                for size, chords in self.interface_chord_schedule
            ],
            core_size_m,
        )


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: Any) -> Optional[int]:
    """Return a positive integer without accepting booleans as mesh counts."""

    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _positive_float(value: Any) -> Optional[float]:
    """Return a finite positive float, or ``None`` for untrusted evidence."""

    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0.0 else None


def _read_core_debug_evidence(path: Path) -> Dict[str, Any]:
    """Measure the retained Gmsh core directly, rather than trust its target size.

    The core debug MSH2 is the actual tetrahedral mesh that was merged into the
    volume.  Its mean tetrahedron volume gives a transparent, mesh-derived
    effective spacing: the edge length of an equal-volume regular tetrahedron.
    This remains meaningful if a future internal transition topology coarsens
    the cavity interface independently of the requested Gmsh size.
    """

    if not path.is_file():
        return {"valid": False, "reason": "retained core-debug MSH2 is missing", "path": str(path)}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        node_start = lines.index("$Nodes") + 1
        node_count = int(lines[node_start])
        node_rows = lines[node_start + 1 : node_start + 1 + node_count]
        if len(node_rows) != node_count or lines[node_start + 1 + node_count] != "$EndNodes":
            raise ValueError("malformed $Nodes section")
        nodes = {
            int(row.split()[0]): tuple(float(value) for value in row.split()[1:4])
            for row in node_rows
        }
        if len(nodes) != node_count:
            raise ValueError("duplicate or malformed node identifiers")
        element_start = lines.index("$Elements") + 1
        element_count = int(lines[element_start])
        element_rows = lines[element_start + 1 : element_start + 1 + element_count]
        if len(element_rows) != element_count or lines[element_start + 1 + element_count] != "$EndElements":
            raise ValueError("malformed $Elements section")
    except (OSError, UnicodeDecodeError, ValueError, IndexError) as error:
        return {"valid": False, "reason": "cannot parse retained core-debug MSH2: %s" % error, "path": str(path)}

    tetrahedra = 0
    total_volume = 0.0
    try:
        for row in element_rows:
            fields = row.split()
            if len(fields) < 4 or int(fields[1]) != 4:
                continue
            tag_count = int(fields[2])
            vertex_ids = [int(value) for value in fields[3 + tag_count :]]
            if len(vertex_ids) != 4:
                raise ValueError("linear tetrahedron does not have four vertices")
            a, b, c, d = (nodes[vertex] for vertex in vertex_ids)
            determinant = (
                (b[0] - a[0]) * ((c[1] - a[1]) * (d[2] - a[2]) - (c[2] - a[2]) * (d[1] - a[1]))
                - (b[1] - a[1]) * ((c[0] - a[0]) * (d[2] - a[2]) - (c[2] - a[2]) * (d[0] - a[0]))
                + (b[2] - a[2]) * ((c[0] - a[0]) * (d[1] - a[1]) - (c[1] - a[1]) * (d[0] - a[0]))
            )
            volume = abs(determinant) / 6.0
            if not math.isfinite(volume) or volume <= 0.0:
                raise ValueError("non-positive or non-finite core tetrahedron volume")
            total_volume += volume
            tetrahedra += 1
    except (ValueError, KeyError) as error:
        return {"valid": False, "reason": "invalid core tetrahedron evidence: %s" % error, "path": str(path)}
    if tetrahedra == 0 or not math.isfinite(total_volume) or total_volume <= 0.0:
        return {"valid": False, "reason": "core-debug MSH2 has no positive tetrahedra", "path": str(path)}
    mean_tetra_volume = total_volume / tetrahedra
    # Volume of a regular tetrahedron with edge h is h^3 / (6 * sqrt(2)).
    effective_spacing = (6.0 * math.sqrt(2.0) * mean_tetra_volume) ** (1.0 / 3.0)
    return {
        "valid": True,
        "path": str(path),
        "sha256": _sha256(path),
        "coreTetrahedra": tetrahedra,
        "totalCoreVolumeM3": total_volume,
        "meanCoreTetraVolumeM3": mean_tetra_volume,
        "effectiveCoreSpacingM": effective_spacing,
        "method": "equal-volume regular-tetrahedron edge from retained core-debug MSH2",
    }


def _core_debug_path(gate: Dict[str, Any]) -> Optional[Path]:
    """Locate retained core evidence, including legacy screen reports.

    Existing v2 screen records did not list ``coreDebug`` in their gate
    artifact index, although it was retained alongside ``surfaceReport``.  The
    sibling fallback keeps those records reviewable without treating an absent
    file as evidence.
    """

    artifacts = gate.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    for key in ("coreDebug", "surfaceReport"):
        item = artifacts.get(key)
        artifact_path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(artifact_path, str):
            continue
        candidate = Path(artifact_path)
        if key == "surfaceReport":
            candidate = candidate.with_name("core-debug.msh")
        if candidate.is_file():
            return candidate
    return None


def _attach_core_mesh_evidence(gate: Dict[str, Any]) -> Dict[str, Any]:
    """Attach independent core-mesh measurements to one gate record."""

    record = dict(gate)
    path = _core_debug_path(record)
    evidence = (
        _read_core_debug_evidence(path)
        if path is not None
        else {"valid": False, "reason": "no retained core-debug MSH2 artifact"}
    )
    reported = record.get("surface", {}).get("report", {})
    if evidence.get("valid") and isinstance(reported, dict):
        reported_count = _positive_int(reported.get("coreTetrahedra"))
        if reported_count is not None and reported_count != evidence["coreTetrahedra"]:
            evidence = dict(evidence)
            evidence.update({
                "valid": False,
                "reason": "core-debug tetrahedron count does not match layered builder report",
                "reportedCoreTetrahedra": reported_count,
            })
    record["coreMeshEvidence"] = evidence
    return record


def _finite_positive(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ScreenPreflightError("selected candidate has invalid %s" % name)
    if result <= 0:
        raise ScreenPreflightError("selected candidate has non-positive %s" % name)
    return result


def _load_selected_config(selected: Any) -> SelectedLayerConfig:
    if not isinstance(selected, dict):
        raise ScreenPreflightError("screen report has no selected candidate")
    identifier = selected.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ScreenPreflightError("selected candidate has no stable id")
    try:
        layer_count = int(selected["layer_count"])
    except (KeyError, TypeError, ValueError):
        raise ScreenPreflightError("selected candidate has invalid layer_count")
    if layer_count < 1:
        raise ScreenPreflightError("selected candidate has layer_count below one")
    return SelectedLayerConfig(
        identifier=identifier,
        layer_count=layer_count,
        chord_multiplier=_finite_positive(selected.get("chord_multiplier"), "chord_multiplier"),
        first_layer_m=_finite_positive(selected.get("first_layer_m"), "first_layer_m"),
        growth_ratio=_finite_positive(selected.get("growth_ratio"), "growth_ratio"),
    )


def _load_volume_strategy(screen: Dict[str, Any], selected: Any) -> VolumeStrategyBinding:
    """Require an explicit non-legacy strategy before rebuilding any level.

    A v2 surface screen alone is not authorization to reuse the old dense
    inner interface.  The redesigned campaign must name and version the
    volume strategy at either the selected-candidate or screen scope.
    """

    raw = selected.get("volumeStrategy") if isinstance(selected, dict) else None
    if raw is None:
        raw = screen.get("volumeStrategy")
    if not isinstance(raw, dict) or raw.get("legacy") is True:
        raise ScreenPreflightError("screen has no explicit non-legacy volume strategy binding")
    identifier = raw.get("id")
    version = raw.get("version")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ScreenPreflightError("volume strategy has no stable id")
    if not isinstance(version, str) or not version.strip():
        raise ScreenPreflightError("volume strategy has no stable version")
    schedule_raw = raw.get("interfaceChordSchedule")
    if not isinstance(schedule_raw, list):
        raise ScreenPreflightError("volume strategy has no required core-size-dependent interface chord schedule")
    try:
        interface_chord_schedule = tuple(
            (core_size_m, interface_chords_for_core_size(schedule_raw, core_size_m))
            for core_size_m in CORE_SIZES_M
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ScreenPreflightError("volume strategy has an invalid core-size-dependent interface chord schedule: %s" % error) from error
    transition_thickness_m = _finite_positive(raw.get("transitionThicknessM"), "volume strategy transitionThicknessM")
    if identifier != "coarsened-inner-interface":
        raise ScreenPreflightError("volume strategy is not the required coarsened inner-interface path")
    if version != "v2":
        raise ScreenPreflightError("volume strategy is not the required v4 core-size-dependent strategy version")
    return VolumeStrategyBinding(
        identifier=identifier,
        version=version,
        interface_chord_schedule=interface_chord_schedule,
        transition_thickness_m=transition_thickness_m,
    )


def _screen_master_hash(screen: Dict[str, Any], master: Path) -> Tuple[str, Dict[str, Any]]:
    master_info = screen.get("master")
    if not isinstance(master_info, dict):
        raise ScreenPreflightError("screen report has no master binding")
    expected = master_info.get("declaredSurfaceSha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ScreenPreflightError("screen report has no valid declared master surface hash")
    observed = master_info.get("observed")
    if not isinstance(observed, dict) or observed.get("surfaceSha256") != expected:
        raise ScreenPreflightError("screen report master observed hash does not match its declared hash")
    current = msh2_surface_fingerprint(master)
    if current.get("surfaceSha256") != expected:
        raise ScreenPreflightError("current master surface hash does not match the accepted screen")
    return expected, current


def load_accepted_screen(screen_report: Path, master: Path) -> Tuple[Dict[str, Any], SelectedLayerConfig, VolumeStrategyBinding, str, Dict[str, Any]]:
    """Load and bind a report which is allowed to authorize continuation."""

    try:
        raw = json.loads(screen_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScreenPreflightError("cannot read screen report: %s" % error)
    if not isinstance(raw, dict) or raw.get("schema") != SCREEN_SCHEMA:
        raise ScreenPreflightError("screen report is not an accepted v4 layered screen")
    disposition = raw.get("disposition")
    if not isinstance(disposition, dict) or disposition.get("accepted") is not True:
        raise ScreenPreflightError("screen report did not accept a one-level candidate")
    selected_raw = disposition.get("selectedCandidate")
    selected = _load_selected_config(selected_raw)
    strategy = _load_volume_strategy(raw, selected_raw)
    expected_hash, current_master = _screen_master_hash(raw, master)
    return raw, selected, strategy, expected_hash, current_master


def _strategy_from_layered_report(gate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    surface = gate.get("surface")
    report = surface.get("report") if isinstance(surface, dict) else None
    if not isinstance(report, dict):
        return None
    strategy = report.get("volumeStrategy")
    return strategy if isinstance(strategy, dict) else None


def _attach_strategy_binding(
    gate: Dict[str, Any], expected: VolumeStrategyBinding, core_size_m: float,
) -> Dict[str, Any]:
    """Record whether one rebuilt level actually used the screened strategy."""

    record = dict(gate)
    actual = _strategy_from_layered_report(record)
    matched = (
        isinstance(actual, dict)
        and actual.get("id") == expected.identifier
        and actual.get("version") == expected.version
        and actual.get("interfaceChords") == expected.chords_for(core_size_m)
        and _positive_float(actual.get("transitionThicknessM")) == expected.transition_thickness_m
        and actual.get("legacy") is not True
    )
    record["volumeStrategyBinding"] = {
        "expected": asdict(expected),
        "expectedInterfaceChordsForCoreSize": expected.chords_for(core_size_m),
        "observed": actual,
        "matched": matched,
    }
    return record


def _run_logged(command: Sequence[str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        log.write_text(completed.stdout, encoding="utf-8")
        return completed.returncode
    except OSError as error:
        log.write_text("command could not be executed: %s\n" % error, encoding="utf-8")
        return 127


def _not_run_log(path: Path, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not run: %s\n" % reason, encoding="utf-8")


def _level_paths(root: Path, core_size_m: float) -> Dict[str, Path]:
    level = root / "levels" / ("core-%.4f" % core_size_m)
    artifacts = level / "artifacts"
    foam = level / "foam"
    return {
        "level": level,
        "artifacts": artifacts,
        "foam": foam,
        "volume": level / "volume.msh",
        "configuration": artifacts / "configuration.json",
        "layeredReport": artifacts / "layered-report.json",
        "gmshLog": artifacts / "gmsh.log",
        "coreDebug": artifacts / "core-debug.msh",
        "gmshToFoamLog": foam / "gmshToFoam.log",
        "checkMeshLog": foam / "checkMesh.log",
        "gateReport": artifacts / "gate-report.json",
    }


def _run_level(
    root: Path,
    master: Path,
    expected_surface_sha256: str,
    selected: SelectedLayerConfig,
    strategy: VolumeStrategyBinding,
    core_size_m: float,
) -> Dict[str, Any]:
    """Run exactly one core size and retain enough evidence to reject it."""

    paths = _level_paths(root, core_size_m)
    paths["artifacts"].mkdir(parents=True, exist_ok=True)
    builder_status = _run_logged(
        [
            sys.executable, "-m", "server.flowlab.layered_immutable_volume",
            "--master", str(master), "--output", str(paths["volume"]),
            "--report", str(paths["layeredReport"]), "--core-debug-msh", str(paths["coreDebug"]),
            "--configuration", str(paths["configuration"]), "--gmsh-log", str(paths["gmshLog"]),
            "--expected-surface-sha256", expected_surface_sha256,
            "--first-layer-m", str(selected.first_layer_m), "--layer-count", str(selected.layer_count),
            "--growth-ratio", str(selected.growth_ratio), "--core-size-m", str(core_size_m),
            "--core-interface-chords", str(strategy.chords_for(core_size_m)),
            "--transition-thickness-m", str(strategy.transition_thickness_m),
            "--volume-strategy-version", strategy.version,
        ],
        paths["artifacts"] / "builder-command.log",
    )
    conversion_status = None
    checkmesh_status = None
    if builder_status == 0 and paths["volume"].is_file():
        _write_minimal_case(paths["foam"])
        conversion_status = _run_logged(
            ["gmshToFoam", "-case", str(paths["foam"]), str(paths["volume"])], paths["gmshToFoamLog"],
        )
        if conversion_status == 0:
            checkmesh_status = _run_logged(
                ["checkMesh", "-case", str(paths["foam"]), "-allGeometry", "-allTopology"], paths["checkMeshLog"],
            )
        else:
            _not_run_log(paths["checkMeshLog"], "gmshToFoam did not complete")
    else:
        _not_run_log(paths["gmshToFoamLog"], "layered volume builder did not produce a volume")
        _not_run_log(paths["checkMeshLog"], "layered volume builder did not produce a volume")
    gate = audit_immutable_surface_candidate(
        surface_msh=master, volume_msh=paths["volume"], surface_report=paths["layeredReport"],
        configuration=paths["configuration"], gmsh_log=paths["gmshLog"],
        gmsh_to_foam_log=paths["gmshToFoamLog"], checkmesh_log=paths["checkMeshLog"],
        poly_mesh_dir=paths["foam"] / "constant" / "polyMesh",
    )
    gate["level"] = {
        "coreSizeM": core_size_m,
        "selectedLayerConfig": asdict(selected),
        "reusedFromAcceptedScreen": False,
    }
    gate = _attach_strategy_binding(gate, strategy, core_size_m)
    gate = _attach_core_mesh_evidence(gate)
    gate["commandStatus"] = {"builder": builder_status, "gmshToFoam": conversion_status, "checkMesh": checkmesh_status}
    _write_json(paths["gateReport"], gate)
    return gate


def _report(
    root: Path,
    screen_report: Path,
    screen_sha256: str,
    master: Path,
    expected_hash: Optional[str],
    current_master: Optional[Dict[str, Any]],
    selected: Optional[SelectedLayerConfig],
    strategy: Optional[VolumeStrategyBinding],
    runtime: Dict[str, str],
    levels: List[Dict[str, Any]],
    preflight_error: Optional[str] = None,
) -> Dict[str, Any]:
    mesh_gates_passed = (
        preflight_error is None
        and len(levels) == len(CORE_SIZES_M)
        and all(level.get("accepted") is True for level in levels)
    )
    cell_counts = [level.get("checkMesh", {}).get("counts", {}).get("cells") for level in levels]
    core_evidence = [level.get("coreMeshEvidence") for level in levels]
    strategy_bindings = [level.get("volumeStrategyBinding") for level in levels]
    core_tetrahedra = [
        evidence.get("coreTetrahedra") if isinstance(evidence, dict) else None
        for evidence in core_evidence
    ]
    effective_spacing = [
        evidence.get("effectiveCoreSpacingM") if isinstance(evidence, dict) else None
        for evidence in core_evidence
    ]
    refinement_reasons: List[str] = []
    if not all(_positive_int(count) is not None for count in cell_counts):
        refinement_reasons.append("three-core evidence lacks positive checkMesh cell counts")
    elif any(later <= earlier for earlier, later in zip(cell_counts, cell_counts[1:])):
        refinement_reasons.append(
            "decreasing core target sizes did not produce strictly increasing cell counts"
        )
    if not all(isinstance(evidence, dict) and evidence.get("valid") is True for evidence in core_evidence):
        refinement_reasons.append("three-core evidence lacks valid retained core-debug mesh measurements")
    elif not all(_positive_int(count) is not None for count in core_tetrahedra):
        refinement_reasons.append("three-core evidence lacks positive core tetrahedron counts")
    elif any(later <= earlier for earlier, later in zip(core_tetrahedra, core_tetrahedra[1:])):
        refinement_reasons.append(
            "decreasing core target sizes did not produce strictly increasing measured core tetrahedra"
        )
    if all(isinstance(evidence, dict) and evidence.get("valid") is True for evidence in core_evidence):
        if not all(_positive_float(spacing) is not None for spacing in effective_spacing):
            refinement_reasons.append("three-core evidence lacks finite positive effective core spacing")
        elif any(later >= earlier for earlier, later in zip(effective_spacing, effective_spacing[1:])):
            refinement_reasons.append(
                "decreasing core target sizes did not produce strictly decreasing measured effective core spacing"
            )
    if not all(isinstance(binding, dict) and binding.get("matched") is True for binding in strategy_bindings):
        refinement_reasons.append("one or more levels did not prove the explicit non-legacy volume strategy binding")
    refinement_passed = not refinement_reasons
    accepted = mesh_gates_passed and refinement_passed
    return {
        "schema": "flowlab.layered-immutable-three-core.v1",
        "status": "accepted_three_core_mesh_family" if accepted else "rejected_three_core_mesh_family",
        "screenBinding": {
            "path": str(screen_report), "sha256": screen_sha256,
            "requiredSchema": SCREEN_SCHEMA, "selectedLayerConfig": asdict(selected) if selected else None,
            "volumeStrategy": asdict(strategy) if strategy else None,
        },
        "master": {
            "path": str(master), "declaredSurfaceSha256": expected_hash,
            "observed": current_master,
        },
        "runtime": runtime,
        "coreSizesM": list(CORE_SIZES_M),
        "levels": levels,
        "preflightError": preflight_error,
        "refinement": {
            "accepted": refinement_passed,
            "cellCountsCoarseToFine": cell_counts,
            "coreTetrahedraCoarseToFine": core_tetrahedra,
            "effectiveCoreSpacingMCoarseToFine": effective_spacing,
            "effectiveCoreSpacingMethod": "equal-volume regular-tetrahedron edge from retained core-debug MSH2",
            "coreMeshEvidence": core_evidence,
            "volumeStrategyBindings": strategy_bindings,
            "rejectionReasons": refinement_reasons,
        },
        "disposition": {
            "accepted": accepted,
            "allThreeStrictGatesPassed": mesh_gates_passed,
            "threeDistinctRefinementLevels": refinement_passed,
            "parabolicSmokeStarted": False,
            "cfdStarted": False,
            "gciStarted": False,
            "nativeTimingStarted": False,
        },
    }


def _write_readme(root: Path, report: Dict[str, Any]) -> None:
    status = "accepted" if report["disposition"]["accepted"] else "rejected"
    root.joinpath("README.md").write_text(
        (
            "# Layered immutable-surface three-core continuation\n\n"
            "Status: **%s**.\n\n" % status
        )
        + "This retains only meshing, import, and strict checkMesh evidence for 0.003, 0.002, and 0.0015 m core sizes. "
        "All three levels are rebuilt under one explicit, versioned coarsened-inner-interface strategy; the prior dense-interface 0.003 m evidence is never re-used. "
        "No CFD, smoke solve, GCI, or timing work is performed here.\n\n"
        "Advance only when `artifacts/three-core-report.json` records both `disposition.allThreeStrictGatesPassed: true` and `disposition.threeDistinctRefinementLevels: true`; the latter requires increasing imported cells and measured core tetrahedra plus decreasing mesh-derived effective core spacing.\n",
        encoding="utf-8",
    )


def run_three_core(
    root: Path,
    master: Path,
    screen_report: Path,
    runtime: Dict[str, str],
    reuse_screen_core: bool = False,
) -> Dict[str, Any]:
    """Run the bounded continuation, fail-closed before any CFD action."""

    root.mkdir(parents=True, exist_ok=True)
    screen_digest = _sha256(screen_report) if screen_report.is_file() else "unavailable"
    try:
        screen, selected, strategy, expected_hash, current_master = load_accepted_screen(screen_report, master)
        levels: List[Dict[str, Any]] = []
        # A changed inner-interface strategy invalidates the old 0.003 m
        # core evidence even while the frozen outer surface remains identical.
        # Rebuild every level so the complete family proves one strategy.
        if reuse_screen_core:
            raise ScreenPreflightError(
                "reusing the prior screen core is forbidden for a versioned non-legacy volume strategy"
            )
        sizes_to_run = CORE_SIZES_M
        for core_size_m in sizes_to_run:
            levels.append(_run_level(root, master, expected_hash, selected, strategy, core_size_m))
        levels.sort(key=lambda level: float(level["level"]["coreSizeM"]), reverse=True)
        report = _report(root, screen_report, screen_digest, master, expected_hash, current_master, selected, strategy, runtime, levels)
    except (OSError, ValueError, ScreenPreflightError) as error:
        report = _report(root, screen_report, screen_digest, master, None, None, None, None, runtime, [], str(error))
    _write_json(root / "artifacts" / "three-core-report.json", report)
    _write_readme(root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--screen-report", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--runtime-image", default="unknown")
    parser.add_argument("--runtime-image-id", default="unknown")
    parser.add_argument(
        "--reuse-screen-core", action="store_true",
        help="deprecated and rejected: a versioned internal-volume strategy requires rebuilding all three levels",
    )
    args = parser.parse_args()
    report = run_three_core(
        root=args.run_root, master=args.master, screen_report=args.screen_report,
        runtime={"image": args.runtime_image, "imageId": args.runtime_image_id},
        reuse_screen_core=args.reuse_screen_core,
    )
    return 0 if report["disposition"]["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
