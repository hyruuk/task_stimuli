#!/usr/bin/env bash
# run_mario_eeg.sh
# Launch the mario-eeg task with the right env. Generates the participant
# design TSV on first run.
#
# Usage:
#   bash run_mario_eeg.sh <subject> <session> [extra main.py args...]
#
# Common overrides (env vars):
#   EXP_WIN_FULLSCR=0 EXP_WIN_SCREEN=0 EXP_WIN_W=800 EXP_WIN_H=600  # windowed
#   EEG=1                                                          # send --eeg (LSL by default)
#   EEG=1 EEG_PORT=serial                                          # markers via /dev/ttyACM0
#   EEG=1 EEG_PORT=parallel                                        # markers via /dev/parport1
#   OUTPUT_DIR=./output                                             # where logs go

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
PY="${VENV_DIR}/bin/python"
LOCAL_LIB_DIR="${ROOT_DIR}/.local-libs"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/output}"

if [[ ! -x "${PY}" ]]; then
  echo "venv not found at ${VENV_DIR}; run setup_env.sh first" >&2
  exit 1
fi
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <subject> <session> [extra main.py args...]" >&2
  exit 2
fi

SUBJECT="$1"; shift
SESSION="$1"; shift

# Use bundled libportaudio if the system one isn't installed.
if [[ -e "${LOCAL_LIB_DIR}/libportaudio.so.2" ]]; then
  export LIBRARY_PATH="${LOCAL_LIB_DIR}${LIBRARY_PATH:+:${LIBRARY_PATH}}"
  export LD_LIBRARY_PATH="${LOCAL_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

DESIGN_FILE="${ROOT_DIR}/data/videogames/mario/designs/sub-${SUBJECT}_design.tsv"
if [[ ! -e "${DESIGN_FILE}" ]]; then
  echo "Generating design TSV for sub-${SUBJECT}..."
  cd "${ROOT_DIR}" && "${PY}" src/sessions/ses-mario-eeg.py "${SUBJECT}"
fi

EEG_FLAGS=()
if [[ "${EEG:-0}" == "1" ]]; then
  EEG_FLAGS+=("--eeg")
  case "${EEG_PORT:-lsl}" in
    lsl)      ;;  # default
    serial)   EEG_FLAGS+=("--serial") ;;
    parallel) EEG_FLAGS+=("--parallel") ;;
    *) echo "EEG_PORT must be one of: lsl, serial, parallel" >&2; exit 2 ;;
  esac
fi

mkdir -p "${OUTPUT_DIR}"

cd "${ROOT_DIR}"
exec "${PY}" main.py \
  --subject "${SUBJECT}" \
  --session "${SESSION}" \
  --tasks mario-eeg \
  --output "${OUTPUT_DIR}" \
  --no-force-resolution \
  --run_on_battery \
  --skip-soundcheck \
  "${EEG_FLAGS[@]}" \
  "$@"
