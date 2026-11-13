"""Hands-free voice commands: microphone, local Whisper, then the same path as typing.

The microphone is always listening. Speech louder than the room starts an
utterance, and 0.7 s of quiet ends it. Whisper turns the utterance into text
on this Mac with MLX, so the audio never leaves the machine. The text goes to
the scene exactly as if it had been typed into the command box.
"""

from __future__ import annotations

import queue
import re
import threading
import time

import numpy as np

from planner import MLX_LOCK

RATE = 16000
BLOCK = 1600  # 100 ms of audio
DEFAULT_STT_MODEL = "base.en"  # SWARM: faster-whisper size; the Mac version used mlx-community/whisper-small.en-mlx
# Whisper spells words the way this prompt does, so "drone two" comes out as "Drone 2".
PROMPT = (
    "Drone 1, drone 2, drone 3. Go to the left of the chair. Go behind the chair. Move forward. Move left. Land. "
    "Circle the object. All drones circle the object. Closer. Back off a little. "
    "Higher. Lower. Rotate a little. Swap drone 1 and drone 3. Return to start. Stop the movement."
)

# What Whisper writes for silence, breathing, or a cough.
_JUNK = {
    "you",
    "thank you",
    "thanks",
    "thanks for watching",
    "thank you for watching",
    "bye",
    "okay",
    "ok",
    "so",
    "uh",
    "um",
    "hmm",
    "oh",
    "yeah",
}
_HEARD_AS = (
    (re.compile(r"\b(?:throne|drawn|groan|drones?)\s+(?=(?:\d|one|two|three|to|too|won|tree|free)\b)", re.I), "drone "),
    (re.compile(r"\bdrone\s+(?:to|too)\b", re.I), "drone 2"),
    (re.compile(r"\bdrone\s+won\b", re.I), "drone 1"),
    (re.compile(r"\bdrone\s+(?:tree|free)\b", re.I), "drone 3"),
    (re.compile(r"\b(?:i'll|all the)\s+drones?\b", re.I), "all drones"),
    (re.compile(r"\ball drone\b", re.I), "all drones"),
)
# "Drone 2. Circle the object." is one command, not a name and then an order.
_NAME_THEN_ORDER = re.compile(r"\b((?:drone|drones)\s+(?:\d|one|two|three))\s*[.!?]\s+(?=[a-z])", re.I)


def clean_transcript(text: str) -> str:
    """Return the command Whisper heard, or "" for noise."""
    text = " ".join((text or "").split())
    bare = re.sub(r"[^a-z' ]", "", text.lower()).strip()
    if not bare or bare in _JUNK or len(bare.split()) > 30:
        return ""
    for pattern, replacement in _HEARD_AS:
        text = pattern.sub(replacement, text)
    text = _NAME_THEN_ORDER.sub(r"\1, ", text)
    return text.strip()


class Segmenter:
    """Cut a stream of 100 ms blocks into utterances by loudness."""

    def __init__(self, start_blocks=2, end_blocks=7, preroll_blocks=3, min_blocks=3, max_blocks=100, min_level=0.008):
        self.start_blocks = start_blocks
        self.end_blocks = end_blocks
        self.preroll_blocks = preroll_blocks
        self.min_blocks = min_blocks
        self.max_blocks = max_blocks
        self.min_level = min_level
        self.noise = None
        self.reset()

    def reset(self):
        self.speaking = False
        self.recent = []
        self.utterance = []
        self.loud_run = 0
        self.quiet_run = 0
        self.loud_total = 0
        self.level = 0.0

    def threshold(self) -> float:
        return max(self.min_level, 3.0 * (self.noise or 0.0))

    def feed(self, block):
        """Return a finished utterance as float32 samples, or None."""
        block = np.asarray(block, dtype=np.float32)
        self.level = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0
        loud = self.level > self.threshold()
        if not self.speaking:
            if not loud:
                self.noise = self.level if self.noise is None else 0.95 * self.noise + 0.05 * self.level
            self.recent.append(block)
            del self.recent[: -(self.preroll_blocks + self.start_blocks)]
            self.loud_run = self.loud_run + 1 if loud else 0
            if self.loud_run >= self.start_blocks:
                self.speaking = True
                self.utterance = list(self.recent)
                self.loud_total = self.loud_run
                self.quiet_run = 0
            return None
        self.utterance.append(block)
        if loud:
            self.loud_total += 1
            self.quiet_run = 0
        else:
            self.quiet_run += 1
        if self.quiet_run < self.end_blocks and len(self.utterance) < self.max_blocks:
            return None
        blocks = self.utterance[: len(self.utterance) - max(0, self.quiet_run - 2)]
        enough = self.loud_total >= self.min_blocks
        self.reset()
        return np.concatenate(blocks) if enough and blocks else None


