"""Marker dispatch for EEG synchronization.

Three transport backends are supported, selected at runtime via
:func:`configure`:

* ``"lsl"`` (default) — pylsl :class:`StreamOutlet`, int32 channel.
* ``"serial"``        — pyserial write to ``config.SERIAL_PORT_ADDRESS``
  (default ``/dev/ttyACM0``). One byte per marker.
* ``"parallel"``      — pyparallel write to ``config.PARALLEL_PORT_ADDRESS``
  (default ``/dev/parport1``). One byte per marker.

Marker codes — single positive-byte scheme so all three transports see
the **same** value (no negatives, no ``& 0xFF`` collisions, identical
analysis pipeline regardless of backend):

==========  ==========================================================
  0         TASK_START — once when ``task.run()`` enters its loop
  1         TASK_STOP  — once when ``task.run()`` exits
  2         GAME_RESET — once per ``emulator.reset()`` (start of a
              gameplay segment; the bk2 movie's leading frame)
  3         NON_GAME_FLIP — heartbeat on every non-gameplay PsychoPy
              flip (instructions, fixation, ratings, pause)
  4..15     reserved
 16..255    GAME_FRAME — gameplay frame heartbeat, encoded as
              ``16 + (level_step % 240)``. Pushed once per
              ``emulator.step()`` so the marker count exactly matches
              the bk2 frame count even when render frames are dropped.
              The byte wraps every 240 frames (~4 s at 60 Hz NES); the
              bk2 disambiguates the absolute frame index.
==========  ==========================================================

Lifecycle codes (0–3) and gameplay codes (16–255) are disjoint so
analysts can carve up the stream without ambiguity.
"""
import os
import pylsl

from . import config

EEG_MARKERS_ON_FLIP = True

# ---------------------------------------------------------------------------
# Marker codes (single byte, positive — identical on LSL/serial/parallel)
# ---------------------------------------------------------------------------
TASK_START = 0
TASK_STOP = 1
GAME_RESET = 2
NON_GAME_FLIP = 3

# Gameplay frame heartbeat is encoded into the [16, 255] range.
_GAME_FRAME_BASE = 16
_GAME_FRAME_MOD = 240  # 256 - _GAME_FRAME_BASE


def encode_frame(level_step):
    """Encode a gameplay frame index as a single positive byte (16..255)."""
    return _GAME_FRAME_BASE + (int(level_step) % _GAME_FRAME_MOD)


# LSL stream identity — overridable via env so concurrent sessions don't
# collide.
STREAM_NAME = os.environ.get("LSL_STREAM_NAME", "task_stimuli")
STREAM_TYPE = os.environ.get("LSL_STREAM_TYPE", "Markers")
STREAM_SOURCE_ID = os.environ.get("LSL_STREAM_SOURCE_ID", "task_stimuli_markers")

# ---------------------------------------------------------------------------
# Backend abstraction. configure() picks the active transport; send_signal()
# dispatches to it. LSL is the default.
# ---------------------------------------------------------------------------

_backend = None


class _LSLBackend:
    """LSL outlet on an int32 channel. Honors per-event timestamps so the
    marker carries the actual event time, not push() time."""
    def __init__(self):
        info = pylsl.StreamInfo(
            name=STREAM_NAME,
            type=STREAM_TYPE,
            channel_count=1,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_int32,
            source_id=STREAM_SOURCE_ID,
        )
        chans = info.desc().append_child("channels")
        ch = chans.append_child("channel")
        ch.append_child_value("label", "marker")
        ch.append_child_value("unit", "code")
        ch.append_child_value("type", "Marker")
        self._outlet = pylsl.StreamOutlet(info)

    def send(self, value, timestamp=None):
        # pylsl interprets timestamp=0.0 as "stamp at push time"; an explicit
        # local_clock() value pins the sample to the event time.
        ts = 0.0 if timestamp is None else float(timestamp)
        self._outlet.push_sample([int(value)], ts)


class _SerialBackend:
    """pyserial backend; writes one byte per marker.

    The ``timestamp`` argument is accepted for API parity but ignored —
    serial markers are stamped by the EEG amplifier on byte arrival.
    """
    def __init__(self, port_address):
        import serial
        self._port_address = port_address
        self._port = serial.Serial(port_address)

    def send(self, value, timestamp=None):
        self._port.write((int(value) & 0xFF).to_bytes(1, byteorder="big"))


class _ParallelBackend:
    """pyparallel backend; writes one byte per marker.

    The ``timestamp`` argument is accepted for API parity but ignored —
    parallel markers are stamped by the EEG amplifier on byte arrival.
    """
    def __init__(self, port_address):
        import parallel
        self._port_address = port_address
        # pyparallel.Parallel takes the device path or a port number; the
        # constructor signature differs slightly across forks.
        try:
            self._port = parallel.Parallel(port_address)
        except TypeError:
            self._port = parallel.Parallel(port=port_address)

    def send(self, value, timestamp=None):
        self._port.setData(int(value) & 0xFF)


def configure(backend="lsl", port=None):
    """Select the active marker transport.

    Parameters
    ----------
    backend : {"lsl", "serial", "parallel"}
        Where to send markers. Default is ``"lsl"``.
    port : str, optional
        Device path. For serial defaults to :data:`config.SERIAL_PORT_ADDRESS`,
        for parallel to :data:`config.PARALLEL_PORT_ADDRESS`. Ignored for LSL.
    """
    global _backend
    if backend == "lsl":
        _backend = _LSLBackend()
    elif backend == "serial":
        addr = port or config.SERIAL_PORT_ADDRESS
        _backend = _SerialBackend(addr)
    elif backend == "parallel":
        addr = port or config.PARALLEL_PORT_ADDRESS
        _backend = _ParallelBackend(addr)
    else:
        raise ValueError(f"unknown EEG backend: {backend!r}")
    return _backend


def get_outlet():
    """Compatibility shim for callers that want to eager-init LSL discovery.

    Configures the LSL backend if no backend is active yet, then returns the
    underlying :class:`pylsl.StreamOutlet` (or ``None`` for byte backends).
    """
    if _backend is None:
        configure("lsl")
    return getattr(_backend, "_outlet", None)


def send_signal(data, timestamp=None):
    """Push integer ``data`` to the active backend.

    Parameters
    ----------
    data : int
        Marker code (must fit in a byte; see module docstring).
    timestamp : float, optional
        Event time in the LSL clock (``pylsl.local_clock()``). For LSL this
        is forwarded to ``StreamOutlet.push_sample`` so the sample carries
        the true event time rather than the push time. For serial/parallel
        this argument is ignored (timing comes from the amplifier).
    """
    if _backend is None:
        configure("lsl")
    _backend.send(data, timestamp=timestamp)


def now():
    """Capture an LSL-clock timestamp at the moment of the event.

    Use this immediately after the event of interest, then pass the result
    to :func:`send_signal` so the marker is stamped with the event time
    rather than the push time.
    """
    return pylsl.local_clock()
