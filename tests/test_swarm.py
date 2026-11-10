import json
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swarm.config import address_body_id, load_config, normalize_address
from swarm.pose_hub import PoseHub
from swarm.sim import FakeMotive
from swarm.stream import AideckClient, DeckReader

YAML = """
defaults: {channel: 80, datarate: 2M}
drones:
  - {address: "0xE7E7E7E7E2", radio_index: 0}
  - {body_id: 5, address: "E7E7E7E7E5", radio_index: 1, deck_ip: "127.0.0.1", camera_rotation: 180}
"""


def write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text)
    return p


class ConfigTests(unittest.TestCase):
    def test_body_id_from_address(self):
        self.assertEqual(address_body_id("0xE7E7E7E7E5"), 5)
        self.assertEqual(normalize_address("0xe7e7e7e7e9"), "E7E7E7E7E9")
        with self.assertRaises(ValueError):
            address_body_id("E7E7E7E7E0")
        with self.assertRaises(ValueError):
            normalize_address("E7E7")

    def test_load_and_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(write(tmp, "d.yaml", YAML))
        self.assertEqual([d.body_id for d in cfg.drones], [2, 5])
        self.assertEqual(cfg.by_body_id(5).uri, "radio://1/80/2M/E7E7E7E7E5")
        self.assertEqual(cfg.by_body_id(5).camera_rotation, 180)
        self.assertEqual(cfg.by_body_id(2).camera_rotation, 0)

    def test_mismatch_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = 'drones:\n  - {body_id: 3, address: "E7E7E7E7E5"}\n'
            with self.assertRaises(ValueError):
                load_config(write(tmp, "a.yaml", bad))
            four = "drones:\n" + "".join(f'  - {{address: "E7E7E7E7E{i}", radio_index: 0}}\n' for i in (1, 2, 3, 4))
            with self.assertRaises(ValueError):
                load_config(write(tmp, "b.yaml", four))
            dup = 'drones:\n  - {address: "E7E7E7E7E1"}\n  - {address: "E7E7E7E7E1"}\n'
            with self.assertRaises(ValueError):
                load_config(write(tmp, "c.yaml", dup))


class PoseHubTests(unittest.TestCase):
    def test_per_drone_matrix_and_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "axis_calibration_5.json").write_text(json.dumps(
                {"body_id": 5, "matrix": [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], "yaw_offset_deg": 0}))
            hub = PoseHub([2, 5], tmp)
            self.assertEqual(hub.missing_calibration, [2])
            hub.on_rigid_body(5, (1.0, 2.0, 3.0), (0, 0, 0, 1))
            hub.on_rigid_body(2, (1.0, 2.0, 3.0), (0, 0, 0, 1))
            hub.on_rigid_body(9, (0, 0, 0), None)  # unknown body ignored
            self.assertEqual(hub.get(5).position, (-1.0, 3.0, 2.0))   # drone 5 map
            self.assertEqual(hub.get(2).position, (1.0, 3.0, 2.0))    # default map
            self.assertIsNone(hub.get(9))
            self.assertTrue(hub.all_fresh(0.5))
            time.sleep(0.15)
            self.assertFalse(hub.fresh(5, 0.1))

    def test_fake_motive_feeds_all_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = PoseHub([2, 4, 5, 6, 7], tmp)
            sim = FakeMotive(hub.on_rigid_body, [2, 4, 5, 6, 7], hz=200)
            sim.start()
            time.sleep(0.3)
            self.assertTrue(hub.all_fresh(0.2))
            sim.shutdown()


def fake_deck(port_holder, frames, delay, stop):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])
    srv.settimeout(0.2)
    while not stop.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        try:
            for i in range(frames):
                payload = bytes([i % 256]) * 1000
                head = struct.pack("<BHHBBI", 0xBC, 324, 244, 1, 1, len(payload))
                conn.sendall(struct.pack("<HBB", len(head) + 2, 0, 0) + head)
                for off in range(0, len(payload), 400):  # chunked
                    chunk = payload[off:off + 400]
                    conn.sendall(struct.pack("<HBB", len(chunk) + 2, 0, 0) + chunk)
                time.sleep(delay)
        except OSError:
            pass
        finally:
            conn.close()
    srv.close()


class StreamTests(unittest.TestCase):
    def test_client_parses_chunked_frame(self):
        stop, port = threading.Event(), []
        t = threading.Thread(target=fake_deck, args=(port, 3, 0.0, stop), daemon=True)
        t.start()
        while not port:
            time.sleep(0.01)
        c = AideckClient("127.0.0.1", port[0], 2.0)
        c.connect()
        w, h, fmt, payload = c.read_frame()
        self.assertEqual((w, h, fmt, len(payload)), (324, 244, 1, 1000))
        c.close()
        stop.set()

    def test_five_decks_fps_and_reconnect(self):
        stop, ports, readers = threading.Event(), [], []
        for _ in range(5):
            holder = []
            threading.Thread(target=fake_deck, args=(holder, 6, 0.1, stop), daemon=True).start()
            while not holder:
                time.sleep(0.01)
            ports.append(holder[0])
        got = []
        for k, port in enumerate(ports):
            r = DeckReader(f"cf{k}", "127.0.0.1", port, on_frame=lambda *a: got.append(a[0]), reconnect_s=0.1)
            r.start()
            readers.append(r)
        time.sleep(1.5)
        for r in readers:
            s = r.snapshot()
            self.assertGreater(s.frames, 6, s)       # server closes after 6 frames; reader reconnects
            self.assertGreaterEqual(s.reconnects, 1)
            self.assertGreater(s.fps, 3.0)
            r.stop()
        stop.set()
        self.assertEqual(set(got), {f"cf{k}" for k in range(5)})


if __name__ == "__main__":
    unittest.main()
