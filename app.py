"""MotionClick Studio — four screen/audio automation tools in one Windows GUI.

Run: python app.py
Stop: Esc (global), GUI Stop, or move cursor to top-left (PyAutoGUI failsafe).
"""
from __future__ import annotations

import csv
import math
import queue
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from square_tracking import (SquareTrack, associate_and_update,
                             detect_squares, draw_results)

# Deliberately import platform/device libraries lazily: allows loading the GUI
# and testing project logic without needing Windows audio/screen hardware.
APP_NAME = "MotionClick Studio"
VERSION = "1.0.0"
OUTPUT_DIR = Path.home() / "Documents" / "MotionClick Studio"
MODES = ("Sound → Click", "Timed Click", "Red Ball", "Blue Squares")


def validate_number(value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not math.isfinite(number) or not (minimum <= number <= maximum):
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return number


def pick_random_delay(low: float, high: float, rng=random.uniform) -> float:
    if high < low:
        raise ValueError("Maximum delay must be at least the minimum delay.")
    return rng(low, high)


def predicted_position(x, y, vx, vy, horizon):
    return x + vx * horizon, y + vy * horizon


class MotionClickStudio:
    BG = "#101724"
    PANEL = "#1b2535"
    BORDER = "#344359"
    TEXT = "#e9f0f8"
    MUTED = "#9caec5"
    ACCENT = "#5c9cfa"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME}  ·  v{VERSION}")
        self.root.geometry("930x750")
        self.root.minsize(790, 650)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.messages = queue.Queue()
        self.stop_event = threading.Event()
        self.close_event = threading.Event()
        self.active_mode = None
        self.state = "idle"  # idle, armed, running
        self.worker = None
        self.mouse_listener = None
        self.keyboard_listener = None
        self.preview_queue = queue.Queue(maxsize=2)
        self.preview_photo = None
        self.lock = threading.RLock()
        self.synthetic_click = False
        self.ignore_until = 0.0
        self.region = None
        self.started_at = None

        self.sound_delay = tk.StringVar(value="1.5")
        self.sound_threshold = tk.StringVar(value="0.015")
        self.sound_silence = tk.StringVar(value="0.08")
        self.click_min = tk.StringVar(value="7.95")
        self.click_max = tk.StringVar(value="8.05")
        self.ball_interval = tk.StringVar(value="0.2")
        self.ball_prediction = tk.StringVar(value="0.04")
        self.ball_min_area = tk.StringVar(value="80")
        self.blue_fps = tk.StringVar(value="30")
        self.monitor_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="Idle · Select a tool and press Arm")
        self.region_var = tk.StringVar(value="No board region selected")

        self.setup_style()
        self.setup_ui()
        self.start_listeners()
        self.root.after(60, self.flush_messages)
        self.post("Ready. ESC stops all tools; the Stop button works too.")

    def setup_style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TFrame", background=self.BG)
        s.configure("Panel.TFrame", background=self.PANEL)
        s.configure("TLabel", background=self.PANEL, foreground=self.TEXT,
                    font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=self.PANEL, foreground=self.MUTED,
                    font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=self.BG, foreground=self.TEXT,
                    font=("Segoe UI Semibold", 21))
        s.configure("Status.TLabel", background=self.BG, foreground=self.ACCENT,
                    font=("Segoe UI Semibold", 10))
        s.configure("TEntry", fieldbackground="#101724", foreground=self.TEXT,
                    bordercolor=self.BORDER, lightcolor=self.ACCENT, padding=7)
        s.configure("TCombobox", fieldbackground="#101724", foreground=self.TEXT,
                    padding=6)
        s.configure("TButton", background="#33445a", foreground=self.TEXT,
                    font=("Segoe UI Semibold", 10), padding=(15, 10),
                    borderwidth=0)
        s.map("TButton", background=[("active", "#466280")])
        s.configure("Accent.TButton", background=self.ACCENT,
                    foreground="#091626")
        s.map("Accent.TButton", background=[("active", "#86b6ff")])
        s.configure("TNotebook", background=self.BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=self.PANEL,
                    foreground=self.MUTED, padding=(16, 12), font=("Segoe UI", 10))
        s.map("TNotebook.Tab", foreground=[("selected", self.TEXT)],
              background=[("selected", "#304361")])

    def setup_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=22, pady=(18, 15))
        ttk.Label(outer, text="MotionClick Studio", style="Title.TLabel").pack(anchor="w")
        tk.Label(outer, text="Audio triggers  /  Timed clicks  /  Visual tracking",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(
                     anchor="w", pady=(0, 12))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        for title in MODES:
            page = ttk.Frame(self.tabs, style="Panel.TFrame", padding=22)
            self.tabs.add(page, text=title)
            if title == MODES[0]:
                self.build_sound(page)
            elif title == MODES[1]:
                self.build_timed(page)
            elif title == MODES[2]:
                self.build_ball(page)
            else:
                self.build_blue(page)
        self.tabs.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(14, 8))
        self.arm_button = ttk.Button(controls, text="Arm selected tool",
                                     style="Accent.TButton", command=self.arm)
        self.arm_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop / Disarm",
                                      command=self.stop)
        self.stop_button.pack(side="left", padx=10)
        ttk.Button(controls, text="Open results folder",
                   command=self.open_results).pack(side="right")
        tk.Label(outer, textvariable=self.status_var, bg=self.BG, fg=self.ACCENT,
                 anchor="w", font=("Segoe UI Semibold", 10)).pack(fill="x", pady=(0, 8))
        tk.Label(outer, text="ACTIVITY", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w")
        self.log = tk.Text(outer, height=6, bg="#0b111d", fg="#c4d4e9",
                           insertbackground="white", relief="flat",
                           font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="x", pady=(6, 0))

    def header(self, parent, title, subtitle):
        ttk.Label(parent, text=title, font=("Segoe UI Semibold", 16)).pack(anchor="w")
        ttk.Label(parent, text=subtitle, style="Muted.TLabel", wraplength=690,
                  justify="left").pack(anchor="w", pady=(5, 22))

    def field(self, parent, label, variable, row, column=0, width=14):
        container = ttk.Frame(parent, style="Panel.TFrame")
        container.grid(row=row, column=column, sticky="w", padx=(0, 32), pady=10)
        ttk.Label(container, text=label, style="Muted.TLabel").pack(anchor="w", pady=(0, 5))
        ttk.Entry(container, textvariable=variable, width=width).pack(anchor="w")

    def instructions(self, parent, message):
        tk.Label(parent, text=message, bg=self.PANEL, fg=self.MUTED,
                 justify="left", anchor="w", wraplength=700,
                 font=("Segoe UI", 10)).pack(fill="x", pady=(18, 0))

    def build_sound(self, page):
        self.header(page, "Sound-triggered click",
                    "Capture system output audio (not microphone). Detect a new sound, wait, and click once.")
        frame = ttk.Frame(page, style="Panel.TFrame")
        frame.pack(anchor="w")
        self.field(frame, "Sound → click delay (seconds)", self.sound_delay, 0, 0)
        self.field(frame, "Audio detection threshold (RMS)", self.sound_threshold, 0, 1)
        self.field(frame, "Silence required to re-arm (seconds)", self.sound_silence, 1, 0)
        self.instructions(page, "Arm starts listening immediately. Each new sound schedules one click after "
                          "the delay. No second click is queued until the first has fired and sound has fallen silent.")

    def build_timed(self, page):
        self.header(page, "Random delayed click",
                    "Release the left mouse button once. The app waits a randomized duration, then left-clicks.")
        frame = ttk.Frame(page, style="Panel.TFrame")
        frame.pack(anchor="w")
        self.field(frame, "Minimum delay (seconds)", self.click_min, 0, 0)
        self.field(frame, "Maximum delay (seconds)", self.click_max, 0, 1)
        self.instructions(page, "Arm, then release the left button in your game or website. "
                          "The timer starts on release, not press. The automatic click does not re-trigger the timer. "
                          "After completion, make another manual click to repeat. Actual Windows timing may vary.")

    def build_ball(self, page):
        self.header(page, "Predictive red-ball follower",
                    "Find the red circle on screen and move the cursor toward its estimated future position.")
        frame = ttk.Frame(page, style="Panel.TFrame")
        frame.pack(anchor="w")
        self.field(frame, "Scan interval (seconds)", self.ball_interval, 0, 0)
        self.field(frame, "Predict ahead (seconds)", self.ball_prediction, 0, 1)
        self.field(frame, "Minimum red ball area (px²)", self.ball_min_area, 1, 0)
        self.field(frame, "Monitor number", self.monitor_var, 1, 1)
        self.instructions(page, "Arm, then release left mouse to start tracking. Tracks a red circular object "
                          "using smoothed velocity estimation. 0.2s scan interval and 0.04s prediction are "
                          "defaults; sudden motion changes may cause errors. No automatic clicking.")

    def build_blue(self, page):
        self.header(page, "Blue-square shuffle tracker",
                    "Remember which squares started blue, even after they all become white.")
        frame = ttk.Frame(page, style="Panel.TFrame")
        frame.pack(anchor="w")
        self.field(frame, "Target tracking FPS", self.blue_fps, 0, 0)
        self.field(frame, "Monitor number", self.monitor_var, 0, 1)
        ttk.Button(page, text="Select game board region", command=self.select_region).pack(anchor="w", pady=(15, 8))
        ttk.Label(page, textvariable=self.region_var, style="Muted.TLabel").pack(anchor="w")
        self.instructions(page, "Choose the board region, then Arm and release left mouse during the blue phase. "
                          "F finishes/saves; uncertainty is flagged after ambiguous crossings. "
                          "Output: PNGs + CSV in Documents/MotionClick Studio.")
        self.preview_label = tk.Label(page, text="Live tracked-board preview will appear here",
                                      bg="#0d1523", fg=self.MUTED,
                                      width=60, height=8, anchor="center")
        self.preview_label.pack(fill="both", expand=True, pady=(12, 0))

    def post(self, message, status=None):
        self.messages.put((str(message), status))

    def flush_messages(self):
        try:
            while True:
                message, status = self.messages.get_nowait()
                if status:
                    self.status_var.set(status)
                self.log.configure(state="normal")
                self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
                self.log.see("end")
                line_count = int(self.log.index("end-1c").split(".")[0])
                if line_count > 180:
                    self.log.delete("1.0", "30.0")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            from PIL import ImageTk
            while True:
                preview_image = self.preview_queue.get_nowait()
                self.preview_photo = ImageTk.PhotoImage(preview_image)
                self.preview_label.configure(image=self.preview_photo, text="")
        except queue.Empty:
            pass
        except Exception as exc:
            # The GUI remains usable even if Pillow is unavailable.
            pass
        if not self.close_event.is_set():
            self.root.after(75, self.flush_messages)

    def on_tab_changed(self, _event):
        with self.lock:
            active = self.state != "idle"
        if active:
            self.stop()
            self.post("Switched tools; previous tool stopped.")

    def start_listeners(self):
        try:
            from pynput import mouse, keyboard
        except ImportError:
            self.post("Missing pynput; run pip install -r requirements.txt", "Dependencies missing")
            return
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()
        self.keyboard_listener = keyboard.Listener(on_press=self.on_key)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()

    def on_key(self, key):
        from pynput import keyboard
        if key == keyboard.Key.esc:
            self.stop()
        elif getattr(key, "char", "") and getattr(key, "char", "").lower() == "f":
            with self.lock:
                if self.active_mode == MODES[3] and self.state == "running":
                    self.stop_event.set()
                    self.post("Finishing tracking; saving output…", "Saving results…")

    def on_click(self, _x, _y, button, pressed):
        from pynput import mouse
        if button != mouse.Button.left or pressed:
            return
        released_at = time.perf_counter()
        with self.lock:
            if self.synthetic_click or released_at < self.ignore_until:
                return
            if self.state == "armed" and self.active_mode in (MODES[2], MODES[3]):
                self.state = "running"
                self.started_at = released_at
                self.post(f"{self.active_mode}: capture started", "Tracking active · ESC to stop")
                self.launch_worker(self.red_ball_worker if self.active_mode == MODES[2]
                                   else self.blue_square_worker)
            elif self.state == "armed" and self.active_mode == MODES[1]:
                delay = pick_random_delay(self.parameters["min_delay"],
                                          self.parameters["max_delay"])
                self.state = "running"
                self.post(f"Manual mouse release detected; scheduled click in {delay:.4f}s",
                          "Timed click pending · ESC to cancel")
                self.launch_worker(self.timed_worker, released_at, delay)

    def get_parameters(self, mode):
        if mode == MODES[0]:
            return {"delay": validate_number(self.sound_delay.get(), "Sound delay", 0, 120),
                    "threshold": validate_number(self.sound_threshold.get(), "Threshold", 0.000001, 1),
                    "silence": validate_number(self.sound_silence.get(), "Silence interval", 0.005, 10)}
        if mode == MODES[1]:
            low = validate_number(self.click_min.get(), "Min delay", 0, 600)
            high = validate_number(self.click_max.get(), "Max delay", 0, 600)
            if low > high:
                raise ValueError("Minimum delay cannot exceed maximum delay.")
            return {"min_delay": low, "max_delay": high}
        if mode == MODES[2]:
            return {"interval": validate_number(self.ball_interval.get(), "Scan interval", 0.01, 5),
                    "prediction": validate_number(self.ball_prediction.get(), "Prediction", 0, 3),
                    "min_area": validate_number(self.ball_min_area.get(), "Minimum area", 3, 50000),
                    "monitor": int(validate_number(self.monitor_var.get(), "Monitor", 1, 20))}
        return {"fps": validate_number(self.blue_fps.get(), "Tracking FPS", 1, 120),
                "monitor": int(validate_number(self.monitor_var.get(), "Monitor", 1, 20))}

    def arm(self):
        mode = MODES[self.tabs.index(self.tabs.select())]
        try:
            params = self.get_parameters(mode)
            if mode == MODES[3] and self.region is None:
                raise ValueError("First click 'Select game board region'.")
            import numpy
            if mode in (MODES[2], MODES[3]):
                import cv2
                import mss
            if mode == MODES[3]:
                import scipy
            if mode == MODES[0]:
                import soundcard
            elif mode in (MODES[1], MODES[2]):
                import pyautogui
        except Exception as exc:
            messagebox.showerror("Unable to arm", str(exc))
            return
        self.stop()
        with self.lock:
            self.stop_event = threading.Event()
            self.parameters = params
            self.active_mode = mode
            self.state = "armed"
            self.ignore_until = time.perf_counter() + 0.15
            self.started_at = None
            if mode == MODES[0]:
                self.state = "running"
                self.launch_worker(self.sound_worker)
                self.post("Listening to output audio. ESC to stop.", "Monitoring system audio")
            else:
                self.post(f"{mode} armed. Release the left mouse button to start.",
                          "Armed · waiting for mouse release")

    def launch_worker(self, callback, *args):
        event = self.stop_event
        def guarded():
            try:
                callback(event, *args)
            except Exception as exc:
                self.post(f"Error in {self.active_mode}: {exc}", "Error · Disarmed")
                with self.lock:
                    if self.stop_event is event:
                        self.stop_event.set()
                        self.state = "idle"
                        self.active_mode = None
        threading.Thread(target=guarded, daemon=True).start()

    def stop(self):
        with self.lock:
            prior = self.state
            self.stop_event.set()
            self.state = "idle"
            self.active_mode = None
            self.ignore_until = time.perf_counter() + 0.2
        if prior != "idle":
            self.post("Stopped / disarmed.", "Idle · Select a tool and press Arm")

    def automatic_click(self, event):
        if event.is_set():
            return None
        import pyautogui
        with self.lock:
            if event.is_set():
                return None
            self.synthetic_click = True
        try:
            pyautogui.click(button="left")
            released = time.perf_counter()
            return released
        finally:
            with self.lock:
                self.ignore_until = time.perf_counter() + 0.30
                self.synthetic_click = False

    def timed_worker(self, event, started, delay):
        target = started + delay
        if event.wait(max(0.0, target - time.perf_counter())):
            return
        released = self.automatic_click(event)
        if released is not None:
            self.post(f"Click completed. Target {delay:.4f}s; observed Python interval "
                      f"{released - started:.4f}s.", "Ready for next manual release")
        with self.lock:
            if self.stop_event is event and not event.is_set():
                self.state = "armed"

    def sound_worker(self, event):
        import soundcard as sc
        import numpy as np
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default playback device found.")
        loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        if loopback is None:
            raise RuntimeError("Cannot find output loopback device.")
        self.post(f"Capturing audio: {speaker.name}")
        threshold = self.parameters["threshold"]
        silence = self.parameters["silence"]
        delay = self.parameters["delay"]
        last_sound = time.perf_counter()
        ready = True
        pending = False
        # 512-sample frames at 48kHz (10.7 ms nominal) are reliable across devices.
        with loopback.recorder(samplerate=48000, blocksize=1024) as recorder:
            while not event.is_set():
                audio = recorder.record(numframes=512)
                if audio.size == 0:
                    continue
                volume = float(np.sqrt(np.mean(np.asarray(audio, dtype=float)**2)))
                now = time.perf_counter()
                if volume >= threshold:
                    last_sound = now
                    if ready and not pending:
                        ready = False
                        pending = True
                        self.post(f"Audio detected (RMS {volume:.4f}); click in {delay:.2f}s")
                        # Scheduling is independent of future audio reads.
                        def schedule_click():
                            nonlocal pending
                            try:
                                if event.wait(delay):
                                    return
                                released = self.automatic_click(event)
                                if released is not None:
                                    self.post("Audio-triggered click completed.")
                            except Exception as exc:
                                self.post(f"Audio click failed: {exc}",
                                          "Audio click failed — stop and retry")
                                event.set()
                            finally:
                                pending = False
                        threading.Thread(target=schedule_click, daemon=True).start()
                elif now - last_sound >= silence and not pending:
                    ready = True

    def red_ball_worker(self, event):
        import cv2
        import mss
        import numpy as np
        import pyautogui
        params = self.parameters
        last_position = None
        last_time = None
        vx = vy = 0.0
        misses = 0
        count = 0
        with mss.mss() as sct:
            if params["monitor"] >= len(sct.monitors):
                raise ValueError(f"Monitor {params['monitor']} not present; available: 1–{len(sct.monitors)-1}")
            monitor = sct.monitors[params["monitor"]]
            while not event.is_set():
                tick = time.perf_counter()
                raw = np.asarray(sct.grab(monitor))
                capture_time = time.perf_counter()
                hsv = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                hsv = cv2.cvtColor(hsv, cv2.COLOR_BGR2HSV)
                mask = cv2.bitwise_or(cv2.inRange(hsv, (0,120,70),(10,255,255)),
                                      cv2.inRange(hsv, (170,120,70),(179,255,255)))
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                candidates = []
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area < params["min_area"]:
                        continue
                    perimeter = cv2.arcLength(contour, True)
                    if perimeter <= 0 or 4 * np.pi * area / (perimeter ** 2) < 0.65:
                        continue
                    (x, y), _radius = cv2.minEnclosingCircle(contour)
                    candidates.append((x + monitor["left"], y + monitor["top"], area))
                if candidates:
                    if last_position is None:
                        chosen = max(candidates, key=lambda c: c[2])
                    else:
                        dt = max(0.001, capture_time - last_time)
                        expected = (last_position[0] + vx * dt,
                                    last_position[1] + vy * dt)
                        chosen = min(candidates, key=lambda c: math.dist(c[:2], expected))
                    x, y, _ = chosen
                    if last_position is not None:
                        dt = max(0.001, capture_time-last_time)
                        alpha = 0.5
                        vx = alpha * (x-last_position[0])/dt + (1-alpha)*vx
                        vy = alpha * (y-last_position[1])/dt + (1-alpha)*vy
                    else:
                        vx = vy = 0.0
                    px, py = predicted_position(x,y,vx,vy,params["prediction"])
                    px = max(monitor["left"], min(px, monitor["left"]+monitor["width"]-1))
                    py = max(monitor["top"], min(py, monitor["top"]+monitor["height"]-1))
                    if not event.is_set():
                        pyautogui.moveTo(round(px), round(py), duration=0)
                    last_position = (x,y)
                    last_time = capture_time
                    misses = 0
                    count += 1
                    if count % 15 == 0:
                        self.post(f"Red ball ({x:.0f}, {y:.0f}) → predicted ({px:.0f}, {py:.0f})")
                else:
                    misses += 1
                    if misses > 4:
                        last_position = last_time = None
                        vx = vy = 0.0
                event.wait(max(0.0, params["interval"]-(time.perf_counter()-tick)))

    def select_region(self):
        with self.lock:
            if self.state != "idle":
                messagebox.showinfo("Stop tracking first", "Stop the active tool before selecting a region.")
                return
        try:
            import cv2
            import numpy as np
            import mss
            monitor_number = int(validate_number(self.monitor_var.get(), "Monitor", 1, 20))
            with mss.mss() as sct:
                if monitor_number >= len(sct.monitors):
                    raise ValueError(f"Monitor {monitor_number} not available.")
                mon = sct.monitors[monitor_number]
                screen = cv2.cvtColor(np.asarray(sct.grab(mon)), cv2.COLOR_BGRA2BGR)
            # OpenCV ROI selection MUST run on GUI/main thread on Windows.
            roi = cv2.selectROI("Select game board — drag then Enter", screen,
                                showCrosshair=True, fromCenter=False)
            cv2.destroyAllWindows()
            x, y, w, h = map(int, roi)
            if w < 30 or h < 30:
                self.post("Region selection cancelled.")
                return
            self.region = {"left":mon["left"]+x,"top":mon["top"]+y,
                           "width":w,"height":h}
            self.region_var.set(f"Board selected: {w} × {h} at ({self.region['left']}, {self.region['top']})")
            self.post(f"Board region selected: {self.region}")
        except Exception as exc:
            messagebox.showerror("Region selection failed", str(exc))

    def blue_square_worker(self, event):
        import cv2
        import mss
        import numpy as np
        settings = self.parameters
        region = dict(self.region)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tracks = []
        size_hint = None
        initial_time = time.perf_counter()
        last_time = None
        last_frame = None
        frames = []
        frame_number = 0
        last_report = initial_time
        last_preview = initial_time - 1.0
        with mss.mss() as sct:
            while not event.is_set():
                tick = time.perf_counter()
                frame = cv2.cvtColor(np.asarray(sct.grab(region)), cv2.COLOR_BGRA2BGR)
                now = time.perf_counter()
                last_frame = frame
                if not tracks:
                    detections = detect_squares(frame)
                    blue_count = sum(d["color"] == "blue" for d in detections)
                    if blue_count and len(detections) >= 2:
                        detections.sort(key=lambda d: (d["y"],d["x"]))
                        tracks = [SquareTrack(i+1,d) for i,d in enumerate(detections)]
                        size_hint = float(np.median([d["side"] for d in detections]))
                        last_time = now
                        init_path = OUTPUT_DIR / f"squares_initial_{stamp}.png"
                        cv2.imwrite(str(init_path), draw_results(frame,tracks,size_hint))
                        self.post(f"Identified {len(tracks)} squares; {blue_count} initially blue.")
                    elif now-initial_time > 6.0:
                        self.post("No initial blue squares detected within 6s. Check region and colors.",
                                  "No blue squares found")
                        break
                else:
                    dt = max(0.001, min(now-last_time, 0.25))
                    last_time = now
                    detections = detect_squares(frame, size_hint=size_hint)
                    associate_and_update(tracks,detections,dt,size_hint,now)
                    for tr in tracks:
                        frames.append([frame_number,round(now-initial_time,4),tr.number,
                                       int(tr.was_blue),round(float(tr.state[0]),2),
                                       round(float(tr.state[1]),2),int(tr.observed),
                                       int(tr.uncertain),round(now-tr.last_seen,3)])
                    if now-last_report >= 1.5:
                        observed = sum(tr.observed for tr in tracks)
                        ambiguous = sum(tr.uncertain for tr in tracks)
                        self.post(f"Squares: {observed}/{len(tracks)} visible; "
                                  f"{ambiguous} identities possibly ambiguous")
                        last_report = now
                if tracks and now - last_preview >= 0.4:
                    self.enqueue_preview(draw_results(frame, tracks, size_hint))
                    last_preview = now
                frame_number += 1
                event.wait(max(0.0, 1.0/settings["fps"] - (time.perf_counter()-tick)))
        if tracks and last_frame is not None:
            final_path = OUTPUT_DIR / f"squares_final_{stamp}.png"
            csv_path = OUTPUT_DIR / f"squares_tracking_{stamp}.csv"
            final_image = draw_results(last_frame,tracks,size_hint)
            cv2.imwrite(str(final_path),final_image)
            self.enqueue_preview(final_image)
            with open(csv_path,"w",newline="",encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["frame","seconds","id","originally_blue",
                                 "x_in_board","y_in_board","observed",
                                 "possibly_ambiguous","seconds_since_seen"])
                writer.writerows(frames)
            summary = []
            for tr in tracks:
                if tr.was_blue:
                    status = "uncertain" if tr.uncertain or not tr.observed else "tracked"
                    summary.append(f"#{tr.number} ({tr.state[0]:.0f},{tr.state[1]:.0f}) [{status}]")
            self.post("Original blue final positions: " + "; ".join(summary))
            self.post(f"Saved {final_path.name} and {csv_path.name}", "Results saved · Idle")
        with self.lock:
            if self.stop_event is event:
                self.state = "idle"
                self.active_mode = None
                self.stop_event.set()

    def enqueue_preview(self, frame_bgr):
        """Queue a small image for Tk; only the main UI thread constructs PhotoImage."""
        try:
            import cv2
            from PIL import Image
            h, w = frame_bgr.shape[:2]
            scale = min(700 / w, 175 / h, 1.0)
            resized = cv2.resize(frame_bgr, (max(1, int(w * scale)),
                                              max(1, int(h * scale))))
            img = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
            if self.preview_queue.full():
                try:
                    self.preview_queue.get_nowait()
                except queue.Empty:
                    pass
            self.preview_queue.put_nowait(img)
        except Exception:
            pass

    def open_results(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            import os
            os.startfile(str(OUTPUT_DIR))
        else:
            messagebox.showinfo("Results folder", str(OUTPUT_DIR))

    def close(self):
        self.stop()
        self.close_event.set()
        if self.mouse_listener is not None:
            self.mouse_listener.stop()
        if self.keyboard_listener is not None:
            self.keyboard_listener.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    MotionClickStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
