from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "electron" / "backend"
REQUIRED_OGRID_REVIEW_DIGEST = (
    "11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6"
)
EVIDENCE_FILES = (
    "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/artifacts/candidate-report.json",
    "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/artifacts/evidence-package.json",
    "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/serial/fine/runtime/periodic-diagnostics.json",
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v29-affine-grid-invariance/artifacts/affine-grid-invariance.json",
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v36-non-affine-mms/artifacts/non-affine-mms-report.json",
    "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v40-laminar-force-benchmark/artifacts/laminar-force-benchmark.json",
    "benchmarks/cases/open-boundary/campaigns/validated-campaign-pointer.json",
    "benchmarks/cases/open-boundary/campaigns/2026-07-16-laminar-all-hex-v4-followups/final-assessment-r2/final-assessment.json",
    "benchmarks/tools/flowlabPatchTractionAudit/flowlabPatchTractionAudit.C",
    "benchmarks/tools/flowlabPatchTractionAudit/Make/files",
    "benchmarks/tools/flowlabPatchTractionAudit/Make/options",
)
BUILD_INPUTS = (
    "package-lock.json",
    "desktop/electron/main.cjs",
    "desktop/electron/preload.cjs",
    "desktop/electron/release-contract.json",
    "desktop/electron/requirements-build.txt",
    "desktop/electron/runtime.cjs",
    "desktop/macos/backend_main.py",
    "scripts/build_electron_backend.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def copy_required(source_relative: str, internal_root: Path) -> None:
    source = ROOT / source_relative
    if not source.is_file():
        raise FileNotFoundError(f"Required packaged evidence is missing: {source_relative}")
    destination = internal_root / source_relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_backend(output: Path) -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(
            f"Electron backend packaging requires CPython 3.12; received {platform.python_version()}."
        )
    pyinstaller_version = importlib.metadata.version("pyinstaller")
    if pyinstaller_version != "6.21.0":
        raise RuntimeError(
            f"Electron backend packaging requires PyInstaller 6.21.0; received {pyinstaller_version}."
        )

    build_root = output.parent
    work_root = build_root / "pyinstaller-work"
    spec_root = build_root / "pyinstaller-spec"
    staged_dist = build_root / "pyinstaller-dist"
    config_root = build_root / "pyinstaller-config"
    for candidate in (output, work_root, spec_root, staged_dist, config_root):
        if candidate.exists():
            shutil.rmtree(candidate)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "FlowLabBackend",
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "uvicorn",
        "--distpath",
        str(staged_dist),
        "--workpath",
        str(work_root),
        "--specpath",
        str(spec_root),
        str(ROOT / "desktop" / "macos" / "backend_main.py"),
    ]
    build_environment = {
        **os.environ,
        "PYINSTALLER_CONFIG_DIR": str(config_root),
    }
    subprocess.run(command, cwd=ROOT, env=build_environment, check=True)
    shutil.copytree(staged_dist / "FlowLabBackend", output, symlinks=True)

    internal_root = output / "_internal"
    reference_cases = ROOT / "reference_cases"
    if not reference_cases.is_dir():
        raise FileNotFoundError("Required reference_cases directory is missing.")
    shutil.copytree(reference_cases, internal_root / "reference_cases")
    for relative in EVIDENCE_FILES:
        copy_required(relative, internal_root)

    dependencies = {
        distribution.metadata["Name"]: distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }
    manifest = {
        "schema": "flowlab.electron_backend_build_manifest.v1",
        "builtAtUtc": datetime.now(UTC).isoformat(),
        "sourceCommit": git("rev-parse", "HEAD"),
        "sourceTreeClean": not git("status", "--porcelain", "--untracked-files=no"),
        "target": {
            "platform": sys.platform,
            "architecture": platform.machine(),
        },
        "buildRuntime": {
            "implementation": platform.python_implementation(),
            "pythonVersion": platform.python_version(),
            "pythonExecutableName": Path(sys.executable).name,
            "pyinstallerVersion": pyinstaller_version,
        },
        "inputs": {relative: sha256(ROOT / relative) for relative in BUILD_INPUTS},
        "pythonDistributions": dict(
            sorted(dependencies.items(), key=lambda item: item[0].lower())
        ),
        "packagedEvidence": {
            relative: sha256(ROOT / relative) for relative in EVIDENCE_FILES
        },
    }
    (output / "flowlab-electron-backend-build-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    release_authorized = (
        os.environ.get("FLOWLAB_EXTERNAL_RELEASE_AUTHORIZED", "").strip().lower()
        == "true"
    )
    accepted_review_digest = os.environ.get(
        "FLOWLAB_OGRID_REVIEW_ACCEPTED_DIGEST", ""
    ).strip()
    release_tag = os.environ.get("FLOWLAB_RELEASE_TAG", "").strip()
    if release_authorized:
        if accepted_review_digest != REQUIRED_OGRID_REVIEW_DIGEST:
            raise RuntimeError(
                "External release authorization requires the exact controlled-review "
                "O-grid evidence digest."
            )
        if not release_tag:
            raise RuntimeError("External release authorization requires FLOWLAB_RELEASE_TAG.")
    release_receipt = {
        "schema": "flowlab.electron_release_authorization.v1",
        "externalPublicationAuthorized": release_authorized,
        "releaseTag": release_tag or None,
        "sourceCommit": manifest["sourceCommit"],
        "controlledReview": {
            "requiredPackageTreeDigest": REQUIRED_OGRID_REVIEW_DIGEST,
            "acceptedPackageTreeDigest": accepted_review_digest or None,
            "accepted": (
                release_authorized
                and accepted_review_digest == REQUIRED_OGRID_REVIEW_DIGEST
            ),
        },
        "github": {
            "repository": os.environ.get("GITHUB_REPOSITORY"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        },
        "scientificPromotionAuthorized": False,
        "claimBoundary": (
            "Downloadable desktop software; full O-grid remains a bounded steady "
            "incompressible laminar straight-pipe verification candidate unless a "
            "separate promotion record says otherwise."
        ),
    }
    (output / "flowlab-electron-release-authorization.json").write_text(
        json.dumps(release_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT not in output.parents:
        raise ValueError("Electron backend output must remain inside the repository.")
    build_backend(output)
    print(f"Built Electron backend at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
