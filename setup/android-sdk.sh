#!/usr/bin/env bash
# Build tools for the Android app (android/): a JDK 17, the Android SDK, Gradle, and a Python 3.14 for
# Chaquopy (the app runs the repo's Python scanner/ on the phone; the build compiles it with this).
# No root, nothing system-wide. Safe to re-run: it skips what's already there.
# The first run shows Google's Android SDK licenses and asks you to accept them (sdkmanager --licenses).
#
#   setup/android-sdk.sh            install into ~/Android
#   setup/android-sdk.sh --build    install if needed, run the Python tests, build the APK
#   setup/android-sdk.sh --install  build, then install on a phone plugged in by USB (USB debugging on)
#
# Override the location with YF_ANDROID=/some/dir.
set -euo pipefail

ROOT="${YF_ANDROID:-$HOME/Android}"
JDK="$ROOT/jdk-17"
SDK="$ROOT/Sdk"
GRADLE_VERSION=8.10.2
GRADLE="$ROOT/gradle-$GRADLE_VERSION"
CMDLINE_TOOLS=commandlinetools-linux-11076708_latest.zip
REPO="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$ROOT"
need() { command -v "$1" >/dev/null || { echo "Needs '$1' (install it with your package manager)"; exit 1; }; }
need curl; need unzip; need tar; need uv

if [ ! -x "$JDK/bin/java" ]; then
  echo "== JDK 17 (Eclipse Temurin) -> $JDK"
  case "$(uname -m)" in x86_64) a=x64;; aarch64) a=aarch64;; *) echo "unsupported CPU $(uname -m)"; exit 1;; esac
  tmp=$(mktemp -d)
  curl -fL --retry 3 -o "$tmp/jdk.tgz" "https://api.adoptium.net/v3/binary/latest/17/ga/linux/$a/jdk/hotspot/normal/eclipse"
  mkdir -p "$JDK" && tar -xzf "$tmp/jdk.tgz" -C "$JDK" --strip-components=1
  rm -rf "$tmp"
fi
export JAVA_HOME="$JDK" PATH="$JDK/bin:$PATH"

SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMANAGER" ]; then
  echo "== Android command-line tools -> $SDK"
  tmp=$(mktemp -d)
  curl -fL --retry 3 -o "$tmp/tools.zip" "https://dl.google.com/android/repository/$CMDLINE_TOOLS"
  mkdir -p "$SDK/cmdline-tools" && unzip -q "$tmp/tools.zip" -d "$tmp"
  rm -rf "$SDK/cmdline-tools/latest" && mv "$tmp/cmdline-tools" "$SDK/cmdline-tools/latest"
  rm -rf "$tmp"
fi
export ANDROID_HOME="$SDK"

if [ ! -d "$SDK/platforms/android-35" ] || [ ! -d "$SDK/build-tools/35.0.0" ] || [ ! -d "$SDK/platform-tools" ]; then
  if [ ! -d "$SDK/licenses" ]; then
    echo "== Android SDK licenses: read them and answer y to accept (needed to download the SDK)"
    "$SDKMANAGER" --licenses
  fi
  echo "== SDK packages"
  "$SDKMANAGER" "platform-tools" "platforms;android-35" "build-tools;35.0.0"
fi

if [ ! -f "$REPO/android/gradle/wrapper/gradle-wrapper.jar" ] && [ ! -x "$GRADLE/bin/gradle" ]; then
  echo "== Gradle $GRADLE_VERSION -> $GRADLE"
  tmp=$(mktemp -d)
  curl -fL --retry 3 -o "$tmp/gradle.zip" "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
  unzip -q "$tmp/gradle.zip" -d "$ROOT" && rm -rf "$tmp"
fi

# Chaquopy's build Python: the same minor version as the phone's (android/app/build.gradle.kts, version = "3.14")
PY_VERSION=3.14
BUILD_PY="$(uv python find "$PY_VERSION" 2>/dev/null || true)"
if [ -z "$BUILD_PY" ]; then
  echo "== Python $PY_VERSION for the build (uv)"
  uv python install "$PY_VERSION"
  BUILD_PY="$(uv python find "$PY_VERSION")"
fi

printf 'sdk.dir=%s\nyf.buildPython=%s\n' "$SDK" "$BUILD_PY" > "$REPO/android/local.properties"
cd "$REPO/android"
if [ ! -f gradle/wrapper/gradle-wrapper.jar ]; then
  "$GRADLE/bin/gradle" wrapper --gradle-version "$GRADLE_VERSION" -q
fi
echo "Ready: JAVA_HOME=$JDK ANDROID_HOME=$SDK, build Python $BUILD_PY"

case "${1:-}" in
  --build)
    (cd "$REPO" && uv run python -m unittest discover -s tests)  # the scanner the app runs, phone bridge included
    ./gradlew --no-daemon assembleDebug
    echo "APK: $REPO/android/app/build/outputs/apk/debug/app-debug.apk" ;;
  --install)
    ./gradlew --no-daemon assembleDebug
    "$SDK/platform-tools/adb" install -r app/build/outputs/apk/debug/app-debug.apk ;;
esac
