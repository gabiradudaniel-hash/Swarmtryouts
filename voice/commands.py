"""Turn a first-responder sentence into per-drone actions.

The room frame matches the swarm window: +x is forward, +y is left, +z is up.
A small translation is 20 cm on the named axis. A small rotate is 20° of yaw.
Positive yaw turns toward +y (counterclockwise when seen from above).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

STEP_M = 0.20
YAW_STEP_DEG = 20.0

HINT = (
    "Try: Drone 1 go to the left of the object · Drone 2 stay over the object · "
    "Move a little to the right · Rotate a little · Closer · Higher · "
    "Circle the object · All drones circle the object · Swap drone 1 and drone 3 · "
    "Return to start · Stop. A small move is 20 cm. A small rotate is 20°."
)

DEMO = (
    "Drone 1 go to the left of the object",
    "Drone 2 stay over the object",
    "Drone 3, you go from the right",
)

AGAIN = {
    "again",
    "repeat",
    "same",
    "same thing",
    "do that again",
    "do it again",
    "once more",
}

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "first": 1,
    "second": 2,
    "third": 3,
}

_SIDE_WORDS = {
    "left": "left",
    "right": "right",
    "front": "front",
    "forward": "front",
    "ahead": "front",
    "back": "back",
    "behind": "back",
    "backward": "back",
    "rear": "back",
}

_DIRECTION_WORDS = (
    ("forward", "forward"),
    ("ahead", "forward"),
    ("backward", "back"),
    ("backwards", "back"),
    ("behind", "back"),
    ("back", "back"),
    ("left", "left"),
    ("right", "right"),
    ("upward", "up"),
    ("up", "up"),
    ("higher", "up"),
    ("climb", "up"),
    ("ascend", "up"),
    ("rise", "up"),
    ("downward", "down"),
    ("down", "down"),
    ("lower", "down"),
    ("descend", "down"),
)

_SPLIT = re.compile(
    r"(?<!\d)[.!?;]+(?!\d)"
    r"|,\s*(?=(?:drone|cf|copter|uav|all|everyone)\b)"
    r"|\s+and\s+(?=(?:drone|cf|copter|uav|all|everyone|move|go|fly|"
    r"rotate|turn|yaw|spin|inspect|examine|check|survey|scan|stay|"
    r"hover|hold|face|look|nudge|shift|slide|circle|orbit|return|come|"
    r"head|swap|switch|stop|halt|get|back|climb|descend)\b)",
    re.IGNORECASE,
)
# "swap drone 1 and drone 3" is one clause, so its "and" must not split.
_SWAP_PAIR = re.compile(
    r"\b(swap|switch|exchange|trade)(\s+(?:places\s+of\s+|positions\s+of\s+)?"
    r"(?:drones?|cf|copter|uav)\s*\w+)\s+and\s+(?=(?:drone|cf|copter|uav)\b)",
    re.IGNORECASE,
)
_PAIR_SWAP = re.compile(
    r"\b((?:drone|cf|copter|uav)\s*\w+)\s+and\s+((?:drone|cf|copter|uav)\s*\w+)\s+"
    r"(swap|switch|exchange|trade)\b",
    re.IGNORECASE,
)

_DRONE = re.compile(
    r"\b(?:drone|cf|copter|uav)\s*(?:number\s*)?#?\s*(\d+|one|two|three)\b"
)
_ORDINAL = re.compile(r"\b(first|second|third)\s+(?:drone|cf|copter|uav)\b")
_OF_OBJECT = re.compile(
    r"\b(left|right|front|forward|ahead|back|behind|backward|rear)\b"
    r"(?:\s+\w+){0,4}\s+of\s+"
    r"(?:the\s+|an?\s+)?(?:object|cube|box|chair|target|it)\b"
)
_FROM_SIDE = re.compile(
    r"\bfrom\s+(?:the\s+)?(left|right|front|forward|ahead|back|behind|backward|rear)\b"
)
_THE_SIDE = re.compile(
    r"\b(?:on|to|at)\s+the\s+(left|right|front|back|rear)\s+side\b"
)
_ITS_SIDE = re.compile(r"\b(?:on|to)\s+its\s+(left|right|front|back|rear)\b")
_BESIDE = re.compile(
    r"\b(behind|front)\s+(?:of\s+)?(?:the\s+|an?\s+)?(?:object|cube|box|chair|target|it)\b"
)
_OBJECT_SIDE = re.compile(r"\b(?:objects?|cubes?|box|boxs|chairs?|targets?)\s+(left|right|front|back|rear)\s+side\b")


@dataclass(frozen=True)
class Action:
    kind: str
    scope: str = "active"
    drone: int | None = None
    side: str = ""
    directions: tuple[str, ...] = ()
    distance: float = STEP_M
    yaw_deg: float = YAW_STEP_DEG
    turns: float | None = None
    radius: float | None = None
    sign: float = 1.0
    partner: int | None = None
    phrase: str = ""


def normalize(text: str) -> str:
    text = text.lower().replace("'", "")
    text = re.sub(r"[^a-z0-9.\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_clauses(text: str) -> list[str]:
    clauses = []
    for line in re.split(r"[\n\r]+", text):
        line = _SWAP_PAIR.sub(r"\1\2 with ", line)
        line = _PAIR_SWAP.sub(r"\1 \3 with \2", line)
        for part in _SPLIT.split(line):
            cleaned = normalize(part)
            if cleaned:
                clauses.append(cleaned)
    return clauses


def _subject(text: str):
    match = _DRONE.search(text) or _ORDINAL.search(text)
    if match:
        token = match.group(1)
        number = int(token) if token.isdigit() else _NUMBER_WORDS[token]
        if number not in (1, 2, 3):
            return None, f"This scene has drones 1, 2, and 3 (heard drone {number})."
        return number, ""
    if re.search(
        r"\b(all drones|all of them|everyone|every drone|the swarm|whole swarm|all three|all)\b",
        text,
    ):
        return "all", ""
    return None, ""


def _scope(subject):
    if subject == "all":
        return "all", None
    if isinstance(subject, int):
        return "one", subject
    return "active", None


def wants_over(text: str) -> bool:
    if "on top" in text:
        return True
    if re.search(
        r"\b(?:over|above|atop)\b(?:\s+\w+){0,3}\s+"
        r"(?:the\s+|an?\s+)?(?:object|cube|box|chair|target|it)\b",
        text,
    ):
        return True
    return bool(
        re.search(r"\b(?:over|above|atop)\b", text)
        and re.search(r"\b(?:stay|hover|hold|remain|wait|go|fly|move|station|inspect)\b", text)
    )


def find_station_side(text: str) -> str:
    match = (
        _OF_OBJECT.search(text)
        or _FROM_SIDE.search(text)
        or _THE_SIDE.search(text)
        or _ITS_SIDE.search(text)
        or _BESIDE.search(text)
        or _OBJECT_SIDE.search(text)
    )
    if not match:
        return ""
    return _SIDE_WORDS[match.group(1)]


_OBJECT = r"(?:the\s+|an?\s+)?(?:object|cube|box|chair|target|it)\b"
_STOP = re.compile(r"\b(stop|halt|freeze|abort|cancel|emergency|brake)\b")
_SWAP = re.compile(r"\b(swap|switch|exchange|trade)\b")
_ORBIT = re.compile(
    r"\b(circle|circles|circling|orbit|orbits|orbiting|360)\b"
    r"|\b(?:go|fly|move|travel|rotate|turn|spin|pan|head|loop|hover)\s+(?:all\s+)?(?:the\s+way\s+)?around\s+"
    + _OBJECT
    + r"|\ball\s+around\s+"
    + _OBJECT
)
_HOME = re.compile(
    r"\bhome\b"
    r"|\b(?:return|go\s+back|come\s+back|head\s+back|fly\s+back|back)\s+to\s+(?:the\s+)?(?:start|base|launch|beginning)\b"
    r"|\bstart(?:ing)?\s+(?:position|point|spot|line)s?\b"
    r"|\breturn\b|\brecall\b"
)
_CLOSER = re.compile(r"\b(closer|nearer|move in|zoom in|get close|come close|approach)\b")
_FARTHER = re.compile(
    r"\b(farther|further|back off|back away|move away|step away|pull back|zoom out|away from|more space|some space)\b"
)
_DRONE_NUMBER = re.compile(r"\b(\d+|one|two|three|first|second|third)\b")


_ADDRESS_WORDS = {"you", "please", "hey", "ok", "okay", "now", "listen", "the"}


def _only_names_a_drone(text: str) -> bool:
    rest = re.sub(r"[^\w\s]", " ", _ORDINAL.sub(" ", _DRONE.sub(" ", text))).split()
    return not [word for word in rest if word not in _ADDRESS_WORDS]


def wants_stop(text: str) -> bool:
    return bool(_STOP.search(text))


def wants_swap(text: str) -> bool:
    return bool(_SWAP.search(text))


def wants_orbit(text: str) -> bool:
    return bool(_ORBIT.search(text))


def wants_home(text: str) -> bool:
    return bool(_HOME.search(text))


def wants_radial(text: str) -> str:
    """Return "in" for closer, "out" for farther, or ""."""
    if _CLOSER.search(text):
        return "in"
    match = _FARTHER.search(text)
    if not match:
        return ""
    # "move further left" is a nudge to the left, not away from the cube.
    if match.group(1) in {"farther", "further"} and set(find_directions(text)) - {"back"}:
        return ""
    return "out"


def drone_numbers(text: str) -> list[int]:
    numbers = []
    for token in _DRONE_NUMBER.findall(text):
        number = int(token) if token.isdigit() else _NUMBER_WORDS[token]
        if number not in numbers:
            numbers.append(number)
    return numbers


_TURN_COUNT = {"once": 1.0, "twice": 2.0, "thrice": 3.0}
_TURN_WORDS = {"one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0, "a": 1.0}


def find_turns(text: str) -> float | None:
    # "drone 3 circle the object" names a drone, not three circles.
    text = _ORDINAL.sub(" ", _DRONE.sub(" ", text))
    for word, value in _TURN_COUNT.items():
        if re.search(rf"\b{word}\b", text):
            return value
    if re.search(r"\bhalf\s+(?:a\s+)?(?:lap|circle|turn|orbit|way|loop)\b", text):
        return 0.5
    match = re.search(
        r"\b(\d+(?:\.\d+)?|one|two|three|four|five|a)\s+(?:full\s+)?"
        r"(?:times|laps?|turns?|circles?|rounds?|loops?|orbits?|revolutions?)\b",
        text,
    )
    if match:
        token = match.group(1)
        return float(token) if token[0].isdigit() else _TURN_WORDS[token]
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)\b", text)
    if match:
        return float(match.group(1)) / 360.0
    return None


def find_radius(text: str) -> float | None:
    if not re.search(r"\d\s*(?:cm|mm|m\b|met|cent|milli)", text):
        return None
    return find_distance(text)


def _clamp_turns(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(min(5.0, max(0.1, value)))


def _clamp_radius(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(min(1.9, max(0.55, value)))


def wants_inspect(text: str) -> bool:
    if re.search(r"\b(inspect|inspection|examine|survey|scan)\b", text):
        return True
    return bool(re.search(r"\bcheck\b", text) and re.search(r"\b(object|cube|box|chair|target|it)\b", text))


def wants_face(text: str) -> bool:
    return bool(
        re.search(r"\b(face|look|point|aim)\b", text)
        and re.search(r"\b(object|cube|box|chair|target|it)\b", text)
    )


def wants_yaw(text: str) -> bool:
    return bool(re.search(r"\b(rotate|rotation|turn|yaw|spin)\b", text))


def wants_hold(text: str) -> bool:
    return bool(re.search(r"\b(stop|freeze|hover|hold|stay|wait|remain)\b", text))


def find_directions(text: str) -> list[str]:
    found = []
    for word, direction in _DIRECTION_WORDS:
        if direction in found:
            continue
        if re.search(rf"\b{re.escape(word)}\b", text):
            found.append(direction)
    return found


def _is_nudge(text: str, directions: list[str]) -> bool:
    if not directions:
        return False
    if re.search(r"\b(move|go|fly|shift|slide|nudge|head|send|come|step|translate)\b", text):
        return True
    if re.search(r"\b(little|bit|slightly|tad|touch)\b", text):
        return True
    return len(text.split()) <= 6


def find_distance(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(cm|centimeters?|centimetres?|mm|millimeters?|millimetres?|m|meters?|metres?)\b",
        text,
    )
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.startswith("cm") or unit.startswith("cent"):
            return value * 0.01
        if unit.startswith("mm") or unit.startswith("milli"):
            return value * 0.001
        return value
    match = re.search(r"\b(?:by|move|go|fly|shift|nudge)\s+(\d+(?:\.\d+)?)\b", text)
    if match:
        return float(match.group(1))
    return None


def find_yaw_magnitude(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)\b", text)
    if match:
        return float(match.group(1))
    return None


def yaw_sign(text: str) -> float:
    if re.search(r"counter|ccw|anti", text):
        return 1.0
    if re.search(r"\bclockwise\b|\bcw\b", text):
        return -1.0
    if re.search(r"\bright\b", text):
        return -1.0
    return 1.0


def _clamp_distance(value: float | None) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        return STEP_M
    return float(min(2.0, max(0.02, value)))


def _clamp_yaw(value: float | None) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        return YAW_STEP_DEG
    return float(min(180.0, max(1.0, abs(value))))


def _unknown(phrase: str) -> str:
    return (
        f"No command for “{phrase}”. "
        "Name a drone and a side of the object, or nudge the selected drone. "
        "Try: Drone 1 go to the left of the object · "
        "Move a little to the right · Rotate a little."
    )


def parse_clause(text: str):
    subject, error = _subject(text)
    if error:
        return error
    scope, drone = _scope(subject)

    if isinstance(subject, int) and _only_names_a_drone(text):
        return Action(kind="select", scope="one", drone=subject, phrase=text)

    if wants_stop(text):
        # A stop that names nobody stops everyone.
        return Action(kind="stop", scope="all" if subject is None else scope, drone=drone, phrase=text)

    if wants_swap(text):
        numbers = drone_numbers(text)
        wrong = [number for number in numbers if number not in (1, 2, 3)]
        if wrong:
            return f"This scene has drones 1, 2, and 3 (heard drone {wrong[0]})."
        if len(numbers) >= 2:
            return Action(kind="swap", scope="one", drone=numbers[0], partner=numbers[1], phrase=text)
        if len(numbers) == 1:
            return Action(kind="swap", scope="active", partner=numbers[0], phrase=text)
        return "Name the two drones to swap, for example: swap drone 1 and drone 3."

    if wants_orbit(text):
        return Action(
            kind="orbit",
            scope=scope,
            drone=drone,
            turns=_clamp_turns(find_turns(text)),
            radius=_clamp_radius(find_radius(text)),
            sign=yaw_sign(text),
            phrase=text,
        )

    if wants_over(text):
        return Action(kind="station", scope=scope, drone=drone, side="over", phrase=text)

    side = find_station_side(text)
    if side:
        return Action(kind="station", scope=scope, drone=drone, side=side, phrase=text)

    if wants_home(text):
        return Action(kind="home", scope="all" if subject is None else scope, drone=drone, phrase=text)

    radial = wants_radial(text)
    if radial:
        return Action(
            kind="radial",
            scope=scope,
            drone=drone,
            directions=(radial,),
            distance=_clamp_distance(find_distance(text)),
            phrase=text,
        )

    if wants_inspect(text):
        if subject is None:
            scope = "all"
        return Action(kind="inspect", scope=scope, drone=drone, phrase=text)

    if wants_face(text):
        return Action(kind="face", scope=scope, drone=drone, phrase=text)

    if wants_yaw(text):
        signed = _clamp_yaw(find_yaw_magnitude(text)) * yaw_sign(text)
        return Action(kind="yaw", scope=scope, drone=drone, yaw_deg=signed, phrase=text)

    directions = find_directions(text)
    if _is_nudge(text, directions):
        return Action(
            kind="nudge",
            scope=scope,
            drone=drone,
            directions=tuple(directions),
            distance=_clamp_distance(find_distance(text)),
            phrase=text,
        )

    if wants_hold(text):
        return Action(kind="hold", scope=scope, drone=drone, phrase=text)
    return None


def parse_commands(text: str):
    """Return (actions, error). A non-empty error means nothing should move."""
    clauses = split_clauses(text)
    if not clauses:
        return [], "Type a command for drone 1, 2, or 3."
    actions = []
    for clause in clauses:
        if clause in AGAIN:
            return [], "Say again by itself to repeat the last command."
        parsed = parse_clause(clause)
        if isinstance(parsed, str):
            return [], parsed
        if parsed is None:
            return [], _unknown(clause)
        actions.append(parsed)
    return actions, ""
