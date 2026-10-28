#!/usr/bin/env python3
"""
CorsacVibes - a cozy little beat & tune maker, named for the corsac fox
(a small, pale, steppe-dwelling fox).

A step sequencer: tap in a drum beat and a little melody, press Play, and
CorsacVibes synthesises and loops it right there - no audio files needed.
Export what you make as a real .wav file to keep or share.

Only the Python standard library is used. Playback uses `winsound`, so it
needs Windows; without it you can still build patterns, save them, and
export .wav files to play elsewhere.

Start it with:  python corsacvibes.py   (or double-click start_corsacvibes.bat)
"""
from __future__ import annotations

import json
import math
import random
import sys
import tempfile
import threading
import queue
import wave
from array import array
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "CorsacVibes"
IS_WIN = sys.platform == "win32"

if IS_WIN:
    import winsound
else:
    winsound = None

# ---------------------------------------------------------------- palette
# Same cozy family as Buddy, Den and KitLaunch - paler and sandier for the
# corsac fox, with a few "vibes" accent colours for the sequencer grid.
CREAM = "#FFF6EA"
PAPER = "#FFFDF9"
PAPER2 = "#FBF2DF"
CARAMEL = "#F2A65A"
CARAMEL_DARK = "#E08E3C"
COCOA = "#5C4033"
EYE = "#6B5A3E"
SOFT = "#B49A80"
FUR = "#D9C7A8"
FUR_EDGE = "#B5A27E"
WHITE_FUR = "#FFFDF9"
NOSE = "#2B1B14"
ERR = "#B5483A"
OK = "#4F8A4B"
BUBBLE_EDGE = "#EBD3B8"
CARD_BORDER = "#EADFC8"
PLAYHEAD = "#E0A83C"

DRUM_COLORS = {"Kick": "#C84B4B", "Snare": "#3B6EA5", "Hi-hat": "#D9A23B", "Clap": "#7FB069"}
MELODY_COLOR = "#8B5AA3"

# ------------------------------------------------------------------- music
STEPS = 16
SAMPLE_RATE = 44100
DRUM_TRACKS = ["Kick", "Snare", "Hi-hat", "Clap"]

_NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _midi(letter: str, octave: int) -> int:
    return (octave + 1) * 12 + _NOTE_INDEX[letter]


def _freq(midi_number: int) -> float:
    return 440.0 * (2.0 ** ((midi_number - 69) / 12.0))


def _build_melody_notes() -> list[tuple[str, float]]:
    notes = []
    for octave in (3, 4, 5):
        for letter in ("C", "D", "E", "F", "G", "A", "B"):
            if octave == 5 and letter != "C":
                continue
            notes.append((f"{letter}{octave}", _freq(_midi(letter, octave))))
    return list(reversed(notes))  # top row = highest note


MELODY_NOTES = _build_melody_notes()  # 15 rows, C3..C5, high to low


# -------------------------------------------------------------- synthesis
def synth_lead(freq: float, n: int, volume: float = 0.28) -> array:
    """A small chip-style square wave note with a soft attack/release."""
    buf = array("h", bytes(2 * n))
    if n <= 0:
        return buf
    phase = 0.0
    inc = freq / SAMPLE_RATE
    attack = max(1, int(n * 0.03))
    release = max(1, int(n * 0.15))
    for i in range(n):
        phase = (phase + inc) % 1.0
        val = 1.0 if phase < 0.5 else -1.0
        if i < attack:
            env = i / attack
        elif i > n - release:
            env = max(0.0, (n - i) / release)
        else:
            env = 1.0
        buf[i] = int(max(-1.0, min(1.0, val * env * volume)) * 32767)
    return buf


