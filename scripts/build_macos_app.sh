#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/release/FlowLab.app"
CONTRACT="$ROOT/desktop/macos/release-contract.json"
BUILD_PYTHON="${FLOWLAB_BUILD_PYTHON:-$(command -v python3)}"
SIGNING_IDENTITY="${FLOWLAB_CODESIGN_IDENTITY:--}"
ARCHITECTURE="$(plutil -extract supportedArchitecture raw "$CONTRACT")"
MINIMUM_MACOS="$(plutil -extract minimumMacOSVersion raw "$CONTRACT")"
PYTHON_SERIES="$(plutil -extract pythonRuntime.versionSeries raw "$CONTRACT")"
PYINSTALLER_VERSION="$(plutil -extract backendPackage.builderVersion raw "$CONTRACT")"
SDK_VERSION="$(xcrun --sdk macosx --show-sdk-version)"
BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/flowlab-macos-build.XXXXXX")"
STAGED_APP="$BUILD_ROOT/FlowLab.app"
CONTENTS="$STAGED_APP/Contents"
RESOURCES="$CONTENTS/Resources"
BACKEND_ROOT="$RESOURCES/backend"

cleanup() {
  rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

cd "$ROOT"

[[ "$ARCHITECTURE" == "arm64" ]] || {
  echo "Unsupported macOS release architecture: $ARCHITECTURE" >&2
  exit 1
}
"$BUILD_PYTHON" -c "import platform,sys; assert platform.machine() == '$ARCHITECTURE', platform.machine(); assert f'{sys.version_info.major}.{sys.version_info.minor}' == '$PYTHON_SERIES', sys.version"
"$BUILD_PYTHON" -c "import PyInstaller; assert PyInstaller.__version__ == '$PYINSTALLER_VERSION', PyInstaller.__version__"

npm run build

mkdir -p "$CONTENTS/MacOS" "$RESOURCES"

xcrun clang \
  -arch "$ARCHITECTURE" \
  "-mmacosx-version-min=$MINIMUM_MACOS" \
  -fobjc-arc \
  -O2 \
  -framework Cocoa \
  -framework WebKit \
  desktop/macos/main.m \
  -o "$CONTENTS/MacOS/FlowLab"

cp desktop/macos/Info.plist "$CONTENTS/Info.plist"
cp -R dist "$RESOURCES/dist"
cp desktop/macos/release-contract.json "$RESOURCES/release-contract.json"
cp desktop/macos/requirements-build.txt "$RESOURCES/requirements-build.txt"

PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-config" "$BUILD_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name FlowLabBackend \
  --target-architecture "$ARCHITECTURE" \
  --paths "$ROOT" \
  --collect-submodules uvicorn \
  --distpath "$BUILD_ROOT/backend-dist" \
  --workpath "$BUILD_ROOT/backend-work" \
  --specpath "$BUILD_ROOT/backend-spec" \
  desktop/macos/backend_main.py

mkdir -p "$BACKEND_ROOT"
cp -R "$BUILD_ROOT/backend-dist/FlowLabBackend/." "$BACKEND_ROOT/"
cp -R reference_cases "$BACKEND_ROOT/_internal/reference_cases"

copy_evidence() {
  local source="$1"
  local target="$BACKEND_ROOT/_internal/$source"
  mkdir -p "$(dirname "$target")"
  cp "$ROOT/$source" "$target"
}

copy_evidence "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/artifacts/candidate-report.json"
copy_evidence "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/artifacts/evidence-package.json"
copy_evidence "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2/serial/fine/runtime/periodic-diagnostics.json"
copy_evidence "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v29-affine-grid-invariance/artifacts/affine-grid-invariance.json"
copy_evidence "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v36-non-affine-mms/artifacts/non-affine-mms-report.json"
copy_evidence "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v40-laminar-force-benchmark/artifacts/laminar-force-benchmark.json"
copy_evidence "benchmarks/cases/open-boundary/campaigns/validated-campaign-pointer.json"
copy_evidence "benchmarks/cases/open-boundary/campaigns/2026-07-16-laminar-all-hex-v4-followups/final-assessment-r2/final-assessment.json"
copy_evidence "benchmarks/tools/flowlabPatchTractionAudit/flowlabPatchTractionAudit.C"
copy_evidence "benchmarks/tools/flowlabPatchTractionAudit/Make/files"
copy_evidence "benchmarks/tools/flowlabPatchTractionAudit/Make/options"

"$BUILD_PYTHON" scripts/write_macos_build_manifest.py \
  --output "$RESOURCES/build-manifest.json" \
  --architecture "$ARCHITECTURE" \
  --minimum-macos "$MINIMUM_MACOS" \
  --sdk "$SDK_VERSION" \
  --signing-identity "$SIGNING_IDENTITY"

codesign --force --deep --sign "$SIGNING_IDENTITY" "$STAGED_APP"
bash scripts/qa_macos_app.sh "$STAGED_APP" internal

mkdir -p "$(dirname "$APP")"
rm -rf "$APP"
mv "$STAGED_APP" "$APP"
echo "Built $APP"
