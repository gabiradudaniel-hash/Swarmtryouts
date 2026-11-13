"""Top view and room view for the first-responder scene.

Same window as the swarm language: a command box, a top view, and the room
at elevation 28° and azimuth −58°. The cube at the origin is the object.
The caption shows the plan and which planner produced it.
"""

from __future__ import annotations

import math
import queue
import sys
import textwrap
import threading

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Circle, Rectangle
from matplotlib.widgets import Button, TextBox
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from scene import CUBE_HALF, CUBE_TOP, STANDOFF, cube_faces

FPS = 20
TRAIL_POINTS = 600
LIMIT_M = 2.35
Z_LIMIT_M = 1.40
HEADING_M = 0.38
COLORS = ("#0077bb", "#009988", "#cc3311")
CUBE_FACE = "#f0c14a"
CUBE_EDGE = "#6b4e16"


def _set_line3d(artist, xs, ys, zs):
    if hasattr(artist, "set_data_3d"):
        artist.set_data_3d(xs, ys, zs)
        return
    artist.set_data(xs, ys)
    artist.set_3d_properties(zs)


def _set_point3d(artist, x, y, z):
    _set_line3d(artist, [x], [y], [z])


WORD_BACK = {"alt+backspace", "ctrl+backspace", "ctrl+w"}
WORD_FORWARD = {"alt+delete", "ctrl+delete"}
LINE_BACK = {"cmd+backspace", "super+backspace", "ctrl+u", "escape"}


def _word_start(text: str, cursor: int) -> int:
    index = cursor
    while index > 0 and text[index - 1].isspace():
        index -= 1
    while index > 0 and not text[index - 1].isspace():
        index -= 1
    return index


def _word_end(text: str, cursor: int) -> int:
    index = cursor
    while index < len(text) and text[index].isspace():
        index += 1
    while index < len(text) and not text[index].isspace():
        index += 1
    return index


class CommandBox(TextBox):
    """TextBox that also deletes a whole word or the whole line.

    Backspace and Delete remove one character. Option+Backspace, Ctrl+Backspace,
    or Ctrl+W remove the word before the cursor. Option+Delete removes the next
    word. Cmd+Backspace, Ctrl+U, or Esc clear everything before the cursor.
    """

    def _keypress(self, event):
        if self.ignore(event) or not self.capturekeystrokes:
            return
        key = event.key or ""
        if key in {"\x7f", "\b"}:
            event.key = "backspace"
            key = "backspace"
        text, cursor = self.text, self.cursor_index
        if key in WORD_BACK:
            start = _word_start(text, cursor)
            text, cursor = text[:start] + text[cursor:], start
        elif key in WORD_FORWARD:
            text = text[:cursor] + text[_word_end(text, cursor):]
        elif key in LINE_BACK:
            text, cursor = text[cursor:], 0
        else:
            super()._keypress(event)
            return
        self.text_disp.set_text(text)
        self.cursor_index = cursor
        self._rendercursor()
        if self.eventson:
            self._observers.process("change", self.text)


