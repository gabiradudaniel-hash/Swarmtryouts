"""Load config/drones.yaml.

Convention from the lab: radio address = 0xE7E7E7E7E<id>. The last hex digit
is the rigid-body (body) id, 1-9, set by the drone firmware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

MAX_DRONES_PER_RADIO = 3


def normalize_address(text: str) -> str:
    raw = str(text).strip()
    if raw.lower().startswith("0x"):
        raw = raw[2:]
    raw = raw.upper()
    if len(raw) != 10 or any(c not in "0123456789ABCDEF" for c in raw):
        raise ValueError(f"Bad Crazyflie address {text!r}. Expected 10 hex digits, e.g. E7E7E7E7E5.")
    return raw


def address_body_id(address: str) -> int:
    body_id = int(normalize_address(address)[-1], 16)
    if not 1 <= body_id <= 9:
        raise ValueError(f"Address {address!r} ends in {body_id}; body ids are 1-9.")
    return body_id


@dataclass(frozen=True)
class Drone:
    body_id: int
    address: str
    radio_index: int
    channel: int = 80
    datarate: str = "2M"
    deck_ip: str | None = None
    deck_port: int = 5000
    camera_rotation: int = 0  # degrees clockwise to turn the deck image upright: 0, 90, 180 or 270

    @property
    def name(self) -> str:
        return f"cf{self.body_id}"

    @property
    def uri(self) -> str:
        return f"radio://{self.radio_index}/{self.channel}/{self.datarate}/{self.address}"


@dataclass(frozen=True)
class SwarmConfig:
    drones: tuple[Drone, ...]
    motive_group: str = "239.255.42.99"
    motive_port: int = 1511
    extra: dict = field(default_factory=dict)

    def by_body_id(self, body_id: int) -> Drone:
        for drone in self.drones:
            if drone.body_id == body_id:
                return drone
        raise KeyError(body_id)


def _rotation(value, address: str) -> int:
    deg = int(value) % 360
    if deg not in (0, 90, 180, 270):
        raise ValueError(f"camera_rotation for {address} must be 0, 90, 180 or 270, got {value}")
    return deg


def load_config(path: str | Path) -> SwarmConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    drones = []
    for entry in data["drones"]:
        address = normalize_address(entry["address"])
        derived = address_body_id(address)
        body_id = int(entry.get("body_id", derived))
        if body_id != derived:
            raise ValueError(f"body_id {body_id} does not match address {address} (last digit {derived}).")
        drones.append(
            Drone(
                body_id=body_id,
                address=address,
                radio_index=int(entry.get("radio_index", 0)),
                channel=int(entry.get("channel", defaults.get("channel", 80))),
                datarate=str(entry.get("datarate", defaults.get("datarate", "2M"))),
                deck_ip=entry.get("deck_ip"),
                deck_port=int(entry.get("deck_port", 5000)),
                camera_rotation=_rotation(entry.get("camera_rotation", 0), address),
            )
        )
    ids = [d.body_id for d in drones]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Duplicate body ids: {ids}")
    per_radio: dict[int, int] = {}
    for d in drones:
        per_radio[d.radio_index] = per_radio.get(d.radio_index, 0) + 1
    for radio, count in per_radio.items():
        if count > MAX_DRONES_PER_RADIO:
            raise ValueError(f"Radio {radio} has {count} drones; the lab limit is {MAX_DRONES_PER_RADIO}.")
    motive = data.get("motive", {})
    return SwarmConfig(
        drones=tuple(drones),
        motive_group=motive.get("multicast", "239.255.42.99"),
        motive_port=int(motive.get("port", 1511)),
        extra={k: v for k, v in data.items() if k not in ("defaults", "drones", "motive")},
    )
