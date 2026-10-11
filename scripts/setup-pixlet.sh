#!/usr/bin/env bash
# Set up Pixlet apps: download Tronbyt's Pixlet (Apache-2.0) at a pinned version, check its SHA-256 and
# install it as native/pixlet/build/pixlet. Then install apps from Settings → Plugins → Pixlet apps.
# Safe to run again: a matching binary is kept.
#
#   ./scripts/setup-pixlet.sh
#
# Nothing here is committed: the binary lives in native/pixlet/build/ (git-ignored). Restart the server,
# or just reload the web page: the server looks for the binary each time it renders.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD="$ROOT/native/pixlet/build"
VERSION=v0.54.0
BASE="https://github.com/tronbyt/pixlet/releases/download/$VERSION"
# SHA-256 of each release archive (the same digests GitHub lists for the release assets)
SHA_linux_arm64=89b89556448ef54f023efcb1b842d275a411bf936367d9181ebdbdb288d97be9
SHA_linux_amd64=ed22e8835afd3546d462af5da87a5c4092376566edfae157f87adf646a4e4e2b

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/3  Checking tools"
missing=""
for t in curl tar sha256sum; do command -v "$t" >/dev/null || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "Missing:$missing"
  echo "Install them with:  sudo apt install curl tar coreutils"
  exit 1
fi
arch="$(uname -m)"
case "$arch" in
  aarch64|arm64) plat=linux-arm64; sha=$SHA_linux_arm64 ;;
  x86_64|amd64) plat=linux-amd64; sha=$SHA_linux_amd64 ;;
  *) echo "Pixlet has no build for $arch (a 64-bit OS is needed: arm64 or x86-64)."; exit 1 ;;
esac
echo "curl, tar, sha256sum OK; $arch -> $plat"

say "2/3  Pixlet $VERSION"
mkdir -p "$BUILD"
if [ -x "$BUILD/pixlet" ] && [ "$(cat "$BUILD/.arch" 2>/dev/null)" = "$arch" ] && [ "$(cat "$BUILD/.version" 2>/dev/null)" = "$VERSION" ]; then
  echo "already installed"
else
  tgz="$BUILD/pixlet_${VERSION}_$plat.tar.gz"
  if [ ! -f "$tgz" ] || [ "$(sha256sum "$tgz" | cut -d' ' -f1)" != "$sha" ]; then
    echo "downloading pixlet_${VERSION}_$plat.tar.gz (12 MB)"
    curl -fL --retry 3 -o "$tgz.part" "$BASE/pixlet_${VERSION}_$plat.tar.gz"
    [ "$(sha256sum "$tgz.part" | cut -d' ' -f1)" = "$sha" ] || { rm -f "$tgz.part"; echo "The download has the wrong checksum"; exit 1; }
    mv "$tgz.part" "$tgz"
  fi
  tmp="$BUILD/unpack"
  rm -rf "$tmp" && mkdir -p "$tmp"
  tar -xzf "$tgz" -C "$tmp" pixlet
  mv "$tmp/pixlet" "$BUILD/pixlet.tmp" && chmod 755 "$BUILD/pixlet.tmp" && mv "$BUILD/pixlet.tmp" "$BUILD/pixlet"
  rmdir "$tmp"
  echo "$arch" > "$BUILD/.arch"
  echo "$VERSION" > "$BUILD/.version"
  echo "installed $BUILD/pixlet"
fi

say "3/3  Checking that it runs"
"$BUILD/pixlet" version
app="$BUILD/check.star"
cat > "$app" <<'STAR'
load("render.star", "render")
def main():
    return render.Root(child = render.Text("OK"))
STAR
out="$BUILD/check.webp"
rm -f "$out"
if "$BUILD/pixlet" render "$app" -o "$out" --silent >/dev/null 2>&1 && [ -s "$out" ]; then
  echo "Pixlet renders. Install apps in Settings → Plugins → Pixlet apps; they appear in Apps → Plugins."
  rm -f "$out" "$app"
else
  echo "Pixlet didn't render the test app."
  exit 1
fi
