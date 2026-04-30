#!/bin/sh
# HWPX document skill wrapper — auto-installs hwpxskill and delegates to Python scripts
# Usage:
#   hwpx.sh build [args]           # Build HWPX from template/XML
#   hwpx.sh analyze [args]         # Analyze reference HWPX
#   hwpx.sh unpack <file> <dir>    # Unpack HWPX to directory
#   hwpx.sh pack <dir> <file>      # Pack directory to HWPX
#   hwpx.sh validate <file>        # Validate HWPX structure
#   hwpx.sh page-guard [args]      # Check page drift vs reference
#   hwpx.sh extract [args]         # Extract text from HWPX
#
# Auto-installs on first use: git clone + pip install lxml

set -e

# Resolve HWPX_DIR: skill folder first (provisioned), then PVC clone location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$SKILL_DIR/scripts/build_hwpx.py" ]; then
  HWPX_DIR="$SKILL_DIR"
else
  HWPX_DIR="${HWPX_DIR:-$HOME/.clawy/hwpxskill}"
fi
HWPX_PYLIB="${HWPX_PYLIB:-$HOME/.clawy/.pylib}"
ACTION="$1"
shift 2>/dev/null || true

if [ -z "$ACTION" ]; then
  echo '{"error":"Usage: hwpx.sh <build|analyze|unpack|pack|validate|page-guard|extract> [args]"}'
  exit 1
fi

# --- Auto-install (only if scripts not in skill folder, e.g. pruned by K8s Secret limit) ---
ensure_installed() {
  if [ -f "$HWPX_DIR/scripts/build_hwpx.py" ]; then
    return 0
  fi
  HWPX_DIR="$HOME/.clawy/hwpxskill"
  if [ -f "$HWPX_DIR/scripts/build_hwpx.py" ]; then
    return 0
  fi
  echo '{"status":"installing","message":"Cloning hwpxskill..."}'
  git clone --depth 1 https://github.com/Canine89/hwpxskill.git "$HWPX_DIR" 2>/dev/null || {
    echo '{"error":"Failed to clone hwpxskill. Check network connectivity."}'
    exit 1
  }
  echo '{"status":"installed","path":"'"$HWPX_DIR"'"}'
}

# --- Ensure lxml ---
ensure_lxml() {
  python3 -c "import lxml" 2>/dev/null && return 0
  # Try system package first (Alpine)
  apk add --no-cache py3-lxml 2>/dev/null && return 0
  # Fallback: pip install to user lib
  mkdir -p "$HWPX_PYLIB"
  python3 -m ensurepip 2>/dev/null || true
  python3 -m pip install --target="$HWPX_PYLIB" lxml 2>/dev/null || {
    pip3 install --target="$HWPX_PYLIB" lxml 2>/dev/null || {
      echo '{"error":"Failed to install lxml. Run: pip3 install lxml"}'
      exit 1
    }
  }
  export PYTHONPATH="$HWPX_PYLIB:${PYTHONPATH:-}"
}

# --- Set PYTHONPATH if pylib exists ---
if [ -d "$HWPX_PYLIB" ]; then
  export PYTHONPATH="$HWPX_PYLIB:${PYTHONPATH:-}"
fi

# --- Run Python script ---
run_py() {
  python3 "$@"
}

case "$ACTION" in
  build)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/build_hwpx.py" "$@"
    ;;
  analyze)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/analyze_template.py" "$@"
    ;;
  unpack)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/office/unpack.py" "$@"
    ;;
  pack)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/office/pack.py" "$@"
    ;;
  validate)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/validate.py" "$@"
    ;;
  page-guard)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/page_guard.py" "$@"
    ;;
  extract)
    ensure_installed
    ensure_lxml
    run_py "$HWPX_DIR/scripts/text_extract.py" "$@"
    ;;
  *)
    echo '{"error":"Unknown action: '"$ACTION"'. Use: build, analyze, unpack, pack, validate, page-guard, extract"}'
    exit 1
    ;;
esac
