#!/usr/bin/env bash
# setup_env.sh
# Reproducible environment setup for task_stimuli (PsychoPy + stable-retro).
# Tested on Ubuntu 22.04 LTS, Python 3.10, x86_64.
#
# Usage:
#   bash setup_env.sh                  # full install (apt + venv)
#   SKIP_APT=1 bash setup_env.sh       # skip the system-deps step (use bundled portaudio fallback)
#   PYTHON_VERSION=3.10 bash setup_env.sh
#
# After running, activate the venv with:
#   source .venv/bin/activate
# and launch the task with the helper:
#   bash run_mario_eeg.sh <subject> <session>

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
UBUNTU_VERSION="$(lsb_release -rs 2>/dev/null || echo 22.04)"
WX_FIND_LINKS="${WX_FIND_LINKS:-https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-${UBUNTU_VERSION}/}"
LOCAL_LIB_DIR="${ROOT_DIR}/.local-libs"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[!]\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. System (apt) dependencies
# ---------------------------------------------------------------------------
APT_PACKAGES=(
  # build toolchain
  build-essential pkg-config cmake swig git curl ca-certificates
  # python build deps
  python3-dev python3-venv libffi-dev libssl-dev
  # PsychoPy / wxPython dependencies
  libsdl2-dev libsdl2-2.0-0
  libgtk-3-dev
  libwebkit2gtk-4.0-dev
  libnotify-dev
  libxtst-dev
  libsm-dev
  freeglut3-dev
  libglu1-mesa-dev
  libegl1-mesa-dev
  libgles2-mesa-dev
  libxkbcommon-dev
  libgstreamer1.0-dev
  libgstreamer-plugins-base1.0-dev
  # audio
  portaudio19-dev libasound2-dev libpulse-dev libsndfile1-dev
  # video / image
  ffmpeg
  libavformat-dev libavcodec-dev libavutil-dev libswscale-dev
  libjpeg-dev libpng-dev libtiff-dev
  # USB / serial / EEG
  libusb-1.0-0-dev
  # stable-retro deps
  zlib1g-dev libbz2-dev liblzma-dev
  # X / fonts (psychopy text rendering)
  libxcb-xinerama0
  libxrandr-dev
  libxinerama-dev
  libfreetype6-dev
  fonts-dejavu-core
)

