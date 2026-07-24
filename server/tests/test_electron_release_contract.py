import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_electron_release_contract_has_bounded_macos_and_windows_targets() -> None:
    contract = json.loads(
        (ROOT / "desktop/electron/release-contract.json").read_text(encoding="utf-8")
    )

    assert contract["schema"] == "flowlab.electron_release_contract.v1"
    assert contract["distributionShell"] == {
        "framework": "Electron",
        "renderer": "FlowLab React production build",
        "backend": "bundled local FastAPI service",
        "contextIsolationRequired": True,
        "nodeIntegrationAllowed": False,
        "rendererSandboxRequired": True,
    }
    assert contract["targets"]["macos-arm64"] == {
        "platform": "macOS",
        "architecture": "arm64",
        "minimumVersion": "13.0",
        "installers": ["dmg", "zip"],
    }
    assert contract["targets"]["windows-x64"] == {
        "platform": "Windows",
        "architecture": "x64",
        "minimumVersion": "Windows 11",
        "installers": ["squirrel", "zip"],
    }
    assert contract["pythonRuntime"]["bundledPerPlatform"] is True
    assert contract["pythonRuntime"]["externalInterpreterAllowed"] is False
    assert contract["authorization"] == {
        "packageCandidateBuildAuthorized": True,
        "externalPublicationAuthorized": False,
        "scientificPromotionAuthorized": False,
    }


def test_electron_runtime_is_locked_and_uses_a_narrow_preload_bridge() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    main = (ROOT / "desktop/electron/main.cjs").read_text(encoding="utf-8")
    preload = (ROOT / "desktop/electron/preload.cjs").read_text(encoding="utf-8")
    runtime = (ROOT / "desktop/electron/runtime.cjs").read_text(encoding="utf-8")

    assert package["main"] == "desktop/electron/main.cjs"
    assert package["devDependencies"]["electron"] == "43.2.0"
    for dependency in (
        "@electron-forge/cli",
        "@electron-forge/maker-dmg",
        "@electron-forge/maker-squirrel",
        "@electron-forge/maker-zip",
    ):
        assert package["devDependencies"][dependency] == "7.11.2"

    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert "webSecurity: true" in main
    assert "Content-Security-Policy" in main
    assert "setPermissionRequestHandler" in main
    assert 'ipcMain.handle("flowlab:save-files"' in main
    assert 'contextBridge.exposeInMainWorld("flowlabDesktop"' in preload
    assert "ipcRenderer.invoke(\"flowlab:save-files\"" in preload
    assert "executeJavaScript" not in main
    assert "shell: true" not in main
    assert "darwin" in runtime and "win32" in runtime


def test_electron_backend_requirements_are_exact_and_platform_aware() -> None:
    requirements = (
        ROOT / "desktop/electron/requirements-build.txt"
    ).read_text(encoding="utf-8").splitlines()

    assert requirements
    assert all("==" in line for line in requirements if line and not line.startswith("#"))
    assert 'macholib==1.16.4; sys_platform == "darwin"' in requirements
    assert 'pefile==2024.8.26; sys_platform == "win32"' in requirements
    assert 'pywin32-ctypes==0.2.3; sys_platform == "win32"' in requirements
    assert 'uvloop==0.22.1; sys_platform != "win32"' in requirements


def test_electron_forge_config_keeps_signing_secret_driven() -> None:
    config = (ROOT / "forge.config.cjs").read_text(encoding="utf-8")

    assert "FLOWLAB_MACOS_SIGN_IDENTITY" in config
    assert "FLOWLAB_APPLE_API_KEY" in config
    assert "FLOWLAB_WINDOWS_CERTIFICATE_FILE" in config
    assert "FLOWLAB_WINDOWS_CERTIFICATE_PASSWORD" in config
    assert "@electron-forge/maker-dmg" in config
    assert "@electron-forge/maker-squirrel" in config
    assert "@electron-forge/maker-zip" in config
    assert "contextIsolation" not in config


def test_electron_release_workflow_is_cross_platform_and_review_gated() -> None:
    candidate = (
        ROOT / ".github/workflows/desktop-electron-candidate.yml"
    ).read_text(encoding="utf-8")
    release = (
        ROOT / ".github/workflows/desktop-electron-release.yml"
    ).read_text(encoding="utf-8")

    for workflow in (candidate, release):
        assert "macos-15" in workflow
        assert "windows-2025" in workflow
        assert "desktop:electron:qa" in workflow
        assert "desktop:electron:qa:artifacts" in workflow
        assert "desktop:electron:smoke" in workflow
        assert "npm audit --audit-level=high" in workflow
        assert "npm run test:e2e" in workflow
    assert "workflow_dispatch" in release
    assert "FLOWLAB_EXTERNAL_RELEASE_AUTHORIZED" in release
    assert "FLOWLAB_OGRID_REVIEW_ACCEPTED_DIGEST" in release
    assert (
        "11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6"
        in release
    )
    assert "environment: external-release" in release
    assert "notarytool submit" in release
    assert "stapler staple" in release
    assert "--prerelease" in release
    assert "not independently validated or promotion-authorized" in release