def synth_drum(kind: str, n: int, volume: float = 0.9) -> array:
    buf = array("h", bytes(2 * n))
    if n <= 0:
        return buf
    if kind == "Kick":
        f_hi, f_lo = 150.0, 45.0
        phase = 0.0
        pitch_tau = max(1.0, n * 0.12)
        amp_tau = max(1.0, n * 0.35)
        for i in range(n):
            freq = f_lo + (f_hi - f_lo) * math.exp(-i / pitch_tau)
            phase += freq / SAMPLE_RATE
            env = math.exp(-i / amp_tau)
            val = math.sin(2 * math.pi * phase) * env * volume
            buf[i] = int(max(-1.0, min(1.0, val)) * 32767)
    elif kind in ("Snare", "Clap"):
        amp_tau = max(1.0, n * (0.45 if kind == "Snare" else 0.3))
        phase = 0.0
        for i in range(n):
            env = math.exp(-i / amp_tau)
            noise = random.uniform(-1.0, 1.0)
            phase += 180.0 / SAMPLE_RATE
            tone = math.sin(2 * math.pi * phase) * 0.3
            val = (noise * 0.8 + tone) * env * volume
            buf[i] = int(max(-1.0, min(1.0, val)) * 32767)
    elif kind == "Hi-hat":
        amp_tau = max(1.0, n * 0.2)
        for i in range(n):
            env = math.exp(-i / amp_tau)
            val = random.uniform(-1.0, 1.0) * env * volume * 0.6
            buf[i] = int(max(-1.0, min(1.0, val)) * 32767)
    return buf


def _mix_into(master: array, buf: array, offset: int) -> None:
    for i, v in enumerate(buf):
        master[offset + i] += v


def render_pattern(pattern: dict, repeats: int = 1) -> bytes:
    """Mix the whole pattern down to 16-bit mono PCM bytes at SAMPLE_RATE."""
    bpm = max(1, pattern.get("bpm", 120))
    steps = len(pattern["drums"][DRUM_TRACKS[0]])
    step_dur = 60.0 / bpm / 4.0
    step_n = max(1, int(SAMPLE_RATE * step_dur))
    total = step_n * steps * max(1, repeats)
    master = array("i", bytes(4 * total))

    for rep in range(max(1, repeats)):
        base = rep * steps * step_n
        for s in range(steps):
            off = base + s * step_n
            for track in DRUM_TRACKS:
                if pattern["drums"][track][s]:
                    _mix_into(master, synth_drum(track, step_n), off)
            for row, (_label, freq) in enumerate(MELODY_NOTES):
                if pattern["melody"][row][s]:
                    _mix_into(master, synth_lead(freq, step_n), off)

    peak = 1
    for v in master:
        a = v if v >= 0 else -v
        if a > peak:
            peak = a
    scale = min(1.0, 29000.0 / peak)
    pcm = array("h", (max(-32768, min(32767, int(v * scale))) for v in master))
    return pcm.tobytes()


def write_wav(path, pcm_bytes: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_bytes)


def new_pattern(bpm: int = 120, steps: int = STEPS) -> dict:
    return {
        "bpm": bpm,
        "drums": {track: [False] * steps for track in DRUM_TRACKS},
        "melody": [[False] * steps for _ in MELODY_NOTES],
    }


