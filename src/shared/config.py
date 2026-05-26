import os
from dotenv import load_dotenv
load_dotenv()

from psychopy import prefs
from psychopy.monitors import Monitor

# avoids delay in movie3 audio seek
prefs.hardware["audioLib"] = ["sounddevice"]
# prefs.hardware['general'] = ['glfw']

TR = 1.49 #seconds

OUTPUT_DIR = "output"

EYETRACKING_ROI = (60, 30, 660, 450)

EXP_SCREEN_XRANDR_NAME = os.environ.get("EXP_SCREEN_XRANDR_NAME", "DVI-D-0")

EXP_MONITOR = Monitor(
    name='__blank__',
    width=55,
    distance=180,
    )

# Defaults for fMRI/EEG rig (1920x1080, second screen, fullscreen).
# Override via env (EXP_WIN_W, EXP_WIN_H, EXP_WIN_SCREEN, EXP_WIN_FULLSCR=0)
# for development on a single-monitor box. When EXP_WIN_W/H/SCREEN are not
# set, geometry is auto-detected from the connected screens so we don't
# request a non-existent size or screen index (which would land in a
# decorated, non-fullscreen window).
def _detect_screen_geometry():
    try:
        import pyglet
        screens = pyglet.canvas.Display().get_screens()
    except Exception:
        return 1920, 1080, 1
    if not screens:
        return 1920, 1080, 1
    requested = int(os.environ.get("EXP_WIN_SCREEN", 1))
    idx = requested if requested < len(screens) else len(screens) - 1
    s = screens[idx]
    return s.width, s.height, idx


_det_w, _det_h, _det_screen = _detect_screen_geometry()

EXP_WINDOW = dict(
    size=(int(os.environ.get("EXP_WIN_W", _det_w)),
          int(os.environ.get("EXP_WIN_H", _det_h))),
    screen=int(os.environ.get("EXP_WIN_SCREEN", _det_screen)),
    fullscr=os.environ.get("EXP_WIN_FULLSCR", "1") not in ("0", "false", "False"),
    gammaErrorPolicy="warn",
    #waitBlanking=False,
)

EXP_MONITOR.setSizePix(EXP_WINDOW['size'])

CTL_WINDOW = dict(
    size=(1280, 1024),
    pos=(100, 0),
    screen=0,
    gammaErrorPolicy="warn",
    #    swapInterval=0.,
    waitBlanking=False,  # avoid ctrl window to block the script in case of differing refresh rate.
)

FRAME_RATE = 60

# task parameters
INSTRUCTION_DURATION = 3

WRAP_WIDTH = 2

# port for meg setup, also default for --eeg --parallel
PARALLEL_PORT_ADDRESS = os.environ.get("PARALLEL_PORT_ADDRESS", "/dev/parport1")

# serial port for --eeg --serial
SERIAL_PORT_ADDRESS = os.environ.get("SERIAL_PORT_ADDRESS", "/dev/ttyACM0")
