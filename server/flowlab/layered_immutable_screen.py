#!/usr/bin/env python3
"""Run a fail-closed layered immutable-surface mesh-quality campaign.

Run this module *inside* the pinned OpenFOAM/Gmsh runtime.  It executes only
the bounded one-core candidate matrix.  It never launches the three-core-size
suite; a caller must inspect the accepted screen report before scheduling that
separate next stage.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .gmsh_immutable_surface_probe import msh2_surface_fingerprint
from .immutable_surface_gates import audit_immutable_surface_candidate


DEFAULT_LAYER_COUNTS = (2, 4, 8)
DEFAULT_CHORD_MULTIPLIERS = (0.5, 1.0, 2.0)
DEFAULT_CORE_SIZES_M = (0.003, 0.002, 0.0015)
SCREEN_SCHEMA = "flowlab.layered-immutable-screen.v4"
VOLUME_STRATEGY = {"id": "coarsened-inner-interface", "version": "v2"}
DEFAULT_INTERFACE_CHORD_SCHEDULE = ((0.003, 64), (0.002, 96), (0.0015, 128))

_CONTROL_DICT = """FoamFile
{
    format      ascii;
    class       dictionary;
    object      controlDict;
}
application     checkMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
"""


@dataclass(frozen=True)
class ScreenCandidate:
    layer_count: int
    chord_multiplier: float
    first_layer_m: float
    core_size_m: float
    growth_ratio: float

    @property
    def identifier(self) -> str:
        return f"layers{self.layer_count}-chord{self.chord_multiplier:.1f}"


def _matching_core_size(value: float) -> float | None:
    """Return the canonical campaign core size matching ``value``, if any."""

    return next(
        (expected for expected in DEFAULT_CORE_SIZES_M if math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-12)),
        None,
    )


def normalize_interface_chord_schedule(
    schedule: Sequence[tuple[float, int]],
) -> tuple[tuple[float, int], ...]:
    """Bind exactly one valid internal-interface density to every core level."""

    normalized: dict[float, int] = {}
    for core_size_m, chords in schedule:
        canonical_size = _matching_core_size(float(core_size_m))
        if canonical_size is None:
            raise ValueError("interface chord schedule contains a core size outside the bounded three-level campaign")
        if canonical_size in normalized:
            raise ValueError("interface chord schedule repeats a core size")
        if isinstance(chords, bool) or int(chords) != chords or int(chords) < 16 or int(chords) % 8:
            raise ValueError("interface chord schedule values must be multiples of eight and at least sixteen")
        normalized[canonical_size] = int(chords)
    if set(normalized) != set(DEFAULT_CORE_SIZES_M):
        raise ValueError("interface chord schedule must bind all and only the three declared core sizes")
    return tuple((core_size_m, normalized[core_size_m]) for core_size_m in DEFAULT_CORE_SIZES_M)


def parse_interface_chord_schedule(value: str) -> tuple[tuple[float, int], ...]:
    """Parse ``core-size:chords`` pairs without permitting implicit defaults."""

    entries: list[tuple[float, int]] = []
    try:
        for item in value.split(","):
            core_size, chords = item.split(":", 1)
            entries.append((float(core_size), int(chords)))
    except (TypeError, ValueError):
        raise ValueError("interface chord schedule must use core-size:chords pairs separated by commas") from None
    return normalize_interface_chord_schedule(entries)


def interface_chords_for_core_size(schedule: Sequence[dict[str, Any]], core_size_m: float) -> int:
    """Read the declared chord count for one canonical core target."""

    try:
        normalized = normalize_interface_chord_schedule(
            tuple((float(entry["coreSizeM"]), int(entry["interfaceChords"])) for entry in schedule)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("volume strategy has no valid interface chord schedule: %s" % error) from error
    canonical_size = _matching_core_size(core_size_m)
    if canonical_size is None:
        raise ValueError("candidate core size is outside the bounded campaign")
    return dict(normalized)[canonical_size]


def bounded_candidates(*, wall_chord_m: float, core_size_m: float, growth_ratio: float) -> tuple[ScreenCandidate, ...]:
    """The intentionally bounded 2/4/8 by 0.5/1/2-chord screen matrix."""

    if wall_chord_m <= 0 or core_size_m <= 0 or growth_ratio < 1:
        raise ValueError("wall chord/core size must be positive and growth ratio must be at least one")
    return tuple(
        ScreenCandidate(
            layer_count=layer_count,
            chord_multiplier=multiplier,
            first_layer_m=wall_chord_m * multiplier,
            core_size_m=core_size_m,
            growth_ratio=growth_ratio,
        )
        for layer_count in DEFAULT_LAYER_COUNTS
        for multiplier in DEFAULT_CHORD_MULTIPLIERS
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_logged(command: Sequence[str], *, log: Path) -> int:
    """Run one retained-evidence command without treating its status as quality."""

    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        log.write_text(completed.stdout, encoding="utf-8")
        return completed.returncode
    except OSError as error:
        log.write_text(f"command could not be executed: {error}\n", encoding="utf-8")
        return 127


def _candidate_paths(root: Path, identifier: str) -> dict[str, Path]:
    candidate = root / "candidates" / identifier
    artifacts = candidate / "artifacts"
    foam = candidate / "foam"
    return {
        "candidate": candidate,
        "artifacts": artifacts,
        "foam": foam,
        "volume": candidate / "volume.msh",
        "configuration": artifacts / "configuration.json",
        "layeredReport": artifacts / "layered-report.json",
        "gmshLog": artifacts / "gmsh.log",
        "coreDebug": artifacts / "core-debug.msh",
        "gmshToFoamLog": foam / "gmshToFoam.log",
        "checkMeshLog": foam / "checkMesh.log",
        "gateReport": artifacts / "gate-report.json",
    }


def _write_minimal_case(foam: Path) -> None:
    """Create the only OpenFOAM case dictionary checkMesh requires."""

    system = foam / "system"
    system.mkdir(parents=True, exist_ok=True)
    (system / "controlDict").write_text(_CONTROL_DICT, encoding="utf-8")


def _run_candidate(
    *,
    root: Path,
    master: Path,
    expected_surface_sha256: str,
    candidate: ScreenCandidate,
    volume_strategy: dict[str, Any],
) -> dict[str, Any]:
    paths = _candidate_paths(root, candidate.identifier)
    paths["artifacts"].mkdir(parents=True, exist_ok=True)
    builder_status = _run_logged(
        [
            sys.executable,
            "-m",
            "server.flowlab.layered_immutable_volume",
            "--master",
            str(master),
            "--output",
            str(paths["volume"]),
            "--report",
            str(paths["layeredReport"]),
            "--core-debug-msh",
            str(paths["coreDebug"]),
            "--configuration",
            str(paths["configuration"]),
            "--gmsh-log",
            str(paths["gmshLog"]),
            "--expected-surface-sha256",
            expected_surface_sha256,
            "--first-layer-m",
            str(candidate.first_layer_m),
            "--layer-count",
            str(candidate.layer_count),
            "--growth-ratio",
            str(candidate.growth_ratio),
            "--core-size-m",
            str(candidate.core_size_m),
            "--core-interface-chords",
            str(interface_chords_for_core_size(volume_strategy["interfaceChordSchedule"], candidate.core_size_m)),
            "--transition-thickness-m",
            str(volume_strategy["transitionThicknessM"]),
            "--volume-strategy-version",
            str(volume_strategy["version"]),
        ],
        log=paths["artifacts"] / "builder-command.log",
    )
    conversion_status: int | None = None
    checkmesh_status: int | None = None
    if builder_status == 0 and paths["volume"].is_file():
        _write_minimal_case(paths["foam"])
        conversion_status = _run_logged(
            ["gmshToFoam", "-case", str(paths["foam"]), str(paths["volume"])],
            log=paths["gmshToFoamLog"],
        )
        if conversion_status == 0:
            checkmesh_status = _run_logged(
                ["checkMesh", "-case", str(paths["foam"]), "-allGeometry", "-allTopology"],
                log=paths["checkMeshLog"],
            )
    gate = audit_immutable_surface_candidate(
        surface_msh=master,
        volume_msh=paths["volume"],
        surface_report=paths["layeredReport"],
        configuration=paths["configuration"],
        gmsh_log=paths["gmshLog"],
        gmsh_to_foam_log=paths["gmshToFoamLog"],
        checkmesh_log=paths["checkMeshLog"],
        poly_mesh_dir=paths["foam"] / "constant" / "polyMesh",
    )
    gate["commandStatus"] = {
        "builder": builder_status,
        "gmshToFoam": conversion_status,
        "checkMesh": checkmesh_status,
    }
    candidate_record = asdict(candidate)
    candidate_record["id"] = candidate.identifier
    candidate_record["volumeStrategy"] = volume_strategy
    gate["candidate"] = candidate_record
    _write_json(paths["gateReport"], gate)
    return gate


def accepted_candidates(reports: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [report for report in reports if report.get("accepted") is True]


def select_candidate(reports: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Apply the declared non-orthogonality, skewness, then cell-count order."""

    accepted = accepted_candidates(reports)
    if not accepted:
        return None

    def quality_key(report: dict[str, Any]) -> tuple[float, float, float, str]:
        checkmesh = report.get("checkMesh", {})
        metrics = checkmesh.get("metrics", {})
        counts = checkmesh.get("counts", {})
        candidate = report.get("candidate", {})
        return (
            float(metrics.get("maxNonOrthogonality", float("inf"))),
            float(metrics.get("maxSkewness", float("inf"))),
            float(counts.get("cells", float("inf"))),
            str(candidate.get("id", "")),
        )

    return min(accepted, key=quality_key)