# ------------------------------------------------------------------ fox art
class FoxPanel:
    SIZE = 150

    def __init__(self, parent: tk.Widget):
        self.frame = tk.Frame(parent, bg=CREAM, width=190)
        self.frame.pack_propagate(False)

        tk.Label(self.frame, text="Corsac", font=("Georgia", 14, "bold italic"),
                 fg=COCOA, bg=CREAM).pack(pady=(10, 0))
        tk.Label(self.frame, text="your beatmaking fox", font=("Georgia", 9, "italic"),
                 fg=SOFT, bg=CREAM).pack()

        self.canvas = tk.Canvas(self.frame, width=self.SIZE, height=self.SIZE, bg=CREAM, highlightthickness=0)
        self.canvas.pack(pady=(4, 6))

        bubble = tk.Frame(self.frame, bg="white", highlightbackground=BUBBLE_EDGE, highlightthickness=2)
        bubble.pack(fill="x", padx=12)
        self.said = tk.StringVar(value="Tap in a beat, then press Play!")
        tk.Label(bubble, textvariable=self.said, font=("Segoe UI", 9), fg=COCOA, bg="white",
                 wraplength=160, justify="left", anchor="nw", height=5, padx=8, pady=8).pack(fill="x")

        self.mood = "idle"
        self.bob = 0
        self.draw()

    def say(self, text: str, mood: str = "idle") -> None:
        self.mood = mood
        self.said.set(text)
        self.draw()

    def pulse(self) -> None:
        """A tiny head-bob, called once per step while playing."""
        self.bob = 1 - self.bob
        self.draw()

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        cx = self.SIZE // 2
        cy = 84 - (3 if (self.mood == "happy" and self.bob) else 0)
        mood = self.mood

        for side in (-1, 1):
            bx, by = cx + side * 22, cy - 40
            tip = (bx + side * 14, by - 38)
            c.create_polygon(bx - side * 15, by + 8, bx + side * 17, by - 6, tip,
                              fill=FUR, outline=FUR_EDGE, width=2)
            inner_tip = (bx + side * 9, by - 20)
            c.create_polygon(bx - side * 7, by, bx + side * 8, by - 6, inner_tip,
                              fill=WHITE_FUR, outline="")

        c.create_oval(cx - 50, cy - 40, cx + 50, cy + 42, fill=FUR, outline=FUR_EDGE, width=2)
        c.create_oval(cx - 31, cy + 2, cx + 31, cy + 38, fill=WHITE_FUR, outline="")

        for side in (-1, 1):
            ex, ey = cx + side * 19, cy - 3
            if mood == "happy":
                c.create_arc(ex - 7, ey - 5, ex + 7, ey + 9, start=0, extent=180, style=tk.ARC, outline=EYE, width=3)
            else:
                c.create_oval(ex - 5, ey - 6, ex + 5, ey + 6, fill=EYE, outline="")
                c.create_oval(ex - 3.5, ey - 4.5, ex - 0.5, ey - 1.5, fill="white", outline="")
                if mood == "worried":
                    if side < 0:
                        c.create_line(ex - 7, ey - 9, ex + 5, ey - 13, fill=EYE, width=2, capstyle=tk.ROUND)
                    else:
                        c.create_line(ex - 5, ey - 13, ex + 7, ey - 9, fill=EYE, width=2, capstyle=tk.ROUND)

        c.create_polygon(cx - 5, cy + 7, cx + 5, cy + 7, cx, cy + 13, fill=NOSE, outline="")
        if mood == "happy":
            c.create_arc(cx - 8, cy + 11, cx + 8, cy + 27, start=180, extent=180, style=tk.CHORD,
                         fill="#E8808A", outline=EYE, width=2)
        elif mood == "worried":
            c.create_arc(cx - 7, cy + 16, cx + 7, cy + 26, start=0, extent=180, style=tk.ARC, outline=EYE, width=2)
        else:
            c.create_line(cx, cy + 13, cx, cy + 17, fill=EYE, width=2)


# ------------------------------------------------------------------ grid UI
class StepCell:
    def __init__(self, parent: tk.Widget, color: str, off_color: str, on_toggle=None):
        self.canvas = tk.Canvas(parent, width=24, height=24, bg=CREAM, highlightthickness=0)
        self.color = color
        self.off_color = off_color
        self.active = False
        self.on_toggle = on_toggle
        self.rect = self.canvas.create_rectangle(2, 2, 22, 22, fill=off_color, outline=CARD_BORDER, width=1)
        self.canvas.bind("<Button-1>", self._click)

    def grid(self, **kw) -> None:
        self.canvas.grid(**kw)

    def _click(self, _event=None) -> None:
        self.set_active(not self.active)
        if self.on_toggle:
            self.on_toggle(self.active)

    def set_active(self, value: bool) -> None:
        self.active = value
        self.canvas.itemconfig(self.rect, fill=(self.color if value else self.off_color))

    def set_playhead(self, on: bool) -> None:
        self.canvas.itemconfig(self.rect, outline=(PLAYHEAD if on else CARD_BORDER), width=(3 if on else 1))


