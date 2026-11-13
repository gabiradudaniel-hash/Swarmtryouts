# Swarm as first responder, read by a local model

Three drones, one cube in the middle of the room, and a sentence for each of them. A language model running on this Mac reads the sentence. Nothing is sent to a server. The window is the same one as the swarm language: top view on the left, room on the right, command box along the bottom.

```text
Drone 1 go to the left of the object
Drone 2 stay over the object
Drone 3, you go from the right
Move a little to the right
Rotate a little
```

![Demo after the three stations, a small move, and a small rotate](docs/demo.png)

![All drones circle the object, 120° apart](docs/circle.png)

## How a sentence becomes motion

1. `planner.py` sends the sentence to the local model with a short prompt and a few examples. By default that is Qwen2.5-3B-Instruct in 4-bit on MLX, about 1.7 GB. It answers with one JSON action per instruction, for example `{"drone":3,"do":"nudge","move":["right"]}`. The model never writes coordinates.
2. The reply is checked against the words the pilot actually said. A drone named in a clause is the drone that moves. A clause that names no drone goes to the drone you spoke to last. A distance or angle the pilot did not say becomes the small step. A clear side of the object ("behind the cube") parks the drone there, even if the model called it a nudge. The direction of a single rotate comes from its clause.
3. `scene.py` turns the actions into poses, keeps the drones out of the cube and apart from each other, and animates the move.

If no model can be loaded, or the reply is not a valid action, the phrase parser in `commands.py` reads the sentence instead. The caption and the terminal show which one answered, the raw model reply, and how long it took (about 1 s per sentence on this Mac).

## Commands

The room frame matches `nl_swarm`: +x is forward, +y is left, +z is up. Yaw 0 faces +x. Positive yaw turns toward +y.

A small move is **20 cm** along that axis of the room. A small rotate is **20°** of yaw. "Rotate a little" turns counterclockwise. "Rotate a little to the right" and "rotate clockwise" turn the other way. A distance or angle you say out loud replaces the small step (`move 50 cm to the left`, `rotate 45 degrees`).

The cube sits on the floor at the origin. Sending a drone to the left, right, front, or back parks it 0.95 m out from the cube at 0.70 m, nose pointed at the cube. "Over the object" parks it at 1.05 m, above the cube. "Inspect the object" with no drone number sends drone 1 to the left, drone 2 overhead, and drone 3 to the right. "Drone 2 inspect the object" sends that drone to the nearest open side and turns it toward the cube.

A command that does not name a drone uses the last drone you named. Until you name one, a small move or a small rotate applies to all three. A new sentence starts from wherever the drones are at that moment. "again" repeats the last command from there.

### Filming the object

```text
Circle the object
Drone 3 circle the object twice clockwise
All drones circle the object
Closer / back off a little
Higher / lower
Swap drone 1 and drone 3
Return to start
Stop the movement
```

- **Circle.** The drone flies to the nearest point of a circle around the cube, then flies once around it at 0.35 m/s, nose on the cube the whole time. The circle keeps the drone's current distance (0.6–1.8 m, otherwise 0.95 m) and height (at least 0.60 m). If another drone is parked on that circle, it widens to 1.30, 1.60, or 1.90 m, then tries 0.35 m higher. "twice", "3 laps", "half a lap", "clockwise", and "at 1.5 m" change it.
- **All drones circle.** The three drones take spots 120° apart on one circle, wait for each other, and start together. Each flies 120°, so together they film all the way around in a third of the time of one lap.
- **Closer / farther.** 20 cm toward or away from the cube along the line to it, then nose on the cube. A drone stops 0.50 m from the cube. Straight above the cube, closer means lower.
- **Higher / lower.** 20 cm up or down. Any small move keeps a drone that faced the cube facing it.
- **Swap.** Both drones fly at once to each other's spot and heading. The second bows 0.40 m to the side, so they never meet.
- **Return to start.** Back to the start line, nose forward. With no drone named, all three go.
- **Stop.** Every drone, or only the one you name, freezes where it is. A stop skips the model, so it takes effect on the next frame.

Each drone flies its own path, so a drone that is circling keeps circling while you move another. The dashed line in both views is where each drone is still going.

This folder only simulates. It does not arm the Crazyflies.

## Run

```shell
cd nl_swarm/responder_local
./run.sh
./run.sh --demo
./run.sh --say "Drone 1 inspect the object"
./run.sh --backend rules
./run.sh --demo --save after.png
```

`./run.sh` opens with the drones hovering at their start positions and waits for your first command. `--demo` sends the three-station example right away.

In the command box, Backspace and Delete remove one character. Option+Backspace (or Ctrl+W) removes the previous word, Option+Delete the next word, and Cmd+Backspace (or Ctrl+U, or Esc) clears the line.

`./run.sh` makes a virtualenv on first launch if `.venv` is missing, and installs numpy, matplotlib, mlx-lm, mlx-whisper, and sounddevice. The first launch downloads the models from Hugging Face. After that it runs offline. The window needs a Python with Tk. On this Mac that is the miniconda Python 3.14.

`--backend auto` tries MLX, then Ollama (if `ollama serve` is running with `qwen2.5:3b`), then the phrase parser. `--model mlx-community/Qwen2.5-1.5B-Instruct-4bit` is about twice as fast but sends unnamed commands to all three drones more often.

## Voice

The window listens on the microphone, so you can speak instead of typing. Say a command, for example "Drone 2, circle the object", then pause. The mic line at the bottom of the window shows what it heard. The command goes through the same model and checks as typed text. Speech to text is Whisper running locally through mlx-whisper (`whisper-small.en`, about 0.3 s per sentence), so audio never leaves the Mac.

- **Listening:** a command starts when the sound gets louder than the room and ends after 0.7 s of quiet. The threshold adapts to background noise.
- **Pausing:** the **Mic on** button turns listening off and on, which helps while you talk to someone. "Stop" works by voice too and takes effect at once.
- **Choosing a drone:** saying just "Drone 2" selects it. "Closer" or "Rotate a little" then moves that drone.
- **Permission:** on the first launch macOS asks for microphone access for Cursor or Terminal. If the mic line says "no sound", enable the app under System Settings → Privacy & Security → Microphone and restart it.

```shell
./run.sh --no-voice
./run.sh --stt-model mlx-community/whisper-large-v3-turbo
./run.sh --mic 2
```

`--no-voice` disables the microphone. The turbo model copes better with accents and noise but downloads about 1.6 GB and takes about 1 s per sentence. `--mic` picks an input device. `.venv/bin/python -m sounddevice` lists the devices.

## Check

```shell
.venv/bin/python test_local.py
.venv/bin/python test_local.py --live
.venv/bin/python test_voice.py --live
.venv/bin/python eval_model.py
```

`test_voice.py` checks the transcript cleanup (for example "drone to" becomes "drone 2") and the cutting of utterances on synthetic audio. `--live` speaks commands with macOS `say`, runs them through Whisper, and checks where the drones end up.

`test_local.py` checks the reply validation, the parser, and the end poses using canned model replies, so it runs without loading a model. It writes `/tmp/responder_local_before.png` and `/tmp/responder_local_after.png`. `--live` also flies the story through the real model. `eval_model.py` scores the model on 43 pilot sentences, alone and after the check against the sentence. The 3B model gets 38 alone and 43 after the check, at about 1.4 s per sentence.
