"""Ask a local model which drone does what.

The model never writes coordinates. It reads the sentence and returns one
small JSON action per clause: which drone, what kind of motion, which side
of the object, which way to nudge, which way to turn. The scene turns those
actions into poses. MLX runs the weights on this Mac. Ollama is used when it
is already serving the model. The phrase parser in commands.py takes over
when neither is available, or when the model reply is not a valid action.

Numbers come from the sentence, not from the model. A distance or angle the
pilot did not say becomes the small step: 20 cm, or 20° of yaw.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace as _replace

from commands import (
    STEP_M,
    YAW_STEP_DEG,
    Action,
    find_directions,
    find_distance,
    find_station_side,
    find_yaw_magnitude,
    parse_clause,
    parse_commands,
    split_clauses,
    wants_face,
    wants_home,
    wants_orbit,
    wants_over,
    wants_radial,
    wants_yaw,
    yaw_sign,
    _clamp_distance,
    _clamp_radius,
    _clamp_turns,
    _clamp_yaw,
    _subject,
)

# Whisper and the planner model share the GPU; MLX work from two threads at once can crash.
MLX_LOCK = threading.Lock()

DEFAULT_MLX_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"  # SWARM: the model already in Ollama on the lab laptop

KINDS = ("station", "inspect", "nudge", "yaw", "face", "hold", "orbit", "closer", "farther", "home", "swap", "stop")
# A clause the phrase parser reads as one of these wins over a different model guess.
SURE_KINDS = ("orbit", "radial", "home", "swap", "stop", "select")
SIDES = ("left", "right", "front", "back", "over")
DIRECTIONS = ("forward", "back", "left", "right", "up", "down")
_MOVE_WORDS = {
    "front": "forward",
    "forwards": "forward",
    "ahead": "forward",
    "backward": "back",
    "backwards": "back",
    "behind": "back",
    "above": "up",
    "upward": "up",
    "below": "down",
    "downward": "down",
}

_KIND_WORDS = {
    "turn": "yaw",
    "rotate": "yaw",
    "spin": "yaw",
    "move": "nudge",
    "circle": "orbit",
    "approach": "closer",
    "return": "home",
    "halt": "stop",
    "hover": "hold",
    "look": "face",
}

SYSTEM = """You command three inspection drones around one object (a chair, also called the object, cube or box) in the middle of a room.
Turn the pilot sentence into JSON only, no markdown: {"actions":[...]} with one action per instruction, in order.
Each action has these keys:
drone: 1, 2, 3, "all", or null. null means the drone the pilot spoke to last.
do: station, inspect, nudge, yaw, face, hold, orbit, closer, farther, home, swap, or stop.
  station parks the drone at one side of the object, facing it.
  inspect picks a free side by itself. nudge is a small translation. yaw turns in place.
  face points the drone at the object. hold hovers. stop halts at once.
  orbit circles around the object while facing it. closer and farther move toward or away from the object.
  home returns to the starting position. swap exchanges places with the drone in "with".