_FW_MODELS = {}


def _faster_whisper(samples, model) -> str:
    """SWARM: Whisper through faster-whisper (Windows/Linux). model is a size name
    (base.en, small.en) or a folder; voice/models/<name> is used when it exists."""
    from pathlib import Path

    from faster_whisper import WhisperModel

    if model not in _FW_MODELS:
        local = Path(__file__).resolve().parent / "models" / model
        _FW_MODELS[model] = WhisperModel(str(local) if local.exists() else model, device="cpu", compute_type="int8")
    segments, _info = _FW_MODELS[model].transcribe(
        np.asarray(samples, dtype=np.float32),
        language="en",
        initial_prompt=PROMPT,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        beam_size=1,
    )
    return " ".join(segment.text for segment in segments)


def transcribe(samples, model=DEFAULT_STT_MODEL) -> str:
    if not str(model).startswith("mlx-community/"):
        return _faster_whisper(samples, model)
    import mlx_whisper

    with MLX_LOCK:
        result = mlx_whisper.transcribe(
            np.asarray(samples, dtype=np.float32),
            path_or_hf_repo=model,
            language="en",
            initial_prompt=PROMPT,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
    return result.get("text", "")


class VoiceListener:
    """Listens on the microphone in the background and hands each command to on_text."""

    def __init__(self, on_text, model=DEFAULT_STT_MODEL, device=None):
        self.on_text = on_text
        self.model = model
        self.device = device
        self.enabled = True
        self.state = "Loading speech model..."
        self.heard = ""
        self.blocks = queue.Queue()
        self.segmenter = Segmenter()
        threading.Thread(target=self._run, name="responder-voice", daemon=True).start()

    def toggle(self):
        self.enabled = not self.enabled
        self.segmenter.reset()
        print(f"\n  mic: {'on' if self.enabled else 'off'}", flush=True)

    def status(self) -> str:
        if not self.enabled:
            return "Mic off. Click Mic to listen."
        heard = f'   Heard: "{self.heard}"' if self.heard else ""
        return f"Mic on: {self.state}{heard}"

    def _callback(self, indata, _frames, _time, _status):
        if self.enabled:
            self.blocks.put(indata[:, 0].copy())

    def _run(self):
        try:
            import sounddevice as sd

            transcribe(np.zeros(RATE // 2, dtype=np.float32), self.model)
            stream = sd.InputStream(
                samplerate=RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCK,
                device=self.device,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            self.enabled = False
            self.state = f"unavailable ({exc})"
            print(f"\n  mic: unavailable ({exc})", flush=True)
            return
        self.stream = stream
        self.state = "listening. Say a command."
        print("\n  mic: listening (say a command, e.g. 'Drone 2, circle the object')", flush=True)
        silent_since = time.perf_counter()
        while True:
            block = self.blocks.get()
            if np.any(block):
                silent_since = None
            elif silent_since is not None and time.perf_counter() - silent_since > 3.0:
                self.state = (
                    "no sound. Check the microphone (Windows: Settings, Privacy, Microphone) or pick one with --mic."
                )
            utterance = self.segmenter.feed(block)
            if utterance is None:
                if silent_since is None:
                    self.state = "hearing you..." if self.segmenter.speaking else "listening. Say a command."
                continue
            self.state = "transcribing..."
            started = time.perf_counter()
            try:
                raw = transcribe(utterance, self.model)
            except Exception as exc:
                self.state = f"speech model error ({exc})"
                continue
            text = clean_transcript(raw)
            spent = time.perf_counter() - started
            self.state = "listening. Say a command."
            if not text:
                if raw.strip():
                    print(f"\n  heard (ignored): {raw.strip()}", flush=True)
                continue
            self.heard = text
            print(f"\n  heard: {text}  ({len(utterance) / RATE:.1f} s of speech, {spent:.2f} s to transcribe)", flush=True)
            self.on_text(text)
