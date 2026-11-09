#!/usr/bin/env bash
# Build the MAD68 HE libhmk firmware and wrap it in the vendor upgrade format.
#   tools/build.sh            -> out/MAD68HE_libhmk.bin (+ .elf, raw .img)
# Needs PlatformIO (pio) on PATH or PIO=/path/to/pio. The PlatformIO workspace
# is kept on a short path because the Windows 260-character limit breaks the
# build tree inside deep project folders.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
PIO="${PIO:-pio}"
PY="${PY:-python}"
export PLATFORMIO_WORKSPACE_DIR="${PLATFORMIO_WORKSPACE_DIR:-$HOME/.mad68-pio}"
cd "$here/firmware"
"$PY" setup.py -k mad68he >/dev/null
"$PIO" run -e mad68he
tc="$HOME/.platformio/packages/toolchain-gccarmnoneeabi/bin"
mkdir -p "$here/out"
cp "$PLATFORMIO_WORKSPACE_DIR/build/mad68he/firmware.elf" "$here/out/MAD68HE_libhmk.elf"
"$tc/arm-none-eabi-objcopy" -O binary "$here/out/MAD68HE_libhmk.elf" "$here/out/MAD68HE_libhmk.img"
"$PY" "$here/tools/pack.py" "$here/out/MAD68HE_libhmk.img" "$here/out/MAD68HE_libhmk.bin"
