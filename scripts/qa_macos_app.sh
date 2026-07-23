#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/release/FlowLab.app}"
MODE="${2:-internal}"
CONTRACT="$ROOT/desktop/macos/release-contract.json"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
SHELL_EXECUTABLE="$CONTENTS/MacOS/FlowLab"
BACKEND_EXECUTABLE="$RESOURCES/backend/FlowLabBackend"

fail() {
  echo "macOS package QA failed: $*" >&2
  exit 1
}

[[ "$MODE" == "internal" || "$MODE" == "external" ]] || fail "mode must be internal or external"
[[ -d "$APP" ]] || fail "app bundle not found at $APP"
[[ -x "$SHELL_EXECUTABLE" ]] || fail "native shell is missing or not executable"
[[ -x "$BACKEND_EXECUTABLE" ]] || fail "bundled backend is missing or not executable"
[[ ! -e "$RESOURCES/python-path.txt" ]] || fail "machine-local python-path.txt is prohibited"

SUPPORTED_ARCH="$(plutil -extract supportedArchitecture raw "$CONTRACT")"
MINIMUM_MACOS="$(plutil -extract minimumMacOSVersion raw "$CONTRACT")"
PLIST_MINIMUM="$(plutil -extract LSMinimumSystemVersion raw "$CONTENTS/Info.plist")"
[[ "$SUPPORTED_ARCH" == "arm64" ]] || fail "unsupported contract architecture $SUPPORTED_ARCH"
[[ "$PLIST_MINIMUM" == "$MINIMUM_MACOS" ]] || fail "Info.plist minimum $PLIST_MINIMUM does not match contract $MINIMUM_MACOS"

plutil -lint "$CONTENTS/Info.plist" >/dev/null
plutil -convert xml1 -o /dev/null "$RESOURCES/release-contract.json"
plutil -convert xml1 -o /dev/null "$RESOURCES/build-manifest.json"
codesign --verify --deep --strict "$APP"

while IFS= read -r candidate; do
  if file "$candidate" | grep -q "Mach-O"; then
    ARCHS="$(lipo -archs "$candidate")"
    [[ "$ARCHS" == "$SUPPORTED_ARCH" ]] || fail "$candidate has architectures '$ARCHS', expected '$SUPPORTED_ARCH'"
    while IFS= read -r dependency; do
      case "$dependency" in
        @*|/System/Library/*|/usr/lib/*) ;;
        "$candidate") ;;
        *) fail "$candidate has non-system absolute dependency $dependency" ;;
      esac
    done < <(otool -L "$candidate" | tail -n +2 | awk '{print $1}')
  fi
done < <(find "$CONTENTS" -type f -perm -111 -o -name "*.dylib" -o -name "*.so")

SHELL_MINIMUM="$(otool -l "$SHELL_EXECUTABLE" | awk '/minos/{print $2; exit}')"
[[ "$SHELL_MINIMUM" == "$MINIMUM_MACOS" ]] || fail "native shell minimum $SHELL_MINIMUM does not match contract $MINIMUM_MACOS"

if rg -a -l --fixed-strings "/opt/anaconda3/bin/python3" "$APP" >/dev/null; then
  fail "bundle contains the prohibited historical Anaconda interpreter path"
fi

if [[ "$MODE" == "external" ]]; then
  SIGNATURE="$(codesign -dv --verbose=4 "$APP" 2>&1)"
  grep -q "^Authority=Developer ID Application:" <<<"$SIGNATURE" || fail "Developer ID Application signature is absent"
  grep -Eq "^TeamIdentifier=[A-Z0-9]{10}$" <<<"$SIGNATURE" || fail "Apple Developer Team ID is absent"
  spctl --assess --type execute --verbose=4 "$APP"
  xcrun stapler validate "$APP"
fi

echo "macOS package QA passed ($MODE): $APP"
