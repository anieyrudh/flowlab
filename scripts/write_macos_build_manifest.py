from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS = (
    "package-lock.json",
    "desktop/macos/Info.plist",
    "desktop/macos/backend_main.py",
    "desktop/macos/main.m",
    "desktop/macos/release-contract.json",
    "desktop/macos/requirements-build.txt",
    "scripts/build_macos_app.sh",
    "scripts/qa_macos_app.sh",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*parts: str) -> str:
    return subprocess.check_output(parts, cwd=ROOT, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--minimum-macos", required=True)
    parser.add_argument("--sdk", required=True)
    parser.add_argument("--signing-identity", required=True)
    args = parser.parse_args()

    source_commit = command("git", "rev-parse", "HEAD")
    source_tree_clean = not command(
        "git", "status", "--porcelain", "--untracked-files=no"
    )
    dependencies = {
        distribution.metadata["Name"]: distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }
    manifest = {
        "schema": "flowlab.macos_build_manifest.v1",
        "builtAtUtc": datetime.now(UTC).isoformat(),
        "sourceCommit": source_commit,
        "sourceTreeClean": source_tree_clean,
        "target": {
            "platform": "macOS",
            "architecture": args.architecture,
            "minimumMacOSVersion": args.minimum_macos,
            "sdkVersion": args.sdk,
        },
        "buildRuntime": {
            "implementation": platform.python_implementation(),
            "pythonVersion": platform.python_version(),
            "pythonArchitecture": platform.machine(),
        },
        "signing": {
            "requestedIdentity": (
                "ad-hoc" if args.signing_identity == "-" else args.signing_identity
            )
        },
        "inputs": {
            path: sha256(ROOT / path)
            for path in INPUTS
        },
        "pythonDistributions": dict(sorted(dependencies.items(), key=lambda item: item[0].lower())),
        "nodeLockSha256": sha256(ROOT / "package-lock.json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
