import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_macos_release_contract_is_self_contained_and_arm64() -> None:
    contract = json.loads(
        (ROOT / "desktop/macos/release-contract.json").read_text(encoding="utf-8")
    )

    assert contract["schema"] == "flowlab.macos_release_contract.v1"
    assert contract["platform"] == "macOS"
    assert contract["supportedArchitecture"] == "arm64"
    assert contract["minimumMacOSVersion"] == "13.0"
    assert contract["pythonRuntime"] == {
        "implementation": "CPython",
        "versionSeries": "3.12",
        "bundled": True,
        "externalInterpreterAllowed": False,
    }
    assert "Developer ID Application signature" in contract["releaseGates"]["externalRelease"]
    assert "clean supported arm64 Mac launch" in contract["releaseGates"]["externalRelease"]


def test_macos_build_uses_the_bundled_backend_and_enforces_contract() -> None:
    build_script = (ROOT / "scripts/build_macos_app.sh").read_text(encoding="utf-8")
    shell_source = (ROOT / "desktop/macos/main.m").read_text(encoding="utf-8")
    qa_script = (ROOT / "scripts/qa_macos_app.sh").read_text(encoding="utf-8")

    assert "python-path.txt" not in build_script
    assert "FLOWLAB_PYTHON" not in build_script
    assert "FLOWLAB_PYTHON" not in shell_source
    assert 'resourceURL:@"backend/FlowLabBackend"' in shell_source
    assert "reserveLoopbackPort" in shell_source
    assert "self.backendPort = [self reserveLoopbackPort]" in shell_source
    assert 'environment[@"FLOWLAB_BACKEND_PORT"] = [NSString stringWithFormat:@"%ld", (long)self.backendPort]' in shell_source
    assert "static const NSInteger FlowLabPort" not in shell_source
    assert "--target-architecture" in build_script
    assert "-mmacosx-version-min" in build_script
    assert "python-path.txt is prohibited" in qa_script
    assert "Developer ID Application signature is absent" in qa_script


def test_macos_build_dependencies_are_exactly_pinned() -> None:
    requirements = (
        ROOT / "desktop/macos/requirements-build.txt"
    ).read_text(encoding="utf-8").splitlines()

    assert requirements
    assert "pyinstaller==6.21.0" in [line.lower() for line in requirements]
    assert all("==" in line for line in requirements if line and not line.startswith("#"))
