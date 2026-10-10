#!/usr/bin/env bash
# Set up the Doom app: fetch doomgeneric (GPL-2.0) at a pinned commit, build it with our flipdot
# platform file (native/doom), and download Freedoom: Phase 1 (BSD licence) into data/doom/.
# Safe to run again: finished steps are skipped.
#
#   ./scripts/setup-doom.sh                          build + Freedoom
#   ./scripts/setup-doom.sh --shareware DOOM1.WAD    also install the DOOM shareware WAD (a file or a URL)
#   DATA=/path/to/data ./scripts/setup-doom.sh       if the server runs with --data elsewhere
#
# Nothing here is committed: the source and binary live in native/doom/build/, the WADs in data/doom/
# (the server copies data/ into storage.db within a few seconds, so they survive a reinstall).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
DATA="${DATA:-$ROOT/data}"
BUILD="$ROOT/native/doom/build"
SRC="$BUILD/doomgeneric-src"
DG_REPO=https://github.com/ozkl/doomgeneric.git
DG_PIN=dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284
FD_VERSION=0.13.0
FD_URL="https://github.com/freedoom/freedoom/releases/download/v$FD_VERSION/freedoom-$FD_VERSION.zip"
FD_ZIP_SHA256=3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59
FD_WAD_SHA256=7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d
SHAREWARE_MD5=f0cefca49926d00903cf57551d901abe  # DOOM1.WAD v1.9
SHAREWARE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --shareware) SHAREWARE="${2:-}"; [ -n "$SHAREWARE" ] || { echo "--shareware needs a file or URL"; exit 2; }; shift 2 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/5  Checking build tools"
missing=""
for t in gcc make git curl unzip sha256sum; do command -v "$t" >/dev/null || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "Missing:$missing"
  echo "Install them with:  sudo apt install build-essential git curl unzip"
  exit 1
fi
echo "gcc, make, git, curl, unzip OK"

say "2/5  Fetching doomgeneric ($DG_PIN)"
mkdir -p "$BUILD"
if [ ! -d "$SRC/.git" ]; then
  git clone --quiet "$DG_REPO" "$SRC"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$DG_PIN" ]; then
  git -C "$SRC" cat-file -e "$DG_PIN^{commit}" 2>/dev/null || git -C "$SRC" fetch --quiet origin
  git -C "$SRC" checkout --quiet --detach "$DG_PIN"
fi
echo "doomgeneric at $(git -C "$SRC" rev-parse --short HEAD)"

say "3/5  Building native/doom/build/doomgeneric_flipdot"
# A build copied over from another machine (e.g. an x86 PC to the Pi) looks up to date to make but
# can't run here, so rebuild from scratch whenever it was made for a different CPU.
arch="$(uname -m)"
if [ -f "$BUILD/doomgeneric_flipdot" ] && [ "$(cat "$BUILD/.arch" 2>/dev/null)" != "$arch" ]; then
  echo "existing build isn't for $arch, rebuilding"
  make -C native/doom --no-print-directory clean >/dev/null
fi
make -C native/doom -j"$(nproc)" --no-print-directory >"$BUILD/make.log" 2>&1 || { tail -30 "$BUILD/make.log"; exit 1; }
echo "$arch" > "$BUILD/.arch"
echo "built: $(ls -l "$BUILD/doomgeneric_flipdot" | awk '{print $5}') bytes"

say "4/5  Game data in $DATA/doom"
mkdir -p "$DATA/doom"
wad="$DATA/doom/freedoom1.wad"
if [ -f "$wad" ] && [ "$(sha256sum "$wad" | cut -d' ' -f1)" = "$FD_WAD_SHA256" ]; then
  echo "freedoom1.wad already there"
else
  zip="$BUILD/freedoom-$FD_VERSION.zip"
  if [ ! -f "$zip" ] || [ "$(sha256sum "$zip" | cut -d' ' -f1)" != "$FD_ZIP_SHA256" ]; then
    echo "downloading Freedoom $FD_VERSION (24 MB)"
    curl -fL --retry 3 -o "$zip.part" "$FD_URL"
    [ "$(sha256sum "$zip.part" | cut -d' ' -f1)" = "$FD_ZIP_SHA256" ] || { echo "Freedoom download has the wrong checksum"; exit 1; }
    mv "$zip.part" "$zip"
  fi
  unzip -p "$zip" "freedoom-$FD_VERSION/freedoom1.wad" > "$wad.tmp"
  [ "$(sha256sum "$wad.tmp" | cut -d' ' -f1)" = "$FD_WAD_SHA256" ] || { echo "freedoom1.wad has the wrong checksum"; exit 1; }
  mv "$wad.tmp" "$wad"
  unzip -p "$zip" "freedoom-$FD_VERSION/COPYING.txt" > "$DATA/doom/FREEDOOM-COPYING.txt"
  echo "installed freedoom1.wad"
fi
if [ -n "$SHAREWARE" ]; then
  dst="$DATA/doom/doom1.wad"
  case "$SHAREWARE" in
    http://*|https://*) curl -fL --retry 3 -o "$dst.tmp" "$SHAREWARE" ;;
    *) cp "$SHAREWARE" "$dst.tmp" ;;
  esac
  if [ "$(md5sum "$dst.tmp" | cut -d' ' -f1)" = "$SHAREWARE_MD5" ]; then
    echo "DOOM1.WAD v1.9 shareware recognised"
  else
    echo "Note: this isn't the v1.9 shareware DOOM1.WAD (checksum differs); using it anyway"
  fi
  mv "$dst.tmp" "$dst"
  echo "installed doom1.wad (pick \"DOOM shareware\" in the Doom app's settings)"
fi

say "5/5  Checking that it runs"
frame=$(cd "$BUILD" && (sleep 6) | timeout 8 "$BUILD/doomgeneric_flipdot" -iwad "$wad" -warp 1 1 -skill 3 -flipfps 10 -nogui \
           2>"$BUILD/verify.log" | head -c 4 || true)
if [ "$frame" = "FDM2" ]; then
  echo "Doom starts and sends frames. Run the \"Doom\" app (Apps > Games) and open /play to control it."
else
  echo "Doom didn't send a frame. Its output:"
  tail -20 "$BUILD/verify.log"
  exit 1
fi