# ------------------------------------------------------------------ the app
class CorsacVibesApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.configure(bg=CREAM)
        root.resizable(False, False)

        self.pattern = new_pattern()
        self.playing = False
        self.current_step = -1
        self.play_job = None
        self.render_queue: queue.Queue = queue.Queue()
        self.preview_path = Path(tempfile.gettempdir()) / "corsacvibes_preview.wav"

        self.drum_cells: dict[str, list[StepCell]] = {}
        self.melody_cells: list[list[StepCell]] = []

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(300, lambda: self.fox.say("Tap in a beat, then press Play!", "idle"))
        self.root.after(200, self.pump_results)

    # ------------------------------------------------------------- building
    def build_ui(self) -> None:
        top = tk.Frame(self.root, bg=CARAMEL_DARK)
        top.pack(side="top", fill="x")

        self.play_btn = tk.Button(top, text="▶ Play", command=self.toggle_play, bg=CARAMEL, fg="white",
                                  activebackground=CARAMEL_DARK, activeforeground="white", relief="flat", bd=0,
                                  font=("Segoe UI", 10, "bold"), cursor="hand2", padx=12, pady=5)
        self.play_btn.pack(side="left", padx=(8, 4), pady=6)

        for label, cmd in [("Clear", self.clear_pattern), ("Save...", self.save_pattern),
                           ("Load...", self.load_pattern), ("Export WAV...", self.export_wav)]:
            tk.Button(top, text=label, command=cmd, bg=CARAMEL, fg="white", activebackground=CARAMEL_DARK,
                     activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                     cursor="hand2", padx=10, pady=5).pack(side="left", padx=4, pady=6)

        tk.Label(top, text="BPM:", bg=CARAMEL_DARK, fg="white").pack(side="left", padx=(16, 4))
        self.bpm_var = tk.IntVar(value=self.pattern["bpm"])
        tk.Spinbox(top, from_=60, to=200, width=5, textvariable=self.bpm_var,
                  command=self._on_bpm_change).pack(side="left", pady=6)

        main = tk.Frame(self.root, bg=CREAM)
        main.pack(side="top", fill="both", expand=True)

        self.fox = FoxPanel(main)
        self.fox.frame.pack(side="right", fill="y")

        grids = tk.Frame(main, bg=CREAM)
        grids.pack(side="left", fill="both", expand=True, padx=12, pady=10)

        tk.Label(grids, text="Beat", font=("Segoe UI", 10, "bold"), fg=COCOA, bg=CREAM, anchor="w"
                ).pack(fill="x")
        drum_frame = tk.Frame(grids, bg=CREAM)
        drum_frame.pack(fill="x", pady=(2, 14))
        self.build_header(drum_frame, row=0, col_offset=1)
        for r, track in enumerate(DRUM_TRACKS, start=1):
            tk.Label(drum_frame, text=track, font=("Segoe UI", 9), fg=COCOA, bg=CREAM, width=7, anchor="w"
                    ).grid(row=r, column=0, sticky="w", padx=(0, 6))
            cells = []
            for s in range(STEPS):
                cell = StepCell(drum_frame, DRUM_COLORS[track], self._off_color(s),
                               on_toggle=self._make_toggle(self.pattern["drums"][track], s))
                cell.grid(row=r, column=s + 1, padx=1, pady=1)
                cells.append(cell)
            self.drum_cells[track] = cells

        tk.Label(grids, text="Melody", font=("Segoe UI", 10, "bold"), fg=COCOA, bg=CREAM, anchor="w"
                ).pack(fill="x")
        melody_frame = tk.Frame(grids, bg=CREAM)
        melody_frame.pack(fill="x")
        self.build_header(melody_frame, row=0, col_offset=1)
        for r, (label, _freq) in enumerate(MELODY_NOTES, start=1):
            sharp = "#" not in label and label[0] in ("C",)  # tint the C rows so octaves are easy to spot
            fg = COCOA if not sharp else CARAMEL_DARK
            tk.Label(melody_frame, text=label, font=("Segoe UI", 9, "bold" if sharp else "normal"),
                    fg=fg, bg=CREAM, width=4, anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 6))
            row_data = self.pattern["melody"][r - 1]
            cells = []
            for s in range(STEPS):
                cell = StepCell(melody_frame, MELODY_COLOR, self._off_color(s),
                               on_toggle=self._make_toggle(row_data, s))
                cell.grid(row=r, column=s + 1, padx=1, pady=1)
                cells.append(cell)
            self.melody_cells.append(cells)

        self.status_v = tk.StringVar(value="Ready")
        bar = tk.Frame(self.root, bg=CARAMEL_DARK)
        bar.pack(side="bottom", fill="x")
        tk.Label(bar, textvariable=self.status_v, bg=CARAMEL_DARK, fg="white", anchor="w",
                font=("Segoe UI", 9)).pack(side="left", padx=10, pady=3)
        if not IS_WIN:
            tk.Label(bar, text="No speaker on this platform - Export still works.", bg=CARAMEL_DARK,
                    fg="white", font=("Segoe UI", 9, "italic")).pack(side="right", padx=10, pady=3)

    def build_header(self, parent: tk.Widget, row: int, col_offset: int) -> None:
        tk.Label(parent, text="", width=7 if col_offset == 1 else 4, bg=CREAM
                ).grid(row=row, column=0)
        for s in range(STEPS):
            bold = (s % 4 == 0)
            tk.Label(parent, text=str(s + 1), font=("Segoe UI", 8, "bold" if bold else "normal"),
                    fg=COCOA if bold else SOFT, bg=CREAM, width=2
                    ).grid(row=row, column=s + 1)

    @staticmethod
    def _off_color(step: int) -> str:
        return PAPER if (step // 4) % 2 == 0 else PAPER2

    def _make_toggle(self, row_list: list, step: int):
        def _toggle(value: bool) -> None:
            row_list[step] = value
        return _toggle

    def _on_bpm_change(self) -> None:
        try:
            self.pattern["bpm"] = max(40, min(240, int(self.bpm_var.get())))
        except (tk.TclError, ValueError):
            pass

    # ------------------------------------------------------------- playback
    def toggle_play(self) -> None:
        if self.playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self) -> None:
        if not IS_WIN:
            self.fox.say("Playback needs Windows. Try Export WAV instead, and play it in any player!", "worried")
            return
        self.play_btn.config(text="Rendering...", state="disabled")
        self.status_v.set("Rendering...")
        threading.Thread(target=self._render_and_play, daemon=True).start()

    def _render_and_play(self) -> None:
        try:
            pcm = render_pattern(self.pattern, repeats=1)
            write_wav(self.preview_path, pcm)
            winsound.PlaySound(str(self.preview_path),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            self.render_queue.put(("playing",))
        except Exception as e:
            self.render_queue.put(("error", str(e)))

    def stop_play(self) -> None:
        self.playing = False
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job = None
        if IS_WIN:
            winsound.PlaySound(None, winsound.SND_PURGE)
        self.clear_playhead()
        self.play_btn.config(text="▶ Play", state="normal")
        self.status_v.set("Stopped.")

    def pump_results(self) -> None:
        try:
            while True:
                try:
                    item = self.render_queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] == "playing":
                    self.playing = True
                    self.current_step = -1
                    self.play_btn.config(text="■ Stop", state="normal")
                    self.status_v.set("Playing - loops until you press Stop.")
                    self.fox.say("Here we go!", "happy")
                    self._advance_playhead()
                else:
                    self.play_btn.config(text="▶ Play", state="normal")
                    self.status_v.set("Couldn't play that.")
                    self.fox.say(f"Hmm, playback hiccuped: {item[1]}", "worried")
        finally:
            self.root.after(200, self.pump_results)

    def _advance_playhead(self) -> None:
        if not self.playing:
            return
        self.clear_playhead()
        self.current_step = (self.current_step + 1) % STEPS
        for cells in list(self.drum_cells.values()) + self.melody_cells:
            cells[self.current_step].set_playhead(True)
        if self.current_step % 4 == 0:
            self.fox.pulse()
        step_dur = 60.0 / max(40, self.pattern["bpm"]) / 4.0
        self.play_job = self.root.after(max(30, int(step_dur * 1000)), self._advance_playhead)

    def clear_playhead(self) -> None:
        if self.current_step < 0:
            return
        for cells in list(self.drum_cells.values()) + self.melody_cells:
            cells[self.current_step].set_playhead(False)
        self.current_step = -1

    # --------------------------------------------------------------- files
    def clear_pattern(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Clear the whole pattern?"):
            return
        for track in DRUM_TRACKS:
            for s in range(STEPS):
                self.pattern["drums"][track][s] = False
                self.drum_cells[track][s].set_active(False)
        for r, row in enumerate(self.pattern["melody"]):
            for s in range(STEPS):
                row[s] = False
                self.melody_cells[r][s].set_active(False)
        self.fox.say("A fresh page! What shall we make?", "happy")

    def save_pattern(self) -> None:
        chosen = filedialog.asksaveasfilename(
            parent=self.root, title="Save pattern", defaultextension=".json",
            filetypes=[("CorsacVibes pattern", "*.json")], initialfile="my_beat.json")
        if not chosen:
            return
        try:
            Path(chosen).write_text(json.dumps(self.pattern, indent=2), encoding="utf-8")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't save:\n{e}")
            return
        self.status_v.set(f"Saved {Path(chosen).name}")
        self.fox.say("Saved! That one's a keeper.", "happy")

    def load_pattern(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root, title="Load pattern", filetypes=[("CorsacVibes pattern", "*.json"), ("All files", "*.*")])
        if not chosen:
            return
        try:
            data = json.loads(Path(chosen).read_text(encoding="utf-8"))
            steps = len(data["drums"][DRUM_TRACKS[0]])
            if steps != STEPS or len(data["melody"]) != len(MELODY_NOTES):
                raise ValueError("That file doesn't match this pattern shape.")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Couldn't read that pattern:\n{e}")
            return
        self.pattern = data
        self.bpm_var.set(self.pattern.get("bpm", 120))
        for track in DRUM_TRACKS:
            for s in range(STEPS):
                self.drum_cells[track][s].set_active(bool(self.pattern["drums"][track][s]))
        for r, row in enumerate(self.pattern["melody"]):
            for s in range(STEPS):
                self.melody_cells[r][s].set_active(bool(row[s]))
        self.status_v.set(f"Loaded {Path(chosen).name}")
        self.fox.say("Got it! Loaded that one up.", "happy")

    def export_wav(self) -> None:
        repeats = simpledialog.askinteger(APP_NAME, "How many times should the pattern loop?",
                                          initialvalue=4, minvalue=1, maxvalue=64, parent=self.root)
        if not repeats:
            return
        chosen = filedialog.asksaveasfilename(
            parent=self.root, title="Export as WAV", defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")], initialfile="my_beat.wav")
        if not chosen:
            return
        self.status_v.set("Rendering export...")
        threading.Thread(target=self._export_worker, args=(chosen, repeats), daemon=True).start()

    def _export_worker(self, chosen: str, repeats: int) -> None:
        try:
            pcm = render_pattern(self.pattern, repeats=repeats)
            write_wav(chosen, pcm)
            self.render_queue.put(("exported", chosen))
        except Exception as e:
            self.render_queue.put(("export_error", str(e)))

    def on_close(self) -> None:
        if self.playing:
            self.stop_play()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CorsacVibesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
