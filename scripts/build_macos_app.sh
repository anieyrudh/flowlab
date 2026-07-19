#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/release/FlowLab.app"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
PYTHON_BIN="${FLOWLAB_PYTHON:-$(command -v python3)}"

cd "$ROOT"
npm run build

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$RESOURCES"

xcrun clang \
  -fobjc-arc \
  -O2 \
  -framework Cocoa \
  -framework WebKit \
  desktop/macos/main.m \
  -o "$CONTENTS/MacOS/FlowLab"

cp desktop/macos/Info.plist "$CONTENTS/Info.plist"
cp -R dist "$RESOURCES/dist"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests' server/ "$RESOURCES/server/"
cp -R reference_cases "$RESOURCES/reference_cases"
cp server/requirements.txt "$RESOURCES/requirements.txt"
printf '%s\n' "$PYTHON_BIN" > "$RESOURCES/python-path.txt"

copy_evidence() {
  local source="$1"
  local target="$RESOURCES/$source"
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

codesign --force --deep --sign - "$APP"
echo "Built $APP"
