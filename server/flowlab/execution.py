from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import struct
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import Any, Callable, Iterable

from . import adapters
from .result_identity import (
    FULL_OGRID_SOURCE_IDENTITY_ALGORITHMS,
    SOURCE_CELL_ID_FIELD,
    SOURCE_IDENTITY_ALGORITHM_FULL_OGRID_PATH,
    ResultIdentityError,
    reorder_solver_values_to_source,
    resolve_openfoam_source_cell_identity,
)
from .results import parse_vtk_result, preview_vtk_result_text, summarize_vtk_result_text
from .schemas import JobRecord, SolverCase, SolverRuntimeStatus
from .validated_preset import (
    OPEN_BOUNDARY_BENCHMARK_ID,
    is_validated_open_boundary_case,
    read_validated_open_boundary_result,
    validate_validated_open_boundary_case,
    validated_result_error,
)

CASE_MANIFEST_PATH = "flowlab_case_manifest.json"
TERMINAL_STATUSES = {"complete", "failed", "blocked", "cancelled"}
EXECUTABLE_SOLVERS = {"openfoam", "su2", "code-saturne", "mujoco"}
REQUIRED_NATIVE_COMMANDS = {
    "openfoam": "foamRun",
    "su2": "SU2_CFD",
    "code-saturne": "code_saturne",
    "mujoco": "python3",
}
REQUIRED_PYTHON_MODULES = {
    "mujoco": "mujoco",
}
RESULT_EXTENSIONS = {".vtk", ".vtu"}
RESULT_COLLECTION_EXTENSIONS = {".pvd"}
OPENFOAM_NATIVE_RESULT_FIELDS = {
    "U",
    "p",
    "p_rgh",
    "T",
    "alpha.water",
    "alpha.air",
    "alpha.vapour",
    "rho",
    "k",
    "omega",
    "nut",
    "alphat",
}
DIAGNOSTIC_EXTENSIONS = {".dat", ".csv", ".txt", ".log", ".json"}
DIAGNOSTIC_SPECIAL_FILENAMES = {"error", "listing", "run_status.failed", "run_status.finished", "summary"}
DIAGNOSTIC_ROOTS = ("postProcessing", "RESU", "outputs")
DIAGNOSTIC_TOP_LEVEL_FILENAMES = {"history.csv", "history.dat", "flow.csv", "surface_flow.csv"}
PATCH_METRIC_PATH_HINTS = ("patchflowrate", "patchaverage", "wallshearstress", "forces", "wallforces", "probes")
OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH = "postProcessing/flowlab_diagnostics_acceptance.json"
MESHIO_ROUNDTRIP_VALIDATION_PATH = "mesh/meshio_roundtrip_validation.json"
RESULT_COLLECTION_PATH = "postProcessing/flowlab_results.pvd"
RUN_ARTIFACT_MANIFEST_PATH = "postProcessing/flowlab_run_artifacts.json"
VISUAL_POSTPROCESSING_PATH = "postProcessing/flowlab_visual_postprocessing.json"
JOB_RECORD_FILENAME = "flowlab_job_record.json"
CASE_RECORD_FILENAME = "flowlab_case_record.json"
MAX_RESULT_FILES = 8
MAX_RESULT_FILE_BYTES = 2_000_000
MAX_ARTIFACT_CHUNK_BYTES = 262_144
MAX_RESULT_PREVIEW_FILE_BYTES = 8_000_000
# Upper bound for reading an oversized result off disk purely to confirm it
# contains fields and no NaN. Nothing this large is ever embedded in a job
# response; it is read once, summarized, and released.
MAX_RESULT_VERIFICATION_FILE_BYTES = 64_000_000
MAX_RESULT_COLLECTION_FILE_BYTES = 1_000_000
MAX_RESULT_COLLECTION_DATASETS = 500
MAX_DIAGNOSTIC_FILES = 12
MAX_DIAGNOSTIC_FILE_BYTES = 500_000
MAX_MESH_QUALITY_ARTIFACT_BYTES = 120_000
MESH_QUALITY_ARTIFACT_PATHS = (
    "mesh/production_mesh_acceptance.json",
    "mesh/meshio_roundtrip_validation.json",
    "log.surfaceFeatureExtract",
    "log.blockMesh",
    "log.snappyHexMesh",
    "log.checkMesh",
    "log.yPlus",
)
DOCKER_IMAGES = {
    "openfoam": adapters.DEFAULT_OPENFOAM_IMAGE,
}
SU2_DOCKER_IMAGE = "ubuntu:22.04"
DOCKER_ENTRYPOINTS = {
    # The official OpenFOAM Foundation image starts an interactive shell through
    # /entry.sh and ignores the command argv unless the entrypoint is replaced.
    "openfoam": "/bin/bash",
}
DOCKER_PLATFORMS = {
    # The official OpenFOAM Foundation v11 image currently publishes linux/amd64.
    "openfoam": "linux/amd64",
}
DOCKER_ENV_SETUP = {
    "openfoam": "source /opt/openfoam11/etc/bashrc",
}
CODE_SATURNE_DOCKER_PLATFORM_ENV = "FLOWLAB_CODE_SATURNE_PLATFORM"
RUNTIME_SOLVER_ORDER: tuple[str, ...] = ("instant-1d", "openfoam", "su2", "code-saturne", "mujoco")
REQUIRED_CASE_FILES: dict[str, tuple[str, ...]] = {
    "openfoam": (
        "Allrun",
        "system/blockMeshDict",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
        "0/U",
        "0/p",
        "0/T",
        "constant/transportProperties",
        "constant/turbulenceProperties",
        "constant/flowlab.json",
        "mesh/flowlab_mesh.json",
        "mesh/flowlab_mesh.vtk",
        "mesh/flowlab_mesh.vtu",
        "mesh/quality.json",
        "mesh/boundary_layer_plan.json",
        "mesh/adaptation_plan.json",
        "mesh/production_mesh_plan.json",
        "mesh/production_mesh_acceptance.json",
        "mesh/openfoam_review.json",
    ),
    "su2": (
        "case.cfg",
        "flowlab_su2_mode_preset.json",
        "flowlab_su2_native_setup_checklist.json",
        "flowlab_su2_capability_matrix.json",
        "mesh/flowlab_mesh.su2",
        "mesh/flowlab_mesh.json",
        "mesh/flowlab_mesh.vtk",
        "mesh/flowlab_mesh.vtu",
        "mesh/quality.json",
    ),
    "code-saturne": (
        "DATA/setup.xml",
        "DATA/flowlab_physics_preset.json",
        "DATA/flowlab_native_setup_checklist.json",
        "DATA/flowlab_code_saturne_capability_matrix.json",
        "DATA/run.cfg",
        "DATA/cs_user_scripts.py",
        "DATA/cs_user_physics.py",
        "SRC/cs_user_boundary_conditions.f90",
        "MESH/flowlab_mesh.msh",
        "mesh/flowlab_mesh.json",
        "mesh/quality.json",
    ),
    "mujoco": (
        "model.xml",
        "run_mujoco.py",
        "mesh/flowlab_mesh.json",
    ),
}


def _docker_image(solver: str) -> str | None:
    if solver == "openfoam":
        return adapters._openfoam_image()
    return DOCKER_IMAGES.get(solver)

OPENFOAM_MODE_FILES: dict[str, tuple[str, ...]] = {
    "compressible-flow": ("0/rho", "constant/thermophysicalProperties"),
    "heat-transfer": ("0/rho", "constant/thermophysicalProperties"),
    "conjugate-heat-transfer": (
        "0/rho",
        "0/k",
        "0/omega",
        "0/nut",
        "0/alphat",
        "constant/thermophysicalProperties",
        "0/fluid/U",
        "0/fluid/p",
        "0/fluid/p_rgh",
        "0/fluid/T",
        "0/fluid/rho",
        "0/fluid/k",
        "0/fluid/omega",
        "0/fluid/nut",
        "0/fluid/alphat",
        "0/solid/T",
    ),
    "multiphase-vof": (
        "0/alpha.water",
        "0/alpha.air",
        "0/rho",
        "constant/phaseProperties",
        "constant/physicalProperties.water",
        "constant/physicalProperties.air",
    ),
    "cavitation": (
        "0/alpha.vapour",
        "0/rho",
        "0/k",
        "0/omega",
        "0/nut",
        "constant/phaseProperties",
        "constant/physicalProperties.water",
        "constant/physicalProperties.vapour",
        "constant/thermodynamicProperties",
        "constant/fvModels",
        "constant/cavitationProperties",
    ),
    "water-hammer": ("constant/waterHammerPreview.json", "constant/waterHammerWaveform.csv"),
}

OPENFOAM_CHT_REGION_FILES: tuple[str, ...] = (
    "constant/fluid/physicalProperties",
    "constant/fluid/momentumTransport",
    "constant/fluid/g",
    "constant/solid/physicalProperties",
    "system/fluid/fvSchemes",
    "system/fluid/fvSolution",
    "system/solid/fvSchemes",
    "system/solid/fvSolution",
)

OPENFOAM_CHT_REGION_MESH_FILES: tuple[str, ...] = (
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
)