class ResponderView:
    def __init__(self, scene, voice=None, lines=None):
        self.scene = scene
        self.voice = voice
        self.trails = [[] for _ in scene.names]
        self._tracks = list(scene.tracks)
        self.lines = lines if lines is not None else queue.Queue()
        self.fig = plt.figure(figsize=(12.0, 6.8), dpi=100)
        self.top = self.fig.add_axes([0.05, 0.16, 0.42, 0.68])
        self.view = self.fig.add_axes([0.52, 0.14, 0.44, 0.70], projection="3d")
        self.title = self.fig.suptitle("", fontsize=14, y=0.975)
        self.caption = self.fig.text(0.5, 0.91, "", ha="center", va="center", fontsize=9, color="#333333")
        self._build_top()
        self._build_view()
        box = self.fig.add_axes([0.14, 0.035, 0.72, 0.055])
        self.box = CommandBox(box, "Command ", initial="", color="white", hovercolor="#f4f4f4")
        self._clearing = False
        self.box.on_submit(self._submitted)
        self.mic_status = self.fig.text(0.5, 0.012, "", ha="center", va="center", fontsize=9, color="#555555")
        self.mic_button = None
        if voice is not None:
            self.mic_button = Button(self.fig.add_axes([0.875, 0.035, 0.08, 0.055]), "Mic on", color="#d8f0dc", hovercolor="#c2e6c8")
            self.mic_button.on_clicked(self._toggle_mic)
        try:
            self.fig.canvas.manager.set_window_title("Swarm as First Responder (local model)")
        except Exception:
            pass
        self._paint(self.scene.pos)

    def _build_top(self):
        top = self.top
        top.add_patch(
            Circle(
                (0.0, 0.0),
                STANDOFF,
                fill=False,
                linestyle="--",
                linewidth=0.7,
                edgecolor="#d5d5d5",
                zorder=1,
            )
        )
        self.cube_top = Rectangle(
            (-CUBE_HALF, -CUBE_HALF),
            2 * CUBE_HALF,
            2 * CUBE_HALF,
            facecolor=CUBE_FACE,
            edgecolor=CUBE_EDGE,
            linewidth=1.4,
            zorder=3,
            alpha=0.96,
        )
        top.add_patch(self.cube_top)
        top.text(0.0, -CUBE_HALF - 0.16, "chair", ha="center", va="top", fontsize=8, color=CUBE_EDGE, zorder=3)
        self.marks = []
        self.trail_lines = []
        self.headings = []
        self.labels = []
        self.goals = []
        self.plans = []
        for index, name in enumerate(self.scene.names):
            color = COLORS[index % len(COLORS)]
            home = self.scene.home[index]
            top.scatter(
                [home[0]],
                [home[1]],
                facecolors="none",
                edgecolors=color,
                s=46,
                linewidths=1.2,
                zorder=3,
            )
            trail, = top.plot([], [], color=color, lw=1.5, zorder=2, solid_capstyle="round")
            heading, = top.plot([], [], color=color, lw=2.0, zorder=5, solid_capstyle="round")
            goal, = top.plot([], [], "+", color=color, ms=8, mew=1.2, zorder=4, alpha=0.45)
            plan, = top.plot([], [], color=color, lw=1.0, ls="--", zorder=2, alpha=0.45)
            self.plans.append(plan)
            mark, = top.plot(
                [],
                [],
                "o",
                color=color,
                ms=9,
                markeredgecolor="white",
                markeredgewidth=0.8,
                label=name,
                zorder=6,
            )
            label = top.text(
                0.0,
                0.0,
                str(index + 1),
                color=color,
                fontsize=8,
                ha="left",
                va="bottom",
                zorder=8,
                bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
            )
            self.marks.append(mark)
            self.trail_lines.append(trail)
            self.headings.append(heading)
            self.goals.append(goal)
            self.labels.append(label)
        self.active_ring = top.scatter(
            [0.0],
            [0.0],
            s=150,
            facecolors="none",
            edgecolors="#222222",
            linewidths=1.0,
            zorder=5,
        )
        self.active_ring.set_alpha(0.0)
        top.set_aspect("equal", adjustable="box")
        top.set_xlim(-LIMIT_M, LIMIT_M)
        top.set_ylim(-LIMIT_M, LIMIT_M)
        top.set_xlabel("forward x (m)")
        top.set_ylabel("left y (m)")
        top.set_title("Top view")
        top.legend(loc="upper right", frameon=False)

    def _build_view(self):
        view = self.view
        cube = Poly3DCollection(
            cube_faces(),
            facecolors=CUBE_FACE,
            edgecolors=CUBE_EDGE,
            linewidths=1.0,
            alpha=0.96,
        )
        view.add_collection3d(cube)
        self.dots = []
        self.headings_3d = []
        self.drops = []
        self.plans_3d = []
        for index in range(len(self.scene.names)):
            color = COLORS[index % len(COLORS)]
            plan, = view.plot([], [], [], color=color, lw=0.9, ls="--", alpha=0.45)
            self.plans_3d.append(plan)
            drop, = view.plot([], [], [], color="#666666", lw=1.0, ls=":")
            heading, = view.plot([], [], [], color=color, lw=2.0)
            dot, = view.plot([], [], [], "o", color=color, ms=7, markeredgecolor="white", markeredgewidth=0.6)
            self.drops.append(drop)
            self.headings_3d.append(heading)
            self.dots.append(dot)
        view.set_xlim(-LIMIT_M, LIMIT_M)
        view.set_ylim(-LIMIT_M, LIMIT_M)
        view.set_zlim(0.0, Z_LIMIT_M)
        try:
            view.set_box_aspect((LIMIT_M * 2, LIMIT_M * 2, Z_LIMIT_M))
        except Exception:
            pass
        view.set_xlabel("x")
        view.set_ylabel("y")
        view.set_zlabel("z")
        view.set_title("Room")
        view.view_init(elev=28, azim=-58)

    def _toggle_mic(self, _event):
        self.voice.toggle()
        on = self.voice.enabled
        self.mic_button.label.set_text("Mic on" if on else "Mic off")
        self.mic_button.color = "#d8f0dc" if on else "#eeeeee"
        self.mic_button.hovercolor = "#c2e6c8" if on else "#dddddd"
        self.mic_button.ax.set_facecolor(self.mic_button.color)

    def _submitted(self, text):
        if self._clearing:
            return
        self._clearing = True
        self.box.set_val("")
        self._clearing = False
        self.scene.submit(text)

    def update(self, _frame):
        while True:
            try:
                self.scene.submit(self.lines.get_nowait())
            except queue.Empty:
                break
        pose = self.scene.tick()
        for index, track in enumerate(self.scene.tracks):
            if track is not None and track is not self._tracks[index]:
                self.trails[index] = []
            self._tracks[index] = track
        for index, point in enumerate(pose):
            self.trails[index].append((float(point[0]), float(point[1])))
            if len(self.trails[index]) > TRAIL_POINTS:
                del self.trails[index][:-TRAIL_POINTS]
        self._paint(pose)
        return ()

    def _paint(self, pose):
        elapsed = max(0.0, self.scene.clock() - self.scene.t_start)
        self.title.set_text(f"{self.scene.phase}    t={elapsed:0.1f} s")
        self.caption.set_text("\n".join(textwrap.wrap(self.scene.caption(), 150)[:3]))
        if self.voice is not None:
            self.mic_status.set_text(self.voice.status())
            if self.mic_button is not None and not self.voice.enabled and self.mic_button.label.get_text() == "Mic on":
                self.mic_button.label.set_text("Mic off")
        goal = self.scene.goal
        active = self.scene.active
        if active is None:
            self.active_ring.set_alpha(0.0)
        else:
            point = pose[active - 1]
            self.active_ring.set_offsets(np.asarray([[point[0], point[1]]], dtype=float))
            self.active_ring.set_alpha(0.9)
        for index, point in enumerate(pose):
            x, y, z = float(point[0]), float(point[1]), float(point[2])
            yaw = float(self.scene.yaw[index])
            nose_x = x + HEADING_M * math.cos(yaw)
            nose_y = y + HEADING_M * math.sin(yaw)
            self.marks[index].set_data([x], [y])
            self.headings[index].set_data([x, nose_x], [y, nose_y])
            if math.hypot(x, y) < CUBE_HALF + 0.08:
                self.labels[index].set_position((CUBE_HALF + 0.08, CUBE_HALF + 0.04))
            else:
                self.labels[index].set_position((x + 0.16, y + 0.14))
            trail = self.trails[index]
            if trail:
                self.trail_lines[index].set_data([item[0] for item in trail], [item[1] for item in trail])
            else:
                self.trail_lines[index].set_data([], [])
            plan = self.scene.path(index)
            if plan is None:
                self.plans[index].set_data([], [])
                _set_line3d(self.plans_3d[index], [], [], [])
            else:
                self.plans[index].set_data(plan[:, 0], plan[:, 1])
                _set_line3d(self.plans_3d[index], plan[:, 0], plan[:, 1], plan[:, 2])
            if goal is not None and float(np.hypot(goal[index, 0] - x, goal[index, 1] - y)) > 0.05:
                self.goals[index].set_data([goal[index, 0]], [goal[index, 1]])
            else:
                self.goals[index].set_data([], [])
            _set_point3d(self.dots[index], x, y, z)
            _set_line3d(self.headings_3d[index], [x, nose_x], [y, nose_y], [z, z])
            above = math.hypot(x, y) < CUBE_HALF + 0.05 and z > CUBE_TOP
            if above:
                _set_line3d(self.drops[index], [x, x], [y, y], [CUBE_TOP, z])
            else:
                _set_line3d(self.drops[index], [], [], [])

    def run(self):
        self._listen()
        self._anim = FuncAnimation(
            self.fig,
            self.update,
            interval=1000 / FPS,
            cache_frame_data=False,
            blit=False,
        )
        plt.show()

    def _listen(self):
        if sys.stdin is None or not sys.stdin.isatty():
            return

        def loop():
            print("Type a command here, or in the window.", flush=True)
            while True:
                try:
                    line = sys.stdin.readline()
                except Exception:
                    return
                if not line:
                    return
                self.lines.put(line)

        threading.Thread(target=loop, name="responder-stdin", daemon=True).start()