def _screen_report(
    *,
    root: Path,
    master: Path,
    expected_surface_sha256: str,
    wall_chord_m: float,
    core_size_m: float,
    growth_ratio: float,
    volume_strategy: dict[str, Any],
    runtime: dict[str, str],
    candidates: list[dict[str, Any]],
    master_error: str | None = None,
) -> dict[str, Any]:
    winner = select_candidate(candidates)
    selected = winner["candidate"] if winner else None
    return {
        "schema": SCREEN_SCHEMA,
        "status": "accepted_one_level_candidate" if selected else "rejected_no_candidate_advanced_to_three_core_sizes",
        "master": {
            "path": str(master),
            "declaredSurfaceSha256": expected_surface_sha256,
            "observed": None if master_error else msh2_surface_fingerprint(master),
            "validationError": master_error,
        },
        "runtime": runtime,
        "volumeStrategy": volume_strategy,
        "screen": {
            "coreSizeM": core_size_m,
            "growthRatio": growth_ratio,
            "wallChordM": wall_chord_m,
            "matrix": "layer_count in {2,4,8}; first_layer in {0.5,1,2} times wallChordM",
            "advanceRule": "Only an accepted zero-failure immutable-surface gate advances to the three core sizes; select by minimum maximum non-orthogonality, then skewness, then cell count.",
        },
        "candidates": candidates,
        "disposition": {
            "accepted": selected is not None,
            "selectedCandidate": selected,
            "threeCoreSizeRunsStarted": False,
            "solverSmokeStarted": False,
        },
    }


