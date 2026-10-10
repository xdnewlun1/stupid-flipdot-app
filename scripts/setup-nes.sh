#!/usr/bin/env bash
# Set up the NES app: fetch the agnes emulator (MIT licence) at a pinned commit, apply our patch
# (native/nes/agnes_flipdot.patch: per-pixel layers for the board, mappers 3 and 7) and build
# native/nes/build/nes_flipdot. Safe to run again: it rebuilds only what changed.
#
#   ./scripts/setup-nes.sh
#
# No games are included or downloaded: upload your own .nes files in the web UI (Apps > NES > NES ROMs).
# Nothing here is committed: the source and binary live in native/nes/build/, ROMs and saves in data/nes/
# (the server copies data/ into storage.db within a few seconds, so they survive a reinstall).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD="$ROOT/native/nes/build"
AG_PIN=0e4220b084c467e39c04805d955e78c463feadd0
AG_URL="https://raw.githubusercontent.com/kgabis/agnes/$AG_PIN"
DL="$BUILD/agnes-$AG_PIN"
declare -A SHA256=(
  [agnes.c]=2a8ff8770cc4fd1dacaa17b4841e7344fc1e458aba66351ee46e8423e7af618f
  [agnes.h]=a595b12240134ab46861562303a62f19e0e2796ae2ff0b71b5169d98425d45a1
  [LICENSE]=6f27bbb94b336f7940bfe1373adc71c0629960553a2abac082bc8caf95a950df
)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
sum() { sha256sum "$1" | cut -d' ' -f1; }

say "1/4  Checking build tools"
missing=""
for t in gcc make patch curl sha256sum python3; do command -v "$t" >/dev/null || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "Missing:$missing"
  echo "Install them with:  sudo apt install build-essential patch curl python3"
  exit 1
fi
echo "gcc, make, patch, curl OK"

say "2/4  Fetching agnes (${AG_PIN:0:7})"
mkdir -p "$DL"
for f in agnes.c agnes.h LICENSE; do
  if [ -f "$DL/$f" ] && [ "$(sum "$DL/$f")" = "${SHA256[$f]}" ]; then continue; fi
  curl -fsSL --retry 3 -o "$DL/$f.part" "$AG_URL/$f"
  [ "$(sum "$DL/$f.part")" = "${SHA256[$f]}" ] || { echo "agnes $f has the wrong checksum"; rm -f "$DL/$f.part"; exit 1; }
  mv "$DL/$f.part" "$DL/$f"
done
echo "agnes.c, agnes.h, LICENSE verified"
# a fresh copy every time, so the patch always applies to the pristine source
mkdir -p "$BUILD/src.new"
cp "$DL/agnes.c" "$DL/agnes.h" "$DL/LICENSE" "$BUILD/src.new/"
patch -s -p1 -d "$BUILD/src.new" < "$ROOT/native/nes/agnes_flipdot.patch" || { echo "the patch doesn't apply"; exit 1; }
if [ -d "$BUILD/src" ] && cmp -s "$BUILD/src.new/agnes.c" "$BUILD/src/agnes.c" && cmp -s "$BUILD/src.new/agnes.h" "$BUILD/src/agnes.h"; then
  rm -r "$BUILD/src.new"  # unchanged: keep the old timestamps so make has nothing to do
else
  rm -rf "$BUILD/src"
  mv "$BUILD/src.new" "$BUILD/src"
fi
echo "patched (agnes_flipdot.patch)"

say "3/4  Building native/nes/build/nes_flipdot"
# A build copied over from another machine (e.g. an x86 PC to the Pi) looks up to date to make but
# can't run here, so rebuild from scratch whenever it was made for a different CPU.
arch="$(uname -m)"
if [ -f "$BUILD/nes_flipdot" ] && [ "$(cat "$BUILD/.arch" 2>/dev/null)" != "$arch" ]; then
  echo "existing build isn't for $arch, rebuilding"
  make -C native/nes --no-print-directory clean >/dev/null
fi
make -C native/nes --no-print-directory >"$BUILD/make.log" 2>&1 || { tail -30 "$BUILD/make.log"; exit 1; }
echo "$arch" > "$BUILD/.arch"
echo "built: $(ls -l "$BUILD/nes_flipdot" | awk '{print $5}') bytes"

say "4/4  Checking that it runs"
# a 24 KB do-nothing test cartridge (JMP to itself), made here: no game is downloaded
python3 - "$BUILD/selftest.nes" <<'EOF'
import sys
prg = bytearray(16384)
prg[0:3] = b"\x4c\x00\xc0"                      # $C000: JMP $C000
prg[-6:] = b"\x00\xc0\x00\xc0\x00\xc0"          # NMI, RESET, IRQ -> $C000
open(sys.argv[1], "wb").write(b"NES\x1a\x01\x01" + bytes(10) + prg + bytes(8192))
EOF
frame=$( (sleep 2) | timeout 4 "$BUILD/nes_flipdot" "$BUILD/selftest.nes" -fps 10 2>"$BUILD/verify.log" | head -c 4 || true)
if [ "$frame" = "NES1" ]; then
  echo "The emulator starts and sends frames."
  echo "Now upload your own .nes files: Apps > Games > NES > NES ROMs, then Show on board and open /play."
else
  echo "The emulator didn't send a frame. Its output:"
  tail -20 "$BUILD/verify.log"
  exit 1
fi