apt_install_if_missing() {
  local missing=()
  for pkg in "$@"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      missing+=("$pkg")
    fi
  done
  if (( ${#missing[@]} == 0 )); then
    log "All required apt packages already installed."
    return 0
  fi
  log "Installing ${#missing[@]} apt packages (requires sudo): ${missing[*]}"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
}

if [[ "${SKIP_APT:-0}" == "1" ]]; then
  warn "SKIP_APT=1 — skipping system package installation."
else
  if sudo -n true 2>/dev/null || sudo true; then
    apt_install_if_missing "${APT_PACKAGES[@]}"
  else
    warn "sudo unavailable — skipping apt step. Falling back to local libportaudio."
    SKIP_APT=1
  fi
fi

# ---------------------------------------------------------------------------
# 1b. No-sudo fallback: extract libportaudio2 from a downloaded .deb so
#     sounddevice can dlopen() it via LIBRARY_PATH.
# ---------------------------------------------------------------------------
if [[ "${SKIP_APT:-0}" == "1" ]] || ! ldconfig -p 2>/dev/null | grep -q libportaudio.so.2; then
  if ! [[ -e "${LOCAL_LIB_DIR}/libportaudio.so.2" ]]; then
    log "Fetching libportaudio2 to ${LOCAL_LIB_DIR} (no sudo needed)"
    mkdir -p "${LOCAL_LIB_DIR}"
    tmp_d=$(mktemp -d)
    (cd "$tmp_d" && apt-get download libportaudio2 >/dev/null)
    deb_file="$(ls "$tmp_d"/libportaudio2*.deb)"
    extract_d="$tmp_d/extracted"
    mkdir -p "$extract_d"
    dpkg -x "$deb_file" "$extract_d"
    so_real=$(find "$extract_d" -name 'libportaudio.so.2.*' -print -quit)
    cp -L "$so_real" "${LOCAL_LIB_DIR}/"
    so_basename=$(basename "$so_real")
    ln -sfn "$so_basename" "${LOCAL_LIB_DIR}/libportaudio.so.2"
    ln -sfn "$so_basename" "${LOCAL_LIB_DIR}/libportaudio.so"
    rm -rf "$tmp_d"
    log "libportaudio fallback installed at ${LOCAL_LIB_DIR}"
  fi
fi

# ---------------------------------------------------------------------------
# 2. uv (https://github.com/astral-sh/uv)
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
log "uv $(uv --version)"

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtualenv at ${VENV_DIR} (python ${PYTHON_VERSION})"
  uv venv --python "${PYTHON_VERSION}" "${VENV_DIR}"
fi

# Use uv pip with explicit --python so we don't need to source the venv.
PY="${VENV_DIR}/bin/python"
PIP_INSTALL=(uv pip install --python "${PY}")

"${PIP_INSTALL[@]}" --upgrade pip setuptools wheel

# ---------------------------------------------------------------------------
# 4. wxPython
# ---------------------------------------------------------------------------
# Try PyPI first (4.2.2+ ships manylinux wheels); fall back to the
# wxpython.org extras index that hosts wheels built against the local libgtk.
log "Installing wxPython..."
if ! "${PIP_INSTALL[@]}" "wxPython>=4.2.2"; then
  warn "PyPI wxPython install failed; retrying with extras.wxpython.org wheels"
  "${PIP_INSTALL[@]}" --find-links "${WX_FIND_LINKS}" wxPython
fi

# ---------------------------------------------------------------------------
# 5. PsychoPy + numerical/utility deps
# ---------------------------------------------------------------------------
# psychopy 2024.1.x – 2024.2.5 require an unpublished 'pypi-search' package,
# so we let the resolver pick the latest (currently 2026.x) which has dropped
# that broken dep. PsychoPy itself pins pyglet to 1.5.27.
log "Installing PsychoPy and utilities..."
"${PIP_INSTALL[@]}" \
  "psychopy" \
  "numpy<2" \
  pyserial \
  pylsl \
  python-dotenv \
  "pandas>=1.1.1" \
  "tqdm>=4.60.0" \
  textdistance \
  colorama \
  scikit-video \
  sounddevice \
  soundfile \
  Pillow

# ---------------------------------------------------------------------------
# 6. stable-retro (drop-in for the retired gym-retro)
# ---------------------------------------------------------------------------
log "Installing stable-retro..."
"${PIP_INSTALL[@]}" stable-retro

# ---------------------------------------------------------------------------
# 7. Smoke test
# ---------------------------------------------------------------------------
log "Smoke testing imports..."
LIB_OVERRIDE=""
if [[ -e "${LOCAL_LIB_DIR}/libportaudio.so.2" ]]; then
  LIB_OVERRIDE="LIBRARY_PATH=${LOCAL_LIB_DIR} LD_LIBRARY_PATH=${LOCAL_LIB_DIR}"
fi
env ${LIB_OVERRIDE} "${PY}" - <<'PYEOF'
import importlib, sys
mods = ["psychopy", "psychopy.visual", "wx", "retro",
        "pandas", "sounddevice", "serial", "tqdm",
        "textdistance", "colorama", "PIL"]
ok = True
for m in mods:
    try:
        importlib.import_module(m)
        print(f"  ok  {m}")
    except Exception as e:
        print(f"  FAIL {m}: {e}", file=sys.stderr)
        ok = False
if not ok:
    sys.exit(1)
import retro
print(f"  retro version: {retro.__version__}")
PYEOF

log "Done. Activate the environment with:"
echo "    source ${VENV_DIR}/bin/activate"
if [[ -e "${LOCAL_LIB_DIR}/libportaudio.so.2" ]]; then
  echo "    Then export LD_LIBRARY_PATH=${LOCAL_LIB_DIR}:\$LD_LIBRARY_PATH"
  echo "    (or just run ./run_mario_eeg.sh which sets it automatically)"
fi