def _write_campaign_readme(root: Path, report: dict[str, Any]) -> None:
    disposition = report["disposition"]
    status = "accepted one-level candidate" if disposition["accepted"] else "rejected; no candidate advanced"
    master = report["master"]
    screen = report["screen"]
    root.joinpath("README.md").write_text(
        "# Layered immutable-surface quality screen v4\n\n"
        f"Status: **{status}**.\n\n"
        "This is a one-core-size quality screen only. It retains a frozen v2 master, "
        "per-candidate builder/configuration/Gmsh/OpenFOAM evidence, and the fail-closed gate report. "
        "It does not start the three-core-size suite, CFD smoke, GCI, or timing work.\n\n"
        f"Declared master hash: `{master['declaredSurfaceSha256']}`.\n\n"
        f"Screen core size: `{screen['coreSizeM']}` m; wall chord: `{screen['wallChordM']}` m; "
        f"growth ratio: `{screen['growthRatio']}`.\n\n"
        f"Volume strategy: `{report['volumeStrategy']['id']}` `{report['volumeStrategy']['version']}`; "
        f"internal interface chord schedule: `{report['volumeStrategy']['interfaceChordSchedule']}`; "
        f"transition thickness: `{report['volumeStrategy']['transitionThicknessM']}` m.\n\n"
        "Advance only if `artifacts/screen-report.json` records `disposition.accepted: true`; "
        "then use its selected candidate for the separately scheduled three-core-size suite.\n",
        encoding="utf-8",
    )


