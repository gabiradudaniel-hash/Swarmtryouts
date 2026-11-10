"""Read the Bitcraze wifi-img-streamer from many AI decks at once.

Protocol (same as legacy aideck_capture/capture.py): CPX header
<HBB length routing function>, image header <B magic 0xBC, H width, H height,
B depth, B format (0 raw, else JPEG), I size>, then chunks <HBB> + payload.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass

AIDECK_MAGIC = 0xBC


class AideckClient:
    def __init__(self, ip, port, timeout_sec=5.0):
        self._ip, self._port, self._timeout = ip, port, timeout_sec
        self._sock = None

    @property
    def connected(self):
        return self._sock is not None

    def connect(self):
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self._timeout)
        sock.connect((self._ip, self._port))
        self._sock = sock

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def read_frame(self):
        """Return (width, height, fmt, payload). fmt 0 = raw, otherwise JPEG."""
        length, _routing, _function = struct.unpack("<HBB", self._rx(4))
        header = self._rx(length - 2)
        if len(header) < 11:
            raise ValueError(f"Image header is {len(header)} bytes, expected at least 11")
        magic, width, height, _depth, fmt, size = struct.unpack_from("<BHHBBI", header)
        if magic != AIDECK_MAGIC:
            raise ValueError(f"Unexpected magic byte 0x{magic:02X}")
        payload = bytearray()
        while len(payload) < size:
            chunk_len, _dst, _src = struct.unpack("<HBB", self._rx(4))
            payload.extend(self._rx(chunk_len - 2))
        return width, height, fmt, bytes(payload[:size])

    def _rx(self, size):
        if self._sock is None:
            raise RuntimeError("Not connected")
        data = bytearray()
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Socket closed while reading")
            data.extend(chunk)
        return data


@dataclass
class DeckStats:
    name: str
    frames: int = 0
    bytes: int = 0
    reconnects: int = 0
    last_error: str = ""
    last_frame_t: float = 0.0
    first_frame_t: float = 0.0
    max_gap_s: float = 0.0
    size: tuple = (0, 0)

    @property
    def fps(self):
        span = self.last_frame_t - self.first_frame_t
        return (self.frames - 1) / span if self.frames > 1 and span > 0 else 0.0


class DeckReader(threading.Thread):
    """One thread per deck. on_frame(name, t_monotonic, width, height, fmt, payload)."""

    def __init__(self, name, ip, port=5000, on_frame=None, reconnect_s=2.0, timeout_s=5.0):
        super().__init__(daemon=True, name=f"deck-{name}")
        self.stats = DeckStats(name)
        self._client = AideckClient(ip, port, timeout_s)
        self._on_frame = on_frame
        self._reconnect_s = reconnect_s
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()

    @property
    def connected(self):
        """True while the TCP connection to the deck is open (it may still be waiting for frames)."""
        return self._client.connected

    def stop(self):
        self._stop_evt.set()
        self._client.close()

    def snapshot(self):
        with self._lock:
            s = self.stats
            return DeckStats(s.name, s.frames, s.bytes, s.reconnects, s.last_error,
                             s.last_frame_t, s.first_frame_t, s.max_gap_s, s.size)

    def run(self):
        while not self._stop_evt.is_set():
            try:
                if not self._client.connected:
                    self._client.connect()
                width, height, fmt, payload = self._client.read_frame()
                now = time.monotonic()
                with self._lock:
                    s = self.stats
                    if s.frames == 0:
                        s.first_frame_t = now
                    else:
                        s.max_gap_s = max(s.max_gap_s, now - s.last_frame_t)
                    s.frames += 1
                    s.bytes += len(payload)
                    s.last_frame_t = now
                    s.size = (width, height)
                if self._on_frame:
                    self._on_frame(self.stats.name, now, width, height, fmt, payload)
            except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
                if self._stop_evt.is_set():
                    break
                with self._lock:
                    self.stats.last_error = str(exc)
                    self.stats.reconnects += 1
                self._client.close()
                self._stop_evt.wait(self._reconnect_s)
        self._client.close()