orbit keys: turn (ccw or cw), turns (laps, only if said), radius_m (only if said).
with: the other drone number, only for swap.
side: left, right, front, back, or over. Only for station. "from the right" means side right. "above" or "on top" means over.
A place next to the object (left of it, behind it, in front of it, above it) is always station, never nudge.
move: list of forward, back, left, right, up, down. Only for nudge. nudge never mentions the object.
Use face only when the pilot asks the drone to face or look at the object.
turn: ccw or cw. Only for yaw. "to the right" or "clockwise" is cw. Otherwise ccw.
distance_m: meters, only when the pilot says a distance. yaw_deg: degrees, only when the pilot says an angle.
Leave out keys an action does not use."""

EXAMPLES = (
    ("Drone 1 go to the left of the object", [{"drone": 1, "do": "station", "side": "left"}]),
    ("Drone 2 stay over the object", [{"drone": 2, "do": "station", "side": "over"}]),
    ("Drone 3, you go from the right", [{"drone": 3, "do": "station", "side": "right"}]),
    ("Move a little to the right", [{"drone": None, "do": "nudge", "move": ["right"]}]),
    ("Rotate a little", [{"drone": None, "do": "yaw", "turn": "ccw"}]),
    ("drone two turn slightly to the right", [{"drone": 2, "do": "yaw", "turn": "cw"}]),
    ("inspect the object", [{"drone": "all", "do": "inspect"}]),
    ("drone 2 get in front of the box", [{"drone": 2, "do": "station", "side": "front"}]),
    ("drone 1 hover above it", [{"drone": 1, "do": "station", "side": "over"}]),
    ("third drone climb 30 cm", [{"drone": 3, "do": "nudge", "move": ["up"], "distance_m": 0.3}]),
    (
        "move back a bit and turn left a little",
        [{"drone": None, "do": "nudge", "move": ["back"]}, {"drone": None, "do": "yaw", "turn": "ccw"}],
    ),
    ("drone 3 look at the cube", [{"drone": 3, "do": "face"}]),
    (
        "Drone 1 check it out from behind, drone 2 hover on top of the box",
        [
            {"drone": 1, "do": "station", "side": "back"},
            {"drone": 2, "do": "station", "side": "over"},
        ],
    ),
    ("everyone hold position", [{"drone": "all", "do": "hold"}]),
    ("Drone 2 circle the object", [{"drone": 2, "do": "orbit", "turn": "ccw"}]),
    ("all drones circle the object", [{"drone": "all", "do": "orbit", "turn": "ccw"}]),
    ("fly around it twice clockwise", [{"drone": None, "do": "orbit", "turn": "cw", "turns": 2}]),
    ("get a little closer", [{"drone": None, "do": "closer"}]),
    ("drone 3 back off a bit", [{"drone": 3, "do": "farther"}]),
    ("go a bit higher", [{"drone": None, "do": "nudge", "move": ["up"]}]),
    ("everyone return to start", [{"drone": "all", "do": "home"}]),
    ("swap drone 1 and drone 3", [{"drone": 1, "do": "swap", "with": 3}]),
    ("stop the movement", [{"drone": "all", "do": "stop"}]),
)


def messages_for(text: str, active: int | None):
    messages = [{"role": "system", "content": SYSTEM}]
    for sentence, actions in EXAMPLES:
        messages.append({"role": "user", "content": f"Pilot: {sentence}"})
        messages.append({
            "role": "assistant",
            "content": json.dumps({"actions": actions}, separators=(",", ":")),
        })
    spoken_to = f"(last spoken to: drone {active}) " if active else ""
    messages.append({"role": "user", "content": f"{spoken_to}Pilot: {text}"})
    return messages


def _merge_pairs(pairs):
    # Small models sometimes write {"actions":[a],"actions":[b]}.
    merged = {}
    for name, value in pairs:
        if name in merged and isinstance(merged[name], list) and isinstance(value, list):
            merged[name] = merged[name] + value
        else:
            merged[name] = value
    return merged


def extract_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start = text.find("{")
    if start < 0:
        raise ValueError("no json object in the model reply")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                blob = text[start : index + 1]
                try:
                    return json.loads(blob, object_pairs_hook=_merge_pairs)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", blob), object_pairs_hook=_merge_pairs)
    raise ValueError("no json object in the model reply")


def _spoken_numbers(text: str) -> list[float]:
    return [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]


def _was_spoken(value, spoken, scales) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return any(abs(float(value) - number * scale) < 1e-6 for number in spoken for scale in scales)


def _drone_field(value):
    if value is None or value == "" or value == "null":
        return "active", None
    if isinstance(value, str):
        word = value.strip().lower()
        if word in {"all", "everyone", "every", "swarm"}:
            return "all", None
        if word.isdigit():
            value = int(word)
        else:
            raise ValueError(f"unknown drone {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"unknown drone {value!r}")
    number = int(value)
    if number not in (1, 2, 3):
        raise ValueError(f"This scene has drones 1, 2, and 3 (heard drone {number}).")
    return "one", number


def _clean_word(value) -> str:
    return str(value or "").strip().lower()


def actions_from_json(payload, text: str) -> list[Action]:
    """Validate the model reply and fill in the 20 cm / 20° steps."""
    items = payload.get("actions") if isinstance(payload, dict) else payload
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        raise ValueError("no actions in the model reply")
    spoken = _spoken_numbers(text)
    phrase = " ".join(text.split()).lower()
    actions = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("an action is not an object")
        kind = _clean_word(item.get("do") or item.get("kind"))
        kind = _KIND_WORDS.get(kind, kind)
        if kind == "nudge":
            moves = item.get("move") or item.get("direction") or []
            moves = [_clean_word(m) for m in ([moves] if isinstance(moves, str) else moves)]
            if any(m in {"closer", "nearer", "in", "toward", "towards"} for m in moves):
                kind = "closer"
            elif any(m in {"farther", "further", "away", "out"} for m in moves):
                kind = "farther"
        if kind not in KINDS:
            raise ValueError(f"unknown action {kind!r}")
        scope, drone = _drone_field(item.get("drone"))
        if kind == "station":
            side = _clean_word(item.get("side"))
            side = {"above": "over", "top": "over", "behind": "back", "forward": "front"}.get(side, side)
            if side not in SIDES:
                raise ValueError(f"unknown side {side!r}")
            actions.append(Action(kind="station", scope=scope, drone=drone, side=side, phrase=phrase))
        elif kind == "nudge":
            moves = item.get("move") or item.get("direction") or []
            if isinstance(moves, str):
                moves = [moves]
            moves = [_MOVE_WORDS.get(_clean_word(m), _clean_word(m)) for m in moves]
            moves = [m for m in DIRECTIONS if m in moves]
            if not moves:
                raise ValueError("nudge without a direction")
            given = item.get("distance_m")
            distance = given if _was_spoken(given, spoken, (1.0, 0.01, 0.001)) else STEP_M
            actions.append(
                Action(
                    kind="nudge",
                    scope=scope,
                    drone=drone,
                    directions=tuple(moves),
                    distance=_clamp_distance(distance),
                    phrase=phrase,
                )
            )
        elif kind == "yaw":
            given = item.get("yaw_deg")
            given = abs(given) if isinstance(given, (int, float)) and not isinstance(given, bool) else None
            magnitude = given if _was_spoken(given, spoken, (1.0,)) else YAW_STEP_DEG
            sign = -1.0 if _clean_word(item.get("turn")) == "cw" else 1.0
            actions.append(
                Action(kind="yaw", scope=scope, drone=drone, yaw_deg=_clamp_yaw(magnitude) * sign, phrase=phrase)
            )
        elif kind == "orbit":
            turns = item.get("turns")
            radius = item.get("radius_m")
            actions.append(
                Action(
                    kind="orbit",
                    scope=scope,
                    drone=drone,
                    turns=_clamp_turns(turns) if _was_spoken(turns, spoken, (1.0,)) else None,
                    radius=_clamp_radius(radius) if _was_spoken(radius, spoken, (1.0, 0.01, 0.001)) else None,
                    sign=-1.0 if _clean_word(item.get("turn")) == "cw" else 1.0,
                    phrase=phrase,
                )
            )
        elif kind in {"closer", "farther"}:
            given = item.get("distance_m")
            distance = given if _was_spoken(given, spoken, (1.0, 0.01, 0.001)) else STEP_M
            actions.append(
                Action(
                    kind="radial",
                    scope=scope,
                    drone=drone,
                    directions=("in",) if kind == "closer" else ("out",),
                    distance=_clamp_distance(distance),
                    phrase=phrase,
                )
            )
        elif kind == "swap":
            partner_scope, partner = _drone_field(item.get("with"))
            if partner_scope != "one":
                raise ValueError("swap without a second drone")
            actions.append(Action(kind="swap", scope=scope, drone=drone, partner=partner, phrase=phrase))
        elif kind in {"home", "stop"}:
            # Naming nobody brings everyone home, or stops everyone.
            actions.append(Action(kind=kind, scope="all" if scope == "active" else scope, drone=drone, phrase=phrase))
        else:
            actions.append(Action(kind=kind, scope=scope, drone=drone, phrase=phrase))
    return actions


def reconcile(actions: list[Action], text: str) -> list[Action]:
    """Let the words the pilot actually said win over a small model's guess.

    A face the pilot never asked for is dropped. When the model returned one
    action per clause, a clause the phrase parser reads as a circle, closer,
    farther, home, swap, or stop takes the parser's action with the numbers
    the pilot said, and a clause that names a side of the object parks the
    drone there. Otherwise a drone named in that clause is the drone that moves,
    and a clause that names no drone goes to the selected one.
    A single nudge takes its directions and
    distance from its clause, and a single rotate takes its sign and angle
    from its clause.
    """
    clauses = split_clauses(text)
    fixed = list(actions)
    if len(fixed) > 1 and not any(wants_face(clause) for clause in clauses):
        fixed = [action for action in fixed if action.kind != "face"]
    if len(clauses) == len(fixed):
        for index, clause in enumerate(clauses):
            subject, _error = _subject(clause)
            sure = parse_clause(clause)
            if isinstance(sure, Action) and sure.kind in SURE_KINDS:
                fixed[index] = sure
                continue
            if isinstance(sure, Action) and sure.kind == "station" and fixed[index].kind not in {"station", "inspect"}:
                fixed[index] = sure
            action = fixed[index]
            if isinstance(subject, int) and action.drone != subject:
                fixed[index] = _replace(action, scope="one", drone=subject)
            elif subject == "all" and action.scope != "all":
                fixed[index] = _replace(action, scope="all", drone=None)
            elif subject is None and action.scope != "active" and action.kind not in {"inspect", "home", "stop"}:
                fixed[index] = _replace(action, scope="active", drone=None)

    nudges = [i for i, action in enumerate(fixed) if action.kind == "nudge"]
    nudge_clauses = [
        c for c in clauses
        if find_directions(c)
        and not wants_yaw(c)
        and not find_station_side(c)
        and not wants_over(c)
        and not wants_radial(c)
        and not wants_home(c)
        and not wants_orbit(c)
    ]
    if len(nudges) == 1 and len(nudge_clauses) == 1:
        clause = nudge_clauses[0]
        directions = tuple(find_directions(clause))
        action = fixed[nudges[0]]
        fixed[nudges[0]] = _replace(
            action,
            directions=directions or action.directions,
            distance=_clamp_distance(find_distance(clause)),
        )

    yaws = [i for i, action in enumerate(fixed) if action.kind == "yaw"]
    yaw_clauses = [c for c in clauses if wants_yaw(c) and not wants_orbit(c)]
    if len(yaws) == 1 and len(yaw_clauses) == 1:
        clause = yaw_clauses[0]
        signed = _clamp_yaw(find_yaw_magnitude(clause)) * yaw_sign(clause)
        fixed[yaws[0]] = _replace(fixed[yaws[0]], yaw_deg=signed)
    return fixed


class Planner:
    """Loads the model on a background thread and turns sentences into actions.

    poll() returns (text, actions, error, source, raw). A non-empty error
    means nothing should move.
    """

    def __init__(self, backend="auto", model=None, ollama_model=None):
        self.backend = backend
        self.mlx_id = model or DEFAULT_MLX_MODEL
        self.ollama_model = ollama_model or DEFAULT_OLLAMA_MODEL
        self.kind = "rules"
        self.short_name = "the local model"
        self.status = "Loading the local model..."
        self._mlx = None
        self._tokenizer = None
        self._generate = None
        self._sampler = None
        self.requests = queue.Queue()
        self.results = queue.Queue()
        self.busy = 0
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        self.loaded = threading.Event()
        threading.Thread(target=self._loop, name="responder-planner", daemon=True).start()

    def ask(self, text: str, active: int | None):
        self.busy += 1
        self.requests.put((text, active))

    def poll(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def wait(self, timeout=120.0):
        """Block until one reply is ready. Used by --save and the tests."""
        try:
            return self.results.get(timeout=timeout)
        except queue.Empty:
            return None

    def _loop(self):
        self._load()
        print(self.status, flush=True)
        self.loaded.set()
        while True:
            text, active = self.requests.get()
            try:
                self.results.put(self.plan(text, active))
            except Exception as exc:
                self.results.put((text, [], f"Planner error: {exc}", self.short_name, ""))
            finally:
                self.busy = max(0, self.busy - 1)

    def plan(self, text: str, active: int | None = None):
        if self.kind == "rules":
            actions, error = parse_commands(text)
            return text, actions, error, "phrase parser", ""
        started = time.perf_counter()
        try:
            raw = self._complete(messages_for(text, active))
        except Exception as exc:  # SWARM: model unreachable or too slow -> phrase parser
            actions, error = parse_commands(text)
            return text, actions, error, f"phrase parser ({self.short_name} failed: {exc})", ""
        elapsed = time.perf_counter() - started
        raw_line = " ".join(raw.split())
        try:
            actions = reconcile(actions_from_json(extract_json(raw), text), text)
            return text, actions, "", f"{self.short_name}  {elapsed:.1f}s", raw_line
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            actions, error = parse_commands(text)
            if error:
                return text, [], error, f"{self.short_name}: {exc}", raw_line
            return text, actions, "", "phrase parser, model reply was not an action", raw_line

    def _complete(self, messages):
        if self.kind == "mlx":
            return self._mlx_complete(messages)
        if self.kind == "ollama":
            return self._ollama_complete(messages)
        raise RuntimeError("no model is loaded")

    def _load(self):
        self.status = ""
        if self.backend == "rules":
            self._use_rules("Phrase parser only (no model).")
            return
        if self.backend in {"auto", "mlx"} and self._try_mlx():
            return
        if self.backend == "mlx":
            self._use_rules(self.status)
            return
        if self.backend in {"auto", "ollama"} and self._try_ollama():
            return
        self._use_rules(self.status)

    def _use_rules(self, status):
        self.kind = "rules"
        self.short_name = "phrase parser"
        self.status = status + " Using the phrase parser."

    def _try_mlx(self):
        try:
            from mlx_lm import generate, load
        except ImportError:
            self.status = "MLX is not installed."
            return False
        try:
            with MLX_LOCK:
                model, tokenizer = load(self.mlx_id)
        except Exception as exc:
            self.status = f"Could not load {self.mlx_id} ({exc})."
            return False
        self._mlx, self._tokenizer, self._generate = model, tokenizer, generate
        try:
            from mlx_lm.sample_utils import make_sampler

            self._sampler = make_sampler(temp=0.0)
        except Exception:
            self._sampler = None
        self.kind = "mlx"
        self.short_name = "MLX " + self.mlx_id.split("/")[-1].replace("-Instruct-4bit", "")
        self.status = f"Ready ({self.short_name}, on this Mac)."
        self._mlx_complete(messages_for("Drone 1 hold", None))
        return True

    def _mlx_complete(self, messages):
        prompt = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        kwargs = {"max_tokens": 200, "verbose": False}
        if self._sampler is not None:
            kwargs["sampler"] = self._sampler
        with MLX_LOCK:
            try:
                text = self._generate(self._mlx, self._tokenizer, prompt=prompt, **kwargs)
            except TypeError:
                kwargs.pop("sampler", None)
                text = self._generate(self._mlx, self._tokenizer, prompt=prompt, **kwargs)
        return text if isinstance(text, str) else getattr(text, "text", str(text))

    def _try_ollama(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=0.5) as response:
                payload = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            self.status = f"{self.status} Ollama is not running.".strip()
            return False
        names = [item.get("name", "") for item in payload.get("models", [])]
        wanted = self.ollama_model
        if not any(name == wanted or name.split(":")[0] == wanted.split(":")[0] for name in names):
            self.status = f"{self.status} Ollama does not have {wanted} (ollama pull {wanted}).".strip()
            return False
        self.kind = "ollama"
        self.short_name = "Ollama " + wanted
        self.status = f"Ready ({self.short_name}, on this computer)."
        try:  # SWARM: load the model now so the first command is not slow
            self._ollama_complete(messages_for("Drone 1 hold", None), timeout=180)
        except Exception as exc:
            self.status = f"Ollama could not run {wanted} ({exc})."
            return False
        return True

    def _ollama_complete(self, messages, timeout=20):
        body = json.dumps({
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,       # SWARM: Qwen3 answers directly instead of reasoning first
            "keep_alive": "30m",
            "options": {"temperature": 0},
        }).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())["message"]["content"]