def run_screen(
    *,
    root: Path,
    master: Path,
    expected_surface_sha256: str,
    wall_chord_m: float,
    core_size_m: float,
    growth_ratio: float,
    interface_chord_schedule: Sequence[tuple[float, int]],
    transition_thickness_m: float,
    runtime: dict[str, str],
) -> dict[str, Any]:
    """Execute the one-level screen and retain a final fail-closed report."""

    root.mkdir(parents=True, exist_ok=True)
    if transition_thickness_m <= 0:
        raise ValueError("transition_thickness_m must be positive")
    normalized_schedule = normalize_interface_chord_schedule(interface_chord_schedule)
    volume_strategy = {
        **VOLUME_STRATEGY,
        "interfaceChordSchedule": [
            {"coreSizeM": core_size_m, "interfaceChords": chords}
            for core_size_m, chords in normalized_schedule
        ],
        "transitionThicknessM": transition_thickness_m,
    }
    _write_json(
        root / "artifacts" / "master-provenance.json",
        {
            "schema": "flowlab.layered-immutable-screen-provenance.v1",
            "master": str(master),
            "declaredSurfaceSha256": expected_surface_sha256,
            "runtime": runtime,
            "screen": {
                "wallChordM": wall_chord_m,
                "coreSizeM": core_size_m,
                "growthRatio": growth_ratio,
                "volumeStrategy": volume_strategy,
                "matrix": "layer_count in {2,4,8}; first_layer in {0.5,1,2} times wallChordM",
            },
            "intent": "one-level quality screen; do not start the three-core-size suite without an accepted screen report",
        },
    )
    try:
        observed = msh2_surface_fingerprint(master)
        master_error = None if observed["surfaceSha256"] == expected_surface_sha256 else (
            "declared frozen-surface SHA-256 does not match master "
            f"(expected {expected_surface_sha256}, got {observed['surfaceSha256']})"
        )
    except (OSError, ValueError) as error:
        master_error = f"cannot read frozen master: {error}"
    if master_error:
        report = _screen_report(
            root=root,
            master=master,
            expected_surface_sha256=expected_surface_sha256,
            wall_chord_m=wall_chord_m,
            core_size_m=core_size_m,
            growth_ratio=growth_ratio,
            volume_strategy=volume_strategy,
            runtime=runtime,
            candidates=[],
            master_error=master_error,
        )
        _write_json(root / "artifacts" / "screen-report.json", report)
        _write_campaign_readme(root, report)
        return report
    reports = [
        _run_candidate(
            root=root,
            master=master,
            expected_surface_sha256=expected_surface_sha256,
            candidate=candidate,
            volume_strategy=volume_strategy,
        )
        for candidate in bounded_candidates(
            wall_chord_m=wall_chord_m,
            core_size_m=core_size_m,
            growth_ratio=growth_ratio,
        )
    ]
    report = _screen_report(
        root=root,
        master=master,
        expected_surface_sha256=expected_surface_sha256,
        wall_chord_m=wall_chord_m,
        core_size_m=core_size_m,
        growth_ratio=growth_ratio,
        volume_strategy=volume_strategy,
        runtime=runtime,
        candidates=reports,
    )
    _write_json(root / "artifacts" / "screen-report.json", report)
    _write_campaign_readme(root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--expected-surface-sha256", required=True)
    parser.add_argument("--wall-chord-m", type=float, required=True)
    parser.add_argument("--screen-core-size-m", type=float, default=DEFAULT_CORE_SIZES_M[0])
    parser.add_argument("--growth-ratio", type=float, default=1.2)
    parser.add_argument(
        "--interface-chord-schedule",
        required=True,
        help="Required v4 schedule, e.g. 0.003:64,0.002:96,0.0015:128.",
    )
    parser.add_argument("--transition-thickness-m", type=float, required=True)
    parser.add_argument("--runtime-image", default="unknown")
    parser.add_argument("--runtime-image-id", default="unknown")
    args = parser.parse_args()
    report = run_screen(
        root=args.run_root,
        master=args.master,
        expected_surface_sha256=args.expected_surface_sha256,
        wall_chord_m=args.wall_chord_m,
        core_size_m=args.screen_core_size_m,
        growth_ratio=args.growth_ratio,
        interface_chord_schedule=parse_interface_chord_schedule(args.interface_chord_schedule),
        transition_thickness_m=args.transition_thickness_m,
        runtime={"image": args.runtime_image, "imageId": args.runtime_image_id},
    )
    return 0 if report["disposition"]["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
