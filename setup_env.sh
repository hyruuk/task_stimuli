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
LOCAL_LIB_DIR="${ROOT_DIR}/.local-libs"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/output}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[!]\033[0m %s\n' "$*" >&2; }

# Detect the upstream Ubuntu release. Linux Mint / Pop!_OS / etc. report
# their own version with lsb_release, but ship Ubuntu's apt index, so we
# need the *upstream* number for the wxPython extras URL.
detect_ubuntu_version() {
  if [[ -r /etc/upstream-release/lsb-release ]]; then
    awk -F= '/DISTRIB_RELEASE/{print $2}' /etc/upstream-release/lsb-release
    return
  fi
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}" in
      noble)  echo 24.04; return ;;
      jammy)  echo 22.04; return ;;
      focal)  echo 20.04; return ;;
    esac
  fi
  lsb_release -rs 2>/dev/null || echo 22.04
}
UBUNTU_VERSION="$(detect_ubuntu_version)"
WX_FIND_LINKS="${WX_FIND_LINKS:-https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-${UBUNTU_VERSION}/}"
log "Detected upstream Ubuntu ${UBUNTU_VERSION} (wxPython wheels: ${WX_FIND_LINKS})"

# ---------------------------------------------------------------------------
# 1. System (apt) dependencies
# ---------------------------------------------------------------------------
# libwebkit2gtk was renamed 4.0 -> 4.1 in Ubuntu 24.04 (noble).
if dpkg --compare-versions "${UBUNTU_VERSION}" ge 24.04 2>/dev/null; then
  WEBKIT_PKG="libwebkit2gtk-4.1-dev"
else
  WEBKIT_PKG="libwebkit2gtk-4.0-dev"
fi

APT_PACKAGES=(
  # build toolchain
  build-essential pkg-config cmake swig git curl ca-certificates
  # python build deps
  python3-dev python3-venv libffi-dev libssl-dev
  # PsychoPy / wxPython dependencies
  libsdl2-dev libsdl2-2.0-0
  libgtk-3-dev
  "${WEBKIT_PKG}"
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
# The PyPI wheel (currently 4.2.5) is built against wxWidgets 3.2.6, but
# Ubuntu 24.04 ships 3.2.4 — loading psychopy.visual then dies with
# "libwx_baseu-3.2.so.0: version `WXU_3.2.6' not found". The wxpython.org
# extras index hosts wheels built against the libwx that actually ships in
# each Ubuntu release, so we prefer it on Linux. We pin to the exact wheel
# version listed there (uv treats --find-links as additive to PyPI, so an
# un-pinned install would re-pick the broken 4.2.5 from PyPI). PyPI is the
# fallback for Ubuntu versions the extras index doesn't cover yet.
wx_install_ok() {
  "${PY}" - <<'PYEOF' 2>/dev/null
import wx  # noqa: F401
from wx import App  # noqa: F401
PYEOF
}

PY_TAG="$("${PY}" -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')"
log "Looking up wxPython wheel for ${PY_TAG} at ${WX_FIND_LINKS}"
WX_PINNED_VERSION="$(curl -fsSL "${WX_FIND_LINKS}" 2>/dev/null \
  | grep -oE "wxPython-[0-9]+\.[0-9]+\.[0-9]+-${PY_TAG}-${PY_TAG}-linux_x86_64\.whl" \
  | sed -E "s/^wxPython-([0-9.]+)-.*/\\1/" \
  | sort -V | tail -1 || true)"

wx_installed_ok=0
if [[ -n "${WX_PINNED_VERSION}" ]]; then
  log "Installing wxPython==${WX_PINNED_VERSION} from extras.wxpython.org"
  if "${PIP_INSTALL[@]}" --force-reinstall --find-links "${WX_FIND_LINKS}" "wxPython==${WX_PINNED_VERSION}" \
     && wx_install_ok; then
    wx_installed_ok=1
    log "wxPython ${WX_PINNED_VERSION} installed and imports cleanly."
  fi
else
  warn "No matching wxPython wheel found at ${WX_FIND_LINKS} for ${PY_TAG}."
fi

if (( wx_installed_ok == 0 )); then
  warn "Falling back to PyPI wxPython>=4.2.2"
  "${PIP_INSTALL[@]}" --force-reinstall "wxPython>=4.2.2"
  if ! wx_install_ok; then
    warn "wxPython fails to import. This usually means the system libwxbase/libwxgtk"
    warn "is older than what the PyPI wheel needs (wxWidgets 3.2.6+)."
    warn "On Ubuntu 24.04 the system libwx is 3.2.4 — use the extras index instead."
    exit 1
  fi
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
# 6b. Task data: mario.stimuli submodule + input/output folders
# ---------------------------------------------------------------------------
# Only the mario.stimuli submodule is required for the mario-eeg session;
# the other submodules (friends, things, etc.) are large and not needed
# here, so we deliberately do not run a recursive submodule update.
MARIO_SUBMODULE="data/videogames/mario"
if [[ -z "$(ls -A "${ROOT_DIR}/${MARIO_SUBMODULE}" 2>/dev/null)" ]]; then
  log "Initialising mario.stimuli submodule (${MARIO_SUBMODULE})"
  if ! (cd "${ROOT_DIR}" && git submodule update --init -- "${MARIO_SUBMODULE}"); then
    warn "git submodule update failed for ${MARIO_SUBMODULE}."
    warn "  The submodule URL is git@github.com:courtois-neuromod/mario.stimuli.git"
    warn "  Make sure your SSH key is added to GitHub and has access to courtois-neuromod."
    exit 1
  fi
else
  log "mario.stimuli already populated at ${MARIO_SUBMODULE}"
fi

# The ROM and per-level save-states in mario.stimuli are git-annex pointers
# (datalad). The submodule clone only checks out the symlinks; we need
# `datalad get` (or `git annex get`) to fetch the actual file contents.
MARIO_ROM="${ROOT_DIR}/${MARIO_SUBMODULE}/SuperMarioBros-Nes/rom.nes"
if [[ ! -s "${MARIO_ROM}" ]]; then
  log "Fetching mario.stimuli annexed content (ROM + save-states)..."
  fetched=0
  if command -v datalad >/dev/null 2>&1; then
    if (cd "${ROOT_DIR}/${MARIO_SUBMODULE}" && datalad get .); then
      fetched=1
    else
      warn "datalad get failed; trying git-annex directly"
    fi
  fi
  if (( fetched == 0 )) && command -v git-annex >/dev/null 2>&1; then
    if (cd "${ROOT_DIR}/${MARIO_SUBMODULE}" && git annex get .); then
      fetched=1
    fi
  fi
  if (( fetched == 0 )); then
    warn "Could not fetch annexed mario.stimuli content."
    warn "Install datalad (pip install datalad) or git-annex (apt install git-annex),"
    warn "then run: cd ${MARIO_SUBMODULE} && datalad get ."
    exit 1
  fi
  if [[ ! -s "${MARIO_ROM}" ]]; then
    warn "rom.nes is still empty after fetch — annex content not available from configured remotes."
    exit 1
  fi
  log "Annexed content fetched (rom.nes resolves to a real file)."
else
  log "mario.stimuli annexed content already present (rom.nes is a real file)."
fi

# designs/ is shipped with the submodule, but make sure it's writable for
# the per-subject design TSVs that run_mario_eeg.sh generates on first run.
mkdir -p "${ROOT_DIR}/${MARIO_SUBMODULE}/designs"

log "Ensuring output directory exists: ${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

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
