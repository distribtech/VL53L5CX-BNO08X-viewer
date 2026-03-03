#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bin/flash_esp32.sh [options]

Options:
  --port PORT            Serial port (default: /dev/ttyUSB0)
  --fqbn FQBN            Board FQBN (default: esp32:esp32:esp32)
  --sketch DIR           Sketch directory (default: firmware/vl53l5cx_reader)
  --build-dir DIR        Build directory (default: /tmp/vl53l5cx_reader_build)
  --baud N               Flash baud for LittleFS image (default: 921600)
  --no-firmware          Skip firmware upload
  --no-fs                Skip LittleFS image build/upload
  -h, --help             Show this help
EOF
}

PORT="/dev/ttyUSB0"
FQBN="esp32:esp32:esp32"
SKETCH_DIR="firmware/vl53l5cx_reader"
BUILD_DIR="/tmp/vl53l5cx_reader_build"
BAUD="921600"
DO_FIRMWARE=1
DO_FS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --fqbn) FQBN="$2"; shift 2 ;;
    --sketch) SKETCH_DIR="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --baud) BAUD="$2"; shift 2 ;;
    --no-firmware) DO_FIRMWARE=0; shift ;;
    --no-fs) DO_FS=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "arduino-cli not found in PATH" >&2
  exit 1
fi

if [[ ! -d "$SKETCH_DIR" ]]; then
  echo "Sketch directory not found: $SKETCH_DIR" >&2
  exit 1
fi

if [[ $DO_FS -eq 1 && ! -d "$SKETCH_DIR/data" ]]; then
  echo "Data directory not found: $SKETCH_DIR/data" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR"
echo "==> Compiling sketch to $BUILD_DIR"
arduino-cli compile --fqbn "$FQBN" --build-path "$BUILD_DIR" "$SKETCH_DIR"

if [[ $DO_FIRMWARE -eq 1 ]]; then
  echo "==> Uploading firmware to $PORT"
  arduino-cli upload --fqbn "$FQBN" --port "$PORT" --input-dir "$BUILD_DIR"
fi

if [[ $DO_FS -eq 0 ]]; then
  echo "Done (firmware only)."
  exit 0
fi

if [[ ! -f "$BUILD_DIR/partitions.csv" ]]; then
  echo "Missing $BUILD_DIR/partitions.csv after compile." >&2
  exit 1
fi

if [[ ! -f "$BUILD_DIR/flash_args" ]]; then
  echo "Missing $BUILD_DIR/flash_args after compile." >&2
  exit 1
fi

read -r FS_NAME FS_OFFSET FS_SIZE FS_SUBTYPE < <(
  awk -F, '
    function trim(s){gsub(/^[ \t]+|[ \t]+$/, "", s); return s}
    BEGIN {chosen=""; fallback=""}
    /^[ \t]*#/ {next}
    NF < 5 {next}
    {
      name=trim($1); type=trim($2); subtype=trim($3); off=trim($4); size=trim($5)
      if (type != "data") next
      if (subtype == "littlefs") {
        print name, off, size, subtype
        exit 0
      }
      if (subtype == "spiffs" && fallback == "") {
        fallback = name " " off " " size " " subtype
      }
    }
    END {
      if (fallback != "") print fallback
    }
  ' "$BUILD_DIR/partitions.csv"
)

if [[ -z "${FS_OFFSET:-}" || -z "${FS_SIZE:-}" ]]; then
  echo "Could not find a LittleFS/SPIFFS partition in $BUILD_DIR/partitions.csv" >&2
  exit 1
fi

to_bytes() {
  local v="${1//[[:space:]]/}"
  case "$v" in
    0x*|0X*) printf '%d\n' "$((v))" ;;
    *[Kk]) printf '%d\n' "$(( ${v%[Kk]} * 1024 ))" ;;
    *[Mm]) printf '%d\n' "$(( ${v%[Mm]} * 1024 * 1024 ))" ;;
    *) printf '%d\n' "$((v))" ;;
  esac
}

FS_SIZE_BYTES="$(to_bytes "$FS_SIZE")"

MKLITTLEFS_BIN="$(find "$HOME/.arduino15/packages/esp32/tools/mklittlefs" -type f -name mklittlefs 2>/dev/null | sort -V | tail -n1 || true)"
ESPTOOL_BIN="$(find "$HOME/.arduino15/packages/esp32/tools/esptool_py" -type f -name esptool 2>/dev/null | sort -V | tail -n1 || true)"

if [[ -z "$MKLITTLEFS_BIN" || ! -x "$MKLITTLEFS_BIN" ]]; then
  echo "mklittlefs tool not found under ~/.arduino15/packages/esp32/tools/mklittlefs" >&2
  exit 1
fi

if [[ -z "$ESPTOOL_BIN" || ! -x "$ESPTOOL_BIN" ]]; then
  echo "esptool tool not found under ~/.arduino15/packages/esp32/tools/esptool_py" >&2
  exit 1
fi

LFS_IMAGE="$BUILD_DIR/littlefs.bin"
echo "==> Building LittleFS image ($FS_NAME/$FS_SUBTYPE at $FS_OFFSET, size $FS_SIZE_BYTES bytes)"
"$MKLITTLEFS_BIN" -c "$SKETCH_DIR/data" -b 4096 -p 256 -s "$FS_SIZE_BYTES" "$LFS_IMAGE"

read -r FLASH_FLAGS_LINE < "$BUILD_DIR/flash_args"
read -r -a FLASH_FLAGS <<< "$FLASH_FLAGS_LINE"

echo "==> Uploading LittleFS image to $PORT"
"$ESPTOOL_BIN" \
  --chip esp32 \
  --port "$PORT" \
  --baud "$BAUD" \
  --before default-reset \
  --after hard-reset \
  write-flash -z \
  "${FLASH_FLAGS[@]}" \
  "$FS_OFFSET" "$LFS_IMAGE"

echo "Done."