PopenFactory = Callable[..., subprocess.Popen]
OPENFOAM_TIME_RE = re.compile(r"^Time\s*=\s*([0-9.eE+-]+)")
OPENFOAM_RESIDUAL_RE = re.compile(
    r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+-]+),\s+Final residual\s*=\s*([0-9.eE+-]+),\s+No Iterations\s+(\d+)"
)
OPENFOAM_CHECKMESH_COUNT_RE = re.compile(r"^\s*(points|faces|internal faces|cells)\s*:\s*(\d+)\s*$", re.IGNORECASE)
OPENFOAM_CHECKMESH_ASPECT_RE = re.compile(r"\bMax aspect ratio\s*=\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
OPENFOAM_CHECKMESH_NONORTH_RE = re.compile(
    r"\bMesh non-orthogonality Max:\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s+average:\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)
OPENFOAM_CHECKMESH_SKEW_RE = re.compile(r"\bMax skewness\s*=\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
OPENFOAM_CHECKMESH_FAILED_RE = re.compile(r"\bFailed\s+(\d+)\s+mesh checks?", re.IGNORECASE)
OPENFOAM_CHECKMESH_MIN_VOLUME_RE = re.compile(r"\bMin volume\s*=\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
OPENFOAM_CHECKMESH_REGION_RE = re.compile(
    r"(?:checkMesh\s+-region|region mesh check:)\s+([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
SU2_ITER_RE = re.compile(r"^\s*\|?\s*(\d+)\s*\|")
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
CODE_SATURNE_ITER_RE = re.compile(r"\b(?:iteration|time step)\s*[:=]?\s*(\d+)", re.IGNORECASE)
MUJOCO_STEP_RE = re.compile(r"\b(?:(?:step|steps)\s*[:=]?\s*(\d+)|completed\s+(\d+)\s+steps)\b", re.IGNORECASE)
NAN_TOKEN_RE = re.compile(r"(^|[^A-Za-z])[-+]?nan([^A-Za-z]|$)", re.IGNORECASE)


def _shell_direct_command_line_index(
    script: str,
    *,
    command: str,
    required_tokens: Iterable[str] = (),
) -> int | None:
    """Return a direct, uncommented command line index in a generated shell script.

    Generated FlowLab scripts use one executable command per line. Limiting the
    validator to that subset is deliberate: it makes commented examples, echo
    text, or opaque shell expressions unable to satisfy an execution gate.
    """

    required = set(required_tokens)
    for line_index, raw_line in enumerate(script.splitlines()):
        try:
            tokens = shlex.split(raw_line, comments=True, posix=True)
        except ValueError:
            continue
        if not tokens or tokens[0] != command:
            continue
        if required.issubset(tokens[1:]):
            return line_index
    return None


def _shell_has_uncommented_command(script: str, commands: Iterable[str]) -> bool:
    return any(
        _shell_direct_command_line_index(script, command=command) is not None
        for command in commands
    )


def _shell_openfoam_parallel_command_line_index(
    script: str,
    *,
    ranks: int,
    command: str,
) -> int | None:
    """Return an actual direct MPI OpenFOAM command line with `-parallel`."""

    expected_prefix = ("mpirun", "-np", str(ranks), command)
    for line_index, raw_line in enumerate(script.splitlines()):
        try:
            tokens = shlex.split(raw_line, comments=True, posix=True)
        except ValueError:
            continue
        if tuple(tokens[:4]) == expected_prefix and "-parallel" in tokens[4:]:
            return line_index
    return None


def default_runtime_root() -> Path:
    return Path(os.environ.get("FLOWLAB_RUNTIME_DIR", "runtime/flowlab")).resolve()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_case_path(case_dir: Path, relative_path: str) -> Path:
    destination = (case_dir / relative_path).resolve()
    if not destination.is_relative_to(case_dir.resolve()):
        raise ValueError(f"Unsafe generated file path: {relative_path}")
    return destination


def materialize_case_files(case: SolverCase, case_dir: Path) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, content in case.files.items():
        destination = _safe_case_path(case_dir, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    write_meshio_roundtrip_validation(case_dir)


def write_meshio_roundtrip_validation(case_dir: Path) -> dict[str, Any]:
    mesh_paths = [
        "mesh/flowlab_mesh.vtk",
        "mesh/flowlab_mesh.vtu",
        "mesh/flowlab_mesh.su2",
        "mesh/flowlab_mesh.msh",
    ]
    existing = [relative for relative in mesh_paths if (case_dir / relative).is_file()]
    report: dict[str, Any] = {
        "schema": "flowlab.meshio_roundtrip_validation.v1",
        "generatedAt": _utc_now(),
        "status": "skipped",
        "blocking": False,
        "dependency": {"name": "meshio", "available": False},
        "artifacts": [],
    }
    if not existing:
        report["reason"] = "No supported mesh artifacts were present."
        _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).parent.mkdir(parents=True, exist_ok=True)
        _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
    if importlib.util.find_spec("meshio") is None:
        report["reason"] = "Optional Python package `meshio` is not installed."
        report["artifacts"] = [{"path": relative, "status": "not-run"} for relative in existing]
        _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).parent.mkdir(parents=True, exist_ok=True)
        _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    import meshio  # type: ignore[import-not-found]

    output_dir = case_dir / "mesh" / "meshio_roundtrip"
    output_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    for relative in existing:
        source = case_dir / relative
        roundtrip_relative = f"mesh/meshio_roundtrip/{source.stem}.roundtrip.vtu"
        roundtrip_path = case_dir / roundtrip_relative
        check: dict[str, Any] = {"path": relative, "roundtripPath": roundtrip_relative, "status": "failed"}
        try:
            mesh = meshio.read(str(source))
            meshio.write(str(roundtrip_path), mesh)
            reread = meshio.read(str(roundtrip_path))
            check.update(
                {
                    "status": "passed",
                    "pointCount": int(len(mesh.points)),
                    "cellCount": int(sum(len(block.data) for block in mesh.cells)),
                    "roundtripPointCount": int(len(reread.points)),
                    "roundtripCellCount": int(sum(len(block.data) for block in reread.cells)),
                }
            )
        except Exception as exc:  # pragma: no cover - exercised only when optional meshio is installed
            check["error"] = str(exc)
        checks.append(check)
    failed = [check for check in checks if check.get("status") != "passed"]
    report.update(
        {
            "status": "failed" if failed else "passed",
            "dependency": {"name": "meshio", "available": True},
            "artifacts": checks,
        }
    )
    _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).parent.mkdir(parents=True, exist_ok=True)
    _safe_case_path(case_dir, MESHIO_ROUNDTRIP_VALIDATION_PATH).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _resolve_case_artifact_path(case_dir: Path, relative_path: str) -> tuple[Path, str, bool]:
    path_text = str(relative_path or "").strip()
    if not path_text or path_text.startswith("/") or "\x00" in path_text:
        raise ValueError("Artifact path must be a relative case path.")
    root = case_dir.resolve()
    path = (root / path_text).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Artifact path escapes the job case directory.")
    if not path.is_file():
        raise FileNotFoundError("Artifact not found.")

    relative = path.relative_to(root)
    relative_parts = relative.parts
    relative_name = str(relative)
    if relative_parts and relative_parts[0].lower() == "mesh":
        raise ValueError("Mesh inspection exports are not solver result artifacts.")

    is_result = path.suffix.lower() in RESULT_EXTENSIONS or path.suffix.lower() in RESULT_COLLECTION_EXTENSIONS
    is_diagnostic = (
        relative_name in DIAGNOSTIC_TOP_LEVEL_FILENAMES
        or (bool(relative_parts) and relative_parts[0] in DIAGNOSTIC_ROOTS and _is_diagnostic_file(path))
    )
    if not is_result and not is_diagnostic:
        raise ValueError("Artifact path is not a supported result or diagnostic file.")
    return path, relative_name, is_result


def _result_file_payload(relative_path: str, size: int, text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": relative_path, "size": size, "text": text}
    try:
        payload["fieldSummary"] = summarize_vtk_result_text(text)
    except Exception as exc:
        payload["fieldSummaryError"] = str(exc)
    return payload


def _relative_artifact_path(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _safe_collection_dataset_path(root: Path, collection_path: Path, file_name: str) -> tuple[str | None, str | None]:
    file_text = str(file_name or "").strip()
    if not file_text:
        return None, "missing file"
    if file_text.startswith("/") or "\x00" in file_text:
        return None, "unsafe file path"
    resolved = (collection_path.parent / file_text).resolve()
    if not resolved.is_relative_to(root.resolve()):
        return None, "file path escapes case directory"
    if resolved.suffix.lower() not in RESULT_EXTENSIONS:
        return None, "not a VTK/VTU result file"
    return _relative_artifact_path(root, resolved), None


def _parse_pvd_collection(path: Path, root: Path) -> dict[str, Any]:
    size = path.stat().st_size
    relative_name = _relative_artifact_path(root, path)
    if size > MAX_RESULT_COLLECTION_FILE_BYTES:
        return {
            "schema": "flowlab.pvd_collection.v1",
            "format": "pvd-ascii-v1",
            "path": relative_name,
            "datasetCount": 0,
            "referencedResultCount": 0,
            "missingResultCount": 0,
            "unsafeReferenceCount": 0,
            "truncated": False,
            "skipped": f"file exceeds collection metadata limit {MAX_RESULT_COLLECTION_FILE_BYTES}",
            "datasets": [],
        }
    try:
        root_node = ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError) as exc:
        return {
            "schema": "flowlab.pvd_collection.v1",
            "format": "pvd-ascii-v1",
            "path": relative_name,
            "datasetCount": 0,
            "referencedResultCount": 0,
            "missingResultCount": 0,
            "unsafeReferenceCount": 0,
            "truncated": False,
            "error": str(exc),
            "datasets": [],
        }
    if root_node.tag != "VTKFile" or root_node.attrib.get("type") != "Collection":
        return {
            "schema": "flowlab.pvd_collection.v1",
            "format": "pvd-ascii-v1",
            "path": relative_name,
            "datasetCount": 0,
            "referencedResultCount": 0,
            "missingResultCount": 0,
            "unsafeReferenceCount": 0,
            "truncated": False,
            "error": "PVD root must be VTKFile type=Collection.",
            "datasets": [],
        }
    raw_datasets = root_node.findall("./Collection/DataSet")
    datasets: list[dict[str, Any]] = []
    missing_count = 0
    unsafe_count = 0
    for index, node in enumerate(raw_datasets):
        if len(datasets) >= MAX_RESULT_COLLECTION_DATASETS:
            break
        relative_result, path_error = _safe_collection_dataset_path(root, path, node.attrib.get("file", ""))
        exists = False
        if relative_result is not None:
            exists = (root / relative_result).is_file()
            if not exists:
                missing_count += 1
        else:
            unsafe_count += 1
        timestep_text = node.attrib.get("timestep", "")
        try:
            timestep = float(timestep_text)
        except ValueError:
            timestep = None
        datasets.append(
            {
                "index": index,
                "time": timestep,
                "timeText": timestep_text,
                "file": relative_result,
                "exists": exists,
                **({"pathError": path_error} if path_error else {}),
                **({"part": node.attrib.get("part")} if node.attrib.get("part") else {}),
                **({"group": node.attrib.get("group")} if node.attrib.get("group") else {}),
                **({"name": node.attrib.get("name")} if node.attrib.get("name") else {}),
            }
        )
    return {
        "schema": "flowlab.pvd_collection.v1",
        "format": "pvd-ascii-v1",
        "path": relative_name,
        "datasetCount": len(raw_datasets),
        "referencedResultCount": sum(1 for dataset in datasets if dataset.get("exists") is True),
        "missingResultCount": missing_count,
        "unsafeReferenceCount": unsafe_count,
        "truncated": len(raw_datasets) > len(datasets),
        "datasets": datasets,
    }


def _pvd_result_time_index(root: Path) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return hints
    for path in sorted(root.rglob("*.pvd")):
        relative_parts = path.relative_to(root).parts
        if relative_parts and relative_parts[0].lower() == "mesh":
            continue
        summary = _parse_pvd_collection(path, root)
        for dataset in summary.get("datasets", []):
            if not isinstance(dataset, dict) or not dataset.get("exists") or not dataset.get("file"):
                continue
            file_path = str(dataset["file"])
            if file_path in hints:
                continue
            hints[file_path] = {
                "time": dataset.get("time"),
                "timeText": dataset.get("timeText"),
                "timeSource": "pvd",
                "collectionPath": summary.get("path"),
                "collectionIndex": dataset.get("index"),
            }
    return hints


def _result_artifact_index_entry(
    path: Path,
    relative_name: str,
    size: int,
    artifact_kind: str,
    *,
    case_root: Path | None = None,
    pvd_time_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": relative_name, "size": size, "kind": artifact_kind}
    if pvd_time_hint:
        entry.update({key: value for key, value in pvd_time_hint.items() if value is not None})
    if artifact_kind == "result" and path.suffix.lower() in RESULT_COLLECTION_EXTENSIONS and case_root is not None:
        entry["collectionSummary"] = _parse_pvd_collection(path, case_root)
        return entry
    if artifact_kind != "result" or path.suffix.lower() not in RESULT_EXTENSIONS or size > MAX_RESULT_FILE_BYTES:
        return entry
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        entry["fieldSummaryError"] = "not UTF-8 text"
        return entry
    try:
        entry["fieldSummary"] = summarize_vtk_result_text(text)
    except Exception as exc:
        entry["fieldSummaryError"] = str(exc)
    return entry


def read_case_artifact(case_dir: Path, relative_path: str) -> dict[str, Any]:
    path, relative_name, is_result = _resolve_case_artifact_path(case_dir, relative_path)
    size = path.stat().st_size
    if is_result and path.suffix.lower() in RESULT_COLLECTION_EXTENSIONS:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"path": relative_name, "size": size, "skipped": "not UTF-8 text"}
        return {"path": relative_name, "size": size, "text": text, "collectionSummary": _parse_pvd_collection(path, case_dir)}
    limit = MAX_RESULT_FILE_BYTES if is_result else MAX_DIAGNOSTIC_FILE_BYTES
    if size > limit:
        return {"path": relative_name, "size": size, "skipped": "file too large"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": relative_name, "size": size, "skipped": "not UTF-8 text"}
    if is_result:
        return _result_file_payload(relative_name, size, text)
    return {"path": relative_name, "size": size, "text": text}


def read_case_artifact_chunk(case_dir: Path, relative_path: str, offset: int = 0, limit: int = MAX_ARTIFACT_CHUNK_BYTES) -> dict[str, str | int | bool]:
    path, relative_name, _is_result = _resolve_case_artifact_path(case_dir, relative_path)
    if offset < 0:
        raise ValueError("Artifact chunk offset must be non-negative.")
    if limit <= 0:
        raise ValueError("Artifact chunk limit must be positive.")
    chunk_limit = min(limit, MAX_ARTIFACT_CHUNK_BYTES)
    size = path.stat().st_size
    if offset > size:
        raise ValueError("Artifact chunk offset is beyond the end of the file.")
    with path.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read(chunk_limit)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Artifact chunk is not UTF-8 text.") from exc
    next_offset = offset + len(payload)
    return {
        "path": relative_name,
        "size": size,
        "offset": offset,
        "limit": chunk_limit,
        "text": text,
        "nextOffset": next_offset,
        "complete": next_offset >= size,
    }


def read_case_artifact_preview(case_dir: Path, relative_path: str, point_limit: int = 500, cell_limit: int = 500) -> dict[str, Any]:
    path, relative_name, is_result = _resolve_case_artifact_path(case_dir, relative_path)
    if not is_result or path.suffix.lower() not in RESULT_EXTENSIONS:
        raise ValueError("Only VTK/VTU result artifacts can be previewed.")
    size = path.stat().st_size
    if size > MAX_RESULT_PREVIEW_FILE_BYTES:
        return {
            "path": relative_name,
            "size": size,
            "skipped": f"file exceeds preview limit {MAX_RESULT_PREVIEW_FILE_BYTES}",
        }
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": relative_name, "size": size, "skipped": "not UTF-8 text"}
    preview = preview_vtk_result_text(text, point_limit=point_limit, cell_limit=cell_limit)
    return {"path": relative_name, "size": size, **preview}


def list_case_artifacts(case_dir: Path, kind: str = "result", limit: int = 200) -> dict[str, object]:
    if kind not in {"result", "diagnostic", "all"}:
        raise ValueError("Artifact kind must be `result`, `diagnostic`, or `all`.")
    if limit <= 0:
        raise ValueError("Artifact index limit must be positive.")
    max_items = min(limit, 500)
    root = case_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError("Job case directory is unavailable.")
    artifacts: list[dict[str, Any]] = []
    total = 0
    pvd_time_hints = _pvd_result_time_index(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        relative_parts = relative.parts
        if relative_parts and relative_parts[0].lower() == "mesh":
            continue
        relative_name = str(relative)
        artifact_kind: str | None = None
        if path.suffix.lower() in RESULT_EXTENSIONS or path.suffix.lower() in RESULT_COLLECTION_EXTENSIONS:
            artifact_kind = "result"
        elif (
            relative_name in DIAGNOSTIC_TOP_LEVEL_FILENAMES
            or (bool(relative_parts) and relative_parts[0] in DIAGNOSTIC_ROOTS and _is_diagnostic_file(path))
        ):
            artifact_kind = "diagnostic"
        if artifact_kind is None or (kind != "all" and artifact_kind != kind):
            continue
        total += 1
        if len(artifacts) >= max_items:
            continue
        size = path.stat().st_size
        artifacts.append(
            _result_artifact_index_entry(
                path,
                relative_name,
                size,
                artifact_kind,
                case_root=root,
                pvd_time_hint=pvd_time_hints.get(relative_name),
            )
        )
    return {"artifacts": artifacts, "count": total, "truncated": total > len(artifacts)}


def _read_bounded_mesh_quality_artifact(case_dir: Path, relative_path: str) -> dict[str, Any]:
    path = _safe_case_path(case_dir, relative_path)
    if not path.is_file():
        return {"path": relative_path, "exists": False, "size": 0}
    size = path.stat().st_size
    artifact: dict[str, Any] = {"path": relative_path, "exists": True, "size": size}
    if size > MAX_MESH_QUALITY_ARTIFACT_BYTES:
        artifact["skipped"] = f"file exceeds mesh-quality artifact limit {MAX_MESH_QUALITY_ARTIFACT_BYTES}"
        return artifact
    try:
        artifact["text"] = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        artifact["skipped"] = "not UTF-8 text"
    return artifact


def read_case_mesh_quality(case_dir: Path) -> dict[str, Any]:
    root = case_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError("Job case directory is unavailable.")
    acceptance: dict[str, Any] | None = None
    acceptance_error: str | None = None
    acceptance_path = root / "mesh" / "production_mesh_acceptance.json"
    if acceptance_path.is_file():
        try:
            acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            acceptance_error = str(exc)
    else:
        acceptance_error = "mesh/production_mesh_acceptance.json is missing."

    native_quality = acceptance.get("nativeQualityEvidence") if isinstance(acceptance, dict) else {}
    if not isinstance(native_quality, dict):
        native_quality = {}
    solver_reports = native_quality.get("solverReports") if isinstance(native_quality.get("solverReports"), dict) else {}
    openfoam_report = solver_reports.get("openfoam") if isinstance(solver_reports.get("openfoam"), dict) else {}
    solver_acceptance = acceptance.get("solverAcceptance") if isinstance(acceptance, dict) else {}
    openfoam_acceptance = solver_acceptance.get("openfoam") if isinstance(solver_acceptance.get("openfoam"), dict) else {}
    artifacts = [_read_bounded_mesh_quality_artifact(root, path) for path in MESH_QUALITY_ARTIFACT_PATHS]
    command_runs = openfoam_report.get("commandRuns") if isinstance(openfoam_report.get("commandRuns"), list) else []
    quality_metrics = openfoam_report.get("qualityMetrics") if isinstance(openfoam_report.get("qualityMetrics"), dict) else {}
    yplus_evidence = openfoam_report.get("yPlusEvidence") if isinstance(openfoam_report.get("yPlusEvidence"), dict) else {}
    layer_summary = openfoam_report.get("layerSummary") if isinstance(openfoam_report.get("layerSummary"), dict) else {}
    blocking_reasons = []
    for source in (
        openfoam_report.get("blockingReasons"),
        openfoam_acceptance.get("blockingReasons"),
        acceptance.get("blockingReasons") if isinstance(acceptance, dict) else None,
    ):
        if isinstance(source, list):
            for reason in source:
                if isinstance(reason, str) and reason not in blocking_reasons:
                    blocking_reasons.append(reason)
    return {
        "schema": "flowlab.mesh_quality_summary.v1",
        "status": openfoam_report.get("status") or native_quality.get("status") or ("unavailable" if acceptance_error else "unknown"),
        "productionReady": acceptance.get("productionReady") if isinstance(acceptance, dict) else False,
        "approvalStatus": acceptance.get("approvalStatus") if isinstance(acceptance, dict) else "unavailable",
        "nativeQualityStatus": native_quality.get("status"),
        "solverAcceptanceStatus": openfoam_acceptance.get("status"),
        "acceptanceError": acceptance_error,
        "openfoam": {
            "status": openfoam_report.get("status") or "unavailable",
            "commandRuns": command_runs[:12],
            "qualityMetrics": quality_metrics,
            "yPlusEvidence": yplus_evidence,
            "layerSummary": {
                **layer_summary,
                "excerpts": layer_summary.get("excerpts", [])[:12] if isinstance(layer_summary.get("excerpts"), list) else [],
            },
            "blockingReasons": blocking_reasons[:12],
        },
        "artifacts": artifacts,
        "artifactLimitBytes": MAX_MESH_QUALITY_ARTIFACT_BYTES,
    }


def collect_result_files(case_dir: Path) -> list[dict[str, Any]]:
    identity_contract_required = (
        case_dir / "constant" / "flowlab_result_identity_contract.json"
    ).is_file()
    if identity_contract_required:
        return _collect_openfoam_native_time_results(case_dir, MAX_RESULT_FILES)

    results: list[dict[str, Any]] = []
    overflow_count = 0
    overflow_bytes = 0
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in RESULT_EXTENSIONS:
            continue
        relative_parts = path.relative_to(case_dir).parts
        if relative_parts and relative_parts[0].lower() == "mesh":
            continue
        relative_path = str(path.relative_to(case_dir))
        size = path.stat().st_size
        if len(results) >= MAX_RESULT_FILES:
            overflow_count += 1
            overflow_bytes += size
            continue
        if size > MAX_RESULT_FILE_BYTES:
            entry: dict[str, Any] = {
                "path": relative_path,
                "size": size,
                "skipped": "file too large",
            }
            # An oversized result is still real evidence. The size cap exists to
            # keep the job response small enough to embed, not to judge whether
            # the solve produced fields, so verify the file from disk without
            # embedding its text. Without this, an exit-zero solve whose only
            # result exceeds the cap is reported as failed - which is what a
            # 19,968-cell level does, since its single result is 2.8 MB against
            # a 1 MB cap.
            if size <= MAX_RESULT_VERIFICATION_FILE_BYTES:
                try:
                    oversized_text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    oversized_text = None
                if oversized_text is not None:
                    summary = summarize_vtk_result_text(oversized_text)
                    if isinstance(summary, dict) and summary.get("fields"):
                        entry["fieldSummary"] = summary
                        entry["verifiedOnDisk"] = True
                    if NAN_TOKEN_RE.search(oversized_text):
                        entry["nanDetected"] = True
            results.append(entry)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            results.append({"path": relative_path, "size": size, "skipped": "not UTF-8 text"})
            continue
        results.append(_result_file_payload(relative_path, size, text))
    if not results:
        results.extend(_collect_openfoam_native_time_results(case_dir, MAX_RESULT_FILES))
    if len(results) < MAX_RESULT_FILES:
        results.extend(_collect_code_saturne_ensight_results(case_dir, MAX_RESULT_FILES - len(results)))
    if overflow_count:
        results.append(
            {
                "path": "<additional-result-files>",
                "size": overflow_bytes,
                "skipped": f"{overflow_count} additional VTK/VTU result file(s) omitted after collection limit {MAX_RESULT_FILES}",
            }
        )
    return results


def _result_collection_time(result: dict[str, Any], index: int) -> tuple[float, str]:
    for key in ("time", "timeText"):
        value = result.get(key)
        if isinstance(value, int | float) and math.isfinite(float(value)):
            time_value = float(value)
            return time_value, f"{time_value:g}"
        if isinstance(value, str):
            try:
                time_value = float(value)
            except ValueError:
                continue
            return time_value, value
    return float(index), str(index)


def write_result_collection_pvd(case_dir: Path, result_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    datasets: list[dict[str, Any]] = []
    for result in result_files:
        relative = result.get("path")
        if not isinstance(relative, str) or result.get("skipped"):
            continue
        if Path(relative).suffix.lower() not in RESULT_EXTENSIONS:
            continue
        path = _safe_case_path(case_dir, relative)
        if not path.is_file():
            continue
        time_value, time_text = _result_collection_time(result, len(datasets))
        datasets.append({"path": relative, "time": time_value, "timeText": time_text})
    if not datasets:
        return None

    collection_path = _safe_case_path(case_dir, RESULT_COLLECTION_PATH)
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        "  <Collection>",
    ]
    for dataset in datasets:
        dataset_file = os.path.relpath(case_dir / str(dataset["path"]), collection_path.parent)
        lines.append(
            f'    <DataSet timestep="{xml_escape(str(dataset["timeText"]))}" part="0" file="{xml_escape(dataset_file)}"/>'
        )
    lines.extend(["  </Collection>", "</VTKFile>", ""])
    collection_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "schema": "flowlab.result_collection_generation.v1",
        "path": RESULT_COLLECTION_PATH,
        "datasetCount": len(datasets),
        "datasets": datasets,
    }


def write_optional_visual_postprocessing(case_dir: Path, result_files: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "flowlab.visual_postprocessing.v1",
        "generatedAt": _utc_now(),
        "status": "skipped",
        "tools": {
            "pyvista": {"available": importlib.util.find_spec("pyvista") is not None},
            "paraview": {"pvpython": shutil.which("pvpython")},
        },
        "outputs": [],
        "blocking": False,
    }
    candidates = [
        str(result["path"])
        for result in result_files
        if isinstance(result.get("path"), str) and not result.get("skipped") and Path(str(result["path"])).suffix.lower() in RESULT_EXTENSIONS
    ]
    if not candidates:
        report["reason"] = "No parseable VTK/VTU result artifacts were available for thumbnail generation."
    elif not report["tools"]["pyvista"]["available"]:
        report["reason"] = "Optional Python package `pyvista` is not installed; ParaView `pvpython` detection is reported but not invoked automatically."
    else:
        try:
            import pyvista as pv  # type: ignore[import-not-found]

            output_dir = case_dir / "postProcessing" / "flowlabThumbnails"
            output_dir.mkdir(parents=True, exist_ok=True)
            for relative in candidates[:3]:
                source = case_dir / relative
                output_relative = f"postProcessing/flowlabThumbnails/{Path(relative).stem}.png"
                output_path = case_dir / output_relative
                mesh = pv.read(str(source))
                plotter = pv.Plotter(off_screen=True, window_size=(640, 420))
                plotter.add_mesh(mesh, show_edges=False)
                plotter.screenshot(str(output_path))
                plotter.close()
                report["outputs"].append({"source": relative, "path": output_relative, "kind": "thumbnail"})
            report["status"] = "passed" if report["outputs"] else "skipped"
        except Exception as exc:  # pragma: no cover - optional PyVista runtime only
            report["status"] = "failed"
            report["error"] = str(exc)
    output = _safe_case_path(case_dir, VISUAL_POSTPROCESSING_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def write_run_artifact_manifest(
    case_dir: Path,
    *,
    job: JobRecord,
    result_files: list[dict[str, Any]],
    diagnostic_files: list[dict[str, Any]],
    diagnostic_summary: list[dict[str, Any]],
    mesh_quality: dict[str, Any] | None = None,
    patch_metrics: dict[str, Any] | None = None,
    diagnostics_acceptance: dict[str, Any] | None = None,
    result_collection: dict[str, Any] | None = None,
    visual_postprocessing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema": "flowlab.run_artifact_manifest.v1",
        "generatedAt": _utc_now(),
        "caseDir": str(case_dir),
        "jobId": job.id,
        "caseId": job.caseId,
        "solver": job.solver,
        "status": job.status,
        "execution": job.execution,
        "exitCode": job.exitCode,
        "resultFiles": [
            {key: value for key, value in result.items() if key in {"path", "size", "skipped", "time", "timeText", "timeSource", "sourceFields"}}
            for result in result_files
        ],
        "diagnosticFiles": [{key: value for key, value in item.items() if key in {"path", "size", "skipped"}} for item in diagnostic_files],
        "diagnosticSummaryKinds": [item.get("kind") for item in diagnostic_summary if isinstance(item, dict)],
        "collections": [result_collection] if result_collection else [],
        "meshQuality": {
            "status": mesh_quality.get("status") if isinstance(mesh_quality, dict) else None,
            "productionReady": mesh_quality.get("productionReady") if isinstance(mesh_quality, dict) else False,
            "approvalStatus": mesh_quality.get("approvalStatus") if isinstance(mesh_quality, dict) else None,
        },
        "patchMetrics": {
            "status": patch_metrics.get("status") if isinstance(patch_metrics, dict) else None,
            "patchCount": len(patch_metrics.get("patches", {})) if isinstance(patch_metrics, dict) and isinstance(patch_metrics.get("patches"), dict) else 0,
        },
        "diagnosticsAcceptance": {
            "status": diagnostics_acceptance.get("status") if isinstance(diagnostics_acceptance, dict) else None,
            "completionGate": diagnostics_acceptance.get("completionGate") if isinstance(diagnostics_acceptance, dict) else None,
        },
        "optionalPostprocessing": {
            "meshio": MESHIO_ROUNDTRIP_VALIDATION_PATH if (case_dir / MESHIO_ROUNDTRIP_VALIDATION_PATH).is_file() else None,
            "visual": visual_postprocessing,
        },
        "warnings": [
            "Generated FlowLab starter meshes are not CAD-reviewed production meshes unless meshQuality.productionReady is true.",
            "Optional meshio/PyVista/ParaView evidence is advisory and non-blocking unless a future mode promotes it into a gate.",
        ],
    }
    output = _safe_case_path(case_dir, RUN_ARTIFACT_MANIFEST_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def finalize_run_artifacts(
    case_dir: Path,
    *,
    job: JobRecord,
    result_files: list[dict[str, Any]],
    diagnostic_files: list[dict[str, Any]],
    diagnostic_summary: list[dict[str, Any]],
    mesh_quality: dict[str, Any] | None = None,
    patch_metrics: dict[str, Any] | None = None,
    diagnostics_acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_collection = write_result_collection_pvd(case_dir, result_files)
    visual_postprocessing = write_optional_visual_postprocessing(case_dir, result_files)
    manifest = write_run_artifact_manifest(
        case_dir,
        job=job,
        result_files=result_files,
        diagnostic_files=diagnostic_files,
        diagnostic_summary=diagnostic_summary,
        mesh_quality=mesh_quality,
        patch_metrics=patch_metrics,
        diagnostics_acceptance=diagnostics_acceptance,
        result_collection=result_collection,
        visual_postprocessing=visual_postprocessing,
    )
    return {
        "resultCollection": result_collection,
        "visualPostprocessing": visual_postprocessing,
        "artifactManifest": {
            "path": RUN_ARTIFACT_MANIFEST_PATH,
            "schema": manifest["schema"],
            "status": manifest["status"],
            "resultCount": len(result_files),
            "diagnosticCount": len(diagnostic_files),
        },
    }


def _collect_openfoam_native_time_results(case_dir: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    mesh_path = case_dir / "mesh" / "flowlab_mesh.vtk"
    if not mesh_path.is_file():
        return []
    time_dirs = _openfoam_time_directories(case_dir)
    if not time_dirs:
        return []
    try:
        geometry = parse_vtk_result(mesh_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"path": "openfoam-native-results", "size": 0, "skipped": f"cannot parse mesh/flowlab_mesh.vtk: {exc}"}]
    try:
        result_identity = resolve_openfoam_source_cell_identity(case_dir, geometry)
    except ResultIdentityError as exc:
        return [
            {
                "path": "openfoam-native-results",
                "size": 0,
                "skipped": f"cannot verify OpenFOAM source-cell identity: {exc}",
            }
        ]

    converted: list[dict[str, Any]] = []
    skipped: list[str] = []
    for time_value, time_dir in time_dirs:
        if len(converted) >= limit:
            break
        try:
            vtk_text, field_names = _openfoam_time_directory_to_vtk(
                time_dir,
                geometry,
                time_value,
                result_identity=result_identity,
            )
        except (ValueError, ResultIdentityError) as exc:
            skipped.append(f"{time_dir.name}: {exc}")
            continue
        relative_path = f"postProcessing/flowlabNative/{_safe_time_file_stem(time_dir.name)}.vtk"
        size = len(vtk_text.encode("utf-8"))
        if size > MAX_RESULT_FILE_BYTES:
            # The converted result is real evidence, and downstream evaluation
            # reads it from disk, so still write it and confirm its fields here.
            # Only the inline text is withheld: the size cap bounds the job
            # response, it does not judge whether the solve produced fields.
            # Without this, an exit-zero solve whose converted result exceeds the
            # cap is reported as failed and the file is never written at all -
            # which is what a 19,968-cell level does, since its converted result
            # is 2.8 MB against a 2 MB cap.
            oversized: dict[str, Any] = {
                "path": relative_path,
                "size": size,
                "skipped": "file too large",
                "time": time_value,
                "timeText": time_dir.name,
                "timeSource": "openfoam-time-directory",
                "sourceFields": field_names,
            }
            oversized_path = case_dir / relative_path
            oversized_path.parent.mkdir(parents=True, exist_ok=True)
            oversized_path.write_text(vtk_text, encoding="utf-8")
            oversized_summary = summarize_vtk_result_text(vtk_text)
            if isinstance(oversized_summary, dict) and oversized_summary.get("fields"):
                oversized["fieldSummary"] = oversized_summary
                oversized["verifiedOnDisk"] = True
            if NAN_TOKEN_RE.search(vtk_text):
                oversized["nanDetected"] = True
            converted.append(oversized)
            continue
        output_path = case_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(vtk_text, encoding="utf-8")
        payload = _result_file_payload(relative_path, size, vtk_text)
        payload["time"] = time_value
        payload["timeText"] = time_dir.name
        payload["timeSource"] = "openfoam-time-directory"
        payload["sourceFields"] = field_names
        if result_identity is not None:
            payload["sourceCellIdentity"] = {
                "schema": result_identity["schema"],
                "contractSha256": result_identity["contractSha256"],
                "solverToSourceCellSha256": result_identity[
                    "solverToSourceCellSha256"
                ],
                "sourceCellCount": result_identity["sourceCellCount"],
                "verified": True,
            }
        converted.append(payload)
    if converted:
        return converted
    if skipped:
        return [
            {
                "path": "openfoam-native-results",
                "size": 0,
                "skipped": "No parseable OpenFOAM native time-directory fields: " + "; ".join(skipped[:5]),
            }
        ]
    return []


def _openfoam_time_directories(case_dir: Path) -> list[tuple[float, Path]]:
    entries: list[tuple[float, Path]] = []
    for path in case_dir.iterdir() if case_dir.is_dir() else []:
        if not path.is_dir():
            continue
        try:
            time_value = float(path.name)
        except ValueError:
            continue
        if time_value <= 0:
            continue
        if not any((path / field).is_file() for field in OPENFOAM_NATIVE_RESULT_FIELDS):
            continue
        entries.append((time_value, path))
    return sorted(entries, key=lambda item: (item[0], item[1].name))


def _safe_time_file_stem(name: str) -> str:
    return "time_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", name).replace(".", "_")


def _openfoam_time_directory_to_vtk(
    time_dir: Path,
    geometry: dict[str, Any],
    time_value: float,
    *,
    result_identity: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    points = geometry.get("points") if isinstance(geometry.get("points"), list) else []
    cells = geometry.get("cells") if isinstance(geometry.get("cells"), list) else []
    cell_types = geometry.get("cellTypes") if isinstance(geometry.get("cellTypes"), list) else []
    if not points or not cells or len(cells) != len(cell_types):
        raise ValueError("FlowLab mesh geometry is missing points, cells, or cell types.")
    point_scalars: dict[str, list[float]] = {}
    point_vectors: dict[str, list[tuple[float, float, float]]] = {}
    cell_scalars: dict[str, list[float]] = {}
    cell_vectors: dict[str, list[tuple[float, float, float]]] = {}
    parsed_fields: list[str] = []
    field_errors: list[str] = []

    for field_name in sorted(OPENFOAM_NATIVE_RESULT_FIELDS):
        field_path = time_dir / field_name
        if not field_path.is_file():
            continue
        try:
            kind, values = _parse_openfoam_internal_field(field_path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            field_errors.append(f"{field_name}: {exc}")
            continue
        if kind == "scalar":
            scalar_values = [float(value) for value in values]
            if len(scalar_values) == 1 and len(cells) != 1:
                scalar_values = scalar_values * len(cells)
            if len(scalar_values) == len(cells):
                if result_identity is not None:
                    scalar_values = reorder_solver_values_to_source(
                        scalar_values,
                        result_identity,
                    )
                cell_scalars[field_name] = scalar_values
            elif len(scalar_values) == len(points):
                point_scalars[field_name] = scalar_values
            else:
                field_errors.append(f"{field_name}: tuple count {len(scalar_values)} does not match {len(cells)} cells or {len(points)} points")
                continue
        else:
            vector_values = [tuple(float(component) for component in vector) for vector in values]
            if len(vector_values) == 1 and len(cells) != 1:
                vector_values = vector_values * len(cells)
            if len(vector_values) == len(cells):
                if result_identity is not None:
                    vector_values = reorder_solver_values_to_source(
                        vector_values,
                        result_identity,
                    )
                cell_vectors[field_name] = vector_values
            elif len(vector_values) == len(points):
                point_vectors[field_name] = vector_values
            else:
                field_errors.append(f"{field_name}: tuple count {len(vector_values)} does not match {len(cells)} cells or {len(points)} points")
                continue
        parsed_fields.append(field_name)

    if not parsed_fields:
        detail = "; ".join(field_errors[:5]) if field_errors else "no supported fields found"
        raise ValueError(detail)
    if result_identity is not None:
        source_count = int(result_identity["sourceCellCount"])
        if source_count != len(cells):
            raise ResultIdentityError(
                "verified source-cell count does not match generated result geometry"
            )
        cell_scalars[SOURCE_CELL_ID_FIELD] = [
            float(source_index) for source_index in range(source_count)
        ]
    return _legacy_vtk_unstructured_result(
        points,
        cells,
        cell_types,
        point_scalars,
        point_vectors,
        cell_scalars,
        cell_vectors,
        title=f"FlowLab OpenFOAM native time {time_value:.9g}",
    ), parsed_fields


def _parse_openfoam_internal_field(text: str) -> tuple[str, list[Any]]:
    clean = _strip_openfoam_comments(text)
    uniform_match = re.search(r"\binternalField\s+uniform\s+(\([^;]+?\)|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|nan)\s*;", clean, re.IGNORECASE)
    if uniform_match:
        raw_value = uniform_match.group(1).strip()
        if raw_value.startswith("("):
            return "vector", [_parse_openfoam_vector(raw_value)]
        return "scalar", [float(raw_value)]

    nonuniform_match = re.search(
        r"\binternalField\s+nonuniform\s+List<(?P<kind>scalar|vector)>\s*(?P<count>\d+)\s*\((?P<body>.*?)\)\s*;",
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if not nonuniform_match:
        raise ValueError("internalField must be uniform or nonuniform List<scalar|vector>.")
    expected_count = int(nonuniform_match.group("count"))
    kind = nonuniform_match.group("kind").lower()
    body = nonuniform_match.group("body")
    if kind == "scalar":
        values = [float(match.group(0)) for match in FLOAT_RE.finditer(body)]
    else:
        values = [_parse_openfoam_vector(match.group(0)) for match in re.finditer(r"\([^()]+\)", body)]
    if len(values) != expected_count:
        raise ValueError(f"internalField declared {expected_count} {kind} values but contained {len(values)}.")
    return kind, values


def _strip_openfoam_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _parse_openfoam_vector(value: str) -> tuple[float, float, float]:
    numbers = [float(match.group(0)) for match in FLOAT_RE.finditer(value)]
    if len(numbers) != 3:
        raise ValueError(f"Expected OpenFOAM vector with 3 components, got {len(numbers)}.")
    return (numbers[0], numbers[1], numbers[2])


def _legacy_vtk_unstructured_result(
    points: list[Any],
    cells: list[Any],
    cell_types: list[Any],
    point_scalars: dict[str, list[float]],
    point_vectors: dict[str, list[tuple[float, float, float]]],
    cell_scalars: dict[str, list[float]],
    cell_vectors: dict[str, list[tuple[float, float, float]]],
    *,
    title: str,
) -> str:
    lines = [
        "# vtk DataFile Version 3.0",
        title,
        "ASCII",
        "DATASET UNSTRUCTURED_GRID",
        f"POINTS {len(points)} float",
    ]
    lines.extend(" ".join(_format_float(float(value)) for value in point) for point in points)
    total_size = sum(len(cell) + 1 for cell in cells)
    lines.append(f"CELLS {len(cells)} {total_size}")
    lines.extend(" ".join([str(len(cell)), *(str(int(index)) for index in cell)]) for cell in cells)
    lines.append(f"CELL_TYPES {len(cell_types)}")
    lines.extend(str(int(cell_type)) for cell_type in cell_types)
    if point_scalars or point_vectors:
        lines.append(f"POINT_DATA {len(points)}")
        _append_vtk_fields(lines, point_scalars, point_vectors)
    if cell_scalars or cell_vectors:
        lines.append(f"CELL_DATA {len(cells)}")
        _append_vtk_fields(lines, cell_scalars, cell_vectors)
    return "\n".join(lines) + "\n"


def _append_vtk_fields(
    lines: list[str],
    scalars: dict[str, list[float]],
    vectors: dict[str, list[tuple[float, float, float]]],
) -> None:
    for name, values in sorted(scalars.items()):
        lines.extend([f"SCALARS {name} float 1", "LOOKUP_TABLE default"])
        lines.extend(_format_float(float(value)) for value in values)
    for name, values in sorted(vectors.items()):
        lines.append(f"VECTORS {name} float")
        lines.extend(" ".join(_format_float(float(component)) for component in vector) for vector in values)


def _collect_code_saturne_ensight_results(case_dir: Path, limit: int) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for case_file in sorted(case_dir.glob("RESU/*/postprocessing/RESULTS_FLUID_DOMAIN.case")):
        if len(converted) >= limit:
            break
        relative_path = case_file.relative_to(case_dir)
        output_path = str(relative_path.with_name("flowlab_code_saturne_fluid.vtk"))
        try:
            vtk_text = _code_saturne_ensight_to_vtk(case_file)
        except ValueError as exc:
            converted.append({"path": output_path, "size": 0, "skipped": str(exc)})
            continue
        size = len(vtk_text.encode("utf-8"))
        if size > MAX_RESULT_FILE_BYTES:
            converted.append({"path": output_path, "size": size, "skipped": "file too large"})
            continue
        converted.append(_result_file_payload(output_path, size, vtk_text))
    return converted


def _code_saturne_ensight_to_vtk(case_file: Path) -> str:
    post_dir = case_file.parent
    geometry_name: str | None = None
    variables: list[tuple[str, str, str]] = []
    for raw_line in case_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("model:"):
            geometry_name = line.split(":", 1)[1].strip()
            continue
        match = re.match(r"(scalar|vector)\s+per\s+element:\s+(?:\d+\s+)?(.+?)\s+(\S+)$", line)
        if match:
            variables.append((match.group(1), _normalize_ensight_field_name(match.group(2)), match.group(3)))
    if not geometry_name:
        raise ValueError("Code_Saturne EnSight case is missing geometry model.")
    points, cells = _parse_ensight_hexa_geometry(post_dir / geometry_name)
    point_scalars: dict[str, list[float]] = {}
    point_vectors: dict[str, list[tuple[float, float, float]]] = {}
    for kind, name, pattern in variables:
        candidate = post_dir / pattern.replace("*****", "00001")
        if not candidate.is_file():
            continue
        if kind == "scalar":
            cell_values = _parse_ensight_element_scalar(candidate, len(cells))
            point_scalars[name] = _cell_scalars_to_points(len(points), cells, cell_values)
        elif kind == "vector":
            cell_values = _parse_ensight_element_vector(candidate, len(cells))
            point_vectors[name] = _cell_vectors_to_points(len(points), cells, cell_values)
    if not point_scalars and not point_vectors:
        raise ValueError("Code_Saturne EnSight case has no supported scalar or vector fields.")
    return _legacy_vtk_hexa_result(points, cells, point_scalars, point_vectors)


def _normalize_ensight_field_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_")
    aliases = {
        "Pressure": "pressure",
        "Total_Pressure": "total_pressure",
        "Velocity": "velocity",
        "CFL": "cfl",
        "Fourier_Number": "fourier_number",
    }
    return aliases.get(normalized, normalized)


def _read_ensight_record(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 80 > len(data):
        raise ValueError("Unexpected end of EnSight record.")
    return data[offset : offset + 80].decode("latin1", errors="replace").strip(), offset + 80


def _read_int(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("Unexpected end of EnSight integer data.")
    return struct.unpack_from("<i", data, offset)[0], offset + 4


def _read_floats(data: bytes, offset: int, count: int) -> tuple[list[float], int]:
    byte_count = count * 4
    if offset + byte_count > len(data):
        raise ValueError("Unexpected end of EnSight float data.")
    return list(struct.unpack_from("<" + "f" * count, data, offset)), offset + byte_count


def _parse_ensight_hexa_geometry(path: Path) -> tuple[list[tuple[float, float, float]], list[list[int]]]:
    data = path.read_bytes()
    offset = 0
    node_count: int | None = None
    points: list[tuple[float, float, float]] = []
    while offset < len(data):
        record, offset = _read_ensight_record(data, offset)
        if record == "part":
            _part_id, offset = _read_int(data, offset)
            _part_name, offset = _read_ensight_record(data, offset)
            continue
        if record != "coordinates":
            continue
        node_count, offset = _read_int(data, offset)
        if node_count <= 0 or node_count > 2_000_000:
            raise ValueError("Code_Saturne EnSight geometry has an invalid node count.")
        xs, offset = _read_floats(data, offset, node_count)
        ys, offset = _read_floats(data, offset, node_count)
        zs, offset = _read_floats(data, offset, node_count)
        points = list(zip(xs, ys, zs, strict=True))
        element_type, offset = _read_ensight_record(data, offset)
        if element_type != "hexa8":
            raise ValueError(f"Unsupported Code_Saturne EnSight element type `{element_type}`.")
        cell_count, offset = _read_int(data, offset)
        if cell_count <= 0 or cell_count > 2_000_000:
            raise ValueError("Code_Saturne EnSight geometry has an invalid cell count.")
        connectivity, offset = _read_floats_as_ints(data, offset, cell_count * 8)
        cells = []
        for index in range(0, len(connectivity), 8):
            cell = [node - 1 for node in connectivity[index : index + 8]]
            if any(node < 0 or node >= node_count for node in cell):
                raise ValueError("Code_Saturne EnSight connectivity is out of range.")
            cells.append(cell)
        return points, cells
    raise ValueError("Code_Saturne EnSight geometry is missing coordinates.")


def _read_floats_as_ints(data: bytes, offset: int, count: int) -> tuple[list[int], int]:
    byte_count = count * 4
    if offset + byte_count > len(data):
        raise ValueError("Unexpected end of EnSight connectivity data.")
    return list(struct.unpack_from("<" + "i" * count, data, offset)), offset + byte_count


def _parse_ensight_element_scalar(path: Path, cell_count: int) -> list[float]:
    values = _parse_ensight_element_values(path, cell_count)
    if len(values) < cell_count:
        raise ValueError("Code_Saturne EnSight scalar field is shorter than cell count.")
    return values[:cell_count]


def _parse_ensight_element_vector(path: Path, cell_count: int) -> list[tuple[float, float, float]]:
    values = _parse_ensight_element_values(path, cell_count * 3)
    if len(values) < cell_count * 3:
        raise ValueError("Code_Saturne EnSight vector field is shorter than cell count.")
    xs = values[:cell_count]
    ys = values[cell_count : cell_count * 2]
    zs = values[cell_count * 2 : cell_count * 3]
    return list(zip(xs, ys, zs, strict=True))


def _parse_ensight_element_values(path: Path, minimum_count: int) -> list[float]:
    data = path.read_bytes()
    offset = 0
    while offset < len(data):
        record, offset = _read_ensight_record(data, offset)
        if record == "part":
            _part_id, offset = _read_int(data, offset)
            continue
        if record != "hexa8":
            continue
        remaining = (len(data) - offset) // 4
        if remaining < minimum_count:
            raise ValueError("Code_Saturne EnSight field does not contain enough element values.")
        values, _offset = _read_floats(data, offset, remaining)
        return values
    raise ValueError("Code_Saturne EnSight field is missing hexa8 element data.")


def _cell_scalars_to_points(point_count: int, cells: list[list[int]], values: list[float]) -> list[float]:
    totals = [0.0 for _ in range(point_count)]
    counts = [0 for _ in range(point_count)]
    for cell, value in zip(cells, values, strict=False):
        for point_index in cell:
            totals[point_index] += value
            counts[point_index] += 1
    return [totals[index] / counts[index] if counts[index] else 0.0 for index in range(point_count)]


def _cell_vectors_to_points(
    point_count: int, cells: list[list[int]], values: list[tuple[float, float, float]]
) -> list[tuple[float, float, float]]:
    totals = [[0.0, 0.0, 0.0] for _ in range(point_count)]
    counts = [0 for _ in range(point_count)]
    for cell, vector in zip(cells, values, strict=False):
        for point_index in cell:
            totals[point_index][0] += vector[0]
            totals[point_index][1] += vector[1]
            totals[point_index][2] += vector[2]
            counts[point_index] += 1
    return [
        (
            totals[index][0] / counts[index],
            totals[index][1] / counts[index],
            totals[index][2] / counts[index],
        )
        if counts[index]
        else (0.0, 0.0, 0.0)
        for index in range(point_count)
    ]


def _format_float(value: float) -> str:
    return f"{value:.9g}"


def _legacy_vtk_hexa_result(
    points: list[tuple[float, float, float]],
    cells: list[list[int]],
    scalars: dict[str, list[float]],
    vectors: dict[str, list[tuple[float, float, float]]],
) -> str:
    lines = [
        "# vtk DataFile Version 3.0",
        "FlowLab Code_Saturne EnSight converted result",
        "ASCII",
        "DATASET UNSTRUCTURED_GRID",
        f"POINTS {len(points)} float",
    ]
    lines.extend(" ".join(_format_float(value) for value in point) for point in points)
    lines.append(f"CELLS {len(cells)} {len(cells) * 9}")
    lines.extend(" ".join(["8", *(str(index) for index in cell)]) for cell in cells)
    lines.append(f"CELL_TYPES {len(cells)}")
    lines.extend("12" for _cell in cells)
    lines.append(f"POINT_DATA {len(points)}")
    for name, values in sorted(scalars.items()):
        lines.extend([f"SCALARS {name} float 1", "LOOKUP_TABLE default"])
        lines.extend(_format_float(value) for value in values)
    for name, values in sorted(vectors.items()):
        lines.append(f"VECTORS {name} float")
        lines.extend(" ".join(_format_float(component) for component in vector) for vector in values)
    return "\n".join(lines) + "\n"


def collect_diagnostic_files(case_dir: Path) -> list[dict[str, str | int]]:
    diagnostics: list[dict[str, str | int]] = []
    for name in sorted(DIAGNOSTIC_TOP_LEVEL_FILENAMES):
        path = case_dir / name
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_DIAGNOSTIC_FILE_BYTES:
            diagnostics.append({"path": name, "size": size, "skipped": "file too large"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            diagnostics.append({"path": name, "size": size, "skipped": "not UTF-8 text"})
            continue
        diagnostics.append({"path": name, "size": size, "text": text})
    for root_name in DIAGNOSTIC_ROOTS:
        root = case_dir / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if len(diagnostics) >= MAX_DIAGNOSTIC_FILES:
                return diagnostics
            if not path.is_file() or not _is_diagnostic_file(path):
                continue
            size = path.stat().st_size
            relative_path = str(path.relative_to(case_dir))
            if size > MAX_DIAGNOSTIC_FILE_BYTES:
                diagnostics.append({"path": relative_path, "size": size, "skipped": "file too large"})
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                diagnostics.append({"path": relative_path, "size": size, "skipped": "not UTF-8 text"})
                continue
            diagnostics.append({"path": relative_path, "size": size, "text": text})
    return diagnostics


def _is_diagnostic_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in DIAGNOSTIC_EXTENSIONS
        or name in DIAGNOSTIC_SPECIAL_FILENAMES
        or name.startswith("listing")
    )


def _diagnostic_kind(path: str) -> str:
    lowered = path.lower()
    if lowered == "outputs/summary.json":
        return "mujoco-summary"
    if lowered.startswith("resu/") and (lowered.endswith("/error") or lowered.endswith("/listing")):
        return "code-saturne-error"
    if lowered in {"history.csv", "history.dat"}:
        return "residuals"
    if "residual" in lowered:
        return "residuals"
    if "preprocessor" in lowered:
        return "preprocessor"
    if "compile" in lowered:
        return "compile"
    if "probe" in lowered:
        return "probes"
    if "force" in lowered:
        return "forces"
    if "fieldminmax" in lowered or "fieldextents" in lowered or "minmax" in lowered:
        return "field-min-max"
    return "table"


def _patch_metric_kind(path: str) -> str | None:
    lowered = path.lower()
    if "patchflowrate" in lowered or "flowratepatch" in lowered or "surfaceflow" in lowered or "surface_flow" in lowered:
        return "patch-flow-rate"
    if "patchaverage" in lowered or "patch_average" in lowered:
        return "patch-average"
    if "wallshearstress" in lowered or "wall_shear" in lowered:
        return "wall-shear"
    if "force" in lowered:
        return "forces"
    if "probe" in lowered and (path.endswith("/p") or path.endswith("/p_rgh") or "pressure" in lowered):
        return "pressure-probes"
    return None


def _numeric_table_rows(text: str) -> tuple[list[str], list[dict[str, float]]]:
    header: list[str] | None = None
    rows: list[dict[str, float]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        parts = _split_table_line(stripped)
        if not parts:
            continue
        numbers = [_float_or_none(part) for part in parts]
        if any(value is None for value in numbers):
            if _is_table_header(parts, numbers):
                header = parts
            continue
        if header is None:
            header = ["Time", *[f"c{index}" for index in range(1, len(parts))]] if len(parts) > 1 else [f"c{index}" for index in range(len(parts))]
        if len(parts) != len(header):
            continue
        row = {column: value for column, value in zip(header, numbers, strict=False) if value is not None}
        if len(row) >= 2:
            rows.append(row)
    return header or [], rows


def _clean_patch_column_name(column: str) -> str:
    cleaned = re.sub(r"^(?:areaAverage|areaIntegrate|sum|average|patchFlowRate|flowRate|mag)\((.+)\)$", r"\1", column.strip())
    cleaned = cleaned.strip('"').strip("'").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", cleaned).strip("_")
    return cleaned or column


def _patch_role(patch_name: str) -> str:
    lowered = patch_name.lower()
    if "inlet" in lowered or "source" in lowered:
        return "inlet"
    if "outlet" in lowered or "sink" in lowered:
        return "outlet"
    if "wall" in lowered:
        return "wall"
    if "interface" in lowered:
        return "interface"
    return "unknown"


def _patch_from_metric_path(path: str, fallback: str) -> str:
    parts = path.split("/")
    for part in parts:
        match = re.search(r"\(([^()]+)\)", part)
        if match:
            return _clean_patch_column_name(match.group(1))
    if len(parts) >= 4 and parts[0] == "postProcessing":
        candidate = parts[1]
        if candidate.lower() not in {"forces", "wallforces", "wallshearstress", "patchflowrate", "patchaverage", "probes"}:
            cleaned = _clean_patch_column_name(candidate)
            cleaned = re.sub(r"^(?:patchFlowRate|patchAverage)[_.-]+", "", cleaned, flags=re.IGNORECASE)
            return cleaned or _clean_patch_column_name(candidate)
    if len(parts) >= 3 and parts[0] == "VTK":
        return _clean_patch_column_name(parts[1])
    return fallback


def _ensure_patch(metrics: dict[str, Any], patch_name: str) -> dict[str, Any]:
    patches = metrics.setdefault("patches", {})
    patch = patches.setdefault(patch_name, {"patchName": patch_name, "role": _patch_role(patch_name), "sources": []})
    return patch


def _latest_table(path: Path) -> tuple[list[str], dict[str, float]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    columns, rows = _numeric_table_rows(text)
    if not rows:
        return None
    return columns, rows[-1]


def collect_patch_metrics(case_dir: Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "schema": "flowlab.patch_metrics.v1",
        "status": "missing",
        "patches": {},
        "flowBalance": None,
        "pressureDrops": [],
        "forces": [],
        "pressureProbes": [],
        "warnings": [],
        "sources": [],
    }
    root = case_dir / "postProcessing"
    if not root.is_dir():
        metrics["warnings"].append("OpenFOAM postProcessing directory is missing; patch metrics were not generated.")
        return metrics

    seen_kinds: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(case_dir))
        kind = _patch_metric_kind(relative_path)
        if kind is None:
            continue
        if path.stat().st_size > MAX_DIAGNOSTIC_FILE_BYTES:
            metrics["warnings"].append(f"{relative_path} exceeds the patch-metric parse limit.")
            metrics["sources"].append({"path": relative_path, "kind": kind, "status": "skipped"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            metrics["warnings"].append(f"{relative_path} is not UTF-8 text.")
            metrics["sources"].append({"path": relative_path, "kind": kind, "status": "skipped"})
            continue
        try:
            parsed = _apply_patch_metric_file(metrics, relative_path, kind, text)
        except ValueError as exc:
            parsed = False
            metrics["warnings"].append(f"{relative_path}: {exc}")
        metrics["sources"].append({"path": relative_path, "kind": kind, "status": "parsed" if parsed else "unparsed"})
        if parsed:
            seen_kinds.add(kind)

    for path in sorted((case_dir / "VTK").rglob("*.vtk")) if (case_dir / "VTK").is_dir() else []:
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(case_dir))
        parts = Path(relative_path).parts
        if len(parts) < 3 or _patch_role(parts[1]) != "wall":
            continue
        if path.stat().st_size > MAX_RESULT_FILE_BYTES:
            metrics["warnings"].append(f"{relative_path} exceeds the wall-shear VTK parse limit.")
            metrics["sources"].append({"path": relative_path, "kind": "wall-shear", "status": "skipped"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            metrics["warnings"].append(f"{relative_path} is not UTF-8 text.")
            metrics["sources"].append({"path": relative_path, "kind": "wall-shear", "status": "skipped"})
            continue
        parsed = _apply_openfoam_wall_shear_vtk_metric(metrics, relative_path, text)
        if parsed:
            seen_kinds.add("wall-shear")
        metrics["sources"].append({"path": relative_path, "kind": "wall-shear", "status": "parsed" if parsed else "unparsed"})

    _finalize_patch_metrics(metrics, seen_kinds)
    return metrics


def _load_openfoam_patch_metric_contract(case_dir: Path) -> dict[str, Any]:
    try:
        loaded = json.loads((case_dir / "constant" / "flowlab_patch_metrics.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _case_advanced_mode(case_dir: Path) -> str | None:
    try:
        manifest = json.loads((case_dir / CASE_MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = manifest.get("advancedMode") if isinstance(manifest, dict) else None
    return str(mode) if isinstance(mode, str) and mode else None


def _openfoam_required_diagnostic_kinds(contract: dict[str, Any]) -> dict[str, str]:
    function_objects = contract.get("functionObjects") if isinstance(contract.get("functionObjects"), list) else []
    required = {
        "patch-flow-rate": "patchFlowRate",
        "patch-average": "patchAverage",
        "wall-shear": "wallShearStress",
        "forces": "wallForces",
    }
    if "pressureProbes" in function_objects:
        required["pressure-probes"] = "pressureProbes"
    return required


def _openfoam_patch_diagnostic_output_paths(metrics: dict[str, Any]) -> dict[str, list[str]]:
    observed: dict[str, list[str]] = {}
    for source in metrics.get("sources", []):
        if not isinstance(source, dict) or source.get("status") != "parsed":
            continue
        kind = source.get("kind")
        path = source.get("path")
        if isinstance(kind, str) and isinstance(path, str):
            observed.setdefault(kind, []).append(path)
    return {kind: sorted(paths) for kind, paths in observed.items()}


def _openfoam_mesh_command_runs(case_dir: Path) -> list[dict[str, Any]]:
    try:
        acceptance = json.loads((case_dir / "mesh" / "production_mesh_acceptance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    native_quality = acceptance.get("nativeQualityEvidence") if isinstance(acceptance.get("nativeQualityEvidence"), dict) else {}
    reports = native_quality.get("solverReports") if isinstance(native_quality.get("solverReports"), dict) else {}
    openfoam_report = reports.get("openfoam") if isinstance(reports.get("openfoam"), dict) else {}
    command_runs = openfoam_report.get("commandRuns") if isinstance(openfoam_report.get("commandRuns"), list) else []
    return [run for run in command_runs if isinstance(run, dict)]


def write_openfoam_diagnostics_acceptance(case_dir: Path, *, exit_code: int | None, mode: str | None = None) -> dict[str, Any]:
    contract = _load_openfoam_patch_metric_contract(case_dir)
    metrics = collect_patch_metrics(case_dir)
    observed = _openfoam_patch_diagnostic_output_paths(metrics)
    required = _openfoam_required_diagnostic_kinds(contract)
    missing = [
        {
            "kind": kind,
            "functionObject": function_object,
            "reason": f"No parseable OpenFOAM postProcessing output was found for `{function_object}`.",
        }
        for kind, function_object in required.items()
        if kind not in observed
    ]
    unparsed_sources = [
        source
        for source in metrics.get("sources", [])
        if isinstance(source, dict) and source.get("status") != "parsed"
    ]
    status = "complete" if not missing else "partial" if observed else "missing"
    strict = mode != "conjugate-heat-transfer"
    completion_gate = {
        "strict": strict,
        "status": "pass" if not missing else "fail" if strict else "partial",
        "blockingReasons": [item["reason"] for item in missing] if strict else [],
    }
    artifact = {
        "schema": "flowlab.openfoam_diagnostics_acceptance.v1",
        "status": status,
        "advancedMode": mode,
        "generatedFunctionObjects": contract.get("functionObjects", []),
        "patchPlan": contract.get("patches", {}),
        "requiredDiagnostics": [
            {"kind": kind, "functionObject": function_object}
            for kind, function_object in required.items()
        ],
        "observedOutputs": observed,
        "missingDiagnostics": missing,
        "unparsedSources": unparsed_sources,
        "parserStatus": metrics.get("status"),
        "patchMetrics": metrics,
        "commandExitCodes": {
            "solver": exit_code,
            "nativeMesh": [
                {
                    "command": run.get("command"),
                    "exitCode": run.get("exitCode"),
                    "status": run.get("status"),
                    "logPath": run.get("logPath"),
                }
                for run in _openfoam_mesh_command_runs(case_dir)
            ],
        },
        "completionGate": completion_gate,
    }
    output = case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def openfoam_diagnostics_quality_error(acceptance: dict[str, Any]) -> str | None:
    gate = acceptance.get("completionGate") if isinstance(acceptance.get("completionGate"), dict) else {}
    if gate.get("status") != "fail":
        return None
    reasons = gate.get("blockingReasons") if isinstance(gate.get("blockingReasons"), list) else []
    detail = "; ".join(str(reason) for reason in reasons[:4]) or "required OpenFOAM patch diagnostics were not parsed"
    return f"OpenFOAM diagnostics incomplete: {detail}"


def read_openfoam_diagnostics_acceptance(case_dir: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads((case_dir / OPENFOAM_DIAGNOSTICS_ACCEPTANCE_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _apply_patch_metric_file(metrics: dict[str, Any], relative_path: str, kind: str, text: str) -> bool:
    if kind == "forces":
        summary = _parse_openfoam_forces(relative_path, text)
        if not summary or not isinstance(summary.get("latest"), dict):
            raise ValueError("no parseable OpenFOAM force rows")
        latest = summary["latest"]
        patch_name = _patch_from_metric_path(relative_path, "walls" if "wall" in relative_path.lower() else "forces")
        force = {
            "patchName": patch_name,
            "time": latest.get("Time"),
            "force": {
                "x": float(latest.get("pressureFx", 0.0)) + float(latest.get("viscousFx", 0.0)),
                "y": float(latest.get("pressureFy", 0.0)) + float(latest.get("viscousFy", 0.0)),
                "z": float(latest.get("pressureFz", 0.0)) + float(latest.get("viscousFz", 0.0)),
            },
            "moment": {
                "x": float(latest.get("pressureMx", 0.0)) + float(latest.get("viscousMx", 0.0)),
                "y": float(latest.get("pressureMy", 0.0)) + float(latest.get("viscousMy", 0.0)),
                "z": float(latest.get("pressureMz", 0.0)) + float(latest.get("viscousMz", 0.0)),
            },
            "components": latest,
            "path": relative_path,
        }
        force["forceMagnitude"] = math.sqrt(sum(float(value) ** 2 for value in force["force"].values()))
        force["momentMagnitude"] = math.sqrt(sum(float(value) ** 2 for value in force["moment"].values()))
        metrics["forces"].append(force)
        patch = _ensure_patch(metrics, patch_name)
        patch["force"] = force
        patch["sources"].append(relative_path)
        return True

    if kind == "wall-shear":
        vector_rows: list[
            tuple[float, str, tuple[float, float, float], tuple[float, float, float]]
        ] = []
        number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        row_pattern = re.compile(
            rf"^\s*({number})\s+([A-Za-z0-9_.-]+)\s+"
            rf"\(\s*({number})\s+({number})\s+({number})\s*\)\s+"
            rf"\(\s*({number})\s+({number})\s+({number})\s*\)\s*$"
        )
        for raw_line in text.splitlines():
            match = row_pattern.match(raw_line)
            if match is None:
                continue
            values = [float(value) for value in match.groups()[2:]]
            vector_rows.append(
                (
                    float(match.group(1)),
                    str(match.group(2)),
                    (values[0], values[1], values[2]),
                    (values[3], values[4], values[5]),
                )
            )
        if vector_rows:
            time_value, patch_name, component_minimum, component_maximum = (
                vector_rows[-1]
            )
            patch = _ensure_patch(metrics, patch_name)
            patch["wallShear"] = {
                "min": None,
                "mean": None,
                "max": None,
                "componentMinimumVector": list(component_minimum),
                "componentMaximumVector": list(component_maximum),
                "unit": "solver vector units",
                "time": time_value,
                "path": relative_path,
                "aggregation": "componentwise-extrema-not-pointwise-magnitude",
            }
            patch["sources"].append(relative_path)
            return True

    columns, rows = _numeric_table_rows(text)
    if not rows:
        raise ValueError("no parseable numeric table rows")
    latest = rows[-1]
    time_value = latest.get("Time") or latest.get("time")

    if kind == "patch-flow-rate":
        parsed = False
        value_columns = [column for column in columns if column.lower() != "time"]
        for column, value in latest.items():
            if column.lower() == "time":
                continue
            patch_name = _clean_patch_column_name(column)
            if len(value_columns) == 1:
                patch_name = _patch_from_metric_path(relative_path, patch_name)
            patch = _ensure_patch(metrics, patch_name)
            patch["flowRate"] = {"value": float(value), "unit": "m3/s", "time": time_value, "path": relative_path}
            patch["sources"].append(relative_path)
            parsed = True
        return parsed

    if kind == "patch-average":
        parsed = False
        value_columns = [column for column in columns if column.lower() != "time"]
        for column, value in latest.items():
            if column.lower() == "time":
                continue
            patch_name = _clean_patch_column_name(column)
            patch = _ensure_patch(metrics, patch_name)
            # Identify the pressure field from the OpenFOAM value-column header
            # (e.g. `# Time   areaAverage(p)`) as well as the file path. FlowLab
            # writes per-patch outputs to `patchAverage_<patch>/` directories whose
            # path contains no matchable `p`/`pressure` token, and the numeric-table
            # parser replaces the header with a generic `c1` column name, so
            # path/column-only detection missed the pressure field and left
            # `averagePressure` unset -- which blocked all pressure-drop metrics.
            header_match = re.search(r"^#\s*Time\b(.*)$", text, re.MULTILINE)
            header_fields = header_match.group(1) if header_match else ""
            field_source = f"{column} {header_fields} {relative_path}".lower()
            field_name = "p" if re.search(r"\(\s*p\s*\)|(?:^|[/_.-])p(?:$|[/_.-])|pressure", field_source) else "average"
            patch_key = "averagePressure" if field_name == "p" else "patchAverage"
            unit = "Pa" if field_name == "p" else "solver units"
            if len(value_columns) == 1:
                patch_name = _patch_from_metric_path(relative_path, patch_name)
                patch = _ensure_patch(metrics, patch_name)
            patch[patch_key] = {"value": float(value), "unit": unit, "time": time_value, "path": relative_path, "field": field_name}
            patch["sources"].append(relative_path)
            parsed = True
        return parsed

    if kind == "wall-shear":
        grouped: dict[str, dict[str, float]] = {}
        for column, value in latest.items():
            lower = column.lower()
            if lower == "time":
                continue
            match = re.match(r"(.+?)[_.-]?(min|mean|avg|max)$", column, re.IGNORECASE)
            if match:
                patch_name = _clean_patch_column_name(match.group(1))
                key = "mean" if match.group(2).lower() == "avg" else match.group(2).lower()
                grouped.setdefault(patch_name, {})[key] = float(value)
            else:
                grouped.setdefault(_patch_from_metric_path(relative_path, "walls"), {})[_clean_patch_column_name(column)] = float(value)
        if not grouped:
            return False
        for patch_name, values in grouped.items():
            if {"min", "mean", "max"}.isdisjoint(values):
                ordered_values = [value for key, value in values.items() if key.lower() != "time"]
                if ordered_values:
                    values = {"min": min(ordered_values), "mean": sum(ordered_values) / len(ordered_values), "max": max(ordered_values)}
            patch = _ensure_patch(metrics, patch_name)
            patch["wallShear"] = {
                "min": values.get("min"),
                "mean": values.get("mean"),
                "max": values.get("max"),
                "unit": "Pa",
                "time": time_value,
                "path": relative_path,
            }
            patch["sources"].append(relative_path)
        return True

    if kind == "pressure-probes":
        values = [float(value) for key, value in latest.items() if key.lower() != "time"]
        if not values:
            return False
        metrics["pressureProbes"].append(
            {
                "path": relative_path,
                "time": time_value,
                "sampleCount": len(values),
                "minPressure": min(values),
                "maxPressure": max(values),
                "pressureSpan": max(values) - min(values),
                "unit": "Pa",
            }
        )
        return True

    return False


def _apply_openfoam_wall_shear_vtk_metric(metrics: dict[str, Any], relative_path: str, text: str) -> bool:
    try:
        summary = summarize_vtk_result_text(text)
    except Exception:
        return False
    fields = summary.get("fields") if isinstance(summary, dict) else []
    if not isinstance(fields, list):
        return False
    candidates = [
        field
        for field in fields
        if isinstance(field, dict)
        and str(field.get("name", "")).lower() == "wallshearstress"
        and field.get("kind") == "vector-magnitude"
    ]
    if not candidates:
        return False
    field = next((item for item in candidates if item.get("location") == "cell"), candidates[0])
    patch_name = _patch_from_metric_path(relative_path, "walls")
    patch = _ensure_patch(metrics, patch_name)
    patch["wallShear"] = {
        "min": field.get("min"),
        "mean": field.get("mean"),
        "max": field.get("max"),
        "unit": "Pa",
        "path": relative_path,
        "field": "wallShearStress",
    }
    patch["sources"].append(relative_path)
    return True


def _finalize_patch_metrics(metrics: dict[str, Any], seen_kinds: set[str]) -> None:
    patches = metrics.get("patches") if isinstance(metrics.get("patches"), dict) else {}
    flow_patches = [patch for patch in patches.values() if isinstance(patch, dict) and isinstance(patch.get("flowRate"), dict)]
    inlet_candidates = [patch for patch in flow_patches if patch.get("role") == "inlet"]
    outlet_candidates = [patch for patch in flow_patches if patch.get("role") == "outlet"]
    if not inlet_candidates:
        inlet_candidates = [patch for patch in flow_patches if float(patch["flowRate"].get("value", 0.0)) < 0]
    if not outlet_candidates:
        outlet_candidates = [patch for patch in flow_patches if float(patch["flowRate"].get("value", 0.0)) > 0]
    if inlet_candidates or outlet_candidates:
        inlet_flow = sum(abs(float(patch["flowRate"].get("value", 0.0))) for patch in inlet_candidates)
        outlet_flow = sum(abs(float(patch["flowRate"].get("value", 0.0))) for patch in outlet_candidates)
        imbalance = outlet_flow - inlet_flow
        metrics["flowBalance"] = {
            "inletFlow": inlet_flow,
            "outletFlow": outlet_flow,
            "imbalance": imbalance,
            "relativeImbalance": abs(imbalance) / max(inlet_flow, outlet_flow, 1e-30),
            "unit": "m3/s",
            "inletPatches": [patch["patchName"] for patch in inlet_candidates],
            "outletPatches": [patch["patchName"] for patch in outlet_candidates],
        }

    pressure_patches = [patch for patch in patches.values() if isinstance(patch, dict) and isinstance(patch.get("averagePressure"), dict)]
    inlet_pressure = [patch for patch in pressure_patches if patch.get("role") == "inlet"]
    outlet_pressure = [patch for patch in pressure_patches if patch.get("role") == "outlet"]
    for inlet in inlet_pressure:
        for outlet in outlet_pressure:
            inlet_value = float(inlet["averagePressure"]["value"])
            outlet_value = float(outlet["averagePressure"]["value"])
            metrics["pressureDrops"].append(
                {
                    "fromPatch": inlet["patchName"],
                    "toPatch": outlet["patchName"],
                    "inletPressure": inlet_value,
                    "outletPressure": outlet_value,
                    "deltaP": inlet_value - outlet_value,
                    "unit": "Pa",
                }
            )

    required = {
        "patch-flow-rate": "OpenFOAM patchFlowRate output is missing; inlet/outlet flow balance is unavailable.",
        "patch-average": "OpenFOAM patchAverage pressure output is missing; pressure-drop metrics are unavailable.",
        "wall-shear": "OpenFOAM wallShearStress output is missing; wall shear metrics are unavailable.",
        "forces": "OpenFOAM forces output is missing; integrated force and moment metrics are unavailable.",
    }
    for kind, warning in required.items():
        if kind not in seen_kinds:
            metrics["warnings"].append(warning)
    parsed_sources = [source for source in metrics["sources"] if source.get("status") == "parsed"]
    if parsed_sources:
        metrics["status"] = "partial" if metrics["warnings"] else "complete"
    elif metrics["sources"]:
        metrics["status"] = "unparsed"


def _split_table_line(line: str) -> list[str]:
    cleaned = line.strip().lstrip("#").strip().replace(",", " ")
    return [part.strip('"') for part in cleaned.split() if part.strip('"')]


def _is_table_header(parts: list[str], values: list[float | None]) -> bool:
    if not parts or all(value is not None for value in values):
        return False
    if values[0] is not None:
        return False
    if len(parts) == 1:
        return False
    if any(":" in part or "(" in part or ")" in part for part in parts):
        return False
    return True


def parse_diagnostic_files(diagnostic_files: list[dict[str, str | int]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for diagnostic in diagnostic_files:
        path = str(diagnostic.get("path", ""))
        text = diagnostic.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        text_summary = _parse_text_diagnostic(path, text)
        if text_summary is not None:
            summaries.append(text_summary)
            continue
        if _diagnostic_kind(path) == "code-saturne-error":
            continue
        if path.lower().endswith("/run_solver.log"):
            continue

        header: list[str] | None = None
        rows: list[dict[str, float]] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            parts = _split_table_line(stripped)
            if not parts:
                continue
            numbers = [_float_or_none(part) for part in parts]
            if any(value is None for value in numbers):
                if header is None and _is_table_header(parts, numbers):
                    header = parts
                    continue
                if header is None or len(parts) != len(header):
                    continue
                row = {
                    column: value
                    for column, value in zip(header, numbers, strict=False)
                    if value is not None
                }
                if len(row) >= 2:
                    rows.append(row)
                continue
            if header is None:
                header = [f"c{index}" for index in range(len(parts))]
            if len(parts) != len(header):
                continue
            row = {
                column: value
                for column, value in zip(header, numbers, strict=False)
                if value is not None
            }
            if len(row) < 2:
                continue
            rows.append(row)

        if not rows:
            continue
        latest = rows[-1]
        summaries.append(
            {
                "path": path,
                "kind": _diagnostic_kind(path),
                "columns": list(latest.keys()),
                "rowCount": len(rows),
                "latest": latest,
            }
        )
    return summaries


def _parse_text_diagnostic(path: str, text: str) -> dict[str, object] | None:
    kind = _diagnostic_kind(path)
    if kind == "mujoco-summary":
        return _parse_mujoco_summary(path, text)
    if kind == "forces":
        return _parse_openfoam_forces(path, text)
    if kind != "code-saturne-error":
        return None

    excerpts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if (
            "fatal error" in lowered
            or "definition error" in lowered
            or "boundary condition type" in lowered
            or (lowered.startswith("error") and not lowered.startswith("error detected"))
        ):
            excerpts.append(line)
        if len(excerpts) >= 6:
            break

    if not excerpts:
        return None
    return {
        "path": path,
        "kind": kind,
        "lineCount": len(text.splitlines()),
        "excerpts": excerpts,
    }


def _parse_mujoco_summary(path: str, text: str) -> dict[str, object] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("solver") != "mujoco":
        return None

    latest: dict[str, float] = {}
    steps = payload.get("steps")
    if isinstance(steps, (int, float)):
        latest["steps"] = float(steps)
    final = payload.get("final")
    if isinstance(final, dict):
        for key in ("step", "time", "passiveForceNorm"):
            value = final.get(key)
            if isinstance(value, (int, float)):
                latest[key] = float(value)
        for vector_key in ("position", "velocity"):
            vector = final.get(vector_key)
            if not isinstance(vector, list):
                continue
            for index, value in enumerate(vector[:3]):
                if isinstance(value, (int, float)):
                    latest[f"{vector_key}{index}"] = float(value)

    summary: dict[str, object] = {
        "path": path,
        "kind": "mujoco-summary",
        "lineCount": len(text.splitlines()),
    }
    if latest:
        summary["columns"] = list(latest.keys())
        summary["rowCount"] = 1
        summary["latest"] = latest
    note = payload.get("note")
    if isinstance(note, str) and note:
        summary["excerpts"] = [note]
    return summary


def _parse_openfoam_forces(path: str, text: str) -> dict[str, object] | None:
    rows: list[dict[str, float]] = []
    columns = [
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
    ]
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        values = [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", stripped)]
        if len(values) < len(columns):
            continue
        rows.append({column: value for column, value in zip(columns, values[: len(columns)], strict=True)})
    if not rows:
        return None
    return {
        "path": path,
        "kind": "forces",
        "columns": columns,
        "rowCount": len(rows),
        "latest": rows[-1],
    }


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_case_manifest(case: SolverCase) -> list[str]:
    manifest_text = case.files.get(CASE_MANIFEST_PATH)
    if not manifest_text:
        return [f"Generated case is missing `{CASE_MANIFEST_PATH}`."]
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        return [f"`{CASE_MANIFEST_PATH}` is not valid JSON: {exc}."]

    issues: list[str] = []
    if manifest.get("schema") != "flowlab.case_manifest.v1":
        issues.append(f"`{CASE_MANIFEST_PATH}` has an unsupported schema.")
    for key, expected in {
        "projectName": case.projectName,
        "solver": case.solver,
        "advancedMode": case.advancedMode,
        "status": case.status,
        "runCommand": case.runCommand,
    }.items():
        if manifest.get(key) != expected:
            issues.append(f"`{CASE_MANIFEST_PATH}` {key} does not match generated case.")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        issues.append(f"`{CASE_MANIFEST_PATH}` files entry must be an object.")
        return issues

    generated_paths = sorted(path for path in case.files if path != CASE_MANIFEST_PATH)
    if manifest.get("fileCount") != len(generated_paths):
        issues.append(f"`{CASE_MANIFEST_PATH}` fileCount does not match generated files.")
    for path in generated_paths:
        entry = manifest_files.get(path)
        if not isinstance(entry, dict):
            issues.append(f"`{CASE_MANIFEST_PATH}` is missing generated file `{path}`.")
            continue
        content = case.files[path]
        size = len(content.encode("utf-8"))
        digest = _sha256_text(content)
        if entry.get("size") != size:
            issues.append(f"`{CASE_MANIFEST_PATH}` size mismatch for `{path}`.")
        if entry.get("sha256") != digest:
            issues.append(f"`{CASE_MANIFEST_PATH}` SHA-256 mismatch for `{path}`.")
    extra_paths = sorted(set(manifest_files) - set(generated_paths))
    for path in extra_paths:
        issues.append(f"`{CASE_MANIFEST_PATH}` references unknown file `{path}`.")
    return issues


def parse_solver_logs(solver: str, logs: list[str]) -> dict[str, object]:
    summary: dict[str, object] = {
        "solver": solver,
        "lineCount": len(logs),
        "lastLines": logs[-5:],
    }
    residuals: dict[str, dict[str, float | int]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    times: list[float] = []
    iterations: list[int] = []
    openfoam_checkmesh: dict[str, object] = {}
    openfoam_checkmesh_counts: dict[str, int] = {}
    openfoam_region_checkmeshes: dict[str, dict[str, object]] = {}
    openfoam_region_counts: dict[str, dict[str, int]] = {}
    openfoam_current_region: str | None = None

    for line in logs:
        lower = line.lower()
        if "warning" in lower:
            warnings.append(line)
        if _is_solver_error_log_line(solver, line):
            errors.append(line)

        if solver == "openfoam":
            time_match = OPENFOAM_TIME_RE.search(line)
            if time_match:
                parsed_time = _float_or_none(time_match.group(1))
                if parsed_time is not None:
                    times.append(parsed_time)
            residual_match = OPENFOAM_RESIDUAL_RE.search(line)
            if residual_match:
                field = residual_match.group(1).strip()
                initial = _float_or_none(residual_match.group(2))
                final = _float_or_none(residual_match.group(3))
                count = int(residual_match.group(4))
                if initial is not None and final is not None:
                    residuals[field] = {"initial": initial, "final": final, "iterations": count}
            region_match = OPENFOAM_CHECKMESH_REGION_RE.search(line)
            if region_match:
                openfoam_current_region = region_match.group(1)
                openfoam_region_checkmeshes.setdefault(openfoam_current_region, {})
                openfoam_region_counts.setdefault(openfoam_current_region, {})
            checkmesh_target = (
                openfoam_region_checkmeshes.setdefault(openfoam_current_region, {})
                if openfoam_current_region
                else openfoam_checkmesh
            )
            checkmesh_counts = (
                openfoam_region_counts.setdefault(openfoam_current_region, {})
                if openfoam_current_region
                else openfoam_checkmesh_counts
            )
            count_match = OPENFOAM_CHECKMESH_COUNT_RE.search(line)
            if count_match:
                count_key = count_match.group(1).lower().replace(" ", "_")
                checkmesh_counts[count_key] = int(count_match.group(2))
            aspect_match = OPENFOAM_CHECKMESH_ASPECT_RE.search(line)
            if aspect_match:
                parsed_aspect = _float_or_none(aspect_match.group(1))
                if parsed_aspect is not None:
                    checkmesh_target["maxAspectRatio"] = parsed_aspect
            nonorth_match = OPENFOAM_CHECKMESH_NONORTH_RE.search(line)
            if nonorth_match:
                max_nonorth = _float_or_none(nonorth_match.group(1))
                avg_nonorth = _float_or_none(nonorth_match.group(2))
                if max_nonorth is not None:
                    checkmesh_target["maxNonOrthogonality"] = max_nonorth
                if avg_nonorth is not None:
                    checkmesh_target["averageNonOrthogonality"] = avg_nonorth
            skew_match = OPENFOAM_CHECKMESH_SKEW_RE.search(line)
            if skew_match:
                parsed_skew = _float_or_none(skew_match.group(1))
                if parsed_skew is not None:
                    checkmesh_target["maxSkewness"] = parsed_skew
            min_volume_match = OPENFOAM_CHECKMESH_MIN_VOLUME_RE.search(line)
            if min_volume_match:
                parsed_min_volume = _float_or_none(min_volume_match.group(1))
                if parsed_min_volume is not None:
                    checkmesh_target["minVolume"] = parsed_min_volume
            failed_match = OPENFOAM_CHECKMESH_FAILED_RE.search(line)
            if failed_match:
                failed_count = int(failed_match.group(1))
                checkmesh_target["failedChecks"] = failed_count
                checkmesh_target["completed"] = True
                checkmesh_target["passed"] = failed_count == 0
            if "mesh ok" in lower:
                checkmesh_target["completed"] = True
                checkmesh_target["passed"] = True
                checkmesh_target.setdefault("failedChecks", 0)

        elif solver == "su2":
            if not line.lstrip().startswith("|"):
                continue
            iteration_match = SU2_ITER_RE.search(line)
            numbers = [_float_or_none(value) for value in FLOAT_RE.findall(line)]
            numeric_values = [value for value in numbers if value is not None]
            if iteration_match and numeric_values:
                iteration = int(iteration_match.group(1))
                iterations.append(iteration)
                if len(numeric_values) >= 2:
                    residuals["su2_primary"] = {"initial": numeric_values[1], "final": numeric_values[-1], "iterations": iteration}

        elif solver == "code-saturne":
            iteration_match = CODE_SATURNE_ITER_RE.search(line)
            if iteration_match:
                iterations.append(int(iteration_match.group(1)))
            if "residual" in lower:
                numbers = [_float_or_none(value) for value in FLOAT_RE.findall(line)]
                numeric_values = [value for value in numbers if value is not None]
                if numeric_values:
                    residuals["code_saturne_residual"] = {
                        "initial": numeric_values[0],
                        "final": numeric_values[-1],
                        "iterations": iterations[-1] if iterations else 0,
                    }

        elif solver == "mujoco":
            step_match = MUJOCO_STEP_RE.search(line)
            if step_match:
                iterations.append(int(next(group for group in step_match.groups() if group is not None)))

    if times:
        summary["timeSteps"] = times
        summary["latestTime"] = times[-1]
    if iterations:
        summary["iterations"] = iterations
        summary["latestIteration"] = iterations[-1]
    if residuals:
        summary["residuals"] = residuals
    if solver == "openfoam" and (openfoam_checkmesh or openfoam_checkmesh_counts):
        if openfoam_checkmesh_counts:
            openfoam_checkmesh["counts"] = openfoam_checkmesh_counts
        summary["checkMesh"] = openfoam_checkmesh
    if solver == "openfoam" and (openfoam_region_checkmeshes or openfoam_region_counts):
        for region, counts in openfoam_region_counts.items():
            if counts:
                openfoam_region_checkmeshes.setdefault(region, {})["counts"] = counts
        summary["checkMeshRegions"] = openfoam_region_checkmeshes
    if warnings:
        summary["warnings"] = warnings[-10:]
    if errors:
        summary["errors"] = errors[-10:]
    return summary


def _is_solver_error_log_line(solver: str, line: str) -> bool:
    lower = line.lower()
    if solver == "openfoam":
        if (
            "time step continuity errors" in lower
            or "cumulative continuity errors" in lower
            or "enabling floating point exception trapping" in lower
        ):
            return False
        return (
            "foam fatal" in lower
            or "fatal io error" in lower
            or "fatal error" in lower
            or "floating point exception" in lower
            or lower.startswith("error:")
            or " error:" in lower
        )
    return "error" in lower or "fatal" in lower


def solver_output_quality_error(solver: str, logs: list[str], result_files: list[dict[str, str | int]]) -> str | None:
    if solver != "openfoam":
        return None

    for line in logs:
        lower = line.lower()
        if "foam fatal" in lower or "fatal io error" in lower or "fatal error" in lower:
            return "OpenFOAM reported a fatal error in solver or post-processing logs."
        failed_match = OPENFOAM_CHECKMESH_FAILED_RE.search(line)
        if failed_match and int(failed_match.group(1)) > 0:
            return "OpenFOAM checkMesh reported failed mesh checks."
        if failed_match:
            continue
        if "failed" in lower and "mesh check" in lower:
            return "OpenFOAM checkMesh reported failed mesh checks."
        if "mesh check failed" in lower or "checkmesh failed" in lower:
            return "OpenFOAM checkMesh reported failed mesh checks."
        if NAN_TOKEN_RE.search(line):
            return "OpenFOAM produced NaN values in solver logs."

    for result in result_files:
        text = result.get("text")
        if isinstance(text, str) and NAN_TOKEN_RE.search(text):
            return f"OpenFOAM produced NaN values in result file `{result.get('path', 'unknown')}`."
        # Oversized results are verified from disk rather than embedded, so their
        # NaN verdict arrives as a flag instead of inline text.
        if result.get("nanDetected") is True:
            return f"OpenFOAM produced NaN values in result file `{result.get('path', 'unknown')}`."

    # A result counts as parseable when its fields were confirmed, whether it was
    # small enough to embed or was verified on disk because it exceeded the
    # embedding cap. The guarantee this preserves is unchanged: an exit-zero job
    # is only complete once real field data has actually been parsed.
    parseable_results = [
        result
        for result in result_files
        if isinstance(result.get("fieldSummary"), dict)
        and result.get("fieldSummary", {}).get("fields")
        and (isinstance(result.get("text"), str) or result.get("verifiedOnDisk") is True)
    ]
    if not parseable_results:
        skipped = [str(result.get("skipped")) for result in result_files if result.get("skipped")]
        detail = f" {'; '.join(skipped[:3])}" if skipped else ""
        return "OpenFOAM completed but no parseable VTK/VTU or native time-directory field results were surfaced." + detail

    return None


def _finite_json_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _safe_relative_stl_path(path_value: Any) -> bool:
    if not isinstance(path_value, str):
        return False
    candidate = path_value.strip()
    if not candidate or "\x00" in candidate:
        return False
    path = Path(candidate)
    if path.is_absolute() or path.suffix.lower() != ".stl":
        return False
    return not any(part in {"", ".", ".."} for part in path.parts)


def _ascii_stl_is_sane(text: Any) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    if "solid" not in lowered or "facet normal" not in lowered or "vertex" not in lowered:
        return False
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _reviewed_boundary_tag_status(starter_geometry: dict[str, Any]) -> tuple[bool, list[str], list[dict[str, Any]]]:
    validation = starter_geometry.get("boundaryTagValidation") if isinstance(starter_geometry.get("boundaryTagValidation"), dict) else {}
    missing = [str(item) for item in validation.get("missingRequiredRoles", []) if isinstance(item, str)]
    tags = [tag for tag in validation.get("tags", []) if isinstance(tag, dict)]
    return validation.get("complete") is True and not missing, missing, tags


def _reviewed_surface_status(starter_geometry: dict[str, Any]) -> tuple[bool, list[str], list[dict[str, Any]], list[str]]:
    surfaces = starter_geometry.get("surfaces") if isinstance(starter_geometry.get("surfaces"), list) else []
    surfaces = [surface for surface in surfaces if isinstance(surface, dict)]
    coverage = starter_geometry.get("surfaceCoverage") if isinstance(starter_geometry.get("surfaceCoverage"), dict) else {}
    if not coverage:
        coverage = starter_geometry.get("boundaryCoverage") if isinstance(starter_geometry.get("boundaryCoverage"), dict) else {}
    missing = [str(item) for item in coverage.get("missingRequiredRoles", []) if isinstance(item, str)]
    if surfaces and not coverage:
        reviewed_roles = {
            str(surface.get("role"))
            for surface in surfaces
            if surface.get("cadReviewed") is True and isinstance(surface.get("role"), str)
        }
        missing = sorted({"inlet", "outlet", "wall"} - reviewed_roles)
    required_patches = [str(item) for item in coverage.get("requiredPatchNames", []) if isinstance(item, str)]
    if not required_patches:
        required_patches = [
            str(surface.get("patchName"))
            for surface in surfaces
            if surface.get("cadReviewed") is True
            and surface.get("role") in {"inlet", "outlet", "wall"}
            and isinstance(surface.get("patchName"), str)
            and surface.get("patchName")
        ]
    complete = coverage.get("complete") is True if coverage else bool(surfaces) and not missing
    return complete and not missing, missing, surfaces, required_patches


def _field_defines_patch(field_text: str, patch_name: str) -> bool:
    return re.search(rf"(^|\n)\s*{re.escape(patch_name)}\s*\n\s*\{{", field_text) is not None


def _openfoam_required_surface_bc_fields(files: dict[str, str]) -> list[str]:
    fields = [field for field in ("0/U", "0/p", "0/p_rgh", "0/T") if field in files]
    fields.extend(sorted(path for path in files if path.startswith("0/alpha.") and path not in fields))
    return fields


def _openfoam_metric_patch_expectations(
    files: dict[str, str],
    surface_geometry: dict[str, Any],
) -> tuple[dict[str, list[str]], bool]:
    try:
        y_junction_profile = json.loads(
            files.get("constant/flowlab_y_junction_profile.json", "")
        )
    except json.JSONDecodeError:
        y_junction_profile = {}
    if (
        isinstance(y_junction_profile, dict)
        and y_junction_profile.get("schema") == "flowlab.y-junction-profile.v1"
    ):
        return {
            "inlet": ["inlet"],
            "outlet": ["outletUpper", "outletLower"],
            "wall": ["walls"],
        }, False
    _, _, reviewed_surfaces, _ = _reviewed_surface_status(surface_geometry)
    if reviewed_surfaces:
        inlet = [
            str(surface.get("patchName"))
            for surface in reviewed_surfaces
            if surface.get("role") == "inlet" and isinstance(surface.get("patchName"), str) and surface.get("patchName")
        ]
        outlet = [
            str(surface.get("patchName"))
            for surface in reviewed_surfaces
            if surface.get("role") == "outlet" and isinstance(surface.get("patchName"), str) and surface.get("patchName")
        ]
        wall = [
            str(surface.get("patchName"))
            for surface in reviewed_surfaces
            if surface.get("role") == "wall" and isinstance(surface.get("patchName"), str) and surface.get("patchName")
        ]
    else:
        inlet = ["inlet"]
        outlet = ["outlet"]
        wall = ["walls"]
    try:
        project = json.loads(files.get("flowlab_project.json", "{}"))
    except json.JSONDecodeError:
        project = {}
    nodes = project.get("nodes") if isinstance(project, dict) else {}
    if isinstance(nodes, dict):
        node_values = nodes.values()
    elif isinstance(nodes, list):
        node_values = nodes
    else:
        node_values = []
    has_probe_nodes = any(isinstance(node, dict) and node.get("type") == "probe" for node in node_values)
    return {"inlet": inlet or ["inlet"], "outlet": outlet or ["outlet"], "wall": wall or ["walls"]}, has_probe_nodes


def _control_dict_function_block(control_dict: str, name: str) -> str | None:
    match = re.search(rf"(^|\n)\s*{re.escape(name)}\s*\n\s*\{{", control_dict)
    if not match:
        return None
    start = control_dict.find("{", match.end() - 1)
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(control_dict)):
        char = control_dict[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return control_dict[start : index + 1]
    return None


def validate_solver_case(case: SolverCase) -> list[str]:
    if is_validated_open_boundary_case(case):
        return validate_validated_open_boundary_case(case)
    issues: list[str] = validate_case_manifest(case)
    files = case.files
    for path in REQUIRED_CASE_FILES.get(case.solver, ()):
        if path not in files:
            issues.append(f"Missing required {case.solver} case file `{path}`.")

    quality_text = files.get("mesh/quality.json")
    if quality_text is not None:
        try:
            mesh_quality = json.loads(quality_text)
        except json.JSONDecodeError:
            issues.append("`mesh/quality.json` is not valid JSON.")
        else:
            if mesh_quality.get("schema") != "flowlab.mesh_quality.v1":
                issues.append("`mesh/quality.json` has an unsupported schema.")
            summary = mesh_quality.get("summary")
            thresholds = mesh_quality.get("thresholds")
            if not isinstance(summary, dict):
                issues.append("`mesh/quality.json` must include a summary object.")
                summary = {}
            if not isinstance(thresholds, dict):
                issues.append("`mesh/quality.json` must include a thresholds object.")
                thresholds = {}
            for field in ("maxNonOrthogonalityDeg", "maxSkewnessEstimate"):
                metric = summary.get(field)
                threshold = thresholds.get(field)
                if not _finite_json_number(metric):
                    issues.append(f"`mesh/quality.json` summary must include numeric `{field}`.")
                if not _finite_json_number(threshold):
                    issues.append(f"`mesh/quality.json` thresholds must include numeric `{field}`.")
                if _finite_json_number(metric) and _finite_json_number(threshold) and float(metric) > float(threshold):
                    warnings = mesh_quality.get("warnings")
                    if mesh_quality.get("status") == "ok":
                        issues.append(f"`mesh/quality.json` status cannot be ok when `{field}` exceeds its threshold.")
                    if not isinstance(warnings, list) or not warnings:
                        issues.append(f"`mesh/quality.json` must include warnings when `{field}` exceeds its threshold.")
            if mesh_quality.get("status") == "failed":
                warnings = mesh_quality.get("warnings")
                issue_detail = "; ".join(str(item) for item in warnings) if isinstance(warnings, list) else "mesh quality failed"
                issues.append(f"Mesh quality check failed: {issue_detail}")

    boundary_layer_text = files.get("mesh/boundary_layer_plan.json")
    if boundary_layer_text is not None:
        try:
            boundary_layer_plan = json.loads(boundary_layer_text)
        except json.JSONDecodeError:
            issues.append("`mesh/boundary_layer_plan.json` is not valid JSON.")
        else:
            if boundary_layer_plan.get("schema") != "flowlab.boundary_layer_plan.v1":
                issues.append("`mesh/boundary_layer_plan.json` has an unsupported schema.")
            edge_plans = boundary_layer_plan.get("edges")
            if not isinstance(edge_plans, list):
                issues.append("`mesh/boundary_layer_plan.json` must include edge sizing entries.")
            if boundary_layer_plan.get("productionReady") is not False:
                issues.append("`mesh/boundary_layer_plan.json` must remain productionReady=false until solver-native y-plus evidence exists.")

    prism_layer_text = files.get("mesh/prism_layer_plan.json")
    if prism_layer_text is not None:
        try:
            prism_layer_plan = json.loads(prism_layer_text)
        except json.JSONDecodeError:
            issues.append("`mesh/prism_layer_plan.json` is not valid JSON.")
        else:
            if prism_layer_plan.get("schema") != "flowlab.prism_layer_plan.v1":
                issues.append("`mesh/prism_layer_plan.json` has an unsupported schema.")
            edge_plans = prism_layer_plan.get("edges")
            if not isinstance(edge_plans, list):
                issues.append("`mesh/prism_layer_plan.json` must include edge prism-layer entries.")
            if prism_layer_plan.get("productionReady") is not False:
                issues.append("`mesh/prism_layer_plan.json` must remain productionReady=false until native prism-layer mesh evidence exists.")
            checks = prism_layer_plan.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/prism_layer_plan.json` must include readinessChecks.")
            if prism_layer_plan.get("productionReady") is False and not prism_layer_plan.get("blockingReasons"):
                issues.append("`mesh/prism_layer_plan.json` must list blockingReasons while productionReady=false.")

    production_mesh_text = files.get("mesh/production_mesh_plan.json")
    if production_mesh_text is not None:
        try:
            production_mesh_plan = json.loads(production_mesh_text)
        except json.JSONDecodeError:
            issues.append("`mesh/production_mesh_plan.json` is not valid JSON.")
        else:
            if production_mesh_plan.get("schema") != "flowlab.production_mesh_plan.v1":
                issues.append("`mesh/production_mesh_plan.json` has an unsupported schema.")
            checks = production_mesh_plan.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/production_mesh_plan.json` must include readinessChecks.")
            if production_mesh_plan.get("productionReady") is not False:
                issues.append("`mesh/production_mesh_plan.json` must remain productionReady=false until CAD-quality 3D mesh evidence exists.")
            if production_mesh_plan.get("productionReady") is False and not production_mesh_plan.get("blockingReasons"):
                issues.append("`mesh/production_mesh_plan.json` must list blockingReasons while productionReady=false.")
            required_mesh_plan_artifacts = {
                "mesh/native_meshing_plan.json",
                "mesh/gmsh_production_handoff.geo",
                "mesh/prism_layer_plan.json",
                "mesh/adaptation_plan.json",
                "mesh/physical_groups.json",
                "mesh/openfoam_snappy_handoff.json",
                "mesh/openfoam_native_mesh_preflight.py",
                "mesh/su2_native_meshing_handoff.json",
                "mesh/code_saturne_native_meshing_handoff.json",
                "mesh/openfoam_snappyHexMeshDict.template",
                "mesh/openfoam_surfaceFeatureExtractDict.template",
                "mesh/openfoam_meshQualityDict.template",
                "constant/triSurface/reviewedFlowLabSurfaces.stl",
                "system/snappyHexMeshDict",
                "system/surfaceFeatureExtractDict",
                "system/meshQualityDict",
                "mesh/production_mesh_acceptance.json",
            }
            if not required_mesh_plan_artifacts.issubset(files):
                issues.append("Generated production mesh plans must include native meshing handoff JSON, prism-layer plan JSON, adaptation plan JSON, physical group map JSON, production mesh acceptance checklist JSON, OpenFOAM/SU2/Code_Saturne native meshing handoff artifacts, OpenFOAM native mesh preflight script, OpenFOAM snappy handoff JSON/templates, installed OpenFOAM starter surface/dictionaries, and Gmsh .geo artifacts.")

    adaptation_text = files.get("mesh/adaptation_plan.json")
    if adaptation_text is not None:
        try:
            adaptation_plan = json.loads(adaptation_text)
        except json.JSONDecodeError:
            issues.append("`mesh/adaptation_plan.json` is not valid JSON.")
        else:
            if adaptation_plan.get("schema") != "flowlab.mesh_adaptation_plan.v1":
                issues.append("`mesh/adaptation_plan.json` has an unsupported schema.")
            if adaptation_plan.get("productionReady") is not False:
                issues.append("`mesh/adaptation_plan.json` must remain productionReady=false until native adapted mesh evidence exists.")
            targets = adaptation_plan.get("adaptationTargets")
            if not isinstance(targets, list) or not targets:
                issues.append("`mesh/adaptation_plan.json` must include adaptationTargets.")
            else:
                for target in targets:
                    if not isinstance(target, dict):
                        issues.append("`mesh/adaptation_plan.json` adaptationTargets must be objects.")
                        break
                    if not target.get("edgeId") or not isinstance(target.get("fieldIndicatorTargets"), list) or not target.get("fieldIndicatorTargets"):
                        issues.append("`mesh/adaptation_plan.json` targets must include edgeId and fieldIndicatorTargets.")
                        break
                    boundary_targets = target.get("boundaryLayerTargets") if isinstance(target.get("boundaryLayerTargets"), dict) else {}
                    geometry_targets = target.get("geometryTargets") if isinstance(target.get("geometryTargets"), dict) else {}
                    if "enabled" not in boundary_targets or "enabled" not in geometry_targets:
                        issues.append("`mesh/adaptation_plan.json` targets must include geometryTargets and boundaryLayerTargets enabled flags.")
                        break
            checks = adaptation_plan.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/adaptation_plan.json` must include readinessChecks.")
            elif not any(isinstance(check, dict) and check.get("status") == "fail" for check in checks):
                issues.append("`mesh/adaptation_plan.json` must include failing native-adaptation readiness checks while productionReady=false.")
            source_artifacts = adaptation_plan.get("sourceArtifacts") if isinstance(adaptation_plan.get("sourceArtifacts"), dict) else {}
            expected_adaptation_sources = {
                "quality": "mesh/quality.json",
                "refinementPlan": "mesh/refinement_plan.json",
                "boundaryLayerPlan": "mesh/boundary_layer_plan.json",
                "prismLayerPlan": "mesh/prism_layer_plan.json",
                "physicalGroups": "mesh/physical_groups.json",
            }
            for key, expected_path in expected_adaptation_sources.items():
                if source_artifacts.get(key) != expected_path:
                    issues.append("`mesh/adaptation_plan.json` must reference generated mesh source artifacts.")
                    break
            if adaptation_plan.get("productionReady") is False and not adaptation_plan.get("blockingReasons"):
                issues.append("`mesh/adaptation_plan.json` must list blockingReasons while productionReady=false.")

    production_acceptance_text = files.get("mesh/production_mesh_acceptance.json")
    if production_acceptance_text is not None:
        try:
            production_acceptance = json.loads(production_acceptance_text)
        except json.JSONDecodeError:
            issues.append("`mesh/production_mesh_acceptance.json` is not valid JSON.")
        else:
            if production_acceptance.get("schema") != "flowlab.production_mesh_acceptance.v1":
                issues.append("`mesh/production_mesh_acceptance.json` has an unsupported schema.")
            if production_acceptance.get("productionReady") is not False:
                issues.append("`mesh/production_mesh_acceptance.json` must remain productionReady=false until CAD/native mesh acceptance evidence exists.")
            criteria = production_acceptance.get("acceptanceCriteria")
            if not isinstance(criteria, list) or not criteria:
                issues.append("`mesh/production_mesh_acceptance.json` must include acceptanceCriteria.")
            elif not any(isinstance(item, dict) and item.get("status") == "fail" for item in criteria):
                issues.append("`mesh/production_mesh_acceptance.json` must include at least one failing acceptance criterion while productionReady=false.")
            solver_acceptance = production_acceptance.get("solverAcceptance")
            if not isinstance(solver_acceptance, dict) or not {"openfoam", "su2", "codeSaturne"}.issubset(solver_acceptance):
                issues.append("`mesh/production_mesh_acceptance.json` must include OpenFOAM, SU2, and Code_Saturne solver acceptance entries.")
            else:
                for solver_key in ("openfoam", "su2", "codeSaturne"):
                    entry = solver_acceptance.get(solver_key) if isinstance(solver_acceptance.get(solver_key), dict) else {}
                    if entry.get("status") != "blocked":
                        issues.append("`mesh/production_mesh_acceptance.json` solver acceptance entries must remain blocked.")
                        break
                    if not isinstance(entry.get("requiredEvidence"), list) or not entry.get("requiredEvidence"):
                        issues.append("`mesh/production_mesh_acceptance.json` solver acceptance entries must list requiredEvidence.")
                        break
                    if not isinstance(entry.get("currentEvidence"), list):
                        issues.append("`mesh/production_mesh_acceptance.json` solver acceptance entries must list currentEvidence.")
                        break
            native_quality = production_acceptance.get("nativeQualityEvidence") if isinstance(production_acceptance.get("nativeQualityEvidence"), dict) else {}
            if not native_quality:
                issues.append("`mesh/production_mesh_acceptance.json` must include nativeQualityEvidence.")
            else:
                if native_quality.get("schema") != "flowlab.native_mesh_quality_evidence.v1":
                    issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence has an unsupported schema.")
                if native_quality.get("productionReady") is not False:
                    issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence must remain productionReady=false.")
                if native_quality.get("status") != "missing-native-quality-reports":
                    issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence must report missing native quality reports.")
                shared_evidence = native_quality.get("sharedRequiredEvidence")
                if not isinstance(shared_evidence, list) or not {"solver-native cell-quality report", "wall-distance or y-plus field for wall-bounded cases"}.issubset(set(shared_evidence)):
                    issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence must list solver-native cell-quality and y-plus evidence.")
                solver_reports = native_quality.get("solverReports")
                if not isinstance(solver_reports, dict) or not {"openfoam", "su2", "codeSaturne"}.issubset(solver_reports):
                    issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence must include OpenFOAM, SU2, and Code_Saturne reports.")
                else:
                    for solver_key in ("openfoam", "su2", "codeSaturne"):
                        report = solver_reports.get(solver_key) if isinstance(solver_reports.get(solver_key), dict) else {}
                        required_metrics = report.get("requiredMetrics")
                        if report.get("status") != "missing":
                            issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence solver reports must remain missing.")
                            break
                        if not isinstance(report.get("commands"), list) or not report.get("commands"):
                            issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence solver reports must list commands.")
                            break
                        if not isinstance(required_metrics, list) or not required_metrics:
                            issues.append("`mesh/production_mesh_acceptance.json` nativeQualityEvidence solver reports must list requiredMetrics.")
                            break
                    openfoam_metrics = solver_reports.get("openfoam", {}).get("requiredMetrics") if isinstance(solver_reports.get("openfoam"), dict) else []
                    if isinstance(openfoam_metrics, list) and "yPlusMinMeanMax" not in openfoam_metrics:
                        issues.append("`mesh/production_mesh_acceptance.json` OpenFOAM native quality evidence must require y-plus metrics.")
                    openfoam_evidence = solver_reports.get("openfoam", {}).get("currentEvidence") if isinstance(solver_reports.get("openfoam"), dict) else []
                    if isinstance(openfoam_evidence, list) and not {
                        "constant/triSurface/reviewedFlowLabSurfaces.stl",
                        "system/snappyHexMeshDict",
                        "system/surfaceFeatureExtractDict",
                        "system/meshQualityDict",
                    }.issubset(set(openfoam_evidence)):
                        issues.append("`mesh/production_mesh_acceptance.json` OpenFOAM native quality evidence must list generated starter surface and installed dictionary artifacts.")
            source_artifacts = production_acceptance.get("sourceArtifacts") if isinstance(production_acceptance.get("sourceArtifacts"), dict) else {}
            expected_sources = {
                "productionMeshPlan": "mesh/production_mesh_plan.json",
                "nativeMeshingPlan": "mesh/native_meshing_plan.json",
                "physicalGroups": "mesh/physical_groups.json",
                "prismLayerPlan": "mesh/prism_layer_plan.json",
                "adaptationPlan": "mesh/adaptation_plan.json",
                "openfoamSnappyHandoff": "mesh/openfoam_snappy_handoff.json",
                "openfoamNativeMeshPreflight": "mesh/openfoam_native_mesh_preflight.py",
                "su2NativeMeshingHandoff": "mesh/su2_native_meshing_handoff.json",
                "codeSaturneNativeMeshingHandoff": "mesh/code_saturne_native_meshing_handoff.json",
            }
            for key, expected_path in expected_sources.items():
                if source_artifacts.get(key) != expected_path:
                    issues.append("`mesh/production_mesh_acceptance.json` must reference generated production mesh source artifacts.")
                    break
            if production_acceptance.get("productionReady") is False and not production_acceptance.get("blockingReasons"):
                issues.append("`mesh/production_mesh_acceptance.json` must list blockingReasons while productionReady=false.")

    physical_groups_text = files.get("mesh/physical_groups.json")
    if physical_groups_text is not None:
        try:
            physical_groups = json.loads(physical_groups_text)
        except json.JSONDecodeError:
            issues.append("`mesh/physical_groups.json` is not valid JSON.")
        else:
            if physical_groups.get("schema") != "flowlab.physical_group_map.v1":
                issues.append("`mesh/physical_groups.json` has an unsupported schema.")
            if physical_groups.get("productionReady") is not False:
                issues.append("`mesh/physical_groups.json` must remain productionReady=false until native CAD-quality mesh evidence exists.")
            groups = physical_groups.get("groups")
            if not isinstance(groups, list) or not groups:
                issues.append("`mesh/physical_groups.json` must include physical groups.")
            else:
                dimensions = {group.get("dimension") for group in groups if isinstance(group, dict)}
                roles = {group.get("role") for group in groups if isinstance(group, dict)}
                if 2 not in dimensions or 3 not in dimensions:
                    issues.append("`mesh/physical_groups.json` must include both boundary and volume groups.")
                if not {"inlet", "outlet", "wall", "front-back"}.issubset(roles):
                    issues.append("`mesh/physical_groups.json` must include inlet, outlet, wall, and front-back boundary roles.")
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    solver_names = group.get("solverNames")
                    if not isinstance(solver_names, dict) or not {"gmsh", "codeSaturne", "openfoam"}.issubset(solver_names):
                        issues.append("`mesh/physical_groups.json` groups must include gmsh, Code_Saturne, and OpenFOAM solver names.")
                        break
            solver_targets = physical_groups.get("solverTargets") if isinstance(physical_groups.get("solverTargets"), dict) else {}
            if not {"gmsh", "su2", "codeSaturne", "openfoam"}.issubset(solver_targets):
                issues.append("`mesh/physical_groups.json` must include solver target mappings for Gmsh, SU2, Code_Saturne, and OpenFOAM.")

    openfoam_starter_geometry: dict[str, Any] = {}
    openfoam_reviewed_surface_geometry: dict[str, Any] = {}
    openfoam_boundary_condition_coverage: dict[str, Any] = {}
    openfoam_snappy_text = files.get("mesh/openfoam_snappy_handoff.json")
    if openfoam_snappy_text is not None:
        try:
            openfoam_snappy = json.loads(openfoam_snappy_text)
        except json.JSONDecodeError:
            issues.append("`mesh/openfoam_snappy_handoff.json` is not valid JSON.")
        else:
            if openfoam_snappy.get("schema") != "flowlab.openfoam_snappy_handoff.v1":
                issues.append("`mesh/openfoam_snappy_handoff.json` has an unsupported schema.")
            if openfoam_snappy.get("productionReady") is not False:
                issues.append("`mesh/openfoam_snappy_handoff.json` must remain productionReady=false until native snappyHexMesh evidence exists.")
            layers = openfoam_snappy.get("addLayersControls", {}).get("layers") if isinstance(openfoam_snappy.get("addLayersControls"), dict) else None
            if not isinstance(layers, list):
                issues.append("`mesh/openfoam_snappy_handoff.json` must include addLayersControls.layers.")
            boundary_patch_plan = openfoam_snappy.get("boundaryPatchPlan") if isinstance(openfoam_snappy.get("boundaryPatchPlan"), dict) else {}
            if not {"inlet", "outlet", "walls", "frontAndBack"}.issubset(boundary_patch_plan):
                issues.append("`mesh/openfoam_snappy_handoff.json` must include inlet, outlet, walls, and frontAndBack patch plans.")
            checks = openfoam_snappy.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/openfoam_snappy_handoff.json` must include readinessChecks.")
            if openfoam_snappy.get("productionReady") is False and not openfoam_snappy.get("blockingReasons"):
                issues.append("`mesh/openfoam_snappy_handoff.json` must list blockingReasons while productionReady=false.")
            openfoam_boundary_condition_coverage = (
                openfoam_snappy.get("boundaryConditionCoverage")
                if isinstance(openfoam_snappy.get("boundaryConditionCoverage"), dict)
                else {}
            )
            template_artifacts = openfoam_snappy.get("templateArtifacts") if isinstance(openfoam_snappy.get("templateArtifacts"), dict) else {}
            for required_template in (
                "mesh/openfoam_snappyHexMeshDict.template",
                "mesh/openfoam_surfaceFeatureExtractDict.template",
                "mesh/openfoam_meshQualityDict.template",
            ):
                if required_template not in template_artifacts.values():
                    issues.append("`mesh/openfoam_snappy_handoff.json` must reference generated OpenFOAM dictionary templates.")
                    break
            installed_artifacts = openfoam_snappy.get("installedArtifacts") if isinstance(openfoam_snappy.get("installedArtifacts"), dict) else {}
            for required_artifact in (
                "constant/triSurface/reviewedFlowLabSurfaces.stl",
                "system/snappyHexMeshDict",
                "system/surfaceFeatureExtractDict",
                "system/meshQualityDict",
            ):
                if required_artifact not in installed_artifacts.values():
                    issues.append("`mesh/openfoam_snappy_handoff.json` must reference installed starter OpenFOAM surface and dictionary artifacts.")
                    break
            starter_geometry = openfoam_snappy.get("starterGeometry") if isinstance(openfoam_snappy.get("starterGeometry"), dict) else {}
            openfoam_starter_geometry = starter_geometry
            reviewed_geometry = openfoam_snappy.get("reviewedGeometry") if isinstance(openfoam_snappy.get("reviewedGeometry"), dict) else {}
            surface_geometry = reviewed_geometry if isinstance(reviewed_geometry.get("surfaces"), list) else starter_geometry
            openfoam_reviewed_surface_geometry = surface_geometry
            if starter_geometry.get("triSurface") != "constant/triSurface/reviewedFlowLabSurfaces.stl":
                issues.append("`mesh/openfoam_snappy_handoff.json` must reference the generated reviewedFlowLabSurfaces STL.")
            location = starter_geometry.get("locationInMesh")
            if not isinstance(location, list) or len(location) != 3 or not all(_finite_json_number(value) for value in location):
                issues.append("`mesh/openfoam_snappy_handoff.json` must include a numeric starterGeometry.locationInMesh.")
            source_type = starter_geometry.get("sourceType") or reviewed_geometry.get("sourceType") or "flowlab-generated"
            if source_type not in {"flowlab-generated", "uploaded-stl", "local-stl-path", "multi-surface-stl"}:
                issues.append("`mesh/openfoam_snappy_handoff.json` has unsupported starterGeometry.sourceType.")
            stl_path = starter_geometry.get("stlPath")
            if stl_path is not None and not _safe_relative_stl_path(stl_path):
                issues.append("`mesh/openfoam_snappy_handoff.json` starterGeometry.stlPath must be a safe relative .stl path.")
            validation = starter_geometry.get("validation") if isinstance(starter_geometry.get("validation"), dict) else {}
            if starter_geometry.get("cadReviewed") is True:
                if source_type == "flowlab-generated":
                    issues.append("`mesh/openfoam_snappy_handoff.json` cannot mark FlowLab-generated starter geometry as cadReviewed.")
                if validation.get("status") != "pass":
                    issues.append("`mesh/openfoam_snappy_handoff.json` reviewed geometry validation must pass before cadReviewed=true.")
            tag_complete, missing_tags, reviewed_tags = _reviewed_boundary_tag_status(starter_geometry)
            _ = tag_complete, missing_tags
            surface_complete, missing_surface_roles, reviewed_surfaces, _ = _reviewed_surface_status(surface_geometry)
            _ = surface_complete, missing_surface_roles
            if reviewed_surfaces:
                plan = openfoam_snappy.get("boundaryPatchPlan") if isinstance(openfoam_snappy.get("boundaryPatchPlan"), dict) else {}
                if plan.get("source") != "reviewed-surfaces":
                    issues.append("`mesh/openfoam_snappy_handoff.json` must use reviewed surfaces in boundaryPatchPlan for multi-surface STL geometry.")
                if missing_surface_roles:
                    issues.append(
                        "OpenFOAM reviewed multi-surface geometry is missing required inlet/outlet/wall surfaces: "
                        + ", ".join(missing_surface_roles)
                        + "."
                    )
                for surface in reviewed_surfaces:
                    patch_name = str(surface.get("patchName") or "")
                    tri_surface = str(surface.get("triSurface") or "")
                    if not patch_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", patch_name):
                        issues.append("`mesh/openfoam_snappy_handoff.json` reviewed surfaces must include OpenFOAM-safe patchName values.")
                    if not tri_surface.startswith("constant/triSurface/") or not tri_surface.endswith(".stl"):
                        issues.append("`mesh/openfoam_snappy_handoff.json` reviewed surfaces must target constant/triSurface/*.stl files.")
                    elif tri_surface not in files:
                        issues.append(f"Missing reviewed surface STL file `{tri_surface}`.")
                    elif not _ascii_stl_is_sane(files[tri_surface]):
                        issues.append(f"`{tri_surface}` must be a sane ASCII STL containing solid, facet normal, and vertex records.")
            if source_type != "flowlab-generated" and reviewed_tags:
                plan = openfoam_snappy.get("boundaryPatchPlan") if isinstance(openfoam_snappy.get("boundaryPatchPlan"), dict) else {}
                if plan.get("source") != "reviewed-boundary-tags":
                    issues.append("`mesh/openfoam_snappy_handoff.json` must use reviewed boundary tags in boundaryPatchPlan for imported STL geometry.")

    snappy_template = files.get("mesh/openfoam_snappyHexMeshDict.template")
    if snappy_template is not None:
        for marker in ("review-only OpenFOAM native meshing template", "castellatedMeshControls", "addLayersControls", "reviewedFlowLabSurfaces.stl"):
            if marker not in snappy_template:
                issues.append("`mesh/openfoam_snappyHexMeshDict.template` is missing required review-template sections.")
                break
    surface_feature_template = files.get("mesh/openfoam_surfaceFeatureExtractDict.template")
    if surface_feature_template is not None and "surfaceFeatureExtractDict" not in surface_feature_template:
        issues.append("`mesh/openfoam_surfaceFeatureExtractDict.template` must identify surfaceFeatureExtractDict.")
    mesh_quality_template = files.get("mesh/openfoam_meshQualityDict.template")
    if mesh_quality_template is not None and "meshQualityDict" not in mesh_quality_template:
        issues.append("`mesh/openfoam_meshQualityDict.template` must identify meshQualityDict.")
    starter_stl = files.get("constant/triSurface/reviewedFlowLabSurfaces.stl")
    if starter_stl is not None:
        source_type = openfoam_starter_geometry.get("sourceType", "flowlab-generated")
        if not _ascii_stl_is_sane(starter_stl):
            issues.append("`constant/triSurface/reviewedFlowLabSurfaces.stl` must be a sane ASCII STL containing solid, facet normal, and vertex records.")
        elif source_type == "flowlab-generated" and "FlowLab-generated starter triSurface" not in starter_stl:
            issues.append("`constant/triSurface/reviewedFlowLabSurfaces.stl` must keep FlowLab starter provenance when sourceType=flowlab-generated.")
    installed_snappy = files.get("system/snappyHexMeshDict")
    if installed_snappy is not None:
        _, _, reviewed_surfaces, _ = _reviewed_surface_status(openfoam_reviewed_surface_geometry or openfoam_starter_geometry)
        if not reviewed_surfaces and "reviewedFlowLabSurfaces.stl" not in installed_snappy:
            issues.append("`system/snappyHexMeshDict` must include reviewedFlowLabSurfaces geometry.")
        if "castellatedMeshControls" not in installed_snappy:
            issues.append("`system/snappyHexMeshDict` must include castellatedMeshControls.")
        for surface in reviewed_surfaces:
            patch_name = str(surface.get("patchName") or "")
            tri_surface = str(surface.get("triSurface") or "")
            role = str(surface.get("role") or "")
            file_name = Path(tri_surface).name
            if file_name and file_name not in installed_snappy:
                issues.append(f"`system/snappyHexMeshDict` must include reviewed surface file `{file_name}`.")
            if patch_name and patch_name not in installed_snappy:
                issues.append(f"`system/snappyHexMeshDict` must include reviewed surface patch `{patch_name}`.")
            expected_type = "wall" if role == "wall" else "patch"
            if patch_name and f"type {expected_type};" not in installed_snappy:
                issues.append(f"`system/snappyHexMeshDict` must map reviewed surface patch `{patch_name}` to OpenFOAM type `{expected_type}`.")
        _, _, reviewed_tags = _reviewed_boundary_tag_status(openfoam_starter_geometry)
        if reviewed_tags:
            for tag in reviewed_tags:
                patch_name = str(tag.get("patchName") or "")
                role = str(tag.get("role") or "")
                if patch_name and patch_name not in installed_snappy:
                    issues.append(f"`system/snappyHexMeshDict` must include reviewed boundary tag `{patch_name}`.")
                expected_group = role if role in {"inlet", "outlet", "wall"} else "interface"
                if patch_name and f"inGroups ({expected_group})" not in installed_snappy:
                    issues.append(f"`system/snappyHexMeshDict` must map reviewed boundary tag `{patch_name}` to group `{expected_group}`.")
        match = re.search(r"locationInMesh\s*\(([^)]+)\)", installed_snappy)
        if not match:
            issues.append("`system/snappyHexMeshDict` must include locationInMesh.")
        elif " ".join(match.group(1).split()) == "0 0 0":
            issues.append("`system/snappyHexMeshDict` must not use the placeholder locationInMesh (0 0 0).")
    installed_surface_feature = files.get("system/surfaceFeatureExtractDict")
    if installed_surface_feature is not None and "surfaceFeatureExtractDict" not in installed_surface_feature:
        issues.append("`system/surfaceFeatureExtractDict` must identify surfaceFeatureExtractDict.")
    installed_mesh_quality = files.get("system/meshQualityDict")
    if installed_mesh_quality is not None and "meshQualityDict" not in installed_mesh_quality:
        issues.append("`system/meshQualityDict` must identify meshQualityDict.")

    su2_handoff_text = files.get("mesh/su2_native_meshing_handoff.json")
    if su2_handoff_text is not None:
        try:
            su2_handoff = json.loads(su2_handoff_text)
        except json.JSONDecodeError:
            issues.append("`mesh/su2_native_meshing_handoff.json` is not valid JSON.")
        else:
            if su2_handoff.get("schema") != "flowlab.su2_native_meshing_handoff.v1":
                issues.append("`mesh/su2_native_meshing_handoff.json` has an unsupported schema.")
            if su2_handoff.get("productionReady") is not False:
                issues.append("`mesh/su2_native_meshing_handoff.json` must remain productionReady=false until native SU2 mesh evidence exists.")
            marker_plan = su2_handoff.get("markerPlan") if isinstance(su2_handoff.get("markerPlan"), dict) else {}
            if not isinstance(marker_plan.get("allMarkers"), list) or not marker_plan.get("allMarkers"):
                issues.append("`mesh/su2_native_meshing_handoff.json` must include a non-empty markerPlan.allMarkers list.")
            viscous_plan = su2_handoff.get("viscousLayerPlan") if isinstance(su2_handoff.get("viscousLayerPlan"), dict) else {}
            if viscous_plan.get("source") != "mesh/prism_layer_plan.json":
                issues.append("`mesh/su2_native_meshing_handoff.json` must reference `mesh/prism_layer_plan.json`.")
            checks = su2_handoff.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/su2_native_meshing_handoff.json` must include readinessChecks.")
            if su2_handoff.get("productionReady") is False and not su2_handoff.get("blockingReasons"):
                issues.append("`mesh/su2_native_meshing_handoff.json` must list blockingReasons while productionReady=false.")

    code_saturne_handoff_text = files.get("mesh/code_saturne_native_meshing_handoff.json")
    if code_saturne_handoff_text is not None:
        try:
            code_saturne_handoff = json.loads(code_saturne_handoff_text)
        except json.JSONDecodeError:
            issues.append("`mesh/code_saturne_native_meshing_handoff.json` is not valid JSON.")
        else:
            if code_saturne_handoff.get("schema") != "flowlab.code_saturne_native_meshing_handoff.v1":
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` has an unsupported schema.")
            if code_saturne_handoff.get("productionReady") is not False:
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must remain productionReady=false until native Code_Saturne mesh evidence exists.")
            import_plan = code_saturne_handoff.get("importPlan") if isinstance(code_saturne_handoff.get("importPlan"), dict) else {}
            if not isinstance(import_plan.get("boundaryGroups"), list) or not import_plan.get("boundaryGroups"):
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must include importPlan.boundaryGroups.")
            if not isinstance(import_plan.get("volumeGroups"), list) or not import_plan.get("volumeGroups"):
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must include importPlan.volumeGroups.")
            prism_import = code_saturne_handoff.get("prismLayerImportPlan") if isinstance(code_saturne_handoff.get("prismLayerImportPlan"), dict) else {}
            if prism_import.get("source") != "mesh/prism_layer_plan.json":
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must reference `mesh/prism_layer_plan.json`.")
            checks = code_saturne_handoff.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must include readinessChecks.")
            if code_saturne_handoff.get("productionReady") is False and not code_saturne_handoff.get("blockingReasons"):
                issues.append("`mesh/code_saturne_native_meshing_handoff.json` must list blockingReasons while productionReady=false.")

    native_meshing_text = files.get("mesh/native_meshing_plan.json")
    if native_meshing_text is not None:
        try:
            native_meshing_plan = json.loads(native_meshing_text)
        except json.JSONDecodeError:
            issues.append("`mesh/native_meshing_plan.json` is not valid JSON.")
        else:
            if native_meshing_plan.get("schema") != "flowlab.native_meshing_plan.v1":
                issues.append("`mesh/native_meshing_plan.json` has an unsupported schema.")
            if native_meshing_plan.get("productionReady") is not False:
                issues.append("`mesh/native_meshing_plan.json` must remain productionReady=false until native production meshing evidence exists.")
            handoff_artifacts = native_meshing_plan.get("handoffArtifacts")
            if not isinstance(handoff_artifacts, list) or "mesh/gmsh_production_handoff.geo" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated Gmsh handoff artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/physical_groups.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated physical group map artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/openfoam_snappy_handoff.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated OpenFOAM snappy handoff artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/openfoam_native_mesh_preflight.py" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated OpenFOAM native mesh preflight artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/su2_native_meshing_handoff.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated SU2 native meshing handoff artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/code_saturne_native_meshing_handoff.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated Code_Saturne native meshing handoff artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/production_mesh_acceptance.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated production mesh acceptance checklist artifact.")
            if not isinstance(handoff_artifacts, list) or "mesh/adaptation_plan.json" not in handoff_artifacts:
                issues.append("`mesh/native_meshing_plan.json` must list the generated adaptation plan artifact.")
            for required_template in (
                "mesh/openfoam_snappyHexMeshDict.template",
                "mesh/openfoam_surfaceFeatureExtractDict.template",
                "mesh/openfoam_meshQualityDict.template",
            ):
                if not isinstance(handoff_artifacts, list) or required_template not in handoff_artifacts:
                    issues.append("`mesh/native_meshing_plan.json` must list generated OpenFOAM dictionary template artifacts.")
                    break
            checks = native_meshing_plan.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("`mesh/native_meshing_plan.json` must include readinessChecks.")
            prism_plan = native_meshing_plan.get("prismLayerPlan") if isinstance(native_meshing_plan.get("prismLayerPlan"), dict) else {}
            if prism_plan.get("file") != "mesh/prism_layer_plan.json":
                issues.append("`mesh/native_meshing_plan.json` must reference `mesh/prism_layer_plan.json`.")
            adaptation_ref = native_meshing_plan.get("adaptationPlan") if isinstance(native_meshing_plan.get("adaptationPlan"), dict) else {}
            if adaptation_ref.get("file") != "mesh/adaptation_plan.json":
                issues.append("`mesh/native_meshing_plan.json` must reference `mesh/adaptation_plan.json`.")
            if native_meshing_plan.get("productionReady") is False and not native_meshing_plan.get("blockingReasons"):
                issues.append("`mesh/native_meshing_plan.json` must list blockingReasons while productionReady=false.")

    gmsh_handoff = files.get("mesh/gmsh_production_handoff.geo")
    if gmsh_handoff is not None and "FlowLab review-only native meshing handoff" not in gmsh_handoff:
        issues.append("`mesh/gmsh_production_handoff.geo` must identify itself as a review-only native meshing handoff.")

    openfoam_native_preflight = files.get("mesh/openfoam_native_mesh_preflight.py")
    if openfoam_native_preflight is not None:
        required_markers = (
            "flowlab.openfoam_native_mesh_preflight.v1",
            "flowlab.openfoam_native_mesh_preflight_report.v1",
            "constant/triSurface/reviewedFlowLabSurfaces.stl",
            "locationInMesh",
            "snappyHexMesh -overwrite",
            "postProcess -func yPlus",
        )
        if not all(marker in openfoam_native_preflight for marker in required_markers):
            issues.append("`mesh/openfoam_native_mesh_preflight.py` must identify the native mesh preflight schema and required OpenFOAM native meshing checks.")

    if case.solver == "openfoam":
        review_text = files.get("mesh/openfoam_review.json", "")
        try:
            mesh_review = json.loads(review_text)
        except json.JSONDecodeError:
            issues.append("OpenFOAM `mesh/openfoam_review.json` must be valid JSON.")
        else:
            if mesh_review.get("schema") != "flowlab.openfoam_mesh_review.v1":
                issues.append("OpenFOAM mesh review manifest has an unsupported schema.")
            checks = mesh_review.get("readinessChecks")
            if not isinstance(checks, list) or not checks:
                issues.append("OpenFOAM mesh review manifest must include readinessChecks.")
            if mesh_review.get("productionReady") is False and not mesh_review.get("blockingReasons"):
                issues.append("OpenFOAM non-production mesh review manifest must list blockingReasons.")
        if case.runCommand != ["bash", "Allrun"]:
            issues.append("OpenFOAM case must run through `bash Allrun`.")
        allrun = files.get("Allrun", "")
        if "blockMesh" not in allrun and "constant/polyMesh" not in allrun:
            issues.append("OpenFOAM `Allrun` must use fitted `constant/polyMesh` or execute `blockMesh`.")
        serial_checkmesh_line = _shell_direct_command_line_index(
            allrun,
            command="checkMesh",
            required_tokens=("-allGeometry", "-allTopology"),
        )
        if serial_checkmesh_line is None:
            issues.append("OpenFOAM `Allrun` must execute `checkMesh -allGeometry -allTopology` before the solver.")
        parallel_plan_text = files.get("constant/flowlab_openfoam_parallel_plan.json")
        if parallel_plan_text is not None:
            try:
                parallel_plan = json.loads(parallel_plan_text)
            except json.JSONDecodeError:
                issues.append("OpenFOAM parallel plan must be valid JSON.")
                parallel_plan = {}
            if not isinstance(parallel_plan, dict):
                issues.append("OpenFOAM parallel plan must be a JSON object.")
                parallel_plan = {}
            if parallel_plan.get("schema") != "flowlab.openfoam-parallel-plan.v1":
                issues.append("OpenFOAM parallel plan has an unsupported schema.")
            parallel_execution = parallel_plan.get("execution")
            if parallel_execution == "parallel-candidate":
                if case.advancedMode != "incompressible-navier-stokes":
                    issues.append("OpenFOAM parallel candidate is currently limited to incompressible-navier-stokes.")
                ranks = parallel_plan.get("ranks")
                if not isinstance(ranks, int) or isinstance(ranks, bool) or ranks < 2:
                    issues.append("OpenFOAM parallel plan must declare an integer rank count of at least 2.")
                if parallel_plan.get("decomposition") != "scotch":
                    issues.append("OpenFOAM parallel candidate must declare scotch decomposition.")
                decompose_dict = files.get("system/decomposeParDict", "")
                if not decompose_dict:
                    issues.append("OpenFOAM parallel candidate requires system/decomposeParDict.")
                elif isinstance(ranks, int) and not isinstance(ranks, bool):
                    if f"numberOfSubdomains {ranks};" not in decompose_dict:
                        issues.append("OpenFOAM decomposeParDict rank count must match the parallel plan.")
                    if "method          scotch;" not in decompose_dict:
                        issues.append("OpenFOAM decomposeParDict must use scotch decomposition.")
                decompose_line = _shell_direct_command_line_index(
                    allrun,
                    command="decomposePar",
                    required_tokens=("-force",),
                )
                if decompose_line is None:
                    issues.append("OpenFOAM parallel candidate Allrun must execute decomposePar -force.")
                parallel_checkmesh_line: int | None = None
                parallel_solver_line: int | None = None
                if isinstance(ranks, int) and not isinstance(ranks, bool):
                    parallel_checkmesh_line = _shell_openfoam_parallel_command_line_index(
                        allrun,
                        ranks=ranks,
                        command="checkMesh",
                    )
                    if parallel_checkmesh_line is None:
                        issues.append("OpenFOAM parallel candidate Allrun must execute checkMesh through mpirun with -parallel after decomposition.")
                    parallel_solver_line = _shell_openfoam_parallel_command_line_index(
                        allrun,
                        ranks=ranks,
                        command="foamRun",
                    )
                    if parallel_solver_line is None:
                        issues.append("OpenFOAM parallel candidate Allrun must launch foamRun through mpirun with -parallel and the planned rank count.")
                reconstruct_line = _shell_direct_command_line_index(
                    allrun,
                    command="reconstructPar",
                    required_tokens=("-latestTime",),
                )
                if reconstruct_line is None:
                    issues.append("OpenFOAM parallel candidate Allrun must reconstruct the latest result.")
                ordered_lines = (
                    serial_checkmesh_line,
                    decompose_line,
                    parallel_checkmesh_line,
                    parallel_solver_line,
                    reconstruct_line,
                )
                if all(line is not None for line in ordered_lines) and not (
                    serial_checkmesh_line < decompose_line < parallel_checkmesh_line < parallel_solver_line < reconstruct_line
                ):
                    issues.append(
                        "OpenFOAM parallel candidate Allrun must order serial checkMesh, decomposePar, parallel checkMesh, parallel foamRun, and reconstructPar."
                    )
            elif parallel_execution == "serial-baseline":
                if parallel_plan.get("ranks") != 1:
                    issues.append("OpenFOAM serial baseline plan must declare exactly one rank.")
                if "system/decomposeParDict" in files:
                    issues.append("OpenFOAM serial baseline case must not include decomposeParDict.")
            else:
                issues.append("OpenFOAM parallel plan must declare serial-baseline or parallel-candidate execution.")
        if case.advancedMode == "conjugate-heat-transfer":
            try:
                cht_interface_for_run = json.loads(files.get("constant/flowlab_cht_interface.json", ""))
            except json.JSONDecodeError:
                cht_interface_for_run = {}
            cht_production_ready = isinstance(cht_interface_for_run, dict) and cht_interface_for_run.get("productionReady") is True
            if cht_production_ready:
                if not _shell_has_uncommented_command(allrun, ("foamMultiRun", "chtMultiRegionFoam")):
                    issues.append("OpenFOAM production-ready conjugate heat-transfer `Allrun` must execute a CHT solver command.")
            else:
                if _shell_has_uncommented_command(allrun, ("foamMultiRun", "chtMultiRegionFoam")):
                    issues.append(
                        "OpenFOAM CHT `Allrun` must not execute `foamMultiRun` or `chtMultiRegionFoam` while the interface manifest is productionReady=false."
                    )
                if "full foamMultiRun remains blocked" not in allrun:
                    issues.append("OpenFOAM CHT non-production `Allrun` must clearly block full `foamMultiRun` execution.")
            allmesh_check = files.get("AllmeshCheck", "")
            if "checkMesh -region fluid -allGeometry -allTopology" not in allmesh_check:
                issues.append("OpenFOAM conjugate heat-transfer `AllmeshCheck` must check the fluid region mesh.")
            if "checkMesh -region solid -allGeometry -allTopology" not in allmesh_check:
                issues.append("OpenFOAM conjugate heat-transfer `AllmeshCheck` must check the solid region mesh.")
        elif "foamRun -solver" not in allrun:
            issues.append("OpenFOAM `Allrun` must run `foamRun -solver`.")
        block_mesh = files.get("system/blockMeshDict", "")
        # An axisymmetric wedge pipe uses wedge front/back + a collapsed axis instead
        # of a single empty frontAndBack; its fitted polyMesh is skipped so blockMesh
        # builds the wedge (and handles the singular axis) at run time.
        is_axisymmetric_wedge = "type wedge" in block_mesh
        try:
            full_ogrid_profile = json.loads(files.get("constant/flowlab_full_ogrid_profile.json", ""))
        except json.JSONDecodeError:
            full_ogrid_profile = {}
        full_ogrid_schema = (
            full_ogrid_profile.get("schema")
            if isinstance(full_ogrid_profile, dict)
            else None
        )
        is_full_ogrid = full_ogrid_schema in {
            "flowlab.full-ogrid-profile.v1",
            "flowlab.full-ogrid-path-profile.v1",
        }
        is_full_ogrid_path = (
            full_ogrid_schema == "flowlab.full-ogrid-path-profile.v1"
        )
        try:
            curved_elbow_profile = json.loads(
                files.get("constant/flowlab_curved_elbow_profile.json", "")
            )
        except json.JSONDecodeError:
            curved_elbow_profile = {}
        is_curved_elbow = (
            isinstance(curved_elbow_profile, dict)
            and curved_elbow_profile.get("schema")
            == "flowlab.curved-elbow-ogrid-profile.v1"
        )
        try:
            y_junction_profile = json.loads(
                files.get("constant/flowlab_y_junction_profile.json", "")
            )
        except json.JSONDecodeError:
            y_junction_profile = {}
        is_y_junction = (
            isinstance(y_junction_profile, dict)
            and y_junction_profile.get("schema") == "flowlab.y-junction-profile.v1"
            and y_junction_profile.get("effectiveMeshMode")
            == "generated-cartesian-all-hex-y-junction"
        )
        if is_axisymmetric_wedge:
            try:
                axisymmetric_profile = json.loads(files.get("constant/flowlab_axisymmetric_profile.json", ""))
            except json.JSONDecodeError:
                axisymmetric_profile = {}
            if not isinstance(axisymmetric_profile, dict) or axisymmetric_profile.get("schema") != "flowlab.axisymmetric-profile.v1":
                issues.append("OpenFOAM axisymmetric wedge requires a valid canonical axisymmetric profile manifest.")
            else:
                if axisymmetric_profile.get("effectiveMeshMode") != "axisymmetric-wedge":
                    issues.append("OpenFOAM axisymmetric profile must declare effectiveMeshMode=axisymmetric-wedge.")
                if not axisymmetric_profile.get("stations") or not axisymmetric_profile.get("segments"):
                    issues.append("OpenFOAM axisymmetric profile must declare physical stations and conformal block segments.")
                benchmark_contract = axisymmetric_profile.get("benchmarkContract")
                if isinstance(benchmark_contract, dict):
                    if benchmark_contract.get("schema") != "flowlab.axisymmetric-straight-pipe-contract.v1":
                        issues.append("OpenFOAM axisymmetric benchmark contract has an unsupported schema.")
                    if (
                        benchmark_contract.get("fixtureId") != "straight-pipe"
                        or benchmark_contract.get("fixtureStatus") != "pending-real-run"
                        or benchmark_contract.get("boundaryCondition") != "periodic-pressure-gradient"
                    ):
                        issues.append("OpenFOAM axisymmetric benchmark contract must remain a pending periodic straight-pipe candidate.")
                    if (
                        not isinstance(benchmark_contract.get("fullCircleScale"), (int, float))
                        or isinstance(benchmark_contract.get("fullCircleScale"), bool)
                        or float(benchmark_contract["fullCircleScale"]) <= 1.0
                    ):
                        issues.append("OpenFOAM axisymmetric benchmark contract requires a positive wedge-to-full-circle scale.")
                    fv_constraints = files.get("system/fvConstraints", "")
                    if "type            meanVelocityForce;" not in fv_constraints or "Ubar" not in fv_constraints:
                        issues.append("OpenFOAM axisymmetric benchmark requires meanVelocityForce flow control.")
                    if (
                        "type cyclic;" not in block_mesh
                        or "neighbourPatch outlet;" not in block_mesh
                        or "neighbourPatch inlet;" not in block_mesh
                    ):
                        issues.append("OpenFOAM axisymmetric benchmark requires paired cyclic inlet/outlet mesh patches.")
                    for field in ("0/U", "0/p", "0/T"):
                        if files.get(field, "").count("type            cyclic;") < 2:
                            issues.append(f"OpenFOAM axisymmetric benchmark field `{field}` requires cyclic inlet and outlet conditions.")
                    fv_solution = files.get("system/fvSolution", "")
                    if "residualControl" not in fv_solution or "PIMPLE" in fv_solution:
                        issues.append("OpenFOAM axisymmetric benchmark requires direct steady SIMPLE residual controls.")
            try:
                axisymmetric_preview = json.loads(files.get("mesh/flowlab_mesh.json", ""))
            except json.JSONDecodeError:
                axisymmetric_preview = {}
            spans = axisymmetric_preview.get("boundsSpanM") if isinstance(axisymmetric_preview, dict) else None
            if (
                not isinstance(axisymmetric_preview, dict)
                or axisymmetric_preview.get("spatialDimension") != 3
                or axisymmetric_preview.get("representation") != "pre-solve-blockMesh-equivalent-wedge"
                or not isinstance(spans, list)
                or len(spans) != 3
                or any(not isinstance(value, int | float) or value <= 0 for value in spans)
            ):
                issues.append("OpenFOAM axisymmetric wedge requires a non-degenerate 3D blockMesh-equivalent inspection artifact.")
        if is_full_ogrid:
            expected_mesh_mode = (
                "full-revolution-multi-segment-five-block-ogrid"
                if is_full_ogrid_path
                else "full-revolution-five-block-ogrid"
            )
            if full_ogrid_profile.get("effectiveMeshMode") != expected_mesh_mode:
                issues.append(
                    "OpenFOAM full O-grid profile must declare the expected "
                    "five-block full-revolution representation."
                )
            topology = full_ogrid_profile.get("topology") if isinstance(full_ogrid_profile.get("topology"), dict) else {}
            resolution = topology.get("resolution") if isinstance(topology.get("resolution"), dict) else {}
            interfaces = topology.get("interfaces") if isinstance(topology.get("interfaces"), dict) else {}
            try:
                circumference = int(resolution["circumferentialCells"])
                core = int(resolution["coreCellsPerSide"])
                expected_cells = int(resolution["cellCount"])
            except (KeyError, TypeError, ValueError):
                circumference = core = expected_cells = 0
            expected_block_count = (
                5 * int(topology.get("geometrySegmentCount", 0))
                if is_full_ogrid_path
                else 5
            )
            if (
                topology.get("spatialDimension") != 3
                or topology.get("blockCount") != expected_block_count
                or topology.get("cellTypes") != ["hex"]
                or topology.get("collapsedAxisCells") != 0
                or circumference < 16
                or circumference % 4 != 0
                or core != circumference // 4
                or expected_cells <= 0
            ):
                issues.append("OpenFOAM full O-grid profile has an invalid or non-conformal topology contract.")
            interface_count_valid = (
                interfaces.get("crossSectionBlockInterfaces")
                == 4 * int(topology.get("geometrySegmentCount", 0))
                and interfaces.get("axialSegmentInterfaces")
                == int(topology.get("geometrySegmentCount", 0)) - 1
                if is_full_ogrid_path
                else interfaces.get("count") == 4
            )
            if (
                not interface_count_valid
                or interfaces.get("treatment") != "conformal-internal-faces"
                or interfaces.get("boundaryPatchCount") != 0
            ):
                issues.append("OpenFOAM full O-grid center/wall interfaces must remain conformal internal faces.")
            if block_mesh.count("    hex (") != expected_block_count:
                issues.append(
                    "OpenFOAM full O-grid blockMeshDict must contain the "
                    "contracted number of hexahedral blocks."
                )
            if any(token in block_mesh for token in ("type wedge", "frontAndBack", "neighbourPatch")):
                issues.append("OpenFOAM full O-grid blockMeshDict cannot contain wedge, planar, or cyclic proxy patches.")
            try:
                full_preview = json.loads(files.get("mesh/flowlab_mesh.json", ""))
            except json.JSONDecodeError:
                full_preview = {}
            spans = full_preview.get("boundsSpanM") if isinstance(full_preview, dict) else None
            cells = full_preview.get("cells") if isinstance(full_preview, dict) else None
            cell_types = full_preview.get("cellTypes") if isinstance(full_preview, dict) else None
            volume_quality = full_preview.get("volumeQuality") if isinstance(full_preview, dict) and isinstance(full_preview.get("volumeQuality"), dict) else {}
            if (
                full_preview.get("spatialDimension") != 3
                or full_preview.get("representation")
                != (
                    "pre-solve-blockMesh-equivalent-full-ogrid-path"
                    if is_full_ogrid_path
                    else "pre-solve-blockMesh-equivalent-full-ogrid"
                )
                or full_preview.get("proxyGeometry") is not False
                or not isinstance(spans, list)
                or len(spans) != 3
                or any(not isinstance(value, int | float) or value <= 0 for value in spans)
                or not isinstance(cells, list)
                or len(cells) != expected_cells
                or not isinstance(cell_types, list)
                or len(cell_types) != expected_cells
                or any(cell_type != 12 for cell_type in cell_types)
                or volume_quality.get("positiveVolume") is not True
                or volume_quality.get("zeroVolumeCellCount") != 0
            ):
                issues.append("OpenFOAM full O-grid requires a positive-volume, full-extent 3D all-hex inspection mesh.")
            verification_contract = full_ogrid_profile.get("verificationContract")
            if isinstance(verification_contract, dict):
                if (
                    verification_contract.get("schema") != "flowlab.full-ogrid-verification-contract.v1"
                    or verification_contract.get("status") != "prospective-request-not-validation"
                    or verification_contract.get("boundaryCondition")
                    != "fully-developed-parabolic-inlet-pressure-outlet"
                ):
                    issues.append("OpenFOAM full O-grid verification request has an unsupported prospective contract.")
                velocity_field = files.get("0/U", "")
                if (
                    "fullOGridParabolicInlet" not in velocity_field
                    or "targetFlow/weightedArea" not in velocity_field
                    or "pressureInletOutletVelocity" not in velocity_field
                ):
                    issues.append("OpenFOAM full O-grid verification requires a discrete-flux-normalized parabolic inlet and pressure-coupled outlet.")
                if "residualControl" not in files.get("system/fvSolution", ""):
                    issues.append("OpenFOAM full O-grid verification requires direct steady SIMPLE residual controls.")
            qualification_contract = full_ogrid_profile.get(
                "qualificationContract"
            )
            if is_full_ogrid_path:
                if (
                    not isinstance(qualification_contract, dict)
                    or qualification_contract.get("schema")
                    != (
                        "flowlab.full-ogrid-geometry-experimental-"
                        "qualification-request.v1"
                    )
                    or qualification_contract.get("status")
                    != (
                        "prospective-experimental-software-geometry-"
                        "qualification"
                    )
                    or qualification_contract.get("validated") is not False
                    or qualification_contract.get("promotionAuthorized")
                    is not False
                    or qualification_contract.get(
                        "qoiHistoryWriteIntervalIterations"
                    )
                    != 1
                ):
                    issues.append(
                        "OpenFOAM full O-grid path requires a supported "
                        "prospective nonpromotional qualification contract."
                    )
                if len(full_ogrid_profile.get("pathEdgeIds", [])) > 1:
                    identity_contract_text = files.get(
                        "constant/flowlab_result_identity_contract.json", ""
                    )
                    try:
                        identity_contract = json.loads(identity_contract_text)
                    except json.JSONDecodeError:
                        identity_contract = {}
                    if (
                        identity_contract.get("algorithm")
                        not in FULL_OGRID_SOURCE_IDENTITY_ALGORITHMS
                        or identity_contract.get("orderingAssumptionAllowed")
                        is not False
                        or identity_contract.get("unownedRanges") != []
                    ):
                        issues.append(
                            "OpenFOAM multi-edge full O-grid path requires "
                            "fail-closed explicit source-cell identity with "
                            "complete edge ownership."
                        )
                velocity_field = files.get("0/U", "")
                if (
                    "fullOGridParabolicInlet" not in velocity_field
                    or "targetFlow/weightedArea" not in velocity_field
                    or "pressureInletOutletVelocity" not in velocity_field
                    or "residualControl"
                    not in files.get("system/fvSolution", "")
                ):
                    issues.append(
                        "OpenFOAM full O-grid path qualification requires "
                        "the contracted inlet, outlet, and steady solver controls."
                    )
        if is_curved_elbow:
            if (
                curved_elbow_profile.get("effectiveMeshMode")
                != "canonical-90deg-circular-elbow-fifteen-block-ogrid"
            ):
                issues.append(
                    "OpenFOAM curved-elbow profile must declare the canonical 15-block representation."
                )
            topology = (
                curved_elbow_profile.get("topology")
                if isinstance(curved_elbow_profile.get("topology"), dict)
                else {}
            )
            resolution = (
                topology.get("resolution")
                if isinstance(topology.get("resolution"), dict)
                else {}
            )
            interfaces = (
                topology.get("interfaces")
                if isinstance(topology.get("interfaces"), dict)
                else {}
            )
            geometry = (
                topology.get("geometry")
                if isinstance(topology.get("geometry"), dict)
                else {}
            )
            try:
                circumference = int(resolution["circumferentialCells"])
                core = int(resolution["coreCellsPerSide"])
                expected_cells = int(resolution["cellCount"])
            except (KeyError, TypeError, ValueError):
                circumference = core = expected_cells = 0
            if (
                topology.get("spatialDimension") != 3
                or topology.get("blockCount") != 15
                or topology.get("cellTypes") != ["hex"]
                or topology.get("collapsedAxisCells") != 0
                or circumference < 16
                or circumference % 4 != 0
                or core != circumference // 4
                or expected_cells <= 0
            ):
                issues.append(
                    "OpenFOAM curved-elbow profile has an invalid or non-conformal topology contract."
                )
            if (
                interfaces.get("centerWallCountPerComponent") != 4
                or interfaces.get("longitudinalComponentCount") != 2
                or interfaces.get("treatment")
                != "shared-vertex-conformal-internal-faces"
                or interfaces.get("boundaryPatchCount") != 0
            ):
                issues.append(
                    "OpenFOAM curved-elbow interfaces must remain shared-vertex conformal internal faces."
                )
            if (
                not math.isclose(
                    float(geometry.get("centrelineRadiusOverDiameter", 0.0)),
                    3.0,
                    rel_tol=1.0e-12,
                )
                or not math.isclose(
                    float(geometry.get("inletLegOverDiameter", 0.0)),
                    10.0,
                    rel_tol=1.0e-12,
                )
                or not math.isclose(
                    float(geometry.get("outletLegOverDiameter", 0.0)),
                    10.0,
                    rel_tol=1.0e-12,
                )
                or not math.isclose(
                    float(geometry.get("bendAngleDegrees", 0.0)),
                    90.0,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            ):
                issues.append(
                    "OpenFOAM curved-elbow geometry must remain the bounded 90-degree Rc/D=3, 10D/10D case."
                )
            if block_mesh.count("    hex (") != 15:
                issues.append(
                    "OpenFOAM curved-elbow blockMeshDict must contain exactly 15 hexahedral blocks."
                )
            if any(
                token in block_mesh
                for token in ("type wedge", "frontAndBack", "neighbourPatch")
            ):
                issues.append(
                    "OpenFOAM curved-elbow blockMeshDict cannot contain wedge, planar, or cyclic proxy patches."
                )
            try:
                elbow_preview = json.loads(files.get("mesh/flowlab_mesh.json", ""))
            except json.JSONDecodeError:
                elbow_preview = {}
            spans = (
                elbow_preview.get("boundsSpanM")
                if isinstance(elbow_preview, dict)
                else None
            )
            cells = (
                elbow_preview.get("cells")
                if isinstance(elbow_preview, dict)
                else None
            )
            cell_types = (
                elbow_preview.get("cellTypes")
                if isinstance(elbow_preview, dict)
                else None
            )
            regions = (
                elbow_preview.get("regions")
                if isinstance(elbow_preview, dict)
                else None
            )
            volume_quality = (
                elbow_preview.get("volumeQuality")
                if isinstance(elbow_preview, dict)
                and isinstance(elbow_preview.get("volumeQuality"), dict)
                else {}
            )
            region_ids = [
                region.get("componentId")
                for region in regions
                if isinstance(region, dict)
            ] if isinstance(regions, list) else []
            if (
                elbow_preview.get("spatialDimension") != 3
                or elbow_preview.get("representation")
                != "pre-solve-blockMesh-equivalent-curved-elbow-ogrid"
                or elbow_preview.get("proxyGeometry") is not False
                or elbow_preview.get("requiresExplicitSourceCellProvenance")
                is not True
                or not isinstance(spans, list)
                or len(spans) != 3
                or any(
                    not isinstance(value, int | float) or value <= 0
                    for value in spans
                )
                or not isinstance(cells, list)
                or len(cells) != expected_cells
                or not isinstance(cell_types, list)
                or len(cell_types) != expected_cells
                or any(cell_type != 12 for cell_type in cell_types)
                or region_ids != ["inlet-leg", "elbow", "outlet-leg"]
                or sum(
                    int(region.get("cellCount", 0))
                    for region in regions
                    if isinstance(region, dict)
                )
                != expected_cells
                or volume_quality.get("positiveVolume") is not True
                or volume_quality.get("zeroVolumeCellCount") != 0
            ):
                issues.append(
                    "OpenFOAM curved-elbow requires a positive-volume, full-extent 3D all-hex inspection mesh with explicit component provenance."
                )
            verification_contract = curved_elbow_profile.get(
                "verificationContract"
            )
            if not isinstance(verification_contract, dict) or (
                verification_contract.get("schema")
                != "flowlab.curved-elbow-verification-request.v1"
                or verification_contract.get("status")
                != "prospective-request-not-validation"
                or verification_contract.get("boundaryCondition")
                != "fully-developed-parabolic-inlet-pressure-outlet"
            ):
                issues.append(
                    "OpenFOAM curved-elbow verification request has an unsupported prospective contract."
                )
            velocity_field = files.get("0/U", "")
            if (
                "curvedElbowParabolicInlet" not in velocity_field
                or "targetFlow/weightedArea" not in velocity_field
                or "pressureInletOutletVelocity" not in velocity_field
            ):
                issues.append(
                    "OpenFOAM curved-elbow verification requires a discrete-flux-normalized parabolic inlet and pressure-coupled outlet."
                )
            if "residualControl" not in files.get("system/fvSolution", ""):
                issues.append(
                    "OpenFOAM curved-elbow verification requires direct steady SIMPLE residual controls."
                )
            try:
                probe_provenance = json.loads(
                    files.get(
                        "constant/flowlab_curved_elbow_probe_provenance.json",
                        "",
                    )
                )
            except json.JSONDecodeError:
                probe_provenance = {}
            probe_rows = (
                probe_provenance.get("probes")
                if isinstance(probe_provenance.get("probes"), list)
                else []
            )
            profile_components = (
                curved_elbow_profile.get("components")
                if isinstance(curved_elbow_profile.get("components"), list)
                else []
            )
            expected_component_ranges = {
                str(row.get("componentId")): {
                    "cellStart": int(row.get("cellStart", -1)),
                    "cellCount": int(row.get("cellCount", -1)),
                }
                for row in profile_components
                if isinstance(row, dict)
            }
            if (
                probe_provenance.get("schema")
                != "flowlab.curved-elbow-probe-provenance.v1"
                or probe_provenance.get("probeFunctionObject")
                != "curvedElbowXYZProbes"
                or probe_provenance.get("sourceCellIdentity")
                != "result-component-map-v2-cell-ranges"
                or probe_provenance.get(
                    "geometryInferredOwnershipAllowed"
                )
                is not False
                or probe_provenance.get("probeCount") != 7
                or len(probe_rows) != 7
                or any(
                    row.get("geometryInferredOwnership") is not False
                    or row.get("ownershipMethod")
                    != "explicit-result-component-map-v2-cell-range"
                    or row.get("sourceCellRange")
                    != expected_component_ranges.get(
                        str(row.get("componentId"))
                    )
                    for row in probe_rows
                    if isinstance(row, dict)
                )
                or any(not isinstance(row, dict) for row in probe_rows)
            ):
                issues.append(
                    "OpenFOAM curved-elbow probes require explicit, non-geometric source-cell component provenance."
                )
        if is_y_junction:
            try:
                y_preview = json.loads(files.get("mesh/flowlab_mesh.json", ""))
            except json.JSONDecodeError:
                y_preview = {}
            topology = y_preview.get("topology") if isinstance(y_preview.get("topology"), dict) else {}
            volume_quality = (
                y_preview.get("volumeQuality")
                if isinstance(y_preview.get("volumeQuality"), dict)
                else {}
            )
            regions = y_preview.get("regions") if isinstance(y_preview.get("regions"), list) else []
            edge_regions = [
                region
                for region in regions
                if isinstance(region, dict) and region.get("role") == "edge"
            ]
            junction_regions = [
                region
                for region in regions
                if isinstance(region, dict) and region.get("role") == "junction"
            ]
            profile_edges = y_junction_profile.get("pathEdgeIds")
            if (
                y_preview.get("spatialDimension") != 3
                or y_preview.get("representation")
                != "generated-cartesian-all-hex-y-junction"
                or y_preview.get("proxyGeometry") is not False
                or topology.get("connectedFluidRegions") != 1
                or topology.get("portPatchCount") != 3
                or topology.get("portPatches")
                != ["inlet", "outletUpper", "outletLower"]
                or topology.get("cellTypes") != ["hex"]
                or volume_quality.get("positiveVolume") is not True
                or float(volume_quality.get("minimumCellVolumeM3", 0.0)) <= 0.0
            ):
                issues.append(
                    "OpenFOAM Y-junction requires one positive-volume connected true-3D all-hex generated mesh."
                )
            if (
                not isinstance(profile_edges, list)
                or len(profile_edges) != 3
                or {region.get("edgeId") for region in edge_regions} != set(profile_edges)
                or len(edge_regions) != 3
            ):
                issues.append(
                    "OpenFOAM Y-junction must declare exactly one generated source-cell range for each of its three edges."
                )
            if (
                len(junction_regions) != 1
                or junction_regions[0].get("artifactIdentity", {}).get("artifactId")
                != "generated:y-junction:junction-core:v1"
                or junction_regions[0].get("artifactIdentity", {}).get("schematicOwner")
                is not None
            ):
                issues.append(
                    "OpenFOAM Y-junction junction cells require the dedicated generated artifact identity and no schematic owner."
                )
            ranges = sorted(
                (
                    int(region.get("cellStart", -1)),
                    int(region.get("cellCount", 0)),
                    str(region.get("edgeId", "")),
                )
                for region in edge_regions
            )
            junction_start = (
                int(junction_regions[0].get("cellStart", -1))
                if junction_regions
                else -1
            )
            if (
                any(start < 0 or count <= 0 for start, count, _edge in ranges)
                or any(
                    ranges[index][0] + ranges[index][1] > ranges[index + 1][0]
                    for index in range(len(ranges) - 1)
                )
                or (ranges and ranges[-1][0] + ranges[-1][1] > junction_start)
            ):
                issues.append(
                    "OpenFOAM Y-junction edge source-cell ranges overlap or include generated junction cells."
                )
            for field in ("0/U", "0/p"):
                field_text = files.get(field, "")
                for patch in ("inlet", "outletUpper", "outletLower", "walls"):
                    if not _field_defines_patch(field_text, patch):
                        issues.append(
                            f"OpenFOAM Y-junction field `{field}` is missing patch `{patch}`."
                        )
        expected_block_patches = (
            ("inlet", "outlet", "walls", "front", "back")
            if is_axisymmetric_wedge
            else ("inlet", "outlet", "walls")
            if is_full_ogrid or is_curved_elbow
            else ("inlet", "outletUpper", "outletLower", "walls")
            if is_y_junction
            else ("inlet", "outlet", "walls", "frontAndBack")
        )
        for patch in expected_block_patches:
            if patch not in block_mesh:
                issues.append(f"OpenFOAM `system/blockMeshDict` is missing `{patch}` boundary patch.")
        poly_boundary = files.get("constant/polyMesh/boundary")
        if poly_boundary:
            expected_poly_patches = (
                ("inlet", "outletUpper", "outletLower", "walls")
                if is_y_junction
                else ("inlet", "outlet", "walls", "frontAndBack")
            )
            for patch in expected_poly_patches:
                if patch not in poly_boundary:
                    issues.append(f"OpenFOAM `constant/polyMesh/boundary` is missing `{patch}` boundary patch.")
            for path in (
                "constant/polyMesh/points",
                "constant/polyMesh/faces",
                "constant/polyMesh/owner",
                "constant/polyMesh/neighbour",
            ):
                if path not in files:
                    issues.append(f"OpenFOAM fitted polyMesh is missing `{path}`.")
        for field in ("0/U", "0/p", "0/T"):
            if field in files and "boundaryField" not in files[field]:
                issues.append(f"OpenFOAM field `{field}` is missing `boundaryField`.")
        _, _, reviewed_surfaces_for_bcs, _ = _reviewed_surface_status(openfoam_reviewed_surface_geometry or openfoam_starter_geometry)
        if reviewed_surfaces_for_bcs:
            bc_text = files.get("constant/flowlab_boundary_conditions.json")
            if bc_text is None:
                if openfoam_boundary_condition_coverage.get("status") == "pass":
                    bc_manifest = {
                        "schema": "flowlab.openfoam_surface_boundary_conditions.v1",
                        "status": "complete",
                        "patches": [
                            {"patchName": patch_name}
                            for patch_name in openfoam_boundary_condition_coverage.get("patchesWithConditions", [])
                            if isinstance(patch_name, str)
                        ],
                    }
                else:
                    issues.append("OpenFOAM reviewed surface patches require complete boundary-condition coverage before solve execution.")
                    bc_manifest = {}
            else:
                try:
                    bc_manifest = json.loads(bc_text)
                except json.JSONDecodeError:
                    issues.append("`constant/flowlab_boundary_conditions.json` is not valid JSON.")
                    bc_manifest = {}
                else:
                    if bc_manifest.get("schema") != "flowlab.openfoam_surface_boundary_conditions.v1":
                        issues.append("`constant/flowlab_boundary_conditions.json` has an unsupported schema.")
                    if bc_manifest.get("status") != "complete":
                        missing = bc_manifest.get("missingPatchNames") if isinstance(bc_manifest.get("missingPatchNames"), list) else []
                        invalid = bc_manifest.get("invalidPatchNames") if isinstance(bc_manifest.get("invalidPatchNames"), list) else []
                        details = ", ".join(str(item) for item in [*missing, *invalid]) or "unknown patches"
                        issues.append(f"OpenFOAM reviewed surface boundary conditions are incomplete for: {details}.")
            manifest_patch_names = {
                str(entry.get("patchName"))
                for entry in bc_manifest.get("patches", [])
                if isinstance(entry, dict) and isinstance(entry.get("patchName"), str)
            } if isinstance(bc_manifest.get("patches"), list) else set()
            required_patch_names = [
                str(surface.get("patchName"))
                for surface in reviewed_surfaces_for_bcs
                if isinstance(surface.get("patchName"), str) and surface.get("patchName")
            ]
            for patch_name in required_patch_names:
                if manifest_patch_names and patch_name not in manifest_patch_names:
                    issues.append(f"`constant/flowlab_boundary_conditions.json` is missing reviewed patch `{patch_name}`.")
            for field in _openfoam_required_surface_bc_fields(files):
                field_text = files.get(field, "")
                if "boundaryField" not in field_text:
                    issues.append(f"OpenFOAM field `{field}` is missing `boundaryField`.")
                    continue
                for patch_name in required_patch_names:
                    if not _field_defines_patch(field_text, patch_name):
                        issues.append(f"OpenFOAM field `{field}` is missing reviewed surface patch `{patch_name}`.")
        control_dict = files.get("system/controlDict", "")
        function_object_text = f"{control_dict}\n{files.get('system/functions', '')}"
        metric_patch_plan, has_probe_nodes = _openfoam_metric_patch_expectations(
            files,
            openfoam_reviewed_surface_geometry or openfoam_starter_geometry,
        )
        patch_metrics_text = files.get("constant/flowlab_patch_metrics.json")
        if patch_metrics_text is None:
            issues.append("OpenFOAM case is missing `constant/flowlab_patch_metrics.json`.")
        else:
            try:
                patch_metrics_manifest = json.loads(patch_metrics_text)
            except json.JSONDecodeError:
                issues.append("`constant/flowlab_patch_metrics.json` is not valid JSON.")
                patch_metrics_manifest = {}
            if patch_metrics_manifest.get("schema") != "flowlab.openfoam_patch_metric_function_objects.v1":
                issues.append("`constant/flowlab_patch_metrics.json` has an unsupported schema.")
            manifest_patches = patch_metrics_manifest.get("patches") if isinstance(patch_metrics_manifest.get("patches"), dict) else {}
            for role in ("inlet", "outlet", "wall"):
                listed = manifest_patches.get(role) if isinstance(manifest_patches.get(role), list) else []
                for patch_name in metric_patch_plan[role]:
                    if patch_name not in listed:
                        issues.append(f"`constant/flowlab_patch_metrics.json` is missing {role} patch `{patch_name}`.")
            manifest_functions = patch_metrics_manifest.get("functionObjects") if isinstance(patch_metrics_manifest.get("functionObjects"), list) else []
        profile_probe_name = (
            "curvedElbowXYZProbes"
            if is_curved_elbow
            else "fullOGridXYZProbes"
            if is_full_ogrid
            else "axisymmetricProfileProbes"
            if is_axisymmetric_wedge
            else "yJunctionMirroredProbes"
            if is_y_junction
            else "centerlineProbes"
        )
        required_function_objects = ["residuals", profile_probe_name, "wallForces", "patchFlowRate", "patchAverage", "wallShearStress"]
        if has_probe_nodes:
            required_function_objects.append("pressureProbes")
        for function_object in required_function_objects:
            if function_object not in function_object_text:
                issues.append(f"OpenFOAM `system/controlDict` is missing `{function_object}` function object.")
            if patch_metrics_text is not None and function_object not in {"residuals", profile_probe_name} and function_object not in manifest_functions:
                issues.append(f"`constant/flowlab_patch_metrics.json` is missing `{function_object}` function object.")
        patch_flow_block = _control_dict_function_block(function_object_text, "patchFlowRate") or ""
        patch_average_block = _control_dict_function_block(function_object_text, "patchAverage") or ""
        wall_shear_block = _control_dict_function_block(function_object_text, "wallShearStress") or ""
        wall_forces_block = _control_dict_function_block(function_object_text, "wallForces") or ""
        for patch_name in [*metric_patch_plan["inlet"], *metric_patch_plan["outlet"]]:
            if patch_name not in patch_flow_block:
                issues.append(f"OpenFOAM `patchFlowRate` function object is missing patch `{patch_name}`.")
            if patch_name not in patch_average_block:
                issues.append(f"OpenFOAM `patchAverage` function object is missing patch `{patch_name}`.")
        for patch_name in metric_patch_plan["wall"]:
            if patch_name not in wall_shear_block:
                issues.append(f"OpenFOAM `wallShearStress` function object is missing patch `{patch_name}`.")
            if patch_name not in wall_forces_block:
                issues.append(f"OpenFOAM `wallForces` function object is missing patch `{patch_name}`.")
        if has_probe_nodes:
            pressure_probe_block = _control_dict_function_block(function_object_text, "pressureProbes") or ""
            if "fields          (p p_rgh);" not in pressure_probe_block:
                issues.append("OpenFOAM `pressureProbes` function object must sample pressure fields `(p p_rgh)`.")
        for path in OPENFOAM_MODE_FILES.get(case.advancedMode, ()):
            if path not in files:
                issues.append(f"OpenFOAM `{case.advancedMode}` mode is missing `{path}`.")
        if case.advancedMode == "conjugate-heat-transfer":
            control_dict = files.get("system/controlDict", "")
            if "application     foamMultiRun;" not in control_dict or "regionSolvers" not in control_dict:
                issues.append("OpenFOAM conjugate heat-transfer requires `foamMultiRun` and `regionSolvers` in `system/controlDict`.")
            for path in OPENFOAM_CHT_REGION_FILES:
                if path not in files:
                    issues.append(f"OpenFOAM conjugate heat-transfer requires multi-region file `{path}`.")
            interface_text = files.get("constant/flowlab_cht_interface.json", "")
            try:
                interface = json.loads(interface_text)
            except json.JSONDecodeError:
                issues.append("OpenFOAM CHT `constant/flowlab_cht_interface.json` must be valid JSON.")
            else:
                if interface.get("schema") != "flowlab.openfoam_cht_interface.v1":
                    issues.append("OpenFOAM CHT interface manifest has an unsupported schema.")
                patches = interface.get("patches") if isinstance(interface.get("patches"), dict) else {}
                fluid_patch = patches.get("fluid") if isinstance(patches.get("fluid"), dict) else {}
                solid_patch = patches.get("solid") if isinstance(patches.get("solid"), dict) else {}
                if fluid_patch.get("neighbourRegion") != "solid" or fluid_patch.get("neighbourPatch") != "solid_to_fluid":
                    issues.append("OpenFOAM CHT fluid interface patch must map to solid/solid_to_fluid.")
                if solid_patch.get("neighbourRegion") != "fluid" or solid_patch.get("neighbourPatch") != "fluid_to_solid":
                    issues.append("OpenFOAM CHT solid interface patch must map to fluid/fluid_to_solid.")
                checks = interface.get("readinessChecks")
                if not isinstance(checks, list) or not checks:
                    issues.append("OpenFOAM CHT interface manifest must include readinessChecks.")
                region_mesh_checks = interface.get("regionMeshChecks") if isinstance(interface.get("regionMeshChecks"), dict) else {}
                commands = region_mesh_checks.get("commands") if isinstance(region_mesh_checks.get("commands"), list) else []
                if region_mesh_checks.get("script") != "AllmeshCheck":
                    issues.append("OpenFOAM CHT interface manifest must reference the generated AllmeshCheck script.")
                if "checkMesh -region fluid -allGeometry -allTopology" not in commands:
                    issues.append("OpenFOAM CHT interface manifest must list the fluid region checkMesh command.")
                if "checkMesh -region solid -allGeometry -allTopology" not in commands:
                    issues.append("OpenFOAM CHT interface manifest must list the solid region checkMesh command.")
                prism_plan = interface.get("prismLayerPlan") if isinstance(interface.get("prismLayerPlan"), dict) else {}
                if prism_plan.get("file") != "mesh/prism_layer_plan.json":
                    issues.append("OpenFOAM CHT interface manifest must reference `mesh/prism_layer_plan.json`.")
                if prism_plan.get("productionReady") is not False:
                    issues.append("OpenFOAM CHT interface prism-layer plan must remain productionReady=false.")
                if interface.get("productionReady") is False and not interface.get("blockingReasons"):
                    issues.append("OpenFOAM CHT non-production interface manifest must list blockingReasons.")

    if case.solver == "su2":
        if case.runCommand and case.runCommand != ["SU2_CFD", "case.cfg"]:
            issues.append("SU2 runnable cases must use `SU2_CFD case.cfg`.")
        cfg = files.get("case.cfg", "")
        preset_text = files.get("flowlab_su2_mode_preset.json", "")
        checklist_text = files.get("flowlab_su2_native_setup_checklist.json", "")
        matrix_text = files.get("flowlab_su2_capability_matrix.json", "")
        preset: dict | None = None
        try:
            loaded_preset = json.loads(preset_text)
            preset = loaded_preset if isinstance(loaded_preset, dict) else None
        except json.JSONDecodeError:
            issues.append("SU2 `flowlab_su2_mode_preset.json` must be valid JSON.")
        if preset is None and preset_text:
            issues.append("SU2 mode preset must be a JSON object.")
        if preset is not None:
            if preset.get("schema") != "flowlab.su2_mode_preset.v1":
                issues.append("SU2 mode preset has an unsupported schema.")
            if preset.get("advancedMode") != case.advancedMode:
                issues.append("SU2 mode preset advancedMode must match the generated case.")
            support_level = preset.get("supportLevel")
            supported_by_adapter = preset.get("supportedByAdapter")
            if case.status != "blocked" and preset.get("supportedByAdapter") is not True:
                issues.append("SU2 runnable case must have supportedByAdapter=true in the mode preset.")
            if supported_by_adapter is True and support_level != "starter-supported-single-zone":
                issues.append("SU2 supported mode preset must use supportLevel `starter-supported-single-zone`.")
            if supported_by_adapter is False and support_level != "blocked-export-only":
                issues.append("SU2 blocked export preset must use supportLevel `blocked-export-only`.")
            readiness_checks = preset.get("readinessChecks")
            if not isinstance(readiness_checks, list) or not readiness_checks:
                issues.append("SU2 mode preset must include readinessChecks.")
            else:
                failing_checks = [check for check in readiness_checks if isinstance(check, dict) and check.get("status") == "fail"]
                if failing_checks and not preset.get("blockingReasons"):
                    issues.append("SU2 mode preset must include blockingReasons for failing readiness checks.")
            native_setup_plan = preset.get("nativeSetupPlan")
            if not isinstance(native_setup_plan, dict) or not isinstance(native_setup_plan.get("manualNativeModules"), list):
                issues.append("SU2 mode preset must include nativeSetupPlan.manualNativeModules.")
            result_expectations = preset.get("resultExpectations")
            if (
                not isinstance(result_expectations, dict)
                or not isinstance(result_expectations.get("expectedPrimaryFields"), list)
                or not result_expectations.get("expectedPrimaryFields")
            ):
                issues.append("SU2 mode preset must include resultExpectations.expectedPrimaryFields.")
            if supported_by_adapter is True:
                if preset.get("requestedPhysicsResolved") is not True:
                    issues.append("SU2 supported mode preset must mark requestedPhysicsResolved true.")
            if supported_by_adapter is False:
                if preset.get("requestedPhysicsResolved") is not False:
                    issues.append("SU2 blocked export preset must mark requestedPhysicsResolved false.")
                if not preset.get("blockedOrManualModels") or not preset.get("manualSetupRequirements"):
                    issues.append("SU2 blocked export preset must list blockedOrManualModels and manualSetupRequirements.")
                if isinstance(native_setup_plan, dict) and not native_setup_plan.get("manualNativeModules"):
                    issues.append("SU2 blocked export preset must list nativeSetupPlan.manualNativeModules.")
            if case.status == "blocked" and case.runCommand and supported_by_adapter is not True:
                issues.append("SU2 blocked export-only mode must not include a solver run command.")
        checklist: dict | None = None
        try:
            loaded_checklist = json.loads(checklist_text)
            checklist = loaded_checklist if isinstance(loaded_checklist, dict) else None
        except json.JSONDecodeError:
            issues.append("SU2 `flowlab_su2_native_setup_checklist.json` must be valid JSON.")
        if checklist is None and checklist_text:
            issues.append("SU2 native setup checklist must be a JSON object.")
        if checklist is not None:
            if checklist.get("schema") != "flowlab.su2_native_setup_checklist.v1":
                issues.append("SU2 native setup checklist has an unsupported schema.")
            if checklist.get("advancedMode") != case.advancedMode:
                issues.append("SU2 native setup checklist advancedMode must match the generated case.")
            if not isinstance(checklist.get("generatedFiles"), list) or "case.cfg" not in checklist.get("generatedFiles", []):
                issues.append("SU2 native setup checklist must list generated case files.")
            if not isinstance(checklist.get("expectedPrimaryFields"), list) or not checklist.get("expectedPrimaryFields"):
                issues.append("SU2 native setup checklist must include expectedPrimaryFields.")
            if checklist.get("productionReady") is not False:
                issues.append("SU2 native setup checklist must remain productionReady=false until native review evidence exists.")
            if not isinstance(checklist.get("readinessItems"), list) or not checklist.get("readinessItems"):
                issues.append("SU2 native setup checklist must include readinessItems.")
            if preset is not None:
                if checklist.get("supportLevel") != preset.get("supportLevel"):
                    issues.append("SU2 native setup checklist supportLevel must match the mode preset.")
                if checklist.get("requestedPhysicsResolved") != preset.get("requestedPhysicsResolved"):
                    issues.append("SU2 native setup checklist requestedPhysicsResolved must match the mode preset.")
                preset_expected = preset.get("resultExpectations", {}).get("expectedPrimaryFields") if isinstance(preset.get("resultExpectations"), dict) else None
                if checklist.get("expectedPrimaryFields") != preset_expected:
                    issues.append("SU2 native setup checklist expectedPrimaryFields must match the mode preset.")
            if checklist.get("requestedPhysicsResolved") is False and not checklist.get("actionItems"):
                issues.append("SU2 unresolved native setup checklist must include actionItems.")
        capability_matrix: dict | None = None
        try:
            loaded_matrix = json.loads(matrix_text)
            capability_matrix = loaded_matrix if isinstance(loaded_matrix, dict) else None
        except json.JSONDecodeError:
            issues.append("SU2 capability matrix must be valid JSON.")
        if capability_matrix is None:
            issues.append("SU2 capability matrix must be a JSON object.")
        else:
            if capability_matrix.get("schema") != "flowlab.su2_capability_matrix.v1":
                issues.append("SU2 capability matrix has an unsupported schema.")
            if capability_matrix.get("activeMode") != case.advancedMode:
                issues.append("SU2 capability matrix activeMode must match the generated case.")
            if capability_matrix.get("productionReady") is not False:
                issues.append("SU2 capability matrix must remain productionReady=false.")
            entries = capability_matrix.get("entries")
            if not isinstance(entries, list) or not entries:
                issues.append("SU2 capability matrix must include entries.")
            else:
                entry_by_mode = {entry.get("advancedMode"): entry for entry in entries if isinstance(entry, dict)}
                required_modes = {
                    "incompressible-navier-stokes",
                    "heat-transfer",
                    "compressible-flow",
                    "multiphase-vof",
                    "cavitation",
                    "conjugate-heat-transfer",
                    "water-hammer",
                    "rigid-body-fluid-forces",
                }
                if not required_modes.issubset(entry_by_mode):
                    issues.append("SU2 capability matrix must cover all FlowLab advanced modes.")
                active_entry = entry_by_mode.get(case.advancedMode)
                if not isinstance(active_entry, dict) or active_entry.get("active") is not True:
                    issues.append("SU2 capability matrix must mark the generated advanced mode active.")
                if preset is not None and isinstance(active_entry, dict):
                    if active_entry.get("supportLevel") != preset.get("supportLevel"):
                        issues.append("SU2 capability matrix active entry supportLevel must match the mode preset.")
                    if active_entry.get("supportedByAdapter") != preset.get("supportedByAdapter"):
                        issues.append("SU2 capability matrix active entry supportedByAdapter must match the mode preset.")
                    if active_entry.get("requestedPhysicsResolved") != preset.get("requestedPhysicsResolved"):
                        issues.append("SU2 capability matrix active entry requestedPhysicsResolved must match the mode preset.")
                unresolved_entries = [
                    entry
                    for entry in entries
                    if isinstance(entry, dict) and entry.get("requestedPhysicsResolved") is False
                ]
                if not unresolved_entries:
                    issues.append("SU2 capability matrix must list blocked export-only advanced modes.")
                for entry in unresolved_entries:
                    if not entry.get("manualNativeModules") or not entry.get("expectedPrimaryFields") or not entry.get("blockingReasons"):
                        issues.append("SU2 capability matrix blocked entries must list manualNativeModules, expectedPrimaryFields, and blockingReasons.")
                        break
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("productionReady") is not False:
                        issues.append("SU2 capability matrix entries must remain productionReady=false.")
                        break
                summary = capability_matrix.get("summary") if isinstance(capability_matrix.get("summary"), dict) else {}
                if summary.get("modeCount") != len(entries):
                    issues.append("SU2 capability matrix summary modeCount must match entries.")
                starter_modes = summary.get("starterSupportedModes", [])
                for starter_mode in ("incompressible-navier-stokes", "heat-transfer", "compressible-flow"):
                    if starter_mode not in starter_modes:
                        issues.append("SU2 capability matrix summary must include all starter-supported single-zone modes.")
                        break
                blocked_modes = summary.get("blockedExportOnlyModes", [])
                for blocked_mode in ("multiphase-vof", "cavitation", "conjugate-heat-transfer", "water-hammer", "rigid-body-fluid-forces"):
                    if blocked_mode not in blocked_modes:
                        issues.append("SU2 capability matrix summary must include blocked export-only advanced modes.")
                        break
                handoff_modes = summary.get("handoffModes", [])
                for handoff_mode in ("water-hammer", "conjugate-heat-transfer"):
                    if handoff_mode not in handoff_modes:
                        issues.append("SU2 capability matrix summary must include handoff modes.")
                        break
        if "MESH_FILENAME= mesh/flowlab_mesh.su2" not in cfg:
            issues.append("SU2 `case.cfg` must reference `mesh/flowlab_mesh.su2`.")
        unsupported_export = "FLOWLAB_UNSUPPORTED_MODE= YES" in cfg
        if unsupported_export and preset is not None:
            if preset.get("supportedByAdapter") is not False:
                issues.append("SU2 unsupported-mode config must have supportedByAdapter=false in the mode preset.")
            if preset.get("supportLevel") != "blocked-export-only":
                issues.append("SU2 unsupported-mode config must use blocked-export-only support level.")
            review_template = "flowlab_su2_native_config_template.cfg"
            template_text = files.get(review_template)
            if review_template not in files:
                issues.append("SU2 blocked export-only mode must include `flowlab_su2_native_config_template.cfg`.")
            elif "FLOWLAB_TEMPLATE_ONLY= YES" not in template_text or "FLOWLAB_UNSUPPORTED_MODE= YES" not in template_text:
                issues.append("SU2 native config review template must keep FLOWLAB_TEMPLATE_ONLY and FLOWLAB_UNSUPPORTED_MODE guardrails.")
            if checklist is not None and review_template not in checklist.get("generatedFiles", []):
                issues.append("SU2 native setup checklist must list the native config review template.")
            native_setup_plan = preset.get("nativeSetupPlan")
            if isinstance(native_setup_plan, dict) and native_setup_plan.get("reviewTemplate") != review_template:
                issues.append("SU2 blocked export preset must reference the native config review template.")
            preflight_text = files.get("flowlab_su2_advanced_preflight.json")
            if not preflight_text:
                issues.append("SU2 blocked export-only mode must include `flowlab_su2_advanced_preflight.json`.")
            else:
                try:
                    preflight = json.loads(preflight_text)
                except json.JSONDecodeError:
                    issues.append("SU2 advanced preflight must be valid JSON.")
                    preflight = None
                if isinstance(preflight, dict):
                    if preflight.get("schema") != "flowlab.su2_advanced_preflight.v1":
                        issues.append("SU2 advanced preflight has an unsupported schema.")
                    if preflight.get("advancedMode") != case.advancedMode:
                        issues.append("SU2 advanced preflight advancedMode must match the generated case.")
                    if (
                        preflight.get("targetSolver") != "su2"
                        or preflight.get("productionReady") is not False
                        or preflight.get("nativeSu2Ready") is not False
                        or preflight.get("requestedPhysicsResolved") is not False
                    ):
                        issues.append("SU2 advanced preflight must remain non-production unresolved SU2 evidence.")
                    if preflight.get("status") != "blocked-export-only":
                        issues.append("SU2 advanced preflight status must be blocked-export-only.")
                    if preflight.get("supportLevel") != preset.get("supportLevel"):
                        issues.append("SU2 advanced preflight supportLevel must match the mode preset.")
                    if preflight.get("reviewTemplate") != review_template:
                        issues.append("SU2 advanced preflight must reference the guarded native config review template.")
                    handoff_artifacts = native_setup_plan.get("handoffArtifacts") if isinstance(native_setup_plan, dict) and isinstance(native_setup_plan.get("handoffArtifacts"), list) else []
                    if set(preflight.get("handoffArtifacts", [])) != set(handoff_artifacts):
                        issues.append("SU2 advanced preflight handoffArtifacts must match the mode preset.")
                    artifact_checks = preflight.get("artifactChecks")
                    if not isinstance(artifact_checks, list) or not artifact_checks:
                        issues.append("SU2 advanced preflight must include artifactChecks.")
                    elif any(not isinstance(check, dict) or check.get("status") != "pass" for check in artifact_checks):
                        issues.append("SU2 advanced preflight artifactChecks must pass for generated review artifacts.")
                    elif not {review_template, *handoff_artifacts}.issubset(
                        {check.get("artifact") for check in artifact_checks if isinstance(check, dict)}
                    ):
                        issues.append("SU2 advanced preflight artifactChecks must cover the guarded template and handoff artifacts.")
                    if not isinstance(preflight.get("unresolvedActions"), list) or not preflight.get("unresolvedActions"):
                        issues.append("SU2 advanced preflight must list unresolvedActions.")
                    readiness_checks = preflight.get("readinessChecks")
                    if not isinstance(readiness_checks, list) or not readiness_checks:
                        issues.append("SU2 advanced preflight must include readinessChecks.")
                    elif readiness_checks != preset.get("readinessChecks"):
                        issues.append("SU2 advanced preflight readinessChecks must match the mode preset.")
                    preset_expected_fields = (
                        preset.get("resultExpectations", {}).get("expectedPrimaryFields")
                        if isinstance(preset.get("resultExpectations"), dict)
                        else []
                    )
                    if preflight.get("expectedPrimaryFields") != preset_expected_fields:
                        issues.append("SU2 advanced preflight expectedPrimaryFields must match the mode preset.")
                    if not preflight.get("blockingReasons"):
                        issues.append("SU2 advanced preflight must include blockingReasons.")
            if checklist is not None and "flowlab_su2_advanced_preflight.json" not in checklist.get("generatedFiles", []):
                issues.append("SU2 native setup checklist must list the advanced preflight artifact.")
        if case.advancedMode == "water-hammer":
            if "flowlab_su2_water_hammer_handoff.json" not in files or "flowlab_su2_water_hammer_waveform.csv" not in files:
                issues.append("SU2 water-hammer case must include generated handoff JSON and waveform CSV.")
            else:
                try:
                    handoff = json.loads(files["flowlab_su2_water_hammer_handoff.json"])
                except json.JSONDecodeError:
                    issues.append("SU2 water-hammer handoff must be valid JSON.")
                else:
                    if handoff.get("schema") != "flowlab.water_hammer_handoff.v1":
                        issues.append("SU2 water-hammer handoff has an unsupported schema.")
                    if handoff.get("targetSolver") != "su2":
                        issues.append("SU2 water-hammer handoff must target su2.")
                    if handoff.get("productionReady") is not False or handoff.get("nativeSu2Ready") is not False:
                        issues.append("SU2 water-hammer handoff must remain non-production native setup evidence.")
                    su2_handoff = handoff.get("su2") if isinstance(handoff.get("su2"), dict) else {}
                    if su2_handoff.get("csv") != "flowlab_su2_water_hammer_waveform.csv":
                        issues.append("SU2 water-hammer handoff must reference the generated waveform CSV.")
                if not files["flowlab_su2_water_hammer_waveform.csv"].startswith("time,kinematicPressure,absolutePressure"):
                    issues.append("SU2 water-hammer waveform CSV has an unsupported header.")
        if case.advancedMode == "conjugate-heat-transfer":
            if "flowlab_su2_cht_handoff.json" not in files:
                issues.append("SU2 conjugate heat-transfer case must include generated CHT handoff JSON.")
            else:
                try:
                    cht_handoff = json.loads(files["flowlab_su2_cht_handoff.json"])
                except json.JSONDecodeError:
                    issues.append("SU2 CHT handoff must be valid JSON.")
                else:
                    if not isinstance(cht_handoff, dict):
                        issues.append("SU2 CHT handoff must be a JSON object.")
                    else:
                        if cht_handoff.get("schema") != "flowlab.su2_cht_handoff.v1":
                            issues.append("SU2 CHT handoff has an unsupported schema.")
                        if cht_handoff.get("targetSolver") != "su2":
                            issues.append("SU2 CHT handoff must target su2.")
                        if cht_handoff.get("productionReady") is not False or cht_handoff.get("nativeSu2Ready") is not False:
                            issues.append("SU2 CHT handoff must remain non-production native setup evidence.")
                        fluid_zone = cht_handoff.get("fluidZone") if isinstance(cht_handoff.get("fluidZone"), dict) else {}
                        solid_zone = cht_handoff.get("solidZone") if isinstance(cht_handoff.get("solidZone"), dict) else {}
                        interface = cht_handoff.get("interface") if isinstance(cht_handoff.get("interface"), dict) else {}
                        expected_fields = cht_handoff.get("expectedPrimaryFields")
                        if fluid_zone.get("solver") != "INC_NAVIER_STOKES":
                            issues.append("SU2 CHT handoff must define the fluid zone starter solver.")
                        if solid_zone.get("meshStatus") != "not generated":
                            issues.append("SU2 CHT handoff must state that the solid mesh is not generated.")
                        if interface.get("status") != "manual":
                            issues.append("SU2 CHT handoff must keep interface coupling manual.")
                        if not isinstance(expected_fields, list) or not {"solid_temperature", "heat_flux"}.issubset(set(expected_fields)):
                            issues.append("SU2 CHT handoff must list solid_temperature and heat_flux expected fields.")
        if case.advancedMode in {"multiphase-vof", "cavitation"}:
            phase_handoff_path = (
                "flowlab_su2_cavitation_handoff.json"
                if case.advancedMode == "cavitation"
                else "flowlab_su2_multiphase_handoff.json"
            )
            if phase_handoff_path not in files:
                issues.append("SU2 phase-mode case must include generated phase handoff JSON.")
            else:
                try:
                    phase_handoff = json.loads(files[phase_handoff_path])
                except json.JSONDecodeError:
                    issues.append("SU2 phase handoff must be valid JSON.")
                else:
                    if not isinstance(phase_handoff, dict):
                        issues.append("SU2 phase handoff must be a JSON object.")
                    else:
                        if phase_handoff.get("schema") != "flowlab.su2_phase_handoff.v1":
                            issues.append("SU2 phase handoff has an unsupported schema.")
                        if phase_handoff.get("targetSolver") != "su2":
                            issues.append("SU2 phase handoff must target su2.")
                        if phase_handoff.get("advancedMode") != case.advancedMode:
                            issues.append("SU2 phase handoff advancedMode must match the case.")
                        if phase_handoff.get("productionReady") is not False or phase_handoff.get("nativeSu2Ready") is not False:
                            issues.append("SU2 phase handoff must remain non-production native setup evidence.")
                        phases = phase_handoff.get("phases")
                        interface_setup = (
                            phase_handoff.get("interfaceSetup")
                            if isinstance(phase_handoff.get("interfaceSetup"), dict)
                            else {}
                        )
                        expected_fields = phase_handoff.get("expectedPrimaryFields")
                        if not isinstance(phases, list) or len(phases) < 2:
                            issues.append("SU2 phase handoff must define at least two phases.")
                        if interface_setup.get("status") != "manual":
                            issues.append("SU2 phase handoff must keep interface setup manual.")
                        required_fields = (
                            {"vapour_fraction", "cavitation_source"}
                            if case.advancedMode == "cavitation"
                            else {"phase_fraction", "interface_height"}
                        )
                        if not isinstance(expected_fields, list) or not required_fields.issubset(set(expected_fields)):
                            issues.append("SU2 phase handoff must list expected phase fields.")
                        if case.advancedMode == "cavitation":
                            cavitation_inputs = (
                                phase_handoff.get("cavitationInputs")
                                if isinstance(phase_handoff.get("cavitationInputs"), dict)
                                else {}
                            )
                            if "saturationPressure" not in cavitation_inputs:
                                issues.append("SU2 cavitation handoff must include saturationPressure.")
        if case.advancedMode == "rigid-body-fluid-forces":
            if "flowlab_su2_rigid_body_handoff.json" not in files:
                issues.append("SU2 rigid-body case must include generated rigid-body handoff JSON.")
            else:
                try:
                    rigid_handoff = json.loads(files["flowlab_su2_rigid_body_handoff.json"])
                except json.JSONDecodeError:
                    issues.append("SU2 rigid-body handoff must be valid JSON.")
                else:
                    if not isinstance(rigid_handoff, dict):
                        issues.append("SU2 rigid-body handoff must be a JSON object.")
                    else:
                        if rigid_handoff.get("schema") != "flowlab.su2_rigid_body_handoff.v1":
                            issues.append("SU2 rigid-body handoff has an unsupported schema.")
                        if rigid_handoff.get("targetSolver") != "su2":
                            issues.append("SU2 rigid-body handoff must target su2.")
                        if rigid_handoff.get("productionReady") is not False or rigid_handoff.get("nativeSu2Ready") is not False:
                            issues.append("SU2 rigid-body handoff must remain non-production native setup evidence.")
                        coupling_intent = (
                            rigid_handoff.get("couplingIntent")
                            if isinstance(rigid_handoff.get("couplingIntent"), dict)
                            else {}
                        )
                        motion_setup = (
                            rigid_handoff.get("motionSetup")
                            if isinstance(rigid_handoff.get("motionSetup"), dict)
                            else {}
                        )
                        expected_fields = rigid_handoff.get("expectedPrimaryFields")
                        if coupling_intent.get("status") != "manual":
                            issues.append("SU2 rigid-body handoff must keep coupling setup manual.")
                        if coupling_intent.get("preferredCurrentSandbox") != "mujoco":
                            issues.append("SU2 rigid-body handoff must identify MuJoCo as the current sandbox.")
                        if motion_setup.get("status") != "manual":
                            issues.append("SU2 rigid-body handoff must keep motion setup manual.")
                        if not isinstance(expected_fields, list) or not {"body_force", "moment"}.issubset(set(expected_fields)):
                            issues.append("SU2 rigid-body handoff must list body_force and moment expected fields.")
        if not unsupported_export and not any(marker in cfg for marker in ("MARKER_WALL=", "MARKER_HEATFLUX=", "MARKER_ISOTHERMAL=")):
            issues.append("SU2 `case.cfg` is missing wall boundary markers.")
        if case.status != "blocked" and "FLOWLAB_UNSUPPORTED_MODE= YES" in cfg:
            issues.append("SU2 unsupported-mode config must be marked blocked.")

    if case.solver == "code-saturne":
        if "MESH/flowlab_mesh.msh" not in files.get("DATA/setup.xml", ""):
            issues.append("Code_Saturne `DATA/setup.xml` must reference `MESH/flowlab_mesh.msh`.")
        preset_text = files.get("DATA/flowlab_physics_preset.json", "")
        preset: dict | None = None
        try:
            loaded_preset = json.loads(preset_text)
            preset = loaded_preset if isinstance(loaded_preset, dict) else None
        except json.JSONDecodeError:
            issues.append("Code_Saturne `DATA/flowlab_physics_preset.json` must be valid JSON.")
        else:
            if preset is None:
                issues.append("Code_Saturne physics preset must be a JSON object.")
            elif preset.get("schema") != "flowlab.code_saturne_physics_preset.v1":
                issues.append("Code_Saturne physics preset has an unsupported schema.")
            if preset is None:
                pass
            elif preset.get("advancedMode") != case.advancedMode:
                issues.append("Code_Saturne physics preset advancedMode must match the generated case.")
            support_level = preset.get("supportLevel") if preset is not None else None
            if support_level not in {"starter-supported", "metadata-plus-surrogate", "metadata-plus-handoff", "metadata-only"}:
                issues.append("Code_Saturne physics preset has an unsupported supportLevel.")
            readiness_checks = preset.get("readinessChecks") if preset is not None else None
            if not isinstance(readiness_checks, list) or not readiness_checks:
                issues.append("Code_Saturne physics preset must include readinessChecks.")
            else:
                failing_checks = [check for check in readiness_checks if isinstance(check, dict) and check.get("status") == "fail"]
                if failing_checks and preset is not None and not preset.get("blockingReasons"):
                    issues.append("Code_Saturne physics preset must include blockingReasons for failing readiness checks.")
            native_plan = preset.get("nativeSetupPlan") if preset is not None else None
            if not isinstance(native_plan, dict) or "manualNativeModules" not in native_plan:
                issues.append("Code_Saturne physics preset must include nativeSetupPlan.manualNativeModules.")
            result_expectations = preset.get("resultExpectations") if preset is not None and isinstance(preset.get("resultExpectations"), dict) else {}
            expected_fields = result_expectations.get("expectedPrimaryFields")
            if not isinstance(expected_fields, list) or not expected_fields:
                issues.append("Code_Saturne physics preset must include resultExpectations.expectedPrimaryFields.")
            if preset is not None:
                setup_models = preset.get("setupXmlModels") if isinstance(preset.get("setupXmlModels"), dict) else {}
                expected_turbulence_model = "off" if case.advancedMode in {"incompressible-navier-stokes", "water-hammer"} else "k-epsilon"
                if setup_models.get("turbulence") != expected_turbulence_model:
                    issues.append(f"Code_Saturne physics preset must keep setupXmlModels.turbulence=`{expected_turbulence_model}` for {case.advancedMode}.")
                turbulence_plan = preset.get("turbulencePlan") if isinstance(preset.get("turbulencePlan"), dict) else {}
                if not turbulence_plan:
                    issues.append("Code_Saturne physics preset must include turbulencePlan.")
                else:
                    expected_status = "laminar-starter" if expected_turbulence_model == "off" else "rans-starter"
                    if turbulence_plan.get("schema") != "flowlab.code_saturne_turbulence_plan.v1":
                        issues.append("Code_Saturne turbulencePlan has an unsupported schema.")
                    if turbulence_plan.get("model") != expected_turbulence_model:
                        issues.append("Code_Saturne turbulencePlan model must match setupXmlModels.turbulence.")
                    if turbulence_plan.get("starterStatus") != expected_status:
                        issues.append(f"Code_Saturne turbulencePlan starterStatus must be {expected_status}.")
                    if turbulence_plan.get("productionReady") is not False:
                        issues.append("Code_Saturne turbulencePlan must remain productionReady=false until native turbulence evidence exists.")
                    if not isinstance(turbulence_plan.get("requiredEvidence"), list) or not turbulence_plan.get("requiredEvidence"):
                        issues.append("Code_Saturne turbulencePlan must list requiredEvidence.")
                    unresolved_models = turbulence_plan.get("unresolvedModels")
                    if not isinstance(unresolved_models, list) or not {"LES", "DNS"}.issubset(set(unresolved_models)):
                        issues.append("Code_Saturne turbulencePlan must keep LES and DNS listed as unresolved models.")
            if support_level == "starter-supported":
                if case.runCommand != ["code_saturne", "run"]:
                    issues.append("Code_Saturne starter-supported case must use `code_saturne run`.")
                if preset is not None and (preset.get("supportedByAdapter") is not True or preset.get("requestedPhysicsResolved") is not True):
                    issues.append("Code_Saturne starter-supported presets must mark supportedByAdapter and requestedPhysicsResolved true.")
                if preset is not None and preset.get("productionReady") is not False:
                    issues.append("Code_Saturne starter-supported presets must remain productionReady=false until native review evidence exists.")
            elif support_level in {"metadata-plus-surrogate", "metadata-plus-handoff", "metadata-only"}:
                unresolved_requires_blocked_execution = support_level != "metadata-plus-surrogate"
                if unresolved_requires_blocked_execution and case.runCommand:
                    issues.append("Code_Saturne unresolved physics cases must not include a runnable solver command.")
                if unresolved_requires_blocked_execution and case.status != "blocked":
                    issues.append("Code_Saturne unresolved physics cases must be marked blocked.")
                if preset is not None and (preset.get("supportedByAdapter") is not False or preset.get("requestedPhysicsResolved") is not False):
                    issues.append("Code_Saturne unresolved physics presets must mark supportedByAdapter and requestedPhysicsResolved false.")
                if preset is not None and (not preset.get("blockedOrManualModels") or not preset.get("manualSetupRequirements")):
                    issues.append("Code_Saturne unresolved physics presets must list blockedOrManualModels and manualSetupRequirements.")
                review_template = "DATA/flowlab_native_physics_review.py"
                template_text = files.get(review_template)
                if review_template not in files:
                    issues.append("Code_Saturne unresolved physics preset must include `DATA/flowlab_native_physics_review.py`.")
                elif (
                    "FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True" not in template_text
                    or "FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False" not in template_text
                    or "FLOWLAB_PRODUCTION_READY = False" not in template_text
                ):
                    issues.append("Code_Saturne native physics review template must keep unresolved-physics guardrails.")
                if isinstance(native_plan, dict) and native_plan.get("reviewTemplate") != review_template:
                    issues.append("Code_Saturne unresolved physics preset must reference the native physics review template.")
            if preset is not None and case.advancedMode == "heat-transfer":
                setup_models = preset.get("setupXmlModels") if isinstance(preset.get("setupXmlModels"), dict) else {}
                if setup_models.get("thermalScalar") != "temperature_celsius":
                    issues.append("Code_Saturne heat-transfer preset must keep setupXmlModels.thermalScalar=`temperature_celsius`.")
                if not isinstance(expected_fields, list) or "temperature" not in expected_fields:
                    issues.append("Code_Saturne heat-transfer preset must include temperature in expectedPrimaryFields.")
                thermal_starter = preset.get("thermalStarter") if isinstance(preset.get("thermalStarter"), dict) else {}
                if thermal_starter.get("scalarName") != "temperature_celsius":
                    issues.append("Code_Saturne heat-transfer preset must include thermalStarter scalarName temperature_celsius.")
                boundary_plan = preset.get("thermalBoundaryPlan") if isinstance(preset.get("thermalBoundaryPlan"), dict) else {}
                exclusions = boundary_plan.get("excludedPhysics") if isinstance(boundary_plan.get("excludedPhysics"), list) else []
                for excluded in ("fluid-solid conjugate heat transfer", "radiation", "phase change"):
                    if excluded not in exclusions:
                        issues.append(f"Code_Saturne heat-transfer thermalBoundaryPlan must exclude {excluded}.")
            if preset is not None and case.advancedMode == "compressible-flow":
                handoff_path = "DATA/flowlab_compressible_handoff.json"
                if handoff_path not in files:
                    issues.append("Code_Saturne compressible-flow case must include generated compressible handoff JSON.")
                else:
                    try:
                        compressible_handoff = json.loads(files[handoff_path])
                    except json.JSONDecodeError:
                        issues.append("Code_Saturne compressible handoff must be valid JSON.")
                    else:
                        if not isinstance(compressible_handoff, dict):
                            issues.append("Code_Saturne compressible handoff must be a JSON object.")
                        else:
                            if compressible_handoff.get("schema") != "flowlab.code_saturne_compressible_handoff.v1":
                                issues.append("Code_Saturne compressible handoff has an unsupported schema.")
                            if compressible_handoff.get("targetSolver") != "code-saturne":
                                issues.append("Code_Saturne compressible handoff must target code-saturne.")
                            if compressible_handoff.get("advancedMode") != "compressible-flow":
                                issues.append("Code_Saturne compressible handoff advancedMode must be compressible-flow.")
                            if (
                                compressible_handoff.get("productionReady") is not False
                                or compressible_handoff.get("nativeCodeSaturneReady") is not False
                            ):
                                issues.append("Code_Saturne compressible handoff must remain non-production native setup evidence.")
                            starter_surrogate = (
                                compressible_handoff.get("starterSurrogate")
                                if isinstance(compressible_handoff.get("starterSurrogate"), dict)
                                else {}
                            )
                            required_modules = compressible_handoff.get("requiredNativeModules")
                            thermodynamic_setup = (
                                compressible_handoff.get("thermodynamicSetup")
                                if isinstance(compressible_handoff.get("thermodynamicSetup"), dict)
                                else {}
                            )
                            handoff_expected_fields = compressible_handoff.get("expectedPrimaryFields")
                            if starter_surrogate.get("status") != "pressure-based-incompressible-surrogate":
                                issues.append("Code_Saturne compressible handoff must identify the pressure-based surrogate.")
                            if not isinstance(required_modules, list) or not {"compressible flow module", "equation of state"}.issubset(set(required_modules)):
                                issues.append("Code_Saturne compressible handoff must list required native compressible modules.")
                            if thermodynamic_setup.get("status") != "manual":
                                issues.append("Code_Saturne compressible handoff must keep thermodynamic setup manual.")
                            if not isinstance(handoff_expected_fields, list) or not {"density", "temperature", "mach_number"}.issubset(set(handoff_expected_fields)):
                                issues.append("Code_Saturne compressible handoff must list density, temperature, and mach_number expected fields.")
            matrix_text = files.get("DATA/flowlab_code_saturne_capability_matrix.json", "")
            try:
                loaded_matrix = json.loads(matrix_text)
                capability_matrix = loaded_matrix if isinstance(loaded_matrix, dict) else None
            except json.JSONDecodeError:
                issues.append("Code_Saturne capability matrix must be valid JSON.")
                capability_matrix = None
            if capability_matrix is None:
                issues.append("Code_Saturne capability matrix must be a JSON object.")
            else:
                if capability_matrix.get("schema") != "flowlab.code_saturne_capability_matrix.v1":
                    issues.append("Code_Saturne capability matrix has an unsupported schema.")
                if capability_matrix.get("activeMode") != case.advancedMode:
                    issues.append("Code_Saturne capability matrix activeMode must match the generated case.")
                if capability_matrix.get("productionReady") is not False:
                    issues.append("Code_Saturne capability matrix must remain productionReady=false.")
                entries = capability_matrix.get("entries")
                if not isinstance(entries, list) or not entries:
                    issues.append("Code_Saturne capability matrix must include entries.")
                else:
                    entry_by_mode = {entry.get("advancedMode"): entry for entry in entries if isinstance(entry, dict)}
                    required_modes = {
                        "incompressible-navier-stokes",
                        "heat-transfer",
                        "compressible-flow",
                        "multiphase-vof",
                        "cavitation",
                        "conjugate-heat-transfer",
                        "water-hammer",
                        "rigid-body-fluid-forces",
                    }
                    if not required_modes.issubset(entry_by_mode):
                        issues.append("Code_Saturne capability matrix must cover all FlowLab advanced modes.")
                    active_entry = entry_by_mode.get(case.advancedMode)
                    if not isinstance(active_entry, dict) or active_entry.get("active") is not True:
                        issues.append("Code_Saturne capability matrix must mark the generated advanced mode active.")
                    for mode_name, entry in entry_by_mode.items():
                        if not isinstance(entry, dict):
                            continue
                        expected_turbulence_model = "off" if mode_name in {"incompressible-navier-stokes", "water-hammer"} else "k-epsilon"
                        expected_status = "laminar-starter" if expected_turbulence_model == "off" else "rans-starter"
                        if entry.get("turbulenceModel") != expected_turbulence_model or entry.get("turbulenceStarterStatus") != expected_status:
                            issues.append("Code_Saturne capability matrix entries must include turbulenceModel and turbulenceStarterStatus.")
                            break
                    unresolved_entries = [
                        entry
                        for entry in entries
                        if isinstance(entry, dict) and entry.get("requestedPhysicsResolved") is False
                    ]
                    if not unresolved_entries:
                        issues.append("Code_Saturne capability matrix must list unresolved advanced modes.")
                    for entry in unresolved_entries:
                        if not entry.get("manualNativeModules") or not entry.get("expectedPrimaryFields") or not entry.get("blockingReasons"):
                            issues.append("Code_Saturne capability matrix unresolved entries must list manualNativeModules, expectedPrimaryFields, and blockingReasons.")
                            break
                    summary = capability_matrix.get("summary") if isinstance(capability_matrix.get("summary"), dict) else {}
                    if "incompressible-navier-stokes" not in summary.get("starterSupportedModes", []):
                        issues.append("Code_Saturne capability matrix summary must include starter-supported incompressible mode.")
                    for unresolved_mode in ("compressible-flow", "multiphase-vof", "cavitation", "conjugate-heat-transfer", "water-hammer", "rigid-body-fluid-forces"):
                        if unresolved_mode not in summary.get("unresolvedModes", []):
                            issues.append("Code_Saturne capability matrix summary must include unresolved advanced modes.")
                            break
            if preset is not None and case.advancedMode == "water-hammer":
                if "DATA/flowlab_water_hammer_handoff.json" not in files or "DATA/flowlab_water_hammer_waveform.csv" not in files:
                    issues.append("Code_Saturne water-hammer case must include generated handoff JSON and waveform CSV.")
                else:
                    try:
                        handoff = json.loads(files["DATA/flowlab_water_hammer_handoff.json"])
                    except json.JSONDecodeError:
                        issues.append("Code_Saturne water-hammer handoff must be valid JSON.")
                    else:
                        if handoff.get("schema") != "flowlab.water_hammer_handoff.v1":
                            issues.append("Code_Saturne water-hammer handoff has an unsupported schema.")
                        if handoff.get("targetSolver") != "code-saturne":
                            issues.append("Code_Saturne water-hammer handoff must target code-saturne.")
                        if handoff.get("productionReady") is not False or handoff.get("nativeCodeSaturneReady") is not False:
                            issues.append("Code_Saturne water-hammer handoff must remain non-production native setup evidence.")
                        code_saturne = handoff.get("codeSaturne") if isinstance(handoff.get("codeSaturne"), dict) else {}
                        if code_saturne.get("csv") != "DATA/flowlab_water_hammer_waveform.csv":
                            issues.append("Code_Saturne water-hammer handoff must reference the generated waveform CSV.")
                    if not files["DATA/flowlab_water_hammer_waveform.csv"].startswith("time,kinematicPressure,absolutePressure"):
                        issues.append("Code_Saturne water-hammer waveform CSV has an unsupported header.")
            if preset is not None and case.advancedMode == "conjugate-heat-transfer":
                if "DATA/flowlab_cht_handoff.json" not in files:
                    issues.append("Code_Saturne conjugate heat-transfer case must include generated CHT handoff JSON.")
                else:
                    try:
                        cht_handoff = json.loads(files["DATA/flowlab_cht_handoff.json"])
                    except json.JSONDecodeError:
                        issues.append("Code_Saturne CHT handoff must be valid JSON.")
                    else:
                        if not isinstance(cht_handoff, dict):
                            issues.append("Code_Saturne CHT handoff must be a JSON object.")
                        else:
                            if cht_handoff.get("schema") != "flowlab.code_saturne_cht_handoff.v1":
                                issues.append("Code_Saturne CHT handoff has an unsupported schema.")
                            if cht_handoff.get("targetSolver") != "code-saturne":
                                issues.append("Code_Saturne CHT handoff must target code-saturne.")
                            if cht_handoff.get("productionReady") is not False or cht_handoff.get("nativeCodeSaturneReady") is not False:
                                issues.append("Code_Saturne CHT handoff must remain non-production native setup evidence.")
                            fluid_domain = cht_handoff.get("fluidDomain") if isinstance(cht_handoff.get("fluidDomain"), dict) else {}
                            solid_domain = cht_handoff.get("solidDomain") if isinstance(cht_handoff.get("solidDomain"), dict) else {}
                            interface_coupling = (
                                cht_handoff.get("interfaceCoupling")
                                if isinstance(cht_handoff.get("interfaceCoupling"), dict)
                                else {}
                            )
                            expected_fields = cht_handoff.get("expectedPrimaryFields")
                            if fluid_domain.get("thermalScalar") != "temperature_celsius":
                                issues.append("Code_Saturne CHT handoff must define the starter fluid thermal scalar.")
                            if solid_domain.get("meshStatus") != "not generated":
                                issues.append("Code_Saturne CHT handoff must state that the solid mesh is not generated.")
                            if interface_coupling.get("status") != "manual":
                                issues.append("Code_Saturne CHT handoff must keep interface coupling manual.")
                            if not isinstance(expected_fields, list) or not {"solid_temperature", "heat_flux"}.issubset(set(expected_fields)):
                                issues.append("Code_Saturne CHT handoff must list solid_temperature and heat_flux expected fields.")
            if preset is not None and case.advancedMode in {"multiphase-vof", "cavitation"}:
                phase_path = (
                    "DATA/flowlab_cavitation_handoff.json"
                    if case.advancedMode == "cavitation"
                    else "DATA/flowlab_multiphase_handoff.json"
                )
                if phase_path not in files:
                    issues.append("Code_Saturne phase-physics case must include generated phase handoff JSON.")
                else:
                    try:
                        phase_handoff = json.loads(files[phase_path])
                    except json.JSONDecodeError:
                        issues.append("Code_Saturne phase handoff must be valid JSON.")
                    else:
                        if not isinstance(phase_handoff, dict):
                            issues.append("Code_Saturne phase handoff must be a JSON object.")
                        else:
                            if phase_handoff.get("schema") != "flowlab.code_saturne_phase_handoff.v1":
                                issues.append("Code_Saturne phase handoff has an unsupported schema.")
                            if phase_handoff.get("targetSolver") != "code-saturne":
                                issues.append("Code_Saturne phase handoff must target code-saturne.")
                            if phase_handoff.get("advancedMode") != case.advancedMode:
                                issues.append("Code_Saturne phase handoff advancedMode must match the generated case.")
                            if phase_handoff.get("productionReady") is not False or phase_handoff.get("nativeCodeSaturneReady") is not False:
                                issues.append("Code_Saturne phase handoff must remain non-production native setup evidence.")
                            phases = phase_handoff.get("phases")
                            interface_setup = (
                                phase_handoff.get("interfaceSetup")
                                if isinstance(phase_handoff.get("interfaceSetup"), dict)
                                else {}
                            )
                            expected_fields = phase_handoff.get("expectedPrimaryFields")
                            if not isinstance(phases, list) or len(phases) < 2:
                                issues.append("Code_Saturne phase handoff must list at least two phases.")
                            if interface_setup.get("status") != "manual":
                                issues.append("Code_Saturne phase handoff must keep interface setup manual.")
                            required_fields = (
                                {"vapour_fraction", "cavitation_source"}
                                if case.advancedMode == "cavitation"
                                else {"phase_fraction", "interface_height"}
                            )
                            if not isinstance(expected_fields, list) or not required_fields.issubset(set(expected_fields)):
                                issues.append("Code_Saturne phase handoff must list the expected phase fields.")
                            if case.advancedMode == "cavitation":
                                cavitation_inputs = (
                                    phase_handoff.get("cavitationInputs")
                                    if isinstance(phase_handoff.get("cavitationInputs"), dict)
                                    else {}
                                )
                                if "saturationPressure" not in cavitation_inputs:
                                    issues.append("Code_Saturne cavitation handoff must include saturationPressure.")
            if preset is not None and case.advancedMode == "rigid-body-fluid-forces":
                handoff_path = "DATA/flowlab_rigid_body_handoff.json"
                if handoff_path not in files:
                    issues.append("Code_Saturne rigid-body case must include generated rigid-body handoff JSON.")
                else:
                    try:
                        rigid_handoff = json.loads(files[handoff_path])
                    except json.JSONDecodeError:
                        issues.append("Code_Saturne rigid-body handoff must be valid JSON.")
                    else:
                        if not isinstance(rigid_handoff, dict):
                            issues.append("Code_Saturne rigid-body handoff must be a JSON object.")
                        else:
                            if rigid_handoff.get("schema") != "flowlab.code_saturne_rigid_body_handoff.v1":
                                issues.append("Code_Saturne rigid-body handoff has an unsupported schema.")
                            if rigid_handoff.get("targetSolver") != "code-saturne":
                                issues.append("Code_Saturne rigid-body handoff must target code-saturne.")
                            if rigid_handoff.get("advancedMode") != "rigid-body-fluid-forces":
                                issues.append("Code_Saturne rigid-body handoff advancedMode must be rigid-body-fluid-forces.")
                            if (
                                rigid_handoff.get("productionReady") is not False
                                or rigid_handoff.get("nativeCodeSaturneReady") is not False
                            ):
                                issues.append("Code_Saturne rigid-body handoff must remain non-production native setup evidence.")
                            coupling_intent = (
                                rigid_handoff.get("couplingIntent")
                                if isinstance(rigid_handoff.get("couplingIntent"), dict)
                                else {}
                            )
                            motion_setup = (
                                rigid_handoff.get("motionSetup")
                                if isinstance(rigid_handoff.get("motionSetup"), dict)
                                else {}
                            )
                            expected_fields = rigid_handoff.get("expectedPrimaryFields")
                            if coupling_intent.get("status") != "manual":
                                issues.append("Code_Saturne rigid-body handoff must keep coupling setup manual.")
                            if coupling_intent.get("preferredCurrentSandbox") != "mujoco":
                                issues.append("Code_Saturne rigid-body handoff must identify MuJoCo as the current sandbox.")
                            if motion_setup.get("status") != "manual":
                                issues.append("Code_Saturne rigid-body handoff must keep motion setup manual.")
                            if not isinstance(expected_fields, list) or not {"body_force", "moment"}.issubset(set(expected_fields)):
                                issues.append("Code_Saturne rigid-body handoff must list body_force and moment expected fields.")
        checklist_text = files.get("DATA/flowlab_native_setup_checklist.json", "")
        try:
            checklist = json.loads(checklist_text)
        except json.JSONDecodeError:
            issues.append("Code_Saturne `DATA/flowlab_native_setup_checklist.json` must be valid JSON.")
        else:
            if checklist.get("schema") != "flowlab.code_saturne_native_setup_checklist.v1":
                issues.append("Code_Saturne native setup checklist has an unsupported schema.")
            if checklist.get("advancedMode") != case.advancedMode:
                issues.append("Code_Saturne native setup checklist advancedMode must match the generated case.")
            if not isinstance(checklist.get("generatedFiles"), list) or "DATA/setup.xml" not in checklist.get("generatedFiles", []):
                issues.append("Code_Saturne native setup checklist must list generated native case files.")
            expected_fields = checklist.get("expectedPrimaryFields")
            if not isinstance(expected_fields, list) or not expected_fields:
                issues.append("Code_Saturne native setup checklist must include expectedPrimaryFields.")
            if checklist.get("productionReady") is not False:
                issues.append("Code_Saturne native setup checklist must remain productionReady=false until native review evidence exists.")
            if preset is not None:
                checklist_turbulence = checklist.get("turbulencePlan") if isinstance(checklist.get("turbulencePlan"), dict) else {}
                preset_turbulence = preset.get("turbulencePlan") if isinstance(preset.get("turbulencePlan"), dict) else {}
                if not checklist_turbulence:
                    issues.append("Code_Saturne native setup checklist must include turbulencePlan.")
                elif checklist_turbulence.get("model") != preset_turbulence.get("model") or checklist_turbulence.get("starterStatus") != preset_turbulence.get("starterStatus"):
                    issues.append("Code_Saturne native setup checklist turbulencePlan must match the physics preset.")
            if case.advancedMode == "heat-transfer":
                thermal_starter = checklist.get("thermalStarter") if isinstance(checklist.get("thermalStarter"), dict) else {}
                if thermal_starter.get("scalarName") != "temperature_celsius":
                    issues.append("Code_Saturne heat-transfer checklist must include thermalStarter scalarName temperature_celsius.")
            if checklist.get("requestedPhysicsResolved") is False and not checklist.get("actionItems"):
                issues.append("Code_Saturne unresolved native setup checklist must include actionItems.")
            if checklist.get("requestedPhysicsResolved") is False and "DATA/flowlab_native_physics_review.py" not in checklist.get("generatedFiles", []):
                issues.append("Code_Saturne unresolved native setup checklist must list the native physics review template.")
        user_scripts = files.get("DATA/cs_user_scripts.py", "")
        if "MESH" not in user_scripts or "flowlab_mesh.msh" not in user_scripts:
            issues.append("Code_Saturne `DATA/cs_user_scripts.py` must pin the generated mesh.")
        boundary_source = files.get("SRC/cs_user_boundary_conditions.f90", "")
        if "cs_f_user_boundary_conditions" not in boundary_source:
            issues.append("Code_Saturne case must include a user boundary-condition subroutine.")

    if case.solver == "mujoco":
        if not case.runCommand or case.runCommand[1:] != ["run_mujoco.py"]:
            issues.append("MuJoCo case must run `run_mujoco.py` through the selected Python command.")
        if "<mujoco" not in files.get("model.xml", ""):
            issues.append("MuJoCo `model.xml` is missing an MJCF root element.")
        if "mujoco.MjModel.from_xml_path" not in files.get("run_mujoco.py", ""):
            issues.append("MuJoCo runner must load the generated MJCF model.")

    return issues


def _mujoco_python_command() -> tuple[str, bool]:
    command = adapters._python_command()
    if command:
        return command, True
    configured = adapters._configured_mujoco_python()
    return configured or "python3", False


def runtime_diagnostics() -> list[SolverRuntimeStatus]:
    docker_available = adapters._docker_available()
    statuses: list[SolverRuntimeStatus] = []

    for solver in RUNTIME_SOLVER_ORDER:
        if solver == "instant-1d":
            statuses.append(
                SolverRuntimeStatus(
                    solver="instant-1d",
                    runnable=True,
                    preferredExecution="browser",
                    blockers=[],
                    notes=["Instant 1D hydraulics run in the browser and do not require a local CFD solver."],
                )
            )
            continue

        image = _docker_image(solver)
        command = REQUIRED_NATIVE_COMMANDS.get(solver)
        if solver == "mujoco":
            command, native_available = _mujoco_python_command()
        else:
            native_available = bool(command and adapters._command_exists(command))
        su2_home = adapters._su2_home() if solver == "su2" else None
        if solver == "su2" and su2_home:
            image = SU2_DOCKER_IMAGE
        if solver == "code-saturne":
            image = adapters._code_saturne_image()

        python_module = REQUIRED_PYTHON_MODULES.get(solver)
        python_module_available = None
        if python_module and native_available:
            if solver == "mujoco":
                python_module_available = adapters._python_module_exists_for_command(command, python_module)
            else:
                python_module_available = adapters._python_module_exists(python_module)

        solver_docker_available = bool(image and docker_available and (solver != "su2" or su2_home))
        native_runnable = native_available and python_module_available is not False
        runnable = solver_docker_available or native_runnable
        if solver_docker_available:
            preferred_execution = "docker"
        elif native_runnable:
            preferred_execution = "native"
        else:
            preferred_execution = "none"

        blockers: list[str] = []
        notes: list[str] = []
        if solver == "su2":
            if su2_home and docker_available:
                notes.append(f"Docker daemon is available; mounting FLOWLAB_SU2_HOME `{su2_home}` into `{SU2_DOCKER_IMAGE}`.")
            elif su2_home and not docker_available:
                blockers.append(f"FLOWLAB_SU2_HOME points to `{su2_home}`, but Docker daemon is unavailable for container execution.")
            elif not native_available:
                blockers.append("Set FLOWLAB_SU2_HOME to an official SU2 binary release, or install `SU2_CFD` on PATH.")
        elif solver == "code-saturne":
            if image and docker_available:
                notes.append(f"Docker daemon is available for configured Code_Saturne image `{image}`.")
            elif image and not docker_available:
                blockers.append(f"FLOWLAB_CODE_SATURNE_IMAGE is set to `{image}`, but Docker daemon is unavailable.")
            elif not native_available:
                blockers.append(
                    "Set FLOWLAB_CODE_SATURNE_IMAGE to a Docker image containing `code_saturne`, "
                    "or install native `code_saturne` on PATH."
                )
        elif image:
            if not docker_available:
                blockers.append(f"Docker image `{image}` cannot run because Docker daemon is unavailable.")
            else:
                notes.append(f"Docker daemon is available for image `{image}`.")
        else:
            notes.append("No Docker image is configured for this adapter; native execution is required.")

        if command and not native_available:
            blockers.append(f"Native command `{command}` was not found on PATH.")
        if python_module:
            if native_available and python_module_available is False:
                blockers.append(f"Python module `{python_module}` is not installed for native execution.")
            elif native_available and python_module_available is True:
                notes.append(f"Python module `{python_module}` is available.")

        statuses.append(
            SolverRuntimeStatus(
                solver=solver,  # type: ignore[arg-type]
                runnable=runnable,
                preferredExecution=preferred_execution,  # type: ignore[arg-type]
                dockerImage=image,
                dockerAvailable=docker_available if image else None,
                nativeCommand=command,
                nativeAvailable=native_available,
                pythonModule=python_module,
                pythonModuleAvailable=python_module_available,
                blockers=blockers,
                notes=notes,
            )
        )

    return statuses


def openfoam_diagnostics_smoke_case() -> SolverCase:
    project = {
        "name": "OpenFOAM diagnostics smoke",
        "solver": {"meshResolution": "coarse"},
        "nodes": {
            "source": {"id": "source", "type": "source", "pressure": 250000.0, "position": {"x": 0, "y": 0}},
            "probe_mid": {"id": "probe_mid", "type": "probe", "position": {"x": 50, "y": 0}, "z": 0.005},
            "sink": {"id": "sink", "type": "sink", "pressure": 101325.0, "position": {"x": 100, "y": 0}},
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
    return adapters.generate_case(
        adapters.CaseRequest.model_construct(
            project=project,
            solver="openfoam",
            advancedMode="incompressible-navier-stokes",
        )
    )


def _openfoam_has_base_mesh(case_dir: Path) -> bool:
    poly_mesh = case_dir / "constant" / "polyMesh"
    required = ("points", "faces", "owner", "boundary")
    return all((poly_mesh / name).is_file() for name in required)


def _openfoam_docker_command(case_dir: Path, command: list[str]) -> list[str]:
    image = _docker_image("openfoam")
    if image is None:
        raise RuntimeError("OpenFOAM Docker image is not configured")
    shell_command = shlex.join(command)
    env_setup = DOCKER_ENV_SETUP.get("openfoam")
    if env_setup:
        shell_command = f"{env_setup} && {shell_command}"
    docker_command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{case_dir}:/case",
        "-w",
        "/case",
    ]
    platform = DOCKER_PLATFORMS.get("openfoam")
    if platform:
        docker_command.extend(["--platform", platform])
    entrypoint = DOCKER_ENTRYPOINTS.get("openfoam")
    if entrypoint:
        docker_command.extend(["--entrypoint", entrypoint])
        docker_command.extend([image, "-lc", shell_command])
    else:
        docker_command.extend([image, "/bin/bash", "-lc", shell_command])
    return docker_command


def _openfoam_mesh_command(case_dir: Path, execution: str | None, command: list[str]) -> list[str]:
    if execution == "docker":
        return _openfoam_docker_command(case_dir, command)
    return command


def _classify_openfoam_runtime(lines: list[str]) -> str:
    text = "\n".join(lines)
    lowered = text.lower()
    if "opencfd" in lowered or "openfoam.com" in lowered or re.search(r"\bv\d{4}\b", text):
        return "opencfd"
    if "foundation" in lowered or re.search(r"\bOpenFOAM[- ](?:[1-9]\d?|v[1-9]\d?)\b", text):
        return "foundation"
    return "unknown"


def _openfoam_runtime_version(lines: list[str]) -> str | None:
    text = "\n".join(lines)
    version_patterns = (
        r"\bOpenFOAM[- ](v?\d{1,4})\b",
        r"\bversion[:= ]+(v?\d{1,4})\b",
        r"\b(v\d{4})\b",
    )
    for pattern in version_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _replace_control_dict_functions_with_include(control_dict: str) -> str:
    match = re.search(r"(^|\n)\s*functions\s*\{", control_dict)
    if not match:
        return control_dict + '\nfunctions\n{\n    #include "functions"\n}\n'
    start = control_dict.find("{", match.end() - 1)
    if start < 0:
        return control_dict
    depth = 0
    for index in range(start, len(control_dict)):
        char = control_dict[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return control_dict[: match.start(0)] + '\nfunctions\n{\n    #include "functions"\n}\n' + control_dict[index + 1 :]
    return control_dict


def _control_dict_function_object_span(text: str, name: str) -> tuple[int, int] | None:
    match = re.search(rf"(^|\n)(?P<indent>\s*){re.escape(name)}\s*\n\s*\{{", text)
    if not match:
        return None
    start = match.start("indent")
    brace_start = text.find("{", match.end() - 1)
    if brace_start < 0:
        return None
    depth = 0
    for index in range(brace_start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    return None


def _openfoam_word(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_")
    if not cleaned:
        return "patch"
    if cleaned[0].isdigit():
        cleaned = f"patch_{cleaned}"
    return cleaned


def _foundation_patch_flow_rate_objects(functions_text: str) -> str:
    block = _control_dict_function_block(functions_text, "patchFlowRate")
    if not block:
        return functions_text
    match = re.search(r"\bpatches\s*\((?P<patches>[^)]*)\)", block, re.DOTALL)
    patches = [patch for patch in re.split(r"\s+", match.group("patches").strip()) if patch] if match else []
    if not patches:
        return functions_text
    write_control_match = re.search(r"\bwriteControl\s+(\w+)\s*;", block)
    write_control = write_control_match.group(1) if write_control_match else "writeTime"
    write_interval_match = re.search(r"\bwriteInterval\s+(\d+)\s*;", block)
    write_interval = (
        f"\n        writeInterval   {write_interval_match.group(1)};"
        if write_interval_match
        else ""
    )
    objects = []
    for patch in patches:
        patch_word = _openfoam_word(patch)
        objects.append(
            f"""    patchFlowRate_{patch_word}
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    {write_control};{write_interval}
        log             true;
        writeFields     false;
        regionType      patch;
        name            {patch};
        operation       sum;
        fields          (phi);
    }}"""
        )
    span = _control_dict_function_object_span(functions_text, "patchFlowRate")
    if span is None:
        return functions_text
    return functions_text[: span[0]] + "\n\n".join(objects) + functions_text[span[1] :]


def _foundation_patch_average_objects(functions_text: str) -> str:
    block = _control_dict_function_block(functions_text, "patchAverage")
    if not block:
        return functions_text
    match = re.search(r"\bname\s*\((?P<patches>[^)]*)\)", block, re.DOTALL)
    patches = [patch for patch in re.split(r"\s+", match.group("patches").strip()) if patch] if match else []
    if not patches:
        return functions_text
    fields_match = re.search(r"\bfields\s*\((?P<fields>[^)]*)\)", block, re.DOTALL)
    fields = fields_match.group("fields").strip() if fields_match else "p"
    write_control_match = re.search(r"\bwriteControl\s+(\w+)\s*;", block)
    write_control = write_control_match.group(1) if write_control_match else "writeTime"
    write_interval_match = re.search(r"\bwriteInterval\s+(\d+)\s*;", block)
    write_interval = (
        f"\n        writeInterval   {write_interval_match.group(1)};"
        if write_interval_match
        else ""
    )
    objects = []
    for patch in patches:
        patch_word = _openfoam_word(patch)
        objects.append(
            f"""    patchAverage_{patch_word}
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    {write_control};{write_interval}
        log             true;
        writeFields     false;
        regionType      patch;
        name            {patch};
        operation       areaAverage;
        fields          ({fields});
    }}"""
        )
    span = _control_dict_function_object_span(functions_text, "patchAverage")
    if span is None:
        return functions_text
    return functions_text[: span[0]] + "\n\n".join(objects) + functions_text[span[1] :]


def _apply_openfoam_runtime_style(case_dir: Path, style: str, lines: list[str]) -> None:
    runtime_artifact = {
        "schema": "flowlab.openfoam_runtime_detection.v1",
        "detectedStyle": style,
        "detectedVersion": _openfoam_runtime_version(lines),
        "versionOutput": lines[:20],
        "controlDictAdaptation": "none",
    }
    if style == "foundation" and (case_dir / "system" / "functions").exists():
        control_path = case_dir / "system" / "controlDict"
        functions_path = case_dir / "system" / "functions"
        try:
            control_text = control_path.read_text(encoding="utf-8")
        except OSError:
            control_text = ""
        if control_text:
            adapted = _replace_control_dict_functions_with_include(control_text)
            if adapted != control_text:
                control_path.write_text(adapted, encoding="utf-8")
                runtime_artifact["controlDictAdaptation"] = 'functions block replaced with #include "functions"'
        try:
            functions_text = functions_path.read_text(encoding="utf-8")
        except OSError:
            functions_text = ""
        if functions_text:
            adapted_functions = _foundation_patch_average_objects(_foundation_patch_flow_rate_objects(functions_text))
            if adapted_functions != functions_text:
                functions_path.write_text(adapted_functions, encoding="utf-8")
                runtime_artifact["functionObjectAdaptation"] = (
                    "patchFlowRate and patchAverage replaced with per-patch surfaceFieldValue objects"
                )
    output = case_dir / "constant" / "flowlab_openfoam_runtime.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(runtime_artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _openfoam_required_mesh_commands(case_dir: Path, *, skip_snappy: bool = False) -> list[str]:
    if (
        _openfoam_case_is_axisymmetric_wedge(case_dir)
        or _openfoam_case_is_full_ogrid(case_dir)
        or _openfoam_case_is_curved_elbow(case_dir)
        or _openfoam_case_is_y_junction(case_dir)
    ):
        commands = ["checkMesh"]
        if (
            not _openfoam_has_base_mesh(case_dir)
            and not _openfoam_case_is_y_junction(case_dir)
        ):
            commands.insert(0, "blockMesh")
        return commands
    commands = ["surfaceFeatureExtract", "snappyHexMesh", "checkMesh"]
    if not _openfoam_has_base_mesh(case_dir):
        commands.insert(1, "blockMesh")
    if skip_snappy:
        commands = [command for command in commands if command != "snappyHexMesh"]
    return commands


def _openfoam_case_is_axisymmetric_wedge(case_dir: Path) -> bool:
    """True when the generated blockMeshDict is an axisymmetric wedge pipe.

    A wedge blockMesh is itself the final mesh, so surfaceFeatureExtract/snappyHexMesh
    must be skipped (blockMesh + checkMesh only), exactly like the fitted starter
    polyMesh path. blockMesh also handles the singular collapsed axis natively.
    """
    try:
        profile = json.loads((case_dir / "constant" / "flowlab_axisymmetric_profile.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        profile = {}
    if (
        isinstance(profile, dict)
        and profile.get("schema") == "flowlab.axisymmetric-profile.v1"
        and profile.get("effectiveMeshMode") == "axisymmetric-wedge"
    ):
        return True
    # Backward-compatible recognition for retained pre-manifest development cases.
    try:
        text = (case_dir / "system" / "blockMeshDict").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "type wedge" in text


def _openfoam_case_is_full_ogrid(case_dir: Path) -> bool:
    """Recognize only a manifest-bound canonical direct-blockMesh full O-grid."""

    try:
        profile = json.loads((case_dir / "constant" / "flowlab_full_ogrid_profile.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    topology = profile.get("topology") if isinstance(profile, dict) and isinstance(profile.get("topology"), dict) else {}
    schema = profile.get("schema")
    if schema == "flowlab.full-ogrid-profile.v1":
        representation_matches = (
            profile.get("effectiveMeshMode")
            == "full-revolution-five-block-ogrid"
            and topology.get("blockCount") == 5
        )
    elif schema == "flowlab.full-ogrid-path-profile.v1":
        segment_count = topology.get("geometrySegmentCount")
        representation_matches = (
            profile.get("effectiveMeshMode")
            == "full-revolution-multi-segment-five-block-ogrid"
            and isinstance(segment_count, int)
            and not isinstance(segment_count, bool)
            and segment_count > 0
            and topology.get("blockCount") == 5 * segment_count
        )
    else:
        representation_matches = False
    return (
        representation_matches
        and topology.get("spatialDimension") == 3
        and topology.get("cellTypes") == ["hex"]
        and topology.get("collapsedAxisCells") == 0
        and topology.get("interfaces", {}).get("boundaryPatchCount") == 0
    )


def _openfoam_case_is_curved_elbow(case_dir: Path) -> bool:
    """Recognize only the manifest-bound canonical 15-block elbow O-grid."""

    try:
        profile = json.loads(
            (
                case_dir
                / "constant"
                / "flowlab_curved_elbow_profile.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    topology = (
        profile.get("topology")
        if isinstance(profile, dict)
        and isinstance(profile.get("topology"), dict)
        else {}
    )
    return (
        profile.get("schema") == "flowlab.curved-elbow-ogrid-profile.v1"
        and profile.get("effectiveMeshMode")
        == "canonical-90deg-circular-elbow-fifteen-block-ogrid"
        and topology.get("blockCount") == 15
        and topology.get("collapsedAxisCells") == 0
    )


def _openfoam_case_is_y_junction(case_dir: Path) -> bool:
    """Recognize only the manifest-bound generated direct-polyMesh path."""

    try:
        profile = json.loads(
            (case_dir / "constant" / "flowlab_y_junction_profile.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return False
    required_mesh_files = (
        "points",
        "faces",
        "owner",
        "neighbour",
        "boundary",
        "cellZones",
    )
    return (
        profile.get("schema") == "flowlab.y-junction-profile.v1"
        and profile.get("effectiveMeshMode")
        == "generated-cartesian-all-hex-y-junction"
        and profile.get("ownership", {}).get("geometryInferenceAllowed") is False
        and all(
            (case_dir / "constant" / "polyMesh" / name).is_file()
            for name in required_mesh_files
        )
    )


def _openfoam_uses_starter_fitted_mesh(case_dir: Path, handoff: dict[str, Any]) -> bool:
    starter_geometry = handoff.get("starterGeometry") if isinstance(handoff.get("starterGeometry"), dict) else {}
    reviewed_geometry = handoff.get("reviewedGeometry") if isinstance(handoff.get("reviewedGeometry"), dict) else {}
    reviewed_surfaces = reviewed_geometry.get("surfaces") if isinstance(reviewed_geometry.get("surfaces"), list) else []
    source_type = str(starter_geometry.get("sourceType") or reviewed_geometry.get("sourceType") or "")
    has_empty_patch = False
    try:
        boundary_text = (case_dir / "constant" / "polyMesh" / "boundary").read_text(encoding="utf-8", errors="replace")
        has_empty_patch = bool(re.search(r"\btype\s+empty\s*;", boundary_text))
    except OSError:
        has_empty_patch = False
    return (
        _openfoam_has_base_mesh(case_dir)
        and source_type == "flowlab-generated"
        and starter_geometry.get("cadReviewed") is not True
        and reviewed_geometry.get("cadReviewed") is not True
        and not reviewed_surfaces
        and has_empty_patch
    )


def _openfoam_yplus_evidence(case_dir: Path) -> dict[str, Any]:
    candidates: list[Path] = []
    for root_name in ("postProcessing/yPlus", "postProcessing/wallDistance"):
        root = case_dir / root_name
        if root.is_dir():
            candidates.extend(path for path in sorted(root.rglob("*")) if path.is_file())
    if not candidates:
        return {
            "status": "missing",
            "requiredEvidence": ["postProcessing/yPlus or postProcessing/wallDistance"],
            "blockingReason": "Missing y-plus or wall-distance evidence.",
        }

    values: list[float] = []
    evidence_files: list[str] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_values = [_float_or_none(value) for value in FLOAT_RE.findall(text)]
        numeric_values = [value for value in file_values if value is not None and math.isfinite(value)]
        if numeric_values:
            evidence_files.append(str(path.relative_to(case_dir)))
            values.extend(numeric_values)
    if not values:
        return {
            "status": "missing",
            "files": [str(path.relative_to(case_dir)) for path in candidates],
            "blockingReason": "Y-plus or wall-distance files were present but contained no numeric evidence.",
        }
    return {
        "status": "present",
        "files": evidence_files,
        "min": min(values),
        "mean": sum(values) / len(values),
        "max": max(values),
        "sampleCount": len(values),
    }


def _openfoam_layer_summary(lines: list[str]) -> dict[str, Any]:
    excerpts = [
        line
        for line in lines
        if "layer" in line.lower() or "addlayers" in line.lower() or "snappyhexmesh" in line.lower()
    ]
    return {
        "status": "present" if excerpts else "not-reported",
        "excerptCount": len(excerpts),
        "excerpts": excerpts[-12:],
    }


def _openfoam_checkmesh_metrics(lines: list[str]) -> dict[str, Any]:
    summary = parse_solver_logs("openfoam", lines)
    checkmesh = summary.get("checkMesh")
    return checkmesh if isinstance(checkmesh, dict) else {}


def _append_unique_strings(values: list[Any], additions: Iterable[str]) -> list[Any]:
    seen = {item for item in values if isinstance(item, str)}
    for item in additions:
        if item not in seen:
            values.append(item)
            seen.add(item)
    return values


def _openfoam_patch_coverage(expected_patches: list[str], lines: list[str]) -> dict[str, Any]:
    expected = sorted({patch for patch in expected_patches if patch})
    if not expected:
        return {
            "status": "not-required",
            "expectedPatches": [],
            "presentPatches": [],
            "missingPatches": [],
            "blockingReason": None,
        }
    haystack = "\n".join(lines)
    present = [patch for patch in expected if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(patch)}(?![A-Za-z0-9_.-])", haystack)]
    has_boundary_patch_section = any("boundary patches" in line.lower() for line in lines)
    if not has_boundary_patch_section and not present:
        return {
            "status": "not-reported",
            "expectedPatches": expected,
            "presentPatches": [],
            "missingPatches": [],
            "blockingReason": None,
        }
    missing = [patch for patch in expected if patch not in present]
    return {
        "status": "pass" if not missing else "fail",
        "expectedPatches": expected,
        "presentPatches": present,
        "missingPatches": missing,
        "blockingReason": None if not missing else "Native snappy/checkMesh logs did not confirm reviewed patches: " + ", ".join(missing) + ".",
    }


def _openfoam_native_mesh_blockers(
    *,
    command_runs: list[dict[str, Any]],
    checkmesh_metrics: dict[str, Any],
    yplus_evidence: dict[str, Any],
    cad_reviewed: bool,
    missing_boundary_tags: list[str] | None = None,
    patch_coverage: dict[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    missing = [run["command"] for run in command_runs if run.get("status") == "missing-command"]
    for command in missing:
        blockers.append(f"Missing OpenFOAM native mesh command `{command}`.")
    failed_runs = [run for run in command_runs if run.get("required") and run.get("exitCode") not in (0, None)]
    for run in failed_runs:
        blockers.append(f"OpenFOAM native mesh command `{run.get('command')}` exited with code {run.get('exitCode')}.")
    failed_checks = checkmesh_metrics.get("failedChecks")
    if isinstance(failed_checks, int | float) and int(failed_checks) > 0:
        blockers.append(f"OpenFOAM checkMesh failed {int(failed_checks)} check(s).")
    elif checkmesh_metrics.get("passed") is not True:
        blockers.append("OpenFOAM checkMesh did not report a passing native mesh.")
    if yplus_evidence.get("status") != "present":
        blockers.append(str(yplus_evidence.get("blockingReason") or "Missing y-plus or wall-distance evidence."))
    if not cad_reviewed:
        blockers.append("Generated starter triSurface has not been CAD/B-rep reviewed; production approval remains blocked.")
    if missing_boundary_tags:
        blockers.append("Reviewed STL is missing required boundary tags/surfaces: " + ", ".join(missing_boundary_tags) + ".")
    if patch_coverage and patch_coverage.get("status") == "fail":
        blockers.append(str(patch_coverage.get("blockingReason") or "Native mesh logs did not confirm required reviewed patches."))
    return blockers


def _update_openfoam_production_mesh_acceptance(
    case_dir: Path,
    *,
    command_runs: list[dict[str, Any]],
    checkmesh_metrics: dict[str, Any],
    layer_summary: dict[str, Any],
    yplus_evidence: dict[str, Any],
    cad_reviewed: bool,
    missing_boundary_tags: list[str] | None = None,
    patch_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    acceptance_path = case_dir / "mesh" / "production_mesh_acceptance.json"
    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        acceptance = {
            "schema": "flowlab.production_mesh_acceptance.v1",
            "productionReady": False,
            "approvalStatus": "blocked",
            "acceptanceCriteria": [],
            "nativeQualityEvidence": {
                "schema": "flowlab.native_mesh_quality_evidence.v1",
                "productionReady": False,
                "solverReports": {},
            },
            "solverAcceptance": {},
            "blockingReasons": [],
        }

    blockers = _openfoam_native_mesh_blockers(
        command_runs=command_runs,
        checkmesh_metrics=checkmesh_metrics,
        yplus_evidence=yplus_evidence,
        cad_reviewed=cad_reviewed,
        missing_boundary_tags=missing_boundary_tags,
        patch_coverage=patch_coverage,
    )
    native_ready = (
        not any(run.get("required") and run.get("exitCode") not in (0, None) for run in command_runs)
        and not any(run.get("status") == "missing-command" for run in command_runs)
        and checkmesh_metrics.get("passed") is True
        and int(checkmesh_metrics.get("failedChecks", 0) or 0) == 0
        and yplus_evidence.get("status") == "present"
        and (not patch_coverage or patch_coverage.get("status") in {"pass", "not-required", "not-reported"})
    )
    production_ready = native_ready and cad_reviewed and not blockers

    native_quality = acceptance.setdefault("nativeQualityEvidence", {})
    native_quality["schema"] = "flowlab.native_mesh_quality_evidence.v1"
    native_quality["productionReady"] = production_ready
    native_quality["status"] = "openfoam-native-quality-passed" if native_ready else "openfoam-native-quality-blocked"
    solver_reports = native_quality.setdefault("solverReports", {})
    openfoam_report = solver_reports.setdefault("openfoam", {})
    evidence_paths = [
        str(run["logPath"])
        for run in command_runs
        if isinstance(run.get("logPath"), str) and run.get("logPath")
    ]
    evidence_paths.extend(str(path) for path in yplus_evidence.get("files", []) if isinstance(path, str))
    openfoam_report.update(
        {
            "status": "passed" if native_ready else "blocked",
            "commands": [
                "surfaceFeatureExtract",
                "blockMesh or existing constant/polyMesh",
                "snappyHexMesh -overwrite for reviewed/native surface meshing",
                "checkMesh -allGeometry -allTopology",
                "postProcess -func yPlus -latestTime when available",
            ],
            "requiredMetrics": [
                "failedChecks",
                "maxNonOrthogonality",
                "maxSkewness",
                "maxAspectRatio",
                "minVolume",
                "yPlusMinMeanMax",
            ],
            "currentEvidence": _append_unique_strings(
                list(openfoam_report.get("currentEvidence", [])) if isinstance(openfoam_report.get("currentEvidence"), list) else [],
                evidence_paths,
            ),
            "commandRuns": command_runs,
            "qualityMetrics": {
                "failedChecks": checkmesh_metrics.get("failedChecks"),
                "maxNonOrthogonality": checkmesh_metrics.get("maxNonOrthogonality"),
                "averageNonOrthogonality": checkmesh_metrics.get("averageNonOrthogonality"),
                "maxSkewness": checkmesh_metrics.get("maxSkewness"),
                "maxAspectRatio": checkmesh_metrics.get("maxAspectRatio"),
                "minVolume": checkmesh_metrics.get("minVolume"),
                "counts": checkmesh_metrics.get("counts", {}),
                "passed": checkmesh_metrics.get("passed") is True,
            },
            "layerSummary": layer_summary,
            "yPlusEvidence": yplus_evidence,
            "patchCoverage": patch_coverage
            or {
                "status": "not-required",
                "expectedPatches": [],
                "presentPatches": [],
                "missingPatches": [],
                "blockingReason": None,
            },
            "blockingReasons": blockers,
        }
    )

    solver_acceptance = acceptance.setdefault("solverAcceptance", {})
    openfoam_acceptance = solver_acceptance.setdefault("openfoam", {})
    openfoam_acceptance["status"] = "native-evidence-passed" if native_ready else "blocked"
    openfoam_acceptance["currentEvidence"] = _append_unique_strings(
        list(openfoam_acceptance.get("currentEvidence", [])) if isinstance(openfoam_acceptance.get("currentEvidence"), list) else [],
        evidence_paths,
    )
    openfoam_acceptance["blockingReasons"] = blockers

    for criterion in acceptance.get("acceptanceCriteria", []):
        if not isinstance(criterion, dict):
            continue
        criterion_id = criterion.get("id")
        if criterion_id == "cad-geometry-source":
            cad_gate_ready = cad_reviewed and not missing_boundary_tags
            criterion["status"] = "pass" if cad_gate_ready else "fail"
            criterion["evidence"] = ["constant/triSurface/reviewedFlowLabSurfaces.stl", "mesh/openfoam_snappy_handoff.json"] if cad_gate_ready else []
            criterion["detail"] = (
                "Reviewed user STL is materialized as the OpenFOAM triSurface with required inlet/outlet/wall tags."
                if cad_gate_ready
                else "Reviewed geometry needs CAD review plus inlet, outlet, and wall boundary tags."
            )
        elif criterion_id == "native-3d-volume-mesh":
            criterion["status"] = "pass" if native_ready else "fail"
            criterion["evidence"] = evidence_paths
            criterion["detail"] = (
                "OpenFOAM native meshing completed and checkMesh passed."
                if native_ready
                else "Native 3D volume mesh evidence is incomplete or failed."
            )
        elif criterion_id == "boundary-layer-prism-mesh":
            criterion["status"] = "pass" if native_ready and yplus_evidence.get("status") == "present" else "fail"
            criterion["evidence"] = evidence_paths
            criterion["detail"] = (
                "Native wall evidence is present for the generated mesh."
                if native_ready and yplus_evidence.get("status") == "present"
                else "Native layer or y-plus/wall-distance evidence is missing."
            )
        elif criterion_id == "adapted-refinement-evidence":
            criterion["status"] = "pass" if native_ready else "fail"
            criterion["evidence"] = evidence_paths
            criterion["detail"] = (
                "Native OpenFOAM mesh quality evidence passed for the current refinement handoff."
                if native_ready
                else "Native refinement or adaptation evidence is incomplete."
            )
        elif criterion_id == "solver-native-quality-evidence":
            criterion["status"] = "pass" if native_ready else "fail"
            criterion["evidence"] = evidence_paths
            criterion["detail"] = (
                "OpenFOAM native meshing, checkMesh, and wall evidence passed."
                if native_ready
                else "OpenFOAM native meshing evidence is incomplete or failed."
            )

    acceptance["productionReady"] = production_ready
    acceptance["approvalStatus"] = "approved" if production_ready else "blocked"
    acceptance["blockingReasons"] = [] if production_ready else blockers
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text(json.dumps(acceptance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return acceptance


class JobManager:
    def __init__(self, runtime_root: Path | None = None, popen_factory: PopenFactory | None = None) -> None:
        self.runtime_root = (runtime_root or default_runtime_root()).resolve()
        self.popen_factory = popen_factory or subprocess.Popen
        self.jobs: dict[str, JobRecord] = {}
        self.cases: dict[str, SolverCase] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()
        self._load_persisted_jobs()

    def queue_job(self, case: SolverCase) -> JobRecord:
        job = JobRecord(
            caseId=case.id,
            solver=case.solver,
            status="queued",
            evidenceCapability=case.evidenceCapability.model_copy(deep=True),
            result={"evidenceCapability": case.evidenceCapability.model_dump(mode="json")},
        )
        job_dir = self.runtime_root / "jobs" / job.id
        case_dir = job_dir / "case"
        job.caseDir = str(case_dir)
        job.logs.append(f"Queued {case.solver} case {case.id}.")
        job_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_atomically(job_dir / CASE_RECORD_FILENAME, case.model_dump(mode="json"))
        with self._lock:
            self.cases[job.id] = case.model_copy(deep=True)

        try:
            materialize_case_files(case, case_dir)
            job.logs.append(f"Materialized {len(case.files)} generated file(s) in {case_dir}.")
        except ValueError as exc:
            job.status = "blocked"
            job.error = str(exc)
            job.finishedAt = _utc_now()
            job.logs.append(str(exc))
            self._store(job)
            return job

        validation_issues = validate_solver_case(case)
        if validation_issues:
            job.status = "blocked"
            job.error = "Generated case validation failed."
            job.finishedAt = _utc_now()
            job.logs.extend(validation_issues)
            self._store(job)
            return job

        execution, command, error = self._resolve_execution(case, case_dir)
        job.execution = execution
        job.command = command
        if error:
            job.status = "blocked"
            job.error = error
            job.finishedAt = _utc_now()
            job.logs.append(error)
            self._store(job)
            return job

        self._store(job)
        thread = threading.Thread(target=self._run_job, args=(job.id, case_dir), daemon=True)
        thread.start()
        return self.get_job(job.id)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def get_case_for_job(self, job_id: str) -> SolverCase | None:
        with self._lock:
            case = self.cases.get(job_id)
            return case.model_copy(deep=True) if case else None

    def list_jobs(self, limit: int = 20) -> list[tuple[JobRecord, SolverCase | None]]:
        with self._lock:
            ordered = sorted(self.jobs.values(), key=lambda job: job.updatedAt, reverse=True)[:limit]
            return [
                (
                    job.model_copy(deep=True),
                    self.cases[job.id].model_copy(deep=True) if job.id in self.cases else None,
                )
                for job in ordered
            ]

    def get_logs(self, job_id: str) -> list[str] | None:
        job = self.get_job(job_id)
        return job.logs if job else None

    def cancel_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            if job.status in TERMINAL_STATUSES:
                return job.model_copy(deep=True)
            job.status = "cancelled"
            job.updatedAt = _utc_now()
            job.finishedAt = job.updatedAt
            job.error = "Job cancelled by user."
            job.logs.append("Cancellation requested.")
            process = self._processes.get(job_id)
            self._persist_job_unlocked(job)

        if process:
            try:
                process.terminate()
            except Exception as exc:  # pragma: no cover - defensive logging
                self._append_log(job_id, f"Failed to terminate solver process: {exc}")
        return self.get_job(job_id)

    def stream_log_lines(self, job_id: str, poll_interval: float = 0.25) -> Iterable[str]:
        seen = 0
        while True:
            job = self.get_job(job_id)
            if not job:
                yield "Job not found\n"
                return
            for line in job.logs[seen:]:
                yield f"{line}\n"
            seen = len(job.logs)
            if job.status in TERMINAL_STATUSES:
                return
            time.sleep(poll_interval)

    def cleanup(self) -> None:
        with self._lock:
            for process in self._processes.values():
                try:
                    process.terminate()
                except Exception:
                    pass
            self.jobs.clear()
            self.cases.clear()
            self._processes.clear()
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)

    def _store(self, job: JobRecord) -> None:
        with self._lock:
            job.updatedAt = _utc_now()
            self.jobs[job.id] = job
            self._persist_job_unlocked(job)

    @staticmethod
    def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _persist_job_unlocked(self, job: JobRecord) -> None:
        job_dir = self.runtime_root / "jobs" / job.id
        self._write_json_atomically(job_dir / JOB_RECORD_FILENAME, job.model_dump(mode="json"))

    def _load_persisted_jobs(self) -> None:
        jobs_root = self.runtime_root / "jobs"
        if not jobs_root.is_dir():
            return
        for job_dir in jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            job_path = job_dir / JOB_RECORD_FILENAME
            if not job_path.is_file():
                continue
            try:
                job = JobRecord.model_validate_json(job_path.read_text(encoding="utf-8"))
                if job.id != job_dir.name:
                    continue
                if job.status not in TERMINAL_STATUSES:
                    job.status = "failed"
                    job.error = "The desktop application closed before this solver run reached a terminal state."
                    job.finishedAt = _utc_now()
                    job.updatedAt = job.finishedAt
                    job.logs.append(job.error)
                    self._persist_job_unlocked(job)
                self.jobs[job.id] = job

                case_path = job_dir / CASE_RECORD_FILENAME
                if case_path.is_file():
                    case = SolverCase.model_validate_json(case_path.read_text(encoding="utf-8"))
                    if case.id == job.caseId:
                        self.cases[job.id] = case
            except (OSError, ValueError):
                # Ignore incomplete or manually damaged records; run artifacts remain inspectable on disk.
                continue

    def _append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self.jobs[job_id]
            job.logs.append(line)
            job.updatedAt = _utc_now()

    def _run_openfoam_mesh_command(
        self,
        job_id: str,
        case_dir: Path,
        execution: str | None,
        command: list[str],
        log_path: str,
        *,
        required: bool = True,
        stream_to_job_log: bool = True,
    ) -> dict[str, Any]:
        resolved_command = _openfoam_mesh_command(case_dir, execution, command)
        relative_log = Path(log_path)
        log_file = case_dir / relative_log
        log_file.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        self._append_log(job_id, f"OpenFOAM native mesh: running {shlex.join(command)}")

        process = self.popen_factory(
            resolved_command,
            cwd=case_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._processes[job_id] = process

        stdout = process.stdout
        if stdout is not None:
            for raw_line in stdout:
                line = raw_line.rstrip()
                if line:
                    lines.append(line)
                    if stream_to_job_log:
                        self._append_log(job_id, line)

        exit_code = process.wait()
        log_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        with self._lock:
            self._processes.pop(job_id, None)
            job = self.jobs.get(job_id)
            cancelled = job is not None and job.status == "cancelled"
        status = "cancelled" if cancelled else "complete" if exit_code == 0 else "failed"
        if not required and exit_code != 0:
            self._append_log(job_id, f"OpenFOAM optional mesh command `{shlex.join(command)}` exited with code {exit_code}; see {log_path}.")
        return {
            "command": shlex.join(command),
            "resolvedCommand": shlex.join(resolved_command),
            "execution": execution or "native",
            "required": required,
            "exitCode": exit_code,
            "status": status,
            "logPath": str(relative_log),
            "lineCount": len(lines),
            "lines": lines,
        }

    def _detect_openfoam_runtime_style(self, job_id: str, case_dir: Path, execution: str | None) -> None:
        if execution == "native" and not adapters._command_exists("foamVersion"):
            _apply_openfoam_runtime_style(case_dir, "unknown", ["native foamVersion command not found"])
            return
        resolved_command = _openfoam_mesh_command(case_dir, execution, ["foamVersion"])
        lines: list[str] = []
        try:
            process = self.popen_factory(
                resolved_command,
                cwd=case_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            stdout = process.stdout
            if stdout is not None:
                for raw_line in stdout:
                    line = raw_line.rstrip()
                    if line:
                        lines.append(line)
            exit_code = process.wait()
        except Exception as exc:  # pragma: no cover - defensive runtime detection
            _apply_openfoam_runtime_style(case_dir, "unknown", [f"foamVersion probe failed: {exc}"])
            return
        if exit_code != 0:
            _apply_openfoam_runtime_style(case_dir, "unknown", [*lines, f"foamVersion exited with code {exit_code}"])
            return
        style = _classify_openfoam_runtime(lines)
        _apply_openfoam_runtime_style(case_dir, style, lines)
        self._append_log(job_id, f"OpenFOAM runtime style detected: {style}.")

    def _run_openfoam_native_mesh_stage(self, job_id: str, case_dir: Path, execution: str | None) -> str | None:
        try:
            handoff = json.loads((case_dir / "mesh" / "openfoam_snappy_handoff.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            handoff = {}
        starter_geometry = handoff.get("starterGeometry") if isinstance(handoff.get("starterGeometry"), dict) else {}
        reviewed_geometry = handoff.get("reviewedGeometry") if isinstance(handoff.get("reviewedGeometry"), dict) else {}
        surface_geometry = reviewed_geometry if isinstance(reviewed_geometry.get("surfaces"), list) else starter_geometry
        cad_reviewed = starter_geometry.get("cadReviewed") is True
        is_axisymmetric_wedge = _openfoam_case_is_axisymmetric_wedge(case_dir)
        is_full_ogrid = _openfoam_case_is_full_ogrid(case_dir)
        is_curved_elbow = _openfoam_case_is_curved_elbow(case_dir)
        is_y_junction = _openfoam_case_is_y_junction(case_dir)
        is_direct_block_mesh = (
            is_axisymmetric_wedge
            or is_full_ogrid
            or is_curved_elbow
            or is_y_junction
        )
        skip_snappy_for_starter = _openfoam_uses_starter_fitted_mesh(case_dir, handoff) or is_direct_block_mesh
        _, missing_boundary_tags, _ = _reviewed_boundary_tag_status(starter_geometry)
        _, missing_surface_roles, _, required_patch_names = _reviewed_surface_status(surface_geometry)
        missing_boundary_tags = sorted({*missing_boundary_tags, *missing_surface_roles})
        if is_axisymmetric_wedge:
            required_patch_names = ["inlet", "outlet", "walls", "front", "back", "axis"]
            missing_boundary_tags = []
        elif is_full_ogrid:
            required_patch_names = ["inlet", "outlet", "walls"]
            missing_boundary_tags = []
        elif is_curved_elbow:
            required_patch_names = ["inlet", "outlet", "walls"]
        elif is_y_junction:
            required_patch_names = ["inlet", "outletUpper", "outletLower", "walls"]
            missing_boundary_tags = []

        command_runs: list[dict[str, Any]] = []
        if execution == "native":
            missing_commands = [
                command
                for command in _openfoam_required_mesh_commands(case_dir, skip_snappy=skip_snappy_for_starter)
                if not adapters._command_exists(command)
            ]
            if missing_commands:
                for command in missing_commands:
                    command_runs.append(
                        {
                            "command": command,
                            "execution": "native",
                            "required": True,
                            "status": "missing-command",
                            "exitCode": None,
                            "logPath": None,
                        }
                    )
                acceptance = _update_openfoam_production_mesh_acceptance(
                    case_dir,
                    command_runs=command_runs,
                    checkmesh_metrics={},
                    layer_summary={"status": "not-run", "excerpts": []},
                    yplus_evidence=_openfoam_yplus_evidence(case_dir),
                    cad_reviewed=cad_reviewed,
                    missing_boundary_tags=missing_boundary_tags,
                )
                blockers = acceptance.get("solverAcceptance", {}).get("openfoam", {}).get("blockingReasons", [])
                return "; ".join(str(item) for item in blockers) or "Missing OpenFOAM native mesh command."

        if is_direct_block_mesh:
            geometry_label = (
                "axisymmetric blockMesh wedge"
                if is_axisymmetric_wedge
                else "full-revolution blockMesh O-grid"
                if is_full_ogrid
                else "canonical curved-elbow blockMesh O-grid"
                if is_curved_elbow
                else "generated direct-polyMesh Y-junction"
            )
            command_runs.append(
                {
                    "command": "surfaceFeatureExtract",
                    "execution": execution or "native",
                    "required": False,
                    "status": "skipped",
                    "exitCode": None,
                    "logPath": None,
                    "reason": f"{geometry_label} is defined directly by its generated solver mesh and has no triangulated surface feature stage.",
                }
            )
            self._append_log(job_id, f"OpenFOAM native mesh: skipping surfaceFeatureExtract for {geometry_label}.")
        else:
            command_runs.append(
                self._run_openfoam_mesh_command(
                    job_id,
                    case_dir,
                    execution,
                    ["surfaceFeatureExtract"],
                    "log.surfaceFeatureExtract",
                )
            )
            if command_runs[-1]["exitCode"] != 0:
                _update_openfoam_production_mesh_acceptance(
                    case_dir,
                    command_runs=command_runs,
                    checkmesh_metrics={},
                    layer_summary=_openfoam_layer_summary(command_runs[-1].get("lines", [])),
                    yplus_evidence=_openfoam_yplus_evidence(case_dir),
                    cad_reviewed=cad_reviewed,
                    missing_boundary_tags=missing_boundary_tags,
                )
                return "OpenFOAM native mesh command `surfaceFeatureExtract` failed before solver launch."

        if _openfoam_has_base_mesh(case_dir):
            command_runs.append(
                {
                    "command": "confirm constant/polyMesh",
                    "execution": "filesystem",
                    "required": True,
                    "status": "confirmed",
                    "exitCode": 0,
                    "logPath": None,
                }
            )
            self._append_log(job_id, "OpenFOAM native mesh: confirmed existing constant/polyMesh base mesh.")
        else:
            if execution == "native" and not adapters._command_exists("blockMesh"):
                command_runs.append(
                    {
                        "command": "blockMesh",
                        "execution": "native",
                        "required": True,
                        "status": "missing-command",
                        "exitCode": None,
                        "logPath": None,
                    }
                )
                acceptance = _update_openfoam_production_mesh_acceptance(
                    case_dir,
                    command_runs=command_runs,
                    checkmesh_metrics={},
                    layer_summary=_openfoam_layer_summary(command_runs[0].get("lines", [])),
                    yplus_evidence=_openfoam_yplus_evidence(case_dir),
                    cad_reviewed=cad_reviewed,
                    missing_boundary_tags=missing_boundary_tags,
                )
                blockers = acceptance.get("solverAcceptance", {}).get("openfoam", {}).get("blockingReasons", [])
                return "; ".join(str(item) for item in blockers) or "Missing OpenFOAM native mesh command `blockMesh`."
            command_runs.append(
                self._run_openfoam_mesh_command(
                    job_id,
                    case_dir,
                    execution,
                    ["blockMesh"],
                    "log.blockMesh",
                )
            )
            if command_runs[-1]["exitCode"] != 0:
                _update_openfoam_production_mesh_acceptance(
                    case_dir,
                    command_runs=command_runs,
                    checkmesh_metrics={},
                    layer_summary=_openfoam_layer_summary([line for run in command_runs for line in run.get("lines", [])]),
                    yplus_evidence=_openfoam_yplus_evidence(case_dir),
                    cad_reviewed=cad_reviewed,
                    missing_boundary_tags=missing_boundary_tags,
                )
                return "OpenFOAM native mesh command `blockMesh` failed before solver launch."

        if skip_snappy_for_starter:
            skip_reason = (
                "Axisymmetric wedge geometry is fully defined by blockMesh; surface extraction and snappyHexMesh are not applicable."
                if is_axisymmetric_wedge
                else "Full O-grid geometry is fully defined by blockMesh; surface extraction and snappyHexMesh are not applicable."
                if is_full_ogrid
                else "Canonical curved-elbow O-grid geometry is fully defined by blockMesh; surface extraction and snappyHexMesh are not applicable."
                if is_curved_elbow
                else "The generated Y-junction is fully defined by its manifest-bound constant/polyMesh; surface extraction and snappyHexMesh are not applicable."
                if is_y_junction
                else "Skipped for FlowLab-generated fitted starter polyMesh with empty front/back patches; production reviewed STL meshing still requires snappyHexMesh evidence."
            )
            command_runs.append(
                {
                    "command": "snappyHexMesh -overwrite",
                    "execution": execution or "native",
                    "required": False,
                    "status": "skipped",
                    "exitCode": None,
                    "logPath": None,
                    "reason": skip_reason,
                }
            )
            if is_axisymmetric_wedge:
                self._append_log(job_id, "OpenFOAM native mesh: skipping snappyHexMesh for axisymmetric blockMesh wedge.")
            elif is_full_ogrid:
                self._append_log(job_id, "OpenFOAM native mesh: skipping snappyHexMesh for full-revolution blockMesh O-grid.")
            elif is_curved_elbow:
                self._append_log(
                    job_id,
                    "OpenFOAM native mesh: skipping snappyHexMesh for canonical curved-elbow blockMesh O-grid.",
                )
            elif is_y_junction:
                self._append_log(job_id, "OpenFOAM native mesh: skipping snappyHexMesh for generated direct-polyMesh Y-junction.")
            else:
                self._append_log(
                    job_id,
                    "OpenFOAM native mesh: skipping snappyHexMesh for generated fitted starter polyMesh; running checkMesh on existing constant/polyMesh.",
                )
        else:
            command_runs.append(
                self._run_openfoam_mesh_command(
                    job_id,
                    case_dir,
                    execution,
                    ["snappyHexMesh", "-overwrite"],
                    "log.snappyHexMesh",
                )
            )
            if command_runs[-1]["exitCode"] != 0:
                _update_openfoam_production_mesh_acceptance(
                    case_dir,
                    command_runs=command_runs,
                    checkmesh_metrics={},
                    layer_summary=_openfoam_layer_summary([line for run in command_runs for line in run.get("lines", [])]),
                    yplus_evidence=_openfoam_yplus_evidence(case_dir),
                    cad_reviewed=cad_reviewed,
                    missing_boundary_tags=missing_boundary_tags,
                )
                return "OpenFOAM native mesh command `snappyHexMesh -overwrite` failed before solver launch."

        command_runs.append(
            self._run_openfoam_mesh_command(
                job_id,
                case_dir,
                execution,
                ["checkMesh", "-allGeometry", "-allTopology"],
                "log.checkMesh",
            )
        )
        checkmesh_lines = list(command_runs[-1].get("lines", []))
        checkmesh_metrics = _openfoam_checkmesh_metrics(checkmesh_lines)

        yplus_command_available = execution == "docker" or adapters._command_exists("postProcess")
        if yplus_command_available:
            yplus_run = self._run_openfoam_mesh_command(
                job_id,
                case_dir,
                execution,
                ["postProcess", "-func", "yPlus", "-latestTime"],
                "log.yPlus",
                required=False,
                stream_to_job_log=False,
            )
            command_runs.append(yplus_run)
        else:
            command_runs.append(
                {
                    "command": "postProcess -func yPlus -latestTime",
                    "execution": "native",
                    "required": False,
                    "status": "missing-command",
                    "exitCode": None,
                    "logPath": None,
                }
            )

        yplus_evidence = _openfoam_yplus_evidence(case_dir)
        layer_summary = _openfoam_layer_summary([line for run in command_runs for line in run.get("lines", [])])
        patch_coverage = _openfoam_patch_coverage(
            required_patch_names,
            [line for run in command_runs for line in run.get("lines", []) if isinstance(line, str)],
        )
        acceptance = _update_openfoam_production_mesh_acceptance(
            case_dir,
            command_runs=command_runs,
            checkmesh_metrics=checkmesh_metrics,
            layer_summary=layer_summary,
            yplus_evidence=yplus_evidence,
            cad_reviewed=cad_reviewed,
            missing_boundary_tags=missing_boundary_tags,
            patch_coverage=patch_coverage,
        )
        blockers = acceptance.get("solverAcceptance", {}).get("openfoam", {}).get("blockingReasons", [])
        required_failed = any(run.get("required") and run.get("exitCode") not in (0, None) for run in command_runs)
        failed_checks = checkmesh_metrics.get("failedChecks")
        if required_failed:
            return "; ".join(str(item) for item in blockers) or "OpenFOAM native mesh command failed before solver launch."
        if isinstance(failed_checks, int | float) and int(failed_checks) > 0:
            return f"OpenFOAM checkMesh failed {int(failed_checks)} check(s) before solver launch."
        if checkmesh_metrics.get("passed") is not True:
            return "OpenFOAM checkMesh did not report a passing native mesh before solver launch."
        if patch_coverage.get("status") == "fail":
            return str(patch_coverage.get("blockingReason") or "Native mesh logs did not confirm required reviewed patches.")
        self._append_log(job_id, "OpenFOAM native mesh evidence captured in mesh/production_mesh_acceptance.json.")
        return None

    def _refresh_result_snapshot(self, job_id: str, case_dir: Path, exit_code: int | None = None) -> None:
        result_files = collect_result_files(case_dir)
        diagnostic_files = collect_diagnostic_files(case_dir)
        diagnostic_summary = parse_diagnostic_files(diagnostic_files)
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.result = {
                "caseDir": job.caseDir,
                "exitCode": exit_code,
                "logsCaptured": len(job.logs),
                "logSummary": parse_solver_logs(job.solver, job.logs),
                "resultFiles": result_files,
                "diagnosticFiles": diagnostic_files,
                "diagnosticSummary": diagnostic_summary,
                "meshQuality": read_case_mesh_quality(case_dir),
                "progressive": job.status == "running",
                "evidenceCapability": job.evidenceCapability.model_dump(mode="json"),
            }
            if job.solver == "openfoam":
                job.result["patchMetrics"] = collect_patch_metrics(case_dir)
                validated_result = read_validated_open_boundary_result(case_dir)
                if validated_result is not None:
                    job.result["validatedBenchmark"] = validated_result
                acceptance = read_openfoam_diagnostics_acceptance(case_dir)
                if acceptance is not None:
                    job.result["diagnosticsAcceptance"] = acceptance
            job.updatedAt = _utc_now()

    def _resolve_execution(self, case: SolverCase, case_dir: Path) -> tuple[str, list[str], str | None]:
        if case.solver == "instant-1d":
            return "browser", [], "Instant 1D jobs run in the browser and do not start a local CFD process."
        if case.solver not in EXECUTABLE_SOLVERS:
            return (
                "none",
                [],
                f"{case.solver} execution is still an optional skeleton adapter; generated files were saved but no local run was started.",
            )
        if not case.runCommand:
            return "none", [], "Generated case does not include a solver run command."

        if case.solver == "openfoam" and case.advancedMode == "conjugate-heat-transfer":
            missing_region_mesh = [path for path in OPENFOAM_CHT_REGION_MESH_FILES if not (case_dir / path).exists()]
            if missing_region_mesh:
                preview = ", ".join(missing_region_mesh[:3])
                if len(missing_region_mesh) > 3:
                    preview = f"{preview}, ..."
                return (
                    "none",
                    [],
                    (
                        "openfoam conjugate-heat-transfer runtime blocked: generated fluid/solid region "
                        "dictionaries are present, but FlowLab has not emitted split region polyMesh files "
                        f"and coupled interface patches yet. Missing: {preview}"
                    ),
                )
            try:
                interface_manifest = json.loads((case_dir / "constant/flowlab_cht_interface.json").read_text())
            except (OSError, json.JSONDecodeError):
                interface_manifest = {}
            if interface_manifest.get("productionReady") is not True:
                if (case_dir / "AllmeshCheck").exists():
                    preflight_command = ["bash", "AllmeshCheck"]
                    if image := _docker_image(case.solver):
                        if adapters._docker_available():
                            shell_command = shlex.join(preflight_command)
                            env_setup = DOCKER_ENV_SETUP.get(case.solver)
                            if env_setup:
                                shell_command = f"{env_setup} && {shell_command}"
                            docker_command = [
                                "docker",
                                "run",
                                "--rm",
                                "-v",
                                f"{case_dir}:/case",
                                "-w",
                                "/case",
                            ]
                            platform = DOCKER_PLATFORMS.get(case.solver)
                            if platform:
                                docker_command.extend(["--platform", platform])
                            entrypoint = DOCKER_ENTRYPOINTS.get(case.solver)
                            if entrypoint:
                                docker_command.extend(["--entrypoint", entrypoint])
                                docker_command.extend([image, "-lc", shell_command])
                            else:
                                docker_command.extend([image, "/bin/bash", "-lc", shell_command])
                            return "preflight", docker_command, None
                    if adapters._command_exists("checkMesh"):
                        return "preflight", preflight_command, None
                return (
                    "none",
                    [],
                    (
                        "openfoam conjugate-heat-transfer runtime blocked: FlowLab generated split fluid/solid "
                        "region polyMesh files with mapped-wall starter patches, but the interface manifest is "
                        "not production-ready. Promote the starter solid sleeve to CAD-quality topology, add "
                        "3D boundary-layer/y-plus evidence, and collect per-region mesh-quality evidence before "
                        "running full CHT."
                    ),
                )

        if case.solver == "su2":
            su2_home = adapters._su2_home()
            if su2_home and adapters._docker_available():
                shell_command = f"export PATH=/opt/su2/bin:$PATH PYTHONPATH=/opt/su2/bin:$PYTHONPATH && {shlex.join(case.runCommand)}"
                return (
                    "docker",
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--platform",
                        "linux/amd64",
                        "-v",
                        f"{case_dir}:/case",
                        "-v",
                        f"{su2_home}:/opt/su2:ro",
                        "-w",
                        "/case",
                        SU2_DOCKER_IMAGE,
                        "/bin/bash",
                        "-lc",
                        shell_command,
                    ],
                    None,
                )

        if case.solver == "code-saturne":
            image = adapters._code_saturne_image()
            if image and adapters._docker_available():
                shell_command = f"export USER=${{USER:-flowlab}} LOGNAME=${{LOGNAME:-flowlab}} && {shlex.join(case.runCommand)}"
                docker_command = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{case_dir}:/case",
                    "-w",
                    "/case",
                ]
                platform = os.environ.get(CODE_SATURNE_DOCKER_PLATFORM_ENV, "").strip()
                if platform:
                    docker_command.extend(["--platform", platform])
                docker_command.extend([image, "/bin/bash", "-lc", shell_command])
                return "docker", docker_command, None

        image = _docker_image(case.solver)
        if image and adapters._docker_available():
            shell_command = shlex.join(case.runCommand)
            env_setup = DOCKER_ENV_SETUP.get(case.solver)
            if env_setup:
                shell_command = f"{env_setup} && {shell_command}"
            docker_command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{case_dir}:/case",
                "-w",
                "/case",
            ]
            platform = DOCKER_PLATFORMS.get(case.solver)
            if platform:
                docker_command.extend(["--platform", platform])
            entrypoint = DOCKER_ENTRYPOINTS.get(case.solver)
            if entrypoint:
                docker_command.extend(["--entrypoint", entrypoint])
                docker_command.extend([image, "-lc", shell_command])
            else:
                docker_command.extend([image, "/bin/bash", "-lc", shell_command])
            return (
                "docker",
                docker_command,
                None,
            )

        native_command = case.runCommand[0] if case.solver == "mujoco" else REQUIRED_NATIVE_COMMANDS.get(case.solver, case.runCommand[0])
        if case.solver == "mujoco" and not adapters._command_exists(native_command):
            native_command = "python"
        if adapters._command_exists(native_command):
            required_module = REQUIRED_PYTHON_MODULES.get(case.solver)
            module_available = (
                adapters._python_module_exists_for_command(native_command, required_module)
                if required_module and case.solver == "mujoco"
                else adapters._python_module_exists(required_module)
                if required_module
                else True
            )
            if required_module and not module_available:
                return (
                    "none",
                    [],
                    (
                        f"{case.solver} dependency missing: native command `{native_command}` was found, "
                        f"but Python module `{required_module}` is not installed."
                    ),
                )
            if case.solver == "mujoco" and case.runCommand[0] != native_command:
                return "native", [native_command, *case.runCommand[1:]], None
            return "native", case.runCommand, None

        if case.solver == "mujoco":
            return (
                "none",
                [],
                "mujoco dependency missing: Python was not found on PATH. Install Python and the `mujoco` package.",
            )

        if case.solver == "su2":
            return (
                "none",
                [],
                (
                    "su2 dependency missing: install native `SU2_CFD` on PATH, or set FLOWLAB_SU2_HOME "
                    "to an official SU2 binary release directory and start Docker."
                ),
            )

        if case.solver == "code-saturne":
            return (
                "none",
                [],
                (
                    "code-saturne dependency missing: set FLOWLAB_CODE_SATURNE_IMAGE to a Docker image "
                    "containing `code_saturne` and start Docker, or install native `code_saturne` on PATH."
                ),
            )

        return (
            "none",
            [],
            (
                f"{case.solver} dependency missing: Docker is unavailable and native command "
                f"`{native_command}` was not found on PATH. Start Docker or install the solver CLI."
            ),
        )

    def _run_job(self, job_id: str, case_dir: Path) -> None:
        with self._lock:
            job = self.jobs[job_id]
            if job.status == "cancelled":
                return
            job.status = "running"
            job.updatedAt = _utc_now()
            command = job.command.copy()
            job.logs.append(f"Running command: {shlex.join(command)}")
            self._persist_job_unlocked(job)

        try:
            with self._lock:
                job = self.jobs[job_id]
                solver = job.solver
                execution = job.execution
            is_validated_preset = (
                job.evidenceCapability.status == "validated-benchmark"
                and job.evidenceCapability.evidenceId == OPEN_BOUNDARY_BENCHMARK_ID
            )
            if solver == "openfoam" and execution in {"native", "docker"}:
                self._detect_openfoam_runtime_style(job_id, case_dir, execution)
                mesh_error = None if is_validated_preset else self._run_openfoam_native_mesh_stage(job_id, case_dir, execution)
                if is_validated_preset:
                    self._append_log(job_id, "Validated preset owns its immutable blockMesh and checkMesh stage; generic CAD/native-mesh promotion checks are not applicable.")
                with self._lock:
                    job = self.jobs[job_id]
                    if job.status == "cancelled":
                        job.result = {
                            "caseDir": job.caseDir,
                            "exitCode": job.exitCode,
                            "logsCaptured": len(job.logs),
                            "logSummary": parse_solver_logs(job.solver, job.logs),
                        }
                        return
                if mesh_error:
                    result_files = collect_result_files(case_dir)
                    diagnostic_files = collect_diagnostic_files(case_dir)
                    diagnostic_summary = parse_diagnostic_files(diagnostic_files)
                    mesh_quality = read_case_mesh_quality(case_dir)
                    patch_metrics = collect_patch_metrics(case_dir)
                    with self._lock:
                        job = self.jobs[job_id]
                        job.exitCode = None
                        job.finishedAt = _utc_now()
                        job.updatedAt = job.finishedAt
                        job.status = "blocked" if "Missing OpenFOAM native mesh command" in mesh_error else "failed"
                        job.error = mesh_error
                        job.logs.append(mesh_error)
                        artifact_payload = finalize_run_artifacts(
                            case_dir,
                            job=job,
                            result_files=result_files,
                            diagnostic_files=diagnostic_files,
                            diagnostic_summary=diagnostic_summary,
                            mesh_quality=mesh_quality,
                            patch_metrics=patch_metrics,
                        )
                        job.result = {
                            "caseDir": job.caseDir,
                            "exitCode": None,
                            "logsCaptured": len(job.logs),
                            "logSummary": parse_solver_logs(job.solver, job.logs),
                            "resultFiles": result_files,
                            "diagnosticFiles": diagnostic_files,
                            "diagnosticSummary": diagnostic_summary,
                            "meshQuality": mesh_quality,
                            "patchMetrics": patch_metrics,
                            "progressive": False,
                            **artifact_payload,
                        }
                    return

            process = self.popen_factory(
                command,
                cwd=case_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with self._lock:
                self._processes[job_id] = process

            solver_log_path = case_dir / "postProcessing" / "solverLogs" / "solve.log" if solver == "openfoam" else None
            solver_log_relative = str(solver_log_path.relative_to(case_dir)) if solver_log_path is not None else None
            solver_output_lines: list[str] = []
            stdout = process.stdout
            if stdout is not None:
                last_snapshot_at = 0.0
                residual_snapshot_seen = False
                for raw_line in stdout:
                    line = raw_line.rstrip()
                    solver_output_lines.append(line)
                    if line:
                        self._append_log(job_id, line)
                        now = time.monotonic()
                        residual_line = "residual" in line.lower() or "Solving for" in line
                        # OpenFOAM can emit several residual lines per iteration.
                        # Re-parsing the complete growing log and filesystem on
                        # every residual makes long steady runs quadratic in log
                        # size and can dominate the actual solve. Capture the
                        # first residual immediately, then throttle to 4 Hz.
                        if (residual_line and not residual_snapshot_seen) or now - last_snapshot_at >= 0.25:
                            self._refresh_result_snapshot(job_id, case_dir)
                            last_snapshot_at = now
                            residual_snapshot_seen = residual_snapshot_seen or residual_line

            exit_code = process.wait()
            if solver_log_path is not None:
                try:
                    solver_log_path.parent.mkdir(parents=True, exist_ok=True)
                    solver_log_path.write_text("\n".join(solver_output_lines) + ("\n" if solver_output_lines else ""), encoding="utf-8")
                except OSError as exc:
                    self._append_log(job_id, f"Warning: failed to write solver log artifact: {exc}")
            with self._lock:
                job = self.jobs[job_id]
                if job.status == "cancelled":
                    job.exitCode = exit_code
                    job.result = {
                        "caseDir": job.caseDir,
                        "exitCode": exit_code,
                        "logsCaptured": len(job.logs),
                        "logSummary": parse_solver_logs(job.solver, job.logs),
                        "meshQuality": read_case_mesh_quality(case_dir),
                    }
                    if solver_log_relative is not None:
                        job.result["solverLogPath"] = solver_log_relative
                    if job.solver == "openfoam":
                        job.result["patchMetrics"] = collect_patch_metrics(case_dir)
                    return
                job.exitCode = exit_code
                job.finishedAt = _utc_now()
                job.updatedAt = job.finishedAt
                if exit_code == 0:
                    advanced_mode = _case_advanced_mode(case_dir)
                    diagnostics_acceptance = (
                        write_openfoam_diagnostics_acceptance(case_dir, exit_code=exit_code, mode=advanced_mode)
                        if job.solver == "openfoam" and not is_validated_preset
                        else None
                    )
                    result_files = collect_result_files(case_dir)
                    diagnostic_files = collect_diagnostic_files(case_dir)
                    diagnostic_summary = parse_diagnostic_files(diagnostic_files)
                    mesh_quality = read_case_mesh_quality(case_dir)
                    patch_metrics = (
                        diagnostics_acceptance["patchMetrics"]
                        if job.solver == "openfoam" and diagnostics_acceptance
                        else collect_patch_metrics(case_dir)
                        if job.solver == "openfoam"
                        else None
                    )
                    quality_error = solver_output_quality_error(job.solver, job.logs, result_files)
                    diagnostics_error = openfoam_diagnostics_quality_error(diagnostics_acceptance) if diagnostics_acceptance else None
                    validated_result = read_validated_open_boundary_result(case_dir) if is_validated_preset else None
                    validated_error = validated_result_error(validated_result) if is_validated_preset else None
                    if job.execution == "preflight":
                        job.status = "blocked"
                        job.error = (
                            "OpenFOAM CHT mesh preflight completed; full foamMultiRun remains blocked until "
                            "the interface manifest is production-ready."
                        )
                        job.logs.append(job.error)
                    elif quality_error:
                        job.status = "failed"
                        job.error = quality_error
                        job.logs.append(quality_error)
                    elif diagnostics_error:
                        job.status = "failed"
                        job.error = diagnostics_error
                        job.logs.append(diagnostics_error)
                    elif validated_error:
                        job.status = "failed"
                        job.error = validated_error
                        job.logs.append(validated_error)
                    else:
                        job.status = "complete"
                        job.logs.append(f"Solver process exited successfully with code {exit_code}.")
                    if result_files:
                        job.logs.append(f"Collected {len(result_files)} VTK/VTU result file(s).")
                    if diagnostic_files:
                        job.logs.append(f"Collected {len(diagnostic_files)} solver diagnostic file(s).")
                    artifact_payload = finalize_run_artifacts(
                        case_dir,
                        job=job,
                        result_files=result_files,
                        diagnostic_files=diagnostic_files,
                        diagnostic_summary=diagnostic_summary,
                        mesh_quality=mesh_quality,
                        patch_metrics=patch_metrics,
                        diagnostics_acceptance=diagnostics_acceptance,
                    )
                    job.result = {
                        "caseDir": job.caseDir,
                        "exitCode": exit_code,
                        "logsCaptured": len(job.logs),
                        "logSummary": parse_solver_logs(job.solver, job.logs),
                        "resultFiles": result_files,
                        "diagnosticFiles": diagnostic_files,
                        "diagnosticSummary": diagnostic_summary,
                        "meshQuality": mesh_quality,
                        "progressive": False,
                        **artifact_payload,
                    }
                    if solver_log_relative is not None:
                        job.result["solverLogPath"] = solver_log_relative
                    if job.solver == "openfoam":
                        job.result["patchMetrics"] = patch_metrics
                        if validated_result is not None:
                            job.result["validatedBenchmark"] = validated_result
                        if diagnostics_acceptance is not None:
                            job.result["diagnosticsAcceptance"] = diagnostics_acceptance
                else:
                    job.status = "failed"
                    job.error = f"Solver process exited with code {exit_code}."
                    job.logs.append(job.error)
                    advanced_mode = _case_advanced_mode(case_dir)
                    diagnostics_acceptance = (
                        write_openfoam_diagnostics_acceptance(case_dir, exit_code=exit_code, mode=advanced_mode)
                        if job.solver == "openfoam" and not is_validated_preset
                        else None
                    )
                    result_files = collect_result_files(case_dir)
                    diagnostic_files = collect_diagnostic_files(case_dir)
                    diagnostic_summary = parse_diagnostic_files(diagnostic_files)
                    mesh_quality = read_case_mesh_quality(case_dir)
                    patch_metrics = (
                        diagnostics_acceptance["patchMetrics"]
                        if job.solver == "openfoam" and diagnostics_acceptance
                        else collect_patch_metrics(case_dir)
                        if job.solver == "openfoam"
                        else None
                    )
                    artifact_payload = finalize_run_artifacts(
                        case_dir,
                        job=job,
                        result_files=result_files,
                        diagnostic_files=diagnostic_files,
                        diagnostic_summary=diagnostic_summary,
                        mesh_quality=mesh_quality,
                        patch_metrics=patch_metrics,
                        diagnostics_acceptance=diagnostics_acceptance,
                    )
                    job.result = {
                        "caseDir": job.caseDir,
                        "exitCode": exit_code,
                        "logsCaptured": len(job.logs),
                        "logSummary": parse_solver_logs(job.solver, job.logs),
                        "resultFiles": result_files,
                        "diagnosticFiles": diagnostic_files,
                        "diagnosticSummary": diagnostic_summary,
                        "meshQuality": mesh_quality,
                        "progressive": False,
                        **artifact_payload,
                    }
                    if solver_log_relative is not None:
                        job.result["solverLogPath"] = solver_log_relative
                    if job.solver == "openfoam":
                        job.result["patchMetrics"] = patch_metrics
                        validated_result = read_validated_open_boundary_result(case_dir) if is_validated_preset else None
                        if validated_result is not None:
                            job.result["validatedBenchmark"] = validated_result
                        if diagnostics_acceptance is not None:
                            job.result["diagnosticsAcceptance"] = diagnostics_acceptance
        except Exception as exc:
            with self._lock:
                job = self.jobs[job_id]
                if job.status != "cancelled":
                    job.status = "failed"
                    job.error = f"Failed to start solver process: {exc}"
                    job.logs.append(job.error)
                    job.finishedAt = _utc_now()
                    job.updatedAt = job.finishedAt
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                job = self.jobs.get(job_id)
                if job is not None:
                    self._persist_job_unlocked(job)
